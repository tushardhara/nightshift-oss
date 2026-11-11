"""Issues #88 / #86, ADR-0008: the invalid-ttl defect FAMILY.

PR #85 (merged 8429668) guarded exactly one site, `claim renew` (covered by
`test_claims.py`). `staff` then reproduced the identical unguarded `t + ttl`
shape at five more verbs. This file covers the remaining five.

Two distinct corruptions, one root cause -- `ttl` is arithmetic input that was
never checked:

  RENEWAL verbs (`slot heartbeat`, `slot renew-active`)
      `expires_at = t + ttl` is written unconditionally, so a NEGATIVE ttl
      back-dates it and the call meant to EXTEND the row instead expires it at
      the instant it is granted -- while printing `ok: true`. `slot heartbeat`
      is the ADR-0002 path every worker calls every turn, and `renew-active`
      does it to every active slot in a single call, so this is confirmed
      defect 6 ("the worker budget is not global") re-entering by the renewal
      path.

  ACQUIRE verbs (`slot acquire`, `lease acquire`, `claim acquire`)
      `t + ttl if ttl > 0 else None` -- NULL means never-expires, so a
      non-positive ttl silently produced an IMMORTAL row that `reap` can never
      free, wedging that id against every future acquire, forever.

`inf` gets its own case at every site: `inf > 0` is True, so a `> 0` test alone
lets it through and `expires_at` becomes infinite -- the never-expire wedge by
another route. This is the same subtlety PR #85 found for `claim renew`.

EVIDENCE (WA-008). Every test here is run against the PRE-FIX binary via
`NIGHTSHIFT_TEST_CTL` and shown to FAIL before being shown to pass against the
fixed one. The `Controls` class at the bottom is the WA-009 control: those
tests pass against BOTH binaries, which is what makes the failures above
attributable to the guard rather than to a broken harness.
"""

import math
import unittest

from ctlutil import Ctl, VIRTUAL_T0

# The invalid values every ttl-taking verb must refuse. `0` and `-5` are the
# back-date / never-expire shapes; `inf` is the one that survives a naive
# `> 0` check; `nan` is the one that survives *every* comparison, which is why
# the guard is written `not (ttl > 0)` and not `ttl <= 0`.
BAD_TTLS = ["-5", "0", "inf", "-inf", "nan"]

# Always spell the flag `--ttl=<v>`, never `--ttl <v>`, when the value may be
# negative and non-numeric-looking. argparse only treats a bare `-5` as a value
# because it matches its negative-number pattern; `-inf` does NOT match, so
# `--ttl -inf` is parsed as an unknown OPTION and argparse exits 2 on its own,
# printing no JSON. That is a false pass: the assertion on the exit code would
# be satisfied by argparse while the guard under test was never reached. The
# `=` form forces the value through to the guard.
def ttl_flag(v):
    return "--ttl=" + v


HOLDER = "crew_alpha/eng_claude"


def _acquire_slot(c, holder=HOLDER, ttl="600"):
    if holder.startswith("crew_beta/"):
        crew, agent = "crew_beta", "beta_eng_codex"
    else:
        crew, agent = "crew_alpha", "eng_claude"
    code, r = c.acquire_normal(crew=crew, agent=agent, ttl=ttl)
    assert code == 0, r
    return r["slot_id"]


def _admit_task_for_claim(c, task, agent="eng_claude"):
    title = f"fixture-{task}"
    code, slot = c.acquire_normal(task=task, agent=agent, title=title)
    assert code == 0, slot
    c.seed_admission(task=task, slot_id=slot["slot_id"],
                     agent=agent, title=title)
    c.run("slot", "release", "--slot", str(slot["slot_id"]),
                     "--holder", f"crew_alpha/{agent}")
    return title


def _active_slot_ids(c):
    code, r = c.run("slot", "list")
    assert code == 0, r
    return [row["id"] for row in r["active"]]


class SlotHeartbeatRefusesInvalidTtl(unittest.TestCase):
    """#88 -- the every-worker-every-turn path."""

    def test_refuses_each_invalid_ttl(self):
        for bad in BAD_TTLS:
            with self.subTest(ttl=bad):
                c = Ctl(clock=VIRTUAL_T0)
                slot = _acquire_slot(c)
                code, body = c.run("slot", "heartbeat", "--slot", str(slot),
                                   "--holder", HOLDER, ttl_flag(bad))
                self.assertEqual(code, 2, body)
                self.assertFalse(body["ok"])
                self.assertIn("--ttl", body["reason"])
                self.assertEqual(body["verb"], "slot heartbeat")

    def test_a_refused_heartbeat_does_not_release_the_slot(self):
        """The actual harm, not just the exit code.

        Pre-fix this returned `ok: true` and the slot vanished from
        `slot list` -- the worker kept running while its budget entry was
        handed to someone else.
        """
        c = Ctl(clock=VIRTUAL_T0)
        slot = _acquire_slot(c)
        code, body = c.run("slot", "heartbeat", "--slot", str(slot),
                           "--holder", HOLDER, "--ttl", "-5")
        self.assertEqual(code, 2, body)
        self.assertIn(slot, _active_slot_ids(c),
                      "the slot was released by the call that was told to renew it")

    def test_the_refusal_precedes_any_write(self):
        """A refused heartbeat must not have moved expires_at at all."""
        c = Ctl(clock=VIRTUAL_T0)
        slot = _acquire_slot(c, ttl="600")
        code, before = c.run("slot", "list")
        exp_before = [r["expires_at"] for r in before["active"] if r["id"] == slot][0]
        c.run("slot", "heartbeat", "--slot", str(slot),
              "--holder", HOLDER, "--ttl", "-5")
        code, after = c.run("slot", "list")
        exp_after = [r["expires_at"] for r in after["active"] if r["id"] == slot][0]
        self.assertEqual(exp_before, exp_after)


