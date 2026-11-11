# STATUS — 2026-08-29

**Phase 0 discovery is COMPLETE. Delivery has NOT started — this run stopped at an
explicit local evaluation boundary (no heartbeat, no tracker, no GitHub, no crews,
no commits). Not "complete", not "blocked": paused by instruction.**

## Where the project is

- The objective exists as files: `.omni/goal.md` + `docs/prd/DEFINITION-OF-DONE.md`.
- **23 DoD conditions, 23 remaining.** Nothing has been built or verified yet.
- 5 milestones mapped, every condition assigned exactly once (`docs/prd/MILESTONES.md`).
- Baseline commit: `ca52f103e06087dc2d8a62d7198ed0bd12729d6f` (branch `master`).
- Architecture of record: `docs/architecture/OVERVIEW.md`; ADR-0076…ADR-0082 accepted.
- **Seam count 3** (ADR-0077) — control · narrative memory · customer publication.
  Roster holds 2 crews, so the roster (not the architecture) is the binding cap.
- Scale tier **T0 in every row**; ~$150/month day one, ~$2,000/month at 100×.

## What is in flight

Nothing. No worker is dispatched, no lease is held, no slot is taken.

## What must happen first on any resume

1. Create the hourly heartbeat (`sys_scheduled_task_create`, `FREQ=HOURLY`) and
   verify it with `sys_scheduled_task_list`. **It does not exist.** Without it,
   nothing returns this org to the goal after a turn ends.
2. `nightshift-ctl run current --new` for a run_id — the binary was **absent from
   PATH** in this fixture; every later step (slots, claims, leases, sequences,
   checkpoints) depends on it being installed.
3. Commit and push discovery state, then `nightshift-ctl checkpoint create --wave 1`.
   Everything above is currently untracked or modified; a worker cut from the
   baseline SHA cannot see any of it.
4. Steps 4–5 of the wave loop: boundary map, `docs/owners.md` rows, worker grants,
   milestone-1 charters.

## The one thing most likely to go wrong next

M2 is six conditions of one-way doors (authority, invariants, tenancy, access,
components) and it is where the design stops being reversible. If a crew starts M2
before ADR-0077's seam boundaries are in `docs/owners.md`, two crews will contend on
the same control-plane paths and the lease queue will silently become the schedule.
