"""Issue #214 / ADR-0043 (amended after a staff MERGE_BLOCKER, 2026-08-29):
`tasks.started_at` is set exactly once, at the moment a task's first
`phase='new'` normal worker slot is genuinely ADMITTED by the root
`worker_semaphore` policy -- not merely reserved by `slot acquire` -- and is
never touched by any other admission/reservation shape (denied, reused=True
reservation retry, planning, continuation, oncall), nor by a reservation
that is granted but never consumed.

Amendment history: the original implementation wrote `started_at` inside
`cmd_slot_acquire`'s RESERVATION transaction (`bin/nightshift-ctl`). Per
ADR-0033, a `phase='new'` reservation is mutable -- it can expire or be
released unconsumed, and creates no admission -- so stamping `started_at`
there let an expired, never-consumed reservation permanently pin the wrong
(early) timestamp if the same task was genuinely admitted later, since the
`WHERE started_at IS NULL` guard would then refuse the correct, later write.
Staff found this reachable and not merely theoretical. The write now lives
in `worker_semaphore`'s `phase == "new"` branch
(`deploy/policies/omnipods_policies.py`), the same transaction as the real
`INSERT INTO task_admissions` / `UPDATE tasks SET state='building'` -- the
ONLY place ADR-0033 recognises as genuine admission. See ADR-0043's
amendment note.

Tests that exercise real admission drive `cmd_slot_acquire` for the
reservation AND `omnipods_policies.worker_semaphore` for the consumption,
matching `test_admission_gate.py`'s own convention (admission-policy facts
must exercise the real evaluator, never `ctlutil.seed_admission`, which is
documented there as being for downstream state only). Tests that never
reach admission at all (denied-at-reservation, planning, oncall, the
upsert-cannot-set-it-via-a-flag check) still drive the CLI alone. The small
`send`/`make_task`/`reserve`/`consume`/`admit` helpers below are duplicated
locally rather than imported from `test_admission_gate.py`, matching this
suite's existing convention of self-contained test files (see
`test_policy.py`, which duplicates its own `send()` the same way).
"""

import contextlib
import os
import sqlite3
import sys
import unittest

from ctlutil import REPO, Ctl

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
import omnipods_policies as P  # noqa: E402


