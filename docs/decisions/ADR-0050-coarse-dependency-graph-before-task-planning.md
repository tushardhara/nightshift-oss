# ADR-0050: Discovery draws a coarse dependency graph before task planning; seams stop implying crew count

Date: 2026-08-30 · Status: proposed
Deciders: program · Proposers: program (single-author — see §Process)
Conversation: this Program session, task `221-build`, base_sha
`4e44b1fec23e1e461aae575973820131a50480f8` · Spec: GitHub issue #221
Supersedes: none · Amends: none (additive/rephrasing prose in
`program/skills/discovery/SKILL.md`, `program/skills/roadmap/SKILL.md`, and
`program/config.yaml`'s embedded prompt; no `bin/nightshift-ctl` change, no
schema change, no change to ADR-0031/ADR-0032/ADR-0048/ADR-0049's readiness
or size:L-decomposition semantics — this reuses that mechanism, unmodified)

## Process — why this is single-author, and why that is not a WA-5 case

`docs/owners.md`'s `program/**` row: "no crew owns this... Changes require
an ADR." Issue #221 names exactly that row as its "Expected code seams"
(the discovery and roadmap skills, plus `program/config.yaml` "only if its
current discovery rule still equates seam count with crew count" — it did,
at `program/config.yaml` lines 127-128 pre-change), same pattern as issue
#219/ADR-0048 and issue #220/ADR-0049 before it. Edit leases 16/17/18
(holder `platform`, task `221-build`) were acquired before dispatch against
exactly the three files this ADR changes.

This is not the WA-5 (`.omni/working-agreements.md` item 5) sterile-dispatch
fallback, for the same reason ADR-0049 gave: Program authoring its own
operating prose is its ordinary job (`program/config.yaml`'s Hard Rule 1),
and the one non-prose artifact this ADR's implementation adds — a focused
text-conformance regression test — follows the established
`tests/test_ready_queue_allocation.py` / `tests/test_review_contract.py` /
`tests/test_size_l_decomposition.py` pattern, owned the same way: by
whatever touches the prose it drives.

Per the standing rule this ADR is routed to `staff` for review before either
crew builds on it, and reported back to the user with the PR number.
Cross-crew review round-robin: `crew_beta` reviewed #220/PR #366 most
recently, so `crew_alpha` is next.

## Context

Issue #220/ADR-0049 answered "how does an oversized item become executable
work" (decompose `size:L` into S/M children before any engineer is
chartered). It never answered a question one level up: how does a LARGE
BRIEF — the whole project, before any milestone-1 issue exists — avoid
turning into hundreds of implementation tasks on day one? Today, discovery
Step 4 ("shape the org to the seams") goes straight from the reconciled
architecture to "open the milestone-1 issues," and nothing between the PRD/
milestone plan and issue creation says the brief should first be reduced to
a small, ordered map of product/outcome slices. A large brief can therefore
drown Program in generated backlog before the first slice has even shipped,
losing the order in which product areas actually unlock each other.

A second, narrower defect compounds the first: `program/skills/discovery/
SKILL.md` Step 4 and `program/config.yaml`'s embedded prompt both currently
read "architect's SEAM COUNT sets how many crews can actually run. Two
seams means two crews, whatever the roster holds." That reads seam count as
DETERMINING org size, when the actual fixed input is the roster
(`tools.agents`: `crew_alpha`, `crew_beta` — 2 crews today, independent of
the brief). A future brief with three or more seams would, read literally,
imply "charter a third crew" — exactly the dynamic-crew-creation issue #221
explicitly excludes.

## Options considered

### A — Introduce a new planning artifact/table for the graph (rejected)

