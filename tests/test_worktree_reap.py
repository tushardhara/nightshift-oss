"""Issue #120: worktrees nobody owns, and the commit shape that makes them lethal.

Two mechanisms, tested together because they are two halves of one defect.

THE DETECTOR (`worktree audit`, wired into `reap`)
--------------------------------------------------
A task row reaches a terminal state, the crew moves on, and the linked
worktree it was cut for stays on disk carrying a live index. `reap` expired
claims, leases and slots and knew nothing about worktrees, so the single
condition that had already destroyed merged work twice (#89, #98) was the one
condition nothing watched.

What makes such a worktree dangerous is not the staleness by itself — it is
that every check git makes on a commit there PASSES. The tree really is a
descendant of the ref; the push really is a fast-forward. Only the content is
old. Instance three (#120) was defused by luck: its local ref happened to sit
five commits behind origin, so a commit was rejected non-fast-forward. That is
a property of a ref, not a control, and it evaporates the moment anyone
fast-forwards the shared ref for an unrelated reason.

THE GUARD (`guard-commit` + `hook install`)
--------------------------------------------
The shape, not the instance: a commit that deletes a path still present on the
integration ref is refused. It ships as a git pre-commit hook rather than a
wrapper verb because the decision log already ruled on that question — "a
guard that agents must remember to invoke cannot fix a discipline failure",
and the ADR-0009 wrapper "survives as ergonomics, not as the safety boundary.
Nothing may depend on it being called." So the tests below drive `git commit`
itself, not the verb: a guard that is only ever tested through its own front
door has not been tested at the altitude it has to work at.
"""

import contextlib
import importlib.machinery
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from ctlutil import Ctl, VIRTUAL_T0

IDENT = ["-c", "user.name=t", "-c", "user.email=t@localhost"]
REF = "program/discovery-state"


def git(repo, *argv, check=True):
    return subprocess.run(["git", "-C", repo] + list(argv),
                          capture_output=True, text=True, check=check)


def commit(repo, message):
    git(repo, *IDENT, "commit", "-qm", message)


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def new_repo(prefix):
    """A git repository whose default branch is the integration branch."""
    repo = tempfile.mkdtemp(prefix=prefix)
    git(repo, "init", "-q", "-b", REF)
    return repo


def find(report, path):
    """The audit entry for one worktree, by realpath."""
    want = os.path.realpath(path)
    for entry in report["worktrees"]:
        if os.path.realpath(entry["path"]) == want:
            return entry
    raise AssertionError(f"{path} missing from audit of "
                         f"{[e['path'] for e in report['worktrees']]}")


