---
name: discovery
description: Turn the owner's brief into a PRD, an architecture, a numbered definition of finished, and a milestone plan — three blind drafts (product on Claude, architecture on Codex AND on Claude), a cross-vendor critique of the definition of done before it binds, then reconciliation decision by decision with an explicit tie-break order. Runs once, before any crew is chartered.
---

# discovery — from a paragraph to something finishable

The owner writes `.omni/brief.md` and nothing else. No goal, no completion
condition, no plan. Producing those is this phase's job, and it is the highest
leverage work the org ever does: everything downstream optimises toward the
definition of done written here, so an hour spent getting it right saves days of
confidently building the wrong thing.

Nothing in this phase asks the owner a question. Ambiguity gets resolved in the
direction most likely to be right, and the assumption gets written down where they
can see it.

## Step 1 — three independent reads, same brief, nobody sees anybody

Dispatch all three in the same turn, then end the turn:

- `product` (Claude) → `docs/prd/PROJECT.md`, the definition of done, the milestones.
- `architect` (Codex) → `docs/architecture/PROPOSAL-codex.md`: shape, data model,
  stack, seams, tier picks, hard parts, cost, feasibility.
- `staff` (Claude) → `docs/architecture/PROPOSAL-claude.md`: the SAME brief, its own
  independent architecture. During discovery `staff` is not a reviewer — it is the
  Claude-side architect, competing with the Codex one.

This phase is the ONE place the reasoning tier is spent: `product` already runs
on Fable as a seat, and `staff`'s blind draft — this dispatch only, never its
recurring ADR reviews — gets `args.model: fable` if `.omni/models.json` lists it
(omitting `model` is always safe; an invalid pair fails loud). Size both
dispatches for the 2× price: `cost_budget: {max_cost_usd: 200–267}` where a
Claude-tier dispatch would get 8. Everything after discovery runs the cheaper
tiers — the one-time answers here are what the whole run optimises toward, which
is exactly why the expensive model earns its price here and nowhere else.

Give each ONLY the brief and the repo. Never show one another's draft, and never
summarise one to another — a reviewer anchors on what it is shown, and two drafts
softened toward each other are one draft with extra steps. Collect from
`sys_read_inbox`.

Capacity first: whichever architect reports numbers, both must have written
`docs/architecture/CAPACITY.md`-grade arithmetic before naming any technology. If a
draft names a datastore or a queue without the numbers behind it, send that one back
before comparing — a stack chosen before arithmetic is how a project pays for scale
it does not have.

## Step 1b — the definition of done gets a hostile read before it binds

The definition of done is single-author (`product`) and nothing downstream
re-checks it — every later gate optimises TOWARD it. So before it becomes the
project's fixed point, it gets the one control single-author work can get: an
independent critique from the other vendor.

Dispatch `architect` (Codex, `purpose: "review"`) with ONLY two inputs —
`.omni/brief.md` and the draft `docs/prd/DEFINITION-OF-DONE.md`. Not the PRD,
not product's reasoning: the critique must judge the artifact, not be argued
into the author's frame. Its checklist, finding by finding:

- **Coverage** — does every promise in the brief map to a condition? Name what
  does not.
- **Gaps** — what would obviously be missing at handover that no condition
  catches?
- **Contradictions** — conditions that cannot all be true at once.
- **Verifiability** — any condition with no command, query, or observable that
  settles it. "Works well" is a wish, not a condition.
- **Scope creep** — conditions the brief never asked for.
- **Assumptions stated as facts** — and whether each is written down as an
  assumption the owner can see.
- **Decidability** — for each condition: could a stranger, with the repo and
  the condition alone, answer met/not-met without asking anyone?

`product` reconciles: accept, or reject with one line of reasoning per
finding — never silently ignore one. You settle anything they cannot, and the
DoD's changelog records what the critique changed. This is a critique gate,
not co-authorship: `product` remains the single author of the final text, on
its own vendor, which is exactly why the read comes from the other one.

