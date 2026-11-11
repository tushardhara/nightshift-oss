"""Issue #277 / ADR-0051: close four named regression-proof gaps left open by
#228's own aggregate "Definition of finished" (15 "Required regression
proofs"), verified 2026-08-29 and re-verified during this issue's DoR.

Each class below is named for the exact proof number it closes, and states
in its docstring what mechanism it is protecting and why the existing test
suite (cited by file) did not already cover it. This file adds NEW coverage
only; it does not re-litigate #228's already-shipped design (ADR-0042) or
re-open #231/#232/#233.

Proof #7 (byte-subset causal invalidation on a partial fix) is NOT here --
it extends tests/test_review_eligibility.py directly, since that module
already owns the eligibility/reuse fixture machinery this proof needs.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nightshift_review_plan as plan  # noqa: E402
from test_233_review_routing_activation import (  # noqa: E402
    EXAMPLE_PATH, STAFF_MAPPED_GROUPS, RULES, _suggested_domains,
)
# Deliberately `import ... as` rather than `from ... import ShadowRiskPlanTests`:
# unittest's directory discovery (tests/run_suite.py) finds every TestCase
# subclass bound at module scope in each file it loads. Binding the class
# name here would make its 19 tests run a second time under this file's
# name every time the full suite executes -- a real, easy-to-miss test-count
# inflation bug, not a style nit.
import test_shadow_risk_plan as _shadow_risk_plan_module  # noqa: E402


# ---------------------------------------------------------------------------
# Proof #2 -- "STANDARD work runs independent eligible domains concurrently
# rather than serially." The code-level half of this (suggested_plan's
# human_review.mode/wave staying "parallel"/1 for STANDARD and HIGH_RISK, not
# just FAST) is proven in tests/test_shadow_risk_plan.py's new
# test_standard_and_high_risk_domains_share_one_parallel_wave. What remains
# is the mechanism that ACTUALLY makes dispatch concurrent: the fanout
# SKILL.md instruction telling a crew lead to emit every parallel-safe
# dispatch tool call in the SAME turn. That instruction is prose an LLM
# reads, not code an interpreter runs -- no unit test can execute it -- but
# an accidental future edit that weakens or removes it is exactly the
# regression proof #2 exists to catch, and nothing before this test read the
# committed SKILL.md files to confirm the clause is still there.
# ---------------------------------------------------------------------------
class Proof2ConcurrentDispatchProseTest(unittest.TestCase):
    SKILLS = {
        "crew_alpha": ROOT / "program/agents/crew_alpha/skills/fanout/SKILL.md",
        "crew_beta": ROOT / "program/agents/crew_beta/skills/fanout/SKILL.md",
    }

    def test_fanout_skill_still_instructs_same_turn_concurrent_dispatch(self):
        for crew, path in self.SKILLS.items():
            with self.subTest(crew=crew):
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    "Emit the worktree, slot and dispatch calls in THIS "
                    "turn", text,
                    f"{crew}'s fanout SKILL.md no longer instructs "
                    "same-turn concurrent dispatch -- a regression to "
                    "serial/polled dispatch would not be caught by any "
                    "other test (proof #2, issue #277).")
                self.assertIn("never end a turn", text)
                self.assertIn("Dispatch every parallel-safe task the\n"
                              "   semaphore grants a slot for, then end "
                              "the turn. Do not poll.", text)


# ---------------------------------------------------------------------------
# Proof #10 -- "Routine local fixes do not create/require a fresh ADR merely
# because every implementation historically did; architectural/schema/API/
# security/irreversible changes still do."
#
# tests/test_233_review_routing_activation.py::GroundTruthMappingTest
# already proves the MECHANICAL trigger bidirectionally, but only via a
# synthetic ambiguous-mode (160000) fixture that forces every example
# through the ambiguity->HIGH_RISK path -- it never proves that a REALISTIC,
# cleanly-classified routine change (the actual "no ADR needed" case proof
# #10 cares about) fails to reach the staff gate. This test closes that
# by combining: (a) the same STAFF_MAPPED_GROUPS ground truth, confirming
# every one of #228's named qualifying-change categories still requires
# `staff` (the ADR-consistency domain), and (b) two REAL, blob-classified,
# non-ambiguous fixtures (reusing test_shadow_risk_plan's own bundle
# mechanism, not the synthetic shortcut) proving an ordinary docs/local
# change is classified FAST/STANDARD with `staff` absent -- i.e. no ADR
# review is forced on it.
#
# Deliberately NOT attempted here (recorded in ADR-0051 and
# .omni/decisions.md as an explicit scope-out, per this issue's own DoR):
# mechanically verifying that a qualifying change's diff actually CONTAINS a
# new/updated `docs/decisions/**` file, or that a task record cites a
# specific ADR id. `bin/nightshift-ctl`'s `tasks` table has no ADR-reference
# field today (confirmed absent from `TASK_FIELDS`), and ADR-0039's receipt
# schema is frozen (AC-3's closed JSON key set); adding either is a new
# schema decision bigger than "add a regression test to an existing
# convention" and risks re-litigating already-shipped design without its
# own DoR cycle. Today this direction still relies on the `staff` reviewer's
# own judgment when it reads `docs/reviews/REVIEW-CONTRACT-TEMPLATE.md`'s
# "Staff / ADR consistency" checklist row -- a real, named, currently-open
# gap, not silently assumed closed.
# ---------------------------------------------------------------------------
class Proof10AdrGateBidirectionalTest(unittest.TestCase):
    def test_every_qualifying_category_still_requires_staff(self):
        for group in STAFF_MAPPED_GROUPS:
            with self.subTest(group=group):
                self.assertIn("staff", _suggested_domains(EXAMPLE_PATH[group]),
                              f"{group} no longer routes to `staff` -- a "
                              "qualifying change could integrate without "
                              "ADR-consistency review (proof #10).")

    def test_ordinary_realistic_change_never_requires_staff(self):
        case = _shadow_risk_plan_module.ShadowRiskPlanTests(
            "test_fast_is_canonical_exact_and_has_no_side_effects")
        case.setUp()
        try:
            case._task(crew="program", size="S")
            fast_head = case._head({"docs/guide.md": "human guide\n"})
            fast_report, _ = case._report(fast_head)
            self.assertEqual(fast_report["classification"]["profile"], "FAST")
            self.assertNotIn("staff", fast_report["suggested_domains"])

            case._task(crew="program", size="M", base=fast_head)
            standard_head = case._head({"docs/ordinary.md": "ordinary\n"})
            standard_report, _ = case._report(standard_head)
            self.assertEqual(standard_report["classification"]["profile"],
                             "STANDARD")
            self.assertNotIn("staff", standard_report["suggested_domains"],
                             "an ordinary docs-only change now forces the "
                             "staff/ADR gate -- routine work would pay an "
                             "ADR-debate cost #228 Principle 6 says it "
                             "should never pay (proof #10).")
        finally:
            case.temp.cleanup()


if __name__ == "__main__":
    unittest.main()
