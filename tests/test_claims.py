"""Audit test 1: two (or eight) heartbeats race to recover one task —
exactly one wins the claim; the losers are told to stand down and are
pointed at the same (agent, title) to reuse."""

import time
import unittest

from ctlutil import Ctl, run_parallel, VIRTUAL_T0


def admitted_claim_task(c, task):
    title = f"claim-{task}"
    code, slot = c.acquire_normal(task=task, title=title)
    if code != 0:
        raise AssertionError(slot)
    c.seed_admission(task=task, slot_id=slot["slot_id"], title=title)
    return title


class ClaimRaces(unittest.TestCase):
    def test_concurrent_claims_one_winner(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-race")
        cmds = [
            c.cmdline(
                "claim", "acquire", "--task", "t-race",
                "--claimant", f"heartbeat-{i}",
                "--agent", "eng_claude", "--title", title,
            )
            for i in range(8)
        ]
        results = run_parallel(cmds)
        granted = [r for code, r in results if code == 0 and r["granted"]]
        denied = [r for code, r in results if code == 3]
        self.assertEqual(len(granted), 1)
        self.assertEqual(len(denied), 7)
        for d in denied:
            self.assertEqual(d["reuse"], {"agent": "eng_claude", "title": title})

    def test_release_then_reclaim(self):
        c = Ctl()
        title = admitted_claim_task(c, "t1")
        exact = ("--agent", "eng_claude", "--title", title)
        code, _ = c.run("claim", "acquire", "--task", "t1", "--claimant", "a", *exact)
        self.assertEqual(code, 0)
        code, _ = c.run("claim", "acquire", "--task", "t1", "--claimant", "b", *exact)
        self.assertEqual(code, 3)
        code, _ = c.run("claim", "release", "--task", "t1", "--claimant", "a")
        self.assertEqual(code, 0)
        code, _ = c.run("claim", "acquire", "--task", "t1", "--claimant", "b", *exact)
        self.assertEqual(code, 0)

    def test_ttl_expiry_frees_claim(self):
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t2")
        exact = ("--agent", "eng_claude", "--title", title)
        code, _ = c.run("claim", "acquire", "--task", "t2", "--claimant", "a",
                        *exact, "--ttl", "600")
        self.assertEqual(code, 0)
        code, _ = c.run("claim", "acquire", "--task", "t2", "--claimant", "b", *exact)
        self.assertEqual(code, 3)
        # the claimant dies: renew DOWN, then expiry frees the task
        c.run("claim", "renew", "--task", "t2", "--claimant", "a", "--ttl", "0.15")
        c.advance(0.3)  # virtual time (ADR-0005 D4); was time.sleep(0.3)
        code, body = c.run("claim", "acquire", "--task", "t2", "--claimant", "b", *exact)
        self.assertEqual(code, 0, body)

    def test_renew_extends(self):
        c = Ctl()
        title = admitted_claim_task(c, "t3")
        exact = ("--agent", "eng_claude", "--title", title)
        c.run("claim", "acquire", "--task", "t3", "--claimant", "a",
              *exact, "--ttl", "600")
        code, _ = c.run("claim", "renew", "--task", "t3", "--claimant", "a", "--ttl", "60")
        self.assertEqual(code, 0)
        code, _ = c.run("claim", "acquire", "--task", "t3", "--claimant", "b", *exact)
        self.assertEqual(code, 3)  # renewed claim still holds


class ClaimRenewRejectsGarbageTTL(unittest.TestCase):
    """Issue #77 / ADR-0007: a renew with a non-positive ttl must be refused
    loudly (exit 2, ok:false) and must leave the claim's expiry untouched --
    never accepted with ok:true while quietly back-dating expires_at into the
    past (which is what silently releases the claim it was told to renew).

    R-26's own published formula
    (`ttl=$(python3 -c 'import time;n=time.time();print(int(n-(n%3600)+3600-n-180))')`)
    goes negative in the last 180s of every hour -- these are that shape.
    """

    def test_renew_rejects_negative_ttl(self):
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-neg")
        code, granted = c.run("claim", "acquire", "--task", "t-neg",
                               "--claimant", "a", "--agent", "eng_claude",
                               "--title", title, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        orig_expiry = granted["expires_at"]

        code, body = c.run("claim", "renew", "--task", "t-neg", "--claimant", "a", "--ttl", "-5")
        self.assertEqual(code, 2, body)          # usage error, never 0, never 3
        self.assertFalse(body["ok"])
        self.assertIn("positive", body["reason"])

        # the claim must be UNTOUCHED, not renewed-into-the-past: this is
        # the part a bare "exit code changed" assertion would miss (WA-009 --
        # the counterfactual must fail because of the variable under test).
        code, show = c.run("claim", "show", "--task", "t-neg")
        self.assertEqual(code, 0)
        self.assertEqual(show["holder"]["expires_at"], orig_expiry)

    def test_renew_rejects_zero_ttl(self):
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-zero")
        code, granted = c.run("claim", "acquire", "--task", "t-zero",
                               "--claimant", "a", "--agent", "eng_claude",
                               "--title", title, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        orig_expiry = granted["expires_at"]

        code, body = c.run("claim", "renew", "--task", "t-zero", "--claimant", "a", "--ttl", "0")
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])

        code, show = c.run("claim", "show", "--task", "t-zero")
        self.assertEqual(show["holder"]["expires_at"], orig_expiry)

    def test_renew_rejects_inf_ttl(self):
        # `inf > 0` is True in Python, so the naive `not (ttl > 0)` guard
        # PASSES an infinite ttl straight through: expires_at becomes inf,
        # the claim never expires, `reap` can never free it, and every
        # future `claim acquire` on this task_id returns exit 3 forever --
        # a permanently wedged task. Proves the guard also checks
        # math.isfinite(ttl), not just `> 0`.
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-inf")
        code, granted = c.run("claim", "acquire", "--task", "t-inf",
                               "--claimant", "a", "--agent", "eng_claude",
                               "--title", title, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        orig_expiry = granted["expires_at"]

        code, body = c.run("claim", "renew", "--task", "t-inf", "--claimant", "a", "--ttl", "inf")
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])

        code, show = c.run("claim", "show", "--task", "t-inf")
        self.assertEqual(code, 0)
        self.assertEqual(show["holder"]["expires_at"], orig_expiry)

    def test_renew_rejects_neg_inf_ttl(self):
        # -inf > 0 is already False under the old guard, so this case was
        # already refused before this fix -- included as a control/sibling
        # of test_renew_rejects_inf_ttl, not as new coverage of the inf
        # defect itself.
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-neg-inf")
        code, granted = c.run("claim", "acquire", "--task", "t-neg-inf",
                               "--claimant", "a", "--agent", "eng_claude",
                               "--title", title, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        orig_expiry = granted["expires_at"]

        code, body = c.run("claim", "renew", "--task", "t-neg-inf", "--claimant", "a", "--ttl=-inf")
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])

        code, show = c.run("claim", "show", "--task", "t-neg-inf")
        self.assertEqual(code, 0)
        self.assertEqual(show["holder"]["expires_at"], orig_expiry)

    def test_renew_rejects_nan_ttl(self):
        # float("nan") parses cleanly through argparse's type=float -- the
        # simple `if ttl < 0` a first pass might write does NOT catch this
        # (nan is neither < 0 nor > 0), and now() + nan corrupts expires_at
        # just as badly as a negative ttl does. Proves the guard is
        # `not (ttl > 0)`, not `ttl < 0`.
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-nan")
        code, granted = c.run("claim", "acquire", "--task", "t-nan",
                               "--claimant", "a", "--agent", "eng_claude",
                               "--title", title, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        orig_expiry = granted["expires_at"]

        code, body = c.run("claim", "renew", "--task", "t-nan", "--claimant", "a", "--ttl", "nan")
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])

        code, show = c.run("claim", "show", "--task", "t-nan")
        self.assertEqual(show["holder"]["expires_at"], orig_expiry)



