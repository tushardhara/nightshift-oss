# ADR-0027: Put a human summary before technical evidence

Date: 2026-08-27 · Status: proposed
Deciders: program · Proposers: owner-directed communication audit
Conversation: local issue #201 implementation · Spec: docs/HUMAN-COMMUNICATION.md

## Context

Nightshift keeps strong evidence, but recent issues made the current answer hard
to find. A resolved blocker could remain in the issue body while its replacement
appeared several comments later. Closed backlog items did not always say whether
they were completed or administratively retired. Long hashes, internal IDs, and
review chronology appeared before the outcome an owner or operator needed.

The evidence is useful; its placement is the problem. Owners need a thirty-second
answer, operators need the live impact and next action, and contributors need the
proof below it.

## Options considered

### A — Keep evidence-first reports

Rejected. It preserves detail but makes every reader reconstruct status and lets
stale chronology look like the current blocker.

### B — Rewrite old issue and PR history into short summaries

Rejected. Rewriting history removes provenance, changes the record after the
fact, and would create a large cleanup project unrelated to current delivery.

### C — Remove technical evidence from GitHub updates

Rejected. Operators and reviewers still need exact commands, versions, receipts,
and failure evidence. Removing them would make concise reports easy to read but
impossible to audit.

### D — Layer a short human summary over retained evidence

Chosen. New issue intake uses four plain problem-definition fields. Every later
owner-facing issue or PR status comment, every PR description, and every status
report starts with the same six fields in no more than 180 words. Detailed
evidence follows.

## Decision

Adopt `docs/HUMAN-COMMUNICATION.md` as the communication contract. The first
screen uses, in order: `Status`, `What changed`, `Live impact`, `Still not done`,
`Next step`, and `Owner action`. It uses plain words and contains no internal
IDs, hashes, raw logs, or long evidence chronology. Exact evidence remains below
the summary.

New issue templates lead with the intake fields `Problem`, `Impact`, `Done when`,
and `Not included`; there is no delivery status at intake. The PR template leads
with the six-field status shape. The tracker skill governs every owner-facing
issue comment, PR comment, and status comment, not only reports. Shared crew
dispatch instructions require agent-authored PR bodies to use it too. Existing
history remains unchanged; the next update on an active item provides the
current summary and explicitly retires stale blockers.

## Consequences

The current truth becomes visible on a phone without weakening auditability.
Writers spend a small amount of time separating outcome from proof. Repetition
across the six headings is preferable to ambiguity between merged and live.

## Cost

No infrastructure or subscription cost. The only operational cost is maintaining
the first-screen summary when state changes.

## One-way door?

No. Templates and guidance can be superseded without changing historical records.

## Scale tier

Tier 0 process documentation. It adds no runtime component.

## Reversal trigger

Supersede this ADR if two consecutive delivery waves show that compliant summaries
still leave an owner or operator unable to answer current status, live impact,
remaining work, next owner, and required owner action within 30 seconds.