class Detector(unittest.TestCase):
    """`worktree audit` — who owns this worktree, and is anything in it?"""

    def setUp(self):
        self.repo = new_repo("wt-audit-repo-")
        write(self.repo, "merged.md", "content that is already merged\n")
        git(self.repo, "add", "merged.md")
        commit(self.repo, "base")
        self.c = Ctl()

    def worktree(self, name, branch=None):
        path = os.path.join(self.repo, ".worktrees", name)
        git(self.repo, "worktree", "add", "-q", "-b", branch or f"wip/{name}",
            path, "HEAD")
        return path

    def audit(self):
        code, body = self.c.run("worktree", "audit", "--repo", self.repo)
        return code, body

    # ---------------------------------------------------------- the defect --

    def test_terminal_task_with_dirty_worktree_is_flagged(self):
        """The minimum bar: terminal owner + non-empty tree = a finding."""
        wt = self.worktree("t1")
        self.c.run("task", "upsert", "--id", "t1", "--state", "done",
                   "--worktree", wt)
        write(wt, "merged.md", "stale content from before the merge\n")
        code, body = self.audit()
        entry = find(body, wt)
        self.assertEqual(entry["verdict"], "orphaned-dirty")
        self.assertTrue(entry["reapable"])
        self.assertEqual(entry["task"], "t1")
        self.assertEqual(entry["task_state"], "done")
        self.assertEqual(body["flagged"], 1)
        self.assertEqual(code, 5, "an orphaned worktree must be a non-zero "
                                  "exit or no machine ever notices it")

    def test_staged_deletions_are_the_armed_shape(self):
        """#120's actual index: merged paths staged for deletion, nobody owning it."""
        wt = self.worktree("armed")
        self.c.run("task", "upsert", "--id", "armed", "--state", "merged",
                   "--worktree", wt)
        git(wt, "rm", "-q", "merged.md")
        code, body = self.audit()
        entry = find(body, wt)
        self.assertEqual(entry["counts"]["staged"], 1)
        self.assertTrue(entry["reapable"])
        self.assertEqual(code, 5)

    def test_unstaging_alone_does_not_clear_the_finding(self):
        """Disarmed is not safe, and the detector must not report it as safe.

        #120 was partially defused by unstaging the index — a reversible,
        non-destructive step that took the staged count to zero. The stale
        content stayed in the WORKING TREE, so a commit that stages
        everything is still exactly as destructive as before. A detector
        that only looked at the index would have called that worktree clean
        and handed back a false all-clear on a live landmine.
        """
        wt = self.worktree("disarmed")
        self.c.run("task", "upsert", "--id", "disarmed", "--state", "done",
                   "--worktree", wt)
        git(wt, "rm", "-q", "merged.md")
        self.assertTrue(find(self.audit()[1], wt)["reapable"])

        git(wt, "restore", "--staged", ".")          # the exact disarm step
        code, body = self.audit()
        entry = find(body, wt)
        self.assertEqual(entry["counts"]["staged"], 0, "the index really is empty")
        self.assertEqual(entry["counts"]["unstaged"], 1,
                         "and the destructive content is still on disk")
        self.assertTrue(entry["reapable"],
                        "unstaging is reversible cleanup, not a fix — reporting "
                        "this worktree as clean would be the false all-clear "
                        "that #120 exists to prevent")
        self.assertEqual(code, 5)

    def test_unowned_dirty_worktree_is_flagged(self):
        """No task claims it at all — the strongest form of 'nobody owns this'.

        This is #120's literal instance: the worktree had a shared
        integration branch checked out, belonging to no single task, so
        neither the recorded path nor the branch matched anything.
        """
        wt = self.worktree("nobody")
        write(wt, "merged.md", "stale\n")
        code, body = self.audit()
        entry = find(body, wt)
        self.assertEqual(entry["verdict"], "unowned-dirty")
        self.assertIsNone(entry["task"])
        self.assertTrue(entry["reapable"])
        self.assertEqual(code, 5)

    # ------------------------------------------------- what must NOT fire --

    def test_live_task_dirty_worktree_is_not_flagged(self):
        """A working seat's messy desk is not litter. False positives kill detectors."""
        wt = self.worktree("t2")
        self.c.run("task", "upsert", "--id", "t2", "--state", "building",
                   "--worktree", wt)
        write(wt, "merged.md", "work in progress\n")
        code, body = self.audit()
        entry = find(body, wt)
        self.assertEqual(entry["verdict"], "owned-dirty")
        self.assertFalse(entry["reapable"])
        self.assertEqual(body["flagged"], 0)
        self.assertEqual(code, 0)

    def test_terminal_task_with_clean_worktree_is_not_flagged(self):
        """Terminal and empty is tidy-up, not danger. Only the plane's own
        state machine may call that garbage, and nothing here deletes."""
        wt = self.worktree("t3")
        self.c.run("task", "upsert", "--id", "t3", "--state", "merged",
                   "--worktree", wt)
        code, body = self.audit()
        self.assertEqual(find(body, wt)["verdict"], "clean")
        self.assertEqual(code, 0)

    def test_primary_worktree_is_never_reapable(self):
        """The shared checkout is dirty as a matter of routine — it is where the
        Program writes state — and removing it removes the repository.

        On this box that is /srv/project, which must never be committed from
        and never removed. Encoding that in the tool rather than in a habit is
        the whole point: three of this org's incidents were a control that
        existed only as something everyone was supposed to remember.
        """
        write(self.repo, "merged.md", "the Program is mid-write\n")
        write(self.repo, "untracked-report.md", "a new report\n")
        code, body = self.audit()
        entry = find(body, self.repo)
        self.assertTrue(entry["primary"])
        self.assertEqual(entry["verdict"], "primary")
        self.assertFalse(entry["reapable"])
        self.assertEqual(body["flagged"], 0)
        self.assertEqual(code, 0)

    def test_ignored_files_do_not_flag(self):
        """__pycache__ is on every checkout here. A detector that fires on build
        output gets switched off within a day, which is how this class of
        control dies — see #115 and #118 in the same wave."""
        write(self.repo, ".gitignore", "__pycache__/\n")
        git(self.repo, "add", ".gitignore")
        commit(self.repo, "ignore build output")
        wt = self.worktree("t4")
        self.c.run("task", "upsert", "--id", "t4", "--state", "done",
                   "--worktree", wt)
        write(wt, "__pycache__/thing.cpython-312.pyc", "bytecode\n")
        code, body = self.audit()
        entry = find(body, wt)
        self.assertEqual(entry["counts"]["untracked"], 0)
        self.assertEqual(entry["verdict"], "clean")
        self.assertEqual(code, 0)

    # --------------------------------------------------------- correlation --

    def test_branch_correlates_when_the_recorded_path_is_null(self):
        """`tasks.worktree` is sparse in practice, so branch is the fallback.

        Of the 60 task rows on the live plane when this was written, 52 had
        `worktree` NULL — including `platform-92-hermetic`, the owner in
        #120's own instance. A detector that joined only on the recorded path
        would have failed on the very case it was written for.
        """
        wt = self.worktree("t5", branch="crew_a/t5-work")
        self.c.run("task", "upsert", "--id", "t5", "--state", "done",
                   "--branch", "crew_a/t5-work")
        write(wt, "merged.md", "stale\n")
        entry = find(self.audit()[1], wt)
        self.assertEqual(entry["task"], "t5")
        self.assertEqual(entry["matched_by"], "branch")
        self.assertEqual(entry["verdict"], "orphaned-dirty")
        self.assertTrue(entry["reapable"])

    def test_recorded_path_wins_over_branch(self):
        """Path is the stronger signal: two worktrees can hold one branch, but
        a recorded path names exactly one."""
        wt = self.worktree("t6", branch="crew_a/shared")
        self.c.run("task", "upsert", "--id", "by-branch", "--state", "done",
                   "--branch", "crew_a/shared")
        self.c.run("task", "upsert", "--id", "by-path", "--state", "building",
                   "--worktree", wt, "--branch", "crew_a/shared")
        write(wt, "merged.md", "work in progress\n")
        entry = find(self.audit()[1], wt)
        self.assertEqual(entry["task"], "by-path")
        self.assertEqual(entry["matched_by"], "worktree-path")
        self.assertFalse(entry["reapable"])

    # ------------------------------------- ambiguity: one name, many rows --
    #
    # PR #122 finding 2 (`architect`, cross-vendor). Correlation used to take
    # the FIRST matching row, so when a name spanned rows in different states
    # the verdict was decided by SQLite row order. Terminal row written first
    # => a worktree holding LIVE work was reported reapable. These tests pin
    # the conservative rule in BOTH insertion orders, because a test that
    # passes only in the order rows happen to be written is the same defect
    # wearing a green tick.
    #
    # This is not a contrived shape. On the live plane when this was written,
    # ten branches spanned more than one task row, and two rows recorded the
    # same worktree path -- so both signals needed the same fix, and both are
    # covered below.

    ORDERS = ("live-first", "terminal-first")

    def _rows(self, order, live, terminal):
        """Upsert a live and a terminal row in the requested order."""
        pair = [live, terminal] if order == "live-first" else [terminal, live]
        for kwargs in pair:
            self.c.run("task", "upsert", *kwargs)

    def test_live_row_wins_over_terminal_row_matched_by_branch(self):
        """A branch shared by a finished task and a running one is NOT litter.

        The failure this pins: with the terminal row first, the old code
        answered `orphaned-dirty` / reapable for a worktree whose live owner
        was sitting right there in the same result set.
        """
        for order in self.ORDERS:
            with self.subTest(order=order):
                name = order.replace("-", "")
                branch = f"crew_a/reused-{name}"
                wt = self.worktree(f"amb-{name}", branch=branch)
                self._rows(
                    order,
                    live=("--id", f"live-{name}", "--state", "building",
                          "--branch", branch),
                    terminal=("--id", f"dead-{name}", "--state", "merged",
                              "--branch", branch))
                write(wt, "merged.md", "live work in progress\n")
                entry = find(self.audit()[1], wt)
                self.assertEqual(entry["matched_by"], "branch")
                self.assertEqual(entry["verdict"], "owned-dirty")
                self.assertFalse(entry["reapable"],
                                 "live work reported reapable in "
                                 f"{order} order")
                # The reported owner is a function of the match SET, not of
                # the order the rows were written.
                self.assertEqual(entry["task"], f"live-{name}")
                self.assertEqual(entry["task_state"], "building")

    def test_live_row_wins_over_terminal_row_matched_by_path(self):
        """The recorded path is ambiguous too, and for the same reason.

        `/srv/project/.worktrees/alpha-fix-70-evidence` carried two task rows
        on the live plane. Path being the stronger signal does not make it a
        unique one, so it gets the identical all-rows treatment.
        """
        for order in self.ORDERS:
            with self.subTest(order=order):
                name = order.replace("-", "")
                wt = self.worktree(f"ambpath-{name}")
                self._rows(
                    order,
                    live=("--id", f"plive-{name}", "--state", "pending",
                          "--worktree", wt),
                    terminal=("--id", f"pdead-{name}", "--state", "done",
                              "--worktree", wt))
                write(wt, "merged.md", "live work in progress\n")
                entry = find(self.audit()[1], wt)
                self.assertEqual(entry["matched_by"], "worktree-path")
                self.assertEqual(entry["verdict"], "owned-dirty")
                self.assertFalse(entry["reapable"],
                                 f"live work reported reapable in {order} "
                                 "order via the recorded path")
                self.assertEqual(entry["task"], f"plive-{name}")

    def test_every_matching_row_terminal_still_reaps_in_either_order(self):
        """Conservatism must not switch the detector off.

        The counterpart to the two tests above: when NO matching row is live
        the worktree is still litter, and it is still flagged whichever row
        came first. A guard that resolves every ambiguity to `keep` would
        pass those tests and detect nothing.
        """
        for order in self.ORDERS:
            with self.subTest(order=order):
                name = order.replace("-", "")
                branch = f"crew_a/bothdead-{name}"
                wt = self.worktree(f"dead-{name}", branch=branch)
                self._rows(
                    order,
                    live=("--id", f"m1-{name}", "--state", "merged",
                          "--branch", branch),
                    terminal=("--id", f"m2-{name}", "--state", "superseded",
                              "--branch", branch))
                write(wt, "merged.md", "stale\n")
                entry = find(self.audit()[1], wt)
                self.assertEqual(entry["verdict"], "orphaned-dirty")
                self.assertTrue(entry["reapable"])
                self.assertFalse(entry["ambiguous"])
                self.assertEqual(entry["task"], f"m1-{name}")

    def test_ambiguous_correlation_is_reported_not_just_resolved(self):
        """Resolving quietly is how a detector stops informing anyone.

        The conservative answer is the right one, but an operator still has
        to be able to see that the correlation was ambiguous and which rows
        made it so -- otherwise the only way to learn a branch is overloaded
        is to notice a worktree that never gets reaped.
        """
        branch = "crew_a/overloaded"
        wt = self.worktree("amb-report", branch=branch)
        self.c.run("task", "upsert", "--id", "zz-old", "--state", "merged",
                   "--branch", branch)
        self.c.run("task", "upsert", "--id", "aa-now", "--state", "building",
                   "--branch", branch)
        write(wt, "merged.md", "live\n")
        entry = find(self.audit()[1], wt)
        self.assertTrue(entry["ambiguous"])
        self.assertEqual([t["id"] for t in entry["tasks"]],
                         ["aa-now", "zz-old"])
        self.assertEqual([t["live"] for t in entry["tasks"]], [True, False])
        self.assertIn("zz-old", entry["note"])
        self.assertFalse(entry["reapable"])

    def test_unrecognised_task_state_reads_as_live(self):
        """The terminal set is a closed list; everything else is someone's work.

        `blocked` is a state this plane uses and the terminal set does not
        name. It must not be reapable, and the next state someone invents
        must not be either -- reapability is opt-in by enumeration, so an
        unknown state fails toward the recoverable error.
        """
        for state in ("blocked", "some-state-invented-next-wave"):
            with self.subTest(state=state):
                branch = f"crew_a/{state}"
                wt = self.worktree(f"unk-{abs(hash(state)) % 9999}",
                                   branch=branch)
                self.c.run("task", "upsert", "--id", f"t-{state}",
                           "--state", state, "--branch", branch)
                write(wt, "merged.md", "work\n")
                entry = find(self.audit()[1], wt)
                self.assertEqual(entry["verdict"], "owned-dirty")
                self.assertFalse(entry["reapable"])


