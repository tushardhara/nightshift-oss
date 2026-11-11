# DoR: #390 — Stop no-op/reconciliation heartbeats from creating Git commits and PRs

Status: **READY** (with one split-off follow-up issue to file — see §7)
Owner: crew_alpha PM · Seam: **A** (`bin/nightshift-ctl` / coordination kernel, per `docs/owners.md:14-17`)
Run: `fixture-review-231` · Checkpoint 22 · Base `23b9311157a596a821afdc9323cf3cbae1382529` (origin/main)
Refs #390. Reads (not modified by this item): #174 / ADR-0044, #286/#388 / ADR-0052 (PR #387,
merged after this DoR's base SHA at `7e78ce6`, read here for its shipped shape).

---

## 0. What's actually in the repo today (grounding)

- `bin/nightshift-ctl` is 4765 lines at this SHA. Heartbeat-adjacent primitives that already exist:
  - `SCHEDULE_STATUSES` (`bin/nightshift-ctl:2229-2236`) includes `"standdown"`, deliberately kept
    out of `SCHEDULE_INCIDENT_STATUSES` (`:2241`) per ADR-0044 §6 — a Program that loses the
    authority race is not an incident.
  - `cmd_schedule_observe` (`:2306`) is the CLI surface that records a scheduled-run observation,
    including `--status standdown`. It writes to `scheduled_run_observations` /
    `scheduled_run_detector_state` — control-plane bookkeeping, not Git.
  - `cmd_authority_acquire`/`renew`/`release`/`permit_dispatch`/`show`
    (`:1539-1719`) are ADR-0044's fence primitives. `authority acquire` exit 3 = stand-down (no
    mutation, per ADR-0044 §2/§7b step 1).
  - ADR-0044 §7b (`docs/decisions/ADR-0044-program-authority-fencing.md:224-410`) already specifies,
    in writing, that a Program turn that loses the authority race performs **zero** dispatches and
    exactly one control-plane call (`schedule observe --status standdown --class none`), then ends
    the turn. This is the authority-loss slice of #390's Required Behavior C. It is a *written
    contract for the Program's own scheduled-task prompt*, not yet a code-enforced gate — nothing in
    `bin/nightshift-ctl` today stops a Git-mutating command from running regardless of authority
    state. That gap is in scope for #390 (see §4).
  - `cmd_pr_create` (`:2878`), `cmd_checkpoint_create` (`:1761`) are the actual Git/PR-mutating
    surfaces a heartbeat turn can reach directly from `nightshift-ctl`.
