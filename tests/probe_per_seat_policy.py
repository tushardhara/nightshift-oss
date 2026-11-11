#!/usr/bin/env python3
"""
Live probe for #118: does a policy declared on a SEAT (a `sub_agent_name`
inside a bundle) ever reach the policy engine?

The claim under test (#118, found by `security` by code-read only):

    Only ROOT policies load. A seat is not an independently registered
    agent -- it is a `sub_agent_name` inside ONE bundle, and the policy
    engine is built from the root spec's `guardrails.policies` alone,
    with no sub-agent traversal.

Design is the one specified in the issue: register a two-seat bundle where
the ROOT declares NO policy and a SEAT declares `read_only_os`, then attempt
a write from the seat.

    write SUCCEEDS / is ALLOWED  -> claim CONFIRMED, per-seat policy is dead config
    write is DENIED              -> claim REFUTED, report which mechanism denied it

Three bundles are registered so the result is interpretable rather than a
bare negative:

  probe_seatonly  root: NO guardrails      seat `ro_seat`: read_only_os
                  -> THE EXPERIMENT
  probe_rootonly  root: read_only_os       seat `plain_seat`: no guardrails
                  -> POSITIVE CONTROL. If this does not DENY, the harness
                     is broken and the experiment says nothing.
  probe_none      root: NO guardrails      seat `plain_seat`: no guardrails
                  -> NEGATIVE CONTROL. Establishes the ALLOW baseline.

Two enforcement paths are exercised per session, both the ones a real seat's
tool call travels:

  A. POST /sessions/{id}/policies/evaluate  -- the request-phase gate used by
     the native harness hooks (`omnigent.claude_native_hook`).
  B. POST /sessions/{id}/mcp  `tools/call sys_os_write` -- the MCP proxy. The
     server owns TOOL_CALL policy and only then delegates execution to the
     runner. With no runner attached, a DENY is a policy verdict and any
     runner/transport error means policy ALLOWED the write through to
     execution. Both outcomes are unambiguous.

SAFETY. Everything is scratch: its own port (ephemeral, probed free), its own
SQLite DB, its own artifact dir, its own bundles, all under one temp dir that
is removed on exit. The live server is never contacted, restarted, signalled
or re-registered; its pid is read once and asserted against every pid this
script signals. No served bundle or cache file is read or written.

Usage:  python3 tests/probe_per_seat_policy.py [--keep] [--json PATH]

Exit 0 when every control behaved and a verdict was reached; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_BOOT_TIMEOUT = 120.0
HTTP_TIMEOUT = 30.0

# Any pid this script signals must NOT be one of these. Populated at startup
# from the running `omnigent server` processes we did not create.
_PREEXISTING_PIDS: set[int] = set()


# ── bundles ──────────────────────────────────────────────────────────────────

# Executor/os_env blocks mirror `program/config.yaml` (root) and
# `program/agents/security/config.yaml` (seat) so the probe bundles differ
# from the real ones only in which policies they declare and where.
_ROOT_HEADER = """\
spec_version: 1
name: {name}
description: probe bundle for issue 118 -- {desc}
executor:
  type: omnigent
  config:
    harness: claude-sdk
os_env:
  type: caller_process
  cwd: .
  sandbox:
    type: none
"""

_SEAT_HEADER = """\
spec_version: 1
name: {name}
description: probe seat for issue 118 -- {desc}
executor:
  type: omnigent
  config:
    harness: claude-native
os_env:
  type: caller_process
  cwd: .
  sandbox:
    type: none
"""

# Byte-identical to the block `program/agents/security/config.yaml` declares,
# modulo the deny_reason text -- same builtin, same `type: function` shape.
_READ_ONLY_OS = """\
guardrails:
  policies:
    report_only:
      type: function
      function:
        path: omnigent.policies.builtins.orchestration.read_only_os
        arguments:
          deny_reason: >-
            PROBE118 read_only_os fired
