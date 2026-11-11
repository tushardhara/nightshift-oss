"""Tests for bin/nightshift-workspace.

Placed in tests/ as test_*.py because that -- and only that -- is what the
gated suite runs: tests/run.sh is `unittest discover -s . -p 'test_*.py'` and
.github/workflows/tests.yml runs tests/run.sh on every PR and push to main.
tests/linux/*.sh is NOT discovered by that pattern (#74); an orphaned test
there is why a silent no-op shipped in PR #71.

Hermetic by construction: every repo here is built locally with `git init`, so
the suite needs no network and no credentials. It borrows nothing from
/srv/project -- `--reference` is always passed explicitly, never left to the
tool's auto-detection, so the same assertions hold on a CI runner where
/srv/project does not exist.

The refusal tests are written so they could fail. Each one constructs the real
condition -- an actual linked worktree, an actual staged deletion, an actual
read-only git-dir -- and asserts a specific reason string. A `verify` that
returned 0 unconditionally, or that dropped any single check, turns them red
(WA-008).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, "bin", "nightshift-workspace")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_DENIED = 3

# Identity is fail-closed on this box (#75) and unset on a bare runner, so the
# commits these tests make carry their own rather than relying on ambient git
# config being present.
GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="test",
    GIT_AUTHOR_EMAIL="test@nightshift.local",
    GIT_COMMITTER_NAME="test",
    GIT_COMMITTER_EMAIL="test@nightshift.local",
)


def git(args, cwd=None):
    cmd = ["git"] + (["-C", cwd] if cwd else []) + args
    p = subprocess.run(cmd, capture_output=True, text=True, env=GIT_ENV)
    if p.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


def run_tool(*args):
    """Invoke the tool as a subprocess and return (exit_code, parsed_json).

    Exercised through its real CLI entry point rather than by importing it,
    because the exit code is half of the contract -- ctl's callers branch on
    3-means-denied, and an in-process call would not see it.
    """
    p = subprocess.run(
        [sys.executable, TOOL] + list(args),
        capture_output=True, text=True, env=GIT_ENV,
    )
    payload = json.loads(p.stdout) if p.stdout.strip() else {}
    return p.returncode, payload


class WorkspaceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-workspace-test-")
        self.addCleanup(self._cleanup)
        self.origin = os.path.join(self.tmp, "origin")
        self.root = os.path.join(self.tmp, "workspaces")
        os.makedirs(self.origin)
        git(["init", "--quiet", "--initial-branch=main", "."], cwd=self.origin)
        with open(os.path.join(self.origin, "keeper.txt"), "w") as fh:
            fh.write("first\n")
        git(["add", "keeper.txt"], cwd=self.origin)
        git(["commit", "--quiet", "-m", "first"], cwd=self.origin)
        self.base_sha = git(["rev-parse", "HEAD"], cwd=self.origin)
        with open(os.path.join(self.origin, "second.txt"), "w") as fh:
            fh.write("second\n")
        git(["add", "second.txt"], cwd=self.origin)
        git(["commit", "--quiet", "-m", "second"], cwd=self.origin)
        self.later_sha = git(["rev-parse", "HEAD"], cwd=self.origin)

    def _cleanup(self):
        # A test that made a git-dir read-only would otherwise leave a tree
        # rmtree cannot remove.
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames + filenames:
                target = os.path.join(dirpath, name)
                try:
                    os.chmod(target, os.stat(target).st_mode | stat.S_IWUSR)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self, agent="alpha", sha=None, extra=()):
        code, payload = run_tool(
            "create", "--seat", "eng_codex", "--agent", agent, "--repo", self.origin,
            "--base-sha", sha or self.base_sha, "--root", self.root,
            "--reference", self.origin, *extra
        )
        return code, payload


class TestCreate(WorkspaceTestBase):
    def test_trusted_seats_have_distinct_identities_paths_and_git_dirs(self):
        namespace = __import__("runpy").run_path(TOOL)
        controller = namespace["_load_attested_controller"]()
        seats = (set(controller.COUNTED_AGENTS) |
                 set(controller.EXEMPT_AGENTS) |
                 set(controller.ONCALL_AGENTS))
        identities = [controller.seat_identity(seat) for seat in seats]
        paths = [os.path.join("/home/nightshift/work", seat, "nightshift")
                 for seat in seats]
        git_dirs = [os.path.join(path, ".git") for path in paths]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(len(git_dirs), len(set(git_dirs)))
        self.assertEqual(
            os.path.join("/home/nightshift/work", "eng_codex", "nightshift"),
            paths[list(seats).index("eng_codex")],
        )

    def test_create_sets_repo_local_identity_immediately(self):
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.assertEqual(
            git(["config", "--local", "--get", "user.name"], cwd=payload["path"]),
            "eng_codex",
        )
        self.assertEqual(
            git(["config", "--local", "--get", "user.email"], cwd=payload["path"]),
            "eng_codex@nightshift.seats.invalid",
        )

    def test_unmapped_seat_is_refused_before_creating_root(self):
        root = os.path.join(self.tmp, "not-created")
        code, payload = run_tool(
            "create", "--seat", "not-a-real-seat", "--agent", "nightshift",
            "--repo", self.origin, "--base-sha", self.base_sha, "--root", root,
        )
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertEqual(payload["reason"], "unmapped-seat-identity")
        self.assertFalse(os.path.exists(root))

    @unittest.skipUnless(os.path.isdir("/srv/project/.git"),
                         "/srv/project is not present on this machine")
    def test_srv_project_reference_is_never_opened_for_writing(self):
        trace = os.path.join(self.tmp, "strace.log")
        cmd = [
            "strace", "-f", "-yy", "-s", "4096", "-e",
            "trace=%file,execve", "-o", trace,
            sys.executable, TOOL, "create", "--seat", "eng_codex",
            "--agent", "traced", "--repo", self.origin,
            "--base-sha", self.base_sha, "--root", self.root,
            "--reference", "/srv/project",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, env=GIT_ENV)
        payload = json.loads(p.stdout) if p.stdout.strip() else {}
        self.assertEqual(p.returncode, EXIT_OK, (payload, p.stderr))
        with open(trace, encoding="utf-8") as fh:
            lines = fh.readlines()
        clone_lines = [line for line in lines if "execve(" in line and
                       '"clone"' in line and '"/srv/project"' in line]
        self.assertTrue(clone_lines, "clone argv absent from trace")
        self.assertTrue(any(
            ('"--reference-if-able", "/srv/project"' in line or
             '"--reference", "/srv/project"' in line)
            for line in clone_lines), clone_lines)
        write_flags = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")
        mutations = ("creat(", "rename(", "renameat(", "renameat2(",
                     "unlink(", "unlinkat(", "mkdir(", "mkdirat(",
                     "link(", "linkat(", "symlink(", "symlinkat(")
        violations = []
        for line in lines:
            if "/srv/project" not in line:
                continue
            if any(flag in line for flag in write_flags) or any(
                    call in line for call in mutations):
                violations.append(line.strip())
        self.assertEqual(violations, [], "writes under /srv/project: %r" % violations)

    def test_create_yields_private_git_dir_at_the_requested_sha(self):
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["head_sha"], self.base_sha)
        self.assertTrue(payload["git_dir_private"])
        # The whole point of R-64: the git-dir must live inside the workspace,
        # not back under a shared .git.
        self.assertTrue(
            os.path.realpath(payload["git_dir"]).startswith(
                os.path.realpath(payload["path"]) + os.sep),
            payload,
        )
        self.assertTrue(payload["fetch_usable"])

    def test_create_checks_out_the_explicit_sha_not_the_default_branch(self):
        # base_sha is the FIRST commit; the clone source's HEAD is the second.
        # If create resolved a ref instead of the SHA it was handed, this is
        # the assertion that catches it.
        code, payload = self.create(sha=self.base_sha)
        self.assertEqual(code, EXIT_OK, payload)
        self.assertEqual(payload["head_sha"], self.base_sha)
        self.assertNotEqual(payload["head_sha"], self.later_sha)

    def test_create_borrows_the_reference_object_store(self):
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.assertTrue(payload["reference_used"])
        self.assertTrue(payload["alternates"], "expected an alternates entry")
        self.assertTrue(payload["reference_gc_coupled"])

    def test_dissociate_drops_the_dependency_on_the_reference(self):
        code, payload = self.create(extra=("--dissociate",))
        self.assertEqual(code, EXIT_OK, payload)
        self.assertEqual(payload["alternates"], [])
        self.assertFalse(payload["reference_gc_coupled"])

    def test_create_refuses_an_occupied_workspace(self):
        code, _ = self.create()
        self.assertEqual(code, EXIT_OK)
        code, payload = self.create()
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertEqual(payload["reason"], "workspace-exists")

    def test_create_reports_an_unknown_base_sha_rather_than_landing_elsewhere(self):
        code, payload = self.create(sha="0" * 40)
        self.assertEqual(code, EXIT_ERROR, payload)
        self.assertEqual(payload["reason"], "base-sha-checkout-failed")


class TestVerify(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.ws = payload["path"]

    def test_verify_accepts_a_fresh_workspace(self):
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reasons"], [])

    def test_verify_refuses_a_linked_worktree(self):
        """The R-64 control: a linked worktree must NOT pass as a workspace.

        This is the arrangement #101 is about -- rev-parse --absolute-git-dir
        resolves back under the parent repo's .git, so FETCH_HEAD lands in a
        directory a sandboxed seat cannot write. If the git-dir-not-private
        check were removed or softened, this test goes green wrongly, which is
        exactly what it exists to prevent.
        """
        linked = os.path.join(self.tmp, "linked")
        git(["worktree", "add", "--detach", linked, self.base_sha], cwd=self.origin)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", self.origin, "worktree", "remove", "--force", linked],
            capture_output=True, env=GIT_ENV))

        code, payload = run_tool("verify", "--path", linked, "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("git-dir-not-private", payload["reasons"])
        self.assertFalse(payload["git_dir_private"])
        # And the counterfactual, in the same test: the private clone built
        # from the same commit passes the very check the worktree fails.
        code, ok_payload = run_tool("verify", "--path", self.ws, "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, ok_payload)
        self.assertTrue(ok_payload["git_dir_private"])

    def test_verify_refuses_a_dirty_working_tree(self):
        with open(os.path.join(self.ws, "keeper.txt"), "a") as fh:
            fh.write("scribble\n")
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("worktree-dirty", payload["reasons"])

    def test_verify_refuses_an_index_armed_with_a_staged_deletion(self):
        """The #89/#98 shape, which is the one that destroyed ~880 lines.

        The working tree looks unremarkable; it is the INDEX that is armed. A
        check that only looked at unstaged changes would call this clean and
        let the next commit carry the deletion.
        """
        git(["rm", "--quiet", "keeper.txt"], cwd=self.ws)
        staged = git(["diff", "--cached", "--name-status"], cwd=self.ws)
        self.assertTrue(staged.startswith("D"), staged)

        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("worktree-dirty", payload["reasons"])
        self.assertTrue(any(ln.startswith("D") for ln in payload["dirty_entries"]),
                        payload["dirty_entries"])

    def test_verify_refuses_a_stale_head(self):
        git(["checkout", "--quiet", "--detach", self.later_sha], cwd=self.ws)
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("sha-mismatch", payload["reasons"])
        self.assertEqual(payload["head_sha"], self.later_sha)

    def test_verify_accepts_a_matching_head(self):
        # The other half of the stale-head pair: --expect-sha must be capable
        # of passing, or the test above would prove nothing.
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, payload)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses the permission bits this test relies on")
    def test_verify_refuses_a_git_dir_it_cannot_write(self):
        """The #101 predicate, reproduced rather than asserted.

        A read-only git-dir is how a sandboxed seat sees /srv/project/.git. The
        tool probes by attempting a write, not by reading permission bits, so
        this test is what proves the probe reports the truth.
        """
        git_dir = os.path.join(self.ws, ".git")
        before, payload_before = run_tool("verify", "--path", self.ws,
                                          "--expect-sha", self.base_sha)
        self.assertEqual(before, EXIT_OK, payload_before)
        self.assertTrue(payload_before["fetch_usable"])

        mode = os.stat(git_dir).st_mode
        os.chmod(git_dir, mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        self.addCleanup(os.chmod, git_dir, mode)

        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("fetch-head-not-writable", payload["reasons"])
        self.assertFalse(payload["fetch_usable"])

    def test_verify_refuses_a_detached_head_when_a_branch_is_required(self):
        code, payload = run_tool("verify", "--path", self.ws, "--expect-sha", self.base_sha,
                                 "--require-branch")
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("head-detached", payload["reasons"])
        self.assertIsNone(payload["head_branch"])

    def test_verify_refuses_the_wrong_branch(self):
        """The #65 shape: the checkout sitting on somebody else's branch."""
        git(["checkout", "--quiet", "-b", "someone-elses-pr"], cwd=self.ws)
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha,
                                 "--require-branch", "platform/w4-agent-workspace")
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("branch-mismatch", payload["reasons"])
        self.assertEqual(payload["head_branch"], "someone-elses-pr")

    def test_verify_accepts_the_required_branch(self):
        git(["checkout", "--quiet", "-b", "platform/w4-agent-workspace"], cwd=self.ws)
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha,
                                 "--require-branch", "platform/w4-agent-workspace")
        self.assertEqual(code, EXIT_OK, payload)
        self.assertEqual(payload["head_branch"], "platform/w4-agent-workspace")

    def test_verify_reports_every_failing_check_not_just_the_first(self):
        # A caller fixing one problem at a time would otherwise round-trip once
        # per defect.
        git(["checkout", "--quiet", "--detach", self.later_sha], cwd=self.ws)
        with open(os.path.join(self.ws, "keeper.txt"), "a") as fh:
            fh.write("scribble\n")
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("worktree-dirty", payload["reasons"])
        self.assertIn("sha-mismatch", payload["reasons"])
        self.assertEqual(payload["reason"], payload["reasons"][0])

    def test_verify_refuses_a_path_that_is_not_a_worktree(self):
        plain = os.path.join(self.tmp, "not-a-repo")
        os.makedirs(plain)
        code, payload = run_tool("verify", "--path", plain,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertEqual(payload["reason"], "not-a-git-worktree")


class TestPlantedSubmoduleFsmonitor(WorkspaceTestBase):
    """Audit C2: the seat populates a gitlink with a repository whose config
    names an fsmonitor command. `verify`/`recheck` run git in that workspace
    from outside the sandbox, so they must never run it -- and must not call
    the workspace clean."""

    def setUp(self):
        super().setUp()
        lib = os.path.join(self.tmp, "lib")
        os.makedirs(lib)
        git(["init", "--quiet", "--initial-branch=main", "."], cwd=lib)
        with open(os.path.join(lib, "lib.txt"), "w") as fh:
            fh.write("lib\n")
        git(["add", "lib.txt"], cwd=lib)
        git(["commit", "--quiet", "-m", "lib"], cwd=lib)
        lib_sha = git(["rev-parse", "HEAD"], cwd=lib)
        git(["update-index", "--add", "--cacheinfo",
             "160000,%s,vendor/x" % lib_sha], cwd=self.origin)
        git(["commit", "--quiet", "-m", "gitlink"], cwd=self.origin)
        self.sha = git(["rev-parse", "HEAD"], cwd=self.origin)
        code, payload = self.create(sha=self.sha)
        self.assertEqual(code, EXIT_OK, payload)
        self.ws = payload["path"]
        nested = os.path.join(self.ws, "vendor", "x")
        if os.path.isdir(nested):
            os.rmdir(nested)
        git(["clone", "--quiet", lib, nested])
        self.marker = os.path.join(self.tmp, "fsmonitor-ran")
        payload_path = os.path.join(self.tmp, "payload")
        with open(payload_path, "w") as fh:
            fh.write("#!/bin/sh\n: > %s\nexit 1\n" % self.marker)
        os.chmod(payload_path, 0o755)
        git(["config", "core.fsmonitor", payload_path], cwd=nested)
        with open(os.path.join(nested, "lib.txt"), "w") as fh:
            fh.write("dirty\n")

    def test_plant_is_live_for_plain_git(self):
        git(["status", "--porcelain"], cwd=self.ws)
        self.assertTrue(os.path.exists(self.marker),
                        "the fixture never armed fsmonitor; the test below "
                        "would pass vacuously")

    def test_verify_and_recheck_never_run_it_and_refuse(self):
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("worktree-dirty", payload["reasons"])
        self.assertIn("populated-submodule vendor/x", payload["dirty_entries"])
        run_tool("recheck", "--path", self.ws,
                 "--fingerprint", payload["fingerprint"])
        self.assertFalse(os.path.exists(self.marker))


class TestCliContract(WorkspaceTestBase):
    def test_missing_required_argument_is_a_usage_error(self):
        # 2 is ctl's usage code; a caller distinguishes "I called this wrong"
        # from "the workspace was refused".
        p = subprocess.run([sys.executable, TOOL, "verify"],
                           capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(p.returncode, EXIT_USAGE)

    def test_no_subcommand_is_a_usage_error(self):
        p = subprocess.run([sys.executable, TOOL],
                           capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(p.returncode, EXIT_USAGE)


class TestIsolationIsAPositiveClaim(WorkspaceTestBase):
    """Finding 1: owning a private .git does not make a checkout isolated.

    /srv/project is itself a clone with a private .git. The guard exists to
    refuse exactly that object, so "private git-dir" cannot be the whole
    predicate -- isolation has to be a claim established at create time that a
    generic shared checkout cannot satisfy by existing.
    """

    def setUp(self):
        super().setUp()
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.ws = payload["path"]

    def _shared_primary_clone(self):
        """A fixture shaped like /srv/project: an ordinary clone, own .git,
        clean tree, that no one created as a per-agent workspace."""
        shared = os.path.join(self.tmp, "shared-primary")
        git(["clone", "--quiet", self.origin, shared])
        git(["checkout", "--quiet", "--detach", self.base_sha], cwd=shared)
        return shared

    def test_verify_refuses_a_shared_primary_clone(self):
        shared = self._shared_primary_clone()
        # It satisfies every check the previous revision had: private git-dir,
        # writable, clean, and at the SHA the caller declares.
        self.assertTrue(os.path.isdir(os.path.join(shared, ".git")))
        self.assertEqual(git(["status", "--porcelain"], cwd=shared), "")
        self.assertEqual(git(["rev-parse", "HEAD"], cwd=shared), self.base_sha)

        code, payload = run_tool("verify", "--path", shared,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("not-a-nightshift-workspace", payload["reasons"])
        self.assertFalse(payload["is_nightshift_workspace"])
        # The counterfactual in the same test: a real workspace, same commit,
        # same cleanliness, passes.
        ok_code, ok_payload = run_tool("verify", "--path", self.ws,
                                       "--expect-sha", self.base_sha)
        self.assertEqual(ok_code, EXIT_OK, ok_payload)
        self.assertTrue(ok_payload["is_nightshift_workspace"])

    def test_verify_refuses_a_marker_that_describes_another_path(self):
        """A copied or moved workspace carries a marker that is no longer
        evidence of anything, so the marker must be pinned to its location."""
        moved = os.path.join(self.tmp, "moved")
        shutil.copytree(self.ws, moved, symlinks=True)
        code, payload = run_tool("verify", "--path", moved,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("workspace-marker-path-mismatch", payload["reasons"])

    def test_marker_is_invisible_to_git(self):
        # If the marker landed in the working tree it would show up as an
        # untracked file and make every workspace permanently "dirty".
        self.assertTrue(os.path.exists(
            os.path.join(self.ws, ".git", "nightshift-workspace.json")))
        self.assertEqual(git(["status", "--porcelain"], cwd=self.ws), "")

    @unittest.skipUnless(os.path.isdir("/srv/project/.git"),
                         "/srv/project is not present on this machine")
    def test_verify_refuses_the_real_shared_checkout(self):
        """The subject named in the review, run against the real object.

        Pinned to whatever /srv/project's HEAD is right now, so the refusal
        cannot come from staleness -- it has to come from isolation.
        """
        sha = git(["rev-parse", "HEAD"], cwd="/srv/project")
        code, payload = run_tool("verify", "--path", "/srv/project",
                                 "--expect-sha", sha)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn("not-a-nightshift-workspace", payload["reasons"])


class TestExpectShaIsMandatory(WorkspaceTestBase):
    """Finding 2: a safety check that is off unless asked for is off."""

    def setUp(self):
        super().setUp()
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.ws = payload["path"]

    def test_verify_without_expect_sha_is_a_usage_error(self):
        p = subprocess.run([sys.executable, TOOL, "verify", "--path", self.ws],
                           capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(p.returncode, EXIT_USAGE, p.stderr)

    def test_stale_head_cannot_pass_by_omitting_the_sha(self):
        # The exact reproduction from the review: create at A, detach at B,
        # then try to get a passing verdict without naming a SHA.
        git(["checkout", "--quiet", "--detach", self.later_sha], cwd=self.ws)
        p = subprocess.run([sys.executable, TOOL, "verify", "--path", self.ws],
                           capture_output=True, text=True, env=GIT_ENV)
        self.assertNotEqual(p.returncode, EXIT_OK, p.stdout)


class TestCheckActionRace(WorkspaceTestBase):
    """Finding 3: the verdict is a snapshot. This does not close the race.

    These tests pin the narrowing that IS implemented -- a fingerprint the
    caller presents back at the action boundary -- and pin the honesty of the
    contract, so a later change cannot quietly upgrade "detected" to "atomic".
    """

    def setUp(self):
        super().setUp()
        code, payload = self.create()
        self.assertEqual(code, EXIT_OK, payload)
        self.ws = payload["path"]

    def test_verdict_states_it_is_not_authorization(self):
        code, payload = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, payload)
        self.assertTrue(payload["verdict_is_not_authorization"])
        self.assertTrue(payload["fingerprint"])

    def test_recheck_detects_an_index_armed_after_the_verdict(self):
        """Verdict passes, another writer arms the index, then the caller acts.

        Without the recheck this is exit 0 followed by a commit carrying a
        deletion nobody asked for -- the #89/#98 sequence.
        """
        code, verdict = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, verdict)

        git(["rm", "--quiet", "keeper.txt"], cwd=self.ws)   # the other writer

        code, payload = run_tool("recheck", "--path", self.ws,
                                 "--fingerprint", verdict["fingerprint"])
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertEqual(payload["reason"], "fingerprint-changed")
        self.assertNotEqual(payload["fingerprint"], verdict["fingerprint"])

    def test_recheck_accepts_an_untouched_workspace(self):
        # The other half: recheck must be capable of passing, or the test
        # above would prove nothing.
        code, verdict = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, verdict)
        code, payload = run_tool("recheck", "--path", self.ws,
                                 "--fingerprint", verdict["fingerprint"])
        self.assertEqual(code, EXIT_OK, payload)

    def test_recheck_detects_a_head_move_after_the_verdict(self):
        code, verdict = run_tool("verify", "--path", self.ws,
                                 "--expect-sha", self.base_sha)
        self.assertEqual(code, EXIT_OK, verdict)
        git(["checkout", "--quiet", "--detach", self.later_sha], cwd=self.ws)
        code, payload = run_tool("recheck", "--path", self.ws,
                                 "--fingerprint", verdict["fingerprint"])
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertEqual(payload["reason"], "fingerprint-changed")

    def test_the_documented_limitation_is_actually_documented(self):
        """The gap is stated in the tool, not only in a PR comment.

        A PR comment is not shipped with the code; the person who reads
        --help or the source a month from now is the one who must not be
        misled.
        """
        source = open(TOOL).read()
        self.assertIn("A successful verdict is NOT authorization to write.", source)


class TestAgentNameContainment(WorkspaceTestBase):
    """Finding 4: --agent becomes a directory, so it must not escape --root."""

    def _create_agent(self, agent):
        return run_tool("create", "--seat", "eng_codex", "--agent", agent, "--repo", self.origin,
                        "--base-sha", self.base_sha, "--root", self.root,
                        "--reference", self.origin)

    def test_create_refuses_a_traversing_agent_name(self):
        outside = os.path.join(self.tmp, "escape")
        code, payload = self._create_agent("../escape")
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn(payload["reason"], ("invalid-agent-name", "workspace-escapes-root"))
        # The refusal has to happen BEFORE anything is written.
        self.assertFalse(os.path.exists(outside), "clone escaped the root")

    def test_create_refuses_a_nested_agent_name(self):
        code, payload = self._create_agent("team/alpha")
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertEqual(payload["reason"], "invalid-agent-name")

    def test_create_refuses_an_absolute_agent_name(self):
        target = os.path.join(self.tmp, "absolute-escape")
        code, payload = self._create_agent(target)
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertFalse(os.path.exists(target), "clone escaped the root")

    def test_create_refuses_dot_names(self):
        for name in (".", "..", ""):
            code, payload = self._create_agent(name)
            self.assertEqual(code, EXIT_DENIED, payload)
            self.assertEqual(payload["reason"], "invalid-agent-name")

    def test_ordinary_agent_names_still_work(self):
        # Containment must not be enforced by refusing everything.
        for name in ("platform", "w4-agent", "agent.2", "slot_113"):
            code, payload = self._create_agent(name)
            self.assertEqual(code, EXIT_OK, payload)
            self.assertEqual(
                os.path.dirname(os.path.realpath(payload["path"])),
                os.path.realpath(self.root))


    def test_create_refuses_a_pre_existing_symlink_at_the_workspace_path(self):
        """architect's reproduction: the NAME is clean, the PATH is not.

        `<root>/<agent>` already exists as a symlink to an empty directory
        outside the root. The name has no `..` and no `/`, so lexical
        normalisation sees nothing wrong; git follows the link and writes .git
        outside the declared root. Only resolving the path AS IT EXISTS ON
        DISK catches this.
        """
        outside = os.path.join(self.tmp, "outside-the-root")
        os.makedirs(outside)
        os.makedirs(self.root, exist_ok=True)
        link = os.path.join(self.root, "alpha")
        os.symlink(outside, link)

        code, payload = self._create_agent("alpha")
        self.assertEqual(code, EXIT_DENIED, payload)
        self.assertIn(payload["reason"],
                      ("workspace-path-is-symlink", "workspace-escapes-root"))
        # The property that actually matters: nothing was written outside.
        self.assertFalse(os.path.exists(os.path.join(outside, ".git")),
                         "git wrote outside the declared root")
        self.assertEqual(os.listdir(outside), [])

    def test_create_refuses_a_symlink_even_when_it_points_inside_the_root(self):
        # A workspace path that is already a symlink has no legitimate use
        # here, and refusing it outright is simpler to reason about than
        # following it safely.
        inside = os.path.join(self.root, "real-dir")
        os.makedirs(inside)
        os.symlink(inside, os.path.join(self.root, "alpha"))
        code, payload = self._create_agent("alpha")
        self.assertEqual(code, EXIT_DENIED, payload)

    def test_create_claims_the_workspace_path_itself(self):
        # The clone must land on the path the tool resolved, not somewhere a
        # link redirected it to.
        code, payload = self._create_agent("alpha")
        self.assertEqual(code, EXIT_OK, payload)
        self.assertFalse(os.path.islink(payload["path"]))
        self.assertEqual(os.path.realpath(os.path.dirname(payload["path"])),
                         os.path.realpath(self.root))
        self.assertTrue(os.path.isdir(os.path.join(payload["path"], ".git")))


class TestWorkspacePathSwappedAfterCreate(WorkspaceTestBase):
    """architect's reproduction at 9aef482: mkdir, swap, clone.

    `mkdir` is atomic, but the directory ENTRY stays replaceable. Between the
    mkdir that claims the name and the pathname-based `git clone` that uses it,
    the empty directory can be removed and replaced with an outside-pointing
    symlink; git follows it. The atomicity argument covered the instant of
    creation, not the whole operation.

    The swap is driven by a `git` shim on PATH rather than by a hook in the
    tool, so no test-only surface exists in the shipped code, and it fires
    deterministically in the exact window rather than racing for it.
    """

    def _shim(self, victim, target):
        """A `git` that swaps `victim` for a symlink to `target`, once, on the
        first clone -- then execs the real git."""
        shim_dir = os.path.join(self.tmp, "shim")
        os.makedirs(shim_dir, exist_ok=True)
        real_git = subprocess.run(["which", "git"], capture_output=True,
                                  text=True).stdout.strip()
        guard = os.path.join(self.tmp, "swapped")
        shim = os.path.join(shim_dir, "git")
        with open(shim, "w") as fh:
            fh.write(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = "clone" ] && [ ! -e "%s" ]; then\n'
                '  touch "%s"\n'
                '  rm -rf "%s"\n'
                '  ln -s "%s" "%s"\n'
                "fi\n"
                'exec %s "$@"\n' % (guard, guard, victim, target, victim, real_git)
            )
        os.chmod(shim, 0o755)
        return shim_dir

    def test_the_clone_cannot_be_redirected_by_a_post_mkdir_swap(self):
        outside = os.path.join(self.tmp, "outside-the-root")
        os.makedirs(outside)
        os.makedirs(self.root, exist_ok=True)
        victim = os.path.join(self.root, "alpha")

        env = dict(GIT_ENV, PATH=self._shim(victim, outside) + os.pathsep
                   + GIT_ENV["PATH"])
        p = subprocess.run(
            [sys.executable, TOOL, "create", "--seat", "eng_codex", "--agent", "alpha",
             "--repo", self.origin, "--base-sha", self.base_sha,
             "--root", self.root, "--reference", self.origin],
            capture_output=True, text=True, env=env,
        )
        payload = json.loads(p.stdout) if p.stdout.strip() else {}

        # The swap must have actually happened, or this test proves nothing.
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "swapped")),
                        "the shim never fired; the window was not exercised")

        # THE property: nothing git wrote may land outside the declared root.
        self.assertFalse(
            os.path.exists(os.path.join(outside, ".git")),
            "git wrote .git outside --root: %s" % (payload,))
        self.assertFalse(
            os.path.exists(os.path.join(outside, ".git",
                                        "nightshift-workspace.json")),
            "the marker landed outside --root: %s" % (payload,))
        self.assertEqual(os.listdir(outside), [], payload)

        # And the caller must not be handed a success it cannot rely on.
        self.assertNotEqual(p.returncode, EXIT_OK, payload)

    def test_a_swapped_path_is_refused_rather_than_reported_as_ok(self):
        outside = os.path.join(self.tmp, "outside-the-root")
        os.makedirs(outside)
        os.makedirs(self.root, exist_ok=True)
        victim = os.path.join(self.root, "alpha")
        env = dict(GIT_ENV, PATH=self._shim(victim, outside) + os.pathsep
                   + GIT_ENV["PATH"])
        p = subprocess.run(
            [sys.executable, TOOL, "create", "--seat", "eng_codex", "--agent", "alpha",
             "--repo", self.origin, "--base-sha", self.base_sha,
             "--root", self.root, "--reference", self.origin],
            capture_output=True, text=True, env=env,
        )
        payload = json.loads(p.stdout) if p.stdout.strip() else {}
        self.assertEqual(p.returncode, EXIT_DENIED, payload)
        self.assertEqual(payload.get("reason"), "workspace-path-swapped", payload)

    def _late_shim(self, victim, target, trigger):
        """A `git` that swaps `victim` AFTER the clone has succeeded.

        The directory is MOVED aside rather than deleted, so the descriptor the
        tool holds still points at a live directory with a real .git in it.
        That is what makes this fixture reach the marker write: the swap in the
        earlier test kills the clone, so execution never gets that far, and the
        marker's protection would otherwise be verified only by reading the
        code (WA-012).
        """
        shim_dir = os.path.join(self.tmp, "late-shim")
        os.makedirs(shim_dir, exist_ok=True)
        real_git = subprocess.run(["which", "git"], capture_output=True,
                                  text=True).stdout.strip()
        guard = os.path.join(self.tmp, "late-swapped")
        moved = os.path.join(self.tmp, "moved-aside")
        shim = os.path.join(shim_dir, "git")
        with open(shim, "w") as fh:
            fh.write(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = "%s" ] && [ ! -e "%s" ]; then\n'
                '  touch "%s"\n'
                '  mv "%s" "%s"\n'
                '  ln -s "%s" "%s"\n'
                "fi\n"
                'exec %s "$@"\n'
                % (trigger, guard, guard, victim, moved, target, victim, real_git)
            )
        os.chmod(shim, 0o755)
        return shim_dir, guard, moved

    def test_the_marker_cannot_be_redirected_by_a_post_clone_swap(self):
        """The marker is as security-relevant as the clone.

        At 9aef482 it landed outside --root alongside .git, and `verify` would
        then have accepted the escaped directory as a genuine workspace. This
        pins that the marker follows the descriptor, not the pathname.
        """
        outside = os.path.join(self.tmp, "outside-the-root")
        os.makedirs(outside)
        os.makedirs(self.root, exist_ok=True)
        victim = os.path.join(self.root, "alpha")
        shim_dir, guard, moved = self._late_shim(victim, outside, "rev-parse")

        env = dict(GIT_ENV, PATH=shim_dir + os.pathsep + GIT_ENV["PATH"])
        p = subprocess.run(
            [sys.executable, TOOL, "create", "--seat", "eng_codex", "--agent", "alpha",
             "--repo", self.origin, "--base-sha", self.base_sha,
             "--root", self.root, "--reference", self.origin],
            capture_output=True, text=True, env=env,
        )
        payload = json.loads(p.stdout) if p.stdout.strip() else {}

        self.assertTrue(os.path.exists(guard),
                        "the late shim never fired; the window was not exercised")
        # The clone must have SUCCEEDED before the swap, or this fixture is
        # just the earlier test again and proves nothing about the marker.
        self.assertTrue(os.path.isdir(os.path.join(moved, ".git")),
                        "the clone did not complete before the swap")

        marker_outside = os.path.join(outside, ".git", "nightshift-workspace.json")
        self.assertFalse(os.path.exists(marker_outside),
                         "the marker was written outside --root: %s" % (payload,))
        self.assertEqual(os.listdir(outside), [], payload)
        self.assertNotEqual(p.returncode, EXIT_OK, payload)

    def _git_dir_swap_shim(self, workspace, target, match):
        """A `git` that re-points the workspace's `.git` AFTER a chosen call.

        `create`'s final git invocation is `rev-parse HEAD`; everything after
        it -- the marker write, the identity assertion, the FETCH_HEAD
        writability probe -- is pure Python. So the shim runs the real git
        first and swaps only once the matched call has returned, which lands
        the swap in exactly the window the earlier fixtures cannot reach.

        The real `.git` is MOVED aside rather than deleted, so a tool that
        holds it by descriptor still has a live git dir to write into, and the
        move is itself the evidence that the clone completed before the swap.
        """
        shim_dir = os.path.join(self.tmp, "gitdir-shim")
        os.makedirs(shim_dir, exist_ok=True)
        real_git = subprocess.run(["which", "git"], capture_output=True,
                                  text=True).stdout.strip()
        guard = os.path.join(self.tmp, "gitdir-swapped")
        moved = os.path.join(self.tmp, "gitdir-moved-aside")
        git_dir = os.path.join(workspace, ".git")
        shim = os.path.join(shim_dir, "git")
        with open(shim, "w") as fh:
            fh.write(
                "#!/usr/bin/env bash\n"
                'if %s && [ ! -e "%s" ]; then\n'
                '  %s "$@"\n'
                "  st=$?\n"
                '  touch "%s"\n'
                '  mv "%s" "%s"\n'
                '  ln -s "%s" "%s"\n'
                "  exit $st\n"
                "fi\n"
                'exec %s "$@"\n'
                % (match, guard, real_git, guard, git_dir, moved, target,
                   git_dir, real_git)
            )
        os.chmod(shim, 0o755)
        return shim_dir, guard, moved

    def _create_under_git_dir_swap(self, match=None):
        """Run `create` with the workspace's `.git` swapped for a symlink that
        points outside `--root`, and seed the target with a sentinel named
        exactly what the writability probe writes."""
        match = match or '[ "$1" = "rev-parse" ] && [ "$2" = "HEAD" ]'
        outside = os.path.join(self.tmp, "outside-the-root")
        os.makedirs(outside)
        os.makedirs(self.root, exist_ok=True)
        workspace = os.path.join(self.root, "alpha")
        sentinel = os.path.join(outside, ".nightshift-write-probe")
        with open(sentinel, "w") as fh:
            fh.write("sentinel")
        shim_dir, guard, moved = self._git_dir_swap_shim(workspace, outside,
                                                          match)

        env = dict(GIT_ENV, PATH=shim_dir + os.pathsep + GIT_ENV["PATH"])
        p = subprocess.run(
            [sys.executable, TOOL, "create", "--seat", "eng_codex", "--agent", "alpha",
             "--repo", self.origin, "--base-sha", self.base_sha,
             "--root", self.root, "--reference", self.origin],
            capture_output=True, text=True, env=env,
        )
        payload = json.loads(p.stdout) if p.stdout.strip() else {}

        # Both halves of the fixture must have happened, or it proves nothing:
        # the swap fired, and it fired AFTER a completed clone.
        self.assertTrue(os.path.exists(guard),
                        "the git-dir shim never fired; the window was not "
                        "exercised: %s" % (p.stderr,))
        self.assertTrue(os.path.isfile(os.path.join(moved, "HEAD")),
                        "the clone did not complete before the swap")
        return p, payload, outside, sentinel

    def test_the_write_probe_cannot_be_redirected_by_a_git_dir_swap(self):
        """The FETCH_HEAD writability probe must obey the same identity rule
        as the clone, the checkout and the marker.

        At 313f69b it did not: `create` closed the workspace descriptor and
        then called `fetch_head_writable(git_dir)`, which re-derived its target
        from a path string. Re-pointing `<workspace>/.git` therefore redirected
        the probe -- and the probe does not merely create a file, it TRUNCATES
        whatever already carries that name and then UNLINKS it. That is a write
        and a delete outside `--root`, in the one window the descriptor was
        adopted to close.

        The sentinel is the evidence. A probe that ran in `outside` destroys
        it; a probe that ran through the held descriptor cannot touch it.
        """
        p, payload, outside, sentinel = self._create_under_git_dir_swap()

        self.assertTrue(
            os.path.exists(sentinel),
            "the writability probe unlinked a file outside --root: %s"
            % (payload,))
        with open(sentinel) as fh:
            self.assertEqual(
                fh.read(), "sentinel",
                "the writability probe wrote outside --root: %s" % (payload,))
        # The marker reaches its git dir through the same `.git` component, so
        # it must not have followed the swap either.
        self.assertFalse(
            os.path.exists(os.path.join(outside, "nightshift-workspace.json")),
            "the marker was written outside --root: %s" % (payload,))
        # Nothing at all may have been added outside the root.
        self.assertEqual(sorted(os.listdir(outside)),
                         [".nightshift-write-probe"], payload)
        self.assertNotEqual(p.returncode, EXIT_OK, payload)

    def test_a_git_dir_that_is_a_link_when_claimed_is_refused_as_such(self):
        """The other end of the same window: swapped BEFORE the git dir is
        claimed rather than after.

        Here the shim fires the instant the clone returns, so `create` never
        held the real `.git` at all. Claiming it with O_NOFOLLOW is what turns
        that into a named refusal; without it the tool would adopt whatever the
        link points at as its git dir and only stumble later, on a checkout
        failure that names the wrong cause. Asserting the reason -- not merely
        a non-zero exit -- is what pins the flag.
        """
        p, payload, outside, sentinel = self._create_under_git_dir_swap(
            match='[ "$1" = "clone" ]')
        self.assertEqual(p.returncode, EXIT_DENIED, payload)
        self.assertEqual(payload.get("reason"), "git-dir-not-private", payload)
        self.assertEqual(sorted(os.listdir(outside)),
                         [".nightshift-write-probe"], payload)

    def test_a_swapped_git_dir_is_refused_rather_than_reported_as_ok(self):
        """And the caller is told which refusal it is, not handed a success
        whose `git_dir` and `fetch_usable` describe somewhere else."""
        p, payload, _outside, _sentinel = self._create_under_git_dir_swap()
        self.assertEqual(p.returncode, EXIT_DENIED, payload)
        self.assertEqual(payload.get("reason"), "workspace-path-swapped",
                         payload)

class TestFailedCreateCleansUp(WorkspaceTestBase):
    def test_a_failed_clone_does_not_block_the_retry(self):
        """A half-built directory turns the real error into `workspace-exists`
        on every subsequent attempt (review, non-blocking note)."""
        code, payload = run_tool("create", "--seat", "eng_codex", "--agent", "alpha",
                                 "--repo", os.path.join(self.tmp, "no-such-repo"),
                                 "--base-sha", self.base_sha, "--root", self.root)
        self.assertEqual(code, EXIT_ERROR, payload)
        self.assertEqual(payload["reason"], "clone-failed")
        self.assertEqual(payload["agent"], "alpha")

        code, payload = self.create(agent="alpha")
        self.assertEqual(code, EXIT_OK, payload)


if __name__ == "__main__":
    unittest.main()
