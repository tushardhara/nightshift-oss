"""Issue #236/#471, ADR-0062: derive worker_semaphore dispatch admission from
control-plane state, not caller-supplied slot_id/task_id/generation.

``sys_session_send``'s own declared schema (``additionalProperties: false``
on ``args``) permits only ``input``, ``purpose``, ``model``, ``cost_budget``,
``file_ids`` -- there is no call shape that can also carry
``args.slot_id``/``args.task_id``/``args.generation``, so ADR-0062 replaces
the caller-asserted lookup with one derived, inside the existing
``BEGIN IMMEDIATE`` transaction, from ``(kind, expected_agent,
expected_title, expected_purpose)`` -- the exact tuple the tool schema DOES
mandate. This file exercises the frozen review contract at
``docs/reviews/471-CONTRACT.md`` acceptance criterion by acceptance
criterion (AC-1 through AC-9). It is additional, standalone coverage: the
pre-existing ``test_policy.py``/``test_admission_gate.py`` suites (updated
in this same PR where ADR-0062 legitimately invalidated their old
caller-supplied-slot assertions) still cover the rest of ADR-0033's
unchanged downstream behaviour.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys
import unittest

from ctlutil import REPO, Ctl, run_parallel

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
import omnipods_policies as P  # noqa: E402


def send(agent, title, args):
    return {
        "type": "tool_call",
        "data": {"name": "sys_session_send",
                 "arguments": {"agent": agent, "title": title, "args": args}},
    }


class RT16DispatchRequiresLiveBoundUnconsumedSlot(unittest.TestCase):
    """Fixture-free denial nodes bound to ADR-0069's ancestor capture.

    Keep these tests free of ``Ctl`` and database setup: their byte-identical
    copies must run against the pre-CLI-rename ancestor selected by ADR-0069.
    The baseline also binds the fixture-backed positive control in the separate
    class below; that node uses the current hermetic CLI to construct state and
    swaps only the policy evaluator when reproducing the ancestor failure.
    """

    def setUp(self):
        self.policy = P.worker_semaphore(db="/nonexistent/rt16/control.db")

    def test_session_create_is_not_an_authorized_dispatch_form(self):
        event = {
            "type": "tool_call",
            "data": {"name": "sys_session_create", "arguments": {
                "agent": "eng_claude", "title": "rt16-worker",
                "args": {"input": "implement RT-16", "purpose": "implement"},
            }},
        }
        result = self.policy(event)
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("not an authorized worker dispatch form", result["reason"])

    def test_opaque_session_id_cannot_prove_the_target(self):
        event = {
            "type": "tool_call",
            "data": {"name": "sys_session_send", "arguments": {
                "session_id": "opaque-session",
                "args": {"input": "implement RT-16"},
            }},
        }
        result = self.policy(event)
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("opaque session_id", result["reason"])


# ---------------------------------------------------------------------------
# M-1 (architect, round-1 MERGE_BLOCKER; round-2: schema provenance fixed,
# remainder downgraded MERGE_BLOCKER -> PRE_DEPLOYMENT by the program --
# see `.omni/decisions.md`, 2026-09-01 "round 2 recheck"). AC-6's original
# tests called the policy function directly against a hand-fabricated event
# and never crossed the real dispatch boundary -- never validated against
# the real tool's own schema, never included ``args.input``. That is the
# exact integration boundary #236 was about, so evaluator-level tests alone
# can't prove the contract's claim.
#
# ``sys_session_send`` is a native tool of the external, pinned `omnigent`
# engine (commit-pinned in `deploy/omnigent-runtime.lock.json`, not
# vendored into this repo -- ADR-0062 Context), and this suite is
# stdlib-only by design (`tests/run.sh`'s own header comment: "no pip
# install step exists on purpose"), so there is no way to invoke the live
# tool's real client-side validator from here without a network dependency
# this CI intentionally refuses. Round 1's schema was hand-transcribed from
# ADR-0062's prose and a live repro's error text -- accurate, but "hearsay",
# not sourced from an authoritative schema definition (architect's round-2
# finding). ``SYS_SESSION_SEND_TOOL_SCHEMA`` below is instead copied
# VERBATIM from a live ``ToolSearch(select:sys_session_send)`` call made by
# the program THIS turn, covering issues #236/#471, 2026-09-01 -- first-party,
# dated, authoritative provenance, not memory or prior docs. It corrects one
# real fact round 1's version got wrong: the object-branch of ``args`` has
# ``"required": ["input"]`` -- ``input`` is NOT optional, and round 1's
# validator didn't check for it.
#
# Every AC-6 test below validates its exact payload against this schema
# BEFORE feeding it to the policy, and includes ``args.input`` -- so a
# passing test proves both "the real tool would accept this exact call" and
# "the policy then admits it correctly", not just the latter in isolation.
# What remains structurally unclosable pre-merge -- calling `self.policy()`
# directly still can't cross into the external engine's actual client/hook
# chain, which only exists once this PR is deployed -- is PRE_DEPLOYMENT,
# not fixed here: closed by a real post-merge counted-agent dispatch probe
# through the live policy (program will dispatch this once #473 merges;
# #471/#236 do not close until that probe succeeds).
SYS_SESSION_SEND_TOOL_SCHEMA = {
    "additionalProperties": False,
    "properties": {
        "agent": {"type": "string"},
        "args": {
            "anyOf": [
                {"type": "string"},
                {
                    "additionalProperties": False,
                    "properties": {
                        "cost_budget": {
                            "properties": {
                                "ask_thresholds_usd": {
                                    "items": {"type": "number"},
                                    "type": "array",
                                },
                                "max_cost_usd": {"type": "number"},
                            },
                            "type": "object",
                        },
                        "file_ids": {
                            "items": {"minLength": 1, "type": "string"},
                            "minItems": 1,
                            "type": "array",
                        },
                        "input": {"type": "string"},
                        "model": {"type": "string"},
                        "purpose": {"type": "string"},
                    },
                    "required": ["input"],
                },
            ],
        },
        "session_id": {"type": "string"},
        "title": {"type": "string"},
    },
    "required": ["args"],
}
# The object-branch of args, and its allowed-keys/required-keys, extracted
# once so the validators below don't re-walk the schema literal on every
# call.
_ARGS_OBJECT_BRANCH = SYS_SESSION_SEND_TOOL_SCHEMA["properties"]["args"]["anyOf"][1]
SYS_SESSION_SEND_ARGS_SCHEMA = frozenset(_ARGS_OBJECT_BRANCH["properties"])
SYS_SESSION_SEND_ARGS_REQUIRED = frozenset(_ARGS_OBJECT_BRANCH["required"])


def assert_schema_legal_send(case, event):
    """Assert ``event`` (this file's ``send()`` shape) is a call the real,
    pinned ``sys_session_send`` tool would accept: non-empty string
    ``agent``/``title``, and an ``args`` object containing every key the
    schema REQUIRES (``input``) and ONLY keys the schema's
    ``additionalProperties: false`` object-branch allows."""
    arguments = event["data"]["arguments"]
    agent, title = arguments["agent"], arguments["title"]
    args = arguments["args"]
    case.assertIsInstance(agent, str)
    case.assertTrue(agent)
    case.assertIsInstance(title, str)
    case.assertTrue(title)
    case.assertIsInstance(args, dict)
    illegal = set(args) - SYS_SESSION_SEND_ARGS_SCHEMA
    case.assertEqual(
        illegal, set(),
        f"args key(s) {illegal!r} are not in the real tool's schema -- "
        "the live client rejects this call before it ever reaches the "
        "policy (additionalProperties: false)",
    )
    missing = SYS_SESSION_SEND_ARGS_REQUIRED - set(args)
    case.assertEqual(
        missing, set(),
        f"args is missing required key(s) {missing!r} -- the live client "
        "rejects this call before it ever reaches the policy "
        "(object-branch `required`)",
    )


def assert_schema_illegal_send(case, event):
    """The inverse: assert ``event`` is exactly a shape the real client
    REJECTS, client-side, before any dispatch or cost -- either an
    unrecognised key (the literal #236 root cause: pre-ADR-0062
    ``worker_semaphore`` required caller-supplied fields the schema has
    never actually allowed) or a missing required ``input``."""
    args = event["data"]["arguments"]["args"]
    illegal = set(args) - SYS_SESSION_SEND_ARGS_SCHEMA
    missing = SYS_SESSION_SEND_ARGS_REQUIRED - set(args)
    case.assertTrue(
        illegal or missing,
        "expected this payload to be schema-illegal (simulating the real "
        "client's rejection), but every key it used is schema-legal and "
        "every required key is present",
    )


def rows(db, table, where="", params=()):
    with contextlib.closing(sqlite3.connect(db)) as cx:
        cx.row_factory = sqlite3.Row
        query = f"SELECT * FROM {table}"
        if where:
            query += " WHERE " + where
        query += " ORDER BY rowid"
        return [dict(row) for row in cx.execute(query, params)]


def indexes_on(db, table):
    return {
        row["name"]: row["sql"]
        for row in rows(db, "sqlite_master",
                         "type='index' AND tbl_name=?", (table,))
    }


class DerivationFixture(unittest.TestCase):
    """Shared vocabulary: a fresh control plane, one pending task, the real
    ``worker_semaphore`` evaluator against the same database."""

    def setUp(self):
        self.c = Ctl()
        code, body = self.c.run("run", "current", "--set", "run-471")
        self.assertEqual(code, 0, body)
        self.policy = P.worker_semaphore(db=self.c.db)

    def task(self, task_id="T", *, agent="eng_claude", title="ship-T",
             state="pending", pr=None):
        argv = ["task", "upsert", "--id", task_id, "--crew", "crew_alpha",
                "--agent", agent, "--title", title, "--state", state,
                "--size", "S"]
        if pr is not None:
            argv += ["--pr-url", pr]
        code, body = self.c.run(*argv)
        self.assertEqual(code, 0, body)

    def acquire_new(self, task_id="T", *, agent="eng_claude", title="ship-T"):
        code, granted = self.c.run(
            "slot", "acquire", "--holder", f"crew_alpha/{agent}",
            "--crew", "crew_alpha", "--task", task_id, "--agent", agent,
            "--title", title, "--purpose", "implement", "--ttl", "600",
        )
        self.assertEqual(code, 0, granted)
        return granted

    def admit_new(self, task_id="T", *, agent="eng_claude", title="ship-T"):
        self.task(task_id, agent=agent, title=title)
        granted = self.acquire_new(task_id, agent=agent, title=title)
        # M-1: validate against the real tool schema before dispatching --
        # see the module-level comment on SYS_SESSION_SEND_ARGS_SCHEMA.
        event = send(agent, title,
                     {"purpose": "implement", "input": f"implement {task_id}"})
        assert_schema_legal_send(self, event)
        result = self.policy(event)
        self.assertEqual(result["result"], "ALLOW", result)
        return granted


class SlotStateCoverage(DerivationFixture):
    """Additional denial coverage that is not part of RT-16 evidence."""

    def event(self):
        return send("eng_claude", "ship-T",
                    {"input": "implement T", "purpose": "implement"})

    def test_already_consumed_slot_denies(self):
        self.task()
        granted = self.acquire_new()
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("UPDATE worker_slots SET dispatched_at=1 WHERE id=?",
                       (granted["slot_id"],))
        result = self.policy(self.event())
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("no active undispatched slot matches", result["reason"])

    def test_ttl_expired_slot_denies(self):
        self.task()
        granted = self.acquire_new()
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("UPDATE worker_slots SET expires_at=1 WHERE id=?",
                       (granted["slot_id"],))
        result = self.policy(self.event())
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("no active undispatched slot matches", result["reason"])

    def test_slot_bound_to_different_agent_denies(self):
        self.task()
        granted = self.acquire_new()
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("UPDATE worker_slots SET holder=? WHERE id=?",
                       ("crew_alpha/eng_codex", granted["slot_id"]))
        result = self.policy(self.event())
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("holder", result["reason"])

    def test_ambiguous_matching_slots_deny(self):
        self.task()
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            # Issue #165/ADR-0084 renamed this index to
            # `one_undispatched_dispatch_identity_per_repository` when it
            # widened it to include `repository_id`; dropping the pre-165
            # name here would now be a silent no-op and leave the live
            # index blocking this fixture's own deliberately-ambiguous
            # INSERTs below.
            cx.execute(
                "DROP INDEX IF EXISTS "
                "one_undispatched_dispatch_identity_per_repository")
            for task_id in ("T", "T-2"):
                cx.execute(
                    "INSERT INTO worker_slots(holder,crew,task_id,kind,"
                    "state,acquired_at,expires_at,phase,expected_agent,"
                    "expected_title,expected_purpose,generation,run_id) "
                    "VALUES('crew_alpha/eng_claude','crew_alpha',?,"
                    "'normal','active',1,9999999999,'new','eng_claude',"
                    "'ship-T','implement',1,'run-471')", (task_id,))
        result = self.policy(self.event())
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("ambiguous dispatch identity", result["reason"])


class RT16SlotStateCoverage(DerivationFixture):
    """RT-16's baseline-bound allow-once positive control, plus the
    consumed-slot exclusion node that carries the F-1 kill on persisted
    state rather than on denial prose (ADR-0074, issue #607)."""

    def event(self):
        return send("eng_claude", "ship-T",
                    {"input": "implement T", "purpose": "implement"})

    def test_live_bound_slot_allows_once_and_records_dispatch(self):
        self.task()
        granted = self.acquire_new()
        first = self.policy(self.event())
        self.assertEqual(first["result"], "ALLOW", first)
        second = self.policy(self.event())
        self.assertEqual(second["result"], "DENY", second)
        self.assertIn("no active undispatched slot matches", second["reason"])
        slot = rows(self.c.db, "worker_slots", "id=?",
                    (granted["slot_id"],))[0]
        self.assertIsNotNone(slot["dispatched_at"])
        self.assertEqual(slot["bound_agent"], "eng_claude")
        self.assertEqual(slot["bound_title"], "ship-T")

    def test_consumed_slot_is_excluded_from_derivation_so_a_reused_identity_still_dispatches(self):
        """ADR-0074 (#607): the derivation SELECT's `dispatched_at IS NULL`
        predicate, asserted on persisted `worker_slots` columns.

        `one_undispatched_dispatch_identity` is partial on `state='active'
        AND dispatched_at IS NULL`, so once a slot is dispatched its
        (expected_agent, expected_title) is legitimately free again --
        the reuse path `M2CrossTaskDispatchIdentityGuard.
        test_dispatch_clears_the_identity_for_legitimate_reuse` already
        sanctions. Drop `dispatched_at IS NULL` from the derivation query
        and the consumed row becomes a candidate again: the second dispatch
        turns into a two-row `ambiguous dispatch identity` denial, observable
        as task B's slot staying unconsumed. Nothing here reads a reason
        string, so rewording a denial cannot resurrect the mutant.

        Scope, per ADR-0074 decision 2: the kill is on the LIVENESS face.
        F-1 cannot produce a duplicate dispatch -- the one-use compare-and-set
        independently forecloses that -- so this node proves the predicate is
        load-bearing, not that its removal admits an unbudgeted worker.
        """
        self.task("A", agent="eng_claude", title="ship-T")
        code, slot_a = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "A", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "explore", "--ttl", "600")
        self.assertEqual(code, 0, slot_a)
        first = self.policy(send("eng_claude", "ship-T",
                                 {"input": "explore A", "purpose": "explore"}))
        self.assertEqual(first["result"], "ALLOW", first)

        # Task B reuses the SAME derivation tuple. Legal: task A's slot is
        # consumed and has left the partial unique index.
        self.task("B", agent="eng_claude", title="ship-T")
        code, slot_b = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "B", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "explore", "--ttl", "600")
        self.assertEqual(code, 0, slot_b)

        second = self.policy(send("eng_claude", "ship-T",
                                  {"input": "explore B", "purpose": "explore"}))
        self.assertEqual(second["result"], "ALLOW", second)

        b = rows(self.c.db, "worker_slots", "id=?", (slot_b["slot_id"],))[0]
        self.assertIsNotNone(b["dispatched_at"])
        self.assertEqual(b["bound_agent"], "eng_claude")
        self.assertEqual(b["bound_title"], "ship-T")
        a = rows(self.c.db, "worker_slots", "id=?", (slot_a["slot_id"],))[0]
        self.assertIsNotNone(a["dispatched_at"])
        self.assertNotEqual(a["id"], b["id"])


