# Runbook: cutting ADR-0044 program-authority fencing (`program_authority_fence`) over into the live-served policy

Status: **draft — not yet staff-reviewed. Do not execute.** This document is a procedure only; it
performs no cutover by itself, and no step in it should be run until this runbook has cleared
`staff` review and the human owner has explicitly scheduled a window (see §7).

Refs: ADR-0044 (`docs/decisions/ADR-0044-program-authority-fencing.md`, §§1–7b), issue #174,
`.omni/decisions.md` ~12:40Z entry ("PR #311 is safe to MERGE but must NOT be CUT OVER until §7
... exists"), `deploy/EXACT-SHA-CUTOVER.md` (the general exact-SHA release mechanism this runbook
layers on top of), `deploy/exact_sha_release.py`, issue #107 (served-vs-committed drift — the
reason "merged to `main`" and "live" are different events here).

## 0. What this is, and what it is not

`program_authority_fence` (`program/config.yaml`'s `guardrails.policies` block, wired via PRs
#304/#311/#315/#321) is already merged to `main`. **Merging does not activate it.** The live
Omnigent server reads `program/config.yaml` and `deploy/policies/**` only from whatever exact-SHA
checkout `deploy/exact_sha_release.py` last cut it over to (issue #107). This runbook is the
authority-fence-specific delta on top of the general, already-documented exact-SHA cutover
procedure in `deploy/EXACT-SHA-CUTOVER.md` — read that document first; this one does not repeat
its mechanics, only what's specific to activating this one policy safely.

**The two-piece precondition, named explicitly because it is easy to miss:** activating the fence
requires two independent things to both be true at once:

1. The served release's `program/config.yaml` has `program_authority_fence` attached (git-tracked,
   cut over via the mechanism in §4 below).
2. The Program scheduled-task's own **prompt text** — which lives in Omnigent's scheduler state,
   is edited only via `sys_scheduled_task_update`, and is *not* part of any git checkout or this
   release mechanism at all — already implements ADR-0044 §7b's acquire → renew → digest →
   permit-dispatch → dispatch → release/standdown sequence.

If (1) goes live while (2) is not yet true, **every** Program dispatch to every direct-child seat
(`product, architect, crew_alpha, crew_beta, ux, staff, platform, security, release, oncall`) is
denied from the first post-cutover heartbeat turn onward, because nothing ever calls `authority
acquire` and the policy fails closed on "no live Program authority holds the fence"
(`deploy/policies/omnipods_policies.py:519-524`). This is the self-lockout scenario named in the
ask, and it is **not** rescued by `NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1` — see §6.

As of writing, `.omni/decisions.md` and `.omni/STATUS.md` contain **no record** that the scheduled
task's prompt has actually been edited for §7b. Pre-check 1(b) below is a hard blocker on this, and
it must be verified live, not assumed from a decision-log entry.

## 1. Roles

- **Owner** — the human project owner (git identity `ci` / `owner@example.invalid` in this
  repo's convention; ADR-0013 assigns "outage authority" to this role explicitly, not to any
  agent seat). Only the owner (or a designated, reachable, human on-call standing in for them)
  may supply `--owner-go` to `cutover` or `rollback`, and only the owner drives §5's live
  verification and makes the go/no-go call in §6.
- **Operator** — whoever has host shell access to run `nightshift-release`; may be the owner
  directly, or a human from `release`/`platform` acting under the owner's direct, live
  supervision for this window. Not a Program/crew *agent turn* — see §7 for why.
- No step past `prepare`/`authorize` in this runbook is something a scheduled heartbeat turn
  should ever run autonomously.

## 2. Pre-checks — run all of these before `prepare`

**(a) Chain is fully merged.** Confirm on `main`:
```
git log --oneline --all | grep -E "^\w+ Merge pull request #(304|311|315|321)"
```
All four present, plus the §7b doc content in `docs/decisions/ADR-0044-program-authority-fencing.md`
("Revision note (2026-08-29, round 3) — `174-implement-wiring`, issue #319" — Decision §7b).
Pick the candidate SHA (`--sha` for `prepare`) as a commit on `main` at or after all four merges.

**(b) §7b integration contract is actually being followed — HARD BLOCKER, verify live.**
Fetch the *current* Program heartbeat scheduled-task prompt (`sys_scheduled_task_list`, or
`GET {server}/v1/scheduled-tasks/{id}` — the same read `deploy/exact_sha_release.py:836-841`
uses) and read its literal text. Confirm it contains, in order: an `nightshift-ctl authority
acquire` call at turn start with an exit-3 stand-down path that performs zero dispatch and writes
`schedule observe --status standdown`; a renew-before-every-dispatch-or-every-10-minutes check;
an `authority digest` → `authority permit-dispatch` → dispatch sequence immediately before every
`sys_session_send`/`sys_session_create` to a direct-child seat; and an `authority release` at
clean turn end. This is ADR-0044 §7b's five-step sequence verbatim — do not accept a decision-log
entry *claiming* this was done as a substitute for reading the live prompt text itself.
**If this is not present in the live prompt, stop. Do not proceed to §4.** Editing the prompt is
Program's own job (only Program can call `sys_scheduled_task_update`) and is out of scope for this
runbook.

**(c) No stale/conflicting authority state.**
```
nightshift-ctl authority show
```
Expect `{"active": false}`. If `active: true` with a `holder_label` you don't recognize as a live,
legitimate run, stop and investigate before proceeding — a stale row (e.g. left over from a prior
test run against the same control DB) would deny the very first real acquire after cutover and
look identical to a genuine lockout.

**(d) No cutover already in flight.** Check for an existing, unresolved
`/srv/nightshift-release-state/<sha>/cutover-attempt.json` for any candidate SHA before starting a
new `prepare`.

**(e) Confirm this is a first-time activation.** Read the last accepted cutover's `receipt.json`
(or `nightshift-ctl dump`'s `spec.deployed` vs `spec.expected`, issue #107's own diagnostic) and
confirm the currently-served release does **not** already have `program_authority_fence` enforced,
so you know which direction §5 should read.

**(f) This runbook and ADR-0044 both carry a recorded `staff` review verdict** before scheduling a
window — per the standing decision this runbook exists to satisfy.

**(g) A reachable human owner/on-call is committed to the entire window**, start to finish,
without stepping away — required because of the self-lockout blast radius (§7).

## 3. Where this deviates from ADR-0044's own draft framing

ADR-0044 §7b's closing note says the git-side cutover and the prompt edit are "not performed here"
by that ADR. This runbook is the missing piece for the git-side half only (§4); the prompt edit
(pre-check 2b) is not something this runbook performs and must already be independently true.

## 4. Cutover command sequence

This is the standard `deploy/exact_sha_release.py` prepare → authorize → cutover sequence
documented in full in `deploy/EXACT-SHA-CUTOVER.md`. There is **no separate "config sync" step**:
`program/config.yaml` and `deploy/policies/**` are inside the whole-tree checkout this mechanism
seals and swaps in atomically, so running this sequence against a SHA that includes ADR-0044
§3/§3b **is** the config-sync step. Run as the non-root Nightshift runtime user.

```bash
# 1. Prepare — read-only w.r.t. the live server; materialises and seals a checkout.
/path/to/reviewed/checkout/bin/nightshift-release prepare \
  --source https://github.com/tushardhara/nightshift-oss.git \
  --sha <40-hex-sha-on-main-including-PRs-304-311-315-321> \
  --floor-sha <current release floor sha, per the last accepted receipt> \
  --scheduled-task-id <heartbeat-task-id>
# Prints: plan path, plan digest, forward/rollback command lines.
# LIVE STATE UNCHANGED at this point.

# 2. Pause the heartbeat scheduler through its API (same step EXACT-SHA-CUTOVER.md
#    documents) and let any in-flight Program turn finish naturally. Do not use
#    `omnigent stop`.

# 3. Authorize — read-only quiescence gate, expires after 10 minutes.
/srv/nightshift-releases/<sha>/checkout/bin/nightshift-release authorize \
  --plan /srv/nightshift-release-state/<sha>/plan.json
# Refuses on: active slots/claims/leases, active Omnigent sessions, a live (non-paused)
# scheduler, a changed control-DB inode, a dirty/writable release, sealed Program/policy
# digest tamper, Program registration/version drift, admission-schema/legacy-slot state,
# or a changed heartbeat prompt since `prepare` (deploy/exact_sha_release.py:1120-1219).
# Prints the EXACT cutover command, including the plan digest as --owner-go.
#
# BEFORE running step 4: have the §5 Step 0 re-pause command for the heartbeat
# scheduler already typed and ready to submit in a second terminal. `cutover`
# resumes the scheduler itself as its last act (see note below) — there is a real
# race between that resume and you reacting to the ACCEPTED line, and a live
# Program turn can start inside that gap. Do not wait to read the full `cutover`
# output before submitting the pause.

# 4. Cutover — the OWNER (§1), and only the owner, copies and runs the printed line
#    verbatim. Nobody recomputes --owner-go by hand. A second check *during*
#    cutover re-verifies the server/host/runner PID epochs, sealed-tree state, and
#    control-plane quiescence against what `authorize` captured, and refuses
#    (`_fresh_cutover_gate`, deploy/exact_sha_release.py:1495-1529) if anything
#    drifted in between — this is where PID-epoch reuse is actually caught, not
#    in `authorize` itself.
/srv/nightshift-releases/<sha>/checkout/bin/nightshift-release cutover \
  --plan /srv/nightshift-release-state/<sha>/plan.json \
  --authorization /srv/nightshift-release-state/<sha>/authorization.json \
  --owner-go <plan_digest_printed_by_authorize>
```

`cutover` has its own built-in safety net independent of §5: all seven `CORE_PROOFS`
(`registered`, `served`, `content_at_sha`, `enforced`, `runtime_environment`, `executed`,
`durable_continuity`) must come back green in the same receipt or `cutover` automatically calls
`_rollback_live` and refuses (`deploy/exact_sha_release.py:2380-2388`). Note that `enforced` here
proves `release_provenance_canary` is loaded from the sealed module — it does **not** prove
`program_authority_fence` behaves correctly. That is what §5 is for, and it is authority-specific,
not covered by the receipt.

Also note: `cutover`'s own `scheduler_continuity` proof **resumes the heartbeat scheduler as part
of accepting the release**, before you get to run anything in §5. See §5 step 0.

## 5. Live verification — authority-fence-specific, run immediately after `cutover` prints `ACCEPTED`

The general receipt from §4 does not exercise `program_authority_fence` at all. This step does,
against the actual serving process, using the same `POST /v1/sessions/{id}/policies/evaluate`
mechanism `deploy/exact_sha_release.py`'s own `enforced` proof already uses
(`deploy/exact_sha_release.py:1872-1926`; request-body shape confirmed against
`tests/probe_per_seat_policy.py:291-306`, response-field usage — `resp["result"]` /
`POLICY_ACTION_ALLOW`/`POLICY_ACTION_DENY` — confirmed at `tests/probe_per_seat_policy.py:392-407`
and `tests/test_exact_sha_release.py`'s matching fixture). This evaluates the real, loaded policy
stack for a **synthetic** tool-call event — it does not actually dispatch anything, so it never
spawns a real session against `crew_alpha` or any other seat.

**Fix the verification title once, before Step A, and reuse it literally everywhere below —
do not regenerate a fresh timestamp at each step.** Step A's synthetic event and Step B's digest
must be computed over the byte-identical `{agent, title, args}` tuple or the digest legitimately
won't match, producing a spurious DENY that looks exactly like a broken fence:
```bash
VERIFY_TITLE="cutover-verify-$(date -u +%FT%TZ)"
```
Use `$VERIFY_TITLE` verbatim in every event/digest call in Steps A and B below.

**Step 0 — close the race window.** `cutover` resumes the heartbeat scheduler as its own last
act (§4's `scheduler_continuity` proof) — this happens automatically, before you can react to the
`ACCEPTED` line. Submit the pre-staged re-pause command (per §4's note, same API as §4 step 2)
the instant `cutover` returns, before reading the rest of its output. Resume the scheduler again
only after Step C confirms clean release.

**Step A — confirm the natural DENY state.**
```
nightshift-ctl authority show   # expect {"active": false}
```
Open one throwaway session against the just-registered Program agent id (from the cutover
receipt's `registered` proof operands), then:
```
POST {server}/v1/sessions/{session_id}/policies/evaluate
{"event": {"type": "PHASE_TOOL_CALL", "data": {"name": "sys_session_send",
  "arguments": {"agent": "crew_alpha", "title": "<VERIFY_TITLE>",
                "args": {"purpose": "explore"}}}}}
```
Expect `result: POLICY_ACTION_DENY`, reason containing "no live Program authority holds the
fence". If this instead comes back ALLOW, **stop — the fence is not enforcing. Go to §6
immediately.** (`rollback` itself will restore the scheduler to its pre-cutover state
automatically — see §6 — so there is nothing to manually resume from Step 0's pause first.)

If `authority show` already reports `active: true` with a holder_label that looks like a real
Program run/session id (not one you set) — this means Step 0's re-pause lost its own race, and
a real turn is already exercising the fence live. **Do not** try to force an acquire over it.
Confirm via session/PR activity that this turn's dispatches are actually landing, then resume the
scheduler (it was only ever meant to be paused for the duration of this synthetic check, not
indefinitely) and defer Steps A–C's synthetic check to the next quiescent window rather than
leaving the heartbeat paused by omission.

**Step B — confirm a genuinely permitted call is ALLOWed, and is one-shot.**
```bash
RESP=$(nightshift-ctl authority acquire --holder-label "cutover-verify-$(date -u +%FT%TZ)" --ttl 120)
TOKEN=$(echo "$RESP" | jq -r .token)
GEN=$(echo "$RESP" | jq -r .generation)
DIGEST=$(nightshift-ctl authority digest --tool sys_session_send --agent crew_alpha \
  --title "$VERIFY_TITLE" --args-json '{"purpose":"explore"}' | jq -r .digest)
echo "$TOKEN" | nightshift-ctl authority permit-dispatch --generation "$GEN" \
  --digest "$DIGEST" --token-stdin --ttl 60
```
(The `--holder-label` timestamp above is just a diagnostic label and may be fresh — it is not part
of the digest. `$VERIFY_TITLE` is the value that must stay fixed.)

Re-issue the **exact same** synthetic event from Step A (identical agent/`$VERIFY_TITLE`/args, so
the digest matches) through `/policies/evaluate`. Expect `POLICY_ACTION_ALLOW`. Issue the
identical event a second time: expect `POLICY_ACTION_DENY` — proves the permit is genuinely
one-shot, not fail-open.

**Step C — release immediately. Do not skip. Do not use a longer TTL "to be safe."**
```bash
echo "$TOKEN" | nightshift-ctl authority release --generation "$GEN" --token-stdin
nightshift-ctl authority show   # must be {"active": false} again
```
A verification-held row left active blocks Program's own next real turn from acquiring the fence
for up to its TTL — a self-inflicted version of exactly the lockout this runbook exists to catch.

**Step D — resume the heartbeat scheduler** (paused in Step 0) only after Step C confirms
`{"active": false}`. Then watch the next real scheduled Program turn end-to-end (§8).

## 6. Rollback — fast path, executable without any Program dispatch

**Trigger conditions:** Step A of §5 returns ALLOW instead of DENY; or a live Program turn's own
`authority acquire`/`permit-dispatch` calls come back exit-3 in a way not explained by a
legitimate concurrent holder (e.g., "no unexpired, unconsumed permit" immediately after a
verified-successful `permit-dispatch`, pointing at a digest mismatch between what's live and what
`nightshift-ctl` on this host computes); or Program's own turns visibly stop dispatching to *any*
direct-child seat across two consecutive scheduled runs.

**Do not rely on `NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1` here.** Read
`deploy/policies/omnipods_policies.py:499-506` (missing-control-DB branch) and `:541-548`
(`sqlite3.Error` branch): that flag only flips the verdict to ALLOW in those two narrow branches.
It does **nothing** for the actual self-lockout shape: a healthy, reachable control DB with simply no
active `program_authority` row (the exact state left behind if the prompt never calls `acquire`,
or if `acquire` is being denied because a different generation legitimately holds it). Setting
this env var on the live server process will not un-stick that case. Treat this as a documented
trap, not a rescue.

**The fast, reliable rollback is the release mechanism's own, already tested, already-preserved
bundle restore — run directly by the owner/operator's own shell, with no Program dispatch in the
path at all:**
```bash
/srv/nightshift-releases/<sha>/checkout/bin/nightshift-release rollback \
  --plan /srv/nightshift-release-state/<sha>/plan.json \
  --owner-go <same plan_digest>
```
This is never gated by `program_authority_fence` — that policy only intercepts
`sys_session_send`/`sys_session_create` tool-call events evaluated *inside* an Omnigent session;
a human's direct shell invocation of `nightshift-release` never passes through that policy at all.

`rollback` itself requires quiescence (no active Omnigent busy sessions, heartbeat paused with no
`next_run_at` pending — `deploy/exact_sha_release.py:2410-2425`) and will refuse rather than force
through a still-active control plane. In practice this should resolve quickly: per ADR-0044 §7b, a
Program turn that loses the fence race (or was never granted it) stands down cleanly within its
own turn — zero further dispatch, one `schedule observe --status standdown` call, turn ends — it
does not hang. If a turn appears wedged past a few minutes anyway, that is itself a second-order
bug worth its own incident, not grounds to bypass `rollback`'s own safety refusal.

**Correction (staff review, PR #328): `rollback` DOES auto-resume the scheduler — it is not a
manual step, and there is no checkpoint here to rely on.** `_restore_scheduled_task`
(`deploy/exact_sha_release.py:2091-2101`) is called unconditionally from `_rollback_live`
(`:2192-2220`), the single function both the standalone `rollback` command (`:2426`) and
`cutover`'s own internal auto-rollback-on-failure paths (`:2360,2369,2384,2392`) share. It restores
the scheduled task to `plan["scheduled_task"]["state"]` — the state `prepare` captured (`:927`),
which is **before** the pre-`authorize` pause in §4 step 2, i.e. normally `"active"`. Concretely:
the moment `rollback`/auto-rollback finishes, Program's heartbeat is running again against the
restored, pre-cutover release, whether or not you (or Step 0) had it paused a moment before.

After `rollback` reports `ROLLBACK VERIFIED`:
- `nightshift-ctl authority show` → confirm `{"active": false}`.
- Confirm the restored scheduled task's `state`/`next_run_at` actually came back to the expected
  pre-cutover values (it should have — `_restore_scheduled_task` itself raises if it didn't — but
  verify rather than assume, since Program will start turning again on this state immediately).
- If you want Program held off a little longer past this point — e.g. to finish writing the
  incident record in `.omni/decisions.md` before a new heartbeat turn starts — that pause is on
  you to add explicitly, right now, through the same API used earlier; `rollback` will not leave
  one standing for you, and there is no separate window to catch it in afterward.
- Record the failure evidence (which check tripped, exact reason strings) in `.omni/decisions.md`
  before attempting a second cutover — the standing "do not cut over" decision should be
  re-instated explicitly, not left ambiguous.

## 7. Who should actually press the button

**Recommendation: this stays a manually-supervised, human-triggered action end to end — `prepare`
and `authorize` may be staged ahead of a window by an operator, but `cutover`, all of §5, and any
`rollback` must be run interactively by a human who stays present for the whole window, not
scheduled and not run by an unattended heartbeat turn.**

Reasons:
- Blast radius is total and self-referential: a bad cutover can deny 100% of Program's own
  dispatches, and the one guaranteed-safe recovery path is Program *not* being the thing that
  performs it — automating cutover would make the fix depend on the exact capability the failure
  removes.
- The tooling already encodes this design choice: `cutover` requires `--owner-go` to equal a
  digest the operator must read off `authorize`'s output and paste, never recompute
  (`deploy/exact_sha_release.py:2294-2295`); ADR-0013 assigns "outage authority" to the owner by
  name, not to any seat.
- `prepare`/`authorize` are safe to stage without a human standing by (no live mutation, and
  `authorize` expires after 10 minutes on its own), but nothing past that point should be.

## 8. After a clean cutover

- Record acceptance in `.omni/decisions.md`, explicitly resolving the ~12:40Z "do not cut over"
  decision per its own instruction ("once §7 clears, this decision's 'do not cut over' instruction
  is satisfied and should be marked resolved in a follow-up entry").
- Watch the next 2–3 real scheduled Program heartbeat turns' `authority show` state and any
  `standdown` status writes before treating this as fully bedded in — a race loser standing down
  cleanly is expected and healthy; repeated stand-downs with no winner ever completing a turn is
  not, and is a rollback trigger even after an initially-clean cutover.
