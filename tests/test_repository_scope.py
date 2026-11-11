"""Issue #165 / ADR-0084 -- repository scope on the control plane.

Mockless: every assertion drives the real `bin/nightshift-ctl` through
subprocess against a real SQLite file (see `ctlutil.py`), the same way the
rest of this suite does. The one exception is `Validator`, which loads
`bin/nightshift-ctl` and `bin/nightshift_authority.py` as real Python modules
(no stub) to compare their two validators directly.

Each class below corresponds to exactly one of ADR-0084's 15 accepted
criteria (AC1-AC15); the class/method names match the criteria's own
`how_proven` field so a failure here can be traced straight back to the
acceptance table.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from ctlutil import REPO, Ctl, run_parallel
from test_worktree_reap import git, new_bare_pair, write

BIN = Path(REPO) / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
import nightshift_authority as nsa  # noqa: E402


def _load_ctl_module():
    """Load bin/nightshift-ctl as a real module (name != '__main__', so the
    `if __name__ == "__main__": main()` guard at the bottom never fires).
    No try/except around this: a real import failure must fail the test
    loudly, not be swallowed into a skip (issue #698 anti-pattern)."""
    path = BIN / "nightshift-ctl"
    spec = importlib.util.spec_from_loader(
        "nightshift_ctl_under_test",
        importlib.machinery.SourceFileLoader("nightshift_ctl_under_test", str(path)))
    module = importlib.util.module_from_spec(spec)
    sys.modules["nightshift_ctl_under_test"] = module
    spec.loader.exec_module(module)
    return module


def attest(repository_id, state_root=None):
    """Create a real authority.db registry for `repository_id`, the same way
    a genuinely-bootstrapped repository would. `nsa.connect(..., create=True)`
    (the default) creates the sqlite file on disk immediately, which is all
    `registry_exists` (and therefore `repo bind`) checks for -- see
    test_authority_epoch.AbsentRegistryIsLegacy.
    test_a_bootstrapped_but_empty_registry_file_behaves_the_same for the same
    fact pinned against nightshift_authority directly."""
    state_root = state_root or tempfile.mkdtemp(prefix="nightshift-scope-authority-")
    os.chmod(state_root, 0o700)
    cx = nsa.connect(state_root, repository_id)
    cx.close()
    return state_root


def bind(c, repository_id, state_root=None):
    """Attest `repository_id` and bind `c`'s plane to it. Returns
    (code, body, state_root)."""
    state_root = attest(repository_id, state_root)
    code, body = c.run("repo", "bind", "--repository-id", str(repository_id),
                       "--state-root", state_root)
    return code, body, state_root


def table_info(db, table):
    with contextlib.closing(sqlite3.connect(db)) as cx:
        return list(cx.execute(f"PRAGMA table_info({table})"))


def scalar(db, sql, params=()):
    with contextlib.closing(sqlite3.connect(db)) as cx:
        row = cx.execute(sql, params).fetchone()
        return row[0] if row else None


def count(db, table, where="1=1", params=()):
    return scalar(db, f"SELECT COUNT(*) FROM {table} WHERE {where}", params)


def make_task(c, task_id, *, repository_id=None, paths=None, state=None,
             agent="eng_claude", title=None, crew="crew_alpha"):
    title = title or f"fixture-{task_id}"
    argv = ["task", "upsert", "--id", task_id, "--crew", crew,
            "--agent", agent, "--title", title, "--size", "S"]
    if state is not None:
        argv += ["--state", state]
    if paths is not None:
        argv += ["--paths", paths]
    if repository_id is not None:
        argv += ["--repository-id", str(repository_id)]
    code, body = c.run(*argv)
    return code, body, title


def acquire_slot(c, task_id, *, agent="eng_claude", title, crew="crew_alpha",
                 repository_id=None):
    argv = ["slot", "acquire", "--holder", f"{crew}/{agent}", "--crew", crew,
            "--task", task_id, "--agent", agent, "--title", title,
            "--purpose", "implement"]
    if repository_id is not None:
        argv += ["--repository-id", str(repository_id)]
    return c.run(*argv)


class RepoScopeTestCase(unittest.TestCase):
    """Shared setup: a fresh plane with `run current` already set, which
    every task-bound write path in this file needs."""

    def setUp(self):
        self.c = Ctl()
        code, body = self.c.run("run", "current", "--set", "scope-fixture")
        self.assertEqual(code, 0, body)


# --------------------------------------------------------------- AC1 -------

class MigrationShape(unittest.TestCase):
    """AC1: the migration is additive, NOT NULL DEFAULT 0, typeof-CHECKed,
    on all five work tables, and every pre-migration row lands on 0."""

    SCOPED_TABLES = ("tasks", "task_dependencies", "task_claims",
                     "worker_slots", "leases")

    def _revert_to_pre_165(self, db):
        """Simulate a plane that predates #165: drop the two widened
        indexes (SQLite refuses to DROP COLUMN a column an index still
        references) and drop `repository_id` from all five tables."""
        with contextlib.closing(sqlite3.connect(db)) as cx, cx:
            cx.execute("DROP INDEX IF EXISTS one_active_lease_per_repository")
            cx.execute(
                "DROP INDEX IF EXISTS "
                "one_undispatched_dispatch_identity_per_repository")
            for table in self.SCOPED_TABLES:
                cols = {r[1] for r in cx.execute(f"PRAGMA table_info({table})")}
                if "repository_id" in cols:
                    cx.execute(f"ALTER TABLE {table} DROP COLUMN repository_id")

    def test_column_is_not_null_default_zero_on_every_scoped_table(self):
        c = Ctl()
        code, body = c.run("task", "upsert", "--id", "legacy-1")
        self.assertEqual(code, 0, body)

        self._revert_to_pre_165(c.db)
        for table in self.SCOPED_TABLES:
            cols = {r[1] for r in table_info(c.db, table)}
            self.assertNotIn("repository_id", cols, table)

        # Any verb re-runs ensure_schema and re-migrates.
        code, body = c.run("repo", "show")
        self.assertEqual(code, 0, body)

        for table in self.SCOPED_TABLES:
            info = {r[1]: r for r in table_info(c.db, table)}
            self.assertIn("repository_id", info, table)
            _, name, decl_type, notnull, dflt, pk = info["repository_id"]
            self.assertEqual(notnull, 1, f"{table}.repository_id not NOT NULL")
            self.assertEqual(dflt, "0", f"{table}.repository_id default != 0")

        row = scalar(c.db,
                     "SELECT repository_id, typeof(repository_id) "
                     "FROM tasks WHERE id='legacy-1'")
        self.assertEqual(row, 0)
        self.assertEqual(
            scalar(c.db, "SELECT typeof(repository_id) FROM tasks "
                        "WHERE id='legacy-1'"),
            "integer")

        # CHECK(typeof(repository_id)='integer' AND repository_id>=0) must
        # refuse both a non-integer write and a negative one, on every table
        # (spot-checked on two of the five -- the declaration is identical
        # across all five, generated by the same loop).
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute("UPDATE tasks SET repository_id='7' WHERE id='legacy-1'")
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute("UPDATE tasks SET repository_id=-1 WHERE id='legacy-1'")
        code, _ = c.run("lease", "acquire", "--resource", "x", "--holder", "h")
        self.assertEqual(code, 0)
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute("UPDATE leases SET repository_id='9' WHERE resource='x'")


# --------------------------------------------------------------- AC2-4 -----

class LeaseIsolation(RepoScopeTestCase):
    MIG = "db/migrations/**"

    def test_same_resource_string_in_two_repositories_both_granted(self):
        code, body, _ = bind(self.c, 7)
        self.assertEqual(code, 0, body)
        code, body = self.c.run("lease", "acquire", "--resource", "bin/foo.py",
                                "--holder", "crew_7", "--mode", "edit",
                                "--repository-id", "7")
        self.assertEqual(code, 0, body)
        code, body = self.c.run("lease", "acquire", "--resource", "bin/foo.py",
                                "--holder", "crew_9", "--mode", "edit",
                                "--repository-id", "9")
        self.assertEqual(code, 0, body)

    def test_same_resource_same_repository_still_one_winner(self):
        c = self.c
        cmds = [
            c.cmdline("lease", "acquire", "--resource", self.MIG,
                      "--holder", f"crew_{i}", "--mode", "serialize")
            for i in range(6)
        ]
        results = run_parallel(cmds)
        granted = [r for code, r in results if code == 0]
        denied = [r for code, r in results if code == 3]
        self.assertEqual(len(granted), 1, results)
        self.assertEqual(len(denied), 5, results)
        with contextlib.closing(sqlite3.connect(c.db)) as cx:
            row = cx.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='one_active_lease_per_repository'").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("UNIQUE", row[0].upper())

    def test_cross_repository_release_and_break_are_refused(self):
        code, body, _ = bind(self.c, 7)
        self.assertEqual(code, 0, body)
        code, body = self.c.run("lease", "acquire", "--resource", self.MIG,
                                "--holder", "H", "--mode", "serialize")
        self.assertEqual(code, 0, body)

        code, body = self.c.run("lease", "release", "--resource", self.MIG,
                                "--holder", "H", "--event", "pr-merged",
                                "--repository-id", "9")
        self.assertEqual(code, 3, body)
        code, body = self.c.run("lease", "break", "--resource", self.MIG,
                                "--reason", "x", "--repository-id", "9")
        self.assertEqual(code, 3, body)

        code, body = self.c.run("lease", "list")
        self.assertEqual(code, 0, body)
        self.assertEqual(len(body["active"]), 1)
        self.assertEqual(body["active"][0]["state"], "active")


# --------------------------------------------------------------- AC5 -------

class SequenceGate(RepoScopeTestCase):
    def test_lease_in_another_repository_does_not_satisfy_the_gate(self):
        code, body, _ = bind(self.c, 7)
        self.assertEqual(code, 0, body)
        code, body = self.c.run("lease", "acquire", "--resource",
                                "db/migrations/**", "--holder", "H",
                                "--mode", "serialize")
        self.assertEqual(code, 0, body)

        code, body = self.c.run(
            "seq", "next", "--name", "migrations", "--resource",
            "db/migrations/**", "--holder", "H", "--repository-id", "9")
        self.assertEqual(code, 3, body)

        # Same holder, correct (bound-home) scope: the gate DOES open, so
        # the refusal above is about scope, not a broken feature.
        code, body = self.c.run(
            "seq", "next", "--name", "migrations", "--resource",
            "db/migrations/**", "--holder", "H")
        self.assertEqual(code, 0, body)


# --------------------------------------------------------------- AC6 -------

class DispatchIdentity(RepoScopeTestCase):
    def test_identity_collides_within_a_repository_but_not_across_two(self):
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)

        code, body = c.acquire_normal(task="disp-a", agent="eng_claude",
                                      title="SHIP")
        self.assertEqual(code, 0, body)

        code, body, _ = make_task(c, "disp-9", repository_id=9,
                                  agent="eng_claude", title="SHIP")
        self.assertEqual(code, 0, body)
        code, body = acquire_slot(c, "disp-9", agent="eng_claude",
                                  title="SHIP", repository_id=9)
        self.assertEqual(code, 0, body)

        code, body = c.acquire_normal(task="disp-b", agent="eng_claude",
                                      title="SHIP")
        self.assertEqual(code, 3, body)
        self.assertIn("dispatch identity", body["reason"])


