# ADR-0076: Adopt the merged storage stack and modular monolith with ports

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: discovery product draft `aebc671e0c534bb393f8f8ad8caf85c4`; Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0001-stack-and-shape.md`.

## Context

Nightshift must remove operational and private narrative state from customer
repositories while supporting a simple offline installation and a production
multi-tenant service. It must still coordinate overlapping runs safely, retain
exact source provenance, retrieve narrative semantically, publish only reviewed
customer-owned documents, and support export and deletion.

The accepted capacity model is small in transactions and larger in bytes. Day
one is about 0.6 requests/second average, 6/second peak, 8,600 writes/day, 8,000
new rows/day, 10 GiB/month retained state/memory, and 3 GiB/month source-mirror
delta. The 100× case is about 600 requests/second peak, 24 million new rows/month,
1 TiB/month retained state/memory, 300 GiB/month mirror delta, and 1 TiB workspace
high-water. A 100 MiB artifact is expected; finalized objects are capped at 2
GiB and use resumable 8 MiB chunks.

## Options considered

### A — Five-boundary modular monolith (proposed by staff (Claude))

The Claude draft proposed one relational ledger, a content-addressed artifact
plane, pgvector, external mirrors/workspaces, a publication gateway, and five
organizational seams: control, memory, publication, workspace/provenance, and a
tenancy/safety envelope. It estimated $50–$80/month managed day one and
$700–$950/month at 100×. The component choices and safety mechanisms were mostly
accepted. The five-seam organization and cost estimate were not: the safety
envelope wraps two other seams and provenance crosses every entity, so neither
can be independently delivered without shared paths; the estimate also omitted
the HA standby and material external mirror/workspace capacity. Its estimate is
retained only as the single-AZ, no-standby floor.

### B — Three-port modular monolith (proposed by architect (Codex))

The Codex draft proposed three seams—control, memory and publication—with SQLite
plus filesystem CAS locally, HA PostgreSQL plus pgvector and encrypted object
storage in production, one stateless process, an external source/workspace
module inside control, and a transactional outbox. It estimated about
$150/month day one and $2,000/month at 100×, including the HA standby,
mirror/workspace disk, backups, keys and operational margin. This shape won,
augmented by Claude's per-tenant vector partitioning, strict plaintext-hash
dedupe scope, explicit capability losses in zero-file mode, lineage deletion,
path allowlist, single egress credential, and multi-process local conformance.

### C — Add Redis for cache, leases or queueing (rejected)

Day one has only 7,200 lease renewals/day, or 0.083 renewals/second, 8,600 total
writes/day, and 6 total requests/second at peak. Even 100× is 600 total
requests/second peak. A Redis service would add patching, monitoring,
persistence ambiguity and another failure mode before it removed a measured
bottleneck. It remains a possible non-authoritative wake-up/cache adapter only
after outbox depth exceeds 5,000 jobs for 10 minutes or transactional CPU exceeds
70% for 30 minutes while control p95 is above 250 ms after tuning.

### D — Use a dedicated vector database (rejected)

Selective indexing creates about 4,600 chunks/day and roughly 414,000 live
90-day chunks at day-one load. PostgreSQL can keep vectors beside tenant,
retention, redaction and provenance filters, and per-tenant partitioning keeps
queries scoped. A dedicated service is unjustified until there are more than 25
million live vectors and filtered p95 exceeds 2 seconds for 7 days after tuning,
or measured recall is below 80% at the required 2-second latency.

### E — Adopt Mem0 as memory authority or component (rejected)

Nightshift selects about 4,600 searchable chunks/day from roughly 250 MiB/day of
raw transcript, a volume already covered by immutable manifests and explicit
text/vector indexing. Mem0 would add a second self-editing fact lifecycle exactly
where the architecture requires memory to remain advisory. It is not adopted as
a persisted component or authority; an algorithm may be benchmarked offline,
but no Mem0 state enters NSC-v1.

### F — Add a temporal knowledge graph (rejected)

The relational model creates about 2,020 control, metadata and audit rows/day at
day one and far fewer narrative lineage links. Append-only events already
provide the temporal control record; versioned `narrative_links` provide
time-qualified narrative relationships. A graph engine would add a second
authority-shaped store before 10 million live links, before 25% of a 200-question
sample requires at least 3 time-qualified hops, and before relational accuracy
falls below 80%.

### G — Use Git as the live control or private-memory store (rejected)

One active repository produces about 400 task transitions and 280 artifact
manifests/day. Encoding those as Git changes would recreate the customer-noise
problem and provide no transactional claim, three-minute TTL, monotone fence,
tenant deletion, or current-state query guarantee. Git remains the source
identity/pinning mechanism and the authority for explicitly published
customer-owned documents only.

## Decision

Build a modular monolith with exactly three versioned ports: ControlPort,
MemoryPort and PublicationPort. Source snapshot/workspace management is a narrow
module inside control. The local profile wires a coordinator-owned SQLite
authority, filesystem CAS, local bare mirror/workspaces, and SQLite text search
plus bounded vector rerank. The production profile wires one HA PostgreSQL
primary/standby with pgvector and forced RLS, encrypted object storage, managed
tenant-key wrapping, tenant-scoped mirrors/workspaces, and one stateless
application process. Both use a transactional database outbox. There is no
Redis, broker, dedicated vector service, Mem0, or temporal graph at T0.

Git is never live operational control or private narrative. It is used for exact
source snapshots and only the approved customer-owned documents emitted by the
Publication Gateway.

## Consequences

One transaction can commit projections, events, audit, idempotency and outbox
work. There is one production relational backup story and one local authority.
Artifact bytes do not bloat transactional backups. The three ports allow three
crews to work in parallel and keep adapters replaceable.

The modular monolith still requires strict schema/role ownership; sharing a
process cannot become permission for table joins across modules. Two storage
adapters and one conformance suite must evolve together. External mirrors and
workspaces add disk and backup duties that were previously hidden in the
customer checkout.

## Cost

The managed estimate is about $150/month at day one and $2,000/month at 100×,
with ceilings of $300 and $3,000 respectively. The single-AZ, no-standby floor
is $50–$80/month day one and $700–$950/month at 100× but assumes minimal or
separately charged mirror/workspace capacity. Local service cost is $0, with
roughly 13 GiB retained and 10 GiB ephemeral disk at day-one assumptions.

Operational surface is one local coordinator plus SQLite/filesystem state, or
one application process, HA PostgreSQL, object storage, key wrapping,
mirror/workspace storage, outbox workers, logs and metrics in production.

## One-way door?

No at the component level. Ports and migration/export formats make adapters
replaceable. NSC-v1's externally observed command and receipt meanings are
costly to change once clients and audit history exist, so incompatible behavior
requires a versioned contract rather than reinterpretation.

## Scale tier

T0 for every selected component. testbed A use the numerical triggers in
`docs/architecture/SCALE-LADDER.md`; 100× by itself is not a tier selection.

## Reversal trigger

Revisit the shape if more than 5% of 10,000 sampled real commands require an
escape-hatch transaction across ports, or if the source/workspace seam-4 trigger
fires: two crews blocked for more than one delivery wave on source-mirror or
workspace paths. Replace individual adapters only at their scale-ladder or
conformance reversal trigger.
