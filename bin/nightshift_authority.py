#!/usr/bin/env python3
"""ADR-0082 -- the external authority-epoch registry and its one quiesced switch.

WHAT THIS IS
============
ADR-0082's decision, in one sentence: "Use a per-repository external
bootstrap registry with `active_backend` and a monotone `authority_epoch`."
This module is that registry.

WHY IT IS EXTERNAL
==================
`<state_root>/authority/<repository_id>/authority.db` lives outside BOTH of
the stores it arbitrates -- outside `.omni/control.db` (the legacy store) and
outside every per-tenant database under `<state_root>/tenants/`. That is the
whole point. ADR-0082: "A crash consults the bootstrap binding." If the
binding lived inside either store, a crash that damaged that store would
also destroy the only record of which store was authoritative, and an
operator would have to GUESS. Guessing is how you get dual authority, and
the ADR is explicit that "There is never an intentional dual authority."

THE THREE REFUSALS `require_epoch` OWNS
=======================================
  authority_epoch_stale      the client carries an epoch older than the
                             binding: it was started before a switch and
                             still believes the old backend is live.
  authority_backend_mismatch the client is talking to a backend that is not
                             the authority right now. An EPOCH-UNAWARE
                             client (no epoch presented at all) defaults to
                             `legacy`, so it is refused by this same check
                             the instant authority moves to `external` --
                             which is precisely ADR-0082's requirement to
                             stop "legacy processes that do not understand
                             epochs", enforced structurally rather than by
                             remembering to kill them.
  authority_quiesced         a switch or a snapshot is in progress. Phase 2
                             and phase 5 both quiesce; commands wait.

A fourth, `authority_epoch_ahead`, is refused for a client presenting an
epoch NEWER than the binding. ADR-0082 only names the stale direction, but
the newer direction is a real state: it means this process is reading a
registry that has been rolled back or restored from a backup, and running
against it would be running against a store the rest of the fleet has
already left. Failing closed is the only honest answer.

THE ABSENT-REGISTRY RULE (fail-closed, and NOT a vacuous skip)
==============================================================
`guard()` is what the adapters' `connect()` functions call. When NO registry
file exists, the binding is `('legacy', epoch 0)`:

  * a client claiming `external` is REFUSED (`authority_backend_mismatch`),
  * a `legacy` client proceeds.

That is not a convenience default to keep old tests green -- it is
ADR-0082's phase 1-4 posture written down: "Legacy remains the sole
authority through comparison." Absence of a registry IS the legacy binding.
A registry that has never been bootstrapped has never moved authority
anywhere, so the only backend it could name is the one that was already
there. The rule is fail-closed in the direction that matters: you can never
reach the NEW store without an explicit, recorded, monotone flip.

THE SHADOW NAMESPACE DENIES COMMANDS STRUCTURALLY
=================================================
ADR-0082 phase 3 imports "into a namespace that denies live commands".
`nightshift_migrate.shadow_import` writes its data under
`<state_root>/migration/<migration_id>/` and drops a marker file there. That
directory has NO `authority.db` of its own and never gets one. `guard()`
refuses `shadow_namespace_denies_commands` for any path inside it. The
denial is a fact about the filesystem layout -- there is no boolean anywhere
that could be flipped to "allow commands in the shadow", because allowing
them would require creating a second authority binding, which is the dual
authority the ADR forbids.

ADR-0085 -- REPOSITORY OWNERSHIP IS A SECOND, INDEPENDENT AXIS
===============================================================
Everything above answers "which STORE is authoritative for this
repository's own data". `repository_ownership` (see its schema comment)
answers an unrelated question: "does Nightshift own this repository, or is
it operating on someone else's?" Both use the word "external" for
opposite-scale things -- `active_backend='external'` names a storage
backend, `ownership='external'` names a repository Nightshift may open a
pull request against but must NEVER merge into. A future reader must not
collapse these into one flag; a test in this repository asserts they vary
independently.

Ownership is read from `authority.db` and NOTHING ELSE: not `.omni/
control.db`, not any `program/*.yaml` prompt or config string, not a
memory store, not any environment variable other than the two named at
`ENV_STATE_ROOT`/`ENV_REPOSITORY_ID` below. `classify_repository` and
`require_merge_allowed` are the only two functions that answer this
question, and both are pure reads of this one file.

A sibling control plane may keep its own `repository_id`-scoped column for
routing work rows within one plane (see `control.db`'s scope column, owned
by a different stream this same wave) -- that is a PROJECTION, a
convenience copy for a different purpose, and it can never answer "may I
merge". If the two ever disagree, this registry is the one that is right,
because the fence in `deploy/policies/omnipods_policies.py` reads this file
and nothing downstream of it.

Honest limit, not silently dropped: workers run as the same uid that owns
`state_root` (mode 0700, see `nightshift_project._open_state_root`), so a
compromised worker process can create, delete, or overwrite
`<state_root>/authority/<repository_id>/authority.db` the same as any other
file under its own uid, and the startup descriptor that first names a
repository is unsigned. This registry is therefore NOT tamper-proof against
a compromised worker uid. What it removes is the two channels that actually
caused the bug this ADR fixes: the release agent's own PROMPT (a git-tracked
string a worker can edit in a pull request) and the worker's shell
ENVIRONMENT (the policy evaluator's environment comes from a root-written,
0600 systemd `EnvironmentFile`, which a worker's exported variables cannot
reach). See ADR-0085's Consequences for the full statement.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

AUTHORITY_DIRNAME = "authority"
AUTHORITY_DB_FILENAME = "authority.db"

#: Written by `nightshift_migrate.shadow_import` at the root of a shadow
#: namespace. Its presence anywhere at or above a path makes that path
#: command-denying.
SHADOW_MARKER_FILENAME = ".nightshift-shadow-namespace"

#: Written by `nightshift_migrate.snapshot` at the root of the RETAINED,
#: read-only legacy snapshot. ADR-0082: rollback "requires ... replay into a
#: fresh legacy copy ... never reopening the stale files directly", so
#: `rollback_authority` refuses a root carrying this marker.
RETAINED_SNAPSHOT_MARKER = ".nightshift-retained-snapshot"

BACKENDS = ("legacy", "external")

#: ADR-0085's axis. Deliberately spelled `ownership`, never `mode` or
#: `backend` -- see the module-docstring addendum below on why this must
#: never be conflated with `active_backend` above, even though both use the
#: word "external" for an unrelated question.
OWNERSHIP_VALUES = ("self", "external")

PHASES = ("inventory", "snapshot", "shadow_import", "compare", "switch",
          "soak", "decommissioned")

MAX_ID = (1 << 63) - 1

#: Bound on the walk-up that looks for a shadow marker. A path deeper than
#: this is refused outright rather than walked forever.
_MARKER_WALK_LIMIT = 40


class AuthorityError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise AuthorityError(reason_code, 3, details)


def _failed(reason_code, details=None):
    raise AuthorityError(reason_code, 1, details)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_repository_id(value):
    """`repository_id` becomes a directory name in `bootstrap_path`. Same
    isinstance-int check `prov._validate_scope_id` makes, for the same
    reason: `"../other_repo"` cannot survive it."""
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


def _validate_text(value, reason_code, *, maximum=200, allow_none=False):
    if value is None and allow_none:
        return None
    if (not isinstance(value, str) or not value
            or len(value.encode("utf-8")) > maximum
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)):
        _refuse(reason_code)
    return value


def validate_backend(value):
    if value not in BACKENDS:
        _refuse("invalid_backend", {"backend": value})
    return value


def validate_phase(value):
    if value not in PHASES:
        _refuse("invalid_phase", {"phase": value})
    return value


def validate_ownership(value):
    if value not in OWNERSHIP_VALUES:
        _refuse("invalid_ownership", {"ownership": value})
    return value


def _validate_epoch(value, reason_code, *, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < minimum or value > MAX_ID):
        _refuse(reason_code, {"epoch": value})
    return value


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

AUTHORITY_SCHEMA = """
PRAGMA journal_mode=WAL;

