# ADR-0084: Repository scope on the control plane

Date: 2026-09-05 · Status: **accepted**
Deciders: platform · Proposers: platform (Claude)
Relates to: ADR-0005 (control plane discovery), ADR-0043/ADR-0046 (at-most-once write
invariants live outside `TASK_FIELDS`), ADR-0044 (Program authority fencing),
ADR-0049/#541 (path scope), ADR-0062/#471 (guarded unique-index migrations),
`bin/nightshift_authority.py` (repository identity: `validate_repository_id`,
`registry_exists`) · Issue: #165

## Context

Every control-plane table in `bin/nightshift-ctl` — `tasks`, `task_claims`,
`worker_slots`, `leases`, `task_dependencies` — was implicitly single-repository.
`authority.db` (`bin/nightshift_authority.py`) already addresses per `repository_id`;
the plane's own work tables never got the same treatment.

The sharpest consequence was `leases`: `leases.resource` is matched by exact string
equality, and the partial unique index `one_active_lease ON leases(resource)` was
plane-wide. The moment a second repository's work runs through this control plane,
`bin/foo.py` in this repository and `bin/foo.py` in the second repository become the
SAME resource — one lease acquisition blocks the other, or worse, a lease that is
genuinely held elsewhere reads as free.

## Decision

**One control plane, logically scoped work rows, with a named home repository.**

The plane's *global* resources — the worker semaphore (`max_concurrent_workers`),
`sequences`, `checkpoints`, `program_authority`, `meta.current_run_id` — stay global:
they are properties of the machine and the org, not of a repository. The five WORK
tables (`tasks`, `task_dependencies`, `task_claims`, `worker_slots`, `leases`) get a
`repository_id` column. This is a hybrid, not blanket multi-tenancy, and this ADR
says so rather than pretending the column makes every table multi-tenant.

### The column

On all five tables, added only via the existing race-tolerant `add_column` idiom
(never in `SCHEMA`, so a fresh and a migrated plane get identical physical column
order), appended **last** among each table's own columns:

```sql
ALTER TABLE <t> ADD COLUMN repository_id NOT NULL DEFAULT 0
  CHECK(typeof(repository_id)='integer' AND repository_id>=0)
```

No type name on the declaration. SQLite's `INTEGER` column affinity silently
coerces a string `'7'` to the integer `7` *before* a `CHECK` runs — accepting the
exact TEXT-vs-INTEGER confusion this column exists to make impossible. Verified
empirically (sqlite 3.53.4): the untyped declaration backfills every pre-existing
row to integer `0`, accepts `7`, and refuses `'7'`, `7.0`, and `-1`.

`NOT NULL` is load-bearing, not stylistic. SQLite treats `NULL` as *distinct* in a
partial unique index (verified empirically: two active rows with a `NULL` scope
column both landed in an index meant to enforce one-per-scope). A nullable column
would silently **void** the two uniqueness guarantees this migration narrows below,
for every legacy row, the moment they were widened to include it. `DEFAULT 0` is
therefore the backfill: every row that predates this migration means exactly
`SCOPE_HOME` (see below), and the `ALTER` itself is what gives it that value —
there is no separate backfill step for the column itself.

`SCOPE_HOME = 0` is a **control-plane-local sentinel only**. It never appears in
`authority.db`, in an export manifest, or in a conformance decision — it names "this
plane's own, possibly-not-yet-named repository," a concept that exists only inside
control.db.

### The default: implicit-home, gated behind naming the first repository

Resolution order for every write and every scoped read (`resolve_scope` in
`bin/nightshift-ctl`), evaluated inside the caller's own `BEGIN IMMEDIATE`:

1. If a task is already in play, inherit `tasks.repository_id`. An explicit
   `--repository-id` that disagrees is refused (`repository_scope_conflicts_with_task`,
   exit 3). Claims and slots therefore cannot be mis-scoped at all: they take their
   scope from their task structurally and are never offered a flag of their own.
2. Otherwise, an explicit `--repository-id` — but a non-`SCOPE_HOME` value is refused
   (`repository_scope_unbound`, exit 3) while `meta.repository_id` is unset.
3. Otherwise `meta.repository_id` — the bound home, once one exists.
4. Otherwise `SCOPE_HOME`.

