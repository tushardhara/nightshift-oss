# Concurrency audit — Q1–Q5 (DoD condition 13, issue #477)

Date: 2026-09-01 · Crew: crew_alpha · Issues: #477 (this doc), #423/#475 (parent split),
#482 (FINDINGS.md, not yet created — see F-1 below) · Base/audit SHA:
`f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b` (this audit made no code changes; every
permalink below is against this exact commit, and the full suite was run against it
unmodified).

Raw capture: [`CONCURRENCY.capture.txt`](CONCURRENCY.capture.txt) — every command cited
below as `"type": "command"` evidence appears there verbatim, with its exact invocation,
full stdout, and (for the two ad hoc reproduction scripts) full source. Every
`"type": "test"`-shaped claim below is instead cited as a `"command"` record that runs
the named `python3 -m unittest` node directly — `docs/prd/DOD-MANIFEST.json` is
`product`-owned (`docs/owners.md`: single-writer, "the definition of done is revised
through its changelog, never edited silently"), so this crew-authored audit does not
mint new `RT-nnn` ids into it. Existing ids (`RT-16`–`RT-19`) already sit unsatisfied
(`test_node_id: null`) for closely-related concurrency claims; §"RT id follow-up" below
names the exact tests that would satisfy them, for `product` to bind if it chooses.

## WA-5 disclosure

This crew's dispatch to a counted worker on this exact task was denied twice
(`qa` slot 65, purpose `explore`; `eng_claude` slot 66, purpose `implement`/`propose`),
reproducing the live deployment-lag defect tracked at #489 (a served `nightshift-ctl`/
policy snapshot substantially behind `origin/main`) compounded by #490 (the one
documented refresh mechanism, `nightshift-release prepare`, cannot re-run on this host
because the legacy supervision units it requires are masked-by-design after the prior
successful cutover, per `EXACT-SHA-CUTOVER.md`, and unmasking does not restore them —
`LoadState` goes to `not-found`, not `loaded`). Per **WA-5** (`.omni/working-agreements.md`
item 5, issue #13) and its owner-directed invocation for this item, the crew_alpha lead
built this document directly, single-author, under the control-plane hold recorded for
this task (lease 53, `docs/audit/CONCURRENCY.md`, edit mode, note "WA-5 build
(owner-directed, issues #489/#490 cause), crew lead builds under held slot" — `crew_alpha`
is itself in `EXEMPT_AGENTS`, so no counted-worker `slot acquire` applies to the lead's own
build, matching the standing precedent recorded for #217/#286/#381/#392/etc. in
`.omni/ledger.md`/`.omni/decisions.md`). In-crew review and in-crew QA did not run.
**Cross-crew review (crew_beta) is therefore mandatory, not customary, for this PR.**

Scope, per #477's own boundary: audit and cite evidence only. No code changes, including
to `worker_semaphore`. Any `CONFIRMED-BUG` below is reported, not remediated.

## Summary

| Q | Verdict | Findings |
|---|---|---|
| Q1 — can two workers execute the same task without an authorized recovery transition? | **CONFIRMED-BUG** | F-1 |
| Q2 — does every dispatch path consume the one global budget? | SOUND | — |
| Q3 — acquisition-order/release-trigger table + bounded adversarial test, for every ≥2-of-5 code path | **CONFIRMED-BUG** | F-1 |
| Q4 — authorized recovery transition per hold type when its holder dies | SOUND | — |
| Q5 — does `worker_semaphore` fail closed on every dispatch path, including re-dispatch after denial? | SOUND | — |

```json
[
  {
    "id": "Q1",
    "verdict": "CONFIRMED-BUG",
    "findings": ["F-1"],
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_claim_release", "line": 1669, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "argparse claim release (--task only, no --claimant/--holder field exists)", "line": 5723, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_slot_release", "line": 1485, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "argparse slot release (--holder/--task optional; --slot alone needs no identity)", "line": 5681, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_slot_heartbeat + argparse slot heartbeat (--holder required — the org's own correct control, contrast case)", "line": 1508, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "command", "command": "python3 /tmp/rt37/probe.py", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 /tmp/rt37/probe.py  (Q1: unauthenticated'"},
      {"type": "command", "command": "python3 /tmp/rt37/probe2.py", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 /tmp/rt37/probe2.py  (Q1/Q3 F-1'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_recovery", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_recovery'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_claims", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_claims'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_stress", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_stress'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_admission_gate", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_admission_gate'"}
    ]
  },
  {
    "id": "Q2",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_slot_acquire (single COUNT(*) over worker_slots, unfiltered by crew, checked against .omni/capacity.json before any INSERT)", "line": 1397, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "deploy/policies/omnipods_policies.py", "symbol": "worker_semaphore (EXEMPT_AGENTS bypass by design; ONCALL_AGENTS share the same worker_slots table/total cap, carved by reserve_for_oncall)", "line": 178, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "deploy/policies/omnipods_policies.py", "symbol": "program_authority_fence (the separate, single mechanism that orders EXEMPT_AGENTS/Program-direct-child dispatch instead of the worker budget)", "line": 483, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_slots.GlobalSemaphore.test_concurrent_acquires_respect_budget_and_reserve test_slots.GlobalSemaphore.test_oncall_reserve_usable_only_by_oncall", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_slots'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_stress.Stress.test_concurrent_pods_with_crashes", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_stress'"}
    ]
  },
  {
    "id": "Q3",
    "verdict": "CONFIRMED-BUG",
    "findings": ["F-1"],
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_seq_next (LEASE read+holder-check, then SEQUENCE read-increment-write, single BEGIN IMMEDIATE transaction)", "line": 2019, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_claim_acquire / _derive_normal_phase continuation branch (CLAIM read to gate a continuation SLOT acquisition)", "line": 1306, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "deploy/policies/omnipods_policies.py", "symbol": "worker_semaphore continuation_kind==\"implementer\" branch (re-validates SLOT+CLAIM together, atomically, a second time at actual dispatch)", "line": 386, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_leases.Leases.test_sequence_atomic_and_lease_gated", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_leases'"},
      {"type": "command", "command": "python3 /tmp/rt37/probe2.py", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 /tmp/rt37/probe2.py  (Q1/Q3 F-1'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_admission_gate.RootDispatchPolicy.test_named_alpha_and_beta_dispatches_are_exactly_one_use test_admission_gate.RootDispatchPolicy.test_stale_generation_and_expired_slot_deny_without_consumption", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_admission_gate'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_recovery.Recovery.test_dead_worker_reaped_slot_claim_run test_recovery.Recovery.test_restart_reconstructs_state_no_duplicates", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_recovery'"}
    ]
  },
  {
    "id": "Q4",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "expire_stale (claims->'expired', slots->'reaped'+linked agent_runs->'dead', leases WHERE mode='edit' only ->'expired', program_authority->'expired'; SEQUENCE/CHECKPOINT rows are absent from this function entirely)", "line": 1086, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_lease_acquire (mode==\"serialize\": expires=None, \"never TTL'd away\", comment inline)", "line": 1727, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_lease_release (serialize mode refuses any event but pr-merged/dropped) and cmd_lease_break (explicit force-drop, any mode)", "line": 1758, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_leases.Leases.test_serialize_lifecycle test_leases.Leases.test_break_frees_stale_serialize_holder", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_leases'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_recovery.Recovery.test_dead_worker_reaped_slot_claim_run", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_recovery'"}
    ]
  },
  {
    "id": "Q5",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "deploy/policies/omnipods_policies.py", "symbol": "worker_semaphore (module docstring states fail-closed intent; outer try/except sqlite3.Error and the missing-db branch both DENY unless NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1)", "line": 178, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "consume_slot (CAS: dispatched_at IS NULL guard makes a second dispatch on the same slot_id change zero rows)", "line": 919, "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_policy.WorkerSemaphorePolicy", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_policy.WorkerSemaphorePolicy'"},
      {"type": "command", "command": "cd tests && python3 -m unittest -v test_admission_gate.RootDispatchPolicy.test_named_alpha_and_beta_dispatches_are_exactly_one_use test_admission_gate.RootDispatchPolicy.test_opaque_create_unknown_and_malformed_dispatches_deny test_admission_gate.RootDispatchPolicy.test_stale_generation_and_expired_slot_deny_without_consumption", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 -m unittest -v test_admission_gate'"},
      {"type": "command", "command": "python3 /tmp/rt36/probe.py", "output": "docs/audit/CONCURRENCY.capture.txt § 'python3 /tmp/rt36/probe.py'"}
    ]
  }
]
```

---

## Q1 — can two workers execute the same task without an authorized recovery transition?

**Verdict: CONFIRMED-BUG (F-1).**

The *acquisition* side is sound: a second `claim acquire` against a task whose claim is
still live is denied at the database layer (`task_claims` raises `sqlite3.IntegrityError`
on the active-claim uniqueness the schema enforces; `cmd_claim_acquire` turns that into
`"task already claimed — stand down; the claimant owns recovery"`), and this holds even
across a simulated process restart (`test_restart_reconstructs_state_no_duplicates`: a
`--claimant heartbeat-after-reboot` retry against a still-live claim is denied and told to
reuse the original `agent`/`title`). Likewise a competing NEW-phase slot reservation for
the same task under a different holder/crew/agent/title/purpose is denied
(`_exact_new_retry`), and an identical repeat is idempotently reused, never double-counted.

**The *release* side is not.** `claim release --task <id>` has **no identity parameter in
its argument parser at all** (`bin/nightshift-ctl:5723-5725` — only `--task` is defined),
and `cmd_claim_release` (`:1669`) executes
`UPDATE task_claims SET state='released' ... WHERE task_id=? AND state='active'` with no
check of who is asking. `slot release --slot <id>` (`:5681-5684`, `cmd_slot_release`
`:1485`) is the same shape: `--holder`/`--task` are optional, and releasing by `--slot`
alone requires nothing. Contrast `slot heartbeat` (`:5686-5691`, `cmd_slot_heartbeat`
`:1508`), where `--holder` is `required=True` specifically because, in this file's own
words, *"renewal is a capability of the HOLDER... an optional identity argument is a check
that defaults to off"* (ADR-0002 §4a). The release verbs are exactly that defaulted-off
check.

**Reproduced end-to-end** (`docs/audit/CONCURRENCY.capture.txt`, `probe2.py`): a real
worker (`eng_claude`) legitimately claims task `T1`, acquires a continuation slot bound to
that claim, and is dispatched — `worker_semaphore` returns `ALLOW` and the slot's
`dispatched_at` is set (session 1 is now live, mid-flight, with a 3600s claim TTL nowhere
near expiry). A second, wholly unrelated caller then runs `claim release --task T1` with
zero identity proof; it succeeds (`released: 1`). A second session (`crew_alpha/lead-2`)
now claims the same task/agent/title, acquires its *own* continuation slot, and is *also*
dispatched — `worker_semaphore` again returns `ALLOW`. The database is left with **two**
`worker_slots` rows for the identical `(task_id="T1", agent="eng_claude", title="t")` with
`dispatched_at IS NOT NULL` simultaneously — two live, fully-authorized dispatches for the
same task, with nothing that told session 1 to stop and nothing that told session 2 it was
racing an incumbent. (Note: `cmd_claim_acquire` does pin a claim to the *exact* admitted
`agent`/`title` — `args.agent != admission["agent"]` denies — so this specific race cannot
hand the task to a genuinely *different* agent identity; it can and does hand it to a
second, duplicate *session* of the same nominal identity, which is the shape this org's
own "claim-first" recovery protocol exists to prevent.)

**F-1** (to be created with a disposition in `docs/audit/FINDINGS.md` once #482 lands —
that file does not exist yet in this tree; #477's own dependency note already sequences
#482 to land after or alongside #477–#481 for exactly this reason):

> **F-1 — `claim release`, `slot release`, and `lease release` (without `--holder`) perform
> a live-hold-freeing state transition with no identity check, letting a live claim/slot be
> pre-empted by any caller while its holder is still alive and its TTL has not lapsed.**
> Mechanism: `bin/nightshift-ctl` `cmd_claim_release`/`cmd_slot_release`/`cmd_lease_release`
> (`:1669`, `:1485`, `:1758`) and their argparse definitions (`:5723`, `:5681`, `:5743`).
> Repro: `docs/audit/CONCURRENCY.capture.txt` (`probe.py`, `probe2.py`). Impact: defeats the
> claim-first recovery protocol's own safety property (§Q1 above) for the *live-holder*
> case; TTL-based recovery for a *dead* holder (§Q4) is unaffected, since that path never
> calls the unauthenticated release verbs. Suggested remediation shape (not applied here,
> per #477's scope boundary): make `--holder`/`--claimant` required on all three release
> verbs and check it unconditionally, the same way `slot heartbeat` already requires and
> checks `--holder` (ADR-0002 §4a) — but the actual fix, severity call, and disposition
> belong to whoever picks up #482/a follow-up fix task, not to this audit.

## Q2 — does every dispatch path consume the one global budget?

**Verdict: SOUND.**

Every `COUNTED_AGENTS` dispatch path (`implement`/`explore`/`search`/`review` purposes,
both crews' `eng_claude`/`eng_codex`/`qa`/`reviewer_claude`/`reviewer_codex` and their
`crew_beta` equivalents) is required to present a `slot_id` that was granted by
`cmd_slot_acquire` (`bin/nightshift-ctl:1397`), and that function's admission check is a
single `SELECT kind, holder, crew, task_id, phase FROM worker_slots WHERE state='active'`
with **no crew filter** — the count it derives (`total`, `normal`) spans every crew against
one shared `.omni/capacity.json` (`max_concurrent_workers`, `reserve_for_oncall`). There is
exactly one such counting query in the codebase (`grep -n "COUNT\|FROM worker_slots WHERE state='active'"`
finds this one gate for acquisition; no second, per-crew or per-purpose accounting path
exists). `worker_semaphore` itself does not re-derive the budget at dispatch time — it only
re-validates the identity/liveness/exactness of the one already-budgeted slot referenced by
the call — so there is no route to a counted dispatch that bypasses the single acquisition
gate.

Two categories sit outside this specific budget **by explicit design**, not by omission:
`EXEMPT_AGENTS` (crew leads and other Program-direct-child seats — `crew_alpha`,
`crew_beta`, `product`, `staff`, `ux`, `platform`, `security`, `release`, `pm`, `beta_pm`)
return `_ALLOW` immediately in `worker_semaphore` without touching `worker_slots` at all;
their dispatch ordering is instead fenced by the separate, single `program_authority_fence`
mechanism (ADR-0044 — one active Program-authority generation, one consumable permit per
exact call). `ONCALL_AGENTS` dispatches **do** acquire a real `worker_slots` row
(`kind='oncall'`) and **do** count against the same shared `total < max_concurrent_workers`
ceiling, just carved out of competing with `normal` slots via `reserve_for_oncall` — so the
reserve is still inside the one global pool, not a second budget.

`test_stress.Stress.test_concurrent_pods_with_crashes` is direct, adversarial evidence for
the "global, not per-crew" claim: it runs 6 simulated crews × 4 tasks concurrently
(alternating `crew_alpha`/`crew_beta` identities) against a shared `capacity.json`
(`max_concurrent_workers=4`, `reserve_for_oncall=1`) with an independent sampler thread
reading raw `worker_slots` rows every 10ms; the test fails if the sampled total ever
exceeds 4 or the sampled `normal` count ever exceeds 3, across every crew combined — it
passed (`docs/audit/CONCURRENCY.capture.txt`).

## Q3 — acquisition-order/release-trigger table (≥2 of {slot, claim, lease, sequence, checkpoint})

**Verdict: CONFIRMED-BUG (F-1)** — the ordering/atomicity properties below are sound; the
release-trigger column for the SLOT+CLAIM path is not authenticated (same F-1 as Q1).

**Enumeration.** Grepping every `FROM`/`INSERT INTO` against `leases`, `worker_slots`,
`task_claims`, `sequences`, and `checkpoints` across `bin/nightshift-ctl` and
`deploy/policies/omnipods_policies.py` finds exactly two code paths where a single logical
operation holds/reads ≥2 of the five mechanisms. `checkpoints` never co-occurs with any of
the other four in any function — a checkpoint's `sha` is only ever consumed later, as an
input string (a task's `base_sha`), by code that touches none of `worker_slots`,
`task_claims`, `leases`, or `sequences`; it participates in the wave-startup workflow as
data, never as a concurrently-held resource. (`cmd_dump` and `cmd_run_current`'s "is
anything classified live" pre-check touch multiple tables too, but the former is a
read-only diagnostic snapshot inside one transaction, not an acquisition, and the latter
(`worker_slots` UNION `task_claims`, `:3601`) is a read-only guard against changing
`current_run_id` while classified work is active — neither holds anything across more than
one transaction, so neither has an acquisition-order hazard to enumerate.)

| Path | Resources held | Acquisition order | Release trigger(s) | Enumerated adversarial orders (bounded, exhaustive for this path) | Test evidence |
|---|---|---|---|---|---|
| **P1** `cmd_seq_next` (`bin/nightshift-ctl:2019`) | LEASE (read+holder-check) → SEQUENCE (read-increment-write) | Single `BEGIN IMMEDIATE` transaction: LEASE holder-check happens first and unconditionally gates whether the SEQUENCE statements execute at all — there is no window where the sequence table is touched without the lease check having already passed inside the same transaction | LEASE: `lease release`/`lease break` (see P2 row + Q4) or (edit-mode only) TTL. SEQUENCE: never released — it is a permanent monotonic counter; each `seq next` call is atomic and self-contained and holds nothing after it returns | (a) `seq next` with **no** lease acquired yet → DENY, exit 3; (b) lease acquired by X, then **16 concurrent** `seq next` calls by X → all granted, unique, gapless (`1..16`); (c) `seq next` by Y while X holds the lease → DENY, exit 3, non-holder never allocates | `test_leases.Leases.test_sequence_atomic_and_lease_gated` — all three orders in one test, all pass |
| **P2** SLOT (`phase='continuation'`, `continuation_kind='implementer'`) + CLAIM, spanning `cmd_claim_acquire` (`:1590`) → `_derive_normal_phase` continuation branch (`:1306`, validates `args.claim_id`/`claim_generation`/`claim_run_id` against the live claim row before granting the slot) → `worker_semaphore`'s `continuation_kind=="implementer"` branch (`omnipods_policies.py:386`, re-reads **both** rows together a second time, in a separate later transaction, at actual dispatch) | CLAIM must be `state='active'` before a continuation SLOT referencing it can be acquired; SLOT then carries `claim_id`/`generation`/`run_id` as a fixed reference; at dispatch, both rows are re-read and cross-checked atomically (`claim["generation"] == row["generation"]`, `claim["run_id"] == row["run_id"]`, `claim["state"] == "active"`, `claim["acquired_at"] >= admission["admitted_at"]`, `claim["expires_at"] is None or > now`) before the slot's one-time `dispatched_at` CAS fires | CLAIM: **explicit `claim release` — UNAUTHENTICATED, see F-1** — or TTL expiry (`expire_stale`, `task_claims`) or supersession by a strictly-monotonic newer `generation` on the same task. SLOT: explicit `slot release` — **also UNAUTHENTICATED when called by `--slot` id alone, see F-1** — or TTL expiry (`expire_stale`, `worker_slots`, which also marks the linked `agent_runs` row `dead`) or one-time consumption (CAS) | (i) claim active, slot references it exactly, unexpired, generation matches → dispatch ALLOW exactly once; an identical replay of the same event → DENY ("already used"); (ii) claim released/expired between slot-acquire and dispatch → dispatch DENY, slot **not** consumed (verified: `rows(...)[0] == before`); (iii) a second claim attempt on the same task while the first is active (including after a simulated process restart) → DENY, "stand down"; (iv) original claimant crashes without releasing → both claim and slot TTL-expire independently and one `reap` frees both together, returning capacity, before any recovery dispatch is authorized; **(v) — the confirmed defect — an unrelated, unauthenticated `claim release` while the original session is still live and undispatched-of → the "someone is recovering this" signal is wiped, a second session claims + acquires + dispatches under the identical task/agent/title, and BOTH sessions end up with `dispatched_at` set simultaneously** | (i)/(ii) `test_admission_gate.RootDispatchPolicy.test_named_alpha_and_beta_dispatches_are_exactly_one_use`, `test_stale_generation_and_expired_slot_deny_without_consumption`; (iii) `test_recovery.Recovery.test_restart_reconstructs_state_no_duplicates`, `test_claims.ClaimRaces.test_concurrent_claims_one_winner`; (iv) `test_recovery.Recovery.test_dead_worker_reaped_slot_claim_run`, `test_stress.Stress.test_concurrent_pods_with_crashes`; (v) `docs/audit/CONCURRENCY.capture.txt` `probe2.py` (new reproduction, this audit — no prior regression test covers it, which is itself part of F-1's disposition) |

Order (i)–(iv) are exhaustive for the *authenticated* protocol and are all SOUND — no
interleaving of them produces a double-dispatch, because the two-transaction gap between
"slot acquired" and "slot consumed" is closed by `worker_semaphore` re-validating every
fact fresh at consume time rather than trusting the earlier `cmd_slot_acquire` check. Order
(v) is not a gap in that re-validation logic — every fact `worker_semaphore` checks was
genuinely true for session 2 — it is a gap in *what counts as evidence that recovery is
authorized* one layer up, at the release verb. Same finding as Q1, filed once as **F-1**.

## Q4 — the authorized recovery transition per hold type, when its holder dies

**Verdict: SOUND** (scoped to death/crash recovery specifically — see the F-1
cross-reference at the end of this section for the orthogonal live-holder gap).

| Hold type | TTL auto-release on death? | Mechanism | Explicit alternative |
|---|---|---|---|
| SLOT | **Yes** | `expire_stale`: `worker_slots` rows past `expires_at` → `state='reaped'`; any `agent_runs` row `state='running'` referencing that `slot_id` → `state='dead'` in the same statement group | `slot release` |
| CLAIM | **Yes** | `expire_stale`: `task_claims` rows past `expires_at` → `state='expired'` | `claim release` |
| LEASE, `mode='edit'` | **Yes** | `expire_stale`'s lease `UPDATE` is filtered `AND mode='edit'` | `lease release --event pr-opened\|pr-merged\|dropped\|manual` |
| LEASE, `mode='serialize'` | **No — by design.** `cmd_lease_acquire`: `if args.mode == "serialize": expires = None`, with the comment "held until PR merged/dropped, never TTL'd away." `expire_stale`'s filter (`mode='edit'`) structurally excludes every serialize row; there is no code path that ever sets `expires_at` on one | `lease release --event pr-merged` or `--event dropped` only (`pr-opened` is explicitly refused for serialize — verified: exit 3, "serialize lease releases only on pr-merged or dropped"); or the **explicit break**: `lease break --resource <r> --reason <text>`, which force-drops any active lease regardless of mode, is how a genuinely dead serialize holder (a dropped task, per the system prompt's own "three-loop" rule) is recovered |
| PROGRAM_AUTHORITY | **Yes** | `expire_stale`: `program_authority` rows past `expires_at` → `state='expired'`, freeing the next `authority acquire` to advance the generation. Comment in code: "no force-break exists on purpose (Decision §5)" | none — by design, per ADR-0044 |
| SEQUENCE | N/A — not a "hold" | A sequence value, once allocated, is never revoked; there is nothing to recover | N/A |
| CHECKPOINT | N/A — not a "hold" | Append-only record of a verified `sha`; nothing to recover | N/A |

Evidence: `test_leases.Leases.test_serialize_lifecycle` proves the `pr-opened` refusal and
the `pr-merged` success in one run; `test_leases.Leases.test_break_frees_stale_serialize_holder`
proves the explicit-break recovery path for a stale serialize holder;
`test_recovery.Recovery.test_dead_worker_reaped_slot_claim_run` proves the SLOT+CLAIM TTL
double-recovery (both freed together by one `reap`, capacity genuinely returned — a
follow-up `acquire_normal` on a fresh task succeeds immediately after).

**Cross-reference, for completeness, not because it belongs to this question's own
scope:** Q4 asks specifically about recovery *when the holder dies*, and every row above is
sound for that case. **F-1** (Q1/Q3) shows a claim/slot/edit-lease can also be freed by an
unrelated caller while the holder is very much alive — that is not death-recovery at all,
it is unauthenticated pre-emption, and it is out of this question's literal scope but the
same finding.

## Q5 — does `worker_semaphore` fail closed on every dispatch path, including re-dispatch after a denial?

**Verdict: SOUND.**

Every branch of `worker_semaphore` (`deploy/policies/omnipods_policies.py:178`) that can
reach a decision either `_deny(...)`s or requires an explicit, single, operator-owned
escape hatch (`NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1`) to `_ALLOW`. Confirmed for every distinct
failure shape found in the code, each with a passing test or a fresh reproduction:

- **No slot at all / malformed args** → DENY (`test_dispatch_without_slot_denied`,
  `test_opaque_create_unknown_and_malformed_dispatches_deny` — covers `session_id`-mode
  sends, `sys_session_create`, unknown agents, non-int/bool/float/zero `slot_id`).
- **Live slot, correct tuple, first use** → ALLOW, and the **identical replay of the same
  event immediately after** → DENY ("already used") — this is the literal "re-dispatch
  after [a consumption, which is the terminal case of a denial-adjacent state] " scenario:
  `test_dispatch_with_live_slot_allowed_and_consumed`,
  `test_named_alpha_and_beta_dispatches_are_exactly_one_use` (ALLOW then, same event,
  DENY, asserted back-to-back).
- **Wrong agent bound to the slot** → DENY, and the denial does **not** consume the slot —
  the correct agent can still use it afterward: `test_slot_bound_to_its_agent` (DENY then
  ALLOW on the same slot, different callers).
- **Released or invented slot id** → DENY: `test_released_or_invented_slot_denied`.
- **Stale generation (a slot from an earlier `generation` replayed after a newer one
  superseded it)** → DENY, without mutating the row: `test_stale_generation_and_expired_slot_deny_without_consumption`.
- **Missing control-plane database file** → DENY, `"control plane not initialised"`:
  `test_fails_closed_without_database`.
- **A genuine `sqlite3.Error` reaching the outer exception handler (distinct from the
  missing-file branch above, which is checked separately by `os.path.exists` before any
  connection is attempted)** → DENY, `"control plane unavailable"`. No existing test in the
  suite reaches this specific branch (every existing fail-closed test uses a missing path,
  which is intercepted earlier); this audit closed that gap with a fresh reproduction
  (`docs/audit/CONCURRENCY.capture.txt`, `probe.py`): a real, initialized control DB is
  truncated and overwritten with non-SQLite bytes, then `worker_semaphore` is invoked
  directly against it — result: `DENY`, `"control plane unavailable (DatabaseError) —
  denying rather than dispatching unbudgeted"`, exactly the code's stated intent.
- **The one explicit escape hatch** → with `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1` set, the
  *same* missing-database event that was just denied is now ALLOWed; unsetting the
  variable and **re-issuing the identical event** (a literal re-dispatch after the
  fail-open window closes) returns to DENY: `test_explicit_operator_escape_hatch` — proving
  the escape hatch is not sticky and does not silently persist past the operator's control.
- **Continuation dispatch, budget-expired TOCTOU** (slot acquired while the task's budget
  was live, expired before actual dispatch) → re-derived and DENY at dispatch time for all
  three continuation kinds (qa/review/implementer):
  `ContinuationBudgetRecheckAtDispatch` class, `test_policy.py`.
- **`pm`/exempt agents and non-dispatch tool calls** → correctly ignored (`_ALLOW`,
  policy does not apply) rather than mis-evaluated: `test_pm_exempt_and_non_dispatch_ignored`.

No dispatch path found reaches `_ALLOW` without either the slot's one-time CAS succeeding
or the explicit env-var escape hatch being set at call time; no path re-derives a *stale*
ALLOW from a cached decision (every check re-reads the database fresh, inside a single
`BEGIN IMMEDIATE` transaction, on every call).

## Not included / scope boundary (restated from #477)

This document does not fix F-1, does not modify `worker_semaphore` or any release verb,
and does not create `docs/audit/FINDINGS.md` (#482's own artifact — that issue must create
F-1 there with its own disposition; this crew does not own that path and #477's own
dependency note already sequences #482 after or alongside #477–#481). Crash/restart/reboot
recovery beyond what Q1/Q4 above already touch, vendor rate-limiting, GitHub/PR workflow,
and credential/sandboxing boundaries are #478/#479/#480 and stay there.

### RT id follow-up (for `product`, not applied here)

`docs/prd/DOD-MANIFEST.json` already carries four unsatisfied placeholders that overlap
this audit's findings almost exactly (`test_node_id: null` for all four, per the manifest
read at the start of this task):

- **RT-16** ("a program-level worker dispatch without a live, bound, unconsumed slot is
  denied") ⊇ `test_policy.WorkerSemaphorePolicy.test_dispatch_without_slot_denied`.
- **RT-17** ("concurrent acquires across program and lead paths never exceed
  `max_concurrent_workers - reserve_for_oncall`") ⊇
  `test_slots.GlobalSemaphore.test_concurrent_acquires_respect_budget_and_reserve` and/or
  `test_stress.Stress.test_concurrent_pods_with_crashes`.
- **RT-18** ("dispatching a task whose active claim belongs to another claimant is
  denied") ⊇ `test_claims.ClaimRaces.test_concurrent_claims_one_winner`,
  `test_recovery.Recovery.test_restart_reconstructs_state_no_duplicates`.
- **RT-19** ("a non-holder cannot release or renew a claim") — **this is precisely F-1's
  negative space**: no existing test satisfies RT-19 because, per this audit, the code does
  not actually enforce it for `claim release` (it does for `claim renew`, which requires no
  special identity argument either, but is not release — out of scope to adjudicate further
  here). `product`/whoever owns #482's fix should treat RT-19 remaining unsatisfied as
  additional corroboration of F-1, not a separate gap to chase.

Binding these (or filing new ids) is `docs/prd/**`'s single-writer's call, not this crew's;
named here only so the next reader does not have to re-derive the overlap.
