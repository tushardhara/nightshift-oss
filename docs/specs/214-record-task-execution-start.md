# 214 — Record task execution start on the first worker slot

Status: **frozen spec, amended 2026-08-29** (AC-2/AC-3 location corrected — see the note after each) — reconstructed and independently verified against
`bin/nightshift-ctl` at base_sha `27f4d188b4882f047541811dae0ce8626b99137e`
(Checkpoint 5) in worktree `/srv/project/.worktrees/214-implement`, after the
prior DoR-pass session's transcript (`214-dor` / `214-dor-record-task-execution-start`)
became unrecoverable to server-side truncation. This document, not that
transcript, is the review contract of record.

## 1. Backlog item

Record `started_at` on a task's first NEW-phase normal worker slot. A task
exposes a durable, nullable `started_at`, set exactly once — the moment its
first `phase='new'` normal worker slot is successfully granted — and fixed
thereafter regardless of later planning/continuation/retry/re-admission slots.

## 2. Acceptance criteria

**AC-1.** `tasks` gains an additive, nullable `started_at REAL` column, added
inside `ensure_schema` via the existing `add_column(cx, table, col, decl)`
migration helper (`bin/nightshift-ctl:470`), using the same guarded
`if "started_at" not in tcols: add_column(...)` shape already used for
`tasks.size` (`bin/nightshift-ctl:572-574`) and `review_receipts.rules_digest`
(`bin/nightshift-ctl:582-584`); `started_at` is **not** added to `TASK_FIELDS`
(`bin/nightshift-ctl:186-187`), so `cmd_task_upsert`'s
`fields = {k: getattr(args, k) for k in TASK_FIELDS if getattr(args, k) is not None}`
(`bin/nightshift-ctl:1531`) stays structurally incapable of writing it, and no
`--started-at` flag is added to the `task upsert` subcommand.

**AC-2.** `tasks.started_at` is set exactly once, by an
`UPDATE tasks SET started_at=? WHERE id=? AND started_at IS NULL` (or
equivalent) statement executed inside the SAME transaction as the task's
real ADMISSION write (`INSERT INTO task_admissions`, `UPDATE tasks SET
state='building'`) — never in a separate connection or a separate
transaction.
**Amendment (2026-08-29, staff MERGE_BLOCKER, ADR-0043):** originally read
"inside `cmd_slot_acquire`'s existing single transaction — between its
`begin(cx)` (`bin/nightshift-ctl:916`) and its `cx.execute("COMMIT")`
(`bin/nightshift-ctl:993`)". That location was wrong per ADR-0033 (a
`phase='new'` reservation is mutable and creates no admission) and is
corrected here. The actual site is `worker_semaphore`'s `phase == "new"`
branch, `deploy/policies/omnipods_policies.py`, between its
`cx.execute("BEGIN IMMEDIATE")` (`:241`) and its `cx.execute("COMMIT")`
(`:415`), immediately after the `task_admissions` insert (`:387`) and the
`state='building'` update (`:393`).

**AC-3.** The `started_at` write sits adjacent to the real admission write
(`INSERT INTO task_admissions`) — the only call site that fires for a
genuinely new admission — and appears nowhere else in either file.
**Amendment (2026-08-29, staff MERGE_BLOCKER, ADR-0043):** originally read
"...adjacent to the single `INSERT INTO worker_slots(...)` statement at
`bin/nightshift-ctl:985-992` — the only call site in `cmd_slot_acquire` that
fires for a genuinely new grant — and appears nowhere else in the file."
`INSERT INTO worker_slots` is the RESERVATION write, not admission; the
corrected adjacency is to `deploy/policies/omnipods_policies.py:387`'s
`INSERT INTO task_admissions`, inside `worker_semaphore`'s `phase == "new"`
branch (`:385-415`).

**AC-4.** The write fires if and only if `kind == "normal" and phase == "new"`
at that call site; it must not fire when `phase` is `"planning"` or
`"continuation"`, and must not fire when `kind == "oncall"` (where `phase` is
always `None`, per `bin/nightshift-ctl:920`).

**AC-5.** A denied grant — the `granted = False` branch at
`bin/nightshift-ctl:966-980`, which commits and returns before reaching the
`INSERT INTO worker_slots` — leaves `tasks.started_at` untouched.