- `bin/nightshift_bookkeeping_release.py` (397 lines, landed by PR #387, `docs/decisions/ADR-0052-bookkeeping-only-fast-release-path.md`) is the #286/#388 classifier:
  - Pure read/compute module, no `control.db` writes (same split as
    `bin/nightshift_review_plan.py`) — entry point `build_bookkeeping_plan(cx, task_id, repo,
    head_sha, main_sha=None)` returns a dict with `classification ∈ {"bookkeeping-only","normal"}`,
    `reasons: list[str]`, `checks: dict`, `fast_path_eligible: bool`, `plan_digest`.
  - Fails closed to `"normal"` on anything not an affirmative pure add/modify of an allow-listed
    path (`.omni/STATUS.md`, `.omni/ledger.md`, `.omni/tech-debt.md`, `.omni/reports/**`).
  - Exposed via `nightshift-ctl bookkeeping plan --task <t> --repo <r> --head-sha <sha> [--main-sha
    <sha>]` (plane-required, read-only snapshot), `bookkeeping verify-head --expected-head-sha
    --actual-head-sha` (plane-free, pure SHA comparison), `bookkeeping receipt-record --task
    --base-sha --head-sha --classification --verdict --reasons-json --merged-sha --recorded-by`
    (writes the append-only `bookkeeping_receipts` table).
- `docs/owners.md:14-17,26` — Seam A owns `bin/nightshift-ctl`, `deploy/policies/**`,
  `docs/CONTROL-PLANE.md`/`docs/design/SYSTEM.md`, and tests that drive `nightshift-ctl`. Row 26:
  **`.omni/**` is owned by `program`, single-writer.** Crew_alpha does not write `.omni/STATUS.md`,
  `.omni/ledger.md`, or `.omni/reports/**` in production and must not do so in tests either — see
  §4's fixture-isolation rule.
- **Corrected 2026-08-30 per crew_beta cross-crew review on PR #397 (MERGE_BLOCKER, resolved):**
  the previous version of this bullet said "eleven of the fourteen historical PRs" while listing
  16 numbers — internally inconsistent, and neither "fourteen" nor "eleven" is derivable from
  issue #390's actual text. Issue #390 names two distinct lists: its Problem section cites **18**
  PR numbers as historical evidence (`#389, #382, #373, #368, #363, #360, #359, #358, #357, #351,
  #346, #347, #330, #326, #322, #317, #284, #255`); its AC-14 separately names a smaller, exact
  **11**-PR replay-fixture subset (`#389, #382, #373, #368, #357, #351, #330, #326, #317, #284,
  #255` — reproduced correctly in §3/§7 below, unaffected by this correction). Re-verified against
  this repo via `gh pr view <n> --json state,headRefName,mergedAt` for all 18 Problem-section PRs:
  **all 18 of 18 are `MERGED`** with `headRefName` matching the `program/heartbeat-*-status|
  -close|-followup|-report` pattern (including #363 → `program/heartbeat-0300-status`, merged
  `2026-08-30T03:24:28Z`, and #351 → `program/heartbeat-2300-status`, merged
  `2026-08-29T23:13:31Z`, both omitted from the prior, incorrect list) — i.e. they are
  structurally what the issue says they are: heartbeat-authored bookkeeping PRs, not feature work.

---

## 1. Outcome (observable behaviour)

Today, a Program heartbeat turn that discovers nothing changed — no new dispatch, no authority won,
no unresolved review, no closable issue — still routinely produces a Git commit, branch, and PR
(evidence: §0's eleven-PR list). After this item ships: **a heartbeat turn that has no semantic
durable-repository delta ends with zero new Git objects (no branch, no commit, no PR) and zero
Release dispatches, and this is enforced by a function/CLI call any heartbeat-adjacent code path can
be tested against — not by hoping the turn's own narration is right.**

---

## 2. Required shape (grounds the acceptance criteria — not itself a numbered AC, but every AC below
   depends on this vocabulary existing with these exact names so criteria are checkable by test id,
   not by reading prose)

**New module:** `bin/nightshift_heartbeat_persistence.py` — pure compute, no `control.db` writes,
same split as `nightshift_bookkeeping_release.py` / `nightshift_review_plan.py`.

- `decide(outcome: dict) -> dict` — the persistence-decision function. Deterministic, no I/O, no
  network, no LLM call inside it.
  - Input `outcome` fields (all required unless noted):
    - `authority_status: "granted" | "standdown"` — sourced from the *existing*, unmodified #174
      surface (`authority acquire`/`authority show` exit code / body), never a new authority read.
    - `observation_codes: list[str]` — each member MUST be drawn from a closed enum
      `OBSERVATION_CODES` seeded 1:1 from Required Behavior B's list (16 entries): `queue_empty`,
      `another_program_owns_work`, `standdown`, `no_new_dispatch`, `dod_unchanged`,
      `vendor_cooldown_unchanged`, `issue_blocked`, `pr_under_review`, `merge_confirmed`,
      `worker_still_running`, `reap_found_nothing`, `from_owner_scan_found_nothing_new`,
      `heartbeat_task_still_active`, `next_heartbeat_note_only`, `timestamp_only_change`,
      `reporting_already_on_205`. Any code not in this set is treated as *unrecognized*, not
      silently dropped and not treated as a durable-delta signal.
    - `durable_delta_reason_code: str | None` — `None` unless the caller asserts a durable delta,
      and even then it must be a member of a second closed enum `DURABLE_DELTA_REASON_CODES` (e.g.
      `governed_pr_ready`, `adr_accepted_new_content`, `bookkeeping_only_diff_present` — exact
      membership is the implementer's to enumerate against real call sites, but it must be closed,
      not free text). A string outside this enum is treated identically to `None` (AC-10).
    - `diff_paths: list[str] | None` and `diff_is_timestamp_only: bool` (optional, default False) —
      lets the function refuse to flip `should_persist_to_git` true on a diff whose only changes are
      timestamp-shaped fields (AC-6), independent of what `durable_delta_reason_code` claims.
  - Output: `{"should_persist_to_git": bool, "reason_code": <str, one of a closed output enum>,
    "route": "none" | "bookkeeping_fast_path" | "governed_normal_path", "input_digest": <sha256 of
    canonical_json(outcome)>}`.
  - Output `reason_code` enum (illustrative, implementer finalizes exact spelling against
    `nightshift-ctl`'s existing naming style, e.g. `SCHEDULE_FAILURE_CLASSES`'s
    `snake_case`): `standdown_no_authority`, `observation_only_no_delta`,
    `timestamp_only_rejected`, `unrecognized_observation_fail_closed`, `durable_delta_governed`,
    `durable_delta_bookkeeping_only`, `unrecognized_durable_delta_reason_fail_closed`.
  - **Default/fail-safe:** `should_persist_to_git=False` whenever `durable_delta_reason_code` is
    `None`, unrecognized, or contradicted by `diff_is_timestamp_only=True` — regardless of what
    `observation_codes` or any accompanying prose says. This is the literal AC-10 mechanism: the
    function has no parameter through which an LLM's plain-language judgment ("this really is a
    real change") can flip the boolean; only a value from the closed enum can.

- **Enforcement wrapper**, exposed as `nightshift-ctl heartbeat-persist decide` (new CLI group,
  distinct from the pre-existing unrelated `slot heartbeat` verb at `:4284`) — takes the same
  `outcome` JSON on stdin/`--outcome-json`, calls `decide()`, and is the one call any heartbeat
  turn's own script/prompt is instructed to make before attempting a Git-mutating
  `nightshift-ctl` verb. Exit 0 + `should_persist_to_git=true` unblocks continuing; exit 3 means
  stop, zero Git calls.
  - **Code-enforced, not convention-enforced, on at least one real mutating surface:** at minimum,
    `cmd_pr_create` (`:2878`) — the actual surface every historical no-op PR in §0 went through —
    must refuse to run for a heartbeat-attributed invocation (a new, explicit marker analogous to
    `--claimant program` / `--oncall` in ADR-0044 §3b, e.g. `--heartbeat-persist-receipt <digest>`)
    unless a matching, unconsumed `should_persist_to_git=true` decision was just recorded. This is
    the same template ADR-0044 §3b already established and staff-reviewed (gate a specific verb on
    a specific caller-supplied marker); #390 does not need a new mechanism family, it needs one more
    instance of this one. Exact hook point(s) beyond `pr-create` are the implementer's call, but
    "the heartbeat's own prompt is supposed to check first" is not sufficient for AC-10 — the
    enforcement must survive a heartbeat turn that skips the check.
  - `nightshift-ctl heartbeat-persist record` — the telemetry sink (AC-13): one append-only row per
    decision (mirrors `bookkeeping_receipts`'s shape: new table, e.g. `heartbeat_persist_decisions`,
    additive `CREATE TABLE IF NOT EXISTS`, no existing table touched), fields at minimum:
    `run_id, authority_status, observation_codes_json, durable_delta_reason_code, should_persist,
    output_reason_code, route, recorded_at`.

- **Delegation to #286/#388 (not reimplemented):** when `decide()` returns
  `route="bookkeeping_fast_path"`, the calling code's *next* call is exactly `nightshift-ctl
  bookkeeping plan --task <task> --repo <repo> --head-sha <sha> [--main-sha <sha>]`
  (`bin/nightshift-ctl` bookkeeping group, `cmd_bookkeeping_plan`, backed by
  `nightshift_bookkeeping_release.build_bookkeeping_plan`), and its `fast_path_eligible` field — not
  a re-implementation of `classify_paths`/`check_ledger_append_only`/etc. — decides whether the
  bookkeeping-only fast path actually proceeds. #390's own module MUST NOT contain a second
  path-classifier.

- **Byte-identical, checked against:** AC-8/AC-9's "byte-identical" means: `sha256(file content)`
  immediately before the heartbeat-equivalent code path runs, compared to `sha256(file content)`
  immediately after, over a **temporary fixture copy** of `.omni/ledger.md`/`.omni/STATUS.md`'s
  format — never the live repo's `.omni/**` files, which are `program`-owned
  (`docs/owners.md:26`) and single-writer. Tests construct a tmpdir fixture with representative
  content, run the no-op decision path against it, and assert the fixture file's bytes and mtime
  policy are unchanged; they must not read or write the real `.omni/ledger.md`/`.omni/STATUS.md` in
  this repository.

---

## 3. Acceptance criteria (sharpened from the issue's AC-1..AC-14; same numbering preserved for
   traceability)

1. **AC-1 (unit):** given an `outcome` with `authority_status="granted"`, one or more
   `observation_codes` all in the closed enum, and `durable_delta_reason_code=None`,
   `decide()` returns `should_persist_to_git=False`; a wrapper-level test asserts this same input
   run through `nightshift-ctl heartbeat-persist decide` produces 0 new files/commits/branches/PRs
   and exit 0 with `route="none"`.
2. **AC-2 (follow-up issue — see §7):** a 24-iteration loop that calls `decide()` (or the CLI
   wrapper) with an unchanged no-op `outcome` 24 times running against a fixture repo produces 24
   exit-0/`route="none"` results and 0 Git objects created in the fixture repo across all 24.
3. **AC-3 (follow-up issue — see §7):** a simulated interleaving where one synthetic authority
   holder is "active" for a 4-hour fixture window and 4 separate `decide()` invocations carry
   `authority_status="standdown"` each produces `should_persist_to_git=False`,
   `reason_code="standdown_no_authority"`, and (per §2's enforcement wrapper) zero calls into any
   Git-mutating `nightshift-ctl` verb are permitted across all 4.
4. **AC-4 (unit, buildable now):** one fixture per no-op case — `queue_empty`+`dod_unchanged`,
   `pr_under_review`, `another_program_owns_work` (routing review), `another_program_owns_work`
   (integrating), `merge_confirmed`-only, `worker_still_running`, `issue_blocked`,
   `from_owner_scan_found_nothing_new` — each asserts `decide()` returns
   `should_persist_to_git=False` with a reason_code drawn from the closed output enum (not a
   generic catch-all).
5. **AC-5 (unit, buildable now):** one fixture with `durable_delta_reason_code="governed_pr_ready"`
   (or equivalent recognized code) and `diff_is_timestamp_only=False` asserts
   `should_persist_to_git=True`, `route="governed_normal_path"`, and that exactly one PR results
   when the wrapper is exercised end-to-end against a fixture repo (no more, no fewer).
6. **AC-6 (unit, buildable now):** a fixture identical to AC-5's positive case except
   `diff_is_timestamp_only=True` (or `diff_paths` containing only a timestamp-shaped field per the
   existing `STATUS_HEADING`-style regex convention already used in
   `nightshift_bookkeeping_release.py`) asserts `should_persist_to_git=False` even though
   `durable_delta_reason_code` is a recognized value — proving the reason code alone cannot flip the
   decision.
7. **AC-7 (manual/integration, buildable now):** demonstrate (test or documented manual step) that
   mirroring a heartbeat's findings to issue #205 succeeds via a comment/API call that does not go
   through `heartbeat-persist decide`/`route` at all — i.e. the #205 mirror is explicitly outside
   this decision's scope, not merely "false" through it.
8. **AC-8 (unit, buildable now):** per §2's byte-identical definition, a fixture `.omni/ledger.md`
   copy's SHA-256 and byte content are asserted unchanged after a no-op `decide()` path runs against
   it (fixture only, never the live file — see §2).
9. **AC-9 (unit, buildable now):** same as AC-8 for a fixture `.omni/STATUS.md` copy.
10. **AC-10 (unit, buildable now):** a fixture where `durable_delta_reason_code` is a plausible
    but *not enum-member* string (e.g. `"seems_important"`, or a free-text sentence simulating an
    LLM's own assertion) asserts `should_persist_to_git=False`,
    `reason_code="unrecognized_durable_delta_reason_fail_closed"` — proving textual/LLM assertion
    alone cannot pass the gate. A second test exercises the code-enforced hook on `cmd_pr_create`
    (§2) directly with no valid receipt and asserts it exits non-zero without creating a commit,
    proving the enforcement point is mechanical (survives being called directly, not just through
    a well-behaved wrapper).
11. **AC-11 (unit, buildable now):** a fixture with `route="bookkeeping_fast_path"` calls the real
    `nightshift-ctl bookkeeping plan` (or `nightshift_bookkeeping_release.build_bookkeeping_plan`
    directly, against a synthetic bookkeeping-only diff) and asserts the resulting merge path is
    `bookkeeping plan` → `verify-head` → `receipt-record`, with no second/duplicate classifier
    function present anywhere in `bin/nightshift_heartbeat_persistence.py`.
12. **AC-12 (regression, buildable now):** the full existing suite (`python3 tests/run_suite.py`)
    stays green with this change applied, and a targeted test confirms a normal (non-heartbeat)
    implementation PR's path through `cmd_pr_create`/review/QA/staff/exact-SHA/release-provenance
    gates is byte-for-byte unaffected (no new required flag, no new gate) when the
    `--heartbeat-persist-receipt` marker is simply absent from the call.
13. **AC-13 (unit, buildable now):** `heartbeat-persist record` inserts one row per decision into
    the new telemetry table (§2); a test asserts querying it after N synthetic no-op decisions and
    1 synthetic durable-delta decision returns counts matching N and 1 respectively, and that a
    query of "no-op decisions with an associated PR" is always 0 (the invariant AC-13 names,
    verified as a query result, not a prose claim).
14. **AC-14 (follow-up issue — see §7):** for each of #389, #382, #373, #368, #357, #351, #330,
    #326, #317, #284, #255, a fixture `outcome` record is constructed from that PR's actual
    described state (confirmed in §0 to be `program/heartbeat-*` bookkeeping merges) and asserted
    to produce `should_persist_to_git=False` under `decide()`.

---

## 4. Exact file/seam scope

**May touch:**
- `bin/nightshift_heartbeat_persistence.py` (new module — pure decision logic, per §2)
- `bin/nightshift-ctl` — additive only: new `heartbeat-persist {decide, record}` command group,
  new `heartbeat_persist_decisions` table (`CREATE TABLE IF NOT EXISTS`, new migration sequence
  number, same pattern as `bookkeeping_receipts`), and the minimum-necessary gating addition inside
  `cmd_pr_create` (and/or `cmd_checkpoint_create` if the implementer judges it in-path) for the
  code-enforced hook in §2. No existing table altered; no existing command's default behavior
  changed for non-heartbeat callers (AC-12).
- `tests/**` — new `tests/test_heartbeat_persistence.py` (unit, mirrors
  `tests/test_bookkeeping_release.py`'s style: one negative-controlled test per WA-008 if that
  convention still applies at build time) plus any fixture files under `tests/fixtures/**`. Tests
  operate on tmpdir/fixture copies of `.omni/**`-shaped content, never the live files (§2).
- `docs/decisions/**` — a new ADR (crew_alpha authors its own seam's decision per
  `docs/owners.md`'s decisions row, goes to `staff` before build, same gate ADR-0052 went through).
- `docs/CONTROL-PLANE.md` — a new section documenting the `heartbeat-persist` contract, same as
  ADR-0052 added a bookkeeping section.
- `.omni/tech-debt.md` — at most one row naming any deliberately-deferred scope (e.g. "prompt-side
  wiring is program's to do," see below), same convention PR #387 used.

**Must NOT touch:**
- `program/config.yaml` — ROOT policy / ADR-0044's attachment point. Not this item's to edit
  (`docs/owners.md`: "no crew owns this path... unreviewed edit is how a run silently weakens its
  own controls").
- The `program_authority` / `program_action_permits` tables, or `cmd_authority_acquire/renew/
  permit_dispatch/release/show` (`bin/nightshift-ctl:1539-1719`) — #174/ADR-0044-owned. #390 reads
  `authority_status` as an input value already produced by these commands; it adds no new column,
  no new verb, no new argument to any of them.
- The Program's own scheduled-task prompt (only `sys_scheduled_task_update`-capable callers, i.e.
  the program itself, can edit it) — wiring `heartbeat-persist decide` into the actual hourly
  Program turn's prompt is explicitly **not** built here, exactly as ADR-0044 §7's own text
  disclaims for its analogous case. This item ships the CLI/module/enforcement; a `.omni/
  tech-debt.md` row names the prompt-wiring gap and routes it to the program, same pattern ADR-0044
  used.
- `deploy/**` (crew_beta, seam B) — including `deploy/nightshift-heartbeat.sh`, which ADR-0044 §7
  already established is the unrelated 5-minute watchdog-liveness timer, not the Program-turn
  scheduler, and must stay uninvolved.
- `deploy/policies/**` — technically seam-A-owned, but not anticipated to be needed: per ADR-0044
  §3b's own finding, there is no policy hook on `sys_os_shell`/bare CLI invocation, which is why
  ADR-0044 built its second enforcement point *inside* `nightshift-ctl` rather than as a ROOT
  policy. #390 follows the same precedent. If the implementer finds a genuine need to touch
  `deploy/policies/**`, that is a scope change requiring a new DoR contract version, not a silent
  addition.
- `.omni/**` live content — read-only in this item, including in tests (§2's fixture-isolation
  rule).

---

## 5. Relationship to #174 — explicit confirmation

- #390 is **not blocked by** #174's authority-fence cutover (the ROOT-policy live-enforcement
  flip is a separate program/platform action per ADR-0044 §7b's own closing paragraph). #390's
  `decide()` function accepts whatever `authority_status` value the existing, already-shipped
  `authority acquire`/`show` commands report today, cutover or not.
- #390 **may only read** authority/standdown outcomes — via the exit code / body of the existing
  `nightshift-ctl authority acquire`/`show` calls, and via the existing, ADR-0044-§7b-specified
  `nightshift-ctl schedule observe --status standdown ...` call (already shipped, already the
  contract for how a stand-down gets recorded). #390 adds no new authority-read command and
  modifies none of the ones that exist.

---

## 6. Relationship to #286/#388 — explicit confirmation

Confirmed exact surface to delegate to (§2, from reading PR #387's merged code):
`nightshift-ctl bookkeeping plan --task <task> --repo <repo> --head-sha <sha> [--main-sha <sha>]`
→ inspect `fast_path_eligible` → `bookkeeping verify-head` → `bookkeeping receipt-record`. #390's
module contains zero path/content classification logic of its own for the bookkeeping case; it only
decides *whether a Git change is warranted at all* and, if so, whether to route into this existing
fast path or the normal governed path.

---

## 7. Build split recommendation

`size:L` is correctly labeled ("needs splitting"). Recommendation: split into **two** GitHub issues.

**#390 (this item, kept as-is, scope trimmed to the buildable-now slice):**
- `bin/nightshift_heartbeat_persistence.py` (`decide()` + both closed enums)
- `nightshift-ctl heartbeat-persist {decide, record}` + `heartbeat_persist_decisions` table
- Code-enforced hook on `cmd_pr_create` (minimum) per §2
- AC-1, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10, AC-11, AC-12, AC-13 (11 of 14), fully
  mechanically testable today with no live concurrent-Program infrastructure — fixtures and unit
  tests only.
- **Size: M.** Comparable in shape to #286/#387 (new pure module + CLI verbs + one additive table +
  ADR + focused test file — that shipped as `size:S` at ~1540 lines/8 files), but larger here
  because it also modifies an existing high-traffic command's gating (`cmd_pr_create`) rather than
  only adding new verbs, and needs a new ADR authored (not reusing one). M, not L: the module and
  CLI shape are template-following, not novel design.

**New follow-up issue (`#390-b`, title suggestion: "Heartbeat persistence: 24-hour no-op, concurrent-authority, and historical-replay regression fixtures"):**
- AC-2 (24-simulated-heartbeat loop), AC-3 (concurrent-authority/4-heartbeat standdown
  interleaving), AC-14 (11 named historical-PR replay fixtures).
- **Dependency:** blocked-by #390 (needs `decide()`/the enforcement wrapper to exist before it can
  be exercised in a loop/interleaving/replay harness).
- **Size: M.** Not infrastructure-heavy (no live concurrent Program sessions needed — "concurrent"
  here means constructing interleaved fixture `outcome` sequences, not standing up two real Program
  turns), but wider surface: 11 distinct historical fixtures each needing its own reconstructed
  `outcome` record faithful to that PR's actual described state, plus two simulation harnesses.
- Reasoning for splitting rather than keeping one L item: AC-2/AC-3/AC-14 are regression *breadth*
  (prove the invariant holds across many replayed/simulated states) layered on top of AC-1/4-13's
  regression *depth* (prove the decision function and enforcement point are individually correct).
  The first eleven ACs are gating and useful on their own — shipping them lets `cmd_pr_create`
  start refusing bad heartbeat writes immediately — while the breadth suite can follow without
  blocking that value, and reviewing 11+24+4 fixtures in one PR against one contract is a worse
  review unit than two.

---

## 8. Open questions (not blocking — recorded per DoR rule 6)

- Exact final spelling of the `DURABLE_DELTA_REASON_CODES` enum membership beyond the two
  illustrative examples in §2 is left to the implementer, constrained only by "closed set,
  `snake_case`, extended via a PR touching this file, never by string literal at a call site." Not
  blocking: AC-10's test only requires that *some* out-of-enum string is rejected, not a complete
  enumeration up front.
- Whether `cmd_checkpoint_create` also needs the code-enforced hook (§2) in addition to
  `cmd_pr_create`, or whether gating `pr-create` alone is sufficient because every historical
  no-op case in §0 ultimately routed through it — left to the implementer to confirm against the
  actual call graph. Not blocking: AC-10 as written only requires the hook to exist on "at least
  one real mutating surface."
- The `.omni/tech-debt.md` row naming the prompt-wiring gap (§4) is descriptive bookkeeping, not a
  design decision, and does not require sign-off before build starts.

---

## 9. crew_beta cross-crew review follow-ups (PR #397, ADR-0039 marker `67d10c5cedb1f16ef3a95bac9ac42747`)

Two `FOLLOW_UP` (non-blocking) findings from the review, acknowledged here rather than silently
dropped:

1. **`docs/owners.md`'s `.omni/**` row has no explicit carve-out for `.omni/tech-debt.md`.** §4's
   "may touch `.omni/tech-debt.md` (at most one row)" rests on citing PR #387's precedent each
   time, the same gap `docs/audit/**` had before it got its own explicit ownership row
   (2026-08-23). **Deferred, not fixed in this DoR:** `docs/owners.md` is a shared cross-crew map,
   not this issue's file, and editing it is a program/architect-routed maintenance item, not
   something #390's own scope should bundle in. Recommending it forward: the program should open a
   small `docs/owners.md` maintenance item adding an explicit single-append-only-row exception for
   `.omni/tech-debt.md` (and any other file with the same established pattern), citing PR #387 and
   the `docs/audit/**` precedent. Not blocking #390's build — the precedent-citation approach §4
   already uses is accepted practice today, just not yet written into the map itself.
2. **ADR-0052's `Status:` field is still `proposed` on `origin/main` even though its code (PR #387)
   is merged.** This DoR cites ADR-0052 as "the same gate" #390's own new ADR should go through, so
   the inconsistency is worth closing before or alongside #390's build, but it is a pre-existing
   repo-hygiene gap in ADR-0052 itself, not a defect in this DoR (this DoR never claims ADR-0052 is
   accepted). **Deferred, not fixed in this DoR:** flipping ADR-0052's status is crew_alpha's own
   call (crew_alpha authored it) but belongs in a small separate bookkeeping PR against that ADR
   file, not folded into this docs-only DoR PR, which stays scoped to #390. Flagged here so it
   isn't rediscovered as a surprise when #390's own ADR is authored and needs the same gate to
   point at an actually-accepted prior ADR.

---

## Verdict: READY

All six Definition-of-Ready gates hold for the **trimmed #390** (AC-1, AC-4–AC-13 per §7's split):
1. Outcome stated as observable behaviour (§1) — yes.
2. Numbered, testable acceptance criteria, no "properly/correctly/as expected" (§3) — yes, each
   criterion names its test shape and closed-enum check.
3. Failure/empty states specified — yes: unrecognized observation/reason codes, control-plane
   missing (existing `bookkeeping_plan_denied`/`control_plane_missing` pattern reused), and the
   fail-closed default are all named.
4. Out of scope listed explicitly (§4) — yes.
5. Dependencies named (§4, §5, §6) — yes: reads #174's existing surfaces, delegates to #286/#388's
   existing surfaces, touches nothing either owns.
6. Open questions answered or marked non-blocking (§8) — yes.

The **follow-up issue** (§7, AC-2/AC-3/AC-14) should be filed before dispatch of the trimmed #390
so the dependency is visible in the backlog, but it does not block starting the trimmed #390 itself.