class AC1DerivedNotCallerSupplied(DerivationFixture):
    def test_dispatch_needs_no_caller_supplied_slot_task_or_generation(self):
        self.task()
        self.acquire_new()
        # Exactly the shape the real sys_session_send schema allows: no
        # slot_id/task_id/generation key anywhere in args.
        result = self.policy(send("eng_claude", "ship-T",
                                   {"purpose": "implement"}))
        self.assertEqual(result["result"], "ALLOW", result)
        self.assertEqual(rows(self.c.db, "tasks", "id='T'")[0]["state"],
                          "building")

    def test_caller_supplied_slot_task_generation_are_inert_not_trusted(self):
        """A caller COULD still stuff slot_id/task_id/generation into args
        (a legacy client, a confused LLM re-typing a prior response) -- they
        must have zero effect, positive or negative, on which row is
        admitted. Proves the fields are not read, not merely unused on the
        happy path."""
        self.task()
        self.acquire_new()
        result = self.policy(send("eng_claude", "ship-T", {
            "purpose": "implement",
            "slot_id": 999999999,
            "task_id": "totally-different-nonexistent-task",
            "generation": 999999999,
        }))
        self.assertEqual(result["result"], "ALLOW", result)
        # The REAL task ("T") was admitted, not the caller-asserted one.
        self.assertEqual(rows(self.c.db, "tasks", "id='T'")[0]["state"],
                          "building")
        self.assertEqual(
            rows(self.c.db, "task_admissions", "task_id='T'")[0]["task_id"],
            "T",
        )
        self.assertEqual(
            rows(self.c.db, "task_admissions",
                 "task_id='totally-different-nonexistent-task'"), [],
        )


