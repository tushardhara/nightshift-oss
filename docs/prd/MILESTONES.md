# Milestones — Nightshift Self-Dogfood

**Version 2** — re-mapped 2026-08-22 to the v2 Definition of Done (24 conditions).
v1 mapped condition numbers that no longer exist: the old 15–18 simulation platform
and the old 19 observation/drill were deleted, and four conditions were added
(bug 5, the two duplicate-work defects, the manifest/validator pair, handover and
traceability). See the changelog and `docs/prd/DOD-RECONCILIATION.md`.

Every one of the 24 conditions in `docs/prd/DEFINITION-OF-DONE.md` belongs to
exactly one milestone:

| Milestone | Outcome in one line | DoD conditions |
|---|---|---|
| **M1** | The org can run, install cleanly, and the dead-man's switch tells the truth | 1, 2, 3, 4, 5 |
| **M2** | Evidence stops being self-attested; a clean install proves itself | 8, 9, 10, 11, 12 |
| **M3** | One task, one worker, one budget — enforced, not described | 6, 7, 13 |
| **M4** | It survives days and crashes, and its boundaries hold | 14, 16, 17, 20, 21 |
| **M5** | Enforcement is real, and the record closes | 15, 18, 19, 22, 23, 24 |

## The two seams

Both architects independently returned **seam count = 2** (`.omni/decisions.md` A1),
and the program allocated crews accordingly:

- **Seam A — coordination and workflow semantics** → `crew_alpha`.
  `bin/nightshift-ctl`, `deploy/policies/**`, tests that drive ctl, `docs/CONTROL-PLANE.md`.
  Publishes the ADR-0002 liveness/ownership contract.
- **Seam B — host integration and recovery** → `crew_beta`.
  `deploy/**` (except `policies/`), systemd units, watchdog, state backup,
  `tests/linux/**`, tests that drive `deploy/*.sh`, `docs/TRUST-BOUNDARY.md`.
  Consumes the ADR-0002 contract, and may stub it so neither crew blocks the other.

Not a seam: `program/**` (platform, ADR-gated), `docs/prd/**` (product),
`.omni/**` (program). Where a milestone needs both seams, the ADR-0002 handoff is
the mechanism — the contract is published before it is consumed, and the consumer
may stub it meanwhile.

## Why this order

Two instructions from the program pull in different directions, and both are right,
so they resolve on different axes:

- **M1's purpose and demonstration is the liveness fix.** Both architects
  independently named the watchdog the most dangerous defect, and bug 3 has been
  confirmed on this box to have disabled *both* the watchdog timer and the
  state-backup timer — this run has had no dead-man's switch and no off-box state at
  all. M1 exists to end that.
- **M1's work order starts with the execution substrate.** Bug 5 first: nothing can
  be verified while half the org cannot execute a single tool call (ADR-0003). Then
  install determinism (1, 2, 3) — and bug 3 in particular, because until the user
  manager works the watchdog timer does not run at all, so a liveness fix would ship
  into a unit that never fires. Then bug 4, the liveness signal itself.

Codex's warning is why everything that depends on truthful liveness sits behind M1:
*"A seven-day soak test before this change is almost worthless: backup activity can
make a dead run appear healthy for the entire test."*

---

## M1 — The org can run, installs cleanly, and the dead-man's switch tells the truth

**Outcome (user-visible):** a fresh Ubuntu 24.04 box gets through provisioning and
bootstrap without a human editing a script; a box whose sandbox cannot actually run
is refused instead of passing clean; and a Program that has died is reported dead
within the stale window instead of being kept "healthy" forever by its own backup
timer.

**Exit criteria:** DoD **1, 2, 3, 4, 5**.

**Internal work order** (not five parallel tasks — this is a dependency chain):
**5** (sandbox proven by execution, ADR-0003) → **3** (user manager reachable, so the
timers exist at all) → **1, 2** (Go discovery under `pipefail`, both sites; absent Go
installs) → **4** (Program-owned liveness record, ADR-0002).

**Seam split:**

| Conditions | Seam | Crew | Principal files |
|---|---|---|---|
| 1, 2, 3, 5 | B | `crew_beta` | `deploy/provision-ubuntu.sh`, `deploy/bootstrap.sh`, `deploy/preflight.sh`, `deploy/lib/deploy-lib.sh`, `docs/TRUST-BOUNDARY.md`, `tests/test_provision.py`, `tests/test_bootstrap.py` |
| 4 — the **record** (writer identity, run id, host boot id, monotonic sequence, age) | A | `crew_alpha` | `bin/nightshift-ctl`, ctl-driving tests, `docs/CONTROL-PLANE.md` |
| 4 — the **consumer** (watchdog reads it by content; stops renewing slots when not alive; mutates no service) | B | `crew_beta` | `deploy/nightshift-watchdog.sh`, `tests/test_watchdog.py` |