This is the constraint's own trade-off, made explicit: the ticket forbids regressing
this repository's own operation, and an explicit-required default would break every
existing caller and every charter on day one. The concern the ticket raises about an
implicit default — "an implicit default that silently mixes repositories would be
worse than the bug" — does not apply here, because the implicit value always denotes
**exactly one** repository: the unnamed home, or (once bound) the named home. A
second repository can enter the plane only by being named explicitly, and naming is
refused until the home itself has a name (step 2 above). The failure mode the ticket
warns about — two repositories both omitting the flag and silently sharing one
bucket — is therefore structurally unreachable: there is only ever one implicit
bucket, and it is always the same one repository.

### `repo bind` — the second half of the backfill

`nightshift-ctl repo bind --repository-id N --state-root <root>` names the plane's
home, once, inside one `BEGIN IMMEDIATE`:

1. Refuses `repository_scope_not_attested` (exit 3) unless
   `nightshift_authority.registry_exists(state_root, N)` is true. **`authority.db` is
   the single attested answer to "which repository is this"; `meta.repository_id` is
   a PROJECTION of that attestation and must never be able to disagree with it.**
   Neither `repo bind` nor `project bind` owns identity — both bind against
   `authority.db`.
2. Idempotent: binding to the value already bound is a no-op, `ok: true`.
3. Refuses `repository_scope_already_bound` if already bound to a *different* value.
4. Refuses `repository_scope_plane_not_single_repository` if any row already carries
   a non-zero scope other than `N` — a plane that already went multi-repository (by
   some route other than this file's own guarded writes, e.g. a direct-SQL repair)
   has no single home left to name.
5. `UPDATE <each of the five tables> SET repository_id=N WHERE repository_id=0`, then
   writes `meta.repository_id`.

Step 5 is what relabels the legacy bucket rather than leaving a permanent dual
bucket — after a bind, `bin/foo.py`@0 and `bin/foo.py`@N are never two "free"
resources in what is really one repository.

`repo show` reports the binding and per-table, per-scope row counts. Both verbs are
plane-using (open `control.db`), unlike `project bind`/`project show`, which bind a
private descriptor that lives under `state_root` outside every checkout.

### Indexes — each decided, not swept

* `one_active_lease(resource)` → **replaced** by
  `one_active_lease_per_repository(repository_id, resource) WHERE state='active'`.
  Required: this is the ticket's headline bug.
* `one_undispatched_dispatch_identity(expected_agent, expected_title)` → **replaced**
  by `one_undispatched_dispatch_identity_per_repository(repository_id, expected_agent,
  expected_title) WHERE state='active' AND dispatched_at IS NULL`. Required: its own
  pre-165 comment already named the gap — its uniqueness rested entirely on
  `(expected_agent, expected_title)` never colliding *across tasks*, which is silent
  about colliding across *repositories* too.
* `one_active_claim(task_id)`, `one_active_new_reservation(task_id)`,
  `one_active_planning_target(task_id, expected_agent)`,
  `one_active_slot_per_claim(claim_id)`,
  `one_active_qa_review_continuation(task_id, continuation_kind)`,
  the `task_dependencies` and `task_admissions` primary keys → **unchanged**. Every
  one of these keys on `task_id`/`claim_id`, and `tasks.id` is a plane-global
  `TEXT PRIMARY KEY` — a task id already resolves to exactly one repository, so
  these are already per-repository as a structural consequence. Widening them would
  be a live index swap in exchange for nothing.
* `one_active_program_authority(state)` → **unchanged, deliberately global.**
  `cmd_slot_acquire`'s capacity count reads `worker_slots WHERE state='active'` with
  no scope filter, against one `max_concurrent_workers` in `.omni/capacity.json` —
  the worker budget is a property of the machine, not of a repository. If capacity
  counting is global, the fence that gates dispatch into it must be global too, or
  two Program sessions could dispatch concurrently into one semaphore (ADR-0044 §1's
  exact race).

Both replacements go through the existing `_guarded_unique_index` helper, and the
**old index is dropped only when the guarded create returns `True`.** If a legacy
duplicate blocks the create (a state that cannot arise from a genuine pre-165
migration, since the old plane-wide index already forbade the exact duplicate that
would now conflict — but is defended anyway), the old, narrower index survives
untouched and today's behaviour is preserved exactly; the swap self-heals on the
next `ensure_schema`. Dropping unconditionally would leave the plane with **no**
uniqueness at all on that rare call — strictly worse than the bug being fixed.

`one_active_lease`'s bare `CREATE UNIQUE INDEX` line is removed from the `SCHEMA`
constant entirely (not just superseded in `ensure_schema`): `ensure_schema` runs
`executescript(SCHEMA)` unconditionally on every invocation, so leaving the line in
`SCHEMA` would recreate the exact index the migration just dropped, every time.
`one_undispatched_dispatch_identity` was never in `SCHEMA` to begin with (it was
already guarded-created inside `ensure_schema`), so only its guarded-create call
needed replacing.

### Write paths

`task upsert` stamps the resolved scope on `INSERT`, and refuses a scope *change* on
`UPDATE` (`repository_scope_immutable`). `repository_id` stays **out of
`TASK_FIELDS`**, the same ADR-0043/ADR-0046 rule already applied to `started_at`,
`extension_seconds`, `ready_at`, `pr_opened_at`, and `integrated_at`: a field with an
at-most-once write invariant is unenforceable if the generic `task upsert` whitelist
can also reach it. It is exposed, read-only, in `_task_readiness_snapshot`'s
per-task dict — the same "exposed but not settable" precedent those five columns
already established.

`claim acquire` and `slot acquire` inherit from the task row they already read — no
`--repository-id` flag exists on either verb; there is no argument that could ever
disagree with the inherited value, so there is no way to be wrong. `slot acquire
--oncall` has no task and resolves through the ordinary explicit/bound/`SCOPE_HOME`
order. `lease acquire` inherits from `--task` when that task row exists, and falls
through to the ordinary order otherwise — it must not, and does not, start refusing
an unknown `--task`, which it has always accepted with no existence check.

`task dependency-add` widens its two lookups to also read `repository_id` and
refuses a cross-repository edge (`cross_repository_dependency`) before it can be
written — the guard that makes the column on `task_dependencies` load-bearing rather
than decorative.

### Read paths scoped

`lease acquire`'s conflict lookup, `lease release`, `lease break`, `lease list`,
`seq next`'s lease gate, `slot acquire`'s dispatch-identity precheck, the #541
lease-overlap and in-flight-task loops inside `slot acquire`, `task list`,
`audit_worktrees` (shared by `worktree audit` and `reap`), and `reap`'s remote-branch
correlation all gained a `repository_id` filter. `cmd_dump`'s `task_dependencies`
projection names its three columns explicitly (`SELECT *`-based projections —
`slots`, `claims`, `leases`, `tasks` — pick up `repository_id` automatically once the
column exists; no code change was needed there).

`reap --delete`'s explicit `--repository-id` is further constrained, not just
filtered: if it disagrees with this plane's own bound scope (or, unbound, with
`SCOPE_HOME`), it is refused outright (`repository_scope_conflicts_with_home`,
exit 3) rather than silently accepted. `resolve_scope`'s rule 2 will hand back
*any* attested id, or the `SCOPE_HOME` sentinel, once bound — correct for a
row-scoping read, but `--delete` is irreversible, and a mismatched scope there
does not just fail to see another repository's rows (the point of every other
entry in this section); it fails to see *this* plane's own live tasks too,
because `audit_worktrees`/`reap_delete_branches` correlate delete-eligibility
against exactly one scope's task rows. A caller who mistypes `--repository-id`
(including `0`, which reads as "my own repository" but, once bound, names the
now-empty pre-bind bucket instead) would otherwise have every ADR-0061
delete-protection (conditions b and h) silently discarded, on a verb ADR-0061
already documents as running unattended on every heartbeat. No caller in this
codebase passes `--repository-id` to `reap` today, so the refusal is a no-op
for every existing invocation; it only fires for a scope that was never
correlatable to anything real in the first place.