# --------------------------------------------------------------- AC7 -------

class ScopeInheritance(RepoScopeTestCase):
    def test_slot_and_claim_carry_the_tasks_repository(self):
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)

        code, slot = c.acquire_normal(task="inherit-1", agent="eng_claude",
                                      title="inherit-title")
        self.assertEqual(code, 0, slot)
        self.assertEqual(
            scalar(c.db, "SELECT repository_id FROM worker_slots WHERE id=?",
                  (slot["slot_id"],)),
            7)
        self.assertEqual(
            scalar(c.db, "SELECT repository_id FROM tasks WHERE id='inherit-1'"),
            7)

        c.seed_admission(task="inherit-1", slot_id=slot["slot_id"],
                         title="inherit-title")
        code, claim = c.run("claim", "acquire", "--task", "inherit-1",
                            "--claimant", "heartbeat-1", "--agent", "eng_claude",
                            "--title", "inherit-title")
        self.assertEqual(code, 0, claim)
        self.assertEqual(
            scalar(c.db, "SELECT repository_id FROM task_claims WHERE id=?",
                  (claim["claim_id"],)),
            7)

        code, body = acquire_slot(c, "inherit-1", agent="another_agent",
                                  title="another-title", repository_id=9)
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"),
                         "repository_scope_conflicts_with_task")


