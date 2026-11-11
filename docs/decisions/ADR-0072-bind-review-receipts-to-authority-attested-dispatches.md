# ADR-0072: Bind review receipts to authority-attested author and reviewer dispatches

Date: 2026-09-02 · Status: proposed

Deciders: architect (Codex), program; requires staff architecture gate before implementation

Conversation: `01a0623a-9354-72d3-ba67-b95f702ffacf` · Task:
`561-f102-review-independence-design` · Issue: #561 · Finding: F-102

Amended 2026-09-02 after staff's PRE_DEPLOYMENT review of head `7f4a52d7` and
crew_beta's independent approval of that same head. Staff agreed with the mechanism but
correctly refused to attest that the old head contained the six constraints recorded below.

Amendment traceability to staff's six PRE_DEPLOYMENT findings:

1. **Coverage:** strong Program attestation is limited to `PROGRAM_DIRECT_CHILDREN`; the
   worker-slot/admission path is separately labelled weaker, including its acquire-time
   self-declared crew, and is currently unavailable.
2. **Generation:** minting generation is audit evidence, never a consumption-equality gate.
3. **Fence scope:** marker mint is explicitly the third ADR-0044 section 3b attachment point.
4. **Vendor source:** vendor is omitted unless derived from the agent's own seat configuration;
   `.omni/models.json` and `tasks.crew` are forbidden sources.
5. **#571 consequence:** assignment/consumption state is required and powers the eligibility
   pre-flight.
6. **Cutover sequencing:** #571 lands before #573, with PR #564 as the in-flight case.

## Context

F-102 says the project cannot mechanically establish that a reviewer differs from the
author. Every seat uses the same GitHub PAT and therefore every PR comment and native review
has the same GitHub login. GitHub identity is degenerate by construction.

ADR-0039 deliberately proves only that an unedited review comment is fresh and bound to an
exact `(task_id, head_sha, domain)` marker. It explicitly does not prove which seat performed
the review. A seat name written into the comment or supplied to `review receipt-record` would
not repair that boundary: the shared credential holder could choose any name.

ADR-0068 is consistent but narrower. It requires claimant identity where the CLI already has
an identity-bearing holder record; it does not turn caller-provided strings into authenticated
seat identity.

This ADR independently restates the relevant default-branch rule: `release` is the sole writer
of the default branch, and that boundary must be enforced at the bundle root. Issue #118's live
probe established why: the engine loads the root spec's `guardrails.policies` and does not
traverse per-seat policy declarations, so a seat-local declaration is inert and cannot enforce
the rule. The tool-call event also carries no authenticated seat principal, so this ADR must use
identity only where identity exists and must not infer it later from GitHub or tool-call payloads.
ADR-0070 is a non-normative see-also; this decision does not depend on it being accepted or merged.

The trusted boundary here includes the single active Program authority and the control plane.
Program chooses and directly dispatches only `PROGRAM_DIRECT_CHILDREN`. That gives strong
third-party assignment evidence for those seats, but not for `pm`, `beta_pm`, or crew-internal
`code_review`/`qa`: those are dispatched by a lead, not Program. Counted crew-internal dispatch
is also dead org-wide until #495's cutover, so that weaker route cannot be exercised today.

The property required against ordinary operational error is that durable evidence names the
independently dispatched seats where such an independent dispatch exists and cannot be relabelled
after the fact. A malicious Program can assign the same person under different real credentials
or leak any capability; no mechanism below Program can prove independence from Program itself,
so that is explicitly out of scope.

## Options considered

### A — Authority-attested dispatch identity bound to the existing marker (chosen)

For `PROGRAM_DIRECT_CHILDREN`, persist an append-only author attestation derived from the Program
dispatch target. At review-marker mint, require live Program authority and persist the dispatched
reviewer seat alongside the exact `(author_task_id, review_task_id, head_sha, domain, marker)`
tuple. Derive crew from the closed seat configuration at mint time; derive vendor, if it is needed
for a protected predicate, only from that agent's own immutable seat configuration. Never derive
vendor from `.omni/models.json` (it names no worker seat, has zero code references, and is writable
in the shared tree), and never derive identity, crew, or vendor from `tasks.crew`.

The opaque marker is the one-use capability delivered only in that reviewer dispatch. Receipt
recording consumes that assignment and snapshots its identity binding. This is the strong path.
It does not claim coverage for every review domain.

For `pm_acceptance` and crew-internal `code_review`/`qa`, bind receipts instead to the existing
`worker_slots.bound_agent`/`bound_title`, `worker_slots.crew`, and `task_admissions` record. This
is explicitly weaker: `bound_agent` and `bound_title` are set atomically by the root policy at
dispatch, but `crew` is declared by the acquiring lead at slot-acquire time. The record therefore
proves lead pre-commitment to a counted seat, not independently attested crew provenance or
third-party reviewer assignment. It is useful eligibility evidence but does not satisfy F-102's
strong independence claim. It is not exercisable until #495 restores counted crew-internal
dispatch.

