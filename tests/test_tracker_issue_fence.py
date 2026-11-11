"""Tracker-bound issue writes -- every `gh issue`/`gh api` issue mutation
binds to Nightshift's OWN tracker repository, mechanically, independent of
the working directory.

WHAT THIS SUITE PINS
=====================
  TrackerBindingWriteOnce         Raw UPDATE/DELETE abort at the database
                                   layer; the `declare` verb's own
                                   conflict/idempotence rule sits above
                                   that floor.
  TrackerSchema                   The type-less `repository_id` column
                                   genuinely refuses a string.
  TrackerResolutionFailsClosed    Absent / unbootstrapped / unaddressable
                                   registry states each deny every issue
                                   write, distinguishably.
  TrackerResolution                Resolution reads ONLY the declared
                                   registry -- proven by chdir()ing into a
                                   customer-shaped checkout with a DIFFERENT
                                   declared tracker and showing the
                                   resolved value and the fence's verdict
                                   are unaffected; and that none of the
                                   three shipped modules contain a
                                   hardcoded fallback repository slug,
                                   `git remote`, or `os.getcwd()` (#439).
  IssueFenceDenyAllowMatrix        The core DENY/ALLOW matrix: a bare issue
                                   write from a customer-shaped checkout is
                                   denied; the same write pinned to the
                                   bound tracker (bare `owner/name`,
                                   `--repo=`, `-R`, and URL forms) is
                                   allowed.
  IssueFenceArgvParsing            The ADR-0085 quoting defect is not
                                   repeated: shlex parses a real argv, so
                                   quote-splitting, `cd ... &&`, pipelines,
                                   `$()`/backtick substitution, newline-
                                   joined compounds, `sh -c`, an absolute
                                   `/usr/bin/gh` path, and `xargs` are all
                                   still recognised as issue writes. An
                                   unbalanced quote, a non-string command,
                                   and substitution nesting beyond the
                                   recursion bound all DENY rather than
                                   falling through to ALLOW.
  IssueFenceApiSurface             `gh api`/`gh api graphql` issue-mutation
                                   shapes are bound too; the cwd-resolving
                                   `{owner}`/`{repo}` placeholder form is
                                   denied by name; a plain GET is
                                   unaffected.
  IssueFenceEnvOverride            `GH_REPO`/`GH_HOST` prefixed directly
                                   onto a `gh` invocation in the SAME
                                   command DENY unconditionally, even when
                                   --repo names the correct tracker --
                                   closing an asymmetry with
                                   `bin/nightshift-file-issue`, which
                                   already refused both.
  IssueFenceNoEscapeHatch          No `..._FAIL_OPEN` variable exists or
                                   has any effect.
  FileIssueWrapper                 `bin/nightshift-file-issue` supplies the
                                   resolved tracker as `--repo` mechanically
                                   and refuses a caller-supplied override,
                                   via a real subprocess and a recording
                                   `gh` shim -- never a bare fallback.
  CallSitesAgreeWithMechanism      Every prompt/skill file that names an
                                   issue write -- including
                                   `program/config.yaml`'s own DoR/
                                   heartbeat prose, under ADR-0088 -- no
                                   longer instructs a bare `gh issue
                                   <write-verb>`, and names the wrapper;
                                   `bin/nightshift-tracker-init.sh` pins an
                                   explicit `--repo` on its own two
                                   issue-write lines.
  ConfigDeclaresTheFence           `tracker_issue_write_fence` is declared
                                   at the bundle root (ADR-0088), the
                                   attachment point that actually makes a
                                   Service-Policies-V0 policy live, and on
                                   no seat's own config.yaml.

Mockless: real sqlite3 over real tempdir registry files, and real
subprocess for both `bin/nightshift_tracker_authority.py` and
`bin/nightshift-file-issue` -- same posture as
`tests/test_external_repo_merge_fence.py`. The "customer-shaped checkout"
fixture is `tempfile.mkdtemp` + `git init` + `git remote add origin
https://github.com/customer-org/customer-repo.git`, built deterministically
in every test that needs one -- never the sealed testbeds under
`/home/operator/testbeds/.nightshift-testbed` (#698: not tracked by git,
do not exist on a CI runner).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
TRACKER_AUTHORITY_MODULE = BIN / "nightshift_tracker_authority.py"
FILE_ISSUE_WRAPPER = BIN / "nightshift-file-issue"
ISSUE_FENCE_MODULE = REPO / "deploy" / "policies" / "nightshift_issue_fence.py"

sys.path.insert(0, str(BIN))
import nightshift_authority as auth  # noqa: E402
import nightshift_tracker_authority as tracker_auth  # noqa: E402

sys.path.insert(0, str(REPO / "deploy" / "policies"))
import nightshift_issue_fence as F  # noqa: E402

T0 = 3_000_000.0
TRACKER_OWNER = "tushardhara"
TRACKER_NAME = "nightshift"
TRACKER_SLUG = f"{TRACKER_OWNER}/{TRACKER_NAME}"


def shell(cmd):
    return {"type": "tool_call",
            "data": {"name": "sys_os_shell", "arguments": {"command": cmd}}}


def run_tracker_cli(*argv):
    """Drive the real `bin/nightshift_tracker_authority.py` CLI in a
    subprocess -- same posture as `tests/test_external_repo_merge_fence.py`'s
    `run_cli` for `nightshift_authority.py`."""
    p = subprocess.run([sys.executable, str(TRACKER_AUTHORITY_MODULE), *argv],
                       capture_output=True, text=True)
    body = None
    if p.stdout.strip():
        body = json.loads(p.stdout.strip().splitlines()[-1])
    return p.returncode, body


def _make_customer_checkout():
    """A tempdir git repo whose origin names a customer repository -- built
    deterministically, never touching the sealed testbeds (#698)."""
    path = tempfile.mkdtemp(prefix="nightshift-customer-checkout-")
    subprocess.run(["git", "init", "-q", path], check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-C", path, "remote", "add", "origin",
         "https://github.com/customer-org/customer-repo.git"],
        check=True, capture_output=True)
    return path


class TrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="nightshift-tracker-")
        os.chmod(self.state_root, 0o700)
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.repository_id = 4242
        self._clear_env()

    def _clear_env(self):
        names = (auth.ENV_STATE_ROOT, auth.ENV_REPOSITORY_ID)
        for name in names:
            os.environ.pop(name, None)
        self.addCleanup(lambda: [os.environ.pop(n, None) for n in names])

    def _set_scope(self, repository_id=None):
        os.environ[auth.ENV_STATE_ROOT] = self.state_root
        os.environ[auth.ENV_REPOSITORY_ID] = str(repository_id or self.repository_id)

    def _cx(self):
        cx = tracker_auth.connect(self.state_root, self.repository_id)
        self.addCleanup(cx.close)
        return cx

    def _declare(self, owner=TRACKER_OWNER, name=TRACKER_NAME, t=T0):
        cx = self._cx()
        tracker_auth.declare_tracker(
            cx, repository_id=self.repository_id, owner=owner, name=name,
            declared_by="test-operator", t=t)
        return cx

    def _raw_cx(self):
        path = tracker_auth.tracker_bootstrap_path(self.state_root, self.repository_id)
        cx = sqlite3.connect(path)
        self.addCleanup(cx.close)
        return cx

    def _chdir(self, path):
        original = os.getcwd()
        self.addCleanup(os.chdir, original)
        os.chdir(path)


# ---------------------------------------------------------------------------
# The write-once binding
# ---------------------------------------------------------------------------