Condition 4 is the one cross-seam condition in M1 and the reason ADR-0002 exists.
Seam A publishes the contract first; Seam B may stub it and build the watchdog
against the stub, so neither crew waits on the other. `docs/TRUST-BOUNDARY.md` is
single-writer (Seam B) with an edit lease for anyone else — decided in `.omni/decisions.md` A6.

**The demonstration** (what the owner runs to see a real bug fixed):

```bash
cd /srv/project && tests/run.sh -k watchdog     # today: NO TESTS RAN
bwrap --ro-bind / / /bin/true; echo $?          # today: 1 — and preflight still passes clean
```

plus the `## See it for yourself` one-liners that land with condition 17 — the
watchdog case being the headline: a fixture whose only fresh file is
`.omni/control-backup.json` is now reported **stale and alerting**, where today it is
reported healthy forever.

**Deliberately fakes or defers:**
- No container rehearsal yet; the fixes are proved by tests driving the real script
  functions. **Repaid in M2 (condition 11).**
- Seam B may consume a stubbed liveness record until Seam A publishes the real one.
  **Repaid inside M1** at the ADR-0002 handoff.
- Baseline-failure captures are produced in manifest shape as the work happens, but
  the manifest and validator that check them land in M2. **Repaid in M2 (conditions 8, 9).**

**With `release` and `qa` down:** buildable and reviewable, **not mergeable**.
Conditions 1–5 all anchor their evidence at the tip of `main`, so all five report
`BLOCKED` until the owner applies the AppArmor remediation in `.omni/blocked.md` and
`release` returns. The work does not wait; the exit gate does.

---

## M2 — Evidence stops being self-attested; a clean install proves itself

**Outcome (user-visible):** one command (`python3 tests/dod_validate.py`) answers
"is this project finished?" line by line, and a clean-source container rehearsal
proves the install path without a VPS.

**Exit criteria:** DoD **8, 9, 10, 11, 12**.

**Seam split:**

| Conditions | Seam | Crew | Principal files |
|---|---|---|---|
| 8, 9 (manifest, close-out validator, baseline records) | A | `crew_alpha` | `tests/dod_validate.py`, `docs/prd/DOD-MANIFEST.json`, `docs/audit/baseline/**` |
| 10 (CI triggers, required jobs, headSha equality) | A, with a `platform` lease | `crew_alpha` | `.github/workflows/tests.yml` — platform-owned, single-writer, ADR-gated |
| 11, 12 (clean-source rehearsal; preflight check manifest, bash-file manifest, `\|\| true` annotations) | B | `crew_beta` | `tests/linux/install-rehearsal.sh`, `deploy/preflight.sh`, `tests/test_preflight.py` |

`docs/prd/DOD-MANIFEST.json` sits under `docs/prd/**`, which `product` owns. Crews
propose rows through their PRs; `product` is the only writer of the DoD text itself.
The manifest is data, not the fixed point — that distinction is what keeps a renamed
test from becoming a DoD edit.

**Deliberately fakes or defers:**
- The rehearsal is a container, not a VPS: real login sessions and systemd-user
  behaviour are exercised through the same code path, not through a real login.
  **Permanent by design** (DoD "Explicitly NOT conditions").
- Network-dependent steps may be stubbed, but the decision logic under test must be
  real code and the header must declare each stub. **Not repaid — it is the contract.**
- Nothing about concurrency is examined yet. **Repaid in M3.**

**With `release` and `qa` down:** buildable and reviewable. Condition **9** and
**12** can report `PASS` from the working tree; **8, 10, 11** anchor at the tip and
report `BLOCKED`. Condition 10 is the sharpest case — it requires a *green run whose
`headSha` equals the current `main`*, which cannot exist until merges resume.

---

## M3 — One task, one worker, one budget — enforced, not described

**Outcome (user-visible):** the two duplicate-work defects found during discovery are
closed by enforcement rather than prose, and the owner has a signed answer to "can
two agents do the same task, and can the org wedge?"

**Exit criteria:** DoD **6, 7, 13**.

**Seam split:** entirely **Seam A / `crew_alpha`**. Two workstreams with disjoint
file scope inside the seam:

| Workstream | Conditions | Principal files |
|---|---|---|
| W3-A — audit and evidence (documents and tests only) | 13 | `docs/audit/CONCURRENCY.md`, `tests/test_claims.py`, `tests/test_leases.py` |
| W3-B — enforcement (code) | 6, 7 | `bin/nightshift-ctl`, `deploy/policies/omnipods_policies.py`, `tests/test_slots.py`, `tests/test_policy.py` |

