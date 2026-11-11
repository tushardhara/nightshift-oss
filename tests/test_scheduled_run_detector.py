"""Issue #132: an LLM-independent scheduled-run failure detector.

The tests drive both production boundaries as subprocesses: the real
``nightshift-ctl`` for durable/idempotent state, and the detector executable for
loopback HTTP observation plus direct ``gh`` delivery.  No LLM is present in the
harness, so a delivered alert is also proof that the failure path needs no model
turn or live Program driver.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ctlutil import Ctl, VIRTUAL_T0


REPO = Path(__file__).resolve().parents[1]
DETECTOR = REPO / "deploy" / "nightshift-scheduled-run-detector.py"


class _ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == f"/v1/scheduled-tasks/{self.server.task_id}":
            self._reply(self.server.task)
            return
        if path == f"/v1/scheduled-tasks/{self.server.task_id}/runs":
            self._reply({
                "runs": self.server.runs,
                "next_cursor": self.server.next_cursor,
            })
            return
        prefix = "/v1/sessions/"
        if path.startswith(prefix) and path.endswith("/items"):
            conversation_id = path[len(prefix) : -len("/items")]
            self._reply({"data": self.server.items.get(conversation_id, [])})
            return
        self.send_error(404)

    def log_message(self, *_args):
        pass

    def _reply(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DetectorHarness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="scheduled-run-detector-")
        self.root = Path(self.tmp.name)
        self.ctl = Ctl(clock=VIRTUAL_T0)
        self.task_id = "f" * 32
        self.task = {
            "id": self.task_id,
            "state": "active",
            "rrule": "FREQ=HOURLY",
            "next_run_at": "1970-01-12T13:46:40+00:00",  # epoch 1,000,000
        }
        self.runs = []
        self.items = {}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        self.httpd.task_id = self.task_id
        self.httpd.task = self.task
        self.httpd.runs = self.runs
        self.httpd.items = self.items
        self.httpd.next_cursor = None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.alert_log = self.root / "alerts.jsonl"
        self.fail_flag = self.root / "fail-gh"
        self.gh = self.root / "gh"
        self.gh.write_text(
            """#!/usr/bin/env python3
import json, os, pathlib, sys
if pathlib.Path(os.environ["GH_FAIL_FLAG"]).exists():
    raise SystemExit(17)
args = sys.argv[1:]
body_path = args[args.index("--body-file") + 1]
with open(body_path) as f:
    body = f.read()
with open(os.environ["GH_ALERT_LOG"], "a") as f:
    f.write(json.dumps({"argv": args, "body": body}) + "\\n")
