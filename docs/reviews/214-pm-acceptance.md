# PM acceptance sign-off — issue #214

Work item: GitHub issue #214, "Record task execution start on the first
worker slot". Criteria source: `docs/specs/214-record-task-execution-start.md`
§2 (AC-1..AC-11), mirrored in `docs/reviews/214-record-task-execution-start-CONTRACT.md`
v1. Base SHA: `27f4d188b4882f047541811dae0ce8626b99137e` (Checkpoint 5).
Diff reviewed: `git diff 27f4d188b4882f047541811dae0ce8626b99137e..6d26104`
(branch `feat/214-task-execution-start`).

## Method

Not a review of the prior session's summary. I read the live diff, the new
test file (`tests/test_task_started_at.py`, 7 tests), the 1-line
`tests/test_task_graph.py` fixture diff, `docs/audit/issue-214-baseline-red-and-full-suite.md`,
`docs/decisions/ADR-0043-record-task-started-at.md`, and
`docs/reviews/214-record-task-execution-start-CONTRACT.md`. I ran the
targeted test files and the full suite myself in the worktree
(`/srv/project/.worktrees/214-implement`), independent of the committed
audit doc. Where a criterion's own wording demanded regression-test evidence
that the shipped tests did not provide, I constructed and ran the missing
scenario by hand against the live CLI rather than taking the gap on faith.

## AC-1 — additive nullable column, upsert structurally incapable

**MET.** `ensure_schema`: `bin/nightshift-ctl:580-581` —
`if "started_at" not in tcols: add_column(cx, "tasks", "started_at", "REAL")`,
same guarded shape as the `size`/`rules_digest` precedents (`:572-574`,
`:582-584`). `TASK_FIELDS` (`:186-187`) is byte-for-byte unchanged from base
— grepped, confirmed no `started_at` addition. `cmd_task_upsert`
(`:1531` region) is untouched. `python3 bin/nightshift-ctl task upsert --help`
has no `--started-at` flag (ran it; no match). `test_task_upsert_cannot_set_started_at`
passes: `task upsert --id t-noflag --started-at 123` exits non-zero with no
JSON body.

## AC-2 — set exactly once, inside the existing transaction

**MET.** `begin(cx)` at `bin/nightshift-ctl:922`, the guarded `UPDATE` at
`:1010-1013`, `cx.execute("COMMIT")` at `:1015` — all inside `cmd_slot_acquire`,
same connection, no intervening commit on the path that reaches the write.

## AC-3 — single call site, adjacent to the INSERT

**MET.** `grep -n "UPDATE tasks SET started_at" bin/nightshift-ctl` returns
exactly one hit, `:1012`, immediately after `slot_id = cur.lastrowid`
(`:1000`), which follows the sole `INSERT INTO worker_slots(...)`
(`:991-999`). No other write site exists anywhere in the file.

## AC-4 — fires iff kind='normal' and phase='new'

**MET.** Guard at `bin/nightshift-ctl:1002`:
`if kind == "normal" and phase == "new":`. Confirmed structurally (`phase`
is hard-set to `None` for `kind="oncall"` at `:924`) and empirically: shipped
tests for planning, continuation, and oncall each leave `started_at` NULL;
my own manual runs (AC-7, AC-11 below) reconfirm it under scenarios the
shipped tests don't cover.

## AC-5 — denied grant untouched

**MET.** `granted=False` branch commits and returns at `:976-977`, before
`:991`. `test_denied_grant_leaves_started_at_null` passes, including its own
filler-task control proving the harness can distinguish set from unset.

## AC-6 — reused=True retry untouched

**MET.** `retry is not None` branch commits and returns at `:949-950`,
before `:991`. Traced both `_exact_new_retry` (`:776-793`) and
`_exact_claim_slot_retry` (`:799-817`) — neither returns a path that reaches
the write. `test_reused_retry_leaves_started_at_unchanged` asserts the value
is byte-identical across two acquires of the exact same NEW request, and
passes.

## AC-7 — guard makes exactly-once structural, not phase-singleton-dependent

