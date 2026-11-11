# Nightshift operating lessons and restart guardrails

Status: **ACTIVE OPERATOR GUIDANCE**

Date: 2026-08-26

This document preserves the lessons from the first Nightshift self-dogfood run,
the exact-SHA runtime cutover, the backlog and pull-request audit, and the final
clean-slate reset. It is deliberately operational: accepted ADRs and
`docs/prd/DEFINITION-OF-DONE.md` remain authoritative for product and design
decisions. If this document appears to conflict with an accepted ADR, stop and
reconcile the documents before acting.

## The short version

Nightshift's individual components are useful. Most failures came from a gap
between a declared control and the place where that control needed to execute.
The recurring pattern was:

1. a rule was documented;
2. source and tests implemented something consistent with that rule;
3. the installed runtime, policy loader, scheduler, cache, worktree, or GitHub
   account did not enforce it;
4. a green check or plausible status report was treated as end-to-end proof.

The corrective rule is:

> Treat a guarantee as real only when the actual consumer enforces it and the
> production-shaped path has falsifiable evidence.

## Clean baseline recorded on 2026-08-26

This section is a historical baseline, not a permanent promise. Re-read the
live system before relying on it.

- VPS `/srv/project` was clean on `main` at
  `dba8a373eafeb17aebcab8828e56334b41d97867`, matching `origin/main`.
- Omnigent PostgreSQL application tables were empty. `alembic_version` was
  retained so the existing schema remained identifiable.
- Nightshift SQLite control tables were empty and `PRAGMA integrity_check`
  returned `ok`.
- No Omnigent server, host, Codex app-server, Nightshift worker, scheduled task,
  or runtime listener remained active.
- The Program bundle at that exact `main` SHA successfully registered against
  a disposable database and artifact directory. The disposable state was then
  removed; production PostgreSQL remained empty.
- Historical immutable release directories remained inactive. Their presence
  did not mean that their code was running.
- No database backup was created for the reset, by explicit owner direction.
  The removed conversations, control history, and dirty-worktree-only files are
  not recoverable from the VPS.

## What not to do

### Source, deployment, and runtime truth

- **Do not call a merge a deployment.** A merged commit, green CI run, restarted
  process, or healthy HTTP endpoint does not identify the served Program bytes.
- **Do not serve a Program from a mutable checkout.** `/srv/project`, stale
  worktrees, and `/home/nightshift/nightshift-runtime` are not deployment
  identities. Materialise a reviewed 40-character Git SHA under an immutable
  release root and bind server, host, runner, policy path, and control DB to it.
- **Do not claim an exact-SHA cutover from path names alone.** Verify the Git
  object, filesystem manifest, registered agent snapshot, served descriptor,
  selected non-secret process environment, and an executed Claude/Codex probe.
- **Do not edit the Omnigent cache manually.** Registration owns cache
  replacement. If cache and registration disagree, diagnose the registration
  path and re-register through the supported interface.
- **Do not reset or clean a dirty shared checkout casually.** First enumerate
  staged, modified, untracked, ignored, worktree, and control-plane state. Data
  removal needs explicit owner authority and a stated recovery consequence.
- **Do not let a runtime continue while its database is being cleared.** Pause
  scheduling, quiesce workers, stop every server/host/app-server database
  client, verify listeners and connections are gone, and only then mutate data.

### Program authority and concurrency

- **Do not enable overlapping scheduled Program runs without a mechanical
  single-authority control.** A prompt that asks both Programs to cooperate is
  mitigation, not fencing.
- **Do not trust an empty inbox as evidence that no workers exist.** Children of
  another Program session are invisible to the current session's inbox. Read
  the control database and host processes.
- **Do not trust declared policies merely because their factories and tests
  exist.** Prove the policy is loaded at the scope where the protected tool call
  is evaluated.
- **Do not dispatch a worker without a live, correctly bound, one-use slot.** A
  slot declared in prose, consumed at the wrong org level, or absent from the
  loaded root policy does not enforce a global budget.