-- Exactly one binding per repository, enforced by CHECK(id=1) rather than
-- by a UNIQUE index: a second row is not "unusual", it is a second answer
-- to "which store is authoritative", i.e. the dual authority ADR-0082
-- forbids. It must be unrepresentable, not merely unlikely.
CREATE TABLE IF NOT EXISTS authority_binding(
  id INTEGER PRIMARY KEY CHECK(id=1),
  repository_id INTEGER NOT NULL,
  active_backend TEXT NOT NULL CHECK(active_backend IN ('legacy','external')),
  authority_epoch INTEGER NOT NULL CHECK(authority_epoch>=1),
  phase TEXT NOT NULL CHECK(phase IN ('inventory','snapshot','shadow_import',
                                      'compare','switch','soak','decommissioned')),
  quiesced INTEGER NOT NULL CHECK(quiesced IN (0,1)),
  migration_id TEXT,
  updated_at REAL NOT NULL);

-- Append-only. This is the record a crashed operator reads to answer "did
-- the flip happen?", so a row that can be rewritten answers nothing.
CREATE TABLE IF NOT EXISTS authority_epoch_log(
  epoch INTEGER PRIMARY KEY,
  from_backend TEXT NOT NULL CHECK(from_backend IN ('legacy','external')),
  to_backend TEXT NOT NULL CHECK(to_backend IN ('legacy','external')),
  migration_id TEXT,
  reason TEXT NOT NULL,
  recorded_at REAL NOT NULL);

-- Phase 4's verdict, kept HERE and not in the migration namespace, because
-- `switch_authority` has to read it in the same transaction as the flip. A
-- verdict living in the store being migrated could not be trusted to be
-- readable at the moment the flip needs it.
CREATE TABLE IF NOT EXISTS migration_compare(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_id TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('match','mismatch')),
  mismatch_count INTEGER NOT NULL,
  detail_json TEXT,
  recorded_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS migration_compare_by_migration
  ON migration_compare(migration_id, id);

-- Same BEFORE-UPDATE-partition idiom `source_snapshot_immutable`
-- (nightshift_provenance.py) already establishes: name the transitions that
-- ARE legal and ABORT everything else. Here the invariant is monotonicity --
-- an epoch that can go backwards is an epoch a rolled-back client can
-- satisfy, which reopens exactly the stale-client window the epoch exists
-- to close -- plus the repository binding, which must never be re-pointed
-- at a different repository under the same registry file.
CREATE TRIGGER IF NOT EXISTS authority_binding_monotone
BEFORE UPDATE ON authority_binding FOR EACH ROW
WHEN new.authority_epoch < old.authority_epoch
  OR new.repository_id IS NOT old.repository_id
BEGIN
  SELECT RAISE(ABORT, 'authority: authority_epoch is monotone and repository_id is fixed');
END;

CREATE TRIGGER IF NOT EXISTS authority_epoch_log_immutable
BEFORE UPDATE ON authority_epoch_log FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'authority: authority_epoch_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS authority_epoch_log_no_delete
BEFORE DELETE ON authority_epoch_log FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'authority: authority_epoch_log is append-only');
END;

