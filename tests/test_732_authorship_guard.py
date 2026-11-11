"""Issue #732 / ADR-0094, epic #716 change 5: the coordinator does not write
the code, and a source commit says which seat wrote it.

Both directions on every rule, against temporary git repositories -- a gate
observed only on the tree it ships with has never been seen to refuse.

Read `authorship_guard`'s module docstring first for what this deliberately
does NOT do: it is a merge-time gate on the record, not a moment-of-action
deny, because ADR-0009 established that the tool-call layer cannot see a cwd,
a repository target or a caller identity, and removed its own policy-text deny
for exactly that reason.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authorship_guard as guard  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT = "/usr/bin/git"


class Repo:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._run("init", "-b", "main")
        self.write("README.md", "start\n")
        self.commit("base", author="crew_alpha")
        self.base = self.head()

    def _run(self, *args, author=None):
        env = dict(os.environ)
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": author or "crew_alpha",
                    "GIT_AUTHOR_EMAIL": "a@e.c",
                    "GIT_COMMITTER_NAME": "ci", "GIT_COMMITTER_EMAIL": "c@e.c"})
        return subprocess.run([GIT, "-C", str(self.root), *args], check=True,
                              env=env, capture_output=True, text=True).stdout

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message, author="crew_alpha"):
        self._run("add", "-A", author=author)
        self._run("commit", "-m", message, author=author)
        return self.head()

    def head(self):
        return self._run("rev-parse", "HEAD").strip()

    def scan(self):
        return guard.scan(self.root, self.base, self.head())


class TheGuardRefuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(Path(self.tmp.name) / "r")

    def test_a_crew_writing_source_is_fine(self):
        """The negative control for the whole file. This gate exists to let
        the crews work, so it must be silent when they do."""
        self.repo.write("bin/thing.py", "x = 1\n")
        self.repo.commit("crew ships code", author="crew_alpha")
        self.assertEqual(self.repo.scan(), [])

    def test_the_coordinator_writing_source_is_refused(self):
        self.repo.write("bin/thing.py", "x = 1\n")
        self.repo.commit("program writes code", author="program")
        violations = self.repo.scan()
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("Hard Rule 1", violations[0].reason)
        self.assertEqual(violations[0].paths, ("bin/thing.py",))

    def test_the_default_identity_on_this_box_is_refused(self):
        """The finding that made the first version of this gate VACUOUS.

        `staff` is a seat, and the shared checkout's own git config sets
        `user.name = staff` -- so a coordinator shell inheriting that config
        commits as `staff`, not as `ci` or `program`. The first draft put
        `staff` explicitly out of scope, which meant the gate permitted the
        exact omission path it was built to catch. Hard Rule 1 reserves source
        for crews, platform and oncall; `staff` is not one of them.
        """
        self.repo.write("bin/thing.py", "x = 1\n")
        self.repo.commit("staff writes code", author="staff")
        violations = self.repo.scan()
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("Hard Rule 1", violations[0].reason)

    def test_capitalisation_does_not_evade(self):
        """`Program` with a capital P already exists once in this history and
        passed the first version's case-sensitive check."""
        self.repo.write("bin/thing.py", "x = 1\n")
        self.repo.commit("Program writes code", author="Program")
        self.assertEqual(len(self.repo.scan()), 1)

    def test_a_tool_identity_that_names_no_seat_is_refused(self):
        """`Nightshift Publication Gateway` and `nightshift-state` are real
        in-repo commit identities. Running one lands a source commit under a
        name nobody can be held to, with no --author flag anywhere."""
        for author in ("Nightshift Publication Gateway", "nightshift-state",
                       "nightshift-release"):
            self.repo.write("bin/thing.py", f"# {author}\n")
            self.repo.commit(f"{author} commits", author=author)
        violations = self.repo.scan()
        self.assertEqual(len(violations), 3, violations)
        self.assertTrue(all("names no seat" in v.reason for v in violations))

    def test_a_crews_dotted_and_spaced_identities_pass(self):
        """One seat commits under several spellings. Refusing them would stop
        the crews working, which is the failure that gets a gate deleted."""
        for author in ("crew_alpha.eng_codex", "crew_alpha lead", "crew_beta",
                       "platform", "oncall"):
            self.repo.write("bin/thing.py", f"# {author}\n")
            self.repo.commit(f"{author} ships", author=author)
        self.assertEqual(self.repo.scan(), [])

    def test_an_unrecognised_author_passes(self):
        """Deliberate: the refused set comes from the seat roster, and a human
        contributor is not on it. A gate that blocked a new contributor's first
        commit would be switched off rather than fixed."""
        self.repo.write("bin/thing.py", "x = 1\n")
        self.repo.commit("a person", author="Ada Lovelace")
        self.assertEqual(self.repo.scan(), [])

    def test_a_non_ascii_path_does_not_evade(self):
        """git C-quotes such a path by default, and the leading quote defeated
        the first version's startswith check."""
        self.repo.write("bin/café.py", "x = 1\n")
        self.repo.commit("program adds a unicode path", author="program")
        violations = self.repo.scan()
        self.assertEqual(len(violations), 1, violations)

    def test_moving_source_out_of_the_guarded_trees_does_not_evade(self):
        """Rename detection prints only the destination, so `git mv bin/b.py
        moved.py` reported a path outside the guarded set -- one unflagged
        commit relocates the tree and every later edit becomes invisible."""
        self.repo.write("bin/b.py", "x = 1\n")
        self.repo.commit("crew adds it", author="crew_alpha")
        self.repo._run("mv", "bin/b.py", "moved.py", author="program")
        self.repo._run("commit", "-m", "program moves source out",
                       author="program")
        violations = self.repo.scan()
        self.assertEqual(len(violations), 1, violations)

    def test_the_rule_and_the_tools_under_program_are_guarded(self):
        """The coordinator could otherwise rewrite Hard Rule 1 itself, the
        conformance ruleset, and the publish tool that hardcodes the crews'
        git identity, with this gate silent."""
        for rel in ("program/config.yaml", "program/conformance-rules.json",
                    "program/agents/crew_alpha/agents/eng_codex/tools/x.py"):
            self.repo.write(rel, "x\n")
            self.repo.commit(f"program edits {rel}", author="program")
        self.assertEqual(len(self.repo.scan()), 3)

    def test_a_seats_own_config_is_not_guarded(self):
        """Guarding all of program/** would stop a seat editing its own
        charter, which is not the thing being stopped."""
        self.repo.write("program/agents/staff/config.yaml", "x: 1\n")
        self.repo.commit("staff edits its own charter", author="staff")
        self.assertEqual(self.repo.scan(), [])

    def test_the_coordinators_other_name_is_refused_too(self):
        """It commits under more than one name; refusing only the obvious one
        would leave the gate satisfiable by a git config line."""
        self.repo.write("tests/test_x.py", "x = 1\n")
        self.repo.commit("also program", author="nightshift-program")
        self.assertEqual(len(self.repo.scan()), 1)

    def test_the_coordinator_writing_markdown_is_fine(self):
        """Hard Rule 1 says Markdown only -- not silence. A gate that stopped
        the coordinator doing its actual job would be turned off within a day."""
        self.repo.write(".omni/ledger.md", "- did a thing\n")
        self.repo.write("docs/decisions/ADR-0001-x.md", "# ADR\n")
        self.repo.commit("program records decisions", author="program")
        self.assertEqual(self.repo.scan(), [])

    def test_an_unattributed_identity_writing_source_is_refused(self):
        """The important half. In the run behind #716 the coordinator's source
        commits did not say `program`; they fell through to the box's git
        config and said `ci`, which is why authorship had to be reconstructed
        by hand afterwards."""
        self.repo.write("deploy/x.sh", "echo hi\n")
        self.repo.commit("fallback identity", author="ci")
        violations = self.repo.scan()
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("names no seat", violations[0].reason)

    def test_a_merge_commit_is_not_judged(self):
        """A merge carries the union of its parents' paths under an author who
        wrote none of them. Judging one reports the integrator for the whole
        branch."""
        self.repo.write("bin/a.py", "a = 1\n")
        self.repo.commit("crew work", author="crew_alpha")
        side = self.repo.head()
        self.repo._run("checkout", "-b", "side", self.repo.base)
        self.repo.write("bin/b.py", "b = 1\n")
        self.repo.commit("more crew work", author="crew_beta")
        self.repo._run("checkout", "main")
        self.repo._run("merge", "--no-ff", "-m", "merge", "side",
                       author="program")
        merge_sha = self.repo.head()
        self.assertEqual(self.repo.scan(), [],
                         "a merge commit was judged as authorship")
        # Assert the MECHANISM, not just the outcome. `git show --name-only`
        # happens to print nothing for a merge, so the outcome above holds even
        # with `--no-merges` removed -- the protection would then be incidental
        # and would evaporate the day this switched to `diff-tree -m`.
        listed = {sha for sha, _author in
                  guard.commits_in(self.repo.root, self.repo.base, merge_sha)}
        self.assertNotIn(merge_sha, listed,
                         "the merge commit was enumerated at all")
        self.assertIn(side, listed, "the branch's real commits were skipped")

    def test_only_source_paths_count(self):
        """Documentation is not source, whoever writes it."""
        self.repo.write("docs/thing.md", "words\n")
        self.repo.commit("ci writes docs", author="ci")
        self.assertEqual(self.repo.scan(), [])

    def test_every_source_tree_is_covered(self):
        """The four trees Hard Rule 1 reserves. Asserted one at a time, so a
        prefix quietly dropped from the list fails here rather than silently
        opening a hole."""
        for index, rel in enumerate(("bin/x.py", "deploy/y.sh",
                                     "tests/test_z.py", ".github/w.yml")):
            self.repo.write(rel, f"# {index}\n")
            self.repo.commit(f"program touches {rel}", author="program")
        self.assertEqual(len(self.repo.scan()), 4)

    def test_the_report_names_the_commit_and_the_files(self):
        """A refusal nobody can act on gets waved through."""
        self.repo.write("bin/thing.py", "x = 1\n")
        sha = self.repo.commit("program writes code", author="program")
        text = guard.format_violations(self.repo.scan())
        self.assertIn(sha[:12], text)
        self.assertIn("bin/thing.py", text)