# --------------------------------------------------------------- AC8 -------

class PathScopeIsScoped(RepoScopeTestCase):
    def test_overlap_denies_within_a_repository_and_not_across_two(self):
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        code, body = c.run("lease", "acquire", "--resource", "bin/foo.py",
                           "--holder", "H", "--mode", "edit")
        self.assertEqual(code, 0, body)

        code, body, title9 = make_task(c, "cross-9", repository_id=9,
                                       paths="bin/**", agent="eng_claude")
        self.assertEqual(code, 0, body)
        code, body = acquire_slot(c, "cross-9", agent="eng_claude",
                                  title=title9, repository_id=9)
        self.assertEqual(code, 0, body)

        code, body, title7 = make_task(c, "cross-7", paths="bin/**",
                                       agent="eng_codex")
        self.assertEqual(code, 0, body)
        code, body = acquire_slot(c, "cross-7", agent="eng_codex",
                                  title=title7)
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason"),
                         "task scope overlaps an active lease")
        self.assertEqual(body.get("resource"), "bin/foo.py")
        self.assertEqual(body.get("holder"), "H")


# --------------------------------------------------------------- AC9 -------

class Dependencies(RepoScopeTestCase):
    def test_cross_repository_edge_is_refused(self):
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "A")           # repo 7 (bound default)
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "B", repository_id=9)
        self.assertEqual(code, 0, body)

        code, body = c.run("task", "dependency-add", "--id", "B",
                           "--depends-on", "A")
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "cross_repository_dependency")
        self.assertEqual(
            count(c.db, "task_dependencies",
                 "task_id='B' AND depends_on_task_id='A'"),
            0)


