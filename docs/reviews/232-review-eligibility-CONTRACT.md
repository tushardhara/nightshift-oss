# Review contract

Copy this template into a committed work-item/spec artifact and freeze that file at
the wave checkpoint before the first implementation commit. The delivery PR links
and may mirror the exact committed version; its mutable body is never the authority.
Do not edit a frozen version in place.

## Identity

- Work item: GitHub issue #232, "#228C — reuse unchanged review evidence and
  calculate eligibility" (tushardhara/nightshift). state:ready, pm Definition-of-Ready
  verdict READY posted https://github.com/tushardhara/nightshift/issues/232#issuecomment-5452154503
  (2026-08-28T11:50:09Z).
- Committed contract path and blob OID: `docs/reviews/232-review-eligibility-CONTRACT.md`;
  blob OID is the exact-content hash of this file at the freezing commit — read it
  with `git rev-parse <freezing-commit>:docs/reviews/232-review-eligibility-CONTRACT.md`
  (recorded verbatim in the freezing commit message and in
  `.omni/registry-crew_alpha.json`, since a self-referential hash cannot be embedded
  in the hashed content itself).
- Contract version: v1.
- Base SHA: `4243d1b838af79eac311d60269f9ce1e14bd0a66` (checkpoint id 3, origin/main
  tip at freeze time, includes #228A and #231/PR #246).
- ADR: `docs/decisions/ADR-0040-review-eligibility.md` (authored in this work item;
  single-author — see the ADR's own §Context flag — pending staff review before
  implementation proceeds).
- Review owner: crew_beta (cross-crew, round-robin from crew_alpha as implementer,
  same rotation #231 used).
- Frozen at (UTC): 2026-08-28T12:20:00Z.

## Acceptance criteria

Verbatim from the pm's Definition-of-Ready judgment on issue #232
(#issuecomment-5452154503, §1). IDs are stable identifiers, not reordered.

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | A validated round for domain D at head H2 is stored as new `review_receipts`/`review_findings` rows keyed `(task_id, H2, D)`. No existing row keyed `(task_id, H1, D)` for any prior head H1 is UPDATE'd or DELETE'd. | Test: seed a receipt/finding at H1, validate a round at H2, assert the H1 rows are byte-identical after. |
| AC-2 | A receipt for domain D at a prior head H1 is reusable onto candidate head H2 only when **all** hold: (a) its `review_receipts` row has `verdict='pass'`; (b) zero `review_findings` rows exist for `(task_id, H1, D)` — a receipt with *any* finding, any severity, is never reused; (c) every path in D's covered scope (per #228A's `_classify` — the paths whose ownership/high-risk/conditional triggers include D, or the full changed-path set for `code_review`/`pm_acceptance`) has an identical blob OID diffing `base_sha..H1` vs `base_sha..H2` — any add/delete/modify/rename/typechange inside that scope invalidates D; (d) the risk-group/conditional-domain classification a path triggers (`HIGH_RISK_GROUPS`/`CONDITIONAL_GROUPS` in `bin/nightshift_review_plan.py:16-19` — this item's operational definition of "declared dependency seam") is identical between the two diffs for every path in scope, even if that path's own bytes didn't move (e.g. the ruleset blob OID itself changed). | One property test per invalidation cause: in-scope path added/removed/modified/renamed, ruleset blob OID changed, and one proving an out-of-scope path change does NOT invalidate. |
| AC-3 | CI status, commit/authorship/provenance, PR-body/authorization, and live/runtime evidence are never read from `review_receipts` and are always re-verified fresh, regardless of AC-2. #232 adds no reuse path for these categories — unchanged from ADR-0026. | Conformance test (same shape as #231 AC-5) asserting the eligibility code imports/calls nothing GitHub/live-check-shaped. |
| AC-4 | `review eligibility` for an exact `(task_id, head_sha)` returns `eligible=true` only when: (a) the task resolves unambiguously (#228A's `_read_task` rules); (b) a fresh `review plan` for that exact head reproduces the `plan_digest` every relied-on receipt (direct or reused) assumed — ruleset drift invalidates; (c) head/tree resolve per #228A's `_target`; (d) every domain in the frozen contract's required-domains set (#231's `parse_required_domains`) has a clean receipt at this exact head OR a validly-reused one per AC-2; (e) zero `MERGE_BLOCKER` rows in `review_findings` for any required domain's relied-on receipt. | One integration test per failing sub-condition (unknown task, drifted ruleset, non-descendant head, a required domain with no receipt, a required domain whose only receipt has an open blocker) + one happy-path mixing direct and reused receipts. |
| AC-5 | Eligibility emits one canonical-JSON object; digest = `sha256(canonical_json(...))`, same discipline as #228A/#231's `report_digest`. Minimum fields: `task_id`, `plan_digest`, exact `head_sha`/tree, and per required domain which receipt (head_sha, domain, verdict, direct-or-reused) satisfied it. Two runs over byte-identical inputs produce a byte-identical digest; any input change changes it. Exact wire contract for #233's consumption is **not** frozen here. | Determinism test (two runs, same digest) + a receipt-set-changed test (one new finding) that breaks it. |
| AC-6 | Missing task, missing/invalid head, a required domain with zero usable receipts, a plan-digest mismatch, or a `control.db` read error → `eligible=false` with a reason list naming **only** the domains needing fresh dispatch. Task/plan/head-shaped faults use a distinct non-domain reason code, never a fabricated domain list. Exit-code convention matches #228A/#231 (fail-closed exit code, e.g. `3`). | One test per fail-closed branch (mirrors #231 AC-3), asserting both the false verdict and the exact minimal domain list — no extras, no omissions. |
| AC-7 | If H1 == H2 (no change since last validation), eligibility runs the same AC-2/AC-4 checks, not a silent no-op pass (the diff is trivially empty, so all domains transfer). | Test with identical old/new head; digest matches an equivalent all-direct run. |
| AC-8 | If no receipts exist yet for the task, `eligible=false`, reason lists every required domain as "no receipt yet" — not an error/crash. | Fresh-task test. |

## Explicitly out of scope

- #233's digest wire/consumer contract (properties fixed by AC-5; exact shape left
  open, same discipline #231's `report_digest` used before #232/#233 existed).
- Persistence of free-text reviewer-declared "seams" as authoritative input — seams
  are resolved mechanically from `program/review-risk.json`'s currently committed
  rule-groups (ADR-0040 §Decision), never from prose.
- Any change to `review_receipts`/`review_findings`/`review_dispatch_markers`
  primary keys or #231's validation matrix — #232 is a read-only consumer.
- Any change to #228A's `shadow_only` authority or CLI surface beyond what
  eligibility needs to read (plan/classification data).
- Activating conditional routing, publication, transport, retries, integration,
  rollback, #213–#223.

## Required review domains

- [x] PM acceptance -- already done via the DoR thread on issue #232
      (#issuecomment-5452154503, verdict READY)
- [x] Different-vendor code review
- [x] QA
- [x] Cross-crew review -- crew_beta is review owner
- [x] Staff / ADR consistency -- ADR-0040 required before the first implementation
      commit (`bin/nightshift-ctl` is in `program/review-risk.json`'s
      `high_risk_globs.control_plane` group)
- [x] Security -- control-plane surface (`bin/nightshift-ctl`,
      `bin/nightshift_review_receipts.py`); mandatory per §3 of the DoR judgment
- [ ] UX -- no human-facing flow or operator interface changes; this is a CLI/data
      read path only

Machine-readable parsing of this checklist follows the rule in
`docs/reviews/REVIEW-CONTRACT-TEMPLATE.md` under its own "Required review domains"
section (per #231 AC-9): each row is matched by a fixed canonical-label PREFIX
(`PM acceptance`, `Different-vendor code review`, `QA`, `Cross-crew review`,
`Staff / ADR consistency`, `Security`, `UX`), independent of any parenthetical or
`--`-delimited rationale that follows; `[x]`/`[X]` means required, `[ ]` means not
required for this work item. `nightshift_review_receipts.parse_required_domains()`
fails closed (raises) on an unrecognized label, a label appearing twice, or a
missing canonical row — it never guesses.

Removing a domain after freeze requires a new contract version and rationale.

## Finding receipt

Every finding uses one severity from ADR-0026.

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 |  | `MERGE_BLOCKER` / `PRE_DEPLOYMENT` / `FOLLOW_UP` |  |  |  | open |  |

The review owner publishes one deduplicated fix packet per round. A severity change
must retain the original finding and record the rationale.

Never execute a reproduction against a live system. A production-only security or
data-loss risk may be proven by a concrete causal failure scenario and safe evidence.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

Content evidence may transfer only across identical reviewed blobs with unchanged
dependency seams. Re-run CI, provenance, PR-body, authorization, and live/runtime
checks.

## Contract amendments

Never rewrite the frozen rows above. Create the next version and record:

- previous version and reason;
- criteria, domains, ADR, or scope changed;
- attestations invalidated by that change;
- implementation work that must be reconsidered.

No amendments yet. This is v1, frozen at the timestamp in Identity above.
