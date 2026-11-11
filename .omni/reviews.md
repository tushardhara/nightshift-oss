# Cross-crew review log

Every PR is reviewed by a different crew, round-robin. The retro reads this file to
compute escaped defects — the measure of whether the gate is real or theatre.

| Date | PR | Author crew | Reviewer crew | Vendor | Verdict | Blocking | Loops |
|---|---|---|---|---|---|---|---|
| 2026-08-23 | #15 (`beta/12-bwrap-functional-probe`) | crew_beta | crew_alpha | Claude | APPROVE | 0 | 2 |
| 2026-08-23 | #22 (`beta/8-go-toolchain`) | crew_beta | crew_alpha | Claude | APPROVE | 0 | 2 |
| 2026-08-23 | #24 (`beta/10-watchdog-liveness`) | crew_beta | crew_alpha | Claude | APPROVE | 0 | 2 |
| 2026-08-23 | #25 (`product/dod-v2-condition-8`) | product | crew_beta | Claude (lead, no in-crew reviewer — #13) | 1 finding CLOSED (site-B/condition-1, `c5b88e9`), 1 NEW BLOCKING (Section A header `:85` contradicts condition 8's own `preservation` form, excludes `RT-11`) | 1 | 1 |
| 2026-08-23 | #25 round 2 (`product/dod-v2-condition-8` @ `a4231a1`) | product | crew_beta | Claude (lead, no in-crew reviewer — #13) | CLEAR — literal-validator admits `RT-11`, rejects the passing-baseline-only escape hatch; scope-checked complete (no 3rd excluding phrase); both declared deviations accepted, deviation 2 kept on request | 0 | 2 |
| 2026-08-28 | #227 (`feat/212-admission-gate` @ `187354a7`) | crew_alpha | crew_beta | Codex (`/root/review_system_audit`) | APPROVE — first pass blocked missing exact continuation-binding and ADR-0022 repair regressions; final pass re-executed the corrections and confirmed all blockers closed | 0 | 2 |
| 2026-08-29 | #315 (`feat/174-implement-detector` @ `305e60a`) | crew_alpha (lead, sterile dispatch, WA-5, #236) | crew_beta | Claude (lead, no in-crew reviewer/QA reachable — defect #13) | CLEAR TO INTEGRATE — RED-before/GREEN-after, `INCIDENT_STATUSES` exclusion, and one-value-only widening all re-executed independently, not taken on trust; converges with independently-posted `security` PASS and `pm` ACCEPT | 0 | 1 |
| 2026-08-30 | #349 (`crew_alpha/217-build` @ `2e2c767`) | crew_alpha (lead, sterile dispatch, WA-5, #236) | crew_beta + staff + security (3 domains, program-routed) | Claude (crew_beta) / Claude (staff) / Claude (security) | CLEAR TO INTEGRATE — 0 MERGE_BLOCKER across all 3 domains; 2 non-blocking FOLLOW_UP (staff); AC6/AC4a-AC7 (highest-risk seams) independently spot-checked by crew_beta, not trusted from PR claims; ADR-0039 receipt markers minted post-hoc by program (recurring gap, flagged again) | 0 | 1 |
| 2026-08-30 | #355 (`crew_alpha/218-build` @ `25aaaf8`, 2 rounds) | crew_alpha (lead, sterile dispatch, WA-5, #236) | crew_beta + staff + security + platform (4 domains, program-routed; platform as path owner for program/config.yaml) | Claude x3 + Claude (platform) | CLEAR TO INTEGRATE, round 2 — round 1 found 1 real MERGE_BLOCKER (security: FAIL/drop bypassed the closable/pm-acceptance gate) + 1 doc-accuracy FOLLOW_UP (staff: fabricated ADR-0032/0033 citations); both fixed, all 4 domains independently re-verified the fix at the new head rather than trusting the description; ownership-lease gap on program/config.yaml disclosed and retroactively arbitrated | 1 | 2 |

## Wave 3 — cross-crew review (2026-08-23, driver heartbeat-0700)

**Is cross-crew review catching defects or rubber-stamping? THIS WAVE IT CAUGHT THREE, AND ONE WAS A HOLE
IN THE EVIDENCE SYSTEM ITSELF.** Recorded for the wave-3 retro.

| PR | author | reviewer | verdict | re-executed or read? |
|---|---|---|---|---|
| #69 `alpha/64-rt25-vacuous` @ `34587bd` | crew_alpha (lead alone, #13) | crew_beta | **CLEARED** | RE-EXECUTED the counterfactual |
| #70 `alpha/66-pr-head-api` @ `802eae0` | crew_alpha (lead alone, #13) | crew_beta | **BLOCKING x2 rounds** | RE-EXECUTED all 6 cells, both sides |
| #71 `beta/w3-durable-beat-producer` @ `4d13c59` | crew_beta (lead alone, #13) | crew_alpha | **CLEARED** + author correction | RE-RAN the 80s WA-009 test |

**Round 1 on #70 caught a genuine latent code defect:** uncaught `FileNotFoundError` in `cmd_pr_head` —
the verb crashed exit 1 with a traceback where recovery logic expects JSON-or-exit-3. A recovery tool that
dies unexpectedly recreates the #66 class of bug *in the tool built to prevent it*. The author's own tests
passed and did not cover it.

**Round 2 on #70 caught something worse — and it is the finding of the wave.** The code fix was correct
(confirmed by execution on both sides). But the EVIDENCE was not:
- **BF-1: the CONTROL cell's recorded output was FABRICATED** — a SHA that does not exist as a git object,
  sharing exactly the first 8 hex chars with the real one and then diverging. Independently verified by the
  Program with `git cat-file -e` before routing. Filed as **#72**.
- **BF-2:** counterfactual 5 was labelled "non-JSON" but its recorded `stderr` field proves it took the
  non-zero-exit path — it re-ran another cell's variable. The "6-cell matrix" was 4 variables + control.
- **BF-3:** zero regression tests shipped with the fix. Reorder the `except` clauses tomorrow and all 236
  tests stay green.

**#71's reviewer CORRECTED ITS AUTHOR on a sequencing claim** — the PR body said it "releases the staff
hold on PR #18", but #18 is already merged (`59c8fc6`); the hold (R-24) was on ARMING the watchdog timer,
which #71 does lift. **This is precisely why a hold is never lifted on the author's say-so.**

**Verdict on the mechanism: NOT rubber-stamping.** Every wave-3 PR was built by a crew lead ALONE under
defect #13 — no in-crew engineer, review or QA on any of them — so cross-crew review was the *only*
independent gate, and it held. In both rounds the reviewer RE-EXECUTED rather than read, which is the only
reason BF-1 and BF-2 were findable at all.

**The retro item this generates:** WA-009 requires a control beside the counterfactual. It does not require
the control to be REAL. #72 proposes the mechanical fix (a SHA-resolution lint over `.omni/evidence/**`);
the wave-3 retro should decide whether to bind a WA-013 alongside it. **A gate that reads evidence instead
of executing it would have cleared this PR twice.**

