# Review contract — #398 24-heartbeat no-op sim (AC-2), concurrent-authority standdown sim (AC-3), historical replay fixtures (AC-14)

## Identity

- Work item: GitHub issue #398 ("#390 follow-up: 24-heartbeat no-op sim (AC-2), concurrent-authority standdown sim (AC-3), historical replay fixtures (AC-14)"), task `398-build`
- Committed contract path and blob OID: `docs/reviews/398-build-CONTRACT.md` (this file); blob OID recorded in the freezing commit — the freezing commit's own git blob hash is the authority, not this prose copy
- Contract version: 1
- Base SHA: `35a5dfd04031537c44798ba4af21d8f0c6518078` (`origin/main`, nightshift-ctl checkpoint 32)
- ADR: none. This is additional regression coverage against an already-decided, already-shipped contract (#390 / ADR-0053, status: proposed, code merged) — no new design decision, per the accepted DoR §3's explicit statement that a genuine defect found in `decide()` would be a bug-fix task against #390, not folded in here.
- Review owner (cross-crew lead, not the author): `crew_beta` (round-robin — crew_beta most recently reviewed crew_alpha's PR #461 / `454-build`, so crew_beta is next; only two crews exist so this is also simply "the other crew")
- Frozen at (UTC): 2026-08-31T20:18:24Z, first commit on branch `crew_alpha/398-build`, before any test/fixture code is added

**WA-5 disclosure.** Issue #236 (still open) leaves `sys_session_send`'s `slot_id`/`task_id`/`generation` schema sterile for classified dispatch to `eng_claude`/`eng_codex`/`qa`/`reviewer_*`. Per the standing WA-5 fallback (same pattern as #174/#390/#449/#454), this task is built single-author by the crew_alpha lead. `pm` is unaffected by #236 and was dispatched separately, in the same turn, for acceptance sign-off. Cross-crew review from `crew_beta` stands in for in-crew `code_review`/`qa`.

## Acceptance criteria

Verbatim from the accepted DoR §2 (issue #398 comment, linked above).

| ID | Exact criterion | Verification |
|---|---|---|
| AC-2.1 | Fixture test constructs a `GitRepo` fixture repo at a fixed base commit, then loops 24× calling `hp.decide()` with an unchanged no-op outcome (`authority_status="granted"`, `observation_codes=["queue_empty","dod_unchanged"]`, `durable_delta_reason_code=None`). All 24 results assert `should_persist_to_git=False` and `route="none"`. | New test in `tests/test_heartbeat_persistence.py` (extended) and/or `tests/test_heartbeat_persistence_replay.py` — name it so a failure identifies AC-2.1 by name or docstring |
| AC-2.2 | Before/after the 24-iteration loop, the fixture repo's `git rev-parse HEAD`, `git branch --list`, and `git count-objects` are asserted identical — 0 new commits, branches, or Git objects across all 24 iterations (the repo itself is inspected, not just 24 booleans) | Same file, named for AC-2.2 |
| AC-2.3 | At least one of the 24 iterations uses an out-of-enum `observation_codes` entry mixed with recognized ones, asserting `reason_code="unrecognized_observation_fail_closed"` and the same 0-Git-object outcome | Same file, named for AC-2.3 (failure/empty-state case) |
| AC-3.1 | A fixture test constructs a synthetic 4-hour timeline (4 ticks, e.g. T+0/T+1h/T+2h/T+3h) where one synthetic authority holder holds authority the whole window; each of the 4 `decide()` calls carries `authority_status="standdown"` together with an otherwise-recognized `durable_delta_reason_code` (e.g. `"governed_pr_ready"`) — mirrors existing `test_ac10_standdown_short_circuits_even_with_a_valid_reason`, run 4× | Same file, named for AC-3.1 |
| AC-3.2 | All 4 ticks assert `should_persist_to_git=False`, `reason_code="standdown_no_authority"`, `route="none"` | Same file, named for AC-3.2 |
| AC-3.3 | At each of the 4 ticks, a direct call to the code-enforced hook (`nightshift-ctl pr-create --heartbeat-persist-receipt <id>` with no valid receipt, reusing the `test_ac10_gate_denies_pr_create_with_no_matching_decision` pattern) is denied — exit 3, `heartbeat_persist_denied`/`no_matching_should_persist_decision` — proving no tick's decision leaks a usable receipt into another tick's call; a `GitRepo` fixture shows 0 Git objects created across the whole window | Same file, named for AC-3.3 |
| AC-3.4 | Control (WA-009): a 5th tick in the same simulated window, for the WINNING authority holder (`authority_status="granted"` + a recognized durable-delta reason), DOES grant — proving AC-3's zero-Git-writes result is because of standdown specifically | Same file, named for AC-3.4 |
| AC-14.1 | For each of #389, #382, #373, #368, #357, #351, #330, #326, #317, #284, #255 (all reconfirmed MERGED, `program/heartbeat-*-status` pattern), one fixture `outcome` record built from that PR's actual described state is asserted via `hp.decide()` to return `should_persist_to_git=False`. One subTest/case per PR number (mirrors `test_ac4_every_named_no_op_case_denies`'s style) so a failure names the exact PR number | Same file, named for AC-14.1, 11 subTest cases |
| AC-14.2 | Where a given PR's body doesn't map unambiguously to one `OBSERVATION_CODES` member, the fixture uses the most conservative available mapping and the test still asserts denial (fail-closed default per AC-10) | Same file, named for AC-14.2 |

**Out of scope (explicit, from the DoR §3):** no change to `bin/nightshift_heartbeat_persistence.py`'s `decide()` logic, enum membership, or output shape; no new CLI verb, no new table/migration, no new authority read; no wiring into the Program's scheduled-task prompt; no standing up two real concurrent Program sessions (AC-3's "concurrent" is interleaved fixture sequences against `decide()`, not live infrastructure).

**File scope (from the DoR §4):** MAY touch `tests/test_heartbeat_persistence.py` (extend) and/or new `tests/test_heartbeat_persistence_replay.py`, plus `tests/fixtures/**` if the 11 AC-14 records are cleaner as data files. MUST NOT touch `bin/nightshift_heartbeat_persistence.py`, `bin/nightshift-ctl`, `docs/decisions/**`, `.omni/**` live content.

## Required review domains

**Machine-readable checklist (ADR-0039, issue #231).**

- [x] PM acceptance — crew_alpha's own `pm`, dispatched this same turn, signs each of the 9 AC-IDs above directly against the diff and tests (no separate pm-acceptance doc needed for a test-only item — same precedent as #222/#398-DoR itself)
- [x] Different-vendor code review — WA-5 fallback: satisfied by `crew_beta` cross-crew review standing in, per the disclosure above
- [x] QA — WA-5 fallback: satisfied by the same `crew_beta` cross-crew review reproducing the full suite (`tests/run_suite.py`) and the focused suite, and stating exact commands/counts
- [x] Cross-crew review — `crew_beta` (round-robin)
- [x] Staff / ADR consistency — required: this contract file itself lives under `docs/reviews/**`, one of `program/review-risk.json`'s `high_risk_globs.constitution` paths, which mechanically triggers the staff gate regardless of the tests/-only diff content — Program will route `staff` once the PR is open
- [x] Security — PASS, no MERGE_BLOCKER/FOLLOW_UP. `nightshift-ctl review plan --task 398-build --repo . --head-sha 0d3a87ea70a4dcf935ae396d5bc04b6d21d022db` returned `security` in `suggested_domains` (matched `auth`, `subprocess` terms, pre-existing fixture usage, no new surface). Reviewer: security (Claude), same head SHA. See Scoped attestations.
- [ ] UX — not required: no human-facing flow or operator-interface change, tests-only

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | cross-crew | FOLLOW_UP | AC-3.3 (no tick leaks a usable receipt) | `test_ac3_3_no_tick_leaks_a_usable_receipt_and_zero_git_objects`, `tests/test_heartbeat_persistence_replay.py:136-158` | As implemented, AC-3.3 proves the stronger "no receipt is ever usable" property (each tick's own denied receipt id is itself rejected by `pr-create`, plus a nonexistent-receipt control) rather than the literal cross-tick-reuse case described in the criterion's prose (tick A's receipt id replayed against tick B's call). Not a regression and not a gap in the property actually proved, just narrower than one reading of the wording could imply. | filed-separately | queued as #465, non-blocking, does not gate this PR (ADR-0057/#222's rule) |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| PM acceptance | crew_alpha `pm` (task `398-build`) | ACCEPTED — all 9 AC-IDs (AC-2.1/2.2/2.3, AC-3.1/3.2/3.3/3.4, AC-14.1/14.2) met as-specified, independently re-verified against diff/contract/test code (own `git diff --stat`, own `git diff -- bin/nightshift_heartbeat_persistence.py` = 0 lines, own focused-suite rerun: `Ran 9 tests in 2.217s — OK`) | `0d3a87ea70a4dcf935ae396d5bc04b6d21d022db` | `tests/test_heartbeat_persistence_replay.py`, `docs/reviews/398-build-CONTRACT.md` | crew_alpha session transcript, task `398-build` |
| Security | `security` (Claude) | PASS — no MERGE_BLOCKER/FOLLOW_UP | `0d3a87ea70a4dcf935ae396d5bc04b6d21d022db` | matched `auth`, `subprocess` terms via `nightshift-ctl review plan`; pre-existing fixture usage (`Ctl`, `GitRepo`), no new auth/session/secrets surface | PR #464 review thread |
| Staff / ADR consistency | `staff` | CONSISTENT — no ADR needed, contract matches ADR-0026/ADR-0039/ADR-0053 verbatim, no tech debt to register, round-robin claim verified against PR #461 | `0d3a87ea70a4dcf935ae396d5bc04b6d21d022db` | `docs/reviews/398-build-CONTRACT.md` | PR #464 review thread |
| Cross-crew review (standing in for code_review + QA per WA-5) | `crew_beta` (round-robin) | CLEAN — all 9 AC-IDs independently re-verified, focused suite reproduced (`Ran 9 tests in 2.271s — OK`), file-scope boundary reconfirmed (0 lines in `bin/nightshift_heartbeat_persistence.py`/`bin/nightshift-ctl`), full-suite pre-existing-failure claim independently checked (crew_beta's sandbox permits user namespaces: `Ran 87 tests in 53.050s — OK, 0 failures/errors` on the 3 flagged files directly, consistent with — not contradicting — the bwrap-restriction explanation). One non-blocking FOLLOW_UP (F-1, filed as #465) | `0d3a87ea70a4dcf935ae396d5bc04b6d21d022db` | `tests/test_heartbeat_persistence_replay.py` | PR #464 review thread |

## Contract amendments

**v2 — 2026-08-31, after security/staff/cross-crew review and PM acceptance.** The v1 rows above are NOT rewritten; this records what changed around them.

- **Previous version and reason:** v1, frozen at base_sha `35a5dfd04031537c44798ba4af21d8f0c6518078` as the first commit on branch `crew_alpha/398-build`, before any test/fixture code was added. Amended because all four review domains required by this contract (PM acceptance, security, staff/ADR consistency, cross-crew review standing in for code_review+QA per the WA-5 disclosure) have now returned verdicts against PR #464's head `0d3a87ea70a4dcf935ae396d5bc04b6d21d022db`.
- **Criteria, domains, ADR, or scope changed:** none. All 9 AC-IDs, the required-domain set, the out-of-scope list, and the file scope are unchanged from v1.
- **Attestations invalidated by that change:** none — this is the first time attestations are recorded in this contract, not a re-attestation; all four verdicts were issued against this same head SHA.
- **Findings added:** F-1 (cross-crew, FOLLOW_UP) — AC-3.3 as implemented proves a stronger property (no receipt is ever usable) than one reading of its literal wording (cross-tick receipt reuse) might imply. Filed separately as issue #465, `Refs #398`; per ADR-0057/#222's rule, a non-blocking FOLLOW_UP does not gate this PR.
- **Implementation work reconsidered:** none — no MERGE_BLOCKER or PRE_DEPLOYMENT finding was raised by any domain.
