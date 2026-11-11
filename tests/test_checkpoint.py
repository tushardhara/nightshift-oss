"""Audit test 7: worktree/checkpoint correctness.

A worker dispatched after a spec/ADR update must see EXACTLY the version it
was contracted against: the checkpoint records the SHA, the worktree is cut
from that SHA, and a checkpoint refuses to exist while specs are uncommitted.
"""

import os
import subprocess
import tempfile
import unittest

from ctlutil import Ctl


def git(repo, *argv, **kw):
    return subprocess.run(["git", "-C", repo] + list(argv),
                          capture_output=True, text=True, check=True, **kw)


class Checkpoints(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ckpt-repo-")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        os.makedirs(os.path.join(self.repo, "docs/specs"))

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w") as f:
            f.write(text)

    def test_worktree_pinned_to_checkpoint_sha(self):
        c = Ctl()
        self.write("docs/specs/export.md", "spec v1: CSV only\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "spec v1")
        code, ck = c.run("checkpoint", "create", "--repo", self.repo,
                         "--wave", "1", "--ref", "main",
                         "--note", "wave 1 fan-out")
        self.assertEqual(code, 0, ck)
        sha1 = ck["sha"]
        # spec changes AFTER the wave was contracted
        self.write("docs/specs/export.md", "spec v2: CSV and JSON\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "spec v2")
        # worker worktree is cut from the checkpoint, not from HEAD
        wt = os.path.join(self.repo, ".worktrees", "t-export")
        git(self.repo, "worktree", "add", wt, "-b", "crew_a/t-export", sha1)
        with open(os.path.join(wt, "docs/specs/export.md")) as f:
            self.assertEqual(f.read(), "spec v1: CSV only\n")
        # and the recorded checkpoint reproduces this after any restart
        code, latest = c.run("checkpoint", "latest", "--wave", "1")
        self.assertEqual(latest["checkpoint"]["sha"], sha1)

    def test_dirty_specs_block_checkpoint(self):
        c = Ctl()
        self.write("docs/specs/export.md", "spec v1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "v1")
        self.write("docs/specs/export.md", "uncommitted edit\n")
        code, body = c.run("checkpoint", "create", "--repo", self.repo,
                            "--ref", "main")
        self.assertEqual(code, 3)
        self.assertIn("docs/specs/export.md", " ".join(body["dirty"]))
        # base_sha lands in the task registry row
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "v2")
        code, ck = c.run("checkpoint", "create", "--repo", self.repo,
                          "--ref", "main")
        self.assertEqual(code, 0, ck)
        c.run("task", "upsert", "--id", "t-export", "--base-sha", ck["sha"])
        _, t = c.run("task", "show", "--id", "t-export")
        self.assertEqual(t["task"]["base_sha"], ck["sha"])


class CheckpointMustBeOnRef(unittest.TestCase):
    """Issue #80 / ADR-0007: a checkpoint must be verifiably CONTAINED in the
    ref it claims to checkpoint. Reproduces, at test scale, exactly the
    incident that cut the real wave-3 checkpoint wrong twice: a checkout
    whose HEAD (or an explicit --sha) sits on a commit that never landed on
    the integration branch, recorded anyway with ok:true.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ckpt-ref-repo-")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        with open(os.path.join(self.repo, "f.txt"), "w") as f:
            f.write("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        # a commit that diverges from main and is never merged into it --
        # the shape of the shared checkout's stray detached-HEAD commit
        git(self.repo, "checkout", "-qb", "stray", "main")
        with open(os.path.join(self.repo, "f.txt"), "w") as f:
            f.write("stray work, never merged to main\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "stray commit")
        self.stray_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        git(self.repo, "checkout", "-q", "main")

    def test_ref_is_now_required(self):
        c = Ctl()
        code, _ = c.run("checkpoint", "create", "--repo", self.repo)
        # argparse itself refuses a required flag that is missing: exit 2,
        # never the ok:true a caller could get away with pre-fix.
        self.assertEqual(code, 2)

    def test_explicit_sha_not_on_ref_is_refused(self):
        c = Ctl()
        code, body = c.run("checkpoint", "create", "--repo", self.repo,
                            "--ref", "main", "--sha", self.stray_sha)
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])
        self.assertIn(self.stray_sha, body["reason"])
        self.assertIn("not on ref", body["reason"])
        # and nothing was recorded -- checkpoint latest still finds nothing
        code, latest = c.run("checkpoint", "latest")
        self.assertEqual(code, 3, latest)

    def test_detached_head_off_the_ref_is_refused(self):
        # the exact shape of the real incident: HEAD detached at a commit
        # that is not on the branch the checkpoint is supposed to gate.
        git(self.repo, "checkout", "-q", self.stray_sha)
        c = Ctl()
        code, body = c.run("checkpoint", "create", "--repo", self.repo,
                            "--ref", "main")
        self.assertEqual(code, 2, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["sha"], self.stray_sha)

    def test_sha_on_ref_is_accepted(self):
        # CONTROL: the positive case is unchanged -- exit 0, ok:true, same
        # sha echoed back. This must pass on the pre-fix binary too (it
        # already accepted --ref, just never enforced it); it is here to
        # prove the fix is a REFUSAL added on top, not a behaviour change
        # to the valid path.
        c = Ctl()
        head = git(self.repo, "rev-parse", "main").stdout.strip()
        code, body = c.run("checkpoint", "create", "--repo", self.repo,
                            "--ref", "main", "--sha", head)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["sha"], head)

    def test_recorded_checkpoint_echoes_the_ref_it_was_verified_against(self):
        # REGRESSION (separate from the control above -- do not fold the
        # two together, that hybrid shape is exactly what crew_beta caught
        # on PR #78 this morning): the ref that gated acceptance must be
        # visible in the response, not just stored blind. Pre-fix, "ref"
        # is absent from the output body entirely, so this fails via a
        # clean assertion (`.get` avoids a bare KeyError -- R-45: convert
        # errors to assertions whenever the test is touched), not via
        # harness mechanics.
        c = Ctl()
        head = git(self.repo, "rev-parse", "main").stdout.strip()
        code, body = c.run("checkpoint", "create", "--repo", self.repo,
                            "--ref", "main", "--sha", head)
        self.assertEqual(code, 0, body)
        self.assertEqual(body.get("ref"), "main")


if __name__ == "__main__":
    unittest.main()
