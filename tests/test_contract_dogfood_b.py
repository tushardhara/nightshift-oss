"""Issue #443 -- Dogfood B: "a foreign stack, and the rejection matters more
than the success" (the issue, verbatim).

Unlike `test_project_contract.py`'s own inline `DogfoodBBoringForeignStack`
(a fixture synthesized in memory, string by string), this suite reads a
REAL, on-disk, version-controlled fixture: `tests/fixtures/dogfood_b_java/`.
Deliberately boring, exactly as the issue asks for: Java, Maven, Spring
Boot, JUnit, Cypress -- nothing exotic. The files live on disk so anyone
can `cat` or edit them directly instead of unescaping a Python string.

Two things this suite proves, and only two:

  1. The contract, compiled at the fixture's exact commit SHA, OBSERVES
     all five signals -- java, maven, spring boot, junit, cypress -- each
     with real, non-empty evidence paths.

  2. THE TEST THAT COUNTS (the issue's own framing): the contract's own
     evidence REFUSES a claim proposing Go and Playwright. No conformance
     gate exists yet (#443 explicitly ships facts only) -- this is an
     assertion over the contract's contents, written now, so a later
     gate inherits it rather than re-deriving the comparison.

     "A demo where everything happens to go right proves nothing. A
     recorded refusal proves the contract is load-bearing."

Ownership: this file and `tests/fixtures/dogfood_b_java/**` only. Nothing
here imports from or edits `test_project_contract.py`; the two suites are
independent covers of the same acceptance criterion, not one relying on
the other's fixture or helpers.

INTERFACE (`bin/nightshift_project_contract.py`), as exercised here:
    build_contract(repo_path: str, *, sha: str) -> dict
    open_tree(repo_path: str, sha: str) -> TreeAtSha        # context manager
    validate_contract(contract: dict, tree: TreeAtSha) -> None
    contradicts(contract: dict, claim: dict) -> list[str]
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import traceback
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MODULE_PATH = BIN / "nightshift_project_contract.py"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dogfood_b_java"

# Fixed on purpose: pinning the fixture commit's author/committer date means
# every materialization of IDENTICAL fixture content lands on the IDENTICAL
# SHA (git content-addressing does the rest) -- verified directly: two
# independent `_materialize_fixture()` calls in this suite compare equal.
# A wall-clock timestamp would make baseline_sha, and therefore
# contract_digest (which incorporates it), gratuitously different on every
# run for no reason connected to the fixture's actual content.
_FIXTURE_COMMIT_DATE = "2026-09-04T00:00:00+00:00"
_FIXTURE_AUTHOR = "Nightshift Contract Fixture"
_FIXTURE_EMAIL = "fixture@example.com"


_IMPORT_ERROR = None

def _load_contract_module():
    """Import bin/nightshift_project_contract.py by path. Returns None
    (never raises) if the module is missing or fails to import, so the
    suite can skip with a reason instead of erroring at collection.
    """
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "nightshift_project_contract", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- "not ready" is also not-an-error here
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {MODULE_PATH} failed to import: {exc}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


CONTRACT = _load_contract_module()
SKIP_REASON = (
    f"{MODULE_PATH} not implemented (or not currently importable) -- #443 Dogfood B. "
    f"This suite reads the real fixture at {FIXTURE_DIR}.")
FIXTURE_MISSING_REASON = f"fixture directory not found: {FIXTURE_DIR}"


def _materialize_fixture():
    """Copy the real, committed fixture tree at
    tests/fixtures/dogfood_b_java/ into a fresh tmpdir and give it ONE
    commit there. Discovery reads `git ls-tree`/`git cat-file` at an exact
    SHA -- never a bare directory -- so a real git repo is required, same
    rule `test_project_contract.py`'s `_write_fixture` follows; this one
    just copies real on-disk files instead of writing an inline dict of
    strings. `user.*` config and the commit date are set LOCAL to the
    fixture repo only, never `--global`, and the date is pinned -- see the
    module-level comment on `_FIXTURE_COMMIT_DATE`.

    Returns (root, sha). Cleanup is the caller's `addCleanup`.
    """
    if not FIXTURE_DIR.is_dir():
        raise AssertionError(FIXTURE_MISSING_REASON)
    root = tempfile.mkdtemp(prefix="nightshift-dogfoodb-fixture-")
    shutil.copytree(FIXTURE_DIR, root, dirs_exist_ok=True)
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": _FIXTURE_AUTHOR, "GIT_COMMITTER_NAME": _FIXTURE_AUTHOR,
        "GIT_AUTHOR_EMAIL": _FIXTURE_EMAIL, "GIT_COMMITTER_EMAIL": _FIXTURE_EMAIL,
        "GIT_AUTHOR_DATE": _FIXTURE_COMMIT_DATE, "GIT_COMMITTER_DATE": _FIXTURE_COMMIT_DATE,
    })
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", _FIXTURE_EMAIL],
        ["git", "config", "user.name", _FIXTURE_AUTHOR],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m",
         "dogfood b fixture (#443) -- java, maven, spring boot, junit, cypress"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True, env=env)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
        check=True, env=env,
    ).stdout.strip()
    return root, sha


def _build(root, sha):
    """Call the module's public entry point; assert the JSON-shape
    contract (every seat that reads this off disk gets it through
    json.loads, so a contract that didn't round-trip would be a defect
    only this check would catch).
    """
    contract = CONTRACT.build_contract(str(root), sha=sha)
    round_tripped = json.loads(json.dumps(contract))
    assert round_tripped == contract, "contract does not round-trip through json.dumps"
    return contract


def stack_is_consistent_with(contract: Dict[str, Any],
                              claim: Dict[str, Dict[str, Any]]) -> List[str]:
    """"Is this proposed stack consistent with the contract?" -- the
    helper the task asks for, in the words it asks for it in. A thin,
    intention-revealing wrapper around the module's own `contradicts()`
    rather than a second, competing comparison: `contradicts()` IS this
    answer (empty list == consistent), and it is the exact function the
    issue says a later conformance-gate child should inherit. Wrapping it
    here does not reimplement it.

    Returns the list of refusal reasons -- empty means consistent.
    """
    return CONTRACT.contradicts(contract, claim)


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
@unittest.skipUnless(FIXTURE_DIR.is_dir(), FIXTURE_MISSING_REASON)
class DogfoodBRealFixture(unittest.TestCase):
    """The real, on-disk boring stack: Java + Maven + Spring Boot + JUnit
    + Cypress. Compiled once per class at one fixed, real commit SHA.
    """

    @classmethod
    def setUpClass(cls):
        cls.root, cls.sha = _materialize_fixture()
        cls.contract = _build(cls.root, cls.sha)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    # -- sanity on the fixture itself: the five ingredients are actually
    # there on disk, committed, before anything is asked of the detector --

    def test_fixture_files_exist_on_disk(self):
        for rel in (
            "pom.xml", "mvnw",
            "src/main/java/com/example/dogfoodb/DogfoodBApplication.java",
            "src/test/java/com/example/dogfoodb/GreetingControllerTest.java",
            "cypress.config.ts", "cypress/e2e/greeting.cy.ts", "cypress/package.json",
        ):
            self.assertTrue((FIXTURE_DIR / rel).is_file(), f"missing fixture file: {rel}")

    def test_pom_declares_spring_boot_parent_and_junit_and_surefire(self):
        text = (FIXTURE_DIR / "pom.xml").read_text()
        self.assertIn("spring-boot-starter-parent", text)
        self.assertIn("junit-jupiter-engine", text)
        self.assertIn("maven-surefire-plugin", text)

    def test_cypress_package_json_declares_cypress(self):
        pkg = json.loads((FIXTURE_DIR / "cypress" / "package.json").read_text())
        self.assertIn("cypress", pkg.get("devDependencies", {}))

    # -- the contract compiled at this fixture's exact SHA --

    def test_contract_is_compiled_at_the_fixtures_exact_sha(self):
        self.assertEqual(self.contract["baseline_sha"], self.sha)

    def test_contract_passes_validate_contract_and_carries_a_digest(self):
        with CONTRACT.open_tree(self.root, self.sha) as tree:
            CONTRACT.validate_contract(self.contract, tree)  # must not raise
        self.assertTrue(self.contract["contract_digest"].startswith("sha256:"))

    def test_1_java_is_observed_as_the_unambiguous_primary_language(self):
        env = self.contract["languages"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertTrue(env["evidence"], "languages field populated with no evidence")
        self.assertIn("pom.xml", env["evidence"])
        value = env["value"]
        self.assertEqual(value["primary"], "java")
        self.assertFalse(value["ambiguous"], "the fixture is deliberately unambiguous")

    def test_2_maven_build_is_observed_with_pom_evidence(self):
        env = self.contract["build"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertTrue(env["evidence"])
        self.assertIn("pom.xml", env["evidence"])
        self.assertIn("mvn", env["value"]["command"])
        self.assertEqual(env["value"]["tool"], "maven")

    def test_3_spring_boot_is_observed_in_the_evidence_trail(self):
        """No dedicated top-level "framework" field exists in the schema
        -- Spring Boot sits atop Maven/Java, not a language or a bare
        command -- but it must still be a real, cited fact somewhere in
        the contract's evidence trail, not silently dropped. It surfaces
        in the `test` field's candidate notes, sourced from the same
        pom.xml `<parent>` block the maven-surefire-plugin candidate
        cites.
        """
        env = self.contract["test"]
        notes = " ".join(n for c in env["candidates"] for n in c["notes"])
        self.assertIn("spring boot", notes.lower())
        self.assertIn("pom.xml", notes)

    def test_4_junit_test_framework_is_observed_with_pom_evidence(self):
        env = self.contract["test"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertTrue(env["evidence"])
        self.assertIn("pom.xml", env["evidence"])
        self.assertEqual(env["value"]["framework"], "junit")

    def test_5_cypress_e2e_is_observed_with_config_evidence(self):
        env = self.contract["e2e"]
        self.assertEqual(env["status"], "observed",
                          "cypress.config.ts alone is source_kind config_file -- always "
                          "'observed' per the AC-3 table, not merely 'declared'")
        self.assertTrue(env["evidence"])
        # BOTH independent observations must survive in the trail.
        #
        # This asserted `cypress.config.ts` was in TOP-LEVEL evidence, which
        # held only while the config file was the ONLY thing seen. The CI
        # extractor could not read a `run:` step written as a YAML sequence
        # item (`- run: npx cypress run`), so the workflow was invisible. Now
        # that it is read, the CI candidate wins the envelope because it
        # carries the runnable COMMAND -- which is the point for a seat
        # resolving its toolchain -- and top-level evidence names the workflow.
        #
        # The config file is NOT dropped: it stays a corroborating candidate
        # with the same value. Asserting across the whole trail is strictly
        # stronger than the old form: it fails if EITHER observation vanishes,
        # where the old one watched only the config file.
        trail = set(env["evidence"])
        for cand in env.get("candidates", ()):
            trail.update(cand.get("evidence", ()))
        self.assertIn("cypress.config.ts", trail,
                      f"config-file observation dropped from the trail: {trail}")
        self.assertEqual(env["value"]["framework"], "cypress")

    # -- the assertion the issue names as the one that counts --

    def test_true_claim_is_not_falsely_refused(self):
        """Sanity check on `stack_is_consistent_with()` (== `contradicts()`)
        itself, before trusting its silence below: an ACCURATE claim about
        this exact fixture must not be flagged.
        """
        true_claim = {
            "languages": {"primary": "java"},
            "build": {"tool": "maven"},
            "test": {"framework": "junit"},
            "e2e": {"framework": "cypress"},
        }
        reasons = stack_is_consistent_with(self.contract, true_claim)
        self.assertEqual(reasons, [], f"a true claim was refused: {reasons}")

    def test_6_refuses_a_change_proposing_go_and_playwright(self):
        """THE test that counts, in the issue's own words: "assert the
        contract's own evidence would refuse a change proposing Go and
        Playwright ... A recorded refusal proves the contract is
        load-bearing."

        Two independent checks, deliberately kept apart: first that the
        contract genuinely holds no Go/Playwright evidence at all (so a
        refusal, if it happens, isn't a coincidence of how `contradicts()`
        happens to be wired); second that `stack_is_consistent_with()`
        actually raises the refusal, naming the real conflicting evidence.
        """
        # No evidence path anywhere in the contract mentions go or
        # playwright -- there is nothing here that COULD support the claim.
        full_evidence = json.dumps(self.contract).lower()
        self.assertNotIn("playwright", full_evidence,
                          "fixture evidence must not mention playwright at all")
        go_langs = {e["language"] for e in self.contract["languages"]["value"]["all"]}
        self.assertNotIn("go", go_langs, "fixture languages must not include go")

        claim = {"languages": {"primary": "go"}, "e2e": {"framework": "playwright"}}
        reasons = stack_is_consistent_with(self.contract, claim)

        self.assertTrue(reasons, "the contract's own evidence must refuse this claim")
        self.assertEqual(len(reasons), 2, f"expected exactly 2 refused keys, got: {reasons}")

        lang_reason = next((r for r in reasons if r.startswith("languages.primary")), None)
        e2e_reason = next((r for r in reasons if r.startswith("e2e.framework")), None)
        self.assertIsNotNone(lang_reason, f"no languages.primary refusal in: {reasons}")
        self.assertIsNotNone(e2e_reason, f"no e2e.framework refusal in: {reasons}")

        # Each refusal must NAME both sides of the disagreement...
        self.assertIn("java", lang_reason)
        self.assertIn("go", lang_reason)
        self.assertIn("cypress", e2e_reason)
        self.assertIn("playwright", e2e_reason)
        # ...and cite the real evidence path the contract holds, not a
        # bare assertion -- this is what makes it a RECORDED refusal.
        self.assertIn("pom.xml", lang_reason)
        # The e2e refusal must cite a REAL evidence path the contract actually
        # holds -- that is what makes it a recorded refusal rather than a bare
        # assertion, per the comment above. Which path wins is not the point
        # and is not stable: once the CI extractor could read
        # `- run: npx cypress run`, the workflow became the winning evidence
        # and `cypress.config.ts` became a corroborating candidate.
        #
        # So assert the property instead of the filename: the reason names at
        # least one path the contract genuinely holds for e2e. That is STRONGER
        # than hardcoding a name -- it fails on a fabricated or stale path,
        # which the old form could not detect.
        e2e_env = self.contract["e2e"]
        held = set(e2e_env["evidence"])
        for cand in e2e_env.get("candidates", ()):
            held.update(cand.get("evidence", ()))
        self.assertTrue(
            any(path in e2e_reason for path in held),
            f"the e2e refusal cites no evidence path the contract holds. "
            f"reason={e2e_reason!r} held={sorted(held)}")

    def test_7_a_partially_wrong_claim_is_refused_only_on_the_wrong_part(self):
        """A claim that gets languages right but e2e wrong must be
        refused on e2e alone -- `contradicts()` reports per-key, not an
        all-or-nothing verdict, so a real reviewer sees exactly which
        part of a proposal the contract disagrees with.
        """
        claim = {"languages": {"primary": "java"}, "e2e": {"framework": "playwright"}}
        reasons = stack_is_consistent_with(self.contract, claim)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertTrue(reasons[0].startswith("e2e.framework"), reasons)


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
@unittest.skipUnless(FIXTURE_DIR.is_dir(), FIXTURE_MISSING_REASON)
class DogfoodBFixtureIsReproducible(unittest.TestCase):
    """Not AC-7 itself (that generic determinism test lives in
    `test_project_contract.py`) -- this is the same property checked
    specifically against Dogfood B's own real files, independently, in
    this suite's own materialization path.
    """

    def test_two_independent_materializations_are_byte_identical(self):
        root1, sha1 = _materialize_fixture()
        self.addCleanup(shutil.rmtree, root1, ignore_errors=True)
        root2, sha2 = _materialize_fixture()
        self.addCleanup(shutil.rmtree, root2, ignore_errors=True)

        self.assertNotEqual(root1, root2, "two genuinely separate tmpdirs")
        self.assertEqual(sha1, sha2,
                          "identical fixture content + pinned commit date must land on "
                          "the identical content-addressed SHA")

        c1 = _build(root1, sha1)
        c2 = _build(root2, sha2)
        # `local.repo_path` legitimately differs -- it is the absolute
        # tmpdir path each materialization landed in, which is why
        # `canonical_bytes()` (what `contract_digest` is computed over)
        # deliberately excludes it (see DIGEST_EXCLUDED_TOP_LEVEL_KEYS in
        # nightshift_project_contract.py). Compare on that canonical form,
        # not the raw dict, for exactly that reason.
        self.assertEqual(
            CONTRACT.canonical_bytes(c1), CONTRACT.canonical_bytes(c2),
            "same fixture content, same SHA -- the contract's canonical form must be "
            "identical byte for byte, independent of which tmpdir it was read from")
        self.assertEqual(c1["contract_digest"], c2["contract_digest"])
        self.assertNotEqual(c1["local"]["repo_path"], c2["local"]["repo_path"],
                             "sanity check on the exclusion itself: the two tmpdirs really "
                             "were different paths, so this is a meaningful exclusion, not "
                             "a vacuous one")



# ---------------------------------------------------------------------------
# #695: a module that EXISTS but fails to import is a FAILURE, never a skip.
#
# The loader above returns None on any import error so the suite reports a
# reason at run time instead of erroring at collection.  That is right for a
# genuinely ABSENT module and wrong for a broken one: a syntax error in the
# module under test made every test here skip and the run report
# "OK (skipped=N)" -- CI green on code that cannot be imported.  Proven by
# appending invalid syntax to the module and watching this file report
# `Ran 25 tests ... OK (skipped=25)`.
#
# This guard restores the signal without reintroducing a collection error:
# absent module -> still skips, broken module -> one loud failure carrying
# the traceback.
# ---------------------------------------------------------------------------

class ModuleUnderTestImports(unittest.TestCase):

    def test_a_module_that_exists_must_import(self):
        if _IMPORT_ERROR is not None:
            self.fail(
                "the module under test exists but failed to import; every "
                "other test in this file skipped rather than ran:\n\n"
                + _IMPORT_ERROR)

if __name__ == "__main__":
    unittest.main()
