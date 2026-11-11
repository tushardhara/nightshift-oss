# ADR-0064 — Make exact-SHA cutover supervision state generation-aware

- **Date:** 2026-09-01
- **Status:** Accepted (staff review 2026-09-01, verdict ACCEPT-WITH-CHANGES, both named changes
  applied inline — see "Reconciliation status" and the two flagged edits below). Design input for
  `crew_beta`'s implementation; still not itself authorization for any live deployment or cutover.
- **Issue:** [#490](https://github.com/tushardhara/nightshift/issues/490)
- **Run:** `fixture-review-231`
- **Owner:** `crew_beta` (the `deploy/**`, except `deploy/policies/**`, ownership seam)
- **Amends if accepted:** ADR-0013 D5/D10 and ADR-0014 C2 only to define second and later
  exact-SHA cutovers. Their first-cutover masking decision remains unchanged.
- **Governance gate:** `crew_beta` must not implement this draft until its `pm` records Definition of
  Ready, a different-vendor proposal has been reconciled into this ADR, and `staff` accepts the ADR.
  Implementation then requires QA, different-vendor in-crew review, different-crew review, and Release
  integration. This ADR authorizes no live deployment, cutover, or other host mutation.
- **Reconciliation status (2026-09-01, Program, run_id `fixture-review-231`):** `crew_beta`'s
  independent blind proposal (draft ADR-0065,
  `docs/decisions/ADR-0065-second-cutover-legacy-unit-eligibility.md` on
  `origin/beta/490-design-proposal`; `beta_pm`'s DoR verdict READY, spec on
  `origin/pm/490-dor-spec2`) converged on the same core direction as this ADR: keep the bootstrap
  (first-cutover) check strict; require *verified prior-accepted-cutover evidence*, not a bare mask
  symlink, before treating an already-masked unit as satisfied in a later cutover; no placeholder
  units; no operator override flag. That two independent readings of the same source converged on
  the same shape is treated as the confidence signal this process exists to produce. This ADR's
  single-predecessor, full-runtime-identity-chain design (Decision §2) is adopted over ADR-0065's
  broader "scan every accepted receipt on the host, match per unit" design, because §2's chained
  `inherited_masked` schema already carries earlier generations' masked units forward through each
  new plan, giving the same coverage ADR-0065's scan-everything approach was reaching for (a unit
  masked two generations back and untouched since) without needing to trust receipts outside one
  coherent, currently-live-runtime-verified lineage — the narrower, more auditable mechanism, per
  this project's own tie-break convention. **Folded in from ADR-0065, credited:** the predecessor
  validation checklist below (Decision §2) now also requires the *absence* of a
  `rollback-receipt.json` in the predecessor's own `state_dir` asserting a completed rollback of
  that specific unit — ADR-0065's OQ-2 finding, a genuine edge case (a partially-failed rollback
  can leave on-disk state and backup hash coincidentally matching a stale snapshot while the tool's
  own record says that generation was supposed to be undone) this draft had not made explicit.
  ADR-0065 itself is not separately adopted; its content lives on here, credited, and its branch is
  not merged.
- **Process-gap finding, not fixed here:** `crew_beta` flagged that its `pm`'s routine preflight
  read of `.omni/STATUS.md`/`.omni/decisions.md` (files this task's own dispatch named as evidence
  pointers) incidentally exposed a high-level Program-written summary of this ADR's draft mechanism
  before crew_beta's proposal was finished — even though crew_beta was explicitly told not to open
  any `docs/decisions/` file dated today, that instruction didn't cover Program's own status/ledger
  files, which is a real, distinct information-leakage channel a "blind" dispatch needs to account
  for. crew_beta's own disclosure shows its substantive answer was derived independently from source
  (not copied), so this reconciliation stands, but the gap itself is recorded here for Program's own
  process going forward: a future blind-counter-proposal dispatch should either tell the second
  proposer to avoid `.omni/STATUS.md`/`.omni/decisions.md` too, or defer writing a detailed summary
  of the first proposal into those files until after the second is collected.

## Context

