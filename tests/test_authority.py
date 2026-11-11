"""ADR-0044 §1/§2/§6 — the Program-authority fence primitive.

At most one Program-role session may hold the fence at a time. This suite
covers the primitive itself (`nightshift-ctl authority ...`) and the
nightshift-ctl-side half of AC-7 (the `standdown` status). The two
CONSUMERS of this primitive — the ROOT policy in program/config.yaml
(Decision §3) and the `--claimant program` / `--oncall` token checks inside
`claim acquire`/`slot acquire` (Decision §3b) — are a separate, dependent
task (`174-implement-fence`) and are not covered here; neither is AC-8
(proving that ROOT policy is actually loaded), which belongs to that task.

Numbering matches the DoR spec (.omni/reports/2026-08-29-0911Z-174-dor.md,
branch pm/174-dor) and ADR-0044 itself.
"""

import sqlite3
import unittest

from ctlutil import Ctl, VIRTUAL_T0, run_parallel


class AuthorityAcquireRace(unittest.TestCase):
    def test_20_way_race_exactly_one_winner(self):
        """AC-1."""
        c = Ctl()
        cmds = [
            c.cmdline("authority", "acquire", "--holder-label", f"program-{i}")
            for i in range(20)
        ]
        results = run_parallel(cmds)
        granted = [r for code, r in results if code == 0]
        denied = [r for code, r in results if code == 3]
        self.assertEqual(len(granted), 1)
        self.assertEqual(len(denied), 19)
        self.assertTrue(granted[0]["granted"])
        for r in denied:
            self.assertFalse(r["granted"])

    def test_denied_acquire_is_zero_mutation(self):
        """AC-2: a denied acquire performs zero control-plane writes."""
        c = Ctl()
        code, first = c.run("authority", "acquire", "--holder-label", "program-a")
        self.assertEqual(code, 0)

        def snapshot():
            with sqlite3.connect(c.db) as cx:
                cx.row_factory = sqlite3.Row
                return [dict(r) for r in cx.execute(
                    "SELECT * FROM program_authority ORDER BY id").fetchall()]

        before = snapshot()
        code, denial = c.run("authority", "acquire", "--holder-label", "program-b")
        self.assertEqual(code, 3)
        self.assertFalse(denial["granted"])
        after = snapshot()
        self.assertEqual(before, after)


class AuthorityRenewRelease(unittest.TestCase):
    def test_renew_before_expiry_keeps_continuous_validity(self):
        """AC-3: no window where the fence is expired but the holder is
        still acting — a renew before TTL lapses extends expires_at, and
        `show` reports the row active straight through the renewal."""
        c = Ctl(clock=VIRTUAL_T0)
        code, granted = c.run("authority", "acquire", "--holder-label", "program-a",
                              "--ttl", "100")
        self.assertEqual(code, 0)
        gen, token = granted["generation"], granted["token"]

        c.advance(60)  # inside the original TTL window
        code, show = c.run("authority", "show")
        self.assertTrue(show["active"])

        code, renewed = c.run("authority", "renew", "--generation", str(gen),
                              "--token-stdin", "--ttl", "100", stdin=token)
        self.assertEqual(code, 0, renewed)
        self.assertGreater(renewed["expires_at"], granted["expires_at"])

        c.advance(60)  # past the ORIGINAL ttl (60+60=120 > 100), inside the renewed one
        code, show2 = c.run("authority", "show")
        self.assertTrue(show2["active"])
        self.assertEqual(show2["generation"], gen)

    def test_wrong_token_or_generation_denied_on_renew_release_permit(self):
        """AC-5: old/wrong generation is denied on renew, release AND
        permit-dispatch, not just at the next acquire."""
        c = Ctl(clock=VIRTUAL_T0)
        code, g1 = c.run("authority", "acquire", "--holder-label", "program-a",
                         "--ttl", "1000")
        self.assertEqual(code, 0)
        gen1, token1 = g1["generation"], g1["token"]

        # wrong token, current generation
        code, r = c.run("authority", "renew", "--generation", str(gen1),
                        "--token-stdin", stdin="not-the-real-token")
        self.assertEqual(code, 3)
        code, r = c.run("authority", "release", "--generation", str(gen1),
                        "--token-stdin", stdin="not-the-real-token")
        self.assertEqual(code, 3)
        code, r = c.run("authority", "permit-dispatch", "--generation", str(gen1),
                        "--digest", "deadbeef", "--token-stdin",
                        stdin="not-the-real-token")
        self.assertEqual(code, 3)

        # correct token, WRONG (nonexistent) generation
        code, r = c.run("authority", "renew", "--generation", str(gen1 + 41),
                        "--token-stdin", stdin=token1)
        self.assertEqual(code, 3)

        # correct token+generation actually works (control: the denials above
        # were not a broken command)
        code, r = c.run("authority", "release", "--generation", str(gen1),
                        "--token-stdin", stdin=token1)
        self.assertEqual(code, 0, r)


