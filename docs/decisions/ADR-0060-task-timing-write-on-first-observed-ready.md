# ADR-0060: Record task-lifecycle timing (`ready_at`/`pr_opened_at`/`integrated_at`) with write-on-first-observed-read for `ready_at`

Date: 2026-08-31 · Status: accepted · Deciders: crew_alpha lead · Proposers: none (see §Process — cross-vendor debate blocked)
Conversation: crew_alpha session (title `223-build`) · Spec: issue #223, accepted DoR contract posted at https://github.com/tushardhara/nightshift/issues/223#issuecomment-5482427155
Supersedes: none · Amends: none

## Process — why this is single-author (WA-5)

Issue #236 (`sys_session_send` rejecting `slot_id`/`task_id`/`generation` for
`COUNTED_AGENTS` targets — the schema has no such fields) remained OPEN for
this dispatch and the program explicitly authorized Working Agreement 5 for
this task: the lead builds single-author under the slot it already holds
(serialize lease 38 on `bin/nightshift-ctl`), discloses the gap here, in the
PR body, and in the frozen review contract's "Required review domains"
section, and independence is recovered through cross-crew review rather than
an in-crew design debate. This is the same fallback ADR-0043/ADR-0045/
ADR-0046/ADR-0055 each already invoked for the same reason. `qa`-purpose
dispatch was checked and found equally sterile under the same policy gap, so
the lead also self-QA'd; the exact commands and pass counts are recorded in
the PR body per the #424/#449/#454 precedent, not restated here.

## Context

