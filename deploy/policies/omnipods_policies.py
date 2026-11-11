"""Custom Omnigent policies for the crew org — the machine side of the budget.

Loaded by the Omnigent policy layer via ``importlib`` (``type: function``,
``path: omnipods_policies.<factory>``), so this directory must be on the
server's PYTHONPATH (bootstrap adds ``__TEAMS_DIR__/deploy/policies`` to
``server.env``; local runs export it by hand — preflight checks both).

Contract (Service-Policies-V0, same as omnigent.policies.builtins.*):
  factory(**yaml_arguments) -> evaluator(event) -> {"result": ..., "reason": ...}
  event = {"type": "tool_call", "data": {"name": ..., "arguments": {...}}}

Why this exists: `.omni/capacity.json` used to be a number the orchestrators
were TOLD to respect. These policies make the control plane the arbiter —
LLMs decide what should happen, nightshift-ctl decides what is allowed to happen,
and a dispatch that skipped the semaphore is DENIED at the tool-call layer,
not caught in a retro.

stdlib only. The semaphore FAILS CLOSED: a missing database or a SQLite
error denies the dispatch — the safety mechanism must not evaporate exactly
when infrastructure is failing, and a denial is always the safe direction
here (the lead queues and retries; nothing is lost). The one escape hatch is
explicit and operator-owned: NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1 in the server
environment allows dispatches without a control plane (toy/local runs only).

ADR-0085's `external_repo_merge_fence` is the one exception to that escape
hatch pattern: it FAILS CLOSED with NO fail-open variable at all. A denied
worker dispatch above is requeued and retried — nothing is lost by denying
it unnecessarily. A wrongly-ALLOWed `gh pr merge` against a repository this
deployment does not own writes to a customer's default branch and cannot be
recalled the way a retried dispatch can, so there is deliberately no
`NIGHTSHIFT_MERGE_FENCE_FAIL_OPEN` for an operator to reach for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable, Collection



def _load_attested_controller() -> tuple[Any, str, str]:
    """Load the release sibling controller without creating a second copy.

    nightshift_publish executes ``bin/nightshift-ctl`` as isolated source on
    stdin, so that file must remain standalone.  The long-lived root policy can
    load the same reviewed bytes from its sealed release root and reuse the
    controller's readiness/roster authority.
    """
    path = Path(__file__).resolve().parents[2] / "bin" / "nightshift-ctl"
    if not hasattr(os, "O_CLOEXEC") or not hasattr(os, "O_NOFOLLOW"):
        raise ImportError("platform cannot safely open the admission controller")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ImportError(f"cannot open admission controller: {path}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ImportError(f"admission controller is not a regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        source = b"".join(chunks)
        after = os.fstat(fd)
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size == len(source)
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
        )
        if not stable:
            raise ImportError("admission controller changed while being read")
    finally:
        os.close(fd)

    canonical_path = str(path)
    digest = hashlib.sha256(source).hexdigest()
    module = types.ModuleType("_nightshift_admission_controller")
    module.__file__ = canonical_path
    code = compile(source, canonical_path, "exec", dont_inherit=True)
    exec(code, module.__dict__)
    return module, canonical_path, digest


_ADMISSION, ADMISSION_CONTROLLER_PATH, ADMISSION_CONTROLLER_SHA256 = (
    _load_attested_controller()
)


def _load_authority_module() -> Any:
    """Import ``bin/nightshift_authority.py`` as an ordinary module.

    Deliberately NOT `_load_attested_controller`'s exec-and-verify path
    above. That loader exists because ``bin/nightshift-ctl`` is ALSO
    executed as isolated source elsewhere (``nightshift_publish`` pipes it
    in on stdin) and must therefore stay a standalone file with no import
    side effects of its own. ``nightshift_authority.py`` carries no such
    dual-execution constraint — it is reviewed through the same
    pull-request path as this file — so a plain import is the right amount
    of machinery, and it means there is exactly ONE copy of
    ``classify_repository``/``scope_from_env`` in this codebase, not a
    second one hand-maintained here.
    """
    bin_dir = str(Path(__file__).resolve().parents[2] / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    import nightshift_authority
    return nightshift_authority


_AUTHORITY = _load_authority_module()
COUNTED_AGENTS = _ADMISSION.COUNTED_AGENTS
EXEMPT_AGENTS = _ADMISSION.EXEMPT_AGENTS
ONCALL_AGENTS = _ADMISSION.ONCALL_AGENTS
task_readiness_snapshot = _ADMISSION._task_readiness_snapshot
canonical_dispatch_digest = _ADMISSION.canonical_dispatch_digest
consume_program_action_permit = _ADMISSION.consume_program_action_permit


def _load_review_dispatch_gate() -> Any:
    """Import ``bin/nightshift_review_dispatch_gate.py`` (issue #720).

    Same plain-import idiom, and for the same reason, as
    ``_load_authority_module`` above: one definition of which seats carry a
    review domain, not a second copy hand-maintained here that drifts from
    the one the mint enforces.

    Deliberately NOT wrapped in a try/except. A guarded import would turn a
    broken gate into a fence that quietly stops checking — an inert control
    that still reports ALLOW, which is the precise failure this issue's epic
    exists to name. Failing at policy load is loud and already understood.
    """
    bin_dir = str(Path(__file__).resolve().parents[2] / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    import nightshift_review_dispatch_gate
    return nightshift_review_dispatch_gate


_REVIEW_GATE = _load_review_dispatch_gate()
review_gate_for_agent = _REVIEW_GATE.gate_for_agent
expire_stale = _ADMISSION.expire_stale

# ADR-0044 Decision §3 — the Outcome (a) target set: dispatch to a Program
# direct-child seat. Derived from the SAME attested roster worker_semaphore
# already trusts (EXEMPT_AGENTS/ONCALL_AGENTS), not a second hand-copied
# list that could drift from it. `pm`/`beta_pm` are excluded: they are a
# CREW's own direct child (crew_alpha dispatches pm, not Program), never a
# Program direct-child seat, and the ADR's own Outcome text does not name
# them.
PROGRAM_DIRECT_CHILDREN = (frozenset(EXEMPT_AGENTS) - {"pm", "beta_pm"}) | frozenset(ONCALL_AGENTS)


_Json = dict  # matches the builtins' loose typing

_ALLOW = {"result": "ALLOW", "reason": ""}


def _deny(reason: str) -> _Json:
    return {"result": "DENY", "reason": reason}


# One definition of "a shell tool call" for every shell fence in this
# directory: the casefolded omnigent shell-tool set, `Bash` included.
_POLICY_DIR = str(Path(__file__).resolve().parent)
if _POLICY_DIR not in sys.path:
    sys.path.insert(0, _POLICY_DIR)
from nightshift_issue_fence import (  # noqa: E402
    _KNOWN_GH_SUBCOMMANDS,
    _READ_METHODS,
    _Unparseable,
    _api_segments,
    _consume_leading_flags,
    _full_token_stream,
    _gh_call_argvs,
    _graphql_query_unresolvable,
    _is_dynamic,
    _parse_gh_api,
    _tool_call,
    shell_tool_names,
)


def _process_start_ticks() -> str | None:
    """Return this process epoch from procfs; ``None`` can never prove it."""
    try:
        # Field 22 follows a parenthesised comm which may contain spaces.
        tail = Path("/proc/self/stat").read_text().rsplit(") ", 1)[1]
        return tail.split()[19]
    except (OSError, IndexError):
        return None


def release_provenance_canary(
    *,
    shell_tools: Collection[str] = (),
    command: str = "nightshift-release-provenance-canary",
) -> Callable[[_Json], _Json]:
    """Deny one inert command and expose the policy code that decided it.

    Normal tool calls are unaffected. Exact-SHA postflight sends this one
    command through the serving Program session and verifies that the denial's
    module path, module digest, PID, and process epoch all belong to the sealed
    release. If the policy is absent or shadowed, the negative control must not
    be mistaken for proof.
    """
    module_path = str(Path(__file__).resolve())
    try:
        module_sha256 = hashlib.sha256(Path(module_path).read_bytes()).hexdigest()
    except OSError:
        module_sha256 = "unavailable"
    provenance = {
        "kind": "nightshift_release_provenance",
        "module": module_path,
        "module_sha256": module_sha256,
        "admission_controller": ADMISSION_CONTROLLER_PATH,
        "admission_controller_sha256": ADMISSION_CONTROLLER_SHA256,
        "pid": os.getpid(),
        "process_start_ticks": _process_start_ticks(),
    }
    tools = shell_tool_names(shell_tools)

    def _evaluate(event: _Json) -> _Json:
        call = _tool_call(event, tools)
        if call is None or call.get("command") != command:
            return _ALLOW
        return _deny(json.dumps(provenance, sort_keys=True, separators=(",", ":")))

    return _evaluate


def worker_semaphore(
    *,
    db: str = ".omni/control.db",
    dispatch_tools: tuple[str, ...] = ("sys_session_send", "sys_session_create"),
    exempt_agents: tuple[str, ...] | None = None,
) -> Callable[[_Json], _Json]:
    """Root worker-admission gate from ADR-0033, amended by ADR-0062 (#471).

    ADR-0062: ``sys_session_send``'s own declared schema
    (``additionalProperties: false`` on ``args``) permits only ``input``,
    ``purpose``, ``model``, ``cost_budget``, ``file_ids`` -- there is no call
    shape that can also carry ``args.slot_id``/``args.task_id``/
    ``args.generation``, so this policy no longer reads them from the
    caller. Admission is instead derived, inside the same ``BEGIN IMMEDIATE``
    transaction, from a control-plane lookup keyed on the exact tuple the
    tool schema DOES mandate: ``(kind, expected_agent, expected_title,
    expected_purpose)``. Zero matches denies (unchanged "slot does not
    exist" case, reached via a different query); more than one match denies
    too -- ambiguity is never resolved by a heuristic, and is provably
    unreachable once ``bin/nightshift-ctl``'s
    ``one_active_qa_review_continuation`` / ``one_undispatched_dispatch_identity``
    partial unique indexes are in place, but the check stays as fail-closed
    defense in depth. Every other downstream check (holder match,
    phase/continuation_kind branching, the one-use compare-and-set,
    ``task_admissions`` insert) is unchanged from ADR-0033.
    """
    counted = set(dispatch_tools)
    exempt = set(EXEMPT_AGENTS if exempt_agents is None else exempt_agents)

    def _fail_open() -> bool:
        return os.environ.get("NIGHTSHIFT_SEMAPHORE_FAIL_OPEN") == "1"

    def _evaluate(event: _Json) -> _Json:
        if not isinstance(event, dict) or event.get("type") != "tool_call":
            return _ALLOW
        data = event.get("data")
        if not isinstance(data, dict) or data.get("name") not in counted:
            return _ALLOW
        tool_name = data.get("name")
        call = data.get("arguments")
        if not isinstance(call, dict):
            return _deny("worker dispatch arguments must be an object")
        if tool_name == "sys_session_create":
            return _deny("sys_session_create is not an authorized worker dispatch form")
        if call.get("session_id") is not None:
            return _deny("opaque session_id sends cannot prove the worker target")
        agent = call.get("agent")
        if not isinstance(agent, str) or not agent:
            return _deny("named sys_session_send requires a non-empty agent")
        if agent in exempt:
            return _ALLOW
        args = call.get("args")
        if not isinstance(args, dict):
            return _deny("worker dispatch args must be an object")
        title = call.get("title")
        is_oncall = agent in ONCALL_AGENTS
        if agent not in COUNTED_AGENTS and not is_oncall:
            return _deny(f"unknown worker target '{agent}'")
        purpose = args.get("purpose")
        if not isinstance(title, str) or not title:
            return _deny("worker dispatch requires a non-empty title")
        if not isinstance(purpose, str) or not purpose:
            return _deny("worker dispatch requires a non-empty purpose")
        slot_kind = "oncall" if is_oncall else "normal"
        db_path = os.path.expanduser(db)
        if not os.path.exists(db_path):
            if _fail_open():
                return _ALLOW
            return _deny(
                f"control plane not initialised ({db} missing) — the worker "
                "budget cannot be verified, so the dispatch is denied. Run "
                "bin/nightshift-init.sh (or set NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1 "
                "in the server environment for a deliberately unmanaged run)."
            )
        now = time.time()
        try:
            cx = sqlite3.connect(db_path, timeout=5, isolation_level=None)
            cx.row_factory = sqlite3.Row
            try:
                cx.execute("PRAGMA busy_timeout=5000")
                cx.execute("BEGIN IMMEDIATE")
                cx.execute(
                    "UPDATE task_claims SET state='expired',released_at=? "
                    "WHERE state='active' AND expires_at IS NOT NULL "
                    "AND expires_at < ?", (now, now),
                )
                dead_slots = [item["id"] for item in cx.execute(
                    "SELECT id FROM worker_slots WHERE state='active' "
                    "AND expires_at IS NOT NULL AND expires_at < ?", (now,)
                ).fetchall()]
                if dead_slots:
                    marks = ",".join("?" for _ in dead_slots)
                    cx.execute(
                        f"UPDATE worker_slots SET state='reaped',released_at=? "
                        f"WHERE id IN ({marks})", [now] + dead_slots,
                    )
                    cx.execute(
                        f"UPDATE agent_runs SET state='dead',ended_at=? "
                        f"WHERE state='running' AND slot_id IN ({marks})",
                        [now] + dead_slots,
                    )
                # ADR-0062 (#471): derive slot_id/task_id/generation from the
                # control plane instead of trusting caller-supplied values --
                # the tool schema cannot carry them at all (see docstring).
                # Zero rows: unchanged "slot does not exist" case, reached
                # via a different query. More than one row: ambiguous,
                # denied -- never resolved by a heuristic (see class comment
                # above on why this is provably unreachable post-migration).
                rows = cx.execute(
                    "SELECT * FROM worker_slots WHERE state='active' AND "
                    "dispatched_at IS NULL AND kind=? AND expected_agent=? "
                    "AND expected_title=? AND expected_purpose=?",
                    (slot_kind, agent, title, purpose),
                ).fetchall()
                if not rows:
                    raise ValueError(
                        f"no active undispatched slot matches (kind={slot_kind!r}, "
                        f"agent={agent!r}, title={title!r}, purpose={purpose!r})"
                    )
                if len(rows) > 1:
                    raise ValueError(
                        f"ambiguous dispatch identity: {len(rows)} active "
                        f"undispatched slots match (kind={slot_kind!r}, "
                        f"agent={agent!r}, title={title!r}, purpose={purpose!r})"
                    )
                row = rows[0]
                slot = row["id"]
                task_id = row["task_id"]
                generation = row["generation"]
                if row["expires_at"] is not None and row["expires_at"] <= now:
                    raise ValueError(f"slot {slot} is stale, released, or already used")
                holder = row["holder"] or ""
                if not (holder == agent or holder.endswith("/" + agent)):
                    raise ValueError(f"slot {slot} is bound to another holder")
                if is_oncall:
                    # kind/expected_agent/expected_title/expected_purpose are
                    # already exact by construction -- they were the WHERE
                    # clause above; ADR-0033's oncall-kind requirement is
                    # enforced by the query, not re-asserted here.
                    cur = cx.execute(
                        "UPDATE worker_slots SET dispatched_at=?,bound_agent=?,"
                        "bound_title=? WHERE id=? AND state='active' AND "
                        "dispatched_at IS NULL",
                        (now, agent, title, slot),
                    )
                    if cur.rowcount != 1:
                        raise ValueError("oncall slot lost its one-use race")
                    cx.execute("COMMIT")
                    return _ALLOW

                crew, role = COUNTED_AGENTS[agent]
                # agent/title/purpose are exact by construction (the WHERE
                # clause above); kind/crew/phase are the only facts left to
                # recheck against the classified slot.
                if row["kind"] != "normal" or row["crew"] != crew:
                    raise ValueError("dispatch tuple does not match the classified slot")
                if row["phase"] not in {"planning", "new", "continuation"}:
                    raise ValueError("dispatch tuple does not match the classified slot")
                current = cx.execute(
                    "SELECT value FROM meta WHERE key='current_run_id'"
                ).fetchone()
                if not current or not current["value"] or row["run_id"] != current["value"]:
                    raise ValueError("slot belongs to a different or unknown Program run")
                task = cx.execute(
                    "SELECT * FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                admission = cx.execute(
                    "SELECT * FROM task_admissions WHERE task_id=?", (task_id,)
                ).fetchone()
                if task is None:
                    raise ValueError("task no longer exists")

                if row["phase"] == "planning":
                    if (role not in {"engineer", "qa"}
                            or purpose not in {"explore", "search"}
                            or task["crew"] != crew
                            or task["state"] != "pending" or admission is not None):
                        raise ValueError("PLANNING facts no longer match")
                    if cx.execute(
                        "SELECT 1 FROM worker_slots WHERE task_id=? AND "
                        "state='active' AND phase='new'", (task_id,)
                    ).fetchone():
                        raise ValueError("PLANNING conflicts with active NEW")
                elif row["phase"] == "new":
                    if (role != "engineer" or purpose != "implement"
                            or task["crew"] != crew
                            or task["agent"] != agent or task["title"] != title
                            or task["state"] != "pending" or task["pr_url"]
                            or admission is not None):
                        raise ValueError("NEW task identity/state no longer match")
                    if cx.execute(
                        "SELECT 1 FROM worker_slots WHERE task_id=? AND "
                        "state='active' AND phase='planning'", (task_id,)
                    ).fetchone():
                        raise ValueError("NEW waits for active PLANNING")
                    verdict = task_readiness_snapshot(cx).get(task_id)
                    if not verdict or not verdict["dispatchable"]:
                        reason = (verdict or {}).get("reason_code", "unknown_task")
                        raise ValueError(f"NEW task is not READY: {reason}")
                else:
                    if task["state"] not in {"building", "ready", "fixing"}:
                        raise ValueError("continuation task state is not allowed")
                    # security-530 round 2 (MERGE_BLOCKER, PR #530): a
                    # task_admissions row can be forged stale relative to the
                    # task's CURRENT crew via three separate, individually
                    # legitimate `task upsert` calls (--crew shared-fn,
                    # --seed-shared-admission, --crew back) -- round 1 only
                    # closed the single-call TOCTOU shape. This is the ROOT
                    # admission gate (ADR-0033): reject any continuation
                    # whose seeded/admitted crew no longer matches the task
                    # row it is being used against, for every continuation
                    # kind (qa/review/implementer) uniformly, not just the
                    # implementer branch that already happened to check it.
                    # Legitimate shared-function review/QA continuation is
                    # unaffected -- admission["crew"] and task["crew"] are
                    # the SAME shared-fn crew there and never diverge.
                    if admission is None or admission["crew"] != task["crew"]:
                        raise ValueError(
                            "continuation admission crew does not match "
                            "the task's current crew"
                        )
                    kind = row["continuation_kind"]
                    if kind == "qa":
                        valid = (role == "qa" and purpose == "implement"
                                 and task["crew"] == crew and task["pr_url"])
                    elif kind == "review":
                        # Issue #724 / ADR-0092: the mirror of nightshift-ctl's
                        # own rule, and it has to be here or the fence re-denies
                        # at dispatch exactly what slot-acquire allowed -- the
                        # reviewer would hold a slot it could never use. Before
                        # a PR exists, the implementer's live claim is the
                        # evidence the PR was standing in for (ADR-0033 needed a
                        # substitute precisely because this continuation kind has
                        # no claim of its own). Re-derived here rather than
                        # trusted from the slot row, because that is what makes
                        # it a recheck: the claim can lapse between acquiring the
                        # slot and using it, and then the build is no longer in
                        # flight.
                        valid = role == "reviewer" and purpose == "review" and (
                            bool(task["pr_url"]) or cx.execute(
                                # Bound exactly as slot-acquire binds it --
                                # this task's implementer, this run, not
                                # predating the admission. An unbound active
                                # row would also admit a claim left over from
                                # a previous run.
                                "SELECT 1 FROM task_claims c "
                                "JOIN task_admissions a ON a.task_id=c.task_id"
                                " WHERE c.task_id=? AND c.state='active'"
                                "   AND c.agent=? AND c.run_id=?"
                                "   AND c.acquired_at >= a.admitted_at",
                                (task_id, task["agent"], row["run_id"])
                            ).fetchone() is not None)
                    elif kind == "implementer":
                        valid = (role == "engineer" and purpose == "implement"
                                 and admission is not None
                                 and admission["crew"] == crew
                                 and admission["agent"] == agent
                                 and admission["title"] == title)
                        claim = cx.execute(
                            "SELECT * FROM task_claims WHERE id=?",
                            (row["claim_id"],),
                        ).fetchone()
                        valid = bool(valid and claim
                                     and claim["state"] == "active"
                                     and claim["task_id"] == task_id
                                     and claim["agent"] == agent
                                     and claim["title"] == title
                                     and claim["generation"] == row["generation"]
                                     and claim["run_id"] == row["run_id"]
                                     and claim["acquired_at"] >= admission["admitted_at"]
                                     and (claim["expires_at"] is None
                                          or claim["expires_at"] > now))
                    else:
                        valid = False
                    if admission is None or not valid:
                        raise ValueError("continuation evidence no longer matches")

                    # Issue #216 (ADR-0032 + ADR-0045): recheck at actual
                    # dispatch time via task_readiness_snapshot -- the same
                    # function object bin/nightshift-ctl's own
                    # cmd_slot_acquire uses (see the module-level alias
                    # above), keyed on reason_code=="budget_expired"
                    # EXACTLY, never on `dispatchable` generically (AC9:
                    # every continuation candidate already has
                    # dispatchable=False). Closes the TOCTOU window where a
                    # slot is acquired while the budget is still live but
                    # expires before dispatch actually consumes it.
                    verdict = task_readiness_snapshot(cx).get(task_id)
                    if verdict and verdict["reason_code"] == "budget_expired":
                        raise ValueError(
                            "continuation denied: task budget_seconds has expired"
                        )

                cur = cx.execute(
                    "UPDATE worker_slots SET dispatched_at=?,bound_agent=?,"
                    "bound_title=? WHERE id=? AND state='active' AND "
                    "dispatched_at IS NULL AND generation=?",
                    (now, agent, title, slot, generation),
                )
                if cur.rowcount != 1:
                    raise ValueError("slot lost its one-use race")
                if row["phase"] == "new":
                    cx.execute(
                        "INSERT INTO task_admissions("
                        "task_id,new_slot_id,crew,agent,title,admitted_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (task_id, slot, crew, agent, title, now),
                    )
                    changed = cx.execute(
                        "UPDATE tasks SET state='building',updated_at=? "
                        "WHERE id=? AND state='pending'", (now, task_id),
                    )
                    if changed.rowcount != 1:
                        raise ValueError("NEW task lost its pending-state race")
                    # Issue #214 / ADR-0043 (amended, staff MERGE_BLOCKER):
                    # set exactly once, in the SAME transaction as the real
                    # admission write above -- this is the ONLY place
                    # ADR-0033 recognises as genuine admission, unlike the
                    # mutable `phase='new'` reservation in `cmd_slot_acquire`
                    # (which can expire or be released unconsumed and must
                    # never be allowed to pin this value). The
                    # `WHERE started_at IS NULL` guard makes exactly-once a
                    # property of this statement, not of "a task is only
                    # ever admitted once" staying true forever -- see
                    # `test_task_started_at.py`'s re-admission-after-reset
                    # regression test.
                    cx.execute(
                        "UPDATE tasks SET started_at=? WHERE id=? "
                        "AND started_at IS NULL",
                        (now, task_id),
                    )
                cx.execute("COMMIT")
                return _ALLOW
            except ValueError as exc:
                cx.execute("ROLLBACK")
                return _deny(str(exc))
            finally:
                cx.close()
        except sqlite3.Error as e:
            if _fail_open():
                return _ALLOW
            return _deny(
                f"control plane unavailable ({e.__class__.__name__}) — "
                "denying rather than dispatching unbudgeted; retry after "
                "`nightshift-ctl selftest` passes.")

    return _evaluate


def program_authority_fence(
    *,
    db: str = ".omni/control.db",
    dispatch_tools: tuple[str, ...] = ("sys_session_send", "sys_session_create"),
) -> Callable[[_Json], _Json]:
    """ROOT gate for ADR-0044 Decision §3 — at most one Program-role
    generation may dispatch to a Program direct-child seat at a time.

    Applies only when the call names an agent in PROGRAM_DIRECT_CHILDREN
    (product/architect/crew_alpha/crew_beta/ux/staff/platform/security/
    release/oncall — the ADR's Outcome (a) set). Recomputes the digest of
    the EXACT call being made with the same `canonical_dispatch_digest`
    function `nightshift-ctl authority digest`/`permit-dispatch` use (loaded
    from the attested controller, so there is one definition, not two that
    could drift), looks up an unexpired, unconsumed permit for that digest
    bound to the currently-active authority generation, and consumes it
    atomically (compare-and-set via `consume_program_action_permit`'s
    UPDATE ... WHERE ... rowcount check). ALLOWs iff exactly one row
    changed; DENYs otherwise; fails closed on any DB/policy error — the
    exact same shape `worker_semaphore` above already uses and this project
    already trusts.

    Named, disclosed limitation (ADR-0044 Consequences style — not silently
    dropped): a `session_id`-mode continuation carries no `agent` field, so
    it cannot be classified against PROGRAM_DIRECT_CHILDREN from the event
    alone and is not gated here. The session that `session_id` continues can
    only have been CREATED by an agent+title call, which this policy did
    gate — so this is a narrower gap (racing to continue an
    already-permitted session) than an unfenced creation, but it is real and
    is recorded in the task's own PR body, not claimed fixed.
    """
    tools = set(dispatch_tools)

    def _fail_open() -> bool:
        return os.environ.get("NIGHTSHIFT_AUTHORITY_FAIL_OPEN") == "1"

    def _evaluate(event: _Json) -> _Json:
        if not isinstance(event, dict) or event.get("type") != "tool_call":
            return _ALLOW
        data = event.get("data")
        if not isinstance(data, dict) or data.get("name") not in tools:
            return _ALLOW
        tool_name = data.get("name")
        call = data.get("arguments")
        if not isinstance(call, dict):
            return _ALLOW
        agent = call.get("agent")
        if not isinstance(agent, str) or agent not in PROGRAM_DIRECT_CHILDREN:
            return _ALLOW
        title = call.get("title")
        args = call.get("args")
        digest = canonical_dispatch_digest(tool_name, agent, title, args)
        db_path = os.path.expanduser(db)
        if not os.path.exists(db_path):
            if _fail_open():
                return _ALLOW
            return _deny(
                f"control plane not initialised ({db} missing) — Program-"
                "authority dispatch cannot be verified, so it is denied "
                "(ADR-0044 §3)."
            )
        now = time.time()
        try:
            cx = sqlite3.connect(db_path, timeout=5, isolation_level=None)
            cx.row_factory = sqlite3.Row
            try:
                cx.execute("PRAGMA busy_timeout=5000")
                cx.execute("BEGIN IMMEDIATE")
                expire_stale(cx, now)
                auth_row = cx.execute(
                    "SELECT generation FROM program_authority WHERE state='active'"
                ).fetchone()
                if auth_row is None:
                    cx.execute("ROLLBACK")
                    return _deny(
                        "no live Program authority holds the fence — "
                        "acquire it first (`nightshift-ctl authority "
                        "acquire`, ADR-0044 §2)."
                    )
                generation = auth_row["generation"]
                # Issue #720 / ADR-0091. `security`, `ux` and `staff` carry a
                # review domain the planner decides, so their permits must
                # have been minted through the gated form -- a permit minted
                # blind (`--digest`) has `review_gate` NULL and will not
                # satisfy this consume. Every other agent is unaffected: the
                # gate is None and the WHERE clause is exactly what it was.
                gate = review_gate_for_agent(agent)
                if not consume_program_action_permit(cx, digest, generation,
                                                     now, gate):
                    cx.execute("ROLLBACK")
                    if gate is not None:
                        return _deny(
                            f"dispatching {agent} needs a permit minted "
                            "against the review plan for this head — mint it "
                            "with `nightshift-ctl authority permit-dispatch "
                            f"--agent {agent} --task ... --repo ... "
                            "--head-sha ...` (ADR-0091). A permit minted from "
                            "a bare --digest cannot dispatch a review seat."
                        )
                    return _deny(
                        "no unexpired, unconsumed permit for this exact "
                        "call at the current Program authority generation "
                        "(ADR-0044 §3) — call `nightshift-ctl authority "
                        "permit-dispatch` for this exact digest first."
                    )
                cx.execute("COMMIT")
                return _ALLOW
            except sqlite3.Error:
                cx.execute("ROLLBACK")
                raise
            finally:
                cx.close()
        except sqlite3.Error as e:
            if _fail_open():
                return _ALLOW
            return _deny(
                f"control plane unavailable ({e.__class__.__name__}) — "
                "Program-authority dispatch denied rather than allowed "
                "unverified (ADR-0044 §3)."
            )

    return _evaluate


# GitHub closing keywords ahead of an issue ref. A task PR that carries one
# lets the FIRST merged PR close a backlog issue other task PRs still feed.
_CLOSING = re.compile(
    r"\b(clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))\b\s*:?\s+[\w./-]*#\d+",
    re.IGNORECASE,
)
_PR_CREATE = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
_BODY_FILE = re.compile(r"(^|\s)(--body-file(=|\s)|-F\s)")
_FILL = re.compile(r"(^|\s)--fill(-first|-verbose)?\b")


def pr_close_keyword_guard(
    *,
    shell_tools: Collection[str] = (),
) -> Callable[[_Json], _Json]:
    """
    Factory: make ``nightshift-ctl pr-create`` the only path that can set a PR
    body from a file, and DENY inline closing keywords everywhere.

    Three denials on a raw ``gh pr create``/``gh pr edit`` command:

    1. inline text carrying a GitHub closing keyword
       (``Closes/Fixes/Resolves #N``) — the first merged task PR would close
       a backlog issue other task PRs still feed;
    2. ``--body-file``/``-F`` — the policy cannot read the file's bytes, so
       the file path is only allowed through ``nightshift-ctl pr-create``, which
       lints the actual content before exec'ing gh;
    3. ``--fill*`` on create — the body comes from commit messages and
       cannot be linted ahead of time.

    ``nightshift-ctl pr-create -- …`` commands do not match ``gh pr create`` and
    pass through untouched — that wrapper is the sanctioned path.
    """
    tools = shell_tool_names(shell_tools)

    def _evaluate(event: _Json) -> _Json:
        call = _tool_call(event, tools)
        if call is None:
            return _ALLOW
        cmd = call.get("command")
        if not isinstance(cmd, str) or not _PR_CREATE.search(cmd):
            return _ALLOW
        m = _CLOSING.search(cmd)
        if m:
            return _deny(
                f"PR body/title contains a closing keyword ({m.group(0)!r}). "
                "Task PRs must use `Refs #<n>` — the program closes the issue "
                "only after every task PR has integrated and pm acceptance "
                "passed. Rewrite the body and rerun `nightshift-ctl pr-lint`."
            )
        if _BODY_FILE.search(cmd):
            return _deny(
                "raw `gh pr create/edit` with --body-file is denied — the "
                "policy cannot lint a file it cannot read. Use "
                "`nightshift-ctl pr-create -- <same args>` (it lints the file's "
                "actual content, then calls gh), or pass the body inline."
            )
        if "pr create" in cmd and _FILL.search(cmd):
            return _deny(
                "`gh pr create --fill*` builds the body from commit messages, "
                "which cannot be linted ahead of time — write an explicit "
                "body and open the PR through `nightshift-ctl pr-create -- …`."
            )
        return _ALLOW

    return _evaluate


# What merges a pull request or writes a merge onto a branch: `gh pr merge`,
# `gh repo sync`, the raw REST calls (`pulls/<n>/merge`, `merges`,
# `merge-upstream`) and the GraphQL mutations below. Recognised on the argv
# the ADR-0088 lexer builds, not on the raw string, so quoting, `$'gh'`,
# globbed paths, xargs and `env -S` resolve the same way they do for the
# tracker and Actions fences.
_GRAPHQL_MERGE_MUTATION = re.compile(
    r"\b(mergePullRequest|enablePullRequestAutoMerge|mergeBranch)\b")
_MERGE_GUARDED = frozenset({"pr", "repo", "api"})


def _merges(call: Any, command: str) -> bool:
    """Whether one gh invocation merges -- or cannot be shown not to."""
    tail = call.argv[1:]
    offset, _repo = _consume_leading_flags(tail)
    if offset >= len(tail):
        return call.fed or call.unresolved
    subcommand, rest = tail[offset], tail[offset + 1:]
    if subcommand not in _KNOWN_GH_SUBCOMMANDS:
        return True
    if call.unresolved and subcommand in _MERGE_GUARDED:
        return True
    if subcommand in ("pr", "repo"):
        verb_offset, _ = _consume_leading_flags(rest)
        if verb_offset >= len(rest):
            return call.fed
        verb = rest[verb_offset]
        return _is_dynamic(verb) or verb == ("merge" if subcommand == "pr" else "sync")
    if subcommand != "api":
        return False
    api = _parse_gh_api(rest)
    if api.endpoint is None:
        return False
    if api.endpoint.lower() == "graphql":
        return (bool(_GRAPHQL_MERGE_MUTATION.search(command))
                or _graphql_query_unresolvable(api, call.fed))
    write = call.fed or api.method not in _READ_METHODS
    if "$" in api.endpoint or "`" in api.endpoint or _is_dynamic(api.endpoint):
        return write or "merge" in api.endpoint
    segments = [s.lower() for s in _api_segments(api.endpoint)]
    if "pulls" in segments and "merge" in segments:
        return True
    return write and bool({"merges", "merge-upstream"} & set(segments))


def _is_merge_call(command: Any) -> bool:
    """Fail closed: a command that is not a string, cannot be lexed, or
    holds a gh call that cannot be parsed counts as a merge call."""
    if not isinstance(command, str):
        return True
    try:
        return any(_merges(call, command)
                   for call in _gh_call_argvs(_full_token_stream(command)))
    except Exception:  # _Unparseable, or anything unforeseen: fail closed
        return True


def external_repo_merge_fence(
    *,
    shell_tools: Collection[str] = (),
) -> Callable[[_Json], _Json]:
    """ADR-0085 — the mechanical fence: DENY `gh pr merge` (the equivalent
    raw REST call `gh api .../pulls/<n>/merge`, or the equivalent raw
    GraphQL call `gh api graphql ...mergePullRequest...`) whenever THIS
    DEPLOYMENT is classified as operating on a repository it does not own.

    Classification comes from EXACTLY ONE source:
    ``nightshift_authority.classify_repository``, reading
    ``<state_root>/authority/<repository_id>/authority.db`` and addressed by
    exactly two environment variables — ``NIGHTSHIFT_STATE_ROOT`` and
    ``NIGHTSHIFT_REPOSITORY_ID`` (``nightshift_authority.scope_from_env``).
    Never a prompt, never a YAML string a worker can edit in a pull request,
    never ``.omni/control.db``, never any other environment variable. See
    that module's docstring for the full ADR-0085 rationale, including the
    named limit on what this fence can and cannot guarantee against a
    compromised worker.

    FAILS CLOSED WITH NO ESCAPE HATCH — see the module docstring above for
    why this, alone among the fail-closed policies in this file, has no
    ``..._FAIL_OPEN`` variable. ANY exception reaching this evaluator — a
    missing scope, a malformed repository id, an unreadable or corrupt
    registry — is a DENY, never a crash and never an ALLOW. In practice this
    is defense in depth only: `classify_repository` itself is written to
    never raise, folding every such condition into an `external`
    classification already; this `try/except` exists so that a future
    change to that contract fails safe here too, rather than needing to be
    remembered.

    A command is a merge call when the ADR-0088 lexer finds a merging gh
    invocation in it (see `_merges`), or when it cannot rule one out: a
    non-string or unlexable command, an xargs-fed or expanded `gh`, a flag
    outside gh's table (audit C4). Before, a regex on the raw string missed
    `gh "pr" "merge"`, `gh -R o/r pr merge` and `$'gh' pr merge`.

    Named limitation, disclosed not hidden: this classifies the DEPLOYMENT,
    not the argument of the `gh` call. `gh pr merge -R other/repo` from a
    self-classified deployment is ALLOWed. That is the safe direction for
    the risk this fence exists to close — an external-mode deployment
    denies EVERY merge, including one aimed at its own repository — rather
    than the unsafe one; parsing `gh`'s own repo-resolution fallback (the
    cwd's git remote) would be a heuristic dressed up as a mechanism.
    """
    tools = shell_tool_names(shell_tools)

    def _evaluate(event: _Json) -> _Json:
        call = _tool_call(event, tools)
        if call is None:
            return _ALLOW
        if not _is_merge_call(call.get("command")):
            return _ALLOW
        try:
            state_root, repository_id = _AUTHORITY.scope_from_env()
            classification = _AUTHORITY.classify_repository(state_root, repository_id)
            ownership = classification.get("ownership")
            source = classification.get("source")
        except Exception as exc:  # fail closed -- see docstring above
            return _deny(
                f"repository ownership could not be determined "
                f"({exc.__class__.__name__}) — denying rather than merging "
                "unverified (ADR-0085)."
            )
        if ownership != "self":
            return _deny(
                f"{_AUTHORITY.MERGE_FORBIDDEN_REASON}: this deployment is "
                f"operating on a repository it does not own (source: "
                f"{source}). Leave the pull request open for the customer "
                "team to merge — there is no override (ADR-0085)."
            )
        return _ALLOW

    return _evaluate
