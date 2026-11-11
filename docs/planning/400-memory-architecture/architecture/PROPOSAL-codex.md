# Nightshift state and memory architecture — independent proposal

Status: blind architecture draft. This document makes no implementation or
cutover commitment.

## Capacity gate and decision summary

The arithmetic in CAPACITY.md comes first:

- One active repository means five continuously active workers, about 40 tasks,
  400 task-state transitions, 60 runs, and 280 artifact manifests per day.
- The modeled request load is 0.6 operations/second average and 6/second peak.
  At 100 times the workload it is about 60/second average and 600/second peak.
- Day one creates about 8,000 rows/day. The 100-times case creates about 800,000
  rows/day, or 24 million/month. This is not a high-throughput system.
- Narrative bytes are the larger concern: about 250 MiB of transcript per
  five-worker day, about 10 GiB/month of state and memory after index allowance,
  plus about 3 GiB/month of source-mirror delta. At 100 times, those become about
  1 TiB and 300 GiB/month respectively.
- A long transcript is expected to be at most 100 MiB; the accepted object limit
  is 2 GiB and larger output is split. Day-one infrastructure is budgeted at
  about $150/month, with a $300 ceiling; 100-times is estimated at $2,000/month,
  with a $3,000 ceiling.

Those numbers select a transactional authority plus cheap blob storage, not a
distributed event platform. The local deployment uses SQLite and a filesystem
content-addressed store outside the customer repository. Production uses one
high-availability PostgreSQL instance with pgvector, object storage, and one
stateless application process. A database outbox/job table handles asynchronous
indexing and deletion. Git is used only for source snapshots and explicitly
approved customer-owned documents.

Assumptions not supplied by the brief:

1. Day one is one tenant and one active repository; 100-times is 100 tenants,
   250 registered repositories, and 100 active-repository equivalents.
2. A worker completes eight substantive tasks/day and produces 50 MiB of raw
   transcript/day.
3. Default retention is seven days for disposable workspaces, 90 days for raw
   transcripts, 365 days for reports/handoffs/evidence, and tenant lifetime for
   control history, decisions, publication records, and audit events. Tenant
   policy and deletion requests override those defaults.
4. The average packed source repository is 2 GiB and adds 100 MiB/day while
   active. Repositories over 20 GiB packed or adding over 1 GiB/day require an
   onboarding capacity exception.
5. A managed installation may use shared infrastructure with logical tenant
   isolation unless a customer contract requires a dedicated cell or key.

## Shape

The cheapest correct shape is a modular monolith with external storage adapters:

    Program, workers, operator
               |
      authenticated command/query API
               |
    +----------+------------+----------------+
    | Control authority     | Memory vault   | Publication gateway
    | state machine         | artifacts      | review + Git write
    | claims and leases     | retrieval      |
    +----------+------------+----------------+
               |            |                 |
        transactional       blob/CAS      external source mirror
        store + outbox      + index       and customer remote

The boxes are logical ownership boundaries, not day-one network services. They
may share a process and, in production, a database instance, while owning separate
tables, credentials/capabilities, migrations, and interfaces. No module reads
another module's tables directly. This keeps failures and 3am debugging in one
structured log stream without pretending that six peak requests/second needs
distributed coordination.

### Independent seam count: 3

There are exactly **three independent seams**, so no more than three delivery
crews can work without a shared-path lease. Authentication, provenance,
encryption, the source mirror, migrations, and observability are cross-cutting
constraints and are deliberately not counted as extra seams.

| Seam | Owns | Interface | Can two teams genuinely work either side? |
|---|---|---|---|
| 1. Control State Boundary | Tasks, dependencies, task transitions, runs, slots, claims, leases, repository sequences, idempotency, control audit receipts, and the job outbox | ControlPort v1: versioned commands and current/bounded-stale queries; every mutation returns repository sequence, entity version, and audit ID | **Yes.** Orchestrator/worker clients can use a contract stub while the authority team implements transactions. They share only versioned command/event schemas, not tables. |
| 2. Narrative Memory Boundary | Transcript/report/decision/handoff/evidence manifests and bodies, artifact audit receipts, redaction, retention class, chunks, embeddings, keyword/vector retrieval, and memory export | MemoryPort v1: resumable put/finalize/get/search with tenant, repository, source snapshot, content digest, and freshness/provenance on every result | **Yes.** Producers and retrieval consumers can work against a fake object/index adapter while the memory team owns persistence and ingestion. Control IDs are opaque references validated through ControlPort. |
| 3. Customer Publication Boundary | Publication requests, review approvals, path policy, base/source snapshot checks, generated patches, publish attempts, publication audit receipts, and resulting customer commit identity | PublicationPort v1: stage, review, approve, publish, inspect; an approval is bound to exact bytes, path, base SHA, and expiry | **Yes.** Document producers can stage immutable artifacts while an integration team implements Git adapters and review UX. Workers never receive the publication credential. |

Deployment adapters on the other side of these ports are not a fourth seam:
local and production implementations must pass the same contract suite. Nor is
the retrieval index a fourth seam; it is a replaceable implementation detail of
MemoryPort.

### Operational shape

- One command produces one structured log record with tenant ID, repository ID,
  command/idempotency ID, actor, run ID, input snapshot, repository sequence,
  entity version, fencing generation, duration, outcome, and audit ID. Content
  and secrets never appear in service logs.
- A transactional outbox row is committed with every event that needs indexing,
  deletion, export, or notification. Consumers are at-least-once and deduplicate
  by event ID plus handler version. There is no separate queue at T0.
- Health reports show lease age, outbox oldest age, failed job count, artifact
  finalize failures, active tenant/repository cardinality, database latency, and
  retained bytes/cost per tenant. An operator can reconstruct a task from its
  ordered event receipts without opening a transcript.
- Every future path/schema/module is assigned one owner in docs/owners.md before
  implementation, preserving the repository's single-owner convention.

## The three-way separation

