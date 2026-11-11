"""Issue #541 -- path scope on the task row, enforced at `slot acquire`.

The defect these tests exist to prevent: the control plane cannot answer "can
these two tasks run at the same time?", so the Program serialises work that is
genuinely parallel-safe, and ADR-0049 falls back to a human predicting overlap
at decomposition time ("anything sharing a file goes sequentially").

Every assertion here is about a REAL grant decision from a real subprocess
against real SQLite -- never a mocked predicate.
"""

import unittest

from ctlutil import CTL, Ctl


class ScopePredicate(unittest.TestCase):
    """`scopes_overlap` is glob-aware, unlike `leases.resource` matching.

    `program/skills/allocate/SKILL.md` states the control plane "matches
    resources by string, not by glob overlap". That is exactly the
    false-disjointness this ticket removes: under string equality `src/**` and
    `src/foo/x.py` are unrelated, so two workers edit one tree and the
    integrator discovers it at merge.
    """

    def setUp(self):
        with open(CTL, encoding="utf-8") as handle:
            source = handle.read()
        namespace = {}
        exec(compile(source.split("def main(")[0], "ctl", "exec"), namespace)
        self.overlap = namespace["scopes_overlap"]
        self.parse = namespace["parse_scope"]

    def test_glob_contains_file_in_both_directions(self):
        """The case string equality misses, and it must hold symmetrically."""
        self.assertTrue(self.overlap("src/**", "src/foo/x.py"))
        self.assertTrue(self.overlap("src/foo/x.py", "src/**"))

    def test_directory_prefix_contains_its_subtree(self):
        self.assertTrue(self.overlap("docs/**", "docs/audit/A.md"))

    def test_disjoint_subtrees_do_not_overlap(self):
        self.assertFalse(self.overlap("src/**", "docs/**"))

    def test_sibling_files_do_not_overlap(self):
        """Two different files under one directory are parallel-safe.

        The check must not over-refuse: that would re-create the sequential
        behaviour this ticket exists to remove, just with extra steps.
        """
        self.assertFalse(self.overlap("docs/audit/A.md", "docs/audit/B.md"))

    def test_undeclared_scope_never_overlaps(self):
        """AC-7. An absent scope is NOT a claim on everything.

        Every task row written before this migration has NULL paths. If an
        undeclared scope matched everything, this change would instantly
        deadlock the entire backlog.
        """
        self.assertFalse(self.overlap(None, "src/**"))
        self.assertFalse(self.overlap("", "src/**"))
        self.assertFalse(self.overlap("src/**", None))

    def test_scope_accepts_comma_and_newline_lists(self):
        self.assertEqual(self.parse("a.py, b.py"), ["a.py", "b.py"])
        self.assertEqual(self.parse("a.py\nb.py"), ["a.py", "b.py"])
        self.assertTrue(self.overlap("a.py,b.py", "b.py"))
        self.assertFalse(self.overlap("a.py\nb.py", "c.py"))


