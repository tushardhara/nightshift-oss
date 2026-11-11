#!/usr/bin/env python3
"""Nightshift's own tracker binding -- where FOLLOW_UP issues are filed.

WHY THIS EXISTS
================
Every `gh issue` write in this codebase (`program/agents/*/config.yaml`'s
pm/beta_pm seats, `program/skills/tracker/SKILL.md`, and
`bin/nightshift-tracker-init.sh`) has historically run bare `gh issue
create`/`edit`/`comment`/`close` with no `--repo`. `gh` resolves the target
repository from the CURRENT WORKING DIRECTORY. A worker operating inside a
repository Nightshift does not own (a customer checkout, opened to work a
task there) therefore files Nightshift's own internal FOLLOW_UP tickets --
ADR-0057 mandates one per non-blocking finding, historically ~3.1 per pull
request -- into the CUSTOMER'S issue tracker: public, noisy, and hard to
retract.

This module is the fix's addressable-state half: a write-once record of
which repository IS Nightshift's own tracker, so that record -- never the
process's cwd, never `git remote`, never a literal repository name baked
into this file -- is what an issue-write policy (see
`deploy/policies/nightshift_issue_fence.py`) and the sanctioned wrapper
(`bin/nightshift-file-issue`) both resolve against.

WHY A SEPARATE FILE, NOT A NEW TABLE IN `authority.db`
=======================================================
`nightshift_authority.py`'s `repository_ownership` table already answers a
related-but-different question -- "does Nightshift own the repository it is
CURRENTLY OPERATING ON" (ADR-0085, the merge fence). This module answers a
different one -- "which repository is Nightshift's OWN issue tracker,
period, regardless of what repository is currently checked out". Folding
the two into one table would invite exactly the kind of "two axes read as
one flag" mistake ADR-0085's own docstring warns against for
`active_backend` vs `ownership`. So this is its own registry file, `tracker.db`,
written beside `authority.db` under the SAME per-repository directory
(`<state_root>/authority/<repository_id>/`) so one operator bootstrap
sequence and one runbook covers both, addressed by the SAME two environment
variables `nightshift_authority.scope_from_env` already reads --
`NIGHTSHIFT_STATE_ROOT` and `NIGHTSHIFT_REPOSITORY_ID` -- and nothing else.

NO LITERAL REPOSITORY SLUG LIVES IN THIS FILE (#439: Core carries no
project-specific values). The tracker's `owner/name` arrives exclusively
through an operator's `declare` call, the same way `NIGHTSHIFT_REPOSITORY_ID`
arrives from a root-owned, 0600 systemd `EnvironmentFile` -- never hardcoded,
never read from `git remote`, never read from `.omni/tracker.json` (which is
itself derived from `gh repo view` at whatever directory happens to be
checked out -- see `bin/nightshift-tracker-init.sh` -- and is therefore
exactly the cwd-dependent signal this module exists to NOT be).

WRITE-ONCE, FAIL CLOSED, NO ESCAPE HATCH
=========================================
The binding row is write-once (the same idiom `repository_ownership` uses:
BEFORE-UPDATE/DELETE triggers that abort unconditionally). `resolve_tracker`
never raises; every non-declared, absent, unbootstrapped, or unaddressable
state resolves to `tracker: None`, and the issue-write fence treats a `None`
tracker as "deny every issue write" -- there is no fallback default tracker
name anywhere in this module, because a fallback is exactly the kind of
literal repository value #439 forbids and the kind of guess that could
silently point at the wrong repository.

HONEST LIMIT, disclosed not hidden (same one `nightshift_authority.py`
states for `authority.db`): workers run as the same uid that owns
`state_root` (mode 0700), so a compromised worker process can create,
delete, or overwrite `<state_root>/authority/<repository_id>/tracker.db`
the same as any other file under its own uid. This registry is therefore
NOT tamper-proof against a compromised worker uid; what it removes is cwd
dependence and prompt-text-only enforcement, not a fully adversarial worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

_BIN_DIR = str(Path(__file__).resolve().parent)
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

import nightshift_authority as _auth  # noqa: E402 -- shared scope/dir facts only
from nightshift_project import OWNER_RE, REPOSITORY_RE  # noqa: E402


#: The SAME directory `nightshift_authority.py` addresses
#: (`<state_root>/authority/<repository_id>/`) -- a shared fact, imported as
#: data, not duplicated as a literal, so the two registries can never drift
#: onto different directories.
AUTHORITY_DIRNAME = _auth.AUTHORITY_DIRNAME
TRACKER_DB_FILENAME = "tracker.db"

#: Same numeric bound as `nightshift_authority.MAX_ID`, imported rather than
#: re-typed so the two modules can never silently diverge on it.
MAX_ID = _auth.MAX_ID

ENV_STATE_ROOT = _auth.ENV_STATE_ROOT
ENV_REPOSITORY_ID = _auth.ENV_REPOSITORY_ID
scope_from_env = _auth.scope_from_env


class TrackerAuthorityError(Exception):
    """This module's OWN exception type -- deliberately not
    `nightshift_authority.AuthorityError`. The two registries share a
    directory convention, never an exception hierarchy; a future change to
    one module's error shape must not silently ripple into the other's."""

    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise TrackerAuthorityError(reason_code, 3, details)


def _failed(reason_code, details=None):
    raise TrackerAuthorityError(reason_code, 1, details)


def validate_repository_id(value):
    if (isinstance(value, bool) or not isinstance(value, int)
            or value <= 0 or value > MAX_ID):
        _refuse("invalid_repository_id")
    return value


def _validate_abs_dir(path, reason_code):
    if (not isinstance(path, str) or not path
            or len(path.encode("utf-8")) > 4096
            or not path.startswith("/")
            or path != os.path.normpath(path)
            or path.startswith("//")):
        _refuse(reason_code)
    return path


def _validate_text(value, reason_code, *, maximum=200):
    if (not isinstance(value, str) or not value
            or len(value.encode("utf-8")) > maximum
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)):
        _refuse(reason_code)
    return value


def normalize_owner_name(owner, name):
    """Validate `owner`/`name` against the same regexes
    `nightshift_project._parse_repository` enforces for every other
    repository string this codebase accepts, and lower-case them for
    case-insensitive comparison. Refuses, never silently truncates."""
    if (not isinstance(owner, str) or not OWNER_RE.fullmatch(owner)
            or not isinstance(name, str) or not REPOSITORY_RE.fullmatch(name)
            or name in (".", "..") or name.endswith(".git")):
        _refuse("invalid_tracker_slug", {"owner": owner, "name": name})
    return owner.lower(), name.lower()


def parse_tracker_slug(value):
    """Parse `owner/name` or `https://github.com/owner/name[.git][/]` into a
    lower-cased `(owner, name)` pair. Returns `None` (never raises) on
    anything else -- callers that must refuse loudly call
    `normalize_owner_name` on the result themselves."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.lower().startswith("https://github.com/"):
        rest = text[len("https://github.com/"):]
        rest = rest.rstrip("/")
        if rest.endswith(".git"):
            rest = rest[: -len(".git")]
        if rest.count("/") != 1:
            return None
        owner, name = rest.split("/", 1)
    elif text.count("/") == 1:
        owner, name = text.split("/", 1)
    else:
        return None
    if not OWNER_RE.fullmatch(owner) or not REPOSITORY_RE.fullmatch(name):
        return None
    return owner.lower(), name.lower()


