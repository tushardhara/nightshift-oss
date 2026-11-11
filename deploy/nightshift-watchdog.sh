#!/usr/bin/env bash
# Liveness watchdog for the org's heartbeat — the dead-man's switch OUTSIDE the
# agent loop.
#
# Why: the hourly heartbeat is the autonomy mechanism, but it is a row the
# program agent itself created. If that row is lost in a restart, or the
# scheduler wedges, nothing wakes the org again and the run stops SILENTLY —
# the exact failure the heartbeat exists to prevent. The config tells the
# program to "verify the heartbeat after any restart", but the program only runs
# when woken, so that instruction cannot execute if the heartbeat is what was
# lost. This timer is the check that does not depend on the thing it checks.
#
# ---------------------------------------------------------------------------
# WHAT CHANGED, AND WHY IT MATTERS (ADR-0002, issue #10, DoD condition 4)
#
# This script used to decide liveness from the newest MTIME under `.omni/`.
# `deploy/nightshift-state-backup.sh` rewrites `.omni/control-backup.json` every
# 30 minutes on its own timer, so a DEAD PROGRAM READ HEALTHY FOREVER. Both
# architects, independently, named that the most dangerous defect in the system.
#
# Worse: while that false "healthy" held, this script called
# `nightshift-ctl slot renew-active`, renewing EVERY active slot — Codex: "a
# per-worker lease converted into an organization-wide lease" — and
# `renew-active` itself writes under `.omni/`, so the monitor refreshed the very
# evidence it was monitoring.
#
#     "The recovery mechanism generates the evidence that suppresses recovery."
#
# Now: liveness is read BY CONTENT from a record the Program itself writes,
# via `nightshift-ctl beat show --who program`
# (contract: .omni/handoffs/LIVENESS-RECORD-v1.md). The control plane rejects a
# wrong run id, a heartbeat from a previous boot, a future timestamp, a
# non-increasing sequence and a malformed record — any of which means NOT ALIVE.
#
# STAGING NOTE (ADR-0002, one-way door): the verb `slot renew-active` still
# EXISTS in nightshift-ctl and is deliberately NOT deleted here. This change only
# stops the watchdog CALLING it. The verb is removed in a later, separately
# ADR-authorised PR, after worker-owned renewal is merged and observed working —
# never both in one change.
#
# NAMED SUCCESSOR CONTROL - required by assumption 5 of `.omni/goal.md`:
# removing something requires NAMING the remaining control that makes it
# redundant. This is that naming.
#
# This script no longer consults `STATUS.md` to decide whether the project has
# finished. ADR-0002 §6 requires terminal state to come from a control-plane
# transition, and LIVENESS-RECORD v1 exposes no way to READ one - that missing
# interface is **issue #26**, filed against crew_alpha's seam. When #26 lands,
# this script reads that transition and the noise below disappears.
#
# Until then a FINISHED project keeps alerting once per REALERT_HOURS. That is
# expected and bounded, and it is the deliberate direction of the trade
# (program ruling R-13, `.omni/decisions.md`): a finished project that alerts
# is noise; a dead project whose stale first line still reads "complete" stays
# silent FOREVER - which is bug 4, the defect this milestone exists to close.
# The deleted check was never a safety control here, it was an alert
# SUPPRESSOR, and removing a suppressor cannot weaken a dead-man's switch.
#
# What it does NOT do, on purpose:
#   - renews NOTHING (not slots, not its own signal)
#   - writes NO heartbeat (a monitor that feeds its own input cannot fail)
#   - manages NO services (restarting one kills in-flight worker turns)
#   - never exits non-zero: a watchdog that crashes its timer is worse than none
# ---------------------------------------------------------------------------

set -u
PROJECT_DIR="${1:-/srv/project}"

