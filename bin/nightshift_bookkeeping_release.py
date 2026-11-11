#!/usr/bin/env python3
"""Bookkeeping-only fast release path: mechanical classification plus the
focused deterministic checks a fast-path PR must pass (issue #286, ADR-0052).

Read/compute only, exactly the split `bin/nightshift_review_plan.py` already
uses: this module reads task rows through an injected cursor and Git objects
through plumbing subprocess calls, and returns a plan dict or raises
`BookkeepingDenied`. It never writes to `control.db` -- every INSERT lives in
`bin/nightshift-ctl`'s own `cmd_bookkeeping_*` functions, so "which module
owns control.db writes" stays answerable by one rule everywhere in this
codebase.

FAIL CLOSED (AC-1): `classify_paths` defaults to `"normal"` on anything it
does not affirmatively recognize as a pure add/modify of an allowlisted
regular file. There is no code path that upgrades an uncertain diff to
`"bookkeeping-only"`.

Eligible paths (issue #286's own list, deliberately NOT sourced from
`program/review-risk.json` -- that ruleset is `platform`-owned and governs a
different decision, PR-review domain gating; this module's allowlist is a
fixed, spec-given set that belongs to the crew that owns the release-path
decision):
    .omni/STATUS.md
    .omni/ledger.md
    .omni/tech-debt.md
    .omni/reports/**

Rename detection is disabled on every diff this module runs (`--no-renames`).
That is a control, not an optimization: with rename detection off, a rename
always appears as one `D` (old path) plus one `A` (new path) entry rather
than a single `R` status that could be mistaken for a benign modify. `D` is
never an allowed status here, so a rename denies itself through the ordinary
add/modify rule with no special-case code to keep correct (AC-6).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess


GIT = "/usr/bin/git"
FULL_OID = re.compile(r"[0-9a-f]{40}")
TASK_COLUMNS = ("id", "crew", "size", "spec_ref", "base_sha")

REGULAR_FILE_MODE = "100644"
ELIGIBLE_EXACT_PATHS = frozenset({
    ".omni/STATUS.md", ".omni/ledger.md", ".omni/tech-debt.md",
})
ELIGIBLE_REPORTS_PREFIX = ".omni/reports/"
REPORT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}.*\.md$")
STATUS_HEADING = re.compile(r"^# .*\d{2}:\d{2}Z.*\d{4}-\d{2}-\d{2}")

# Deliberately generous on file COUNT (a bookkeeping PR is inherently small,
# but the cap exists to bound worst-case classify cost, not to narrow
# eligibility -- eligibility is decided entirely by path/mode/status below).
MAX_FILES = 64


class BookkeepingDenied(RuntimeError):
    """A fail-closed refusal: no plan could be produced at all."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _env():
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