class TheGateIsWiredIn(unittest.TestCase):
    """#716 change 4's rule, applied to this change: whoever adds a check must
    show what invokes it."""

    def _step(self):
        """The real parsed step, not a substring of the file.

        Review finding: the first version asserted two substrings, so it passed
        against a step whose `if:` was `false`, against a step commented out,
        and against malformed YAML. It contributed nothing to mutation
        coverage because it could not fail.
        """
        try:
            import yaml
        except ImportError:  # pragma: no cover - pyyaml absent
            self.skipTest("pyyaml not available to parse the workflow")
        spec = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/tests.yml").read_text())
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]
        named = [s for s in steps if s.get("name") == "authorship gate"]
        self.assertEqual(len(named), 1, "the authorship gate step is not there")
        return named[0]

    def test_the_workflow_runs_it(self):
        step = self._step()
        self.assertIn("tests/authorship_guard.py", step["run"])

    def test_it_is_pull_request_scoped_and_uses_the_merge_base(self):
        """Judged against where the branch actually forked. Against the base
        SHA directly, a stale branch is charged with commits that landed on
        main after it was cut -- including the historical ones this gate
        deliberately does not demand be rewritten."""
        step = self._step()
        self.assertEqual(step["if"], "github.event_name == 'pull_request'")
        self.assertIn("git merge-base", step["run"])

    def test_the_cli_exits_nonzero_on_a_violation(self):
        """The workflow's `run:` step fails on exit status alone, so that is
        the contract that has to hold."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(Path(tmp.name) / "r")
        repo.write("bin/thing.py", "x = 1\n")
        repo.commit("program writes code", author="program")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tests/authorship_guard.py"),
             "--repo", str(repo.root), "--base", repo.base,
             "--head", repo.head()],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("bin/thing.py", proc.stdout)

    def test_the_cli_exits_zero_when_clean(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(Path(tmp.name) / "r")
        repo.write("bin/thing.py", "x = 1\n")
        repo.commit("crew writes code", author="crew_alpha")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tests/authorship_guard.py"),
             "--repo", str(repo.root), "--base", repo.base,
             "--head", repo.head()],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class ItFailsClosed(unittest.TestCase):
    def test_an_unusable_ref_is_not_reported_as_clean(self):
        """A broken invocation must be distinguishable from a clean scan. The
        first version raised a bare CalledProcessError, which exits 1 -- the
        same code as a violation, with a traceback instead of a sentence."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(Path(tmp.name) / "r")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tests/authorship_guard.py"),
             "--repo", str(repo.root), "--base", "no-such-ref",
             "--head", repo.head()],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("could not run", proc.stdout)


class WhatThisDoesNotClaim(unittest.TestCase):
    def test_a_forged_author_is_not_detected(self):
        """Stated as a test rather than only in prose, so nobody reads this
        gate as proof of authorship. Identity is unauthenticated (#725): a
        coordinator that passes --author gets through, and what changed is
        that the violation now requires an affirmative lie instead of
        happening by default."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(Path(tmp.name) / "r")
        repo.write("bin/thing.py", "x = 1\n")
        repo.commit("program, wearing a crew's name", author="crew_alpha")
        self.assertEqual(guard.scan(repo.root, repo.base, repo.head()), [],
                         "this gate cannot detect a forged author, and must "
                         "not be documented as if it could")


if __name__ == "__main__":
    unittest.main()
