"""Issue #720 / ADR-0091 — the review plan binds Program's dispatch.

Every test here states its negative control, because the thing under test is
a gate, and a gate that has never refused is indistinguishable from an
`ALLOW` with the check deleted. The suite is deliberately built around one
head SHA whose plan names `security` and does NOT name `ux` or `staff`: the
same call, the same head, the same fixture, permitted for one seat and
refused for another, is the only shape that proves the plan is being read
rather than a constant returned.

The fixture builds a bundle repository that is ALSO the target repository —
which is what Nightshift reviewing its own code looks like, and what makes
`is_own_repository` true so the binding path (decision 4) is the one under
test. The foreign-repository case gets its own separate target.
"""

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GIT = "/usr/bin/git"
BUNDLE_FILES = (
    "bin/nightshift-ctl",
    "bin/nightshift_review_plan.py",
    "bin/nightshift_review_dispatch_gate.py",
    "program/review-risk.json",
)
VIRTUAL_T0 = 1_000_000.0


class ReviewDispatchGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.db = self.root / "control.db"
        self._init_repo(self.bundle)
        for relative in BUNDLE_FILES:
            target = self.bundle / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        os.chmod(self.bundle / "bin/nightshift-ctl", 0o755)
        self.base = self._commit(self.bundle, "bundle")
        self._ctl("init")
        # One change under `auth/**`: a security_path high-risk group, so the
        # plan names `security`. Nothing under `web/**` or the constitution
        # paths, so it names neither `ux` nor `staff`.
        self._write(self.bundle, "auth/thing.py", "def check():\n    return 1\n")
        self.head = self._commit(self.bundle, "head")
        self._task()

    # ---------------------------------------------------------------- fixture

    def _git(self, repo, *args):
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        })
        return subprocess.run(
            [GIT, "-C", str(repo), *args], check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env).stdout.strip()

    def _init_repo(self, repo):
        repo.mkdir(parents=True)
        subprocess.run([GIT, "-c", "init.defaultBranch=main", "init", str(repo)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _write(self, repo, relative, content):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _commit(self, repo, message):
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", message)
        return self._git(repo, "rev-parse", "HEAD")

    def _ctl(self, *args, stdin=None):
        command = [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
                   "--db", str(self.db), "--now", str(VIRTUAL_T0), *args]
        proc = subprocess.run(command, input=stdin, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            body = json.loads(proc.stdout)
        except json.JSONDecodeError:
            body = {"stdout": proc.stdout, "stderr": proc.stderr}
        return proc.returncode, body

    def _task(self, task="T-1", crew="crew_alpha", size="S"):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT OR REPLACE INTO tasks(id,crew,size,spec_ref,base_sha,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (task, crew, size, "spec.md", self.base, VIRTUAL_T0, VIRTUAL_T0))

    def _authority(self):
        code, granted = self._ctl("authority", "acquire",
                                  "--holder-label", "program-a", "--ttl", "1800")
        self.assertEqual(code, 0, granted)
        return granted["generation"], granted["token"]

    def _mint(self, generation, token, *args):
        return self._ctl("authority", "permit-dispatch",
                         "--generation", str(generation), "--token-stdin", *args,
                         stdin=token)

    def _gated_mint(self, generation, token, agent, repo=None, head=None,
                    task="T-1"):
        return self._mint(generation, token, "--agent", agent,
                          "--title", "review this",
                          "--task", task,
                          "--repo", str(repo or self.bundle),
                          "--head-sha", head or self.head)

    def _permits(self):
        with sqlite3.connect(self.db) as cx:
            cx.row_factory = sqlite3.Row
            return [dict(r) for r in cx.execute(
                "SELECT * FROM program_action_permits ORDER BY id")]

    def _degradations(self):
        with sqlite3.connect(self.db) as cx:
            cx.row_factory = sqlite3.Row
            return [dict(r) for r in cx.execute(
                "SELECT * FROM review_gate_degradations ORDER BY id")]

    def _plan_domains(self):
        code, report = self._ctl("review", "plan", "--task", "T-1",
                                 "--repo", str(self.bundle),
                                 "--head-sha", self.head)
        self.assertEqual(code, 0, report)
        return report["suggested_domains"]

    # ------------------------------------------------------------------ tests

    def test_the_fixture_head_separates_a_named_domain_from_an_omitted_one(self):
        """The premise every other test rests on, asserted rather than assumed.

        If this head ever stopped naming `security`, or started naming `ux`,
        the refusal tests below would still pass and would be proving nothing.
        """
        domains = self._plan_domains()
        self.assertIn("security", domains)
        self.assertNotIn("ux", domains)
        self.assertNotIn("staff", domains)

    def test_gate_mints_for_a_domain_the_plan_names(self):
        """AC-1, the ALLOW half."""
        generation, token = self._authority()
        code, body = self._gated_mint(generation, token, "security")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["review_gate"], "security")
        self.assertEqual([p["review_gate"] for p in self._permits()], ["security"])

    def test_gate_refuses_a_domain_the_plan_omits(self):
        """AC-1, the DENY half — the same head, a different seat."""
        generation, token = self._authority()
        code, body = self._gated_mint(generation, token, "ux")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "review_gate_refused")
        self.assertEqual(body["domain"], "ux")
        self.assertEqual(self._permits(), [],
                         "a refused mint must leave no permit behind")

    def test_a_refused_mint_writes_nothing_at_all(self):
        """A refusal that still recorded a degradation would make the
        degradation count unreadable: refusals are the gate WORKING."""
        generation, token = self._authority()
        self._gated_mint(generation, token, "ux")
        self.assertEqual(self._degradations(), [])

    def test_gated_seat_without_its_facts_is_refused_not_waved_through(self):
        """Fail closed on a caller that omits what the gate needs."""
        generation, token = self._authority()
        code, body = self._mint(generation, token, "--agent", "staff",
                                "--title", "t")
        self.assertEqual(code, 2, body)
        self.assertIn("--task", body["reason"])
        self.assertEqual(self._permits(), [])

    def test_ungated_agent_mints_exactly_as_before(self):
        """AC-2 in effect: no gate is added to seats the plan does not decide."""
        generation, token = self._authority()
        code, body = self._mint(generation, token, "--digest", "abc123")
        self.assertEqual(code, 0, body)
        self.assertIsNone(body["review_gate"])
        self.assertEqual(body["action_digest"], "abc123")

    def test_tuple_form_and_digest_form_agree_on_the_digest(self):
        """One definition of 'this exact call', not two that drift."""
        generation, token = self._authority()
        code, expected = self._ctl(
            "authority", "digest", "--tool", "sys_session_send",
            "--agent", "release", "--title", "ship it", "--args-json", "{}")
        self.assertEqual(code, 0, expected)
        code, minted = self._mint(generation, token, "--agent", "release",
                                  "--title", "ship it", "--args-json", "{}")
        self.assertEqual(code, 0, minted)
        self.assertEqual(minted["action_digest"], expected["digest"])
        self.assertIsNone(minted["review_gate"],
                          "release is not a gated review seat")

    def test_digest_form_refuses_every_gate_argument_rather_than_ignoring_it(self):
        """Silently ignoring a supplied `--task`/`--head-sha`/`--not-a-review`
        would hand an operator an ungated permit and tell them nothing."""
        generation, token = self._authority()
        for extra in (("--agent", "security"), ("--task", "T-1"),
                      ("--head-sha", self.head), ("--repo", str(self.bundle)),
                      ("--not-a-review", "because")):
            code, body = self._mint(generation, token, "--digest", "abc123",
                                    *extra)
            self.assertEqual(code, 2, (extra, body))
            self.assertIn(extra[0], body["reason"])

    def test_foreign_repository_mints_and_is_recorded(self):
        """Decision 4 — shadow on a customer repository, but never silently."""
        foreign = self.root / "customer"
        self._init_repo(foreign)
        self._write(foreign, "README.md", "theirs\n")
        foreign_base = self._commit(foreign, "theirs base")
        self._write(foreign, "auth/other.py", "x = 1\n")
        foreign_head = self._commit(foreign, "theirs head")
        self._task(crew="crew_alpha")
        with sqlite3.connect(self.db) as cx:
            cx.execute("UPDATE tasks SET base_sha=? WHERE id='T-1'",
                       (foreign_base,))
        generation, token = self._authority()
        code, body = self._gated_mint(generation, token, "ux", repo=foreign,
                                      head=foreign_head)
        self.assertEqual(code, 0, body)
        rows = self._degradations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "foreign")
        self.assertEqual(rows[0]["agent"], "ux")

    def test_degradation_count_moves_only_when_it_should(self):
        """Zero is not evidence unless something makes it non-zero (AC-3).

        A healthy, binding mint must leave the count at zero; a planner that
        cannot answer must move it. Both halves, one test, because either
        alone is satisfied by a counter that never changes.
        """
        generation, token = self._authority()
        code, _ = self._gated_mint(generation, token, "security")
        self.assertEqual(code, 0)
        self.assertEqual(self._degradations(), [],
                         "a healthy plan must record no degradation")
        # A genuine environment degradation, not a bad argument: the planner
        # refuses to run at all when the executing bundle's own source does
        # not match its committed blobs. Note what this means in practice —
        # the gate is off while anyone is editing the controller.
        rules = self.bundle / "program/review-risk.json"
        rules.write_text(rules.read_text() + "\n")
        code, body = self._gated_mint(generation, token, "security")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["review_gate"], "fallback")
        rows = self._degradations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "degraded")
        self.assertEqual(rows[0]["reason"], "plan_denied:bundle_identity_unproved")

    def test_a_non_review_dispatch_must_declare_itself(self):
        """`ux` is dispatched for design input before any crew is chartered,
        when no head exists. That must keep working -- but only as a stated
        exception, never by leaving the review facts out."""
        generation, token = self._authority()
        code, silent = self._mint(generation, token, "--agent", "ux",
                                  "--title", "design the flow")
        self.assertEqual(code, 2, silent)
        self.assertIn("--not-a-review", silent["reason"])
        self.assertEqual(self._permits(), [])

        code, declared = self._mint(generation, token, "--agent", "ux",
                                    "--title", "design the flow",
                                    "--not-a-review", "design input, no PR yet")
        self.assertEqual(code, 0, declared)
        self.assertEqual(declared["review_gate"], "exempt")

    def test_every_declared_exception_is_counted(self):
        """The exception is only tolerable because it is measurable."""
        generation, token = self._authority()
        self._mint(generation, token, "--agent", "staff", "--title", "draft",
                   "--not-a-review", "blind architecture draft")
        rows = self._degradations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "exempt")
        self.assertIn("blind architecture draft", rows[0]["reason"])

    def test_declaring_it_cannot_be_appended_to_a_refused_call(self):
        """The hole this closes: take the call that was just refused, append
        `--not-a-review`, and mint anyway. The declaration must not be able to
        override facts it contradicts — it has to be a DIFFERENT call."""
        generation, token = self._authority()
        code, refused = self._gated_mint(generation, token, "ux")
        self.assertEqual(code, 3, refused)

        code, contradiction = self._mint(
            generation, token, "--agent", "ux", "--title", "review this",
            "--task", "T-1", "--repo", str(self.bundle),
            "--head-sha", self.head, "--not-a-review", "changed my mind")
        self.assertEqual(code, 2, contradiction)
        self.assertIn("contradicts", contradiction["reason"])
        self.assertEqual(self._permits(), [])

    def test_a_bad_head_is_a_bad_call_not_a_degraded_planner(self):
        """The gate must not be opt-out. A caller that names a head which is
        not a commit has made a bad call; if that bought the degraded-planner
        fallback, every refusal could be dodged by passing a junk head."""
        generation, token = self._authority()
        code, body = self._gated_mint(generation, token, "ux", head="0" * 40)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "review_gate_refused")
        self.assertEqual(self._permits(), [])

    def test_a_fallback_permit_never_claims_to_be_a_verified_one(self):
        """A degraded mint and a bound mint must be distinguishable in the
        table whose whole job is telling them apart."""
        generation, token = self._authority()
        foreign = self.root / "elsewhere"
        self._init_repo(foreign)
        self._write(foreign, "README.md", "theirs\n")
        foreign_base = self._commit(foreign, "b")
        self._write(foreign, "auth/x.py", "y = 1\n")
        foreign_head = self._commit(foreign, "h")
        with sqlite3.connect(self.db) as cx:
            cx.execute("UPDATE tasks SET base_sha=? WHERE id='T-1'",
                       (foreign_base,))
        code, body = self._gated_mint(generation, token, "security",
                                      repo=foreign, head=foreign_head)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["review_gate"], "fallback")
        self.assertNotEqual(body["review_gate"], "security")

    def test_degradation_is_not_writable_without_the_authority_token(self):
        """The count is evidence, so it may not be appended to by a caller
        that has not proved it holds the fence."""
        generation, _ = self._authority()
        code, body = self._gated_mint(generation, "not-the-real-token", "security",
                                      head="0" * 40)
        self.assertEqual(code, 3, body)
        self.assertEqual(self._degradations(), [])


