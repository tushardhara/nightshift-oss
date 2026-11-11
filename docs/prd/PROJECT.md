# Nightshift Self-Dogfood — PRD

*Author: `product` (Claude). Blind draft — written without reading `architect`'s
architecture. Source: `.omni/brief.md`, the only thing the owner wrote.*

---

## What this is

Nightshift is a system that runs autonomous delivery crews on one Ubuntu VPS for
days at a time with nobody watching. This project points Nightshift at itself: fix
the four installation and liveness bugs found during the real Hostinger install,
then audit the machinery that is supposed to keep a multi-day unattended run from
silently stopping, corrupting its own state, or doing the same work twice — and
turn every audit area into a written verdict plus a fix for whatever it confirms.

## Who it is for, and the one job

Two users, one job each. They are not the same user and the failures they see are
not the same failures.

| User | The job they are trying to get done |
|---|---|
| **The owner**, running Nightshift on a VPS | Start a run, close the laptop, and come back in three to seven days to merged work — and be *told* on their phone if it stopped, rather than discovering silence. |
| **The agents**, running inside it | Take exactly the work that is theirs, be able to prove nobody else holds it, survive a crash or a rate limit without losing or repeating the task, and never be blocked forever waiting on a holder that is already dead. |

The owner's failure mode is **silence that looks like health**. The agents' failure
mode is **two workers doing the same task**, or **everyone waiting on everyone**.
Bug 4 in the brief is exactly the first; the audit list is mostly the second.

## The core loop this project must protect

The smallest sequence that makes Nightshift useful — everything else is optional
until this works end to end, unattended:

1. Owner provisions a fresh Ubuntu box (`provision-ubuntu.sh`, then `bootstrap.sh`)
   and it **finishes** without hand-holding.
2. `preflight.sh` says the box can survive a multi-day run, or says exactly why not.
3. The program starts, creates its hourly heartbeat, charters a wave.
4. Crews claim work through `nightshift-ctl` (one slot, one claim, one lease holder),
   build, review across crews, and `release` merges.
5. A crash, a reboot, or a vendor rate limit costs time, never work: state is
   reconstructed from the control plane plus the files, and nothing is dispatched twice.
6. If the loop stops for any reason, the watchdog notices **from evidence the
   program itself produced** and pushes a notification to the owner's phone.

Steps 1 and 6 are broken today, confirmed on a real install. That is why they are
milestone 1.

## Outcomes that matter

Ranked. If the project only achieved the first two it would still have been worth running.

1. **A fresh install completes on a stock Ubuntu 24.04 VPS without a human editing a script.**
2. **A dead program is reported as dead.** No artifact written by machinery outside
   the agent loop — backups, timers, the watchdog itself — may count as proof of life.
3. **No duplicate work and no permanent wait**, demonstrated by tests that fail
   against the pre-fix code, not by argument.
4. **A multi-day run degrades instead of halting** under crashes, restarts, and
   subscription exhaustion.
5. **Every audit area ends with a signed verdict**, so a stranger can read what was
   checked, what was found, and what was deliberately accepted.

## Scope

**In scope**
- The four confirmed bugs, with root cause evidence, regression tests, and the
  smallest safe fix (brief §1–4).
- Install/provisioning reliability, including a repeatable fresh-box rehearsal
  that does not require buying a VPS to run.
- Watchdog liveness correctness, including its side effect on worker-slot renewal.
- Audits of the thirteen listed areas, each converted into a finding document with
  a verdict per sub-question, plus fixes for whatever is confirmed.
- The regression suite (`tests/`) and CI that runs it.

**Explicitly out of scope**
- `/srv/nightshift` (frozen controller) and `/home/nightshift/nightshift-runtime` —
  never read, never modified, permanently out of scope.
- Re-architecting the control plane: no Postgres for `control.db`, no long-lived
  daemon, no compiled binary, no new runtime dependency beyond bash + python3 stdlib.
- New capability: no new agents, crews, skills, harnesses, providers, or UI.
- Multi-box, HA, or distributed workers (the design explicitly says do not split
  crews across boxes; this project does not revisit that).
