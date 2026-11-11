# Nightshift state and memory architecture

Status: design of record  
Date: 2026-08-29

This document defines how Nightshift operates against a customer repository
without making that repository the home of its operational state or private
narrative. It is the implementation target for both local/offline and managed
multi-tenant deployments.

## Capacity and design posture

The capacity model is intentionally ordinary: day one is one active repository,
five concurrent workers, about 40 completed tasks, 400 control transitions, 280
artifact manifests, 8,600 writes, and 51,600 total requests per day. That is
about 0.6 requests/second on average and 6/second at the modeled peak. At 100×,
the modeled peak is 600 requests/second, retained state and memory grow by about
1 TiB/month, source mirrors grow by about 300 GiB/month, and disposable
workspaces reach about 1 TiB high-water. Finalized objects use resumable 8 MiB
chunks and have a 2 GiB cap; a larger logical output is a manifest plus multiple
objects. These numbers justify one transactional authority and cheap body
storage, not a cache tier, broker, dedicated vector database, self-editing memory
product, or graph engine.

Day-one managed cost is estimated at about $150/month, with a $300/month
ceiling. The 100× estimate is about $2,000/month, with a $3,000/month ceiling.
The lower single-AZ, no-standby floor is documented in the scale ladder. Local
mode has no service bill but consumes customer-controlled disk.

## Shape: one modular monolith, exactly three seams

Nightshift is a modular monolith with three versioned ports and deployment-
specific storage adapters. The modules share a process at T0, but they do not
read or write one another's tables directly. Each owns its schema, migrations,
capabilities, audit receipts, and interface.

The seam count is explicitly **three**. It is the maximum number of delivery
crews that can work independently without taking shared-path leases: at most
**three crews can work in parallel** across these boundaries. Authentication,
encryption, provenance, migration, observability, and tenancy are invariants
across the three modules, not extra seams. Source snapshot and workspace
management is a module inside the control seam. If two crews are blocked for
more than one wave on source-mirror/workspace paths, that module is promoted to
seam 4.

| Seam | Ownership | Versioned interface |
|---|---|---|
| **1. Control State Boundary** | Tasks, dependencies, state transitions, runs, slots, claims, leases, fencing generations, repository sequences, idempotency receipts, control audit, outbox jobs, and source snapshot/workspace coordination | `ControlPort v1`: mutate/query, acquire/renew, and allocate. Mutations take tenant, repository, command ID, actor, expected versions, and an optional fence; they return repository sequence, entity versions, result, and audit ID. Its internal `SourceWorkspace` module exposes `snapshot`, `checkout`, and `resolve`. |
| **2. Narrative Memory Boundary** | Artifact manifests and bodies for transcripts, reports, decisions, handoffs, evidence and summaries; redaction, retention, chunks, embeddings, keyword/vector retrieval, memory export and deletion | `MemoryPort v1`: begin upload, put resumable chunk, finalize, get, search, export, and delete. Every call is scoped; every result declares provenance and freshness. |
| **3. Customer Publication Boundary** | Staging, review decisions, one-use approvals, path policy, base checks, Git writes, publication receipts, and resulting commit identity | `PublicationPort v1`: stage, review, approve, publish, and inspect. The sole write credential for a customer repository exists behind this port. |

Clients can develop against contract fakes while storage work proceeds behind a
port. The local and production adapters are not additional seams; both are
implementations of the same contract. Retrieval indexing is an implementation
detail of MemoryPort, not another authority.

## Three authorities that cannot silently merge