# ---------------------------------------------------------------------------
# Schema -- write-once, same idiom as `nightshift_authority.repository_ownership`
# ---------------------------------------------------------------------------

TRACKER_SCHEMA = """
PRAGMA journal_mode=WAL;

-- `repository_id` is declared with NO type name on purpose, exactly like
-- `nightshift_authority.repository_ownership` -- see that module's schema
-- comment for the full explanation (SQLite INTEGER affinity would coerce a
-- string before a `typeof()` CHECK on an INTEGER column ever ran; a
-- type-less, BLOB-affinity column performs no such conversion).
CREATE TABLE IF NOT EXISTS tracker_binding(
  id INTEGER PRIMARY KEY CHECK(id=1),
  repository_id NOT NULL CHECK(typeof(repository_id)='integer'
                               AND repository_id>0),
  owner TEXT NOT NULL,
  name TEXT NOT NULL,
  declared_by TEXT NOT NULL,
  recorded_at REAL NOT NULL);

-- Write-once: a tracker binding a compromised or merely mistaken process
-- could quietly repoint is not a fence, it is a suggestion. A genuine
-- change of Nightshift's own tracker repository is an operator act on a
-- fresh registry (documented in the runbook), never an UPDATE on this row.
CREATE TRIGGER IF NOT EXISTS tracker_binding_immutable
BEFORE UPDATE ON tracker_binding FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'tracker: tracker binding is write-once');
END;

CREATE TRIGGER IF NOT EXISTS tracker_binding_no_delete
BEFORE DELETE ON tracker_binding FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'tracker: tracker binding is write-once');
END;
"""


