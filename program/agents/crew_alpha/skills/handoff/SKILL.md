---
name: handoff
description: Produce or consume a cross-crew handoff. Crews cannot message each other, so a handoff is a written file plus a report to the program — never a verbal promise.
---

# handoff — the contract between crews

Omnigent only lets a session send to its own direct children, so this crew can
never message another crew. Every cross-crew dependency is therefore a FILE plus a
report to the program. That constraint is a feature: it forces the interface to be
written down, which is also what makes it auditable next year.

## Producing a handoff

Write `.omni/handoffs/<YYYY-MM-DD>-<from_pod>-to-<to_pod>-<slug>.md`:

```
# Handoff: <what> — <from_pod> → <to_pod>
Date: YYYY-MM-DD · ADR: ADR-NNNN · PR: <url> · Conversation: <id>

## What is ready
The interface as it actually exists now — signatures, endpoints, schemas, events,
file paths — with file:line references into the merged or open PR.

## How to use it
A minimal working example that you have actually run. Paste the command and its
real output, not an illustration.

## Guarantees
What the consumer may rely on: ordering, idempotency, error contract, limits,
performance envelope.

## NOT guaranteed / not built
The gaps, explicitly. This section is why handoffs work; an empty one means you
did not look.

## Migration or version notes
What changes for anyone already depending on the old shape.

## Open questions for the consumer
```

Then report to your program that the handoff file exists, with its path. Never
declare a handoff complete while the PR is unopened — the consumer would be
building on a promise.

## Consuming a handoff

1. Read the file before writing any code against it. Treat "NOT guaranteed" as
   binding.
2. Verify the example runs, in your own worktree, before you design around it. A
   handoff that does not reproduce is a blocker, not a starting point.
3. Where the file is silent, do NOT invent behaviour and do not patch around the
   gap. Write the question down and send it to the PROGRAM, which owns routing to
   the other crew. Then continue on work that does not depend on the answer.
4. Record in your registry which of your tasks depend on which handoff. When the
   other crew revises it, you need to know instantly what is now suspect.

## Notes

- A handoff is a point-in-time artifact. If the interface changes, write a NEW
  dated handoff rather than editing the old one — the sequence of handoffs is the
  history of the integration.
- Every handoff references the ADR that justified the interface shape. Interface
  without rationale ages into cargo cult.
- If you find yourself wanting a synchronous conversation with the other crew,
  that is a sign the boundary is wrong. Say that to the program explicitly; a
  redrawn boundary beats a chatty one.
