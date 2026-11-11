# Review contract

Copy this template into a committed work-item/spec artifact and freeze that file at
the wave checkpoint before the first implementation commit. The delivery PR links
and may mirror the exact committed version; its mutable body is never the authority.
Do not edit a frozen version in place.

## Identity

- Work item: GitHub issue #214, "Record task execution start on the first worker
  slot" (tushardhara/nightshift). `pm` Definition-of-Ready verdict **READY**,
  11 acceptance criteria, frozen in
  `docs/specs/214-record-task-execution-start.md` (this session's `pm`
  independently re-verified a prior, now-unrecoverable DoR pass from session
  `214-dor` against the live code before restating it).
- Committed contract path and blob OID: `docs/reviews/214-record-task-execution-start-CONTRACT.md`;
  blob OID is the exact-content hash of this file at the freezing commit — read it
  with `git rev-parse <freezing-commit>:docs/reviews/214-record-task-execution-start-CONTRACT.md`.
- Contract version: **v2** (bumped 2026-08-29, crew_beta CB-1 -- corrected AC-2/AC-3 location citations after the ADR-0043 amendment; see "Contract amendments" at the bottom for the full v1->v2 record. AC-2/AC-3 rows below are the v2 text.)
- Base SHA: `27f4d188b4882f047541811dae0ce8626b99137e` (Checkpoint 5, id 5,
  `origin/main` tip at freeze time).
