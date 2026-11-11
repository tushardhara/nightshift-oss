# ADR-0031: Persist task size and dependency edges without scheduling them

Date: 2026-08-27 · Status: accepted
Deciders: crew_alpha and staff · Proposers: Claude and Codex
Spec: GitHub issue #210

## ELI5

Nightshift can remember a task, but it cannot yet remember whether that task is
small, medium, or large, or that one task must wait for another. We will add two
small facts to the existing SQLite control plane:

- a task may have size `S`, `M`, or `L`; and
- a task may name zero or more prerequisite task IDs.

This change only remembers facts. It does not decide whether work is ready,
blocked, or allowed to run. Those decisions belong to later issues.

## Context

Issue #210 is the persistence prerequisite for the ordered roadmap in issues
#211–#223. Today `tasks` has no size column and dependency edges exist only in
prose. Later scheduling code must not depend on an agent remembering prose, but
adding that scheduling behavior in this PR would make the first change too wide
to review safely.

The control plane is a low-write, single-host SQLite database. Existing databases
must migrate during normal `nightshift-ctl` startup, concurrent first invocations
must remain safe, and historical tasks must continue to work without backfilling.

Claude and Codex independently agreed on a nullable size column, a normalized
edge table, explicit endpoint checks inside one write transaction, idempotent
duplicate adds, and a sorted dependency list. They differed on whether the
database should also constrain size and whether the normal control-plane dump
should contain edges. The reconciled decision keeps both protections because a
closed value set and durable backup are properties of the state store, not only
of today's CLI.

## Options considered

### A — Nullable size plus normalized dependency table

Add `tasks.size` and a `task_dependencies` table keyed by
`(task_id, depends_on_task_id)`. Validate both endpoints inside the same
`BEGIN IMMEDIATE` transaction that inserts the edge. This is the chosen option:
it is additive, queryable, race-safe, and keeps one fact per row.

### B — Store dependencies as JSON on each task

Rejected. Concurrent additions become read-modify-write operations, reverse
lookups require scanning JSON, and uniqueness is no longer a database property.

### C — Enable SQLite foreign keys globally

Rejected for this increment. Existing connections do not enable
`PRAGMA foreign_keys`; changing that setting could expose unrelated historical
rows and is much broader than issue #210. The new command instead validates both
task IDs transactionally. A future foreign-key migration needs its own audit.

### D — Reject all dependency cycles now

Rejected as premature scheduling behavior. This issue explicitly rejects a task
depending on itself. Longer-cycle detection belongs before issue #211 or any
consumer treats these stored edges as a runnable DAG.

## Decision

1. Add nullable `tasks.size` constrained to `S`, `M`, or `L`. The CLI uses
   `argparse` choices for a clear exit-2 refusal, and the fresh/migrated SQLite
   schema also carries the `CHECK` so the closed set remains true at rest.
   Historical rows remain `NULL`.
2. Add `task_dependencies(task_id, depends_on_task_id)` with a composite primary
   key and a self-edge `CHECK`. Do not enable global foreign-key enforcement.
3. Keep the issue's narrow CLI shape:
   `task dependency-add --id B --depends-on A`.
4. Before inserting, reject a self-edge and validate both tasks inside one
   `BEGIN IMMEDIATE` transaction. A self-edge exits 2 because the input
   contradicts itself without needing a lookup. An unknown endpoint exits 3,
   matching existing valid-shaped references to missing tasks, slots, and
   observations. Neither refusal writes a row.
5. Duplicate adds are idempotent: exit 0, return `created: false`, and keep one
   row. This matches retryable fact declaration better than treating a repeated
   declaration as contention.
6. `task show` always includes `depends_on`, sorted by prerequisite task ID;
   tasks with no edges return `[]`.
7. The ordinary `dump` projection includes a sorted `task_dependencies` array.
   Otherwise Nightshift's existing backup path would silently omit newly durable
   state. No scheduler, tracker, slot, dispatch, or Program behavior changes.

## Verification contract

Focused tests cover all three valid sizes; invalid and lowercase size on create
and update; one, multiple, and deterministically sorted dependencies; empty
lists; exit-2 self refusal; exit-3 unknown endpoint refusals; idempotent duplicate and concurrent
duplicate adds; pre-change database migration; restart persistence; schema
constraints; and dump projection. Existing task/control-plane suites and the
repository's normal CI remain required before merge.

Claude Staff initially blocked one exit-code mismatch. The ADR now separates
self-contradictory input (exit 2) from valid-shaped references to missing rows
(exit 3), matching current control-plane behavior. Claude Staff then approved;
the independent Codex review also passed with zero blockers.

## Consequences

- Nightshift can durably answer a task's size and exact prerequisites.
- Old tasks remain readable with `size: null` and `depends_on: []`.
- Old binaries ignore the new edge table and preserve the new size column during
  unrelated updates, but their `task show` output does not expose edges. Rollout
  therefore updates the CLI before any later consumer relies on the facts.
- Indirect cycles can be stored. That is intentional in this persistence-only
  increment, not evidence that a cycle is runnable.
- There is still no task-delete command. A future delete operation must define
  what happens to inbound and outbound edges before it ships.

## Cost, scale, and reversibility

This stays at tier 0. It adds no service, dependency, credential, or monthly cost
at today's volume or 100×. Each dependency add performs two indexed existence
checks and one indexed insert; `task show` performs one small ordered lookup. The
schema is additive and the behavior can be stopped by reverting the CLI, although
dropping persisted columns or rows would require a separately reviewed migration.

## Reversal trigger

Before any consumer computes READY/BLOCKED or gates dispatch from these edges,
ship and review indirect-cycle handling. Revisit the storage shape only if the
measured task graph no longer fits indexed SQLite lookups or requires edge
metadata beyond prerequisite identity.
