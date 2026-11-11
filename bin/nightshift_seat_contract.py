#!/usr/bin/env python3
"""nightshift_seat_contract.py -- the missing consumer (#443/#442 epic child,
ADR-0086): binds a seat's toolchain to the COMPILED PROJECT CONTRACT instead
of to prose written into that seat's own config file.

WHY THIS EXISTS
================
`nightshift_project_contract.py` (#443) already compiles, at an exact commit,
which commands this repository's own tooling runs for each of several
capabilities (a unit-level suite, a broader integration pass, an end-to-end
pass, a compile/package step, a style check, a static-type check, a
formatting check) -- with real evidence and an honest `absent`/`conflict`
when it cannot say. Nothing consumed that output before this module: a seat's
own config prose named a specific runner instead, which is correct only for
the one repository that prose happened to be written against and silently
wrong on every other one. This module is that missing link: it reads a
compiled contract and turns it into the one thing a seat needs -- a per-
capability answer of "what do I run" (or an honest "nothing here resolves,
and here is why") plus the observed CI inventory, verbatim.

THE AUTHORITY LAYERING THIS MODULE EXISTS TO PRESERVE (ADR-0086)
=================================================================
  FACTS      observed/declared at an exact commit             -> BINDING
  RULES      declared, frozen, versioned                      -> BINDING
  EXPERIENCE learned across tasks, a prior repository's habits -> ADVISORY ONLY

`resolve()` accepts exactly a compiled contract and a seat identifier -- there
is no parameter through which a memory hit, a retrieval result, or "this
command worked on a different repository last time" could reach the
computation. A command a seat happens to remember is EXPERIENCE; it may
never substitute for, or widen, what this module reports. This module never
imports anything that reads learned or retrieved state.

WHAT THIS DOES NOT DO
======================
It adds zero detection logic and never picks a winner the contract itself
declined to pick: a capability the contract marked `conflict` stays
UNRESOLVED here too (its candidates are surfaced, plainly, so a reader can
see the disagreement -- never quietly collapsed to one of them). A capability
the contract could not observe at all (`absent`) stays unresolved and carries
the contract's own stated reason, verbatim -- never a substituted
convention. This module's own source deliberately names no concrete
ecosystem, runner, or build tool anywhere -- see issue #439's inward mirror,
whose frozen literal list this file must never contain a single one of, in
code, in a comment, or in a docstring.

EXIT CODES (this module's own vocabulary; see nightshift_project_contract.py
and nightshift_conformance.py for their own, deliberately different, tables)
==========================================================================
  0  ok            -- a briefing was produced. A capability reported
                       `unresolved` inside it is a REPORTED FACT, not a
                       failure of this run.
  2  usage error   -- bad CLI arguments, or repo_path is not a repository /
                       not its own toplevel (propagated from the detector).
  3  sha refused   -- the exact SHA requested does not resolve to a commit in
                       the repository (propagated from the detector's own
                       AC-1 refusal). NEVER caught and retried against the
                       working tree or the current checkout -- that fallback
                       is the exact defect shape both this module and the
                       detector it wraps exist to make impossible.
  4  indeterminate -- a supplied contract file could not be read/parsed, its
                       own digest does not recompute over its own canonical
                       bytes, or it names a different baseline commit than
                       the one this run was asked about. A file in this
                       state is not trusted, regardless of how plausible its
                       content looks.

Every non-zero exit prints one JSON object to stdout carrying `ok: false`
and `reason_code` -- the same flat shape the sibling modules already use.

Library:
    from nightshift_seat_contract import resolve, render_briefing
    briefing = resolve(compiled_contract, seat="qa")
    print(render_briefing(briefing))

CLI:
    python3 nightshift_seat_contract.py <repo_path> --sha <sha> --seat <name>
    python3 nightshift_seat_contract.py <repo_path> --sha <sha> --seat <name> \\
        --contract-file <digest-verified contract.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any, Dict, List, Optional

import nightshift_project_contract as pc

# The seven capabilities a seat plans work around. Order here is the order a
# rendered briefing lists them in -- deliberately not the contract's own
# field order, which interleaves them with fields (languages, dependency
# manifests, ...) no seat consumes through this module.
CAPABILITY_FIELDS: tuple = ("test", "integration", "e2e", "build", "lint", "typecheck", "format")


class SeatContractError(Exception):
    def __init__(self, reason_code: str, exit_code: int, details: Optional[dict] = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error: "SeatContractError") -> Dict[str, Any]:
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _usage_error(reason_code: str, details: Optional[dict] = None) -> "SeatContractError":
    return SeatContractError(reason_code, 2, details)


def _sha_refused(reason_code: str, details: Optional[dict] = None) -> "SeatContractError":
    return SeatContractError(reason_code, 3, details)


def _indeterminate(reason_code: str, details: Optional[dict] = None) -> "SeatContractError":
    return SeatContractError(reason_code, 4, details)


# ---------------------------------------------------------------------------
# Loading a previously-compiled contract from disk (digest-verified, AC-10
# of the detector this wraps). Duplicated here rather than imported from the
# conformance gate on purpose: this module's only import is the detector
# itself, so nothing about how a decision gate happens to be built can ever
# become a dependency of what a seat reads before it acts.
# ---------------------------------------------------------------------------

def load_contract_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            contract = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise _indeterminate("contract_unreadable", {"path": str(path), "detail": str(exc)}) \
            from exc

    claimed = contract.get("contract_digest") if isinstance(contract, dict) else None
    recomputed = None
    if isinstance(contract, dict):
        recomputed = "sha256:" + hashlib.sha256(pc.canonical_bytes(contract)).hexdigest()
    if not claimed or claimed != recomputed:
        raise _indeterminate("contract_digest_mismatch", {"path": str(path)})
    return contract


def _verify_baseline_sha(contract: Dict[str, Any], expected_sha: str, path: str) -> None:
    actual = contract.get("baseline_sha")
    if actual != expected_sha:
        raise _indeterminate("baseline_sha_mismatch", {
            "path": str(path), "expected_baseline_sha": expected_sha,
            "actual_baseline_sha": actual,
        })


# ---------------------------------------------------------------------------
# Resolution -- FACTS in, a per-capability answer out. No other input exists.
# ---------------------------------------------------------------------------

def _candidate_views(candidates: Optional[List[Any]], value_key: Optional[str]) -> List[Dict[str, Any]]:
    views: List[Dict[str, Any]] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        v = c.get("value")
        v = v if isinstance(v, dict) else {}
        views.append({
            "command": v.get("command"),
            "framework": v.get(value_key) if value_key else None,
            "evidence": list(c.get("evidence") or []),
            "source_kind": c.get("source_kind"),
            # The contract's own "this command's binary/path was not
            # confirmed present in the tree" signal (_compute_unverified in
            # nightshift_project_contract.py) -- must survive onto what a
            # seat reads, never silently dropped on the way through.
            "unverified": bool(c.get("unverified", False)),
        })
    return views


def _resolve_capability(category: str, field_env: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Returns a per-capability answer. `status` is "resolved" or
    "unresolved", exactly as before. An "unresolved" answer also carries
    `unresolved_kind`, one of:

      "absent"    the contract found NO evidence at all for this capability
                  (nothing runs it here, and nothing disagrees about it
                  either) -- there is nothing to STOP on; a seat that never
                  needed this capability skips it, and a seat that DID need
                  it must go establish it, not wait on a briefing that will
                  never resolve.
      "conflict"  the contract found real, disagreeing evidence and
                  deliberately declined to pick a winner -- THIS is the
                  shape that must STOP a seat about to rely on it, because
                  picking one candidate over another is exactly the
                  judgment call the contract refused to make.

    `unresolved_kind` is always None on a "resolved" answer.

    `unverified` (top-level, always present, always a bool) carries the
    contract's own AC-3 signal through unmodified: True means the
    resolved command names a binary or repo-relative path this tool could
    not confirm actually exists anywhere in the tree at baseline_sha (see
    `_compute_unverified` in nightshift_project_contract.py) -- a resolved
    command is a fact about what THIS repository's tooling declares, not
    a guarantee that declaration is correct. It is False whenever nothing
    resolved (absent/conflict never hand back a single command to
    doubt). Each entry in `candidates` carries the same flag for its own
    value.
    """
    value_key = pc.CATEGORY_VALUE_KEY.get(category)

    if not isinstance(field_env, dict):
        return {
            "status": "unresolved", "unresolved_kind": "absent",
            "command": None, "framework": None, "unverified": False,
            "evidence": [], "reason": f"{category}: not present in the supplied contract",
            "candidates": [],
        }

    status = field_env.get("status")
    candidates = field_env.get("candidates")

    if status in ("observed", "declared"):
        value = field_env.get("value")
        value = value if isinstance(value, dict) else {}
        return {
            "status": "resolved", "unresolved_kind": None,
            "command": value.get("command"),
            "framework": value.get(value_key) if value_key else None,
            "unverified": bool(field_env.get("unverified", False)),
            "evidence": list(field_env.get("evidence") or []),
            "reason": field_env.get("reason"),
            "candidates": _candidate_views(candidates, value_key),
        }

    if status == "conflict":
        # `value` is None by construction for a conflicted field -- the
        # ONLY place a command or a framework can still be read from is
        # each surviving candidate's OWN value. A framework resolves here
        # iff every surviving candidate names the same one (a set-
        # cardinality fact about the candidates themselves, never a pick
        # among them) -- the command never does, because picking one
        # candidate's command over another's is exactly the judgment call
        # the contract already declined to make.
        frameworks = set()
        for c in candidates or []:
            v = c.get("value") if isinstance(c, dict) else None
            if isinstance(v, dict) and value_key:
                fw = v.get(value_key)
                if fw:
                    frameworks.add(fw)
        resolved_framework = next(iter(frameworks)) if len(frameworks) == 1 else None
        return {
            "status": "unresolved", "unresolved_kind": "conflict",
            "command": None,
            "framework": resolved_framework,
            "unverified": False,
            "evidence": list(field_env.get("evidence") or []),
            "reason": field_env.get("reason"),
            "candidates": _candidate_views(candidates, value_key),
        }

    # status == "absent", or any shape this module does not recognise --
    # fail the same way absence does: unresolved, nothing substituted, the
    # contract's own stated reason carried through verbatim.
    return {
        "status": "unresolved", "unresolved_kind": "absent",
        "command": None, "framework": None, "unverified": False,
        "evidence": [], "reason": field_env.get("reason"),
        "candidates": _candidate_views(candidates, value_key),
    }


