"""Issue #442 -- the conformance gate: refuse contract violations, do not
review them.

This suite exists because the builder's own report for #442 named a test
file, `test_conformance_gate.py`, with ~30 specific test methods and
per-guard mutation evidence -- and no such file was ever committed (nor
does it exist untracked, nor anywhere in `git log --all` for this branch).
Every "measured" claim in that report is therefore unverified by anything
except this suite, written after the fact against the code as it actually
stands at commit b267fa3.

INTERFACE (`bin/nightshift_conformance.py`), as exercised here:
    evaluate(*, base_contract, head_contract, rules) -> dict
    permitted_languages(base_contract, rules) -> frozenset
    permitted_test_frameworks(base_contract, rules) -> frozenset
    attach_advisory(decision, notes) -> dict          # raises AdvisoryEscalation
    evaluate_claim(*, base_contract, claim, rules) -> dict
    build_decision(repo_path, *, base_sha, head_sha, rules) -> dict
    load_rules(path) -> dict
    load_contract_file(path) -> dict
    main(argv) -> int                                  # CLI

Two levels of fixture are used deliberately:

  * Hand-built contract-envelope dicts, for pure unit tests of `evaluate`'s
    set-difference logic, `load_rules`' validation, and waiver semantics.
    These do not go through `validate_contract` (only `build_contract`
    does), so they only need to carry the specific keys the functions under
    test actually read -- documented per-helper below.

  * REAL, on-disk, one-or-two-commit git repositories compiled through the
    actual `nightshift_project_contract.build_contract()`, for the
    "epic worked example" (go + playwright), the benign-change case, and
    the base-pinning case -- so the load-bearing claim ("candidates survive
    `status: conflict`; `value` does not") is proven against genuine
    detector output, not a shape I asserted into existence.

MUTATION EVIDENCE lives inline as `_mutate`/`_restore` blocks per guard,
via `unittest.mock.patch` for pure-Python guard logic (loud, precise,
reverted automatically on context exit) and via literal source-line edits
(read -> str.replace -> write -> restore from a saved copy) for the two
guards the report specifically claims are proven "on the real fixture" --
`build_decision`'s base-pinning and `frameworks_of`'s conflict-survival --
so the same on-disk module the CLI subprocess actually runs is what gets
mutated for those two.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MODULE_PATH = BIN / "nightshift_conformance.py"
RULES_PATH = REPO / "program" / "conformance-rules.json"
WORKFLOW_PATH = REPO / ".github" / "workflows" / "tests.yml"

# This suite repeatedly overwrites bin/nightshift_conformance.py in place
# (_SourceMutation, below) and re-imports it. Python's import machinery
# caches compiled bytecode in bin/__pycache__ keyed on the source file's
# (mtime, size); a mutate-write / test / restore-write cycle that completes
# within one filesystem mtime tick can leave a STALE .pyc on disk whose
# (mtime, size) coincidentally matches the just-restored source, so a plain
# `import`/`importlib.reload()` silently serves the wrong bytecode instead
# of recompiling -- observed for real during this suite's own development:
# a fully-restored, verified-correct build_decision() kept running as its
# OWN AC-4 mutation (base compiled at head_sha) because of exactly this.
# Disabling bytecode writes and purging any pre-existing cache up front
# makes every import in this process a real compile of what's on disk.
sys.dont_write_bytecode = True
_STALE_CACHE = BIN / "__pycache__"
if _STALE_CACHE.is_dir():
    shutil.rmtree(_STALE_CACHE, ignore_errors=True)

# Direct import, deliberately with no try/except: if this module exists but
# fails to import, the whole test file must error loudly at collection --
# not skip silently. (Rule: no fail-soft test loaders.)
sys.path.insert(0, str(BIN))
import nightshift_conformance as gate  # noqa: E402
import nightshift_project_contract as pc  # noqa: E402


# ===========================================================================
# Fixture helpers -- real git repos (subprocess, mockless)
# ===========================================================================

def _write_repo(files):
    root = tempfile.mkdtemp(prefix="nightshift-conformance-fixture-")
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "fixture@example.com"],
        ["git", "config", "user.name", "Nightshift Conformance Fixture"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()
    return root, sha


def _commit(root, add_files=None, remove_paths=None, message="update"):
    """Materialise one more commit on top of whatever HEAD currently is in
    `root`. Returns the new commit's sha."""
    for rel, content in (add_files or {}).items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for rel in (remove_paths or []):
        os.remove(os.path.join(root, rel))
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True,
                    capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                           text=True, check=True).stdout.strip()


_JAVA_CLASS_TMPL = (
    "package com.example.svc;\n\npublic class Service{n} {{\n"
    "    public int compute(int x) {{ return x + {n}; }}\n}}\n"
)

_POM_XML = (
    "<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
    "  <modelVersion>4.0.0</modelVersion>\n"
    "  <parent>\n"
    "    <groupId>org.springframework.boot</groupId>\n"
    "    <artifactId>spring-boot-starter-parent</artifactId>\n"
    "    <version>3.2.0</version>\n"
    "  </parent>\n"
    "  <groupId>com.example</groupId>\n"
    "  <artifactId>gatefixture</artifactId>\n"
    "  <version>1.0.0</version>\n"
    "  <properties><maven.compiler.release>17</maven.compiler.release></properties>\n"
    "  <dependencies>\n"
    "    <dependency><groupId>org.springframework.boot</groupId>"
    "<artifactId>spring-boot-starter-web</artifactId></dependency>\n"
    "    <dependency><groupId>org.springframework.boot</groupId>"
    "<artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>\n"
    "    <dependency><groupId>org.junit.jupiter</groupId>"
    "<artifactId>junit-jupiter-engine</artifactId><scope>test</scope></dependency>\n"
    "  </dependencies>\n"
    "  <build><plugins>\n"
    "    <plugin><groupId>org.springframework.boot</groupId>"
    "<artifactId>spring-boot-maven-plugin</artifactId></plugin>\n"
    "    <plugin><groupId>org.apache.maven.plugins</groupId>"
    "<artifactId>maven-surefire-plugin</artifactId></plugin>\n"
    "  </plugins></build>\n"
    "</project>\n"
)


