"""Issue #212 / ADR-0033: classified worker admission.

These tests drive the real controller through subprocess and the real root
policy evaluator against the same temporary SQLite database.  They deliberately
keep allocation and dispatch separate: a NEW slot is only a reservation until
the policy consumes it, while a PLANNING slot can never create admission.
"""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from ctlutil import REPO, Ctl, run_parallel

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
import omnipods_policies as P  # noqa: E402


ALPHA = {
    "crew": "crew_alpha",
    "engineer": "eng_claude",
    "other_engineer": "eng_codex",
    "qa": "qa",
    "reviewer": "reviewer_codex",
}
BETA = {
    "crew": "crew_beta",
    "engineer": "beta_eng_claude",
    "other_engineer": "beta_eng_codex",
    "qa": "beta_qa",
    "reviewer": "beta_reviewer_codex",
}


def rows(db, table, where="", params=()):
    with contextlib.closing(sqlite3.connect(db)) as cx:
        cx.row_factory = sqlite3.Row
        query = f"SELECT * FROM {table}"
        if where:
            query += " WHERE " + where
        query += " ORDER BY rowid"
        return [dict(row) for row in cx.execute(query, params)]


def table_columns(db, table):
    with contextlib.closing(sqlite3.connect(db)) as cx:
        return {row[1] for row in cx.execute(f"PRAGMA table_info({table})")}


def send(agent=None, title=None, args=None, session_id=None):
    arguments = {"args": args or {}}
    if agent is not None:
        arguments["agent"] = agent
    if title is not None:
        arguments["title"] = title
    if session_id is not None:
        arguments["session_id"] = session_id
    return {
        "type": "tool_call",
        "data": {"name": "sys_session_send", "arguments": arguments},
    }


def create(**arguments):
    return {
        "type": "tool_call",
        "data": {"name": "sys_session_create", "arguments": arguments},
    }


class AdmissionFixture:
    """Small vocabulary shared by the phase tests."""

    roster = ALPHA

    def setUp(self):
        # The root policy reads wall-clock time, exactly as the server does.
        # TTL failures below damage only their fixture row explicitly rather
        # than mixing the controller's virtual clock with policy wall time.
        self.c = Ctl()
        code, body = self.c.run("run", "current", "--set", "run-212")
        self.assertEqual(code, 0, body)
        self.policy = P.worker_semaphore(db=self.c.db)

    def task(self, task_id="T", *, state="pending", size="S", pr=None,
             roster=None, agent=None, title="ship-T"):
        roster = roster or self.roster
        agent = agent or roster["engineer"]
        argv = [
            "task", "upsert", "--id", task_id,
            "--crew", roster["crew"], "--agent", agent,
            "--title", title, "--state", state,
        ]
        if size is not None:
            argv += ["--size", size]
        if pr is not None:
            argv += ["--pr-url", pr]
        code, body = self.c.run(*argv)
        self.assertEqual(code, 0, body)

    def acquire(self, *, task="T", agent=None, title="ship-T",
                purpose="implement", roster=None, ttl="600", claim=None):
        roster = roster or self.roster
        agent = agent or roster["engineer"]
        argv = [
            "slot", "acquire",
            "--holder", f"{roster['crew']}/{agent}",
            "--crew", roster["crew"], "--task", task,
            "--agent", agent, "--title", title, "--purpose", purpose,
            "--ttl", ttl,
        ]
        if claim is not None:
            argv += [
                "--claim-id", claim["claim_id"],
                "--claim-generation", claim["generation"],
                "--claim-run-id", claim["run_id"],
            ]
        return self.c.run(*argv)

    def event(self, slot, *, task="T", agent=None, title="ship-T",
              purpose="implement", roster=None, generation=None):
        roster = roster or self.roster
        agent = agent or roster["engineer"]
        args = {"slot_id": slot, "task_id": task, "purpose": purpose}
        if generation is not None:
            args["generation"] = generation
        return send(agent, title, args)

    def admit(self, *, task="T", roster=None, title="ship-T"):
        roster = roster or self.roster
        self.task(task, roster=roster, title=title)
        code, granted = self.acquire(task=task, roster=roster, title=title)
        self.assertEqual(code, 0, granted)
        result = self.policy(self.event(
            granted["slot_id"], task=task, roster=roster, title=title,
            generation=granted.get("generation"),
        ))
        self.assertEqual(result["result"], "ALLOW", result)
        return granted


class AdmissionSchemaMigration(unittest.TestCase):
    def test_fresh_schema_has_classification_and_admission_authority(self):
        c = Ctl()
        self.assertTrue({
            "phase", "expected_agent", "expected_title",
            "expected_purpose", "continuation_kind", "claim_id",
            "generation", "run_id",
        }.issubset(table_columns(c.db, "worker_slots")))
        self.assertTrue({
            "task_id", "new_slot_id", "crew", "agent", "title",
            "admitted_at",
        }.issubset(table_columns(c.db, "task_admissions")))

        indexes = rows(c.db, "sqlite_master", "type='index' AND tbl_name='worker_slots'")
        sql = "\n".join((row.get("sql") or "") for row in indexes).lower()
        self.assertIn("phase='new'", sql.replace('"', "").replace(" ", ""))
        self.assertIn("where", sql)

    def test_legacy_slot_migrates_to_null_phase_and_cannot_be_evidence(self):
        c = Ctl()
        new_columns = (
            "phase", "expected_agent", "expected_title", "expected_purpose",
            "continuation_kind", "claim_id",
        )
        existing_columns = table_columns(c.db, "worker_slots")
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute("DROP INDEX IF EXISTS one_active_new_reservation")
            cx.execute("DROP INDEX IF EXISTS one_active_planning_target")
            cx.execute("DROP INDEX IF EXISTS one_active_slot_per_claim")
            # ADR-0062 (#471) added two more partial indexes over these same
            # soon-to-be-dropped columns (phase/continuation_kind,
            # expected_agent/expected_title) -- drop them too, same reason
            # as the three above: SQLite refuses to DROP COLUMN a column an
            # existing index still references.
            cx.execute("DROP INDEX IF EXISTS one_active_qa_review_continuation")
            # Issue #165/ADR-0084 renamed this index to
            # `one_undispatched_dispatch_identity_per_repository` when it
            # widened it to include `repository_id`.
            cx.execute(
                "DROP INDEX IF EXISTS "
                "one_undispatched_dispatch_identity_per_repository")
            cx.execute("DROP TABLE IF EXISTS task_admissions")
            for column in new_columns:
                if column in existing_columns:
                    cx.execute(f"ALTER TABLE worker_slots DROP COLUMN {column}")
            legacy_id = cx.execute(
                "INSERT INTO worker_slots("
                "holder,crew,task_id,kind,state,acquired_at,expires_at) "
                "VALUES('crew_alpha/eng_claude','crew_alpha','legacy',"
                "'normal','active',1,9999999999)"
            ).lastrowid

        code, _ = c.run("init")
        self.assertEqual(code, 0)
        migrated = rows(c.db, "worker_slots", "id=?", (legacy_id,))[0]
        self.assertIsNone(migrated["phase"])
        self.assertEqual(rows(c.db, "task_admissions"), [])


