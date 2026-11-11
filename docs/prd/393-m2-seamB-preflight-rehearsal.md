# 393 — M2 Seam B: install rehearsal (D11) + preflight check manifest (D12)

**Review contract v1** — frozen at this version before the first implementation
commit (no PR exists against #393 as of this writing: `git log`/`gh pr list`
checked 2026-08-30). Any criteria change after implementation starts needs a
v2 here and invalidates prior sign-off on the affected criterion.

Owner: `pm` (crew_beta). Refs #393 (P1, size:M, `crew_beta`), Refs #5 (closed,
umbrella for bwrap/Codex-sandbox readiness — cited for the presence-vs-function
requirement below). Milestone: M2 (`docs/prd/MILESTONES.md:15`), exit criteria
DoD 8,9,10,11,12; this item covers **11 and 12**, the two seam-B conditions
(`docs/prd/MILESTONES.md:126-131`).

## Problem

`tests/dod_validate.py` at `origin/main` `64b4121` reports (confirmed by
running it, 2026-08-30):

```
D11 FAIL tests/linux/install-rehearsal.sh not found
D12 FAIL manifest declares no preflight_checks section
```

Full baseline (for the "no new FAILs elsewhere" requirement below):

```
D01 FAIL RT-23: unsatisfied ...
D02 PASS
D03 PASS
D04 PASS
D05 PASS
D06 FAIL RT-16: unsatisfied ...
D07 FAIL RT-18: unsatisfied ...
D08 FAIL manifest is structurally valid, but conditions.8.reviewed_by is not populated ...
D09 PASS
D10 PASS
D11 FAIL tests/linux/install-rehearsal.sh not found
D12 FAIL manifest declares no preflight_checks section
D13 FAIL CONCURRENCY.md not found
D14 FAIL SURVIVAL.md not found
D15 FAIL PIPELINE.md not found
D16 FAIL BOUNDARIES.md not found
D17 FAIL INSTALL.md not found
D18 FAIL FINDINGS.md not found
D19 FAIL manifest declares no protected_capabilities
D20 FAIL manifest declares no sentinel_test_node ...
D21 FAIL docs/HANDOVER.md not found ...
D22 FAIL manifest declares no 'changes' ...
D23 FAIL docs/HANDOVER.md not found
D24 FAIL TRACEABILITY.md not found
```

D01, D06-D08, D13-D24 belong to other milestones/other crews (M1/M3/M4/M5,
mostly seam A or `product`/cross-crew evidence) and are explicitly **out of
scope** for this item — see Out of scope.

## Impact

Without D11, nothing proves the install path works from a clean commit rather
than from whatever an engineer's laptop happens to have lying around — the
exact gap that let bugs 1/2/3/5 (`docs/prd/DEFINITION-OF-DONE.md` §A) ship
originally. Without D12, `deploy/preflight.sh`'s checks are not enumerated
anywhere machine-checkable, so a check can be silently deleted, weakened back
to presence-only, or left un-actionable and nothing catches it — which is
exactly how issue #5 happened (`command -v bwrap` reported healthy while every
Codex seat died at its first tool call).

## Scope decision — one backlog item, not two

D11 and D12 are independently testable (`tests/dod_validate.py --self-test`
runs `check_d11`/`check_d12` as fully separate checks) and I considered
splitting into two issues. Combining, because:
- `docs/prd/MILESTONES.md:126` already rows them together as one seam-B unit.
- Both write to `docs/prd/DOD-MANIFEST.json`; two issues in flight at once
  would race on the same JSON file for no benefit (different top-level keys,
  `rehearsal_ci_job` vs `preflight_checks`, but still one file).
- Neither unblocks the other or unblocks other crews' work on its own — M2
  needs both 11 and 12 (plus 8,9,10) before the milestone closes, so there is
  no sequencing win from shipping one first.