**AC-6.** A `reused=True` idempotent-retry grant leaves `tasks.started_at`
untouched: both `_exact_new_retry` (`bin/nightshift-ctl:769-786`) and
`_exact_claim_slot_retry` (`bin/nightshift-ctl:792-810`) return an existing
row, and the `if retry is not None:` branch at `bin/nightshift-ctl:939-946`
commits and returns before the `INSERT INTO worker_slots` is ever reached, so
no retry path can reach the `started_at` write.

**AC-7.** The write is guarded with `AND started_at IS NULL` (or
equivalent), so exactly-once holds structurally even if a second
`phase='new'` grant were ever produced for the same task — the guard, not
`phase='new'` being a true singleton per task, is what the regression test
must assert.

**AC-8.** `tests/test_task_graph.py` and `tests/test_slots.py`, and any
admission/claims test file that exercises `cmd_slot_acquire`
(`tests/test_admission_gate.py`, `tests/test_claims.py`), pass unchanged,
with zero modified assertions.

**AC-9.** A new regression test demonstrates the positive path: acquiring the
first `kind='normal', phase='new'` slot for a task sets `tasks.started_at` to
a non-null value; the test is shown red against the pre-change code and green
after.

**AC-10.** A new regression test (or table-driven set) demonstrates each
negative path leaves `tasks.started_at` NULL: (a) a denied grant, (b) a
`reused=True` retry of an existing NEW slot, (c) a `phase='planning'` grant,
(d) a `phase='continuation'` grant, (e) a `kind='oncall'` grant — each shown
red before the change (i.e. would have failed had the implementation set
`started_at` unconditionally on every grant) and green after.

**AC-11.** Once set, `tasks.started_at` is stable across every later slot
acquisition for that task: a subsequent planning, continuation, retry, or
re-admission NEW-phase slot for the same task must leave the already-set
value unchanged, asserted by re-reading `tasks.started_at` immediately before
and after each such later acquisition in the regression test.

## 3. DoR verdict

**READY** — re-confirmed independently against the live code in
`/srv/project/.worktrees/214-implement/bin/nightshift-ctl` (not against the
prior session's summary). All 11 criteria above are individually testable,
each traces to a named line range in the current file, failure/empty states
are covered by AC-5/AC-6/AC-10, out-of-scope is enumerated below (§4),
dependencies are none beyond this file and its own tests, and there are no
open questions blocking the work.

- Size: **S**.
- Seam: `bin/nightshift-ctl` and its own tests are crew_alpha / seam A
  territory (`docs/owners.md:14,17`). No ADR, no lease, no cross-seam file
  touch required.

## 4. Explicitly out of scope

- No change to `TASK_FIELDS` or to `cmd_task_upsert`'s writable field set.
  `started_at` must remain unreachable through `task upsert` (see AC-1).
- No enforcement or consumption of `started_at` by any other command in this
  PR (e.g. no readiness-snapshot surfacing, no CLI display, no downstream
  metric). That is follow-up work, not this ticket.
- **Correction (staff review, 2026-08-29 — see ADR-0043's amendment note):**
  this section originally claimed "`task_admissions` ... is never populated
  in production code." That was WRONG and staff caught it before merge:
  `task_admissions` IS populated in production, by
  `deploy/policies/omnipods_policies.py`'s `worker_semaphore` root policy
  (`INSERT INTO task_admissions(...)` in its `phase == "new"` branch) — the
  real ADMISSION step, distinct from `cmd_slot_acquire`'s mere RESERVATION.
  The narrower, true observation — there is no `INSERT INTO
  task_admissions` inside `bin/nightshift-ctl` itself — is what the original
  text should have said and is unrelated tech debt, separately correct: the
  dead half is specifically `nightshift-ctl slot consume`/`consume_slot()`
  (`bin/nightshift-ctl`), which really is never wired to `task_admissions`,
  logged at `.omni/tech-debt.md:18` (2026-08-29 row) and tracked via a
  `staff` session titled `tech-debt-214-task-admissions`. Do not duplicate or
  fix that separate `slot consume` gap in this PR regardless.
- Because `worker_semaphore` performs the real admission, this ticket's own
  `started_at` write was RELOCATED (see ADR-0043's amendment) from
  `cmd_slot_acquire`'s reservation transaction into `worker_semaphore`'s
  `phase == "new"` admission transaction, in `deploy/policies/omnipods_policies.py`
  — that file is crew_alpha/seam-A territory too (`docs/owners.md:15`,
  explicit `deploy/policies/**` exception). AC-2/AC-3's line-number
  references below describe the ORIGINAL (superseded) location; see the ADR
  amendment and the frozen review contract's F-4 finding for the corrected
  location.