-- ADR-0085. Repository OWNERSHIP is a DIFFERENT AXIS from `active_backend`
-- above. Both use the word "external"; they answer unrelated questions and
-- must never be read as one. `active_backend='external'` means "the
-- ADR-0082 store is authoritative for reads/writes on THIS repository".
-- `ownership='external'` means "this repository belongs to someone else --
-- Nightshift may open a pull request against it, but must NEVER merge one".
-- A future reader must not "simplify" these into a single flag.
--
-- `repository_id` is declared with NO type name on purpose, unlike
-- `authority_binding.repository_id` above (INTEGER). SQLite INTEGER
-- affinity converts the string '5' to the integer 5 BEFORE any CHECK
-- constraint runs, so the `INTEGER ... CHECK(typeof(x)='integer')` idiom
-- used elsewhere in this codebase (e.g. nightshift_publication.py's
-- tenant_id columns) does NOT reject a string -- it silently coerces it
-- first. A column declared with no type name has BLOB affinity and performs
-- no such conversion, so the CHECK below genuinely refuses a string.
-- Verified directly against sqlite3 before writing this schema.
CREATE TABLE IF NOT EXISTS repository_ownership(
  id INTEGER PRIMARY KEY CHECK(id=1),
  repository_id NOT NULL CHECK(typeof(repository_id)='integer'
                               AND repository_id>0),
  ownership TEXT NOT NULL CHECK(ownership IN ('self','external')),
  declared_by TEXT NOT NULL,
  recorded_at REAL NOT NULL);

-- Write-once, the same way `authority_binding` above is monotone rather
-- than mutable: an ownership declaration a worker could quietly flip back
-- is not a fence, it is a suggestion. A genuine re-classification (a
-- repository changes hands) is an operator act on a fresh registry, not an
-- UPDATE on this one.
CREATE TRIGGER IF NOT EXISTS repository_ownership_immutable
BEFORE UPDATE ON repository_ownership FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'authority: repository ownership is write-once');
END;

