"""ADR-0085 -- external-repository operating mode: PR-only, never merge.

Mockless: real sqlite3 over real authority.db files in real tempdirs, and a
real subprocess for the standalone CLI (`bin/nightshift_authority.py`).  The
tool-call policy (`deploy/policies/omnipods_policies.external_repo_merge_fence`)
is imported and invoked in-process the same way tests/test_policy.py and
tests/test_authority_fence.py already exercise its siblings.

WHAT THIS SUITE PINS
====================
  OwnershipIsWriteOnce            AC-1/AC-2 -- raw UPDATE/DELETE abort, a
                                   second row is unrepresentable, and the
                                   declare verb's own conflict/idempotence
                                   rule sits ABOVE that database floor.
  OwnershipSchema                 AC-3 -- the type-less repository_id column
                                   genuinely refuses a string, unlike the
                                   INTEGER-declared sibling table.
  OwnershipFailsClosed            AC-4 -- absent / unbootstrapped /
                                   unaddressable / declared-self, and their
                                   `source` values do not collapse into one
                                   another.
  MergePreflightCLI                AC-5 -- the real CLI, in a real
                                   subprocess, exits 3 with the named reason
                                   code on every non-self classification and
                                   0 on self.
  ReleaseControlPlaneInterface     Documents a genuine gap between AC-5's
                                   literal statement (`nightshift-ctl repo
                                   merge-preflight`) and what was actually
                                   shipped (a standalone script). See the
                                   class docstring.
  MergeFencePolicy                 AC-6/AC-7/AC-8 -- the tool-call DENY/ALLOW
                                   matrix, the negative controls, and fail-
                                   closed-with-no-escape-hatch.
  MergeFenceRecognisesGraphQLMerge Pins the `gh api graphql` +
                                   `mergePullRequest` shape as a recognised
                                   merge call (previously a false-ALLOW: it
                                   matched `_GH_API` but never
                                   `pulls/<n>/merge`, so it was never
                                   classified as a merge call at all).
  MergeFenceRegexGaps              Not an AC. The evasions a regex on the
                                   raw shell string allowed (quoting, flags
                                   before the verb, `$'gh'`, xargs, value
                                   flags, a variable pull number), each now
                                   DENIED through the lexer's argv.
  ReleaseConfigAgreesWithMechanism AC-9/AC-11 -- the release procedure names
                                   the preflight+reason on every step that
                                   mentions `gh pr merge`, and the fence is
                                   declared at the bundle root and nowhere
                                   under program/agents/.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
AUTHORITY_MODULE = BIN / "nightshift_authority.py"
NIGHTSHIFT_CTL = BIN / "nightshift-ctl"

sys.path.insert(0, str(BIN))
import nightshift_authority as auth  # noqa: E402

sys.path.insert(0, str(REPO / "deploy" / "policies"))
import omnipods_policies as P  # noqa: E402

T0 = 3_000_000.0


def shell(cmd):
    return {"type": "tool_call",
            "data": {"name": "sys_os_shell", "arguments": {"command": cmd}}}


def run_cli(*argv):
    """Drive the real `bin/nightshift_authority.py` CLI in a subprocess --
    same posture as tests/ctlutil.py's `Ctl.run` for `nightshift-ctl`."""
    p = subprocess.run([sys.executable, str(AUTHORITY_MODULE), *argv],
                       capture_output=True, text=True)
    body = None
    if p.stdout.strip():
        body = json.loads(p.stdout.strip().splitlines()[-1])
    return p.returncode, body


class OwnershipTestCase(unittest.TestCase):
    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="nightshift-ownership-")
        os.chmod(self.state_root, 0o700)
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.repository_id = 777
        self._clear_env()

    def _clear_env(self):
        names = (auth.ENV_STATE_ROOT, auth.ENV_REPOSITORY_ID)
        for name in names:
            os.environ.pop(name, None)
        self.addCleanup(lambda: [os.environ.pop(n, None) for n in names])

    def _cx(self):
        """Bootstraps the schema (via the module's own connect()) without
        declaring anything."""
        cx = auth.connect(self.state_root, self.repository_id)
        self.addCleanup(cx.close)
        return cx

    def _declared(self, ownership="self", repository_id=None, t=T0):
        cx = self._cx()
        auth.declare_repository_ownership(
            cx, repository_id=repository_id or self.repository_id,
            ownership=ownership, declared_by="test-operator", t=t)
        return cx

    def _raw_cx(self):
        """A connection that never goes through the module's Python API --
        exactly what AC-1 requires: the database itself must refuse, not
        application code standing in front of it."""
        path = auth.bootstrap_path(self.state_root, self.repository_id)
        cx = sqlite3.connect(path)
        self.addCleanup(cx.close)
        return cx


