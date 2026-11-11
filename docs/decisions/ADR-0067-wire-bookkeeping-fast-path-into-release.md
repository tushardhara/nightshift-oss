# ADR-0067: Wire `release`'s own merge procedure to consult the #286 bookkeeping fast-path classifier

Date: 2026-09-01 · Status: proposed
Deciders: crew_beta (proposer, this task; edit target is `program/**`, platform/program-owned
single-writer per `docs/owners.md` — "no crew owns this... Changes require an ADR"), staff (gate,
required before any crew builds against this ADR, same rule as every other ADR in this tree)
Spec: GitHub issue #388 (refs #286); DoR verdict posted on #388 2026-09-01T23:18:23Z (crew_beta `pm`,
`gh issue view 388`); tech-debt correction row `.omni/tech-debt.md` 2026-09-01 (landing via PR #523,
open); frozen contract `docs/reviews/286-bookkeeping-fast-path-CONTRACT.md` v1, AC-3/AC-8 rows
Depends on: ADR-0052 (defines the classifier this ADR wires in; this ADR changes none of its module,
table, or CLI-verb design — it only adds a caller). Corrects: ADR-0052's Context section and
`.omni/tech-debt.md`'s 2026-08-30 #286 row, both of which named the wrong file as release's merge
procedure (see "Corrects a prior premise" below).

## ELI5

`nightshift-ctl bookkeeping plan` can already answer "is this exact PR head nothing but bookkeeping
files, safely?" — issue #286 built and tested that. Nobody ever asks it the question. `release`, the
one agent that merges to `main`, still runs the full ~20-minute test suite on every single push,
including one that only touched `.omni/STATUS.md`. This ADR changes `release`'s own prompt
(`program/agents/release/config.yaml`) so that for a solo push whose exact head the classifier calls
`fast_path_eligible: true`, `release` skips the slow pre-merge test run and instead relies on the
classifier's own cheap checks plus one pre-merge re-check that the head hasn't moved. Nothing else
about `release`'s job changes: it still tags first, still merges through the same GitHub-side checks,
still re-tests `main` after merging, and still reverts itself if that goes red.

## Context