`deploy/exact_sha_release.py` currently has one supervision model for every release generation.
`prepare()` calls `_legacy_unit_inventory()`, which always adds the four
`REQUIRED_LEGACY_UNITS` and refuses unless every selected unit has `LoadState=loaded` and a readable
`FragmentPath`. The same inventory is then compared again by `authorize()`,
`_fresh_cutover_gate()`, `_mask_legacy_units()`, and rollback restoration.

That model is valid for the first exact-SHA cutover: it inventories the mutable-root supervision
paths, proves they are inactive, backs up their local definitions, masks them, and can restore their
exact prepared state during rollback. It is invalid after acceptance. ADR-0013 D5, as corrected by
ADR-0014 C2, deliberately leaves those paths masked; `deploy/EXACT-SHA-CUTOVER.md` says they remain
masked so they cannot resurrect a mutable-root runtime. On the reproduced host, unmasking removes the
only local `/dev/null` symlink and reveals that no lower-priority unit fragment exists. `LoadState`
therefore becomes `not-found`, not `loaded`. A second `prepare` can never pass.

This is not only a `prepare()` bug. Softening that one call would leave the later inventory equality,
mask, and rollback paths operating on the old premise. In particular, a second-generation rollback
must preserve the masks inherited from the accepted predecessor; it must not reconstruct or unmask
retired mutable-root supervision.

The tool is otherwise already shaped as a repeatable release mechanism. It captures the currently
served Program bundle and policy tree for rollback, verifies runtime identity, accepts a new exact
SHA, and restores the captured predecessor on failure. Declaring the whole tool one-time-only would
discard those useful properties without supplying the redeploy mechanism required by #430/#489.

### Definition-of-Ready input

The item is ready for proposal reconciliation when the crew contract names: (1) the trusted evidence
for entering subsequent-generation mode; (2) the exact supervision-state schema and transition rules;
(3) rollback behavior for inherited masks versus units newly discovered by this run; and (4) a
disposable two-cutover test with observable RED-before-GREEN evidence. No production host access or
credential is needed. `crew_beta` owns every expected source change. Tests driving this deploy module
belong with that seam; any edit to shared test-fixture infrastructure requires a platform lease.

## Options considered

### A — Skip or soften the loaded requirement after finding any accepted receipt

Rejected as stated. It correctly recognizes that the expected post-cutover state differs from the
bootstrap state, but “any `accepted: true` receipt anywhere under the state directory” is insufficient
authority. A copied, stale, unrelated, or structurally incomplete receipt could select the weaker path.
It also does not define what `authorize`, the freshness gate, masking, and rollback compare or restore.

A receipt remains useful, but only as one member of a verified predecessor chain whose SHA, plan
digest, proof set, and current runtime operands agree.

### B — Install inert placeholder legacy unit files

Rejected. A placeholder creates a second representation of deliberately retired supervision purely
to satisfy an obsolete predicate. Its content, installation location, ownership, and continued
inertness become new security-sensitive facts. Worse, rollback code could restore the placeholder as
though it were a real pre-cutover unit. “Never enabled or started” is a procedural promise where
ADR-0014 chose masking as the mechanical guarantee. No runtime capability requires these files.

### C — Declare `nightshift-release` a one-time bootstrap tool and charter another redeployer

Rejected for now. The present state machine already materializes a sealed candidate, snapshots the
served predecessor, authorizes quiescence, performs rollback, and proves the accepted runtime. A new
redeployer would duplicate the most safety-critical parts and create two release protocols. This
option becomes preferable only if the disposable proof shows that the current rollback artifact or
state schema cannot represent an exact-SHA predecessor without weakening ADR-0013's proofs.

### D — Generation-aware supervision reconciliation with an attested predecessor

Chosen. Preserve the strict bootstrap path and add an explicit subsequent-release path. Supervision
is modeled as state to preserve across a transition, not as unit files that must always exist.

## Decision

If accepted, `nightshift-release` SHALL support two fail-closed preparation modes.

### 1. Bootstrap mode remains strict