W3-B's fix list depends on W3-A's verdicts for anything beyond the two already-confirmed
defects; the confirmed two start immediately, so the workstreams converge rather than serialize.

**Because M3 is single-seam, it can run concurrently with M4** (`crew_beta`), which
is single-seam in the other direction. That is the program's allocation call, not a
change to the ordering here: M3 must still *close* before M5, because condition 19's
denial tests build on M3's enforcement.

**Deliberately fakes or defers:**
- Condition 13 Q3's adversarial test covers exactly the enumerated acquisition orders
  — not a universal proof, which is not obtainable. **Not repaid; it is the honest bound.**
- Serialize leases must **not** gain TTL auto-release; recovery is an authorized,
  auditable transition. This deliberately leaves a slower recovery path in place
  rather than weakening a protected invariant.

**With `release` and `qa` down:** buildable and reviewable. Conditions **6, 7** anchor
at the tip → `BLOCKED`. Condition **13** is a document plus tests validated from the
tree → can report `PASS`.

---

## M4 — It survives days and crashes, and its boundaries hold

**Outcome (user-visible):** written, evidenced answers to "what happens when it
crashes, reboots, hits a rate limit, or runs for a week", plus proof that no
credential reaches an operator-visible sink and that the frozen paths are untouched
by this repository.

**Exit criteria:** DoD **14, 16, 17, 20, 21**.

**Seam split:** principally **Seam B / `crew_beta`**, with one cross-seam consumption.

