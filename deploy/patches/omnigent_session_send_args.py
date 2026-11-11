#!/usr/bin/env python3
"""Local patch: let ``sys_session_send`` carry dispatch metadata for policy hooks.

WHY THIS FILE EXISTS
====================
omnigent's ``SysSessionSendTool`` declares ``args`` with
``additionalProperties: false`` and five keys the tool itself interprets:
``input``, ``purpose``, ``model``, ``file_ids``, ``cost_budget``.

That makes it impossible for an admission policy to require a field of its own.
A policy hook only ever sees an event AFTER the tool's schema validation has
passed, so it structurally cannot accept a dispatch the schema already rejected::

    Input validation error: Additional properties are not allowed
    ('generation', 'slot_id', 'task_id' were unexpected)

``deploy/policies/omnipods_policies.py``'s ``worker_semaphore`` requires exactly
those three fields on any named send to a counted engineer/QA/reviewer seat.
They could never reach it, so no counted dispatch has ever succeeded
(tushardhara/nightshift#236, open since 2026-08-23).

WHY A PATCH RATHER THAN AN UPSTREAM FIX
=======================================
The change is small and correct, and it follows omnigent's own precedent —
``args.purpose`` is declared purely so a policy can read it, and the tool does
not interpret that either. But upstream is not taking pull requests, so this
deployment carries the change itself.

Declared rather than relaxed: strict mode requires ``additionalProperties:
false`` (omnigent's ``tests/tools/test_strict.py``), and relaxing it would break
strict-mode providers. All three fields are optional, so no existing caller
changes.

WHY IT IS NOT JUST AN EDIT
==========================
``uv tool upgrade omnigent`` replaces ``site-packages`` wholesale and erases a
hand edit with no warning. The symptom is silent: counted dispatch simply starts
failing again, and the error is indistinguishable from the original bug. This
script is idempotent, verifies that it applied, and is safe to re-run after any
upgrade.

USAGE
=====
::

    python3 deploy/patches/omnigent_session_send_args.py --check    # exit 0 applied, 1 not
    python3 deploy/patches/omnigent_session_send_args.py --apply
    python3 deploy/patches/omnigent_session_send_args.py --revert

``--check`` is wired into ``deploy/preflight.sh`` so a run cannot start on an
unpatched install.

AFTER APPLYING, RESTART BOTH PROCESSES
======================================
The server and the ``omnigent host`` daemon import their modules once at boot
and cache them independently. A patched file on disk is not served until the
owning process restarts, and the host is the one that gets forgotten::

    kill $(ss -lptnH 'sport = :6767' | grep -oE 'pid=[0-9]+' | cut -d= -f2)
    kill $(pgrep -f 'omnigent host')
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

MARKER = '"slot_id"'
BACKUP_SUFFIX = ".nightshift-orig"

ANCHOR = (
    '                                "required": ["input"],\n'
    '                                "additionalProperties": False,'
)
# The properties block closes immediately before the anchor. Matching both
# together is what makes the insertion point unambiguous.
CLOSING = "                                },\n" + ANCHOR

ADDITION = '''                                    "slot_id": {
                                        "type": "integer",
                                        "description": (
                                            "Optional dispatch metadata. Not interpreted "
                                            "by this tool; forwarded so an admission "
                                            "policy can meter a counted worker against a "
                                            "control-plane slot."
                                        ),
                                    },
                                    "task_id": {
                                        "type": "string",
                                        "description": (
                                            "Optional dispatch metadata. Not interpreted "
                                            "by this tool; forwarded so a policy can bind "
                                            "the dispatch to a unit of work."
                                        ),
                                    },
                                    "generation": {
                                        "type": "integer",
                                        "description": (
                                            "Optional dispatch metadata. Not interpreted "
                                            "by this tool; forwarded so a policy can fence "
                                            "a stale grant."
                                        ),
                                    },
'''


def locate() -> Path:
    """Find the installed omnigent's spawn.py, without importing omnigent."""
    candidates: list[Path] = []

    try:
        import importlib.util

        spec = importlib.util.find_spec("omnigent")
        if spec and spec.origin:
            candidates.append(Path(spec.origin).parent / "tools" / "builtins" / "spawn.py")
    except Exception:
        pass

    home = Path.home()
    for base in (
        home / ".local/share/uv/tools/omnigent/lib",
        home / ".local/share/uv/tools/omnigent",
    ):
        if base.is_dir():
            candidates.extend(base.glob("**/site-packages/omnigent/tools/builtins/spawn.py"))

    for c in candidates:
        if c.is_file():
            return c

    raise SystemExit(
        "could not locate omnigent's tools/builtins/spawn.py.\n"
        "Pass the path explicitly with --path, or check that omnigent is installed."
    )


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_check(path: Path) -> int:
    src = path.read_text()
    applied = MARKER in src
    print(f"target : {path}")
    print(f"sha256 : {sha(path)[:16]}")
    print(f"status : {'APPLIED' if applied else 'NOT APPLIED'}")
    if not applied:
        print(
            "\nCounted-seat dispatch will fail with:\n"
            "  Input validation error: Additional properties are not allowed\n"
            "  ('generation', 'slot_id', 'task_id' were unexpected)\n"
            "Run with --apply, then restart the omnigent server AND the host daemon."
        )
    return 0 if applied else 1


def cmd_apply(path: Path) -> int:
    src = path.read_text()
    before = sha(path)

    if MARKER in src:
        print(f"already applied, nothing to do (sha256 {before[:16]})")
        return 0

    if src.count(CLOSING) != 1:
        print(
            f"REFUSING: expected exactly one insertion point, found {src.count(CLOSING)}.\n"
            "omnigent's schema has moved. Re-derive the anchor before forcing this.",
            file=sys.stderr,
        )
        return 2

    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"backup : {backup}")

    path.write_text(src.replace(CLOSING, ADDITION + CLOSING, 1))
    after = sha(path)

    if after == before:
        print("REFUSING: file unchanged after write; the edit did not apply.", file=sys.stderr)
        return 2

    try:
        import ast

        ast.parse(path.read_text())
    except SyntaxError as exc:
        shutil.copy2(backup, path)
        print(f"REFUSING: patch produced a syntax error ({exc}); restored backup.", file=sys.stderr)
        return 2

    print(f"applied: {before[:16]} -> {after[:16]}")
    print(
        "\nNOW RESTART BOTH PROCESSES, or this change is not served:\n"
        "  kill $(ss -lptnH 'sport = :6767' | grep -oE 'pid=[0-9]+' | cut -d= -f2)\n"
        "  kill $(pgrep -f 'omnigent host')"
    )
    return 0


def cmd_revert(path: Path) -> int:
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        print(f"no backup at {backup}; nothing to revert to", file=sys.stderr)
        return 1
    shutil.copy2(backup, path)
    print(f"reverted from {backup} (sha256 {sha(path)[:16]})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", type=Path, help="explicit path to omnigent tools/builtins/spawn.py")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 0 if applied, 1 if not")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    path = args.path or locate()
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2

    if args.check:
        return cmd_check(path)
    if args.apply:
        return cmd_apply(path)
    return cmd_revert(path)


if __name__ == "__main__":
    raise SystemExit(main())
