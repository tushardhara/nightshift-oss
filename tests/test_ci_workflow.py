"""The CI trigger and Release's explanation must remain the same contract."""

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as source:
        return source.read()


class CIWorkflowContractTest(unittest.TestCase):
    def setUp(self):
        self.workflow = read(".github/workflows/tests.yml")
        self.release = read("program/agents/release/config.yaml")

    def test_pr_and_main_are_the_only_automatic_trigger_contract(self):
        trigger = self.workflow.split("on:\n", 1)[1].split("\njobs:\n", 1)[0]
        self.assertIn("  workflow_dispatch:\n", trigger)
        self.assertIn('  push:\n    branches: [main]\n', trigger)
        self.assertIn("  pull_request:\n", trigger)
        self.assertNotIn('branches: ["**"]', trigger)
        self.assertNotIn("paths:", trigger)
        self.assertNotIn("paths-ignore:", trigger)

    def test_pr_runs_supersede_each_other_but_main_runs_never_do(self):
        """PR runs are newest-wins; every merge commit gets its own run.

        A PR groups by number, so pushing again to the same PR cancels the
        older run -- correct, and it frees a runner.

        A push groups by SHA, so each merge commit is alone in its group and
        no later merge can cancel it. Keying pushes on `github.ref` instead
        put EVERY merge commit into one group under cancel-in-progress: on
        2026-09-02 four consecutive merges to main (41f2f90 #499, 9c215e5
        #497, 2dd0dd2 #500, 1f3066b #493) each killed the previous merge's
        verification run, one of them after 7 seconds, leaving main with no
        post-integration run since 3fe45f1. The main run is the only place
        the suite ever executes against the merged result, and a cancelled
        check reads as "not failed", so the gap was silent.
        """
        self.assertIn(
            "group: ${{ github.workflow }}-"
            "${{ github.event.pull_request.number || github.sha }}",
            self.workflow,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.workflow,
        )
        # Regression guards -- these two spellings are the outage itself.
        self.assertNotIn("|| github.ref }}", self.workflow)
        self.assertNotIn("cancel-in-progress: true", self.workflow)

    def test_release_knows_when_to_wait_for_ci(self):
        for statement in (
            "A feature-branch push does not run `control-plane tests` by itself",
            "there are no path exclusions",
            "same PR cancels its older in-progress run",
            # These must not span a line break -- the YAML is hard-wrapped.
            "Pushes to `main` are grouped per commit SHA",
            "treat `cancelled` on a `main` SHA as",
            "Every push to `main`, including bookkeeping-only changes, runs the suite",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.release)


if __name__ == "__main__":
    unittest.main()
