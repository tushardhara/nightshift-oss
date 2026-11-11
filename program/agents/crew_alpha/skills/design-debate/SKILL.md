---
name: design-debate
description: MANDATORY first phase. Before any implementation, have the PM write the spec, two different-vendor explorers propose competing approaches independently, reconcile the disagreement, and record the call as an ADR. The ADR is the gate; unattended runs proceed straight into build.
---

# design-debate — decide before building

The failure mode this prevents: the first model's first idea silently becomes the
architecture, and nobody can reconstruct why six months later. Two vendors
proposing independently, then arguing, is cheap. A wrong foundation is not.

Run this for every charter. A trivial task gets a compressed version — one
explorer, three sentences of comparison, a two-line ADR — but never zero.

## Procedure

1. **Spec first, from your own PM.** Dispatch `pm` (`purpose: "explore"`) with the
   charter. It writes the item and `docs/prd/<slug>.md` or `docs/specs/<slug>.md`
   with numbered, checkable acceptance criteria and applies Definition of Ready.
   Start nothing until criteria exist: competing approaches to an unstated problem
   are noise, and a worker spent on a vague criterion is a worker wasted.
   For user-facing work the design (`docs/design/`) comes from the shared `ux`
   function via the program — ask for it before criteria are finalised, and do not
   let your PM invent interaction design in its place.

   Persist the mechanically ready task before any counted planning turn:
   `nightshift-ctl task upsert --id <task_id> --crew crew_alpha --agent
   <chosen implementer> --title <task_slug> --issue <n> --state pending
   --size <S|M>`. A missing/L/blocked task is not ready for this workflow.

   Acquire a classified planning slot for `qa` with title `test-<slug>`, then send it
   the criteria (`purpose: "explore"`) for the test
   plan. A criterion QA cannot figure out how to test is a spec bug, and you want
   that finding before anyone writes code. Collect its plan and release that
   slot before step 2; today's global planning cap deliberately cannot hold QA
   plus both proposal workers at once.

2. **Independent proposals, in parallel, different vendors.** Dispatch BOTH:
   First acquire one slot per proposer with `nightshift-ctl slot acquire --holder
   crew_alpha/<agent> --crew crew_alpha --task <task_id> --agent <agent>
   --title design-<slug>-<a|b> --purpose explore`. Pass `task_id`, `slot_id`,
   `generation`, and `purpose` from each grant in the matching send.
   - `sys_session_send(agent="eng_claude", title="design-<slug>-a", args={purpose: "explore", task_id: "<task_id>", slot_id: <id>, generation: <generation>, input: <brief>})`
   - `sys_session_send(agent="eng_codex", title="design-<slug>-b", args={purpose: "explore", task_id: "<task_id>", slot_id: <id>, generation: <generation>, input: <brief>})`

   Give each the SAME brief and never show one proposer the other's output —
   correlated proposals defeat the exercise. The brief asks each for: the
   approach, the files it would touch, the migration or rollout path, what it
   would deliberately NOT do, the two biggest risks, roughly how it splits into
   parallel-safe tasks, and one alternative it considered and rejected with the
   reason.

   Dispatch both in the same turn, then end the turn. Collect with
   `sys_read_inbox`, then release their planning slots. NEW remains denied until
   every planning slot for the task is released or expires.

3. **Reconcile.** You compare. Produce a written comparison with a row per
   decision point: what A said, what B said, and which you take. Where they
   agree, say so and move on — agreement across vendors is your cheapest
   confidence signal. Where they disagree, that is the real design question:
   name the tradeoff explicitly rather than splitting the difference. If the
   disagreement turns on a fact about the codebase, dispatch one narrow
   `purpose: "search"` explore to settle it instead of guessing.

4. **Write the ADR** to `docs/decisions/ADR-NNNN-<slug>.md`. Number sequentially
   from the highest existing ADR. Required sections:

   ```
   # ADR-NNNN: <decision in one line>
   Date: YYYY-MM-DD · Status: accepted · Deciders: <crew> lead · Proposers: eng_claude, eng_codex
   Conversation: <conversation id>  ·  Spec: docs/specs/<slug>.md

   ## Context
   What forced a decision. Constraints that were real on this date.

   ## Options considered
   ### A — <name> (proposed by eng_claude)
   ### B — <name> (proposed by eng_codex)
   ### C — <name> (rejected early)
   For each: what it is, and the specific reason it won or lost.

   ## Decision
   What we are doing.

   ## Consequences
   What this makes easy, what it makes hard, what it forecloses.

   ## Reversal trigger
   The observable condition that would make us revisit this.
   ```

   The rejected options and the reversal trigger are the sections future readers
   need most. Never drop them for brevity. Record the constraint that drove the
   call ("Codex-only vendor available that week", "the DB had no index on X"), so
   a later reader can tell whether the reason still holds.

5. **Commit the plan.** Unattended mode: there is no human to approve, so post
   the comparison, the ADR link, and the task split into the conversation AND the
   ADR, then proceed to implementation in the same turn. Any A/B disagreement you
   settled on your own judgment must be named explicitly in the ADR — that is the
   record a human overrules later. A choice that is expensive to reverse does not
   get made here: take the reversible half, record the call in `.omni/decisions.md`, and
   name what you gave up.

6. **Carry the decision forward.** Every implementation dispatch's contract
   quotes the ADR id and the criteria it satisfies. A PR that references no ADR
   is not reviewable a year from now.

## Notes

- Both proposers ran read-only, so this phase costs reads, not writes. It is the
  cheapest phase in the pipeline; do not skip it to save budget.
- If only one vendor is available (see the lead's preflight), run one proposal
  and state plainly in the ADR that no independent second opinion was obtained,
  then continue — but flag it in `STATUS.md`, because a whole wave built without
  cross-vendor review is the human's most likely reason to re-open the work.
- Reopening a settled decision: never edit an accepted ADR's Decision in place.
  Write a new ADR that supersedes it and link both ways — the trail of what we
  used to believe is the whole value.
