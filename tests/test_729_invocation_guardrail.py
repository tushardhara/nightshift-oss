"""Issue #729 / ADR-0093, epic #716 change 4: whoever adds a test must show
what runs it.

The rule came from a run containing three artefacts that were written, looked
correct, and were executed by nothing. This suite tests the guardrail that
asks the question mechanically -- and, per the rule it enforces, the guardrail
is itself reached by ordinary `run_suite.py` discovery, which is the same
proof it demands of everything else.

Every gate is exercised in both directions against a temporary git repository,
not against this one: an audit that can only be observed passing on the tree
it ships with has never been seen to refuse anything.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import invocation_guardrail as guard  # noqa: E402
import dod_validate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT = "/usr/bin/git"


_FIXTURE_SEQ = iter(range(1, 10_000))


class FixtureRepo:
    """A tiny git repo shaped like this one, so the audit can be made to fail.

    Each fixture's ordinary test gets a UNIQUE module name. `unittest`'s
    discovery imports by name and refuses to import a second module of the
    same name from a different directory, so reusing one name would make
    every fixture after the first fail with an ImportError that looks like a
    guardrail bug and is not.
    """

    def __init__(self, root: Path):
        self.root = root
        self.seq = next(_FIXTURE_SEQ)
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.c",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.c",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
        self.env = env
        subprocess.run([GIT, "-c", "init.defaultBranch=main", "init", str(root)],
                       check=True, stdout=subprocess.PIPE)
        (root / "tests").mkdir()
        (root / ".github").mkdir()
        self.write(f"tests/test_ordinary_{self.seq}.py",
                   "import unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_x(self):\n        self.assertTrue(True)\n")
        self.commit()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self):
        subprocess.run([GIT, "-C", str(self.root), "add", "-A"], check=True,
                       env=self.env, stdout=subprocess.PIPE)
        subprocess.run([GIT, "-C", str(self.root), "commit", "-m", "x"],
                       check=True, env=self.env, stdout=subprocess.PIPE)

    def audit(self, manifest):
        return guard.audit(manifest, repo_root=self.root,
                           tests_dir=self.root / "tests")


class TheGuardrailRefuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = FixtureRepo(Path(self.tmp.name) / "r")

    def test_an_ordinary_test_needs_no_entry(self):
        """The negative control for everything below: the guardrail must not
        tax normal work. A test discovery reaches is simply fine."""
        result = self.repo.audit({"entries": []})
        self.assertEqual(result.problems, [], result.problems)

    def test_a_test_in_a_subdirectory_package_is_not_called_unrun(self):
        """A test inside a package IS discovered, and reporting it as unrun
        would force an author to write down that a running test does not run.
        Caught by review: the first version compared a bare basename against
        discovery's dotted names, so every packaged test looked unrun."""
        self.repo.write("tests/__init__.py", "")
        self.repo.write("tests/sub/__init__.py", "")
        self.repo.write("tests/sub/test_inner.py",
                        "import unittest\n\n\nclass T(unittest.TestCase):\n"
                        "    def test_x(self):\n        pass\n")
        self.repo.commit()
        result = self.repo.audit({"entries": []})
        self.assertEqual(result.problems, [], result.problems)

    def test_a_module_that_cannot_be_imported_is_not_credited_as_running(self):
        """The failure the whole epic cites: a module that would not import
        made 247 tests SKIP and the run reported OK. Discovery reports such a
        module as a synthetic failure, which must not be read as evidence that
        the real module ran."""
        self.repo.write("tests/test_boom.py", "raise RuntimeError('nope')\n")
        self.repo.commit()
        state = guard.classify(repo_root=self.repo.root,
                               tests_dir=self.repo.root / "tests")
        self.assertEqual(state["tests/test_boom.py"], "undiscovered")

    def test_a_test_nothing_runs_fails(self):
        """AC-1. The whole point."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": []})
        self.assertTrue(any("nothing declares what does" in p
                            for p in result.problems), result.problems)

    def test_declaring_a_real_invoker_passes(self):
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.write(".github/workflows/w.yml",
                        "jobs:\n  s:\n    steps:\n"
                        "      - run: bash tests/linuxy/smoke.sh\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "invoked",
             "invoked_by": ".github/workflows/w.yml", "reason": "runs it"}]})
        self.assertEqual(result.problems, [], result.problems)
        self.assertEqual(result.invoked, 1)

    def test_a_glob_that_covers_the_directory_counts(self):
        """The natural way to run a set of scripts. A gate that demanded a
        literal path would push people towards listing them one by one, or
        towards the `bash -n` non-fix below."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.write(".github/workflows/w.yml",
                        "      - run: for f in tests/linuxy/*.sh; do bash $f; done\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "invoked",
             "invoked_by": ".github/workflows/w.yml", "reason": "glob"}]})
        self.assertEqual(result.problems, [], result.problems)

    def test_a_declared_invoker_that_does_not_mention_it_fails(self):
        """The declaration is a claim, and this is the part that checks it."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.write(".github/workflows/w.yml", "jobs:\n  s:\n    steps: []\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "invoked",
             "invoked_by": ".github/workflows/w.yml", "reason": "claimed"}]})
        self.assertTrue(any("does not mention it" in p for p in result.problems),
                        result.problems)

    def test_a_missing_invoker_fails(self):
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "invoked",
             "invoked_by": ".github/workflows/absent.yml", "reason": "x"}]})
        self.assertTrue(any("missing invoker" in p for p in result.problems),
                        result.problems)

    def test_a_pull_request_template_is_not_an_invoker(self):
        """Found by review, and it was the fatal one: the first version took
        any mention under `.github/` as invocation, so a checklist asking a
        human to run something satisfied the gate. That is the PR-template
        design this ADR rejected, accepted by accident."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.write(".github/pull_request_template.md",
                        "- [ ] ran tests/linuxy/smoke.sh\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "invoked",
             "invoked_by": ".github/pull_request_template.md",
             "reason": "checklist"}]})
        self.assertTrue(any("does not execute things" in p
                            for p in result.problems), result.problems)

    def test_prose_in_a_crew_config_is_not_an_invoker(self):
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.write("program/config.yaml", "run tests/linuxy/smoke.sh\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "invoked",
             "invoked_by": "program/config.yaml", "reason": "told to"}]})
        self.assertTrue(any("does not execute things" in p
                            for p in result.problems), result.problems)

    def test_debt_without_an_owning_issue_fails(self):
        """AC-3. Debt with no owner is a note, not a debt."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "debt",
             "reason": "someday", "issue": None}]})
        self.assertTrue(any("missing owning issue" in p for p in result.problems),
                        result.problems)

    def test_debt_with_an_owning_issue_passes(self):
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "debt",
             "reason": "not scheduled yet", "issue": 728}]})
        self.assertEqual(result.problems, [], result.problems)
        self.assertEqual(result.debt, 1)

    def test_a_structural_entry_must_say_why(self):
        """Otherwise `structural` is a free pass with a category name."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "structural",
             "reason": ""}]})
        self.assertTrue(any("must say why" in p for p in result.problems),
                        result.problems)

    def test_an_entry_with_no_category_fails(self):
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "reason": "?"}]})
        self.assertTrue(any("no valid category" in p for p in result.problems),
                        result.problems)

    def test_a_duplicate_entry_fails(self):
        """Two entries for one path let a debt hide behind an invoked twin."""
        self.repo.write("tests/linuxy/smoke.sh", "#!/bin/sh\nexit 0\n")
        self.repo.commit()
        result = self.repo.audit({"entries": [
            {"path": "tests/linuxy/smoke.sh", "category": "debt",
             "reason": "r", "issue": 728},
            {"path": "tests/linuxy/smoke.sh", "category": "debt",
             "reason": "r", "issue": 728}]})
        self.assertTrue(any("duplicate" in p for p in result.problems),
                        result.problems)

    def test_a_stale_entry_fails(self):
        """AC-2. Without this the manifest only ever grows, and a file that
        lists every past problem stops being read."""
        result = self.repo.audit({"entries": [
            {"path": "tests/gone.sh", "category": "debt", "reason": "fixed",
             "issue": 728}]})
        self.assertTrue(any("stale manifest entry" in p for p in result.problems),
                        result.problems)

    def test_a_fixture_is_not_a_test(self):
        """tests/fixtures/ holds sample projects that exist to be SCANNED. A
        Cypress spec in there executing nowhere is the point of the fixture."""
        self.repo.write("tests/fixtures/proj/test_thing.py", "x = 1\n")
        self.repo.commit()
        result = self.repo.audit({"entries": []})
        self.assertEqual(result.problems, [], result.problems)


