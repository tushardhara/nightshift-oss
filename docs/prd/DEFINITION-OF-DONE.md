# Definition of Done — Nightshift Self-Dogfood

**Version 2** (revised 2026-08-22 after the blind cross-vendor critique;
see `docs/prd/DOD-RECONCILIATION.md` and the changelog at the bottom).

**There are 24 conditions.** The project is finished when all 24 report `PASS`,
and not before. It does not end when the audits feel complete, and it does not
continue because someone thought of a twenty-fifth thing — that is a follow-up
project (see **Change control**).

v2 is not a lower bar than v1; it is a smaller and harder one. It deletes a
simulation platform, an elapsed-time observation, an operator drill, a throughput
target and a test-count target — all of which were ceremony — and replaces them
with defects that are confirmed, evidence that is machine-checked, and two
honestly-named manual exceptions.

---

## How a condition is settled

**One validator, not twenty-four commands.** `tests/dod_validate.py` (stdlib
Python 3, no dependencies) is the close-out gate. Run:

```bash
cd /srv/project && python3 tests/dod_validate.py
```

It prints exactly one line per condition — `D01 PASS`, `D07 FAIL <reason>`,
`D22 BLOCKED <reason>`, `D21 MANUAL <pointer to the recorded evidence>` — and exits
non-zero if any condition is not `PASS` or a declared `MANUAL` with its evidence
present. Each condition below names the check the validator performs; the
commands quoted are what the check runs, so a stranger can run any single one by
hand.

- **`BLOCKED` never counts as met.** Unreachable GitHub, a dead vendor seat, or a
  missing tool yields `BLOCKED` with a reason. The project is not finished with a
  `BLOCKED` condition outstanding.
- **`MANUAL` is allowed for exactly two conditions** — 8 (production-path
  confirmation by the cross-crew reviewer) and 21 (environment-supplied frozen-path
  evidence). Both are named in the manifest. No third may be added without a
  changelog row.
- **"At the tip of `main`"** means: `gh api repos/tushardhara/nightshift/commits/main --jq .sha`
  equals the `headSha` of the most recent completed run of `.github/workflows/tests.yml`
  on `main` (`gh run list --workflow tests.yml --branch main --limit 1 --json headSha,conclusion,databaseId`),
  that run's `conclusion` is `success`, and every job named in the manifest's
  `required_ci_jobs` concluded `success`
  (`gh run view <databaseId> --json jobs --jq '[.jobs[]|{name,conclusion}]'`).
- **Requirement IDs, not test names.** Required tests are referenced as `RT-01…RT-nn`.
  `docs/prd/DOD-MANIFEST.json` binds each `RT` id to a concrete test node id, the
  production `file:symbol` it drives, and its baseline record in whichever of the
  two forms condition 8 admits (`regression` or `preservation`). Renaming a
  test is a manifest edit, not a DoD edit — the requirement is the evidence, the
  name never was. **IDs are allocated monotonically and are never renumbered or
  reused.** `RT-01`…`RT-20` happen to run in condition order; from `RT-21` onward the
  sequence is **allocation-ordered, not condition-ordered**, because renumbering an id
  already bound to a capture would silently invalidate that evidence. Read the
  condition to learn which ids it requires; never infer it from the number.
- **Evidence must be commit-qualified.** A citation is `path` + symbol + SHA (or a
  GitHub permalink), never a bare `path:line` — line numbers drift the moment a fix
  lands.
- **`docs/architecture/CAPACITY.md` may propose, it cannot bind.** If capacity
  arithmetic contradicts anything here, it is raised as a proposed revision and takes
  effect only after a changelog row below. A downstream artifact does not get to move
  the fixed point silently.

## Verified baselines and assumptions

Facts this file relies on, each labelled. Verified facts carry the command and the
output; assumptions carry what breaks if they are wrong.

| # | Fact | Status |
|---|---|---|
| B1 | Test baseline at `a73ba88`: `cd /srv/project && tests/run.sh` → `Ran 83 tests in 18.084s … OK` | **Verified** 2026-08-22 by `product` on the run host. |
| B2 | `bwrap --ro-bind / / /bin/true` exits **1** with `bwrap: setting up uid map: Permission denied` on this host | **Verified** 2026-08-22. This is confirmed bug 5 (condition 5). |
| B3 | Repository is `github.com/tushardhara/nightshift`, default branch `main`, `gh` authenticated with scopes `repo, workflow, project` | **Verified** 2026-08-22 (`git remote -v`, `gh auth status`). |
| B4 | Day-one scale: one Ubuntu 24.04 VPS, `.omni/capacity.json` = 4 workers / 1 on-call reserve / 6 items in flight, 2 crews (both architects independently returned seam count 2) | **Verified** for the file; **assumption** for the box spec. If wrong, no condition changes — no condition depends on a throughput number. |
| B5 | Every Codex seat is down on this host (`architect`, `release`, `eng_codex`, `qa`, `reviewer_codex`), so nothing can currently merge to `main` | **Stale as of 2026-09-02** — verified true on 2026-08-22, but `.omni/blocked.md` records the seats RESOLVED at 2026-08-23T10:49Z, and `release` has since merged 300+ PRs (`git log --merges a73ba88..HEAD` → 305; **not**
  `--first-parent`, which counts only 219 over the same range — corrected 2026-09-02, same
  revision, see changelog). Condition 22 still gates on the workflow being followed, not on integration having happened, so this correction does not relax it — it only retires the specific justification for why a PR might legitimately be queued rather than merged. See the 2026-09-02 changelog row. |
| B6 | Fixes here take effect on the **next** install; the running controller is frozen and is not upgraded by this project | **Assumption.** If wrong, condition 21's forward control and a restart runbook are needed; raise as a revision. |
| B7 | Both frozen paths (`/srv/nightshift`, `/home/nightshift/nightshift-runtime`) carry owner authority; the second was named in the owner's dispatch rather than in `.omni/brief.md` | **Assumption of record**, ruled by the program 2026-08-22. |
| B8 | No new runtime dependency: bash + coreutils + python3 stdlib, matching `tests/run.sh` and a CI with no install step | **Assumption.** If a fix needs a package it stops being the smallest safe fix; raise as a revision. |

---

## A. Confirmed defects — five install/liveness bugs and two duplicate-work defects

Every condition in this section requires three things, checked by the validator:
an **integrated fix**, a **required test** that passes at the tip, and a
**baseline record proving the property's pre-fix state** — for a `regression`
`RT` id, that its test **failed** at the pre-fix commit; for a `preservation`
`RT` id (condition 8's second evidence form — as of 2026-08-23 the only
section-A instance is `RT-11`), that its test **passed** pre-fix *and* carries
the mutation evidence condition 8 requires. Which form applies is **not a
choice**: condition 8 derives it from the capture's `exit_code`, and no
condition in this section may rest on `preservation` records alone.
Nothing here is speculative: 1–4 are the owner's reported failures, 5 was
reproduced on this box (B2), 6 and 7 are confirmed in `.omni/tech-debt.md`.

**1. Go version discovery survives `set -o pipefail` — at both sites.**
`deploy/provision-ubuntu.sh` resolves the Go version through `curl … | head -1 | sed`,
whose consumer closes the pipe early; the same shape recurs in the checksum lookup
(`curl … | jq … | head -1`). Fixing only the first leaves a latent instance.
**The two sites do not fail identically, and this condition does not claim they do.**
At site A the abort is reproducible with real data. At site B it is not: `jq` reads to
EOF, so `curl` never receives SIGPIPE; the early close lands on `jq`, which only dies if
it outwrites the 64 KB pipe buffer. Measured against the live go.dev index, every
filename appears exactly once, `jq` emits ~65 bytes, and ≥100 duplicate entries would be
needed. Site B is therefore a **latent** instance of the shape — the word this condition
already used — and its **reachable** defect is the same *consequence* as site A's: on a
failed request `pipefail` poisons the substitution and `set -e` kills the script before
its own refusal diagnostic, so an operator sees a bare non-zero exit instead of
"refusing to install an unverified toolchain". **The fix is unchanged and closes both
sites.**
*Required tests:* `RT-01` (version discovery under `set -euo pipefail` with a
producer cut off by its consumer resolves a version and exits 0); `RT-02` (the
checksum lookup resolves under the same options, and on a failed request the refusal
diagnostic is **reached and printed** rather than swallowed by `set -e`); `RT-23`
(structural: no network producer anywhere in `deploy/provision-ubuntu.sh` feeds an
early-closing consumer, and `pipefail` is not disabled anywhere to dodge the problem).
`RT-23` is what proves the **latent** instance closed, because no data-driven test can;
it carries an ordinary `regression` baseline, both pipelines being present pre-fix. Its
number is out of condition order by design — ids are allocation-ordered from `RT-21` on.
Each test names its isolation mechanism and touches no host
path. *Validator check:* all three `RT` ids pass at the tip, zero skips, each has a
baseline record with non-zero exit and captured output; `bash -n deploy/provision-ubuntu.sh` exits 0.

**2. A missing Go toolchain means "install Go", never "stop".**
*Required tests:* `RT-03` — with no Go present, the version probe returns cleanly
under `set -euo pipefail` and provisioning proceeds to the install decision;
`RT-04` — a genuinely unresolvable version still fails loudly, naming
`NIGHTSHIFT_GO_VERSION`. The fixture must be isolated such that the test can
neither read nor write the host `/usr/local/go`, and the manifest records how.
*Validator check:* as condition 1.

**3. Unattended user services install without assuming `systemctl --user` already works.**
The observed failure was `Failed to connect to bus: No medium found` after
`su - nightshift`. The **outcome** is what is required, not a mechanism: on a host
where linger and the user manager are not already established, the install either
establishes that capability in its root-run step, or the user-run step exits
non-zero naming the root remediation. It must not reach for `sudo` from the runtime
user, which has none by design.
*Required tests:* `RT-05` (capability absent → user services end up installed and
enabled, by whichever step the architecture assigns), `RT-06` (capability
unobtainable → non-zero exit whose message names the root command), `RT-07`
(re-run is a no-op). *Named state, so "half-installed" is decidable:* the six units
`omnigent-server.service`, `omnigent-host.service`, `nightshift-state-backup.{service,timer}`,
`nightshift-watchdog.{service,timer}` — on the failure path none of them may be left
enabled, and the validator asserts the test checks exactly that set.

**4. Watchdog liveness is a Program-owned signal, validated by content.**
Today freshness is the newest mtime under `.omni/`, while
`deploy/nightshift-state-backup.sh` writes `.omni/control-backup.json` every 30
minutes on its own timer — so a dead Program stays healthy forever, and the
watchdog keeps calling `nightshift-ctl slot renew-active`, so leaked worker slots
never expire. Registry, ledger and `STATUS.md` writes are **not** an acceptable
replacement signal: a worker or a timer produces those too.
*Required behaviour:* liveness is decided from a record the Program itself writes,
carrying **writer identity, run id, host boot id, a monotonic loop sequence and an
age**, read by content — not mtime, and not a substring match on `STATUS.md`;
backup health is a separate signal with a separate owner.
*Required tests:* `RT-08` a fixture whose only recent writes are the backup file,
timer output and the watchdog's own marker is judged **stale** and alerts;
`RT-09` a fixture with a valid Program record is judged **alive**; `RT-10` while
stale, `slot renew-active` is **not** invoked; `RT-11` the watchdog attempts **no**
service-management mutation — asserted through an instrumented command boundary
that fails on any such attempt, not by grepping for one spelling of `systemctl`;
`RT-12` the alert fires at most once per re-alert window and re-arms after recovery;
`RT-21` the Program's own control-loop write path **emits** a well-formed attributed
record — schema version, run id, Program session identity, timestamp, host boot id,
strictly increasing loop sequence — written atomically, so that `RT-09`'s "valid
record" is one production code produces rather than one a fixture hand-wrote
(ADR-0002 §2); `RT-22` `slot heartbeat` renews **only** for the verified holder and
denies a non-holder, so that narrowing or deleting `slot renew-active` relocates no
unverified-renewal path onto the verb that replaces it (ADR-0002 §4a).
*Naming honesty:* what `RT-08`/`RT-12` establish is an **alert attempt recorded**.
Delivery to the phone is a separate property, tested as configuration
(a status issue is resolvable and the comment command is well-formed), never claimed
as proof of notification.

