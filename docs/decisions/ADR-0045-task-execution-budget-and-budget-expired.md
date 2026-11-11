# ADR-0045: Persist a per-task execution budget and derive `budget_expired` as a new ADR-0032 precedence row

Date: 2026-08-29 · Status: accepted · Deciders: crew_alpha lead · Proposers: none (see §Process — cross-vendor debate blocked)
Conversation: crew_alpha session (title `215-build`) · Spec: `.omni/backlog.md` (crew_alpha section, branch `crew_alpha/215-dor`), full DoR reasoning `.omni/reports/2026-08-29-215-dor-verdict.md`
Amends: ADR-0032 (§3 precedence table — adds one row; does not change the table's runnable-state guarantee)

## Process — cross-vendor debate could not run (WA-5)

Per the mandatory design-debate process, a `qa` planning slot was acquired
(`slot_id=16`, `generation=1`, title `test-215-budget-expiry`) and dispatched
for a test plan. `sys_session_send` rejected it twice, reproduced live this
session under two argument shapes:

1. `slot_id`/`task_id`/`generation` passed inside `args` →
   `Input validation error: Additional properties are not allowed
   ('generation', 'slot_id', 'task_id' were unexpected)`.
2. The same call with those three fields omitted →
   `Denied by policy: args.slot_id must be a strict positive integer`.

The `sys_session_send` tool's own JSON schema declares `additionalProperties:
false` on `args` and does not list `slot_id`/`task_id`/`generation` at all, so
no argument shape can satisfy a policy that requires them. This is issue #236
(P0, open), the same defect ADR-0043 and ADR-0044 hit on issues #214 and #174.
The unused slot was released (`slot release --slot 16`) rather than left to
expire. `pm` dispatch is unaffected (`pm` is in `EXEMPT_AGENTS`, not
classified through `worker_semaphore`), so the Definition-of-Ready pass that
produced this ticket's frozen 10 acceptance criteria proceeded normally
before this session; only the two-vendor **build** debate is blocked.

Per **Working Agreement 5** (*"a crew lead whose worker dispatch is sterile
builds under a slot it holds, and says so in the PR body... independence is
preserved by cross-crew review, which becomes mandatory"*), this ADR is
**single-author**, and the implementation and its tests are authored directly
by the crew_alpha lead rather than by `eng_claude`/`eng_codex`/`qa`. That is a
real gap, not a formality — flagged here, in the PR body, and in the report
to the program. Cross-crew review (crew_beta, round-robin) is the mandatory
independent check for the resulting diff, standing in for both in-crew review
and QA this round.

## Context

Issue #215 asks for a durable, nullable `tasks.budget_seconds`, and for the
existing `_task_readiness_snapshot` (ADR-0032) to expose a new
`reason_code=budget_expired` once a started, non-terminal task's budget has
lapsed. `pm`'s DoR pass (`.omni/reports/2026-08-29-215-dor-verdict.md`) found
a real conflict between the issue's own worked Example
(`dispatchable:true` for an in-flight task 90 minutes before its budget
lapses) and the already-merged ADR-0043(amended) `started_at` write path:
`started_at` is set only in the same transaction that flips a task's `state`
from `pending` to `building` (`deploy/policies/omnipods_policies.py:400-425`),
so a started task's `state` is never `pending` again, and
`_task_readiness_snapshot`'s existing `state != "pending"` rule
(`bin/nightshift-ctl:1816-1817`) already forecloses `dispatchable:true` for it
— independent of anything #215 adds. `pm` resolved this narrowly (the numbered
AC, not the Example, is the frozen contract); the crew_alpha lead confirmed
that reading during DoR follow-up (`.omni/decisions.md`, "crew_alpha lead —
#215 DoR follow-up" entry, 2026-08-29) and it is treated as binding here, not
reopened.

**Whether that confirmation is itself a new decision, or documentation of one
already made, was the question this build session had to answer before
touching ADR-0032.** It is documentation, not a new decision: the frozen
AC5/AC8 already fix `dispatchable`'s boolean value as unchanged for every
reachable case, and AC8 is verified structurally (§5 below, unchanged from
the DoR pass) rather than by policy — there is no code path today where a
started task is ever evaluated for `dispatchable=true`, so there is nothing
in this ticket that could reopen ADR-0032's dispatch-safety guarantee even if
it tried to. The amendment below records a new row in ADR-0032's precedence
table; it does not touch ADR-0032's Decision §5 ("keep enforcement out of
this issue") or the guarantee that `state=ready`/`pending` are the only
lifecycle points a `phase='new'` slot ever admits from.

## Options considered

No competing vendor proposals exist (see §Process). The options below are the
lead's own analysis of the one seam the spec names.

### A — Extend `_task_readiness_snapshot`'s existing if/elif chain with one new branch (chosen)

For a task whose `state != "pending"`: if `state` is in `TERMINAL_TASK_STATES`
→ `not_pending` (unchanged); else if `started_at` and `budget_seconds` are
both present as finite numbers and `now() >= started_at + budget_seconds` →
`budget_expired` (new); else → `not_pending` (unchanged — covers
`building`/`ready`/`fixing` tasks that are unstarted-in-this-sense-impossible,
unbudgeted, or still inside budget). `budget_seconds` joins `TASK_FIELDS`
exactly as `size` does (settable via `task upsert`, both create and update),
validated by a new `require_valid_budget_seconds` guard mirroring
`require_valid_ttl` (ADR-0008): refuse non-positive/non-finite values, exit
2, write nothing. `started_at` stays absent from `TASK_FIELDS` — ADR-0043 is
unchanged.

### B — Persist `budget_expired` as a stored task state (rejected)

Write a background/lazy transition that flips `tasks.state` to a new
`expired` value once the budget lapses. Rejected for the same reason
ADR-0032 rejected persisting readiness generally (its Option A): the value
goes stale the instant it is computed unless something re-derives it on
every read anyway, at which point the persisted column is a second source of
truth that can disagree with the read-time answer. It would also require a
schema migration to widen whatever CHECK constrains `tasks.state`, touching a
column ADR-0043 and ADR-0033 both reason about carefully; #215's own
out-of-scope list already excludes state-machine changes.

### C — Make `dispatchable` reopen to `true` for a started, unexpired task (rejected)

This is the issue's literal worked Example. Rejected: it requires
`_task_readiness_snapshot` to distinguish "started but still within budget"
from "never started" for `phase='new'` admission purposes, which reopens
ADR-0032's dispatch-safety guarantee ("`dispatchable=true` would allow
duplicate implementation") for a size:S ticket whose own out-of-scope list
explicitly excludes touching dispatch/slot-acquisition behavior. Materially
bigger and riskier than the ticket filed; belongs back through `pm` as a
re-scope if a real downstream consumer ever needs it (see Reversal trigger).

## Decision

Option A. Add `tasks.budget_seconds REAL` (nullable, additive) via the
existing `add_column` migration helper. Add `budget_seconds` to
`TASK_FIELDS`/`task upsert`'s argparse wiring (`type=float`, own flag
`--budget-seconds`), guarded by `require_valid_budget_seconds` (refuses
`<= 0`, `nan`, `inf`, `-inf`; exit 2; writes nothing, matching
`require_valid_ttl`'s ADR-0008 convention). Extend ADR-0032's precedence
table with one new row for non-`pending`, non-terminal, started,
budget-exceeded tasks:

| Order | Condition | `dispatchable` | `reason_code` |
|---|---|---:|---|
| 1a | `state` is not `pending` and is in `TERMINAL_TASK_STATES` | false | `not_pending` (unchanged) |
| 1b | `state` is not `pending`, not terminal, `started_at` and `budget_seconds` both finite numbers, `now() >= started_at + budget_seconds` | false | **`budget_expired`** (new) |
| 1c | `state` is not `pending`, none of the above | false | `not_pending` (unchanged) |
| 2–7 | unchanged (pending task's own missing_size → invalid_size → needs_split → dependency_cycle → blocked_dependency → ready chain) | — | — |

A damaged/out-of-band `budget_seconds` (write-time guard bypassed, e.g. a
direct DB write of a non-numeric or non-finite value) is treated as `NULL`
for that read — checked via an `isinstance(..., (int, float))` +
`math.isfinite` guard before any arithmetic, so a corrupted value cannot
raise and cannot manufacture `budget_expired`. This mirrors ADR-0032's
existing defensive handling of a damaged `size`.

`dispatchable`'s boolean value does not change for any currently reachable
case: the `phase="new"` dispatch gate (`bin/nightshift-ctl:899-916`) already
requires `state == "pending"` before consulting the snapshot, and a
`pending` task's `started_at` is always `NULL` under the current
ADR-0043(amended) write path, so rule 1b is structurally inert for every task
`phase="new"` ever evaluates. Continuation dispatch never consults
`dispatchable` at all. This ticket only makes an already-`false` verdict's
reason string more specific for a task that was already unreachable for
dispatch.

## Consequences

**Easier:** Program-side follow-on work that needs a machine-readable signal
for "this task has been running longer than its budget" — dashboards,
staleness alerts, escalation policy — can read `reason_code=budget_expired`
off `task show`/`task ready` instead of reconstructing elapsed time from
`worker_slots` history. **Harder:** nothing new — this is a pure read-side
addition with no consumer in this PR. **Foreclosed:** nothing new by this
increment specifically; the issue's own out-of-scope list (enforcement,
worker cancellation, extension/re-arming, split/fail/block automation) is
unaffected structurally, per the `phase="new"`/continuation-dispatch argument
above.

## Reversal trigger

If a future ticket needs a started-but-within-budget task to read
`dispatchable=true` (Option C above), that is a new, explicit decision under
a new ADR that reopens ADR-0032's dispatch-safety guarantee on its own
terms — never a silent edit to this row. If issue #236 clears, any follow-up
change to `_task_readiness_snapshot` should get a real two-vendor dispatch
before being treated as satisfying the design-debate precondition; this
ADR's single-author status is a property of #236 being open on 2026-08-29,
not a new norm for this crew (same WA-5 reversal trigger as ADR-0043/0044).

## Amendment (2026-08-29) — round-1 fix packet: two MERGE_BLOCKERs, one FOLLOW_UP

Cross-crew review (crew_beta, standing in for code review + QA per WA-5) and
`staff` (ADR consistency) each returned one real, narrow `MERGE_BLOCKER`
against PR #334's head `818854b...`, plus one non-blocking `FOLLOW_UP`. Both
blockers left `dispatchable`'s boolean value untouched in every branch —
ADR-0032's dispatch-safety guarantee was not reopened by either finding or by
either fix. This does not reopen the Decision above; it corrects two
implementation defects in Option A's read-time guards and names, explicitly,
a boundary the Decision left implicit.

**MERGE_BLOCKER-1 (staff): wrong completion set reused for the terminal
check.** The Decision's rule 1a used `TERMINAL_TASK_STATES`
(`bin/nightshift-ctl:3237`), which is the worktree-reap set (`done`,
`merged`, `dropped`, `superseded`) and does not include `integrated`.
ADR-0032's own frozen Verification item 8 requires `integrated` to always
report `not_pending`. Since `integrated` is a real, common terminal state
(an ordinary fully-shipped task) and was not in `TERMINAL_TASK_STATES`, such
a task could incorrectly read `budget_expired` once its (still-set)
`started_at`/`budget_seconds` window lapsed — a reason-code regression
against an already-frozen ADR-0032 guarantee, even though `dispatchable`
itself stayed `false`. **Fix:** a new `READINESS_TERMINAL_STATES =
{"done", "merged", "dropped", "superseded", "integrated"}`, defined and
commented separately from `TERMINAL_TASK_STATES` (which keeps its original
worktree-reap meaning and callers, unchanged), used only by rule 1a. The two
sets are not merged into one: they answer different questions for different
consumers, and staff's finding was explicit that silently repurposing
`TERMINAL_TASK_STATES` for a second meaning is itself the defect, independent
of which states happen to be in it today.

**MERGE_BLOCKER-2 (crew_beta): read-time guard missed the `> 0` half of
AC-9.** `_is_finite_number`, the read-time damage guard, checked type and
`math.isfinite` but never `value > 0`, while the write-time guard
(`require_valid_budget_seconds`) refuses `<= 0` too. A direct-DB-written
`budget_seconds` of `0` or a negative number — exactly AC-9's "write-time
guard bypassed" scenario — therefore read as valid and could produce a
`budget_expired` verdict it should never reach. **Fix:** a new
`_is_valid_budget_seconds(value)` = `_is_finite_number(value) and value > 0`,
used both by the read-time check (rule 1b) and, for the same predicate never
to drift between the two call sites, refactored into
`require_valid_budget_seconds` itself. `started_at`'s own finiteness check is
unaffected — a timestamp of `0` is an early point in time, not a damaged
duration, and required no `> 0` guard to begin with; only `budget_seconds`
(a duration) needed one.

**FOLLOW_UP (staff, non-blocking): name `ready`'s exception explicitly.**
The Decision's rule 1a/1b split leaves `building`/`ready`/`fixing` eligible
for `budget_expired` while `READINESS_TERMINAL_STATES` (above) is not. This
was deliberate, not incidental: these three are exactly the set this file
already treats as "still active, in-flight work" for continuation dispatch
(`bin/nightshift-ctl:981,1260`,
`task["state"] not in {"building", "ready", "fixing"}`). A task sitting in
`ready` (opened a PR, not yet merged) well past its budget is a real signal
worth surfacing to the dashboards/alerting consumers this field is pitched
for (§Consequences, "Easier") — it has not finished, unlike `integrated`.
`ready`'s continued eligibility for `budget_expired` is therefore an
intentional, reasoned choice, not an artifact of which states happened to
land in `TERMINAL_TASK_STATES`, and is recorded here as such per staff's
request rather than left inferable only from the test suite.

Regression coverage for both fixes and the now-explicit `ready` exception:
`tests/test_task_graph.py::TaskBudgetExpiryTest.test_terminal_states_never_report_budget_expired`
(extended to include `integrated`),
`::test_damaged_budget_seconds_is_treated_as_null_and_never_raises`
(extended to include `0.0`/`-50.0`), and the pre-existing
`::test_budget_expired_replaces_not_pending_for_non_terminal_states`
(unchanged, already covers `building`/`ready`/`fixing`). Both new cases were
confirmed to fail against the pre-fix head (`818854b...`) before passing
against this fix.

**Deciders:** crew_alpha lead, single-author under WA-5 (unchanged from the
original Decision), acting on the two reviewers' findings.
