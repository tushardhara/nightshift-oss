# ADR-0043: Record `tasks.started_at` exactly once, inside `cmd_slot_acquire`'s existing NEW-grant transaction

Date: 2026-08-29 · Status: accepted, amended 2026-08-29 · Deciders: crew_alpha lead · Proposers: none (see §Context — cross-vendor debate blocked)
Conversation: crew_alpha session (title `214-implement`) · Spec: `docs/specs/214-record-task-execution-start.md`

## Context

Issue #214 asks for a durable, nullable `tasks.started_at`, set exactly once — the
moment a task's first `phase='new'` normal worker slot is successfully granted —
and left untouched by every later planning/continuation/retry/re-admission slot.
`crew_alpha`'s `pm` ran Definition-of-Ready twice (once in a prior session,
`214-dor`, whose transcript became unrecoverable to server-side truncation; once
again in this session, independently re-verified against the live code at
Checkpoint 5, `27f4d188b4882f047541811dae0ce8626b99137e`) and returned **READY**
both times, with 11 numbered acceptance criteria, frozen in
`docs/specs/214-record-task-execution-start.md`. Size S. Seam A
(`bin/nightshift-ctl` + its own tests) per `docs/owners.md`. No lease, no
cross-seam touch.

**Cross-vendor debate could not run.** Per this crew's mandatory design-debate
process, both `eng_claude` and `eng_codex` were dispatched (`purpose: "explore"`)
for independent competing proposals, after a `qa` planning dispatch for the test
plan. All three dispatches were denied identically:

```
Denied by policy: args.slot_id must be a strict positive integer
```

reproduced across two argument shapes (`slot_id`/`task_id`/`generation` nested
inside `args`, and the same three fields at the top level of the
`sys_session_send` call) — the `sys_session_send` tool's own JSON schema declares
`additionalProperties: false` on `args` and does not list `slot_id`/`task_id`/
`generation` as accepted properties at all, at either level, so no argument
shape can satisfy a policy that requires them. This is exactly the already-open,
still-`OPEN` P0 issue **#236** (*"worker_semaphore requires args.slot_id/
task_id/generation that the sys_session_send tool schema forbids — blocks ALL
crew-internal engineer/QA/reviewer dispatch"*), and matches the precedent this
crew already hit on issue #232 (registry: *"eng_claude/eng_codex/qa/reviewer_*
dispatch confirmed sterile this session, issue #236, reproduced 3 argument
shapes"*). `pm` dispatch is unaffected (`pm` is in `EXEMPT_AGENTS`, not
classified through `worker_semaphore`), so Definition-of-Ready and the
acceptance sign-off below both proceeded normally.

Per **Working Agreement 5** (wave 1, issue #13 — *"a crew lead whose worker
dispatch is sterile builds under a slot it holds, and says so in the PR
body... independence is preserved by cross-crew review, which becomes
mandatory"*) and **WA-6** (*"nobody signs their own acceptance criteria"* — not
triggered here since `pm` is reachable and signs normally, but the same
independence principle extends to code review: the lead does not review its
own diff), this ADR is **single-author** rather than a reconciliation of two
independent proposals. That is a real gap, not a formality — it is flagged here,
in `STATUS.md`, and in the PR body, and cross-crew review (not in-crew review)
is the mandatory independent check for the resulting diff.

## Options considered

No competing vendor proposals exist (see above). The options below are the
lead's own analysis of the one seam the spec names, offered for the record the
same way a proposal would be, so a later reader can see what was rejected and
why.

### A — Guard-claused `UPDATE` inline at the existing `INSERT INTO worker_slots` call site (chosen)

Add `tasks.started_at REAL` additively via `ensure_schema`'s existing
`add_column` helper, then — inside `cmd_slot_acquire`'s single
`begin(cx)` → … → `cx.execute("COMMIT")` transaction, immediately around the
existing `INSERT INTO worker_slots(...)` — execute:

```sql
UPDATE tasks SET started_at = ? WHERE id = ? AND started_at IS NULL
```

guarded by `if kind == "normal" and phase == "new":` at that exact call site.
This call site is reached only for a genuinely new grant: the denied branch
(`granted=False`) and both retry short-circuits (`_exact_new_retry`,
`_exact_claim_slot_retry`) each `COMMIT` and return before this line, so no
denied or `reused=True` request can reach it, and `phase` is `None` for
`kind="oncall"` so the guard excludes it structurally, not by convention. The
`AND started_at IS NULL` half of the guard makes exactly-once a property of
the statement itself, not of "`phase='new'` only ever happens once per task"
staying true forever.

### B — Standalone helper function called from the INSERT site (considered, not chosen)

Factor the same guarded `UPDATE` into a small `_record_task_start(cx, task_id,
t)` function and call it from the same site. Rejected for this change: there
is exactly one call site today, so a helper adds a name to look up without
adding a shared invariant to enforce — `add_column`/`rename_column` earn their
existence by tolerating a concurrency race across many call sites; this
statement has neither property yet. Revisit if a second call site for the same
write ever appears (see §Reversal trigger).

### C — Enforce via a SQL trigger on `worker_slots` instead of application code (rejected)

