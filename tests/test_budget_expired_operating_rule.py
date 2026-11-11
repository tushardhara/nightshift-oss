"""Issue #218 / ADR-0047: Program's operating rule for a `budget_expired`
scheduling verdict.

A stdlib text-conformance gate over `program/config.yaml`'s own prompt,
matching the established pattern for Program-instruction contracts
(`tests/test_review_contract.py`, `tests/test_human_first_communication.py`)
rather than exercising code -- Program's decision procedure here is prose,
not a function, so this is the smallest existing seam #218's own "Expected
code seams" names ("focused prompt/contract regression test ... for Program
instructions").

Covers the issue's 7 acceptance criteria, frozen verbatim in the DoR pass
(`.omni/reports/2026-08-29-...-218-dor-verdict.md`, branch `crew_alpha/218-dor`):
1. `budget_expired` recognized as a scheduling verdict, not infra failure.
2. No busy retry loop after a budget denial.
3. `EXTEND_ONCE` allowed only when no extension already exists.
4. `SPLIT` creates/requests smaller bounded work, not the same task unchanged.
5. `BLOCK` records the concrete external/dependency reason.
6. `FAIL` records why the current approach cannot satisfy the frozen contract.
7. Program reports the chosen action in the normal human-first status format.

Also covers the security MERGE_BLOCKER fix on PR #355 (2026-08-30): the
`FAIL` -> drop disposition must not bypass the pre-existing tracker rule
(same file, "YOU close an issue, and only when `nightshift-ctl issue
status --issue <n>` says closable AND that crew's pm has signed
acceptance"). The bullet now routes the drop through that same gate
instead of closing unconditionally, and reports `Blocked` -- never a
silent close -- when the gate is not met.
"""

