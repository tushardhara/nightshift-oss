# Review contract

## Identity

- Work item: GitHub issue #393 (M2 seam-B close-out: D11 install rehearsal + D12
  preflight check manifest; also refs #5)
- Committed contract path and blob OID: `docs/reviews/393-build-CONTRACT.md`
  (this file; OID recorded at freeze commit)
- Contract version: v1
- Base SHA: `23b9311157a596a821afdc9323cf3cbae1382529` (checkpoint 22)
- ADR: `docs/decisions/ADR-0054-m2-seamb-install-rehearsal-and-preflight-manifest.md`
- Review owner (cross-crew lead, not the author): crew_alpha lead, routed by
  Program (WA-5, issue #236 — in-crew reviewer/QA unreachable this run)
- Frozen at (UTC): 2026-08-30, added retroactively at the program's request
  after the fact that the build proceeded without one was caught (same gap
  found on PR #399/#401 this heartbeat) — acceptance criteria below are
  copied verbatim from the already-frozen DoR doc
  (`docs/prd/393-m2-seamB-preflight-rehearsal.md`, PR #396, frozen before the
  first implementation commit), not re-derived after the fact.

## Acceptance criteria

Numbering matches `docs/prd/393-m2-seamB-preflight-rehearsal.md` exactly
(`D11-n` / `D12-n` / `BOTH-10`), so a criterion here and in the frozen DoR doc
are the same claim under the same id.

| ID | Exact criterion | Verification |
|---|---|---|
| D11-1 | `tests/linux/install-rehearsal.sh` exists; header states, per step, hermetic/network/stub, with a named reason for any stub. | `tests/test_install_rehearsal.py::ScriptExistsAndDeclaresPerStep::test_header_declares_hermetic_networked_stub`, `::test_script_exists`. |
| D11-2 | An extraction via `git archive` (never a bind mount) of a commit with one extra untracked file does not contain that file. | `tests/test_install_rehearsal.py::GitArchiveExcludesUntrackedFiles` (negative case: untracked file absent; positive control: a committed file IS present, so the negative assertion isn't vacuous). |
| D11-3 | The rehearsal asserts `TEAMS_DIR` (`/srv/nightshift`) is absent before install steps run, and fails loudly, naming the path, if not. | `tests/test_install_rehearsal.py::RehearsalRunsRealDecisionLogic::test_teams_dir_present_is_a_hard_refusal`. |
| D11-4 | Running the rehearsal prints one `PASS:` line each for DoD conditions 1, 2, 3, 5, each obtained by executing the real `deploy/provision-ubuntu.sh`/`deploy/bootstrap.sh` code, not a reimplementation. | `tests/test_install_rehearsal.py::RehearsalRunsRealDecisionLogic::test_clean_run_reports_all_five_decision_points`, `::test_condition_5_uses_the_real_bwrap_probe_not_a_reimplementation`. |
| D11-5 | Condition 3 (`systemctl --user`) is proven without assuming an inherited session; a container base image that cannot host a real session bus names that limitation in the header, and the decision-logic assertion still runs for real. | Same test class; header STUB declaration (D11-1) names the systemd-user login-session branch explicitly as the original bug's exact failure mode, not papered over. |
| D11-6 | `docs/prd/DOD-MANIFEST.json` gains `rehearsal_ci_job` naming one entry already in `required_ci_jobs`. | `tests/test_install_rehearsal.py::ScriptExistsAndDeclaresPerStep::test_manifest_names_a_required_rehearsal_ci_job`. |
| D11-7 | `python3 tests/dod_validate.py` prints `D11 PASS`. | Run directly: `D11 PASS` (see Proof below). |
| D12-1 | `docs/prd/DOD-MANIFEST.json` gains `preflight_checks`; each entry has `id`/`severity`(`FAIL`\|`warn`)/`remediation` (non-empty, no "properly"/"correctly"). | `tests/test_preflight.py::CompletenessGuard::test_every_entry_has_id_severity_and_remediation` + manifest wording scan (verified: zero `properly`/`correctly` hits, 69 rows, severities strictly `{FAIL, warn}`). |
| D12-2 | A bwrap-functional entry exists, `severity: FAIL`, remediation names `bwrap --ro-bind / / /bin/true; echo $?`, referencing the existing probe. | `docs/prd/DOD-MANIFEST.json`'s `nightshift_bwrap_diagnosis` row (verified verbatim, see PM sign-off round 1). |
| D12-3 | A structural test asserts the count/id-set of `bad "`/`warn "` call sites in `deploy/preflight.sh` equals the manifest's declared entries (completeness guard). | `tests/test_preflight.py::CompletenessGuard::test_scan_is_not_vacuous`, `::test_every_scanned_call_site_is_single_line_and_uncommented`, `::test_manifest_ids_match_the_scanned_call_sites_exactly`, `::test_manifest_severity_matches_the_scanned_call_kind`. |
| D12-4 | Every hermetically-forceable `FAIL` entry is proven both ways (forced broken → `FAIL` line + remediation; forced satisfied → no `FAIL` line) in `tests/test_preflight.py`. | `tests/test_preflight.py`'s curated fixture classes (`BinariesLoopMissingBinary`, `NightshiftCtlOnPathAndSelftest`, `ControlDbAndAdmissionSchema`, etc.) **plus `BwrapFunctionalProbe`** (added in commit `157e3a9` after `beta_pm`'s round-1 CONDITIONAL PASS found `nightshift_bwrap_diagnosis`'s coverage claim was self-asserted with no fixture in this file — closed, `beta_pm` round-2 PASS, https://github.com/tushardhara/nightshift/pull/404#issuecomment-5469599668). |
| D12-5 | Any `FAIL` entry that cannot be forced hermetically carries a `fixture_exempt_reason` and is still counted by D12-3's scan, never silently dropped. | `tests/test_preflight.py::CompletenessGuard::test_every_fail_entry_either_has_a_fixture_or_a_named_exempt_reason` (50 exempt entries, each with a stated reason — verified in PM's round-1 sign-off). |
| D12-6 | `deploy/preflight.sh` exits 0 with zero `FAIL` branches, non-zero with at least one. | `tests/test_preflight.py::BashSyntaxAndExitCode::test_exits_zero_on_a_clean_environment_and_nonzero_on_one_forced_fail`. |
| D12-7 | `bash -n deploy/preflight.sh` exits 0. | `tests/test_preflight.py::BashSyntaxAndExitCode::test_bash_n`. |
| D12-8 | Diff touches only `docs/prd/DOD-MANIFEST.json` (rows only), `deploy/preflight.sh`, `deploy/lib/deploy-lib.sh` (only if closing a real gap), `tests/test_preflight.py`; never `docs/prd/DEFINITION-OF-DONE.md`. | `git diff origin/main...beta/393-preflight-rehearsal --name-only`: `docs/decisions/ADR-0054-*.md`, `docs/prd/DOD-MANIFEST.json`, `tests/linux/install-rehearsal.sh`, `tests/test_install_rehearsal.py`, `tests/test_preflight.py` — no `deploy/preflight.sh` or `deploy/lib/deploy-lib.sh` touch (manifesting-only, as the DoR doc's "Current state" section anticipated); no `docs/prd/DEFINITION-OF-DONE.md` touch. |
| D12-9 | `python3 tests/dod_validate.py` prints `D12 PASS`. | Run directly: `D12 PASS` (see Proof below). |
| BOTH-10 | `D11`/`D12` `PASS`; every other 22 lines byte-identical to the pre-change baseline. | Run at head `a26ce70`: `D11 PASS`, `D12 PASS`; all other 22 lines unchanged from the `origin/main`/`23b9311` baseline in the DoR doc's Problem section (re-confirmed independently by `beta_pm` both sign-off rounds). |

## Explicitly out of scope

- Editing `tests/dod_validate.py` itself (owned by `crew_alpha` for conditions
  8/9 per `docs/prd/MILESTONES.md:127`).
- Editing `.github/workflows/**` (platform-owned, single-writer, never
  changed mid-wave) — CI wiring reuses the existing `tests` job via
  `rehearsal_ci_job`, no new workflow file.
- Editing `docs/prd/DEFINITION-OF-DONE.md` or its changelog — `product` is
  its sole writer.
- Condition 10 (live CI status reconciliation for the tip commit) — seam A,
  a dependency, not something this item completes. (Note: a transient `D10`
  FAIL was observed and isolated during review to a pre-existing,
  unrelated CI PATH-resolution bug, independently fixed on this same branch
  in commit `a26ce70` — not part of this contract's own acceptance criteria.)
- Re-deriving or restructuring the existing bwrap probe
  (`nightshift_bwrap_probe`/`nightshift_bwrap_diagnosis`, closed issue #12) —
  D12-2/D12-4 reference it, never rebuild it.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review
- [x] QA
- [x] Cross-crew review
- [x] Staff / ADR consistency
- [x] Security
- [ ] UX

Per WA-5 (issue #236, confirmed still open and platform-diagnosed as an
external tool-schema constraint, PR #408 merged): in-crew `pm`/reviewer/QA
seats are unreachable this run for classified dispatch. `beta_pm` itself is
EXEMPT and was dispatched normally (PASS 17/17, both sign-off rounds).
`crew_alpha`'s cross-crew reviewing lead signs PM-acceptance criteria in
place of `pm`'s own in-crew review layer and performs different-vendor code
review, per working-agreements.md rule 6. Different-vendor code review and
QA dispatch unconditionally regardless of `suggested_domains` (ADR-0042
changes neither) — `cross_crew` review stands in for both while #236 stands.

`staff` and `security` are both independently confirmed by `nightshift-ctl
review plan` at every head on this branch (`63c0a57`, `157e3a9`, `a26ce70`
— unchanged across all three): `suggested_domains` = `[code_review,
cross_crew, pm, qa, security, staff]`.

- **Staff**: this diff adds `docs/decisions/ADR-0054-*.md`, inside
  `program/review-risk.json`'s `constitution` `high_risk_globs` group
  (protected path) — an ADR needs staff's cross-crew-consistency check by
  definition.
- **Security**: `tests/linux/install-rehearsal.sh` and `tests/test_preflight.py`
  both execute real subprocess calls (`subprocess`, `exec(`) against
  `deploy/preflight.sh`/`deploy/lib/deploy-lib.sh` and the bwrap sandbox
  probe (`bwrap --ro-bind / / /bin/true`) — installer/preflight surface is
  sensitive-set precedent per PR #15. Nothing in this diff adds a NEW
  privileged operation (the bwrap probe itself, RT-13/14/15, is unchanged,
  closed issue #12) — the new code only *drives* it via the same
  `NIGHTSHIFT_BWRAP` override / scratch-`PROJECT_DIR` convention every
  existing test in this file already uses, never with live/production
  paths, network, or credentials. Security's own call, not pre-judged here.
- **UX**: not checked. No path in this diff matches
  `program/review-risk.json`'s `conditional_globs["ux"]`
  (`ui/**`, `web/**`, `frontend/**`, `templates/**`,
  `docs/HUMAN-COMMUNICATION.md`, `.github/ISSUE_TEMPLATE/**`,
  `.github/pull_request_template.md`) — this is a test/manifest/ADR-only
  change, no human-facing flow or operator interface changes.

Run `nightshift-ctl review plan --task 393-m2-seamB-preflight-rehearsal
--repo . --head-sha <this PR's exact commit SHA>` once the head is final for
each gate, and reuse that one result for every Phase 3/4 gate decision —
never recomputed against a later head.

## Finding receipt

Every finding uses one severity from ADR-0026.

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | pm_acceptance | `MERGE_BLOCKER` (resolved) | D12-4 | `tests/test_preflight.py`'s `CompletenessGuard.TESTED_IDS` claimed `nightshift_bwrap_diagnosis` was fixture-covered in this file with nothing there to back it. | `beta_pm` round-1 sign-off found the self-asserted coverage claim unproven in the shipped diff at head `63c0a57` — the exact failure mode the file's own docstring warns against. | resolved | Fixed by `BwrapFunctionalProbe` (commit `157e3a9`); `beta_pm` round-2 re-verification PASS at head `157e3a9`/`a26ce70` (https://github.com/tushardhara/nightshift/pull/404#issuecomment-5469599668). |

The review owner publishes one deduplicated fix packet per round. A severity
change must retain the original finding and record the rationale.

Never execute a reproduction against a live system. A production-only
security or data-loss risk may be proven by a concrete causal failure
scenario and safe evidence.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| pm_acceptance | `beta_pm` | PASS 17/17 (round 2, supersedes round-1 CONDITIONAL PASS) | `a26ce709c8348de46e85045a86cd16f6172bc150` | `tests/test_preflight.py`, `tests/test_install_rehearsal.py`, `tests/linux/install-rehearsal.sh`, `docs/prd/DOD-MANIFEST.json` | https://github.com/tushardhara/nightshift/pull/404#issuecomment-5469599668 |

Content evidence may transfer only across identical reviewed blobs with
unchanged dependency seams. Re-run CI, provenance, PR-body, authorization,
and live/runtime checks.

## Contract amendments

Never rewrite the frozen rows above. Create the next version and record:

- previous version and reason;
- criteria, domains, ADR, or scope changed;
- attestations invalidated by that change;
- implementation work that must be reconsidered.