class ProgramRunProducer(unittest.TestCase):
    def test_normal_allocation_needs_db_owned_current_run(self):
        c = Ctl()
        code, body = c.run(
            "task", "upsert", "--id", "T", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "ship-T",
            "--state", "pending", "--size", "S",
        )
        self.assertEqual(code, 0, body)
        classified = (
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", "T", "--agent", "eng_claude",
            "--title", "ship-T", "--purpose", "implement",
        )
        code, _ = c.run(*classified)
        self.assertEqual(code, 3)

        code, current = c.run("run", "current", "--new")
        self.assertEqual(code, 0, current)
        self.assertIsInstance(current["run_id"], str)
        self.assertTrue(current["run_id"])
        code, granted = c.run(*classified)
        self.assertEqual(code, 0, granted)
        self.assertEqual(granted["run_id"], current["run_id"])

    def test_program_startup_creates_run_before_any_dispatch(self):
        program = Path(REPO, "program", "config.yaml").read_text()
        startup = program[
            program.index("At Program startup"):program.index("## The control plane")
        ]
        self.assertIn("run current --new", startup)
        self.assertLess(
            startup.index("run current --new"),
            startup.index("sys_session_send"),
        )

    def test_run_new_is_atomic_idempotent_and_preserves_live_capability(self):
        c = Ctl()
        results = run_parallel([
            c.cmdline("run", "current", "--new"),
            c.cmdline("run", "current", "--new"),
        ])
        self.assertEqual([code for code, _ in results], [0, 0], results)
        run_ids = {body["run_id"] for _, body in results}
        self.assertEqual(len(run_ids), 1, results)
        self.assertEqual(
            sorted(body["created"] for _, body in results), [False, True],
        )
        run_id = run_ids.pop()

        code, body = c.run(
            "task", "upsert", "--id", "T", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "ship-T",
            "--state", "pending", "--size", "S",
        )
        self.assertEqual(code, 0, body)
        code, slot = c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", "T", "--agent", "eng_claude",
            "--title", "ship-T", "--purpose", "implement",
        )
        self.assertEqual(code, 0, slot)
        code, restarted = c.run("run", "current", "--new")
        self.assertEqual(code, 0, restarted)
        self.assertEqual(restarted, {
            "ok": True, "run_id": run_id, "created": False,
        })
        policy = P.worker_semaphore(db=c.db)
        result = policy(send("eng_claude", "ship-T", {
            "task_id": "T", "purpose": "implement",
            "slot_id": slot["slot_id"], "generation": slot["generation"],
        }))
        self.assertEqual(result["result"], "ALLOW", result)

    def test_run_new_rejects_inconsistent_active_work_without_current_run(self):
        c = Ctl()
        c.run("run", "current", "--set", "old-run")
        code, slot = c.acquire_normal(task="slot-task", title="slot-task")
        self.assertEqual(code, 0, slot)
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute("DELETE FROM meta WHERE key='current_run_id'")
        code, body = c.run("run", "current", "--new")
        self.assertEqual(code, 3, body)

        c2 = Ctl()
        c2.run("run", "current", "--set", "old-run")
        code, new = c2.acquire_normal(task="claim-task", title="claim-task")
        self.assertEqual(code, 0, new)
        c2.seed_admission(
            task="claim-task", slot_id=new["slot_id"], title="claim-task",
        )
        code, claim = c2.run(
            "claim", "acquire", "--task", "claim-task",
            "--claimant", "crew_alpha/lead", "--agent", "eng_claude",
            "--title", "claim-task",
        )
        self.assertEqual(code, 0, claim)
        c2.run("slot", "release", "--slot", new["slot_id"],
                "--holder", "crew_alpha/eng_claude")
        with contextlib.closing(sqlite3.connect(c2.db)) as cx, cx:
            cx.execute("DELETE FROM meta WHERE key='current_run_id'")
        code, body = c2.run("run", "current", "--new")
        self.assertEqual(code, 3, body)

    def test_run_new_takes_no_value_and_set_remains_explicit_overwrite(self):
        c = Ctl()
        code, _ = c.run("run", "current", "--new", "caller-value")
        self.assertEqual(code, 2)
        code, created = c.run("run", "current", "--new")
        self.assertEqual(code, 0, created)
        code, overwritten = c.run(
            "run", "current", "--set", "operator-test-value",
        )
        self.assertEqual(code, 0, overwritten)
        self.assertEqual(overwritten["run_id"], "operator-test-value")
        code, shown = c.run("run", "current")
        self.assertEqual(code, 0, shown)
        self.assertEqual(shown["run_id"], "operator-test-value")


class PlanningAdmission(AdmissionFixture, unittest.TestCase):
    def test_engineer_and_qa_planning_are_counted_but_never_admit(self):
        for agent, title in (
            (ALPHA["engineer"], "design-T-a"),
            (ALPHA["qa"], "test-T"),
        ):
            with self.subTest(agent=agent):
                self.task(title="ship-T")
                code, granted = self.acquire(
                    agent=agent, title=title, purpose="explore",
                )
                self.assertEqual(code, 0, granted)
                slot = rows(self.c.db, "worker_slots", "id=?",
                            (granted["slot_id"],))[0]
                self.assertEqual(slot["phase"], "planning")
                result = self.policy(self.event(
                    granted["slot_id"], agent=agent, title=title,
                    purpose="explore", generation=granted.get("generation"),
                ))
                self.assertEqual(result["result"], "ALLOW", result)
                self.assertEqual(rows(self.c.db, "task_admissions"), [])
                self.assertEqual(rows(self.c.db, "task_claims"), [])
                _, shown = self.c.run("task", "show", "--id", "T")
                self.assertEqual(shown["task"]["state"], "pending")
                self.c.run("slot", "release", "--slot", granted["slot_id"],
                           "--holder", f"crew_alpha/{agent}")

    def test_planning_wrong_target_or_purpose_denies_without_slot(self):
        self.task()
        before = rows(self.c.db, "worker_slots")
        for agent, purpose in (
            (ALPHA["reviewer"], "explore"),
            (ALPHA["engineer"], "review"),
        ):
            with self.subTest(agent=agent, purpose=purpose):
                code, _ = self.acquire(
                    agent=agent, title="design-T", purpose=purpose,
                )
                self.assertEqual(code, 3)
                self.assertEqual(rows(self.c.db, "worker_slots"), before)

    def test_planning_then_new_and_new_then_planning_are_mutually_exclusive(self):
        self.task()
        code, planning = self.acquire(
            agent=ALPHA["qa"], title="test-T", purpose="search",
        )
        self.assertEqual(code, 0, planning)
        code, _ = self.acquire()
        self.assertEqual(code, 3)
        self.c.run("slot", "release", "--slot", planning["slot_id"],
                   "--holder", "crew_alpha/qa")

        code, new = self.acquire()
        self.assertEqual(code, 0, new)
        code, _ = self.acquire(
            agent=ALPHA["qa"], title="test-T", purpose="explore",
        )
        self.assertEqual(code, 3)

    def test_concurrent_planning_and_new_have_one_phase_winner(self):
        self.task()
        commands = [
            self.c.cmdline(
                "slot", "acquire", "--holder", "crew_alpha/qa",
                "--crew", "crew_alpha", "--task", "T", "--agent", "qa",
                "--title", "test-T", "--purpose", "explore", "--ttl", "600",
            ),
            self.c.cmdline(
                "slot", "acquire", "--holder", "crew_alpha/eng_claude",
                "--crew", "crew_alpha", "--task", "T", "--agent", "eng_claude",
                "--title", "ship-T", "--purpose", "implement", "--ttl", "600",
            ),
        ]
        results = run_parallel(commands)
        self.assertEqual([code for code, _ in results].count(0), 1, results)
        self.assertEqual([code for code, _ in results].count(3), 1, results)
        active = rows(self.c.db, "worker_slots", "state='active'")
        self.assertEqual(len(active), 1)
        self.assertIn(active[0]["phase"], {"planning", "new"})

    def test_global_planning_cap_leaves_one_normal_slot(self):
        for index, agent in enumerate((ALPHA["engineer"], ALPHA["qa"],
                                       BETA["engineer"])):
            roster = ALPHA if index < 2 else BETA
            task = f"P{index}"
            self.task(task, roster=roster, title=f"ship-{task}")
            code, _ = self.acquire(
                task=task, roster=roster, agent=agent,
                title=f"design-{task}", purpose="explore",
            )
            self.assertEqual(code, 0 if index < 2 else 3)


