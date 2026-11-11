# Human-first communication

Audience: owner, operator, and contributor.

Nightshift records detailed technical evidence, but a person must not have to
reconstruct the current truth from a long chronology. Every owner-facing status
comment on an issue or PR, every PR description, and every status report starts
with a **30-second summary of no more than 180 words**. Use plain words. Explain
the outcome before naming mechanisms.

A new issue body is the intake exception: it starts with `Problem`, `Impact`,
`Done when`, and `Not included`, because no work status exists yet. Every later
status comment on that issue uses the six-field first screen below.

The first screen uses these headings, in this order:

## Status

One of: `On track`, `Blocked`, `Ready for review`, `Ready to merge`, `Merged`,
`Superseded`, `Done`, or `Closed without completion`. Add one plain sentence
saying why.

## What changed

Describe the user-visible or operator-visible change. Say `Nothing yet` when the
work is still investigation or design.

## Live impact

State both the live-system impact and live-data impact. Distinguish `merged`,
`available to deploy`, and `running in production`; they are not synonyms. Say
explicitly `Live systems: None; live data: None` when neither changed.

## Still not done

Name the unmet outcome or acceptance criterion. Do not make a reader infer it
from old failed attempts.

## Next step

Name one concrete action, its owner, and the condition that makes it complete.

## Owner action

Say exactly what the owner must do. Use `None` when no action is required.

## Detailed evidence

Put commands, full Git SHAs, digests, session/run/control IDs, PIDs, receipts,
logs, review transcripts, and chronology here. Link durable evidence instead of
copying it repeatedly. Old findings remain part of history, but every later
update states whether each former blocker is resolved, replaced, or still active.

For a closed item, say whether it was completed, superseded, declined, or closed
during backlog cleanup. Never let a closed checkbox list imply success.

## Good issue update

> **Status:** Blocked — the worker can edit but cannot publish.
>
> **What changed:** Exact-version registration and nested dispatch now work.
>
> **Live impact:** Live systems: None; live data: None. This was isolated.
>
> **Still not done:** No worker PR or independent review exists.
>
> **Next step:** Platform will bind custom tools to the session workspace and
> rerun the test.
>
> **Owner action:** None.

## Good PR description

> **Status:** Ready for review.
>
> **What changed:** PR descriptions now begin with one short human summary.
>
> **Live impact:** Live systems: None; live data: None until deployment.
>
> **Still not done:** Historical PR descriptions are unchanged.
>
> **Next step:** Reviewer checks the template and focused test.
>
> **Owner action:** None — Release merges after the gates pass.

## Good status report

> **Status:** On track — one of three delivery steps is complete.
>
> **What changed:** The reviewed bundle is available at an exact Git version.
>
> **Live impact:** Live systems: production runs the previous version; live data:
> unchanged.
>
> **Still not done:** Cutover and one end-to-end worker run.
>
> **Next step:** Release prepares the reversible cutover receipt today.
>
> **Owner action:** None until Release asks for the cutover decision.

## Bad examples

- **Issue update:** “Still investigating; see logs below.” It hides the live
  impact, remaining outcome, next owner, and owner action.
- **PR description:** “Fixes everything. SHA abc123.” It claims an outcome before
  explaining what changed and wrongly treats a hash as the summary.
- **Status report:** “Merged, therefore deployed.” It confuses repository state
  with live-system and live-data state.
