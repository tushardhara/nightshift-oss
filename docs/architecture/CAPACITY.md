# Capacity model

Filled 2026-08-22 from the two blind architecture drafts, which computed this independently and
agreed. Assembled by the program because `architect`'s tools cannot run on this host (ADR-0003,
issue #5); the numbers are transcribed from `docs/architecture/PROPOSAL-codex.md` and
`docs/architecture/PROPOSAL-claude.md`, not invented here. **Pending `architect` ratification
when Codex seats return.**

**The unit of work is a control-plane state transition** — a slot acquired or renewed, a claim
taken or released, a lease granted, a task row updated, a checkpoint cut, a vendor state change.
Each is a handful of small columns; none carries a document. Large artifacts (transcripts,
reports, specs) live in files and Git, never in hot SQLite rows.

| | Day one | Month six | If this succeeds |
|---|---|---|---|
| unit of work (and its size) | one control-plane transition, ~100–300 bytes | same | same |
| volume per day | ~5,760 slot heartbeats + ~1,440 program heartbeats + a few hundred transitions ≈ **7,500/day** | same — this system is one box by design | same |
| peak requests/sec (≈10× average hour) | **<10 writes/sec at burst**, far below 1/sec averaged | same | same |
| concurrent sessions | **4 workers max, 1 reserved for on-call** → 3 ordinary | same | same |
| rows stored / day | low thousands, mostly expiring | same | same |
| storage growth / month | **<5 MB per 7-day run** | same | same |

- **read:write ratio:** roughly 1:1. Every mutating command reads its own precondition inside
  the same transaction; the projection (`dump`) is read-mostly and cheap.
- **reads that tolerate staleness:** the file projections (`.omni/registry-*.json`,
  `control-backup.json`) — narrative, refreshed on demand. **The database is authoritative;**
  when a file and the database disagree, the database is right.
- **latency the user actually notices:** none directly. The operator experiences this system
  through GitHub comments and wave reports, minutes apart. Control-plane commands are
  human-invisible; only their *correctness* is observable.
- **durability and consistency the domain actually requires:** strict serialization on the
  contended resources — one holder per slot, one active claim per task, one holder per lease,
  monotonic sequence allocation. This is the whole reason the control plane exists, and it is
  precisely what a single SQLite writer gives for free.

## The arithmetic

From the Codex draft, showing the working:

```
4 live slots × 1 heartbeat/minute      =      4 writes/minute
                                       =    240 writes/hour
                                       =  5,760 writes/day
                                       = 40,320 writes over a 7-day run
+ 1 program heartbeat/minute           = 10,080 writes over the same week
+ claims, leases, tasks, checkpoints, vendor state, PR events
  (orders of magnitude rarer than heartbeats; allow 10× anyway)
                                       still < 10 writes/sec at burst
                                       still ≪  1 write/sec averaged
```

From the Claude draft, independently: **<200 writes/hour, <10k rows, <5 MB over seven days** —
and `tests/test_stress.py` already exercises roughly **1000× the production rate**.

**One SQLite database in WAL mode is appropriate by a wide margin.** Both architects said so
without seeing each other's arithmetic, and neither named a replacement technology. Most
projects discover their "millions" is thousands; this one discovered its thousands is hundreds.

**The property that actually matters is not row count — it is transaction duration.** No command
may hold a write transaction while launching an agent, calling GitHub, waiting on a vendor,
reading a transcript, running tests, or sleeping for backoff. Acquire, check, mutate, commit,
*then* do external work. (Whether every `nightshift-ctl` command obeys this is a question for
`docs/audit/CONCURRENCY.md`.)

**WAL growth** is bounded by regular successful checkpoints and by no process holding a read
transaction for hours. Backup uses `nightshift-ctl dump`, never a copy of the live file.

## The real ceiling is not here

Both drafts reached the same conclusion, and it is the most useful sentence in this document:
**the binding constraint is subscription quota, not storage.** With one login per vendor, real
capacity is not "four workers" — it is whatever concurrent and cumulative token usage two
subscriptions tolerate. Model tier, prompt size, transcript growth and review loops all matter
more than every SQLite write combined.

GitHub is likewise a non-issue by volume: an aggressive wave produces tens of API calls, not
thousands per minute. **The risk there is retry behaviour, not throughput** — a failed request
must never be retried in a tight loop, and a local state transition must be idempotently
reconcilable after an ambiguous remote result.

## Budget

- **infrastructure ceiling:** not stated in the brief. Assumed: the single VPS the owner already
  runs. No managed services, no second box.
- **estimated monthly cost at day-one volume:** **£0 marginal.** One SQLite file on a disk that
  already exists. The vendor subscriptions are the real spend and they are flat-rate, not
  metered — every notional dollar figure in the policy layer is a token-volume governor, not a bill.
- **estimated monthly cost at 100×:** still £0 marginal for storage. At 100× the *work*, the
  cost lands entirely on subscription quota and rate limits, which is why the scale ladder's
  only meaningful trigger is a second host — not a bigger database.
