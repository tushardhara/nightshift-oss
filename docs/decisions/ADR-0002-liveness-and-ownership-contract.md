# ADR-0002: Separate liveness signals per owner; workers renew their own slots, with fencing

Date: 2026-08-22 · Status: **accepted** (amended 2026-08-22 after `staff` review — decision item 4a added)
Deciders: program (cross-seam interface — neither crew may settle it alone)
Proposers: `architect` (Codex) and `staff` (Claude), independently and blind
Conversation: 370d8bf6ad1442dd988d9a9f77620da3 · Spec: this ADR is the seam contract

**Amended by: ADR-0006 (2026-08-23)** — §1 gains a fifth signal and its ownership rule is restated
as a lifetime-binding property; §2/§3 gain intra-turn progress semantics and an optional `seq_ts`.
**All four original rows, and §4–§7, stand unchanged.** The decision below is NOT edited in place.

**Amended by: ADR-0017 (2026-08-24)** — §1 gains a sixth, separately-owned
scheduled-run observation signal. It may never stand in for or refresh Program
liveness; all prior signal rows and semantics stand unchanged.

## Context

Brief bug 4 says the watchdog treats general `.omni` activity as proof the Program is alive.
Both architects independently found the defect larger than the brief describes, and both named
it the single most dangerous thing in the system.

The mechanism, confirmed in code: `deploy/nightshift-watchdog.sh:53` takes the newest mtime
under `.omni/`; `deploy/nightshift-state-backup.sh:32` rewrites `.omni/control-backup.json`
every 30 minutes. A dead Program therefore reads healthy forever.

Both drafts found the second-order consequence the brief does not name: while that false
"healthy" holds, the watchdog calls `nightshift-ctl slot renew-active`
(`nightshift-watchdog.sh:60-62`), renewing **every** active slot. Codex: this *"converts a
per-worker lease into an organization-wide lease."* Claude: after a restart, capacity can sit
at zero for up to a day with no alert. Codex's summary of the whole class:

> **"The recovery mechanism generates the evidence that suppresses recovery."**

Codex additionally found that the watchdog renews its own signal — `slot renew-active` writes
under `.omni/` — so the monitor refreshes the very evidence it monitors.

Live confirmation on this box (2026-08-22): bug 3 meant neither timer was ever enabled, so
this run has had no watchdog and no off-box backup at all. Bug 3 disables the recovery layer
that exists to catch bug 4.

This ADR is the **interface between the two seams**. Both architects insisted it be written
before implementation: the coordination seam owns and publishes it; the host seam consumes it
and may stub it, so neither crew blocks the other.

## Options considered

### A — Refine the mtime heuristic (exclude backup outputs from the `find`)
Cheapest patch. **Rejected** on Codex's reasoning: *"Do not refine the `find` expression with
exclusions. Any directory-wide activity heuristic will regress when a new timer or report
writer appears."* It decays silently the next time anyone adds a writer under `.omni/` — the
worst failure mode on an unattended box, because it re-breaks without anyone touching it.

### B — Attributed heartbeat, read by name (proposed by `staff`)
`nightshift-ctl beat --who program` writing to a `meta` table, read by `beat show`; renewal
gated on the holder's lead beat and `dispatched_at IS NOT NULL`; `ExecStartPre` reap-all on
host start. Concrete and small.

### C — Structured heartbeat record validated by content (proposed by `architect`)
Four separate signals for four separate owners; a record carrying schema version, run ID,
session identity, wall-clock time, **host boot ID** and a monotonic loop sequence; written
atomically or as one transactional row; the watchdog inspects **content, not mtime**, and
rejects a wrong run ID, a previous boot, a future timestamp, a non-increasing sequence, or a
malformed record. Plus worker-owned renewal and fencing tokens.

**Taken: C, with B's CLI surface.** They agree on the requirement and do not conflict: C is
the record and its validation rules, B is the command shape and the renewal gate. C's boot ID
and monotonic sequence are the parts neither the brief nor draft B had, and they are exactly
what makes a post-reboot stale heartbeat detectable.

## Decision

**1. Four signals, four owners. No signal may stand in for another.**

| Signal | Written by | Never written by | Answers |
|---|---|---|---|
| Program heartbeat | the Program, after a **completed** control-loop iteration | any timer, worker or backup | "did the org's brain run?" |
| Worker heartbeat | the bound worker, or a supervisor that positively verified *that* worker | the Program, blanket | "is this specific worker progressing?" |
| Terminal project state | a control-plane state transition tied to the current run | Markdown | "is the project finished?" |
| Backup health | the backup path, monitored separately | — | "is state reaching off-box?" |

**2. The Program heartbeat record** carries at minimum: schema version, run ID, Program
session identity, wall-clock timestamp, **host boot ID**, last completed loop sequence, and
optionally the current phase. Written atomically (temp file + rename) or as one transactional
row. Preference: a SQLite row, since the watchdog already depends on `nightshift-ctl`; a file
is acceptable if recovery must survive a damaged database. **The backup must never update it.**

**3. The watchdog validates content, not mtime.** It must reject: a wrong run ID; a heartbeat
from a previous boot; a timestamp materially in the future; a non-increasing loop sequence; a
malformed record. Any of those means "not alive", not "alive".

