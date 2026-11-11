"""G0 -- Nightshift reads GitHub Actions results and never causes them.

Mockless: the real policy module is imported and invoked in-process, the
same posture as tests/test_external_repo_merge_fence.py uses for its
ADR-0085 sibling.

WHAT THIS SUITE PINS
====================
  ActionsFenceDenies      Every shape that causes a run -- the `gh workflow`
                          and `gh run` write verbs, and the four `gh api`
                          dispatch/rerun shapes including the one where a
                          field flag silently promotes GET to POST.
  ActionsFenceAllows      Reading still works. If this class fails the fence
                          is useless in practice: the program's own
                          check-runs GET, and PR creation, must pass.
  ActionsFenceArgvParsing The evasions a regex over the raw string misses --
                          quoted tokens, a variable alias, an `sh -c`
                          payload, and a call after a separator. These are
                          the ADR-0085 regex gaps this fence does not
                          inherit.
  ActionsFenceFailsClosed Unlexable, non-string, and unknown-pre-verb-flag
                          commands DENY rather than falling through.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy" / "policies"))
import nightshift_actions_fence as A  # noqa: E402

FENCE = A.github_actions_fence()


def shell(cmd):
    return {"type": "tool_call",
            "data": {"name": "sys_os_shell", "arguments": {"command": cmd}}}


class _FenceCase(unittest.TestCase):
    def assertDenied(self, cmd, msg=None):
        verdict = FENCE(shell(cmd))
        self.assertEqual("DENY", verdict["result"], msg or cmd)
        self.assertIn(A._REASON, verdict["reason"])

    def assertAllowed(self, cmd, msg=None):
        verdict = FENCE(shell(cmd))
        self.assertEqual("ALLOW", verdict["result"], msg or f"{cmd} -> {verdict}")


class ActionsFenceDenies(_FenceCase):
    def test_workflow_write_verbs(self):
        for cmd in ("gh workflow run ci.yml",
                    "gh workflow run ci.yml -f env=prod",
                    "gh workflow enable ci.yml",
                    "gh workflow disable ci.yml",
                    "gh workflow --repo o/r run ci.yml"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_run_write_verbs(self):
        for cmd in ("gh run rerun 12345",
                    "gh run rerun 12345 --failed",
                    "gh run cancel 12345",
                    "gh run delete 12345",
                    "gh run --repo o/r rerun 1"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_api_workflow_dispatch(self):
        self.assertDenied(
            "gh api -X POST repos/o/r/actions/workflows/9/dispatches -f ref=main")

    def test_api_run_rerun(self):
        self.assertDenied("gh api -X POST repos/o/r/actions/runs/123/rerun")

    def test_api_repository_dispatch(self):
        self.assertDenied("gh api -X POST repos/o/r/dispatches -f event_type=go")

    # `gh api` sends POST as soon as a field flag is present, with no -X at
    # all. A fence that only read -X would let this through.
    #
    # The path here deliberately does NOT match the dispatch/rerun rules --
    # those deny on the path alone, so using one would pass this test even
    # with the GET->POST promotion deleted, which is exactly what an earlier
    # version of this test did.
    def test_field_flag_promotes_get_to_post(self):
        self.assertDenied("gh api repos/o/r/actions/variables -f name=x")

    def test_the_same_actions_path_read_only_is_allowed(self):
        """Negative control for the test above: without the field flag the
        very same path is a GET and must pass, so the DENY above is
        attributable to the promotion and nothing else."""
        self.assertAllowed("gh api repos/o/r/actions/variables")

    def test_write_method_against_any_actions_path(self):
        for cmd in ("gh api -X DELETE repos/o/r/actions/runs/5",
                    "gh api --method PUT repos/o/r/actions/secrets/X",
                    "gh api -XPOST repos/o/r/actions/runs/5/cancel"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)


class ActionsFenceAllows(_FenceCase):
    # The one sanctioned read this program already makes.
    def test_check_runs_get_is_the_sanctioned_read(self):
        self.assertAllowed("gh api repos/o/r/commits/abc123/check-runs")

    def test_run_read_verbs(self):
        for cmd in ("gh run list", "gh run list --limit 5", "gh run view 123",
                    "gh run view 123 --log", "gh run watch 123",
                    "gh run download 123"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_workflow_read_verbs(self):
        for cmd in ("gh workflow list", "gh workflow view ci.yml"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    # `--repo` before the verb is valid gh grammar. A fence that reads the
    # verb as the first token after the subcommand sees "--repo" and denies
    # a plain read. Only an ALLOW case catches that -- the DENY cases pass
    # either way, for the wrong reason.
    def test_pre_verb_repo_flag_does_not_blind_the_read_verbs(self):
        for cmd in ("gh run --repo o/r list",
                    "gh run -R o/r view 123",
                    "gh workflow --repo o/r list"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_actions_get_is_read(self):
        self.assertAllowed("gh api repos/o/r/actions/runs/5")

    # If these ever DENY, the fence has broken the run it is meant to protect.
    def test_unrelated_commands_untouched(self):
        for cmd in ("gh pr create --title x --body y",
                    "gh pr view 1",
                    "gh issue list",
                    "git push origin HEAD",
                    "go test ./... -short",
                    "echo actions/runs/1/rerun"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_non_shell_tool_calls_are_not_our_concern(self):
        event = {"type": "tool_call",
                 "data": {"name": "sys_read_inbox", "arguments": {}}}
        self.assertEqual("ALLOW", FENCE(event)["result"])

    def test_non_tool_call_events_pass(self):
        self.assertEqual("ALLOW", FENCE({"type": "message", "data": {}})["result"])


class ActionsFenceArgvParsing(_FenceCase):
    """The ADR-0085 regex gaps this fence does not inherit."""

    def test_quoted_tokens(self):
        self.assertDenied('gh "workflow" "run" ci.yml')

    def test_variable_alias(self):
        self.assertDenied("G=gh; $G workflow run ci.yml")

    def test_interpreter_payload(self):
        self.assertDenied("sh -c 'gh run rerun 1'")

    def test_after_a_separator(self):
        self.assertDenied("go test ./... && gh run rerun 1")

    def test_second_call_in_a_compound(self):
        self.assertDenied("gh run list; gh workflow run ci.yml")

    def test_piped(self):
        self.assertDenied("echo x | gh workflow run ci.yml")


class ActionsFenceEveryShellToolName(unittest.TestCase):
    """Audit C1: claude-native seats report the shell tool as `Bash`. The
    fence used to match only lowercase names, so `gh workflow run` from any
    claude-native seat was ALLOWed."""

    NAMES = ("Bash", "bash", "BASH", "sys_os_shell", "shell", "Shell",
             "terminal", "developer__shell", "run_command", "run_shell_command",
             "mcp__omnigent__sys_os_shell")

    @staticmethod
    def call(name, arguments):
        return {"type": "tool_call", "data": {"name": name, "arguments": arguments}}

    def test_every_harness_shell_tool_is_fenced(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                verdict = FENCE(self.call(name, {"command": "gh workflow run deploy.yml"}))
                self.assertEqual("DENY", verdict["result"], verdict)

    def test_shell_tools_argument_cannot_narrow_the_set(self):
        fence = A.github_actions_fence(shell_tools=("sys_os_shell",))
        verdict = fence(self.call("Bash", {"command": "gh run rerun 1"}))
        self.assertEqual("DENY", verdict["result"])

    def test_command_under_a_harness_specific_key_is_read(self):
        # antigravity's run_command carries the command as `CommandLine`
        verdict = FENCE(self.call("run_command", {"CommandLine": "gh run rerun 1"}))
        self.assertEqual("DENY", verdict["result"])

    def test_non_shell_tool_with_a_command_argument_is_not_ours(self):
        verdict = FENCE(self.call("Read", {"command": "gh run rerun 1"}))
        self.assertEqual("ALLOW", verdict["result"])


class ActionsFenceLexerBypasses(_FenceCase):
    """Audit C4: each of these caused a run and was ALLOWed."""

    def test_value_flags_no_longer_pose_as_the_endpoint(self):
        for cmd in ("gh api -H Accept:x -X POST repos/o/r/actions/workflows/9/dispatches",
                    "gh api --jq .x -X POST repos/o/r/actions/runs/1/rerun",
                    "gh api -q .x -p p --cache 1h -X POST repos/o/r/actions/runs/1/cancel",
                    "gh api --bogus repos/o/r/actions/runs/1"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_leading_slash_full_url_and_repository_id(self):
        for cmd in ("gh api -X POST /repos/o/r/actions/runs/1/cancel",
                    "gh api https://api.github.com/repos/o/r/actions/runs/1/rerun",
                    "gh api -X POST repositories/1/dispatches -f event_type=x",
                    "gh api -X POST repos/o/r/check-suites/9/rerequest",
                    'gh api -X POST "repos/o/r/actions/runs/$N/rerun"'):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_xargs_and_gh_word_tricks(self):
        for cmd in ("echo run rerun 123 | xargs gh",
                    "echo rerun 1 | xargs gh run",
                    "$'gh' workflow run deploy.yml",
                    "${G:-gh} run rerun 5",
                    "/usr/bin/g[h] workflow run deploy.yml",
                    "env -S 'gh workflow run deploy.yml'",
                    "gh $S rerun 1",
                    "gh >/dev/null workflow run deploy.yml",
                    "echo a#; gh run rerun 1",
                    "printf 'gh run rerun 1' | sh"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_reads_through_the_same_parser_still_pass(self):
        for cmd in ("gh api -H Accept:x repos/o/r/commits/abc/check-runs",
                    "gh api --paginate /repos/o/r/actions/runs",
                    "xargs gh run view", "gh --version"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)


class ActionsFenceFailsClosed(_FenceCase):
    def test_unlexable_command_denies(self):
        self.assertDenied("gh workflow run 'unbalanced")

    def test_non_string_command_denies(self):
        event = {"type": "tool_call",
                 "data": {"name": "sys_os_shell", "arguments": {"command": ["gh"]}}}
        verdict = FENCE(event)
        self.assertEqual("DENY", verdict["result"])

    def test_unknown_pre_verb_flag_denies(self):
        self.assertDenied("gh --bogus-flag workflow run ci.yml")

    # The property is "no escape hatch exists", not "the word is absent" --
    # the module docstring says FAIL_OPEN in the sentence promising there is
    # none. Assert the mechanism: no env lookup, and no such variable bound.
    def test_there_is_no_fail_open_escape_hatch(self):
        source = (REPO / "deploy" / "policies" /
                  "nightshift_actions_fence.py").read_text()
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)
        self.assertIsNone(
            re.search(r"^\s*[A-Za-z_]*FAIL_OPEN[A-Za-z_]*\s*=", source, re.M),
            "a FAIL_OPEN variable is bound in the fence")


if __name__ == "__main__":
    unittest.main()