def _java_stack_files():
    """A boring, unambiguously-java-primary stack: Maven + JUnit (via
    Spring Boot's surefire plugin) + two e2e config files that DISAGREE
    with each other on purpose (cypress.config.js vs wdio.conf.js), so the
    e2e field resolves to `status: conflict` / `value: None` at the base --
    exactly the shape AC-2 requires to prove `frameworks_of()` reads
    `candidates`, not `value`.
    """
    files = {
        "pom.xml": _POM_XML,
        "mvnw": "#!/bin/sh\necho fixture mvnw\n",
        "src/main/java/com/example/App.java": (
            "package com.example;\npublic class App { "
            "public static void main(String[] a) {} }\n"),
        "src/test/java/com/example/AppTest.java": (
            "package com.example;\nimport org.junit.jupiter.api.Test;\n"
            "class AppTest { @Test void ok() {} }\n"),
        "cypress.config.js": "module.exports = { e2e: { baseUrl: 'http://x' } };\n",
        "cypress/e2e/smoke.cy.js": (
            "describe('smoke', () => { it('loads', () => { cy.visit('/'); }); });\n"),
        "wdio.conf.js": "exports.config = { specs: ['./test/specs/**'] };\n",
        ".github/workflows/ci.yml": (
            "name: gatefixture CI\non: [push]\njobs:\n  build:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: ./mvnw test\n      - run: npx cypress run\n"),
    }
    for n in range(1, 16):
        files[f"src/main/java/com/example/svc/Service{n}.java"] = _JAVA_CLASS_TMPL.format(n=n)
    return files


def _go_playwright_addition():
    return {
        "go.mod": "module fixture/head\n\ngo 1.21\n",
        "svc/main.go": "package main\n\nfunc main() {}\n",
        "playwright.config.js": "module.exports = { use: { baseURL: 'http://x' } };\n",
    }


REAL_RULES = gate.load_rules(str(RULES_PATH))


# ===========================================================================
# Hand-built contract envelopes -- for pure evaluate()/load_rules() unit
# tests that do not need a real git repo. These deliberately carry ONLY the
# keys evaluate()/languages_of()/frameworks_of() actually read; they are
# never passed through validate_contract (only build_contract() does that).
# ===========================================================================

def _absent(reason="not observed"):
    return {"status": "absent", "value": None, "confidence": None, "evidence": [],
            "reason": reason, "candidates": []}


def _lang_field(languages, evidence=None):
    return {
        "status": "observed", "confidence": "observed",
        "value": {"primary": languages[0] if languages else None, "ambiguous": False,
                  "all": [{"language": l, "share_ppm": 1_000_000 // max(len(languages), 1)}
                          for l in languages]},
        "evidence": evidence or ["dummy.manifest"], "reason": None, "candidates": [],
    }


def _fw_field(framework, evidence=None):
    if framework is None:
        return _absent("no framework observed")
    ev = evidence or [f"{framework}.config"]
    return {
        "status": "observed", "confidence": "observed",
        "value": {"framework": framework, "command": f"run {framework}"},
        "evidence": ev, "reason": None,
        "candidates": [{"value": {"framework": framework, "command": f"run {framework}"},
                        "evidence": ev, "source_kind": "config_file",
                        "confidence": "observed", "rank": 10, "notes": []}],
    }


def _fw_conflict_field(frameworks):
    cands = [{"value": {"framework": f, "command": f"run {f}"}, "evidence": [f"{f}.config"],
              "source_kind": "config_file", "confidence": "observed", "rank": 10, "notes": []}
             for f in frameworks]
    return {"status": "conflict", "confidence": None, "value": None,
            "evidence": sorted(f"{f}.config" for f in frameworks),
            "reason": "conflict: disagreement", "candidates": cands}


def _contract(baseline_sha, contract_digest=None, languages=(), test=None, integration=None,
              e2e=None):
    return {
        "baseline_sha": baseline_sha,
        "contract_digest": contract_digest or f"sha256:{baseline_sha}",
        "languages": _lang_field(list(languages)) if languages else _absent(),
        "test": test if test is not None else _absent(),
        "integration": integration if integration is not None else _absent(),
        "e2e": e2e if e2e is not None else _absent(),
    }


def _rules(rule_kinds=("no_undeclared_language", "no_undeclared_test_framework"), waivers=None):
    all_rules = {
        "no_undeclared_language": {"id": "R-001", "kind": "no_undeclared_language",
                                    "binding": True, "violation": "undeclared_language",
                                    "contract_fields": ["languages"]},
        "no_undeclared_test_framework": {"id": "R-002", "kind": "no_undeclared_test_framework",
                                          "binding": True, "violation": "undeclared_test_framework",
                                          "contract_fields": ["test", "integration", "e2e"]},
    }
    return {"schema_version": 1, "rules": [all_rules[k] for k in rule_kinds],
            "waivers": list(waivers or [])}


# ===========================================================================
# Mutation-testing helper -- edits the ACTUAL on-disk module, runs a given
# test class, and always restores, even if the test unexpectedly errors.
# ===========================================================================

class _SourceMutation:
    """Context manager: replace `old` with `new` (must appear exactly once)
    in bin/nightshift_conformance.py, for the duration of the `with` block.
    Restores the original file on exit unconditionally.

    Reloads the `gate` module from disk on BOTH entry (so the mutated code
    is what actually runs inside the `with` body) and exit (so the
    in-memory module reflects the restored file before the next test runs)
    -- both unconditionally, exception or not. Getting the exit-side reload
    wrong (e.g. only reloading inside the `with` body, never after
    restoring the file) leaves every later test in the process running
    against a permanently mutated module -- which is exactly what this
    class's first draft did, and which the alphabetical-first
    `test_MUTATION_*` methods in several classes below silently exposed by
    corrupting every subsequent test in the same run. Restore-then-reload
    as one atomic step in `__exit__` is what closes that hole.
    """

    def __init__(self, old: str, new: str):
        self.old = old
        self.new = new
        self._original = None

    def __enter__(self):
        self._original = MODULE_PATH.read_text(encoding="utf-8")
        count = self._original.count(self.old)
        assert count == 1, (
            f"expected exactly one occurrence of the mutation target, found {count}:\n"
            f"{self.old!r}")
        MODULE_PATH.write_text(self._original.replace(self.old, self.new), encoding="utf-8")
        _reload_gate()
        return self

    def __exit__(self, exc_type, exc, tb):
        MODULE_PATH.write_text(self._original, encoding="utf-8")
        _reload_gate()
        return False


def _run_cli(*args):
    proc = subprocess.run([sys.executable, str(MODULE_PATH), *args],
                           capture_output=True, text=True)
    body = None
    if proc.stdout.strip():
        try:
            body = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            body = None
    return proc.returncode, body, proc.stderr


class ModuleUnderTestImports(unittest.TestCase):
    def test_the_gate_module_and_its_dependency_both_imported(self):
        self.assertTrue(hasattr(gate, "evaluate"))
        self.assertTrue(hasattr(gate, "build_decision"))
        self.assertTrue(hasattr(pc, "build_contract"))


# ===========================================================================
# AC-5 -- evaluate() and the two permitted_* functions accept ONLY facts
# and rules.
# ===========================================================================

class AuthorityLayeringSignatures(unittest.TestCase):

    def test_evaluate_is_keyword_only_facts_and_rules_exactly(self):
        sig = inspect.signature(gate.evaluate)
        names = list(sig.parameters)
        self.assertEqual(names, ["base_contract", "head_contract", "rules"])
        for p in sig.parameters.values():
            self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY, p.name)
            self.assertIs(p.default, inspect.Parameter.empty, p.name)

    def test_permitted_languages_accepts_only_base_contract_and_rules(self):
        sig = inspect.signature(gate.permitted_languages)
        self.assertEqual(list(sig.parameters), ["base_contract", "rules"])
        for p in sig.parameters.values():
            self.assertNotIn(p.kind, (inspect.Parameter.VAR_POSITIONAL,
                                       inspect.Parameter.VAR_KEYWORD))
            self.assertIs(p.default, inspect.Parameter.empty, p.name)

    def test_permitted_test_frameworks_accepts_only_base_contract_and_rules(self):
        sig = inspect.signature(gate.permitted_test_frameworks)
        self.assertEqual(list(sig.parameters), ["base_contract", "rules"])
        for p in sig.parameters.values():
            self.assertNotIn(p.kind, (inspect.Parameter.VAR_POSITIONAL,
                                       inspect.Parameter.VAR_KEYWORD))
            self.assertIs(p.default, inspect.Parameter.empty, p.name)

    # --- mutation evidence ---
    def test_MUTATION_an_experience_parameter_added_to_permitted_test_frameworks_is_caught(self):
        """Defect: give permitted_test_frameworks a 4th, defaulted
        `experience` parameter (the plausible 'union in what we've seen
        before' implementation). The signature assertion above must fail."""
        old = "def permitted_test_frameworks(base_contract: Dict[str, Any], rules: Dict[str, Any]) -> FrozenSet[str]:"
        new = ("def permitted_test_frameworks(base_contract: Dict[str, Any], rules: Dict[str, Any], "
               "experience=()) -> FrozenSet[str]:")
        with _SourceMutation(old, new):
            with self.assertRaises(AssertionError):
                self.test_permitted_test_frameworks_accepts_only_base_contract_and_rules()


