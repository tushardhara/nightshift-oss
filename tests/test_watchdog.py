"""The watchdog: liveness by CONTENT, and it renews nothing — DoD condition 4, issue #10.

Both architects, blind to each other, named this the most dangerous defect in the
system. There is no watchdog test today; this file is the first.

The defect: `deploy/nightshift-watchdog.sh:53` took the newest mtime under
`.omni/`, and `deploy/nightshift-state-backup.sh:32` rewrites
`.omni/control-backup.json` every 30 minutes on its own timer. **A dead Program
reads healthy forever.** Worse, while that false "healthy" holds, `:60-62` calls
`nightshift-ctl slot renew-active`, renewing EVERY active slot — Codex: *"a
per-worker lease converted into an organization-wide lease"* — and
`renew-active` itself writes under `.omni/`, so the monitor refreshes the very
evidence it monitors.

    "The recovery mechanism generates the evidence that suppresses recovery."

Contract: ADR-0002 and `.omni/handoffs/LIVENESS-RECORD-v1.md` (v1).

Requirement ids (DoD condition 4):
  RT-08  only the backup file / timer output / the watchdog's own marker are
         recent -> judged STALE, and it alerts
  RT-09  a valid Program record -> judged ALIVE
  RT-10  while stale, `slot renew-active` is NOT invoked
  RT-11  NO service-management mutation is attempted, asserted through an
         instrumented command boundary rather than a grep for one spelling
  RT-12  the alert fires at most once per re-alert window, and re-arms
  RT-27  a never-written beat on a box where NO run has been recorded is "not
         yet started", not "dead": the watchdog stays silent. A recorded run
         with no beat still alerts (issue #34)
  RT-28  that silence is BOUNDED, and it is granted on POSITIVE evidence.
         `current_run_id` has one write site and no caller, so "no run
         recorded" is an unexplained absence, not proof of a fresh install; the
         grace expires and alerts (security B1). And because `dump` exports no
         `meta`, every restore reproduces that absence, so the grace is gated
         on the installer's own attestation instead (decision R-22)
  RT-30  the attestation lives under NO writable root the Codex sandbox is
         configured to grant, so an agent worker cannot author the fact that
         decides whether the watchdog stays silent. A sandbox-boundary
         invariant, kept separate from RT-28's grace semantics on purpose: it
         must stay greppable on its own the next time someone widens
         `writable_roots`

Everything runs the REAL `deploy/nightshift-watchdog.sh` against a fake project
directory, with `nightshift-ctl`, `gh` and `systemctl` replaced by stubs on PATH
that JOURNAL their argv. No host path is read or written, no network, no issue
is ever commented on.
"""

import json
import os
import re
import stat
import subprocess
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHDOG = os.path.join(REPO, "deploy", "nightshift-watchdog.sh")

# The commit whose grace gate F1 fixed: three conjuncts, no `-le "$now"`.
# RT-28-f1's reproduction table is measured against this exact script.
F1_BASELINE_SHA = "86efd26"
_f1_baseline_cache = []


def baseline_f1_watchdog():
    """The watchdog as of F1_BASELINE_SHA, or None if it cannot be resolved.

    Two sources, in order: $NS_BASELINE_WATCHDOG (an already-extracted copy,
    which is how this runs from a `git archive` export with no history), then
    `git show`. Returns None rather than raising so the suite still runs in a
    bare export - the differential test skips loudly instead of erroring.
    """
    if _f1_baseline_cache:
        return _f1_baseline_cache[0]
    path = None
    override = os.environ.get("NS_BASELINE_WATCHDOG")
    if override and os.path.exists(override):
        path = override
    else:
        try:
            got = subprocess.run(
                ["git", "-C", REPO, "show",
                 "%s:deploy/nightshift-watchdog.sh" % F1_BASELINE_SHA],
                capture_output=True, text=True, timeout=30)
            if got.returncode == 0 and got.stdout:
                fd, tmp = tempfile.mkstemp(prefix="watchdog-baseline-",
                                           suffix=".sh")
                with os.fdopen(fd, "w") as fh:
                    fh.write(got.stdout)
                path = tmp
        except (OSError, subprocess.SubprocessError):
            path = None
    _f1_baseline_cache.append(path)
    return path
BOOTSTRAP = os.path.join(REPO, "deploy", "bootstrap.sh")
LIB = os.path.join(REPO, "deploy", "lib", "deploy-lib.sh")

# The instrumented boundary. Every stub appends its full argv to one journal, so
# "did it try to manage a service?" and "did it renew slots?" are OBSERVED.
FAKE_CTL = r"""#!/usr/bin/env bash
printf 'nightshift-ctl %s\n' "$*" >> "$NS_LOG"
printf 'CWD %s\n' "$PWD" >> "$NS_LOG"
# `--db` is a TOP-LEVEL flag and precedes the subcommand. Strip it so the
# positional dispatch below is unchanged by its presence or absence.
if [ "${1:-}" = "--db" ]; then
  printf 'DB %s\n' "${2:-}" >> "$NS_LOG"
  shift 2
fi
if [ "${1:-}" = "beat" ] && [ "${2:-}" = "show" ]; then
  cat "$NS_VERDICT_BODY" 2>/dev/null
  exit "$(cat "$NS_VERDICT_CODE" 2>/dev/null || echo 3)"
fi
if [ "${1:-}" = "run" ] && [ "${2:-}" = "current" ]; then
  rc="$(cat "$NS_RUN_CODE" 2>/dev/null || echo 0)"
  if [ "$rc" = "0" ]; then
    printf '{"ok": true, "run_id": "run-1"}\n'
  else
    printf '{"ok": false, "reason": "no current run recorded"}\n'
  fi
  exit "$rc"
fi
exit 0
"""

# NS_GH_LIST_FAIL / NS_GH_COMMENT_FAIL inject the two ways owner notification
# actually breaks in the field: an unauthenticated or rate-limited `gh` (issue
# DISCOVERY fails) and a write that is rejected or times out (DELIVERY fails).
# Both were previously untestable because the stub could only succeed, which is
# how a notification path that silences itself shipped unnoticed.
FAKE_GH = r"""#!/usr/bin/env bash
printf 'gh %s\n' "$*" >> "$NS_LOG"
login="${NS_GH_LOGIN:-nightshift-bot}"
arg() { local want="$1"; shift; while [ $# -gt 1 ]; do [ "$1" = "$want" ] && { printf '%s' "$2"; return; }; shift; done; }
case "${1:-}" in
  api)
    [ "${2:-}" = user ] && [ "$(arg --jq "$@")" = .login ] && echo "$login" ;;
  issue)
    case "${2:-}" in
      list)
        if [ -n "${NS_GH_LIST_FAIL:-}" ]; then
          echo "gh: could not determine the current repository" >&2
          exit 1
        fi
        # NS_GH_ISSUES: the repository's open issues as JSON; gh's own
        # --author filter (none given = every author) and --jq are applied
        # to them, as gh would. The default repository holds one issue, 4242,
        # filed by this identity.
        if [ -n "${NS_GH_ISSUES:-}" ]; then
          jq -c --arg a "$(arg --author "$@")" '[.[] | select($a == "" or .author.login == $a)]' \
            "$NS_GH_ISSUES" | jq -r "$(arg --jq "$@")"
        elif [ -z "$(arg --author "$@")" ] || [ "$(arg --author "$@")" = "$login" ]; then
          echo 4242
        fi ;;
      comment)
        if [ -n "${NS_GH_COMMENT_FAIL:-}" ]; then
          echo "gh: HTTP 503 while creating comment" >&2
          exit 1
        fi
        printf 'ALERTED\n' >> "$NS_ALERTS" ;;
    esac ;;
esac
exit 0
"""

FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "$NS_LOG"
echo active
exit 0
"""

MUTATING_VERBS = {
    "start", "stop", "restart", "reload", "try-restart", "reload-or-restart",
    "kill", "enable", "disable", "mask", "unmask", "daemon-reload", "isolate",
    "set-property", "reset-failed",
}


class Box:
    """A fake project box: .omni/, stub binaries, a journal."""

    def __init__(self):
        self.root = tempfile.mkdtemp()
        self.project = os.path.join(self.root, "project")
        self.omni = os.path.join(self.project, ".omni")
        os.makedirs(self.omni)
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        # The state dir is HARDCODED to $HOME/.config/omnigent (ADR-0004
        # Amendment 2, F2). `self.state` is therefore the PARENT of the
        # `omnigent/` directory - it used to be an XDG_STATE_HOME root, and it
        # is now $HOME/.config, so every existing helper below keeps its
        # meaning with no change.
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        self.state = os.path.join(self.home, ".config")
        os.makedirs(self.state)
        # A DECOY. Set as XDG_STATE_HOME on every single run so that "the
        # watchdog no longer honours XDG" is enforced by construction on every
        # test in this file, not asserted once. If the script ever reads XDG
        # again, every attestation-dependent test breaks at once - which is the
        # point: two processes that CANNOT disagree beat two asserted to agree.
        self.decoy = os.path.join(self.root, "xdg-decoy")
        os.makedirs(self.decoy)
        # A normally-bootstrapped box: deploy/bootstrap.sh writes this before it
        # enables the timers (R-22). Default to "installed just now" so every
        # assertion written before R-22 keeps exactly its original meaning.
        os.makedirs(os.path.join(self.state, "omnigent"), exist_ok=True)
        self.bootstrap_age(0)

        self.log = os.path.join(self.root, "calls.log")
        self.alerts = os.path.join(self.root, "alerts.log")
        self.vcode = os.path.join(self.root, "verdict.code")
        self.vbody = os.path.join(self.root, "verdict.json")
        self.rcode = os.path.join(self.root, "run.code")
        # Default: a run IS recorded in the control plane. Every pre-#34 test
        # was written on a box where the org had started, so this default is
        # what keeps their meaning identical.
        with open(self.rcode, "w") as fh:
            fh.write("0")
        for f in (self.log, self.alerts):
            open(f, "w").close()

        for name, body in (("nightshift-ctl", FAKE_CTL), ("gh", FAKE_GH),
                           ("systemctl", FAKE_SYSTEMCTL)):
            p = os.path.join(self.bin, name)
            with open(p, "w") as fh:
                fh.write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # control.db must exist or the old code skipped renewal for the wrong reason
        open(os.path.join(self.omni, "control.db"), "w").close()
        self.alive()

    # -- the Program's verdict, as `beat show` would report it ----------------
    def alive(self, seq=7):
        self._verdict(0, {"alive": True, "who": "program", "age": 12,
                          "record": {"schema_version": 1, "who": "program",
                                     "run_id": "run-1", "seq": seq}})

    def not_alive(self, reason_code="stale", age=99999):
        self._verdict(3, {"alive": False, "who": "program",
                          "reason_code": reason_code,
                          "reason": f"heartbeat is {reason_code}", "age": age})

    # -- has a run ever been recorded? (`run current`, ADR-0002 / issue #34) --
    def no_run_recorded(self):
        """A freshly installed box: `run current` reports none, exit 3."""
        with open(self.rcode, "w") as fh:
            fh.write("3")

    def run_recorded(self):
        """The org has started: `run current` reports a run, exit 0."""
        with open(self.rcode, "w") as fh:
            fh.write("0")

    def run_current_errors(self):
        """`run current` fails in some way that is NOT the 'no run' verdict."""
        with open(self.rcode, "w") as fh:
            fh.write("1")

    def internal_error(self):
        """The C10 case: corrupt/unreadable control.db -> exit 1, NO JSON."""
        with open(self.vcode, "w") as fh:
            fh.write("1")
        with open(self.vbody, "w") as fh:
            fh.write("")

    def _verdict(self, code, obj):
        with open(self.vcode, "w") as fh:
            fh.write(str(code))
        with open(self.vbody, "w") as fh:
            json.dump(obj, fh)

    # -- the noise that used to fool the old watchdog -------------------------
    def backup_writes_now(self):
        """Exactly the writer that made a dead Program read healthy forever."""
        for name in ("control-backup.json", "STATUS.md", "ledger.md"):
            p = os.path.join(self.omni, name)
            with open(p, "w") as fh:
                fh.write("written by the 30-minute backup timer\n")
            os.utime(p, None)

    def omni_is_old(self, hours=99):
        old = time.time() - hours * 3600
        for base, _d, files in os.walk(self.omni):
            for f in files:
                os.utime(os.path.join(base, f), (old, old))

    def status_says_complete(self):
        with open(os.path.join(self.omni, "STATUS.md"), "w") as fh:
            fh.write("Project complete — all conditions PASS\n")

    # -- run the real script --------------------------------------------------
    def run(self, stale_hours=3, realert_hours=6, unarmed_grace_hours=None,
            extra_env=None, script=None):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["NS_LOG"] = self.log
        env["NS_ALERTS"] = self.alerts
        env["NS_VERDICT_CODE"] = self.vcode
        env["NS_VERDICT_BODY"] = self.vbody
        env["NS_RUN_CODE"] = self.rcode
        env["HOME"] = self.home
        # Deliberately hostile: point XDG_STATE_HOME somewhere the watchdog
        # must never look. The systemd user manager exports only HOME and PATH
        # (deploy/systemd/nightshift-watchdog.service), while bootstrap runs
        # under a login shell that may well have XDG_STATE_HOME set - the exact
        # split that made the producer and the consumer resolvable to different
        # directories (staff tech-debt row 1).
        env["XDG_STATE_HOME"] = self.decoy
        env["NIGHTSHIFT_WATCHDOG_STALE_HOURS"] = str(stale_hours)
        env["NIGHTSHIFT_WATCHDOG_REALERT_HOURS"] = str(realert_hours)
        if unarmed_grace_hours is not None:
            env["NIGHTSHIFT_WATCHDOG_UNARMED_GRACE_HOURS"] = str(unarmed_grace_hours)
        # HERMETIC BY CONSTRUCTION (#132). The watchdog now also runs
        # `deploy/nightshift-scheduler-probe.py`, which talks HTTP to the local
        # Omnigent server. Point it at a closed port on every run so the suite
        # never touches a network OR a live scheduler -- and so that every one
        # of the pre-#132 tests below doubles as proof that an UNREACHABLE
        # scheduler changes no verdict in either direction. A probe that could
        # alter an outcome by failing would be the mtime defect rebuilt.
        # Tests that exercise the probe on purpose override this via extra_env.
        env["NIGHTSHIFT_OMNIGENT_URL"] = "http://127.0.0.1:9"
        # The alert pin (B3) must come only from what a test sets, never
        # from the shell running the suite.
        for name in ("NIGHTSHIFT_ALERT_REPO", "NIGHTSHIFT_ALERT_ISSUE"):
            env.pop(name, None)
        env.update(extra_env or {})
        env["NIGHTSHIFT_CTL"] = os.path.join(self.bin, "nightshift-ctl")
        p = subprocess.run(["bash", script or WATCHDOG, self.project],
                           capture_output=True, text=True, env=env, timeout=60)
        return p.returncode, p.stdout + p.stderr

    # -- observations ---------------------------------------------------------
    def calls(self):
        with open(self.log) as fh:
            return [ln.rstrip("\n") for ln in fh]

    def alert_body(self):
        """The FULL alert body. `calls()` is line-split, and the body is
        multi-line, so comparing only the `gh issue comment` line compares the
        header and silently ignores everything the operator actually reads."""
        with open(self.log) as fh:
            raw = fh.read()
        i = raw.find("gh issue comment")
        return "" if i == -1 else raw[i:]

    def alert_count(self):
        with open(self.alerts) as fh:
            return sum(1 for ln in fh if ln.strip())

    def marker_exists(self):
        return os.path.exists(os.path.join(
            self.state, "omnigent", "watchdog-last-alert"))

    def undelivered_exists(self):
        """The delivery-backoff record. DISTINCT from the throttle marker: this
        one says 'the owner has NOT been told', the other says 'the owner HAS'."""
        return os.path.exists(os.path.join(
            self.state, "omnigent", "watchdog-alert-undelivered"))

    def delivery_attempts(self):
        """How many consecutive delivery failures the watchdog has recorded."""
        p = os.path.join(self.state, "omnigent", "watchdog-alert-undelivered")
        if not os.path.exists(p):
            return 0
        with open(p) as fh:
            return int((fh.read().split() or ["0"])[0])

    # -- the installer's attestation (R-22): positive evidence, not absence ---
    def bootstrap_path(self):
        return os.path.join(self.state, "omnigent", "bootstrapped-at")

    def bootstrap_exists(self):
        return os.path.exists(self.bootstrap_path())

    def decoy_files(self):
        """Anything the watchdog wrote under the poisoned XDG_STATE_HOME."""
        found = []
        for base, _d, files in os.walk(self.decoy):
            found.extend(os.path.join(base, f) for f in files)
        return found

    def old_path_attestation(self, hours=0):
        """Write the attestation ONLY where a pre-move box kept it:
        $HOME/.local/state/omnigent/. The new watchdog must not honour it."""
        d = os.path.join(self.home, ".local", "state", "omnigent")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "bootstrapped-at"), "w") as fh:
            fh.write(str(int(time.time() - hours * 3600)))

    def bootstrap_age(self, hours):
        os.makedirs(os.path.dirname(self.bootstrap_path()), exist_ok=True)
        with open(self.bootstrap_path(), "w") as fh:
            fh.write(str(int(time.time() - hours * 3600)))

    def no_bootstrap_record(self):
        """A control plane restored onto a box, or a wiped state dir: the
        installer's attestation is simply not there."""
        if self.bootstrap_exists():
            os.remove(self.bootstrap_path())

    def write_raw_bootstrap(self, text):
        os.makedirs(os.path.dirname(self.bootstrap_path()), exist_ok=True)
        with open(self.bootstrap_path(), "w") as fh:
            fh.write(text)

    def bootstrap_in_future(self, seconds):
        """An attestation dated AHEAD of now. Well-formed digits, impossible
        claim - the installer cannot have run in the future. Produced by a
        wrong clock at install time (a fresh box before NTP settles, a VM
        resumed from a snapshot, a dead RTC) or by tampering."""
        self.write_raw_bootstrap(str(int(time.time()) + seconds))

    def attest_everywhere(self, seconds_from_now):
        """Plant the attestation at BOTH candidate paths, and return its value.

        The post-F2 path ($HOME/.config/omnigent) AND the XDG_STATE_HOME-
        resolved path the pre-F2 script reads. This exists so that a test can
        compare two COMMITS of the script without PATH RESOLUTION being the
        variable: `bootstrap_age()` alone writes only where the post-F2 script
        looks, so the pre-F2 script sees no attestation at all, computes
        installer_at=0, fails the FIRST conjunct and alerts - which looks like
        the fix working while never once exercising the date. Planting both
        leaves the DATE as the only difference between the two runs.
        """
        val = str(int(time.time()) + seconds_from_now)
        for d in (os.path.join(self.state, "omnigent"),
                  os.path.join(self.decoy, "omnigent")):
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "bootstrapped-at"), "w") as fh:
                fh.write(val)
        return val

    def attest_nowhere(self):
        """Remove the attestation from BOTH candidate paths (installer_at=0)."""
        for d in (os.path.join(self.state, "omnigent"),
                  os.path.join(self.decoy, "omnigent")):
            p = os.path.join(d, "bootstrapped-at")
            if os.path.exists(p):
                os.remove(p)

    # -- the unarmed stamp: bounds the not-yet-started grace (RT-28) ----------
    def stamp_path(self):
        return os.path.join(self.state, "omnigent", "watchdog-unarmed-since")

    def stamp_exists(self):
        return os.path.exists(self.stamp_path())

    def stamp_value(self):
        with open(self.stamp_path()) as fh:
            return fh.read().strip()

    def stamp_age(self, hours):
        """Backdate the unarmed stamp, as if this box had been sitting
        un-armable for `hours`."""
        os.makedirs(os.path.dirname(self.stamp_path()), exist_ok=True)
        with open(self.stamp_path(), "w") as fh:
            fh.write(str(int(time.time() - hours * 3600)))

    def write_raw_stamp(self, text):
        os.makedirs(os.path.dirname(self.stamp_path()), exist_ok=True)
        with open(self.stamp_path(), "w") as fh:
            fh.write(text)

    def db_args(self):
        """Every --db path the watchdog passed to nightshift-ctl."""
        return [c.split(None, 1)[1] for c in self.calls() if c.startswith("DB ")]

    def run_current_calls(self):
        return [c for c in self.calls() if "run current" in c]

    def renew_active_calls(self):
        return [c for c in self.calls() if "renew-active" in c]

    def service_mutations(self):
        out = []
        for c in self.calls():
            if not c.startswith("systemctl "):
                continue
            for tok in c.split()[1:]:
                if tok.startswith("-"):
                    continue
                if tok in MUTATING_VERBS:
                    out.append(c)
                break
        return out


class RT08StaleDespiteBackupNoise(unittest.TestCase):
    """RT-08 — the defect itself. Backup churn must not read as liveness."""

    def test_rt08_backup_writes_do_not_suppress_the_alert(self):
        b = Box()
        b.not_alive("stale")
        b.backup_writes_now()        # every .omni mtime is NOW
        code, out = b.run()
        self.assertEqual(code, 0, f"the watchdog must never exit non-zero: {out}")
        self.assertEqual(b.alert_count(), 1,
                         "a dead Program with a fresh backup file read healthy — "
                         "this is the exact bug (mtime instead of content)")

    def test_rt08_the_alert_carries_the_reason_code(self):
        b = Box()
        b.not_alive("stale", age=54321)
        b.backup_writes_now()
        b.run()
        gh = " ".join(c for c in b.calls() if c.startswith("gh issue comment"))
        self.assertIn("stale", gh, f"reason_code must reach the operator: {gh}")

    def test_rt08_liveness_is_never_decided_from_mtime(self):
        # Positive control: the ONLY thing that changed between these two runs
        # is the heartbeat verdict. Mtimes are fresh in both.
        b = Box()
        b.backup_writes_now()
        b.alive()
        b.run()
        self.assertEqual(b.alert_count(), 0, "fresh heartbeat must be quiet")

        b2 = Box()
        b2.backup_writes_now()
        b2.not_alive("stale")
        b2.run()
        self.assertEqual(b2.alert_count(), 1, "dead heartbeat must alert")

    def test_rt08_a_previous_boot_record_does_not_suppress(self):
        for reason in ("wrong_boot", "future", "malformed", "missing", "wrong_run"):
            with self.subTest(reason=reason):
                b = Box()
                b.not_alive(reason)
                b.backup_writes_now()
                b.run()
                self.assertEqual(b.alert_count(), 1,
                                 f"{reason} must not read as alive")

    def test_rt08_an_unreadable_control_plane_still_alerts(self):
        # LIVENESS-RECORD v1 §3 says output is always one JSON object; on a
        # corrupt/unreadable control.db `beat show` exits 1 with EMPTY stdout.
        # Raised with the program as a contract finding. The watchdog must fail
        # SAFE here: a control plane it cannot read is not evidence of life, and
        # it must not crash trying to parse absent JSON.
        b = Box()
        b.internal_error()
        b.backup_writes_now()
        code, out = b.run()
        self.assertEqual(code, 0, f"must not crash on absent JSON: {out}")
        self.assertEqual(b.alert_count(), 1,
                         "an unreadable control plane must alert, not go quiet")


class RT09ValidRecordIsAlive(unittest.TestCase):
    """RT-09 — a valid Program record is judged alive."""

    def test_rt09_alive_is_quiet(self):
        b = Box()
        b.alive()
        b.omni_is_old(99)            # mtimes ancient; content says alive
        code, out = b.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(b.alert_count(), 0,
                         "a live Program with an idle .omni must not alert — "
                         "content decides, not mtime")

    def test_rt09_asks_the_PROJECT_control_plane(self):
        """Regression: the first cut queried `beat show` from the watchdog's own
        cwd. nightshift-ctl resolves .omni/control.db relative to $PWD, so it
        consulted the wrong database and reported a LIVE Program as
        `unavailable` — a false alert on every tick. Caught only by running
        against the real ctl, which is why this assertion now exists."""
        b = Box()
        b.alive()
        b.run()
        cwds = [c.split(None, 1)[1] for c in b.calls() if c.startswith("CWD ")]
        self.assertTrue(cwds, "the control plane was never consulted")
        real = os.path.realpath(b.project)
        self.assertTrue(
            all(os.path.realpath(c) == real for c in cwds),
            f"beat show must be asked from the project dir; got {cwds}, want {real}")

    def test_rt09_asks_the_control_plane_by_owner(self):
        b = Box()
        b.alive()
        b.run()
        beats = [c for c in b.calls() if "beat show" in c]
        self.assertTrue(beats, f"the watchdog never consulted the heartbeat: {b.calls()}")
        self.assertIn("--who", " ".join(beats),
                      "beat show must be scoped to an owner (contract §4)")
        self.assertIn("program", " ".join(beats))


class RT10NoSlotRenewal(unittest.TestCase):
    """RT-10 — the watchdog renews NOTHING. Not when stale, not when alive."""

    def test_rt10_stale_does_not_renew(self):
        b = Box()
        b.not_alive("stale")
        b.run()
        self.assertEqual(b.renew_active_calls(), [])

    def test_rt10_alive_does_not_renew_either(self):
        # The architect's case 7: a fresh heartbeat is NOT a licence to renew
        # every active slot. Program liveness is not evidence that any
        # particular worker is progressing (ADR-0002 §4).
        b = Box()
        b.alive()
        b.backup_writes_now()
        b.run()
        self.assertEqual(b.renew_active_calls(), [],
                         "org-wide renewal on a Program-level signal is exactly "
                         "the per-worker-lease-to-org-wide-lease defect")

    def test_rt10_the_verb_itself_is_not_deleted_by_this_change(self):
        # ADR-0002 one-way-door: `slot renew-active` is removed in a LATER PR,
        # after worker-owned renewal is observed working. This PR only stops the
        # watchdog calling it. Guard against over-reach in the wrong direction.
        # Comments MAY discuss renew-active — the staging rule is worth
        # explaining in the file. Executable code may not CALL it.
        with open(WATCHDOG, errors="replace") as fh:
            code = "\n".join(ln.split("#", 1)[0] for ln in fh)
        self.assertNotIn("renew-active", code,
                         "the watchdog must not call renew-active (ADR-0002 §4); "
                         "the verb itself stays in nightshift-ctl for a later PR")