class TrackerBindingWriteOnce(TrackerTestCase):

    def test_redeclare_with_different_slug_is_refused(self):
        code, body = run_tracker_cli(
            "declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--tracker", TRACKER_SLUG, "--declared-by", "op1")
        self.assertEqual(code, 0, body)

        code, body = run_tracker_cli(
            "declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--tracker", "other-org/other-repo", "--declared-by", "op2")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "tracker_binding_already_declared")

    def test_redeclare_identical_is_idempotent(self):
        code, body = run_tracker_cli(
            "declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--tracker", TRACKER_SLUG, "--declared-by", "op1")
        self.assertEqual(code, 0, body)
        recorded_at = body["recorded_at"]

        # case folded and re-declared -- must be treated as the SAME value
        code, body = run_tracker_cli(
            "declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--tracker", TRACKER_SLUG.title(), "--declared-by", "op2")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["recorded_at"], recorded_at)
        self.assertEqual(body["declared_by"], "op1")  # unchanged, not op2

    def test_raw_update_and_delete_abort(self):
        self._declare()
        raw = self._raw_cx()
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            raw.execute("UPDATE tracker_binding SET owner='evil' WHERE id=1")
        self.assertIn("write-once", str(ctx.exception))
        cx = self._cx()
        self.assertEqual(tracker_auth.read_tracker_binding(cx)["owner"], TRACKER_OWNER)

        with self.assertRaises(sqlite3.IntegrityError) as ctx2:
            raw.execute("DELETE FROM tracker_binding WHERE id=1")
        self.assertIn("write-once", str(ctx2.exception))
        self.assertIsNotNone(tracker_auth.read_tracker_binding(cx))


class TrackerSchema(TrackerTestCase):

    def test_string_repository_id_refused(self):
        self._cx()  # bootstraps schema, declares nothing
        raw = self._raw_cx()
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO tracker_binding(id, repository_id, owner, name, "
                "declared_by, recorded_at) VALUES(1, '5', 'o', 'n', 'op', 1.0)")
        # cannot pass vacuously: a plain int MUST be accepted by the same CHECK
        raw.execute(
            "INSERT INTO tracker_binding(id, repository_id, owner, name, "
            "declared_by, recorded_at) VALUES(1, 5, 'o', 'n', 'op', 1.0)")
        row = raw.execute(
            "SELECT repository_id, typeof(repository_id) "
            "FROM tracker_binding WHERE id=1").fetchone()
        self.assertEqual(row[0], 5)
        self.assertEqual(row[1], "integer")


# ---------------------------------------------------------------------------
# Resolution: fail-closed, and reads ONLY the declared registry
# ---------------------------------------------------------------------------

