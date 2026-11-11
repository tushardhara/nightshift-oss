"""G0 -- Nightshift READS GitHub Actions results and never CAUSES them.

WHY THIS EXISTS
===============
`external_repo_merge_fence` (ADR-0085) closes merging into a repository this
deployment does not own. Nothing closes the adjacent, cheaper mistake: a
worker that re-runs a red check instead of reporting it. On a customer
repository that spends the customer's Actions minutes, mutates their run
history, and -- for a `workflow_dispatch` on a deploy workflow -- can take a
real action in their infrastructure. "The seat was told not to" is prose,
and prose is exactly the class of thing ADR-0085 had to close mechanically.

WHAT IS DENIED
==============
Every shape that CAUSES a run:
  * `gh workflow run|enable|disable`  (anything under `gh workflow` that is
    not a read verb)
  * `gh run rerun|cancel|delete`     (anything under `gh run` that is not a
    read verb)
  * `gh api` POST/PUT/PATCH/DELETE against any `actions/` path
  * `gh api ... actions/workflows/<id>/dispatches`  (workflow_dispatch)
  * `gh api ... actions/runs/<id>/rerun`            (re-run)
  * `gh api ... repos/<o>/<r>/dispatches`           (repository_dispatch)
  * `gh api` POST to `check-suites|check-runs/<id>/rerequest`
  * any `gh` call the lexer cannot resolve (xargs-fed, `${G:-gh}`, a flag
    outside gh's flag table, an unknown or expanded subcommand) that may
    name `workflow`, `run` or `api` -- audit C4

WHAT IS ALLOWED
===============
Reading. `gh run list|view|watch|download`, `gh workflow list|view`, and any
GET against `actions/` -- including the one sanctioned read this program
already makes, `repos/<o>/<r>/commits/<sha>/check-runs`
(`bin/nightshift_github.py`).

Both verb lists are ALLOW-lists: a `gh workflow`/`gh run` subcommand that is
not named as a read DENIES. A future read-only subcommand is denied until
this module names it, which is the safe direction.

THE ADR-0085 DEFECT THIS FENCE DOES NOT REPEAT
==============================================
It does not regex the raw shell string. It reuses
`nightshift_issue_fence`'s ADR-0088 lexer (`_full_token_stream` /
`_gh_call_argvs`), so `gh "workflow" "run"`, `G=gh; $G workflow run`,
`sh -c 'gh run rerun 1'`, and a `gh` call sitting after `;`/`&&`/`|` all
resolve to a real argv and are classified. That parser is already covered by
`tests/test_tracker_issue_fence.py::IssueFenceArgvParsing`.

FAILS CLOSED, NO ESCAPE HATCH
=============================
Same posture as ADR-0085, for the same reason: a denied read costs one
retry through the sanctioned wrapper; a wrongly-ALLOWed dispatch spends
someone else's minutes and cannot be un-run. A command that cannot be lexed,
a non-string `command`, an unrecognised pre-verb `gh` flag, or any exception
reaching the evaluator is a DENY. There is deliberately no `..._FAIL_OPEN`
variable in this module.

DISCLOSED LIMITS
================
1. This classifies the COMMAND, not the target repository. It denies these
   shapes regardless of which repository they aim at, including this
   deployment's own. That is the safe direction, and matches ADR-0085's
   own deployment-not-argument stance.
2. It sees shell commands only. A dispatch issued from inside a Python
   script the worker writes and runs, or through a library, is not visible
   here -- exactly the limit `nightshift_issue_fence` discloses for itself.
3. `gh` subcommand coverage is inherited from the lexer's allow-list; an
   unknown subcommand (an alias or extension that may run `workflow run`)
   DENIES here too, so this fence does not depend on the tracker fence.
4. Other events that start workflows -- a push, a release, a deployment --
   are not "causing a run" in this fence's sense and are not classified.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Collection

from nightshift_issue_fence import (
    _ALLOW,
    _KNOWN_GH_SUBCOMMANDS,
    _READ_METHODS,
    _GhCall,
    _Unparseable,
    _api_segments,
    _consume_leading_flags,
    _deny,
    _full_token_stream,
    _gh_call_argvs,
    _is_dynamic,
    _parse_gh_api,
    _tool_call,
    shell_tool_names,
)

_Json = dict[str, Any]

#: `gh workflow <verb>` that only reads.
_WORKFLOW_READ_VERBS = frozenset({"list", "view"})

#: `gh run <verb>` that only reads. `watch` polls an already-running run; it
#: starts nothing. `download` fetches artifacts.
_RUN_READ_VERBS = frozenset({"list", "view", "watch", "download"})

#: Subcommands this fence classifies; an unresolvable call naming one DENIES.
_GUARDED = frozenset({"workflow", "run", "api"})

#: Path segments that re-run something whatever the method.
_RERUN_SEGMENTS = frozenset({"rerun", "rerun-failed-jobs"})

_REASON = "actions_dispatch_forbidden"


def _reason(detail: str) -> _Json:
    return _deny(
        f"{_REASON}: {detail} Nightshift reads check results and never causes "
        "them -- report the red, do not re-run it. There is no override (G0)."
    )


def _classify_api(rest: list[str], fed: bool) -> _Json | None:
    """`gh api` is parsed with gh's full flag table (`_parse_gh_api`), so a
    value flag such as `-H Accept:x` or `--jq .x` can no longer pose as the
    endpoint. xargs-fed calls are treated as writes: the method can be
    appended."""
    api = _parse_gh_api(rest)
    if api.endpoint is None or api.endpoint.lower() == "graphql":
        return None
    write = fed or api.method not in _READ_METHODS
    path = re.split(r"[?#]", api.endpoint, 1)[0]
    if "$" in api.endpoint or "`" in api.endpoint or _is_dynamic(path) \
            or (fed and "{" in path):
        return _reason(f"`gh api` endpoint {api.endpoint!r} is rewritten by the "
                       "shell, so it may be an Actions path.") if write else None
    segments = [s.lower() for s in _api_segments(api.endpoint)]
    if "dispatches" in segments:
        kind = "workflow_dispatch" if "workflows" in segments else "repository_dispatch"
        return _reason(f"this is a {kind}.")
    if "actions" in segments and _RERUN_SEGMENTS & set(segments):
        return _reason("this re-runs an existing run.")
    if write and "actions" in segments:
        return _reason(f"this is a {api.method} against an Actions path.")
    if write and "rerequest" in segments:
        return _reason("this re-requests a check suite or check run.")
    return None


def _classify_gh_call(call: _GhCall) -> _Json | None:
    """Return a DENY for one `gh` invocation, or None if it is not our concern."""
    tail = call.argv[1:]
    offset, _repo = _consume_leading_flags(tail)
    if offset >= len(tail):
        if call.fed or call.unresolved:
            return _reason("`gh` takes its subcommand from xargs/find input or "
                           "from an expansion of its own name.")
        return None
    subcommand = tail[offset]
    rest = tail[offset + 1:]
    if subcommand not in _KNOWN_GH_SUBCOMMANDS:
        return _reason(f"{subcommand!r} is not a gh subcommand this fence can "
                       "resolve (an alias, extension or expansion).")
    if call.unresolved and subcommand in _GUARDED:
        return _reason(f"{call.argv[0]!r} is not literally `gh`, but the shell "
                       f"can make it gh, followed by `{subcommand}`.")

    if subcommand == "api":
        return _classify_api(rest, call.fed)

    if subcommand not in ("workflow", "run"):
        return None

    verb_offset, _ = _consume_leading_flags(rest)
    verb = rest[verb_offset] if verb_offset < len(rest) else None
    allowed = _WORKFLOW_READ_VERBS if subcommand == "workflow" else _RUN_READ_VERBS

    if verb is None:
        # `gh workflow` / `gh run` with no verb prints help and starts
        # nothing -- unless xargs appends the verb.
        return _reason(f"`gh {subcommand}` takes its verb from xargs/find "
                       "input.") if call.fed else None
    if verb in allowed:
        return None
    return _reason(f"`gh {subcommand} {verb}` can cause a run.")


def github_actions_fence(
    *,
    shell_tools: Collection[str] = (),
) -> Callable[[_Json], _Json]:
    """G0 -- DENY every shell shape that causes a GitHub Actions run.

    See the module docstring for what is denied, what stays allowed, and the
    disclosed limits. `shell_tools` adds names to `shell_tool_names()`; it
    cannot remove any.
    """
    tools: Collection[str] = shell_tool_names(shell_tools)

    def _evaluate(event: _Json) -> _Json:
        call = _tool_call(event, tools)
        if call is None:
            return _ALLOW

        cmd = call.get("command")
        if not isinstance(cmd, str):
            return _reason("the shell command was not a string, so it could "
                           "not be classified.")

        try:
            gh_calls = _gh_call_argvs(_full_token_stream(cmd))
        except _Unparseable as exc:
            return _reason(f"the command could not be lexed ({exc}).")
        except Exception as exc:  # fail closed -- see docstring
            return _reason(f"classification raised {exc.__class__.__name__}.")

        for call in gh_calls:
            try:
                verdict = _classify_gh_call(call)
            except _Unparseable as exc:
                return _reason(f"the command could not be classified ({exc}).")
            if verdict is not None:
                return verdict

        return _ALLOW

    return _evaluate