| Plane | Contents | Durable authority | Mechanical boundary |
|---|---|---|---|
| **Control** | Current tasks and edges, events, runs, slots, claims, leases, fences, repository sequences, idempotency, authority epochs, and publication authorization | ControlPort is the sole live authority for scheduling and operational state. Publication authorization is also a control fact, owned and mutated only through PublicationPort. Current projections and their immutable events commit together. | Only typed control or publication handlers can write their owned control facts. Expected versions and current fences are checked from authoritative rows in the transaction. Memory roles have no grants or mutation capability. |
| **Narrative memory** | Transcripts, reports, narrative decisions, handoffs, evidence, summaries, redacted searchable text, embeddings, and citations | Finalized artifact bytes and their immutable manifest are authoritative for what was recorded. Search ranks, chunks, embeddings, and generated summaries are derived. | Separate identifiers, schemas, roles, capabilities, retention policy, and APIs. Retrieval returns `advisory_context`, which is not accepted as proof of a control predicate. Reindexing cannot issue control commands. |
| **Approved customer-owned documents** | ADRs, PRDs, release notes and other documents deliberately accepted into the customer repository | After publication, customer Git at the recorded commit is authoritative for the document. The Nightshift artifact and publication receipt remain evidence. | PublicationPort is the only egress and holds the only repository write credential. A producing worker cannot review its own output. Approval is one-use and bound to exact bytes, path, base SHA, reviewer, policy, and expiry. |

When planes disagree, control wins for current operations, the finalized artifact
wins for historical narrative, and customer Git wins for a published document.
A narrative can influence a planner, but it changes control only when an
authorized actor submits a separate typed command that passes current authority
checks. A staged document remains memory until the gateway publishes it.

## NSC-v1: one behavioral contract, two adapters

Nightshift State Contract v1, or **NSC-v1**, is the three ports and their
observable guarantees. Prose explains it; one shared conformance suite defines
it. The suite executes the same canonical traces, randomized races,
kill/restart points, clock-jump cases, migration fixtures, isolation checks,
redaction cases, and deletion/export checks against local and production
adapters, then compares normalized receipts. Performance and retrieval ranking
may differ; authority behavior may not.

### Port operations

| Port | Operations | Required result shape |
|---|---|---|
| ControlPort | `mutate`, `query`, `acquire`, `renew`, `allocate` | A mutation receipt includes repository sequence, changed entity versions, audit ID, and result. Queries include observed sequence and a declared consistency/freshness class. Fence tokens include resource, monotone generation, authority time, and expiry. |
| MemoryPort | `begin_artifact`, `put_chunk`, `finalize`, `get`, `search`, `export`, `delete` | Upload and deletion receipts are idempotent. Finalize returns an immutable verified manifest. Search returns advisory hits with citation, score, freshness, and provenance. Export is anchored to an observed control sequence. |
| PublicationPort | `stage`, `review`, `approve`, `publish`, `inspect` | Review candidates bind artifact, digest, path and base. Approval adds reviewer, policy and expiry. Publish returns resulting full commit and tree OIDs or a conflict; it consumes the approval once. |

### Normative invariants

1. Every call is tenant- and repository-scoped, and authorization is checked at
   object level. A cross-tenant identifier is indistinguishable from a missing
   identifier.
2. Mutations are idempotent by command ID. Reuse with different canonical
   request bytes is rejected; an identical retry returns the original receipt.
3. A successful module mutation and its durable module audit receipt commit
   together. Control also commits any required outbox row atomically. Artifact
   bytes are durable before their manifest becomes visible; orphan bodies may
   be collected, but a manifest never points to partial bytes.
4. Entity updates require expected versions. Claim-, lease-, slot-, Program-,
   and publication-sensitive mutations also require the current fencing
   generation.
5. Repository sequence numbers are strictly monotone, never duplicated and
   never reused. Gaps are allowed after a crashed range holder. Per-task event
   order is derived from repository sequence; there is no second gapless task
   counter.
6. Authority time comes from the store. Client clocks never decide lease
   validity.
7. Every run, artifact, narrative decision, evidence item, and publication is
   bound to an immutable source snapshot. Query results carry observed sequence
   and freshness.
8. Lists are cursor-paginated and bounded. Current control reads are read-after-
   write consistent; bounded-stale and semantic reads declare maximum lag.
9. Artifact finalization is atomic at manifest level: a reader sees no artifact
   or one verified digest and complete chunk manifest, never a partial object.
10. Retrieval ordering may differ by adapter, but scope, redaction, provenance,
    deletion, freshness declarations, and inability to mutate control may not.
