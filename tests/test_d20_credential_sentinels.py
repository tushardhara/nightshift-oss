"""DoD condition 20 -- no credential material reaches any operator-visible sink.

Condition 20 (``docs/prd/DEFINITION-OF-DONE.md:458``) requires that unique
sentinels injected at every credential source -- ``~/.config/omnigent/server.env``,
the Postgres password, ``GH_TOKEN``, and the harness credential files -- are
asserted ABSENT from the enumerated sink corpus of condition 16 Q1, which
``docs/audit/BOUNDARIES.md`` enumerates as ``SINK-01``..``SINK-06``.

WHY THIS FILE IS NOT A TAUTOLOGY
--------------------------------
A sentinel test that cannot fail is worse than none: it marks a DoD condition
satisfied on no evidence. Two structural defences are therefore built in, and
both are themselves assertions in this file rather than review comments:

1.  **Every sink scanner is paired with a POSITIVE CONTROL.** For each sink,
    one test feeds the *same* scanner a known-poisoned input and asserts the
    scanner REPORTS the leak. A scanner that silently stops looking -- a glob
    that no longer matches, a renamed symbol, a moved file, a fixture whose
    credential never actually reached the child process -- fails its positive
    control instead of passing an empty scan.

2.  **The covered corpus is bound to the audit by id.** ``test_corpus_matches_
    the_audit`` re-parses ``docs/audit/BOUNDARIES.md``'s own SINK table with the
    same regex the validator uses and asserts it equals the set covered here,
    so "an audit that grows the inventory grows the test" is enforced rather
    than hoped for. Adding ``SINK-07`` to the audit fails this file until it is
    covered.

Hermetic by construction: no network, no ``gh``, no ``journalctl``, no live
control plane. External binaries are stubbed on PATH. Nothing here may skip --
``tests/dod_validate.py::_test_run_green`` fails D20 on a single skip.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Make sibling test modules importable whether the loader is anchored at the
# repo root ("tests.test_d20_...") or at tests/ ("test_d20_..."), so this file
# runs identically under either of dod_validate's two cwd_choice forms.
sys.path.insert(0, str(Path(__file__).resolve().parent))

CTL = str(REPO / "bin" / "nightshift-ctl")
DETECTOR = str(REPO / "deploy" / "nightshift-scheduled-run-detector.py")

# --------------------------------------------------------------------------
# Sentinels
# --------------------------------------------------------------------------
# One unique, greppable token per credential source. The shape is fixed so a
# single regex can find ANY sentinel in a blob of sink output -- that regex is
# what the positive controls exercise.
SENTINEL_RE = re.compile(r"NSD20-[A-Z0-9]+-[0-9a-f]{32}")


def mint(slot: str) -> str:
    return "NSD20-%s-%s" % (slot, uuid.uuid4().hex)


def scan(*blobs) -> list:
    """Every sentinel visible in these blobs. The single detection primitive:
    if this is broken, every positive control in this file fails."""
    found = set()
    for blob in blobs:
        if blob is None:
            continue
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8", "replace")
        elif not isinstance(blob, str):
            blob = json.dumps(blob)
        found.update(SENTINEL_RE.findall(blob))
    return sorted(found)


class FixtureInstall:
    """A throwaway install whose every credential source holds a sentinel.

    This is the "fixture install" condition 20 names. It is a real directory
    tree with real files, handed to real production entry points as $HOME.
    """

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="d20-fixture-")
        self.home = os.path.join(self.root, "home")
        cfg = os.path.join(self.home, ".config", "omnigent")
        os.makedirs(cfg)

        # Condition 20's four named credential sources.
        self.server_env = mint("SERVERENV")
        self.pgpass = mint("PGPASSWORD")
        self.gh_token = mint("GHTOKEN")
        self.claude_cred = mint("CLAUDECRED")
        self.codex_cred = mint("CODEXCRED")

        self._write(os.path.join(cfg, "server.env"),
                    "OMNIGENT_API_KEY=%s\n"
                    "DATABASE_URL=postgresql://ns:%s@127.0.0.1:5432/ns\n"
                    % (self.server_env, self.pgpass))
        self._write(os.path.join(cfg, "scheduled-run-detector.env"),
                    "GH_TOKEN=%s\n" % self.gh_token)
        self._write(os.path.join(self.home, ".pgpass"),
                    "127.0.0.1:5432:ns:ns:%s\n" % self.pgpass)
        claude = os.path.join(self.home, ".claude")
        os.makedirs(claude)
        self._write(os.path.join(claude, ".credentials.json"),
                    json.dumps({"accessToken": self.claude_cred}))
        codex = os.path.join(self.home, ".codex")
        os.makedirs(codex)
        self._write(os.path.join(codex, "auth.json"),
                    json.dumps({"OPENAI_API_KEY": self.codex_cred}))

    @staticmethod
    def _write(path, text):
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o600)

    def all_sentinels(self) -> list:
        return sorted({self.server_env, self.pgpass, self.gh_token,
                       self.claude_cred, self.codex_cred})

    def env(self, **extra) -> dict:
        """Process environment for a fixture install: the credentials are in
        the environment AND on disk, which is how the real units run them."""
        env = dict(os.environ)
        env.update({
            "HOME": self.home,
            "GH_TOKEN": self.gh_token,
            "GITHUB_TOKEN": self.gh_token,
            "PGPASSWORD": self.pgpass,
            "OMNIGENT_API_KEY": self.server_env,
        })
        env.update(extra)
        return env


# --------------------------------------------------------------------------
# The corpus binding: this file covers exactly what the audit enumerated
# --------------------------------------------------------------------------

# Same regex tests/dod_validate.py::_boundaries_sink_ids uses, so this file and
# the validator can never disagree about what the corpus is.
_SINK_ID_TABLE_RE = re.compile(r"^\|\s*`(SINK-\d+)`\s*\|", re.MULTILINE)

COVERED_SINKS = {
    "SINK-01": "process stdout/stderr as captured by any supervisor",
    "SINK-02": "journald (the query surface over SINK-01's stored output)",
    "SINK-03": "alert bodies (detector + watchdog)",
    "SINK-04": "PR/issue comments produced by the tooling",
    "SINK-05": "CI logs",
    "SINK-06": "agent transcripts",
}


def audit_sink_ids() -> set:
    text = (REPO / "docs" / "audit" / "BOUNDARIES.md").read_text(
        encoding="utf-8", errors="replace")
    return set(_SINK_ID_TABLE_RE.findall(text))


class SinkCorpusBinding(unittest.TestCase):
    """Condition 20: "the sink corpus it scans is the same list the audit
    enumerated (compared by id, so an audit that grows the inventory grows the
    test)"."""

    def test_corpus_matches_the_audit(self):
        audit = audit_sink_ids()
        self.assertTrue(audit, "docs/audit/BOUNDARIES.md has no SINK-nn table")
        self.assertEqual(
            audit, set(COVERED_SINKS),
            "the sink corpus this sentinel test scans has drifted from the "
            "corpus condition 16 Q1 enumerated -- audit-only=%s, test-only=%s. "
            "A sink the audit names and this file does not scan is a sink "
            "nothing checks." % (sorted(audit - set(COVERED_SINKS)),
                                 sorted(set(COVERED_SINKS) - audit)))

    def test_scanner_detects_a_sentinel(self):
        """Positive control for the detection primitive every other test uses."""
        token = mint("SELFTEST")
        self.assertEqual(scan("noise %s noise" % token), [token])
        self.assertEqual(scan("nothing to see here"), [])


