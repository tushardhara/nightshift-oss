# Review contract

## Identity

- Work item: GitHub issue #277 (#228 follow-up: close 4 regression-proof gaps)
- Committed contract path and blob OID: `docs/reviews/277-close-228-regression-proof-gaps-CONTRACT.md` (this file; OID recorded at freeze commit)
- Contract version: v1
- Base SHA: `4e56d23ec4c025166c6fd8d7c79a19e5a01c8b07` (checkpoint 21)
- ADR: `docs/decisions/ADR-0051-close-228-regression-proof-gaps.md`
- Review owner (cross-crew lead, not the author): crew_beta lead, routed by Program (WA-5, issue #236 — in-crew reviewer/QA unreachable this run)
- Frozen at (UTC): 2026-08-30, this PR's opening commit

## Acceptance criteria

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | A new, executed test asserts `suggested_plan.human_review` stays `{"mode": "parallel", "wave": 1, ...}` for representative STANDARD and HIGH_RISK fixtures (proof #2). | `tests/test_shadow_risk_plan.py::ShadowRiskPlanTests::test_standard_and_high_risk_domains_share_one_parallel_wave`; negative control (mode->"sequential") fails it, reverted, green again. |
| AC-2 | A new, executed test proves a partial fix touching only one of two differently-scoped, previously-reviewed paths invalidates only the causally-affected domain and reuses the other; a full fix invalidates both (proof #7). | `tests/test_review_eligibility.py::EligibilityCliTests::test_ac7_byte_subset_partial_fix_invalidates_only_the_touched_domain`; negative control (collapse `covered_scope` to whole-diff) fails it, reverted, green again. |
| AC-3 | A new, executed test proves, under realistic (non-synthetic) classification, that every `STAFF_MAPPED_GROUPS` category still requires `staff`, and an ordinary docs-only change never does (proof #10, mechanical half). | `tests/test_277_228_regression_proofs.py::Proof10AdrGateBidirectionalTest` (2 tests). |
| AC-4 | A new, executed test compares `len(suggested_domains)` and a stated model-turn formula across 5 representative FAST/STANDARD(×2)/HIGH_RISK(×2) fixtures, asserting monotonic bounds and that the two STANDARD fixtures differ (proof #15). | `tests/test_shadow_risk_plan.py::ShadowRiskPlanTests::test_dispatch_counts_stay_narrow_and_distinct_across_risk_tiers`; negative control (hardcode all 6 domains) fails 5 assertions, reverted, green again. |
| AC-5 | Proof #2's prose mechanism (same-turn concurrent dispatch) has a structural regression test against both crews' fanout `SKILL.md`. | `tests/test_277_228_regression_proofs.py::Proof2ConcurrentDispatchProseTest`; negative control (local uncommitted edit) fails it, reverted (byte-identical diff), before commit. |
| AC-6 | Proof #14's named invariant classes (one-integrator/default-branch, no-force-push, rollback, scope/firewall, security, task admission, control-plane) are re-run and confirmed intact before AND after this issue's changes. | `.omni/reports/2026-08-30-277-dor-verdict.md` "Proof #14" section: 268+174 tests pre-change, 274+174 post-change, 0 failures either time. Reviewer must independently re-run at least one of the two groups against the PR head, not trust the count. |
| AC-7 | Proof #10's ADR-citation-existence half is explicitly scoped out with a recorded reason, not silently dropped. | `docs/decisions/ADR-0051-close-228-regression-proof-gaps.md` §D3-B; `.omni/tech-debt.md` follow-up row. |
| AC-8 | No production code changes ship with this PR — tests and documentation only. | `git diff --stat` against this PR's head; reviewer confirms no file under `bin/**` outside `tests/` changed in content (only test files, `.omni/**`, `docs/**` should appear). |

## Explicitly out of scope

- Re-litigating #228's shipped design (ADR-0042, review-routing activation).
- Re-opening #231/#232/#233.
- Mechanically enforcing that a qualifying diff's `docs/decisions/**` contains
  a corresponding ADR (proof #10(b) — see ADR-0051 §D3-B and `.omni/tech-debt.md`).
- Any live, multi-session integration test of actual concurrent agent
  dispatch (sub-agent dispatch is sterile this run, issue #236).

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

`nightshift-ctl review plan --task 277-review-proof-gaps --repo . --head-sha
<this PR's exact commit SHA, see PR/task record>` (run once,
reused for every gate decision below, never recomputed) returned
`suggested_domains = [code_review, cross_crew, pm, qa, security, staff]`,
`target.head_sha` matching this head exactly — authoritative, no fail-closed
fallback needed. `staff` fires because the diff touches `docs/decisions/**`
and `docs/reviews/**` (constitution). `security` fires because
`tests/test_review_eligibility.py`'s new fixture content matches a
`sensitive_terms` scan hit (it legitimately references `auth/login.py` and
security-domain fixture strings as PART OF THE REGRESSION TEST ITSELF, not as
production security-sensitive code) — a real mechanical trigger, reported
rather than second-guessed, per ADR-0042's fail-upward design (proof #5). `ux`
does not fire (no `conditional_globs["ux"]` path touched).

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| _(none yet — filled in by the review owner as findings arrive)_ |  |  |  |  |  |  |  |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| _(filled in by each domain as it posts its receipt)_ |  |  |  |  |  |

## Contract amendments

None yet — v1 is the frozen version as of this PR's opening commit.