class ReapWiring(unittest.TestCase):
    """The audit has to live on the verb this org actually runs.

    A separate command nobody remembers to call is the same defect as the one
    being fixed — #120 names #115 (`risky_action` inert) and #118 (per-seat
    policies never loaded) as the same pattern in the same wave: a control the
    org believed was in force, that was not.
    """

    def test_reap_reports_and_still_expires(self):
        repo = new_repo("wt-reap-repo-")
        write(repo, "merged.md", "merged\n")
        git(repo, "add", "merged.md")
        commit(repo, "base")
        wt = os.path.join(repo, ".worktrees", "orphan")
        git(repo, "worktree", "add", "-q", "-b", "wip/orphan", wt, "HEAD")

        c = Ctl(clock=VIRTUAL_T0)
        title = "fixture-orphan"
        code, slot = c.acquire_normal(
            task="orphan", title=title, ttl="0.2",
        )
        self.assertEqual(code, 0, slot)
        c.seed_admission(
            task="orphan", slot_id=slot["slot_id"], title=title,
        )
        code, claim = c.run(
            "claim", "acquire", "--task", "orphan", "--claimant", "lead",
            "--agent", "eng_claude", "--title", title, "--ttl", "0.2",
        )
        self.assertEqual(code, 0, claim)
        c.run("task", "upsert", "--id", "orphan", "--state", "done",
              "--worktree", wt)
        write(wt, "merged.md", "stale content\n")
        c.advance(0.3)

        code, body = c.run("reap", "--repo", repo)
        self.assertEqual(body["slots_reaped"], 1,
                         "the worktree audit must not disturb the expiry reap "
                         "already does")
        self.assertEqual(body["claims_expired"], 1)
        self.assertEqual(body["worktrees"]["flagged"], 1)
        self.assertEqual(find(body["worktrees"], wt)["verdict"], "orphaned-dirty")
        self.assertEqual(code, 5)

    def test_reap_is_quiet_when_worktrees_are_clean(self):
        repo = new_repo("wt-reap-clean-")
        write(repo, "merged.md", "merged\n")
        git(repo, "add", "merged.md")
        commit(repo, "base")
        c = Ctl()
        code, body = c.run("reap", "--repo", repo)
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual(body["worktrees"]["flagged"], 0)

    def test_no_repository_is_reported_not_guessed(self):
        """A control plane outside any checkout has nothing to leak — but the
        difference between 'audited, found nothing' and 'could not audit' has
        to stay visible. Every condition in #120's family was assumed rather
        than observed; an empty list that reads like a clean bill of health is
        how that happens again."""
        c = Ctl()
        code, body = c.run("reap")      # --db lives in a bare temp dir
        self.assertEqual(code, 0)
        self.assertFalse(body["worktrees"]["available"])
        self.assertTrue(body["worktrees"]["reason"])
        self.assertEqual(body["worktrees"]["flagged"], 0)


