"""bwrap must be probed by FUNCTION, not by presence — DoD condition 5, issue #12.

On stock Ubuntu 24.04 (`kernel.apparmor_restrict_unprivileged_userns=1`, and a
bubblewrap package that ships no AppArmor profile) `bwrap` is installed and
CANNOT RUN.  Every Codex seat dies at its first tool call, and a box in exactly
that state passes preflight clean.  Reproduced on the build host:

    $ bwrap --ro-bind / / /bin/true
    bwrap: setting up uid map: Permission denied   # exit 1
    $ deploy/preflight.sh
      ok   bwrap -> /usr/bin/bwrap                 # <-- the defect

These tests drive `deploy/*.sh`, so they belong to seam B (`docs/owners.md`).

Requirement ids (DoD condition 5):
  RT-13  probe failure  -> preflight FAILs with the userns/AppArmor diagnosis
  RT-14  probe success  -> the functional probe is reported ok, no failure raised
  RT-15  no presence-only bwrap check remains anywhere in deploy/

The probe binary is injected through NIGHTSHIFT_BWRAP, the same override
convention deploy/ already uses for NIGHTSHIFT_PROVIDER and NIGHTSHIFT_GO_VERSION.
Injecting it is what makes RT-13 and RT-14 decidable on ANY host: a test that
asserted the real host's bwrap state would pass or fail for reasons that have
nothing to do with this fix.  No test here reads or writes a host path.
"""

import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(REPO, "deploy")
LIB = os.path.join(DEPLOY, "lib", "deploy-lib.sh")
PREFLIGHT = os.path.join(DEPLOY, "preflight.sh")

ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The real failure this box produces, so the fake behaves like the article.
BROKEN_BWRAP = """#!/usr/bin/env bash
echo 'bwrap: setting up uid map: Permission denied' >&2
exit 1
"""

WORKING_BWRAP = """#!/usr/bin/env bash
exit 0
"""