# ----------------------------------------------- issue #700 follow-up -----
# #700/ADR-0084 scoped `task list` but left `task ready` -- the verb that
# actually SELECTS dispatchable work (program/config.yaml: "`task ready`
# governs which REGISTERED task is dispatchable") -- plane-global. These
# tests close that gap: `task ready` must resolve scope exactly like `task
# list` does (same flag, same dest, same default, same refusal codes),
# must never surface another repository's task, must never WRITE another
# repository's `ready_at` (ADR-0060 AC1), and a refused scope must stamp
# nothing.

class ReadyIsScoped(RepoScopeTestCase):
    def test_ready_never_returns_another_repositorys_task(self):
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "A")                       # repo 7
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "B", repository_id=9)
        self.assertEqual(code, 0, body)

        code, body = c.run("task", "ready")
        self.assertEqual(code, 0, body)
        self.assertEqual({t["id"] for t in body["tasks"]}, {"A"})

        code, body = c.run("task", "ready", "--repository-id", "9")
        self.assertEqual(code, 0, body)
        self.assertEqual({t["id"] for t in body["tasks"]}, {"B"})

    def test_ready_and_list_resolve_scope_identically(self):
        """A parity table over ONE plane: the SAME argv suffix run against
        both verbs must agree on (exit code, reason_code, task-id set) --
        compared to EACH OTHER, not to a hardcoded constant, because
        agreement between the two verbs is exactly the property #700
        broke. Two absolute values are still anchored below (both
        verified against merged main before this fix) so the pair cannot
        agree on the WRONG answer. This is also the first coverage `task
        list --repository-id` has ever had."""
        c = self.c
        code, body, _ = make_task(c, "A")            # unbound -> repo 0
        self.assertEqual(code, 0, body)

        def snapshot(*flags):
            lcode, lbody = c.run("task", "list", *flags)
            rcode, rbody = c.run("task", "ready", *flags)
            l_ids = ({t["id"] for t in lbody["tasks"]}
                     if lbody and "tasks" in lbody else None)
            r_ids = ({t["id"] for t in rbody["tasks"]}
                     if rbody and "tasks" in rbody else None)
            l = (lcode, lbody.get("reason_code") if lbody else None, l_ids)
            r = (rcode, rbody.get("reason_code") if rbody else None, r_ids)
            return l, r

        unbound_rows = [(), ("--repository-id", "0"),
                        ("--repository-id", "9"), ("--repository-id", "-1")]
        for flags in unbound_rows:
            with self.subTest(phase="unbound", flags=flags):
                l, r = snapshot(*flags)
                self.assertEqual(l, r, (flags, l, r))

        # Anchors: the parity above holds even if both verbs agreed on a
        # wrong answer -- these two pin the answer itself.
        l9, _ = snapshot("--repository-id", "9")
        self.assertEqual(l9[1], "repository_scope_unbound", l9)
        lneg, _ = snapshot("--repository-id", "-1")
        self.assertEqual(lneg[1], "invalid_repository_id", lneg)

        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        bound_rows = [(), ("--repository-id", "7"), ("--repository-id", "9")]
        for flags in bound_rows:
            with self.subTest(phase="bound", flags=flags):
                l, r = snapshot(*flags)
                self.assertEqual(l, r, (flags, l, r))

    def test_ready_does_not_stamp_ready_at_on_another_repositorys_task(self):
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "A")                       # repo 7
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "B", repository_id=9)
        self.assertEqual(code, 0, body)
        self.assertIsNone(scalar(c.db, "SELECT ready_at FROM tasks WHERE id='A'"))
        self.assertIsNone(scalar(c.db, "SELECT ready_at FROM tasks WHERE id='B'"))

        code, body = c.run("task", "ready")
        self.assertEqual(code, 0, body)
        self.assertIsNotNone(
            scalar(c.db, "SELECT ready_at FROM tasks WHERE id='A'"))
        self.assertIsNone(
            scalar(c.db, "SELECT ready_at FROM tasks WHERE id='B'"))

        a_ready_at = scalar(c.db, "SELECT ready_at FROM tasks WHERE id='A'")
        code, body = c.run("task", "ready", "--repository-id", "9")
        self.assertEqual(code, 0, body)
        self.assertIsNotNone(
            scalar(c.db, "SELECT ready_at FROM tasks WHERE id='B'"))
        # first-write-wins (ADR-0060 AC1): A's own stamp did not move.
        self.assertEqual(
            a_ready_at, scalar(c.db, "SELECT ready_at FROM tasks WHERE id='A'"))

    def test_ready_scope_refusal_stamps_nothing(self):
        c = self.c
        code, body, _ = make_task(c, "A")            # unbound -> repo 0
        self.assertEqual(code, 0, body)
        self.assertIsNone(scalar(c.db, "SELECT ready_at FROM tasks WHERE id='A'"))

        code, body = c.run("task", "ready", "--repository-id", "9")
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "repository_scope_unbound")
        self.assertIsNone(scalar(c.db, "SELECT ready_at FROM tasks WHERE id='A'"))