| Workstream | Conditions | Seam | Principal files |
|---|---|---|---|
| W4-A — survival and install audit | 14, 17 | B (14 Q3 consumes Seam A's vendor-state semantics via ADR-0002) | `docs/audit/SURVIVAL.md`, `docs/audit/INSTALL.md`, `tests/test_recovery.py` |
| W4-B — boundaries, sentinels, frozen paths | 16, 20, 21 | B | `docs/audit/BOUNDARIES.md`, `docs/TRUST-BOUNDARY.md`, sentinel fixtures, `docs/HANDOVER.md` stub for 21's statement |

Condition 14 Q3 (rate limit versus hard outage, persisted timer identity, terminal
state) reads Seam A's vendor tables; Seam A publishes the state names, Seam B writes
the verdict. Condition 20's sink corpus must be the same list condition 16 Q1
enumerates — the audit and the test share one inventory by id, so growing the
inventory grows the test.

**Deliberately fakes or defers:**
- Live-vendor behaviour is **unobserved** while Codex seats are down, and condition
  14 Q3 must say so in those words rather than implying a live test.
- Condition 21's forward-looking frozen-path control is one of the two declared
  `MANUAL` exceptions: the environment supplies the evidence (read-only mount,
  audit rule, or snapshot). Historical non-modification is explicitly **not** claimed.
- Growth over 3–7 days is answered analytically (what grows, what bounds it), not by
  waiting. **Not repaid — elapsed-time observation was deleted from the DoD.**

**With `release` and `qa` down:** buildable and reviewable. Condition **20** anchors
at the tip → `BLOCKED`. Conditions **14, 16, 17** are documents validated from the
tree, and **21** is repo history plus the manual control → all four can report `PASS`.

---

## M5 — Enforcement is real, and the record closes

**Outcome (user-visible):** the review and release guarantees are shown to be
enforced or honestly recorded as unenforceable on this box; every confirmed finding
is closed; and a stranger can take the project over from one document.

**Exit criteria:** DoD **15, 18, 19, 22, 23, 24**.

**Seam split:**

| Workstream | Conditions | Seam | Principal files |
|---|---|---|---|
| W5-A — pipeline enforcement and protected-boundary denial tests | 15, 19 | A (`crew_alpha`) | `docs/audit/PIPELINE.md`, `tests/test_policy.py`, `tests/test_issue_semantics.py`, `tests/test_pr_create.py` |
| W5-B — closure, provenance, handover | 18, 22, 23, 24 | cross-seam, program/product-authored with crew-built validators | `docs/audit/FINDINGS.md`, `docs/HANDOVER.md`, `docs/prd/TRACEABILITY.md`, `.omni/tech-debt.md` |

**Deliberately fakes or defers:**
- Condition 22 gates on the workflow being **followed**, not on integration having
  happened: a PR queued behind an unavailable gate is compliant, a PR that skipped a
  gate is not. Deferring a gate is not weakening it.
- Half of condition 22 is self-attested and says so: every seat shares one `gh` PAT,
  so GitHub records one identity for author and reviewer alike. That is a confirmed
  finding (below), not a caveat, and it must appear in `docs/audit/PIPELINE.md` Q1
  with a `CONFIRMED-BUG` verdict.
- Complexity findings (16 Q4) are recorded, never acted on where removal would touch
  the brief's protected set. **Permanent — that deferral is the answer.**

**With `release` and `qa` down:** buildable and reviewable. Condition **19** anchors
at the tip → `BLOCKED`. Conditions **15, 18, 22, 24** are written to settle without
integration and can report `PASS`; **23** can report `PASS` only if its clean-clone
verification is run against the ref under test rather than `main`.

---

## Merge status today, in one table

The owner's action in `.omni/blocked.md` (the `bwrap` AppArmor profile) is the single
gate. Until it is applied, `release` and `qa` cannot run, nothing reaches `main`, and
every condition whose evidence is anchored **at the tip of `main`** reports `BLOCKED`.

| | Work completable now | Review completable now | Conditions `BLOCKED` until `release` returns |
|---|---|---|---|
| M1 | yes | yes (Claude reviewers) | 1, 2, 3, 4, 5 |
| M2 | yes | yes | 8, 10, 11 |
| M3 | yes | yes | 6, 7 |
| M4 | yes | yes | 20 |
| M5 | yes | yes | 19 |

**13 of 24 conditions cannot report `PASS` today**, none of them for want of work.
Review is completable but its **independence is not mechanically verifiable** (one
shared PAT), and `main` has **no branch protection** and cannot have it on this
repository's plan — both confirmed, both recorded in `.omni/tech-debt.md`, both bound
for `docs/audit/PIPELINE.md` Q1.

## Work carried that settles under a later condition

Three defects were confirmed after DoD v2 was fixed. None needs a new condition:
each is a `CONFIRMED-BUG` finding that closes under **condition 18**, which requires
an integrated fix plus a regression test plus a baseline record for every confirmed
bug. The milestone that does the work is named; the condition that settles it is 18,
in M5.

| Defect | Work lands in | Audit home | Settles under |
|---|---|---|---|
| Unattended policy returned ASK, parking the program for a human (`ask_timeout` 86400 s; owner issue #6) | **M2**, Seam A | `PIPELINE.md` Q2 (gate determinism) | 18 |
| `main` has no branch protection and cannot have it on this plan (403) — "release is the sole default-branch writer" is prose | **M5**, Seam A | `PIPELINE.md` Q1 | 18 |
| Review independence unverifiable — every seat shares one `gh` PAT | **M5**, Seam A | `PIPELINE.md` Q1 | 18, and self-attested in 22 |

## One proposal for the program — not a change I have made

The ASK defect is owner-observed, and it defeats the project's core outcome directly:
one ASK parks an unattended run for 24 hours. It is currently carried as a finding
under condition 18, which is correct and sufficient. If the program judges it belongs
in section A alongside the other seven confirmed defects — with its own condition, its
own `RT` ids and its own baseline record — that is a **DoD revision**, requiring a
changelog row in `docs/prd/DEFINITION-OF-DONE.md` written by `product`. I have not
made that change unilaterally: moving the fixed point without a recorded reason is
precisely what the change-control section forbids. Say the word and I will write it.

## Close-out (not a milestone)

When all 24 report `PASS`, the program runs the cross-milestone re-checks — condition
8's manifest completeness, 18's findings index, 22's per-change provenance, 24's
traceability matrix — and writes the final report. If a re-check fails, the owning
milestone reopens; the DoD is not amended to accommodate it.

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-08-22 | Initial plan, 5 milestones covering DoD v1 conditions 1–24. | `.omni/brief.md`; repository at `a73ba88`. |
| 2026-08-22 | **Re-mapped to DoD v2.** v1's mapping referenced conditions that no longer exist: the simulation platform (old 15–18) and the observation/drill (old 19) were deleted by the v2 revision, and four conditions were added. M1 now carries the five confirmed install/liveness defects (1–5) including the new `bwrap` bug, ordered substrate → install determinism → liveness per ADR-0003/ADR-0002 and the program's sequencing ruling. M2 carries evidence integrity and install proof (8–12); M3 the two duplicate-work defects plus the concurrency audit (6, 7, 13); M4 survival and boundaries (14, 16, 17, 20, 21); M5 enforcement and closure (15, 18, 19, 22, 23, 24). Added a seam split per milestone (`crew_alpha` = Seam A coordination, `crew_beta` = Seam B host integration), a per-milestone merge-blocked status, and a table of three post-v2 confirmed defects that settle under condition 18 rather than as new conditions. | `docs/prd/DOD-RECONCILIATION.md`; `docs/prd/DEFINITION-OF-DONE.md` v2 changelog; `.omni/decisions.md` A1–A11; ADR-0001/0002/0003; `.omni/tech-debt.md`; `.omni/blocked.md` (Codex seats down, `main` unprotected, one shared PAT). |
