# ADR-0052: A mechanical, fail-closed classifier decides bookkeeping-only vs normal; `release` still does everything else

Date: 2026-08-30 · Status: proposed
Deciders: crew_alpha (implementer, single-author under disclosed WA-5/#236),
staff (gate, constitution/control_plane paths touched)
Spec: GitHub issue #286; DoR `.omni/reports/2026-08-30-286-dor-verdict.md`;
frozen contract `docs/reviews/286-bookkeeping-fast-path-CONTRACT.md` v1
Depends on: ADR-0036 (shadow risk plan — same module-reads/`nightshift-ctl`-
writes split), ADR-0039 (persist review receipts — same append-only receipt-
table idiom), ADR-0042 (suggested_domains gates fanout dispatch, not
eligibility — the same shape this ADR reuses one layer down: a diagnostic
primitive a separate, program-routed consumer decides whether to act on)

## ELI5

Issue #283 was a PR that only touched status/ledger/report files, and it
still took ~20 minutes because Nightshift ran it through the same pipeline
as a code change: scratch merge candidate, full test suite, wait for CI,
merge, re-test main. This ADR adds one new, narrow question Nightshift can
ask about any PR head: "does every changed byte fall inside four specific
bookkeeping paths, in a shape that couldn't hide anything else?" If yes, and
only if yes, a small bundle of cheap, deterministic checks runs instead of
the full pipeline. Every other PR — including any PR this question can't
answer with full confidence — gets the full pipeline, unchanged.

## Context

Engineer/PM/QA/reviewer dispatch is sterile this run (issue #236, WA-5,
confirmed open again this heartbeat via `gh issue view 236`). This ADR is
authored directly by the crew_alpha lead, per the crew's own charter and
working-agreements.md rules 5/6. The debate this ADR would otherwise owe two
independent engineers is recorded below as an explicit two-option call per
decision, by the same author, mirroring what a second vendor would most
plausibly have pushed back on — the same substitute ADR-0039 and ADR-0051
used under the identical constraint.

The charter scopes this issue to Seam A: `bin/nightshift-ctl`,
`deploy/policies/**`, `docs/CONTROL-PLANE.md` — and separately states
`release` "is not your crew's sub-agent and not reachable from you
directly." The code that actually builds scratch candidates, runs the full
suite, waits for CI and merges is `release`'s own prompt,
`program/agents/release/config.yaml` — `program/**`, platform/program-owned,
single-writer per `docs/owners.md` ("no crew owns this... Changes require an
ADR"). Corrected 2026-09-02 per ADR-0067's DoR finding on issue #388: this
paragraph originally named `deploy/exact_sha_release.py` here. Direct read at
checkpoint `2d1813f` showed that file is the operator-triggered cutover tool
(`prepare`/`authorize`/`cutover`/`rollback`, requires its SHA to already be
merged to `main`) with no test-runner or `gh pr merge` call anywhere in it —
not release's merge procedure. This paragraph's conclusion is unchanged by
the correction: either way, the actual candidate-build/full-suite/merge
procedure sits outside this issue's Seam A charter and outside a seam this
crew can edit directly. So this issue cannot be "make release fast for
bookkeeping PRs" in the sense of editing release's own procedure — it has to
be "build the exact-SHA, fail-closed decision primitive release's procedure
would consult," the same relationship `bin/nightshift_review_plan.py`'s
`suggested_domains` already has to every crew's own dispatch procedure
(ADR-0042: the plan is diagnostic, the consuming procedure decides whether to
act on it).

## Options considered

### A — Reuse `program/review-risk.json`'s allowlist mechanism (rejected)

`review-risk.json` already has a `fast_allowlist: ["docs/**"]` key and a
whole ownership/glob-rule engine (`bin/nightshift_review_plan.py`'s
`_classify`). The obvious-looking move is to add a `bookkeeping_allowlist`
key there and reuse `_matches`/`_owner`. Rejected for two reasons, not one:
(1) `program/review-risk.json` is `platform`-owned (`ownership_rules` inside
the file says so about itself); adding a key to it from this seam needs a
lease this issue's own charter never asked for, for a ruleset that governs a
*different* decision (which review DOMAINS a diff needs) than the one this
issue asks for (which RELEASE PATH a diff qualifies for) — conflating them
means a future edit to review-domain gating risks silently moving the
release-path bar too. (2) `review-risk.json`'s glob engine is built for
*inclusion* scoring (does this diff touch a risky area, and by how much) —
this issue needs the opposite shape, an EXCLUSION proof (every changed byte
must be inside four specific paths, in a specific shape, or the whole diff
is refused), which is a much narrower and stricter question than anything
`_classify` computes today. Bending one engine to answer both weakens
whichever question loses the tie-break in a future edit.

### B — A dedicated, hardcoded classifier module in this crew's own seam (adopted)

`bin/nightshift_bookkeeping_release.py`: the four eligible paths
(`.omni/STATUS.md`, `.omni/ledger.md`, `.omni/tech-debt.md`,
`.omni/reports/**`) are Python constants, not a JSON ruleset — this issue's
own body gives a fixed, small, spec-stated list, not something meant to be
configured ad hoc. Diff extraction disables rename detection (`--no-renames`)
so a rename can never present as a single ambiguous `R` status; it always
shows as one `D` + one `A`, and `D` is never an allowed status, so renames
refuse themselves through the ordinary rule with no special-case code to
keep correct. Wins on both counts option A loses: no cross-seam lease, and a
strict allowlist-of-everything check is a different, simpler shape than
inclusion scoring — proven independently in `tests/test_bookkeeping_release.py`
against real git plumbing (a real `git mv`, a real symlink, a real
`chmod +x`), not just synthetic entries.

### C — Skip the module split, put everything inline in `nightshift-ctl` (rejected)

Every other exact-SHA diagnostic this codebase has (`review plan`, `review
eligibility`) keeps the pure compute in a sibling module and the writes in
`nightshift-ctl` itself. Breaking that pattern for this one feature would
make "which module owns `control.db` writes" answerable by a rule with an
exception, which is exactly the kind of drift `bin/nightshift_review_receipts.py`'s
own docstring calls out as worth avoiding. Also loses easy unit-testability
— `classify_paths` and the `check_*` functions are exercised directly by
~20 fast unit tests with no subprocess or CLI overhead specifically because
they are importable, standalone functions.

## Decision

Adopt **B**. `nightshift_bookkeeping_release.py` computes; three new
`nightshift-ctl bookkeeping` verbs (`plan`, `verify-head`, `receipt-record`)
own every read/write against `control.db`, exactly mirroring the
`review plan`/`review marker-mint`/`review receipt-record` split. `plan`
answers "is this exact head fast-path eligible, and why or why not" as data
(`fast_path_eligible: bool`), not as a process-failure exit code — only a
genuinely unproduceable plan (bad task/SHA/git state) exits 3, matching
`review plan`'s own `PlanDenied → 3` convention. `verify-head` is wired into
`PLANE_FREE_VERBS` (ADR-0020's verb-granular set): it is a pure two-SHA
string comparison, needs no control-plane connection, and its sibling verbs
in the same group DO need one — exactly the shape that set exists for.
`receipt-record` persists an append-only audit row (migration seq 3, lease
id 19 on `bin/nightshift-ctl`) so AC-4's "records the exact merged SHA and a
verification receipt" has somewhere durable to land once `release` has
acted — this ADR does not claim `release` calls any of these three verbs
yet; that wiring is downstream, cross-seam work named explicitly in the DoR
verdict and the frozen contract's AC-8 row.

## Consequences

**Easier:** a bookkeeping-only PR now has a single, testable, auditable
yes/no with reasons, ready for `release`'s procedure to consume whenever
that wiring lands — no design work left on that side, only integration.
Every refusal reason is machine-readable and names the offending path,
so a denied fast path is debuggable without re-deriving the diff by hand.

**Harder:** the four-path allowlist is now duplicated conceptually (not in
code) between this issue's body and `ELIGIBLE_EXACT_PATHS`/
`ELIGIBLE_REPORTS_PREFIX` — a future bookkeeping path (say, a new
`.omni/handoffs/**` narrative file) needs a code change here, not a config
edit. Deliberate per Option A's rejection, but it is a real, named cost.

**Forecloses:** nothing in `release`'s own procedure — the primitive is
read-only with respect to `deploy/exact_sha_release.py`'s behavior, and
`release` remains free to ignore `fast_path_eligible` entirely (in which
case nothing regresses; every PR still gets the full pipeline it gets
today).

## Cost

No new operational surface: one new SQLite table inside the existing
`control.db` (append-only, unbounded but bookkeeping-PR-rate-bounded growth,
same shape as `review_receipts`), one new ~350-line Python module, three new
CLI verbs. No new dependency, no new service, no new scheduled job. At 100×
today's bookkeeping-PR volume, `bookkeeping_receipts` grows linearly and
stays cheap to query by the existing `(task_id, head_sha)` index.

## One-way door?

No. Every artifact here is additive (`CREATE TABLE IF NOT EXISTS`, new CLI
verbs, a new module) and read-only with respect to every existing table and
command body. Dropping this feature later means deleting the new file, the
three verbs, and the table — nothing downstream depends on
`bookkeeping_receipts` existing, because `release` does not consume it yet.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`). The trigger that would move
it up: `release`'s procedure is actually wired to this primitive AND
bookkeeping-PR volume grows enough that `bookkeeping_receipts`' unindexed
growth (currently bounded by the same PR-rate this org already operates at)
becomes worth capping or archiving.

## Reversal trigger

`release`'s own procedure, once wired, observes `fast_path_eligible: true`
on a PR that a human or a later review finds should NOT have qualified — at
which point the four-path allowlist or the check bundle needs tightening,
not just a bug fix to one check. Absent that, the design holds: fail-closed
means the cost of being wrong is a bookkeeping PR unnecessarily taking the
slow path, never a code change taking the fast one.
