"""The heartbeat timer writes ITS OWN signal, and never the Program's.

Two defects are pinned here, both found by the `security` gate on PR #71.

#42/#40 -- THE PRODUCER WAS A SILENT NO-OP
    The script resolved its binary by PATH. The unit sets
    PATH=__HOME__/.local/bin:... and ~/.local/bin/nightshift-ctl symlinks to the
    FROZEN /srv/nightshift copy, which has no `beat` subcommand at all. The
    argparse error went to stderr, stdout came back empty, `2>/dev/null` and
    `|| true` swallowed it, and the empty read was mistaken for "no beat yet" --
    so it exited 0 forever while writing nothing.

#76 -- THE TIMER WROTE THE PROGRAM'S SIGNAL  (ADR-0006 D1/D2, P0)
    Worse, and the reason this file was rewritten. The fixed producer wrote
    `--who program`, taking run-id and seq out of the Program's own record.
    ADR-0002 §1 reserves that signal to the Program alone. Three consequences,
    each a regression test below:
      (a) a dead Program read HEALTHY forever -- every tick restamped `ts`
      (b) the post-reboot boot-ID check was erased in ONE tick, because
          `beat write` does `boot = args.boot or host_boot_id()`
      (c) the inverse -- a false DEAD pinned across a run roll, because the
          run-id came from the record instead of from `run current`

    ADR-0006 D1 restates ownership as a property rather than a list of banned
    nouns: a liveness signal may be written only by a process whose continued
    existence is CAUSED BY the liveness of the thing it attests. The review
    question is "what stops this writer when the attested thing stops?" A timer
    fails that for `heartbeat:program` and passes it for its own signal.

WA-009 CONTROL / COUNTERFACTUAL
-------------------------------
  CONTROL        current script -> writes heartbeat:heartbeat-timer;
                 heartbeat:program is byte-identical before and after
  COUNTERFACTUAL script at 78b2771 -> advances heartbeat:program and writes no
                 timer signal at all. Every ownership test below fails against
                 it, and fails FOR THE RIGHT REASON (the Program's key moved),
                 not through harness error.
  WITNESS        script at 4d13c59 -> exit 0, nothing written anywhere

WHY THIS IS A test_*.py AND NOT A SHELL SCRIPT
----------------------------------------------
`tests/run.sh` is `unittest discover -p 'test_*.py'`. Nothing discovers
`tests/linux/*.sh`, so the original WA-009 "proof" for #40/#68 never ran on a
gate -- which is #74, and is why the silent no-op shipped green.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "deploy", "nightshift-heartbeat.sh")
CTL = os.environ.get("NIGHTSHIFT_TEST_CTL") or os.path.join(
    REPO, "bin", "nightshift-ctl")

# The two prior revisions of the producer, used as counterfactuals.
REV_SILENT_NOOP = "4d13c59c661c8d3df54274bf378817a1284c0daa"   # resolved by PATH
REV_WROTE_PROGRAM = "78b2771f68277745135eeb9243bd7fc1e355c8c0"  # --who program

UNIT_PATH_TAIL = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

OTHER_BOOT = "deadbeef-0000-0000-0000-000000000000"

# Reproduces the frozen binary: knows every subcommand EXCEPT `beat`, on which
# argparse exits 2 with an invalid-choice error on stderr and nothing on stdout.
FROZEN_STUB = r"""#!/bin/sh
for a in "$@"; do
  if [ "$a" = "beat" ]; then
    echo "nightshift-ctl: error: argument group: invalid choice: 'beat' (choose from 'init', 'slot', 'claim', 'lease', 'seq', 'checkpoint', 'task', 'issue', 'run', 'vendor', 'pr-lint', 'pr-create', 'reap', 'dump', 'selftest')" >&2
    exit 2
  fi
done
exit 0
"""

# A ctl that DOES speak `beat`, so the capability probe passes, but whose
# `beat write` returns a chosen exit code. Separates a VERDICT (exit 3, stays
# quiet) from a BREAKAGE (anything else, must be loud).
WRITE_RC_STUB = r"""#!/bin/sh
verb=""
sub=""
prev=""
for a in "$@"; do
  case "$prev" in
    beat) verb="beat"; sub="$a" ;;
    run)  verb="run";  sub="$a" ;;
  esac
  prev="$a"
