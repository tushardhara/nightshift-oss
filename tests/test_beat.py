"""ADR-0002 §2/§3 — the Program heartbeat record, validated by CONTENT.

These tests pin the contract published in `.omni/handoffs/LIVENESS-RECORD-v1.md`,
which `crew_beta`'s watchdog consumes. The defect they exist to prevent is the
one both architects independently named the most dangerous in the system: a
liveness signal that anything can produce, so a dead Program reads healthy
forever.

Every assertion here is about CONTENT, never mtime.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from ctlutil import CTL, Ctl


# `beat show` defaults the boot check to the HOST's current boot id. That
# default is the contract: a watchdog that has to remember a flag in order to
# reject a pre-reboot heartbeat will one day forget it, and the check that is
# silently lost is exactly the one ADR-0002 §3 exists to keep.
#
# Several tests below write with an explicit `--boot BOOT_A` and then read back
# with a bare `beat show`, which defaults to `host_boot_id()`. BOOT_A therefore
# has to be the REAL host boot id, not a plausible-looking constant: substitute
# a fake one and both sides of that comparison become the same invented value,
# the boot check passes vacuously, and the test still goes green while proving
# nothing. That is the failure mode ADR-0002 §3 is about, so it must not be
# introduced by the test file that guards it.
#
# Read it the way production does -- `host_boot_id()`, bin/nightshift-ctl:3802,
# same path and same OSError fallback -- so this module IMPORTS on every host.
# A bare open() here made the whole node unloadable off Linux, which is what DoD
# condition D04 reports as `test_beat: FileNotFoundError(2, ...)`: not one failing
# assertion but 40+ tests silently absent from the run.
def _host_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as _f:
            return _f.read().strip()
    except OSError:
        return ""


BOOT_A = _host_boot_id()                # this boot

# No host boot id (macOS, a container without /proc, a BSD): SKIP LOUDLY rather
# than run these assertions against a vacuous default. The module still loads,
# so the node is present and counted; the tests report as skipped with a reason
# instead of vanishing behind an import error.
if not BOOT_A:
    raise unittest.SkipTest(
        "no /proc/sys/kernel/random/boot_id on this host -- the ADR-0002 §3 "
        "boot-identity contract is Linux-specific and cannot be exercised "
        "meaningfully without a real host boot id (see D04/RT-21)")
BOOT_B = "22222222-2222-4222-8222-222222222222"   # some earlier boot


class BeatRecordTest(unittest.TestCase):
    """ADR-0002 §2 — the record and who may write it."""

    def test_beat_write_stores_the_full_record(self):
        c = Ctl()
        code, r = c.run("beat", "write", "--who", "program", "--run", "run-1",
                        "--session", "conv_abc", "--seq", "1",
                        "--boot", BOOT_A, "--phase", "wave-1")
        self.assertEqual(code, 0)
        rec = r["record"]
        # every field ADR-0002 §2 requires, present and typed
        self.assertEqual(rec["schema_version"], 1)
        self.assertEqual(rec["who"], "program")
        self.assertEqual(rec["run_id"], "run-1")
        self.assertEqual(rec["session"], "conv_abc")
        self.assertEqual(rec["boot_id"], BOOT_A)
        self.assertEqual(rec["seq"], 1)
        self.assertEqual(rec["phase"], "wave-1")
        self.assertIsInstance(rec["ts"], float)

    def test_beat_show_reads_back_the_same_record(self):
        c = Ctl()
        c.run("run", "current", "--set", "run-1")
        c.run("beat", "write", "--who", "program", "--run", "run-1",
              "--seq", "7", "--boot", BOOT_A)
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 0)
        self.assertTrue(r["alive"])
        self.assertEqual(r["record"]["seq"], 7)

    def test_signals_are_namespaced_per_owner(self):
        """§1: no signal may stand in for another. A backup beat must not
        satisfy a query for the Program's."""
        c = Ctl()
        c.run("beat", "write", "--who", "backup", "--run", "run-1",
              "--seq", "1", "--boot", BOOT_A)
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3)
        self.assertFalse(r["alive"])
        self.assertEqual(r["reason_code"], "missing")


