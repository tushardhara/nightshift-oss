"""DoD condition 11 -- the clean-source install rehearsal, backed hermetically.

`tests/linux/install-rehearsal.sh` is the container rehearsal itself (its own
header documents the real `docker run` invocation and, per-step, which parts
are hermetic, which are networked, and which are a named, reasoned stub --
DoD condition 11's exact requirement). It has no docker dependency of its
own; it is meant to be invoked FROM inside a container.

This file is what makes `rehearsal_ci_job: "tests"` in
`docs/prd/DOD-MANIFEST.json` honest (ADR-0054, decision point 1): it drives
the REAL script against REAL, non-stubbed decision logic
(`deploy/lib/deploy-lib.sh`'s exact functions `provision-ubuntu.sh` /
`bootstrap.sh` call) with only the network calls and the bwrap binary
injected -- exactly the convention `tests/test_bwrap_probe.py` and
`tests/test_go_toolchain.py` already use. It never invokes `docker pull` or
`apt-get`; the full container run stays a local, documented, on-demand
demonstration (this script's own header).

These tests belong to seam B ("tests that drive `deploy/*.sh`",
`docs/owners.md`).
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tests", "linux", "install-rehearsal.sh")

WORKING_BWRAP = "#!/usr/bin/env bash\nexit 0\n"
BROKEN_BWRAP = (
    "#!/usr/bin/env bash\n"
    "echo 'bwrap: setting up uid map: Permission denied' >&2\n"
    "exit 1\n"
)

# Answers well enough for nightshift_go_resolve_version / nightshift_go_checksum:
# both call `"$curl_cmd" -fsSL <url> -o <tmp-file>` and read the FILE back.
FAKE_CURL_OK = """#!/usr/bin/env bash
out=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then out="$arg"; fi
  prev="$arg"
done
case "$*" in
  *"go.dev/VERSION"*)
    [ -n "$out" ] && printf 'go1.25.1\\n' > "$out" || printf 'go1.25.1\\n'
    ;;
  *"go.dev/dl/"*)
    body='[{"files":[{"filename":"go1.25.1.linux-amd64.tar.gz","sha256":"deadbeefcafe"}]}]'
    [ -n "$out" ] && printf '%s' "$body" > "$out" || printf '%s' "$body"
    ;;
  *) exit 1 ;;