"""


def write_bundle(base: Path, name: str, desc: str, root_policy: bool, seat: str,
                 seat_policy: bool) -> Path:
    """Lay out one bundle dir: root config.yaml plus agents/<seat>/config.yaml."""
    root = base / name
    (root / "agents" / seat).mkdir(parents=True, exist_ok=True)
    root_yaml = _ROOT_HEADER.format(name=name, desc=desc)
    if root_policy:
        root_yaml += _READ_ONLY_OS
    (root / "config.yaml").write_text(root_yaml)
    seat_yaml = _SEAT_HEADER.format(name=seat, desc=f"{desc} / seat")
    if seat_policy:
        seat_yaml += _READ_ONLY_OS
    (root / "agents" / seat / "config.yaml").write_text(seat_yaml)
    return root


# ── http ─────────────────────────────────────────────────────────────────────

class Http:
    """Tiny JSON client pinned to the scratch server's base URL."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def request(self, method: str, path: str, body: object | None = None,
                timeout: float = HTTP_TIMEOUT) -> tuple[int, object]:
        url = f"{self.base}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw

    def get(self, path: str) -> tuple[int, object]:
        return self.request("GET", path)

    def get_root(self, path: str) -> tuple[int, object]:
        """``/health`` is mounted at the app root, not under the ``/v1`` prefix."""
        saved, self.base = self.base, self.base.rsplit("/v1", 1)[0]
        try:
            return self.request("GET", path, timeout=5.0)
        finally:
            self.base = saved

    def post(self, path: str, body: object) -> tuple[int, object]:
        return self.request("POST", path, body)


def free_port() -> int:
    """Bind :0, read the port, close. Racy in theory, fine for a scratch box."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ── server lifecycle ─────────────────────────────────────────────────────────

def snapshot_live_servers() -> set[int]:
    """Record every omnigent server pid that exists BEFORE we start ours."""
    out = subprocess.run(
        ["pgrep", "-f", "omnigent server"], capture_output=True, text=True, check=False
    ).stdout
    return {int(p) for p in out.split() if p.strip().isdigit()}


def start_server(bundle_dirs: list[Path], root: Path, port: int) -> subprocess.Popen:
    db = root / "probe.db"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    log = open(root / "server.log", "wb")
    argv = [
        shutil.which("omnigent") or "omnigent",
        "server",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--database-uri", f"sqlite:///{db}",
        "--artifact-location", str(artifacts),
        "--no-open",
    ]
    for d in bundle_dirs:
        argv += ["--agent", str(d)]
    env = dict(os.environ)
    # Keep the scratch server off any ambient config that points at live state.
    env.pop("OMNIGENT_DATABASE_URI", None)
    env.pop("NIGHTSHIFT_CTL_DB", None)
    env["OMNIGENT_ACCOUNTS_AUTO_OPEN"] = "0"
    proc = subprocess.Popen(
        argv, stdout=log, stderr=subprocess.STDOUT, cwd=str(root),
        env=env, start_new_session=True,
    )
    return proc


def wait_healthy(http: Http, proc: subprocess.Popen, deadline: float) -> None:
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"scratch server exited early rc={proc.returncode}")
        try:
            status, _ = http.get_root("/health")
            if status == 200:
                return
            last = f"status={status}"
        except Exception as exc:  # noqa: BLE001 - boot races are expected
            last = repr(exc)
        time.sleep(0.5)
    raise RuntimeError(f"scratch server never became healthy: {last}")


def stop_server(proc: subprocess.Popen) -> None:
    """Signal ONLY our own process group, and only after re-checking the pid."""
    if proc.poll() is not None:
        return
    assert proc.pid not in _PREEXISTING_PIDS, (
        f"refusing to signal pid {proc.pid}: it pre-existed this script"
    )
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=10)


# ── probe steps ──────────────────────────────────────────────────────────────

def find_agent_id(http: Http, name: str) -> str:
    status, body = http.get("/agents")
    if status != 200:
        raise RuntimeError(f"GET /agents -> {status}: {body!r}")
    # PaginatedList serialises its page under ``data``.
    items = body.get("data") or body.get("items") or [] if isinstance(body, dict) else body
    for a in items or []:
        if a.get("name") == name:
            return a["id"]
    raise RuntimeError(f"agent {name!r} not registered; got {[a.get('name') for a in items or []]}")


def create_session(http: Http, agent_id: str, title: str,
                   parent: str | None = None, seat: str | None = None) -> str:
    body: dict[str, object] = {"agent_id": agent_id, "title": title}
    if parent:
        body["parent_session_id"] = parent
    if seat:
        body["sub_agent_name"] = seat
    status, resp = http.post("/sessions", body)
    if status not in (200, 201):
        raise RuntimeError(f"POST /sessions {body} -> {status}: {resp!r}")
    return resp["id"]


def evaluate_write(http: Http, session_id: str, path: str) -> dict:
    """Path A: the request-phase policy gate the native harness hooks call."""
    # ``event.type`` is the wire phase name, and ``event.data`` must be an
    # object carrying the tool ``name`` (routes_hooks.py rejects anything else
    # rather than gating an unnamed call).
    body = {
        "event": {
            "type": "PHASE_TOOL_CALL",
            "data": {
                "name": "sys_os_write",
                "arguments": {"path": path, "content": "PROBE118"},
            },
        }
    }
    status, resp = http.post(f"/sessions/{session_id}/policies/evaluate", body)
    return {"http_status": status, "response": resp}


def mcp_write(http: Http, session_id: str, path: str) -> dict:
    """Path B: a real `tools/call sys_os_write` through the MCP proxy."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "sys_os_write",
            "arguments": {"path": path, "content": "PROBE118"},
        },
    }
    status, resp = http.post(f"/sessions/{session_id}/mcp", body)
    return {"http_status": status, "response": resp}