When there is no verified accepted predecessor, behavior remains the current behavior: every required
and mechanically discovered mutable-root unit must be loaded from a readable fragment. The plan records
that inventory, cutover masks it, and rollback can restore the exact prepared unit state. Absence,
masking, unreadable fragments, or ambiguous history refuses preparation.

### 2. Subsequent mode requires a verified predecessor, not mere receipt existence

The caller supplies or the tool deterministically resolves exactly one predecessor `plan.json` and
`receipt.json`. The tool enters subsequent mode only when all of the following hold:

1. the predecessor plan validates against its embedded `plan_digest`;
2. the receipt has the supported schema, names that plan digest, has `accepted: true`, and every proof
   required by that receipt schema is green;
3. the currently served registration, content digest, release root/SHA, policy path, control DB
   identity, and PID/runtime environment agree with the accepted predecessor operands using the same
   checks already used by authorization/postflight; and
4. every legacy unit inherited from the predecessor is still mechanically masked at its recorded path
   (`/dev/null` symlink), inactive, and not replaced by a newly loaded fragment; and
5. no `rollback-receipt.json` exists in the predecessor's own `state_dir` (folded in from
   ADR-0065's OQ-2: a partially-failed rollback can leave a unit's on-disk state and backup hash
   coincidentally matching a stale, supposedly-undone snapshot; checking only the mask/hash match
   would silently treat that unit as eligible when the tool's own record says the opposite).
   **Granularity (resolved at `staff` review, 2026-09-01):** `rollback-receipt.json` as written by
   `_verify_rollback`/`_rollback_live` (`deploy/exact_sha_release.py:2150-2220`) is a
   whole-generation artifact today — `verified_at`/`green`/`session_id`/`served_digest`/
   `control_identity`/`policy_digest`/`runtime_environment`, no per-unit field. Item 5 is therefore
   whole-generation, not per-unit: the presence of *any* `rollback-receipt.json` in that predecessor's
   `state_dir` disqualifies the predecessor entirely (every one of its `inherited_masked` units),
   not just the units it happened to touch. This requires zero schema change, is safe by
   over-refusal (a rolled-back predecessor simply cannot be trusted as anyone's evidence, which is
   the conservative and correct reading anyway), and matches what the code already writes. Per-unit
   granularity (a keyed field added to `rollback-receipt.json`) is explicitly out of scope for this
   ADR; revisit only if whole-generation disqualification is shown too coarse in practice.

Missing, multiple, malformed, unsupported, or runtime-mismatched predecessors refuse. Directory order,
mtime, and “latest receipt” are not authority. The selected predecessor identity and evidence digest
are recorded in the new plan so `authorize` and `cutover` can re-check the same facts.

### 3. Split supervision inventory into inherited masks and newly discovered units

The new plan schema records a supervision transition with at least:

- `mode`: `bootstrap` or `subsequent`;
- `predecessor`: accepted plan/receipt identity in subsequent mode;
- `inherited_masked`: retired units whose exact pre-run state is already masked;
- `to_mask`: currently loaded, inactive units discovered mechanically because their fragments refer to
  a stale mutable root, including dependants; and
- for every `to_mask` row, the current local override and fragment evidence needed for exact rollback.

The four historical `REQUIRED_LEGACY_UNITS` remain required *names*, but in subsequent mode their valid
state is the inherited, inactive mask attested by the predecessor, not a readable legacy fragment.
Discovery still scans loaded user services/timers/sockets/paths so a newly introduced mutable-root
supervision path cannot escape merely because the host is no longer in bootstrap mode.

`prepare`, `authorize`, and `_fresh_cutover_gate` compare this complete transition object. They do not
silently omit supervision checks.

### 4. Masking and rollback are delta-based

Cutover leaves `inherited_masked` untouched and masks only `to_mask`. Acceptance leaves both groups
masked. Rollback restores only the exact pre-run state of `to_mask`; inherited masks remain masks.
Thus a failed second cutover restores the accepted predecessor runtime without resurrecting the
pre-bootstrap mutable-root services.

No implementation may synthesize a unit fragment, unmask an inherited unit, or infer legacy unit
contents from source templates. Any unexpected change to an inherited mask or newly discovered unit
between prepare, authorize, and cutover is a refusal.

