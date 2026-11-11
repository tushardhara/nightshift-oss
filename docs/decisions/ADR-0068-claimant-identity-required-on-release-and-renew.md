# ADR-0068: `claim`/`slot`/`lease` release and renew require and verify caller identity
(+ addendum: shared-function task admission seeding, issue #488)

Date: 2026-09-02 · Status: accepted · Deciders: crew_alpha lead · Proposers: crew_alpha lead
(self-build under WA-5 — see Context; no independent second engineer proposal was obtainable)
Conversation: ac27ff1683cf4294bc58a9826e781dbd · Spec: issue #508 and issue #503 (PM review
contracts, both DoR PASS, posted as issue comments; folded into one PR per PM's own call); issue
#488 folded in same-session by program (see Addendum below), PM review contract posted on #488.
Requires `staff` review before merge (control-plane/policy-layer change) — reported to program,
not yet run as of this ADR's authoring.

## Context

DoD conditions D07 and D18 are FAIL (verified fresh against `origin/main@2d1813fe`, this turn,
via `python3 tests/dod_validate.py` in a clean detached-HEAD worktree — not the shared, dirty
`/srv/project` working tree). D07's RT-18/RT-19/RT-20 are unsatisfied because the dispatch/claim
layer never proves who is releasing or renewing a claim. D18 is blocked on F-1
(`docs/audit/FINDINGS.md`, still uncreated at HEAD) naming a merged PR + RT id for issue #503,
found by the #477 concurrency audit (`docs/audit/CONCURRENCY.md` Q1/Q3, reproduced end-to-end in
PR #502's `probe2.py`): `claim release`, `claim renew`, `slot release`, and `lease release` all
perform an identity-unverified state transition. `slot release`'s `--holder` and `lease
release`'s `--holder` are present but optional (silently skip the check when omitted); `claim
release`/`claim renew` don't even define an identity flag. Contrast `cmd_slot_heartbeat`
(bin/nightshift-ctl, `holder_matches()`), which ADR-0002 §4a made a required, verified check
specifically to close this exact class of gap for renewal — release was left behind.

Two GitHub issues cover this one root defect class from different entry points: #508 (D07,
claim-only) and #503 (F-1/D18, generalizes to slot+lease). `pm` ran Definition of Ready on both
this session (#503 had no `state:ready` label yet), posted two frozen review contracts as issue
comments, and made the fold call: **one PR, two issues, both cited as `Refs #<n>`** — kept as
separate issues because `docs/audit/FINDINGS.md`'s F-1 record and D18's check already name #503
by number.

**Why this ADR has one proposer, not two.** The normal `design-debate` procedure dispatches two
different-vendor engineers to propose independently. Both `qa` planning and the `eng_claude`/
`eng_codex` proposal dispatches were attempted this session and denied identically:
`sys_session_send`'s `args` object schema (`additionalProperties: false`, only `input`/
`purpose`/`model`/`cost_budget`/`file_ids`) structurally cannot carry `slot_id`/`task_id`/
`generation`, which the `worker_semaphore` policy (`deploy/policies/omnipods_policies.py`)
requires for any `COUNTED_AGENTS` dispatch (`qa`, `eng_claude`, `eng_codex`, `reviewer_*`).
Confirmed by nine isolated reproductions this turn (varying key names, args-as-string,
top-level-vs-nested placement, with/without `purpose`, pre-consumed vs fresh slot, a second
counted agent) — all denied identically, either by a client-side schema rejection
(`Additional properties are not allowed`) or the server-side guardrail
(`Denied by policy: args.slot_id must be a strict positive integer`). This is the already-filed,
already-diagnosed issue **#489** (root cause recorded as deployment lag in an external
Omnigent tool/policy layer, not a fixable defect in this repo — see `.omni/decisions.md`'s
2026-09-01 "Corrected #489's root cause" entry), still `OPEN` at the time of this ADR (checked
live via `gh issue view 489`), blocked from a second cutover attempt by the now-`CLOSED` #490.
This is squarely **WA-5** (`.omni/working-agreements.md` item 5): "A crew lead whose worker
dispatch is sterile builds under a slot it holds, and says so in the PR body. In-crew review and
in-crew QA did not happen... Independence is preserved by cross-crew review, which becomes
mandatory rather than customary." `pm` itself is reachable (not a counted agent, no slot
required) and already produced both frozen contracts and DoR verdicts under its own authority, so
WA-6 (which additionally waives PM sign-off) does not apply — PM's acceptance sign-off against
these criteria still happens normally, post-PR.

