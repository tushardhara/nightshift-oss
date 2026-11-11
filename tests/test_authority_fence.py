"""ADR-0044 Decision §3 (ROOT-policy dispatch fence) and §3b (the
`--claimant program` / `--oncall` token checks) — the two CONSUMERS of the
Program-authority primitive built in `174-implement-authority` (PR #304).

Covers AC-5 (dispatch / control-plane-write denial for a fenced-out
generation) and AC-8 (enforcement proved loaded via a live end-to-end test,
not a static config read) from the DoR
(.omni/reports/2026-08-29-0911Z-174-dor.md, branch pm/174-dor).

`174-implement-authority`'s own `tests/test_authority.py` covers the
primitive itself (§1/§2/§6) and explicitly excludes this task's scope; this
file does not re-test that ground.
"""

import importlib
import importlib.util
import os
import sqlite3
import sys
import unittest

import yaml

from ctlutil import REPO, Ctl, VIRTUAL_T0

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
import omnipods_policies as P  # noqa: E402


def send(agent, args, title="worker-task", tool="sys_session_send"):
    return {"type": "tool_call",
            "data": {"name": tool,
                     "arguments": {"agent": agent, "title": title,
                                   "args": args}}}


class AuthorityFixture(unittest.TestCase):
    """Shared helpers: acquire a live Program authority and issue permits."""

    def acquire_authority(self, c, holder_label="program-a", ttl=1000):
        code, granted = c.run("authority", "acquire", "--holder-label",
                              holder_label, "--ttl", str(ttl))
        self.assertEqual(code, 0, granted)
        return granted["generation"], granted["token"]

    def permit(self, c, generation, token, digest, ttl=60):
        code, body = c.run("authority", "permit-dispatch", "--generation",
                           str(generation), "--digest", digest,
                           "--token-stdin", "--ttl", str(ttl), stdin=token)
        self.assertEqual(code, 0, body)
        return body


# --------------------------------------------------------------- §3b: claim


class ClaimAcquireProgramTokenGate(AuthorityFixture):
    def _seed_admitted_task(self, c, task="fence-claim-task",
                            agent="eng_claude", title="t"):
        code, _ = c.run("run", "current", "--set", "fixture-run")
        self.assertEqual(code, 0)
        code, _ = c.run("task", "upsert", "--id", task, "--crew", "crew_alpha",
                        "--agent", agent, "--title", title,
                        "--state", "building", "--size", "S")
        self.assertEqual(code, 0)
        c.seed_admission(task=task, slot_id=1, crew="crew_alpha",
                         agent=agent, title=title)
        return task, agent, title

    def _claims(self, c):
        with sqlite3.connect(c.db) as cx:
            cx.row_factory = sqlite3.Row
            return [dict(r) for r in cx.execute(
                "SELECT * FROM task_claims ORDER BY id").fetchall()]

    def test_denied_without_token_or_generation(self):
        c = Ctl(clock=VIRTUAL_T0)
        task, agent, title = self._seed_admitted_task(c)
        before = self._claims(c)
        code, body = c.run("claim", "acquire", "--task", task,
                           "--claimant", "program", "--agent", agent,
                           "--title", title)
        self.assertEqual(code, 3)
        self.assertFalse(body["granted"])
        self.assertIn("authority token", body["reason"])
        self.assertNotIn("already claimed", body["reason"])
        self.assertEqual(before, self._claims(c))  # zero mutation on denial

    def test_denied_with_wrong_token(self):
        c = Ctl(clock=VIRTUAL_T0)
        task, agent, title = self._seed_admitted_task(c)
        gen, _token = self.acquire_authority(c)
        code, body = c.run("claim", "acquire", "--task", task,
                           "--claimant", "program", "--agent", agent,
                           "--title", title, "--authority-generation", str(gen),
                           "--authority-token-stdin", stdin="not-the-real-token")
        self.assertEqual(code, 3)
        self.assertFalse(body["granted"])

    def test_denied_for_a_fenced_out_generation(self):
        """AC-5: a generation that lost the fence is denied on this
        control-plane write too, not just at the next `authority acquire`."""
        c = Ctl(clock=VIRTUAL_T0)
        task, agent, title = self._seed_admitted_task(c)
        gen1, token1 = self.acquire_authority(c, "program-a", ttl=10)
        c.advance(11)  # gen1 lapses (crash/never-released, no force-break)
        gen2, _token2 = self.acquire_authority(c, "program-b", ttl=1000)
        self.assertEqual([gen1, gen2], [1, 2])
        # replaying gen1's live-at-the-time token against the now-fenced-out
        # generation is denied, exactly as AC-5 requires
        code, body = c.run("claim", "acquire", "--task", task,
                           "--claimant", "program", "--agent", agent,
                           "--title", title, "--authority-generation", str(gen1),
                           "--authority-token-stdin", stdin=token1)
        self.assertEqual(code, 3)
        self.assertFalse(body["granted"])

    def test_allowed_with_live_current_generation_token(self):
        c = Ctl(clock=VIRTUAL_T0)
        task, agent, title = self._seed_admitted_task(c)
        gen, token = self.acquire_authority(c)
        code, body = c.run("claim", "acquire", "--task", task,
                           "--claimant", "program", "--agent", agent,
                           "--title", title, "--authority-generation", str(gen),
                           "--authority-token-stdin", stdin=token)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["granted"])

    def test_crew_claimant_is_not_gated(self):
        """A crew's own claim-first recovery of its own task is a different
        action entirely and must stay unaffected — no authority args needed."""
        c = Ctl(clock=VIRTUAL_T0)
        task, agent, title = self._seed_admitted_task(c)
        code, body = c.run("claim", "acquire", "--task", task,
                           "--claimant", "crew_alpha", "--agent", agent,
                           "--title", title)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["granted"])


