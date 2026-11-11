"""DoD condition 12 -- every preflight check enumerated, tested both ways,
actionable (docs/prd/DEFINITION-OF-DONE.md section C, issue #393).

`docs/prd/DOD-MANIFEST.json`'s `preflight_checks` array names every `bad`/
`warn` diagnostic in `deploy/preflight.sh` "as it exists at close-out" (69
entries). `tests/dod_validate.py`'s `check_d12` validates each row's shape
(id/severity/remediation, id present verbatim in the script, `bash -n`
clean) but -- as documented in its own module docstring -- does not verify
the manifest is COMPLETE (every check in the script is actually manifested).
That is this file's job (ADR-0054, decision point 2): `CompletenessGuard`
below statically scans `deploy/preflight.sh` for every `bad "..."` / `warn
"..."` call site and asserts the id set equals the manifest's id set, so a
check added to (or renamed/removed from) `preflight.sh` without a matching
manifest row fails here.

A subset of the manifest's `FAIL`-severity entries also get a genuine
positive-and-negative hermetic fixture below (`FixtureCoverage`) -- the ones
whose triggering condition is reachable via `$PROJECT_DIR`, `$HOME`, `$PATH`,
or an existing `NIGHTSHIFT_*` env override, per DoD condition 12 criterion 4.
Every manifest entry NOT covered here carries its own `fixture_exempt_reason`
(criterion 5) -- named live-host/network/systemd/gh-auth/docker dependencies,
or "same mechanism as the entry above, not independently re-tested to keep
this fixture set reviewable". Both are recorded, not silently dropped; see
ADR-0054 and the PR body for the follow-up this defers.

These tests drive `deploy/preflight.sh`, so they belong to seam B
(`docs/owners.md`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy"
PREFLIGHT = DEPLOY / "preflight.sh"
MANIFEST_PATH = REPO / "docs" / "prd" / "DOD-MANIFEST.json"

ANSI = re.compile(r"\x1b\[[0-9;]*m")

CALL_PATTERN = re.compile(r'\b(bad|warn)\s+"((?:[^"\\]|\\.)*)"')


def scan_preflight_call_sites():
    """The same mechanical scan the manifest was generated from (ADR-0054):
    every literal `bad "..."` / `warn "..."` call site outside a `#` comment,
    keyed by the EXACT literal text between the quotes (so it is always a
    genuine substring of the file, matching `check_d12`'s own requirement).
    A dynamic message built entirely from a command substitution (the bwrap
    diagnosis line) cannot be scanned this way; it is a documented, single
    exception (see NON_LITERAL_IDS below).
    """
    text = PREFLIGHT.read_text(encoding="utf-8")
    ids = {}
    for lineno, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith("#"):
            continue
        for m in CALL_PATTERN.finditer(line):
            severity = "FAIL" if m.group(1) == "bad" else "warn"
            literal = line[m.start(2):m.end(2)]
            if literal.replace('\\"', '"').startswith("$("):
                continue  # dynamic message; see NON_LITERAL_IDS
            ids.setdefault(literal, {"severity": severity, "line": lineno})
    return ids


# The one check whose message is built at runtime (nightshift_bwrap_diagnosis()),
# so no literal string sits at its call site for the scanner above to find.
# `nightshift_bwrap_diagnosis` itself IS a literal substring of preflight.sh
# (both the success and failure branches call it), and that is the id the
# manifest uses for this row instead.
NON_LITERAL_IDS = {"nightshift_bwrap_diagnosis"}


def load_manifest():
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class CompletenessGuard(unittest.TestCase):
    """DoD condition 12 criterion 3 -- the manifest cannot silently fall
    behind the script. This is exactly the gap `check_d12` names as not its
    own job.

    Known, documented limitation of the regex scan (in the same spirit as
    `tests/dod_validate.py`'s own doctrine -- a limitation must be written
    down, not assumed away): it is not a shell parser, so it would not catch
    a `bad`/`warn` call written across multiple lines, or one whose quotes
    contain an un-escaped literal `"` that breaks the pattern. Verified
    against the actual file as of this test: every `bad`/`warn` call site in
    `deploy/preflight.sh` is single-line and outside a comment.
    """

    def test_scan_is_not_vacuous(self):
        scanned = scan_preflight_call_sites()
        self.assertGreaterEqual(len(scanned), 60, "the call-site scan looks wrong (too few checks found)")

    def test_every_scanned_call_site_is_single_line_and_uncommented(self):
        # Documents and verifies the scan's own known limitation, rather than
        # assuming it away: every `bad(`/`warn(` call resolves entirely on
        # one line, and none live inside a `#` comment.
        text = PREFLIGHT.read_text(encoding="utf-8")
        offenders = []
        for lineno, line in enumerate(text.split("\n"), start=1):
            code = line
            if re.search(r'\b(bad|warn)\s+"[^"]*$', code) and not code.lstrip().startswith("#"):
                offenders.append(f"{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], f"a bad/warn call appears to span multiple lines, which the scan cannot see: {offenders}")

    def test_manifest_ids_match_the_scanned_call_sites_exactly(self):
        scanned_ids = set(scan_preflight_call_sites().keys()) | NON_LITERAL_IDS
        manifest = load_manifest()
        section = manifest.get("preflight_checks") or []
        manifest_ids = {entry["id"] for entry in section}

        missing_from_manifest = scanned_ids - manifest_ids
        extra_in_manifest = manifest_ids - scanned_ids
        self.assertEqual(
            missing_from_manifest, set(),
            f"deploy/preflight.sh has check(s) with no matching docs/prd/DOD-MANIFEST.json "
            f"preflight_checks row -- this is exactly how issue #5 happened: "
            f"{sorted(missing_from_manifest)}",
        )
        self.assertEqual(
            extra_in_manifest, set(),
            f"docs/prd/DOD-MANIFEST.json names a preflight check that no longer exists in "
            f"deploy/preflight.sh -- stale manifest row(s): {sorted(extra_in_manifest)}",
        )

    def test_manifest_severity_matches_the_scanned_call_kind(self):
        scanned = scan_preflight_call_sites()
        manifest_by_id = {e["id"]: e for e in load_manifest().get("preflight_checks", [])}
        mismatches = []
        for literal, info in scanned.items():
            entry = manifest_by_id.get(literal)
            if entry and entry["severity"] != info["severity"]:
                mismatches.append((literal[:60], entry["severity"], info["severity"]))
        self.assertEqual(mismatches, [], f"severity mismatches (manifest vs actual bad/warn call): {mismatches}")

    def test_every_entry_has_id_severity_and_remediation(self):
        for entry in load_manifest().get("preflight_checks", []):
            for key in ("id", "severity", "remediation"):
                self.assertIn(key, entry, f"entry missing {key!r}: {entry}")
            self.assertIn(entry["severity"], ("FAIL", "warn"))
            self.assertTrue(entry["remediation"].strip())
            self.assertNotIn("properly", entry["remediation"].lower())
            self.assertNotIn("correctly", entry["remediation"].lower())

    def test_every_fail_entry_either_has_a_fixture_or_a_named_exempt_reason(self):
        # Criterion 4/5: every FAIL-severity row is EITHER covered by
        # FixtureCoverage below (see TESTED_IDS) OR carries a
        # `fixture_exempt_reason`. Nothing may be silently uncovered.
        for entry in load_manifest().get("preflight_checks", []):
            if entry["severity"] != "FAIL":
                continue
            covered = entry["id"] in TESTED_IDS
            exempted = bool(entry.get("fixture_exempt_reason"))
            self.assertTrue(
                covered or exempted,
                f"FAIL entry {entry['id'][:60]!r} has neither a fixture (TESTED_IDS) "
                f"nor a fixture_exempt_reason",
            )


class BashSyntaxAndExitCode(unittest.TestCase):
    """DoD condition 12's `Also:` clause."""

    def test_bash_n(self):
        p = subprocess.run(["bash", "-n", str(PREFLIGHT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, f"bash -n deploy/preflight.sh failed: {p.stderr}")

    def test_exits_zero_on_a_clean_environment_and_nonzero_on_one_forced_fail(self):
        # "Clean" here means "no FAIL branch printed", not "every host fact
        # happens to be true" -- preflight.sh is a whole-host audit and will
        # always print SOME warn on an arbitrary CI box. What must hold is the
        # documented invariant: exit 0 iff zero FAIL lines were printed.
        code, out = run_preflight()
        fail_lines = [ln for ln in out.splitlines() if ln.strip().startswith("FAIL")]
        if fail_lines:
            self.assertNotEqual(code, 0, f"printed FAIL line(s) but exited 0:\n{fail_lines}")
        else:
            self.assertEqual(code, 0, f"printed no FAIL line but exited non-zero:\n{out[-2000:]}")

        # Force one deterministic FAIL (missing .omni/capacity.json field) and
        # confirm the exit code follows it.
        code2, out2 = run_preflight(project_files={".omni/capacity.json": "{}"})
        self.assertIn("FAIL", out2)
        self.assertNotEqual(code2, 0, "a forced FAIL branch did not produce a non-zero exit")


# ---------------------------------------------------------------------------
# Hermetic fixture harness
# ---------------------------------------------------------------------------

# Real external tools deploy/preflight.sh invokes by bare name. Reconstructed
# into an isolated bin/ directory per run so a specific tool can be hidden
# from `command -v` (real removal from PATH, not merely shadowed) without
# breaking every OTHER check in the same whole-host audit.
REQUIRED_TOOLS = [
    "bash", "sh", "id", "dirname", "readlink", "cat", "grep", "sed", "cut",
    "tr", "wc", "mktemp", "python3", "jq", "git", "gh", "curl", "ss", "free",
    "df", "systemctl", "docker", "loginctl", "tailscale", "uname", "head",
    "basename", "mkdir", "rm", "ls", "date", "sort", "chmod", "xargs",
    "nightshift-ctl", "rtk", "go", "cargo", "rustc", "floci", "node", "tmux",
    "omnigent", "claude", "codex", "awk", "ps", "pgrep", "cmp",
]


def _write_exec(path, body):
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _build_bin_dir(bindir, hide=(), stub=None):
    """Symlink every real REQUIRED_TOOLS entry into bindir, except `hide`.
    `stub` is an optional {name: script_body} overlay written AFTER the
    symlinks (so a stub always wins over the real binary of the same name)."""
    hide = set(hide)
    for name in REQUIRED_TOOLS:
        if name in hide:
            continue
        real = shutil.which(name)
        if real:
            try:
                os.symlink(real, os.path.join(bindir, name))
            except FileExistsError:
                pass
    for name, body in (stub or {}).items():
        target = os.path.join(bindir, name)
        if os.path.lexists(target):
            os.remove(target)
        _write_exec(target, body)
    return bindir


def run_preflight(project_files=None, home_files=None, hide=(), stub=None, env=None,
                  server_env=None):
    """Run the REAL deploy/preflight.sh with an isolated PROJECT_DIR, HOME,
    and (optionally) a reconstructed PATH. Returns (exit_code, clean_output).
    `server_env` is written to a temp file that NIGHTSHIFT_SERVER_ENV names,
    standing in for the root-owned /etc/nightshift/server.env.
    """
    if server_env is not None:
        home_files = dict(home_files or {}, **{"etc-nightshift/server.env": server_env})
        env = dict(env or {}, NIGHTSHIFT_SERVER_ENV="@HOME@/etc-nightshift/server.env")
    with tempfile.TemporaryDirectory() as project_dir, \
         tempfile.TemporaryDirectory() as home_dir, \
         tempfile.TemporaryDirectory() as bindir:
        for rel, content in (project_files or {}).items():
            p = Path(project_dir) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        for rel, content in (home_files or {}).items():
            p = Path(home_dir) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        run_env = dict(os.environ)
        if hide or stub:
            _build_bin_dir(bindir, hide=hide, stub=stub)
            run_env["PATH"] = bindir
        run_env["HOME"] = home_dir
        if env:
            run_env.update({k: v.replace("@HOME@", home_dir) for k, v in env.items()})

        p = subprocess.run(
            ["bash", str(PREFLIGHT), project_dir],
            capture_output=True, text=True, env=run_env, timeout=300,
        )
        return p.returncode, ANSI.sub("", p.stdout + p.stderr)


def lines_for(output, needle):
    return [ln.strip() for ln in output.splitlines() if needle in ln]


# ---------------------------------------------------------------------------
# Curated fixture coverage -- one class per mechanism, both directions.
# TESTED_IDS must list every manifest id these fixtures actually exercise,
# so CompletenessGuard.test_every_fail_entry_either_has_a_fixture_or_a_named_exempt_reason
# can cross-check coverage claims against reality.
# ---------------------------------------------------------------------------

TESTED_IDS = {
    "$b missing",
    "nightshift_bwrap_diagnosis",
    "nightshift-ctl not on PATH — worker semaphore, claims and leases are all prompt-only (re-run bootstrap.sh)",
    "nightshift-ctl selftest failed — SQLite too old or filesystem refuses WAL",
    ".omni/control.db missing — the semaphore FAILS CLOSED, so EVERY worker dispatch will be DENIED (run bin/nightshift-init.sh)",
    "worker admission cutover needs migration/quiescence or reviewed legacy reconciliation",
    "fail-open escape hatch set: ${fail_open_hits%;} — every *_FAIL_OPEN is forbidden for a managed run",
    "server.env has no PYTHONPATH — systemd-started server cannot load the custom policies (as root: deploy/install-root-config.sh)",
    "server environment $SERVER_ENV missing — omnigent-server will not start; as root run deploy/install-root-config.sh --user $runtime_user (bootstrap prints the full line)",
    "$anchor_count fence-configuration path(s) writable by $runtime_user, first: $anchor_first — a worker can change what the next restart loads; as root run deploy/install-root-config.sh (--own-bundle), or run a root-sealed release",
    "user units load configuration other than $SERVER_ENV:$unit_problems — the runtime user can edit those; re-run bootstrap.sh and delete the drop-in directory",
    '~/.codex/config.toml missing approval_policy=\\"never\\" — Codex workers run yolo:false and WILL stall on their first approval (re-run bootstrap.sh)',
    "~/.aws/credentials exists — static AWS keys are readable by workers; use an instance role if the project needs AWS (docs/TRUST-BOUNDARY.md)",
    "~/.aws/credentials exists — static cloud keys are readable by workers; remove them unless the project itself needs them (docs/TRUST-BOUNDARY.md)",
    ".omni/tracker.json missing — run bin/nightshift-tracker-init.sh in the repo",
    "$f missing (run bin/nightshift-init.sh)",
    "brief.md is still the untouched template — write your brief and delete the TEMPLATE line",
    "brief.md has only ${words} words of actual content — discovery needs something to work with",
    "no definition of done yet — the program's first act is the discovery skill",
    ".worktrees not gitignored",
    ".omni/capacity.json has no max_concurrent_workers",
    "running release has no bin/nightshift-ctl: $release_ctl",
    "installed nightshift-ctl is missing subcommands the charters call:$missing",
    "systemd units pin a release the server is not running — a restart silently changes capability:$unit_drift",
    "deployed policy differs from the running release's copy — dispatch admission may not match the reviewed code",
    "state-backup remote $backup_slug is not a private repository — the snapshot carries the control dump and incidents; point nightshift-state at a private repository",
    "runtime user $runtime_user can sudo without a password — every worker runs as this user, so that is root for any worker; as root, remove its NOPASSWD sudoers entry or run Nightshift as a dedicated user (deploy/providers/aws/README.md)",
    "IMDSv1 answers on this instance — any worker can read instance-role credentials with one unauthenticated GET; require IMDSv2 with hop limit 1 (deploy/providers/aws/README.md)",
    "OMNIGENT_DATABASE_URI in server.env carries the database password — it becomes the server's argv, readable by every local user through ps; move the password to a PGPASSFILE (re-run bootstrap.sh)",
    "a running process has the database password in its --database-uri argument — restart it from a server.env whose URI has no password",
}


class BinariesLoopMissingBinary(unittest.TestCase):
    """PATH-hermetic: `deploy/preflight.sh`'s `for b in omnigent claude codex
    node tmux git gh jq` loop (id `$b missing`)."""

    def test_missing_binary_is_a_fail(self):
        code, out = run_preflight(hide={"jq"})
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, "jq missing"), f"expected a FAIL line for jq: {out}")

    def test_present_binary_is_ok(self):
        code, out = run_preflight()  # full reconstructed PATH, nothing hidden
        ok_lines = lines_for(out, "jq ->")
        self.assertTrue(ok_lines, f"expected an ok line for jq: {out}")


class BwrapFunctionalProbe(unittest.TestCase):
    """DoD condition 12's bwrap row (id `nightshift_bwrap_diagnosis`,
    NON_LITERAL_IDS above) -- the presence-vs-function distinction issue #5
    exists to prevent regressing, driven through the REAL
    `deploy/preflight.sh` (not a reimplementation), via the same
    `NIGHTSHIFT_BWRAP` override convention `tests/test_bwrap_probe.py`
    already uses for the underlying `nightshift_bwrap_probe`/
    `nightshift_bwrap_diagnosis` functions in `deploy/lib/deploy-lib.sh`.

    PM's acceptance pass on PR #404 found this row's `TESTED_IDS` claim
    (line ~291 above) had nothing in THIS file backing it -- the real
    coverage existed only in the separate `tests/test_bwrap_probe.py`,
    which `CompletenessGuard` has no way to know about. This class is that
    fix: `nightshift_bwrap_diagnosis` is not asserted true here, it is
    proven, end to end through `deploy/preflight.sh` itself.
    """

    # The real failure stock Ubuntu 24.04 produces (see
    # tests/test_bwrap_probe.py's module docstring) -- bwrap present,
    # `command -v` succeeds, but the namespace call itself is refused.
    BROKEN_BODY = "#!/usr/bin/env bash\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n"
    WORKING_BODY = "#!/usr/bin/env bash\nexit 0\n"

    def _stub(self, tmpdir, body, name="bwrap"):
        path = os.path.join(tmpdir, name)
        _write_exec(path, body)
        return path

    def test_functional_bwrap_is_ok_not_presence_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bw = self._stub(tmpdir, self.WORKING_BODY)
            code, out = run_preflight(env={"NIGHTSHIFT_BWRAP": bw})
        self.assertTrue(
            lines_for(out, "bwrap functional probe: 'bwrap --ro-bind / / /bin/true' exits 0"),
            f"expected the functional-probe ok line: {out}",
        )
        self.assertFalse(lines_for(out, "INSTALLED BUT CANNOT RUN"))

    def test_present_but_non_functional_bwrap_is_a_fail_with_the_function_diagnosis(self):
        # The exact defect issue #5 named: bwrap is present (`command -v`
        # succeeds) but the real namespace call fails -- a presence-only
        # check would report this box clean.
        with tempfile.TemporaryDirectory() as tmpdir:
            bw = self._stub(tmpdir, self.BROKEN_BODY)
            code, out = run_preflight(env={"NIGHTSHIFT_BWRAP": bw})
        self.assertNotEqual(code, 0)
        fail_lines = lines_for(out, "INSTALLED BUT CANNOT RUN")
        self.assertTrue(fail_lines, f"expected the function-diagnosis FAIL line: {out}")
        self.assertTrue(
            any("apparmor_restrict_unprivileged_userns" in ln for ln in fail_lines),
            f"diagnosis must name the actual cause, not just fail silently: {fail_lines}",
        )

    def test_missing_bwrap_binary_is_a_fail_with_the_missing_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = os.path.join(tmpdir, "no-such-bwrap-binary")
            code, out = run_preflight(env={"NIGHTSHIFT_BWRAP": missing})
        self.assertNotEqual(code, 0)
        self.assertTrue(
            lines_for(out, "bwrap missing"),
            f"expected the missing-binary FAIL line (distinct wording from the non-functional case): {out}",
        )
        self.assertFalse(lines_for(out, "INSTALLED BUT CANNOT RUN"))


class NightshiftCtlOnPathAndSelftest(unittest.TestCase):
    """`deploy/preflight.sh` hardcodes `/tmp/nightshift-ctl-preflight.db` (not
    parameterized), and `nightshift-ctl ... selftest` never creates a control
    plane that does not already exist (`bin/nightshift-ctl`: `connect(args.db,
    create=(args.group == "init"))` -- `selftest` is not `init`). So the
    "selftest passing" branch is only reachable when that exact path already
    holds a valid, initialised control plane -- exactly the state a real
    `nightshift-ctl init` leaves behind. This fixture recreates that real
    prerequisite instead of asserting an unreachable state."""

    FIXED_DB = "/tmp/nightshift-ctl-preflight.db"

    def setUp(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(self.FIXED_DB + suffix)
            except FileNotFoundError:
                pass

    tearDown = setUp

    def test_absent_from_path_is_a_fail(self):
        code, out = run_preflight(hide={"nightshift-ctl"})
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, "nightshift-ctl not on PATH"))

    def test_present_and_selftest_passing_is_ok(self):
        # Repo-relative, not shutil.which("nightshift-ctl"): CI's stdlib-only
        # 'control-plane tests' workflow never puts bin/ on PATH, so
        # shutil.which returns None there while resolving fine locally on a
        # dev box with bin/ on PATH -- the exact CI-vs-local skew every other
        # fixture in this file (and tests/ctlutil.py, tests/test_fencing.py)
        # avoids by resolving the controller from REPO instead.
        ctl = REPO / "bin" / "nightshift-ctl"
        self.assertTrue(ctl.is_file(), f"expected the real controller at {ctl}")
        subprocess.run([str(ctl), "--db", self.FIXED_DB, "init"], check=True, capture_output=True, timeout=60)
        # deploy/preflight.sh itself locates the CLI via `command -v
        # nightshift-ctl` on ITS OWN PATH (a bare-name lookup, deploy/
        # preflight.sh:114) -- resolving `ctl` above does not change that.
        # run_preflight() with no hide/stub passes the ambient PATH through
        # unmodified, which is exactly what CI lacks, so give it one that
        # has REPO/bin first without touching the process-wide PATH used by
        # every other fixture in this file.
        ctl_path_env = {"PATH": f"{ctl.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
        code, out = run_preflight(env=ctl_path_env)
        self.assertTrue(lines_for(out, "nightshift-ctl selftest"))
        self.assertFalse(lines_for(out, "nightshift-ctl selftest failed"))

    def test_present_but_selftest_failing_is_a_fail(self):
        # No prior `init` at the fixed path -> selftest genuinely fails
        # (control_plane_missing), which is exactly the FAIL branch this
        # entry documents.
        stub = {"nightshift-ctl": "#!/usr/bin/env bash\nif [ \"$1\" = --db ]; then exit 1; fi\necho ok\n"}
        code, out = run_preflight(stub=stub)
        self.assertTrue(lines_for(out, "nightshift-ctl selftest failed"))
        self.assertNotEqual(code, 0)


class ControlDbAndAdmissionSchema(unittest.TestCase):
    def test_control_db_missing_is_a_fail(self):
        code, out = run_preflight()
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, ".omni/control.db missing"))

    def test_control_db_present_is_ok(self):
        with tempfile.TemporaryDirectory() as scratch:
            db_path = os.path.join(scratch, "control.db")
            cx = sqlite3.connect(db_path)
            cx.executescript(
                "CREATE TABLE task_admissions (task_id TEXT);"
                "CREATE TABLE worker_slots (id INTEGER, state TEXT, phase TEXT);"
                "CREATE TABLE tasks (id TEXT, state TEXT, pr_url TEXT);"
            )
            cx.commit()
            cx.close()
            with open(db_path, "rb") as fh:
                blob = fh.read()
        self._write_binary_and_run(blob)

    def _write_binary_and_run(self, blob):
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as home_dir:
            (Path(project_dir) / ".omni").mkdir(parents=True, exist_ok=True)
            (Path(project_dir) / ".omni" / "control.db").write_bytes(blob)
            env = dict(os.environ)
            env["HOME"] = home_dir
            p = subprocess.run(["bash", str(PREFLIGHT), project_dir], capture_output=True, text=True, env=env, timeout=300)
            out = ANSI.sub("", p.stdout + p.stderr)
            self.assertTrue(lines_for(out, "ADR-0033 schema present"), f"expected the schema-present ok line: {out}")
            self.assertFalse(lines_for(out, "worker admission cutover needs migration"))

    def test_legacy_schema_is_a_fail(self):
        with tempfile.TemporaryDirectory() as scratch:
            db_path = os.path.join(scratch, "control.db")
            cx = sqlite3.connect(db_path)
            # No task_admissions table at all -> the ADR-0033 schema check must fail.
            cx.executescript("CREATE TABLE worker_slots (id INTEGER, state TEXT, phase TEXT);")
            cx.commit()
            cx.close()
            with open(db_path, "rb") as fh:
                blob = fh.read()
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as home_dir:
            (Path(project_dir) / ".omni").mkdir(parents=True, exist_ok=True)
            (Path(project_dir) / ".omni" / "control.db").write_bytes(blob)
            env = dict(os.environ)
            env["HOME"] = home_dir
            p = subprocess.run(["bash", str(PREFLIGHT), project_dir], capture_output=True, text=True, env=env, timeout=300)
            out = ANSI.sub("", p.stdout + p.stderr)
            self.assertTrue(lines_for(out, "worker admission cutover needs migration"))
            self.assertNotEqual(p.returncode, 0)


