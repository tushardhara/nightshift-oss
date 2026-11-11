# ADR-0056: DoR queue terminalization and mechanical issue-selection order

Date: 2026-08-31 · Status: accepted · Deciders: program · Proposers: program
(single-author — see §Process)
Conversation: this Program session (`run_id fixture-review-231`, task
`449-build`, `base_sha d7f17599b7997f064a67923770b9d0e6e4308cc3`) ·
Spec: GitHub issue #449, accepted DoR contract recorded on the issue
Supersedes: none · Amends: none (additive prose in `program/config.yaml`'s
`prompt:` block and `program/skills/tracker/SKILL.md`; no `bin/nightshift-ctl`
change, no schema change, no new database, scheduler, or general release
subsystem)

## Process — why this is single-author, and why that is not a WA-5 case

`docs/owners.md`'s `program/**` row: "no crew owns this... Changes require an
ADR." This work item was chartered directly to this Program session — not to
`crew_alpha` or `crew_beta` — precisely because its two expected code seams
(`program/config.yaml`, `program/skills/tracker/SKILL.md`) are exactly that
row, and the owner's accepted DoR comment on #449 names this implementation
path explicitly ("A future Program-authored turn implements this contract...
no crew owns `program/**`, so Program builds it directly — ADR-0047/ADR-0048
precedent. Not chartered to `crew_alpha`/`crew_beta`."). Edit leases 27/28/29
(holder `platform`, task `449-build`) were acquired for `program/config.yaml`,
`program/skills/tracker/SKILL.md`, and the new test file before any file was
touched.

This is not the WA-5 (`.omni/working-agreements.md` item 5) sterile-dispatch
fallback ADR-0043/0045/0046/0047 each invoked — issue #236 (`sys_session_send`
rejecting `slot_id`/`task_id`/`generation` for `COUNTED_AGENTS` targets) is
irrelevant here because no worker dispatch occurs at all: Program authoring
its own operating prose directly is Program's ordinary job
(`program/config.yaml`'s own Hard Rule 1: "You author Markdown only"), exactly
as ADR-0048 established for the same `program/**` row. The one non-prose
artifact this ADR's implementation adds — a focused text-conformance
regression test plus a minimal literal-predicate fixture — follows the
established `tests/test_budget_expired_operating_rule.py` /
`tests/test_ready_queue_allocation.py` pattern exactly, and is owned the same
way: by whatever touches `program/config.yaml`'s prompt, per `docs/owners.md`'s
"ownership by what the test DRIVES, not by filename" rule.

## Review

`staff` reviewed this ADR and its implementation PR (#451) per Hard Rule 5.
Round 1 verdict: inconsistent-with ADR-0048 — one real finding (§Options E),
one non-blocking FOLLOW_UP (a tautological assertion in the new test file,
fixed in the same round). `security` (sensitive-set scan, triggered by
`program/config.yaml`/`SKILL.md` being protected "constitution" paths):
clean. Round 1 fix applied directly (single-author, same as the rest of this
ADR); round 2 re-review confirms clean before this reaches `release`.

## Context

Issue #449's own evidence: Program finished a Definition-of-Ready pass on
issue #423, correctly proving under the current rules that #423 could not yet
advance — and then left #423 labelled `needs-dor`, so the next heartbeat
selected #423 again. This repeated three times before the temporary sequencer
stopped on `no_durable_github_progress`. Moving #423 to `state:blocked`
immediately allowed #424 to be selected next — proving the fix, but only as a
one-off manual intervention, not a durable rule.

`pm`'s DoR pass on #449 (this session), corroborated in parallel by an
independent `architect` (Codex) feasibility read (verdict READY-WITH-CHANGES,
4 findings) and a `staff` (Claude) adversarial review (verdict
inconsistent-with-as-drafted, resolved by incorporating the findings — not a
needs-a-wider-decision case), confirmed the actual root cause is broader than
a missing terminalization rule: `program/config.yaml` had **no** durable rule
at all for how the next `needs-dor` candidate is chosen — the
`P0 > P1 > P2 > from-owner, oldest number; exclude needs-owner/state:blocked`
order existed only as ad hoc, inconsistently-renumbered convention inside
`.omni/decisions.md` and inside one-off manually-dispatched heartbeat
overrides, never promoted into `program/config.yaml` or
`program/skills/tracker/SKILL.md` (`staff` finding A). Separately,
`tracker/SKILL.md`'s existing `state:blocked` guidance ("Dropped after three
loops... label `state:blocked` only if it genuinely needs something
unobtainable") reads narrower than #449's intended usage — same underlying
meaning ("the org cannot presently obtain/decide something"), but its
documented example was scoped to PR-review-loop drops only (`architect`
finding 4).

## Options considered

No competing vendor proposals exist in the usual dual-draft sense (see
§Process — `architect` and `staff` reviewed Program's own draft in parallel
rather than each drafting an independent one, since this is prose Program
itself operates under, not a system component with two plausible designs).
The options below are the two real design forks the reconciliation
surfaced.

### A — Two additive prose seams (`program/config.yaml` bullet + `tracker/SKILL.md` mirror) plus one regression fixture, no new subsystem (chosen)

Satisfies AC-7 directly: reuses GitHub issue labels (`needs-dor`,
`state:ready`, `state:blocked`, `needs-owner`) and the existing
Program/PM selection contract. The queue-selection order and terminalization
rule are genuinely missing prose, not a reword of anything already there
(confirmed live: `program/config.yaml` had zero durable statement of the
selection order before this ADR). Placement: one new bullet in
`program/config.yaml`'s `UNATTENDED MODE` section, immediately after the
existing `NEVER IDLE WITH THE GOAL UNMET...` bullet and before
`HEARTBEAT — THIS IS THE AUTONOMY MECHANISM...`. This is deliberate: it does
not touch the `HEARTBEAT` bullet's `nightshift-ctl issue closable` /
`gh issue close` clause (ADR-0055's separate edit target) or any
ADR-0044/#174 authority-fence prose in the same section (`staff` finding,
placement) — satisfying AC-8 by construction rather than by a separate
carve-out.

### B — A new `nightshift-ctl` subcommand or queue table to track DoR-pass state (rejected, and out of scope)

The issue's own AC-7 rules this out directly ("No new queue database or
scheduler subsystem is introduced. Reuse GitHub state labels and the existing
Program/PM selection contract."). The failure #449 describes is not a data
tracking gap — the real, live label state on GitHub was already accurate
(`gh issue view 423` → already `state:blocked` today) — it is a missing
*durable instruction* for Program to read and act on that state consistently.
A new subsystem would duplicate label state in a second place that must now
agree with GitHub, without fixing the actual defect (undurable prose).

### C — A closed `dor_reason` taxonomy (enum of fixed blocking reasons) vs. free text (chosen: free text)

`architect`'s finding 3 flagged that a closed taxonomy would be scope creep
beyond #449's stated fix, and `staff` separately flagged a vocabulary-collision
risk: ADR-0032's control-plane `reason_code` is already a closed, schema-level
vocabulary (`budget_expired`, `already_extended`, `not_budget_expired`, …);
introducing a second closed vocabulary at the GitHub-label layer under a
near-identical name (`reason_code` vs `dor_reason`) risks a future reader
misreading one layer's reason as the other's. Free text with a distinct key
name (`dor_reason:`) avoids both problems and matches the existing latitude
ADR-0047's `BLOCK` action and the pre-existing "Dropped after three loops"
rule already have — neither of those is a closed enum either.

### D — Where the new bullet edits `tracker/SKILL.md`'s existing `state:blocked` guidance: replace it, or extend it in place (chosen: extend in place)

Rejected replacing the existing "Dropped after three loops" bullet's own
text, since it remains accurate and independently useful (a PR-review-loop
drop is a real, distinct trigger for `state:blocked`, not superseded by
#449's broader trigger). Instead, one clarifying sentence is appended stating
the shared underlying meaning, and a new `### Queue selection order`
subsection is added transcribing the full mechanical order and
terminalization rule for the tracker skill's own audience.

### E — Step 2's `P0`>`P1`>`P2` triage vs ADR-0048's five-tag pooled-READY-set
    order: two orders for one candidate pool, or one order with two scopes
    (chosen: one order, two scopes, reconciled explicitly)

Caught by `staff`'s review of this ADR's implementation PR (#451, round 1):
the original draft's step 2 claimed to run "before a task row exists in the
control plane" for `state:ready` issues too, but ADR-0048's own TRACKER-step
mirroring already gives a `state:ready` issue a task row at the moment it is
labelled — that claim is only true for step 3 (`needs-dor`). More
consequentially, ADR-0048's PLAN AND ALLOCATE step already locks the ranking
to use whenever more READY items exist than a wave has grant for — the
five-tag order (`unblocks-other-work > user-visible-breakage >
committed-outcome > new-value > cleanup`), explicitly "reused verbatim, never
reinvented." Step 2's `P0`>`P1`>`P2` triage orders substantially the same
candidate pool (GitHub `state:ready` issues, which by construction are
`task ready`'s registered set) by a different vocabulary, with no stated
relationship to the five-tag order — a real ADR-vs-ADR inconsistency, not a
cosmetic one, since the two orders can disagree on which issue goes first.

Rejected: dropping `P0`/`P1`/`P2` from step 2 entirely and reusing the
five-tag order verbatim there too. This would work but throws away a useful,
already-labelled triage signal (`P0`/`P1`/`P2` is set at issue-creation time
by the `tracker` skill's own `gh issue create` convention, cheap to read at a
single-item heartbeat pickup) for no benefit when there is no batch to rank
against — the five-tag order's own inputs (`unblocks-other-work` etc.) are
themselves whole-backlog judgments a `pm` applies deliberately, not always
available or meaningful for a single ad hoc heartbeat pick between one
`state:ready` issue and another.

Chosen instead: name the two orders' scopes explicitly rather than merging or
duplicating them. Step 2's `P0`>`P1`>`P2` governs the coarser, single-pick
heartbeat/sequential-turn case (this ADR's whole subject: which ONE issue
does Program work on next, outside a wave-boundary batch allocation).
ADR-0048's five-tag order continues to govern, unchanged, the finer-grained
case it already owned — ranking WITHIN a capacity-constrained charter batch
at PLAN AND ALLOCATE (step 4) — and wins whenever both could apply to the
same decision (multiple crews, capacity-constrained, step 4 actually
running). Corrected: the "runs before a task row exists" framing now scopes
explicitly to step 3 only.

## Decision

Option A + C + D. Three additive changes, all inside `program/**`:

**1. `program/config.yaml`** — one new bullet, `QUEUE SELECTION IS MECHANICAL,
AND A COMPLETED DOR PASS ALWAYS TERMINALIZES` (#449, ADR-0056), inserted
between the end of the `NEVER IDLE WITH THE GOAL UNMET...` bullet and the
start of the `HEARTBEAT` bullet. States: (a) this is the coarser,
single-pick GitHub-issue-label layer Program reads at each heartbeat to
choose ONE next item, distinct in scope from both ADR-0032/ADR-0048's
`nightshift-ctl task ready` (registered-task dispatchability) and step 4 PLAN
AND ALLOCATE's five-tag pooled-READY-set order (capacity-constrained batch
ranking, reused verbatim, never reinvented — see §Options E — and winning
whenever both could apply to the same decision); (b) the mechanical
four-step selection order (open delivery PR > oldest `state:ready` by
`P0`>`P1`>`P2`, single-pick heartbeat triage only > oldest `from-owner
needs-dor` > exclude `needs-owner`/`state:blocked`); (c) same-turn
terminalization to exactly one of `state:ready` (with the accepted contract
recorded in a comment) or `state:blocked` (with a comment carrying free-text
`dor_reason:` and `unblock_condition:` fields); (d) explicit, never-silent
reconciliation back to `needs-dor`; (e) GitHub's own label-and-comment
history is the durable record, `.omni/decisions.md` an optional echo. The
"runs before a task row exists" framing is scoped to the `needs-dor` branch
only — a `state:ready` issue may already have a task row via ADR-0048's own
TRACKER-step mirroring.

**2. `program/skills/tracker/SKILL.md`** — a `state:blocked` transition
example next to the existing `state:ready` one in the `## Issue lifecycle`
code block; a new `### Queue selection order (#449, ADR-0056)` subsection
transcribing the same order and terminalization rule for this skill's own
readers; one clarifying sentence appended to the existing "Dropped after
three loops" bullet generalizing its `state:blocked` trigger to an
exhaustively-reviewed DoR pass, not only a PR-review-loop drop.

**3. `tests/test_449_dor_queue_terminalization.py` (new)** — text-conformance
assertions isolating both new prose blocks by their markers (established
`tests/test_budget_expired_operating_rule.py` /
`tests/test_ready_queue_allocation.py` pattern), plus a minimal executable
fixture: a literal `is_dor_candidate(labels) -> bool` predicate mirroring the
prose's own filter (not a general priority sorter) and a `next_candidate(...)`
helper implementing exactly the "oldest-numbered ... carrying needs-dor"
selection clause. A canned fixture reproduces the real #423/#424 label
history (`gh issue view 423`/`424`, confirmed live this session): asserts
`is_dor_candidate(423) is False`, `is_dor_candidate(424) is True`,
`next_candidate(...) == 424`, that this holds across two further simulated
no-change heartbeats (AC-5), that a defensive double-label
(`needs-dor` + `state:blocked` together) still predicates `False` (blocked
wins), and that explicit reconciliation reopens candidacy (AC-6).

## Consequences

**Easier:** the #423/#424 loop this issue observed cannot recur silently — an
exhaustively-reviewed `needs-dor` issue now has a durable, textually-enforced
instruction to terminalize the same turn, and `state:blocked` is durably
excluded from re-selection. The selection order itself is now readable in
`program/config.yaml` and `program/skills/tracker/SKILL.md` instead of living
only in `.omni/decisions.md` narrative or tribal knowledge.

**Harder:** nothing new operationally. No new `nightshift-ctl` surface, no
schema change, no new crew-config edits, no new label taxonomy beyond the
four labels already in use (`needs-dor`, `state:ready`, `state:blocked`,
`needs-owner`).

**Foreclosed:** nothing this increment specifically adds. #395's broader
owner-intent consistency gate and #424's issue-closure redesign (the
`nightshift-ctl issue close` gateway is a separate primitive, confirmed
non-colliding by placement above) are both out of scope, per the issue's own
"Not included" list. `bin/nightshift-ctl` is untouched. #174's authority-fence
prose and the recurring-automation scheduled-task mechanism are untouched
(verified: the new bullet's insertion point sits strictly between two
existing, unedited bullets).

## Cost

No monthly cost or new operational surface at today's volume or at 100×: this
is prose plus one new stdlib text-conformance test file.

## One-way door?

No. Prose in Program's own operating instructions and a skill file; a future
ADR can revise or supersede this with no migration and no public contract at
stake.

## Reversal trigger

If a future retro finds the mechanical four-step order itself wrong (for
example, a fifth queue-worthy state emerges, or the free-text `dor_reason`
proves too unstructured to act on reliably), revise via a superseding ADR —
never a silent edit to this one's Decision section.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — no infrastructure, cost, or
throughput dimension is touched by this change.