class OwnershipIsWriteOnce(OwnershipTestCase):

    def test_raw_update_aborts(self):
        self._declared(ownership="self")
        raw = self._raw_cx()
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            raw.execute(
                "UPDATE repository_ownership SET ownership='external' WHERE id=1")
        self.assertIn("write-once", str(ctx.exception))
        cx = self._cx()
        self.assertEqual(auth.read_repository_ownership(cx)["ownership"], "self")

    def test_raw_delete_aborts(self):
        self._declared(ownership="self")
        raw = self._raw_cx()
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            raw.execute("DELETE FROM repository_ownership WHERE id=1")
        self.assertIn("write-once", str(ctx.exception))
        cx = self._cx()
        self.assertIsNotNone(auth.read_repository_ownership(cx))

    def test_second_row_refused(self):
        self._declared(ownership="self")
        raw = self._raw_cx()
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO repository_ownership(id, repository_id, ownership, "
                "declared_by, recorded_at) VALUES(2, 888, 'external', 'x', 1.0)")

    def test_conflicting_redeclare_refused_same_redeclare_idempotent(self):
        code, body = run_cli(
            "ownership-declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--ownership", "self", "--declared-by", "op1")
        self.assertEqual(code, 0, body)
        recorded_at = body["recorded_at"]

        # a DIFFERENT ownership on re-declare is refused, not silently applied
        code, body = run_cli(
            "ownership-declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--ownership", "external", "--declared-by", "op2")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "repository_ownership_already_declared")

        # the SAME ownership on re-declare is idempotent -- same recorded_at
        code, body = run_cli(
            "ownership-declare", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id),
            "--ownership", "self", "--declared-by", "op3")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["recorded_at"], recorded_at)
        self.assertEqual(body["declared_by"], "op1")  # unchanged, not op3


class OwnershipSchema(OwnershipTestCase):

    def test_string_repository_id_refused(self):
        self._cx()  # bootstraps schema; declares nothing
        raw = self._raw_cx()
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO repository_ownership(id, repository_id, ownership, "
                "declared_by, recorded_at) VALUES(1, '5', 'self', 'op', 1.0)")
        # cannot pass vacuously: a plain int MUST be accepted by the same CHECK
        raw.execute(
            "INSERT INTO repository_ownership(id, repository_id, ownership, "
            "declared_by, recorded_at) VALUES(1, 5, 'self', 'op', 1.0)")
        row = raw.execute(
            "SELECT repository_id, typeof(repository_id) "
            "FROM repository_ownership WHERE id=1").fetchone()
        self.assertEqual(row[0], 5)
        self.assertEqual(row[1], "integer")


class OwnershipFailsClosed(OwnershipTestCase):

    def test_absent_registry_is_external(self):
        result = auth.classify_repository(self.state_root, self.repository_id)
        self.assertEqual(result["ownership"], "external")
        self.assertEqual(result["source"], "registry_absent")

    def test_unbootstrapped_registry_is_external(self):
        self._cx()  # file exists, schema run, nothing ever declared
        result = auth.classify_repository(self.state_root, self.repository_id)
        self.assertEqual(result["ownership"], "external")
        self.assertEqual(result["source"], "registry_unbootstrapped")

    def test_unaddressable_scope_is_external(self):
        for state_root, repository_id in (
            (None, self.repository_id),
            (self.state_root, None),
            (self.state_root, -5),
            (self.state_root, "not-an-int"),
        ):
            result = auth.classify_repository(state_root, repository_id)
            self.assertEqual(result["ownership"], "external")
            self.assertEqual(result["source"], "unaddressable",
                             f"state_root={state_root!r} repository_id={repository_id!r}")

    def test_declared_self_is_self(self):
        self._declared(ownership="self")
        result = auth.classify_repository(self.state_root, self.repository_id)
        self.assertEqual(result["ownership"], "self")
        self.assertEqual(result["source"], "registry")

    def test_four_sources_are_pairwise_distinct(self):
        """Guards against the four cases collapsing into fewer than four --
        a bug that individual-case assertions above would not by themselves
        catch if two branches happened to agree."""
        self._cx()
        sources = {
            auth.classify_repository(None, self.repository_id)["source"],
            auth.classify_repository(self.state_root,
                                     self.repository_id + 1)["source"],  # absent
            auth.classify_repository(self.state_root, self.repository_id)["source"],
        }
        self.assertEqual(sources, {"unaddressable", "registry_absent",
                                   "registry_unbootstrapped"})


