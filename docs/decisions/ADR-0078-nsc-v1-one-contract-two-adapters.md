# ADR-0078: Define NSC-v1 as one contract with two adapters

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0003-nsc-v1-one-contract-two-adapters.md`.

## Context

Nightshift needs an offline SQLite/filesystem deployment and a managed
PostgreSQL/object-storage deployment. They differ in concurrency, isolation,
DDL, key custody, finalization mechanics and retrieval ranking, but they may not
differ on authority, fencing, idempotency, provenance, tenant scope, deletion or
publication safety. Written intent cannot detect behavior that drifts during an
incident fix.

## Options considered

### A — Three seam interfaces plus seven invariants (proposed by staff (Claude))

Claude proposed `ControlStore`, `ArtifactStore` and `Publisher`, with a shared
suite enforcing monotone fencing, append-only events, immutable per-tenant
artifacts, tenant scope, approval-gated publication, store-side lease time and
advisory retrieval. It added two decisive anti-divergence rules: every row
carries `tenant_id` even locally—local mode is multi-tenant code with one
tenant—and a fix lands as a conformance test first, then two implementations.
It also required the local suite to run multiple processes. The substance was
accepted; the interface set alone needed a versioned behavioral frame and the
broader receipt invariants.

### B — NSC-v1 with three ports and ten invariants (proposed by architect (Codex))

Codex proposed one named Nightshift State Contract containing ControlPort,
MemoryPort and PublicationPort operations, ten normative invariants, and
normalized receipt comparison across both adapters. It covered scoped
authorization, idempotency bytes, atomic audit/outbox, versions/fences,
repository sequences, authority time, snapshot provenance, bounded reads,
atomic manifest finalization, and allowed retrieval-ranking differences. This
versioned frame won, with Claude's tenant-row and test-first rules added as
invariants eleven and twelve and its multi-process local execution made
mandatory.

### C — Separate local and managed contracts (rejected early)

Two contracts would make local mode a weak simulation and let production-only
fixes drift. SQLite could then acquire in-memory locks or single-process
assumptions, while PostgreSQL could hide domain behavior in SQL. Migration from
local to managed would become an application migration rather than an adapter
switch, defeating the brief's shared-behavior requirement.

## Decision

Adopt versioned **NSC-v1** as the union of the three ports and twelve normative
invariants in `docs/architecture/OVERVIEW.md`. The contract is the executable
conformance suite, not the prose: a backend is compliant only when it passes the
same command traces, randomized races, multiprocess local cases, kill/restart
points, clock jumps, migration fixtures, tenant-isolation checks, redaction,
export and deletion cases and yields equivalent normalized receipts.

The suite deliberately does not compare query plans, storage layout,
performance, key-custody implementation, or semantic result ordering. It does
compare scope, command outcomes, receipt identity, fencing, idempotency,
repository ordering, authority time, provenance, freshness declarations,
atomic finalization, export and lineage-complete deletion.

Named divergence risks are PostgreSQL versus SQLite writer concurrency,
isolation and uniqueness; NULL, collation, JSON and timestamp semantics; dialect
and migration DDL; filesystem rename versus object-store finalization; local
encryption/redaction shortcuts; client clocks entering lease decisions;
single-process locks in the local adapter; and an incident fix landing in only
one backend.

## Consequences

Workers and operators can depend on one behavior and move between deployment
profiles. Randomized races can prove the coordinator rather than merely test
single-threaded SQLite. Adapter optimizations remain possible because ranking
and implementation are explicitly outside the contract.

Every behavioral fix costs two implementations and one shared test. Normalized
receipt fixtures and migration cases become maintained public design assets.
Incompatible behavior requires NSC-v2; it cannot hide behind adapter selection.

## Cost

The suite adds test execution and maintenance, not a separately deployed
service. Runtime cost remains inside the approximately $150/month day-one and
$2,000/month 100× estimates. Engineering cost is that every behavioral change
must be specified once, tested once, and implemented twice.

## One-way door?

Yes, once NSC-v1 receipts appear in exports, audit trails and clients. Their
meaning cannot be reinterpreted safely. Components remain replaceable behind
the contract, and incompatible improvements use a new version with an explicit
migration.

## Scale tier

T0. The same suite applies at every scale tier. A tier promotion is invalid if
the candidate adapter does not pass NSC-v1 before cutover.

## Reversal trigger

Version or revise NSC-v1 if a proof or production trace shows that a required
operation cannot be atomic and fenced under its rules, or if more than 5% of a
10,000-command representative sample needs an escape-hatch transaction that the
three ports cannot express. A single adapter-only failure is a bug and
conformance case, not grounds to weaken the shared contract.
