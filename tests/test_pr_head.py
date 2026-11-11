"""RT-66 — nightshift-ctl pr-head reads the GitHub API, not local refs (issue #66).

HERMETIC (issue #92). This whole module runs with no network and no GitHub
credential. Every `gh` invocation is faked through $NIGHTSHIFT_CTL_GH, the
same seam PrHeadDegradedModes below already uses.

What was here before, and why it had to go:

    subprocess.run([CTL, "pr-head", "69"])          # a REAL API call
    self.assertEqual(body["ref"], "alpha/64-rt25-vacuous")

Two separate defects, both fatal to the suite's usefulness:

1. It could only pass on a box holding a live `gh` credential. GitHub Actions
   has none, so `pr-head` exited 3 and the gated suite was red on EVERY run
   -- 8 of 8 on program/discovery-state. A suite that is always red carries
   no signal: nobody can mechanically answer "is this green?", so crews
   measured locally and reported a number instead, which is exactly how a
   real regression walks in unnoticed.
2. It pinned a LIVE BRANCH NAME. `alpha/64-rt25-vacuous` can be deleted by
   anyone at any time, so the test decayed even on a credentialed box.

The intent is preserved, and it is the only thing worth preserving: pr-head
must READ THE API, not resolve a local git ref. That claim is now tested by
a discriminating experiment rather than by a live call -- see PrHead below.
"""

import json
import os
import subprocess
import tempfile
import unittest

from ctlutil import CTL


def git(repo, *argv):
    return subprocess.run(["git", "-C", repo] + list(argv),
                          capture_output=True, text=True, check=True)


class PrHead(unittest.TestCase):
    """pr-head reads the GitHub API, proven WITHOUT touching the network.

    The proof is a discriminating experiment, not an observation. We build a
    real local git repo that genuinely contains a branch named exactly like
    the PR's head ref, and we point that local branch at one SHA while the
    (faked) API reports a DIFFERENT one:

        local  refs/heads/hermetic/pr-head-probe -> <a real commit>
        API    headRefOid                        -> 40 * "b"

    The two answers cannot be confused. An implementation that resolved the
    local ref returns the local SHA and this test FAILS; only an
    implementation that reads the API returns the API SHA. That is a
    stronger statement than the old live test ever made -- the old one ran
    against a repo where the local ref and the API agreed, so it could not
    have detected a local-ref implementation at all (issue #81: a test that
    passes no matter what is worth less than no test).

    Nothing here names a branch that lives on the remote, so nothing here
    decays when a branch is deleted.
    """

    # Arbitrary: `gh` is faked, so this number is never sent anywhere. It only
    # has to survive the round trip through the JSON body.
    PR = 7301
    # We CREATE this ref locally. It is not a remote branch and never was.
    REF = "hermetic/pr-head-probe"
    # Deliberately not a SHA any local object could have.
    API_SHA = "b" * 40

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rt66-hermetic-")
        self.argv_log = os.path.join(self.dir, "gh-argv")

        # A real repo holding a real branch named exactly like the head ref.
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        with open(os.path.join(self.repo, "f.txt"), "w") as f:
            f.write("local\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "local state")
        git(self.repo, "branch", self.REF)
        self.local_sha = git(
            self.repo, "rev-parse", self.REF).stdout.strip()

        # Guard the premise of the whole experiment: the two answers must
        # actually differ, or the test proves nothing.
        self.assertRegex(self.local_sha, r"^[0-9a-f]{40}$")
        self.assertNotEqual(self.local_sha, self.API_SHA)

    def _gh(self, body):
        path = os.path.join(self.dir, "gh")
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)
        return path

    def _run(self, gh_path):
        """Run pr-head INSIDE the repo, so a local ref is there to be found."""
        env = dict(os.environ, NIGHTSHIFT_CTL_GH=gh_path)
        # Clear any real credential: if the fake is ever bypassed we want a
        # hard failure, not a silent fall-through to the live API.
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        return subprocess.run(
            [CTL, "pr-head", str(self.PR)],
            capture_output=True, text=True, env=env, cwd=self.repo,
        )

    def _api_gh(self):
        """A fake `gh` that answers like the API and records its own argv."""
        return self._gh(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > {self.argv_log}\n'
            "cat <<'JSON'\n"
            f'{{"headRefOid": "{self.API_SHA}", "headRefName": "{self.REF}"}}\n'
            "JSON\n")

    def test_pr_head_returns_sha_from_api_not_the_local_ref(self):
        """THE test: API and local ref disagree, and the API wins."""
        p = self._run(self._api_gh())
        self.assertEqual(p.returncode, 0, f"pr-head failed: {p.stderr[:400]!r}")
        body = json.loads(p.stdout)

        self.assertEqual(body["pr"], self.PR)
        self.assertRegex(body["head"], r"^[0-9a-f]{40}$",
                         "head should be a full 40-character SHA")
        self.assertEqual(body["ref"], self.REF)

        # The discriminator, stated twice on purpose -- positively and
        # negatively -- because this single pair of assertions is the entire
        # reason the test exists.
        self.assertEqual(
            body["head"], self.API_SHA,
            "pr-head must return the SHA the API reported")
        self.assertNotEqual(
            body["head"], self.local_sha,
            "pr-head returned the LOCAL ref's SHA -- it is resolving local "
            "git state instead of reading the GitHub API (issue #66)")

    def test_pr_head_actually_invokes_the_api_endpoint(self):
        """Belt and braces: the right `gh` call was made, with the right argv.

        The SHA comparison above proves the local ref was not used. This
        proves what WAS used: the PR-view API, asked for exactly the two
        fields pr-head parses. An implementation that shelled out to
        `git ls-remote` would satisfy neither.
        """
        self._run(self._api_gh())
        with open(self.argv_log) as f:
            argv = f.read().split()
        self.assertEqual(argv[:3], ["pr", "view", str(self.PR)])
        self.assertIn("--json", argv)
        self.assertIn("headRefOid,headRefName", argv)

    def test_pr_head_fails_on_nonexistent_pr(self):
        """pr-head exits 3 when the API says the PR does not exist.

        Hermetic too: the old version asked the live API about PR 999999.
        With no credential that call fails for the WRONG reason (auth, not
        absence) and the test passed anyway -- it could not tell the two
        apart. The fake reproduces GitHub's actual not-found behaviour.
        """
        gh = self._gh(
            "#!/bin/sh\n"
            'echo "could not resolve to a PullRequest with the number of '
            '%s." >&2\n'
            "exit 1\n" % self.PR)
        p = self._run(gh)
        self.assertEqual(p.returncode, 3, "should exit 3 for nonexistent PR")
        body = json.loads(p.stdout)
        self.assertEqual(body.get("error"), "gh_api_failed")
        self.assertEqual(body.get("pr"), self.PR)