class AC2ZeroAndMultiMatchDeny(DerivationFixture):
    def test_zero_match_denies(self):
        # No task, no slot at all.
        result = self.policy(send("eng_claude", "nothing-here",
                                   {"purpose": "implement"}))
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("no active undispatched slot matches", result["reason"])

    def test_ambiguous_match_denies_without_consuming_either_row(self):
        """More-than-one-match is provably unreachable through the real CLI
        once the ADR-0062 indexes are live (that is what AC-3/AC-4 assert),
        so reaching it here requires the same legacy-duplicate construction
        AC-4 uses: drop the guarding index, insert two rows that collide on
        the derivation tuple, and confirm worker_semaphore's own
        defense-in-depth still denies cleanly instead of guessing."""
        self.task()
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                # Issue #165/ADR-0084 renamed this index (see the comment
                # on the other DROP INDEX above in this file).
                "DROP INDEX IF EXISTS "
                "one_undispatched_dispatch_identity_per_repository")
            # Different task_id per row -- these collide only on the
            # (expected_agent, expected_title) identity this ADR's second
            # index protects, not on the PRE-EXISTING
            # one_active_new_reservation index (task_id alone, unrelated to
            # ADR-0062), which stays in place for this test on purpose.
            for task_id in ("T", "T-2"):
                cx.execute(
                    "INSERT INTO worker_slots(holder,crew,task_id,kind,"
                    "state,acquired_at,expires_at,phase,expected_agent,"
                    "expected_title,expected_purpose,generation,run_id) "
                    "VALUES('crew_alpha/eng_claude','crew_alpha',?,"
                    "'normal','active',1,9999999999,'new','eng_claude',"
                    "'ship-T','implement',1,'run-471')", (task_id,)
                )
        before = rows(self.c.db, "worker_slots",
                      "expected_agent='eng_claude' AND expected_title='ship-T'")
        self.assertEqual(len(before), 2)
        result = self.policy(send("eng_claude", "ship-T",
                                   {"purpose": "implement"}))
        self.assertEqual(result["result"], "DENY", result)
        self.assertIn("ambiguous dispatch identity", result["reason"])
        after = rows(self.c.db, "worker_slots",
                     "expected_agent='eng_claude' AND expected_title='ship-T'")
        self.assertEqual(after, before)
        self.assertTrue(all(r["dispatched_at"] is None for r in after))