CREATE TRIGGER IF NOT EXISTS repository_ownership_no_delete
BEFORE DELETE ON repository_ownership FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'authority: repository ownership is write-once');
END;
"""


# ---------------------------------------------------------------------------
# Paths and connection
# ---------------------------------------------------------------------------

def authority_dir(state_root, repository_id):
    _validate_abs_dir(state_root, "invalid_state_root")
    validate_repository_id(repository_id)
    return os.path.join(state_root, AUTHORITY_DIRNAME, str(repository_id))


def bootstrap_path(state_root, repository_id):
    return os.path.join(authority_dir(state_root, repository_id),
                        AUTHORITY_DB_FILENAME)


def registry_exists(state_root, repository_id):
    return os.path.isfile(bootstrap_path(state_root, repository_id))


def connect(state_root, repository_id, *, create=True, ensure_schema=True):
    """Open this repository's authority registry.

    `ensure_schema=False` is what the CLIENT GATE uses. Running
    `executescript(AUTHORITY_SCHEMA)` on every command would take a write
    lock on a file that is read-mostly by design -- the registry is
    consulted once per command and written only at a bootstrap, a quiesce or
    the one flip. Turning every `connect()` into a writer would put every
    command in this system into contention on the one file whose
    availability decides whether any command may run at all.
    """
    path = bootstrap_path(state_root, repository_id)
    directory = os.path.dirname(path)
    if not create and not os.path.isfile(path):
        _refuse("authority_registry_missing", {"expected_path": path})
    if create or ensure_schema:
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
            os.chmod(os.path.dirname(directory), 0o700)
        except OSError as exc:
            _failed("authority_registry_unwritable",
                    {"path": directory, "error": str(exc)})
    cx = sqlite3.connect(path, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    if ensure_schema:
        cx.executescript(AUTHORITY_SCHEMA)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return cx


# ---------------------------------------------------------------------------
# Shadow namespace
# ---------------------------------------------------------------------------

def shadow_marker_path(root):
    return os.path.join(root, SHADOW_MARKER_FILENAME)


def find_shadow_marker(path):
    """Walk UP from `path` looking for a shadow-namespace marker. Returns the
    marker path or None. Bounded, and stops at the filesystem root."""
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    current = os.path.normpath(path)
    for _ in range(_MARKER_WALK_LIMIT):
        if os.path.isfile(os.path.join(current, SHADOW_MARKER_FILENAME)):
            return os.path.join(current, SHADOW_MARKER_FILENAME)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def assert_not_shadow_namespace(path):
    marker = find_shadow_marker(path)
    if marker is not None:
        _refuse("shadow_namespace_denies_commands", {
            "path": path, "marker": marker,
            "reason": "ADR-0082 phase 3: the shadow-import namespace has no "
                      "authority binding and never accepts live commands",
        })


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def init_binding(cx, *, repository_id, t=None):
    """Seed epoch 1 on the `legacy` backend, in phase `inventory`.

    Epoch starts at 1, not 0, because 0 is reserved for the ABSENT registry
    (see `guard`). Keeping those two distinguishable means "nobody has
    bootstrapped this repository yet" and "this repository is bootstrapped
    and still on legacy" are different, readable states, not one ambiguous
    default.
    """
    validate_repository_id(repository_id)
    t = time.time() if t is None else float(t)
    existing = read_binding(cx)
    if existing is not None:
        if existing["repository_id"] != repository_id:
            _refuse("authority_repository_mismatch", {
                "bound_repository_id": existing["repository_id"],
                "presented_repository_id": repository_id})
        return existing
    cx.execute(
        "INSERT INTO authority_binding(id, repository_id, active_backend, "
        "authority_epoch, phase, quiesced, migration_id, updated_at) "
        "VALUES(1, ?, 'legacy', 1, 'inventory', 0, NULL, ?)",
        (repository_id, t))
    cx.execute(
        "INSERT INTO authority_epoch_log(epoch, from_backend, to_backend, "
        "migration_id, reason, recorded_at) VALUES(1,'legacy','legacy',NULL,?,?)",
        ("bootstrap", t))
    return read_binding(cx)


def read_binding(cx):
    """The binding a crashed operator consults. Returns None when the
    registry file exists but was never bootstrapped -- including the case
    where the file exists with no schema at all, which the client gate
    reaches because it opens without running the schema script."""
    try:
        row = cx.execute("SELECT * FROM authority_binding WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else dict(row)


def epoch_log(cx):
    return [dict(r) for r in cx.execute(
        "SELECT * FROM authority_epoch_log ORDER BY epoch").fetchall()]


def set_phase(cx, *, phase, t=None):
    validate_phase(phase)
    t = time.time() if t is None else float(t)
    binding = read_binding(cx)
    if binding is None:
        _refuse("authority_binding_missing")
    cx.execute("UPDATE authority_binding SET phase=?, updated_at=? WHERE id=1",
               (phase, t))
    return read_binding(cx)


def record_compare_verdict(cx, *, migration_id, verdict, mismatch_count,
                           detail_json=None, t=None):
    _validate_text(migration_id, "invalid_migration_id")
    if verdict not in ("match", "mismatch"):
        _refuse("invalid_compare_verdict", {"verdict": verdict})
    t = time.time() if t is None else float(t)
    cx.execute(
        "INSERT INTO migration_compare(migration_id, verdict, mismatch_count, "
        "detail_json, recorded_at) VALUES(?,?,?,?,?)",
        (migration_id, verdict, int(mismatch_count), detail_json, t))
    return latest_compare_verdict(cx, migration_id)


def latest_compare_verdict(cx, migration_id):
    row = cx.execute(
        "SELECT * FROM migration_compare WHERE migration_id=? ORDER BY id DESC LIMIT 1",
        (migration_id,)).fetchone()
    return None if row is None else dict(row)


def begin_quiesce(cx, *, migration_id, t=None):
    """Phase 2 and phase 5 both stop the world briefly. Quiesce is recorded
    in the SAME row `require_epoch` reads, so a command cannot slip through
    by consulting a different flag than the one the operator set."""
    _validate_text(migration_id, "invalid_migration_id")
    t = time.time() if t is None else float(t)
    binding = read_binding(cx)
    if binding is None:
        _refuse("authority_binding_missing")
    if binding["quiesced"]:
        if binding["migration_id"] != migration_id:
            _refuse("authority_already_quiesced",
                    {"migration_id": binding["migration_id"]})
        return binding
    cx.execute(
        "UPDATE authority_binding SET quiesced=1, migration_id=?, updated_at=? "
        "WHERE id=1 AND quiesced=0", (migration_id, t))
    return read_binding(cx)


def end_quiesce(cx, *, migration_id, t=None):
    _validate_text(migration_id, "invalid_migration_id")
    t = time.time() if t is None else float(t)
    binding = read_binding(cx)
    if binding is None:
        _refuse("authority_binding_missing")
    if binding["quiesced"] and binding["migration_id"] != migration_id:
        _refuse("authority_quiesce_owner_mismatch",
                {"migration_id": binding["migration_id"]})
    cx.execute("UPDATE authority_binding SET quiesced=0, updated_at=? WHERE id=1", (t,))
    return read_binding(cx)


def require_epoch(cx, *, presented_epoch, backend):
    """THE client gate.

    Deliberately a pure read plus four comparisons: it runs on EVERY
    command, so anything expensive here becomes a per-command tax, and
    anything that writes here becomes a write on the path of a command that
    may be about to be refused.
    """
    validate_backend(backend)
    _validate_epoch(presented_epoch, "invalid_presented_epoch", minimum=0)
    binding = read_binding(cx)
    if binding is None:
        _refuse("authority_binding_missing")
    if binding["quiesced"]:
        _refuse("authority_quiesced", {
            "migration_id": binding["migration_id"], "phase": binding["phase"]})
    if backend != binding["active_backend"]:
        _refuse("authority_backend_mismatch", {
            "active_backend": binding["active_backend"], "presented_backend": backend})
    if presented_epoch < binding["authority_epoch"]:
        _refuse("authority_epoch_stale", {
            "authority_epoch": binding["authority_epoch"],
            "presented_epoch": presented_epoch})
    if presented_epoch > binding["authority_epoch"]:
        _refuse("authority_epoch_ahead", {
            "authority_epoch": binding["authority_epoch"],
            "presented_epoch": presented_epoch})
    return binding


def guard(state_root, repository_id, *, presented_epoch=None, backend=None):
    """The connect-site gate, with the absent-registry rule.

    Returns the effective binding dict (synthesised when the registry is
    absent) or raises. See the module docstring: absence of a registry IS
    the legacy binding at epoch 0, so an `external` claim is refused and a
    `legacy` client proceeds.

    `presented_epoch=None` means "this client does not carry an epoch". Such
    a client is treated as `legacy` and is therefore refused by the ordinary
    `authority_backend_mismatch` check the moment authority moves to
    `external` -- no separate rule, no separate flag.
    """
    assert_not_shadow_namespace(state_root)
    backend = "legacy" if backend is None else validate_backend(backend)
    if not registry_exists(state_root, repository_id):
        if backend != "legacy":
            _refuse("authority_backend_mismatch", {
                "active_backend": "legacy", "presented_backend": backend,
                "registry": "absent",
                "reason": "ADR-0082 phases 1-4: legacy remains the sole "
                          "authority; an absent registry has never moved it"})
        if presented_epoch is not None:
            _validate_epoch(presented_epoch, "invalid_presented_epoch", minimum=0)
            if presented_epoch > 0:
                _refuse("authority_epoch_ahead", {
                    "authority_epoch": 0, "presented_epoch": presented_epoch,
                    "registry": "absent"})
        return {"repository_id": repository_id, "active_backend": "legacy",
                "authority_epoch": 0, "phase": "inventory", "quiesced": 0,
                "migration_id": None, "registry": "absent"}
    # ensure_schema=False: the gate runs on EVERY command and must not
    # take a write lock to answer a read.
    cx = connect(state_root, repository_id, create=False, ensure_schema=False)
    try:
        binding = read_binding(cx)
        if binding is None:
            # The file exists but nothing was ever bootstrapped into it.
            # Same posture as an absent file, and for the same reason: no
            # flip has been recorded, so authority has not moved.
            if backend != "legacy":
                _refuse("authority_backend_mismatch", {
                    "active_backend": "legacy", "presented_backend": backend,
                    "registry": "unbootstrapped"})
            return {"repository_id": repository_id, "active_backend": "legacy",
                    "authority_epoch": 0, "phase": "inventory", "quiesced": 0,
                    "migration_id": None, "registry": "unbootstrapped"}
        # An epoch-unaware client presents the binding's own epoch, so the
        # epoch comparison is a no-op for it and the BACKEND check is what
        # decides its fate -- refusing it exactly when authority has moved
        # to `external`, and never before.
        effective = (binding["authority_epoch"] if presented_epoch is None
                     else presented_epoch)
        binding = require_epoch(cx, presented_epoch=effective, backend=backend)
        binding = dict(binding)
        binding["registry"] = "present"
        return binding
    finally:
        cx.close()


def switch_authority(cx, *, migration_id, from_backend, to_backend, from_epoch,
                     active_leases, t=None, reason="cutover"):
    """ADR-0082 phase 5 -- THE one flip.

    A single conditional UPDATE guarded by `cur.rowcount == 1`, deliberately
    the same rowcount-CAS shape as
    `nightshift_publication.consume_publication_authorization` and
    `nightshift-ctl`'s `consume_program_action_permit`. Two operators racing
    the same cutover: exactly one UPDATE matches, the loser sees rowcount 0
    and is refused, and no epoch is skipped -- because the winner's own
    `authority_epoch=authority_epoch+1` is evaluated by SQLite inside the
    same statement that tested `authority_epoch=?`.
    """
    _validate_text(migration_id, "invalid_migration_id")
    validate_backend(from_backend)
    validate_backend(to_backend)
    _validate_epoch(from_epoch, "invalid_from_epoch", minimum=1)
    if from_backend == to_backend:
        _refuse("authority_switch_is_a_noop",
                {"from_backend": from_backend, "to_backend": to_backend})
    if active_leases is None:
        _refuse("active_leases_not_supplied")
    leases = list(active_leases)
    t = time.time() if t is None else float(t)

    binding = read_binding(cx)
    if binding is None:
        _refuse("authority_binding_missing")
    if not binding["quiesced"]:
        _refuse("authority_not_quiesced", {"migration_id": binding["migration_id"]})
    if binding["migration_id"] != migration_id:
        _refuse("authority_quiesce_owner_mismatch",
                {"migration_id": binding["migration_id"]})
    if leases:
        # ADR-0082 phase 5: "verify no active legacy lease". A lease still
        # held is a writer that has not been drained; flipping under it is
        # the lost-update the whole procedure exists to avoid.
        _refuse("active_legacy_lease", {"active_leases": leases[:16],
                                        "count": len(leases)})
    verdict = latest_compare_verdict(cx, migration_id)
    if verdict is None or verdict["verdict"] != "match":
        _refuse("compare_not_matched", {
            "migration_id": migration_id,
            "verdict": None if verdict is None else verdict["verdict"]})

    cur = cx.execute(
        "UPDATE authority_binding SET active_backend=?, "
        "authority_epoch=authority_epoch+1, migration_id=?, phase='switch', "
        "updated_at=? WHERE id=1 AND authority_epoch=? AND active_backend=? "
        "AND quiesced=1",
        (to_backend, migration_id, t, from_epoch, from_backend))
    if cur.rowcount != 1:
        current = read_binding(cx)
        _refuse("authority_switch_refused", {
            "expected_epoch": from_epoch, "expected_backend": from_backend,
            "actual_epoch": None if current is None else current["authority_epoch"],
            "actual_backend": None if current is None else current["active_backend"]})
    after = read_binding(cx)
    cx.execute(
        "INSERT INTO authority_epoch_log(epoch, from_backend, to_backend, "
        "migration_id, reason, recorded_at) VALUES(?,?,?,?,?,?)",
        (after["authority_epoch"], from_backend, to_backend, migration_id, reason, t))
    return after


def rollback_authority(cx, *, migration_id, fresh_legacy_root, from_epoch,
                       t=None, reason="rollback"):
    """ADR-0082's rollback: "another quiesce, replay into a fresh legacy
    copy, validation, a new epoch and another single switch -- never
    reopening the stale files directly."

    `fresh_legacy_root` is therefore MANDATORY and NAMED, and a root
    carrying the retained-snapshot marker is refused `stale_snapshot_reopen`.
    The check is against a marker file the snapshot itself wrote, not
    against a caller-supplied flag: an operator under cutover pressure who
    points this at the read-only snapshot gets a refusal, not a silent
    rollback onto files the switch already declared stale.
    """
    _validate_text(migration_id, "invalid_migration_id")
    _validate_abs_dir(fresh_legacy_root, "invalid_fresh_legacy_root")
    _validate_epoch(from_epoch, "invalid_from_epoch", minimum=1)
    t = time.time() if t is None else float(t)

    binding = read_binding(cx)
    if binding is None:
        _refuse("authority_binding_missing")
    if not binding["quiesced"]:
        _refuse("authority_not_quiesced", {"migration_id": binding["migration_id"]})
    if binding["active_backend"] != "external":
        _refuse("authority_rollback_not_applicable",
                {"active_backend": binding["active_backend"]})
    if not os.path.isdir(fresh_legacy_root):
        _refuse("fresh_legacy_root_missing", {"path": fresh_legacy_root})
    if os.path.isfile(os.path.join(fresh_legacy_root, RETAINED_SNAPSHOT_MARKER)):
        _refuse("stale_snapshot_reopen", {
            "path": fresh_legacy_root,
            "reason": "ADR-0082: rollback replays into a FRESH legacy copy; "
                      "the retained snapshot is read-only evidence and is "
                      "never reopened as the live store"})
    if not os.listdir(fresh_legacy_root):
        _refuse("fresh_legacy_root_empty", {"path": fresh_legacy_root})

    cur = cx.execute(
        "UPDATE authority_binding SET active_backend='legacy', "
        "authority_epoch=authority_epoch+1, migration_id=?, phase='soak', "
        "updated_at=? WHERE id=1 AND authority_epoch=? AND "
        "active_backend='external' AND quiesced=1",
        (migration_id, t, from_epoch))
    if cur.rowcount != 1:
        current = read_binding(cx)
        _refuse("authority_switch_refused", {
            "expected_epoch": from_epoch, "expected_backend": "external",
            "actual_epoch": None if current is None else current["authority_epoch"],
            "actual_backend": None if current is None else current["active_backend"]})
    after = read_binding(cx)
    cx.execute(
        "INSERT INTO authority_epoch_log(epoch, from_backend, to_backend, "
        "migration_id, reason, recorded_at) VALUES(?,'external','legacy',?,?,?)",
        (after["authority_epoch"], migration_id, reason, t))
    return after


# ===========================================================================
# ADR-0085 -- repository ownership: self vs external, PR-only-never-merge
# ===========================================================================

def declare_repository_ownership(cx, *, repository_id, ownership, declared_by, t=None):
    """Write-once bootstrap of this repository's ownership classification.

    Idempotent on a re-declare with the SAME `(repository_id, ownership)` --
    same posture as `init_binding` -- and returns the existing row unchanged.
    A re-declare with a DIFFERENT `ownership` (or a different
    `repository_id`) is refused `repository_ownership_already_declared`: the
    row is write-once at the database layer (the two triggers on
    `repository_ownership`), and application code must not paper over that
    by silently accepting a change no operator asked the database to make
    permanent. Reversing a declaration means removing the registry file
    (an operator act, documented in the runbook), never rewriting this row.
    """
    validate_repository_id(repository_id)
    validate_ownership(ownership)
    _validate_text(declared_by, "invalid_declared_by")
    t = time.time() if t is None else float(t)
    existing = read_repository_ownership(cx)
    if existing is not None:
        if (existing["repository_id"] != repository_id
                or existing["ownership"] != ownership):
            _refuse("repository_ownership_already_declared", {
                "declared_repository_id": existing["repository_id"],
                "declared_ownership": existing["ownership"],
                "presented_repository_id": repository_id,
                "presented_ownership": ownership})
        return existing
    cx.execute(
        "INSERT INTO repository_ownership(id, repository_id, ownership, "
        "declared_by, recorded_at) VALUES(1, ?, ?, ?, ?)",
        (repository_id, ownership, declared_by, t))
    return read_repository_ownership(cx)


def read_repository_ownership(cx):
    """The declared ownership row, or None. None covers both "this registry
    predates ADR-0085 and has no `repository_ownership` table at all" (same
    `try/except sqlite3.OperationalError` idiom `read_binding` already
    uses) and "the table exists but nothing has been declared into it yet".
    Both are the empty state, and `classify_repository` treats both as
    `external` -- never as a reason to raise."""
    try:
        row = cx.execute(
            "SELECT * FROM repository_ownership WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else dict(row)


MERGE_FORBIDDEN_REASON = "merge_forbidden_external_repository"


def classify_repository(state_root, repository_id):
    """Classify a repository as `self` or `external`, reading `authority.db`
    ALONE and never raising.

    Fails closed in the direction that matters here: anything that is NOT a
    present `ownership='self'` row classifies `external`. This is the
    opposite default from `guard()`'s absent-registry rule, which defaults
    `active_backend` to `legacy` -- and that asymmetry is intentional, not
    an inconsistency to "fix": "which store is authoritative" has a correct
    historical default (nothing has moved, so legacy); "do I own this
    repository" has no safe default except no. An unbootstrapped registry
    has never asserted ownership, and treating silence as permission to
    merge into someone else's default branch is exactly the failure mode
    ADR-0085 exists to remove.

    Returns `{"ownership": "self"|"external", "source": ..., "repository_id":
    ...}`. `source` is one of:
      registry               a present, declared row was read
      registry_absent        no `authority.db` file exists at this scope
      registry_unbootstrapped  the file exists but no row was ever declared
      unaddressable           `state_root`/`repository_id` do not name a
                              registry at all (missing, malformed, or an
                              I/O error reading one that does exist)
    """
    if state_root is None or repository_id is None:
        return {"ownership": "external", "source": "unaddressable",
                "repository_id": repository_id}
    try:
        validate_repository_id(repository_id)
        path = bootstrap_path(state_root, repository_id)
    except AuthorityError:
        return {"ownership": "external", "source": "unaddressable",
                "repository_id": repository_id}
    if not os.path.isfile(path):
        return {"ownership": "external", "source": "registry_absent",
                "repository_id": repository_id}
    try:
        cx = connect(state_root, repository_id, create=False, ensure_schema=False)
        try:
            row = read_repository_ownership(cx)
        finally:
            cx.close()
    except (sqlite3.Error, AuthorityError, OSError):
        # A registry that cannot be opened or read (permissions, a
        # corrupt file, a path replaced by a directory) is exactly as
        # unaddressable as one that is not there -- fail closed, do not
        # propagate the I/O error to a caller that might treat "crashed"
        # as different from "denied".
        return {"ownership": "external", "source": "unaddressable",
                "repository_id": repository_id}
    if row is None:
        return {"ownership": "external", "source": "registry_unbootstrapped",
                "repository_id": repository_id}
    ownership = row["ownership"] if row.get("ownership") in OWNERSHIP_VALUES else "external"
    return {"ownership": ownership, "source": "registry",
            "repository_id": repository_id}


def require_merge_allowed(state_root, repository_id):
    """THE merge gate. Returns the classification when this deployment may
    merge a pull request into the target repository's default branch;
    refuses (exit 3, `MERGE_FORBIDDEN_REASON`) otherwise.

    The only accepted classification is a present `ownership='self'` row --
    see `classify_repository`. There is no override and no fail-open
    parameter: a wrongly-denied merge on a correctly self-owned, correctly
    bootstrapped deployment is an operator error to fix by bootstrapping
    (see the runbook); a wrongly-allowed merge on someone else's repository
    is not something a retry can undo.
    """
    classification = classify_repository(state_root, repository_id)
    if classification["ownership"] != "self":
        _refuse(MERGE_FORBIDDEN_REASON, dict(classification))
    return classification


#: The two, and only two, environment variables `scope_from_env` reads.
#: `classify_repository`/`require_merge_allowed` never consult any other
#: environment variable, `.omni/control.db`, a `program/*.yaml` prompt, or
#: any memory store -- see the module docstring's ADR-0085 section.
ENV_STATE_ROOT = "NIGHTSHIFT_STATE_ROOT"
ENV_REPOSITORY_ID = "NIGHTSHIFT_REPOSITORY_ID"


def scope_from_env(env=None):
    """Return `(state_root, repository_id)` for THIS process's declared
    repository scope. Either or both may be `(None, None)` when unset --
    that is not malformed, it is simply "no scope declared", and
    `classify_repository` treats it as `unaddressable` (fails closed to
    `external`, never raises for it).

    A PRESENT but malformed `repository_id` (unparseable, non-positive, a
    bool, larger than `MAX_ID`) is REFUSED, never silently ignored -- the
    same posture `presented_from_env` already uses for the epoch/backend
    pair, for the same reason: silently downgrading a malformed value to
    "absent" would let a misconfigured deployment merge by accident instead
    of by a checked, present, `ownership='self'` row.
    """
    env = os.environ if env is None else env
    state_root = env.get(ENV_STATE_ROOT)
    if state_root is not None:
        state_root = state_root.strip() or None
    raw_repo = env.get(ENV_REPOSITORY_ID)
    repository_id = None
    if raw_repo is not None and raw_repo.strip():
        try:
            repository_id = int(raw_repo.strip(), 10)
        except ValueError:
            _refuse("invalid_repository_id", {"presented_repository_id": raw_repo})
        validate_repository_id(repository_id)
    return state_root, repository_id


# ===========================================================================
# STEP 5 -- the client gate at every adapter's connect()
# ===========================================================================
#
# HOW A CLIENT CARRIES ITS EPOCH, AND WHY IT IS THE ENVIRONMENT
# =============================================================
# Not a parameter. Three of the four call sites have signatures that are
# deliberately frozen and TESTED as frozen:
#
#   nightshift_memory.connect(state_root, *, create=True)
#       tests/test_memory_boundary.py asserts
#       `list(inspect.signature(connect).parameters) == ["state_root",
#       "create"]` -- the whole point being that no parameter shape exists
#       through which a caller could smuggle in a second handle.
#   nightshift_publication.connect(state_root, *, create=True)
#       its own docstring: "The signature is deliberately FROZEN ... so
#       there is no argument through which tenant B could name tenant A's
#       provenance.db."
#
# Widening either would weaken a guarantee that is already load-bearing, to
# add a gate. So the epoch travels the way a process identity normally
# does: in the process environment.
#
# THIS IS NOT A PRIVILEGE CHANNEL. Setting these variables cannot grant
# anything. The registry decides; the environment only states what the
# client BELIEVES, and every belief is checked:
#   * claiming `external` while the binding says `legacy`  -> refused
#   * claiming an epoch below the binding's                -> refused
#   * claiming an epoch above the binding's                -> refused
#   * claiming nothing at all                              -> epoch-unaware
#                                                             legacy client
# The only way to pass is to state what is already true.
#
# A MALFORMED value is REFUSED, never ignored. Ignoring `EPOCH=seven` would
# silently demote a client that thinks it is epoch-aware into an
# epoch-unaware one, which is precisely the class of client ADR-0082 phase 5
# says must be stopped.

ENV_BACKEND = "NIGHTSHIFT_AUTHORITY_BACKEND"
ENV_EPOCH = "NIGHTSHIFT_AUTHORITY_EPOCH"


def presented_from_env(env=None):
    """Return `(backend_or_None, epoch_or_None)` for THIS process."""
    env = os.environ if env is None else env
    backend = env.get(ENV_BACKEND)
    if backend is not None:
        backend = backend.strip()
        if not backend:
            backend = None
        else:
            validate_backend(backend)
    raw = env.get(ENV_EPOCH)
    epoch = None
    if raw is not None and raw.strip():
        try:
            epoch = int(raw.strip(), 10)
        except ValueError:
            _refuse("invalid_presented_epoch", {"presented_epoch": raw})
        _validate_epoch(epoch, "invalid_presented_epoch", minimum=0)
    return backend, epoch


def derive_scope(path):
    """Recover `(state_root, repository_id)` from an already-scoped root.

    The adapters are handed roots that ALREADY encode their scope:

        <state_root>/tenants/<tenant_id>/repos/<repository_id>   publication,
                                                                 provenance
        <state_root>/memory/<repository_id>                      memory

    Returns `(None, None)` for anything else -- a bare per-test tempdir, for
    instance. That is not a failure: `guard_connect` still applies the
    absent-registry rule to it, so an unrecognised root can never be used to
    claim the `external` backend.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return None, None
    parts = os.path.normpath(path).split(os.sep)
    if (len(parts) >= 5 and parts[-2] == "repos" and parts[-4] == "tenants"
            and parts[-1].isdigit() and parts[-3].isdigit()):
        return os.sep.join(parts[:-4]) or os.sep, int(parts[-1])
    if len(parts) >= 3 and parts[-2] == "memory" and parts[-1].isdigit():
        return os.sep.join(parts[:-2]) or os.sep, int(parts[-1])
    return None, None


