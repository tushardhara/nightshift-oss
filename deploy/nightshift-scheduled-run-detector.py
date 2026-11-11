#!/usr/bin/env python3
"""Report failed, missed, or stuck Omnigent scheduled runs without an LLM.

Omnigent remains authoritative for scheduled-task firings.  This process reads
its loopback API, normalizes a closed failure class, persists observation and
delivery state through ``nightshift-ctl``, and comments on the configured GitHub
issue directly.  It never starts, retries, pauses, or edits a scheduled task.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RUNS_PER_POLL = 100
MAX_DELIVERIES_PER_POLL = 5
MAX_EPOCH = 253_402_300_799  # 9999-12-31T23:59:59Z
OMNIGENT_STALE_RUN_SECONDS = 6 * 60 * 60
INCIDENT_STATUSES = frozenset({"failed", "skipped", "missed", "stuck"})
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class DetectorError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never let a loopback URL redirect the detector onto another authority."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number: {token}")


def _validate_server(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("--server must be an http(s) loopback URL")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError as exc:
        raise ValueError("--server must use a loopback IP literal") from exc
    if not loopback:
        raise ValueError("--server must use a loopback IP literal")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("--server must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("--server must not contain a path")
    return value.rstrip("/")


def _validate_id(label: str, value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be 1-128 safe id characters")
    return value


def _api_get(server: str, path: str, *, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        server + path,
        headers={"Accept": "application/json", "User-Agent": "nightshift-run-detector/1"},
        method="GET",
    )
    try:
        # Never inherit proxy variables.  A proxy would turn a syntactically
        # loopback-only URL into off-box disclosure and let the proxy forge the
        # scheduler response that drives durable state and alerts.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DetectorError(f"Omnigent API read failed: {type(exc).__name__}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise DetectorError("Omnigent API response exceeded 2 MiB")
    try:
        value = json.loads(
            body,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise DetectorError("Omnigent API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise DetectorError("Omnigent API returned a non-object")
    return value


def _ctl(args: argparse.Namespace, *command: str) -> dict[str, Any]:
    argv = [args.controller, "--db", args.control_db]
    if args.now is not None:
        argv += ["--now", repr(float(args.now))]
    argv += list(command)
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DetectorError(f"control-plane invocation failed: {type(exc).__name__}") from exc
    payload = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            pass
    if result.returncode != 0 or not isinstance(payload, dict):
        reason = payload.get("reason") if isinstance(payload, dict) else "invalid output"
        raise DetectorError(
            f"control-plane command failed ({result.returncode}): {reason}"
        )
    return payload


def _epoch(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DetectorError("timestamp must not be boolean")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise DetectorError("timestamp must be finite")
        epoch = int(value)
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError as exc:
            raise DetectorError("invalid ISO timestamp from Omnigent") from exc
        if parsed.tzinfo is None:
            raise DetectorError("Omnigent timestamp has no timezone")
        try:
            epoch = int(parsed.timestamp())
        except (OverflowError, OSError, ValueError) as exc:
            raise DetectorError("timestamp is outside the supported range") from exc
    elif not isinstance(value, (int, float)):
        raise DetectorError("unsupported timestamp type from Omnigent")
    if not 0 <= epoch <= MAX_EPOCH:
        raise DetectorError("timestamp is outside the supported range")
    return epoch


def _assistant_texts(items: dict[str, Any]) -> list[str]:
    data = items.get("data")
    if not isinstance(data, list):
        return []
    texts: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text[:16_384])
    return texts


def _classify(error_code: Any, assistant_texts: list[str]) -> str:
    code = error_code.lower() if isinstance(error_code, str) else ""
    text = "\n".join(assistant_texts).lower()
    combined = f"{code}\n{text}"
    if "529" in combined or "overloaded" in combined or "overload" in combined:
        return "vendor_overloaded"
    if any(token in combined for token in ("rate_limit", "rate limit", "429", "usage limit")):
        return "vendor_rate_limited"
    if any(token in combined for token in (
        "unauthorized", "authentication", "credential", "invalid token", "401", "403"
    )):
        return "auth"
    if any(token in combined for token in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(token in combined for token in (
        "host_offline", "host unavailable", "host_unavailable", "no connected host"
    )):
        return "host_unavailable"
    if code == "incomplete":
        return "incomplete"
    if any(token in combined for token in (
        "runner", "harness", "launch_failed", "session_create_failed", "owner_grant_failed"
    )):
        return "runner_harness"
    return "unknown"


def _observe(
    args: argparse.Namespace,
    *,
    key: str,
    scheduled_at: int,
    status: str,
    failure_class: str,
    run_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    command = [
        "schedule", "observe", "--task", args.task_id, "--key", key,
        "--scheduled-at", str(scheduled_at), "--status", status,
        "--class", failure_class,
    ]
    if run_id:
        command += ["--run", run_id]
    if conversation_id:
        command += ["--conversation", conversation_id]
    return _ctl(args, *command)


def _iso(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


def _alert_body(observation: dict[str, Any]) -> str:
    count = int(observation["consecutive_failures"])
    severity = "OUTAGE" if count >= 2 else "DEGRADED"
    run_id = observation.get("run_id") or "none (missed occurrence)"
    conversation_id = observation.get("conversation_id") or "none"
    key = observation["observation_key"]
    return (
        f"## Nightshift scheduled heartbeat {severity}\n\n"
        f"<!-- nightshift-scheduled-run:{key} -->\n"
        "The autonomy run could not proceed. This report was delivered by the "
        "non-LLM detector; no Claude/Codex turn or Program driver produced it.\n\n"
        f"- task: `{observation['task_id']}`\n"
        f"- run: `{run_id}`\n"
        f"- scheduled: `{_iso(int(observation['scheduled_at']))}`\n"
        f"- observed status: `{observation['status']}`\n"
        f"- normalized failure class: `{observation['failure_class']}`\n"
        f"- consecutive failures: {count}\n"
        f"- conversation: `{conversation_id}`\n\n"
        "Raw transcript/error text is intentionally excluded.\n\n"
        "Refs #132\n"
    )


def _deliver(args: argparse.Namespace, observation: dict[str, Any]) -> None:
    body_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="nightshift-run-alert-",
            suffix=".md", delete=False,
        ) as handle:
            body_path = handle.name
            os.chmod(body_path, 0o600)
            handle.write(_alert_body(observation))
        result = subprocess.run(
            [args.gh, "issue", "comment", str(args.issue), "--repo", args.repo,
             "--body-file", body_path],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DetectorError(f"GitHub alert delivery failed: {type(exc).__name__}") from exc
    finally:
        if body_path:
            try:
                os.unlink(body_path)
            except FileNotFoundError:
                pass
    if result.returncode != 0:
        raise DetectorError(f"GitHub alert delivery failed with exit {result.returncode}")
    _ctl(args, "schedule", "alerted", "--key", observation["observation_key"])


def _deliver_pending(
    args: argparse.Namespace, state: dict[str, Any], budget: list[int]
) -> None:
    pending = state.get("pending_alerts")
    if not isinstance(pending, list):
        raise DetectorError("control-plane state omitted pending_alerts")
    pending_count = state.get("pending_alert_count", len(pending))
    if not isinstance(pending_count, int) or pending_count < len(pending):
        raise DetectorError("control-plane returned an invalid pending alert count")
    if pending_count > budget[0]:
        print(
            "nightshift-scheduled-run-detector: pending alert delivery capped "
            f"at {budget[0]} of {pending_count} for this poll",
            file=sys.stderr,
        )
    for observation in pending[:budget[0]]:
        if not isinstance(observation, dict):
            raise DetectorError("control-plane returned invalid pending observation")
        budget[0] -= 1
        _deliver(args, observation)


def _process_run(args: argparse.Namespace, run: dict[str, Any], now: int) -> None:
    run_id = _validate_id("run id", str(run.get("id", "")))
    if run.get("scheduled_task_id") != args.task_id:
        raise DetectorError("run belongs to a different scheduled task")
    scheduled_at = _epoch(run.get("scheduled_at"))
    if scheduled_at is None:
        raise DetectorError("run omitted scheduled_at")
    status = run.get("status")
    # ADR-0044 Decision §6/AC-7 (detector-side half; the nightshift-ctl-side
    # SCHEDULE_STATUSES widening landed in 174-implement-authority, PR #304).
    # A Program that lost the authority race records its own run as
    # "standdown" via `schedule observe --status standdown`. It must be an
    # accepted raw status here (or every such run is a fatal DetectorError,
    # never reaching _observe at all) and it must NOT join INCIDENT_STATUSES
    # above -- standing down cleanly is not itself a failure to alert on.
    if status not in {"scheduled", "running", "succeeded", "failed", "skipped", "standdown"}:
        raise DetectorError("run returned an unsupported status")
    conversation_id = run.get("conversation_id")
    if conversation_id is not None:
        conversation_id = _validate_id("conversation id", str(conversation_id))

    observed_status = status
    failure_class = "none"
    if status == "running":
        started_at = _epoch(run.get("fired_at")) or scheduled_at
        if now - started_at >= args.stuck_deadline:
            observed_status = "stuck"
            failure_class = "incomplete"
    elif status in {"failed", "skipped"}:
        texts: list[str] = []
        if conversation_id:
            items = _api_get(
                args.server,
                f"/v1/sessions/{urllib.parse.quote(conversation_id)}/items"
                "?limit=50&order=desc",
                timeout=args.timeout,
            )
            texts = _assistant_texts(items)
        failure_class = _classify(run.get("error_code"), texts)

    _observe(
        args,
        key=f"{args.task_id}:run:{run_id}",
        run_id=run_id,
        scheduled_at=scheduled_at,
        conversation_id=conversation_id,
        status=observed_status,
        failure_class=failure_class,
    )


def run_detector(args: argparse.Namespace) -> None:
    now = int(args.now if args.now is not None else time.time())
    delivery_budget = [MAX_DELIVERIES_PER_POLL]

    # A delivery outage must not stop observation.  Try old pending alerts, but
    # retain the failure and continue polling so every new incident is durable
    # even while gh is unavailable.  The unit still exits nonzero at the end.
    control = _ctl(args, "schedule", "state", "--task", args.task_id)
    delivery_error = None
    try:
        _deliver_pending(args, control, delivery_budget)
    except DetectorError as exc:
        delivery_error = exc

    task = _api_get(
        args.server,
        f"/v1/scheduled-tasks/{urllib.parse.quote(args.task_id)}",
        timeout=args.timeout,
    )
    if task.get("id") != args.task_id:
        raise DetectorError("task detail returned a different id")
    if task.get("state") != "active":
        _ctl(args, "schedule", "expect", "--task", args.task_id, "--clear")
        if delivery_error is not None:
            raise delivery_error
        return

    history = _api_get(
        args.server,
        f"/v1/scheduled-tasks/{urllib.parse.quote(args.task_id)}/runs?limit=100",
        timeout=args.timeout,
    )
    runs = history.get("runs")
    if not isinstance(runs, list):
        raise DetectorError("run history returned an invalid runs list")
    history_truncated = (
        len(runs) >= MAX_RUNS_PER_POLL or history.get("next_cursor") is not None
    )
    if len(runs) > MAX_RUNS_PER_POLL:
        print(
            "nightshift-scheduled-run-detector: Omnigent returned "
            f"{len(runs)} runs; processing the first {MAX_RUNS_PER_POLL}",
            file=sys.stderr,
        )
        runs = runs[:MAX_RUNS_PER_POLL]
    if any(not isinstance(run, dict) for run in runs):
        raise DetectorError("run history returned an invalid runs list")

    state = control.get("state")
    last_scheduled_at = None if state is None else state.get("last_run_scheduled_at")
    expected_at = None if state is None else state.get("next_expected_at")
    if last_scheduled_at is not None:
        last_scheduled_at = int(last_scheduled_at)
    if expected_at is not None:
        expected_at = int(expected_at)

    run_times = {_epoch(run.get("scheduled_at")) for run in runs}
    if (
        expected_at is not None
        and not history_truncated
        and now > expected_at + args.missed_grace
        and expected_at not in run_times
    ):
        _observe(
            args,
            key=f"{args.task_id}:missed:{expected_at}",
            scheduled_at=expected_at,
            status="missed",
            failure_class="incomplete",
        )

    ordered = sorted(runs, key=lambda run: (_epoch(run.get("scheduled_at")) or -1,
                                            str(run.get("id", ""))))
    if last_scheduled_at is None:
        candidates = ordered[-1:]
    else:
        # Include equality so an unacknowledged newest alert is retried and two
        # run-now firings in the same epoch second cannot hide one another.
        candidates = [
            run for run in ordered
            if (_epoch(run.get("scheduled_at")) or -1) >= last_scheduled_at
        ]
    for run in candidates:
        _process_run(args, run, now)

    next_expected = _epoch(task.get("next_run_at"))
    if next_expected is not None:
        _ctl(
            args, "schedule", "expect", "--task", args.task_id,
            "--at", str(next_expected),
        )

    # Deliver only after every observation/checkpoint mutation committed.  A
    # failed transport leaves all rows pending for the next timer invocation.
    if delivery_error is None:
        pending = _ctl(args, "schedule", "state", "--task", args.task_id)
        _deliver_pending(args, pending, delivery_budget)
    else:
        raise delivery_error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", default="http://127.0.0.1:6767")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--control-db", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--missed-grace", type=int, default=600)
    parser.add_argument("--stuck-deadline", type=int, default=4500)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--now", type=float, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        args.server = _validate_server(args.server)
        args.task_id = _validate_id("--task-id", args.task_id)
        if not REPO_RE.fullmatch(args.repo):
            raise ValueError("--repo must be owner/name")
        if args.issue <= 0:
            raise ValueError("--issue must be positive")
        if args.missed_grace <= 0:
            raise ValueError("--missed-grace must be positive")
        if not 0 < args.stuck_deadline < OMNIGENT_STALE_RUN_SECONDS:
            raise ValueError("--stuck-deadline must be positive and below six hours")
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if not math.isfinite(args.timeout):
            raise ValueError("--timeout must be finite")
        if args.now is not None and not math.isfinite(args.now):
            raise ValueError("--now must be finite")
        for label in ("controller", "control-db"):
            if not os.path.isabs(getattr(args, label.replace("-", "_"))):
                raise ValueError(f"--{label} must be absolute")
        resolved_gh = shutil.which(args.gh)
        if resolved_gh is None:
            raise ValueError("--gh must name an executable")
        args.gh = resolved_gh
    except ValueError as exc:
        parser.error(str(exc))

    try:
        run_detector(args)
    except (DetectorError, ValueError) as exc:
        print(f"nightshift-scheduled-run-detector: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
