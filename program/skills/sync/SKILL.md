---
name: sync
description: Written cross-crew standup at a wave boundary — collect each crew's status, route what one needs from the other, update the ledger, and decide the next wave.
---

# sync — the wave-boundary standup between crews

Crews cannot talk to each other. Interface truth flows through handoff files (the
file is the channel); this is where STATUS flows — what shipped, what is blocked,
what one crew needs chartered from another — reconciled at wave boundaries, on
files, so it survives restarts, context loss, and next year's questions.

## Procedure

1. **Collect.** For each crew, take its last inbox report (do not re-dispatch just
   to ask "status?" — that burns a session for nothing). If a crew has been silent
   through a whole wave, check its registry file, then its session history.

2. **Write the standup** to `.omni/sync/<YYYY-MM-DD>-wave-<n>.md`, one block per
   crew: shipped (with PR urls), in flight, blocked on what, needs from the other
   crew, decisions taken (with ADR ids), burn observed, and any vendor that hit a
   rate limit.

3. **Route the asks.** For each "needs from other crew": either it exists as a
   handoff file already (point the consumer at it), or you charter it as work in
   the next wave. Never pass along a verbal answer you synthesized yourself about
   another crew's code — that is how two crews end up with different beliefs about
   the same interface. If you are unsure, dispatch a narrow `purpose: "search"`
   explore to the OWNING crew and quote its answer.

4. **Resolve contradictions loudly.** When two crews report incompatible facts
   about a shared interface, that is the most important item in the standup. Do not
   average it. Get the owning crew to state the truth with `file:line`, write it
   into the handoff file, and tell the consumer what changed.

5. **Update the ledger.** Append to `.omni/ledger.md`: date, wave, decisions,
   reversals, handoffs produced, and the reason for any plan change. One line per
   fact, ADR ids linked. This file is the project's memory — it must be readable
   without the transcripts.

6. **Decide the next wave** and re-charter (see `roadmap`). Post the wave report
   as a comment on the pinned status issue (see `tracker`): what shipped, what is
   at risk, what you decided and what you rejected. Nothing in it waits on a
   reply — anything genuinely impossible without the owner goes in the FIRST line
   and into `.omni/blocked.md`, and the wave keeps moving around it.

## Notes

- Cadence: run this at every wave boundary, and any time a crew reports a blocker
  that another crew owns. Do not run it on a timer while crews are mid-task —
  interrupting workers to generate status is pure waste.
- Keep the standup factual. "Crew alpha believes the event schema is final" is a
  fact; "the schema is final" needs the owning crew's word plus a handoff file.
- If the ledger and the code disagree, the code wins and the ledger gets a
  correcting entry — never a silent edit.
