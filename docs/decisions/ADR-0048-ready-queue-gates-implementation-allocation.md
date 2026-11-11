# ADR-0048: The READY queue gates normal implementation allocation

Date: 2026-08-30 · Status: accepted
Deciders: program · Proposers: program (single-author — see §Process)
Conversation: this Program session (`run_id fixture-review-231`, task `219-build`,
checkpoint 18, `base_sha 57fc0d0c59e93598d9dc3066d86b0e446a6808e0`) · Spec: GitHub issue #219
Supersedes: none · Amends: none (additive prose in `program/config.yaml`'s
`prompt:` block and `program/skills/roadmap/SKILL.md`; no `bin/nightshift-ctl`
change, no schema change, no change to ADR-0032's readiness semantics)

## Process — why this is single-author, and why that is not a WA-5 case

`docs/owners.md`'s `program/**` row: "no crew owns this... Changes require an
ADR." This work item was chartered directly to this Program session — not to
`crew_alpha` or `crew_beta` — precisely because its two expected code seams
(`program/config.yaml`, `program/skills/roadmap/SKILL.md`) are exactly that
row. Edit leases `10`/`11` (holder `platform`, task `219-build`) were acquired
by the charterer *before* dispatch, closing the sequencing gap #218's own
retro flagged (lease-at-charter, not after-the-fact).

This is not the WA-5 (`.omni/working-agreements.md` item 5) sterile-dispatch
fallback ADR-0043/0045/0046/0047 each invoked — issue #236 is about
`sys_session_send` rejecting `slot_id`/`task_id`/`generation` for
`COUNTED_AGENTS` targets (engineer/QA/reviewer seats). Program authoring its
own operating prose directly is not a fallback from a broken dispatch path;
it is Program's ordinary job (`program/config.yaml`'s own Hard Rule 1: "You
author Markdown only"). The one non-prose artifact this ADR's implementation
adds — a focused text-conformance regression test — follows the established
`tests/test_budget_expired_operating_rule.py` / `tests/test_review_contract.py`
pattern exactly, and is owned the same way: by whatever touches
`program/config.yaml`'s prompt, per `docs/owners.md`'s "ownership by what the
test DRIVES, not by filename" rule, which for this platform-owned seam is
this session, not a crew's `eng_claude`/`eng_codex`.

## Context

Issues #211/#212 (ADR-0031/ADR-0032) gave the control plane a mechanical,
derived-at-read-time answer to "is this task runnable right now":
`nightshift-ctl task ready` returns exactly the `pending`, sized S/M,
acyclic, unblocked task rows, sorted by ID — "a deterministic set, not a
priority queue, reservation, or authorization to dispatch" (ADR-0032 §4).
Issue #219 observes that Program itself never asks. Charter decisions
(`roadmap` skill step 5, and the ad hoc "find the next ready item" clause in
`program/config.yaml`'s `NEVER IDLE` bullet) are made from `docs/prd/
MILESTONES.md` and crew backlog narrative alone — GitHub issue labels
(`needs-dor` → `state:ready`), never the control-plane graph. That means
Program can charter an item the mechanical graph would call blocked (a stale
`blocked-by:#<n>` label nobody removed) or miss a newly-unblocked item until
someone remembers to re-read prose.

**The DoR gap this ADR resolves.** Read literally, AC1 ("Program queries
`task ready` *before* normal implementation allocation") and AC2
("charters are created only for tasks returned as READY") require a task row
to exist, sized and with its dependency edges, *before* the charter that
would be its first mention anywhere in the control plane. But the only
existing writer of `task upsert --state pending --size` is the *owning
crew*, and it writes that row during Phase 1 (`program/agents/crew_alpha/
config.yaml`, "Persist the pending S/M task first") — which is *after*
Program has already chartered the crew for that item. Querying `task ready`
at charter time, with nothing written yet, would always return an empty set
for brand-new work and silently defeat the whole mechanism — a literal
reading of AC1/AC2 that makes the feature charter nothing, ever, which is
plainly not the issue's intent (its own worked example assumes A/B/C/D/E
already exist as rows with edges *before* Program chooses among them).

## Options considered

### A — Leave task registration where it is (crew-owned, at Phase 1); Program's `task ready` query is therefore advisory only, not gating (rejected)

This satisfies nothing: with no row until after charter, `task ready` can
never return an item Program hasn't already chartered, so "charter only from
that set" (AC2) is vacuously true and the query has no effect on any actual
decision. Rejected as not meeting the issue's own literal AC1/AC2 text, and
not what the worked example describes.

### B — Move task registration wholesale to Program, replacing the crew's own Phase 1 `task upsert` call (rejected)

Would require editing `program/agents/crew_alpha/config.yaml` and
`program/agents/crew_beta/config.yaml` — files issue #219's own "Expected
code seams" does not name, and a materially larger diff than "the smallest
current scheduling seam" the issue asks for. It also duplicates writer
responsibility for the same field (size) in two places that must now agree,
without gaining anything B doesn't already get from C below.

### C — Program registers a *pending* task row (id, issue, size, dependency edges) the moment a candidate passes DoR, at the existing TRACKER step, strictly *before* it is a charter candidate at PLAN AND ALLOCATE; the owning crew's later Phase 1 `task upsert` becomes an idempotent re-affirmation of the same row (same `--id`), not a competing write (chosen)

`nightshift-ctl task upsert` is idempotent per `--id` (verified live this
session: re-running it against `219-build` updates the existing row, it does
not create a duplicate or error). So Program writing the row first, and the
crew's existing Phase 1 call writing it again with the same `--id` once
building starts, is not a race or a double-write conflict — it's the same
row touched twice by two steps that already agree on the id, matching the
`<issue>-build` convention this repo already uses live (`218-build`,
`219-build` — this ADR's own task id). No change to
`program/agents/crew_*/config.yaml` is required: their existing `task
upsert --state pending --size` call is a no-op re-assertion when the id
already exists with the same fields.

Size comes from the issue's own `size:S/M/L` label — already required at DoR
(the `pm`'s Definition of Ready doesn't have a numbered size criterion, but
every backlog item is created with a `size:` label per `tracker/SKILL.md`'s
`gh issue create --label "<crew>,P1,size:M,needs-dor"` convention, and issue
#219 itself carries `size:S`). Dependency edges come from the *existing*
`blocked-by:#<n>` GH label (`tracker/SKILL.md`, "Dependencies are declared,
not remembered") — no new dependency vocabulary. Registration for a
dependent item is deferred (not double-write-races, just doesn't happen yet)
until its prerequisite has its own task row: `nightshift-ctl task
dependency-add --depends-on <id>` needs that id to exist, and adding an edge
to a not-yet-existing id would either fail (if the control plane validates
the target) or silently create a phantom expectation (if it doesn't) —
either way, safer to leave the dependent unregistered (absent from `task
ready`'s whole universe, exactly as blocked as `blocked_dependency` would
read) than to guess. This is the one place this ADR is deliberately
conservative rather than clever.

### D — Compute an ad hoc, program-local notion of "ready" from GitHub labels instead of the control-plane graph (rejected, and out of scope)

This is exactly the "narrative planning/backlog state" the issue's own
Problem statement says Program should stop doing. It would also silently
duplicate ADR-0032's cycle-detection and precedence logic in prose, which
issue #219's own "Not included" list rules out ("changing control-plane
readiness semantics" — reimplementing it out-of-band is the same defect by
another name).

## Decision

Option C. Two additive changes:

**1. `program/config.yaml`, TRACKER step (1b).** As soon as a candidate
passes DoR (`state:ready`), mirror it into the control plane:
`nightshift-ctl task upsert --id <issue>-build --crew <owning crew> --issue
<n> --state pending --size <S|M|L>` (size from the issue's own label), then
`nightshift-ctl task dependency-add --id <issue>-build --depends-on
<prereq>-build` for each `blocked-by:#<prereq>` label whose *own* task row
already exists; a `blocked-by:` label whose prerequisite has no row yet
leaves this item unregistered rather than mis-encoded.

**2. `program/config.yaml`, PLAN AND ALLOCATE step (4), and the `NEVER
IDLE` bullet.** Before any NORMAL implementation charter (both at the wave
boundary and at an ad hoc mid-wave allocation), run `nightshift-ctl task
ready` fresh and charter only from the ids it returns (AC1, AC2). Rank a
READY set larger than the wave's grant by the pre-existing order —
`unblocks-other-work > user-visible-breakage > committed-outcome >
new-value > cleanup`, already `program/agents/crew_*/agents/pm/config.yaml`'s
own tag vocabulary, reused verbatim rather than reinvented (AC3). This
selects *which* task; it composes with, and never replaces, the existing
`slot acquire` capacity gate — a READY task still queues if the semaphore is
full (AC6). If the normal-implementation READY set is empty, Program does
not invent unrelated work: it does already-required review/QA/handoff work,
or waits, or reports the blocker as the first line of its next status
report — never a busywork substitute (AC4). Because eligibility is derived
at read time (ADR-0032), a prerequisite reaching `integrated` makes its
dependent reappear in the very next `task ready` read with no prose to
rewrite (AC5).

**3. `program/skills/roadmap/SKILL.md`**, steps 4–5: mirror declared
dependencies into `task dependency-add` once both sides have rows, and
charter only what `task ready` returns; the charter template gains a `Task
ID:` line carrying the exact id from that call, so the owning crew's later
Phase 1 `task upsert` reuses it rather than inventing a new one.

A focused regression test, `tests/test_ready_queue_allocation.py`, follows
the established stdlib text-conformance pattern
(`tests/test_budget_expired_operating_rule.py`, `tests/test_review_contract.py`)
over the parsed `program/config.yaml` prompt and the raw
`program/skills/roadmap/SKILL.md` text — this is prose Program reads, not a
function, so a text gate is the correct and only adequate seam.

## Consequences

**Easier:** Program has one deterministic query to run before every normal
charter instead of re-deriving "is this actually unblocked" from GitHub
label prose each time; a stale `blocked-by:` label can no longer cause a
charter for work the graph would call blocked, and a newly-integrated
prerequisite unlocks its dependent without anyone touching a file.

**Harder:** nothing new operationally. No new `nightshift-ctl` surface (`task
upsert`, `task dependency-add`, `task ready` all pre-exist from #211/#212);
no schema change; no new crew-config edits.

**Foreclosed:** nothing this increment specifically adds. The issue's own
"Not included" list — automatic `L` decomposition, big-PRD decomposition, a
critical-path algorithm, dynamic crew creation, changed readiness semantics
— is untouched: `L` items still read `needs_split` and Program still does
not act on that itself; priority among READY items is still a five-tag
lookup applied by Program's own judgment, not a computed ranking function.

## Cost

No monthly cost or new operational surface at today's volume or at 100×:
this is prose plus two pre-existing CLI calls added to two existing steps.

## One-way door?

No. Prose in Program's own operating instructions; a future ADR can revise
or supersede this with no migration and no public contract at stake.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — no infrastructure, cost, or
throughput characteristic changes.

## Reversal trigger

If a future ticket needs Program to rank READY items by something other than
the existing five-tag order (a computed critical-path weight, for instance —
explicitly out of this issue's scope), or if issue registration needs to
move off the crew-agnostic `<issue>-build` id convention this ADR assumes,
that is a new, explicit decision under a superseding ADR, not a silent edit
to this one.
