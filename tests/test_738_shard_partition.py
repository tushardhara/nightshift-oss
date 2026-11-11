"""Issue #738: the suite may be split across runners without losing a test.

**Read this first.** Sharding makes a new failure possible: a module that
falls out of every shard is a module nothing runs, and the build goes green
faster while covering less. That is exactly the failure #729 exists to catch,
arriving through the door marked "faster CI" — so the partition being TOTAL
and DISJOINT is not a detail of this change, it is the change's entire safety
argument, and it is asserted here for many shard counts rather than for the
one the workflow happens to use.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_suite  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent


class ThePartitionLosesNothing(unittest.TestCase):
    def setUp(self):
        self.modules = sorted(
            {run_suite._module_of(i) for i in run_suite._all_test_ids()})
        self.assertGreater(len(self.modules), 50, "discovery found almost nothing")

    def test_every_module_lands_in_exactly_one_shard(self):
        """The load-bearing property, for every shard count that could be
        configured -- not only the one in the workflow today."""
        for shards in range(1, 13):
            bins = run_suite.partition(self.modules, shards)
            union = set().union(*bins)
            placed = sum(len(b) for b in bins)
            self.assertEqual(union, set(self.modules),
                             f"N={shards}: a module is in no shard")
            self.assertEqual(placed, len(union),
                             f"N={shards}: a module is in two shards")

    def test_the_shards_collect_exactly_what_one_run_collects(self):
        """The strongest form of the safety property: not "every module is in
        a bin" but "unittest, asked for these shards, loads the identical set
        of TEST IDS it loads for the whole suite".

        Measured when written: 2930 ids, union equal, 0 lost, 0 duplicated.
        Counts alone would not show this -- the shards are deliberately uneven
        by count (816/723/629/762) because they are balanced by DURATION.
        """
        import unittest as ut
        loader = ut.TestLoader()
        union, total = set(), 0
        for shard in run_suite.partition(self.modules, 4):
            ids = set()
            for module in shard:
                ids |= run_suite._flatten_ids(loader.loadTestsFromName(module))
            total += len(ids)
            union |= ids
        full = run_suite._all_test_ids()
        self.assertEqual(union, full,
                         f"{len(full - union)} test ids are in no shard")
        self.assertEqual(total, len(union),
                         f"{total - len(union)} test ids are in two shards")

    def test_one_shard_is_the_whole_suite(self):
        """`--shard 1/1` must be exactly today's behaviour, so the unsharded
        path stays available and comparable."""
        self.assertEqual(run_suite.partition(self.modules, 1),
                         [sorted(self.modules)])

    def test_more_shards_than_modules_is_allowed_and_empty_ones_are_empty(self):
        bins = run_suite.partition(["a", "b"], 5)
        self.assertEqual(sorted(x for b in bins for x in b), ["a", "b"])
        self.assertEqual(sum(1 for b in bins if not b), 3)

    def test_the_split_is_deterministic_given_the_same_module_set(self):
        """`partition` sorts its input, so input ORDER cannot matter -- which
        makes comparing forward against reversed a tautology, and it is only
        kept here to pin that sort in place."""
        self.assertEqual(run_suite.partition(self.modules, 4),
                         run_suite.partition(list(reversed(self.modules)), 4))

    def test_a_differing_module_set_reshuffles_every_shard(self):
        """The determinism that actually matters, stated as the hazard it is.

        Each runner computes its own assignment from its own discovery. The
        assignment is a function of the WHOLE module list, so if one runner
        discovers even one module the others do not, its bins are not "the
        same split plus one" -- they are a different split, and modules run
        twice or not at all with every shard reporting success.

        Nothing compares the module list across runners at the end of a build,
        so the safety of the split rests entirely on discovery being identical
        everywhere. This test exists to show the consequence is real, which is
        why `_modules_to_shard` refuses ANY discovery anomaly rather than
        carrying on with a set that is merely plausible.
        """
        full = run_suite.partition(self.modules, 4)
        without_one = run_suite.partition(self.modules[:-1], 4)
        moved = sum(1 for a, b in zip(full, without_one)
                    if set(a) - {self.modules[-1]} != set(b))
        self.assertGreater(
            moved, 0,
            "dropping a module left every shard unchanged, so this test can "
            "no longer demonstrate the hazard it documents")

    def test_a_broken_partition_rule_is_caught(self):
        """The negative control for this whole file: prove these assertions
        can fail. A rule that drops the last module passes nothing here."""
        def dropping(modules, shards):
            bins = run_suite.partition(modules, shards)
            bins[-1] = bins[-1][:-1]
            return bins
        bins = dropping(self.modules, 3)
        union = set().union(*bins)
        self.assertNotEqual(union, set(self.modules),
                            "the fixture did not actually drop anything")

    def test_zero_shards_is_refused(self):
        with self.assertRaises(ValueError):
            run_suite.partition(self.modules, 0)


class TheWeightsOnlyAffectBalance(unittest.TestCase):
    def test_a_missing_weight_does_not_drop_a_module(self):
        """A newly added module has no measured weight. It must still be
        placed -- treated as median, never skipped."""
        bins = run_suite.partition(["brand_new_module", "test_watchdog"], 2)
        self.assertEqual(sorted(x for b in bins for x in b),
                         ["brand_new_module", "test_watchdog"])

    def test_weights_are_advisory_and_absence_is_survivable(self):
        """If the weights file is missing or malformed the split must still be
        total -- slower, never smaller."""
        original = run_suite.SHARD_WEIGHTS_PATH
        run_suite.SHARD_WEIGHTS_PATH = Path("/nonexistent/weights.json")
        self.addCleanup(setattr, run_suite, "SHARD_WEIGHTS_PATH", original)
        bins = run_suite.partition(["a", "b", "c"], 2)
        self.assertEqual(sorted(x for b in bins for x in b), ["a", "b", "c"])


class TheWeightsAreRealAndCurrent(unittest.TestCase):
    """The weights only affect BALANCE -- a stale file makes the split slower,
    never smaller, and the totality tests above hold regardless. But a file
    that has rotted badly silently un-balances CI, so its decay is measured."""

    def setUp(self):
        import json
        self.data = json.loads(
            run_suite.SHARD_WEIGHTS_PATH.read_text(encoding="utf-8"))
        self.times = self.data["modules"]
        self.modules = sorted(
            {run_suite._module_of(i) for i in run_suite._all_test_ids()})

    def test_most_modules_have_a_measurement(self):
        """Rot is gradual: modules get added without re-timing. Below this the
        balance is guesswork and the file should be regenerated."""
        known = [m for m in self.modules if m in self.times]
        self.assertGreater(len(known) / len(self.modules), 0.8,
                           f"only {len(known)} of {len(self.modules)} modules "
                           "have measured weights -- regenerate the file")

    def test_a_non_numeric_entry_cannot_break_the_split(self):
        """The file carries a comment and a total beside the measurements. A
        stray string reaching the sort turned an advisory hint into a hard
        failure of the entire suite."""
        weights = run_suite._weights()
        self.assertTrue(all(isinstance(v, (int, float)) for v in weights.values()))
        self.assertNotIn("_comment", weights)

    def _loads(self, bins):
        """Score a split the way `partition` scores it.

        Using a different default for an unweighted module (1.0 rather than
        the median) measures a split nobody computed, and can report a
        balanced partition as unbalanced or the reverse.
        """
        known = sorted(v for v in self.times.values()
                       if isinstance(v, (int, float)))
        median = known[len(known) // 2]
        return [sum(self.times.get(m, median) for m in b) for b in bins]

    def test_four_shards_are_balanced(self):
        """AC-4 as a test rather than a claim: the split must actually be
        even, or four runners buy nothing over one.

        Note what this can and cannot show. It scores LPT with the same
        measurements LPT used, so it proves the ARITHMETIC is even, not that
        CI is: real runners differ in speed, and the observed spread on the
        first sharded build was 3m00 to 5m03 against these equal weights.
        Balancing work is all this file claims to do.
        """
        loads = self._loads(run_suite.partition(self.modules, 4))
        self.assertLess((max(loads) - min(loads)) / max(loads), 0.10,
                        f"shards are unbalanced: {[round(x) for x in loads]}")

    def test_no_split_can_beat_the_longest_module(self):
        """The floor, stated so nobody expects more from more runners: one
        module cannot be split across machines, so the slowest shard can never
        be faster than the slowest module."""
        longest = max(self.times.values())
        for shards in (4, 12, 200):
            loads = self._loads(run_suite.partition(self.modules, shards))
            self.assertGreaterEqual(
                max(loads), longest,
                f"N={shards}: the makespan fell below the longest single "
                "module, which means a module was split or lost")


class TheArgumentParses(unittest.TestCase):
    def test_both_spellings(self):
        self.assertEqual(run_suite._parse_shard(["--shard", "2/5"]), ([], (2, 5)))
        self.assertEqual(run_suite._parse_shard(["--shard=2/5"]), ([], (2, 5)))

    def test_other_arguments_survive(self):
        rest, shard = run_suite._parse_shard(["-k", "foo", "--shard", "1/2"])
        self.assertEqual((rest, shard), (["-k", "foo"], (1, 2)))

    def test_no_shard_means_the_whole_suite(self):
        self.assertEqual(run_suite._parse_shard(["-v"]), (["-v"], None))

    def test_nonsense_is_refused_loudly(self):
        for bad in ("3/2", "0/4", "abc", "1"):
            with self.assertRaises(SystemExit, msg=bad):
                run_suite._parse_shard(["--shard", bad])

    def test_module_of_handles_a_real_id(self):
        self.assertEqual(
            run_suite._module_of("test_watchdog.SomeClass.test_thing"),
            "test_watchdog")


class ShardingRunsRealTests(unittest.TestCase):
    """End to end through the real runner, because the partition being right
    in memory says nothing about what unittest was actually asked to run."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "run_suite.py", *args],
            cwd=str(TESTS_DIR), capture_output=True, text=True, timeout=600)

    def test_a_shard_runs_a_strict_subset_and_says_which(self):
        """The name promises three things, so all three are checked: it
        succeeds, it announces the shard, and what it ran is a PROPER subset.
        Asserting only the banner would pass on a shard that ran zero tests
        or exited non-zero."""
        proc = self._run("--shard", "1/12", "-k", "test_module_of_handles")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out[-3000:])
        self.assertIn("shard 1/12:", out)
        mine, total = (int(x) for x in
                       out.split("shard 1/12:")[1].split()[0:3:2])
        self.assertGreater(mine, 0, "the shard was empty")
        self.assertLess(mine, total, "the shard was the whole suite")

    def test_an_empty_shard_says_so_rather_than_reporting_success(self):
        """`0 tests, OK` and `everything passed` print identically. A shard
        with nothing in it has to announce that, or a misconfigured matrix is
        indistinguishable from a green build."""
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0,'.'); import run_suite;"
             "sys.exit(run_suite.main(['--shard','200/200']))"],
            cwd=str(TESTS_DIR), capture_output=True, text=True, timeout=120)
        self.assertIn("no modules assigned", proc.stdout)
        self.assertEqual(proc.returncode, 0)