class RT11NoServiceMutation(unittest.TestCase):
    """RT-11 — no service management, proven at the command boundary."""

    def test_rt11_no_mutating_systemctl_on_any_path(self):
        for setup in ("alive", "stale", "error"):
            with self.subTest(path=setup):
                b = Box()
                if setup == "alive":
                    b.alive()
                elif setup == "stale":
                    b.not_alive("stale")
                else:
                    b.internal_error()
                b.run()
                self.assertEqual(
                    b.service_mutations(), [],
                    f"the watchdog attempted service management on the {setup} "
                    f"path — restarting a service kills in-flight worker turns")

    def test_rt11_the_instrumented_boundary_can_actually_fire(self):
        # A detector that cannot detect proves nothing. Prove it fires.
        b = Box()
        with open(b.log, "a") as fh:
            fh.write("systemctl --user restart omnigent-host\n")
        self.assertEqual(len(b.service_mutations()), 1)


class RT12AlertRateLimitAndRearm(unittest.TestCase):
    """RT-12 — at most once per window, and it re-arms after recovery."""

    def test_rt12_second_run_inside_the_window_is_silent(self):
        b = Box()
        b.not_alive("stale")
        b.run(); b.run(); b.run()
        self.assertEqual(b.alert_count(), 1,
                         "the phone must not be spammed once per timer tick")

    def test_rt12_rearms_after_recovery(self):
        b = Box()
        b.not_alive("stale")
        b.run()
        self.assertEqual(b.alert_count(), 1)
        b.alive()
        b.run()                      # recovery disarms
        self.assertEqual(b.alert_count(), 1)
        b.not_alive("stale")
        b.run()                      # and it fires again
        self.assertEqual(b.alert_count(), 2,
                         "after recovery the alert must re-arm")


class TerminalStateIsNotMarkdown(unittest.TestCase):
    """ADR-0002 §6 — the word 'complete' in Markdown is not a control signal."""

    def test_status_md_saying_complete_does_not_silence_the_watchdog(self):
        b = Box()
        b.not_alive("stale")
        b.status_says_complete()
        b.backup_writes_now()
        b.run()
        self.assertEqual(
            b.alert_count(), 1,
            "STATUS.md's first line containing 'complete' silenced a dead "
            "Program — terminal state must come from a control-plane "
            "transition (ADR-0002 §6)")

    def test_watchdog_does_not_grep_status_md_for_liveness(self):
        # The alert BODY legitimately mentions STATUS.md — the recovery
        # one-liner tells the operator to read it, and the body says the verdict
        # is explicitly not taken from it. What must not exist is the watchdog
        # READING it to decide anything. So look for reader constructs, not for
        # the string. (`read` is excluded: it is a stdin builtin, and it appears
        # in the recovery prose.)
        with open(WATCHDOG, errors="replace") as fh:
            code = "\n".join(ln.split("#", 1)[0] for ln in fh)
        reader = re.compile(r"(?:\b(?:head|cat|grep|sed|awk|tail|source)\b|<)"
                            r"[^\n]*STATUS\.md")
        hits = reader.findall(code)
        self.assertEqual(hits, [],
                         f"the watchdog reads STATUS.md to decide something: {hits}")
        # positive control: the scanner must be able to fire
        self.assertTrue(reader.search('x=$(head -n1 "$P/.omni/STATUS.md")'),
                        "the STATUS.md reader scanner matches nothing")


class AlertTargetCannotBeCaptured(unittest.TestCase):
    """Audit B3: the alert went to `.[0]` of ANY open issue whose title matched
    "Program status", so a stranger's issue could capture the dead-man's
    switch. The target is now pinned, or else limited to this identity's own
    exact-title issue."""

    def stale_box(self):
        b = Box()
        b.not_alive("stale")
        return b

    def issues(self, b, rows):
        path = os.path.join(b.root, "issues.json")
        with open(path, "w") as fh:
            json.dump(rows, fh)
        return {"NS_GH_ISSUES": path}

    def comments(self, b):
        return [c for c in b.calls() if c.startswith("gh issue comment")]

    def test_a_strangers_program_status_issue_never_receives_the_alert(self):
        b = self.stale_box()
        env = self.issues(b, [
            {"number": 666, "title": "Program status", "author": {"login": "mallory"}}])
        code, out = b.run(extra_env=env)
        self.assertEqual(code, 0, out)
        self.assertEqual(b.alert_count(), 0)
        self.assertEqual(self.comments(b), [])
        self.assertTrue(b.undelivered_exists(), "the miss must stay visibly undelivered")
        self.assertIn("not pinned", out)

    def test_unpinned_delivery_goes_to_this_identitys_exact_title_issue(self):
        b = self.stale_box()
        env = self.issues(b, [
            {"number": 666, "title": "Program status", "author": {"login": "mallory"}},
            {"number": 41, "title": "Program status v2", "author": {"login": "nightshift-bot"}},
            {"number": 42, "title": "Program status", "author": {"login": "nightshift-bot"}}])
        code, out = b.run(extra_env=env)
        self.assertEqual(code, 0, out)
        self.assertEqual(b.alert_count(), 1)
        self.assertTrue(self.comments(b)[0].startswith("gh issue comment 42 --body"))

    def test_a_pin_in_the_environment_is_used_without_any_search(self):
        b = self.stale_box()
        code, out = b.run(extra_env={"NIGHTSHIFT_ALERT_REPO": "acme/product",
                                     "NIGHTSHIFT_ALERT_ISSUE": "3"})
        self.assertEqual(code, 0, out)
        self.assertEqual(b.alert_count(), 1)
        self.assertTrue(self.comments(b)[0].startswith(
            "gh issue comment 3 --repo acme/product --body"))
        self.assertFalse(any(c.startswith("gh issue list") for c in b.calls()))

    def test_the_detector_env_file_pins_it_too(self):
        b = self.stale_box()
        with open(os.path.join(b.state, "omnigent", "scheduled-run-detector.env"), "w") as fh:
            fh.write("NIGHTSHIFT_SCHEDULED_TASK_ID=x\n"
                     'NIGHTSHIFT_ALERT_REPO="acme/product"\nNIGHTSHIFT_ALERT_ISSUE=7\n')
        code, out = b.run()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.comments(b)[0].startswith(
            "gh issue comment 7 --repo acme/product --body"))

    def test_a_half_set_or_malformed_pin_never_falls_back_to_a_search(self):
        for env in ({"NIGHTSHIFT_ALERT_ISSUE": "3"},
                    {"NIGHTSHIFT_ALERT_REPO": "acme/product", "NIGHTSHIFT_ALERT_ISSUE": "3;id"}):
            with self.subTest(env=env):
                b = self.stale_box()
                code, out = b.run(extra_env=env)
                self.assertEqual(code, 0, out)
                self.assertEqual(b.alert_count(), 0)
                self.assertTrue(b.undelivered_exists())
                self.assertIn("alert pin is malformed", out)
                self.assertFalse(any(c.startswith("gh issue") for c in b.calls()))


class NeverFailsLoud(unittest.TestCase):
    """A watchdog that crashes its own timer is worse than no watchdog."""

    def test_exit_zero_even_with_no_control_plane_at_all(self):
        b = Box()
        os.remove(os.path.join(b.bin, "nightshift-ctl"))
        code, out = b.run()
        self.assertEqual(code, 0, f"must never exit non-zero: {out}")

    def test_exit_zero_when_the_project_is_not_initialised(self):
        b = Box()
        for f in os.listdir(b.omni):
            os.remove(os.path.join(b.omni, f))
        os.rmdir(b.omni)
        code, out = b.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(b.alert_count(), 0, "an uninitialised box is not a dead org")


