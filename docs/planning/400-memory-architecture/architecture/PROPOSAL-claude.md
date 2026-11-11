# Nightshift memory & project-state architecture — Claude-side proposal

Status: discovery draft, written blind against the Codex-side proposal.
Author: staff (acting as claude-side architect). Input: `.omni/brief.md` only.
Scope: architecture and planning only. No code, no infra, no external services.

Assumptions made where the brief is silent are marked **[A#]** and listed inline.

---

## Capacity

Arithmetic first; no technology named until the numbers exist. All numbers are
derived from what Nightshift actually is: a delivery org of ~5 concurrent agent
workers running for days against one repository.

**[A1]** Day one = 1 tenant, 1–3 repositories, ~5 concurrent workers, ~10 human
operators. 100× = ~100 tenants, ~300 repos, ~500 concurrent workers across all
tenants. "Month six" interpolated at ~10 tenants.

### Unit-of-work arithmetic (per org-day, day one)

| Quantity | Estimate | Working |
|---|---|---|
| tasks/day | ~50 | a program decomposes a day's delivery into 20–60 tasks; take 50 |
| dependency edges/day | ~100 | ~2 edges/task |
| task-state transitions/day | ~400 | ~8 transitions/task (created→ready→claimed→running→review→done, plus retries/re-queues) |
| claim/lease events/day | ~200 | claims, releases, expiries logged as events; heartbeats are **updates in place** on the lease row (5 workers × 1/30 s ≈ 14k updates/day, deliberately NOT logged as rows) |
| decisions/day | ~10 | ADR-level and program-level decisions |
| reports + handoffs/day | ~60 | ~1 report/task + ~10 handoffs |
| evidence artifacts/day | ~100 | test logs, diffs, screenshots |
| transcript volume/day | ~100 MB raw | 5 workers × 200–400 turns/day × 30–60 KB JSONL/turn ≈ 20 MB/worker-day. Compresses 5–10× → ~15–25 MB/day stored |
| largest single artifact | ~5 MB typical, 1 GB cap | full build/test log or a worker-day transcript archive; cap enforced at ingest |
| embedding chunks/day | ~1,000 | embed reports, decisions, handoffs, summaries, and *salient* transcript segments only — never raw transcripts wholesale. ~1k chunks × 768 dims × 4 B ≈ 3 MB/day of vectors |

### Rates

- Control-plane writes: ~1–2k rows/day + ~14k in-place lease updates/day
  ≈ **0.2 writes/sec average, ~2/sec at 10× peak hour**.
- Control-plane reads: workers polling ready-tasks + operator UI ≈ 5–10 reads
  per write → **~5 req/sec peak** day one.
- Rows stored/day: ~3–4k across all tables → **~100k rows/month** day one.
- Storage growth/month: control DB < 10 MB; blobs ~3 GB (compressed
  transcripts + artifacts); vectors ~90 MB.
- At 100×: ~350k rows/day → **~11M rows/month**; ~12 writes/sec average,
  **~120/sec peak**; blob growth ~300 GB/month; vectors ~18 GB/month at worst
  (mitigations below).

### Ratios, staleness, latency, durability

- **Read:write** — control ≈ 5–10:1; memory plane is write-heavy at ingest,
  read-light at query (tens of retrieval queries/day per org).
- **Staleness tolerance** — retrieval and dashboards tolerate seconds-to-minutes
  of staleness. Claim/lease/transition reads tolerate **zero** staleness: a stale
  read there is duplicated or conflicting work.
- **Noticed latency** — operator dashboard < 1 s; retrieval < 1 s; claim latency
  matters only for throughput, sub-100 ms buys nothing at 5 workers.
- **Durability, differentiated** — losing a lease is *safe by design* (TTL +
  fencing recovers it). Losing a task-state or claim row is a **correctness
  loss** (split brain, repeated work) → synchronous durable commit required.
  Losing a transcript is an **explainability loss** — bad, not corrupting →
  batched flush with a seconds-wide loss window is acceptable. Published
  documents live in customer Git and survive Nightshift entirely.

### Verdict of the arithmetic

Peak 120 writes/sec at 100×, 11M rows/month, tenant-scoped queries throughout.
**One relational instance handles this with an order of magnitude to spare, for
years.** The only quantities that ever get big are blobs (object-storage-shaped)
and vectors (bounded by embedding selectively and partitioning per tenant —
retrieval is always tenant-scoped, so no index ever needs to span tenants).

### Cost

| | Day one (local) | Day one (managed) | 100× |
|---|---|---|---|
| relational store | $0 (embedded) | small instance, 2 vCPU/4 GB: $25–50 | 8 vCPU/32 GB + 0.5 TB disk: $350–450 |
| blob storage | $0 (disk) | 3 GB/mo growth: <$1 | ~1 TB live (90-day transcript retention): $25–50 |
| compute | $0 | 1 small node: $20 | 3 medium nodes: $150 |
| key management | $0 | 1–3 keys + requests: $2–5 | ~100 tenant keys + requests: $100–130 |
| embeddings | ~$0 | <$5 | ~$50 |
| **total/month** | **$0** | **≈ $50–80** | **≈ $700–950** |

No cache tier, no queue broker, no dedicated vector database appears in either
column. The numbers do not justify them; the ladder (below) names the triggers
that would.

---

## Shape

**SEAM COUNT: 5.** That number caps parallel crews; it is not inflated — the
operator UI/CLI is a *consumer* of seams 1, 2 and 5, not a sixth seam, and
would collide with all of them if chartered independently before the interfaces
freeze.

| # | Seam | Owns | Interface | Two crews without collision? |
|---|---|---|---|---|
| 1 | **Control Ledger** | tasks, edges, task events, runs, slots, claims, leases, fencing tokens, sequence allocation, program epoch | `ControlStore`: transactional verbs — `create_task`, `claim(worker, ttl) → (lease, fence)`, `renew`, `transition(task, from, to, fence)`, `allocate_seq(scope)` | yes — consumers code against the verb contract; internals free behind it |
| 2 | **Memory Plane** | immutable content-addressed artifacts (transcripts, reports, decisions-as-documents, handoffs, evidence), artifact metadata index, chunking + embeddings + retrieval | `ArtifactStore`: `put(bytes, kind, provenance) → artifact_id`, `get`, `search(tenant, query) → advisory citations` | yes — pure append + read; no shared mutable state with seam 1 |
| 3 | **Publication Gateway** | the ONLY write path into a customer repository; approval checks, path allowlist, provenance trailers, publication records | `Publisher`: `publish(artifact_id, decision_id, target_path) → publication_id` (refuses without an approval decision in the ledger) | yes — consumes seams 1+2 read-only, owns Git egress exclusively |
| 4 | **Workspace & Provenance Manager** | Nightshift-owned bare mirrors of customer repos, worktrees outside the repo, SHA pinning, keep-refs, snapshot records | `Workspace`: `snapshot(repo) → (commit_sha, keep_ref)`, `checkout(snapshot) → worktree_path`, `resolve(sha) → objects` | yes — owns Git ingress exclusively; seam 3 owns egress; they meet only at the ledger |
| 5 | **Tenancy & Safety Envelope** | authn/z, tenant scoping enforcement, envelope encryption + key custody, redaction pipeline, retention jobs, export, deletion (incl. crypto-shred), audit read API | `Envelope`: wraps seams 1–2 storage calls; `export(tenant)`, `delete(tenant)`, `redact(artifact_id)` | yes — enforced at the storage boundary, built against seam 1/2 contracts |

Data flows: workers ↔ seam 1 (state) and seam 2 (artifacts); seam 4 feeds
provenance identifiers into everything; seam 3 is the single egress; seam 5
wraps 1 and 2. Git ingress (4) and Git egress (3) are deliberately different
components so "read the customer's code" and "write into the customer's repo"
can never share a code path or a credential.

---

## The three-way separation

| Plane | Contents | Lives in | Mutability |
|---|---|---|---|
| **CONTROL** | tasks, edges, state, runs, slots, claims, leases, sequences, epochs | Control Ledger (relational, transactional) | mutable under transactions + fencing |
| **MEMORY** | transcripts, reports, decisions-as-narrative, handoffs, evidence, embeddings | Memory Plane (immutable content-addressed blobs + index + vectors) | append-only; redaction = supersede + crypto-shred |
| **PUBLISHED** | ADRs, PRDs, release notes deliberately placed in the customer repo | customer Git, via Publication Gateway only | customer-owned the moment it merges |

**Who wins when they disagree:**

- Control vs memory: **control wins, always and mechanically** — for "what is
  the state now" there is exactly one durable authority (the ledger). A report
  claiming a task is done while the ledger says `running` is a narrative error,
  logged as such, never reconciled *toward* the narrative.
- Control/memory vs published: for **state**, the ledger wins. For **"what the
  customer approved"**, Git wins — a published doc is a snapshot; if the
  customer edits it, that edit is customer content, ingested as new evidence,
  never auto-overwritten. Re-publication is a new explicit operation.

**Mechanisms that stop silent plane-crossing (not intentions):**

1. **Privilege separation.** The retrieval/memory service runs under a database
   role with zero grants on control tables. It *cannot* write state, even when
   buggy.
2. **Fencing-token gate.** Every control mutation requires a fencing token that
   only the ledger issues to a live lease holder. Memory-plane code is never a
   lease holder; retrieval results are typed as advisory citations
   (artifact-id + span), a type no `ControlStore` verb accepts.
3. **Single egress.** Nothing except the Publication Gateway holds a credential
   that can write to a customer repo; the gateway refuses any publish lacking a
   ledger approval decision, and refuses paths outside a per-tenant allowlist.
   A memory artifact cannot "drift" into the repo; there is no path.
4. **Append-only memory.** Artifacts are content-addressed and immutable, so
   memory cannot be edited to retroactively agree with anything; corrections
   are new artifacts that supersede, with both retained (subject to
   redaction/retention).

---

## Data model

IDs are ULIDs (sortable, non-sequential, shardable). Every row carries
`tenant_id`. All timestamps UTC. All list reads paginated.

**Entities:**

- `tenant` — id, name, key_ref, retention_policy, status.
- `repo` — id, tenant_id, origin_url, mirror_path.
- `snapshot` — id, repo_id, commit_sha, keep_ref, observed_branch,
  observed_at. The provenance anchor.
- `task` — id, tenant_id, repo_id, title, spec_artifact_id, state,
  current_fence, created_by_epoch.
- `task_edge` — (from_task, to_task, kind).
- `task_event` — id, task_id, seq (gapless per task), from_state, to_state,
  actor, fence, at. **Append-only; this is the audit log of control.**
- `run` — id, tenant_id, program_epoch, started_at, snapshot_id, status.
- `slot` — id, run_id, capacity semantics.
- `lease` — id, task_id, worker_id, fence (monotone per task, allocated by the
  ledger), expires_at (**ledger clock**), heartbeat_at.
- `decision` — id, tenant_id, subject, approvers, artifact_id, status.
- `artifact` — id, tenant_id, content_hash, kind, size, encryption_key_ref,
  snapshot_id, producer (run/worker/task), created_at.
- `embedding_chunk` — id, tenant_id, artifact_id, span, vector,
  derived_from — **the lineage edge that makes deletion complete**.
- `publication` — id, tenant_id, artifact_id, decision_id, repo_id, target_path,
  published_commit_sha, trailer_version.

**Concurrency-critical structures and failure modes:**

- **Worker dies holding a lease.** TTL expires (judged by ledger `now()`, never
  the worker's clock). Next claimant gets `fence+1`. The dead worker's
  in-flight writes carry the old fence and are rejected atomically at the
  ledger. Late artifact uploads are harmless (append-only, marked with the
  superseded fence).
- **Two Program turns racing.** A `program_epoch` row per tenant advanced by
  compare-and-swap; every program-issued mutation carries its epoch; stale
  epoch → rejected. Exactly one program turn can win; the loser observes and
  re-plans. Single-writer without any lock a crash can orphan.
- **Crash mid-schema-migration.** Migrations are transactional and versioned
  with a single migration-lock row; a crash rolls back or leaves a resumable
  recorded version — never a half-applied schema. (This is a hard requirement
  on the relational engine choice, met below.)
- **Clock skew.** No correctness decision ever reads a worker clock. Lease
  expiry, event ordering (per-task `seq`), and fencing are all ledger-side.
  Worker clocks appear only as informational annotations.
- **Idempotency.** Every externally-triggered mutation carries an idempotency
  key (worker_id + intent + fence); replays are no-ops. Artifact `put` is
  naturally idempotent via content hash (per tenant).

---

## Storage comparison — decided on the numbers above

| Candidate | Verdict | Why, on arithmetic |
|---|---|---|
| **PostgreSQL** | **take now** (production) | 120 writes/sec peak at 100× vs ~10k+/sec capacity of one instance; transactional migrations, `SKIP LOCKED` for claims, RLS for tenancy, gapless sequences, and vectors in the same box. One system of record, one backup story. |
| **SQLite** | **take now** (local/offline backend) | local mode must be zero-infra; 5 workers on one machine peak ~2 writes/sec, WAL-mode SQLite yawns at that. Same `ControlStore` contract, verified by a shared conformance suite (see contract section). |
| **Object storage** | **take now** (production blobs; content-addressed directory locally) | 1 GB artifact cap and 300 GB/month at 100× are object-storage-shaped, not row-shaped. $25–50/month at 100× vs bloating every DB backup with transcript bytes. |
| **Git (as a datastore)** | **published docs only; never for control or memory** | Git is the *problem statement* of this brief: no transactions, no leases, no TTLs, sync races, and every write is customer-visible noise. It is kept for exactly what it is good at — customer-owned reviewed documents and immutable content-addressed provenance. |
| **Redis** | **never at these numbers** | its jobs here would be cache (nothing is hot: 5 req/sec) and queue (claims at 2/sec peak day one, 120/sec at 100× — `SKIP LOCKED` covers it). Trigger to revisit: sustained claim contention > 500/sec or p95 claim latency > 250 ms. Adding it now is unbudgeted operational surface. |
| **pgvector** | **take now** | day one ~30k vectors/month — trivial. At 100×, per-tenant partitioning keeps every index at ~30k vectors/month *per tenant* because retrieval is always tenant-scoped; no index ever spans tenants. 768-dim embeddings halve storage. |
| **Dedicated vector DB** | **at a named trigger, likely never** | trigger: any single tenant's index > 5M vectors, or retrieval p95 > 300 ms at k=20 after quantization. Below that it is a second datastore, a second backup, a second failure mode, for queries we run tens of times a day. |
| **Mem0** | **never** | the brief's hardest requirement is that memory *cannot decide*. Mem0 is an opinionated layer that auto-extracts and auto-updates "facts" — a second, self-editing authority, which is precisely the failure mode the fence exists to prevent. Its capability is reproducible with `artifact` + `embedding_chunk` + retrieval under our own privilege separation, at our sizes, with no vendor coupling. Rejected on architecture *and* arithmetic. |
| **Temporal knowledge graph** | **never now** | the append-only `task_event` log with per-task `seq` *is* the temporal record, and it is queryable with recursive CTEs. A KG is a second representation to keep consistent, serving no query we have. Trigger to revisit: a named product feature needing multi-hop entity traversal across > 100k entities that measured SQL cannot serve under 1 s. |

---

## Zero-file repo mode

**Default mode. [A2]** The customer repo contains nothing of Nightshift's: no
`.omni/`, no generated `docs/`, no worktrees, no refs pushed.

- **Where state goes instead:** everything under `$NIGHTSHIFT_HOME`
  (default `~/.nightshift/`) locally, or the managed service's stores:
  - `mirrors/<repo>.git` — Nightshift-owned bare mirror (seam 4). All reads
    come from here; the customer remote is only fetched from.
  - `worktrees/<tenant>/<repo>/<run>/` — detached worktrees created from the
    mirror at a pinned snapshot SHA; deleted after run + evidence capture.
  - control facts → ledger; narrative/docs → memory plane, viewable through
    Nightshift's UI/CLI or exportable as a docs bundle.
- **Generated docs:** live as artifacts in the memory plane. They reach the
  repo only through the Publication Gateway as a reviewed change (branch + PR
  in production; a prepared patch/commit for local mode **[A3]** — the brief
  forbids PR creation *for this evaluation*, not for the designed system).
- **Capability lost in this mode, named honestly:**
  1. *Repo-local discoverability* — a developer browsing the repo sees no
     Nightshift docs unless they were published.
  2. *History travels with the clone* — Nightshift's history no longer rides
     along with `git clone`; you need the export bundle.
  3. *Plain-Git review of working notes* — reviewing drafts requires
     Nightshift's viewer or an export, not `git diff`.

---

## Local/offline vs production multi-tenant: one contract, two deployments

**The contract is the three seam interfaces plus these invariants:**

1. Fencing tokens are strictly monotone per task; a mutation with a stale fence
   is rejected, atomically, in both deployments.
2. `task_event` is append-only with a gapless per-task `seq`; no deployment may
   ever update or delete an event.
3. Artifacts are immutable and content-addressed per tenant; `put` of identical
   bytes is idempotent.
4. Every row carries `tenant_id` — **even locally**, where it is the literal
   tenant `local`. Local mode is multi-tenant code with one tenant, not
   single-tenant code.
5. `publish` refuses without an approval decision; both deployments.
6. Lease expiry is judged by store-side time in both deployments.
7. Retrieval output is advisory-typed in both deployments; no control verb
   accepts it.

**Enforcement mechanism, not intention: a shared conformance test suite is the
contract.** Every invariant above is a test executed against both backends
(SQLite and Postgres) in CI, including the kill-a-lease-holder and
two-epochs-race cases. A backend that passes is compliant; prose is not.

**What would make the two silently diverge — named:**

- SQL dialect drift (`RETURNING`, upsert semantics, serialization behavior,
  time functions) leaking through the `ControlStore` interface → keep the verb
  surface narrow; no raw SQL crosses it.
- Local mode skipping the encryption/redaction path "because it's local" →
  envelope runs in both; local KEK sits on disk/OS-keychain instead of KMS,
  but the code path is one.
- Single-process assumptions (in-memory locks, file locks) creeping into local
  code → the conformance suite runs local mode with multiple processes.
- Behavior added to one backend during an incident and never ported → rule:
  a fix lands as a conformance test first, then two implementations.

---

## Provenance

Every artifact, event, decision and publication carries
`(tenant_id, repo_id, snapshot_id)`, and `snapshot` binds to an **exact commit
SHA**.

Surviving history rewrite: a commit SHA is an immutable content address — a
rebase, squash or force-push creates *new* SHAs; it cannot invalidate old ones,
only make them unreachable and GC-able **on the remote**. So seam 4's mirror
pins `refs/nightshift/keep/<sha>` for every snapshot Nightshift ever
referenced: the objects stay resolvable in Nightshift's mirror forever
(retention permitting), regardless of what happens upstream.

Additionally per snapshot: observed branch name and per-change `patch-id`, so a
human can map "the work at old SHA X" onto the rewritten history after a squash
merge; when Nightshift observes a rewrite, it records a mapping event
(old SHAs ↔ new merge SHA) as evidence — advisory, since the original SHAs
remain the ground truth.

Published documents carry a versioned commit trailer
(`Nightshift-Publication: v1; publication_id; source snapshot SHA`), so
provenance survives *inside* the customer's history for exactly the documents
they chose to accept.

---

## Semantic memory, fenced

Retrieval helps drafting, review and "what happened" questions. It cannot
decide scheduling or override state. The **enforcing mechanisms** (all three of
the plane-separation mechanisms above, restated as they bind retrieval):

1. DB role for retrieval: `SELECT` on memory schema only; **no grants** on
   control tables. Enforcement is in the database, not in review discipline.
2. Control mutations require a lease-bound fencing token; retrieval components
   are not lease-eligible principals. There is no token for them to present.
3. Type fence at the API: retrieval returns `AdvisoryCitation` values;
   `ControlStore` verbs accept none of them. Influence on what a program
   *proposes* is allowed; authority over what the ledger *records* is denied —
   and every recorded mutation is validated against ledger invariants
   regardless of what inspired it.
4. Audit: every control mutation logs its actor and lease; a retrieval-driven
   mutation is impossible to record, hence impossible to smuggle.

---

## Tenancy and safety

- **Isolation:** `tenant_id` on every row + Postgres row-level security in
  production; per-tenant blob prefixes; per-tenant vector partitions (which is
  also the performance strategy — one mechanism, two benefits).
- **Encryption boundary & key custody:** envelope encryption. Per-tenant KEK in
  the platform KMS (production) or on disk/OS-keychain (local). Per-artifact
  DEK wrapped by the tenant KEK. Blobs encrypted client-side before reaching
  object storage. Content address = hash of plaintext **[A4]**, dedupe strictly
  per tenant (cross-tenant dedupe leaks content existence across the isolation
  boundary — refused by design).
- **Redaction:** secrets/PII scan at ingest, *before* embedding and storage.
  Post-hoc redaction of an immutable artifact = write redacted successor +
  tombstone + **crypto-shred the original's DEK** + delete all derived
  `embedding_chunk` rows via the lineage edge. Lineage makes redaction
  complete; without it, vectors of redacted text silently survive.
- **Retention:** per-tenant policy; default transcripts 90 d, evidence 365 d,
  ledger events retained (small, and they are the audit log) **[A5]**.
- **Export:** per-tenant bundle — ledger as JSONL, decrypted artifacts,
  publication manifest, provenance index. Also serves as the local↔managed
  migration format — one exporter, two uses.
- **Deletion:** *delete a tenant* = crypto-shred the tenant KEK (all blobs
  instantly unreadable), delete rows and vectors, retain a minimal deletion
  stub (tenant id, time, actor) unless full erasure is demanded. *Remove
  Nightshift entirely* = zero-file mode means the repo is already clean;
  publications remain (customer-owned by definition, enumerated in the export
  manifest); delete tenant; uninstall.
- **Audit:** `task_event` + decision + publication records *are* the audit
  history — append-only, fenced, provenance-bound. No separate audit subsystem
  to keep consistent.

---

## Migration from today's `.omni/`-in-repo model

At quiescence (no active runs), stepwise, each step reversible until the last:

1. **Inventory & classify** everything in `.omni/` and generated `docs/` into
   control / memory / publication-candidate. Read-only. *Rollback: n/a.*
2. **Import** history into the memory plane; provenance = the repo SHA at
   import. Ledger initialized from current task state. `.omni/` untouched.
   *Rollback: drop the new stores; `.omni/` still authoritative.*
3. **Shadow period:** new runs write to ledger + memory plane; `.omni/` is
   frozen read-only legacy. Verify N days: state answers, retrieval answers,
   export works. *Rollback: point Nightshift back at `.omni/`; delta since
   cutover is exportable back to files.*
4. **Cleanup change:** propose removal of `.omni/` and generated docs from the
   repo — itself a reviewed Publication-Gateway operation (the gateway's first
   real customer). *Rollback: revert the commit.*
5. **Optional re-publication** of documents the customer wants to keep in-repo,
   now with provenance trailers. *Rollback: revert.*

Nothing is destroyed at any step: even after step 4, Git history retains the
old `.omni/` tree, and step 2's import preserved it in the memory plane with
provenance.

---

## Hard parts — the three most likely to be wrong, and early falsification

1. **Fencing/lease correctness across two backends.** The design leans entirely
   on "stale fence rejected atomically" holding on SQLite *and* Postgres under
   crashes. *Prove early:* the conformance chaos test — N processes claiming,
   `kill -9` the holder mid-write, assert zero stale-fence writes land — is the
   **first thing built**, before any feature.
2. **Provenance surviving history rewrite.** Keep-refs in the mirror are the
   load-bearing idea. *Prove early:* scripted test repo that gets rebased,
   force-pushed and squash-merged; assert every recorded SHA still resolves in
   the mirror and mapping events are emitted.
3. **Deletion/redaction completeness across derived data.** Crypto-shredding a
   blob while its vectors, chunks or citation snippets survive is a silent
   compliance failure. *Prove early:* deletion audit that walks the
   `derived_from` lineage graph and asserts nothing derived outlives its
   source; run it on every redaction in CI.

---

## One-way doors

| Decision | Why expensive to reverse | Reversal trigger |
|---|---|---|
| plaintext-hash content addressing | changing it re-keys/re-addresses every blob | compliance finding that plaintext hashes leak across a boundary (mitigated day one by per-tenant scope) |
| ledger shape: append-only `task_event` + mutable snapshot rows | moving to pure event sourcing later migrates every consumer | any consistency bug traced to snapshot/event divergence |
| publication trailer format | lives in customer Git history forever | none — versioned from v1 so evolution isn't reversal |
| per-tenant (not global) dedupe | globalizing later means re-examining every blob's isolation | none foreseen; global dedupe is the mistake, not the door |
| the `ControlStore` verb contract | two backends mean every contract change costs double, forever | conformance suite failing to express a needed invariant |

---

## Scale ladder (this project's rows)

| Concern | T0 — build this | Move up to | Measurable trigger |
|---|---|---|---|
| control ledger | one Postgres instance (SQLite locally) | read replica; partition `task_event` | p95 write > 100 ms sustained, or `task_event` > 100M rows (~10 months at 100×) |
| work claiming | ledger table + `SKIP LOCKED` | broker (Redis streams/SQS-shaped) | sustained claims > 500/sec or p95 claim > 250 ms |
| blobs | object storage + presigned URLs (content-addressed dir locally) | + lifecycle tiering / CDN | egress > $100/mo or restore-time SLO miss |
| vectors | pgvector, per-tenant partitions, 768-dim | quantization/halfvec; then dedicated store | per-tenant index > 5M vectors or retrieval p95 > 300 ms at k=20 |
| cache | none | app-memory; then Redis | any single query > 10% of DB CPU sustained |
| search | Postgres FTS on artifact index | OpenSearch single node | FTS p95 > 500 ms at measured corpus |
| compute | one process per deployment | autoscaling group | worker-start queueing > 60 s sustained |
| realtime (UI) | polling (5 s) | SSE | > 50 concurrent operator sessions per node |
| observability | structured logs + host metrics | managed dashboards | first cross-tenant incident that logs alone couldn't sequence |

Free-now/ruinous-later checklist (ULIDs, tenant column everywhere, idempotency
keys, UTC, pagination, versioned event shapes, object-level authz, no
process-local state): **adopted from the first table, day one** — each appears
in the data model above, not as aspiration.

---

## Contradictions found in the brief — named, not papered over

1. **"Zero-Nightshift-file mode" vs "documents that should become part of the
   customer's repository."** Publishing an ADR puts a Nightshift-produced file
   in the repo. Finishable reading: zero-file means zero *unsolicited* files;
   an explicitly reviewed publication is customer-owned the instant it merges,
   so the mode's promise ("no operational clutter, nothing unapproved")
   survives. The impossible reading — literally never a byte — would delete the
   publication requirement.
2. **"Semantic memory cannot decide scheduling" vs agents that plan using
   retrieved context.** Retrieval *will* influence what a program proposes;
   that is its purpose. Finishable reading: influence allowed, **authority
   denied** — the fence sits at the mutation interface (roles, fences, types),
   not inside the planner's head. The reading "memory must not influence
   planning" is unenforceable and unfinishable.
3. **"Same behavioural contract" local/offline vs multi-tenant obligations
   (KMS custody, RLS).** Identical *implementation* is impossible offline.
   Finishable reading: the contract is behaviour and invariants (the
   conformance suite), with named custody substitutions; local is multi-tenant
   code with one tenant, so the invariants are exercised, not waived.
4. *(Minor)* the brief mandates capacity-before-technology yet pre-names eight
   technologies. Read as comparison scope, not a shortlist endorsement; each
   got a verdict on arithmetic above.

---

## Assumptions register

- **[A1]** Day-one and 100× population as stated in Capacity.
- **[A2]** Zero-file mode is the *default* for customer repos; dogfooding may
  opt into legacy in-repo mode during migration only.
- **[A3]** The brief's ban on PRs/branches applies to this evaluation, not to
  the designed system's publication mechanism.
- **[A4]** Content addressing hashes plaintext (enables per-tenant dedupe and
  stable IDs across re-encryption); accepted as a one-way door above.
- **[A5]** Default retention (90 d transcripts / 365 d evidence / ledger kept)
  pending per-tenant policy; ledger events are small enough (~100k rows/mo
  day one) that keeping them is cheaper than arguing about them.
