# ADR-0040: Compute review eligibility by recomputing scope/seams live from Git, no new schema

Date: 2026-08-28 · Status: accepted · Deciders: crew_alpha lead · Proposers: crew_alpha lead (single-author, see §Context)
Conversation: crew_alpha lead session, run `fixture-review-231` · Spec: issue #232 DoR judgment, https://github.com/tushardhara/nightshift/issues/232#issuecomment-5452154503

## Context

Issue #232 (#228C) needs a `review eligibility` capability: given an exact
`(task_id, head_sha)`, decide whether every REQUIRED review domain (the frozen
per-work-item contract, never #228A's `suggested_domains`) is satisfied either by a
fresh receipt at this head or by a validly REUSED receipt from a prior head, and
emit one deterministic aggregate digest. #228A (ADR-0036/0038, shadow risk plan) and
#231 (ADR-0039, persisted `review_receipts`/`review_findings`/
`review_dispatch_markers`) are both merged at checkpoint 3
(`4243d1b838af79eac311d60269f9ce1e14bd0a66`). The pm's DoR judgment on #232
(READY, AC-1..AC-8) is this item's criteria and is not restated in full here; this
ADR covers the design questions it deliberately left open plus the ones a build
needs answered mechanically.

**Single-author flag.** `design-debate`'s normal procedure puts two independent,
different-vendor engineer proposals in front of the lead before it reconciles and
writes this document. That step could not run: `eng_claude`/`eng_codex` (and `qa`,
`reviewer_claude`, `reviewer_codex`) are all `COUNTED_AGENTS` and every
`sys_session_send` dispatch to a counted agent is currently rejected — either by the
tool's own JSON schema (`args`/top-level both reject `slot_id`/`task_id`/
`generation` with "Additional properties are not allowed") or, once those fields are
omitted, by `deploy/policies/omnipods_policies.py`'s `worker_semaphore` policy
demanding exactly those fields. Reproduced independently this session (three
argument shapes, all denied) and already on record as a standing org-wide defect
(`.omni/decisions.md`, entries referencing issue `#236`, dated the same day/run).
No independent second opinion was obtained for the options below. Flagged in
`STATUS.md` per the `design-debate` skill's own instruction for this situation.
This ADR is not a decision to implement without review — the required-domains
checklist below still routes different-vendor code review through whatever the
program can reach (`architect`, once #236 clears or the program authorizes a
substitute), same as #231 did.

## Options considered

### A — Recompute scope, blob OIDs, and dependency-seam classification live from Git on every eligibility call; no new schema (this ADR's decision)

