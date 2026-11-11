#!/usr/bin/env python3
"""Issue #400 / #694 -- ONE tenant identity, shared by every adapter.

WHY THIS MODULE EXISTS
======================
Encryption keys (ADR-0080) and authority epochs (ADR-0082) both have to be
scoped to "a tenant". Before they can be, this repository has to agree on
what a tenant *is*.

Two answers were live in the tree:

  * `nightshift_provenance.validate_tenant_id` (:164) refuses anything that
    is not a positive `int`, SPECIFICALLY so `"../other_tenant"` can never
    become a directory name in `resolve_publication_root`'s path join.
  * `nightshift_publication` recorded a free-text tenant identity, and its
    tests passed the literal `"tenant-alpha"`.

The ADR-0080 amendment settled the *storage* type: INTEGER is canonical for
the local profile, with `prov.validate_tenant_id` as the one validator both
modules call. That change has already landed in the working tree -- every
tenant-bearing column in BOTH schemas is now
`INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0)`.

What the amendment did NOT provide is the other half: humans, policies,
contracts and support tickets name a tenant with a STRING ("tenant-alpha"),
and something has to map that string onto the integer surrogate without
ever letting the string reach a path join. That mapping is this module.

  tenant_uid  TEXT   -- the external name. Never a path segment. Never
                        joined into a filesystem path by anything here.
  tenant_id   INTEGER -- the surrogate. The ONLY value that reaches
                        `os.path.join`, and it is machine-allocated by
                        SQLite's AUTOINCREMENT, so it is a positive int by
                        construction and cannot be attacker-chosen.

DELIBERATELY NOT A MIGRATION
============================
No existing column changes type and no existing test is edited. The
registry is a NEW database (`<state_root>/tenants/registry.db`) sitting
beside the per-tenant directories `prov.resolve_publication_root` already
creates. Adapters keep taking `tenant_id` exactly as they do today; this
module is what an operator-facing caller uses to turn a name into that id
once, at the edge.

WHY AUTOINCREMENT AND NOT `MAX(id)+1`
=====================================
`AUTOINCREMENT` keeps a monotone high-water mark in `sqlite_sequence`, so a
deleted tenant's id is NEVER reissued. Reissuing an id would silently point
a NEW tenant at a DELETED tenant's on-disk directory
(`tenants/<tenant_id>/`), its retained backups and -- worse -- its wrapped
data-encryption keys. That is a cross-tenant data leak dressed up as an id
allocation, and `AUTOINCREMENT` is the one-word defence against it.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import unicodedata

TENANT_REGISTRY_DIRNAME = "tenants"
TENANT_REGISTRY_FILENAME = "registry.db"

MAX_TENANT_UID_BYTES = 200

TENANT_STATUSES = ("active", "deleting", "deleted")


class TenantError(Exception):
    """Same error shape as ProvenanceError / PublicationError /
    MemoryStoreError, so a CLI boundary can catch all four identically."""

    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise TenantError(reason_code, 3, details)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

TENANT_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tenant(
  tenant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_uid TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('active','deleting','deleted')),
  created_at REAL NOT NULL);

-- Status is a one-way ratchet: active -> deleting -> deleted. A tenant
-- cannot be resurrected, because ADR-0080's deletion path DESTROYS the
-- tenant KEK, and no status update can bring a destroyed key back. Letting
-- 'deleted' walk back to 'active' would produce a tenant whose rows are
-- unreadable ciphertext and whose operator believes it is live.
CREATE TRIGGER IF NOT EXISTS tenant_status_is_one_way
BEFORE UPDATE ON tenant FOR EACH ROW
WHEN NOT (
  new.tenant_id IS old.tenant_id
  AND new.tenant_uid IS old.tenant_uid
  AND new.created_at IS old.created_at
  AND (
       (old.status = 'active'   AND new.status IN ('active','deleting'))
    OR (old.status = 'deleting' AND new.status IN ('deleting','deleted'))
    OR (old.status = 'deleted'  AND new.status = 'deleted')
  )
)
BEGIN
  SELECT RAISE(ABORT, 'tenant: status is a one-way ratchet active->deleting->deleted');
END;

-- A tenant row is never deleted. The registry is the record of WHICH ids
-- have been issued; dropping the row would let AUTOINCREMENT's high-water
-- mark be the only survivor of that fact, and a corrupted sqlite_sequence
-- would then silently reissue a dead tenant's directory.
CREATE TRIGGER IF NOT EXISTS tenant_row_is_never_deleted
BEFORE DELETE ON tenant FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'tenant: tenant rows are never deleted, only marked deleted');
END;
"""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_UID_ALLOWED = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


def canonicalize_uid(value):
    """NFC-normalize, strip, casefold -- the SAME canonical form
    `nightshift_publication.canonicalize_identity` applies to reviewer
    identities. Without it `Tenant-Alpha` and `tenant-alpha` would be two
    tenants with two directories and two key sets, and an operator deleting
    one would believe both were gone.
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value)).strip().casefold()


def validate_tenant_uid(value):
    """The external name never reaches a path join, but it is still refused
    strictly: an unbounded or control-character-bearing uid ends up in log
    lines, JSON receipts and error payloads, and `..` in a name that LOOKS
    like a directory invites a future caller to treat it as one."""
    if not isinstance(value, str):
        _refuse("invalid_tenant_uid")
    canonical = canonicalize_uid(value)
    if (not canonical
            or len(canonical.encode("utf-8")) > MAX_TENANT_UID_BYTES
            or not _UID_ALLOWED.match(canonical)
            or canonical in (".", "..")
            or ".." in canonical):
        _refuse("invalid_tenant_uid")
    return canonical


def validate_tenant_id(value):
    """Identical strictness to `prov.validate_tenant_id`, restated here so
    this module has no import edge into provenance (provenance is a heavy
    module that shells out to git; the registry must be openable from a
    key-management or migration context that has no git at all).
    A bool is refused explicitly: `isinstance(True, int)` is True in Python,
    so `True == 1` would silently resolve to tenant 1."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or value <= 0 or value > (1 << 63) - 1):
        _refuse("invalid_tenant_id")
    return value


