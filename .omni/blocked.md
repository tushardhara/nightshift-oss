# blocked

## RESOLVED 2026-08-28T~14:30Z — `release`'s Codex sandbox cannot pass `ProjectTrustedAncestry` (issue #245)
- **Root cause, confirmed by direct reproduction:** `release`'s local candidate-merge gate fails
  `tests.test_project_bind.ProjectTrustedAncestry` (14 failures + 1 error) whenever it runs inside
  Codex's default bwrap-based Linux sandbox (an unprivileged user namespace mapping only the
  calling uid). Root-owned host paths — including `/`, root-owned on any normal Linux filesystem —
  report as the kernel overflow uid (65534) from inside it, and `$HOME` outside the declared
  `writable_roots` is read-only (`EROFS`). Reproduced directly (`codex sandbox -- stat`, 3/3 runs)
  and confirmed generic, not Codex-specific (plain `bwrap --unshare-user --ro-bind / /` reproduces
  the identical remap with no Codex involved).
- **Trust-boundary call: RESOLVED.** `architect` wrote ADR-0041 (supplements ADR-0003, preserves
  ADR-0021's ownership predicate unchanged), `staff` reviewed it CONSISTENT with no merge blocker.
  Decision: split the gate by execution capability — `ProjectTrustedAncestry` is tagged and skips,
  by exact test id against a checked-in manifest, only when a uid remap is detected locally; every
  other test still runs and must still pass; the same tagged tests stay mandatory on the exact
  candidate SHA in GitHub Actions; `release` must verify that GHA status before merging. Rejected:
  running candidate code unsandboxed on this host, trusting uid 65534 as root, and relying on
  deprecated legacy Landlock as a durable fix. Full text:
  `docs/decisions/ADR-0041-split-release-gate-by-capability.md`
  (`architect/245-adr-0041-split-gate`, `2407f8b94bc20243a04423bc691501ce218d84b6`).
- **Implemented:** PR #249 (branch `fix/245-runsh-uid-remap-preflight`) — `tests/host_uid_ancestry.py`
  (the detector + skip decorator), `tests/host-uid-skip-manifest.json` (the exact 12-test-id
  manifest, derived by running the class under a simulated remap, not hand-written), and
  `tests/run_suite.py` (audits the actual skip set against the manifest; fails the gate on any
  mismatch). Verified end to end: full suite green on the bare host (no skips) and under a
  simulated remap (exactly the 12 manifest tests skip, everything else — ~900 other tests —
  still runs and passes, gate exits 0).
- **Item 4 (GHA-status join) also implemented and merged:** PR #252
  (`platform/245-release-gha-status`) wires `release`'s merge procedure to `bin/nightshift_github.py`'s
  `enumerate_check_runs`/`evaluate_required_checks` (no second GitHub-polling implementation) and binds
  the exact head SHA to `gh pr merge --match-head-commit`, closing a real race window crew_beta's
  cross-crew review found and platform fixed. **Issue #245 fully closed** — root cause, trust-boundary
  decision (ADR-0041, `docs/decisions/ADR-0041-split-release-gate-by-capability.md`, accepted), and
  both implementation PRs (#249, #252) all landed. Verified post-merge: 950 tests, `OK (skipped=12)`,
  no recurrence of the original failure.
- **Also unresolved, lower priority:** why PR #246's `release` run happened not to hit the remap
  locally while #244 (×2) and #247 did, before this ADR landed. Not investigated further since the
  ADR makes the local sandboxed result non-authoritative on its own either way — but worth a look
  if session/transcript access to `w9-integrate-244/246/247` becomes available, for completeness.

## RESOLVED 2026-08-22T21:05Z — was: scheduled-task creation unauthorized (HTTP 401)
- **CLOSED.** The durable hourly heartbeat now exists (task `f3e532fd631045858aa386a26405d56a`,
  `FREQ=HOURLY`, verified via `sys_scheduled_task_list`). The 401 no longer reproduces; the call
  succeeds when `workspace` is omitted. Needs nothing from the owner. Original text below.
- ~~**Scheduled-task creation is unauthorized (HTTP 401).**~~ `sys_scheduled_task_create`
  fails with `{"code":"unauthorized"}` while `sys_scheduled_task_list` succeeds, so the
  session can read schedules but not create them.
  - **Unblocks:** a durable hourly heartbeat that survives a session drop, a service
    restart, or a reboot — the mechanism that carries this run across days.
  - **Working around it:** a repeating 900s in-session timer
    (`timer_75606428e7674ee185a2aa9a3b8785b1`). The run stays autonomous while this
    session lives; if the session dies, only `deploy/nightshift-watchdog.sh` can
    restart it, and auditing that path is already in scope for this project.
  - **What is needed:** a credential/scope permitting scheduled-task creation for the
    program session. Nothing else is blocked by it.

## RESOLVED 2026-08-23T10:49Z — was: every Codex seat dead (bwrap/userns, issue #5)
- **CLOSED.** Owner installed `/etc/apparmor.d/bwrap` at 10:49Z on 2026-08-23 (acting on the
  `state:blocked` label below). Re-probed from two independent seats: `bwrap --ro-bind / / /bin/true`
  exits 0, real `uname` output, no AppArmor denial. `architect` and `release` (both Codex) confirmed
  alive and have since executed real work repeatedly (e.g. `architect` ran full disclosed suites by
  execution and posted a durable PASS on PR #234 at 2026-08-28T06:?, `release` has merged multiple PRs
  across waves 3–9). This entry was left unmarked in the file for several waves — a documentation gap,
  not a live blocker; fixed at 2026-08-28 heartbeat. Note: bwrap cannot nest, so a functional bwrap
  probe run from inside an already-sandboxed shell can still fail locally; that is environmental, not a
  reproduction of #5, and CI (unnested) is the authoritative gate — see ledger 2026-08-24 entries.
  Original text preserved below for the record.
- ~~**Every Codex seat is dead on this box: `bwrap: setting up uid map: Permission denied`.**~~
  Codex runs each model-issued shell command inside its own bubblewrap sandbox. This
  host is Ubuntu 24.04 with `kernel.apparmor_restrict_unprivileged_userns = 1`, and
  `bubblewrap 0.9.0-1ubuntu0.1` ships no AppArmor profile here (`/etc/apparmor.d/` has
  `unprivileged_userns` and `lxc-usernsexec`, no `bwrap`). Unprivileged user namespaces
  are refused host-wide — even bare `unshare -U -r /bin/true` fails with
  `write failed /proc/self/uid_map: Operation not permitted`.
  - **Dead seats:** `architect`, `release`, `eng_codex`, `qa`, `reviewer_codex`.
  - **Unblocks:** the QA gate and `release` — the ONLY agent permitted to merge to the
    default branch. Claude seats are unaffected, so crews can still build and open PRs;
    nothing can be merged or verified on the default branch until this is fixed.
  - **What is needed — as root, either one:**
    1. *(preferred — restores the intended boundary rather than removing it)*
       ```
       printf 'abi <abi/4.0>,\ninclude <tunables/global>\nprofile bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n  include if exists <local/bwrap>\n}\n' > /etc/apparmor.d/bwrap
       apparmor_parser -r /etc/apparmor.d/bwrap
       ```
    2. *(blunter, host-wide)* `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`
       and persist it in `/etc/sysctl.d/`.
    Verify with `bwrap --ro-bind / / /bin/true; echo $?` → must print `0`.
  - **What I will NOT do without you:** set `sandbox_mode = "danger-full-access"` in
    `~/.codex/config.toml`. That would unblock Codex by deleting its sandbox, which the
    brief forbids. Say the word and I will, but it is your call, not mine.
  - **Meanwhile:** discovery continues. Codex's architecture draft is being produced with
    evidence pasted inline and returned as text.

## Needs the owner — 2026-08-28 (external tool schema, not urgent, has an approved workaround)
- **`sys_session_send`'s `args` schema (`additionalProperties: false`) rejects `slot_id`/`task_id`/
  `generation`, which `deploy/policies/omnipods_policies.py`'s `worker_semaphore` policy requires for any
  named send to a counted engineer/QA/reviewer worker.** Confirmed by `platform` (task
  `228a-slot-dispatch-deadlock`, 2026-08-28): reproduced across 4 parameter shapes, 2 vendors, git-history
  confirmed present since wave 1 (`.omni/ledger.md:3371`, ~2026-08-23, PR #94) — **not** a regression from
  issue #212 (#212 only added `task_id`/`generation` on top of an already-broken `slot_id` contract).
  **Not fixable in this repo**: `sys_session_send` is a native tool of the external `omnigent` engine,
  pinned only by commit SHA in `deploy/omnigent-runtime.lock.json` (`ef39b91a…`) — not vendored, not
  installed, not inspectable here. The policy hook only ever sees an event AFTER the tool's own schema
  validation already passed, so it structurally cannot pre-empt the rejection.
  - **Unblocks:** genuine in-crew engineer/QA/reviewer delegation (a lead dispatching its own counted
    workers with slot bookkeeping) — this has never worked successfully once in this project's history.
  - **Not blocking delivery today**: Working Agreement #5 (`.omni/working-agreements.md`, wave 1, issue
    #13) is the standing, already-approved fallback — the crew lead builds directly under a slot it holds
    and says so in the PR body; cross-crew review becomes mandatory. Every PR shipped so far was built
    this way. This ask is about restoring the INTENDED delegation model, not about being currently stuck.
  - **What is needed:** a fix to the `sys_session_send` tool's schema in the external `omnigent` repo
    (`tushardhara/omnigent`, pinned commit `ef39b91a…`) to accept `slot_id`/`task_id`/`generation` as
    object properties on `args` — outside this repo's control.
  - **What I will NOT do without you:** loosen `worker_semaphore` to accept these fields via an
    already-allowed channel (e.g. overloading `purpose`) — evaluated and rejected by `platform`, since it
    would either break existing phase logic or encode control-plane authorization in a field ADR-0033
    explicitly disclaims trusting for that purpose. That is exactly the kind of gate-weakening workaround
    the brief forbids inventing unilaterally.
  - Tracked at https://github.com/tushardhara/nightshift/issues/236 and https://github.com/tushardhara/nightshift/issues/13.

## OPEN 2026-08-28T~13:50Z — cross-crew review has no admission-gated slot path (issue #253, distinct from #236)
- Discovered live during PR #249's and PR #252's cross-crew reviews (`crew_alpha`, `crew_beta`):
  `nightshift-ctl slot acquire`'s reviewer-continuation path only admits when the target task has an
  active admission AND is in `{building, ready, fixing}`. A task registered to track cross-crew review
  (state `review`/`done`, no admission row) is denied — and `platform`/`security`/`ux`/`staff` are all
  `EXEMPT_AGENTS`, so their tasks never get an admission row at all, meaning independent cross-vendor
  review of ANY exempt-team work currently has no slot path.
- **Not blocking delivery:** both crew leads did the adversarial review themselves rather than block
  (fully reproducible regardless of who ran it, per each review's own write-up), same posture as #236's
  WA-5 fallback. Tracked at https://github.com/tushardhara/nightshift/issues/253.
- **What is needed:** either a documented task-state/admission recipe a heartbeat can register a
  review-tracking task into so this actually admits, or a review-dispatch path in `nightshift-ctl` that
  doesn't require the same admission shape build dispatch does. In-repo fix, not external like #236.