class ClaimReleaseRenewRequireRealClaimant(unittest.TestCase):
    """Issue #508 D07 AC1-AC4, ADR-0068: `claim release`/`claim renew` must
    prove the caller is the actual claimant before mutating anything -- a
    non-holder is refused (exit 3, real claimant named, row untouched); the
    real holder still succeeds; a missing claim gets a reason distinguishable
    from a wrong-claimant reason (two different strings, not one reused)."""

    def test_release_wrong_claimant_denied_row_untouched(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-ac1")
        exact = ("--agent", "eng_claude", "--title", title)
        code, granted = c.run("claim", "acquire", "--task", "t-ac1",
                               "--claimant", "a", *exact)
        self.assertEqual(code, 0, granted)
        code, body = c.run("claim", "release", "--task", "t-ac1",
                            "--claimant", "mallory")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertIn("a", body["reason"])  # names the REAL claimant
        code, show = c.run("claim", "show", "--task", "t-ac1")
        self.assertEqual(code, 0)
        self.assertEqual(show["holder"]["claimant"], "a")
        self.assertEqual(show["holder"]["state"], "active")

    def test_release_real_claimant_succeeds(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-ac2")
        exact = ("--agent", "eng_claude", "--title", title)
        c.run("claim", "acquire", "--task", "t-ac2", "--claimant", "a", *exact)
        code, body = c.run("claim", "release", "--task", "t-ac2",
                            "--claimant", "a")
        self.assertEqual(code, 0, body)

    def test_renew_wrong_claimant_denied_expiry_untouched(self):
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-ac3")
        exact = ("--agent", "eng_claude", "--title", title)
        code, granted = c.run("claim", "acquire", "--task", "t-ac3",
                               "--claimant", "a", *exact, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        code, body = c.run("claim", "renew", "--task", "t-ac3",
                            "--claimant", "mallory", "--ttl", "60")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertIn("a", body["reason"])
        code, show = c.run("claim", "show", "--task", "t-ac3")
        self.assertEqual(show["holder"]["expires_at"], granted["expires_at"])

    def test_renew_real_claimant_succeeds(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-ac3b")
        exact = ("--agent", "eng_claude", "--title", title)
        c.run("claim", "acquire", "--task", "t-ac3b", "--claimant", "a",
              *exact, "--ttl", "600")
        code, body = c.run("claim", "renew", "--task", "t-ac3b",
                            "--claimant", "a", "--ttl", "60")
        self.assertEqual(code, 0, body)

    def test_renew_bad_ttl_and_wrong_claimant_neither_applies(self):
        """AC3: a request with BOTH a bad ttl and a wrong claimant must not
        renew -- the pre-existing ttl guard fires first (exit 2), independent
        of the new claimant check, and the claim is untouched either way."""
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-ac3c")
        exact = ("--agent", "eng_claude", "--title", title)
        code, granted = c.run("claim", "acquire", "--task", "t-ac3c",
                               "--claimant", "a", *exact, "--ttl", "600")
        self.assertEqual(code, 0, granted)
        code, body = c.run("claim", "renew", "--task", "t-ac3c",
                            "--claimant", "mallory", "--ttl", "-5")
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])
        code, show = c.run("claim", "show", "--task", "t-ac3c")
        self.assertEqual(show["holder"]["expires_at"], granted["expires_at"])
        self.assertEqual(show["holder"]["claimant"], "a")

    def test_release_no_active_claim_reason_distinct_from_wrong_claimant(self):
        c = Ctl()
        code, body = c.run("claim", "release", "--task", "t-ac4-none",
                            "--claimant", "a")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "no active claim on that task")

    def test_renew_no_active_claim_reason_distinct_from_wrong_claimant(self):
        c = Ctl()
        code, body = c.run("claim", "renew", "--task", "t-ac4-none2",
                            "--claimant", "a", "--ttl", "60")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "no active claim on that task")
        # distinct from the wrong-claimant string used elsewhere in this class
        self.assertNotIn("is claimed by", body["reason"])