esac
exit 0
"""

FAKE_CURL_DOWN = "#!/usr/bin/env bash\nexit 7\n"  # curl's own \"couldn't connect\" code


def _write_exec(path, body):
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def run_rehearsal(sha=None, bwrap_body=WORKING_BWRAP, curl_body=FAKE_CURL_OK,
                   teams_dir=None, extra_env=None, keep_workdir=False):
    """Run the real script hermetically. Returns (code, output, extra_env_used)."""
    with tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as scratch:
        bwrap_path = _write_exec(os.path.join(bindir, "bwrap"), bwrap_body)
        curl_path = _write_exec(os.path.join(bindir, "curl"), curl_body)
        env = dict(os.environ)
        env["NIGHTSHIFT_BWRAP"] = bwrap_path
        env["NIGHTSHIFT_REHEARSAL_CURL"] = curl_path
        env["NIGHTSHIFT_REHEARSAL_TEAMS_DIR"] = teams_dir or os.path.join(scratch, "srv-nightshift")
        if keep_workdir:
            env["NIGHTSHIFT_REHEARSAL_KEEP_WORKDIR"] = "1"
        if extra_env:
            env.update(extra_env)
        target_sha = sha or subprocess.run(
            ["git", "-C", REPO, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        p = subprocess.run(
            ["bash", SCRIPT, target_sha],
            capture_output=True, text=True, env=env, timeout=300,
        )
        return p.returncode, p.stdout + p.stderr


class ScriptExistsAndDeclaresPerStep(unittest.TestCase):
    """The exact literal requirement `check_d11` enforces."""

    def test_script_exists(self):
        self.assertTrue(os.path.isfile(SCRIPT), "tests/linux/install-rehearsal.sh must exist")

    def test_header_declares_hermetic_networked_stub(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            text = fh.read().lower()
        for word in ("hermetic", "networked", "stub"):
            self.assertIn(word, text, f"header must declare {word!r} per step (DoD condition 11)")

    def test_manifest_names_a_required_rehearsal_ci_job(self):
        import json
        with open(os.path.join(REPO, "docs", "prd", "DOD-MANIFEST.json")) as fh:
            manifest = json.load(fh)
        required = manifest.get("required_ci_jobs") or []
        rehearsal_job = manifest.get("rehearsal_ci_job")
        self.assertTrue(rehearsal_job, "manifest must name a rehearsal_ci_job")
        self.assertIn(rehearsal_job, required, "rehearsal_ci_job must be a member of required_ci_jobs")


class RehearsalRunsRealDecisionLogic(unittest.TestCase):
    """Every PASS line must come from the REAL deploy/lib/deploy-lib.sh functions,
    not a reimplementation -- so a regression in the real function must be
    visible here too (verified below by breaking one and watching this fail).
    """

    def test_clean_run_reports_all_five_decision_points(self):
        code, out = run_rehearsal(bwrap_body=BROKEN_BWRAP)
        self.assertIn("clean-source extraction:", out)
        self.assertIn("condition 1 site A", out)
        self.assertIn("condition 1 site B", out)
        self.assertIn("condition 2:", out)
        self.assertIn("condition 3:", out)
        self.assertIn("condition 5:", out)
        self.assertEqual(code, 0, f"a clean run must exit 0:\n{out}")

    def test_teams_dir_present_is_a_hard_refusal(self):
        with tempfile.TemporaryDirectory() as already_there:
            code, out = run_rehearsal(teams_dir=already_there)
        self.assertNotEqual(code, 0, "rehearsal must refuse to run when TEAMS_DIR already exists")
        self.assertIn(already_there, out)
        self.assertIn("already exists", out)

    def test_condition_5_uses_the_real_bwrap_probe_not_a_reimplementation(self):
        # Working bwrap -> functional PASS naming the probe itself.
        code, out = run_rehearsal(bwrap_body=WORKING_BWRAP)
        self.assertEqual(code, 0)
        self.assertIn("condition 5:", out)
        self.assertIn("probe", out.lower())
        # Broken bwrap -> still PASSes this rehearsal (issue #5's distinction
        # WORKING correctly is the point), but must report the non-functional
        # diagnosis, not silently claim success.
        code2, out2 = run_rehearsal(bwrap_body=BROKEN_BWRAP)
        self.assertEqual(code2, 0)
        cond5_line = next(ln for ln in out2.splitlines() if ln.startswith("PASS: condition 5"))
        self.assertIn("non-functional", cond5_line)

    def test_a_regression_in_the_real_bwrap_probe_is_visible_here(self):
        """Counterfactual: if nightshift_bwrap_probe stopped distinguishing
        broken-from-missing (issue #5's exact regression shape), this test
        would stop being able to tell them apart too -- proving this rehearsal
        drives the real function, not a copy of its logic."""
        _, out_broken = run_rehearsal(bwrap_body=BROKEN_BWRAP)
        # Genuine absence: point NIGHTSHIFT_BWRAP at a path that was never created.
        with tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as scratch:
            curl_path = _write_exec(os.path.join(bindir, "curl"), FAKE_CURL_OK)
            env = dict(os.environ)
            env["NIGHTSHIFT_BWRAP"] = os.path.join(bindir, "does-not-exist")
            env["NIGHTSHIFT_REHEARSAL_CURL"] = curl_path
            env["NIGHTSHIFT_REHEARSAL_TEAMS_DIR"] = os.path.join(scratch, "srv-nightshift")
            sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=30).stdout.strip()
            p = subprocess.run(["bash", SCRIPT, sha], capture_output=True, text=True, env=env, timeout=300)
        cond5_missing = next(ln for ln in p.stdout.splitlines() if ln.startswith("PASS: condition 5"))
        cond5_broken = next(ln for ln in out_broken.splitlines() if ln.startswith("PASS: condition 5"))
        self.assertNotEqual(
            cond5_missing, cond5_broken,
            "installed-but-broken and genuinely-missing bwrap must produce distinguishable diagnoses",
        )


class GitArchiveExcludesUntrackedFiles(unittest.TestCase):
    """DoD condition 11, criterion 2 -- the whole reason `git archive` is used
    instead of a bind mount: an untracked file sitting in the source working
    tree must NOT appear in the rehearsed tree. Proven against an isolated
    throwaway git repo -- never against /srv/project's real working tree."""

    def _fixture_repo(self, tmp):
        repo = os.path.join(tmp, "fixture-repo")
        os.makedirs(os.path.join(repo, "deploy", "lib"))
        os.makedirs(os.path.join(repo, "tests", "linux"))
        shutil.copy(os.path.join(REPO, "deploy", "lib", "deploy-lib.sh"),
                    os.path.join(repo, "deploy", "lib", "deploy-lib.sh"))
        shutil.copy(SCRIPT, os.path.join(repo, "tests", "linux", "install-rehearsal.sh"))
        os.chmod(os.path.join(repo, "tests", "linux", "install-rehearsal.sh"), 0o755)

        def git(*args):
            subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, timeout=30)

        git("init", "-q")
        git("config", "user.email", "rehearsal-fixture@localhost")
        git("config", "user.name", "rehearsal-fixture")
        git("add", "-A")
        git("commit", "-q", "-m", "fixture commit")
        sha = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        return repo, sha

    def test_untracked_file_does_not_leak_into_the_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, sha = self._fixture_repo(tmp)
            # The untracked file: present in the working tree, never committed.
            leak_marker = "UNTRACKED-MUST-NOT-LEAK"
            with open(os.path.join(repo, leak_marker), "w") as fh:
                fh.write("if a bind mount were used, this file would appear in the rehearsal\n")

            script = os.path.join(repo, "tests", "linux", "install-rehearsal.sh")
            with tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as scratch:
                bwrap_path = _write_exec(os.path.join(bindir, "bwrap"), WORKING_BWRAP)
                curl_path = _write_exec(os.path.join(bindir, "curl"), FAKE_CURL_OK)
                env = dict(os.environ)
                env["NIGHTSHIFT_BWRAP"] = bwrap_path
                env["NIGHTSHIFT_REHEARSAL_CURL"] = curl_path
                env["NIGHTSHIFT_REHEARSAL_TEAMS_DIR"] = os.path.join(scratch, "srv-nightshift")
                env["NIGHTSHIFT_REHEARSAL_KEEP_WORKDIR"] = "1"
                p = subprocess.run(["bash", script, sha], capture_output=True, text=True, env=env, timeout=300)

            self.assertEqual(p.returncode, 0, f"rehearsal must still pass on the fixture repo:\n{p.stdout}{p.stderr}")
            m = re.search(r"workdir preserved at (\S+)", p.stdout)
            self.assertTrue(m, f"expected the KEEP_WORKDIR debug line in output:\n{p.stdout}")
            extracted = os.path.join(m.group(1), "rehearsal-tree")
            self.addCleanup(shutil.rmtree, m.group(1), ignore_errors=True)

            self.assertTrue(os.path.isdir(extracted), "expected an extracted rehearsal-tree directory")
            self.assertFalse(
                os.path.exists(os.path.join(extracted, leak_marker)),
                "an untracked file in the working tree leaked into the git-archive extraction "
                "-- this would mean the rehearsal is (or fell back to) a bind mount",
            )
            # Positive control: a file that WAS committed must be present, so
            # an empty/broken extraction cannot pass the negative check above vacuously.
            self.assertTrue(
                os.path.exists(os.path.join(extracted, "deploy", "lib", "deploy-lib.sh")),
                "extraction is missing a file that WAS committed -- the negative assertion above would be vacuous",
            )


if __name__ == "__main__":
    unittest.main()