class NewAdmission(AdmissionFixture, unittest.TestCase):
    def test_new_reservation_is_idempotent_but_competitor_denies(self):
        self.task()
        code, first = self.acquire()
        self.assertEqual(code, 0, first)
        self.assertEqual(first["phase"], "new")
        self.assertFalse(first.get("reused", False))
        self.assertEqual(rows(self.c.db, "task_admissions"), [])
        _, shown = self.c.run("task", "show", "--id", "T")
        self.assertEqual(shown["task"]["state"], "pending")

        code, retry = self.acquire()
        self.assertEqual(code, 0, retry)
        self.assertTrue(retry["reused"])
        self.assertEqual(retry["slot_id"], first["slot_id"])

        code, _ = self.acquire(
            agent=ALPHA["other_engineer"], title="other-T",
        )
        self.assertEqual(code, 3)
        self.assertEqual(len(rows(self.c.db, "worker_slots")), 1)

    def test_consume_atomically_admits_and_transitions_once(self):
        self.task()
        _, granted = self.acquire()
        # ADR-0043 (corrected 2026-08-29 per staff review, citing ADR-0033):
        # allocation alone must never stamp started_at -- only genuine
        # consumption/admission may.
        self.assertIsNone(
            rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"]
        )
        event = self.event(
            granted["slot_id"], generation=granted.get("generation"),
        )
        self.assertEqual(self.policy(event)["result"], "ALLOW")
        admission = rows(self.c.db, "task_admissions")
        self.assertEqual(len(admission), 1)
        self.assertEqual(admission[0]["task_id"], "T")
        self.assertEqual(admission[0]["new_slot_id"], granted["slot_id"])
        _, shown = self.c.run("task", "show", "--id", "T")
        self.assertEqual(shown["task"]["state"], "building")
        started = rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"]
        self.assertIsInstance(started, float)
        self.assertEqual(self.policy(event)["result"], "DENY")
        self.assertEqual(rows(self.c.db, "task_admissions"), admission)
        self.assertEqual(
            rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"], started,
        )

    def test_denied_or_abandoned_new_reservation_never_poisons_started_at(self):
        """Issue #214 / ADR-0043 (corrected 2026-08-29 per staff review,
        citing ADR-0033): `started_at` must be written by `worker_semaphore`'s
        `phase=='new'` consume branch, never by `slot acquire` (allocation).
        Pre-fix, `cmd_slot_acquire` stamped `started_at` the instant a NEW
        slot was ALLOCATED, so a reservation later DENIED at consume time --
        a routine, ADR-0033-documented retry path -- or one that simply
        expired unconsumed left a permanent, false, uncorrectable
        `started_at` once the task was genuinely admitted later, because the
        allocation-time write's own `AND started_at IS NULL` guard then
        blocked the real value from ever landing. This reproduces exactly
        that shape (denial via readiness drift at consume time, mirroring
        `test_readiness_drift_at_consume_denies_without_partial_mutation`)
        and proves the real admission's timestamp lands cleanly.
        """
        self.task("dep", state="integrated", title="ship-dep")
        self.task()
        self.c.run("task", "dependency-add", "--id", "T", "--depends-on", "dep")

        _, first = self.acquire()
        self.assertIsNone(
            rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"]
        )
        self.c.run("task", "upsert", "--id", "dep", "--state", "building")
        result = self.policy(self.event(
            first["slot_id"], generation=first.get("generation"),
        ))
        self.assertEqual(result["result"], "DENY", result)
        self.assertEqual(rows(self.c.db, "task_admissions"), [])
        self.assertIsNone(
            rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"]
        )

        self.c.run("slot", "release", "--slot", first["slot_id"],
                   "--holder", "crew_alpha/eng_claude")
        self.c.run("task", "upsert", "--id", "dep", "--state", "integrated")

        _, second = self.acquire()
        self.assertNotEqual(second["slot_id"], first["slot_id"])
        result = self.policy(self.event(
            second["slot_id"], generation=second.get("generation"),
        ))
        self.assertEqual(result["result"], "ALLOW", result)
        admission = rows(self.c.db, "task_admissions")
        self.assertEqual(len(admission), 1)
        self.assertEqual(admission[0]["new_slot_id"], second["slot_id"])
        started = rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"]
        self.assertIsInstance(started, float)

    def test_new_allocation_reuses_adr0032_reasoning(self):
        cases = (
            ("missing", None, "pending"),
            ("large", "L", "pending"),
            ("started", "S", "building"),
        )
        for task_id, size, state in cases:
            with self.subTest(task=task_id):
                self.task(task_id, size=size, state=state, title=f"ship-{task_id}")
                code, _ = self.acquire(task=task_id, title=f"ship-{task_id}")
                self.assertEqual(code, 3)

        self.task("dep", state="pending", title="ship-dep")
        self.task("blocked", state="pending", title="ship-blocked")
        self.c.run("task", "dependency-add", "--id", "blocked",
                   "--depends-on", "dep")
        code, _ = self.acquire(task="blocked", title="ship-blocked")
        self.assertEqual(code, 3)

        self.task("cycle-a", title="ship-cycle-a")
        self.task("cycle-b", title="ship-cycle-b")
        self.c.run("task", "dependency-add", "--id", "cycle-a",
                   "--depends-on", "cycle-b")
        self.c.run("task", "dependency-add", "--id", "cycle-b",
                   "--depends-on", "cycle-a")
        code, _ = self.acquire(task="cycle-a", title="ship-cycle-a")
        self.assertEqual(code, 3)

        self.task("invalid", title="ship-invalid")
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("PRAGMA ignore_check_constraints=ON")
            cx.execute("UPDATE tasks SET size='XL' WHERE id='invalid'")
        code, _ = self.acquire(task="invalid", title="ship-invalid")
        self.assertEqual(code, 3)

    def test_readiness_drift_at_consume_denies_without_partial_mutation(self):
        self.task("dep", state="integrated", title="ship-dep")
        self.task()
        self.c.run("task", "dependency-add", "--id", "T",
                   "--depends-on", "dep")
        _, granted = self.acquire()
        self.c.run("task", "upsert", "--id", "dep", "--state", "building")

        before_slot = rows(self.c.db, "worker_slots", "id=?",
                           (granted["slot_id"],))[0]
        result = self.policy(self.event(
            granted["slot_id"], generation=granted.get("generation"),
        ))
        self.assertEqual(result["result"], "DENY", result)
        self.assertEqual(rows(self.c.db, "task_admissions"), [])
        after_slot = rows(self.c.db, "worker_slots", "id=?",
                          (granted["slot_id"],))[0]
        self.assertEqual(after_slot, before_slot)
        _, shown = self.c.run("task", "show", "--id", "T")
        self.assertEqual(shown["task"]["state"], "pending")

    def test_abandoned_new_never_unlocks_continuation(self):
        self.task()
        _, new = self.acquire()
        self.c.run("slot", "release", "--slot", new["slot_id"],
                   "--holder", "crew_alpha/eng_claude")
        self.assertEqual(rows(self.c.db, "task_admissions"), [])
        _, shown = self.c.run("task", "show", "--id", "T")
        self.assertEqual(shown["task"]["state"], "pending")

        self.c.run("task", "upsert", "--id", "T", "--state", "ready",
                   "--pr-url", "https://github.com/o/r/pull/1")
        code, _ = self.acquire(
            agent=ALPHA["qa"], title="test-T", purpose="implement",
        )
        self.assertEqual(code, 3)

    def test_competing_new_reservations_have_one_winner(self):
        self.task()
        commands = [
            self.c.cmdline(
                "slot", "acquire", "--holder", f"crew_alpha/{agent}",
                "--crew", "crew_alpha", "--task", "T", "--agent", agent,
                "--title", title, "--purpose", "implement", "--ttl", "600",
            )
            for agent, title in (
                (ALPHA["engineer"], "ship-T"),
                (ALPHA["other_engineer"], "other-T"),
            )
        ]
        results = run_parallel(commands)
        self.assertEqual([code for code, _ in results].count(0), 1, results)
        self.assertEqual([code for code, _ in results].count(3), 1, results)
        self.assertEqual(len(rows(self.c.db, "worker_slots", "phase='new'")), 1)