def _reload_gate():
    """Re-import the module fresh from disk after a _SourceMutation edit
    (or after restoring it) -- inspect.signature must see the file as it
    currently is, not a cached bytecode object."""
    import importlib
    importlib.reload(gate)


# ===========================================================================
# AC-1 / AC-2 / AC-3 / AC-11 -- the epic worked example, on a real fixture.
# ===========================================================================

class EpicWorkedExample(unittest.TestCase):
    """One base commit (Java/Maven/JUnit, e2e already a conflict between
    cypress and webdriverio), one head commit on top adding go.mod + a Go
    file + playwright.config.js. This is the real dogfood-B-style
    fixture the builder's report claims to have used; unlike that report,
    this one is actually committed and actually runs."""

    @classmethod
    def setUpClass(cls):
        cls.root, cls.base_sha = _write_repo(_java_stack_files())
        cls.head_sha = _commit(cls.root, add_files=_go_playwright_addition(), message="add go + playwright")
        cls.base_contract = pc.build_contract(cls.root, sha=cls.base_sha)
        cls.head_contract = pc.build_contract(cls.root, sha=cls.head_sha)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_base_e2e_field_is_genuinely_a_conflict(self):
        """Sanity check on the fixture itself, not the gate: if this ever
        stops being true the rest of this class is not testing what it
        claims to."""
        self.assertEqual(self.base_contract["e2e"]["status"], "conflict")
        self.assertIsNone(self.base_contract["e2e"]["value"])
        fws = {c["value"]["framework"] for c in self.base_contract["e2e"]["candidates"]}
        self.assertEqual(fws, {"cypress", "webdriverio"})

    def test_go_and_playwright_are_denied_together(self):
        decision = gate.evaluate(base_contract=self.base_contract,
                                  head_contract=self.head_contract, rules=REAL_RULES)
        self.assertEqual(decision["verdict"], "deny")
        self.assertEqual(decision["reason_code"], "project_contract_violation")
        kinds = {(v["kind"], v["value"]) for v in decision["violations"]}
        self.assertEqual(kinds, {("undeclared_language", "go"),
                                  ("undeclared_test_framework", "playwright")})

    def test_go_violation_carries_real_evidence_paths(self):
        decision = gate.evaluate(base_contract=self.base_contract,
                                  head_contract=self.head_contract, rules=REAL_RULES)
        lang_v = next(v for v in decision["violations"] if v["kind"] == "undeclared_language")
        self.assertIn("go.mod", lang_v["evidence"])

    def test_cli_end_to_end_denies_with_exit_code_3(self):
        code, body, stderr = _run_cli(self.root, "--base-sha", self.base_sha,
                                       "--head-sha", self.head_sha,
                                       "--rules", str(RULES_PATH), "--compact")
        self.assertEqual(code, 3, stderr)
        self.assertEqual(body["verdict"], "deny")
        kinds = {(v["kind"], v["value"]) for v in body["violations"]}
        self.assertEqual(kinds, {("undeclared_language", "go"),
                                  ("undeclared_test_framework", "playwright")})

    def test_a_benign_change_within_the_declared_stack_is_allowed(self):
        """AC-3: another Java file, another Cypress spec -- volume of
        change, not a widening of the declared stack."""
        benign_root, benign_sha = _write_repo(_java_stack_files())
        self.addCleanup(shutil.rmtree, benign_root, ignore_errors=True)
        benign_head = _commit(benign_root, add_files={
            "src/main/java/com/example/svc/ServiceExtra.java":
                _JAVA_CLASS_TMPL.format(n="Extra"),
            "cypress/e2e/second.cy.js":
                "describe('second', () => { it('also loads', () => {}); });\n",
        }, message="benign")
        decision = gate.build_decision(benign_root, base_sha=benign_sha, head_sha=benign_head,
                                        rules=REAL_RULES)
        self.assertEqual(decision["verdict"], "allow")
        self.assertEqual(decision["violations"], [])

    # --- mutation evidence: AC-2's load-bearing reading choice ---
    def test_MUTATION_reading_value_only_for_frameworks_hides_playwright_under_conflict(self):
        """Defect: frameworks_of() reads only `value["framework"]` (None
        under conflict) and ignores `candidates`. Both base and head e2e
        are `status: conflict` / `value: None` in this fixture, so a
        value-only reading sees {} vs {} on the e2e field and the
        undeclared_test_framework violation must disappear."""
        old = (
            "    if not field_env:\n"
            "        return frozenset()\n"
            "    names = set()\n"
            "    value = field_env.get(\"value\")\n"
            "    if isinstance(value, dict):\n"
            "        fw = value.get(\"framework\")\n"
            "        if fw:\n"
            "            names.add(str(fw).casefold())\n"
            "    for cand in field_env.get(\"candidates\") or []:\n"
            "        cand_value = cand.get(\"value\") if isinstance(cand, dict) else None\n"
            "        if isinstance(cand_value, dict):\n"
            "            fw = cand_value.get(\"framework\")\n"
            "            if fw:\n"
            "                names.add(str(fw).casefold())\n"
            "    return frozenset(names)\n"
        )
        new = (
            "    if not field_env:\n"
            "        return frozenset()\n"
            "    names = set()\n"
            "    value = field_env.get(\"value\")\n"
            "    if isinstance(value, dict):\n"
            "        fw = value.get(\"framework\")\n"
            "        if fw:\n"
            "            names.add(str(fw).casefold())\n"
            "    return frozenset(names)\n"
        )
        with _SourceMutation(old, new):
            decision = gate.evaluate(base_contract=self.base_contract,
                                      head_contract=self.head_contract, rules=REAL_RULES)
            kinds = {(v["kind"], v["value"]) for v in decision["violations"]}
            self.assertNotEqual(
                kinds, {("undeclared_language", "go"), ("undeclared_test_framework", "playwright")},
                "mutation did not change behaviour -- frameworks_of's candidates-reading "
                "is unproven by this test")
            self.assertNotIn(
                "undeclared_test_framework", {k for k, _v in kinds},
                "expected the playwright violation to vanish under the value-only defect")

    # --- AC-11: the claim path delegates to contradicts() ---
    def test_claim_path_is_denied_for_the_resolved_language_field(self):
        claim = {"languages": {"primary": "go"}, "e2e": {"framework": "playwright"}}
        result = gate.evaluate_claim(base_contract=self.base_contract, claim=claim, rules=REAL_RULES)
        self.assertEqual(result["verdict"], "deny")
        self.assertTrue(any("languages.primary" in r for r in result["violations"]))
        # e2e is a conflict field at the base -- contradicts() is documented
        # to see nothing there, so it must not be silently treated as
        # agreeing with the claim. Check for the FIELD-PREFIXED reason
        # ("e2e.framework: ...") rather than a bare "e2e" substring: the
        # languages reason's own evidence list legitimately contains
        # "cypress/e2e/smoke.cy.js", which would otherwise false-positive.
        self.assertFalse(any(r.startswith("e2e.") for r in result["violations"]))

    def test_claim_touching_only_the_conflicted_field_reports_it_as_unevaluated_not_allowed(self):
        """The property that actually matters: a claim the contract could
        not resolve must not read as consent. Isolate to ONLY the
        conflicted field so `contradicts()` genuinely returns []."""
        claim = {"e2e": {"framework": "playwright"}}
        result = gate.evaluate_claim(base_contract=self.base_contract, claim=claim, rules=REAL_RULES)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["claim_unevaluated_fields"], ["e2e.framework"])

    # --- mutation evidence for the unevaluated-fields guard ---
    def test_MUTATION_dropping_unevaluated_tracking_reads_silence_as_consent(self):
        old = (
            "    unevaluated: List[str] = []\n"
            "    for field, claimed_fields in claim.items():\n"
            "        if not isinstance(claimed_fields, dict):\n"
            "            continue\n"
            "        env = base_contract.get(field)\n"
            "        if not env or env.get(\"status\") not in (\"observed\", \"declared\"):\n"
            "            unevaluated.extend(f\"{field}.{k}\" for k in claimed_fields)\n"
            "            continue\n"
            "        actual_value = env.get(\"value\") or {}\n"
            "        for key in claimed_fields:\n"
            "            if actual_value.get(key) is None:\n"
            "                unevaluated.append(f\"{field}.{key}\")\n"
        )
        new = "    unevaluated: List[str] = []\n"
        with _SourceMutation(old, new):
            claim = {"e2e": {"framework": "playwright"}}
            result = gate.evaluate_claim(base_contract=self.base_contract, claim=claim,
                                          rules=REAL_RULES)
            self.assertEqual(result["claim_unevaluated_fields"], [],
                              "mutation should have silenced the unevaluated report")
            self.assertEqual(result["verdict"], "allow",
                              "and the caller now has no signal that this was unproven, "
                              "not actually permitted")


