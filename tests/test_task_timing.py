"""Issue #223 / ADR-0060: task-lifecycle timing (queue time, time-to-main).

Three new, additive, nullable `tasks` columns -- `ready_at`, `pr_opened_at`,
`integrated_at` -- each written exactly once, ever, guarded by
`WHERE <col> IS NULL`, the same idiom `started_at` (#214/ADR-0043),
`budget_seconds` (#215/ADR-0045) and `extension_seconds`/`extended_at`
(#217/ADR-0046) already established in this file. `started_at` itself is
reused as-is (AC2) -- not touched by this change.

`ready_at` is written from `_task_readiness_snapshot`, the one shared
read function behind `task show`, `task ready`, the continuation-slot
verdict lookups, and `task extend` -- so any of those observing
`reason_code == "ready"` for the first time records it (AC1). `pr_opened_at`
and `integrated_at` are written inside `cmd_task_upsert`'s own transaction,
guarded exactly like `extended_at` (AC3/AC4).

Tests that only exercise ready_at/pr_opened_at/integrated_at run on
`Ctl(clock=VIRTUAL_T0)` for exact-value assertions (issue #223 never touches
`started_at`'s own write site, which lives in
`deploy/policies/omnipods_policies.py` and has no virtual-clock support --
see `test_task_started_at.py`'s own `new_ctl()`, which uses the real wall
clock for the same reason). Tests that need a genuine admission (to exercise
`queue_time`/`active_delivery_time`, which both read `started_at`) use a
real-time `Ctl` and drive the real policy evaluator, matching
`test_task_started_at.py`'s `admit()` convention, and assert the algebraic
relationship between the three derived values rather than an exact literal
(the wall clock is not controllable), the same discipline that suite already
uses (`assertIsInstance(v, float)` rather than a literal).
"""

import contextlib
import os
import sqlite3
import sys
import unittest

from ctlutil import REPO, Ctl, VIRTUAL_T0

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
import omnipods_policies as P  # noqa: E402


def task_row(c, task_id):
    with contextlib.closing(sqlite3.connect(c.db)) as cx:
        cx.row_factory = sqlite3.Row
        row = cx.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None


def send(agent, title, args):
    return {"type": "tool_call",
            "data": {"name": "sys_session_send",
                     "arguments": {"agent": agent, "title": title, "args": args}}}


def make_task(c, task_id, *, crew="crew_alpha", agent="eng_claude",
              title=None, state="pending", size="S"):
    title = title or f"ship-{task_id}"
    argv = ["task", "upsert", "--id", task_id, "--crew", crew,
            "--agent", agent, "--title", title, "--state", state]
    if size is not None:
        argv += ["--size", size]
    code, body = c.run(*argv)
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
    """Full genuine admission -- the only thing that sets `started_at`.
    Mirrors `test_task_started_at.py::admit()`."""
    title = title or f"ship-{task_id}"
    make_task(c, task_id, crew=crew, agent=agent, title=title)
    code, g = reserve(c, task_id, crew=crew, agent=agent, title=title, ttl=ttl)
    assert code == 0, g
    result = consume(policy, g, task_id, agent=agent, title=title)
    assert result["result"] == "ALLOW", result
    return g