class RT27FirstBootIsNotADeadOrg(unittest.TestCase):
    """RT-27 - issue #34, the cutover the fix to defect 4 created.

    Defect 4 was a watchdog whose liveness signal was always-true, which made it
    useless. Making the signal REAL gave it something an always-true signal
    never had: a moment before the signal exists. On a freshly installed box the
    Program has not completed a loop yet, so `beat show` correctly answers
    `missing` - and arming the timer then fires STALE alerts on a perfectly
    healthy system, which is the false-alert class R-14 guards, arriving from
    the opposite direction.

    The distinguishing fact is NOT in the heartbeat record - absence of a record
    cannot say why it is absent. It comes from `run current`, which the control
    plane writes ONCE at run start, by a different command than the heartbeat,
    which is what makes it independent evidence rather than the record checking
    itself (nightshift-ctl:expected_run_id).

    Direction of the grace, deliberately narrow:
      no beat AND no run recorded -> not yet started -> SILENT
      no beat AND a run recorded  -> alert (this is the real "was alive, now
                                    missing", and also "started but wedged
                                    before the first loop")
    Anything other than `missing`, and any `run current` failure that is not
    the explicit exit-3 "no run" verdict, keeps alerting. The grace is granted
    only on positive evidence; absence of evidence does not buy silence.
    """

    def test_rt27_never_started_box_does_not_alert(self):
        """The issue itself: arm the watchdog against a never-written beat."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        code, out = b.run()
        self.assertEqual(code, 0, f"the watchdog must never exit non-zero: {out}")
        self.assertEqual(
            b.alert_count(), 0,
            "a box where no run has ever been recorded and no beat has ever "
            "been written is NOT a dead org - it is an org that has not "
            "started, and alerting on it fires STALE on every fresh install")

    def test_rt27_never_started_box_does_not_burn_the_realert_window(self):
        """Silence must be real silence: no marker, so the FIRST genuine alert
        after the org starts is not swallowed by the re-alert throttle."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.run()
        self.assertFalse(
            b.marker_exists(),
            "the not-yet-started path wrote the alert marker; the first real "
            "alert after startup would then be throttled away")

    def test_rt27_a_recorded_run_with_no_beat_STILL_alerts(self):
        """The fail-closed half. This is the ONLY thing separating the fix from
        simply deleting the `missing` alert."""
        b = Box()
        b.not_alive("missing")
        b.run_recorded()
        code, out = b.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(
            b.alert_count(), 1,
            "a run IS recorded but the Program has never written a heartbeat "
            "for it - that is 'started and never ran' or 'was alive, now "
            "missing', and both must alert")

    def test_rt27_only_missing_is_ever_graced(self):
        """A beat that EXISTS but is stale/wrong-boot/malformed is evidence the
        org did start, whatever `run current` says. Grace must not leak."""
        for reason in ("stale", "wrong_boot", "malformed", "wrong_run",
                       "future", "unknown_version", "control_plane_unreadable"):
            with self.subTest(reason=reason):
                b = Box()
                b.not_alive(reason)
                b.no_run_recorded()
                b.run()
                self.assertEqual(
                    b.alert_count(), 1,
                    f"{reason} was silenced by the not-yet-started grace; the "
                    "grace is only for a beat that was NEVER written")

    def test_rt27_an_unreadable_control_plane_is_not_a_fresh_install(self):
        """`beat show` exits 1 with no JSON (pre-#16 shape) and `run current`
        errors rather than answering. Nothing here is positive evidence of a
        fresh install, so it must alert."""
        b = Box()
        b.internal_error()
        b.run_current_errors()
        code, out = b.run()
        self.assertEqual(code, 0, f"must not crash: {out}")
        self.assertEqual(
            b.alert_count(), 1,
            "an unreadable control plane must never be mistaken for a box "
            "that has not started yet - that is silence on the exact failure "
            "the dead-man's switch exists to catch")

    def test_rt27_run_current_is_asked_of_the_PROJECT_control_plane(self):
        """Same trap as RT-09: nightshift-ctl resolves .omni/control.db from
        $PWD, so asking from the wrong cwd consults the wrong plane - and
        issue #31 says a wrong cwd silently CREATES an empty one, which would
        answer 'no run recorded' and grant the grace on a live box."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.run()
        self.assertTrue(b.run_current_calls(),
                        f"`run current` was never consulted: {b.calls()}")
        cwds = [c.split(None, 1)[1] for c in b.calls() if c.startswith("CWD ")]
        real = os.path.realpath(b.project)
        self.assertTrue(
            all(os.path.realpath(c) == real for c in cwds),
            f"every ctl query must be asked from the project dir; got {cwds}")

    def test_rt27_the_grace_is_not_paid_for_on_every_healthy_tick(self):
        """A live org must not incur a second control-plane query per tick."""
        b = Box()
        b.alive()
        b.run()
        self.assertEqual(
            b.run_current_calls(), [],
            "the alive path must short-circuit before asking `run current`")


class RT27FirstBootPathEndToEnd(unittest.TestCase):
    """RT-27 - the first-boot path RUN AS A SEQUENCE, in one box.

    This path executes exactly once per install and is therefore the least
    tested code in the system. Each state below is asserted on the SAME box, in
    the order a real install produces them, because the bug being fixed is a
    TRANSITION, and three independent tests of three states cannot observe a
    transition between them.
    """

    def test_rt27_install_then_start_then_first_beat(self):
        b = Box()

        # 1. Freshly bootstrapped: units installed, timer armed, org not started.
        b.not_alive("missing")
        b.no_run_recorded()
        code, _ = b.run()
        self.assertEqual(code, 0)
        self.assertEqual(b.alert_count(), 0,
                         "install-time tick alerted on a healthy fresh box")
        self.assertFalse(b.marker_exists())

        # 2. Timer ticks again before anyone starts the org. Still silent, and
        #    still not consuming the alert window.
        b.run()
        self.assertEqual(b.alert_count(), 0,
                         "repeated pre-start ticks must stay silent")

        # 3. `run current --set` lands: the org has started, but the Program has
        #    not completed its first loop yet. The grace is OVER - from here a
        #    missing beat is a real fault.
        b.run_recorded()
        b.run()
        self.assertEqual(b.alert_count(), 1,
                         "once a run is recorded, a missing beat is a fault "
                         "and must alert - the grace must not persist")

        # 4. The Program completes its first loop and writes beat seq=1.
        #    Healthy, silent, and the alert re-arms.
        b.alive(seq=1)
        b.run()
        self.assertEqual(b.alert_count(), 1, "a live Program must be quiet")
        self.assertFalse(b.marker_exists(),
                         "recovery must disarm the throttle marker")

        # 5. The Program later dies; its last beat goes stale. Alerts again,
        #    proving the switch is still armed after the whole cutover.
        b.not_alive("stale")
        b.run()
        self.assertEqual(b.alert_count(), 2,
                         "after the first-boot cutover the dead-man's switch "
                         "must still fire on a genuinely dead Program")


class RT28TheGraceIsBounded(unittest.TestCase):
    """RT-28 - `security` finding B1 on PR #35.

    RT-27's grace fires when `run current` exits 3, i.e. when the `meta` row
    `current_run_id` is ABSENT. That row has exactly ONE write site in the tree
    (`run current --set`) and NOTHING calls it - not bootstrap.sh, not any unit,
    not preflight.sh. `cmd_run_start` inserts into `agent_runs` and never
    touches `meta`, so the plane can prove the org started and still answer
    "no run recorded".

    So the grace's own trigger is an unexplained ABSENCE, and the exposure
    window is "no beat AND no current_run_id" - which is precisely *the org was
    supposed to start and never wrote its first heartbeat*, the failure the
    dead-man's switch exists to catch. RT-27 made that silent FOREVER.

    RT-27's own principle is what convicts it: absence of evidence never buys
    silence. Two unexplained absences are not evidence of a fresh install.

    The bound: stamp when first seen unarmed, stay silent inside the window,
    ALERT after it. The stamp is a DISTINCT file from the alert marker, so the
    throttle is untouched. Every way the bound could itself fail open - a
    garbled stamp, a stamp that cannot be persisted, a stamp that silently
    renews itself - resolves toward alerting.
    """

    def test_rt28_an_expired_unarmed_state_alerts(self):
        """The finding itself. A box that has been un-armable for longer than
        the grace is not installing - it is broken, and must say so."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.stamp_age(25)
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, f"the watchdog must never exit non-zero: {out}")
        self.assertEqual(
            b.alert_count(), 1,
            "a box with no heartbeat and no recorded run for longer than the "
            "grace stayed SILENT - that is 'the org never wrote its first "
            "heartbeat', silenced forever (security B1)")

    def test_rt28_the_expired_alert_names_the_state(self):
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.stamp_age(25)
        b.run(unarmed_grace_hours=24)
        gh = " ".join(c for c in b.calls() if c.startswith("gh issue comment"))
        self.assertIn(
            "never_started", gh,
            "the operator must be told this is 'provisioned but never started', "
            f"not a generic stale heartbeat: {gh}")

    def test_rt28_inside_the_window_is_still_silent(self):
        """RT-27's behaviour must survive: a genuine fresh install is quiet."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.stamp_age(1)
        b.run(unarmed_grace_hours=24)
        self.assertEqual(b.alert_count(), 0,
                         "a box 1h into a 24h grace must stay silent")

    def test_rt28_the_first_ever_tick_stamps_and_stays_silent(self):
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        self.assertFalse(b.stamp_exists())
        b.run(unarmed_grace_hours=24)
        self.assertEqual(b.alert_count(), 0)
        self.assertTrue(b.stamp_exists(),
                        "the first unarmed tick must record WHEN the grace "
                        "started, or the grace cannot be bounded at all")
        self.assertRegex(b.stamp_value(), r"^\d+$")

    def test_rt28_the_grace_does_not_renew_itself_on_every_tick(self):
        """The subtle fail-open: re-stamping each tick makes the window slide
        forward forever, which is an unbounded grace wearing a bound's clothes.
        """
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.run(unarmed_grace_hours=24)
        first = b.stamp_value()
        b.run(unarmed_grace_hours=24)
        b.run(unarmed_grace_hours=24)
        self.assertEqual(b.stamp_value(), first,
                         "the unarmed stamp moved forward on a later tick - "
                         "the grace renews itself and never expires")

    def test_rt28_the_stamp_is_not_the_alert_marker(self):
        """RT-27 asserts the graced path writes no alert marker. The bound must
        not smuggle one in under a different name, or the first real alert
        after startup is throttled away."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.run(unarmed_grace_hours=24)
        self.assertTrue(b.stamp_exists())
        self.assertFalse(b.marker_exists(),
                         "the graced path wrote the alert throttle marker")
        self.assertNotEqual(
            os.path.realpath(b.stamp_path()),
            os.path.realpath(os.path.join(b.state, "omnigent",
                                          "watchdog-last-alert")),
            "the stamp and the alert marker must be different files")

    def test_rt28_a_garbled_stamp_is_treated_as_EXPIRED(self):
        """An unreadable bound is not a bound. Fail closed."""
        for junk in ("", "   ", "not-a-timestamp", "-17", "99999999999999999999x"):
            with self.subTest(stamp=junk):
                b = Box()
                b.not_alive("missing")
                b.no_run_recorded()
                b.write_raw_stamp(junk)
                b.run(unarmed_grace_hours=24)
                self.assertEqual(
                    b.alert_count(), 1,
                    f"a stamp of {junk!r} could not be read as a time, so the "
                    "grace has no known start and must not be granted")

    def test_rt28_a_stamp_that_cannot_be_PERSISTED_does_not_buy_silence(self):
        """If the stamp cannot be written, every tick looks like the first one
        and the grace never expires. That must alert instead."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        d = os.path.join(b.state, "omnigent")
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o500)                      # readable, not writable
        try:
            code, out = b.run(unarmed_grace_hours=24)
            self.assertEqual(code, 0, f"must not crash: {out}")
            self.assertEqual(
                b.alert_count(), 1,
                "the grace start could not be persisted, so every tick would "
                "restart the window - an unbounded grace. It must alert")
        finally:
            os.chmod(d, 0o700)

    def test_rt28_a_recorded_run_ignores_the_stamp_entirely(self):
        """The stamp bounds ONLY the not-yet-started grace. A recorded run with
        no beat alerts immediately, stamp or no stamp."""
        b = Box()
        b.not_alive("missing")
        b.run_recorded()
        b.stamp_age(0)
        b.run(unarmed_grace_hours=24)
        self.assertEqual(b.alert_count(), 1,
                         "a recorded run with no beat must alert at once")

    def test_rt28_ctl_is_pinned_to_the_PROJECT_plane_with_db(self):
        """`--db` defaults to os.environ['NIGHTSHIFT_CTL_DB'] before
        DEFAULT_DB, and DEFAULT_DB is RELATIVE (issue #31). Passing --db
        explicitly is what stops a stray env var or a surprising cwd pointing
        the dead-man's switch at an empty plane that answers 'no run
        recorded'."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.run(unarmed_grace_hours=24)
        dbs = b.db_args()
        self.assertTrue(dbs, f"no --db was passed to nightshift-ctl: {b.calls()}")
        want = os.path.realpath(os.path.join(b.project, ".omni", "control.db"))
        self.assertTrue(
            all(os.path.realpath(d) == want for d in dbs),
            f"every ctl call must address the project plane; got {dbs}")

    def test_rt28_a_stray_NIGHTSHIFT_CTL_DB_cannot_redirect_the_watchdog(self):
        """security residual 2: an exported NIGHTSHIFT_CTL_DB pointing at an
        empty plane would answer 'no run recorded' and silence the switch."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        env_db = os.path.join(b.root, "decoy.db")
        b.run(unarmed_grace_hours=24,
              extra_env={"NIGHTSHIFT_CTL_DB": env_db})
        dbs = b.db_args()
        self.assertTrue(dbs)
        self.assertTrue(all(os.path.realpath(d) != os.path.realpath(env_db)
                            for d in dbs))
        want = os.path.realpath(os.path.join(b.project, ".omni", "control.db"))
        self.assertEqual(os.path.realpath(dbs[0]), want,
                         "an explicit --db must win over the environment")


class RT28GraceNeedsPositiveEvidence(unittest.TestCase):
    """RT-28 (extension) - decision R-22, from `staff`'s review of ADR-0004.

    The bound narrowed the absence-keyed grace; it did not close it. The grace
    still triggered on `run current` exiting 3, and that absence is NOT
    discriminating, because `nightshift-ctl dump` exports slots, claims, leases,
    tasks, checkpoints and vendors - and NOT `meta`. Measured on this repo's own
    `.omni/control-backup.json`: no `meta` key, no beats.

    So every restore from the project's documented backup path yields exactly
    "no current_run_id, no beats", and `nightshift-state-backup.sh` runs every
    30 minutes so that path gets used. An absence-keyed grace goes quiet on a
    genuinely dead Program as the GUARANTEED outcome of recovery.

    ADR-0004's own Option D argument, not applied to its own design: a recreated
    database has no memory that a key once existed either. `current_run_id` and
    the beat are independent in WHO WRITES THEM and perfectly correlated in WHAT
    DESTROYS THEM. The discriminating fact needs an independent DESTRUCTION
    mode, which is why it is a file written by the installer and not a `meta`
    row that the backup drops.
    """

    def test_rt28_no_installer_attestation_alerts(self):
        """The R-22 case. Absence of evidence must buy nothing - including the
        absence that the grace itself keys on."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.no_bootstrap_record()
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, f"must never exit non-zero: {out}")
        self.assertEqual(
            b.alert_count(), 1,
            "no heartbeat, no recorded run AND no installer attestation - "
            "nothing here is positive evidence of a fresh install, so the "
            "watchdog must not grant itself silence")

    def test_rt28_a_restored_plane_on_an_ESTABLISHED_box_alerts_at_once(self):
        """The decisive case, and the one the bounded-but-absence-keyed version
        got wrong: a 30-day-old box whose control plane was just restored. The
        unarmed stamp is FRESH (this is the first tick that could not arm), so
        the 24h bound alone would stay silent for a further 24h with a dead
        Program. The installer attestation is what makes it immediate."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.bootstrap_age(24 * 30)      # installed a month ago
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            b.alert_count(), 1,
            "an empty control plane on a box installed a month ago is a "
            "RESTORE, not an install, and must alert immediately rather than "
            "after another 24h of silence")

    def test_rt28_a_freshly_bootstrapped_box_is_still_graced(self):
        """R-22 must not undo #34: a genuinely fresh install stays quiet."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.bootstrap_age(0)
        b.run(unarmed_grace_hours=24)
        self.assertEqual(b.alert_count(), 0,
                         "a box bootstrapped moments ago, with no beat yet, is "
                         "the healthy first-boot case and must stay silent")

    def test_rt28_the_attestation_expires_with_the_grace(self):
        for hours, want in ((23, 0), (25, 1)):
            with self.subTest(bootstrapped_hours_ago=hours):
                b = Box()
                b.not_alive("missing")
                b.no_run_recorded()
                b.bootstrap_age(hours)
                b.run(unarmed_grace_hours=24)
                self.assertEqual(
                    b.alert_count(), want,
                    f"a box bootstrapped {hours}h ago with no beat and no run "
                    f"should {'alert' if want else 'stay silent'}")

    def test_rt28_a_garbled_attestation_alerts(self):
        """An attestation that cannot be read is not positive evidence.

        The last two entries are NOT garbled - they are well-formed digits,
        and that is exactly why they were missed (F1). `now - installer_at`
        goes NEGATIVE for a future date, and a negative number is always less
        than the grace window, so the future-dated case did not merely slip
        past the guard: it was the one input that could never fail it. An
        attestation dated 2027 bought silence forever.
        """
        cases = [("", ""), ("   ", "   "), ("yesterday", "yesterday"),
                 ("-5", "-5"), ("12x34", "12x34"),
                 ("now+3600", str(int(time.time()) + 3600)),
                 ("now+365d", str(int(time.time()) + 365 * 86400))]
        for label, junk in cases:
            with self.subTest(stamp=label):
                b = Box()
                b.not_alive("missing")
                b.no_run_recorded()
                b.write_raw_bootstrap(junk)
                b.run(unarmed_grace_hours=24)
                self.assertEqual(
                    b.alert_count(), 1,
                    f"an attestation of {label!r} attests nothing an installer "
                    "could have done, so it must not buy silence")

    def test_rt28_a_future_attestation_cannot_rescue_an_ESTABLISHED_box(self):
        """R-22's headline case, defeated by F1.

        `test_rt28_a_restored_plane_on_an_ESTABLISHED_box_alerts_at_once` is
        the reason the attestation exists. Re-date that same box's attestation
        into the future and the whole R-22 gate inverts: the box is 30 days
        old, its control plane was just restored, the Program is dead - and
        the watchdog goes quiet. Measured at 86efd26: alerts = 0.

        This is the failure the fix must make impossible, so it is asserted on
        its own rather than only as a subTest of the unreadable list."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.bootstrap_in_future(365 * 86400)
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, f"must never exit non-zero: {out}")
        self.assertEqual(
            b.alert_count(), 1,
            "a future-dated attestation made `now - installer_at` negative, "
            "which is unconditionally inside the grace window - so the one "
            "case R-22 was built to catch (a restored plane on an established "
            "box) was silenced by the very gate that was supposed to catch it")

    def test_rt28_a_future_attestation_gets_its_OWN_operator_story(self):
        """F4: 'the installer ran in the future' and 'no installer ran' are
        different faults with different recoveries - check the clock and the
        file's provenance, versus re-run the installer. They must not render
        identically, for the same reason the missing/stale pair must not."""
        b = Box()
        b.not_alive("missing"); b.no_run_recorded()
        b.bootstrap_in_future(3600)
        b.run(unarmed_grace_hours=24)
        future_story = b.alert_body()

        b2 = Box()
        b2.not_alive("missing"); b2.no_run_recorded(); b2.no_bootstrap_record()
        b2.run(unarmed_grace_hours=24)
        missing_story = b2.alert_body()

        self.assertIn("never_started", future_story)
        self.assertIn("future", future_story,
                      "the alert must say the attestation is dated in the "
                      "future - otherwise the operator is sent to re-run an "
                      "installer that already ran, and the wrong clock (the "
                      "actual fault) survives the repair")
        self.assertNotEqual(
            future_story, missing_story,
            "a future-dated attestation and an absent one rendered "
            "identically; one is a clock or tampering fault, the other is a "
            "restored or wiped state directory")

    # -- RT-28-f1's reproduction table, as shipped tests ---------------------
    #
    # The correction commit 479af20 demolished all four archived RT-28 failures
    # (they failed for harness reasons, not for F1), which left the four-row
    # table in docs/audit/baseline/RT-28-f1.txt as the ONLY evidence in the tree
    # that F1 was ever real - and a table in a text file cannot be re-run. These
    # two tests ARE that table. Rows: attestation dated +48h / +10y (future),
    # -30d (established box) and -1h (genuine fresh install), all planted at
    # BOTH candidate paths so only the DATE varies. `posted` in the table is
    # Box.alert_count().
    #
    # WA-009: the controls (-30d must ALERT, -1h must stay SILENT) are asserted
    # beside the counterfactual on BOTH sides, and the baseline test separately
    # proves the future-dated silence is caused by the DATE and not by an
    # unread attestation.

    F1_ROWS = (
        ("+48h future", 48 * 3600, "alerts"),
        ("+10y future", 10 * 365 * 86400, "alerts"),
        ("-30d established", -30 * 86400, "alerts"),
        ("-1h fresh install", -3600, "silent"),
    )

    def _f1_box(self, offset, script=None):
        """An established box whose plane came back with the Program dead."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.attest_everywhere(offset)
        b.run(unarmed_grace_hours=24, script=script)
        return b

    def test_rt28_f1_reproduction_table_the_HEAD_column(self):
        """RT-28-f1 table, right-hand column: every row at the shipped script.

        A future-dated attestation must not buy silence, while the two controls
        keep their meaning: an established box still alerts, a genuinely fresh
        one is still graced.
        """
        for label, offset, expect in self.F1_ROWS:
            with self.subTest(row=label):
                b = self._f1_box(offset)
                want = 1 if expect == "alerts" else 0
                self.assertEqual(
                    b.alert_count(), want,
                    "RT-28-f1 table row %r: expected the shipped watchdog to "
                    "be %s, got alert_count()=%d" % (label, expect,
                                                     b.alert_count()))
                if offset > 0:
                    self.assertIn(
                        "future", b.alert_body(),
                        "a future-dated attestation must get its own operator "
                        "story, not the no-attestation one")

    def test_rt28_f1_reproduction_table_the_86efd26_BASELINE_column(self):
        """RT-28-f1 table, left-hand column: the same rows at 86efd26.

        This is the half that makes the table falsifiable. If F1 was real, the
        pre-fix script must go SILENT on the future-dated rows while both
        controls behave identically to head. If this test ever passes at head's
        script, or the baseline alerts on a future date, the table is WRONG.
        """
        baseline = baseline_f1_watchdog()
        if baseline is None:
            self.skipTest(
                "the %s watchdog could not be resolved (no git history here); "
                "re-run from a checkout, or point $NS_BASELINE_WATCHDOG at an "
                "extracted copy" % F1_BASELINE_SHA)

        for label, offset, expect_at_head in self.F1_ROWS:
            with self.subTest(row=label):
                b = self._f1_box(offset, script=baseline)
                # Future rows INVERT at the baseline; controls do not move.
                want = 0 if offset > 0 else (1 if expect_at_head == "alerts"
                                             else 0)
                self.assertEqual(
                    b.alert_count(), want,
                    "RT-28-f1 table row %r at %s: expected alert_count()=%d, "
                    "got %d" % (label, F1_BASELINE_SHA, want, b.alert_count()))

        # WA-009, the whole point: prove the future-dated SILENCE above is
        # caused by the DATE, not by a baseline that never read the attestation
        # at all. With no attestation anywhere, installer_at=0 fails the FIRST
        # conjunct and the baseline ALERTS - with the no-attestation story. So
        # the +48h cell and this cell differ only in that the file is present
        # and dated ahead, and they come out opposite ways round.
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.attest_nowhere()
        b.run(unarmed_grace_hours=24, script=baseline)
        self.assertEqual(
            b.alert_count(), 1,
            "at %s an ABSENT attestation must still alert (installer_at=0 "
            "fails the first conjunct) - if this is silent too, then the "
            "future-dated silence proves nothing about the date"
            % F1_BASELINE_SHA)
        self.assertIn(
            "NO installer attestation", b.alert_body(),
            "the baseline's absent-attestation alert must be the "
            "no-attestation story - that is what distinguishes it from the "
            "future-dated case, which at %s is silent" % F1_BASELINE_SHA)

    def test_rt28_the_watchdog_NEVER_writes_the_attestation(self):
        """A monitor that forges the installer's signature is the same defect as
        a beat forged on the Program's behalf (ADR-0004 option A): it attests
        something it cannot know. It must alert, not self-certify."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.no_bootstrap_record()
        b.run(unarmed_grace_hours=24)
        self.assertFalse(
            b.bootstrap_exists(),
            "the watchdog created the installer's attestation - it would then "
            "grace itself on evidence it manufactured, which is the forged "
            "liveness signal ADR-0004 rejected, wearing another hat")
        b.run(unarmed_grace_hours=24)
        self.assertFalse(b.bootstrap_exists())

    def test_rt28_the_alert_tells_the_operator_WHICH_state_this_is(self):
        """'restored plane' and 'never installed' need different operator
        actions, so they must not render identically."""
        b = Box()
        b.not_alive("missing"); b.no_run_recorded(); b.no_bootstrap_record()
        b.run(unarmed_grace_hours=24)
        missing_story = b.alert_body()

        b2 = Box()
        b2.not_alive("missing"); b2.no_run_recorded(); b2.bootstrap_age(24 * 30)
        b2.run(unarmed_grace_hours=24)
        old_story = b2.alert_body()

        self.assertIn("never_started", missing_story)
        self.assertIn("never_started", old_story)
        self.assertIn("NO installer attestation", missing_story)
        self.assertIn("bootstrapped", old_story)
        self.assertNotEqual(
            missing_story, old_story,
            "a missing attestation and a stale one produced identical alerts; "
            "they are different faults with different recoveries")

    def test_rt28_a_recorded_run_still_ignores_the_attestation(self):
        """The attestation gates ONLY the not-yet-started grace."""
        b = Box()
        b.not_alive("missing")
        b.run_recorded()
        b.bootstrap_age(0)
        b.run(unarmed_grace_hours=24)
        self.assertEqual(b.alert_count(), 1,
                         "a recorded run with no beat alerts regardless of how "
                         "recently the box was bootstrapped")


class RT28TheAttestationLivesWhereTheSANDBOXCannotReachIt(unittest.TestCase):
    """F2, behaviour half - `security` on PR #35 at 86efd26.

    `deploy/bootstrap.sh` grants Codex workers `~/.local/state` as a writable
    root under `approval_policy = "never"`, and the state dir lived inside it.
    So an agent worker could write `bootstrapped-at` AND
    `watchdog-unarmed-since` - both inputs to the only decision that can break
    the org's silence. Measured with a dead Program: 200 ticks, an agent
    refreshing both stamps, 200/200 silent.

    The fix moves the WHOLE state dir to `$HOME/.config/omnigent` and hardcodes
    it. The location changes; the AUTHOR does not. bootstrap still writes it,
    unprivileged, because the act that dates the arming must be the act that
    arms - see the amendment's rejection of the root-provisioner option.
    """

    def test_the_watchdog_resolves_the_state_dir_from_HOME_ALONE(self):
        """The grace still works when XDG_STATE_HOME points somewhere else
        entirely - because the script must no longer consult it."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.bootstrap_age(0)          # written under $HOME/.config/omnigent
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            b.alert_count(), 0,
            "a freshly bootstrapped box went noisy: the watchdog did not find "
            "the attestation at $HOME/.config/omnigent, which means it is "
            "still resolving the state dir through XDG_STATE_HOME")

    def test_nothing_is_ever_written_under_XDG_STATE_HOME(self):
        """The producer/consumer seam, enforced rather than asserted. If the
        watchdog still honoured XDG it would write its stamps into the decoy,
        and on the real box bootstrap (login shell, XDG possibly set) and the
        watchdog (systemd user manager, only HOME and PATH) would resolve to
        two different directories - a healthy install alerting with the story
        'the plane was RESTORED'. That is defect #34 reintroduced."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.no_bootstrap_record()
        b.run(unarmed_grace_hours=24)   # a path that writes the unarmed stamp
        self.assertEqual(
            b.decoy_files(), [],
            "the watchdog wrote state under XDG_STATE_HOME; the systemd unit "
            "does not export that variable, so the producer and the consumer "
            "would resolve the attestation to different directories")

    def test_the_alert_throttle_moved_too(self):
        """The whole directory moves, not just the attestation. Leaving
        `watchdog-last-alert` behind would be the SAME defect class: an agent
        that can refresh the throttle can suppress every repeat alert."""
        b = Box()
        b.not_alive("stale")
        b.run()
        self.assertTrue(
            b.marker_exists(),
            "the alert marker is not under $HOME/.config/omnigent - if it "
            "stayed in the sandbox-writable tree, a worker could hold the "
            "throttle open and silence every re-alert")
        self.assertEqual(b.decoy_files(), [])

    def test_the_producer_and_the_consumer_agree_when_EXECUTED(self):
        """NOT a grep. This runs bootstrap.sh's real attestation block and then
        the real watchdog, in two different environments, and checks they land
        on one directory.

        staff's tech-debt row 1: the producer/consumer seam was only ever
        asserted by two source-text greps, and no test would have failed if the
        two halves resolved differently. Here the producer runs with
        XDG_STATE_HOME SET (a login shell, as bootstrap really runs) and the
        consumer runs with it pointed somewhere else entirely. Before F2 that
        combination is what silently split them; after F2 neither script reads
        the variable, so it cannot split them - and this test is what proves
        the claim by execution rather than by assertion."""
        with open(BOOTSTRAP, errors="replace") as fh:
            src_b = fh.read()
        m = re.search(r"^(NIGHTSHIFT_STATE_DIR=.*?\ndate \+%s > .*?\n)",
                      src_b, re.M | re.S)
        self.assertIsNotNone(
            m, "could not locate bootstrap.sh's attestation block; this test "
               "must execute the real one, never a copy")
        block = m.group(1)

        b = Box()
        b.no_bootstrap_record()      # only bootstrap may create it here
        p = subprocess.run(
            ["bash", "-c", "set -eu\n" + block],
            capture_output=True, text=True,
            env={"HOME": b.home, "PATH": os.environ.get("PATH", ""),
                 # the operator profile that used to break this seam
                 "XDG_STATE_HOME": os.path.join(b.root, "login-shell-xdg")})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(
            b.bootstrap_exists(),
            "bootstrap.sh's own attestation block did not write to "
            "$HOME/.config/omnigent, which is where the watchdog now looks")

        b.not_alive("missing")
        b.no_run_recorded()
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            b.alert_count(), 0,
            "the watchdog did not honour the attestation the REAL installer "
            "block had just written: the producer and the consumer resolved "
            "the state dir to different directories, which makes a healthy "
            "fresh install alert with the story 'the plane was RESTORED'")
        self.assertEqual(b.decoy_files(), [])

    def test_an_attestation_at_the_OLD_path_is_NOT_honoured(self):
        """Migration direction is fail-CLOSED. A box that upgrades the scripts
        without re-running bootstrap has its attestation only at the old
        location; the watchdog must treat that as no attestation and ALERT,
        never as a fresh install. Fail-closed costs one false alert with an
        actionable story; fail-open costs the dead-man's switch."""
        b = Box()
        b.not_alive("missing")
        b.no_run_recorded()
        b.no_bootstrap_record()        # nothing at the NEW path
        b.old_path_attestation(0)      # a perfectly fresh one at the OLD path
        code, out = b.run(unarmed_grace_hours=24)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            b.alert_count(), 1,
            "an attestation at the pre-move path bought silence; the migration "
            "must fail closed, or the move itself becomes a way to feed the "
            "watchdog a stamp from the sandbox-writable tree")