# ---- every numeric knob below is a RAW ENVIRONMENT INPUT ------------------
# So each one is VALIDATED, not trusted. Before this block the bounds were
# asserted in a comment and in a source-text test, and enforced nowhere:
#
#   * `DELIVERY_BACKOFF_MAX=banana` made `[ "$backoff" -gt "$MAX" ]` abort with
#     "integer expression expected", so NO cap was applied at all;
#   * `DELIVERY_BACKOFF_BASE=86400 DELIVERY_BACKOFF_MAX=86400` deferred the
#     retry of an UNDELIVERED owner alert by 86399s — four times past the 6h
#     re-alert window — while the owner stayed untold.
#
# That is bug 4 (an owner who is never told) reached by a new path: not through
# a throttle marker this time, but through a backoff the environment was
# allowed to grow without bound. A safety bound that only a comment enforces is
# not a safety bound.
#
# FAIL-CLOSED: anything that is not a plain decimal integer inside its
# documented range is REJECTED, loudly, and replaced by the shipped default. A
# malformed value never becomes a huge cap, an absent cap or a negative one.
sane_int() {  # name raw default min max -> echoes the effective value
  local _name="$1" _raw="$2" _def="$3" _lo="$4" _hi="$5" _val
  case "$_raw" in
    # Rejects empty, negative (the `-`), non-numeric, decimals, hex, and
    # anything long enough to overflow shell arithmetic into a wrapped value.
    (''|*[!0-9]*|??????????*)
      echo "watchdog: ${_name}='${_raw}' is not a plain integer — using ${_def}" >&2
      printf '%s\n' "$_def"; return 0 ;;
  esac
  _val=$(( 10#$_raw ))   # `10#` so `010` is ten, not octal eight
  if [ "$_val" -lt "$_lo" ] || [ "$_val" -gt "$_hi" ]; then
    echo "watchdog: ${_name}=${_val} is outside [${_lo},${_hi}] — using ${_def}" >&2
    printf '%s\n' "$_def"; return 0
  fi
  printf '%s\n' "$_val"
}

# 3 missed hourly heartbeats. Below 1h it would alert on a healthy box; a
# staleness threshold measured in weeks is not a dead-man's switch.
STALE_HOURS=$(sane_int NIGHTSHIFT_WATCHDOG_STALE_HOURS \
  "${NIGHTSHIFT_WATCHDOG_STALE_HOURS:-3}" 3 1 168)
# Don't spam the phone. Bounded on BOTH sides on purpose: 0 would mean "no
# throttle at all", and a re-alert window measured in months is not a re-alert,
# it is the silence this watchdog exists to prevent. It is also the ceiling the
# delivery backoff is clamped against below, so an unbounded value here would
# hand the backoff back the very freedom this block removes.
REALERT_HOURS=$(sane_int NIGHTSHIFT_WATCHDOG_REALERT_HOURS \
  "${NIGHTSHIFT_WATCHDOG_REALERT_HOURS:-6}" 6 1 24)
# Backoff for RETRYING AN UNDELIVERED ALERT. Distinct from REALERT_HOURS, which
# throttles alerts that were actually DELIVERED. This one only ever governs how
# often a FAILING transport is re-attempted, and its cap is deliberately far
# below one REALERT window: a delivery backoff that grew past the re-alert
# window would reinvent the very defect it exists to fix, going quiet for longer
# than the throttle it replaced. Doubling from one tick, capped at an hour.
#
# BASE must be >= 1: a zero base collapses the whole curve to zero and retries a
# rate-limited API every tick forever. MAX may legitimately be 0 — that means
# "retry on every tick", which is LOUD, the safe direction, and the only value
# of zero that is not a mute.
DELIVERY_BACKOFF_BASE=$(sane_int NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE \
  "${NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE:-600}" 600 1 3600)
DELIVERY_BACKOFF_MAX=$(sane_int NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX \
  "${NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX:-3600}" 3600 0 86400)

# ---- THE SAFETY BOUND, ENFORCED -------------------------------------------
# The invariant, stated once and checked in code: AN UNDELIVERED OWNER ALERT IS
# NEVER DEFERRED PAST THE RE-ALERT WINDOW. Both knobs are derived from the
# EFFECTIVE re-alert window rather than from a literal, so the bound holds for
# whatever REALERT_HOURS actually ends up being — that is what the old literal
# `3600` could not do.
#
# Half the window, not the whole window: a failing transport then gets at least
# TWO delivery attempts inside every window in which a DELIVERED alert would
# have been throttled. Clamping to the window exactly would leave the boundary
# case — deferred to precisely the re-alert instant — indistinguishable from
# the defect. At the shipped defaults this changes nothing (3600 < 21600/2).
REALERT_SECONDS=$(( REALERT_HOURS * 3600 ))
DELIVERY_BACKOFF_CEILING=$(( REALERT_SECONDS / 2 ))
if [ "$DELIVERY_BACKOFF_MAX" -gt "$DELIVERY_BACKOFF_CEILING" ]; then
  echo "watchdog: delivery backoff cap ${DELIVERY_BACKOFF_MAX}s exceeds half the" \
       "${REALERT_HOURS}h re-alert window — clamped to ${DELIVERY_BACKOFF_CEILING}s" >&2
  DELIVERY_BACKOFF_MAX="$DELIVERY_BACKOFF_CEILING"
fi
if [ "$DELIVERY_BACKOFF_BASE" -gt "$DELIVERY_BACKOFF_CEILING" ]; then
  DELIVERY_BACKOFF_BASE="$DELIVERY_BACKOFF_CEILING"
fi

# How long a box may sit un-armable (no heartbeat AND no recorded run) before
# that itself becomes the alert. See the first-boot cutover block below.
UNARMED_GRACE_HOURS=$(sane_int NIGHTSHIFT_WATCHDOG_UNARMED_GRACE_HOURS \
  "${NIGHTSHIFT_WATCHDOG_UNARMED_GRACE_HOURS:-24}" 24 1 8760)
# ---- ctl resolution: NEVER from PATH by default (issue #42) ---------------
# #42: the watchdog ran a DIFFERENT, STALE `nightshift-ctl` picked up from PATH
# — one with no `beat` verb at all. The unit hardcodes
# `Environment=PATH=__HOME__/.local/bin:...`, and on the live box
# `~/.local/bin/nightshift-ctl` is a symlink into a frozen checkout. So the
# watchdog ran THIS tree's script against ANOTHER tree's binary, and every
# `beat show` failed. The exact-SHA cutover did not fix that: it changed which
# checkout the SCRIPT comes from and left the unit's PATH untouched.
#
# The failure is not loud. A ctl with no `beat` verb exits non-zero, `alive`
# stays 0, and the watchdog alerts `unavailable` — "the control plane could not
# be read". That story blames the control plane for a packaging fault, so the
# alarm is not merely broken, it is MISLEADING, which is worse: it sends the
# operator to the wrong place while the real dead-man's switch is down.
#
# Fix: default to the ctl that ships ALONGSIDE THIS SCRIPT, resolved from the
# script's own location. Script and binary then come from one tree by
# construction, whichever tree that is, and PATH is out of the trust path
# entirely. A stale unit that was never re-rendered still gets this right,
# because it is the script — not the unit — that decides.
#
# $NIGHTSHIFT_CTL still wins when set: the tests need a stub, and an operator
# on a hand-built box needs an escape hatch. Explicit configuration overriding a
# safe default is fine; a safe default silently overridden by ambient PATH is
# not, and that distinction is the whole of #42.
_self_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P 2>/dev/null) || _self_dir=""
CTL="${NIGHTSHIFT_CTL:-}"
if [ -z "$CTL" ]; then
  if [ -n "$_self_dir" ] && [ -x "$_self_dir/../bin/nightshift-ctl" ]; then
    CTL="$_self_dir/../bin/nightshift-ctl"
  else
    # Last resort. Kept so a hand-assembled box still functions, and made
    # VISIBLE below by the capability assertion rather than trusted.
    CTL="nightshift-ctl"
  fi
fi
# The probe ships alongside this script too, for the same reason.
PROBE="${NIGHTSHIFT_SCHEDULER_PROBE:-${_self_dir:-.}/nightshift-scheduler-probe.py}"
SCHED_ENABLED="${NIGHTSHIFT_WATCHDOG_SCHEDULER:-1}"
SCHED_RETRY="${NIGHTSHIFT_WATCHDOG_RETRY:-1}"
# HARDCODED, and the XDG indirection is deliberately DROPPED. Two reasons,
# both load-bearing:
#
#   1. SECURITY (F2). bootstrap grants Codex workers a set of writable_roots
#      under `approval_policy = "never"`, and the old location
#      (~/.local/state) was inside that grant. An agent worker could therefore
#      write `bootstrapped-at` and `watchdog-unarmed-since` -- the two files
#      that decide whether this dead-man's switch stays quiet. Measured with a
#      dead Program: 200 ticks, an agent refreshing both, 200/200 silent. The
#      state dir now sits outside every granted root; see RT-30, which asserts
#      that by PARSING the config bootstrap actually writes.
#
#   2. The producer and the consumer must be unable to DISAGREE. bootstrap.sh
#      writes this file under a login shell; this script runs under the systemd
#      user manager, which exports only HOME and PATH
#      (deploy/systemd/nightshift-watchdog.service). With the XDG indirection,
#      an operator with XDG_STATE_HOME set in their profile got two different
#      directories, and the watchdog would then read "no installer attestation"
#      on a perfectly healthy install and alert with the story "the plane was
#      RESTORED" -- defect #34 reintroduced, wearing a misleading label. Two
#      processes that CANNOT disagree beat two asserted to agree, which closes
#      that seam by construction rather than by test.
#
# $HOME/.config/omnigent matches the convention already used for server.env and
# the systemd units. The departure from XDG is the point, not an oversight.
STATE_DIR="$HOME/.config/omnigent"
MARKER="$STATE_DIR/watchdog-last-alert"
# DISTINCT from $MARKER on purpose: $MARKER throttles alerts and is cleared on
# recovery, while this records when the box was FIRST seen un-armable. Merging
# them would make the not-yet-started path consume the alert window and swallow
# the first genuine alert after startup.
UNARMED_STAMP="$STATE_DIR/watchdog-unarmed-since"
# The scheduler alarm gets its OWN throttle marker. Sharing $MARKER would let
# whichever alarm fired first silence the other for a whole REALERT window —
# and these two report different faults with different fixes ("the Program
# stopped beating" vs "the thing that WAKES the Program is failing"). A stale
# beat alert must not swallow the news that the wake-up mechanism is down.
MARKER_SCHED="$STATE_DIR/watchdog-last-alert-scheduler"
# UNDELIVERED, not "last alert". These record that the owner has NOT been told,
# and they exist because the opposite used to be recorded instead.
#
# The old path collapsed any `gh issue list` failure to an empty issue number,
# journalled to the LOCAL journal -- on the box that is itself the thing that
# broke -- and then wrote the throttle marker anyway. Every tick for the next
# REALERT_HOURS exited early on that marker. Nothing ever reached the pinned
# status issue and the owner was never alerted, while the watchdog's own state
# claimed a delivered alert. A dead-man's switch that eats its own alarm is
# precisely the failure it exists to prevent, so the throttle marker is now
# written ONLY after CONFIRMED delivery and these files carry the failure.
#
# Paired one-to-one with the throttle markers above so that, exactly as with
# MARKER vs MARKER_SCHED, an undelivered beat alert cannot mute the scheduler
# alarm's retries or vice versa.
MARKER_UNDELIVERED="$STATE_DIR/watchdog-alert-undelivered"
MARKER_UNDELIVERED_SCHED="$STATE_DIR/watchdog-alert-undelivered-scheduler"
# Written by deploy/bootstrap.sh before it enables the timers. POSITIVE evidence
# that the installer ran, and when. Never written by this script — see R-22.
BOOTSTRAP_STAMP="$STATE_DIR/bootstrapped-at"
mkdir -p "$STATE_DIR" 2>/dev/null || true

# Not initialized yet, or wrong box — nothing to watch.
[ -d "$PROJECT_DIR/.omni" ] || exit 0

now=$(date +%s)
max_age=$(( STALE_HOURS * 3600 ))

# ------------------------------------------------------- scheduler (#132) ---
# The half of the dead-man's switch the beat record cannot cover.
#
# `beat show` answers "is the Program alive?". It does NOT answer "will the
# Program ever be woken again?" — and on 2026-08-24 that was the question that
# mattered. The hourly scheduled task failed at 04:00Z, 05:00Z and 06:00Z on
# correlated upstream `529 Overloaded`, and the only reason anyone noticed was
# that a driver session happened to still be awake. Luck is not a control.
#
# Two things are gained here, and neither is available from the beat:
#
#   ESCALATE — a failed run is a FACT the scheduler records the instant it
#     happens, with an `error_code`. Inferring it from a beat that has not
#     advanced takes STALE_HOURS (3 missed wake-ups) to reach the same
#     conclusion the scheduler already stated. Alerting on the stated fact is
#     both faster and more specific.
#
#   SUPPRESS — the org was deliberately silent 08:00Z->19:00Z on 2026-08-24, an
#     owner maintenance freeze with the task paused via the API. A beat-only
#     watchdog alerts on that, twice. An alarm that fires on intended states is
#     an alarm people learn to ignore, and that is how the real one gets missed.
#     `state != active` is a POSITIVE statement of intent, so this is the one
#     path in this script that buys silence with evidence rather than inferring
#     it from the shape of a silence.
#
# Fail-safe direction is the same as everywhere else in this file: ONLY exit 4
# suppresses, and only exit 4 carries that positive statement. Server down,
# probe missing, malformed JSON, timeout — all land on "unknown" and change
# NOTHING, leaving the beat check to alert on its own. A scheduler probe that
# could go quiet on its own failure would be a suppressor wearing a monitor's
# clothes, which is the exact defect ADR-0002 removed from the mtime version.
sched_alert=0
sched_reason=""
sched_detail=""
sched_json=""
if [ "$SCHED_ENABLED" = "1" ] && [ -f "$PROBE" ] && command -v python3 >/dev/null 2>&1; then
  sched_args="--now $now"
  # Retry is condition 4 of #132: a single transient 529 must not cost the org
  # an hour. The probe caps it hard (current slot only, budget per slot,
  # exponential backoff, never while a run is in flight).
  [ "$SCHED_RETRY" = "1" ] && sched_args="$sched_args --retry"
  sched_rc=0
  # shellcheck disable=SC2086
  sched_json=$(python3 "$PROBE" $sched_args 2>/dev/null) || sched_rc=$?
  case "$sched_rc" in
    4)
      # Deliberate pause. Say so in the journal — a silent exit here is
      # indistinguishable from the watchdog itself being dead, and an operator
      # checking "is my watchdog running?" deserves an answer.
      echo "watchdog: scheduled task is PAUSED on purpose — deliberate quiet, not a fault; no alert"
      exit 0
      ;;
    3)
      sched_alert=1
      parsed=$(printf '%s' "$sched_json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(d, dict):
    print(d.get("reason_code", "") or "")
    print((d.get("detail", "") or "").replace("\n", " "))
' 2>/dev/null || true)
      sched_reason=$(printf '%s\n' "$parsed" | sed -n '1p')
      sched_detail=$(printf '%s\n' "$parsed" | sed -n '2p')
      [ -n "$sched_reason" ] || sched_reason="scheduler_fault"
      ;;
    *) : ;;   # 0 healthy, 2 unknown, anything else — no verdict either way
  esac
