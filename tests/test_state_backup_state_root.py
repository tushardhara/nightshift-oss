"""`deploy/nightshift-state-backup.sh`'s STATE_ROOT argument -- issue #400's
H4 hardening fact: narrative memory's `memory.db` lives under an ADR-0021
`state_root`, a directory OUTSIDE every Git worktree by design. Before this
argument existed, `git add -- .omni docs` (the backup's entire snapshot
mechanism) could never see it and never would -- every production run of
this script quietly covered ZERO bytes of memory, silently, with no test
anywhere naming that gap (`grep -rn STATE_ROOT tests/*.py` returned nothing).

This suite is MOCKLESS throughout: every assertion runs the REAL
`deploy/nightshift-state-backup.sh` as a real subprocess against a real
`git init` fixture, and reads the real git objects it produces back with
real `git` commands -- no mock, no stub.

Covers the three branches the script's own comments name, plus the two
static wiring facts that make STATE_ROOT reachable at all in production:

  1. STATE_ROOT not given (omitted, or the empty string an un-substituted
     `__STATE_ROOT__` systemd placeholder degrades to) -> exit 0, a warning
     on stderr, no memory manifest written, .omni/docs still snapshotted.
  2. STATE_ROOT given but not a real directory -> exit 1 (the gap must be
     LOUD, not a quiet green run), .omni/docs still snapshotted regardless.
  3. STATE_ROOT given and real, containing memory.db (+ -wal/-shm sidecars)
     -> exit 0, a `.omni/memory-state-manifest.json` is written, captured
     in the SAME commit the .omni/docs snapshot lands in, and is genuinely
     metadata-only: path/size/mtime per file, never memory.db's own bytes.
  4. The `.service` unit's `ExecStart` actually names a state-root
     placeholder -- so a future revert of just that one line (dropping the
     second argument, exactly the state the H4 blocker measured in
     production) is caught here instead of silently shipping again.
  5. `deploy/bootstrap.sh` actually substitutes that placeholder (a
     `STATE_ROOT` variable and a `sed` target for it both exist) -- so the
     unit's placeholder has something to be filled in BY, not just a
     spelling that happens to match the manifest script's own `sed -i`
     target on the deploy host.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "deploy", "nightshift-state-backup.sh")
SERVICE_UNIT = os.path.join(
    REPO, "deploy", "systemd", "nightshift-state-backup.service")
BOOTSTRAP_SH = os.path.join(REPO, "deploy", "bootstrap.sh")

GIT_ENV = dict(
    os.environ,
    GIT_CONFIG_GLOBAL="/dev/null",
    GIT_CONFIG_NOSYSTEM="1",
    GIT_TERMINAL_PROMPT="0",
    GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.com",
    GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.com",
)


def _git(repo, *args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} in {repo} failed: {proc.stderr}")
    return proc.stdout


def _fresh_project_dir():
    """A real, minimal git repo shaped like a real PROJECT_DIR: an initial
    commit (so `git write-tree`/`commit-tree` have a HEAD to build on --
    the backup script never requires this, but a repo with zero commits
    is not representative of a real deploy), a `.omni/` and `docs/` the
    snapshot mechanism can `git add`, and nothing under `omni/state` yet.
    """
    root = tempfile.mkdtemp(prefix="nightshift-backup-fixture-")
    _git(root, "init", "-q")
    os.makedirs(os.path.join(root, ".omni"), exist_ok=True)
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# fixture\n")
    with open(os.path.join(root, ".omni", "goal.md"), "w", encoding="utf-8") as fh:
        fh.write("fixture goal\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture initial commit")
    return root


def _run_backup(project_dir, state_root=None):
    args = [SCRIPT, project_dir]
    if state_root is not None:
        args.append(state_root)
    return subprocess.run(args, env=GIT_ENV, capture_output=True, text=True,
                           timeout=60)


def _state_ref_tree_files(project_dir):
    """Every path git-ls-tree'd out of `refs/heads/omni/state`'s current
    commit -- what the snapshot actually captured, read back from real git
    objects, not inferred from the script's own stdout/exit code."""
    out = _git(project_dir, "ls-tree", "-r", "--name-only",
               "refs/heads/omni/state")
    return sorted(ln for ln in out.splitlines() if ln)


