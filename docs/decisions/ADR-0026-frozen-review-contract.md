# ADR-0026: Freeze one review contract and one blocker stream per work item

Date: 2026-08-27 · Status: accepted
Deciders: program · Proposers: Claude and Codex independent audits
Conversation: local owner-directed review-system audit · Spec: docs/reviews/REVIEW-CONTRACT-TEMPLATE.md

## Context

Nightshift already requires independent review, but the participating prompts do
not use one severity vocabulary or one immutable review scope. Recent work showed
the failure mode: reviewers found real defects, but they also opened new scope in
later rounds, PM criteria could change during final acceptance, and Program had to
adjudicate several independent blocker streams. A small change could therefore
spend hours moving through superseding PRs even when its implementation bytes did
not change.

Claude and Codex reviewed this independently. Claude recommended a versioned
contract, objective severity rules, one integrator, and content-addressed evidence
reuse. Codex agreed, while finding that prompts alone cannot make the entire gate
transactional: a future control-plane ledger is needed to fail closed before
IMPLEMENT and Release. This ADR deliberately separates the immediate reviewer
runtime fix from that larger control-plane project.

The unmerged proposal in closed PR #185 (ADR-0025) suggested one delivery PR per
work item. It is historical input, not current authority. This ADR adopts that
useful part and adds the missing review contract and finding rules.

## Options considered

### A — Fix only the current PR

Rejected. The repeated loops occurred across several PRs and reviewers. Editing
one PR would leave the same ambiguity in every future review.

### B — Standardise reviewer runtime now; add transactional gates separately

Chosen. The Program bundle gets one frozen contract, one severity model, one
review owner, and a CI test that prevents its prompts from drifting. This changes
what reviewers actually receive while keeping the patch small enough to review.

### C — Add the complete review ledger and dispatch policy in this PR

Rejected for this work item. New control-plane schema, CLI verbs, migration,
dispatch policy, and evidence invalidation rules are a separate behavioural
change. Combining them with the prompt correction would recreate the oversized,
slow-to-review PR pattern this decision is intended to stop.

## Decision

Each work item has one versioned review contract in a committed work-item/spec
artifact, frozen at the wave checkpoint before its first implementation commit.
The mutable PR body only links or mirrors that exact artifact. The contract records:

- stable criterion identifiers and exact acceptance text;
- explicit out-of-scope items;
- base SHA, contract version, required review domains, and one review owner;
- the ADR and design evidence the implementation may rely on.

Changing criteria, scope, domains, or ADR after implementation starts creates a
new contract version. The change is visible and previous affected approvals are
invalidated; PM cannot quietly rewrite a criterion during final sign-off.

All code, Staff, Security, UX, and cross-crew review findings use exactly three
severities:

- `MERGE_BLOCKER`: the diff violates a frozen criterion, or introduces/activates
  a correctness, security, data-loss, or default-branch-integrity failure. It must
  cite the criterion or invariant, exact changed hunk, reachable execution/data
  path, and a concrete failure scenario or safe deterministic reproduction. A
  reproduction is never executed against a live system.
- `PRE_DEPLOYMENT`: source may merge, but cutover is unsafe until named live,
  migration, rollout, authorization, or environment evidence exists. It blocks a
  deployment, never a source merge.
- `FOLLOW_UP`: pre-existing or unreachable behaviour, polish, convention
  preference, untriggered scale work, or a reviewer request to add work outside
  the frozen scope. It becomes a separate work item and does not hold the current
  PR. A diff that itself changes explicitly excluded behaviour still violates the
  frozen contract and may be a `MERGE_BLOCKER` with the required causal proof.

After the first review round, a new `MERGE_BLOCKER` is valid only when it is tied
to bytes or criteria changed since that round, or to a direct causal dependency
the earlier review demonstrably missed. The reviewer states which case applies.
The same reviewer rechecks the same contract.

The assigned cross-crew reviewing lead is the review owner. It consolidates all
domain findings into one fix packet per round. It may resolve duplicates and
severity disputes only with a visible rationale; it cannot silently downgrade a
finding. Program arbitrates a remaining dispute once in `.omni/decisions.md`.

A pass is scoped to the reviewed head SHA/tree, domain, files, and declared
dependency seams. Content review may be reused for identical blob OIDs only when
the affected seams are unchanged. CI, commit/authorship metadata, PR body,
authorization, and installed/live-runtime evidence are rerun and are never
transferred merely because a tree or blob matches.

One work item has one active delivery PR. A replacement PR must name the PR it
supersedes and the reason; it does not reset the frozen contract or erase findings.
Release consumes the consolidated receipt and verifies its required gates. It does
not start a new review or invent new acceptance scope.

This PR enforces vocabulary and required proof in the shipped Program prompts and
with `python3 -m unittest tests.test_review_contract`. Transactional storage and
fail-closed dispatch/release checks are explicitly follow-up work, not falsely
claimed by this ADR.

## Consequences

Reviewers can still stop unsafe code, but must show why the changed artifact causes
the failure. Deployment evidence remains mandatory without holding safe source
integration. Review rounds become bounded and auditable, and content that truly did
not change can retain the appropriate review evidence.

The immediate enforcement is at the Program-bundle boundary. Until the follow-up
control-plane ledger lands, a broken external caller could omit the contract; the
prompt conformance test cannot detect that runtime omission.

## Cost

No infrastructure or subscription cost. Reviews become slightly more structured
and should consume less quota by eliminating duplicate and late-scope rounds. The
future transactional ledger has its own ADR and cost analysis.

## One-way door?

No. The template and prompts can be superseded. Existing Git and review history
remain append-only.

## Scale tier

Tier 0 process metadata. Revisit the storage design when more than one host must
coordinate review state or when measured review-ledger contention exceeds the
single SQLite control plane.

## Reversal trigger

Supersede this ADR if two completed waves show either (a) a real changed-code
regression incorrectly classified below `MERGE_BLOCKER`, or (b) a median of more
than one avoidable late blocker per PR despite reviewers following this contract.