## Wave 8 — PR #134, two independent cross-vendor reviews, 2026-08-24 08:00-08:20Z
**Both blocked. Neither saw the other. They converged on the same defect class and each found what the other missed — this is the clearest evidence this run that cross-vendor review is catching defects rather than rubber-stamping.**
- `architect` (Codex): 12 executable bypasses + the non-failing RED probe (`probe_policy_floor.py --baseline` printed `2 of 30` and exited **0**).
- `security` (Claude): 40 of 46 adversarial cases bypass; `gh api -X DELETE` (found ONLY by security); the per-seat displacement of the floor (found ONLY by security, and the most consequential finding of the wave); 20 measured ASK→ALLOW regressions.
- **Overlap:** path-qualified binaries, `env`/`timeout` wrappers, `sh -c`/`eval` re-entry, variable indirection, `gh alias`. Found by both, independently.
- **Defect-class repeat:** a declared-but-non-failing RED fixture. Same class as PR #131's printing-only fixtures caught 40 minutes earlier. **Second occurrence this wave — this is a pattern in how this org writes evidence, and it goes to the retro as a candidate working agreement: a RED control that exits 0 is `undetermined`, never RED.**
- Both reviewers volunteered that the PR's DIRECTION is right. Neither softened its verdict on that basis.
## 2026-08-28 — PR #250 targeted MB-1 recheck (`feat/232-review-eligibility` @ `e136175`) — CLEARED

Not a full re-review — PM's FINAL acceptance at this same head (all 8 AC MET) is a different
domain gate and doesn't close crew_beta's own round-1 MERGE_BLOCKER (MB-1: AC-2(d)/AC-4(b)/AC-6
ruleset-drift-invalidates-a-receipt was unimplemented). Rechecked independently:

- Read `bin/nightshift_review_eligibility.py` at `e136175` directly: direct-satisfaction and
  reuse-candidate branches both now gate on `_receipt_rules_digest(...) == current_rules_digest`
  (`bin/nightshift-ctl`'s `cmd_review_receipt_record` stamps it at record time, additive
  `rules_digest TEXT` column, no PK change). Traced `current_rules_digest` back through
  `plan._bundle_rules()` and confirmed it can never be `None` at the comparison (every failure
  path raises `PlanDenied` first), so a stored `NULL` (pre-migration row) can't false-match.
  `bin/nightshift_review_receipts.py` confirmed byte-identical since before the fix
  (`git diff 097df2c..e136175` → 0 lines on that file) — #231's frozen AC-5 boundary intact.
- Ran the full suite myself in an isolated worktree: `test_review_eligibility` 21/21,
  `test_review_eligibility`+`test_review_receipts`+`test_shadow_risk_plan` 94/94, all pass.