def omnigent_python() -> str | None:
    """
    The interpreter that has ``omnigent`` importable.

    ``omnigent`` is installed as a uv tool, so the ambient ``python3`` cannot
    import it. Read the launcher's shebang rather than hard-coding a path.
    """
    launcher = shutil.which("omnigent")
    if not launcher:
        return None
    try:
        first = Path(launcher).read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if first.startswith("#!"):
        candidate = first[2:].strip().split()[0]
        if Path(candidate).exists():
            return candidate
    return None


def introspect_specs(bundles: list[Path]) -> dict:
    """
    Load each probe bundle with the PRODUCTION parser and report where the
    policies landed in the resulting object graph.

    This distinguishes the two ways a per-seat policy could fail to apply:

      * the seat's ``guardrails:`` block never parses / never reaches the
        server -- a registration or schema failure, and
      * the block parses fine, is present on ``spec.sub_agents[i].guardrails``
        in the very object the engine holds, and is simply never traversed.

    The second is the claim in #118. Uses ``omnigent.spec.load`` -- the same
    entry point ``cli._preregister_agent`` and ``AgentCache.load`` call.
    """
    interp = omnigent_python()
    if interp is None:
        return {"available": False, "reason": "could not locate the omnigent interpreter"}
    snippet = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from omnigent.spec import load as load_spec\n"
        "def names(s):\n"
        "    g=getattr(s,'guardrails',None)\n"
        "    return sorted(p.name for p in (g.policies or [])) if g else []\n"
        "out={}\n"
        "for b in sys.argv[1:]:\n"
        "    s=load_spec(Path(b))\n"
        "    out[b.rsplit('/',1)[-1]]={\n"
        "        'root_policies': names(s),\n"
        "        'seat_policies': {sub.name: names(sub) for sub in s.sub_agents},\n"
        "    }\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run(
        [interp, "-c", snippet, *[str(b) for b in bundles]],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return {"available": False, "reason": proc.stderr[-2000:]}
    return {"available": True, "interpreter": interp, "bundles": json.loads(proc.stdout)}


def classify(evaluate: dict, mcp: dict, target: Path) -> str:
    """DENIED / ALLOWED / INCONCLUSIVE for one session."""
    resp = evaluate.get("response")
    result = resp.get("result") if isinstance(resp, dict) else None
    m = mcp.get("response")
    err = m.get("error") if isinstance(m, dict) else None
    msg = (err or {}).get("message", "") if isinstance(err, dict) else ""

    # Either enforcement path denying is a denial; they are not required to
    # agree, and a denial anywhere refutes the claim.
    if result == "POLICY_ACTION_DENY" or "Denied by policy" in msg:
        return "DENIED"
    if target.exists():
        return "ALLOWED"
    # No runner is attached, so an ALLOWED write ends in a transport error
    # from the delegation step the server reaches only AFTER the TOOL_CALL
    # policy has passed. That is execution failing, not a control firing.
    mcp_passed_policy = bool(msg) and "No runner" in msg
    evaluate_passed_policy = result in ("POLICY_ACTION_ALLOW", "POLICY_ACTION_UNSPECIFIED")
    if mcp_passed_policy or evaluate_passed_policy:
        return "ALLOWED"
    # Neither path produced a verdict we understand. Say so rather than
    # scoring an unreadable result as either answer.
    return "INCONCLUSIVE"


