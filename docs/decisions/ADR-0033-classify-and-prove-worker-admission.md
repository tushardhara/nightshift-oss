# ADR-0033: Classify and prove worker admission at the root dispatch gate

Date: 2026-08-27 · Status: accepted
Deciders: crew_alpha, architect, staff, and security review
Spec: GitHub issue #212
Depends on: ADR-0002, ADR-0022, ADR-0031, ADR-0032
Supersedes: ADR-0032 section 5 only where it assigns a second READY recheck to
issue #213; ADR-0032 readiness semantics remain unchanged

## ELI5

Nightshift has three different reasons to send a crew worker:

1. **PLANNING** prepares a design or test plan before coding.
2. **NEW** starts implementation for the first and only time.
3. **CONTINUATION** verifies, reviews, repairs, or recovers work that already
   started correctly.

Each send receives a one-use ticket tied to the exact task and worker. Only a
successful NEW send writes the permanent database receipt that says the task
was admitted. Later work must prove that receipt. Saying “continuation” is not
proof.

## Context

ADR-0032 derives READY only for a pending S/M task with no unsatisfied edge or
cycle. That predicate is correct for first implementation, but not for the QA,
review, repair, and recovery turns that happen after a task becomes `building`
or persisted `ready`.

The live lifecycle also has a third pre-build path: two blind engineering
proposals and QA test planning consume worker capacity before implementation.
Calling those turns NEW would falsely admit implementation; calling them
CONTINUATION would require evidence that cannot yet exist.

Finally, issue #118 proved that the shipped per-seat `worker_semaphore` policy
declarations are not loaded for nested seats. Bundle-root policies do govern
nested dispatches. Existing `dispatched_at`, task state, PR URL, agent-run
history, or caller labels therefore cannot be treated as universal admission
proof.

## Threat model and guarantee

This decision enforces structured dispatch authorization. The root policy
validates the exact phase, task, target agent, title, purpose, slot generation,
and durable control-plane evidence. Caller-supplied values may narrow a
decision, never widen it.

Omnigent does not give this policy trusted caller/session identity and the
policy cannot infer free-text intent. Therefore this ADR does not claim that
`purpose=explore` makes a writable worker physically read-only, that an allowed
send proves the child process started, or that admission grants Git/PR/merge
rights. The immutable receipt means only that one first implementation send was
authorized after every check below passed.

## Options considered

### A — Require ADR-0032 READY for every normal slot

Rejected. It denies QA, review, repair, and recovery after the task leaves
`pending`, and it cannot represent pre-build planning.

### B — Trust task state, old slot dispatch timestamps, or caller phase

Rejected. Raw `building`/`ready` can be written without valid admission, the
per-seat policy did not reliably write old dispatch fields, and a caller label
is adversarial input.

### C — Add physically read-only planning seats

Rejected for #212. It would require trusted runtime/session identity and new
capability-restricted seats. The smaller structured boundary closes accidental
and mislabeled dispatch authorization without claiming free-text enforcement.

### D — Root-gated classified reservations plus an immutable admission row

Chosen. It fits the actual three paths, uses the root policy seam proven by
#118, keeps one transactional SQLite authority, and remains additive and
reversible.

## Decision

### 1. Classify every target fail-closed

The bundle-root policy statically partitions targets:

- counted normal: both crews' engineers, QA seats, and reviewers;
- on-call: only `oncall`, using the reserved slot path;
- exempt: Program/shared orchestrators, crew leads, and `pm`/`beta_pm`; and
- unknown: denied.

Only named `sys_session_send(agent, title, args)` may dispatch counted workers.
Opaque `session_id` sends and every `sys_session_create` form—including both
`agent_id` and `config_path`—are unsupported and denied globally because their
target cannot be proven at policy time.