class ADiscoveryAnomalyStopsTheShard(unittest.TestCase):
    """The defect this file exists to prevent, arriving by the one route the
    totality tests above cannot see.

    A module that raises on import is never discovered as `test_foo.Case.test`.
    unittest substitutes `unittest.loader._FailedTest.test_foo`, whose derived
    module name is `unittest.loader` -- a name that loads an EMPTY suite. So
    the broken module lands in no bin, its import error is raised by nobody,
    and the build goes green precisely BECAUSE a test file is broken: sharding
    would have converted a red build into a fast green one.

    The totality tests cannot catch this, because they compare discovery
    against itself and the module is missing from both sides. Worse, the one
    check that would notice (`test_the_shards_collect_exactly_what_one_run
    _collects`) cannot guard the case where the unimportable module is THIS
    file -- it collapses too, and nothing is left to compare. Hence the
    refusal lives in `run_suite`, ahead of any test.
    """

    def _sharded(self, extra: str):
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("run_suite.py", "run.sh"):
                shutil.copy(TESTS_DIR / name, Path(tmp) / name)
            # An empty skip manifest: this scratch suite has none of the
            # real repository's tests, so the real manifest would be entirely
            # dangling here for reasons that have nothing to do with sharding.
            (Path(tmp) / "host-uid-skip-manifest.json").write_text(
                json.dumps({"reason_marker": run_suite.HOST_UID_REMAP_REASON_MARKER,
                            "tests": []}))
            (Path(tmp) / "test_fine.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self): pass\n")
            (Path(tmp) / "test_broken.py").write_text(extra)
            # `run_suite` imports helpers that live beside it; the real
            # tests directory is on the path for those, while discovery still
            # reads this scratch directory (it globs its own __file__ parent).
            env = dict(os.environ, PYTHONPATH=str(TESTS_DIR))
            return subprocess.run(
                [sys.executable, "run_suite.py", "--shard", "1/2"],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=120)

    def test_a_module_that_cannot_be_imported_fails_the_shard(self):
        proc = self._sharded("from no_such_module_xyz import thing\n")
        self.assertNotEqual(proc.returncode, 0,
                            "a broken test module produced a GREEN shard")
        self.assertIn("not importable test modules",
                      proc.stdout + proc.stderr)

    def test_the_refusal_names_what_it_could_not_place(self):
        """Refusing without saying why sends the reader to the wrong file."""
        proc = self._sharded("import unittest\nraise RuntimeError('boom')\n")
        self.assertIn("unittest.loader", proc.stdout + proc.stderr)

    def test_a_healthy_pair_still_shards(self):
        """The negative control: without a broken module the same fixture is
        green, so the test above fails for the reason it claims."""
        proc = self._sharded(
            "import unittest\n"
            "class U(unittest.TestCase):\n"
            "    def test_ok(self): pass\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_bare_shard_flag_is_refused_rather_than_running_everything(self):
        """`--shard` with no value used to mean "no sharding requested", so a
        typo in the workflow ran the full suite and exited 0. Failing open is
        the wrong direction for a flag whose job is to narrow what runs."""
        with self.assertRaises(SystemExit):
            run_suite._parse_shard(["--shard"])


class TheWorkflowCannotDriftFromTheDenominator(unittest.TestCase):
    """The partition is verified in this process; CI runs a DEPLOYED one.

    The two were joined only by a coincidence of two numbers written in
    different places -- `shard: [1, 2, 3, 4]` and a literal `/4` in the run
    command. Editing the matrix alone left shard 4's modules unrun with every
    check, including the aggregate, green. There is no test that could catch
    that reliably after the fact, so the denominator is no longer written
    down: it is `strategy.job-total`, which GitHub derives from the matrix
    itself and cannot disagree with it.
    """

    WORKFLOW = TESTS_DIR.parent / ".github" / "workflows" / "tests.yml"

    def setUp(self):
        self.text = self.WORKFLOW.read_text(encoding="utf-8")
        self.shard_lines = [ln.strip() for ln in self.text.splitlines()
                            if "--shard" in ln]
        self.assertTrue(self.shard_lines, "no shard invocation in the workflow")

    def test_the_denominator_is_derived_from_the_matrix(self):
        for line in self.shard_lines:
            self.assertIn("strategy.job-total", line,
                          f"shard count written by hand: {line}")

    def test_no_shard_count_is_written_as_a_literal(self):
        import re
        for line in self.shard_lines:
            self.assertIsNone(
                re.search(r"--shard[= ]\S*/\d", line),
                f"a literal denominator can drift from the matrix: {line}")


if __name__ == "__main__":
    unittest.main()