**PARTIALLY MET.** The guard exists exactly as specified
(`bin/nightshift-ctl:1012`, `AND started_at IS NULL`). AC-7's own text
requires this be "what the regression test must assert," but none of the 7
shipped tests exercises a second genuine (non-retry) `phase='new'` grant for
the same task — `test_reused_retry_...` never reaches the UPDATE a second
time; it short-circuits at the `reused=True` branch (AC-6's path, not
AC-7's). I constructed the missing case by hand: acquired NEW for a task,
released the slot, re-acquired NEW for the same still-pending task
(`reused=False`, `generation` incremented — a genuine second grant, not a
retry): `started_at` was unchanged. **The guard works**, verified live, but
the diff ships no test that would catch a regression of it. Real, citable
coverage gap against the criterion's literal wording.

## AC-8 — existing tests green, zero assertion changes

**MET.** `git diff -- tests/test_task_graph.py` is a 3-line diff, purely the
`tasks_nullable` DDL mirror gaining a 15th column (`started_at REAL`) — no
`assert*` line touched (disclosed by the implementer as finding F-2 in the
CONTRACT doc; correctly classified `FOLLOW_UP`/`resolved`, not silently
claimed as an untouched file). `tests/test_slots.py` has zero diff. I ran
`test_task_started_at`, `test_task_graph`, `test_slots`,
`test_admission_gate`, `test_claims` together: **98/98 OK**. I then ran the
full suite myself: **`Ran 968 tests in 544.364s / OK`**, independently
reproducing the 968/968 claim. Note: the committed
`docs/audit/issue-214-baseline-red-and-full-suite.md`'s own "Full suite,
post-fix" section is truncated — it shows only trailing `rollback:` teardown
noise, not the actual `Ran 968 tests ... OK` line. The number is correct (I
proved it independently), but the artifact meant to be the evidence for it
doesn't itself contain that evidence. Fix before merge; does not block this
sign-off since AC-8 does not name that specific document.

## AC-9 — positive path, red before / green after

**MET.** Baseline-red log: `test_first_new_grant_sets_started_at ... ERROR`
(`sqlite3.OperationalError: no such column: started_at`, pre-fix binary).
Post-fix: `... ok`. Genuine transition, reproduced independently.

## AC-10 — each negative path red before / green after

**PARTIALLY MET.** (a) denied, (b) reused, (c) planning, (d) continuation
all show a genuine `ERROR → ok` transition in the baseline-red/post-fix log
— real regression evidence. (e) **oncall does not**:
`test_oncall_grant_touches_no_task_row` is `... ok` in *both* the pre-fix
and post-fix runs in the audit doc's own log. It never goes red, because the
invariant it checks (oncall never touches the `tasks` table) was already
true before this feature existed — the oncall branch never queries `tasks`
at all, with or without this change. AC-10(e)'s literal requirement ("shown
red before the change... and green after") is not satisfied by the evidence
provided; it is a static assertion, not a demonstrated regression catch. The
underlying behavior is still correct (`phase` is `None` for `kind="oncall"`,
so the `phase=="new"` guard structurally excludes it) — but 4 of 5 sub-cases
meet the red/green bar, the 5th does not.

## AC-11 — stable across later planning/continuation/retry/re-admission

**PARTIALLY MET.** Continuation sub-case: tested and green
(`test_continuation_grant_never_overwrites_started_at`, seeds real admission
via `seed_admission`, takes a QA continuation slot, asserts `started_at`
unchanged). Retry sub-case: tested and green
(`test_reused_retry_leaves_started_at_unchanged`). Planning-after-set and
re-admission sub-cases — the other two shapes the backlog item names
verbatim — have **no test in the diff**. I reproduced both by hand: (1)
re-admission (same run as AC-7) — unchanged; (2) planning after
`started_at` already set — acquired NEW for a task (sets `started_at`),
released the slot, then took a PLANNING slot for the same still-pending task
— `started_at` unchanged. Both hold at runtime, but neither is protected by
a shipped regression test, so this criterion's four-way claim is only half
evidenced by the PR's own artifacts.

## Summary

- **8 of 11 fully MET**, each citing exact lines, backed by independently
  reproduced test runs: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8, AC-9.
- **3 PARTIALLY MET**: AC-7, AC-10, AC-11. In every case the *behavior* is
  correct — verified by hand where the shipped suite didn't cover it — but
  the *regression-test evidence the criteria themselves demand* is
  incomplete: no test for a second genuine `phase='new'` grant (AC-7), no
  red-before for the oncall negative case (AC-10(e)), no test for
  planning-after-set or re-admission (AC-11, 2 of 4 named shapes).
- No criterion is met-differently-than-specified or outright not-met.
  Nothing here requires a new contract version — this is a coverage gap in
  the shipped tests, not a design deviation from the frozen spec.

**Overall verdict: PASS-WITH-NOTES.** The implementation satisfies the
backlog item's observable behavior on every criterion, confirmed either by
the diff's own tests or by my independent reproduction. It should not be
blocked on the gaps above, but they should not be silently dropped either.

**Recommended before merge (not a re-open of DoR, not a blocker for this
sign-off):**
1. Add one test that forces a second genuine (non-retry) NEW grant on a
   released-then-reacquired task and asserts `started_at` unchanged — closes
   AC-7 on the diff's own terms.
2. Add a planning-after-set case to `test_task_started_at.py` — closes half
   of AC-11's gap; re-admission is covered by the same test as (1).
3. Fix `docs/audit/issue-214-baseline-red-and-full-suite.md`'s truncated
   "Full suite, post-fix" section so it actually contains the
   `Ran 968 tests ... OK` line it claims to.

**Process note, outside PM's lane but surfaced for the record:**
`docs/reviews/214-record-task-execution-start-CONTRACT.md` marks cross-crew
review `[x]` mandatory (WA-5), but its "Scoped attestations" table is empty
— no reviewer, no verdict recorded as of this sign-off. This PM sign-off
covers acceptance criteria only; the contract's own required-domains table
still needs the mandatory cross-crew review before merge.

**Tech-debt cross-reference:** the dead `task_admissions` INSERT path
flagged as explicitly out of scope in `docs/specs/214-record-task-execution-start.md`
§4 is the same finding recorded at `.omni/tech-debt.md:18` (repo root,
2026-08-29 row, "`nightshift-ctl slot consume` is a dead half of the
admission write ADR-0033 requires... It never inserts into
`task_admissions`..."), explicitly attributed there to "crew_alpha's pm
during the #214 DoR trace." Confirmed the same finding, not a duplicate.

---

## Addendum (crew_alpha lead, 2026-08-29) — staff MERGE_BLOCKER, not a PM finding

Nothing above (pm's own sign-off) is edited. For the record: staff's ADR-0043
review found the `started_at` write's *location* inconsistent with ADR-0033
(reservation vs. real admission) and, in the course of that review, corrected
a false claim in `docs/specs/214-record-task-execution-start.md` §4 ("admission
is dead code never populated in production" — wrong; `worker_semaphore` does
populate it). This is unrelated to the tech-debt cross-reference pm made
above (the real, separate, still-valid dead-code gap is `nightshift-ctl slot
consume`, not `worker_semaphore`). Fixed, amended, and re-tested — see
ADR-0043's amendment section and the frozen contract's F-4 finding. Does not
change or invalidate any of PM's AC-1..AC-11 verdicts above; PM should
re-confirm AC-2/AC-3 against the new location before final sign-off closes.

---

# Re-attestation: AC-2 / AC-3 against relocated write (2026-08-29, commit `d845d1b` / contract v2 `ad1d05c`)

Prompted by crew_beta's round-2 review finding **CB-1**: the PR addendum
committed to re-confirming AC-2/AC-3 against the new location before final
sign-off closes. This section is appended, not edited into the original
verdicts above, per the frozen-record convention.

## Context

Commit `d845d1b` relocated the guarded `UPDATE tasks SET started_at=...`
from `cmd_slot_acquire`'s RESERVATION transaction (`bin/nightshift-ctl`) to
`worker_semaphore`'s ADMISSION transaction
(`deploy/policies/omnipods_policies.py`), fixing a staff-found
`MERGE_BLOCKER` (F-4 in the contract): a granted-but-never-consumed
reservation could permanently pin the wrong (early) `started_at` because the
guard would then refuse the correct, later write. `docs/reviews/214-record-task-execution-start-CONTRACT.md`
was subsequently bumped to **v2** (commit `ad1d05c`) to correct the AC-2/AC-3
citations. This re-attestation references contract v2, confirmed present at
both HEAD and `origin/feat/214-task-execution-start` (`ad1d05c`) at the time
of writing — v1's citations are stale and superseded.

## Method

Did not trust either the crew's summary or the contract's own v2 text as
given. Read the live `deploy/policies/omnipods_policies.py` and
`bin/nightshift-ctl` at commit `d845d1b`/`ad1d05c` directly, independently
derived the line numbers below before comparing them to contract v2's
citations (they matched), and ran the relevant tests myself rather than
trusting the commit message's claimed counts.

## AC-2 re-verification

**MET, at the corrected location.** `worker_semaphore` opens one connection
and one transaction per call (`cx.execute("BEGIN IMMEDIATE")` at
`deploy/policies/omnipods_policies.py:241`) and commits exactly once on the
success path (`cx.execute("COMMIT")` at `:415`). Inside the
`if row["phase"] == "new":` block (`:385`), the real admission writes —
`INSERT INTO task_admissions` (`:387-391`) and
`UPDATE tasks SET state='building'` (`:393-395`) — and the guarded
`UPDATE tasks SET started_at=? WHERE id=? AND started_at IS NULL`
(`:411-413`) all execute on the same `cx`, before the single `COMMIT`
(`:415`). No separate connection, no separate transaction. Any raised
`ValueError` anywhere in the block goes to `ROLLBACK` (`:417`), not
`COMMIT` — confirmed live by `test_expired_unconsumed_reservation_then_later_genuine_admission`
(`tests/test_task_started_at.py`), which forces exactly this path (a DENY on
an expired, unconsumed reservation) and asserts `started_at` stays NULL and
no `task_admissions` row was written. Ran it: **passes.**

`bin/nightshift-ctl`'s `cmd_slot_acquire` no longer contains any
`started_at` write — confirmed by `grep -n "started_at" bin/nightshift-ctl`,
which returns only the schema/migration lines (`:277`, `:580-581`, an
unrelated `agent_runs.started_at` column at `:2170`) and an explanatory
comment left in the reservation's former write site (`:1002-1013`)
documenting the relocation and why. Zero executable `started_at` writes
remain in that file.

**Note on the spec's own §2 text:** `docs/specs/214-record-task-execution-start.md`
§2's AC-2 wording (as committed) still literally reads *"executed inside
`cmd_slot_acquire`'s existing single transaction... between its `begin(cx)`
(`bin/nightshift-ctl:916`) and its `cx.execute("COMMIT")` (`bin/nightshift-ctl:993`)"*
— this is now stale/superseded. Only §4 was amended with a correction note
pointing at the ADR amendment; §2's criterion text itself was not rewritten.
Contract v2 correctly supersedes it (see the v2 AC-2 row, which explicitly
flags "v1 wrongly named `cmd_slot_acquire`'s reservation transaction"), so
this doesn't block sign-off, but the spec's §2 should get the same literal
correction contract v2 already has, for anyone who reads the spec instead of
the contract.

## AC-3 re-verification

**MET, at the corrected location.** `grep -n "started_at" deploy/policies/omnipods_policies.py`
returns exactly one write site: `:411-413`
(`UPDATE tasks SET started_at=... AND started_at IS NULL`), immediately
following the `task_admissions` INSERT (`:387-391`) inside the same
`if row["phase"] == "new":` block (`:385`) — the only call site in the
entire codebase that fires for a genuinely new admission. Combined with the
AC-2 grep above (zero write sites left in `bin/nightshift-ctl`), the write
appears exactly once, system-wide.

**Same spec-staleness note as AC-2 applies to AC-3's §2 text** — it still
names `INSERT INTO worker_slots(...)` (`bin/nightshift-ctl:985-992`) as the
adjacency target, which is now the RESERVATION write, not the ADMISSION
write the criterion actually cares about. Contract v2's AC-3 row already
carries the correct text; the spec's §2 does not yet mirror it.

## Tests re-run (not just read)

```
tests/test_task_started_at.py    10/10 OK  (includes test_expired_unconsumed_reservation_then_later_genuine_admission)
tests/test_admission_gate.py     36/36 OK  (includes 3 new tests added by d845d1b, e.g.
                                            test_denied_or_abandoned_new_reservation_never_poisons_started_at,
                                            test_continuation_never_touches_started_at)
tests/test_task_graph.py, tests/test_slots.py, tests/test_claims.py — all green, zero diff beyond the
  already-disclosed F-2 DDL-mirror line
```
Combined run (`test_task_started_at test_admission_gate test_task_graph
test_slots test_claims`): **103/103 OK**.

## Incidental observation (not scored today — CB-1 asked for AC-2/AC-3 only)

The same commit's new tests appear to also close most of my earlier F-3
coverage gaps as a side effect: `test_planning_denied_once_task_is_genuinely_admitted`
and `test_re_admission_after_reset_to_pending_does_not_overwrite_started_at`
directly cover the two AC-11 sub-cases (planning-after-set, re-admission)
I previously found untested and had to verify by hand.
`test_re_admission_after_reset_to_pending_does_not_overwrite_started_at`
also exercises a second genuine (non-retry) admission for the same task,
closing AC-7's gap. AC-10(e)'s oncall sub-case still cannot show a red
baseline (structurally impossible, as before) but is now explicitly
reasoned about in the test's own docstring rather than left silent. I have
not re-run the full v1 F-3 finding as a formal re-attestation — this is an
observation for the record, not today's verdict. If the record needs it
closed formally, ask separately.

## Verdict

**AC-2: MET** (against the corrected, contract-v2 location).
**AC-3: MET** (against the corrected, contract-v2 location).

**Overall verdict: unchanged, PASS-WITH-NOTES.** The relocation is a
genuine fix (closes a real MERGE_BLOCKER), not a new deviation from the
frozen criteria, so it does not reopen the acceptance question — it answers
CB-1's specific ask. The one new note this re-attestation adds: the spec's
own §2 AC-2/AC-3 text (`docs/specs/214-record-task-execution-start.md`)
should be corrected to match contract v2's wording — right now only the
contract carries the accurate location, and the spec is the document listed
as "the review contract of record" in its own header.