| Plane | Contents and location | Authority | What prevents promotion by accident? |
|---|---|---|---|
| **CONTROL** | Current tasks, dependency edges, state-machine events, worker runs, slots, claims, leases, generation/fencing tokens, repository sequences, idempotency receipts, and publication authorization. Stored in the ControlPort transactional store. | ControlPort is the sole live authority for scheduling and current operational state. Ordered transition events explain history; current rows are projections updated in the same transaction. | Only control command handlers have write capability. They accept typed commands, expected entity versions, and current fencing tokens—not arbitrary documents or retrieved text. Database roles in production and capability-scoped adapter objects locally deny MemoryPort a control write path. |
| **MEMORY** | Transcripts, reports, narrative decisions, handoffs, evidence, summaries, redacted search text, and embeddings. Bodies live in the artifact store; immutable manifests and index metadata live behind MemoryPort. | The finalized artifact bytes plus manifest are authoritative for what was recorded. Retrieval ranking and generated summaries are derived and disposable. Memory is never authoritative for task state, priority, dependency satisfaction, claims, or publication approval. | Separate IDs, tables/table prefixes, service scopes, retention policies, and APIs. Search results have an advisory_context type and cannot be supplied as a ControlPort mutation receipt. Reindexing cannot emit control commands. |
| **APPROVED CUSTOMER-OWNED DOCUMENTS** | ADRs, PRDs, release notes, or other reviewed files intentionally committed to the customer repository. Staged bytes remain a memory artifact until publication. | After publication, the customer Git repository at the resulting commit is authoritative for the document. The publication record is audit evidence, not a competing editable copy. | Only PublicationPort has a repository write credential. Approval is an explicit, expiring capability bound to content digest, target path, expected base SHA, reviewer, and policy version. Any byte/path/base change invalidates it. Workers and the memory indexer cannot call publish. |

If the planes disagree, current CONTROL facts win for operations, the finalized
artifact wins for historical narrative, and customer Git wins for a published
document at a named commit. A narrative decision affects scheduling only when an
authorized actor issues a separate ControlPort command that cites that decision;
the audit event then records both.

The reviewer must be an authenticated human or customer-policy integration, never
the producing worker. Policy may additionally require reviewer and requester to
be distinct principals; the approval receipt records whether that rule applied.

## Nightshift State Contract v1: one behavior, two deployments

The exact shared contract is **Nightshift State Contract v1 (NSC-v1)**. It is the
union of the three ports above plus these canonical operations:

    ControlPort.mutate(
      tenant_id, repository_id, command_id, actor,
      expected_versions, optional_fence, command
    ) -> MutationReceipt(repository_sequence, versions, audit_id, result)

    ControlPort.query(
      tenant_id, repository_id, query, consistency, cursor, limit
    ) -> Page(items, next_cursor, observed_sequence)

    ControlPort.acquire(
      resource, holder_run_id, command_id, ttl
    ) -> FenceToken(resource, generation, authority_time, expires_at)

    ControlPort.renew(fence_token, command_id) -> FenceToken
    ControlPort.allocate(namespace, count, command_id) -> SequenceRange

    MemoryPort.begin_artifact(
      scope, command_id, kind, provenance, media_type,
      expected_size, expected_digest, retention_class
    ) -> UploadSession(session_id, chunk_size, expires_at)

    MemoryPort.put_chunk(
      scope, session_id, ordinal, chunk_digest, bytes, command_id
    ) -> ChunkReceipt

    MemoryPort.finalize(
      scope, session_id, expected_chunk_count, expected_digest, command_id
    ) -> ArtifactManifest

    MemoryPort.get(scope, artifact_id, optional_byte_range) -> Artifact

    MemoryPort.search(
      scope, query, mode, as_of_snapshot, filters, cursor, limit
    ) -> Page(SearchHit(citation, score, freshness, provenance))

    MemoryPort.export(scope, observed_control_sequence, policy, command_id)
      -> ExportReceipt
    MemoryPort.delete(scope, artifact_id, expected_version, command_id)
      -> DeletionReceipt

    PublicationPort.stage(
      scope, artifact_id, content_digest, path, base_snapshot_id, command_id
    ) -> ReviewCandidate

    PublicationPort.review(
      candidate_id, reviewer, policy_version, outcome, comment, command_id
    ) -> ReviewReceipt

    PublicationPort.approve(
      candidate_id, reviewer, exact_digest, exact_path,
      exact_base_snapshot, expiry, command_id
    ) -> OneUseApproval

    PublicationPort.publish(
      one_use_approval, observed_customer_base_oid, command_id
    ) -> PublicationReceipt(result_commit_oid, result_tree_oid)

    PublicationPort.inspect(scope, publication_id) -> PublicationRecord

NSC-v1 invariants are normative:

1. All calls are tenant- and repository-scoped; authorization is checked at the
   object, not endpoint, level. Cross-tenant IDs are indistinguishable from
   missing IDs.
2. Mutations are idempotent by command ID. Reusing a command ID with different
   canonical request bytes is an error; retrying identical bytes returns the
   original receipt.
3. Every successful module mutation includes its durable module audit receipt.
   A successful control acknowledgement includes its control audit event and,
   when needed, an outbox event in the same transaction. Artifact bytes are
   durably acknowledged before the manifest and memory audit commit; an orphaned
   body is harmless and collected, while a manifest never points at partial data.
4. Entity updates use expected versions. Claim-, lease-, slot-, Program-, and
   publish-sensitive updates also require the current fencing generation.
5. Repository sequence numbers are strictly increasing and never reused. Gaps
   after a crashed range holder are allowed; duplicate numbers are not.
6. Authority time comes from the storage authority. Client clocks are never used
   to decide lease validity.
7. Every run, artifact, decision, evidence item, and publication is bound to an
   immutable source snapshot. A query result carries its observed sequence and
   freshness class.
8. Lists are cursor-paginated and bounded. Current control reads are read-after-
   write consistent; bounded-stale and semantic reads advertise their maximum lag.
