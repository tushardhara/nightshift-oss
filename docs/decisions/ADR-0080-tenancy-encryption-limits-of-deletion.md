# ADR-0080: Enforce tenant encryption while stating the limits of deletion

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: discovery product draft `aebc671e0c534bb393f8f8ad8caf85c4`; Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0005-tenancy-encryption-limits-of-deletion.md`.

## Context

Managed Nightshift stores sensitive operational metadata, narrative, source
packs, embeddings and exports for multiple tenants. It must isolate, encrypt,
redact, retain, export and delete them without overstating what key destruction
does to searchable relational/index data already present in shared backups.
Deletion must also reach embeddings, summaries and citations derived from a
redacted source.

## Options considered

### A — Tenant KEK crypto-shred plus derived lineage (proposed by staff (Claude))

Claude proposed per-tenant KEKs, per-artifact wrapped DEKs, plaintext content
hashes scoped strictly within a tenant, redacted successors, crypto-shredding the
original DEK, and a `derived_from` edge walked so no embedding outlives its
source. The lineage mechanism and strict refusal of cross-tenant dedupe were
accepted. Calling tenant KEK destruction complete erasure was rejected because
control fields and embeddings must remain searchable and therefore are not all
individually envelope-encrypted.

### B — Layered deletion with backup disclosure and canary verification (proposed by architect (Codex))

Codex proposed tombstone, link revocation, index/chunk deletion, body deletion,
key destruction, tenant fencing and a canary list checked against every storage
adapter. It explicitly stated that shared-backup deletion finishes on a declared
maximum 35-day backup expiry, not instantly, and is not cryptographic erasure.
Its retention classes—7-day workspace, 24-hour unfinalized upload, 90-day raw
transcript, 365-day report/handoff/evidence, tenant-lifetime control/audit/
decision/publication receipts—were accepted. Claude's lineage walk was added
because a no-resurrection rule alone could miss already-derived vectors.

### C — Physical tenant isolation or individual encryption for every field (rejected early)

A database, bucket and backup set per tenant would make deletion boundaries
cleaner, but day one has 1 tenant and 6 peak requests/second; 100× has 100 tenants
and 600 peak requests/second. That topology is unpriced and unjustified at T0.
Individually envelope-encrypting embeddings and coordination fields would make
their required indexes unusable. Dedicated cells remain a contractual or
measured-load tier, not the default.

## Decision

Every row, object, job, mirror, workspace and audit correlation is tenant-scoped;
repository rows add repository scope. Production forces PostgreSQL row-level
security and object-level authorization. Local mode runs the same multi-tenant
code with one tenant. Plaintext content hashes deduplicate only inside one
tenant; cross-tenant dedupe is forbidden.

Use encrypted transport and volumes plus application envelope encryption for
artifact bodies and export bundles. Versioned per-object data keys are wrapped
under a tenant key, itself protected by a service hardware-backed key by default,
an OS keystore/passphrase locally, or an approved customer-controlled key. Key
rotation is resumable and audited.

Redact secrets and configured sensitive data before durable transcript
finalization and indexing. Post-hoc redaction writes a verified redacted
replacement and tombstones the original. Deletion walks typed `derived_from`
lineage and removes every content-carrying descendant—chunks, embeddings,
summaries and snippets—before deleting body/chunk bytes and keys and recording
an idempotent receipt. A redacted replacement is a distinct replacement edge;
expiry of a Git source snapshot is non-propagating and instead sets
`source_not_retained` on surviving narrative. Tenant deletion blocks commands,
revokes credentials, advances fence generations, drains work, optionally
exports, removes all live storage and verifies per-adapter canaries.

**Shared-backup deletion completes only when the backup expires, with a declared
maximum of 35 days. It is NOT cryptographic erasure.** Destroying the tenant key
can make envelope-encrypted artifact bodies unreadable sooner, but searchable
embeddings, control metadata and shared database pages cannot all be
individually envelope-encrypted. The design discloses this limit rather than
calling backup-resident data erased.

## Consequences

Tenant scope, roles, keys and lineage make live isolation and deletion testable.
Search cannot resurrect a tombstoned source because every derived row is
reachable and deleted. Export can enumerate exactly what policy retained.

Shared infrastructure does not offer immediate physical removal from backups.
Customers requiring a shorter physical-erasure window need a dedicated
database/bucket/backup cell priced before ingestion. A minimal non-content
deletion receipt remains only when disclosed law or contract requires it.

## Cost

Key operations, encrypted storage, backups, retention jobs and deletion scans
are included in the approximately $150/month day-one and $2,000/month 100×
estimates. The 100× model assigns roughly $250–$600/month to logs, keys, network
and operational margin. Dedicated tenant cells are outside T0 and require a
separate cost plan.

## One-way door?

Yes for data already admitted under the 35-day shared-backup promise: key
destruction cannot retroactively change physical backup layout. The key wrapper,
storage adapter and future tenant placement remain replaceable. Retention or
isolation contracts must be decided before accepting data that needs stronger
erasure.

## Scale tier

T0: forced-RLS shared schema, tenant-scoped object storage and per-tenant keys;
separate local roots. Move a named tenant to T1 after 1 signed physical-isolation
or BYOK contract, or when it exceeds 25% of database/storage load for 30 days.

## Reversal trigger

Revisit shared tenancy if 1 isolation test produces a cross-tenant read, 1
signed contract or law requires physical deletion sooner than 35 days, a tenant
exceeds 25% of database/storage load for 30 days, or key rotation misses 24
hours in 3 consecutive drills. The remedy is a dedicated adapter/cell and
migration, never a stronger claim about shared backups.

## Amendment (issue #694): INTEGER is the canonical local-profile tenant id

`bin/nightshift_provenance.py` validated `tenant_id` as a positive INTEGER while
`bin/nightshift_publication.py` recorded it as free TEXT, so the same identifier
was accepted by one module and refused by the other. #694 makes INTEGER the one
canonical type for the LOCAL profile, with `nightshift_provenance.validate_tenant_id`
as the single validator both modules call.

The reason is isolation, not tidiness. `resolve_publication_root` joins the id
into a filesystem path, and an `isinstance(value, int)` check makes
`"../other_tenant"` unrepresentable as a directory name in one line; a TEXT
tenant id would need a hand-written traversal validator to reach a weaker
guarantee. Column declarations do not help on their own — an `INTEGER NOT NULL`
column in SQLite stores `'../other_tenant'` happily — so every tenant-bearing
table in both schemas additionally carries
`CHECK(typeof(tenant_id)='integer' AND tenant_id>0)`. That CHECK is a second
line, never the first: it coerces the string `'7'` to integer 7 and accepts it,
which the Python validator does not.

This decision is scoped to the local profile. `docs/planning/400-memory-architecture/architecture/OVERVIEW.md`
specifies UUIDv7 durable ids for the managed-production PostgreSQL profile;
reaching that profile is an ADR-0082 authority-epoch migration and is explicitly
NOT what #694 does. UUIDv7 is recorded here as the deferred PostgreSQL
migration, not as a contradiction of the type chosen above.
