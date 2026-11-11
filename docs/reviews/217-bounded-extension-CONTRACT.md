# Review contract

## Identity

- Work item: issue #217 — "Allow one explicit bounded extension for an expired task" (task `217-build`)
- Committed contract path and blob OID: `docs/reviews/217-bounded-extension-CONTRACT.md`, blob OID recorded in the freezing commit (this file's first commit on this build branch) — the freezing commit's own git blob hash is the authority, not this prose copy
- Contract version: 1
- Base SHA: `73874292051baa6c569197fdb78c4ac86d3c75e7` (`origin/main`, checkpoint id 14, wave `217`)
- ADR: `docs/decisions/ADR-0046-bounded-task-extension.md`. Amends ADR-0032 (§3 precedence table) and ADR-0045 (the `budget_expired` expiry formula)
- Review owner (cross-crew lead, not the author): crew_beta
- Frozen at (UTC): 2026-08-29T22:40Z (implementation, tests, and full-suite green evidence already existed at freeze time — see the WA-5 disclosure below for why this could not be frozen strictly before implementation this round)

**Process note.** Unlike #216 (whose contract froze before any implementation
commit), this contract is written and committed together with the
implementation in one pass, because the same WA-5 fallback that removes
in-crew review/QA also removes the natural checkpoint (an engineer's first
commit) that would otherwise separate "contract frozen" from "code written."
The full acceptance-criteria set below is copied verbatim from the DoR
verdict (`.omni/reports/2026-08-29-217-dor-verdict.md`, frozen before any
line of implementation was written), so the contract's content is not
retrofitted to match what shipped — only its commit timing differs from
#216's.