class PrHeadDegradedModes(unittest.TestCase):
    """RT-66 BF-3 — every degraded mode fails STRUCTURALLY, never as a crash.

    `pr-head` is a RECOVERY tool: it is the thing you run when you already do
    not trust your local state. A crash here makes recovery scripts fail in
    unknown ways, which is the class of bug this command exists to prevent.
    The contract is therefore identical in all five modes:

        exit 3   +   pure JSON on stdout   +   ZERO bytes on stderr

    The zero-byte stderr assertion is the heart of these tests, not a detail.
    The original defect (PR #70) was a FileNotFoundError traceback on stderr
    with exit 1; a test that checked only the exit code would still have
    caught THAT, but a test that checks only the JSON body would not have
    caught a handler that prints a warning before returning cleanly.

    Every test here fakes `gh` through $NIGHTSHIFT_CTL_GH, so the whole class
    is hermetic: no network, no live GitHub API, no PR number that must exist.
    It runs offline. (PrHead above is hermetic on the same seam as of issue
    #92; it used to hit the live API and hard-code a branch name.)
    """

    # Arbitrary. `gh` is faked in every test in this class, so nothing this
    # number names is ever contacted -- it only has to survive round-tripping
    # through the JSON body.
    PR = 4242

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rt66-degraded-")

    def _gh(self, name, body, mode=0o755):
        """Write a fake `gh` and return its path."""
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, mode)
        return path

    def _run(self, gh_path, cwd=None):
        env = dict(os.environ, NIGHTSHIFT_CTL_GH=gh_path)
        return subprocess.run(
            [CTL, "pr-head", str(self.PR)],
            capture_output=True, text=True, env=env, cwd=cwd,
        )

    def _assert_structured_failure(self, p, expected_error):
        """The shared contract: exit 3, pure JSON out, nothing on stderr."""
        self.assertEqual(
            p.returncode, 3,
            f"expected exit 3, got {p.returncode}; stderr={p.stderr[:400]!r}")
        self.assertEqual(
            len(p.stderr), 0,
            f"stderr must be 0 bytes, got {len(p.stderr)}: {p.stderr[:400]!r}")
        try:
            body = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"stdout must be pure JSON ({e}): {p.stdout[:400]!r}")
        self.assertEqual(body.get("error"), expected_error)
        self.assertEqual(body.get("pr"), self.PR)
        return body

    # -- mode 1: the binary is not there -----------------------------------
    # THE REGRESSION TEST for PR #70. subprocess.run() raises FileNotFoundError
    # before any CalledProcessError handler can see it. Pre-fix this was an
    # unhandled traceback + exit 1.
    def test_missing_gh_binary_is_structured_not_a_traceback(self):
        p = self._run("/nonexistent/gh")
        body = self._assert_structured_failure(p, "gh_binary_missing")
        self.assertEqual(body.get("gh_path"), "/nonexistent/gh")
        self.assertNotIn("Traceback", p.stderr)

    # -- mode 2: the binary is there but cannot be executed -----------------
    # Distinct variable from mode 1: the path RESOLVES, the exec fails.
    # Reaches the same arm through PermissionError (an OSError, not a
    # FileNotFoundError), which is why the handler must catch OSError too.
    def test_non_executable_gh_binary_is_structured(self):
        gh = self._gh("gh-noexec", "#!/bin/sh\nexit 0\n", mode=0o644)
        body = self._assert_structured_failure(
            self._run(gh), "gh_binary_missing")
        self.assertEqual(body.get("gh_path"), gh)

    # -- mode 3: gh runs and exits NON-ZERO ---------------------------------
    def test_non_zero_exit_is_gh_api_failed(self):
        gh = self._gh(
            "gh-nonzero",
            '#!/bin/sh\necho "API rate limit exceeded" >&2\nexit 1\n')
        body = self._assert_structured_failure(self._run(gh), "gh_api_failed")
        # The wrapper's stderr is CARRIED INTO the JSON body rather than
        # leaking to our own stderr -- that is why stderr is 0 bytes above.
        self.assertIn("API rate limit exceeded", body.get("stderr", ""))

    # -- mode 4: gh exits ZERO with a body that is not JSON -----------------
    # A DIFFERENT VARIABLE from mode 3, and this is the one that was never
    # exercised before (finding BF-2): the old record labelled a cell
    # "non-JSON" but recorded a `stderr` field, which only the non-zero-exit
    # arm produces -- so it had re-run mode 3 under mode 4's name.
    def test_exit_zero_with_non_json_body_is_parse_failed(self):
        gh = self._gh("gh-nonjson", '#!/bin/sh\necho "<!DOCTYPE html>"\nexit 0\n')
        body = self._assert_structured_failure(self._run(gh), "parse_failed")
        # The discriminator: this arm has no `stderr` field. If this assertion
        # ever fails, mode 4 has silently become mode 3 again.
        self.assertNotIn("stderr", body)

    # -- mode 5: gh exits ZERO with valid JSON that lacks headRefOid --------
    # The shape a GitHub API change would take. Same error, different
    # exception (KeyError), so the handler must catch both.
    def test_exit_zero_with_json_missing_head_ref_oid_is_parse_failed(self):
        gh = self._gh(
            "gh-nokey",
            '#!/bin/sh\necho \'{"headRefName": "some/branch"}\'\nexit 0\n')
        body = self._assert_structured_failure(self._run(gh), "parse_failed")
        self.assertIn("headRefOid", body.get("details", ""))

    # -- the BF-2 guard, stated as its own test -----------------------------
    def test_non_zero_exit_and_non_json_body_reach_different_arms(self):
        """Two different variables must produce two different errors.

        This is the assertion that would have caught BF-2 mechanically. It is
        cheap, and it fails loudly the moment someone collapses the parse arm
        into the API-failure arm.
        """
        nonzero = self._gh("gh-a", '#!/bin/sh\necho boom >&2\nexit 1\n')
        nonjson = self._gh("gh-b", '#!/bin/sh\necho not-json\nexit 0\n')
        a = json.loads(self._run(nonzero).stdout)["error"]
        b = json.loads(self._run(nonjson).stdout)["error"]
        self.assertEqual(a, "gh_api_failed")
        self.assertEqual(b, "parse_failed")
        self.assertNotEqual(
            a, b, "non-zero exit and unparseable body are NOT the same failure")

    # -- the other half of the fix: pr-head needs no control plane ----------
    def test_pr_head_runs_with_no_control_plane_database(self):
        """`pr-head` is in PLANE_FREE_COMMANDS: it must not require a DB.

        The same commit that added the FileNotFoundError handler also added
        "pr-head" to PLANE_FREE_COMMANDS. A recovery tool that cannot run
        until the control plane is healthy is useless in exactly the incident
        it exists for. Run from a scratch directory with no .omni anywhere.
        """
        gh = self._gh(
            "gh-ok",
            '#!/bin/sh\necho \'{"headRefOid": "%s", "headRefName": "x/y"}\'\n'
            % ("a" * 40))
        p = self._run(gh, cwd=self.dir)
        self.assertEqual(
            p.returncode, 0,
            f"pr-head must work with no DB; stderr={p.stderr[:400]!r}")
        self.assertEqual(len(p.stderr), 0, f"stderr: {p.stderr[:400]!r}")
        self.assertEqual(json.loads(p.stdout)["head"], "a" * 40)


if __name__ == "__main__":
    unittest.main()
