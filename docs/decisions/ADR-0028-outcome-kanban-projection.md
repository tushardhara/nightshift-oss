# ADR-0028: Project outcome issues onto one delivery board

Date: 2026-08-27 · Status: accepted
Deciders: program · Proposers: issue #203 owner-directed Kanban audit
Spec: issue #203

Project and default-view naming is narrowly superseded by ADR-0029. All field,
projection, safety and saved-delivery-view decisions below remain in force.

## Context

The GitHub Project is meant to answer what is moving and who is active on a
phone. It currently has no automatic projection from the control plane. Making
each worker session a card would replace the backlog with short-lived machinery,
while manual field updates become stale whenever a heartbeat or worker finishes.

An outcome issue can have several tasks and several simultaneous workers. A
single `Agent` value is therefore also misleading. Absence must be explicit:
an empty cell must not leave the owner guessing whether the sync failed. The
same rule applies to delivery: an issue closed without completion must not look
like a successfully delivered outcome.

## Options considered

### A — One ephemeral card per worker session

Rejected. Sessions are evidence about work, not outcomes. Their creation and
cleanup would flood the board and make stopped sessions look like unfinished
product work.

### B — Keep Project fields manual

Rejected. The control plane already records task, run and slot transitions.
Repeating those transitions by hand creates a second, drifting account.

### C — Project durable facts onto outcome issues

Chosen. Each GitHub outcome issue is one canonical card. A deterministic,
idempotent reconciler projects its delivery stage, lane, activity, active roles
and current action. It plans by default. An apply requires both `--apply` and an
explicit `--db` path resolving to `<repo>/.omni/control.db`, the target
repository's only authoritative control database.

## Decision

Create these additive fields. Preserve any existing `Stage`, `Crew`, and `Wave`
fields, but do not add those legacy fields to a new Project:

| Field | Values or meaning |
|---|---|
| `Delivery stage` | `Shape`, `Ready`, `Doing`, `Checking`, `Finished` |
| `Lane` | `Program/shared`, `crew_alpha`, `crew_beta`, `Cross-lane/Unassigned` |
| `Activity` | `Active` when a verified live worker is bound to the outcome; otherwise `Idle` |
| `Active roles` | All verified running agent names, or `No agent running` |
| `Current action` | One short action derived from the current durable state |

Stage mapping is fixed:

| Task or issue fact | Delivery stage |
|---|---|
| `pending`, `backlog`, `design`; no ready task | `Shape` |
| no task and issue carries `state:ready` | `Ready` |
| `building`, `in_progress`, `fixing`, `blocked` | `Doing` |
| `ready`, `review`, `in_review`, `testing`, `qa`, `integrating`, `published` | `Checking` |
| `integrated`, `done`, `merged`, `dropped`, `superseded` | ignored while another task is active; otherwise `Checking` until closure |
| closed outcome issue with GitHub state reason `COMPLETED` | `Finished` |
| closed outcome issue with GitHub state reason `NOT_PLANNED` | `Checking` |

`Doing` wins over `Checking` when tasks are in both phases. Blocked is
orthogonal: it changes `Current action`, not the stage. A `NOT_PLANNED` closure
always renders `Current action` as `Closed without completion`; it never implies
that the outcome shipped. Unknown active task states refuse the whole
reconciliation instead of being guessed.

An agent appears active only when its `agent_runs` row is `running` and joins to
one task and one live slot whose agent, title, task and current Program run
bindings all match. Stale, released, expired or unbound slots are excluded with
a diagnostic. A valid active run without an outcome issue refuses the sync.
`Activity` is derived from that same verified set, never from a session name or
a cached GitHub value.

The initializer creates or reuses the fields and two saved views only. `Delivery
now` contains all outcome issues and is grouped by `Delivery stage`. `Agent
activity` is filtered to `Activity:Active` regardless of whether an issue is open
or closed, and grouped by `Lane`. Existing views, including `View 1`, are never
deleted or rewritten automatically. The initializer does not add outcome cards
and does not publish an empty runtime snapshot; the first projection is a
separate, explicit sync against `<repo>/.omni/control.db`.

Before planning or writing, the reconciler proves that `.omni/tracker.json`, the
current Git repository, the configured GitHub repository and the configured
Project owner/number agree. Every existing issue card must carry an unambiguous
issue URL and repository identity for that same repository. Missing or conflicting
identity refuses the whole operation before any write.

The default command is a read-only plan. Write mode requires the explicit
canonical target database, for example
`nightshift-tracker-sync --db .omni/control.db --apply`; it never infers a
database for an apply. The bare command resolves through the exact-SHA release
wrapper's `PATH`; `--db` independently binds that reviewed executable to the
target repository's authoritative control state. After path resolution, the
argument must equal `<repo>/.omni/control.db`; another existing database is a
refusal. One exclusive reconciliation lock covers the durable snapshot, GitHub
read, plan and all writes, so overlapping heartbeats cannot publish older state
after newer state. A busy lock is a refusal, not a retry loop.

The reconciler never edits issues, labels, comments, pull requests or SQLite.
GitHub failure never rolls back or rewrites control-plane truth, and is never
retried in a loop.

## Consequences

The owner sees outcomes rather than session debris, while multiple simultaneous
workers remain visible on the outcome they advance. GitHub is a replaceable
projection: a failed sync leaves durable scheduling state intact and the next
heartbeat can reconcile it.

The view bootstrap uses GitHub's versioned Project Views REST endpoint and
GraphQL read-back. This adds a small API compatibility surface but no service or
database.

## Cost

Tier 0. One bounded GitHub read/reconciliation per transition and heartbeat;
no new infrastructure and no subscription charge.

## One-way door?

No. Fields and views are additive, existing views and legacy fields are left in
place, and the projection can be disabled without changing outcome issues or
control-plane state.

## Review evidence

A read-only cross-vendor review on 2026-08-27 traced control-plane task `ready`
to the post-PR review phase, not backlog readiness. It also found five ways an
apparently green sync could mislead the owner: ambiguous repository identity,
automatic view deletion, overlapping writers, a mixed-epoch database read, and
`NOT_PLANNED` closures presented as delivery. This proposal now freezes the
required behavior for each finding: task `ready` maps to `Checking`; identity is
exact and fail-closed; views are never deleted; apply is serialized around one
database snapshot; and only `COMPLETED` reaches `Finished`.

Staff accepted the decision on 2026-08-27 with zero blocking findings after a
read-only Claude review. Executable fake-GitHub and temporary-SQLite tests cover
initializer create/reuse/read-back, partial failure, the exclusive apply lock,
exact identity, and an idempotent second apply. Acceptance freezes the decision;
it does not itself authorize deployment or a live `--apply`.

## Reversal trigger

Supersede this ADR if one outcome regularly needs independent owners or stages
that cannot be understood on one card, if two consecutive waves show the
derived `Current action` is less accurate than an explicit durable field, or if
GitHub can no longer provide exact repository identity and saved-view read-back.
Until a superseding ADR is accepted, that last condition disables apply rather
than permitting a best-effort projection.