class AC3IndexesShipInTheMigration(unittest.TestCase):
    def test_both_partial_unique_indexes_exist_with_the_specified_shape(self):
        c = Ctl()
        idx = indexes_on(c.db, "worker_slots")
        self.assertIn("one_active_qa_review_continuation", idx)
        # Issue #165/ADR-0084 widened and renamed this index to include
        # `repository_id` -- see ADR-0084 for why (the pre-165 name's own
        # uniqueness rested entirely on (expected_agent, expected_title)
        # never colliding across REPOSITORIES, which nothing enforced).
        self.assertIn("one_undispatched_dispatch_identity_per_repository", idx)

        qa_review_sql = (idx["one_active_qa_review_continuation"] or "").lower()
        qa_review_sql = qa_review_sql.replace('"', "").replace(" ", "")
        self.assertIn("task_id,continuation_kind", qa_review_sql)
        self.assertIn("state='active'", qa_review_sql)
        self.assertIn("phase='continuation'", qa_review_sql)
        self.assertIn("continuation_kindin('qa','review')", qa_review_sql)

        identity_sql = (idx["one_undispatched_dispatch_identity_per_repository"]
                        or "").lower()
        identity_sql = identity_sql.replace('"', "").replace(" ", "")
        self.assertIn("repository_id,expected_agent,expected_title", identity_sql)
        self.assertIn("state='active'", identity_sql)
        self.assertIn("dispatched_atisnull", identity_sql)