class FailOpenForbidden(unittest.TestCase):
    """Every *_FAIL_OPEN, not only the semaphore's: the authority fence's
    NIGHTSHIFT_AUTHORITY_FAIL_OPEN was neither unset nor checked."""

    def _hits(self, out):
        return lines_for(out, "fail-open escape hatch set:")

    def test_semaphore_env_var_set_is_a_fail(self):
        code, out = run_preflight(env={"NIGHTSHIFT_SEMAPHORE_FAIL_OPEN": "1"})
        self.assertNotEqual(code, 0)
        self.assertIn("NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1 (environment)", self._hits(out)[0])

    def test_authority_env_var_set_is_a_fail(self):
        code, out = run_preflight(env={"NIGHTSHIFT_AUTHORITY_FAIL_OPEN": "1"})
        self.assertNotEqual(code, 0)
        self.assertIn("NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1", self._hits(out)[0])

    def test_any_fail_open_in_the_server_env_or_a_unit_drop_in_is_a_fail(self):
        for rel, text in (
            ("etc-nightshift/server.env", "export NIGHTSHIFT_AUTHORITY_FAIL_OPEN='1'\n"),
            (".config/systemd/user/omnigent-server.service.d/x.conf",
             "[Service]\nEnvironment=\"SOME_NEW_FENCE_FAIL_OPEN=1\"\n"),
        ):
            with self.subTest(rel=rel):
                code, out = run_preflight(
                    home_files={rel: text},
                    env={"NIGHTSHIFT_SERVER_ENV": "@HOME@/etc-nightshift/server.env"})
                self.assertNotEqual(code, 0)
                self.assertTrue(self._hits(out), out)

    def test_a_value_other_than_1_is_inert_and_not_reported(self):
        _, out = run_preflight(env={"NIGHTSHIFT_AUTHORITY_FAIL_OPEN": "0"})
        self.assertFalse(self._hits(out))

    def test_none_set_is_ok(self):
        code, out = run_preflight()
        self.assertFalse(self._hits(out))
        self.assertTrue(lines_for(out, "no *_FAIL_OPEN escape hatch set"))


