# Survival audit — Q1–Q4 (DoD condition 14, issue #478)

Date: 2026-09-01 · Crew: crew_alpha · Issues: #478 (this doc), #423/#475 (parent split),
#482 (FINDINGS.md — not yet merged at the time this doc was written; see the provisional-id
note under Findings) · Base/audit SHA: `662600662ee17267aed75bb945556f9a40ae0e42`
(`origin/main` tip, re-verified with `git rev-parse origin/main` in the same turn the
worktree was cut — this audit made no code changes, and every permalink below and every
test run cited was executed fresh against this exact commit).

Raw capture: [`SURVIVAL.capture.txt`](SURVIVAL.capture.txt) — every command cited below as
`"type": "command"` evidence appears there verbatim, with its exact invocation and full
output. As in #477's `CONCURRENCY.md`, `docs/prd/DOD-MANIFEST.json` is `product`-owned
(single-writer per `docs/owners.md`), so this crew-authored audit does not mint new
`RT-nnn` ids into it; every `"type": "test"`-shaped claim is instead cited as a `"command"`
record running the exact `python3 -m unittest` node directly.

## WA-5 disclosure

This crew's dispatch to a counted worker on this exact task reproduces the live wall
tracked at #489 (`sys_session_send`'s object-form `args` schema — `{cost_budget, file_ids,
input, model, purpose}` — has no field for `slot_id`/`task_id`/`generation`, which the root
`worker_semaphore` policy requires on every `eng_claude`/`eng_codex`/`qa`/`reviewer_*`
dispatch; the tool call is rejected by client-side schema validation before the policy ever
runs), compounded by #490 (the one documented redeploy mechanism, `nightshift-release
prepare`, cannot re-run on this host because the legacy supervision units it requires are
masked-by-design after the prior successful cutover, and unmasking does not restore them —
`LoadState` goes to `not-found`, not `loaded`). Per **WA-5** (`.omni/working-agreements.md`
item 5, issue #13) and the owner's explicit, same-session invocation of it for this item
(`.omni/decisions.md`, 2026-09-01 "Owner overrode platform-edit-lease pattern with WA-5 for
DoD document items" entry — cause `#489/#490`), the crew_alpha lead built this document
directly, single-author, under the control-plane hold recorded for this task (lease 66,
`docs/audit/SURVIVAL.md`, edit mode, note "WA-5 build (owner-directed parallel batch, cause
#489/#490)" — `crew_alpha` is itself in `EXEMPT_AGENTS`
(`bin/nightshift-ctl:223-226`), so no counted-worker `slot acquire` applies to the lead's
own build). No `eng_claude`/`eng_codex` dispatch was attempted for this task, per the
owner's explicit instruction that the substitution was already established today. In-crew
review and in-crew QA did not run. **Cross-crew review (crew_beta) is therefore mandatory,
not customary, for this PR.**

