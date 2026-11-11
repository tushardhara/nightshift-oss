#!/usr/bin/env python3
"""Scheduler-state probe: did the org's wake-up mechanism itself fail?

WHY THIS EXISTS (issue #132)
---------------------------
`nightshift-watchdog.sh` answers one question: *is the Program alive?* — read by
content from the beat record the Program writes (ADR-0002). That is the right
question and this probe does not replace it.

But it is not the ONLY question, and on 2026-08-24 the missing one cost the org
an hour. The hourly scheduled task is the only thing that returns this org to its
objective after a session ends. It FAILED at 04:00Z, 05:00Z and 06:00Z
(`runner_error`, correlated upstream `529 Overloaded`). Nobody noticed, and the
only reason it was noticed at all is that an earlier driver session happened to
still be awake. **That luck is the defect.**

The beat record cannot close this on its own, for two reasons:

  1. It is SLOW to the failure. A failed run writes no beat, so the beat goes
     stale — but only after STALE_HOURS (3 by default). Three missed wake-ups
     is a poor first alert for a mechanism that fires hourly. The scheduler
     already KNOWS the run failed, the instant it failed, and records it with an
     `error_code`. Waiting three hours to infer what is stated as a fact is
     throwing away the signal.

  2. It CANNOT tell a deliberate pause from a death, and so it cries wolf. The
     org was silent 08:00Z->19:00Z on 2026-08-24: an owner maintenance freeze,
     the task paused on purpose via the API. No beats for eleven hours. A
     beat-only watchdog alerts — twice, at the re-alert interval — about a
     silence the owner created deliberately. An alarm that fires on intended
     states is an alarm that gets ignored, which is how a real one gets missed.

The discriminating fact is not in the control plane at all; it is in the
scheduler, and it is explicit rather than inferred:

    a DEATH      leaves `status: failed` run rows, or a `next_run_at` that has
                 passed with nothing fired, while `state` stays `active`
    a PAUSE      leaves NO run rows at all AND `state` is not `active`

So "don't scream about a deliberate pause" is keyed on a positive statement of
intent (`state`), not on a heuristic about how long silence has lasted. That is
the same discipline the watchdog already lives by: absence of evidence never
buys silence, and silence is only ever bought with a positive fact.

LLM-INDEPENDENCE IS THE POINT
-----------------------------
Zero model calls. Zero agent sessions. stdlib only, over HTTP to the local
Omnigent server. If noticing that the loop is dead required the loop, it would
have reproduced the bug it is meant to catch.

EXIT CODE CONTRACT (mirrors `nightshift-ctl beat show`, deliberately)
---------------------------------------------------------------------
Exactly one JSON object on stdout on EVERY path, including errors — a consumer
never has to special-case an empty read.

    0  healthy    the scheduled task exists, is active, and its recent runs are fine
    3  VERDICT    something is wrong and a human should be told. `reason_code`
                  says which: task_missing | runs_failing | overdue
    4  paused     positive evidence of a DELIBERATE pause. The caller must
                  SUPPRESS, not alert.
    2  unknown    the probe could not reach or parse the scheduler. This is NOT
                  a verdict in either direction and must never buy silence —
                  the caller falls through to the beat check, which alerts on
                  its own if the org really is dead.

Note the asymmetry, and that it is deliberate: only exit 4 suppresses, and only
exit 4 requires a positive `state` field saying so. Every failure mode of this
probe — server down, malformed JSON, unknown schema, timeout — lands on 2 and
changes nothing. A probe that fails silent would be worse than no probe.

RETRY WITH BACKOFF (issue #132 closing condition 4)
---------------------------------------------------
The 04:00-06:00Z failures were transient upstream vendor overload. A single 529
must not cost this org an hour, and it does today: the scheduler records the
failure and then waits for the next hourly tick. `--retry` re-fires a failed run
through the scheduler's own `/run` endpoint after a backoff, with hard limits:

  - only when the task `state` is `active` (never re-fire a paused task)
  - only for the CURRENT scheduling slot — a failure from three hours ago is
    history, not something to resurrect
  - never while any run is `running` — no double-driving the org
  - at most `--max-retries` attempts per slot, tracked in a state file keyed by
    the slot's `scheduled_at`, so ticks and restarts cannot multiply them
  - exponential backoff from the failed run's `finished_at`, so a vendor that
    is still overloaded is not hammered

Retry is OPT-IN (`--retry`). Firing production work is a side effect, and a
probe's safe default is to observe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = os.environ.get("NIGHTSHIFT_OMNIGENT_URL", "http://127.0.0.1:6767")
DEFAULT_TIMEOUT = 10.0

# Bearer for a server running omnigent's accounts mode (deploy/README.md,
# "Control API"). Read from the environment -- the variable omnigent's own CLI
# reads -- and deliberately NOT an option: argv is world-readable via `ps`.
AUTH_TOKEN_ENV = "OMNIGENT_REMOTE_AUTH_TOKEN"


def _headers(**extra: str) -> dict:
    headers = {"Accept": "application/json", **extra}
    token = (os.environ.get(AUTH_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

# One failed run is enough. The whole point of #132 is that the org must not
# need a second failure — let alone a third — before anyone hears about it.
DEFAULT_FAIL_THRESHOLD = 1

# How late `next_run_at` may be before "the scheduler did not fire" is the
# story. The observed fire latency on this box is 3-7s; 15 minutes is a wide
# margin that still catches a wedged scheduler inside one hourly period.
DEFAULT_OVERDUE_GRACE = 900

DEFAULT_RETRY_BASE = 120          # first retry 2 minutes after the failure
DEFAULT_MAX_RETRIES = 2


class ProbeUnknown(Exception):
    """The scheduler could not be consulted. Never a verdict — see exit code 2."""


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError(url) from exc
        if exc.code in (401, 403):
            raise ProbeUnknown(f"HTTP {exc.code} from {url}: the server requires "
                               f"auth; set {AUTH_TOKEN_ENV} in this unit's environment") from exc
        raise ProbeUnknown(f"HTTP {exc.code} from {url}") from exc
    except Exception as exc:  # network, timeout, malformed JSON
        raise ProbeUnknown(f"{type(exc).__name__}: {exc}") from exc


def _post(url: str, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers=_headers(**{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]
    except Exception as exc:
        raise ProbeUnknown(f"{type(exc).__name__}: {exc}") from exc


def find_task(base_url: str, task_id: str | None, name_match: str | None,
              timeout: float) -> dict | None:
    """Resolve the heartbeat task. Returns None only if it is genuinely absent.

    An explicit id is authoritative. Matching by name is the fallback because
    the documented recovery move is to RE-CREATE the heartbeat task, which mints
    a new id — a probe pinned to a dead id would go quiet exactly after the
    incident it exists to report.
    """
    if task_id:
        try:
            return _get_json(f"{base_url}/v1/scheduled-tasks/{task_id}", timeout)
        except FileNotFoundError:
            return None
    listing = _get_json(f"{base_url}/v1/scheduled-tasks", timeout)
    tasks = listing.get("scheduled_tasks")
    if tasks is None:
        raise ProbeUnknown("listing has no 'scheduled_tasks' key")
    needle = (name_match or "").lower()
    for task in tasks:
        if needle in (task.get("name") or "").lower():
            return task
    return None


def _runs(base_url: str, task_id: str, timeout: float) -> list[dict]:
    payload = _get_json(f"{base_url}/v1/scheduled-tasks/{task_id}/runs", timeout)
    runs = payload.get("runs")
    if runs is None:
        raise ProbeUnknown("runs payload has no 'runs' key")
    # Newest first. Do not trust server ordering.
    return sorted(runs, key=lambda r: r.get("scheduled_at") or 0, reverse=True)


def _parse_next_run_at(value) -> float | None:
    """`next_run_at` is an ISO-8601 string here but has been an epoch elsewhere.

    Accept both and return None on anything else. An unparseable value must not
    manufacture an `overdue` verdict out of a formatting change.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            import datetime as dt
            parsed = dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def consecutive_failures(runs: list[dict]) -> tuple[int, list[dict]]:
    """Count failed runs at the HEAD of the history, skipping a live run.

    A currently-`running` newest run is skipped rather than treated as success:
    the run in flight says nothing yet about the ones before it, and swallowing
    the count would hide a failing task for as long as a run happens to be up.
    """
    failed: list[dict] = []
    for run in runs:
        status = (run.get("status") or "").lower()
        if status == "running":
            continue
        if status == "failed":
            failed.append(run)
            continue
        break
    return len(failed), failed


