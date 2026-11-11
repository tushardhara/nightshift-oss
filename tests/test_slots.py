"""Audit tests 2 and 3: the global worker semaphore.

- N crews hammering `slot acquire` concurrently never exceed the budget,
  and the on-call reserve is honoured (normal workers cap at max-reserve).
- A worker that dies holding a slot returns its capacity by TTL, with no
  human and no LLM remembering anything.
"""

import contextlib
import os
import sqlite3
import time
import unittest
from unittest import mock

from ctlutil import Ctl, run_parallel, VIRTUAL_T0


class RT17ConcurrentAcquiresShareOneBudget(unittest.TestCase):
    def test_concurrent_acquires_converge_on_one_normal_worker_ceiling(self):
        max_workers = 7
        reserve = 2
        c = Ctl(capacity={
            "max_concurrent_workers": max_workers,
            "reserve_for_oncall": reserve,
        })
        # ADR-0069 section 5: normal allocation is not caller-identity keyed.
        # Independently produced attempts vary real task, holder, and crew facts;
        # no label pretends the shared CLI can distinguish their producer.
        commands = []
        for i in range(14):
            crew = "crew_alpha" if i % 2 == 0 else "crew_beta"
            agent = ("eng_claude" if crew == "crew_alpha"
                     else "beta_eng_claude")
            commands.append(c.normal_slot_cmdline(
                task=f"rt17-contender-{i}", crew=crew, agent=agent,
                title=f"rt17-contender-{i}",
            ))

        results = run_parallel(commands)
        successes = [body for code, body in results if code == 0]
        losers = [(code, body) for code, body in results if code != 0]
        self.assertEqual(len(successes), max_workers - reserve, results)
        self.assertEqual(len(losers), len(commands) - len(successes), results)
        self.assertTrue(all(code == 3 for code, _ in losers), losers)
        self.assertTrue(all(body and body.get("granted") is False
                            for _, body in losers), losers)

        code, listing = c.run("slot", "list")
        self.assertEqual(code, 0, listing)
        self.assertEqual(len(listing["active"]), len(successes))

        # At saturation, normal attempts from either crew cannot consume reserve.
        for i, (crew, agent) in enumerate((
            ("crew_alpha", "eng_claude"),
            ("crew_beta", "beta_eng_claude"),
        )):
            code, body = c.acquire_normal(
                task=f"rt17-saturated-{i}", crew=crew, agent=agent,
                title=f"rt17-saturated-{i}",
            )
            self.assertEqual(code, 3, body)
            self.assertEqual(body["reason"], "worker budget exhausted")

        code, authority = c.run(
            "authority", "acquire", "--holder-label", "rt17-authority")
        self.assertEqual(code, 0, authority)
        code, body = c.run(
            "slot", "acquire", "--holder", "oncall", "--agent", "oncall",
            "--title", "rt17-incident", "--purpose", "implement",
            "--oncall", "--authority-generation", str(authority["generation"]),
            "--authority-token-stdin", stdin=authority["token"],
        )
        self.assertEqual(code, 0, body)

    def test_oncall_reserve_rejects_missing_and_wrong_authority_token(self):
        c = Ctl()
        command = (
            "slot", "acquire", "--holder", "oncall", "--agent", "oncall",
            "--title", "rt17-unauthorized", "--purpose", "implement",
            "--oncall",
        )
        with mock.patch.dict(
                os.environ, {"NIGHTSHIFT_CTL_AUTHORITY_TOKEN": ""}):
            code, body = c.run(*command)
            self.assertEqual(code, 3, body)
            self.assertFalse(body["granted"], body)
            self.assertIn("authority token", body["reason"])

            code, authority = c.run(
                "authority", "acquire", "--holder-label", "rt17-authority")
            self.assertEqual(code, 0, authority)
            code, body = c.run(
                *command,
                "--authority-generation", str(authority["generation"]),
                "--authority-token-stdin", stdin="not-the-authority-token",
            )
            self.assertEqual(code, 3, body)
            self.assertFalse(body["granted"], body)
            self.assertIn("authority token", body["reason"])

        code, listing = c.run("slot", "list")
        self.assertEqual(code, 0, listing)
        self.assertEqual(listing["active"], [])