class RT30AttestationIsOutsideEverySandboxWritableRoot(unittest.TestCase):
    """RT-30 - the sandbox-boundary invariant, held separate from RT-28.

    RT-28 records R-22's grace SEMANTICS. This records a different property:
    whatever the grace is keyed on must not be authorable by the workers the
    grace is protecting the operator from. Both must hold; neither implies the
    other; and this one must stay greppable on its own for whoever next widens
    `writable_roots`.

    The roots are PARSED from the config `deploy/bootstrap.sh` actually writes.
    A hardcoded expected list would keep passing while the real config drifted,
    which is the failure mode this test exists to prevent.
    """

    ATTESTATION = "$HOME/.config/omnigent/bootstrapped-at"

    def heredoc(self):
        """The exact text bootstrap.sh writes to ~/.codex/config.toml."""
        with open(BOOTSTRAP, errors="replace") as fh:
            src = fh.read()
        m = re.search(
            r"cat > \"\$HOME/\.codex/config\.toml\" <<'EOF'\n(.*?)\nEOF\n",
            src, re.S)
        self.assertIsNotNone(
            m, "could not find the codex config heredoc in deploy/bootstrap.sh "
               "- this test parses the REAL config and must not silently "
               "degrade into a vacuous pass")
        return m.group(1)

    def roots(self):
        text = self.heredoc()
        m = re.search(r"^\s*writable_roots\s*=\s*\[(.*?)\]", text, re.M | re.S)
        self.assertIsNotNone(m, "no writable_roots in the config bootstrap writes")
        found = re.findall(r'"([^"]*)"', m.group(1))
        self.assertTrue(found, "writable_roots parsed as empty - vacuous pass")
        return found

    def grants(self, roots, path, home):
        """Ask the REAL shell helper, executed - not a second implementation
        that could disagree with the one bootstrap ships."""
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.toml")
            with open(cfg, "w") as fh:
                fh.write("[sandbox_workspace_write]\n")
                fh.write("writable_roots = [%s]\n"
                         % ", ".join('"%s"' % r for r in roots))
            p = subprocess.run(
                ["bash", "-c",
                 'set -u; . "%s"; nightshift_sandbox_grants_path "$1" "$2"'
                 % LIB, "_", path, cfg],
                capture_output=True, text=True,
                env=dict(os.environ, HOME=home))
        return p.returncode == 0

    def test_rt30_the_attestation_is_under_no_configured_writable_root(self):
        """The invariant itself, against the shipping config."""
        home = "/home/nightshift"
        roots = self.roots()
        path = self.ATTESTATION.replace("$HOME", home)
        self.assertFalse(
            self.grants(roots, path, home),
            "a Codex worker running approval_policy=never can write the "
            "installer attestation. It could then date its own fresh install, "
            "grace itself, and keep a dead Program silent - the file exists "
            "precisely because it must be unforgeable by the agents")

    def test_rt30_every_stamp_in_the_state_dir_is_outside_too(self):
        """Not just the attestation: the unarmed stamp starts the grace clock
        and the alert marker throttles the alert. All three decide silence."""
        home = "/home/nightshift"
        roots = self.roots()
        for name in ("bootstrapped-at", "watchdog-unarmed-since",
                     "watchdog-last-alert"):
            with self.subTest(stamp=name):
                self.assertFalse(
                    self.grants(roots, "%s/.config/omnigent/%s" % (home, name),
                                home),
                    "%s is sandbox-writable; every file that can buy silence "
                    "must be outside the grant" % name)

    def test_rt30_the_check_itself_detects_a_root_that_DOES_cover(self):
        """The negative above is only worth anything if the checker can say
        yes. A checker that always answers 'not covered' would pass every
        assertion in this class while proving nothing."""
        home = "/home/nightshift"
        path = "%s/.config/omnigent/bootstrapped-at" % home
        for root in ("~/.config", "~", "~/.config/omnigent", home + "/.config"):
            with self.subTest(root=root):
                self.assertTrue(
                    self.grants([root], path, home),
                    "%r covers the attestation and the checker missed it" % root)

    def test_rt30_the_PRE_MOVE_arrangement_is_reported_as_GRANTED(self):
        """The state `security` actually measured, pinned so the checker can
        never quietly go blind.

        The negative assertions above are only meaningful if this checker,
        given the config and the path as they were at 86efd26, says GRANTED.
        It is the counterfactual that keeps RT-30 from passing for the boring
        reason that a checker answers 'no' to everything - which is exactly how
        it behaved before the fix existed, and why the baseline record refuses
        to count RT-30's headline assertion as pre-change evidence."""
        home = "/home/nightshift"
        pre_move_roots = ["~/.cache", "~/.npm", "~/.cargo", "~/.rustup",
                          "~/go", "~/.floci", "~/.local/state"]
        self.assertTrue(
            self.grants(pre_move_roots,
                        "%s/.local/state/omnigent/bootstrapped-at" % home,
                        home),
            "the checker does not recognise the very grant this fix removes; "
            "every 'not granted' result it returns elsewhere is therefore "
            "unfalsifiable")

    def test_rt30_a_sibling_with_a_shared_PREFIX_does_not_count(self):
        """`~/.configuration` is not `~/.config`. A naive string-prefix test
        would report a grant that does not exist, and the loud WARN this feeds
        would train operators to ignore it."""
        home = "/home/nightshift"
        self.assertFalse(
            self.grants(["~/.configuration", "~/.local/state"],
                        "%s/.config/omnigent/bootstrapped-at" % home, home),
            "a shared string prefix was mistaken for a covering root")