Scope, per #478's own boundary: audit and cite evidence only. No code changes. Any
`CONFIRMED-BUG` below is reported, not remediated, and any finding is disposed in
`docs/audit/FINDINGS.md` (#482), cited here as provisional if that document has not merged
yet when this PR is reviewed.

## Summary

| Q | Verdict | Findings |
|---|---|---|
| Q1 — `kill -9` of a worker, a lead, and the Program (mid-wave): what is lost, what reconstructs, is anything lost or duplicated? | **SOUND** | — |
| Q2 — does the first post-reboot heartbeat reconcile without re-dispatching in-flight work? | **SOUND** | — |
| Q3 — rate limit vs. hard outage: transitions, persisted timer identity, max retries, terminal state | **SOUND** | — |
| Q4 — 3–7 day growth (what's bounded, what isn't) and single points of silence | **SOUND** | — |

No `CONFIRMED-BUG` verdict is raised in this document. Two named, already-accepted
structural limitations are documented in full below because the DoD's own condition-14 text
requires recording them plainly (Q3's non-expiring-outage gap; Q4's off-box terminator gap,
already an accepted residual risk in ADR-0006) — neither is a behavioural defect: the code
does exactly what its own design documents say it does, and the gap is an absent
capability, not an incorrect one. Both are cross-referenced to the ADR/decision that already
owns them rather than re-raised as new findings.

```json
[
  {
    "id": "Q1",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "expire_stale (claims/slots/agent_runs/leases[edit]/program_authority all lazily expired past their own TTL, inside the caller's own transaction)", "line": 1086, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_authority_acquire (expire_stale runs BEFORE the incumbent check; a dead Program's row is reaped by the very next acquire attempt, which then mints generation+1, reason string names 'ADR-0044 §5')", "line": 1839, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/decisions/ADR-0044-program-authority-fencing.md", "symbol": "Decision: no force-break, adopting B -- a crashed holder cannot present a live token, so recovery is TTL expiry advancing the generation, never an operator break", "line": 205, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_lease_release (--holder optional identity check) vs cmd_lease_break (unconditional force-drop, any mode, no identity at all)", "line": 1758, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "EXEMPT_AGENTS (crew_alpha/crew_beta/pm/... never acquire a counted worker_slot or task_claims row for their own WA-5 self-performed build)", "line": 223, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_recovery", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest -v test_recovery'"},
      {"type": "command", "command": "sqlite3 read: SELECT * FROM task_claims WHERE task_id IN ('477-concurrency-audit','478-survival-audit')", "output": "docs/audit/SURVIVAL.capture.txt § 'sqlite3 read (live control.db, read-only) -- Q1 lead-build evidence'"},
      {"type": "command", "command": "nightshift-ctl lease list (live)", "output": "docs/audit/SURVIVAL.capture.txt § 'Live control-plane facts read at audit time'"}
    ]
  },
  {
    "id": "Q2",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "tasks / task_claims / worker_slots schema (task id is a stable TEXT PRIMARY KEY; generation is a monotonic per-task-id counter on both task_claims and worker_slots)", "line": 238, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "TERMINAL_TASK_STATES = frozenset({'done','merged','dropped','superseded'})", "line": 4393, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "the wider closable set {'done','merged','dropped','superseded','integrated'} used by cmd_issue_closable's per-task state reduction", "line": 2186, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "continuation-phase gate: task[state] not in {'building','ready','fixing'} is refused -- only these three are eligible in-flight/recoverable states for a continuation dispatch", "line": 1267, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_beat_show wrong_boot check -- a heartbeat written before the last reboot is REFUSED, never restamped, so a fresh post-reboot Program cannot be told alive by a pre-reboot record", "line": 3627, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_recovery.Recovery.test_restart_reconstructs_state_no_duplicates", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest -v test_recovery'"},
      {"type": "command", "command": "cd tests && python3 -m unittest test_beat", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest test_beat'"},
      {"type": "command", "command": "cd tests && python3 -m unittest test_checkpoint", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest test_checkpoint'"}
    ]
  },
  {
    "id": "Q3",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "vendors table (name PK, cooldown_until REAL nullable, consecutive_limits INTEGER, no max-retries column, no terminal/outage-state column)", "line": 302, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_vendor_limited (cooldown = min(base * 2**(n-1), cap) -- exponential backoff with a cap, n = consecutive_limits with no ceiling on the counter itself)", "line": 3722, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_vendor_ok (resets cooldown_until=NULL, consecutive_limits=0 on any success)", "line": 3741, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_vendor_check (--all reports all_cooling + min_wait_seconds -- exactly the timer a caller sleeps on; single-vendor check denies only that vendor, no storm)", "line": 3753, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_vendor", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest -v test_vendor'"},
      {"type": "command", "command": "python3 /tmp/rt478/probe_vendor_timer.py", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 /tmp/rt478/probe_vendor_timer.py'"}
    ]
  },
  {
    "id": "Q4",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "journald limits (SystemMaxUse=2G, MaxRetentionSec=2week) -- bounds journal growth on multi-day runs", "line": 401, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "hourly disk guard (cron.hourly, df>=85% prunes worktrees/docker/npm cache, >=93% logs CRITICAL) -- bounds disk growth from worktrees/node_modules/docker layers", "line": 410, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "reap_delete_worktrees / reap_delete_branches (ADR-0061, #433) -- bounded, condition-gated reclamation of clean, task-terminal worktrees and merged branches", "line": 4976, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-state-backup.sh", "symbol": "single-writer 30-minute state snapshot to omni/state -- bounds a dead-box state-loss window to 30 minutes, does not itself bound control.db size", "line": 1, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-watchdog.sh", "symbol": "scheduler-wedge detection (#132) -- reads the scheduler's own run history/error_code, independent of the heartbeat record, so a wedged wake-up mechanism is detected even while an earlier session's heartbeat still reads alive", "line": 264, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/decisions/ADR-0006-liveness-producer-lifetime-binding.md", "symbol": "R1 -- accepted residual risk: no off-box terminator exists; a destroyed or powered-off box, or a disabled watchdog timer, is silent indefinitely and nobody learns", "line": 391, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/decisions/ADR-0006-liveness-producer-lifetime-binding.md", "symbol": "D9 reversal trigger -- if an off-box dead-man's switch proves unbuildable, the instructed remedy is to record the gap in .omni/tech-debt.md, which is not yet done as of this audit", "line": 468, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "cd tests && python3 -m unittest test_watchdog", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest test_watchdog'"},
      {"type": "command", "command": "cd tests && python3 -m unittest test_worktree_reap", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest test_worktree_reap'"},
      {"type": "command", "command": "cd tests && python3 -m unittest test_scheduler_probe", "output": "docs/audit/SURVIVAL.capture.txt § 'python3 -m unittest test_scheduler_probe'"}
    ]
  }
]
```

---

## Q1 — `kill -9` of a worker, a lead, and the Program (each, mid-wave)

**Verdict: SOUND**, in all three sub-cases, on real reconstruction evidence rather than
narrative. This document does not re-litigate whether the acquisition side of the
concurrency primitives is safe against a *live* competing actor — that is #477
(`CONCURRENCY.md`) D13's own scope, and its one `CONFIRMED-BUG` (F-1, the identity-free
`claim release`/`slot release` verbs) is a different question from what this section
answers: not "can a live rival pre-empt me" but "when the process holding a resource is
simply gone, does the org notice and recover correctly, without losing or duplicating the
task."