class AuthorityCrashRecoveryNoAba(unittest.TestCase):
    def test_crash_advances_to_generation_n_plus_1_with_reason(self):
        """AC-4."""
        c = Ctl(clock=VIRTUAL_T0)
        code, g1 = c.run("authority", "acquire", "--holder-label", "program-a",
                         "--ttl", "30")
        self.assertEqual(code, 0)
        self.assertEqual(g1["generation"], 1)

        c.advance(31)  # holder crashes, never renews/releases -> TTL lapses
        code, g2 = c.run("authority", "acquire", "--holder-label", "program-b",
                         "--ttl", "30")
        self.assertEqual(code, 0, g2)
        self.assertEqual(g2["generation"], 2)
        self.assertIn("expired", g2["reason"])
        self.assertTrue(g2["reason"])

    def test_no_aba_old_generations_never_readmitted(self):
        """AC-6: a released/expired generation can never be re-admitted as
        current, even after further generations have come and gone."""
        c = Ctl(clock=VIRTUAL_T0)
        code, g1 = c.run("authority", "acquire", "--holder-label", "program-a",
                         "--ttl", "10")
        gen1, token1 = g1["generation"], g1["token"]
        c.advance(11)
        code, g2 = c.run("authority", "acquire", "--holder-label", "program-b",
                         "--ttl", "10")
        gen2, token2 = g2["generation"], g2["token"]
        c.advance(11)
        code, g3 = c.run("authority", "acquire", "--holder-label", "program-c",
                         "--ttl", "10")
        gen3, token3 = g3["generation"], g3["token"]
        self.assertEqual([gen1, gen2, gen3], [1, 2, 3])

        # generation 1's token, replayed now: denied, not just "not current"
        code, r = c.run("authority", "renew", "--generation", str(gen1),
                        "--token-stdin", "--ttl", "10", stdin=token1)
        self.assertEqual(code, 3)
        # generation 2's token, replayed after generation 3 exists: denied too
        code, r = c.run("authority", "renew", "--generation", str(gen2),
                        "--token-stdin", "--ttl", "10", stdin=token2)
        self.assertEqual(code, 3)
        # control: generation 3 (current) still works
        code, r = c.run("authority", "renew", "--generation", str(gen3),
                        "--token-stdin", "--ttl", "10", stdin=token3)
        self.assertEqual(code, 0, r)


class AuthorityNoForceBreak(unittest.TestCase):
    def test_no_break_subcommand_exists(self):
        """AC-10: the ADR's stated, explicit choice is no-force-break —
        recovery is TTL-expiry-only (exercised in AC-4 above). Confirm the
        CLI surface has no way to force one."""
        c = Ctl()
        code, _ = c.run("authority", "break", "--generation", "1")
        # argparse rejects the unknown subcommand at the usage level (exit 2),
        # never reaching a granted/denied control-plane decision.
        self.assertEqual(code, 2)