class AC4GuardedMigrationNeverCrashes(unittest.TestCase):
    """Staff's blocking finding on ADR-0062: a bare CREATE UNIQUE INDEX
    hitting a pre-existing legacy-duplicate active row would raise
    IntegrityError UNCAUGHT and crash every subsequent nightshift-ctl
    invocation org-wide, not fail closed. Both new indexes must instead
    report and skip -- and self-heal once the conflicting rows clear."""

    def _run(self, *argv):
        p = subprocess.run(self.c.cmdline(*argv), capture_output=True,
                            text=True)
        return p

    def setUp(self):
        self.c = Ctl()

    def test_legacy_duplicate_qa_review_continuation_reports_not_crashes(self):
        code, body = self.c.run(
            "task", "upsert", "--id", "T", "--crew", "crew_alpha",
            "--agent", "qa", "--title", "test-T", "--state", "building",
            "--size", "S", "--pr-url", "https://github.com/o/r/pull/1",
        )
        self.assertEqual(code, 0, body)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "DROP INDEX IF EXISTS one_active_qa_review_continuation")
            # dispatched_at is set (non-NULL) on both rows -- a plausible
            # legacy state (two QA continuation slots that were both
            # actually dispatched before this fix existed) that isolates
            # this test to ONLY the qa_review index: it keeps both rows out
            # of one_undispatched_dispatch_identity's own
            # ``dispatched_at IS NULL`` scope, so that index (still live,
            # untouched by this test) does not also fire here.
            for _ in range(2):
                cx.execute(
                    "INSERT INTO worker_slots(holder,crew,task_id,kind,"
                    "state,acquired_at,expires_at,dispatched_at,phase,"
                    "expected_agent,expected_title,expected_purpose,"
                    "continuation_kind,generation,run_id) VALUES("
                    "'crew_alpha/qa','crew_alpha','T','normal','active',"
                    "1,9999999999,1,'continuation','qa','test-T',"
                    "'implement','qa',1,'run-471')"
                )

        p = self._run("slot", "list")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("WARNING", p.stderr)
        self.assertIn("one_active_qa_review_continuation", p.stderr)
        # F-1 (architect, round 1): assert the EXACT conflicting
        # (task_id, continuation_kind, count) tuple the precheck's own
        # ``GROUP BY task_id, continuation_kind`` query reports, not just
        # that some warning fired -- proves the report identifies the real
        # conflicting rows, not an unrelated or malformed one.
        self.assertIn("('T', 'qa', 2)", p.stderr, p.stderr)
        idx = indexes_on(self.c.db, "worker_slots")
        self.assertNotIn("one_active_qa_review_continuation", idx)

        # Never crashes anything else, either -- an unrelated subsequent
        # command still returns a clean verdict, not a traceback.
        p2 = self._run("slot", "list")
        self.assertEqual(p2.returncode, 0, p2.stderr)

        # Self-heals once the conflict clears.
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            dupes = [r["id"] for r in rows(
                self.c.db, "worker_slots",
                "task_id='T' AND continuation_kind='qa'")]
            cx.execute("UPDATE worker_slots SET state='released' WHERE id=?",
                       (dupes[0],))
        p3 = self._run("slot", "list")
        self.assertEqual(p3.returncode, 0, p3.stderr)
        idx3 = indexes_on(self.c.db, "worker_slots")
        self.assertIn("one_active_qa_review_continuation", idx3)

    def test_legacy_duplicate_dispatch_identity_reports_not_crashes(self):
        code, body = self.c.run(
            "task", "upsert", "--id", "T", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "ship-T", "--state",
            "pending", "--size", "S",
        )
        self.assertEqual(code, 0, body)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                # Issue #165/ADR-0084 renamed this index (see the comment
                # on the other DROP INDEX above in this file).
                "DROP INDEX IF EXISTS "
                "one_undispatched_dispatch_identity_per_repository")
            for task_id in ("T", "T-2"):
                cx.execute(
                    "INSERT INTO worker_slots(holder,crew,task_id,kind,"
                    "state,acquired_at,expires_at,phase,expected_agent,"
                    "expected_title,expected_purpose,generation,run_id) "
                    "VALUES('crew_alpha/eng_claude','crew_alpha',?,"
                    "'normal','active',1,9999999999,'new','eng_claude',"
                    "'ship-T','implement',1,'run-471')", (task_id,)
                )

        p = self._run("slot", "list")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("WARNING", p.stderr)
        # Substring match: "one_undispatched_dispatch_identity_per_repository"
        # (issue #165/ADR-0084's renamed index) still contains this exact
        # string as a prefix, so this assertion is unaffected by the rename.
        self.assertIn("one_undispatched_dispatch_identity", p.stderr)
        # F-1 (architect, round 1): same tightening as the qa/review case
        # above -- assert the exact (repository_id, expected_agent,
        # expected_title, count) tuple the precheck's ``GROUP BY
        # repository_id, expected_agent, expected_title`` query reports.
        # Issue #165/ADR-0084 prepended `repository_id` to that GROUP BY,
        # widening the reported tuple from 3 to 4 elements.
        self.assertIn("(0, 'eng_claude', 'ship-T', 2)", p.stderr, p.stderr)
        idx = indexes_on(self.c.db, "worker_slots")
        self.assertNotIn("one_undispatched_dispatch_identity_per_repository", idx)

        p2 = self._run("slot", "list")
        self.assertEqual(p2.returncode, 0, p2.stderr)