# ---------------------------------------------------------------- §3b: slot


class SlotAcquireOncallTokenGate(AuthorityFixture):
    def _worker_slots(self, c):
        with sqlite3.connect(c.db) as cx:
            cx.row_factory = sqlite3.Row
            return [dict(r) for r in cx.execute(
                "SELECT * FROM worker_slots ORDER BY id").fetchall()]

    def test_denied_without_token(self):
        c = Ctl(clock=VIRTUAL_T0)
        before = self._worker_slots(c)
        code, body = c.run("slot", "acquire", "--holder", "oncall",
                           "--agent", "oncall", "--title", "t",
                           "--purpose", "incident", "--oncall")
        self.assertEqual(code, 3)
        self.assertFalse(body["granted"])
        self.assertIn("authority token", body["reason"])
        self.assertEqual(before, self._worker_slots(c))

    def test_denied_for_a_fenced_out_generation(self):
        c = Ctl(clock=VIRTUAL_T0)
        gen1, token1 = self.acquire_authority(c, "program-a", ttl=10)
        c.advance(11)
        self.acquire_authority(c, "program-b", ttl=1000)
        code, body = c.run("slot", "acquire", "--holder", "oncall",
                           "--agent", "oncall", "--title", "t",
                           "--purpose", "incident", "--oncall",
                           "--authority-generation", str(gen1),
                           "--authority-token-stdin", stdin=token1)
        self.assertEqual(code, 3)
        self.assertFalse(body["granted"])

    def test_allowed_with_live_current_generation_token(self):
        c = Ctl(clock=VIRTUAL_T0)
        gen, token = self.acquire_authority(c)
        code, body = c.run("slot", "acquire", "--holder", "oncall",
                           "--agent", "oncall", "--title", "t",
                           "--purpose", "incident", "--oncall",
                           "--authority-generation", str(gen),
                           "--authority-token-stdin", stdin=token)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["granted"])

    def test_normal_slot_acquire_is_not_gated(self):
        c = Ctl(clock=VIRTUAL_T0)
        code, body = c.acquire_normal(task="fence-normal-slot")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["granted"])


# ----------------------------------------------------------------- §3: ROOT