9. Artifact finalization is atomic at the manifest level: readers see either no
   artifact or one verified digest and complete chunk manifest, never a partial
   object.
10. Retrieval order may differ across implementations, but scope, redaction,
    provenance, deletion, freshness, and the inability to mutate control state
    may not.

### Local/offline adapter

- A local coordinator owns one SQLite database per local state root, uses WAL,
  short transactions, busy timeouts, and BEGIN IMMEDIATE for contested writes.
  Multiple workers communicate through the coordinator rather than each carrying
  ad hoc SQL. This preserves one logical writer while allowing overlapping runs.
- Artifact bodies and encrypted export bundles use a filesystem
  content-addressed store under a configured state root outside the repository.
  Finalization uses write-to-temporary, fsync, digest verification, and atomic
  rename on one filesystem.
- A bare source mirror and disposable clones/workspaces also live under that
  root. Search uses SQLite full-text filtering and a bounded exact vector rerank
  over the filtered candidate set. This is sufficient for roughly 414,000
  90-day search chunks at one active repository.
- Local encryption uses a passphrase- or operating-system-keystore-wrapped tenant
  key. The mode remains usable without a network.

### Production multi-tenant adapter

- One high-availability PostgreSQL primary/standby deployment owns control,
  manifests, audit, jobs, text search, and pgvector indexes. Logical schemas and
  database roles follow the module boundaries; row-level security and mandatory
  tenant predicates enforce shared-schema isolation.
- Encrypted object storage holds artifact bodies, resumable chunks, source packs,
  and exports. Application processes are stateless; disposable workspace disks
  are tenant-scoped and never an authority.
- One application process is enough at day one. Restart plus the durable outbox
  meets the one-hour RTO. A second process is added only at the compute trigger;
  all correctness already survives it.

### The main divergence risk

SQLite and PostgreSQL differ in writer concurrency, isolation, NULL uniqueness,
collation, time precision, JSON behavior, partial indexes, and migration DDL.
Filesystem rename and object-store finalization fail differently. Silent
divergence would occur if domain rules move into adapter-specific SQL, if local
tests use one process only, if client wall time enters leases, or if retrieval
rank is mistaken for a contract invariant.

Prevention is executable, not documentary: a single NSC-v1 conformance suite
runs the same canonical command traces, randomized races, kill/restart points,
clock-jump scenarios, migration fixtures, redaction cases, and deletion/export
checks against both adapters. Receipts are normalized and compared. Every release
must pass both adapters; adapter-specific behavior requires a contract version,
not a hidden conditional.

## Data model

All durable entities use sortable UUIDv7 IDs generated without tenant sequence
leakage. Every row includes tenant_id; every repository-owned row also includes
repository_id. Foreign keys include tenant/repository scope so an ID cannot be
attached across boundaries. UTC timestamps include timezone, but ordering relies
on repository sequence and entity version rather than timestamp.

### Core entities

| Entity/table family | Key fields | Important invariants |
|---|---|---|
| tenants | tenant_id, status, retention_policy_id, key_reference | status gates every operation; deleting blocks new leases before data removal |
| repositories | (tenant_id, repository_id), canonical locator, object format, authority epoch | locator is metadata, not identity; authority epoch changes on migration/cutover |
| source_snapshots | snapshot_id, repository_id, Git object format, full commit OID, tree OID, pin/bundle digest, observed ref, synthetic flag | immutable; commit object is pinned externally before a run may become active |
| tasks | task_id, repository_id, current_state, version, priority, source_snapshot_id, last_sequence | current projection; update requires expected version |
| task_dependencies | (task_id, depends_on_task_id, kind), created_sequence | same repository only; no self-edge; cycle check serialized per repository |
| task_transition_events | event_id, repository_sequence, task_id, from/to, task_version, actor/run, command_id, decision_artifact_id | immutable and unique on repository sequence and command ID |
| worker_runs | run_id, task_id, attempt, input_snapshot_id, output_snapshot_id, state, version | attempt is allocated, not guessed; terminal state is monotonic |
| worker_slots | (repository_id, slot_number), active_run_id, generation, lease_id | at most one current holder per slot |
| task_claims | claim_id, task_id, run_id, generation, lease_id, state | at most one active claim per task and one per claimed slot |
| leases | (resource_type, resource_id), holder_run_id, generation, expires_at, state | acquire/reclaim increments generation; renewal never lowers it |
| program_authorities | repository_id, holder_turn_id, generation, lease_id | at most one unexpired Program authority per repository |
| repository_sequences | (repository_id, namespace), next_value, version | allocated transactionally; gaps allowed, reuse forbidden |
| idempotency_receipts | tenant_id, scope, command_id, request_digest, result_digest/receipt, expires_at | unique in scope; mismatch is rejected |
| audit_events (logical family per module) | audit_id, repository_sequence where applicable, actor, action, target, before/after digests, snapshot_id, outcome | append-only to application roles; each module commits its own receipt with its mutation; a read-only AuditView unions normalized receipts without becoming a fourth authority |
| outbox_jobs | event_id, handler_kind/version, state, attempt, available_at, generation | at-least-once; handler dedupe key is unique |
| artifact_manifests | artifact_id, kind, repository_id, run/task, input/output snapshot IDs, content digest, object locator, byte size, media type, redaction status, retention class, key version, finalized_at | immutable after finalize; object locator alone is never trusted without digest |
| memory_chunks | (artifact_id, ordinal, index_version), redacted text or text locator, embedding/model/chunk version, valid time | derived and deletable; never referenced by control constraints |
| narrative_links | link_id, subject/object artifact IDs, relation, valid_from_snapshot, valid_to_snapshot | supports temporal questions without making a graph engine authoritative |
| publication_requests | publication_id, artifact/content digest, path, base_snapshot_id, policy version, requester/reviewer, state/version | approval cannot outlive or change its exact tuple |
| publications | publication_id, resulting full commit/tree OID, attempt, receipt, published_at | immutable evidence; customer Git owns file contents |