11. **Every row carries `tenant_id` even locally — local mode is multi-tenant
    code with one tenant, not single-tenant code.** Repository-owned rows also
    carry `repository_id`, and scoped keys prevent cross-boundary references.
12. **A fix lands as a conformance test first, then two implementations.** An
    adapter-only behavior change is a contract-version proposal, not a hidden
    conditional.

The named divergence risks are SQLite/PostgreSQL isolation and uniqueness
semantics, SQL dialect and migration DDL, collation and timestamp precision,
filesystem rename versus object-store finalization, local encryption shortcuts,
client wall time entering lease logic, and single-process locks creeping into
local code. The local conformance run therefore uses **multiple operating-system
processes**, not merely multiple tasks in one process.

## Deployment adapters

### Local and offline

A local coordinator owns the single SQLite writer for a state root. Workers in
multiple processes call the coordinator instead of carrying ad hoc SQL. SQLite
uses WAL, short transactions, busy timeouts, and immediate write acquisition for
contested changes. The coordinator is a mechanism for serialization, not an
alternate authority.

Artifact bodies, encrypted exports, bare source mirrors, retained source packs,
and disposable clones live beneath a configured external state root. The body
adapter is a filesystem content-addressed store whose finalize path writes a
temporary object, flushes it, verifies its digest, and atomically renames it on
one filesystem. Search uses SQLite text filtering plus a bounded exact vector
rerank. Local keys are wrapped by an OS keystore or a passphrase; offline use
does not require a network.

The conformance suite launches the coordinator and competing worker processes,
kills holders at transaction boundaries, retries commands, injects a wall-clock
jump, and proves that the same normalized outcomes emerge as in production.

### Production multi-tenant

One highly available PostgreSQL primary/standby deployment owns control rows,
manifests, audit receipts, outbox jobs, full-text indexes, and pgvector indexes.
Per-tenant vector partitioning defers the dedicated-vector-store trigger.
Schemas, forced row-level security, roles, and scoped keys follow port
ownership. One stateless application process is sufficient at T0; durable state
does not depend on it.

Encrypted object storage holds artifact bodies, upload chunks, source packs,
mirrors, and export bundles. Tenant-scoped disposable workspace disk holds no
authority. Provider volume/database/backup encryption is supplemented by
application envelope encryption where the data remains usable in encrypted
form.

## Data model and concurrency

Durable IDs are UUIDv7, generated without tenant sequence leakage. Every row
has `tenant_id`; repository-owned rows have `repository_id`. Foreign, unique and
index keys include scope. Timestamps are timezone-aware UTC annotations;
repository sequence and entity version establish order.

| Entity family | Essential fields and invariant |
|---|---|
| Tenants and repositories | Tenant status, retention policy and key reference gate every operation. Repository records include stable identity, object format, source locator metadata, and current `authority_epoch`. |
| Source snapshots | Full commit and tree OIDs, object format, observed ref, pin/pack digest, closure-manifest digest, synthetic flag, parent and patch digest when synthetic, retention state, and `source_not_retained`. Immutable. |
| Tasks and dependencies | Current state, version, priority, source snapshot and last repository sequence. Dependencies are same-repository, non-self edges; insertion is serialized per repository and cycle-checked. |
| Task transition events | Immutable event ID, repository sequence, task, before/after state, version, actor/run, command ID and cited decision artifact. Repository sequence is unique; per-task order is its filtered order. |
| Runs, slots, claims and leases | Attempt, input/output snapshots, holder, resource, expiry, state and 64-bit generation. At most one active Program authority, task claim and slot holder in scope. |
| Repository sequences and idempotency | Transactionally allocated next value/ranges; abandoned ranges leave gaps. A scoped command ID records request digest and original receipt; mismatched reuse fails. |
| Audit and outbox | Each module appends its own normalized audit receipt. Outbox rows carry event ID, handler kind/version, attempts, availability and claim generation. A read-only audit view does not become another authority. |
| Artifact manifests and derived memory | Per-tenant plaintext content digest, object locator, sizes, kind, run/task and snapshot provenance, redaction/retention/key versions and finalize time. Chunk, embedding, summary and citation rows carry explicit `derived_from` lineage. Cross-tenant deduplication is forbidden. |
| Publications | Staged artifact/digest, exact path and base, policy, requester/reviewer, expiry, state/version, attempt, resulting full commit/tree OIDs and immutable receipt. |