done
if [ "$verb" = "run" ] && [ "$sub" = "current" ]; then
  echo '{"ok": true, "run_id": "r-stub"}'
  exit 0
fi
case "$sub" in
  --help) exit 0 ;;
  show)
    echo '{"alive": false, "reason_code": "no_expected_run", "record": {"seq": 5, "run_id": "r-stub", "who": "heartbeat-timer", "phase": "seed", "session": "s"}}'
    exit 3 ;;
  write)
    echo '{"ok": false, "reason_code": "stub", "who": "heartbeat-timer"}'
    exit __RC__ ;;
esac
exit 0
"""


def _write_exec(path, content):
    with open(path, "w") as fh:
        fh.write(content)
    os.chmod(path, 0o755)


class ProducerBase(unittest.TestCase):
    """Drives the real deploy/nightshift-heartbeat.sh through subprocess."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nightshift-heartbeat-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.tmp, "project")
        self.stubs = os.path.join(self.tmp, "stubs")
        for d in (self.home, os.path.join(self.proj, ".omni"),
                  os.path.join(self.proj, "bin"), self.stubs):
            os.makedirs(d)

        # The project's OWN ctl -- what an absolute default must find.
        _write_exec(os.path.join(self.proj, "bin", "nightshift-ctl"),
                    '#!/bin/sh\nexec %s "$@"\n' % CTL)

        self.plane = os.path.join(self.proj, ".omni", "control.db")
        self.ctl("init")

        # The frozen ctl, exactly where the unit's PATH would find it.
        self.frozen = os.path.join(self.stubs, "nightshift-ctl")
        _write_exec(self.frozen, FROZEN_STUB)

    # -- control plane ---------------------------------------------------
    def ctl(self, *args, now=None):
        cmd = [CTL]
        if now is not None:
            cmd += ["--now", str(now)]
        cmd += ["--db", self.plane] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def set_run(self, run_id):
        self.assertEqual(self.ctl("run", "current", "--set", run_id).returncode, 0)

    def write_beat(self, who, seq, run, session="seed", phase="seed",
                   boot=None, now=None):
        args = ["beat", "write", "--who", who, "--run", run, "--seq", str(seq),
                "--session", session, "--phase", phase]
        if boot:
            args += ["--boot", boot]
        res = self.ctl(*args, now=now)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def record(self, who):
        out = self.ctl("beat", "show", "--who", who).stdout
        return json.loads(out).get("record")

    def rec(self, who):
        """record(), asserting presence -- so a counterfactual run fails with a
        readable message instead of a TypeError on None."""
        got = self.record(who)
        self.assertIsNotNone(got, "no `heartbeat:%s` record was written" % who)
        return got

    def verdict(self, who, run=None):
        args = ["beat", "show", "--who", who]
        if run:
            args += ["--run", run]
        return json.loads(self.ctl(*args).stdout)

    # -- the script ------------------------------------------------------
    def unit_env(self, ctl=None, path_head=None):
        """The environment the .service really sets."""
        env = dict(os.environ)
        env.pop("NIGHTSHIFT_CTL", None)
        env["HOME"] = self.home
        env["PATH"] = (path_head or self.stubs) + ":" + UNIT_PATH_TAIL
        if ctl is not None:
            env["NIGHTSHIFT_CTL"] = ctl
        return env

    def run_script(self, script=None, **kw):
        return subprocess.run(
            ["bash", script or SCRIPT, self.proj],
            env=self.unit_env(**kw), capture_output=True, text=True)

    def script_at(self, rev):
        """Materialise a historical revision of the producer."""
        if not shutil.which("git"):
            self.skipTest("git unavailable")
        got = subprocess.run(
            ["git", "-C", REPO, "show", "%s:deploy/nightshift-heartbeat.sh" % rev],
            capture_output=True, text=True)
        if got.returncode != 0:
            self.skipTest("revision %s not reachable" % rev[:7])
        path = os.path.join(self.tmp, "producer-%s.sh" % rev[:7])
        _write_exec(path, got.stdout)
        return path