def evaluate(task: dict | None, runs: list[dict], now: float,
             fail_threshold: int, overdue_grace: int) -> tuple[int, dict]:
    if task is None:
        return 3, {
            "status": "verdict",
            "reason_code": "task_missing",
            "detail": ("no scheduled task matches — the org's only wake-up "
                       "mechanism is GONE and nothing will restart it"),
        }

    task_id = task.get("id")
    state = (task.get("state") or "").lower()
    base = {"task_id": task_id, "task_name": task.get("name"), "task_state": state}

    # POSITIVE evidence of intent. Only this path buys silence.
    if state and state != "active":
        return 4, dict(base, status="paused", reason_code="paused",
                       detail=(f"task state is {state!r}, not 'active' — this is a "
                               "deliberate pause, not a failure; staying quiet"))
    if not state:
        # No state field at all is a schema surprise, not a statement of intent.
        raise ProbeUnknown("task has no 'state' field")

    n_failed, failed = consecutive_failures(runs)
    if n_failed >= fail_threshold:
        newest = failed[0]
        return 3, dict(
            base,
            status="verdict",
            reason_code="runs_failing",
            consecutive_failures=n_failed,
            last_error_code=newest.get("error_code"),
            last_failed_scheduled_at=newest.get("scheduled_at"),
            last_failed_run_id=newest.get("id"),
            detail=(f"{n_failed} consecutive scheduled run(s) FAILED "
                    f"(newest error_code={newest.get('error_code')!r}) while the "
                    f"task is still active — the org is not being woken"),
        )

    next_run = _parse_next_run_at(task.get("next_run_at"))
    if next_run is not None:
        late = now - next_run
        if late > overdue_grace:
            return 3, dict(
                base, status="verdict", reason_code="overdue",
                overdue_s=int(late), next_run_at=task.get("next_run_at"),
                detail=(f"next_run_at passed {int(late)}s ago with nothing fired "
                        f"(grace {overdue_grace}s) — the scheduler is wedged"),
            )

    return 0, dict(base, status="healthy", reason_code="healthy",
                   consecutive_failures=n_failed,
                   next_run_at=task.get("next_run_at"),
                   detail="task is active and its recent runs are not failing")


