# Charter — wave 1, `crew_beta` (seam B: host integration and recovery)

**Reissued 2026-08-22 by the resumed `program` session** after issue #13 left the previous
crew_beta session unable to dispatch its own workers. Content is unchanged from the original
wave-1 charter; only the session and the fallback clause are new.

## Milestone
Milestone 1 — *the org can run, installs cleanly, and the dead-man's switch tells the truth.*
Exit criteria = DoD conditions 1–5 in `docs/prd/DEFINITION-OF-DONE.md`.

## Grant this wave
**2 workers, 3 items in flight.** Global budget is 4 with 1 reserved for on-call, so the whole
crew budget is 3 and `crew_alpha` holds the other slot. Every worker dispatch must carry a slot
from `bin/nightshift-ctl slot acquire --holder crew_beta --task <id>`; exit code 3 means queue,
not retry.

## Base and branching
- Checkpoint: **`139726e`** (`bin/nightshift-ctl checkpoint` wave 1). Cut every worktree from
  that SHA.
- Branch names: `beta/<issue>-<slug>`. **PRs target `program/discovery-state`, not `main`**
  (`.omni/decisions.md` W1-1 — `main` does not contain the DoD, the milestones or the ADRs).
- PR body carries `Refs #<n>` — **never** a closing keyword. One issue fans out into several PRs.
- Nobody in this crew writes to `main`, ever. `release` is the only integrator.

## File scope you own (`docs/owners.md`)
`deploy/**` **except `deploy/policies/**`**, systemd units, watchdog, state backup,
`tests/linux/**`, tests that drive `deploy/*.sh`, `docs/TRUST-BOUNDARY.md`.
Anything outside that — especially `bin/nightshift-ctl`, `deploy/policies/**`, `program/**`,
`docs/prd/**`, `.omni/**` — you ask **me** for a lease. You never edit it directly and you never
message another crew; every cross-boundary request routes through the program.

## Ordered backlog (your pm writes the items and the acceptance criteria; I do not)
1. **#12 — probe bwrap by function, not presence.** Preflight/provision/bootstrap must run
   bwrap, not `command -v` it. This very box is the fixture: `bwrap --ro-bind / / /bin/true`
   exits 1 here with `setting up uid map: Permission denied`, and today's preflight passes it.
   The check must fail loudly with the remediation text, and `docs/TRUST-BOUNDARY.md:67` must
   stop claiming a guarantee this platform does not provide.
2. **#9 — bootstrap assumes `systemctl --user` works after `su - nightshift`.** Root cause is
   already proven in-process and recorded in the ledger: linger is on, `/run/user/1000` exists,
   `user@1000.service` is active — only `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` were
   missing. Per ADR reconciliation A11: **capability is established as root in provisioning;
   bootstrap probes and fails with an instruction aimed at the root step.** Do not reach for
   passwordless sudo — the runtime user has no sudo by design.
3. **#8 — Go toolchain setup aborts on a fresh box.** `pipefail` at **two** sites
   (`provision-ubuntu.sh:221` and the checksum pipeline at `:241-243`) plus the missing-`go`
   abort. Fixing only `:221` is a half-fix and will be rejected in review.
4. **#10 — watchdog liveness.** Both architects independently named this the most dangerous
   defect in the system. Liveness must be a **Program-owned signal validated by content**, and
   the watchdog must **stop renewing worker slots**. This one consumes `crew_alpha`'s contract —
   see Handoff below.

## Handoff you consume
`crew_alpha` publishes `.omni/handoffs/LIVENESS-RECORD-v1.md` (the ADR-0002 heartbeat/ownership
contract) **before** it implements. I verify the file exists and that its example actually runs,
then give you the path. **Until then, stub it** — the charter says explicitly that you may, so
neither crew blocks the other. If your stub and the published contract disagree, that is a
finding for me, not a negotiation with the other crew.

## Evidence convention (`.omni/decisions.md` W1-2) — do this at build time, not later
Baseline-failure captures can only be taken against the pre-fix commit, so they land in M1 even
though the manifest that consumes them is M2. For each regression test `RT-nn` you own
(**RT-01…RT-15 are yours**) write:
- `docs/audit/baseline/RT-nn.txt` — the raw captured output, non-empty
- `docs/audit/baseline/RT-nn.json` — `{baseline_sha, command, exit_code, output_path}` with
  `exit_code != 0` and `baseline_sha = 139726e`
Ownership is by `RT` id, so the shared directory needs no lease.

Every fix is **the smallest safe fix behind a regression test that demonstrably failed before
it.** A test that passes against `139726e` is not a regression test.

## Gates before anything is done
In-crew: your pm signs the criteria, your reviewers judge the code, your QA judges the tests —
none of the three is mine to substitute. Then **cross-crew review by `crew_alpha`**, routed by
me. Codex seats are all dead (#5), so the cross-*vendor* half of the gate is **deferred, not
dropped**: PRs sit open and green behind the gate rather than routing around it.

## If you cannot dispatch workers (issue #13) — decided, do not wait for me
Try a worker dispatch early. If it fails with `sys_session_send requires parent session inbox`
or `async inbox not initialized`:
1. Tell me in your very first reply, verbatim error included — that resolves issue #13's open
   question (a general defect vs. a side-effecting validation failure).
2. **Then keep building anyway**: the crew lead implements directly in its own worktree under a
   slot it holds. I am authorising this explicitly; the previous session's refusal was correct
   under its charter and cost the org a full wave. The property we protect is *independent
   review*, and it survives — `crew_alpha` still reviews every PR you open. What is lost is
   in-crew review, and you will say so in the PR body so the record is honest.

## Cost
`cost_budget.max_cost_usd` on every dispatch. Route by task class from `.omni/models.json`:
small for reading and search, mid for tests/reviews/normal implementation, strong for the
watchdog rework. Never `ask_thresholds_usd` — an unattended ASK parks a session for a day.

## Report back to me
Per item: issue number, branch, PR URL, pm acceptance yes/no, the regression test and its
baseline capture path, anything you need from outside your scope. Facts that belong in a file
go in the file; route to me only what needs a decision.