def resolve(contract: Dict[str, Any], *, seat: str) -> Dict[str, Any]:
    """The one function through which a compiled contract becomes a seat's
    answer. Takes ONLY `contract` and `seat` -- no third parameter exists
    through which anything advisory could reach this computation (ADR-0086).
    `seat` labels the output; it never changes which facts are reported --
    two seats reading the same contract read the same facts.
    """
    capabilities = {category: _resolve_capability(category, contract.get(category))
                    for category in CAPABILITY_FIELDS}
    return {
        "seat": seat,
        "baseline_sha": contract.get("baseline_sha"),
        "contract_digest": contract.get("contract_digest"),
        "capabilities": capabilities,
        "ci": contract.get("ci"),
    }


# ---------------------------------------------------------------------------
# Rendering -- the markdown a seat actually reads.
# ---------------------------------------------------------------------------

def render_briefing(resolved: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Toolchain briefing -- seat: {resolved.get('seat')}")
    lines.append("")
    lines.append(f"- baseline_sha: {resolved.get('baseline_sha')}")
    lines.append(f"- contract_digest: {resolved.get('contract_digest')}")
    lines.append("")
    lines.append(
        "Everything below was compiled from the repository at the exact commit "
        "named above. A command you recall from a different repository, or from "
        "an earlier task, is EXPERIENCE -- it may explain what you are reading "
        "but it may never replace one of these facts and may never widen what "
        "you run beyond what is written here (ADR-0086)."
    )
    lines.append("")

    for category, cap in resolved.get("capabilities", {}).items():
        lines.append(f"## {category}")
        lines.append(f"- status: {cap.get('status')}")
        if cap.get("status") == "unresolved":
            lines.append(f"- unresolved_kind: {cap.get('unresolved_kind')}")
        lines.append(f"- command: {cap.get('command')!r}")
        lines.append(f"- framework: {cap.get('framework')!r}")
        if cap.get("status") == "resolved":
            lines.append(f"- unverified: {cap.get('unverified')!r}")
        if cap.get("status") == "unresolved" and cap.get("reason"):
            lines.append(f"- reason: {cap['reason']}")
        if cap.get("evidence"):
            lines.append(f"- evidence: {', '.join(cap['evidence'])}")
        candidates = cap.get("candidates") or []
        if candidates:
            lines.append("- candidates on record (no winner was picked; do not pick one "
                          "yourself):")
            for c in candidates:
                lines.append(
                    f"  - command={c.get('command')!r} framework={c.get('framework')!r} "
                    f"unverified={c.get('unverified')!r} evidence={c.get('evidence')}")
        lines.append("")

    ci = resolved.get("ci")
    ci = ci if isinstance(ci, dict) else {}
    lines.append("## ci")
    lines.append(f"- status: {ci.get('status')}")
    ci_value = ci.get("value")
    ci_value = ci_value if isinstance(ci_value, dict) else {}
    for system in ci_value.get("systems") or []:
        lines.append(
            f"  - system={system.get('system')} workflows={system.get('workflows')} "
            f"checks={system.get('checks')}")
    lines.append("")

    lines.append(
        "A capability you are about to rely on that shows `unresolved` above is a "
        "STOP only when its `unresolved_kind` is `conflict`: the contract found "
        "real, disagreeing evidence and deliberately declined to pick a winner for "
        "you, and picking one yourself is exactly the judgment call it refused to "
        "make -- escalate rather than substitute a command from elsewhere. When "
        "`unresolved_kind` is `absent`, the contract found NO evidence at all -- "
        "this repository's own tooling simply does not have that capability, "
        "there is nothing to disagree about, and nothing to STOP on; skip it and "
        "say so in your report. A capability you were never going to rely on "
        "being `unresolved`, of either kind, is not your problem to solve."
    )
    lines.append(
        "A `resolved` capability with `unverified: True` names a command whose "
        "binary or repo-relative path this contract could NOT confirm exists "
        "anywhere in the tree at baseline_sha -- it is still the only command "
        "this repository's own tooling declares, so run it, but do not be "
        "surprised by a not-found failure, and say in your report that the "
        "command was unverified rather than treating a first-run failure as "
        "something you broke."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nightshift_seat_contract.py",
        description="Resolves a seat's toolchain FACTS from the compiled project contract "
                     "(#443) instead of from config prose (ADR-0086). Prints a markdown "
                     "briefing on success; a JSON error object on any refusal.")
    parser.add_argument("repo_path", help="path to the repository root to inspect")
    parser.add_argument("--sha", required=True,
                         help="exact 40 or 64 character commit SHA to resolve at")
    parser.add_argument("--seat", required=True, help="the seat this briefing is addressed to")
    parser.add_argument("--contract-file",
                         help="digest-verified contract JSON file, used in place of "
                              "compiling repo_path at --sha")
    args = parser.parse_args(argv)

    def emit_error(obj: Dict[str, Any], code: int) -> int:
        print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True))
        return code

    try:
        if args.contract_file:
            contract = load_contract_file(args.contract_file)
            _verify_baseline_sha(contract, args.sha, args.contract_file)
        else:
            try:
                contract = pc.build_contract(args.repo_path, sha=args.sha)
            except pc.ShaNotPresent as exc:
                raise _sha_refused("sha_not_present", {"sha": args.sha, "detail": str(exc)}) \
                    from exc
            except pc.ContractInvalid as exc:
                raise _indeterminate("contract_invalid", {"sha": args.sha, "detail": str(exc)}) \
                    from exc
            except NotADirectoryError as exc:
                raise _usage_error("usage_error", {"detail": str(exc)}) from exc
    except SeatContractError as exc:
        payload = {"seat": args.seat, **error_payload(exc)}
        return emit_error(payload, exc.exit_code)

    resolved = resolve(contract, seat=args.seat)
    print(render_briefing(resolved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
