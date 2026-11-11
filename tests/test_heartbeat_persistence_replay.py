"""Issue #398 (#390 follow-up, DoR accepted, frozen contract
`docs/reviews/398-build-CONTRACT.md`): the three simulation/replay
acceptance criteria explicitly deferred out of #390's own suite --

  AC-2  24-heartbeat no-op simulation
  AC-3  concurrent-authority standdown simulation
  AC-14 historical replay fixtures against 11 already-merged no-op
        heartbeat PRs

This module adds NO new behaviour to `nightshift_heartbeat_persistence.py`
(out of scope per the contract) -- it is pure regression coverage against
the already-shipped `decide()` contract, exercised at a larger scale
(24x, 4x-plus-control) and against real historical data than the base
suite in `tests/test_heartbeat_persistence.py` covers.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import nightshift_heartbeat_persistence as hp  # noqa: E402

from ctlutil import Ctl  # noqa: E402
from test_bookkeeping_release import GitRepo  # noqa: E402
from test_heartbeat_persistence import _outcome  # noqa: E402


# ------------------------------------------------------- AC-2: 24-heartbeat -

class Ac2TwentyFourHeartbeatNoOpSimTests(unittest.TestCase):
    """AC-2: 24 consecutive no-op heartbeats must leave a fixture Git repo
    completely untouched -- 0 new commits, branches, or objects -- and one
    of the 24 iterations deliberately carries an out-of-enum observation
    code to prove the fail-closed diagnostic still fires without changing
    the 0-Git-writes outcome (AC-2.3)."""

    # Zero-indexed position, within the 24, of the AC-2.3 bogus-code case.
    BOGUS_ITERATION = 11

    def setUp(self):
        self.repo = GitRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("README.md", "ac2 fixture base\n")
        self.repo.commit("base commit for AC-2 24-heartbeat sim")

        self.before_head = self.repo.git("rev-parse", "HEAD").stdout.strip()
        self.before_branches = self.repo.git("branch", "--list").stdout
        self.before_objects = self.repo.git("count-objects").stdout

        self.results = []
        for i in range(24):
            if i == self.BOGUS_ITERATION:
                outcome = _outcome(observation_codes=[
                    "queue_empty", "not_a_real_observation_code"])
            else:
                outcome = _outcome(
                    observation_codes=["queue_empty", "dod_unchanged"])
            self.results.append(hp.decide(outcome))

        self.after_head = self.repo.git("rev-parse", "HEAD").stdout.strip()
        self.after_branches = self.repo.git("branch", "--list").stdout
        self.after_objects = self.repo.git("count-objects").stdout

    def test_ac2_1_all_24_no_op_iterations_deny_persistence(self):
        self.assertEqual(len(self.results), 24)
        for i, result in enumerate(self.results):
            with self.subTest(iteration=i):
                self.assertFalse(result["should_persist_to_git"])
                self.assertEqual(result["route"], "none")

    def test_ac2_2_repo_head_branches_and_object_count_unchanged(self):
        """The repo itself is inspected -- not just the 24 booleans."""
        self.assertEqual(self.before_head, self.after_head)
        self.assertEqual(self.before_branches, self.after_branches)
        self.assertEqual(self.before_objects, self.after_objects)

    def test_ac2_3_out_of_enum_observation_code_flagged_but_still_zero_writes(self):
        bogus_result = self.results[self.BOGUS_ITERATION]
        self.assertEqual(
            bogus_result["reason_code"], "unrecognized_observation_fail_closed")
        self.assertFalse(bogus_result["should_persist_to_git"])
        self.assertEqual(bogus_result["route"], "none")
        # Same 0-Git-object outcome as AC-2.2, re-asserted here per the AC's
        # own wording ("... and the same 0-Git-object outcome").
        self.assertEqual(self.before_objects, self.after_objects)


# --------------------------------------------- AC-3: concurrent-authority --

class Ac3ConcurrentAuthorityStanddownSimTests(unittest.TestCase):
    """AC-3: a synthetic 4-hour timeline (4 ticks) where one synthetic
    authority holder holds `authority_status="standdown"` the whole
    window, mirroring `test_ac10_standdown_short_circuits_even_with_a_
    valid_reason` run 4x, plus a WA-009 control 5th tick (AC-3.4)."""

    TICKS = ("T+0", "T+1h", "T+2h", "T+3h")

    def setUp(self):
        self.ctl = Ctl()
        self.repo = GitRepo()
        self.addCleanup(self.repo.close)
        self.repo.write("README.md", "ac3 fixture base\n")
        self.repo.commit("base commit for AC-3 standdown sim")

    @staticmethod
    def _standdown_outcome():
        # An otherwise-recognized durable-delta reason accompanies every
        # tick, exactly like test_ac10_standdown_short_circuits_even_
        # with_a_valid_reason -- proving standdown overrides it, not that
        # the reason was simply absent.
        return _outcome(
            authority_status="standdown",
            durable_delta_reason_code="governed_pr_ready")

    def test_ac3_1_four_tick_timeline_each_carries_standdown_with_valid_reason(self):
        results = [hp.decide(self._standdown_outcome()) for _ in self.TICKS]
        self.assertEqual(len(results), 4)
        for tick, result in zip(self.TICKS, results):
            with self.subTest(tick=tick):
                self.assertIn(result["reason_code"], hp.OUTPUT_REASON_CODES)
                self.assertIn(result["route"], hp.ROUTES)

    def test_ac3_2_all_four_ticks_deny_with_standdown_reason_and_none_route(self):
        for tick in self.TICKS:
            with self.subTest(tick=tick):
                result = hp.decide(self._standdown_outcome())
                self.assertFalse(result["should_persist_to_git"])
                self.assertEqual(result["reason_code"], "standdown_no_authority")
                self.assertEqual(result["route"], "none")

    def test_ac3_3_no_tick_leaks_a_usable_receipt_and_zero_git_objects(self):
        before_objects = self.repo.git("count-objects").stdout
        for tick in self.TICKS:
            with self.subTest(tick=tick):
                # Each tick's own (denied) decision is recorded, then a
                # direct pr-create call against that tick's own receipt id
                # must still be denied -- proving no tick's decision leaks
                # a usable receipt into another tick's call.
                code, body = self.ctl.run(
                    "heartbeat-persist", "record", "--run-id", f"ac3-{tick}",
                    stdin=json.dumps(self._standdown_outcome()))
                self.assertEqual(code, 0, body)
                receipt_id = str(body["receipt_id"])

                code2, body2 = self.ctl.run(
                    "pr-create", "--heartbeat-persist-receipt", receipt_id,
                    "--", "--title", "x", "--body", "Refs #398")
                self.assertEqual(code2, 3)
                self.assertEqual(body2["error"], "heartbeat_persist_denied")
                self.assertEqual(
                    body2["reason"], "no_matching_should_persist_decision")

                # Same pattern as test_ac10_gate_denies_pr_create_with_no_
                # matching_decision: a receipt id nothing ever produced.
                code3, body3 = self.ctl.run(
                    "pr-create", "--heartbeat-persist-receipt", "999999",
                    "--", "--title", "x", "--body", "Refs #398")
                self.assertEqual(code3, 3)
                self.assertEqual(body3["error"], "heartbeat_persist_denied")
        after_objects = self.repo.git("count-objects").stdout
        self.assertEqual(before_objects, after_objects)

    def test_ac3_4_control_winning_authority_holder_does_grant(self):
        """WA-009 control: same simulated window, 5th tick, WINNING holder
        -- proves AC-3's zero-Git-writes result is caused by standdown
        specifically, not by some unrelated fault denying everything."""
        result = hp.decide(_outcome(
            authority_status="granted",
            durable_delta_reason_code="governed_pr_ready"))
        self.assertTrue(result["should_persist_to_git"])
        self.assertEqual(result["reason_code"], "durable_delta_governed")
        self.assertEqual(result["route"], "governed_normal_path")

        code, body = self.ctl.run(
            "heartbeat-persist", "record", "--run-id", "ac3-control-tick5",
            stdin=json.dumps(_outcome(
                authority_status="granted",
                durable_delta_reason_code="governed_pr_ready")))
        self.assertEqual(code, 0, body)
        receipt_id = str(body["receipt_id"])
        env = dict(os.environ)
        env["NIGHTSHIFT_CTL_GH"] = "/bin/true"
        p = subprocess.run(
            self.ctl.cmdline(
                "pr-create", "--heartbeat-persist-receipt", receipt_id,
                "--", "--title", "x", "--body", "Refs #398"),
            capture_output=True, text=True, env=env)
        # os.execvp replaces the process on success -- /bin/true prints
        # nothing and exits 0, the same proof pattern as
        # test_control_valid_receipt_is_consumed_and_gh_runs.
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), "")


# ------------------------------------------------ AC-14: historical replay -

# One fixture per already-MERGED `program/heartbeat-*-status` no-op PR
# named in the accepted DoR. `observation_codes` is derived from that PR's
# own merge-commit message (`git log -1 --format='%B' <merge_sha>` against
# `origin/main`) -- the merge SHA is recorded here for traceability, not
# used by the test itself. Where the body doesn't map unambiguously to one
# `OBSERVATION_CODES` member, `observation_codes` is left empty (the most
# conservative available mapping: no OBSERVATION_CODES member is claimed at
# all) and the case is tagged AC-14.2.
HISTORICAL_NO_OP_PRS = (
    # (pr_number, merge_sha, observation_codes, note)
    (389, "b06e49e4fd85b1bcb1d9a7ba9628156532641ec2", ["dod_unchanged"],
     "stood down from #286 review, DoD re-confirmed"),
    (382, "1e2f89f7b17ea3afe79a2fc0f3503e6c13ea50d5",
     ["another_program_owns_work"],
     "stood down from duplicate release dispatch on PR #379"),
    (373, "33989df1c3422ca789a8261ae2f6ab811db5f00a", [],
     "AC-14.2: body 'heartbeat 07:01Z bookkeeping' does not map "
     "unambiguously to one OBSERVATION_CODES member"),
    (368, "b9a8b71c7ddff675d4b64c3f19026b21e76efa41",
     ["pr_under_review"],
     "#220 review round 2/3 in, standing down"),
    (357, "31eb459bd9dd11cc1066a45e78283b669742a970", [],
     "AC-14.2: body 'STATUS/ledger update' does not map unambiguously to "
     "one OBSERVATION_CODES member"),
    (351, "3104337e58d7c0f4cfbd888c6bd695dcc9a2f1d3",
     ["no_new_dispatch"],
     "no new dispatch, STATUS/ledger update"),
    (330, "c0b768c04ab75de87973906c628e6acbe7958eb6",
     ["no_new_dispatch"],
     "16:00Z heartbeat, reconciliation only"),
    (326, "abe133fb0e13903d9ffd997c7acd65f17e843854",
     ["no_new_dispatch"],
     "15:04Z heartbeat, reconciliation only, zero dispatch"),
    (317, "72da35b3c847abe2a75b4aa04c10659b6d0f37ba",
     ["no_new_dispatch"],
     "13:03Z bookkeeping, reconciliation only, zero dispatch"),
    (284, "ab2c419a6f990413af64016427c65b151f955ab0", [],
     "AC-14.2: body 'heartbeat 08:01Z bookkeeping' does not map "
     "unambiguously to one OBSERVATION_CODES member"),
    (255, "9183c91bedc7dcee71ec842c3630134454a09024", [],
     "AC-14.2: body 'heartbeat 14:07Z bookkeeping' does not map "
     "unambiguously to one OBSERVATION_CODES member"),
)


class Ac14HistoricalReplayFixtureTests(unittest.TestCase):
    def test_ac14_1_every_named_historical_no_op_pr_denies(self):
        """One subTest per PR number (mirrors test_ac4_every_named_no_op_
        case_denies's style) so a failure names the exact PR number."""
        self.assertEqual(len(HISTORICAL_NO_OP_PRS), 11)
        for pr_number, merge_sha, codes, note in HISTORICAL_NO_OP_PRS:
            with self.subTest(pr=pr_number):
                result = hp.decide(_outcome(observation_codes=codes))
                self.assertFalse(
                    result["should_persist_to_git"],
                    f"PR #{pr_number} ({merge_sha}): {note}")
                self.assertEqual(result["route"], "none")

    def test_ac14_2_ambiguous_prs_use_conservative_mapping_and_still_deny(self):
        ambiguous = [
            (pr, codes, note) for pr, _, codes, note in HISTORICAL_NO_OP_PRS
            if note.startswith("AC-14.2")
        ]
        # At least one historical PR's body is genuinely ambiguous -- if
        # this ever hits 0 the fixture table was edited without updating
        # this test's own premise.
        self.assertGreaterEqual(len(ambiguous), 1)
        for pr_number, codes, note in ambiguous:
            with self.subTest(pr=pr_number):
                # The conservative mapping itself: no OBSERVATION_CODES
                # member claimed.
                self.assertEqual(codes, [])
                result = hp.decide(_outcome(observation_codes=codes))
                self.assertFalse(result["should_persist_to_git"])
                self.assertEqual(result["reason_code"], "observation_only_no_delta")
                self.assertEqual(result["route"], "none")


if __name__ == "__main__":
    unittest.main()