### Transaction rule

Single writer means one logical authority for a repository aggregate, not one
global process. A control mutation performs the following atomically:

1. Lock or compare-and-swap the repository and target rows.
2. Recompute state-machine, dependency, slot, claim, lease and authorization
   predicates from current control rows; check expected versions and fences.
3. Allocate the next repository sequence. The sequence is monotone, gaps are
   allowed, and uniqueness makes duplicates impossible.
4. Update the projection and append the event, module audit, required outbox
   work, and idempotency receipt in the same commit.

Acquire or reclaim increments the resource's generation. A lease has a
three-minute TTL and normally renews every minute. Expiry by itself is not a
fence: every later sensitive mutation must present the generation that the
authority still considers current. PostgreSQL store time decides production
expiry. Local authority uses persisted wall time and a wall/monotonic jump
detector; a jump greater than 30 seconds pauses new grants and revalidates
leases.

Two racing Program turns contend on the repository authority row; one receives
generation N and the other receives the current-holder conflict. A worker that
dies stops renewing; a replacement receives N+1. If the old worker returns, it
may preserve a late immutable artifact marked as fenced evidence, but it cannot
transition the task, accept an output snapshot, release the new holder, or
publish. A lost response after commit is recovered by replaying the same command
ID and receiving the stored receipt.

Schema changes use expand/backfill/verify/contract with a checksummed migration
ledger and idempotent checkpoints. Transactional DDL rolls back on failure;
non-transactional work does not enter contract until verification succeeds.

## Transactional outbox

Indexing, retention, deletion, export and other asynchronous effects begin as an
outbox/job row committed with the domain event that requires them. Handlers are
at-least-once, make no ordering assumption, and deduplicate by event ID plus
handler version. Claims are fenced and retry state is durable. A broker is not
an authority and is not present at T0; if later added, it only wakes workers
while the outbox remains the durable job record.

## Zero-file modes

The default **zero-Nightshift-file mode** writes no `.omni/`, Nightshift
database, registry, transcript, report, generated internal document, workspace,
Git alternate, lock file, ignore-file edit, or linked-worktree metadata into the
customer checkout or its `.git` directory. Nightshift may read an explicitly
registered checkout, but it fetches into a tenant-scoped external bare mirror
and operates in external disposable clones. Control uses ControlPort and all
unpublished narrative uses MemoryPort.

An explicitly reviewed document may pass through PublicationPort and becomes
customer-owned when committed. Customers requiring literal no generated bytes
select **no-generated-file mode**: publication writes are disabled and the
gateway can return only a reviewed patch for the customer to apply independently.
Both variants therefore satisfy a command-level footprint check; the stricter
variant leaves no Nightshift-produced repository write at all.

The costs are real:

- Repo-local discoverability is lost; only published documents appear to a
  developer browsing the repository.
- Nightshift history no longer travels with `git clone`; the external state root
  or an export bundle must be backed up separately.
- Working-note review is no longer available through plain `git diff`; it needs
  the Nightshift viewer or an export.
- External mirrors and workspaces add disk, synchronization, backup and cleanup
  cost. The planning baseline is a 2 GiB initial packed mirror, 3 GiB/month of
  mirror delta per active repository, and 10 GiB workspace high-water at day
  one. A repository over 20 GiB packed or growing over 1 GiB/day needs its own
  capacity plan.

Unpinned workspaces expire after seven days. Source objects required by retained
evidence follow the associated retention policy.

## Provenance that survives Git history changes

Every run, artifact, decision, evidence item and publication names a stable
repository ID and immutable source snapshot rather than relying on repository or
branch names. Snapshot capture records the Git object format, full commit and
tree OIDs, observed ref, capture time, private retention ref, and a content-
addressed pack or bundle digest.

The closure manifest records every materialized submodule commit OID and every
required Git LFS object OID/digest. A pinned superproject commit with missing
submodule or LFS bytes is not a complete retained snapshot. A run starts only
after policy-required closure has been fetched, pinned, and verified.

