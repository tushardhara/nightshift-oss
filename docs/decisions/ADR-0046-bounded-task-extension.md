# ADR-0046: One durable, bounded extension for an expired task (`task extend`)

Date: 2026-08-29 · Status: accepted · Deciders: crew_alpha lead · Proposers: none (see §Process — cross-vendor debate blocked)
Conversation: crew_alpha session (title `217-build`) · Spec: `.omni/backlog.md` (crew_alpha section, branch `crew_alpha/217-dor`), full DoR reasoning `.omni/reports/2026-08-29-217-dor-verdict.md`
Amends: ADR-0032 (§3 precedence table — the `budget_expired` row ADR-0045 added gains one additive term; the table's runnable-state guarantee is unchanged), ADR-0045 (the expiry formula itself)

## Process — cross-vendor debate could not run (WA-5)

Per the mandatory design-debate process, a `qa` planning slot was acquired
this session (`slot_id=18`, `generation=1`, title `test-217-extend`,
`nightshift-ctl slot acquire --holder crew_alpha/qa --crew crew_alpha --task
217-build --agent qa --title test-217-extend --purpose explore`) and
dispatched for a test plan. `sys_session_send` rejected it, reproduced live
this session under both argument shapes documented by issue #236:

1. `slot_id`/`task_id`/`generation` passed inside `args` →
   `Input validation error: Additional properties are not allowed
   ('generation', 'slot_id', 'task_id' were unexpected)`.
2. The same call with those three fields omitted →
   `Denied by policy: args.slot_id must be a strict positive integer`.

`sys_session_send`'s own JSON schema declares `args` with
`additionalProperties: false` and does not list `slot_id`/`task_id`/
`generation` at all (confirmed by fetching the tool's live schema this
session), so no argument shape can satisfy `worker_semaphore`
(`deploy/policies/omnipods_policies.py:178-...`), which requires them for
any `COUNTED_AGENTS` target (`eng_claude`, `eng_codex`, `qa`,
`reviewer_claude`, `reviewer_codex`). This is issue #236 (P0, open), the
same defect ADR-0043/ADR-0044/ADR-0045 hit on issues #214/#174/#215, and
that #216's own build (PR #339, ~2 hours before this one) hit again. The
unused slot was released (`slot release --slot 18`) rather than left to
expire.

`pm` dispatch is unaffected (`pm` is in `EXEMPT_AGENTS`, not classified
through `worker_semaphore`), so the Definition-of-Ready pass that produced
this ticket's frozen 12 acceptance criteria (`.omni/reports/
2026-08-29-217-dor-verdict.md`) proceeded normally; only the two-vendor
**build** debate is blocked.

Per **Working Agreement 5** (`.omni/working-agreements.md`, item 5 —
*"a crew lead whose worker dispatch is sterile builds under a slot it
holds, and says so in the PR body... independence is preserved by
cross-crew review, which becomes mandatory"*), this ADR is **single-author**,
and the implementation and its tests are authored directly by the
crew_alpha lead rather than by `eng_claude`/`eng_codex`/`qa`. That is a real
gap, not a formality — flagged here, in the PR body, and in the report to
the program. Cross-crew review (crew_beta, round-robin) is the mandatory
independent check for the resulting diff, standing in for both in-crew
review and QA this round. Not a standing authorization for future tasks —
reconfirmed live, for this task specifically, per crew_alpha's own rule
against inferring WA-5 from precedent alone.

## Context

Issue #217 asks for exactly one durable, bounded extension for a task whose
budget has already expired (issue #216's enforcement — refusing new worker
capacity once `_task_readiness_snapshot` reports `reason_code=
budget_expired` — otherwise leaves a nearly-finished task with no recovery
short of abandoning or re-splitting it). #216's own diff (`gh pr view 339
--json files`) introduced no new `tasks` columns; it added two denial
*checks* that both consume `reason_code` by reference through one shared,
aliased function (`bin/nightshift-ctl`'s `_task_readiness_snapshot`, aliased
in `omnipods_policies.py` as `task_readiness_snapshot`). This means #217
only has to extend that one shared expiry formula once — #216's two
checkpoints re-honor an extended task automatically, with zero code change
on their side. Confirmed by reading both checkpoints directly (`.omni/
reports/2026-08-29-217-dor-verdict.md` §1), not assumed.

## Options considered

No competing vendor proposals exist (see §Process). The options below are
the lead's own analysis, covering the three seams the DoR verdict left as
technical (non-product) calls.

### A1 — Three additive nullable columns on `tasks`, excluded from `TASK_FIELDS` (chosen)

`extension_seconds REAL`, `extension_reason TEXT`, `extended_at REAL`,
added via the same `add_column`-if-missing idiom already used for
`started_at`/`budget_seconds`. Kept out of `TASK_FIELDS`/`TASK_ROW_FIELDS`
for the same reason ADR-0043 keeps `started_at` out: a field with an "at
most once, ever" write invariant (AC4a) is unenforceable if `task upsert`'s
generic whitelist can also reach it. Written only inside `cmd_task_extend`'s
own guarded transaction, read only inside `_task_readiness_snapshot`'s
expiry formula and `cmd_task_extend`'s own success payload (AC9).

### B1 — A separate `task_extensions` table (rejected)

One row per extension request (granted or denied), giving a full audit
trail "for free" via table scan instead of relying on three columns plus
git history of `task extend` invocations. Rejected: the issue's own scope
is exactly one extension per task, ever (AC4a) — a table implies a
one-to-many relationship this ticket deliberately forecloses, adds a new
migration surface (FK, index) for a fact that fits in three scalar columns,
and every consumer this ticket names (`_task_readiness_snapshot`, `task
extend`'s own success output) needs only the CURRENT extension, never a
history. If a future ticket needs a full audit log of denied attempts too
(not just the one granted extension), that is new, additive scope for a new
ADR — not a reason to pay the schema cost now for a feature not asked for.

### A2 — Reuse `_task_readiness_snapshot`'s own verdict for AC4b's eligibility check (chosen)

`cmd_task_extend` calls `_task_readiness_snapshot(cx).get(args.id)` and
requires `reason_code == "budget_expired"` exactly, the same string-equality
discipline #216 froze for its own two checkpoints, rather than
re-deriving "is this task currently over budget" locally from
`started_at`/`budget_seconds`/`now()`.

### B2 — Reimplement the expiry test locally inside `cmd_task_extend` (rejected)

Read `started_at`/`budget_seconds`/`extension_seconds` directly and compare
against `now()` inline, avoiding a second full-graph read
(`_task_readiness_snapshot` joins `task_dependencies` too, which `task
extend` does not need). Rejected: this is exactly the "two implementations
of one computation" duplication ADR-0032/ADR-0045 both warn against by
name — a future change to the expiry formula (e.g. a second amendment) would
have to be made in two places to stay correct, and the DoR verdict's own
grounding work (§1) exists specifically to prevent this class of drift. The
extra join cost is not measured to matter at this task's volume (single-row
CLI verb, not a hot dispatch path), so there is no efficiency case for B2
either.

### C — No upper bound on `--seconds` (chosen, per the DoR verdict's own out-of-scope call)

The issue's own AC2 only demands finite+positive, the same bar #215 set for
`budget_seconds` itself. A numeric ceiling is a customer-facing tradeoff the
DoR verdict deliberately forked to `.omni/decisions.md` as a non-blocking
product-policy question rather than deciding unilaterally — recorded here
for completeness, not re-litigated.

### D — Second-attempt denial is a hard refusal, never a silent no-op or a fresh grant (chosen)

The issue's own worked example states plainly: "A second extension attempt
is refused." AC4a implements this as exit 3 + `reason_code:
already_extended`, matching `_slot_denied`'s existing exit-3-plus-
`reason_code` shape (`bin/nightshift-ctl:875-877`) rather than either (a) a
silent idempotent success (would let repeated `task extend` calls
compound unboundedly if the check were ever weakened) or (b) treating a
task whose extended deadline has itself lapsed as newly eligible (the exact
failure mode the issue's "Definition of finished" rules out — see AC7).

## Decision

Options A1, A2, C, D. Add `tasks.extension_seconds`/`extension_reason`/
`extended_at` (additive, nullable, excluded from `TASK_FIELDS`). Add a new
`task extend --id --seconds --reason` CLI verb implementing AC0–AC9 exactly
as frozen in the DoR verdict:

- **AC2/AC3** (value-shape): `--seconds` reuses `_is_valid_budget_seconds`
  directly (finite AND > 0); `--reason` required non-empty after stripping.
  Both checked before any transaction opens — usage error, exit 2, writes
  nothing.
- **AC4** (state-dependent denials, ordered AC4c → AC4b → AC4a): unknown
  task → exit 3 (`cmd_task_show`'s existing "unknown task" shape); current
  `reason_code` not exactly `"budget_expired"` → exit 3,
  `reason_code=not_budget_expired`; `extension_seconds IS NOT NULL` already
  → exit 3, `reason_code=already_extended`, regardless of whether that
  prior extension has itself since expired (AC7).
- **AC6** (effective budget, the amendment to ADR-0032/ADR-0045's
  precedence table row 1b): the expiry test becomes `now() >=
  started_at + budget_seconds + effective_extension`, where
  `effective_extension = extension_seconds if
  _is_valid_budget_seconds(extension_seconds) else 0` — the identical
  damaged-value guard `budget_seconds` itself already gets, applied a
  second time to the new column.
- **AC9**: `cmd_task_extend`'s own success payload surfaces
  `extension_seconds`/`extension_reason`/`extended_at`/
  `effective_budget_seconds`; the three new columns are deliberately NOT
  added to `TASK_ROW_FIELDS`/`task show`/`task ready`'s generic output this
  round (genuinely open per the DoR verdict, AC9) — direct schema/DB
  inspection is this file's existing precedent for a durability AC
  (`TaskBudgetExpiryTest.test_budget_seconds_column_is_additive_and_nullable`).

No change to `deploy/policies/omnipods_policies.py`, `_derive_normal_phase`,
or either of #216's two `budget_expired` checkpoints: both already consume
`reason_code` by reference through the one shared function, so an extended
task simply stops reporting `budget_expired` until the extended deadline
also lapses, with zero code change on #216's side. This matches the issue's
own "Expected code seams" list (`bin/nightshift-ctl`, `tests/
test_task_graph.py` only).

## Consequences

**Easier:** a task doing genuinely final work when its budget lapses has
one narrow, audited way to keep going, without weakening #216's refusal
informally or reopening ADR-0032's dispatch-safety guarantee. **Harder:**
nothing new — this is additive to the existing read-time snapshot, with no
new write path outside `cmd_task_extend`'s own guarded transaction.
**Foreclosed:** a second extension, an automatic split/block/fail decision,
worker cancellation, and any UI/Kanban surface — all explicitly out of
scope per the issue and unreachable by this diff's file scope.

## Reversal trigger

If a future ticket needs more than one extension, or a policy-level ceiling
on `--seconds`, or a persisted history of denied attempts (Option B1
above), that is a new, explicit decision under a new ADR — never a silent
edit to this one. If issue #236 clears, any follow-up change to this seam
should get a real two-vendor dispatch before being treated as satisfying
the design-debate precondition; this ADR's single-author status is a
property of #236 being open on 2026-08-29, not a new norm for this crew
(same WA-5 reversal trigger as ADR-0043/0044/0045).

**Deciders:** crew_alpha lead, single-author under WA-5.
