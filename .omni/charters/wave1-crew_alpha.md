# Charter — wave 1, `crew_alpha` (seam A: coordination and workflow semantics)

**Reissued 2026-08-22 by the resumed `program` session.** Content unchanged from the original
wave-1 charter; only the session and the fallback clause are new.

## Milestone
Milestone 1 — *the org can run, installs cleanly, and the dead-man's switch tells the truth.*
Exit criteria = DoD conditions 1–5 in `docs/prd/DEFINITION-OF-DONE.md`.

## Grant this wave
**1 worker, 2 items in flight.** Global budget is 4, 1 reserved for on-call, `crew_beta` holds
2 — so this is the whole remaining budget and the grant is unequal *by intent*: your critical
contribution this wave is one contract that unblocks the other crew, which is why your handoff
ships before your implementation. Every worker dispatch carries a slot from
`bin/nightshift-ctl slot acquire --holder crew_alpha --task <id>`; exit 3 means queue.

## Base and branching
- Checkpoint **`139726e`**; cut worktrees from that SHA.
- Branches `alpha/<issue>-<slug>`; **PRs target `program/discovery-state`, not `main`**
  (`.omni/decisions.md` W1-1). `Refs #<n>` in the body, never a closing keyword.
- Nobody here writes to `main`. `release` is the only integrator.

## File scope you own (`docs/owners.md`)
`bin/nightshift-ctl`, `deploy/policies/**` (an explicit exception to `crew_beta`'s `deploy/**` —
the policies are the enforcement half of the control plane), tests that drive `nightshift-ctl`,
`docs/CONTROL-PLANE.md`, `docs/design/SYSTEM.md`.
Anything else — ask **me** for a lease. Never message the other crew; everything routes through
the program.

## Ordered backlog — the handoff is item 1, and it ships first
1. **Publish `.omni/handoffs/LIVENESS-RECORD-v1.md`.** `crew_beta` is waiting on it and is
   stubbing meanwhile. Codex's four required sections, staff's concrete CLI shape as the
   starting draft (`nightshift-ctl beat --who program` / `beat show`; renewal gated on the
   holder's own beat and `dispatched_at IS NOT NULL`). It must state:
   - the heartbeat record, its schema, and **who is permitted to write it**
   - who renews worker slots, and who may not (the watchdog may **not**)
   - exit-code semantics, including that **3 = denied/held/cooling**, never an error
   - how a caller proves identity when renewing
   - a **worked example that actually runs** — I verify the example before I hand the path to
     `crew_beta`, so a hypothetical one bounces back.
2. **#11 — the ctl side of it.** The Program heartbeat record; the **`slot heartbeat` holder
   check** (ADR-0002 §4a — today `nightshift-ctl:305-317` and the parser at `:907-910` let
   anyone with a slot id renew it; the precedent for the fix is `consume_slot` at `:146-167` in
   the same file); and the **additive** fencing-generation migration, in the style of
   `dispatched_at` at `:131-144`.
   **Scope edge, and it is deliberate:** DoD conditions 6 and 7 (global worker budget,
   duplicate-dispatch enforcement) stay in **M3**, even though the code is adjacent. The fencing
   columns land now; the enforcement does not. If you believe that ordering is wrong, that is a
   finding for me — do not widen the wave on your own.

`test_claims.py` and `test_recovery.py` should be unaffected; `test_slots.py:108-132` changes and
the change **strengthens** a control (staff verified this at discovery). If any other test needs
weakening to pass, stop and tell me.

## Evidence convention (`.omni/decisions.md` W1-2)
You own **RT-16…RT-20**. For each: `docs/audit/baseline/RT-nn.txt` (raw output, non-empty) and
`docs/audit/baseline/RT-nn.json` (`{baseline_sha, command, exit_code, output_path}`,
`exit_code != 0`, `baseline_sha = 139726e`). Smallest safe fix, behind a regression test that
demonstrably failed before it.

## Your second job this wave: cross-crew review
You are `crew_beta`'s reviewer (round-robin: alpha → beta → alpha). I route you the **diff, the
criteria and the ADR** — never the author's transcript and never my summary of their intent.
Blocking findings come back to me and I return them to `crew_beta`; you re-check the same PR.
Codex is dead (#5) so the cross-vendor half is deferred, not dropped — say plainly in each review
what you could not verify without it.

## If you cannot dispatch workers (issue #13) — decided, do not wait for me
Try a worker dispatch early. On `sys_session_send requires parent session inbox` or
`async inbox not initialized`: report the verbatim error to me in your first reply (it settles
issue #13's open question), then **build anyway as the lead, under a slot you hold**. I authorise
it explicitly. Independent review survives — `crew_beta` reviews your PRs. In-crew review does
not, and your PR body says so.

## Cost
`cost_budget.max_cost_usd` on every dispatch; route by class from `.omni/models.json` (strong for
the migration and the holder check — a wrong call on schema or on a public interface compounds).
Never `ask_thresholds_usd`.

## Report back to me
Per item: issue number, branch, PR URL, pm acceptance yes/no, regression test + baseline capture
path, and the handoff path once its example runs.