# -------------------------------------------------------------- AC10 -------

class DefaultIsImplicitHome(RepoScopeTestCase):
    def test_non_zero_scope_refused_before_bind_and_accepted_after(self):
        c = self.c
        code, body = c.run("lease", "acquire", "--resource", "x",
                           "--holder", "H", "--repository-id", "9")
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "repository_scope_unbound")

        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)

        code, body = c.run("lease", "acquire", "--resource", "x",
                           "--holder", "H")
        self.assertEqual(code, 0, body)
        self.assertEqual(
            scalar(c.db, "SELECT repository_id FROM leases WHERE resource='x'"),
            7)

        code, body = c.run("lease", "acquire", "--resource", "y",
                           "--holder", "H", "--repository-id", "9")
        self.assertEqual(code, 0, body)


# -------------------------------------------------------------- AC11 -------

class RepoBind(RepoScopeTestCase):
    def _seed_legacy_rows(self, c):
        code, body, title = make_task(c, "A")
        self.assertEqual(code, 0, body)
        code, body, _ = make_task(c, "B")
        self.assertEqual(code, 0, body)
        code, body = c.run("task", "dependency-add", "--id", "B",
                           "--depends-on", "A")
        self.assertEqual(code, 0, body)
        code, body = c.run("lease", "acquire", "--resource", "res1",
                           "--holder", "H1", "--mode", "edit")
        self.assertEqual(code, 0, body)
        code, slot = acquire_slot(c, "A", agent="eng_claude", title=title)
        self.assertEqual(code, 0, slot)
        c.seed_admission(task="A", slot_id=slot["slot_id"], title=title)
        code, claim = c.run("claim", "acquire", "--task", "A",
                            "--claimant", "heartbeat-1", "--agent", "eng_claude",
                            "--title", title)
        self.assertEqual(code, 0, claim)

    def test_bind_backfills_every_legacy_row_and_refuses_a_second_identity(self):
        c = self.c
        self._seed_legacy_rows(c)
        for table in MigrationShape.SCOPED_TABLES:
            self.assertGreater(count(c.db, table, "repository_id=0"), 0, table)

        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        self.assertFalse(body["already_bound"])
        self.assertTrue(any(n > 0 for n in body["backfilled"].values()), body)

        for table in MigrationShape.SCOPED_TABLES:
            self.assertEqual(count(c.db, table, "repository_id=0"), 0, table)
        self.assertEqual(scalar(c.db, "SELECT value FROM meta WHERE key='repository_id'"),
                         "7")

        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["already_bound"])
        self.assertIsNone(body["backfilled"])

        code, body, _ = bind(c, 9)
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "repository_scope_already_bound")
        self.assertEqual(scalar(c.db, "SELECT value FROM meta WHERE key='repository_id'"),
                         "7")


