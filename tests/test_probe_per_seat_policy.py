"""
Unit tests for the #118 per-seat-policy probe harness.

The probe's own reporting is load-bearing evidence, so its classification and
verdict logic is tested here rather than trusted. Nothing in this module
starts a server, binds a port, or touches any state — the live probe is
`tests/probe_per_seat_policy.py`, run by hand.

The property that matters: a verdict is only ever reported when BOTH controls
behaved. A positive control that fails to DENY means the harness could not
have observed a DENY at all, so an ALLOW in the experiment proves nothing.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_PROBE = Path(__file__).resolve().parent / "probe_per_seat_policy.py"
_spec = importlib.util.spec_from_file_location("probe118", _PROBE)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe118"] = probe
_spec.loader.exec_module(probe)


def _evaluate(result, reason=None):
    body = {"result": result}
    if reason:
        body["reason"] = reason
    return {"http_status": 200, "response": body}


def _mcp_error(message):
    return {
        "http_status": 200,
        "response": {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": message}},
    }


class MissingFile:
    """A path-like that is never present, so `classify` cannot read a write."""

    @staticmethod
    def exists():
        return False


class PresentFile:
    @staticmethod
    def exists():
        return True


class TestClassify(unittest.TestCase):
    """One session's outcome, from the two enforcement paths it exercised."""

    def test_deny_on_the_evaluate_path_is_denied(self):
        out = probe.classify(
            _evaluate("POLICY_ACTION_DENY", "read_only_os fired"),
            _mcp_error("Denied by policy: read_only_os fired"),
            MissingFile,
        )
        self.assertEqual(out, "DENIED")

    def test_deny_seen_only_on_the_mcp_path_is_still_denied(self):
        """Either enforcement path denying is a denial; they are not required to agree."""
        out = probe.classify(
            _evaluate("POLICY_ACTION_ALLOW"),
            _mcp_error("Denied by policy: read_only_os fired"),
            MissingFile,
        )
        self.assertEqual(out, "DENIED")

    def test_no_runner_after_an_allow_is_allowed_not_denied(self):
        """
        The load-bearing case. With no runner attached, an ALLOWED write ends in
        a transport error. That is execution failing, not a control firing, and
        must never be scored as a denial — scoring it DENIED would have turned
        a CONFIRMED probe into a REFUTED one.
        """
        out = probe.classify(
            _evaluate("POLICY_ACTION_ALLOW"),
            _mcp_error("No runner bound for session 'abc'"),
            MissingFile,
        )
        self.assertEqual(out, "ALLOWED")

    def test_unspecified_is_allowed(self):
        """UNSPECIFIED is pass-through: no policy objected."""
        out = probe.classify(
            _evaluate("POLICY_ACTION_UNSPECIFIED"),
            _mcp_error("No runner bound for session 'abc'"),
            MissingFile,
        )
        self.assertEqual(out, "ALLOWED")

    def test_a_file_that_actually_appeared_is_allowed(self):
        out = probe.classify(
            _evaluate("POLICY_ACTION_ALLOW"),
            {"http_status": 200, "response": {"jsonrpc": "2.0", "id": 1, "result": {}}},
            PresentFile,
        )
        self.assertEqual(out, "ALLOWED")

    def test_an_unrecognised_verdict_with_no_write_is_inconclusive(self):
        """A shape we do not understand must not be read as either answer."""
        out = probe.classify(
            _evaluate("POLICY_ACTION_ASK"),
            _mcp_error("Tool call denied by user"),
            MissingFile,
        )
        self.assertEqual(out, "INCONCLUSIVE")

    def test_a_rejected_evaluate_call_still_reads_the_mcp_path(self):
        """
        A 400 from the evaluate gate is the harness being wrong about the wire
        shape, not a verdict. The MCP path is independent and still readable.
        """
        out = probe.classify(
            {"http_status": 400, "response": {"error": {"code": "invalid_input"}}},
            _mcp_error("No runner bound for session 'abc'"),
            MissingFile,
        )
        self.assertEqual(out, "ALLOWED")

    def test_both_paths_unreadable_is_inconclusive_not_allowed(self):
        """A broken harness must report INCONCLUSIVE, never a silent ALLOW."""
        out = probe.classify(
            {"http_status": 400, "response": {"error": {"code": "invalid_input"}}},
            {"http_status": 500, "response": "gateway exploded"},
            MissingFile,
        )
        self.assertEqual(out, "INCONCLUSIVE")


class TestBundleLayout(unittest.TestCase):
    """The bundles must differ ONLY in which policies they declare and where."""

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="probe118-unit-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_seat_policy_bundle_declares_nothing_at_root(self):
        root = probe.write_bundle(
            self.tmp, "seatonly", "d", root_policy=False, seat="ro_seat", seat_policy=True
        )
        root_yaml = (root / "config.yaml").read_text()
        seat_yaml = (root / "agents" / "ro_seat" / "config.yaml").read_text()
        self.assertNotIn("guardrails", root_yaml)
        self.assertIn("read_only_os", seat_yaml)

    def test_root_policy_bundle_declares_nothing_on_the_seat(self):
        root = probe.write_bundle(
            self.tmp, "rootonly", "d", root_policy=True, seat="plain", seat_policy=False
        )
        self.assertIn("read_only_os", (root / "config.yaml").read_text())
        self.assertNotIn(
            "guardrails", (root / "agents" / "plain" / "config.yaml").read_text()
        )

    def test_both_bundles_use_the_identical_policy_block(self):
        """
        The experiment and its positive control must differ in placement only.
        A different deny_reason, handler or argument set between them would
        make the comparison meaningless.
        """
        a = probe.write_bundle(
            self.tmp, "a", "d", root_policy=True, seat="s", seat_policy=False
        )
        b = probe.write_bundle(
            self.tmp, "b", "d", root_policy=False, seat="s", seat_policy=True
        )
        root_block = (a / "config.yaml").read_text().split("guardrails:", 1)[1]
        seat_block = (b / "agents" / "s" / "config.yaml").read_text().split("guardrails:", 1)[1]
        self.assertEqual(root_block, seat_block)

    def test_every_bundle_declares_a_spec_version(self):
        """Without it `omnigent server --agent` refuses the bundle at startup."""
        root = probe.write_bundle(
            self.tmp, "v", "d", root_policy=False, seat="s", seat_policy=False
        )
        self.assertTrue((root / "config.yaml").read_text().startswith("spec_version:"))
        self.assertTrue(
            (root / "agents" / "s" / "config.yaml").read_text().startswith("spec_version:")
        )


class TestSafety(unittest.TestCase):
    """The probe must never be able to signal a process it did not create."""

    def test_stop_server_refuses_a_pre_existing_pid(self):
        class FakeProc:
            pid = 4242

            @staticmethod
            def poll():
                return None

        saved = probe._PREEXISTING_PIDS
        probe._PREEXISTING_PIDS = {4242}
        try:
            with self.assertRaises(AssertionError):
                probe.stop_server(FakeProc)
        finally:
            probe._PREEXISTING_PIDS = saved

    def test_stop_server_is_a_noop_on_an_already_dead_process(self):
        class DeadProc:
            pid = 4242

            @staticmethod
            def poll():
                return 0

        saved = probe._PREEXISTING_PIDS
        probe._PREEXISTING_PIDS = {4242}
        try:
            probe.stop_server(DeadProc)  # returns before the pid assertion
        finally:
            probe._PREEXISTING_PIDS = saved


if __name__ == "__main__":
    unittest.main()