class ServerEnvPythonPath(unittest.TestCase):
    def test_missing_pythonpath_is_a_fail(self):
        code, out = run_preflight(server_env="SOME_OTHER_VAR=1\n")
        self.assertTrue(lines_for(out, "server.env has no PYTHONPATH"))
        self.assertNotEqual(code, 0)

    def test_present_pythonpath_is_ok(self):
        code, out = run_preflight(server_env="PYTHONPATH=/some/path\n")
        self.assertTrue(lines_for(out, "PYTHONPATH in server.env"))
        self.assertFalse(lines_for(out, "server.env has no PYTHONPATH"))


class CodexApprovalPolicy(unittest.TestCase):
    def test_missing_never_policy_is_a_fail(self):
        code, out = run_preflight(home_files={".codex/config.toml": "sandbox_mode = \"workspace-write\"\n"})
        # deploy/preflight.sh's source has `\"never\"` (bash-escaped); the
        # PRINTED output has the backslashes consumed by bash quoting, so the
        # runtime text reads `approval_policy="never"` with real quotes.
        self.assertTrue(lines_for(out, 'missing approval_policy="never"'))
        self.assertNotEqual(code, 0)

    def test_present_never_policy_is_ok(self):
        code, out = run_preflight(home_files={".codex/config.toml": 'approval_policy = "never"\n'})
        self.assertTrue(lines_for(out, "approval_policy=never"))
        self.assertFalse(lines_for(out, 'missing approval_policy'))


