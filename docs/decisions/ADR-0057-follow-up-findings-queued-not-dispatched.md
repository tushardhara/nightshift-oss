# ADR-0057: FOLLOW_UP findings are filed and queued, never dispatched into the current fix packet

Date: 2026-08-31 · Status: accepted · Deciders: program · Proposers: program
(single-author — see §Process)
Conversation: this Program session, task `222-build`, base SHA
`f95d543bd9154e65750258682f5aa8c54987b693` · Spec: GitHub issue #222, accepted
DoR contract recorded on the issue
(`https://github.com/tushardhara/nightshift/issues/222#issuecomment-5477574014`)
Supersedes: none · Amends: none (additive prose in `program/config.yaml`'s
`prompt:` block, `program/agents/crew_alpha/config.yaml`'s and
`program/agents/crew_beta/config.yaml`'s `prompt:` blocks, and one reinforcing
sentence in `program/skills/cross-crew-review/SKILL.md`; no
`bin/nightshift-ctl` change, no schema change, no new database, scheduler, or
general subsystem)
Depends on: ADR-0026 (frozen review contract and `MERGE_BLOCKER` /
`PRE_DEPLOYMENT` / `FOLLOW_UP` severity vocabulary), ADR-0039 (persisted
review receipts, the `fingerprint` / `consolidate_findings` mechanism reused
here verbatim), ADR-0048 (the READY-queue gate that already makes AC-3 and
AC-4 structurally true for any newly filed issue)

## Process — why this is single-author, and why that is not a WA-5 case

`docs/owners.md`'s `program/**` row: "no crew owns this... Changes require an
ADR." This work item was chartered directly to this Program session — not to
`crew_alpha` or `crew_beta` — because its four expected code seams
(`program/config.yaml`, `program/agents/crew_alpha/config.yaml`,
`program/agents/crew_beta/config.yaml`,
`program/skills/cross-crew-review/SKILL.md`) are exactly that row, and the
owner's accepted DoR comment on #222 names this implementation path
explicitly: "no crew owns this, ADR required... A future Program-authored
turn implements this directly (mirrors ADR-0047/ADR-0048/ADR-0056)." Edit
leases 34-37 (holder `platform`, task `222-build`) were acquired for the four
files above before any of them was touched; they auto-release when the PR
opens.