class SlotRenewActiveRefusesInvalidTtl(unittest.TestCase):
    """#88 -- one call renews EVERY active slot, so one bad ttl empties the org."""

    def test_refuses_each_invalid_ttl(self):
        for bad in BAD_TTLS:
            with self.subTest(ttl=bad):
                c = Ctl(clock=VIRTUAL_T0)
                _acquire_slot(c)
                code, body = c.run("slot", "renew-active", ttl_flag(bad))
                self.assertEqual(code, 2, body)
                self.assertFalse(body["ok"])
                self.assertEqual(body["verb"], "slot renew-active")

    def test_a_refused_renew_active_does_not_release_every_slot(self):
        c = Ctl(clock=VIRTUAL_T0)
        a = _acquire_slot(c, holder="crew_alpha/eng_claude")
        b = _acquire_slot(c, holder="crew_beta/eng_codex")
        code, body = c.run("slot", "renew-active", "--ttl", "-5")
        self.assertEqual(code, 2, body)
        still = _active_slot_ids(c)
        self.assertIn(a, still)
        self.assertIn(b, still, "one bad watchdog ttl released the whole org's slots")


class SlotAcquireRefusesInvalidTtl(unittest.TestCase):
    """#86 family -- non-positive ttl used to mean never-expires."""

    def test_refuses_each_invalid_ttl(self):
        for bad in BAD_TTLS:
            with self.subTest(ttl=bad):
                c = Ctl(clock=VIRTUAL_T0)
                code, body = c.run("slot", "acquire", "--holder", HOLDER,
                                   "--crew", "crew_alpha", ttl_flag(bad))
                self.assertEqual(code, 2, body)
                self.assertEqual(body["verb"], "slot acquire")

    def test_no_immortal_slot_is_created(self):
        """Pre-fix `--ttl 0` produced expires_at NULL: a slot reap can never
        free, permanently consuming one of the four global worker seats."""
        c = Ctl(clock=VIRTUAL_T0)
        code, body = c.run("slot", "acquire", "--holder", HOLDER,
                           "--crew", "crew_alpha", "--ttl", "0")
        self.assertEqual(code, 2, body)
        self.assertEqual(_active_slot_ids(c), [],
                         "a never-expiring slot was created and now cannot be reaped")

    def test_every_granted_slot_has_a_finite_expiry(self):
        """The invariant the guard buys: no active slot is ever immortal."""
        c = Ctl(clock=VIRTUAL_T0)
        _acquire_slot(c, ttl="600")
        code, r = c.run("slot", "list")
        for row in r["active"]:
            self.assertIsNotNone(row["expires_at"])
            self.assertTrue(math.isfinite(float(row["expires_at"])))


class ClaimAcquireRefusesInvalidTtl(unittest.TestCase):
    """#86 -- the issue's named site."""

    def test_refuses_each_invalid_ttl(self):
        for bad in BAD_TTLS:
            with self.subTest(ttl=bad):
                c = Ctl(clock=VIRTUAL_T0)
                code, body = c.run("claim", "acquire", "--task", "t-bad",
                                   "--claimant", "a", ttl_flag(bad))
                self.assertEqual(code, 2, body)
                self.assertEqual(body["verb"], "claim acquire")

    def test_no_immortal_claim_wedges_the_task_forever(self):
        """The wedge, demonstrated end to end.

        Pre-fix: `--ttl 0` claimed the task with expires_at NULL. Time passes,
        `reap` runs, and the claim is still there -- so every future claimant
        of that task id gets exit 3 for the rest of the run.
        """
        c = Ctl(clock=VIRTUAL_T0)
        code, body = c.run("claim", "acquire", "--task", "t-wedge",
                           "--claimant", "dead-worker", "--ttl", "0")
        self.assertEqual(code, 2, body)
        c.advance(86_400)
        c.run("reap")
        title = _admit_task_for_claim(c, "t-wedge")
        code, body = c.run("claim", "acquire", "--task", "t-wedge",
                           "--claimant", "the-next-worker",
                           "--agent", "eng_claude", "--title", title,
                           "--ttl", "600")
        self.assertEqual(code, 0, "the task is permanently wedged: %r" % (body,))
        self.assertTrue(body["granted"])


