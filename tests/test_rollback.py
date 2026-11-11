"""Audit test 10: rollback on the published default branch preserves history.

Two layers:
- A live git simulation: a bad PR merges, the integrator reverts with
  `git revert -m 1`. The default branch goes back to good content while the
  bad merge stays reachable — nothing is reset, nothing rewritten.
- Bundle consistency: no prompt in the bundle may instruct resetting the
  published default branch to a prior SHA (the policy layer denies it, so an
  instruction to do it would strand an agent between prompt and policy).
"""

import os
import re
import subprocess
import tempfile
import unittest

from ctlutil import REPO


def git(repo, *argv):
    return subprocess.run(["git", "-C", repo] + list(argv),
                          capture_output=True, text=True, check=True)


class RevertPreservesHistory(unittest.TestCase):
    def test_revert_not_reset(self):
        repo = tempfile.mkdtemp(prefix="rollback-")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        with open(os.path.join(repo, "app.txt"), "w") as f:
            f.write("good\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "good state")
        pre = git(repo, "rev-parse", "HEAD").stdout.strip()
        # bad PR lands as a real merge commit
        git(repo, "checkout", "-qb", "crew_a/bad-task")
        with open(os.path.join(repo, "app.txt"), "w") as f:
            f.write("bad\n")
        git(repo, "commit", "-aqm", "bad change")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "--no-ff", "-q", "-m", "merge bad PR", "crew_a/bad-task")
        merge_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        # integrator rolls back the published branch: REVERT, never reset
        git(repo, "revert", "-m", "1", "--no-edit", merge_sha)
        with open(os.path.join(repo, "app.txt")) as f:
            self.assertEqual(f.read(), "good\n")
        # history-preserving: the bad merge is still an ancestor of main
        anc = subprocess.run(
            ["git", "-C", repo, "merge-base", "--is-ancestor", merge_sha, "main"])
        self.assertEqual(anc.returncode, 0)
        # and the pre-merge tag point is too (nothing was rewritten away)
        anc = subprocess.run(
            ["git", "-C", repo, "merge-base", "--is-ancestor", pre, "main"])
        self.assertEqual(anc.returncode, 0)


class BundleConsistency(unittest.TestCase):
    def _bundle_text(self):
        chunks = []
        for root, _, files in os.walk(os.path.join(REPO, "program")):
            for fn in files:
                if fn.endswith((".yaml", ".md")):
                    p = os.path.join(root, fn)
                    with open(p) as f:
                        chunks.append((p, f.read()))
        with open(os.path.join(REPO, "AGENTS.md")) as f:
            chunks.append((os.path.join(REPO, "AGENTS.md"), f.read()))
        return chunks

    def test_no_reset_instruction_for_published_branch(self):
        offenders = []
        for path, text in self._bundle_text():
            for m in re.finditer(r"reset the branch to|git reset --hard", text):
                # allow only lines that explicitly forbid it
                line = text[:m.start()].count("\n")
                snippet = text.splitlines()[line]
                if not re.search(r"never|denied|DENY|do not", snippet, re.I):
                    offenders.append(f"{path}:{line + 1}: {snippet.strip()}")
        self.assertEqual(offenders, [])

    def test_no_closing_keyword_instruction_on_task_prs(self):
        offenders = []
        for path, text in self._bundle_text():
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(r"`Closes #", line) and not re.search(
                        r"never|not|instead of", line, re.I):
                    offenders.append(f"{path}:{i}: {line.strip()}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