# ------------------------------------------------------------------ retry ---

def _load_retry_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        # A corrupt or missing file must not block a retry, and must not be
        # read as "retries already spent" either. Start clean.
        return {}


def _save_retry_state(path: str, state: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def maybe_retry(base_url: str, task: dict, runs: list[dict], verdict: dict,
                now: float, state_path: str, base_delay: int, max_retries: int,
                timeout: float, period_s: int) -> dict:
    """Re-fire the CURRENT slot's failed run, with backoff and hard caps.

    Returns a dict describing what was decided, always — 'skipped' with a reason
    is a result, not a non-event, and the operator needs to see it in the
    journal to trust that retry is working.
    """
    task_id = task.get("id")
    n_failed, failed = consecutive_failures(runs)
    if not failed:
        return {"attempted": False, "why": "no failed run at the head of the history"}
    newest = failed[0]

    if any((r.get("status") or "").lower() == "running" for r in runs):
        return {"attempted": False, "why": "a run is currently in flight — never double-drive the org"}

    slot = newest.get("scheduled_at")
    if not isinstance(slot, (int, float)):
        return {"attempted": False, "why": "failed run has no usable scheduled_at"}

    # Only the current slot. Re-firing an old hour re-runs stale instructions
    # and tells the operator nothing they can still act on.
    if now - slot > period_s:
        return {"attempted": False,
                "why": f"failed slot is {int((now - slot) / 60)}m old (> one {period_s}s period) — history, not a retry"}

    finished = newest.get("finished_at") or newest.get("fired_at") or slot
    state = _load_retry_state(state_path)
    # KEY ON THE SCHEDULING PERIOD, NOT ON THE RUN'S OWN `scheduled_at`.
    #
    # Found by running this against a real scheduler rather than a fixture: a
    # re-fire creates a NEW run row with a NEW `scheduled_at`, so a key built
    # from `scheduled_at` is a different key every time. The budget reset on
    # every attempt and the cap never bound anything — the only thing left
    # limiting re-fires was the backoff, which also restarted from each new
    # failure. A task failing persistently would have been re-fired on roughly
    # every watchdog tick, forever.
    #
    # The period bucket is stable across re-fires by construction, which is
    # exactly the guarantee the cap needs, and it states the intended policy
    # directly: a bounded number of retries PER SCHEDULING PERIOD. The rule
    # this exists to serve is "a single 529 must not cost the org an hour" —
    # not "retry until it works".
    key = f"{task_id}:period:{int(slot // period_s)}"
    attempts = int(state.get(key, {}).get("attempts", 0))
    if attempts >= max_retries:
        return {"attempted": False, "why": f"retry budget spent for this scheduling period ({attempts}/{max_retries})"}

    delay = base_delay * (2 ** attempts)
    # Backoff runs from the last ATTEMPT when there has been one. Measuring from
    # the newest failure's `finished_at` would restart the clock on every
    # re-fire's own failure, so the exponent grew while the actual wait did not.
    since = state.get(key, {}).get("last_at") if attempts else None
    due = (since if isinstance(since, (int, float)) else finished) + delay
    if now < due:
        return {"attempted": False,
                "why": f"backing off — retry {attempts + 1}/{max_retries} due in {int(due - now)}s"}

    # RESERVE THE ATTEMPT BEFORE THE POST, AND FAIL CLOSED IF IT WILL NOT STICK.
    #
    # The POST is a PRODUCTION side effect: it mints a real run of the org's
    # hourly task. The budget that bounds it lives in a file, so the budget is
    # only real while that file can be written. Recording the attempt AFTER the
    # POST -- the previous shape -- meant every write failure degraded to a
    # `state_persisted:false` field that nothing consumed: the next 10-minute
    # tick loaded zero attempts and minted another run, forever, for as long as
    # the state directory stayed unwritable, full or corrupt. SP12 made the
    # budget KEY stable across re-fires; this makes the budget survive its own
    # DEPENDENCY failing. Both were the same bug wearing different clothes.
    #
    # Ordering it this way also covers the crash between the two operations: if
    # the process dies after the POST, the attempt is already counted.
    #
    # The direction of the failure is the whole point. Unreservable budget means
    # NO retry, not a free one -- a retry that cannot count itself does not get
    # to fire. Losing one legitimate re-fire costs the org an hour, which is the
    # cost #132 already describes and which the alert still reports. Getting it
    # wrong the other way hammers production with no bound at all.
    state[key] = {"attempts": attempts + 1, "last_at": int(now), "last_http": None}
    if not _save_retry_state(state_path, state):
        return {"attempted": False, "state_persisted": False,
                "attempt": attempts + 1, "max_retries": max_retries,
                "why": ("FAIL CLOSED: the retry budget could not be durably reserved at "
                        f"{state_path!r}, so the re-fire was REFUSED. Without a persisted "
                        "attempt count every tick would re-fire this run again, unbounded, "
                        "against production. Fix the state path; the scheduler fault itself "
                        "is still being alerted.")}

    status_code, body = _post(f"{base_url}/v1/scheduled-tasks/{task_id}/run", timeout)
    ok = 200 <= status_code < 300
    # The attempt is ALREADY counted whether or not this succeeded -- a retry
    # that failed to dispatch still consumed it. This second write only enriches
    # the reserved record with the outcome, so it is best-effort by design: if it
    # fails, the reservation above still stands and the cap still binds.
    state[key] = {"attempts": attempts + 1, "last_at": int(now), "last_http": status_code}
    persisted = _save_retry_state(state_path, state)
    return {"attempted": True, "ok": ok, "http_status": status_code,
            "attempt": attempts + 1, "max_retries": max_retries,
            "backoff_s": delay, "state_persisted": True,
            "outcome_recorded": persisted,
            "response": body if not ok else None,
            "why": "re-fired the failed scheduled run" if ok else "re-fire REJECTED by the scheduler"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--task-id", default=os.environ.get("NIGHTSHIFT_HEARTBEAT_TASK_ID") or None)
    parser.add_argument("--name-match", default="heartbeat",
                        help="substring match on the task name when no --task-id is given")
    parser.add_argument("--fail-threshold", type=int, default=DEFAULT_FAIL_THRESHOLD)
    parser.add_argument("--overdue-grace", type=int, default=DEFAULT_OVERDUE_GRACE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--now", type=float, default=None,
                        help="override the clock, epoch seconds (test seam, argv only)")
    parser.add_argument("--retry", action="store_true",
                        help="re-fire a failed run for the current slot, with backoff")
    parser.add_argument("--retry-state", default=os.path.join(
        os.path.expanduser("~"), ".config", "omnigent", "scheduler-retry.json"))
    parser.add_argument("--retry-base-delay", type=int, default=DEFAULT_RETRY_BASE)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--period", type=int, default=3600,
                        help="the task's scheduling period in seconds; bounds which slot may be retried")
    args = parser.parse_args(argv)

    now = args.now if args.now is not None else time.time()

    try:
        task = find_task(args.base_url, args.task_id, args.name_match, args.timeout)
        runs = _runs(args.base_url, task["id"], args.timeout) if task else []
        code, verdict = evaluate(task, runs, now, args.fail_threshold, args.overdue_grace)
    except ProbeUnknown as exc:
        # NOT a verdict. Buys no silence and raises no alarm on its own.
        print(json.dumps({"status": "unknown", "reason_code": "probe_unavailable",
                          "detail": str(exc)}))
        return 2

    if args.retry and code == 3 and verdict.get("reason_code") == "runs_failing":
        try:
            verdict["retry"] = maybe_retry(
                args.base_url, task, runs, verdict, now, args.retry_state,
                args.retry_base_delay, args.max_retries, args.timeout, args.period)
        except ProbeUnknown as exc:
            verdict["retry"] = {"attempted": False, "why": f"retry unavailable: {exc}"}

    print(json.dumps(verdict))
    return code


if __name__ == "__main__":
    sys.exit(main())