Given no second proposal was obtainable, this ADR is the crew lead's own analysis, informed
directly by `docs/audit/CONCURRENCY.md` Q1/Q3 (the exact reproduction and root-cause citations)
and PM's two contracts (which already specify the comparator, the scope split, the RT-id
allocation, and the RT-20/#518 rebinding call in detail) rather than a reconciliation between
two competing drafts.

## Options considered

### A — Mirror `holder_matches`/`cmd_slot_heartbeat`'s existing pattern exactly (chosen)
Add a required `--claimant` to `claim release`/`claim renew`; make `slot release`'s and `lease
release`'s existing `--holder` required; verify with the same `holder_matches()` predicate
already proven correct for `slot heartbeat` (accepts `<crew>/<agent>` or bare agent). One
comparator, one failure shape (exit 3, `ok:false`, names the real holder/claimant), reused
across four call sites. No new schema, no new column, no new CLI verb.

### B — A dedicated `--recovery` flag/verb for explicit recovery transitions
Considered because condition 7's text says "a second execution requires an explicit, recorded
recovery transition" — read literally, this could mean recovery needs its own marked CLI action
distinct from an ordinary claim acquire. Rejected: `cmd_claim_acquire`'s existing
`COALESCE(MAX(generation),0)+1` already records a durable, monotonic, auditable transition every
time a new claim is legitimately granted (visible via `claim show`), and gating it behind an
identity-verified release (Option A) is what makes that increment *authorized* rather than
racy — the generation counter was never the missing piece; the missing piece was proving the
release that makes way for it. A new verb would duplicate state (two ways to say "this is a
recovery") without adding a property the fix doesn't already deliver, and PM's contract (#508
AC7 / the RT-20 binding decision) reached the same conclusion independently.

### C — HMAC-signed capability tokens for claimant/holder identity (rejected early)
Considered and dropped without a full writeup: this control plane is a single trusted SQLite
database behind `bin/nightshift-ctl`, written only by seats already inside the org's trust
boundary (`EXEMPT_AGENTS`/`COUNTED_AGENTS`), not a multi-tenant or internet-facing surface. A
signed-token scheme solves spoofing across a trust boundary that doesn't exist here and adds a
key-management problem this codebase has nowhere to put. `holder_matches`'s plain string
comparison is the same trust model already accepted for `slot heartbeat`, `slot consume`, and
every `--holder`/`--claimant` check that already exists; Option A's job is closing the *missing*
checks, not raising the bar on the ones already there.

## Decision

Implement PM's two frozen contracts as written (issue #508 comment, issue #503 comment), Option
A throughout:

1. `claim release` / `claim renew` gain a required `--claimant`; `cmd_claim_release`/
   `cmd_claim_renew` verify it against `task_claims.claimant` via `holder_matches()` before
   mutating anything. Wrong claimant: exit 3, names the real claimant, releases/renews nothing.
2. `slot release`'s `--holder` and `lease release`'s `--holder` become `required=True`;
   `cmd_slot_release`/`cmd_lease_release` verify unconditionally via `holder_matches()` (removing
   `lease release`'s `if args.holder and ...` short-circuit, which is what let the check be
   skipped by omission).
3. RT-18 (dispatch onto a task with a foreign active claim is denied), RT-19 (non-holder cannot
   release/renew a claim; claim-scoped only, per #508 AC6), and the "sequential re-execution
   without a recorded recovery generation is refused; with one it's permitted and the generation
   is recorded" property become real test classes.

   **Correction, same session, before this PR opened:** this ADR originally planned to rebind
   `RT-20` itself onto the new recovery-generation test, "retiring #518 by elimination." That plan
   is now known wrong: `staff` independently and concurrently ran the dedicated, authoritative
   decision task for issue #518 (`518-rt20-binding-decision`, `.omni/ledger.md` 2026-09-02, "PR
   #526 opened, review routed") and ruled **"option (b) split"** -- `RT-20` narrows to
   `test_fencing.py`'s `FencingMigrationTest` (id unchanged, its own baseline re-capture), and a
   NEW id, `RT-31`, is minted for `MigrationConcurrencyTest` (first multi-symbol binding, queued
   behind lease 81 for issue #509's re-dispatch -- not this task's scope). Neither binding is
   retired; both stay alive under staff's own ruling. Overriding a `DECIDED`, dedicated ruling on
   the exact same issue number with this ADR's own unilateral call would itself be the kind of
   namespace collision WA-007 exists to prevent. This PR therefore leaves `RT-20`'s manifest entry
   **untouched** (staff's own item to land) and allocates a fresh id, **RT-34**, for the new
   recovery-generation test class instead -- re-checked immediately before use: `RT-24`..`RT-30`
   are occupied (staff's own finding, corrected point 4 below), `RT-31` is staff's, `RT-32`/`RT-33`
   are this same PR's other two new ids (points 4 and the #488 addendum), so `RT-34` is the first
   genuinely free id. Class named `RT34RecoveryTransitionRequiredForSequentialReexecution`.
4. RT-32 (see correction below) is the #503 regression: reproduces the exact
   two-simultaneous-`ALLOW` scenario from `docs/audit/CONCURRENCY.md` Q1/Q3 end-to-end, asserts it
   is now refused. Bound to condition 18 in the manifest (condition 18 doesn't enumerate RT ids by
   name in DEFINITION-OF-DONE.md, so no changelog row is needed there).

   **Correction, same session, before this PR opened:** this ADR originally allocated `RT-24` here
   and claimed it had been "re-checked immediately before use per WA-007" -- that check was not
   thorough enough. A fresh, independent re-check this turn found `docs/audit/baseline/RT-24.json`/
   `RT-24.txt` already committed on `main` (commit `8cd0f88`) for an unrelated, older defect
   (`test_beat.ControlPlaneUnreadableTest`, control-plane readability) -- retired from
   `docs/prd/DOD-MANIFEST.json`'s current `rt_ids` map but never actually free, per `staff`'s own
   concurrent, authoritative finding on `518-rt20-binding-decision` (`.omni/ledger.md`, 2026-09-02
   "PR #526 opened, review routed"): "real captures already occupy 24-30, unfolded" -- which is why
   staff itself took `RT-31`, not `RT-24`, for `MigrationConcurrencyTest`. Renumbered here to
   **RT-32** (the first id after staff's own `RT-31`, allocated the same session) to avoid a second
   WA-007 collision on top of the one just found. Class renamed
   `RT32UnauthenticatedReleaseCannotDuplicateDispatch`; no behavioural test content changed, only
   the id and its docstring/fixture-string cosmetics.
5. `docs/prd/DOD-MANIFEST.json`'s `rt_ids.RT-18`/`RT-19` move off `test_node_id: null` to real
   `file:symbol` bindings with fresh baseline captures; `RT-32` and `RT-34` are added the same way
   (each a fresh baseline capture under its own name -- the stale `RT-24.json`/`RT-24.txt` files
   are left untouched on their own historical commit, not overwritten). `RT-20` is **not** touched
   by this PR (see point 3's correction -- that entry is staff's own item). `docs/audit/
   FINDINGS.md`'s F-1 disposition is updated to name this PR and RT-32 once it exists (created
   fresh — issue #482, which was to create it, is closed and merged to `origin/main`, but this
   local worktree is cut from `2d1813fe` which already has it on `origin/main`; verified present at
   that SHA before editing).

## Consequences

**Easier:** D07 and D18 both go PASS from one coherent fix; every future `--holder`/`--claimant`
release/renew call site has exactly one established pattern to copy, the same one `slot heartbeat`
already set. Issue #518 is NOT retired by this PR (corrected from this ADR's original plan --
staff's own concurrent, dedicated ruling on #518 stays authoritative and untouched here; see point
3's correction).

**Harder:** this PR was built single-author under WA-5 — no in-crew adversarial proposal, no
in-crew code review, no in-crew QA authored the tests independently of the implementer. Cross-crew
review is mandatory here, not optional, and carries more weight than usual as the only outside
check before merge. `pm`'s acceptance sign-off still runs normally against the two frozen
contracts' numbered criteria.

**Forecloses:** a future contract-negotiated identity format other than `<crew>/<agent>` string
equality for claimant/holder — anyone wanting a stronger identity primitive (signed tokens,
per-session nonces) starts from Option C's rejection here and has to argue the trust boundary
changed, not just that this fix was incomplete.

## Reversal trigger

Revisit Option A if either becomes true: (1) a future audit demonstrates `holder_matches`'s plain
string comparison is spoofable within the org's actual trust boundary (not a hypothetical
multi-tenant one) — e.g., two seats can legitimately produce the same `<crew>/<agent>` string
without being the same caller; or (2) issue #489 resolves (`sys_session_send` can carry
`slot_id`/`task_id`/`generation` for counted-agent dispatch again) and a subsequent finding on
this same code shows a genuine two-vendor debate would have caught something this single-author
build missed — in that case, re-open with a real second proposal rather than amending this ADR in
place.

## Addendum: issue #488 (shared-function task admission cannot reach continuation)

Date: 2026-09-02 · folded into this PR by program (heartbeat 591a6445, run_id fixture-review-231),
same file/lease already held (one serialize cycle, not two). Spec: `pm` review contract posted on
issue #488 (frozen before the first implementation commit).

**Context.** `crew_beta`'s cross-crew review of PR #526 (`product/491-dod-validator-prose`)
could not run its two unconditionally-required domains (`beta_reviewer_codex` code-review,
`beta_qa`): `nightshift-ctl slot acquire`/`claim acquire` denied both with `"only the task
implementer may request NEW"` against every task id tried, because `491-dod-validator-prose`
(charted via `nightshift-ctl task upsert`, Program's path for shared-function seats — product,
staff, platform, security, release) has zero `task_admissions` rows: that row is written in
exactly one place, `worker_semaphore`'s `phase=='new'` branch
(`deploy/policies/omnipods_policies.py:178-440`), reachable only through a genuine counted `eng_*`
NEW dispatch — and none of the five shared-function seat names is a `COUNTED_AGENTS` key, so none
can ever earn one. This blocks cross-crew code-review/QA for ANY shared-function-authored PR, not
just #526 — confirmed by 9 independent diagnostic attempts (`.omni/decisions.md`, 2026-09-02
~00:50Z) and posted as a comment on #488 itself.

