# ADR-0062: derive worker-dispatch admission from control-plane state, not caller-supplied slot_id/task_id/generation (#236, #471, #472)

Date: 2026-09-01 · Status: proposed -- awaiting further verification
Deciders: program (reconciling two independent blind proposals)
Proposers: `architect` (Codex, session `f8f6e9b41e8948e59ff876a993bc9dd9`, title `236-fix-design-proposal`), `crew_alpha` (Claude, session `4e78e2d069cf44d99e2be8384538c751`, title `236-fix-design-proposal`)
Conversation: program top-level session, this turn (owner-directed critical-path turn on #236)
Spec: GitHub issues #236 (parent), #471 (hot fix), #472 (backfill)

## Process

Two-vendor design-debate process, run as intended: `architect` (Codex) and
`crew_alpha` (Claude, the owning seam-A crew for `deploy/policies/**` and
`bin/nightshift-ctl`) each independently read the live issue thread and the
exact reviewed origin/main source (`20dee546c8c03f5d9fd1e8db23eb56ae89008b34`)
and wrote a full design proposal, blind to each other's draft. `staff` was
deliberately excluded from authoring either proposal, since it is the
standing independent reviewer of every ADR in this project; having it also
propose one of the two drafts under reconciliation would make it review its
own work. Full proposals preserved at `/tmp/issue236-review/architect-proposal.md`
and `/tmp/issue236-review/crew_alpha-proposal.md` (scratch; this ADR is the
durable record).

## Context

`worker_semaphore` (`deploy/policies/omnipods_policies.py:178-441`, ADR-0033)
requires `args.slot_id` (strict positive int), and for non-oncall counted
dispatch also `args.task_id` (non-empty str) and `args.generation` (strict
positive int), as literal sibling keys inside `sys_session_send`'s `args`
object. The `sys_session_send` MCP tool's own declared schema for that object
is `additionalProperties: false`, permitting only `input`, `purpose`, `model`,
`cost_budget`, `file_ids`. No call shape satisfies both simultaneously.

Reproduced this turn, against exact reviewed origin/main:
1. Direct source read at the pinned SHA confirms requirement (1) above.
2. A live, isolated, disposable probe (`sys_session_send(agent="product",
   title="236-schema-probe-disposable", args={input, purpose:"explore",
   slot_id:999999, task_id:"probe-236", generation:1})`) was rejected
   client-side, before any dispatch or cost: `Input validation error:
   Additional properties are not allowed ('generation', 'slot_id', 'task_id'
   were unexpected)`.
3. Consistent with 6+ independently-recorded live reproductions already on
   #236's thread (2 vendors, 5 distinct parameter shapes, spanning
   `implement`- and `review`-purpose sends and the oncall-reserve path).

`sys_session_send` is a native tool of the external `omnigent` engine, pinned
by commit SHA in `deploy/omnigent-runtime.lock.json`, not vendored, not
ours to change. The fix must live entirely inside
`deploy/policies/omnipods_policies.py` and/or `bin/nightshift-ctl`'s
control-plane tables, using only fields the tool schema already allows.

Blocks every counted engineer/QA/reviewer dispatch and the oncall-reserve
path org-wide since wave 1 (no successful counted-worker dispatch with
`slot_id` appears anywhere in this project's ledger history). Gates DoD
conditions 6, 7, 13, 19. Standing fallback: Working Agreement #5 (crew lead
builds directly under a slot it holds, cross-crew review becomes mandatory)
-- tolerated, not a fix.

## Options considered

