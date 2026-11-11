"""Issue #390: stop no-op/reconciliation heartbeats from creating Git
commits and PRs. Trimmed scope per the accepted DoR (PR #397,
`docs/prd/390-heartbeat-persistence-dor.md`): AC-1, AC-4-13. AC-2 (24-heartbeat
sim), AC-3 (concurrent-authority sim) and AC-14 (11 historical-replay
fixtures) are explicitly out of scope here -- issue #398, blocked-by #390.

Layers, matching this suite's own established convention
(`tests/test_bookkeeping_release.py`'s docstring):
- Unit tests call `nightshift_heartbeat_persistence.decide()` directly --
  pure, no I/O, no sqlite, no subprocess.
- Fixture tests prove the AC-8/AC-9 "byte-identical" guarantee against a
  temporary copy of ledger/STATUS-shaped content, never the live repo files
  (`.omni/**` is `program`-owned, single-writer -- see the DoR's own §2).
- CLI integration tests drive the real `bin/nightshift-ctl heartbeat-persist
  {decide,record}` and the `pr-create --heartbeat-persist-receipt` gate end
  to end, proving the wiring: exit codes, PLANE_FREE_VERBS, one-shot
  receipt consumption, and AC-12's "marker absent -> unaffected" guarantee.

WA-008/WA-009 evidence pairs (control passes, counterfactual fails, for the
right reason) are called out by name at each pair below rather than
collected in one place, so each pair stays next to what it is evidence for.
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import nightshift_heartbeat_persistence as hp  # noqa: E402

from ctlutil import Ctl  # noqa: E402
from test_bookkeeping_release import GitRepo  # noqa: E402

CTL = os.environ.get("NIGHTSHIFT_TEST_CTL") or str(ROOT / "bin" / "nightshift-ctl")


def _outcome(**kwargs):
    base = {
        "authority_status": "granted",
        "observation_codes": [],
        "durable_delta_reason_code": None,
    }
    base.update(kwargs)
    return base


# ------------------------------------------------------------ unit: decide -

class DecideUnitTests(unittest.TestCase):
    def test_ac1_unchanged_heartbeat_defaults_false(self):
        result = hp.decide(_outcome(
            observation_codes=["queue_empty", "dod_unchanged"]))
        self.assertFalse(result["should_persist_to_git"])
        self.assertEqual(result["route"], "none")
        self.assertIn(result["reason_code"], hp.OUTPUT_REASON_CODES)

    def test_ac4_every_named_no_op_case_denies(self):
        """One fixture per issue #390's own no-op reconciliation examples
        (Required Behavior B / AC-4)."""
        cases = [
            (["queue_empty", "dod_unchanged"], "queue_empty+dod_unchanged"),
            (["pr_under_review"], "pr_under_review"),
            (["another_program_owns_work"], "another_program routing review"),
            (["another_program_owns_work", "no_new_dispatch"],
             "another_program integrating"),
            (["merge_confirmed"], "merge_confirmed only"),
            (["worker_still_running"], "worker_still_running"),
            (["issue_blocked"], "issue_blocked"),
            (["from_owner_scan_found_nothing_new"], "no new owner input"),
        ]
        for codes, label in cases:
            with self.subTest(label):
                result = hp.decide(_outcome(observation_codes=codes))
                self.assertFalse(result["should_persist_to_git"], label)
                self.assertEqual(result["route"], "none", label)

    def test_ac5_recognized_durable_delta_grants_exactly_one_route(self):
        """CONTROL for the AC-6/AC-10 counterfactuals below: a genuinely
        recognized, non-timestamp-only reason DOES grant, proving the
        mechanism can pass, not just always fail (WA-009)."""
        result = hp.decide(_outcome(durable_delta_reason_code="governed_pr_ready"))
        self.assertTrue(result["should_persist_to_git"])
        self.assertEqual(result["route"], "governed_normal_path")
        self.assertEqual(result["reason_code"], "durable_delta_governed")

    def test_ac5_bookkeeping_reason_routes_to_fast_path(self):
        result = hp.decide(
            _outcome(durable_delta_reason_code="bookkeeping_only_diff_present"))
        self.assertTrue(result["should_persist_to_git"])
        self.assertEqual(result["route"], "bookkeeping_fast_path")
        self.assertEqual(result["reason_code"], "durable_delta_bookkeeping_only")

    def test_ac6_timestamp_only_overrides_a_recognized_reason(self):
        """COUNTERFACTUAL to test_ac5_recognized_durable_delta_grants_*:
        identical reason code, only `diff_is_timestamp_only` differs, and the
        grant flips to a denial -- proving the reason code alone cannot pass
        the gate (WA-009's paired control/counterfactual)."""
        result = hp.decide(_outcome(
            durable_delta_reason_code="governed_pr_ready",
            diff_is_timestamp_only=True))
        self.assertFalse(result["should_persist_to_git"])
        self.assertEqual(result["reason_code"], "timestamp_only_rejected")
        self.assertEqual(result["route"], "none")

    def test_ac10_unrecognized_reason_code_denied(self):
        """A plausible, LLM-assertion-shaped free-text string is not a
        member of the closed enum and must be denied exactly like `None`."""
        for bogus in ("seems_important", "this really is a real change",
                      "governed_pr_ready ", "Governed_Pr_Ready"):
            with self.subTest(bogus):
                result = hp.decide(_outcome(durable_delta_reason_code=bogus))
                self.assertFalse(result["should_persist_to_git"])
                self.assertEqual(
                    result["reason_code"],
                    "unrecognized_durable_delta_reason_fail_closed")

    def test_ac10_standdown_short_circuits_even_with_a_valid_reason(self):
        """ADR-0044 §7b step 1: a losing Program performs zero dispatches
        regardless of anything else in the outcome. Even a recognized
        durable-delta reason must not override standdown."""
        result = hp.decide(_outcome(
            authority_status="standdown",
            durable_delta_reason_code="governed_pr_ready"))
        self.assertFalse(result["should_persist_to_git"])
        self.assertEqual(result["reason_code"], "standdown_no_authority")
        self.assertEqual(result["route"], "none")

    def test_malformed_authority_status_fails_closed(self):
        for bogus in (None, "", "GRANTED", "maybe", 42):
            with self.subTest(bogus):
                result = hp.decide(_outcome(authority_status=bogus))
                self.assertFalse(result["should_persist_to_git"])
                self.assertEqual(result["reason_code"], "standdown_no_authority")

    def test_unrecognized_observation_code_is_flagged_but_still_denied(self):
        result = hp.decide(_outcome(observation_codes=["something_new"]))
        self.assertFalse(result["should_persist_to_git"])
        self.assertEqual(
            result["reason_code"], "unrecognized_observation_fail_closed")

    def test_never_raises_on_structurally_malformed_input(self):
        for bad in (None, [], "a string", 42, {}, {"observation_codes": "x"}):
            with self.subTest(repr(bad)):
                result = hp.decide(bad)
                self.assertFalse(result["should_persist_to_git"])
                self.assertIn(result["reason_code"], hp.OUTPUT_REASON_CODES)

    def test_security_review_pr401_unhashable_field_values_never_raise(self):
        """MERGE_BLOCKER, security review on PR #401 (head f7f8742): a
        well-typed OUTER dict whose `authority_status`, an
        `observation_codes` element, or `durable_delta_reason_code` is
        itself a list/dict crashed `decide()` with an uncaught `TypeError`
        -- every `x in <frozenset-of-str>` membership test requires `x` to
        be hashable, and the prior malformed-input fixture set (above) never
        covered a malformed INNER value on an otherwise well-typed outer
        dict. Reproduces the exact adversarial shapes from the security
        report's repro commands."""
        adversarial = (
            {"authority_status": ["x"]},
            {"authority_status": {"a": 1}},
            {"authority_status": "granted", "durable_delta_reason_code": ["x"]},
            {"authority_status": "granted", "durable_delta_reason_code": {"a": 1}},
            {"authority_status": "granted", "observation_codes": [["nested"]]},
            {"authority_status": "granted", "observation_codes": [{"a": 1}]},
        )
        for bad in adversarial:
            with self.subTest(repr(bad)):
                result = hp.decide(bad)  # must not raise
                self.assertFalse(result["should_persist_to_git"])
                self.assertIn(result["reason_code"], hp.OUTPUT_REASON_CODES)

    def test_output_shape_always_closed_enums(self):
        fixtures = [
            _outcome(),
            _outcome(authority_status="standdown"),
            _outcome(durable_delta_reason_code="governed_pr_ready"),
            _outcome(durable_delta_reason_code="adr_accepted_new_content"),
            _outcome(durable_delta_reason_code="bookkeeping_only_diff_present"),
            _outcome(durable_delta_reason_code="nonsense"),
            _outcome(durable_delta_reason_code="governed_pr_ready",
                     diff_is_timestamp_only=True),
        ]
        for outcome in fixtures:
            result = hp.decide(outcome)
            self.assertIn(result["route"], hp.ROUTES)
            self.assertIn(result["reason_code"], hp.OUTPUT_REASON_CODES)
            self.assertIsInstance(result["should_persist_to_git"], bool)
            self.assertEqual(len(result["input_digest"]), 64)

    def test_digest_is_deterministic_and_input_sensitive(self):
        a = hp.decide(_outcome(observation_codes=["queue_empty"]))
        b = hp.decide(_outcome(observation_codes=["queue_empty"]))
        c = hp.decide(_outcome(observation_codes=["issue_blocked"]))
        self.assertEqual(a["input_digest"], b["input_digest"])
        self.assertNotEqual(a["input_digest"], c["input_digest"])


# --------------------------------------------------------- AC-8/AC-9 fixture

class ByteIdenticalFixtureTests(unittest.TestCase):
    """AC-8/AC-9's own definition (DoR §2): byte-identical means sha256 of a
    TEMPORARY FIXTURE COPY of ledger/STATUS-shaped content, taken before and
    after the no-op decision path runs, over content representative of the
    real files -- never the live repo's `.omni/ledger.md`/`.omni/STATUS.md`,
    which are `program`-owned (`docs/owners.md`) and single-writer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "ledger.md"
        self.status = Path(self.tmp.name) / "STATUS.md"
        self.ledger.write_text("## 2026-08-30 ~09:00Z -- prior entry\nsome text\n")
        self.status.write_text("# \U0001F7E2 09:00Z (2026-08-30) -- ok\nbody\n")

    def _sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ac8_ledger_fixture_unchanged_across_no_op_decisions(self):
        before = self._sha(self.ledger)
        for _ in range(5):
            hp.decide(_outcome(observation_codes=["queue_empty"]))
        self.assertEqual(before, self._sha(self.ledger))

    def test_ac9_status_fixture_unchanged_across_no_op_decisions(self):
        before = self._sha(self.status)
        for _ in range(5):
            hp.decide(_outcome(observation_codes=["merge_confirmed"]))
        self.assertEqual(before, self._sha(self.status))

    def test_control_fixture_unchanged_even_across_a_durable_delta_decision(self):
        """CONTROL: the fixture files are unchanged not because `decide()`
        was never called, but because `decide()` never performs file I/O at
        all -- true even for a decision that GRANTS persistence, since
        granting is a caller instruction, not a write this module performs."""
        before_l, before_s = self._sha(self.ledger), self._sha(self.status)
        hp.decide(_outcome(durable_delta_reason_code="governed_pr_ready"))
        self.assertEqual(before_l, self._sha(self.ledger))
        self.assertEqual(before_s, self._sha(self.status))


# ------------------------------------------------------------- AC-7 structural

class NoGitHubSideEffectTests(unittest.TestCase):
    """AC-7: mirroring a heartbeat's findings to issue #205 does not require
    `heartbeat-persist`/`route` at all -- proven structurally, the same way
    `DoesNotRunFullSuiteTests` proves AC-3 for the bookkeeping module: this
    module contains no subprocess or network call of any kind, so nothing in
    it can gate, require, or itself perform a GitHub post."""

    def test_module_has_no_subprocess_or_network_calls(self):
        src = (ROOT / "bin" / "nightshift_heartbeat_persistence.py").read_text()
        for forbidden in ("subprocess", "urllib", "requests", "socket", "http."):
            self.assertNotIn(forbidden, src)

    def test_module_contains_no_duplicate_path_classifier(self):
        """AC-11: this module must not reimplement #286/#388's classifier."""
        src = (ROOT / "bin" / "nightshift_heartbeat_persistence.py").read_text()
        for forbidden in ("classify_paths", "git diff", "ELIGIBLE_EXACT_PATHS"):
            self.assertNotIn(forbidden, src)


# --------------------------------------------------------- CLI integration -

class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ctl = Ctl()

    def _decide(self, outcome, **extra_argv):
        argv = ["heartbeat-persist", "decide"]
        for k, v in extra_argv.items():
            argv += [f"--{k.replace('_', '-')}", v]
        return self.ctl.run(*argv, stdin=json.dumps(outcome))

    def _record(self, outcome, run_id="test-run"):
        return self.ctl.run(
            "heartbeat-persist", "record", "--run-id", run_id,
            stdin=json.dumps(outcome))

    def test_decide_is_plane_free(self):
        """PLANE_FREE_VERBS wiring: `decide` works even against a
        nonexistent --db (AC-1: it cannot itself produce any Git artifact)."""
        p = subprocess.run(
            [CTL, "--db", "/nonexistent/path/control.db",
             "heartbeat-persist", "decide"],
            input=json.dumps(_outcome(observation_codes=["queue_empty"])),
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = json.loads(p.stdout.strip())
        self.assertFalse(body["should_persist_to_git"])
        self.assertEqual(body["route"], "none")

    def test_ac1_decide_wrapper_matches_module(self):
        code, body = self._decide(_outcome(observation_codes=["queue_empty"]))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["should_persist_to_git"])
        self.assertEqual(body["route"], "none")

    def test_decide_denies_malformed_json_without_crashing(self):
        code, body = self.ctl.run(
            "heartbeat-persist", "decide", stdin="not valid json{{{")
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"], "outcome_json_invalid")

    def test_security_review_pr401_cli_repro_no_longer_crashes(self):
        """End-to-end regression for the security review's exact repro
        command (PR #401, head f7f8742) -- through the real CLI subprocess,
        not just the module function, proving the fix closes the gap where
        a caller checking `returncode == 3` would previously see an
        uncaught-traceback `exit 1` instead."""
        code, body = self.ctl.run(
            "heartbeat-persist", "decide",
            stdin=json.dumps({"authority_status": "granted",
                              "durable_delta_reason_code": ["x"]}))
        self.assertEqual(code, 0, body)  # a produced verdict, not a crash
        self.assertFalse(body["should_persist_to_git"])
        self.assertEqual(
            body["reason_code"], "unrecognized_durable_delta_reason_fail_closed")

    def test_outcome_json_oversized_input_denied_not_unbounded_read(self):
        """FOLLOW_UP, security review on PR #401: `_read_outcome_json` now
        bounds its read (`MAX_OUTCOME_JSON_BYTES`) instead of reading an
        unbounded stdin/file. An input over the cap denies cleanly rather
        than being parsed (it would fail JSON parsing anyway once
        truncated, but this proves the bound is enforced BEFORE parsing,
        not relied upon incidentally)."""
        oversized = json.dumps(_outcome(
            durable_delta_reason_code="governed_pr_ready",
            diff_paths=["x" * (1_048_576 + 1000)]))
        code, body = self.ctl.run("heartbeat-persist", "decide", stdin=oversized)
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"], "outcome_json_invalid")

    def test_ac13_record_persists_one_row_per_call_no_op_and_delta_alike(self):
        for _ in range(3):
            code, body = self._record(_outcome(observation_codes=["queue_empty"]))
            self.assertEqual(code, 0, body)
        code, body = self._record(
            _outcome(durable_delta_reason_code="governed_pr_ready"))
        self.assertEqual(code, 0, body)

        with sqlite3.connect(self.ctl.db) as cx:
            cx.row_factory = sqlite3.Row
            n_noop = cx.execute(
                "SELECT COUNT(*) AS n FROM heartbeat_persist_decisions "
                "WHERE should_persist=0").fetchone()["n"]
            n_delta = cx.execute(
                "SELECT COUNT(*) AS n FROM heartbeat_persist_decisions "
                "WHERE should_persist=1").fetchone()["n"]
            # AC-13's own invariant, as a query, not a narrated claim: no
            # no-op decision is ever consumed (nothing consumed it in this
            # test), so a query for "no-op decisions with a PR" is 0.
            n_noop_consumed = cx.execute(
                "SELECT COUNT(*) AS n FROM heartbeat_persist_decisions "
                "WHERE should_persist=0 AND consumed_at IS NOT NULL"
            ).fetchone()["n"]
        self.assertEqual(n_noop, 3)
        self.assertEqual(n_delta, 1)
        self.assertEqual(n_noop_consumed, 0)

    def test_ac10_gate_denies_pr_create_with_no_matching_decision(self):
        code, body = self.ctl.run(
            "pr-create", "--heartbeat-persist-receipt", "999999",
            "--", "--title", "x", "--body", "Refs #390")
        self.assertEqual(code, 3)
        self.assertEqual(body["error"], "heartbeat_persist_denied")

    def test_ac10_gate_denies_a_no_op_receipt(self):
        code, body = self._record(_outcome(observation_codes=["queue_empty"]))
        self.assertEqual(code, 0, body)
        receipt_id = str(body["receipt_id"])
        code2, body2 = self.ctl.run(
            "pr-create", "--heartbeat-persist-receipt", receipt_id,
            "--", "--title", "x", "--body", "Refs #390")
        self.assertEqual(code2, 3)
        self.assertEqual(body2["reason"], "no_matching_should_persist_decision")

    def test_ac10_gate_survives_being_called_directly_no_wrapper(self):
        """Mechanical, not convention: the same denial fires even when
        `pr-create` is invoked directly, with no prior `decide` step at
        all -- there is no valid receipt to present."""
        code, body = self.ctl.run(
            "pr-create", "--heartbeat-persist-receipt", "1",
            "--", "--title", "x", "--body", "Refs #390")
        self.assertEqual(code, 3)
        self.assertEqual(body["error"], "heartbeat_persist_denied")

    def test_control_valid_receipt_is_consumed_and_gh_runs(self):
        """CONTROL for the two denial tests above (WA-009): a genuinely
        granted, unconsumed receipt DOES let `pr-create` proceed to `gh`."""
        code, body = self._record(
            _outcome(durable_delta_reason_code="governed_pr_ready"))
        self.assertEqual(code, 0, body)
        receipt_id = str(body["receipt_id"])
        env = dict(os.environ)
        env["NIGHTSHIFT_CTL_GH"] = "/bin/true"
        p = subprocess.run(
            self.ctl.cmdline(
                "pr-create", "--heartbeat-persist-receipt", receipt_id,
                "--", "--title", "x", "--body", "Refs #390"),
            capture_output=True, text=True, env=env)
        # os.execvp replaces the process on success -- /bin/true prints
        # nothing and exits 0, which is itself the proof `gh` actually ran
        # (a denial always prints our own JSON error object first).
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), "")

    def test_one_shot_receipt_cannot_be_reused(self):
        code, body = self._record(
            _outcome(durable_delta_reason_code="governed_pr_ready"))
        receipt_id = str(body["receipt_id"])
        env = dict(os.environ)
        env["NIGHTSHIFT_CTL_GH"] = "/bin/true"
        first = subprocess.run(
            self.ctl.cmdline(
                "pr-create", "--heartbeat-persist-receipt", receipt_id,
                "--", "--title", "x", "--body", "Refs #390"),
            capture_output=True, text=True, env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        code2, body2 = self.ctl.run(
            "pr-create", "--heartbeat-persist-receipt", receipt_id,
            "--", "--title", "x", "--body", "Refs #390")
        self.assertEqual(code2, 3)
        self.assertEqual(body2["reason"], "no_matching_should_persist_decision")

    def test_ac12_marker_absent_pr_create_stays_plane_free_and_unaffected(self):
        """The ordinary (non-heartbeat) PR path: no marker, no --db needed at
        all, and the exact same refusal shape as before issue #390."""
        p = subprocess.run(
            [CTL, "--db", "/nonexistent/path/control.db", "pr-create",
             "--", "--title", "x"],
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        body = json.loads(p.stdout.strip())
        self.assertIn("no --body/--body-file given", body["problems"][0])

    def test_ac11_bookkeeping_route_delegates_to_the_real_classifier(self):
        """route='bookkeeping_fast_path' is informational for the caller;
        the actual classification is proven independently by the real,
        already-shipped `bookkeeping plan` chain (#286/#388, PR #387) -- not
        duplicated here."""
        repo = GitRepo()
        self.addCleanup(repo.close)
        repo.write(".omni/STATUS.md", "placeholder\n")
        repo.write(".omni/ledger.md", "entry1\n")
        base = repo.commit("base")
        code, body = self.ctl.run(
            "task", "upsert", "--id", "hp-bkt", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "hp-bkt", "--state", "building",
            "--base-sha", base, "--size", "S")
        self.assertEqual(code, 0, body)

        decide_result = hp.decide(
            _outcome(durable_delta_reason_code="bookkeeping_only_diff_present"))
        self.assertEqual(decide_result["route"], "bookkeeping_fast_path")

        repo.write(".omni/ledger.md", "entry1\nentry2\n")
        head = repo.commit("bookkeeping-only change")
        code2, plan = self.ctl.run(
            "bookkeeping", "plan", "--task", "hp-bkt", "--repo",
            str(repo.path), "--head-sha", head)
        self.assertEqual(code2, 0, plan)
        self.assertTrue(plan["fast_path_eligible"])
        self.assertEqual(plan["classification"], "bookkeeping-only")


if __name__ == "__main__":
    unittest.main()