For an explicitly permitted dirty checkout, Nightshift copies the tree to an
external workspace, creates a **synthetic snapshot commit there**, records its
parent and canonical patch digest, marks it synthetic, and pins its bundle. It
does not modify the customer checkout or its Git metadata.

Rebase, force-push and squash create new commit identities. Nightshift never
rewrites old provenance to a convenient new head. It records `derived_from`
edges among input snapshot, worker output snapshot, reviewed patch and resulting
commit/tree. Equal patch IDs or tree digests are advisory evidence of
equivalence, not proof that two commits are identical.

Every exported provenance envelope includes tenant, repository, artifact and
content digest; producer run and identity; input and optional output snapshot;
control repository sequence; creation time; redaction policy; and tool/model
metadata. If source retention expires before narrative retention, the exact OID
and lineage remain, but the snapshot is visibly marked `source_not_retained`.
At that point Nightshift cannot reconstruct the source bytes and must not imply
that it can.

## Layered semantic-memory fence

Only finalized, policy-allowed, redacted material is indexed. By default that is
reports, decisions, handoffs, evidence captions and transcript summaries rather
than raw tool dumps. Chunks carry scope, artifact, snapshot, validity interval,
retention, redaction, chunker and embedding versions. Search results carry
citations and freshness. Four independent mechanisms keep memory advisory:

1. **Principal and storage separation.** The scheduler principal has no memory
   search capability. Retrieval roles have zero grants on control tables in
   production; local dependency injection exposes no control mutation object to
   them.
2. **Capability, fence and type separation.** Planners receive
   `advisory_context`, never a control entity, approval, or FenceToken. Control
   mutations require authority-issued capabilities and no control command
   accepts advisory context as a predicate result.
3. **Authoritative recomputation.** Inside the same transaction as a mutation,
   ControlPort recomputes every scheduling predicate from current control rows.
   Retrieved text cannot satisfy a dependency, assign a slot, set priority,
   renew a lease, allocate a sequence, or approve publication.
4. **Audit and executable denial.** Every mutation records actor, authority and
   fence; denied crossing attempts are audited. Conformance cases prove a memory
   principal cannot obtain a mutation handle or change control outcomes.

Deleting and rebuilding every vector may change retrieval quality, but it
cannot change the task graph or select a worker.

## Tenancy, encryption, redaction and lifecycle

### Isolation and authorization

Tenant scope is part of every row, key, object lookup, source mirror, workspace,
job and log correlation. Production forces PostgreSQL row-level security even
for application roles, sets one authenticated tenant context per transaction,
and uses object-level authorization. Cross-tenant body deduplication is refused
because a plaintext content hash shared across tenants would leak content
existence. Background jobs carry bounded tenant scope rather than scanning an
unrestricted global namespace. Local mode exercises the same rules with one
tenant and normally one separate state root.

### Encryption and key custody

Transport, managed disks, database volumes and backups are encrypted. Artifact
bodies and export bundles use application envelope encryption: per-object data
keys are wrapped under a versioned tenant key. Production defaults to a
hardware-backed service master key; a contract may select customer-controlled
wrapping or a dedicated cell. Local mode uses an OS keystore or passphrase. Loss
of the only local wrapping secret is explicitly unrecoverable.

Sensitive relational narrative and credentials are application-encrypted.
Embeddings and the control fields required for indexed coordination cannot be
individually envelope-encrypted while they remain searchable. This distinction
sets the deletion promise below.

### Redaction and retention

Secrets, configured patterns, credentials and high-risk personal data are
scanned and redacted before durable transcript finalization and before indexing.
The manifest records scanner and policy versions and irreversible redaction
counts, never the secret. A separately authorized encrypted raw vault may have a
shorter policy, but it is never indexed. A failed scan quarantines narrative and
blocks search/publication without preventing control audit.

Defaults are:

| Class | Default retention |
|---|---:|
| Unpinned workspace | 7 days |
| Unfinalized upload | 24 hours |
| Raw transcript | 90 days |
| Report, handoff and evidence | 365 days |
| Control history, audit, decisions and publication receipts | Tenant lifetime |