class AwsCredentialsWarn(unittest.TestCase):
    def test_credentials_present_is_a_warn(self):
        code, out = run_preflight(home_files={".aws/credentials": "[default]\naws_access_key_id=x\n"})
        self.assertTrue(lines_for(out, "~/.aws/credentials exists"))

    def test_credentials_absent_is_ok(self):
        code, out = run_preflight()
        self.assertTrue(lines_for(out, "no static cloud credentials"))
        self.assertFalse(lines_for(out, "~/.aws/credentials exists"))


class TrackerJsonMissing(unittest.TestCase):
    def test_missing_is_a_fail(self):
        code, out = run_preflight()
        self.assertTrue(lines_for(out, ".omni/tracker.json missing"))
        self.assertNotEqual(code, 0)

    def test_present_advances_past_the_missing_check(self):
        code, out = run_preflight(project_files={".omni/tracker.json": json.dumps({"project_number": 7})})
        self.assertFalse(lines_for(out, ".omni/tracker.json missing"))


class ProjectScaffoldingFiles(unittest.TestCase):
    SCAFFOLD = [".omni/brief.md", ".omni/ledger.md", ".omni/capacity.json", "docs/owners.md", "docs/decisions"]

    def test_missing_scaffold_files_are_a_fail(self):
        code, out = run_preflight()
        self.assertNotEqual(code, 0)
        for f in self.SCAFFOLD:
            self.assertTrue(lines_for(out, f"{f} missing"), f"expected a FAIL for missing {f}")

    def test_present_scaffold_files_are_ok(self):
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as home_dir:
            for f in self.SCAFFOLD:
                if f == "docs/decisions":
                    (Path(project_dir) / f).mkdir(parents=True, exist_ok=True)
                else:
                    p = Path(project_dir) / f
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("x", encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = home_dir
            p = subprocess.run(["bash", str(PREFLIGHT), project_dir], capture_output=True, text=True, env=env, timeout=300)
            out = ANSI.sub("", p.stdout + p.stderr)
        for f in self.SCAFFOLD:
            self.assertFalse(lines_for(out, f"{f} missing"), f"{f} still reported missing")


class BriefMdChecks(unittest.TestCase):
    TEMPLATE = "<!-- TEMPLATE: replace this -->\nsome content\n"

    def test_template_untouched_is_a_fail(self):
        code, out = run_preflight(project_files={".omni/brief.md": self.TEMPLATE})
        self.assertTrue(lines_for(out, "brief.md is still the untouched template"))
        self.assertNotEqual(code, 0)

    def test_too_few_words_is_a_fail(self):
        code, out = run_preflight(project_files={".omni/brief.md": "only a few words here\n"})
        self.assertTrue(lines_for(out, "words of actual content"))
        self.assertNotEqual(code, 0)

    def test_enough_real_content_is_ok(self):
        content = " ".join(f"word{i}" for i in range(30)) + "\n"
        code, out = run_preflight(project_files={".omni/brief.md": content})
        self.assertTrue(lines_for(out, "brief.md written"))
        self.assertFalse(lines_for(out, "words of actual content"))


class DefinitionOfDoneWarn(unittest.TestCase):
    def test_absent_is_a_warn(self):
        code, out = run_preflight()
        self.assertTrue(lines_for(out, "no definition of done yet"))

    def test_present_is_ok(self):
        code, out = run_preflight(project_files={"docs/prd/DEFINITION-OF-DONE.md": "1. one\n2. two\n"})
        self.assertTrue(lines_for(out, "definition of done exists"))
        self.assertFalse(lines_for(out, "no definition of done yet"))


class WorktreesGitignoreWarn(unittest.TestCase):
    def test_missing_line_is_a_warn(self):
        code, out = run_preflight(project_files={".gitignore": "node_modules/\n"})
        self.assertTrue(lines_for(out, ".worktrees not excluded"))

    def test_present_line_is_ok(self):
        code, out = run_preflight(project_files={".gitignore": ".worktrees/\n"})
        self.assertTrue(lines_for(out, ".worktrees excluded from git"))
        self.assertFalse(lines_for(out, ".worktrees not excluded"))

    def test_the_rule_in_info_exclude_also_counts(self):
        """#744: `nightshift-init.sh` now writes to `.git/info/exclude`, because
        `.gitignore` is tracked and belongs to the repository's owner. A check
        that reads only `.gitignore` warns about every correctly initialised
        repository -- a warning that is always wrong is one people learn to
        ignore."""
        code, out = run_preflight(
            project_files={".gitignore": "node_modules/\n",
                           ".git/info/exclude": ".worktrees/\n"})
        self.assertTrue(lines_for(out, ".worktrees excluded from git"))
        self.assertFalse(lines_for(out, ".worktrees not excluded"))


class CapacityJsonBudget(unittest.TestCase):
    def test_missing_field_is_a_fail(self):
        code, out = run_preflight(project_files={".omni/capacity.json": "{}"})
        self.assertTrue(lines_for(out, ".omni/capacity.json has no max_concurrent_workers"))
        self.assertNotEqual(code, 0)

    def test_present_field_is_ok(self):
        code, out = run_preflight(project_files={".omni/capacity.json": json.dumps({"max_concurrent_workers": 5})})
        self.assertTrue(lines_for(out, "global worker budget: 5"))
        self.assertFalse(lines_for(out, "has no max_concurrent_workers"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Control-plane version drift (issue #495). On 2026-09-01 this host ran four
# copies of nightshift-ctl at once and nothing compared them, so `authority`
# and `bookkeeping` were simply absent from the copy agents invoked. These
# fixtures force each drift shape the check is meant to catch, and the clean
# case proves the check is not merely always-failing.
# ---------------------------------------------------------------------------

SHA_RUNNING = "a" * 40
SHA_OTHER = "b" * 40

CTL_FULL = 'print("usage: nightshift-ctl [-h]")\nprint("   {init,slot,task,review}")\n'
CTL_THIN = 'print("usage: nightshift-ctl [-h]")\nprint("   {init,slot,task}")\n'


def _release(root, sha, ctl_src=CTL_FULL, policy="POLICY-RELEASE\n", charter="nightshift-ctl review consolidate\n"):
    d = Path(root) / sha / "checkout"
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "deploy" / "policies").mkdir(parents=True, exist_ok=True)
    (d / "program").mkdir(parents=True, exist_ok=True)
    if ctl_src is not None:
        (d / "bin" / "nightshift-ctl").write_text(ctl_src, encoding="utf-8")
    (d / "deploy" / "policies" / "omnipods_policies.py").write_text(policy, encoding="utf-8")
    (d / "program" / "config.yaml").write_text(charter, encoding="utf-8")
    (d / "AGENTS.md").write_text("", encoding="utf-8")
    return d


def _run_drift(releases_root, teams_dir, home_files=None, project_files=None):
    """Drive the real preflight with a fabricated releases root and a `ps`
    stub that reports the server running from SHA_RUNNING."""
    ps_stub = (
        "#!/usr/bin/env bash\n"
        f"echo 'omnigent server --agent {releases_root}/{SHA_RUNNING}/checkout/program'\n"
    )
    return run_preflight(
        project_files=project_files,
        home_files=home_files,
        stub={"ps": ps_stub},
        env={"NIGHTSHIFT_RELEASES_ROOT": str(releases_root), "TEAMS_DIR": str(teams_dir)},
    )


class ControlPlaneDriftChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "releases"
        self.teams = Path(self._tmp.name) / "teams"
        (self.teams / "deploy" / "policies").mkdir(parents=True)
        (self.teams / "program").mkdir(parents=True)
        (self.teams / "AGENTS.md").write_text("", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def _teams_policy(self, text):
        (self.teams / "deploy" / "policies" / "omnipods_policies.py").write_text(text, encoding="utf-8")

    def test_clean_host_reports_no_drift(self):
        _release(self.root, SHA_RUNNING)
        self._teams_policy("POLICY-RELEASE\n")
        _, out = _run_drift(self.root, self.teams,
                            project_files={"bin/nightshift-ctl": CTL_FULL})
        self.assertEqual(lines_for(out, "systemd units pin a release"), [])
        self.assertEqual(lines_for(out, "deployed policy differs"), [])
        self.assertEqual(lines_for(out, "missing subcommands the charters call"), [])
        self.assertTrue(lines_for(out, "running release:"), "the drift section did not run at all")

    def test_release_without_a_control_plane_binary_is_a_fail(self):
        _release(self.root, SHA_RUNNING, ctl_src=None)
        self._teams_policy("POLICY-RELEASE\n")
        _, out = _run_drift(self.root, self.teams)
        self.assertTrue(lines_for(out, "running release has no bin/nightshift-ctl"), out)

    def test_charters_calling_a_subcommand_the_binary_lacks_is_a_fail(self):
        # release ships a THIN cli, but its own charters call `review`
        _release(self.root, SHA_RUNNING, ctl_src=CTL_THIN)
        self._teams_policy("POLICY-RELEASE\n")
        _, out = _run_drift(self.root, self.teams,
                            project_files={"bin/nightshift-ctl": CTL_FULL})
        hits = lines_for(out, "missing subcommands the charters call")
        self.assertTrue(hits, out)
        self.assertIn("review", hits[0])

    def test_unit_pinning_another_release_is_a_fail(self):
        _release(self.root, SHA_RUNNING)
        _release(self.root, SHA_OTHER)
        self._teams_policy("POLICY-RELEASE\n")
        unit = f"[Service]\nEnvironment=PATH={self.root}/{SHA_OTHER}/checkout/bin\n"
        _, out = _run_drift(self.root, self.teams,
                            home_files={".config/systemd/user/omnigent-server.service": unit},
                            project_files={"bin/nightshift-ctl": CTL_FULL})
        self.assertTrue(lines_for(out, "systemd units pin a release the server is not running"), out)

    def test_deployed_policy_differing_from_the_release_is_a_fail(self):
        _release(self.root, SHA_RUNNING, policy="POLICY-RELEASE\n")
        self._teams_policy("POLICY-HAND-EDITED\n")
        _, out = _run_drift(self.root, self.teams,
                            project_files={"bin/nightshift-ctl": CTL_FULL})
        self.assertTrue(lines_for(out, "deployed policy differs from the running release"), out)


# ---------------------------------------------------------------------------
# Audit B4: the state snapshot goes only to an explicit, private remote.
# ---------------------------------------------------------------------------

def _repo_with_backup_remote(url):
    """A minimal on-disk Git repository whose only remote is `url`."""
    return {
        ".git/HEAD": "ref: refs/heads/main\n",
        ".git/config": ("[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
                        f"[remote \"nightshift-state\"]\n\turl = {url}\n"),
        ".git/objects/.keep": "",
        ".git/refs/heads/.keep": "",
    }


class StateBackupRemote(unittest.TestCase):
    URL = "https://github.com/example-org/state.git"

    def _run(self, visibility):
        gh = ("#!/bin/sh\ncase \"$*\" in *.visibility*) echo %s ;; "
              "*) exit 1 ;; esac\n" % visibility)
        return run_preflight(project_files=_repo_with_backup_remote(self.URL),
                             stub={"gh": gh})

    def test_public_backup_remote_is_a_fail(self):
        code, out = self._run("public")
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(
            out, "state-backup remote example-org/state is not a private repository"), out)

    def test_private_backup_remote_is_not_a_fail(self):
        _, out = self._run("private")
        self.assertFalse(lines_for(out, "is not a private repository"), out)

    def test_no_backup_remote_is_a_warn(self):
        _, out = run_preflight()
        self.assertTrue(lines_for(out, "no nightshift-state remote"), out)


class PasswordlessSudo(unittest.TestCase):
    """Audit low: provisioning adopts the existing owner of /srv/nightshift,
    which on EC2 is `ubuntu` with NOPASSWD:ALL -- root for every worker."""

    def test_nopasswd_entry_is_a_fail(self):
        sudo = "#!/bin/sh\n[ \"$2\" = -l ] && echo '    (ALL) NOPASSWD: ALL' && exit 0\nexit 1\n"
        code, out = run_preflight(stub={"sudo": sudo})
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, "can sudo without a password"), out)

    def test_cached_or_listpw_never_sudo_is_a_fail(self):
        sudo = "#!/bin/sh\n[ \"$2\" = true ] && exit 0\nexit 1\n"
        _, out = run_preflight(stub={"sudo": sudo})
        self.assertTrue(lines_for(out, "can sudo without a password"), out)

    def test_password_gated_or_absent_sudo_is_ok(self):
        for stub in ({"sudo": "#!/bin/sh\necho 'sudo: a password is required' >&2\nexit 1\n"},
                     {}):
            with self.subTest(stub=bool(stub)):
                _, out = run_preflight(stub=stub or None, hide={"sudo"})
                self.assertFalse(lines_for(out, "can sudo without a password"), out)
                self.assertTrue(lines_for(out, "no passwordless sudo"), out)


class DatabasePasswordOffArgv(unittest.TestCase):
    """Audit low: `--database-uri` carried the Postgres password on argv,
    readable by every local user through `ps`."""

    def test_password_in_server_env_uri_is_a_fail(self):
        for uri in ("postgresql://omnigent:s3cret@127.0.0.1:5433/omnigent",
                    "postgresql://omnigent@127.0.0.1:5433/omnigent?password=s3cret"):
            with self.subTest(uri=uri):
                code, out = run_preflight(server_env=f"OMNIGENT_DATABASE_URI={uri}\n")
                self.assertNotEqual(code, 0)
                self.assertTrue(lines_for(out, "carries the database password"), out)

    def test_password_free_uri_is_not_a_fail(self):
        _, out = run_preflight(server_env=(
            "OMNIGENT_DATABASE_URI=postgresql://omnigent@127.0.0.1:5433/omnigent\n"
            "PGPASSFILE=/home/x/.config/omnigent/pgpass\n"))
        self.assertFalse(lines_for(out, "database password"), out)

    def test_a_running_server_with_the_password_on_argv_is_a_fail(self):
        ps = ("#!/bin/sh\necho 'omnigent server --database-uri "
              "postgresql://omnigent:s3cret@127.0.0.1:5433/omnigent --agent /x'\n")
        code, out = run_preflight(stub={"ps": ps})
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, "password in its --database-uri argument"), out)