class ContinuationAdmission(AdmissionFixture, unittest.TestCase):
    def continuation(self, agent, title, purpose):
        code, granted = self.acquire(agent=agent, title=title, purpose=purpose)
        self.assertEqual(code, 0, granted)
        result = self.policy(self.event(
            granted["slot_id"], agent=agent, title=title, purpose=purpose,
            generation=granted.get("generation"),
        ))
        return result, granted

    def test_continuation_never_touches_started_at(self):
        """Issue #214 / ADR-0043 (corrected 2026-08-29). A continuation slot
        (qa here) is structurally excluded from the `phase=='new'` branch
        that writes `started_at`, so an already-admitted task's value must
        survive a continuation consume unchanged."""
        self.admit()
        started = rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"]
        self.assertIsInstance(started, float)
        self.c.run("task", "upsert", "--id", "T",
                   "--pr-url", "https://github.com/o/r/pull/1")
        result, slot = self.continuation(ALPHA["qa"], "test-T", "implement")
        self.assertEqual(result["result"], "ALLOW", result)
        self.assertEqual(
            rows(self.c.db, "tasks", "id=?", ("T",))[0]["started_at"], started,
        )

    def test_qa_and_review_need_admission_pr_role_and_exact_state(self):
        self.admit()
        permitted = ("building", "ready", "fixing")
        for state in permitted:
            with self.subTest(state=state, role="qa"):
                self.c.run("task", "upsert", "--id", "T", "--state", state,
                           "--pr-url", "https://github.com/o/r/pull/1")
                result, slot = self.continuation(
                    ALPHA["qa"], "test-T", "implement",
                )
                self.assertEqual(result["result"], "ALLOW", result)
                self.c.run("slot", "release", "--slot", slot["slot_id"],
                           "--holder", "crew_alpha/qa")

            with self.subTest(state=state, role="review"):
                result, slot = self.continuation(
                    ALPHA["reviewer"], "review-T", "review",
                )
                self.assertEqual(result["result"], "ALLOW", result)
                self.c.run("slot", "release", "--slot", slot["slot_id"],
                           "--holder", "crew_alpha/reviewer_codex")

        for state in ("pending", "integrated", "done", "mystery"):
            with self.subTest(state=state):
                self.c.run("task", "upsert", "--id", "T", "--state", state)
                code, _ = self.acquire(
                    agent=ALPHA["qa"], title="test-T", purpose="implement",
                )
                self.assertEqual(code, 3)

    def test_qa_and_review_wrong_or_missing_pr_evidence_denies(self):
        self.admit()
        for agent, title, purpose in (
            (ALPHA["qa"], "test-T", "implement"),
            (ALPHA["reviewer"], "review-T", "review"),
        ):
            with self.subTest(agent=agent):
                code, _ = self.acquire(agent=agent, title=title, purpose=purpose)
                self.assertEqual(code, 3)
        self.c.run("task", "upsert", "--id", "T",
                   "--pr-url", "https://github.com/o/r/pull/1")
        code, _ = self.acquire(
            agent=ALPHA["reviewer"], title="review-T", purpose="implement",
        )
        self.assertEqual(code, 3)

    def test_qa_pr_evidence_is_rechecked_at_root_consume(self):
        self.admit()
        self.c.run("task", "upsert", "--id", "T",
                   "--pr-url", "https://github.com/o/r/pull/1")
        code, slot = self.acquire(
            agent=ALPHA["qa"], title="test-T", purpose="implement",
        )
        self.assertEqual(code, 0, slot)
        before = rows(self.c.db, "worker_slots", "id=?", (slot["slot_id"],))[0]
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("UPDATE tasks SET pr_url=NULL WHERE id='T'")
        result = self.policy(self.event(
            slot["slot_id"], agent=ALPHA["qa"], title="test-T",
            purpose="implement", generation=slot.get("generation"),
        ))
        self.assertEqual(result["result"], "DENY", result)
        self.assertEqual(
            rows(self.c.db, "worker_slots", "id=?", (slot["slot_id"],))[0],
            before,
        )

    def test_implementer_continuation_requires_exact_live_claim(self):
        self.admit()
        self.c.run("task", "upsert", "--id", "T",
                   "--pr-url", "https://github.com/o/r/pull/1")
        code, _ = self.acquire()
        self.assertEqual(code, 3)

        code, claim = self.c.run(
            "claim", "acquire", "--task", "T", "--claimant", "crew_alpha/lead",
            "--agent", ALPHA["engineer"], "--title", "ship-T",
            "--reason", "review blocker", "--ttl", "600",
        )
        self.assertEqual(code, 0, claim)
        code, slot = self.acquire(claim=claim)
        self.assertEqual(code, 0, slot)
        self.assertEqual(slot["phase"], "continuation")
        self.assertEqual(slot["continuation_kind"], "implementer")
        self.assertEqual(slot["claim_id"], claim["claim_id"])
        result = self.policy(self.event(
            slot["slot_id"], generation=slot.get("generation"),
        ))
        self.assertEqual(result["result"], "ALLOW", result)

    def test_wrong_claim_identity_or_expired_claim_denies(self):
        self.admit()
        code, _ = self.c.run(
            "claim", "acquire", "--task", "T", "--claimant", "crew_alpha/lead",
            "--agent", ALPHA["other_engineer"], "--title", "wrong-T",
            "--ttl", "600",
        )
        self.assertEqual(code, 3)

        code, claim = self.c.run(
            "claim", "acquire", "--task", "T", "--claimant", "crew_alpha/lead",
            "--agent", ALPHA["engineer"], "--title", "ship-T",
            "--ttl", "600",
        )
        self.assertEqual(code, 0)
        code, slot = self.acquire(claim=claim)
        self.assertEqual(code, 0, slot)
        before = rows(self.c.db, "worker_slots", "id=?", (slot["slot_id"],))[0]
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE task_claims SET generation=generation+1 WHERE id=?",
                (claim["claim_id"],),
            )
        result = self.policy(self.event(
            slot["slot_id"], generation=slot.get("generation"),
        ))
        self.assertEqual(result["result"], "DENY", result)
        self.assertEqual(
            rows(self.c.db, "worker_slots", "id=?", (slot["slot_id"],))[0],
            before,
        )

        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE task_claims SET generation=?,expires_at=0 WHERE id=?",
                (claim["generation"], claim["claim_id"]),
            )
        result = self.policy(self.event(
            slot["slot_id"], generation=slot.get("generation"),
        ))
        self.assertEqual(result["result"], "DENY", result)
        self.assertEqual(
            rows(self.c.db, "worker_slots", "id=?", (slot["slot_id"],))[0],
            before,
        )

    def test_implementer_continuation_rejects_run_task_and_slot_mismatch(self):
        self.admit()
        code, claim = self.c.run(
            "claim", "acquire", "--task", "T", "--claimant", "crew_alpha/lead",
            "--agent", ALPHA["engineer"], "--title", "ship-T", "--ttl", "600",
        )
        self.assertEqual(code, 0, claim)
        code, slot = self.acquire(claim=claim)
        self.assertEqual(code, 0, slot)

        def authority_state():
            return {
                "slot": rows(
                    self.c.db, "worker_slots", "id=?", (slot["slot_id"],),
                )[0],
                "claim": rows(
                    self.c.db, "task_claims", "id=?", (claim["claim_id"],),
                )[0],
                "admission": rows(self.c.db, "task_admissions", "task_id='T'")[0],
                "task": rows(self.c.db, "tasks", "id='T'")[0],
            }

        def denied_without_mutation(event):
            before = authority_state()
            result = self.policy(event)
            self.assertEqual(result["result"], "DENY", result)
            self.assertEqual(authority_state(), before)
            self.assertIsNone(before["slot"]["dispatched_at"])

        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE task_claims SET run_id='persisted-other-run' WHERE id=?",
                (claim["claim_id"],),
            )
        denied_without_mutation(self.event(
            slot["slot_id"], generation=slot["generation"],
        ))
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE task_claims SET run_id=? WHERE id=?",
                (claim["run_id"], claim["claim_id"]),
            )
            cx.execute(
                "UPDATE meta SET value='current-other-run' "
                "WHERE key='current_run_id'",
            )
        denied_without_mutation(self.event(
            slot["slot_id"], generation=slot["generation"],
        ))
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE meta SET value=? WHERE key='current_run_id'",
                (claim["run_id"],),
            )

        # ADR-0062 (#471) removed the two cases that used to live here:
        # a caller-supplied wrong ``task_id`` and a caller-supplied
        # invented/wrong ``slot_id``. Neither is read by ``worker_semaphore``
        # any more -- both are derived from the control-plane row matching
        # ``(kind, agent, title, purpose)``, so there is no longer any event
        # shape that can express "the same admission, but for a different
        # task" or "the same admission, but a different slot id"; the real
        # ``sys_session_send`` schema cannot carry either field at all. The
        # run_id-mismatch cases above remain meaningful and are unchanged --
        # ``run_id`` is not part of the derivation key, it is still an
        # explicit downstream cross-check against ``task_claims``/``meta``.

        result = self.policy(self.event(
            slot["slot_id"], generation=slot["generation"],
        ))
        self.assertEqual(result["result"], "ALLOW", result)

    def test_claim_cannot_be_preseeded_or_caller_fenced(self):
        self.task()
        code, _ = self.c.run(
            "claim", "acquire", "--task", "unknown",
            "--claimant", "crew_alpha/lead", "--agent", ALPHA["engineer"],
            "--title", "ship-T",
        )
        self.assertEqual(code, 3)
        code, _ = self.c.run(
            "claim", "acquire", "--task", "T",
            "--claimant", "crew_alpha/lead", "--agent", ALPHA["engineer"],
            "--title", "ship-T",
        )
        self.assertEqual(code, 3)
        self.assertEqual(rows(self.c.db, "task_claims"), [])

        self.admit()
        code, _ = self.c.run(
            "claim", "acquire", "--task", "T",
            "--claimant", "crew_alpha/lead", "--agent", ALPHA["engineer"],
            "--title", "ship-T", "--generation", "99",
            "--run-id", "caller-chosen",
        )
        self.assertEqual(code, 2)
        self.assertEqual(rows(self.c.db, "task_claims"), [])

        code, _ = self.c.run(
            "claim", "acquire", "--task", "T",
            "--claimant", "crew_alpha/lead",
            "--agent", ALPHA["other_engineer"], "--title", "wrong-T",
        )
        self.assertEqual(code, 3)
        code, claim = self.c.run(
            "claim", "acquire", "--task", "T",
            "--claimant", "crew_alpha/lead", "--agent", ALPHA["engineer"],
            "--title", "ship-T",
        )
        self.assertEqual(code, 0, claim)
        self.assertGreater(claim["generation"], 0)
        self.assertEqual(claim["run_id"], "run-212")

    def test_beta_planning_qa_review_and_implementer_paths_are_symmetric(self):
        task, title = "B", "ship-B"
        self.task(task, roster=BETA, title=title)
        code, planning = self.acquire(
            task=task, roster=BETA, agent=BETA["qa"], title="test-B",
            purpose="explore",
        )
        self.assertEqual(code, 0, planning)
        self.assertEqual(planning["phase"], "planning")
        self.assertEqual(self.policy(self.event(
            planning["slot_id"], task=task, roster=BETA, agent=BETA["qa"],
            title="test-B", purpose="explore",
            generation=planning.get("generation"),
        ))["result"], "ALLOW")
        self.c.run("slot", "release", "--slot", planning["slot_id"],
                   "--holder", "crew_beta/beta_qa")

        code, admitted = self.acquire(task=task, roster=BETA, title=title)
        self.assertEqual(code, 0, admitted)
        self.assertEqual(self.policy(self.event(
            admitted["slot_id"], task=task, roster=BETA, title=title,
            generation=admitted.get("generation"),
        ))["result"], "ALLOW")
        self.c.run("task", "upsert", "--id", task,
                   "--pr-url", "https://github.com/o/r/pull/2")

        for agent, worker_title, purpose in (
            (BETA["qa"], "test-B", "implement"),
            (BETA["reviewer"], "review-B", "review"),
        ):
            code, slot = self.acquire(
                task=task, roster=BETA, agent=agent,
                title=worker_title, purpose=purpose,
            )
            self.assertEqual(code, 0, slot)
            result = self.policy(self.event(
                slot["slot_id"], task=task, roster=BETA, agent=agent,
                title=worker_title, purpose=purpose,
                generation=slot.get("generation"),
            ))
            self.assertEqual(result["result"], "ALLOW", result)
            self.c.run("slot", "release", "--slot", slot["slot_id"],
                       "--holder", f"crew_beta/{agent}")

        code, claim = self.c.run(
            "claim", "acquire", "--task", task, "--claimant", "crew_beta/lead",
            "--agent", BETA["engineer"], "--title", title, "--ttl", "600",
        )
        self.assertEqual(code, 0, claim)
        code, slot = self.acquire(
            task=task, roster=BETA, title=title, claim=claim,
        )
        self.assertEqual(code, 0, slot)
        result = self.policy(self.event(
            slot["slot_id"], task=task, roster=BETA, title=title,
            generation=slot.get("generation"),
        ))
        self.assertEqual(result["result"], "ALLOW", result)

    def test_cross_crew_review_uses_reviewer_crew_without_reowning_task(self):
        for task_roster, reviewer_roster, task in (
            (ALPHA, BETA, "alpha-task"),
            (BETA, ALPHA, "beta-task"),
        ):
            with self.subTest(task_crew=task_roster["crew"]):
                title = f"ship-{task}"
                self.task(task, roster=task_roster, title=title)
                code, new = self.acquire(
                    task=task, roster=task_roster, title=title,
                )
                self.assertEqual(code, 0, new)
                self.assertEqual(self.policy(self.event(
                    new["slot_id"], task=task, roster=task_roster, title=title,
                    generation=new.get("generation"),
                ))["result"], "ALLOW")
                self.c.run(
                    "task", "upsert", "--id", task,
                    "--pr-url", f"https://github.com/o/r/pull/{1 if task == 'alpha-task' else 2}",
                )

                reviewer = reviewer_roster["reviewer"]
                review_title = f"cross-review-{task}"
                code, slot = self.acquire(
                    task=task, roster=reviewer_roster, agent=reviewer,
                    title=review_title, purpose="review",
                )
                self.assertEqual(code, 0, slot)
                result = self.policy(self.event(
                    slot["slot_id"], task=task, roster=reviewer_roster,
                    agent=reviewer, title=review_title, purpose="review",
                    generation=slot.get("generation"),
                ))
                self.assertEqual(result["result"], "ALLOW", result)
                self.c.run("slot", "release", "--slot", slot["slot_id"],
                               "--holder", f"{reviewer_roster['crew']}/{reviewer}")
                self.c.run("slot", "release", "--slot", new["slot_id"],
                               "--holder", f"{task_roster['crew']}/{task_roster['engineer']}")

    def test_one_claim_cannot_mint_two_implementer_continuation_capabilities(self):
        self.admit()
        code, claim = self.c.run(
            "claim", "acquire", "--task", "T", "--claimant", "crew_alpha/lead",
            "--agent", ALPHA["engineer"], "--title", "ship-T", "--ttl", "600",
        )
        self.assertEqual(code, 0, claim)
        code, first = self.acquire(claim=claim)
        self.assertEqual(code, 0, first)

        code, second = self.acquire(claim=claim)
        if code == 0:
            self.assertTrue(second["reused"])
            self.assertEqual(second["slot_id"], first["slot_id"])
        else:
            self.assertEqual(code, 3, second)
        active = rows(
            self.c.db, "worker_slots",
            "task_id='T' AND state='active' AND phase='continuation' "
            "AND continuation_kind='implementer'",
        )
        self.assertEqual(len(active), 1)


