# Review contract

## Identity

- Work item: #471 (child of #236) — derive worker_semaphore dispatch admission from control-plane state
- Committed contract path and blob OID: `docs/reviews/471-CONTRACT.md` (blob OID recorded once crew_alpha commits it as the first commit of the build branch)
- Contract version: 2 (v1 froze the 9 ACs pre-build; v2 appends the round-1/2
  finding receipt, scoped attestations, and PM self-sign -- the 9 ACs
  themselves are unchanged from v1)
- Base SHA: `20dee546c8c03f5d9fd1e8db23eb56ae89008b34` (checkpoint id 34, wave `236`)
- ADR: ADR-0062 (`docs/decisions/ADR-0062-*.md`, committed same branch, addendum incorporating staff's required fixes)
- Review owner (cross-crew lead, not the author): crew_beta lead (round-robin: crew_alpha authors → crew_beta reviews)
- Frozen at (UTC): 2026-09-01 (this turn)

## Acceptance criteria

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | `worker_semaphore` derives `slot_id`/`task_id`/`generation` from a control-plane lookup keyed on `(kind, expected_agent, expected_title, expected_purpose)` inside the existing `BEGIN IMMEDIATE` transaction; no caller-supplied `slot_id`/`task_id`/`generation` is read or required | Code diff + unit test exercising the new lookup path directly |
| AC-2 | Zero-match denies; more-than-one-match denies (never resolved by a heuristic) | Deterministic test constructing both cases |
| AC-3 | Migration adds `one_active_qa_review_continuation` (task_id, continuation_kind) partial index AND `one_undispatched_dispatch_identity` (expected_agent, expected_title) partial index, both `WHERE state='active'` scoped as specified in ADR-0062 | Migration diff + schema inspection test |
| AC-4 | Both new indexes are guarded by a race-tolerant precheck (mirroring `add_column`/`rename_column`) that detects pre-existing violating rows and denies/reports rather than letting `CREATE UNIQUE INDEX` raise `IntegrityError` uncaught through `ensure_schema` | Test constructing a legacy-duplicate row for each index class, asserting a clean denial/report and NOT an uncaught exception, and that `nightshift-ctl` does not crash org-wide as a result |
| AC-5 | `_derive_normal_phase`'s qa/review branch gets a matching pre-INSERT uniqueness check (deny on a second concurrent acquire for the same `(task_id, continuation_kind)`) | Test: two concurrent `slot acquire --purpose review` for the same task, second denies cleanly |
| AC-6 | All three dispatch paths (NEW/engineer, continuation QA/reviewer/implementer, oncall-reserve) succeed end-to-end via direct `sys_session_send` calls carrying only schema-legal fields (`agent`, `title`, `args.purpose`/`input`/etc — no `slot_id`/`task_id`/`generation`) | Deterministic integration test per path |
| AC-7 | Does not weaken the fail-closed invariant: no fail-open, `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN` untouched, one-slot-one-use compare-and-set preserved, `purpose`/`input` never parsed for hidden identity | Code review + existing fail-open tests still pass unmodified |
| AC-8 | Budget/capacity accounting unaffected (backfill and new rows correctly excluded/included per `state='active'` filtering) | Existing budget tests still pass; new test for the two new indexes' interaction with capacity counting |
| AC-9 | ADR-0062 (with addendum) committed as first commit of the build branch; PR body discloses this is #471 (P0, part of #236) and links #472 as the separate non-blocking follow-up | PR review |

## Explicitly out of scope

- #472 (WA-5 admission backfill) — separate PR, does not gate this one.
- Narrowing Working Agreement #5 or correcting the crew registries' "dispatch is sterile" claims — filed as fast-follow, not this PR.
- Any change to `sys_session_send`'s external tool schema (out of this repo's control).
- Flattening the org (#432 Option A) — decided against in ADR-0062.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review
- [x] QA
- [x] Cross-crew review
- [x] Staff / ADR consistency
- [x] Security (touches the sensitive set — the dispatch authorization control plane)
- [ ] UX

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | Staff/ADR | MERGE_BLOCKER (pre-empted) | AC-4 | `ensure_schema`'s bare `CREATE UNIQUE INDEX` | Legacy-duplicate row present → uncaught `IntegrityError` → org-wide `nightshift-ctl` crash | resolved-in-design | Folded into AC-4 before build started (staff review, ADR-0062 addendum) |
| F-2 | Staff/ADR | MERGE_BLOCKER (pre-empted) | AC-3 | Derivation query has no task_id filter | Cross-task `(agent,title)` collision undetected without the second index | resolved-in-design | Folded into AC-3 before build started (staff review, ADR-0062 addendum) |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| Staff/ADR | staff | INCONSISTENT → addendum incorporated | n/a (pre-build) | ADR-0062 draft | `.omni/decisions.md` 2026-09-01 "staff verdict" entry |
| Staff/ADR | staff | CONSISTENT (round 2) | `3bc82ef` | -- | PR #473 round-2 recheck |
| Different-vendor code review | architect | MERGE_BLOCKER (M-1, round 1) → F-1/F-2 fixed, M-1 downgraded PRE_DEPLOYMENT (round 2) | round 1: `e2be2c3`; round 2: `f64ed6e` | `tests/test_admission_derivation.py::AC6AllThreePathsEndToEnd` | PR #473 comments; `.omni/decisions.md` 2026-09-01 "round 2 recheck" |
| Security | security | MERGE_BLOCKER (M-2/F-SEC-1, round 1) → CONSISTENT (round 2) | round 1: `e2be2c3`; round 2 (fix): `3bc82ef` | `bin/nightshift-ctl::cmd_slot_acquire` | PR #473 comments |
| Cross-crew | crew_beta | CONSISTENT | `f64ed6e` | -- | PR #473 round-2 recheck |
| PM acceptance | crew_alpha lead (WA-5 self-sign) | ACCEPTED, AC-1 through AC-9 | `f64ed6e` | -- | `.omni/ledger.md` 2026-09-01 PM self-sign entry; below |

## Round 1/2 finding receipt (post-build; ID namespace `R<round>-<kind>-<n>`,
distinct from the pre-build `F-1`/`F-2` staff findings above, which stay as
recorded)

