# Issue #214 -- baseline-red / post-fix evidence

Base sha: 27f4d188b4882f047541811dae0ce8626b99137e (Checkpoint 5)
Pre-fix binary: git show HEAD~1:bin/nightshift-ctl (the docs-only freeze commit, i.e. base_sha unmodified)

## Baseline red (NIGHTSHIFT_TEST_CTL pinned to the pre-fix binary)
```
test_continuation_grant_never_overwrites_started_at (test_task_started_at.TaskStartedAt.test_continuation_grant_never_overwrites_started_at)
AC-4 / AC-10(d) / AC-11. Seed real admission (state=building) on ... ERROR
test_denied_grant_leaves_started_at_null (test_task_started_at.TaskStartedAt.test_denied_grant_leaves_started_at_null)
AC-10(a). Control: the filler task's own NEW grant DOES set ... ERROR
test_first_new_grant_sets_started_at (test_task_started_at.TaskStartedAt.test_first_new_grant_sets_started_at)
AC-9 (positive path). ... ERROR
test_oncall_grant_touches_no_task_row (test_task_started_at.TaskStartedAt.test_oncall_grant_touches_no_task_row)
AC-4 / AC-10(e). oncall grants are not task-bound at all; assert ... ok
test_planning_grant_never_sets_started_at (test_task_started_at.TaskStartedAt.test_planning_grant_never_sets_started_at)
AC-4 / AC-10(c). A PLANNING slot is only reachable for a ... ERROR
test_reused_retry_leaves_started_at_unchanged (test_task_started_at.TaskStartedAt.test_reused_retry_leaves_started_at_unchanged)
AC-6 / AC-10(b). The exact same NEW request twice must return ... ERROR
test_task_upsert_cannot_set_started_at (test_task_started_at.TaskStartedAt.test_task_upsert_cannot_set_started_at)
AC-1. There is no --started-at flag; task upsert stays ... ok

======================================================================
ERROR: test_continuation_grant_never_overwrites_started_at (test_task_started_at.TaskStartedAt.test_continuation_grant_never_overwrites_started_at)
AC-4 / AC-10(d) / AC-11. Seed real admission (state=building) on
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 98, in test_continuation_grant_never_overwrites_started_at
    v0 = started_at(c, "t-cont")
         ^^^^^^^^^^^^^^^^^^^^^^^
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 22, in started_at
    row = cx.execute(
          ^^^^^^^^^^^
sqlite3.OperationalError: no such column: started_at

======================================================================
ERROR: test_denied_grant_leaves_started_at_null (test_task_started_at.TaskStartedAt.test_denied_grant_leaves_started_at_null)
AC-10(a). Control: the filler task's own NEW grant DOES set
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 45, in test_denied_grant_leaves_started_at_null
    self.assertIsInstance(started_at(c, "filler"), float)  # control
                          ^^^^^^^^^^^^^^^^^^^^^^^
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 22, in started_at
    row = cx.execute(
          ^^^^^^^^^^^
sqlite3.OperationalError: no such column: started_at

======================================================================
ERROR: test_first_new_grant_sets_started_at (test_task_started_at.TaskStartedAt.test_first_new_grant_sets_started_at)
AC-9 (positive path).
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 36, in test_first_new_grant_sets_started_at
    v = started_at(c, "t-start-1")
        ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 22, in started_at
    row = cx.execute(
          ^^^^^^^^^^^
sqlite3.OperationalError: no such column: started_at

======================================================================
ERROR: test_planning_grant_never_sets_started_at (test_task_started_at.TaskStartedAt.test_planning_grant_never_sets_started_at)
AC-4 / AC-10(c). A PLANNING slot is only reachable for a
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 88, in test_planning_grant_never_sets_started_at
    self.assertIsNone(started_at(c, "t-plan"))
                      ^^^^^^^^^^^^^^^^^^^^^^^
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 22, in started_at
    row = cx.execute(
          ^^^^^^^^^^^
sqlite3.OperationalError: no such column: started_at

======================================================================
ERROR: test_reused_retry_leaves_started_at_unchanged (test_task_started_at.TaskStartedAt.test_reused_retry_leaves_started_at_unchanged)
AC-6 / AC-10(b). The exact same NEW request twice must return
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 58, in test_reused_retry_leaves_started_at_unchanged
    v1 = started_at(c, "t-retry")
         ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/srv/project/.worktrees/214-implement/tests/test_task_started_at.py", line 22, in started_at
    row = cx.execute(
          ^^^^^^^^^^^
sqlite3.OperationalError: no such column: started_at

----------------------------------------------------------------------
Ran 7 tests in 2.563s

FAILED (errors=5)
```