def guard_connect(path, *, repository_id=None, state_root=None, env=None):
    """THE gate the adapters call. Never returns a "maybe"; it either
    returns the effective binding or raises `AuthorityError`."""
    backend, epoch = presented_from_env(env)
    assert_not_shadow_namespace(path)
    derived_root, derived_repo = derive_scope(path)
    root = state_root or derived_root
    repo = repository_id if repository_id is not None else derived_repo
    if isinstance(repo, str) and repo.isdigit():
        repo = int(repo)
    if isinstance(root, str) and root:
        # Normalise a trailing slash or a relative path rather than refusing
        # it. The SHAPE of a state root is the adapter's business -- each one
        # has its own `_validate_state_root` and its own error for it, and
        # this gate must not pre-empt that with a different reason_code.
        root = os.path.abspath(root)
    addressable = root is not None and isinstance(repo, int) and not isinstance(repo, bool) and repo > 0
    if addressable:
        try:
            bootstrap_path(root, repo)
        except AuthorityError:
            # The registry is not nameable from these arguments. Fall through
            # to the absent-registry rule; the adapter will refuse the path
            # itself, in its own vocabulary, a moment later.
            addressable = False
    if not addressable:
        # No registry is addressable from this path. Same posture as an
        # absent registry, and for the same reason (ADR-0082 phases 1-4):
        # authority has not been moved, so `legacy` is the only backend a
        # client may claim from here.
        if backend is not None and backend != "legacy":
            _refuse("authority_backend_mismatch", {
                "active_backend": "legacy", "presented_backend": backend,
                "registry": "unaddressable", "path": path})
        if epoch is not None and epoch > 0:
            _refuse("authority_epoch_ahead", {
                "authority_epoch": 0, "presented_epoch": epoch,
                "registry": "unaddressable", "path": path})
        return {"repository_id": repo, "active_backend": "legacy",
                "authority_epoch": 0, "phase": "inventory", "quiesced": 0,
                "migration_id": None, "registry": "unaddressable"}
    return guard(root, repo, presented_epoch=epoch, backend=backend)