| ID | Domain | Severity | Criterion or invariant | Failure scenario / fix | Status |
|---|---|---|---|---|---|
| R1-M-1 | Code review (architect) | MERGE_BLOCKER | AC-6 | AC-6 tests never crossed the real `sys_session_send` schema boundary, omitted `args.input` | round 1: schema validation + `args.input` added; round 2: schema source corrected (see R2-M-1) |
| R1-M-2 | Security (F-SEC-1) | MERGE_BLOCKER | AC-3/AC-4/AC-5 | `cmd_slot_acquire`'s shared INSERT unguarded for PLANNING/NEW/continuation-implementer/oncall paths (only qa/review continuation had a guard) → uncaught `IntegrityError` on a legitimate cross-task same-identity sequence | fixed commit `3bc82ef`; security CONSISTENT round 2 |
| R1-F-1 | Code review (architect) | FOLLOW_UP | AC-4 | AC-4 tests asserted a warning fired, not the exact conflicting tuple | fixed commit `3bc82ef`; reverified fixed round 2 |
| R1-F-2 | Code review (architect) | FOLLOW_UP | AC-9/PR body | PR body's pre-change-test claim internally inconsistent (19/19 vs 18/19) | fixed commit `3bc82ef` (corrected to the accurate, freshly-verified 20/22 on the then-current 22-test file); reverified fixed round 2 |
| R2-M-1 | Code review (architect) | MERGE_BLOCKER → PRE_DEPLOYMENT (program call) | AC-6 | R1-M-1's schema was hand-transcribed ("hearsay"), not sourced authoritatively; `self.policy()` still can't cross into the real, external, unvendored engine's client/hook chain from a pre-merge test | schema fixed commit `f64ed6e` (grounded in a live `ToolSearch(select:sys_session_send)` call, program turn, 2026-09-01); remainder downgraded to PRE_DEPLOYMENT -- structurally unclosable pre-merge, closed by a real post-merge counted-agent dispatch probe (tracked, non-blocking for this PR; #471/#236 stay open until it succeeds) |

## PM acceptance (WA-5 self-sign, crew_alpha lead, commit `f64ed6e`)

QA/PM dispatch is structurally sterile for the same reason #471 exists
(circular), so this acceptance is self-performed by the crew_alpha lead
under the standing WA-5 fallback, same as #433/PR #466, and should be
treated as provisional until an independent PM seat exists to recheck it.

| AC | Verdict | Evidence |
|---|---|---|
| AC-1 | met | `deploy/policies/omnipods_policies.py::worker_semaphore`; `test_admission_derivation.py::AC1DerivedNotCallerSupplied` (2/2 pass) |
| AC-2 | met | same file/function; `AC2ZeroAndMultiMatchDeny` (2/2 pass) |
| AC-3 | met | `bin/nightshift-ctl::ensure_schema`; `AC3IndexesShipInTheMigration` (1/1 pass) |
| AC-4 | met | `_guarded_unique_index`; `AC4GuardedMigrationNeverCrashes` (2/2 pass, exact-tuple assertions per R1-F-1) |
| AC-5 | met | `_derive_normal_phase` qa/review branch; `AC5QaReviewContinuationPreInsertCheck` (2/2 pass) |
| AC-6 | met, with the PRE_DEPLOYMENT caveat recorded above (R2-M-1) | `AC6AllThreePathsEndToEnd` (7/7 pass); real-world round-trip proof is a tracked post-merge step, not a gate on this PR |
| AC-7 | met | `AC7FailClosedInvariantUnchanged` (3/3 pass); existing `test_policy.py::WorkerSemaphorePolicy` fail-open tests unmodified, still pass |
| AC-8 | met | `AC8CapacityAccountingUnaffected` (1/1 pass) |
| AC-9 | met | ADR-0062 and this contract both committed as the first two commits of `crew_alpha/471-build`; PR #473 body discloses #471 (P0, part of #236) and links #472 |

**Verdict: ACCEPTED.** All 9 criteria met (AC-6 with a recorded, tracked,
non-blocking post-merge follow-up). Ready for Release integration.