def _validate_state_root(path):
    if (not isinstance(path, str) or not path
            or len(path.encode("utf-8")) > 4096
            or not path.startswith("/")
            or path != os.path.normpath(path)
            or path.startswith("//")):
        _refuse("invalid_state_root")
    return path


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def registry_path(state_root):
    _validate_state_root(state_root)
    return os.path.join(state_root, TENANT_REGISTRY_DIRNAME,
                        TENANT_REGISTRY_FILENAME)


def connect(state_root, *, create=True):
    """Open `<state_root>/tenants/registry.db` at mode 0700.

    `tenants/` is the SAME directory `prov.resolve_publication_root` creates
    and chmods to 0700; opening the registry must not weaken it, so the mode
    is forced here too rather than left to the umask.
    """
    path = registry_path(state_root)
    directory = os.path.dirname(path)
    if not create and not os.path.isfile(path):
        _refuse("tenant_registry_missing", {"expected_path": path})
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise TenantError("tenant_registry_unwritable", 1,
                          {"path": directory, "error": str(exc)}) from exc
    cx = sqlite3.connect(path, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    cx.execute("PRAGMA secure_delete=ON")
    cx.executescript(TENANT_SCHEMA)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return cx


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def register(cx, tenant_uid, t=None):
    """Allocate (or return) the integer surrogate for an external name.

    Idempotent by UID: registering the same name twice returns the same
    `tenant_id` and does NOT reset a `deleting`/`deleted` status. Re-issuing
    an id for a deleted tenant is exactly the leak `AUTOINCREMENT` exists to
    prevent, so the caller is told plainly which status it got back rather
    than being handed a live-looking id.
    """
    canonical = validate_tenant_uid(tenant_uid)
    t = time.time() if t is None else float(t)
    existing = cx.execute(
        "SELECT tenant_id, tenant_uid, status, created_at FROM tenant WHERE tenant_uid=?",
        (canonical,)).fetchone()
    if existing is not None:
        return dict(existing)
    cx.execute(
        "INSERT INTO tenant(tenant_uid, status, created_at) VALUES(?,'active',?)",
        (canonical, t))
    row = cx.execute(
        "SELECT tenant_id, tenant_uid, status, created_at FROM tenant WHERE tenant_uid=?",
        (canonical,)).fetchone()
    return dict(row)


def resolve_id(cx, tenant_uid):
    """External name -> integer surrogate. Refuses an unknown name rather
    than returning None: a None flowing onward would be refused later by
    `validate_tenant_id` anyway, but at a site that cannot say WHICH name
    was unknown."""
    canonical = validate_tenant_uid(tenant_uid)
    row = cx.execute("SELECT tenant_id FROM tenant WHERE tenant_uid=?",
                     (canonical,)).fetchone()
    if row is None:
        _refuse("tenant_uid_unknown", {"tenant_uid": canonical})
    return int(row["tenant_id"])


def resolve_uid(cx, tenant_id):
    validate_tenant_id(tenant_id)
    row = cx.execute("SELECT tenant_uid FROM tenant WHERE tenant_id=?",
                     (tenant_id,)).fetchone()
    if row is None:
        _refuse("tenant_id_unknown", {"tenant_id": tenant_id})
    return row["tenant_uid"]


def get(cx, tenant_id):
    validate_tenant_id(tenant_id)
    row = cx.execute(
        "SELECT tenant_id, tenant_uid, status, created_at FROM tenant WHERE tenant_id=?",
        (tenant_id,)).fetchone()
    if row is None:
        _refuse("tenant_id_unknown", {"tenant_id": tenant_id})
    return dict(row)


def require_active(cx, tenant_id):
    """THE gate every tenant-scoped command calls. ADR-0080: "Tenant
    deletion blocks commands" -- a tenant in `deleting` is already fenced,
    before its keys are destroyed, so no new object can be written that the
    deletion sweep has already walked past."""
    row = get(cx, tenant_id)
    if row["status"] != "active":
        _refuse("tenant_not_active",
                {"tenant_id": tenant_id, "status": row["status"]})
    return row


def set_status(cx, tenant_id, status, t=None):
    """Advance the one-way ratchet. The trigger, not this function, is what
    makes the ratchet true -- a caller with a raw connection cannot walk it
    backwards either."""
    validate_tenant_id(tenant_id)
    if status not in TENANT_STATUSES:
        _refuse("invalid_tenant_status", {"status": status})
    get(cx, tenant_id)
    try:
        cx.execute("UPDATE tenant SET status=? WHERE tenant_id=?", (status, tenant_id))
    except sqlite3.IntegrityError as exc:
        _refuse("tenant_status_transition_refused",
                {"tenant_id": tenant_id, "to": status, "error": str(exc)})
    return get(cx, tenant_id)


def list_tenants(cx):
    return [dict(r) for r in cx.execute(
        "SELECT tenant_id, tenant_uid, status, created_at FROM tenant "
        "ORDER BY tenant_id").fetchall()]