# ===========================================================================
# AC-2, the "absent from the base contract" reading. EpicWorkedExample above
# proves the `conflict` reading (base e2e is cypress-vs-webdriverio, value is
# None by construction, candidates carry the evidence). This class proves
# the OTHER reading of "a field the base contract never observed": status
# `absent`, not `conflict` -- the shape nightshift's own `integration` and
# `e2e` fields are in TODAY (verified directly against this repo's own
# compiled contract: both `absent`, never `conflict`). A field the base
# never observed must contribute nothing to what is permitted, so
# introducing it in the candidate is a violation like any other, not a
# skipped, unenforced field.
# ===========================================================================

def _java_only_no_e2e_files():
    """Java + Maven + JUnit (surefire, run from CI), with no e2e/integration
    tooling of any kind tracked -- so `test` resolves `declared`/`observed`
    but `integration` and `e2e` resolve `absent`, exactly like nightshift's
    own contract today. One pre-existing `.js` file (`webpack.config.js`)
    so a later `playwright.config.js` addition does not also register as a
    NEW language and muddy the assertion -- this class is about the
    framework rule's `absent` reading, not the language rule's."""
    return {
        "pom.xml": _POM_XML,
        "mvnw": "#!/bin/sh\necho fixture mvnw\n",
        "src/main/java/com/example/App.java": (
            "package com.example;\npublic class App { "
            "public static void main(String[] a) {} }\n"),
        "src/test/java/com/example/AppTest.java": (
            "package com.example;\nimport org.junit.jupiter.api.Test;\n"
            "class AppTest { @Test void ok() {} }\n"),
        "webpack.config.js": "module.exports = {};\n",
        ".github/workflows/ci.yml": (
            "name: gatefixture CI\non: [push]\njobs:\n  build:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: ./mvnw test\n"),
    }