class OwnershipAndBackendAreIndependentAxes(OwnershipTestCase):
    """ADR-0085's own Decision section claims: 'a future reader must not
    read them as one flag, and this decision's schema comment and module
    docstring say so explicitly, alongside a test asserting the two vary
    independently.' No such test exists anywhere in this diff or the
    pre-existing suite -- `git grep -n "vary independently"` finds only the
    ADR's own prose, in both the doc and the module docstring. This is that
    test, since the claim should not stand unverified.
    """

    def test_active_backend_and_ownership_vary_independently(self):
        cx = self._cx()
        auth.init_binding(cx, repository_id=self.repository_id, t=T0)
        auth.declare_repository_ownership(
            cx, repository_id=self.repository_id, ownership="self",
            declared_by="op", t=T0)
        self.assertEqual(auth.read_binding(cx)["active_backend"], "legacy")
        self.assertEqual(auth.read_repository_ownership(cx)["ownership"], "self")

        # Flip active_backend via the real ADR-0082 switch. `ownership` sits
        # in a different table with no foreign key or trigger tying it to
        # `authority_binding` -- it must not move.
        auth.begin_quiesce(cx, migration_id="mig-independent", t=T0 + 1)
        auth.record_compare_verdict(cx, migration_id="mig-independent",
                                    verdict="match", mismatch_count=0, t=T0 + 1)
        auth.switch_authority(cx, migration_id="mig-independent",
                              from_backend="legacy", to_backend="external",
                              from_epoch=1, active_leases=[], t=T0 + 2)
        self.assertEqual(auth.read_binding(cx)["active_backend"], "external")
        self.assertEqual(auth.read_repository_ownership(cx)["ownership"], "self")

        # The opposite combination is equally representable: a repository
        # this deployment does NOT own, still entirely on the legacy
        # backend (the ordinary state of a brand-new external engagement).
        other_repo = self.repository_id + 1
        cx2 = auth.connect(self.state_root, other_repo)
        self.addCleanup(cx2.close)
        auth.init_binding(cx2, repository_id=other_repo, t=T0)
        auth.declare_repository_ownership(
            cx2, repository_id=other_repo, ownership="external",
            declared_by="op", t=T0)
        self.assertEqual(auth.read_binding(cx2)["active_backend"], "legacy")
        self.assertEqual(auth.read_repository_ownership(cx2)["ownership"], "external")


class MergePreflightCLI(OwnershipTestCase):

    def test_external_repository_refuses_with_reason_code(self):
        code, body = run_cli(
            "merge-preflight", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id))
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "merge_forbidden_external_repository")
        self.assertFalse(body["ok"])

    def test_self_repository_allows(self):
        self._declared(ownership="self")
        code, body = run_cli(
            "merge-preflight", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id))
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["ownership"], "self")

    def test_unbootstrapped_repository_refuses_too(self):
        self._cx()
        code, body = run_cli(
            "merge-preflight", "--state-root", self.state_root,
            "--repository-id", str(self.repository_id))
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "merge_forbidden_external_repository")
        self.assertEqual(body["source"], "registry_unbootstrapped")