**(a) A worker (`eng_claude`/`eng_codex`/`qa`/`reviewer_*`) is `kill -9`'d mid-task.** It was
holding a `worker_slots` row, a `task_claims` row, and an `agent_runs` row in state
`running`. Nothing it held carries a liveness signal of its own — recovery is entirely
TTL-driven. `expire_stale` (`bin/nightshift-ctl:1086`) runs inside the transaction of
*every* mutating command (`slot acquire`, `claim acquire`, `slot list`, `claim show`,
`dump`, …), so the very next control-plane call from anyone — not a dedicated reaper
process — expires the claim, reaps the slot, and marks the linked `agent_runs` row `dead`,
atomically, in one `UPDATE`. `reap` performs the same sweep explicitly, for when nobody
happens to call another verb first. **Reproduced end to end**
(`test_dead_worker_reaped_slot_claim_run`, `SURVIVAL.capture.txt`): a slot+claim+run is
created, the clock is advanced past both TTLs (simulating the worker vanishing), `reap`
reports exactly one slot reaped and one claim expired, `slot list` shows zero active rows,
and a fresh `slot acquire` for a *different* task succeeds immediately — the capacity is
genuinely returned, not merely reported as returned. **Nothing is lost**: the task's row in
`tasks` is untouched (state, `base_sha`, PR url all survive independently of the dead
claim/slot). **Nothing is duplicated**: `test_restart_reconstructs_state_no_duplicates`
proves a "heartbeat-after-reboot" recovery attempt against a claim that is *still live* is
flatly denied (exit 3) with a `reuse` hint naming the exact `(agent, title)` to resume
instead of re-dispatching — the recovery path cannot double-hire a task whose prior claim
has not yet expired, which is the one scenario that would actually risk two workers on one
task.