def run(root: Path, keep: bool) -> dict:
    port = free_port()
    base = f"http://127.0.0.1:{port}/v1"
    bundles = [
        write_bundle(root / "bundles", "probe_seatonly", "root none / seat read_only_os",
                     root_policy=False, seat="ro_seat", seat_policy=True),
        write_bundle(root / "bundles", "probe_rootonly", "root read_only_os / seat none",
                     root_policy=True, seat="plain_seat", seat_policy=False),
        write_bundle(root / "bundles", "probe_none", "root none / seat none",
                     root_policy=False, seat="plain_seat", seat_policy=False),
    ]
    http = Http(base)
    proc = start_server(bundles, root, port)
    report: dict = {
        "port": port,
        "scratch_root": str(root),
        "scratch_db": f"sqlite:///{root / 'probe.db'}",
        "server_pid": proc.pid,
        "live_pids_untouched": sorted(_PREEXISTING_PIDS),
        "cases": {},
    }
    try:
        wait_healthy(http, proc, time.time() + SERVER_BOOT_TIMEOUT)
        writes = root / "writes"
        writes.mkdir(parents=True, exist_ok=True)

        cases = [
            ("EXPERIMENT_seat_policy", "probe_seatonly", "ro_seat"),
            ("CONTROL_POS_root_policy", "probe_rootonly", "plain_seat"),
            ("CONTROL_NEG_no_policy", "probe_none", "plain_seat"),
            # The root session of the seat-policy bundle: nothing is declared
            # at root there, so this must ALLOW under either hypothesis. It
            # separates "the seat's policy did not load" from "the bundle's
            # policies did not load at all".
            ("CONTROL_seatonly_root_session", "probe_seatonly", None),
        ]
        for label, bundle, seat in cases:
            agent_id = find_agent_id(http, bundle)
            parent_id = create_session(http, agent_id, f"probe118 {label} parent")
            if seat is None:
                sid = parent_id
            else:
                sid = create_session(http, agent_id, f"probe118 {label}",
                                     parent=parent_id, seat=seat)
            target = writes / f"{label}.txt"
            ev = evaluate_write(http, sid, str(target))
            mc = mcp_write(http, sid, str(target))
            report["cases"][label] = {
                "bundle": bundle,
                "seat": seat,
                "session_id": sid,
                "evaluate": ev,
                "mcp_tools_call": mc,
                "file_written": target.exists(),
                "outcome": classify(ev, mc, target),
            }
        report["spec_introspection"] = introspect_specs(bundles)
    finally:
        tail = (root / "server.log")
        if tail.exists():
            report["server_log_tail"] = tail.read_text(errors="replace")[-4000:]
        stop_server(proc)

    outcomes = {k: v["outcome"] for k, v in report["cases"].items()}
    pos = outcomes.get("CONTROL_POS_root_policy")
    neg = outcomes.get("CONTROL_NEG_no_policy")
    exp = outcomes.get("EXPERIMENT_seat_policy")
    report["controls_valid"] = (pos == "DENIED" and neg == "ALLOWED")
    if not report["controls_valid"]:
        report["verdict"] = "INVALID"
        report["verdict_reason"] = (
            f"controls did not behave: positive={pos!r} (want DENIED), "
            f"negative={neg!r} (want ALLOWED). The experiment says nothing."
        )
    elif exp == "ALLOWED":
        report["verdict"] = "CONFIRMED"
        report["verdict_reason"] = (
            "A seat declaring read_only_os was ALLOWED to write while an "
            "identical policy at root DENIED. Per-seat policy is dead config."
        )
    elif exp == "DENIED":
        report["verdict"] = "REFUTED"
        report["verdict_reason"] = "The seat's own read_only_os denied the write."
    else:
        report["verdict"] = "INCONCLUSIVE"
        report["verdict_reason"] = f"experiment outcome={exp!r}"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true", help="keep the scratch dir")
    ap.add_argument("--json", dest="json_path", help="write the full report here")
    args = ap.parse_args()

    global _PREEXISTING_PIDS
    _PREEXISTING_PIDS = snapshot_live_servers()
    print(f"pre-existing omnigent server pids (never signalled): "
          f"{sorted(_PREEXISTING_PIDS) or 'none'}", file=sys.stderr)

    root = Path(tempfile.mkdtemp(prefix="probe118-"))
    try:
        report = run(root, args.keep)
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json_path:
        Path(args.json_path).write_text(text + "\n")
    print(text)
    print(f"\nVERDICT: {report['verdict']} -- {report['verdict_reason']}", file=sys.stderr)
    return 0 if report["verdict"] in ("CONFIRMED", "REFUTED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