The current projection and immutable transition event are updated together. The
event log is for audit/rebuild; arbitrary event replay is not the write API.
Memory and publication tables may share a physical database but are owned by
their ports, with no cross-module writes or ad hoc joins.

### Single-writer, claims, leases, and sequences

Single-writer means one **logical authority per repository aggregate**, not one
global process:

1. A command locks or compare-and-swaps the repository/target rows, checks
   expected versions and the state machine, and verifies the fence generation.
2. It allocates one repository sequence by incrementing repository_sequences in
   that transaction. Sequence ranges are allowed for externally numbered
   migrations; an abandoned range creates a visible gap.
3. It changes the projection, appends transition/audit/outbox rows, and stores the
   idempotency receipt in the same commit.
4. A uniqueness constraint enforces one active Program authority, task claim, and
   slot holder. Partial-unique constraints in production and equivalent guarded
   transactions locally provide the same result.

Leases use a three-minute TTL and one-minute renewal at the modeled load. Acquire
or reclaim increments a 64-bit generation. Every sensitive later command carries
the opaque FenceToken containing resource identity and generation. Expiration
alone does not make an old worker safe; generation comparison at the write
authority is the fence.

### Concrete failure behavior

| Failure | Required outcome |
|---|---|
| Two Program turns race | Both attempt to acquire program_authorities for the repository. One transaction increments and receives generation N; the other receives conflict/current holder. Only commands bearing N can allocate work. A retried winning command gets the same receipt. |
| A Program turn pauses beyond TTL | Another turn may reclaim and receive N+1. The old turn can preserve a late transcript as fenced narrative evidence, but every control mutation and publication request with N fails. |
| Worker dies holding task claim and slot | Renewals stop. After storage-authority time passes expires_at, reclamation atomically increments lease/claim/slot generations and starts a new attempt. The old run is marked abandoned by an idempotent reaper job. |
| Old worker resumes after reclamation | It may upload chunks to its immutable run namespace, tagged late, so evidence is not lost. It cannot transition the task, write an output snapshot as accepted, release the new claim, or publish because its generation is stale. |
| Network drops after commit | Client retries the same command ID and canonical bytes; the stored receipt is returned. A different request under that ID is rejected and audited. |
| Crash during artifact upload | Unfinalized chunks are invisible and garbage-collected after 24 hours. Finalize verifies count, size, and digest before committing the manifest. |
| Crash during schema migration | Transactional DDL rolls back. Non-transactional work is split into expand/backfill/verify/contract phases with a checksummed migration ledger and idempotent checkpoints; old code remains compatible until contract. |
| Clock jumps or clients have skew | Client timestamps are informational. PostgreSQL authority time decides production expiry. Local authority compares persisted wall time while running a wall/monotonic jump detector; a jump over 30 seconds pauses new grants and revalidates leases. Forward jumps may cause safe early expiry; backward jumps delay recovery, never authorize a stale generation. |

Dependency insertion locks the repository dependency namespace and checks the
small repository-local graph for a cycle before commit. At 40 tasks/day, a
serialized cycle check is cheaper and safer than eventually repairing a corrupt
DAG.

## Storage comparison against the capacity model

| Candidate | Verdict | Arithmetic-based reason |
|---|---|---|
| PostgreSQL | **Take it now for production.** | Six peak requests/second and about 8,000 rows/day at day one are trivial. Even 600 peak requests/second and 24 million new rows/month at 100-times fit a well-indexed transactional system with retention/partitioning. It gives atomic projection/event/audit/idempotency writes, row locks, constraints, row-level security, full-text search, and one operational surface. |
| SQLite | **Take it now for local/offline only.** | One local repository has about 0.1 writes/second average, 6 total operations/second peak, and under 1 GiB/month of relational/index data. WAL plus a coordinating writer is enough and costs nothing. Network filesystems, multi-host failover, shared multi-tenant isolation, and 500 concurrent workers make it the wrong production authority. |
| Object storage | **Take it now for production artifact and source-pack bodies.** | About 8 GiB/month of raw narrative, 3 GiB/month of source delta, 100 MiB expected large objects, and a 2 GiB limit should not bloat transactions or backups. Cheap immutable bodies plus digested manifests fit. Local mode uses the same semantics over a filesystem CAS. |
| Git | **Take it now, only for source identity/pinning and approved customer documents. Never use it for live control or private narrative.** | At 400 transitions and 280 artifacts/day per active repository, control-in-Git would create hundreds of unrelated changes, races, merge conflicts, and secret exposure. Git is excellent for immutable source OIDs and reviewed customer history; it has no safe TTL, compare-and-swap claim, tenant deletion, or bounded-staleness query contract. |
| Redis | **Take it only at the named trigger: lease/job/cache traffic drives transactional CPU over 70% for 30 minutes and control p95 exceeds 250 ms after query/index tuning, or outbox depth stays over 5,000 for 10 minutes.** | Day one has only 7,200 lease renewals/day (0.083/second) and 8,600 total writes/day; another service adds more failure modes than capacity. At the trigger Redis may carry cache, wake-ups, or streams, but never authoritative tasks, fences, sequences, or the only job copy. |
| pgvector | **Take it now for production semantic retrieval.** | Selected memory creates about 4,600 vectors/day, roughly 414,000 at 90 days day one and 4.1 million at month-six load. Keeping vector metadata beside tenant/provenance filters avoids a new system. It remains derived; rebuild is expected. |
| Dedicated vector database | **Take it at the named trigger: more than 25 million live vectors and filtered-search p95 over 2 seconds for seven days after index/partition tuning, or a fixed relevance benchmark falls below 80% recall at the required latency.** | The 100-times 90-day set can reach 41 million chunks, so the trigger may eventually fire. It does not fire day one or month six. SearchPort prevents the change from touching scheduling. |
| Mem0 | **Never adopt it as a Nightshift system component or authority.** | The load is only thousands of selected chunks/day, already covered by manifests, text/vector search, and explicit retention. A second opaque extraction/memory lifecycle would duplicate authority, complicate provenance/redaction/deletion, and risk promoted summaries. It may be benchmarked as an offline retrieval algorithm, but no persisted Mem0 state enters NSC-v1. |
| Temporal knowledge graph | **Take a dedicated temporal graph engine only at the named trigger: at least 25% of sampled operator questions require three-or-more-hop time-qualified traversal, the relational narrative_links implementation scores under 80% on that query benchmark, and there are over 10 million live links.** | Day one creates around 2,000 durable non-index rows/day and far fewer decision/evidence links. valid-from/valid-to relational links answer expected provenance questions cheaply. A graph engine now would be a second authority-shaped store with no numerical justification. |