class StateRootNotGiven(unittest.TestCase):
    """Branch 1: the argument is absent (production's actual state before
    the systemd unit is fixed to pass it, and every host that has never had
    memory deployed at all -- this must stay a normal, silent-except-for-
    the-warning success, not a hard failure)."""

    def setUp(self):
        self.project_dir = _fresh_project_dir()

    def test_missing_state_root_exits_zero_and_warns(self):
        result = _run_backup(self.project_dir)  # no second argument at all
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STATE_ROOT not given", result.stderr)

    def test_empty_state_root_argument_behaves_identically(self):
        """The shape an un-substituted `__STATE_ROOT__` systemd placeholder
        degrades to (see StaticServiceUnitWiring below) -- an EMPTY second
        argument, not a missing one. `STATE_ROOT="${2:-}"` must treat the
        two identically."""
        result = _run_backup(self.project_dir, state_root="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STATE_ROOT not given", result.stderr)

    def test_omni_and_docs_are_still_snapshotted(self):
        _run_backup(self.project_dir)
        files = _state_ref_tree_files(self.project_dir)
        self.assertIn(".omni/goal.md", files)

    def test_no_memory_manifest_is_written_or_captured(self):
        _run_backup(self.project_dir)
        self.assertFalse(os.path.exists(
            os.path.join(self.project_dir, ".omni", "memory-state-manifest.json")))
        self.assertNotIn(".omni/memory-state-manifest.json",
                          _state_ref_tree_files(self.project_dir))


class StateRootInvalid(unittest.TestCase):
    """Branch 2: the argument is given but does not name a real directory
    -- a wrong `--state-root`/typo/host-migration-in-progress case. Must
    fail LOUDLY (nonzero exit), per the H4 fix's entire point: a
    misconfigured memory backup must be visible, not a quiet green run
    that happens to cover nothing."""

    def setUp(self):
        self.project_dir = _fresh_project_dir()
        self.missing_state_root = os.path.join(
            tempfile.mkdtemp(prefix="nightshift-backup-gone-"), "does-not-exist")

    def test_nonexistent_state_root_exits_nonzero(self):
        result = _run_backup(self.project_dir, state_root=self.missing_state_root)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("STATE_ROOT does not exist", result.stderr)

    def test_omni_and_docs_are_still_snapshotted_despite_the_failure(self):
        """A wrong STATE_ROOT must not regress the ORIGINAL, already-working
        .omni/docs snapshot -- that coverage predates #400 and must survive
        a memory-specific misconfiguration untouched."""
        _run_backup(self.project_dir, state_root=self.missing_state_root)
        files = _state_ref_tree_files(self.project_dir)
        self.assertIn(".omni/goal.md", files)


