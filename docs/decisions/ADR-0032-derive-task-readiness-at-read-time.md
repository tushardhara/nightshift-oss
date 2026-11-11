# ADR-0032: Derive task readiness from one read snapshot

Date: 2026-08-27 · Status: accepted
Deciders: crew_beta and staff · Proposers: Claude and Codex
Spec: GitHub issue #211
Supersedes: none

## ELI5

Nightshift now remembers task sizes and dependency arrows. This decision teaches
it how to answer, without changing any stored task:

- “Can this task start now?”
- “If not, what exactly stops it?”

Only a task that has not started (`state=pending`), is small or medium, is not
inside a dependency cycle, and has every direct prerequisite integrated is
READY. The answer is calculated from one consistent database snapshot. It is
not saved as another state, and this change does not allocate a worker or permit
a dispatch.

## Context

Issue #210 and ADR-0031 added nullable `tasks.size` and durable direct edges in
`task_dependencies`. They deliberately did not compute readiness. ADR-0031 also
requires indirect-cycle handling before any consumer treats the edges as a
runnable graph.

Issue #211 asks for `dispatchable`, `reason_code`, and `blocked_by` on `task
show`, plus a `task ready` read command. The result must change when a
prerequisite becomes `integrated` without rewriting the dependent task.

The existing lifecycle names are easy to misread. A new task starts as
`pending`; fanout changes it to `building` before worker dispatch; after a PR is
opened it becomes `ready`; Release eventually records `integrated`. Therefore
the persisted state named `ready` means “ready for review/integration,” not
“safe to dispatch a new implementation worker.” Treating it as dispatchable
would allow duplicate implementation.

Claude and Codex produced independent proposals. They agreed that readiness is
derived, historical null sizes must fail closed, `L` never runs, output must be
stable, cycles must not recurse forever, and issue #212 enforcement is out of
scope. They disagreed on two material points:

- Claude proposed `state=ready` as the runnable lifecycle state. Codex traced
  current fanout and release behavior and proposed `state=pending`. The issue's
  own before/after example also leaves the dependent task in its default
  `pending` state. We choose `pending`; `ready` would redispatch work whose PR
  is already open.
- Claude proposed a full-graph cycle detector. Codex proposed direct-state
  evaluation, which makes an all-live cycle block naturally. We choose explicit
  cycle detection because a mixed historical cycle can contain an integrated
  prerequisite and otherwise make a pending cycle member appear runnable. That
  would violate ADR-0031's reversal trigger.

## Options considered

### A — Persist READY/BLOCKED on each task

Rejected. The value becomes stale whenever any prerequisite changes and creates
a second source of truth beside task states and dependency edges.

### B — Derive only from direct prerequisite states

Rejected as incomplete. It safely blocks an all-pending cycle, but a cycle that
contains historical `integrated` rows can let a pending member appear ready.

### C — Reject new cycles in `dependency-add`

Rejected for this increment. Existing databases may already contain indirect
cycles, and changing the write contract does not make those rows safe to read.
A separately reviewed mutation rule may reject new cycles later.

### D — Derive from one full-graph read snapshot and detect cycles

Chosen. One ordered joined read supplies tasks, direct edges, and prerequisite
states. Python derives blockers and cycle membership without writing. At the
expected hundreds-to-low-thousands tier-0 graph size, an O(V+E) pass is smaller
and easier to audit than recursive SQL or a cache.

## Decision

### 1. Read one coherent graph snapshot

`task show` and `task ready` use the same helper. It executes one ordered
`LEFT JOIN` over all tasks, their outgoing dependency edges, and prerequisite
rows. One SQLite statement supplies a coherent read snapshot and avoids the
current two-query `task show` race. It also supplies the full graph required for
cycle detection without a write lock or persisted cache.

An edge whose prerequisite row is missing is treated as unmet and fails closed.
That shape cannot be created by the supported CLI today, but it is possible
after raw database damage because ADR-0031 did not enable global foreign keys.

### 2. Detect indirect cycles without mutating them

Run a deterministic O(V+E) strongly-connected-component or equivalent
color-marking pass over the snapshot. Every task in a component of more than
one task is cycle-bound. The database already rejects self-edges, but the
algorithm also handles one defensively. Edges pointing to a missing row do not
become graph vertices; they remain ordinary unmet direct blockers.

Cycle detection has no recursion loop, performs no repair, and never adds or
deletes an edge. A pending S/M task in a cycle is not dispatchable even if its
direct prerequisite rows currently say `integrated`.

### 3. Freeze eligibility and reason precedence

The helper always computes `blocked_by` independently as the sorted direct
prerequisite IDs whose row is missing or whose state is not exactly
`integrated`. Only exact `integrated` satisfies an edge; `dropped`,
`superseded`, `ready`, `building`, `done`, `merged`, null, and unknown states do
not.

Then the first matching rule supplies the single verdict:

| Order | Condition | `dispatchable` | `reason_code` |
|---|---|---:|---|
| 1 | own state is not exactly `pending` | false | `not_pending` |
| 2 | size is null | false | `missing_size` |
| 3 | size is not `S`, `M`, or `L` | false | `invalid_size` |
| 4 | size is `L` | false | `needs_split` |
| 5 | task is in an indirect cycle | false | `dependency_cycle` |
| 6 | `blocked_by` is not empty | false | `blocked_dependency` |
| 7 | pending S/M, no cycle, no blockers | true | `ready` |

This ordering preserves the clarified issue contract: a pending L task is
`needs_split`, while already-started or terminal work is `not_pending` rather
than being presented as work to split or repair for another dispatch. `blocked_by` remains
truthful even when another reason wins, so operators do not lose direct-edge
evidence.

A mixed historical cycle can therefore report `reason_code=dependency_cycle`
with `blocked_by: []` when its direct prerequisite row is already integrated.
That is intentional: the direct edge is satisfied as stored, while cycle
membership is the separate fail-closed reason the task is not runnable.

Unknown or null own states fail closed through `not_pending`. Invalid non-null
sizes are prevented by the schema but retain an explicit defensive verdict
rather than becoming runnable if a damaged database bypasses the constraint.

### 4. Keep one stable read contract

`task show --id X` preserves its existing envelope and task fields, and always
adds:

```json
{
  "depends_on": ["A"],
  "dispatchable": false,
  "reason_code": "blocked_dependency",
  "blocked_by": ["A"]
}
```

All ID lists are sorted lexicographically and are `[]`, never null, when empty.
An unknown shown task keeps the existing exit-3 refusal.

`task ready` exits 0 and returns:

```json
{"tasks": [<the same enriched task objects as task show>]}
```

It contains only objects whose verdict is `dispatchable: true`, sorted by task
ID. No eligible task returns `{"tasks":[]}`. This is a deterministic set, not a
priority queue, reservation, or authorization to dispatch.

`task list`, `task upsert`, `task dependency-add`, and `dump` do not gain derived
fields or change behavior.

### 5. Keep enforcement out of this issue

The snapshot can become stale immediately after it is returned. Issue #211
does not claim a task, acquire a slot, consume capacity, or authorize a worker.
Issues #212 and #213 must re-check the same semantics transactionally at their
actual enforcement boundaries. No retry loop or lock is added here.

## Verification contract

Focused regression tests must prove:

1. pending S and M tasks with no prerequisites are ready;
2. a dependent changes from `blocked_dependency` to `ready` when only its
   prerequisite changes to `integrated`, while its stored row and `updated_at`
   remain unchanged;
3. only exact `integrated` satisfies an edge;
4. multiple unmet direct blockers are filtered and sorted deterministically;
5. no dependencies yields both `depends_on: []` and `blocked_by: []`;
6. `L` yields `needs_split` and never appears in `task ready`;
7. null size yields `missing_size`; invalid size fails closed defensively;
8. `building`, persisted `ready`, `integrated`, `dropped`, `superseded`,
   `done`, `merged`, null, and unknown own states are `not_pending`;
9. two- and three-node indirect cycles yield `dependency_cycle`, never loop,
   and never enter `task ready`, including a mixed integrated/pending cycle;
10. a cycle elsewhere does not taint an unrelated ready task;
11. a raw orphan edge fails closed and names the missing prerequisite;
12. `task ready` returns only dispatchable objects in task-ID order and exits 0
    with an exact empty list;
13. precedence overlap is executable: non-pending plus null size is
    `not_pending`, and pending L plus cycle is `needs_split`, while each still
    reports its independently calculated direct blockers;
14. schema, raw task rows, timestamps, and dump contain no persisted readiness;
15. the new suite fails against the exact pre-change binary; and
16. affected task, migration, recovery, issue, stress, and tracker suites remain
    green, followed by normal repository CI.

## Consequences

- Nightshift can explain readiness without agent memory or a second state.
- Historical tasks with no size remain readable but cannot be dispatched until
  deliberately sized.
- Existing lifecycle state `ready` keeps its current PR/review meaning and is
  never confused with the derived `reason_code=ready` verdict.
- Indirect cycles become observable, fail-closed read verdicts instead of
  infinite traversal or accidental eligibility.
- Reads are O(V+E) and return a snapshot, not a durable reservation.

## Cost, scale, and reversibility

This remains tier 0: no service, dependency, credential, schema migration, or
monthly cost now or at 100x. Each readiness read scans the task graph once. If
measured p95 readiness reads exceed 100 ms at 10,000 tasks or 50,000 edges,
revisit with indexed recursive queries or a transactionally maintained
projection. Until that numeric trigger fires, a cache would add drift and
operational surface without evidence.

The change is reversible by removing the read fields and command; persisted
task facts remain untouched. No deployment or database migration is required.

## Review evidence

The first independent Codex review blocked two conflicts in the original issue
wording: cycle-bound S/M tasks were not excluded explicitly, and `L ->
needs_split` did not say what happens after a task has already started or
finished. Before any implementation, PM clarified the live issue to require
cycle-bound fail-closed behavior, pending-only eligibility, and the exact reason
precedence in this ADR.

Claude Staff then approved with zero blockers and requested executable overlap
cases for precedence plus an explanation of mixed cycles whose direct blocker
list is empty. Both are now in the verification contract and decision text. A
final Claude Staff re-review approved; the final Codex re-review confirmed both
original blockers were closed and reported no new blocker.

## Reversal trigger

Supersede this ADR if lifecycle state names are normalized so persisted
`state=ready` truly means “not started and dispatchable,” if issue #212 needs a
different atomic query contract, or if the measured scale trigger above fires.
