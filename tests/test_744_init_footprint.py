"""Issue #744: initialising a repository must not change it.

The zero-footprint guarantee (#400 AC1) says operating on a borrowed repository
writes nothing into it. `nightshift-init.sh` broke that on its very first
command: it appended ignore rules to `.gitignore`, a TRACKED file, so the
owner's repository was modified before an agent had done anything -- and if
anyone later committed with `-a`, that edit shipped inside unrelated work.

No test ran the script end to end, which is why nobody noticed. These do.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "bin" / "nightshift-init.sh"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


class InitialisingARepositoryChangesNothingInIt(unittest.TestCase):
    def _repo(self, tracked_omni=False):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        (repo / ".gitignore").write_text("node_modules/\n*.log\n", encoding="utf-8")
        (repo / "app.go").write_text("package main\n", encoding="utf-8")
        if tracked_omni:
            # A repository that KEEPS its org state, as Nightshift's own does.
            (repo / ".omni").mkdir()
            (repo / ".omni" / "backlog.md").write_text("kept\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
        return repo

    def _run_init(self, repo):
        proc = subprocess.run(["bash", str(INIT)], cwd=str(repo),
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def test_no_tracked_file_is_modified(self):
        """The guarantee this ticket is actually about, stated at the strength
        it can actually hold.

        Init creates `.omni/` and a `docs/` scaffolding, and those stay visible
        as untracked files. That is deliberate: `nightshift-state-backup.sh`
        snapshots them with `git add -- .omni docs`, so hiding them would make
        the disaster-recovery backup capture nothing. Keeping the org's state
        out of a borrowed tree ENTIRELY is #400's problem -- the state belongs
        outside the repository, and no ignore rule can achieve that without
        breaking the backup that reads the same files.

        What must never happen, and what #744 reported, is a change to a file
        the repository's owner owns."""
        repo = self._repo()
        self._run_init(repo)
        modified = git(repo, "diff", "--name-only").stdout.strip()
        self.assertEqual(modified, "",
                         f"init modified tracked files:\n{modified}")
        staged = git(repo, "diff", "--cached", "--name-only").stdout.strip()
        self.assertEqual(staged, "", f"init staged changes:\n{staged}")

    def test_gitignore_is_not_modified(self):
        """Stated separately from the test above because it is the specific
        regression: `.gitignore` is tracked, so editing it is a change to the
        owner's repository, not a local convenience."""
        repo = self._repo()
        before = (repo / ".gitignore").read_text(encoding="utf-8")
        self._run_init(repo)
        self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"), before)

    def test_the_rules_are_in_info_exclude_and_actually_work(self):
        """Not merely written somewhere -- git must actually honour them.
        Asserting the file's contents would pass on a rule git ignores."""
        repo = self._repo()
        self._run_init(repo)
        exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".worktrees/", exclude)
        (repo / ".worktrees").mkdir(exist_ok=True)
        (repo / ".worktrees" / "scratch").write_text("x\n", encoding="utf-8")
        # git must actually honour it -- asserting the file's contents alone
        # would pass on a rule git ignores.
        self.assertNotIn(".worktrees",
                         git(repo, "status", "--porcelain").stdout)

    def test_a_repository_can_always_start_tracking_its_org_state(self):
        """The control that killed the first version of this fix.

        That version excluded the whole of `.omni/` in any repository that did
        not already track it, and made the choice ONCE, at init, on a repository
        that by definition has nothing yet. A project bootstrapped by this
        script could therefore never begin recording its own charters and
        decisions -- `git add` refused them as ignored. The rules must not
        depend on what the repository happened to contain the first time this
        ran."""
        repo = self._repo()          # tracks NOTHING under .omni yet
        self._run_init(repo)
        # Deliberately NOT asserting an empty status here. In a repository that
        # keeps its org state, the files init creates under .omni/ SHOULD be
        # visible -- that is how they get committed. Hiding them is the bug this
        # branch exists to avoid, so an empty status would mean the wrong thing.
        (repo / ".omni" / "charters").mkdir(parents=True, exist_ok=True)
        (repo / ".omni" / "charters" / "first.md").write_text("kept\n", encoding="utf-8")
        # `--porcelain` alone collapses an untracked directory to `?? .omni/`;
        # -uall is what actually shows whether the file is reachable.
        self.assertIn("first.md",
                      git(repo, "status", "--porcelain", "-uall").stdout,
                      "a new project cannot begin tracking its own org state")
        self.assertEqual(git(repo, "add", ".omni/charters/first.md").returncode, 0,
                         "git refused to add the org's own charter")
        self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"),
                         "node_modules/\n*.log\n",
                         "the tracked .gitignore was modified")

    def test_the_state_backup_can_still_see_what_it_snapshots(self):
        """`deploy/nightshift-state-backup.sh` runs `git add -- .omni docs`, and
        that add honours `.git/info/exclude` exactly as it honoured
        `.gitignore`. The first version of this fix excluded `.omni/` wholesale
        and so made the 30-minute disaster-recovery backup capture NOTHING, in
        exactly the borrowed repositories it exists for -- silently, because the
        add is `2>/dev/null || true`.

        This asserts the backup's own command still finds files. Runtime state
        (transcripts, the database) is excluded by design and is not wanted in
        a snapshot."""
        repo = self._repo()
        self._run_init(repo)
        git(repo, "add", "--", ".omni", "docs")
        staged = git(repo, "diff", "--cached", "--name-only").stdout.strip()
        self.assertNotEqual(staged, "", "the state backup would snapshot nothing")
        self.assertIn(".omni/", staged)
        self.assertIn("docs/", staged)
        self.assertNotIn("control.db", staged, "runtime state must stay out")
        self.assertNotIn("transcripts", staged, "transcripts must stay out")

    def test_running_it_twice_does_not_duplicate_the_rules(self):
        repo = self._repo()
        self._run_init(repo)
        self._run_init(repo)
        exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertEqual(exclude.count("# nightshift working state"), 1)
        self.assertEqual(exclude.count(".worktrees/"), 1)
        self.assertEqual(git(repo, "diff", "--name-only").stdout.strip(), "")


