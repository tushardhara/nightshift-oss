"""Audit test 6: one backlog issue fans out into several task PRs — the
first merge must not close the issue.

Two deterministic mechanisms under test:
- `nightshift-ctl pr-lint` rejects GitHub closing keywords on task PR bodies and
  requires a `Refs #<n>` line.
- `nightshift-ctl issue status` computes closability from the tasks table: every
  non-dropped task integrated → closable; anything less → not. Restart-safe
  because it reads only persisted state.
"""

import unittest

from ctlutil import Ctl


class PrLint(unittest.TestCase):
    def lint(self, body):
        c = getattr(self, "_c", None) or Ctl()
        self._c = c
        return c.run("pr-lint", stdin=body)

    def test_closing_keywords_rejected(self):
        for kw in ("Closes #12", "closes #12", "Fixes #12", "fixed #12",
                   "Resolves #12", "resolve: #12", "Closes org/repo#12"):
            code, body = self.lint(f"Task PR.\n\n{kw}\n")
            self.assertEqual(code, 3, (kw, body))

    def test_refs_required_and_sufficient(self):
        code, _ = self.lint("Task PR with no issue link.\n")
        self.assertEqual(code, 3)
        code, _ = self.lint("Task PR.\n\nRefs #12\nADR: docs/decisions/ADR-0007.md\n")
        self.assertEqual(code, 0)
        code, _ = self.lint("Ref #12 — part 2 of 3.\n")
        self.assertEqual(code, 0)

    def test_prose_mentioning_fix_is_fine(self):
        code, _ = self.lint("This fixes the flaky parser (no issue ref).\n\nRefs #9\n")
        self.assertEqual(code, 0)


class IssueClose(unittest.TestCase):
    def test_first_merge_does_not_close(self):
        c = Ctl()
        c.run("task", "upsert", "--id", "t1", "--issue", "123", "--state", "building")
        c.run("task", "upsert", "--id", "t2", "--issue", "123", "--state", "building")
        # first task PR integrates
        c.run("task", "upsert", "--id", "t1", "--state", "integrated")
        code, st = c.run("issue", "status", "--issue", "123")
        self.assertEqual(code, 3)
        self.assertFalse(st["closable"])
        # second one lands → now closable
        c.run("task", "upsert", "--id", "t2", "--state", "integrated")
        code, st = c.run("issue", "status", "--issue", "123")
        self.assertEqual(code, 0)
        self.assertTrue(st["closable"])
        _, closable = c.run("issue", "closable")
        self.assertEqual(closable["closable"], [123])

    def test_dropped_tasks_do_not_hold_issue_open(self):
        c = Ctl()
        c.run("task", "upsert", "--id", "a", "--issue", "7", "--state", "integrated")
        c.run("task", "upsert", "--id", "b", "--issue", "7", "--state", "dropped")
        code, st = c.run("issue", "status", "--issue", "7")
        self.assertEqual(code, 0)
        self.assertTrue(st["closable"])

    def test_all_dropped_is_not_closable(self):
        c = Ctl()
        c.run("task", "upsert", "--id", "x", "--issue", "8", "--state", "dropped")
        code, st = c.run("issue", "status", "--issue", "8")
        self.assertEqual(code, 3)
        self.assertFalse(st["closable"])

    def test_unknown_issue_not_closable(self):
        c = Ctl()
        code, st = c.run("issue", "status", "--issue", "999")
        self.assertEqual(code, 3)
        self.assertFalse(st["closable"])


if __name__ == "__main__":
    unittest.main()