class ProgramAuthorityFencePolicy(unittest.TestCase):
    def setUp(self):
        # Real wall-clock Ctl, deliberately NOT VIRTUAL_T0: this policy's
        # `_evaluate` reads `time.time()` directly (the same design
        # `worker_semaphore` already uses — see test_policy.py's own
        # `WorkerSemaphorePolicy` fixture), so a virtual-clock-written
        # `expires_at` (~1e6) would read as already-expired against a real
        # `now` (~1.8e9) and every row would appear stale immediately.
        # "Fenced-out generation" scenarios below use an explicit
        # `authority release` instead of TTL expiry for the same reason.
        self.c = Ctl()
        self.ev = P.program_authority_fence(db=self.c.db)

    def _acquire(self, holder_label="program-a", ttl=1000):
        code, granted = self.c.run("authority", "acquire", "--holder-label",
                                   holder_label, "--ttl", str(ttl))
        self.assertEqual(code, 0, granted)
        return granted["generation"], granted["token"]

    def _permit(self, generation, token, digest, ttl=60):
        code, body = self.c.run("authority", "permit-dispatch", "--generation",
                                str(generation), "--digest", digest,
                                "--token-stdin", "--ttl", str(ttl), stdin=token)
        self.assertEqual(code, 0, body)
        return body

    def test_no_active_authority_denies_dispatch(self):
        r = self.ev(send("crew_alpha", {"purpose": "explore"}))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("no live Program authority", r["reason"])

    def test_matching_permit_allows_and_is_one_shot(self):
        gen, token = self._acquire()
        event = send("crew_alpha", {"input": "go", "purpose": "explore"},
                     title="wave-9-plan")
        digest = P.canonical_dispatch_digest(
            "sys_session_send", "crew_alpha", "wave-9-plan",
            {"input": "go", "purpose": "explore"})
        self._permit(gen, token, digest)
        r = self.ev(event)
        self.assertEqual(r["result"], "ALLOW", r)
        # one-shot: the identical call again is denied
        r2 = self.ev(event)
        self.assertEqual(r2["result"], "DENY")

    def test_permit_from_a_fenced_out_generation_denied(self):
        """AC-5: dispatch denial for a fenced-out generation."""
        gen1, token1 = self._acquire("program-a")
        event = send("crew_alpha", {"purpose": "explore"}, title="wave-9-plan")
        digest = P.canonical_dispatch_digest(
            "sys_session_send", "crew_alpha", "wave-9-plan", {"purpose": "explore"})
        self._permit(gen1, token1, digest)
        code, _ = self.c.run("authority", "release", "--generation", str(gen1),
                             "--token-stdin", stdin=token1)
        self.assertEqual(code, 0)
        self._acquire("program-b")  # generation advances to 2; gen1 is now fenced out
        r = self.ev(event)
        self.assertEqual(r["result"], "DENY")
        self.assertIn("no unexpired, unconsumed permit", r["reason"])

    def test_digest_mismatch_denied(self):
        gen, token = self._acquire()
        digest = P.canonical_dispatch_digest(
            "sys_session_send", "crew_alpha", "wave-9-plan", {"purpose": "explore"})
        self._permit(gen, token, digest)
        # same agent/title, DIFFERENT args -> different digest -> no match
        r = self.ev(send("crew_alpha", {"purpose": "search"}, title="wave-9-plan"))
        self.assertEqual(r["result"], "DENY")

    def test_non_program_seat_ignored(self):
        """Counted-worker dispatch is worker_semaphore's job, not this
        policy's — it must pass through untouched even with no authority
        held at all."""
        r = self.ev(send("eng_claude", {"slot_id": 1, "task_id": "t",
                                        "purpose": "implement", "generation": 1}))
        self.assertEqual(r["result"], "ALLOW")

    def test_pm_is_not_a_program_direct_child(self):
        """pm is a CREW's own child (crew_alpha dispatches pm), never
        Program's — must never be fenced by this policy."""
        r = self.ev(send("pm", {"purpose": "explore"}))
        self.assertEqual(r["result"], "ALLOW")

    def test_session_create_to_a_fenced_seat_is_also_gated(self):
        gen, token = self._acquire()
        # `platform`, not `staff`: issue #720 / ADR-0091 makes `staff` one of
        # the three seats whose permit must be minted against the review plan,
        # so a bare `--digest` permit no longer dispatches it. That change has
        # its own coverage in tests/test_review_dispatch_gate.py. What THIS
        # test is about is ADR-0044's sequence -- deny, permit, allow -- which
        # is unchanged, and is stated here on a seat the review gate does not
        # claim, so the two rules are tested separately rather than one
        # quietly masking the other.
        event = send("platform", {"purpose": "review"}, title="review-296",
                     tool="sys_session_create")
        r_before_permit = self.ev(event)
        self.assertEqual(r_before_permit["result"], "DENY")
        digest = P.canonical_dispatch_digest(
            "sys_session_create", "platform", "review-296",
            {"purpose": "review"})
        self._permit(gen, token, digest)
        r = self.ev(event)
        self.assertEqual(r["result"], "ALLOW", r)

    def test_fails_closed_without_database(self):
        ev = P.program_authority_fence(db="/nonexistent/nowhere/control.db")
        r = ev(send("crew_alpha", {"purpose": "explore"}))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("not initialised", r["reason"])

    def test_explicit_operator_escape_hatch(self):
        ev = P.program_authority_fence(db="/nonexistent/nowhere/control.db")
        event = send("crew_alpha", {"purpose": "explore"})
        os.environ["NIGHTSHIFT_AUTHORITY_FAIL_OPEN"] = "1"
        try:
            self.assertEqual(ev(event)["result"], "ALLOW")
        finally:
            del os.environ["NIGHTSHIFT_AUTHORITY_FAIL_OPEN"]
        self.assertEqual(ev(event)["result"], "DENY")