The strong path is affordable now: it uses the existing SQLite control plane, Program authority,
dispatch channel, and ADR-0039 marker. It adds no account, billable service, or long-lived secret.
Its trust is precise: it proves which direct child Program dispatched and that the recipient
possessed that dispatch's unpredictable marker. It does not claim GitHub authenticated that seat.

### B — Per-seat signing keys (rejected for the current tier)

A seat could sign the canonical receipt payload and the control plane could verify the public
key. This is cryptographically stronger only if private keys are injected through a confidential
per-seat channel and cannot be read by other seats. This environment has no demonstrated
per-seat secret store or authenticated tool-call principal; putting keys in the shared checkout,
environment setup, or SQLite would let another seat sign as the reviewer. Building secure key
provisioning, rotation, revocation, recovery, and audit solely for this property is not affordable
at today's scale. It becomes the next option if marker delivery cannot be kept seat-confined.

### C — Separate GitHub App installations or PATs (rejected for this environment)

Distinct GitHub principals would make author and reviewer identity directly observable at the
VCS boundary and are the strongest long-term answer. They require owner-created accounts or App
installations, credential provisioning and rotation, permission maintenance, and external state
that this repository cannot create autonomously. They are not available or affordable in the
current unattended environment. Adopt them when the owner supplies managed per-seat identities;
then retain control-plane attestations as defense in depth.

### D — Accept the gap and narrow D18 (rejected)

F-102 covers review requirements in the brief's protected set. D18 therefore forbids an
`ACCEPTED-RISK` disposition. Changing the prose would make the gate green by deleting the
promise, not by enforcing it. That is not a remediation.

### E — Store a caller-supplied reviewer field on the receipt (rejected)

This would look like enforcement and be inert. The author, reviewer, or Program could supply
any seat name, and all GitHub evidence would still resolve to the shared login. Mutable task
`crew` or `agent` columns have the same defect. Neither may be an authority input.

## Decision

Implement Option A as the minimum honest mechanism for reviews of Program-direct children. The
control plane will own two append-only facts: an author-dispatch attestation and a review
assignment. On the strong path both identities come from the authority-gated Program dispatch
target at dispatch/mint time, never from GitHub, comment content, `task upsert`, or receipt-record
arguments. On the weaker crew path, agent and title come from
`worker_slots.bound_agent`/`bound_title`, while crew comes from the acquiring lead's
`worker_slots.crew` declaration; `task_admissions` supplies the associated admission record.
The receipt must label that weaker provenance rather than presenting it as dispatch-bound crew
identity or third-party assignment.

The review assignment must bind at least:

- `author_task_id`, immutable author seat, derived author crew, and provenance strength;
- `review_task_id`, immutable reviewer seat, derived reviewer crew, and provenance strength;
- PR identity, exact `head_sha`, review `domain`, opaque marker, mapping/config digest, and
  minting Program-authority generation;
- explicit assignment state (`dispatched`, `consumed`, or `invalidated`) plus mint, consumption,
  and invalidation timestamps.

Marker minting must reject the same author and reviewer seat. It must additionally reject equal
crew for `cross_crew_review`. If a domain is used to prove different-vendor review, vendor must be
derived from each agent's own seat configuration and inequality must be checked; if that source is
not mechanically available, omit vendor from the invariant and do not claim different-vendor
proof. Re-dispatch invalidates the earlier unconsumed assignment.

