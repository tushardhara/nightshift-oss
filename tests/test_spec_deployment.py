"""#107 — merged program/** changes must not be invisible to the running server.

The defect this suite pins: the Omnigent server is launched with
`cd /srv/project` and reads `program/agents/*/config.yaml` from underneath
it, and that shared checkout sits on a DETACHED HEAD at a commit that is not
on the integration branch. Every `program/**` change that merges — including
every tightening of `worker_semaphore`, `spawn_bounds`, `blast_radius`,
`stay_in_workspace` or the sandbox `write_paths` — has no effect on any
running agent, with no error and no warning anywhere.

Nothing here talks to the real box. The suite builds real git repositories
and a fake procfs and points the same code at them, because a detector
nobody can watch fail is not evidence (WA-008). The live capture that shows
this firing on the real divergence is on the PR.

Exit codes under test: 0 aligned, **4 diverged**, 2 undetermined. 4 has to be
its own code — 3 already means "denied / held / cooling", which callers are
taught to treat as *stand down and retry*, and divergence never clears by
waiting.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest

from ctlutil import CTL, Ctl

DIVERGED = 4


def git(repo, *argv, check=True):
    return subprocess.run(
        ["git", "-C", repo,
         "-c", "user.name=test", "-c", "user.email=test@localhost"] + list(argv),
        capture_output=True, text=True, check=check).stdout.strip()


def spec_repo(tmp, name):
    """A repo shaped like this one: a `program/agents/<seat>/config.yaml`."""
    root = os.path.join(tmp, name)
    os.makedirs(os.path.join(root, "program", "agents", "platform"))
    git(root, "init", "-q", "-b", "main")
    write_spec(root, "worker_semaphore: 4\n")
    commit(root, "base")
    return root


def write_spec(root, text):
    with open(os.path.join(root, "program", "agents", "platform",
                           "config.yaml"), "w") as f:
        f.write(text)


def commit(root, msg):
    git(root, "add", "--", "program")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def fake_proc(tmp, entries):
    """Build a procfs the scanner can walk. entries: [(pid, argv, cwd|None)]."""
    proc = tempfile.mkdtemp(dir=tmp, prefix="proc-")
    for pid, argv, cwd in entries:
        d = os.path.join(proc, str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in argv) + b"\0")
        if cwd is not None:
            os.symlink(cwd, os.path.join(d, "cwd"))
    return proc


def clone_with_remote(tmp, src, name):
    """A clone that really has `refs/remotes/origin/...`.

    The freshness rule only engages for remote-tracking refs, so these
    cases cannot be built from a bare `--expected-ref main` the way the
    others are; they need a genuine fetch relationship.
    """
    dst = os.path.join(tmp, name)
    subprocess.run(["git", "clone", "-q", "--no-local", src, dst],
                   check=True, capture_output=True)
    return dst


def age_remote_ref(clone, ref_full, seconds):
    """Backdate the caller's copy of a remote-tracking ref by `seconds`.

    Mirrors what actually happens on a box: nobody rewrites the ref, it
    simply is not fetched again. Whichever of the loose file or
    `packed-refs` git chose to use is the one the detector stats, so age
    both when both exist.
    """
    common = subprocess.run(
        ["git", "-C", clone, "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True).stdout.strip()
    if not os.path.isabs(common):
        common = os.path.join(clone, common)
    when = time.time() - seconds
    touched = 0
    for cand in (os.path.join(common, ref_full),
                 os.path.join(common, "packed-refs")):
        if os.path.exists(cand):
            os.utime(cand, (when, when))
            touched += 1
    assert touched, "no ref file to age in %s" % clone
    return touched


def run_spec(*argv):
    """`spec status` needs no control plane; it must run from anywhere."""
    p = subprocess.run([CTL, "spec", "status"] + list(argv),
                       capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


class SpecStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spec-deploy-test-")
        self.repo = spec_repo(self.tmp, "repo")

    # ------------------------------------------------------------ aligned --

    def test_aligned_when_deployed_is_exactly_the_ref(self):
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["status"], "aligned")
        self.assertIs(rep["diverged"], False)
        self.assertEqual(rep["deployed"]["sha"], rep["expected"]["sha"])

    # ----------------------------------------------------------- diverged --

    def test_behind_the_ref_is_diverged_with_a_distance(self):
        """The ordinary case: the checkout is real, just never advanced."""
        deployed = git(self.repo, "rev-parse", "HEAD")
        write_spec(self.repo, "worker_semaphore: 2\n")   # a tightening
        commit(self.repo, "tighten the semaphore")
        stale = os.path.join(self.tmp, "stale")
        subprocess.run(["git", "clone", "-q", "--no-local", self.repo, stale],
                       check=True, capture_output=True)
        git(stale, "checkout", "-q", "--detach", deployed)

        code, rep = run_spec("--spec-root", stale,
                             "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, DIVERGED, rep)
        self.assertIs(rep["diverged"], True)
        self.assertEqual(rep["divergence"]["kind"], "behind")
        self.assertEqual(rep["divergence"]["behind"], 1)
        self.assertTrue(rep["deployed"]["detached"])
        self.assertIn("MERGED IS NOT DEPLOYED", rep["reason"])

    def test_commit_not_on_the_ref_at_all(self):
        """The condition on this box: /srv/project sits on 27f3915, which is
        not reachable from the integration branch in either direction."""
        base = git(self.repo, "rev-parse", "HEAD")
        write_spec(self.repo, "worker_semaphore: 2\n")
        commit(self.repo, "on the branch")
        git(self.repo, "checkout", "-q", "--detach", base)
        write_spec(self.repo, "worker_semaphore: 9\n")
        orphan = commit(self.repo, "never merged")
        git(self.repo, "checkout", "-q", "main")

        deployed = os.path.join(self.tmp, "orphaned")
        subprocess.run(["git", "clone", "-q", "--no-local", self.repo, deployed],
                       check=True, capture_output=True)
        git(deployed, "fetch", "-q", "origin", orphan)
        git(deployed, "checkout", "-q", "--detach", orphan)

        code, rep = run_spec("--spec-root", deployed,
                             "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, DIVERGED, rep)
        self.assertIs(rep["diverged"], True)
        self.assertEqual(rep["divergence"]["kind"], "not_on_ref")
        self.assertEqual(rep["deployed"]["sha"], orphan)
        self.assertIn("not on the integration branch", rep["reason"])

    def test_uncommitted_spec_edits_are_divergence_even_at_the_right_sha(self):
        """A sha comparison alone would call this aligned. It is not: what
        the server loads is not what anyone reviewed."""
        write_spec(self.repo, "worker_semaphore: 99  # not committed\n")
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, DIVERGED, rep)
        self.assertIs(rep["diverged"], True)
        self.assertEqual(rep["divergence"]["kind"], "dirty")
        self.assertEqual(rep["deployed"]["sha"], rep["expected"]["sha"])
        self.assertTrue(rep["deployed"]["dirty"])

    def test_diverged_does_not_reuse_exit_3(self):
        """3 means 'denied / held / cooling' tool-wide — stand down and retry.
        Divergence never clears by waiting, so it must not share the code."""
        base = git(self.repo, "rev-parse", "HEAD")
        write_spec(self.repo, "worker_semaphore: 2\n")
        commit(self.repo, "advance")
        stale = os.path.join(self.tmp, "s3")
        subprocess.run(["git", "clone", "-q", "--no-local", self.repo, stale],
                       check=True, capture_output=True)
        git(stale, "checkout", "-q", "--detach", base)
        code, _ = run_spec("--spec-root", stale, "--expected-repo", self.repo,
                           "--expected-ref", "main")
        self.assertNotEqual(code, 3)
        self.assertEqual(code, DIVERGED)

    # ------------------------------------------------------- fail-closed ---

    def test_no_server_running_is_undetermined_not_aligned(self):
        """The whole bug is that absence of signal was read as good news."""
        empty = fake_proc(self.tmp, [])
        code, rep = run_spec("--proc-root", empty,
                             "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 2, rep)
        self.assertEqual(rep["status"], "undetermined")
        self.assertIs(rep["diverged"], True)

    def test_unresolvable_expected_ref_is_undetermined_not_aligned(self):
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.repo,
                             "--expected-ref", "origin/does-not-exist")
        self.assertEqual(code, 2, rep)
        self.assertEqual(rep["status"], "undetermined")
        self.assertIs(rep["diverged"], True)

    def test_directory_without_program_agents_is_undetermined(self):
        bare = os.path.join(self.tmp, "notaspecs")
        os.makedirs(bare)
        git(bare, "init", "-q", "-b", "main")
        code, rep = run_spec("--spec-root", bare, "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 2, rep)
        self.assertIs(rep["diverged"], True)
        self.assertIn("program/agents", rep["deployed"]["error"])

    # --------------------------------------------------- server discovery --

    def test_finds_the_server_by_proc_cwd_not_by_its_command_line(self):
        """The launcher's argv never names the directory — `cd /srv/project`
        happens inside a `bash -lc` string. /proc/<pid>/cwd is the only place
        the answer actually exists."""
        proc = fake_proc(self.tmp, [
            (4242, ["/usr/bin/python", "/home/x/.local/bin/omnigent", "server",
                    "--host", "127.0.0.1", "--port", "6767"], self.repo),
        ])
        code, rep = run_spec("--proc-root", proc, "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["deployed"]["root"], self.repo)
        self.assertEqual(rep["deployed"]["source"], "proc:4242")

    def test_the_neighbours_that_also_say_omnigent_are_not_the_server(self):
        """On the real box every one of these is running next to the server
        and every one of them contains the string `omnigent`. Matching any of
        them would report the wrong directory as deployed — worse than
        reporting nothing, because it would look like an answer."""
        decoys = [
            # the tmux wrapper that launches the server; `ns-server` is a
            # session name and the `cd` lives inside the -c string
            (11, ["tmux", "new-session", "-d", "-s", "ns-server", "bash", "-lc",
                  "cd /srv/project\nexec ~/.local/bin/omnigent server --port 6767"],
             self.tmp),
            # the host process: --server is a flag, not the verb
            (12, ["/usr/bin/python", "/home/x/.local/bin/omnigent", "host",
                  "--server", "http://127.0.0.1:6767", "--non-interactive"],
             self.tmp),
            # a runner zygote
            (13, ["/usr/bin/python", "-P", "-m", "omnigent.runner._zygote"],
             self.tmp),
            # codex carrying the omnigent MCP bridge in a -c argument
            (14, ["node", "/home/x/.local/bin/codex", "app-server", "--listen",
                  "ws://127.0.0.1:38055", "-c",
                  "mcp_servers.omnigent.command=/home/x/omnigent/bin/python"],
             self.tmp),
        ]
        proc = fake_proc(self.tmp, decoys)
        code, rep = run_spec("--proc-root", proc, "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 2, rep)
        self.assertIn("no running omnigent server", rep["deployed"]["error"])

        # and the real one is still found when it is standing among them
        proc = fake_proc(self.tmp, decoys + [
            (15, ["/usr/bin/python", "/home/x/.local/bin/omnigent", "server",
                  "--port", "6767"], self.repo)])
        code, rep = run_spec("--proc-root", proc, "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["deployed"]["source"], "proc:15")

    def test_two_servers_in_different_directories_refuses_to_guess(self):
        other = spec_repo(self.tmp, "other")
        proc = fake_proc(self.tmp, [
            (21, ["/usr/bin/python", "/x/omnigent", "server"], self.repo),
            (22, ["/usr/bin/python", "/x/omnigent", "server"], other),
        ])
        code, rep = run_spec("--proc-root", proc, "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 2, rep)
        self.assertIs(rep["diverged"], True)
        self.assertIn("refusing to guess", rep["deployed"]["error"])

    # ------------------------------------------------------- do-no-damage --

    def test_the_check_never_moves_the_checkout_it_inspects(self):
        """#89/#98: moving the shared checkout under the agents working in it
        has silently reverted merged work twice. This detector is pointed
        straight at that checkout, so it must be provably read-only."""
        base = git(self.repo, "rev-parse", "HEAD")
        write_spec(self.repo, "worker_semaphore: 2\n")
        commit(self.repo, "advance")
        stale = os.path.join(self.tmp, "readonly")
        subprocess.run(["git", "clone", "-q", "--no-local", self.repo, stale],
                       check=True, capture_output=True)
        git(stale, "checkout", "-q", "--detach", base)

        def snapshot():
            return (git(stale, "rev-parse", "HEAD"),
                    git(stale, "status", "--porcelain"),
                    git(stale, "reflog", "--format=%H %gs"),
                    git(stale, "for-each-ref", "--format=%(refname) %(objectname)"))

        before = snapshot()
        code, _ = run_spec("--spec-root", stale, "--expected-repo", self.repo,
                           "--expected-ref", "main")
        self.assertEqual(code, DIVERGED)
        self.assertEqual(snapshot(), before)


class DumpCarriesSpecTest(unittest.TestCase):
    """#107 ask 3: a driver must be able to see this without reading `ps`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spec-dump-test-")
        self.repo = spec_repo(self.tmp, "repo")

    def test_dump_reports_divergence_but_keeps_exit_0(self):
        """`dump` is a projection: the 30-minute state backup redirects it
        into .omni/control-backup.json. It must report the deployment fact
        and must NOT start failing on it — `spec status` is the gate."""
        base = git(self.repo, "rev-parse", "HEAD")
        write_spec(self.repo, "worker_semaphore: 2\n")
        commit(self.repo, "advance")
        stale = os.path.join(self.tmp, "stale")
        subprocess.run(["git", "clone", "-q", "--no-local", self.repo, stale],
                       check=True, capture_output=True)
        git(stale, "checkout", "-q", "--detach", base)

        c = Ctl()
        code, body = c.run("dump", "--spec-root", stale,
                           "--expected-repo", self.repo, "--expected-ref", "main")
        self.assertEqual(code, 0, body)
        self.assertIs(body["spec"]["diverged"], True)
        self.assertEqual(body["spec"]["status"], "diverged")
        self.assertEqual(body["spec"]["deployed"]["sha"], base)
        # the rest of dump is untouched
        self.assertIn("slots", body)
        self.assertIn("checkpoints", body)

    def test_dump_reports_aligned_when_it_is(self):
        c = Ctl()
        code, body = c.run("dump", "--spec-root", self.repo,
                           "--expected-repo", self.repo, "--expected-ref", "main")
        self.assertEqual(code, 0, body)
        self.assertIs(body["spec"]["diverged"], False)
        self.assertEqual(body["spec"]["status"], "aligned")