Issue #223 asks for four durable, nullable task-lifecycle timestamps
(`ready_at` is new; `started_at` is reused as-is from #214/ADR-0043) plus
three derived, nullable read-only values (`queue_time`, `time_to_main`,
`active_delivery_time`), each null until both of its inputs exist — never
invented. `pm` ran Definition of Ready and the program posted the accepted
contract on the issue with 8 acceptance criteria plus binding implementation
notes tightening (not expanding) three of them:

- AC1: `_task_readiness_snapshot()` is today a pure computed read (no
  persistence), shared by `task ready` and `task show`. The DoR notes
  require a *guarded write the first time it observes `reason_code=="ready"`
  for a task* — write-on-first-observed-read, matching the issue's own "do
  not reset on every read" wording.
- AC2/AC4: copy the exact idiom already established for `started_at`/
  `budget_seconds`/`extension_seconds` — `add_column` plus a
  `WHERE x_at IS NULL` guarded write inside `cmd_task_upsert`, not a new
  subsystem, not a raw settable `task upsert` field.
- AC3: `pr_url` (via `task upsert --pr-url`) is one of the two DoR-accepted
  hook points for "the control plane first learns the task's delivery PR
  exists"; wiring it alone is sufficient because first-write-wins makes
  adding the other hook point (`pr-head`/`pr-create`) safe later.

Migration/schema sequence 9 was pre-allocated by the program under
`bin/nightshift-ctl` serialize lease 38 before this decision; this ADR does
not re-request it.

The one design question the DoR notes leave open is *where inside
`_task_readiness_snapshot` the guarded write for `ready_at` belongs*, since
that function has several distinct exit branches and multiple external
callers. That is the decision this ADR records.

## Options considered

No competing vendor proposals exist (see §Process). The options below are
the lead's own analysis of the one open design question, offered for the
record the same way a proposal would be, so a later reader can see what was
rejected and why.

### A — Guarded write inside the shared per-task loop of `_task_readiness_snapshot`, gated on `reason == "ready"` (chosen)

Add `ready_at`/`pr_opened_at`/`integrated_at` additively via
`ensure_schema`'s existing `add_column` helper. Inside
`_task_readiness_snapshot`'s single per-task loop, at the point the function
already computes `dispatchable, reason` for that task, add:

```sql
UPDATE tasks SET ready_at=? WHERE id=? AND ready_at IS NULL
```

guarded by `if reason == "ready" and task["ready_at"] is None:`, then update
the in-memory `task` dict from the same value so the row this function
returns is coherent with what it just wrote (no second read is needed to see
the timestamp it just set). Every caller of this shared function —
`task show`, `task ready`, the continuation-slot verdict lookups, and
`task extend` — passes through this one loop, so every path that can ever
observe readiness participates without a per-caller write. The
`AND ready_at IS NULL` half of the guard makes "exactly once, ever" a
property of the statement itself, not of "this branch only runs once per
process" staying true — the same discipline ADR-0043 established for
`started_at`.

### B — Write only from `task show`/`task ready`'s command handlers, after calling the (still-pure) snapshot function (considered, not chosen)

Leave `_task_readiness_snapshot` a pure read, and add the guarded `UPDATE`
in `cmd_task_show` and `cmd_task_ready` individually, after each calls the
snapshot function and inspects `reason_code`. Rejected: the continuation-slot
verdict lookup and `task extend` also call `_task_readiness_snapshot` and
also observe `reason_code` (confirmed by reading their call sites before
deciding), so Option B would silently miss `ready_at` for any task whose
*first* observed-ready moment happens through one of those paths rather than
through `task show`/`task ready` — exactly the failure mode AC1's "do not
reset on every read" wording is guarding against being read too narrowly.
Duplicating the same guarded `UPDATE` at four call sites instead of one is
also strictly worse for the same reason ADR-0043 §Option B rejected a
standalone helper at a single call site: more places for a future edit to
forget the guard.

### C — A separate write-only pass over the readiness table, run once per command invocation before the read (rejected)

Compute readiness as today, then run one more query afterward that finds
every task whose `reason_code` is `"ready"` and writes `ready_at` for all of
them in one batch `UPDATE`. Rejected: this recomputes or re-derives
`reason_code` outside the loop that already has it in memory, doubling the
graph-read cost for no benefit, and it reintroduces exactly the
multiple-source-of-truth risk (the batch pass could disagree with what the
per-task loop believes `reason_code` is if any dependency state changes
between the two reads within the same call) that keeping the write inside
the single coherent full-graph read avoids.

## Decision

Option A. `ready_at`, `pr_opened_at`, `integrated_at` (all nullable, all
additive) via `add_column`. `ready_at` is written by a guarded
`UPDATE ... WHERE id=? AND ready_at IS NULL` inside
`_task_readiness_snapshot`'s existing per-task loop, gated on
`reason == "ready"`, so every caller of that shared function participates.
`pr_opened_at` and `integrated_at` are written by the same guard shape
inside `cmd_task_upsert`'s existing transaction — `pr_opened_at` when
`--pr-url` is present in the update, `integrated_at` when the resulting
`state` is `"integrated"` — for both the UPDATE and INSERT branches of that
function, so a task created with `pr_url`/`state=integrated` already set
(e.g. a backfill) still records its first-observed timestamp. All three
columns are deliberately absent from `TASK_FIELDS`/`TASK_ROW_FIELDS`, so
`task upsert` stays structurally incapable of setting any of them directly
— the same protected-output-contract shape ADR-0043 established for
`started_at`. `queue_time`/`time_to_main`/`active_delivery_time` (AC7) are
computed by a small pure helper (`_task_timing`) and merged additively into
the same dict `_task_readiness_snapshot` already returns for `task show`/
`task ready`; each is null unless both of its inputs are present
(`_is_finite_number`, the same damaged-value guard already established for
`started_at`/`budget_seconds`/`extension_seconds`), never invented.

## Consequences

**Easier:** `queue_time`, `time_to_main`, and `active_delivery_time` become
directly readable through the two existing commands operators already use
(`task show`, `task ready`) with no new command surface, and a future
dashboard or SLA ticket (explicitly out of scope here per AC8) has a
race-free, already-tested data source to build on instead of reconstructing
timing from slot/admission history. **Harder:** nothing new — this is a pure
addition with no dashboard or enforcement consumer in this PR. **Foreclosed:**
none of the three new timestamps can be cleared or re-armed by an operator
through any existing command surface (mirrors `started_at`'s own
AC-11-derived guarantee); a genuine do-over needs a new task id, matching the
existing convention rather than introducing a new one.

## Reversal trigger

If a future ticket needs any of `ready_at`/`pr_opened_at`/`integrated_at` to
reset on a real restart rather than persist for the task's lifetime, that is
a new, explicit decision under a new ADR — never a silent edit to this one
(AC6 requires the opposite: restart/reopen preserves all timestamps). If a
second call site for the `pr_opened_at`/`integrated_at` guarded-write shape
appears outside `cmd_task_upsert` (e.g. a second hook point such as
`pr-head`/`pr-create` gets wired per AC3's "either is acceptable" note),
revisit whether to extract a shared helper rather than duplicate the SQL a
third time. If issue #236 is fixed and cross-vendor debate becomes possible
again for follow-up work on this seam, future ADRs on this file should run
the full two-proposal process; this ADR's single-author status is a property
of #236 being open on 2026-08-31, not a new norm for this crew.
