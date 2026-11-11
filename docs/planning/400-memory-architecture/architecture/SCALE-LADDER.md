# Scale ladder

Every concern is currently **T0**. “Now at” says **T0 in every row** because the
architecture starts with the cheapest correct component and moves only after a
measured trigger fires. Tier moves also require a measured cost delta, rollback
plan, migration verification and continued NSC-v1 conformance.

| Concern | Tier 0 | Tier 1 | Tier 2 | Switch when | Now at |
|---|---|---|---|---|---|
| Control and audit authority | SQLite through one coordinator locally; one HA PostgreSQL primary/standby in production; indexed current rows plus append-only events | Add read replica and time/tenant partitions | Tenant cells or shards | T1 when any hot table exceeds **100 million rows**, primary CPU exceeds **70% for 30 minutes**, or current-read p95 exceeds **250 ms for 7 days** after tuning; T2 at **2 TiB hot data** or **3,000 sustained writes/second** | T0 |
| Background indexing, deletion and export | Transactional outbox/job table with fenced polling and idempotent handlers | Managed durable queue or Redis Streams for wake-up/delivery; database remains job authority | Partitioned broker | T1 when oldest runnable job exceeds **30 seconds** or depth exceeds **5,000 jobs for 10 minutes**, while polling/claims consume over **20% DB CPU for 30 minutes**; T2 at **100,000 queued jobs for 60 minutes** or **10,000 deliveries/second** | T0 |
| Cache and lease traffic | No shared cache; process cache only for immutable policy | One non-authoritative Redis cache | Clustered cache | T1 when cacheable bounded-stale reads exceed **80% of reads for 7 days** and DB CPU exceeds **70% for 30 minutes**, or lease renewals push control p95 above **250 ms for 7 days** after batching; T2 at **50,000 cache operations/second for 60 minutes** | T0 |
| Artifact, source-pack and export bodies | Filesystem CAS locally; encrypted object storage with lifecycle policy in production | Cold tier and separate regulated-tenant buckets | Regional replication | T1 above **5 TiB retained** or **$500/month** body storage and backup; T2 when tested restore exceeds **1 hour in 3 consecutive drills** or **1 signed residency contract** requires another region | T0 |
| Semantic retrieval | SQLite text search plus bounded exact rerank locally; per-tenant-partitioned PostgreSQL FTS and pgvector in production | Dedicated vector service | Tenant/region-sharded vector service | T1 above **25 million live vectors** and filtered p95 above **2 seconds for 7 days** after index/partition tuning, or benchmark recall below **80% at 2 seconds**; T2 above **500 million live vectors** or **1 signed residency contract** requiring isolated regional indexes | T0 |
| Temporal relationships | Versioned relational `narrative_links` | Dedicated temporal graph read model | Sharded graph read model | T1 above **10 million live links**, with **25% of 200 sampled questions** needing at least **3 time-qualified hops** and benchmark accuracy below **80%**; T2 above **500 million live links** or **2,000 graph queries/second for 60 minutes** | T0 |
| Source mirrors and workspaces | Tenant-scoped bare mirrors, closure packs and disposable external clones | Shared source service with per-tenant deduplication and sparse hydration | Regional source cells | T1 when workspace startup p95 exceeds **2 minutes for 7 days**, hot workspace disk exceeds **2 TiB at 70% capacity for 24 hours**, or mirror transfer exceeds **20% of monthly subsystem cost**; T2 above **25 TiB hot source data** or **1 signed residency contract** | T0 |
| API and coordinator compute | One stateless production process; one local coordinator | Two or more autoscaled processes | Regional cells | T1 when CPU exceeds **60% for 15 minutes**, memory exceeds **75% for 15 minutes**, or request queueing makes API p95 exceed **1 second for 7 days**; T2 above **3,000 sustained requests/second for 60 minutes** or **1 signed regional-RTO contract** | T0 |
| Tenancy boundary and keys | Shared production schema with forced RLS, per-tenant envelope keys and separate local roots | Dedicated database/bucket/key boundary for named tenants | Full tenant cells | T1 when **1 signed contract** requires physical isolation/BYOK or one tenant exceeds **25% of DB or storage load for 30 days**; T2 when dedicated tenants exceed **20 tenants** and shared operations breach SLO in **2 quarters** | T0 |
| Repository publication | One Publication Gateway module, one scoped credential and reviewed serial operations | Separately scaled gateway workers sharing the approval ledger | Regional egress cells | T1 when execution-only publish queue p95 exceeds **10 minutes for 7 days** or throughput exceeds **100 publications/minute for 60 minutes**; T2 above **5,000 publications/minute for 60 minutes** or **1 signed regional-egress contract** | T0 |
| Realtime status | Current reads plus **5-second** dashboard polling | Server-sent events from outbox | Pub/sub fanout | T1 above **50 concurrent dashboards for 7 days** with polling over **20% of reads**, or p95 status lag above **5 seconds for 7 days**; T2 above **5,000 concurrent streams for 60 minutes** | T0 |
| Observability | Structured logs, command/audit correlation, core metrics and inspection export | Managed dashboards and traces | Cross-cell APM and long-term telemetry pipeline | T1 after **2 incidents in 90 days** with detection over **15 minutes** or diagnosis over **1 hour**, or above **100 GiB logs/month**; T2 above **10 regions** or **5 TiB telemetry/month** | T0 |