Tenant policy, an authorized deletion, and visible legal holds override these
defaults. Deletion and legal policy win over indefinite explainability.

### Export

Export takes a consistent observed control sequence and emits paginated
NDJSON/CSV control and audit data, decrypted original artifacts, manifests and
checksums, publication receipts, key metadata, and optional retained Git bundles
with closure manifests. Items created after the selected sequence are named as a
tail; they are not silently mixed into the snapshot. Local and managed modes
share this format.

### Redaction and deletion completeness

Post-hoc redaction writes a verified redacted successor before tombstoning the
original. Artifact deletion first tombstones access and revokes links, then
walks typed `derived_from` edges to delete every content-carrying descendant:
chunks, embeddings, summaries, snippets and other representations that could
retain the removed source text. A redacted replacement is explicitly typed as a
replacement, not as an old-content-bearing derivative. Expiry of retained Git
source is also non-propagating: narrative may remain under its own policy but
must say `source_not_retained`. A reindexer treats tombstones and lineage as
authority, so it cannot resurrect removed text. The deletion job is idempotent
and ends with a per-adapter canary enumeration and receipt.

Tenant deletion changes status to `deleting`, blocks new commands, revokes
sessions and publication credentials, advances lease generations, drains or
fences in-flight work, optionally exports, and removes live rows, indexes,
objects, mirrors and workspaces. It destroys wrapped tenant keys and verifies
that every live adapter reports zero tenant canaries.

Deletion from shared backups is **not immediate and is not cryptographic
erasure**. Backup-resident ciphertext and relational/index pages age out only on
backup expiry, whose declared maximum is **35 days**. Destroying a tenant key
makes envelope-encrypted objects unreadable sooner, but that does not justify
calling searchable shared-backup data erased. This limitation is disclosed to
customers. A minimal non-content deletion receipt may remain only when disclosed
law or contract requires it.

Removing Nightshift invokes the same export/deletion flow and revokes integration
credentials. Approved Git documents remain because the customer owns them and
may revert them normally.

## Publication is the sole repository egress

Only PublicationPort holds the tenant-scoped credential that can write to a
customer repository, and it enforces a per-tenant path allowlist. Workers,
memory indexers, source mirror readers and migration importers have no egress
credential.

A candidate begins as an immutable finalized artifact. Review produces a
one-use approval bound to the **exact content digest (therefore exact bytes),
exact target path, expected base SHA, authenticated reviewer, policy version,
and expiry**. The reviewer is never the producing worker. Any byte, path, base,
reviewer context, policy or expiry change invalidates approval and requires a
new review. Publish compares the observed customer base, consumes the approval
atomically with its attempt, and records the resulting full commit/tree OIDs.
Retries use idempotency receipts; a base mismatch conflicts rather than silently
rebasing approved bytes.

Normal zero-file mode can publish approved customer-owned documents. Strict
no-generated-file mode wires the patch-returning adapter and never gives the
gateway a repository write credential.

## Migration from `.omni/` to external authority

Migration is per repository and is governed by an external bootstrap registry
containing `active_backend` and monotone `authority_epoch`. There is exactly one
durable authority at every moment and one **quiesced authority switch**. There
is no dual-authority shadow period.

