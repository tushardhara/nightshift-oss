# Review contract

## Identity

- Work items: issue #381 ("ADR-0039 header still says Status: proposed despite being
  shipped/accepted") and issue #392 ("D01 regression gap: RT-23 BothSitesFixed evidence
  capture still missing despite #30/#72 closed") — task `381-392-quick-fixes`
- Committed contract path and blob OID: `docs/reviews/381-392-quick-fixes-CONTRACT.md`,
  blob OID recorded in the freezing commit (this file's first commit on this build
  branch) — the freezing commit's own git blob hash is the authority, not this prose
  copy
- Contract version: 1
- Base SHA: `23b9311157a596a821afdc9323cf3cbae1382529` (`origin/main`, checkpoint id 22,
  wave `286`)
- ADR: no new numbered ADR — see `.omni/decisions.md`, 2026-08-30 ~13:20Z entry
  ("#381/#392 quick fixes built under WA-5"), the design-debate-equivalent record for
  this task (both items are S-sized: #381 is a one-line metadata fix with no design
  choice, #392 is evidence-wiring onto an already-existing, already-green test class)
- Review owner (cross-crew lead, not the author): `crew_beta` (routed by the Program)
- Frozen at (UTC): 2026-08-30T13:25Z (implementation, tests, and full-suite green
  evidence already existed at freeze time — see the WA-5 disclosure below)

**Process note.** Written and committed together with the implementation in one pass,
same reason as the `#216`/`#217` precedent: the WA-5 fallback that removes in-crew
review/QA also removes the natural checkpoint (an engineer's first commit) that would
otherwise separate "contract frozen" from "code written."

**WA-5 disclosure.** Issue #236 (P0, open) leaves `sys_session_send`'s
`slot_id`/`task_id`/`generation` schema sterile for classified dispatch to
`eng_claude`/`eng_codex`/`qa`/`reviewer_*` — confirmed live this turn by reading the
tool's own schema (`additionalProperties: false`, no `slot_id`/`task_id`/`generation`
key) and by reading `deploy/policies/omnipods_policies.py` directly (`crew_alpha` is
`EXEMPT_AGENTS`, not `COUNTED_AGENTS`; `slot acquire --holder crew_alpha` denied
`"unknown normal worker"`, the expected response). Per the standing WA-5 fallback (same
pattern as #174/#214/#215/#216/#217/#277/#319), this task is built directly by the
crew_alpha lead, single-author, under the task's `building` row in the control plane.
`pm` is unaffected by #236 (not a `COUNTED_AGENT`) and ran its normal DoR pass and
criteria below. Cross-crew review from `crew_beta` stands in for in-crew
`code_review`/`qa`. Not a standing authorization for future tasks; reconfirmed live for
this task specifically. Full detail: `.omni/decisions.md`, 2026-08-30 ~13:20Z entry.

## Acceptance criteria

Verbatim from `pm`'s sign-off pass on this thread (session `381-392-quick-fixes`,
2026-08-30).

### Issue #381

| ID | Criterion | Verification |
|---|---|---|
| 1 | Line 3 of `docs/decisions/ADR-0039-persist-review-receipts.md` reads exactly `Date: 2026-08-28 · Status: accepted` — only the `Status` token changes | `git diff` on that file, one line changed |
| 2 | No other line in the ADR body changes; no `## Acceptance` note added (issue calls it optional; kept out to stay a pure one-line fix) | `git diff --stat` for this file: 1 line changed |
| 3 | The PR's diff for this item touches exactly one file | `git diff --stat` |
| 4 | No test file changes — `ADR-0039` mentions in `tests/` are docstring/comment only | `grep -rn "ADR-0039" tests/`, unchanged by this PR |

### Issue #392

| ID | Criterion | Verification |
|---|---|---|
| 1 | `docs/prd/DOD-MANIFEST.json`'s `RT-23` entry sets `test_node_id: "tests.test_go_toolchain.BothSitesFixed"`, `condition: 1`, `binding.file: "deploy/provision-ubuntu.sh"` | manifest diff |
| 2 | `baseline` block has `baseline_sha` (ancestor of HEAD), `command: "python3 -m unittest -v tests.test_go_toolchain.BothSitesFixed"`, `exit_code: 0`, `kind: "preservation"`, `output_path: "docs/audit/baseline/RT-23.txt"`; that file exists, is non-empty, and is the literal captured `OK` run output | `docs/audit/baseline/RT-23.txt`; `git merge-base --is-ancestor <sha> HEAD` |
| 3 | Two mutation entries under `baseline.mutations`: `RT-23.M1` (site A, `provision-ubuntu.sh:221` version discovery) and `RT-23.M2` (site B, the `:241-243` checksum pipeline), each with its own `patch_path` (a real unified diff under `docs/audit/baseline/`, touching only `provision-ubuntu.sh`), `applied_to_sha` (ancestor of HEAD), `command` byte-identical to the baseline command, non-zero `exit_code`, and a real, non-empty captured FAIL transcript at `output_path` | `docs/audit/baseline/RT-23.mutant-site{A,B}.patch`, `RT-23.mutation-site{A,B}.txt`; `python3 tests/dod_validate.py` internally re-validates this shape (`validate_rt_baseline`/`_validate_mutation`) |
| 4 | `python3 tests/dod_validate.py` reports `D01 PASS` | run it |
| 5 | No changes to `deploy/provision-ubuntu.sh` itself, and no changes to any other RT id or condition in the manifest | `git diff --stat`; manifest diff scoped to `rt_ids.RT-23` only |

**Failure/empty state:** if either mutation's captured output shows the test still
passing (guard doesn't fire), that mutation is invalid evidence and AC3 is not met.
**Out of scope:** fixing the pipefail bug itself (already fixed; this is
evidence-wiring only, Seam A); RT-01/RT-02/other RT ids; `DEFINITION-OF-DONE.md`.
**Dependencies:** none blocking — `BothSitesFixed` already existed in the tree; #30/#72
are closed history only.

## Required review domains

**Machine-readable checklist (ADR-0039, issue #231)** — parsed by
`bin/nightshift_review_receipts.parse_required_domains()`. Reasoned from what this
contract already established above, not asserted independently.

- [x] PM acceptance — satisfied: `pm` posted ACCEPT 4/4 (#381) and ACCEPT 5/5 (#392),
  independently re-verified (applied both `RT-23` mutations itself in a throwaway
  worktree, re-ran `BothSitesFixed`, diffed the full `dod_validate.py` report), on the
  PR: https://github.com/tushardhara/nightshift/pull/399#issuecomment-5469083998
- [x] Different-vendor code review — required (dispatches unconditionally per
  ADR-0042); in-crew `reviewer_claude`/`reviewer_codex` dispatch is sterile (issue
  #236, `COUNTED_AGENTS`), so per WA-5 rule 5 this domain is satisfied by cross-crew
  review (`crew_beta`) standing in for it, not a separate in-crew reviewer — requested
  from the Program, not yet landed
- [x] QA — required (dispatches unconditionally per ADR-0042, independent of whether
  `qa` appears in a given `review plan` call's `suggested_domains`); in-crew `qa`
  dispatch is sterile for the same reason (issue #236), so per WA-5 rule 5 this domain
  is also satisfied by the same cross-crew review — requested from the Program, not
  yet landed
- [x] Cross-crew review — mandatory under WA-5 rule 5 (issue #236, confirmed live this
  turn — see the WA-5 disclosure above), standing in for both rows above — requested
  from the Program, not yet landed
- [x] Staff / ADR consistency — required: `review plan`'s `suggested_domains` for this
  PR's head SHA includes `staff` (`docs/decisions/ADR-0039-persist-review-receipts.md`
  and this contract, `docs/reviews/381-392-quick-fixes-CONTRACT.md`, are both in
  `program/review-risk.json`'s `constitution` high-risk category) — requested from the
  Program, not yet landed
- [ ] Security (when sensitive paths are touched) — not required: Sensitive set is No
  (see below) — no auth/session/token/secret/PII/export/SQL/subprocess/file-path/
  cookie/crypto/dependency surface in this diff — and `review plan`'s
  `suggested_domains` for this PR's head SHA does not include `security`
- [ ] UX (when a human-facing flow or operator interface changes) — not required: no
  path under `program/review-risk.json`'s `conditional_globs["ux"]` (`ui/**`,
  `web/**`, `frontend/**`, `templates/**`, `docs/HUMAN-COMMUNICATION.md`,
  `.github/ISSUE_TEMPLATE/**`, `.github/pull_request_template.md`) is touched by this
  diff, and `suggested_domains` does not include `ux`

## Green gates

- `python3 tests/dod_validate.py` — D01 PASS, no other condition's PASS/FAIL changed
  from the pre-task baseline (6/24 → 7/24, only D01 moved)
- `tests/run.sh` (full control-plane + toolchain suite) — `Ran 1133 tests ... OK`, zero
  failures/errors
- `python3 -m unittest -v tests.test_go_toolchain` — `Ran 23 tests ... OK`
- `bash -n deploy/provision-ubuntu.sh` — clean (file is byte-identical to base_sha; no
  production code touched by this task)

## Sensitive set

No. Diff is `docs/decisions/ADR-0039-*.md` (one line), `docs/prd/DOD-MANIFEST.json`
(one manifest entry, evidence rows only, not the DoD text), and new files under
`docs/audit/baseline/` (evidence captures) plus this contract and the `.omni/decisions.md`
/`.omni/ledger.md` disclosure entries. No auth/session/token/secret/PII/export/SQL/
subprocess/file-path/cookie/crypto/dependency surface.