def _fake_bwrap(dirpath, body, name="bwrap"):
    """Write an executable stub and return its absolute path."""
    path = os.path.join(dirpath, name)
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def run_preflight(bwrap_path, drop_env=()):
    """Run the real preflight with an injected bwrap. Returns (code, clean_stdout).

    Preflight is a whole-host audit: on any given box it reports unrelated
    problems too.  That is why every assertion below is on the bwrap LINE, never
    merely on the exit code — a bare `exit != 0` assertion would pass for
    reasons that have nothing to do with bubblewrap.

    `drop_env` REMOVES names from the child environment (the `env -u NAME`
    sense), which is not the same test as setting them empty: `set -u` fires on
    an absent name and is perfectly happy with an empty one.  Seats that run
    preflight unattended (release, cron, systemd units) inherit no `USER`, so
    removal is the condition those seats actually present.
    """
    env = dict(os.environ)
    for name in drop_env:
        env.pop(name, None)
    env["NIGHTSHIFT_BWRAP"] = bwrap_path
    with tempfile.TemporaryDirectory() as projdir:
        p = subprocess.run(
            ["bash", PREFLIGHT, projdir],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
    return p.returncode, ANSI.sub("", p.stdout + p.stderr)


def bwrap_lines(output):
    return [ln.strip() for ln in output.splitlines() if "bwrap" in ln.lower() or "bubblewrap" in ln.lower()]


def require_bwrap_report(test, code, output, context=""):
    """Return the bwrap lines, or fail with what the subprocess ACTUALLY did.

    An empty bwrap section means preflight never REACHED the probe.  That is a
    different fact from "the probe ran and failed", and conflating the two cost
    this project a full incident (#121): preflight aborted at `line 33: USER:
    unbound variable`, and RT-13/RT-14 both reduced that to "preflight said
    nothing at all about bwrap" — which reads as a bubblewrap execution failure
    and sent the investigation at the sandbox instead of at the shell bug.

    So an empty section reports the exit status and the tail of the output.  The
    next person to hit an unrelated early abort gets told what happened.
    """
    lines = bwrap_lines(output)
    if not lines:
        tail = output.strip().splitlines()[-25:]
        rendered = "\n    ".join(tail) if tail else "(no output at all)"
        test.fail(
            "preflight produced no bwrap line at all, so it never reached the "
            "injected probe. This is an EARLY ABORT, not a bwrap failure — read "
            "the output below before suspecting bubblewrap."
            + (f"\n  context: {context}" if context else "")
            + f"\n  preflight exit status: {code}"
            + f"\n  preflight output (last {len(tail)} line(s)):\n    {rendered}"
        )
    return lines


def bash_lib(snippet, env=None):
    """Source deploy-lib.sh and run a snippet. Returns (code, stdout+stderr)."""
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(
        ["bash", "-c", f'. "{LIB}"; {snippet}'],
        capture_output=True, text=True, env=e, timeout=60,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def shebang_bash_manifest(root):
    """Every bash file under `root`: shebang-identified, plus sourced *.sh
    fragments (deploy/lib/deploy-lib.sh carries `# shellcheck shell=bash`
    instead of a shebang, because it is sourced and never executed)."""
    out = []
    for base, _dirs, files in os.walk(root):
        for fn in sorted(files):
            path = os.path.join(base, fn)
            try:
                with open(path, "r", errors="replace") as fh:
                    first = fh.readline()
            except OSError:
                continue
            if re.match(r"^#!.*\b(ba)?sh\b", first) or fn.endswith(".sh"):
                out.append(path)
    return out


class ProbeFunction(unittest.TestCase):
    """The probe itself: three outcomes, distinguished. Hermetic and fast."""

    def test_working_bwrap_probes_clean(self):
        with tempfile.TemporaryDirectory() as d:
            bw = _fake_bwrap(d, WORKING_BWRAP)
            code, _ = bash_lib(f'nightshift_bwrap_probe "{bw}"')
            self.assertEqual(code, 0, "a bwrap that runs must probe clean")

    def test_installed_but_unable_to_run_is_distinguished_from_missing(self):
        # This is the whole point of the bug: "installed" and "works" are two
        # different propositions and the old check conflated them.
        with tempfile.TemporaryDirectory() as d:
            broken = _fake_bwrap(d, BROKEN_BWRAP)
            broken_code, _ = bash_lib(f'nightshift_bwrap_probe "{broken}"')
            missing_code, _ = bash_lib(f'nightshift_bwrap_probe "{d}/not-installed"')
        self.assertNotEqual(broken_code, 0, "a bwrap that cannot run must not probe clean")
        self.assertNotEqual(missing_code, 0, "a missing bwrap must not probe clean")
        self.assertNotEqual(
            broken_code, missing_code,
            "installed-but-broken and missing need different exit codes: they "
            "have different remediations (AppArmor profile vs apt install)",
        )

    def test_probe_does_not_mutate_the_host(self):
        # --ro-bind / / is read-only by construction; assert we never grew a
        # writable bind or a --bind into the probe.
        with open(LIB) as fh:
            body = fh.read()
        self.assertIn("--ro-bind / / /bin/true", body)
        self.assertNotIn("--bind / /", body)

    def test_diagnosis_names_the_cause_and_the_remediation(self):
        code, out = bash_lib("nightshift_bwrap_diagnosis 1")
        self.assertEqual(code, 0)
        low = out.lower()
        for token in ("userns", "apparmor"):
            self.assertIn(token, low, f"diagnosis must name {token}: {out!r}")
        self.assertTrue(
            "blocked.md" in low or "apparmor.d" in low,
            f"diagnosis must reference the remediation: {out!r}",
        )


class RT13ProbeFailureFailsPreflight(unittest.TestCase):
    """RT-13 — probe failure -> preflight non-zero WITH the diagnosis."""

    def test_rt13_broken_bwrap_is_a_preflight_failure_not_an_ok(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run_preflight(_fake_bwrap(d, BROKEN_BWRAP))
        lines = require_bwrap_report(self, code, out, context="injected a bwrap that exits 1")

        # It must FAIL. Not ok, and — per the DoD — not warn either.
        self.assertFalse(
            any(ln.startswith("ok") for ln in lines),
            f"preflight reported a broken bwrap as ok: {lines}",
        )
        self.assertTrue(
            any(ln.startswith("FAIL") for ln in lines),
            f"a bwrap that cannot run must FAIL preflight, got: {lines}",
        )
        self.assertFalse(
            any(ln.startswith("warn") for ln in lines),
            f"the DoD requires FAIL, not warn: {lines}",
        )

        blob = " ".join(lines).lower()
        for token in ("userns", "apparmor"):
            self.assertIn(token, blob, f"diagnosis must name {token}: {lines}")
        self.assertNotEqual(code, 0, "preflight must exit non-zero when bwrap cannot run")


class RT14ProbeSuccessRaisesNothing(unittest.TestCase):
    """RT-14 — probe success -> the functional probe is reported ok, no failure."""

    def test_rt14_working_bwrap_reports_the_functional_probe_ok(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run_preflight(_fake_bwrap(d, WORKING_BWRAP))
        lines = require_bwrap_report(self, code, out, context="injected a bwrap that exits 0")

        self.assertFalse(
            any(ln.startswith("FAIL") for ln in lines),
            f"a working bwrap must raise no failure: {lines}",
        )
        # Positive evidence, and the reason this is a REGRESSION test rather
        # than a tautology: the pre-fix preflight also raises no bwrap failure
        # here — it just never ran bwrap. Requiring the line to state that the
        # probe RAN is what the old code cannot satisfy.
        ok_lines = [ln for ln in lines if ln.startswith("ok")]
        self.assertTrue(ok_lines, f"expected an ok line for bwrap: {lines}")
        self.assertTrue(
            any("probe" in ln.lower() or "runs" in ln.lower() for ln in ok_lines),
            f"the ok line must record that bwrap was EXECUTED, not merely found: {ok_lines}",
        )


class PreflightReachesTheProbeWithoutInheritedUserEnv(unittest.TestCase):
    """RT-13/RT-14's PRECONDITION: preflight must not need an inherited `USER`.

    No new RT id is allocated here on purpose — RT ids are allocated by
    `product`, and this repo has already spent a retro on two crews minting the
    same id on branches that never met (R-20). This guards the precondition of
    RT-13 and RT-14, which already have ids.

    #121: `deploy/preflight.sh` ran `ok "non-root user: $USER"` under `set -u`.
    The release seat has no `USER` entry in its environment (`id -un` correctly
    reports `nightshift`), so preflight aborted with

        deploy/preflight.sh: line 33: USER: unbound variable

    before it ever reached the injected bwrap probe, and `release` — the only
    agent permitted to write `main` (R-11) — could never observe a green suite.

    Note which branch crashed. Running as root takes the `if`, reports FAIL, and
    is entirely safe. The `else` is the SUCCESS path. This bug fires only when
    everything else about the box is correct, which is the worst possible
    trigger condition for a gate: it is invisible until it is load-bearing.
    """

    def test_preflight_reports_the_runtime_user_with_user_unset(self):
        # `env -u USER`, not `USER=`: `set -u` fires on an ABSENT name and is
        # satisfied by an empty one, so setting it empty would not reproduce.
        with tempfile.TemporaryDirectory() as d:
            code, out = run_preflight(_fake_bwrap(d, WORKING_BWRAP), drop_env=("USER",))

        self.assertNotIn(
            "unbound variable", out,
            "preflight aborted on an unbound variable with USER removed from the "
            "environment — it must derive the runtime user from the effective "
            f"uid, not from optional inherited env:\n{out}",
        )

        # The point of the regression: it must REACH the probe, not merely survive.
        lines = require_bwrap_report(
            self, code, out, context="USER removed from the environment (env -u USER)",
        )
        self.assertTrue(
            any(ln.startswith("ok") for ln in lines),
            f"a working bwrap must still probe ok with USER unset: {lines}",
        )

        # And it must still report WHO it is running as — degrading to silence
        # would satisfy the assertions above while losing the check's content.
        user_lines = [ln.strip() for ln in out.splitlines() if "non-root user" in ln]
        self.assertTrue(user_lines, f"preflight stopped reporting the runtime user:\n{out}")
        self.assertTrue(
            any(ln.split(":")[-1].strip() for ln in user_lines),
            f"the runtime-user line names nobody: {user_lines}",
        )
        expected = subprocess.run(
            ["id", "-un"], capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        self.assertTrue(
            any(expected and expected in ln for ln in user_lines),
            f"expected the effective uid's name {expected!r} in {user_lines}",
        )


class RT15NoPresenceOnlyCheckSurvives(unittest.TestCase):
    """RT-15 — no presence-only bwrap check remains in deploy/, by manifest scan."""

    #  `command -v bwrap`, `which bwrap`, `type bwrap`, `[ -x /usr/bin/bwrap ]`
    PRESENCE_ONLY = re.compile(
        r"(command\s+-v\s+bwrap"
        r"|\bwhich\s+bwrap"
        r"|\btype\s+-?[a-zA-Z]*\s*bwrap"
        r"|-[fx]\s+\S*/bwrap)"
    )

    def test_rt15_manifest_is_not_empty(self):
        # A scan over zero files would pass vacuously.
        manifest = shebang_bash_manifest(DEPLOY)
        self.assertGreaterEqual(len(manifest), 3, "shebang manifest looks wrong")
        for required in (PREFLIGHT, os.path.join(DEPLOY, "provision-ubuntu.sh"),
                         os.path.join(DEPLOY, "bootstrap.sh"), LIB):
            self.assertIn(required, manifest, f"{required} missing from the manifest")

    def test_rt15_no_presence_only_bwrap_check_in_deploy(self):
        offenders = []
        for path in shebang_bash_manifest(DEPLOY):
            with open(path, errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    code = line.split("#", 1)[0]
                    if self.PRESENCE_ONLY.search(code):
                        offenders.append(f"{os.path.relpath(path, REPO)}:{n}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "presence-only bwrap checks still present — a box where bwrap is "
            "installed but cannot run would pass these:\n  " + "\n  ".join(offenders),
        )

    def test_rt15_all_three_scripts_probe_by_function(self):
        for script in ("preflight.sh", "provision-ubuntu.sh", "bootstrap.sh"):
            with open(os.path.join(DEPLOY, script), errors="replace") as fh:
                body = fh.read()
            self.assertTrue(
                "nightshift_bwrap_probe" in body,
                f"deploy/{script} does not call nightshift_bwrap_probe — it must "
                f"probe bwrap by function, not presence (DoD condition 5)",
            )


class TrustBoundaryClaimCorrected(unittest.TestCase):
    """DoD condition 5 also requires the false, load-bearing doc claim to go.

    It is the stated reason the design chose bwrap over systemd namespace
    options, so correcting it is part of the fix, not a doc tidy.
    """

    def test_apparmor_profile_claim_is_gone(self):
        path = os.path.join(REPO, "docs", "TRUST-BOUNDARY.md")
        with open(path, errors="replace") as fh:
            body = fh.read()
        claim = "AppArmor profile on 24.04"
        self.assertFalse(
            claim in body,
            f"docs/TRUST-BOUNDARY.md still contains the false claim {claim!r}. "
            "The DoD settles this condition with a literal grep, so the phrase "
            "must not survive even inside a correction that refutes it.",
        )


if __name__ == "__main__":
    unittest.main()
