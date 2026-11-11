"""Issue #222 / ADR-0057: FOLLOW_UP findings are filed and queued, never
dispatched into the current implementer's fix packet.

A stdlib text-conformance gate over `program/config.yaml`'s parsed `prompt:`
string, `program/agents/crew_alpha/config.yaml`'s and
`program/agents/crew_beta/config.yaml`'s parsed `prompt:` strings, and
`program/skills/cross-crew-review/SKILL.md`'s raw text, matching the
established pattern (`tests/test_449_dor_queue_terminalization.py`,
`tests/test_review_contract.py`) rather than exercising code -- the routing
rule this issue fixes is prose an agent reads at cross-crew review time, not
a function with a call graph, so a text gate is the correct and only
adequate seam (per the accepted DoR contract: no new database, scheduler, or
general subsystem).

Reconciles independent `architect` (Codex) and `staff` (Claude) DoR reads
(both READY) per the accepted contract recorded on issue #222
(https://github.com/tushardhara/nightshift/issues/222#issuecomment-5477574014).

Covers the tightened acceptance criteria from that contract:
1. (unchanged, already true) FOLLOW_UP creates/records a separate backlog
   issue via the existing tracker path (`needs-dor`).
2. (unchanged, already true) FOLLOW_UP never blocks current Release
   eligibility.
3. (tightened) FOLLOW_UP does not automatically spawn Architect/Staff/
   Security/crew/engineer work in the current delivery loop, AND the review
   owner does not extend the same implementer's fix work on the current PR
   to cover a FOLLOW_UP entry -- the implementer-facing repair payload
   contains only MERGE_BLOCKER findings.
4. (unchanged, already true) the new item participates later in normal
   READY/priority scheduling.
5. (unchanged) MERGE_BLOCKER/PRE_DEPLOYMENT behavior from ADR-0026/PR #200
   is unchanged.
6. (tightened, scope-limited) dedup is scoped to the IN-ROUND case only,
   reusing ADR-0039's existing fingerprint/consolidate_findings mechanism
   (`nightshift-ctl review consolidate`) -- a FOLLOW_UP finding already
   present in the same round's consolidated findings is filed as at most one
   backlog issue, not one per contributing domain. Cross-PR/cross-time
   duplicate detection is an explicit non-goal.
"""

import os
import re
import sys
import unittest

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "bin"))

import nightshift_review_receipts as receipts  # noqa: E402


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        return f.read()