The verdicts distinguish a data model from an engine: temporal provenance links
exist at T0, but a temporal graph product does not. Likewise semantic vectors
exist, but their engine is replaceable and non-authoritative.

## Zero-file repository mode

Zero-file mode means the customer working repository receives **no .omni
directory, Nightshift database, registry, transcript, report, generated internal
document, workspace, alternate, lock, or ignore-file edit**. Nightshift also
avoids Git linked-worktree commands against the customer checkout because those
write metadata under its .git directory.

1. On registration, Nightshift records the repository identity externally and
   clones/fetches into a tenant-scoped bare mirror under the configured state
   root or managed source store. Reading a local source path is allowed; writing
   it is not.
2. Each run creates a disposable clone/workspace from that external mirror under
   state_root/tenants/{tenant}/repositories/{repository}/workspaces/{run}. It is
   never nested in the customer repository. The mirror pins every input and
   accepted output commit needed by retention policy.
3. All control data goes through ControlPort. All transcripts, reports, handoffs,
   evidence, and generated documents remain MemoryPort artifacts.
4. A generated ADR/PRD/release note is initially an artifact. Publication stages
   a patch in the external workspace. Only an exact-byte/path/base-SHA review
   grants PublicationPort a one-use credential to push a branch/commit or return
   an applyable patch according to customer policy.
5. Cleanup deletes unpinned disposable workspaces after seven days (so a recent
   crash remains inspectable) and retains pinned Git objects with their evidence.

No coordination, history, semantic retrieval, audit, or provenance capability is
lost. The lost conveniences are in-repository status files, offline portability
by merely cloning the customer repository, and direct inspection with ordinary
file tools. Local users must back up/export the external state root separately.
There is additional mirror/workspace disk cost and source synchronization must be
explicit.

An approved document is customer-owned, not Nightshift operational clutter, so
it may exist in normal zero-Nightshift-file mode. A stricter **no-generated-file
mode** disables PublicationPort writes and permits only reviewed patch export.

## Provenance

Repository names and branch names are mutable, so provenance uses a stable
repository_id plus immutable Git object identities.

### Snapshot capture

- source_snapshots stores the Git object format (SHA-1 or SHA-256), full commit
  OID, full tree OID, observed ref/remote only as context, and capture time. A
  closure manifest also records materialized submodule commit OIDs and Git LFS
  object OIDs/digests; a tree containing only an LFS pointer is not represented
  as preserved LFS content.
- Before a run is admitted, the exact commit is fetched into the external bare
  mirror, required submodule/LFS content is resolved under customer policy, a
  private retention ref pins the commit, and a content-addressed pack/bundle plus
  closure-manifest digest is recorded. Production backups preserve pinned packs;
  local export can include them.
- A clean committed checkout is the default input. If a user explicitly permits
  a dirty checkout, Nightshift copies it to the external workspace, creates a
  synthetic snapshot commit there, records its parent and patch digest, marks it
  synthetic, and pins its bundle. The customer checkout remains untouched.
- A run records input_snapshot_id. Each material artifact records the input and,
  if it describes modified code, output/workspace snapshot ID. Decisions record
  the exact snapshot they observed, not merely the task's latest snapshot.

### Surviving rewritten history

A rebase or force-push changes refs but cannot change the pinned commit object,
tree OID, or retained bundle. A squash merge creates a new commit: Nightshift
records a derived_from relation among input snapshot, worker output snapshot,
reviewed patch digest, and resulting squash commit/tree. It never overwrites the
original provenance with the convenient new branch head. Equal tree or patch
digests may prove content equivalence, but they do not claim commit identity.

Each artifact carries a provenance envelope:

    tenant_id, repository_id, artifact_id, content_digest,
    producer_run_id, producer_identity, input_snapshot_id,
    optional_output_snapshot_id, control_repository_sequence,
    created_at, redaction_policy_version, tool/model metadata

The envelope is included in export and audit. If source retention expires before
narrative retention, the record still names the exact OID but must visibly say
source_not_retained; the system must not imply it can reconstruct bytes it
deleted.

## Semantic memory, fenced

Semantic ingestion runs only after artifact finalization and secret redaction.
By default it indexes reports, decisions, handoffs, evidence captions, and
transcript summaries—about 10% of narrative bytes—not raw tool dumps. Every
chunk carries tenant/repository, artifact, snapshot, validity interval, retention
class, redaction policy, chunker version, and embedding model version. Results
return citations and freshness; uncited generated summaries are not stored as
facts.

The enforcing mechanism is **capability separation plus write-authority
validation**:

1. The scheduler/lease principal receives ControlSchedulerPort only. It has no
   MemoryPort search capability or database permission for memory schemas.
2. Planner and narrator principals may search memory, but receive
   advisory_context values, not control entities or FenceTokens.
3. ControlPort accepts a closed set of typed commands and recomputes all
   scheduling predicates from current control rows inside the transaction.
   Retrieved text cannot satisfy a dependency, assign a slot, choose a priority,
   renew a lease, allocate a sequence, or approve publication.