**Two design corrections found by `pm`, re-reading the dispatch code directly rather than the
row-existence path alone (both confirmed independently by the crew lead against the same line
numbers before building):**
1. Both `_derive_normal_phase` and `worker_semaphore`'s continuation branch deny continuation
   dispatch outright unless `task["state"] in {"building","ready","fixing"}`, independent of
   admission — seeding a row for a still-`"pending"` task would write evidence nothing can use yet.
2. `qa`/`beta_qa` continuation requires `task["crew"] == expected_crew` (the *dispatched* QA
   agent's own crew); `review`-continuation has no such check. A shared-function task's `crew`
   (e.g. `"product"`) can never equal `"crew_alpha"`/`"crew_beta"`, so this fix unblocks
   `reviewer_*`/`beta_reviewer_*` only — `qa`/`beta_qa` continuation for such tasks stays denied
   either way, admission row or not. Filed separately as **issue #527** (`needs-dor`, explicitly
   non-blocking here) rather than silently patched — relaxing a same-crew invariant for QA is its
   own architecture question, not a corollary of the admission-row gap.

### Decision

Add `nightshift-ctl task upsert --seed-shared-admission` (opt-in, default off — AC1): given an
**already-existing** task (AC2: `task_missing` denial, no insert of any kind, if it doesn't) whose
`crew` is in a new `SHARED_FUNCTION_CREWS = frozenset({'product','staff','platform','security',
'release'})` (AC3: any other crew, explicitly including `crew_alpha`/`crew_beta`, is refused with
a named reason, never silently ignored) and whose `state` is already `building`/`ready`/`fixing`
(AC4: denied otherwise, and this command never transitions state itself — that stays the caller's
job via the existing `--state` flag), write the missing `task_admissions` row. No schema
migration: `task_admissions.new_slot_id` stays `NOT NULL UNIQUE`, satisfied by inserting a real
`worker_slots` row of a new `kind='seed'` (no `CHECK` constraint on `kind`), written `state=
'released'` **directly at insert** (never `'active'` then updated) so it can never be picked up by
any `state='active'` admission/budget check, `generation=0` (the column's own default, proven
inert against `_next_slot_generation`'s `COALESCE(MAX(generation),0)+1` — AC7), then the
`task_admissions` row pointing at that seed slot's id. Idempotent (AC5): a `task_admissions` row
already present is a no-op, `{"ok": true, "created": false, ...}`, never a second `INSERT` against
the table's own `PRIMARY KEY`. Rejected alternatives: a schema migration to make `new_slot_id`
nullable (unnecessary risk/blast-radius for a property a synthetic-but-real audit row already
satisfies); relaxing `worker_semaphore`'s continuation gate to accept `admission is None` for
shared-function crews directly (would also have to duplicate or bypass the role-specific checks
that already run *after* that gate, widening the policy's own surface instead of supplying the one
fact it is missing).

### Verification

`tests/test_admission_gate.py::SharedFunctionAdmissionSeed` (9 cases: missing-task denial,
crew_alpha/crew_beta refusal, wrong-state denial, idempotency, generation inertness, budget/
active-slot-count inertness, `selftest` tripwire) plus **RT-33** (DoD condition 18/#488,
`test_reviewer_continuation_succeeds_qa_continuation_still_denied`): proves, against a seeded
shared-function task, that `reviewer_codex`/`beta_reviewer_codex` continuation now succeeds AND
that `qa`/`beta_qa` continuation still fails on the pre-existing crew-mismatch reason — the #527
limitation is a passing, documented assertion, not a silent gap.

### Consequences

**Easier:** cross-crew code-review becomes admissible for any shared-function-authored PR with one
explicit, auditable, opt-in call — no schema change, no hand-written DB row, no live dispatch
round-trip. **Harder:** `qa`/`beta_qa` cross-crew coverage for such PRs stays blocked until #527 is
decided — reviewers taking on that PR's QA domain today do so via their own reviewer-side
practical-usage check, not a counted `qa` dispatch, same as before this fix. **Forecloses:**
treating "a `task_admissions` row exists" as proof of a genuine engineer build — it is not, for
`kind='seed'` rows, and any future code that branches on admission presence to infer *how* a task
was built (not just *that* continuation is now legal) must check `worker_slots.kind` first.

### Reversal trigger (addendum)

Revisit if a future audit finds `kind='seed'` rows are NOT provably inert against some admission/
budget/selftest check this ADR didn't enumerate, or if #527 is decided in a way that requires this
command's shape to change (e.g. a `qa`-continuation carve-out that needs its own opt-in flag here
rather than a separate mechanism).

### Round-1 review response (commit `63bc644`)

`security` (`security-530`) and `crew_beta` (`review-530`) independently found the same real
defect, both with a working PoC: `task upsert --id T --crew <shared-fn> --seed-shared-admission`
in ONE call let `cmd_task_upsert`'s own `TASK_FIELDS` `UPDATE` commit `crew` before
`_seed_shared_admission` re-read the row — a real `crew_alpha`/`crew_beta` task could forge
shared-function admission, then flip `crew` back in a second call, fully bypassing AC3's refusal.
Fixed by refusing `--seed-shared-admission` outright whenever the same call also requests any
other `TASK_FIELDS` change — it now only ever reads a row an *earlier, already-committed* call
produced. Verified against both reviewers' exact PoCs in disposable scratch DBs. This also
resolves `staff`'s adjacent follow-up (denial paths silently committing a concurrent field edit):
`fields` is now always empty by the time either denial check runs.

`crew_beta`'s MERGE_BLOCKER 2 (`--seed-shared-admission` denies the real `491-dod-validator-prose`
task, state `review-clean`) resolved without a code change: `review-clean` was never in this
command's (or the pre-existing continuation gate's) valid `{building,ready,fixing}` set — program
confirmed this was its own invented value and corrected the task to `state=building` directly.
Re-verified end-to-end against a scratch copy of the real control-plane DB: `--seed-shared-admission`
alone now succeeds, and a subsequent `beta_reviewer_codex --purpose review` slot acquire against
that task grants (`continuation_kind=review`).

