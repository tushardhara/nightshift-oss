# Review contract

## Identity

- Work item: issue #218 — "Make Program stop or split work when a task budget
  is exhausted" (task `218-build`)
- Committed contract path and blob OID: `docs/reviews/218-budget-expired-operating-rule-CONTRACT.md`,
  blob OID recorded in the freezing commit (this file's first commit on this
  build branch) — the freezing commit's own git blob hash is the authority,
  not this prose copy
- Contract version: 2
- Base SHA: `ad1766810fe74eda800c4f163b2e27afe74cc26d` (`origin/main` tip at
  checkpoint 15, wave `218`)
- ADR: `docs/decisions/ADR-0047-budget-expired-operating-rule.md`. Cites and
  is bound by ADR-0027 (`STATUSES` tuple) and ADR-0028 (`Closed without
  completion` ↔ `NOT_PLANNED` closure); amends neither. (Corrected
  2026-08-30: this line previously also cited ADR-0032/ADR-0033. Staff
  review of PR #355 found ADR-0047 never actually cites either — grep
  confirms zero occurrences — and that the `superseded` semantics this
  contract relies on come from ADR-0045's pre-existing
  `TERMINAL_TASK_STATES`, not from ADR-0032 or ADR-0033. Documentation-only
  fix; no change to ADR-0047's decision content or to `program/config.yaml`.)
- Review owner (cross-crew lead, not the author): crew_beta
- Frozen at (UTC): 2026-08-30 (added post-hoc to PR #355 at the program's
  request, before that PR is routed to review — see the Process note below;
  implementation, ADR, and full-suite green evidence already existed on this
  branch at freeze time)

**Process note.** This build (task `218-build`, PR #355) was opened without
a committed contract alongside it, unlike #216/#217's precedent. The gap was
identified by the program (session `c2dff535…`) before routing the PR to
review and is corrected here, in the same worktree and branch, before any
review dispatch: the contract's content is not retrofitted to match what
shipped — the acceptance criteria below are copied verbatim from the issue
and from `pm`'s own DoR verdict, both of which existed and were finalized
before the implementation commit (`fb6d25b`), only the *contract file's own
commit* postdates it. Same WA-5 single-author build disclosed in the PR body
and ADR-0047 §Process applies unchanged; nothing here revises that.

## Acceptance criteria

Verbatim from the GitHub issue body (`gh issue view 218`) plus the AC6/AC7
resolution `pm`'s Definition-of-Ready pass settled before returning READY
(recorded in ADR-0047 §Context/§Decision and `.omni/decisions.md`'s
`~00:15Z` entry for #218).

| ID | Exact criterion | Verification |
|---|---|---|
| AC1 | Program recognizes `budget_expired` as a scheduling verdict, not infrastructure failure. | `tests/test_budget_expired_operating_rule.py::*` — bullet text asserts the verdict framing explicitly, distinct from the adjacent "assume the worker died" recovery rule |
| AC2 | No busy retry loop after a budget denial. | Bullet text requires Program to choose exactly one of the four (first expiry) or three (post-extension) actions instead of re-attempting the same denied `claim acquire`/`slot acquire` |
| AC3 | `EXTEND_ONCE` is allowed only when no extension already exists. | Bullet text gates `EXTEND_ONCE` on `extension_seconds` reading null, backed by #217's own `already_extended` control-plane refusal as a second layer; explicitly named unavailable after the one extension |
| AC4 | SPLIT creates/requests smaller bounded work rather than continuing the same oversized task unchanged. | Bullet text: `SPLIT` dispatches the owning crew's `pm` to create smaller, bounded (S/M) work items against the same checkpoint `base_sha` |
| AC5 | BLOCK records the concrete external/dependency reason that prevents safe progress. | Bullet text: `BLOCK` records the concrete reason in `.omni/blocked.md` and the ledger, sets `state:blocked` on the issue |
| AC6 | FAIL records why the current approach cannot satisfy the frozen contract. | Bullet text: `FAIL` records the reason in `.omni/decisions.md`, then applies exactly one disposition — see AC6/AC7 resolution below |
| AC7 | Program reports the chosen action in the normal human-first status format. | Bullet text requires the chosen action and its one-line reason to be the first thing in the task's next status report, using the existing six-field first screen |

**AC6/AC7 resolution (settled by `pm`'s DoR pass, binding on this
contract).** ADR-0027's `STATUSES` tuple (`docs/HUMAN-COMMUNICATION.md`,
enforced by `tests/test_human_first_communication.py`) is closed and has no
word for "the approach failed," which put AC6 (FAIL records why the
approach can't satisfy the contract) and AC7 (report the action in the
existing human-first format) in real tension. Resolution: `FAIL` is not a
single fixed status word — its disposition is exactly one of the two
existing negative/terminal statuses, chosen by what Program actually decided
to do next:

- `FAIL` → drop the task: mark the task `state=dropped`, then close the
  GitHub issue `NOT_PLANNED` and report `Closed without completion` ONLY
  when `nightshift-ctl issue status --issue <n>` reports `closable` AND
  that crew's `pm` has signed off on the drop — the SAME gate the
  TRACKER step already requires for every issue closure, not a new one.
  If `closable` reads false (the common case for a stand-alone dropped
  task, since `state=dropped` is excluded from that command's own
  `active` set and nothing else exists to integrate), Program does NOT
  close the issue: it reports `Blocked` instead and leaves the close to
  a later human-equivalent pm/product call. Corrected 2026-08-30 per a
  `security` MERGE_BLOCKER on PR #355 — see "Contract amendments" and
  ADR-0047's addendum below; the original wording closed the issue
  unconditionally, bypassing this same file's own pre-existing
  closability/pm-acceptance gate.
- `FAIL` → re-plan under a new ADR: mark the task `state=superseded`, name
  the successor, report `Superseded` (`superseded` is already an ordinary
  terminal `tasks.state` value, per `bin/nightshift-ctl`'s
  `TERMINAL_TASK_STATES` / ADR-0045; not a new status).
- `SPLIT` keeps sole, exclusive ownership of "cut scope and keep going" —
  never a `FAIL` disposition — so the four top-level actions stay mutually
  exclusive, per the issue's own requirement that Program choose exactly
  one.

This resolution is binding, not reopened by this contract; ADR-0047 treats
it the same way, consistent with the precedent ADR-0045 set for a
materially identical DoR-follow-up resolution on issue #215.

## Explicitly out of scope

Issue's own list, carried forward verbatim: changing control-plane budget
math; worker cancellation; automatically choosing split boundaries with a
new service; PRD decomposition; changing review rules. Structurally
guaranteed additions:

- No change to `bin/nightshift-ctl` — every mechanism this rule references
  (`task show`, `task extend`, `reason_code=budget_expired`/
  `already_extended`, `state=superseded`) already exists from #215/#216/#217
  and is reused, not modified.
- No new ADR-0027 `STATUSES` value — the AC6/AC7 resolution maps `FAIL` onto
  the two existing negative/terminal statuses only (see above); no new
  status word is introduced.
- No schema change, no new CLI surface.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review (delegate: crew_beta, standing in for
      in-crew `reviewer_claude`/`reviewer_codex` per Working Agreement 5 —
      issue #236, reproduced live for this task in ADR-0047 §Process)
- [x] QA (delegate: crew_beta, same WA-5 substitution)
- [x] Cross-crew review
- [x] Staff / ADR consistency (new ADR this round — ADR-0047 — cites
      ADR-0027/ADR-0028 only (corrected 2026-08-30; see the ADR bullet
      above); staff review is also mandatory per `docs/owners.md`'s
      `program/**` row, independent of `suggested_domains`)
- [x] Security — `nightshift-ctl review plan --task 218-build --repo . --head-sha
      fb6d25bf24a7ce6aafb357c47e3ae32c9268b229` (ADR-0042; run against this
      PR's exact, final head) returned `suggested_domains: [code_review,
      cross_crew, pm, qa, security, staff]`, `profile: HIGH_RISK`
      (`program/config.yaml` sensitive-content terms: auth/session/token,
      pre-existing in the file, not newly introduced by this diff) —
      security required
- [ ] UX — not in `suggested_domains`; no path under `conditional_globs["ux"]`
      touched

**Additional domain beyond `suggested_domains`: `platform`.** The same
`review plan` call also flagged an `other_owner: platform` trigger on
`program/config.yaml`. `docs/owners.md` assigns `program/**` to `platform`,
single-writer, "no crew owns this... Changes require an ADR." PR #355 edited
`program/config.yaml` and opened before an edit lease was requested — a
disclosed sequencing gap (`.omni/decisions.md`'s `~00:20Z` entry for #218;
`nightshift-ctl dump` confirmed `leases: []` at every point checked, so no
competing writer collided). An `edit` lease (`lease_id=8`) was acquired and
released per the `pr-opened` event as an honest audit trail after the fact.
The program has separately, retroactively recorded a duplicate lease record
(`lease_id=9`, acquired+released `pr-opened`) covering the same resource —
flagged here for the review owner's awareness, not an action item on this
contract. `platform` review of this PR is requested on top of the six
domains above, given the ownership question ADR-0047 itself does not
resolve.

**Machine-readable checklist (ADR-0039, issue #231).** Per
`bin/nightshift_review_receipts.parse_required_domains()`: the seven
canonical `[x]`/`[ ]` rows above are the parsed, authoritative set. The
`platform` addition is recorded in prose only (outside the seven canonical
prefixes) because `platform` review here is an ownership-gap remediation
request, not one of the seven receipt-tracked domains ADR-0039 enumerates —
it does not change what `parse_required_domains()` returns for this item.

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | security | MERGE_BLOCKER | AC6 (`FAIL` records why the contract can't be satisfied) + the pre-existing TRACKER closability/pm-acceptance gate (same file, "YOU close an issue, and only when `nightshift-ctl issue status --issue <n>` says closable AND that crew's pm has signed acceptance") | `program/config.yaml`'s `FAIL` → drop clause, originally "close the GitHub issue with reason `NOT_PLANNED`" with no closability/pm check | A task that hits `FAIL` never reaches `integrated`, so `closable` (`bin/nightshift-ctl`'s `all(s == "integrated" for s in active)`) is false every time this path fires — the bullet would tell Program to close an issue that structurally can never pass its own gate, with no pm sign-off, potentially while other dependent work is still open. Reproduced by reading `_issue_status` directly, not asserted from precedent. | resolved | Bullet rewritten to mark the task `state=dropped` then route the close through the SAME closable+pm-signoff gate; reports `Blocked` (not a silent close) when that gate is not met. 5 new regression tests added (`FailDropRoutesThroughTheExistingTrackerCloseGate`), shown failing pre-fix / passing post-fix. See ADR-0047's 2026-08-30 addendum. |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## Contract amendments

**v2, 2026-08-30.** In response to `security`'s MERGE_BLOCKER (finding F-1
above) on PR #355 at head `691dc2805a7c5c2f9d92ded2d96234e67db51328`: the
`FAIL` → drop AC6/AC7 resolution bullet above is corrected to route the
GitHub issue close through the pre-existing TRACKER closability/pm-acceptance
gate instead of closing unconditionally, and to report `Blocked` (never a
silent close) when that gate is not met. `program/config.yaml`,
`docs/decisions/ADR-0047-budget-expired-operating-rule.md`, and
`tests/test_budget_expired_operating_rule.py` all changed in the same
commit as this amendment; no other acceptance criterion is affected. Fixed
by the same implementer (crew_alpha lead, WA-5), same branch
(`crew_alpha/218-build`), same PR (#355).
