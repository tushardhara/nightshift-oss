# Review contract

## Identity

- Work item: issue #216 — "Refuse new worker capacity after a task budget expires" (task `216-build`)
- Committed contract path and blob OID: `docs/reviews/216-refuse-capacity-CONTRACT.md`, blob OID recorded in the freezing commit (first commit on this build branch; the freezing commit's own git blob hash is the authority, not this prose copy)
- Contract version: 1
- Base SHA: `d9e8c08973a3f4c2fc52fd6513d8893272c330f8` (`origin/main`, checkpoint id 13, wave `216`). Corrected from the charter's `5e568a9b3a88ff3c7529b60f92888930b4dc6c36`: main advanced past that SHA (heartbeat close-out + PR #337) before this build actually started; `git log 5e568a9..d9e8c08 -- bin/nightshift-ctl deploy/policies/omnipods_policies.py` is empty, so the frozen ACs and their line-number citations are unaffected. Branch/worktree also differ from the crew's normal `crew_alpha/216-build` @ `.worktrees/216-build` convention after repeated live collisions on that exact conventional path/branch name (something else kept recreating it independently, mid-session, with different WIP content each time) — see `.omni/decisions.md`, 2026-08-29 ~19:30Z and ~19:40Z entries, for the full record. Registered in the control plane (`nightshift-ctl task upsert --id 216-build --worktree ... --branch ... --state building`) so this is the task's authoritative build location.
- ADR: no new ADR (per DoR verdict §4). Cites `docs/decisions/ADR-0032-...md` (`_task_readiness_snapshot` precedence table) and `docs/decisions/ADR-0045-task-execution-budget-and-budget-expired.md` (the `budget_expired` row and its amendment, issue #215/PR #334)
- Review owner (cross-crew lead, not the author): crew_beta
- Frozen at (UTC): 2026-08-29T19:23:37Z (original freeze); base_sha and worktree/branch corrected 2026-08-29T19:33:00Z-19:45Z; no criterion/domain/ADR field changed, version not bumped

**Process note.** This contract is frozen as the FIRST commit on this build branch,
before any implementation — applying last round's retro finding live rather than
adding the contract retroactively after the PR opened, as `docs/reviews/215-...
CONTRACT.md` had to. No implementation commit exists yet at freeze time.

**WA-5 disclosure.** Issue #236 (P0, open) leaves `sys_session_send`'s `slot_id`/
`task_id`/`generation` schema sterile for in-crew `eng_claude`/`eng_codex`/`qa`/
`reviewer_*` dispatch. Per the standing WA-5 fallback (same pattern as #215,
#214, #174 task A/B), this task is built directly by the crew_alpha lead,
single-author, with `code_review` and `qa` delegated to crew_beta rather than
in-crew reviewers — same substitution `.omni/backlog.md`/the DoR verdict
already anticipated (`§ Rigor note`). Not a standing authorization for future
tasks; disclosed here per this task only, consistent with crew_alpha's own
rule against inferring WA-5 from precedent alone. The build-time design note
required by AC9 is recorded in `.omni/decisions.md`, 2026-08-29 ~19:30Z entry.

## Acceptance criteria

Verbatim from `.omni/backlog.md` (branch `crew_alpha/216-dor-v2`, commit `8d354a5`),
full reasoning in `.omni/reports/2026-08-29-216-dor-verdict.md`. 9 numbered ACs,
tracing 1:1 to the issue's own 7 (AC1/AC2/AC7 tightened), AC8/AC9 added during DoR
for failure-state/testability completeness and to freeze the single highest-risk
implementation detail.

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | `cmd_slot_acquire`'s `continuation` branch of `_derive_normal_phase` (`bin/nightshift-ctl:940`, the `state in {"building","ready","fixing"}` branch starting at line 997) must deny — via the existing `_slot_denied` helper (JSON body, exit 3, transaction COMMITs with no new `worker_slots` row for the attempted request) — a continuation slot request for a task whose `_task_readiness_snapshot(cx).get(args.task)["reason_code"]` is **exactly** `"budget_expired"` (string equality — see AC9), with `reason_code="budget_expired"` included in the denial's facts. This check runs **after** that branch's existing kind/role/crew/`pr_url`/claim validity checks (AC7) — a request already invalid for another reason keeps its existing, more specific denial. `phase="planning"`/`"new"` requests are structurally unaffected and need no new check. | `tests/test_slots.py::ContinuationBudgetGate.test_continuation_denied_when_budget_expired_qa`, `.test_continuation_denied_when_budget_expired_review`, `.test_continuation_denied_when_budget_expired_implementer`, `.test_already_invalid_continuation_keeps_its_specific_denial_even_if_budget_expired` |
| AC-2 | `worker_semaphore`'s continuation branch (`deploy/policies/omnipods_policies.py:357-390`) must perform the same recheck at actual-dispatch time, calling `task_readiness_snapshot` (already aliased at `omnipods_policies.py:96` to the same function object `_derive_normal_phase` uses), and deny (via the existing `except ValueError → ROLLBACK → _deny(str(exc))` pattern) when the recheck reports `reason_code="budget_expired"`. Runs after this branch's existing `admission is None or not valid` check, before the `dispatched_at` UPDATE — this is the TOCTOU-closing recheck for a slot that was acquired while the budget was still live but whose budget expires before the dispatch actually consumes it. | `tests/test_policy.py::ContinuationBudgetRecheckAtDispatch.test_qa_continuation_dispatch_denied_when_budget_expires_after_slot_acquired`, `.test_review_continuation_dispatch_denied_when_budget_expires_after_slot_acquired`, `.test_implementer_continuation_dispatch_denied_when_budget_expires_after_slot_acquired` |
| AC-3 | A budget denial at either checkpoint leaves zero durable trace of newly-granted capacity: at `slot acquire`, the transaction `COMMIT`s (per `_slot_denied`) with no new `worker_slots` row for the attempted request. At dispatch (`worker_semaphore`), the transaction `ROLLBACK`s, so `dispatched_at`/`bound_agent`/`bound_title` are never written on the already-active slot row — it stays `dispatched_at IS NULL`, eligible to expire via its existing TTL like any other unconsumed reservation. No new cleanup/expiry path is added. | `ContinuationBudgetGate.test_continuation_denied_when_budget_expired_qa` (asserts `slot list` gains no new row for the attempt), `ContinuationBudgetRecheckAtDispatch.test_qa_continuation_dispatch_denied_when_budget_expires_after_slot_acquired` (asserts the slot row's `dispatched_at` stays NULL after the DENY) |
| AC-4 | This ticket's file scope (`bin/nightshift-ctl`, `deploy/policies/omnipods_policies.py`) adds no code path that reads or writes `agent_runs`, and issues no cancellation of any kind — verified structurally: both new checks (AC1, AC2) sit strictly before `dispatched_at` is ever set for that call, and neither call site this ticket touches has any reachable path to an already-running worker's `agent_runs` row. | Structural: `git diff` of the two new hunks touches zero lines referencing `agent_runs`; confirmed by direct read at review time |
| AC-5 | `kind="oncall"` slot acquisition and `worker_semaphore`'s oncall handling (`is_oncall`/`agent in exempt`) are structurally untouched: neither calls `_derive_normal_phase`/`task_readiness_snapshot` today, and this ticket adds no new call from either path. Verified by AC1/AC2 being scoped strictly inside the `kind="normal"` continuation branches. | Structural: `git diff --stat` shows no changed lines before the `if is_oncall:` early return in `worker_semaphore`, nor in `cmd_slot_acquire`'s `kind == "oncall"` branch; existing `tests/test_slots.py::GlobalSemaphore.test_oncall_reserve_usable_only_by_oncall` (AC7) continues to pass unmodified |
| AC-6 | A task with `budget_seconds IS NULL`, or any task whose `_task_readiness_snapshot` reason_code is anything other than exactly `"budget_expired"`, sees byte-identical `cmd_slot_acquire`/`worker_semaphore` behavior to pre-#216 code in every state — transitively guaranteed by #215's own frozen AC6/AC9. | `ContinuationBudgetGate.test_continuation_unaffected_when_budget_seconds_is_null` |
| AC-7 | The full existing `tests/test_slots.py` (`GlobalSemaphore`, 10 tests) and `tests/test_policy.py` (`WorkerSemaphorePolicy`/`PrCloseKeywordPolicy`/`ReleaseProvenanceCanary`, 14 tests) suites pass unmodified, and (per AC1/AC2's explicit ordering) an already-invalid continuation request keeps its pre-existing, more specific denial reason rather than being masked by `budget_expired`. | Full suite green, pre-existing tests byte-for-byte unmodified; `ContinuationBudgetGate.test_already_invalid_continuation_keeps_its_specific_denial_even_if_budget_expired` |
| AC-8 | A missing task or missing `_task_readiness_snapshot` entry at either checkpoint fails closed via each checkpoint's pre-existing "unknown task"/"task no longer exists" handling — this ticket adds no new gap here and needs no new code for it, only confirmation the existing handling still fires. | `ContinuationBudgetGate.test_missing_task_at_slot_acquire_fails_closed_via_existing_handling` |
| AC-9 | Both new checks (AC1, AC2) must key off `reason_code == "budget_expired"` **specifically** — never off `dispatchable` generically. Every `continuation`-eligible task already has `state != "pending"`, so `_task_readiness_snapshot` already reports `dispatchable=False` for **every** continuation candidate whether or not its budget has expired (`reason_code="not_pending"` when it hasn't). A check written against `dispatchable` instead would deny 100% of continuation dispatch unconditionally — a total-outage regression. Both new checks' tests must include at least one case proving ordinary (non-expired) continuation dispatch for each of the `qa`/`review`/`implementer` continuation kinds is unaffected, not just that the expired case denies correctly. This detail is named as its own explicit checked item in the build-time design note (`.omni/decisions.md`, 2026-08-29 ~19:30Z entry), not left to be inferred from a diff read. | `ContinuationBudgetGate.test_ordinary_non_expired_continuation_unaffected_qa`, `.test_ordinary_non_expired_continuation_unaffected_review`, `.test_ordinary_non_expired_continuation_unaffected_implementer`; `ContinuationBudgetRecheckAtDispatch.test_qa_continuation_dispatch_allowed_when_budget_not_expired`, `.test_review_continuation_dispatch_allowed_when_budget_not_expired`, `.test_implementer_continuation_dispatch_allowed_when_budget_not_expired` |

## Explicitly out of scope

Issue's own list, carried forward verbatim (per `.omni/backlog.md`):

- Task extension.
- Automatic split/block/fail decisions.
- Program prompt changes.
- Worker cancellation.
- Time-to-main metrics.
- Any change to `_task_readiness_snapshot` itself (frozen by #215, merged).
- Any change to `dispatchable`'s boolean value or the ADR-0032 precedence table (not reopened — this ticket only adds denial branches that consume its existing output).
- Any new CLI verb.
- Retrofitting the `planning`/`new` phases (structurally unreachable — see the DoR verdict doc §1(3), the Example-vs-AC gap).
- Any lease into another crew's paths.
- A new ADR.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review (delegate: crew_beta, standing in for in-crew `reviewer_claude`/`reviewer_codex` per WA-5 — issue #236 leaves `eng_claude`/`eng_codex`/`qa`/`reviewer_*` dispatch sterile this round, same pattern as #215/#231/#232/#233)
- [x] QA (delegate: crew_beta, same WA-5 substitution — no in-crew `qa` test-plan/execution pass happened)
- [x] Cross-crew review
- [x] Staff / ADR consistency (cites ADR-0032's precedence table and ADR-0045's amendment; `docs/decisions/**`-adjacent reasoning even though this ticket adds no new ADR text)
- [x] Security (`bin/nightshift-ctl` and `deploy/policies/omnipods_policies.py` are both sensitive-set paths per `program/review-risk.json`; same trigger #215's contract recorded)
- [ ] UX (no human-facing flow, operator interface, or path under `conditional_globs["ux"]` changes)

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | Cross-crew review | FOLLOW_UP | Frozen contract attestation bookkeeping | Review-only documentation; no production-code causal path | The scoped-attestations table remained empty at reviewed head `109d0b8ab60a60255556d8aea3cc4958b8ad662a`; crew_beta requested release-time population from the receipts recorded on PR #339 | resolved | Release populated this finding and all six scoped attestations in the merge candidate from the exact-head receipts; no reviewed production blob changed. |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| PM acceptance | crew_alpha pm | PASS | `109d0b8ab60a60255556d8aea3cc4958b8ad662a` / `4f5966756cb61f45a6b677f2b490b4279a5ac9c9` | All five contract-scoped files at the reviewed tree; 9/9 frozen ACs accepted | PR #339 comment `5464628757`; marker `53fff06d6c821de084b1668c4c12c4c7` |
| Different-vendor code review | crew_beta (WA-5 delegate) | PASS | `109d0b8ab60a60255556d8aea3cc4958b8ad662a` / `4f5966756cb61f45a6b677f2b490b4279a5ac9c9` | Production and test hunks plus readiness-snapshot and semaphore dependency seams | PR #339 comments `5464611827`, `5464631643`; marker `2afb5f18e7e4756c2d1e87c03464c0ae` |
| QA | crew_beta (WA-5 delegate) | PASS | `109d0b8ab60a60255556d8aea3cc4958b8ad662a` / `4f5966756cb61f45a6b677f2b490b4279a5ac9c9` | New slot/policy tests, base regression proof, and collateral suite | PR #339 comments `5464611827`, `5464631986`; marker `127f5d74ca8b9ed689dba164b0e671db` |
| Cross-crew review | crew_beta | PASS | `109d0b8ab60a60255556d8aea3cc4958b8ad662a` / `4f5966756cb61f45a6b677f2b490b4279a5ac9c9` | Full five-file diff and named dependency seams; F-1 was documentation-only | PR #339 comments `5464611827`, `5464631407`; marker `55274b059b19c21999dec58364c72260` |
| Staff / ADR consistency | staff | PASS | `109d0b8ab60a60255556d8aea3cc4958b8ad662a` / `4f5966756cb61f45a6b677f2b490b4279a5ac9c9` | ADR-0032 precedence and ADR-0045 budget-expired seam; no new ADR | PR #339 comment `5464630180`; marker `384c4bb8f880d3341c3eb283bf313954` |
| Security | security | PASS | `109d0b8ab60a60255556d8aea3cc4958b8ad662a` / `4f5966756cb61f45a6b677f2b490b4279a5ac9c9` | Sensitive-path diff in `bin/nightshift-ctl` and `deploy/policies/omnipods_policies.py`; no auth, PII, secret, subprocess, or external-input seam added | PR #339 comment `5464629890`; marker `a87ac92c743dfeaed7d4a0007eaec343` |

Populated as each review domain reports. Content evidence may transfer only across
identical reviewed blobs with unchanged dependency seams. Re-run CI, provenance,
PR-body, authorization, and live/runtime checks.

## Contract amendments

None yet — version 1, frozen pre-implementation, no criterion/domain/ADR/scope
change so far (the base_sha and worktree-path corrections above are factual
corrections to the Identity block, made before any implementation commit
existed, not a scope change).
