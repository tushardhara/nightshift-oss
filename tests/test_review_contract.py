"""ADR-0026: shipped reviewers share one frozen contract and severity model.

This is a stdlib text conformance gate, matching the bundle's dependency-free CI.
It prevents one reviewer or crew from drifting back to a private blocker language.
"""

import os
import re
import unittest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REVIEWERS = (
    "program/agents/crew_alpha/agents/reviewer_claude/config.yaml",
    "program/agents/crew_alpha/agents/reviewer_codex/config.yaml",
    "program/agents/crew_beta/agents/beta_reviewer_claude/config.yaml",
    "program/agents/crew_beta/agents/beta_reviewer_codex/config.yaml",
)

ENGINEER_REVIEWERS = (
    "program/agents/crew_alpha/agents/eng_claude/config.yaml",
    "program/agents/crew_alpha/agents/eng_codex/config.yaml",
    "program/agents/crew_beta/agents/beta_eng_claude/config.yaml",
    "program/agents/crew_beta/agents/beta_eng_codex/config.yaml",
)

COMMON_REVIEW_CONSUMERS = REVIEWERS + ENGINEER_REVIEWERS + (
    "program/skills/cross-crew-review/SKILL.md",
    "program/agents/crew_alpha/skills/cross-review/SKILL.md",
    "program/agents/crew_beta/skills/cross-review/SKILL.md",
    "program/agents/staff/config.yaml",
    "program/agents/security/config.yaml",
    "program/agents/ux/skills/design-review/SKILL.md",
)


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as source:
        return source.read()


class FrozenReviewContractTest(unittest.TestCase):
    def test_template_freezes_identity_scope_domains_and_evidence(self):
        body = read("docs/reviews/REVIEW-CONTRACT-TEMPLATE.md")
        for required in (
            "committed work-item/spec artifact", "wave checkpoint",
            "mutable body is never the authority",
            "Committed contract path and blob OID:",
            "Contract version:", "Base SHA:", "Review owner",
            "Explicitly out of scope", "Required review domains",
            "MERGE_BLOCKER", "PRE_DEPLOYMENT", "FOLLOW_UP",
            "Head SHA / tree", "Blob OIDs and dependency seams",
            "Failure scenario or safe reproduction and evidence",
            "Never execute a reproduction against a live system",
            "Re-run CI, provenance, PR-body, authorization, and live/runtime",
        ):
            self.assertIn(required, body)

    def test_every_domain_reviewer_uses_the_exact_severity_vocabulary(self):
        for path in COMMON_REVIEW_CONSUMERS:
            body = read(path)
            with self.subTest(path=path):
                for severity in ("MERGE_BLOCKER", "PRE_DEPLOYMENT", "FOLLOW_UP"):
                    self.assertIn(severity, body)
                legacy = (
                    r"Report blocking issues,\s*non-blocking issues,\s*and suggestions",
                    r"Three lists\s*[—-]\s*BLOCKING,\s*SHOULD-FIX,\s*NOTE",
                    r"BLOCKING,\s*NON-BLOCKING,\s*SUGGESTIONS",
                )
                for pattern in legacy:
                    self.assertIsNone(re.search(pattern, body, re.IGNORECASE), pattern)

    def test_code_reviewers_require_causal_proof_and_bound_rechecks(self):
        for path in REVIEWERS:
            body = read(path)
            with self.subTest(path=path):
                for required in (
                    "frozen review contract", "exact changed hunk",
                    "reachable causal path",
                    "failure scenario or safe deterministic",
                    "live system",
                    "changed bytes or criteria", "direct dependency",
                    "same frozen contract", "head SHA/tree", "blob OIDs",
                    "dependency seams", "CI, provenance, PR-body, authorization",
                ):
                    self.assertIn(required, body)

    def test_review_purpose_engineers_cannot_emit_the_old_taxonomy(self):
        for path in ENGINEER_REVIEWERS:
            body = read(path)
            with self.subTest(path=path):
                for required in (
                    "frozen review contract", "Report exactly `MERGE_BLOCKER`",
                    "`PRE_DEPLOYMENT`", "`FOLLOW_UP`", "exact changed",
                    "reachable causal path", "failure scenario or safe deterministic",
                    "live system",
                ):
                    self.assertIn(required, body)

    def test_program_freezes_and_consolidates_one_review_stream(self):
        body = read("program/config.yaml")
        for required in (
            "FREEZE THE REVIEW CONTRACT", "REVIEW-CONTRACT-TEMPLATE.md",
            "committed work-item/spec", "never a mutable PR body",
            "one cross-crew review owner", "one consolidated fix packet per round",
            "same reviewer rechecks", "consolidated cross-crew receipt",
            "exactly one active delivery PR", "inherits the contract and open findings",
        ):
            self.assertIn(required, body)

    def test_canonical_review_uses_committed_contract_and_safe_proof(self):
        body = read("program/skills/cross-crew-review/SKILL.md")
        for required in (
            "committed work-item/spec artifact", "freeze it at the wave checkpoint",
            "mutable body is never the authority", "failure scenario",
            "safe deterministic reproduction", "Never execute a reproduction",
        ):
            self.assertIn(required, body)

    def test_review_owner_and_domain_passes_bind_their_evidence(self):
        for path in (
            "program/agents/crew_alpha/config.yaml",
            "program/agents/crew_beta/config.yaml",
        ):
            body = read(path)
            with self.subTest(path=path):
                self.assertIn("You are the review owner", body)
                self.assertIn("one deduplicated fix", body)
                self.assertIn("never silently", body)
        for path in (
            "program/agents/staff/config.yaml",
            "program/agents/security/config.yaml",
            "program/agents/ux/skills/design-review/SKILL.md",
        ):
            body = read(path)
            with self.subTest(path=path):
                for required in (
                    "head SHA/tree", "files", "blob OIDs", "dependency seams",
                    "evidence", "consolidated receipt",
                ):
                    self.assertIn(required, body)

    def test_pm_cannot_silently_change_frozen_criteria(self):
        for path in (
            "program/agents/crew_alpha/agents/pm/config.yaml",
            "program/agents/crew_beta/agents/beta_pm/config.yaml",
        ):
            body = read(path)
            with self.subTest(path=path):
                self.assertIn("may not silently rewrite", body)
                self.assertIn("visible new contract version", body)
                self.assertIn("invalidates every affected approval", body)

    def test_release_consumes_receipt_without_reopening_scope(self):
        body = read("program/agents/release/config.yaml")
        for required in (
            "consolidated receipt", "do not start a new review",
            "invent acceptance scope", "PRE_DEPLOYMENT", "blocks cutover",
            "identical blobs with unchanged seams", "must be rerun",
            "exactly one active delivery PR", "superseded PR",
            "retains the frozen contract and findings",
        ):
            self.assertIn(required, body)


if __name__ == "__main__":
    unittest.main()
