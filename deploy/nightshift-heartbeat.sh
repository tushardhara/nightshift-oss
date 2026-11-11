#!/usr/bin/env bash
#
# nightshift-heartbeat.sh -- the beat-producer health signal.
#
# WHAT THIS WRITES, AND WHAT IT DOES NOT (ADR-0006 D2, issue #76)
# ===============================================================
# This timer writes ONE signal: `heartbeat:heartbeat-timer`. It answers exactly
# one question -- "is the recovery layer's timer still firing?" -- and it is the
# only thing it is entitled to answer.
#
# It MUST NEVER write `heartbeat:program`. That signal belongs to the Program,
# and only after a COMPLETED control-loop iteration (ADR-0002 §1).
#
# The first version of this script did write `--who program`, taking both the
# run-id and the sequence out of the Program's own record. That is the defect
# class ADR-0002's Context names: "the recovery mechanism generates the evidence
# that suppresses recovery." Three consequences were reproduced against it:
#
#   (a) A dead Program read HEALTHY forever -- each tick restamped `ts`, so the
#       staleness check could never turn red.
#   (b) The post-reboot boot-ID check was erased in ONE tick. `cmd_beat_write`
#       does `boot = args.boot or host_boot_id()`, and this script passes no
#       --boot, so every tick stamped the LIVE boot id over the old one. With
#       OnBootSec=2min, a box that reboots and never starts its Program reported
#       healthy from T+2min onward, permanently.
#   (c) The inverse: a false DEAD pinned across a run roll, because the run-id
#       came from the record instead of from `run current`. It self-healed only
#       when the Program wrote a beat -- which, during a long turn, is precisely
#       the thing not happening.
#
# THE RULE THIS IS BUILT AGAINST (ADR-0006 D1)
# ============================================
# ADR-0002 §1 stated ownership as a list of banned nouns ("never written by any
# timer, worker or backup"). A noun list is an enumerable surface. The property
# underneath it is:
#
#   A liveness signal may be written only by a process whose continued existence
#   is caused by the liveness of the thing that signal attests.
#
#   Review question: WHAT STOPS THIS WRITER WHEN THE ATTESTED THING STOPS?
#
# A systemd timer fails that question for the Program's signal -- an independent
# lifetime is the entire point of a timer. It PASSES for its own signal: what
# stops this timer is the timer stopping. Its output is now a claim about the
# timer, supported only by inputs about the timer. The self-reference is gone.
#
# Nothing may infer Program liveness from this signal (ADR-0006 D3). The two may
# be displayed side by side; they may never be OR'd. #68 -- a long Program turn
# outliving its own max_age -- is NOT fixed here; it is fixed by Program-authored
# progress beats (ADR-0006 D4), which is a separate change.
#
# EXIT STATUS -- permanent misconfiguration is LOUD, transient is QUIET
# =====================================================================
# An earlier version never exited non-zero, which is how a PATH bug (#42) shipped
# invisibly: Type=oneshot plus exit 0 plus output to /dev/null means systemd,
# `list-timers` and the journal all report a healthy component doing nothing.
#
# The line is drawn between a VERDICT and a BREAKAGE.
#
#   QUIET (exit 0) -- nothing to do, or the plane legitimately declined:
#     * $PROJECT_DIR/.omni does not exist -- not initialised, or wrong box.
#     * no current run recorded -- there is no run to attest yet.
#     * `beat write` exits 3 -- a verdict, not an error. Retry next tick.
#
#   LOUD (exit 1, message on stderr) -- MISCONFIGURED; retrying cannot fix it:
#     * the control binary is missing, not executable, or cannot speak `beat`
#     * the control plane is unreadable
#     * `run current` or `beat show` fails for any reason but the above
#     * `beat write` fails for any reason other than exit 3
#
# Note the asymmetry with the old script, and that it is deliberate: a MISSING
# record is no longer a refusal-to-write. This timer is the legitimate owner of
# `heartbeat:heartbeat-timer`, so bootstrapping its own seq=1 is not forgery.
# The refusal was right when the target was someone else's signal; it does not
# apply to one's own.

set -u

PROG="nightshift-heartbeat"

# The signal this timer owns. Changing this to "program" reintroduces #76.
# There is no code path in this script that writes any other --who.
WHO="heartbeat-timer"
TIMER_SESSION="heartbeat-timer-durable"

die() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  exit 1
}

PROJECT_DIR="${1:-/srv/project}"

# ABSOLUTE by default. ~/.local/bin/nightshift-ctl on a deployed box is a symlink
# to the FROZEN /srv/nightshift copy, which has no `beat` subcommand (#42), so a
# bare PATH name silently found a binary that could not do this job.
CTL="${NIGHTSHIFT_CTL:-$PROJECT_DIR/bin/nightshift-ctl}"

# Not initialised yet, or wrong box -- nothing to write, and that is not a fault.
[ -d "$PROJECT_DIR/.omni" ] || exit 0

PLANE="$PROJECT_DIR/.omni/control.db"

# ------------------------------------------------------------- probe --------
# Probe, THEN mutate. Same ordering as nightshift_install_user_units.

[ -n "$CTL" ] || die "NIGHTSHIFT_CTL is set but empty; refusing to guess a control binary"

CTL_PATH=$(command -v -- "$CTL" 2>/dev/null || true)
if [ -z "$CTL_PATH" ] || [ ! -x "$CTL_PATH" ]; then
  die "control binary not found or not executable: '$CTL'"
