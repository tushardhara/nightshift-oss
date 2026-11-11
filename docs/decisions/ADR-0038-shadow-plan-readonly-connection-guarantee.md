# ADR-0038: The shadow planner's read-only connection guarantees no data mutation, not no sidecar file

Date: 2026-08-28
Status: accepted
Deciders: owner, program, crew_alpha (run `run-ac08f05e1429e62d68551e8fe306c067`)
Issue: #228 (approved delivery slice #228A)
Supersedes: ADR-0036, only for the WAL-materialization/file-write claim in its Decision and
Acceptance boundary sections. ADR-0036's text is unedited except for a one-line backlink to
this ADR; every other claim in ADR-0036 (shadow-only authority, exact-SHA classification,
canonical output, scope boundaries) stands as written and is unaffected by this ADR.

## Context

ADR-0036 shipped `nightshift-ctl review plan`'s read-only connection to `control.db` with a
Decision and Acceptance boundary that claimed the command performs "no schema creation, write
transaction, or WAL creation" and "writes no file."

Cross-crew adversarial review (`crew_beta`) proved that claim false in the shape that matters:
building a real control DB via the actual `nightshift-ctl init` path (which sets `journal_mode
=WAL`) with no prior writer session open, then running `review plan` against it, produces two
newly-created files: `<db>-wal` and a non-empty `<db>-shm`. Root cause: the shared read-only
connect helper (`connect(db_path, readonly=True)` in `bin/nightshift-ctl`) opens with `mode=ro`
in the SQLite URI. Under `mode=ro` alone, SQLite still has to probe/attach the WAL machinery on
first read of a WAL-mode database, which materializes those files even though the connection
never writes.

The first attempt at a fix added `immutable=1` to the same URI, on the reasoning that a
connection whose whole contract is "read-only snapshot, never invalidated mid-read" should
tell SQLite the file will not change for the life of the connection, avoiding WAL bring-up
entirely. `architect`'s independent recheck proved, by execution, that this is the wrong tool:
`immutable=1` makes SQLite treat the file's contents as fixed at the moment the connection
opens, so it never consults the live WAL. A row committed by a concurrent writer but not yet
checkpointed into the base `.db` file becomes **invisible** to that connection. Reproduced
directly: a persistent writer connection commits a row without checkpointing; against the
original `mode=ro`-only connection (commit `6321682`) both `review plan` and `project-snapshot`
correctly see the row; against the `immutable=1` connection, `review plan` returns `exit 3
task_missing` and `project-snapshot` silently omits the row. `control.db` is under continuous
concurrent write load in normal operation (tasks, slots, and claims are written by other live
sessions constantly), so this is silent stale reads / data loss, not a cosmetic defect — worse
than the file-creation issue it was meant to fix.

ADR-0036 is `Status: accepted` (it was merged once via the original PR #234, later reverted by
an unrelated concurrent-session collision and reintroduced unchanged in this PR). The
constitution is explicit that an accepted ADR's Decision is not edited in place; a claim that
turns out wrong is corrected by a superseding ADR, scoped narrowly, with a backlink both ways.
This ADR is that correction, scoped to exactly the one claim that was wrong.

## Options considered

### A. Keep `immutable=1`

Rejected. Proven by execution to cause silent stale reads: a committed-but-uncheckpointed row
from a concurrent writer becomes invisible to `review plan` and `project-snapshot`. A
review-risk planner or task/slot snapshot that can silently miss a task is a worse defect than
the sidecar-file creation it replaces, especially given `control.db`'s continuous concurrent
write load.

### B. Keep ADR-0036's original claim ("no WAL creation", "writes no file") and find a different mechanism to make it literally true

Rejected. No mechanism is evidenced in this repo, in SQLite's documented connection options, or
in `architect`'s report that suppresses `-wal`/`-shm` sidecar materialization on a WAL-mode
database without either (a) `immutable=1`'s staleness regression above, or (b) taking a write
lock, which a read-only command must not do. Materializing SQLite's own sidecar bookkeeping
files when opening any connection (read-only or not) to a WAL-mode database is standard SQLite
behavior; treating it as a defect makes the claim unachievable rather than merely difficult.