- **Do not retry exit code 3, a held claim, or a rate-limited vendor in a loop.**
  Queue, reroute once where the contract permits, or wait with bounded backoff.
- **Do not multiply crews to create apparent speed.** Crews add ownership and
  queueing; they do not increase the global worker or subscription budget.

### PostgreSQL and SQLite

- **Do not confuse the two databases.** PostgreSQL holds Omnigent users, agents,
  policies, sessions, conversations, files, hosts, and scheduled tasks. SQLite
  holds Nightshift slots, claims, leases, tasks, checkpoints, vendors, beats,
  sequences, and detector state.
- **Do not delete either database on the strength of a UI screenshot.** Resolve
  the actual server URI, SQLite inode/path, client processes, table counts, and
  schema version first.
- **Do not remove migration metadata when the intention is to clear application
  rows while retaining the schema.** For Omnigent, retain `alembic_version`
  unless the explicit task is a full schema rebuild.
- **Do not report a clean database from a successful command alone.** Recount
  every table, verify zero unexpected connections, run SQLite integrity checks,
  and confirm no process immediately repopulated rows.
- **Do not run a migration, production reset, credential rotation, or permanent
  data deletion without an explicit owner instruction that names the
  irreversible consequence.**

### Issues, pull requests, and decisions

- **Do not open a PR for every proposal, probe, critique, or review.** A GitHub
  PR represents merge intent. Preserve blind inputs and exploratory evidence as
  immutable `.omni/` artifacts with hashes; use one active delivery PR per work
  item.
- **Do not start implementation before Definition of Ready, two independent
  proposals, reconciliation, an ADR, and the required design gates.** Small work
  may use smaller artifacts, but it does not skip the sequence.
- **Do not keep multiple canonical PRs for one decision.** A replacement must
  name the exact superseded content and one canonical successor. Audit PRs do
  not remain competing authorities.
- **Do not carry a review verdict across a moved head.** Every verdict names the
  exact head SHA. Any later commit makes the standing verdict historical.
- **Do not declare a branch frozen and then push another round.** If new evidence
  requires a change, explicitly reopen the round and invalidate the freeze
  before editing.
- **Do not let PR bodies drift from their heads.** Status, current SHA, file
  scope, blockers, verification, and replacement lineage must describe the
  bytes actually proposed.
- **Do not allocate ADR, receipt, or migration numbers by inspection.** Use the
  transactional sequence allocator. Two branches independently chose ADR-0025;
  that collision was a predictable control failure.
- **Do not use closing keywords in task PRs when the control plane owns
  acceptance.** Merge first, verify acceptance, update the task state, and close
  the issue deliberately.
- **Do not leave absorbed issues and umbrellas without a current checklist.**
  Consolidation is useful only when the surviving issue shows what remains,
  what merged, and what acceptance evidence is still missing.
- **Do not label many unrelated items P0.** P0 means interrupt current work. If
  everything is P0, the label carries no scheduling information.

### GitHub enforcement and identity

- **Do not treat same-account comments as enforceable independent reviews.** A
  shared GitHub identity cannot prove which vendor/seat authored a verdict, and
  a PR author cannot approve their own PR through GitHub.
- **Do not assume branch protection or rulesets exist.** Verify the repository
  plan and actual API response. When GitHub cannot enforce a gate, the Release
  path must fail closed using independently recorded evidence.
- **Do not allow every seat to possess an unscoped merge capability while
  claiming Release is the only integrator.** Either enforce the identity at the
  credential/platform boundary or state honestly that it is advisory.
- **Do not leave merged branch deletion disabled indefinitely.** Delete merged
  delivery branches after the merge and rollback record are durable. Preserve
  open-PR heads and state branches deliberately, not accidentally.

### Testing and evidence

- **Do not count tests with `grep`.** Report the exact command and the test
  runner's result.
