"""Audit test 4: transactional shared-path leases.

- Two crews racing for the migration lease: exactly one wins.
- Serialize leases refuse to release at PR-open; they hold until merged
  or dropped (migrations, lockfiles, schema sequencing).
- Migration sequence numbers allocate atomically, and only to the lease
  holder — two crews can never both get "the next number".
"""

import unittest

from ctlutil import Ctl, run_parallel

MIG = "db/migrations/**"


class Leases(unittest.TestCase):
    def test_concurrent_lease_one_winner(self):
        c = Ctl()
        cmds = [
            c.cmdline("lease", "acquire", "--resource", MIG,
                      "--holder", f"crew_{i}", "--mode", "serialize")
            for i in range(6)
        ]
        results = run_parallel(cmds)
        granted = [r for code, r in results if code == 0]
        self.assertEqual(len(granted), 1)
        self.assertEqual(len([r for code, r in results if code == 3]), 5)

    def test_serialize_lifecycle(self):
        c = Ctl()
        code, _ = c.run("lease", "acquire", "--resource", MIG,
                        "--holder", "crew_beta", "--mode", "serialize")
        self.assertEqual(code, 0)
        # PR opened: an edit lease could release here; serialize must not.
        code, body = c.run("lease", "release", "--resource", MIG,
                           "--holder", "crew_beta", "--event", "pr-opened")
        self.assertEqual(code, 3, body)
        code, _ = c.run("lease", "release", "--resource", MIG,
                        "--holder", "crew_beta", "--event", "pr-merged")
        self.assertEqual(code, 0)
        code, _ = c.run("lease", "acquire", "--resource", MIG,
                        "--holder", "crew_alpha", "--mode", "serialize")
        self.assertEqual(code, 0)

    def test_edit_lease_releases_on_pr_open(self):
        c = Ctl()
        c.run("lease", "acquire", "--resource", "src/shared/**",
              "--holder", "crew_alpha", "--mode", "edit")
        code, _ = c.run("lease", "release", "--resource", "src/shared/**",
                        "--holder", "crew_alpha", "--event", "pr-opened")
        self.assertEqual(code, 0)

    def test_wrong_holder_cannot_release(self):
        c = Ctl()
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        code, _ = c.run("lease", "release", "--resource", MIG,
                        "--holder", "crew_alpha", "--event", "pr-merged")
        self.assertEqual(code, 3)

    def test_break_frees_stale_serialize_holder(self):
        c = Ctl()
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        code, _ = c.run("lease", "break", "--resource", MIG,
                        "--reason", "holder task dropped after 3 loops")
        self.assertEqual(code, 0)
        code, _ = c.run("lease", "acquire", "--resource", MIG,
                        "--holder", "crew_alpha", "--mode", "serialize")
        self.assertEqual(code, 0)

    def test_sequence_atomic_and_lease_gated(self):
        c = Ctl()
        code, _ = c.run("seq", "next", "--name", "migrations",
                        "--resource", MIG, "--holder", "crew_beta")
        self.assertEqual(code, 3)  # nobody holds the lease yet
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        cmds = [
            c.cmdline("seq", "next", "--name", "migrations",
                      "--resource", MIG, "--holder", "crew_beta")
            for _ in range(16)
        ]
        results = run_parallel(cmds)
        values = sorted(r["value"] for code, r in results if code == 0)
        self.assertEqual(values, list(range(1, 17)))  # unique, gapless
        code, _ = c.run("seq", "next", "--name", "migrations",
                        "--resource", MIG, "--holder", "crew_alpha")
        self.assertEqual(code, 3)  # non-holder still denied


if __name__ == "__main__":
    unittest.main()
