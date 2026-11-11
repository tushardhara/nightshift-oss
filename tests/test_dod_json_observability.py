"""Issue #423-A / ADR-0063 -- `tests/dod_validate.py --json` (AC-1, AC-2, AC-7).

Every REGISTRY entry here is a fixture Check whose `run` never shells out to
git/gh -- `dod_validate.py`'s real checks make their own subprocess calls and
are a confirmed possible-timeout dependency in this sandbox's network
conditions (ADR-0063's own Context section), so these tests exercise the
JSON-assembly and plain-report logic in isolation, the same discipline
`run_self_test()` already uses (a scratch worktree + monkeypatched evidence)
one level further up.

AC-2's "byte comparison test against a captured pre-change baseline" is done
literally: the pre-#423-A source is loaded straight from git history at this
issue's own base SHA (f82559a7, checkpoint id 35) and run side-by-side with
the current file, both against the identical fixture REGISTRY, and their
plain-text (--json absent) stdout must be byte-identical.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOD_VALIDATE = REPO / "tests" / "dod_validate.py"
BASE_SHA = "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b"


def _load_module(name, path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so @dataclass (which looks itself up via
    # sys.modules[cls.__module__]) can resolve this module during import.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_base_module(tmp_path_factory_dir):
    """The pre-#423-A dod_validate.py, straight from git history."""
    proc = subprocess.run(
        ["git", "show", f"{BASE_SHA}:tests/dod_validate.py"],
        cwd=str(REPO), capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise unittest.SkipTest(
            f"cannot read base_sha dod_validate.py from git history: {proc.stderr}")
    base_path = tmp_path_factory_dir / "dod_validate_base.py"
    base_path.write_text(proc.stdout, encoding="utf-8")
    return _load_module("dod_validate_base", base_path)


def _install_fixture_registry(module):
    """A small, deterministic REGISTRY covering every verdict shape --
    including a FAIL with a derivable artifact, a FAIL with none, and both
    MANUAL_CONDITIONS members -- none of it touching git/gh/the filesystem.
    """
    def mk(id_, cond, status, reason=""):
        return module.Check(
            id_, cond,
            lambda ctx, s=status, r=reason: module.Verdict(s, r),
            lambda root, m: m)

    module.REGISTRY = [
        mk("D01", 1, module.Status.PASS),
        mk("D08", 8, module.Status.MANUAL, "manual pointer for D08"),
        mk("D09", 9, module.Status.BLOCKED, "git unavailable"),
        mk("D13", 13, module.Status.FAIL, "docs/audit/CONCURRENCY.md missing"),
        mk("D21", 21, module.Status.FAIL, "no single derivable artifact"),
        mk("D24", 24, module.Status.PASS),
    ]
    if hasattr(module, "BLOCKING_ARTIFACT_BY_ID"):
        module.BLOCKING_ARTIFACT_BY_ID = {"D13": "docs/audit/CONCURRENCY.md"}
    module.MANUAL_CONDITIONS = (8,)


def _run_main(module, argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main(argv)
    return rc, buf.getvalue()


class JsonSchemaTest(unittest.TestCase):
    """AC-1: --json emits {id, verdict, reason, blocking_artifact} per
    condition plus {schema_version, manifest_readable, dod_complete,
    unallocatable}."""

    def setUp(self):
        self.module = _load_module("dod_validate_current", DOD_VALIDATE)
        _install_fixture_registry(self.module)

    def test_json_payload_shape_and_values(self):
        rc, stdout = _run_main(self.module, ["--json"])
        self.assertEqual(rc, 1)  # not dod_complete: D13/D21 FAIL
        payload = json.loads(stdout)

        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["manifest_readable"])
        self.assertFalse(payload["dod_complete"])

        by_id = {c["id"]: c for c in payload["conditions"]}
        self.assertEqual(
            set(by_id), {"D01", "D08", "D09", "D13", "D21", "D24"})

        # PASS: no artifact.
        self.assertEqual(by_id["D01"]["verdict"], "PASS")
        self.assertIsNone(by_id["D01"]["blocking_artifact"])

        # FAIL with a derivable artifact.
        self.assertEqual(by_id["D13"]["verdict"], "FAIL")
        self.assertEqual(
            by_id["D13"]["blocking_artifact"], "docs/audit/CONCURRENCY.md")
        self.assertIn("missing", by_id["D13"]["reason"])

        # FAIL with NO derivable artifact -> null (AC-1's own clause).
        self.assertEqual(by_id["D21"]["verdict"], "FAIL")
        self.assertIsNone(by_id["D21"]["blocking_artifact"])

        # BLOCKED / MANUAL never fold into FAIL (ADR-0025, reasserted by
        # ADR-0063) and never carry a blocking_artifact.
        self.assertEqual(by_id["D08"]["verdict"], "MANUAL")
        self.assertIsNone(by_id["D08"]["blocking_artifact"])
        self.assertEqual(by_id["D09"]["verdict"], "BLOCKED")
        self.assertIsNone(by_id["D09"]["blocking_artifact"])

    def test_unallocatable_is_fail_without_artifact_plus_blocked_manual(self):
        _, stdout = _run_main(self.module, ["--json"])
        payload = json.loads(stdout)
        self.assertEqual(set(payload["unallocatable"]), {"D21", "D08", "D09"})
        # D13 has a derivable artifact -- NOT unallocatable.
        self.assertNotIn("D13", payload["unallocatable"])

    def test_dod_complete_true_when_every_condition_passes(self):
        def mk(id_, cond):
            return self.module.Check(
                id_, cond,
                lambda ctx: self.module.Verdict(self.module.Status.PASS),
                lambda root, m: m)
        self.module.REGISTRY = [mk("D01", 1), mk("D02", 2)]
        _, stdout = _run_main(self.module, ["--json"])
        payload = json.loads(stdout)
        self.assertTrue(payload["dod_complete"])
        self.assertEqual(payload["unallocatable"], [])


class ManifestUnreadableTest(unittest.TestCase):
    """AC-7: an absent/unparseable manifest is reported explicitly, not
    silently folded into a generic FAIL count."""

    def setUp(self):
        self.module = _load_module("dod_validate_current_ac7", DOD_VALIDATE)
        _install_fixture_registry(self.module)

    def test_manifest_readable_false_when_load_manifest_fails(self):
        self.module.load_manifest = (
            lambda repo_root: (None, "docs/prd/DOD-MANIFEST.json not found"))
        _, stdout = _run_main(self.module, ["--json"])
        payload = json.loads(stdout)
        self.assertFalse(payload["manifest_readable"])

    def test_manifest_readable_true_when_load_manifest_succeeds(self):
        self.module.load_manifest = lambda repo_root: ({"ok": True}, None)
        _, stdout = _run_main(self.module, ["--json"])
        payload = json.loads(stdout)
        self.assertTrue(payload["manifest_readable"])


class PlainReportByteIdenticalTest(unittest.TestCase):
    """AC-2: --json absent leaves stdout byte-identical to the pre-#423-A
    report, verified against the real pre-change source from git history."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="dod-ac2-baseline-")
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.current = _load_module("dod_validate_ac2_current", DOD_VALIDATE)
        _install_fixture_registry(self.current)

        self.base = _load_base_module(self.tmp_path)
        _install_fixture_registry(self.base)

    def test_no_json_flag_output_matches_pre_change_baseline_exactly(self):
        rc_current, out_current = _run_main(self.current, [])
        rc_base, out_base = _run_main(self.base, [])
        self.assertEqual(rc_current, rc_base)
        self.assertEqual(
            out_current, out_base,
            "stdout must be byte-identical to the pre-#423-A report when "
            "--json is absent (AC-2)")

    def test_self_test_flag_still_parses_and_is_unaffected(self):
        # --self-test's own machinery (run_self_test) does real git worktree
        # I/O against the repo; stub it so this stays a fast argparse-wiring
        # check, not a repeat of run_self_test's own coverage. The point is
        # that adding --json did not disturb --self-test's own routing:
        # `main` must still call run_self_test() and return based on it,
        # rather than raising an argparse usage error (exit code 2).
        calls = []
        self.current.run_self_test = lambda: (calls.append(1) or True)
        rc = self.current.main(["--self-test"])
        self.assertEqual(calls, [1])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
