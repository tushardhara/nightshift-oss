# Review contract

## Identity

- Work item: GitHub issue #286 (bookkeeping-only PRs merge through a fast,
  proportionate release path; refs #283)
- Committed contract path and blob OID: `docs/reviews/286-bookkeeping-fast-path-CONTRACT.md` (this file; OID recorded at freeze commit)
- Contract version: v2 (amended 2026-09-02 — see "Amendment (v1 → v2)" below; v1 froze 2026-08-30 and its wording is preserved in git history)
- Base SHA: `23b9311157a596a821afdc9323cf3cbae1382529` (checkpoint 22)
- ADR: `docs/decisions/ADR-0052-bookkeeping-only-fast-release-path.md`; AC-3's v2 scope also governed by `docs/decisions/ADR-0067-wire-bookkeeping-fast-path-into-release.md`
- Review owner (cross-crew lead, not the author): crew_beta lead, routed by Program (WA-5, issue #236 — in-crew reviewer/QA unreachable this run)
- Frozen at (UTC): 2026-08-30, this PR's opening commit

## Amendment (v1 → v2, 2026-09-02)

**What changed:** AC-3's exact criterion text. v1 read "Does not run the full
`tests/run.sh` candidate/post-merge suites for eligible PRs" (plural — both
suites). Once `release`'s own procedure was actually wired to this primitive
(ADR-0067, issue #388's implementation PR), `staff`'s review of ADR-0067
returned `consistent-with` ADR-0052/ADR-0037 but `needs-a-wider-decision` on
this point: the design delivers only the PRE-merge candidate suite skip
(step 3), keeping the POST-merge suite (step 8) and revert-on-red (step 9)
mandatory, unconditionally, as the safety net — narrower than v1's literal
plural wording.

**Why:** Program settled the `needs-a-wider-decision` as the narrower, safer
scope, rather than building literal AC-3 (skip both suites, which staff
called materially riskier with no option analysis behind it) or stalling the
build pending a full re-litigation. Full reasoning, rejected alternatives,
and the reversal trigger are recorded in `.omni/decisions.md`'s 2026-09-02
"ADR-0067 / #286 AC-3 scope" entry. Issue #388's acceptance bullet was
amended the same way via a GitHub comment, not silently.

**What did not change:** AC-1, AC-2, AC-4 through AC-9 are unchanged from v1.
This is a disclosure/scope correction to one criterion's wording to match the
design staff already reviewed — not a re-opening of any other criterion, and
not a weakening of what actually ships (the post-merge safety net stays
mandatory; nothing here removes a check that existed before).

## Acceptance criteria

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | Nightshift mechanically classifies the exact PR head as `bookkeeping-only` or `normal`, failing closed to `normal`. | `nightshift_bookkeeping_release.classify_paths`; `tests/test_bookkeeping_release.py::ClassifyPathsTests` (empty diff, allowlisted add+modify, non-allowlisted path, one-bad-path-denies-whole-diff). |
| AC-2 | An eligible bookkeeping PR runs focused deterministic checks only: PR/body lint, allowed-path validation, ledger append-only validation, status/report format checks, clean mergeability, exact head/base identity. | `nightshift-ctl bookkeeping plan`'s `CHECKS` bundle + `check_clean_mergeability`; PR/body lint is the existing unconditional `pr-lint` invariant, unchanged. `BuildPlanTests::test_ac2_eligible_bookkeeping_pr_is_fast_path_eligible` asserts all five checks ran. |
| AC-3 (v2) | Skips the PRE-merge candidate suite (step 3) for an eligible bookkeeping-only, solo-branch PR. The POST-merge suite (step 8) and revert-on-red (step 9) remain mandatory, unconditionally, as the safety net — narrowed from v1's "candidate/post-merge suites" (plural) per the 2026-09-02 program decision (`.omni/decisions.md`, "ADR-0067 / #286 AC-3 scope"), settling staff's `needs-a-wider-decision` verdict. | Primitive level (unchanged by this amendment): `DoesNotRunFullSuiteTests` (structural: module's only subprocess binary is `git`, no test-runner string anywhere in source) + `CliIntegrationTests::test_ac3_plan_does_not_run_tests_run_sh`. Wiring level (new, ADR-0067): `program/agents/release/config.yaml`'s "The bookkeeping-only fast path" section is diff-reviewable prompt text, not code — `release`'s procedure has no test harness of its own, the same shape v1's AC-8 row already used for this reason. **Reviewer: confirm this narrower, disclosed scope is accepted, matching AC-8's precedent, not silently short of the original criterion.** |
| AC-4 | Release remains the ONLY writer to main; records the exact merged SHA and a verification receipt. | `bookkeeping_receipts` table (migration seq 3) + `bookkeeping receipt-record`, append-only; `CliIntegrationTests::test_ac4_receipt_record_persists_and_is_append_only` (two records for one task/head persist as two distinct rows, second carrying `merged_sha`). "Release is the only writer" itself is an existing, unmodified invariant — this PR adds no new writer to `main`. |
| AC-5 | Adding/changing any non-allowlisted path refuses the fast path. | `ClassifyPathsTests::test_ac5_non_allowlisted_path_classifies_normal`, `::test_ac5_one_bad_path_denies_the_whole_diff`; `CliIntegrationTests::test_ac5_source_change_plans_ineligible_but_not_an_error`. |
| AC-6 | Renames, deletions, symlinks, mode changes, malformed ledger edits, and head changes during verification all refuse the fast path. | `ClassifyPathsTests` (delete/rename-shape/copy/type-change/mode statuses) + `RealGitBoundaryTests` (real `git mv`, real symlink, real `chmod +x`) + `ContentChecksTests::test_ac6_ledger_malformed_edit_fails` + `HeadIdentityTests::test_ac6_head_changed_since_classification_denied` + `CliIntegrationTests::test_ac6_verify_head_match_and_mismatch`. |
| AC-7 | Normal implementation PRs retain every existing QA/review/security/candidate/CI/post-merge gate, unchanged. | No existing command's function body is modified by this PR (diff-reviewable: every change is additive — one new table, one new module, three new CLI verbs, wiring additions). Full existing suite re-run green with this change applied: `python3 tests/run_suite.py` — 1171 tests, 0 failures (includes the 38 new tests). |
| AC-8 | When GitHub is healthy, an eligible bookkeeping PR should normally reach main within 5 minutes of Release receiving it. | Structural/primitive-level only, per the DoR verdict's named scope boundary: `deploy/exact_sha_release.py` (the procedure that would actually skip the slow steps and observe wall-clock time) is `crew_beta`-owned and out of this seam's reach (charter: "release is not your crew's sub-agent and not reachable from you directly"). This PR proves the primitive adds no slow step on its own critical path; end-to-end timing is provable only once `release`'s own procedure is wired to consume `fast_path_eligible`, which is downstream, cross-seam work. **Reviewer: confirm this scoping is accepted, not silently short of the criterion.** |
| AC-9 | Tests cover both the successful path and every fail-closed boundary above. | `tests/test_bookkeeping_release.py`, 38 tests. One negative control run and recorded in the DoR verdict (`check_ledger_append_only`'s prefix check disabled → its own test fails; re-enabled → green), proving the suite can catch the regression it claims to guard (WA-008). |

## Explicitly out of scope

- Weakening any review/release gate for code (this PR touches only the
  bookkeeping-only branch; every other diff shape still classifies
  `"normal"` and gets no new fast path).
- Skipping exact-SHA/head verification (`_target`'s exact-commit resolution,
  `bookkeeping verify-head`'s pre-cutover re-check).
- Auto-merging arbitrary documentation (`.omni/reports/**`'s allowlist is
  narrower than "docs" — see `ELIGIBLE_REPORTS_PREFIX`, not `docs/**`, and
  every entry still needs a clean format check).
- Changing the #214–#223 delivery order.
- Wiring `deploy/exact_sha_release.py` or any other `release`-owned
  procedure to actually call this primitive — see AC-8 row and the DoR
  verdict's named scope boundary. [v2 note, 2026-09-02: this bullet described
  #286's own scope at v1 freeze and is left as-is (v1 correctly excluded that
  wiring from #286 itself). The wiring has since happened, as its own
  separately-chartered work — issue #388, `docs/decisions/ADR-0067-*.md` —
  which is what AC-3's v2 disclosure above documents. `deploy/exact_sha_release.py`
  was also never the right file for that wiring; see ADR-0067's Context and
  ADR-0052's corrected Context section for that finding.]

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review
- [x] QA
- [x] Cross-crew review
- [x] Staff / ADR consistency
- [x] Security
- [ ] UX

Per WA-5 (issue #236): `pm`/QA/in-crew reviewer seats are unreachable this
run. `crew_beta`'s cross-crew reviewing lead signs PM-acceptance criteria in
place of `pm` and performs different-vendor code review, per
working-agreements.md rule 6. Different-vendor code review and QA dispatch
unconditionally regardless of `suggested_domains` (ADR-0042 changes neither).

`staff` and `security` are expected to fire: this diff touches
`bin/nightshift-ctl` and adds `bin/nightshift_bookkeeping_release.py`, both
inside `program/review-risk.json`'s `high_risk_globs.control_plane`
(`staff`), and the diff adds a new SQLite table plus subprocess calls to
`git` with caller-influenced SHA arguments (`security`-relevant: every SHA
is validated against `FULL_OID` / `_exact_commit` before being interpolated
into a `git` argv, never shell-interpreted). Run `nightshift-ctl review plan
--task 286-dor-build --repo . --head-sha <this PR's exact commit SHA>` once
this PR's head is final, and reuse that one result for every gate decision
in Phase 3/4 — never recomputed against a later head.