### C. Revise the guarantee to what actually matters, and add a stronger positive guarantee data visibility must hold

Chosen. `review plan` and `project-snapshot` do not need "no sidecar file ever touched" — every
consumer of ADR-0026's review contract, and every consumer of these two commands, cares about
two different properties instead: (1) the connection never mutates control-plane DATA, takes no
write transaction, and makes no schema change; and (2) the connection must never silently miss
data that has already been committed by a writer, even if that writer has not yet checkpointed
its commit into the base file. Property (2) is new relative to ADR-0036's original text — it
was implicit in "the command reads the task's exact ... state" but never stated as an explicit
guarantee, which is exactly the gap that let the `immutable=1` regression through review twice.

## Decision

`connect(db_path, readonly=True)`'s SQLite URI uses plain `mode=ro`, with **no** `immutable=1`
and no other option that changes WAL visibility. This is byte-identical to the connection code
originally reviewed and merged in commit `6321682`.

`review plan` and `project-snapshot` guarantee:

- **No control-plane data mutation, no write transaction, no schema change or migration.** The
  connection is opened `PRAGMA query_only=ON` and, at the OS level, the base `.db` file is
  opened `mode=ro` (effectively `O_RDONLY`), so this connection cannot alter the base file's own
  bytes under any code path.
- **No silent staleness.** A row committed by a concurrent writer, even one not yet checkpointed
  into the base `.db` file, remains visible to both commands. Neither command may return a
  not-found/missing verdict, nor silently omit a row, because of pending-but-committed WAL
  content.

They do **not** guarantee that SQLite's own `-wal`/`-shm` sidecar bookkeeping files never appear
or never change size. Opening any connection (read-only or not) to a WAL-mode `control.db` is
ordinary SQLite behavior that may create or touch those files; that is not a logical write to
Nightshift's data, not a schema change, and not a write transaction, and #228A does not attempt
to suppress it.

## Acceptance boundary

Tests must prove, by execution against a real WAL-mode `control.db` built through the actual
`nightshift-ctl init` path:

1. With no prior writer session open, `review plan` still succeeds and returns the correct task
   (crew_beta's original repro shape) — sidecar files may appear; that is not a failure.
2. A row committed by a concurrent writer holding an open, uncheckpointed transaction remains
   visible to `review plan` (exit 0, correct task) and to `project-snapshot` (row present in its
   task list) — `architect`'s repro shape.
3. The base `.db` file's own bytes are byte-for-byte identical before and after a `review plan`
   or `project-snapshot` run.

Every other Acceptance boundary condition in ADR-0036 (canonical repeatability, rename path/blob
evaluation, ambiguous-evidence promotion to `HIGH_RISK`, frozen fixtures, missing-DB/missing-task
exit 3, no admission/dispatch/ADR-0026/review/Release/workflow/policy/Git-state change) is
unaffected by this ADR and continues to apply as ADR-0036 states it.

## Consequences

The corrected guarantee is honest about SQLite's actual behavior on a WAL-mode database and adds
a data-visibility guarantee that was previously only implicit. `control.db-wal` and
`control.db-shm` may appear or grow as a side effect of running `review plan` or
`project-snapshot`; nothing currently checkpoints them proactively. That is accepted as ordinary
SQLite bookkeeping, not a defect, per Option B above. If unbounded WAL growth from this planner
running repeatedly with no nearby checkpointing writer ever becomes a measured operational
problem, the fix is a checkpoint-triggering mechanism, not a read-only-connection option that
risks staleness again (see Reversal trigger).

## One-way door and reversal trigger

Not a one-way door: the connection option is a single line, already proven revertible once in
this same PR. Revisit only if: (a) unbounded `-wal` file growth from repeated `review
plan`/`project-snapshot` invocations with no nearby checkpointing writer is measured as an actual
operational problem — the fix in that case is an explicit, tested checkpoint trigger, not
`immutable=1` or any other option that risks silently hiding committed-but-uncheckpointed data;
or (b) a future SQLite version or connection option is shown, by the same execution-based
standard `architect` applied here, to suppress sidecar-file creation without reintroducing the
staleness regression this ADR exists to prevent.