- ADR: `docs/decisions/ADR-0043-record-task-started-at.md` (`Status: accepted`;
  **single-author** — cross-vendor design-debate could not run because
  `eng_claude`/`eng_codex`/`qa` dispatch is confirmed sterile this session
  (issue #236, `OPEN`, P0 — `args.slot_id`/`task_id`/`generation` required by
  `worker_semaphore` but forbidden by `sys_session_send`'s own JSON schema,
  reproduced across 2 argument shapes). Staff review of the ADR requested from
  the program; per the design-debate skill's "unattended mode" clause,
  implementation proceeds in the same turn rather than blocking on it, and
  that gap is disclosed here, in `STATUS.md`, and in the PR body.
- Implementer: crew_alpha lead, under **WA-5** (wave 1, issue #13 — a crew lead
  whose worker dispatch is sterile builds under a slot it holds, and says so in
  the PR body). Held slot: `slot_id=7`, `generation=3`, `phase=new`,
  `holder=crew_alpha/eng_claude`, `run_id=fixture-review-231` (acquired against
  the live control plane before the first implementation commit).
- Review owner: crew_beta (cross-crew; **mandatory, not customary**, per WA-5,
  since in-crew review/QA could not be dispatched).
- Frozen at (UTC): 2026-08-29 (this session).

## Acceptance criteria

Verbatim from `docs/specs/214-record-task-execution-start.md` §2. IDs are
stable identifiers, not reordered.

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | `tasks` gains an additive, nullable `started_at REAL` column via the existing `add_column` migration helper; `started_at` is NOT added to `TASK_FIELDS`, so `cmd_task_upsert` stays structurally incapable of writing it; no `--started-at` flag exists on `task upsert`. | Code inspection: `ensure_schema` diff, `TASK_FIELDS` diff (must be empty), `task upsert --help` diff (must be empty). |
| AC-2 | (v2) `tasks.started_at` is set exactly once, by an `UPDATE ... WHERE id=? AND started_at IS NULL` (or equivalent) executed inside the SAME transaction as the task's real ADMISSION write (`INSERT INTO task_admissions`, `UPDATE tasks SET state='building'`) — never in a separate connection or transaction. v1 wrongly named `cmd_slot_acquire`'s reservation transaction; see spec AC-2's amendment note and ADR-0043's amendment. | Code inspection: `deploy/policies/omnipods_policies.py`'s `worker_semaphore`, `phase == "new"` branch, between `cx.execute("BEGIN IMMEDIATE")` (`:241`) and `cx.execute("COMMIT")` (`:415`); test: `test_task_started_at.py::test_expired_unconsumed_reservation_then_later_genuine_admission` proves a reservation alone (which still uses `cmd_slot_acquire`'s own transaction) cannot set it, and only the real admission consume does. |
| AC-3 | (v2) The `started_at` write sits adjacent to the real admission write (`INSERT INTO task_admissions`) — the only call site that fires for a genuinely new admission — and appears nowhere else in either file. v1 wrongly named `INSERT INTO worker_slots` (the RESERVATION write) as the adjacency target; see spec AC-3's amendment note. | `grep -n "started_at" bin/nightshift-ctl` shows only schema/migration lines (no write site left in this file); `grep -n "started_at" deploy/policies/omnipods_policies.py` shows exactly one write site, at `:411-414`, immediately after the `task_admissions` insert (`:387-392`) inside the same `if row["phase"] == "new":` block (`:385`). |
| AC-4 | The write fires if and only if `kind == "normal" and phase == "new"`; it must not fire when `phase` is `"planning"` or `"continuation"`, and must not fire when `kind == "oncall"`. | Tests: one acquisition per phase/kind combination, asserting `tasks.started_at` stays NULL for every combination except `("normal", "new")`. |
| AC-5 | A denied grant (`granted=False`) leaves `tasks.started_at` untouched. | Test: force a denial (e.g. budget exhausted, or a malformed NEW request), assert `tasks.started_at` still NULL. |
| AC-6 | A `reused=True` idempotent-retry grant leaves `tasks.started_at` untouched. | Test: acquire NEW once (sets `started_at`), capture the value, acquire the exact same NEW request again (`reused=True` expected), assert the value is byte-identical (not just non-null) after. |
| AC-7 | The write is guarded with `AND started_at IS NULL` (or equivalent), so exactly-once holds structurally even if a second `phase='new'` grant were ever produced for the same task. | Test: directly exercise the guarded `UPDATE` twice with different timestamps against the same row (bypassing the phase machinery if needed to reach the second call), assert the second call is a no-op. |
| AC-8 | `tests/test_task_graph.py`, `tests/test_slots.py`, `tests/test_admission_gate.py`, `tests/test_claims.py` pass unchanged, zero modified assertions. | `git diff <base>..<head> -- tests/test_task_graph.py tests/test_slots.py tests/test_admission_gate.py tests/test_claims.py` is empty; full run green. |
| AC-9 | A new regression test demonstrates the positive path: acquiring the first `kind='normal', phase='new'` slot for a task sets `tasks.started_at` to a non-null value; shown red pre-change, green post-change. | Named test, run against `NIGHTSHIFT_TEST_CTL` pinned to the pre-change binary (fails/errors) and the post-change binary (passes). |
| AC-10 | A new regression test (or table-driven set) demonstrates each negative path leaves `tasks.started_at` NULL: (a) denied, (b) `reused=True` retry, (c) `phase='planning'`, (d) `phase='continuation'`, (e) `kind='oncall'` — each shown red before the change (i.e. would fail had the implementation set `started_at` unconditionally on every grant) and green after. | Same red/green baseline-capture method as AC-9, one sub-test per case, each with a stated control per WA-009 (the case that must pass, e.g. the NEW-phase grant itself still working). |
| AC-11 | Once set, `tasks.started_at` is stable across every later slot acquisition for that task: a subsequent planning, continuation, retry, or re-admission NEW-phase slot for the same task leaves the already-set value unchanged. | Test: set `started_at` via a NEW grant, then drive at least one planning slot, one continuation slot (via `seed_admission`-style fixture), and one retry of the same NEW request, re-reading `tasks.started_at` before/after each and asserting no change. |

## Explicitly out of scope

- Any change to `TASK_FIELDS` or `cmd_task_upsert`'s writable field set —
  `started_at` must remain unreachable through `task upsert` (AC-1).
- Any enforcement, consumption, or display of `started_at` by any other
  command (readiness snapshot, CLI output, metrics) — follow-up, not this
  ticket.
- The previously-flagged dead `task_admissions` INSERT path (confirmed by
  `pm`: the table is created, read, and dumped, but never populated anywhere
  in `bin/nightshift-ctl`). Tracked separately — a `staff` session titled
  `tech-debt-214-task-admissions` exists; the program has been asked to route
  it. `.omni/tech-debt.md` did not yet show a `214`-tagged row as of this
  freeze — confirm before or alongside this PR; do not duplicate or fix that
  gap here regardless.

## Required review domains

- [x] PM acceptance — `pm` is reachable (`EXEMPT_AGENTS`, not gated by the
      sterile `worker_semaphore` dispatch); signs criteria in the normal way.