The Program-authority generation at mint is recorded for audit but MUST NOT be required to equal
the live generation at receipt consumption. Authority TTL is 1800 seconds and generation advances
after every lapse; reviews routinely span that boundary (#571 is the default-case example).
Validity binds to task, exact head SHA, domain, and assignee. Requiring generation equality would
make a correctly dispatched review unrecordable merely because Program renewed its authority.

Requiring live Program authority for `review marker-mint` is approved as an explicit amendment to
ADR-0044 section 3b's fenced set. ADR-0044 fenced two CLI attachment points; marker mint becomes
the third. This is intentionally narrow: no other review or receipt command becomes
authority-gated by implication. Remove this third fence if marker minting moves into a dispatcher
event that already proves the same Program authority generation and exact assignment atomically;
do not keep two ceremonies for the same proof.

`review receipt-record` must, in the same `BEGIN IMMEDIATE` transaction as ADR-0039's final
marker recheck and receipt write, resolve exactly one unconsumed assignment, repeat the required
seat/crew/vendor inequalities that its provenance can prove, consume it, and store its attestation
id, provenance strength, and digest on the receipt.
A missing, ambiguous, stale, mismatched, legacy, or already-consumed binding is exit 3 and writes
nothing. Consolidation and DoD validation count only receipts with valid bindings; old receipts
are historical content evidence and are never backfilled from mutable task rows. Recovery is a
fresh independently dispatched review of the current head.

This closes accidental self-review and relabelling under the present trust boundary only if the
opaque marker is disclosed solely to the dispatched reviewer. Implementation must first add a
regression test proving that dispatch delivery is seat-confined. If that test cannot be made true
with the deployed orchestrator, implementation stops: Option A would otherwise be only an
assignment record, not proof of performance, and F-102 remains open until Option B or C exists.

The eventual F-102 disposition in `docs/audit/FINDINGS.md` must remain `CONFIRMED-BUG` and say,
in substance: **fixed by integrated PR #N; RT-N proves that a strongly attested receipt for a
Program-direct-child review cannot count unless an authority-attested, different-seat (and where
required different-crew/different-vendor) dispatch for the exact task/head/domain was consumed
atomically.** The implementation PR and a newly allocated real RT id must replace both `N`
placeholders. F-102 closes on this strongly attested half only. The disposition must explicitly
exclude `pm_acceptance` and crew-internal `code_review`/`qa`, whose worker-slot/admission binding
is weaker lead pre-commitment. This design PR alone must not be cited as the fix and cannot close
D18.

## Consequences

Review independence becomes mechanically checkable without pretending the shared GitHub login
is meaningful. Assignment, evidence, and exact code head become one auditable chain, and mutable
task metadata cannot launder self-review.

The control plane gains schema, dispatcher-policy, receipt, consolidation, validator, and
migration surface. In particular, the marker assignment row and its consumption state are
required product state, not optional audit decoration. `review_dispatch_markers` has no
consumption state today, so the control plane cannot distinguish dispatched-but-never-recorded
from never-dispatched; that is why #571 remains invisible until release refuses a candidate. The
same assignment/state row must drive #571's `review eligibility --pr` pre-flight.

Existing receipts cannot satisfy the new predicate, so affected heads need fresh reviews. The
mechanism also depends on confidential one-use marker delivery within the existing trusted
orchestration boundary; it is not a defense against a malicious Program or colluding seats that
deliberately share capabilities.

D18 cannot close honestly in the environment as it exists today. It can close after this
mechanism is implemented, the seat-confinement regression passes, an RT id is captured, the
implementation PR is integrated, and F-102 names that integrated PR and RT. If seat confinement
is unavailable, D18 requires per-seat signing keys with real secret isolation or distinct GitHub
credentials and must remain FAIL until one lands.

## Cutover sequencing

Issue #571 must land before ADR-0072's implementation task #573. First deploy the assignment-state
row and `review eligibility --pr` pre-flight so every in-flight candidate can expose missing,
dispatched, consumed, or invalidated review state before release. Then land #573's strong receipt
predicate and require fresh bound reviews. Reversing that order would invalidate every legacy
receipt at once while leaving the missing-work state invisible, applying #571's late-release cost
to the entire in-flight set. PR #564 is already in exactly that state today and is the concrete
cutover rehearsal, not a hypothetical edge case.

## Cost

At current volume and 100 times current review volume, direct monthly infrastructure cost is
zero beyond the existing host and SQLite database. Storage and transaction work grow linearly
with review assignments and receipts. The operational cost is one additive migration, config
digest/version handling, invalidation and recovery tooling, and regression coverage for
concurrency and capability leakage. Per-seat key or GitHub credential operations are explicitly
not added at this tier.

## One-way door?

No. The tables and receipt columns are additive. A later per-seat signature or GitHub principal
can be added as a stronger attestation, followed by fresh reviews; old evidence is already
fail-closed and need not be rewritten.

## Scale tier

Tier 0: the existing single-host SQLite control plane. Move identity attestation to a stronger
adapter only when measured lock contention causes review persistence to miss its existing
latency budget or when dispatch moves across a trust boundary that cannot keep markers
seat-confined. Review count alone is not a reason to add infrastructure.

## Reversal trigger

Revisit Option A before implementation, or supersede this ADR after acceptance, if any one of
these is observed:

1. a regression proves a non-dispatched seat can obtain or spend another seat's review marker;
2. the dispatch event cannot provide a Program-authority-bound target seat independently of
   caller-controlled task metadata; or
3. managed per-seat GitHub identities or confidential signing keys become available.

For triggers 1 or 2, F-102 stays open and the project adopts Option B or C; it must not fall back
to a caller-supplied seat field. For trigger 3, the stronger VCS/signature identity becomes the
primary proof and these dispatch attestations remain defense in depth.