fi

# ------------------------------------------ ctl capability assertion (#42) ---
# A resolved ctl that cannot answer the question is not a working watchdog, and
# it must not be allowed to masquerade as one. Without this, a `beat`-less ctl
# produces `unavailable` and the operator is told the control plane is corrupt —
# they check the database, find it healthy, and conclude the watchdog is flaky.
# Naming the real fault is the durable half of the #42 repair; resolving the
# path correctly is only the half that works until someone edits a unit again.
#
# Run from $PROJECT_DIR like every other ctl call in this file. `beat --help`
# reads no database, so the cwd is immaterial to the ANSWER — but "every ctl
# query is asked from the project dir" is an invariant RT-09/RT-27 assert
# against the journal, and an exception carved out for convenience is how such
# an invariant stops being one.
ctl_beatless=0
if ! (cd "$PROJECT_DIR" && "$CTL" beat --help) >/dev/null 2>&1; then
  ctl_beatless=1
fi

# --------------------------------------------------------------- liveness ---
# Contract §3: exit 0 = alive, exit 3 = NOT ALIVE with a machine-readable
# reason_code. Exit 3 is a VERDICT, not an error, and `set -e` would treat it as
# fatal exactly when the alert path must run — hence the capture idiom, which is
# the shape the contract tells consumers to copy.
# The plane is named TWICE, deliberately, and both are load-bearing:
#
#   1. `--db "$PLANE"` — nightshift-ctl's `--db` defaults to
#      `os.environ["NIGHTSHIFT_CTL_DB"]` and only then to a RELATIVE
#      `.omni/control.db` (issue #31). An explicit `--db` overrides BOTH. Without
#      it, a stray exported NIGHTSHIFT_CTL_DB silently re-points the dead-man's
#      switch at an empty plane — which answers "no run recorded" and buys
#      silence, without touching a single file on disk.
#   2. `cd "$PROJECT_DIR"` — retained as belt-and-braces for any older ctl on the
#      box whose `--db` is absent or spelled differently; there the cwd is still
#      what resolves the relative default.
#
# Getting this wrong does not read as an error. It reads as a healthy-looking
# empty plane, which is the worst possible failure for a monitor.
PLANE="$PROJECT_DIR/.omni/control.db"
verdict=""
alive=0
never_started_why=""
if verdict=$(cd "$PROJECT_DIR" && "$CTL" --db "$PLANE" beat show --who program --max-age "$max_age" 2>/dev/null); then
  alive=1