# --------------------------------------------------------------------------
# SINK-01 -- process stdout/stderr as captured by any supervisor
# --------------------------------------------------------------------------

class Sink01ProcessOutput(unittest.TestCase):
    """The units route stdout/stderr to the journal, so anything a production
    entry point prints is operator-visible. Run the real entry points inside a
    poisoned fixture install and read what they actually emit."""

    def setUp(self):
        self.fx = FixtureInstall()
        self.db = os.path.join(self.fx.root, "control.db")

    def _run(self, argv):
        p = subprocess.run(argv, capture_output=True, text=True,
                           env=self.fx.env(), cwd=self.fx.root, timeout=120)
        return p.stdout + p.stderr

    def test_production_entry_points_emit_no_credential(self):
        # POSITIVE CONTROL, inline (MB-1): the targets must exist and must
        # actually run. A moved, renamed or deleted entry point yields
        # "can't open file ..." on stderr, which carries no sentinel, so the
        # absence assertions below would pass on an empty corpus.
        for target in (CTL, DETECTOR):
            self.assertTrue(os.path.exists(target),
                            "%s is missing, so scanning its output proves "
                            "nothing" % target)
        # Includes deliberate ERROR paths: a usage error and a missing-argument
        # abort are where a program is most likely to dump its environment or a
        # traceback carrying locals.
        for argv in (
            [sys.executable, CTL, "--help"],
            [sys.executable, CTL, "--db", self.db, "selftest"],
            [sys.executable, CTL, "--db", self.db, "slot", "acquire"],
            [sys.executable, CTL, "--db", self.db, "task", "show"],
            [sys.executable, DETECTOR, "--help"],
            [sys.executable, DETECTOR],
        ):
            with self.subTest(argv=argv[1:]):
                out = self._run(argv)
                # ... and each one must really have executed: an interpreter
                # that could not open the target produces no scannable output.
                self.assertTrue(out.strip(),
                                "produced no output at all, so there is "
                                "nothing to have scanned")
                self.assertNotIn("can't open file", out,
                                 "the entry point never ran")
                self.assertEqual(
                    scan(out), [],
                    "a credential sentinel reached process output, which the "
                    "systemd units capture into the journal")

    def test_the_fixture_credentials_really_reach_child_processes(self):
        """POSITIVE CONTROL for the test above.

        Without this, `test_production_entry_points_emit_no_credential` would
        also pass if the fixture never put a credential into the child's
        environment at all -- an empty scan of an unpoisoned process.
        """
        leaked = self._run(["sh", "-c", 'echo "$GH_TOKEN $OMNIGENT_API_KEY"'])
        self.assertEqual(scan(leaked),
                         sorted({self.fx.gh_token, self.fx.server_env}),
                         "the fixture is not actually poisoning child "
                         "processes, so the absence checks prove nothing")

    def test_no_shell_tracing_in_shipped_scripts(self):
        """`set -x` turns every expanded credential into journal output. The
        audit swept for this (BOUNDARIES.capture.txt section 9); this keeps it
        swept."""
        offenders = []
        scanned = 0
        for base in ("deploy", "bin"):
            for path in sorted((REPO / base).rglob("*")):
                if not path.is_file() or path.suffix in (".md", ".json"):
                    continue
                scanned += 1
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if re.match(r"^\s*set\s+-[a-wyz]*x", line):
                        offenders.append("%s:%d" % (path.relative_to(REPO), n))
        # POSITIVE CONTROL (MB-1): rglob over an absent deploy/ or bin/ yields
        # no offenders, which is indistinguishable from a clean tree.
        self.assertTrue(scanned, "no shipped scripts were scanned at all")
        self.assertEqual(offenders, [],
                         "shell tracing echoes expanded credentials to stderr")