4. Production database grants and service scopes enforce the split. Local mode
   injects separate capability objects and the contract test verifies that a
   memory principal cannot obtain a control mutation handle.
5. A human/Program may turn a retrieved narrative into a proposed task or
   explicit decision citation. A separately authorized ControlPort command, with
   expected versions and a current fence, is the only point where it can affect
   state.

Thus deleting and rebuilding every vector changes search quality, never the task
graph or next worker assignment.

## Tenancy and safety

### Isolation and authorization

- Every database primary/foreign/unique/index key begins with or includes
  tenant_id as appropriate. Production row-level security is forced even for
  application owners; a request transaction sets one authenticated tenant
  context and cannot disable policy.
- Repository and artifact authorization is checked at each object. Background
  jobs carry signed/scoped tenant context and cannot scan an unbounded all-tenant
  table.
- Object prefixes aid operations but are not the security boundary. Tenant-
  scoped credentials/policies and manifest authorization gate object access.
  Source mirrors, workspaces, exports, and logs follow the same boundary.
- Local mode defaults to one tenant per state root. Sharing a state root requires
  the same identity checks as production; separate roots are the safer default.

### Encryption and key custody

- Transport is encrypted. Managed disks/databases/backups use provider volume
  encryption. Artifact bodies and export bundles also use application envelope
  encryption with a unique data key per tenant and versioned object keys.
- Default production custody is a service-controlled hardware-backed master key
  wrapping tenant data keys. Enterprise policy may supply a customer-controlled
  wrapping key or require a dedicated cell. Local custody is an OS keystore or
  passphrase-held wrapping key; losing it is explicitly unrecoverable.
- Sensitive relational narrative text and credentials are application-encrypted.
  Control indexes retain only fields required to coordinate. Embeddings and
  operational control metadata live on encrypted volumes but cannot be
  individually envelope-encrypted while remaining searchable; deletion from
  shared backups therefore completes on backup expiry, not instantly. This limit
  is disclosed rather than mislabeled cryptographic erasure.
- Key rotation rewraps tenant data keys first; object re-encryption is resumable
  and versioned. No object key or secret enters logs.

### Redaction and ingestion

Secrets, customer-configured patterns, credentials, and high-risk personal data
are redacted before durable transcript finalization and before indexing. The
manifest records redaction policy/version, scanner result, and irreversible
redaction count, never the secret. A customer may opt into a separately
authorized encrypted raw vault with shorter retention; raw-vault content is
never indexed. Failed scans quarantine an artifact and block search/publication
without blocking control-state recording.

### Retention, export, deletion, and removal

- Retention is per tenant and artifact class. T0 defaults are: unpinned
  workspaces seven days, raw transcripts/unfinalized uploads 90 days/24 hours,
  reports/handoffs/evidence 365 days, and control/audit/decisions/publication
  receipts for tenant lifetime. Legal holds are explicit, visible, scoped, and
  audited.
- Export takes a consistent control sequence and produces paginated NDJSON/CSV
  control/audit data, original artifact files, manifests and checksums,
  publication receipts, key metadata, and optional pinned Git bundles. It
  reports items created after the snapshot rather than silently mixing them.
- Delete artifact first tombstones access, revokes links, removes search chunks,
  then deletes body/chunks and records an idempotent deletion receipt. Search
  reindex cannot resurrect a tombstoned manifest.
- Delete tenant changes tenant status to deleting, revokes sessions and
  publication credentials, fences all leases by advancing generations, waits
  for in-flight commands to stop, exports if requested, removes live
  rows/indexes/object prefixes/mirrors/workspaces, and destroys wrapped tenant
  keys. A canary list verifies every storage adapter reports zero live objects.
  Encrypted shared backups age out within a declared maximum of 35 days.
- A minimal non-content deletion receipt (tenant pseudonym, request/finish time,
  policy and outcome) may remain under the operator audit key when law/contract
  requires it. Otherwise it is deleted too. This exception is disclosed before
  deletion.
- Remove Nightshift locally stops the coordinator, optionally exports, deletes
  the external state root and credentials, and removes no customer-repository
  file because none was created. In managed mode it invokes tenant deletion and
  revokes integration credentials. Approved customer-owned Git documents are not
  removed by uninstall; the customer owns them and may revert them normally.

Audit events are append-only to service roles, sequenced, access-controlled, and
periodically exported with hashes/checksums. They record authorization and
metadata, not transcript bodies. Break-glass access, exports, retention changes,
key changes, publication, and deletion are themselves audited.

## Migration from today's in-repository model

Migration is per repository and uses an external bootstrap registry containing
active_backend and authority_epoch. There is never an intentional dual authority.
The old .omni store remains authoritative until one quiesced atomic switch.