- Prompt-scaffolding A/B experiments (`README.md` § "Prompts and the reasoning tier").
- Any credential work: rotation, new secrets, new vendor accounts.
- Coverage-percentage targets and line-count-reduction targets. Both are gilding
  proxies; conditions here name specific untested invariants instead.

## Assumptions

Numbered. Each is something the owner did not say; each has a stated break.

1. **Fixes apply to the *next* install and the *next* start, not to the running box.**
   The controller is frozen, so nothing merged here heals the live instance mid-run.
   *Breaks if wrong:* the owner expects the current box to self-heal; then milestone 1
   needs an operator upgrade runbook and a restart window.
2. **Day-one capacity is one Ubuntu 24.04 VPS (~32 GB / 8 vCPU, Hostinger KVM8),
   4 concurrent workers with 1 on-call reserve, 2 crews, ≤6 items in flight, runs of
   3–7 days.** Taken from `.omni/capacity.json` and `tests/test_deploy_lib.py`.
   *Breaks if wrong:* every performance condition in the DoD is anchored to the wrong
   numbers and must be re-anchored to `docs/architecture/CAPACITY.md`, which `architect`
   owns and which is an empty template as of this draft.
3. **`docs/architecture/CAPACITY.md` will be filled in by `architect` during this
   discovery; where it and this PRD disagree on numbers, it wins and the DoD is
   revised with a changelog entry.** *Breaks if wrong:* the DoD's scale conditions stay
   anchored to assumption 2 and are labelled as such.
4. **No new runtime dependency.** `tests/run.sh` is stdlib-only on purpose and CI has
   no install step. *Breaks if wrong:* a fix needing a package changes the deploy story
   and the CI workflow, and stops being "smallest safe fix".
5. **Container rehearsal is acceptable evidence for install correctness.** The repo
   already uses this pattern (`tests/linux/toolchain-smoke.sh`, `docker-bind-smoke.sh`).
   *Breaks if wrong:* only a real fresh VPS proves an install, and the DoD gains an
   owner-run condition that costs a machine and a day.
6. **The four bugs are single-root-cause and as diagnosed in the current tree**
   (`deploy/provision-ubuntu.sh` Go section; `deploy/bootstrap.sh` `systemctl --user`;
   `deploy/nightshift-watchdog.sh` `find .omni -printf %T@` versus
   `deploy/nightshift-state-backup.sh` writing `.omni/control-backup.json`).
   *Breaks if wrong:* milestone 1 grows by whatever hides behind them, and the extra
   bugs get their own conditions via the changelog.
7. **"Regression test" means an automated test that fails against pre-fix code and
   passes after.** Manual checks count only where the DoD names them as an operator
   drill. *Breaks if wrong:* "tested" becomes self-reported and DoD 5 is unenforceable.
8. **Multi-day evidence comes from this project's own run plus a time-compressed
   simulation, not from a separate idle soak.** *Breaks if wrong:* the project's end
   date becomes hostage to a calendar it cannot compress.
9. **The audit's obligation is a verdict per sub-question and a fix per confirmed
   bug — not proof that no bug exists.** *Breaks if wrong:* the audits never finish,
   which is the failure mode this PRD exists to prevent.
10. **`main` on GitHub with Actions (`ubuntu-latest`) is the arbiter of "green".**
    *Breaks if wrong:* every "passes on the default branch" condition needs a local
    substitute command and loses its independence from the box.
11. **This repo stays single-writer from this box**, so `omni/state` pushes
    fast-forward. *Breaks if wrong:* the backup path needs conflict handling, which
    is a new finding, not a silent fix.
12. **Nothing in the brief's "do not weaken" list may be traded for finishability.**
    Where a simplification would touch worker limits, leases, claims, review
    requirements, credential boundaries, sandboxing, branch protections, or release
    gates, the answer is "keep it and record why". *Breaks if wrong:* nothing —
    this is the conservative direction by construction.

## Non-goals and anti-requirements — what this must NOT become

- **Not a rewrite.** Every fix is the smallest safe change with a test. A finding
  whose fix is a redesign becomes a recorded item in `.omni/tech-debt.md` with a
  trigger, and a follow-up project — not scope creep here.
- **Not a watchdog that acts.** It alerts. It must never restart services (that kills
  in-flight worker turns) and must never try to re-create the heartbeat.
