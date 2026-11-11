"""RT-25 — the control plane is discovered, never invented (issue #31, ADR-0005).

`DEFAULT_DB` used to be the relative path `.omni/control.db`, resolved against
the process CWD, and `connect()` created whatever it named. From any directory
that was not the project root the tool therefore built a brand-new empty
control plane and answered questions about it without ever erroring.

That is not a cosmetic bug. On a phantom plane `slot acquire` always grants
(the project's defect 6, through a side door), `claim acquire` always grants
(defect 7) and `lease acquire` grants any resource — so the semaphore the whole
org's concurrency budget rests on silently becomes a no-op for any caller with
the wrong working directory.

WA-008 — what these tests would have shown had the claim been false:
against the pre-fix binary at base 7c16fbe, `test_rt25_mutating_command_from_a_
foreign_cwd_refuses` fails on its FIRST assertion, because the run exits 0 with
{"granted": true, "slot_id": 1} and leaves a real SQLite file behind. Reproduce:

    git show 7c16fbe:bin/nightshift-ctl > /tmp/ctl-base && chmod +x /tmp/ctl-base
    NIGHTSHIFT_TEST_CTL=/tmp/ctl-base python3 -m unittest test_db_resolution -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ctlutil import CTL, Ctl


class DbResolution(unittest.TestCase):
    def setUp(self):
        self.foreign = tempfile.mkdtemp(prefix="rt25-foreign-")
        # A foreign CWD is only a fair test if it starts with nothing in it and
        # no .omni anywhere above it. /tmp satisfies the second half.
        self.addCleanup(shutil.rmtree, self.foreign, True)

    def _run(self, *argv, cwd=None, env=None):
        e = dict(os.environ)
        e.pop("NIGHTSHIFT_CTL_DB", None)
        e.pop("NIGHTSHIFT_NOW", None)
        e.update(env or {})
        p = subprocess.run([CTL, *[str(a) for a in argv]], cwd=cwd or self.foreign,
                           capture_output=True, text=True, env=e)
        body = None
        if p.stdout.strip():
            try:
                body = json.loads(p.stdout.strip().splitlines()[-1])
            except json.JSONDecodeError:
                body = None
        return p.returncode, body, p.stdout, p.stderr

    def _nothing_created(self):
        leftovers = []
        for root, dirs, files in os.walk(self.foreign):
            for f in files:
                leftovers.append(os.path.join(root, f))
        return leftovers

    def _find_all_control_dbs(self, root):
        """Return paths to all control.db files under root."""
        dbs = []
        for dirpath, dirnames, filenames in os.walk(root):
            if "control.db" in filenames:
                dbs.append(os.path.join(dirpath, "control.db"))
        return dbs

    # ---- the headline regression ------------------------------------------
    def test_rt25_mutating_command_from_a_foreign_cwd_refuses(self):
        """A mutating command from a foreign CWD: no database, no success."""
        code, body, _, _ = self._run("slot", "acquire", "--holder",
                                     "crew_alpha/eng", "--crew", "crew_alpha",
                                     "--task", "phantom")
        # 1. it must not report success. Pre-fix this was 0 with granted:true.
        self.assertNotEqual(code, 0, f"granted on a plane that does not exist: {body}")
        self.assertIsNotNone(body, "the contract is one JSON object on stdout")
        self.assertFalse(body.get("granted", False),
                         "defect 6 through the side door: a phantom plane "
                         "grants every slot it is asked for")
        self.assertEqual(body.get("error"), "control_plane_missing")
        # 2. it must not have created anything.
        self.assertEqual(self._nothing_created(), [],
                         "a phantom control plane was written to disk")

    def test_rt25_release_does_not_report_a_false_negative(self):
        """The observed symptom in #31: {"released": 0} instead of a failure.

        `released: 0` reads as "your slot was already gone" — which is how this
        defect convinced a previous session it had corrupted another crew's
        slot. A missing plane must not be able to answer this question at all.
        """
        code, body, _, _ = self._run("slot", "release", "--slot", "21",
                                     "--holder", "crew_alpha/eng_claude")
        self.assertEqual(body.get("error"), "control_plane_missing", body)
        self.assertNotIn("released", body)
        self.assertEqual(self._nothing_created(), [])

    def test_rt25_claim_and_lease_also_refuse(self):
        """Defect 7 and the lease equivalent close by the same mechanism."""
        for argv in (("claim", "acquire", "--task", "t", "--claimant", "x"),
                     ("lease", "acquire", "--resource", "bin/*",
                      "--holder", "crew_alpha", "--mode", "edit")):
            with self.subTest(cmd=argv[0]):
                code, body, _, _ = self._run(*argv)
                self.assertNotEqual(code, 0, body)
                self.assertEqual(body.get("error"), "control_plane_missing")
                self.assertNotEqual(body.get("granted"), True)
        self.assertEqual(self._nothing_created(), [])

    # ---- the contracts the fix must NOT break ------------------------------
    def test_rt25_beat_show_stays_fail_closed_and_keeps_its_two_keys(self):
        """`beat show` has a security-cleared contract (PR #16, 8cd0f88).

        A missing plane must reach that SAME interception, not a new one: one
        JSON object, exactly two keys, exit 3. The watchdog branches on 0 vs 3
        and an exit-1 traceback is neither.
        """
        code, body, _, _ = self._run("beat", "show", "--who", "program")
        self.assertEqual(code, 3, "not-alive is a VERDICT, exit 3")
        self.assertEqual(body, {"alive": False,
                                "reason_code": "control_plane_unreadable"})
        self.assertEqual(self._nothing_created(),
                         [], "not even the liveness path may create a plane")

    def test_rt25_init_is_the_one_command_that_creates(self):
        code, body, _, _ = self._run("init")
        self.assertEqual(code, 0, body)
        self.assertTrue(os.path.isfile(
            os.path.join(self.foreign, ".omni", "control.db")))
        # and once it exists, the plane resolves from that directory
        code, body, _, _ = self._run("slot", "list")
        self.assertEqual(code, 0, body)

    def test_rt25_explicit_db_to_a_missing_plane_also_refuses(self):
        """--db is explicit about WHICH plane, never permission to invent one."""
        target = os.path.join(self.foreign, "nested", "deep", "control.db")
        code, body, _, _ = self._run("--db", target, "slot", "list")
        self.assertNotEqual(code, 0)
        self.assertEqual(body.get("error"), "control_plane_missing")
        self.assertFalse(os.path.exists(os.path.dirname(target)),
                         "the parent directory was created for a plane that "
                         "was never allowed to exist")

    def test_rt25_discovery_finds_the_plane_from_a_subdirectory(self):
        """The watchdog's shape: cd somewhere inside the project, no --db.

        deploy/nightshift-watchdog.sh does `cd $PROJECT_DIR && ctl beat show`
        with no --db, so discovery has to work from inside a project tree or
        the dead-man's switch breaks. crew_beta owns that file and this crew
        may not edit it, which is precisely why this is pinned here.

        WA-009 (issue #64): the PRE-FIX binary passes this test by
        MANUFACTURING a plane at a/b/.omni/control.db instead of discovering
        c.dir/.omni/control.db, so exit=0 and active=[] are satisfied either
        way. The control (must PASS): the fixed binary discovers the real
        plane and creates nothing. The counterfactual (must FAIL): the pre-fix
        binary manufactures a phantom plane. Evidence discriminates only if we
        assert the filesystem state, not just the JSON output.
        """
        c = Ctl()  # builds a real plane in its own temp project
        sub = os.path.join(c.dir, "a", "b")
        os.makedirs(sub)
        code, body, _, _ = self._run("slot", "list", cwd=sub)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["active"], [])
        # The fix: assert no phantom plane was manufactured.
        dbs = self._find_all_control_dbs(c.dir)
        self.assertEqual(dbs, [c.db],
                         f"expected only {c.db}, found phantom planes: {dbs}")

    def test_rt25_discovery_does_not_escape_the_repo(self):
        """Discovery stops at a .git boundary rather than adopting a stranger.

        Without the bound, a project nested under a directory that happens to
        hold `.omni/control.db` would silently drive the outer plane — the same
        class of bug wearing a different hat.
        """
        outer = tempfile.mkdtemp(prefix="rt25-outer-")
        self.addCleanup(shutil.rmtree, outer, True)
        os.makedirs(os.path.join(outer, ".omni"))
        subprocess.run([CTL, "--db", os.path.join(outer, ".omni", "control.db"),
                        "init"], capture_output=True)
        inner = os.path.join(outer, "inner")
        os.makedirs(os.path.join(inner, ".git"))
        code, body, _, _ = self._run("slot", "list", cwd=inner)
        self.assertNotEqual(code, 0,
                            "discovery escaped a .git boundary and adopted an "
                            "unrelated control plane")
        self.assertEqual(body.get("error"), "control_plane_missing")

    def test_rt25_a_git_worktree_shares_the_projects_plane(self):
        """A worktree is the same project, so it uses the same control plane.

        This is not a convenience. Engineers implement in worktrees and run
        `nightshift-ctl` there; if discovery stopped at the worktree's `.git`
        FILE they would get `control_plane_missing` plus a hint suggesting
        `init` -- which would create a phantom plane in the worktree, i.e.
        this defect reintroduced by its own error message.

        WA-009 (issue #64): the PRE-FIX binary passes this test by
        MANUFACTURING a plane at wt/.omni/control.db instead of discovering
        c.dir/.omni/control.db. The control: the fixed binary discovers the
        real plane and creates nothing. The counterfactual: the pre-fix binary
        manufactures a phantom plane in the worktree.
        """
        c = Ctl()
        subprocess.run(["git", "init", "-q", c.dir], capture_output=True)
        subprocess.run(["git", "-C", c.dir, "commit", "-q", "--allow-empty",
                        "-m", "x"], capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t",
                                GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                                GIT_COMMITTER_EMAIL="t@e"))
        wt = os.path.join(self.foreign, "wt")
        r = subprocess.run(["git", "-C", c.dir, "worktree", "add", "-q", wt],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest(f"git worktree unavailable: {r.stderr}")
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")),
                        "expected a worktree .git FILE, not a directory")
        code, body, _, _ = self._run("slot", "list", cwd=wt)
        self.assertEqual(code, 0, body)
        self.assertNotIn("error", body or {})
        # The fix: assert no phantom plane was manufactured in the worktree.
        # The worktree is in self.foreign, so check both trees.
        dbs_main = self._find_all_control_dbs(c.dir)
        dbs_wt = self._find_all_control_dbs(self.foreign)
        self.assertEqual(dbs_main, [c.db],
                         f"expected only {c.db} in main tree, found: {dbs_main}")
        self.assertEqual(dbs_wt, [],
                         f"worktree should create no phantom plane, found: {dbs_wt}")

    def test_rt25_env_var_still_wins_over_discovery(self):
        c = Ctl()
        code, body, _, _ = self._run("slot", "list",
                                     env={"NIGHTSHIFT_CTL_DB": c.db})
        self.assertEqual(code, 0, body)


class UntrustedControlPlane(unittest.TestCase):
    """Audit C9: a target repo that TRACKS `.omni/control.db` (or its -wal)
    hands discovery forged slots, authority rows and triggers. Only a plane
    this uid made -- untracked, not a link, 0600 or tighter -- is opened."""

    setUp = DbResolution.setUp
    _run = DbResolution._run

    def _repo_with_plane(self):
        repo = os.path.join(self.foreign, "repo")
        os.makedirs(os.path.join(repo, ".omni"))
        subprocess.run(["git", "init", "-q", repo], check=True)
        db = os.path.join(repo, ".omni", "control.db")
        code, body, _, _ = self._run("--db", db, "init")
        self.assertEqual(code, 0, body)
        return repo, db

    def assert_untrusted(self, result, reason_part):
        code, body, _, _ = result
        self.assertEqual(code, 1, body)
        self.assertEqual(body["error"], "control_plane_untrusted", body)
        self.assertIn(reason_part, body["reason"])

    def test_init_creates_the_plane_owner_only(self):
        _, db = self._repo_with_plane()
        self.assertEqual(os.stat(db).st_mode & 0o777, 0o600)

    def test_a_tracked_plane_is_refused_discovered_or_explicit(self):
        repo, db = self._repo_with_plane()
        subprocess.run(["git", "-C", repo, "add", "-f", ".omni/control.db"],
                       check=True)
        self.assert_untrusted(self._run("slot", "list", cwd=repo), "tracked")
        self.assert_untrusted(self._run("--db", db, "slot", "list"), "tracked")
        self.assert_untrusted(self._run("--db", db, "init"), "tracked")

    def test_a_tracked_wal_alone_is_refused(self):
        repo, db = self._repo_with_plane()
        with open(db + "-wal", "wb"):
            pass
        os.chmod(db + "-wal", 0o600)
        subprocess.run(["git", "-C", repo, "add", "-f",
                        ".omni/control.db-wal"], check=True)
        self.assert_untrusted(self._run("slot", "list", cwd=repo), "tracked")

    def test_a_symlinked_plane_is_refused(self):
        repo, real = self._repo_with_plane()
        os.rename(real, real + ".real")
        os.symlink(real + ".real", real)
        self.assert_untrusted(self._run("slot", "list", cwd=repo), "symlink")

    def test_a_group_writable_plane_is_refused(self):
        repo, db = self._repo_with_plane()
        os.chmod(db, 0o664)
        self.assert_untrusted(self._run("slot", "list", cwd=repo), "writable")

    def test_a_legacy_world_readable_plane_is_tightened_and_opened(self):
        repo, db = self._repo_with_plane()
        os.chmod(db, 0o644)
        code, body, _, _ = self._run("slot", "list", cwd=repo)
        self.assertEqual(code, 0, body)
        self.assertEqual(os.stat(db).st_mode & 0o777, 0o600)

    def test_dump_write_refuses_a_symlinked_projection(self):
        """Audit C3: `dump --write` truncated and rewrote whatever
        `.omni/control-state.json` pointed at."""
        repo, db = self._repo_with_plane()
        victim = os.path.join(self.foreign, "bashrc")
        with open(victim, "w") as f:
            f.write("keep me\n")
        os.symlink(victim, os.path.join(repo, ".omni", "control-state.json"))
        code, body, _, _ = self._run("--db", db, "dump", "--write")
        self.assertEqual(code, 1, body)
        self.assertEqual(body["error"], "state_file_refused", body)
        with open(victim) as f:
            self.assertEqual(f.read(), "keep me\n")


if __name__ == "__main__":
    unittest.main()