class SharedFunctionAdmissionSeed(AdmissionFixture, unittest.TestCase):
    """Issue #488 / ADR-0068 addendum: `task upsert --seed-shared-admission`.

    A task charted via `nightshift-ctl task upsert` (Program's charter path
    for shared-function seats -- product/staff/platform/security/release)
    never goes through worker_semaphore's `phase=='new'` branch, the only
    place a `task_admissions` row is otherwise written, because none of
    those seat names is a `COUNTED_AGENTS` key. Without that row, EVERY
    `reviewer_*`/`qa` continuation dispatch against such a task is denied
    with "only the task implementer may request NEW" -- a NEW-only message,
    because `admission is None` diverts every continuation attempt into that
    branch before any role-specific check ever runs.
    """

    def shared_task(self, task_id="S", *, crew="product", state="pending",
                     pr=None, title="ship-S", agent=None, size="S"):
        agent = agent or crew
        argv = ["task", "upsert", "--id", task_id, "--crew", crew,
                "--agent", agent, "--title", title, "--state", state,
                "--size", size]
        if pr is not None:
            argv += ["--pr-url", pr]
        code, body = self.c.run(*argv)
        self.assertEqual(code, 0, body)
        return body

    def seed(self, task_id="S"):
        return self.c.run("task", "upsert", "--id", task_id,
                           "--seed-shared-admission")

    def test_missing_task_denied_no_insert_of_any_kind(self):
        code, body = self.seed("does-not-exist-S")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "task_missing")
        self.assertEqual(rows(self.c.db, "tasks", "id='does-not-exist-S'"), [])
        self.assertEqual(rows(self.c.db, "task_admissions"), [])

    def test_crew_alpha_and_crew_beta_are_refused_not_ignored(self):
        for roster in (ALPHA, BETA):
            with self.subTest(crew=roster["crew"]):
                task_id = f"own-{roster['crew']}"
                self.task(task_id, roster=roster, state="building")
                code, body = self.seed(task_id)
                self.assertEqual(code, 3, body)
                self.assertFalse(body["ok"])
                self.assertIn(roster["crew"], body["reason"])
                self.assertEqual(
                    rows(self.c.db, "task_admissions", "task_id=?", (task_id,)),
                    [],
                )

    def test_wrong_state_denied_no_row_written(self):
        for state in ("pending", "integrated", "done", "mystery"):
            with self.subTest(state=state):
                task_id = f"S-{state}"
                self.shared_task(task_id, state=state)
                code, body = self.seed(task_id)
                self.assertEqual(code, 3, body)
                self.assertFalse(body["ok"])
                self.assertIn(state, body["reason"])
                self.assertEqual(
                    rows(self.c.db, "task_admissions", "task_id=?", (task_id,)),
                    [],
                )

    def test_seed_creates_seed_slot_and_admission_row(self):
        self.shared_task("S", state="building")
        code, body = self.seed("S")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["created"])
        admission = rows(self.c.db, "task_admissions", "task_id='S'")
        self.assertEqual(len(admission), 1)
        self.assertEqual(admission[0]["crew"], "product")
        slot = rows(self.c.db, "worker_slots", "id=?",
                    (admission[0]["new_slot_id"],))
        self.assertEqual(len(slot), 1)
        self.assertEqual(slot[0]["kind"], "seed")
        self.assertEqual(slot[0]["state"], "released")
        self.assertEqual(slot[0]["generation"], 0)
        self.assertIsNone(slot[0]["phase"])

    def test_idempotent_second_seed_is_a_no_op_not_a_second_row(self):
        self.shared_task("S", state="building")
        code, first = self.seed("S")
        self.assertEqual(code, 0, first)
        self.assertTrue(first["created"])
        code, second = self.seed("S")
        self.assertEqual(code, 0, second)
        self.assertFalse(second["created"])
        self.assertEqual(
            second["task_admissions"]["new_slot_id"],
            first["task_admissions"]["new_slot_id"],
        )
        self.assertEqual(len(rows(self.c.db, "task_admissions", "task_id='S'")), 1)

    def test_generation_inertness_seed_row_does_not_perturb_numbering(self):
        """AC7: `_next_slot_generation` is COALESCE(MAX(generation),0)+1 over
        ALL worker_slots rows for the task, unfiltered by state -- prove the
        seed row's generation=0 does not shift a real acquire's numbering."""
        self.shared_task("S", state="building", agent="eng_claude")
        code, seeded = self.seed("S")
        self.assertEqual(code, 0, seeded)
        # Flip crew to crew_alpha's own so a genuine NEW acquire is legal --
        # a fresh unseeded task would also produce generation=1 here.
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("DELETE FROM task_admissions WHERE task_id='S'")
            cx.execute("UPDATE tasks SET crew='crew_alpha', state='pending' "
                       "WHERE id='S'")
        code, granted = self.acquire(task="S", title="ship-S")
        self.assertEqual(code, 0, granted)
        self.assertEqual(granted["generation"], 1)

    def test_budget_and_active_slot_counts_unaffected_by_seed(self):
        self.shared_task("S", state="building")
        # Direct counts, exactly what worker_semaphore/_derive_normal_phase
        # read: active rows, and phase IN ('new','planning') rows.
        def counts():
            active = rows(self.c.db, "worker_slots", "state='active'")
            phased = rows(
                self.c.db, "worker_slots",
                "state='active' AND phase IN ('new','planning')",
            )
            return len(active), len(phased)

        before_counts = counts()
        code, body = self.seed("S")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["created"])
        self.assertEqual(counts(), before_counts)

    def test_seed_refuses_combination_with_any_other_field_toctou(self):
        """security round-1 MERGE_BLOCKER (PR #530): combining
        --seed-shared-admission with a field update (e.g. --crew) in the SAME
        call let the update's own new value be read back by the admission
        check before anyone else observed it -- a crew_alpha/crew_beta task
        could forge shared-function admission and then flip crew back."""
        self.task("victim", roster=ALPHA, state="building", title="ship-victim")
        code, body = self.c.run(
            "task", "upsert", "--id", "victim", "--crew", "product",
            "--seed-shared-admission",
        )
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertIn("crew", body["reason"])
        # Neither the forged crew change NOR any admission survived.
        task_row = rows(self.c.db, "tasks", "id='victim'")[0]
        self.assertEqual(task_row["crew"], "crew_alpha")
        self.assertEqual(rows(self.c.db, "task_admissions", "task_id='victim'"), [])

    def test_seed_refuses_combination_with_state_too(self):
        """The --state + --seed-shared-admission one-call combo (found by
        crew_beta's review as an undocumented working path) is refused for
        the same TOCTOU reason -- state changes go through their own,
        separate `task upsert --state ...` call, same as crew."""
        self.shared_task("S", state="pending")
        code, body = self.seed("S")  # not yet building -- would be denied anyway
        self.assertEqual(code, 3, body)
        code, body = self.c.run(
            "task", "upsert", "--id", "S", "--state", "building",
            "--seed-shared-admission",
        )
        self.assertEqual(code, 3, body)
        self.assertIn("--seed-shared-admission must be the only change",
                       body["reason"])
        self.assertEqual(rows(self.c.db, "tasks", "id='S'")[0]["state"], "pending")

    def test_selftest_passes_unchanged(self):
        self.shared_task("S", state="building")
        code, _ = self.c.run("selftest")
        self.assertEqual(code, 0)
        code, body = self.seed("S")
        self.assertEqual(code, 0, body)
        code, _ = self.c.run("selftest")
        self.assertEqual(code, 0)

    def test_reviewer_continuation_succeeds_qa_continuation_still_denied(self):
        """RT-33 (DoD condition 18/#488): after seeding, `reviewer_*`/
        `beta_reviewer_*` continuation is admitted; `qa`/`beta_qa`
        continuation stays denied on the PRE-EXISTING crew-mismatch check
        (bin/nightshift-ctl _derive_normal_phase, issue #527, not fixed
        here) -- documented as a passing assertion, not a silent gap."""
        self.shared_task("S", state="building",
                          pr="https://github.com/o/r/pull/526")
        code, seeded = self.seed("S")
        self.assertEqual(code, 0, seeded)

        for roster in (ALPHA, BETA):
            with self.subTest(reviewer=roster["reviewer"]):
                code, slot = self.acquire(
                    task="S", agent=roster["reviewer"], title="review-S",
                    purpose="review", roster=roster,
                )
                self.assertEqual(code, 0, slot)
                self.assertEqual(slot["phase"], "continuation")
                self.assertEqual(slot["continuation_kind"], "review")
                result = self.policy(self.event(
                    slot["slot_id"], task="S", agent=roster["reviewer"],
                    title="review-S", purpose="review",
                    generation=slot.get("generation"),
                ))
                self.assertEqual(result["result"], "ALLOW", result)
                self.c.run("slot", "release", "--slot", str(slot["slot_id"]),
                           "--holder", f"{roster['crew']}/{roster['reviewer']}")

            with self.subTest(qa=roster["qa"]):
                code, body = self.acquire(
                    task="S", agent=roster["qa"], title="test-S",
                    purpose="implement", roster=roster,
                )
                self.assertEqual(code, 3, body)
                self.assertIn("QA continuation target must belong to the "
                              "task crew", body["reason"])

    def test_crew_forged_via_three_separate_calls_denies_continuation(self):
        """security-530 round 2 MERGE_BLOCKER (PR #530): round 1 only
        blocked the literal same-call PoC (`--crew X --seed-shared-admission`
        in one `task upsert`). Splitting the identical forgery into 3
        separate, individually-legitimate calls -- (1) `--crew security`
        alone, (2) `--seed-shared-admission` alone (seeds admission.crew=
        security), (3) `--crew crew_alpha` alone (flips the task back) --
        reproduces the same forbidden end-state (task.crew=crew_alpha,
        admission.crew=security) with no error anywhere. Both the root
        policy gate and `_derive_normal_phase`'s own slot-acquire-time check
        must refuse reviewer-continuation dispatch off a stale/forged
        admission whose crew no longer matches the task's CURRENT crew --
        not just `admission is not None`."""
        self.shared_task("victim2", crew="security", state="building",
                          pr="https://github.com/o/r/pull/999")
        code, seeded = self.seed("victim2")
        self.assertEqual(code, 0, seeded)
        self.assertEqual(
            rows(self.c.db, "task_admissions", "task_id='victim2'")[0]["crew"],
            "security",
        )
        code, body = self.c.run(
            "task", "upsert", "--id", "victim2", "--crew", "crew_alpha",
        )
        self.assertEqual(code, 0, body)
        self.assertEqual(
            rows(self.c.db, "tasks", "id='victim2'")[0]["crew"], "crew_alpha",
        )
        # Forged end-state confirmed: exactly what round 1 was meant to
        # prevent, just built via 3 calls instead of 1.
        self.assertEqual(
            rows(self.c.db, "task_admissions", "task_id='victim2'")[0]["crew"],
            "security",
        )

        code, body = self.acquire(
            task="victim2", agent=ALPHA["reviewer"], title="review-victim2",
            purpose="review", roster=ALPHA,
        )
        self.assertEqual(code, 3, body)
        self.assertIn("does not match", body["reason"])
        self.assertEqual(
            rows(self.c.db, "worker_slots",
                 "task_id='victim2' AND phase='continuation'"),
            [],
        )

        # Even if a slot were somehow issued (e.g. a future regression that
        # only fixes _derive_normal_phase's own check), the ROOT gate must
        # independently refuse it too -- exercise worker_semaphore directly
        # with a hand-forged slot row to prove that layer alone still holds.
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cur = cx.execute(
                "INSERT INTO worker_slots("
                "holder,crew,task_id,kind,state,acquired_at,expires_at,"
                "phase,expected_agent,expected_title,expected_purpose,"
                "continuation_kind,generation,run_id) "
                "VALUES(?,?,?,'normal','active',0,999999999,'continuation',"
                "?,?,?, 'review',1,?)",
                ("crew_alpha/reviewer_codex", "crew_alpha", "victim2",
                 "reviewer_codex", "review-victim2", "review", "run-212"),
            )
            forged_slot_id = cur.lastrowid
        result = self.policy(self.event(
            forged_slot_id, task="victim2", agent="reviewer_codex",
            title="review-victim2", purpose="review", generation=1,
        ))
        self.assertEqual(result["result"], "DENY", result)


