"""Audit tests 3 and 5: crash and restart recovery.

- A worker dies holding a slot + claim: `reap` (or any later transaction)
  returns the capacity and marks the run dead — deterministically.
- A box restart mid-wave: all state is in .omni/control.db, so a fresh
  process reconstructs exactly the same picture and a recovery dispatch
  cannot duplicate a still-claimed task.
"""

import time
import unittest

from ctlutil import Ctl, VIRTUAL_T0


class Recovery(unittest.TestCase):
    def test_dead_worker_reaped_slot_claim_run(self):
        c = Ctl(clock=VIRTUAL_T0)
        _, slot = c.acquire_normal(task="t1", title="t1", ttl="0.2")
        c.seed_admission(task="t1", slot_id=slot["slot_id"], title="t1")
        _, run = c.run("run", "start", "--agent", "eng_claude", "--title",
                       "t1", "--slot", slot["slot_id"], "--task", "t1")
        c.run("claim", "acquire", "--task", "t1", "--claimant", "lead",
              "--agent", "eng_claude", "--title", "t1",
              "--ttl", "0.2")
        c.advance(0.3)  # worker dies silently
        code, reaped = c.run("reap")
        self.assertEqual(code, 0)
        self.assertEqual(reaped["slots_reaped"], 1)
        self.assertEqual(reaped["claims_expired"], 1)
        _, listing = c.run("slot", "list")
        self.assertEqual(listing["active"], [])
        # capacity actually returned
        code, _ = c.acquire_normal(task="next-task")
        self.assertEqual(code, 0)

    def test_rt26_explicit_reap_is_immune_to_setup_scheduling(self):
        """RT-26 — the flake in #32, made deterministic (ADR-0005 D4).

        `platform` root-caused the load-sensitive failure of
        test_dead_worker_reaped_slot_claim_run: expiry is lazy AND explicit
        (bin/nightshift-ctl:24), so every mutating command expires stale rows
        first. The test acquires a slot with TTL 0.2, runs two more CLI
        subprocesses, then asserts the EXPLICIT `reap` collected it. Under
        load the setup gap alone exceeds 0.2s, so `claim acquire`'s lazy
        expiry already reaped the slot and `reap` correctly returns 0. The
        test was asserting something the design never promised.

        Lengthening the sleep cannot fix that: the race is between
        `slot acquire` and `claim acquire`, both upstream of the sleep.

        This test pins the property that actually matters — **the answer must
        not depend on how the OS scheduled the setup** — and it does so
        deterministically, by forcing the gap rather than hoping for it. The
        0.35s of REAL time below is longer than the 0.2s TTL on any box, so
        the condition that 8 CPU hogs produced by luck is produced here on
        purpose, every run, on an idle machine.

        WA-008 — how this test fails, and what that does and does not prove.

        This record used to say that running the test against the pre-fix
        binary reproduces `0 != 1`, the signature `platform` recorded under
        load. That was TRUE when written — the harness at 685e773 injected
        the clock through `$NIGHTSHIFT_NOW`, which a pre-fix binary silently
        ignores — and was falsified by 265ff26, which made the clock
        argv-only at `security`'s request. The code moved; the record did
        not. Corrected under #55, measured rather than asserted:

            harness         binary         result
            --------------  -------------  -------------------------------
            HEAD (argv)     HEAD           OK                       (0.68s)
            HEAD (argv)     7c16fbe base   TypeError, nothing ran   (0.14s)
            685e773 (env)   7c16fbe base   AssertionError 0 != 1    (0.73s)
            685e773 (env)   HEAD           AssertionError 0 != 1    (0.77s)

        Read the last two rows before reusing this as evidence. `0 != 1`
        appears against BOTH binaries, so it is produced by the clock channel
        being dead, not by the binary lacking the fix — swapping the binary
        changes nothing. This test is not, and never was, a discriminator
        between the fixed and unfixed CLI, and the old recipe implied it was.

        Row 2 is the trap that made #55 blocking: `--now` is rejected by the
        base binary's argparse, so `slot` is None and the test dies at the
        FIRST command in 0.14s, 0.35s before the behaviour under test. That
        `TypeError` is what ANY newly-added argv flag produces. It reads the
        same whether or not the defect exists.

        What this test does pin — and it is worth pinning — is that the
        virtual clock genuinely reaches the binary end to end. Every way that
        channel can break surfaces here as a loud failure: `0 != 1` if the
        clock is silently ignored, `TypeError` if argv is rejected. Row 1 is
        the load-bearing row: it is OK only when injection actually works.

        Reproduce row 1, the live one, from this directory with no setup:

            python3 -m unittest \
                test_recovery.Recovery.test_rt26_explicit_reap_is_immune_to_setup_scheduling

        Reproduce row 3, the historical capture. It needs the harness of the
        day as well as the binary of the day; the binary alone will not do it:

            cd /srv/project
            git show 7c16fbe:bin/nightshift-ctl > /tmp/ctl-base
            chmod +x /tmp/ctl-base
            rm -rf /tmp/wa008-685e773 && mkdir -p /tmp/wa008-685e773
            git archive 685e773 tests | tar -x -C /tmp/wa008-685e773
            cd /tmp/wa008-685e773/tests && \
            NIGHTSHIFT_TEST_CTL=/tmp/ctl-base python3 -m unittest \
                test_recovery.Recovery.test_rt26_explicit_reap_is_immune_to_setup_scheduling
        """
        c = Ctl(clock=VIRTUAL_T0)
        _, slot = c.acquire_normal(task="t1", title="t1", ttl="0.2")
        c.seed_admission(task="t1", slot_id=slot["slot_id"], title="t1")
        c.run("run", "start", "--agent", "eng_claude", "--title", "t1",
              "--slot", slot["slot_id"], "--task", "t1")
        # The load-induced setup gap, made deterministic. On the virtual clock
        # this wall-clock time does not exist, so it must change nothing.
        time.sleep(0.35)
        c.run("claim", "acquire", "--task", "t1", "--claimant", "lead",
              "--agent", "eng_claude", "--title", "t1",
              "--ttl", "0.2")
        c.advance(0.3)  # now the TTLs lapse, in virtual time
        code, reaped = c.run("reap")
        self.assertEqual(code, 0)
        self.assertEqual(
            reaped["slots_reaped"], 1,
            "the EXPLICIT reap must collect the slot regardless of how long "
            "the setup took in wall-clock terms; getting 0 here means lazy "
            "expiry in an earlier subprocess won the race (issue #32)")
        self.assertEqual(reaped["claims_expired"], 1)

    def test_restart_reconstructs_state_no_duplicates(self):
        c = Ctl()
        # wave in flight: slot + claim + task rows
        _, slot = c.acquire_normal(
            task="t9", agent="eng_codex", title="add-export",
        )
        c.seed_admission(
            task="t9", slot_id=slot["slot_id"], agent="eng_codex",
            title="add-export",
        )
        c.run("claim", "acquire", "--task", "t9", "--claimant", "lead-alpha",
              "--agent", "eng_codex", "--title", "add-export", "--ttl", "3600")
        c.run("task", "upsert", "--id", "t9", "--crew", "crew_a", "--agent",
              "eng_codex", "--title", "add-export", "--state", "building",
              "--base-sha", "abc123")
        # "restart": every later command is a fresh process reading the same
        # db — the heartbeat's first recovery move must be refused because the
        # claim is still live, so no duplicate worker can be hired.
        code, body = c.run("claim", "acquire", "--task", "t9",
                           "--claimant", "heartbeat-after-reboot",
                           "--agent", "eng_codex", "--title", "add-export")
        self.assertEqual(code, 3)
        self.assertEqual(body["reuse"], {"agent": "eng_codex",
                                         "title": "add-export"})
        _, dump = c.run("dump")
        self.assertEqual(len(dump["slots"]), 1)
        self.assertEqual(len(dump["claims"]), 1)
        self.assertEqual(dump["tasks"][0]["base_sha"], "abc123")
        self.assertEqual(dump["tasks"][0]["state"], "building")


if __name__ == "__main__":
    unittest.main()