class ThisRepositoryIsClean(unittest.TestCase):
    def test_the_real_manifest_audits_clean(self):
        data = guard.load_manifest()
        result = guard.audit(data)
        self.assertEqual(result.problems, [],
                         "\n".join(result.problems) + "\n" +
                         guard.summary_line(result))

    def test_the_pinned_counts(self):
        """`debt` may only go DOWN: a change that raises it has to edit this
        number, which is the moment a reviewer gets to ask why something new is
        unrunnable.

        Day one was (6, 2, 4, 0) -- four `tests/linux` scripts nothing ran.
        Issue #728 scheduled all four in `.github/workflows/linux-smoke.yml`
        after executing each one and confirming it passed, so the debt is now
        zero and they are declared `invoked`. That is the mechanism working
        rather than an exception to it: the count moved because the problem was
        fixed, and this line is where that had to be shown."""
        result = guard.audit(guard.load_manifest())
        self.assertEqual(
            (result.total, result.invoked, result.debt, result.structural),
            (6, 6, 0, 0), guard.summary_line(result))

    def test_the_four_linux_scripts_now_name_what_runs_them(self):
        """They were this manifest's original debt (#728). Each is now
        declared `invoked`, and `verify_declaration` -- exercised by the audit
        above -- checks the named workflow exists and mentions it, so this is
        not a relabelling."""
        data = guard.load_manifest()
        linux = {e["path"]: e for e in data["entries"]
                 if e["path"].startswith("tests/linux/")}
        self.assertEqual(len(linux), 4)
        for entry in linux.values():
            self.assertEqual(entry["category"], "invoked")
            self.assertEqual(entry["invoked_by"],
                             ".github/workflows/linux-smoke.yml")

    def test_discovery_agrees_with_the_suite_runner_exactly(self):
        """The guardrail must certify the suite that actually runs.

        Asserting that the source contains "loader.discover" was a grep, not a
        test: a hand-rolled `glob("test_*.py")` with that string in a comment
        would pass it, while behaving differently -- it would credit a module
        that cannot be imported as discovered, which is the precise bug this
        guardrail exists to catch. So compare the two answers instead.
        """
        import unittest as ut
        suite = ut.TestLoader().discover(str(REPO_ROOT / "tests"),
                                         pattern="test_*.py")

        def walk(s):
            for item in s:
                if isinstance(item, ut.TestSuite):
                    yield from walk(item)
                else:
                    yield type(item).__module__

        runner_sees = {m for m in walk(suite) if not m.startswith("unittest")}
        self.assertEqual(guard.discovered_modules(REPO_ROOT / "tests"),
                         runner_sees)