class ReadyAtFirstObservedRead(unittest.TestCase):
    """AC1, AC5: `ready_at` is set the first time reason_code=="ready" is
    observed, and never rewritten by a later read."""

    def test_ready_task_gets_ready_at_on_first_read_and_it_survives_later_reads(self):
        c = Ctl(clock=VIRTUAL_T0)
        tid = "t-ready-1"
        make_task(c, tid)
        code, body = c.run("task", "show", "--id", tid)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["task"]["reason_code"], "ready")
        self.assertEqual(body["task"]["ready_at"], VIRTUAL_T0)

        c.advance(500)
        code, body2 = c.run("task", "show", "--id", tid)
        self.assertEqual(code, 0, body2)
        # AC1/AC5: NOT bumped to the later read's `now()`.
        self.assertEqual(body2["task"]["ready_at"], VIRTUAL_T0)
        self.assertEqual(task_row(c, tid)["ready_at"], VIRTUAL_T0)

    def test_not_yet_ready_task_never_gets_ready_at(self):
        c = Ctl(clock=VIRTUAL_T0)
        tid = "t-not-ready"
        make_task(c, tid, size=None)  # missing_size -> not dispatchable
        code, body = c.run("task", "show", "--id", tid)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["task"]["reason_code"], "missing_size")
        self.assertIsNone(body["task"]["ready_at"])
        self.assertIsNone(task_row(c, tid)["ready_at"])

    def test_task_ready_list_sets_ready_at_for_every_newly_ready_task(self):
        c = Ctl(clock=VIRTUAL_T0)
        make_task(c, "t-ready-2")
        make_task(c, "t-blocked", size=None)
        code, body = c.run("task", "ready")
        self.assertEqual(code, 0, body)
        ids = {t["id"]: t for t in body["tasks"]}
        self.assertIn("t-ready-2", ids)
        self.assertNotIn("t-blocked", ids)
        self.assertEqual(ids["t-ready-2"]["ready_at"], VIRTUAL_T0)
        # the blocked task's OWN readiness_snapshot pass still ran (it is
        # part of the same full-graph read) but never wrote ready_at, since
        # it was never observed ready.
        self.assertIsNone(task_row(c, "t-blocked")["ready_at"])

    def test_task_upsert_cannot_set_timing_fields_directly(self):
        c = Ctl(clock=VIRTUAL_T0)
        for flag in ("--ready-at", "--pr-opened-at", "--integrated-at"):
            code, body = c.run(
                "task", "upsert", "--id", "t-noflag", flag, "123",
            )
            self.assertNotEqual(code, 0)
            self.assertIsNone(body)


class PrOpenedAtAndIntegratedAt(unittest.TestCase):
    """AC3, AC4, AC5: guarded first-write inside `cmd_task_upsert`."""

    def test_pr_opened_at_set_once_and_not_rewritten_by_a_later_pr_url(self):
        c = Ctl(clock=VIRTUAL_T0)
        tid = "t-pr-1"
        make_task(c, tid)
        self.assertIsNone(task_row(c, tid)["pr_opened_at"])

        c.advance(10)
        code, _ = c.run(
            "task", "upsert", "--id", tid,
            "--pr-url", "https://example.invalid/pr/1",
        )
        self.assertEqual(code, 0)
        first = task_row(c, tid)["pr_opened_at"]
        self.assertEqual(first, VIRTUAL_T0 + 10)

        c.advance(10)
        code, _ = c.run(
            "task", "upsert", "--id", tid,
            "--pr-url", "https://example.invalid/pr/2",  # a DIFFERENT PR
        )
        self.assertEqual(code, 0)
        row = task_row(c, tid)
        self.assertEqual(row["pr_url"], "https://example.invalid/pr/2")  # pr_url itself DID change
        self.assertEqual(row["pr_opened_at"], first)  # pr_opened_at did NOT

    def test_integrated_at_set_once_and_not_rewritten_by_a_later_upsert(self):
        c = Ctl(clock=VIRTUAL_T0)
        tid = "t-int-1"
        make_task(c, tid)
        self.assertIsNone(task_row(c, tid)["integrated_at"])

        c.advance(20)
        code, _ = c.run("task", "upsert", "--id", tid, "--state", "integrated")
        self.assertEqual(code, 0)
        first = task_row(c, tid)["integrated_at"]
        self.assertEqual(first, VIRTUAL_T0 + 20)

        c.advance(20)
        code, _ = c.run("task", "upsert", "--id", tid, "--state", "integrated")
        self.assertEqual(code, 0)
        self.assertEqual(task_row(c, tid)["integrated_at"], first)

    def test_upsert_that_never_touches_pr_url_or_state_leaves_both_null(self):
        """Control: an ordinary upsert (e.g. touching only `branch`) must
        not spuriously set either column."""
        c = Ctl(clock=VIRTUAL_T0)
        tid = "t-untouched"
        make_task(c, tid)
        c.advance(5)
        code, _ = c.run(
            "task", "upsert", "--id", tid, "--branch", "feat/whatever",
        )
        self.assertEqual(code, 0)
        row = task_row(c, tid)
        self.assertIsNone(row["pr_opened_at"])
        self.assertIsNone(row["integrated_at"])