# ===========================================================================
# CLI -- ADR-0085's three plane-free verbs. This module owns its own CLI
# rather than adding a `repo` group to `bin/nightshift-ctl`'s single control
# plane, exactly like `bin/nightshift_publication.py`'s `build_parser`/
# `main` (see that module's tail): a fence over a repository's default
# branch is not a control-plane-scoped operation, and `require_merge_allowed`
# must be callable directly by any process (including a shell one-liner in
# the release procedure) without going through `bin/nightshift-ctl` at all.
# ===========================================================================

def out(obj, code=0):
    print(json.dumps(obj, sort_keys=True))
    sys.exit(code)


def _run_authority_command(fn, args):
    """Mirrors `bin/nightshift_publication.py`'s `_run_publication_command`
    boundary (itself modelled on `bin/nightshift-ctl`'s
    `_run_project_command`): never leaks a local path or a raw traceback
    through this CLI surface."""
    try:
        result = fn(args)
    except AuthorityError as exc:
        out(error_payload(exc), exc.exit_code)
    except Exception:
        out({"ok": False, "reason_code": "authority_operation_failed"}, 1)
    out(result)


def cmd_ownership_declare(args):
    cx = connect(args.state_root, args.repository_id)
    try:
        row = declare_repository_ownership(
            cx, repository_id=args.repository_id, ownership=args.ownership,
            declared_by=args.declared_by, t=args.now)
        return {"ok": True, **row}
    finally:
        cx.close()