| Phase | Work | Authority, rollback and crash behavior |
|---|---|---|
| **1. Inventory** | Read `.omni/`, generated documents, worktrees and Git status. Classify every item as control, memory, approved customer document, unknown or disposable; write the dry-run report externally. | Legacy remains authority. Unknown data is neither guessed nor deleted. Rerun is harmless. |
| **2. Snapshot** | Briefly quiesce, take a consistent legacy snapshot, and record high-water sequence, counts and checksums externally; then resume legacy work. | Legacy remains authority. An incomplete copy is rejected by digest. |
| **3. Shadow import** | Idempotently import projections/events into a command-disabled namespace, copy/redact/finalize narrative, pin source closure, and retain old source keys plus `migration_id`. | Legacy remains authority; imported state accepts no live commands. The namespace can be dropped or tombstoned. |
| **4. Compare** | Replay legacy changes after the snapshot; compare graph, states, claims, sequences, runs, artifact counts and sampled bytes; execute NSC-v1 reads against both representations. | Legacy remains authority. Resume from the recorded high-water mark. No cutover with any unexplained mismatch. |
| **5. Quiesced switch and reversible soak** | Acquire migration authority, stop new Program turns, drain or expire workers, import the final delta, verify no active legacy lease, increment `authority_epoch`, and atomically flip `active_backend`. Retain the old snapshot read-only and journal post-switch commands during a soak. | The binding flip makes the new adapter sole authority. A crash reads the bootstrap registry to learn which side won. Rollback requires another quiesce, replay into a fresh legacy copy, validation, a new epoch and another single flip—never reopening stale files. |
| **6. Decommission** | After acceptance, remove `.omni/` and unapproved generated documents through a reviewed publication operation; retain approved customer documents. After the rollback window and restore drill, delete legacy snapshots and credentials under policy. | The cleanup commit is revertible customer Git history, but reverting it does not restore legacy authority. Retained exports support later import. |

The commit that removes `.omni/` is itself a Publication Gateway operation and
is the gateway's **first real customer**. This proves path policy, exact-byte
review, base-SHA checking, customer ownership and publication audit during the
migration that makes those mechanisms authoritative.

## Operations and scale

Every command emits structured metadata including tenant, repository, command,
actor, run, input snapshot, repository sequence, entity version, fence,
duration, outcome and audit ID; content and secrets are excluded. Operators
monitor authority latency, lease age, outbox depth/oldest age, failed jobs,
finalization errors, search freshness, retained bytes and cost by tenant.

All components begin at T0. Numerical promotion and reversal triggers,
replaceable adapter guarantees, current cost, and higher tiers are defined in
[`SCALE-LADDER.md`](./SCALE-LADDER.md). The success case is a measurement point,
not an automatic instruction to add infrastructure.

## Decision records

This document states what the design **is**. It deliberately does not restate the
options that were rejected or what would reverse each call — those live in the
ADRs, which are the thing to read before arguing with anything above. Each was
reconciled from two blind architecture drafts; the drafts are kept unedited at
[`PROPOSAL-codex.md`](./PROPOSAL-codex.md) and
[`PROPOSAL-claude.md`](./PROPOSAL-claude.md), and the decision-by-decision
reconciliation with its tie-break rules is in `.omni/decisions.md`.

| ADR | Decision | Governs |
|---|---|---|
| [ADR-0076](../decisions/ADR-0076-stack-and-shape.md) | Merged storage stack, modular monolith with ports | Capacity posture, component choices, the four rejections (Mem0, temporal graph, Redis, dedicated vector DB) |
| [ADR-0077](../decisions/ADR-0077-three-seams-not-five.md) | Three seams, not five | Shape; caps how many crews can ever run in parallel; seam-4 promotion trigger |
| [ADR-0078](../decisions/ADR-0078-nsc-v1-one-contract-two-adapters.md) | NSC-v1: one contract, two adapters | Local/production equivalence, conformance suite, what is explicitly not contract |
| [ADR-0079](../decisions/ADR-0079-provenance-pinned-snapshots-closure-derived-from.md) | Pinned snapshots, closure manifests, `derived_from` | Provenance across rewritten history, dirty trees, `source_not_retained` |
| [ADR-0080](../decisions/ADR-0080-tenancy-encryption-limits-of-deletion.md) | Tenant encryption, and the stated limits of deletion | Isolation, redaction, retention, export, backup-expiry disclosure |
| [ADR-0081](../decisions/ADR-0081-publication-gateway-only-repository-write-path.md) | Publication Gateway is the sole egress | Zero-file modes, digest-bound one-use approval, path allowlist |
| [ADR-0082](../decisions/ADR-0082-migration-authority-epoch-quiesced-switch.md) | Authority epoch with one quiesced switch | Migration from `.omni/`, and why dual authority never exists |

An accepted ADR's decision is never edited. A changed call is a new, superseding
ADR linked both ways.