- **Not a safety-relaxation project.** "Unnecessary complexity" must not become an
  excuse to delete a control. Any control removed must be shown to be redundant with
  a named remaining control.
- **Not an observability product.** No dashboards, no metrics stack, no new UI.
- **Not a coverage exercise.** No percentage target; named invariants only.
- **Never touches the frozen controller** or the runtime directory, for any reason,
  including "just to check".

## Open questions

None blocking. Each has a decision taken now.

| # | Question | Status | Decision meanwhile |
|---|---|---|---|
| Q1 | Should the watchdog restart services or re-create the heartbeat when the org is silent? | proceed-anyway | **No.** Alert only. Restarting kills in-flight turns; only a program session can re-create the heartbeat, and the alert carries the one-liner. |
| Q2 | Does "done" require a real fresh-VPS install? | proceed-anyway | **No.** Container rehearsal from a clean clone is the checkable evidence (assumption 5). A real reinstall is an owner option, not a gate. |
| Q3 | How long is "unattended 3–7 days" as a *condition*? | proceed-anyway | **72 h observed on this run + a simulated 7-day cycle in tests.** A literal 7-day idle soak would make the end date a calendar fact. |
| Q4 | Should the four fixes be backported to the running box? | proceed-anyway | **No** (assumption 1). Out of scope; the fixes ship for the next install. |
| Q5 | If an audit confirms a bug whose fix would weaken a boundary, what happens? | proceed-anyway | **Record, do not fix.** Finding stands with verdict `ACCEPTED-RISK`, a trigger in `.omni/tech-debt.md`, and a note that it needs an owner decision. |
| Q6 | Is a fifth install bug in scope if found during milestone 1? | proceed-anyway | **Yes, if it blocks a fresh install.** It gets a numbered DoD condition via the changelog. Anything else goes to the audit milestones. |

## Contradictions and unfinishable asks in the brief, and what I did with them

Recorded here so nobody re-litigates them later.

1. **"Audit X" is not finishable.** Thirteen audit areas with no end condition — an
   audit can always be continued. **Converted to:** five finding documents under
   `docs/audit/`, each with a fixed list of sub-questions, each sub-question carrying
   one verdict from the closed set `CONFIRMED-BUG | SOUND | ACCEPTED-RISK | OUT-OF-SCOPE`
   plus file:line or command evidence, and every `CONFIRMED-BUG` closed by a merged
   fix with a regression test or an entry in `.omni/tech-debt.md` with a trigger.
   The end condition is "no sub-question unanswered, no confirmed finding undisposed".
2. **"Unattended operation for 3–7 days" cannot be a re-runnable condition.** A
   stranger with the repo cannot wait a week. **Converted to** DoD 15/16/19: an
   accelerated-clock simulation plus a named operator drill plus an observed-run report.
3. **"Fix the install" versus "the controller is frozen".** The bugs were found in a
   deploy path this run cannot re-execute against itself. **Reading taken:** fixes are
   proved by tests and a container rehearsal from a clean clone; the live box is not
   modified (assumption 1). Dropped: any notion of "the install is fixed" being
   demonstrated on the running instance.
4. **"Test coverage" as an audit area has no natural end.** **Converted to:** a
   written list of the system's safety invariants with, per invariant, either the test
   that pins it or a recorded gap. No percentage anywhere.
5. **"Unnecessary complexity" versus "do not weaken safety".** These pull opposite
   ways. **Reading taken:** complexity findings are recorded with a kept/removed
   verdict; removal requires naming the remaining control that makes the removed one
   redundant. Dropped: any net-LOC or "simplify the control plane" goal.
6. **"Use normal branch/PR/review workflow" while running fully unattended.** Kept
   as-is: the existing cross-crew review and `release`-only-merge rules are the
   workflow, and branch protection is not changed (brief forbids weakening it).

## Where the fixed point is

`docs/prd/DEFINITION-OF-DONE.md` — 24 numbered conditions. This project ends when
they are all met. Nothing here overrides that file; where this PRD and the DoD
disagree, the DoD is the contract.

## Changelog

| Date | Change | Evidence that forced it |
|---|---|---|
| 2026-08-22 | Initial draft. | `.omni/brief.md`; repository read at `a73ba88`. |
