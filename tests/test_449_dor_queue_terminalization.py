"""Issue #449 / ADR-0056: the heartbeat queue must terminalize a stalled DoR
pass and advance, instead of re-selecting the same `needs-dor` issue forever.

A stdlib text-conformance gate over `program/config.yaml`'s parsed `prompt:`
string and `program/skills/tracker/SKILL.md`'s raw text, matching the
established pattern (`tests/test_budget_expired_operating_rule.py`,
`tests/test_ready_queue_allocation.py`, `tests/test_review_contract.py`)
rather than exercising code -- Program's queue-selection procedure here is
prose an agent reads, not a function with a call graph, so a text gate is
the correct and only adequate seam (AC-7: no new database, scheduler, or
general release subsystem).

Reconciles `architect` (Codex, READY-WITH-CHANGES, 4 findings) and `staff`
(Claude, inconsistent-with-as-drafted -> resolved by incorporating findings)
per the accepted DoR contract recorded on issue #449.

Covers the issue's 8 acceptance criteria:
1. A completed DoR pass must durably produce exactly one queue outcome:
   `state:ready` or `state:blocked`, in the same turn.
2. `state:blocked` records a concise machine-readable `dor_reason:` and a
   human-readable `unblock_condition:`.
3. The queue selector excludes `state:blocked` (and `needs-owner`) and
   advances to the next eligible issue.
4. A regression fixture reproduces the #423/#424 sequence: #423 cannot
   advance, #423 becomes blocked, #424 is selected next.
5. Repeated heartbeats with no change to blocked #423 produce zero new #423
   DoR/implementation dispatches.
6. An explicit reconciliation (unblock condition changed) may return the
   issue to `needs-dor`; history remains auditable.
7. No new queue database or scheduler subsystem -- reuses GitHub state
   labels and the existing Program/PM selection contract.
8. #174's Program-authority overlap and the recurring-automation mechanism
   are unchanged (no edit to that prose).
"""

import os
import re
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