`crew_beta`'s MERGE_BLOCKER 1 (PR body's "D07 FAIL→PASS" claim) is disputed: a fresh `git clone`
straight from GitHub (never `/srv/project`'s local checkout) confirms `139726e830...` (RT-20's
baseline_sha) IS an ancestor of this PR's own head, and `python3 tests/dod_validate.py` there
reports `D07 PASS` — matching `staff`'s own, independently-reached conclusion from a separately
fetched worktree. `/srv/project`'s shared local checkout has itself become a shallow clone (`git
rev-parse --is-shallow-repository` → `true`); any `git worktree add` cut from it inherits that
shallow boundary and produces this exact false "not an ancestor" negative. Program has already
noted this for platform/oncall as a watch item. Not a defect in this PR; not fixed here (out of
scope, not `bin/nightshift-ctl`).

`claim renew --task program-driver --ttl "$ttl"` (R-26/R-26 AMENDMENT, `.omni/decisions.md`)
breaking against this PR's new required `--claimant` is real, confirmed independently by both
`crew_beta` and `staff`. The corrected idiom (`--claimant program`, reusing the identity string
`cmd_claim_acquire` already special-cases for ADR-0044 §3b) is recorded as a new, append-only
decision (`.omni/decisions.md`, "R-26 AMENDMENT 2") rather than a code change — the actual caller
is program's own scheduled-task/agent-bundle prompt, outside this repo (R-26's own "Durable half,
flagged not absorbed" note). This cannot be fixed from `bin/nightshift-ctl`; it requires program to
adopt the corrected flag before or immediately upon this PR merging, flagged explicitly in the PR
report.