def started_at(c, task_id):
    with contextlib.closing(sqlite3.connect(c.db)) as cx:
        cx.row_factory = sqlite3.Row
        row = cx.execute(
            "SELECT started_at FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        return row["started_at"] if row else "MISSING_TASK_ROW"


def admission_rows(c, task_id):
    with contextlib.closing(sqlite3.connect(c.db)) as cx:
        cx.row_factory = sqlite3.Row
        return [dict(r) for r in cx.execute(
            "SELECT * FROM task_admissions WHERE task_id=?", (task_id,)
        )]


def send(agent, title, args):
    return {"type": "tool_call",
            "data": {"name": "sys_session_send",
                     "arguments": {"agent": agent, "title": title, "args": args}}}


def new_ctl(capacity=None):
    c = Ctl(capacity=capacity)
    code, body = c.run("run", "current", "--set", "run-214")
    assert code == 0, body
    return c, P.worker_semaphore(db=c.db)


def make_task(c, task_id, *, crew="crew_alpha", agent="eng_claude",
              title=None, state="pending"):
    title = title or f"ship-{task_id}"
    code, body = c.run(
        "task", "upsert", "--id", task_id, "--crew", crew, "--agent", agent,
        "--title", title, "--state", state, "--size", "S",
    )
    assert code == 0, body
    return title


def reserve(c, task_id, *, crew="crew_alpha", agent="eng_claude",
            title=None, purpose="implement", ttl="600"):
    title = title or f"ship-{task_id}"
    return c.run(
        "slot", "acquire", "--holder", f"{crew}/{agent}", "--crew", crew,
        "--task", task_id, "--agent", agent, "--title", title,
        "--purpose", purpose, "--ttl", ttl,
    )


def consume(policy, granted, task_id, *, agent="eng_claude", title=None,
            purpose="implement"):
    title = title or f"ship-{task_id}"
    return policy(send(agent, title, {
        "slot_id": granted["slot_id"], "task_id": task_id,
        "purpose": purpose, "generation": granted.get("generation"),
    }))


def admit(c, policy, task_id, *, crew="crew_alpha", agent="eng_claude",
          title=None, ttl="600"):
    """Full genuine admission: task + reservation + real policy consumption
    -- the only thing ADR-0033 recognises as admission. Mirrors
    `test_admission_gate.py`'s `AdmissionFixture.admit()`."""
    title = title or f"ship-{task_id}"
    make_task(c, task_id, crew=crew, agent=agent, title=title)
    code, g = reserve(c, task_id, crew=crew, agent=agent, title=title, ttl=ttl)
    assert code == 0, g
    result = consume(policy, g, task_id, agent=agent, title=title)
    assert result["result"] == "ALLOW", result
    return g


class TaskStartedAt(unittest.TestCase):
    def test_first_new_admission_sets_started_at(self):
        """AC-9 (positive path). A bare reservation must NOT set it -- this
        line is RED under the pre-amendment code, which set it right here,
        at reservation time. Only genuine policy admission sets it."""
        c, policy = new_ctl()
        title = make_task(c, "t-start-1")
        code, g = reserve(c, "t-start-1", title=title)
        self.assertEqual(code, 0, g)
        self.assertEqual(g["phase"], "new")
        self.assertFalse(g["reused"])
        self.assertIsNone(started_at(c, "t-start-1"))

        result = consume(policy, g, "t-start-1", title=title)
        self.assertEqual(result["result"], "ALLOW", result)
        v = started_at(c, "t-start-1")
        self.assertIsInstance(v, float)

    def test_denied_grant_leaves_started_at_null(self):
        """AC-10(a). Control: the filler task's own full admission DOES set
        started_at, proving the harness can distinguish set from unset."""
        c, policy = new_ctl(capacity={"max_concurrent_workers": 1,
                                       "reserve_for_oncall": 0})
        admit(c, policy, "filler")
        self.assertIsInstance(started_at(c, "filler"), float)  # control

        title = make_task(c, "t-denied")
        code, g = reserve(c, "t-denied", title=title)
        self.assertEqual(code, 3, g)  # budget exhausted -> denied
        self.assertIsNone(started_at(c, "t-denied"))

    def test_reused_new_reservation_never_sets_started_at(self):
        """AC-6 / AC-10(b). A reused reservation, by definition, never
        reached policy consumption, so under the amended write site it
        cannot have touched started_at -- it stays NULL throughout, not
        merely 'unchanged from some already-set value'."""
        c, _policy = new_ctl()
        title = make_task(c, "t-retry")
        code, g1 = reserve(c, "t-retry", title=title)
        self.assertEqual(code, 0, g1)
        self.assertIsNone(started_at(c, "t-retry"))

        code, g2 = reserve(c, "t-retry", title=title)
        self.assertEqual(code, 0, g2)
        self.assertTrue(g2["reused"])
        self.assertEqual(g2["slot_id"], g1["slot_id"])
        self.assertIsNone(started_at(c, "t-retry"))

    def test_expired_unconsumed_reservation_then_later_genuine_admission(self):
        """Staff finding (ADR-0043 amendment, MERGE_BLOCKER regression
        test): a first reservation that is granted but never consumed --
        it expires/is reaped per ADR-0033 Sec6, and the task stays pending
        -- must not pin started_at. This assertion is RED under the
        pre-amendment code (which already set started_at right after the
        first `reserve()` call, before any expiry or consumption). A later,
        genuinely admitted reservation for the same still-pending task is
        the one that must set it, at the real (later) admission time."""
        c, policy = new_ctl()
        title = make_task(c, "t-late-admit")
        code, g1 = reserve(c, "t-late-admit", title=title)
        self.assertEqual(code, 0, g1)
        self.assertIsNone(started_at(c, "t-late-admit"))

        # the reservation expires, unconsumed -- simulate directly, matching
        # test_admission_gate.py's own convention for a genuinely expired
        # slot (`test_stale_generation_and_expired_slot_deny_without_consumption`)
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute(
                "UPDATE worker_slots SET expires_at=0 WHERE id=?",
                (g1["slot_id"],),
            )
        denied = consume(policy, g1, "t-late-admit", title=title)
        self.assertEqual(denied["result"], "DENY", denied)
        self.assertIsNone(started_at(c, "t-late-admit"))
        self.assertEqual(admission_rows(c, "t-late-admit"), [])

        # task is still pending (never admitted) -- a genuinely new, later
        # reservation is taken out and this time actually consumed
        code, g2 = reserve(c, "t-late-admit", title=title)
        self.assertEqual(code, 0, g2)
        self.assertNotEqual(g2["slot_id"], g1["slot_id"])
        allowed = consume(policy, g2, "t-late-admit", title=title)
        self.assertEqual(allowed["result"], "ALLOW", allowed)
        v = started_at(c, "t-late-admit")
        self.assertIsInstance(v, float)

    def test_planning_grant_never_sets_started_at(self):
        """AC-4 / AC-10(c). A PLANNING slot is only reachable for a
        pending, unadmitted task, so it structurally can never follow a NEW
        grant for the same task -- this is the only shape the code allows,
        and it must still never write started_at."""
        c, _policy = new_ctl()
        title = make_task(c, "t-plan")
        code, g = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", "t-plan", "--agent", "qa", "--title", "test-t-plan",
            "--purpose", "explore",
        )
        self.assertEqual(code, 0, g)
        self.assertEqual(g["phase"], "planning")
        self.assertIsNone(started_at(c, "t-plan"))

    def test_planning_denied_once_task_is_genuinely_admitted(self):
        """AC-11 (the 'planning-after-set' sub-case flagged as untested in
        PM's acceptance sign-off, docs/reviews/214-pm-acceptance.md). Once a
        task is genuinely admitted, PLANNING is structurally unreachable
        for it (`_derive_normal_phase` requires a pending, unadmitted task,
        and the root policy rechecks the same fact) -- the request is
        denied outright, and started_at, already set by the admission, is
        untouched."""
        c, policy = new_ctl()
        title = "ship-t-plan-after"
        g0 = admit(c, policy, "t-plan-after", title=title)
        v0 = started_at(c, "t-plan-after")
        self.assertIsInstance(v0, float)

        code, g = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", "t-plan-after", "--agent", "qa", "--title", "test-t-plan-after",
            "--purpose", "explore",
        )
        self.assertEqual(code, 3, g)
        self.assertEqual(g["reason"], "PLANNING requires a pending, unadmitted task")
        self.assertEqual(started_at(c, "t-plan-after"), v0)
        self.assertNotEqual(g0["slot_id"], None)

    def test_continuation_grant_never_overwrites_started_at(self):
        """AC-4 / AC-10(d) / AC-11 (continuation sub-case). After genuine
        admission sets started_at, take a QA continuation slot for the SAME
        task all the way through real policy consumption, and assert the
        original value survives unchanged."""
        c, policy = new_ctl()
        title = "ship-t-cont"
        admit(c, policy, "t-cont", title=title)
        v0 = started_at(c, "t-cont")
        self.assertIsInstance(v0, float)

        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute(
                "UPDATE tasks SET pr_url=? WHERE id=?",
                ("https://example.invalid/pr/1", "t-cont"),
            )

        code, g2 = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", "t-cont", "--agent", "qa", "--title", "t-cont-qa",
            "--purpose", "implement",
        )
        self.assertEqual(code, 0, g2)
        self.assertEqual(g2["phase"], "continuation")
        self.assertEqual(g2["continuation_kind"], "qa")

        result = policy(send("qa", "t-cont-qa", {
            "slot_id": g2["slot_id"], "task_id": "t-cont",
            "purpose": "implement", "generation": g2.get("generation"),
        }))
        self.assertEqual(result["result"], "ALLOW", result)
        self.assertEqual(started_at(c, "t-cont"), v0)

    def test_re_admission_after_reset_to_pending_does_not_overwrite_started_at(self):
        """AC-11 (the 're-admission' sub-case flagged as untested in PM's
        acceptance sign-off). If a task genuinely returns to 'pending' after
        already being admitted once (a reset/retry flow) and is admitted
        again, the already-set started_at must survive: the
        `WHERE started_at IS NULL` guard makes exactly-once a property of
        the write itself, not of 'admission only ever happens once per task
        id' staying true forever."""
        c, policy = new_ctl()
        title = "ship-t-readmit"
        g1 = admit(c, policy, "t-readmit", title=title)
        v0 = started_at(c, "t-readmit")
        self.assertIsInstance(v0, float)

        # a legitimate reset-to-pending-for-retry flow
        code, _ = c.run("task", "upsert", "--id", "t-readmit", "--state", "pending")
        self.assertEqual(code, 0)
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute("DELETE FROM task_admissions WHERE task_id=?", ("t-readmit",))
            cx.execute(
                "UPDATE worker_slots SET state='released' WHERE task_id=?",
                ("t-readmit",),
            )

        g2 = admit(c, policy, "t-readmit", title=title)
        self.assertNotEqual(g2["slot_id"], g1["slot_id"])
        self.assertEqual(started_at(c, "t-readmit"), v0)

    def test_oncall_grant_touches_no_task_row(self):
        """AC-4 / AC-10(e). oncall grants are not task-bound at all -- kind
        == "oncall" has no task_id in either the pre- or post-amendment
        code, so there is no code path in EITHER version that could set
        started_at for it. PM's acceptance sign-off correctly noted this
        sub-case can never show a genuine red baseline (it passes
        identically before and after any fix to this feature) -- that is a
        structural fact about oncall grants, not a coverage gap, and is
        recorded here rather than silently claimed otherwise. What this
        test DOES prove: the tasks table stays empty, i.e. no accidental
        row is acquired via some shared/default task id."""
        c, _policy = new_ctl()
        # ADR-0044 Decision §3b (task 174-implement-fence): the oncall
        # reserve slot now additionally requires a live, current-generation
        # Program authority token.
        code, granted = c.run("authority", "acquire", "--holder-label", "p")
        self.assertEqual(code, 0, granted)
        code, g = c.run(
            "slot", "acquire", "--holder", "oncall", "--agent", "oncall",
            "--title", "incident", "--purpose", "implement", "--oncall",
            "--authority-generation", str(granted["generation"]),
            "--authority-token-stdin", stdin=granted["token"],
        )
        self.assertEqual(code, 0, g)
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.row_factory = sqlite3.Row
            rows = cx.execute("SELECT * FROM tasks").fetchall()
        self.assertEqual(rows, [])

    def test_task_upsert_cannot_set_started_at(self):
        """AC-1. There is no --started-at flag; task upsert stays
        structurally incapable of writing this column."""
        c, _policy = new_ctl()
        code, body = c.run(
            "task", "upsert", "--id", "t-noflag", "--started-at", "123",
        )
        self.assertNotEqual(code, 0)
        self.assertIsNone(body)


if __name__ == "__main__":
    unittest.main()