An `AFTER INSERT` trigger on `worker_slots` that stamps `tasks.started_at` when
a new row lands with `phase='new'` would make the invariant harder to bypass
accidentally from a future code path. Rejected: every other cross-table
invariant in this schema (fencing generations, dispatched_at, admission rows)
is enforced in application code inside the same transaction, not via triggers;
introducing the one and only trigger in the file for this single column would
be an inconsistent style for a marginal safety gain, and triggers are
invisible to `grep`-based auditing that this codebase's own comments rely on
(`add_column`'s docstring, `ensure_schema`'s migration comments) as the primary
way future readers verify what fires where.

## Decision

Option A. `tasks.started_at REAL` (nullable, additive) via `add_column`, and a
guarded `UPDATE ... WHERE id=? AND started_at IS NULL` inside
`cmd_slot_acquire`'s existing transaction, gated on `kind == "normal" and
phase == "new"`, sitting next to the single `INSERT INTO worker_slots` that
fires only for a genuinely new grant. `TASK_FIELDS` and `cmd_task_upsert`
are not touched, so `task upsert` remains structurally incapable of writing
`started_at` directly (AC-1). No other command reads, enforces, or displays
`started_at` in this PR (AC out-of-scope §4 of the spec) — consumption is
follow-up work.

## Consequences

**Easier:** any future "how long has this task actually been in flight"
question — dashboards, staleness alerts, DoD reconciliation — has a real,
race-free timestamp to read instead of reconstructing it from `worker_slots`
history. **Harder:** nothing new — this is a pure addition with no consumer in
this PR, so it forecloses no future design for what "restarted" or "resumed"
should mean; whatever consumes `started_at` next chooses that meaning.
**Foreclosed:** a task whose `started_at` needs to move (e.g. a genuine
do-over) must get a new task id — this ADR does not add a way to clear or
re-arm the column, deliberately, since the spec (AC-11) requires it to stay
fixed once set.

## Reversal trigger

If a future ticket needs "started_at" to reset on a real restart rather than
persist for the task's lifetime, that is a new, explicit decision (a new
column or a documented clearing path) under a new ADR — never a silent edit to
this one. If a second call site for the same guarded write appears, revisit
Option B (extract the helper) at that time rather than duplicating the SQL.
If issue #236 is fixed and cross-vendor debate becomes possible again for
follow-up work on this seam, future ADRs on this file should run the full
two-proposal process; this ADR's single-author status is a property of #236
being open on 2026-08-29, not a new norm for this crew.

## Amendment (2026-08-29) — relocated to `worker_semaphore`, per a staff MERGE_BLOCKER

**Staff finding (inconsistent-with ADR-0033, real, reachable, not a nitpick).**
Option A above stamps `started_at` inside `cmd_slot_acquire`'s RESERVATION
transaction. ADR-0033 draws a hard line between a `phase='new'` reservation
(mutable — can expire or be released unconsumed, creates no admission) and
the root `worker_semaphore` policy's actual ADMISSION consume
(`deploy/policies/omnipods_policies.py`, the ONLY place that does
`INSERT INTO task_admissions` — staff grepped and confirmed this,
contradicting this ADR's/the spec's own claim that admission was dead code
never populated in production; corrected in
`docs/specs/214-record-task-execution-start.md` §4).

**Reachable bug, as originally written:** if a first reservation for task T
is granted (stamping `started_at=t1`) but never consumed — expires or is
reaped per ADR-0033 §6, task stays `pending` — and T is later actually
admitted at real time `t2 ≫ t1`, `started_at` stayed permanently `t1`:
wrong, silent, with no repair path short of a new task id (AC-11 forecloses
re-arming it). It also leaked into `.omni/control-state.json` via
`cmd_dump`'s unconditional projection, contradicting the original §4 claim
that no other command reads it.

**Fix (same shape as Option A, relocated, no schema change):** the guarded
`UPDATE tasks SET started_at=? WHERE id=? AND started_at IS NULL` moved out
of `cmd_slot_acquire` and into `worker_semaphore`'s `phase == "new"` branch,
in the SAME transaction as the existing `INSERT INTO task_admissions` /
`UPDATE tasks SET state='building'` write. `deploy/policies/**` is
crew_alpha/seam-A territory too (`docs/owners.md:15`, explicit exception —
"the policies are the enforcement half of the kernel"), so no lease was
needed. A regression test drives the exact reservation-expires-then-later-
genuine-admission sequence
(`tests/test_task_started_at.py::test_expired_unconsumed_reservation_then_later_genuine_admission`).
This does not reopen the Option A/B/C analysis above — the *shape* of the
write (one guarded `UPDATE`, same-transaction, `WHERE started_at IS NULL`)
is unchanged and was not staff's objection; only its *location* moved, from
the mutable reservation to the durable admission. Per staff, this did not
require a wider decision or a fresh ADR.

**Consequence for AC-2/AC-3's line-number references above:** they describe
this ADR's original (superseded) location in `bin/nightshift-ctl`. The
write's current, correct location is
`deploy/policies/omnipods_policies.py`'s `worker_semaphore`, `phase == "new"`
branch, immediately after the `task_admissions` insert and the
`state='building'` update, inside the same transaction.

**Reversal trigger (unchanged in spirit):** if a second call site for the
same guarded write ever appears, revisit Option B (extract a helper) rather
than duplicating the SQL — now scoped to `omnipods_policies.py` as well as
`bin/nightshift-ctl`.