This is not the WA-5 (`.omni/working-agreements.md` item 5) sterile-dispatch
fallback — no worker dispatch occurs at all here. Program authoring its own
operating prose directly is Program's ordinary job
(`program/config.yaml`'s own Hard Rule 1: "You author Markdown only"), the
same precedent ADR-0048/ADR-0056 established for the same `program/**` row.
The one non-prose artifact this ADR's implementation adds — a focused
text-conformance regression test plus a literal fixture reproducing the
in-round dedup case — follows the established
`tests/test_449_dor_queue_terminalization.py` / `tests/test_review_contract.py`
pattern and is owned the same way, by whatever touches `program/config.yaml`'s
prompt, per `docs/owners.md`'s "ownership by what the test DRIVES, not by
filename" rule.

## Context

PR #200 (ADR-0026) already classifies unrelated or scope-expanding review
findings as `FOLLOW_UP`, distinct from `MERGE_BLOCKER` and `PRE_DEPLOYMENT`.
What was missing was an explicit scheduling rule that discovering a
`FOLLOW_UP` during review cannot itself consume engineering capacity in the
current delivery loop — issue #222's own Impact section names the failure
mode directly: "a useful unrelated finding can still trigger opportunistic
investigation/implementation and pull the active task back into scope
expansion."

The owner's DoR pass on #222 (this session), corroborated in parallel by an
independent `architect` (Codex) feasibility read and a `staff` (Claude)
adversarial read (both READY), found that most of the issue's acceptance
criteria were **already true** structurally:

- AC-1 (separate backlog item via the existing path) —
  `program/skills/tracker/SKILL.md` already routes non-blocking findings to a
  labelled, `pm`-ranked issue via `needs-dor`.
- AC-2 (never blocks Release) — ADR-0026 /
  `program/skills/cross-crew-review/SKILL.md`'s "Routing the verdict" already
  states: "`FOLLOW_UP` findings → a separate work item, not another review
  loop."
- AC-4 (later normal scheduling) — ADR-0048 already gates all normal
  implementation allocation through `nightshift-ctl task ready`'s five-tag
  order; no ad hoc dispatch path exists for a freshly filed issue.
- AC-5 (`MERGE_BLOCKER`/`PRE_DEPLOYMENT` unchanged) — untouched by this
  ticket by construction (see §Decision — the edit only narrows what is
  routed to the implementer, never what qualifies as `MERGE_BLOCKER` or
  `PRE_DEPLOYMENT`).
- Most of AC-3 (no auto-spawned Architect/Staff/Security/crew/engineer work)
  — structurally guaranteed by ADR-0048's READY gate: a freshly filed
  `needs-dor` issue cannot receive an implementation charter.

The genuine remaining gap: `program/config.yaml`'s step 7b operational
instruction — the compressed text an agent actually executes, distinct from
the fuller and already-correct `cross-crew-review/SKILL.md` prose — said the
review owner "emits one consolidated fix packet per round using exactly
`MERGE_BLOCKER`, `PRE_DEPLOYMENT`, and `FOLLOW_UP`" without ever restating
that only `MERGE_BLOCKER` returns to the implementer on the current PR. That
compressed phrasing, not the fuller skill prose, is the literal mechanism
behind the issue's stated failure mode: an eager implementer patching a
`FOLLOW_UP` sitting in the same packet as its `MERGE_BLOCKER` siblings. The
identical compressed phrasing was duplicated verbatim in
`program/agents/crew_alpha/config.yaml` and
`program/agents/crew_beta/config.yaml`'s "Reviewing another crew's PR"
sections.

Separately, AC-6 as originally worded ("Duplicate FOLLOW_UP findings should
not create obvious duplicate active work for the same finding when the
existing review contract already identifies it") was ambiguous between an
in-round case (the same finding surfaced by two or more review domains in one
round's consolidated findings) and a cross-PR/cross-time case (the same
finding recurring across separate rounds or separate PRs). Only the first is
buildable inside this ticket's stated seams: `nightshift-ctl review
consolidate` (ADR-0039) already deduplicates findings by content
`fingerprint` — which deliberately excludes `domain` — so multiple domains
reporting the identical `FOLLOW_UP` already collapse to one consolidated
entry with a `domains: [...]` list, not one entry per domain. The
cross-PR/cross-time case would require a new read verb over
`review_findings` in `bin/nightshift-ctl`, which is `crew_alpha`-owned
(Seam A) per `docs/owners.md` — out of scope for a `program/**`-solo,
size:S item, and named as an explicit non-goal on the accepted DoR contract.

## Options considered

No competing vendor proposals exist in the usual dual-draft sense (see
§Process — `architect` and `staff` reviewed Program's own DoR draft in
parallel rather than each drafting an independent implementation, since this
is prose Program itself operates under). The options below are the real
design forks the DoR pass and this build resolved.

### A — Narrow the implementer-facing fix packet in `program/config.yaml` step 7b and mirror it in both crew configs and the skill, no new subsystem (chosen)

Satisfies the issue's own "Expected code seams" list directly:
`program/skills/cross-crew-review/SKILL.md`, `program/config.yaml` (only
where Program routes follow-up findings), and a focused regression test — no
`bin/nightshift-ctl` change, no schema change. Placement: a new clause
inside step 7b's existing sentence about the consolidated fix packet, and an
equivalent new bullet inside each crew config's "Reviewing another crew's PR"
section, immediately after the existing "combine every domain verdict..."
bullet — deliberately adjacent to, not replacing, the existing severity
vocabulary sentence the pre-existing `tests/test_review_contract.py` already
pins in place.

### B — Build a new `nightshift-ctl` command or table to track filed FOLLOW_UP issues and prevent re-filing (rejected, out of scope)

The accepted DoR contract rules this out directly: AC-6 is explicitly
scope-limited to the in-round case, reusing ADR-0039's existing mechanism;
cross-PR/cross-time dedup "would need a new crew_alpha-owned
`bin/nightshift-ctl` read verb — a separate wider ticket, not this one."
`review_findings`/`review_receipts` (ADR-0039) and their `fingerprint`
column already exist and already sit under `crew_alpha`'s seam-A ownership;
adding a second, overlapping duplicate-tracking mechanism at the
`program/**` layer would duplicate state that must now agree with the
existing table, without fixing anything the in-round case does not already
cover.

### C — Where to put the MERGE_BLOCKER-only clause: a new step 7c, or inline inside step 7b (chosen: inline)

A new numbered step was rejected because the constraint is not a new phase
of the review process — it narrows an action (`consolidated fix packet`)
step 7b already names. Splitting it into its own step would require every
implementer-facing action of 7b to be re-read across two non-adjacent
sections to understand the packet's actual contents, inviting exactly the
drift the issue describes. Keeping it inline, immediately after the
sentence it qualifies, keeps the full definition of "consolidated fix
packet" in one place.

### D — Whether the in-round dedup check belongs in `program/config.yaml`/crew configs, in `cross-crew-review/SKILL.md`, or both (chosen: both, skill as reinforcement only)

The independent `staff` DoR read found `cross-crew-review/SKILL.md`'s
existing "Routing the verdict" prose already correct on AC-1/AC-2/AC-4 and
did not require an edit for AC-6 to be satisfiable — the skill already names
`nightshift-ctl review consolidate`-shaped consolidation as the review
owner's job elsewhere in the same file (`consolidate every domain verdict`).
The one-line addition made here is optional polish per the accepted
contract, added anyway so a reader of the skill file alone (not just the
compressed `program/config.yaml` prompt) sees the same dedup pointer,
without duplicating the full mechanical rule the config files now state in
full.

## Decision

Option A + C + D. Four additive changes, all inside `program/**`:

**1. `program/config.yaml`, step 7b** — one new clause inserted into the
existing sentence describing the review owner's consolidated fix packet:
states (a) the implementer-facing fix packet is `MERGE_BLOCKER`-only; (b)
`FOLLOW_UP` is filed via the existing tracker path (`needs-dor`) and left
untouched on the current PR; (c) it does not spawn
Architect/Staff/Security/crew/engineer work in the current delivery loop;
(d) before filing, `nightshift-ctl review consolidate` is run for the
round's head SHA and its consolidated findings checked, so a `FOLLOW_UP`
already present there (same fingerprint, contributed by two or more domains)
files as at most one backlog issue; (e) this is in-round dedup only —
cross-PR/cross-time duplicate detection is explicitly out of scope.

**2. `program/agents/crew_alpha/config.yaml` and
`program/agents/crew_beta/config.yaml`** — the identical new bullet
(verbatim, both files, to avoid drift) inside "## Reviewing another crew's
PR", immediately after the existing "You are the review owner: combine every
domain verdict..." bullet, restating the same `MERGE_BLOCKER`-only /
in-round-dedup rule for the crew lead acting as review owner.

**3. `program/skills/cross-crew-review/SKILL.md`** — one reinforcing
sentence appended to the existing "`FOLLOW_UP` findings → a separate work
item, not another review loop" line in "Routing the verdict", pointing at
the same `nightshift-ctl review consolidate` in-round dedup check. Optional
polish per the accepted contract, not required to satisfy the ACs — the
file's prior text was already correct.

**4. `tests/test_follow_up_queueing.py` (new)** — text-conformance
assertions isolating the new prose in all three prompt-bearing files by
marker (established `_section`/marker-isolation pattern from
`tests/test_449_dor_queue_terminalization.py`), asserting each of: the
`MERGE_BLOCKER`-only fix-packet clause, the separate-tracker-path routing
for `FOLLOW_UP`, the no-auto-spawn clause, and the `nightshift-ctl review
consolidate` in-round dedup check — present in `program/config.yaml`,
mirrored identically in both crew configs, and reinforced in the skill file.
Plus a literal fixture (`consolidate_for_filing`) reproducing AC-6's own
scenario: the same `FOLLOW_UP` finding surfaced by two or more review
domains in one round's `consolidate_findings` output collapses to exactly
one filing decision, never one per contributing domain, while a distinct
second `FOLLOW_UP` fingerprint still files as its own separate issue, and
`MERGE_BLOCKER`/`PRE_DEPLOYMENT` entries in the same consolidated set are
never routed through the FOLLOW_UP-filing path at all.

## Consequences

**Easier:** the failure mode #222 describes — an implementer opportunistically
patching a `FOLLOW_UP` because it rode along in the same fix packet as real
blockers — is now precluded by an explicit, textually-enforced instruction at
the exact seam (`program/config.yaml` step 7b) an agent actually executes,
not only in the fuller skill prose. A `FOLLOW_UP` surfaced identically by
multiple review domains in one round no longer risks becoming multiple
backlog issues for the same underlying defect.

**Harder:** nothing new operationally. No new `nightshift-ctl` surface, no
schema change, no new label taxonomy — `needs-dor` and the existing
`MERGE_BLOCKER`/`PRE_DEPLOYMENT`/`FOLLOW_UP` vocabulary (ADR-0026) are reused
verbatim.

**Foreclosed:** nothing this increment specifically adds. Cross-PR/cross-time
FOLLOW_UP dedup remains out of scope, per the accepted DoR contract's explicit
non-goal — it would need a new `crew_alpha`-owned (Seam A) read verb over
`review_findings`, a separate, wider-decision ticket if ever chartered.
Severity definitions, review ownership, repair-round logic, task-graph/
readiness semantics, and Kanban redesign are all untouched, per the issue's
own "Not included" list.

## Cost

No monthly cost or new operational surface at today's volume or at 100×: this
is prose plus one new stdlib text-conformance test file, reusing an
already-persisted table (`review_findings`, ADR-0039) read-only.

## One-way door?

No. Prose in Program's own operating instructions and two crew configs, plus
a one-line skill reinforcement; a future ADR can revise or supersede this
with no migration and no public contract at stake.

## Reversal trigger

If a future retro finds the `MERGE_BLOCKER`-only fix packet too rigid — for
example, a `FOLLOW_UP` that genuinely must block the same PR for a reason
not captured by the existing severities — revise via a superseding ADR, never
a silent edit to this one's Decision section. If cross-PR/cross-time FOLLOW_UP
duplication becomes a real, observed cost, charter the separate
`crew_alpha`-owned ticket named in §Options B rather than reopening this one.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — no infrastructure, cost, or
throughput dimension is touched by this change.