# -------------------------------------------------------------- AC12 -------

class NoRegression(RepoScopeTestCase):
    def test_unscoped_plane_behaves_identically_and_capacity_stays_global(self):
        c = self.c
        code, body, title = make_task(c, "unbound-1")
        self.assertEqual(code, 0, body)
        code, body = c.run("lease", "acquire", "--resource", "res-u",
                           "--holder", "H")
        self.assertEqual(code, 0, body)
        code, slot = acquire_slot(c, "unbound-1", agent="eng_claude",
                                  title=title)
        self.assertEqual(code, 0, slot)
        c.seed_admission(task="unbound-1", slot_id=slot["slot_id"],
                         title=title)
        code, claim = c.run("claim", "acquire", "--task", "unbound-1",
                            "--claimant", "heartbeat-1", "--agent", "eng_claude",
                            "--title", title)
        self.assertEqual(code, 0, claim)
        code, body = c.run("task", "upsert", "--id", "unbound-2")
        self.assertEqual(code, 0, body)
        code, body = c.run("task", "dependency-add", "--id", "unbound-2",
                           "--depends-on", "unbound-1")
        self.assertEqual(code, 0, body)

        for table in MigrationShape.SCOPED_TABLES:
            total = count(c.db, table)
            self.assertGreater(total, 0, table)
            self.assertEqual(count(c.db, table, "repository_id!=0"), 0, table)

    def test_capacity_is_never_scoped_by_repository(self):
        c = Ctl(capacity={"max_concurrent_workers": 2, "reserve_for_oncall": 0})
        code, _ = c.run("run", "current", "--set", "cap-fixture")
        self.assertEqual(code, 0)
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)

        for i in range(2):
            code, body = c.acquire_normal(task=f"cap-{i}", agent="eng_claude",
                                          title=f"cap-title-{i}")
            self.assertEqual(code, 0, body)

        code, body, _ = make_task(c, "cap-9", repository_id=9,
                                  agent="eng_claude", title="cap-title-9")
        self.assertEqual(code, 0, body)
        code, body = acquire_slot(c, "cap-9", agent="eng_claude",
                                  title="cap-title-9", repository_id=9)
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason"), "worker budget exhausted")


# -------------------------------------------------------------- AC13 -------