The vector trigger is deliberately a vector: partition by tenant first, then
move only when corpus size and user-visible latency are both bad after tuning,
or when measured recall falls below its floor. A 300 ms retrieval result is not
a tier trigger because the noticed-latency budget is 2 seconds.

## Current tier and cost

- Current tier: **T0 for every concern and every deployment role**.
- Managed day one: **about $150/month**, with a reasonable $100–$250 range and
  a hard planning ceiling of **$300/month**.
- Managed 100×: **about $2,000/month**, with a reasonable $1,200–$2,800 range and
  a hard planning ceiling of **$3,000/month**.
- Local/offline: **$0 service bill**, plus roughly 13 GiB retained and 10 GiB
  ephemeral customer-controlled disk at the day-one assumptions.
- A single-AZ, no-standby managed install has an honest floor of roughly
  **$50–$80/month day one** and **$700–$950/month at 100×**. That floor gives up
  the HA standby and assumes minimal or separately charged external mirror and
  workspace capacity; it is not the production reliability estimate.

Crossing $3,000/month or $3 per active repository-day without a row trigger
requires an architecture review rather than an automatic tier increase.

## Reversal triggers

Each selected component below adds something to run, patch, monitor, back up or
pay for. Its reversal trigger is the condition for removing or replacing that
surface, not a promotion trigger.

| Selected operational component | Reversal trigger |
|---|---|
| Local SQLite coordinator | Remove or replace if the multiprocess NSC race suite records **1 stale-fence acceptance in 10,000 runs**, or coordinator unavailability exceeds **60 minutes in 30 days** at measured local load. |
| HA PostgreSQL primary/standby | Reconsider HA topology if measured managed availability target is below **99.0% for 2 consecutive quarters** and contracts permit an RPO above **0 acknowledged mutations**, or replace PostgreSQL if a tuned cell exceeds **3,000 sustained writes/second for 60 minutes**. |
| Filesystem CAS and encrypted object storage | Replace an adapter if **1 finalized manifest in 1 million reads** resolves to missing or digest-invalid bytes, or if body storage/backup exceeds **$500/month for 3 months** with a cheaper NSC-conformant adapter. |
| PostgreSQL FTS and pgvector | Remove or replace if the semantic ladder trigger fires, or if maintaining the extension and indexes exceeds **20% of database CPU for 30 days** while semantic queries are below **100/day**. |
| Transactional outbox workers | Replace delivery mechanics if runnable age exceeds **30 seconds** or depth exceeds **5,000 jobs for 10 minutes** despite available handler capacity; retain a durable atomic outbox unless another adapter proves RPO **0 events**. |
| External source mirrors and workspaces | Replace the layout if workspace startup p95 exceeds **2 minutes for 7 days**, source storage exceeds **20% of subsystem cost for 3 months**, or **20% of 100 surveyed local users** reject separate backup and a clean alternative passes footprint/provenance tests. |
| Per-tenant envelope-key service | Replace key custody if rotation exceeds **24 hours in 3 consecutive drills**, **1 required customer-held-key contract** cannot be served, or the selected algorithm receives **1 applicable deprecation notice**. |
| One stateless production process | Replace the one-process topology when CPU exceeds **60% for 15 minutes** or API p95 exceeds **1 second for 7 days** because of request queueing. |
| Forced RLS shared-schema tenancy | Move a tenant out if it exceeds **25% of DB/storage load for 30 days**, **1 isolation test** produces a cross-tenant read, or **1 signed contract** requires physical isolation. |
| Publication Gateway and scoped Git credential | Replace the egress adapter when **1 signed customer requirement** names a non-Git authority, or execution-only publish p95 exceeds **10 minutes for 7 days**; disable all egress after **1 approval-tuple mismatch** until the contract failure is resolved. |
| Structured logs and core metrics | Replace with managed tracing if **2 incidents in 90 days** take over **1 hour** each to diagnose, or log volume exceeds **100 GiB/month**. |

