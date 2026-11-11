---
name: allocate
description: Divide one global worker budget between N crews each wave, and arbitrate leases on shared paths — the two mechanisms that stop adding a crew from adding load or corrupting shared files.
---

# allocate — crews are ownership, parallelism is a shared budget

The bug this prevents: giving every crew its own worker cap. Two crews at two workers
is four; five crews is ten, against the same two subscriptions, and every vendor
saturates at once. Adding a crew must add **queueing**, never load.

So there is ONE budget, and you hand out grants from it.

## Capacity

`.omni/capacity.json` holds the CONFIGURED numbers:

```json
{ "max_concurrent_workers": 5, "max_items_in_flight": 6,
  "reserve_for_oncall": 1, "notes": "2 subscriptions" }
```

The ENFORCEMENT is the control plane: every dispatch to an engineer, qa, or
reviewer — any real CLI turn — takes an exact task/agent/title/purpose slot from
`nightshift-ctl slot acquire` first, and the named send carries task_id, purpose,
slot_id and generation. The root policy denies a dispatch without one. A slot is a
ONE-DISPATCH capability: it is bound to the agent it was acquired for
(`--holder <crew>/<agent>`) and consumed by the single send it authorizes —
reusing an id, or spending it on a different tuple, is denied. Mutable fix-tasks
claim first and take a fresh implementer-continuation slot. The check fails CLOSED: no control plane, no dispatch.
`pm` dispatches are exempt (a planning seat, not a CLI worker). The reserve is machine-kept:
normal acquires cap at `max - reserve`, so the incident lane's slot cannot be
eaten no matter how hot a wave runs. A slot not released by its holder frees
itself by TTL, so a crashed worker returns capacity without anyone noticing
it died. The upshot for you: grants are your scheduling INTENT; the semaphore
is the law; an arithmetic mistake in a charter queues work instead of
overloading a vendor.

Five concurrent workers total is the first measured trial for two subscriptions — each
slot is a real CLI process, and review/QA turns draw from the same budget
because they are the same physical load. Raise it (edit capacity.json; the
control plane reads it inside every transaction) only after a wave where no
vendor hit a limit; drop it the wave after any `usageLimited`.

## Granting, each wave

For every crew, put an explicit grant in its charter:

```
Grant this wave: 2 workers, 3 items in flight
```

Rules that make grants meaningful:

1. **The grants must sum to `max_concurrent_workers - reserve_for_oncall` or
   less.** If they don't, you have not allocated, you have overbooked — and the
   slot you overbooked is the incident lane's.
2. **Unequal is correct.** The crew on the critical path gets more. A crew waiting on
   a handoff gets one worker, or none.
3. **Zero is a real grant.** It means "you are next, not now". Say it plainly, and
   give that crew review work — reviewing another crew's PR costs almost nothing and is
   the most useful thing an unstarted crew can do.
4. **Never carry a grant over silently.** Re-state it every wave, even when it is
   unchanged, so no crew is running on a number it remembers rather than one it was
   given.
5. **A starved crew must say so, not self-serve.** If a crew reports its grant is too
   small for the work, either re-grant or re-sequence — but a crew quietly running a
   third worker is a bug you should catch in the retro.

Reserve one worker's worth of headroom for `oncall` when anything is in production.
An incident that has to wait for a feature task to finish is a self-inflicted
outage.

## Leases on shared paths

`docs/owners.md` maps every path to exactly one owner. Most shared things —
lockfile, CI config, lint config, the migration sequence, shared libs, test
fixtures — belong to `platform`, not to any crew.

When a crew needs a path it does not own, it asks you. You grant through the
control plane, which enforces **one holder per resource** transactionally:

```bash
# normal borrow — an EDIT lease, releasable the moment the PR opens
nightshift-ctl lease acquire --resource 'src/shared/**' --holder crew_alpha \
  --task add-retry --mode edit
nightshift-ctl lease release --resource 'src/shared/**' --event pr-opened

# order-sensitive resources — a SERIALIZE lease, held through review
nightshift-ctl lease acquire --resource 'db/migrations/**' --holder crew_beta \
  --task add-tax-column --mode serialize
nightshift-ctl seq next --name migrations --resource 'db/migrations/**' --holder crew_beta
nightshift-ctl lease release --resource 'db/migrations/**' --event pr-merged  # or dropped
```

Mirror each grant as a one-line file in `.omni/leases/` for the human record —
the database is authoritative, the file is the projection.

- **Grant leases on the exact glob strings from `docs/owners.md`.** The
  control plane matches resources by string, not by glob overlap — one
  canonical string per resource is what makes "one holder" checkable.
- Migrations are the sharpest case: the sequence number comes from `seq next`
  and allocates ONLY to the lease holder, atomically. Two crews each picking
  "the next number" produce a schema history that cannot be replayed — now
  the second crew gets exit 3 instead of a duplicate number.
- A lockfile lease means one crew resolves dependencies at a time — SERIALIZE,
  because the resolution that matters is the one that merges. N crews resolving
  in parallel produce N incompatible trees and one broken default branch.
- EDIT leases release at PR-open — holding through review blocks everyone for
  a review cycle. SERIALIZE leases hold until merge/drop by design, and the
  tool refuses an early release; that queue is the feature, not a bug.
- A stale holder (dropped task, dead crew) is broken explicitly: `nightshift-ctl
  lease break --resource '<glob>' --reason "..."` — recorded, never silent.
  Edit leases also lapse by TTL as a backstop; serialize leases never do.
- Queue the rest. Tell the waiting crew what it is waiting for and how long; let
  it work on something else meanwhile. A crew that reaches into a leased path
  anyway is a retro finding with a name attached.

## When contention keeps happening

Repeated leases on the same glob from different crews is not a scheduling problem, it
is a **slicing** problem: the boundary map put one concern in two crews. Fix the map
at the next wave boundary rather than running a permanent queue, and record why in
`.omni/ledger.md`.

The honest ceiling: N crews only work if the repo has N reasonably independent
seams. Adding a crew to a codebase with two seams buys you three crews queueing on the
same files. Crews scale with the architecture, not with ambition — and when the
architecture is the limit, say so in the report instead of quietly serialising
everything.