class RootDispatchPolicy(AdmissionFixture, unittest.TestCase):
    def test_named_alpha_and_beta_dispatches_are_exactly_one_use(self):
        for roster, task in ((ALPHA, "A"), (BETA, "B")):
            with self.subTest(crew=roster["crew"]):
                title = f"ship-{task}"
                self.task(task, roster=roster, title=title)
                code, granted = self.acquire(
                    task=task, roster=roster, title=title,
                )
                self.assertEqual(code, 0, granted)
                event = self.event(
                    granted["slot_id"], task=task, roster=roster, title=title,
                    generation=granted.get("generation"),
                )
                self.assertEqual(self.policy(event)["result"], "ALLOW")
                self.assertEqual(self.policy(event)["result"], "DENY")

    def test_opaque_create_unknown_and_malformed_dispatches_deny(self):
        for event in (
            send(session_id="opaque", args={"slot_id": 1}),
            create(agent_id="eng_claude", args={"slot_id": 1}),
            create(config_path="/tmp/agent.yaml", args={"slot_id": 1}),
            send("unknown_worker", "x", {"slot_id": 1, "task_id": "T",
                                           "purpose": "implement"}),
            send("eng_claude", "x", {"slot_id": True, "task_id": "T",
                                       "purpose": "implement"}),
            send("eng_claude", "x", {"slot_id": 1.5, "task_id": "T",
                                       "purpose": "implement"}),
            send("eng_claude", "x", {"slot_id": 0, "task_id": "T",
                                       "purpose": "implement"}),
        ):
            with self.subTest(event=event):
                self.assertEqual(self.policy(event)["result"], "DENY")

    def test_stale_generation_and_expired_slot_deny_without_consumption(self):
        """ADR-0062 (#471) removed the caller-supplied-generation half of
        this test's original scenario: ``worker_semaphore`` no longer reads
        ``args.generation`` at all (the real ``sys_session_send`` schema
        cannot carry it), so a caller passing a "stale" generation has no
        expressible effect any more -- the real, current generation is
        derived from the matching control-plane row regardless of what the
        event's ``args`` dict happens to contain. What is still meaningful,
        and still covered here: an expired-but-not-yet-reaped slot must
        still deny cleanly and leave no admission trace.
        """
        self.task()
        code, slot = self.acquire()
        self.assertEqual(code, 0, slot)

        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE worker_slots SET expires_at=0 WHERE id=?",
                (slot["slot_id"],),
            )
        self.assertEqual(self.policy(self.event(
            slot["slot_id"], generation=slot["generation"],
        ))["result"], "DENY")
        self.assertEqual(rows(self.c.db, "task_admissions"), [])

    def test_exempt_and_oncall_are_static_not_caller_selected(self):
        for agent in ("product", "architect", "crew_alpha", "crew_beta", "pm",
                      "beta_pm", "ux", "staff", "platform", "security", "release"):
            with self.subTest(agent=agent):
                self.assertEqual(self.policy(send(agent, "x", {}))["result"],
                                 "ALLOW")

        # ADR-0044 Decision §3b (task 174-implement-fence): oncall slot
        # acquisition now additionally requires a live, current-generation
        # Program authority token. This fixture's subject is worker_semaphore
        # (self.policy), not the authority gate, so it satisfies that
        # precondition directly rather than widening its own scope.
        code, granted = self.c.run("authority", "acquire", "--holder-label", "p")
        self.assertEqual(code, 0, granted)
        code, oncall = self.c.run(
            "slot", "acquire", "--holder", "oncall", "--agent", "oncall",
            "--title", "incident", "--purpose", "implement", "--oncall",
            "--authority-generation", str(granted["generation"]),
            "--authority-token-stdin", stdin=granted["token"],
        )
        self.assertEqual(code, 0, oncall)
        event = send("oncall", "incident", {
            "slot_id": oncall["slot_id"], "purpose": "implement",
        })
        self.assertEqual(self.policy(send("oncall", "wrong-title", {
            "slot_id": oncall["slot_id"], "purpose": "implement",
        }))["result"], "DENY")
        self.assertEqual(self.policy(send("oncall", "incident", {
            "slot_id": oncall["slot_id"], "purpose": "review",
        }))["result"], "DENY")
        self.assertEqual(self.policy(event)["result"], "ALLOW")
        self.assertEqual(self.policy(event)["result"], "DENY")

        code, _ = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--agent", "eng_claude", "--title", "not-an-incident",
            "--purpose", "implement", "--oncall",
        )
        self.assertEqual(code, 3)