class AC5QaReviewContinuationPreInsertCheck(DerivationFixture):
    def setUp(self):
        super().setUp()
        # Real admission, not just a hand-set task state -- continuation is
        # only reachable once task_admissions actually has a row for T.
        self.admit_new()
        code, body = self.c.run(
            "task", "upsert", "--id", "T",
            "--pr-url", "https://github.com/o/r/pull/1",
        )
        self.assertEqual(code, 0, body)

    def _review_cmdline(self):
        return self.c.cmdline(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", "T", "--agent",
            "reviewer_codex", "--title", "test-T", "--purpose", "review",
            "--ttl", "600",
        )

    def test_sequential_second_review_acquire_for_same_task_denies_cleanly(self):
        code, first = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", "T", "--agent",
            "reviewer_codex", "--title", "test-T", "--purpose", "review",
            "--ttl", "600",
        )
        self.assertEqual(code, 0, first)
        code2, second = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", "T", "--agent",
            "reviewer_codex", "--title", "test-T", "--purpose", "review",
            "--ttl", "600",
        )
        self.assertEqual(code2, 3, second)
        self.assertIn("active QA/review continuation slot", second["reason"])
        active = rows(
            self.c.db, "worker_slots",
            "task_id='T' AND state='active' AND continuation_kind='review'",
        )
        self.assertEqual(len(active), 1)

    def test_concurrent_review_acquires_have_exactly_one_winner(self):
        results = run_parallel([self._review_cmdline() for _ in range(5)])
        codes = sorted(code for code, _ in results)
        self.assertEqual(codes.count(0), 1, results)
        # every loser denies cleanly (exit 3), none crash (no other exit code)
        self.assertEqual(set(codes) - {0}, {3}, results)
        active = rows(
            self.c.db, "worker_slots",
            "task_id='T' AND state='active' AND continuation_kind='review'",
        )
        self.assertEqual(len(active), 1)


class M2CrossTaskDispatchIdentityGuard(DerivationFixture):
    """Security F-SEC-1 (round-1 MERGE_BLOCKER, `cmd_slot_acquire`).

    AC-5's pre-INSERT check only covers the qa/review continuation branch
    of `_derive_normal_phase`. Every OTHER path that reaches
    `cmd_slot_acquire`'s single shared INSERT -- PLANNING, NEW, continuation
    implementer, oncall -- was left unguarded against the
    `one_undispatched_dispatch_identity` partial unique index, so a
    legitimate, unremarkable sequence (nothing prevents two different tasks
    reusing the same title) crashed instead of denying cleanly. Concrete
    repro from the security finding: task A holds an active, undispatched
    PLANNING slot for (eng_claude, T); task B (a different task) then
    requests a NEW slot for that same (eng_claude, T) -- pre-fix, this hit
    the index's `sqlite3.IntegrityError` uncaught, raw traceback, exit 1.
    """

    def test_planning_then_different_task_new_same_identity_denies_cleanly(self):
        self.task("A", agent="eng_claude", title="ship-T")
        code, planning = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "A", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "explore", "--ttl", "600",
        )
        self.assertEqual(code, 0, planning)

        self.task("B", agent="eng_claude", title="ship-T")
        code2, denial = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "B", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "implement", "--ttl", "600",
        )
        # Pre-fix: this crashed (uncaught sqlite3.IntegrityError, exit 1,
        # raw traceback -- not JSON, `self.c.run` would raise trying to
        # parse it). Post-fix: a clean, JSON-shaped deny at exit 3.
        self.assertEqual(code2, 3, denial)
        self.assertIn("another active undispatched slot", denial["reason"])
        self.assertEqual(denial["agent"], "eng_claude")
        self.assertEqual(denial["title"], "ship-T")

        # Task B's own attempt left no trace: no row inserted for it, no
        # partial mutation, task B still pending.
        self.assertEqual(
            rows(self.c.db, "worker_slots", "task_id='B'"), [])
        self.assertEqual(rows(self.c.db, "tasks", "id='B'")[0]["state"],
                          "pending")
        # Task A's own PLANNING slot is completely untouched by B's denied
        # attempt.
        still_active = rows(
            self.c.db, "worker_slots",
            "task_id='A' AND state='active' AND phase='planning'")
        self.assertEqual(len(still_active), 1)
        self.assertEqual(still_active[0]["id"], planning["slot_id"])

        # A subsequent, unrelated command still returns cleanly -- the
        # crash-class failure this guards against would otherwise not be
        # scoped to just this one request.
        code3, unrelated = self.c.run("slot", "list")
        self.assertEqual(code3, 0, unrelated)

    def test_dispatch_clears_the_identity_for_legitimate_reuse(self):
        """Not a regression on the legitimate reuse path: once task A's
        PLANNING slot is actually dispatched (`dispatched_at` set), the
        identity is free again -- this guard only blocks a genuine live
        (state='active', dispatched_at IS NULL) collision, matching the
        index's own scope exactly; it does not turn (agent, title) into a
        permanently exhausted resource."""
        self.task("A", agent="eng_claude", title="ship-T")
        code, planning = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "A", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "explore", "--ttl", "600",
        )
        self.assertEqual(code, 0, planning)
        result = self.policy(send("eng_claude", "ship-T", {"purpose": "explore"}))
        self.assertEqual(result["result"], "ALLOW", result)

        self.task("B", agent="eng_claude", title="ship-T")
        code2, granted = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "B", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "implement", "--ttl", "600",
        )
        self.assertEqual(code2, 0, granted)