class AuthorityPermitDispatch(unittest.TestCase):
    def test_permit_dispatch_requires_live_current_generation(self):
        c = Ctl(clock=VIRTUAL_T0)
        code, g1 = c.run("authority", "acquire", "--holder-label", "program-a",
                         "--ttl", "1000")
        gen1, token1 = g1["generation"], g1["token"]
        code, permit = c.run("authority", "permit-dispatch", "--generation",
                             str(gen1), "--digest", "abc123", "--token-stdin",
                             stdin=token1)
        self.assertEqual(code, 0, permit)
        self.assertTrue(permit["ok"])
        self.assertIsNotNone(permit["permit_id"])
        with sqlite3.connect(c.db) as cx:
            cx.row_factory = sqlite3.Row
            row = cx.execute(
                "SELECT * FROM program_action_permits WHERE id=?",
                (permit["permit_id"],)).fetchone()
        self.assertEqual(row["action_digest"], "abc123")
        self.assertEqual(row["generation"], gen1)
        self.assertIsNone(row["consumed_at"])


class AdditiveMigrationPreservesExistingRows(unittest.TestCase):
    def test_schema_rollout_does_not_touch_existing_tables(self):
        """AC-9: the new tables are additive; re-applying the schema (what a
        rollout does) leaves every existing row's fields untouched."""
        c = Ctl(clock=VIRTUAL_T0)
        MIG = "db/migrations/**"
        code, acquired = c.run("lease", "acquire", "--resource", MIG,
                               "--holder", "crew_beta", "--mode", "serialize")
        self.assertEqual(code, 0)
        code, before = c.run("lease", "list")
        self.assertEqual(code, 0)

        # Re-run schema application against the SAME already-populated DB --
        # this is exactly what a rollout does (`ensure_schema` on every
        # invocation); "init" drives the identical path explicitly.
        code, _ = c.run("init")
        self.assertEqual(code, 0)

        code, after = c.run("lease", "list")
        self.assertEqual(code, 0)
        self.assertEqual(before, after)
        # And the two new tables exist and are empty (additive, not touched).
        with sqlite3.connect(c.db) as cx:
            cx.row_factory = sqlite3.Row
            authority_rows = cx.execute(
                "SELECT COUNT(*) AS n FROM program_authority").fetchone()["n"]
            permit_rows = cx.execute(
                "SELECT COUNT(*) AS n FROM program_action_permits").fetchone()["n"]
        self.assertEqual(authority_rows, 0)
        self.assertEqual(permit_rows, 0)


class StanddownStatus(unittest.TestCase):
    """AC-7, nightshift-ctl half only. The detector-side mirror
    (deploy/nightshift-scheduled-run-detector.py) is a separate task
    (174-implement-detector, crew_beta-owned file, pending a lease)."""

    def test_schedule_observe_accepts_standdown_as_non_incident(self):
        """Before this change, `--status standdown` was `invalid choice`
        (argparse, exit 2) before any table was touched -- SCHEDULE_STATUSES
        did not include it. This asserts both halves of AC-7's nightshift-ctl
        side through real CLI behaviour: (1) it is now a valid choice at all,
        and (2) `alert_required` -- which is computed directly from
        `status in SCHEDULE_INCIDENT_STATUSES` -- is False for it, i.e. it
        never reuses the incident-alerting path `skipped`/`failed`/etc. use.
        """
        c = Ctl(clock=VIRTUAL_T0)
        code, body = c.run(
            "schedule", "observe", "--task", "heartbeat",
            "--key", "heartbeat:run:standdown-1", "--run", "sd1",
            "--scheduled-at", "100", "--status", "standdown", "--class", "none",
        )
        self.assertEqual(code, 0, body)
        self.assertFalse(body["alert_required"])
        self.assertEqual(body["observation"]["status"], "standdown")


if __name__ == "__main__":
    unittest.main()