class ReapIsScoped(RepoScopeTestCase):
    def test_reap_ignores_another_repositorys_tasks_and_holds(self):
        """Two independent correlations, both against a repo-9 task that
        repo 7's `reap` must never be able to see:

        - `audit_worktrees`: a terminal repo-9 task recorded against a
          worktree path that also exists in repo 7's checkout must not be
          reported as that worktree's owner (it would turn a genuine
          "nobody owns this" finding into a false "orphaned-dirty" one
          naming the wrong task).
        - `reap_delete_branches` condition (h): a LIVE (non-terminal)
          repo-9 task correlated by branch name must not protect that
          branch from deletion under repo 7's scope -- the exact defect
          this AC exists to prevent (a merged, otherwise-deletable branch
          staying retained forever because some OTHER repository happens
          to still be building on a same-named branch).
        """
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)

        repo = new_bare_pair("scope-reap-")
        git(repo, "branch", "feature/shared-name", "main")
        git(repo, "push", "-q", "origin", "feature/shared-name")

        wt_path = os.path.join(repo, ".worktrees", "shared")
        git(repo, "worktree", "add", "-q", "-b", "wip/shared", wt_path, "HEAD")
        write(wt_path, "dirty.md", "stale content\n")

        # Terminal repo-9 task correlated by WORKTREE PATH only.
        code, body = c.run("task", "upsert", "--id", "r9-wt-owner", "--state",
                           "done", "--worktree", wt_path,
                           "--repository-id", "9")
        self.assertEqual(code, 0, body)
        # LIVE repo-9 task correlated by BRANCH NAME only (condition h).
        code, body = c.run("task", "upsert", "--id", "r9-branch-owner",
                           "--state", "building",
                           "--branch", "feature/shared-name",
                           "--repository-id", "9")
        self.assertEqual(code, 0, body)

        code, body = c.run("reap", "--repo", repo, "--delete")
        self.assertIn(code, (0, 5), body)

        entries = {os.path.realpath(e["path"]): e
                  for e in body["worktrees"]["worktrees"]}
        entry = entries[os.path.realpath(wt_path)]
        self.assertIsNone(entry["task"],
                         "repo 9's task must not be visible to repo 7's audit")
        self.assertEqual(entry["verdict"], "unowned-dirty")

        branches = body["delete"]["branches"]
        self.assertIn("origin/feature/shared-name", branches["removed"],
                      "repo 9's LIVE task must not protect repo 7's "
                      "candidate branch from deletion")
        retained_names = {r["branch"] for r in branches["retained"]}
        self.assertNotIn("origin/feature/shared-name", retained_names)


# ------------------------------------------------ reap --delete safety -----

class ReapDeleteRefusesScopeMismatchedWithHome(RepoScopeTestCase):
    """Not one of ADR-0084's 15 numbered ACs -- a gap found reviewing #165:
    AC13 above proves scoping ADDS protection (another repository's task
    must not protect this repository's branch/worktree). Nothing proved the
    opposite direction: that THIS repository's own live tasks stay
    protected when the caller's `--repository-id` disagrees with the
    plane's own bound home. `resolve_scope` will happily hand back any
    attested id (or the `SCOPE_HOME` sentinel) for an explicit flag once
    bound -- correct for a scoped READ, but `reap --delete` is destructive,
    and a mismatched scope there makes `audit_worktrees`/
    `reap_delete_branches` correlate against a bucket this plane's own
    tasks are never in: every real protection silently disappears and a
    live worktree / a branch a live task is still building on gets
    deleted for real.
    """

    def _fixture(self, c):
        """A repo-7 (bound-home) live task protecting a worktree (condition
        b) and a repo-7 live task protecting a merged branch by name
        (condition h) -- the exact pair ADR-0061 exists to retain."""
        repo = new_bare_pair("scope-reap-home-")
        wt_path = os.path.join(repo, ".worktrees", "live7")
        git(repo, "worktree", "add", "-q", "-b", "wip/live7", wt_path, "HEAD")
        code, body = c.run("task", "upsert", "--id", "r7-wt", "--state",
                           "building", "--worktree", wt_path)
        self.assertEqual(code, 0, body)

        git(repo, "branch", "feature/live-7", "main")
        git(repo, "push", "-q", "origin", "feature/live-7")
        code, body = c.run("task", "upsert", "--id", "r7-branch", "--state",
                           "building", "--branch", "feature/live-7")
        self.assertEqual(code, 0, body)
        return repo, wt_path

    def _remote_branches(self, repo):
        text = git(repo, "for-each-ref", "--format=%(refname:short)",
                   "refs/remotes/origin").stdout
        return set(text.split())

    def test_matching_and_absent_scope_still_retain_both(self):
        """Baseline (unchanged behaviour): no flag, and an explicit flag
        that agrees with the bound home, both still protect real live
        work -- the guard must never fire on the common path."""
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        repo, wt_path = self._fixture(c)

        for extra in ([], ["--repository-id", "7"]):
            with self.subTest(extra=extra):
                code, body = c.run("reap", "--repo", repo, "--delete", *extra)
                self.assertEqual(code, 0, body)
                retained_wt = {os.path.realpath(r["path"]) for r in
                              body["delete"]["worktrees"]["retained"]}
                self.assertIn(os.path.realpath(wt_path), retained_wt, body)
                self.assertTrue(os.path.isdir(wt_path))
                retained_br = {r["branch"] for r in
                              body["delete"]["branches"]["retained"]}
                self.assertIn("origin/feature/live-7", retained_br, body)
                self.assertIn("origin/feature/live-7",
                              self._remote_branches(repo))

    def test_scope_home_after_bind_is_refused_not_silently_emptied(self):
        """The exact reported trap: `0` reads as "my own repository" but,
        once bound, names the now-vacated pre-bind bucket instead -- a
        bucket this plane's own tasks are never in."""
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        repo, wt_path = self._fixture(c)

        code, body = c.run("reap", "--repo", repo, "--delete",
                           "--repository-id", "0")
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"),
                         "repository_scope_conflicts_with_home")
        self.assertEqual(body.get("repository_id"), 0)
        self.assertEqual(body.get("home_repository_id"), 7)

        # Nothing was touched: refusal happens before any git mutation.
        self.assertTrue(os.path.isdir(wt_path))
        self.assertIn("origin/feature/live-7", self._remote_branches(repo))

    def test_unattested_non_home_scope_is_also_refused(self):
        """Not just the `0` trap -- any explicit id that disagrees with the
        bound home is equally an empty, non-correlating bucket, attested
        or not."""
        c = self.c
        code, body, _ = bind(c, 7)
        self.assertEqual(code, 0, body)
        repo, wt_path = self._fixture(c)

        code, body = c.run("reap", "--repo", repo, "--delete",
                           "--repository-id", "9")
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"),
                         "repository_scope_conflicts_with_home")
        self.assertTrue(os.path.isdir(wt_path))
        self.assertIn("origin/feature/live-7", self._remote_branches(repo))

    def test_guard_is_a_noop_before_any_bind_exists(self):
        """Pre-bind, the home IS `SCOPE_HOME` -- an explicit `0` agrees
        with it and must not be refused (the guard must not overreach
        beyond the reported trap)."""
        c = self.c
        repo, wt_path = self._fixture(c)
        code, body = c.run("reap", "--repo", repo, "--delete",
                           "--repository-id", "0")
        self.assertEqual(code, 0, body)
        retained_wt = {os.path.realpath(r["path"]) for r in
                      body["delete"]["worktrees"]["retained"]}
        self.assertIn(os.path.realpath(wt_path), retained_wt, body)