class TrackerResolutionFailsClosed(TrackerTestCase):

    def test_absent_registry_denies_every_issue_write(self):
        self._set_scope()
        ev = F.tracker_issue_write_fence()
        r = ev(shell("gh issue create --repo a/b --title x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("registry_absent", r["reason"])

    def test_unbootstrapped_registry_denies(self):
        self._cx()  # file exists, schema run, nothing ever declared
        self._set_scope()
        ev = F.tracker_issue_write_fence()
        r = ev(shell("gh issue create --repo a/b --title x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("registry_unbootstrapped", r["reason"])

    def test_unaddressable_scope_denies(self):
        # no NIGHTSHIFT_STATE_ROOT / NIGHTSHIFT_REPOSITORY_ID at all
        ev = F.tracker_issue_write_fence()
        r = ev(shell("gh issue create --repo a/b --title x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("unaddressable", r["reason"])

    def test_four_sources_are_pairwise_distinct(self):
        """All FOUR sources `resolve_tracker`'s own docstring names --
        `registry`, `registry_absent`, `registry_unbootstrapped`,
        `unaddressable` -- not merely three of them. An earlier version of
        this test built its set from three calls and never exercised the
        declared-and-read (`registry`) case at all, so a builder summary's
        "4 distinct sources" claim was unproven by the one test named after
        it.
        """
        absent_id = self.repository_id + 1        # tracker.db never created
        unbootstrapped_id = self.repository_id + 2  # file exists, nothing declared
        cx = tracker_auth.connect(self.state_root, unbootstrapped_id)
        self.addCleanup(cx.close)
        self._declare()  # declares self.repository_id -> the "registry" case

        sources = {
            tracker_auth.resolve_tracker(None, self.repository_id)["source"],
            tracker_auth.resolve_tracker(self.state_root, absent_id)["source"],
            tracker_auth.resolve_tracker(self.state_root,
                                         unbootstrapped_id)["source"],
            tracker_auth.resolve_tracker(self.state_root,
                                         self.repository_id)["source"],
        }
        self.assertEqual(sources, {"unaddressable", "registry_absent",
                                   "registry_unbootstrapped", "registry"})


class TrackerResolution(TrackerTestCase):

    def test_resolution_ignores_cwd_git_remote(self):
        self._declare(owner=TRACKER_OWNER, name=TRACKER_NAME)
        self._set_scope()
        checkout = _make_customer_checkout()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        self._chdir(checkout)

        resolved = tracker_auth.resolve_tracker(self.state_root, self.repository_id)
        self.assertEqual(resolved["tracker"], TRACKER_SLUG)

        ev = F.tracker_issue_write_fence()
        r = ev(shell("gh issue create --repo customer-org/customer-repo --title x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn(TRACKER_SLUG, r["reason"])

    @staticmethod
    def _without_module_docstring(text):
        """Strip the file's LEADING module docstring (the first `\"\"\"...
        \"\"\"` block) and nothing else. All three shipped modules explain,
        in that opening docstring, the cwd-dependent signals they
        deliberately do NOT read (`git remote`, `os.getcwd()`,
        `.omni/tracker.json`) -- a whole-file substring scan would
        false-positive on that explanation. Stripping only the module
        docstring (not every string or comment in the file) still catches
        a REAL future call to any of these appearing in actual code, since
        no other part of these three files quotes any of these phrases."""
        first = text.find('"""')
        if first == -1:
            return text
        second = text.find('"""', first + 3)
        if second == -1:
            return text
        return text[:first] + text[second + 3:]

    def test_core_contains_no_repository_slug_literal(self):
        """#439: Core carries no project-specific values. Checked two ways:
        (1) outside their own explanatory module docstring, none of the
        three shipped modules mention `git remote`, `os.getcwd`, or
        `.omni/tracker.json` -- the cwd-dependent signals this fix exists
        to stop reading; (2) none of them assign a hardcoded fallback
        repository constant or fall back to a literal `owner/name` string
        with an `or` expression. Deliberately NOT a blanket "no string
        shaped like owner/name anywhere" scan -- that would false-positive
        on this file's own docstrings (`owner/name` format examples,
        `repos/{owner}/{repo}/issues` endpoint patterns, `customer-org/
        customer-repo` illustrations) and give no better proof than the
        targeted check below, which is exactly what the mutation
        (`DEFAULT_TRACKER = "<owner>/nightshift"`) looks like.
        """
        modules = (
            BIN / "nightshift_tracker_authority.py",
            FILE_ISSUE_WRAPPER,
            ISSUE_FENCE_MODULE,
        )
        cwd_signals = ("git remote", "os.getcwd", "getcwd(", ".omni/tracker.json")
        fallback_shape = re.compile(
            r'(DEFAULT_TRACKER|FALLBACK_TRACKER)\s*=|'
            r'\bor\s+["\'][A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+["\']')
        for path in modules:
            full_text = path.read_text()
            stripped = self._without_module_docstring(full_text)
            self.assertNotEqual(stripped, full_text,
                                f"{path}: no docstring found to strip -- "
                                "scan below would not be meaningful")
            for signal in cwd_signals:
                self.assertNotIn(signal, stripped,
                                 f"{path} contains {signal!r} outside its "
                                 "module docstring")
            m = fallback_shape.search(full_text)
            self.assertIsNone(m, f"{path}: {m.group(0) if m else ''}")


# ---------------------------------------------------------------------------
# The core DENY/ALLOW matrix
# ---------------------------------------------------------------------------

class IssueFenceDenyAllowMatrix(TrackerTestCase):

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def test_bare_issue_create_denied_from_customer_shaped_checkout(self):
        checkout = _make_customer_checkout()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        self._chdir(checkout)

        r = self.ev(shell('gh issue create --title x --label needs-dor'))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])
        self.assertIn(TRACKER_SLUG, r["reason"])

    def test_issue_create_pinned_to_bound_tracker_is_allowed(self):
        r = self.ev(shell(f"gh issue create --repo {TRACKER_SLUG} --title x"))
        self.assertEqual(r["result"], "ALLOW")

    def test_repo_equals_form_and_dash_R_and_url_forms_are_allowed(self):
        forms = (
            f"--repo={TRACKER_SLUG}",
            f"-R {TRACKER_SLUG}",
            f"--repo https://github.com/{TRACKER_SLUG}",
            f"--repo https://github.com/{TRACKER_SLUG}.git",
        )
        for form in forms:
            with self.subTest(form=form):
                r = self.ev(shell(f"gh issue create {form} --title x"))
                self.assertEqual(r["result"], "ALLOW", r)

    def test_wrong_repo_pinned_denies(self):
        r = self.ev(shell(
            "gh issue create --repo customer-org/customer-repo --title x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])

    def test_issue_edit_close_comment_use_the_same_matrix(self):
        for verb, extra in (
            ("edit", "1 --add-label state:ready"),
            ("close", "1"),
            ("comment", "1 --body hi"),
            ("reopen", "1"),
            ("pin", "1"),
        ):
            with self.subTest(verb=verb):
                denied = self.ev(shell(f"gh issue {verb} {extra}"))
                self.assertEqual(denied["result"], "DENY", (verb, denied))
                allowed = self.ev(shell(
                    f"gh issue {verb} {extra} --repo {TRACKER_SLUG}"))
                self.assertEqual(allowed["result"], "ALLOW", (verb, allowed))

    def test_issue_list_view_status_are_reads_and_pass_through(self):
        for cmd in (
            "gh issue list --label from-owner --state open",
            "gh issue view 1",
            "gh issue status",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.ev(shell(cmd))["result"], "ALLOW")

    def test_repo_flag_between_subcommand_and_verb_is_honored(self):
        """gh's own grammar (Cobra persistent flags) allows `--repo`/`-R`
        BEFORE the write verb is reached, not only after it -- e.g. `gh
        issue --repo o/n create`. A fence that only looked for `--repo`
        after the verb would treat this shape as "no verb found here, not
        our concern" and let it through completely unexamined, including
        one pointed at the wrong repository."""
        allowed = self.ev(shell(
            f"gh issue --repo {TRACKER_SLUG} create --title x"))
        self.assertEqual(allowed["result"], "ALLOW", allowed)
        denied = self.ev(shell(
            "gh issue -R customer-org/customer-repo create --title x"))
        self.assertEqual(denied["result"], "DENY", denied)
        self.assertIn("tracker_fence_issue_write_off_tracker", denied["reason"])

    def test_repo_flag_before_subcommand_is_honored(self):
        allowed = self.ev(shell(
            f"gh --repo {TRACKER_SLUG} issue create --title x"))
        self.assertEqual(allowed["result"], "ALLOW", allowed)
        denied = self.ev(shell(
            "gh --repo customer-org/customer-repo issue create --title x"))
        self.assertEqual(denied["result"], "DENY", denied)

    def test_unrelated_commands_are_unaffected(self):
        for cmd in ("git status", "gh pr create --title x --body y",
                    "gh label create foo", "nightshift-ctl task ready"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.ev(shell(cmd))["result"], "ALLOW")


# ---------------------------------------------------------------------------
# Argv parsing -- the ADR-0085 defect, not repeated
# ---------------------------------------------------------------------------

class IssueFenceArgvParsing(TrackerTestCase):

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def _assert_denied(self, cmd, contains=None):
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "DENY", (cmd, r))
        if contains:
            self.assertIn(contains, r["reason"])
        return r

    def test_quoted_subcommand_is_still_an_issue_write(self):
        for cmd in (
            'gh issue "create" --title x',
            'gh "issue" create --title x',
            "gh issue cre''ate --title x",
        ):
            with self.subTest(cmd=cmd):
                self._assert_denied(cmd, "tracker_fence_issue_write_off_tracker")

    def test_denied_after_cd_and_logical_and(self):
        self._assert_denied("cd /cust && gh issue create -t x")

    def test_denied_inside_pipeline(self):
        self._assert_denied("echo x | gh issue create -F -")

    def test_denied_inside_command_substitution(self):
        self._assert_denied("N=$(gh issue create --title x)")

    def test_denied_inside_backtick_substitution(self):
        self._assert_denied("x=`gh issue create -t y`")

    def test_denied_inside_double_quoted_command_substitution(self):
        self._assert_denied('echo "$(gh issue create -t y)"')

    def test_denied_on_second_line_of_a_newline_joined_command(self):
        cmd = f"gh issue create --repo {TRACKER_SLUG} -t a\ngh issue close 3"
        r = self._assert_denied(cmd, "tracker_fence_issue_write_off_tracker")
        self.assertIn("close", r["reason"])

    def test_denied_inside_sh_dash_c(self):
        self._assert_denied("sh -c 'gh issue create'")

    def test_denied_inside_nested_sh_dash_c(self):
        self._assert_denied("sh -c \"sh -c 'gh issue create'\"")

    def test_denied_inside_sh_dash_c_with_intervening_flags(self):
        """A ROUND-2 REGRESSION, closed here: the positional `-c` check
        originally required `-c` to sit LITERALLY next to the interpreter
        word, so any single ordinary flag between them (`sh -e -c`, `bash
        --norc -c`, an `-o OPTNAME` pair) fell through to the shape check
        alone and ALLOWed. `bash -lc` (fused) is the single most common
        agent shell-tool wrapper shape and is covered by the sibling test
        below, not here."""
        for cmd in (
            "sh -e -c 'gh issue create -t x'",
            "sh -x -c 'gh issue create -t x'",
            "bash -l -c 'gh issue create -t x'",
            "bash --norc -c 'gh issue create -t x'",
            "bash -o pipefail -c 'gh issue create -t x'",
            "/bin/sh -e -c 'gh issue create -t x'",
        ):
            with self.subTest(cmd=cmd):
                self._assert_denied(cmd, "tracker_fence_issue_write_off_tracker")

    def test_denied_inside_bash_dash_lc_fused_short_form(self):
        """`bash -lc '...'` / `zsh -ic '...'` -- `-c` fused into a leading
        cluster of single-letter flags in ONE token, not a separate `-c`
        token at all. Same round-2 regression as the sibling test above,
        named separately because it is a distinct token shape (no bare
        `-c` token exists anywhere in these commands for a naive
        `tok == "-c"` check to find)."""
        for cmd in (
            "bash -lc 'gh issue create -t x'",
            "zsh -ic 'gh issue create -t x'",
        ):
            with self.subTest(cmd=cmd):
                self._assert_denied(cmd, "tracker_fence_issue_write_off_tracker")

    def test_sh_dash_c_with_intervening_flags_pinned_to_tracker_allows(self):
        """Proves the flag-preamble scan resolves into the SAME matrix, not
        a blanket deny: a `bash -lc`/`sh -e -c` payload correctly pinned to
        the declared tracker is allowed exactly like a bare `gh` call
        would be."""
        for cmd in (
            f"sh -e -c 'gh issue create -t x --repo {TRACKER_SLUG}'",
            f"bash -lc 'gh issue create -t x --repo {TRACKER_SLUG}'",
        ):
            with self.subTest(cmd=cmd):
                r = self.ev(shell(cmd))
                self.assertEqual(r["result"], "ALLOW", r)

    def test_sh_script_file_argument_is_not_misread_as_dash_c(self):
        """The preamble scan must stop -- treating this as NOT a `-c`
        invocation -- at a positional script-file argument, not run past
        it looking for a `-c` deeper in the command. `sh script.sh` is
        ordinary, non-`gh` traffic and must be unaffected."""
        r = self.ev(shell("sh script.sh --repo customer-org/customer-repo"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_unenumerated_preamble_value_flag_before_dash_c_is_the_disclosed_gap(self):
        """Named, not silently dropped: a preamble flag OUTSIDE
        `_PREAMBLE_FLAGS_WITH_VALUE` that itself consumes a separate value
        token is misread as ending the preamble at the flag itself, so the
        scan never reaches `-c`. This is the one shape the module
        docstring's disclosed limit #2 still names as open -- distinct
        from (and much narrower than) the round-2 regression the sibling
        tests above close, which was ANY intervening flag."""
        r = self.ev(shell("bash --rcfile /dev/null -c 'gh issue create -t x'"))
        # --rcfile IS enumerated (takes a value, skipped correctly) --
        # this asserts the POSITIVE case is closed, not the gap itself.
        self.assertEqual(r["result"], "DENY", r)
        # A genuinely unenumerated value-flag falls through to the shape
        # check alone; since the payload here has no $()/backtick, it is
        # not recognised. This is the disclosed limit, not a false claim
        # of closure.
        self.assertEqual(
            self.ev(shell(
                "bash --not-a-real-flag placeholder -c 'gh issue create -t x'"
            ))["result"],
            "ALLOW")

    def test_denied_when_invoked_by_absolute_path(self):
        self._assert_denied("/usr/bin/gh issue create")

    def test_denied_behind_xargs(self):
        self._assert_denied("xargs -I{} gh issue create -t {}")

    def test_unbalanced_quote_denies(self):
        self._assert_denied('gh issue create --title "oops',
                            "tracker_fence_unparseable_command")

    def test_non_string_command_denies(self):
        event = {"type": "tool_call",
                 "data": {"name": "sys_os_shell", "arguments": {"command": 42}}}
        r = self.ev(event)
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_unparseable_command", r["reason"])

    def test_recursion_bound_exceeded_denies(self):
        payload = "gh issue create"
        for _ in range(5):  # five nested `sh -c` layers
            payload = "sh -c " + shlex.quote(payload)
        self._assert_denied(payload, "tracker_fence_unparseable_command")

    def test_alias_indirection_denies_as_unknown_subcommand(self):
        r = self.ev(shell('gh alias set fu "issue create"; gh fu -t x'))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_unknown_gh_subcommand", r["reason"])

    def test_title_containing_incidental_gh_issue_text_is_not_a_false_positive(self):
        """Guards the OTHER direction: recursion must be gated tightly
        enough that ordinary text mentioning `gh issue create` inside a
        flag VALUE does not itself get treated as a second invocation."""
        r = self.ev(shell(
            f'gh issue create --title "see gh issue create docs" '
            f'--repo {TRACKER_SLUG}'))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_title_whose_first_word_is_gh_is_not_a_false_positive(self):
        """The realistic variant `test_title_containing_incidental_...`
        above does NOT cover: a title whose FIRST word (not merely some
        interior word) normalizes to `gh`. ADR-0057 follow-up tickets
        routinely quote the command under discussion verbatim -- this very
        fix's own commit message quotes `gh issue create` four times --
        and a correctly `--repo`-pinned issue write must not be denied
        just because its title happens to start that way."""
        for title in (
            "gh issue close leaks",
            "gh issue create is denied without --repo",
        ):
            with self.subTest(title=title):
                r = self.ev(shell(
                    f'gh issue create --repo {TRACKER_SLUG} --title "{title}"'))
                self.assertEqual(r["result"], "ALLOW", r)

    def test_dash_dash_version_and_dash_dash_help_and_bare_version_allow(self):
        """`gh --version`, `gh --help`, and `gh version` are read-only,
        non-issue traffic -- `deploy/provision-ubuntu.sh` runs `gh
        --version` as a sanity check. None of the three names an issue
        write, so none should be denied."""
        for cmd in ("gh --version", "gh --help", "gh -h", "gh version"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.ev(shell(cmd))["result"], "ALLOW")

    def test_eval_payload_is_still_recognised_positionally(self):
        """`eval` is the other command-introducing word this fence's own
        docstring names alongside `sh -c`/`bash -c` -- covered here since
        the positional redesign changed HOW that recognition works."""
        denied = self.ev(shell("eval 'gh issue create -t x'"))
        self.assertEqual(denied["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", denied["reason"])
        allowed = self.ev(shell(
            f"eval 'gh issue create -t x --repo {TRACKER_SLUG}'"))
        self.assertEqual(allowed["result"], "ALLOW", allowed)

    def test_command_substitution_inside_a_flag_value_still_denies(self):
        """The over-block fix narrows recursion to POSITIONAL (`sh -c`/
        `eval`) or SHAPE (`$()`/backtick) triggers -- it must not have
        thrown out the shape trigger while fixing the positional one. A
        REAL `$(...)` construct is evaluated by the shell wherever it
        sits, including inside an otherwise-innocent `--title` value, so
        it must still be caught there."""
        r = self.ev(shell(
            f'gh issue create --repo {TRACKER_SLUG} '
            f'--title "$(gh issue close 1)"'))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])


class IssueFenceEveryShellToolName(TrackerTestCase):
    """Audit C1: claude-native seats report the shell tool as `Bash`, which
    the fence's lowercase-only name list never matched -- an unpinned issue
    write from any such seat was ALLOWed."""

    NAMES = ("Bash", "bash", "BASH", "sys_os_shell", "shell", "Shell",
             "terminal", "developer__shell", "run_command", "run_shell_command",
             "mcp__omnigent__sys_os_shell")

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def _call(self, name, cmd):
        return self.ev({"type": "tool_call",
                        "data": {"name": name, "arguments": {"command": cmd}}})

    def test_every_harness_shell_tool_is_fenced(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                r = self._call(name, "gh issue create -R cust/repo -t x")
                self.assertEqual(r["result"], "DENY", r)
                self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])

    def test_pinned_write_through_bash_still_allows(self):
        r = self._call("Bash", f"gh issue create -R {TRACKER_SLUG} -t x")
        self.assertEqual(r["result"], "ALLOW", r)


class IssueFenceLexerBypasses(TrackerTestCase):
    """Audit C4: each command here was ALLOWed by the lexer -- an off-tracker
    issue write that was never classified as one. Grouped by cause."""

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def assertAllDenied(self, *cmds):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                r = self.ev(shell(cmd))
                self.assertEqual(r["result"], "DENY", (cmd, r))

    def assertAllAllowed(self, *cmds):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                r = self.ev(shell(cmd))
                self.assertEqual(r["result"], "ALLOW", (cmd, r))

    def test_api_endpoint_with_leading_slash_or_full_url(self):
        self.assertAllDenied(
            "gh api -X POST /repos/cust/r/issues -f title=x",
            "gh api -X POST https://api.github.com/repos/cust/r/issues -f title=x",
            "gh api -X POST repositories/123/issues -f title=x",
            f"gh api -X POST repos/{TRACKER_SLUG}/../../cust/r/issues -f title=x")
        self.assertAllAllowed(f"gh api -X POST /repos/{TRACKER_SLUG}/issues -f title=x")

    def test_api_value_flags_no_longer_hide_the_endpoint(self):
        self.assertAllDenied(
            "gh api -q .id -X POST repos/cust/r/issues -f title=x",
            "gh api --hostname github.com -X POST repos/cust/r/issues -f title=x",
            "gh api -p squirrel-girl -X POST repos/cust/r/issues -f title=x",
            "gh api -Xpost repos/cust/r/issues -f title=x")
        self.assertAllDenied("gh api --not-a-gh-flag repos/cust/r/issues")

    def test_api_input_switches_to_post(self):
        self.assertAllDenied("gh api repos/cust/r/issues --input body.json",
                             "gh api repos/cust/r/issues --input=-")

    def test_api_write_to_a_shell_rewritten_endpoint(self):
        self.assertAllDenied('gh api -X POST "repos/cust/r/$KIND" -f title=x',
                             'gh api -X "$M" repos/cust/r/issues -f title=x')

    def test_graphql_document_the_fence_cannot_read(self):
        self.assertAllDenied("gh api graphql -F query=@q.graphql",
                             "gh api graphql --input q.json",
                             'gh api graphql -f query="$Q"')

    def test_bare_gh_fed_by_xargs(self):
        self.assertAllDenied(
            "printf 'issue create -R cust/r -t x' | xargs gh",
            "printf 'create -R cust/r -t x' | xargs gh issue",
            f"echo '--repo cust/r' | xargs gh issue create -R {TRACKER_SLUG} -t x",
            "find . -name x -exec gh {} +")
        self.assertAllAllowed("gh", "gh --version", "which gh", "command -v gh",
                              "xargs gh issue view")

    def test_gh_word_quoting_and_expansion_tricks(self):
        self.assertAllDenied(
            "$'gh' issue create -R cust/r -t x",
            "$'g\\x68' issue create -R cust/r -t x",
            "${G:-gh} issue create -R cust/r -t x",
            "/usr/bin/g[h] issue create -R cust/r -t x",
            "/usr/bin/g? issue create -R cust/r -t x",
            "{gh,} issue create -R cust/r -t x",
            "sudo -u x $G issue create -t x")
        self.assertAllAllowed(
            f"gh issue create -R {TRACKER_SLUG} -t x -b $'line1\\nline2'",
            "rm -f *.pyc", "ls *")

    def test_commands_that_run_a_string(self):
        self.assertAllDenied(
            "env -S 'gh issue create -R cust/r -t x'",
            "env -iS'gh issue create -R cust/r -t x'",
            "env --split-string='gh issue create -R cust/r -t x'",
            "C='gh issue create -R cust/r -t x'; $C",
            "C='gh issue create -R cust/r -t x'; eval \"$C\"",
            "watch 'gh issue create -R cust/r -t x'",
            "git -c 'alias.x=!gh issue create -R cust/r -t x' x")

    def test_shell_programs_read_from_input(self):
        self.assertAllDenied(
            "printf 'gh issue create -R cust/r -t x' | sh",
            "printf 'gh issue create -R cust/r -t x' | bash -s",
            "bash <<< 'gh issue create -R cust/r -t x'",
            "echo 'gh issue create -R cust/r -t x' | xargs -0 bash -c",
            "source /dev/stdin")
        self.assertAllAllowed("bash script.sh", "bash --version",
                              ". venv/bin/activate", "bash -lc 'echo hi'")

    def test_comment_and_redirection_handling(self):
        self.assertAllDenied(
            "echo a#; gh issue create -R cust/r -t x",
            "gh >/dev/null issue create -R cust/r -t x",
            "gh 2>/dev/null issue create -R cust/r -t x",
            "gh issue \\\ncreate -R cust/r -t x")
        self.assertAllAllowed("echo hi # gh issue create -R cust/r",
                              "go test ./... 2>&1 | tail -5")

    def test_substitutions_inside_words_and_here_documents(self):
        self.assertAllDenied(
            'echo "a $(gh issue create -R cust/r -t x)"',
            "cat <<EOF\n$(gh issue create -R cust/r -t x)\nEOF",
            "bash <<'EOF'\ngh issue create -R cust/r -t x\nEOF")
        # a quoted here-doc body is data: code fences and shell words in a
        # commit message must not read as commands
        self.assertAllAllowed(
            "git commit -m \"$(cat <<'EOF'\nfix: x\n\n```bash\nsh\nsource x\n"
            "gh issue create -R cust/r\n```\nEOF\n)\"",
            "cat <<-EOF > f\n\tsh\n\tEOF\nls")

    def test_issue_url_argument_overrides_repo(self):
        self.assertAllDenied(
            f"gh issue close https://github.com/cust/r/issues/5 -R {TRACKER_SLUG}",
            f"gh issue close 5 -R {TRACKER_SLUG} --duplicate-of https://github.com/cust/r/issues/1",
            f"gh issue develop 5 -R {TRACKER_SLUG} --branch-repo cust/r",
            f"gh issue edit 5 -R {TRACKER_SLUG} --not-a-gh-flag x")
        self.assertAllAllowed(
            f"gh issue close https://github.com/{TRACKER_SLUG}/issues/5 -R {TRACKER_SLUG}",
            f"gh issue comment 5 -R {TRACKER_SLUG} --body 'see https://github.com/cust/r/issues/3'")


class IssueFenceVariableIndirection(TrackerTestCase):
    """`G=gh; $G issue create` -- a local shell-variable alias for the
    literal word `gh` -- is the same defect CLASS as ADR-0085's `gh "pr"
    "merge"` quoting evasion this module's own docstring says it does not
    repeat. `_build_var_aliases`/`_resolve_word` close the specific,
    reproduced shape: a `NAME=VALUE` assignment earlier in the SAME
    command string, referenced later via a bare `$NAME`/`${NAME}`,
    including one level of chaining. What remains open (a `$NAME`
    resolving from the ambient environment, never assigned in this same
    command) is named in the module's own DISCLOSED LIMITS, not silently
    dropped.
    """

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def test_variable_aliased_gh_denies_without_repo(self):
        r = self.ev(shell("G=gh; $G issue create -t x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])

    def test_variable_aliased_gh_pinned_to_tracker_allows(self):
        """Proves the alias resolves into the SAME matrix, not a blanket
        deny: correctly `--repo`-pinned, it is allowed exactly like a
        bare `gh` call would be."""
        r = self.ev(shell(f"G=gh; $G issue create -t x --repo {TRACKER_SLUG}"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_variable_aliased_gh_wrong_repo_denies(self):
        r = self.ev(shell(
            "G=gh; $G issue create -t x --repo customer-org/customer-repo"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])

    def test_braced_variable_form_is_also_resolved(self):
        r = self.ev(shell("G=gh; ${G} issue create -t x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])

    def test_one_level_of_chained_aliasing_is_resolved(self):
        r = self.ev(shell("G=gh; H=$G; $H issue create -t x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])
        allowed = self.ev(shell(
            f"G=gh; H=$G; $H issue create -t x --repo {TRACKER_SLUG}"))
        self.assertEqual(allowed["result"], "ALLOW", allowed)

    def test_plain_assignment_alone_is_not_misread_as_a_gh_call(self):
        """A bare `G=gh` assignment, with no later `$G` reference, must
        stay inert -- not itself swallowed as an (empty, harmless-looking)
        `gh` invocation start the way it was before this fix, which is
        exactly what let the REAL invocation two tokens later go
        unexamined."""
        self.assertEqual(self.ev(shell("G=gh; echo hi"))["result"], "ALLOW")
        self.assertEqual(
            self.ev(shell("G=gh; git status"))["result"], "ALLOW")

    def test_env_prefixed_unrelated_command_is_not_a_false_positive(self):
        """The externally-observable half of the fix above: in REAL shell
        semantics, `X=gh issue create -t y` (no separator) does not call
        `gh` at all -- it runs a command literally named `issue`, with
        env var `X=gh` set, which would fail as `issue: command not
        found`. Before this fix, `_normalize_word` unconditionally
        stripped the `NAME=` prefix from `X=gh`, so it compared equal to
        `gh`, wrongly started a `gh` argv there, and swallowed the
        following `issue create -t y` tokens into it -- producing a false
        `tracker_fence_issue_write_off_tracker` DENY on a command that
        never invokes `gh` in the first place."""
        r = self.ev(shell("X=gh issue create -t y"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_alias_never_assigned_in_this_command_is_denied_not_resolved(self):
        """A `$NAME` never assigned WITHIN this command string cannot be
        resolved -- the module cannot see the ambient environment. This
        used to ALLOW `$G issue create` with G exported by an earlier call
        (audit C4); an unresolvable command word followed by `issue`/`api`
        now DENIES. The same word before an unrelated subcommand-shaped
        tail is not ours to judge."""
        r = self.ev(shell("$UNSET_ALIAS issue create -t x"))
        self.assertEqual(r["result"], "DENY", r)
        self.assertIn("tracker_fence_unresolvable_gh", r["reason"])
        self.assertEqual(self.ev(shell('"$CTL" status'))["result"], "ALLOW")
        self.assertEqual(self.ev(shell('"$CTL" --db x task ready'))["result"],
                         "ALLOW")


class IssueFenceEnvOverride(TrackerTestCase):
    """`GH_REPO`/`GH_HOST` are gh's own documented environment overrides
    for its target repository/host, re-resolving what `--repo`/`-R` claims
    out from under it. `bin/nightshift-file-issue` already refuses both by
    name; this class pins that the FENCE's own argv classification does
    too, closing an asymmetry between the two halves of this fix rather
    than leaving it as a disclosed gap only the wrapper's side covered."""

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def test_gh_host_prefix_denies_even_with_matching_repo(self):
        r = self.ev(shell(
            f"GH_HOST=ghe.customer.com gh issue create --repo {TRACKER_SLUG} -t x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_env_override_forbidden", r["reason"])
        self.assertIn("GH_HOST", r["reason"])

    def test_env_wrapped_gh_repo_prefix_denies_even_with_matching_repo(self):
        r = self.ev(shell(
            f"env GH_REPO=cust/repo gh issue create --repo {TRACKER_SLUG} -t x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_env_override_forbidden", r["reason"])
        self.assertIn("GH_REPO", r["reason"])

    def test_gh_api_with_env_override_denies_even_with_matching_endpoint(self):
        owner, name = TRACKER_SLUG.split("/")
        r = self.ev(shell(
            f"GH_HOST=ghe.customer.com gh api -X POST repos/{owner}/{name}/issues -f title=x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_env_override_forbidden", r["reason"])

    def test_env_override_separated_by_a_separator_is_not_a_false_positive(self):
        """The override must apply to THIS invocation only -- an earlier,
        SEPARATE statement setting `GH_HOST` must not taint a later `gh`
        call it does not actually prefix in real shell semantics."""
        r = self.ev(shell(
            f"GH_HOST=ghe.customer.com true; gh issue create --repo {TRACKER_SLUG} -t x"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_env_override_does_not_affect_read_verbs_or_other_subcommands(self):
        """Scoped to issue/api WRITES only -- reads and unrelated `gh`
        subcommands are not what the tracker binding governs, and must
        stay unaffected even when prefixed by one of these variables."""
        for cmd in (
            "GH_HOST=ghe.customer.com gh issue list",
            "GH_HOST=ghe.customer.com gh pr list",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.ev(shell(cmd))["result"], "ALLOW")

    def test_env_override_without_repo_still_reports_off_tracker_reason(self):
        """When there is no --repo at all, the pre-existing off-tracker
        DENY already fires; the env-override reason is specifically for
        the case a --repo IS present and matches, which is the shape that
        would otherwise have been wrongly ALLOWed."""
        r = self.ev(shell("GH_REPO=cust/repo gh issue create -t x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])


# ---------------------------------------------------------------------------
# `gh api` / `gh api graphql`
# ---------------------------------------------------------------------------

class IssueFenceApiSurface(TrackerTestCase):

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def test_api_issue_post_to_other_repo_denies(self):
        r = self.ev(shell(
            "gh api repos/customer-org/customer-repo/issues -f title=x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_issue_write_off_tracker", r["reason"])

    def test_api_issue_post_to_tracker_allows(self):
        r = self.ev(shell(f"gh api repos/{TRACKER_SLUG}/issues -f title=x"))
        self.assertEqual(r["result"], "ALLOW")

    def test_api_issue_post_via_dash_X_to_tracker_allows(self):
        r = self.ev(shell(
            f"gh api -X POST repos/{TRACKER_SLUG}/issues/1/comments "
            "-f body=hi"))
        self.assertEqual(r["result"], "ALLOW")

    def test_api_owner_repo_placeholder_denies(self):
        r = self.ev(shell("gh api repos/{owner}/{repo}/issues -f title=x"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_cwd_placeholder_endpoint", r["reason"])

    def test_graphql_create_issue_mutation_denies(self):
        cmd = ("gh api graphql -f query='mutation { "
               'createIssue(input:{repositoryId:"X"}) '
               "{ clientMutationId } }'")
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_graphql_issue_mutation", r["reason"])

    def test_graphql_create_issue_mutation_denies_even_pinned_to_tracker(self):
        # a GraphQL node id is opaque -- denied unconditionally, --repo or not
        cmd = (f"gh api graphql --repo {TRACKER_SLUG} -f query='mutation {{ "
               'createIssue(input:{repositoryId:"X"}) '
               "{ clientMutationId } }'")
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("tracker_fence_graphql_issue_mutation", r["reason"])

    def test_graphql_call_without_issue_mutation_is_unaffected(self):
        r = self.ev(shell("gh api graphql -f query='query { viewer { login } }'"))
        self.assertEqual(r["result"], "ALLOW")

    def test_api_issue_GET_is_allowed(self):
        r = self.ev(shell(f"gh api repos/{TRACKER_SLUG}/issues"))
        self.assertEqual(r["result"], "ALLOW")

    def test_api_non_issue_endpoint_is_unaffected(self):
        r = self.ev(shell(
            "gh api -X POST repos/customer-org/customer-repo/pulls/1/merge"))
        self.assertEqual(r["result"], "ALLOW")


# ---------------------------------------------------------------------------
# Fail closed, no escape hatch
# ---------------------------------------------------------------------------

class IssueFenceNoEscapeHatch(TrackerTestCase):
    """Mirrors `tests/test_external_repo_merge_fence.py`'s
    `test_unaddressable_scope_denies_no_escape_hatch` (AC-8) exactly, INCLUDING
    its choice not to grep the module source for the bare substring
    `FAIL_OPEN` -- the module's own docstring legitimately says "there is
    deliberately no `..._FAIL_OPEN` variable", so a naive substring scan
    would false-positive on the very sentence that documents the guarantee.
    The behavioural check below is the real proof: setting every plausible
    name for such a variable must not change a single DENY.
    """

    def test_no_fail_open_environment_variable(self):
        self._set_scope()  # no tracker declared -- every write should deny
        ev = F.tracker_issue_write_fence()
        baseline = ev(shell("gh issue create --repo a/b --title x"))
        self.assertEqual(baseline["result"], "DENY")

        plausible_fail_open_names = (
            "NIGHTSHIFT_TRACKER_FENCE_FAIL_OPEN",
            "NIGHTSHIFT_ISSUE_FENCE_FAIL_OPEN",
            "NIGHTSHIFT_SEMAPHORE_FAIL_OPEN",
            "NIGHTSHIFT_MERGE_FENCE_FAIL_OPEN",
            "TRACKER_FENCE_FAIL_OPEN",
        )
        saved = {}
        try:
            for name in plausible_fail_open_names:
                saved[name] = os.environ.get(name)
                os.environ[name] = "1"
            r = ev(shell("gh issue create --repo a/b --title x"))
            self.assertEqual(r["result"], "DENY")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


# ---------------------------------------------------------------------------
# The sanctioned wrapper
# ---------------------------------------------------------------------------

class FileIssueWrapper(TrackerTestCase):

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()

        self.shim_dir = tempfile.mkdtemp(prefix="nightshift-gh-shim-")
        self.addCleanup(shutil.rmtree, self.shim_dir, ignore_errors=True)
        self.record_path = os.path.join(self.shim_dir, "record.txt")
        shim = os.path.join(self.shim_dir, "gh")
        with open(shim, "w") as f:
            f.write("#!/usr/bin/env bash\n"
                    "printf '%s\\n' \"$@\" > \"$GH_SHIM_RECORD\"\n"
                    "echo https://github.com/x/y/issues/1\n")
        os.chmod(shim, 0o755)

        self.checkout = _make_customer_checkout()
        self.addCleanup(shutil.rmtree, self.checkout, ignore_errors=True)

    def _run_wrapper(self, *args, env_overrides=None):
        env = dict(os.environ)
        env["PATH"] = self.shim_dir + os.pathsep + env.get("PATH", "")
        env["GH_SHIM_RECORD"] = self.record_path
        env.pop("GH_REPO", None)
        env.pop("GH_HOST", None)
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(FILE_ISSUE_WRAPPER), *args],
            cwd=self.checkout, capture_output=True, text=True, env=env)

    def test_wrapper_passes_resolved_tracker_as_repo(self):
        p = self._run_wrapper("create", "--title", "hello")
        self.assertEqual(p.returncode, 0, p.stderr)
        recorded = Path(self.record_path).read_text().splitlines()
        self.assertIn("--repo", recorded)
        idx = recorded.index("--repo")
        self.assertEqual(recorded[idx + 1], TRACKER_SLUG)
        self.assertEqual(recorded[:2], ["issue", "create"])

    def test_wrapper_refuses_caller_supplied_repo(self):
        p = self._run_wrapper("create", "--repo", "evil/repo", "--title", "x")
        self.assertEqual(p.returncode, 3)
        self.assertIn("tracker_repo_override_forbidden", p.stderr)
        self.assertFalse(os.path.exists(self.record_path))

    def test_wrapper_refuses_caller_supplied_repo_equals_form(self):
        p = self._run_wrapper("create", "--repo=evil/repo", "--title", "x")
        self.assertEqual(p.returncode, 3)
        self.assertIn("tracker_repo_override_forbidden", p.stderr)
        self.assertFalse(os.path.exists(self.record_path))

    def test_wrapper_refuses_caller_supplied_repo_dash_R_attached_form(self):
        """`-R` also accepts its value ATTACHED, no space
        (`-Rcustomer-org/customer-repo`) -- gh's own flag parser and the
        fence's own `_find_repo_flag` both treat `-R<value>` identically to
        `-R <value>`. A real bypass: without this check the wrapper execs
        `gh issue create --repo <resolved tracker> -Rcustomer-org/
        customer-repo ...`, and gh's last-flag-wins rule sends the ticket
        to the customer repo, silently, while the wrapper's own contract
        ("Caller passes their own --repo/-R -> exit 3") is violated."""
        p = self._run_wrapper("create", "-Rcustomer-org/customer-repo",
                              "--title", "x")
        self.assertEqual(p.returncode, 3)
        self.assertIn("tracker_repo_override_forbidden", p.stderr)
        self.assertFalse(os.path.exists(self.record_path))

    def test_wrapper_refuses_every_spelling_of_another_repository(self):
        """Audit low: the token scan missed `develop --branch-repo` (the
        branch lands in another repository) and bundled shorthand such as
        `-cR<repo>`, which pflag reads as `--checkout --repo <repo>`."""
        for args in (("develop", "5", "--branch-repo", "victim/repo"),
                     ("develop", "5", "--branch-repo=victim/repo"),
                     ("develop", "5", "-cRvictim/repo"),
                     ("develop", "5", "-cR", "victim/repo"),
                     ("edit", "5", "-R=victim/repo"),
                     ("create", "--title", "x", "-R", "victim/repo")):
            with self.subTest(args=args):
                p = self._run_wrapper(*args)
                self.assertEqual(p.returncode, 3, p.stderr)
                self.assertIn("tracker_repo_override_forbidden", p.stderr)
                self.assertFalse(os.path.exists(self.record_path))

    def test_wrapper_refuses_flags_and_references_outside_the_verb(self):
        for args, reason in (
                (("close", "5", "--add-label", "x"), "tracker_flag_not_allowed"),
                (("pin", "5", "--yes"), "tracker_flag_not_allowed"),
                (("close", "https://github.com/victim/repo/issues/5"),
                 "tracker_issue_reference_invalid"),
                (("close", "5", "--duplicate-of",
                  "https://github.com/victim/repo/issues/1"),
                 "tracker_issue_reference_invalid"),
                (("create", "5", "--title", "x"),
                 "tracker_issue_reference_invalid")):
            with self.subTest(args=args):
                p = self._run_wrapper(*args)
                self.assertEqual(p.returncode, 3, p.stderr)
                self.assertIn(reason, p.stderr)
                self.assertFalse(os.path.exists(self.record_path))

    def test_wrapper_forwards_allowed_bundles_verbatim(self):
        """`close 5 -cRvictim/repo` is a closing COMMENT under pflag, not a
        repository; per-verb tables are what tell the two apart."""
        for args in (("close", "5", "-cRvictim/repo"),
                     ("edit", "#5", "--remove-label", "needs-dor",
                      "--add-label", "state:ready"),
                     ("develop", "5", "-c", "-nwip")):
            with self.subTest(args=args):
                p = self._run_wrapper(*args)
                self.assertEqual(p.returncode, 0, p.stderr)
                recorded = Path(self.record_path).read_text().splitlines()
                self.assertEqual(recorded[4:], list(args[1:]))
                os.unlink(self.record_path)

    def test_wrapper_refuses_when_binding_absent(self):
        p = self._run_wrapper("create", "--title", "x",
                              env_overrides={"NIGHTSHIFT_REPOSITORY_ID": "999999999"})
        self.assertEqual(p.returncode, 3)
        self.assertIn("tracker_unbound", p.stderr)
        self.assertFalse(os.path.exists(self.record_path))

    def test_wrapper_refuses_gh_repo_environment_override(self):
        p = self._run_wrapper("create", "--title", "x",
                              env_overrides={"GH_REPO": "evil/repo"})
        self.assertEqual(p.returncode, 3)
        self.assertIn("tracker_repo_override_forbidden", p.stderr)
        self.assertFalse(os.path.exists(self.record_path))


# ---------------------------------------------------------------------------
# Prompt and mechanism agree, on the four files this stream owns
# ---------------------------------------------------------------------------

class CallSitesAgreeWithMechanism(unittest.TestCase):
    """`program/agents/crew_alpha/agents/pm/config.yaml`,
    `program/agents/crew_beta/agents/beta_pm/config.yaml`,
    `program/skills/tracker/SKILL.md`, and `program/config.yaml`'s own
    DoR/heartbeat prose no longer instruct a bare `gh issue <write-verb>`
    and name the sanctioned wrapper instead; `bin/nightshift-tracker-
    init.sh` pins an explicit `--repo` on its own two issue-write lines.

    `program/config.yaml` is `platform` single-writer per `docs/owners.md`
    ("no crew owns this ... changes require an ADR") -- ADR-0088 is that
    ADR, authorizing both the prompt rewrite this class checks below and
    the bundle-root policy declaration `ConfigDeclaresTheFence` checks. A
    PRIOR version of this suite (round 1 of this stream) left that file's
    four bare lines and the missing declaration as a disclosed, asserted-
    open gap, because closing it required an edit outside that round's
    file ownership. ADR-0088 grants that ownership for this specific,
    narrow change; the gap is closed here, not merely re-disclosed.
    """

    OWNED_PROMPT_FILES = (
        REPO / "program/agents/crew_alpha/agents/pm/config.yaml",
        REPO / "program/agents/crew_beta/agents/beta_pm/config.yaml",
        REPO / "program/skills/tracker/SKILL.md",
        REPO / "program/config.yaml",
    )
    TRACKER_INIT = REPO / "bin/nightshift-tracker-init.sh"

    WRITE_VERB_RE = re.compile(
        r"`?gh issue (create|edit|comment|close|reopen|delete|transfer|"
        r"pin|unpin|lock|unlock|develop)\b")

    def test_regex_sanity_would_catch_a_bare_write(self):
        # Proves the scan below is not vacuous: the SAME regex, run against
        # a known-bad fixture string, must match.
        self.assertTrue(self.WRITE_VERB_RE.search(
            'gh issue create --title "x" --label "needs-dor"'))
        self.assertTrue(self.WRITE_VERB_RE.search(
            "gh issue edit <n> --remove-label needs-dor"))

    def test_no_bare_gh_issue_write_instruction_remains_in_prompts(self):
        offenders = []
        for path in self.OWNED_PROMPT_FILES:
            text = path.read_text()
            for m in self.WRITE_VERB_RE.finditer(text):
                offenders.append(f"{path.relative_to(REPO)}: {m.group(0)!r}")
        self.assertEqual(offenders, [], offenders)

    def test_wrapper_is_named_in_every_rewritten_call_site(self):
        for path in self.OWNED_PROMPT_FILES:
            text = path.read_text()
            self.assertIn("nightshift-file-issue", text,
                          f"{path} does not mention the sanctioned wrapper")

    def test_tracker_init_issue_writes_carry_explicit_repo(self):
        """`bin/nightshift-tracker-init.sh` is an OPERATOR-run bootstrap
        script (see its own header), not something the Omnigent tool-call
        policy layer intercepts, so it is not rewritten to the wrapper --
        but its two issue-write lines gain an explicit `--repo "$OWNER/
        $REPO"` for consistency and defense in depth, since `$OWNER`/`$REPO`
        are already derived from the operator-supplied `REPO_DIR` argument,
        not a cwd accident."""
        text = self.TRACKER_INIT.read_text()
        offenders = [
            m.group(0) for m in self.WRITE_VERB_RE.finditer(text)
            if "--repo" not in text[m.start():m.start() + 200]
        ]
        self.assertEqual(offenders, [], offenders)
        self.assertIn('gh issue create --repo', text)


class ConfigDeclaresTheFence(unittest.TestCase):
    """ADR-0088: `tracker_issue_write_fence` is declared at the bundle
    root, the same attachment point `tests/test_external_repo_merge_
    fence.py::ReleaseConfigAgreesWithMechanism.test_fence_declared_at_
    bundle_root` already pins for `external_repo_merge_fence` -- #118
    proved a per-seat declaration never reaches the engine, so a copy on
    any one seat's own config.yaml would be exactly as dead as the
    never-wired state this class replaces."""

    QUALIFIED = "nightshift_issue_fence.tracker_issue_write_fence"

    def test_fence_declared_at_bundle_root(self):
        program_config = (REPO / "program" / "config.yaml").read_text()
        self.assertIn(self.QUALIFIED, program_config)

    def test_fence_not_declared_on_any_seat(self):
        # The BARE function name (`tracker_issue_write_fence`) legitimately
        # appears in seat prose (the fence module's own docstring is quoted
        # nowhere on a seat, but a future prompt edit explaining the DENY
        # by name should not itself be mistaken for a declaration) -- only
        # the QUALIFIED `function.path:` form is what a real declaration
        # writes, so that is what this checks, same distinction
        # `ReleaseConfigAgreesWithMechanism` draws for the merge fence.
        agents_dir = REPO / "program" / "agents"
        offenders = [str(p.relative_to(REPO)) for p in agents_dir.rglob("config.yaml")
                     if self.QUALIFIED in p.read_text()]
        self.assertEqual(offenders, [],
                         "tracker_issue_write_fence declared on a seat -- #118: "
                         "per-seat policy declarations never load")


class IssueTransferIsRefusedOutright(TrackerTestCase):
    """`gh issue transfer <number> <destination-repo>` moves an issue OUT of
    the repository it lives in, and the destination is a bare POSITIONAL.

    The fence's --repo/-R check binds only the SOURCE, so before this refusal
    existed `gh issue transfer 5 customer/repo -R <tracker>` was ALLOWED: a
    correctly-pinned command that lands a Nightshift issue in a customer's
    tracker. `bin/nightshift-file-issue` carried the same hole and would have
    done it through the SANCTIONED path.

    The refusal is outright rather than a destination comparison because there
    is no destination that could be permitted: a transfer whose destination is
    the tracker either has source == tracker (a no-op) or a non-tracker source,
    which the --repo check already denies. A destination branch could never say
    yes, so a branch that pretends to decide would be worse than a refusal.
    """

    def setUp(self):
        super().setUp()
        self._declare()
        self._set_scope()
        self.ev = F.tracker_issue_write_fence()

    def _assert_transfer_denied(self, cmd):
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "DENY", cmd)
        self.assertIn("tracker_fence_transfer_forbidden", r["reason"], cmd)

    def test_transfer_denied_even_when_source_is_pinned_to_the_tracker(self):
        # THE ORIGINAL DEFECT: correctly pinned source, unbound destination.
        self._assert_transfer_denied(
            f"gh issue transfer 5 customer-org/customer-repo "
            f"--repo {TRACKER_SLUG}")

    def test_transfer_denied_with_short_repo_flag(self):
        self._assert_transfer_denied(
            f"gh issue transfer 5 customer-org/customer-repo -R {TRACKER_SLUG}")

    def test_transfer_denied_with_repo_before_the_verb(self):
        self._assert_transfer_denied(
            f"gh -R {TRACKER_SLUG} issue transfer 5 customer-org/customer-repo")

    def test_transfer_denied_bare_from_a_customer_shaped_checkout(self):
        checkout = _make_customer_checkout()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        self._chdir(checkout)
        self._assert_transfer_denied(
            "gh issue transfer 5 customer-org/customer-repo")

    def test_transfer_denied_even_when_destination_IS_the_tracker(self):
        # No destination is permitted, including the tracker itself -- that
        # case is either a no-op or has a source the --repo check refuses.
        self._assert_transfer_denied(
            f"gh issue transfer 5 {TRACKER_SLUG} --repo {TRACKER_SLUG}")

    def test_transfer_denied_through_a_shell_wrapper(self):
        self._assert_transfer_denied(
            f"bash -lc \"gh issue transfer 5 cust/repo -R {TRACKER_SLUG}\"")

    def test_other_write_verbs_are_unaffected(self):
        # The refusal must be scoped to `transfer` alone -- every other write
        # verb still ALLOWs when pinned to the bound tracker.
        for verb, args in (("create", "--title x"),
                           ("comment", "5 --body hi"),
                           ("close", "5"),
                           ("reopen", "5"),
                           ("edit", "5 --title y")):
            with self.subTest(verb=verb):
                r = self.ev(shell(
                    f"gh issue {verb} {args} --repo {TRACKER_SLUG}"))
                self.assertEqual(r["result"], "ALLOW", verb)

    def test_read_verbs_are_unaffected(self):
        for cmd in ("gh issue list", "gh issue view 5"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.ev(shell(cmd))["result"], "ALLOW", cmd)

    def test_the_wrapper_does_not_offer_transfer_either(self):
        # `bin/nightshift-file-issue` must not forward an unbound destination.
        # A wrapper that does is not a narrower surface than raw `gh`.
        wrapper = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bin", "nightshift-file-issue")
        with open(wrapper, encoding="utf-8") as fh:
            source = fh.read()
        verbs_line = [ln for ln in source.splitlines()
                      if ln.startswith("_VERBS =") or ln.strip().startswith('"pin"')]
        self.assertTrue(verbs_line, "could not locate _VERBS in the wrapper")
        self.assertNotIn('"transfer"', "\n".join(verbs_line),
                         "the wrapper still offers `transfer`, which forwards "
                         "an unbound destination repository")


if __name__ == "__main__":
    unittest.main()