class AdmissionDeploymentContract(unittest.TestCase):
    def assert_classified_dispatch_contract(self, text, label):
        compact = " ".join(text.split())
        self.assertIn("slot acquire", compact, label)
        for flag in ("--holder", "--crew", "--task", "--agent", "--title",
                     "--purpose"):
            self.assertIn(flag, compact, f"{label} missing acquire {flag}")
        for field in ("task_id", "purpose", "slot_id", "generation"):
            self.assertIn(field, compact, f"{label} missing send args.{field}")

    def test_root_is_single_policy_authority_and_managed_preflight_proves_it(self):
        root = Path(REPO, "program", "config.yaml").read_text()
        alpha = Path(
            REPO, "program", "agents", "crew_alpha", "config.yaml"
        ).read_text()
        beta = Path(
            REPO, "program", "agents", "crew_beta", "config.yaml"
        ).read_text()
        preflight = Path(REPO, "deploy", "preflight.sh").read_text()

        self.assertIn("worker_semaphore:", root)
        self.assertNotIn("worker_semaphore:", alpha)
        self.assertNotIn("worker_semaphore:", beta)
        # The semaphore switch is caught by the generic *_FAIL_OPEN scan;
        # test_preflight.FailOpenForbidden drives it with the semaphore name.
        self.assertIn("*_FAIL_OPEN) printf", preflight)
        self.assertIn("crew_alpha", preflight)
        self.assertIn("crew_beta", preflight)
        self.assertIn("worker_semaphore", preflight)

    def test_managed_preflight_executes_both_root_probes_and_requires_policy(self):
        preflight = Path(REPO, "deploy", "preflight.sh").read_text()
        start = preflight.index(
            "if grep -q '^[[:space:]]*worker_semaphore:'"
        )
        end = preflight.index("\ngrep -q '^PYTHONPATH='", start)
        executable_gate = preflight[start:end]
        script = """\
TEAMS_DIR=$1
OMNIPY_CP=$2
PROJECT_DIR=$3
ok() { return 0; }
bad() { return 1; }
""" + executable_gate

        control = Ctl()
        project = str(Path(control.db).parents[1])
        loaded = subprocess.run(
            ["bash", "-s", "--", REPO, sys.executable, project],
            input=script, text=True, capture_output=True, check=False,
        )
        self.assertEqual(loaded.returncode, 0, loaded.stderr)

        with tempfile.TemporaryDirectory(
            prefix="nightshift-missing-root-policy-"
        ) as root:
            root = Path(root)
            (root / "program" / "agents" / "crew_alpha").mkdir(parents=True)
            (root / "program" / "agents" / "crew_beta").mkdir(parents=True)
            (root / "program" / "config.yaml").write_text("policies: {}\n")
            (root / "program" / "agents" / "crew_alpha" / "config.yaml").write_text(
                "policies: {}\n"
            )
            (root / "program" / "agents" / "crew_beta" / "config.yaml").write_text(
                "policies: {}\n"
            )
            missing = subprocess.run(
                ["bash", "-s", "--", str(root), sys.executable, project],
                input=script, text=True, capture_output=True, check=False,
            )
        self.assertNotEqual(missing.returncode, 0, missing.stdout)

    def test_managed_preflight_detects_every_shell_assignment_shape_of_fail_open(self):
        preflight = Path(REPO, "deploy", "preflight.sh").read_text()
        marker = 'python3 - "${fail_open_sources[@]}" <<\'PY\''
        marker_start = preflight.index(marker)
        start = preflight.index("\n", marker_start) + 1
        end = preflight.index("\nPY\n", start)
        detector = preflight[start:end]
        cases = (
            "NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1\n",
            "export NIGHTSHIFT_SEMAPHORE_FAIL_OPEN='1'\n",
            '  NIGHTSHIFT_SEMAPHORE_FAIL_OPEN = "1"  # managed escape\n',
            "NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1\n",
            'Environment="NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1"\n',
        )
        with tempfile.TemporaryDirectory(prefix="nightshift-fail-open-") as root:
            path = Path(root, "server.env")
            for content in cases:
                with self.subTest(content=content):
                    path.write_text(content)
                    found = subprocess.run(
                        [sys.executable, "-", str(path)], input=detector,
                        text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(found.returncode, 0, found.stderr)
            path.write_text("NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=0\n")
            absent = subprocess.run(
                [sys.executable, "-", str(path)], input=detector,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(absent.returncode, 1, absent.stderr)

    def test_alpha_beta_workflow_does_not_write_building_before_new(self):
        for crew in ("crew_alpha", "crew_beta"):
            with self.subTest(crew=crew):
                config = Path(REPO, "program", "agents", crew,
                              "config.yaml").read_text()
                fanout = Path(
                    REPO, "program", "agents", crew, "skills", "fanout", "SKILL.md"
                ).read_text()
                design = Path(
                    REPO, "program", "agents", crew, "skills",
                    "design-debate", "SKILL.md",
                ).read_text()
                before_dispatch = fanout.split("sys_session_send", 1)[0]
                self.assertNotIn("--state building", before_dispatch)
                self.assertIn("--purpose implement", before_dispatch)
                self.assertNotIn("--state building", " ".join(config.split()))
                self.assert_classified_dispatch_contract(
                    config, f"{crew} authoritative config",
                )
                self.assert_classified_dispatch_contract(
                    fanout, f"{crew} fanout",
                )
                self.assert_classified_dispatch_contract(
                    design, f"{crew} design debate",
                )
                qa = "qa" if crew == "crew_alpha" else "beta_qa"
                engineers = (
                    ("eng_claude", "eng_codex") if crew == "crew_alpha"
                    else ("beta_eng_claude", "beta_eng_codex")
                )
                self.assertIn(qa, design)
                for engineer in engineers:
                    self.assertIn(engineer, design)

    def test_alpha_beta_repair_workflows_distinguish_continuation_from_new(self):
        for crew in ("crew_alpha", "crew_beta"):
            with self.subTest(crew=crew):
                root = Path(REPO, "program", "agents", crew)
                config = " ".join((root / "config.yaml").read_text().split())
                fanout = " ".join(
                    (root / "skills" / "fanout" / "SKILL.md").read_text().split()
                )
                review = " ".join(
                    (root / "skills" / "cross-review" / "SKILL.md")
                    .read_text().split()
                )

                self.assertIn(
                    "findings and failures on a mutable branch CLAIM FIRST",
                    config,
                )
                self.assertIn("fresh classified continuation slot", config)
                self.assertIn(
                    "ADR-0022 broker-frozen/post-publication repair creates a "
                    "superseding task/worktree/branch and passes NEW instead",
                    config,
                )
                self.assertIn("Every mutable in-place fix CLAIMS FIRST", fanout)
                self.assertIn("fresh implementer-continuation slot", fanout)
                self.assertIn(
                    "ADR-0022 broker-frozen or post-publication Codex branch, "
                    "create a superseding task/worktree/branch and use NEW instead",
                    fanout,
                )
                self.assertIn("For a mutable branch, acquire an exact task claim", review)
                self.assertIn("fresh implementer-continuation slot", review)
                self.assertIn(
                    "ADR-0022 broker-frozen or post-publication Codex branch, "
                    "create a superseding task/worktree/branch and pass NEW instead",
                    review,
                )

    def test_oncall_workflow_uses_the_exact_reserved_tuple(self):
        program = " ".join(
            Path(REPO, "program", "config.yaml").read_text().split()
        )
        self.assertIn("slot acquire", program)
        for token in ("--holder oncall", "--oncall", "--agent oncall",
                      "--title", "--purpose"):
            self.assertIn(token, program)
        oncall_section = program[program.find("--holder oncall"):]
        self.assertIn("slot_id", oncall_section)
        self.assertIn("purpose", oncall_section)


class AdmissionSymlinkInvocation(unittest.TestCase):
    def test_controller_source_stdin_is_self_contained_for_publication_guards(self):
        """The publisher executes only the attested controller FD with ``-I``.

        Plane-free guard startup must not depend on an unauthenticated sibling
        module that the source-FD transport neither opens nor attests.
        """
        source = Path(REPO, "bin", "nightshift-ctl").read_text()
        with tempfile.TemporaryDirectory(
            prefix="nightshift-controller-stdin-"
        ) as workspace:
            proc = subprocess.run(
                [sys.executable, "-I", "-", "pr-lint", "--kind", "free"],
                cwd=workspace, input=source, text=True,
                capture_output=True, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"ok": True})

    def test_bootstrap_style_ctl_symlink_resolves_reviewed_policy_library(self):
        """bootstrap installs a symlink; helper lookup must follow its target."""
        with tempfile.TemporaryDirectory(prefix="nightshift-212-symlink-") as root:
            root = Path(root)
            ctl = root / "nightshift-ctl"
            ctl.symlink_to(Path(REPO, "bin", "nightshift-ctl"))
            omni = root / ".omni"
            omni.mkdir()
            db = omni / "control.db"
            (omni / "capacity.json").write_text(json.dumps({
                "max_concurrent_workers": 4,
                "reserve_for_oncall": 1,
            }))

            commands = (
                ("init",),
                ("run", "current", "--set", "symlink-run"),
                ("task", "upsert", "--id", "T", "--crew", "crew_alpha",
                 "--agent", "eng_claude", "--title", "ship-T", "--state",
                 "pending", "--size", "S"),
                ("slot", "acquire", "--holder", "crew_alpha/eng_claude",
                 "--crew", "crew_alpha", "--task", "T", "--agent",
                 "eng_claude", "--title", "ship-T", "--purpose", "implement"),
            )
            last = None
            for argv in commands:
                last = subprocess.run(
                    [str(ctl), "--db", str(db), *argv],
                    text=True, capture_output=True,
                )
                self.assertEqual(
                    last.returncode, 0,
                    f"{argv} through {ctl} failed:\nstdout={last.stdout}\n"
                    f"stderr={last.stderr}",
                )
            body = json.loads(last.stdout.strip().splitlines()[-1])
            self.assertEqual(body["phase"], "new")


class AdmissionPolicyControllerAuthority(unittest.TestCase):
    @staticmethod
    def _copy_policy_release(root, *, controller=True):
        release = Path(root, "release")
        policy = release / "deploy" / "policies" / "omnipods_policies.py"
        policy.parent.mkdir(parents=True)
        policy.write_bytes(Path(
            REPO, "deploy", "policies", "omnipods_policies.py"
        ).read_bytes())
        controller_path = release / "bin" / "nightshift-ctl"
        controller_path.parent.mkdir()
        if controller:
            controller_path.write_bytes(Path(
                REPO, "bin", "nightshift-ctl"
            ).read_bytes())
        return policy, controller_path

    @staticmethod
    def _import_policy(policy, *, cwd):
        loader = """
import importlib.util, json, pathlib, sys
policy = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("isolated_policy", policy)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({
    "controller": module.ADMISSION_CONTROLLER_PATH,
    "digest": module.ADMISSION_CONTROLLER_SHA256,
    "module_name": module._ADMISSION.__name__,
}))
"""
        return subprocess.run(
            [sys.executable, "-I", "-c", loader, str(policy)],
            cwd=cwd, text=True, capture_output=True, check=False,
        )

    def test_controller_missing_symlink_or_nonregular_refuses_policy_import(self):
        for shape in ("missing", "external-symlink", "directory"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory(
                prefix=f"nightshift-controller-{shape}-"
            ) as root:
                policy, controller = self._copy_policy_release(
                    root, controller=False,
                )
                sentinel = Path(root, "external-controller-ran")
                if shape == "external-symlink":
                    external = Path(root, "external-controller.py")
                    external.write_text(
                        f"open({str(sentinel)!r}, 'w').write('ran')\n"
                    )
                    controller.symlink_to(external)
                elif shape == "directory":
                    controller.mkdir()

                proc = self._import_policy(policy, cwd=root)

                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("ImportError", proc.stderr)
                self.assertFalse(sentinel.exists())

    def test_controller_path_replacement_executes_held_reviewed_bytes_only(self):
        with tempfile.TemporaryDirectory(
            prefix="nightshift-controller-race-"
        ) as root:
            policy, controller = self._copy_policy_release(root)
            reviewed = controller.read_bytes()
            expected_digest = hashlib.sha256(reviewed).hexdigest()
            sentinel = Path(root, "replacement-ran")
            replacement = Path(root, "replacement.py")
            replacement.write_text(
                f"open({str(sentinel)!r}, 'w').write('ran')\n"
                "COUNTED_AGENTS = {}\n"
                "EXEMPT_AGENTS = frozenset()\n"
                "ONCALL_AGENTS = frozenset()\n"
                "def _task_readiness_snapshot(_cx): return {}\n"
            )
            loader = """
import importlib.util, json, os, pathlib, sys
policy = pathlib.Path(sys.argv[1])
controller = pathlib.Path(sys.argv[2])
replacement = pathlib.Path(sys.argv[3])
real_read = os.read
swapped = False
def racing_read(fd, size):
    global swapped
    chunk = real_read(fd, size)
    if chunk and not swapped:
        swapped = True
        os.replace(replacement, controller)
    return chunk
os.read = racing_read
spec = importlib.util.spec_from_file_location("raced_policy", policy)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
event = {"type": "tool_call", "data": {
    "name": "sys_os_shell",
    "arguments": {"command": "nightshift-release-provenance-canary"},
}}
decision = module.release_provenance_canary()(event)
print(json.dumps({
    "controller": module.ADMISSION_CONTROLLER_PATH,
    "digest": module.ADMISSION_CONTROLLER_SHA256,
    "module_name": module._ADMISSION.__name__,
    "provenance": json.loads(decision["reason"]),
}))
"""
            proc = subprocess.run(
                [sys.executable, "-I", "-c", loader, str(policy),
                 str(controller), str(replacement)],
                cwd=root, text=True, capture_output=True, check=False,
            )

            if proc.returncode == 0:
                proof = json.loads(proc.stdout)
                self.assertEqual(proof["controller"], str(controller))
                self.assertEqual(proof["digest"], expected_digest)
                self.assertNotEqual(proof["module_name"], "__main__")
                self.assertEqual(
                    proof["provenance"]["admission_controller"],
                    str(controller),
                )
                self.assertEqual(
                    proof["provenance"]["admission_controller_sha256"],
                    expected_digest,
                )
            else:
                self.assertIn("ImportError", proc.stderr)
                self.assertIn("changed while being read", proc.stderr)
            self.assertFalse(sentinel.exists())

    def test_canary_reports_executed_controller_path_digest_without_calling_main(self):
        event = {
            "type": "tool_call",
            "data": {
                "name": "sys_os_shell",
                "arguments": {
                    "command": "nightshift-release-provenance-canary",
                },
            },
        }
        decision = P.release_provenance_canary()(event)
        provenance = json.loads(decision["reason"])
        controller = Path(REPO, "bin", "nightshift-ctl").resolve()

        self.assertEqual(
            Path(provenance["admission_controller"]).resolve(), controller,
        )
        self.assertEqual(
            provenance["admission_controller_sha256"],
            hashlib.sha256(controller.read_bytes()).hexdigest(),
        )
        self.assertNotEqual(P._ADMISSION.__name__, "__main__")

    def test_policy_symlink_cannot_redirect_canonical_readiness_authority(self):
        """A policy path alias must still load the reviewed release controller.

        Omnigent imports policies from a configured directory. Neither a
        neighboring Python module nor a controller symlink in that apparent
        directory may replace the readiness/roster implementation.
        """
        real_policy = Path(
            REPO, "deploy", "policies", "omnipods_policies.py"
        ).resolve()
        real_controller = Path(REPO, "bin", "nightshift-ctl").resolve()
        with tempfile.TemporaryDirectory(
            prefix="nightshift-policy-shadow-"
        ) as root:
            root = Path(root)
            policy_dir = root / "deploy" / "policies"
            policy_dir.mkdir(parents=True)
            policy_link = policy_dir / "omnipods_policies.py"
            policy_link.symlink_to(real_policy)
            sentinel = root / "shadow-loaded"
            (policy_dir / "nightshift_admission.py").write_text(
                f"open({str(sentinel)!r}, 'w').write('module')\n"
            )
            malicious_controller = root / "malicious-controller.py"
            malicious_controller.write_text(
                f"open({str(sentinel)!r}, 'w').write('controller')\n"
                "COUNTED_AGENTS = {}\n"
                "EXEMPT_AGENTS = frozenset()\n"
                "ONCALL_AGENTS = frozenset()\n"
                "def _task_readiness_snapshot(_cx): return {}\n"
            )
            shadow_bin = root / "bin"
            shadow_bin.mkdir()
            (shadow_bin / "nightshift-ctl").symlink_to(malicious_controller)

            loader = """
import importlib.util, json, pathlib, sys
policy = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(policy.parent))
spec = importlib.util.spec_from_file_location("shadow_policy", policy)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({
    "controller": module.task_readiness_snapshot.__code__.co_filename,
    "same_evaluator": (
        module.task_readiness_snapshot
        is module._ADMISSION._task_readiness_snapshot
    ),
    "same_roster": module.COUNTED_AGENTS is module._ADMISSION.COUNTED_AGENTS,
}))
"""
            proc = subprocess.run(
                [sys.executable, "-c", loader, str(policy_link)],
                cwd=root,
                env=dict(os.environ, PYTHONPATH=str(policy_dir)),
                text=True, capture_output=True, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            proof = json.loads(proc.stdout)
            self.assertEqual(
                Path(proof["controller"]).resolve(), real_controller,
            )
            self.assertTrue(proof["same_evaluator"])
            self.assertTrue(proof["same_roster"])
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