class ReleaseControlPlaneInterface(unittest.TestCase):
    """AC-5's literal STATEMENT names the interface as
    `nightshift-ctl repo merge-preflight`. AC-9's how_proven plan likewise
    expects the release config's preflight line to contain the substring
    "repo merge-preflight". Neither exists: `nightshift-ctl` has no `repo`
    subcommand group at all (confirmed below), and the release config's
    actual command is `python3 bin/nightshift_authority.py merge-preflight`
    -- a wholly separate script, not a subcommand of the control plane.

    The builder's own report calls this out as a deliberate deviation ("per
    the cross-stream ruling ... shipped the CLI as nightshift_authority.py's
    own main() instead of a nightshift-ctl repo subgroup"). This test does
    not take a position on whether that ruling was legitimate -- it only
    pins that the literal interface named in the acceptance criteria does
    not exist, so that fact is not lost.
    """

    def test_nightshift_ctl_has_no_repo_subcommand(self):
        p = subprocess.run([str(NIGHTSHIFT_CTL), "repo", "merge-preflight"],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("invalid choice", p.stderr)
        self.assertNotIn("repo", _ctl_subcommand_choices(p.stderr))

    def test_release_config_does_not_literally_say_repo_merge_preflight(self):
        text = (REPO / "program" / "agents" / "release" / "config.yaml").read_text()
        self.assertNotIn("repo merge-preflight", text)
        # what IS actually there:
        self.assertIn("nightshift_authority.py merge-preflight", text)


def _ctl_subcommand_choices(stderr_text):
    m = re.search(r"\{([^}]*)\}", stderr_text)
    return set(m.group(1).split(",")) if m else set()


class MergeFencePolicy(OwnershipTestCase):

    def setUp(self):
        super().setUp()
        self.ev = P.external_repo_merge_fence()

    def _set_scope(self, repository_id=None):
        os.environ[auth.ENV_STATE_ROOT] = self.state_root
        os.environ[auth.ENV_REPOSITORY_ID] = str(repository_id or self.repository_id)

    def test_gh_pr_merge_denied_on_external_repository(self):
        self._set_scope()
        self._declared(ownership="external")
        r = self.ev(shell('gh pr merge --merge --match-head-commit abc123'))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("merge_forbidden_external_repository", r["reason"])

    def test_gh_api_merge_denied_on_external_repository(self):
        self._set_scope()
        self._declared(ownership="external")
        r = self.ev(shell("gh api -X PUT repos/o/r/pulls/42/merge"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("merge_forbidden_external_repository", r["reason"])

    def test_self_repository_allows_merge(self):
        self._set_scope()
        self._declared(ownership="self")
        r = self.ev(shell('gh pr merge --merge --match-head-commit abc123'))
        self.assertEqual(r["result"], "ALLOW")

    def test_unrelated_shell_command_allowed_when_external(self):
        self._set_scope()
        self._declared(ownership="external")
        self.assertEqual(self.ev(shell("git status"))["result"], "ALLOW")
        self.assertEqual(self.ev(shell("gh pr create --title x --body y"))["result"],
                         "ALLOW")

    def test_unaddressable_scope_denies_no_escape_hatch(self):
        # no NIGHTSHIFT_STATE_ROOT / NIGHTSHIFT_REPOSITORY_ID at all
        r = self.ev(shell("gh pr merge --merge"))
        self.assertEqual(r["result"], "DENY")
        plausible_fail_open_names = [
            "NIGHTSHIFT_MERGE_FENCE_FAIL_OPEN",
            "NIGHTSHIFT_SEMAPHORE_FAIL_OPEN",
            "NIGHTSHIFT_AUTHORITY_FENCE_FAIL_OPEN",
            "NIGHTSHIFT_MERGE_FAIL_OPEN",
            "MERGE_FENCE_FAIL_OPEN",
        ]
        saved = {}
        try:
            for name in plausible_fail_open_names:
                saved[name] = os.environ.get(name)
                os.environ[name] = "1"
            r2 = self.ev(shell("gh pr merge --merge"))
            self.assertEqual(r2["result"], "DENY")
        finally:
            for name, v in saved.items():
                if v is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = v

    def test_malformed_repository_id_env_denies(self):
        os.environ[auth.ENV_STATE_ROOT] = self.state_root
        os.environ[auth.ENV_REPOSITORY_ID] = "not-an-integer"
        r = self.ev(shell("gh pr merge --merge"))
        self.assertEqual(r["result"], "DENY")

    def test_unreadable_registry_denies(self):
        self._set_scope()
        path = auth.bootstrap_path(self.state_root, self.repository_id)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"not a sqlite database at all")
        r = self.ev(shell("gh pr merge --merge"))
        self.assertEqual(r["result"], "DENY")


class MergeFenceEveryShellToolName(OwnershipTestCase):
    """Audit C1: claude-native seats (eng_claude, beta_eng_claude, oncall,
    platform) report the shell tool as `Bash`. The fence matched lowercase
    names only, so those seats could merge into a customer repository."""

    NAMES = ("Bash", "bash", "BASH", "sys_os_shell", "shell", "Shell",
             "terminal", "developer__shell", "run_command", "run_shell_command",
             "mcp__omnigent__sys_os_shell")

    def setUp(self):
        super().setUp()
        os.environ[auth.ENV_STATE_ROOT] = self.state_root
        os.environ[auth.ENV_REPOSITORY_ID] = str(self.repository_id)
        self._declared(ownership="external")

    @staticmethod
    def call(name, cmd):
        return {"type": "tool_call",
                "data": {"name": name, "arguments": {"command": cmd}}}

    def test_every_harness_shell_tool_is_fenced(self):
        ev = P.external_repo_merge_fence()
        for name in self.NAMES:
            with self.subTest(name=name):
                r = ev(self.call(name, "gh pr merge 1 --merge"))
                self.assertEqual(r["result"], "DENY", r)
                self.assertIn("merge_forbidden_external_repository", r["reason"])

    def test_shell_tools_argument_cannot_narrow_the_set(self):
        ev = P.external_repo_merge_fence(shell_tools=("sys_os_shell",))
        r = ev(self.call("Bash", "gh pr merge 1"))
        self.assertEqual(r["result"], "DENY", r)


class MergeFenceRecognisesGraphQLMerge(OwnershipTestCase):
    """`gh api graphql` with a `mergePullRequest` mutation is a documented,
    first-class way to merge a pull request -- and, unlike the two shapes
    the module docstring originally named, it was not merely allowed on a
    self-owned repository: it was never classified as a merge call at all,
    because it matches `_GH_API` but never `pulls/<n>/merge`. This pins the
    fix: the shape is now recognised and denied on an external repository,
    same as the other two.
    """

    def setUp(self):
        super().setUp()
        self.ev = P.external_repo_merge_fence()
        os.environ[auth.ENV_STATE_ROOT] = self.state_root
        os.environ[auth.ENV_REPOSITORY_ID] = str(self.repository_id)

    def test_graphql_merge_mutation_denied_on_external_repository(self):
        self._declared(ownership="external")
        cmd = ("gh api graphql -f query='mutation { "
               'mergePullRequest(input:{pullRequestId:"X"}) '
               "{ clientMutationId } }'")
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("merge_forbidden_external_repository", r["reason"])

    def test_graphql_merge_mutation_allowed_on_self_repository(self):
        self._declared(ownership="self")
        cmd = ("gh api graphql -f query='mutation { "
               'mergePullRequest(input:{pullRequestId:"X"}) '
               "{ clientMutationId } }'")
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "ALLOW")

    def test_graphql_call_without_merge_mutation_is_unaffected(self):
        # sanity: an ordinary, unrelated graphql query on an external repo
        # must not be swept up by the new match.
        self._declared(ownership="external")
        cmd = "gh api graphql -f query='query { viewer { login } }'"
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "ALLOW")


