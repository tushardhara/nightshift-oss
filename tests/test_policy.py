"""The enforcement layer and the exact-SHA provenance canary.

These are what turn the budget and the issue semantics from prompts into
machine decisions — a dispatch without a live slot, or a `gh pr create`
carrying a closing keyword, is DENIED at the tool-call layer.
"""

import os
import sys
import time
import unittest

from ctlutil import REPO, Ctl

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
import omnipods_policies as P  # noqa: E402


def send(agent, args, title="worker-task"):
    return {"type": "tool_call",
            "data": {"name": "sys_session_send",
                     "arguments": {"agent": agent, "title": title,
                                   "args": args}}}


def shell(cmd):
    return {"type": "tool_call",
            "data": {"name": "sys_os_shell", "arguments": {"command": cmd}}}


class WorkerSemaphorePolicy(unittest.TestCase):
    def setUp(self):
        self.c = Ctl()
        self.ev = P.worker_semaphore(db=self.c.db)

    def test_dispatch_without_slot_denied(self):
        r = self.ev(send("eng_claude", {
            "task_id": "missing", "purpose": "implement",
        }))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("slot", r["reason"])

    def test_dispatch_with_live_slot_allowed_and_consumed(self):
        # ADR-0062 (#471): admission is derived from (kind, agent, title,
        # purpose) inside the control plane, not from a caller-supplied
        # slot_id/task_id/generation -- the real sys_session_send schema
        # cannot carry those fields at all, so this event only needs the
        # ones it still legitimately can.
        self.c.acquire_normal(task="policy-live", title="worker-task")
        args = {"purpose": "implement"}
        r = self.ev(send("eng_claude", args))
        self.assertEqual(r["result"], "ALLOW")
        # a slot is a ONE-dispatch capability: second use denies -- once
        # dispatched, the derivation query's own `dispatched_at IS NULL`
        # clause finds no active undispatched row left to match.
        r2 = self.ev(send("eng_claude", args))
        self.assertEqual(r2["result"], "DENY")
        self.assertIn("no active undispatched slot matches", r2["reason"])
        # ...even from a different agent (also finds nothing to match)
        r3 = self.ev(send("eng_codex", args))
        self.assertEqual(r3["result"], "DENY")

    def test_slot_bound_to_its_agent(self):
        # ADR-0062 (#471): same derivation change as above -- the holder
        # check still applies to whichever agent's identity the derivation
        # query is keyed on, so requesting as the WRONG agent (eng_codex,
        # when the slot was acquired for eng_claude) now denies via "no
        # active undispatched slot matches" (there is no such row keyed on
        # eng_codex), not "bound to another holder" -- the holder check is
        # downstream of a successful derivation match, and derivation on the
        # wrong agent identity fails first, which is strictly earlier and at
        # least as safe (still denies, still consumes nothing).
        _, g = self.c.acquire_normal(task="policy-binding", title="worker-task")
        args = {"purpose": "implement"}
        r = self.ev(send("eng_codex", args))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("no active undispatched slot matches", r["reason"])
        # the denial did NOT consume the slot — the right agent can still use it
        r2 = self.ev(send("eng_claude", args))
        self.assertEqual(r2["result"], "ALLOW")

    def test_released_or_invented_slot_denied(self):
        _, g = self.c.acquire_normal(task="policy-released", title="worker-task")
        self.c.run("slot", "release", "--slot", g["slot_id"],
                   "--holder", "crew_alpha/eng_claude")
        args = {"slot_id": g["slot_id"], "task_id": "policy-released",
                "purpose": "implement", "generation": g["generation"]}
        self.assertEqual(
            self.ev(send("eng_claude", args))["result"],
            "DENY")
        self.assertEqual(
            self.ev(send("eng_claude", dict(args, slot_id=424242)))["result"],
            "DENY")

    def test_pm_exempt_and_non_dispatch_ignored(self):
        self.assertEqual(self.ev(send("pm", {}))["result"], "ALLOW")
        self.assertEqual(self.ev(shell("git status"))["result"], "ALLOW")

    def test_fails_closed_without_database(self):
        ev = P.worker_semaphore(db="/nonexistent/nowhere/control.db")
        r = ev(send("eng_claude", {"slot_id": 1, "task_id": "T",
                                    "purpose": "implement", "generation": 1}))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("not initialised", r["reason"])

    def test_explicit_operator_escape_hatch(self):
        ev = P.worker_semaphore(db="/nonexistent/nowhere/control.db")
        event = send("eng_claude", {
            "slot_id": 1, "task_id": "T", "purpose": "implement",
            "generation": 1,
        })
        os.environ["NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"] = "1"
        try:
            self.assertEqual(ev(event)["result"], "ALLOW")
        finally:
            del os.environ["NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"]
        self.assertEqual(ev(event)["result"], "DENY")