The root policy is the sole semaphore authority. Remove the inert alpha/beta
copies to avoid both false confidence now and double consumption if upstream
policy loading later changes. Without the explicit operator escape hatch, a
loaded policy denies a missing/unusable DB, malformed input, or SQLite error.
`NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1` remains available only for deliberately
unmanaged local/toy runs. Because an absent policy cannot evaluate anything,
managed release preflight must prove the root gate is loaded and reject that
escape hatch before serving.

### 2. Make a reservation different from an admission receipt

Extend `worker_slots` additively with exact immutable dispatch metadata:

`phase, expected_agent, expected_title, expected_purpose, continuation_kind,
claim_id, generation, run_id`

A live `phase=new` slot is a mutable reservation. It may expire or be released.
A separate `task_admissions` row is immutable and begins only at successful NEW
consumption:

`task_id PRIMARY KEY, new_slot_id UNIQUE, crew, agent, title, admitted_at`

No legacy field or task state substitutes for that row. A partial unique index
makes the reservation single-winner:

```sql
CREATE UNIQUE INDEX one_active_new_reservation
ON worker_slots(task_id)
WHERE state = 'active' AND phase = 'new';
```

Inside the allocation transaction, an exact same-holder/tuple retry returns the
existing live reservation with `reused:true`; a different tuple is denied.
`task_admissions.task_id PRIMARY KEY` independently makes final admission
single-winner.

### 3. Treat PLANNING as counted but non-admitting

PLANNING requires a pending, unadmitted known task; an engineer or QA target;
and purpose `explore` or `search`. It never changes task state, creates an
admission, or creates a continuation claim.

PLANNING and NEW are mutually exclusive for a task at both allocation and root
consumption. A dispatched planning slot stays active until release/TTL, so NEW
cannot begin while planning is still running. At most one planning slot exists
per exact task/target. Globally, planning may occupy at most one fewer than the
normal-slot capacity, with a minimum cap of one; today's cap is two.

Every check and stale-resource reap occurs under `BEGIN IMMEDIATE`, closing
both allocation orders and the consume race.

### 4. Admit NEW only once and only while actually READY

NEW allocation requires the exact task/crew implementer/title, purpose
`implement`, no admission, no live task planning slot, and the full ADR-0032
READY snapshot. It reserves capacity and a one-use exact-bound slot but leaves
the task `pending`.

The root consume transaction is the linearization point. It revalidates the
slot tuple and generation, task existence/state, absence of planning/admission,
and the full ADR-0032 graph again. One transaction then marks the slot
dispatched, inserts the immutable admission, and changes `pending` to
`building`. A denial or transaction error changes none of those facts.

Release/expiry of unconsumed NEW leaves the task pending and creates no
admission. A later request rechecks READY. If policy authorization commits but
transport or worker startup fails, the task remains admitted/building and uses
recovery; it never receives a second NEW admission.

Because the consume-time READY check is required for #212 correctness, this ADR
narrowly supersedes ADR-0032 section 5's assignment of that recheck to #213.
ADR-0032's READY predicate and snapshot semantics are unchanged. #213 must be
re-triaged after #212 merges and is not implemented as a separate work item by
this decision.

### 5. Prove every continuation from admission plus subtype evidence

Every continuation requires the immutable admission, an exact allowed task
state, PR-stage evidence where applicable, and a fresh one-use slot. The only
allowed states are `building`, persisted `ready`, and `fixing`; null, unknown,
and every other state deny for every subtype.

| Subtype | Exact task states | Exact target and purpose | Additional evidence |
|---|---|---|---|
| QA | `building`, `ready`, or `fixing` | crew QA, `implement` | admitted task and task PR |
| review/cross-review | `building`, `ready`, or `fixing` | crew reviewer, `review` | admitted task and task PR |
| implementer continuation | `building`, `ready`, or `fixing` | original admitted engineer/title, `implement` | exact live task claim bound to claim id, generation/run id, and slot |

The policy cannot observe whether an existing child is alive, so it does not
trust a caller-selected “fix” versus “recovery” label. Every post-admission
engineer turn claims first, including in-place rework. One live claim per task
keeps the implementer single-owner; completion releases it and abandoned claims
expire.