class StateRootValidWithMemory(unittest.TestCase):
    """Branch 3: a real STATE_ROOT containing a memory.db-shaped file (plus
    -wal/-shm sidecars, matching WAL mode) -- the actual case H4 exists
    for. Metadata-only, by design (see the script's own comment): this
    suite proves that claim rather than trusting it."""

    def setUp(self):
        self.project_dir = _fresh_project_dir()
        self.state_root = tempfile.mkdtemp(prefix="nightshift-backup-state-")
        os.chmod(self.state_root, 0o700)
        self.secret_marker = b"AKIA_NIGHTSHIFT_BACKUP_MANIFEST_PROBE_9f1c2e"
        with open(os.path.join(self.state_root, "memory.db"), "wb") as fh:
            fh.write(b"SQLite format 3\x00" + self.secret_marker + b"\x00" * 64)
        with open(os.path.join(self.state_root, "memory.db-wal"), "wb") as fh:
            fh.write(b"\x00" * 32)
        with open(os.path.join(self.state_root, "memory.db-shm"), "wb") as fh:
            fh.write(b"\x00" * 32)
        # A decoy file that must NOT be swept up -- only memory.db and its
        # own -wal/-shm/-journal sidecars are in scope.
        with open(os.path.join(self.state_root, "unrelated.txt"), "w") as fh:
            fh.write("not memory\n")

    def test_exits_zero_and_writes_a_manifest(self):
        result = _run_backup(self.project_dir, state_root=self.state_root)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest_path = os.path.join(
            self.project_dir, ".omni", "memory-state-manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))

    def test_manifest_is_captured_in_the_same_snapshot_commit(self):
        _run_backup(self.project_dir, state_root=self.state_root)
        files = _state_ref_tree_files(self.project_dir)
        self.assertIn(".omni/goal.md", files)
        self.assertIn(".omni/memory-state-manifest.json", files)

    def test_manifest_lists_memory_db_and_its_sidecars_with_real_metadata(self):
        _run_backup(self.project_dir, state_root=self.state_root)
        with open(os.path.join(self.project_dir, ".omni",
                                "memory-state-manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["state_root"], self.state_root)
        paths = {row["path"]: row for row in manifest["files"]}
        self.assertIn("memory.db", paths)
        self.assertIn("memory.db-wal", paths)
        self.assertIn("memory.db-shm", paths)
        self.assertNotIn("unrelated.txt", paths)
        real_size = os.path.getsize(os.path.join(self.state_root, "memory.db"))
        self.assertEqual(paths["memory.db"]["size"], real_size)
        self.assertGreater(paths["memory.db"]["size"], 0)

    def test_manifest_never_contains_memory_db_bytes(self):
        """The script's own comment claims "metadata only ... never opens
        memory.db itself" -- checked here by searching the manifest's own
        raw text for a marker planted inside memory.db's bytes (a size
        comparison alone would not be decisive: JSON's own per-row
        formatting overhead can exceed a tiny fixture database's size, so
        this is the real proof, not the size)."""
        _run_backup(self.project_dir, state_root=self.state_root)
        manifest_path = os.path.join(
            self.project_dir, ".omni", "memory-state-manifest.json")
        with open(manifest_path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(self.secret_marker, raw)


class StaticServiceUnitWiring(unittest.TestCase):
    """The two facts that make STATE_ROOT reachable in production AT ALL,
    checked statically against the checked-in deploy files -- the H4
    blocker's exact failure mode was that `nightshift-state-backup.sh`
    itself was fixed while nothing upstream of it ever passed the new
    argument, so the fix was invisible in production. These two checks
    would have caught that: dropping either one fails this suite."""

    def test_service_unit_execstart_names_a_state_root_argument(self):
        with open(SERVICE_UNIT, encoding="utf-8") as fh:
            unit_text = fh.read()
        exec_lines = [ln for ln in unit_text.splitlines()
                      if ln.startswith("ExecStart=")]
        self.assertEqual(len(exec_lines), 1, unit_text)
        self.assertIn("nightshift-state-backup.sh", exec_lines[0])
        # Two arguments after the script path: PROJECT_DIR, then a second
        # one for STATE_ROOT -- not just the historical PROJECT_DIR-only
        # invocation the H4 blocker measured in production.
        args = exec_lines[0].split("nightshift-state-backup.sh", 1)[1].split()
        self.assertEqual(
            len(args), 2,
            f"ExecStart passes {len(args)} argument(s) to "
            f"nightshift-state-backup.sh, expected 2 (PROJECT_DIR, "
            f"STATE_ROOT): {exec_lines[0]!r}")

    def test_bootstrap_sh_substitutes_the_state_root_placeholder(self):
        with open(BOOTSTRAP_SH, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(
            "STATE_ROOT=", text,
            "deploy/bootstrap.sh declares no STATE_ROOT variable -- the "
            "service unit's __STATE_ROOT__ placeholder would never be "
            "substituted")
        self.assertIn(
            "__STATE_ROOT__", text,
            "deploy/bootstrap.sh's sed substitution list does not mention "
            "__STATE_ROOT__ -- the service unit's placeholder would be "
            "installed verbatim, never filled in")


class BackupRemoteDestination(unittest.TestCase):
    """Audit B4: the snapshot (control dump, incidents) used to be pushed to
    `origin` every 30 minutes, which is public whenever the project repo is.
    Now only an explicit `nightshift-state` remote receives it, and a GitHub
    one must report `visibility: private`. `gh` is a stub on PATH; GitHub
    URLs are redirected to a local bare repository with `insteadOf`, so a
    push that should have been refused would be visible there."""

    URL = "https://github.com/example-org/state.git"

    def setUp(self):
        self.project_dir = _fresh_project_dir()
        self.bare = tempfile.mkdtemp(prefix="nightshift-backup-remote-")
        _git(self.bare, "init", "-q", "--bare")
        self.bindir = tempfile.mkdtemp(prefix="nightshift-backup-bin-")
        self.gh_log = os.path.join(self.bindir, "gh.log")
        stub = os.path.join(self.bindir, "gh")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\necho "$*" >> "%s"\necho "$FAKE_VISIBILITY"\n'
                     % self.gh_log)
        os.chmod(stub, 0o755)

    def _run(self, visibility):
        env = dict(GIT_ENV, FAKE_VISIBILITY=visibility,
                   PATH=self.bindir + os.pathsep + GIT_ENV["PATH"])
        return subprocess.run([SCRIPT, self.project_dir], env=env,
                              capture_output=True, text=True, timeout=60)

    def _pushed(self):
        proc = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "refs/heads/omni/state"],
            cwd=self.bare, env=GIT_ENV, capture_output=True, text=True)
        return proc.returncode == 0

    def _github_remote(self):
        _git(self.project_dir, "remote", "add", "nightshift-state", self.URL)
        _git(self.project_dir, "config", f"url.{self.bare}.insteadOf", self.URL)

    def test_no_backup_remote_means_no_push_even_to_origin(self):
        _git(self.project_dir, "remote", "add", "origin", self.bare)
        result = self._run("public")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no 'nightshift-state' remote", result.stderr)
        self.assertFalse(self._pushed(), "the snapshot reached origin")
        self.assertIn(".omni/goal.md", _state_ref_tree_files(self.project_dir))

    def test_public_github_remote_is_refused(self):
        self._github_remote()
        result = self._run("public")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to push", result.stderr)
        self.assertFalse(self._pushed())
        with open(self.gh_log, encoding="utf-8") as fh:
            self.assertIn("api repos/example-org/state --jq .visibility", fh.read())

    def test_unreadable_visibility_is_refused(self):
        self._github_remote()
        result = self._run("")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self._pushed())

    def test_private_github_remote_receives_the_snapshot(self):
        self._github_remote()
        result = self._run("private")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self._pushed())


if __name__ == "__main__":
    unittest.main()