"""
        )
        self.gh.chmod(self.gh.stat().st_mode | stat.S_IXUSR)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    @property
    def server(self):
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def run(self, *, now, missed_grace=600, stuck_deadline=4500,
            server=None, extra_env=None):
        env = os.environ.copy()
        env.update(
            GH_ALERT_LOG=str(self.alert_log),
            GH_FAIL_FLAG=str(self.fail_flag),
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [
                str(DETECTOR),
                "--server",
                server or self.server,
                "--task-id",
                self.task_id,
                "--controller",
                str(REPO / "bin" / "nightshift-ctl"),
                "--control-db",
                self.ctl.db,
                "--repo",
                "owner/nightshift",
                "--issue",
                "3",
                "--gh",
                str(self.gh),
                "--missed-grace",
                str(missed_grace),
                "--stuck-deadline",
                str(stuck_deadline),
                "--now",
                str(now),
            ],
            env=env,
            text=True,
            capture_output=True,
        )

    def alerts(self):
        if not self.alert_log.exists():
            return []
        return [json.loads(line) for line in self.alert_log.read_text().splitlines()]

    @staticmethod
    def run_record(run_id, scheduled_at, status, *, conversation_id=None, fired_at=None,
                   finished_at=None, error_code=None):
        return {
            "id": run_id,
            "scheduled_task_id": "f" * 32,
            "status": status,
            "scheduled_at": scheduled_at,
            "conversation_id": conversation_id,
            "fired_at": fired_at,
            "finished_at": finished_at,
            "error_code": error_code,
        }

    def assistant_item(self, conversation_id, text):
        self.items[conversation_id] = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ]


class ScheduleControlPlane(unittest.TestCase):
    def test_observation_is_idempotent_but_distinct_failures_escalate(self):
        c = Ctl(clock=VIRTUAL_T0)
        code, first = c.run(
            "schedule", "observe", "--task", "heartbeat", "--key", "heartbeat:run:a",
            "--run", "a", "--scheduled-at", "100", "--status", "failed",
            "--class", "vendor_overloaded",
        )
        self.assertEqual(code, 0)
        self.assertTrue(first["alert_required"])
        self.assertEqual(first["observation"]["consecutive_failures"], 1)

        _, repeat = c.run(
            "schedule", "observe", "--task", "heartbeat", "--key", "heartbeat:run:a",
            "--run", "a", "--scheduled-at", "100", "--status", "failed",
            "--class", "vendor_overloaded",
        )
        self.assertEqual(repeat["observation"]["consecutive_failures"], 1)

        c.run("schedule", "alerted", "--key", "heartbeat:run:a")
        _, delivered = c.run(
            "schedule", "observe", "--task", "heartbeat", "--key", "heartbeat:run:a",
            "--run", "a", "--scheduled-at", "100", "--status", "failed",
            "--class", "vendor_overloaded",
        )
        self.assertFalse(delivered["alert_required"])

        _, second = c.run(
            "schedule", "observe", "--task", "heartbeat", "--key", "heartbeat:run:b",
            "--run", "b", "--scheduled-at", "200", "--status", "failed",
            "--class", "timeout",
        )
        self.assertEqual(second["observation"]["consecutive_failures"], 2)
        self.assertTrue(second["alert_required"])

    def test_same_run_status_supersedes_without_double_counting(self):
        c = Ctl(clock=VIRTUAL_T0)
        args = (
            "schedule", "observe", "--task", "heartbeat", "--key", "heartbeat:run:a",
            "--run", "a", "--scheduled-at", "100",
        )
        _, stuck = c.run(*args, "--status", "stuck", "--class", "incomplete")
        c.run("schedule", "alerted", "--key", "heartbeat:run:a")
        _, failed = c.run(
            *args, "--status", "failed", "--class", "vendor_overloaded"
        )
        self.assertEqual(stuck["observation"]["consecutive_failures"], 1)
        self.assertEqual(failed["observation"]["consecutive_failures"], 1)
        self.assertTrue(failed["alert_required"], "supersession must deliver an update")

    def test_success_resets_consecutive_count(self):
        c = Ctl(clock=VIRTUAL_T0)
        common = ("schedule", "observe", "--task", "heartbeat")
        c.run(*common, "--key", "run:a", "--run", "a", "--scheduled-at", "100",
              "--status", "failed", "--class", "unknown")
        c.run(*common, "--key", "run:b", "--run", "b", "--scheduled-at", "200",
              "--status", "succeeded", "--class", "none")
        _, third = c.run(*common, "--key", "run:c", "--run", "c",
                         "--scheduled-at", "300", "--status", "failed",
                         "--class", "timeout")
        self.assertEqual(third["observation"]["consecutive_failures"], 1)

    def test_late_run_after_missed_is_one_occurrence_for_escalation(self):
        c = Ctl(clock=VIRTUAL_T0)
        common = ("schedule", "observe", "--task", "heartbeat")
        _, missed = c.run(
            *common, "--key", "heartbeat:missed:100", "--scheduled-at", "100",
            "--status", "missed", "--class", "incomplete",
        )
        _, late = c.run(
            *common, "--key", "heartbeat:run:a", "--run", "a",
            "--scheduled-at", "100", "--status", "failed",
            "--class", "vendor_overloaded",
        )
        self.assertEqual(missed["observation"]["consecutive_failures"], 1)
        self.assertEqual(late["observation"]["consecutive_failures"], 1)
        self.assertNotEqual(
            missed["observation"]["observation_key"],
            late["observation"]["observation_key"],
        )

    def test_same_second_repoll_cannot_regress_task_escalation(self):
        c = Ctl(clock=VIRTUAL_T0)
        common = ("schedule", "observe", "--task", "heartbeat")
        c.run(*common, "--key", "heartbeat:run:z", "--run", "z",
              "--scheduled-at", "100", "--status", "failed", "--class", "timeout")
        _, second = c.run(
            *common, "--key", "heartbeat:run:a", "--run", "a",
            "--scheduled-at", "100", "--status", "failed", "--class", "timeout",
        )
        c.run(*common, "--key", "heartbeat:run:z", "--run", "z",
              "--scheduled-at", "100", "--status", "failed", "--class", "timeout")
        _, state = c.run("schedule", "state", "--task", "heartbeat")
        self.assertEqual(second["observation"]["consecutive_failures"], 2)
        self.assertEqual(state["state"]["consecutive_failures"], 2)


class ScheduledRunDetector(unittest.TestCase):
    def setUp(self):
        self.h = DetectorHarness()

    def tearDown(self):
        self.h.close()

    def test_failed_529_alerts_without_an_llm_and_does_not_leak_raw_text(self):
        run = self.h.run_record(
            "a" * 32, 999_000, "failed", conversation_id="c" * 32,
            fired_at=999_001, finished_at=999_010, error_code="runner_error",
        )
        self.h.runs.append(run)
        raw = "API Error: 529 Overloaded. private transcript detail"
        self.h.assistant_item("c" * 32, raw)
        result = self.h.run(now=999_100)
        self.assertEqual(result.returncode, 0, result.stderr)
        alerts = self.h.alerts()
        self.assertEqual(len(alerts), 1)
        body = alerts[0]["body"]
        self.assertIn("vendor_overloaded", body)
        self.assertIn("a" * 32, body)
        self.assertIn("c" * 32, body)
        self.assertIn("consecutive failures: 1", body.lower())
        self.assertNotIn("private transcript detail", body)
        self.assertNotIn("API Error", body)
        self.assertEqual(alerts[0]["argv"][:2], ["issue", "comment"])

    def test_repeat_poll_is_quiet_but_second_failed_run_escalates(self):
        first = self.h.run_record(
            "a" * 32, 998_000, "failed", conversation_id="c" * 32,
            error_code="runner_error",
        )
        self.h.runs.append(first)
        self.h.assistant_item("c" * 32, "API Error: 529 Overloaded")
        self.assertEqual(self.h.run(now=998_100).returncode, 0)
        self.assertEqual(self.h.run(now=998_200).returncode, 0)
        self.assertEqual(len(self.h.alerts()), 1)

        second = self.h.run_record(
            "b" * 32, 999_000, "failed", conversation_id="d" * 32,
            error_code="runner_error",
        )
        self.h.runs.insert(0, second)
        self.h.assistant_item("d" * 32, "request timed out")
        self.assertEqual(self.h.run(now=999_100).returncode, 0)
        alerts = self.h.alerts()
        self.assertEqual(len(alerts), 2)
        self.assertIn("consecutive failures: 2", alerts[-1]["body"].lower())
        self.assertIn("OUTAGE", alerts[-1]["body"])

    def test_known_expected_occurrence_becomes_missed_after_grace(self):
        self.h.runs.append(self.h.run_record("a" * 32, 995_000, "succeeded"))
        self.h.task["next_run_at"] = "1970-01-12T12:46:40+00:00"  # 996,400
        self.assertEqual(self.h.run(now=996_000).returncode, 0)
        self.assertEqual(self.h.alerts(), [])

        self.h.task["next_run_at"] = "1970-01-12T13:46:40+00:00"  # 1,000,000
        self.assertEqual(self.h.run(now=997_001, missed_grace=600).returncode, 0)
        alerts = self.h.alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("missed", alerts[0]["body"])

    def test_running_past_deadline_alerts_stuck(self):
        self.h.runs.append(
            self.h.run_record("a" * 32, 990_000, "running", fired_at=990_100)
        )
        result = self.h.run(now=995_000, stuck_deadline=4500)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stuck", self.h.alerts()[0]["body"])

    def test_stuck_then_failed_updates_same_incident_without_incrementing(self):
        run = self.h.run_record(
            "a" * 32, 990_000, "running", conversation_id="c" * 32,
            fired_at=990_100,
        )
        self.h.runs.append(run)
        self.assertEqual(self.h.run(now=995_000).returncode, 0)
        run.update(status="failed", finished_at=995_100, error_code="runner_error")
        self.h.assistant_item("c" * 32, "API Error: 529 Overloaded")
        self.assertEqual(self.h.run(now=995_200).returncode, 0)
        alerts = self.h.alerts()
        self.assertEqual(len(alerts), 2)
        self.assertIn("stuck", alerts[0]["body"])
        self.assertIn("vendor_overloaded", alerts[1]["body"])
        self.assertIn("consecutive failures: 1", alerts[1]["body"].lower())

    def test_failed_delivery_is_not_acknowledged_and_retries_next_poll(self):
        self.h.runs.append(
            self.h.run_record("a" * 32, 999_000, "failed", error_code="timeout")
        )
        self.h.fail_flag.touch()
        failed = self.h.run(now=999_100)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.h.alerts(), [])

        # A second incident is still observed while the first delivery is
        # blocked. Both rows remain pending instead of detection freezing.
        self.h.runs.insert(
            0, self.h.run_record("b" * 32, 999_050, "failed", error_code="timeout")
        )
        still_failed = self.h.run(now=999_150)
        self.assertNotEqual(still_failed.returncode, 0)
        _, state = self.h.ctl.run("schedule", "state", "--task", self.h.task_id)
        self.assertEqual(len(state["pending_alerts"]), 2)

        self.h.fail_flag.unlink()
        retried = self.h.run(now=999_200)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(len(self.h.alerts()), 2)

    def test_non_loopback_server_is_rejected_before_any_request(self):
        result = subprocess.run(
            [str(DETECTOR), "--server", "https://example.com", "--task-id", self.h.task_id,
             "--controller", str(REPO / "bin" / "nightshift-ctl"),
             "--control-db", self.h.ctl.db, "--repo", "owner/nightshift", "--issue", "3"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("loopback", result.stderr.lower())

    def test_proxy_environment_cannot_redirect_loopback_poll(self):
        dead_loopback = self.h.server.replace("127.0.0.1", "127.0.0.2")
        result = self.h.run(
            now=999_100,
            server=dead_loopback,
            extra_env={
                "http_proxy": self.h.server,
                "HTTP_PROXY": self.h.server,
                "all_proxy": self.h.server,
                "ALL_PROXY": self.h.server,
                "no_proxy": "",
                "NO_PROXY": "",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.h.alerts(), [])
        self.assertIn("API read failed", result.stderr)

    def test_non_finite_timestamp_is_rejected_without_traceback(self):
        self.h.runs.append(self.h.run_record("a" * 32, float("inf"), "failed"))
        result = self.h.run(now=999_100)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("invalid JSON", result.stderr)

    def test_run_and_delivery_work_are_bounded_per_poll(self):
        # Remember an expected occurrence which the oversized response omits.
        # A capped page is not complete evidence that the occurrence was missed.
        self.h.task["next_run_at"] = 997_900
        self.h.runs.append(self.h.run_record("baseline", 997_800, "succeeded"))
        baseline = self.h.run(now=997_850)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.h.task["next_run_at"] = 1_000_000
        self.h.runs.clear()
        self.h.runs.extend(
            self.h.run_record(f"r{i:03d}" + "x" * 25, 998_001 + i, "failed",
                              error_code="timeout")
            for i in range(101)
        )
        result = self.h.run(now=999_100)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.h.alerts()), 5)
        self.assertIn("processing the first 100", result.stderr)
        self.assertIn("delivery capped", result.stderr)
        self.assertNotIn("missed", "\n".join(a["body"] for a in self.h.alerts()))
        _, state = self.h.ctl.run("schedule", "state", "--task", self.h.task_id)
        self.assertEqual(len(state["pending_alerts"]), 95)

    def test_full_page_with_cursor_is_not_complete_missed_run_evidence(self):
        self.h.task["next_run_at"] = 998_100
        self.h.runs.append(self.h.run_record("baseline", 998_000, "succeeded"))
        baseline = self.h.run(now=998_050)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)

        # Exactly the requested page size is conforming, and the cursor says
        # the expected occurrence may be on another page. All rows are older
        # than the checkpoint so this remains a fast, inference-only test.
        self.h.runs[:] = [
            self.h.run_record(f"old{i:03d}", 997_000 - i, "succeeded")
            for i in range(100)
        ]
        self.h.httpd.next_cursor = "more"
        self.h.task["next_run_at"] = 1_000_000
        result = self.h.run(now=998_801, missed_grace=600)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.h.alerts(), [])


class StanddownStatus(unittest.TestCase):
    """ADR-0044 Decision §6/AC-7, detector-side half. A Program that loses
    the authority race records `standdown`, not `skipped` -- it must be
    accepted as a raw run status (not a fatal DetectorError) and it must
    NOT be treated as an incident (no alert, not in INCIDENT_STATUSES)."""

    def test_standdown_is_excluded_from_incident_statuses(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "nightshift_scheduled_run_detector", DETECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertNotIn("standdown", module.INCIDENT_STATUSES)

    def test_standdown_run_is_accepted_and_raises_no_alert(self):
        h = DetectorHarness()
        try:
            h.runs.append(h.run_record("a" * 32, 990_000, "standdown"))
            result = h.run(now=990_100)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(h.alerts(), [])
            import sqlite3
            with sqlite3.connect(h.ctl.db) as cx:
                row = cx.execute(
                    "SELECT status, alerted_at FROM scheduled_run_observations "
                    "WHERE observation_key=?",
                    (f"{h.task_id}:run:{'a' * 32}",),
                ).fetchone()
            self.assertIsNotNone(row, "standdown run was never observed/persisted")
            self.assertEqual(row[0], "standdown")
            self.assertIsNone(row[1], "standdown must never be queued for alert delivery")
        finally:
            h.close()

    def test_unsupported_status_before_this_change_would_have_been_rejected(self):
        """Control: an actually-unknown status is still a hard DetectorError --
        this change widens the set by exactly one value, not arbitrarily."""
        h = DetectorHarness()
        try:
            h.runs.append(h.run_record("a" * 32, 990_000, "not-a-real-status"))
            result = h.run(now=990_100)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported status", result.stderr)
        finally:
            h.close()


class ScheduledRunDetectorUnit(unittest.TestCase):
    def test_unit_binds_code_and_controller_to_one_rendered_release_root(self):
        unit = (REPO / "deploy" / "systemd" /
                "nightshift-scheduled-run-detector.service").read_text()
        self.assertIn("__TEAMS_DIR__/deploy/nightshift-scheduled-run-detector.py", unit)
        self.assertIn("--controller __TEAMS_DIR__/bin/nightshift-ctl", unit)
        self.assertNotIn("/srv/project/deploy/", unit)
        self.assertNotIn("omnigent stop", unit)
        self.assertIn("Environment=http_proxy= https_proxy= all_proxy=", unit)
        self.assertIn("TimeoutStartSec=240", unit)
        self.assertNotIn("RuntimeMaxSec=", unit)


if __name__ == "__main__":
    unittest.main()
