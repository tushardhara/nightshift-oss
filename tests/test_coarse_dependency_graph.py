"""Issue #221 / ADR-0050: discovery draws a small COARSE dependency graph of
outcome-shaped project slices -- before any implementation planning -- and
only the currently-actionable slice(s) get expanded into executable S/M task
graphs through the EXISTING size:L decomposition mechanism (#220,
ADR-0049), unmodified. Architecture seam count informs ownership/dependency
boundaries between crews but no longer implies "one crew per seam"; crew
count stays fixed by the roster regardless of graph shape.

A stdlib text-conformance gate over `program/skills/discovery/SKILL.md`,
`program/skills/roadmap/SKILL.md` (raw text) and `program/config.yaml`'s
parsed `prompt:` string, matching the established pattern
(`tests/test_size_l_decomposition.py`, `tests/test_ready_queue_allocation.py`,
`tests/test_review_contract.py`) rather than exercising code -- this is
prose an agent reads, not a function with a call graph, so a text gate is
the smallest seam that can regress it.

Control + counterfactual: every assertion below targets text introduced by
this change. Run against the pre-change files (`origin/main`,
base_sha 4e44b1fec23e1e461aae575973820131a50480f8) every assertion in this
module fails, since none of the marker strings exist yet; run against the
post-change tree, every assertion passes. `tests/test_pre_change_control.py`
style manual verification: `git show 4e44b1fec23e1e461aae575973820131a50480f8:
program/skills/discovery/SKILL.md` contains none of the markers this file
checks for.

Covers the issue's 8 acceptance criteria:
1. Large briefs get a small coarse dependency map before implementation
   planning.
2. Coarse nodes describe product/outcome slices, not source files or
   implementation steps.
3. Dependencies are between work/outcomes, never between crew names.
4. Only currently actionable coarse slices are expanded into executable S/M
   task graphs.
5. Do not eagerly generate the full project's entire implementation backlog
   on day one.
6. Configured crews remain fixed; the graph changes work assignment, not
   org size.
7. Architecture seams inform ownership and dependency boundaries but no
   longer imply "create one crew per seam".
8. The existing PRD, DoD and milestone artifacts remain the product truth;
   this graph is an execution/dependency view, not a replacement PRD.
"""

import os
import re
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DISCOVERY_SKILL_PATH = "program/skills/discovery/SKILL.md"
ROADMAP_SKILL_PATH = "program/skills/roadmap/SKILL.md"
PROGRAM_CONFIG_PATH = "program/config.yaml"


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


def discovery_skill():
    return read(DISCOVERY_SKILL_PATH)


def roadmap_skill():
    return read(ROADMAP_SKILL_PATH)