class AC6AllThreePathsEndToEnd(DerivationFixture):
    """One derivation mechanism must cover NEW/engineer, continuation
    (qa/reviewer/implementer), and oncall-reserve -- driven with only the
    fields the real sys_session_send schema allows."""

    def test_new_engineer_path(self):
        granted = self.admit_new()
        self.assertTrue(granted["granted"])
        self.assertEqual(rows(self.c.db, "tasks", "id='T'")[0]["state"],
                          "building")

    def test_continuation_qa_path(self):
        self.admit_new()
        self.c.run("task", "upsert", "--id", "T",
                   "--pr-url", "https://github.com/o/r/pull/1")
        code, granted = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew",
            "crew_alpha", "--task", "T", "--agent", "qa", "--title",
            "test-T", "--purpose", "implement", "--ttl", "600",
        )
        self.assertEqual(code, 0, granted)
        event = send("qa", "test-T",
                     {"purpose": "implement", "input": "run the test plan"})
        assert_schema_legal_send(self, event)
        result = self.policy(event)
        self.assertEqual(result["result"], "ALLOW", result)

    def test_continuation_reviewer_path(self):
        self.admit_new()
        self.c.run("task", "upsert", "--id", "T",
                   "--pr-url", "https://github.com/o/r/pull/1")
        code, granted = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", "T", "--agent",
            "reviewer_codex", "--title", "test-T", "--purpose", "review",
            "--ttl", "600",
        )
        self.assertEqual(code, 0, granted)
        event = send("reviewer_codex", "test-T",
                     {"purpose": "review", "input": "review the diff"})
        assert_schema_legal_send(self, event)
        result = self.policy(event)
        self.assertEqual(result["result"], "ALLOW", result)

    def test_continuation_implementer_path(self):
        self.admit_new()
        code, claim = self.c.run(
            "claim", "acquire", "--task", "T", "--claimant",
            "crew_alpha/lead", "--agent", "eng_claude", "--title", "ship-T",
            "--ttl", "600",
        )
        self.assertEqual(code, 0, claim)
        code, granted = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude", "--crew",
            "crew_alpha", "--task", "T", "--agent", "eng_claude", "--title",
            "ship-T", "--purpose", "implement", "--ttl", "600",
            "--claim-id", claim["claim_id"], "--claim-generation",
            claim["generation"], "--claim-run-id", claim["run_id"],
        )
        self.assertEqual(code, 0, granted)
        event = send("eng_claude", "ship-T",
                     {"purpose": "implement", "input": "continue the fix"})
        assert_schema_legal_send(self, event)
        result = self.policy(event)
        self.assertEqual(result["result"], "ALLOW", result)

    def test_oncall_path(self):
        code, granted = self.c.run("authority", "acquire", "--holder-label",
                                    "p")
        self.assertEqual(code, 0, granted)
        code, oncall = self.c.run(
            "slot", "acquire", "--holder", "oncall", "--agent", "oncall",
            "--title", "incident", "--purpose", "implement", "--oncall",
            "--authority-generation", str(granted["generation"]),
            "--authority-token-stdin", stdin=granted["token"],
        )
        self.assertEqual(code, 0, oncall)
        event = send("oncall", "incident",
                     {"purpose": "implement", "input": "handle the incident"})
        assert_schema_legal_send(self, event)
        result = self.policy(event)
        self.assertEqual(result["result"], "ALLOW", result)

    def test_pre_adr0062_call_shape_is_schema_illegal(self):
        """The literal #236 root cause, codified as a regression test: the
        call shape ``worker_semaphore`` required BEFORE ADR-0062
        (``args.slot_id``/``args.task_id``/``args.generation``) was never
        actually acceptable to the real tool, and must never become
        "the fix" again by silently reintroducing caller-supplied identity
        fields into ``args``."""
        pre_fix_event = send("eng_claude", "ship-T", {
            "purpose": "implement",
            "slot_id": 1,
            "task_id": "T",
            "generation": 1,
        })
        assert_schema_illegal_send(self, pre_fix_event)

        # And the ADR-0062 shape this PR actually dispatches with -- same
        # (agent, title, purpose) identity, none of the illegal keys -- IS
        # schema-legal.
        post_fix_event = send("eng_claude", "ship-T",
                              {"purpose": "implement", "input": "x"})
        assert_schema_legal_send(self, post_fix_event)

    def test_payload_missing_required_input_is_schema_illegal(self):
        """Round-2 correction (architect): the authoritative schema's
        object-branch has ``"required": ["input"]`` -- ``input`` is not
        optional. A payload with only schema-KNOWN keys but no ``input`` is
        still illegal, and this is a distinct failure mode from an
        unrecognised key (``additionalProperties: false``): every key used
        here is legal on its own, but the object is incomplete."""
        no_input_event = send("eng_claude", "ship-T", {"purpose": "implement"})
        assert_schema_illegal_send(self, no_input_event)
        args = no_input_event["data"]["arguments"]["args"]
        self.assertEqual(set(args) - SYS_SESSION_SEND_ARGS_SCHEMA, set(),
                         "every key used should itself be schema-known -- "
                         "the failure here is specifically the missing "
                         "required key, not an unrecognised one")
        self.assertIn("input", SYS_SESSION_SEND_ARGS_REQUIRED - set(args))


