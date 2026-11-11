"""The scheduler probe: notice a failed scheduled run WITHOUT an LLM — issue #132.

The defect this closes, in the owner's words: the hourly scheduled task is the
only mechanism that returns this org to its objective after a session ends. It
failed at 04:00Z, 05:00Z and 06:00Z on 2026-08-24 (correlated upstream
`529 Overloaded`), nobody noticed for an hour, and the only reason it was ever
noticed is that an earlier driver session happened to still be awake.
**That luck is the defect.**

So the requirement is not "detect it" — it is "detect it with ZERO model calls
and NO agent session alive". Everything here runs `deploy/nightshift-scheduler-probe.py`
as a subprocess against a stdlib HTTP server on localhost. No model, no agent,
no live scheduler, no network beyond the loopback socket this file opens.

Requirement ids:
  SP-01  a healthy active task with clean runs -> exit 0, no alarm
  SP-02  a FAILED run at the head of the history -> exit 3 `runs_failing`,
         on the FIRST failure (one 529 must not need a second to be heard)
  SP-03  a DELIBERATELY PAUSED task -> exit 4, and the caller must suppress.
         This is the 08:00Z->19:00Z maintenance freeze, and it is the half of
         the problem that makes a naive detector useless: an alarm that fires
         on intended states is an alarm people stop reading
  SP-04  the task row is GONE -> exit 3 `task_missing` (the original fear: the
         heartbeat is a row the program agent created, and nothing recreates it)
  SP-05  `next_run_at` passed with nothing fired -> exit 3 `overdue`
  SP-06  the scheduler cannot be reached / answers garbage -> exit 2 `unknown`,
         which is NOT a verdict and must never buy silence
  SP-07  a run currently `running` is SKIPPED, not counted as success — an
         in-flight run must not mask the failures behind it
  SP-08  retry (condition 4) re-fires the failed run through the scheduler's own
         endpoint, honours exponential backoff, and is capped per slot
  SP-09  retry NEVER fires for a paused task, while a run is in flight, or for
         a slot older than one scheduling period
  SP-10  stdout is exactly one JSON object on EVERY path including errors
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "deploy", "nightshift-scheduler-probe.py")

HOUR = 3600
NOW = 1_787_600_000.0          # fixed clock; the probe takes --now as a test seam
SLOT = NOW - 300               # "this hour's" slot, 5 minutes ago


def run_row(scheduled_at, status, error_code=None, finished_at=None, rid=None):
    return {
        "id": rid or f"run-{int(scheduled_at)}",
        "scheduled_at": scheduled_at,
        "status": status,
        "error_code": error_code,
        "fired_at": scheduled_at + 3,
        "finished_at": finished_at if finished_at is not None else (
            None if status == "running" else scheduled_at + 200),
    }


class FakeScheduler:
    """The Omnigent scheduler's read API, plus /run, served on loopback.

    Deliberately a REAL socket rather than a monkeypatched urlopen: the probe is
    shipped as a standalone script run by a shell script under a systemd timer,
    and the thing most likely to break it is transport, not logic.
    """

    def __init__(self, tasks, runs, run_status=200, require_token=None):
        self.tasks = tasks
        self.runs = runs
        self.run_status = run_status
        self.posts = []
        self.auth_seen = []
        self.require_token = require_token
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                seen = self.headers.get("Authorization")
                outer.auth_seen.append((self.command, seen))
                if outer.require_token and seen != f"Bearer {outer.require_token}":
                    self._send(401, {"detail": "unauthenticated", "login_url": "/login"})
                    return False
                return True

            def do_GET(self):
                if not self._authorized():
                    return None
                path = self.path
                if path == "/v1/scheduled-tasks":
                    return self._send(200, {"scheduled_tasks": outer.tasks})
                if path.endswith("/runs"):
                    tid = path.split("/")[3]
                    return self._send(200, {"runs": outer.runs.get(tid, [])})
                if path.startswith("/v1/scheduled-tasks/"):
                    tid = path.rsplit("/", 1)[-1]
                    for t in outer.tasks:
                        if t["id"] == tid:
                            return self._send(200, t)
                    return self._send(404, {"detail": "not found"})
                return self._send(404, {"detail": "no route"})

            def do_POST(self):
                if not self._authorized():
                    return None
                outer.posts.append(self.path)
                return self._send(outer.run_status, {"ok": outer.run_status < 300})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.retry_state = os.path.join(self.tmp, "retry.json")
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.stop()

    def serve(self, state="active", runs=None, next_run_at=None,
              name="Nightshift hourly heartbeat", tasks=None, run_status=200,
              require_token=None):
        task = {"id": "task-1", "name": name, "state": state,
                "next_run_at": next_run_at}
        all_tasks = tasks if tasks is not None else [task]
        self.server = FakeScheduler(all_tasks, {"task-1": runs or []},
                                    run_status=run_status, require_token=require_token)
        return self.server

    def probe(self, *extra, now=NOW, url=None, token=None):
        cmd = [sys.executable, PROBE, "--base-url", url or self.server.url,
               "--now", repr(now), "--retry-state", self.retry_state, *extra]
        env = dict(os.environ)
        env.pop("OMNIGENT_REMOTE_AUTH_TOKEN", None)
        if token is not None:
            env["OMNIGENT_REMOTE_AUTH_TOKEN"] = token
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        # SP-10 asserted on EVERY call in this file, not once.
        try:
            payload = json.loads(p.stdout)
        except Exception:  # pragma: no cover - the assertion below reports it
            self.fail(f"stdout was not one JSON object: {p.stdout!r} / {p.stderr!r}")
        self.assertIsInstance(payload, dict)
        return p.returncode, payload


class SP01Healthy(Base):
    def test_active_task_with_clean_runs_is_silent(self):
        self.serve(runs=[run_row(SLOT, "succeeded"),
                         run_row(SLOT - HOUR, "succeeded")])
        code, out = self.probe()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["reason_code"], "healthy")
        self.assertEqual(out["consecutive_failures"], 0)


class SP02FailedRun(Base):
    def test_ONE_failed_run_is_enough(self):
        """The whole point of #132: the org must not need a SECOND failure.

        The production incident ran 04:00Z, 05:00Z, 06:00Z before a human
        happened to look. A threshold of two would have bought a second free
        hour of silence, which is the cost the issue exists to remove.
        """
        self.serve(runs=[run_row(SLOT, "failed", "runner_error"),
                         run_row(SLOT - HOUR, "succeeded")])
        code, out = self.probe()
        self.assertEqual(code, 3, out)
        self.assertEqual(out["reason_code"], "runs_failing")
        self.assertEqual(out["consecutive_failures"], 1)
        self.assertEqual(out["last_error_code"], "runner_error")

    def test_the_production_shape_three_in_a_row(self):
        """The real 2026-08-24 history, replayed."""
        self.serve(runs=[run_row(SLOT, "failed", "runner_error"),
                         run_row(SLOT - HOUR, "failed", "runner_error"),
                         run_row(SLOT - 2 * HOUR, "failed", "runner_error"),
                         run_row(SLOT - 3 * HOUR, "succeeded")])
        code, out = self.probe()
        self.assertEqual(code, 3)
        self.assertEqual(out["consecutive_failures"], 3)

    def test_history_order_is_not_trusted(self):
        """Same rows, shuffled. The verdict must come from scheduled_at."""
        self.serve(runs=[run_row(SLOT - 2 * HOUR, "succeeded"),
                         run_row(SLOT, "failed", "runner_error"),
                         run_row(SLOT - HOUR, "succeeded")])
        code, out = self.probe()
        self.assertEqual(code, 3, out)
        self.assertEqual(out["consecutive_failures"], 1)


class SP03DeliberatePause(Base):
    """The half that makes the difference between a detector and a nuisance."""

    def test_paused_task_is_exit_4_and_says_why(self):
        # 11 hours of total silence -- exactly the 08:00Z->19:00Z freeze.
        self.serve(state="paused", runs=[run_row(NOW - 11 * HOUR, "succeeded")])
        code, out = self.probe()
        self.assertEqual(code, 4, out)
        self.assertEqual(out["reason_code"], "paused")
        self.assertEqual(out["status"], "paused")

    def test_a_paused_task_with_FAILED_runs_still_does_not_alarm(self):
        """Order matters. Intent is read BEFORE the run history.

        A task paused after a bad hour still has failed rows at the head of its
        history. Reporting them would mean the owner cannot stop the alarm by
        pausing the thing that is alarming -- which is precisely the state they
        put the org into for maintenance today.
        """
        self.serve(state="paused",
                   runs=[run_row(SLOT, "failed", "runner_error")])
        code, out = self.probe()
        self.assertEqual(code, 4, out)
        self.assertEqual(out["reason_code"], "paused")

    def test_silence_is_bought_only_with_a_POSITIVE_state(self):
        """A task with no `state` field at all is a schema surprise, not consent.

        This is the same rule the watchdog already lives by: absence of evidence
        never buys silence. An unknown shape must land on `unknown` (exit 2),
        never on `paused`.
        """
        self.serve(tasks=[{"id": "task-1", "name": "heartbeat",
                           "next_run_at": None}],
                   runs=[run_row(SLOT, "succeeded")])
        code, out = self.probe()
        self.assertEqual(code, 2, out)
        self.assertEqual(out["reason_code"], "probe_unavailable")


class SP04TaskGone(Base):
    def test_no_matching_task_is_loud(self):
        self.serve(tasks=[], runs=[])
        code, out = self.probe()
        self.assertEqual(code, 3, out)
        self.assertEqual(out["reason_code"], "task_missing")

    def test_an_explicit_task_id_that_404s_is_also_loud(self):
        self.serve(runs=[run_row(SLOT, "succeeded")])
        code, out = self.probe("--task-id", "does-not-exist")
        self.assertEqual(code, 3, out)
        self.assertEqual(out["reason_code"], "task_missing")

    def test_name_match_survives_the_task_being_RECREATED(self):
        """The documented recovery move mints a NEW task id.

        A probe pinned to the dead id would go quiet exactly after the incident
        it exists to report, so the name fallback is load-bearing, not a
        convenience.
        """
        self.serve(runs=[run_row(SLOT, "succeeded")],
                   name="Nightshift hourly heartbeat (recreated)")
        code, out = self.probe()
        self.assertEqual(code, 0, out)


class SP05Overdue(Base):
    def test_next_run_at_long_past_is_a_wedged_scheduler(self):
        self.serve(runs=[run_row(SLOT - 4 * HOUR, "succeeded")],
                   next_run_at="2026-08-24T18:00:00+00:00")
        code, out = self.probe(now=1_787_608_000.0)   # well past 18:00Z
        self.assertEqual(code, 3, out)
        self.assertEqual(out["reason_code"], "overdue")

    def test_a_next_run_in_the_near_future_is_fine(self):
        self.serve(runs=[run_row(SLOT, "succeeded")],
                   next_run_at="2026-08-24T20:00:00+00:00")
        code, out = self.probe(now=1_787_598_800.0)
        self.assertEqual(code, 0, out)

    def test_an_unparseable_next_run_at_does_not_invent_a_verdict(self):
        """A formatting change upstream must not manufacture an alarm."""
        self.serve(runs=[run_row(SLOT, "succeeded")], next_run_at="soon-ish")
        code, out = self.probe()
        self.assertEqual(code, 0, out)


class SP06CannotBeConsulted(Base):
    """Exit 2 is not a verdict. It must never suppress and never alarm."""

    def test_server_refused(self):
        self.serve(runs=[])
        port = self.server.httpd.server_address[1]
        self.server.stop()
        self.server = None
        code, out = self.probe(url="http://127.0.0.1:%d" % port)
        self.assertEqual(code, 2, out)
        self.assertEqual(out["reason_code"], "probe_unavailable")

    def test_listing_without_the_expected_key(self):
        self.server = FakeScheduler([], {})
        # Serve a shape the probe does not understand.
        self.server.tasks = None
        code, out = self.probe()
        self.assertEqual(code, 2, out)


class SP07RunningRunDoesNotMask(Base):
    def test_an_in_flight_run_is_skipped_not_counted_as_success(self):
        """Otherwise a task that fails every hour hides for as long as one run
        happens to be up -- and the run in flight says nothing yet about the
        ones before it."""
        self.serve(runs=[run_row(SLOT + HOUR, "running"),
                         run_row(SLOT, "failed", "runner_error")])
        code, out = self.probe()
        self.assertEqual(code, 3, out)
        self.assertEqual(out["consecutive_failures"], 1)


class SP08Retry(Base):
    """Condition 4: a single transient 529 must not cost the org an hour."""

    def test_retry_refires_the_run_after_the_backoff(self):
        finished = NOW - 600
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=finished)])
        code, out = self.probe("--retry", "--retry-base-delay", "120")
        self.assertEqual(code, 3, out)
        self.assertTrue(out["retry"]["attempted"], out["retry"])
        self.assertTrue(out["retry"]["ok"])
        self.assertEqual(self.server.posts,
                         ["/v1/scheduled-tasks/task-1/run"])

    def test_backoff_is_honoured_before_the_first_attempt(self):
        finished = NOW - 30          # only 30s ago; base delay is 120s
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=finished)])
        code, out = self.probe("--retry", "--retry-base-delay", "120")
        self.assertEqual(code, 3)
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("backing off", out["retry"]["why"])
        self.assertEqual(self.server.posts, [])

    def test_the_budget_is_capped_and_the_backoff_grows(self):
        """Ticks repeat every 10 minutes. Without a per-slot budget this would
        re-fire the org forever."""
        finished = NOW - 10 * HOUR   # long past any backoff
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=finished)])
        # The clock advances between ticks, as the timer's does. Backoff after
        # the first attempt is measured from that ATTEMPT, not from the original
        # failure -- otherwise the exponent grows while the actual wait does not.
        for tick, expected in ((0, 1), (1, 2)):
            code, out = self.probe("--retry", "--max-retries", "2",
                                   now=NOW + tick * 600)
            self.assertTrue(out["retry"]["attempted"], out["retry"])
            self.assertEqual(out["retry"]["attempt"], expected)
        code, out = self.probe("--retry", "--max-retries", "2",
                               now=NOW + 2 * 600)
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("budget spent", out["retry"]["why"])
        self.assertEqual(len(self.server.posts), 2)

    def test_the_backoff_after_an_attempt_is_measured_from_that_attempt(self):
        """A second tick moments later must WAIT, not re-fire."""
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=NOW - 10 * HOUR)])
        self.probe("--retry", "--retry-base-delay", "300", "--max-retries", "3")
        _code, out = self.probe("--retry", "--retry-base-delay", "300",
                                "--max-retries", "3", now=NOW + 60)
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("backing off", out["retry"]["why"])
        self.assertEqual(len(self.server.posts), 1)

    def test_a_rejected_refire_still_consumes_an_attempt(self):
        """Otherwise a scheduler that answers 500 becomes an unbounded loop."""
        finished = NOW - 10 * HOUR
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=finished)], run_status=500)
        code, out = self.probe("--retry", "--max-retries", "1")
        self.assertTrue(out["retry"]["attempted"])
        self.assertFalse(out["retry"]["ok"])
        code, out = self.probe("--retry", "--max-retries", "1")
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("budget spent", out["retry"]["why"])

    def test_retry_is_opt_in(self):
        finished = NOW - 10 * HOUR
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=finished)])
        code, out = self.probe()
        self.assertEqual(code, 3)
        self.assertNotIn("retry", out)
        self.assertEqual(self.server.posts, [])


class SP09RetryRefusals(Base):
    def test_never_retries_a_paused_task(self):
        self.serve(state="paused",
                   runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=NOW - 10 * HOUR)])
        code, out = self.probe("--retry")
        self.assertEqual(code, 4, out)
        self.assertEqual(self.server.posts, [])

    def test_never_retries_while_a_run_is_in_flight(self):
        self.serve(runs=[run_row(SLOT + HOUR, "running"),
                         run_row(SLOT, "failed", "runner_error",
                                 finished_at=NOW - 10 * HOUR)])
        code, out = self.probe("--retry")
        self.assertEqual(code, 3)
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("in flight", out["retry"]["why"])
        self.assertEqual(self.server.posts, [])

    def test_never_resurrects_a_slot_older_than_one_period(self):
        """Re-running a three-hour-old slot re-runs stale instructions and tells
        the operator nothing they can still act on."""
        old = NOW - 5 * HOUR
        self.serve(runs=[run_row(old, "failed", "runner_error",
                                 finished_at=old + 100)])
        code, out = self.probe("--retry", "--period", "3600")
        self.assertEqual(code, 3)
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("history, not a retry", out["retry"]["why"])
        self.assertEqual(self.server.posts, [])

    def test_a_corrupt_retry_state_file_does_not_wedge_the_retry(self):
        with open(self.retry_state, "w") as fh:
            fh.write("{not json")
        finished = NOW - 10 * HOUR
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=finished)])
        code, out = self.probe("--retry")
        self.assertTrue(out["retry"]["attempted"], out["retry"])


class SP11NoModelIsInvolved(unittest.TestCase):
    """The hard requirement, asserted against the source rather than assumed.

    "LLM-independent" is the closing condition, so it gets a test. If someone
    later reaches for a model call to classify a failure, this fails loudly.
    """

    def test_the_probe_imports_only_the_standard_library(self):
        import ast
        with open(PROBE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        allowed = {"argparse", "json", "os", "sys", "time", "urllib",
                   "datetime", "__future__"}
        self.assertEqual(imported - allowed, set(),
                         "the probe must stay stdlib-only and model-free")

    def test_no_model_or_agent_call_appears_anywhere_in_the_probe(self):
        with open(PROBE, encoding="utf-8") as fh:
            text = fh.read().lower()
        for forbidden in ("anthropic", "openai", "sys_call", "claude",
                          "sys_session", "completion(", "chat("):
            self.assertNotIn(forbidden, text)


class SP14ControlApiAuth(Base):
    """Public-release audit C5: the probe can talk to an authenticated server,
    and its bearer travels in the environment, never on argv."""

    def failing(self, **kw):
        return self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                        finished_at=NOW - 600)], **kw)

    def test_every_request_carries_the_bearer_from_the_environment(self):
        self.failing(require_token="s3cret")
        code, out = self.probe("--retry", "--retry-base-delay", "120", token="s3cret")
        self.assertEqual(code, 3, out)
        self.assertTrue(out["retry"]["ok"], out["retry"])
        methods = {m for m, _ in self.server.auth_seen}
        self.assertEqual(methods, {"GET", "POST"})
        self.assertTrue(all(a == "Bearer s3cret" for _, a in self.server.auth_seen),
                        self.server.auth_seen)

    def test_no_token_means_no_authorization_header(self):
        self.failing()
        self.probe()
        self.assertTrue(self.server.auth_seen)
        self.assertTrue(all(a is None for _, a in self.server.auth_seen))

    def test_a_rejected_probe_is_unknown_and_says_how_to_fix_it(self):
        self.failing(require_token="s3cret")
        code, out = self.probe("--retry")
        self.assertEqual(code, 2, out)
        self.assertIn("OMNIGENT_REMOTE_AUTH_TOKEN", out["detail"])
        self.assertEqual(self.server.posts, [], "nothing may fire unauthenticated")

    def test_there_is_no_argv_option_for_the_secret(self):
        p = subprocess.run([sys.executable, PROBE, "--help"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertNotRegex(p.stdout.lower(), r"--[a-z-]*(token|secret|bearer|auth)")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SP12TheRetryBudgetSurvivesTheRefire(Base):
    """Regression, found by running the probe against a REAL scheduler.

    A re-fire creates a NEW run row with a NEW `scheduled_at`. The budget was
    keyed on that value, so every attempt minted a fresh key, the cap reset, and
    the only thing still limiting re-fires was a backoff that also restarted
    from each new failure. A persistently failing task would have been re-fired
    on roughly every watchdog tick, forever — against production.

    The fixtures in SP08 could not catch this: they hold the run history fixed
    across probe invocations, which is precisely the thing a real scheduler does
    not do. This test replays what actually happened.
    """

    def _refire_appends_a_new_failed_run(self, runs, slot_gap):
        """Mimic the scheduler: POST /run appends a new failed row."""
        original_do_post = self.server.posts

        class Appending(list):
            def append(inner, path):
                original_do_post.__class__.append(inner, path)
                nxt = max(r["scheduled_at"] for r in runs) + slot_gap
                runs.insert(0, run_row(nxt, "failed", "runner_error",
                                       finished_at=nxt + 5))
                self.server.runs["task-1"] = list(runs)

        self.server.posts = Appending(original_do_post)

    def test_a_refires_new_run_row_does_not_reset_the_budget(self):
        runs = [run_row(SLOT, "failed", "runner_error", finished_at=NOW - 10 * HOUR)]
        self.serve(runs=list(runs))
        self.server.runs["task-1"] = list(runs)
        self._refire_appends_a_new_failed_run(runs, slot_gap=30)

        # Advance the clock between ticks the way the 10-minute timer does, so
        # the backoff is never what is doing the limiting here -- the CAP is.
        seen = []
        for tick in range(6):
            _code, out = self.probe("--retry", "--max-retries", "2",
                                    "--retry-base-delay", "1",
                                    now=NOW + tick * 600)
            seen.append(out["retry"]["attempted"])
        self.assertEqual(len(self.server.posts), 2,
                         f"the cap must bound re-fires across new run rows; got {seen}")

    def test_the_budget_key_is_the_period_not_the_run(self):
        """Two failures in the SAME hour share one budget."""
        runs = [run_row(SLOT, "failed", "runner_error", finished_at=NOW - 10 * HOUR),
                run_row(SLOT - 60, "failed", "runner_error", finished_at=NOW - 10 * HOUR)]
        self.serve(runs=runs)
        self.probe("--retry", "--max-retries", "1", "--retry-base-delay", "1")
        _code, out = self.probe("--retry", "--max-retries", "1", "--retry-base-delay", "1")
        self.assertFalse(out["retry"]["attempted"])
        self.assertIn("budget spent", out["retry"]["why"])
        with open(self.retry_state) as fh:
            keys = list(json.load(fh))
        self.assertEqual(len(keys), 1, f"one key per period, got {keys}")
        self.assertIn(":period:", keys[0])


class SP13RetryFailsClosedWhenTheBudgetCannotPersist(Base):
    """The cap is only a cap while the file it lives in can be written.

    Review finding (architect/Codex, cross-vendor review of PR #144): the retry
    POSTed the production side effect FIRST and persisted the spent attempt
    afterward, downgrading every write failure to a `state_persisted:false`
    field. Nothing consumed that field. On the next 10-minute tick the attempts
    loaded as zero again and another production run was minted — forever, if the
    state directory stays unwritable, full or corrupt. SP12 fixed the budget KEY;
    this fixes the budget's DEPENDENCY. The claim in the PR body was a hard cap
    "across ticks and restarts", and a cap that evaporates with its own storage
    is not that.

    So the rule is: RESERVE the attempt durably BEFORE the POST, and if the
    reservation cannot be written, do not POST at all. A retry that cannot count
    itself does not get to fire.
    """

    def _unwritable_state_path(self):
        """Put the state file under a PARENT THAT IS A FILE.

        Deliberately not a chmod-0500 directory: this suite must fail closed the
        same way when it runs as root, and root ignores directory permissions.
        `os.makedirs()` raises on a non-directory parent for every uid.
        """
        blocked = os.path.join(self.tmp, "blocked")
        with open(blocked, "w") as fh:
            fh.write("not a directory")
        self.retry_state = os.path.join(blocked, "retry.json")

    def test_a_refire_is_refused_when_the_budget_cannot_be_reserved(self):
        self._unwritable_state_path()
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=NOW - 10 * HOUR)])
        code, out = self.probe("--retry", "--max-retries", "2")
        self.assertEqual(code, 3, out)
        self.assertFalse(out["retry"]["attempted"], out["retry"])
        self.assertFalse(out["retry"]["state_persisted"], out["retry"])
        self.assertIn("fail closed", out["retry"]["why"].lower())
        self.assertEqual(self.server.posts, [],
                         "an unreservable budget must not POST to production")

    def test_repeated_ticks_never_mint_a_run_while_the_state_never_persists(self):
        """The actual production hazard: not one extra run, an unbounded loop.

        Six ticks is an hour of the 10-minute timer. Before the fix every one of
        them POSTed, because every one of them loaded an empty budget.
        """
        self._unwritable_state_path()
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=NOW - 10 * HOUR)])
        fired = []
        for tick in range(6):
            _code, out = self.probe("--retry", "--max-retries", "2",
                                    "--retry-base-delay", "1",
                                    now=NOW + tick * 600)
            if out["retry"]["attempted"]:
                fired.append(tick)
        # Asserted on the whole hour, not on the first tick: the defect is the
        # LOOP, and a RED run should print every tick that minted a run.
        self.assertEqual(self.server.posts, [],
                         f"unbounded re-fire loop restored: {len(self.server.posts)} "
                         f"production runs minted at ticks {fired} "
                         f"despite max-retries=2")

    def test_the_reservation_is_written_before_the_post_not_after(self):
        """Asserted on the file, not on the return value.

        If the process were killed between the POST and the write — the crash
        that makes 'persist afterward' unsafe even on a writable disk — the
        attempt must already be on disk. The FakeScheduler records the POST, so
        reading the state file after a successful attempt proves the ordering
        only if the count is there; the refusal tests above prove the converse.
        """
        self.serve(runs=[run_row(SLOT, "failed", "runner_error",
                                 finished_at=NOW - 10 * HOUR)])
        _code, out = self.probe("--retry", "--max-retries", "2")
        self.assertTrue(out["retry"]["attempted"], out["retry"])
        with open(self.retry_state) as fh:
            state = json.load(fh)
        key = next(iter(state))
        self.assertEqual(state[key]["attempts"], 1, state)
        self.assertEqual(self.server.posts, ["/v1/scheduled-tasks/task-1/run"])