# ------------------------------------------------------------------- AC-8


class RootPolicyProvenLoadedFromConfigYaml(unittest.TestCase):
    """AC-8: enforcement proved loaded via a live end-to-end test, not a
    static config read. Parses program/config.yaml's own declared wiring,
    dynamically imports EXACTLY the module.path it names, and exercises a
    real ALLOW/DENY exchange through the function that resolves to — the
    same load-proof shape `release_provenance_canary`'s exact-SHA cutover
    check already gives worker_semaphore, applied here at test time instead
    of at postflight time.
    """

    def test_config_yaml_wiring_resolves_to_working_enforcement(self):
        with open(os.path.join(REPO, "program", "config.yaml")) as f:
            config = yaml.safe_load(f)
        entry = config["guardrails"]["policies"]["program_authority_fence"]
        self.assertEqual(entry["type"], "function")
        # YAML 1.1's "Norway problem": a bare `on:` key resolves to the
        # boolean True, not the string "on" -- this project's config.yaml
        # uses the bare form throughout (see worker_semaphore's own block
        # just above it), so tolerate either resolution rather than assert
        # a specific one.
        on_value = entry.get("on", entry.get(True))
        self.assertIn("tool_call", on_value)
        module_path, _, attr = entry["function"]["path"].rpartition(".")
        self.assertEqual(module_path, "omnipods_policies")
        self.assertEqual(attr, "program_authority_fence")

        module = importlib.import_module(module_path)
        factory = getattr(module, attr)

        c = Ctl()
        arguments = dict(entry["function"]["arguments"])
        arguments["db"] = c.db  # same shape config.yaml declares, isolated db
        ev = factory(**arguments)

        # DENY: no live authority yet — this is the real evaluator running,
        # not a string match against the YAML.
        r = ev(send("crew_alpha", {"purpose": "explore"}))
        self.assertEqual(r["result"], "DENY")

        code, granted = c.run("authority", "acquire", "--holder-label", "p")
        self.assertEqual(code, 0)
        digest = module.canonical_dispatch_digest(
            "sys_session_send", "crew_alpha", "worker-task", {"purpose": "explore"})
        code, _ = c.run("authority", "permit-dispatch", "--generation",
                        str(granted["generation"]), "--digest", digest,
                        "--token-stdin", stdin=granted["token"])
        self.assertEqual(code, 0)

        # ALLOW: the exact module.path config.yaml names now grants it.
        r2 = ev(send("crew_alpha", {"purpose": "explore"}))
        self.assertEqual(r2["result"], "ALLOW", r2)