class AC7FailClosedInvariantUnchanged(DerivationFixture):
    def test_missing_db_denies_and_fail_open_hatch_still_works(self):
        ev = P.worker_semaphore(db="/nonexistent/nowhere/control.db")
        r = ev(send("eng_claude", "x", {"purpose": "implement"}))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("not initialised", r["reason"])
        os.environ["NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"] = "1"
        try:
            r2 = ev(send("eng_claude", "x", {"purpose": "implement"}))
            self.assertEqual(r2["result"], "ALLOW")
        finally:
            del os.environ["NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"]

    def test_one_slot_one_use_compare_and_set_preserved(self):
        self.admit_new()
        r2 = self.policy(send("eng_claude", "ship-T", {"purpose": "implement"}))
        self.assertEqual(r2["result"], "DENY")

    def test_purpose_and_title_are_matched_literally_never_parsed_for_identity(self):
        self.task()
        self.acquire_new()
        # A purpose string that LOOKS like it might smuggle identity is
        # still just a literal, exact-match column value -- it does not
        # widen or redirect the lookup.
        r = self.policy(send("eng_claude", "ship-T", {
            "purpose": "implement; slot_id=1; task_id=T; generation=1",
        }))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("no active undispatched slot matches", r["reason"])
        # the real purpose still works afterward -- nothing was consumed
        r2 = self.policy(send("eng_claude", "ship-T", {"purpose": "implement"}))
        self.assertEqual(r2["result"], "ALLOW", r2)


class AC8CapacityAccountingUnaffected(DerivationFixture):
    def test_released_and_reaped_rows_excluded_from_derivation_and_capacity(self):
        """The derivation query and both new indexes are scoped
        ``WHERE state='active'`` -- a released/reaped row sharing the same
        (agent, title) as a live one must neither ambiguate the lookup nor
        count against capacity."""
        self.task()
        granted = self.acquire_new()
        self.c.run("slot", "release", "--slot", granted["slot_id"],
                   "--holder", "crew_alpha/eng_claude")
        # Re-acquire the identical identity -- must not be treated as a
        # collision with the now-released row (proves the partial index's
        # state='active' scoping, not just the derivation query's).
        second = self.acquire_new()
        self.assertNotEqual(second["slot_id"], granted["slot_id"])
        result = self.policy(send("eng_claude", "ship-T",
                                   {"purpose": "implement"}))
        self.assertEqual(result["result"], "ALLOW", result)
        # exactly one admission recorded (the released row never counted)
        self.assertEqual(
            len(rows(self.c.db, "task_admissions", "task_id='T'")), 1)
        # the released row is still there, still excluded from every
        # state='active' scoped query -- capacity accounting (worker_slots
        # WHERE state='active') and both new indexes see it identically.
        released = rows(self.c.db, "worker_slots",
                        "id=?", (granted["slot_id"],))[0]
        self.assertEqual(released["state"], "released")
        undispatched_active = rows(
            self.c.db, "worker_slots",
            "task_id='T' AND state='active' AND dispatched_at IS NULL")
        self.assertEqual(undispatched_active, [])


class AC9ADRAndContractCommitted(unittest.TestCase):
    def test_adr_and_contract_files_exist_in_this_tree(self):
        adr = os.path.join(
            REPO, "docs", "decisions",
            "ADR-0062-derive-dispatch-admission-from-control-plane-state.md")
        contract = os.path.join(REPO, "docs", "reviews", "471-CONTRACT.md")
        self.assertTrue(os.path.isfile(adr), adr)
        self.assertTrue(os.path.isfile(contract), contract)
        with open(adr, encoding="utf-8") as f:
            head = f.read(200)
        self.assertIn("ADR-0062", head)


if __name__ == "__main__":
    unittest.main()