class AwsInstanceMetadata(unittest.TestCase):
    """Audit low: on AWS every worker is a host process, so a role served by
    IMDS is readable by all of them, and IMDSv1 needs no token at all."""

    CURL = (
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *api/token*) [ -n \"$V2\" ] && echo token && exit 0; exit 22 ;;\n"
        "  *iam/security-credentials*) [ -n \"$ROLE\" ] && echo \"$ROLE\"; exit 0 ;;\n"
        "  *latest/meta-data/*) [ -n \"$V1\" ] && exit 0; exit 22 ;;\n"
        "  *) exit 7 ;;\n"
        "esac\n"
    )

    def _run(self, **env):
        return run_preflight(stub={"curl": self.CURL},
                             env={"NIGHTSHIFT_PROVIDER": "aws", **env})

    def test_imdsv1_is_a_fail(self):
        code, out = self._run(V1="1", V2="1")
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, "IMDSv1 answers on this instance"), out)

    def test_a_served_role_is_a_warn_naming_it(self):
        _, out = self._run(V2="1", ROLE="project-role")
        self.assertFalse(lines_for(out, "IMDSv1 answers"), out)
        self.assertTrue(lines_for(out, "instance role project-role is served by IMDS"), out)

    def test_disabled_imds_is_ok(self):
        _, out = self._run()
        self.assertTrue(lines_for(out, "IMDS disabled or unreachable"), out)

    def test_not_probed_off_aws(self):
        _, out = run_preflight(stub={"curl": self.CURL},
                               env={"NIGHTSHIFT_PROVIDER": "generic", "V1": "1"})
        self.assertFalse(lines_for(out, "IMDS"), out)