**(b) A crew lead performing a WA-5 self-build (this very document's own author) is
`kill -9`'d mid-task.** `crew_alpha`/`crew_beta`/`pm`/etc. are in `EXEMPT_AGENTS`
(`bin/nightshift-ctl:223-226`), and the live control plane confirms this is not merely a
policy exemption in name: a direct read of `task_claims` for this task and for #477's
identically-shaped WA-5 build returns **zero rows** for either (`SURVIVAL.capture.txt` §
sqlite3 read) — a self-performing lead never creates a `task_claims` row at all, because the
whole claim/slot admission machinery exists to gate a *counted* worker dispatch, and
`EXEMPT_AGENTS` bypasses that machinery outright rather than acquiring-and-holding inside
it. What the lead *does* hold is a bounded `leases` row (this task's lease 66,
`docs/audit/SURVIVAL.md`, mode `edit`) and the task's own row in `tasks`. If the lead's
process dies: the edit lease is TTL-bound (`CAP_DEFAULTS["lease_ttl_seconds"] = 86400`,
`bin/nightshift-ctl:188-201` — confirmed live: lease 66's `expires_at - acquired_at` is
exactly 86400s, `SURVIVAL.capture.txt`), so it self-clears via the same lazy-`expire_stale`
mechanism as (a) within at most 24h even with no operator action, and it is *also*
unconditionally force-droppable sooner via `lease break` (`bin/nightshift-ctl:1784`),
which — unlike `lease release` — takes no holder identity at all and works in every lease
mode, precisely because ADR-0061's own delete-eligibility logic and any operator need a way
to reclaim a resource whose holder cannot be asked. Separately, the actual work product — an
uncommitted git worktree and branch on local disk — is not owned by any control-plane row at
all and is not destroyed by the process dying: `git status`/`git worktree list` in a fresh
session sees exactly what was there, and a new lead session can resume authoring from the
same files. **Nothing is lost** beyond the dead process's own in-memory reasoning; the
lease's bounded TTL and the unconditional break path both bound recovery time, and the
worktree's on-disk state survives independent of either.

**(c) The Program itself is `kill -9`'d mid-turn.** ADR-0044 designed the `program_authority`
fence specifically for this case and made an explicit, reasoned choice (Decision §5,
`docs/decisions/ADR-0044-program-authority-fencing.md:205`): **no force-break.** An
authenticated break would require presenting the live token, and a crashed holder cannot
present anything — so an authenticated-break primitive would only ever be usable by
reconstructing the token out of band, which recreates the exact unauthenticated-break hazard
it was meant to avoid. The only recovery path is TTL expiry advancing the generation, and
the code demonstrates this is not merely stated but built-in: `cmd_authority_acquire`
(`bin/nightshift-ctl:1839`) runs `expire_stale` *before* checking for a live incumbent, in
the same transaction — so the very next Program session's own first `authority acquire`
call reaps the dead generation and mints `generation + 1` itself, with a reason string that
literally names `"prior generation N expired (TTL lapsed, no force-break — ADR-0044 §5)"`.
The bound on "how long until self-recovery" is `authority_ttl_seconds = 1800` (30 minutes,
`CAP_DEFAULTS`, `bin/nightshift-ctl:188-201`), renewed more frequently during an active turn
per the ADR's own recommendation. **Nothing is duplicated**: the dispatch-admission fencing
`worker_semaphore` performs (per #477 Q3's own evidence, not re-derived here) is keyed to the
same generation-stamped rows this section describes, so a stale Program session that somehow
resumed after its own authority expired would be dispatching under a generation number the
next `authority acquire` has already superseded, not one that could silently coexist with a
new Program's dispatches. `program_authority` was empty at audit time (`SURVIVAL.capture.txt`
§ "Live control-plane facts"), consistent with no Program turn holding the fence at the
moment this snapshot was read — not itself evidence of correctness, only stated for
completeness/WA-009 (there is no "control" cell to show for an invariant about an *absent*
row; the control cell for this claim is the `cmd_authority_acquire` code path and the
generation-advance behaviour it implements, both cited above).

## Q2 — after a reboot, does the first heartbeat reconcile without re-dispatching in-flight work?

**Verdict: SOUND.** The mechanism this depends on is that every fact needed to answer "is
this task already being worked" lives in `.omni/control.db`, a file on the box's own
persistent disk, not in any process's memory — so a reboot destroys every in-memory Program
loop and every agent session, but destroys zero control-plane rows. The first heartbeat after
a reboot reads the same file the pre-reboot Program was writing, and two independent
mechanisms stop it from re-dispatching:

1. **The heartbeat's own liveness check refuses to be fooled by survival.** `cmd_beat_show`
   (`bin/nightshift-ctl:3627`) rejects a heartbeat record whose `boot_id` does not match the
   live boot (`wrong_boot`, line 3659) — so a heartbeat record that *itself* survived the
   reboot (the row is just a SQLite value; it is not destroyed by rebooting) reads correctly
   as **NOT ALIVE**, "the record survived, the Program did not." This is what makes "the
   first heartbeat" a genuinely fresh determination rather than an accidental continuation of
   stale state.
2. **Task readiness/continuation admission is a database predicate, not a memory of what was
   dispatched.** A task is only continuation-eligible from `building`/`ready`/`fixing`
   (`bin/nightshift-ctl:1267`); a `NEW`-phase dispatch is refused outright while an active
   claim or an active `phase='new'` slot already exists for that task
   (`_exact_new_retry`/`_exact_claim_slot_retry`, evidence already established in #477's Q1/Q3
   and not re-derived here). Concretely, **reproduced end to end**
   (`test_restart_reconstructs_state_no_duplicates`, `SURVIVAL.capture.txt`): a slot, an
   active claim (`claimant=lead-alpha`), and a `tasks` row (`state=building`,
   `base_sha=abc123`) are seeded to represent an in-flight wave; every subsequent CLI
   invocation is a **fresh process** reading the same db (the test's own stand-in for "the
   box restarted"); a `claim acquire --claimant heartbeat-after-reboot` — exactly the shape a
   post-reboot heartbeat's own recovery-claim-first move would take — is **denied** (exit 3,
   `reuse: {"agent": "eng_codex", "title": "add-export"}`), and a `dump` immediately after
   shows the slot and claim counts unchanged (still exactly one of each) and the task's
   `base_sha`/`state` intact. The heartbeat is told to *resume* the named `(agent, title)`,
   never to *duplicate* it.

**State → owner invariant table**, by lifecycle state, compared by the stable `tasks.id`
(a `TEXT PRIMARY KEY` that never changes across recovery) and the per-task `generation`
counter (`MAX(generation)+1` on both `task_claims` and `worker_slots`, so a resumed/retried
dispatch for the same task always carries a strictly higher generation than what it
supersedes):

| Lifecycle state | Where it lives | Owner recorded | Survives a reboot? | Re-dispatch risk |
|---|---|---|---|---|
| **queued** | `tasks.state = 'pending'` (schema default, `bin/nightshift-ctl:238`) | none — no active claim/slot | Yes (plain row) | None: nothing to duplicate until admitted |
| **in-flight** | `tasks.state IN ('building','ready','fixing')` + an active `worker_slots` (phase `new`/`continuation`) and/or active `task_claims` row | the `(holder, expected_agent, expected_title)` on the slot row / `claimant` on the claim row, both generation-stamped | Yes — rows are on disk; only the *heartbeat asking about them* is boot-checked, not the rows themselves | None: `_exact_*_retry` reuses an identical retry, denies a competing one, per generation |
| **recoverable** | `task_claims.state = 'expired'` (TTL lapsed) with `tasks.state` still `building`/`ready`/`fixing` | none live — the expired row's `claimant` field is retained for audit, not as an active owner | Yes | None: a fresh `claim acquire` mints a strictly higher `generation`; the expired row is never reused as if still active |
| **dropped** | `tasks.state = 'dropped'` (member of `TERMINAL_TASK_STATES`, `bin/nightshift-ctl:4393`) | none — terminal | Yes | None: terminal states are excluded from continuation eligibility (line 1267) and from `reap --delete`'s live-task check |
| **integrated** | `tasks.state = 'integrated'` (in the wider closable set, `bin/nightshift-ctl:2186`, but deliberately *not* in `TERMINAL_TASK_STATES` since #433/ADR-0061 treats it as the one terminal state that is also delete-eligible) | none — closed out | Yes | None: `_worktree_delete_eligible` and the continuation gate both already exclude it |

## Q3 — rate limit versus hard outage

**Verdict: SOUND**, with a limitation this section is required to record plainly, per the
DoD's own condition-14 text, and does.

**State transitions and persisted identity.** The `vendors` table
(`bin/nightshift-ctl:302-305`) keyed by vendor name persists exactly two facts:
`cooldown_until` (a REAL epoch timestamp, nullable) and `consecutive_limits` (an integer
counter). `vendor limited` (`:3722`) computes `cooldown = min(base * 2**(n-1), cap)` where
`n` is `consecutive_limits + 1` and writes both columns in one `INSERT ... ON CONFLICT ...
UPDATE`; `vendor ok` (`:3741`) resets both to `NULL`/`0` on any success; `vendor check`
(`:3753`) compares `cooldown_until` against `now()` and, for `--all`, reports
`all_cooling` plus `min_wait_seconds` — literally the timer a caller sleeps on before
retrying. **Persisted-row evidence**: `test_backoff_doubles_and_caps` confirms the doubling
sequence 1800 → 3600 → 7200 → …capped at 14400; `test_single_vendor_cooling_other_available`
confirms one vendor cooling does not block the other (`vendor check --vendor claude` denies,
`--vendor codex` grants — "work moves here, no storm"); `test_both_limited_yields_timer`
confirms `--all` reports the *minimum* remaining wait across vendors, not a sum or a max.
**Resume attempt across the timer boundary, on a fixture, using persisted rows** (the DoD's
own required evidence shape): `probe_vendor_timer.py` (`SURVIVAL.capture.txt`) drives the
real CLI through the `tests/ctlutil.Ctl` virtual-clock harness — `vendor limited` records
`cooldown_until`; `vendor check` one second *before* that timestamp still denies (exit 3,
`remaining: 1.0`); advancing the clock two seconds and checking again **grants** (exit 0,
`cooling: false`) — the timer, not any process's memory of having slept, is what gates the
retry, which is exactly what "the persisted timer/cooldown identity" in the DoD's own
wording asks to be shown.

**Maximum retries and terminal state — the recorded limitation.** There is no maximum-retry
column, no retry counter that is ever compared against a ceiling, and no state a vendor row
can reach that stops it from being checked and retried again. `probe_vendor_timer.py`
demonstrates this directly: six additional consecutive `vendor limited` calls after a
recovery leave `consecutive_limits` at 7 with `cooldown_seconds` sitting at the 14400s
(4-hour) cap — the counter keeps incrementing forever, the wait time does not grow past the
cap, and nothing transitions the row to a state meaning "give up" or "this is not transient,
escalate to a human." **`nightshift-ctl vendor` cannot currently express a non-expiring
outage.** This is a factual, currently-true statement about the shipped mechanism, not a new
defect: the code does precisely what its own tests specify (retry forever with a bounded
backoff), and unbounded-retry-with-a-cap is a defensible design for genuinely transient rate
limits — it simply has no distinct encoding for "this vendor is never coming back within any
horizon a human should wait for," which is a different failure class than a rate limit. This
matches, rather than newly discovers, the framing already carried in
`docs/prd/DEFINITION-OF-DONE.md`'s brief item B5.

**Live-vendor behaviour, stated plainly as required.** No real dispatched worker turn against
either vendor's live API happened during this audit or, per this session's own WA-5
dispatch-substitution pattern (`#489`), during today's session generally — every
`eng_claude`/`eng_codex`/`qa`/`reviewer_*` dispatch this session has hit the same
schema-validation wall before ever reaching a live model call, for *both* vendors equally.
So: live-vendor rate-limit/outage behaviour is unobserved today, and this document states
that plainly, as required — but for a more current and more precise reason than brief item
B5's literal text. B5 (`docs/prd/DEFINITION-OF-DONE.md:77`) names the *specific* 2026-08-22
cause as "every Codex seat is down" via a `bwrap`/user-namespace sandbox failure (issue #5);
`.omni/blocked.md` records that specific defect **RESOLVED** as of 2026-08-23. Repeating B5's
literal wording unqualified would therefore overstate a since-fixed cause. The practical
conclusion B5 exists to protect — *this audit did not observe live vendor behaviour* — still
holds today, just via #489's universal dispatch-schema wall rather than #5's Codex-specific
sandbox fault. All evidence above is fixture/unit-test evidence on the persisted control-plane
rows, exactly as #478's own "Not included" scope permits ("Does not require reproducing a
live 3–7 day run against real vendor infrastructure").

## Q4 — over a 3–7 day run: monotonic growth, its bounds, and single points of silence

**Verdict: SOUND**, meaning: every growth surface this audit could find either has a
mechanism that bounds it, or is already the subject of an existing, named, accepted
architectural gap rather than an undiscovered one.

**What grows, and what bounds it:**

| Grows | Bound | Mechanism |
|---|---|---|
| journald | 2 GB / 2 weeks | `/etc/systemd/journald.conf.d/omnigent.conf` (`SystemMaxUse=2G`, `MaxRetentionSec=2week`), written by `deploy/provision-ubuntu.sh:401-408` |
| Disk (worktrees, `node_modules`, docker layers, npm cache) | Reactive: prune at 85% full, log CRITICAL at 93% | Hourly cron `/etc/cron.hourly/omnigent-disk-guard`, `deploy/provision-ubuntu.sh:410-426` — `git worktree prune`, `docker system prune -f`, delete npm cache entries older than 3 days |
| Worktrees (task-specific, tracked by the control plane) | Condition-gated reclamation (AC-1(a)-(e): never the primary worktree, never a live task, never an active claim/lease, never dirty, never unreadable git state) | `reap_delete_worktrees`/`_worktree_delete_eligible`, `bin/nightshift-ctl:4935-5006` (ADR-0061/#433); re-verified green this turn: `test_worktree_reap`, 57/57 |
| Branches | Same ADR-0061 mechanism, branch-side conditions (never `main`, never `origin/HEAD`, never unmerged, never correlated to a live task) | `reap_delete_branches`, `bin/nightshift-ctl:5094` |
| `omni/state` history (the off-box git-branch snapshot of `.omni/` + `docs/`) | Bounded to 30-minute loss windows, not itself size-bounded (each snapshot is a new commit on `omni/state`; the branch itself grows without an explicit prune) | `deploy/nightshift-state-backup.sh` — single writer, always fast-forwards, "a dead machine costs at most 30 minutes of state" |
| `control.db` itself | **Unverified** — no `VACUUM`, no size cap, and no scheduled compaction were found anywhere in `bin/nightshift-ctl` or `deploy/*.sh`. WAL journal mode (`bin/nightshift-ctl:236`) bounds *transaction* memory, not file size. | None found — named here as an open question, not asserted safe |
| Agent transcripts | **Unverified — needs host file access outside this repo's checkout** (transcripts are a runtime artifact of the Omnigent host/server, not tracked in this git tree); `docs/architecture/PROPOSAL-codex.md:468,482` already names this exact gap ("transcript growth… unverified — needs file access") | None found in-repo |

**Single points of silence, and which are now detected:**

| Silent failure | Detected? | Mechanism |
|---|---|---|
| Program heartbeat lost/stale/wrong-boot/wrong-run | **Yes** | `deploy/nightshift-watchdog.sh` reads `beat show --who program` **by content**, never by file mtime (the #10 defect this replaced); 101/101 `test_watchdog` green this turn, including the RT08 mtime-independence and RT27/RT28 fresh-install-vs-restored-plane distinctions |
| Scheduler wedged (the hourly wake-up mechanism itself failing, even while an old session's heartbeat still reads alive) | **Yes, independently** | The same watchdog run also asks the scheduler's own run history for a stated `error_code` (`deploy/nightshift-watchdog.sh:264`, issue #132) — explicitly does **not** get suppressed by a live beat, because "the beat said ALIVE, truthfully, and the mechanism that would wake the org after that session ended was already broken" (script's own comment); `test_scheduler_probe`, 33/33 green |
| A worker/session dying silently | **Bounded, not alerted.** Capacity is reclaimed within `slot_ttl_seconds`/`claim_ttl_seconds` (4h/1h, `CAP_DEFAULTS`) by the same lazy `expire_stale` sweep as Q1(a) — so it never wedges the org's *capacity* — but no mechanism raises an operator-facing alert the way the Program watchdog does for the Program itself. This is a real asymmetry, not a defect: task-level attention is a property of the Program's own recurring per-heartbeat triage (which task registry rows read `state: building` for how long), not of a dedicated per-worker watchdog. |
| Watchdog timer itself disabled, or the box powered off entirely | **No — a named, already-accepted gap, not a new finding.** ADR-0006's own Decision record states this explicitly as **R1**: *"there is no terminator today, and D9 does not build one… A destroyed or powered-off box is silent indefinitely and nobody learns"* (`docs/decisions/ADR-0006-liveness-producer-lifetime-binding.md:391-397`) — every control in the system as designed is "fail-open" (something on the box must act to raise an alert), and the regress only terminates at an off-box, fail-closed, negative-acknowledgement receiver, which D9 explicitly defers building. The ADR's own reversal-trigger text for D9 (line 468) instructs that if this proves unbuildable, "the honest response is to record that the system has no terminator in `.omni/tech-debt.md`" — a check of that file at audit time (`grep -i "D9\|off-box\|terminator" .omni/tech-debt.md`) found **no matching row**, so that instruction does not yet appear to have been carried out. This audit does not action that gap itself (no `.omni/tech-debt.md` edit lease was held for this task, and the fix belongs to whoever owns that file), but names it plainly here as the DoD's Q4 asks. |

**Note on scope:** none of the above required, or attempted, an actual 3–7 day continuous
run against live infrastructure, matching #478's own explicit "Not included" scope
("Does not require reproducing a live 3–7 day run… a fixture/simulated timer boundary is
sufficient evidence"). Every bound and every gap named above is read from the shipped
mechanism itself (config files, cron/systemd units, control-plane schema) plus its
fresh-run test suite, not from an extrapolated narrative.