What it is: `review eligibility` takes `(task_id, head_sha, --contract <path>)`,
same shape as #231's `review consolidate --contract`. For each domain required by
the frozen contract (`nightshift_review_receipts.parse_required_domains`), it looks
for (a) a receipt at exactly `head_sha` with `verdict='pass'` and zero
`MERGE_BLOCKER` findings (AC-4e — direct satisfaction), or failing that (b) any
prior receipt for that domain, at any head H1 this task has one for, that survives
a fresh AC-2(a)-(d) reuse check computed entirely from two live `git diff`s
(`base_sha..H1` and `base_sha..head_sha`) plus the CURRENTLY COMMITTED
`program/review-risk.json` ruleset — reusing `bin/nightshift_review_plan.py`'s
existing `_changes`/`_matches`/`_blob_oid`/`_target` machinery rather than adding a
parallel implementation. No new table, no migration, no cache. `review_receipts`,
`review_findings`, `review_dispatch_markers` (#231's schema) are read-only inputs;
nothing about their primary keys or validation matrix changes.

Files touched: `bin/nightshift_review_receipts.py` (new pure functions:
`path_domain_scope`, `reuse_eligible`, `compute_eligibility`), `bin/nightshift-ctl`
(new `review eligibility` subcommand + its `cmd_review_eligibility`), no schema
change.

Migration/rollout: none — additive CLI verb, no DB write path at all (eligibility
is pure read, matching #231's `consolidate`'s own "never touches GitHub or a
subprocess" discipline, extended here to "and never writes to `control.db`
either").

Deliberately NOT done: no caching of blob-OID/scope computations across calls, no
persistence of which head a domain was satisfied from (the eligibility digest
itself is the record of that, per AC-5 — recomputed on demand, never trusted
stale).

Two biggest risks: (1) recomputing two full diffs plus per-path classification on
every call is O(diff size) work repeated per eligibility check — acceptable given
#228A's own `review plan` already pays this cost once per head and eligibility is
called far less often than plans are generated; if this becomes a real bottleneck,
an additive cache is the natural follow-up, explicitly deferred per the DoR's own
ambiguity-resolution #2. (2) the domain→scope mapping (below) is a new derived
artifact reverse-engineered from `_classify`'s inline trigger logic rather than a
single shared function — a change to `_classify`'s trigger rules could drift out of
sync with `path_domain_scope` if only one side is updated. Mitigated by a
conformance test that runs both `_classify` and `path_domain_scope` over the same
fixture diff and asserts the extra-domains they derive agree.

Rejected alternative considered and set aside: teach `_classify` itself to return
a `path -> triggered_domains` map and have both `review plan` and `review
eligibility` call the same function. Correct long-term shape, but touches
#228A's already-shipped, already-reviewed `_classify` return contract
(`bin/nightshift_review_plan.py`, consumed by `review plan`'s existing output
shape) — a change there is a bigger, riskier diff than #232's own scope justifies,
and #228A's `suggested_domains`/`suggested_plan` contract is explicitly frozen
(“shadow-only authority” per ADR-0036, and this DoR's own out-of-scope list bars
changing #228A's CLI surface beyond what's needed to read plan/classification
data). Named here as the reversal trigger below, not silently dropped.

### B — Persist covered-paths/blob-OID/seam snapshots into new additive tables at receipt-record time (rejected)

What it is: extend `review_receipts` (additively — a new sibling table, e.g.
`review_receipt_scope(task_id, head_sha, domain, path, blob_oid, groups_json)`,
never touching #231's existing columns/PK) so `review eligibility` becomes a pure
DB read with no live `git diff` at check time.

Why it loses: the DoR's own ambiguity-resolution #2 already worked this out and
set the default the other way — `base_sha`/`head_sha` are real, immutable,
still-reachable commits (no-force-push is a standing invariant per ADR-0026), so
this data is always exactly reconstructable from Git; a persisted snapshot is a
second source of truth for something Git already answers authoritatively, and it
would need its own migration-sequence allocation and lease on `bin/nightshift-ctl`
(already held for this task, but the schema footprint is the thing to avoid when
the read-live path is just as correct and strictly less to build, test, and keep in
sync). Performance is the only real argument for B, and nothing in the DoR's AC-1..
AC-8 requires eligibility to be fast — it requires it to be *correct* and
*deterministic*. Kept as the explicit fallback if live recomputation turns out too
slow in practice (see Option A's risk (1)).

### C — Treat every domain's covered scope as "the whole diff" (no per-path scoping at all) (rejected early)

What it is: skip AC-2(c)/(d)'s per-path blob-OID and seam-classification checks
entirely; a receipt reuses onto a new head only if the ENTIRE diff (`base..H1` vs
`base..H2`) is byte-identical.

Why it loses: this satisfies AC-1/AC-3/AC-6/AC-7/AC-8 but makes AC-2 vacuous in
exactly the case #232 exists to handle — an unrelated file changing anywhere in the
repo would invalidate every domain's every receipt, which is functionally "never
reuse anything," defeating the entire point of the item (the PM's own framing:
"reuse only clean semantic receipts... unchanged"). Rejected without needing a
second opinion to see it fails AC-2 by construction.

## Decision

Option A. Live recomputation from Git and the currently-committed ruleset, no new
schema, reusing `bin/nightshift_review_plan.py`'s existing diff/classification
primitives rather than duplicating them.

**Domain → covered-scope mapping** (mechanical translation of
`bin/nightshift_review_plan.py`'s `_classify`, per the DoR's ambiguity-resolution
#3 — "a path's dependency seam = the set of `program/review-risk.json` rule-groups
it matches under the currently committed ruleset"):

| Domain (receipt schema name) | Covered scope |
|---|---|
| `pm_acceptance` | every path in the diff |
| `code_review` | every path in the diff |
| `cross_crew_review` | every path in the diff (mirrors `_classify`: added whenever `profile == HIGH_RISK`, i.e. whenever anything below is non-empty) |
| `security` | paths whose `high_risk_globs` matches include `control_plane`, `workflow`, or `security_path`, OR that match a `sensitive_terms` entry in `content_scan_globs`-matched content |
| `staff_adr` | paths whose `high_risk_globs` matches include `constitution`, `control_plane`, `schema_migration`, `dependency`, or `public_contract` |
| `qa` | paths matching `conditional_globs["qa"]` |
| `ux` | paths matching `conditional_globs["ux"]` |

**Direct satisfaction (AC-4e):** a domain is satisfied at `head_sha` directly when a
`review_receipts` row exists for `(task_id, head_sha, domain)` with `verdict='pass'`
and zero `review_findings` rows of severity `MERGE_BLOCKER` for that key
(`PRE_DEPLOYMENT`/`FOLLOW_UP` findings do not block direct satisfaction).

**Reuse satisfaction (AC-2):** a domain is satisfied via reuse from a prior head H1
when a `review_receipts` row exists for `(task_id, H1, domain)` with
`verdict='pass'` AND **zero** `review_findings` rows of ANY severity for that key
(stricter than direct — any finding at all blocks reuse), AND every path in that
domain's covered scope at `head_sha` (computed against `base_sha..head_sha`) has an
identical blob OID at `H1` and at `head_sha` (`git rev-parse H1:<path>` ==
`git rev-parse head_sha:<path>`; a path absent at one side is a mismatch), AND the
set of triggered rule-groups for every such path is identical when the same path's
presence is evaluated against both diffs under today's ruleset.

**Never reused, always fresh (AC-3):** CI status, provenance/authorship,
PR-body/authorization, and live/runtime evidence are not modeled by this ADR at
all — `review eligibility` never calls GitHub or any live check; it is a pure read
over `control.db` plus local `git` — so there is no reuse path for these categories
to accidentally create.

**Fail-closed matrix (AC-6):** unknown task / unresolvable head / non-descendant
head / a `control.db` read error → a distinct non-domain `reason` (`task_missing`,
`head_invalid`, `head_not_descendant`, `control_db_unreadable`), never a fabricated
domain list. A required domain satisfied by neither path → its canonical domain
name appears in `missing_domains`, and only domains actually unsatisfied appear
there. Exit code 3 on any fail-closed branch, exit 0 with `eligible` in the JSON
body otherwise — matching #228A/#231's convention exactly.

**Empty states:** H1==H2 (AC-7) runs the identical direct/reuse logic (the
`head_sha==head_sha` case trivially satisfies AC-2's blob-OID/seam checks for every
path, so this is not special-cased in code, only exercised by a dedicated test). No
receipts at all (AC-8) → `eligible=false`, `missing_domains` lists every required
domain, verified by a fresh-task test.

**Digest (AC-5):** `eligibility_digest = sha256(canonical_json(body))` where `body`
contains `task_id`, a freshly computed `plan_digest` (via
`nightshift_review_plan.build_plan(cx, task_id, repo, head_sha)`, unmodified — this
is what makes ruleset drift visible, since `plan_digest` is a function of the
currently committed `program/review-risk.json`), exact `head_sha`/`head_tree_oid`,
`eligible`, and one entry per required domain: `{domain, satisfied_by: "direct" |
"reused", head_sha (of the satisfying receipt), verdict}`. `canonical_json` is the
same `sort_keys=True, separators=(",", ":")` discipline #228A/#231 already use.
The exact wire shape for #233's eventual consumption is explicitly not frozen here,
per the DoR's own ambiguity-resolution #4.

## Consequences

Makes easy: adding eligibility without touching #231's schema or #228A's shadow-only
plan contract at all — this is a pure new read path layered on two already-shipped,
already-reviewed primitives. Auditable: every eligibility answer is fully
reconstructible from Git plus `control.db`'s existing rows, nothing new to trust.

Makes hard: performance at scale (Option A's risk (1) — mitigated by Option B being
a named, ready fallback if it's ever needed) and keeping `path_domain_scope` in sync
with `_classify` by hand (mitigated by the cross-check conformance test named
above).

Forecloses, for now: any caching of scope/OID computations across calls. Revisit
under Option B if profiling ever shows this matters — not a blocker for #232's own
correctness criteria.

## Reversal trigger

(1) If `_classify` in `bin/nightshift_review_plan.py` is ever reworked to expose a
shared `path -> triggered_domains` map as part of its own return contract (not just
aggregated `suggested_domains`), `path_domain_scope` should be deleted and both
`review plan` and `review eligibility` should call the shared function instead —
Option A's rejected-early alternative becomes correct once that refactor happens on
its own schedule. (2) If profiling on real task history shows live per-call
recomputation is materially too slow, revisit Option B (additive scope-snapshot
table) rather than hand-optimizing the live path further. (3) If a future session
demonstrates a successful `COUNTED_AGENTS` dispatch (see `.omni/decisions.md`,
issue #236), re-run this ADR's design phase properly with an independent
`eng_claude`/`eng_codex` proposal and either ratify or supersede this decision with
that second opinion folded in — this ADR is accepted on the strength of its own
reasoning, not because two vendors agreed, and that gap should be closed the moment
it's possible to close it.

## Addendum — PM-acceptance round 1 finding, fixed same PR/branch

`pm`'s acceptance pass (task `232-acceptance`, EXEMPT-agent dispatch, unaffected by
#236) refused sign-off and found a real gap in the ORIGINAL version of this ADR's
Decision: AC-2(d)/AC-4(b)/AC-6's "ruleset drift invalidates" requirement was
restated correctly in prose but never actually implemented. The original code
computed the CURRENT ruleset once per call and used that single snapshot to
classify both the H1-diff and the H2-diff, so the "identical seam classification"
comparison was tautologically true whenever blob OIDs matched — it structurally
could not detect that the ruleset itself had changed between when a receipt was
validated and when eligibility later relies on it, because nothing in #231's schema
recorded which ruleset was current at validation time. `plan_digest` was computed
and put in the output but never compared against anything either.

**Fix, additive, same PR:** `review_receipts` gains one nullable column,
`rules_digest` (migration seq 2, allocated under the `bin/nightshift-ctl` lease
this task already holds — no new lease, no PK change, no rewrite of existing rows).
`review receipt-record` stamps it with the currently-committed ruleset's digest
(`nightshift_review_plan._bundle_rules()`'s own `rules_digest`, the same value
`review plan` already exposes) at validation time — computed and written entirely
from `bin/nightshift-ctl`'s side, so `bin/nightshift_review_receipts.py`'s own
frozen AC-5 boundary (never imports the planner) is untouched. `review eligibility`
now compares a candidate receipt's stored `rules_digest` against a freshly computed
one for BOTH direct and reused satisfaction; a NULL (pre-migration row, or a
best-effort record-time failure to prove bundle identity) reads identically to a
real mismatch — fail closed, never guessed as "no drift." This also let the
per-path seam-classification comparison in the original `reuse_eligible` be
DELETED as redundant: once `rules_digest` equality proves the whole ruleset is
byte-identical, per-path classification of any content-identical path is
trivially identical too, and when `rules_digest` differs the receipt is already
rejected before reaching that comparison. Net simplification, not just a fix.

Tests added: stale-digest blocks direct satisfaction, stale-digest blocks reuse
even when the domain's diff-scope is otherwise vacuous, NULL/pre-migration
treated as drift, and the output `rules_digest` field is reproducible. Full
regression: `test_review_receipts` (56, unaffected — `cmd_review_receipt_record`'s
existing behavior for every OTHER column is unchanged), `test_shadow_risk_plan`
(17), `test_review_eligibility` (15, was 11), plus a broader control-plane sweep —
207 tests total, all green, no regressions.

This did not change the Decision in §Decision above (Option A, live recomputation,
no new schema for *scope/blob-OID* data) — it completes it. Ruleset-drift detection
specifically needed one narrow, additive column because it is the one piece of
"what was true at validation time" that Git itself cannot answer after the fact
(unlike scope/blob-OIDs, which base_sha/head_sha make exactly reconstructable).
Recorded here rather than as a new ADR number because this PR has not merged —
same discipline #231's own round-1 fixes used on its own branch before merge.