Issue #286 (ADR-0052) built `bin/nightshift_bookkeeping_release.py` and three `nightshift-ctl
bookkeeping` verbs (`plan`, `verify-head`, `receipt-record`) as a **primitive**: a fail-closed,
exact-SHA classifier that answers "is this diff nothing but an add/modify inside
`.omni/STATUS.md`/`.omni/ledger.md`/`.omni/tech-debt.md`/`.omni/reports/**`, in a shape that could not
hide anything else?" ADR-0052 explicitly scoped wiring a consumer to this primitive as out of bounds
for that work ("Forecloses: nothing in `release`'s own procedure... `release` remains free to ignore
`fast_path_eligible` entirely") and named it as downstream, cross-seam work. `.omni/tech-debt.md`'s
2026-08-30 row recorded the gap: AC-3 (skip the full suite) and AC-8 (5-minute merge target) are
proven only as primitive capability, never as `release`'s observed behavior, because nothing calls
`bookkeeping plan` from `release`'s own procedure.

### Corrects a prior premise

Issue #388 was originally chartered to close this gap by editing `deploy/exact_sha_release.py`. That
premise is wrong, and it also appears in ADR-0052's own Context section (the paragraph naming
`deploy/exact_sha_release.py` as "the code that actually builds scratch candidates, runs the suite,
waits for CI and merges") and in the 2026-08-30 `.omni/tech-debt.md` row. Direct read at checkpoint
`2d1813f`:

- `deploy/exact_sha_release.py:2`'s docstring: "Deterministic exact-Git-SHA release preparation and
  cutover proofs." Its only subcommands are `prepare`/`authorize`/`cutover`/`rollback`
  (`deploy/exact_sha_release.py:2717-2749`), and `prepare --sha` requires the SHA to already be an
  ancestor of `origin/main` (`deploy/exact_sha_release.py:1062`) — it only ever runs *after* a PR is
  merged. `deploy/EXACT-SHA-CUTOVER.md:9-11` confirms it is the manual, operator-triggered path that
  cuts the live Omnigent *server process* over to an already-reviewed SHA.
- An exhaustive scan of every subprocess call in that file (`grep -n "_run(\[\|subprocess.run(\["`)
  shows only git plumbing, an interpreter `-c` snippet, `omnigent --version`, `ss`, and `tmux` session
  management. No `tests/run.sh`, no `gh pr merge`, no scratch-merge-candidate build anywhere in it.
- The actual candidate-build/full-suite/CI-check/merge procedure — the thing issue #388 needs wired —
  lives entirely in `program/agents/release/config.yaml`'s prompt: its 9-step "integration procedure"
  (steps 3 and 8 name "Run the FULL suite on that merge result" and its post-merge repeat; step 7 runs
  `gh pr merge --merge --match-head-commit`). That file is `program/**`, confirmed platform/program
  -owned by `docs/reviews/233-activate-review-routing-CONTRACT.md`'s AC-2 row, and its own prompt
  states under "What you never do": "You do not DEPLOY."

This ADR targets `program/agents/release/config.yaml` directly — the file that actually contains the
mandatory-suite text AC-3 needs weakened, narrowly and conditionally. Fixing the two stale references
above (ADR-0052's Context, the 2026-08-30 tech-debt row) is bundled into this ADR's implementation PR
(see Decision); it is a factual correction to prose describing already-adopted, unchanged designs, not
a re-opening of ADR-0052's own Decision or Consequences.

### Why this needs an ADR at all

`program/**` is `docs/owners.md`'s one path with no crew owner: "an unreviewed edit is how a run
silently weakens its own controls. Changes require an ADR." `release`'s prompt is the literal text of
the only gate standing between every crew's work and `main`. Editing it without the same ADR-plus
-staff discipline every other control-plane change goes through would be exactly the failure mode that
rule exists to prevent, regardless of how narrow the edit looks.

## Options considered

### A — Add a conditional fast-path section to `release`'s own prompt (adopted)

Insert one new prompt section, "The bookkeeping-only fast path," that `release` reads and follows
directly: before building a candidate, if the candidate is a single task branch, call `nightshift-ctl
bookkeeping plan` on its exact head; if `fast_path_eligible` is true, skip step 3's local full-suite
run for that candidate and substitute `bookkeeping verify-head` immediately before merge plus
`bookkeeping receipt-record` immediately after. Every other precondition, the GitHub Actions check,
step 8's post-merge full suite, and step 9's revert-on-red are unchanged text, unconditionally. Wins
because it reuses ADR-0052's already-built, already-tested primitive exactly as designed (a diagnostic
a downstream consumer decides whether to act on — the same shape `suggested_domains`/ADR-0042 already
has), needs zero new code (no new CLI verb, no new table: `plan`, `verify-head`, and `receipt-record`
already take every argument `release` has on hand — task id from the branch/registry, repo from cwd, a
head SHA it already sources once per existing step 6, and `main`'s current tip), and is reviewable as
a small, self-contained prompt diff.

### B — A wrapper script in `deploy/**` that `release` invokes (rejected)

Write a new `deploy/release_fast_path.sh` (or similar) that internally calls `bookkeeping plan` and
either runs the full suite or doesn't, and have `release`'s prompt call that script instead of
inlining the logic. Rejected: it recreates exactly the premise issue #388 was chartered on and PR #523
found false — there is no `deploy/**` script in `release`'s actual procedure to begin with, so adding
one only to have the prompt call it is an unnecessary intermediate layer with its own new file, new
tests, and new failure surface, for a decision (`fast_path_eligible: true/false`) `bookkeeping plan`
already returns as one JSON call. It also relocates control-plane decision logic for what merges to
`main` out of the platform-owned prompt and into a crew-ownable path (`deploy/**`, `crew_beta`-owned,
not single-writer per `docs/owners.md`), which is a strictly worse ownership shape for a merge-gate
decision than keeping it inline in the one file that already requires an ADR to touch.

### C — Encode the skip as a `guardrails.policies` function instead of prompt text (rejected)

`release`'s `config.yaml` already has a `guardrails.policies` block (three function-backed policies:
`blast_radius`, `pr_close_keyword_guard`, `stuck_detector`) that runs on every tool call, independent
of what the prompt says. A `bookkeeping_fast_path` policy could intercept the FULL-suite tool call and
short-circuit it when `bookkeeping plan` says eligible. Rejected: those three policies are all
DENY-shaped backstops (block a catastrophic or forbidden action outright) — none of them today decide
*what evidence substitutes* for a skipped step, and building that shape here means the actual
skip-and-substitute logic (call `verify-head`, call `receipt-record`, choose the substitute evidence)
lives in Python the model never sees or reasons about, invisible to the same audit trail (the
integration log, this ADR, a human reading the prompt) every other release decision is legible through.
`release`'s own procedure already narrates itself in `.omni/integration-log.md`; a policy-layer
short-circuit would silently do something the log doesn't explain. Prompt text keeps the decision in
the one place `release` (and a reviewer) already reads to understand what it does and why.

## Decision

Adopt **A**. `program/agents/release/config.yaml`'s prompt gets one new section, "The bookkeeping-only
fast path," placed after "The non-crew lanes" and before "When the GitHub check is expected":

- **Scope of the fast path**: applies only to a candidate consisting of exactly one task branch (never
  a multi-branch candidate, even if every branch in it independently classifies eligible — batching is
  outside ADR-0052's single-exact-head design and stays out here too).
- **Classify before building**: call `nightshift-ctl bookkeeping plan --task <task-id> --repo .
  --head-sha <head_sha> --main-sha <current main tip>`, reusing the exact head SHA sourced once via
  the existing step 6 `nightshift-ctl pr-head <n>` call rather than a second read. Read
  `fast_path_eligible` directly; never re-derive the classification.
- **If `fast_path_eligible` is `false`**: the normal procedure runs, completely unchanged — this is the
  default outcome for every code PR and for any bookkeeping-shaped diff the fail-closed classifier
  cannot fully vouch for.
- **If `fast_path_eligible` is `true`**: step 3's local full-suite run for this candidate is skipped.
  In its place: immediately before merging (still inside step 7's position in the sequence), re-read
  the head SHA and call `nightshift-ctl bookkeeping verify-head --expected-head-sha <the classified
  head> --actual-head-sha <freshly read head>`; a mismatch is handled exactly like a red candidate
  (step 4: drop it, do not merge, report back to the crew — never fall through to a merge on stale
  evidence). After step 7's merge succeeds, call `nightshift-ctl bookkeeping receipt-record` with
  `--task <the plan's own `task.id`> --base-sha <the plan's own `target.base_sha`> --head-sha <the
  plan's own `target.head_sha`> --classification bookkeeping-only --verdict pass --merged-sha <the
  merge commit SHA> --recorded-by release` — `--task`/`--base-sha`/`--head-sha` reused verbatim from
  the classifying `bookkeeping plan` call's own JSON response, never re-derived — or `--verdict fail`
  (no `--merged-sha`) if `verify-head` denied. `nightshift-ctl` requires `--task`, `--base-sha`,
  `--head-sha`, `--classification`, `--verdict`, and `--recorded-by`; the receipt table is the audit
  trail for every classify-then-merge attempt, not only the successful ones.
- **What stays mandatory, unconditionally, for a fast-pathed merge exactly as for every other merge**:
  preconditions 1-6 (crew review, ADR reference, security when applicable), the "GitHub check" section
  (step 6's required-check verification is a separate, DoD-mandated gate this classifier does not
  touch), step 8's post-merge full suite, and step 9's revert-on-red. Skipping step 3 removes the
  slowest step standing between an eligible PR and reaching `main`; it does not remove the safety net
  that catches "something outside this candidate moved" (step 8's own stated purpose) once it's there.
- The "When the GitHub check is expected" section's line "the candidate and post-merge local suites in
  steps 3 and 8 remain mandatory" is corrected to name the fast-path exception it now has (a pointer
  to the new section, not a rewrite of its meaning — GitHub's own check stays unconditional).

**Bundled in the same implementation PR** (factual corrections, not a re-opening of either document's
own Decision): ADR-0052's Context section, corrected to name `program/agents/release/config.yaml`
(not `deploy/exact_sha_release.py`) as the file containing the candidate-build/full-suite/merge
procedure; `.omni/tech-debt.md`'s 2026-08-30 #286 row is left as-is (already superseded by its own
2026-09-01 append-only correction row, landing via PR #523 — this PR does not duplicate that row).

**No new `nightshift-ctl` glue.** `bookkeeping plan`, `verify-head`, and `receipt-record` already
accept every argument `release`'s existing procedure has on hand at the point each is called; this ADR
adds a caller, not a new CLI surface.

### FOLLOW_UP: why step 8/step 9 staying mandatory is safe (staff review, point 4)

`staff`'s review of this ADR returned `consistent-with` ADR-0052/ADR-0037 but `needs-a-wider-decision`
on one point: this design skips only the PRE-merge candidate suite (step 3), not the POST-merge suite
(step 8) `docs/reviews/286-bookkeeping-fast-path-CONTRACT.md` v1's AC-3 row and issue #388's original
acceptance bullet both named (plural, both suites). Program settled this as `.omni/decisions.md`'s
2026-09-02 "ADR-0067 / #286 AC-3 scope" entry records: adopt the narrower, safer scope as designed here,
not literal AC-3. Staff's own point-4 safety reasoning, folded in here (and inline in the prompt itself,
so it is legible without re-deriving it from this ADR) rather than left implicit:

- The fast path is solo-branch-only, so step 3's actual purpose — catching a failure that only shows up
  when two branches combine — does not apply to what gets skipped here.
- Step 6's required GitHub check already ran the identical full suite on this exact PR head before
  merge is even considered — no path is exempted from ever being tested (ADR-0037: no exclusion).
- The one real gap a skipped step 3 opens — `main` advancing between that GitHub check and the merge
  moment — is caught post-hoc by two independent, unconditional full-suite runs (GitHub's own
  push-triggered run on the merge commit, and this procedure's own step 8) plus step 9's unconditional
  revert-on-red.

This is why `docs/reviews/286-bookkeeping-fast-path-CONTRACT.md` is amended to v2 in this same
implementation PR, disclosing AC-3 as "skips the pre-merge candidate suite; the post-merge suite is
intentionally retained as the safety net" rather than the original plural wording — see that file's
amendment note and `.omni/decisions.md` for the full option analysis and reversal trigger.

## Consequences

**Easier:** an eligible bookkeeping-only PR (issue #283's original complaint) stops paying the full
~20-minute suite cost it cannot possibly need — the change AC-3/AC-8 have been waiting on since
ADR-0052 landed becomes observable in practice, not just provable at the primitive level.

**Harder:** `release`'s prompt gains one more conditional branch a reader has to hold in their head;
the "always run the full suite" invariant every prior version of this prompt had is no longer
literally true, so anyone reasoning about `release`'s guarantees from memory instead of the current
prompt text needs to know this exception exists.

**Forecloses:** nothing about `bookkeeping plan`/`verify-head`/`receipt-record`'s own design (ADR-0052
untouched); nothing about any other release lane (oncall, platform-shared-path) or precondition. Does
not extend to any path outside the four already-allowlisted in `nightshift_bookkeeping_release.py` —
widening that allowlist is a change to that module under ADR-0052's own ownership, not to this ADR.

## Cost

No new operational surface: zero new files, zero new CLI verbs, zero new tables. One prompt section
(~40 lines) in an existing file, plus one corrected paragraph in ADR-0052's Context and one corrected
sentence in the same prompt's existing "GitHub check" section. `release` makes one extra
`nightshift-ctl` call (`bookkeeping plan`) per solo-branch candidate it would otherwise have taken
through the full pipeline anyway — strictly cheaper than what it replaces.

## One-way door?

No. The added section is one self-contained block of prompt text; reverting means deleting it (and
restoring the "remain mandatory" sentence to its prior unconditional wording) with no residual state —
`bookkeeping_receipts` rows already recorded stay as an accurate historical audit trail regardless, and
nothing downstream depends on this ADR's wiring existing.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`). The trigger that would move it up: bookkeeping-only PR
volume grows enough that solo-branch-only batching (this ADR's scope restriction) becomes a real
throughput bottleneck worth a multi-branch fast-path design — not proposed here because no such volume
exists today.

## Reversal trigger

If a PR that took this fast path is later found to have caused a regression that the skipped local
full suite would have caught — whether because the classifier's allowlist or checks had a gap, or
because some interaction the fast path's "solo candidate only" restriction did not anticipate slipped
through — revert this ADR's prompt change (restore step 3's unconditional full-suite run) and treat
the underlying classifier gap as a defect against ADR-0052, not against this wiring. Absent that,
`release`'s post-merge full suite (step 8, unchanged and unconditional) and the receipt trail
(`bookkeeping_receipts`) are the ongoing evidence this hasn't happened.
