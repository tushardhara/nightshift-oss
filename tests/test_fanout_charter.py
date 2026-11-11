"""Issue #556 -- the charter must not tell Program that `slot acquire` is blind.

Bullet 1 of heartbeat triage used to end with `slot acquire` "has no path
column and cannot see this collision". That was true when written and false
since #541 / PR #552, which added `tasks.paths` and a glob-aware overlap
refusal inside `cmd_slot_acquire`'s own BEGIN IMMEDIATE.

While that sentence stood, the only provably-safe reading was to serialise, so
the org ran one worker against a capacity of ten. These tests pin the corrected
contract in both directions: the stale claim is gone, the same-path safety rule
survives, and the disjoint-work instruction is present and unconditional.

Same shape as tests/test_449_dor_queue_terminalization.py, which also asserts
on charter text rather than behaviour -- the charter IS the interface here.
"""

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def charter():
    with open(os.path.join(ROOT, "program", "config.yaml"), encoding="utf-8") as f:
        return f.read()


class FanoutCharterTest(unittest.TestCase):
    def setUp(self):
        self.text = charter()

    def test_stale_blindness_claim_is_gone(self):
        """AC-1/AC-6. The load-bearing falsehood must not return.

        It may only appear as the quoted history of what the bullet used to
        say -- never as a live instruction. Asserting on the phrase alone
        would fail on the correction note that cites it, so require the
        citation form to be present wherever the phrase is.
        """
        phrase = "has no path column and cannot see this collision"
        if phrase in self.text:
            self.assertIn("previously said", self.text,
                          "the stale claim appears without being marked as history")
            self.assertIn("false since #541", self.text)

    def test_charter_states_slot_acquire_now_enforces_scope(self):
        """AC-1. The replacement must be an affirmative statement, not silence."""
        self.assertIn("`slot acquire` DOES see path collisions", self.text)
        self.assertIn("tasks.paths", self.text)

    def test_same_path_work_is_still_serialised(self):
        """AC-2. This ticket must NOT weaken the safety rule it builds on.

        The whole value of enforcing scope is that colliding work stops. If
        this edit had removed the same-path rule, it would have traded a
        conservative-but-correct charter for a fast-and-wrong one.
        """
        self.assertIn("SHARING a path must be", self.text)
        self.assertIn("started one at a time, in issue-number order", self.text)

    def test_disjoint_work_must_be_started_together(self):
        """AC-3. Mandatory, not permitted -- 'may' would change nothing."""
        self.assertIn("DISJOINT work MUST be started together", self.text)
        self.assertIn("dispatch them ALL in the same turn", self.text)
        self.assertIn("Do not start them one per turn", self.text)

    def test_unscoped_task_hazard_is_named(self):
        """AC-4. An empty scope silently disables the entire mechanism.

        #541 AC-7 makes an unscoped task never refuse, on purpose, so that
        pre-migration rows keep working. The cost is that a task nobody scoped
        looks parallel-safe with everything. The charter has to say so, or the
        check quietly becomes a no-op as new work arrives unscoped.
        """
        self.assertIn("UNSCOPED task never refuses", self.text)
        self.assertIn("incompletely decomposed", self.text)

    def test_no_stricter_prose_ceiling_is_invented(self):
        """AC-5. The ceilings are the configured ones, not a number in prose."""
        self.assertIn("spawn_bounds.max_dispatches_per_turn", self.text)
        self.assertIn("do not invent a stricter one here", self.text)


if __name__ == "__main__":
    unittest.main()
