"""Issue #745: the org publishes a pull request and cannot hear it.

After opening PR #737 on a customer repository the org ran its OWN reviewer,
moved the task to `fixing`, and spent 31 minutes deliberating while the
customer's reviewer had filed four HIGH findings and CI had gone red. Neither
reached it.

That is not an oversight. `program/agents/release/config.yaml` tells the seat
that on an external repository it must "STOP HERE ... record the task
`delivered`", and explains why: the required-check identity is a rule about
THIS repository, so evaluating it against a customer's would drop a green PR
and send a crew chasing a failure that never happened. Sound reasoning that
produced NO evaluation instead of a DIFFERENT one.

These test the different one. The check fixture is the real check-run set from
PR #737, not invented data.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import nightshift_github as gh  # noqa: E402


def run(name, status="completed", conclusion="success"):
    return {"id": 1, "app_id": 15368, "app_slug": "github-actions",
            "name": name, "status": status, "conclusion": conclusion}


#: Exactly what `repos/example-org/example-repo/commits/95eece30/check-runs`
#: returned. A 10-second `lint` failure took four other jobs with it as skipped.
PR_737 = [
    run("lint", conclusion="failure"),
    run("go-tests", conclusion="skipped"),
    run("build-images", conclusion="skipped"),
    run("cypress-e2e-tests", conclusion="skipped"),
    run("on-comment", conclusion="skipped"),
    run("on-pr"),
    run("post-summary"),
]


class TheRealPullRequestIsJudgedCorrectly(unittest.TestCase):
    def test_pr_737_is_adverse(self):
        verdict = gh.observed_check_verdict(PR_737)
        self.assertTrue(verdict["adverse"])
        self.assertEqual(verdict["failed"], ["lint"])

    def test_pr_737_reports_that_the_signal_was_lost(self):
        """The finding that matters. Four jobs never ran, so this build did not
        merely fail -- it stopped reporting. A run that reads it as one red
        check concludes it has test coverage it does not have."""
        verdict = gh.observed_check_verdict(PR_737)
        self.assertTrue(verdict["signal_lost"])
        self.assertIn("go-tests", verdict["skipped"])
        self.assertIn("cypress-e2e-tests", verdict["skipped"])


class SkipsAreNotFailures(unittest.TestCase):
    def test_an_all_skipped_build_is_not_adverse(self):
        """Workflows skip legitimately -- a path filter that matches nothing is
        not a failure. Counting skips as failures would make the org chase
        outages that never happened, which is the harm `release` was avoiding
        when it stopped reading CI at all."""
        verdict = gh.observed_check_verdict(
            [run("go-tests", conclusion="skipped"), run("lint", conclusion="skipped")])
        self.assertFalse(verdict["adverse"])
        self.assertFalse(verdict["signal_lost"])

    def test_a_still_running_check_is_pending_not_adverse(self):
        """A pull request read seconds after it opens has every check queued.
        Calling that adverse dispatches a crew at a build that has not run --
        the 'chase a failure that never happened' harm this path exists to
        avoid, reintroduced by the fix for it."""
        verdict = gh.observed_check_verdict(
            [run("lint"), run("go-tests", status="queued", conclusion=None)])
        self.assertFalse(verdict["adverse"])
        self.assertTrue(verdict["pending"])

    def test_a_superseded_run_does_not_claim_the_build_stopped_reporting(self):
        """`cancelled` is routine wherever a workflow sets cancel-in-progress.
        Folding it into `skipped` made a superseded run announce that nothing
        green was evidence of anything."""
        verdict = gh.observed_check_verdict(
            [run("a", conclusion="failure"), run("b", conclusion="cancelled")])
        self.assertFalse(verdict["signal_lost"])
        self.assertEqual(verdict["superseded"], ["b"])

    def test_conclusions_that_are_not_failures_are_not_treated_as_failures(self):
        """`neutral` passes branch protection; `cancelled` is routine wherever a
        workflow sets cancel-in-progress; `stale` means superseded. Each one
        called a failure is a crew dispatched at nothing."""
        for conclusion in ("neutral", "cancelled", "stale"):
            with self.subTest(conclusion=conclusion):
                verdict = gh.observed_check_verdict([run("x", conclusion=conclusion)])
                self.assertFalse(verdict["adverse"], conclusion)

    def test_real_failures_are_still_failures(self):
        """The negative control for the test above: widening the passing set
        until nothing is adverse would pass it just as well."""
        for conclusion in ("failure", "timed_out", "action_required"):
            with self.subTest(conclusion=conclusion):
                self.assertTrue(
                    gh.observed_check_verdict([run("x", conclusion=conclusion)])["adverse"])

    def test_no_checks_at_all_is_flagged_rather_than_read_as_green(self):
        """`evaluate_required_checks` refuses an empty set outright. Dropping
        that refusal without replacing it would make a project with no CI
        indistinguishable from one that passed."""
        verdict = gh.observed_check_verdict([])
        self.assertFalse(verdict["adverse"])
        self.assertTrue(verdict["no_checks"])


class TheStatusesReaderProvesItSawEverything(unittest.TestCase):
    """The function added to stop evidence being missed must not miss evidence.

    Both sibling readers refuse without a completeness proof. This one is the
    only place the response hands us a server-side total, so trusting the page
    would be a choice -- and the wrong one here above all, since a dropped
    failure comes back as a green verdict and the seat records `delivered`."""

    def _payload(self, total, n):
        return {"total_count": total,
                "statuses": [{"context": f"c{i}", "state": "success"}
                             for i in range(n)]}

    def test_a_truncated_page_is_refused_not_trusted(self):
        with self.assertRaises(gh.CheckSetRefusal) as caught:
            gh.enumerate_commit_statuses(
                "o/r", "sha", getter=lambda _e: self._payload(500, 100))
        self.assertEqual(str(caught.exception), "status_set_incomplete")

    def test_a_complete_page_is_accepted(self):
        """The negative control: refusing everything would pass the test above."""
        got = gh.enumerate_commit_statuses(
            "o/r", "sha", getter=lambda _e: self._payload(3, 3))
        self.assertEqual(len(got), 3)

    def test_a_failing_status_becomes_a_failure(self):
        got = gh.enumerate_commit_statuses("o/r", "sha", getter=lambda _e: {
            "total_count": 1, "statuses": [{"context": "snyk", "state": "error"}]})
        self.assertTrue(gh.observed_check_verdict(got)["adverse"])


class ReviewsAreReadAsGitHubReadsThem(unittest.TestCase):
    def _reviews(self, *rows):
        return gh.enumerate_reviews("o/r", 737, getter=lambda _e: list(rows))

    def test_the_customers_reviewer_is_visible(self):
        reviews = self._reviews(
            {"user": {"login": "owner-review[bot]"},
             "state": "CHANGES_REQUESTED", "submitted_at": "2026-09-08T10:19:00Z"})
        self.assertEqual(gh.latest_review_states(reviews),
                         {"owner-review[bot]": "CHANGES_REQUESTED"})

    def test_a_later_comment_does_not_clear_a_blocking_review(self):
        """GitHub's own rule, and the one that would silently unblock a PR if
        this took the last review of any kind."""
        reviews = self._reviews(
            {"user": {"login": "v"}, "state": "CHANGES_REQUESTED",
             "submitted_at": "2026-09-08T10:00:00Z"},
            {"user": {"login": "v"}, "state": "COMMENTED",
             "submitted_at": "2026-09-08T11:00:00Z"})
        self.assertEqual(gh.latest_review_states(reviews), {"v": "CHANGES_REQUESTED"})

    def test_a_later_approval_does_clear_it(self):
        reviews = self._reviews(
            {"user": {"login": "v"}, "state": "CHANGES_REQUESTED",
             "submitted_at": "2026-09-08T10:00:00Z"},
            {"user": {"login": "v"}, "state": "APPROVED",
             "submitted_at": "2026-09-08T12:00:00Z"})
        self.assertEqual(gh.latest_review_states(reviews), {"v": "APPROVED"})

    def test_two_reviewers_are_kept_apart(self):
        """The org has its own reviewer. Collapsing authors would let its own
        approval hide a customer's blocking review -- which is the shape of the
        original failure, where it acted on its own review and never saw theirs."""
        reviews = self._reviews(
            {"user": {"login": "reviewer_codex"}, "state": "APPROVED",
             "submitted_at": "2026-09-08T10:19:00Z"},
            {"user": {"login": "owner-review[bot]"},
             "state": "CHANGES_REQUESTED", "submitted_at": "2026-09-08T10:20:00Z"})
        states = gh.latest_review_states(reviews)
        self.assertEqual(states["reviewer_codex"], "APPROVED")
        self.assertEqual(states["owner-review[bot]"], "CHANGES_REQUESTED")

    def _rows(self, n):
        return [{"user": {"login": f"r{i}"}, "state": "APPROVED",
                 "submitted_at": "2026-09-08T10:00:00Z"} for i in range(n)]

    def test_exactly_one_full_page_is_answered_not_refused(self):
        """100 reviews fit in the page requested. Refusing them would reject a
        pull request that can be read completely -- and the reviews endpoint
        returns no server-side total, so page length alone proves nothing."""
        self.assertEqual(
            len(gh.enumerate_reviews("o/r", 1, getter=lambda _e: self._rows(100))), 100)

    def test_more_than_one_page_is_refused_rather_than_half_answered(self):
        with self.assertRaises(gh.CheckSetRefusal):
            gh.enumerate_reviews("o/r", 1, getter=lambda _e: self._rows(101))


class TheReleaseSeatIsActuallyToldToDoIt(unittest.TestCase):
    """The config is prose an agent is asked to follow, so the least this can do
    is pin the words the behaviour depends on -- the technique this repository
    already uses on this same file (`test_seat_contract.ReleaseCheckIdentity`,
    `test_external_repo_merge_fence.ReleaseConfigAgreesWithMechanism`).

    It does not make the seat obey. Nothing here can. What it stops is the
    instruction being silently deleted or reverted while the helpers stay,
    which is how a feature becomes three functions nobody calls."""

    @classmethod
    def setUpClass(cls):
        import yaml
        path = (Path(__file__).resolve().parents[1] / "program" / "agents"
                / "release" / "config.yaml")
        cls.text = yaml.safe_load(path.read_text(encoding="utf-8"))["prompt"]

    def test_the_external_path_names_the_observation_helpers(self):
        for token in ("observed_check_verdict", "latest_review_states",
                      "enumerate_commit_statuses"):
            self.assertIn(token, self.text, token)

    def test_delivered_is_no_longer_unconditional_on_the_external_path(self):
        """The defect that made the first version of this change inert: a new
        instruction upstream, and the old `record the task as delivered` still
        standing unqualified at the point of action."""
        self.assertIn("ONLY IF step 6's reading of", self.text)

    def test_a_pending_build_is_not_described_as_a_fix_task(self):
        self.assertIn("is NOT adverse and is not a fix task", self.text)


if __name__ == "__main__":
    unittest.main()