# ---------------------------------------------------------------------------
# Paths and connection -- same directory nightshift_authority.py uses, a
# different filename inside it.
# ---------------------------------------------------------------------------

def tracker_dir(state_root, repository_id):
    _validate_abs_dir(state_root, "invalid_state_root")
    validate_repository_id(repository_id)
    return os.path.join(state_root, AUTHORITY_DIRNAME, str(repository_id))


def tracker_bootstrap_path(state_root, repository_id):
    return os.path.join(tracker_dir(state_root, repository_id), TRACKER_DB_FILENAME)


def registry_exists(state_root, repository_id):
    return os.path.isfile(tracker_bootstrap_path(state_root, repository_id))


def connect(state_root, repository_id, *, create=True, ensure_schema=True):
    path = tracker_bootstrap_path(state_root, repository_id)
    directory = os.path.dirname(path)
    if not create and not os.path.isfile(path):
        _refuse("tracker_registry_missing", {"expected_path": path})
    if create or ensure_schema:
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
        except OSError as exc:
            _failed("tracker_registry_unwritable",
                    {"path": directory, "error": str(exc)})
    cx = sqlite3.connect(path, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    if ensure_schema:
        cx.executescript(TRACKER_SCHEMA)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return cx


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def declare_tracker(cx, *, repository_id, owner, name, declared_by, t=None):
    """Write-once bootstrap of Nightshift's own tracker repository.

    Idempotent on an identical re-declare (same `owner`/`name`, case folded)
    -- same posture as `nightshift_authority.declare_repository_ownership`.
    A re-declare naming a DIFFERENT repository is refused
    `tracker_binding_already_declared`: reversing a declaration means
    removing the registry file (an operator act), never rewriting this row.
    """
    validate_repository_id(repository_id)
    owner, name = normalize_owner_name(owner, name)
    _validate_text(declared_by, "invalid_declared_by")
    t = time.time() if t is None else float(t)
    existing = read_tracker_binding(cx)
    if existing is not None:
        if (existing["repository_id"] != repository_id
                or existing["owner"] != owner or existing["name"] != name):
            _refuse("tracker_binding_already_declared", {
                "declared_repository_id": existing["repository_id"],
                "declared_tracker": f"{existing['owner']}/{existing['name']}",
                "presented_repository_id": repository_id,
                "presented_tracker": f"{owner}/{name}"})
        return existing
    cx.execute(
        "INSERT INTO tracker_binding(id, repository_id, owner, name, "
        "declared_by, recorded_at) VALUES(1, ?, ?, ?, ?, ?)",
        (repository_id, owner, name, declared_by, t))
    return read_tracker_binding(cx)


def read_tracker_binding(cx):
    """The declared tracker row, or `None`. `None` covers both "no
    `tracker_binding` table at all" and "the table exists but nothing has
    been declared into it yet" -- same `try/except sqlite3.OperationalError`
    idiom `nightshift_authority.read_repository_ownership` uses."""
    try:
        row = cx.execute(
            "SELECT * FROM tracker_binding WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else dict(row)


def resolve_tracker(state_root, repository_id):
    """Resolve Nightshift's own tracker repository, reading `tracker.db`
    ALONE and never raising. Mirrors
    `nightshift_authority.classify_repository`'s contract exactly.

    Returns `{"tracker": "owner/name"|None, "source": ..., "repository_id":
    ...}`. `source` is one of the same four values `classify_repository`
    uses, for the same reasons:
      registry               a present, declared row was read
      registry_absent        no `tracker.db` file exists at this scope
      registry_unbootstrapped  the file exists but no row was ever declared
      unaddressable           `state_root`/`repository_id` do not name a
                              registry at all
    """
    if state_root is None or repository_id is None:
        return {"tracker": None, "source": "unaddressable",
                "repository_id": repository_id}
    try:
        validate_repository_id(repository_id)
        path = tracker_bootstrap_path(state_root, repository_id)
    except TrackerAuthorityError:
        return {"tracker": None, "source": "unaddressable",
                "repository_id": repository_id}
    if not os.path.isfile(path):
        return {"tracker": None, "source": "registry_absent",
                "repository_id": repository_id}
    try:
        cx = connect(state_root, repository_id, create=False, ensure_schema=False)
        try:
            row = read_tracker_binding(cx)
        finally:
            cx.close()
    except (sqlite3.Error, TrackerAuthorityError, OSError):
        # A registry that cannot be opened or read is exactly as
        # unaddressable as one that is not there -- fail closed.
        return {"tracker": None, "source": "unaddressable",
                "repository_id": repository_id}
    if row is None:
        return {"tracker": None, "source": "registry_unbootstrapped",
                "repository_id": repository_id}
    owner, name = row.get("owner"), row.get("name")
    tracker = f"{owner}/{name}" if isinstance(owner, str) and isinstance(name, str) else None
    return {"tracker": tracker, "source": "registry",
            "repository_id": repository_id}


TRACKER_UNBOUND_REASON = "tracker_unbound"


def require_tracker(state_root, repository_id):
    """THE resolution gate other code should call when it needs the tracker
    slug or nothing at all -- refuses (exit 3, `TRACKER_UNBOUND_REASON`)
    rather than ever returning a guessed value."""
    resolved = resolve_tracker(state_root, repository_id)
    if resolved["tracker"] is None:
        _refuse(TRACKER_UNBOUND_REASON, resolved)
    return resolved


# ---------------------------------------------------------------------------
# CLI -- own CLI, same posture as `nightshift_authority.py`'s tail: callable
# directly by any process (the wrapper, the init script, an operator
# one-liner) without going through `bin/nightshift-ctl`.
# ---------------------------------------------------------------------------

def out(obj, code=0):
    print(json.dumps(obj, sort_keys=True))
    sys.exit(code)


def _run_tracker_command(fn, args):
    try:
        result = fn(args)
    except TrackerAuthorityError as exc:
        out(error_payload(exc), exc.exit_code)
    except Exception:
        out({"ok": False, "reason_code": "tracker_operation_failed"}, 1)
    out(result)


def cmd_declare(args):
    parsed = parse_tracker_slug(args.tracker)
    if parsed is None:
        _refuse("invalid_tracker_slug", {"tracker": args.tracker})
    owner, name = parsed
    cx = connect(args.state_root, args.repository_id)
    try:
        row = declare_tracker(
            cx, repository_id=args.repository_id, owner=owner, name=name,
            declared_by=args.declared_by, t=args.now)
        return {"ok": True, **row}
    finally:
        cx.close()


def cmd_show(args):
    resolved = resolve_tracker(args.state_root, args.repository_id)
    return {"ok": True, **resolved}


def build_parser():
    p = argparse.ArgumentParser(
        prog="nightshift_tracker_authority",
        description="Write-once binding of Nightshift's own tracker repository")
    p.add_argument("--now", type=float, default=None,
                   help="override the clock, epoch seconds (test seam, argv only)")
    sub = p.add_subparsers(dest="group", required=True)

    def common(sp):
        sp.add_argument("--state-root", required=True)
        sp.add_argument("--repository-id", required=True, type=int)

    d = sub.add_parser("declare", help="write-once bootstrap of the tracker repository")
    common(d)
    d.add_argument("--tracker", required=True, help="owner/name or a github.com URL")
    d.add_argument("--declared-by", required=True,
                   help="operator identity recorded on the write-once row")
    d.set_defaults(func=cmd_declare)

    s = sub.add_parser("show", help="read the declared tracker, or the reason it is absent")
    common(s)
    s.set_defaults(func=cmd_show)

    g = sub.add_parser("slug", help="print owner/name on stdout; exit 3 if unbound")
    common(g)
    # `slug`'s own success/failure output shape is special-cased in `main()`
    # (bare stdout slug, no JSON envelope) so it can be captured with plain
    # `$(...)` in a shell script; it has no `func` handler of its own.

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.group == "slug":
        # `slug` is meant for `$(...)` capture in shell scripts: bare stdout
        # on success, nothing on stdout on failure (the JSON error goes to
        # the same place `out()` always prints it -- this CLI has no
        # separate stderr channel, so a caller doing `X=$(... slug)` and
        # checking `$?` is the supported contract, not scraping stdout on
        # failure).
        try:
            resolved = require_tracker(args.state_root, args.repository_id)
        except TrackerAuthorityError as exc:
            print(json.dumps(error_payload(exc), sort_keys=True), file=sys.stderr)
            sys.exit(exc.exit_code)
        print(resolved["tracker"])
        sys.exit(0)
    _run_tracker_command(args.func, args)


if __name__ == "__main__":
    main()