- **Do not call a test non-vacuous until it fails for the intended reason against
  the pre-change implementation.** A mutation must exercise the same production
  path and fail on the promised invariant.
- **Do not mock the fact under test.** A rollback test whose rollback function is
  mocked to return `verified: true` proves the fixture, not rollback.
- **Do not test only templates when the defect is in installation.** Render the
  unit, assert no placeholders remain, execute the installed command shape, and
  inspect the effective systemd fragment/environment where possible.
- **Do not convert unavailable safety evidence into healthy defaults.** A failed
  systemd query, unreadable DB, missing schema, unknown Git ancestry, or
  unsupported keyword is a refusal, not `inactive`, `empty`, or `safe`.
- **Do not key security controls to third-party English error text.** Assert facts
  controlled by Nightshift: exit class, filesystem identity, parsed structure,
  exact SHA, or database state.
- **Do not claim production readiness from CI alone.** CI proves the repository
  test environment. Deployment receipts prove the installed and served
  environment.

### Sessions, observability, and reporting

- **Do not create an hourly stream of indistinguishable session titles.** Use a
  stable automation identity plus run/session identifiers and expose which one
  is active.
- **Do not infer active work from the mobile session list.** A stopped session
  may leave an app-server process; a live child may be invisible to the current
  Program. Reconcile UI, process, scheduler, PostgreSQL, and control-plane data.
- **Do not leave orphaned native app servers indefinitely.** Quiescence and reset
  procedures enumerate and terminate them, then verify their listening ports
  are gone.
- **Do not publish `.omni/STATUS.md` as “always current” without a freshness and
  reconciliation check.** During the first run, `main`, `program/discovery-state`,
  `omni/state`, GitHub, and the control DB all described different waves.
- **Do not use an append-only status issue as the only current view.** Historical
  reports remain valuable, but one generated digest must supersede old status
  and link to evidence.
- **Do not report future timestamps or trust local clock ordering blindly.**
  Compare UTC, commit time, control DB timestamps, and the current process epoch.

### Models, subscriptions, and credentials

- **Do not retry Claude 529 or usage-limit failures aggressively.** Stop that
  vendor for the wave, use computed backoff, and reroute only when the task and
  independence requirements allow it.
- **Do not move every hard task to the most expensive model.** Improve the
  contract and reasoning effort first; raise tier after evidence such as two
  blocking-review loops or a compounding architecture decision.
- **Do not put API keys into this subscription-only design.** Use supported
  subscription authentication and keep budget fields as token-volume runaway
  controls, not reported spend.
- **Do not paste passwords, database URIs, tokens, or private environment values
  into issues, PRs, transcripts, commands that will be logged, or chat.** Rotate
  a credential after accidental disclosure and prefer SSH keys over root
  password login.

## Required workflow for the next run

1. **One issue.** Open exactly one owner-visible issue. Give it one outcome,
   numbered acceptance criteria, priority, owner, lifecycle state, and explicit
   exclusions. Do not start until PM records Definition of Ready.
2. **One work item and one delivery PR.** Persist blind design inputs outside
   GitHub PRs, reconcile them, and open one Draft delivery PR with merge intent.
3. **Pin the base.** Resolve `origin/main` to a 40-character SHA and record it.
   Do not use a dirty or detached shared checkout as the base oracle.
4. **Build narrowly.** Acquire the required slot/claim/lease before dispatch.
   Keep one path owner and one coherent diff.
5. **Prove the change.** Run focused tests, the full deterministic suite, the
   pre-change mutation, and any Linux/installation path the change depends on.
6. **Review the exact head.** Different-vendor, different-crew, QA, PM, Staff,
   and Security gates each name the SHA and their actual evidence. A new commit
   invalidates every standing verdict it affects.
7. **Merge once.** Release verifies the merge result and the default branch
   after merge. Delete the merged branch only after evidence is durable.
8. **Prepare an immutable release.** Materialise the merged SHA under
   `/srv/nightshift-releases/<sha>/checkout`; never launch production from
   `/srv/project`.