def cmd_ownership_show(args):
    """Answers from `classify_repository` directly -- same fail-closed
    classification `merge-preflight` uses, so an operator reading this
    output sees exactly what the fence would decide, never a different
    view of the same registry."""
    classification = classify_repository(args.state_root, args.repository_id)
    return {"ok": True, **classification}


def cmd_merge_preflight(args):
    classification = require_merge_allowed(args.state_root, args.repository_id)
    return {"ok": True, **classification}


def build_parser():
    p = argparse.ArgumentParser(
        prog="nightshift_authority",
        description="ADR-0082 migration authority registry + "
                     "ADR-0085 repository-ownership merge fence")
    p.add_argument("--now", type=float, default=None,
                   help="override the clock, epoch seconds (test seam, argv only)")
    sub = p.add_subparsers(dest="group", required=True)

    def common(sp):
        sp.add_argument("--state-root", required=True)
        sp.add_argument("--repository-id", required=True, type=int)

    od = sub.add_parser("ownership-declare",
                         help="write-once bootstrap of this repository's ownership")
    common(od)
    od.add_argument("--ownership", required=True, choices=OWNERSHIP_VALUES)
    od.add_argument("--declared-by", required=True,
                     help="operator identity recorded on the write-once row")
    od.set_defaults(func=cmd_ownership_declare)

    osh = sub.add_parser("ownership-show",
                          help="read this repository's ownership classification")
    common(osh)
    osh.set_defaults(func=cmd_ownership_show)

    mp = sub.add_parser("merge-preflight",
                         help="exit 0 iff this deployment may merge into this "
                              "repository's default branch; exit 3 otherwise")
    common(mp)
    mp.set_defaults(func=cmd_merge_preflight)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    _run_authority_command(args.func, args)


if __name__ == "__main__":
    main()