def new_bare_pair(prefix):
    """A bare upstream plus a clone wired as `origin`, `main` as HEAD.

    AC-5's branch-delete tests need a real remote to push feature branches
    to and `--delete` a merged one from -- `new_repo()` above is a single
    non-bare repo (fine for worktree-only tests, which never push) and
    cannot receive a push to its own checked-out branch at all.
    """
    upstream = tempfile.mkdtemp(prefix=prefix + "-upstream-")
    git(upstream, "init", "-q", "--bare")
    root = tempfile.mkdtemp(prefix=prefix + "-clone-")
    clone = os.path.join(root, "work")
    git(root, "clone", "-q", upstream, "work")
    git(clone, "checkout", "-q", "-b", "main")
    write(clone, "base.md", "base\n")
    git(clone, "add", "base.md")
    commit(clone, "base")
    git(clone, "push", "-q", "origin", "main")
    git(clone, "remote", "set-head", "origin", "main")
    return clone


class DeleteWorktrees(unittest.TestCase):
    """`reap --delete` / `--dry-run` -- ADR-0061, accepted AC-1/AC-2/AC-4.

    Deliberately a DIFFERENT predicate from the audit's own `reapable`: the
    audit flags DIRTY, unowned/orphaned debris as a finding; `--delete` only
    ever removes the opposite case -- a CLEAN worktree with no live task, no
    active claim, and no active lease standing over it. Every retention case
    below pins one of the five conditions (a)-(e) in isolation.
    """

    def setUp(self):
        self.repo = new_repo("wt-delete-repo-")
        write(self.repo, "merged.md", "content that is already merged\n")
        git(self.repo, "add", "merged.md")
        commit(self.repo, "base")
        self.c = Ctl()

    def worktree(self, name, branch=None):
        path = os.path.join(self.repo, ".worktrees", name)
        git(self.repo, "worktree", "add", "-q", "-b", branch or f"wip/{name}",
            path, "HEAD")
        return path

    def reap(self, *extra):
        return self.c.run("reap", "--repo", self.repo, "--delete", *extra)

    def claim(self, task_id, state="active"):
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "INSERT INTO task_claims(task_id,claimant,state,acquired_at) "
                "VALUES(?,?,?,?)", (task_id, "tester", state, 0.0))

    def lease(self, task_id, state="active"):
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "INSERT INTO leases(resource,mode,holder,task_id,state,"
                "acquired_at) VALUES(?,?,?,?,?,?)",
                ("bin/nightshift-ctl", "edit", "tester", task_id, state, 0.0))

    # ------------------------------------------------------------ AC-7 --

    def test_fully_reclaimable_worktree_is_removed_and_reported(self):
        """The happy path: terminal task, no claim, no lease, clean tree."""
        wt = self.worktree("done")
        self.c.run("task", "upsert", "--id", "done", "--state", "done",
                   "--worktree", wt)
        code, body = self.reap()
        d = body["delete"]["worktrees"]
        self.assertEqual(d["removed"], [wt])
        # the primary checkout itself is always evaluated and always
        # retained (condition a) -- it is the only OTHER entry here
        self.assertEqual([r["path"] for r in d["retained"]], [self.repo])
        self.assertFalse(os.path.exists(wt))
        self.assertNotIn(wt, [w["path"] for w in
                              self.c.run("worktree", "audit", "--repo",
                                         self.repo)[1]["worktrees"]])

    def test_unowned_clean_worktree_is_also_reclaimable(self):
        """No task correlates at all -- (b)-(d) are vacuously satisfied."""
        wt = self.worktree("nobody")
        code, body = self.reap()
        d = body["delete"]["worktrees"]
        self.assertIn(wt, d["removed"])
        self.assertFalse(os.path.exists(wt))

    # ------------------------------------------------------ AC-1 / AC-6 --

    def test_primary_worktree_is_never_delete_eligible(self):
        """Condition (a), unconditional: overrides everything else."""
        _, body = self.reap()
        d = body["delete"]["worktrees"]
        retained = {r["path"]: r["reason"] for r in d["retained"]}
        self.assertIn(self.repo, retained)
        self.assertIn("condition a", retained[self.repo])
        self.assertTrue(os.path.isdir(self.repo))

    def test_task_live_worktree_is_retained(self):
        """Condition (b): a live task's clean desk is still someone's desk."""
        wt = self.worktree("live")
        self.c.run("task", "upsert", "--id", "live", "--state", "building",
                   "--worktree", wt)
        _, body = self.reap()
        retained = {r["path"]: r["reason"] for r in
                   body["delete"]["worktrees"]["retained"]}
        self.assertIn(wt, retained)
        self.assertIn("condition b", retained[wt])
        self.assertTrue(os.path.exists(wt))

    def test_claimed_worktree_is_retained(self):
        """Condition (c): an active claim on the correlated task blocks delete
        even though the task row itself already reads terminal."""
        wt = self.worktree("claimed")
        self.c.run("task", "upsert", "--id", "claimed", "--state", "done",
                   "--worktree", wt)
        self.claim("claimed")
        _, body = self.reap()
        retained = {r["path"]: r["reason"] for r in
                   body["delete"]["worktrees"]["retained"]}
        self.assertIn(wt, retained)
        self.assertIn("condition c", retained[wt])
        self.assertTrue(os.path.exists(wt))

    def test_leased_worktree_is_retained(self):
        """Condition (d): same shape as (c), the other resource kind."""
        wt = self.worktree("leased")
        self.c.run("task", "upsert", "--id", "leased", "--state", "done",
                   "--worktree", wt)
        self.lease("leased")
        _, body = self.reap()
        retained = {r["path"]: r["reason"] for r in
                   body["delete"]["worktrees"]["retained"]}
        self.assertIn(wt, retained)
        self.assertIn("condition d", retained[wt])
        self.assertTrue(os.path.exists(wt))

    def test_expired_claim_does_not_block_delete(self):
        """Only `state='active'` counts -- a released/expired claim is not a
        live hold, matching AC-1(c)'s own wording."""
        wt = self.worktree("released")
        self.c.run("task", "upsert", "--id", "released", "--state", "done",
                   "--worktree", wt)
        self.claim("released", state="released")
        _, body = self.reap()
        self.assertIn(wt, body["delete"]["worktrees"]["removed"])

    def test_dirty_worktree_is_retained(self):
        """Condition (e): uncommitted content, however it got there, blocks
        delete -- this is the audit's own dangerous case, and it must stay
        dangerous (flagged, never auto-removed), not become auto-deleted."""
        wt = self.worktree("dirty")
        self.c.run("task", "upsert", "--id", "dirty", "--state", "merged",
                   "--worktree", wt)
        write(wt, "merged.md", "stale uncommitted content\n")
        _, body = self.reap()
        retained = {r["path"]: r["reason"] for r in
                   body["delete"]["worktrees"]["retained"]}
        self.assertIn(wt, retained)
        self.assertIn("condition e", retained[wt])
        self.assertTrue(os.path.exists(wt))
        # and the pre-existing danger-audit still flags it, unrelated to
        # --delete leaving it alone
        self.assertEqual(find(body["worktrees"], wt)["verdict"],
                         "orphaned-dirty")

    def test_unreadable_worktree_is_retained_not_assumed_clean(self):
        """Condition (e)'s other half: absence of proof is not proof of
        absence. A worktree whose git state cannot be read must never be
        treated as satisfying `clean`."""
        wt = self.worktree("broken")
        self.c.run("task", "upsert", "--id", "broken", "--state", "done",
                   "--worktree", wt)
        os.remove(os.path.join(wt, ".git"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("not a real gitdir pointer\n")
        _, body = self.reap()
        retained = {r["path"]: r["reason"] for r in
                   body["delete"]["worktrees"]["retained"]}
        self.assertIn(wt, retained)
        self.assertIn("unreadable", retained[wt])
        self.assertTrue(os.path.exists(wt))

    # ------------------------------------------------------------ AC-4 --

    def test_dry_run_lists_without_removing(self):
        wt = self.worktree("wouldgo")
        self.c.run("task", "upsert", "--id", "wouldgo", "--state", "done",
                   "--worktree", wt)
        _, body = self.reap("--dry-run")
        d = body["delete"]["worktrees"]
        self.assertTrue(d["dry_run"])
        self.assertIn(wt, d["removed"])
        self.assertTrue(os.path.exists(wt), "dry-run must not remove anything")
        # a real, undecorated audit still sees it, untouched
        self.assertEqual(find(body["worktrees"], wt)["verdict"], "clean")

    # -------------------------------------------------- failure / empty --

    def test_nothing_reclaimable_reports_zero_removed_and_available(self):
        """Distinguishable from a failed scan: available True, removed []."""
        wt = self.worktree("live-only")
        self.c.run("task", "upsert", "--id", "live-only", "--state",
                   "building", "--worktree", wt)
        code, body = self.reap()
        d = body["delete"]["worktrees"]
        self.assertTrue(d["available"])
        self.assertEqual(d["removed"], [])
        self.assertEqual(code, 0)

    def test_no_repository_is_a_failed_scan_not_an_empty_one(self):
        c = Ctl()
        code, body = c.run("reap", "--delete")   # --db lives in a bare temp dir
        d = body["delete"]["worktrees"]
        self.assertFalse(d["available"])
        self.assertTrue(d["reason"])
        self.assertEqual(d["removed"], [])

    def test_removal_failure_is_reported_per_worktree_and_reap_continues(self):
        """Permissions / open handles: named, not fatal to the rest of the
        run. Forced by making ONLY the `jammed` worktree's own directory
        unwritable -- git needs write+exec on it to delete the files inside
        before it can remove the entry -- while `clear`, a sibling under the
        same `.worktrees/` parent, is left fully writable and must still go
        through. Our own clean-check (staged/unstaged/untracked) still
        passes for `jammed` since permission bits don't change file
        content -- so this exercises git's OWN removal failure, not ours."""
        wt = self.worktree("jammed")
        wt2 = self.worktree("clear")
        self.c.run("task", "upsert", "--id", "jammed", "--state", "done",
                   "--worktree", wt)
        self.c.run("task", "upsert", "--id", "clear", "--state", "done",
                   "--worktree", wt2)
        mode = os.stat(wt).st_mode
        os.chmod(wt, 0o555)
        try:
            _, body = self.reap()
        finally:
            os.chmod(wt, mode)
        d = body["delete"]["worktrees"]
        self.assertIn(wt2, d["removed"], "one failure must not stop the rest")
        errored = {e["path"] for e in d["errors"]}
        self.assertIn(wt, errored)
        self.assertTrue(os.path.exists(wt),
                        "a failed removal must not half-delete the tree")

    # -------------------------------------------------------------- AC-3 --

    def test_undecorated_reap_has_no_delete_key_at_all(self):
        """Byte-for-byte: `--delete` is strictly additive."""
        wt = self.worktree("untouched")
        self.c.run("task", "upsert", "--id", "untouched", "--state", "done",
                   "--worktree", wt)
        _, body = self.c.run("reap", "--repo", self.repo)
        self.assertNotIn("delete", body)
        self.assertTrue(os.path.exists(wt))


class DeleteWorktreesTOCTOU(unittest.TestCase):
    """MERGE_BLOCKER-2 (PR #466 round 1, security): `entry["counts"]` is a
    scan-time snapshot produced once by `audit_worktrees` at the top of
    `cmd_reap`; `reap_delete_worktrees` used to trust it all the way to the
    `git worktree remove --force` call, which explicitly suppresses git's
    own dirty-worktree refusal. Real wall-clock elapses between the scan and
    any given worktree's turn in the delete loop (real fleets run ~20
    worktrees, ~3 git calls each) -- so a worktree that was genuinely clean
    at scan time can go dirty for real before its turn comes up, and nothing
    would have caught that.

    Exercising the real cross-process race through the CLI's black-box
    subprocess interface (every other test in this file drives `nightshift-
    ctl` that way) is inherently non-deterministic -- there is no hook to
    pause the delete loop mid-flight. So this drives `reap_delete_worktrees`
    directly instead, importing `bin/nightshift-ctl` exactly the way
    `tests/test_scheduled_run_detector.py`'s `StanddownStatus` class already
    does for `nightshift_scheduled_run_detector` (guarded by the module's
    own `if __name__ == "__main__"`, so importing it has no side effects).
    It feeds the function a hand-built `wt_report` in the EXACT shape
    `audit_worktrees` would have produced at the moment of the scan -- clean,
    terminal task, no claim/lease -- against a worktree whose real, on-disk
    state has since changed. That proves the re-check the fix adds; it is a
    different claim from `DeleteWorktrees.test_dirty_worktree_is_retained`
    above, which only proves a scan that ALREADY saw the dirt retains it --
    true before this fix too.
    """

    CTL_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin", "nightshift-ctl")

    @classmethod
    def setUpClass(cls):
        # `nightshift-ctl` has no `.py` suffix, so -- unlike
        # `test_scheduled_run_detector.py`'s `DETECTOR` -- the loader can't
        # be inferred from the filename; it has to be named explicitly.
        loader = importlib.machinery.SourceFileLoader(
            "nightshift_ctl_toctou_433", cls.CTL_PATH)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.ctl_module = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.ctl_module)

    def setUp(self):
        self.repo = new_repo("wt-toctou-repo-")
        write(self.repo, "merged.md", "content that is already merged\n")
        git(self.repo, "add", "merged.md")
        commit(self.repo, "base")
        self.c = Ctl()

    def worktree(self, name):
        path = os.path.join(self.repo, ".worktrees", name)
        git(self.repo, "worktree", "add", "-q", "-b", f"wip/{name}",
           path, "HEAD")
        return path

    def test_worktree_dirtied_after_scan_is_retained_not_removed(self):
        wt = self.worktree("raced")
        # the exact `audit_worktrees` entry shape at THE MOMENT of the scan:
        # clean, terminal task, no claim, no lease -- i.e. fully eligible by
        # every condition EXCEPT what happens to the tree after this point
        stale_entry = {
            "path": wt, "primary": False,
            "tasks": [{"id": "raced", "live": False}],
            "counts": {"staged": 0, "unstaged": 0, "untracked": 0},
        }
        stale_report = {"available": True, "worktrees": [stale_entry]}
        # real wall-clock elapses; the worktree goes dirty for real, on
        # disk, exactly like a concurrent agent editing it between this
        # box's scan and its turn in the delete loop would
        write(wt, "merged.md", "raced-in content, post-scan\n")

        with contextlib.closing(sqlite3.connect(self.c.db)) as cx:
            cx.row_factory = sqlite3.Row
            result = self.ctl_module.reap_delete_worktrees(
                cx, self.repo, stale_report, dry_run=False)

        self.assertEqual(result["removed"], [],
                         "a stale-clean snapshot must never authorize "
                         "deleting a worktree that is dirty for real")
        retained = {r["path"]: r["reason"] for r in result["retained"]}
        self.assertIn(wt, retained)
        self.assertIn("became dirty between scan and delete", retained[wt])
        self.assertTrue(os.path.exists(wt))
        self.assertEqual(result["errors"], [])


class DeleteBranches(unittest.TestCase):
    """`reap --delete` branch cleanup -- ADR-0061, accepted AC-5, AC-4."""

    def setUp(self):
        self.repo = new_bare_pair("branch-delete-")
        self.c = Ctl()

    def push_branch(self, name, from_ref="main"):
        git(self.repo, "branch", name, from_ref)
        git(self.repo, "push", "-q", "origin", name)
        return git(self.repo, "rev-parse", name).stdout.strip()

    def diverge(self, name):
        """A branch with a commit NOT reachable from main -- never merged."""
        git(self.repo, "checkout", "-q", "-b", name, "main")
        write(self.repo, f"{name}.md", "unmerged work\n")
        git(self.repo, "add", f"{name}.md")
        commit(self.repo, f"work on {name}")
        git(self.repo, "push", "-q", "origin", name)
        git(self.repo, "checkout", "-q", "main")

    def reap(self, *extra):
        return self.c.run("reap", "--repo", self.repo, "--delete", *extra)

    def branch_names(self):
        text = git(self.repo, "for-each-ref", "--format=%(refname:short)",
                   "refs/remotes/origin").stdout
        return set(text.split())

    # ------------------------------------------------------------ AC-5 --

    def test_merged_untasked_branch_is_removed_and_reported(self):
        self.push_branch("feature/merged")
        _, body = self.reap()
        b = body["delete"]["branches"]
        self.assertIn("origin/feature/merged", b["removed"])
        self.assertNotIn("origin/feature/merged", self.branch_names())

    def test_option_like_branch_name_is_deleted_safely_not_injected(self):
        """MERGE_BLOCKER-1 (PR #466 round 1, security): `for-each-ref`
        enumerates whatever ref shape actually exists on `origin`, and
        git's low-level ref validator accepts a ref shaped like an option
        (`refs/heads/--receive-pack=...`) even though `git check-ref-format
        --branch` -- the stricter check `git branch`/an ordinary `git push`
        of a new branch applies -- rejects it. So neither of those would
        ever let this name exist in the first place; it is constructed here
        via `update-ref` directly on the bare upstream and the local
        tracking ref, bypassing that porcelain validation the same way
        security's own disposable-scratch-repo reproduction did. Without
        `--` before the branch operand in `reap_delete_branches`, `git push
        origin --delete <name>` would parse this name as an option --
        e.g. `--receive-pack=<path>` selects the program the remote side
        runs -- rather than as the ref to delete."""
        upstream = git(self.repo, "remote", "get-url",
                       "origin").stdout.strip()
        main_sha = git(self.repo, "rev-parse", "main").stdout.strip()
        marker = os.path.join(tempfile.gettempdir(),
                              "nightshift-433-injection-marker")
        evil = f"--receive-pack={marker}"
        git(upstream, "update-ref", f"refs/heads/{evil}", main_sha)
        git(self.repo, "update-ref", f"refs/remotes/origin/{evil}", main_sha)

        _, body = self.reap()

        b = body["delete"]["branches"]
        self.assertEqual(b["errors"], [],
                         "an option-shaped ref must not be misparsed into "
                         "a git error either")
        self.assertIn(f"origin/{evil}", b["removed"],
                      "the option-shaped ref must be recognised and "
                      "deleted as an ordinary branch, not swallowed as a "
                      "flag")
        remaining = git(upstream, "for-each-ref", "--format=%(refname)",
                        "refs/heads").stdout
        self.assertNotIn(evil, remaining,
                         "the ref must actually be gone from the real "
                         "remote, not silently left behind by a no-op")
        self.assertFalse(os.path.exists(marker),
                         "no argument-injection side effect (e.g. an "
                         "invoked --receive-pack program) occurred")

    def test_main_is_never_deletable(self):
        _, body = self.reap()
        retained = {r["branch"]: r["reason"] for r in
                   body["delete"]["branches"]["retained"]}
        self.assertIn("origin/main", retained)
        self.assertIn("condition f", retained["origin/main"])
        self.assertIn("origin/main", self.branch_names())

    def test_origin_head_symref_is_excluded_not_offered_at_all(self):
        _, body = self.reap()
        b = body["delete"]["branches"]
        names = ({r["branch"] for r in b["retained"]} | set(b["removed"]))
        self.assertNotIn("origin/HEAD", names,
                         "a symref must never even be evaluated as a "
                         "candidate, let alone removed")

    def test_integration_ref_is_protected(self):
        self.push_branch("program/discovery-state")
        _, body = self.reap()
        retained = {r["branch"]: r["reason"] for r in
                   body["delete"]["branches"]["retained"]}
        self.assertIn("origin/program/discovery-state", retained)
        self.assertIn("condition f",
                      retained["origin/program/discovery-state"])

    def test_unmerged_branch_is_retained(self):
        """Condition (g): not an ancestor of the freshly-fetched origin/main."""
        self.diverge("feature/wip")
        _, body = self.reap()
        retained = {r["branch"]: r["reason"] for r in
                   body["delete"]["branches"]["retained"]}
        self.assertIn("origin/feature/wip", retained)
        self.assertIn("condition g", retained["origin/feature/wip"])
        self.assertIn("origin/feature/wip", self.branch_names())

    def test_merged_branch_correlated_to_a_live_task_is_retained(self):
        """Condition (h): a later round is still building on the same name --
        the exact #215-adjacent reuse pattern `_owning_task`'s docstring
        describes, now applied to branch deletion."""
        self.push_branch("feature/reused")
        self.c.run("task", "upsert", "--id", "still-building", "--state",
                   "building", "--branch", "feature/reused")
        _, body = self.reap()
        retained = {r["branch"]: r["reason"] for r in
                   body["delete"]["branches"]["retained"]}
        self.assertIn("origin/feature/reused", retained)
        self.assertIn("condition h", retained["origin/feature/reused"])

    def test_merged_branch_whose_task_reached_integrated_is_deletable(self):
        """The staff-mandated ADR-0061 fix, pinned directly: `integrated` is
        READINESS-terminal even though it is deliberately excluded from
        TERMINAL_TASK_STATES (the worktree-litter set). Reusing the wrong
        constant here would permanently retain the single most common
        shipped end-state -- this test fails on that regression."""
        self.push_branch("feature/shipped")
        self.c.run("task", "upsert", "--id", "shipped", "--state",
                   "integrated", "--branch", "feature/shipped")
        _, body = self.reap()
        self.assertIn("origin/feature/shipped",
                      body["delete"]["branches"]["removed"])

    def test_merged_branch_whose_only_matching_task_is_terminal_is_deletable(self):
        self.push_branch("feature/done")
        self.c.run("task", "upsert", "--id", "old-round", "--state", "merged",
                   "--branch", "feature/done")
        _, body = self.reap()
        self.assertIn("origin/feature/done",
                      body["delete"]["branches"]["removed"])

    # ------------------------------------------------------------ AC-4 --

    def test_dry_run_lists_without_removing(self):
        self.push_branch("feature/wouldgo")
        _, body = self.reap("--dry-run")
        b = body["delete"]["branches"]
        self.assertTrue(b["dry_run"])
        self.assertIn("origin/feature/wouldgo", b["removed"])
        self.assertIn("origin/feature/wouldgo", self.branch_names(),
                      "dry-run must not remove anything")

    # -------------------------------------------------- failure / empty --

    def test_nothing_reclaimable_reports_zero_removed_and_available(self):
        self.diverge("feature/wip")
        code, body = self.reap()
        b = body["delete"]["branches"]
        self.assertTrue(b["available"])
        self.assertEqual(b["removed"], [])
        self.assertEqual(code, 0)


class PlantedSubmoduleFsmonitor(unittest.TestCase):
    """Audit C2: a seat populates a gitlink with its own repository whose
    config names an fsmonitor command. Plain `git diff`/`status` recurse into
    it and run that command; the audit and reap must never do so, and must
    never call such a worktree clean."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wt-fsmonitor-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        lib = os.path.join(self.tmp, "lib")
        git(self.tmp, "init", "-q", "-b", "main", lib)
        write(lib, "lib.txt", "lib\n")
        git(lib, "add", "lib.txt")
        commit(lib, "lib")
        lib_sha = git(lib, "rev-parse", "HEAD").stdout.strip()
        self.repo = new_repo("wt-fsmonitor-repo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        write(self.repo, "merged.md", "merged\n")
        git(self.repo, "add", "merged.md")
        git(self.repo, "update-index", "--add", "--cacheinfo",
            f"160000,{lib_sha},vendor/x")
        commit(self.repo, "base")
        self.wt = os.path.join(self.repo, ".worktrees", "done")
        git(self.repo, "worktree", "add", "-q", "-b", "wip/done", self.wt,
            "HEAD")
        nested = os.path.join(self.wt, "vendor", "x")
        if os.path.isdir(nested):
            os.rmdir(nested)
        git(self.tmp, "clone", "-q", lib, nested)
        self.marker = os.path.join(self.tmp, "fsmonitor-ran")
        payload = write(self.tmp, "payload",
                        f"#!/bin/sh\n: > {self.marker}\nexit 1\n")
        os.chmod(payload, 0o755)
        git(nested, "config", "core.fsmonitor", payload)
        write(nested, "lib.txt", "dirty\n")
        self.c = Ctl()
        self.c.run("task", "upsert", "--id", "done", "--state", "done",
                   "--worktree", self.wt)

    def test_plant_is_live_for_plain_git(self):
        git(self.wt, "status", "--porcelain")
        self.assertTrue(os.path.exists(self.marker),
                        "the fixture never armed fsmonitor; the tests below "
                        "would pass vacuously")

    def test_audit_and_delete_never_run_it_and_never_remove_it(self):
        _, body = self.c.run("worktree", "audit", "--repo", self.repo)
        entry = find(body, self.wt)
        self.assertEqual(entry["counts"]["populated_submodules"], 1)
        self.assertNotEqual(entry["verdict"], "clean")
        code, body = self.c.run("reap", "--repo", self.repo, "--delete")
        self.assertNotIn(self.wt, body["delete"]["worktrees"]["removed"])
        self.assertTrue(os.path.isdir(self.wt))
        self.assertFalse(os.path.exists(self.marker))


class StagedDeletionGuard(unittest.TestCase):
    """`guard-commit` — refuse a commit that deletes what is live on the ref."""

    def setUp(self):
        self.upstream = new_repo("guard-up-")
        write(self.upstream, "merged.md", "merged work\n")
        write(self.upstream, "also-merged.md", "more merged work\n")
        git(self.upstream, "add", "merged.md", "also-merged.md")
        commit(self.upstream, "base")
        self.root = tempfile.mkdtemp(prefix="guard-clone-")
        self.repo = os.path.join(self.root, "work")
        git(self.root, "clone", "-q", self.upstream, "work")
        self.c = Ctl()

    def guard(self, *extra):
        return self.c.run("guard-commit", "--repo", self.repo, *extra)

    def test_clean_index_passes(self):
        code, body = self.guard()
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual(body["deletions"], [])

    def test_additions_and_modifications_pass(self):
        write(self.repo, "new.md", "brand new\n")
        write(self.repo, "merged.md", "an ordinary edit\n")
        git(self.repo, "add", "new.md", "merged.md")
        code, body = self.guard()
        self.assertEqual(code, 0, body)

    def test_deletion_of_a_live_path_is_refused(self):
        git(self.repo, "rm", "-q", "merged.md")
        code, body = self.guard()
        self.assertEqual(code, 3)
        self.assertFalse(body["ok"])
        self.assertEqual(body["deletions"], ["merged.md"])

    def test_every_deleted_live_path_is_named(self):
        """The refusal has to be actionable. #120's index carried 21 deletions;
        a message saying only 'some files' would send the reader back to git."""
        git(self.repo, "rm", "-q", "merged.md", "also-merged.md")
        code, body = self.guard()
        self.assertEqual(code, 3)
        self.assertEqual(body["deletions"], ["also-merged.md", "merged.md"])

    def test_deleting_a_path_that_is_not_on_the_ref_passes(self):
        """Creating a file and dropping it again touches nothing merged."""
        write(self.repo, "scratch.md", "temporary\n")
        git(self.repo, "add", "scratch.md")
        commit(self.repo, "add scratch")
        git(self.repo, "rm", "-q", "scratch.md")
        code, body = self.guard()
        self.assertEqual(code, 0, body)

    def test_a_rename_away_from_a_live_path_is_still_a_deletion(self):
        """Rename detection is off on purpose.

        With it on, moving a merged file reports as R and its deletion never
        appears — so an identical index would pass or fail depending on
        whether some unrelated added file happened to look similar. The
        question is whether the commit removes a path that is live on the
        integration branch, and that is answered by the path set.
        """
        git(self.repo, "mv", "merged.md", "moved.md")
        code, body = self.guard()
        self.assertEqual(code, 3)
        self.assertEqual(body["deletions"], ["merged.md"])

    def test_unresolvable_ref_fails_closed(self):
        """ADR-0009's central mechanism was falsified by the opposite choice.

        Its freshness guard could not fetch on sandboxed seats (#101), so it
        compared two stale values and PASSED — fail-open, for precisely the
        seats most likely to need it. A guard that cannot see what it is
        protecting must refuse, and must say which of the two it is: "I
        checked and it is fine" and "I could not check" are different answers.
        """
        git(self.repo, "rm", "-q", "merged.md")
        code, body = self.guard("--ref", "origin/no-such-branch")
        self.assertEqual(code, 3)
        self.assertFalse(body["ok"])
        self.assertIn("cannot resolve", body["reason"])
        self.assertNotIn("deletions", body,
                         "a refusal for not knowing must not be mistakable "
                         "for a refusal for finding something")

    def test_guard_needs_no_control_plane(self):
        """It has to run from any worktree, including one with no plane in
        reach — ADR-0005's own argument for why `pr-lint` is plane-free."""
        p = subprocess.run(
            [os.environ.get("NIGHTSHIFT_TEST_CTL") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "bin", "nightshift-ctl"),
             "--db", os.path.join(self.root, "nonexistent", "control.db"),
             "guard-commit", "--repo", self.repo],
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


class PreCommitHook(unittest.TestCase):
    """The guard has to fire without anyone choosing to run it.

    These drive `git commit` rather than the verb. The decision log's ruling
    is that nothing may depend on the wrapper being called, so a test suite
    that only ever enters through the wrapper would be testing the half of
    the mechanism that was explicitly declared not to be the safety boundary.
    """

    def setUp(self):
        self.upstream = new_repo("hook-up-")
        write(self.upstream, "merged.md", "merged work\n")
        git(self.upstream, "add", "merged.md")
        commit(self.upstream, "base")
        self.root = tempfile.mkdtemp(prefix="hook-clone-")
        self.repo = os.path.join(self.root, "work")
        git(self.root, "clone", "-q", self.upstream, "work")
        self.c = Ctl()

    def install(self, *extra):
        return self.c.run("hook", "install", "--repo", self.repo, *extra)

    def test_installed_hook_blocks_a_destructive_commit(self):
        code, body = self.install()
        self.assertEqual(code, 0, body)
        self.assertTrue(os.access(body["path"], os.X_OK))

        git(self.repo, "rm", "-q", "merged.md")
        before = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        p = git(self.repo, *IDENT, "commit", "-m", "an innocuous message",
                check=False)
        self.assertNotEqual(p.returncode, 0,
                            "the commit that reverts merged work must not land")
        self.assertIn("merged.md", p.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(),
                         before, "HEAD moved — the deletion landed anyway")

    def test_installed_hook_lets_ordinary_work_through_quietly(self):
        """A guard that prints a paragraph on every commit is a guard people
        delete, and a guard that blocks ordinary work is worse than none."""
        code, body = self.install()
        self.assertEqual(code, 0, body)
        self.assertTrue(os.path.exists(body["path"]),
                        "without this the test passes vacuously against any "
                        "build that cannot install the hook at all")
        write(self.repo, "new.md", "new work\n")
        git(self.repo, "add", "new.md")
        p = git(self.repo, *IDENT, "commit", "-m", "add new work", check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr.strip(), "",
                         "success must be silent on stderr")

    def test_hook_covers_linked_worktrees(self):
        """Every checkout that matters here is a linked worktree, whose .git is
        a file. Resolving the hooks directory through git rather than assuming
        `.git/hooks` is what makes one install cover them all."""
        self.install()
        wt = os.path.join(self.root, "linked")
        git(self.repo, "worktree", "add", "-q", "-b", "wip/linked", wt, "HEAD")
        git(wt, "rm", "-q", "merged.md")
        p = git(wt, *IDENT, "commit", "-m", "delete from the linked worktree",
                check=False)
        self.assertNotEqual(p.returncode, 0,
                            "a linked worktree is exactly where #89, #98 and "
                            "#120 all happened")

    def test_install_refuses_to_clobber_a_foreign_hook(self):
        hooks = git(self.repo, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.repo, hooks)
        os.makedirs(hooks, exist_ok=True)
        path = os.path.join(hooks, "pre-commit")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n# somebody else's hook\nexit 0\n")

        code, body = self.install()
        self.assertEqual(code, 3)
        self.assertIn("refusing to overwrite", body["reason"])
        with open(path) as f:
            self.assertIn("somebody else's hook", f.read())

        code, body = self.install("--force")
        self.assertEqual(code, 0, body)
        with open(path) as f:
            self.assertIn("staged-deletion guard", f.read())

    def test_the_checkout_being_committed_cannot_supply_the_guard(self):
        """Audit C10: the hook preferred `$root/bin/nightshift-ctl`, so any
        checkout shipping a file by that name ran it on every commit -- and
        an `exit 0` there waved the destructive commit through."""
        sentinel = os.path.join(self.root, "checkout-ctl-ran")
        write(self.repo, "bin/nightshift-ctl",
              f"#!/bin/sh\n: > {sentinel}\nexit 0\n")
        os.chmod(os.path.join(self.repo, "bin/nightshift-ctl"), 0o755)
        code, body = self.install()
        self.assertEqual(code, 0, body)
        git(self.repo, "rm", "-q", "merged.md")
        p = git(self.repo, *IDENT, "commit", "-m", "x", check=False)
        self.assertNotEqual(p.returncode, 0, "the planted ctl passed the commit")
        self.assertFalse(os.path.exists(sentinel))

    def test_reinstall_over_our_own_hook_is_idempotent(self):
        self.assertEqual(self.install()[0], 0)
        code, body = self.install()
        self.assertEqual(code, 0, body)


if __name__ == "__main__":
    unittest.main()
