# Flaky tests

Owned by `platform`. A quarantined test is a P0 with a name — never a silent retry.

| Date | Test | Measured failure rate | Quarantined? | Fix issue |
|---|---|---|---|---|

## 2026-08-22 — one unattributed failure in `tests/run.sh`, wave 1
- **Reported by:** `crew_alpha`, during cross-crew review of PR #15 — raised explicitly **not**
  against that PR (the flake is in `crew_alpha`'s own pre-existing tests).
- **Observed:** the first full-suite run at `beta/12` head returned `FAILED (failures=1)`.
  **Not reproducible in 14 subsequent runs**, including three under 8× CPU contention.
  Every other run in this wave, from both crews, was green (93 and 99 tests).
- **Prime suspects:** the sleep-based TTL tests in `tests/test_slots.py` — timing-sensitive,
  seam A, `crew_alpha`'s to own.
- **Why it is recorded rather than chased:** it did not reproduce, so there is nothing to fix
  yet; and a flake that is remembered instead of written down is how a suite's credibility rots.
  DoD condition 15 Q4 requires a flaky inventory, and this is its first entry.
- **Action:** `crew_alpha` owns it. Not wave-1 work. If it recurs, it becomes a fix task ahead
  of new work; if the TTL tests get a deterministic clock in M3, this entry retires with them.
