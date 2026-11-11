"""Skip-manifest audit for host-UID-ancestry tests — ADR-0041, issue #245.

`tests.test_project_bind.ProjectTrustedAncestry` (ADR-0021) depends on real
host UID ownership of `/`, `/home`, and `/tmp`. Inside a sandbox that remaps
host UIDs into an unprivileged user namespace with no mapping for uid 0
(`bwrap --unshare-user`, no `--uid 0` passthrough), every root-owned path —
including `/`, which is root-owned on any normal Linux filesystem — reports
as the kernel overflow uid instead, and that class cannot pass.

An earlier revision of this fix made `tests/run.sh` abort the ENTIRE suite
the moment it detected that condition. Correct call about the failure being
confusing, wrong call about the blast radius: it hid every one of the
suite's ~900 unrelated tests' regression evidence behind one early exit.
ADR-0041 supersedes that with a narrower rule: only `ProjectTrustedAncestry`
skips, by exact test id against a checked-in manifest
(`host-uid-skip-manifest.json`), and `run_suite.py` audits that the actual
skip set matches the manifest exactly — any test skipping for this reason
that isn't on the manifest, or any manifest entry that doesn't skip, fails
the gate. Everything else in the suite still runs and must still pass. GHA
still runs `ProjectTrustedAncestry` unconditionally (real UID visibility,
no sandbox); `release`'s own merge procedure is what must additionally check
that GHA run before accepting a local skip as sufficient.

`bwrap --unshare-user`, used below, reproduces the exact overflow-uid remap
deterministically with no Codex involved — proof the cause is the
user-namespace mapping itself, not anything Codex-specific — and
`--ro-bind / /` is read-only by construction, matching the probe already
vetted in test_bwrap_probe.py: this test does not mutate the host. `/tmp` is
re-bound read-write on top, matching Codex's own `workspace-write` sandbox
profile (cwd/tmp/declared roots stay writable; everything else is
read-only) — without it, `tempfile` can't find a writable directory at all
and every test errors in `setUp`, which is not what release's sandbox
actually does.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO / "tests"
RUN_SH = TESTS_DIR / "run.sh"

sys.path.insert(0, str(TESTS_DIR))
try:
    import host_uid_ancestry
    import run_suite
finally:
    sys.path.remove(str(TESTS_DIR))

BWRAP = shutil.which("bwrap")

# Deliberately no test here asserting `not host_uid_ancestry.uid_remapped()`
# as an ambient fact: this suite must itself still pass when it runs INSIDE
# a real remapped sandbox (that's the exact scenario ADR-0041 is for), and a
# test asserting "this host is normal" would fail in exactly that case --
# the same anti-pattern this ADR exists to remove from ProjectTrustedAncestry.
# `uid_remapped()` not being vacuously true is proven instead by the
# end-to-end tests below: they show its answer actually differs between an
# unwrapped invocation (nothing skips) and one wrapped in real `bwrap`
# (exactly the manifest skips) -- and the bare-host `tests/run.sh` run
# (verified separately, zero skips) proves the False side directly.


class SkipManifestAuditUnitTests(unittest.TestCase):
    """audit() itself, hermetically -- no subprocess, no bwrap. This is the
    part of ADR-0041 that actually enforces the "exact" in "exact, checked-in
    approved skip manifest": a mismatch in either direction must fail."""

    class _FakeTest:
        def __init__(self, test_id):
            self._id = test_id

        def id(self):
            return self._id

    class _FakeResult:
        def __init__(self, skipped):
            self.skipped = skipped

    def _marked(self, test_id):
        return (self._FakeTest(test_id),
                f"host-uid-remap: ... ({host_uid_ancestry.HOST_UID_REMAP_REASON_MARKER}, ADR-0041)")

    def test_exact_match_under_a_remap_is_clean(self):
        a = f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}a"
        b = f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}b"
        manifest = {a, b}
        result = self._FakeResult([self._marked(a), self._marked(b)])
        self.assertEqual(run_suite.audit(result, manifest, remapped=True), [])

    def test_unexpected_skip_not_on_manifest_fails(self):
        manifest = {"a"}
        result = self._FakeResult([self._marked("a"), self._marked("c")])
        problems = run_suite.audit(result, manifest, remapped=True)
        self.assertTrue(
            any("unexpected" in p and "'c'" in p for p in problems), problems)

    def test_manifest_entry_that_does_not_skip_under_a_remap_fails(self):
        manifest = {"a", "b"}
        result = self._FakeResult([self._marked("a")])
        problems = run_suite.audit(result, manifest, remapped=True)
        self.assertTrue(
            any("did NOT skip" in p and "'b'" in p for p in problems), problems)

    def test_unrelated_skips_are_ignored(self):
        # A skip for a totally different reason (e.g. a missing optional
        # tool elsewhere in the suite) must not register as an unexpected
        # host-uid-remap skip -- this audit is scoped to ADR-0041 only.
        manifest = set()
        result = self._FakeResult(
            [(self._FakeTest("z"), "unrelated: some other tool is missing")])
        self.assertEqual(run_suite.audit(result, manifest, remapped=True), [])

    def test_manifest_entries_that_run_normally_with_no_remap_are_fine(self):
        # The bug this guards: on a bare host (no remap), every manifest
        # test runs for real and PASSES instead of skipping -- that must be
        # a clean, zero-problem result, not a wall of "did NOT skip" alarms.
        manifest = {
            f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}a",
            f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}b",
        }
        result = self._FakeResult([])
        self.assertEqual(run_suite.audit(result, manifest, remapped=False), [])

    def test_a_skip_with_no_remap_detected_is_itself_flagged(self):
        # If nothing forced a remap but a test skipped with this reason
        # anyway, something is wrong (e.g. the decorator's condition drifted
        # from uid_remapped()) -- that must fail loud, not pass silently.
        manifest = {"a"}
        result = self._FakeResult([self._marked("a")])
        problems = run_suite.audit(result, manifest, remapped=False)
        self.assertTrue(
            any("unexpected" in p and "'a'" in p for p in problems), problems)

    def test_dangling_manifest_entry_is_flagged(self):
        # Reviewed finding on PR #249: a manifest id matching NO real test
        # can never appear in `actual` or `collected_ids` either (both are
        # necessarily built from real tests), so a check that only compares
        # manifest against those two silently drops it -- exit 0, no
        # diagnostic. `all_ids` is what proves a manifest id is real at all,
        # independent of whether it was selected or skipped this run.
        manifest = {
            f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}test_real",
            f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}test_fabricated",
        }
        real = f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}test_real"
        result = self._FakeResult([self._marked(real)])
        problems = run_suite.audit(
            result, manifest, remapped=True,
            collected_ids={real}, all_ids={real})
        self.assertTrue(
            any("do not correspond to any real" in p and "test_fabricated" in p
                for p in problems),
            problems)

    def test_dangling_check_does_not_fire_when_the_id_is_real(self):
        # Control: a manifest id that IS in all_ids must not be flagged as
        # dangling just because it happens to also be absent from
        # collected_ids (e.g. a -k-filtered run that excluded it).
        manifest = {"a"}
        result = self._FakeResult([])
        problems = run_suite.audit(
            result, manifest, remapped=True, collected_ids=set(), all_ids={"a"})
        self.assertFalse(
            [p for p in problems if "do not correspond to any real" in p], problems)

    def test_off_scope_manifest_entry_is_flagged(self):
        # Only ProjectTrustedAncestry may ever appear on this manifest --
        # defense against a manifest edit that names the wrong class.
        manifest = {"test_other_module.SomeOtherClass.test_x"}
        result = self._FakeResult([])
        problems = run_suite.audit(result, manifest, remapped=True, all_ids=manifest)
        self.assertTrue(
            any("outside" in p and "test_other_module" in p for p in problems),
            problems)

    def test_off_scope_skip_is_flagged_even_if_it_matches_the_manifest(self):
        # Defense in depth: even if an off-scope id somehow ALSO ended up on
        # the manifest (so unexpected/missing alone wouldn't catch it), the
        # class-prefix check must still flag it independently.
        off_scope = "test_other_module.SomeOtherClass.test_x"
        manifest = {off_scope}
        result = self._FakeResult([self._marked(off_scope)])
        problems = run_suite.audit(
            result, manifest, remapped=True,
            collected_ids={off_scope}, all_ids={off_scope})
        self.assertTrue(
            any("skipped with the host-uid-remap reason" in p and off_scope in p
                for p in problems),
            problems)