### Deliberately unscoped

`expire_stale` (expiry is a function of time, not of repository); the worker-slot
capacity count; `sequences`; `checkpoints`; `program_authority`;
`meta.current_run_id`; `task_admissions` (its own primary key is `task_id`, already
plane-global); every claim/slot read keyed on `task_id`/`claim_id` alone.

## Deviations from the drafted plan

Two places where the codebase, read carefully, disagreed with the plan this ADR was
drafted against:

1. **`bin/nightshift_authority.py` exports no `ENV_STATE_ROOT`, `ENV_REPOSITORY_ID`,
   or `scope_from_env`.** Exhaustively grepped: neither the strings
   `NIGHTSHIFT_STATE_ROOT`/`NIGHTSHIFT_REPOSITORY_ID` nor a `scope_from_env` function
   exist anywhere in this repository as of this change, and
   `bin/nightshift_authority.py` has no `__main__`/CLI of its own yet.
   `validate_repository_id` and `registry_exists` — the two names this feature
   actually depends on — are present exactly as expected. `repo bind`/`repo show`
   therefore require `--state-root` explicitly (the same pattern `memory *`/
   `project show` already use) rather than falling back to an environment carrier
   that does not exist yet. A follow-up can wire the env fallback in once it lands;
   nothing here forecloses it.
