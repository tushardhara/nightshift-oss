# Charter — wave 1 integration (`platform` as integrator)

**Status: READY TO DISPATCH.** Every gate is green as of 2026-08-23T01:5xZ, and the method below has
been REHEARSED end to end (dry run, slot 19). Corrections from that rehearsal are folded in and
marked ⚑ — the charter as originally written would have mis-gated the run.

## Authority and the hard edge
Under decision **R-11**, `platform` integrates onto **`program/discovery-state` ONLY**. `main` stays
reachable exclusively through `release` (dead — Codex seat, issue #5) via PR #14. This is not a
workaround to be widened: if you find yourself wanting to merge to `main`, stop and return to the
program. Nothing here deploys, migrates a real database, rotates a credential, force-pushes, or
rewrites published history.

## Merge order is a HARD DEPENDENCY (R-14), not a preference
```
#16 → #15 → #18 → #22 → #24
```
- **#24 depends on #16.** The rewritten watchdog calls `nightshift-ctl beat show`, which does not
  exist until #16 lands. Inverted, it does not fail silently — it yields `invalid choice: 'beat'` →
  `alive=0` → **permanent false alerts**. Safe direction, loud enough to be its own incident.
  ⚑ **Verified empirically in rehearsal**, not merely asserted: base `nightshift-ctl` gives
  `invalid choice: 'beat'`; the merged tree has `beat`; watchdog line 92 calls
  `beat show --who program --max-age`. The dependency is real and the stated failure mode is accurate.
- `security` reached the same ordering from the other end: `renew-active` writes `control.db` under
  `.omni/`, and the pre-#24 watchdog gated liveness on `.omni/` mtime — a self-sustaining loop
  exactly in the failure case. **The loop closes only if BOTH #16 and #24 land.**

## Two manual conflicts are EXPECTED — they are not a signal something is wrong
`deploy/lib/deploy-lib.sh` conflicts at **#18** and again at **#22**. All three PRs append at EOF and
keep lines 1-145 identical to base; the **union of the appended tails** is the correct resolution.
⚑ **Rehearsal confirmed this is mechanically safe, and showed why:** lines 1-145 byte-identical to
base on all three appenders; the three appended tails (50 + 113 + 97 lines) collide on nothing —
18 functions, **zero duplicate names, zero globals, no top-level side effects**. Final file is
**405 lines**, an exact concatenation, and `source`s cleanly under `set -euo pipefail`.

⚑ **Verify the resolution byte-exactly rather than eyeballing it.** Deleting the conflict markers by
hand yields 307 lines, not 308 — git collapses one blank separator. Cosmetic, but do not debug it:
```
cat <base deploy-lib.sh> <tail from #15> <tail from #18> <tail from #22> > /tmp/expected.sh
diff /tmp/expected.sh deploy/lib/deploy-lib.sh && bash -n deploy/lib/deploy-lib.sh
```
A conflict here does NOT mean re-open the review. A conflict ANYWHERE ELSE does — stop and report.

## ⚑ THE PASS CRITERION IS 177 TESTS, NOT 144. This is the correction that matters most.
The previous version of this charter said *"Expected: 144 tests OK once all five land"*, citing a
crew_alpha simulation. **That number is wrong and the citation does not survive.**

The rehearsal reproduced exactly where 144 comes from: it is the chain **`#16 → #18 → #24` with #15
and #22 OMITTED**. The arithmetic is exact — 177 − 23 (`test_go_toolchain.py`, #22) − 10
(`test_bwrap_probe.py`, #15) = 144.

**The consequence is worse than the wrong number.** That 3-PR chain hits **zero conflicts** — so the
run cited as validating the `deploy-lib.sh` tail-union *never exercised the tail-union at all*, since
#15's and #22's tails are two of the three being unioned. The resolution was resting on evidence that
could not have tested it. It does hold; the rehearsal is the first thing that actually established
that.

**If you gate on 144 you will either think something double-counted, or — far worse — stop merging
at three PRs, see green, and ship an incomplete chain.**

Three clean rehearsal runs on the real merge result: **177 OK** in 33.5s / 32.1s / 33.2s.

## ⚑ Record the count after EVERY merge, not only at the end
A red at the end does not tell you which merge caused it. Expected per-step counts:
```
after #16 → 111    after #15 → 121    after #18 → 135    after #22 → 158    after #24 → 177
```
A regression then localises immediately to one merge.

## ⚑ FLAKE PROTOCOL — read before interpreting any red run
Issue **#32** (P1, `platform`): the control-plane suite has a **load-sensitive timing flake**.
**A single red run is NOT a verdict. Re-run once and record BOTH results.** Never attribute a flake
to whichever PR you happened to be merging — that bounces a good PR back to its author with nothing
to fix, and it trains the org to re-run until green, which launders a real intermittent defect.

Rehearsal reproduced it deliberately under 8 CPU hogs: `Ran 177 tests`, 1 failure, 58.6s —
`test_recovery.Recovery.test_dead_worker_reaped_slot_claim_run`, `AssertionError: 0 != 1` on
`slots_reaped`. Clean re-run immediately after: 177 OK.

Root cause (do NOT fix during integration): expiry is *lazy and explicit* — every mutating command
calls `expire_stale` first. The test acquires a slot with TTL 0.2, runs two more CLI subprocesses,
sleeps 0.3, then asserts the **explicit** `reap` collected it. Under load the setup gap exceeds 0.2s,
so `claim acquire`'s lazy expiry already reaped it and `reap` correctly returns 0. **Lengthening the
sleep does not fix this** — the race is between slot-acquire and claim-acquire, not around the sleep.
The real fix is injectable time (`NIGHTSHIFT_NOW` / `--now`), which removes all six sleeps and cuts
suite time. **Sequencing: it lands in `bin/nightshift-ctl`, the file #16 rewrites, so it must land
AFTER #16 integrates or it collides.**

## Method
1. ⚑ **Tag the current tip deliberately, with a name, and clean it up afterwards.** The previous
   wording ("tag the current tip") was ambiguous; the rehearsal recorded a SHA instead. For the real
   run a tag is right — name it, and say so in your report.
2. Merge in the order above, one at a time, resolving only the two expected tail-unions, verifying
   each byte-exactly per the command above.
3. ⚑ Record the suite count after **each** merge against the table above.
4. Build the candidate as a **real merge result** and run the FULL suite on it — not each PR's suite
   in isolation. **Expected: 177 OK.** Wall clock ~33s (16.7s at base; the five PRs roughly double it).
5. If the integrated tree goes red, apply the flake protocol FIRST. If it is genuinely red, revert
   your own merge and report which PR did it. A dropped PR becomes a fix task for its authoring crew,
   ahead of new work.
6. Do not rebase the crew branches. crew_beta offered and is holding; if a rebase is genuinely
   needed, ask the program and it will route the request to the owning crew.

## Gate table — ALL GREEN as of 2026-08-23T01:5xZ
| PR | In-crew gates | Cross-crew review | Security | Status |
|---|---|---|---|---|
| #16 | crew_alpha (agreement 5: no in-crew review/QA, stated in PR) | crew_beta: 9/10 MET, contract finding closed at `e1f50cb` | **CLEARED at `8cd0f88`** (delta re-confirm, four commits) | **READY** — tip `8cd0f88` |
| #15 | crew_beta | crew_alpha **APPROVE**, blocker cleared (40-char `baseline_sha`) | n/a | **READY** — tip `287bbe4` |
| #18 | crew_beta | crew_alpha **SIGNED, clean** (cond. 3) | n/a | **READY** — tip `658e6f2` |
| #22 | crew_beta | crew_alpha **APPROVE**, real pre-fix abort captured; RT-23 gap filed as #30 | n/a | **READY** — tip `51a9fdd` |
| #24 | crew_beta | crew_alpha **APPROVE**, successor control named (R-13) | n/a | **READY** — tip `07f728f` |
| #25 | product | crew_beta **CLEAR** at round 2 (`a4231a1`) | n/a | **READY** — docs-only, does not gate the code stack |

**#25 sequencing note:** docs-only, on a separate branch, does not participate in the `deploy-lib.sh`
conflict. It may merge before or after the code stack; it must not gate it.

## Known, accepted, and NOT to be fixed during integration
- **Issue #33** — #24's comment at `:96-101` says `beat show` "exits 1 with EMPTY stdout", which #16
  makes false once the chain lands. The guard itself stays (belt-and-braces on a dead-man's switch).
  Fast-follow; do NOT reopen #24, which carries a cleared review.
- **Issue #30** — RT-23's evidence capture for `BothSitesFixed`. Fast-follow before M2's manifest.
- **Issue #31** — `nightshift-ctl`'s `DEFAULT_DB` is relative. ⚑ The rehearsal found the watchdog
  `cd`s to `PROJECT_DIR` and calls `beat show` **without `--db`**, so it rides directly on this. If
  `PROJECT_DIR` is ever wrong, #31 turns it into a phantom empty DB → `alive=0` → the exact
  false-alert incident R-14 exists to prevent. #24 has a regression test
  (`test_rt09_asks_the_PROJECT_control_plane`), so it is covered today — but #31 and the watchdog are
  one `cd` apart. Fix #31 after the chain lands.

## Do not touch
`RT-11.reviewed_by` stays **absent** until crew_alpha's second look against the two mutant patches —
absent reads as FAIL, a placeholder reads as attested. Do not "tidy" it.