class ThePreflightCatalogueMatchesBothWays(unittest.TestCase):
    """The same disease in a neighbouring file: `check_d12` verified every
    catalogued entry exists in the script, and never the reverse."""

    def setUp(self):
        self.script = (REPO_ROOT / "deploy/preflight.sh").read_text()
        self.manifest = json.loads(
            (REPO_ROOT / "docs/prd/DOD-MANIFEST.json").read_text())

    def test_every_literal_check_in_the_script_is_catalogued(self):
        """AC-5, the direction that was missing."""
        catalogued = {e["id"] for e in self.manifest["preflight_checks"]}
        messages = dod_validate._preflight_check_messages(self.script)
        self.assertEqual(sorted(messages - catalogued), [])

    def test_the_extractor_finds_the_checks_that_are_there(self):
        """A reverse check that matched nothing would pass vacuously forever.
        78 of the script's 80 messages are literal; the other two are built by
        command substitution and are covered by the forward direction."""
        messages = dod_validate._preflight_check_messages(self.script)
        self.assertGreaterEqual(len(messages), 70, len(messages))

    def test_check_d12_itself_fails_when_a_check_is_uncatalogued(self):
        """AC-5 through the real verdict function, not a re-implementation of
        its comparison. Asserting the two sets match today would still pass
        with the guard deleted -- it tests the tree, not the check."""
        doctored = json.loads(json.dumps(self.manifest))
        messages = sorted(dod_validate._preflight_check_messages(self.script))
        dropped = messages[0]
        doctored["preflight_checks"] = [
            e for e in doctored["preflight_checks"] if e["id"] != dropped]
        ctx = dod_validate.Ctx(dod_validate.REPO_ROOT, doctored, None)
        verdict = dod_validate.check_d12(ctx)
        self.assertEqual(verdict.status, "FAIL", verdict)
        self.assertIn(dropped, verdict.reason)

    def test_check_d12_passes_on_the_real_manifest(self):
        """The negative control for the test above."""
        ctx = dod_validate.Ctx(dod_validate.REPO_ROOT, self.manifest, None)
        self.assertEqual(dod_validate.check_d12(ctx).status, "PASS")

    def test_a_command_substituted_message_is_not_demanded(self):
        text = 'bad "$(diagnose_it) failed"\nwarn "plain message"\n'
        found = dod_validate._preflight_check_messages(text)
        self.assertEqual(found, {"plain message"})


if __name__ == "__main__":
    unittest.main()