class RT28BootstrapWritesTheAttestation(unittest.TestCase):
    """R-22 - the producer half. A gate nothing writes is a gate that fails
    every healthy install, so the write site is part of the contract."""

    BOOTSTRAP = os.path.join(REPO, "deploy", "bootstrap.sh")

    def test_rt28_bootstrap_writes_the_attestation(self):
        with open(self.BOOTSTRAP, errors="replace") as fh:
            src = fh.read()
        code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
        self.assertIn("bootstrapped-at", code,
                      "deploy/bootstrap.sh must write the installer "
                      "attestation the watchdog gates the grace on")

    def test_rt28_the_producer_hardcodes_the_same_path_as_the_consumer(self):
        """Producer and consumer must not merely agree - they must be unable to
        disagree. Both hardcode $HOME/.config/omnigent, so there is no variable
        left whose presence in one environment and absence in the other could
        split them (staff tech-debt row 1, closed by construction)."""
        with open(self.BOOTSTRAP, errors="replace") as fh:
            boot = "\n".join(ln.split("#", 1)[0] for ln in fh.read().splitlines())
        with open(WATCHDOG, errors="replace") as fh:
            watch = "\n".join(ln.split("#", 1)[0] for ln in fh.read().splitlines())
        self.assertIn("$HOME/.config/omnigent", boot)
        self.assertIn("$HOME/.config/omnigent", watch)
        for name, code in (("bootstrap.sh", boot), ("nightshift-watchdog.sh", watch)):
            with self.subTest(script=name):
                self.assertNotIn(
                    "XDG_STATE_HOME", code,
                    "%s still resolves state through XDG_STATE_HOME. The "
                    "systemd user manager exports only HOME and PATH, so the "
                    "two halves can resolve to different directories and a "
                    "healthy install alerts as a RESTORE" % name)

    def test_rt28_an_existing_codex_config_gets_the_INVERSE_upgrade_hint(self):
        """bootstrap only writes the codex config `if [ ! -f ]`, so narrowing
        the heredoc changes NOTHING on the live box - which is exactly where
        `security` measured the vulnerability. The existing-config branch must
        therefore CHECK the config it refused to rewrite, and warn.

        It must warn rather than rewrite: the ':99' contract says the file is
        only written if absent and the operator's edits survive. Silently
        rewriting an operator's sandbox policy is its own defect."""
        with open(self.BOOTSTRAP, errors="replace") as fh:
            src = fh.read()
        code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
        i = code.find("leaving it alone")
        self.assertNotEqual(i, -1, "the existing-config branch moved")
        tail = code[i:]
        self.assertIn(
            "nightshift_sandbox_grants_path", tail,
            "the existing-config branch does not check whether the config "
            "already on disk grants a writable root covering the attestation "
            "- so the box where the vulnerability was actually measured would "
            "be left exactly as it was found")
        self.assertIn("WARN", tail)
        self.assertNotIn(
            "cat > \"$HOME/.codex/config.toml\"", tail,
            "the existing-config branch rewrites the operator's config; :99 "
            "promises their edits survive")

    def test_rt28_it_is_written_BEFORE_the_units_are_enabled(self):
        """Ordering is the whole point: a timer armed while the attestation is
        missing alerts on its first tick, on a healthy install."""
        with open(self.BOOTSTRAP, errors="replace") as fh:
            code = "\n".join(ln.split("#", 1)[0] for ln in fh.read().splitlines())
        write = code.find("bootstrapped-at")
        enable = code.find("nightshift_install_user_units")
        self.assertNotEqual(write, -1)
        self.assertNotEqual(enable, -1)
        self.assertLess(
            write, enable,
            "the attestation is written AFTER the units are enabled, so the "
            "first watchdog tick can fire before the file exists")


if __name__ == "__main__":
    unittest.main()


# ===========================================================================
# #132 — the LLM-INDEPENDENT heartbeat detector, and #42 — the stale ctl.
#
# The watchdog above answers "is the Program alive?" from the beat record. These
# cases cover the question it could NOT answer, which is the one that cost the
# org an hour on 2026-08-24: "will the Program ever be woken again, and did its
# last wake-up FAIL?"
#
# The probe is exercised through the real script, against a stub probe on disk
# whose exit code the test chooses. What is under test HERE is the watchdog's
# WIRING — which verdict suppresses, which escalates, which changes nothing, and
# which marker throttles it. The probe's own logic is tested in
# tests/test_scheduler_probe.py against a real HTTP server.
# ===========================================================================

FAKE_PROBE = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["NS_PROBE_CODE"]) as fh:
    code = int(fh.read().strip() or "2")
with open(os.environ["NS_PROBE_BODY"]) as fh:
    sys.stdout.write(fh.read())
with open(os.environ["NS_LOG"], "a") as fh:
    fh.write("probe %s\n" % " ".join(sys.argv[1:]))