fi

if ! probe_out=$("$CTL_PATH" beat --help 2>&1 >/dev/null); then
  probe_line=$(printf '%s\n' "$probe_out" | sed -n '1p')
  die "control binary '$CTL_PATH' does not support the 'beat' subcommand; this timer cannot write a heartbeat with it. Point NIGHTSHIFT_CTL at the project's own bin/nightshift-ctl (issue #42, frozen binary on PATH). Probe said: $probe_line"
fi

# ---------------------------------------------------------- current run -----
# From `run current`, NEVER from a beat record. Reading it out of the record is
# what produced reproduction (c): a signal permanently pinned to a stale run,
# self-healing only when someone else wrote a beat.
run_err=$(mktemp) || die "mktemp failed"
trap 'rm -f "$run_err"' EXIT

run_out=$(cd "$PROJECT_DIR" && "$CTL_PATH" --db "$PLANE" run current 2>"$run_err")
run_rc=$?

case "$run_rc" in
  0) : ;;
  3)
    # No current run recorded. There is nothing to attest yet -- not a fault.
    exit 0
    ;;
  *)
    die "\`run current\` failed with exit $run_rc: $(sed -n '1p' "$run_err" 2>/dev/null)"
    ;;
esac

run_id=$(printf '%s' "$run_out" | python3 -c '
import json, sys
try:
    doc = json.loads(sys.stdin.read() or "{}")
except Exception:
    print(""); raise SystemExit(0)
rid = doc.get("run_id")
print(rid if isinstance(rid, str) else "")
' 2>/dev/null || true)

[ -n "$run_id" ] || die "\`run current\` returned exit 0 but no usable run_id"

# ------------------------------------------------ this timer's own record ---
# Read OUR OWN signal to find our own last sequence. Note --who "$WHO": this
# script never reads or writes heartbeat:program.
show_err=$(mktemp) || die "mktemp failed"
trap 'rm -f "$run_err" "$show_err"' EXIT

# `beat show` exits 3 for "stale", for "missing" AND for a perfectly good record,
# so the exit code cannot discriminate here. reason_code is the only signal.
show_out=$(cd "$PROJECT_DIR" && "$CTL_PATH" --db "$PLANE" beat show --who "$WHO" 2>"$show_err")

parsed=$(printf '%s' "$show_out" | python3 -c '
import json, sys

raw = sys.stdin.read().strip()
if not raw:
    print("FATAL"); print("`beat show` produced no output on stdout"); raise SystemExit(0)
try:
    doc = json.loads(raw)
except Exception as exc:
    print("FATAL"); print("`beat show` output is not JSON: %s" % (exc,)); raise SystemExit(0)

code = doc.get("reason_code")
rec = doc.get("record")

if not isinstance(rec, dict):
    if code == "missing":
        # First tick for this signal. We own it, so seq=1 is ours to write.
        print("MISSING"); print("0"); print(""); raise SystemExit(0)
    print("FATAL"); print("no beat record in `beat show` output (reason_code=%s)" % (code,))
    raise SystemExit(0)

seq = rec.get("seq")
if isinstance(seq, bool) or not isinstance(seq, int):
    print("FATAL"); print("beat record has a non-integer seq: %r" % (seq,)); raise SystemExit(0)

run = rec.get("run_id")
print("OK"); print(seq); print(run if isinstance(run, str) else "")
') || die "failed to parse \`beat show\` output (is python3 present?)"

status=$(printf '%s\n' "$parsed" | sed -n '1p')
last_seq=$(printf '%s\n' "$parsed" | sed -n '2p')
last_run=$(printf '%s\n' "$parsed" | sed -n '3p')

case "$status" in
  MISSING|OK) : ;;
  FATAL)
    detail=$(printf '%s\n' "$parsed" | sed -n '2p')
    stderr_line=$(sed -n '1p' "$show_err" 2>/dev/null || true)
    if [ -n "$stderr_line" ]; then
      die "$detail (stderr: $stderr_line)"
    fi
    die "$detail"
    ;;
  *)
    die "internal error: unexpected parser status '$status'"
    ;;
esac

case "$last_seq" in (''|*[!0-9]*) die "internal error: non-numeric seq '$last_seq'" ;; esac

# A run roll restarts this signal's sequence: `beat write` reports reset:true and
# the sequence space is per-run. Continuing to increment across a roll would make
# seq meaningless as a count of this timer's ticks within the current run.
if [ "$last_run" = "$run_id" ]; then
  next_seq=$((last_seq + 1))
else
  next_seq=1
fi

# ------------------------------------------------------------- write --------
write_out=$(cd "$PROJECT_DIR" && "$CTL_PATH" --db "$PLANE" beat write \
  --who "$WHO" \
  --run "$run_id" \
  --seq "$next_seq" \
  --session "$TIMER_SESSION" \
  --phase "durable-timer" 2>&1)
write_rc=$?

case "$write_rc" in
  0|3)
    # 0 = accepted. 3 = a verdict (seq did not advance, wrong run); something
    # else wrote first. Either way the next tick re-reads and retries.
    exit 0
    ;;
  *)
    write_line=$(printf '%s\n' "$write_out" | sed -n '1p')
    die "\`beat write\` failed with exit $write_rc (who $WHO, seq $next_seq, run $run_id): $write_line"
    ;;
esac
