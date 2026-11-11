# ADR-0077: Use three independent seams, not five

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0002-three-seams-not-five.md`.

## Context

A seam is an organizational boundary across which two crews can build against a
stable interface without editing shared implementation paths. The count caps
parallel delivery; it is not a count of concerns, modules, tables or diagrams.
The two blind drafts agreed on control, memory and publication, but disagreed on
whether workspace/provenance and tenancy/safety were independently crewable.
This was the only discovery choice not settled directly by capacity or a hard
correctness invariant.

## Options considered

### A — Five seams (proposed by staff (Claude))

Claude's position, verbatim:

> **SEAM COUNT: 5.** That number caps parallel crews; it is not inflated — the
> operator UI/CLI is a *consumer* of seams 1, 2 and 5, not a sixth seam, and
> would collide with all of them if chartered independently before the interfaces
> freeze.

The five named seams were Control Ledger, Memory Plane, Publication Gateway,
Workspace & Provenance Manager, and Tenancy & Safety Envelope. Claude's
supporting argument, verbatim:

> Data flows: workers ↔ seam 1 (state) and seam 2 (artifacts); seam 4 feeds
> provenance identifiers into everything; seam 3 is the single egress; seam 5
> wraps 1 and 2. Git ingress (4) and Git egress (3) are deliberately different
> components so "read the customer's code" and "write into the customer's repo"
> can never share a code path or a credential.

This position gives Git ingress a particularly clean credential boundary and
would let a dedicated crew own `snapshot`, `checkout`, and `resolve`. It also
recognizes tenancy and deletion as load-bearing, rather than incidental.

### B — Three seams (proposed by architect (Codex))

Codex's position, verbatim:

> There are exactly **three independent seams**, so no more than three delivery
> crews can work without a shared-path lease. Authentication, provenance,
> encryption, the source mirror, migrations, and observability are cross-cutting
> constraints and are deliberately not counted as extra seams.

The three named seams were Control State Boundary, Narrative Memory Boundary,
and Customer Publication Boundary. Codex's supporting boundary argument,
verbatim:

> Deployment adapters on the other side of these ports are not a fourth seam:
> local and production implementations must pass the same contract suite. Nor is
> the retrieval index a fourth seam; it is a replaceable implementation detail of
> MemoryPort.

This position treats a seam as independent edit ownership, not conceptual
importance. It keeps cross-cutting rules inside every affected module and makes
the source/workspace code a narrow control-owned module.

### C — Treat every cross-cutting concern as a seam (rejected early)

Counting authentication, encryption, provenance, retention, migration and
observability separately could produce 9 or more boxes, but each would need to
edit or wrap the same control and memory storage paths. Such a count would
promise parallelism the repository paths cannot provide and multiply integration
leases rather than remove them.

## Decision

Use exactly three independent seams: control, narrative memory, and customer
publication. At most three crews can work in parallel without shared-path
leases. Source snapshot/workspace management remains a named module inside the
control seam with its own `snapshot`, `checkout`, and `resolve` interface.
Tenancy, safety, provenance, encryption, redaction, retention, migration and
observability are enforced across the three seams and are not independently
chartered seams.

The deciding rule was the reconciliation ladder's second rule: **fewer moving
parts**, applied to the seam test itself. Claude described seam 5 as wrapping
seams 1 and 2; a wrapper that must change both implementations cannot be built by
an independent crew without a shared-path lease. Provenance appears in every
entity in both drafts and fails the same independence test. Workspace/mirror is
the close case because it has a narrow interface. Reversibility decided that
remainder: promoting it later is cheap, while un-chartering a prematurely
independent crew is disruptive.

## Consequences

The organization can charter no more than three independent implementation
crews from this architecture. Cross-cutting safety work must be coordinated
inside each seam rather than delegated to a wrapper team. The control owner also
owns source/workspace paths initially, which can become a scheduling hotspot.

The narrow internal workspace interface preserves the option to split. Git
ingress and egress still use separate capabilities and code paths even though
only egress is an independent seam.

## Cost

This choice adds no infrastructure cost; it remains inside the approximately
$150/month day-one and $2,000/month 100× system estimates. Its cost is
organizational: no more than three crews can proceed independently, and the
control crew initially carries source/workspace ownership.

## One-way door?

No. A module with a narrow contract can be extracted into a seam without
changing NSC-v1. Recombining a prematurely split team and reconciling shared
paths would be more expensive, which is why the smaller count is selected first.

## Scale tier

T0 organizational shape. It does not promote because request volume grows; it
promotes only when measured crew blocking demonstrates an independent edit
boundary.

## Reversal trigger

If **two crews are blocked for more than one delivery wave on source-mirror or
workspace paths, promote workspace to seam 4**. That is the sole seam-count
promotion currently authorized; another cross-cutting concern needs equivalent
evidence that two crews can work either side without shared-path edits.