## Step 2 — reconcile the two architectures, decision by decision

Do not pick a winning document. Go through the decisions one at a time — data model,
stack, boundaries, tier per concern, what to build first — and take the better answer
from either draft. The merged result is usually neither proposal intact, and that is
the expected outcome.

Write the comparison into `.omni/decisions.md`: one row per decision point, what
Codex said, what Claude said, what you took, and why.

**Where they agree**, you are done thinking — cross-vendor agreement is the cheapest
confidence available. Note it and move on.

**Where they disagree**, use this order and stop at the first rule that decides it:
1. **Numbers.** Does the capacity model settle it? If one draft's choice is only
   justified at volumes this project will not see for a year, the other wins.
2. **Fewer moving parts.** Prefer the design with less to run, patch, monitor and
   pay for. Every component is a permanent operational tax.
3. **Reversible over optimal.** Prefer the choice that can be switched later behind
   an interface. An almost-right decision you can undo beats a perfect one you cannot.
4. **Cheaper to run**, when the two are otherwise equivalent — including the cost of
   the operational surface, not just the bill.
5. **Already in the repo.** An existing convention beats a marginally better new one.
6. **Debuggable.** Prefer the failure mode you can actually diagnose at 3am from
   logs the project already has.

If it survives all six, it is a genuine judgment call: take the cheaper and more
reversible option, record BOTH positions in the ADR verbatim, and name the trigger
that would make you switch to the other. Never average two designs — an averaged
architecture has the drawbacks of both.

**Scope clashes** (product wants more than the architects think is buildable):
feasibility wins on *can*, product wins on *what matters*. The resolution is almost
always fewer things done properly — cut scope, do not stretch the schedule.

**A contradiction in the brief** — either architect may report one — is never papered
over. Write it down, take the reading that makes the project finishable, say what you
dropped, and continue. That note is the first thing the owner should see.

## Step 2b — finalize, in writing

The debate ends in artifacts, not a conversation:

- `docs/architecture/OVERVIEW.md` — the merged design, owned by `architect` from here
  on. It must read as one coherent document, not a diff of two drafts.
- `docs/architecture/SCALE-LADDER.md` — the agreed tier per concern with each
  trigger, and `Now at: T0` for nearly everything.
- **ADR-0001 (stack and shape)** and one ADR per irreversible decision either draft
  flagged as a one-way door. Each carries both positions, the rule that decided it,
  the cost now and at 100×, and the reversal trigger.
- Keep both `PROPOSAL-*.md` files in the repo, unedited. In six months the question
  "did anyone consider X?" is answerable in seconds, and the answer is not a
  reconstruction.

From here, architecture changes follow the same discipline in miniature: any ADR that
changes the shape or jumps a ladder tier gets a blind second opinion from the OTHER
vendor's architect before it is accepted — `architect` and `staff`, opposite sides,
same rule.

## Step 3 — write the goal yourself

`.omni/goal.md` is GENERATED here, never hand-authored:

```
Outcome: <one sentence, from the PRD>
Definition of finished: docs/prd/DEFINITION-OF-DONE.md — N numbered conditions
Milestones: docs/prd/MILESTONES.md — M milestones, each with exit criteria
Derived from: .omni/brief.md on <date> — product, architect (Codex) and staff
              (Claude); reconciled in ADR-0001
Assumptions the owner has not confirmed: <numbered, one line each>
```

Then create the HOURLY HEARTBEAT — `sys_scheduled_task_create` with `FREQ=HOURLY`
— and confirm it exists with `sys_scheduled_task_list` before ending the turn.

That pair is what carries the objective across turns and days: the file holds
*what finished means*, the heartbeat is *what brings you back to it*. Do NOT reach
for Omnigent's Goal mode — it is Codex-only (the server rejects it for any
non-`codex-native` session) and the program runs `claude-sdk`. The file-plus-timer
pair is also strictly more durable: it survives a session drop, a service restart,
and a rebuild onto a new box, which a harness-internal goal does not.