class SignalOwnership(ProducerBase):
    """#76 / ADR-0006 D2: five signals, five owners."""

    def test_timer_writes_its_own_signal(self):
        self.set_run("run-A")
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)
        rec = self.record("heartbeat-timer")
        self.assertIsNotNone(rec, "timer wrote no signal of its own")
        self.assertEqual(rec["seq"], 1)
        self.assertEqual(rec["run_id"], "run-A")
        self.assertEqual(rec["session"], "heartbeat-timer-durable")

    def test_timer_never_touches_the_program_signal(self):
        """The whole of #76 in one assertion."""
        self.set_run("run-A")
        self.write_beat("program", 7, "run-A", session="program-real")
        before = self.record("program")

        for _ in range(3):
            self.assertEqual(self.run_script().returncode, 0)

        self.assertEqual(self.record("program"), before,
                         "the timer modified heartbeat:program (ADR-0002 §1)")
        self.assertEqual(self.rec("heartbeat-timer")["seq"], 3)

    def test_repro_a_a_dead_program_does_not_read_healthy(self):
        """A dead Program must go stale and STAY stale across timer ticks."""
        self.set_run("run-A")
        # A Program beat one hour old, written on the virtual clock.
        self.write_beat("program", 5, "run-A", now=1_000_000)
        stale_before = self.verdict("program", run="run-A")
        self.assertFalse(stale_before["alive"])

        for _ in range(3):
            self.assertEqual(self.run_script().returncode, 0)

        after = self.verdict("program", run="run-A")
        self.assertFalse(after["alive"],
                         "a dead Program was laundered back to alive by the timer")
        self.assertEqual(self.assertIn("record", after) or after["record"]["seq"], 5, "timer advanced the Program's seq")
        self.assertEqual(self.assertIn("record", after) or after["record"]["ts"], stale_before["record"]["ts"],
                         "timer restamped the Program's ts")

    def test_repro_b_boot_id_check_is_not_erased(self):
        """After a reboot with no Program, wrong_boot must hold."""
        self.set_run("run-A")
        self.write_beat("program", 5, "run-A", boot=OTHER_BOOT)
        self.assertEqual(self.verdict("program", run="run-A").get("reason_code"),
                         "wrong_boot")

        for _ in range(3):
            self.assertEqual(self.run_script().returncode, 0)

        after = self.verdict("program", run="run-A")
        self.assertEqual(after.get("reason_code"), "wrong_boot",
                         "the timer restamped the live boot id over the record")
        self.assertEqual(self.assertIn("record", after) or after["record"]["boot_id"], OTHER_BOOT)

    def test_repro_c_run_id_comes_from_run_current_not_the_record(self):
        """A run roll must not pin the timer's signal to the stale run."""
        self.set_run("run-A")
        self.run_script()
        self.assertEqual(self.rec("heartbeat-timer")["run_id"], "run-A")

        # The run rolls. The Program is mid-turn and writes nothing.
        self.set_run("run-B")
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)

        rec = self.record("heartbeat-timer")
        self.assertEqual(rec["run_id"], "run-B",
                         "run_id was taken from the record, not from `run current`")
        self.assertEqual(rec["seq"], 1, "a run roll restarts this signal's sequence")

    def test_missing_own_record_bootstraps_seq_1(self):
        """The timer owns its signal, so writing its own seq=1 is not forgery."""
        self.set_run("run-A")
        self.assertIsNone(self.record("heartbeat-timer"))
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.rec("heartbeat-timer")["seq"], 1)

    def test_the_program_signal_is_never_named_in_the_script(self):
        """Structural: no code path can write --who program."""
        with open(SCRIPT) as fh:
            body = fh.read()
        code = [ln for ln in body.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        self.assertFalse([ln for ln in code if "--who program" in ln],
                         "the script names `--who program` in executable code")

    # -- COUNTERFACTUAL --------------------------------------------------
    def test_counterfactual_78b2771_wrote_the_program_signal(self):
        """The ownership tests must fail against 78b2771, and for the right reason.

        Not "the harness broke" -- the Program's own key is advanced and
        restamped, and the timer's key is never created.
        """
        old = self.script_at(REV_WROTE_PROGRAM)
        self.set_run("run-A")
        self.write_beat("program", 5, "run-A", now=1_000_000)
        before = self.record("program")

        res = subprocess.run(["bash", old, self.proj],
                             env=self.unit_env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

        after = self.record("program")
        self.assertEqual(after["seq"], 6,
                         "expected 78b2771 to advance the PROGRAM's seq")
        self.assertNotEqual(after["ts"], before["ts"],
                            "expected 78b2771 to restamp the PROGRAM's ts")
        self.assertEqual(after["session"], "heartbeat-timer-durable")
        self.assertIsNone(self.record("heartbeat-timer"),
                          "expected 78b2771 to write no signal of its own")


class ResolutionAndFailureMode(ProducerBase):
    """#40/#42: absolute resolution, and loud-vs-quiet exits."""

    def test_control_unit_env_without_override_advances_the_beat(self):
        """Unit PATH with the frozen ctl first, and no NIGHTSHIFT_CTL."""
        self.set_run("run-A")
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.rec("heartbeat-timer")["seq"], 1)
        self.assertEqual(self.rec("heartbeat-timer")["phase"], "durable-timer")

    def test_control_explicit_override_advances_the_beat(self):
        self.set_run("run-A")
        self.write_beat("heartbeat-timer", 11, "run-A")
        res = self.run_script(ctl=os.path.join(self.proj, "bin", "nightshift-ctl"))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.rec("heartbeat-timer")["seq"], 12)

    def test_counterfactual_frozen_ctl_fails_loudly(self):
        self.set_run("run-A")
        self.write_beat("heartbeat-timer", 5, "run-A")
        res = self.run_script(ctl=self.frozen)
        self.assertNotEqual(res.returncode, 0,
                            "frozen ctl reported SUCCESS -- this is the #40 defect")
        self.assertIn("beat", res.stderr.lower())
        self.assertEqual(self.rec("heartbeat-timer")["seq"], 5)

    def test_missing_ctl_fails_loudly(self):
        self.set_run("run-A")
        res = self.run_script(ctl=os.path.join(self.tmp, "no-such-binary"))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not found or not executable", res.stderr)

    def test_unreadable_plane_fails_loudly(self):
        self.set_run("run-A")
        os.remove(self.plane)
        os.makedirs(self.plane)  # present but unusable
        res = self.run_script()
        self.assertNotEqual(res.returncode, 0)
        self.assertTrue(res.stderr.strip())

    def test_no_current_run_is_quiet(self):
        """Nothing to attest yet -- not a fault, and nothing written."""
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr.strip(), "")
        self.assertIsNone(self.record("heartbeat-timer"))

    def test_uninitialised_project_is_quiet(self):
        shutil.rmtree(os.path.join(self.proj, ".omni"))
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr.strip(), "")

    def test_rejected_write_is_a_verdict_not_an_error(self):
        stub = os.path.join(self.stubs, "rc3")
        _write_exec(stub, WRITE_RC_STUB.replace("__RC__", "3"))
        res = self.run_script(ctl=stub)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr.strip(), "")

    def test_hard_write_failure_is_loud(self):
        stub = os.path.join(self.stubs, "rc1")
        _write_exec(stub, WRITE_RC_STUB.replace("__RC__", "1"))
        res = self.run_script(ctl=stub)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("beat write", res.stderr)

    def test_witness_4d13c59_was_a_silent_noop(self):
        """The original: exit 0, nothing written anywhere (WA-008/#64)."""
        old = self.script_at(REV_SILENT_NOOP)
        self.set_run("run-A")
        self.write_beat("program", 5, "run-A")
        res = subprocess.run(["bash", old, self.proj],
                             env=self.unit_env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(self.rec("program")["seq"], 5)
        self.assertIsNone(self.record("heartbeat-timer"))


if __name__ == "__main__":
    unittest.main()