# --------------------------------------------------------------------------
# SINK-02 -- journald
# --------------------------------------------------------------------------

_CRED_KEY_RE = re.compile(
    r"\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|CREDENTIAL)[A-Z0-9_]*)="
    r"([^\s\"']*)")


def unit_inline_credentials(text: str) -> list:
    """Credential-shaped assignments carried INLINE on a unit's Environment= or
    ExecStart= line, where the command line is visible to `ps` and journald.
    A secret must arrive by EnvironmentFile=, which is not command-line
    visible. Empty values (e.g. proxy scrubbing) are not credentials."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not (s.startswith("Environment=") or s.startswith("ExecStart=")):
            continue
        for key, value in _CRED_KEY_RE.findall(s):
            if value:
                hits.append("%d:%s" % (n, key))
    return hits


class Sink02Journald(unittest.TestCase):
    """journald is the query surface over SINK-01's stored output, plus the
    unit command lines themselves."""

    def test_no_unit_puts_a_credential_on_a_command_line(self):
        units = sorted((REPO / "deploy" / "systemd").glob("*.service"))
        self.assertTrue(units, "no systemd units found to check")
        for unit in units:
            with self.subTest(unit=unit.name):
                self.assertEqual(
                    unit_inline_credentials(
                        unit.read_text(encoding="utf-8", errors="replace")),
                    [],
                    "%s carries a credential inline; journald and `ps` capture "
                    "it. Use EnvironmentFile=." % unit.name)

    def test_inline_credential_is_detected(self):
        """POSITIVE CONTROL: the same checker must flag a unit that does what
        the test above forbids, or it is just returning an empty list."""
        token = mint("UNIT")
        poisoned = ("[Service]\n"
                    "Environment=GH_TOKEN=%s\n"
                    "ExecStart=/bin/true\n" % token)
        self.assertEqual(
            [h.split(":")[1] for h in unit_inline_credentials(poisoned)],
            ["GH_TOKEN"])
        # And the sanctioned form is NOT flagged, so the check is discriminating
        # rather than merely alarmist.
        self.assertEqual(unit_inline_credentials(
            "[Service]\nEnvironmentFile=/home/ns/.config/omnigent/server.env\n"), [])


# --------------------------------------------------------------------------
# SINK-03 -- alert bodies
# --------------------------------------------------------------------------

def load_detector():
    import importlib.util
    spec = importlib.util.spec_from_file_location("d20_detector", DETECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sink03DetectorAlertBody(unittest.TestCase):
    """`_alert_body()` says "Raw transcript/error text is intentionally
    excluded." That sentence is the property; this makes it a test.

    An agent transcript is exactly where a leaked credential would sit, so the
    fixture puts a sentinel in the transcript and then asks the alert builder
    for the operator-visible body.
    """

    def setUp(self):
        self.fx = FixtureInstall()
        self.mod = load_detector()
        self.items = {"data": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text",
                         "text": "export GH_TOKEN=%s" % self.fx.gh_token}],
        }]}

    def test_transcript_text_reaches_the_classifier(self):
        """POSITIVE CONTROL, and the load-bearing one for this sink.

        `_assistant_texts` is the one function that DOES read transcript
        content. Proving the sentinel is visible THERE is what makes its
        absence from `_alert_body` meaningful: it shows the credential really
        was present and reachable in the input, so the empty scan below is a
        property of the alert builder, not of an empty fixture.
        """
        self.assertEqual(scan(self.mod._assistant_texts(self.items)),
                         [self.fx.gh_token])

    def test_alert_body_excludes_credential_material(self):
        # The six STRUCTURED fields `_alert_body` renders carry no sentinel --
        # rendering them is correct and must stay green. The sentinels sit in
        # the raw transcript/error-derived material that travels alongside an
        # observation, which is exactly what the body promises to exclude. So
        # this fails precisely when someone widens the body to render raw
        # context, and not when they touch the legitimate fields.
        observation = {
            "consecutive_failures": 2,
            "run_id": "run_abc123",
            "conversation_id": "conv_abc123",
            "observation_key": "task:2026-09-02T00:00:00Z",
            "task_id": "hourly-heartbeat",
            "scheduled_at": 1788330000,
            "status": "failed",
            "failure_class": "auth",
            # Raw context an operator-helpful change would be tempted to inline:
            "error_text": "auth failed for GH_TOKEN=%s" % self.fx.gh_token,
            "last_assistant_text": self.mod._assistant_texts(self.items)[0],
            "env_snapshot": {"OMNIGENT_API_KEY": self.fx.server_env},
        }
        body = self.mod._alert_body(observation)
        self.assertTrue(body.strip(), "alert body must not be empty")
        # The body must still be the real report, not an empty string that
        # trivially contains no sentinel.
        self.assertIn("Nightshift scheduled heartbeat", body)
        self.assertIn("hourly-heartbeat", body)
        self.assertEqual(
            scan(body), [],
            "a credential sentinel reached the GitHub alert body")


class Sink03WatchdogAlertBody(unittest.TestCase):
    """The other half of SINK-03: `deploy/nightshift-watchdog.sh`'s `body=`.

    Drives the REAL script through the existing watchdog fixture, with `gh`,
    `systemctl` and `nightshift-ctl` stubbed on PATH, inside a poisoned
    environment.
    """

    def setUp(self):
        from test_watchdog import Box
        self.box = Box()
        self.fx = FixtureInstall()

    def test_watchdog_alert_body_excludes_credential_material(self):
        self.box.not_alive("stale")
        self.box.backup_writes_now()
        code, out = self.box.run(extra_env={
            "GH_TOKEN": self.fx.gh_token,
            "GITHUB_TOKEN": self.fx.gh_token,
            "PGPASSWORD": self.fx.pgpass,
            "OMNIGENT_API_KEY": self.fx.server_env,
        })
        self.assertEqual(code, 0, "the watchdog must not exit non-zero: %s" % out)
        # POSITIVE CONTROL: an alert must actually have been delivered. Without
        # this, "no sentinel in the alert body" would pass trivially on a run
        # that produced no alert body at all.
        self.assertEqual(self.box.alert_count(), 1,
                         "no alert fired, so the body assertion below would be "
                         "vacuous")
        body = self.box.alert_body()
        self.assertTrue(body.strip(), "alert body must not be empty")
        self.assertEqual(scan(body, out), [],
                         "a credential sentinel reached the watchdog alert "
                         "body or its process output")


# --------------------------------------------------------------------------
# SINK-04 -- PR/issue comments produced by the tooling
# --------------------------------------------------------------------------

class Sink04PrIssueComments(unittest.TestCase):
    """`nightshift-ctl pr-create` is the sanctioned PR path and wraps
    `gh pr create`. Stub gh, capture the exact bytes it was handed."""

    def setUp(self):
        self.fx = FixtureInstall()
        self.marker = os.path.join(self.fx.root, "gh-argv.json")
        self.gh = os.path.join(self.fx.root, "gh-stub")
        with open(self.gh, "w") as fh:
            fh.write("#!/usr/bin/env python3\n"
                     "import json, sys\n"
                     "json.dump(sys.argv[1:], open(%r, 'w'))\n" % self.marker)
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IEXEC)
        self.body_path = os.path.join(self.fx.root, "body.md")
        with open(self.body_path, "w") as fh:
            fh.write("Implements the thing.\n\nRefs #486\n")

    def test_pr_body_carries_no_credential(self):
        p = subprocess.run(
            [sys.executable, CTL, "--db", os.path.join(self.fx.root, "c.db"),
             "pr-create", "--", "--title", "t",
             "--body-file", self.body_path],
            capture_output=True, text=True, timeout=120,
            env=self.fx.env(NIGHTSHIFT_CTL_GH=self.gh))
        # POSITIVE CONTROL: gh must actually have been invoked, otherwise there
        # is no comment body to have scanned.
        self.assertTrue(os.path.exists(self.marker),
                        "gh was never invoked, so this proves nothing: %s%s"
                        % (p.stdout, p.stderr))
        with open(self.marker) as fh:
            argv = json.load(fh)
        delivered = ""
        for i, arg in enumerate(argv):
            if arg == "--body-file" and i + 1 < len(argv):
                with open(argv[i + 1]) as fh:
                    delivered = fh.read()
        self.assertEqual(
            scan(argv, delivered, p.stdout, p.stderr), [],
            "a credential sentinel reached a PR/issue comment body, its "
            "argv, or the tool's own output")


# --------------------------------------------------------------------------
# SINK-05 -- CI logs
# --------------------------------------------------------------------------

_SECRET_REF_RE = re.compile(r"\$\{\{\s*secrets\.")


def workflow_secret_in_run_block(text: str) -> list:
    """Secret interpolations that land inside a `run:` script.

    A secret bound under `env:` is passed to the process; a secret interpolated
    into a `run:` line is substituted into the shell command itself, which the
    CI log renders verbatim. Only the latter is a leak.
    """
    hits = []
    run_indent = None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if run_indent is not None and indent <= run_indent:
            run_indent = None
        m = re.match(r"^(\s*)-?\s*run:", line)
        if m:
            run_indent = len(m.group(1))
            if _SECRET_REF_RE.search(line):
                hits.append(n)
            continue
        if run_indent is not None and _SECRET_REF_RE.search(line):
            hits.append(n)
    return hits


class Sink05CiLogs(unittest.TestCase):
    def test_no_workflow_interpolates_a_secret_into_a_command(self):
        flows = sorted((REPO / ".github" / "workflows").glob("*.yml"))
        self.assertTrue(flows, "no workflows found to check")
        for flow in flows:
            with self.subTest(workflow=flow.name):
                self.assertEqual(
                    workflow_secret_in_run_block(
                        flow.read_text(encoding="utf-8", errors="replace")),
                    [],
                    "%s interpolates a secret into a run: script; the CI log "
                    "renders that command verbatim. Bind it under env: "
                    "instead." % flow.name)

    def test_secret_in_a_run_block_is_detected(self):
        """POSITIVE CONTROL: flags the leak, and does NOT flag the sanctioned
        `env:` binding that the real workflow uses."""
        leaky = ("jobs:\n  j:\n    steps:\n      - name: x\n"
                 "        run: echo ${{ secrets.GITHUB_TOKEN }}\n")
        self.assertEqual(workflow_secret_in_run_block(leaky), [5])
        safe = ("jobs:\n  j:\n    steps:\n      - name: x\n"
                "        env:\n          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                "        run: python3 tests/live/x.py\n")
        self.assertEqual(workflow_secret_in_run_block(safe), [])


# --------------------------------------------------------------------------
# SINK-06 -- agent transcripts
# --------------------------------------------------------------------------

_READ_TO_STDOUT_RE = re.compile(
    r"\b(cat|head|tail|less|more|xxd|base64|od|strings|jq)\b[^\n;|&]*?"
    r"(server\.env|\.pgpass|credentials\.json|auth\.json|detector\.env)")


def credential_dumps(path_iter) -> list:
    """Committed tooling that reads a credential file's CONTENTS to stdout.

    Anything a committed script prints becomes the lead's tool output, which
    becomes an agent transcript -- the sink whose safety the audit found rested
    on operator discipline (F-1) rather than a structural control. Writing to
    these files (bootstrap) is fine; dumping them is not.
    """
    hits = []
    for path in path_iter:
        if not path.is_file() or path.suffix in (".md", ".json"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if _READ_TO_STDOUT_RE.search(s):
                hits.append("%s:%d" % (path.name, n))
    return hits


class Sink06AgentTranscripts(unittest.TestCase):
    def test_no_committed_script_dumps_a_credential_file(self):
        paths = []
        for base in ("deploy", "bin"):
            paths.extend(sorted((REPO / base).rglob("*")))
        # POSITIVE CONTROL (MB-1): count what credential_dumps will actually
        # examine, using its own filter. An absent tree, or one holding only
        # docs, scans nothing and would report clean.
        scanned = [p for p in paths
                   if p.is_file() and p.suffix not in (".md", ".json")]
        self.assertTrue(scanned, "no committed scripts were scanned at all")
        self.assertEqual(
            credential_dumps(paths), [],
            "a committed script prints a credential file's contents; that "
            "output becomes an operator-visible agent transcript")

    def test_credential_dump_is_detected(self):
        """POSITIVE CONTROL: the scanner must flag a real dump, or its empty
        result above means only that it never looked."""
        tmp = Path(tempfile.mkdtemp(prefix="d20-sink06-"))
        leaky = tmp / "leaky.sh"
        leaky.write_text('#!/bin/sh\ncat "$HOME/.config/omnigent/server.env"\n')
        self.assertEqual(credential_dumps([leaky]), ["leaky.sh:2"])
        safe = tmp / "safe.sh"
        safe.write_text('#!/bin/sh\nprintf "K=v\\n" > "$HOME/.pgpass"\n')
        self.assertEqual(credential_dumps([safe]), [])


if __name__ == "__main__":
    unittest.main()
