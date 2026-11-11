# ADR-0049: A crew PM decomposes size:L work into S/M children before any charter

Date: 2026-08-30 · Status: proposed
Deciders: program · Proposers: program (single-author — see §Process)
Conversation: this Program session, task `220-build`, base_sha
`93b81e376c2bd0eb5f067bd8835f40cf5306a072` · Spec: GitHub issue #220
Supersedes: none · Amends: none (additive prose in
`program/agents/crew_alpha/agents/pm/config.yaml`,
`program/agents/crew_beta/agents/beta_pm/config.yaml`, and
`program/skills/roadmap/SKILL.md`; no `bin/nightshift-ctl` change, no schema
change, no change to ADR-0032's readiness semantics)

## Process — why this is single-author, and why that is not a WA-5 case

`docs/owners.md`'s `program/**` row: "no crew owns this... Changes require an
ADR." Issue #220 names exactly that row as its "Expected code seams" (the
roadmap skill and both crews' `pm` configs), same as issue #219/ADR-0048
before it. Edit leases `12`/`13` (holder `platform`, task `220-build`) were
acquired before dispatch. Lease `14` was minted against
`program/agents/crew_beta/agents/pm/config.yaml`, a path that does not exist
in this repo — crew_beta's PM agent is named `beta_pm`, not `pm`
(`program/agents/crew_beta/agents/beta_pm/config.yaml`; confirmed against
`program/agents/crew_beta/agents/` and against
`tests/test_ready_queue_allocation.py`'s own reference to that same file for
ADR-0048's priority-order check). Lease `15` was acquired against the correct
path before this file was edited; lease `14` is left to expire on its own TTL
rather than force-released, since force-releasing a lease this session did
not acquire is not this ADR's business.

This is not the WA-5 (`.omni/working-agreements.md` item 5) sterile-dispatch
fallback. Program authoring its own operating prose directly is its ordinary
job (`program/config.yaml`'s Hard Rule 1: "You author Markdown only"), and
the one non-prose artifact this ADR's implementation adds — a focused
text-conformance regression test — follows the established
`tests/test_ready_queue_allocation.py` / `tests/test_review_contract.py`
pattern, owned the same way: by whatever touches the prompt it drives
(`docs/owners.md`, "ownership by what the test DRIVES, not by filename").

Per the standing rule this ADR is routed to `staff` for review before either
crew builds on it, and reported back to the user with the PR number rather
than routed directly — cross-crew review this time goes to `crew_beta`,
since `crew_alpha` reviewed #219/ADR-0048.

## Context

Issues #211/#212 (ADR-0031/ADR-0032) gave the control plane a mechanical
answer to "is this task runnable right now" — and a mechanical refusal for
anything sized `L`: `nightshift-ctl task ready` never returns a pending `L`
row; its `reason_code` is `needs_split`. Issue #219/ADR-0048 then made
Program's own charter step consult that query and stop chartering
GitHub-prose-only candidates. Neither closed the actual gap issue #220
names: **there was, and is, no planning rule for turning an `L` item into
executable work.** `needs_split` is a permanent dead end today — nothing in
`program/config.yaml`, `program/skills/roadmap/SKILL.md`, or either crew
`pm`'s Definition of Ready says who splits an `L` item, into what, or how the
pieces get registered. Without a rule, an `L` item either rots unstarted
forever, or someone bypasses the size gate the same way #211/#212 exist to
prevent (a "just charter it anyway, it's important" exception).

**A live precedent already exists**, done ad hoc before this rule was
written down: issue #174's Phase 2 implementation (`.omni/ledger.md`,
2026-08-29 checkpoint 7) hit exactly this — `slot acquire` on task
`174-implement` (size L) was denied with `reason_code: needs_split` — and the
crew lead split it manually into `174-implement-authority` (M, no
dependency, dispatched first), `174-implement-fence` (M,
`depends_on=174-implement-authority`, both because it consumes the
primitive Task A builds and because both tasks touch `bin/nightshift-ctl` —
"anything sharing a file goes sequentially, not in parallel worktrees"), and
`174-implement-detector` (S, file-disjoint, no dependency edge, built and
merged independently). That is precisely the shape issue #220's own worked
example asks for (`A size:S`, `B size:M depends_on=A`, `C size:S
depends_on=B`) and precisely what should not have needed to be reinvented
live under pressure on a real build.

## Options considered

### A — Have Program (roadmap skill) perform the split itself at charter time (rejected)

Program is not the owner of any crew's backlog content — `docs/owners.md`
and every crew `pm` config make GitHub issues, PRDs, and Definition of Ready
exclusively the crew PM's. Program computing S/M child scope, acceptance
criteria, and exclusions for a crew's own item would violate "Staying in
your slice" from the other direction: it would mean Program re-ranking and
re-scoping a crew's backlog, which the existing configs are explicit is not
Program's call. It would also duplicate DoR authorship in two places that
must then agree. Rejected.

### B — Add a new automatic `nightshift-ctl` split mechanism (out of scope, rejected)

Issue #220's own "Not included" list rules this out explicitly
("critical-path optimizer," implicitly any mechanical auto-split — the
issue's Problem statement frames this as "PM/roadmap," i.e. prose
discipline, not new code). An automatic splitter would also need to
understand semantic file/interface boundaries a CLI cannot derive from a
GitHub issue body. Rejected, and out of scope.

### C — The owning crew PM decomposes `size:L` items into S/M children as a Definition-of-Ready gate, before any child (and never the parent) can reach `state:ready`; Program's roadmap skill routes a stuck `needs_split` candidate back to that PM the same way any other DoR failure returns to the queue (chosen)

This keeps backlog authorship exactly where it already sits (the crew PM
owns DoR, per every `pm`/`beta_pm` config's own "What you own" section) and
adds no new control-plane surface — `task upsert`/`task dependency-add`/
`task ready` are the same three calls ADR-0031/ADR-0032/ADR-0048 already
established and the TRACKER step already uses. It generalizes the #174
precedent above into a rule instead of leaving it as tribal knowledge from
one ledger entry. It reuses the crew PM's own DoR criteria 1-6 for every
child (issue AC3) rather than inventing a second acceptance-criteria
format, and reuses the existing `unblocks-other-work > ... > cleanup`
priority order without change.

## Decision

Option C. Three additive changes:

**1. Both crew `pm` configs** (`program/agents/crew_alpha/agents/pm/
config.yaml`, `program/agents/crew_beta/agents/beta_pm/config.yaml` —
identical prose in both, since this is a shared contract, not a per-crew
choice) gain:
- **Definition of Ready criterion 7**: an item's size must be S or M to
  reach `state:ready`; a `size:L` item is decomposed first, and the L parent
  itself never gets a `state:ready` label, a `size:S`/`size:M` relabel, or a
  `task upsert` row of its own (AC1, AC6).
- A new **"Decomposing size:L work"** section giving the mechanics: split
  into the smallest set of S/M children, each with a full standalone DoR
  (AC2, AC3); add a `task dependency-add` edge only where a real file/
  interface constraint forces order, matching the file-sharing-forces-
  sequential rule the #174 precedent already used live (AC4, AC5); do not
  cut children to mirror architecture layers unless each layer child is
  independently reviewable and mergeable on its own (AC7); leave the L
  parent open as a tracking container and sign it off against the AND of
  its children's own acceptance, never a single implementation PR (AC6).

**2. `program/skills/roadmap/SKILL.md`, step 5 (Charter each crew).** A
`needs_split` candidate the READY-queue gate (ADR-0048) already excludes is
now explicitly routed back to the owning crew's PM to decompose per
criterion 7 above, the same way any other DoR gap returns to the top of the
queue — rather than left as a silent, permanent omission from every future
charter pass. Only the resulting children, each with their own task row and
real dependency edges, can ever appear in the READY set; the L parent
cannot, by construction (ADR-0032's own `needs_split` reason code, unchanged
here).

**3. A focused regression test**, `tests/test_size_l_decomposition.py`,
follows the established stdlib text-conformance pattern
(`tests/test_ready_queue_allocation.py`, `tests/test_review_contract.py`)
over both crew `pm` prompts and the raw `program/skills/roadmap/SKILL.md`
text — this is prose an agent reads, not a function with a call graph, so a
text gate is the correct and only adequate seam, exactly as ADR-0048 reasoned
for its own equivalent test.

## Consequences

**Easier:** a crew PM facing an `L` item has a written procedure instead of
reinventing the #174 precedent from scratch under time pressure; Program's
charter step has an explicit action for a `needs_split` candidate instead of
silently starving it forever; every future L-split leaves the same
file/dependency reasoning the #174 ledger entry captured, in the DoR itself
rather than in one ledger paragraph nobody will find in six months.

**Harder:** nothing new operationally — no new CLI surface, no schema
change. A crew PM now does slightly more work per `L` item (writing N
standalone DoRs instead of one), which is the point: that work was already
required to make the item dispatchable at all, just previously undocumented
and inconsistently done.

**Foreclosed:** nothing this increment specifically adds. Per issue #220's
own "Not included" list — decomposing the entire top-level PRD, a new
project object/table, dynamic crew creation, a critical-path optimizer, and
any change to readiness/budget enforcement — none of that is introduced.
`nightshift-ctl task ready` still refuses `L` mechanically and unconditionally
(ADR-0032, untouched); this ADR only says what a human-authored PM does in
response, which is prose, not a control-plane change.

## Cost

No monthly cost or new operational surface at today's volume or at 100×:
this is prose plus reuse of three pre-existing CLI calls (`task upsert`,
`task dependency-add`, `task ready`) that already exist from #210/#211/#212.

## One-way door?

No. Prose in two crew PM configs and one Program skill; a future ADR can
revise or supersede this with no migration and no public contract at stake.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — no infrastructure, cost, or
throughput characteristic changes.

## Reversal trigger

If a future ticket needs the split itself to be computed mechanically (an
automatic decomposer, explicitly out of this issue's scope) rather than
authored by a crew PM, or if the `<child-issue>-build` id convention this
ADR assumes needs to change, that is a new, explicit decision under a
superseding ADR, not a silent edit to this one.