class InitNeverWritesThroughATrackedSymlink(unittest.TestCase):
    """Audit C3: the target repository can track `.omni/STATUS.md` -- or
    `.omni` itself -- as a symlink into the operator's home, and init's
    `printf > "$f"` / `cat > file` wrote through it."""

    def _setup(self):
        import os
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home, repo = Path(tmp.name) / "home", Path(tmp.name) / "repo"
        home.mkdir()
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        return os, home, repo

    def _init(self, repo):
        return subprocess.run(["bash", str(INIT)], cwd=str(repo),
                              capture_output=True, text=True, timeout=180)

    def test_a_dangling_file_link_is_refused_and_nothing_is_created(self):
        os, home, repo = self._setup()
        (repo / ".omni").mkdir()
        os.symlink(home / ".bashrc", repo / ".omni" / "STATUS.md")
        proc = self._init(repo)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("symlink", proc.stderr)
        self.assertFalse((home / ".bashrc").exists())

    def test_a_linked_state_directory_is_refused_before_any_write(self):
        os, home, repo = self._setup()
        os.symlink(home, repo / ".omni")
        proc = self._init(repo)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("symlink", proc.stderr)
        self.assertEqual(os.listdir(home), [])

    def test_a_tracked_control_db_is_refused(self):
        """Audit C9 through init: a repository that ships `.omni/control.db`
        hands init a forged plane to adopt."""
        _, _, repo = self._setup()
        (repo / ".omni").mkdir()
        (repo / ".omni" / "control.db").write_bytes(b"")
        git(repo, "add", "-f", ".omni/control.db")
        proc = self._init(repo)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("control_plane_untrusted", proc.stderr)


if __name__ == "__main__":
    unittest.main()
