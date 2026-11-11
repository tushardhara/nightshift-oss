# Review contract

Copy this template into a committed work-item/spec artifact and freeze that file at
the wave checkpoint before the first implementation commit. The delivery PR links
and may mirror the exact committed version; its mutable body is never the authority.
Do not edit a frozen version in place.

## Identity

- Work item:
- Committed contract path and blob OID:
- Contract version:
- Base SHA:
- ADR:
- Review owner (cross-crew lead, not the author):
- Frozen at (UTC):

## Acceptance criteria

Use stable identifiers. Each criterion must name its verification.

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 |  |  |

## Explicitly out of scope

-

## Required review domains

- [ ] PM acceptance
- [ ] Different-vendor code review
- [ ] QA
- [ ] Cross-crew review
- [ ] Staff / ADR consistency
- [ ] Security (when sensitive paths are touched)
- [ ] UX (when a human-facing flow or operator interface changes)

**Machine-readable checklist (ADR-0039, issue #231).** This section is parsed by
`bin/nightshift_review_receipts.parse_required_domains()` to derive the exact set
of domains a work item's consolidation must wait for. The rule is exact and fails
closed:

- Each row is `- [ ]` or `- [x]`/`- [X]` followed by a label.
- A label is matched by a fixed canonical PREFIX, independent of anything after
  it (a parenthetical condition, a `--`-delimited rationale, a delegate name):
  `PM acceptance`, `Different-vendor code review`, `QA`, `Cross-crew review`,
  `Staff / ADR consistency`, `Security`, `UX`. These seven prefixes are the
  complete canonical domain set and none is a prefix of another.
- `[x]`/`[X]` means required for this work item; `[ ]` means not required.
- A label matching zero or more than one canonical prefix, a canonical domain
  appearing twice, or a canonical domain never appearing at all in the section —
  each is a parse failure (`ReceiptDenied`), never a guess.

Removing a domain after freeze requires a new contract version and rationale.

## Finding receipt

Every finding uses one severity from ADR-0026.

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 |  | `MERGE_BLOCKER` / `PRE_DEPLOYMENT` / `FOLLOW_UP` |  |  |  | open |  |

The review owner publishes one deduplicated fix packet per round. A severity change
must retain the original finding and record the rationale.

Never execute a reproduction against a live system. A production-only security or
data-loss risk may be proven by a concrete causal failure scenario and safe evidence.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

Content evidence may transfer only across identical reviewed blobs with unchanged
dependency seams. Re-run CI, provenance, PR-body, authorization, and live/runtime
checks.

## Contract amendments

Never rewrite the frozen rows above. Create the next version and record:

- previous version and reason;
- criteria, domains, ADR, or scope changed;
- attestations invalidated by that change;
- implementation work that must be reconsidered.