class RT18DispatchDeniedForForeignActiveClaim(unittest.TestCase):
    """RT-18 (DoD condition 7, issue #508): dispatch onto a task whose active
    claim belongs to another claimant is refused. `claim acquire` IS the real
    dispatch/slot-acquire surface for this property (not merely the raw
    `worker_slots` NEW reservation, which carries no claimant at all) --
    `cmd_slot_acquire`'s implementer-continuation branch additionally
    requires the caller to already hold the exact active claim's id/
    generation/run_id (bin/nightshift-ctl `_derive_normal_phase`), values
    only ever handed back to the real claim acquirer, so a foreign claimant
    cannot reach that branch either. This test proves the enforcement point
    the audit already found sound (docs/audit/CONCURRENCY.md), now with a
    real, bound RT id -- no new production code, real regression coverage."""

    def test_second_claimant_denied_first_claim_untouched(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-rt18")
        exact = ("--agent", "eng_claude", "--title", title)
        code, granted = c.run("claim", "acquire", "--task", "t-rt18",
                               "--claimant", "a", *exact)
        self.assertEqual(code, 0, granted)
        code, body = c.run("claim", "acquire", "--task", "t-rt18",
                            "--claimant", "b", *exact)
        self.assertEqual(code, 3, body)
        self.assertFalse(body["granted"])
        self.assertEqual(body["reuse"], {"agent": "eng_claude", "title": title})
        code, show = c.run("claim", "show", "--task", "t-rt18")
        self.assertEqual(code, 0)
        self.assertEqual(show["holder"]["claimant"], "a")
        self.assertEqual(show["holder"]["generation"], granted["generation"])
        self.assertEqual(show["holder"]["state"], "active")


class RT34RecoveryTransitionRequiredForSequentialReexecution(unittest.TestCase):
    """RT-34 (DoD condition 7, issue #508): sequential re-execution without a
    recorded recovery transition is refused and the generation does not
    move; with one of the three recognised recovery transitions -- an
    authorized release (AC1/AC2 above), TTL expiry (ADR-0002 reaping), or
    the ADR-0044 sec3b program-authority claim-first path (unaffected by this
    change) -- it is permitted and the new generation is recorded and
    visible via `claim show`.

    NOT bound to RT-20: `staff`'s dedicated, authoritative ruling on issue
    #518 (task `518-rt20-binding-decision`, `.omni/ledger.md` 2026-09-02)
    concurrently and independently narrowed RT-20 to `test_fencing.py`'s
    `FencingMigrationTest` (id unchanged, its own baseline re-capture) and
    minted a separate new id, RT-31, for `MigrationConcurrencyTest` -- staff
    did NOT retire either binding, contradicting this class's own earlier
    docstring (see ADR-0068's correction note). RT-34 is a fresh id, chosen
    to avoid colliding with RT-24..30 (staff: "real captures already occupy
    24-30, unfolded") and RT-31/32/33 (staff's own item and this PR's other
    two new ids), not a replacement for RT-20."""

    def test_refused_without_any_recovery_transition(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-rt20a")
        exact = ("--agent", "eng_claude", "--title", title)
        code, g1 = c.run("claim", "acquire", "--task", "t-rt20a",
                          "--claimant", "a", *exact)
        self.assertEqual(code, 0, g1)
        code, body = c.run("claim", "acquire", "--task", "t-rt20a",
                            "--claimant", "b", *exact)
        self.assertEqual(code, 3, body)
        code, show = c.run("claim", "show", "--task", "t-rt20a")
        self.assertEqual(show["holder"]["generation"], g1["generation"])

    def test_permitted_after_authorized_release_generation_recorded(self):
        c = Ctl()
        title = admitted_claim_task(c, "t-rt20b")
        exact = ("--agent", "eng_claude", "--title", title)
        code, g1 = c.run("claim", "acquire", "--task", "t-rt20b",
                          "--claimant", "a", *exact)
        self.assertEqual(code, 0, g1)
        code, rel = c.run("claim", "release", "--task", "t-rt20b",
                           "--claimant", "a")
        self.assertEqual(code, 0, rel)
        code, g2 = c.run("claim", "acquire", "--task", "t-rt20b",
                          "--claimant", "b", *exact)
        self.assertEqual(code, 0, g2)
        self.assertEqual(g2["generation"], g1["generation"] + 1)
        code, show = c.run("claim", "show", "--task", "t-rt20b")
        self.assertEqual(show["holder"]["generation"], g2["generation"])
        self.assertEqual(show["holder"]["claimant"], "b")

    def test_permitted_after_ttl_expiry_generation_recorded(self):
        c = Ctl(clock=VIRTUAL_T0)
        title = admitted_claim_task(c, "t-rt20c")
        exact = ("--agent", "eng_claude", "--title", title)
        code, g1 = c.run("claim", "acquire", "--task", "t-rt20c",
                          "--claimant", "a", *exact, "--ttl", "0.15")
        self.assertEqual(code, 0, g1)
        c.advance(0.3)
        code, g2 = c.run("claim", "acquire", "--task", "t-rt20c",
                          "--claimant", "b", *exact)
        self.assertEqual(code, 0, g2)
        self.assertEqual(g2["generation"], g1["generation"] + 1)


if __name__ == "__main__":
    unittest.main()
