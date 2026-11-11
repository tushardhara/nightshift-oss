# Capacity model

This model is the gate for the technology choices in `PROPOSAL-codex.md`. The
brief supplies concurrency (about five workers on one repository for days), but
not throughput or retention. The assumptions below are deliberately explicit so
measurements can replace them.

`If this succeeds` means 100 times the day-one active workload, not 100 times
every stored byte forever. An *active-repository equivalent* is one repository
running five workers continuously for 24 hours. Registered but idle repositories
add metadata, not meaningful load.

| | Day one | Month six | If this succeeds (100×) |
|---|---:|---:|---:|
| unit of work (and its size) | 1 completed task: about 10 control transitions, 1.5 worker runs, 7 logical artifacts, and about 7 MiB narrative bytes | same | same; no assumed growth in task size |
| tenants / repositories | 1 tenant / 1 registered and active repository | 10 tenants / 25 registered repositories / 10 active-repository equivalents | 100 tenants / 250 registered repositories / 100 active-repository equivalents |
| tasks completed / day | 40 | 400 | 4,000 |
| control-state transitions / day | 400 | 4,000 | 40,000 |
| artifact manifests / day | 280 | 2,800 | 28,000 |
| request operations / day | about 51,600 (8,600 writes, 43,000 reads) | about 516,000 | about 5.16 million |
| peak requests/sec (about 10× all-day average) | 6 | 60 | 600 |
| concurrent worker sessions | 5 | 50 | 500 |
| new stored rows / day | about 8,000 | about 80,000 | about 800,000 |
| retained state/memory growth / month | about 10 GiB | about 100 GiB | about 1 TiB |
| source-mirror delta / month | about 3 GiB, plus a 2 GiB assumed initial mirror | about 30 GiB, plus 50 GiB initial mirrors across 25 repositories | about 300 GiB, plus 500 GiB initial mirrors across 250 repositories |
| ephemeral workspace high-water mark | about 10 GiB (5 workspaces × 2 GiB repository) | about 100 GiB | about 1 TiB |
| largest artifact | 100 MiB expected long transcript bundle; 2 GiB hard accepted-object limit | same | same; large content is chunked rather than raising the limit |

- **Read:write ratio:** approximately 5:1 by request. The 8,600 day-one
  mutations are dominated by 7,200 in-place lease renewals; durable inserts are
  only about 8,000/day, including idempotency/outbox and retrieval-index chunks.
  Operator dashboards may make the ratio higher, which is favorable.
- **Reads that tolerate staleness:** semantic retrieval and narrative search may
  lag finalization by 60 seconds; dashboard lists and aggregate progress may lag
  by 5 seconds; cost reports may lag by 15 minutes. Claim, lease, slot, task
  transition, dependency, sequence-allocation, publish-authorization, and
  read-after-own-write checks must use the current authority and may not use a
  stale replica or semantic index.
- **Latency the user actually notices:** claim/renew/transition p95 under 250 ms
  (a worker notices 1 second); task and run views p95 under 1 second; semantic
  retrieval p95 under 2 seconds; a finalized artifact should become fetchable
  within 2 seconds and searchable within 60 seconds. Bulk export and customer
  document publication are human-reviewed jobs measured in seconds or minutes,
  not interactive milliseconds.
- **Durability and consistency:** an acknowledged task mutation, sequence number,
  claim generation, artifact manifest, decision, or audit event has RPO 0 and is
  committed atomically with its idempotency record. Concurrency-critical changes
  are serializable per repository. Production target RTO is one hour. A lease
  renewal is intentionally softer: losing the most recent renewal may expire a
  worker within the three-minute TTL, but a fencing generation prevents its late
  writes from corrupting state. An unacknowledged transcript upload may lose at
  most its last 8 MiB chunk and is retried. A finalized transcript or evidence
  object has RPO 0 after acknowledgement. Approved customer-owned documents are
  durable and authoritative at the recorded customer Git commit; a Nightshift
  copy is only evidence.

## The arithmetic

### Work rate

Assume each continuously active worker completes eight substantive tasks per
worker-day (a three-hour mean including retries and review):

```text
5 workers × 8 tasks/worker-day = 40 completed tasks/repository-day
40 tasks × 10 task-state transitions = 400 transitions/day
40 tasks × 1.5 worker runs/task = 60 runs/day
40 tasks × 7 logical artifacts/task = 280 artifact manifests/day
```

The seven artifacts are an average, not a required bundle: roughly one transcript,
one report or handoff, four evidence items, and one decision/run-log/other item.
Thus day one is hundreds of domain events, not millions.

### Requests and peak rate

A three-minute lease TTL renewed once per minute gives:

```text
5 active workers × 1,440 renewals/worker-day = 7,200 renewal writes/day
about 1,040 meaningful control/artifact finalization writes/day
about 360 resumable upload or retrieval-ingest writes/day
= about 8,600 writes/day

8,600 writes × 5 reads/write = about 43,000 reads/day
total = about 51,600 request operations/day
51,600 / 86,400 seconds = 0.60 operations/second average
10 × average = 6 operations/second modeled peak
```

At 100× this is about 10 write operations/second average, 60 total operations/
second average, and 600 operations/second at the deliberately conservative burst.
That is ordinary transactional load. Even the success case is hundreds of peak
requests/second, not internet-scale millions.