- [x] Different-vendor code review — **substituted by cross-crew review**,
      since in-crew `reviewer_claude`/`reviewer_codex` dispatch is sterile
      (issue #236). Mark this box satisfied only by the cross-crew verdict
      below, never by the lead's own read of its own diff.
- [x] QA — **substituted by cross-crew review's QA-domain check** plus the
      lead-authored regression tests at AC-9/AC-10/AC-11, since in-crew `qa`
      dispatch is sterile. Cross-crew review must independently confirm the
      baseline-red evidence, not just that tests exist.
- [x] Cross-crew review — mandatory per WA-5, not customary.
- [ ] Staff / ADR consistency — requested from the program for ADR-0043;
      not required to gate this contract's "ready to build" state per the
      design-debate skill's unattended-mode clause, but required before this
      PR merges. Flagged, not silently dropped.
- [ ] Security — scope is `bin/nightshift-ctl` (schema + one guarded
      `UPDATE`/`INSERT` transaction) and its own tests only; no auth,
      authorization, sessions, tokens, secrets, PII, data export, external
      input, SQL string construction (parameterised throughout), subprocess,
      file paths, cookies, crypto, or new dependency. Not required unless a
      reviewer disagrees once the diff exists.
- [ ] UX — no human-facing flow, copy, or operator-facing interface change.

Machine-readable parsing of this checklist follows the rule in
`docs/reviews/REVIEW-CONTRACT-TEMPLATE.md`'s "Required review domains" section:
each row matched by a fixed canonical-label PREFIX, independent of any
parenthetical rationale; `[x]`/`[X]` means required, `[ ]` means not required.

Removing a domain after freeze requires a new contract version and rationale.

## Finding receipt

Every finding uses one severity from ADR-0026.

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 |  | `MERGE_BLOCKER` / `PRE_DEPLOYMENT` / `FOLLOW_UP` |  |  |  | open |  |
| F-2 | implementer (self-found, not external review) | `FOLLOW_UP` | AC-8's literal wording ("pass unchanged, with zero modified assertions") | `tests/test_task_graph.py`'s `allow_null_task_state` helper hardcodes a 14-column mirror (`tasks_nullable`) of the `tasks` table for a damaged-legacy-schema fixture; adding `started_at` made real `tasks` 15 columns, so `INSERT INTO tasks_nullable SELECT * FROM tasks` raised `sqlite3.OperationalError: table tasks_nullable has 14 columns but 15 values were supplied` in `TaskReadinessTest.test_every_non_pending_own_state_fails_closed` and `test_null_prerequisite_state_does_not_satisfy_edge`. | Baseline red captured pre-fix (`/tmp/prefix-214/nightshift-ctl`, both tests ERROR with exactly that message); fixed by adding `started_at REAL` as the 15th column to the `tasks_nullable` DDL (column order matches, since `ALTER TABLE ADD COLUMN` always appends). Full suite green after (968/968, `tests/run.sh`). | resolved | Not an assertion change (no `assert*` line in either test was touched) — AC-8 holds on its literal wording — but the file itself has a 1-line diff, so disclosed here rather than silently claimed as untouched. |
| F-3 | pm (acceptance sign-off, self-found before cross-review) | `FOLLOW_UP` | AC-7, AC-10(e), AC-11 | No shipped test exercises a second genuine `phase='new'` grant for the guarded `UPDATE ... AND started_at IS NULL` (AC-7); the `oncall` sub-case of AC-10 passes in both the pre-fix and post-fix baseline-red capture, so it never demonstrates the required red state; AC-11's planning-after-set and re-admission sub-cases are not covered by any shipped test. | `pm` independently reproduced all three behaviors by hand against the live CLI and confirmed each holds correctly -- this is a test-coverage gap, not a behavior defect. Full detail: `docs/reviews/214-pm-acceptance.md`. | open | Behavior verified correct by manual reproduction; routed to cross-crew review's QA-domain check per this contract's substitution clause, since in-crew `qa` dispatch is sterile (issue #236). Cross-crew reviewer: please add or confirm regression coverage for these three cases before signing the QA domain. |
| F-4 | staff (ADR-0043 review) | `MERGE_BLOCKER` | inconsistent-with ADR-0033 (the `phase='new'` reservation vs. `worker_semaphore` admission boundary) | The guarded `UPDATE tasks SET started_at=...` sat inside `cmd_slot_acquire`'s RESERVATION transaction (`bin/nightshift-ctl`), not `worker_semaphore`'s ADMISSION transaction (`deploy/policies/omnipods_policies.py`, the only real `INSERT INTO task_admissions` site) -- contradicting this contract's/the spec's own claim that admission was dead code never populated in production (corrected, see spec §4 and ADR-0043's amendment). | Reachable: a first reservation granted but never consumed (expires/reaped per ADR-0033 §6, task stays pending) permanently pinned `started_at` to its own early timestamp even when the task was genuinely admitted later at real time t2 ≳ t1, with no repair path (AC-11 forecloses re-arming). Also leaked into `.omni/control-state.json` via `cmd_dump`'s unconditional projection. | resolved | Relocated the same guarded `UPDATE` into `worker_semaphore`'s `phase == "new"` branch, same transaction as the real `task_admissions` insert / `state='building'` update. Regression test added: `tests/test_task_started_at.py::test_expired_unconsumed_reservation_then_later_genuine_admission` (RED against the pre-amendment binary, GREEN after -- see `docs/audit/issue-214-baseline-red-and-full-suite.md` amendment section). Full `test_task_started_at.py` (10/10), `test_admission_gate.py`, `test_policy.py`, `test_claims.py`, `test_task_graph.py`, `test_slots.py` all green; full suite green. No schema change; no ADR reopening beyond ADR-0043's amendment note, per staff. |

The review owner publishes one deduplicated fix packet per round. A severity change
must retain the original finding and record the rationale.

Never execute a reproduction against a live system. A production-only security or
data-loss risk may be proven by a concrete causal failure scenario and safe evidence.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| PM acceptance | pm (session `214-implement`) | PASS-WITH-NOTES -- 8/11 ACs (1,2,3,4,5,6,8,9) fully MET; AC-7, AC-10(e), AC-11 PARTIALLY MET (test-coverage gaps only, see F-3; behavior independently reproduced and confirmed correct) | commit `9b43bdb` | n/a | `docs/reviews/214-pm-acceptance.md` |
| staff | staff (ADR-0043 review) | INCONSISTENT-WITH ADR-0033 -> MERGE_BLOCKER (F-4), now RESOLVED per this row's own fix | n/a (ADR/design-level finding, not tied to one diff SHA) | n/a | ADR-0043 amendment section; F-4 above |
|  |  |  |  |  |  |

Content evidence may transfer only across identical reviewed blobs with unchanged
dependency seams. Re-run CI, provenance, PR-body, authorization, and live/runtime
checks.

## Contract amendments

Never rewrite the frozen rows above. Create the next version and record:

- previous version and reason;
- criteria, domains, ADR, or scope changed;
- attestations invalidated by that change;
- implementation work that must be reconsidered.

### v1 -> v2 (2026-08-29, crew_beta CB-1)

- **Reason:** the ADR-0043 amendment (staff MERGE_BLOCKER, finding F-4)
  relocated the `started_at` write from `cmd_slot_acquire` (reservation) to
  `worker_semaphore` (real admission). v1's AC-2 and AC-3 rows described the
  superseded location verbatim from the (then-unamended) spec, so they were
  left factually wrong about *where* the write lives even though the
  *property* they assert (exactly-once, same-transaction-as-admission,
  adjacent-to-the-real-INSERT) was unaffected by the relocation and never
  stopped holding.
- **Criteria changed:** AC-2, AC-3 (wording and verification method only;
  no criterion was added, removed, or weakened). AC-1, AC-4..AC-11
  unchanged.
- **ADR:** `docs/decisions/ADR-0043-record-task-started-at.md`'s amendment
  section is the authority for this change; not re-litigated here.
- **Attestations invalidated by this change:** PM's original AC-2/AC-3 "MET"
  verdicts (`docs/reviews/214-pm-acceptance.md`, commit `9b43bdb`) were
  against v1's wording and the pre-amendment code (commit `6d26104`) and are
  superseded, not wrong for their time -- they correctly described that
  code. PM re-attests AC-2/AC-3 against v2 and commit `d845d1b` separately
  (see `docs/reviews/214-pm-acceptance.md`'s dated addendum).
  crew_beta's round-2 review (zero MERGE_BLOCKER on correctness, including
  an independent mutation-test rerun) is unaffected by this wording bump.
- **Implementation work to reconsider:** none -- the code was already fixed
  and tested (commit `d845d1b`) before this contract wording caught up to
  it; this bump is documentation-only.
