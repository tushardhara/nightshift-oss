"""Issue #423-A / ADR-0063 -- `nightshift-ctl dod ingest`, the additive
`on_dod_path`/`linked_conditions` fields, and the heartbeat delta line
(AC-3, AC-4, AC-5, AC-6).

Every test drives the real CLI through subprocess against a fresh temporary
database, same discipline as the rest of this suite (`ctlutil.Ctl`).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import unittest

from ctlutil import Ctl


def _seed_link(db, task_id, condition_id, blocking_artifact=None, created_at=0.0):
    with contextlib.closing(sqlite3.connect(db)) as cx, cx:
        cx.execute(
            "INSERT INTO task_dod_links(task_id,condition_id,"
            "blocking_artifact,created_at) VALUES(?,?,?,?)",
            (task_id, condition_id, blocking_artifact, created_at),
        )


def _dod_conditions_rows(db):
    with contextlib.closing(sqlite3.connect(db)) as cx:
        cx.row_factory = sqlite3.Row
        return [
            dict(r) for r in cx.execute(
                "SELECT condition_id,verdict,reason,blocking_artifact "
                "FROM dod_conditions ORDER BY condition_id")
        ]


def _dod_conditions_rows_with_observational(db):
    """Like `_dod_conditions_rows` but INCLUDES ingested_at/run_id -- used to
    prove AC-3 covers the observational columns too (round-1 review
    MERGE_BLOCKER, PR #487): the plain helper above deliberately excludes them
    so unrelated tests are not coupled to timing/run_id, which is exactly what
    let the original bug hide behind a passing idempotency test."""
    with contextlib.closing(sqlite3.connect(db)) as cx:
        cx.row_factory = sqlite3.Row
        return [
            dict(r) for r in cx.execute(
                "SELECT condition_id,verdict,reason,blocking_artifact,"
                "ingested_at,run_id FROM dod_conditions ORDER BY condition_id")
        ]


def _hash_rows(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True).encode("utf-8")
    ).hexdigest()


SAMPLE_PAYLOAD = json.dumps({
    "schema_version": 1,
    "manifest_readable": True,
    "dod_complete": False,
    "unallocatable": ["D21"],
    "conditions": [
        {"id": "D01", "verdict": "PASS", "reason": "", "blocking_artifact": None},
        {"id": "D13", "verdict": "FAIL", "reason": "missing doc",
         "blocking_artifact": "docs/audit/CONCURRENCY.md"},
        {"id": "D21", "verdict": "FAIL", "reason": "no artifact",
         "blocking_artifact": None},
    ],
})


class DodIngestIdempotencyTest(unittest.TestCase):
    """AC-3: ingesting the same payload twice leaves dod_conditions
    content-identical, never duplicated."""

    def test_ingest_twice_is_content_identical(self):
        c = Ctl()
        code1, body1 = c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        self.assertEqual(code1, 0, body1)
        self.assertEqual(body1["ingested"], 3)
        rows1 = _dod_conditions_rows(c.db)
        self.assertEqual(len(rows1), 3)

        code2, body2 = c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        self.assertEqual(code2, 0, body2)
        rows2 = _dod_conditions_rows(c.db)

        self.assertEqual(len(rows2), 3, "re-ingest must not duplicate rows")
        self.assertEqual(
            _hash_rows(rows1), _hash_rows(rows2),
            "re-ingesting identical input must leave content-identical rows")

    def test_ingest_upserts_changed_verdict(self):
        c = Ctl()
        c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        changed = json.dumps({
            "conditions": [
                {"id": "D13", "verdict": "PASS", "reason": "",
                 "blocking_artifact": None},
            ]
        })
        code, body = c.run("dod", "ingest", stdin=changed)
        self.assertEqual(code, 0, body)
        rows = {r["condition_id"]: r for r in _dod_conditions_rows(c.db)}
        self.assertEqual(rows["D13"]["verdict"], "PASS")
        self.assertEqual(len(rows), 3, "unrelated rows (D01/D21) untouched")

    def test_malformed_payload_rejected(self):
        c = Ctl()
        code, body = c.run("dod", "ingest", stdin="not json")
        self.assertNotEqual(code, 0)
        self.assertFalse(body.get("ok", True))

    def test_ingest_twice_leaves_observational_columns_unchanged(self):
        """The actual AC-3 gap round-1 review found: `ingested_at`/`run_id`
        are observational, not semantic, so a re-ingest of byte-identical
        input must leave them unchanged too -- CONTROL for the next test's
        counterfactual (WA-009)."""
        c = Ctl()
        code1, body1 = c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        self.assertEqual(code1, 0, body1)
        rows1 = _dod_conditions_rows_with_observational(c.db)

        code2, body2 = c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        self.assertEqual(code2, 0, body2)
        rows2 = _dod_conditions_rows_with_observational(c.db)

        self.assertEqual(
            rows1, rows2,
            "re-ingesting identical input must not advance ingested_at/run_id "
            "-- they are part of AC-3's idempotency contract, not exempt from it")

    def test_ingest_changed_verdict_advances_observational_columns(self):
        """COUNTERFACTUAL for the test above: when the semantic fields
        genuinely change, ingested_at/run_id MUST advance -- proves the fix is
        a real conditional, not a no-op that always preserves the old values."""
        c = Ctl()
        c.run("run", "current", "--set", "run-before")
        c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        before = {r["condition_id"]: r for r in
                  _dod_conditions_rows_with_observational(c.db)}
        self.assertEqual(before["D13"]["run_id"], "run-before")

        c.run("run", "current", "--set", "run-after")
        changed = json.dumps({
            "conditions": [
                {"id": "D13", "verdict": "PASS", "reason": "",
                 "blocking_artifact": None},
            ]
        })
        code, body = c.run("dod", "ingest", stdin=changed)
        self.assertEqual(code, 0, body)
        after = {r["condition_id"]: r for r in
                 _dod_conditions_rows_with_observational(c.db)}

        self.assertNotEqual(
            before["D13"]["ingested_at"], after["D13"]["ingested_at"],
            "a real semantic change must advance ingested_at")
        self.assertEqual(
            after["D13"]["run_id"], "run-after",
            "a real semantic change must advance run_id")
        # unrelated rows are untouched by the same re-ingest, observationally too
        # (run_id is a global "current run" value, not per-row, so it alone can
        # legitimately differ here -- ingested_at/verdict/reason/blocking_artifact
        # must not)
        for k in ("verdict", "reason", "blocking_artifact", "ingested_at"):
            self.assertEqual(before["D01"][k], after["D01"][k])
            self.assertEqual(before["D21"][k], after["D21"][k])

    def test_ingest_rejects_non_string_id(self):
        """FOLLOW_UP named in round-1 review (PR #487): a non-scalar `id`
        must be a clean rejection, not an uncaught sqlite3.ProgrammingError
        from using it as a TEXT primary key."""
        c = Ctl()
        bad = json.dumps({
            "conditions": [
                {"id": ["D01"], "verdict": "PASS", "reason": "",
                 "blocking_artifact": None},
            ]
        })
        code, body = c.run("dod", "ingest", stdin=bad)
        self.assertNotEqual(code, 0)
        self.assertFalse(body.get("ok", True))
        self.assertNotIn("Traceback", json.dumps(body))


class OnDodPathTest(unittest.TestCase):
    """AC-4: `task ready`/`task show` gain additive on_dod_path/
    linked_conditions, OR-of-paths over FAIL-verdict links only."""

    def setUp(self):
        self.c = Ctl()
        self.c.run(
            "task", "upsert", "--id", "t-linked", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "t-linked",
            "--state", "pending", "--size", "S")
        self.c.run(
            "task", "upsert", "--id", "t-unlinked", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "t-unlinked",
            "--state", "pending", "--size", "S")
        self.c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)

    def _task(self, task_id):
        code, body = self.c.run("task", "ready")
        self.assertEqual(code, 0, body)
        by_id = {t["id"]: t for t in body["tasks"]}
        return by_id[task_id]

    def test_zero_links_is_false_and_empty(self):
        task = self._task("t-unlinked")
        self.assertFalse(task["on_dod_path"])
        self.assertEqual(task["linked_conditions"], [])

    def test_one_fail_one_pass_link_is_true_or_of_paths(self):
        _seed_link(self.c.db, "t-linked", "D01")  # PASS
        _seed_link(self.c.db, "t-linked", "D13")  # FAIL
        task = self._task("t-linked")
        self.assertTrue(task["on_dod_path"])
        self.assertEqual(sorted(task["linked_conditions"]), ["D01", "D13"])

    def test_all_pass_links_is_false(self):
        _seed_link(self.c.db, "t-linked", "D01")  # PASS only
        task = self._task("t-linked")
        self.assertFalse(task["on_dod_path"])
        self.assertEqual(task["linked_conditions"], ["D01"])

    def test_blocked_or_manual_link_is_not_on_path(self):
        # A condition never ingested (unknown to dod_conditions) or one
        # reporting BLOCKED/MANUAL must not count as a FAIL path.
        blocked_payload = json.dumps({"conditions": [
            {"id": "D09", "verdict": "BLOCKED", "reason": "git unavailable",
             "blocking_artifact": None},
        ]})
        self.c.run("dod", "ingest", stdin=blocked_payload)
        _seed_link(self.c.db, "t-linked", "D09")
        task = self._task("t-linked")
        self.assertFalse(task["on_dod_path"])

    def test_task_show_gets_the_same_fields(self):
        _seed_link(self.c.db, "t-linked", "D13")
        code, body = self.c.run("task", "show", "--id", "t-linked")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["task"]["on_dod_path"])
        self.assertEqual(body["task"]["linked_conditions"], ["D13"])


class DormancyTest(unittest.TestCase):
    """AC-5: no existing ready/plan/slot-acquire admit/deny outcome changes,
    even for a task that IS on_dod_path -- this gate ships wired but
    dormant; only #423-C (a separate, later task) reads it for admission."""

    def test_slot_acquire_admits_identically_regardless_of_on_dod_path(self):
        baseline = Ctl()
        code_a, body_a = baseline.acquire_normal(task="dormancy-a")
        self.assertEqual(code_a, 0, body_a)
        self.assertTrue(body_a["granted"])

        linked = Ctl()
        code_b, body_b = linked.acquire_normal(task="dormancy-b")
        self.assertEqual(code_b, 0, body_b)
        # Link the SAME task to a FAIL condition -- on_dod_path would now
        # read true -- strictly AFTER admission, to prove seeding the link
        # table has no bearing on the slot-acquire verdict either way; then
        # re-derive readiness and confirm the computed field is inert.
        linked.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        _seed_link(linked.db, "dormancy-b", "D13")
        readiness_code, readiness_body = linked.run("task", "show", "--id", "dormancy-b")
        self.assertEqual(readiness_code, 0, readiness_body)
        self.assertTrue(readiness_body["task"]["on_dod_path"])

        # Same shape, same grant -- on_dod_path status never entered the
        # slot-acquire decision that already ran above.
        self.assertEqual(body_a["granted"], body_b["granted"])
        self.assertEqual(body_a["kind"], body_b["kind"])
        self.assertEqual(body_a["phase"], body_b["phase"])

    def test_a_second_task_pre_linked_before_acquire_is_still_admitted(self):
        c = Ctl()
        c.run(
            "task", "upsert", "--id", "pre-linked", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "pre-linked",
            "--state", "pending", "--size", "S")
        c.run("dod", "ingest", stdin=SAMPLE_PAYLOAD)
        _seed_link(c.db, "pre-linked", "D13")
        c.run("run", "current", "--set", "fixture-run")
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", "pre-linked",
            "--agent", "eng_claude", "--title", "pre-linked",
            "--purpose", "implement")
        self.assertEqual(code, 0, body)
        self.assertTrue(
            body["granted"],
            "on_dod_path being true must not deny a slot -- that is "
            "#423-C's job, not this issue's")


class HeartbeatDeltaLineTest(unittest.TestCase):
    """AC-6: `DoD <prev>/24 -> <now>/24 (delta <n>)`, with escalation on the
    SECOND consecutive zero-delta heartbeat, not the first."""

    def _pass_count(self, c, n):
        conditions = [
            {"id": f"D{i:02d}", "verdict": "PASS", "reason": "",
             "blocking_artifact": None}
            for i in range(1, n + 1)
        ]
        c.run("dod", "ingest", stdin=json.dumps({"conditions": conditions}))

    def test_first_heartbeat_never_escalates(self):
        c = Ctl()
        self._pass_count(c, 3)
        code, body = c.run("dod", "heartbeat-line")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["prev"], 0)
        self.assertEqual(body["now"], 3)
        self.assertEqual(body["delta"], 3)
        self.assertFalse(body["escalated"])
        self.assertEqual(body["line"], "DoD 0/24 -> 3/24 (delta 3)")

    def test_second_consecutive_zero_delta_escalates_not_the_first(self):
        c = Ctl()
        self._pass_count(c, 5)
        _, first = c.run("dod", "heartbeat-line")
        self.assertEqual(first["delta"], 5)
        self.assertFalse(first["escalated"])

        # Second observation: same pass count -> delta 0, but this is the
        # FIRST zero-delta observation, so no escalation yet.
        _, second = c.run("dod", "heartbeat-line")
        self.assertEqual(second["delta"], 0)
        self.assertFalse(second["escalated"])

        # Third observation: still delta 0 -- now it's the SECOND
        # consecutive zero-delta heartbeat -> escalate.
        _, third = c.run("dod", "heartbeat-line")
        self.assertEqual(third["delta"], 0)
        self.assertTrue(third["escalated"])

    def test_a_nonzero_delta_resets_the_escalation_streak(self):
        c = Ctl()
        self._pass_count(c, 2)
        c.run("dod", "heartbeat-line")             # delta 2
        c.run("dod", "heartbeat-line")              # delta 0 (1st zero)
        self._pass_count(c, 4)
        _, resumed = c.run("dod", "heartbeat-line")  # delta 2 again
        self.assertEqual(resumed["delta"], 2)
        self.assertFalse(resumed["escalated"])
        _, after_reset = c.run("dod", "heartbeat-line")  # delta 0 (1st again)
        self.assertEqual(after_reset["delta"], 0)
        self.assertFalse(after_reset["escalated"])


if __name__ == "__main__":
    unittest.main()