class LeaseAcquireRefusesInvalidTtl(unittest.TestCase):
    """#86 family -- and the one site with a legitimate never-expires mode."""

    def test_refuses_each_invalid_ttl(self):
        for bad in BAD_TTLS:
            with self.subTest(ttl=bad):
                c = Ctl(clock=VIRTUAL_T0)
                code, body = c.run("lease", "acquire", "--resource", "docs/x.md",
                                   "--holder", "crew_alpha", ttl_flag(bad))
                self.assertEqual(code, 2, body)
                self.assertEqual(body["verb"], "lease acquire")

    def test_no_immortal_lease_wedges_the_resource_forever(self):
        c = Ctl(clock=VIRTUAL_T0)
        code, body = c.run("lease", "acquire", "--resource", "docs/x.md",
                           "--holder", "crew_alpha", "--ttl", "0")
        self.assertEqual(code, 2, body)
        c.advance(86_400)
        c.run("reap")
        code, body = c.run("lease", "acquire", "--resource", "docs/x.md",
                           "--holder", "crew_beta", "--ttl", "600")
        self.assertEqual(code, 0, "the resource is permanently leased: %r" % (body,))


class Controls(unittest.TestCase):
    """WA-009 CONTROLS.

    Every test in this class passes against BOTH the pre-fix and the fixed
    binary. They are named here as the control beside the counterfactual: they
    are what shows the failures above come from the guard and not from a
    harness that fails everything. They are also the regression bound on the
    guard itself -- a guard that refused valid input would break these.
    """

    def test_control_valid_ttl_still_works_at_all_five_sites(self):
        c = Ctl(clock=VIRTUAL_T0)
        slot = _acquire_slot(c, ttl="600")                       # slot acquire
        code, r = c.run("slot", "heartbeat", "--slot", str(slot),
                        "--holder", HOLDER, "--ttl", "600")      # slot heartbeat
        self.assertEqual(code, 0, r)
        self.assertTrue(r["ok"])
        code, r = c.run("slot", "renew-active", "--ttl", "600")  # slot renew-active
        self.assertEqual(code, 0, r)
        self.assertTrue(r["ok"])
        title = _admit_task_for_claim(c, "t-ok")
        code, r = c.run("claim", "acquire", "--task", "t-ok",
                        "--claimant", "a", "--agent", "eng_claude",
                        "--title", title, "--ttl", "600")       # claim acquire
        self.assertEqual(code, 0, r)
        code, r = c.run("lease", "acquire", "--resource", "docs/ok.md",
                        "--holder", "crew_alpha", "--ttl", "600")  # lease acquire
        self.assertEqual(code, 0, r)

    def test_control_omitted_ttl_still_uses_the_capacity_default(self):
        """The guard runs AFTER default resolution, so omitting --ttl must
        still work at every site -- that is the overwhelmingly common call."""
        c = Ctl(clock=VIRTUAL_T0)
        code, r = c.acquire_normal()
        self.assertEqual(code, 0, r)
        slot = r["slot_id"]
        code, r = c.run("slot", "heartbeat", "--slot", str(slot), "--holder", HOLDER)
        self.assertEqual(code, 0, r)
        code, r = c.run("slot", "renew-active")
        self.assertEqual(code, 0, r)
        title = _admit_task_for_claim(c, "t-def")
        code, r = c.run("claim", "acquire", "--task", "t-def",
                        "--claimant", "a", "--agent", "eng_claude",
                        "--title", title)
        self.assertEqual(code, 0, r)
        code, r = c.run("lease", "acquire", "--resource", "docs/def.md",
                        "--holder", "crew_alpha")
        self.assertEqual(code, 0, r)

    def test_control_serialize_lease_is_still_legitimately_never_expiring(self):
        """The one carve-out. A `serialize` lease is held until the PR merges
        or is dropped, so expires_at NULL is correct there and the guard must
        not touch it (ADR-0008, "The serialize carve-out")."""
        c = Ctl(clock=VIRTUAL_T0)
        code, r = c.run("lease", "acquire", "--resource", "db/migrations/**",
                        "--holder", "crew_alpha", "--mode", "serialize")
        self.assertEqual(code, 0, r)
        self.assertIsNone(r["expires_at"])
        c.advance(86_400)
        c.run("reap")
        code, r = c.run("lease", "acquire", "--resource", "db/migrations/**",
                        "--holder", "crew_beta", "--ttl", "600")
        self.assertEqual(code, 3, "a serialize lease must survive TTL recovery")

    def test_control_a_slot_still_expires_on_its_own_ttl(self):
        """TTL recovery still works: the guard rejects bad input, it does not
        make slots immortal."""
        c = Ctl(clock=VIRTUAL_T0)
        slot = _acquire_slot(c, ttl="600")
        self.assertIn(slot, _active_slot_ids(c))
        c.advance(601)
        self.assertNotIn(slot, _active_slot_ids(c))


if __name__ == "__main__":
    unittest.main()