class RestartReopenPreservesTimestamps(unittest.TestCase):
    """AC6: a restart/reopen must not clear or rewrite any of the three
    timestamps already set."""

    def test_full_chain_survives_a_reset_to_pending_and_re_upsert(self):
        c = Ctl(clock=VIRTUAL_T0)
        tid = "t-reopen"
        make_task(c, tid)

        code, body = c.run("task", "show", "--id", tid)
        v_ready = body["task"]["ready_at"]
        self.assertEqual(v_ready, VIRTUAL_T0)

        c.advance(10)
        c.run("task", "upsert", "--id", tid,
              "--pr-url", "https://example.invalid/pr/9")
        v_pr = task_row(c, tid)["pr_opened_at"]
        self.assertEqual(v_pr, VIRTUAL_T0 + 10)

        c.advance(10)
        c.run("task", "upsert", "--id", tid, "--state", "integrated")
        v_int = task_row(c, tid)["integrated_at"]
        self.assertEqual(v_int, VIRTUAL_T0 + 20)

        # simulate a restart/reopen: task returns to pending
        c.advance(10)
        code, _ = c.run("task", "upsert", "--id", tid, "--state", "pending")
        self.assertEqual(code, 0)
        row = task_row(c, tid)
        self.assertEqual(row["ready_at"], v_ready)
        self.assertEqual(row["pr_opened_at"], v_pr)
        self.assertEqual(row["integrated_at"], v_int)

        # the task is observed ready again (still pending/size S/no deps);
        # the guard must still hold even though reason_code=="ready" fires
        # a second time for the same task.
        c.advance(10)
        code, body = c.run("task", "show", "--id", tid)
        self.assertEqual(body["task"]["reason_code"], "ready")
        self.assertEqual(body["task"]["ready_at"], v_ready)

        # re-integrate with a different PR url; nothing already-set moves.
        c.advance(10)
        c.run("task", "upsert", "--id", tid,
              "--pr-url", "https://example.invalid/pr/10")
        c.run("task", "upsert", "--id", tid, "--state", "integrated")
        row = task_row(c, tid)
        self.assertEqual(row["ready_at"], v_ready)
        self.assertEqual(row["pr_opened_at"], v_pr)
        self.assertEqual(row["integrated_at"], v_int)


class DerivedTimingValues(unittest.TestCase):
    """AC2 (reuse started_at), AC7 (queue_time/time_to_main/
    active_delivery_time, null until known). Drives real admission through
    the actual policy evaluator, so real wall-clock time is used for the
    `started_at` leg (see module docstring) -- assertions are on the
    algebraic relationship between the three derived values, not a literal.
    """

    def test_derived_values_appear_in_order_and_are_null_until_known(self):
        c = Ctl()  # real wall clock -- required for a genuine admission
        policy = P.worker_semaphore(db=c.db)
        code, _ = c.run("run", "current", "--set", "run-223")
        self.assertEqual(code, 0)
        tid = "t-derive-1"
        title = make_task(c, tid)

        code, body = c.run("task", "show", "--id", tid)
        self.assertEqual(code, 0, body)
        task = body["task"]
        self.assertIsInstance(task["ready_at"], float)
        self.assertIsNone(task["queue_time"])
        self.assertIsNone(task["time_to_main"])
        self.assertIsNone(task["active_delivery_time"])

        g = admit(c, policy, tid, title=title)
        self.assertIsNotNone(g)

        code, body = c.run("task", "show", "--id", tid)
        task = body["task"]
        self.assertIsInstance(task["queue_time"], float)
        self.assertGreaterEqual(task["queue_time"], 0.0)
        self.assertIsNone(task["time_to_main"])
        self.assertIsNone(task["active_delivery_time"])
        queue_time = task["queue_time"]

        c.run("task", "upsert", "--id", tid,
              "--pr-url", "https://example.invalid/pr/derive")
        c.run("task", "upsert", "--id", tid, "--state", "integrated")

        code, body = c.run("task", "show", "--id", tid)
        task = body["task"]
        self.assertIsInstance(task["time_to_main"], float)
        self.assertIsInstance(task["active_delivery_time"], float)
        self.assertGreaterEqual(task["time_to_main"], 0.0)
        self.assertGreaterEqual(task["active_delivery_time"], 0.0)
        # time_to_main == queue_time + active_delivery_time, algebraically,
        # regardless of what the real wall clock's absolute values were.
        self.assertAlmostEqual(
            task["time_to_main"], queue_time + task["active_delivery_time"],
            places=3,
        )
        # started_at itself stays out of the exposed dict (ADR-0043's
        # established output contract, unchanged by this issue) -- confirm
        # directly against the row instead.
        self.assertNotIn("started_at", task)
        row = task_row(c, tid)
        self.assertAlmostEqual(
            task["queue_time"], row["started_at"] - row["ready_at"], places=3,
        )
        self.assertAlmostEqual(
            task["active_delivery_time"],
            row["integrated_at"] - row["started_at"], places=3,
        )


if __name__ == "__main__":
    unittest.main()