| Phase | Action | Authority and reversibility | Crash/rollback |
|---|---|---|---|
| 0. Inventory | Read .omni databases/files, generated docs, worktrees, Git status, artifact sizes/digests, task counts, and current commit. Classify each item as control, memory, approved customer doc, unknown, or disposable. Produce a dry-run report outside the repo. | Old model unchanged and authoritative. Fully reversible. Unknown files are never guessed or deleted. | Rerun from scratch; no mutation. |
| 1. Snapshot | Quiesce only long enough to take a consistent read snapshot and record old high-water sequence/checksum; copy it externally. Resume old operation. | Old model remains authority. Snapshot is rollback evidence. | Partial copy is untrusted and discarded by digest. |
| 2. Shadow import | Idempotently import control projections/events into a new repository namespace, copy/redact/finalize memory artifacts, pin source snapshots, and classify generated docs. Imported rows carry migration_id and old source key. | Old remains authority; new namespace denies live commands. Reversible by dropping/tombstoning the imported namespace. | Migration ledger resumes by stable old key plus digest; duplicate IDs return the same receipt. |
| 3. Compare | Replay changes since the snapshot into shadow, compare task graph/state, claims, sequences, run/artifact counts and sampled bytes; run NSC-v1 queries against both. Resolve every mismatch. | Old remains authority. Fully reversible. | A crash resumes at old high-water mark. No cutover while mismatches remain. |
| 4. Quiesced cutover | Acquire a migration lease, stop new Program turns, drain or expire workers, take/import final delta, verify no active old lease, increment authority_epoch, then atomically change the external bootstrap binding to the new adapter. New clients require that epoch. | New store becomes sole authority at the binding flip. No dual write. | Before flip, old resumes. After flip, clients read only new. A crash reads bootstrap to learn which side won. Old processes must be stopped/revoked because they do not understand the epoch. |
| 5. Reversible soak | Operate externally while retaining the read-only old snapshot. Append a canonical migration/export journal of every post-cutover command. Do not remove .omni yet. | New is authority. Rollback is possible but requires another quiesce. | To roll back, pause, create a fresh legacy copy, replay the canonical post-cutover journal, validate high-water digests, increment epoch, and flip binding. Never reopen the stale old files directly. |
| 6. Repository cleanup | After acceptance, present a reviewed cleanup patch. Remove Nightshift operational files and linked-worktree metadata only with explicit customer approval. Approved customer docs remain; unapproved generated docs were imported to memory and are removed only in that patch. | External model remains authority. Backup/export makes state recoverable, but Git cleanup is an ordinary customer commit/revert. | Revert the cleanup commit to restore files, but do not make them authoritative without a controlled rollback. |
| 7. Retire legacy | After the agreed rollback window, securely delete old snapshots/credentials according to retention, leaving a migration receipt and export. | Reversal now requires restore/import from export. | Verify export and restore drill before deletion. |

A schema migration within either adapter uses expand/backfill/verify/contract.
Readers tolerate both representations during expand/backfill. Backfill is bounded,
idempotent, and checksummed. Contract is a separate release after restore has
been tested; a migration lock plus generation ensures one runner, and allocated
migration sequence gaps are acceptable after a crash.

## Hard parts: most likely to be wrong

1. **Local and production concurrency may not actually be equivalent.** SQLite's
   writer lock and PostgreSQL's row concurrency can expose different race wins,
   lease timing, and uniqueness behavior. Prove this early with the same
   randomized two-Program/ten-worker trace, kill at every transaction boundary,
   and compare normalized receipts for 10,000 runs. Any split-brain or duplicate
   active claim disproves the design.
2. **Exact provenance may cost or leak more than estimated.** Private pinned
   mirrors and synthetic dirty snapshots may be huge or retain deleted secrets.
   Trial 20 representative repositories, including monorepos and Git LFS, measure
   packed growth/workspace startup, then force-push and squash; reconstruct every
   sampled source tree from export alone. Over 20 GiB packed, 1 GiB/day growth,
   failed reconstruction, or unauthorized object reachability disproves the base
   tier.
3. **Deletion/redaction may be incomplete across indexes, backups, exports, and
   logs.** Seed tenant-specific canary secrets into every artifact path, run
   delete-tenant, and scan live stores, caches, mirrors, metrics labels, exports,
   and restored backups. Any live canary after the deletion SLA—or any search
   result after tombstone—blocks production. Shared-backup limits must match the
   customer contract.

## One-way doors and reversal triggers

| Expensive decision | Why reversal is expensive | Reversal trigger |
|---|---|---|
| NSC-v1 command/receipt semantics and three authority planes | Every client, adapter, audit trail, and migration depends on them | A contract proof shows a required operation cannot be made atomic/fenced, or more than 5% of real commands require an escape-hatch transaction across planes |
| External state/source root and zero-file default | Installation, backup, support tooling, and migration all assume the customer checkout is not an authority | More than 20% of measured local users reject a separately backed-up state root and an encrypted in-repository export can meet the same cleanliness/security requirements; add an explicit non-default adapter rather than weakening zero-file mode |
| Tenant/repository-scoped UUIDv7 identity | IDs appear in artifacts, exports, logs, and customer references | A merger/federation requirement cannot represent external identities without collision or an explicit alias layer |
| Immutable full Git OID plus retained source snapshot | Provenance and storage retention depend on it | A supported non-Git source system or customer policy forbids retained source objects; add a versioned SourceSnapshot adapter rather than reinterpret old IDs |
| PostgreSQL production authority and SQLite local authority | Schema, migrations, operations, and recovery become adapter-specific | NSC conformance fails irreducibly, or the measurable control-store ladder trigger fires after tuning |
| Content-addressed immutable artifacts with 2 GiB cap | Digests/object layouts are embedded in manifests and exports | More than 1% of legitimate artifacts require splitting or the digest algorithm is deprecated; introduce a new manifest/digest version |
| Shared-schema production tenancy with row-level security | Moving tenants changes keys, backups, and operational topology | A contract/regulation requires physical isolation/BYOK, a tenant exceeds 25% of DB/storage load for 30 days, or an isolation test finds one cross-tenant read |
| Per-tenant envelope-key hierarchy | Re-encryption, exports, and deletion rely on it | Required customer-held keys cannot be integrated, the algorithm is deprecated, or rotation cannot meet a 24-hour SLO |
| Default retention classes and 35-day backup expiry | They drive capacity, explainability, deletion promises, and contracts | Legal/customer policy requires a shorter deletion or longer evidence window; price and isolate the exception before accepting data |
| Git commit as published-document authority | Publication audit and customer workflow rely on it | A customer uses another authoritative document/VCS system; add a PublicationPort adapter and preserve old receipts |
| Append-only audit plus mutable current projections | Rebuild, debugging, and compliance depend on event meaning | Audit storage exceeds 20% of the subsystem budget for three months or replay cannot reproduce sampled projections; archive/version events, do not rewrite history |

Changing embedding model, chunking, or vector engine is intentionally not a
one-way door: all are versioned derived indexes rebuilt from retained artifacts.

## Scale ladder

Every concern starts at T0. A tier move requires its trigger, a measured cost
delta, a rollback plan, and continued NSC-v1 conformance.