**5. Bubblewrap is probed by function, not by presence.** *(Fifth confirmed bug, issue #5.)*
`deploy/preflight.sh` (`binaries` loop), `deploy/provision-ubuntu.sh` (the mandatory
bwrap check) and `deploy/bootstrap.sh` (the versions block) all test `command -v bwrap`.
On stock Ubuntu 24.04 with `kernel.apparmor_restrict_unprivileged_userns=1` and no
shipped `bwrap` AppArmor profile, bubblewrap is installed and **cannot run** — every
Codex seat dies at its first tool call, and such a box passes preflight clean (B2, B5).
*Required behaviour:* the functional probe `bwrap --ro-bind / / /bin/true` (exit 0)
replaces the presence check in all three scripts; preflight **FAILs** — not warns —
with a diagnosis naming the unprivileged-userns / AppArmor cause and the remediation
reference; and the false, load-bearing claim in `docs/TRUST-BOUNDARY.md` that
bubblewrap "ships an AppArmor profile on 24.04" is corrected.
*Required tests:* `RT-13` (probe failure → preflight non-zero with the diagnosis),
`RT-14` (probe success → no failure raised), `RT-15` (no remaining
presence-only bwrap check in `deploy/`, by shebang-manifest scan).
*Validator check:* the three `RT` ids pass; `grep -n 'AppArmor profile on 24.04' docs/TRUST-BOUNDARY.md`
returns nothing.

**6. The worker budget is global.** *(Confirmed: `.omni/tech-debt.md`, 2026-08-22.)*
`worker_semaphore` binds crew leads only; program-level dispatches are capped only by
`spawn_bounds: max_dispatches_per_turn = 8` (owner-raised from 4 on 2026-09-02
together with `.omni/capacity.json` `max_concurrent_workers` 5 -> 10, so the harness
bound does not become the new ceiling), so true concurrency exceeds the documented
and configured budget. The budget the constitution calls "one global semaphore"
is not one, and raising the numbers does not fix that -- D06 stays red until every
dispatch path consumes a slot.
*Required behaviour:* every dispatch path that starts a worker consumes a slot from
the same budget in `.omni/capacity.json`; the on-call reserve remains unreachable by
normal acquires.
*Required tests:* `RT-16` (a program-level worker dispatch without a live, bound,
unconsumed slot is denied), `RT-17` (independently produced concurrent acquires converge
on one atomic budget: the number granted at once never exceeds
`max_concurrent_workers − reserve_for_oncall`, no normal acquire reaches the reserve even
at saturation, and the reserve is reachable only by an on-call acquire presenting a live,
current-generation Program authority token).
*Not claimed by `RT-16`/`RT-17`:* that the harness routes every real producer through the
gate and the budget these tests exercise. The program-vs-lead producer distinction is not
observable at this seam — normal slot allocation is deliberately not caller-identity keyed,
so `RT-17`'s corpus varies real crew, agent and task facts but cannot witness a producer
*class*; and `RT-16` shows the gate denies the schema-reachable bypass forms, not that the
harness offers only those forms. That routing property is external harness state, is a
deployment concern tracked under #495/#534, and is deliberately given **no `RT` id**:
condition 8's binding rule requires a file in this repository and there is none, so an id
for it could never be satisfied. It is carried instead as an `ACCEPTED-RISK` with a
trigger, in `docs/HANDOVER.md`'s *Accepted risks* register under the row "Program-vs-lead
producer routing is unproven, and `RT-16`/`RT-17` cannot prove it" — whose trigger is
#495's cutover landing or #534's generation-aware cutover succeeding, at which point the
deployed dispatch path becomes observable and must be re-verified rather than assumed. A green D06 therefore means
*this repository's budget is one budget and its gate refuses un-slotted dispatch* — it does
not mean every deployed producer has been observed reaching it.

**7. Duplicate dispatch is enforced, not described.** *(Confirmed: `.omni/tech-debt.md`, 2026-08-22.)*
The policy never consults `task_claims`, and `claim release`/`claim renew` take no
`--claimant`, unlike leases — so "at most one active claim per task" does not bind the
dispatch path, and any holder can release another's claim.
*Required behaviour:* at most **one authorized execution generation per task** —
a second execution requires an explicit, recorded recovery transition; `claim release`
and `claim renew` require and verify `--claimant`.
*Required tests:* `RT-18` (dispatching a task whose active claim belongs to another
claimant is denied), `RT-19` (a non-holder cannot release or renew a claim),
`RT-20` (sequential re-execution without a recorded recovery generation is refused;
with one, it is permitted and the generation is recorded).

---

## B. Evidence integrity

**8. Every required test is bound to real evidence by a machine-readable manifest.**
`docs/prd/DOD-MANIFEST.json` exists and contains, for each `RT-nn`: the concrete test
node id; **the DoD condition it evidences**; the production **binding** the test drives —
`file:symbol` where the production artifact defines an addressable symbol, and `file`
alone where it does not (a flat shell script may define none — see the Changelog row of
2026-09-02 for what this file's own example artifact currently defines and which `RT` ids
that reaches). Which form applies is a **measured property of the artifact, not a
preference**, and **no production code is refactored to manufacture a symbol** — a
documentation rule does not get to edit a script that is under review. And a baseline
record `{baseline_sha, command, exit_code, output_path}` where `output_path` points to a
non-empty capture under `docs/audit/baseline/` and `baseline_sha` is an ancestor of `HEAD`.
Every baseline record carries a `kind`, which is **computed from the capture, never
chosen:**

- **`kind` is derived, and therefore optional in storage.** `exit_code != 0` → `regression`;
  `exit_code == 0` → `preservation`. The validator computes the label from the capture
  whether or not the record states it; a record that omits `kind` is not defective, and a
  record that *declares* it must agree with its own `exit_code` — disagreement is a hard
  `FAIL`. A computed label cannot be relabelled.
- **`regression` — `exit_code != 0`.** The test failed against the pre-fix commit, for the
  reason the condition names. This is the default and covers every behavioural fix. No
  mutation record is required: the pre-fix commit *is* the mutant, and a real one.
- **`preservation` — `exit_code == 0`.** The test pins a property that already held and that
  the fix must not break. A passing baseline is honest evidence and is captured as such;
  manufacturing a failure to fit the regression form is falsified evidence and is itself a
  condition-8 violation. Because a passing baseline discriminates nothing, the record must
  additionally carry `mutations`: a **non-empty array** (a lone object is read as a
  one-element array) of
  `{patch_path, applied_to_sha, command, exit_code, output_path}`, where —
  - `patch_path` is a patch under `docs/audit/baseline/` that applies cleanly to
    `applied_to_sha`, **not** to "the tip": a patch perishes the moment a context line
    drifts, and the tip moves. `applied_to_sha` is an ancestor of `HEAD`.
  - **the files the patch touches — read out of the patch itself — are a subset of the
    production files declared for this `RT` id**, so the mutation breaks the production path
    and not the test, the fixture or the detector. This is derived from the patch: there is
    no field to fill in, nothing to keep in sync, and it works for an artifact with no
    symbols.
  - `command` is byte-identical to the baseline `command`, so the same assertion judges both
    runs; `exit_code != 0`; `output_path` is a non-empty capture of that failing run.
  - the mutated code is **never committed to a mergeable branch** — the patch is evidence,
    applied to a scratch tree, captured, and reverted.

  **One mutant is a floor, not a quota.** Where a mutant cannot reach a path the test
  asserts over — an early return, an unreachable branch — another mutant must cover it, or
  that path carries no proof and the record self-attests exactly what this rule exists to
  kill. `RT-11`'s `M1` (alert path) and `M2` (alive path, unreachable from `M1`) are the
  worked example.

The manifest also lists every condition, its kind (`auto`/`manual`) — a different field from
a baseline record's derived `kind` above — and `required_ci_jobs`.
*Validator check:* every `RT` id referenced in this file appears in the manifest; every
manifest test runs at the tip, passes, and reports **zero skips**; every baseline record
satisfies the constraints above; every named production file exists and is referenced by
the test file; every `RT` id declares the condition it evidences; any `kind` present agrees
with its `exit_code` (`!=0` ⇔ `regression`, `==0` ⇔ `preservation`); every `preservation`
record carries at least one well-formed `mutation` entry meeting the constraints above; and
no condition in section A is evidenced by `preservation` records alone — each retains at
least one `regression` `RT` id, resolved through the per-`RT` condition edge above.
*Manual exception (1 of 2):* that a test genuinely exercises the production path rather
than a mock of it is confirmed by the cross-crew reviewer and recorded in the manifest as
`reviewed_by` with the PR/review URL. The validator checks the field is populated and
resolvable; it cannot check the judgement. For a `preservation` record the same reviewer
additionally attests, in the same `reviewed_by` field, that (i) the property is one the fix
could plausibly have broken; (ii) each mutant is a **minimal** break of *that* property,
rather than of the detector or of something incidental sharing the same file; and (iii) the
mutants **together cover every path the test asserts over**. Clause (iii) is the one a
validator cannot reach: it can count mutants and check each one, but only a reader can see
that a branch went unmutated. This extends the existing exception; it does not create a
third.

**9. One close-out validator reports every condition independently.**
`tests/dod_validate.py` exists, is stdlib-only, and emits exactly one line per condition
`D01…D24` with status `PASS | FAIL | BLOCKED | MANUAL`, a reason for anything that is not
`PASS`, and a non-zero exit unless every condition is `PASS` or a declared `MANUAL` with
its evidence present. It declares exactly the two manual exceptions named in this file
(conditions 8 and 21) and fails if the manifest declares a third.
*Settled by:* `python3 tests/dod_validate.py; echo $?` — and by the validator's own
self-check `python3 tests/dod_validate.py --self-test`, which asserts that each condition
check fails when its evidence is removed (a validator that cannot fail proves nothing).

**10. CI runs the required jobs, on the right triggers, against the current tip.**
*Validator check:* `.github/workflows/tests.yml` triggers on `pull_request` for every
path and on `push` to `main` (statically parsed, not grepped), so a feature branch runs
once when it enters review and the integrated result runs once more; every job named in the manifest's
`required_ci_jobs` exists; and the most recent completed run on `main` has `conclusion:
success`, `headSha` equal to the current `main` SHA, and `success` for each required job.
`BLOCKED` if GitHub is unreachable.

---

## C. Install reliability

**11. The install rehearsal runs from a clean source of the exact tested commit.**
`tests/linux/install-rehearsal.sh` consumes a `git archive` (or equivalent) of the
commit under test, unpacked into an empty container workspace — not a bind mount of the
working tree, which would carry untracked files and local state. It asserts the source
tree is clean, that `/srv/nightshift` is absent, and prints one `PASS:` line per
confirmed-bug path (conditions 1, 2, 3, 5). Its header declares, per step, whether the
step is hermetic or requires network, and exactly which steps are stubbed; the decision
logic under test must be real code, never a stub.
*Validator check:* the script exists, its header contains the hermetic/networked/stubbed
declaration, and the rehearsal CI job (a `required_ci_job`) concluded `success` for the
tip. Locally: `docker run --rm -v "$PWD":/src:ro ubuntu:24.04 bash -c 'set -e; …'` as
documented in the header.

**12. Every preflight check is enumerated, tested both ways, and actionable.**
A manifest section lists every check in `deploy/preflight.sh` **as it exists at close-out**
(the condition does not reach into future branches), each with: id, severity
(`FAIL`/`warn`), a positive fixture and a negative fixture, and a remediation reference.
Each `FAIL` branch must carry actionable diagnosis (what failed, why, where the
remediation is written) and, **where one safe deterministic command exists**, that exact
command — the `bwrap` case in `.omni/blocked.md` is the model.
*Also:* `deploy/preflight.sh` exits non-zero if and only if at least one `FAIL` was
printed; `bash -n` passes over the shebang-identified bash file set (not "every file in
`deploy/` and `bin/`" — `bin/nightshift-ctl` is Python); and no `|| true` added by this
project in an install-critical path lacks a parsed exception annotation.

---

## D. Audits that end

Each audit document carries a fenced ```json record with one entry per question:
`{"id":"Qn","verdict":"CONFIRMED-BUG|SOUND|ACCEPTED-RISK|OUT-OF-SCOPE","evidence":[…],"findings":["F-nnn"]}`,
where evidence items are typed `permalink` (path + symbol + SHA), `command`
(command + captured output path) or `test` (`RT` id). Prose sits below the record and is
for humans. *Validator check, applied to each:* ids are exactly `Q1…Qn` with no
duplicates and none missing; exactly one verdict per question from the closed set;
evidence non-empty and of an allowed type; every `F-nnn` resolvable in the findings index.

**13. `docs/audit/CONCURRENCY.md` — duplicate work, deadlocks, claims, leases, slots (Q1–Q5).**
Q1 Can two workers execute the same task without an authorized recovery transition?
Q2 Does every dispatch path consume the one global budget?
Q3 For every code path that holds two or more of {slot, claim, lease, sequence,
checkpoint}: the acquisition order and the release trigger, as a table — plus a bounded
adversarial test over exactly those enumerated orders. (An unbounded "is there any
order…" is not answerable; the enumeration is.)
Q4 For each hold type, the **authorized, auditable** recovery transition when its holder
dies. Serialize leases must not gain TTL auto-release — the recovery path is a verified
drop/reassignment or an explicitly recorded break.
Q5 Does `worker_semaphore` fail closed on every dispatch path, including re-dispatch
after a denial?

**14. `docs/audit/SURVIVAL.md` — crash/restart, vendor limits and exhaustion, multi-day operation (Q1–Q4).**
Q1 `kill -9` of a worker, a lead and the Program (each, mid-wave): what state is lost,
what reconstructs from where, and is any task lost or duplicated?
Q2 After a reboot, does the first heartbeat reconcile without re-dispatching in-flight
work? Answer includes a **state→owner invariant table by lifecycle state** (queued,
in-flight, recoverable, dropped, integrated) compared by stable task id and execution
generation.
Q3 Rate limit versus hard outage: the state transitions, the persisted timer/cooldown
identity, maximum retries, and the terminal state — observed on persisted rows and a
resume attempt across the timer boundary in a fixture. It must state plainly that
live-vendor behaviour is unobserved while Codex seats are down (B5), and record that
`nightshift-ctl vendor` cannot currently express a non-expiring outage.
Q4 Over a 3–7 day run, what grows monotonically (disk, journal, worktrees, `control.db`,
transcripts, `omni/state` history) and what bounds each; and which single points of
silence remain (heartbeat lost, scheduler wedged, session dead, watchdog timer disabled,
box off) and which are now detected.

**15. `docs/audit/PIPELINE.md` — GitHub/PR/review workflow, release gates, coverage (Q1–Q4).**
Q1 For different-crew review, self-review prohibition and release-only-merge: where is
each **enforced**, and where is it merely instructed?
Q2 Which release gates are deterministic (same inputs → same verdict) and which rest on
model judgement alone?
Q3 Issue-close semantics, and whether branch protection requiring human approval would
deadlock every merge — and whether preflight actually detects that configuration.
Q4 Coverage map: each control-plane and deploy safety invariant with the `RT` id that
pins it or a recorded gap; plus the environment-dependent/flaky inventory, including the
recorded sub-agent transport truncation defect.

**16. `docs/audit/BOUNDARIES.md` — security boundaries and unnecessary complexity (Q1–Q4).**
Q1 The credential **sink inventory**: every source→sink path (process logs, journald,
alert bodies, PR and issue comments, CI logs, agent transcripts), with a verdict each.
Q2 Each claim in `docs/TRUST-BOUNDARY.md` verified against the code as it stands —
including the bwrap AppArmor claim already known false (condition 5).
Q3 Can a worker escape its declared write scope by a documented path (docker socket,
floci's socket mount, `writable_roots`, symlinks into `$HOME`)?
Q4 Which controls are redundant with another **named** control and could be removed, and
which complexity is load-bearing and must never be removed. Nothing in the brief's
protected set may be proposed for removal.

**17. `docs/audit/INSTALL.md` — deployment and install reliability (Q1–Q4).**
Q1 Root cause of each of the five confirmed install/liveness bugs, commit-qualified.
Q2 What else depends on a pipeline that can SIGPIPE, an interactive session, a login
shell, a capability probed by presence rather than function, or a file a fresh box may
not have?
Q3 Are `provision-ubuntu.sh` and `bootstrap.sh` idempotent against their own header
claims, over the paths this project touched? (A false claim in the artifact is a defect;
this is scoped to what we changed, not a blanket rewrite.)
Q4 Do failure paths give actionable diagnosis plus a remediation reference — and an exact
command wherever one safe deterministic command exists?
*Plus* a `## See it for yourself` section: one copy-pasteable command per confirmed bug
that visibly demonstrates the fixed behaviour on any Linux box with bash and python3.

**18. Every confirmed finding is closed, and the index says how.**
`docs/audit/FINDINGS.md` carries a machine-readable index, one record per finding:
`{"id":"F-nnn","source_doc":…,"verdict":…,"disposition":…}`.
*Rules the validator enforces:* ids unique, and **resolvable from the audit documents**
— an id is checked against its own `source_doc`'s findings index, not merely trusted
because it appears self-consistent inside this file alone; every `CONFIRMED-BUG`
disposition is an **integrated fix** — the `RT` id it cites must resolve to a real,
baseline-valid entry in `docs/prd/DOD-MANIFEST.json`, and the PR it cites must resolve to
a PR condition 22 actually checked (tracked in the manifest's `changes` list, merged or
gate-queued per condition 22's own verdict); **only** `ACCEPTED-RISK` may point to
`.omni/tech-debt.md`, and then only to a row carrying a trigger and an owner. No dangling
links, no empty dispositions.
*Not machine-checked, by honest necessity:* whether a given `ACCEPTED-RISK` disposition is
actually a confirmed violation of the brief's protected set, mislabelled to dodge condition
19. Nothing in this record's shape marks a finding as "about" a protected capability, so
there is no field to check it against — this is a human judgement, made by the audit's
author and re-made by the cross-crew reviewer on every disposition, the same reader-only
judgement clause (iii) of condition 8's manual exception already concedes for a different
record shape. **This is not a third manual exception** (condition 9 permits exactly two,
and fails if a third is declared): condition 18 stays fully automatic PASS/FAIL; this one
clause of it is enforced by review, not by this file. (Corrected 2026-08-22 wording that
read this clause as validator-enforced — see the 2026-09-02 changelog row.)

---

## E. Non-regression

**19. Every protected boundary still denies what it denied, proven behaviourally.**
The brief's protected set — worker limits, ownership leases, task claims, review
requirements, credential boundaries, sandboxing, branch protections, release gates — is
enumerated in the manifest as capabilities, each with a test asserting the denial still
happens (dispatch without a slot denied; non-holder release denied; PR body with a
closing keyword denied; reviewer seat read-only; only `release` may write the default
branch; semaphore fails closed with no database). **No ADR waives an invariant.** An ADR
may explain a change; it cannot make a weakened control pass this condition.
*Validator check:* every enumerated capability has a passing denial test at the tip, and
the count of enumerated capabilities is not lower than at v2 adoption.

**20. No credential material reaches any operator-visible sink.**
Unique sentinels are injected at every credential source (`~/.config/omnigent/server.env`,
the Postgres password, `GH_TOKEN`, harness credential files) in a fixture install, and
asserted absent from the enumerated sink corpus of condition 16 Q1 — process output,
journald capture, watchdog alert body, backup output, preflight output, PR/issue comment
bodies produced by the tooling, and CI logs.
*Validator check:* the sentinel test passes at the tip and the sink corpus it scans is the
same list the audit enumerated (compared by id, so an audit that grows the inventory grows
the test).

**21. The frozen paths are out of this repository, and stay out for the remainder of the run.**
*What is claimed:* `git log --name-only --all` in `/srv/project` contains no path under
`/srv/nightshift` or `/home/nightshift/nightshift-runtime` as a tracked file in this
repository's history; and `docs/HANDOVER.md` states that neither path was modified by
this project. **(Narrowed 2026-08-28 — see changelog.)** The original second clause here
— "no committed script, unit, test or document in this repository writes to either path
(shebang-manifest scan for both literals in a write position)" — is retired as
unsatisfiable-as-written: `deploy/bootstrap.sh`, `deploy/provision-ubuntu.sh` and
`deploy/preflight.sh` predate this project and legitimately write to `/srv/nightshift` as
their designed first-install target on any fresh box (measured:
`deploy/provision-ubuntu.sh:360`, `install -d -o "$NS_USER" -g "$NS_USER" /srv/nightshift
/srv/project`) — a property this condition was never trying to forbid. What this condition
actually protects — that nothing *this project adds or changes* reaches into the owner's
already-running frozen instance — has no static-grep proof (a script naming the path is not
evidence of reaching into a live one), which is exactly why the manual exception below
exists.
*What is explicitly NOT claimed:* that no write ever occurred. No `git log` inside this
repository can observe writes to a path outside it, and absence from a ledger is not
evidence. Asserting otherwise would put an unprovable statement in the fixed point.
*Manual exception (2 of 2):* a forward-looking control for the remainder of the run — a
read-only mount, a filesystem audit rule, or a before/after snapshot — is supplied by the
environment and recorded in the manifest as `frozen_path_control` with its evidence.
The validator checks the field is populated and its evidence file exists.
**(Discharged 2026-09-02 — see changelog.)** The control of record is
[`docs/audit/FROZEN-PATH-CONTROL.md`](../audit/FROZEN-PATH-CONTROL.md): a committed
baseline manifest of both frozen roots (content hash per file, plus each root's git
`HEAD`) and the re-runnable command that recomputes it, so any later write shows up as a
`diff`. The other two shapes were ruled out as unavailable to any seat here, measured, not
assumed: neither frozen root is a mount point (both on `/`, `/dev/sda1`), `auditctl` is not
installed, and no seat has passwordless `sudo`. Three things follow, and are part of the
condition rather than of the artifact: (a) the control is **detective, not preventive** —
it cannot stop a write, only make one impossible to complete unnoticed; (b) the running
controller's own live state directory
(`/home/nightshift/nightshift-runtime/.omni/**`) is **excluded** from the baseline, because
the frozen instance writes there continuously by design, so a write into *that* directory
is a named, accepted blind spot; (c) the control counts only if it is re-run — at every
close-out attempt, before and after any provisioning or deploy rehearsal, and once at final
handover with the result recorded in `docs/HANDOVER.md`. A `manual` verdict from
`check_d21` that nobody re-ran is not a satisfied condition.
*Both paths carry owner authority;* the second was named in the owner's dispatch rather
than in `.omni/brief.md` (B7).

---

## F. Workflow and handover

**22. Every change followed the workflow — gated on followed, not on merged.**
B5's Codex-seats-down state has since resolved (`.omni/blocked.md`, 2026-08-23) and
`release` has merged hundreds of PRs since — the vendor-outage case this condition was
written under is no longer the common case, but the condition does not relax because of
that: it still gates on the workflow being followed, not on integration having happened,
for the rare PR that genuinely is waiting on a currently-unavailable gate. Deferring a
gate is not weakening it: such a PR queues **behind** the gate and drains when it returns.
This condition requires, for every change this project produced: a branch and an open or
merged PR; a body that passes `bin/nightshift-ctl pr-lint`; a **recorded review** by a
different crew and a different vendor than the author — in **either** the shape GitHub's
API calls a Review object, **or** ADR-0039's actual persisted-receipt mechanism: a fenced
`` ```nightshift-review `` JSON block a PR comment carries, in the exact shape
`bin/nightshift_review_receipts.py` itself accepts (closed key set, valid JSON, `verdict`
in `{pass, fail}`, `domain` one of its seven canonical labels) — **not** a prose comment
that merely reads like a review, which is different evidence and is not accepted for a PR
merged **after** `bin/nightshift_review_receipts.py` (the module implementing this
mechanism) itself landed — commit `cb8d68fc`, 2026-08-28T09:50:53Z, the same commit that
adds ADR-0039's own document, so its acceptance and the tool's landing are one event. A PR
merged **at or before** that exact timestamp is grandfathered to the weaker bar (a prose
comment naming the reviewing crew/vendor and a verdict, or a GitHub Review object) — the
strict mechanism could not possibly have existed yet when it merged, and demanding it
retroactively would make this condition permanently unpassable for historical merges
through no fault of theirs. **This is a timestamp comparison, not a calendar-date one:**
the module landed mid-day, so several real PRs (#234, #235, #237, #239, #241, #243) merged
earlier the same day, before the tool existed at all — a date-truncated comparison
wrongly ungrandfathers them. Reusing an already-accepted, already-integrated public module
read-only like this does not modify its owning crew's code path, so this needed no new ADR
and no sign-off from that crew; and the deterministic gates run with their results
attached.
**A fourth form — a RETROACTIVE structured review attestation — is accepted, for one closed set
of already-merged PRs only, and it is the weakest thing on this list.** Numbering them in the order introduced above
— the validator's own comments use *form (3)* and *form (4)* for the last two, and this
extends that numbering to the rest rather than inventing a second scheme: **(1)** a GitHub
Review object; **(2)** an ADR-0039 `` ```nightshift-review `` receipt;
**(3)** withdrawn before it ever merged, see the next paragraph; **(4)** a fenced
`` ```nightshift-retro-review `` block. **The pre-`cb8d68fc` grandfathered prose bar is
deliberately left unnumbered:** it is a dated fallback for merges that predate the mechanism,
not a form anyone may choose today. So this condition accepts **three** forms — (1), (2) and
(4) — and `check_d22` has **four** acceptance paths, the fourth being that fallback. Count them
that way; the two numbers are not a discrepancy.
Form (4) is accepted only when **all three** of its
bounds hold, and **no one of them suffices** — that is the whole design, not a
belt-and-braces flourish. `_has_retro_review_form` requires the three together, and ADR-0075
records them in the same terms: *"three independent bounds, all required together"*.
**(a) Eligibility, by ancestry through a pinned commit:** the PR's merge commit must be an
ancestor of `FORM_4_ELIGIBLE_THROUGH`, the tip at which the review corpus was audited
(`_pr_is_form_4_eligible`). **This is the bound that makes the form retroactive rather than
merely posterior** — merging more work moves `main` and never moves the pin, so the eligible
set is closed and cannot grow. The pin is not a constant anyone can quietly advance:
ADR-0075 carries its value and its reversal trigger (*"the pin must never be advanced to make a
newer PR attestable"*), and `test_the_pin_matches_the_unique_accepted_adr_that_carries_one`
binds the code constant to that ADR, so moving it means superseding an accepted decision rather
than editing a line. **(b) Posteriority, measured on GitHub's clock:** the attesting comment's
own `createdAt` must be strictly after `mergedAt`, and a comment carrying no `createdAt` files
nothing — no clock, no evidence. **`filed-at` is a required field the filer declares** so that a
human reading the artifact sees a date in it; **it is not the control, and nothing in this
condition rests on it.** `security` measured why: a value the filer types is not evidence about
when it was typed. **(c) The reviewed SHA is one of the PR's own commits:** reachable
from the PR's head **and not** reachable from `mergeCommit^1`, both halves asked of git and
both required to answer — `_sha_is_one_of_this_prs_own_commits` returns False rather than
raising when git cannot say. Each of the three fails closed the same way, and for the same
reason: an unanswered question is not a satisfied bound, so an unavailable git degrades to
*no evidence* and can only make this condition stricter, never falsely pass it.
**Underneath all three, the claim must be an explicit fenced block parsed as a unit**,
carrying `reviewer-seat`, `reviewed-sha`, `verdict`, `filed-at` and one
fixed `attests` sentence, and it **fails closed**: a missing, duplicated, unknown or empty
field, a junk line, two blocks in one body, a lowercase or unknown verdict, an unknown seat, an
abbreviated or uppercase SHA, a timestamp with no timezone, or a paraphrased assertion each
yield *no evidence at all*, never partial credit. None of the three bounds carries the form on
its own. **(a)** alone is satisfied by every PR in the audited corpus **by construction** — it
bounds *which* PRs may be attested, and says nothing about whether one was reviewed.
**(c)** alone is insufficient because
`security` measured that an author's own *"Rebased onto `<sha>`; program bookkeeping only, CI
PASS"* — naming a genuine commit of the PR's own — still satisfies it with no intent to attest
at all (`tests/test_dod_validate_d22.py`, `test_architects_four_adversarial_cases_are_all_refused`).
And a perfectly-formed block filed after any merge may name any SHA in the world.
**Form (4) is weaker than a live receipt and this file will not pretend otherwise.** It is an
attestation **filed after the fact by whoever typed it**. It carries no control-plane-minted
marker, it is written after the review rather than during it, and it cannot establish that the
filer is not the author — every seat shares one GitHub PAT, ADR-0039's own named threat-model
boundary, tracked as F-102. **It does not prove a review happened**; it proves a named seat put
its name to the claim that one did, in a shape ordinary PR prose cannot produce by accident.
Three further properties hold that weakening in place — these are containment, not the three
bounds above. **It is retroactive by construction, and bound (a) is what makes that true:**
eligibility runs through ancestry of the pin, so a form can only ever speak about a merge that
was already inside the audited corpus. **It cannot displace the live receipt path on new work,**
because a PR merged after the pin is not eligible at all and merging more work never enlarges
the eligible set — which is why the bound is ancestry through a fixed commit and not an ordering
between two timestamps. An unmerged PR cannot be attested either, for the plainer reason that it
has no merge commit to test. **It is consulted last,** only once every stronger form has declined — the Review object, the
ADR-0039 receipt, and, for a PR still inside the grandfather window, the prose convention — so
the population it counts is exactly the one resting on an attestation and nothing else. **And it is
announced in the verdict, not merely documented here:** the condition reports `PASS (retro: N
of these rest on a RETROACTIVE form (4) attestation, which is weaker than a live ADR-0039
receipt (…))`, in the same wording on the FAIL side as on the PASS side, and a corpus with no
forms filed stays a bare `PASS` — so the clause keeps carrying information instead of becoming
boilerplate a reader learns to skip.
**The accepted evidence forms were unchanged by the 2026-09-02 revision that withdrew form (3),
and that was deliberate.** A
third form — a "SHA-bound structured review comment" (reviewed SHA + reviewing seat +
verdict token) — was implemented in the first round of task `520-d22-validator-defects`
and **removed before merge**, on a `MERGE_BLOCKER` raised independently by `architect` and
`security`. Its stated compensating control was that the reviewed SHA had to resolve to a
commit reachable from the PR's own head; that control does not exist. Reachability proves
**ancestry, not review**: one constant string naming this condition's own baseline commit
— which every PR in range descends from by construction — satisfied the entire form for
**216 of 216** in-range PRs. Worse than forgeability, the form required no semantic
relationship between its three signals, so an author's own "Rebased onto `<sha>`; CI PASS"
satisfied it **with no intent to attest at all**. A rule that manufactures false `PASS`es
without needing a bad actor has no place in the one condition whose purpose is proving
review happened. Any future third form must bind the SHA to the PR's **own** commits
(reachable from the head but **not** from `mergeCommit^1`) **and** carry an explicit
structured review assertion; neither remedy alone is sufficient. Recorded here rather than
in the changelog alone, because the next attempt at this will start by reading this
paragraph.
**Form (4) is that next attempt, and the two forms must not be read as the same thing.** This
paragraph withdraws form (3); it does not forbid form (4). It set the reintroduction conditions
quoted immediately above, and form (4) is the form built to meet them — so a reader who arrives
here from the paragraph above is looking at a specification that was satisfied, not a
prohibition that was overridden. Three differences are checkable in the artifact rather than
taken on trust. **First, the `mergeCommit^1` exclusion:** form (3) asked only whether the SHA
was reachable from the head, which every in-range PR's shared baseline commit satisfies by
construction — that is the 216-of-216 result above. Form (4) additionally requires the SHA to be
*unreachable* from `mergeCommit^1`, which no pre-existing baseline commit can be, so the single
constant string that defeated form (3) scores zero against form (4) at every real head.
**Second, the explicit assertion sentence:** form (3) accepted three signals co-occurring
anywhere in a comment with no semantic relationship between them, which is why an author's
rebase note satisfied it; form (4) requires one fixed sentence, inside one fenced block, parsed
as a unit, stating that the filer reviewed this PR at that SHA and that the verdict is that
review's own finding. A rebase note cannot contain it by accident, and a paraphrase does not
count. **Third, eligibility pinned by ancestry:** form (3) bounded the population it could
speak for not at all, and so was available as a substitute for live review on new work; form (4)
is admissible only for a merge that is an ancestor of the audited tip, a set fixed when the
corpus was audited and unable to grow afterwards. The first two close the forgery-by-accident
hole form (3) opened; the third stops the remedy becoming a permanent second route around
ADR-0039. An ordering between two timestamps would not have done that — it proves
**posteriority, not retroactivity**, which is the same shape of error as reachability proving
**ancestry, not review**, one paragraph above. The bound had to be the pin. 151 checked PRs have zero comments, zero reviews, zero receipts and zero repo
records, and they still `FAIL`. Any rule that passed them would pass everything and make
this condition a check that cannot fail.
**`pr-lint` is run at the kind the PR's own diff shows, never at an assumed one.** The kind
is `free` iff every changed path in that PR's diff is under `.omni/**`, else `task`, and
**fails closed to `task`** (the stricter kind) if the diff cannot be determined. Invoking
`pr-lint` with no `--kind` takes its `task` default and thereby asserts every PR in this
project's history is a task PR; `.omni/**`-only program bookkeeping PRs have no backlog
issue by construction (`docs/owners.md` records `.omni/**` as `program`-owned run record,
"persisted off-box via the `omni/state` branch, never via `main`"), so the `Refs #<n>` rule
was being applied to PRs it was never written for — 20 of 31 measured `pr-lint` failures
were exactly that, and deriving the kind takes the real failure count from **31 to 11**.
**This changes only which `--kind` `pr-lint` runs at. It does NOT exempt `.omni/**`-only
PRs from the review requirement**, and nothing in this file or `docs/owners.md` does — that
table's third column is `Single-writer`, not "review required". The only place an `.omni/**`
review exemption is asserted is inside the bodies of the bookkeeping PRs themselves, which
is self-attestation, not policy. **120 of the 218** measured review failures are such PRs,
leaving **98 code-touching**; whether the run record is "a change this project produced" at
all is a **live scope question for this condition**, recorded on issue #520 and deliberately
not settled by this row.
*Validator check:* for the union of the PRs listed in the manifest's `changes` and the PR
numbers found on every first-parent merge commit in `a73ba88..main` not already in that
list (`git log --first-parent --merges`, reading GitHub's own "Merge pull request #N"
message — nothing here is silently skipped just because nobody added it to the manifest),
**minus any PR merged at or before the `a73ba88` baseline itself**, since this condition's
range is `a73ba88..main` and a PR already in the baseline is not a change it audits. That
removes exactly two: PR #1, whose merge commit `a428fb5` is an *ancestor* of the baseline,
and PR #2, whose merge commit **is** `a73ba88`. This is a **range** correction, derived
structurally from git rather than hardcoded, and it is **not** a grandfather clause — no PR
in the checked set predates the `Refs #<n>` rule (which landed in `bin/omni-ctl` at
`4f931e6`, 2026-08-21T20:16:30+05:30 — 18h45m10s before PR #1's merge commit; the later
`38fe32c` only *renamed* that file and did not introduce the rule), so a timestamp-keyed
clause would be a no-op that only obscured things. An empty checked set is a `FAIL`, never
a vacuous `PASS`.
For each such PR, `gh pr view <n> --json
state,mergedAt,body,reviews,comments,statusCheckRollup,headRefName,files,headRefOid,mergeCommit`
(the last two are what make form (4)'s eligibility and commit bindings evaluable at all)
— body lints clean **at the kind derived from `files`**; `reviews` non-empty, **or** a
comment containing a block `bin/nightshift_review_receipts.py`'s own structural parser
(imported, not reimplemented) accepts, **or**, only for a PR with `mergedAt` at or before
`2026-08-28T09:50:53Z`, a
comment matching the grandfathered cross-crew/cross-vendor prose convention, **or** — last,
and only once every stronger form has declined — a form (4) `` ```nightshift-retro-review ``
block on a **merged** PR whose merge commit is an ancestor of the pinned eligibility tip,
whose attesting comment's own `createdAt` is strictly after that PR's `mergedAt`, whose
`reviewed-sha` is reachable from the PR's head and not from `mergeCommit^1`, and whose fields
and `attests` sentence parse exactly; gate results present. Any PR counted through form (4)
is named in the verdict line's `retro: N` clause, whether the condition passes or fails.
**The whole corpus is evaluated — the check never returns on the first failing PR**, and
its reason line reports totals (PRs checked, and how many fail each of `pr-lint`, recorded
review and gate results) with a bounded, explicitly-marked sample of examples, never a
single item that reads as if it were the whole problem. Per **WA-015**, a check that stops
at the first failure must say how many failures exist in total: this one reported *1*
failure against a 251-PR corpus, and three consecutive heartbeats — Program's included —
sized a 251-PR problem as a 1-PR problem on the strength of that reason line and deferred
it each time as small. The receipt
check stops at the block's own structural validity — it does not reach the
marker/freshness/PR-identity checks `validate_receipt()` performs, which need a task id, a
head SHA and `control.db` access this validator does not have (stdlib + git/gh only, per
ADR-0025); that narrower forgery class, and **verifying the reviewer's identity differs
from the author's, are both out of scope for this check** — every seat shares one GitHub
PAT (ADR-0039's own named threat-model boundary), so nothing here can attribute a comment
or review to a specific agent; tracked separately as finding F-102 (`docs/audit/FINDINGS.md`,
owner `product`) and condition 18/19, not repeated here. A PR with no gate results reports
`FAIL` **unless** the manifest's `conditions.22.queued_gate` names a gate and a verbatim
excerpt that is still present, right now, in `.omni/blocked.md` — in which case that PR
contributes to the condition's own verdict line as `PASS (queued: <gate>)` (naming every
distinct gate any checked PR is queued behind, if more than one) rather than `FAIL`; a
merged PR with no gate results always `FAIL`s (queuing is only for a PR still open).
`BLOCKED` if GitHub is unreachable.
*The review requirement is never waived* — the brief protects it explicitly, and
grandfathering the evidence FORM for pre-cutoff PRs does not waive it: every PR still needs
a recorded review, only the shape of acceptable evidence differs by merge date. Admitting
form (4) does not waive it either, and it is not a grandfather clause: it adds a form, removes
no requirement, narrows no corpus, and is available only *after* a merge and only to a PR that
still must produce evidence in some accepted shape. A PR with nothing filed still `FAIL`s. (Rewritten
2026-09-02; corrected 2026-09-02, same revision, before merge — see changelog: round 1
accepted a prose comment that merely *read like* a review as equivalent to ADR-0039's
receipt for every PR regardless of merge date; cross-crew review and staff independently
reproduced this live against PR #523's own comment, which the project's real receipt
parser rejects as `structured_block_malformed`. Round 2 reuses that real parser and dates
the grandfather clause explicitly.)

**23. A stranger can take over from `docs/HANDOVER.md` alone.**
It contains: what changed and why (per merged/queued fix, with `RT` ids); what is
confirmed still broken and where it is recorded; accepted risks with their triggers;
operator actions required (in particular whether a reinstall or a root remediation is
needed — the bwrap profile is the live example); rollback notes per change; and links to
the audit documents, the findings index and this file.
*Validator check:* every required section is present and non-empty; every link resolves;
and the commands in its `## Verify` section run to completion against a fresh clone in a
temporary directory.

**24. A traceability matrix maps every promise in the brief to its evidence.**
`docs/prd/TRACEABILITY.md` carries a machine-readable matrix with one row per obligation:
the five confirmed bugs and the thirteen audit areas named in `.omni/brief.md`, each
mapped to its DoD condition, its audit question ids, its findings, its `RT` ids and its
PR(s).
*Validator check:* all eighteen obligations present exactly once; every referenced
condition, question id, finding id, `RT` id and PR resolves; no obligation has an empty
evidence column.

---

## The quality bar for THIS project

- **Fix size:** the smallest change that makes the required test pass. A fix that needs a
  redesign is recorded as an `ACCEPTED-RISK` with a trigger and becomes a follow-up
  project — unless it is in the brief's protected set, in which case it must be fixed.
- **Test standard:** every behavioural fix has a test that fails against the pre-fix
  commit, with the failure captured (condition 8). A test that pins a property the fix must
  **not** break is recorded as a `preservation` record — passing baseline plus failing
  mutant — never as a contrived failure. No coverage percentage exists here and none may be
  added.
- **Polish:** bash files parse (`bash -n` over the shebang manifest) and re-run cleanly.
  No cosmetic refactors, renames or formatting sweeps.
- **Performance:** no throughput or latency target. The real ceiling on this system is
  vendor quota and a four-worker budget, not SQLite; a p95 number would have been a
  number nobody asked for. The only timing constraint is practical: the full suite plus
  the validator must stay runnable in one CI job's timeout.
- **Edge cases that count:** anything reachable on a fresh Ubuntu 24.04 VPS, on restart,
  on crash, and with a vendor unavailable. A second box, another distro or another
  provider is out.
- **Documentation:** where behaviour changed, the file that described the old behaviour
  changes in the same PR. Stale documentation of a fixed bug is a defect — `TRUST-BOUNDARY.md`
  in condition 5 is the case in point.

## Explicitly NOT conditions of done

A real fresh-VPS reinstall; a literal multi-day idle soak or any elapsed-time observation
requirement; an operator-run silence drill on the live host; a coverage percentage; a test
count; a line-count reduction; a latency or throughput target; any change to
`/srv/nightshift` or `/home/nightshift/nightshift-runtime`; support for a second box,
another distro or another provider; prompt-scaffolding experiments; new agents, crews or
skills.

## Change control

- **Authority:** `product` is the single author. The program may rule on scope; `architect`
  and `staff` may propose; nobody edits this file but `product`.
- **Evidence threshold:** a condition changes only on evidence from delivery or a
  cross-vendor critique — never on convenience, and never because a condition turned out
  to be hard.
- **Immutable history:** conditions are never quietly edited. Every change appends a
  changelog row with the date, the conditions touched, the change, and the evidence that
  forced it. Superseded text is described in the row, not erased from the record —
  `docs/prd/DOD-RECONCILIATION.md` holds the full v1→v2 audit trail.
- **Additions late in the project:** the default answer is no. A newly-discovered
  obligation becomes a follow-up project unless it is (a) a confirmed defect in the
  brief's protected set, or (b) a defect that makes an existing condition undecidable.
  Both exceptions require a changelog row naming which.
- **Downstream artifacts propose, they do not bind** — including `docs/architecture/CAPACITY.md`.

## Changelog

| Date | Condition(s) | Change | Evidence |
|---|---|---|---|
| 2026-09-02 | **22** (a fourth accepted evidence FORM named — retroactive structured review attestation. **No requirement removed, no corpus narrowed, no existing form's bar relaxed** — but a fourth form is added, so the set of PRs able to pass does grow, and this row says so rather than hiding it behind "text-only"; `.omni/**` stays in scope and the "never waived" clause is untouched. The prose describing form (3)'s withdrawal is amended, not erased) | **Condition 22 now names a fourth accepted evidence form and describes it where a reader meets the condition.** Form (4) is a fenced `` ```nightshift-retro-review `` block, accepted only on a **merged** PR inside a closed, pinned corpus, and only when **all three** bounds hold: the PR's merge commit is an **ancestor of `FORM_4_ELIGIBLE_THROUGH`** (the tip the corpus was audited at, pinned by ADR-0075), the attesting comment's own GitHub **`createdAt`** is strictly after `mergedAt`, **and** the `reviewed-sha` is reachable from the PR's head **and not** from `mergeCommit^1`. Beneath all three, the block must parse as a unit and fails closed on any missing, duplicated, unknown or empty field. The body now also carries the retro weakening (after-the-fact attestation, weaker than a live ADR-0039 receipt, cannot establish filer ≠ author, announced in the verdict as `PASS (retro: N …)`, consulted only after stronger forms decline, and admissible only for merges already inside the pinned corpus). The form (3) withdrawal paragraph is amended so the two forms cannot be conflated, and the `gh pr view --json` field list is corrected to the one the validator actually requests. | **The condition's text and its validator were about to disagree in the project's only fixed point.** The prose named two accepted forms plus the pre-`cb8d68fc` grandfather clause, and carried a paragraph narrating the withdrawal of a third — *"a SHA-bound structured review comment (reviewed SHA + reviewing seat + verdict token)"* — because *reachability proves ancestry, not review*. Form (4) is **also** a SHA-bound structured review comment carrying a reviewed SHA, a reviewing seat and a verdict, so against unamended text a reader would land on a paragraph that appears to **forbid, in almost the same words, what the validator now accepts** — with no way to resolve it from the file alone. The forcing reason is **not** "the validator changed": it is `staff`'s D22 scope ruling (adopted by Program in full, 2026-09-02T13:44:23Z — `.omni/**` is in scope, root cause #593) together with the `MERGE_BLOCKER`s `architect` and `security` each raised against form (3), whose stated reintroduction conditions this text now records as **met**. Both this row and the validator execute a decision already taken. The three bounds are stated **jointly mandatory** because `security` measured that the exclusion alone still admits an author's own *"Rebased onto `<sha>`; program bookkeeping only, CI PASS"* naming a genuine commit of the PR's own (`tests/test_dod_validate_d22.py`, `test_architects_four_adversarial_cases_are_all_refused`); the fenced block alone would accept a form naming any SHA at all. The 216-of-216 form (3) measurement and the *"reachability proves ancestry, not review"* reasoning are **retained verbatim** per this file's own change control — that measurement is exactly the kind of evidence that must survive — and the withdrawal paragraph is extended to record that its own stated reintroduction conditions are the ones form (4) was built to meet. Every claim here was read in `tests/dod_validate.py` at commit `76a70b2` on branch `product/520-d22-review-form` (#626, round 6) — named by symbol rather than line, because #626 is still open and line numbers there have moved every round: `_REVIEW_FORM_FENCE_TAG`, `_parse_review_form` (the fail-closed branches), `_sha_is_one_of_this_prs_own_commits` (both halves, and the degrade-to-no-evidence path), `_has_retro_review_form` (the three bounds required together, and the call site placed after `_has_recorded_review` declines), `_pr_is_form_4_eligible` and `FORM_4_ELIGIBLE_THROUGH` (the ancestry bound and its pin), `test_the_pin_matches_the_unique_accepted_adr_that_carries_one` (which binds that constant to ADR-0075, so the pin moves only by superseding an accepted decision), `_retro_clause` (the emitted wording), and the `gh pr view --json` field list that adds `headRefOid,mergeCommit`. **`filed-at` is described as a declared field only.** It is required in the block so a human reader sees a date, but it is not the temporal control and this condition rests nothing on it — per Program's ruling on **#636**, which chose *delete the claim* over *implement the cross-check* on the grounds that `createdAt` already decides the only thing that matters and a field the filer types cannot verify another field the filer types. Text-only; this row implements no code. **It does widen what can pass**, and that is the point of naming a fourth form: a PR inside the pinned corpus that previously had no admissible evidence can now acquire some. What is *not* relaxed is any existing form's bar, the `.omni/**` scope, or the never-waived requirement. Lands in the **same release candidate** as the validator change (#626) — either alone leaves the record inconsistent in a different direction. Refs #520. |
| 2026-09-02 | **22** (two validator defects fixed; the checked range corrected to match this condition's own declared range. **No change to the accepted evidence forms**, no change to PASS/FAIL logic, and the "never waived" clause untouched) | **`check_d22` was reporting 1 failure against a 264-PR corpus, and was measuring the wrong thing on two counts.** **(1) It returned on the first failing PR** (`tests/dod_validate.py:1192`), so its reason line named one PR when it had stopped at one PR. Three consecutive heartbeats — Program's included — sized a 251-PR problem as a 1-PR problem on the strength of that line and deferred it each time as small; this is the finding that produced **WA-015**. The check now evaluates the whole corpus and its reason line reports totals per clause with a bounded, explicitly-marked sample (`+N more`), never a sample that reads as the whole set. The review total is additionally **split** into PRs merged at or before the receipt tooling landed and PRs merged after it, because those need different work; the `pr-lint` total is broken down by **which convention was missed, and how often**. **(2) `pr-lint` was invoked with no `--kind`** (`:1184`), taking its `task` default (`bin/nightshift-ctl:6648`) while `lint_body()` applies the `Refs #<n>` rule only for `kind == "task"` (`:4270-4282`) — so the validator asserted all 264 PRs were task PRs. The kind is now **derived from the PR's own diff** (`free` iff every changed path is under `.omni/**`, else `task`) and **fails closed to `task`**, the stricter kind, when the diff cannot be determined — counted and named separately rather than folded into the `pr-lint` total, so a `gh` outage that emptied every `files` array cannot silently re-acquire this defect disguised as a body-convention problem. Measured: **31 → 11** real `pr-lint` failures. **(3) Range correction:** this condition's declared range is `a73ba88..main`, so PR #1 (merge commit `a428fb5`, an *ancestor* of the baseline) and PR #2 (merge commit **is** `a73ba88`) leave the checked set — derived structurally from git, not hardcoded, and **not** clemency: neither is cited in `TRACEABILITY.md` or `FINDINGS.md`. Explicitly **not** a grandfather clause — the `Refs #<n>` rule landed at `4f931e6` 18h45m10s before PR #1 merged, so a timestamp-keyed clause would be a no-op that only obscured things. An empty checked set is now an explicit `FAIL`, never a vacuous `PASS`. **WITHDRAWN BEFORE MERGE — a third accepted evidence form** (a "SHA-bound structured review comment") was implemented in round 1 of this task and removed on a `MERGE_BLOCKER` raised independently by `architect` and `security`. Its stated binding did not exist: reachability from the PR head proves **ancestry, not review**, and one constant string naming this condition's own baseline commit satisfied the whole form for **216 of 216** in-range PRs. It also required no semantic relationship between its three signals, so an author's own rebase note satisfied it with no intent to attest. The condition's prose now records the two remedies any future third form must satisfy together. **This changelog row therefore records a validator correctness fix only — it does not widen, narrow or waive what counts as review evidence.** **D22 still FAILs — 218 of 264 — and that is the correct outcome**; the point of this row is that it now fails *accurately and completely* rather than reporting 1. **Nothing here waives the review requirement and no historical PR is exempted.** The scope question is recorded, not settled: of the 218 review failures, **120 are `.omni/**`-only bookkeeping PRs** and **98 are code-touching**; of those 98, **~48 carry a substantive real review the validator cannot currently bind** (that is what the withdrawn form was for), leaving roughly **50 with no review evidence anywhere**. The claim that `.omni/**` PRs need no cross-crew review **is not supported by `docs/owners.md`** — that table's third column is `Single-writer`, not "review required" — and traces only to the bodies of those PRs themselves, which is self-attestation. Whether the run record is "a change this project produced" is a decision for `product`/`staff`/Program on issue #520. **Cost, stated rather than discovered later:** the check now makes one `gh pr view` per PR and takes ~5 minutes, where returning on the first failure took one call. | Task `520-d22-validator-defects`, issue **#520**, PR **#587** round 1, wave 38 / checkpoint 39, run `fixture-review-231`; `staff` decision on #520 (2026-09-02T08:52:51Z); Program's re-scope (2026-09-02T08:57:08Z, `.omni/decisions.md` D-w38-6); **WA-015**; `architect` and `security` `MERGE_BLOCKER` on PR #587 (`.omni/reports/2026-09-02-security-587.md`, reviewed head `a4e7d09`); Program's SPLIT ruling. **Reproduce with the condition's own instrument** — `python3 tests/dod_validate.py` prints every number below on the `D22` line itself; no scratch script is the source of record. Full-corpus run at base `5eb1430`: **264 checked** (257 `manifest.changes` − 2 baseline + 9 first-parent merges); `pr-lint` **31→11** (120 `free`, 144 `task`, 0 undeterminable); review evidence 3 native / 30 fenced receipt / 13 grandfathered prose / **218 FAIL** (49 at-or-before the tooling landed, 169 after); gate results 0 failing; **218 of 264 distinct failing**. Review failures split `free`/`task` = **120/98**. **Number attribution (Program made this binding before any scope decision leans on it):** the round-1 figure of "60 PRs recovered" was **wrong, and the error was mine, in the measuring harness rather than in the fix** — its classifier was an `elif` chain testing the new form *before* the grandfathered prose bar, so it absorbed **12** PRs (#15, #22, #24, #35, #39, #63, #78, #85, #90, #94, #111, #227 — all grandfathered, all already accepted by the prose bar before this PR) that the form never moved. True form-only rescues: **48** over the 264 corpus = **33 in-range** (byte-identical to `security`'s independent replay over its 216-PR in-range set) **+ 15** manifest-listed PRs outside the first-parent merge range (all grandfathered). `security`'s hypothesis that the 60 conflated all three fixes is **not** the cause: the range correction and the `--kind` derivation do not touch the review clause at all. Historical premises re-verified against the repository rather than inherited: `4f931e6` (`bin/omni-ctl`, introduces "the PR must reference its backlog issue") 2026-08-21T20:16:30+05:30 vs PR #1 merge `a428fb5` 2026-08-22T15:01:40+05:30 — the charter's own premise that PR #1 predates the rule is **false**, and `38fe32c` is only the later rename. `tests/test_dod_validate_d22.py`: **31 tests**, all passing — whole-corpus accumulation, the reason-line splits and breakdown, kind derivation and its fail-closed fallback, the range correction, and two real-repository tests that guard the `4f931e6` and `#1`/`#2` premises from going stale. **The closure guard is asserted through `check_d22` itself, not through `_has_recorded_review`** — its first version was asserted one layer below and `security` mutation-tested it to be **vacuous**: the withdrawn form was reached via a defaulted parameter `check_d22` supplied, so a call at the lower layer could not observe that shape and both behavioural assertions passed against any implementation, leaving the guard's whole weight on five string literals. Verified by re-running that mutation: identical code under five plausible successor names, fully wired in, leaves all 30 other tests green and fails only this guard, on all three of its adversarial cases (the baseline-SHA universal string, an author's own rebase note, and seat/SHA/verdict drawn from three unrelated sentences). Its `git` fake answers **yes** to every SHA question, so a reintroduced form cannot be rejected merely for want of `git` — the same "passes for the wrong reason" failure the guard exists to catch. The `hasattr` check is retained as a separate, explicitly weaker test that catches a straight revert by name and is documented as blind to a rename. |
| 2026-09-02 | **6** (`RT-17`'s id text amended to its provable form; `RT-16` untouched; condition 6's *Required behaviour* paragraph untouched; an explicit *Not claimed* scope statement added) | **`RT-17`'s id text asserted a discrimination the fixed system does not make, and no correct implementation could have satisfied it.** As allocated on 2026-08-22 it read *"concurrent acquires **across program and lead paths** never exceed `max_concurrent_workers − reserve_for_oncall`"* — wording that described the **defect** (the program path consumed no slot at all) as though the fix would leave a per-producer distinction behind to observe. It does not: the remedy *is* the absence of the distinction. Condition 6's *Required behaviour* asks for **one** budget consumed by every dispatch path, never for per-caller accounting, and normal slot allocation is deliberately not caller-identity keyed. `staff` verified at the seam that "program" versus "lead" survives only as a **task-name substring**; ADR-0069 §5 records the same limitation independently. The retired wording therefore demanded either an unobservable property or a redesign — adding caller-identity keying to the normal path — that condition 6 never asked for and that the quality bar would have sent to a follow-up project. **Amended to:** independently produced concurrent acquires converge on one atomic budget, no normal acquire reaches the reserve at saturation, and the reserve is reachable only by an on-call acquire presenting a live, current-generation Program authority token. **Not a narrowing-to-fit — and the ADR did not err.** ADR-0069:15 is the document's only restatement of `RT-17` and it reads, in full, *"independently produced concurrent acquires converge on one atomic budget; normal acquires cannot consume the reserve, and on-call reserve access requires Program authority"* — all three clauses, nothing dropped. The amended text above is **coextensive** with that restatement, and was derived independently from condition 6's own *Required behaviour*, not from the ADR and not from the landed test. That the two agree is corroboration, not authorship: an ADR still does not get to re-cut a frozen id's text to fit the evidence it can produce, and amending an id remains `product`'s act on the condition's terms. **This amendment does not rest on the ADR having erred.** It rests on the retired wording having demanded a discrimination no correct implementation could ever show — which is the whole of the argument, and it survives an honest restatement of what the ADR said. **Corrected before merge:** this row's first published form (PR #611, commit `47d4c85`) claimed the ADR "silently drops the reserve fence" and called the amendment "stricter than the ADR asked for". That claim was false and rested on a quote of line 15 truncated at its semicolon; `crew_beta`'s cross-crew review of #611 caught it and raised it as a `MERGE_BLOCKER`. It is recorded here rather than quietly deleted, because the claim it made — that the ADR narrowed and `product` restored — is exactly the accusation this row exists to answer, and a false version of it in the permanent record would have been worse than the objection it was answering. **Not a widening:** no new obligation, no new `RT` id, no new condition, and `check_d06` (`generic_rt_condition(6)`) still requires **both** ids. **`RT-16` is not amended** — its wording is earned as written, because what landed is stronger than what it says: the gate does not key on the caller at all, so denial of an un-slotted dispatch holds for every caller, the program path included. An id whose evidence exceeds its text needs no edit; that is the asymmetry between the two ids and the reason only one moved. **What was given up, stated in the same terms `staff` used:** (i) no in-repo evidence distinguishes the **producer class** of an acquire — `RT-17`'s corpus varies real crew, agent and task facts, and that is all this seam can witness; (ii) `RT-16` proves the gate refuses the two *schema-reachable* bypass forms, not that the harness offers only those forms; (iii) consequently **nothing in this repository shows that a real deployed program-level dispatch transits the gate and the budget these ids assert**. That residue is external harness state (only root `guardrails.policies` enforce — #118), lives behind **#495/#534**, is now named in condition 6 as *Not claimed*, and takes **no `RT` id** — condition 8's binding rule requires a file in this repository and there is none, so an id for it could never be satisfied; the same foreclosure ADR-0069 already records for the harness roster. It is carried as an `ACCEPTED-RISK` with a **written, dated trigger** in `docs/HANDOVER.md`'s *Accepted risks* register — the row "Program-vs-lead producer routing is unproven, and `RT-16`/`RT-17` cannot prove it", added by this PR under EDIT lease **120**; neither that nor condition 23 is a new obligation, both are existing mechanisms. **Corrected before merge (round 3):** rounds 1 and 2 of this row asserted that residue *was* so carried while `docs/HANDOVER.md` was byte-identical to the merge-base and contained zero references to #495 or #534 — `crew_beta` settled it with one command (`git diff <merge-base>..a83ca08 -- docs/HANDOVER.md`, empty). The obligation is now discharged rather than described, and two stale entries in that file are corrected in the same pass: its "Conditions 6–7 ... not yet started; no issue is currently filed" bullet (stale on both halves — #507 is filed and open for 6, and 7 is **done**, `D07 PASS`, #508 closed), and the worker-budget risk row's *"documented 4, true concurrency ~9"* arithmetic, which predated the owner's 2026-09-02 capacity raise and contradicted condition 6's own body in this very PR. **Corrected before merge (round 4), and it sharpens the rule:** round 3's rewrite of that risk row re-measured the *installed* side of a two-sided comparison (1073 lines, correct) and **inherited** the repository side (6147 — actually `e06b1d9`, 12 commits back on that file), where the true figure is **6828** at `fa93374`; `crew_beta` caught it and the owner verified it before relaying. The relocation was right and the gap was *understated*, so the correction enlarges the risk rather than softening it. **When a claim is a comparison, both sides are measured in the same breath — an inherited number is an inferred state wearing a digit.** The row now also carries the drift-resistant structural form of the same fact (15 installed subcommand groups against 29 in the repository, 14 named as missing; installed policy module 11,181 bytes against 32,922, one `worker_semaphore` reference against three), because a line count goes stale on the next merge and a missing subcommand does not. **Also in round 4:** the register's *duplicate-dispatch* row was found false of the repository and **true of this host** — `claim release`/`claim renew` require `--claimant` at `origin/main` and offer it nowhere on the installed binary; the policy consults `task_claims` twice at `origin/main` and zero times as installed — so it is re-grounded as a deployment risk, not retired, exactly as the worker-budget row was. Retiring it on the strength of an undeployed fix would have discarded a live risk. And the *Accepted risks* preamble, which claimed its rows were current "today" with nothing recording when any was checked, now carries per-row verification dates and names which rows are unverified as-of-`f82559a`; this is deliberately kept out of the general HANDOVER refresh (#613), since those bullets sit in a section labelled stale while these claim to be current. **Round 5, two corrections and nothing else:** (i) a clause inside the now-stale-labelled section still said `docs/audit/FINDINGS.md` "does not exist yet" and #482 was open, and directed readers to treat that section as authoritative — the file is 13,194 bytes and #482 is closed, so the false clause is **struck** (labelling a section stale does not neutralise a sentence inside it saying the opposite; and #613's title scopes that ticket to the bullets, so it was not covered there); (ii) the residue row's support was swapped for the one that actually entails its claim. Both domains found independently that the `worker_semaphore` "one reference against three" count, while accurate, is non-probative — both modules define the function exactly **once** and the delta is two prose comments (`origin/main:102`, `:521`) — so it could not establish that the deployed gate differs in behaviour, even though it does. It is replaced by the entailing facts: the installed evaluator takes `slot_arg="slot_id"` and denies any dispatch lacking `args.slot_id`, the field ADR-0062 records the dispatch tool's schema cannot carry (`additionalProperties: false`), and exempts one seat against the repository's eleven, while the repository's evaluator reads no `args.slot_id` at all and derives the slot from control-plane state. The perishable byte counts are likewise replaced by an **identity pin**: both installed files are byte-identical to `a73ba88` (`cmp`), which also corrects a residue of the round-3 draft — `0945f07` **predates** `a73ba88` and is present in the installed file, so the deployed gap is everything after the 2026-08-22 baseline, not that commit. **Why the alternative was rejected:** holding the old wording keeps D06 red on a seam this box cannot exercise, dischargeable only by an integration probe against the live harness — precisely the class of evidence *Explicitly NOT conditions of done* already rules out. Condition 6 is a confirmed defect promoted out of `.omni/tech-debt.md` and so cannot be parked as an accepted risk either, so that path ends in a condition that can never be closed by any work this project is permitted to do. Same reasoning shape as this file's 2026-09-02 D22 row: a condition must not be made permanently unpassable through no fault of the delivery. **Scope:** this row **does not flip D06** and does not touch `docs/prd/DOD-MANIFEST.json`. The manifest's `RT-17` `unsatisfied_reason` still quotes the retired wording and must carry the amended text when the bindings land — STEP 2, behind `product` lease 110, with its own open gates (`staff` F-8 capture reproducibility, #607, #608). **Binding on STEP 2, and a condition of D06 going green:** the id text's *live, current-generation* clause is **not yet demonstrated by `RT-17`'s bound nodes**, which cover valid-token acceptance and missing- and wrong-token refusal **at the current generation only** — no stale or fenced-out generation, no expired authority — and **M2** kills only the missing/wrong refusal. ADR-0069 §5 draws that line scrupulously and this id's wording, taken alone, collapses it. The property is provable and already proven: `tests/test_authority_fence.py::SlotAcquireOncallTokenGate::test_denied_for_a_fenced_out_generation`. STEP 2 must add that node to `RT-17`'s bound list, so the clause cannot reach a green D06 unearned. **The text is deliberately not weakened to match the current bound set** — that would be the same defect as narrowing-to-fit, wearing the opposite sign; the id keeps the stronger property and the evidence is made to meet it. `docs/audit/CONCURRENCY.md`'s `RT-17` quote is advisory, crew-owned and stale from this row; correct it when next touched. Neither string is a binding. | Owner routing of `staff`'s needs-a-wider-decision on PR #585, 2026-09-02; PR #585 (`crew_alpha/506-d06-rt16-rt17`, head `7277033`), ADR-0069 §§1, 3, 5 and its round-3 **G2** class rename (`RT17ConcurrentAcquiresShareOneBudget`, made because the old class name asserted the same unprovable distinction and reaches the manifest as `test_node_id`); `staff` mutation of the derivation query (drop `dispatched_at IS NULL`) — bound set GREEN at the old head, **exit 1 / D06 FAIL** at the current head — independently rebuilt by `crew_beta` at all three sites with the same kill, two domains, two implementations; `tests/test_slots.py::RT17ConcurrentAcquiresShareOneBudget` (14 concurrent independently produced acquires at hermetic capacity 7/2 → exactly 5 granted, losers exit 3 `worker budget exhausted`, saturated normal acquires refused from both crews, on-call grant only against a live authority token) with mutants **M1** `7 != 5` and **M2** `0 != 3`; program ruling on #507 (D06 must not flip until its binding contains a cell that can fail); `.omni/charters/486-d06-rt16-rt17.md` (original id text as chartered); lease **112** on `docs/prd/DEFINITION-OF-DONE.md`, held by `product` (carried forward, not re-granted); ADR-0069 line 15 read in full (the correction above); `crew_beta` cross-crew review of PR #611 — `FAIL`, one `MERGE_BLOCKER` on the truncated line-15 quote and one `PRE_DEPLOYMENT` on the unbound generation-fencing clause — which also verified at the seam that the normal acquire path exposes **no producer-class axis to vary** (the load-bearing fact under this whole row, checked against the code rather than against this prose), confirmed `M1` kills clause 1 at `7 != 5` so the id is not vacuous, and re-ran `tests/dod_validate.py` in clean detached worktrees at the merge-base **and** at this row's head with **identical verdict lines**, 19 `PASS` / 2 `MANUAL` / 3 `FAIL` — no silent flip; `tests/test_authority_fence.py:162` (`SlotAcquireOncallTokenGate::test_denied_for_a_fenced_out_generation`); `crew_beta` round-2 review of PR #611 (`MERGE_BLOCKER` MB-2, settled by `git diff b68ca3c..a83ca08 -- docs/HANDOVER.md` → empty, zero mentions of #495/#534); `docs/HANDOVER.md` *Accepted risks* register and its conditions-6/7 bullet as rewritten here under EDIT lease **120** (`docs/HANDOVER.md`, task `d06-rt17-text-decision`); `0945f07` (2026-08-21, the landed semaphore fix); `.omni/capacity.json` (`max_concurrent_workers: 10`, `reserve_for_oncall: 1`, `max_items_in_flight: 12`, raised by the owner 2026-09-02 with `spawn_bounds.max_dispatches_per_turn` 4→8, PR #533); issue #495 (installed `nightshift-ctl` 1073 lines vs `origin/main` 6147) and #534, both open; issue #508 closed and `D07 PASS`. |
| 2026-09-02 | **21** (manual exception discharged and its terms made checkable; "What is claimed" and "What is explicitly NOT claimed" unchanged) | The manual exception said a forward-looking control "is supplied by the environment" and named three acceptable shapes, but never said which one this environment actually supplies, never said who re-runs it, and never bounded what it covers — so the clause could be satisfied by any file existing at the recorded path. It is now discharged concretely and its terms are stated: the control of record is `docs/audit/FROZEN-PATH-CONTROL.md` (a committed before/after snapshot — per-file content hash plus each root's git `HEAD` for both frozen roots, and the read-only command that recomputes it), the other two shapes are ruled out **as measured on this host** rather than assumed (neither root is a mount point — `findmnt -T` → `/`, `/dev/sda1`; `auditctl` not installed; no passwordless `sudo`), and three obligations move into the condition itself: the control is detective and not preventive; `/home/nightshift/nightshift-runtime/.omni/**` is an excluded, named blind spot because the frozen instance legitimately writes there continuously; and the control must be re-run at every close-out attempt, around every provisioning/deploy rehearsal, and once at handover. **Not a widening.** `check_d21`'s behaviour is unchanged — it still only checks the field is populated and the evidence file exists, and still returns `manual`. What changed is that the manual verdict now points at a control with a stated failure mode, a stated blind spot and a stated cadence, instead of at an unbounded "the environment will supply something". The 2026-08-28 row narrowed this condition's *claim*; this row discharges its *exception*. | Task `522-frozen-path-control`, issue #522; baseline captured 2026-09-02T06:39:57Z on `<vps-host>`, both frozen roots at `a73ba88`, 186-line manifest, byte-identical on a repeat run, body digest `2a080693e933139e5d57c4109612e790c1301514ae03999e1b90e380b216cb21` (`docs/audit/FROZEN-PATH-CONTROL.capture.txt`); `tests/dod_validate.py::check_d21` inline NOTE; `docs/prd/DOD-MANIFEST.json` `conditions."21".frozen_path_control`; measured `deploy/provision-ubuntu.sh:360`. |
| 2026-09-02 | **8** (justifying prose only; binding form, PASS/FAIL logic and required tests unchanged) | **Stale premise in condition 8's `file`-alone example, corrected.** The parenthetical justifying the `file`-alone binding form claimed *"a flat shell script defines none: `deploy/nightshift-watchdog.sh` defines zero functions"* — true when PR #25 measured it on 2026-08-22, false since 2026-08-24: `36fbf12` and `71c451e` (Refs #132, Refs #42) added `sane_int()` (`deploy/nightshift-watchdog.sh:87`) and `delivery_failed()` (`:739`). `crew_alpha` confirmed independently while binding `RT-18`–`RT-22`/`RT-32`–`RT-34` (PR #538, task `536-rt-binding-symbols`): `RT-08`–`RT-12` exercise `sane_int()` unconditionally on every run; none of them exercise `delivery_failed()` — unreached, because every one of their fixtures uses the default successful `gh` stub. **Not a widening or a change to what is verified:** `RT-08`–`RT-12`'s binding in `docs/prd/DOD-MANIFEST.json` stays `file`-alone (unchanged by this row), condition 8's PASS/FAIL logic is untouched, and no new required test is added. The stale parenthetical is struck from the body rather than replaced with a new function-count claim, since a count is not itself pinned to any `RT` id and would go stale again the next time the script changes. Whether `delivery_failed()` needs its own coverage is not adjudicated by this row — that is a separate finding, not a defect in condition 8's binding rule. | `36fbf12` (`platform: enforce the delivery-backoff safety bound instead of asserting it`, Refs #132, #42); `71c451e` (`platform: retry fails closed, alerts throttle only on confirmed delivery`, Refs #132, #42); `deploy/nightshift-watchdog.sh:87` (`sane_int()`), `:739` (`delivery_failed()`); `crew_alpha` PR #538 (`fix/536-rt-binding-symbols`), task `536-rt-binding-symbols`; `docs/prd/DOD-MANIFEST.json` `RT-08`–`RT-12` `binding: {"file": "deploy/nightshift-watchdog.sh"}` (unchanged by this row); `tests/test_watchdog.py` RT-08–RT-12 fixtures (default successful `gh` stub, `delivery_failed()` unreached). |
| 2026-09-02 | **22** (same revision, corrected a second time before merge) | **Round-2's grandfather-clause fix (row below) compared a truncated calendar date, not the mechanism's actual landing timestamp, and wrongly ungrandfathered same-day historical PRs.** `bin/nightshift_review_receipts.py` -- the module implementing ADR-0039's receipt mechanism -- landed at commit `cb8d68fc`, 2026-08-28T09:50:53Z (the same commit also adds ADR-0039's own document, so the ADR's acceptance and the tool's landing are one event). Round 2's `merged_at[:10] < "2026-08-28"` compared calendar dates: any PR that merged EARLIER THE SAME CALENDAR DAY, before 09:50:53Z, was wrongly treated as governed by a mechanism that could not possibly have existed for it yet. `staff`'s re-check of PR #528 (task `staff-528-round2`) reproduced this live against real PR #234 (`mergedAt: 2026-08-28T07:13:31Z`, `reviews: []`, prose-only comment, no receipt fence) and named five more affected PRs (#235/#237/#239/#241/#243), all merged the same morning before the tool landed. Neither of round 2's own boundary tests (a PR merged 2026-08-20, well before; a PR merged 2026-09-01, well after) exercised this same-day case. **Fix:** the comparison is now against the full ISO-8601 landing timestamp (`merged_at <= "2026-08-28T09:50:53Z"`), not a truncated date string; a PR merged at or before that instant is grandfathered regardless of which calendar day it shares with the tool's own landing. Live re-verified: all six named PRs (#234/#235/#237/#239/#241/#243) now correctly compute as grandfathered. **Also, per the same review round:** the reasoning for why this fix needed no new ADR and no `crew_alpha` sign-off (reusing an already-accepted, already-integrated public module strictly read-only does not modify its owning crew's code path) is now stated self-contained in this file and in `tests/dod_validate.py`'s own comment, rather than pointing a reader at `.omni/decisions.md`, which is this project's uncommitted operational ledger and not resolvable from a fresh clone of just this diff. Three new boundary tests added (`tests/test_dod_validate_d22.py`): same-day-before-landing (grandfathered), exact-landing-instant (grandfathered, inclusive), one-second-after-same-day (not grandfathered). `--self-test` re-run: exit 0, all 24 checks still flip away from `PASS`. | `staff` task `staff-528-round2`, PR #528 comment `https://github.com/tushardhara/nightshift/pull/528#issuecomment-5502901191`; `git log --diff-filter=A -- bin/nightshift_review_receipts.py` (`cb8d68fc`, `2026-08-28T09:50:53+00:00`); live `gh pr view 234/235/237/239/241/243 --json mergedAt,reviews` (all `reviews: []`, all `mergedAt` between `07:13:31Z` and `08:44:52Z` on 2026-08-28); `tests/test_dod_validate_d22.py` (3 new boundary tests, 13/13 pass); lease 85 on `tests/dod_validate.py` (carried forward, not re-granted). |
| 2026-09-02 | **22** (same revision, corrected before merge) | **Round-1 D22 fix accepted a look-alike prose comment as equivalent to ADR-0039's actual receipt mechanism, for every PR regardless of merge date — wrong, and caught before merge.** Cross-crew review (`crew_beta`) and staff independently confirmed the same `MERGE_BLOCKER`, live-reproducible: PR #523's real comment is prose only, no `` ```nightshift-review `` fence, which the project's own `bin/nightshift_review_receipts.py` rejects elsewhere as `structured_block_malformed` — the round-1 validator accepted it anyway. Program settled this as a correctness fix against an already-accepted ADR (ADR-0039, 2026-08-28), not new architecture, so no fresh ADR or `crew_alpha` sign-off was required to read their already-integrated public parser module. **Fix:** `check_d22` now imports `bin/nightshift_review_receipts.py` and reuses its real structural parser (`_extract_block`, `CANONICAL_DOMAINS`, `VERDICTS`) to recognize a genuine receipt, instead of a hand-rolled regex over comment prose. This is dated, not universal: a PR merged **on or after 2026-08-28** (ADR-0039's acceptance date) must clear the real parser; a PR merged **before** that date is explicitly grandfathered to the weaker prose-or-native-review bar, because the strict mechanism did not exist yet when it merged and retroactive demand would make the condition permanently unpassable for historical merges through no fault of theirs (69 of 288 total merges to `main` predate the cutoff — reproducible via `git log main --merges --oneline \| wc -l` → 288, `git log main --merges --since=2026-08-28T00:00:00 --oneline \| wc -l` → 219, 288−219=69). This receipt check stops at the block's own structural validity; it does not reach `validate_receipt()`'s marker/freshness/PR-identity checks, which need control-plane DB access this stdlib-only validator does not have — named explicitly in the prose as out of scope, not silently absent. **Also corrected in the same pass:** the round-1 B5 citation (`Verified baselines` table) named `--first-parent --merges` for a count (305) that command does not produce — 305 is `--merges` without `--first-parent` over the same range; text now names the exact command that reproduces the number. **Also fixed:** `check_d22`'s `PASS (queued: <gate>)` reason previously overwrote itself across PRs queued behind different gates, reporting only the last one seen; it now lists every distinct gate. **Live re-verified after this round's fix:** PR #523 itself (merged 2026-09-01, after the cutoff, prose-only comment, no fence) now correctly reports a D22 `FAIL` for that PR specifically — the round-1 gap this fix closes was not hypothetical, it was already live in the manifest's own tracked PR set. `--self-test` re-run: exit 0, all 24 checks still flip away from `PASS`. | `staff` task `staff-526-consistency`; `crew_beta` review of PR #526, `docs/reviews/491-dod-validator-prose-CONTRACT.md` v2 finding F-1; `.omni/decisions.md` D-hb-shared3 (program settles, 2026-09-02 continued); live `gh pr view 523 --json state,mergedAt,reviews,comments` (`mergedAt: 2026-09-01T23:42:30Z`, `reviews: []`, comment has no `nightshift-review` fence); `bin/nightshift_review_receipts.py` (`_extract_block`, `BLOCK_FENCE`, `CANONICAL_DOMAINS`, `VERDICTS`); lease 85 on `tests/dod_validate.py`. |
| 2026-09-02 | **18, 20, 22, 24** (validator tightened to match this file; one clause of 18 corrected to match what a validator can actually check) | Three findings (#491, #520, #521) showed `tests/dod_validate.py` claimed more than it checked. **D18** (`check_d18`): now confirms a finding id is cited in its own `source_doc`'s findings index (not just present in `FINDINGS.md` alone), that a `CONFIRMED-BUG`'s cited `RT` id resolves to a real, baseline-valid manifest entry, and that its cited PR is tracked in the manifest's `changes` list (condition 22's own registry) — previously it only regex-matched for the literal substrings "PR" and "RT-" anywhere in the disposition prose, which a fabricated or stale citation would also match. Prose corrected in the same row: the protected-set/`ACCEPTED-RISK` clause was described as validator-enforced but nothing in the record's shape lets a validator judge it, so it is now named as a human judgement (not a third manual exception — condition 18 stays automatic PASS/FAIL). **D20** (`check_d20`): now requires and compares a `manifest.sentinel_sinks` list against `docs/audit/BOUNDARIES.md`'s own `SINK-nn` table by id — previously it only checked that a declared `sentinel_test_node` ran green, never that it scanned the sink corpus condition 16 Q1 actually enumerated. Prose was already accurate; only the validator changed. **D22** (`check_d22`): review evidence now accepts this project's actual convention — a structured cross-crew/cross-vendor PR comment (ADR-0039) — in addition to a native GitHub review object, which no crew has ever produced (empirically confirmed live against merged PR #523: `reviews: []`, would have failed every prior run); the PR set checked is now the union of the manifest's `changes` and every PR found on a first-parent merge commit in `a73ba88..main`, closing the "manifest silently omits a merged PR" gap named by #521; and a PR genuinely waiting on an unavailable gate now reports `PASS (queued: <gate>)`, evidenced against a manifest-declared, `.omni/blocked.md`-verified excerpt, instead of always `FAIL`ing. B5's premise (Codex seats down) was also found stale during this same review — resolved 2026-08-23, 305 PRs merged since — and is corrected in the Verified baselines table; this does not relax condition 22, which never depended on B5 being current, only on the queued-vs-skipped distinction B5 originally motivated. Reviewer-identity verification (does the reviewer actually differ from the author) remains explicitly out of scope for D22, tracked separately as finding F-102 — not solved or claimed solved by this row. **D24** (`check_d24`): now resolves every `condition`, `questions`, `findings`, `rt_ids` and `prs` citation against the manifest/audit docs/`FINDINGS.md`, instead of only checking the five evidence columns are non-empty. Prose was already accurate; only the validator changed. All four checks re-verified against the real repository state (self-test suite, all 24 checks, still passes; D18/D20/D24 produce byte-identical verdicts against current `main` before and after; D22 spot-checked live against PR #523 and against synthetic fixtures covering merged/open/queued/receipt/native-review/uncovered-PR cases). | `#491`, `#520`, `#521` (this reconciliation, run `fixture-review-231`, checkpoint 37); `docs/audit/FINDINGS.md` F-1/F-101/F-102 (unchanged post-tightening verdicts, confirming no regression); `docs/audit/BOUNDARIES.md` `SINK-01`..`SINK-06`; `docs/decisions/ADR-0039-persist-review-receipts.md`; `.omni/blocked.md` ("RESOLVED 2026-08-23T10:49Z"); `docs/audit/FINDINGS.md` F-102; live `gh pr view 523 --json reviews,comments`; `tests/dod_validate.py --self-test` (exit 0, all 24 checks flip away from PASS); `.omni/decisions.md` D-hb-shared2 (lease 84). |
| 2026-08-28 | **21** (clause narrowed; manual exception and "what is explicitly NOT claimed" unchanged) | Retired the literal second clause of "What is claimed" — "no committed script, unit, test or document in this repository writes to either path (shebang-manifest scan for both literals in a write position)" — which is unsatisfiable as written: `deploy/bootstrap.sh`, `deploy/provision-ubuntu.sh` and `deploy/preflight.sh` predate this project and write to `/srv/nightshift` as their designed first-install target on any fresh box, a property this condition was never trying to forbid. Replaced with a plain "no path under either root is a tracked file in this repository's history" claim, which is what `git log --name-only --all` can actually prove, plus an explicit statement that the property this condition actually protects — nothing *this project adds* reaches into the owner's already-running frozen instance — has no static-grep proof and is exactly why the manual, environment-supplied `frozen_path_control` exception exists. `tests/dod_validate.py`'s `check_d21` already implemented only the soundly-checkable claim (git-log absence of a tracked file at either path) and flagged the literal-reading gap rather than editing this file, per this file's own Change control and ADR-0025 §8 ("anything genuinely unverifiable as written is reported ... and left standing"); this row is the correction that report asked for. **Not a widening or a weakening of what is verified** — `check_d21`'s behaviour is unchanged by this row; only the prose it satisfies now says what it actually checks. | `crew_alpha` PR #268 (`alpha/186-dod-close-out-gate-v2`) `.omni/registry-crew_alpha.json` `known_scope_notes`, flagged non-blocking at PM sign-off; measured `deploy/provision-ubuntu.sh:360` (`install -d -o "$NS_USER" -g "$NS_USER" /srv/nightshift /srv/project`); `docs/decisions/ADR-0025-dod-close-out-gate.md` §8; `tests/dod_validate.py::check_d21` (inline rationale); product content review of `docs/prd/DOD-MANIFEST.json`, 2026-08-28. |
| 2026-08-28 | **10** (trigger correction; required jobs and exact-main proof unchanged) | Replaced `push` to all branches with `push` to `main`; retained unfiltered `pull_request`, added cancellation of superseded PR runs, and retained exact-current-`main` post-merge CI. A feature branch now consumes no runner before it becomes reviewable and no longer launches both push and PR suites for the same delivery commit. No path exclusions were added. | Owner-directed GitHub Actions usage review; same-SHA duplicate runs `33147113704` (`push`) and `33147153019` (`pull_request`); bookkeeping duplicate runs `33150793142` and `33150795967`; ADR-0037. |
| 2026-08-22 | 1–24 (v1) | Initial set. | `.omni/brief.md`; repository at `a73ba88`; baseline `Ran 83 tests … OK`. |
| 2026-08-22 | **all — v2 rewrite** | Blind cross-vendor critique returned 50 findings and the verdict *reject as binding*: several checks were vacuous or self-attested, condition 14 contradicted the brief by letting confirmed bugs be parked in tech debt, condition 23 was unsatisfiable with `release`/`qa` down, and conditions 15–19 prescribed simulation infrastructure the brief never asked for. **Deleted:** the accelerated seven-day simulation, the crash and vendor omnibus harnesses, the ≥72-hour observation, the operator-run real-host drill, the ≥105 test count, the 500 ms p95 target, and the ADR waiver clause in the old condition 20. **Added:** confirmed bug 5 (bwrap probed by presence, not function — reproduced on this host), the two confirmed duplicate-work defects promoted out of `.omni/tech-debt.md` into fixes (6, 7), the manifest and single close-out validator (8, 9), and handover plus traceability (23, 24). **Rewritten:** every prose or `grep`-based check into a validator check over structured records, with exactly two named manual exceptions. One finding rejected (frozen-path #40 — owner authority via dispatch); five partially rejected with reasoning. | `.omni/handoffs/DOD-CRITIQUE-codex.md`; reconciliation in `docs/prd/DOD-RECONCILIATION.md`; program rulings 1–8 of 2026-08-22; `.omni/tech-debt.md` (defects 6, 7); `.omni/blocked.md` + verified probe `bwrap --ro-bind / / /bin/true` → exit 1 (bug 5). |
| 2026-08-22 | **4** (evidence only; required behaviour unchanged) | **Added `RT-21` and `RT-22`.** `crew_alpha` finished wave 1 of M1 and found that the work ADR-0002 requires from seam A had **no requirement id**: `RT-08`…`RT-12` are all *consumer*-side (the watchdog reading a fixture), so nothing bound the **producer** of the liveness record to production code — condition 4 could have reported `PASS` on hand-written fixtures with no Program writer in the tree. That is exception **(b)** of *Additions late in the project*: a defect that made an existing condition undecidable. `RT-22` is exception **(a)**: `cmd_slot_heartbeat` renews any slot for anyone who knows its id, a confirmed ownership defect in the brief's protected set, and ADR-0002 §4 deletes/narrows `slot renew-active` — without a holder check on its replacement the condition-4 fix would relocate the unverified-renewal defect rather than remove it. **Not a widening:** no new obligation, no new condition, condition 4's *Required behaviour* paragraph is untouched; only its evidence list now reaches the production writer and the verb that replaces the one being removed. `RT-16` was **not** rebound — conditions 6 and 7 stay in M3 as written, and the crew was right to refuse: condition 8's `file:symbol` binding would have rejected the false evidence. ADR-0002 §5 (additive fencing columns) gets **no new id**: the columns are inert until enforcement lands, and their capture becomes `RT-20`'s baseline-failure record under condition 7 in M3. | `crew_alpha` wave-1 delivery report, 2026-08-22; ADR-0002 §§2, 4, 4a, 5 and the `staff` pre-build review that added §4a (`bin/nightshift-ctl:305-317`, parser `:907-910`, precedent `consume_slot:146-167`); existing captures `docs/audit/baseline/ADR0002-{2,4a,5}`. |
| 2026-08-22 | — | **Follow-up owed, not a condition change:** `docs/prd/MILESTONES.md` still maps v1 condition numbers and must be re-mapped to v2 before any crew is chartered. This turn was scoped to two files. | Scope of the reconciliation dispatch. |
| 2026-08-22 | **8** (evidence form; also the *Test standard* bullet in **The quality bar**) | **Baseline records gain a derived `kind`, and a second admissible evidence form.** `crew_beta`'s `RT-11` asserts the rewritten watchdog performs no service management — a property that held **before** the fix (the old watchdog never managed services; nothing enforced it), so it cannot produce the non-zero baseline condition 8 required. The crew captured the passing baseline honestly, labelled it `kind: "preservation"`, and escalated rather than manufacturing a failure — a contrived failing baseline would have placed falsified evidence in the exact manifest field condition 8's validator exists to police. **Added:** `kind` is **derived from the capture, not declared** — `exit_code != 0` ⇔ `regression`, `exit_code == 0` ⇔ `preservation`, disagreement is a `FAIL`; a `preservation` record must additionally carry a `mutation` block (patch applying cleanly to the tip, breaking a `file:symbol` already bound to that `RT` id, run by a **byte-identical** command, exiting non-zero, capture non-empty, patch never committed to a mergeable branch) **and retain** its passing baseline; no condition in section A may be evidenced by `preservation` records alone; the existing cross-crew `reviewed_by` exception is extended to attest the classification and the minimality of the mutant — **no third manual exception**. **Not a relaxation:** mutation evidence is strictly stronger than a pre-fix baseline and is what makes the label unfakeable — a crew that could not make a regression fail lands in the `preservation` branch and is then asked for a mutant it cannot produce without breaking real production code. **Deliberately not universal:** mutation is *not* required for `regression` records — the pre-fix commit is already the mutant, and a real one; requiring both doubles capture cost for zero discriminating information. `RT-11` is the first record of this form: its mutant captures landed in `1feb881` on PR #24 — `M1` on the alert path, `M2` on the alive path, both byte-identical commands, both patches evidence-only — leaving only crew_alpha's attestation outstanding. `test_rt11_the_instrumented_boundary_can_actually_fire` is retained but does **not** satisfy the requirement: it proves the detector parses a mutation string, not that the production path reaches the boundary. | `crew_beta` PR #24 (`beta/10-watchdog-liveness`, `bdcdcfc`); `docs/audit/baseline/RT-11.json` (`exit_code: 0`, `kind: "preservation"`, escalation note) against siblings `RT-08/09/10/12` (all `exit_code: 1`) at baseline `139726e`; `tests/test_watchdog.py:334-358`; condition 4 required-tests list; product ruling 2026-08-22. |
| 2026-08-22 | **8** (same revision, corrected before merge) | **Six corrections from cross-crew review of the revision itself.** `crew_beta` implemented the paragraph above **exactly as written** as a literal validator and ran it against all eighteen baseline captures that exist today (`RT-01`…`RT-15`, `ADR0002-{2,4a,5}`). Two results were blocking and the rule was wrong, not the artifact. **(B1) `mutation` was specified as one object; `RT-11` structurally needs two** — `M1` cannot reach the watchdog's `alive` branch, which returns early, so a single-mutation schema would have left `path='alive'` asserting a property it cannot detect the loss of, which is the self-attestation this rule exists to kill. Now a **non-empty array**, with path coverage moved into the `reviewed_by` attestation as clause (iii), where a human can see an unmutated branch and a validator cannot. **(B2) `mutated_symbol` was unsatisfiable for 5–7 of 15 `RT` ids** — measured: `deploy/nightshift-watchdog.sh` defines **zero** shell functions and `RT-08`…`RT-12` all bind there; `deploy/preflight.sh` has four, none on the bwrap path (`RT-13`/`RT-14`). The field is **removed**: the validator now derives the mutated files from the patch itself and requires them to be a subset of the `RT` id's declared production files — no field to fill, and it works for a symbol-less artifact. The binding rule gains an explicit `file`-alone form, and an explicit prohibition on **refactoring production code to manufacture a symbol**: a documentation rule does not get to edit a script under review to make itself satisfiable. **(N1)** `kind` is now **optional in storage, always computed** — declaring it was contradicting "derived, never chosen" and would have failed all eighteen existing captures on a missing field; a computed label cannot be relabelled at all, so this is stronger, not looser. **(N2)** "applies cleanly to the tip" perishes on any context-line drift (issue #26 targets that neighbourhood) — replaced by `applied_to_sha`. **(N3)** the section-A guard needed an `RT`→condition edge the manifest never defined; each `RT` id now declares the condition it evidences, which condition 9's author can implement and condition 24 reuses. **(Stale fact)** row above corrected before merge: `RT-11`'s mutants **landed** in `1feb881`; the row was written while they were outstanding and must not enter history stale. | `crew_beta` cross-crew review of PR #25, 2026-08-22 — condition 8 implemented as a literal validator and run against all eighteen captures; measured symbol counts in `deploy/`; `1feb881` (`RT-11` `M1`/`M2`); issue #26. |
| 2026-08-22 | — | **Confirmation, narrowed by the row above:** condition 8's `RT`-id **allocation** rule stands unrevised — the binding's **granularity** is revised there (B2), and this row was never a claim about granularity. Both crews independently reported the same edge — `crew_alpha` that ADR-0002 §2/§4a had no requirement id, `crew_beta` that binding `RT-16` to an ADR-0002 §2 test would be rejected because `RT-16`…`RT-20` belong to conditions 6 and 7 in M3. The rule was not defective; it **refused false evidence**, which is its purpose. The remedy for a missing id is allocation, which the 2026-08-22 `RT-21`/`RT-22` row already performed: §2 → `RT-21`, §4a → `RT-22`, §5 → no new id (its capture becomes `RT-20`'s baseline record under condition 7 in M3). No ADR-0002 obligation requires `RT-16`; conditions 6 and 7 remain in M3 as written. | `crew_alpha` wave-1 delivery report and `crew_beta` PR #24 escalation, both 2026-08-22; changelog row of 2026-08-22 (`RT-21`/`RT-22`); ADR-0002 §§2, 4a, 5. |
| 2026-08-22 | **1** (factual correction + `RT-23`; fix and required behaviour unchanged) | **Site B is a LATENT instance, not a second reproducible abort — the vivid version of the discovery story was wrong at one of the two sites.** Since discovery this project has said Go version discovery "dies under `set -o pipefail` **at two sites**". Measured by `crew_beta` against the **live** go.dev index while re-capturing #22's evidence: 8662 distinct filenames, maximum duplicates for any one filename **1**, so `jq` emits ~65 bytes and SIGPIPE at site B would need **≥100 duplicate entries**. `jq` reads to EOF, so `curl` never SIGPIPEs; the early close lands on `jq`, which survives well under the 64 KB pipe buffer. **Site B's SIGPIPE mode is not reproducible with real data.** The crew built the fixture, watched it refuse to abort, and **measured the threshold instead of tuning the fixture until it went red** — *"I nearly shipped a fixture that aborted only because I fed it 400 duplicate entries real go.dev never produces, which would have been the same error crew_alpha refused, one layer deeper."* **Conditions 1 and 2 were not falsified and neither is weakened:** condition 1 already said "the same shape recurs" and "fixing only the first leaves a **latent** instance", which is exactly right, and condition 2 never mentioned site B. **What changed:** condition 1 now states the asymmetry explicitly rather than leaving `RT-02` to inherit site A's mechanism through the words "the checksum lookup, same" — the inheritance is what a reader, and a literal validator, would turn into a claim of a data-reproducible abort at site B. `RT-02` now names what it actually proves: site B's **reachable** defect, the same *consequence* as site A's — on a failed request the refusal diagnostic is killed by `set -e` before it prints. **`RT-23` allocated** for the structural assertion (no network producer feeds an early-closing consumer; `pipefail` disabled nowhere), because that is the only possible proof that the **latent** instance is closed and, unbound to an id, it could be deleted with condition 1 still reporting `PASS` — exception **(b)**, the same reasoning as `RT-21`. **`RT-02` is NOT reclassified as `preservation`:** site B's defect is real and reachable and its baseline genuinely exits non-zero at `139726e`; the derived `kind` stays `regression` and no mutant is owed. **The fix is unchanged and still closes both sites.** The discovery finding stands on its merits — the Codex architect found site B by reading rather than by a failure, which is the harder find, and a latent second instance is a *stronger* illustration of the half-fix shape than a visible one would have been, since a visible one dies for the first person who runs it. Only the vivid gloss is retired. | `crew_beta` re-capture of PR #22, 2026-08-22: live go.dev index — 8662 distinct filenames, max duplicates 1, ≥100 required for SIGPIPE; `tests/test_go_toolchain.py` `RT02ChecksumLookupSurvivesPipefail` and `BothSitesFixed` at `51a9fdd`; baseline `RT-02.json` exit 1 at `139726e`; `deploy/provision-ubuntu.sh:221` and `:241-243`. |
| 2026-08-23 | **section A header** (evidence-form cross-reference; also the *Requirement IDs* bullet's use of the same phrase). No condition's required behaviour, number or evidence list changes. | **The section-A header excluded the exact artifact condition 8 was revised to admit.** The header required, unqualified, *"a **baseline-failure record** proving that test fails against the pre-fix commit"* for every condition in section A. Read literally — and this project's review method is now to implement the prose as a literal validator — that excludes `RT-11`: condition 4's own required test and condition 8's first `preservation` record, whose baseline is `exit_code: 0` because it **passes** pre-fix by design, the property having held before the fix. PR #25 is what created the contradiction: it formalised the `preservation` form inside condition 8 and left the older umbrella sentence standing. Same defect shape as findings **B1**, **B2** and **N3** in the review of that same PR — a rule written before an exception existed and not updated when the exception landed. **Changed:** the header now states the pre-fix obligation per form — `regression` ⇒ the test failed at the pre-fix commit; `preservation` ⇒ it passed pre-fix *and* carries condition 8's mutation evidence — and the `RT`-id bullet at the top of the file says "baseline record in whichever of the two forms condition 8 admits" rather than repeating the same excluding phrase two paragraphs earlier. **Not a relaxation, and not an escape hatch:** the header restates in place that the form is **derived from the capture's `exit_code`, never chosen**, and that no section-A condition may rest on `preservation` records alone — both already binding under condition 8, neither previously visible where the reader meets the requirement. A `preservation` id is therefore held to a *stronger* bar than a `regression` one (passing baseline **plus** a mutant that breaks a declared production file under a byte-identical command), so nothing becomes provable that was not provable before. Every other section-A id (`RT-01`…`RT-10`, `RT-12`…`RT-15`, `RT-21`…`RT-23`) is `regression` and is unaffected. | `crew_beta` cross-crew re-check of PR #25, 2026-08-23T01:12Z (blocking finding: `DEFINITION-OF-DONE.md:85` vs. condition 4's `RT-11`); `docs/audit/baseline/RT-11.json` on `beta/10-watchdog-liveness` — `exit_code: 0`, mutants `M1`/`M2` at `1feb881`; condition 8's derived-`kind` rule and section-A guard as landed at `c0436ea`. |