- crew_beta issues in this tracker are otherwise single-PR-sized bug/feature
  units (see #12, #23, #34, #45 etc.) — splitting a paired seam-table row into
  two tracker issues with no tracking/epic label available (`gh label list`
  has no epic/tracking label) would be bookkeeping without payoff.

The two criterion sets below (`D11-*`, `D12-*`) stay separately numbered and
separately checkable, so acceptance sign-off can still say "D11 met, D12 not
met" if only one lands clean.

## Current state (read before starting — do not rebuild what exists)

- `deploy/lib/deploy-lib.sh:148-195` already has a **functional** bwrap probe
  (`nightshift_bwrap_probe`, `nightshift_bwrap_diagnosis`) from RT-13/RT-14/RT-15
  (issue #12, closed). It runs `bwrap --ro-bind / / /bin/true` and returns
  distinct exit codes for usable/installed-but-broken/missing. `deploy/preflight.sh`
  (~line 58-66), `deploy/provision-ubuntu.sh` and `deploy/bootstrap.sh` all call
  it. `tests/test_bwrap_probe.py` (RT13/RT14/RT15 classes) already covers it
  end to end, including the AppArmor/userns diagnosis wording.
- **D12 is therefore substantially a manifesting task, not a build task** for
  the bwrap row specifically — issue #393's own scope note says so, and reading
  the code confirms it. Do not re-derive or restructure the probe; reference it.
- `docs/prd/DOD-MANIFEST.json` currently has only 5 top-level keys: `_notes`,
  `schema_version`, `required_ci_jobs` (`["tests"]`), `conditions`, `rt_ids`.
  No `preflight_checks`, no `rehearsal_ci_job`.
- `.github/workflows/tests.yml` has exactly one job, `tests`, which runs
  `tests/run.sh` (→ `tests/run_suite.py`) plus a shell-parse step. Per
  `docs/owners.md`, `.github/workflows/**` is **platform-owned and
  single-writer, never changed mid-wave** — this item's diff must not touch it.
- `tests/run.sh` / `tests/run_suite.py` are "test fixtures / harness" —
  platform-owned but explicitly **not single-writer**, additive changes
  preferred (`docs/owners.md`). Reusing the existing `tests` CI job by adding
  an invocation there (rather than a new workflow job) avoids needing a
  platform lease.
- `check_d11`/`check_d12` (`tests/dod_validate.py:1000-1050`, exact logic read
  2026-08-30) are the literal gate. They live in a file whose principal
  ownership for conditions 8/9 is `crew_alpha` (`docs/prd/MILESTONES.md:127`)
  — **this item does not edit `tests/dod_validate.py`.** What they check,
  verbatim:
  - `check_d11`: `tests/linux/install-rehearsal.sh` exists; its text
    (case-insensitive) contains all of `hermetic`, `networked`, `stub`;
    manifest has a `required_ci_jobs` list and a `rehearsal_ci_job` value that
    is a member of it.
  - `check_d12`: manifest `preflight_checks` is a non-empty list; `deploy/preflight.sh`
    exists; every entry is a dict with `id`, `severity`, `remediation`;
    `severity` ∈ {`FAIL`, `warn`}; `entry["id"]` is a literal substring of
    `deploy/preflight.sh`; `bash -n deploy/preflight.sh` exits 0.
  - Neither check calls `gh` or verifies live CI status for the tip commit —
    that reconciliation (the DoD prose's "concluded success for the tip") is
    condition 10's job, seam A. Named as a dependency below, not something
    this item can single-handedly complete.
  - `check_d12` does **not** itself verify the manifest is *complete* (every
    check in `deploy/preflight.sh` enumerated) — it only validates whatever
    rows are present. Criterion D12-3 below closes that gap with our own test,
    the same doctrine `tests/dod_validate.py`'s own `--self-test` uses ("a stub
    that returns PASS is the defect this file exists to prevent").

## Done when

**D11 — observable behaviour:** anyone can run one documented command against
a clean checkout of the exact tested commit — no bind-mounted working tree,
no reliance on the developer's local Go/npm/tool cache — and see a `PASS:`
line for each of DoD conditions 1 (Go pipefail resilience, both sites), 2
(missing Go → install, not stop), 3 (`systemctl --user` capability
established without an inherited session), and 5 (bwrap functional probe) —
proven by executing the real `deploy/provision-ubuntu.sh` / `deploy/bootstrap.sh`
code, not a reimplementation of it.

**D12 — observable behaviour:** every check `deploy/preflight.sh` currently
performs is named, in `docs/prd/DOD-MANIFEST.json`, with a stable id, a
severity, and a remediation an operator can act on without reading the shell
source — and a change to `deploy/preflight.sh` that adds, removes, or silently
weakens a check is caught by a test, not by someone noticing later.

## Detailed acceptance criteria

### D11 — install rehearsal

1. `tests/linux/install-rehearsal.sh` exists, and its header states, per step,
   whether the step is hermetic, requires network, or is stubbed (all three
   literal words present — `check_d11`'s exact requirement) — with a named
   reason for any stub.
2. Given the same commit checked out two ways — (a) a normal working-tree copy
   with one extra untracked file added, and (b) what `install-rehearsal.sh`
   actually extracts — the extracted tree does not contain the untracked file.
   Demonstrated via `git archive <sha> | tar -x` (or equivalent) into an empty
   directory, never a bind mount of the working tree.
3. The rehearsal asserts `/srv/nightshift` (or the script's `TEAMS_DIR`
   equivalent) is absent before the install steps run, and fails loudly,
   naming the path, if it is not.
4. Running the rehearsal against the current tree prints one `PASS:` line each
   for conditions 1, 2, 3, 5 (four lines minimum), each obtained by executing
   the real code path in `deploy/provision-ubuntu.sh` / `deploy/bootstrap.sh`
   — not a hand-written reimplementation of the same logic. A network-dependent
   sub-step (e.g. `apt-get update`, Go tarball download) may be declared
   `networked` in the header; the decision logic being exercised (e.g. "no Go
   present → proceed to install, don't abort") is never stubbed even if its
   network call is faked, per the header's per-step declaration (criterion 1).
5. Condition 3 specifically: the rehearsal proves the outcome DoD condition 3
   requires — user services end up installed and enabled OR the run exits
   non-zero naming the root remediation — without assuming an inherited
   `systemctl --user` session (the original bug's precondition, `su -
   nightshift` with no session bus). If the chosen container base image cannot
   host a real user session bus at all, that limitation is named in the
   header's per-step declaration (criterion 1) with the reason, and the
   decision-logic assertion (enabled-or-named-remediation) still runs for
   real against the actual script.
6. `docs/prd/DOD-MANIFEST.json` gains `rehearsal_ci_job` naming one entry
   already present in `required_ci_jobs` (reuse `"tests"` unless a documented
   reason requires a new job — see Dependencies/Open questions on why a new
   workflow job is disfavored).
7. `python3 tests/dod_validate.py` prints `D11 PASS`.

### D12 — preflight check manifest

1. `docs/prd/DOD-MANIFEST.json` gains a `preflight_checks` array. Each entry:
   `id` (stable, unique, a literal substring already present — or added — in
   `deploy/preflight.sh`), `severity` (`FAIL` or `warn`, matching how that
   check currently behaves — `bad` → `FAIL`, `warn` → `warn`), `remediation`
   (non-empty, names what to run or where to look, no "properly"/"correctly").
2. One entry exists for the bwrap functional check specifically, with
   `severity: FAIL` and a `remediation` that names the exact deterministic
   verification command `bwrap --ro-bind / / /bin/true; echo $?` (the model
   `.omni/blocked.md` already uses, and the string `nightshift_bwrap_diagnosis`
   already produces) — referencing the existing probe, not a new one.
3. `tests/test_preflight.py` contains a structural test asserting the number
   (and id set) of distinct FAIL/warn diagnostic branches in `deploy/preflight.sh`
   (a static scan for `bad "` / `warn "` call sites, analogous to
   `tests/test_bwrap_probe.py`'s `RT15NoPresenceOnlyCheckSurvives` manifest
   scan) equals the entries declared in `preflight_checks` — so a check added
   to `preflight.sh` without a matching manifest row fails this test, and vice
   versa. This is the completeness guard `check_d12` itself does not provide.
4. For every `FAIL`-severity entry whose triggering condition can be forced
   without touching live host services/network (i.e. it reads a file or env
   var reachable through `deploy/preflight.sh`'s `$PROJECT_DIR` argument or an
   existing `NIGHTSHIFT_*`-style override) — `tests/test_preflight.py` runs
   `deploy/preflight.sh` once with the condition forced broken (asserts the
   exact `FAIL` line and its remediation text appear) and once forced
   satisfied (asserts no `FAIL` line for that check). Reuses the
   `NIGHTSHIFT_BWRAP` override convention where one already exists (bwrap);
   uses a scratch `PROJECT_DIR`/`$HOME` for file-presence checks (mirrors the
   existing pattern in `tests/test_bwrap_probe.py`'s `run_preflight` helper).
5. Any `FAIL`-severity entry that genuinely cannot be forced hermetically
   (live systemd unit state, a live network socket bind, tailscale/docker
   daemon state) is exempted from criterion 4 explicitly — its manifest entry
   carries a one-line `fixture_exempt_reason`, and it is still counted by
   criterion 3's completeness scan. Never silently dropped from the array.
6. A test in `tests/test_preflight.py` asserts `deploy/preflight.sh` exits 0
   when zero `FAIL` branches printed and non-zero when at least one did
   (two cases: a clean scratch environment, and one forced `FAIL`).
7. `bash -n deploy/preflight.sh` exits 0.
8. The diff for this item touches only: `docs/prd/DOD-MANIFEST.json` (rows /
   the new `preflight_checks` section — no other key), `deploy/preflight.sh`,
   `deploy/lib/deploy-lib.sh` (only if closing an actual gap the manifesting
   pass finds — e.g. a check whose id string isn't yet a stable substring),
   and `tests/test_preflight.py`. `docs/prd/DEFINITION-OF-DONE.md` and its
   changelog are not touched by this item under any circumstance — `product`
   is its sole writer (`docs/owners.md`, `docs/prd/DEFINITION-OF-DONE.md`
   "Change control").
9. `python3 tests/dod_validate.py` prints `D12 PASS`.

### Both

10. `python3 tests/dod_validate.py` run from the PR's tip: lines `D11` and
    `D12` read `PASS`; every one of the other 22 lines is **byte-identical**
    (same status, same reason text where applicable) to the baseline captured
    above under Problem. Any other condition that flips status is out of
    scope for this item and must be reverted or split out, not accepted here.

## Failure and empty states

- If `tests/linux/install-rehearsal.sh` cannot obtain Docker in the developer's
  environment: the header documents the exact `docker run` invocation (mirror
  `tests/linux/toolchain-smoke.sh`'s header comment style) and the script
  itself, run with no Docker available, prints a clear one-line diagnosis and
  exits non-zero — never a silent skip that looks like success.
- If a `preflight_checks` manifest entry references an `id` that stops
  appearing in `deploy/preflight.sh` (renamed or removed check): `check_d12`
  already fails this (`"{id}: not found in deploy/preflight.sh"`) — no new
  behaviour needed, just don't break it.
- If `deploy/preflight.sh` itself is run against a box with zero failures: it
  must print `preflight clean — safe to start the run` and exit 0 (existing
  behaviour, `deploy/preflight.sh` tail — regression-guarded by D12-6).
- If the rehearsal's container base image lacks a capability the DoD condition
  3 check needs (a real session bus): the header names the gap per D11-5
  rather than the script hanging or reporting a false PASS.

## Out of scope

- D01, D06, D07, D08, D13–D24 (other milestones / other crews / `product`'s
  manual conditions) — unaffected by this item, and the "byte-identical"
  criterion above (Both-10) is what protects that boundary.
- Editing `tests/dod_validate.py` (crew_alpha's principal file for the
  validator itself, per `docs/prd/MILESTONES.md:127`).
- Editing `docs/prd/DEFINITION-OF-DONE.md` or its changelog — manifest rows
  only, per `docs/owners.md` and the DoD's own "Change control" section.
  `product` is the sole author of that file.
- Editing `.github/workflows/**` (platform-owned, single-writer, "never
  changed mid-wave" per `docs/owners.md`) — the rehearsal must reuse the
  existing `tests` job via an additive `tests/run.sh`/`tests/run_suite.py`
  change, not a new workflow job, unless a platform lease is explicitly
  granted (see Dependencies).
- Rebuilding or redesigning the bwrap functional probe itself
  (`deploy/lib/deploy-lib.sh:148-195`) — RT-13/RT-14/RT-15 (issue #12) already
  closed that; this item references it.
- Re-opening the "bind every `writable_roots` entry into the bwrap probe"
  design question raised in issue #5's later comments (`~/go`, `~/.local/state`
  bind-mount failures). That was addressed separately (directory-creation +
  warn path in `deploy/bootstrap.sh`/`deploy-lib.sh`, `~/.local/state` later
  removed per PR #35's F2 finding) and issue #5 is closed. Out of scope here;
  file a new issue if evidence resurfaces that the current probe misses a
  real failure mode.
- A real fresh-VPS install, a multi-day soak, an operator drill on the live
  host — explicitly listed as NOT conditions of done
  (`docs/prd/DEFINITION-OF-DONE.md` "Explicitly NOT conditions"). The
  container rehearsal is the **permanent** substitute for D11, by design.
- Any change under `/srv/nightshift` or `/home/nightshift/nightshift-runtime`
  (frozen paths, DoD condition 21).

## Dependencies

- **Condition 10** (CI triggers / required jobs / headSha equality — seam A,
  `crew_alpha`) is what actually confirms the rehearsal CI job "concluded
  success for the tip" per the DoD prose. `check_d11` itself does not verify
  this — it only checks the manifest names a `rehearsal_ci_job` that is a
  `required_ci_job`. This item can reach `D11 PASS` on its own; the fuller DoD
  condition 11 text is only completely satisfied once condition 10 is also
  green. Not blocking for dispatch — named so sign-off doesn't over-claim.
- Reusing the existing `tests` CI job (rather than requesting a new
  `.github/workflows/**` job) depends on the rehearsal's runtime fitting inside
  that job's existing `timeout-minutes: 15` budget alongside a `docker pull
  ubuntu:24.04`. If it does not fit, this item's crew raises that to `pm`/lead
  for a platform-lease request rather than editing the workflow file directly.
- `deploy/lib/deploy-lib.sh`'s existing bwrap probe (RT-13/14/15, issue #12,
  closed) — D12's bwrap row depends on it existing exactly as read above; no
  action needed beyond referencing it correctly.

## Open questions (none block dispatch)

1. Does the install-rehearsal container need a systemd-capable base image to
   exercise condition 3 for real, or is a stub-with-named-reason (D11-5)
   acceptable for the session-bus precondition specifically? **Not blocking**
   — default: the crew documents whichever it implements in the header per
   D11-1; a stub is acceptable there only, per D11-5's explicit carve-out.
2. Will the rehearsal fit inside the existing `tests` job's 15-minute budget?
   **Not blocking** — default: image layer caching / a pinned digest first;
   if that's insufficient, escalate per the Dependencies note above rather
   than silently expanding CI scope.
3. Is `~/.local/state`'s prior removal from `writable_roots` (PR #35, F2)
   still current, i.e. does any D12 fixture need to account for it? **Not
   blocking** — the manifesting pass reads `deploy/bootstrap.sh`/`deploy-lib.sh`
   as they exist at close-out (D12's own text), so whatever is current when
   this item is implemented is what gets manifested; no need to resolve the
   history first.

## Definition of Ready — verdict

- [x] Outcome stated as observable behaviour (Done when, above) — not "add a
      preflight_checks field".
- [x] Acceptance criteria numbered, each checkable by a test or a named
      manual step (D11-1..7, D12-1..9, Both-10). No "properly"/"correctly"/
      "as expected" used.
- [x] Failure and empty states specified.
- [x] Out of scope listed explicitly, including the two adjacent temptations
      (rebuilding the bwrap probe, editing DEFINITION-OF-DONE.md/workflows).
- [x] Dependencies named (condition 10/seam A; CI time budget; existing
      bwrap probe).
- [x] Open questions answered or marked non-blocking (all 3 non-blocking).

**All six boxes checked. Issue #393 stays `state:ready`.**