class FenceBindsTheGateTests(unittest.TestCase):
    """The consumption half: a permit minted blind cannot dispatch a review
    seat. Without this, the mint gate would be advisory — Program could mint
    the same digest through `--digest` and dispatch anyway."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "deploy" / "policies"))
        self.addCleanup(sys.path.remove, str(ROOT / "deploy" / "policies"))
        import omnipods_policies
        self.policies = omnipods_policies
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "control.db"
        subprocess.run(
            [sys.executable, str(ROOT / "bin/nightshift-ctl"), "--db",
             str(self.db), "init"], check=True, stdout=subprocess.PIPE)

    def _event(self, agent, title="review this", args=None):
        return {"type": "tool_call",
                "data": {"name": "sys_session_send",
                         "arguments": {"agent": agent, "title": title,
                                       "args": args or {}}}}

    def _authority_row(self):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT INTO program_authority(generation,holder_label,"
                "token_hash,acquired_at,expires_at,state) "
                "VALUES(1,'program-a','x',0,4102444800,'active')")
        return 1

    def _permit(self, agent, review_gate, title="review this"):
        digest = self.policies.canonical_dispatch_digest(
            "sys_session_send", agent, title, {})
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT INTO program_action_permits(generation,action_digest,"
                "expires_at,review_gate) VALUES(1,?,4102444800,?)",
                (digest, review_gate))
        return digest

    def test_blind_permit_cannot_dispatch_a_review_seat(self):
        self._authority_row()
        self._permit("security", None)
        fence = self.policies.program_authority_fence(db=str(self.db))
        verdict = fence(self._event("security"))
        self.assertEqual(verdict["result"], "DENY", verdict)
        self.assertIn("review plan", verdict["reason"])

    def test_gated_permit_dispatches_the_same_seat(self):
        """The negative control for the test above: same seat, same digest,
        same fence — only the permit's provenance differs."""
        self._authority_row()
        self._permit("security", "security")
        fence = self.policies.program_authority_fence(db=str(self.db))
        self.assertEqual(fence(self._event("security"))["result"], "ALLOW")

    def test_a_permit_gated_for_another_domain_does_not_transfer(self):
        self._authority_row()
        self._permit("security", "ux")
        fence = self.policies.program_authority_fence(db=str(self.db))
        self.assertEqual(fence(self._event("security"))["result"], "DENY")

    def test_a_declared_non_review_permit_dispatches(self):
        """The fence cannot tell a design-input dispatch from a review one --
        both are `sys_session_send` to `ux`. The mint-time declaration is the
        only signal there is, so the fence honours it, and the count is what
        keeps it honest."""
        self._authority_row()
        self._permit("ux", "exempt")
        fence = self.policies.program_authority_fence(db=str(self.db))
        self.assertEqual(fence(self._event("ux"))["result"], "ALLOW")

    def test_blind_permit_still_dispatches_an_ungated_seat(self):
        """AC-4/AC-2: everything that is not a gated review seat is untouched."""
        self._authority_row()
        self._permit("release", None)
        fence = self.policies.program_authority_fence(db=str(self.db))
        self.assertEqual(fence(self._event("release"))["result"], "ALLOW")

    def test_the_fence_actually_calls_the_gate(self):
        """AC-6: the invocation, not the mapping, is the thing under test.

        Asserting the mapping alone would pass with the fence's call to it
        deleted. So this replaces the gate with one that classifies an
        ORDINARY seat as a review seat, and requires the fence's verdict to
        change — which it can only do by consulting it."""
        self._authority_row()
        self._permit("release", None)
        fence = self.policies.program_authority_fence(db=str(self.db))
        original = self.policies.review_gate_for_agent
        self.addCleanup(setattr, self.policies, "review_gate_for_agent",
                        original)
        self.policies.review_gate_for_agent = (
            lambda agent: "security" if agent == "release" else None)
        self.assertEqual(fence(self._event("release"))["result"], "DENY",
                         "the fence is not consulting the gate at all")


class EligibilityStaysFrozenTests(unittest.TestCase):
    """AC-5 / ADR-0042: this is a DISPATCH gate. Nothing here may reach merge
    eligibility, whose required domains come only from the frozen contract."""

    def test_eligibility_does_not_import_the_dispatch_gate(self):
        source = (ROOT / "bin/nightshift_review_eligibility.py").read_text()
        self.assertNotIn("nightshift_review_dispatch_gate", source)
        self.assertNotIn("suggested_domains", source.split('"""', 2)[-1])

    def test_the_gate_does_not_speak_the_frozen_vocabulary(self):
        """The planner's names and the frozen contract's names are different
        vocabularies. A mapping between them in this file would be the first
        step of carrying `suggested_domains` into eligibility by accident."""
        sys.path.insert(0, str(ROOT / "bin"))
        self.addCleanup(sys.path.remove, str(ROOT / "bin"))
        import nightshift_review_dispatch_gate as gate
        import nightshift_review_receipts as receipts
        frozen = set(receipts.canonical_domains())
        for domain in gate.GATED_SEATS.values():
            if domain not in {"security", "ux"}:
                self.assertNotIn(domain, frozen)


if __name__ == "__main__":
    unittest.main()