def program_prompt():
    with open(os.path.join(REPO, PROGRAM_CONFIG_PATH), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["prompt"]


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


def step_3b_section(text):
    return _section(
        text,
        "## Step 3b — draw the coarse dependency graph",
        r"\n## Step 4",
    )


def step_4_section(text):
    return _section(
        text,
        "## Step 4 — shape the org to the seams, not to the ambition",
        r"\n## Step 5",
    )


def roadmap_step_1_section(text):
    return _section(text, "1. **State the wave's outcome**", r"\n2\. \*\*Ask")


def roadmap_step_5_section(text):
    return _section(text, "5. **Charter each crew.**", r"\n6\. \*\*Commit")


def roadmap_notes_section(text):
    return _section(text, "## Notes", r"\Z")


def program_seam_rule_section(prompt):
    return _section(
        prompt,
        "Two rules from discovery bind everything after it:",
        r"\n  ## The wave loop",
    )


def program_phase0_section(prompt):
    return _section(prompt, "## Phase 0 — discovery", r"\n  ## The wave loop")


def flat(text):
    return " ".join(text.split())


class LargeBriefsGetACoarseDependencyMapFirst(unittest.TestCase):
    """AC1."""

    def test_discovery_gains_step_3b_before_any_implementation_planning(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn("draw the brief's COARSE", body)
        self.assertIn("BEFORE any implementation planning", body)

    def test_step_3b_precedes_step_4_in_document_order(self):
        text = discovery_skill()
        self.assertLess(
            text.index("## Step 3b — draw the coarse dependency graph"),
            text.index("## Step 4 — shape the org to the seams"),
            "Step 3b must run before org-shaping / issue-opening (Step 4)",
        )

    def test_program_prompt_names_the_coarse_graph_as_discovery_output(self):
        body = flat(program_phase0_section(program_prompt()))
        self.assertIn("COARSE DEPENDENCY GRAPH", body)
        self.assertIn("drawn before any", body)
        self.assertIn("implementation planning", body)


class CoarseNodesAreOutcomeSlicesNotFilesOrSteps(unittest.TestCase):
    """AC2."""

    def test_node_definition_excludes_files_crews_and_implementation_steps(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn("A coarse node is a PRODUCT/OUTCOME slice", body)
        self.assertIn(
            "never a source file, a crew name, or an implementation step", body
        )


class DependenciesAreBetweenOutcomesNeverCrews(unittest.TestCase):
    """AC3."""

    def test_edges_are_between_outcomes_not_crews(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn("An edge is between two outcomes", body)
        self.assertIn("never between two crews", body)


class OnlyActionableSlicesExpandIntoExecutableTaskGraphs(unittest.TestCase):
    """AC4."""

    def test_discovery_only_opens_issues_for_actionable_nodes(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn("Only the currently-actionable node(s) get GitHub issues opened", body)

    def test_discovery_step_4_scopes_the_boundary_map_to_the_actionable_node(self):
        body = flat(step_4_section(discovery_skill()))
        self.assertIn(
            "scoped to the currently-actionable coarse node(s) from Step 3b, never the whole graph",
            body,
        )

    def test_roadmap_scopes_every_charter_to_actionable_nodes(self):
        body = flat(roadmap_step_1_section(roadmap_skill()))
        self.assertIn("CURRENTLY-ACTIONABLE node(s) only", body)
        self.assertIn("no unmet `depends_on`", body)

    def test_roadmap_reuses_the_existing_size_l_mechanism_unmodified(self):
        body = flat(roadmap_step_5_section(roadmap_skill()))
        self.assertIn(
            "typically filed as exactly one `size:L` issue per", body
        )
        self.assertIn(
            "it never changes how an oversized issue turns into executable "
            "work — that stays the existing #220/ADR-0049 mechanism, unmodified",
            body,
        )


class NoEagerFullBacklogGeneration(unittest.TestCase):
    """AC5."""

    def test_discovery_names_the_drowning_failure_mode_it_prevents(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn(
            "generating the whole project's implementation backlog for P2-P5 "
            "before P1 ships is exactly the drowning failure mode this graph "
            "exists to prevent",
            body,
        )

    def test_gated_nodes_get_no_issues_yet(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn(
            "A node still gated by an unmet `depends_on` gets no issues yet",
            body,
        )

    def test_roadmap_never_gives_an_idle_crew_a_head_start_on_a_gated_node(self):
        body = flat(roadmap_notes_section(roadmap_skill()))
        self.assertIn(
            "Never expand a coarse graph node (discovery Step 3b) into issues "
            "or S/M tasks merely because a crew has spare grant", body
        )
        self.assertIn("never a head start on a", body)


class ConfiguredCrewsRemainFixed(unittest.TestCase):
    """AC6."""

    def test_discovery_step_4_fixes_crew_count_to_the_roster(self):
        body = flat(step_4_section(discovery_skill()))
        self.assertIn(
            "Crew count is FIXED by the roster (the `crew_*` entries in your "
            "tool list), regardless of how many seams the reconciled "
            "architecture has or how many nodes the coarse graph has",
            body,
        )

    def test_program_config_fixes_crew_count_to_the_roster(self):
        body = flat(program_seam_rule_section(program_prompt()))
        self.assertIn("Crew count is fixed by the roster", body)
        self.assertIn(
            "regardless of seam count or how many nodes the coarse "
            "dependency graph has",
            body,
        )


class SeamsInformBoundariesNotOneCrewPerSeam(unittest.TestCase):
    """AC7."""

    def test_discovery_step_4_no_longer_equates_seams_with_crew_count(self):
        text = discovery_skill()
        # Legacy phrase, unwrapped, must not survive anywhere.
        self.assertNotIn("Two seams means", flat(text))
        body = flat(step_4_section(text))
        self.assertIn("informs OWNERSHIP and dependency boundaries", body)
        self.assertIn("it is never grounds to mint or retire one", body)

    def test_program_config_no_longer_equates_seam_count_with_crew_count(self):
        prompt = program_prompt()
        self.assertNotIn("Two seams means", flat(prompt))
        self.assertNotIn("SEAM COUNT sets how many crews", flat(prompt))
        body = flat(program_seam_rule_section(prompt))
        self.assertIn(
            "SEAMS inform OWNERSHIP and dependency boundaries between the "
            "crews that already exist; they never set how many crews exist",
            body,
        )
        self.assertIn("that is never a reason to mint or retire a crew", body)


class ExistingPrdDodMilestonesRemainTheProductTruth(unittest.TestCase):
    """AC8."""

    def test_graph_is_an_execution_view_generated_from_existing_artifacts(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn(
            "The graph is an execution/dependency VIEW generated from the "
            "same PRD, DoD and milestone plan already written above",
            body,
        )
        self.assertIn("it never replaces or restates them", body)

    def test_graph_gets_no_database_object_of_its_own(self):
        body = flat(step_3b_section(discovery_skill()))
        self.assertIn(
            "it gets no database row or table of its own — it is a file, "
            "exactly like the rest of `.omni/goal.md`",
            body,
        )


class ConfigsStillParseAsValidYaml(unittest.TestCase):
    """Guard against a broken prompt block -- Program reads this file every
    turn."""

    def test_program_config_parses_with_a_nonempty_prompt(self):
        prompt = program_prompt()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)


if __name__ == "__main__":
    unittest.main()
