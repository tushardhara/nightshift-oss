"""Issue #400 acceptance condition 21, verbatim:

    "Operations expose volume, latency, outbox age, retention/deletion
    backlog, export duration, publication queue time, isolation failures,
    and restore evidence."

`bin/nightshift_observability.py` reports exactly these eight signals, each
either MEASURED from real rows or REFUSED with a typed reason code -- never
a bare `null`.

Mockless: reuses `tests/test_publication_gateway.py`'s real-git,
real-SQLite fixture (`PublicationGatewayTestCase`) to build a genuine
staged->reviewed->approved->applied publication with controlled
timestamps, and drives `nightshift_export.py` for real to flip
`export_duration` from refused to measured.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_observability as obs  # noqa: E402
import nightshift_export as exp         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_publication_gateway import (  # noqa: E402
    NSP, PROV, PublicationGatewayTestCase, T0,
)

NAMED_IN_AC_21 = (
    "volume", "latency", "outbox age", "retention/deletion backlog",
    "export duration", "publication queue time", "isolation failures",
    "restore evidence",
)


class TheSignalSetIsExactlyTheEightNamed(unittest.TestCase):

    def test_required_signals_matches_the_eight_named_in_the_ac_verbatim(self):
        normalized = {name.replace(" ", "_").replace("/", "_") for name in NAMED_IN_AC_21}
        self.assertEqual(normalized, set(obs.REQUIRED_SIGNALS))
        self.assertEqual(len(obs.REQUIRED_SIGNALS), 8)

    def test_collect_on_a_never_touched_state_root_still_returns_all_eight_keys(self):
        import tempfile
        state_root = tempfile.mkdtemp(prefix="nightshift-obs-empty-")
        report = obs.collect(state_root, tenant_id=1001, repository_id=1)
        self.assertEqual(set(report), set(obs.REQUIRED_SIGNALS))
        for name, entry in report.items():
            self.assertIn(entry["status"], ("measured", "refused"), name)


class RefusedSignalsRaiseAndAreNotSkipped(PublicationGatewayTestCase):

    def test_the_refused_signals_are_exactly_these_before_any_export(self):
        """No outbox mechanism, no isolation-failure audit table, no restore
        drill, and no export yet -- exactly these four are refused; the
        other four are measured (real zero, not skipped)."""
        fx = self._new_fixture()
        report = obs.collect(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"])
        refused = {name for name, entry in report.items() if entry["status"] == "refused"}
        measured = {name for name, entry in report.items() if entry["status"] == "measured"}
        self.assertEqual(refused, {"outbox_age", "isolation_failures",
                                    "restore_evidence", "export_duration"})
        self.assertEqual(measured, {"volume", "latency",
                                     "retention_deletion_backlog", "publication_queue_time"})
        for name in refused:
            self.assertIn("reason_code", report[name], name)
            self.assertNotIn("value", report[name], name)

    def test_export_duration_flips_to_measured_once_a_real_export_exists(self):
        """The one refusal in the closed set that is conditional on real
        Nightshift-internal state (an export receipt), not on an
        unbuilt mechanism -- proven by actually running one."""
        import tempfile
        fx = self._new_fixture()
        before = obs.collect(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"])
        self.assertEqual(before["export_duration"]["status"], "refused")

        out_dir = tempfile.mkdtemp(prefix="nightshift-obs-export-")
        import shutil
        shutil.rmtree(out_dir)
        exp.export_tenant(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"], out_dir)

        after = obs.collect(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"])
        self.assertEqual(after["export_duration"]["status"], "measured")
        self.assertEqual(after["export_duration"]["value"]["count"], 1)

    def test_a_signal_with_no_backing_data_refuses_it_does_not_return_null(self):
        """The anti-pattern this AC exists to forbid: a dashboard full of
        silent nulls reading as 'healthy'. `outbox_age` must come back as
        a typed refusal, never `{"status": "measured", "value": None}`."""
        fx = self._new_fixture()
        report = obs.collect(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"])
        entry = report["outbox_age"]
        self.assertEqual(entry["status"], "refused")
        self.assertNotEqual(entry, {"status": "measured", "value": None})
        self.assertEqual(entry["reason_code"], "no_outbox_table")


class MeasuredSignalsAreArithmeticOnRealRows(PublicationGatewayTestCase):

    def test_publication_queue_time_equals_the_real_stage_to_attempt_delta(self):
        """Fixture stages/reviews/approves at T0, then applies 100s later
        (a fixed, pinned clock -- no wall-clock timing involved). The
        signal must report exactly 100.0, computed from
        publication_attempts.started_at - publication_stage.staged_at,
        never a hardcoded or approximate value."""
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, t=T0)
        self._apply(fx, authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                    idempotency_key="idem-queue-time", t=T0 + 100.0)

        report = obs.collect(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"])
        entry = report["publication_queue_time"]
        self.assertEqual(entry["status"], "measured")
        self.assertEqual(entry["value"]["count"], 1)
        self.assertEqual(entry["value"]["p50"], 100.0)
        self.assertEqual(entry["value"]["max"], 100.0)

    def test_volume_counts_real_rows_across_both_stores(self):
        fx = self._new_fixture()
        self._stage_review_approve(fx, t=T0)
        report = obs.collect(fx["unscoped_state_root"], fx["tenant_id"], fx["repository_id"])
        volume = report["volume"]["value"]
        self.assertEqual(volume["publication_stage"], 1)
        self.assertEqual(volume["publication_review"], 1)
        self.assertEqual(volume["publication_authorizations"], 1)
        self.assertEqual(volume["source_snapshot"], 1)


if __name__ == "__main__":
    unittest.main()
