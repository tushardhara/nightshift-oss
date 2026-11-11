# ADR-0079: Preserve provenance with pinned snapshots, closure manifests and derived_from

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0004-provenance-pinned-snapshots-closure-derived-from.md`.

## Context

Nightshift must explain every artifact and decision against the exact source that
produced it even after rebases, force-pushes, squash merges, dirty local input,
submodule movement, Git LFS garbage collection, retention expiry, export or
removal. A branch name or commit string alone does not retain the bytes. At the
same time, provenance retention cannot override authorized deletion or promise
indefinite possession of customer source.

## Options considered

### A — Keep refs plus patch-id rewrite mapping (proposed by staff (Claude))

Claude proposed a private `refs/nightshift/keep/<sha>` in the external mirror for
every referenced snapshot, plus observed branch, patch-id and mapping evidence
from old SHAs to new squash/merge SHAs. This correctly keeps unreachable commit
objects and provides a useful human equivalence signal after rewriting. It was
accepted as part of the design, but a keep ref alone does not prove that
submodule commits or Git LFS payloads were fetched and retained.

### B — Pinned bundles, closure manifests, synthetic commits and lineage (proposed by architect (Codex))

Codex proposed full commit/tree OIDs and object format, a private retention ref,
a content-addressed pack or bundle, and a closure manifest covering materialized
submodule commit OIDs and Git LFS object OIDs/digests. Dirty input becomes a
synthetic commit in an external workspace, carrying parent and patch digest.
Rewrites produce `derived_from` edges rather than overwritten identity. When
retention expires, `source_not_retained` prevents the record from claiming
reconstructability. This was the more complete base and won; Claude's patch-id
mapping was added as advisory evidence.

### C — Record only the observed Git SHA (rejected early)

A full SHA is an immutable identity only while its object and closure remain
available. A force-pushed remote may garbage-collect it; a superproject tree may
contain only a submodule OID or an LFS pointer. Recording 40 task inputs/day
without retaining their objects would be precise labeling but not reproducible
provenance. It also has no honest representation for dirty working-tree bytes.

## Decision

Every run, artifact, narrative decision, evidence item and publication binds to
an immutable `source_snapshot`. A snapshot records repository ID, Git object
format, full commit and tree OIDs, observed ref as context, private retention ref,
pack/bundle digest, closure-manifest digest, capture time, retention state and
synthetic metadata where applicable.

The closure manifest enumerates policy-required submodule commit OIDs and Git
LFS object OIDs/digests. A run is admitted only after its required closure is
fetched, pinned and verified. A dirty checkout is copied externally; Nightshift
creates and pins a synthetic commit there, records its parent and canonical
patch digest, and leaves the customer checkout and `.git` untouched.

Rebase, squash and force-push create new identities. The system records
`derived_from` among input snapshot, worker output, reviewed patch and resulting
commit/tree. Matching patch IDs or tree digests are advisory equivalence, never
commit identity.

The limit is explicit: when source retention expires before narrative
retention, the artifact still carries its exact OID and lineage but the snapshot
is marked `source_not_retained`. Nightshift can then explain the recorded
identity and derivation, but **cannot reconstruct the deleted source bytes** and
must not imply otherwise.

## Consequences

Provenance survives upstream reference rewriting and can reconstruct retained
source without trusting the customer's current remote. Synthetic commits make
dirty input explicit and repeatable. Lineage supports complete deletion of
derived indexes and honest squash relationships.

Mirrors and closure packs may retain deleted secrets or unexpectedly large LFS
objects. Onboarding and retention therefore need measurement, authorization,
encryption, export and deletion coverage. Patch-id collisions or normalization
mean mapping remains advisory.

## Cost

The base estimate includes a 2 GiB initial packed mirror, 3 GiB/month source
delta and 10 GiB workspace high-water for one active repository; at 100× it
includes roughly 500 GiB mirror baseline, 300 GiB/month delta and 1 TiB workspace
high-water. These are included in the approximately $150/month day-one and
$2,000/month 100× totals. Closure capture adds fetch latency, manifest storage,
digest verification and retention/deletion jobs.

## One-way door?

Yes for the snapshot and lineage identities already embedded in artifacts,
exports and audit receipts. Their meaning cannot later be weakened. The mirror,
bundle and workspace adapters remain replaceable, and new source systems can add
a versioned snapshot adapter.

## Scale tier

T0: tenant-scoped bare mirrors, retention refs and closure packs with disposable
external clones. T1 is a shared sparse-hydration source service when workspace
startup p95 exceeds 2 minutes for 7 days, hot workspace disk exceeds 2 TiB at
70% for 24 hours, or transfer exceeds 20% of subsystem cost.

## Reversal trigger

Revisit the retained-Git implementation if a supported non-Git source system is
required by 1 signed contract, customer policy forbids retaining source objects,
or trials find packed repositories over 20 GiB, growth over 1 GiB/day, any failed
closure reconstruction, or any unauthorized cross-tenant object reachability.
Add a versioned SourceWorkspace adapter; do not reinterpret existing snapshots.

## Amendment (issue #694): the snapshot boundary is addressed by `ref_id`, and the closure claim is always recorded

Issue #694 joins this ADR's `derived_from` graph to the publication gateway's own
lineage edges in a single view (`lineage_edge`, `bin/nightshift_publication.py`),
so one query walks a landed commit back to the source snapshot it was built from.
That crossing needs a rule for how the two graphs name the same node. This
amendment records it, because the first implementation had one and did not say so
— and the unwritten rule contradicted this document's own convention.

**The rule: a `source_snapshot` lineage node is addressed by its `ref_id`.**
`lineage_node.node_id` stays free-form. Every existing caller already treats it
that way: the snapshot id is carried in `ref_id` and `node_id` is a local label
(`n_snap`, `node-snapshot`). Publication's `input_snapshot` lineage parent is the
snapshot id verbatim, so the unified view resolves a provenance `source_snapshot`
node through `ref_id` — the column that means *which snapshot* — and through
`node_id` for every other node kind.

The alternative — requiring `node_id == ref_id == snapshot_id` for snapshot nodes
— was rejected. It would rename existing nodes, it would put a naming convention
in the load-bearing position of a join, and, most importantly, violating it
degraded silently: the walk stopped at the file boundary and still reported
`closed: true, truncated: false`, because the snapshot was resolved from the
`source_snapshot` table rather than from the walk. A broken join was
indistinguishable from a complete one. Joining on `ref_id` removes the convention
rather than documenting it.

Two consequences are enforced at the write path, not assumed:

1. A `source_snapshot` node's `ref_id` is validated as a snapshot id — same
   charset, and not in publication's reserved `pub:` namespace — because it is
   the id that enters the shared vocabulary. At most one such node exists per
   (tenant, repository, `ref_id`): two nodes for one snapshot collapse to one id
   in the unified view, and an edge between them appears there as the self-edge
   `record_derived_from` already refuses.
2. The snapshot-id half of the namespace is disjoint from the lineage-node half.
   `record_lineage_node` refuses a non-`source_snapshot` node named with an
   existing snapshot id, and `record_source_snapshot` refuses a snapshot id
   already held by such a node. Either would alias the boundary node and hand a
   landed commit an identity from an ancestry the walk never traversed.
   `lineage_chain_from_commit` re-checks this at read time and refuses
   `lineage_namespace_collision` rather than answering, for a row written around
   both validators.

**The closure claim is recorded on every snapshot, or the snapshot is refused.**
This ADR's closure manifest was optional in the schema (`closure_manifest_digest`
is NULLable) and `verify_closure_complete` treated NULL as "nothing was
required, pass". Combined with the fact that no code computed the digest, the
`closure_incomplete` refusal could not fire on any snapshot a capture produced —
the requirement a caller used to be able to decline to declare had only moved
from the caller's arguments into an optional column.

`record_source_snapshot` now freezes the empty-manifest digest when the caller
supplies none, so no snapshot carries *no claim*; `verify_closure_complete`
refuses a NULL column (`reason: no_closure_claim_recorded`) instead of passing
it; and `capture_dirty_snapshot` discovers the closure of the synthetic commit it
writes — from that tree, not from customer HEAD, since a dirty tree can add or
drop a gitlink — and returns both the entries and their digest.

The limit that remains is stated rather than papered over: a frozen "nothing was
required" is explicit and immutable, but it is only as true as the capture that
produced it. A caller that hand-builds a snapshot row for a repository with
submodules and discovers nothing records a truthful claim of having claimed
nothing, and publication accepts it. Closing that is the capture path's job, not
the verifier's, which is why discovery now lives in `capture_dirty_snapshot`.