class MergeFenceRegexGaps(OwnershipTestCase):
    """The gaps the fence had while it matched a regex against the raw shell
    STRING, each now closed: the fence classifies the argv the ADR-0088
    lexer builds (audit C4). These tests used to assert ALLOW and document
    the gap; a fence that still let them through would be the regression.
    """

    def setUp(self):
        super().setUp()
        self.ev = P.external_repo_merge_fence()
        os.environ[auth.ENV_STATE_ROOT] = self.state_root
        os.environ[auth.ENV_REPOSITORY_ID] = str(self.repository_id)
        self._declared(ownership="external")

    def assertDenied(self, cmd):
        r = self.ev(shell(cmd))
        self.assertEqual(r["result"], "DENY", (cmd, r))
        self.assertIn("merge_forbidden_external_repository", r["reason"])

    def test_baseline_unquoted_merge_is_denied(self):
        # sanity: the ordinary case the fence is built for
        self.assertDenied("gh pr merge --merge")

    def test_quote_split_merge_command_is_denied(self):
        self.assertDenied('gh "pr" "merge" --merge')

    def test_variable_pull_number_in_gh_api_path_is_denied(self):
        self.assertDenied('n=42; gh api -X PUT "repos/o/r/pulls/$n/merge"')

    def test_flags_before_the_subcommand_or_verb(self):
        for cmd in ("gh -R o/r pr merge 1", "gh pr -R o/r merge 1",
                    "gh >/dev/null pr merge 1"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_gh_word_obfuscation(self):
        for cmd in ("$'gh' pr merge 1", "${G:-gh} pr merge 1",
                    "/usr/bin/g[h] pr merge 1", "env -S 'gh pr merge 1'",
                    "echo merge 1 | xargs gh pr", "echo pr merge 1 | xargs gh"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_api_value_flags_and_leading_slash(self):
        for cmd in ("gh api -H Accept:x -X PUT repos/o/r/pulls/1/merge",
                    "gh api --jq .sha -X PUT /repos/o/r/pulls/1/merge",
                    "gh api -X POST repos/o/r/merges -f base=main -f head=x",
                    "gh api graphql -f query='mutation { "
                    "enablePullRequestAutoMerge(input:{}) { clientMutationId } }'",
                    "gh api graphql -F query=@merge.graphql",
                    "gh repo sync o/r"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_unclassifiable_commands_count_as_merges(self):
        self.assertDenied("gh pr merge 'unbalanced")
        r = self.ev({"type": "tool_call", "data": {
            "name": "Bash", "arguments": {"command": ["gh", "pr", "merge"]}}})
        self.assertEqual(r["result"], "DENY")

    def test_reads_stay_allowed_on_an_external_repository(self):
        for cmd in ("gh pr view 1", "gh pr list", "gh api repos/o/r/pulls/1",
                    "gh pr create --title x --body y", "git status"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.ev(shell(cmd))["result"], "ALLOW")


class ReleaseConfigAgreesWithMechanism(unittest.TestCase):
    STEP_RE = re.compile(r"^\s*\d+\.\s", re.MULTILINE)

    def setUp(self):
        self.release_config_path = REPO / "program" / "agents" / "release" / "config.yaml"
        self.text = self.release_config_path.read_text()

    def _steps(self):
        starts = [m.start() for m in self.STEP_RE.finditer(self.text)]
        starts.append(len(self.text))
        return [self.text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

    def test_merge_is_conditioned_on_preflight(self):
        steps = self._steps()
        merge_steps = [s for s in steps if "gh pr merge" in s]
        # cannot pass vacuously if the step were renumbered or deleted
        self.assertTrue(merge_steps,
                        "no numbered step mentions 'gh pr merge' -- test is vacuous")
        for s in merge_steps:
            self.assertIn("merge-preflight", s)
            self.assertIn("merge_forbidden_external_repository", s)

    def test_fence_declared_at_bundle_root(self):
        # The QUALIFIED name (`omnipods_policies.external_repo_merge_fence`)
        # is what a `function.path:` declaration actually writes -- see
        # program/config.yaml's own entry a few lines below. The bare name
        # also legitimately appears in the release config's PROSE (step 7
        # explains the denial by name), so checking for the bare name on a
        # seat would false-positive on documentation, not a declaration.
        qualified = "omnipods_policies.external_repo_merge_fence"
        program_config = (REPO / "program" / "config.yaml").read_text()
        self.assertIn(qualified, program_config)
        agents_dir = REPO / "program" / "agents"
        offenders = [str(p) for p in agents_dir.rglob("config.yaml")
                    if qualified in p.read_text()]
        self.assertEqual(offenders, [],
                         "external_repo_merge_fence declared on a seat -- #118: "
                         "per-seat policy declarations never load")


if __name__ == "__main__":
    unittest.main()