class ContinuationBudgetRecheckAtDispatch(unittest.TestCase):
    """Issue #216 (ADR-0032 + ADR-0045), AC2: ``worker_semaphore``'s own
    continuation branch must re-derive ``reason_code`` at actual dispatch
    time and deny when it is exactly ``"budget_expired"`` -- the TOCTOU case
    where a continuation slot was acquired while the budget was still live,
    but the budget expires before the dispatch actually consumes the slot.
    AC3's zero-durable-trace guarantee and AC9's dispatchable-vs-reason_code
    counterfactual (ordinary dispatch unaffected for every kind) are also
    covered here, at the dispatch checkpoint rather than the acquire one.
    """

    def setUp(self):
        self.c = Ctl()
        self.ev = P.worker_semaphore(db=self.c.db)

    def _admit(self, task, agent="eng_claude"):
        code, g = self.c.acquire_normal(task=task, agent=agent)
        self.assertEqual(code, 0, g)
        r = self.ev(send(agent, {
            "slot_id": g["slot_id"], "task_id": task,
            "purpose": "implement", "generation": g["generation"],
        }, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_qa_continuation_dispatch_denied_when_budget_expires_after_slot_acquired(self):
        task = "dispatch-budget-qa"
        self._admit(task)
        code, body = self.c.run("task", "upsert", "--id", task,
                                 "--budget-seconds", "2",
                                 "--pr-url", "https://example.invalid/pr/10")
        self.assertEqual(code, 0, body)
        # acquired WHILE the budget is still live
        code, slot = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", task, "--agent", "qa", "--title", f"fixture-{task}",
            "--purpose", "implement",
        )
        self.assertEqual(code, 0, slot)
        # ...then the budget expires before dispatch actually runs
        time.sleep(2.5)  # real budget window elapses (AC2 is a real-dispatch-time recheck)
        args = {"slot_id": slot["slot_id"], "task_id": task,
                "purpose": "implement", "generation": slot["generation"]}
        r = self.ev(send("qa", args, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "DENY", r)
        self.assertIn("budget", r["reason"].lower())
        # AC3: ROLLBACK -- the already-active slot keeps dispatched_at NULL
        code, listing = self.c.run("slot", "list")
        self.assertEqual(code, 0)
        row = next(x for x in listing["active"] if x["id"] == slot["slot_id"])
        self.assertIsNone(row["dispatched_at"])

    def test_review_continuation_dispatch_denied_when_budget_expires_after_slot_acquired(self):
        task = "dispatch-budget-review"
        self._admit(task)
        code, body = self.c.run("task", "upsert", "--id", task,
                                 "--budget-seconds", "2",
                                 "--pr-url", "https://example.invalid/pr/11")
        self.assertEqual(code, 0, body)
        code, slot = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_claude",
            "--crew", "crew_alpha", "--task", task, "--agent", "reviewer_claude",
            "--title", f"fixture-{task}", "--purpose", "review",
        )
        self.assertEqual(code, 0, slot)
        time.sleep(2.5)  # real budget window elapses (AC2 is a real-dispatch-time recheck)
        args = {"slot_id": slot["slot_id"], "task_id": task,
                "purpose": "review", "generation": slot["generation"]}
        r = self.ev(send("reviewer_claude", args, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "DENY", r)
        self.assertIn("budget", r["reason"].lower())

    def test_implementer_continuation_dispatch_denied_when_budget_expires_after_slot_acquired(self):
        task = "dispatch-budget-impl"
        self._admit(task)
        code, claim = self.c.run("claim", "acquire", "--task", task,
                                  "--claimant", "crew_alpha", "--agent", "eng_claude",
                                  "--title", f"fixture-{task}")
        self.assertEqual(code, 0, claim)
        code, body = self.c.run("task", "upsert", "--id", task, "--budget-seconds", "2")
        self.assertEqual(code, 0, body)
        code, slot = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", task, "--agent", "eng_claude",
            "--title", f"fixture-{task}", "--purpose", "implement",
            "--claim-id", str(claim["claim_id"]),
            "--claim-generation", str(claim["generation"]),
            "--claim-run-id", claim["run_id"],
        )
        self.assertEqual(code, 0, slot)
        time.sleep(2.5)  # real budget window elapses (AC2 is a real-dispatch-time recheck)
        args = {"slot_id": slot["slot_id"], "task_id": task,
                "purpose": "implement", "generation": slot["generation"]}
        r = self.ev(send("eng_claude", args, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "DENY", r)
        self.assertIn("budget", r["reason"].lower())

    def test_qa_continuation_dispatch_allowed_when_budget_not_expired(self):
        task = "dispatch-ok-qa"
        self._admit(task)
        code, body = self.c.run("task", "upsert", "--id", task,
                                 "--budget-seconds", "600",
                                 "--pr-url", "https://example.invalid/pr/12")
        self.assertEqual(code, 0, body)
        code, slot = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", task, "--agent", "qa", "--title", f"fixture-{task}",
            "--purpose", "implement",
        )
        self.assertEqual(code, 0, slot)
        args = {"slot_id": slot["slot_id"], "task_id": task,
                "purpose": "implement", "generation": slot["generation"]}
        r = self.ev(send("qa", args, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_review_continuation_dispatch_allowed_when_budget_not_expired(self):
        task = "dispatch-ok-review"
        self._admit(task)
        code, body = self.c.run("task", "upsert", "--id", task,
                                 "--budget-seconds", "600",
                                 "--pr-url", "https://example.invalid/pr/13")
        self.assertEqual(code, 0, body)
        code, slot = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", task, "--agent", "reviewer_codex",
            "--title", f"fixture-{task}", "--purpose", "review",
        )
        self.assertEqual(code, 0, slot)
        args = {"slot_id": slot["slot_id"], "task_id": task,
                "purpose": "review", "generation": slot["generation"]}
        r = self.ev(send("reviewer_codex", args, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "ALLOW", r)

    def test_implementer_continuation_dispatch_allowed_when_budget_not_expired(self):
        task = "dispatch-ok-impl"
        self._admit(task)
        code, claim = self.c.run("claim", "acquire", "--task", task,
                                  "--claimant", "crew_alpha", "--agent", "eng_claude",
                                  "--title", f"fixture-{task}")
        self.assertEqual(code, 0, claim)
        code, body = self.c.run("task", "upsert", "--id", task, "--budget-seconds", "600")
        self.assertEqual(code, 0, body)
        code, slot = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", task, "--agent", "eng_claude",
            "--title", f"fixture-{task}", "--purpose", "implement",
            "--claim-id", str(claim["claim_id"]),
            "--claim-generation", str(claim["generation"]),
            "--claim-run-id", claim["run_id"],
        )
        self.assertEqual(code, 0, slot)
        args = {"slot_id": slot["slot_id"], "task_id": task,
                "purpose": "implement", "generation": slot["generation"]}
        r = self.ev(send("eng_claude", args, title=f"fixture-{task}"))
        self.assertEqual(r["result"], "ALLOW", r)


class PrCloseKeywordPolicy(unittest.TestCase):
    def setUp(self):
        self.ev = P.pr_close_keyword_guard()

    def test_inline_closing_keyword_denied(self):
        r = self.ev(shell("gh pr create --title x --body 'Closes #12'"))
        self.assertEqual(r["result"], "DENY")
        r = self.ev(shell("gh pr edit 4 --body 'Fixes org/repo#9'"))
        self.assertEqual(r["result"], "DENY")

    def test_refs_and_unrelated_commands_allowed(self):
        self.assertEqual(
            self.ev(shell("gh pr create --body 'Refs #12'"))["result"], "ALLOW")
        self.assertEqual(self.ev(shell("git commit -m 'fix bug'"))["result"],
                         "ALLOW")

    def test_body_file_and_fill_denied_wrapper_allowed(self):
        # a file the policy cannot read is not a body it can lint
        r = self.ev(shell("gh pr create --title x --body-file /tmp/b.md"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("nightshift-ctl pr-create", r["reason"])
        self.assertEqual(
            self.ev(shell("gh pr edit 7 -F body.md"))["result"], "DENY")
        self.assertEqual(
            self.ev(shell("gh pr create --fill"))["result"], "DENY")
        # the sanctioned wrapper passes through (it lints the real bytes)
        self.assertEqual(
            self.ev(shell("nightshift-ctl pr-create -- --title x --body-file b.md"))
            ["result"], "ALLOW")


#: Every shell tool name an omnigent harness reports. claude-native and
#: kimi-native seats say `Bash`; a fence keyed on lowercase names never saw
#: them (audit C1).
HARNESS_SHELL_TOOLS = ("Bash", "bash", "BASH", "sys_os_shell", "shell", "Shell",
                       "terminal", "developer__shell", "run_command",
                       "run_shell_command", "mcp__omnigent__sys_os_shell")


def shell_as(name, cmd):
    return {"type": "tool_call",
            "data": {"name": name, "arguments": {"command": cmd}}}


class ShellFencesSeeEveryHarnessToolName(unittest.TestCase):
    def test_pr_close_keyword_guard_fires_for_every_shell_tool(self):
        ev = P.pr_close_keyword_guard()
        for name in HARNESS_SHELL_TOOLS:
            with self.subTest(name=name):
                r = ev(shell_as(name, "gh pr create --title x --body 'Closes #12'"))
                self.assertEqual(r["result"], "DENY", r)

    def test_canary_fires_for_every_shell_tool(self):
        ev = P.release_provenance_canary()
        for name in HARNESS_SHELL_TOOLS:
            with self.subTest(name=name):
                r = ev(shell_as(name, "nightshift-release-provenance-canary"))
                self.assertEqual(r["result"], "DENY", r)

    def test_shell_tools_argument_widens_and_never_narrows(self):
        ev = P.pr_close_keyword_guard(shell_tools=("custom_shell",))
        for name in ("Bash", "custom_shell"):
            with self.subTest(name=name):
                r = ev(shell_as(name, "gh pr create --body 'Closes #1'"))
                self.assertEqual(r["result"], "DENY", r)

    def test_non_shell_tools_are_untouched(self):
        ev = P.pr_close_keyword_guard()
        r = ev(shell_as("Read", "gh pr create --body 'Closes #1'"))
        self.assertEqual(r["result"], "ALLOW")


class ReleaseProvenanceCanary(unittest.TestCase):
    def setUp(self):
        self.ev = P.release_provenance_canary()

    def test_only_the_exact_inert_canary_is_denied(self):
        self.assertEqual(self.ev(shell("git status"))["result"], "ALLOW")
        self.assertEqual(
            self.ev(shell("nightshift-release-provenance-canary extra"))["result"],
            "ALLOW",
        )
        result = self.ev(shell("nightshift-release-provenance-canary"))
        self.assertEqual(result["result"], "DENY")

    def test_denial_carries_fresh_machine_readable_process_provenance(self):
        import json

        result = self.ev(shell("nightshift-release-provenance-canary"))
        proof = json.loads(result["reason"])
        self.assertEqual(proof["kind"], "nightshift_release_provenance")
        self.assertEqual(proof["pid"], os.getpid())
        self.assertEqual(len(proof["module_sha256"]), 64)
        self.assertTrue(proof["module"].endswith("/omnipods_policies.py"))
        if P.Path("/proc/self/stat").is_file():
            self.assertTrue(proof["process_start_ticks"].isdigit())
        else:
            self.assertIsNone(proof["process_start_ticks"])

    def test_process_epoch_parser_uses_field_22(self):
        from unittest import mock

        proc_stat = "123 (worker name) " + " ".join(
            ["S", *("0" for _ in range(18)), "4242"]
        )
        with mock.patch.object(P.Path, "read_text", return_value=proc_stat):
            self.assertEqual(P._process_start_ticks(), "4242")

    def test_unreadable_process_epoch_cannot_become_a_truthy_sentinel(self):
        import json
        from unittest import mock

        with mock.patch.object(P.Path, "read_text", side_effect=OSError("no proc")):
            result = P.release_provenance_canary()(shell(
                "nightshift-release-provenance-canary"
            ))
        proof = json.loads(result["reason"])
        self.assertIsNone(proof["process_start_ticks"])


if __name__ == "__main__":
    unittest.main()
