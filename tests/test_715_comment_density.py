"""Issue #715, epic #716: a comment-density ceiling, measured not asked for.

The run that produced this ticket wrote a package at **47% comment lines**
against a repository whose own baseline was 4-7%. The instruction to match the
surrounding style already existed and was followed at 47%, so this is
arithmetic rather than another sentence.

Every test builds its own repository, because the property under test is
"the ceiling comes from THIS repository" -- a test that judged against a
constant would pass on any tree and prove nothing about that.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comment_density_guard as guard  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


class Repo:
    """A throwaway repository with a house style of its own."""

    def __init__(self, root: Path):
        self.root = root
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL="/dev/null",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.c",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.c")
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init",
                        str(root)], check=True, capture_output=True)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message="c"):
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True,
                       env=self.env, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-m", message],
                       check=True, env=self.env, capture_output=True)
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def house(self, comment_lines, code_lines, name="house.go"):
        self.write(name, "// h\n" * comment_lines + "x := 1\n" * code_lines)
        return self.commit("house style")

    def change(self, comment_lines, code_lines, name="new.go"):
        self.write(name, "// explanation\n" * comment_lines + "y := 2\n" * code_lines)
        return self.commit("the change")

    def py_house(self, comment_lines, code_lines, name="house.py"):
        self.write(name, "# h\n" * comment_lines + "x = 1\n" * code_lines)
        return self.commit("python house")

    def py_docstring_change(self, prose_lines, code_lines, name="new.py"):
        """Python prose as it is actually written: a docstring, not `#`."""
        body = '"""\n' + "prose line\n" * prose_lines + '"""\n'
        self.write(name, body + "y = 2\n" * code_lines)
        return self.commit("python change")


def repo(case):
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    return Repo(Path(tmp.name))


class TheCeilingComesFromTheRepository(unittest.TestCase):
    def test_the_47_percent_case_is_refused(self):
        """The measurement that produced this ticket, reproduced."""
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=100)
        head = r.change(comment_lines=47, code_lines=53)
        verdicts = guard.assess(r.root, base, head)
        self.assertEqual(len(verdicts), 1)
        self.assertTrue(verdicts[0].over, verdicts[0].describe())
        self.assertIn("47%", verdicts[0].describe())
        self.assertIn("5%", verdicts[0].describe())

    def test_writing_like_the_repository_passes(self):
        """The negative control. This gate must be silent on ordinary work."""
        r = repo(self)
        base = r.house(comment_lines=8, code_lines=100)
        head = r.change(comment_lines=9, code_lines=100)
        self.assertFalse(guard.assess(r.root, base, head)[0].over)

    def test_the_same_change_passes_in_a_verbose_repository(self):
        """AC-5, and the reason this is a multiple rather than a number.

        One identical change -- 30% comments -- judged against two house
        styles. A codebase that genuinely writes at 25% should not be forced
        down to 5%, and a terse one should not be dragged up.
        """
        terse = repo(self)
        base = terse.house(comment_lines=5, code_lines=100)
        head = terse.change(comment_lines=30, code_lines=70)
        self.assertTrue(guard.assess(terse.root, base, head)[0].over)

        verbose = repo(self)
        base = verbose.house(comment_lines=25, code_lines=75)
        head = verbose.change(comment_lines=30, code_lines=70)
        self.assertFalse(guard.assess(verbose.root, base, head)[0].over,
                         "a house that writes prose was refused its own style")

    def test_the_change_cannot_move_the_baseline_it_is_judged_against(self):
        """A large enough change would otherwise dominate the average and
        license itself -- the bigger the violation, the more it raises the bar
        it has to clear."""
        r = repo(self)
        base = r.house(comment_lines=2, code_lines=40)
        head = r.change(comment_lines=400, code_lines=400)
        verdict = guard.assess(r.root, base, head)[0]
        self.assertTrue(verdict.over, verdict.describe())
        self.assertLess(verdict.house.line_ratio, 0.10,
                        "the touched file leaked into its own baseline")

    def test_a_tiny_change_is_not_judged(self):
        """Three lines with two comments is 66% and means nothing."""
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=100)
        head = r.change(comment_lines=6, code_lines=3)
        self.assertFalse(guard.assess(r.root, base, head)[0].over)

    def test_tests_are_not_judged(self):
        """Test files legitimately explain themselves more than source, and
        the issue measured non-test source deliberately."""
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=100)
        r.write("thing_test.go", "// explanation\n" * 90 + "z := 3\n" * 10)
        head = r.commit("a very chatty test")
        self.assertEqual(guard.assess(r.root, base, head), [])

    def test_each_language_is_judged_against_its_own_style(self):
        """Go's house style is not Python's."""
        r = repo(self)
        r.write("house.go", "// h\n" * 2 + "x := 1\n" * 100)
        r.write("house.py", "# h\n" * 40 + "x = 1\n" * 60)
        base = r.commit("two styles")
        r.write("new.go", "// e\n" * 30 + "y := 2\n" * 70)
        r.write("new.py", "# e\n" * 30 + "y = 2\n" * 70)
        head = r.commit("same ratio, both languages")
        by_suffix = {v.suffix: v for v in guard.assess(r.root, base, head)}
        self.assertTrue(by_suffix[".go"].over, "terse Go accepted 30%")
        self.assertFalse(by_suffix[".py"].over, "verbose Python refused 30%")


class TheHolesAReviewFound(unittest.TestCase):
    """Each of these passed the first implementation. Kept as named
    regressions because every one made the guard weaker than it looked."""

    def test_python_prose_in_docstrings_is_counted(self):
        """The fatal one. Python prose lives in docstrings, and counting only
        `#` lines made the gate INERT on this repository -- 48 Python files,
        no Go. The first version scored its own source at 2%; it is 24%."""
        r = repo(self)
        base = r.py_house(comment_lines=4, code_lines=100)
        head = r.py_docstring_change(prose_lines=90, code_lines=30)
        verdict = guard.assess(r.root, base, head)[0]
        self.assertTrue(verdict.over, verdict.describe())
        self.assertGreater(verdict.added.line_ratio, 0.6,
                           "docstring prose was counted as code")

    def test_a_language_new_to_the_repository_is_still_judged(self):
        """`the first external run wrote a package` -- a language the repo
        does not yet use has no baseline, and the first version printed `[ok]`
        and judged nothing. It now falls back to the absolute floor."""
        r = repo(self)
        base = r.py_house(comment_lines=4, code_lines=100)
        head = r.change(comment_lines=47, code_lines=53)
        verdict = guard.assess(r.root, base, head)[0]
        self.assertTrue(verdict.no_baseline)
        self.assertTrue(verdict.over, verdict.describe())
        self.assertIn("absolute floor", verdict.describe())

    def test_touching_a_big_file_does_not_launder_prose(self):
        """One appended line to a large code file diluted any amount of new
        prose below the ceiling, because whole FILES were measured. Added
        lines are what the change is responsible for."""
        r = repo(self)
        r.write("house.go", "// h\n" * 5 + "x := 1\n" * 100)
        r.write("big.go", "z := 0\n" * 4000)
        base = r.commit("house + a big file")
        r.write("new.go", "// explanation\n" * 470 + "y := 2\n" * 530)
        r.write("big.go", "z := 0\n" * 4000 + "z := 1\n")
        head = r.commit("prose, plus one line elsewhere")
        verdict = guard.assess(r.root, base, head)[0]
        self.assertTrue(verdict.over, verdict.describe())

    def test_a_terse_repository_still_allows_ordinary_comments(self):
        """A 1% baseline would otherwise forbid all commenting, and a gate
        that refuses honest work gets deleted rather than obeyed."""
        r = repo(self)
        base = r.house(comment_lines=10, code_lines=1000)
        head = r.change(comment_lines=8, code_lines=92)
        self.assertFalse(guard.assess(r.root, base, head)[0].over)

    def test_prose_on_one_enormous_line_does_not_evade(self):
        """A line-based ratio is defeated by unwrapping. Characters are
        checked with the same multiple."""
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=200)
        r.write("new.go", "// " + ("word " * 4000) + "\n" + "y := 2\n" * 80)
        head = r.commit("one very long comment")
        verdict = guard.assess(r.root, base, head)[0]
        self.assertEqual(verdict.added.comment_lines, 1,
                         "the prose really is on one raw line")
        self.assertGreater(verdict.added.comment_units, 100,
                           "wrapping was not normalised away")
        self.assertTrue(verdict.over, "unwrapped prose evaded the ceiling")

    def test_a_filename_containing_test_is_not_exempt(self):
        """Substring matching exempted `latest_run.go` and `spec.go` from both
        judgement and the baseline -- a one-rename bypass."""
        self.assertTrue(guard.is_source("internal/latest_run.go"))
        self.assertTrue(guard.is_source("api/spec.go"))
        self.assertFalse(guard.is_source("pkg/thing_test.go"))
        self.assertFalse(guard.is_source("tests/helper.py"))
        self.assertFalse(guard.is_source("vendor/lib/x.go"))

    def test_the_baseline_reads_the_revision_not_the_working_tree(self):
        """The house style must come from the committed tree too, not just the
        added lines. A mutation that read the checkout instead survived the
        first version of this suite, which means nothing covered it."""
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=200)
        head = r.change(comment_lines=40, code_lines=60)
        # Make the working tree look like a prose-heavy house style. If the
        # baseline reads the checkout, the ceiling rises and the change passes.
        (r.root / "house.go").write_text("// h\n" * 500 + "x := 1\n" * 10)
        verdict = guard.assess(r.root, base, head)[0]
        self.assertLess(verdict.house.line_ratio, 0.10,
                        "the baseline came from the dirty working tree")
        self.assertTrue(verdict.over)

    def test_the_verdict_reads_the_revision_not_the_working_tree(self):
        """Content came from the checkout while paths came from a revision, so
        a dirty tree silently changed the verdict."""
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=100)
        head = r.change(comment_lines=47, code_lines=53)
        (r.root / "new.go").write_text("y := 2\n")   # scrub, uncommitted
        self.assertTrue(guard.assess(r.root, base, head)[0].over,
                        "the working tree overrode the committed revision")


class CountingIsHonest(unittest.TestCase):
    def test_block_comments_count(self):
        d = guard.classify("/* one\n two\n three */\nx := 1\n".splitlines(), ".go")
        self.assertEqual((d.comment_lines, d.code_lines), (3, 1))

    def test_a_block_that_reopens_into_code_is_code(self):
        """`/* x */ code()` was counted as pure comment -- the code line was
        lost AND a comment invented, which is the unsafe direction."""
        d = guard.classify(["/* x */ code()"], ".go")
        self.assertEqual((d.comment_lines, d.code_lines), (0, 1))

    def test_an_unterminated_block_does_not_swallow_the_file(self):
        d = guard.classify("/* oops\nstill comment\n".splitlines(), ".go")
        self.assertEqual(d.comment_lines, 2)

    def test_a_shebang_is_a_comment_but_one_line(self):
        d = guard.classify(["#!/bin/sh", "echo hi"], ".sh")
        self.assertEqual((d.comment_lines, d.code_lines), (1, 1))

    def test_blank_lines_count_as_neither(self):
        d = guard.classify("// a\n\n\nx := 1\n".splitlines(), ".go")
        self.assertEqual((d.comment_lines, d.code_lines), (1, 1))

    def test_a_url_in_code_is_not_a_comment(self):
        d = guard.classify(['u := "https://example.com/x"'], ".go")
        self.assertEqual((d.comment_lines, d.code_lines), (0, 1))

    def test_trailing_comments_are_code(self):
        """`x := 1 // why` is a line of code. Counting it as prose would
        punish the density the house style is largely made of."""
        d = guard.classify(["x := 1 // why"], ".go")
        self.assertEqual((d.comment_lines, d.code_lines), (0, 1))


class ItIsWiredIn(unittest.TestCase):
    def _step(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover
            self.skipTest("pyyaml not available")
        spec = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/tests.yml").read_text())
        steps = [s for job in spec["jobs"].values() for s in job["steps"]]
        named = [s for s in steps if s.get("name") == "comment density"]
        self.assertEqual(len(named), 1, "the comment density step is not there")
        return named[0]

    def test_the_workflow_runs_it(self):
        self.assertIn("tests/comment_density_guard.py", self._step()["run"])

    def test_it_is_pull_request_scoped_and_uses_the_merge_base(self):
        step = self._step()
        self.assertEqual(step["if"], "github.event_name == 'pull_request'")
        self.assertIn("git merge-base", step["run"])

    def test_the_cli_exit_status_is_the_contract(self):
        r = repo(self)
        base = r.house(comment_lines=5, code_lines=100)
        head = r.change(comment_lines=47, code_lines=53)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tests/comment_density_guard.py"),
             "--repo", str(r.root), "--base", base, "--head", head],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("REFUSED", proc.stdout)

    def test_an_unusable_ref_fails_closed_and_says_so(self):
        r = repo(self)
        r.house(comment_lines=5, code_lines=100)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tests/comment_density_guard.py"),
             "--repo", str(r.root), "--base", "no-such-ref", "--head", "HEAD"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("could not run", proc.stdout)


if __name__ == "__main__":
    unittest.main()
