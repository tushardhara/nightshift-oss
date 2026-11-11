"""Fixture/conformance tests named by issue #233's frozen review contract
(docs/reviews/233-activate-review-routing-CONTRACT.md, AC-3, AC-7, AC-8) for
ADR-0042 (suggested_domains gates fanout dispatch, not eligibility).

The gates these tests verify (`SECURITY GATE`, `UX GATE`, `STAFF GATE`, the
Phase 3/4 fail-closed defaults) are prompt text consumed by an LLM crew lead,
not executed code. Per the contract's own AC-3 note, these are
structural/textual assertions against the committed files -- following
tests/test_review_receipts.py:457-463's own pattern -- not agent execution.
AC-3's "ground truth" (which review-risk.json group maps to which
suggested_domains entry) is computed by calling the real
`bin/nightshift_review_plan.py:_classify` rather than hand-duplicating its
mapping, so this test cannot silently drift from the classifier it verifies
against.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import nightshift_review_plan as plan  # noqa: E402

RULES = json.loads((ROOT / "program/review-risk.json").read_text(encoding="utf-8"))
TASK = {"crew": "platform", "size": "S", "spec_ref": "https://example/spec"}

CONFIGS = {
    "crew_alpha": ROOT / "program/agents/crew_alpha/config.yaml",
    "crew_beta": ROOT / "program/agents/crew_beta/config.yaml",
}
SKILLS = {
    "crew_alpha": ROOT / "program/agents/crew_alpha/skills/fanout/SKILL.md",
    "crew_beta": ROOT / "program/agents/crew_beta/skills/fanout/SKILL.md",
}

# One concrete (non-glob-pattern) example path per group/domain this test
# needs ground truth for. `security_path`/`workflow`/`control_plane` overlap
# content_scan_globs; new_mode is deliberately not 100644/100755 so
# `_classify` takes its ambiguity branch instead of reading a (nonexistent,
# for this synthetic fixture) blob -- extra_domains is computed earlier in
# the same loop, before that branch, so this does not affect the result.
EXAMPLE_PATH = {
    "constitution": "AGENTS.md",
    "control_plane": "bin/nightshift-ctl",
    "workflow": ".github/workflows/tests.yml",
    "schema_migration": "db/migrations/0001_init.sql",
    "dependency": "package.json",
    "public_contract": "api/openapi.yaml",
    "security_path": "auth/login.py",
    "ux": RULES["conditional_globs"]["ux"][0],
    "qa": "some_module.py",
}

# Groups _classify maps to `staff` (ADR-0042's STAFF GATE) vs `security`
# (unchanged SECURITY GATE) -- asserted below as ground truth, not assumed.
STAFF_MAPPED_GROUPS = {"constitution", "control_plane", "schema_migration",
                        "dependency", "public_contract"}
SECURITY_MAPPED_GROUPS = {"control_plane", "workflow", "security_path"}


def _suggested_domains(path):
    entries = [{
        "old_path": None, "new_path": path,
        "old_blob_oid": None, "new_blob_oid": "0" * 40,
        "old_mode": None, "new_mode": "160000",
    }]
    _, domains = plan._classify(RULES, TASK, entries, 1, False, None)
    return set(domains)


def _paragraph(text, heading):
    match = re.search(rf"^ *{re.escape(heading)}[ —:].*?(?=\n\n)", text, re.S | re.M)
    assert match is not None, f"{heading!r} paragraph not found"
    return match.group(0)


class GroundTruthMappingTest(unittest.TestCase):
    """Confirms this file's own STAFF_MAPPED_GROUPS/SECURITY_MAPPED_GROUPS
    constants (used by the AC-3 tests below) match the real classifier,
    rather than asserting a hand-duplicated table nothing checks."""

    def test_staff_and_security_mapped_groups_match_the_real_classify(self):
        for group, path in EXAMPLE_PATH.items():
            if group in ("ux", "qa"):
                continue
            with self.subTest(group=group):
                domains = _suggested_domains(path)
                self.assertEqual("staff" in domains, group in STAFF_MAPPED_GROUPS)
                self.assertEqual("security" in domains, group in SECURITY_MAPPED_GROUPS)


class GateWordingConformanceTest(unittest.TestCase):
    """AC-3: dispatch narrowing never skips a domain review-risk.json
    mechanically triggers; code review and QA stay unconditional."""

    def test_code_review_and_qa_are_unconditional_in_both_crews(self):
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew):
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    "`code_review` (cross-review) and `qa` dispatch\n  "
                    "unconditionally, exactly as before this ADR", text)

    def test_ux_gate_names_the_exact_conditional_globs_ux_keys(self):
        self.assertTrue("ux" in _suggested_domains(EXAMPLE_PATH["ux"]))
        # Dedupe the `**/<x>/**` nested-match duplicates down to one key per
        # conceptual category -- the paragraph names each category once, not
        # every raw glob variant that reaches it.
        unique_globs = sorted({g[3:] if g.startswith("**/") else g
                               for g in RULES["conditional_globs"]["ux"]})
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew):
                para = _paragraph(path.read_text(encoding="utf-8"), "UX GATE")
                self.assertIn('conditional_globs["ux"]', para)
                for glob in unique_globs:
                    self.assertIn(glob, para,
                                 f"{glob} missing from UX GATE ({crew})")

    def test_staff_gate_names_exactly_the_groups_classify_maps_to_staff(self):
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew):
                para = _paragraph(path.read_text(encoding="utf-8"), "STAFF GATE")
                for group in STAFF_MAPPED_GROUPS:
                    self.assertIn(group, para,
                                 f"{group} missing from STAFF GATE ({crew})")
                # workflow/security_path map to `security` only (never
                # `staff`) -- a STAFF GATE naming them would over-ask.
                for group in SECURITY_MAPPED_GROUPS - STAFF_MAPPED_GROUPS:
                    self.assertNotIn(group, para,
                                     f"{group} wrongly present in STAFF GATE ({crew})")


class FailClosedConformanceTest(unittest.TestCase):
    """AC-7: four named fail-closed conditions, each falling back to the
    full/unconditional set. AC-8: the fallback reason is written to the
    existing dispatch/admission log, not a new subsystem."""

    REASONS = ("suggestion_missing", "suggestion_stale", "suggestion_malformed",
               "activation_disabled")

    def test_suggestion_missing_falls_back_to_full_fanout(self):
        self._assert_fallback_condition(
            "its\n  `suggested_domains` / `target.head_sha` is missing")

    def test_suggestion_stale_falls_back_to_full_fanout(self):
        self._assert_fallback_condition("does not match the PR's\n  actual "
                                        "current head SHA")

    def test_suggestion_malformed_falls_back_to_full_fanout(self):
        self._assert_fallback_condition("or is otherwise malformed")

    def test_activation_disabled_falls_back_to_full_fanout(self):
        self._assert_fallback_condition("ADR-0042's activation is off")

    def _assert_fallback_condition(self, must_contain):
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew):
                text = path.read_text(encoding="utf-8")
                phase3 = _paragraph(text, "FAIL-CLOSED DEFAULT")
                self.assertIn(must_contain, phase3)
                self.assertIn("ask\n  the program for every gate below on "
                              "that PR", phase3)

    def test_all_four_named_reasons_appear_in_phase3_fallback_of_both_crews(self):
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew):
                text = path.read_text(encoding="utf-8")
                phase3 = _paragraph(text, "FAIL-CLOSED DEFAULT")
                for reason in self.REASONS:
                    self.assertIn(reason, phase3,
                                 f"{reason} missing from Phase 3 fallback ({crew})")

    def test_phase4_and_skill_fallback_also_name_all_four_reasons(self):
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew, source="config.yaml"):
                text = path.read_text(encoding="utf-8")
                # Phase 4's own paragraph, not Phase 3's (search past Phase 3).
                phase4 = text.split("## Phase 4", 1)[1]
                phase4_fallback = _paragraph(phase4, "FAIL-CLOSED DEFAULT")
                self.assertIn("treat cross-crew review as required regardless",
                              phase4_fallback)
                self.assertIn("ADR-0042's activation is off", phase4_fallback)
        for crew, path in SKILLS.items():
            with self.subTest(crew=crew, source="SKILL.md"):
                text = path.read_text(encoding="utf-8")
                for reason in self.REASONS:
                    self.assertIn(reason, text,
                                 f"{reason} missing from {crew}'s fanout SKILL.md")

    def test_fallback_reason_is_logged_not_a_new_subsystem(self):
        """AC-8: the reason string is written into the existing
        dispatch/admission log entry, never a new table or file."""
        for crew, path in CONFIGS.items():
            with self.subTest(crew=crew):
                phase3 = _paragraph(path.read_text(encoding="utf-8"),
                                    "FAIL-CLOSED DEFAULT")
                self.assertIn(
                    "dispatch/admission log entry you'd write for a normal "
                    "gate decision", phase3)


if __name__ == "__main__":
    unittest.main()