import os
import re
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The frozen ADR-0027 status vocabulary (tests/test_human_first_communication.py
# owns the canonical copy; duplicated here deliberately -- this file must keep
# working even if that module's internals move, and the whole point of AC7 is
# that this rule invents no status outside this closed set).
STATUSES = (
    "On track",
    "Blocked",
    "Ready for review",
    "Ready to merge",
    "Merged",
    "Superseded",
    "Done",
    "Closed without completion",
)


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


def program_prompt():
    with open(os.path.join(REPO, "program", "config.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["prompt"]


def budget_expired_section(prompt):
    """Isolate the bullet this rule lives in, so assertions can't pass by
    accidentally matching unrelated prose elsewhere in the same large prompt."""
    marker = "BUDGET_EXPIRED IS A SCHEDULING VERDICT, NOT A CRASH."
    assert marker in prompt, "budget_expired operating rule bullet not found"
    start = prompt.index(marker)
    # Bounded by the next top-level bullet at the same "  - " indentation.
    rest = prompt[start + len(marker):]
    m = re.search(r"\n  - [A-Z]", rest)
    end = start + len(marker) + (m.start() if m else len(rest))
    return prompt[start:end]


class BudgetExpiredIsAVerdictNotAFailure(unittest.TestCase):
    """AC1, AC2."""

    def test_verdict_language_not_infrastructure_failure(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("SCHEDULING VERDICT, NOT A CRASH", body)
        self.assertIn("reason_code: budget_expired", body)
        self.assertIn("control plane's own considered answer", body)
        self.assertIn("never read it as infrastructure failure", body)
        self.assertIn("dead worker", body)

    def test_no_busy_retry_loop(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        for required in (
            "decide, never loop",
            "reason to retry the same acquire",
            "never keep retrying a denied slot",
        ):
            self.assertIn(required, body)


class FirstExpiryChoosesExactlyOneOfFour(unittest.TestCase):
    """AC3, AC4, AC5, AC6."""

    def test_names_all_four_actions_as_a_first_expiry_choice(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("EXACTLY ONE of four", body)
        for action in ("EXTEND_ONCE", "SPLIT", "BLOCK", "FAIL"):
            self.assertIn(f"`{action}`", body)

    def test_extend_once_gated_on_no_existing_extension(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn(
            "allowed only when no extension already exists", body
        )
        self.assertIn("extension_seconds` is null", body)
        self.assertIn("task extend --id", body)
        self.assertIn("--reason", body)
        self.assertIn("already_extended", body)

    def test_split_creates_smaller_bounded_work_not_the_same_task(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("smaller, bounded (S/M) work items", body)
        self.assertIn("rather than continuing the same oversized task unchanged", body)

    def test_block_records_concrete_external_dependency_reason(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("concrete external or dependency reason", body)
        self.assertIn(".omni/blocked.md", body)
        self.assertIn("state:blocked", body)

    def test_fail_records_why_the_contract_cannot_be_satisfied(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn(
            "why the current approach cannot satisfy the frozen contract", body
        )
        self.assertIn(".omni/decisions.md", body)

    def test_fail_and_split_do_not_overlap(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn('never means "cut scope and keep going"', body)
        self.assertIn("SPLIT`'s job", body)
        self.assertIn("the four actions never overlap", body)


class ExpiryAfterExtensionDropsExtendOnce(unittest.TestCase):
    """AC3 (second half) + AC2: no repeat extension request, no retry."""

    def test_only_split_block_fail_after_the_one_extension(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("AFTER the one extension", body)
        self.assertIn("EXACTLY ONE of `SPLIT` / `BLOCK` / `FAIL`", body)
        self.assertIn("EXTEND_ONCE` is not available a second time", body)
        self.assertIn("never request another extension", body)


class ReportsInHumanFirstFormat(unittest.TestCase):
    """AC7 -- and that no status outside the frozen ADR-0027 vocabulary is
    invented for this rule."""

    def test_reports_the_chosen_action_first_using_the_first_screen(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("FIRST thing in that task's next status report", body)
        self.assertIn("human-first six-field first screen", body)

    def test_every_status_word_used_is_in_the_frozen_vocabulary(self):
        body = budget_expired_section(program_prompt())
        # \s+ (not a literal space) because the YAML prompt line-wraps
        # "report status" and its backtick-quoted word independently of
        # any given edit -- a wrap must never make this check pass by
        # accident missing a status word (WA-008).
        used = set(re.findall(r"report status\s+`([^`]+)`", body))
        self.assertTrue(used, "expected at least one `report status ...` phrase")
        for status in used:
            self.assertIn(status, STATUSES)
        # And the two FAIL-branch statuses this rule actually documents:
        self.assertIn("Closed without completion", used)
        self.assertIn("Superseded", used)
        # Guard against reintroducing a status this contract deliberately
        # rejected during DoR (there is no "Failed" in the frozen tuple).
        self.assertNotIn("Failed", body)

    def test_fail_drop_disposition_closes_the_issue_not_planned(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("NOT_PLANNED", body)
        self.assertIn("Closed without completion", body)

    def test_fail_replan_disposition_marks_task_superseded(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("state=superseded", body)
        self.assertIn("name the successor\n    task/ADR".replace("\n    ", " "), body)


class FailDropRoutesThroughTheExistingTrackerCloseGate(unittest.TestCase):
    """Security MERGE_BLOCKER on PR #355: `FAIL` -> drop must not invent a
    parallel issue-closing path. It has to reuse the SAME `nightshift-ctl
    issue status --issue <n>` closable + pm-signoff gate the TRACKER step
    earlier in this same prompt already requires for every issue closure
    (`program_prompt()` -- not a copy, the real committed text), and must
    NOT close the GitHub issue when that gate is not met.
    """

    def test_drop_marks_the_task_dropped_before_touching_the_issue(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("mark this task `state=dropped`", body)

    def test_drop_is_gated_on_the_same_closable_and_pm_signoff_check(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("nightshift-ctl issue status --issue", body)
        self.assertIn("`closable`", body)
        self.assertIn("pm` has signed off", body)

    def test_drop_gate_reuses_the_tracker_rule_rather_than_a_new_path(self):
        prompt = program_prompt()
        # The TRACKER step's own closing rule, quoted verbatim, must exist
        # elsewhere in the SAME prompt -- this is the gate being reused,
        # not a phrase invented locally in the budget_expired bullet.
        self.assertIn(
            "YOU close an issue, and only when `nightshift-ctl issue status",
            prompt,
        )
        self.assertIn("says closable AND that crew's pm has signed", prompt)
        body = " ".join(budget_expired_section(prompt).split())
        self.assertIn("the SAME gate the TRACKER step above already requires", body)
        self.assertIn("never a FAIL-specific shortcut around the TRACKER gate", body)

    def test_lone_drop_cannot_satisfy_closable_and_must_not_close(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("excluded from that command's own", body)
        self.assertIn("`active` set", body)
        self.assertIn("closable` reads true only when some OTHER task", body)
        self.assertIn("closable: false` and stays that way", body)
        self.assertIn("do NOT close the GitHub issue", body)

    def test_gate_not_met_reports_blocked_not_a_silent_close(self):
        body = " ".join(budget_expired_section(program_prompt()).split())
        self.assertIn("report status `Blocked`", body)
        self.assertIn(".omni/blocked.md", body)
        self.assertIn("human-equivalent pm/product call", body)


class RuleUsesTheRealHumanCommunicationContract(unittest.TestCase):
    """Cross-check against the actual frozen vocabulary source, not just this
    file's own hardcoded copy -- catches the two ever drifting apart."""

    def test_local_statuses_copy_matches_the_committed_contract(self):
        contract = read("docs/HUMAN-COMMUNICATION.md")
        for status in STATUSES:
            self.assertIn(status, contract)


if __name__ == "__main__":
    unittest.main()