2. **No `--repository-id` flag on `task dependency-add` or `dump`.** Both were named
   in the original CLI-surface list, but neither has a consumer: a dependency edge's
   scope is always derived from the two tasks it names (there is no "no task in
   play" case to disambiguate), and `dump` reports every row globally by design (see
   "Read paths scoped" above) with no per-repository filtering to gate. An unused
   flag is dead plumbing — a way to be wrong with nothing reading it — so neither was
   added.

## Options considered

### A — Repository scope on the five work tables (chosen)

As decided above.

### B — Per-repository control planes

Nearly the status quo — `control.db` is already discovered per checkout
(`discover_control_plane`). Rejected because it multiplies the worker semaphore: N
planes each independently granting `max_concurrent_workers` is N× the real budget on
one box, exactly the invariant ADR-0044 exists to protect.

### C — Composite `(repository_id, id)` primary key on `tasks`

The honest long-term answer. Rejected for now: it is a table rebuild of `tasks` plus
a composite-key change to `task_claims`, `worker_slots`, `task_admissions`,
`review_receipts`, `task_dod_links`, `scheduled_run_detector_state`, and
`bookkeeping_receipts` — far past an additive migration, and unnecessary while task
ids are plane-global. See "Reversal trigger" below.

### D — Nullable `repository_id`

Disproven empirically (see "The column" above): it silently voids both narrowed
unique indexes for every legacy row.

## Consequences

Makes easy: a second repository's leases, claims, and slots no longer collide with
this repository's identically-named resources; `reap`/`worktree audit` never act on
another repository's tasks. Makes hard: nothing new — every existing caller that
supplies no `--repository-id` is byte-for-byte unaffected, because SCOPE_HOME is the
only value that has ever existed until a bind names a second one. Forecloses,
*for now*: reusing a task id across two repositories (option C, deferred, not
forbidden).

## Cost

No new operational surface — no new process, no new dependency, one additive SQLite
column and two additive indexes on an existing file. `repo bind`'s backfill holds
this plane's write lock (`BEGIN IMMEDIATE`) for the duration of five `UPDATE`
statements; on a large live plane this could stall concurrent agents briefly. It is a
one-time operator action, not a per-request cost, and it self-heals on retry.

## One-way door?

Partially. The column and its `DEFAULT 0`/`CHECK` are additive and reversible (drop
the column, drop the two new indexes, recreate the two old ones). `repo bind`'s
backfill is a real `UPDATE` across five tables and is not itself undone by any verb
in this change — reversing a bind (moving rows back to `SCOPE_HOME`, or relabelling
an already-multi-repository plane) has no verb and is deliberately out of scope.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`). This is the mechanism a later tier
would need, not a tier commitment itself.

## Reversal trigger

The first time a caller needs to reuse a task id across two repositories. `tasks.id`
being a plane-global primary key is exactly the premise every unwidened index in
this ADR rests on; the day that premise is false, option C (composite
`(repository_id, id)` primary key) is the correct next step, and the seven indexes
named "unchanged" above are exactly the ones that would then need widening.