Issue #221's own "Not included" list rules out a new DB/table explicitly.
A markdown section inside the already-generated `.omni/goal.md` carries the
same information with no new storage surface, no migration, and no new
read/write path — `.omni/goal.md` is already the file every heartbeat
re-reads (`program/config.yaml`'s Persistence and HEARTBEAT sections).
Rejected.

### B — A scheduler/optimizer computes the actionable slice and dependency order automatically (out of scope, rejected)

Issue #221 excludes both a scheduler daemon and a critical-path optimizer
explicitly. The coarse graph is small (a handful of nodes) precisely because
discovery draws it by hand from the PRD/architecture it already produced,
the same way it already hand-writes milestones — no algorithm is needed at
this scale, and building one would be exactly the "sophisticated
critical-path optimizer" the issue excludes. Rejected, and out of scope.

### C — Discovery draws a small coarse dependency graph of outcome-shaped slices as a new step (3b) immediately after `.omni/goal.md` is generated and before any issue is opened; only the currently-actionable node(s) get issues, and roadmap scopes every wave's charters to that graph; seam count is reworded everywhere it appears to inform ownership/boundaries rather than set crew count (chosen)

This keeps the existing PRD/DoD/milestone plan as the product truth
(`docs/prd/**`, unchanged) and layers a thin execution/dependency view on
top, generated from those same artifacts — never a second PRD, per the
issue's AC8. It reuses the exact `size:L` → PM-decomposition mechanism
#220/ADR-0049 already built: a coarse node, once actionable, is filed as one
`size:L` issue and decomposed by its owning crew's PM through the unchanged
existing procedure — no new decomposition mechanism, only a new trigger
(dependency-graph unlock) for the same one. Seam-count language is reworded
in both places it appears (`program/skills/discovery/SKILL.md`,
`program/config.yaml`) to say what was already true in practice — crew
count is fixed by the roster — closing the literal-reading gap above without
changing today's actual crew count (still 2).

## Decision

Option C. Three changes, all additive or rephrasing, no behavior change to
`bin/nightshift-ctl` or the control plane:

**1. `program/skills/discovery/SKILL.md`** gains **Step 3b — draw the
coarse dependency graph**, run immediately after Step 3 writes
`.omni/goal.md` and before any implementation planning. A coarse node is a
product/outcome slice (never a source file, crew name, or implementation
step, AC2); edges are `depends_on` between outcomes (never between crews,
AC3); the graph is written into `.omni/goal.md` as its own section,
generated from the same PRD/DoD/milestones (never a replacement PRD, AC8);
only currently-actionable node(s) get GitHub issues opened (AC1, AC4, AC5).
Step 4 ("shape the org to the seams") is reworded: crew count is fixed by
the roster regardless of seam count or graph shape (AC6); seams inform
ownership/dependency boundaries between the crews that already exist, never
grounds to mint or retire one (AC7); the boundary map and issue-opening are
scoped to the currently-actionable node(s), never the whole graph (AC5).

**2. `program/skills/roadmap/SKILL.md`** step 1 now reads the graph from
`.omni/goal.md` and scopes every wave's charters to its currently-actionable
node(s) (AC1, AC4, AC5). Step 5 (Charter each crew) notes that a coarse node
is typically filed as one `size:L` issue on arrival, hitting the pre-existing
`needs_split` → PM-decomposition gate from #220/ADR-0049 unmodified — the
coarse graph changes WHICH issue exists and WHEN, never how an oversized
issue turns into S/M work. A Notes-section addition states explicitly that
an idle crew never gets a head start decomposing a still-gated node (AC5).

**3. `program/config.yaml`'s embedded PROGRAM prompt.** The Phase 0
discovery paragraph names the coarse graph as part of discovery's output.
The "Two rules from discovery" block is reworded from "seam count sets how
many crews can actually run" to "seams inform ownership and dependency
boundaries between the crews that already exist; they never set how many
crews exist... regardless of seam count or graph shape" (AC6, AC7) — this
is the one config.yaml change issue #221 conditions on "its current
discovery rule still equates seam count with crew count," confirmed true
pre-change.

**4. A focused regression test**, `tests/test_coarse_dependency_graph.py`,
follows the established stdlib text-conformance pattern
(`tests/test_size_l_decomposition.py`, `tests/test_ready_queue_allocation.py`)
over the raw `program/skills/discovery/SKILL.md` and
`program/skills/roadmap/SKILL.md` text and `program/config.yaml`'s parsed
embedded prompt — this is prose Program reads, not a function with a call
graph, so a text gate is the correct and smallest seam, exactly as
ADR-0048/ADR-0049 reasoned for their own equivalent tests. Confirmed red
against the pre-change (base SHA) files, green after.

## Consequences

**Easier:** a large brief no longer forces Program to choose between
generating an unmanageable full backlog or leaving later product areas
completely unplanned — the coarse graph gives an explicit, small, ordered
answer, reusing every mechanism (`.omni/goal.md`, GitHub issues, the
`size:L` DoR gate) that already existed. The seam-count reword removes a
literal misreading that pointed toward dynamic crew creation, which #221
and prior ADRs already exclude.

**Harder:** discovery now performs one additional hand-drawn step per
project (or per brief revision) — a handful of nodes and edges, not
meaningfully more effort than the milestone plan it is derived from.

**Foreclosed:** nothing this increment adds. Per issue #221's own "Not
included" list — a new DB/table, a scheduler daemon, dynamic crew creation,
multi-repo planning, a critical-path optimizer, product-specific logic, and
a UI/Kanban redesign — none of that is introduced here.

## Cost

No monthly cost or new operational surface at today's volume or at 100×:
this is prose plus reuse of pre-existing GitHub issue creation and the
`size:L` decomposition mechanism from #220/ADR-0049.

## One-way door?

No. Prose in two Program skills and one embedded prompt; a future ADR can
revise or supersede this with no migration and no public contract at stake.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — no infrastructure, cost, or
throughput characteristic changes.

## Reversal trigger

If a future project genuinely needs the coarse graph computed or optimized
mechanically (an automatic critical-path scheduler, explicitly out of this
issue's scope) rather than hand-drawn by discovery, or if graph nodes need
their own control-plane object rather than living as a section of
`.omni/goal.md`, that is a new, explicit decision under a superseding ADR,
never a silent edit to this one.