sys.exit(code)
"""


class SchedulerBox(Box):
    """A Box whose scheduler probe answers whatever the test wants."""

    def __init__(self):
        Box.__init__(self)
        self.pcode = os.path.join(self.root, "probe.code")
        self.pbody = os.path.join(self.root, "probe.json")
        self.probe = os.path.join(self.root, "fake-probe.py")
        with open(self.probe, "w") as fh:
            fh.write(FAKE_PROBE)
        os.chmod(self.probe, os.stat(self.probe).st_mode | stat.S_IEXEC)
        self.scheduler_unknown()

    def _probe(self, code, obj):
        with open(self.pcode, "w") as fh:
            fh.write(str(code))
        with open(self.pbody, "w") as fh:
            json.dump(obj, fh)

    def scheduler_ok(self):
        self._probe(0, {"status": "healthy", "reason_code": "healthy"})

    def scheduler_paused(self):
        self._probe(4, {"status": "paused", "reason_code": "paused",
                        "detail": "task state is 'paused', not 'active'"})

    def scheduler_failing(self, reason="runs_failing"):
        self._probe(3, {"status": "verdict", "reason_code": reason,
                        "detail": "2 consecutive scheduled run(s) FAILED "
                                  "(newest error_code='runner_error')"})

    def scheduler_unknown(self):
        self._probe(2, {"status": "unknown", "reason_code": "probe_unavailable",
                        "detail": "connection refused"})

    def run(self, **kw):
        extra = dict(kw.pop("extra_env", None) or {})
        extra.setdefault("NIGHTSHIFT_SCHEDULER_PROBE", self.probe)
        extra.setdefault("NS_PROBE_CODE", self.pcode)
        extra.setdefault("NS_PROBE_BODY", self.pbody)
        return Box.run(self, extra_env=extra, **kw)


class RT132DeliberatePauseIsNotAnEmergency(unittest.TestCase):
    """The owner's own framing: distinguishing "paused on purpose" from "dying"
    is part of the problem, not an edge case.

    The org was silent 08:00Z->19:00Z on 2026-08-24 — an owner maintenance
    freeze. Eleven hours with no beat. A beat-only watchdog alerts on that,
    twice at the six-hour re-alert interval, and every one of those alerts is
    wrong. An alarm that fires on intended states is an alarm people learn to
    ignore, which is how the real one gets missed.
    """

    def setUp(self):
        self.b = SchedulerBox()

    def test_a_paused_task_silences_even_a_LONG_dead_beat(self):
        self.b.not_alive("stale", age=11 * 3600)
        self.b.scheduler_paused()
        rc, out = self.b.run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.b.alert_count(), 0,
                         "a DELIBERATE pause must never raise an alarm")
        self.assertIn("PAUSED on purpose", out)

    def test_the_pause_is_journalled_not_swallowed(self):
        """A silent exit is indistinguishable from the watchdog itself being
        dead. An operator asking "is my watchdog running?" needs an answer."""
        self.b.not_alive("stale")
        self.b.scheduler_paused()
        _rc, out = self.b.run()
        self.assertTrue(out.strip(), "the pause path must say something")

    def test_pausing_does_not_burn_the_beat_alert_window(self):
        """After the freeze ends, a genuinely dead Program must still alert
        immediately — the quiet period must not have consumed the throttle."""
        self.b.not_alive("stale")
        self.b.scheduler_paused()
        self.b.run()
        self.b.scheduler_ok()          # freeze over, task active again
        self.b.run()
        self.assertEqual(self.b.alert_count(), 1)


class RT132AFailedRunIsHeardImmediately(unittest.TestCase):
    """Closing condition 2: notice a failed scheduled run and report it."""

    def setUp(self):
        self.b = SchedulerBox()

    def test_failed_runs_alert_even_though_the_BEAT_IS_ALIVE(self):
        """THE case that made #132 expensive.

        On 2026-08-24 a driver session was still awake and beating while the
        scheduled runs failed underneath it. The beat said ALIVE — truthfully —
        and the mechanism that would wake the org once that session ended was
        already broken. A watchdog that exits early on a live beat is exactly
        the luck the issue names as the defect.
        """
        self.b.alive()                 # the Program is beating. Genuinely.
        self.b.scheduler_failing()
        rc, out = self.b.run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.b.alert_count(), 1,
                         "a live beat must NOT acquit a failing scheduler")
        body = self.b.alert_body()
        self.assertIn("WAKE-UP MECHANISM", body)
        self.assertIn("runs_failing", body)

    def test_the_alert_leads_with_the_scheduler_story(self):
        """Burying it under the beat narrative sends the operator to the wrong
        place: they read about heartbeat staleness while the actual news is that
        the org will never be woken again."""
        self.b.alive()
        self.b.scheduler_failing()
        self.b.run()
        body = self.b.alert_body()
        self.assertLess(body.index("Scheduler fault"), body.index("heartbeat"),
                        "the scheduler fault must lead the alert body")

    def test_a_missing_task_row_is_reported(self):
        self.b.alive()
        self.b.scheduler_failing(reason="task_missing")
        self.b.run()
        self.assertIn("task_missing", self.b.alert_body())

    def test_no_model_call_is_made_anywhere_on_this_path(self):
        """LLM-independence at the instrumented boundary: the executables the
        watchdog invokes on the detect path are the ctl, gh, systemctl and the
        probe -- and none of them is handed anything model-shaped.

        Scope, stated plainly: the journal can only record stubs that exist, so
        this shows what DID run, not that nothing else COULD. The companion test
        below closes that from the other side by reading the script itself.
        """
        self.b.alive()
        self.b.scheduler_failing()
        self.b.run()
        # The journal is line-based and the alert BODY is multi-line, so it is
        # not a pure list of argv records. Assert on what actually matters:
        # which executables ran, and that nothing model-shaped appears at all.
        stubs = ("nightshift-ctl", "gh", "systemctl", "probe")
        argv = [line for line in self.b.calls() if line.startswith(stubs)]
        self.assertEqual({line.split()[0] for line in argv}, set(stubs),
                         "exactly the ctl, gh, systemctl and the probe ran")
        for forbidden in ("anthropic", "openai", "sys_call", "sys_session"):
            self.assertNotIn(forbidden, "\n".join(argv).lower(),
                             "the detect path must involve no model call")

    def test_the_script_itself_contains_no_model_or_agent_call(self):
        """Structural, so it holds for paths this suite does not exercise."""
        with open(WATCHDOG, encoding="utf-8") as fh:
            text = fh.read().lower()
        for forbidden in ("anthropic", "openai", "sys_call", "sys_session",
                          "omnigent host", "claude "):
            self.assertNotIn(forbidden, text)


class RT132TheTwoAlarmsDoNotMuteEachOther(unittest.TestCase):
    """They report different faults with different fixes. Sharing one throttle
    would let whichever fired first silence the other for six hours."""

    def setUp(self):
        self.b = SchedulerBox()

    def test_a_beat_alert_does_not_swallow_a_later_scheduler_alert(self):
        self.b.not_alive("stale")
        self.b.scheduler_unknown()
        self.b.run()
        self.assertEqual(self.b.alert_count(), 1)      # beat alarm
        self.b.alive()
        self.b.scheduler_failing()
        self.b.run()
        self.assertEqual(self.b.alert_count(), 2,
                         "the scheduler alarm has its own throttle marker")

    def test_the_scheduler_alarm_still_throttles_itself(self):
        self.b.alive()
        self.b.scheduler_failing()
        self.b.run()
        self.b.run()
        self.assertEqual(self.b.alert_count(), 1)


class RT132AnUnreachableProbeChangesNothing(unittest.TestCase):
    """Only a POSITIVE `paused` buys silence. Every failure mode of the probe —
    server down, garbage JSON, script missing — must leave the beat verdict
    exactly as it was. A probe that could go quiet on its own failure would be
    the mtime defect rebuilt in a new place."""

    def setUp(self):
        self.b = SchedulerBox()

    def test_probe_unavailable_still_alerts_on_a_dead_beat(self):
        self.b.not_alive("stale")
        self.b.scheduler_unknown()
        self.b.run()
        self.assertEqual(self.b.alert_count(), 1)

    def test_probe_unavailable_still_stays_quiet_on_a_live_beat(self):
        self.b.alive()
        self.b.scheduler_unknown()
        rc, _ = self.b.run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.b.alert_count(), 0)

    def test_a_MISSING_probe_script_does_not_break_the_watchdog(self):
        self.b.not_alive("stale")
        rc, _ = self.b.run(extra_env={
            "NIGHTSHIFT_SCHEDULER_PROBE": os.path.join(self.b.root, "nope.py")})
        self.assertEqual(rc, 0, "a watchdog that crashes its timer is worse than none")
        self.assertEqual(self.b.alert_count(), 1)

    def test_the_scheduler_layer_can_be_switched_off(self):
        self.b.alive()
        self.b.scheduler_failing()
        self.b.run(extra_env={"NIGHTSHIFT_WATCHDOG_SCHEDULER": "0"})
        self.assertEqual(self.b.alert_count(), 0)


class RT42TheCtlComesFromTHISTree(unittest.TestCase):
    """Issue #42: the watchdog ran a DIFFERENT, STALE `nightshift-ctl` from PATH
    that has no `beat` subcommand.

    Two independent repairs, and both are tested because either alone is
    fragile: resolve the binary from the script's own tree (so PATH is out of
    the trust path), and NAME the fault when the resolved binary still cannot
    answer (so the alarm stops blaming the control plane for a packaging bug).
    """

    def setUp(self):
        self.b = SchedulerBox()

    def test_a_beatless_ctl_is_named_not_misattributed_as_a_corrupt_db(self):
        """Before this, a `beat`-less ctl produced `unavailable` — "the control
        plane could not be read". The operator checks the database, finds it
        healthy, and concludes the watchdog is flaky. The dead-man's switch is
        down and the alert points away from the cause."""
        # Replace the stub IN PLACE rather than pointing NIGHTSHIFT_CTL
        # elsewhere: the harness pins NIGHTSHIFT_CTL to this path on every run,
        # and overwriting the binary that actually gets resolved is the faithful
        # simulation anyway -- #42 is not "the variable was wrong", it is "the
        # binary that got resolved could not answer".
        beatless = os.path.join(self.b.bin, "nightshift-ctl")
        with open(beatless, "w") as fh:
            fh.write("#!/usr/bin/env bash\n"
                     "printf 'nightshift-ctl %s\\n' \"$*\" >> \"$NS_LOG\"\n"
                     "case \"$*\" in *beat*) exit 2 ;; esac\nexit 0\n")
        os.chmod(beatless, os.stat(beatless).st_mode | stat.S_IEXEC)
        self.b.run()
        body = self.b.alert_body()
        self.assertIn("ctl_lacks_beat", body)
        self.assertIn("#42", body)
        self.assertNotIn("corrupt or unreadable control.db", body)

    def test_the_default_ctl_is_the_one_beside_the_script_not_PATH(self):
        """The durable half. `~/.local/bin/nightshift-ctl` on the live box is a
        symlink into a frozen checkout; the unit exported only that directory.
        With the default self-locating, a stale unit that was never re-rendered
        still resolves the co-located binary."""
        with open(WATCHDOG, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('_self_dir', text)
        self.assertIn('"$_self_dir/../bin/nightshift-ctl"', text)
        self.assertNotIn('CTL="${NIGHTSHIFT_CTL:-nightshift-ctl}"', text,
                         "PATH must no longer be the DEFAULT source of the ctl")

    def test_the_unit_pins_the_ctl_to_the_same_tree_as_the_script(self):
        unit = os.path.join(REPO, "deploy", "systemd",
                            "nightshift-watchdog.service")
        with open(unit, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Environment=NIGHTSHIFT_CTL=__TEAMS_DIR__/bin/nightshift-ctl",
                      text)
        path_line = [l for l in text.splitlines()
                     if l.startswith("Environment=PATH=")][0]
        self.assertTrue(
            path_line.startswith("Environment=PATH=__TEAMS_DIR__/bin:"),
            f"the release bin dir must come FIRST on the unit PATH: {path_line}")


class RT21TheAlertMustNotSILENCEITSELF(unittest.TestCase):
    """RT-21 — a dead-man's switch that eats its own alarm is the failure it exists to prevent.

    Review finding (architect/Codex, cross-vendor review of PR #144): any
    `gh issue list` failure collapsed to an empty issue number, the script
    journalled to the LOCAL journal — the one nobody is reading, because the box
    is the thing that is broken — and then wrote the six-hour throttle marker
    anyway. Every tick for the next six hours exited early on that marker. No
    comment ever reached pinned issue #3 and the owner was never alerted, while
    the watchdog's own state said "alerted".

    That is worse than no watchdog: the box reports a delivered alert it never
    delivered. So the rule is:

      * the throttle marker is written ONLY after CONFIRMED delivery
      * a delivery failure is LOUD and stays visibly degraded
      * later ticks RETRY, with a backoff that is bounded well under REALERT_HOURS
        and can never become permanent silence

    `gh issue comment` failing was the same hole with a better ending: it avoided
    the marker and retried, but emitted no diagnostic at all, so a persistently
    undeliverable alert looked identical to a quiet, healthy box.
    """

    # -- discovery fails: `gh issue list` cannot name the status issue --------
    def test_a_failed_issue_lookup_does_not_consume_the_throttle(self):
        b = Box()
        b.not_alive("stale")
        code, out = b.run(extra_env={"NS_GH_LIST_FAIL": "1"})
        self.assertEqual(code, 0, f"the watchdog must never exit non-zero: {out}")
        self.assertEqual(b.alert_count(), 0, "nothing was delivered")
        self.assertFalse(
            b.marker_exists(),
            "the throttle marker was written for an alert the owner never received — "
            "the next six hours of ticks will exit early on it")

    def test_a_failed_issue_lookup_is_loud(self):
        b = Box()
        b.not_alive("stale")
        _code, out = b.run(extra_env={"NS_GH_LIST_FAIL": "1"})
        self.assertIn("UNDELIVERED", out,
                      f"a silent notification failure is indistinguishable from "
                      f"a healthy box: {out}")

    def test_a_failed_issue_lookup_is_retried_on_the_next_tick(self):
        """The alarm must survive its own transport being down."""
        b = Box()
        b.not_alive("stale")
        b.run(extra_env={"NS_GH_LIST_FAIL": "1"})
        self.assertTrue(b.undelivered_exists(), "the degraded state must be recorded")
        # gh recovers. The next eligible tick must deliver -- not wait out a
        # six-hour window it never earned. A zero delivery-backoff cap is how
        # this suite says "the delivery backoff has elapsed" without sleeping.
        b.run(extra_env={"NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "0"})
        self.assertEqual(b.alert_count(), 1,
                         "the owner was never told; the alert died with the outage")
        self.assertTrue(b.marker_exists(), "a DELIVERED alert does throttle")
        self.assertFalse(b.undelivered_exists(),
                         "the degraded record must clear once delivery succeeds")

    # -- delivery fails: the issue is found, the comment is rejected ----------
    def test_a_failed_comment_is_loud_and_does_not_consume_the_throttle(self):
        b = Box()
        b.not_alive("stale")
        _code, out = b.run(extra_env={"NS_GH_COMMENT_FAIL": "1"})
        self.assertEqual(b.alert_count(), 0)
        self.assertFalse(b.marker_exists(),
                         "a rejected comment must not throttle the next attempt")
        self.assertIn("UNDELIVERED", out,
                      f"a rejected comment emitted no diagnostic at all: {out}")

    def test_delivery_is_retried_until_it_succeeds(self):
        b = Box()
        b.not_alive("stale")
        for _ in range(3):
            b.run(extra_env={"NS_GH_COMMENT_FAIL": "1",
                             "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "0"})
        self.assertEqual(b.alert_count(), 0)
        self.assertEqual(b.delivery_attempts(), 3, "every failure must be counted")
        b.run(extra_env={"NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "0"})
        self.assertEqual(b.alert_count(), 1,
                         "delivery must keep being retried while it keeps failing")
        self.assertTrue(b.marker_exists())

    # -- the backoff is a backoff, never a mute ------------------------------
    def test_repeated_failures_back_off_rather_than_hammer_github(self):
        b = Box()
        b.not_alive("stale")
        b.run(extra_env={"NS_GH_COMMENT_FAIL": "1"})
        first = b.delivery_attempts()
        self.assertEqual(first, 1, "the first failure must be recorded")
        # Immediately again: within the backoff, so no second API call.
        b.run(extra_env={"NS_GH_COMMENT_FAIL": "1"})
        self.assertEqual(b.delivery_attempts(), 1,
                         "a 10-minute timer must not retry delivery every 10 minutes "
                         "forever against a rate-limited API")

    def test_the_delivery_backoff_can_never_exceed_the_realert_window(self):
        """A backoff that grows past REALERT_HOURS would reinvent the defect:
        the alarm would go quiet for longer than the throttle it replaced."""
        with open(WATCHDOG) as fh:
            src = fh.read()
        m = re.search(r"NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX:-([0-9]+)", src)
        self.assertIsNotNone(m, "the delivery backoff must have a hard cap")
        cap = int(m.group(1))
        self.assertGreater(cap, 0, "an uncapped-at-zero backoff would hammer the API")
        self.assertLess(cap, 6 * 3600,
                        f"the delivery backoff cap ({cap}s) reaches or exceeds one "
                        f"REALERT window, so a failing transport would go quieter "
                        f"than the throttle it replaced")
        # And behaviourally: 20 consecutive failures. Doubling without a cap is
        # 600 * 2^19 -- over three months of silence on attempt twenty.
        b = Box()
        b.not_alive("stale")
        for _ in range(20):
            b.run(extra_env={"NS_GH_COMMENT_FAIL": "1",
                             "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "0"})
        self.assertEqual(b.delivery_attempts(), 20, "every failure must be counted")
        b.run(extra_env={"NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "0"})
        self.assertEqual(b.alert_count(), 1,
                         "after 20 failures the alarm had muted itself permanently")

    def test_an_undelivered_alert_never_counts_as_a_delivered_one(self):
        """The two markers must not be the same file, by construction."""
        b = Box()
        b.not_alive("stale")
        b.run(extra_env={"NS_GH_LIST_FAIL": "1"})
        self.assertTrue(b.undelivered_exists())
        self.assertFalse(b.marker_exists())


class RT21TheSAFETYBOUNDIsENFORCEDNotASSERTED(unittest.TestCase):
    """The safety bound on the delivery backoff must survive a HOSTILE ENV.

    Second review loop on #132. The first loop closed the throttle-marker hole:
    a marker is written only on CONFIRMED delivery, and an undelivered alert is
    retried with a backoff "bounded well under REALERT_HOURS". The bound itself,
    though, was only ever ASSERTED — by a comment, and by a source-text test
    that regex-matched the LITERAL DEFAULT `3600` out of the script. Nothing in
    the running script enforced it, and both operands were raw, unvalidated
    environment inputs:

        DELIVERY_BACKOFF_MAX="${NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX:-3600}"
        REALERT_HOURS="${NIGHTSHIFT_WATCHDOG_REALERT_HOURS:-6}"

    So the computed backoff was capped only against a number the environment
    chose, and never against the re-alert window at all. Proven by execution:
    after one forced `gh issue comment` failure, a tick under
    `..._BACKOFF_BASE=86400 ..._BACKOFF_MAX=86400` reported the next retry
    86399s away — four times past the 6h window — with the owner still untold.
    A muted UNDELIVERED alert is bug 4 exactly, reached by a new path.

    A test that reads the default out of the source cannot catch that, because
    the default is not what runs. Every test here therefore DRIVES the script
    with hostile overrides and reads the BEHAVIOUR back.

    The invariant under test, stated once:

        AN UNDELIVERED OWNER ALERT IS NEVER DEFERRED PAST THE RE-ALERT WINDOW,
        WHATEVER THE ENVIRONMENT SAYS.
    """

    # The watchdog clamps to HALF the window, so a failing transport gets at
    # least two attempts per window. The tests assert the WINDOW bound — the
    # property that matters — so tightening the margin later cannot break them.
    WINDOW = 6 * 3600

    def deferral_after_one_failure(self, env):
        """One forced delivery failure, then a second tick under `env`.

        Returns how many seconds the watchdog says it will wait before
        RETRYING an alert THE OWNER HAS NOT RECEIVED.
        """
        b = Box()
        b.not_alive("stale")
        hostile = dict(env)
        hostile["NS_GH_COMMENT_FAIL"] = "1"
        code, _out = b.run(extra_env=hostile)
        self.assertEqual(code, 0, "the watchdog must never exit non-zero")
        self.assertEqual(b.delivery_attempts(), 1, "the failure must be recorded")
        code, out = b.run(extra_env=hostile)
        self.assertEqual(code, 0, "the watchdog must never exit non-zero")
        self.assertEqual(b.alert_count(), 0, "nothing was ever delivered")
        m = re.search(r"next delivery retry in (\d+)s", out)
        self.assertIsNotNone(
            m, f"the undelivered alert did not report its retry deadline: {out}")
        return int(m.group(1))

    def assertWithinWindow(self, deferral, window=None, why=""):
        window = self.WINDOW if window is None else window
        self.assertLess(
            deferral, window,
            f"an UNDELIVERED owner alert was deferred {deferral}s — past the "
            f"{window}s re-alert window. The owner has not been told, and will "
            f"not be told for longer than the throttle a DELIVERED alert would "
            f"have earned. {why}")

    # -- the reviewer's own reproduction, verbatim ---------------------------
    def test_a_hostile_backoff_env_cannot_mute_an_undelivered_alert(self):
        """BASE=86400 MAX=86400: the exact env that reported 86399s."""
        d = self.deferral_after_one_failure({
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "86400",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "86400",
        })
        self.assertWithinWindow(d, why="This is the reviewer's reproduction.")

    def test_the_bound_follows_the_EFFECTIVE_realert_window_not_a_literal(self):
        """The old cap was the literal 3600. Shrink the window to 1h and a
        3600s backoff is no longer 'well under' anything — it IS the window.
        A bound derived from the effective window catches that; a literal
        cannot, which is precisely why the literal was not a bound."""
        d = self.deferral_after_one_failure({
            "NIGHTSHIFT_WATCHDOG_REALERT_HOURS": "1",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "3600",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "3600",
        })
        self.assertWithinWindow(d, window=3600,
                                why="The cap equalled the whole re-alert window.")

    def test_the_bound_holds_after_repeated_doubling(self):
        """The mute does not need a hostile FIRST tick — doubling gets there on
        its own if the ceiling is missing. Twelve failures from a legal base
        would reach 600 * 2^11 = 1,228,800s (over two weeks) uncapped."""
        b = Box()
        b.not_alive("stale")
        hostile = {"NS_GH_COMMENT_FAIL": "1",
                   "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "3600",
                   "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "86400"}
        for _ in range(12):
            b.run(extra_env=dict(hostile,
                                 NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX="0"))
        self.assertEqual(b.delivery_attempts(), 12, "every failure must be counted")
        _code, out = b.run(extra_env=hostile)
        m = re.search(r"next delivery retry in (\d+)s", out)
        self.assertIsNotNone(m, f"no retry deadline reported: {out}")
        self.assertWithinWindow(int(m.group(1)),
                                why="Doubling escaped the ceiling.")

    # -- fail-closed validation: malformed input must not become no cap ------
    def test_a_non_numeric_backoff_cap_does_not_silently_remove_the_cap(self):
        """`[ "$backoff" -gt banana ]` aborts with 'integer expression
        expected' and returns non-zero, so the capping line was simply SKIPPED
        and the raw base survived. A malformed cap must fall back to a safe
        bound, never to no bound at all."""
        d = self.deferral_after_one_failure({
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "86400",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "banana",
        })
        self.assertWithinWindow(d, why="A non-numeric cap disabled capping.")

    def test_a_negative_backoff_cap_is_rejected(self):
        d = self.deferral_after_one_failure({
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "86400",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "-1",
        })
        self.assertWithinWindow(d)

    def test_malformed_backoff_input_is_reported_not_swallowed(self):
        """Fail-closed is necessary but not sufficient: an operator who
        mistyped a knob must be TOLD the value was discarded, or the box runs
        for months on settings nobody believes are in force."""
        b = Box()
        b.not_alive("stale")
        _code, out = b.run(extra_env={
            "NS_GH_COMMENT_FAIL": "1",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "banana",
        })
        self.assertIn("NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX", out,
                      f"a rejected knob was discarded silently: {out}")
        self.assertNotIn("integer expression expected", out,
                         f"the raw value still reached shell arithmetic: {out}")

    def test_a_malformed_knob_never_produces_a_shell_arithmetic_error(self):
        """Every malformed spelling, on every knob, must be absorbed by
        validation rather than surfacing as a `[:` or `((:` diagnostic — those
        mean the value reached the comparison, and a failed comparison is a
        SKIPPED safety check."""
        for knob in ("NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE",
                     "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX",
                     "NIGHTSHIFT_WATCHDOG_REALERT_HOURS",
                     "NIGHTSHIFT_WATCHDOG_STALE_HOURS",
                     "NIGHTSHIFT_WATCHDOG_UNARMED_GRACE_HOURS"):
            for bad in ("banana", "-1", "", "6.5", "0x10", "1e9", " ", "9" * 40):
                with self.subTest(knob=knob, value=bad):
                    b = Box()
                    b.not_alive("stale")
                    code, out = b.run(extra_env={"NS_GH_COMMENT_FAIL": "1",
                                                 knob: bad})
                    self.assertEqual(code, 0,
                                     f"{knob}={bad!r} crashed the watchdog: {out}")
                    for noise in ("integer expression expected",
                                  "syntax error", "invalid arithmetic",
                                  "unbound variable", "value too great"):
                        self.assertNotIn(
                            noise, out,
                            f"{knob}={bad!r} reached shell arithmetic ({noise}), "
                            f"so a safety comparison was skipped: {out}")

    # -- REALERT_HOURS gets the same rigour ----------------------------------
    def test_a_hostile_realert_window_cannot_widen_the_delivery_bound(self):
        """REALERT_HOURS is the ceiling the backoff is measured against, so an
        unvalidated one hands the backoff back exactly the freedom the clamp
        removes: `REALERT_HOURS=99999` would license a backoff of weeks."""
        d = self.deferral_after_one_failure({
            "NIGHTSHIFT_WATCHDOG_REALERT_HOURS": "99999",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "86400",
            "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "86400",
        })
        self.assertWithinWindow(d, why="A hostile re-alert window widened the cap.")

    def test_a_malformed_realert_window_falls_back_to_the_safe_default(self):
        """`[ N -lt banana ]` errors, the throttle comparison returns non-zero,
        and the window silently becomes 'no throttle'. Fail-closed means the
        shipped 6h default, announced."""
        for bad in ("banana", "-5", "0"):
            with self.subTest(value=bad):
                b = Box()
                b.not_alive("stale")
                _code, out = b.run(extra_env={
                    "NS_GH_COMMENT_FAIL": "1",
                    "NIGHTSHIFT_WATCHDOG_REALERT_HOURS": bad})
                self.assertIn("NIGHTSHIFT_WATCHDOG_REALERT_HOURS", out,
                              f"REALERT_HOURS={bad!r} was accepted silently: {out}")
                self.assertIn("the 6h alert throttle", out,
                              f"REALERT_HOURS={bad!r} did not fall back to the "
                              f"shipped default: {out}")

    def test_a_hostile_realert_window_cannot_mute_a_DELIVERED_alert_forever(self):
        """The other half of the same hole. REALERT_HOURS also throttles alerts
        that WERE delivered, so an unbounded value mutes a live alarm for as
        long as it likes — the original defect with a different marker."""
        b = Box()
        b.not_alive("stale")
        b.run(extra_env={"NIGHTSHIFT_WATCHDOG_REALERT_HOURS": "99999"})
        self.assertEqual(b.alert_count(), 1, "the first alert must be delivered")
        # Age the throttle marker by a day. Under an accepted 99999h window the
        # box stays silent for eleven years; under a validated one it re-alerts.
        marker = os.path.join(b.state, "omnigent", "watchdog-last-alert")
        with open(marker, "w") as fh:
            fh.write(str(int(time.time() - 25 * 3600)))
        b.run(extra_env={"NIGHTSHIFT_WATCHDOG_REALERT_HOURS": "99999"})
        self.assertEqual(
            b.alert_count(), 2,
            "a hostile REALERT_HOURS muted a DELIVERED alarm past any sane "
            "window — the throttle became the silence it exists to bound")

    # -- the bound holds without disturbing the shipped defaults -------------
    def test_the_shipped_defaults_are_unchanged_by_the_clamp(self):
        """The clamp must bound hostile input without quietly re-tuning the
        box everyone actually runs: default first retry is still one base
        interval, not a clamped value."""
        d = self.deferral_after_one_failure({})
        self.assertGreater(d, 500, f"the default backoff was clamped away: {d}s")
        self.assertWithinWindow(d)

    def test_delivery_still_succeeds_once_the_transport_recovers(self):
        """A clamp that bounded the backoff by breaking retries would trade one
        silent failure for another."""
        b = Box()
        b.not_alive("stale")
        b.run(extra_env={"NS_GH_COMMENT_FAIL": "1",
                         "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_BASE": "86400",
                         "NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "86400"})
        self.assertEqual(b.alert_count(), 0)
        b.run(extra_env={"NIGHTSHIFT_WATCHDOG_DELIVERY_BACKOFF_MAX": "0"})
        self.assertEqual(b.alert_count(), 1,
                         "the alert never recovered after a hostile-env tick")
        self.assertTrue(b.marker_exists(), "a DELIVERED alert does throttle")
        self.assertFalse(b.undelivered_exists(),
                         "the degraded record must clear once delivery succeeds")