## Post-fix (this branch's bin/nightshift-ctl)
```
test_continuation_grant_never_overwrites_started_at (test_task_started_at.TaskStartedAt.test_continuation_grant_never_overwrites_started_at)
AC-4 / AC-10(d) / AC-11. Seed real admission (state=building) on ... ok
test_denied_grant_leaves_started_at_null (test_task_started_at.TaskStartedAt.test_denied_grant_leaves_started_at_null)
AC-10(a). Control: the filler task's own NEW grant DOES set ... ok
test_first_new_grant_sets_started_at (test_task_started_at.TaskStartedAt.test_first_new_grant_sets_started_at)
AC-9 (positive path). ... ok
test_oncall_grant_touches_no_task_row (test_task_started_at.TaskStartedAt.test_oncall_grant_touches_no_task_row)
AC-4 / AC-10(e). oncall grants are not task-bound at all; assert ... ok
test_planning_grant_never_sets_started_at (test_task_started_at.TaskStartedAt.test_planning_grant_never_sets_started_at)
AC-4 / AC-10(c). A PLANNING slot is only reachable for a ... ok
test_reused_retry_leaves_started_at_unchanged (test_task_started_at.TaskStartedAt.test_reused_retry_leaves_started_at_unchanged)
AC-6 / AC-10(b). The exact same NEW request twice must return ... ok
test_task_upsert_cannot_set_started_at (test_task_started_at.TaskStartedAt.test_task_upsert_cannot_set_started_at)
AC-1. There is no --started-at flag; task upsert stays ... ok

----------------------------------------------------------------------
Ran 7 tests in 3.518s

OK
```

## Full suite, post-fix
```
rollback: /tmp/tmpiugvv0nb/head/release/bin/nightshift-release rollback --plan /tmp/tmpiugvv0nb/head/plan.json --owner-go dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
rollback: /tmp/tmpiugvv0nb/tree/release/bin/nightshift-release rollback --plan /tmp/tmpiugvv0nb/tree/plan.json --owner-go dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
rollback: /tmp/tmpfr1d1x3g/release/bin/nightshift-release rollback --plan /tmp/tmpfr1d1x3g/plan.json --owner-go dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
rollback: /tmp/tmpkj5xfv2o/release/bin/nightshift-release rollback --plan /tmp/tmpkj5xfv2o/plan.json --owner-go dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
rollback: /tmp/tmp_t_j_k64/release/bin/nightshift-release rollback --plan /tmp/tmp_t_j_k64/plan.json --owner-go dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
```

## Amendment (staff MERGE_BLOCKER, 2026-08-29): relocation red/green

The write moved from `cmd_slot_acquire` (RESERVATION) to `worker_semaphore`'s
`phase == "new"` branch (ADMISSION) — see ADR-0043's amendment section and
review-contract finding F-4. `tests/test_task_started_at.py` was rewritten to
drive real admission through `omnipods_policies.worker_semaphore`, matching
`test_admission_gate.py`'s convention, and gained two tests: the staff-requested
reservation-expires-then-later-genuine-admission regression, and the two other
AC-11 sub-cases PM's acceptance sign-off found untested (planning-after-set,
re-admission-after-reset).

### RED — same binary that shipped before this amendment (`/tmp/prefix-214-v2/nightshift-ctl`, the pre-amendment `cmd_slot_acquire`), run against the amended test file
```
$ NIGHTSHIFT_TEST_CTL=/tmp/prefix-214-v2/nightshift-ctl python3 -m unittest test_task_started_at -v
test_continuation_grant_never_overwrites_started_at ... ok
test_denied_grant_leaves_started_at_null ... ok
test_expired_unconsumed_reservation_then_later_genuine_admission ... FAIL
test_first_new_admission_sets_started_at ... FAIL
test_oncall_grant_touches_no_task_row ... ok
test_planning_denied_once_task_is_genuinely_admitted ... ok
test_planning_grant_never_sets_started_at ... ok
test_re_admission_after_reset_to_pending_does_not_overwrite_started_at ... ok
test_reused_new_reservation_never_sets_started_at ... FAIL
test_task_upsert_cannot_set_started_at ... ok

FAIL: test_expired_unconsumed_reservation_then_later_genuine_admission
    self.assertIsNone(started_at(c, "t-late-admit"))
AssertionError: 1787994767.9588425 is not None

FAIL: test_first_new_admission_sets_started_at
    self.assertIsNone(started_at(c, "t-start-1"))
AssertionError: 1787994768.4350557 is not None

FAIL: test_reused_new_reservation_never_sets_started_at
    self.assertIsNone(started_at(c, "t-retry"))
AssertionError: 1787994771.119787 is not None

Ran 10 tests in 5.225s
FAILED (failures=3)
```
Confirms: the pre-amendment code set `started_at` at bare-reservation time
(before any admission), exactly the behavior staff flagged as inconsistent
with ADR-0033. The 7 other cases were already correct under the old location
and remain green — this amendment only relocates the write, it does not
change any other behavior.

### GREEN — this branch's code (write relocated to `worker_semaphore`)
```
$ python3 -m unittest test_task_started_at -v
Ran 10 tests in 5.201s
OK
```

### GREEN — related suites, same run
```
$ python3 -m unittest test_task_started_at test_task_graph test_slots test_admission_gate test_claims test_policy -v
Ran 115 tests in 85.043s
OK
```

### GREEN — full suite re-run after the amendment
```
$ ./tests/run.sh
Ran 973 tests in 626.238s
OK
```