class ExpectedFreshnessTest(unittest.TestCase):
    """#107 / staff B5 — `aligned` against a ref nobody fetched is an
    assertion wearing a receipt's clothes.

    The expected side is resolved with `rev-parse` in the CALLER'S clone
    and no fetch, by design: a read-only detector that reaches the network
    stops being runnable where this one has to run. So the fix is not a
    `--fetch` flag, it is a FIELD -- report the freshness and let the
    existing fail-closed rule do the work.

    The timing is the whole argument. Today the deployed commit is on no
    ancestor path either way, so a stale `origin/...` cannot manufacture a
    green. Once specs actually deploy, "deployed == a recent tip" becomes
    the normal state and a clone that last fetched before the newest deploy
    resolves `origin/<branch>` to exactly the deployed SHA -- green, and
    wrong. Least trustworthy exactly when relied upon.
    """

    REF = "refs/remotes/origin/main"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spec-fresh-test-")
        self.repo = spec_repo(self.tmp, "repo")
        self.clone = clone_with_remote(self.tmp, self.repo, "clone")

    def test_a_freshly_fetched_remote_ref_still_reports_aligned(self):
        """The field must not cost the ordinary caller a false alarm."""
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.clone,
                             "--expected-ref", "origin/main")
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["status"], "aligned")
        self.assertIs(rep["diverged"], False)
        self.assertIs(rep["expected"]["fetched"], True)
        self.assertIs(rep["expected"]["stale"], False)
        self.assertLess(rep["expected"]["age_seconds"], 60)

    def test_matching_shas_against_a_stale_ref_is_undetermined_not_aligned(self):
        """THE case. Identical SHAs, clean tree -- and still not `aligned`,
        because the ref they match may itself be behind the remote."""
        age_remote_ref(self.clone, self.REF, 4 * 3600)
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.clone,
                             "--expected-ref", "origin/main")
        self.assertEqual(rep["deployed"]["sha"], rep["expected"]["sha"])
        self.assertEqual(rep["deployed"]["dirty"], [])
        self.assertEqual(code, 2, rep)
        self.assertEqual(rep["status"], "undetermined")
        self.assertIs(rep["diverged"], True)
        self.assertIs(rep["expected"]["stale"], True)
        self.assertGreater(rep["expected"]["age_seconds"], 3600)
        self.assertIn("not proof", rep["reason"])

    def test_the_bound_is_configurable_and_is_what_decides_it(self):
        """Same repo, same SHAs, same ref age -- only the bound moves."""
        age_remote_ref(self.clone, self.REF, 1800)
        common = ("--spec-root", self.repo, "--expected-repo", self.clone,
                  "--expected-ref", "origin/main")
        code, rep = run_spec(*common, "--max-ref-age", "3600")
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["status"], "aligned")
        code, rep = run_spec(*common, "--max-ref-age", "600")
        self.assertEqual(code, 2, rep)
        self.assertEqual(rep["status"], "undetermined")
        self.assertEqual(rep["expected"]["max_age_seconds"], 600)

    def test_a_stale_ref_never_rescues_a_real_divergence(self):
        """Fail-closed has a direction. A stale ref can only make a real
        gap look smaller; it must never turn `diverged` into a softer
        verdict, or today's 143-behind finding would evaporate."""
        base = git(self.repo, "rev-parse", "HEAD")
        write_spec(self.repo, "worker_semaphore: 2\n")
        commit(self.repo, "tighten")
        clone = clone_with_remote(self.tmp, self.repo, "clone2")
        stale = os.path.join(self.tmp, "deployed")
        subprocess.run(["git", "clone", "-q", "--no-local", self.repo, stale],
                       check=True, capture_output=True)
        git(stale, "checkout", "-q", "--detach", base)
        age_remote_ref(clone, self.REF, 10 * 3600)

        code, rep = run_spec("--spec-root", stale, "--expected-repo", clone,
                             "--expected-ref", "origin/main")
        self.assertEqual(code, DIVERGED, rep)
        self.assertEqual(rep["status"], "diverged")
        self.assertIs(rep["diverged"], True)
        self.assertIs(rep["expected"]["stale"], True)

    def test_a_local_ref_has_no_fetch_behind_it_so_it_is_not_downgraded(self):
        """`fetched` is None, not False. False would mean "we looked and
        could not tell"; a local branch has no remote round trip to have
        gone stale, and conflating the two would make every offline caller
        undetermined forever."""
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.repo,
                             "--expected-ref", "main")
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["status"], "aligned")
        self.assertIsNone(rep["expected"]["fetched"])
        self.assertIsNone(rep["expected"]["age_seconds"])
        self.assertEqual(rep["expected"]["source"], "local_ref")
        self.assertIs(rep["expected"]["stale"], False)

    def test_freshness_is_not_read_from_FETCH_HEAD(self):
        """FETCH_HEAD is shared, mutable, and rewritten by every concurrent
        agent that fetches ANY ref in the same clone. Dating this ref from
        it would invent freshness out of a neighbour's unrelated fetch --
        the precise defect this field exists to close."""
        age_remote_ref(self.clone, self.REF, 5 * 3600)
        with open(os.path.join(self.clone, ".git", "FETCH_HEAD"), "w") as f:
            f.write("%s\t\tbranch 'main' of elsewhere\n"
                    % git(self.repo, "rev-parse", "HEAD"))
        code, rep = run_spec("--spec-root", self.repo,
                             "--expected-repo", self.clone,
                             "--expected-ref", "origin/main")
        self.assertEqual(code, 2, rep)
        self.assertIs(rep["expected"]["stale"], True)
        self.assertNotIn("FETCH_HEAD", rep["expected"].get("source", ""))

    def test_dump_carries_the_freshness_fields_too(self):
        """A driver reading the 30-minute backup must see the same caveat
        the gate saw, or the backup becomes the green light."""
        age_remote_ref(self.clone, self.REF, 6 * 3600)
        c = Ctl()
        code, body = c.run("dump", "--spec-root", self.repo,
                           "--expected-repo", self.clone,
                           "--expected-ref", "origin/main")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["spec"]["status"], "undetermined")
        self.assertIs(body["spec"]["diverged"], True)
        self.assertIs(body["spec"]["expected"]["stale"], True)
        self.assertIn("age_seconds", body["spec"]["expected"])


if __name__ == "__main__":
    unittest.main()