**WA-5 disclosure.** Issue #236 (P0, open) leaves `sys_session_send`'s
`slot_id`/`task_id`/`generation` schema sterile for in-crew `eng_claude`/
`eng_codex`/`qa`/`reviewer_*` dispatch — reproduced live this session
(both documented failure shapes, task `217-build`, `qa` planning slot 18).
Per the standing WA-5 fallback (same pattern as #216/#215/#214/#174), this
task is built directly by the crew_alpha lead, single-author, with
`code_review` and `qa` delegated to crew_beta rather than in-crew
reviewers. Not a standing authorization for future tasks; reconfirmed live
for this task specifically. Full detail: ADR-0046 §Process.

## Acceptance criteria

Verbatim from `.omni/backlog.md` / `.omni/reports/2026-08-29-217-dor-verdict.md`
(branch `crew_alpha/217-dor`, commit `6cc7450`). 12 numbered criteria
(AC0–AC9, with AC4 split into AC4a–AC4d), tracing 1:1 to the issue's own 8.

| ID | Exact criterion | Verification |
|---|---|---|
| AC0 | New `task extend --id --seconds --reason` CLI verb under the existing `task` subparser, all three flags required. | `TaskExtendTest.test_extend_requires_id_seconds_and_reason` |
| AC1 | Three additive, nullable columns (`extension_seconds REAL`, `extension_reason TEXT`, `extended_at REAL`) on `tasks`, deliberately excluded from `TASK_FIELDS`/`TASK_ROW_FIELDS` so `task upsert` cannot write them. | `test_extension_columns_are_additive_and_nullable`, `test_task_upsert_cannot_set_extension_fields` |
| AC2 | `--seconds` must be finite and positive (reuses `_is_valid_budget_seconds` directly) → usage error, exit 2, writes nothing. | `test_seconds_must_be_finite_and_positive` (0, -50, nan, inf, -inf), `test_valid_seconds_control` |
| AC3 | `--reason` required, non-empty after stripping whitespace → exit 2, writes nothing; stored verbatim on success. | `test_reason_must_be_non_empty`, `test_reason_is_stored_verbatim` |
| AC4a | Exactly one extension per task, ever: `extension_seconds IS NOT NULL` refuses a second call, exit 3, `reason_code=already_extended` — holds even if the prior extension has itself since expired (AC7). | `test_second_extension_attempt_is_refused`, `test_expired_again_after_extension_still_refuses_a_second_extension` |
| AC4b | `task extend` is valid only when the task's CURRENT `reason_code` (via `_task_readiness_snapshot`) is exactly `"budget_expired"`; any other value (`not_pending`, `ready`, no budget, terminal) denies, exit 3, `reason_code=not_budget_expired`. | `test_extend_denied_when_not_currently_budget_expired` (pending / in-budget / terminal) |
| AC4c | Unknown `--id`: exit 3, `{"ok": false, "reason": "unknown task"}`, matching `cmd_task_show`'s existing shape. | `test_unknown_task_is_denied_exit_3` |
| AC4d | Ordering: AC2 → AC3 (value-shape, before any transaction) → AC4c → AC4b → AC4a → commit. | `test_bad_seconds_wins_over_unknown_task` |
| AC5 | `task extend` never writes `started_at`. | `test_extend_never_writes_started_at` |
| AC6 | Effective budget = `started_at + budget_seconds + effective_extension`, where a damaged/out-of-band `extension_seconds` reads as no extension (never a crash or bogus expiry) — the highest-risk formula change in this ticket. | `test_effective_budget_extends_the_expiry_window_at_the_boundary`, `test_damaged_extension_seconds_is_treated_as_no_extension` |
| AC7 | Once the EXTENDED deadline also lapses, readiness reports `budget_expired` again, but a second `task extend` call still hits AC4a's denial — "expired again" is never "eligible again." | `test_expired_again_after_extension_still_refuses_a_second_extension` (asserts both facts together) |
| AC8 | A task with `extension_seconds IS NULL` (every pre-#217 task) computes `effective_extension == 0` — byte-identical readiness to pre-#217 code. | `test_extension_seconds_null_leaves_readiness_unaffected` |
| AC9 | `cmd_task_extend`'s success payload surfaces `extension_seconds`/`extension_reason`/`extended_at`/`effective_budget_seconds`; the three columns are not (yet) added to `TASK_ROW_FIELDS`/generic `task show`/`task ready` output — genuinely open build-time latitude, not a gap. | `test_success_payload_surfaces_the_recorded_extension` |

**Known vacuous-pass note (WA-009 discipline).** Three tests pass for the
same (correct) reason both before and after this diff, because the thing
they assert ("this flag combination is refused") was already true when the
`task extend` subcommand did not exist at all: `test_extend_requires_id_seconds_and_reason`,
`test_task_upsert_cannot_set_extension_fields`, and
`test_bad_seconds_wins_over_unknown_task`. This is the same class of
structural-invariant test `tests/test_task_started_at.py::
test_task_upsert_cannot_set_started_at` already establishes as acceptable
precedent in this file. The other 14 of 17 new/modified tests were
confirmed failing against the pre-change binary (`git stash` of
`bin/nightshift-ctl` only, tests kept) before passing against this diff —
see the PR body's Tests section for the exact counts.

## Explicitly out of scope

Issue's own list, carried forward verbatim: deciding automatically whether
to split/block/fail; Program prompt changes; worker cancellation; multiple
extensions; UI/Kanban changes. Structurally guaranteed additions:

- No change to `deploy/policies/omnipods_policies.py`, `_derive_normal_phase`,
  or either of #216's two `budget_expired` checkpoints — both consume
  `reason_code` by reference through the one shared, aliased function.
- No change to `_is_valid_budget_seconds`/`require_valid_budget_seconds`
  (reused, not modified) or to `budget_seconds`'s own write/read path.
- No numeric upper bound on `--seconds` (DoR verdict §5, forked non-blocking
  to `.omni/decisions.md`).
- No authorization/role gate on who may call `task extend` — same trust
  model as every other `nightshift-ctl` verb.
- The three new columns are not added to `TASK_ROW_FIELDS`/`task show`/
  `task ready` (AC9, genuinely open latitude).

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review (delegate: crew_beta, standing in for
      in-crew `reviewer_claude`/`reviewer_codex` per WA-5 — issue #236)
- [x] QA (delegate: crew_beta, same WA-5 substitution)
- [x] Cross-crew review
- [x] Staff / ADR consistency (new ADR this round — ADR-0046 — cites and
      amends ADR-0032/ADR-0045; staff review requested per the standard ADR
      gate, not only per `suggested_domains`)
- [x] Security — `nightshift-ctl review plan --task 217-build --repo . --head-sha 54edaa3c49c2590e856e22ae2cbc001b3ff345b2` (ADR-0042; run against this PR's exact, final head) returned `suggested_domains: [code_review, cross_crew, pm, qa, security, staff]`, `profile: HIGH_RISK` (`bin/nightshift-ctl` sensitive-content terms: auth/secret/session/subprocess/token, pre-existing in the file, not newly introduced by this diff) — security required
- [ ] UX — not in `suggested_domains`; no path under `conditional_globs["ux"]` touched

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | No findings yet at contract-freeze time | open | Populated as review rounds return findings |