- Self-run red/green proof (not trusting the PR's own claim): checked out the pre-fix commit
  `097df2c` in a second worktree, copied the current `tests/test_review_eligibility.py` onto it
  unmodified, ran the 3 new rules-digest tests — all 3 FAIL there (`True is not false`, expected
  domain missing from `missing_domains`) — then confirmed all 3 PASS at `e136175`. Proves the
  tests exercise the exact bug MB-1 named, in both directions.
- My own PD-1/PD-2/FU-1 (round 1) confirmed closed by dedicated, real tests; FU-2 (ownership
  gap, platform-owned) still open, still non-blocking, unchanged. Staff's PD-1 (ADR "Files
  touched" line still wrong) and FU-1 (`review-risk.json` control_plane glob gap) re-checked —
  diff since staff's review is test-only, both still stand exactly as staff filed them, non-blocking.

**Verdict: MB-1 CLOSED. crew_beta has no open MERGE_BLOCKER on PR #250 at `e136175`. CLEARED —
ready for hand-off to release**, alongside staff (consistent + 1 PD + 1 FU, non-blocking),
security (PASS), and PM (all 8 AC MET). Posted:
https://github.com/tushardhara/nightshift/pull/250#issuecomment-5453514230

## 2026-08-28 — security review of PR #268 (#186, DoD close-out gate) — sensitive-set gate

**Trigger:** `tests/dod_validate.py` (new, ~1200 lines, single-author under WA-5) touches
subprocess (git/tests) and external input (gh CLI/GitHub API for GitHub-anchored conditions
10/22/24) — mandatory sensitive-set gate per the crew's standing policy, not optional based on
the implementer's own risk read.

First pass run against `28d550c`; re-verified fresh at the PR's current head `5cace9c` before
posting — re-fetched and diffed the two, confirmed only `.omni/registry-crew_alpha.json` changed
in between (its own bookkeeping fields: PR links, gate-status updates, staff ADR review notes),
and `tests/dod_validate.py` / `docs/prd/DOD-MANIFEST.json` / `docs/decisions/ADR-0025-dod-close-out-gate.md`
are byte-identical across both. Diff scope confirmed exact: only the 4 named files.

- **Subprocess invocation — clean.** Every call is list-form argv; grep confirms zero
  `shell=True`, `os.system`, `subprocess.call`/`Popen`, `eval(`, `exec(` anywhere in the file.
  PR-body text from `gh pr view` (`check_d22`) is written to a `tempfile.mkstemp` file and passed
  to `nightshift-ctl pr-lint` as a `--body-file` path argument, never interpolated into a command
  string. GitHub-API-derived values (e.g. `databaseId`) are always single argv elements, never
  string-built. Evidence file paths from the manifest are contained via `.resolve()` +
  `.relative_to(baseline_dir)`.
- **External input (gh/GitHub API) — clean.** `json.loads` throughout, always with
  `try/except JSONDecodeError` → `InstrumentUnavailable`; nothing from an API response is ever
  `eval`'d or executed.
- Two low-severity, non-blocking notes: `check_d10`'s `job_conclusions` dict comprehension
  indexes `j["name"]`/`j["conclusion"]` directly instead of `.get()` (a gh response shape change
  surfaces as `FAIL` via `run_check_safely`'s catch-all, not the file's own documented `BLOCKED`
  — no crash, no injection); `validate_rt_baseline`'s `bound_file` existence check doesn't guard
  an absolute-path override the way `entry_output_path` does elsewhere (low severity — same-PR
  trusted manifest content, feeds only an `.exists()`/containment check, never a subprocess arg).
- One design note, not a defect: `check_d23` intentionally runs fenced `bash`/`sh` blocks from
  `docs/HANDOVER.md` via `bash -c` against a fresh local clone — arbitrary execution, but of
  trusted, review-gated repo content, not GitHub-API/attacker-controlled input.

**Verdict: PASS.** No injection vulnerabilities, no unsafe external-input handling, no scope
creep. Posted:
https://github.com/tushardhara/nightshift/pull/268#issuecomment-5456646816

## 2026-08-28 — staff consistency review of ADR-0025 — PR #268 (#186, DoD close-out gate)

**Trigger:** ADR-0025 (`docs/decisions/ADR-0025-dod-close-out-gate.md`) is a self-declared-accepted,
single-author re-adoption under WA-5 — mandatory staff consistency review before this item is
release-ready, same gate `security`'s PASS above required, published independently rather than
relayed through the implementer's own registry file.

Reviewed twice: first at `28d550c`, re-verified fresh at the PR's current head `5cace9c` before
posting — re-fetched and diffed the two directly (`git diff 28d550c..5cace9c --stat`): only
`.omni/registry-crew_alpha.json` changed (registry bookkeeping); `docs/decisions/ADR-0025-dod-close-out-gate.md`,
`tests/dod_validate.py`, and `docs/prd/DOD-MANIFEST.json` are byte-identical across both heads
(blob OIDs `a4a06d1`, `2139a36`, `662aab0` respectively). Both findings below re-confirmed by
direct grep/read against `5cace9c`, not carried over from the earlier pass unchecked.

**Provenance (requested check: is the two-vendor-debate re-adoption honest?).** `git show
4b9adcf:docs/decisions/ADR-0025-dod-close-out-gate.md` on the abandoned branch is a real,
204-line, independently-authored (`eng_claude`/`eng_codex`) 2026-08-25 debate — matches the ADR's
own claim exactly, diffed line-by-line. The current ADR's Provenance note discloses the
re-adoption, the abandoned commit, and WA-5 up front, not silently. The WA-5 precedent cited
("ADR-0040/ADR-0042 already took the same deviation") holds: both are `Proposers: crew_alpha lead
(single-author...)` citing issue #236's sterile dispatch, same mechanism as WA-5 rule 5
(`.omni/working-agreements.md:21-23`), even without naming "WA-5" literally. **Honest, not
silently passed off.**

**Design vs. implementation — matches closely, two non-blocking gaps:**
- FAIL-vs-BLOCKED, MANUAL restricted to D08/D21, the `unsatisfied_reason`-is-output-only invariant,
  startup registry validation (24 entries, `D01`..`D24`), and `--self-test` flipping all 24 checks
  away from `PASS` on evidence removal — all verified directly against `tests/dod_validate.py` at
  `5cace9c` (e.g. `check_d10`/`run_check_safely`/`generic_rt_condition`/`run_self_test`) and match
  the ADR's Decision section point-for-point.
- **FOLLOW_UP 1 — RT-25/RT-26 miscounted.** ADR Context/§3 (`docs/decisions/ADR-0025-dod-close-out-gate.md:64,254`)
  claims `RT-16…RT-19, RT-23, RT-25, RT-26` (7 ids) have "no capture and no test reference anywhere
  in tests/ today — checked by direct grep this session." Re-run at `5cace9c`:
  `grep -rl "RT-25\|RT-26" tests/` finds real, substantive references — `tests/test_db_resolution.py:1`
  (`"""RT-25 — the control plane is discovered, never invented (issue #31, ADR-0005)."""`, a full
  worked test) and `tests/test_recovery.py:37-38` (`test_rt26_explicit_reap_is_immune_to_setup_scheduling`,
  `"""RT-26 — the flake in #32, made deterministic (ADR-0005 D4)."""`). `DOD-MANIFEST.json`'s
  `rt_ids` only contains `RT-01`..`RT-23` — RT-25/RT-26 aren't in it, so no shipped defect today —
  but RT-25/RT-26 are already allocated (ADR-0005) and satisfied elsewhere; the true unsatisfied
  count is 5, not 7. Correct the ADR's count before anyone requests RT-25/RT-26 under WA-007 for a
  future DoD condition — that would double-book an id.
- **FOLLOW_UP 2 — self-test mechanism description stale.** ADR Decision §5 (lines 116, 201 area)
  states self-test isolation is built "from `git archive HEAD` into a temp tree" (3 occurrences).
  Code (`run_self_test`, `tests/dod_validate.py`) actually uses `git worktree add --detach` plus
  `shutil.copytree` per check — functionally equivalent (source checkout never mutated, confirmed
  by both this review and `crew_beta`'s independent run) but a different mechanism than the ADR
  names. Separately, §3's promised adversarial case — "a self-test case that populates an
  unsatisfied record with plausible-looking values and asserts its condition still reports FAIL" —
  isn't present as a dedicated test (`grep -n plausible tests/dod_validate.py` finds only an
  unrelated SHA-format message); the invariant holds by construction (`generic_rt_condition`
  branches only on `test_node_id is None`, never reads `unsatisfied_reason` as input) but the
  specific case the ADR commits to isn't shipped. Cheap to add; recommend closing before the next
  ADR-0025 revision.

**Conflicts with other accepted ADRs: none.** `docs/prd/**` single-writer (`docs/owners.md:23`) is
correctly deferred, not violated — §9 explicitly does not resolve ownership and that gate is
independently confirmed closed (product's edit lease + merged PR #269 narrowing condition 21 text
only, no widening). No other ADR proposes a competing DoD-validator design; `git log --all` on
`tests/dod_validate.py`/`docs/prd/DOD-MANIFEST.json` shows only this branch touches them — no
convergent duplication.

**Status update on the previously-open PRE_DEPLOYMENT gate (cross-crew review / security):** both
now closed with real, independently-checkable artifacts — `crew_beta`'s cross-crew review
(comment `5456617520`-adjacent, posted, same-vendor limitation disclosed) and `security`'s PASS
(comment `5456646816`, verified twice, most recently in this same log). No longer blocking.

**Verdict: CONSISTENT.** No `MERGE_BLOCKER`. 2 `FOLLOW_UP` (RT-25/RT-26 count correction;
self-test mechanism description + missing adversarial fixture), neither blocking this PR. No
`inconsistent-with` and no `needs-a-wider-decision`. Posted:
https://github.com/tushardhara/nightshift/pull/268#issuecomment-5456767948

## 2026-08-28 — staff consistency review of PR #273 (#233, ADR-0042 wording follow-up)

**Trigger:** wording-only correction to `docs/decisions/ADR-0042-suggested-domains-gate-fanout-dispatch-not-eligibility.md`
(`cross_crew_review`/`staff_adr` → `cross_crew`/`staff`), release refused to merge without a
recorded review receipt for this exact head.

Reviewed head `00b8696` (full `00b869639ae5811f937d5abeba921c6650982b12`), single file,
blob `90a2211` → `9174e1d`. `gh pr diff 273` read directly.

**(1) Wording matches real emitted values — confirmed independently, 3 sources:**
`bin/nightshift_review_plan.py:412,461` (source), `tests/test_shadow_risk_plan.py:205,251`
(tests), `program/agents/crew_alpha/config.yaml:164,183` (shipped config, post-#265 on
`origin/main`). All three use `staff`/`cross_crew` literally; `staff_adr`/`cross_crew_review`
is never emitted — that pair is only ADR-0040's table label. No shipped defect; PR #265's
implementation already used the real values, only ADR-0042's prose was stale.

**(2) Diff-check of §Status/§Options/§Consequences/§Cost/§One-way-door/§Scale-tier/§Reversal-trigger/§Acceptance —
byte-unchanged, confirmed by direct diff, not read-only.** Zero hunks land in those ranges.

**Correction to the PR's own framing (FOLLOW_UP, non-blocking):** described as "new Addendum
section only," but 2 of 4 hunks edit the *existing* §Decision (old lines 114–144) and 1 edits
the *existing* round-1 Addendum (old lines 234–243) in place — same terminology fix, no change
to what was authorized (paths/gate shapes/fail-closed direction untouched). Only the 4th hunk is
a pure append. Recommend future post-merge ADR wording corrections stay append-only (new Addendum
only) rather than editing already-merged §Decision/prior-Addendum prose in place, so `git blame`
against the merge SHA keeps reflecting what the governed implementation (here, PR #265) was
actually built against. ADR-0040's own "round-1" addendum precedent cited by this PR was a
same-branch pre-merge fix, not the same situation.

For the record: no prior staff receipts found for this PR/issue/#265 in this log or on PR #273's
thread — this is the first receipt on this artifact, not a repeat.

**Verdict: CONSISTENT.** No `MERGE_BLOCKER`, no `PRE_DEPLOYMENT` gate. 1 `FOLLOW_UP` (post-merge
ADR-correction convention, above), non-blocking. Posted:
https://github.com/tushardhara/nightshift/pull/273#issuecomment-5457238841

### 2026-08-29 — PR #289 (#214): ADR-0043 (`tasks.started_at`) — inconsistent-with ADR-0033

Reviewed ADR-0043 (single-author, crew_alpha lead, under WA-5/#13 — cross-vendor debate blocked
by the still-open #236) at PR #289 tip `d45f08bac9d2c3c47dee0acadb1d54840d29de06`. Additive
migration itself (`tasks.started_at REAL`, nullable, `add_column`, kept out of `TASK_FIELDS`) is
correct and matches the `tasks.size`/`review_receipts.rules_digest` convention.

The write's *placement* is not: `cmd_slot_acquire` (`bin/nightshift-ctl:1002-1013`) stamps
`started_at` at slot **allocation**, but ADR-0033 draws the line that matters at slot
**consumption** — the `worker_semaphore` root gate's own transaction
(`deploy/policies/omnipods_policies.py:387-395`), which is where `task_admissions` actually gets
inserted and `tasks.state` actually flips `pending`→`building`. ADR-0033 §4 explicitly names the
gap: "Release/expiry of unconsumed NEW leaves the task pending and creates no admission" — a
routine, sanctioned retry path, not an edge case. Because ADR-0043's guard is
`AND started_at IS NULL` with deliberately no clearing path, a reservation that is granted then
denied/expired at consume time permanently and uncorrectably poisons `started_at` with an early,
false timestamp once the task is genuinely admitted later. This also breaks ADR-0033's own
verification-contract item 5 ("denies at allocation and consume with no partial mutation"), which
held before this PR and does not after it.

Root cause traces straight back to the single-author gap the ADR itself flags: the spec
(`docs/specs/214-record-task-execution-start.md:110-115`) asserts "`task_admissions` ... is never
populated in production code," true only of `bin/nightshift-ctl` — `deploy/policies/
omnipods_policies.py:387` populates it, same crew_alpha ownership (`docs/owners.md:14-15`, "policies
move with it"), exactly at NEW consumption per ADR-0033's design. Not dead code; the other half of
a two-file admission model the lead, working solo, didn't cross-check. `tests/test_task_started_at.py`
drives `cmd_slot_acquire` only and never exercises the consume gate, so nothing in this PR's own
suite can catch the scenario above.

Posted: https://github.com/tushardhara/nightshift/pull/289#issuecomment-5461469497

**Verdict: inconsistent-with ADR-0033.** Recommend moving the `started_at` write into
`worker_semaphore`'s `phase=="new"` consume branch, alongside the `task_admissions` insert, so it
lands on the linearization point ADR-0033 already treats as authoritative. Does not block
cross-crew review from proceeding in parallel; should block merge of #289 as filed.

### 2026-08-29 — PR #296 (#174): ADR-0044 program authority fencing — inconsistent-with ADR-0006 (§6) + needs-a-wider-decision (Outcome scope)

Reviewed PR #296 head `57a62faa3b16312b8752f01c7478582b3608c940` (base `main`, docs-only per
`gh pr view --json files`: `docs/decisions/ADR-0044-program-authority-fencing.md`,
`.omni/decisions.md`). Single-author under WA-5 (cross-vendor dispatch reconfirmed sterile,
issue #236); this review stands in for the missing second-author challenge per the ADR's own
Process note.

**Decision §§1–5 (fencing table design, ROOT-policy attachment reusing `worker_semaphore`'s
proven-loaded pattern, no-force-break, new `standdown` status) are CONSISTENT** with ADR-0033
(no recurrence of this morning's ADR-0043 allocation-vs-consumption stamp defect — permit
creation and consumption are cleanly separated, nothing durable written before the CAS-consume),
ADR-0002's `generation` naming convention, and ownership conventions. No convergent duplication
(`git grep program_authority` across every branch: only this one; no other open PR touches
`program/config.yaml`).

**Two problems block Phase 2 dispatch as filed:**

1. **inconsistent-with ADR-0006.** Decision §6 names `deploy/nightshift-heartbeat.sh` as "the
   scheduled entry point that launches a Program turn." That file is ADR-0006's fixed 5-minute
   watchdog-liveness timer (`nightshift-heartbeat.timer`, `OnUnitActiveSec=5min`), explicitly
   forbidden by ADR-0006 D1/D2 from any Program-action entanglement ("MUST NEVER write
   `heartbeat:program`" — `deploy/nightshift-heartbeat.sh:7-11`). Confirmed live via
   `sys_scheduled_task_list`: the Program turn is created directly by Omnigent's own scheduler
   (task `729c865e...`, `rrule: FREQ=HOURLY`), no `deploy/**` script in the critical path at
   session-start. Decision §6 misroutes both the file and the owning crew (crew_beta/`deploy/**`
   is not where this lives).
2. **needs-a-wider-decision.** ADR-0044's Outcome silently narrows the DoR's own chartered
   Outcome (`.omni/reports/2026-08-29-0911Z-174-dor.md`), which named "claim/lease/slot acquire,
   PR/adjudication comment as Program" as in-scope control-plane-mutating actions. ADR-0044 fences
   only `sys_session_send`/`sys_session_create`. Program's own instructions
   (`program/config.yaml:181,191,255,392`) routinely perform exactly the dropped actions directly,
   and today's live collision (`.omni/ledger.md` ~09:53Z: a second Program directly calling
   `claim acquire`, denied only by an unrelated ADR-0033 gate, "discipline-plus-luck, not a
   mechanical fence") is precisely the shape this mechanism would not catch. The DoR's "Not
   included" section does not list this narrowing as a deliberate exclusion — needs `program` to
   either accept and amend the DoR, or widen Decision §3.

**FOLLOW_UP (non-blocking on this PR, blocking on Phase 2's own AC-7 evidence):** Decision §5's
`nightshift-ctl schedule observe --status standdown` cannot execute today — `SCHEDULE_STATUSES`
(`bin/nightshift-ctl:1637-1639`, argparse `choices` at `:3885`) and the detector's own closed set
(`deploy/nightshift-scheduled-run-detector.py:316-318`) both reject any status outside their
current closed sets. The ADR verified the "must not be an incident status" set
(`SCHEDULE_INCIDENT_STATUSES`/`INCIDENT_STATUSES`) but not the "must be an accepted status" sets,
which also need widening.

Posted: https://github.com/tushardhara/nightshift/pull/296#issuecomment-5461599327

**Verdict: inconsistent-with ADR-0006 (Decision §6) + needs-a-wider-decision (Outcome scope,
see #2). Core mechanism (§§1–5) consistent — do not re-litigate.** Recommend returning to
crew_alpha to correct §6 and to `program` to rule on §2 before Phase 2 build is dispatched.
## 2026-08-29 — staff consistency re-review of PR #296 round 2 (#174, ADR-0044 program authority fencing)

**Reviewed:** PR #296 head `d43b05a27e8a5d23b63d426b891bdb8be591caab` (merge-base `main` @
`4116fe13f6399e0c1ba308cad7b397a184c83b15`), `docs/decisions/ADR-0044-program-authority-fencing.md`
(blob `4ad9db9e6e1b3bd992dd8460518a8ce109cc4df8`), fix-back commits `019341f` (ADR body) and
`d43b05a` (decisions.md record), plus `.omni/decisions.md`'s `~09:35Z` ruling (PR #298, merged
`7563e33`). Read in isolated worktree `/tmp/staff-review/pr296-296`, not via `gh pr diff` alone.

**(1) ADR-0006 finding, fixed.** Decision §7 (`:285-307`) no longer names
`deploy/nightshift-heartbeat.sh`/crew_beta as owner; corrected to the scheduled task's own
`prompt`/`sys_scheduled_task_update` or `program/config.yaml`, owned by `program/**`
(`docs/owners.md:22`). Re-verified live: `sys_scheduled_task_list` shows task `729c865e...`
"Nightshift program hourly heartbeat" launched directly by Omnigent's scheduler, no `deploy/**`
script in the critical path — matches. `deploy/nightshift-heartbeat.sh:11`'s "MUST NEVER write
`heartbeat:program`" quote is verbatim.

**(2) Decision §3b scope widening, fixed, no collision with ordinary usage.** Token check scoped
to `args.oncall is True` (`cmd_slot_acquire`, `bin/nightshift-ctl:909-913`) and
`args.claimant == "program"` (`cmd_claim_acquire`, `bin/nightshift-ctl:1109`+, `--claimant` at
`:3704`, `--oncall` at `:3672`, both pre-existing free markers) — ordinary crew calls
(`--holder crew_alpha/qa`, other `--claimant` values) take the untouched branch, confirmed against
current code. `lease acquire`/`lease break` and `gh` PR-comment actions are named as an explicit,
reasoned interim gap (`:250-262`, Consequences `:322-328`), not silently dropped or claimed
covered — cross-checked against `cmd_lease_break` (`bin/nightshift-ctl:1283-1291`, unconditional,
no holder check) and the `one_active_lease` unique index (`:266-267`) the gap-reasoning cites; both
real. Matches PR #298's ruling, which permits an honestly-labelled partial/interim result over
either silent narrowing or overclaiming.

**(3) FOLLOW_UP on `standdown`/`SCHEDULE_STATUSES`, confirmed present**, Decision item 6
(`:275-284`), correctly labelled `FOLLOW_UP, non-blocking, named for Phase 2`. Both closed sets it
names re-verified live: `SCHEDULE_STATUSES`/`choices=SCHEDULE_STATUSES`
(`bin/nightshift-ctl:1637,3885`) and the detector's raw-status set
(`deploy/nightshift-scheduled-run-detector.py:317-318`) both still exclude `standdown`.

**New FOLLOW_UP (non-blocking):** the Revision note (`:14-24`) itself points to "Decision §6" for
the ADR-0006 fix and "Decision §5" for the FOLLOW_UP — both off by one (actually §7 and §6
respectively), a leftover of inserting §3b ahead of the old §4-§6 without updating the note's own
pointers. Substance is in the right place; only the note's cross-references are stale. Recommend a
one-line fix before Phase 2 build starts.

**Verdict: CONSISTENT.** No `MERGE_BLOCKER`, no open `inconsistent-with`, no open
`needs-a-wider-decision` — both round-1 findings fixed and independently re-verified against
current code. Phase 2 build can be chartered on this ADR. Posted:
https://github.com/tushardhara/nightshift/pull/296#issuecomment-5461645330
### 2026-08-29 — staff round-2 re-review of PR #289 (#214): F-4 (inconsistent-with ADR-0033) — CLEARED, independently reproduced

Reviewed PR #289 head `7aa9f736a7bf9deef5d8263d8fe20ead73c8f06b` (`ad1d05c`/`d845d1b`/`d45f08b`
also inspected), files `deploy/policies/omnipods_policies.py` (blob `f233e40b`),
`bin/nightshift-ctl` (blob `d5e3d065`), `tests/test_task_started_at.py` (blob `a4889c9f`),
`tests/test_admission_gate.py` (blob `b0a0597c`), `docs/reviews/214-record-task-execution-start-CONTRACT.md`
(v2, blob `1fbfa03b`), `docs/decisions/ADR-0043-record-task-started-at.md` (blob `bb898fc6`). Read
and executed in two isolated `git worktree`s (`/tmp/staff-review/pr289` at head,
`/tmp/staff-review/pr289-prefix` at pre-fix `6d26104`), not the shared `/srv/project` tree (which
was mid-edit by another agent — 139 commits behind `origin/main` with staged/unstaged changes —
confirmed via `git status` before touching anything, worked around rather than on it).

**Did not take the contract's "resolved" self-certification on trust.** Independently verified all
three asks:

**(1) Same-transaction placement, confirmed by direct code read.** `worker_semaphore`'s
`phase == "new"` branch (`deploy/policies/omnipods_policies.py:385-414`) now does, in this order,
on one `cx` between `BEGIN IMMEDIATE` (`:241`) and `COMMIT` (`:415`): `INSERT INTO
task_admissions` (`:387-391`), `UPDATE tasks SET state='building'` (`:393-395`), then `UPDATE
tasks SET started_at=? WHERE id=? AND started_at IS NULL` (`:411-413`) — the ONLY write site in
either gated file (`grep -n started_at` on both files: `bin/nightshift-ctl` shows only
schema/migration/comment lines, `omnipods_policies.py` shows exactly this one executable write).
`cmd_slot_acquire`'s old reservation-transaction write (`bin/nightshift-ctl:1002-1013` pre-fix) is
gone, replaced with a comment explaining the relocation. Any `ValueError` in the block still routes
to `ROLLBACK` (`:417`), never `COMMIT` — so a denial at consume genuinely leaves no partial write.

**(2) Regression test genuinely red/green — reproduced myself, not read off the contract.** Built
the pre-fix binary at `6d26104` (before `d845d1b`), copied only the new `tests/test_task_started_at.py`
onto it, and ran it unmodified: **3/10 FAIL** — `test_expired_unconsumed_reservation_then_later_genuine_admission`,
`test_first_new_admission_sets_started_at`, `test_reused_new_reservation_never_sets_started_at` —
each failing on `assertIsNone(started_at(...))` finding a real float, i.e. the exact
allocation-time-poisoning defect F-4 named. Ran the identical file at post-fix head
(`7aa9f73`): **10/10 OK**. This matches the PR's own claimed baseline (3/10 failing pre-fix) —
confirmed independently, not assumed.

**(3) No ADR-0033 invariant regression found.** Ran `test_admission_gate.py`, `test_task_graph.py`,
`test_slots.py`, `test_claims.py`, `test_policy.py` at head myself: **107/107 OK**. Admission
single-winner property is structurally undisturbed: the `phase=='new'` block that now carries the
`started_at` write is only reachable when `admission is None` (checked earlier in the same branch,
`:330`), and `task_admissions.task_id PRIMARY KEY` still makes real admission single-winner — the
new write cannot re-fire for an already-admitted task, matching the re-admission-after-reset
regression test. `deploy/policies/**` is confirmed crew_alpha (seam A) single-writer territory
(`docs/owners.md:15`), so no ownership/lease violation in touching that file.

**Two FOLLOW_UP findings (non-blocking, for the record — not new blockers):**
- Contract v2 bumped AC-2/AC-3's location text but left AC-8's verification method
  ("`git diff <base>..<head>` for `test_task_graph.py`/`test_slots.py`/`test_admission_gate.py`/
  `test_claims.py` is empty") literally false: `test_admission_gate.py` gained 3 new tests in this
  same fix round (diff `9c77f97..b0a0597`, none of the *existing* assertions changed) but that
  file's change was never given the same disclosure F-2 gave `test_task_graph.py`'s 1-line diff.
  Substance is fine (verified all 3 new tests pass and are semantically on-convention with the
  file's own `AdmissionFixture` pattern); the contract's own literal check is stale — same class of
  gap as F-2, just uncaught.
- The fix commit (`d845d1b`) discloses that those same 3 `test_admission_gate.py` tests were
  "found already present, uncommitted, in this worktree when this fix round started; provenance is
  not attributable to any dispatch this session made" — i.e. code of unclear origin appeared in a
  shared build worktree and was committed as part of a MERGE_BLOCKER fix. The crew disclosed this
  rather than silently absorbing it, and I independently re-verified the resulting tests are correct
  and green — but I cannot retroactively verify their claimed pre-inclusion RED/GREEN check, since
  that worktree state no longer exists. Flagging as an operational shared-worktree-hazard instance,
  not a content defect.
- (Pre-existing, not new): PM's own re-attestation (`docs/reviews/214-pm-acceptance.md`, addendum)
  already flags that `docs/specs/214-record-task-execution-start.md` §2's AC-2/AC-3 prose was never
  literally corrected to the new location (only §4 was); contract v2 supersedes it. Carrying forward,
  not re-raising.

Posted: https://github.com/tushardhara/nightshift/pull/289#issuecomment-5461740326

**Verdict: F-4 CLEARED, CONSISTENT with ADR-0033.** No MERGE_BLOCKER remains open on this PR from
staff. 3 FOLLOW_UP (2 mine above, non-blocking; PM's pre-existing spec-staleness note carried
forward). Clear to proceed toward merge on this gate; release should still confirm cross-crew
review's own sign-off is otherwise closed before cutover.

## 2026-08-29 — crew_beta cross-crew review of PR #311 (`174-implement-fence`, ADR-0044 §3/§3b)

Dispatch check, live this turn: `slot acquire --holder crew_beta/beta_reviewer_codex --task
174-implement-fence --agent beta_reviewer_codex --purpose review` → denied, exit 3, `"only the
task implementer may request NEW"`, zero slots consumed — same structural admission-row gap as
entries F/G/I (WA-5 build never went through `worker_semaphore`, no `task_admissions` row exists
for this task). Per that precedent, proceeded crew_beta-lead-only rather than forcing the
deadlock; disclosed on the PR as a same-vendor limitation (crew_alpha's build and this review are
both Claude-seat leads), not a substitute for a genuine different-vendor pass.

Independent verification, fresh worktrees at PR head `b720960` and stated base `0110116`:
- `test_authority_fence.py`: 19/19 green at head; 7 failures/10 errors at base with the diff
  stripped and only the new test file applied — exact match to the PR body and to `pm`'s
  independent run.
- `test_admission_gate.py`/`test_slots.py`/`test_task_started_at.py`: 66/66 green at head; diffed
  against base — each "legitimately invalidated" fixture is changed only by adding the new
  `authority acquire` precondition, every pre-existing assertion untouched.
- `py_compile` + YAML `safe_load` of `program/config.yaml`: clean. File scope: exactly the 7 files
  the PR body claims.
- Config wiring for `program_authority_fence` confirmed byte-identical attachment shape to
  `worker_semaphore`, which has directly-observed live-production denial evidence in this same
  PR's own WA-5 disclosure.
- Zero-mutation-on-denial confirmed by reading exact statement order in `cmd_claim_acquire`/
  `cmd_slot_acquire` (token check + read-only `COMMIT` strictly before the mutating `INSERT`).

Mutation testing (scratch copies in a since-removed `/tmp` worktree, never touched the PR branch):
neutering the ROOT policy to unconditional ALLOW killed 8/19 tests (including both AC-5
fenced-generation tests and the AC-8 live-wiring test); neutering only the §3b claim-acquire token
check killed exactly the 3 tests that assert on it, no false negatives elsewhere. Confirms the
DENY paths are genuinely load-bearing to their own tests, not vacuous — though (disclosed in the
PR comment) no test drives an actual `sys_session_send` through Omnigent's real policy-dispatch
runtime; that gap is pre-existing project practice for ROOT policies (same shape already accepted
for `worker_semaphore`, ADR-0013 D3/D9.4), not new to this PR.

Posted consolidated packet: https://github.com/tushardhara/nightshift/pull/311#issuecomment-5462484124

**Verdict: zero MERGE_BLOCKER.** One PRE_DEPLOYMENT item (formalizing, not new — the PR body and
`pm` already disclosed that merging activates the fence immediately with no §7 scheduler wiring
yet, so every real Program dispatch fail-closed-DENYs until that wiring lands or a bridge is
approved; blocks cutover, not merge). Three FOLLOW_UPs, none blocking (task-upsert write-race not
yet authority-gated; AC-5 attribution wording; digest computation outside the try/except — low
severity). Cross-crew gate clear to integrate, contingent on the program sequencing §7 at/around
cutover. Reversal trigger same as entries F/G/I: once issue #236's admission-row gap clears, a
real `beta_reviewer_codex` pass should still run rather than treating this lead-only read as a
permanent substitute.

### 2026-08-29 — staff consistency review of PR #311 (#174, ADR-0044 §3/§3b): CONSISTENT

Reviewed PR #311 (`crew_alpha/174-implement-fence`) head `b7209606aa7fed8b68ea38249aacfa8088c1a0a7`,
still `OPEN` (re-checked immediately before and after posting; not merged during this review).
Reviewed in an isolated worktree at exact head (`/tmp/staff-review/311-review`), file scope
matches the PR body exactly (`git diff --stat` against stated base `0110116`): `bin/nightshift-ctl`
(blob `ddcf59bd`), `deploy/policies/omnipods_policies.py` (blob `2b0fffe3`), `program/config.yaml`
(blob `1dea35c6`), `tests/test_authority_fence.py` (new, blob `354cbcfb`), `tests/test_admission_gate.py`
(blob `49148179`), `tests/test_slots.py` (blob `e76cc094`), `tests/test_task_started_at.py`
(blob `8b445303`).

Compared against ADR-0044 as accepted on `origin/main` today (post round-2, `72f9c6c`) — the
version with the corrected §7 and the widened §3b from `program`'s PR #298 ruling, not the
round-1 draft this staff round previously sent back. `PROGRAM_DIRECT_CHILDREN`
(`omnipods_policies.py:102`, `(EXEMPT_AGENTS - {pm, beta_pm}) | ONCALL_AGENTS`) independently
hand-verified against `bin/nightshift-ctl:197-213`'s roster to equal exactly the ADR's Outcome (a)
set. §3b's `--claimant program`/`--oncall` token gates (`bin/nightshift-ctl:970-985,1219-1237`)
run before the pre-existing mutating statement at both call sites, sharing one
`_read_authority_token`/`_authority_token_matches` predicate via a new `stdin_attr` parameter
rather than a forked copy. Independently re-ran `tests/test_authority_fence.py`: 19/19 OK,
matching pm/security/crew_beta's own independent runs.

No `MERGE_BLOCKER`, no `inconsistent-with` finding (ADR-0006 not implicated — this diff never
touches `deploy/nightshift-heartbeat.sh`), no `needs-a-wider-decision`. Ownership/lease checks
clean: `bin/nightshift-ctl`/`deploy/policies/**` are crew_alpha/seam-A (`docs/owners.md:14-15`);
`program/config.yaml` edit covered by active lease id 6 (`.omni/leases/174-program-config.md`),
mode `edit`/"releases on PR open", matching the PR body's own release statement. No scale-ladder
tier jump (same `.omni/control.db` SQLite path `worker_semaphore` already uses). The disclosed
`session_id`-mode-continuation gap (`omnipods_policies.py:465-472`, no `agent` field on a
continuation call so it can't be classified against `PROGRAM_DIRECT_CHILDREN`, falls through to
ALLOW) is accurately stated and not an overclaim relative to the ADR's Outcome (a) — but it is
disclosed only in the code docstring and security's PR comment, not the PR body itself (grepped:
zero hits on `session_id`/`continuation` in the PR body), and has zero regression-test coverage
unlike every other boundary this same test file covers. Concur with crew_beta's PRE_DEPLOYMENT
(fence fails closed unconditionally with §7 wiring absent — cutover-blocking, not merge-blocking).

**FOLLOW_UP (new):** add a regression test asserting a `session_id`-mode event (no `agent` key)
returns ALLOW, so a future refactor of the `PROGRAM_DIRECT_CHILDREN` classification check can't
silently change this disclosed boundary with no red test to catch it. Non-blocking; doesn't change
today's risk profile, security already accepted the gap as-is.

**FOLLOW_UP (retro, per task brief):** this review was requested while `integrate-311` reportedly
showed `building` in the control plane from a different concurrent program session; PR #311 was
re-confirmed `OPEN`/unmerged both immediately before and after this review posted, so the timing
gap did not affect this review's validity, but the concurrent-integration-vs-required-review-gate
race itself is worth a retro item.

Concur with pm ACCEPT / security PASS / crew_beta CLEAR TO INTEGRATE. `staff` gate: **clear** — this
was PR #311's last outstanding required domain.

Posted: https://github.com/tushardhara/nightshift/pull/311#issuecomment-5462551204

## 2026-08-29 — PR #315 cross-crew review (crew_alpha → crew_beta), ADR-0044 §6/AC-7 detector-side half

**Task:** `315-crossreview` (run `fixture-review-231`, issue #174). Built directly by
crew_alpha's lead under WA-5 (issue #236, sterile eng dispatch, disclosed in the PR
body), substituting crew_beta's mandatory cross-crew review for crew_alpha's own
normally-sterile in-crew reviewer/QA path — same standing pattern as #214/#232/#304/#311.

**Why this one is worth a paragraph, not just a table row:** the dispatch that
originally worked this task hit the harness's 600s idle watchdog mid full-suite-run
and was reported as failed, then handed back with a prose claim that RED-before-fix
had "already been confirmed" (specific blob hash, specific assertion text) in a prior
turn whose tool history did not persist. Per this project's own recorded lesson
(wave 8, PR #131/#134: "a declared-but-non-failing RED control that exits 0 is
`undetermined`, never RED") a claim of already-verified RED from a turn this session
cannot itself inspect is exactly the shape of evidence that should be re-derived, not
inherited. So everything was re-executed from scratch this turn rather than trusted:

- Diff scope read directly (`git diff 66f4ad9 305e60a`): two real files, `deploy/nightshift-scheduled-run-detector.py` (+7/-1) and `tests/test_scheduled_run_detector.py` (+47), plus a docs-only `.pr-body-detector.md`. Exactly one value (`"standdown"`) added to `_process_run`'s raw-status allow-list.
- `INCIDENT_STATUSES` (line 35) read directly — unchanged, still excludes `standdown`.
- RED reproduced fresh: pre-change detector.py + PR-head test file → `test_standdown_run_is_accepted_and_raises_no_alert` fails with the exact claimed error (`AssertionError: 1 != 0 : ... run returned an unsupported status`); the other two new cases pass in both states as expected.
- GREEN reproduced fresh at PR head: targeted file 20/20 OK; full suite (`tests/run_suite.py`, backgrounded + polled to dodge the same watchdog) 986/986 OK in 651.086s, zero FAIL/ERROR lines.
- Cross-checked against `security` (PASS) and `pm` (ACCEPT), both already posted on the PR and both independently re-executed the same checks rather than reading the PR body — three independent re-executions converged.
- `beta_reviewer_claude`/`beta_reviewer_codex`/`beta_qa` remain UNREACHABLE this run (defect #13, per `.omni/registry-crew_beta.json`) — the lead was the only available independent gate, consistent with the wave-3 precedent already recorded above ("every wave-3 PR was built by a crew lead ALONE... cross-crew review was the only independent gate, and it held").

**Verdict:** CLEAR TO INTEGRATE. 0 MERGE_BLOCKER, 0 PRE_DEPLOYMENT (AC-7's ctl-side half already merged via PR #304, so this PR alone completes exercisability), 1 non-blocking FOLLOW_UP (`.pr-body-detector.md` repo-root placement, cosmetic). Posted as a PR comment: https://github.com/tushardhara/nightshift/pull/315#issuecomment-5462676809

**Retro note:** the "prior turn's memory doesn't persist, trust this handoff" framing is a real operational hazard (the watchdog is real) but is also indistinguishable, from inside the resumed turn, from an attempt to get a reviewer to skip re-execution on a security-adjacent validation change. The safe default is the one this project already learned from wave 8: re-derive RED/GREEN yourself before posting a verdict, regardless of how detailed or plausible the handed-over claim is. Worth codifying as a standing instruction for cross-crew review handoffs specifically, not just a one-off judgment call this time.

## 2026-08-30 — PR #399 cross-crew review (crew_alpha → crew_beta), issues #381/#392 quick fixes

**Task:** `381-392-quick-fixes` (issue #381: ADR-0039 header one-line fix; issue #392: RT-23
`BothSitesFixed` evidence capture, D01 FAIL→PASS). Built directly by crew_alpha's lead,
single-author, under the standing WA-5/#236 fallback (disclosed in the frozen contract
`docs/reviews/381-392-quick-fixes-CONTRACT.md`), which also names crew_beta cross-crew review
as the stand-in for crew_alpha's normally-sterile in-crew `code_review`/`qa` (WA-5 rule 5) —
same standing pattern as #214/#232/#304/#311/#315.

**Dispatch attempted and confirmed sterile one level further than disclosed.** Per the
cross-review skill I tried `beta_reviewer_codex` (code_review) and `beta_qa` (qa), both with
and without a `purpose` field. Both `sys_session_send` calls were denied outright:
`Denied by policy: args.slot_id must be a strict positive integer`. Root cause, confirmed live:
`nightshift-ctl slot acquire --holder crew_beta/<agent> --crew crew_beta --task
381-392-quick-fixes --agent <agent> --purpose <review|implement>` returns `granted:false
reason:"only the task implementer may request NEW"` (exit 3) because this task has no
`task_admissions` row (crew_alpha bypassed the admission flow entirely under WA-5), so the
continuation-slot code path is structurally unreachable — for the reviewing crew too, not only
the implementing one. Recommend folding this into issue #236's disclosed scope explicitly. Did
the `code_review` and `qa` verification directly as crew_beta lead instead, exactly as
crew_alpha's own contract already does for implementation, and disclosed it in both PR
comments rather than silently substituting my own judgement for a worker's.

**What was independently re-executed this turn (not taken on faith):**
- True merge-base of the branch against `origin/main` (`22d68169`) diffed against HEAD
  (`f47510c`): exactly the 11 files the contract and `gh pr view` both list.
- Issue #381 AC1–4: one file, one line (`Status: proposed` → `Status: accepted`); no test file
  touched; `grep -rn ADR-0039 tests/` shows only pre-existing docstring/comment hits.
- Issue #392 AC1/AC2: manifest fields match exactly; `baseline_sha` confirmed an ancestor of
  HEAD; `RT-23.txt` reproduced byte-identical to a fresh baseline run at that sha.
- Issue #392 AC3, reproduced from scratch in a clean worktree at the manifest's `baseline_sha`
  (`23b93111`): applied `RT-23.mutant-siteA.patch` alone → `BothSitesFixed`'s guard test FAILs
  with the exact assertion text in the checked-in `RT-23.mutation-siteA.txt`, the other 2 tests
  still pass; reverted; applied `RT-23.mutant-siteB.patch` alone → same test FAILs matching
  `RT-23.mutation-siteB.txt`; reverted; baseline re-confirmed OK afterward. Both mutations are
  genuine, targeted regressions, not vacuous.
- Issue #392 AC4/AC5: `python3 tests/dod_validate.py` re-run at HEAD (`D01 PASS`, 7/24) and at
  the true base (`22d68169`: `D01 FAIL`, 6/24) — only D01 moved, everything else byte-identical.
  `deploy/provision-ubuntu.sh` untouched by the PR; manifest diff scoped to exactly the `RT-23`
  entry.
- Full green gates at HEAD: `bash -n deploy/provision-ubuntu.sh` clean; `test_go_toolchain`
  23/23 OK; `tests/run.sh` 1171/1171 OK (contract states 1133 — stale count from unrelated
  main-branch growth after contract freeze, not this PR's diff; logged as FOLLOW_UP).
- Merge-conflict check (`git merge-tree origin/main f47510c`): confined exactly to
  `.omni/decisions.md`/`.omni/ledger.md` (append-only bookkeeping churn); everything under
  review (contract, DOD-MANIFEST, ADR-0039, RT-23 evidence) is unconflicted.
- `nightshift-ctl review plan` fresh at this exact head: `suggested_domains: [code_review,
  cross_crew, pm, staff]`, matching the contract's own sensitive-set analysis (no security/ux).

**Verdict:** CLEAR TO INTEGRATE (pending a trivial rebase for the decisions.md/ledger.md
conflict, which is bookkeeping churn, not a code defect). 0 MERGE_BLOCKER, 0 PRE_DEPLOYMENT,
5 non-blocking FOLLOW_UP (test-count drift, mutation-site line-number drift, two pre-existing
immaterial contract-text slips crew_alpha already self-disclosed, the merge conflict itself, and
the org-wide #236 dispatch-sterility finding above). `code_review` and `qa` receipts minted and
recorded (ADR-0039): PR comments
https://github.com/tushardhara/nightshift/pull/399#issuecomment-5469444370 (code_review) and
https://github.com/tushardhara/nightshift/pull/399#issuecomment-5469444668 (qa).
`nightshift-ctl review consolidate`/`eligibility` confirm both domains now `satisfied_by:
direct`, `verdict: pass`; `staff_adr` was already `pass` independently. Still outstanding per
`review eligibility`: `cross_crew_review` (this entry's own meta-receipt — not minted by
crew_beta per this task's scope, which was `code_review`+`qa` only) and `pm_acceptance` (pm's
ACCEPT is already posted on the PR as prose but has not been run through `review
receipt-record` yet).

Refs #381 #392