class GlobalSemaphore(unittest.TestCase):
    def test_concurrent_acquires_respect_budget_and_reserve(self):
        c = Ctl()  # max 4, reserve 1 → 3 normal slots
        cmds = [
            c.normal_slot_cmdline(
                task=f"capacity-{i}",
                crew="crew_alpha" if i % 2 == 0 else "crew_beta",
                agent="eng_claude" if i % 2 == 0 else "beta_eng_claude",
            )
            for i in range(12)
        ]
        results = run_parallel(cmds)
        granted = [r for code, r in results if code == 0]
        denied = [r for code, r in results if code == 3]
        self.assertEqual(len(granted), 3)
        self.assertEqual(len(denied), 9)
        code, listing = c.run("slot", "list")
        self.assertEqual(len(listing["active"]), 3)

    def test_oncall_reserve_usable_only_by_oncall(self):
        c = Ctl()
        for i in range(3):
            code, _ = c.acquire_normal(task=f"reserve-{i}")
            self.assertEqual(code, 0)
        code, _ = c.acquire_normal(task="reserve-3")
        self.assertEqual(code, 3)  # normal denied at max-reserve
        # ADR-0044 Decision §3b (task 174-implement-fence): the oncall
        # reserve slot now additionally requires a live, current-generation
        # Program authority token.
        code, granted = c.run("authority", "acquire", "--holder-label", "p")
        self.assertEqual(code, 0, granted)
        code, body = c.run("slot", "acquire", "--holder", "oncall",
                           "--agent", "oncall", "--title", "incident",
                           "--purpose", "implement", "--oncall",
                           "--authority-generation", str(granted["generation"]),
                           "--authority-token-stdin", stdin=granted["token"])
        self.assertEqual(code, 0)  # reserve is the incident lane's
        code, _ = c.run("slot", "acquire", "--holder", "oncall2",
                        "--agent", "oncall2", "--title", "incident-2",
                        "--purpose", "implement", "--oncall",
                        "--authority-generation", str(granted["generation"]),
                        "--authority-token-stdin", stdin=granted["token"])
        self.assertEqual(code, 3)  # hard ceiling still applies

    def test_dead_worker_slot_recovered_by_ttl(self):
        # Long TTLs while asserting capacity (never a race against subprocess
        # startup on a slow runner), then shorten explicitly to test expiry.
        c = Ctl(clock=VIRTUAL_T0)
        slots = []
        for i in range(3):
            code, g = c.acquire_normal(task=f"ttl-{i}", ttl="600")
            self.assertEqual(code, 0)
            slots.append(g["slot_id"])
        code, _ = c.acquire_normal(task="ttl-late")
        self.assertEqual(code, 3)
        # the "workers" die: their leases run out. Each shortens its OWN
        # ttl — ADR-0002 §4a made --holder required, and this call is fixture
        # machinery, so it names the holder it always implicitly was. The
        # assertion below is unchanged.
        for i, s in enumerate(slots):
            c.run("slot", "heartbeat", "--slot", s,
                  "--holder", "crew_alpha/eng_claude",
                  "--ttl", "0.15")
        c.advance(0.3)  # virtual time (ADR-0005 D4); was time.sleep(0.3)
        code, body = c.acquire_normal(task="ttl-late")
        self.assertEqual(code, 0, body)
        _, listing = c.run("slot", "list")
        self.assertEqual(len(listing["active"]), 1)

    def test_release_returns_capacity(self):
        c = Ctl()
        _, g = c.acquire_normal(task="release-0")
        for i in range(2):
            c.acquire_normal(task=f"release-{i + 1}")
        code, _ = c.acquire_normal(task="release-3")
        self.assertEqual(code, 3)
        code, _ = c.run("slot", "release", "--slot", g["slot_id"],
                        "--holder", "crew_alpha/eng_claude")
        self.assertEqual(code, 0)
        code, _ = c.acquire_normal(task="release-3")
        self.assertEqual(code, 0)

    def test_concurrent_consume_one_winner(self):
        c = Ctl()
        _, g = c.acquire_normal(task="t1", title="consume-t1")
        cmds = [
            c.cmdline("slot", "consume", "--slot", g["slot_id"],
                      "--agent", "eng_claude", "--title", "consume-t1")
            for i in range(6)
        ]
        results = run_parallel(cmds)
        winners = [r for code, r in results if code == 0]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len([r for code, r in results if code == 3]), 5)

    def test_consume_binding_and_reuse(self):
        c = Ctl()
        _, g = c.acquire_normal(task="binding", title="binding")
        code, body = c.run("slot", "consume", "--slot", g["slot_id"],
                           "--agent", "eng_codex", "--title", "binding")
        self.assertEqual(code, 3)  # bound to eng_claude
        code, _ = c.run("slot", "consume", "--slot", g["slot_id"],
                        "--agent", "eng_claude", "--title", "binding")
        self.assertEqual(code, 0)
        code, body = c.run("slot", "consume", "--slot", g["slot_id"],
                           "--agent", "eng_claude", "--title", "binding")
        self.assertEqual(code, 3)  # one dispatch per slot
        self.assertIn("already authorized", body["reason"])
        # consumed-but-unreleased still counts against the budget, and
        # release still frees it
        code, _ = c.run("slot", "release", "--slot", g["slot_id"],
                        "--holder", "crew_alpha/eng_claude")
        self.assertEqual(code, 0)

    def test_renew_active_respects_max_age(self):
        c = Ctl(clock=VIRTUAL_T0)
        c.acquire_normal(task="renew-a", ttl="600")
        code, r = c.run("slot", "renew-active", "--ttl", "60",
                        "--max-age", "86400")
        self.assertEqual(r["renewed"], 1)
        # prove renew-active actually REPLACES the TTL: shrink it, slot expires
        c.run("slot", "renew-active", "--ttl", "0.15", "--max-age", "86400")
        c.advance(0.3)  # virtual time (ADR-0005 D4); was time.sleep(0.3)
        _, listing = c.run("slot", "list")
        self.assertEqual(len(listing["active"]), 0)
        # a slot older than max-age is NOT renewed (leak cap)
        c.acquire_normal(task="renew-b", ttl="600")
        # Under the wall clock the ~10ms between these two commands made the
        # slot older than --max-age by accident. Virtual time puts them at the
        # SAME instant, so the gap the assertion depends on must be stated.
        c.advance(1)
        code, r = c.run("slot", "renew-active", "--ttl", "60", "--max-age",
                        "0.0001")
        self.assertEqual(r["renewed"], 0)

    def test_heartbeat_keeps_slot_alive(self):
        """CHANGED by ADR-0002 §4a. This test previously renewed a slot with
        no identity argument at all. It now proves the holder can still renew
        its OWN slot — the capability is unchanged; only unattributed renewal
        is gone. This is a STRENGTHENING of a control, not a weakening: the
        pre-change version passes against a command that lets anyone with a
        slot id renew anyone's slot.
        """
        c = Ctl(clock=VIRTUAL_T0)
        _, g = c.acquire_normal(task="heartbeat-w", ttl="600")
        # heartbeat SETS the ttl (either direction) — shrinking proves it took
        code, _ = c.run("slot", "heartbeat", "--slot", g["slot_id"],
                        "--holder", "crew_alpha/eng_claude", "--ttl", "0.15")
        self.assertEqual(code, 0)
        c.advance(0.3)  # virtual time (ADR-0005 D4); was time.sleep(0.3)
        _, listing = c.run("slot", "list")
        self.assertEqual(len(listing["active"]), 0)
        # and extending is the normal direction
        _, g2 = c.acquire_normal(task="heartbeat-w2", ttl="600")
        code, _ = c.run("slot", "heartbeat", "--slot", g2["slot_id"],
                        "--holder", "crew_alpha/eng_claude", "--ttl", "1200")
        self.assertEqual(code, 0)

    def test_heartbeat_requires_the_holder_to_prove_identity(self):
        """ADR-0002 §4a. Renewal is a capability of the HOLDER, not of
        whoever knows an integer. Same check, same wording family as
        consume_slot (bin/nightshift-ctl, `consume_slot`)."""
        c = Ctl()
        _, mine = c.acquire_normal(task="t1", ttl="600")
        _, theirs = c.acquire_normal(
            task="t2", crew="crew_beta", agent="beta_eng_codex", ttl="600",
        )

        # the holder renews its own slot: allowed, by full name or by agent
        code, _ = c.run("slot", "heartbeat", "--slot", mine["slot_id"],
                        "--holder", "crew_alpha/eng_claude", "--ttl", "600")
        self.assertEqual(code, 0)
        code, _ = c.run("slot", "heartbeat", "--slot", mine["slot_id"],
                        "--holder", "eng_claude", "--ttl", "600")
        self.assertEqual(code, 0)

        # a DIFFERENT holder cannot renew it — exit 3, denied, not an error
        code, r = c.run("slot", "heartbeat", "--slot", mine["slot_id"],
                        "--holder", "crew_beta/beta_eng_codex", "--ttl", "600")
        self.assertEqual(code, 3)
        self.assertFalse(r["ok"])
        self.assertIn("acquired for holder", r["reason"])

        # ... and the denial did not renew it as a side effect
        _, listing = c.run("slot", "list")
        got = {s["id"]: s["expires_at"] for s in listing["active"]}
        self.assertIn(mine["slot_id"], got)

        # the watchdog, which holds no slot, may renew NOTHING (ADR-0002 §4)
        code, r = c.run("slot", "heartbeat", "--slot", theirs["slot_id"],
                        "--holder", "watchdog", "--ttl", "600")
        self.assertEqual(code, 3)

    def test_heartbeat_without_a_holder_is_refused(self):
        """The defect verbatim: `slot heartbeat --slot <n>` with no identity.
        Before ADR-0002 §4a this renewed the slot and exited 0."""
        c = Ctl()
        _, g = c.acquire_normal(task="missing-holder", ttl="600")
        code, _ = c.run("slot", "heartbeat", "--slot", g["slot_id"],
                        "--ttl", "600")
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()