## Step 3b — draw the coarse dependency graph

Immediately after `.omni/goal.md` is generated, and BEFORE any implementation
planning — not even milestone 1's own S/M tasks — draw the brief's COARSE
dependency graph: a small handful of outcome-shaped project slices, ordered by
`depends_on` edges. This is required for every brief, not just a large one; a
small brief simply draws a graph with one or two nodes. A slice is what the
milestone plan already groups toward, not a second decomposition of it —
usually one slice per early milestone or a small cluster of them.

```text
P1 Foundation
P2 Core engine      depends_on P1
P3 Application      depends_on P2
P4 Analytics        depends_on P2
P5 Delivery          depends_on P3
```

A coarse node is a PRODUCT/OUTCOME slice — what becomes true for a user or an
operator — never a source file, a crew name, or an implementation step. An
edge is between two outcomes ("Application needs Core engine to consume"),
never between two crews: crew ownership is a boundary decision made later
(Step 4 below, and roadmap's own boundary-mapping step), not a property of
the graph itself.

Write the graph into `.omni/goal.md` as its own section, one line per node
(`id, name, depends_on`), plus which node(s) are CURRENTLY ACTIONABLE — no
unmet `depends_on` — since that is the field the `roadmap` skill's step 1
reads to scope every wave. The graph is an execution/dependency VIEW
generated from the same PRD, DoD and milestone plan already written above;
it never replaces or restates them, and it gets no database row or table of
its own — it is a file, exactly like the rest of `.omni/goal.md`.

Only the currently-actionable node(s) get GitHub issues opened, at Step 4
below. A node still gated by an unmet `depends_on` gets no issues yet, no
implementation detail beyond what the milestone plan already carries, and no
crew attention — generating the whole project's implementation backlog for
P2-P5 before P1 ships is exactly the drowning failure mode this graph exists
to prevent. When a node's dependency is later satisfied (its prerequisite
milestone closes), open ITS issues then, same rule, never earlier.

Re-running this step follows Step 3's own discipline: a materially different
brief, or a milestone review that proves a dependency wrong, gets a dated
note in `.omni/decisions.md` and an updated graph — never a silent redraw.

## Step 4 — shape the org to the seams, not to the ambition

Crew count is FIXED by the roster (the `crew_*` entries in your tool list),
regardless of how many seams the reconciled architecture has or how many
nodes the coarse graph has. The RECONCILED design's seam count — and if the
two drafts disagreed about the seams, that is settled here and nowhere
else — informs OWNERSHIP and dependency boundaries between the crews that
already exist; it is never grounds to mint or retire one. Map seams onto the
fixed roster: a crew with no seam or no actionable slice to own this wave
gets a grant of zero and stays there until the graph unlocks work for it,
and you say so in the report — crews queueing on the same files look busy
and deliver nothing.

Write the boundary map into `.omni/goal.md`, one row per crew: slice, owned
paths, interfaces owned and consumed — scoped to the currently-actionable
coarse node(s) from Step 3b, never the whole graph. Add the matching rows to
`docs/owners.md`, and open issues for ONLY that actionable slice so the
board is populated before the first wave starts; a slice still behind an
unmet `depends_on` gets no issues yet.

## Step 5 — one report, then start

Post to the pinned status issue, in this order:

1. What you are building, in two sentences.
2. What "finished" means — the number of conditions, and the milestone list.
3. What you decided that the brief did not say, and what you assumed.
4. What you cut, and why.
5. The one thing most likely to be wrong.

Then charter milestone 1 in the same turn. Do not wait for a reply: if the owner
disagrees they will say so, and correcting course after milestone 1 is cheap — which
is exactly why milestone 1 must produce something demonstrable rather than
scaffolding.

## Re-running discovery

Only when the brief itself changes, or a milestone review proves the definition of
done was wrong. Then it is a revision with a recorded reason, never a rewrite:
supersede the ADR, changelog the PRD, and never quietly edit a DoD condition — that
erases the only fixed point the project has.
