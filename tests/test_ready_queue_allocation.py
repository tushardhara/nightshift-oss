"""Issue #219 / ADR-0048: Program pulls normal implementation work from the
control-plane READY queue instead of narrative backlog state.

A stdlib text-conformance gate over `program/config.yaml`'s parsed `prompt:`
string and `program/skills/roadmap/SKILL.md`'s raw text, matching the
established pattern (`tests/test_budget_expired_operating_rule.py`,
`tests/test_review_contract.py`) rather than exercising code -- Program's
allocation procedure here is prose an agent reads, not a function with a call
graph, so this is the smallest seam that can regress it.

Covers the issue's 6 acceptance criteria, frozen verbatim in the DoR pass
(this session, `docs/decisions/ADR-0048-ready-queue-gates-implementation-
allocation.md`):
1. Program queries `nightshift-ctl task ready` before normal implementation
   allocation.
2. Implementation charters are created only for tasks returned as READY.
3. The existing priority order among READY tasks is kept: unblocks-other-work
   > user-visible-breakage > committed-outcome > new-value > cleanup.
4. If no eligible READY implementation work exists, Program waits, does
   already-required review/handoff work, or reports the blocker -- it does
   not invent unrelated implementation to keep agents busy.
5. A newly integrated prerequisite unlocks a dependent on the next
   scheduling pass without rewriting dependency prose.
6. Existing global worker capacity remains authoritative after task
   selection.
"""

import os
import re
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRIORITY_ORDER = (
    "unblocks-other-work > user-visible-breakage > committed-outcome > "
    "new-value > cleanup"
)


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


def program_prompt():
    with open(os.path.join(REPO, "program", "config.yaml"), encoding="utf-8") as f:
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


def tracker_registration_section(prompt):
    # The YAML block scalar (`prompt: |`) strips the file's 2-space base
    # indent from every line, so "  2. SLICE IT." in program/config.yaml
    # becomes "2. SLICE IT." in the parsed string -- match the stripped form.
    return _section(
        prompt,
        "REGISTER DoR-PASSED CANDIDATES INTO THE CONTROL PLANE",
        r"\n2\. SLICE IT\.",
    )


def plan_and_allocate_section(prompt):
    return _section(
        prompt,
        "THE READY QUEUE GATES NORMAL IMPLEMENTATION ALLOCATION",
        r"\n5\. RUN\.",
    )


def never_idle_section(prompt):
    return _section(
        prompt,
        "NEVER INVENT WORK TO FILL A QUIET QUEUE",
        r"\n- HEARTBEAT",
    )


def flat(text):
    return " ".join(text.split())


class TaskReadyQueriedBeforeAllocation(unittest.TestCase):
    """AC1, AC2."""

    def test_plan_and_allocate_queries_task_ready_first(self):
        body = flat(plan_and_allocate_section(program_prompt()))
        self.assertIn("run `nightshift-ctl task ready` fresh", body)
        self.assertIn("charter ONLY the task ids it returns", body)

    def test_absent_candidates_do_not_get_charters_regardless_of_gh_prose(self):
        body = flat(plan_and_allocate_section(program_prompt()))
        self.assertIn("unregistered, blocked, cycle-bound, or pending", body)
        self.assertIn("needs_split", body)
        self.assertIn("never auto-split by Program", body)
        self.assertIn("however finished its GH backlog prose looks", body)

    def test_in_flight_continuations_and_incidents_are_not_gated(self):
        body = flat(plan_and_allocate_section(program_prompt()))
        self.assertIn("not incident response", body)
        self.assertIn("not an in-flight task's own continuation", body)
        self.assertIn("drops out of `task ready` on its own", body)

    def test_ad_hoc_mid_wave_allocation_uses_the_same_gate(self):
        body = flat(never_idle_section(program_prompt()))
        self.assertIn("nightshift-ctl task ready", body)
        self.assertIn("never the crews' backlog prose alone", body)
        self.assertIn("same rule and priority order as step 4 PLAN AND ALLOCATE", body)

    def test_roadmap_skill_charters_only_from_task_ready(self):
        body = flat(roadmap_skill())
        self.assertIn("THE READY QUEUE GATES THIS STEP", body)
        self.assertIn("Run `nightshift-ctl task ready` fresh and charter ONLY", body)
        self.assertIn("never an item that looks finished in GitHub prose", body)


class PreChargerTasksAreRegisteredBeforeAllocation(unittest.TestCase):
    """DoR resolution (ADR-0048 Option C): a task row must exist before it
    can appear in `task ready`, so Program registers DoR-passed candidates
    itself, ahead of charter time."""

    def test_tracker_step_registers_dor_passed_candidates(self):
        body = flat(tracker_registration_section(program_prompt()))
        self.assertIn("nightshift-ctl task upsert --id <issue>-build", body)
        self.assertIn("--state pending --size", body)
        self.assertIn("size from the issue's own `size:` label", body)

    def test_tracker_step_mirrors_blocked_by_labels_as_dependency_edges(self):
        body = flat(tracker_registration_section(program_prompt()))
        self.assertIn("nightshift-ctl task dependency-add --id <issue>-build", body)
        self.assertIn("--depends-on <prereq>-build", body)
        self.assertIn("blocked-by:#<prereq>", body)

    def test_unregistered_prerequisite_defers_registration_not_miscodes_it(self):
        body = flat(tracker_registration_section(program_prompt()))
        self.assertIn("leaves THIS item unregistered rather than", body)
        self.assertIn("mis-encoded", body)

    def test_crew_phase_1_upsert_is_reused_not_duplicated(self):
        body = flat(tracker_registration_section(program_prompt()))
        self.assertIn("idempotent per", body)
        self.assertIn("not a competing write", body)
        self.assertIn("no crew-config change needed", body)

    def test_roadmap_charter_template_carries_the_registered_task_id(self):
        body = flat(roadmap_skill())
        self.assertIn("Task ID: <the exact id from nightshift-ctl task ready", body)
        self.assertIn("the crew's own Phase 1 task upsert reuses this id", body)

    def test_roadmap_mirrors_declared_dependencies_before_the_gate(self):
        body = flat(roadmap_skill())
        self.assertIn("nightshift-ctl task dependency-add --id <dependent>-build", body)
        self.assertIn("invisible to step 5", body)