class AbsentBaseFieldsStillBind(unittest.TestCase):

    def test_a_language_absent_from_the_base_contract_is_denied_on_introduction(self):
        base = _contract("b", languages=[])            # languages: absent at base
        head = _contract("h", languages=["go"])
        decision = gate.evaluate(base_contract=base, head_contract=head, rules=REAL_RULES)
        self.assertEqual(decision["verdict"], "deny")
        kinds = {(v["kind"], v["value"]) for v in decision["violations"]}
        self.assertEqual(kinds, {("undeclared_language", "go")})
        self.assertEqual(decision["not_applicable"], [])

    def test_a_test_framework_absent_from_the_base_contract_is_denied_on_introduction(self):
        """The epic's own motivating example: introducing Playwright must
        DENY even when the base's `e2e` field was never observed at all
        (status `absent`), not only when it was observed as a conflict."""
        base = _contract("b", languages=["python"])    # test/integration/e2e default absent
        head = _contract("h", languages=["python"], e2e=_fw_field("playwright"))
        decision = gate.evaluate(base_contract=base, head_contract=head, rules=REAL_RULES)
        self.assertEqual(decision["verdict"], "deny")
        kinds = {(v["kind"], v["value"]) for v in decision["violations"]}
        self.assertEqual(kinds, {("undeclared_test_framework", "playwright")})
        self.assertEqual(decision["not_applicable"], [])

    def test_only_the_field_that_actually_gained_a_framework_violates(self):
        """`test`, `integration`, and `e2e` are all absent at this base, same
        as `test_a_test_framework_absent...` above -- but only `integration`
        gains a framework in the head, so only that one violates."""
        base = _contract("b", languages=["python"])
        head = _contract("h", languages=["python"], integration=_fw_field("testcontainers"))
        decision = gate.evaluate(base_contract=base, head_contract=head, rules=REAL_RULES)
        self.assertEqual(decision["verdict"], "deny")
        kinds = {(v["kind"], v["value"]) for v in decision["violations"]}
        self.assertEqual(kinds, {("undeclared_test_framework", "testcontainers")})

    def test_cli_end_to_end_denies_playwright_against_a_real_absent_e2e_field(self):
        """Real repo, real detector, real CLI subprocess -- not a hand-built
        envelope. Confirms the fix on-disk, not just the in-process
        `evaluate()` unit above."""
        root, base_sha = _write_repo(_java_only_no_e2e_files())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        base_contract = pc.build_contract(root, sha=base_sha)
        self.assertEqual(base_contract["integration"]["status"], "absent")
        self.assertEqual(base_contract["e2e"]["status"], "absent")
        head_sha = _commit(root, add_files={
            "playwright.config.js": "module.exports = { use: { baseURL: 'http://x' } };\n",
        }, message="add playwright")
        code, body, stderr = _run_cli(root, "--base-sha", base_sha, "--head-sha", head_sha,
                                       "--rules", str(RULES_PATH), "--compact")
        self.assertEqual(code, 3, stderr)
        self.assertEqual(body["verdict"], "deny")
        kinds = {(v["kind"], v["value"]) for v in body["violations"]}
        self.assertEqual(kinds, {("undeclared_test_framework", "playwright")})
        self.assertEqual(body["not_applicable"], [])

    # --- mutation evidence ---
    def test_MUTATION_reintroducing_the_absent_bailout_for_languages_lets_a_new_language_through(self):
        old = (
            "    base_env = base_contract.get(_LANGUAGE_FIELD)\n"
            "    head_env = head_contract.get(_LANGUAGE_FIELD)\n"
            "\n"
            "    # No `status == \"absent\"` bailout here: `languages_of()` already reads\n"
            "    # an absent field as contributing the empty set (see its own\n"
            "    # docstring), which is precisely what makes a language introduced\n"
            "    # against a never-observed base register as NEW and DENY. Treating\n"
            "    # `absent` as \"not_applicable\" instead -- skipping the diff entirely --\n"
            "    # would silently ALLOW the first-ever introduction of a language nobody\n"
            "    # declared, which is the exact violation this rule exists to catch.\n"
            "    base_langs = languages_of(base_env)\n"
        )
        new = (
            "    base_env = base_contract.get(_LANGUAGE_FIELD)\n"
            "    head_env = head_contract.get(_LANGUAGE_FIELD)\n"
            "\n"
            "    if base_env is not None and base_env.get(\"status\") == \"absent\":\n"
            "        return [], [{\"rule_id\": rule[\"id\"], \"field\": _LANGUAGE_FIELD,\n"
            "                      \"reason\": base_env.get(\"reason\")}]\n"
            "\n"
            "    base_langs = languages_of(base_env)\n"
        )
        with _SourceMutation(old, new):
            with self.assertRaises(AssertionError):
                self.test_a_language_absent_from_the_base_contract_is_denied_on_introduction()

    def test_MUTATION_reintroducing_the_absent_bailout_for_frameworks_lets_playwright_through(self):
        old = (
            "        head_envs[field] = head_env\n"
            "\n"
            "        # No `status == \"absent\"` bailout here -- same reasoning as\n"
            "        # `_language_violations` above. `frameworks_of()` already reads an\n"
            "        # absent field as contributing the empty set, so a framework\n"
            "        # introduced against a never-observed base (e.g. `e2e` absent, head\n"
            "        # adds Playwright) registers as NEW and DENIES. Skipping the field\n"
            "        # as \"not_applicable\" instead was this module's one hole: on\n"
            "        # nightshift's own contract `integration` and `e2e` are both\n"
            "        # absent, so it let through the epic's own motivating example.\n"
            "        base_fws = frameworks_of(base_env)\n"
        )
        new = (
            "        head_envs[field] = head_env\n"
            "\n"
            "        if base_env is not None and base_env.get(\"status\") == \"absent\":\n"
            "            not_applicable.append({\"rule_id\": rule[\"id\"], \"field\": field,\n"
            "                                    \"reason\": base_env.get(\"reason\")})\n"
            "            continue\n"
            "\n"
            "        base_fws = frameworks_of(base_env)\n"
        )
        with _SourceMutation(old, new):
            with self.assertRaises(AssertionError):
                self.test_a_test_framework_absent_from_the_base_contract_is_denied_on_introduction()


# ===========================================================================
# AC-4 -- FACTS are read at the BASE sha only.
# ===========================================================================

class FactsArePinnedToTheBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root, cls.base_sha = _write_repo(_java_stack_files())
        cls.head_sha = _commit(
            cls.root,
            add_files={"go.mod": "module fixture/onlygo\n\ngo 1.21\n",
                       "main.go": "package main\n\nfunc main() {}\n"},
            remove_paths=["pom.xml", "mvnw", "cypress.config.js", "wdio.conf.js",
                          "cypress/e2e/smoke.cy.js", "src/main/java/com/example/App.java",
                          "src/test/java/com/example/AppTest.java",
                          ".github/workflows/ci.yml"]
            + [f"src/main/java/com/example/svc/Service{n}.java" for n in range(1, 16)],
            message="delete the declared stack, keep only go",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_deleting_the_manifest_does_not_widen_the_permitted_set(self):
        decision = gate.build_decision(self.root, base_sha=self.base_sha, head_sha=self.head_sha,
                                        rules=REAL_RULES)
        self.assertEqual(decision["verdict"], "deny")
        self.assertTrue(any(v["kind"] == "undeclared_language" and v["value"] == "go"
                             for v in decision["violations"]))

    def test_vacuous_comparison_is_refused_not_silently_allowed(self):
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.build_decision(self.root, base_sha=self.base_sha, head_sha=self.base_sha,
                                 rules=REAL_RULES)
        self.assertEqual(ctx.exception.reason_code, "vacuous_comparison")
        self.assertEqual(ctx.exception.exit_code, 4)

    # --- mutation evidence ---
    def test_MUTATION_compiling_base_at_head_sha_degrades_to_indeterminate_not_a_silent_allow(self):
        """Defect (exactly as the plan predicted): compile the base
        contract at head_sha instead of base_sha inside build_decision.
        Both contracts then compile from the SAME tree, so
        baseline_sha collapses to head_sha on both sides -- which the
        INDEPENDENT vacuous_comparison guard (evaluate()'s own base_sha ==
        head_sha check on the CONTRACT's own baseline_sha field, not the
        caller's arguments) catches. Confirms build_decision has no path
        to a silent ALLOW here, whether or not the second guard exists --
        and separately proves the second guard is real, not decorative."""
        old = (
            "    base_contract = _compile_contract(repo_path, base_sha)\n"
            "    head_contract = _compile_contract(repo_path, head_sha)\n"
        )
        new = (
            "    base_contract = _compile_contract(repo_path, head_sha)\n"
            "    head_contract = _compile_contract(repo_path, head_sha)\n"
        )
        with _SourceMutation(old, new):
            with self.assertRaises(gate.ConformanceError) as ctx:
                gate.build_decision(self.root, base_sha=self.base_sha, head_sha=self.head_sha,
                                     rules=REAL_RULES)
            self.assertNotEqual(
                ctx.exception.reason_code, None,
                "build_decision must not silently return an allow decision under this defect")
            self.assertIn(ctx.exception.exit_code, (3, 4))
            # The specific claim under test: it degrades to indeterminate
            # (never to a bare allow), via the independent baseline_sha check.
            self.assertEqual(ctx.exception.reason_code, "vacuous_comparison")


# ===========================================================================
# AC-6 -- advisory content cannot widen a decision.
# ===========================================================================

class AdvisoryCannotWidenADecision(unittest.TestCase):

    def setUp(self):
        base = _contract("base-sha", languages=["java"], e2e=_fw_field("cypress"))
        head = _contract("head-sha", languages=["java", "go"], e2e=_fw_field("cypress"))
        self.decision = gate.evaluate(base_contract=base, head_contract=head, rules=_rules())
        self.assertEqual(self.decision["verdict"], "deny")  # sanity on the fixture

    def test_attach_advisory_leaves_verdict_violations_and_digest_untouched(self):
        notes = [{"note": "we've seen go used here before, seemed fine"}]
        updated = gate.attach_advisory(self.decision, notes)
        self.assertEqual(updated["verdict"], self.decision["verdict"])
        self.assertEqual(updated["violations"], self.decision["violations"])
        self.assertEqual(updated["decision_digest"], self.decision["decision_digest"])
        self.assertEqual(updated["advisory"], notes)

    # --- mutation evidence ---
    def test_MUTATION_dropping_a_violation_matching_advisory_text_raises_AdvisoryEscalation(self):
        """Defect: the plausible-sounding 'we've seen this here before, so
        don't flag it' implementation -- filter out any violation whose
        `value` appears in an advisory note's text. Because this changes
        `violations` out from under the digest attach_advisory itself
        recomputes, it must raise AdvisoryEscalation rather than quietly
        returning a laundered decision."""
        old = (
            "    updated = dict(decision)\n"
            "    updated[\"advisory\"] = [dict(n) if isinstance(n, dict) else n for n in notes]\n"
        )
        new = (
            "    updated = dict(decision)\n"
            "    updated[\"advisory\"] = [dict(n) if isinstance(n, dict) else n for n in notes]\n"
            "    _text = \" \".join(str(n) for n in notes).casefold()\n"
            "    updated[\"violations\"] = [v for v in updated.get(\"violations\", [])\n"
            "                              if str(v.get(\"value\", \"\")).casefold() not in _text]\n"
        )
        with _SourceMutation(old, new):
            notes = [{"note": "we've seen go used here before, seemed fine"}]
            with self.assertRaises(gate.AdvisoryEscalation):
                gate.attach_advisory(self.decision, notes)


# ===========================================================================
# AC-7 -- RULES are binding by construction: an unrecognised kind or a
# binding:false rule is refused, never skipped.
# ===========================================================================

class RulesAreBindingByConstruction(unittest.TestCase):

    def _write_rules_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_the_real_shipped_rules_file_loads_cleanly(self):
        rules = gate.load_rules(str(RULES_PATH))
        self.assertEqual(rules["schema_version"], 1)
        self.assertEqual({r["kind"] for r in rules["rules"]},
                          {"no_undeclared_language", "no_undeclared_test_framework"})
        self.assertEqual(rules["waivers"], [])

    def test_unknown_rule_kind_is_refused_not_skipped(self):
        path = self._write_rules_file({
            "schema_version": 1,
            "rules": [{"id": "R-X", "kind": "no_undeclared_mystery_thing", "binding": True,
                       "violation": "undeclared_mystery_thing", "contract_fields": ["languages"]}],
            "waivers": [],
        })
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.load_rules(path)
        self.assertEqual(ctx.exception.reason_code, "rules_invalid")
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_a_rule_declaring_itself_non_binding_is_refused(self):
        path = self._write_rules_file({
            "schema_version": 1,
            "rules": [{"id": "R-001", "kind": "no_undeclared_language", "binding": False,
                       "violation": "undeclared_language", "contract_fields": ["languages"]}],
            "waivers": [],
        })
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.load_rules(path)
        self.assertEqual(ctx.exception.reason_code, "rules_invalid")

    # --- mutation evidence ---
    def test_MUTATION_skipping_unknown_kinds_instead_of_refusing(self):
        old = (
            "        known = KNOWN_RULE_KINDS.get(kind)\n"
            "        if known is None:\n"
            "            # Never `continue` here: a rule kind this module does not\n"
            "            # recognise must stop the run, not be silently dropped -- a\n"
            "            # silently-skipped rule is the \"sign nobody enforces\" failure\n"
            "            # in miniature.\n"
            "            raise _rules_invalid(path, \"unrecognised rule kind\", rule_id=rule_id, kind=kind)\n"
        )
        new = (
            "        known = KNOWN_RULE_KINDS.get(kind)\n"
            "        if known is None:\n"
            "            continue\n"
        )
        with _SourceMutation(old, new):
            with self.assertRaises(AssertionError):
                self.test_unknown_rule_kind_is_refused_not_skipped()

    def test_MUTATION_accepting_binding_false(self):
        old = (
            "        binding = rule.get(\"binding\")\n"
            "        if binding is not True:\n"
        )
        new = (
            "        binding = rule.get(\"binding\")\n"
            "        if binding is None:\n"
        )
        with _SourceMutation(old, new):
            with self.assertRaises(AssertionError):
                self.test_a_rule_declaring_itself_non_binding_is_refused()


# ===========================================================================
# AC-8 -- waivers require an ADR reference and permit only the exact
# {kind, value} pair they name.
# ===========================================================================

class WaiversSupersedeOnlyWhatTheyName(unittest.TestCase):

    def _write_rules_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_waiver_without_adr_reference_is_refused(self):
        path = self._write_rules_file({
            "schema_version": 1,
            "rules": [{"id": "R-001", "kind": "no_undeclared_language", "binding": True,
                       "violation": "undeclared_language", "contract_fields": ["languages"]}],
            "waivers": [{"kind": "undeclared_language", "value": "go"}],
        })
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.load_rules(path)
        self.assertEqual(ctx.exception.reason_code, "rules_invalid")

    def test_a_waiver_permits_only_the_value_it_names(self):
        base = _contract("b", languages=["java"])
        head = _contract("h", languages=["java", "go", "python"])
        rules = _rules(rule_kinds=("no_undeclared_language",),
                        waivers=[{"adr": "ADR-0001", "kind": "undeclared_language", "value": "go"}])
        decision = gate.evaluate(base_contract=base, head_contract=head, rules=rules)
        self.assertEqual(decision["verdict"], "deny")
        values = {v["value"] for v in decision["violations"]}
        self.assertEqual(values, {"python"})  # go waived, python is not

    # --- mutation evidence ---
    def test_MUTATION_waiver_without_adr_check_removed_lets_it_through(self):
        old = (
            "            adr = waiver.get(\"adr\")\n"
            "            if not adr or not isinstance(adr, str):\n"
            "                # AC-8: a waiver without an ADR reference is refused outright.\n"
            "                raise _rules_invalid(path, \"waiver is missing an 'adr' reference\", waiver=waiver)\n"
        )
        # Fix indentation to match the real file (validated below via replace count==1)
        old = old.replace("            ", "        ")
        new = "        adr = waiver.get(\"adr\")\n"
        path = self._write_rules_file({
            "schema_version": 1,
            "rules": [{"id": "R-001", "kind": "no_undeclared_language", "binding": True,
                       "violation": "undeclared_language", "contract_fields": ["languages"]}],
            "waivers": [{"kind": "undeclared_language", "value": "go"}],
        })
        with _SourceMutation(old, new):
            rules = gate.load_rules(path)  # must not raise under the defect
            self.assertEqual(rules["waivers"][0]["value"], "go")

    def test_MUTATION_a_waiver_treated_as_permitting_its_whole_kind(self):
        """Defect: `_is_waived` returns True for any waiver matching `kind`,
        ignoring `value` -- a waiver for 'go' would then also permit
        'python'."""
        old = (
            "    value_cf = str(value).casefold()\n"
            "    for w in waivers:\n"
            "        if w[\"kind\"] == kind and w[\"value\"].casefold() == value_cf:\n"
            "            return True\n"
            "    return False\n"
        )
        new = (
            "    for w in waivers:\n"
            "        if w[\"kind\"] == kind:\n"
            "            return True\n"
            "    return False\n"
        )
        with _SourceMutation(old, new):
            base = _contract("b", languages=["java"])
            head = _contract("h", languages=["java", "go", "python"])
            rules = _rules(rule_kinds=("no_undeclared_language",),
                            waivers=[{"adr": "ADR-0001", "kind": "undeclared_language",
                                      "value": "go"}])
            decision = gate.evaluate(base_contract=base, head_contract=head, rules=rules)
            values = {v["value"] for v in decision["violations"]}
            self.assertEqual(
                values, set(),
                "under the defect the 'go' waiver incorrectly also waives 'python'")


# ===========================================================================
# AC-9 -- every condition under which the gate cannot see what it is
# judging returns indeterminate (exit 4), never allow.
# ===========================================================================

class CannotCheckIsNotAllow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root, cls.sha = _write_repo(_java_stack_files())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_an_unresolvable_sha_is_indeterminate_never_allow(self):
        fake_sha = "b" * 40
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.build_decision(self.root, base_sha=self.sha, head_sha=fake_sha, rules=REAL_RULES)
        self.assertEqual(ctx.exception.reason_code, "sha_not_present")
        self.assertEqual(ctx.exception.exit_code, 4)

    def test_a_missing_rules_file_is_indeterminate(self):
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.load_rules("/no/such/rules/file.json")
        self.assertEqual(ctx.exception.reason_code, "rules_unreadable")
        self.assertEqual(ctx.exception.exit_code, 4)

    def test_base_equals_head_is_indeterminate_not_a_free_allow(self):
        base = _contract("same-sha", languages=["java"])
        head = _contract("same-sha", languages=["java"])
        with self.assertRaises(gate.ConformanceError) as ctx:
            gate.evaluate(base_contract=base, head_contract=head, rules=_rules())
        self.assertEqual(ctx.exception.reason_code, "vacuous_comparison")
        self.assertEqual(ctx.exception.exit_code, 4)

    def test_contract_invalid_from_the_detector_is_translated_to_indeterminate(self):
        """The detector's own ContractInvalid is a real, exported exception
        (nightshift_project_contract.ContractInvalid) -- this exercises
        `_compile_contract`'s translation of it, which is the boundary this
        module actually owns. Provoking ContractInvalid itself from a real
        repo would require corrupting detector internals, so this uses
        `unittest.mock.patch` on the specific call site rather than a
        hand-built repo -- the one place in this suite that mocks, and it
        mocks the DEPENDENCY the gate is honestly documented to call, not
        the gate's own logic."""
        with mock.patch.object(gate.pc, "build_contract",
                                side_effect=pc.ContractInvalid("synthetic for this test")):
            with self.assertRaises(gate.ConformanceError) as ctx:
                gate.build_decision(self.root, base_sha=self.sha, head_sha=self.sha,
                                     rules=REAL_RULES)
        self.assertEqual(ctx.exception.reason_code, "contract_invalid")
        self.assertEqual(ctx.exception.exit_code, 4)

    # --- mutation evidence ---
    def test_MUTATION_catching_and_allowing_on_sha_not_present(self):
        old = (
            "def build_decision(repo_path: str, *, base_sha: str, head_sha: str,\n"
            "                    rules: Dict[str, Any]) -> Dict[str, Any]:\n"
            "    \"\"\"Compile the base and candidate contracts fresh, at their own exact\n"
            "    SHAs, and evaluate. FACTS are pinned to the base SHA specifically --\n"
            "    the base contract is never compiled at head_sha (AC-4).\"\"\"\n"
            "    base_contract = _compile_contract(repo_path, base_sha)\n"
            "    head_contract = _compile_contract(repo_path, head_sha)\n"
            "    return evaluate(base_contract=base_contract, head_contract=head_contract, rules=rules)\n"
        )
        new = (
            "def build_decision(repo_path: str, *, base_sha: str, head_sha: str,\n"
            "                    rules: Dict[str, Any]) -> Dict[str, Any]:\n"
            "    base_contract = _compile_contract(repo_path, base_sha)\n"
            "    try:\n"
            "        head_contract = _compile_contract(repo_path, head_sha)\n"
            "    except ConformanceError:\n"
            "        return {\"ok\": True, \"verdict\": \"allow\", \"reason_code\": None,\n"
            "                \"violations\": [], \"not_applicable\": [], \"advisory\": []}\n"
            "    return evaluate(base_contract=base_contract, head_contract=head_contract, rules=rules)\n"
        )
        with _SourceMutation(old, new):
            with self.assertRaises(AssertionError):
                self.test_an_unresolvable_sha_is_indeterminate_never_allow()


# ===========================================================================
# AC-10 -- a contract supplied as a file is digest-verified before it is
# trusted, and checked against the SHA it was supplied for.
# ===========================================================================

class SuppliedContractsAreVerified(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root, cls.base_sha = _write_repo(_java_stack_files())
        cls.head_sha = _commit(cls.root, add_files=_go_playwright_addition(), message="head")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _dump_contract(self, sha):
        contract = pc.build_contract(self.root, sha=sha)
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(contract, fh)
        self.addCleanup(os.remove, path)
        return path, contract

    def test_untampered_supplied_contracts_reproduce_the_same_deny(self):
        base_path, _ = self._dump_contract(self.base_sha)
        head_path, _ = self._dump_contract(self.head_sha)
        code, body, stderr = _run_cli(
            self.root, "--base-sha", self.base_sha, "--head-sha", self.head_sha,
            "--rules", str(RULES_PATH), "--contract-base", base_path,
            "--contract-head", head_path, "--compact")
        self.assertEqual(code, 3, stderr)
        self.assertEqual(body["verdict"], "deny")

    def test_a_tampered_contract_file_is_refused(self):
        base_path, contract = self._dump_contract(self.base_sha)
        tampered = copy.deepcopy(contract)
        tampered["languages"]["value"]["all"].append({"language": "go", "share_ppm": 1})
        with open(base_path, "w", encoding="utf-8") as fh:
            json.dump(tampered, fh)  # contract_digest left stale on purpose
        code, body, stderr = _run_cli(
            self.root, "--base-sha", self.base_sha, "--head-sha", self.head_sha,
            "--rules", str(RULES_PATH), "--contract-base", base_path, "--compact")
        self.assertEqual(code, 4, stderr)
        self.assertEqual(body["reason_code"], "contract_digest_mismatch")

    def test_a_digest_valid_contract_supplied_for_the_wrong_sha_is_refused(self):
        base_path, _ = self._dump_contract(self.base_sha)
        # Real, digest-valid contract -- just compiled at the WRONG sha for
        # what this invocation claims --base-sha is.
        code, body, stderr = _run_cli(
            self.root, "--base-sha", self.head_sha, "--head-sha", self.head_sha,
            "--rules", str(RULES_PATH), "--contract-base", base_path, "--compact")
        self.assertEqual(code, 4, stderr)
        self.assertEqual(body["reason_code"], "contract_invalid")

    # --- mutation evidence ---
    def test_MUTATION_skipping_digest_recompute_would_allow_a_tampered_contract(self):
        old = (
            "    claimed = contract.get(\"contract_digest\") if isinstance(contract, dict) else None\n"
            "    recomputed = None\n"
            "    if isinstance(contract, dict):\n"
            "        recomputed = \"sha256:\" + hashlib.sha256(pc.canonical_bytes(contract)).hexdigest()\n"
            "    if not claimed or claimed != recomputed:\n"
            "        raise _indeterminate(\"contract_digest_mismatch\", {\"path\": str(path)})\n"
        )
        new = (
            "    claimed = contract.get(\"contract_digest\") if isinstance(contract, dict) else None\n"
            "    if not claimed:\n"
            "        raise _indeterminate(\"contract_digest_mismatch\", {\"path\": str(path)})\n"
        )
        base_path, contract = self._dump_contract(self.base_sha)
        tampered = copy.deepcopy(contract)
        tampered["languages"]["value"]["all"].append({"language": "go", "share_ppm": 1})
        with open(base_path, "w", encoding="utf-8") as fh:
            json.dump(tampered, fh)
        with _SourceMutation(old, new):
            loaded = gate.load_contract_file(base_path)  # must NOT raise under the defect
            self.assertIn("go", [e["language"] for e in loaded["languages"]["value"]["all"]],
                           "the tampered field must have been accepted under this defect")


# ===========================================================================
# CI wiring -- the gate actually runs on every pull request.
# ===========================================================================

class TheGateIsWiredIntoCI(unittest.TestCase):

    def setUp(self):
        self.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    STEP_HEADER = "      - name: project conformance gate"

    def _assert_gate_is_wired(self, workflow_text):
        """The four things that must be true of the workflow for the gate to
        actually run. Takes the text as an argument so the mutation test can
        hand it a deliberately broken version without touching any file."""
        self.assertIn("nightshift_conformance.py", workflow_text)
        self.assertIn("if: github.event_name == 'pull_request'", workflow_text)
        self.assertIn("git merge-base", workflow_text)
        self.assertIn("program/conformance-rules.json", workflow_text)

    @staticmethod
    def _without_the_gate_step(workflow_text):
        """Return the workflow with the conformance-gate step removed, built
        deterministically from the text in hand.

        Deliberately NOT `git show main:.github/workflows/tests.yml`. Issue
        #698: a test must not depend on state git does not carry. A CI
        checkout has no local `main` ref, so that command exits 128 and the
        test ERRORS rather than proving anything -- which is exactly how this
        test failed on its own pull request. The mutated state is constructed
        here instead, so local and CI exercise identical input."""
        head, sep, rest = workflow_text.partition(
            TheGateIsWiredIntoCI.STEP_HEADER)
        if not sep:
            raise AssertionError(
                "cannot build the mutant: the conformance-gate step header "
                f"{TheGateIsWiredIntoCI.STEP_HEADER!r} is not in the workflow")
        # Drop everything up to the next step at the same indent, so the rest
        # of the job survives and the assertion failure can only be about the
        # gate step.
        marker = "\n      - name: "
        nxt = rest.find(marker)
        return head + (rest[nxt + 1:] if nxt != -1 else "")

    def test_ci_runs_the_conformance_gate_on_every_pull_request(self):
        self._assert_gate_is_wired(self.workflow)

    def test_MUTATION_removing_the_step_is_caught(self):
        mutant = self._without_the_gate_step(self.workflow)
        # The mutant really is the step-removed state, and nothing else.
        self.assertNotIn("nightshift_conformance.py", mutant)
        self.assertIn("runs-on:", mutant)
        # No file was written: the real workflow is untouched by this test.
        self.assertEqual(WORKFLOW_PATH.read_text(encoding="utf-8"),
                         self.workflow)
        with self.assertRaises(AssertionError):
            self._assert_gate_is_wired(mutant)


if __name__ == "__main__":
    unittest.main()
