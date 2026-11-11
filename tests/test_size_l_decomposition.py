"""Issue #220 / ADR-0049: a crew PM decomposes size:L work into a dependency
graph of S/M child issues -- with its own Definition of Ready -- before any
engineer is chartered; Program's roadmap skill routes a stuck `needs_split`
candidate back to that PM instead of leaving it a permanent dead end.

A stdlib text-conformance gate over both crew `pm` agents' parsed `prompt:`
strings and `program/skills/roadmap/SKILL.md`'s raw text, matching the
established pattern (`tests/test_ready_queue_allocation.py`,
`tests/test_review_contract.py`, `tests/test_budget_expired_operating_rule.py`)
rather than exercising code -- this is prose an agent reads, not a function
with a call graph, so a text gate is the smallest seam that can regress it.

Covers the issue's 7 acceptance criteria, frozen in the DoR pass (this
session, `docs/decisions/ADR-0049-decompose-size-l-before-implementation.md`):
1. No size:L item is passed to an engineer.
2. L decomposition produces only S/M executable leaves.
3. Child tasks have observable outcomes, acceptance criteria and explicit
   exclusions using the existing Definition of Ready.
4. Ordering dependencies are persisted through the control-plane dependency
   mechanism (`nightshift-ctl task dependency-add`).
5. Independent children may run in parallel only when their dependencies/file
   scopes allow it.
6. Parent L issue/item remains a planning container; completion is based on
   its child outcomes, not a giant implementation PR.
7. Children are not created merely to mirror architecture layers; each child
   should be independently reviewable/mergeable where practical.
"""

import os
import re
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PM_CONFIG_PATHS = (
    "program/agents/crew_alpha/agents/pm/config.yaml",
    "program/agents/crew_beta/agents/beta_pm/config.yaml",
)


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


def pm_prompt(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["prompt"]


def roadmap_skill():
    return read("program/skills/roadmap/SKILL.md")


def _section(text, marker, end_pattern):
    """Isolate the block starting at `marker`, bounded by the next match of
    `end_pattern` -- so assertions can't pass by matching unrelated prose
    elsewhere in a large shared file."""
    assert marker in text, f"marker not found: {marker!r}"
    start = text.index(marker)
    rest = text[start + len(marker):]
    m = re.search(end_pattern, rest)
    end = start + len(marker) + (m.start() if m else len(rest))
    return text[start:end]


def dor_section(prompt):
    return _section(prompt, "## Definition of Ready", r"\n  ## Decomposing")


def decomposition_section(prompt):
    return _section(prompt, "## Decomposing size:L work", r"\n  ## Priority")


def charter_step_section(skill_text):
    return _section(skill_text, "5. **Charter each crew.**", r"\n6\. \*\*Commit")


def flat(text):
    return " ".join(text.split())


class NoSizeLItemReachesAnEngineer(unittest.TestCase):
    """AC1."""

    def test_dor_gains_a_size_criterion_that_blocks_l(self):
        for path in PM_CONFIG_PATHS:
            body = flat(dor_section(pm_prompt(path)))
            self.assertIn("Size is S or M", body)
            self.assertIn("A `size:L` item never reaches `state:ready` itself", body)

    def test_l_parent_never_gets_a_task_row_or_ready_relabel(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("never becomes `state:ready`", body)
            self.assertIn("never gets a `nightshift-ctl task upsert` row for dispatch", body)

    def test_roadmap_routes_stuck_needs_split_back_to_the_owning_pm(self):
        body = flat(charter_step_section(roadmap_skill()))
        self.assertIn("A stuck `needs_split` item is not simply skipped forever", body)
        self.assertIn("hand it back to the owning crew's `pm` to decompose", body)
        self.assertIn("never the L parent itself", body)


class DecompositionProducesOnlySMLeaves(unittest.TestCase):
    """AC2."""

    def test_decomposition_targets_sm_children_only(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("smallest set of S/M child issues", body)
            self.assertIn("A child you cannot write a standalone DoR for is cut too coarse; split it again", body)


class ChildrenCarryTheirOwnDefinitionOfReady(unittest.TestCase):
    """AC3."""

    def test_children_get_a_standalone_dor_not_a_fragment(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("each carrying its own full Definition of Ready", body)
            self.assertIn("a standalone DoR, not a fragment of the parent's", body)

    def test_dor_criterion_7_references_the_decomposition_procedure(self):
        for path in PM_CONFIG_PATHS:
            body = flat(dor_section(pm_prompt(path)))
            self.assertIn('see "Decomposing size:L work" below', body)


class DependencyOrderingUsesTheControlPlaneMechanism(unittest.TestCase):
    """AC4."""

    def test_decomposition_uses_task_dependency_add(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("nightshift-ctl task dependency-add --id", body)
            self.assertIn("--depends-on <prereq>-build", body)

    def test_roadmap_only_admits_registered_children_with_real_edges(self):
        body = flat(charter_step_section(roadmap_skill()))
        self.assertIn("registered with", body)
        self.assertIn("their own task rows and any real dependency edges", body)


class IndependentChildrenParallelizeOnlyWhenScopeAllows(unittest.TestCase):
    """AC5."""

    def test_dependency_edges_are_added_only_for_real_constraints(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("only where a real constraint forces order", body)

    def test_shared_file_forces_sequential_not_parallel(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn(
                "a shared file is always sequential, never parallel worktrees",
                body,
            )

    def test_disjoint_scope_children_stay_independent_and_parallel(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn(
                "disjoint file scope and no such need stay independent and may build",
                body,
            )


class ParentStaysAPlanningContainer(unittest.TestCase):
    """AC6."""

    def test_parent_stays_open_and_is_never_relabeled(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("Leave the L parent issue open as the planning container", body)
            self.assertIn("never gets relabeled `size:S`/`size:M`", body)

    def test_signoff_is_against_children_not_one_giant_pr(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("Sign it off only", body)
            self.assertIn("once every child has merged and met its own acceptance criteria", body)
            self.assertIn("never against one giant implementation PR, because none should exist for it", body)


class ChildrenAreNotMereArchitectureLayerMirrors(unittest.TestCase):
    """AC7."""

    def test_layer_splits_require_independent_reviewability(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("Do not cut children to mirror architecture layers by default", body)
            self.assertIn(
                "acceptable only when each layer child is itself independently "
                "reviewable and mergeable without the others in flight",
                body,
            )


class BothCrewPmsShareTheIdenticalContract(unittest.TestCase):
    """Not a per-crew choice -- the same decomposition rule for both crews,
    same as the priority-order vocabulary ADR-0048's test already checks
    identically across both pm agents."""

    def test_decomposition_section_text_matches_verbatim_across_crews(self):
        sections = [flat(decomposition_section(pm_prompt(p))) for p in PM_CONFIG_PATHS]
        self.assertEqual(sections[0], sections[1])

    def test_worked_example_matches_the_issues_own_example(self):
        for path in PM_CONFIG_PATHS:
            body = flat(decomposition_section(pm_prompt(path)))
            self.assertIn("`A size:S — define model`", body)
            self.assertIn("`B size:M — implement service, depends_on=A`", body)
            self.assertIn("`C size:S — expose API, depends_on=B`", body)


class ConfigsStillParseAsValidYaml(unittest.TestCase):
    """Guard against a broken prompt block -- both crews read this file at
    every turn."""

    def test_both_pm_configs_parse_with_a_nonempty_prompt(self):
        for path in PM_CONFIG_PATHS:
            prompt = pm_prompt(path)
            self.assertIsInstance(prompt, str)
            self.assertGreater(len(prompt), 0)


if __name__ == "__main__":
    unittest.main()
