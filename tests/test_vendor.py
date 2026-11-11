"""Audit tests 8 and 9: vendor rate limits.

- One vendor limited → cooldown recorded, checks deny, no retry storm
  (the backoff doubles per consecutive limit, capped).
- Both vendors limited → `vendor check --all` reports all_cooling with the
  minimum wait, which is exactly the timer the program sleeps on — and a
  second `limited` call never spawns anything, it only extends state.
"""

import unittest

from ctlutil import Ctl


class VendorCooldowns(unittest.TestCase):
    def test_backoff_doubles_and_caps(self):
        c = Ctl()
        _, r1 = c.run("vendor", "limited", "--vendor", "claude",
                      "--base", "1800", "--cap", "14400")
        _, r2 = c.run("vendor", "limited", "--vendor", "claude",
                      "--base", "1800", "--cap", "14400")
        _, r3 = c.run("vendor", "limited", "--vendor", "claude",
                      "--base", "1800", "--cap", "14400")
        _, r5 = c.run("vendor", "limited", "--vendor", "claude",
                      "--base", "1800", "--cap", "14400")
        self.assertEqual(r1["cooldown_seconds"], 1800)
        self.assertEqual(r2["cooldown_seconds"], 3600)
        self.assertEqual(r3["cooldown_seconds"], 7200)
        self.assertEqual(r5["cooldown_seconds"], 14400)  # capped at 4h

    def test_single_vendor_cooling_other_available(self):
        c = Ctl()
        c.run("vendor", "limited", "--vendor", "claude")
        c.run("vendor", "ok", "--vendor", "codex")
        code, _ = c.run("vendor", "check", "--vendor", "claude")
        self.assertEqual(code, 3)
        code, _ = c.run("vendor", "check", "--vendor", "codex")
        self.assertEqual(code, 0)  # work moves here, no storm

    def test_both_limited_yields_timer(self):
        c = Ctl()
        c.run("vendor", "limited", "--vendor", "claude", "--base", "100")
        c.run("vendor", "limited", "--vendor", "codex", "--base", "300")
        code, body = c.run("vendor", "check", "--all")
        self.assertEqual(code, 3)
        self.assertTrue(body["all_cooling"])
        self.assertLessEqual(body["min_wait_seconds"], 100)
        self.assertGreater(body["min_wait_seconds"], 90)

    def test_success_resets_backoff(self):
        c = Ctl()
        c.run("vendor", "limited", "--vendor", "claude", "--base", "1800")
        c.run("vendor", "limited", "--vendor", "claude", "--base", "1800")
        c.run("vendor", "ok", "--vendor", "claude")
        code, _ = c.run("vendor", "check", "--vendor", "claude")
        self.assertEqual(code, 0)
        _, r = c.run("vendor", "limited", "--vendor", "claude", "--base", "1800")
        self.assertEqual(r["cooldown_seconds"], 1800)  # back to base


if __name__ == "__main__":
    unittest.main()