class ScopeEnforcedAtSlotAcquire(unittest.TestCase):
    """AC-4/AC-5/AC-6 -- the grant decision itself."""

    def _task_with_scope(self, c, task, scope, agent="eng_claude"):
        title = f"fixture-{task}"
        code, body = c.run("run", "current", "--set", "fixture-run")
        self.assertEqual(code, 0, body)
        code, body = c.run("task", "upsert", "--id", task, "--crew",
                           "crew_alpha", "--agent", agent, "--title", title,
                           "--state", "pending", "--size", "S",
                           "--paths", scope)
        self.assertEqual(code, 0, body)
        return title

    def _acquire(self, c, task, title, agent="eng_claude"):
        return c.run("slot", "acquire", "--holder", f"crew_alpha/{agent}",
                     "--crew", "crew_alpha", "--task", task, "--agent", agent,
                     "--title", title, "--purpose", "implement")

    def test_overlapping_scopes_second_grant_is_refused(self):
        """AC-5. Two tasks whose scopes intersect cannot both be in flight."""
        c = Ctl()
        title_a = self._task_with_scope(c, "scope-a", "src/**")
        code, body = self._acquire(c, "scope-a", title_a)
        self.assertEqual(code, 0, body)

        title_b = self._task_with_scope(c, "scope-b", "src/foo/x.py",
                                        agent="eng_codex")
        code, body = self._acquire(c, "scope-b", title_b, agent="eng_codex")
        self.assertEqual(code, 3, body)
        self.assertIn("scope", body["reason"])
        # the refusal must NAME the conflict -- a denial you cannot act on
        # sends the caller into a retry loop against the same wall
        self.assertEqual(body.get("conflicting_task"), "scope-a")

    def test_disjoint_scopes_are_both_granted(self):
        """AC-6. The whole point: parallel-safe work must stay parallel."""
        c = Ctl()
        title_a = self._task_with_scope(c, "disj-a", "src/**")
        code, body = self._acquire(c, "disj-a", title_a)
        self.assertEqual(code, 0, body)

        title_b = self._task_with_scope(c, "disj-b", "docs/**",
                                        agent="eng_codex")
        code, body = self._acquire(c, "disj-b", title_b, agent="eng_codex")
        self.assertEqual(code, 0, body)

    def test_scope_overlapping_an_active_lease_is_refused(self):
        """AC-4. Leases are held by agents that may not hold a slot at all."""
        c = Ctl()
        code, body = c.run("lease", "acquire", "--resource", "bin/nightshift-ctl",
                           "--mode", "serialize", "--holder", "crew_beta")
        self.assertEqual(code, 0, body)

        title = self._task_with_scope(c, "lease-clash", "bin/**")
        code, body = self._acquire(c, "lease-clash", title)
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("resource"), "bin/nightshift-ctl")
        self.assertEqual(body.get("holder"), "crew_beta")

    def test_task_own_lease_does_not_block_its_own_slot(self):
        """#570 regression. A task's own lease is its reservation, not a rival.

        `program/skills/allocate/SKILL.md` has leads acquire edit leases BEFORE
        touching a file, so the lease for a task reliably exists by the time
        that task asks for a slot. The original #541 loop matched every active
        lease with no `task_id` exclusion -- so following the documented
        workflow deadlocked the task against itself, and on 2026-09-02 two
        real scoped tasks (`463-469-ctl-guards`, `520-d22-validator-defects`)
        sat pending behind their own leases.
        """
        c = Ctl()
        title = self._task_with_scope(c, "own-lease", "bin/**")
        code, body = c.run("lease", "acquire", "--resource", "bin/nightshift-ctl",
                           "--holder", "crew_alpha", "--task", "own-lease")
        self.assertEqual(code, 0, body)
        code, body = self._acquire(c, "own-lease", title)
        self.assertEqual(code, 0, body)

    def test_another_tasks_lease_still_blocks(self):
        """The exclusion must be by task identity, not a blanket opt-out.

        Relaxing this to "any lease the holder owns" would re-open exactly the
        cross-task collision #541 exists to refuse.
        """
        c = Ctl()
        code, body = c.run("lease", "acquire", "--resource", "bin/nightshift-ctl",
                           "--holder", "crew_alpha", "--task", "some-other-task")
        self.assertEqual(code, 0, body)
        title = self._task_with_scope(c, "foreign-lease", "bin/**")
        code, body = self._acquire(c, "foreign-lease", title)
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("resource"), "bin/nightshift-ctl")

    def test_unbound_lease_still_blocks(self):
        """A lease with no task_id belongs to nobody and must keep blocking."""
        c = Ctl()
        code, body = c.run("lease", "acquire", "--resource", "bin/nightshift-ctl",
                           "--holder", "crew_alpha")
        self.assertEqual(code, 0, body)
        title = self._task_with_scope(c, "unbound-lease", "bin/**")
        code, body = self._acquire(c, "unbound-lease", title)
        self.assertEqual(code, 3, body)

    def test_task_without_scope_is_unaffected(self):
        """AC-7. Backward compatibility, asserted rather than assumed.

        `acquire_normal` creates its task with no --paths at all, exactly like
        every row that predates this migration.
        """
        c = Ctl()
        code, body = c.acquire_normal(task="no-scope-1")
        self.assertEqual(code, 0, body)
        code, body = c.acquire_normal(task="no-scope-2", agent="eng_codex")
        self.assertEqual(code, 0, body)


if __name__ == "__main__":
    unittest.main()