### Rows

One active repository-day creates approximately:

```text
40 task rows + 80 dependency edges + 400 transition events
+ 60 run rows + 120 claim/lease lifecycle rows
+ 280 artifact manifests + about 1,040 durable audit/provenance events
= about 2,020 control, metadata, and audit rows/day

27 MiB selected searchable narrative / 6 KiB effective chunk
= about 4,600 retrieval-index rows/day

subtotal = about 6,600 domain/audit and retrieval-index rows/day
```

Lease renewals update the current lease row and go to bounded operational logs;
only acquire, fence, expire, release, and anomalous renewal events enter durable
audit history. Otherwise routine liveness would dominate permanent business data.

Add about 1,040 durable idempotency receipts for the meaningful mutations and 280
outbox rows for artifact/index work:

```text
2,020 domain/audit + 1,040 idempotency + 280 outbox
= about 3,340 non-index rows/day
3,340 non-index + 4,600 retrieval-index
= about 7,940, rounded to 8,000 new rows/day
```

Routine lease-renew idempotency is held in the five current lease rows rather
than creating a permanent receipt every minute; it expires with the lease retry
window. At 100×, 800,000 new rows/day is about 24 million/month and 292 million/
year before retention or archive. That is a future partitioning signal, not a
day-one requirement.

### Bytes

Raw transcript is the dominant byte source. Assume 50 MiB per worker-day,
including prompts, responses, tool I/O, and timestamps:

```text
5 workers × 50 MiB = 250 MiB transcript/day
240 non-transcript artifacts × 100 KiB average = 23.4 MiB/day
(250 + 23.4) MiB × 30 = about 8.0 GiB raw narrative/month
```

Only redacted reports, decisions, handoffs, evidence captions, and transcript
summaries are indexed by default. Assuming that selected text is 10% of narrative
bytes:

```text
27 MiB selected text/day / 6 KiB effective chunk = about 4,600 chunks/day
4,600 × 4 KiB text/vector/index allowance × 30 = about 0.53 GiB/month
3,340 durable non-index rows × 2 KiB indexed allowance × 30 = about 0.19 GiB/month
8.0 + 0.53 + 0.19 = 8.72 GiB/month
round to 10 GiB/month for manifests, fragmentation, and estimation error
```

Month six uses ten active-repository equivalents, so it grows by about 100
GiB/month. The 100× case grows by about 1 TiB/month. With a default 90-day raw
transcript retention, raw narrative approaches a roughly 2.4 TiB steady set at
100×; retained decisions, reports, audit data, and approved evidence are smaller
but longer-lived. Tenant policy may shorten or extend these periods, so usage and
cost must be metered per tenant.

The expected largest single artifact is a 100 MiB unusually long transcript
bundle. The interface accepts resumable 8 MiB chunks and caps a finalized object
at 2 GiB; larger build output must be split into a manifest plus objects. No
transactional record contains artifact bodies.

Zero-file mode also needs an external source mirror and external workspaces.
Repository size is customer-dependent and can dominate these estimates, so it is
metered separately. The planning assumption is a 2 GiB initial packed mirror per
registered repository, 100 MiB/day of new reachable Git objects per active
repository, and five 2 GiB workspace checkouts per active repository:

```text
1 active repository × 100 MiB/day × 30 = about 3 GiB mirror delta/month
250 registered repositories × 2 GiB = about 500 GiB one-time mirror baseline at 100×
100 active repositories × 100 MiB/day × 30 = about 300 GiB mirror delta/month at 100×
100 active repositories × 5 workspaces × 2 GiB = about 1 TiB ephemeral high-water mark
```

Onboarding must measure packed repository size before acceptance. A repository
over 20 GiB packed or growth over 1 GiB/day is outside the base estimate and gets
a tenant-specific capacity plan. Source objects pinned for provenance follow the
associated audit/evidence retention; unpinned workspace data is removed after a
run.

## Budget

- **Infrastructure ceiling (assumed because the brief gives none):** for the
  state, memory, retrieval, and API plane only, at most **$300/month at day one**
  and **$3,000/month at 100×**. Source mirrors and workspace disk are included;
  worker/model inference, the customer's authoritative repository hosting, and
  customer network egress are outside this budget and must be reported
  separately. Local/offline mode has a $0
  service bill but consumes about 13 GiB of retained customer-controlled disk per
  active month plus roughly 10 GiB of ephemeral workspace at the assumptions
  above.
- **Estimated monthly cost at day-one volume:** **about $150/month** for a small
  highly available control service, one application process, encrypted artifact
  and source-mirror storage, workspace disk and backup, key operations, and basic
  logs/metrics. A reasonable range is $100–$250; retained storage capacity itself
  is well under $10/month.
- **Estimated monthly cost at 100×:** **about $2,000/month**, with a reasonable
  range of $1,200–$2,800: $600–$1,200 for primary/standby transactional and
  retrieval capacity, $250–$500 for application processes, $100–$250 for the
  retained multi-terabyte artifact/mirror set, workspace disk and backups, and
  $250–$600 for logs, keys, network, and operational margin. Crossing $3,000/month
  or $3 per active repository-day without an observed scale trigger requires a
  design review.