| Concern | T0 — cheapest correct choice | T1 / T2 | Measurable switch trigger | Now |
|---|---|---|---|---|
| Control and audit storage | SQLite local; one HA PostgreSQL production instance; indexed current tables and append-only events | T1 read replica plus time/tenant partitions; T2 tenant cells/shards | T1 when any hot table exceeds 100 million rows, primary CPU is over 70% for 30 minutes, or current-read p95 exceeds 250 ms for seven days after tuning. T2 at 2 TiB hot data, 3,000 sustained writes/second, or a residency/isolation contract. | T0 |
| Background indexing/deletion/export | Transactional outbox/job table, at-least-once handlers, polling with fenced claims | T1 Redis streams or managed durable queue for wake-up/delivery while DB remains job authority; T2 partitioned broker | Oldest runnable job over 30 seconds or depth over 5,000 for 10 minutes while workers have capacity, and job polling/claims consume over 20% DB CPU. | T0 |
| Ephemeral cache/lease traffic | No shared cache; small process cache only for immutable policy | T1 single Redis; T2 clustered cache | Cacheable bounded-stale reads exceed 80% of reads and cause DB CPU over 70% for 30 minutes, or lease renewals alone push control p95 above 250 ms after batching/tuning. | T0 |
| Artifact/source bodies | Filesystem CAS local; encrypted object storage production; 90-day transcript lifecycle | T1 cold tier and separate regulated-tenant buckets; T2 regional replication | Cold tier at over 5 TiB retained or body storage/backup over $500/month. Regional replication only when measured restore exceeds one-hour RTO or a residency contract requires it. | T0 |
| Semantic retrieval | SQLite FTS plus bounded rerank local; PostgreSQL FTS + pgvector production | T1 dedicated vector service; T2 tenant/region-sharded vector service | T1 over 25 million live vectors plus p95 over 2 seconds for seven days after tuning, or benchmark recall under 80%. T2 at 500 million live vectors or residency requirement. | T0 |
| Temporal relationships | Versioned relational narrative_links | T1 dedicated temporal graph read model; T2 sharded graph | T1 only when over 10 million links, at least 25% of sampled questions need 3+ time-qualified hops, and benchmark accuracy is under 80%. | T0 |
| Source mirrors/workspaces | Tenant-scoped bare mirrors, pinned refs/packs, disposable external clones | T1 shared deduplicated source service and sparse hydration; T2 regional source cells | T1 when workspace startup p95 exceeds 2 minutes for seven days, hot workspace disk exceeds 2 TiB/70% capacity, or mirror transfer exceeds 20% subsystem cost. T2 on residency or over 25 TiB. | T0 |
| API compute | One stateless production process with restart; one local coordinator | T1 two-plus autoscaled processes; T2 regional cells | T1 when CPU exceeds 60% for 15 minutes, memory exceeds 75%, or API p95 exceeds 1 second from request queueing. T2 when one region cannot meet residency/RTO or sustained load exceeds 3,000 requests/second. | T0 |
| Tenancy boundary | Shared schema with forced row-level security; per-tenant keys; separate local roots | T1 dedicated database/bucket for named tenants; T2 full tenant cells | T1 on contractual physical-isolation/BYOK requirement, or one tenant exceeds 25% of DB/storage load for 30 days. T2 when dedicated tenants exceed 20 and shared operations breach SLO twice/quarter. | T0 |
| Realtime status | Current reads plus five-second dashboard polling | T1 server-sent events from outbox; T2 pub/sub fanout | T1 when over 50 concurrent dashboards and polling is over 20% of reads for seven days, or observed status lag exceeds five seconds. T2 over 5,000 concurrent streams. | T0 |
| Observability | Structured existing logs, command/audit correlation, core metrics and inspection export | T1 managed dashboards/traces; T2 full cross-cell APM | T1 after two incidents in a quarter with detection over 15 minutes or diagnosis over one hour, or log volume exceeds 100 GiB/month and needs sampling/tiering. | T0 |

The success case does not automatically select every T1. For example, 600 burst
requests/second may still fit T0 control storage, while 41 million 90-day vectors
is likely to fire the semantic trigger first.

## Contradictions and chosen readings

1. **Preserve everything versus retention, redaction, deletion, and removing
   Nightshift.** Indefinite byte-for-byte preservation cannot coexist with secret
   redaction or a tenant deletion request. The finishable reading is: preserve
   acknowledged, policy-allowed material for its declared retention window and
   until an authorized deletion; deletion and legal policy win. Manifests state
   when source bytes are no longer retained.
2. **A literally zero-Nightshift-file repository versus publishing approved
   generated documents into it.** The finishable reading is that a reviewed ADR,
   PRD, or release note becomes customer-owned and therefore is not Nightshift
   operational clutter. Customers who mean no generated files at all use the
   stricter no-generated-file mode and receive patches only.
3. **Exact SHA provenance versus rebases, force-pushes, squash merges, and
   customer removal.** A SHA label alone is not durable if its object disappears.
   The finishable reading requires Nightshift to retain a tenant-scoped pin or
   pack/bundle for the retention window, and to record derivation rather than
   pretending a squash commit is the original. Deletion may later make the bytes
   unavailable, which must be reported honestly.
4. **One local/production behavioral contract versus different storage
   guarantees.** Identical query plans or search ranking are impossible and not
   useful. The finishable reading makes command outcomes, fencing, idempotency,
   authority, isolation, provenance, freshness declarations, export, and deletion
   identical while allowing adapter-specific performance and advisory ranking.
5. **Work entirely locally versus proposing a managed multi-tenant deployment.**
   The scope forbids deploying or calling external services during this work; it
   does not make the production architecture local-only. This proposal names
   components and costs but creates no service, credential, or deployment.

These readings preserve the brief's safety outcomes without turning temporary
operational material into customer source or promising logically incompatible
forms of permanence.