def _git(repo, *args, text=False):
    """Run git, raising BookkeepingDenied on any nonzero exit.

    Only for calls whose nonzero exit always means "cannot proceed" (rev-parse,
    diff, cat-file, merge-base). `merge-tree`'s nonzero exit means "conflict",
    a real answer, not a failure -- that call goes through `_git_capture`.
    """
    try:
        proc = subprocess.run(
            [GIT, "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
             "-C", repo, *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False, env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BookkeepingDenied("git_unreadable") from exc
    if proc.returncode != 0:
        raise BookkeepingDenied("git_unreadable")
    return proc.stdout.decode() if text else proc.stdout


def _git_capture(repo, *args):
    """Run git and return (returncode, stdout, stderr) without raising."""
    try:
        proc = subprocess.run(
            [GIT, "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
             "-C", repo, *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False, env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BookkeepingDenied("git_unreadable") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _exact_commit(repo, oid, reason):
    if not isinstance(oid, str) or not FULL_OID.fullmatch(oid):
        raise BookkeepingDenied(reason)
    resolved = _git(repo, "rev-parse", "--verify", "--end-of-options",
                     oid + "^{commit}", text=True).strip()
    if resolved != oid:
        raise BookkeepingDenied(reason)
    return resolved


def _path_safe(path):
    if (path is None or path.startswith("/") or "\x00" in path
            or ".." in path.split("/")):
        raise BookkeepingDenied("path_unsafe")
    return path


def _read_task(cx, task_id):
    import sqlite3
    if not isinstance(task_id, str) or not task_id:
        raise BookkeepingDenied("task_missing")
    try:
        cx.execute("BEGIN")
        row = cx.execute(
            "SELECT id,crew,size,spec_ref,base_sha FROM tasks WHERE id=?",
            (task_id,)).fetchone()
        cx.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            cx.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise BookkeepingDenied("control_db_unreadable") from exc
    if row is None:
        raise BookkeepingDenied("task_missing")
    return {column: row[column] for column in TASK_COLUMNS}


def _target(repo, base, head):
    import os
    repo = os.path.realpath(repo)
    if not os.path.isdir(repo):
        raise BookkeepingDenied("target_repo_unreadable")
    top = _git(repo, "rev-parse", "--show-toplevel", text=True).strip()
    if os.path.realpath(top) != repo:
        raise BookkeepingDenied("target_repo_unreadable")
    _exact_commit(repo, base, "base_invalid")
    _exact_commit(repo, head, "head_invalid")
    code, _out, _err = _git_capture(repo, "merge-base", "--is-ancestor", base, head)
    if code != 0:
        raise BookkeepingDenied("head_not_descendant")
    base_tree = _git(repo, "rev-parse", f"{base}^{{tree}}", text=True).strip()
    head_tree = _git(repo, "rev-parse", f"{head}^{{tree}}", text=True).strip()
    if not FULL_OID.fullmatch(base_tree) or not FULL_OID.fullmatch(head_tree):
        raise BookkeepingDenied("git_object_unreadable")
    return repo, base_tree, head_tree


def _changes(repo, base, head, max_files=MAX_FILES):
    """Raw diff entries, rename/copy detection OFF (see module docstring)."""
    raw = _git(repo, "diff", "--raw", "-z", "--no-abbrev", "--no-ext-diff",
               "--no-textconv", "--no-renames", base, head)
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    entries, index = [], 0
    try:
        while index < len(tokens):
            header = tokens[index].decode("ascii")
            index += 1
            match = re.fullmatch(
                r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}) "
                r"([0-9a-f]{40}) ([ACDMRT])(\d*)", header)
            if not match or index >= len(tokens):
                raise ValueError
            old_mode, new_mode, old_oid, new_oid, status, _score = match.groups()
            path = tokens[index].decode("utf-8")
            index += 1
            _path_safe(path)
            entries.append({
                "status": status, "path": path,
                "old_mode": None if status == "A" else old_mode,
                "new_mode": None if status == "D" else new_mode,
                "old_blob_oid": None if status == "A" else old_oid,
                "new_blob_oid": None if status == "D" else new_oid,
            })
            if len(entries) > max_files:
                raise BookkeepingDenied("diff_too_large")
    except (UnicodeDecodeError, ValueError) as exc:
        raise BookkeepingDenied("git_diff_unreadable") from exc
    entries.sort(key=lambda row: row["path"])
    return entries


def classify_paths(entries):
    """Mechanical, fail-closed bookkeeping-only vs normal classification.

    Returns (classification, reasons). `reasons` is empty iff classification
    is "bookkeeping-only". Every reason names the offending path so a denial
    is auditable, never just "no".
    """
    if not entries:
        return "normal", ["empty_diff"]
    reasons = []
    for entry in entries:
        path, status = entry["path"], entry["status"]
        if status not in ("A", "M"):
            reasons.append(f"non_add_modify_status:{status}:{path}")
            continue
        eligible_path = (path in ELIGIBLE_EXACT_PATHS
                          or path.startswith(ELIGIBLE_REPORTS_PREFIX))
        if not eligible_path:
            reasons.append(f"path_not_allowlisted:{path}")
            continue
        for mode in (entry["old_mode"], entry["new_mode"]):
            if mode is not None and mode != REGULAR_FILE_MODE:
                reasons.append(f"non_regular_mode:{path}:{mode}")
                break
    if reasons:
        return "normal", reasons
    return "bookkeeping-only", []


def _blob(repo, oid):
    if oid is None:
        return b""
    return _git(repo, "cat-file", "-p", oid)


def check_ledger_append_only(repo, entries):
    """`.omni/ledger.md` (AC-2/AC-6): every edit must be a pure append -- the
    OLD blob's bytes must be an exact prefix of the NEW blob's bytes. Nothing
    removed, nothing reordered, nothing edited in place."""
    for entry in entries:
        if entry["path"] != ".omni/ledger.md" or entry["status"] != "M":
            continue
        old, new = _blob(repo, entry["old_blob_oid"]), _blob(repo, entry["new_blob_oid"])
        if not new.startswith(old):
            return False, "ledger_not_append_only"
    return True, None


def check_tech_debt_append_only(repo, entries):
    """`.omni/tech-debt.md` is a register, same append-only invariant as the
    ledger (rows are added, never edited or reordered)."""
    for entry in entries:
        if entry["path"] != ".omni/tech-debt.md" or entry["status"] != "M":
            continue
        old, new = _blob(repo, entry["old_blob_oid"]), _blob(repo, entry["new_blob_oid"])
        if not new.startswith(old):
            return False, "tech_debt_not_append_only"
    return True, None


def check_status_format(repo, entries):
    """`.omni/STATUS.md` is fully overwritten each heartbeat (not append-only)
    but must keep its heading shape: `# <marker> HH:MMZ (YYYY-MM-DD) -- ...`."""
    for entry in entries:
        if entry["path"] != ".omni/STATUS.md":
            continue
        new = _blob(repo, entry["new_blob_oid"])
        try:
            text = new.decode("utf-8")
        except UnicodeDecodeError:
            return False, "status_not_utf8"
        lines = text.splitlines()
        if not lines or not STATUS_HEADING.match(lines[0]):
            return False, "status_heading_malformed"
    return True, None


def check_report_format(repo, entries):
    """`.omni/reports/**`: filename must be date-prefixed, content must be
    valid UTF-8 markdown starting with a heading, and reports never nest into
    subdirectories."""
    for entry in entries:
        if not entry["path"].startswith(ELIGIBLE_REPORTS_PREFIX):
            continue
        name = entry["path"][len(ELIGIBLE_REPORTS_PREFIX):]
        if "/" in name or not REPORT_NAME.match(name):
            return False, f"report_filename_malformed:{entry['path']}"
        new = _blob(repo, entry["new_blob_oid"])
        try:
            text = new.decode("utf-8")
        except UnicodeDecodeError:
            return False, f"report_not_utf8:{entry['path']}"
        if not text.lstrip().startswith("# "):
            return False, f"report_heading_malformed:{entry['path']}"
    return True, None


def check_clean_mergeability(repo, head, main_sha):
    """AC-2/AC-6: the head must merge cleanly onto the CURRENT tip of `main`
    Release is about to write to -- not just onto the wave's stale base_sha.
    Skipped (reported clean) only when the caller genuinely has no current
    main tip to check against yet; Release always supplies one."""
    if main_sha is None:
        return True, None
    _exact_commit(repo, main_sha, "main_invalid")
    code, mb_out, _err = _git_capture(repo, "merge-base", head, main_sha)
    if code != 0:
        return False, "no_common_ancestor"
    merge_base = mb_out.decode().strip()
    if not FULL_OID.fullmatch(merge_base):
        return False, "no_common_ancestor"
    code, _out, _err = _git_capture(
        repo, "merge-tree", "--write-tree", "--merge-base", merge_base,
        main_sha, head)
    if code != 0:
        return False, "merge_conflict"
    return True, None


def check_head_identity(expected_head, actual_head):
    """AC-6: a head that changed between classification and the moment
    Release is about to merge must refuse the fast path -- this is the cheap,
    no-git-required re-check Release runs immediately before cutover."""
    if not FULL_OID.fullmatch(expected_head or ""):
        return False, "expected_head_sha_invalid"
    if not FULL_OID.fullmatch(actual_head or ""):
        return False, "actual_head_sha_invalid"
    if expected_head != actual_head:
        return False, "head_changed_since_classification"
    return True, None


CHECKS = (
    ("ledger_append_only", check_ledger_append_only),
    ("tech_debt_append_only", check_tech_debt_append_only),
    ("status_format", check_status_format),
    ("report_format", check_report_format),
)


def build_bookkeeping_plan(cx, task_id, repo, head_sha, main_sha=None):
    """The one entry point. Mirrors `nightshift_review_plan.build_plan`'s
    shape: read the task's base_sha, resolve exact commits, diff, classify,
    and -- only when classification is "bookkeeping-only" -- run the focused
    deterministic checks bundle (AC-2). `fast_path_eligible` is the single
    field a caller needs to branch on; everything else is the audit trail."""
    task = _read_task(cx, task_id)
    base = task["base_sha"]
    if not isinstance(base, str) or not FULL_OID.fullmatch(base):
        raise BookkeepingDenied("base_invalid")
    repo, base_tree, head_tree = _target(repo, base, head_sha)
    entries = _changes(repo, base, head_sha)
    classification, reasons = classify_paths(entries)
    reasons = list(reasons)

    checks = {}
    if classification == "bookkeeping-only":
        for name, fn in CHECKS:
            ok, reason = fn(repo, entries)
            checks[name] = {"ok": ok, "reason": reason}
            if not ok:
                reasons.append(f"{name}:{reason}")
        ok, reason = check_clean_mergeability(repo, head_sha, main_sha)
        checks["clean_mergeability"] = {"ok": ok, "reason": reason}
        if not ok:
            reasons.append(f"clean_mergeability:{reason}")

    fast_path_eligible = (classification == "bookkeeping-only"
                           and bool(checks)
                           and all(c["ok"] for c in checks.values()))
    plan = {
        "schema_version": 1,
        "task": task,
        "target": {
            "base_sha": base, "base_tree_oid": base_tree,
            "head_sha": head_sha, "head_tree_oid": head_tree,
            "main_sha": main_sha,
            "changes": entries,
        },
        "classification": classification,
        "reasons": reasons,
        "checks": checks,
        "fast_path_eligible": fast_path_eligible,
    }
    plan["plan_digest"] = _digest(canonical_json(plan))
    return plan
