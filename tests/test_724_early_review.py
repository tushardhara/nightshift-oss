"""Issue #724 / ADR-0092: a reviewer may start before a PR exists, and what
it finds is recorded and counted.

Two things were in the way, and fixing either alone is worse than fixing
neither. `slot acquire` refused a review continuation without `task.pr_url`
(ADR-0033 §5), so a reviewer could not be dispatched until the very end. And
`review log` refused an entry whose `pr` was not a positive integer, with the
retro aggregating on `(repo, pr)` -- so a review that DID somehow run early
could not be written down, and #716's agreed measure is computed from exactly
those records. A capability whose output vanishes is the shape this epic
exists to name.

Every gate here is asserted in both directions. The PR requirement is not
deleted, it is REPLACED: an early review is admissible while the implementer's
claim is live, which is control-plane state a reviewer cannot assert for
itself. So each permit test has a refusal beside it that differs only in that
evidence.
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from ctlutil import Ctl, REPO

sys.path.insert(0, os.path.join(REPO, "deploy", "policies"))
sys.path.insert(0, os.path.join(REPO, "bin"))
import omnipods_policies as P  # noqa: E402
import nightshift_review_log as review_log  # noqa: E402


HEAD = "c" * 40


def send(agent, title, args):
    return {"type": "tool_call",
            "data": {"name": "sys_session_send",
                     "arguments": {"agent": agent, "title": title,
                                   "args": args}}}


class EarlyReviewSlotTests(unittest.TestCase):
    """The slot gate: `bin/nightshift-ctl` slot acquire."""

    def setUp(self):
        self.c = Ctl()
        code, body = self.c.run("run", "current", "--set", "run-724")
        self.assertEqual(code, 0, body)
        self.policy = P.worker_semaphore(db=self.c.db)
        self._admit()

    # -- fixture -----------------------------------------------------------

    def _admit(self, task="T", agent="eng_claude", title="ship-T", pr=None):
        argv = ["task", "upsert", "--id", task, "--crew", "crew_alpha",
                "--agent", agent, "--title", title, "--state", "pending",
                "--size", "S"]
        if pr:
            argv += ["--pr-url", pr]
        code, body = self.c.run(*argv)
        self.assertEqual(code, 0, body)
        code, granted = self.c.run(
            "slot", "acquire", "--holder", f"crew_alpha/{agent}",
            "--crew", "crew_alpha", "--task", task, "--agent", agent,
            "--title", title, "--purpose", "implement", "--ttl", "600")
        self.assertEqual(code, 0, granted)
        result = self.policy(send(agent, title,
                                  {"purpose": "implement",
                                   "input": f"implement {task}"}))
        self.assertEqual(result["result"], "ALLOW", result)
        return granted

    def _claim(self, task="T", claimant="crew_alpha/eng_claude"):
        code, body = self.c.run("claim", "acquire", "--task", task,
                                "--claimant", claimant, "--agent", "eng_claude",
                                "--title", "ship-T")
        self.assertEqual(code, 0, body)
        return body

    def _acquire_review(self, task="T", agent="reviewer_codex"):
        return self.c.run(
            "slot", "acquire", "--holder", f"crew_alpha/{agent}",
            "--crew", "crew_alpha", "--task", task, "--agent", agent,
            "--title", "ship-T", "--purpose", "review", "--ttl", "600")

    def _slot_kinds(self):
        with sqlite3.connect(self.c.db) as cx:
            return [r[0] for r in cx.execute(
                "SELECT continuation_kind FROM worker_slots "
                "WHERE continuation_kind IS NOT NULL ORDER BY id")]

    def _set_pr(self, url, task="T"):
        code, body = self.c.run("task", "upsert", "--id", task, "--pr-url", url)
        self.assertEqual(code, 0, body)

    # -- tests -------------------------------------------------------------

    def test_a_reviewer_may_start_before_a_pr_exists(self):
        """AC-1, the permit half."""
        self._claim()
        code, granted = self._acquire_review()
        self.assertEqual(code, 0, granted)
        self.assertEqual(self._slot_kinds(), ["review"])

    def test_without_a_live_claim_it_is_refused(self):
        """AC-1's negative control. The PR requirement is replaced, not
        removed: with no build in flight there is nothing to review, and the
        acquisition must fail exactly as it did before this change."""
        code, denied = self._acquire_review()
        self.assertEqual(code, 3, denied)
        self.assertIn("active claim", denied["reason"])
        self.assertEqual(self._slot_kinds(), [])

    def test_a_released_claim_closes_the_window_again(self):
        """The claim bounds the early window without needing a timer."""
        claim = self._claim()
        code, granted = self._acquire_review()
        self.assertEqual(code, 0, granted)
        code, body = self.c.run("slot", "release", "--slot",
                                str(granted["slot_id"]),
                                "--holder", "crew_alpha/reviewer_codex")
        self.assertEqual(code, 0, body)
        code, body = self.c.run("claim", "release", "--task", "T",
                                "--claimant", "crew_alpha/eng_claude")
        self.assertEqual(code, 0, body)
        code, denied = self._acquire_review()
        self.assertEqual(code, 3, denied)
        self.assertIn("active claim", denied["reason"])

    def test_once_a_pr_exists_the_pr_is_still_sufficient(self):
        """The ordinary path is untouched: a PR admits a review with no claim
        needed, exactly as before this change."""
        self._set_pr("https://github.com/o/r/pull/7")
        code, granted = self._acquire_review()
        self.assertEqual(code, 0, granted)
        self.assertEqual(self._slot_kinds(), ["review"])

    def test_qa_still_requires_a_pr(self):
        """Decision 2: #716 item I calls QA's PR requirement legitimate. This
        change must not quietly relax it on the way past."""
        self._claim()
        code, denied = self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/qa",
            "--crew", "crew_alpha", "--task", "T", "--agent", "qa",
            "--title", "ship-T", "--purpose", "implement", "--ttl", "600")
        self.assertEqual(code, 3, denied)
        self.assertIn("PR evidence", denied["reason"])

    def test_two_early_reviews_of_one_task_have_one_winner(self):
        """AC-3. Decided by the index, not by luck."""
        self._claim()
        code, first = self._acquire_review()
        self.assertEqual(code, 0, first)
        code, second = self._acquire_review(agent="reviewer_claude")
        self.assertEqual(code, 3, second)
        self.assertIn("continuation slot", second["reason"], second)
        self.assertEqual(self._slot_kinds(), ["review"])

    def test_an_early_review_holds_the_slot_the_pr_review_will_want(self):
        """The consequence of reusing the `review` kind, asserted rather than
        assumed: one active review continuation per task, so an early review
        makes a post-PR review WAIT. A wait, not a deadlock -- releasing the
        slot frees it. Pinned because the alternative reading (two kinds, two
        slots) is what a future reader is most likely to 'fix' it into."""
        self._claim()
        code, granted = self._acquire_review()
        self.assertEqual(code, 0, granted)
        self._set_pr("https://github.com/o/r/pull/7")
        code, blocked = self._acquire_review(agent="reviewer_claude")
        self.assertEqual(code, 3, blocked)
        self.assertIn("continuation slot", blocked["reason"], blocked)

        code, body = self.c.run("slot", "release", "--slot",
                                str(granted["slot_id"]),
                                "--holder", "crew_alpha/reviewer_codex")
        self.assertEqual(code, 0, body)
        code, after = self._acquire_review(agent="reviewer_claude")
        self.assertEqual(code, 0, after)

    def test_a_claim_from_another_run_does_not_admit_a_review(self):
        """The claim is bound, not merely counted. An unbound `state='active'`
        lookup would accept a claim left over from a previous run against an
        older admission -- evidence that a build WAS in flight, once."""
        self._claim()
        with sqlite3.connect(self.c.db) as cx:
            cx.execute("UPDATE task_claims SET run_id='some-earlier-run' "
                       "WHERE task_id='T'")
        code, denied = self._acquire_review()
        self.assertEqual(code, 3, denied)
        self.assertIn("active claim", denied["reason"], denied)

    def test_a_claim_naming_another_agent_does_not_admit_a_review(self):
        self._claim()
        with sqlite3.connect(self.c.db) as cx:
            cx.execute("UPDATE task_claims SET agent='reviewer_codex' "
                       "WHERE task_id='T'")
        code, denied = self._acquire_review()
        self.assertEqual(code, 3, denied)


class TheFenceAgreesWithTheSlotTests(unittest.TestCase):
    """AC-2. The root policy has its own copy of this rule. If it does not
    learn the new kind, the reviewer holds a slot the fence will not honour --
    a capability that exists and cannot be used."""

    def setUp(self):
        self.c = Ctl()
        code, body = self.c.run("run", "current", "--set", "run-724")
        self.assertEqual(code, 0, body)
        self.semaphore = P.worker_semaphore(db=self.c.db)
        base = EarlyReviewSlotTests._admit
        self.admit = lambda: base(self)
        self.policy = self.semaphore
        self.admit()

    def _claim(self):
        code, body = self.c.run("claim", "acquire", "--task", "T",
                                "--claimant", "crew_alpha/eng_claude",
                                "--agent", "eng_claude", "--title", "ship-T")
        self.assertEqual(code, 0, body)

    def _acquire_review(self):
        return self.c.run(
            "slot", "acquire", "--holder", "crew_alpha/reviewer_codex",
            "--crew", "crew_alpha", "--task", "T", "--agent", "reviewer_codex",
            "--title", "ship-T", "--purpose", "review", "--ttl", "600")

    def test_the_fence_allows_the_dispatch_the_slot_permitted(self):
        self._claim()
        code, granted = self._acquire_review()
        self.assertEqual(code, 0, granted)
        verdict = self.semaphore(
            send("reviewer_codex", "ship-T", {"purpose": "review",
                                              "input": "review T"}))
        self.assertEqual(verdict["result"], "ALLOW", verdict)

    def test_the_fence_denies_it_once_the_claim_is_gone(self):
        """The negative control, at the fence rather than the CLI: the slot
        was legitimately acquired and the evidence then lapsed."""
        self._claim()
        code, granted = self._acquire_review()
        self.assertEqual(code, 0, granted)
        code, body = self.c.run("claim", "release", "--task", "T",
                                "--claimant", "crew_alpha/eng_claude")
        self.assertEqual(code, 0, body)
        verdict = self.semaphore(
            send("reviewer_codex", "ship-T", {"purpose": "review",
                                              "input": "review T"}))
        self.assertEqual(verdict["result"], "DENY", verdict)
        # Named, because this DENY is also what the pre-change fence returned
        # for a task with no PR -- without the reason the test cannot tell
        # "rechecked the claim and found it gone" from "never learned about
        # claims at all", and would pass with the whole change reverted.
        self.assertIn("continuation", verdict["reason"].lower(), verdict)


def report(**overrides):
    base = {
        "repo": "example-org/example-service",
        "pr": 4211,
        "task_id": "t1",
        "head_sha": HEAD,
        "domain": "cross_crew_review",
        "author_seat": "crew_alpha/eng_codex",
        "reviewer_seat": "crew_beta/beta_reviewer_claude",
        "vendor": "Claude",
        "verdict": "fail",
        "loops": 1,
        "independent": "yes",
        "slot_state": "absent",
        "slot_absence_reason": "reviewer dispatched outside the semaphore",
        "provenance": "local",
        "recorded_at": "2026-09-06T10:26:50Z",
        "findings": [{"severity": "MERGE_BLOCKER", "criterion": "AC-1",
                      "summary": "the fence is inert"}],
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


class RecordingAReviewThatHasNoPrTests(unittest.TestCase):
    """AC-4/AC-6. Without this the slot change ships findings that vanish."""

    def setUp(self):
        self.c = Ctl()
        self.reviews = os.path.join(self.c.omni, "reviews.md")

    def log(self, payload):
        return self.c.run("review", "log", "--report-json", "-",
                          "--reviews-path", self.reviews,
                          stdin=json.dumps(payload))

    def test_a_review_with_no_pr_is_recorded(self):
        code, body = self.log(report(pr=None))
        self.assertEqual(code, 0, body)
        text = Path(self.reviews).read_text(encoding="utf-8")
        self.assertIn("before any PR", text)
        self.assertIn("t1", text)

    def test_a_defect_that_escapes_a_pre_pr_pass_is_scored(self):
        """AC-4, and the question #716 actually asked: did reviewing earlier
        catch anything? A pre-PR review and the PR's own reviews are reviews
        of the SAME WORK. Keyed apart, a defect that passed the early review
        and surfaced at the PR sits in two disjoint buckets and scores zero --
        the instrument structurally unable to answer its own question.
        """
        code, body = self.log(report(pr=None, task_id="t1", verdict="pass",
                                     findings=[],
                                     recorded_at="2026-09-06T10:00:00Z"))
        self.assertEqual(code, 0, body)
        code, body = self.log(report(pr=4211, task_id="t1", verdict="fail",
                                     recorded_at="2026-09-06T12:00:00Z"))
        self.assertEqual(code, 0, body)

        code, after = self.c.run("review", "escaped-defects",
                                 "--reviews-path", self.reviews)
        self.assertEqual(code, 0, after)
        self.assertEqual(after["entries"], 2)
        self.assertEqual(after["prs_reviewed"], 1,
                         "the pre-PR review and its own PR counted as two")
        self.assertEqual(after["escaped"], 1,
                         "a defect that escaped the early pass scored zero")

    def test_one_defect_reported_on_both_sides_is_counted_once(self):
        """The same finding raised pre-PR and again at the PR is one defect.
        Counted twice it inflates the headline number in exactly the direction
        that flatters this feature."""
        for pr, when in ((None, "10:00"), (4211, "11:30")):
            code, body = self.log(report(
                pr=pr, task_id="t1", verdict="fail",
                recorded_at=f"2026-09-06T{when}:00Z"))
            self.assertEqual(code, 0, body)
        code, body = self.log(report(pr=None, task_id="t1", verdict="pass",
                                     findings=[],
                                     recorded_at="2026-09-06T09:00:00Z"))
        self.assertEqual(code, 0, body)
        code, after = self.c.run("review", "escaped-defects",
                                 "--reviews-path", self.reviews)
        self.assertEqual(code, 0, after)
        self.assertEqual(after["escaped"], 1, after)

    def test_two_pre_pr_reviews_of_different_tasks_are_two_units(self):
        """The subtle one. Keying a PR-less entry on `(repo, None)` would
        merge every pre-PR review in a repository into a single unit -- one
        'PR' reviewed many times and passing once, which manufactures an
        escaped defect that nobody observed."""
        self.assertEqual(
            review_log.review_unit({"repo": "o/r", "pr": None,
                                    "task_id": "t1"}),
            ("o/r", "task:t1"))
        self.assertNotEqual(
            review_log.review_unit({"repo": "o/r", "pr": None,
                                    "task_id": "t1"}),
            review_log.review_unit({"repo": "o/r", "pr": None,
                                    "task_id": "t2"}))

    def test_a_task_id_can_never_collide_with_a_pr_number(self):
        self.assertNotEqual(
            review_log.review_unit({"repo": "o/r", "pr": 7, "task_id": "x"}),
            review_log.review_unit({"repo": "o/r", "pr": None,
                                    "task_id": "7"}))

    def test_a_pr_less_entry_must_name_its_task(self):
        """Otherwise it is unattributable, and counts as a unit of nothing."""
        code, denied = self.log(report(pr=None, task_id=None))
        self.assertEqual(code, 3, denied)
        # The REASON, not merely a refusal: without it this passes on any
        # unrelated rejection and would survive the check being deleted.
        self.assertEqual(denied["reason"], "pre_pr_review_requires_task_id")

    def test_only_a_local_review_may_omit_the_pr(self):
        """A reconstructed entry is replaying history, and the reviews it
        describes had PRs. An attested one needs a comment to attest to."""
        code, denied = self.log(report(pr=None, provenance="reconstructed",
                                       findings_recovered=False))
        self.assertEqual(code, 3, denied)
        self.assertEqual(denied["reason"], "pr_required_for_this_provenance")

    def test_a_malformed_pr_is_still_refused(self):
        """The contract widened by exactly one value -- null -- not by type."""
        for bad in (0, -1, "4211", True):
            code, denied = self.log(report(pr=bad))
            self.assertEqual(code, 3, (bad, denied))
            self.assertEqual(denied["reason"], "pr_invalid", (bad, denied))

    def test_a_pre_pr_review_mints_no_receipt(self):
        """The worst defect this change had, kept as a regression.

        `review_receipts.pr_url` is NOT NULL and its only job is to LOCATE the
        PR. A pre-PR entry has no PR, so the first implementation wrote the
        literal string `.../pull/None` -- and the no-overwrite rule then made
        that fabricated, findings-free receipt FINAL for
        `(task_id, head_sha, domain)`. The real post-PR review of the same
        head could then no longer record its MERGE_BLOCKER: a passing receipt
        with no findings standing in front of a blocker, which is precisely
        what the overwrite rule exists to prevent.
        """
        code, body = self.log(report(pr=None))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"], body)
        self.assertEqual(body["receipt_skipped_reason"],
                         "pre_pr_review_has_no_attestable_pr")
        with sqlite3.connect(self.c.db) as cx:
            rows = list(cx.execute("SELECT pr_url FROM review_receipts"))
        self.assertEqual(rows, [], "a pre-PR review minted a receipt")

    def test_the_pr_review_can_still_record_its_blocker_afterwards(self):
        """The other half: having reviewed early must not cost the real
        review its receipt. Same head, same domain, PR arrives later."""
        code, body = self.log(report(pr=None, verdict="pass", findings=[]))
        self.assertEqual(code, 0, body)
        code, body = self.c.run(
            "task", "upsert", "--id", "t1", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "ship",
            "--pr-url", "https://github.com/example-org/example-service/pull/4211")
        self.assertEqual(code, 0, body)
        code, body = self.log(report(pr=4211, verdict="fail",
                                     recorded_at="2026-09-06T12:00:00Z"))
        self.assertEqual(code, 0, body)
        self.assertTrue(body["receipt_recorded"], body)
        with sqlite3.connect(self.c.db) as cx:
            verdicts = [r[0] for r in cx.execute(
                "SELECT verdict FROM review_receipts")]
            findings = list(cx.execute("SELECT 1 FROM review_findings"))
        self.assertEqual(verdicts, ["fail"], "the blocker's receipt was lost")
        self.assertTrue(findings, "the blocker was not recorded in the plane")

    def test_an_ordinary_pr_review_is_unchanged(self):
        """AC-6: history must not be reinterpreted by a widened format."""
        code, body = self.log(report())
        self.assertEqual(code, 0, body)
        text = Path(self.reviews).read_text(encoding="utf-8")
        self.assertIn("PR #4211", text)
        self.assertNotIn("before any PR", text)
        code, after = self.c.run("review", "escaped-defects",
                                 "--reviews-path", self.reviews)
        self.assertEqual(code, 0, after)
        self.assertEqual(after["entries"], 1)


if __name__ == "__main__":
    unittest.main()