class ContinuationBudgetGate(unittest.TestCase):
    """Issue #216 (ADR-0032 + ADR-0045): ``cmd_slot_acquire``'s own
    ``continuation`` branch of ``_derive_normal_phase`` must deny a
    continuation slot request when the task's ``reason_code`` is exactly
    ``"budget_expired"`` (AC1), leave no durable trace of the attempt (AC3),
    keep an already-invalid request's own more specific denial (AC1/AC7),
    stay byte-identical when there is no budget at all (AC6), fail closed on
    a missing task (AC8), and never key off ``dispatchable`` generically,
    which would deny every ordinary continuation dispatch unconditionally
    (AC9).
    """

    def _admit(self, c, task, agent="eng_claude", crew="crew_alpha"):
        """Seed post-root-policy admission facts directly (same shape and
        rationale as ``ctlutil.Ctl.seed_admission``): this class's subject is
        the continuation-branch budget check, a downstream consumer of an
        already-admitted task, not the admission transaction itself -- and a
        real ``worker_semaphore`` dispatch always uses wall-clock ``now()``
        internally (it has no ``--now`` override), which cannot be mixed
        with this class's injected virtual clock. ``acquire_normal`` (a pure
        CLI call, which DOES respect the virtual clock) still creates the
        task and the real NEW-phase slot row this reuses."""
        code, g = c.acquire_normal(task=task, crew=crew, agent=agent)
        self.assertEqual(code, 0, g)
        title = f"fixture-{task}"
        started_at = c.clock if c.clock is not None else time.time()
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute(
                "INSERT INTO task_admissions("
                "task_id,new_slot_id,crew,agent,title,admitted_at) "
                "VALUES(?,?,?,?,?,?)",
                (task, g["slot_id"], crew, agent, title, started_at),
            )
            cx.execute(
                "UPDATE tasks SET state='building',started_at=? WHERE id=?",
                (started_at, task),
            )
            cx.execute(
                "UPDATE worker_slots SET dispatched_at=?,bound_agent=?,"
                "bound_title=? WHERE id=?",
                (started_at, agent, title, g["slot_id"]),
            )

    def test_continuation_denied_when_budget_expired_qa(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-cont-qa"
        self._admit(c, task)
        code, body = c.run("task", "upsert", "--id", task,
                            "--budget-seconds", "30",
                            "--pr-url", "https://example.invalid/pr/1")
        self.assertEqual(code, 0, body)
        c.advance(31)
        code, before = c.run("slot", "list")
        self.assertEqual(code, 0)
        before_ids = {row["id"] for row in before["active"]}
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", task, "--agent", "qa", "--title", f"fixture-{task}",
            "--purpose", "implement",
        )
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "budget_expired")
        # AC3: the denial COMMITs with no new worker_slots row for the attempt
        code, after = c.run("slot", "list")
        self.assertEqual(code, 0)
        self.assertEqual({row["id"] for row in after["active"]}, before_ids)

    def test_continuation_denied_when_budget_expired_review(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-cont-review"
        self._admit(c, task)
        code, body = c.run("task", "upsert", "--id", task,
                            "--budget-seconds", "30",
                            "--pr-url", "https://example.invalid/pr/2")
        self.assertEqual(code, 0, body)
        c.advance(31)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_claude",
            "--crew", "crew_alpha", "--task", task, "--agent", "reviewer_claude",
            "--title", f"fixture-{task}", "--purpose", "review",
        )
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "budget_expired")

    def test_continuation_denied_when_budget_expired_implementer(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-cont-impl"
        self._admit(c, task)
        code, claim = c.run("claim", "acquire", "--task", task,
                             "--claimant", "crew_alpha", "--agent", "eng_claude",
                             "--title", f"fixture-{task}")
        self.assertEqual(code, 0, claim)
        code, body = c.run("task", "upsert", "--id", task, "--budget-seconds", "30")
        self.assertEqual(code, 0, body)
        c.advance(31)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", task, "--agent", "eng_claude",
            "--title", f"fixture-{task}", "--purpose", "implement",
            "--claim-id", str(claim["claim_id"]),
            "--claim-generation", str(claim["generation"]),
            "--claim-run-id", claim["run_id"],
        )
        self.assertEqual(code, 3, body)
        self.assertEqual(body.get("reason_code"), "budget_expired")

    def test_ordinary_non_expired_continuation_unaffected_qa(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-ok-qa"
        self._admit(c, task)
        code, body = c.run("task", "upsert", "--id", task,
                            "--budget-seconds", "600",
                            "--pr-url", "https://example.invalid/pr/3")
        self.assertEqual(code, 0, body)
        c.advance(1)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", task, "--agent", "qa", "--title", f"fixture-{task}",
            "--purpose", "implement",
        )
        self.assertEqual(code, 0, body)

    def test_ordinary_non_expired_continuation_unaffected_review(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-ok-review"
        self._admit(c, task)
        code, body = c.run("task", "upsert", "--id", task,
                            "--budget-seconds", "600",
                            "--pr-url", "https://example.invalid/pr/4")
        self.assertEqual(code, 0, body)
        c.advance(1)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", task, "--agent", "reviewer_codex",
            "--title", f"fixture-{task}", "--purpose", "review",
        )
        self.assertEqual(code, 0, body)

    def test_ordinary_non_expired_continuation_unaffected_implementer(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-ok-impl"
        self._admit(c, task)
        code, claim = c.run("claim", "acquire", "--task", task,
                             "--claimant", "crew_alpha", "--agent", "eng_claude",
                             "--title", f"fixture-{task}")
        self.assertEqual(code, 0, claim)
        code, body = c.run("task", "upsert", "--id", task, "--budget-seconds", "600")
        self.assertEqual(code, 0, body)
        c.advance(1)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", task, "--agent", "eng_claude",
            "--title", f"fixture-{task}", "--purpose", "implement",
            "--claim-id", str(claim["claim_id"]),
            "--claim-generation", str(claim["generation"]),
            "--claim-run-id", claim["run_id"],
        )
        self.assertEqual(code, 0, body)

    def test_already_invalid_continuation_keeps_its_specific_denial_even_if_budget_expired(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-cont-invalid"
        self._admit(c, task)
        # budget expired AND no --pr-url set: the pre-existing PR-evidence
        # denial (AC1/AC7 ordering) must win, not budget_expired.
        code, body = c.run("task", "upsert", "--id", task, "--budget-seconds", "30")
        self.assertEqual(code, 0, body)
        c.advance(31)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", task, "--agent", "qa", "--title", f"fixture-{task}",
            "--purpose", "implement",
        )
        self.assertEqual(code, 3, body)
        self.assertIn("PR evidence", body.get("reason", ""))
        self.assertNotEqual(body.get("reason_code"), "budget_expired")

    def test_continuation_unaffected_when_budget_seconds_is_null(self):
        c = Ctl(clock=VIRTUAL_T0)
        task = "budget-cont-null"
        self._admit(c, task)  # budget_seconds never set -> NULL
        c.advance(10_000)
        code, body = c.run("task", "upsert", "--id", task,
                            "--pr-url", "https://example.invalid/pr/5")
        self.assertEqual(code, 0, body)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", task, "--agent", "qa", "--title", f"fixture-{task}",
            "--purpose", "implement",
        )
        self.assertEqual(code, 0, body)

    def test_missing_task_at_slot_acquire_fails_closed_via_existing_handling(self):
        c = Ctl(clock=VIRTUAL_T0)
        code, body = c.run("run", "current", "--set", "fixture-run")
        self.assertEqual(code, 0, body)
        code, body = c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa", "--crew", "crew_alpha",
            "--task", "does-not-exist", "--agent", "qa", "--title", "x",
            "--purpose", "implement",
        )
        self.assertEqual(code, 3, body)
        self.assertIn("unknown task", body.get("reason", ""))