## Switchability contract

Ports state the weakest guarantee callers may rely on. A T0 adapter may
over-deliver, but clients cannot make that stronger behavior part of the
contract. Each deployment profile wires exactly one adapter per role; candidates
not named as wired are unwired.

| Replaceable role / port | Operations | Weakest guarantee callers may depend on | Candidate adapters | Single adapter wired now |
|---|---|---|---|---|
| Control authority / `ControlPort` | Mutate/query; acquire/renew; allocate | Serializable per repository for critical mutations; RPO 0 after acknowledgement; expected-version and monotone-fence checks; idempotent receipts; monotone unique repository sequence with gaps allowed | SQLite coordinator; HA PostgreSQL; tenant-sharded PostgreSQL | Local: SQLite coordinator. Managed: HA PostgreSQL. |
| Artifact bodies / `ArtifactBodyStore` | Begin/put chunk/finalize/get/delete | Per-tenant plaintext content addressing; resumable 8 MiB chunks; atomic visibility only after verified manifest; at most 2 GiB per finalized object; no cross-tenant dedupe | Filesystem CAS; encrypted object storage; regional object store | Local: filesystem CAS. Managed: encrypted object storage. |
| Retrieval / `RetrievalIndex` | Index/delete/search/rebuild | Advisory, tenant-scoped citations with declared freshness; at-least-once indexing; ranking may differ; complete rebuild and lineage deletion supported | SQLite FTS plus exact rerank; PostgreSQL FTS plus pgvector; dedicated vector service | Local: SQLite FTS plus exact rerank. Managed: per-tenant PostgreSQL FTS plus pgvector. |
| Durable asynchronous work / `JobQueue` | Enqueue/claim/renew/complete/fail | Enqueue commits atomically with domain event; at-least-once delivery; no ordering guarantee; idempotent consumers dedupe by event ID plus handler version | Transactional outbox; outbox plus Redis Streams; outbox plus managed queue | Both profiles: transactional outbox. |
| Source snapshots and workspaces / `SourceWorkspace` | Snapshot/checkout/resolve/release | Exact Git OID and closure digest while retained; synthetic dirty snapshots supported; resolve either reconstructs verified closure or returns `source_not_retained`; workspace is disposable | Local bare mirror and filesystem clones; managed mirror packs plus workspace disk; dedicated sparse-hydration source service | Local: bare mirror and filesystem clones. Managed: mirror packs and tenant workspace disk. |
| Key wrapping / `KeyWrapper` | Create/wrap/unwrap/rotate/destroy | Versioned tenant boundary; authenticated wrapping; object-key material never logged; destroy receipt does not claim shared-backup erasure | OS keystore/passphrase; service KMS; customer-controlled KMS | Local: OS keystore or configured passphrase. Managed: service KMS. |
| Customer repository egress / `PublicationPort` | Stage/review/approve/publish/inspect | Refuse without one-use exact-byte/path/base/reviewer/expiry approval; publish at expected base or conflict; immutable receipt | Customer Git publisher; reviewed-patch publisher; future non-Git document publisher | Normal mode: customer Git publisher. No-generated-file mode: reviewed-patch publisher. Exactly one per repository. |
| Export destination / `ExportSink` | Begin/write/finalize/verify/delete | Consistent control-sequence snapshot, checksummed parts, explicit post-snapshot tail, idempotent finalization | Encrypted filesystem bundle; encrypted object-store bundle | Local: filesystem bundle. Managed: object-store bundle. |

## Free now, ruinous later

- [x] UUIDv7 sortable, non-sequential, shardable IDs
- [x] `tenant_id` on every row, even with one local tenant
- [x] Idempotency keys on every externally triggered mutation
- [x] UTC timestamps with timezone awareness
- [x] Cursor pagination and bounded list queries
- [x] Versioned public APIs, commands, receipts and event shapes
- [x] Object-level authorization from the start
- [x] No server-local state that prevents a second process