class PriorityOrderIsKeptNotReinvented(unittest.TestCase):
    """AC3."""

    def test_plan_and_allocate_uses_the_existing_five_tag_order(self):
        body = flat(plan_and_allocate_section(program_prompt()))
        self.assertIn(PRIORITY_ORDER, body)
        self.assertIn("reused verbatim, never reinvented", body)
        self.assertIn("size is an input to sequencing, never to priority", body)

    def test_roadmap_charter_step_uses_the_same_order(self):
        body = flat(roadmap_skill())
        self.assertIn(PRIORITY_ORDER, body)

    def test_order_matches_the_crew_pm_source_of_truth(self):
        # Not a copy that can silently drift: the crew pm files are the
        # original source of this vocabulary (program/config.yaml reuses it
        # rather than defining a competing one).
        for path in (
            "program/agents/crew_alpha/agents/pm/config.yaml",
            "program/agents/crew_beta/agents/beta_pm/config.yaml",
        ):
            body = flat(read(path))
            self.assertIn(PRIORITY_ORDER, body)


class EmptyReadySetDoesNotInventBusywork(unittest.TestCase):
    """AC4."""

    def test_never_idle_forbids_unready_or_unrelated_substitutes(self):
        body = flat(never_idle_section(program_prompt()))
        self.assertIn(
            "NOT license to charter an unrelated, unready, or busywork item",
            body,
        )

    def test_never_idle_names_the_three_legitimate_fallbacks(self):
        body = flat(never_idle_section(program_prompt()))
        self.assertIn("already-required review, QA,", body)
        self.assertIn("staff, or cross-crew handoff work that is genuinely pending", body)
        self.assertIn("sys_timer_set", body)
        self.assertIn("report it as the FIRST line of", body)
        self.assertIn("naming the specific blocked item(s) and what they", body)

    def test_roadmap_charter_step_carries_the_same_no_busywork_rule(self):
        body = flat(roadmap_skill())
        self.assertIn("do not charter unrelated or unready work to fill its grant", body)
        self.assertIn("give it review/handoff work instead and say so", body)


class UnlockOnNextReadNeedsNoProseRewrite(unittest.TestCase):
    """AC5."""

    def test_never_idle_states_the_derived_at_read_time_unlock(self):
        body = flat(never_idle_section(program_prompt()))
        self.assertIn("A prerequisite reaching `integrated` makes its dependents", body)
        self.assertIn("reappear in `task ready` on the very next read", body)
        self.assertIn("nobody rewrites dependency prose to unlock them", body)


class WorkerCapacityStaysAuthoritativeAfterSelection(unittest.TestCase):
    """AC6."""

    def test_plan_and_allocate_states_the_composition_with_slot_acquire(self):
        body = flat(plan_and_allocate_section(program_prompt()))
        self.assertIn("it never replaces the worker-budget check below", body)
        self.assertIn("a READY task still queues on a full `slot acquire`", body)
        self.assertIn(
            "existing global worker capacity stays authoritative after selection",
            body,
        )


class OutOfScopeStaysOutOfScope(unittest.TestCase):
    """Issue #219's own "Not included" list: automatic L decomposition,
    big-PRD decomposition, a critical-path algorithm/service, dynamic crew
    creation, changed control-plane readiness semantics. None of the new
    prose should promise any of these."""

    def test_l_sized_items_are_never_auto_split_by_program(self):
        prompt = program_prompt()
        self.assertIn("never auto-split by Program", flat(plan_and_allocate_section(prompt)))
        self.assertIn("never auto-split here", flat(roadmap_skill()))

    def test_no_new_readiness_computation_is_invented(self):
        # Program consumes `task ready`'s verdict; it must not describe
        # deriving its own ready/blocked answer from GitHub labels alone.
        for text in (plan_and_allocate_section(program_prompt()), roadmap_skill()):
            body = flat(text)
            self.assertNotIn("critical-path", body.lower())
            self.assertNotIn("critical path", body.lower())

    def test_dynamic_crew_creation_is_not_introduced(self):
        for text in (plan_and_allocate_section(program_prompt()), roadmap_skill()):
            self.assertNotIn("new crew", flat(text).lower())


class ConfigStillParsesAsValidYaml(unittest.TestCase):
    """Guard against a broken prompt block -- the whole org reads this file
    at every turn."""

    def test_program_config_is_valid_yaml_with_a_prompt_key(self):
        prompt = program_prompt()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)


if __name__ == "__main__":
    unittest.main()
