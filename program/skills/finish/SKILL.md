---
name: finish
description: Close a milestone, and eventually the project — verify every condition of done against merged code, produce the handover, and stop. The mechanism that makes this a project rather than an endless run.
---

# finish — a project that ends

An autonomous org with no stopping rule does not finish a project; it accumulates
one. `docs/prd/DEFINITION-OF-DONE.md` is the fixed point, and this skill is how the
org converges on it and then stops.

Run it at every milestone boundary, and once more at the end.

## Closing a milestone

1. **Verify against merged code, not against reports.** For each of this
   milestone's exit criteria, check it on the default branch as it now stands — run
   the command, read the test, exercise the path. A criterion that passed in a PR and
   is now broken by a later merge is not met.
2. **Get product's judgment.** Dispatch `product` with the milestone's criteria and
   the merged state. Its verdict per criterion is `met`, `not met`, or
   `met-differently-than-specified`. That third one matters most: the org solved the
   problem another way, and either the criterion updates (with a changelog line) or
   the work is not done.
3. **Demand the demonstrable thing.** Every milestone must end in something a person
   can see working. Write down exactly how to see it — the command, the URL, the
   screen, the query. "The API supports it" is not a demonstration.
4. **Reconcile the board and the docs.** Every issue in this milestone is closed,
   dropped with a reason, or moved to the next milestone explicitly. No issue silently
   carries over.
5. **Report** to the pinned status issue: what is now true that was not before, how
   to see it, what moved to the next milestone and why, and the remaining count of
   DoD conditions. That count going down every milestone is the only real progress
   signal.
6. **Then run `retro`** before chartering the next milestone, so what was learned
   changes how the next one runs.

## Closing the project

Only when every condition in the definition of done is met and verified on the
default branch. Not "substantially met" — every one, or a recorded decision that a
condition was dropped, with the reason.

Produce `docs/HANDOVER.md`, written for an engineer who has never seen the repo:

- **What this is and what it does** — two paragraphs.
- **How to run it** — from a clean clone, exact commands, with the expected output.
  Verify these commands actually work by running them from a fresh worktree; a
  handover with a broken setup path is worse than none.
- **How to see it work** — the demonstration for the core loop.
- **The map** — components, what each owns, and where to change what. Link
  `docs/architecture/OVERVIEW.md`.
- **How to test it** — the suites, what each covers, how long they take, and any
  test that is known-flaky with its rate.
- **Decisions that will confuse you** — the three or four ADRs a newcomer will
  otherwise want to undo, each with why it is that way and the condition under which
  it should change. Link, do not restate.
- **What is deliberately missing** — deferred scope, known limits, the debt register
  with its triggers.
- **Where the record lives** — ADRs, PRD, decisions log, review log, integration log,
  incidents, working agreements.

Then the final report: what was built against the original brief, what changed along
the way and why, the numbers (milestones, merged PRs, escaped defects, incidents,
reverts), what a person should do first with it, and what the next project would be.

## Then stop

Delete the hourly heartbeat task (`sys_scheduled_task_delete` — otherwise it wakes
the org every hour on a finished project and spends the owner's quota), write
`STATUS.md` down to a single line saying the project is complete and pointing at
the handover, and stop dispatching. An org that keeps
polishing after done is spending the owner's quota on work nobody asked for.

Three legitimate stop states, and each is written plainly in `STATUS.md`:

- **Complete** — every DoD condition met.
- **Complete with exceptions** — every condition met except those explicitly dropped,
  each with its recorded reason. Name them in the first line; do not let a dropped
  condition hide in a table.
- **Blocked** — a condition cannot be met without something outside the org's reach
  (a credential, a purchase, an environment, a decision with legal or pricing
  weight). Say exactly what is needed, what it unblocks, and what was finished
  around it.

Never stop silently, and never report a project complete while a condition is
unverified. The owner's entire relationship with this run is the report; a report
that overstates is the one failure that discredits everything else the org did.