class BeatValidationTest(unittest.TestCase):
    """ADR-0002 §3 — the watchdog rejects, it does not believe."""

    def _beat(self, c, **kw):
        if kw.get("run"):
            c.run("run", "current", "--set", kw["run"])
        argv = ["beat", "write", "--who", "program"]
        for k, v in kw.items():
            argv += ["--" + k, str(v)]
        return c.run(*argv)

    def test_non_increasing_sequence_is_refused_at_write(self):
        c = Ctl()
        self._beat(c, run="run-1", seq=5, boot=BOOT_A)
        code, r = self._beat(c, run="run-1", seq=5, boot=BOOT_A)
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "seq_not_increasing")
        code, r = self._beat(c, run="run-1", seq=4, boot=BOOT_A)
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "seq_not_increasing")
        # and the stored record was NOT clobbered by the refused write
        _, s = c.run("beat", "show", "--who", "program")
        self.assertEqual(s["record"]["seq"], 5)

    def test_new_boot_resets_the_sequence_and_says_so(self):
        """A reboot legitimately restarts the loop counter. It must be
        allowed, and it must be visible rather than silent."""
        c = Ctl()
        self._beat(c, run="run-1", seq=9, boot=BOOT_A)
        code, r = self._beat(c, run="run-2", seq=1, boot=BOOT_B)
        self.assertEqual(code, 0)
        self.assertTrue(r["reset"])

    def test_stale_record_is_not_alive(self):
        c = Ctl()
        self._beat(c, run="run-1", seq=1, boot=BOOT_A)
        time.sleep(0.25)
        code, r = c.run("beat", "show", "--who", "program", "--max-age", "0.1")
        self.assertEqual(code, 3)
        self.assertFalse(r["alive"])
        self.assertEqual(r["reason_code"], "stale")
        self.assertGreater(r["age"], 0.1)

    def test_previous_boot_is_not_alive_BY_DEFAULT(self):
        """The record survived a reboot; the Program did not. No flag is
        needed to catch it — this is the post-reboot stale heartbeat that
        the mtime heuristic believed."""
        c = Ctl()
        self._beat(c, run="run-1", seq=1, boot=BOOT_B)
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3)
        self.assertFalse(r["alive"])
        self.assertEqual(r["reason_code"], "wrong_boot")
        # and an explicitly-named boot is honoured too
        code, r = c.run("beat", "show", "--who", "program", "--boot", BOOT_B)
        self.assertEqual(code, 0)

    def test_wrong_run_is_not_alive(self):
        c = Ctl()
        self._beat(c, run="run-1", seq=1, boot=BOOT_A)
        code, r = c.run("beat", "show", "--who", "program", "--run", "run-2")
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "wrong_run")

    def test_future_timestamp_is_not_alive(self):
        c = Ctl()
        self._beat(c, run="run-1", seq=1, boot=BOOT_A,
                   **{"ts-override": time.time() + 3600})
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "future")

    def test_malformed_record_is_not_alive_and_does_not_crash(self):
        """A corrupt row must read as NOT ALIVE (exit 3), never as an
        unhandled traceback and never as alive."""
        c = Ctl()
        self._beat(c, run="run-1", seq=1, boot=BOOT_A)
        import sqlite3
        cx = sqlite3.connect(c.db)
        cx.execute("UPDATE meta SET value='{not json' WHERE key=?",
                   ("heartbeat:program",))
        cx.commit()
        cx.close()
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3)
        self.assertFalse(r["alive"])
        self.assertEqual(r["reason_code"], "malformed")

    def test_unknown_schema_version_is_not_alive(self):
        """SECURITY B1. `read_beat` checked that schema_version was PRESENT and
        never what it was, so a record from a future writer read ALIVE. The
        published contract (handoff §1) tells crew_beta the opposite: "a
        consumer that does not recognise the version must report not alive,
        never guess" — and §5 makes `beat show` the consumer of record. This is
        the fail-OPEN direction on the one signal ADR-0002 exists to make
        trustworthy."""
        import sqlite3
        c = Ctl()
        self._beat(c, run="run-1", seq=1, boot=BOOT_A)
        cx = sqlite3.connect(c.db)
        rec = json.loads(cx.execute(
            "SELECT value FROM meta WHERE key='heartbeat:program'").fetchone()[0])
        rec["schema_version"] = 99
        cx.execute("UPDATE meta SET value=? WHERE key='heartbeat:program'",
                   (json.dumps(rec),))
        cx.commit(); cx.close()
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3)
        self.assertFalse(r["alive"])
        self.assertEqual(r["reason_code"], "unknown_version")

    def test_previous_runs_heartbeat_is_not_alive_BY_DEFAULT(self):
        """SECURITY B2. --run was optional, and the handoff's own §5 recipe
        omitted it — so a heartbeat left by a previous, DEAD run read alive
        indefinitely while it stayed fresh and the box had not rebooted. That
        is the ADR's own failure class: a record outliving the thing it
        attests. The expected run now resolves from the control plane, so the
        check is on by default and cannot be forgotten."""
        c = Ctl()
        c.run("run", "current", "--set", "run-1")
        self._beat(c, run="run-1", seq=1, boot=BOOT_A)
        code, _ = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 0)          # this run: alive

        # the Program dies; a NEW run starts. The old record is still fresh.
        c.run("run", "current", "--set", "run-2")
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3, "a dead run's fresh heartbeat must not read alive")
        self.assertEqual(r["reason_code"], "wrong_run")

    def test_no_expected_run_fails_closed(self):
        """With no expected run resolvable the check cannot be performed, so
        the answer is NOT ALIVE with a remediation — never a silent pass."""
        c = Ctl()
        # deliberately does NOT record a current run
        c.run("beat", "write", "--who", "program", "--run", "run-1",
              "--seq", "1", "--boot", BOOT_A)
        code, r = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "no_expected_run")
        self.assertIn("run current --set", r["reason"])
        # an explicit --run still works, and so does the env override
        code, _ = c.run("beat", "show", "--who", "program", "--run", "run-1")
        self.assertEqual(code, 0)

    def test_not_alive_is_exit_3_never_exit_1(self):
        """Exit-code contract published to crew_beta: 3 is a VERDICT
        (denied/held/cooling), not an error. A watchdog that treats 3 as a
        crash is the bug this line prevents."""
        c = Ctl()
        c.run("run", "current", "--set", "run-1")
        for argv in (("beat", "show", "--who", "program"),
                     ("beat", "show", "--who", "nobody")):
            code, r = c.run(*argv)
            self.assertEqual(code, 3, argv)
            self.assertFalse(r["alive"])
            self.assertIn("reason", r)


