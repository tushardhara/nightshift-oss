"""Shared helpers for the control-plane test suite.

Every test drives the real CLI (`bin/nightshift-ctl`) through subprocess, so
atomicity is exercised across genuinely separate processes — the same way
concurrent agents hit it in production.

VIRTUAL TIME (ADR-0005 D4, issue #32)
-------------------------------------
`Ctl(clock=VIRTUAL_T0)` pins every command this instance runs to a virtual
clock, advanced explicitly with `advance()`. Tests that need a TTL to lapse
move the clock instead of sleeping.

The clock is a property of the INSTANCE, not of a single call, and that is
deliberate. Expiry is computed against whatever `now()` returns, so a test
that ran some commands on the virtual clock and others on the wall clock
would have its virtual timestamps (~1e6) look ancient to a wall-clock command
(~1.8e9) and get everything reaped instantly. Per-instance makes the mixed
state unrepresentable rather than merely discouraged.

Injection goes through `--now` on ARGV, built in `cmdline()`. It deliberately
does NOT go through the environment: an inherited variable would leak out of
the harness into any shell or child process that copied the recipe, and the
clock drives TTL expiry, so a leaked value silently reaps a real control
plane (`expire_stale` runs inside `slot list`, `lease list`, `claim show` and
`dump`) or silently reports a dead Program as alive. Argv does not inherit.
If you are tempted to add an env fallback back for convenience, read the
comment on the clock block in `bin/nightshift-ctl` first.
"""

import contextlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# $NIGHTSHIFT_TEST_CTL points the whole suite at a DIFFERENT nightshift-ctl.
# That is what makes the RT-25 / RT-26 pre-fix captures reproducible: extract
# the base-revision binary and re-run these tests against it. A regression
# test nobody can watch fail is not evidence (WA-008).
CTL = os.environ.get("NIGHTSHIFT_TEST_CTL") or os.path.join(
    REPO, "bin", "nightshift-ctl")

# Far enough from 0 that `t - max_age` stays positive for a day-sized max-age,
# and far from wall-clock values so a mix-up is obvious in a failure message.
VIRTUAL_T0 = 1_000_000.0


class Ctl:
    def __init__(self, capacity=None, clock=None):
        self.dir = tempfile.mkdtemp(prefix="nightshift-ctl-test-")
        self.omni = os.path.join(self.dir, ".omni")
        os.makedirs(self.omni)
        self.db = os.path.join(self.omni, "control.db")
        self.clock = clock
        cap = {"max_concurrent_workers": 4, "reserve_for_oncall": 1}
        cap.update(capacity or {})
        with open(os.path.join(self.omni, "capacity.json"), "w") as f:
            json.dump(cap, f)
        self.run("init")
        self._normal_fixture_seq = 0

    def normal_slot_cmdline(self, *, task=None, crew="crew_alpha",
                            agent="eng_claude", title=None,
                            purpose="implement", ttl=None):
        """Acquire one ADR-0033-classified normal slot for an ordinary test.

        Before ADR-0033, capacity/TTL tests used invented taskless holders.
        Normal allocation is now deliberately task-bound and fail-closed, so
        those fixtures need real control-plane facts even when the property
        under test is only capacity. Tests of malformed/missing facts still
        call ``run('slot', 'acquire', ...)`` directly.
        """
        self._normal_fixture_seq += 1
        task = task or f"fixture-slot-{self._normal_fixture_seq}"
        title = title or f"fixture-{task}"
        code, body = self.run("run", "current", "--set", "fixture-run")
        if code != 0:
            raise AssertionError(body)
        code, body = self.run(
            "task", "upsert", "--id", task, "--crew", crew,
            "--agent", agent, "--title", title, "--state", "pending",
            "--size", "S",
        )
        if code != 0:
            raise AssertionError(body)
        argv = [
            "slot", "acquire", "--holder", f"{crew}/{agent}",
            "--crew", crew, "--task", task, "--agent", agent,
            "--title", title, "--purpose", purpose,
        ]
        if ttl is not None:
            argv += ["--ttl", ttl]
        return self.cmdline(*argv)

    def acquire_normal(self, **kwargs):
        command = self.normal_slot_cmdline(**kwargs)
        process = subprocess.run(command, capture_output=True, text=True)
        body = None
        if process.stdout.strip():
            body = json.loads(process.stdout.strip().splitlines()[-1])
        return process.returncode, body

    def seed_admission(self, *, task, slot_id, crew="crew_alpha",
                       agent="eng_claude", title=None):
        """Create post-root-policy facts for a non-policy fixture.

        Use only when the test's subject is downstream state such as claim
        expiry or restart recovery. Admission-policy tests must exercise the
        real evaluator instead and live in ``test_admission_gate.py``.
        """
        title = title or f"fixture-{task}"
        admitted_at = self.clock if self.clock is not None else time.time()
        with contextlib.closing(sqlite3.connect(self.db)) as cx, cx:
            cx.execute(
                "INSERT INTO task_admissions("
                "task_id,new_slot_id,crew,agent,title,admitted_at) "
                "VALUES(?,?,?,?,?,?)",
                (task, slot_id, crew, agent, title, admitted_at),
            )
            cx.execute(
                "UPDATE tasks SET state='building' WHERE id=?", (task,),
            )
            cx.execute(
                "UPDATE worker_slots SET dispatched_at=?,bound_agent=?,"
                "bound_title=? WHERE id=?",
                (admitted_at, agent, title, slot_id),
            )

    def advance(self, seconds):
        """Move virtual time forward. The replacement for time.sleep()."""
        if self.clock is None:
            raise AssertionError("advance() needs Ctl(clock=...)")
        self.clock += seconds
        return self.clock

    def cmdline(self, *argv, now=None):
        """Build the argv for one command, clock override included.

        The clock rides on ARGV and there is no environment channel for it --
        see the note in the module docstring. Because this is the single place
        argv is built, `run_parallel(c.cmdline(...))` gets the same clock as
        `c.run(...)`; when the injection lived in `env()` it did not, since
        `run_parallel` never passed an env.
        """
        t = now if now is not None else self.clock
        pre = [CTL, "--db", self.db]
        if t is not None:
            pre += ["--now", repr(float(t))]
        return pre + [str(a) for a in argv]

    def run(self, *argv, stdin=None, now=None):
        """Run nightshift-ctl; return (exit_code, parsed_json)."""
        p = subprocess.run(
            self.cmdline(*argv, now=now), capture_output=True, text=True,
            input=stdin,
        )
        body = None
        if p.stdout.strip():
            body = json.loads(p.stdout.strip().splitlines()[-1])
        return p.returncode, body


def run_parallel(cmdlines):
    """Launch all command lines at once; return list of (exit_code, json)."""
    procs = [
        subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for c in cmdlines
    ]
    results = []
    for p in procs:
        so, _ = p.communicate()
        body = json.loads(so.strip().splitlines()[-1]) if so.strip() else None
        results.append((p.returncode, body))
    return results
