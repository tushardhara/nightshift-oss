# Scale ladder

Design the INTERFACE for the target scale. Build the CHEAPEST CORRECT thing for
today's numbers. Move up only when an observable trigger fires.

A trigger is a number — "p95 > 300ms", "queue depth sustained > 5k", "table >
100M rows", "cost per 1k requests > $X" — never a feeling and never a date.
`staff` rejects a tier jump with no trigger fired, and the program reports the
current tier and cost every milestone.

**#400 acceptance condition 19** ("Numeric capacity and latency triggers govern
any move to Redis, a broker, dedicated vector storage, separated services, or
higher infrastructure tiers") and **condition 20** ("The design remains a
modular monolith at the initial measured load") are this table. Rows below are
Nightshift's own — the generic template's `cache`, `analytics` and `realtime`
concerns are deleted because nothing in this repository touches them: there is
no request-driven cache to invalidate, no scheduled-ETL/warehouse reporting
path, and no live/streaming surface (every #400 component is a CLI invocation
or a library call, never a long-lived connection). The numbers are first-cut
engineering thresholds derived from this repository's own schemas, SQLite's
documented single-writer behavior, and the real scan volumes already recorded
against #400 (nightshift 93 files, testbed A 856, testbed B 55,235 tracked / 77 s
/ 58 MB) — not a business SLA. `bin/nightshift_observability.py` (#400
condition 21) is what makes `volume`, `latency` and `publication_queue_time`
measurable against these triggers going forward; before it existed there was
nothing to compare a number to.

| Concern | Tier 0 — build this | Tier 1 | Tier 2 | Switch when | Now at |
|---|---|---|---|---|---|
| storage | SQLite per (tenant, repository), local filesystem, no server | one HA PostgreSQL authority, forced tenant RLS | + read replica, partitioning | any one store file > 8 GB, OR any single table > 5,000,000 rows, OR sustained `SQLITE_BUSY` retries > 1% of write attempts over a rolling hour | T0 |
| background work | none — every write path (`stage`/`review`/`approve`/`apply_publication`) commits synchronously inside its own transaction; no outbox exists (AC-21's `outbox_age` signal is REFUSED for exactly this reason, ADR-0087) | Postgres table + `SKIP LOCKED` | a broker (SQS/Kafka) | a caller needs a decision to survive a crash between "committed" and "delivered" (e.g. a landed publication triggering a downstream notification), AND projected redelivery volume exceeds ~100 events/minute sustained | T0 |
| search / advisory retrieval | SQLite FTS5, contentless (`artifact_fts`) | PostgreSQL full-text + pgvector, forced tenant RLS | managed cluster | a single project's `artifact` table exceeds 1,000,000 live rows, OR p95 keyword-search latency measured > 300 ms sustained, OR a real semantic/vector-similarity requirement is accepted (FTS5 has no vector search at any volume — this is a capability trigger, not only a load one) | T0 |
| files (large artifacts) | local content-addressed filesystem under `state_root` (`nightshift_provenance.stage_object`) | encrypted object storage (S3-compatible), presigned access | + CDN, multi-region | one tenant's `objects/` tree exceeds 10 GB, OR the combined CAS tree exceeds 50 GB, OR local disk free space drops below 20% | T0 |
| compute | one stateless `python3` process per CLI invocation, no daemon | one long-lived stateless application process | autoscaling group | AC-21's `publication_queue_time` p95 measured > 5 s sustained, OR per-invocation process-start overhead measured > 10% of total observed latency | T0 |
| observability | `bin/nightshift_observability.py`, invoked on demand, JSON to stdout (#400 condition 21) | scheduled collection + a managed dashboard | full APM | more than 1 on-call person needs concurrent read access to a signal without shelling into the box, OR any currently-REFUSED signal (`outbox_age`, `isolation_failures`, `restore_evidence`, `export_duration` before a first export) gains a real backing mechanism and needs trend history retained across more than 1 collection run (a single point-in-time snapshot can no longer answer it) | T0 |

No row above has fired. The design is a modular monolith at the initial
measured load (#400 condition 20): `bin/*.py` imports resolve entirely to the
Python 3.9+ standard library — no `redis`, `kafka`, `psycopg`/`psycopg2`,
`pgvector`, `celery`, or `kubernetes` import exists anywhere under `bin/`.

## Switchability contract
Every laddered concern sits behind a narrow interface that `platform` owns. Two
adapters may exist; exactly one is wired. Switching is a config change plus a
migration script — never a rewrite.

Specify each interface at the WEAKER contract, so tier-0 code cannot depend on
something tier 1 lacks: at-least-once delivery, no ordering guarantee, idempotent
consumers, explicit dedupe keys. Tier 0 simply over-delivers on it.

## Free now, ruinous later — done from the first commit
- [ ] sortable, non-sequential, shardable ids (ULID / UUIDv7)
- [ ] tenant / owner column on every row, even with one tenant
- [ ] idempotency keys on every externally-triggered mutation
- [ ] UTC timestamps with timezone awareness
- [ ] pagination on every list endpoint; no unbounded queries
- [ ] versioned public API and event shapes
- [ ] object-level authorization from the start
- [ ] no server-local state that blocks a second process