9. **Start manually first.** Register the Program, verify the served bundle and
   database identity, and execute one Claude and one Codex seat. Prove the
   control plane remains durable after both.
10. **Add observability before repetition.** Prove failure and recovery signals
    against the installed artifact. Only then create or enable recurring
    automation, with mechanical overlap prevention.
11. **Record one receipt.** The receipt binds Git SHA, release root, Program
    descriptor, registration identity, DB identity, process epochs, executed
    seats, control-plane continuity, and scheduler state.
12. **Finish or stop.** Close the issue only after its acceptance criteria are
    individually verified. Do not open the next issue until the current one is
    merged, accepted, and cleaned up.

## Evidence hierarchy

When sources disagree, use this order for the fact each layer owns:

| Question | Authoritative evidence |
|---|---|
| What code is approved? | exact Git commit and reviewed diff |
| What code is installed? | immutable release manifest and receipt |
| What Program is served? | server registration/descriptor plus executed probe |
| What processes are active? | host process table and listening sockets |
| What Omnigent work exists? | PostgreSQL rows plus live server state |
| What Nightshift work owns resources? | SQLite control DB |
| What should happen next? | the one ready issue and accepted ADR/spec |
| What happened historically? | merged PRs, closed issues, ADRs, incidents, ledger and reports |

No lower row may overrule a higher row on a fact it does not own. In
particular, narrative status never overrules the control DB about live holders,
and the control DB never proves which Program bytes the server loaded.

## Legacy Draft PR lesson map

The following map preserves why each legacy Draft PR mattered before it was
closed. Closure means “not the active delivery path”; it does not erase its
GitHub discussion or make every underlying problem fixed.

| PR | Durable lesson retained here |
|---|---|
| #131 | Agent identity must bind the actual registered name and expected id; probes need hermetic process cleanup and reproducible raw evidence. |
| #134 | A deny floor is only as strong as its complete irreversible-action classifier; loaded scope and wrapper/indirection bypasses must be tested. |
| #148 | Detector identity faults and occurrence reconciliation must be durable, but a rendered/installed invocation path is part of the feature. |
| #149 | Safe watchdog defaults can preserve beat liveness while scheduler mutation remains opt-in; source approval does not fix an unwritten install path. |
| #155 | Exact-SHA observability installation needs fresh-host support, fail-closed user-manager probes, non-vacuous rollback, and installed-artifact evidence. |
| #172 | Blind architecture proposals are inputs, not merge candidates. |
| #173 | Blind product proposals are inputs, not merge candidates. |
| #175 | Worker-budget enforcement must be reconciled with actual nested dispatch and loaded policy scope. |
| #176 | A canonical receipt specification must settle capacity arithmetic, bounded fields, shared predicates, and exact-head gates before implementation. |
| #177 | Incident reproduction and the limits of repo-owned Program fencing remain valuable even when the proposal is not an implementation contract. |
| #180 | A one-shot dispatch permit can narrow authority, but raw PR creation exposed that the documented PR guard was not loaded. |
| #182 | Independent capacity calculations exposed an under-specified receipt bound; correct evidence does not excuse workspace-policy violations. |
| #183 | Provenance defects, repeated review rounds, stale bodies, and a post-freeze Round 8 created an audit artifact that could not be a delivery authority. |
| #185 | One delivery PR per work item is the right direction, but process ADRs also require current migration facts and collision-free identifiers. |
| #187 | A clean replacement must carry the final canonical content and receive fresh exact-head gates; copying an earlier passed blob is insufficient after later edits. |

## Reversal and amendment

Amend this guidance when a production-shaped run provides contradictory
evidence. Record the command, exact SHA, environment, observed result, and the
rule being changed. Do not silently soften a guard because it slows one run.

If a rule becomes mechanically enforced, keep the lesson but replace procedural
instructions with a pointer to the enforcement point and its regression test.
The desired end state is fewer reminders and more controls that fail closed.