def _prompt(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["prompt"]


def program_prompt():
    return _prompt("program/config.yaml")


def crew_alpha_prompt():
    return _prompt("program/agents/crew_alpha/config.yaml")


def crew_beta_prompt():
    return _prompt("program/agents/crew_beta/config.yaml")


def cross_crew_review_skill():
    return read("program/skills/cross-crew-review/SKILL.md")


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


def step_7b_block(prompt):
    return _section(
        prompt,
        "7b. CROSS-CREW REVIEW.",
        r"\n  8\. INTEGRATE",
    )


def reviewing_another_crews_pr_block(prompt):
    return _section(
        prompt,
        "## Reviewing another crew's PR",
        r"\n\n  ## Choosing models",
    )


def routing_the_verdict_block(skill_text):
    return _section(
        skill_text,
        "## Routing the verdict",
        r"\n## Anti-patterns",
    )


REQUIRED_CLAUSES = (
    "MERGE_BLOCKER-ONLY",
    "#222, ADR-0057",
    "never extend",
    "FOLLOW_UP` entry",
    "needs-dor",
    "does not spawn",
    "Architect/Staff/Security/crew/engineer",
    "nightshift-ctl review consolidate",
    "same fingerprint",
    "never one per contributing domain",
    "cross-PR/cross-time duplicate detection is out of scope",
)


class ProgramConfigStep7bIsMergeBlockerOnly(unittest.TestCase):
    """AC-3: the implementer-facing fix packet is narrowed at the exact seam
    Program executes (step 7b), not only in the fuller skill prose."""

    def test_all_required_clauses_present(self):
        body = " ".join(step_7b_block(program_prompt()).split())
        for clause in REQUIRED_CLAUSES:
            with self.subTest(clause=clause):
                self.assertIn(clause, body)

    def test_severity_vocabulary_sentence_unchanged(self):
        # The pre-existing sentence `tests/test_review_contract.py` pins in
        # place must still be there, unedited, immediately before the new
        # clause -- this is an addition, not a rewrite.
        body = " ".join(step_7b_block(program_prompt()).split())
        self.assertIn(
            "one consolidated fix packet per round using exactly "
            "`MERGE_BLOCKER`, `PRE_DEPLOYMENT`, and `FOLLOW_UP`.",
            body,
        )

    def test_merge_blocker_pre_deployment_routing_untouched(self):
        # AC-5: the same-reviewer-rechecks / severity-dispute prose survives
        # verbatim after the new clause.
        body = " ".join(step_7b_block(program_prompt()).split())
        self.assertIn("The same reviewer rechecks the same contract", body)
        self.assertIn("You settle a remaining severity dispute", body)


class CrewConfigsMirrorTheSameClauseVerbatim(unittest.TestCase):
    """The issue names both crew configs as seams to patch in the same PR,
    same wording, to avoid drift."""

    def test_crew_alpha_has_all_required_clauses(self):
        body = " ".join(
            reviewing_another_crews_pr_block(crew_alpha_prompt()).split()
        )
        for clause in REQUIRED_CLAUSES:
            with self.subTest(clause=clause):
                self.assertIn(clause, body)

    def test_crew_beta_has_all_required_clauses(self):
        body = " ".join(
            reviewing_another_crews_pr_block(crew_beta_prompt()).split()
        )
        for clause in REQUIRED_CLAUSES:
            with self.subTest(clause=clause):
                self.assertIn(clause, body)

    def test_both_crew_configs_carry_the_identical_new_bullet(self):
        alpha_block = reviewing_another_crews_pr_block(crew_alpha_prompt())
        beta_block = reviewing_another_crews_pr_block(crew_beta_prompt())
        marker = "THE IMPLEMENTER-FACING FIX PACKET IS MERGE_BLOCKER-ONLY"
        alpha_bullet = _section(alpha_block, marker, r"\n  - A clean review")
        beta_bullet = _section(beta_block, marker, r"\n  - A clean review")
        self.assertEqual(alpha_bullet, beta_bullet)

    def test_existing_review_owner_bullet_unchanged(self):
        # `tests/test_review_contract.py::test_review_owner_and_domain_
        # passes_bind_their_evidence` pins these substrings; confirm the new
        # bullet is additive, not a replacement.
        for prompt in (crew_alpha_prompt(), crew_beta_prompt()):
            body = reviewing_another_crews_pr_block(prompt)
            self.assertIn("You are the review owner", body)
            self.assertIn("one deduplicated fix", body)
            self.assertIn("never silently", body)


class SkillFileReinforcesWithoutDuplicatingTheFullRule(unittest.TestCase):
    """AC-1/AC-2/AC-4 were already true in the skill file per the
    independent staff DoR read; this is optional polish, not a required
    rewrite -- confirm the pre-existing correct prose survives and the new
    pointer is present."""

    def test_pre_existing_routing_prose_survives(self):
        body = routing_the_verdict_block(cross_crew_review_skill())
        self.assertIn(
            "a separate work item, not another review loop", body
        )

    def test_reinforcing_pointer_present(self):
        body = " ".join(routing_the_verdict_block(cross_crew_review_skill()).split())
        self.assertIn("#222, ADR-0057", body)
        self.assertIn("nightshift-ctl review consolidate", body)
        self.assertIn("in-round dedup only", body)


# --------------------------------------------------------------------------
# AC-6: a literal fixture reproducing the in-round dedup case, reusing
# ADR-0039's real `consolidate_findings` (bin/nightshift_review_receipts.py)
# rather than a parallel implementation.
# --------------------------------------------------------------------------

def consolidate_for_filing(rows):
    """The literal AC-6 mechanism: run the real `consolidate_findings` (the
    same pure function `nightshift-ctl review consolidate` calls), then
    return only the FOLLOW_UP entries -- these are exactly the candidates
    for backlog-issue filing, one filing decision per consolidated entry,
    never one per contributing domain."""
    consolidated = receipts.consolidate_findings(rows)
    return [entry for entry in consolidated if entry["severity"] == "FOLLOW_UP"]


class InRoundDedupFixture(unittest.TestCase):
    """AC-6: the same FOLLOW_UP finding surfaced by 2+ review domains in one
    round's consolidated findings files as exactly one backlog issue, not
    one per contributing domain. A distinct FOLLOW_UP still files
    separately. MERGE_BLOCKER/PRE_DEPLOYMENT entries never take this path."""

    def setUp(self):
        criterion_a = "unrelated pre-existing cleanup in module X"
        summary_a = "dead helper function left over from a prior refactor"
        criterion_b = "unrelated logging verbosity"
        summary_b = "debug-level log line left in a hot path"

        self.rows = [
            # Same FOLLOW_UP finding, reported independently by two domains
            # in one round -- must fingerprint identically (ADR-0039:
            # fingerprint excludes domain).
            {
                "domain": "staff",
                "severity": "FOLLOW_UP",
                "criterion": criterion_a,
                "summary": summary_a,
                "fingerprint": receipts.fingerprint(criterion_a, summary_a),
            },
            {
                "domain": "security",
                "severity": "FOLLOW_UP",
                "criterion": criterion_a,
                "summary": summary_a,
                "fingerprint": receipts.fingerprint(criterion_a, summary_a),
            },
            # A distinct FOLLOW_UP finding -- must remain a separate entry.
            {
                "domain": "cross_crew",
                "severity": "FOLLOW_UP",
                "criterion": criterion_b,
                "summary": summary_b,
                "fingerprint": receipts.fingerprint(criterion_b, summary_b),
            },
            # A real blocker in the same round -- must never be routed
            # through the FOLLOW_UP-filing path.
            {
                "domain": "staff",
                "severity": "MERGE_BLOCKER",
                "criterion": "criterion 3",
                "summary": "changed code loses data on empty input",
                "fingerprint": receipts.fingerprint(
                    "criterion 3", "changed code loses data on empty input"
                ),
            },
            {
                "domain": "security",
                "severity": "PRE_DEPLOYMENT",
                "criterion": "rollout authorization",
                "summary": "needs a live credential rotation before cutover",
                "fingerprint": receipts.fingerprint(
                    "rollout authorization",
                    "needs a live credential rotation before cutover",
                ),
            },
        ]

    def test_same_finding_from_two_domains_files_exactly_once(self):
        to_file = consolidate_for_filing(self.rows)
        fingerprints = [entry["fingerprint"] for entry in to_file]
        self.assertEqual(len(fingerprints), len(set(fingerprints)))
        self.assertEqual(len(to_file), 2)  # criterion_a (merged) + criterion_b

    def test_the_merged_entry_records_both_contributing_domains(self):
        to_file = consolidate_for_filing(self.rows)
        merged = next(
            e for e in to_file if e["domains"] == sorted(["staff", "security"])
        )
        self.assertEqual(merged["domains"], ["security", "staff"])

    def test_distinct_finding_still_files_as_its_own_issue(self):
        to_file = consolidate_for_filing(self.rows)
        single_domain_entries = [e for e in to_file if e["domains"] == ["cross_crew"]]
        self.assertEqual(len(single_domain_entries), 1)

    def test_merge_blocker_and_pre_deployment_never_enter_the_filing_set(self):
        to_file = consolidate_for_filing(self.rows)
        severities = {e["severity"] for e in to_file}
        self.assertEqual(severities, {"FOLLOW_UP"})

    def test_filing_count_equals_distinct_fingerprints_not_row_count(self):
        # 5 raw finding rows this round; only 2 distinct FOLLOW_UP
        # fingerprints -- filing one issue per row would over-file by 1.
        to_file = consolidate_for_filing(self.rows)
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(len(to_file), 2)

    def test_no_current_loop_dispatch_action_for_a_follow_up_entry(self):
        # AC-3: simulate the current-loop action set an implementer/review
        # owner would take this round -- a FOLLOW_UP entry contributes
        # nothing to it (no charter, no repair dispatch, no
        # Architect/Staff/Security/crew/engineer dispatch), only a filing
        # action, which is out-of-loop bookkeeping.
        to_file = consolidate_for_filing(self.rows)
        merge_blockers = [
            e for e in receipts.consolidate_findings(self.rows)
            if e["severity"] == "MERGE_BLOCKER"
        ]
        implementer_repair_payload = [e["fingerprint"] for e in merge_blockers]
        for entry in to_file:
            self.assertNotIn(entry["fingerprint"], implementer_repair_payload)


if __name__ == "__main__":
    unittest.main()
