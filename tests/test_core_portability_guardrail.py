"""Tests for the core-portability guardrail -- issue #439.

Two groups: `RealRepoAudit` runs the actual AC-2 scan-set/matching rule
against this repo's real `bin/`, `deploy/`, `program/` content and the
checked-in manifest (AC-4/AC-5/AC-6/AC-7). `FixtureAuditUnitTests` and the
rest exercise `core_portability_guardrail`'s logic hermetically against temp
directories, so they don't depend on -- or need to mutate -- real Core
content (file scope for #439 is `tests/` only; these fixtures are the only
place a "project-specific token in a Core file" is ever synthesized).

Deliberately absent: no test here asserts detection of an *undeclared*
toolchain invented against a project's declared/discovered build commands --
that's AC-10's explicit non-goal (belongs to #438, not this guardrail).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
try:
    import core_portability_guardrail as guardrail
finally:
    sys.path.remove(str(TESTS_DIR))


def _write(root: Path, rel_path: str, content: str) -> None:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _base_manifest_data(core_paths=("bin", "deploy", "program"), tokens=None, entries=None,
                         allowlist=None) -> dict:
    return {
        "core_paths": list(core_paths),
        "tokens": list(tokens) if tokens is not None else ["pytest", "cargo", "npm"],
        "structural_allowlist": allowlist or {"path_prefixes": ["deploy/"], "paths": []},
        "entries": entries or [],
    }


class RealRepoAudit(unittest.TestCase):
    """Runs the pinned AC-2 rule against real Core content (git-tracked
    blobs under bin/, deploy/, program/ at HEAD) and the checked-in
    manifest."""

    def test_manifest_matches_current_scan(self):
        data = guardrail.load_manifest()
        result = guardrail.audit(data)
        print(guardrail.summary_line(result))
        self.assertEqual(result.problems, [], "\n".join(result.problems))

    def test_reports_total_and_debt_counts(self):
        # AC-7: total entry count and the debt subset must both be visible
        # on every run, not just derivable by reading the manifest by hand.
        data = guardrail.load_manifest()
        result = guardrail.audit(data)
        line = guardrail.summary_line(result)
        print(line)
        self.assertIn(f"{result.total} entries total", line)
        self.assertIn(f"{result.debt} debt", line)

    def test_day_one_manifest_counts(self):
        # AC-6 snapshot, measured per docs/specs/439-guardrail.md v2 S9,
        # plus the v3 addition once #400/#443 landed in bin/: 13 more
        # structural hits (10 in nightshift_project_contract.py's own
        # polyglot toolchain detector, 3 in nightshift_memory_bootstrap.
        # py's DEFAULT_GENERATED_DIRS comments) -- debt is unchanged, only
        # `structural` grows, 19 -> 32. Then #443 schema v2's dedicated
        # e2e field adds a `playwright` pattern alongside the existing
        # `cypress` one (same AC-11 structural shape) -- one more hit,
        # 32 -> 33, 39 -> 40 total.
        #
        # Then the #442 epic's "bind every dispatch to contract_digest +
        # baseline_sha" child (bin/nightshift_seat_contract.py) actually
        # bound the QA seats and the release seat to the compiled contract,
        # so three of the debt entries this manifest tracked stopped
        # matching anything and were dropped by regenerate() (AC-5/AC-12):
        # both QA configs' `pytest` bullet and release config's
        # `tests/run.sh` required-check prose. structural is untouched;
        # debt drops 7 -> 4, total 40 -> 37. Not a weakened guard -- the
        # thing the debt entries were tracking got fixed.
        #
        # Then the public-release supply-chain fix committed the harness
        # CLIs' lockfile (deploy/harness-clis/package-lock.json), whose
        # registry URLs hit `npm`: one structural deploy/** entry, 33 -> 34,
        # total 37 -> 38. Debt is unchanged.
        data = guardrail.load_manifest()
        result = guardrail.audit(data)
        self.assertEqual(result.total, 38)
        self.assertEqual(result.debt, 4)
        self.assertEqual(result.structural, 34)

    def test_every_debt_entry_has_an_owning_issue(self):
        # AC-3: only debt entries require one, but every debt entry must
        # actually carry it -- checked directly against the manifest, not
        # just through audit()'s aggregate problem list.
        data = guardrail.load_manifest()
        for entry in guardrail.parse_entries(data):
            if entry.category == "debt":
                self.assertTrue(entry.issue, f"{entry.file} / {entry.token} has no owning issue")

    def test_every_structural_entry_is_on_the_ac11_allowlist(self):
        data = guardrail.load_manifest()
        allowlist = data["structural_allowlist"]
        for entry in guardrail.parse_entries(data):
            if entry.category == "structural":
                self.assertTrue(
                    guardrail.is_structural_allowed(entry.file, allowlist),
                    f"{entry.file} claims structural but is outside the AC-11 allowlist")

    def test_ac11_allowlist_has_exact_contract_shape_and_real_disclosures(self):
        """AC-11's multipliers are part of the closed-list contract, not just
        examples: 3 writable-roots configs + 5 capability disclosures + 2
        handoff idiom files + 2 borrowed-project polyglot detectors (#400/
        #443, added once those landed in bin/ -- see docs/specs/439-
        guardrail.md's v3 note), in addition to deploy/**."""
        data = guardrail.load_manifest()
        allowlist = data["structural_allowlist"]
        expected_paths = {
            "program/config.yaml",
            "program/agents/crew_alpha/config.yaml",
            "program/agents/crew_beta/config.yaml",
            "program/agents/crew_alpha/agents/eng_codex/config.yaml",
            "program/agents/crew_alpha/agents/eng_claude/config.yaml",
            "program/agents/platform/config.yaml",
            "program/agents/crew_beta/agents/beta_eng_codex/config.yaml",
            "program/agents/crew_beta/agents/beta_eng_claude/config.yaml",
            "program/agents/crew_alpha/skills/handoff/SKILL.md",
            "program/agents/crew_beta/skills/handoff/SKILL.md",
            "bin/nightshift_project_contract.py",
            "bin/nightshift_memory_bootstrap.py",
        }
        self.assertEqual(allowlist["path_prefixes"], ["deploy/"])
        self.assertEqual(set(allowlist["paths"]), expected_paths)

        # Independent real-content spot checks of two capability-disclosure
        # configs, one from each crew.  They must be actual token hits rather
        # than paths accidentally admitted to the closed list.
        for rel_path in (
            "program/agents/crew_alpha/agents/eng_claude/config.yaml",
            "program/agents/crew_beta/agents/beta_eng_codex/config.yaml",
        ):
            text = (guardrail.REPO_ROOT / rel_path).read_text(encoding="utf-8")
            self.assertIn("cargo", text, rel_path)

    def test_real_file_repeated_token_is_one_file_token_hit(self):
        """AC-2 hit granularity against real Core content, not a fixture."""
        rel_path = "deploy/bootstrap.sh"
        text = (guardrail.REPO_ROOT / rel_path).read_text(encoding="utf-8")
        self.assertGreater(text.count("npm"), 1, "real-content premise changed: "
                           "the token must repeat for this to test granularity")
        hits = guardrail.scan_hits([rel_path], ["npm"], guardrail.REPO_ROOT)
        self.assertEqual(hits, {(rel_path, "npm")})


class FixtureAuditUnitTests(unittest.TestCase):
    """Hermetic tests against temp directories -- `list_files=walk_files` so
    no real git repo is needed at the fixture root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _audit(self, data):
        return guardrail.audit(data, repo_root=self.root, list_files=guardrail.walk_files)

    def _touch_core_dirs(self, core_paths=("bin", "deploy", "program")):
        for base in core_paths:
            (self.root / base).mkdir(parents=True, exist_ok=True)

    def test_clean_tree_matching_manifest_passes(self):
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "print('hello')\n")
        data = _base_manifest_data()
        result = self._audit(data)
        self.assertEqual(result.problems, [])
        self.assertEqual(result.total, 0)

    def test_ac8_adding_a_project_specific_token_fails(self):
        """AC-8: proof, by test, that a new hit in a Core-path fixture file
        fails the audit -- not asserted by inspection."""
        self._touch_core_dirs()
        _write(self.root, "program/agents/qa/config.yaml", "Use whatever runner the project declares.\n")
        data = _base_manifest_data()

        before = self._audit(data)
        self.assertEqual(before.problems, [], "fixture must start clean")

        _write(self.root, "program/agents/qa/config.yaml",
               "Use whatever runner the project declares.\nFor pytest, run `pytest -q`.\n")
        after = self._audit(data)

        self.assertNotEqual(after.problems, [])
        self.assertTrue(
            any("program/agents/qa/config.yaml" in p and "'pytest'" in p for p in after.problems),
            after.problems,
        )

    def test_stale_manifest_entry_fails(self):
        # AC-5: the file no longer contains the token the entry claims.
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "nothing project-specific here\n")
        data = _base_manifest_data(entries=[
            {"file": "bin/tool.py", "token": "pytest", "category": "debt",
             "reason": "stale fixture entry", "issue": 439},
        ])
        result = self._audit(data)
        self.assertTrue(any("stale manifest entry" in p for p in result.problems), result.problems)

    def test_token_inside_a_comment_still_counts_as_a_hit(self):
        # Spec S3: no comment-aware special-casing -- a token explaining why
        # it's forbidden is still a hit and still needs an explicit exemption.
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "# forbidden: do not add npm here\n")
        data = _base_manifest_data()
        result = self._audit(data)
        self.assertTrue(
            any("bin/tool.py" in p and "'npm'" in p for p in result.problems), result.problems)

    def test_missing_core_path_fails_and_names_it(self):
        # bin/ and program/ exist; deploy/ was never created.
        _write(self.root, "bin/tool.py", "x = 1\n")
        _write(self.root, "program/config.yaml", "k: v\n")
        data = _base_manifest_data()
        with self.assertRaises(guardrail.ManifestError) as ctx:
            self._audit(data)
        self.assertIn("deploy", str(ctx.exception))

    def test_empty_manifest_and_no_hits_passes_with_zero_debt(self):
        for base in ("bin", "deploy", "program"):
            _write(self.root, f"{base}/.keep", "\n")
        data = _base_manifest_data(entries=[])
        result = self._audit(data)
        self.assertEqual(result.problems, [])
        self.assertEqual(result.total, 0)
        self.assertEqual(result.debt, 0)

    def test_unclassified_entry_fails_pending_human_review(self):
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "pytest goes here\n")
        data = _base_manifest_data(entries=[
            {"file": "bin/tool.py", "token": "pytest", "category": None,
             "reason": "", "issue": None},
        ])
        result = self._audit(data)
        self.assertTrue(any("needs human classification" in p for p in result.problems),
                         result.problems)

    def test_debt_entry_without_issue_fails(self):
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "pytest goes here\n")
        data = _base_manifest_data(entries=[
            {"file": "bin/tool.py", "token": "pytest", "category": "debt",
             "reason": "no issue attached", "issue": None},
        ])
        result = self._audit(data)
        self.assertTrue(any("missing owning issue" in p for p in result.problems), result.problems)

    def test_structural_claim_outside_allowlist_fails_closed(self):
        # AC-11: mechanical path check -- this file has a perfectly good
        # reason string, but it's not on the allowlist, so it still fails.
        self._touch_core_dirs()
        _write(self.root, "program/agents/qa/config.yaml", "pytest goes here\n")
        data = _base_manifest_data(entries=[
            {"file": "program/agents/qa/config.yaml", "token": "pytest", "category": "structural",
             "reason": "trust me", "issue": None},
        ])
        result = self._audit(data)
        self.assertTrue(
            any("outside the AC-11 allowlist" in p or "outside AC-11 allowlist" in p
                for p in result.problems),
            result.problems,
        )

    def test_structural_claim_inside_allowlist_passes(self):
        self._touch_core_dirs()
        _write(self.root, "deploy/setup.sh", "cargo build --release\n")
        data = _base_manifest_data(entries=[
            {"file": "deploy/setup.sh", "token": "cargo", "category": "structural",
             "reason": "deploy/** allowlisted", "issue": None},
        ])
        result = self._audit(data)
        self.assertEqual(result.problems, [])
        self.assertEqual(result.structural, 1)


class ManifestFailClosedTests(unittest.TestCase):
    def test_missing_manifest_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(guardrail.ManifestError):
                guardrail.load_manifest(Path(tmp) / "does-not-exist.json")

    def test_corrupt_manifest_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(guardrail.ManifestError):
                guardrail.load_manifest(path)

    def test_non_utf8_core_content_fails_closed_and_names_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_path = "bin/non-utf8.dat"
            path = root / rel_path
            path.parent.mkdir(parents=True)
            path.write_bytes(b"\xff\xfe\x00npm")
            with self.assertRaises(guardrail.ManifestError) as ctx:
                guardrail.scan_hits([rel_path], ["npm"], root)
            self.assertIn(rel_path, str(ctx.exception))
            self.assertIn("cannot read as text", str(ctx.exception))


class LiteralMatchingSemanticsTests(unittest.TestCase):
    def test_substring_in_content_matches_but_token_in_filename_does_not(self):
        """AC-2 is literal whole-file substring matching: no word boundary,
        and paths themselves are outside the searched content."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "bin/npm-helper.txt", "a clean file\n")
            _write(root, "bin/word.txt", "prefixnpmSuffix\n")
            hits = guardrail.scan_hits(
                ["bin/npm-helper.txt", "bin/word.txt"], ["npm"], root)
            self.assertEqual(hits, {("bin/word.txt", "npm")})


class RegenerateUnitTests(unittest.TestCase):
    """AC-12: diff-based regeneration preserves hand-set fields on survivors,
    lands new hits unclassified, and prunes stale entries."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _regen(self, data):
        return guardrail.regenerate(data, repo_root=self.root, list_files=guardrail.walk_files)

    def _touch_core_dirs(self, core_paths=("bin", "deploy", "program")):
        for base in core_paths:
            (self.root / base).mkdir(parents=True, exist_ok=True)

    def test_surviving_entry_keeps_hand_set_fields(self):
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "pytest still here\n")
        data = _base_manifest_data(entries=[
            {"file": "bin/tool.py", "token": "pytest", "category": "debt",
             "reason": "hand-written reason", "issue": 439},
        ])
        regenerated = self._regen(data)
        self.assertEqual(regenerated["entries"], [
            {"file": "bin/tool.py", "token": "pytest", "category": "debt",
             "reason": "hand-written reason", "issue": 439},
        ])

    def test_new_raw_hit_lands_unclassified(self):
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "npm install\n")
        data = _base_manifest_data(entries=[])
        regenerated = self._regen(data)
        self.assertEqual(regenerated["entries"], [
            {"file": "bin/tool.py", "token": "npm", "category": None, "reason": "", "issue": None},
        ])
        result = guardrail.audit(regenerated, repo_root=self.root, list_files=guardrail.walk_files)
        self.assertTrue(any("needs human classification" in p for p in result.problems))

    def test_stale_entry_is_dropped(self):
        self._touch_core_dirs()
        _write(self.root, "bin/tool.py", "nothing here now\n")
        data = _base_manifest_data(entries=[
            {"file": "bin/tool.py", "token": "pytest", "category": "debt",
             "reason": "used to be true", "issue": 439},
        ])
        regenerated = self._regen(data)
        self.assertEqual(regenerated["entries"], [])


if __name__ == "__main__":
    unittest.main()