class RT32UnauthenticatedReleaseCannotDuplicateDispatch(unittest.TestCase):
    """RT-32 (DoD condition 18, issue #503/F-1): `slot release` and `lease
    release` must prove the caller is the actual holder before releasing --
    otherwise an unrelated caller frees a slot/lease still legitimately in
    use, and a second acquisition for the identical (task, agent, title) (or
    resource) becomes possible while the first is still live: the exact
    two-simultaneous-ALLOW scenario `docs/audit/CONCURRENCY.md` Q1/Q3 (F-1)
    demonstrated end-to-end in `probe2.py`. Reproduces it here and asserts
    it is now refused."""

    def test_slot_release_wrong_holder_denied_no_duplicate_row(self):
        c = Ctl()
        code, granted = c.acquire_normal(
            task="t-rt32-slot", crew="crew_alpha", agent="eng_claude",
            title="ship-t-rt32-slot", purpose="implement")
        self.assertEqual(code, 0, granted)
        slot_id = granted["slot_id"]

        # An unrelated caller (wrong holder) cannot free it.
        code, body = c.run("slot", "release", "--slot", str(slot_id),
                            "--holder", "crew_alpha/eng_codex")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertIn("crew_alpha/eng_claude", body["reason"])

        # No duplicate row: re-acquiring the identical (task, agent, title)
        # returns the SAME slot, not a second live one.
        code, retry = c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", "t-rt32-slot",
            "--agent", "eng_claude", "--title", "ship-t-rt32-slot",
            "--purpose", "implement")
        self.assertEqual(code, 0, retry)
        self.assertTrue(retry["reused"])
        self.assertEqual(retry["slot_id"], slot_id)

        # Counterfactual (WA-008): the REAL holder can free it, and only
        # then does a fresh acquire mint a genuinely new row.
        code, rel = c.run("slot", "release", "--slot", str(slot_id),
                           "--holder", "crew_alpha/eng_claude")
        self.assertEqual(code, 0, rel)
        code, fresh = c.run(
            "slot", "acquire", "--holder", "crew_alpha/eng_claude",
            "--crew", "crew_alpha", "--task", "t-rt32-slot",
            "--agent", "eng_claude", "--title", "ship-t-rt32-slot",
            "--purpose", "implement")
        self.assertEqual(code, 0, fresh)
        self.assertNotEqual(fresh["slot_id"], slot_id)

    def test_lease_release_wrong_holder_denied_no_duplicate_grant(self):
        c = Ctl()
        mig = "db/migrations/**"
        code, granted = c.run("lease", "acquire", "--resource", mig,
                               "--holder", "crew_alpha", "--mode", "serialize")
        self.assertEqual(code, 0, granted)

        code, body = c.run("lease", "release", "--resource", mig,
                            "--holder", "crew_beta", "--event", "pr-merged")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])

        # Still held: a second crew cannot acquire it.
        code, body = c.run("lease", "acquire", "--resource", mig,
                            "--holder", "crew_beta", "--mode", "serialize")
        self.assertEqual(code, 3, body)

        # Counterfactual: the real holder releases, then it's free.
        code, rel = c.run("lease", "release", "--resource", mig,
                           "--holder", "crew_alpha", "--event", "pr-merged")
        self.assertEqual(code, 0, rel)
        code, body = c.run("lease", "acquire", "--resource", mig,
                            "--holder", "crew_beta", "--mode", "serialize")
        self.assertEqual(code, 0, body)


if __name__ == "__main__":
    unittest.main()