### 5. Scope and implementation owner

`crew_beta` implements the plan-schema, inventory, transition, rollback, runbook, and module-test
changes under its `deploy/**` seam. The likely files are `deploy/exact_sha_release.py`,
`deploy/EXACT-SHA-CUTOVER.md`, and tests that drive that module. No live cutover is part of the PR.
Because the code consumes filesystem/systemd state and release receipts as external input, `security`
must review the diff under the project’s sensitive-input gate.

The implementation PR must reference #490 and this ADR. It proceeds through `crew_beta` QA,
different-vendor in-crew review, a reviewer from `crew_alpha`, security, PM acceptance sign-off, and
Release testing/integration. Only Release may merge. Merge is not permission to deploy.

## Acceptance proof

The acceptance test is a disposable or fully simulated **two-generation release sequence**, not a live
host operation:

1. Start with readable, inactive legacy fragments and candidate SHA A. Run
   `prepare → authorize → cutover`; produce an accepted receipt with all required proofs green and
   legacy units masked.
2. Remove the underlying legacy fragments so unmasking would yield `LoadState=not-found`, matching the
   issue reproduction. Keep the accepted masks and make runtime operands resolve to SHA A.
3. Prepare candidate SHA B using A's verified predecessor evidence, then run
   `authorize → cutover`. It must accept B without recreating or unmasking a legacy fragment; all
   required proofs must be freshly green.
4. In a separate B attempt, inject a failure after masking/startup has begun. Verified rollback must
   restore runtime A, keep all inherited masks in place, and restore only units newly selected in B's
   `to_mask` delta.
5. Demonstrate refusal for each of: forged `accepted: true`; red or missing predecessor proof; plan or
   receipt digest mismatch; current runtime not matching the predecessor; missing/replaced/unmasked
   inherited mask; active inherited unit; a newly loaded mutable-root unit changing after prepare;
   and (staff review, item 5's own coverage gap) **a `rollback-receipt.json` present in the
   predecessor's `state_dir`, including the coincidental-match sub-case ADR-0065 named** — mask
   symlink and backup hash both still matching the stale pre-run snapshot exactly, but the rollback
   receipt's presence alone must still refuse subsequent-mode entry for that predecessor.

QA must show the second-generation success case fails against the pre-change code at the reproduced
`legacy supervision units are unreadable` boundary, then passes with the change. Test-runner output,
not grep or assertion count, supplies the test count. The exact test, lint, and typecheck commands and
file set are recorded in the PR report.

## Consequences

- Exact-SHA release becomes repeatable while retaining the one-supervision-path decision and all eight
  acceptance proofs.
- First-cutover behavior does not become more permissive. Subsequent behavior is permitted only when a
  digest-linked accepted predecessor agrees with the runtime and the inherited masks.
- Plans and receipts gain a lineage and supervision-transition contract. Schema versioning and clear
  refusal of unsupported versions are required; silently interpreting old fields under new semantics is
  forbidden.
- Rollback becomes safer after the first cutover because it can no longer restore retired supervision
  merely because a later release failed.
- Implementation is larger than removing one check: all inventory consumers and rollback paths must be
  changed together. That is intentional; partial softening would create inconsistent safety gates.
- No placeholder units, new daemon, queue, datastore, deployment environment, or live migration is
  introduced. Runtime cost is bounded by the existing unit scan and receipt verification.

## Reversal trigger

`crew_beta` owns the trigger; evidence is the disposable two-generation acceptance transcript and its
receipts, recorded with the PR.

Supersede this decision with a separate repeatable redeploy mechanism (Option C) if either:

1. the disposable proof shows that an accepted exact-SHA predecessor cannot be identified and restored
   without relying on mutable or unauthenticated state; or
2. two implementation/review loops find that generation-aware behavior necessarily forks the core
   prepare/authorize/cutover proofs rather than reusing them, making one state machine less auditable
   than two explicit tools.

Do not reverse by installing placeholder units or by weakening predecessor/runtime/mask agreement.
Those approaches may be reconsidered only through a superseding ADR with a mechanical proof that they
cannot resurrect mutable-root supervision.