def program_prompt():
    with open(os.path.join(REPO, "program", "config.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["prompt"]


def tracker_skill():
    return read("program/skills/tracker/SKILL.md")


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


def queue_selection_bullet(prompt):
    return _section(
        prompt,
        "QUEUE SELECTION IS MECHANICAL, AND A COMPLETED DOR PASS ALWAYS "
        "TERMINALIZES",
        r"\n  - HEARTBEAT",
    )


def queue_selection_order_subsection(skill_text):
    return _section(
        skill_text,
        "### Queue selection order (#449, ADR-0056)",
        r"\n## Owner input",
    )


class QueueSelectionOrderIsDurableInConfigYaml(unittest.TestCase):
    """AC3, AC7: the mechanical order lives in `program/config.yaml`'s own
    prompt, not only in `.omni/decisions.md` or an ad hoc heartbeat override."""

    def test_names_the_layer_and_its_relation_to_task_ready(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn("GitHub-issue-label layer", body)
        self.assertIn("task ready", body)
        # Scoped correctly per staff's round-1 finding: "before a task row
        # exists" describes the needs-dor branch only, not state:ready
        # (which may already have a row via ADR-0048's TRACKER mirroring).
        self.assertIn(
            "needs-dor` issue has no task row yet (true \"before\" case)", body
        )
        self.assertIn("describes step 3 only, not step 2", body)

    def test_states_the_four_step_mechanical_order(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn("oldest-numbered `state:ready` issue", body)
        self.assertIn("`P0` > `P1` > `P2`", body)
        self.assertIn("oldest-numbered `from-owner` issue carrying", body)
        self.assertIn("needs-dor", body)
        self.assertIn("`needs-owner` and `state:blocked` are never selected", body)

    def test_states_the_in_flight_pr_is_first_not_only(self):
        """ADR-0066 (2026-09-01): step 1 is FIRST, not ONLY -- an owner
        correction after the prior version of this test (before ADR-0066)
        kept asserting the retired 'ONLY' wording, which would have kept
        passing vacuously against text ADR-0066 deliberately changed."""
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn("goes FIRST, not ONLY", body)
        self.assertIn("shepherd that PR ahead of", body)
        self.assertIn("normal worker slot is free", body)
        self.assertIn("ALSO start any `state:ready` item", body)
        self.assertIn("shares NO path scope, NO", body)

    def test_no_new_queue_subsystem_introduced(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        # The queue is GITHUB LABELS, not a parallel subsystem -- that is what
        # this test protects, and it still holds. #706/ADR-0088 changed only
        # WHICH BINARY issues the label edit: `nightshift-file-issue` is a thin
        # wrapper that pins `--repo` to Nightshift's own tracker, because bare
        # `gh issue ...` resolves its target repository from the working
        # directory and would file into a customer's tracker from a customer
        # checkout. Same GitHub labels, same transition, safer surface -- so the
        # negative assertions below are the ones carrying this test's meaning.
        self.assertIn("nightshift-file-issue edit", body)
        self.assertNotIn("nightshift-ctl queue", body)
        self.assertNotIn("CREATE TABLE", body)


class ReconcilesWithADR0048FiveTagOrder(unittest.TestCase):
    """staff review round 1 finding: step 2's `P0`>`P1`>`P2` triage must not
    silently reinvent ADR-0048's five-tag pooled-READY-set ranking -- the two
    orders must have an explicit, non-overlapping scope, with the five-tag
    order winning whenever both could apply."""

    def test_config_yaml_names_the_five_tag_order_and_its_scope(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn(
            "unblocks-other-work > user-visible-breakage > "
            "committed-outcome > new-value > cleanup",
            body,
        )
        self.assertIn("reused verbatim, not reinvented here", body)
        self.assertIn("ADR-0048's five-tag order wins", body)
        self.assertIn("single-pick heartbeat triage", body)

    def test_tracker_skill_mirrors_the_same_reconciliation(self):
        body = " ".join(
            queue_selection_order_subsection(tracker_skill()).split()
        )
        self.assertIn(
            "unblocks-other-work > user-visible-breakage > "
            "committed-outcome > new-value > cleanup",
            body,
        )
        self.assertIn("never reinvented here", body)
        self.assertIn("wins whenever both could apply", body)


class DorPassAlwaysTerminalizesSameTurn(unittest.TestCase):
    """AC1, AC2: exactly one of two outcomes, same turn, with the required
    machine-readable + human-readable fields."""

    def test_ready_outcome_flips_the_labels_and_records_the_contract(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn(
            "nightshift-file-issue edit <n> --remove-label needs-dor,state:blocked "
            "--add-label state:ready",
            body,
        )
        self.assertIn("plus an issue comment recording the", body)
        self.assertIn("accepted contract", body)

    def test_blocked_outcome_flips_the_labels_and_records_reason_fields(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn(
            "nightshift-file-issue edit <n> --remove-label needs-dor,state:ready "
            "--add-label state:blocked",
            body,
        )
        self.assertIn("dor_reason:", body)
        self.assertIn("unblock_condition:", body)
        self.assertIn("no closed taxonomy", body)

    def test_never_left_unchanged_as_needs_dor(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn(
            "MUST terminalize to exactly one of two outcomes in the same turn",
            body,
        )
        self.assertIn(
            "never left unchanged for the next heartbeat to reselect", body
        )

    def test_blocked_meaning_matches_dropped_after_three_loops(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn("SAME meaning", body)
        self.assertIn("Dropped after three loops", body)


class ReconciliationIsExplicitNeverSilent(unittest.TestCase):
    """AC6."""

    def test_unblock_returns_to_needs_dor_with_a_named_reason(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn(
            "nightshift-file-issue edit <n> --remove-label state:blocked --add-label needs-dor",
            body,
        )
        self.assertIn("naming which", body)
        self.assertIn("never silently", body)

    def test_github_is_the_durable_record(self):
        body = " ".join(queue_selection_bullet(program_prompt()).split())
        self.assertIn("durable record", body)
        self.assertIn(".omni/decisions.md", body)
        self.assertIn("not itself required for durability", body)


class AuthorityAndAutomationFencesUntouched(unittest.TestCase):
    """AC8: the new bullet sits between the existing NEVER IDLE bullet and
    the HEARTBEAT bullet without altering either, and the recurring
    automation / #174 authority-fence text is unaffected."""

    def test_insertion_point_does_not_disturb_neighbors(self):
        prompt = program_prompt()
        normalized = " ".join(prompt.split())
        self.assertIn(
            "nobody rewrites dependency prose to unlock them. "
            "- QUEUE SELECTION IS MECHANICAL",
            normalized,
        )
        self.assertIn("TERMINALIZES (#449, ADR-0056)", normalized)
        # The next bullet after this one is still the real HEARTBEAT bullet,
        # verbatim, not truncated or reworded by the insertion.
        self.assertIn(
            "HEARTBEAT — THIS IS THE AUTONOMY MECHANISM, NOT A BACKSTOP. "
            "At run start create",
            normalized,
        )

    def test_heartbeat_mechanism_prose_unchanged_count(self):
        # The HEARTBEAT bullet itself (the scheduler mechanism this fix must
        # not touch or duplicate) still exists EXACTLY once. Note:
        # `sys_scheduled_task_create`/`FREQ=HOURLY` legitimately appear twice
        # in the real prompt -- once in Phase 0 discovery's own mention, once
        # in this bullet -- so those are not useful uniqueness checks here.
        prompt = program_prompt()
        self.assertEqual(
            prompt.count("HEARTBEAT — THIS IS THE AUTONOMY MECHANISM"), 1
        )
        self.assertEqual(
            prompt.count("Nothing else returns you to the objective"), 1
        )


class TrackerSkillMirrorsTheSameRule(unittest.TestCase):
    """AC1-AC3 mirrored inside `program/skills/tracker/SKILL.md`."""

    def test_state_blocked_transition_example_next_to_state_ready(self):
        skill = tracker_skill()
        self.assertIn(
            "nightshift-file-issue edit <n> --remove-label needs-dor --add-label state:ready",
            skill,
        )
        self.assertIn(
            "nightshift-file-issue edit <n> --remove-label needs-dor,state:ready "
            "--add-label state:blocked",
            skill,
        )

    def test_dropped_after_three_loops_generalized(self):
        skill = tracker_skill()
        idx = skill.index("**Dropped after three loops**")
        following = skill[idx: idx + 900]
        self.assertIn("#449, ADR-0056", following)
        self.assertIn("not only a", following)
        self.assertIn("PR-review-loop drop", following)

    def test_queue_selection_order_subsection_present(self):
        body = " ".join(queue_selection_order_subsection(tracker_skill()).split())
        self.assertIn("open delivery PR outranks everything", body)
        self.assertIn("oldest-numbered `state:ready` issue", body)
        self.assertIn("`P0` > `P1` > `P2`", body)
        self.assertIn("needs-dor", body)
        self.assertIn("`needs-owner` and `state:blocked` are never selected", body)
        self.assertIn("dor_reason:", body)
        self.assertIn("unblock_condition:", body)


# --------------------------------------------------------------------------
# AC4, AC5: a minimal executable fixture mirroring the prose's own filter --
# a single literal predicate, not a general priority sorter (reconciling
# `architect`'s "don't build a parallel scheduler" with `staff`'s "the
# fixture needs real prose to conform against").
# --------------------------------------------------------------------------

def is_dor_candidate(labels):
    """True iff the issue is a live DoR candidate under the queue-selection
    order's step 3/4: carries `needs-dor`, and is excluded by neither
    `state:blocked` nor `needs-owner`. Blocked wins over needs-dor if an
    issue is defensively mislabelled with both (AC1's "exactly one outcome"
    holds even through a label-hygiene slip)."""
    return (
        "needs-dor" in labels
        and "state:blocked" not in labels
        and "needs-owner" not in labels
    )


def next_candidate(issues):
    """The literal AC4 mechanism: the lowest-numbered issue (by dict key)
    whose labels pass `is_dor_candidate`, or None. Not a general reusable
    scheduler -- mirrors exactly the "oldest-numbered ... carrying
    needs-dor" clause and nothing more."""
    candidates = [n for n, labels in issues.items() if is_dor_candidate(labels)]
    return min(candidates) if candidates else None


class Issue423424RegressionFixture(unittest.TestCase):
    """AC4, AC5: reproduces the real #423/#424 label history from
    `gh issue view 423`/`424` (confirmed live during #449's DoR pass)."""

    def setUp(self):
        # Real, live label sets -- #423 already `state:blocked` today; #424
        # was `needs-dor` at the time this loop was originally observed.
        self.issues = {
            423: {"P0", "from-owner", "state:blocked"},
            424: {"P0", "from-owner", "needs-dor"},
        }

    def test_423_is_not_a_dor_candidate(self):
        self.assertFalse(is_dor_candidate(self.issues[423]))

    def test_424_is_a_dor_candidate(self):
        self.assertTrue(is_dor_candidate(self.issues[424]))

    def test_next_candidate_selects_424_not_423(self):
        self.assertEqual(next_candidate(self.issues), 424)

    def test_two_further_no_change_heartbeats_still_exclude_423(self):
        # AC5: repeated heartbeats with no change to blocked #423 produce
        # zero new #423 DoR dispatches -- simulate two more heartbeat reads
        # against the same unchanged label state.
        for _ in range(2):
            self.assertFalse(is_dor_candidate(self.issues[423]))
            self.assertEqual(next_candidate(self.issues), 424)

    def test_defensive_double_label_blocked_wins(self):
        # A label-hygiene slip -- both needs-dor and state:blocked present
        # simultaneously -- still predicates False. Blocked wins.
        mislabelled = {"P0", "from-owner", "needs-dor", "state:blocked"}
        self.assertFalse(is_dor_candidate(mislabelled))

    def test_needs_owner_also_excludes_regardless_of_needs_dor(self):
        labels = {"P1", "from-owner", "needs-dor", "needs-owner"}
        self.assertFalse(is_dor_candidate(labels))

    def test_reconciliation_reopens_candidacy(self):
        # AC6: explicit reconciliation (label flip back to needs-dor) makes
        # the issue a candidate again -- this is not a permanent exclusion.
        reconciled = dict(self.issues)
        reconciled[423] = {"P0", "from-owner", "needs-dor"}
        self.assertTrue(is_dor_candidate(reconciled[423]))
        self.assertEqual(next_candidate(reconciled), 423)


if __name__ == "__main__":
    unittest.main()
