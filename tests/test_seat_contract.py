"""Tests for bin/nightshift_seat_contract.py -- the missing consumer that
binds a seat's toolchain to the COMPILED PROJECT CONTRACT (#443) instead of
to config prose (#442/ADR-0086 epic child).

INTERFACE, as exercised here:
    resolve(contract, *, seat) -> dict
    render_briefing(resolved) -> str
    load_contract_file(path) -> dict
    main(argv) -> int                                  # CLI

Three levels of fixture:

  * REAL, on-disk, one-commit git repositories compiled through the actual
    `nightshift_project_contract.build_contract()` -- for the polyglot
    (Go + a JS-ecosystem end-to-end runner) worked example, the
    same-runner-different-command conflict, and the different-runner
    conflict. Built with the `_write_repo`/`_commit` pattern
    `tests/test_conformance_gate.py` already established for this repo, so
    fixture state is deterministic and git-tracked at a real commit --
    never a bare working tree, never `HEAD` resolved twice, never `main`
    (#698: a CI checkout carries no local `main` ref).

  * THIS repository itself, resolved at `git rev-parse HEAD` of the
    checkout under test -- never a hardcoded SHA, so the assertions read
    whatever this checkout's own compiled contract says rather than
    encoding a toolchain literal into the test.

  * Hand-built contract-envelope fragments only where a real repository
    cannot economically produce the shape under test (none needed here --
    every case below is reproducible with a handful of tracked files).

`tests/` is explicitly OUTSIDE issue #439's scan set (its manifest's
`core_paths` is `["bin", "deploy", "program"]`), so fixture content in this
file is free to name concrete ecosystems; `bin/nightshift_seat_contract.py`
itself is NOT exempt, and is scanned directly here for the same tokens.

MUTATION EVIDENCE for every guard below was produced by hand -- editing the
on-disk source, confirming the edit actually landed, running this file,
naming the test that turned red, then restoring the original from git and
confirming green again. That loop is reported in the accompanying task
summary rather than replayed automatically here, matching how
`tests/test_conformance_gate.py` documents its own external, literal
source-line mutation passes.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MODULE_PATH = BIN / "nightshift_seat_contract.py"
MANIFEST_PATH = REPO / "tests" / "core_portability_manifest.json"
RELEASE_CONFIG_PATH = REPO / "program" / "agents" / "release" / "config.yaml"
QA_ALPHA_CONFIG_PATH = REPO / "program" / "agents" / "crew_alpha" / "agents" / "qa" / "config.yaml"
QA_BETA_CONFIG_PATH = (
    REPO / "program" / "agents" / "crew_beta" / "agents" / "beta_qa" / "config.yaml")

# See test_conformance_gate.py's own header comment: a mutate-write / test /
# restore-write cycle that lands within one filesystem mtime tick can leave
# a STALE .pyc on disk whose (mtime, size) coincidentally matches the
# just-restored source. Disabling bytecode writes and purging any
# pre-existing cache up front makes every import in this process (and every
# subprocess this file spawns against the same interpreter) a real compile
# of what is actually on disk.
sys.dont_write_bytecode = True
_STALE_CACHE = BIN / "__pycache__"
if _STALE_CACHE.is_dir():
    shutil.rmtree(_STALE_CACHE, ignore_errors=True)

# Direct import, deliberately with no try/except (rule: no fail-soft test
# loaders) -- if this module fails to import, the whole file errors loudly
# at collection.
sys.path.insert(0, str(BIN))
import nightshift_seat_contract as sc  # noqa: E402
import nightshift_project_contract as pc  # noqa: E402


# ===========================================================================
# Fixture helpers -- real git repos (subprocess, mockless), the same shape
# tests/test_conformance_gate.py uses.
# ===========================================================================

def _write_repo(files):
    root = tempfile.mkdtemp(prefix="nightshift-seat-contract-fixture-")
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "fixture@example.com"],
        ["git", "config", "user.name", "Nightshift Seat Contract Fixture"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()
    return root, sha


def _go_and_js_e2e_fixture():
    """Deliberately reproduces the shape a real Go-primary repository with a
    JS-ecosystem end-to-end runner exhibits: a CI-observed command for the
    unit-level suite, and TWO end-to-end sources that name the same runner
    but disagree on the command -- the exact disagreement shape a sealed,
    non-Nightshift testbed was independently confirmed to exhibit before
    this fixture was written (see the task's own current-behaviour notes).

    The package.json side deliberately carries THREE cypress-bucketed
    scripts, not one: "cy:open" ("cypress open", the interactive GUI runner
    that never exits on its own) and "cy:wallet" (an unrelated narrowly-
    named script that happens to prefix-match the same bucket) alongside
    the real, terminating "cy:run". This is the exact multi-script-one-file
    shape a round-2 verifier finding proved `_pkg_candidates` mishandled by
    grabbing whichever script JSON declared first, regardless of whether it
    could ever complete -- `_select_pkg_entry` must still land on "cy:run"
    here (never "cy:open"/"cy:wallet"), and that command must still disagree
    with CI's own "npx cypress run" (different flags), so the SAME real
    conflict shape this test asserts survives the fix rather than being
    accidentally resolved away.
    """
    return {
        "go.mod": "module fixture/seat\n\ngo 1.21\n",
        "cmd/main.go": "package main\n\nfunc main() {}\n",
        ".github/workflows/ci.yml": (
            "name: seat fixture CI\n"
            "on: [push]\n"
            "jobs:\n"
            "  go-tests:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Run tests\n"
            "        run: go test ./... -short\n"
            "  cypress-e2e:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Run e2e\n"
            "        run: npx cypress run\n"
        ),
        "cypress.config.js": "module.exports = { e2e: { baseUrl: 'http://x' } };\n",
        "itf/package.json": json.dumps({
            "name": "itf",
            "scripts": {
                "cy:open": "cypress open",
                "cy:wallet": "cypress run --spec 'cypress/e2e/wallet/**'",
                "cy:run": "cypress run --browser chrome",
            },
        }) + "\n",
    }


def _different_e2e_runners_fixture():
    """Two end-to-end config sources naming DIFFERENT runners -- the sibling
    conflict shape (framework itself unresolved, not just the command)."""
    return {
        "README.md": "fixture repository, no primary language claimed\n",
        "cypress.config.js": "module.exports = { e2e: { baseUrl: 'http://x' } };\n",
        "wdio.conf.js": "exports.config = { specs: ['./test/specs/**'] };\n",
    }


def _no_lint_fixture():
    """A repository with a resolvable test command and nothing that could
    possibly answer the lint capability -- for AC-4 absent-passthrough."""
    return {
        "go.mod": "module fixture/nolint\n\ngo 1.21\n",
        "cmd/main.go": "package main\n\nfunc main() {}\n",
        ".github/workflows/ci.yml": (
            "name: no-lint fixture CI\n"
            "on: [push]\n"
            "jobs:\n"
            "  go-tests:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Run tests\n"
            "        run: go test ./...\n"
        ),
    }


def _compile(repo_root, sha):
    return pc.build_contract(repo_root, sha=sha)


def _flatten_checks(ci_field):
    value = ci_field.get("value") if isinstance(ci_field, dict) else None
    value = value if isinstance(value, dict) else {}
    names = set()
    for system in value.get("systems") or []:
        names.update(system.get("checks") or [])
    return names


def _resolved_checks(contract):
    """Route through the resolver itself, not the raw contract -- this is
    what proves resolve()'s own `ci` passthrough is real and not just an
    artefact of the underlying detector."""
    resolved = sc.resolve(contract, seat="release")
    return _flatten_checks(resolved["ci"])


# ===========================================================================
# The polyglot worked example
# ===========================================================================

class GoCypressFixture(unittest.TestCase):
    def test_go_and_cypress_contract_resolves_to_its_own_toolchain(self):
        root, sha = _write_repo(_go_and_js_e2e_fixture())
        try:
            contract = _compile(root, sha)
            resolved = sc.resolve(contract, seat="qa")
            briefing = sc.render_briefing(resolved)

            test_cap = resolved["capabilities"]["test"]
            self.assertEqual(test_cap["status"], "resolved")
            self.assertEqual(test_cap["command"], contract["test"]["value"]["command"])
            self.assertEqual(test_cap["command"], "go test ./... -short")
            self.assertEqual(test_cap["framework"], contract["test"]["value"]["framework"])
            self.assertEqual(test_cap["framework"], "go test")

            # No Python test-runner token anywhere in what a seat would read.
            for token in ("pytest", "unittest"):
                self.assertNotIn(token, briefing)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# This repository resolves to its own real commands
# ===========================================================================

class ThisRepoResolution(unittest.TestCase):
    def test_this_repo_resolves_to_its_own_suite_command(self):
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True, check=True).stdout.strip()
        contract = _compile(str(REPO), sha)
        resolved = sc.resolve(contract, seat="qa")

        test_cap = resolved["capabilities"]["test"]
        self.assertEqual(test_cap["status"], "resolved")
        # Compared against the SAME compiled contract's own field -- this
        # test file hardcodes no toolchain literal of its own.
        self.assertEqual(test_cap["command"], contract["test"]["value"]["command"])
        self.assertEqual(test_cap["framework"], contract["test"]["value"]["framework"])
        self.assertEqual(resolved["baseline_sha"], sha)
        self.assertEqual(resolved["contract_digest"], contract["contract_digest"])


# ===========================================================================
# A conflict never gets a winner picked for it
# ===========================================================================

class ConflictHandling(unittest.TestCase):
    def test_conflict_reports_framework_but_refuses_to_pick_a_command(self):
        # Main case: two sources, SAME runner, different commands.
        root, sha = _write_repo(_go_and_js_e2e_fixture())
        try:
            contract = _compile(root, sha)
            # Guard against a fixture that accidentally resolves cleanly --
            # the shape under test must actually be a conflict before
            # anything about the resolver is asserted.
            self.assertEqual(contract["e2e"]["status"], "conflict")

            resolved = sc.resolve(contract, seat="qa")
            e2e = resolved["capabilities"]["e2e"]
            self.assertEqual(e2e["status"], "unresolved")
            # A real disagreement, not a plain absence -- `unresolved_kind`
            # must say "conflict", the ONLY kind release's step 3 is allowed
            # to treat as a STOP (program/agents/release/config.yaml).
            self.assertEqual(e2e["unresolved_kind"], "conflict")
            self.assertIsNone(e2e["command"])
            self.assertEqual(e2e["framework"], "cypress")

            candidate_commands = {c["command"] for c in e2e["candidates"]}
            self.assertIn("npx cypress run", candidate_commands)
            # The package.json side must contribute its "cy:run" pick --
            # never "cy:open" (interactive, never exits) or "cy:wallet"
            # (loses the canonical-name tiebreak): a round-2 verifier
            # finding proved the old first-match logic could surface
            # "cy:open" here instead.
            self.assertTrue(
                any("cy:run" in (cmd or "") for cmd in candidate_commands),
                candidate_commands,
            )
            for cmd in candidate_commands:
                self.assertNotIn("cy:open", cmd or "")
                self.assertNotIn("cy:wallet", cmd or "")
                self.assertNotIn("cypress open", cmd or "")
            for c in e2e["candidates"]:
                self.assertTrue(c["evidence"], c)
        finally:
            shutil.rmtree(root, ignore_errors=True)

        # Sibling case: two sources, DIFFERENT runners -- framework itself
        # must not resolve either.
        root2, sha2 = _write_repo(_different_e2e_runners_fixture())
        try:
            contract2 = _compile(root2, sha2)
            self.assertEqual(contract2["e2e"]["status"], "conflict")

            resolved2 = sc.resolve(contract2, seat="qa")
            e2e2 = resolved2["capabilities"]["e2e"]
            self.assertEqual(e2e2["status"], "unresolved")
            self.assertEqual(e2e2["unresolved_kind"], "conflict")
            self.assertIsNone(e2e2["command"])
            self.assertIsNone(e2e2["framework"])
        finally:
            shutil.rmtree(root2, ignore_errors=True)


# ===========================================================================
# An absent capability is reported with the contract's own reason
# ===========================================================================

class AbsentHandling(unittest.TestCase):
    def test_absent_capability_carries_the_contracts_own_reason(self):
        root, sha = _write_repo(_no_lint_fixture())
        try:
            contract = _compile(root, sha)
            self.assertEqual(contract["lint"]["status"], "absent")

            resolved = sc.resolve(contract, seat="qa")
            lint = resolved["capabilities"]["lint"]
            self.assertEqual(lint["status"], "unresolved")
            # No evidence at all, nothing disagreeing -- this is the OTHER
            # `unresolved_kind`, the one release's step 3 must NOT treat as
            # a STOP (program/agents/release/config.yaml).
            self.assertEqual(lint["unresolved_kind"], "absent")
            self.assertIsNone(lint["command"])
            self.assertEqual(lint["reason"], contract["lint"]["reason"])
            self.assertTrue(lint["reason"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# `unverified` is the contract's own "this command's binary/path was not
# confirmed present" signal -- it must survive resolve()/render_briefing(),
# never be dropped on the way to a seat (round-2 verifier finding).
# ===========================================================================

def _unverified_test_script_fixture():
    return {
        "package.json": json.dumps({
            "name": "fixture-unverified", "version": "1.0.0",
            "scripts": {"test": "./scripts/does-not-exist.sh"},
        }),
        "index.js": "module.exports = () => 1;\n",
    }


class UnverifiedSurvivesResolution(unittest.TestCase):
    def test_unverified_command_is_flagged_on_the_capability_and_its_candidate(self):
        root, sha = _write_repo(_unverified_test_script_fixture())
        try:
            contract = _compile(root, sha)
            # Guard: the shape under test must actually BE unverified in
            # the compiled contract before anything about the resolver is
            # asserted -- otherwise this test would pass vacuously.
            self.assertTrue(contract["test"]["unverified"])

            resolved = sc.resolve(contract, seat="qa")
            test_cap = resolved["capabilities"]["test"]
            self.assertEqual(test_cap["status"], "resolved")
            self.assertTrue(test_cap["unverified"])
            self.assertEqual(len(test_cap["candidates"]), 1)
            self.assertTrue(test_cap["candidates"][0]["unverified"])

            briefing = sc.render_briefing(resolved)
            self.assertIn("unverified: True", briefing)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_verified_command_is_flagged_false_not_merely_absent(self):
        """The flip side: a plain, real command must render `unverified:
        False` explicitly -- not omit the key, and not something falsy-
        but-different like None, which `render_briefing`'s ! r formatting
        would show as the string "None" rather than "False".
        """
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True, check=True).stdout.strip()
        contract = _compile(str(REPO), sha)
        resolved = sc.resolve(contract, seat="qa")
        test_cap = resolved["capabilities"]["test"]
        self.assertEqual(test_cap["status"], "resolved")
        self.assertIs(test_cap["unverified"], False)
        briefing = sc.render_briefing(resolved)
        self.assertIn("unverified: False", briefing)


# ===========================================================================
# EXPERIENCE cannot widen what a seat runs -- resolve() takes only FACTS
# ===========================================================================

class AuthorityLayering(unittest.TestCase):
    def test_resolve_accepts_only_facts_and_a_seat_name(self):
        sig = inspect.signature(sc.resolve)
        params = list(sig.parameters.values())
        self.assertEqual(len(params), 2, params)

        contract_param, seat_param = params
        self.assertEqual(contract_param.name, "contract")
        self.assertIs(contract_param.default, inspect.Parameter.empty)
        self.assertIn(
            contract_param.kind,
            (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY),
        )

        self.assertEqual(seat_param.name, "seat")
        self.assertIs(seat_param.default, inspect.Parameter.empty)
        self.assertEqual(seat_param.kind, inspect.Parameter.KEYWORD_ONLY)

        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("nightshift_memory", source)


# ===========================================================================
# Refusals -- a SHA that isn't there, and a contract file that lies
# ===========================================================================

class Refusals(unittest.TestCase):
    def test_absent_sha_is_refused_with_exit_3(self):
        root, _sha = _write_repo(_no_lint_fixture())
        try:
            fake_sha = "a" * 40
            proc = subprocess.run(
                [sys.executable, str(MODULE_PATH), root, "--sha", fake_sha, "--seat", "qa"],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["ok"], False)
            self.assertIn(fake_sha, json.dumps(payload))
            self.assertNotIn("capabilities", payload)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_tampered_contract_file_is_indeterminate(self):
        root, sha = _write_repo(_no_lint_fixture())
        try:
            contract = _compile(root, sha)
            with tempfile.TemporaryDirectory() as tmp:
                contract_path = os.path.join(tmp, "contract.json")
                with open(contract_path, "w", encoding="utf-8") as fh:
                    json.dump(contract, fh)

                # Tamper: change the resolved test command WITHOUT
                # recomputing contract_digest.
                with open(contract_path, "r", encoding="utf-8") as fh:
                    tampered = json.load(fh)
                tampered["test"]["value"]["command"] = "rm -rf /nowhere"
                with open(contract_path, "w", encoding="utf-8") as fh:
                    json.dump(tampered, fh)
                # Assert the mutant actually applied before trusting the run.
                with open(contract_path, "r", encoding="utf-8") as fh:
                    self.assertIn("rm -rf /nowhere", fh.read())

                proc = subprocess.run(
                    [sys.executable, str(MODULE_PATH), root, "--sha", sha, "--seat", "qa",
                     "--contract-file", contract_path],
                    capture_output=True, text=True,
                )
                self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertEqual(payload["reason_code"], "contract_digest_mismatch")
                self.assertNotIn("rm -rf /nowhere", proc.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# The QA seats' "baseline SHA" is a value `git` itself already produces --
# not a value some other, unbuilt piece of the platform owes them.
# ===========================================================================

class BaselineShaIsSelfContained(unittest.TestCase):
    def test_qa_configs_derive_baseline_sha_from_git_not_a_bare_placeholder(self):
        for path in (QA_ALPHA_CONFIG_PATH, QA_BETA_CONFIG_PATH):
            text = path.read_text(encoding="utf-8")
            self.assertIn("git rev-parse HEAD", text, path)
            # The old prose left `<baseline sha>` as a placeholder for the
            # seat to fill in from nowhere in particular; the fixed text
            # assigns it from a real command instead.
            self.assertNotIn("--sha <baseline sha>", text, path)
            self.assertIn('--sha "$baseline_sha"', text, path)


# ===========================================================================
# No owned seat config, and no new bin/ module, names a toolchain literal
# ===========================================================================

class NoHardcodedToolchain(unittest.TestCase):
    def test_owned_seat_configs_and_new_module_name_no_toolchain_token(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        tokens = manifest["tokens"]
        self.assertTrue(tokens)

        for path in (QA_ALPHA_CONFIG_PATH, QA_BETA_CONFIG_PATH, RELEASE_CONFIG_PATH,
                     MODULE_PATH):
            text = path.read_text(encoding="utf-8")
            hits = [t for t in tokens if t in text]
            self.assertEqual(hits, [], f"{path}: {hits}")


# ===========================================================================
# The release seat cannot trust a required-check identity blind
# ===========================================================================

class ReleaseCheckIdentity(unittest.TestCase):
    def test_required_check_is_cross_checked_against_the_observed_inventory(self):
        text = RELEASE_CONFIG_PATH.read_text(encoding="utf-8")
        self.assertIn("resolved `ci` inventory", text)
        self.assertIn("STOP", text)

        # The instruction is not vacuous: prove the inventory really does
        # differ between a repository that publishes the named check and
        # one that does not.
        root, sha = _write_repo(_go_and_js_e2e_fixture())
        try:
            foreign_contract = _compile(root, sha)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        foreign_checks = _resolved_checks(foreign_contract)
        self.assertNotIn("tests", foreign_checks)

        this_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                                   capture_output=True, text=True, check=True).stdout.strip()
        this_contract = _compile(str(REPO), this_sha)
        this_checks = _resolved_checks(this_contract)
        self.assertIn("tests", this_checks)


# ===========================================================================
# Step 3's FULL suite must not be a permanent STOP on THIS repository --
# most capabilities here are genuinely absent, not in conflict, and an
# absent capability is not part of "the full suite" for a candidate.
# ===========================================================================

class ReleaseStepThreeAbsentIsNotAStop(unittest.TestCase):
    def test_release_config_distinguishes_absent_from_conflict(self):
        text = RELEASE_CONFIG_PATH.read_text(encoding="utf-8")
        self.assertIn("unresolved_kind", text)
        self.assertIn("absent", text)
        self.assertIn("not a STOP", text)
        self.assertIn("conflict", text)
        self.assertIn("STOP for the candidate", text)

    def test_this_repos_own_unresolved_capabilities_are_all_absent_not_conflict(self):
        """This is the exact command the verifier ran against this
        repository's own HEAD: `test` resolves, and every one of
        integration/e2e/build/lint/typecheck/format comes back
        `unresolved`. Read literally as "the FULL suite is test, lint,
        typecheck, plus e2e", that is a permanent STOP on this repository --
        the release train would never merge here. Prove instead that every
        one of those unresolved capabilities is `unresolved_kind: absent`
        (nothing here disagrees; this repository's own toolchain simply
        does not have a separate command for it) and NONE is `conflict` --
        so under the corrected step 3 (skip `absent`, STOP only on
        `conflict`), this repository keeps working.
        """
        this_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                                   capture_output=True, text=True, check=True).stdout.strip()
        this_contract = _compile(str(REPO), this_sha)
        resolved = sc.resolve(this_contract, seat="release")

        test_cap = resolved["capabilities"]["test"]
        self.assertEqual(test_cap["status"], "resolved")

        saw_at_least_one_unresolved = False
        for category, cap in resolved["capabilities"].items():
            if cap["status"] != "unresolved":
                continue
            saw_at_least_one_unresolved = True
            self.assertEqual(
                cap["unresolved_kind"], "absent",
                f"{category} is unresolved as {cap['unresolved_kind']!r}, not "
                "'absent' -- step 3 as corrected would STOP the release train "
                "on this repository")
        # Guard against a vacuously-true pass if this repository's contract
        # ever grows a command for every one of the seven capabilities.
        self.assertTrue(saw_at_least_one_unresolved, resolved["capabilities"])


if __name__ == "__main__":
    unittest.main()