fi

# Contract §3 states stdout is always one JSON object, on every path. When this
# guard was written that was FALSE — on a corrupt or unreadable control.db
# `beat show` exited 1 with EMPTY stdout — and it was reported to the program as
# a finding against the handoff. **That finding is now SETTLED (issue #33).**
# PR #16 made an unreadable plane a verdict like any other:
# `{"alive": false, "reason_code": "control_plane_unreadable"}` on stdout with
# exit 3, and handoff §3 was updated to match. The narrative that used to stand
# here described a contract violation that no longer exists.
#
# The guard below is nevertheless RETAINED, DELIBERATELY. `security` reviewed it
# and demoted it from load-bearing to belt-and-braces rather than asking for its
# removal, and on a dead-man's switch that is the right call: the guard costs one
# `[ -n ]` test, while being wrong costs the watchdog dying on a parse at exactly
# the moment the control plane is the thing that broke. A contract that holds
# today is a promise about upstream code, not a property of this script; the
# whole point of a dead-man's switch is that it does not assume its inputs are
# well-formed. Keeping it also means the exit-1-with-no-JSON shape stays
# survivable for any older `nightshift-ctl` on the box.
#
# NO BEHAVIOUR CHANGE in this edit: `alive` still defaults to 0 and is still
# assigned ONLY on exit 0, so every non-zero exit — 3 (a verdict) or 1 (an
# error) — leaves NOT ALIVE. Fail-closed in both directions.
reason_code=""
age_s=""
if [ -n "$verdict" ]; then
  parsed=$(printf '%s' "$verdict" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(d, dict):
    print(d.get("reason_code", "") or "")
    print(d.get("age", "") if d.get("age") is not None else "")
' 2>/dev/null || true)
  reason_code=$(printf '%s\n' "$parsed" | sed -n '1p')
  age_s=$(printf '%s\n' "$parsed" | sed -n '2p')
fi

if [ "$alive" -eq 1 ]; then
  rm -f "$MARKER"   # the BEAT is healthy — arm the next beat alert
  # NOTHING is renewed here. Program liveness is not evidence that any
  # particular worker is progressing (ADR-0002 §4). Workers renew their own
  # slots; a leaked slot returns to the budget on its own TTL.
  #
  # A live beat does NOT clear a scheduler fault, and this is the case that
  # made #132 expensive. On 2026-08-24 a driver session was still awake and
  # beating while the scheduled runs were failing underneath it: the beat said
  # ALIVE, truthfully, and the mechanism that would wake the org after that
  # session ended was already broken. Exiting here on a live beat is exactly
  # the "an earlier driver happened to still be awake" luck the issue names as
  # the defect. The two signals answer different questions and neither may
  # acquit the other.
  if [ "$sched_alert" -eq 0 ] && [ "$ctl_beatless" -eq 0 ]; then
    exit 0
  fi
  reason_code="ok"   # the beat is fine; the fault reported below is not the beat
fi

[ -n "$reason_code" ] || reason_code="unavailable"

# #42, named rather than misattributed. Checked AFTER the beat attempt so a ctl
# that works is never slowed down, and BEFORE the story is chosen so the
# operator is sent to the packaging fault instead of to the database.
if [ "$ctl_beatless" -eq 1 ]; then
  reason_code="ctl_lacks_beat"
fi

# ------------------------------------------------ first-boot cutover (#34) ---
# `missing` is TWO different states rendered identically, and only one of them
# is a fault:
#
#   (a) no beat has ever been written because the org has not STARTED yet — a
#       freshly bootstrapped box, between `enable --now` on this timer and the
#       Program's first completed loop. Healthy. Alerting here fires STALE on
#       every fresh install, which is the false-alert class ruling R-14 guards,
#       arriving from the opposite direction.
#   (b) a run IS recorded but its Program has never written a beat for it —
#       either it never completed an iteration, or the record was lost. A fault,
#       and exactly what this switch exists to catch.
#
# The heartbeat record cannot distinguish them: the ABSENCE of a record carries
# no reason for its own absence. The distinguishing fact is `current_run_id`,
# written ONCE at run start by a DIFFERENT command than the heartbeat — which is
# precisely why it is independent evidence and not the record vouching for
# itself (see `expected_run_id` in nightshift-ctl, and ADR-0002 §1's rule that
# no signal may stand in for another).
#
# The grace is granted ONLY on positive evidence and ONLY for `missing`:
#   - any other reason_code means a beat EXISTS, so the org demonstrably started;
#   - any `run current` outcome other than the explicit exit-3 "no run recorded"
#     verdict (error, missing binary, unreadable plane) is NOT evidence of a
#     fresh install, and alerts.
# It writes no marker, so a box that has not started yet does not burn its
# re-alert window and swallow the first genuine alert after startup.
#
# Ordering in bootstrap.sh was the alternative and was REJECTED (ADR-0004): the
# only way for bootstrap to satisfy it is to write the first beat itself, and a
# beat written by anything other than its named owner is a forged liveness
# signal — defect 4 rebuilt in a new place, and invisible, because a forged beat
# looks exactly like a real one.
#
# THE GRACE IS BOUNDED, and that bound is not optional (`security` finding B1 on
# PR #35). The trigger for this branch is the ABSENCE of `current_run_id`, and
# that row has exactly one write site in the whole tree — `run current --set` —
# which NOTHING currently calls: not bootstrap.sh, not any unit, not preflight.
# `run start` does not write it either; it inserts into `agent_runs` and never
# touches `meta`. So the plane can prove the org started and still answer "no
# run recorded".
#
# That makes the exposure window exactly "no beat AND no current_run_id", which
# is *the org was supposed to start and never wrote its first heartbeat* — the
# failure this switch exists to catch. An unbounded grace would silence it
# forever. The rule this file already lives by is the one that convicts it:
# ABSENCE OF EVIDENCE NEVER BUYS SILENCE, and two unexplained absences are not
# evidence of a fresh install.
#
# So: stamp when first seen un-armable, stay quiet inside the window, and ALERT
# once it expires. Every way the bound could itself fail open resolves toward
# alerting — a stamp that cannot be parsed, or cannot be persisted, is not a
# bound, and an unbounded grace is refused rather than granted. The stamp is
# never rewritten once set, because a stamp refreshed each tick is a window that
# slides forward forever: an unbounded grace wearing a bound's clothes.
#
# The bound also closes, at 24h, the whole family of "empty plane" cases —
# deleted, zero-byte truncated (a valid empty SQLite database), or addressed
# through the wrong path — independently of WHY the plane is empty, including
# reasons nobody has thought of yet. Pinning `--db` above prevents most of them
# outright; this catches the rest.
if [ "$reason_code" = "missing" ]; then
  run_rc=0
  (cd "$PROJECT_DIR" && "$CTL" --db "$PLANE" run current >/dev/null 2>&1) || run_rc=$?
  if [ "$run_rc" -eq 3 ]; then
    unarmed_since=""
    if [ -f "$UNARMED_STAMP" ]; then
      unarmed_since=$(cat "$UNARMED_STAMP" 2>/dev/null || echo "")
      # Unreadable or garbled: the grace has no known start, so it has no known
      # end. Treat as long expired rather than restart it.
      case "$unarmed_since" in (''|*[!0-9]*) unarmed_since=0 ;; esac
    else
      # First tick in this state. Record when the clock started — and if that
      # cannot be persisted, every future tick would look like the first one and
      # the grace would never expire. Refuse it instead.
      if echo "$now" > "$UNARMED_STAMP" 2>/dev/null; then
        unarmed_since="$now"
      else
        unarmed_since=0
      fi
    fi
    # ---- POSITIVE GATE (decision R-22) ----------------------------------
    # Everything above still keys the grace on an ABSENCE — `run current`
    # exiting 3 — and a bound narrows that window without closing it. The
    # absence is not discriminating, because `nightshift-ctl dump` exports
    # slots, claims, leases, tasks, checkpoints and vendors and NOT `meta`
    # (issue #41). So EVERY restore from this project's own documented backup
    # path yields exactly "no current_run_id, no beats" — and
    # nightshift-state-backup.sh runs every 30 minutes precisely so that path
    # gets used. An absence-keyed grace therefore goes quiet on a genuinely
    # dead Program as the GUARANTEED outcome of recovery, not as an edge case.
    #
    # My own Option D argument, which I failed to apply to my own design: a
    # recreated database has no memory that a key once existed either.
    # `current_run_id` and the beat record are independent in WHO WRITES THEM
    # — which is what ADR-0004 designed for — and perfectly correlated in WHAT
    # DESTROYS THEM. Independent writers are not enough. The discriminating
    # fact must have an independent DESTRUCTION mode, and that is why it lives
    # in the state directory rather than in `meta`, which the backup drops.
    #
    #   fresh box            -> attestation present and RECENT -> grace
    #   restored plane       -> attestation present and OLD    -> alert NOW
    #   no attestation       -> installer never ran, or it was removed -> alert
    #
    # The third line is this file's own rule finally load-bearing on its own
    # trigger: ABSENCE OF EVIDENCE NEVER BUYS SILENCE. The grace is now granted
    # only on a positive fact, and EVERY conjunct of the gate below must hold
    # before silence is bought -- so the gate is strictly more fail-closed than
    # any one conjunct alone. Which conjuncts those are is NOT restated here:
    # the `if` below is the only authority on that. A count kept in prose drifts
    # the moment a conjunct is added, and that is precisely the defect -- this
    # sentence said "the two conditions" after F1 made them four (issue #53,
    # the same drift already struck from the ADR).
    #
    # This script NEVER writes $BOOTSTRAP_STAMP. A monitor that forges the
    # installer's attestation is the same defect as a beat forged on the
    # Program's behalf (ADR-0004 option A) — it would be attesting something it
    # cannot know.
    installer_at=0
    if [ -f "$BOOTSTRAP_STAMP" ]; then
      installer_at=$(cat "$BOOTSTRAP_STAMP" 2>/dev/null || echo 0)
      case "$installer_at" in (''|*[!0-9]*) installer_at=0 ;; esac
    fi
    grace_window=$(( UNARMED_GRACE_HOURS * 3600 ))
    # The `-le "$now"` conjunct is F1, and it is not defensive tidying: without
    # it a future-dated attestation made `now - installer_at` NEGATIVE, and a
    # negative number is unconditionally less than the grace window. So a
    # future date did not merely slip past this gate -- it was the one input
    # that could never fail it, and it bought silence FOREVER, on any box,
    # including the established-box-with-a-restored-plane case this whole
    # positive gate was built to catch. Fail-open, in the one direction a
    # dead-man's switch must never fail.
    if [ "$installer_at" -gt 0 ] \
       && [ "$installer_at" -le "$now" ] \
       && [ $(( now - installer_at )) -lt "$grace_window" ] \
       && [ $(( now - unarmed_since )) -lt "$grace_window" ]; then
      echo "watchdog: not armed — no run recorded and no heartbeat ever written," \
           "and this box was bootstrapped $(( (now - installer_at) / 60 ))m ago;" \
           "the org has not started yet (within the ${UNARMED_GRACE_HOURS}h" \
           "grace), so there is nothing to watch"
      exit 0
    fi
    # No positive evidence of a fresh install, or the grace expired.
    if [ "$installer_at" -eq 0 ]; then
      never_started_why="there is NO installer attestation on this box at all (\`$BOOTSTRAP_STAMP\` is missing or unreadable), so this is not a fresh install — the control plane was most likely RESTORED or replaced under a running install, or the state directory was wiped"
    elif [ "$installer_at" -gt "$now" ]; then
      never_started_why="the installer attestation (\`$BOOTSTRAP_STAMP\`) is dated in the FUTURE, $(( (installer_at - now) / 60 ))m ahead of this box's clock — either the clock was wrong when the installer ran, or the file was tampered with. A future-dated attestation attests nothing that could have happened, so it cannot buy the fresh-install grace. CHECK THE CLOCK AND THE FILE'S PROVENANCE — re-running the installer would overwrite the evidence without fixing the cause"
    elif [ $(( now - installer_at )) -ge "$grace_window" ]; then
      never_started_why="this box was bootstrapped $(( (now - installer_at) / 3600 ))h ago, so it is NOT installing — an empty control plane on an established box means the plane was restored, replaced or truncated (\`nightshift-ctl dump\` does not export \`meta\`, so every restore looks like this)"
    else
      never_started_why="it has been un-armable for over ${UNARMED_GRACE_HOURS}h"
    fi
    reason_code="never_started"
  fi
fi

# --------------------------------------------------------- alert throttle ---
# Which marker throttles this alert depends on WHICH alarm is speaking. See the
# MARKER_SCHED comment: one alarm must not be able to mute the other.
active_marker="$MARKER"
undelivered_marker="$MARKER_UNDELIVERED"
if [ "$sched_alert" -eq 1 ]; then
  active_marker="$MARKER_SCHED"
  undelivered_marker="$MARKER_UNDELIVERED_SCHED"
fi
if [ -f "$active_marker" ]; then
  last=$(cat "$active_marker" 2>/dev/null || echo 0)
  case "$last" in (''|*[!0-9]*) last=0 ;; esac
  if [ $(( (now - last) / 3600 )) -lt "$REALERT_HOURS" ]; then
    exit 0
  fi
fi

# Read-only service diagnostics for the operator. `is-active` QUERIES; nothing
# on any path of this script may manage a service.
server_state=$(systemctl --user is-active omnigent-server 2>/dev/null || echo unknown)
host_state=$(systemctl --user is-active omnigent-host 2>/dev/null || echo unknown)

age_human="unknown"
if [ -n "$age_s" ]; then
  case "$age_s" in
    (*[!0-9.-]*) : ;;
    (*) age_human="$(( ${age_s%%.*} / 3600 ))h" ;;
  esac
fi

# `wrong_boot` and `stale` are different operator stories; pass the reason through.
case "$reason_code" in
  missing)     story="a run IS recorded in the control plane, but the Program has never written a heartbeat for it — it has not completed a single loop iteration, or its record was lost. (A box where no run has been recorded at all is 'not started yet' and is handled silently above, issue #34.)" ;;
  never_started) story="NO Program heartbeat and NO recorded run — and ${never_started_why:-the grace expired}. Either the Program never started on this box, or the control plane being consulted is not the one it writes to." ;;
  malformed)   story="the Program heartbeat record is unparseable." ;;
  wrong_run)   story="the stored heartbeat belongs to a DIFFERENT run." ;;
  wrong_boot)  story="the heartbeat was written BEFORE the last reboot — the record survived, the Program did not." ;;
  future)      story="the heartbeat timestamp is materially in the future (clock skew)." ;;
  stale)       story="the Program heartbeat has not advanced within the threshold." ;;
  unavailable) story="the control plane could not be read at all (missing, corrupt or unreadable control.db)." ;;
  ctl_lacks_beat) story="this watchdog resolved a \`nightshift-ctl\` with NO \`beat\` verb (\`${CTL}\`), so it cannot read the Program's liveness record at all. **The dead-man's switch is DOWN — this alert says nothing about whether the Program is alive.** This is issue #42: the script and the binary came from different trees. Point \`NIGHTSHIFT_CTL\` at the ctl from the same checkout as this script, or re-render the unit." ;;
  ok)          story="the Program's heartbeat record is VALID — the fault reported below is not the Program itself." ;;
  *)           story="the Program heartbeat is not valid (reason: ${reason_code})." ;;