**A. Flatten the org to a flat worker pool (issue #432's Option A).**
Rejected by both independent proposals. Neither the schema/policy conflict
nor the missing QA/review-continuation uniqueness invariant is caused by the
two-crew hierarchy; flattening removes the "different crew" referent DoD
condition 22 requires for independent cross-crew review, without fixing the
actual defect. Both proposers converged on rejecting this without being
asked to compare against it explicitly -- independent convergence on the
same rejection is itself evidence.

**B. Move admission onto caller identity via a control-plane lookup (issue
#432's Option B), refined against what the runtime event shape actually
exposes.** Both proposals independently arrived at a version of this.
`crew_alpha`'s proposal additionally corrected the literal wording of #432's
comment ("resolve the caller's identity") against the actual policy-hook
event shape (`{"type": "tool_call", "data": {"name", "arguments"}}` --
confirmed by grep across the whole module: no caller/session identity field
exists there at all). The only usable identity is the tuple the tool schema
already mandates: `agent` (top-level), `title` (top-level), `purpose`
(inside `args`, already required). **Adopted.**

**C. Ride the forbidden fields inside an already-allowed field (`purpose` or
`input`).** Already evaluated and rejected in a prior task's diagnosis
(#236 comment, 2026-08-28): breaks existing phase logic (`purpose` is
compared byte-exact against DB state) and violates ADR-0033's own explicit
disclaimer against inferring control-plane authorization from free text.
Neither new proposal re-litigated this; both explicitly built on the same
rejection. **Rejected, unchanged.**

**On the two proposals' points of difference, resolved as follows:**

- **Lookup key width.** `architect` proposed keying on `(expected_agent,
  expected_title)` alone, with a new blanket partial unique index across all
  slots. `crew_alpha` proposed keying on `(expected_agent, expected_title,
  expected_purpose)` -- three columns, all already schema-mandatory -- and,
  critically, *verified against the actual `bin/nightshift-ctl` schema*
  (independently re-confirmed by program: `grep -n "CREATE UNIQUE INDEX"`
  against the exact origin/main source) that three of the four phase/kind
  combinations already carry a uniqueness index (`one_active_new_reservation`,
  `one_active_planning_target`, `one_active_slot_per_claim`), leaving exactly
  one real gap: QA/review continuation, where `claim_id IS NULL` structurally
  excludes it from the existing claim-keyed index. **Adopted `crew_alpha`'s
  narrower, source-verified design**: it is strictly safer (fewer legitimate
  cases can collide with a 3-column key than a 2-column key), has a smaller
  migration blast radius (only the QA/review-continuation gap needs a new
  index, not a project-wide backfill-and-verify across every existing slot),
  and is grounded in a direct, re-checked read of the governing schema rather
  than an assumption that no protection existed. `architect`'s general
  algorithm shape (transactional derive-then-consume, ambiguity always
  denies, never resolved by a heuristic) is preserved unchanged -- the two
  proposals differ only in exactly which columns close the ambiguity gap, not
  in the mechanism.
- **WA-5 legacy admission backfill.** `architect` proposed a new
  `wa5_task_admissions` table plus an authority-gated `admission adopt-wa5`
  command and a dual-source read helper ("if both exist, deny as corrupt").
  `crew_alpha` proposed a synthetic, tagged (`kind='wa5_backfill'`,
  `state='released'`) row directly in the *existing* `worker_slots` +
  `task_admissions` tables, reusing the existing single-source admission read
  path unchanged. **Adopted `crew_alpha`'s design**: same outcome
  (auditable, reversible, additive), smaller surface area (no new table, no
  new command, no dual-source normalization logic to build, review, and keep
  in sync across `nightshift-ctl` and the policy), and does not introduce a
  second source of admission truth that every future admission read must
  remember to check.
- **Delivery split.** `architect` proposed five children (236-A schema,
  236-B derived binding, 236-C end-to-end coverage tests, 236-D WA-5
  reconciliation with a new table, 236-E oncall coverage), sequentially
  dependent. `crew_alpha` proposed two (the hot derivation+uniqueness fix,
  covering all three paths in one mechanism since they share one query
  shape; and the cold, non-blocking backfill), plus an optional third
  process/registry cleanup filed but not chartered. **Adopted `crew_alpha`'s
  split** (#471, #472): the derivation mechanism is genuinely one coherent
  transactional change -- splitting the index from the policy logic that
  depends on it (as 236-A/236-B would) creates an interval where #236 is
  still fully broken between two required merges, for no independent
  shippable value in between. "Split into the smallest independently
  shippable child issues only if genuinely necessary" argues for the
  tighter split here. `architect`'s explicit end-to-end coverage requirement
  (its 236-C) is folded into #471's acceptance criteria directly rather than
  chartered as a separate issue.

## Decision

Adopt Option B as refined by `crew_alpha`, incorporating `architect`'s
general transactional-derivation algorithm and rollback-trigger framing.

Inside the same `BEGIN IMMEDIATE` transaction `worker_semaphore` already
opens: replace the caller-asserted `args.slot_id` lookup with

```sql
SELECT * FROM worker_slots
WHERE state='active' AND dispatched_at IS NULL
  AND kind = :kind            -- 'oncall' or 'normal', from is_oncall
  AND expected_agent = :agent
  AND expected_title = :title
  AND expected_purpose = :purpose
```

Zero rows denies (unchanged "slot does not exist" case, just reached via a
different query). More than one row denies (ambiguous; provably unreachable
once the new index below ships; kept as fail-closed defense in depth, never
resolved by a heuristic). Exactly one row is the sole source of
`slot_id`/`task_id`/`generation`/`run_id`/`phase`/`continuation_kind`/
`claim_id` -- read directly, not asserted-by-caller-then-checked. Every
existing downstream check (holder match, phase/continuation_kind branching,
the one-use `UPDATE ... WHERE dispatched_at IS NULL` compare-and-set,
`task_admissions` insert) is unchanged.

Add one migration: `CREATE UNIQUE INDEX IF NOT EXISTS
one_active_qa_review_continuation ON worker_slots(task_id, continuation_kind)
WHERE state='active' AND phase='continuation' AND continuation_kind IN
('qa','review');`, plus a matching pre-INSERT check in
`_derive_normal_phase`'s qa/review branch in `bin/nightshift-ctl`, shipped in
the same PR as the policy change (shipping either alone either turns a
legitimate retry into a raw `IntegrityError`, or leaves the invariant
convention-only).

Covers all three dispatch paths with one mechanism: NEW/engineer,
continuation (QA/reviewer/implementer -- implementer already uniqueness-
protected via `claim_id`), and oncall-reserve (`kind='oncall'`,
`expected_agent='oncall'`, already written at grant time).

WA-5 legacy backfill (#472) is a separate, non-blocking, additive data
repair using synthetic tagged `worker_slots`/`task_admissions` rows -- no
new table, no new command, no dual-source read path.

Split into #471 (hot fix, P0) and #472 (backfill, P2, does not gate #471).

## Consequences

- Removes a class of caller transcription/hallucination error (the LLM
  re-typing a `slot acquire` response's numbers into a later call) without
  removing any real independent verification, since those numbers were never
  independently sourced from anywhere the caller could be trusted to
  originate them in the first place.
- `title` becomes a nonce-like dispatch identity for the lifetime of an
  undispatched slot within one `(agent, purpose)` pair. Reuse is fine once
  the preceding row is dispatched, released, or reaped.
- Once #471 is verified live, true delegated dispatch becomes viable for the
  first time since wave 1. This has two flagged, not-yet-actioned downstream
  effects: Working Agreement #5 should narrow back to its literal per-task-
  exception scope (currently operating as a standing default), and
  `.omni/registry-crew_alpha.json` / `.omni/registry-crew_beta.json`'s
  "dispatch is sterile" claims become stale. Both filed as fast-follow, not
  bundled into #471.
- No new operator escape hatch. `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1` is
  untouched by this change.

## Fail-closed invariant (unchanged in kind, restated for this design)

A counted or on-call named dispatch is allowed only if, inside one
`BEGIN IMMEDIATE` transaction, there exists exactly one `worker_slots` row
that is `state='active'`, `dispatched_at IS NULL`, matches this call's
`(kind, expected_agent, expected_title, expected_purpose)` exactly, whose
holder binds to the calling agent, and whose phase/continuation/task-state
cross-checks all still hold -- and that same transaction atomically marks it
dispatched so it can never authorize a second start. This is stated,
security-reviewed, and must be re-verified true of the merged code, not just
the design.

## Reversal trigger

Any of: a counted dispatch succeeds with no `worker_slots.dispatched_at`
transition; one slot authorizes more than one worker start; a dispatch binds
to the wrong task/generation/run/title/purpose; normal and oncall slots
become mutually selectable; the migration cannot establish uniqueness
without unresolved legacy conflicts; or, within the first 5 real
post-deploy dispatch attempts across any of the three paths,
`worker_semaphore` denies for a reason other than a genuine "no live
slot"/budget/task-state mismatch. On trigger: halt rollout, `git revert`
(lossless -- every piece of this change is additive: code, the new index,
and the tagged backfill rows are all independently reversible), fall back to
WA-5, reopen #236 with the new failure mode recorded rather than iterating
live against the org's single dispatch gate.

## Addendum — required changes from `staff`'s review (2026-09-01, session `529a808b31a74b92887ee22b178ae9f3`)

**Verdict: INCONSISTENT as originally drafted.** Two changes required to the
Decision/Migration section before #471 builds; both confirmed internal to
`crew_alpha`'s owned files, no cross-crew question reopened, no re-litigation
of the Option A/B or WA-5-table-vs-synthetic-row decisions above.

**1. Blocking defect — migration must not crash the control plane (staff §3).**
`ensure_schema(cx)` runs unconditionally on every non-readonly `nightshift-ctl`
invocation, and the three existing `CREATE UNIQUE INDEX` statements it
contains are bare, unguarded `cx.execute()` calls that are safe today only
because their columns (`phase`, `continuation_kind`) were brand-new when those
indexes were added — every legacy row was structurally excluded (`phase IS
NULL`). That precedent does not extend to `one_active_qa_review_continuation`:
if a duplicate active `(task_id, continuation_kind)` row already exists
(plausible — the exact race a retried `slot acquire --purpose review` without
releasing the first would produce, and retries against a dead-end acquire are
expected operator behavior given #236 has blocked all dispatch since wave 1),
a bare `CREATE UNIQUE INDEX` raises `IntegrityError` uncaught, and *every*
subsequent `nightshift-ctl` command of any kind — not just `slot acquire` —
crashes with exit 1 until an operator manually deletes the conflicting rows.
That is a total control-plane outage, not a fail-closed deny; the reversal
trigger listed above ("migration cannot establish uniqueness") cannot even
fire, because the tool used to observe it is what's crashing.

**Required:** guard both new indexes (this one and the one below) with a
race-tolerant precheck mirroring the `add_column`/`rename_column` idiom
already used four times in this exact file (`bin/nightshift-ctl:660-675`):
detect existing violating rows before the `CREATE UNIQUE INDEX` runs, and on
a hit, deny/report the exact conflicting `(task_id, continuation_kind)` or
`(expected_agent, expected_title)` groups rather than letting `ensure_schema`
raise uncaught. Empirically, live `.omni/control.db` has zero such rows today
(57 total `worker_slots` rows, zero active-undispatched) — but that is a fact
about today's data, not a property the design guaranteed, and the entire
point of #471 is that it does not ship today.

**2. Second required index — cross-task ambiguity is a distinct gap from the
QA/review one (staff §5).** The general derivation query has no `task_id`
filter (it can't — `task_id` is what's being derived), so its uniqueness
depends on `(expected_agent, expected_title, expected_purpose)` never
colliding *across different tasks*. `COUNTED_AGENTS` is a small, fixed,
reused-forever set, so `expected_agent` is never task-unique by construction;
this invariant currently rests entirely on the unenforced convention that
`title` happens to embed the task/issue id. This is a *different* ambiguity
class than the QA/review-continuation gap (which is same-task, two
reservations for one task) — `architect`'s original blanket index and
`crew_alpha`'s narrow index solve different problems, not the same one at
different widths, and the reconciliation above incorrectly treated adopting
the narrower one as strictly safer/sufficient.

**Required:** add `architect`'s general index alongside the narrow one, in
the same migration, guarded by the same precheck as (1):
```sql
CREATE UNIQUE INDEX IF NOT EXISTS one_undispatched_dispatch_identity
ON worker_slots(expected_agent, expected_title)
WHERE state='active' AND dispatched_at IS NULL;
```
Consequence if this invariant is ever violated pre-fix: fail-closed deny only
(re-confirmed against the ADR's §(1) fail-closed analysis — not a security/
authorization defect), and staff found zero historical title collisions in
the live DB — low-probability, not zero-risk, and a one-line additive index
with no interaction cost, so it ships in the same PR rather than being filed
separately.

**Not required / explicitly re-confirmed clean by staff:** the #471/#472
split boundary (traced #472's `state='released'` rows through every
`state='active'`-filtered query and confirmed zero live-dispatch-path
interaction); the fail-closed/ADR-0033-disclaimer analysis in this ADR's
§(1)/Consequences; no interaction found with ADR-0061 (reap) or other
in-flight ADRs.

**Disposition:** both changes are additive, same-file, same-PR, no new
schema-column risk, no change to the Options-considered reconciliation above.
Folded into #471's charter directly rather than re-running the two-vendor
proposal round for a fix this narrowly scoped and this concretely specified.