# -------------------------------------------------------------- AC14 -------

class IndexPremise(unittest.TestCase):
    def test_task_id_is_a_plane_global_primary_key(self):
        c = Ctl()
        info = table_info(c.db, "tasks")
        pk_cols = [r[1] for r in info if r[5] > 0]
        self.assertEqual(
            pk_cols, ["id"],
            "ADR-0084's seven unwidened indexes (one_active_claim, "
            "one_active_new_reservation, one_active_planning_target, "
            "one_active_slot_per_claim, one_active_qa_review_continuation, "
            "one_active_program_authority, and every task_id-keyed lookup) "
            "rest on tasks.id being a plane-global PRIMARY KEY. If this "
            "fails, every one of those indexes needs repository_id added.")


# -------------------------------------------------------------- AC15 -------

class Validator(unittest.TestCase):
    def test_agrees_with_nightshift_authority(self):
        mod = _load_ctl_module()
        values = (0, 1, -1, True, 2**63, "abc", "7", 7.0, None)
        for value in values:
            with self.subTest(value=value):
                try:
                    ctl_result = ("ok", mod._validate_scope_value(value))
                except mod.RepositoryScopeError as exc:
                    ctl_result = ("refused", exc.reason_code)
                try:
                    nsa_result = ("ok", nsa.validate_repository_id(value))
                except nsa.AuthorityError as exc:
                    nsa_result = ("refused", exc.reason_code)

                if value == mod.SCOPE_HOME and isinstance(value, int) \
                        and not isinstance(value, bool):
                    # The one documented divergence: 0 names "this plane's
                    # own repository" to the control plane, but is not a
                    # real, attestable repository id to authority.db.
                    self.assertEqual(ctl_result, ("ok", 0))
                    self.assertEqual(nsa_result[0], "refused")
                else:
                    self.assertEqual(ctl_result, nsa_result,
                                     f"diverged on {value!r}")


if __name__ == "__main__":
    unittest.main()