class ProgramTurnSequenceProvenLoadedFromConfigYaml(unittest.TestCase):
    """174-implement-wiring (issue #319, ADR-0044 Decision §7b): AC-8 at the
    full-Program-turn-sequence level, not just the single-policy-call level
    `RootPolicyProvenLoadedFromConfigYaml` above already gives it. Plays out
    §7b's ordered sequence — acquire, renew across a TTL boundary, two
    permit-dispatch/send round trips against two DIFFERENT
    PROGRAM_DIRECT_CHILDREN agents, release — against the exact
    `program_authority_fence` function `program/config.yaml` declares,
    resolved the same way `RootPolicyProvenLoadedFromConfigYaml` resolves it
    (dynamic import of the declared module.path, not a mock/stub of that
    function or of `canonical_dispatch_digest`), plus the negative case: a
    dispatch with no live authority for the current generation, denied by
    that same real evaluator.
    """

    def _resolve_real_evaluator(self, c):
        with open(os.path.join(REPO, "program", "config.yaml")) as f:
            config = yaml.safe_load(f)
        entry = config["guardrails"]["policies"]["program_authority_fence"]
        module_path, _, attr = entry["function"]["path"].rpartition(".")
        self.assertEqual((module_path, attr),
                          ("omnipods_policies", "program_authority_fence"))
        module = importlib.import_module(module_path)
        factory = getattr(module, attr)
        arguments = dict(entry["function"]["arguments"])
        arguments["db"] = c.db
        return factory(**arguments), module

    def test_full_turn_sequence_two_agents_then_release(self):
        # Real wall-clock Ctl, deliberately, matching
        # RootPolicyProvenLoadedFromConfigYaml above: the in-process
        # `program_authority_fence` evaluator reads `time.time()` directly
        # (there is no `--now` seam for it the way there is for the CLI
        # subprocess), so mixing it with a virtual `Ctl(clock=VIRTUAL_T0)`
        # would make the DB rows' `expires_at` (~1e6) look ancient to the
        # evaluator's real `now()` (~1.8e9) and everything would DENY as
        # spuriously expired. AC-3's renewal-continuity claim over a real TTL
        # boundary is exactly what `tests/test_authority.py::
        # test_renew_before_expiry_keeps_continuous_validity` already proves
        # at the CLI level (cited in ADR-0044 §7b, not re-proved here); this
        # test's job is the full ALLOW/ALLOW/DENY sequence shape against the
        # real evaluator, not re-manufacturing a TTL lapse.
        c = Ctl()
        ev, module = self._resolve_real_evaluator(c)

        # Step 1: acquire (§7b step 1, granted branch).
        code, granted = c.run("authority", "acquire", "--holder-label",
                              "program-turn-319", "--ttl", "1000")
        self.assertEqual(code, 0, granted)
        gen, token = granted["generation"], granted["token"]

        # Step 2 (first agent): digest -> permit-dispatch -> real-evaluator
        # ALLOW.
        event_a = send("crew_alpha", {"purpose": "explore"}, title="wave-9-plan")
        digest_a = module.canonical_dispatch_digest(
            "sys_session_send", "crew_alpha", "wave-9-plan", {"purpose": "explore"})
        code, _ = c.run("authority", "permit-dispatch", "--generation", str(gen),
                        "--digest", digest_a, "--token-stdin", "--ttl", "60",
                        stdin=token)
        self.assertEqual(code, 0)
        self.assertEqual(ev(event_a)["result"], "ALLOW")

        # §7b step 2a: renew before the second dispatch (exercised here as a
        # real call in the sequence; its continuity guarantee is the cited
        # CLI-level test above, not re-asserted via a manufactured lapse).
        code, renewed = c.run("authority", "renew", "--generation", str(gen),
                              "--token-stdin", "--ttl", "1000", stdin=token)
        self.assertEqual(code, 0, renewed)

        # Step 2 (second agent, DIFFERENT PROGRAM_DIRECT_CHILDREN seat,
        # sys_session_create): digest -> permit-dispatch -> real-evaluator
        # ALLOW.
        # `platform` rather than `staff` for the same reason as the §7b
        # single-call test above (issue #720 / ADR-0091): the seat set the
        # review plan gates is covered by its own suite, and this one is about
        # the two-agent turn sequence.
        event_b = send("platform", {"purpose": "review"}, title="review-319",
                       tool="sys_session_create")
        digest_b = module.canonical_dispatch_digest(
            "sys_session_create", "platform", "review-319",
            {"purpose": "review"})
        code, _ = c.run("authority", "permit-dispatch", "--generation", str(gen),
                        "--digest", digest_b, "--token-stdin", "--ttl", "60",
                        stdin=token)
        self.assertEqual(code, 0)
        self.assertEqual(ev(event_b)["result"], "ALLOW")

        # Step 3: release.
        code, _ = c.run("authority", "release", "--generation", str(gen),
                        "--token-stdin", stdin=token)
        self.assertEqual(code, 0)

        # After release, this generation is no longer live — a fresh
        # dispatch attempt (new digest, no permit requested against the
        # released generation) is denied by the same real evaluator,
        # matching §7b step 1's exit-3 losing-the-race path being
        # mechanically indistinguishable from "no live authority at all".
        event_c = send("crew_alpha", {"purpose": "search"}, title="wave-10-plan")
        self.assertEqual(ev(event_c)["result"], "DENY")

    def test_no_live_authority_denied_by_the_real_evaluator(self):
        """§7b step 1's exit-3 (losing-the-race) branch: a fresh control
        plane with no `authority acquire` ever issued denies a
        PROGRAM_DIRECT_CHILDREN dispatch through the real, config-resolved
        evaluator — not a hand-constructed fixture bypassing
        `program/config.yaml`'s declared wiring."""
        c = Ctl(clock=VIRTUAL_T0)
        ev, _module = self._resolve_real_evaluator(c)
        r = ev(send("platform", {"purpose": "review"}, title="w6-lease"))
        self.assertEqual(r["result"], "DENY")
        self.assertIn("no live Program authority", r["reason"])


if __name__ == "__main__":
    unittest.main()