class ManifestReasonMarkerWiring(unittest.TestCase):
    """host-uid-skip-manifest.json's `reason_marker` field must actually be
    checked against the code constant, not just carried as decoration that
    can silently drift from HOST_UID_REMAP_REASON_MARKER."""

    def test_matching_marker_loads_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            path.write_text(json.dumps({
                "reason_marker": host_uid_ancestry.HOST_UID_REMAP_REASON_MARKER,
                "tests": ["a"],
            }))
            with mock.patch.object(run_suite, "MANIFEST_PATH", path):
                self.assertEqual(run_suite._load_manifest_ids(), {"a"})

    def test_mismatched_marker_refuses_to_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            path.write_text(json.dumps({
                "reason_marker": "issue #999-drifted",
                "tests": ["a"],
            }))
            with mock.patch.object(run_suite, "MANIFEST_PATH", path):
                with self.assertRaises(SystemExit):
                    run_suite._load_manifest_ids()


@unittest.skipUnless(BWRAP, "bwrap not installed on this host")
class SkipManifestEndToEnd(unittest.TestCase):
    """Real bwrap remap, real run_suite.py. Scoped with -k for speed --
    the full suite's own CI/local run is what covers everything else."""

    def _run(self, k_pattern):
        return subprocess.run(
            [BWRAP, "--unshare-user", "--ro-bind", "/", "/",
             "--bind", "/tmp", "/tmp", "--dev", "/dev", "--proc", "/proc",
             "bash", str(RUN_SH), "-k", k_pattern],
            capture_output=True, text=True, timeout=120,
        )

    def test_host_ancestry_tests_skip_and_the_gate_still_passes(self):
        proc = self._run("ProjectTrustedAncestry")
        blob = proc.stdout + proc.stderr
        self.assertEqual(
            proc.returncode, 0,
            f"the manifest is exact, so a clean remap must not fail the gate:\n{blob}")
        self.assertIn("skipped=12", blob.replace(" ", ""), blob)
        self.assertNotIn("FATAL", blob, blob)

    def test_an_unrelated_class_still_runs_and_passes_under_the_same_remap(self):
        # Control: a class with no host-uid-ancestry dependency must still
        # execute for real (not silently skipped) and still pass -- proof
        # the remap's blast radius stops at ProjectTrustedAncestry.
        proc = self._run("ProjectConcurrency")
        blob = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, blob)
        self.assertIn("OK", blob, blob)
        self.assertNotIn("skipped", blob.lower(), blob)

    def test_a_dangling_manifest_entry_fails_the_gate_end_to_end(self):
        # The reviewed finding on PR #249, reproduced against the real
        # manifest file and a real bwrap remap, not just audit()'s unit
        # tests: append one nonexistent test id to the checked-in manifest
        # and confirm the gate now refuses instead of reporting a clean
        # `OK (skipped=12)` / exit 0.
        manifest_path = TESTS_DIR / "host-uid-skip-manifest.json"
        original = manifest_path.read_text()
        try:
            data = json.loads(original)
            data["tests"].append(
                f"{host_uid_ancestry.HOST_UID_REMAP_TEST_PREFIX}"
                "test_this_does_not_exist_fabricated")
            manifest_path.write_text(json.dumps(data))

            proc = self._run("ProjectTrustedAncestry")
        finally:
            manifest_path.write_text(original)

        blob = proc.stdout + proc.stderr
        self.assertNotEqual(
            proc.returncode, 0,
            f"a dangling manifest entry must fail the gate, not pass it:\n{blob}")
        self.assertIn("do not correspond to any real", blob, blob)
        self.assertIn("test_this_does_not_exist_fabricated", blob, blob)


if __name__ == "__main__":
    unittest.main()