esac

# The scheduler fault leads when it is what fired. It is the more actionable
# story (a named error_code on a named run), and burying it under the beat
# narrative is how an operator ends up reading about heartbeat staleness while
# the actual news is that the org will never be woken again.
sched_section=""
if [ "$sched_alert" -eq 1 ]; then
  sched_section="**Scheduler fault: \`${sched_reason}\`.** ${sched_detail}

This is read from the scheduler's own run history — \`status\` and \`error_code\` per run — with ZERO model calls and no agent session (issue #132). A DELIBERATE pause is not reported here: a paused task is positive evidence of intent and this watchdog stays silent for it.

"
fi

headline="the Program is not alive"
reason_label="$reason_code"
if [ "$sched_alert" -eq 1 ]; then
  headline="the org's WAKE-UP MECHANISM is failing"
  # When the beat is VALID, `ok` is the beat's status, not the fault's name.
  # Labelling the alert `ok` would be actively confusing at the top of a 🚨.
  [ "$reason_code" = "ok" ] && reason_label="$sched_reason"
fi

# The beat narrative is included ONLY when the beat is the fault. On a pure
# scheduler alarm it is worse than filler: "the watchdog cannot tell finished
# from dead" and "heartbeat age" invite the operator to dismiss an alert whose
# subject is a failing scheduler, and the heartbeat here is FINE. An alert must
# not argue against itself.
beat_section=""
if [ "$reason_code" != "ok" ]; then
  beat_section="${story}