class FenceConfigurationOwnership(unittest.TestCase):
    """Audit C6: server.env lived in the runtime user's HOME, written by that
    user, and workers share its uid. Preflight must FAIL while any file that
    configures the fences is writable by the user running it."""

    def test_missing_root_server_env_is_a_fail(self):
        code, out = run_preflight(env={"NIGHTSHIFT_SERVER_ENV": "@HOME@/absent/server.env"})
        self.assertNotEqual(code, 0)
        self.assertTrue(lines_for(out, "server environment"), out)
        self.assertTrue(lines_for(out, "install-root-config.sh --user"), out)

    def test_server_env_the_runtime_user_owns_is_a_fail(self):
        code, out = run_preflight(server_env="PYTHONPATH=/nonexistent\n")
        self.assertNotEqual(code, 0)
        hits = lines_for(out, "fence-configuration path(s) writable by")
        self.assertTrue(hits, out)
        self.assertIn("owned by the runtime uid", hits[0])

    def test_writable_policy_dir_and_registry_are_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "authority", "7"))
            _, out = run_preflight(server_env=(
                f"PYTHONPATH={tmp}\nNIGHTSHIFT_STATE_ROOT={tmp}\n"
                "NIGHTSHIFT_REPOSITORY_ID=7\n"))
        hits = lines_for(out, "fence-configuration path(s) writable by")
        self.assertTrue(hits, out)
        self.assertGreaterEqual(int(hits[0].split()[1]), 3, hits[0])

    def test_root_owned_files_are_ok(self):
        # /etc, not /usr/share: GitHub's hosted runners ship /usr/share
        # world-writable, and the ancestor walk rightly fails that.
        _, out = run_preflight(env={"NIGHTSHIFT_SERVER_ENV": "/etc/passwd",
                                    "TEAMS_DIR": "/etc/nonexistent-bundle"})
        self.assertFalse(lines_for(out, "fence-configuration path(s) writable by"), out)
        self.assertTrue(lines_for(out, "not writable by"), out)

    def test_unit_pointing_elsewhere_or_with_a_drop_in_is_a_fail(self):
        unit = ".config/systemd/user/omnigent-server.service"
        for files in ({unit: "[Service]\nEnvironmentFile=/home/x/.config/omnigent/server.env\n"},
                      {unit: "[Service]\nEnvironmentFile=@SERVER_ENV@\n",
                       unit + ".d/override.conf": "[Service]\n"}):
            with self.subTest(files=sorted(files)):
                code, out = run_preflight(
                    home_files={k: v.replace("@SERVER_ENV@", "/etc/passwd")
                                for k, v in files.items()},
                    env={"NIGHTSHIFT_SERVER_ENV": "/etc/passwd"})
                self.assertNotEqual(code, 0)
                self.assertTrue(lines_for(out, "user units load configuration other than"), out)

    def test_legacy_home_server_env_is_a_warn(self):
        _, out = run_preflight(home_files={".config/omnigent/server.env": "X=1\n"})
        self.assertTrue(lines_for(out, "is no longer read"), out)


class OptInChecks(unittest.TestCase):
    """rtk and Tailscale Serve are opt-in; their absence is not a finding
    unless the operator asked for them."""

    TAILSCALE = ("#!/bin/sh\ncase \"$*\" in\n  'serve status') echo 'No serve config'; exit 0 ;;\n"
                 "  *) exit 0 ;;\nesac\n")

    def test_absent_rtk_is_silent_unless_opted_in(self):
        _, out = run_preflight(hide={"rtk"})
        self.assertFalse(lines_for(out, "rtk missing"), out)
        _, out = run_preflight(hide={"rtk"}, env={"NIGHTSHIFT_RTK": "1"})
        self.assertTrue(lines_for(out, "rtk missing"), out)

    def test_absent_serve_proxy_is_fine_unless_opted_in(self):
        _, out = run_preflight(stub={"tailscale": self.TAILSCALE})
        self.assertFalse(lines_for(out, "not proxying 6767"), out)
        self.assertTrue(lines_for(out, "tailscale serve not expected"), out)
        _, out = run_preflight(stub={"tailscale": self.TAILSCALE},
                               env={"NIGHTSHIFT_TAILSCALE_SERVE": "1"})
        self.assertTrue(lines_for(out, "not proxying 6767"), out)