Mutable in-place repair is implementer continuation. ADR-0022 broker-frozen or
post-publication Codex repair instead creates a superseding task/worktree/branch
and passes NEW.

### 6. Keep on-call and legacy rows fail-closed

Only an on-call-kind slot bound to `oncall` receives the current taskless
reserve exemption. A crew worker cannot self-select it.

Old slots with null phase continue counting until release/expiry but authorize
nothing. No admission is inferred from old `dispatched_at`, `building`,
persisted `ready`, PR URLs, or agent runs. Managed cutover requires zero active
legacy slots and zero unfinished legacy task needing continuation. Otherwise it
stops for a separately reviewed evidence-reconciliation procedure.

No VPS deploy or live database mutation belongs to #212.

## Verification contract

Tests must prove all of the following against alpha and beta:

1. Root nested sends deny without an exact classified slot and allow once with
   one.
2. Unknown/opaque/malformed dispatches and missing/corrupt DB fail closed; an
   executed managed preflight proves the root gate is loaded and rejects
   managed fail-open before serving.
3. PLANNING never admits or mutates task/claim state and cannot race NEW.
4. Concurrent READY NEW allocation/consumption has exactly one winner.
5. Missing/invalid/L/blocked/cycle/non-pending NEW denies at allocation and
   consume with no partial mutation.
6. Abandoned/stale NEW reservations never unlock continuation.
7. Manual lifecycle/PR/legacy facts never substitute for admission.
8. QA/review require exact role and PR-stage evidence.
9. Every implementer continuation requires the exact live claim, original
   agent/title, generation/run id, task, and slot.
10. On-call reserve, global capacity, TTL, holder binding, one-dispatch,
    fencing, recovery, and exact-SHA behavior remain intact.
11. Alpha/beta workflows are symmetric and no longer write `building` before
    NEW consumption.
12. New bypass tests are baseline-red on pre-change main; focused suites and
    the full test runner pass after implementation.

## Consequences

- First implementation admission becomes an auditable durable fact rather than
  a lifecycle inference.
- Legitimate QA/review/rework/recovery continues without pretending started
  work is still READY.
- Pre-build planning consumes capacity without minting implementation rights.
- Fix/recovery instruction paths must add exact claim acquisition/release.
- Root agent classification becomes explicit; adding a new seat requires a
  reviewed classification update.
- #212 is a coherent M-sized schema, policy, workflow, and test change rather
  than a one-file slot predicate.

## Cost, scale, and reversibility

This remains tier 0: SQLite and existing policy code only, no new service,
dependency, credential, or recurring cost now or at 100x. Added operations are
indexed single-task/slot lookups plus the existing O(V+E) readiness snapshot at
NEW allocation and consumption. Revisit only if measured p95 admission exceeds
100 ms at 10,000 tasks or 50,000 dependency edges.

The schema change is additive. The behavior can be disabled by removing the
root gate and classified workflow only in an unmanaged environment; managed
rollback must restore the previous exact-SHA bundle and its compatible control
database copy. Admission rows are retained as audit evidence.

## Review evidence

PM, Codex architecture, Claude Staff, and adversarial control-plane reviews
first rejected the two-class proposal. Before implementation, their findings
were reconciled into the final #212 contract: the real PLANNING path, root
enforcement, immutable admission, admission-time READY recheck, ADR-0022 repair
path, PLANNING/NEW exclusion, and claim-backed implementer continuation.

All final reviewers returned READY under the explicit structured-authorization
threat model. The issue amendment preserves the earlier blocker comments as
history.

## Reversal triggers

Supersede this ADR if Omnigent exposes trusted caller/session identity that can
support physically restricted planning, if the lifecycle gains a canonical
admission state that is transactionally equivalent to the immutable receipt,
or if the measured scale trigger above fires. Never edit this accepted decision
in place.