**4. `slot renew-active` is deleted, or narrowed to a per-slot operation requiring proof of
the corresponding live run.** Program liveness must never blanket-renew worker capacity.
Workers renew their own slots.

**4a. `slot heartbeat` must verify caller identity before it renews anything.** Added
2026-08-22 after `staff`'s pre-build review, and this is the reason the review exists.
`cmd_slot_heartbeat` (`bin/nightshift-ctl:305-317`; parser at `:907-910`) takes only `--slot`
and `--ttl` and executes `UPDATE worker_slots SET expires_at=? WHERE id=? AND state='active'`.
**There is no holder check at all** — anyone who knows a slot id can renew it. Wiring workers
onto this verb as-is would satisfy the letter of "worker-owned renewal" while reproducing, in
miniature, the exact defect this ADR exists to remove: an unverified signal treated as proof.

The fix is not novel; the precedent is in the same file. `consume_slot`
(`bin/nightshift-ctl:146-167`) already does the holder match:

```python
if agent and not (holder == agent or holder.endswith("/" + agent)):
    return False, (f"slot {slot_id} was acquired for holder '{holder}', ...")
```

`slot heartbeat` must take the same `--holder`/`--agent` argument and apply the same check
inside its existing transaction. **This is required work in milestone 1, named here so it is
not discovered mid-wave.**

`tests/test_slots.py:108-132` currently exercises `slot heartbeat` without an identity argument
and must be updated to assert the check. That is a **strengthening** of a control, not a
weakening — flagged explicitly because this project's brief forbids weakening controls, so
every test change touching a safety invariant has to be visible and justified rather than
quietly absorbed.

**5. Slots and claims carry a fencing generation.** A monotonically increasing claim
generation, bound to run ID; completion succeeds only if that generation is still current.
Codex's failure mode, which nothing today prevents: *"a paused worker can resume after its
claim expired and overwrite the replacement worker's result."*

**6. Terminal state comes from a control-plane transition**, not from `STATUS.md`'s first line
containing "complete" (`nightshift-watchdog.sh:45-48`).

**7. Claim ownership is not exactly-once.** Every externally visible action (PR creation,
issue comment) needs an idempotency key or a reconciliation step, because a worker that acts,
loses the response and dies will have its action repeated by whoever reclaims the task.

## Consequences

**Easy:** a dead Program is detectable within one threshold regardless of what else writes to
`.omni/`; leaked slots return to the budget on their own TTL; a post-reboot stale heartbeat is
rejected rather than believed; the two seams can proceed in parallel against this contract.

**Hard:** this changes public semantics that existing code and tests depend on — the reason it
is sequenced first, behind a written contract, rather than patched mid-project.

**Forecloses:** blanket renewal as a recovery tactic. Anything wanting it must positively
verify each bound run instead.

## Cost

Zero infrastructure. One extra row or small file written per control-loop iteration —
Codex's arithmetic puts a per-minute Program heartbeat at ~10,080 writes per seven-day run,
irrelevant against SQLite's capacity. Operational surface is net **negative**: three
mechanisms are deleted and one is added.

## One-way door?

**Partly — this is the one to be careful with.** Changing the public semantics of slot renewal
and heartbeat ownership was on both architects' one-way-door lists. Mitigations, required:
version the heartbeat record from its first commit (`schema_version`); keep the old renewal
path alive for one wave behind the new one; back up `control.db` before any schema change;
support one schema version of rollback. The DELETION of `slot renew-active` lands only after
worker-owned renewal is merged and observed working — not in the same PR.

## Scale tier

T0. No ladder movement. This is a correctness change, not a capacity one.

## Reversal trigger

Revisit if worker-owned renewal proves unable to cover a legitimate long-running turn — i.e. a
worker is reaped mid-flight *despite* renewing correctly. That would mean the TTL, not the
ownership model, is wrong; fix the TTL, and do **not** restore blanket renewal, which would
reintroduce exactly the failure this ADR removes.


---

## Staff review — `consistent-with`, conditionally (2026-08-22)

Full review: `docs/decisions/ADR-0001-0003-staff-review.md`.

**What it verified against the code**, which is what made it worth doing:
- The `meta` table and the transactional pattern this contract needs **already exist** and fit.
- The fencing-generation columns are a **conventional additive migration**, the same pattern as
  `dispatched_at` (`bin/nightshift-ctl:131-144`). No destructive schema change; no rewrite.
- The staged deletion of `slot renew-active` is safe **as staged** — add worker-owned renewal,
  observe it, delete blanket renewal in a later PR.
- `tests/test_claims.py` and `tests/test_recovery.py` do **not** need to change. Only
  `tests/test_slots.py`, and only to assert a stronger check.

**Its condition for starting milestone 1:** decision item 4a above had to be written into this
ADR before build begins. It now is, which satisfies the condition.

**Bias note:** `staff` wrote one of the two blind architecture drafts this ADR reconciles, so it
was explicitly instructed not to rubber-stamp the parts derived from its own draft nor fight the
parts from Codex's. It found a defect in the merged result that neither input contained — which
is the outcome that justifies the separate review step rather than trusting the merge.