This verdict is read from the Program's own heartbeat record BY CONTENT — not from file timestamps under \`.omni/\`, and not from \`STATUS.md\`. Backup writes, timer output and this watchdog's own marker cannot make it look healthy (ADR-0002, issue #10). Heartbeat age: \`${age_human}\`.

**If this run has FINISHED, this alert is EXPECTED and can be ignored.** Terminal project state is not yet readable from the control plane (issue #26, ADR-0002 §6). Until that lands the watchdog cannot tell \"finished\" from \"dead\", and it deliberately errs toward alerting: a finished run that alerts every ${REALERT_HOURS}h is noise, a dead run that stays quiet is the defect this watchdog exists to catch.

"
else
  beat_section="The Program's own heartbeat record is VALID (age \`${age_human}\`) — **this is not a report that the Program is dead.** It is a report that the mechanism which WAKES it is failing, which is the more dangerous state precisely because the org looks healthy right now: the fault only becomes visible when the current session ends and nothing starts the next one.

"
fi

recovery="Recovery (one command on the box): start a session on the program agent with:
\`Resume from files: read .omni/goal.md, .omni/STATUS.md and the registries, reconcile against open PRs, continue the current wave. Re-create the hourly heartbeat task and confirm it with sys_scheduled_task_list.\`"
if [ "$sched_alert" -eq 1 ]; then
  recovery="What to check first: \`curl -s http://127.0.0.1:6767/v1/scheduled-tasks\` for the task's \`state\`, and \`.../runs\` for the failing runs' \`error_code\`. A \`529\`/overload family error is transient and the watchdog re-fires it with backoff on its own; a \`launch_failed\` or a missing task row is not, and needs a human.

If the task row is GONE, only a program session can recreate it: start one on the program agent with \`Re-create the hourly heartbeat task and confirm it with sys_scheduled_task_list.\`"
fi

body="🚨 **Watchdog: ${headline}** (reason \`${reason_label}\`, threshold ${STALE_HOURS}h).

${sched_section}${beat_section}Services (read-only check): omnigent-server \`${server_state}\`, omnigent-host \`${host_state}\`.

${recovery}"

# ------------------------------------------------------------ delivery ---
# THE THROTTLE MARKER IS WRITTEN ONLY ON CONFIRMED DELIVERY. Everything below
# is arranged around that one rule.

alarm="${reason_code}${sched_reason:+/$sched_reason}"

# How many consecutive delivery failures, and when was the last attempt?
d_attempts=0
d_last=0
if [ -f "$undelivered_marker" ]; then
  read -r d_attempts d_last _rest < "$undelivered_marker" 2>/dev/null || true
  case "$d_attempts" in (''|*[!0-9]*) d_attempts=0 ;; esac
  case "$d_last"     in (''|*[!0-9]*) d_last=0 ;; esac
fi

# Back off a FAILING transport -- never the alarm itself. This can only delay a
# retry of something that was never delivered; it can never mark it delivered.
if [ "$d_attempts" -gt 0 ]; then
  backoff=$(( DELIVERY_BACKOFF_BASE * (1 << (d_attempts > 20 ? 20 : d_attempts - 1)) ))
  [ "$backoff" -gt "$DELIVERY_BACKOFF_MAX" ] && backoff="$DELIVERY_BACKOFF_MAX"
  # The invariant again, AT THE POINT OF USE. The cap above is only as good as
  # the value the environment supplied; this line is the one that cannot be
  # talked out of it. Whatever DELIVERY_BACKOFF_* say, an undelivered owner
  # alert is never deferred past the re-alert window.
  [ "$backoff" -gt "$DELIVERY_BACKOFF_CEILING" ] && backoff="$DELIVERY_BACKOFF_CEILING"
  if [ $(( now - d_last )) -lt "$backoff" ]; then
    echo "watchdog: ALERT UNDELIVERED (${alarm}) — still not delivered to the owner after ${d_attempts} attempt(s); next delivery retry in $(( backoff - (now - d_last) ))s. THE OWNER HAS NOT BEEN TOLD." >&2
    exit 0
  fi
fi

# A delivery failure must be LOUD, must record the degraded state, and must
# leave the throttle marker untouched so the next eligible tick tries again.
delivery_failed() {
  echo "$(( d_attempts + 1 )) $now" > "$undelivered_marker" 2>/dev/null || true
  echo "watchdog: ALERT UNDELIVERED (${alarm}) — $1. THE OWNER HAS NOT BEEN TOLD; the ${REALERT_HOURS}h alert throttle was NOT consumed and delivery will be retried. Attempt $(( d_attempts + 1 ))." >&2
  exit 0
}

# WHERE the alert goes is pinned, the way the scheduled-run detector pins it:
# the same NIGHTSHIFT_ALERT_REPO / NIGHTSHIFT_ALERT_ISSUE, from the environment
# or read BY KEY (never sourced) from the operator's detector env file, which
# sits outside every sandbox-writable root. The old target was `.[0]` of any
# open issue whose title matched "Program status" -- on a public repository a
# stranger can open one, and then receives the owner's dead-man's-switch alert.
pin() {  # key -> value
  local _v
  _v=$(printenv "$1" 2>/dev/null || true)
  if [ -z "$_v" ] && [ -r "$STATE_DIR/scheduled-run-detector.env" ]; then
    _v=$(sed -n "s/^$1=//p" "$STATE_DIR/scheduled-run-detector.env" | tail -n 1)
    _v=${_v#[\"\']}; _v=${_v%[\"\']}
  fi
  printf '%s' "$_v"
}
alert_repo=$(pin NIGHTSHIFT_ALERT_REPO)
issue=$(pin NIGHTSHIFT_ALERT_ISSUE)
if [ -n "$alert_repo$issue" ]; then
  # A half-set or malformed pin is a misconfiguration, never a reason to fall
  # back to guessing by title.
  [[ "$alert_repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] && [[ "$issue" =~ ^[1-9][0-9]{0,9}$ ]] \
    || delivery_failed "the alert pin is malformed (NIGHTSHIFT_ALERT_REPO='${alert_repo}' NIGHTSHIFT_ALERT_ISSUE='${issue}')"
else
  # Unpinned (a hand-built box): only an exact-title issue that the
  # authenticated identity filed itself, and a loud note that it is unpinned.
  echo "watchdog: alert target is not pinned; set NIGHTSHIFT_ALERT_REPO and NIGHTSHIFT_ALERT_ISSUE" >&2
  me=$(cd "$PROJECT_DIR" && gh api user --jq .login 2>/dev/null || true)
  [[ "$me" =~ ^[A-Za-z0-9-]+$ ]] \
    || delivery_failed "the authenticated GitHub login could not be resolved to find the Program status issue"
  issue=$(cd "$PROJECT_DIR" && gh issue list --search "Program status in:title" --state open \
    --author "$me" --json number,title,author \
    --jq "[.[] | select(.title == \"Program status\" and .author.login == \"$me\")][0].number // empty" \
    2>/dev/null || true)
fi
if [ -z "${issue:-}" ] || [ "$issue" = "null" ]; then
  # Could not even NAME the status issue: `gh` unauthenticated or rate-limited,
  # no network, or the tracker really has no such issue. Previously this wrote
  # the throttle marker and went quiet for REALERT_HOURS. It is a DELIVERY
  # FAILURE like any other, and it stays visibly degraded until it clears.
  delivery_failed "no open Program status issue could be resolved (\`gh issue list\` failed or returned nothing)"
fi

if [ -n "$alert_repo" ]; then
  comment() { gh issue comment "$issue" --repo "$alert_repo" --body "$body"; }
else
  comment() { gh issue comment "$issue" --body "$body"; }
fi
if ! (cd "$PROJECT_DIR" && comment) >/dev/null 2>&1; then
  # Previously this emitted NOTHING: it correctly avoided the marker and
  # retried, but a persistently undeliverable alert looked exactly like a
  # quiet, healthy box.
  delivery_failed "\`gh issue comment\` failed against issue #$issue"
fi

# DELIVERED. Only now may the alert be throttled, and the degraded state clears.
echo "$now" > "$active_marker"
rm -f "$undelivered_marker" 2>/dev/null || true
echo "watchdog: ALERT (${alarm}) — alerted on issue #$issue"
exit 0
