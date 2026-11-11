# Review contract

## Identity

- Work item: issue #215 — "Persist task execution budgets and derive budget expiry" (task `215-build`)
- Committed contract path and blob OID: `docs/reviews/215-task-execution-budget-CONTRACT.md`, blob OID recorded in the freezing commit (see PR #334 commit history; this file's own git blob hash at the frozen commit is the authority, not this prose copy)
- Contract version: 1
- Base SHA: `aaf2e75d0d8f6baa08e4e0216b61bc023ac01169`
- Head SHA at freeze: `e8cf4b2e04a4460190a12de999e320eabc0fe149`
- ADR: `docs/decisions/ADR-0045-task-execution-budget-and-budget-expired.md` (amends ADR-0032's `_task_readiness_snapshot` precedence table)
- Review owner (cross-crew lead, not the author): crew_beta
- Frozen at (UTC): 2026-08-29T18:05:00Z

**Process note.** This contract is being added retroactively, after PR #334 opened and after `pm`'s acceptance sign-off, at the program's request — it documents what was already true at build time (the frozen criteria below are byte-identical to `.omni/backlog.md` on branch `crew_alpha/215-dor`, unchanged since the DoR pass), not a re-scoping. It does not block or reset the `staff`/`security`/cross-crew reviews already dispatched in parallel against this same head SHA.

## Acceptance criteria

Verbatim from `.omni/backlog.md` (branch `crew_alpha/215-dor`), full reasoning in
`.omni/reports/2026-08-29-215-dor-verdict.md`. AC9/AC10 were added during DoR for
failure-state/testability completeness; AC2/AC5/AC8 were tightened with an
operational decision the issue itself left open (see the verdict doc §2, §4).

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | Additive nullable `tasks.budget_seconds REAL`, via the existing `add_column` migration helper (same shape as `tasks.size`/`tasks.started_at`, `bin/nightshift-ctl:606-615`). No backfill; existing rows read NULL. | `TaskBudgetExpiryTest.test_budget_seconds_column_is_additive_and_nullable` |
| AC-2 | `budget_seconds` joins `TASK_FIELDS` and is settable via `task upsert`, on both create and update. When supplied, it must satisfy `value > 0 and math.isfinite(value)` — refused exactly like `require_valid_ttl` (ADR-0008): JSON body on stdout, exit 2, row written with **no** fields at all from that call on refusal. `started_at` stays absent from `TASK_FIELDS` (ADR-0043 unchanged). | `TaskBudgetExpiryTest.test_budget_seconds_settable_on_create_and_update`, `TaskBudgetExpiryTest.test_invalid_budget_seconds_create_and_update_write_nothing` |
| AC-3 | `started_at IS NULL` ⇒ budget has not begun; a READY, queued task never accrues expiry no matter how long it waits. | `TaskBudgetExpiryTest.test_pending_task_never_accrues_expiry_no_matter_how_long_it_waits` |
| AC-4 | Expiry = `now() >= started_at + budget_seconds`, using the existing `now()`/`set_clock_override` (ADR-0005 D4). No new clock source. | `TaskBudgetExpiryTest.test_expiry_boundary_uses_the_injectable_clock` |
| AC-5 | `_task_readiness_snapshot` gains one precedence rule for non-`pending` tasks, evaluated after the terminal check and before the generic fallback: if `state` is **not** in `TERMINAL_TASK_STATES`, and `started_at` and `budget_seconds` are both set, and `now() >= started_at + budget_seconds`, the task reports `dispatchable=false, reason_code="budget_expired"`. Every other non-pending case keeps today's `not_pending`. `dispatchable` never becomes `true` for any task that has started. | `TaskBudgetExpiryTest.test_budget_expired_replaces_not_pending_for_non_terminal_states`, `test_every_other_non_pending_case_keeps_not_pending`, `test_budget_expired_task_never_appears_in_task_ready` |
| AC-6 | A task whose `state` is in `TERMINAL_TASK_STATES` (`done`, `merged`, `dropped`, `superseded`) always reports `not_pending`, regardless of `started_at`/`budget_seconds` — `budget_expired` never overrides a terminal verdict. | `TaskBudgetExpiryTest.test_terminal_states_never_report_budget_expired` |
| AC-7 | A task with `budget_seconds IS NULL` shows byte-identical `dispatchable`/`reason_code` behavior to today, in every state. The full existing `tests/test_task_graph.py` suite passes unmodified. | Full suite, 38/38 green (28 pre-existing + 10 new); pre-existing `test_every_non_pending_own_state_fails_closed` unmodified and green; `TaskBudgetExpiryTest.test_every_other_non_pending_case_keeps_not_pending` (no-budget sub-case) |
| AC-8 | No write to `worker_slots`/`task_admissions`, no change to `cmd_slot_acquire` or `worker_semaphore`'s dispatch/continuation decisions, no extend/clear/reset path for a budget or `started_at`. Verified structurally: the `phase="new"` gate (`bin/nightshift-ctl:899-916`) already requires `state=="pending"` before consulting `_task_readiness_snapshot`, and a `pending` task's `started_at` is always NULL under ADR-0043(amended) — so `budget_expired` is structurally unreachable from that gate. Continuation dispatch never consults `dispatchable` at all. | Structural: the PR's diff (`git diff aaf2e75d..e8cf4b2e -- bin/nightshift-ctl deploy/`) touches zero lines in `cmd_slot_acquire` or `deploy/policies/omnipods_policies.py::worker_semaphore` — confirmed by `git diff --stat`, `deploy/policies/**` does not appear in the changed-paths list at all; ADR-0045 §Decision traces the call sites by line number |
| AC-9 | A damaged/out-of-band `budget_seconds` value found at read time (write-time guard bypassed, e.g. direct DB write) must not raise and must not produce `budget_expired` — treat it as `NULL` for that read. Mirrors ADR-0032's existing defensive handling of a damaged `size`. | `TaskBudgetExpiryTest.test_damaged_budget_seconds_is_treated_as_null_and_never_raises` (non-numeric string, `nan`, `inf`, `-inf`) |
| AC-10 | `tests/test_task_graph.py` sets `started_at`/`budget_seconds` via direct SQLite row writes (this file's own existing convention), not by driving real admission through `omnipods_policies.worker_semaphore` (that stricter convention belongs to `tests/test_task_started_at.py`). No new CLI verb is added to set `started_at` or to set `budget_seconds` outside `task upsert`. | `TaskBudgetExpiryTest.seed_started` helper (direct `sqlite3` writes); no new argparse verb added anywhere in the diff |

## Explicitly out of scope

Issue's own list, reaffirmed as structurally guaranteed by AC-8 (see verification above):

- Enforcing expiry in slot acquisition/worker policy.
- Killing/cancelling active workers.
- Task/budget extension or re-arming.
- Program decision logic that consumes `budget_expired`.
- Automatic split/fail/block behavior.
- Time-to-main metrics.
- Any change to `dispatchable`'s boolean value for any reachable case.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review (delegate: crew_beta, standing in for in-crew `reviewer_claude`/`reviewer_codex` per WA-5 — issue #236 leaves `eng_claude`/`eng_codex`/`qa`/`reviewer_*` dispatch sterile this round, same pattern as #231/#232/#233)
- [x] QA (delegate: crew_beta, same WA-5 substitution — no in-crew `qa` test-plan/execution pass happened)
- [x] Cross-crew review
- [x] Staff / ADR consistency (ADR-0045 amends ADR-0032; `docs/decisions/**` is a constitution-group protected path per `program/review-risk.json`)
- [x] Security (`bin/nightshift-ctl` diff trips the sensitive-content mechanical trigger; `nightshift-ctl review plan` names it explicitly)
- [ ] UX (no human-facing flow, operator interface, or path under `conditional_globs["ux"]` changes)

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | staff | `MERGE_BLOCKER` | ADR-0032 Verification item 8 (`integrated` always `not_pending`) | `_task_readiness_snapshot`'s new rule 1a reused `TERMINAL_TASK_STATES` (worktree-reap set, `bin/nightshift-ctl:3237`), which excludes `integrated` | `task upsert` to `state=integrated` with `started_at`+`budget_seconds` set and elapsed → returned `budget_expired`; ADR-0032 item 8 requires `not_pending`. `dispatchable` stayed `false` in both readings (not a dispatch-safety break) | **resolved** | New `READINESS_TERMINAL_STATES = {done, merged, dropped, superseded, integrated}`, distinct from `TERMINAL_TASK_STATES` (kept, unchanged meaning/callers); used only by rule 1a. Regression: `TaskBudgetExpiryTest.test_terminal_states_never_report_budget_expired` extended to `integrated`, confirmed failing pre-fix. **staff recheck at `5ad75cd`: CONSISTENT** — fresh reproduction against the fix + full suite including ADR-0032's own frozen contract class |
| F-2 | crew_beta (cross-crew) | `MERGE_BLOCKER` | AC-9 (damaged/out-of-band `budget_seconds` treated as NULL, write-time guard bypassed) | `_is_finite_number`, the read-time guard, checked type+`isfinite` but never `> 0`, unlike the write-time `require_valid_budget_seconds` | Direct-DB write `started_at=100.0, budget_seconds=0` and `=-50` both yielded `budget_expired`; AC-9 requires `not_pending`. `dispatchable` stayed `false` in both readings | **resolved** | New `_is_valid_budget_seconds(value) = _is_finite_number(value) and value > 0`, used at both the read-time check (rule 1b) and refactored into `require_valid_budget_seconds` itself so the predicate cannot drift between write- and read-time again. `started_at`'s own check is unaffected (a `0` timestamp is not a damaged duration). Regression: `test_damaged_budget_seconds_is_treated_as_null_and_never_raises` extended to `0.0`/`-50.0`, confirmed failing pre-fix. **crew_beta recheck at `5ad75cd`: CLEARED** — reverified reproduction + write-path regression check + full suite |
| F-3 | staff | `FOLLOW_UP` | ADR-0045 documentation completeness | ADR-0045's original Decision left `ready`'s continued eligibility for `budget_expired` inferable only from the test suite, not stated as a deliberate exception | n/a (documentation gap, not a code defect) | addressed | ADR-0045 Amendment (2026-08-29) names `ready` (with `building`/`fixing`) as an intentional exception, tied to the same "still active, in-flight work" set this file already uses for continuation dispatch (`bin/nightshift-ctl:981,1260`) |

Both `MERGE_BLOCKER`s are resolved: `pm` re-signed all 10 criteria at `5ad75cd` (https://github.com/tushardhara/nightshift/pull/334#issuecomment-5464007326), `staff` returned CONSISTENT, and `crew_beta` returned CLEARED — all three against this same head. No open findings remain.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| PM acceptance (round 0) | pm | ACCEPTED — all 10 criteria met | `e8cf4b2e04a4460190a12de999e320eabc0fe149` | `bin/nightshift-ctl`, `tests/test_task_graph.py`, `docs/decisions/ADR-0045-...md` | https://github.com/tushardhara/nightshift/pull/334#issuecomment-5463900283 |
| PM acceptance (round 1 recheck, supersedes round 0 — blob changed) | pm | ACCEPTED — all 10 criteria remain met, AC-6/AC-9 independently re-verified | `5ad75cd354271c0dc409358ab817e09713793f2c` | `bin/nightshift-ctl`, `tests/test_task_graph.py`, `docs/decisions/ADR-0045-...md`, `docs/reviews/215-...CONTRACT.md` | https://github.com/tushardhara/nightshift/pull/334#issuecomment-5464007326 |
| Different-vendor code review | crew_beta (delegate, per WA-5) | CLEARED — MERGE_BLOCKER-2 resolved | `5ad75cd354271c0dc409358ab817e09713793f2c` | `bin/nightshift-ctl` | reported to crew_alpha lead by program this session |
| QA | crew_beta (delegate, per WA-5) | CLEARED — reverified reproduction + write-path regression + full suite | `5ad75cd354271c0dc409358ab817e09713793f2c` | `bin/nightshift-ctl`, `tests/test_task_graph.py` | reported to crew_alpha lead by program this session |
| Cross-crew review | crew_beta | CLEARED | `5ad75cd354271c0dc409358ab817e09713793f2c` | `bin/nightshift-ctl`, `tests/test_task_graph.py`, `docs/decisions/ADR-0045-...md` | reported to crew_alpha lead by program this session |
| Staff / ADR consistency | staff | CONSISTENT — MERGE_BLOCKER-1 resolved, ADR-0032's frozen contract class reverified | `5ad75cd354271c0dc409358ab817e09713793f2c` | `bin/nightshift-ctl`, `docs/decisions/ADR-0045-...md` | reported to crew_alpha lead by program this session |
| Security | security | PASS — rechecked against `818854b..967b6ba` directly: both hunks are pure validation-tightening, no new surface, `dispatchable` boolean untouched, false-positive HIGH_RISK trigger reconfirmed | `967b6ba40359d0665830f5e553dd5c0ffff9b613` | `bin/nightshift-ctl` | reported to crew_alpha lead by program this session |

Content evidence may transfer only across identical reviewed blobs with unchanged
dependency seams. Re-run CI, provenance, PR-body, authorization, and live/runtime
checks.

## Contract amendments

**2026-08-29, round 1 (no version bump — no criterion, domain, ADR, or scope
changed).** Two `MERGE_BLOCKER`s (F-1, F-2) and one `FOLLOW_UP` (F-3) came
back from the parallel `staff`/security/crew_beta dispatch against head
`818854b...`; both blockers are fixed and one commit adds regression
coverage for each (see Finding receipt above and `ADR-0045`'s own
Amendment section). The frozen AC-1..AC-10 text, the required-domains
checklist, and the ADR id are unchanged — this was an implementation defect
in Option A's read-time guards, not a re-scoping, so no new contract version
is opened. If a future round changes what is being reviewed (a criterion,
a domain, or the ADR itself), that gets version 2 here, per the template's
rule — this round does not.
