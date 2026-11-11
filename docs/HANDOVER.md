# HANDOVER

Audience: a stranger picking this project up cold, with no access to any agent's
transcript. This document is meant to stand alone — everything it asserts is either
verified in the `## Verify` section below or linked to its evidence. It satisfies
DoD condition 23 (`docs/prd/DEFINITION-OF-DONE.md`, and its machine check `check_d23`
in `tests/dod_validate.py`) and folds in condition 21's narrowed frozen-path
statement (D21) per the owner's instruction on issue #205 (17:32Z) and issue #484's
DoR verdict comment.

This file is audit/documentation only. It changes no code and fixes nothing itself;
every fix it describes is a separate, already-filed change.

## What changed and why

Five confirmed install/liveness bugs (`docs/prd/DEFINITION-OF-DONE.md` §A, DoD
conditions 1–5) and one evidence-integrity condition (condition 8) are merged to
`main` as of this writing. `tests/dod_validate.py` at this doc's base commit
(`f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b`) reports `D01`–`D05` and `D09`–`D12`
`PASS`.

- **Conditions 1–2 — Go toolchain survives `set -o pipefail`, and a missing Go means
  "install", never "stop".** PR [#22](https://github.com/tushardhara/nightshift/pull/22)
  (`beta/8-go-toolchain`), merged 2026-08-23. Fixes both the version-discovery pipe
  (`curl | head -1 | sed`) and the checksum-lookup pipe (`curl | jq | head -1`) under
  `pipefail`, and makes a missing Go binary proceed to install rather than abort.
  Tests: `RT-01`, `RT-02`, `RT-03`, `RT-04`. The structural closure for the second,
  non-reproducible pipe site is `RT-23` (landed later, see below) — both sites are one
  fix, not two.
- **Condition 3 — unattended user services install without assuming `systemctl
  --user` already works.** PR [#18](https://github.com/tushardhara/nightshift/pull/18)
  (`beta/9-user-manager-capability`), merged 2026-08-23. The root provisioning step
  now establishes the systemd user-manager capability; the user-run bootstrap step
  probes it and fails naming the root remediation instead of reaching for `sudo` (the
  runtime account has none by design). Tests: `RT-05`, `RT-06`, `RT-07`.
- **Condition 4 — watchdog liveness is a Program-owned signal, validated by
  content.** PR [#24](https://github.com/tushardhara/nightshift/pull/24)
  (`beta/10-watchdog-liveness`) plus its control-plane prerequisite PR
  [#16](https://github.com/tushardhara/nightshift/pull/16) (`alpha/11-liveness-record`),
  both merged 2026-08-23. Liveness now reads an attributed record (writer identity,
  run id, host boot id, monotonic loop sequence, age) instead of `.omni/` mtime, and
  `slot renew-active`/`slot heartbeat` no longer renew while stale or for a
  non-holder. Tests: `RT-08`–`RT-12`, `RT-21`, `RT-22`.
- **Condition 5 — bubblewrap is probed by function, not presence.** PR
  [#15](https://github.com/tushardhara/nightshift/pull/15)
  (`beta/12-bwrap-functional-probe`), merged 2026-08-23. Replaced `command -v bwrap`
  with a functional probe (`bwrap --ro-bind / / /bin/true`) in `deploy/preflight.sh`,
  `deploy/provision-ubuntu.sh` and `deploy/bootstrap.sh`, and corrected
  `docs/TRUST-BOUNDARY.md`'s false claim that bubblewrap ships an AppArmor profile on
  24.04. Tests: `RT-13`, `RT-14`, `RT-15`. **See "Operator actions required" below —
  this is the live example of a fix whose code is done but whose host-level
  remediation is not automatic.**
- **Condition 1's latent second pipe site, closed structurally.** PR
  [#399](https://github.com/tushardhara/nightshift/pull/399) (issues #381/#392),
  merged 2026-08-30, wired `RT-23` (`BothSitesFixed`, a structural guard that no
  network producer in `deploy/provision-ubuntu.sh` feeds an early-closing consumer)
  to a real test class after six days with no open issue tracking it — see
  `.omni/ledger.md` 2026-08-30 ~13:15Z entry for the tracking-gap history.
- **Condition 8 — every required test bound to real evidence by a machine-readable
  manifest.** PR [#25](https://github.com/tushardhara/nightshift/pull/25)
  (`product/dod-v2-condition-8`), merged 2026-08-23, added the `preservation` record
  form (baseline pass + mutation evidence) and applied it to `RT-11`. **Queued, not
  closed**: `manifest.conditions.8.reviewed_by` is still unpopulated — see "confirmed
  still broken" below.
- **Admission-derivation control-plane fix.** PR
  [#473](https://github.com/tushardhara/nightshift/pull/473) (issue #471,
  `docs/decisions/ADR-0062-derive-dispatch-admission-from-control-plane-state.md`),
  merged 2026-09-01. Fixed one half of a two-part dispatch defect: derives
  worker-dispatch admission from control-plane state rather than a stale
  precondition. **The other half is issue #489 below, and is why this document was
  authored by the crew lead directly instead of through a dispatched engineer — see
  the note at the top of "Operator actions required".**

## Current operational state (Program checkpoint #2, 2026-09-01 ~19:38Z)

A prior checkpoint (PR #504, not yet merged — its content lives on that branch/PR, not on `main`
yet) recorded state as of ~18:42Z. This section supersedes it in currency.

**`main` is now `6626006`** (was `3fe45f1` at the last checkpoint). **7 real merges today:**
#496 (D23 HANDOVER.md), #499 (control-plane drift preflight, owner-merged), #500 (CI timeout
headroom), #497 (D15 PIPELINE.md), #493 (ADR-0066, heartbeat triage FIRST-not-ONLY), #505 (CI
per-SHA concurrency groups, owner-merged, amends ADR-0037 — main's post-merge verification runs
no longer cancel each other; path exclusions still explicitly rejected, nothing else changed).

**#487 and #494 both fully re-verified clean by their original reviewers, sent to `release`**
(tasks `integrate-487`, `integrate-502` — do NOT restart either fix, just waiting on
integration). #494: architect confirmed all 3 findings closed (one non-blocking test-coverage
note); security's independent re-check may still be in flight — check before treating fully clean.

**Owner-directed parallel batch in flight (5 tasks, disjoint files, dispatched ~19:37Z) — do NOT
restart any of these, resume by (agent,title) if a session drops:**
| Task | Agent/title | Lease | File |
|---|---|---|---|
| #478 (D14) | `crew_alpha`/478-build | 66 | docs/audit/SURVIVAL.md |
| #480 (D16) | `crew_alpha`/480-build | 67 | docs/audit/BOUNDARIES.md |
| #481 (D17) | `crew_alpha`/481-build | 68 | docs/audit/INSTALL.md |
| #482 (D18) | `crew_alpha`/482-build | 69 | docs/audit/FINDINGS.md |
| #483 (D19) | `product`/483-build (own file domain, EXEMPT, no WA-5 needed) | 71 | docs/prd/DOD-MANIFEST.json |

**DoD status:** 8/24 as of this checkpoint (was 6/24 at the last one) — D15 and D23 landed today.
Landing the batch above adds 5 more → 13/24.

**Still not started:** #485 (D24, depends on the 5 audit docs + #482 landing), #486 (D20, depends
on #480 — now in flight). #482's own FINDINGS.md content should pull real entries from #503
(F-1, from #477's audit) and #479's merged PIPELINE.md (F-101-F-104), not invent placeholders.

## What is confirmed still broken

**Snapshot warning.** The bullet list below was read from `python3 tests/dod_validate.py`
at this doc's base commit (`f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b`), where `D06`,
`D07`, `D08`, `D13`–`D22`, `D24` all reported `FAIL`. **That snapshot is now stale and
several bullets below describe work that has since landed.** Measured on 2026-09-02 at
`a83ca08`: **19 `PASS`, 2 `MANUAL` (`D08`, `D21`), 3 `FAIL` (`D06`, `D18`, `D22`)**. The
conditions-6/7 bullet and the worker-budget risk row have been corrected against that
measurement (see the changelog row of 2026-09-02 in `docs/prd/DEFINITION-OF-DONE.md`
touching condition 6); **the remaining bullets have not been re-verified by that edit and
are owed a refresh by this document's owner under condition 23** — treat any bullet not
named here as as-of-`f82559a`, not as-of-today. The machine-readable finding index this
condition points to, `docs/audit/FINDINGS.md`, **exists** (13,194 bytes; issue #482
**closed**) and is authoritative over this section wherever the two disagree.

- **Condition 6 — the global worker budget (`RT-16`/`RT-17`).** `D06 FAIL`, but no longer
  for the reason this bullet used to give. The *code* fix landed on 2026-08-21 (`0945f07`,
  "slots are one-dispatch capabilities; semaphore fails closed"), and the `RT-16`/`RT-17`
  test classes and baselines are in flight in PR
  [#585](https://github.com/tushardhara/nightshift/pull/585). What is outstanding is
  **STEP 2**: writing the `RT-16`/`RT-17` bindings into `docs/prd/DOD-MANIFEST.json`, which
  still carries `test_node_id: null` for both — that, not this PR, is what flips `D06`.
  STEP 2 must also bind
  `tests/test_authority_fence.py::SlotAcquireOncallTokenGate::test_denied_for_a_fenced_out_generation`
  to `RT-17`, so that id's "live, current-generation authority token" clause is earned
  rather than asserted. Tracked as issue
  [#507](https://github.com/tushardhara/nightshift/issues/507) (open). **What these ids
  will still not show** is in *Accepted risks* below, under "Program-vs-lead producer
  routing" — read it before treating a green `D06` as a statement about the running system.
- **Condition 7 — duplicate dispatch — is DONE, not broken.** `D07 PASS`; issue
  [#508](https://github.com/tushardhara/nightshift/issues/508) is **closed**; `RT-18`,
  `RT-19` and `RT-34` are bound with baselines under ADR-0068. The text this bullet
  replaced listed conditions 6 and 7 together as "not yet started; no issue is currently
  filed", which was stale on both halves: an issue exists for 6, and 7 has landed.
- **Condition 8's manual exception.** `manifest.conditions.8.reviewed_by` is unset —
  the `RT-11` preservation record has not yet been claimed by a cross-crew reviewer.
- **Conditions 13–17 — the five audit documents.** None of
  `docs/audit/CONCURRENCY.md`, `SURVIVAL.md`, `PIPELINE.md`, `BOUNDARIES.md`,
  `INSTALL.md` exist yet. Tracked as issues
  [#477](https://github.com/tushardhara/nightshift/issues/477) (D13, CONCURRENCY),
  [#478](https://github.com/tushardhara/nightshift/issues/478) (D14, SURVIVAL),
  [#479](https://github.com/tushardhara/nightshift/issues/479) (D15, PIPELINE),
  [#480](https://github.com/tushardhara/nightshift/issues/480) (D16, BOUNDARIES),
  [#481](https://github.com/tushardhara/nightshift/issues/481) (D17, INSTALL) — all
  open as of this writing.
- **Condition 18 — the finding index itself.** `docs/audit/FINDINGS.md` does not
  exist. Tracked as issue [#482](https://github.com/tushardhara/nightshift/issues/482)
  (open); this doc depends on it and #477–#481 landing first (per #484's own DoR:
  "sequence it last, after the other nine").
- **Conditions 19–20 — manifest fields for protected-capability and sentinel-node
  proof.** `docs/prd/DOD-MANIFEST.json` declares neither `protected_capabilities`
  (issue [#483](https://github.com/tushardhara/nightshift/issues/483), D19) nor
  `sentinel_test_node` (issue [#486](https://github.com/tushardhara/nightshift/issues/486),
  D20 — depends on #480's sink corpus).
- **Condition 22 — workflow-followed manifest.** `manifest.changes` is empty; no
  issue is separately filed, this is folded into the eventual manifest-completion
  work alongside D19/D20.
- **Condition 24 — traceability matrix.** `docs/prd/TRACEABILITY.md` does not exist.
  Tracked as issue [#485](https://github.com/tushardhara/nightshift/issues/485)
  (open, D24), depends on #477–#482 landing first.
- **Dispatch mechanism, two separate open defects (both live and current):**
  - Issue [#489](https://github.com/tushardhara/nightshift/issues/489) (open):
    `sys_session_send`'s own declared tool schema rejects `slot_id`/`task_id`/
    `generation`, the fields the root `worker_semaphore` policy requires on every
    counted-agent dispatch — confirmed live on 2026-09-01 against a properly
    admitted task (issue #474 / PR #487). This is very likely the root cause behind
    the long run of WA-5 "self-perform" substitutions across this repo's history
    (PRs #450, #461, #462, and **this PR**). PR #473/ADR-0062 above fixed a related
    but distinct admission-*derivation* defect at the control-plane layer; #489 is
    the remaining half, in the dispatch tool's schema itself.
  - Issue [#490](https://github.com/tushardhara/nightshift/issues/490) (open):
    `deploy/exact_sha_release.py`'s `prepare()` requires the legacy supervision
    units to be `LoadState=loaded`; after the first successful exact-SHA cutover
    those units are permanently masked and, once unmasked, come back
    `not-found`, not `loaded` — reproduced live on this host 2026-09-01. A second
    cutover through the reviewed mechanism is currently impossible on this host.
  - Issue [#236](https://github.com/tushardhara/nightshift/issues/236) (open, P0):
    the umbrella tracking issue for the crew-internal dispatch blockage that #489
    narrows to its exact mechanism.

## Accepted risks

Full register with triggers: `.omni/tech-debt.md` (owned by `staff`; every row
carries a named trigger, not just a description).

**Currency scoping — read this before trusting a row.** This section previously claimed
its rows were current "today" with nothing recording when any of them was last checked.
Rows re-measured on 2026-09-02 are marked ***measured 2026-09-02***; **a row without that
marker has not been re-verified since this document's base commit `f82559a` and should be
read as as-of-then, not as-of-today.** Two rows below were found to be **false as
statements about the repository and true as statements about this host** — the fix is in
`origin/main` and is not what `/srv/nightshift` runs — so a risk row here is a claim about
*which side*, and any row that does not say which side it means is under-specified. That
distinction is the live theme of #495 and is not a documentation nicety. This scoping is
deliberately **not** folded into the general HANDOVER refresh ticket
[#613](https://github.com/tushardhara/nightshift/issues/613): the stale bullets in *What is
confirmed still broken* sit in a section now labelled stale, whereas these rows sit in a
section that claims to be current, which is the more dangerous of the two.

Verified in this pass: the worker-budget row, the duplicate-dispatch row, the
producer-routing row (new), `main` branch protection (live `403`, "Upgrade to GitHub Pro",
re-run 2026-09-02) and the `slot consume` CAS row (re-read at `origin/main`:
`cmd_slot_consume` still writes neither `task_admissions` nor `tasks.state='building'`).
**Not re-verified in this pass, and therefore as-of-`f82559a`:** the two shared-identity
rows (review independence; receipt authorship) and the sub-agent truncation row.

The rows most relevant to operating this project:

- **Worker budget is not global — in the DEPLOYED control plane; the in-repo defect is
  fixed.** `worker_semaphore` no longer binds crew leads only: `0945f07` (2026-08-21,
  "slots are one-dispatch capabilities; semaphore fails closed") is on `main`. The
  arithmetic this row used to carry — *"documented 4, true concurrency ~9"* — was measured
  before the owner raised `.omni/capacity.json` to `max_concurrent_workers: 10`,
  `reserve_for_oncall: 1`, `max_items_in_flight: 12` alongside
  `spawn_bounds.max_dispatches_per_turn` 4→8 on 2026-09-02 (PR #533), and it is retired
  here because it now contradicts DoD condition 6's own body. **The risk is live in
  operation, not in the repository:** the installed `/srv/nightshift/bin/nightshift-ctl` is
  **1073** lines against `origin/main`'s **6828** — both sides measured 2026-09-02, the
  repository side at `fa93374`. (This row first carried *6147* for the repository side;
  that figure is `e06b1d9`, 12 commits back on this file and 127 behind `origin/main`
  overall, and it was **inherited rather than measured** when the row was rewritten. The
  true gap is larger than the row claimed, not smaller.) **Line counts drift with every
  merge, so the durable form of the same fact is structural:** the installed binary exposes
  **15 subcommand groups against the repository's 29**, missing `authority`, `dod`,
  `review`, `bookkeeping`, `heartbeat-persist`, `project`, `schedule`, `beat`, `pr-head`,
  `worktree`, `guard-commit`, `hook`, `spec` and `project-snapshot`. **Stronger still, the
  installed side is pinned by identity rather than by a date or a byte count:** both
  `bin/nightshift-ctl` and `deploy/policies/omnipods_policies.py` under `/srv/nightshift`
  are **byte-identical to `a73ba88`** (`cmp`, 2026-09-02), this project's 2026-08-22
  baseline, and differ from `origin/main`. So what this host runs is the baseline-era
  control plane, and **nothing merged after `a73ba88` is deployed** — including ADR-0062's
  admission derivation, which is the behaviour `RT-16`/`RT-17` assert (issue
  [#495](https://github.com/tushardhara/nightshift/issues/495), open). *Stated precisely,
  because an earlier draft of this row implied otherwise:* `0945f07` **predates** `a73ba88`
  and **is** present in the installed file; the deployed gap is everything after the
  baseline, not that commit.
  *Trigger:* any counted dispatch succeeding on this host before #495's cutover is
  verified, or vendor cooldowns firing more often than the 10/1 arithmetic predicts.
  *This is condition 6/`RT-16`, above.* *Both sides re-measured 2026-09-02.*
- **Program-vs-lead producer routing is unproven, and `RT-16`/`RT-17` cannot prove it.**
  DoD condition 6's evidence stops at the CLI/policy seam. `RT-16` shows the gate denies
  the two *schema-reachable* bypass forms; `RT-17` shows that acquires which reach the CLI
  converge on one atomic budget with the on-call reserve fenced. **Neither shows that the
  deployed harness routes every real producer through that gate.** Normal slot allocation
  is deliberately not caller-identity keyed, so there is no producer-class axis in the
  normal acquire path to observe, and at that seam "program" versus "lead" survives only as
  a task-name substring (ADR-0069 §5, verified independently by `staff` and `crew_beta`).
  Only root `guardrails.policies` load (issue #118, closed), so this is external harness
  state with no in-repo binding, and it is deliberately given **no `RT` id** — DoD
  condition 8's binding rule requires a file in this repository and there is none.
  **A green `D06` therefore means this repository's budget is one budget and its gate
  refuses un-slotted dispatch; it does not mean every deployed producer has been observed
  reaching it.** *Trigger:* #495's cutover landing, or
  [#534](https://github.com/tushardhara/nightshift/issues/534)'s generation-aware cutover
  succeeding — either makes the deployed dispatch path observable, and this must then be
  re-verified by an integration probe rather than assumed; and before then, any counted
  worker starting on this host with no corresponding row in `slot list`. *Owner:*
  #495/#534. *Stated in:* `docs/prd/DEFINITION-OF-DONE.md`, condition 6, "Not claimed by
  `RT-16`/`RT-17`". *Measured 2026-09-02, and this is the entailment rather than a
  restatement:* the installed evaluator takes `slot_arg="slot_id"` and denies any dispatch
  whose `args` lack `slot_id` — the field ADR-0062 records the dispatch tool's schema
  **cannot carry**, being `additionalProperties: false` — and it exempts exactly one seat,
  `("pm",)`, against the repository's eleven (`architect`, `beta_pm`, `crew_alpha`,
  `crew_beta`, `platform`, `pm`, `product`, `release`, `security`, `staff`, `ux`). The
  repository's evaluator reads no `args.slot_id` anywhere and derives the slot from
  control-plane state instead. **The gate `RT-16` asserts is therefore demonstrably not the
  gate this host is running.**
- **Duplicate-dispatch prevention is prose, not enforcement — in the DEPLOYED control
  plane; the in-repo defect is fixed.** As written before 2026-09-02 this row said flatly
  that "the policy never consults `task_claims`" and that "`claim release`/`claim renew`
  take no `--claimant`". **Both halves are false of the repository and were already false
  at this branch's merge-base** — `crew_beta` checked them against the binary rather than
  inferring, and re-measurement here confirms it on both sides:

  | claim | repository (`origin/main`, `fa93374`) | installed (`/srv/nightshift`) |
  |---|---|---|
  | `claim release --claimant` | **required** (`usage: … --task TASK --claimant CLAIMANT`) | **absent** |
  | `claim renew --claimant` | **required** | **absent** |
  | policy consults `task_claims` | **yes**, 2 references | **no**, 0 references |

  So this is the **same shape as the worker-budget row above**, and the same root cause:
  condition 7 is *done* in the repository (`D07 PASS`, issue #508 closed, `RT-18`/`RT-19`/
  `RT-34` bound under ADR-0068), and the defect survives only in what this host actually
  runs, because the installed control plane predates the fix (#495). The row is therefore
  **re-grounded, not retired** — deleting it would have discarded a live operational risk
  on the strength of a repository-side fix that is not deployed. *Trigger:* any observed
  double-dispatch of one task id — and, checkable today in one command,
  `/srv/nightshift/bin/nightshift-ctl claim release --help` offering no `--claimant` while
  the repository's requires it. *This is condition 7/`RT-18`; the "condition 7 is DONE"
  bullet above is about the repository, this row is about the host.*
  *Both sides measured 2026-09-02.*
- **`main` has no branch protection** (GitHub plan limitation) — the "release is the
  sole default-branch writer" invariant is prose, not a mechanism GitHub enforces.
  *Trigger:* already fired once (three program commits reached `main` directly,
  issue #6); enforcement has to come from a pre-push hook or agent-layer policy, not
  GitHub. *Re-run 2026-09-02: `gh api repos/tushardhara/nightshift/branches/main/protection`
  still returns `403 "Upgrade to GitHub Pro or make this repository public"`. Measured
  2026-09-02.*
- **Review independence is not mechanically verifiable** — every seat authenticates
  through one shared `gh` PAT, so GitHub records one author/reviewer identity.
  *Trigger:* already accepted as a stated boundary in DoD condition 22's own text.
- **Persisted review receipts (ADR-0039) cannot prove WHICH agent posted them** —
  same shared-identity root cause as above, one layer up. *Trigger:* per-seat GitHub
  credentials existing.
- **`slot consume`'s CAS bind is a stale pre-ADR-0033 half-write** — it flips
  `dispatched_at` but never writes `task_admissions` or `tasks.state='building'`.
  *Trigger:* the first use of `slot consume` outside a test fixture on a
  `phase='new'` slot. *Re-read at `origin/main` 2026-09-02: `cmd_slot_consume` still
  contains zero references to `task_admissions` and zero to `state='building'` — the row
  stands. Measured 2026-09-02.*
- **Sub-agent results can be silently truncated in transit** (~24.7k character cut
  observed, no error surfaced). *Trigger:* any org artifact that is a long document
  rather than a diff — workaround in use is chunked resend with
  `[CONTINUES]`/`[END]` markers.

## Operator actions required

**Read this section before assuming any fix above is "done" on your own box** — code
correctness and host state are two different questions, and this project answers
only the first for several of these.

- **The bwrap/AppArmor gap (condition 5) is the live example of that split.** The
  code fix (PR #15) makes every preflight/provision/bootstrap script probe bwrap by
  *function* — `bwrap --ro-bind / / /bin/true` — not by presence, and fail loudly
  with a named cause and remediation command (`deploy/lib/deploy-lib.sh`,
  `nightshift_bwrap_diagnosis`) instead of silently passing a box where every Codex
  seat then dies at its first tool call. **That probe result depends on the actual
  host and is not fixed by this project**: on a stock Ubuntu 24.04 box with
  `kernel.apparmor_restrict_unprivileged_userns=1` (the default) and no bwrap
  AppArmor profile, the probe still fails (exit 1) until an operator, as root,
  writes an AppArmor profile granting `userns` to `/usr/bin/bwrap` and reloads it:
  ```
  # /etc/apparmor.d/bwrap must grant userns to /usr/bin/bwrap, then:
  apparmor_parser -r /etc/apparmor.d/bwrap
  bwrap --ro-bind / / /bin/true; echo $?   # must print 0
  ```
  Run the functional probe yourself as the runtime user (not root — root can create
  user namespaces even when the box-wide restriction denies them to everyone else,
  so a root-run probe can pass on a box where every worker still dies). If the
  restriction is off or a profile is already present, no action is needed; check
  first rather than assuming.
- **Dispatch through a sub-agent may currently be sterile on this host.** Issues
  #489/#490 (above) mean a crew lead's own `sys_session_send` to a counted-agent
  sub-agent (`eng_claude`, `eng_codex`, `qa`, `reviewer_*`) can be denied by tool
  schema validation even with a legitimately acquired slot, and this deployment's
  served snapshot may be lagging `origin/main`'s own fixes (see `nightshift-ctl
  dump`'s `spec.diverged` field). Working agreement **WA-5**
  (`.omni/working-agreements.md` item 5) is the standing control for this: a crew
  lead whose worker dispatch is sterile builds directly under a slot it holds, states
  that substitution explicitly in the PR body, and treats cross-crew review as
  mandatory rather than customary — exactly what **this PR** does. Do not silently
  retry dispatch in a loop; confirm #489/#490's status first.
- **No reinstall or root remediation is otherwise required by anything this project
  has changed.** The Go-toolchain, user-manager and watchdog-liveness fixes
  (conditions 1–4) all take effect on the next normal install/bootstrap run; none of
  them require a manual host step beyond what the scripts themselves already do.

## Rollback notes

Each merged PR above is a single, reviewed change on `main`; reverting any one is a
plain `git revert` of its merge commit, independent of the others (they touch
disjoint files: `deploy/provision-ubuntu.sh`/toolchain code, `deploy/bootstrap.sh`
user-manager probing, watchdog liveness code plus its control-plane prerequisite,
and the three bwrap-probe call sites plus `docs/TRUST-BOUNDARY.md`).

- **PR #22 (Go toolchain)**: revert restores the old `curl | head -1 | sed` /
  `curl | jq | head -1` shape and the "missing Go = abort" behavior. No data
  migration; safe to revert alone.
- **PR #18 (user-manager capability)**: revert removes the root-side capability
  establishment; bootstrap reverts to assuming `systemctl --user` already works.
  Any host already fixed by a completed provisioning run keeps its working user
  manager — reverting the script does not undo an already-established capability.
- **PR #16 + PR #24 (liveness record + watchdog)**: revert together (#24 depends on
  #16's schema). Reverting drops the attributed liveness record and returns
  freshness to `.omni/` mtime — re-opens the "dead Program stays healthy forever"
  defect this pair closed. No destructive data loss; the control-plane liveness
  table is additive.
- **PR #15 (bwrap functional probe)**: revert restores `command -v bwrap`
  presence-only checks and un-corrects the `docs/TRUST-BOUNDARY.md` claim. Safe to
  revert alone; does not affect any host's actual AppArmor state either way (see
  operator actions above — that state is never automated by this project).
- **PR #25 (condition 8 preservation record)**: revert removes the `preservation`
  evidence form and `RT-11`'s mutation captures; DoD condition 8 would need a
  different route to close `RT-11`.
- **PR #473 / ADR-0062 (admission derivation)**: revert restores the prior
  precondition-based admission check. Independent of #489 (tool-schema issue),
  which is not fixed by this PR or by reverting it.
- **This PR (docs/HANDOVER.md)**: revert is a no-op on running behavior — this
  change is documentation only and touches no code path.

## Links

- Definition of Done: `docs/prd/DEFINITION-OF-DONE.md`
- DoD manifest: `docs/prd/DOD-MANIFEST.json`
- Milestones: `docs/prd/MILESTONES.md`
- Tech-debt register: `.omni/tech-debt.md`
- Working agreements (WA-5 substitution rule): `.omni/working-agreements.md`
- Trust boundary (corrected by condition 5's fix): `docs/TRUST-BOUNDARY.md`
- Admission-derivation ADR: `docs/decisions/ADR-0062-derive-dispatch-admission-from-control-plane-state.md`
- Per-seat policy probe (referenced by the tech-debt register): `docs/audit/118-per-seat-policy-probe.md`
- Audit documents not yet written: issues
  [#477](https://github.com/tushardhara/nightshift/issues/477),
  [#478](https://github.com/tushardhara/nightshift/issues/478),
  [#479](https://github.com/tushardhara/nightshift/issues/479),
  [#480](https://github.com/tushardhara/nightshift/issues/480),
  [#481](https://github.com/tushardhara/nightshift/issues/481)
- Findings index not yet written: issue
  [#482](https://github.com/tushardhara/nightshift/issues/482)
- Traceability matrix not yet written: issue
  [#485](https://github.com/tushardhara/nightshift/issues/485)
- Manifest field gaps: issues
  [#483](https://github.com/tushardhara/nightshift/issues/483),
  [#486](https://github.com/tushardhara/nightshift/issues/486)
- Dispatch-mechanism defects: issues
  [#489](https://github.com/tushardhara/nightshift/issues/489),
  [#490](https://github.com/tushardhara/nightshift/issues/490),
  [#236](https://github.com/tushardhara/nightshift/issues/236)
- This file: `docs/HANDOVER.md`

## Frozen paths (DoD condition 21)

Neither `/srv/nightshift` nor `/home/nightshift/nightshift-runtime` was modified by
this project. This repository's own tracked git history (`git log --name-only
--all`) contains no file living at either path. Both paths carry owner authority
(the second was named in the owner's dispatch on issue #205 rather than in
`.omni/brief.md`); DoD condition 21's own text is explicit that no `git log` inside
this repository can prove a write to a path *outside* it never occurred, and does
not claim otherwise — the forward-looking manual control (a read-only mount,
filesystem audit rule, or before/after snapshot) is supplied by the environment
separately and recorded in `docs/prd/DOD-MANIFEST.json`'s
`conditions."21".frozen_path_control` field.

## Verify

The commands below are read-only, deterministic, and safe against a bare clone —
they do not touch `.omni/` runtime state, run services, or need credentials. They
deliberately do **not** invoke `tests/dod_validate.py`'s full registry, because that
registry includes this document's own D23 check, which itself clones the repo and
re-runs this section — invoking it from inside itself would recurse.

```bash
set -euo pipefail

# The three deploy entrypoints this document describes still parse.
bash -n deploy/provision-ubuntu.sh
bash -n deploy/preflight.sh
bash -n deploy/bootstrap.sh
bash -n deploy/lib/deploy-lib.sh

# Condition 5's fix stays fixed: no reintroduction of the corrected false claim
# that bubblewrap ships an AppArmor profile on 24.04.
if grep -q "AppArmor profile on 24.04" docs/TRUST-BOUNDARY.md; then
  echo "FAIL: stale AppArmor claim reintroduced in docs/TRUST-BOUNDARY.md" >&2
  exit 1
fi

# This document satisfies its own required-content check (DoD D23, condition 23).
lower=$(tr '[:upper:]' '[:lower:]' < docs/HANDOVER.md)
for phrase in "what changed and why" "confirmed still broken" "accepted risk" "operator action" "rollback"; do
  case "$lower" in
    *"$phrase"*) ;;
    *) echo "FAIL: docs/HANDOVER.md missing required phrase: $phrase" >&2; exit 1 ;;
  esac
done

# This document states both frozen paths verbatim (DoD condition 21).
grep -q "/srv/nightshift" docs/HANDOVER.md
grep -q "/home/nightshift/nightshift-runtime" docs/HANDOVER.md

echo "HANDOVER verify: OK"
```