class ControlPlaneUnreadableTest(unittest.TestCase):
    """LIVENESS-RECORD-v1 §3 — an unreadable control plane is a VERDICT of
    not-alive, not a crash.

    §3 promises consumers that `beat show` output is "always one JSON object
    on stdout, on every path including denials". The error path broke that
    promise: a corrupt, unreadable or uncreatable control plane produced a
    Python traceback on stderr, NO JSON at all on stdout, and exit **1**.

    Exit 1 is not a verdict. `crew_beta`'s watchdog branches on 0 (alive) vs
    3 (not alive); an exit-1 traceback is neither, so the dead-man's switch
    mishandles the single failure it most needs to catch — the control plane
    itself being gone — as an unhandled error rather than as "not alive".
    Exit 3 = denied/verdict is the discipline every other nightshift-ctl
    subcommand already follows, and an unreadable control plane is squarely a
    verdict: nothing can attest the Program is alive, so it is not alive.

    Raised by `crew_beta` in cross-crew review of PR #16; shape ruled by
    `security`. Refs #11.
    """

    EXPECTED = {"alive": False, "reason_code": "control_plane_unreadable"}

    def _mkomni(self):
        d = tempfile.mkdtemp(prefix="nightshift-ctl-unreadable-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        omni = os.path.join(d, ".omni")
        os.makedirs(omni)
        with open(os.path.join(omni, "capacity.json"), "w") as f:
            json.dump({"max_concurrent_workers": 4, "reserve_for_oncall": 1}, f)
        return omni

    def _show(self, db):
        return subprocess.run(
            [CTL, "--db", db, "beat", "show", "--who", "program"],
            capture_output=True, text=True,
        )

    def _assert_verdict(self, p):
        """Assert BOTH halves of the contract: the exit code AND the payload.

        Asserting only the exit code would pass on a bare `exit 3` that prints
        nothing, and asserting only the payload would pass on a body printed
        with exit 1. §3 promises both, so both are pinned here.
        """
        self.assertNotIn("Traceback", p.stderr,
                         "an unreadable control plane must not crash")
        self.assertTrue(p.stdout.strip(),
                        "§3: one JSON object on stdout on EVERY path")
        body = json.loads(p.stdout.strip().splitlines()[-1])
        # exact keys, not a superset: the ruled shape is these two and no more
        self.assertEqual(body, self.EXPECTED)
        self.assertEqual(sorted(body), ["alive", "reason_code"])
        self.assertEqual(p.returncode, 3,
                         "3 = verdict; 1 would be a crash, and a crash is not "
                         "a verdict")

    def test_corrupt_control_plane_is_a_verdict_not_a_crash(self):
        """A control.db that is not a SQLite database at all."""
        omni = self._mkomni()
        db = os.path.join(omni, "control.db")
        with open(db, "w") as f:
            f.write("this is not a sqlite database")
        self._assert_verdict(self._show(db))

    @unittest.skipIf(os.geteuid() == 0, "root bypasses file permissions")
    def test_unreadable_control_plane_file_is_a_verdict_not_a_crash(self):
        """The database exists but this process may not open it."""
        omni = self._mkomni()
        db = os.path.join(omni, "control.db")
        subprocess.run([CTL, "--db", db, "init"], capture_output=True)
        os.chmod(db, 0o000)
        self.addCleanup(os.chmod, db, 0o600)
        self._assert_verdict(self._show(db))

    @unittest.skipIf(os.geteuid() == 0, "root bypasses file permissions")
    def test_absent_control_plane_dir_is_a_verdict_not_a_crash(self):
        """The control plane is absent and cannot even be created."""
        omni = self._mkomni()
        parent = os.path.dirname(omni)
        os.chmod(parent, 0o500)
        self.addCleanup(os.chmod, parent, 0o700)
        self._assert_verdict(self._show(os.path.join(parent, "gone", "control.db")))

    def test_a_readable_but_empty_control_plane_still_reads_missing(self):
        """The fix must not swallow the ordinary case.

        An EXISTING but empty control plane is not unreadable: ctl opens it,
        finds no heartbeat record, and reports `missing`. Only a plane that
        genuinely cannot be opened reports `control_plane_unreadable`, so the
        interception cannot mask a plain empty control plane.

        AMENDED by ADR-0005 D2 (issue #31). This test used to assert the same
        thing about an ABSENT database, because ctl created one on demand:
        "absent-but-creatable is not unreadable". Silent creation is exactly
        the defect #31 removes -- from the wrong directory it manufactured a
        phantom control plane on which `slot acquire` always granted -- so
        "absent" is no longer an ordinary case, and the sibling test below
        pins its new meaning. The property this test was written to protect
        (an empty plane reads `missing`, not `unreadable`) is unchanged and
        still asserted; only the setup now creates the plane explicitly.
        """
        omni = self._mkomni()
        db = os.path.join(omni, "control.db")
        subprocess.run([CTL, "--db", db, "init"], capture_output=True)
        p = self._show(db)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(json.loads(p.stdout)["reason_code"], "missing")

    def test_an_absent_control_plane_reads_unreadable_not_missing(self):
        """The other half of the ADR-0005 D2 split, pinned deliberately.

        `missing` means "the plane is fine, nobody wrote a heartbeat".
        `control_plane_unreadable` means "I cannot see a plane at all". Before
        #31 those two collapsed into the first, because ctl created the plane
        it was asked about -- so a watchdog pointed at the WRONG PATH got a
        confident "no heartbeat yet" instead of "your control plane is gone".
        Both are fail-closed and both exit 3; the distinction is what an
        operator needs to tell a quiet night from a broken install.
        """
        omni = self._mkomni()
        db = os.path.join(omni, "definitely-not-created", "control.db")
        p = self._show(db)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(json.loads(p.stdout)["reason_code"],
                         "control_plane_unreadable")
        self.assertFalse(os.path.exists(os.path.dirname(db)),
                         "asking about a plane must never create one")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses file permissions")
    def test_other_subcommands_do_not_fake_a_verdict(self):
        """Deliberate scope: this verdict belongs to `beat show` ALONE.

        `beat show` exists to answer "is this owner alive?", so an unreadable
        plane has a correct answer: no. `slot acquire` has no such answer —
        turning its failure into exit 3 would report "denied, queue and retry"
        for a plane that is broken, hiding an outage behind a routine verdict.
        Those commands keep failing loudly.
        """
        omni = self._mkomni()
        db = os.path.join(omni, "control.db")
        with open(db, "w") as f:
            f.write("this is not a sqlite database")
        p = subprocess.run(
            [CTL, "--db", db, "slot", "acquire", "--holder", "crew_alpha/x"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(p.returncode, 3,
                            "a broken control plane must not masquerade as a "
                            "routine denial")
        self.assertNotIn("control_plane_unreadable", p.stdout)


if __name__ == "__main__":
    unittest.main()


class BeatProgressTest(unittest.TestCase):
    """ADR-0006 D4 — intra-turn progress beats (#68).

    #68 is real and has been observed: `beat show` returned `alive: false`
    while the Program was actively working, because one long turn outlives its
    own 900s `max_age`. The fix is that the Program beats MORE OFTEN. It is
    emphatically not that something else beats for it — that was PR #71, and
    it made a dead Program read healthy forever.

    Two properties are under test here, and they are the whole design:

    1. `progress` refreshes `ts` and never advances `seq`. `seq` means "last
       COMPLETED iteration" (ADR-0002 §2). That is why this is a separate
       verb: reusing `beat write` would mean weakening its strictly-increasing
       `seq` guard, and that guard is a control (#39).
    2. `progress` REFUSES rather than repairs. It may only extend a record the
       owner already earned. The boot-mismatch case is the sharpest: `progress`
       refuses where `beat write` RESTAMPS, and that restamping is exactly how
       PR #71's timer erased the post-reboot check in one tick. The contrast is
       asserted directly, in `test_beat_write_restamps_the_boot_id_that_progress_refuses`.
    """

    # Deterministic stamps. `beat write --ts-override` sets ts AND seq_ts;
    # `beat progress --ts-override` sets ts only. Explicit stamps rather than
    # sleeping make "did seq_ts move?" an exact assertion.
    #
    # They are anchored to WALL CLOCK, in the recent past, on purpose.
    # `beat show` computes age against now() and there is no --now here, so
    # virtual-clock-style constants (1_000_000.0) would read ~1.8e9 seconds
    # stale and every liveness assertion below would fail for a reason that
    # has nothing to do with what it is testing. That is the mixed-clock trap
    # ctlutil's module docstring warns about; it caught this class once.
    def setUp(self):
        t0 = time.time()
        self.T_WRITE = t0 - 600.0        # inside the 900s default max_age
        self.T_PROGRESS = t0 - 300.0     # later, still in the past
        self.T_WRITE2 = t0 - 100.0       # later still

    def _seed(self, c, run="run-1", seq=5, boot=BOOT_A, ts=None):
        c.run("run", "current", "--set", run)
        code, r = c.run("beat", "write", "--who", "program", "--run", run,
                        "--seq", str(seq), "--boot", boot,
                        "--ts-override", repr(ts if ts is not None else self.T_WRITE))
        self.assertEqual(code, 0, r)
        return r["record"]

    def _stored(self, c, who="program"):
        """Read the record straight out of the plane, not through the verb
        that just claimed to have written it."""
        import sqlite3
        cx = sqlite3.connect(c.db)
        row = cx.execute("SELECT value FROM meta WHERE key=?",
                         ("heartbeat:" + who,)).fetchone()
        cx.close()
        return json.loads(row[0]) if row else None

    # ---- the core behaviour ------------------------------------------------

    def test_progress_refreshes_ts_without_advancing_seq(self):
        c = Ctl()
        before = self._seed(c, seq=5)
        code, r = c.run("beat", "progress", "--who", "program",
                        "--ts-override", repr(self.T_PROGRESS))
        self.assertEqual(code, 0, r)
        self.assertFalse(r["advanced_seq"])
        after = self._stored(c)
        self.assertEqual(after["ts"], self.T_PROGRESS, "progress must refresh ts")
        self.assertGreater(after["ts"], before["ts"])
        self.assertEqual(after["seq"], 5,
                         "progress must NOT advance seq: seq means a COMPLETED "
                         "iteration and progress is not completion")

    def test_a_progress_beat_makes_a_stale_record_read_alive_again(self):
        """The point of the whole verb (#68), asserted end to end: a record
        aged past max_age reads NOT alive, and a progress beat — with no
        completed iteration, so no seq change — restores it."""
        c = Ctl()
        self._seed(c, seq=5)
        code, r = c.run("beat", "show", "--who", "program", "--max-age", "1")
        self.assertEqual(code, 3, "seeded record should be far past a 1s max-age")
        self.assertEqual(r["reason_code"], "stale")
        code, _ = c.run("beat", "progress", "--who", "program")
        self.assertEqual(code, 0)
        code, r = c.run("beat", "show", "--who", "program", "--max-age", "60")
        self.assertEqual(code, 0, "a progress beat must restore freshness")
        self.assertTrue(r["alive"])
        self.assertEqual(r["record"]["seq"], 5, "and still no completed iteration")

    # ---- it refuses; it never repairs --------------------------------------

    def test_progress_refuses_on_run_mismatch_and_writes_nothing(self):
        c = Ctl()
        self._seed(c, run="run-1", seq=5)
        c.run("run", "current", "--set", "run-2")     # the run rolled
        code, r = c.run("beat", "progress", "--who", "program",
                        "--ts-override", repr(self.T_PROGRESS))
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "wrong_run")
        self.assertFalse(r["wrote"])
        after = self._stored(c)
        self.assertEqual(after["ts"], self.T_WRITE,
                         "a refused progress beat must not touch the record")
        self.assertEqual(after["run_id"], "run-1",
                         "and must never re-pin the record to the live run")

    def test_progress_refuses_on_boot_mismatch_and_writes_nothing(self):
        """Reproduction (b) from ADR-0006, kept closed. The record survived
        the reboot; the process that earned it did not."""
        c = Ctl()
        self._seed(c, seq=5, boot=BOOT_B)             # written before a reboot
        code, r = c.run("beat", "progress", "--who", "program",
                        "--boot", BOOT_A,             # live boot differs
                        "--ts-override", repr(self.T_PROGRESS))
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "wrong_boot")
        self.assertFalse(r["wrote"])
        after = self._stored(c)
        self.assertEqual(after["boot_id"], BOOT_B,
                         "progress must never restamp the boot id")
        self.assertEqual(after["ts"], self.T_WRITE)
        # and the verdict is unchanged: still not alive, for the same reason
        code, r = c.run("beat", "show", "--who", "program", "--boot", BOOT_A)
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "wrong_boot")

    def test_beat_write_restamps_the_boot_id_that_progress_refuses(self):
        """CHARACTERIZATION, not a regression test — this behaviour is
        unchanged by this PR and passes against the pre-fix binary.

        It is here because it is the CONTRAST that makes `progress` worth
        having. `cmd_beat_write` does `boot = args.boot or host_boot_id()`, so
        a write with no explicit --boot silently restamps a pre-reboot record
        with the live boot id and a dead box reads healthy. That is the
        mechanism of ADR-0006 reproduction (b). `progress` is the verb that
        refuses to do this; if this test ever starts failing, the contrast has
        moved and the progress guard should be re-read against it.
        """
        c = Ctl()
        self._seed(c, run="run-1", seq=5, boot=BOOT_B)
        code, r = c.run("beat", "show", "--who", "program", "--boot", BOOT_A)
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "wrong_boot")
        # beat write, with no --boot, restamps to the live boot id
        code, r = c.run("beat", "write", "--who", "program", "--run", "run-1",
                        "--seq", "6")
        self.assertEqual(code, 0)
        self.assertNotEqual(r["record"]["boot_id"], BOOT_B,
                            "beat write restamps — this is the behaviour "
                            "progress deliberately does not have")

    def test_progress_refuses_when_no_record_exists(self):
        """A progress beat extends a record the owner earned; it never
        creates one. Forging seq=1 is ADR-0004 option A, already rejected."""
        c = Ctl()
        c.run("run", "current", "--set", "run-1")
        code, r = c.run("beat", "progress", "--who", "program")
        self.assertEqual(code, 3)
        self.assertEqual(r["reason_code"], "missing")
        self.assertFalse(r["wrote"])
        self.assertIsNone(self._stored(c), "nothing may be written")

    def test_progress_refusal_is_exit_3_never_exit_1(self):
        """House contract: 0 accepted, 3 verdict, 1 real error."""
        c = Ctl()
        c.run("run", "current", "--set", "run-1")
        code, r = c.run("beat", "progress", "--who", "program")
        self.assertEqual(code, 3)
        self.assertIn("reason", r)
        self.assertFalse(r["ok"])

    # ---- seq_ts ------------------------------------------------------------

    def test_seq_ts_advances_only_when_seq_advances(self):
        """Closes the failure mode progress beats would otherwise INTRODUCE: a
        Program wedged in an infinite loop would beat forever and read
        perfectly healthy on freshness alone. seq_ts lets a consumer tell
        `alive and iterating` from `alive but has completed nothing in N`."""
        c = Ctl()
        self._seed(c, seq=1, ts=self.T_WRITE)
        rec0 = self._stored(c)
        # Asserted, not subscripted. A bare rec["seq_ts"] discriminates only
        # until someone rewrites it as rec.get("seq_ts", 0) -- and then the
        # counterfactual goes vacuous with no signal at all. The question a
        # counterfactual has to answer is not "did it blow up" but WOULD THIS
        # HAVE FAILED IF THE FEATURE WERE PRESENT BUT BROKEN.
        self.assertIn("seq_ts", rec0,
                      "a completed iteration must record when seq advanced")
        self.assertEqual(rec0["seq_ts"], self.T_WRITE)

        code, _ = c.run("beat", "progress", "--who", "program",
                        "--ts-override", repr(self.T_PROGRESS))
        self.assertEqual(code, 0)
        rec = self._stored(c)
        self.assertEqual(rec["ts"], self.T_PROGRESS, "freshness moved")
        self.assertEqual(rec["seq_ts"], self.T_WRITE,
                         "seq did not advance, so seq_ts must NOT move — "
                         "otherwise a wedged loop looks like a working one")

        code, _ = c.run("beat", "write", "--who", "program", "--run", "run-1",
                        "--seq", "2", "--boot", BOOT_A,
                        "--ts-override", repr(self.T_WRITE2))
        self.assertEqual(code, 0)
        rec = self._stored(c)
        self.assertEqual(rec["seq"], 2)
        self.assertEqual(rec["seq_ts"], self.T_WRITE2,
                         "a completed iteration DOES move seq_ts")

    def test_seq_ts_is_additive_and_the_schema_version_is_NOT_bumped(self):
        """ADR-0006 D4(d), and the sharpest edge in this change.

        `read_beat` fails closed on a version mismatch and `cmd_beat_show`
        turns that into NOT ALIVE. That direction is correct and must stay —
        but it means bumping the version for a merely ADDITIVE field would
        read the entire org NOT ALIVE at the instant of deploy, until every
        consumer was updated: a self-inflicted outage of the liveness signal,
        caused by the clause meant to protect it.

        The literal 1 is deliberate. Asserting against
        HEARTBEAT_SCHEMA_VERSION would still pass if someone bumped both the
        constant and this test together, which is precisely the change this
        test exists to make someone stop and think about.
        """
        c = Ctl()
        self._seed(c, seq=1)
        rec = self._stored(c)
        self.assertIn("seq_ts", rec)
        self.assertEqual(rec["schema_version"], 1,
                         "seq_ts is ADDITIVE: do not bump the schema version "
                         "for a new optional field (ADR-0006 D4d)")
        code, _ = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 0, "a record carrying seq_ts must still read alive")

    def test_a_legacy_record_without_seq_ts_still_reads_alive(self):
        """CHARACTERIZATION, not a regression test — and crew_beta was right
        to insist on the distinction (PR #78 review).

        The property is "a record lacking seq_ts still reads alive", and it
        ALREADY HELD before this change: seed a record with the pre-change
        binary and `beat show` returns exit=0. So this can never be evidence
        that this change fixed anything. It is kept because it is the
        forward-compatibility guarantee that makes seq_ts safe to add without
        a version bump — but it is labelled for what it is.

        Its earlier form failed pre-change only because the fixture did
        `del rec["seq_ts"]` on a binary that never writes that key: failure
        through HARNESS MECHANICS, not through the variable under test. That
        is the same shape as a vacuous control, and it is why this is now
        split from the no-backfill assertion below, which IS a regression
        test.
        """
        import sqlite3
        c = Ctl()
        self._seed(c, seq=4)
        cx = sqlite3.connect(c.db)
        rec = json.loads(cx.execute(
            "SELECT value FROM meta WHERE key='heartbeat:program'").fetchone()[0])
        rec.pop("seq_ts", None)                # as an older binary wrote it
        cx.execute("UPDATE meta SET value=? WHERE key='heartbeat:program'",
                   (json.dumps(rec),))
        cx.commit(); cx.close()
        self.assertNotIn("seq_ts", self._stored(c), "fixture precondition")

        code, _ = c.run("beat", "show", "--who", "program")
        self.assertEqual(code, 0, "a legacy record must still read alive")

    def test_progress_does_not_backfill_seq_ts_on_a_legacy_record(self):
        """REGRESSION test, and the half of the old hybrid that genuinely
        discriminates.

        It fails against the pre-change binary because the verb does not
        exist, and — the stronger property, per crew_beta's ruling — it would
        ALSO fail if the verb existed but were broken: a `progress`
        implementation that backfilled seq_ts to now would trip the final
        assertion. An unknown last-completion time must be reported as
        unknown, never invented as recent, because inventing it asserts an
        iteration that never happened — which is the exact failure seq_ts
        exists to make visible.
        """
        import sqlite3
        c = Ctl()
        self._seed(c, seq=4)
        cx = sqlite3.connect(c.db)
        rec = json.loads(cx.execute(
            "SELECT value FROM meta WHERE key='heartbeat:program'").fetchone()[0])
        rec.pop("seq_ts", None)
        cx.execute("UPDATE meta SET value=? WHERE key='heartbeat:program'",
                   (json.dumps(rec),))
        cx.commit(); cx.close()

        code, _ = c.run("beat", "progress", "--who", "program",
                        "--ts-override", repr(self.T_PROGRESS))
        self.assertEqual(code, 0, "a legacy record must still be progressable")
        after = self._stored(c)
        self.assertEqual(after["ts"], self.T_PROGRESS)
        self.assertEqual(after["seq"], 4)
        self.assertNotIn("seq_ts", after,
                         "progress must not invent a seq_ts it cannot know")
