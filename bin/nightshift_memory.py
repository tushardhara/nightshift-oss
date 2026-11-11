#!/usr/bin/env python3
"""Narrative memory store (issue #400): the store and writer half (A1).

A SECOND SQLite database, separate from `control.db`, living under the
ADR-0021 `state_root` -- OUTSIDE any git worktree. Append-only rows plus an
FTS5 keyword index. No vector store, no embedding model, no mem0, no new
service: measured basis is 1,446 chunks, 0.03s index build, 3.21MB db,
brute-force KNN 0.04ms -- an ANN index would be 2-3 orders of magnitude
premature (see PLAN-400-memory-system.md).

ONE `memory.db` PER PROJECT, never one shared database with a tenant
column: physical file separation makes cross-project leakage
unrepresentable rather than merely policy-enforced, and makes per-project
export/delete a file operation instead of a query. `artifact.project` still
exists as a column (defense-in-depth / an internal consistency check), even
though the file boundary is the real isolation mechanism.

Same split as `nightshift_project.py` / `nightshift_bookkeeping_release.py`:
this module owns memory.db end to end (open, schema, read, write) and is
never handed the control-plane connection -- no transaction can span a
memory read and a control-plane grant, because the two are different file
descriptors. That physical separation is the advisory boundary: retrieval
from this store is evidence about the past, never proof of a control
predicate (see `TRUST_TAG` below, and H3 in the module's own hardening
notes).

Four hardening facts this module exists to satisfy (all measured, see
PLAN-400-memory-system.md; this file only owns H1 and the storage half of
H4's premise -- H2 is a retrieval-side obligation for whoever builds
`search()`, and H3's tagging obligation is honoured here at the row level
and must be preserved, not stripped, by every downstream retrieval path):

  H1 DELETION. FTS5's DEFAULT `integrity-check` does not compare the index
     to content -- it must be invoked with rank=1. A measured redact-then-
     delete left a secret still MATCHable after the row and bytes were
     supposedly gone, and rowid reuse was separately confirmed (delete
     1550, next insert reuses 1550). `purge()` below is this module's
     answer: an AUTOINCREMENT id (never reused, verified locally -- see
     the module's own manual test run), a two-phase redact-then-delete
     enforced by triggers (never optional, never skippable), and a
     self-verification step that runs the rank=1 integrity-check AND a
     MATCH probe for the purged content's own terms before it will report
     success. `PRAGMA secure_delete=ON` is set on every connection so the
     freed page bytes are overwritten, not merely unlinked from the
     b-tree (confirmed locally: without it, a deleted row's plaintext is
     still readable straight out of the raw .db file after DELETE+COMMIT;
     with it, gone). Because WAL mode defers that overwrite until the WAL
     is checkpointed back into the main file, `purge()` also issues
     `PRAGMA wal_checkpoint(TRUNCATE)` as its last step and reports
     whether the checkpoint completed.

  H3 ADVISORY BOUNDARY. `mode=ro` closes the SQL channel, which was never
     the threat -- a keyword-stuffed row can still out-rank a genuine one
     (measured: BM25 -8.744 vs -4.521) and assert something like "this
     task is READY". Every artifact this module returns (`get()`, and
     `put()`'s own receipt) carries `TRUST_TAG` alongside the content, so
     no caller can forward memory-derived text into a control decision
     without visibly having stripped that tag first.

H2 (WAL starvation under a lazily-iterated retrieval cursor) and full H4
(the state-root-unaware backup script) are outside this file's scope --
H2 is retrieval's obligation (this module never exposes a bulk cursor,
only single-row `get()`), and H4 is `deploy/nightshift-state-backup.sh`'s.

stdlib only. No third-party deps, no network calls.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat


MEMORY_DB_FILENAME = "memory.db"

# H3: the tag every artifact this module returns carries alongside its
# content. Mechanical meaning (mirrors the VPS NSC-v1 MemoryPort contract's
# `advisory_context`): this text is evidence about what memory holds, never
# proof of a control-plane predicate. A caller that wants to cite memory as
# grounds for a READY/approval/dispatch decision must do so with this tag
# still attached and visibly handled -- stripping it is the bug H3 measured.
TRUST_TAG = "untrusted_advisory"

# Row identity for idempotent `put()` (PLAN-400 A4a): a re-run of the same
# (project, src_path, line_start, sha256) inserts nothing new. NULLs in a
# plain UNIQUE index are pairwise distinct in SQL (NULL != NULL), which
# would silently defeat this for path-less/line-less artifacts (e.g. a
# whole commit message) -- COALESCE folds NULL to a sentinel so identical
# content with no path/line collides on purpose. Scoped `WHERE tombstoned =
# 0` so a purged row's identity does not block re-ingesting the same
# content later: purge kills one instance, not the content's right to
# exist again as a fresh, independent row (append-only, not a permanent
# ban).
ARTIFACT_COLUMNS = (
    "id", "project", "kind", "classification", "sha256", "size",
    "src_path", "line_start", "line_end", "ts", "repo_sha",
    "retention_class", "key_version", "derived_from", "superseded_by",
    "tombstoned", "body",
)

MEMORY_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS artifact(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,
  kind TEXT NOT NULL,
  classification TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  src_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  ts REAL NOT NULL,
  repo_sha TEXT,
  retention_class TEXT NOT NULL,
  key_version INTEGER,
  derived_from INTEGER,
  superseded_by INTEGER,
  -- H1: the append-only gate. 0 = live and immutable-except-metadata; 1 =
  -- redacted, awaiting (or already past, on a crash-resumed purge) the
  -- physical DELETE the BEFORE DELETE trigger below requires. No column
  -- here means "deleted" -- deletion is a row leaving the table entirely,
  -- this is only the mandatory waypoint on the way there.
  tombstoned INTEGER NOT NULL DEFAULT 0,
  body TEXT NOT NULL,
  CHECK(line_end IS NULL OR line_start IS NULL OR line_end >= line_start)
);

-- See ARTIFACT_COLUMNS' comment above for why this is COALESCE'd and
-- partial rather than a bare UNIQUE(project, src_path, line_start, sha256).
CREATE UNIQUE INDEX IF NOT EXISTS artifact_identity
  ON artifact(project, COALESCE(src_path, ''), COALESCE(line_start, -1), sha256)
  WHERE tombstoned = 0;

CREATE INDEX IF NOT EXISTS artifact_by_project ON artifact(project);
CREATE INDEX IF NOT EXISTS artifact_by_src_path ON artifact(project, src_path);

-- ref(artifact_id, kind, val) -- issue/PR/ADR/commit/crew/path, per
-- PLAN-400. Neither prior authority (VPS OVERVIEW.md/ADRs) nor the plan
-- specifies a primary key. A bare (artifact_id, kind, val) PRIMARY KEY
-- would silently collapse legitimate duplicates -- one artifact commonly
-- carries several `path` refs, or the same issue ref twice from two
-- extraction passes over different source lines -- so this is a build-time
-- decision, documented here rather than assumed: a surrogate id, no
-- uniqueness constraint. ref rows have no independent lifecycle: this
-- module's public API never updates or deletes one directly -- they are
-- inserted only alongside their artifact in `put()` and removed only in
-- lockstep with it, by the cascade trigger below, when `purge()` deletes
-- the artifact row.
CREATE TABLE IF NOT EXISTS ref(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  val TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ref_by_artifact ON ref(artifact_id);
CREATE INDEX IF NOT EXISTS ref_by_kind_val ON ref(kind, val);

-- content='' : FTS5 stores no copy of the original text, only its tokenized
-- index -- the body's one authoritative copy lives in artifact.body. This
-- is strictly more private than "external content" mode, not merely
-- equivalent to it: there is no second plaintext copy for a leak or a
-- forgotten sync trigger to expose in the first place.
-- contentless_delete=1 : lets this contentless table support real UPDATE
-- and DELETE (verified locally against this deployment's SQLite, 3.53.4 --
-- see the module's own manual test run; VPS is 3.45.1, also >= the 3.43.0
-- that introduced this option). Without it, contentless tables accept no
-- DML at all, which is exactly the shape of the historical bug this
-- module's purge() must not repeat: unable to tell the fts table anything
-- was deleted, so the only workaround is to never call delete on it, and
-- the redacted row's terms stay MATCHable forever.
CREATE VIRTUAL TABLE IF NOT EXISTS artifact_fts USING fts5(
  body, src_path, content='', contentless_delete=1
);

-- H1 purge self-verification, the REAL check. `INSERT INTO artifact_fts
-- (artifact_fts, rank) VALUES ('integrity-check', 1)` (see purge() below)
-- is necessary but, for a contentless (content='') table, NOT sufficient:
-- rank=1 compares the FTS index against a content table that does not
-- exist here, so it structurally cannot detect "the index still mentions
-- a rowid whose artifact row is gone" -- confirmed locally (see purge()'s
-- own docstring). fts5vocab's 'instance' mode gives a direct, per-rowid
-- answer instead: `doc` is the exact rowid of the artifact_fts row a term
-- instance belongs to, so "SELECT 1 FROM artifact_fts_vocab WHERE doc = ?"
-- is a complete, unsampled proof that NO term at all is still indexed
-- against a given (purged) rowid -- unlike a MATCH probe over a handful of
-- sampled terms, it needs no knowledge of what the purged body ever said,
-- which also makes it work on the crash-resume path where the original
-- body is already unavailable (tombstoned to '').
CREATE VIRTUAL TABLE IF NOT EXISTS artifact_fts_vocab
  USING fts5vocab(artifact_fts, 'instance');

-- ---------------------------------------------------------------- H1 --
-- Append-only enforcement. Three BEFORE UPDATE triggers partition every
-- possible UPDATE into exactly one of: refused outright (tombstoned rows
-- are immutable), the one narrow redaction transition purge() is allowed
-- to make, or an ordinary metadata-only update (e.g. a later row's
-- `superseded_by` pointing back at this one) that must not touch body.
-- Order among these three does not matter -- each WHEN clause is mutually
-- exclusive with the others by construction.

CREATE TRIGGER IF NOT EXISTS artifact_no_update_after_tombstone
BEFORE UPDATE ON artifact FOR EACH ROW
WHEN old.tombstoned = 1
BEGIN
  SELECT RAISE(ABORT, 'memory: tombstoned artifact rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS artifact_tombstone_is_redaction_only
BEFORE UPDATE ON artifact FOR EACH ROW
WHEN old.tombstoned = 0 AND new.tombstoned = 1
  AND (new.body != ''
       OR new.project IS NOT old.project
       OR new.kind IS NOT old.kind
       OR new.classification IS NOT old.classification
       OR new.sha256 IS NOT old.sha256
       OR new.size IS NOT old.size
       OR new.src_path IS NOT old.src_path
       OR new.line_start IS NOT old.line_start
       OR new.line_end IS NOT old.line_end
       OR new.ts IS NOT old.ts
       OR new.repo_sha IS NOT old.repo_sha
       OR new.retention_class IS NOT old.retention_class
       OR new.key_version IS NOT old.key_version
       OR new.derived_from IS NOT old.derived_from
       OR new.superseded_by IS NOT old.superseded_by)
BEGIN
  SELECT RAISE(ABORT, 'memory: tombstoning may only redact body, no other column may change');
END;

CREATE TRIGGER IF NOT EXISTS artifact_body_append_only
BEFORE UPDATE ON artifact FOR EACH ROW
WHEN old.tombstoned = 0 AND new.tombstoned = 0 AND new.body IS NOT old.body
BEGIN
  SELECT RAISE(ABORT, 'memory: body is append-only, rewrite refused');
END;

-- "REFUSE ... untombstoned deletes", literally: a bare DELETE against a
-- live row is structurally impossible. The only path to a physical DELETE
-- is through the redaction UPDATE above first.
CREATE TRIGGER IF NOT EXISTS artifact_delete_requires_tombstone
BEFORE DELETE ON artifact FOR EACH ROW
WHEN old.tombstoned = 0
BEGIN
  SELECT RAISE(ABORT, 'memory: refuse delete of a live (untombstoned) artifact row');
END;

-- ------------------------------------------------------- FTS5 sync --
-- Three triggers keep artifact_fts a mechanical mirror of artifact, so
-- nothing that calls put()/purge() through this module's API can forget
-- to sync it -- the sync is not something a caller can get right or wrong.

CREATE TRIGGER IF NOT EXISTS artifact_fts_ai
AFTER INSERT ON artifact FOR EACH ROW
BEGIN
  INSERT INTO artifact_fts(rowid, body, src_path) VALUES (new.id, new.body, new.src_path);
END;

-- Redact the index the same moment the row is redacted (belt), ahead of
-- the physical fts DELETE that follows the artifact DELETE (suspenders).
-- Both independently remove the term from what MATCH can find.
CREATE TRIGGER IF NOT EXISTS artifact_fts_au_redact
AFTER UPDATE ON artifact FOR EACH ROW
WHEN old.tombstoned = 0 AND new.tombstoned = 1
BEGIN
  UPDATE artifact_fts SET body = '', src_path = '' WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS artifact_fts_ad
AFTER DELETE ON artifact FOR EACH ROW
BEGIN
  DELETE FROM artifact_fts WHERE rowid = old.id;
END;

-- ref rows never outlive their artifact; see the ref table's own comment.
CREATE TRIGGER IF NOT EXISTS artifact_ref_cascade_delete
AFTER DELETE ON artifact FOR EACH ROW
BEGIN
  DELETE FROM ref WHERE artifact_id = old.id;
END;
"""


class MemoryStoreError(Exception):
    """Mirrors nightshift_project.ProjectError's shape exactly, so a caller
    (the future nightshift-ctl `memory` verb wiring) can reuse the same
    `_run_*_command` boundary idiom already established for `project`.

    Named MemoryStoreError, not MemoryError -- the latter shadows a Python
    builtin (a real out-of-memory condition), which would be a landmine for
    any code that catches the builtin expecting the real thing.
    """

    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise MemoryStoreError(reason_code, 3, details)


def _operation_failed(reason_code="memory_operation_failed", details=None):
    raise MemoryStoreError(reason_code, 1, details)


def db_path(state_root):
    """Path to the memory database living directly inside `state_root`.

    NOT necessarily 1:1 with one project by itself -- an ADR-0021 state_root
    is 1:1 with one project run only when the caller already scoped it that
    way. In the general case a single physical state_root directory can
    legitimately host descriptors for MULTIPLE repositories at once
    (`state_root/startups/<repository_id>/<project_run_id>.json` -- see
    nightshift_project.py's `_publish`), so handing the bare ADR-0021
    state_root straight to this function for two different repository_ids
    would put both projects' rows in the SAME memory.db: exactly the
    cross-project leak PLAN-400 says physical separation must make
    unrepresentable. This function's own signature stays a single
    `state_root` argument on purpose -- see `resolve_memory_root` below and
    `connect()`'s docstring -- so callers that DO need repository_id-level
    isolation (every real production caller through `nightshift-ctl`
    does) must resolve a repository-scoped directory FIRST, with
    `resolve_memory_root`, and hand THAT to `db_path`/`connect` as
    `state_root`. Called with a `state_root` a caller already privately
    dedicates to exactly one project (as every test in this module's own
    suite does, and as `resolve_memory_root`'s own return value is
    designed to be used), this is genuinely 1:1 and needs no further
    partitioning. Sibling to the existing `startups/` descriptor tree --
    no naming collision either way."""
    return os.path.join(state_root, MEMORY_DB_FILENAME)


def resolve_memory_root(state_root, repository_id):
    """Compute (and create, if needed) the physically project-scoped
    directory that should be handed to `connect()`/`db_path()` as their
    `state_root` argument, when the caller's real ADR-0021 state_root may
    be shared by more than one repository_id.

    `connect()`'s own signature is deliberately frozen to
    `(state_root, create)` -- see test_memory_boundary.py's
    `test_connect_accepts_no_parameter_that_could_carry_a_control_plane_
    handle` -- so repository-level partitioning happens one layer up, by
    construction, rather than teaching `connect()` a second identity axis:
    the CALLER computes a per-repository_id subdirectory and hands THAT to
    `connect()` as its `state_root`. This is physical separation, the same
    mechanism `db_path` already relies on for per-project isolation, not a
    query filter layered on top of one shared file (PLAN-400: "makes
    cross-project leakage unrepresentable" -- a filter that could be
    forgotten in one code path would only make it policy-enforced).

    Every real memory verb reachable through `nightshift-ctl` calls this
    (or a wrapper that does) before ever touching `connect()`/`db_path()`,
    because `nightshift-ctl memory ...` requires `--repository-id` on
    every subcommand precisely so a bare `--state-root` can never be handed
    through un-scoped. Modules that are also exercised directly against a
    private, single-project-per-test `state_root` (this module's own test
    suite, `nightshift_memory_bootstrap.py`'s, `nightshift_memory_search.
    py`'s) may keep doing so unscoped -- repository_id is optional
    everywhere above this function precisely so that per-test isolation
    (a fresh tempdir per test) stays valid without needing a repository_id
    at all.

    Creates the target subdirectory at mode 0700 (matching ADR-0020 section
    2's state_root mode requirement -- ADR-0021 narrows the ANCESTOR-trust
    question, but the final directory's own mode comes from ADR-0020) if it
    does not already exist -- `connect()`'s
    `_validate_state_root` refuses a directory that is not already present
    at exactly that mode, so the first call for a given repository_id must
    create it, not merely compute its name.
    """
    if isinstance(repository_id, bool) or not isinstance(repository_id, int) or repository_id <= 0:
        _refuse("invalid_repository_id")
    _validate_state_root(state_root)
    root = os.path.join(state_root, "memory", str(repository_id))
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)  # makedirs' mode is subject to umask; force it
        parent = os.path.dirname(root)
        if os.path.isdir(parent):
            os.chmod(parent, 0o700)
    except OSError as exc:
        raise MemoryStoreError("state_root_unwritable", 3, {
            "state_root": state_root, "error": str(exc)}) from exc
    return root


def _validate_state_root(path):
    """Defense-in-depth check that `state_root` is the private directory
    ADR-0021 expects, before memory.db is placed inside it.

    This deliberately does NOT repeat nightshift_project.py's full
    per-component ancestry walk (`_open_state_root`) -- that trust judgement
    (foreign-owner / group-writable boundaries walking down from `/`) is
    made once, authoritatively, at `project bind` time, and recorded in the
    startup descriptor's `ancestry_trust` field. By the time any `memory`
    verb runs, the same state_root has already cleared that check for this
    project run. What this still owes on every call is the final-directory
    guarantee `_open_state_root` also enforces last: the path is absolute,
    normalized, and (with no symlink hop at the final component) resolves
    to a directory this process owns, mode exactly 0700 -- so memory.db is
    never placed into a world- or group-writable directory, or one swapped
    out from under a stale caller-supplied path.

    There is a TOCTOU window between this check and sqlite3's own open()
    -- Python's sqlite3 module takes a path string, not a directory file
    descriptor, so that window cannot be closed here. `connect()` in
    nightshift-ctl already accepts the identical tradeoff for control.db
    (see its comment on the `os.path.isfile` check ahead of its own
    `sqlite3.connect`); this mirrors that, not a weaker standard.
    """
    if (not isinstance(path, str) or not path
            or len(path.encode("utf-8")) > 4096
            or not path.startswith("/")
            or path != os.path.normpath(path)
            or path.startswith("//")):
        _refuse("invalid_state_root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise MemoryStoreError("invalid_state_root", 3) from exc
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        _refuse("invalid_state_root")


def _authority_guard(state_root):
    """ADR-0082's client gate (STEP 5 of the #400 close-out).

    Called from inside `connect()` rather than added to its signature: this
    function's parameter list is FROZEN and tests/test_memory_boundary.py
    asserts on it exactly (`["state_root", "create"]`). The client's epoch
    and backend therefore travel in the process environment -- see the
    long note in nightshift_authority.py for why that is not a privilege
    channel (the registry decides; the environment only states a belief,
    and every belief is checked).

    An ABSENT registry is the legacy binding at epoch 0: an `external`
    claim is refused, a `legacy` client proceeds. See ADR-0082 phases 1-4.
    """
    try:
        import nightshift_authority as _nsa
    except ImportError:                                   # pragma: no cover
        return None
    try:
        return _nsa.guard_connect(state_root)
    except _nsa.AuthorityError as exc:
        raise MemoryStoreError(exc.reason_code, exc.exit_code, exc.details) from exc


def connect(state_root, *, create=True):
    """Open this project's narrative-memory database, creating and/or
    migrating its schema as needed (both idempotent -- safe to call from
    every verb, every invocation, like nightshift-ctl's own `ensure_schema`
    over control.db).

    Physically separate from control.db by construction: this function
    never receives, and this module never imports, anything that opens
    control.db. No transaction can span a memory read and a control-plane
    grant, because there is no single connection that could hold both --
    the boundary is enforced by which file descriptor is open, not by a
    mode flag or a caller's promise (see the module docstring's H3 note).

    `create=False` refuses (`memory_db_missing`) rather than silently
    creating an empty database and answering questions about it -- the same
    failure mode issue #31 fixed for control.db's `discover_control_plane`.
    """
    _validate_state_root(state_root)
    _authority_guard(state_root)
    path = db_path(state_root)
    if not create and not os.path.isfile(path):
        _refuse("memory_db_missing")
    cx = sqlite3.connect(path, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    # H1: PRAGMA secure_delete is per-connection, not persisted in the file
    # -- must be reissued every connect(), unlike journal_mode=WAL (which
    # IS persisted, but MEMORY_SCHEMA's leading PRAGMA line is cheap and
    # idempotent to reissue too). Confirmed locally: without secure_delete,
    # a deleted row's plaintext body is still readable straight out of the
    # raw .db file after DELETE+COMMIT; with it, the freed page bytes are
    # overwritten. See purge()'s wal_checkpoint step for why WAL mode needs
    # one more push before that guarantee reaches the file on disk.
    cx.execute("PRAGMA secure_delete=ON")
    ensure_schema(cx)
    return cx


def ensure_schema(cx):
    cx.executescript(MEMORY_SCHEMA)


def _row_to_dict(row):
    result = dict(row)
    result["tombstoned"] = bool(result["tombstoned"])
    return result


def _find_by_identity(cx, project, src_path, line_start, sha256):
    row = cx.execute(
        "SELECT * FROM artifact WHERE tombstoned = 0 AND project = ? AND sha256 = ? "
        "AND COALESCE(src_path, '') = COALESCE(?, '') "
        "AND COALESCE(line_start, -1) = COALESCE(?, -1)",
        (project, sha256, src_path, line_start),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def put(cx, *, project, kind, classification, retention_class, body, ts,
        src_path=None, line_start=None, line_end=None, repo_sha=None,
        key_version=None, derived_from=None, refs=None):
    """Insert one immutable artifact row. The only way a body ever enters
    this store -- there is no update_body()/rewrite() anywhere in this
    module, by design (H1: append-only).

    `ts` is caller-supplied, not read from the wall clock here -- this
    module has no clock of its own (unlike nightshift-ctl's injectable
    `now()`/`set_clock_override`), so it duplicates none of ADR-0005 D4's
    virtual-clock machinery while staying just as deterministic to test:
    the caller (typically the future `memory put` CLI verb, passing
    nightshift-ctl's own `now()`) decides what time it is.

    `refs` is an iterable of `(kind, val)` pairs -- issue/PR/ADR/commit/
    crew/path, see the `ref` table's schema comment -- inserted alongside
    the artifact in the same transaction, or omitted/empty for none.

    Idempotent by content: row identity is `(project, src_path, line_start,
    sha256)` (sha256 computed here, from `body`, never trusted from a
    caller). A retry with the same identity returns the ORIGINAL receipt
    rather than inserting a duplicate -- but only if the retry's other
    salient fields (`kind`, `classification`, `retention_class`,
    `repo_sha`) genuinely match the existing row too; a same-identity call
    that disagrees on any of those is refused (`memory_put_conflict`)
    rather than silently answering for a different logical write, the same
    shape nightshift_project.py's `_publish`/`_existing_result` already
    uses for `project bind`'s write-once descriptors.

    Returns `{"ok": True, "status": "created"|"existing", "id", "sha256",
    "size", "trust"}`. Raises MemoryStoreError on conflict or failure.
    """
    if not isinstance(body, str):
        _operation_failed("memory_put_invalid_body")
    body_bytes = body.encode("utf-8")
    sha256 = hashlib.sha256(body_bytes).hexdigest()
    size = len(body_bytes)
    ref_pairs = [(str(k), str(v)) for k, v in (refs or [])]

    cx.execute("BEGIN IMMEDIATE")
    try:
        try:
            cur = cx.execute(
                "INSERT INTO artifact(project, kind, classification, sha256, size, "
                "src_path, line_start, line_end, ts, repo_sha, retention_class, "
                "key_version, derived_from, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project, kind, classification, sha256, size, src_path, line_start,
                 line_end, ts, repo_sha, retention_class, key_version, derived_from,
                 body),
            )
        except sqlite3.IntegrityError:
            existing = _find_by_identity(cx, project, src_path, line_start, sha256)
            cx.execute("ROLLBACK")
            if existing is None:
                _operation_failed("memory_put_failed")
            if (existing["kind"] != kind
                    or existing["classification"] != classification
                    or existing["retention_class"] != retention_class
                    or existing["repo_sha"] != repo_sha):
                _refuse("memory_put_conflict", {"id": existing["id"]})
            return {"ok": True, "status": "existing", "id": existing["id"],
                    "sha256": sha256, "size": size, "trust": TRUST_TAG}

        artifact_id = cur.lastrowid
        for ref_kind, ref_val in ref_pairs:
            cx.execute("INSERT INTO ref(artifact_id, kind, val) VALUES (?, ?, ?)",
                       (artifact_id, ref_kind, ref_val))
        cx.execute("COMMIT")
    except MemoryStoreError:
        raise
    except Exception:
        cx.execute("ROLLBACK")
        raise

    return {"ok": True, "status": "created", "id": artifact_id,
            "sha256": sha256, "size": size, "trust": TRUST_TAG}


def get(cx, artifact_id):
    """Read one artifact row by id, plus its refs.

    Returns None if the id does not resolve to a live row -- either it
    never existed, or `purge()` has already run to completion for it.
    `purge()` ends with the row physically removed (H1: "the row and bytes
    were gone", and the BEFORE DELETE trigger above exists at all only
    because a real DELETE is the intended end state) -- there is no
    lingering tombstoned ghost row for get() to distinguish "purged" from
    "never existed" by. That distinction lives in `purge()`'s own receipt
    at the moment it runs, not as a permanent, queryable fact afterward.

    A row `purge()` has redacted but not yet deleted -- the crash-resumed
    intermediate state, `tombstoned=True` with `body=""` -- IS still
    visible here if a caller happens to read between those two phases;
    ordinary callers never observe it, because both phases run inside one
    transaction (see `purge()`).

    Every result carries `trust: TRUST_TAG` (H3): this is advisory content,
    not a verified control-plane fact, however confident it reads.
    """
    row = cx.execute("SELECT * FROM artifact WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        return None
    result = _row_to_dict(row)
    result["refs"] = [dict(r) for r in cx.execute(
        "SELECT kind, val FROM ref WHERE artifact_id = ? ORDER BY id", (artifact_id,)
    ).fetchall()]
    result["trust"] = TRUST_TAG
    return result


_TERM_RE = re.compile(r"[A-Za-z0-9_]{4,}")


def _sample_terms(body, *, limit=8):
    """A handful of distinct tokens from `body`, for purge()'s own leak
    self-check. Does not need to match FTS5's tokenizer exactly (the actual
    MATCH query re-tokenizes whatever we pass it the same way it would
    tokenize indexed text, symmetrically) -- it only needs to plausibly
    represent what was in the body, restricted to characters that are never
    FTS5 query-syntax operators so the probe query can never misparse."""
    seen = []
    for tok in _TERM_RE.findall(body or ""):
        low = tok.lower()
        if low in seen:
            continue
        seen.append(low)
        if len(seen) >= limit:
            break
    return seen


def _match_rowids(cx, term):
    return [r[0] for r in cx.execute(
        "SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?", (term,)
    ).fetchall()]


def purge(cx, artifact_id, *, checkpoint=True):
    """Irreversibly redact and remove one artifact row -- the only path by
    which a live row's body ever leaves this database (H1).

    Idempotent and crash-safe. Two phases inside one BEGIN IMMEDIATE
    transaction, so no reader ever observes a redacted-but-undeleted
    intermediate row:
      1. redact: UPDATE ... SET tombstoned=1, body='' WHERE id=? AND
         tombstoned=0 -- a no-op (via the WHERE, not an error) if a prior
         crashed purge already got this far; the append-only triggers
         refuse to redact an already-tombstoned row, so this phase is
         skipped outright rather than retried, on resume.
      2. remove: DELETE FROM artifact WHERE id=? -- the BEFORE DELETE
         trigger requires tombstoned=1, which phase 1 (this call's, or an
         earlier crashed one's) guarantees.

    Calling purge() again on an id already fully removed returns
    `{"ok": True, "status": "absent", ...}` -- success, not an error; the
    row being gone is exactly what a repeated purge is asking for.

    After commit, this function VERIFIES the deletion rather than trusting
    the SQL to have done what it says -- the measured H1 finding was
    precisely a redact-then-delete that LOOKED complete and left a secret
    still MATCHable:
      - `INSERT INTO artifact_fts(artifact_fts, rank) VALUES
        ('integrity-check', 1)` must not raise. NOTE this alone is NOT
        sufficient proof for THIS schema: rank=1 compares the FTS index
        against a content table, and `artifact_fts` is contentless
        (content=''), so there is nothing for it to compare against --
        confirmed locally, it passes cleanly even when handed a
        deliberately orphaned index entry (see the module's own manual
        test run). It is still run and still must not raise (a real
        structural corruption, e.g. a torn write, WOULD trip it), but the
        actual leak proof below does not depend on it.
      - `SELECT 1 FROM artifact_fts_vocab WHERE doc = ?` (the fts5vocab
        'instance' shadow table declared in MEMORY_SCHEMA) must return no
        row for this artifact_id. This is a COMPLETE proof, not a sample:
        it lists every term still indexed against this exact rowid,
        whatever that term is -- unlike a MATCH probe over a handful of
        the original body's own terms, it needs no knowledge of what the
        purged body said, so it is equally conclusive on the crash-resume
        path where the original body is already unavailable (tombstoned
        to '' by an earlier, interrupted purge).
      - for a sample of the ORIGINAL body's own terms (captured before
        redaction, when available), a MATCH probe must not return this
        row's id -- kept as a second, independent, semantically-legible
        confirmation alongside the fts5vocab proof above, not the only
        line of defense.
    Any of these failing raises MemoryStoreError -- purge() never reports
    success for a deletion it has not itself re-confirmed.

    Finally, `PRAGMA wal_checkpoint(TRUNCATE)` (unless `checkpoint=False`)
    pushes the redaction out of the WAL and into the main file, where
    secure_delete's overwrite-on-free actually lands (confirmed locally:
    right after DELETE+COMMIT in WAL mode the freed bytes are still
    sitting, unredacted, in the base file until a checkpoint runs). The
    receipt reports `wal_checkpoint_busy` honestly if a concurrent reader
    held the checkpoint back -- the row is still fully, verifiably gone
    from every query's point of view either way; only the on-disk
    reclaiming of the freed page may be delayed.
    """
    row = cx.execute("SELECT * FROM artifact WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        return {"ok": True, "status": "absent", "id": artifact_id}

    old_body = row["body"] or ""
    terms = _sample_terms(old_body)

    cx.execute("BEGIN IMMEDIATE")
    try:
        if row["tombstoned"] == 0:
            cx.execute(
                "UPDATE artifact SET tombstoned = 1, body = '' "
                "WHERE id = ? AND tombstoned = 0", (artifact_id,))
        cx.execute("DELETE FROM artifact WHERE id = ?", (artifact_id,))
        cx.execute("COMMIT")
    except Exception:
        cx.execute("ROLLBACK")
        raise

    try:
        cx.execute("INSERT INTO artifact_fts(artifact_fts, rank) VALUES ('integrity-check', 1)")
    except sqlite3.DatabaseError as exc:
        _operation_failed("memory_purge_integrity_check_failed", {"detail": str(exc)})

    # The real leak proof (see docstring): complete, not sampled, and
    # independent of whether the original body's terms were still
    # available to sample from.
    vocab_hit = cx.execute(
        "SELECT 1 FROM artifact_fts_vocab WHERE doc = ? LIMIT 1",
        (artifact_id,)).fetchone()
    if vocab_hit is not None:
        _operation_failed("memory_purge_leak_detected", {
            "detail": "fts5vocab instance still references the purged rowid"})

    for term in terms:
        if artifact_id in _match_rowids(cx, term):
            _operation_failed("memory_purge_leak_detected", {"term": term})

    receipt = {"ok": True, "status": "purged", "id": artifact_id}
    if checkpoint:
        busy, _log, _checkpointed = cx.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        receipt["wal_checkpoint_busy"] = bool(busy)
    return receipt


# --------------------------------------------------------------------------
# CLI-shaped entrypoints -- `command(args) -> dict`, the same boundary shape
# every other domain module uses (nightshift_project.py's `bind_project`/
# `show_project`, nightshift_memory_search.py's `search_memory`/
# `verify_memory`), so `bin/nightshift-ctl`'s `_run_memory_command` can call
# these exactly like it calls their siblings. Everything above this point is
# the connection-taking library API (`connect`/`put`/`get`/`purge`); these
# two are thin adapters onto it -- `nightshift_memory_bootstrap.py`'s own
# module docstring documents `put_memory(args)` as its ASSUMED WRITER
# CONTRACT for exactly this shape.
# --------------------------------------------------------------------------

def _effective_state_root(args):
    """`args.state_root` alone when no `repository_id` is given (every test
    in this repo's own suite calls `connect()`/`put()`/`purge()` this way,
    against a private per-test tempdir), or the repository-scoped
    subdirectory `resolve_memory_root` computes when one is (every call
    that arrives through `nightshift-ctl`, which requires
    `--repository-id` on every `memory` subcommand). See
    `resolve_memory_root`'s own docstring for why this partitioning
    happens here, one layer above `connect()`, rather than inside it.
    """
    state_root = getattr(args, "state_root", None)
    if not state_root:
        _refuse("missing_argument", {"argument": "state_root"})
    repository_id = getattr(args, "repository_id", None)
    if repository_id is None:
        return state_root
    return resolve_memory_root(state_root, repository_id)


def put_memory(args):
    """`args`-taking adapter onto `put()`, matching the contract
    `nightshift_memory_bootstrap.py`'s `_load_writer()` already documents
    and depends on: `from nightshift_memory import put_memory`,
    `put_memory(args) -> dict`, `args` a plain attribute-access object
    (argparse.Namespace-shaped), idempotent by `(args.project, args.
    src_path, args.line_start, args.sha256)` in the sense that a retry
    with the same content resolves to `put()`'s own "existing" receipt --
    `sha256` itself is still computed by `put()` from `args.body`, never
    trusted from `args.sha256` (H1: never a caller-supplied hash), exactly
    as `put()`'s own docstring already requires.

    Opens and closes its own connection per call (mirrors `nightshift_
    project.py`'s verb-per-call shape) rather than assuming a caller wants
    to keep one connection alive across many artifacts -- a caller that
    does (e.g. a tight bootstrap loop over hundreds of files) is free to
    call `connect()`/`put()` directly instead of going through this
    adapter; both are the same schema, same triggers, same identity rule.
    """
    ts = getattr(args, "ts", None)
    if ts is None:
        import time
        ts = time.time()
    cx = connect(_effective_state_root(args))
    try:
        return put(
            cx, project=args.project, kind=args.kind,
            classification=args.classification,
            retention_class=args.retention_class, body=args.body, ts=ts,
            src_path=getattr(args, "src_path", None),
            line_start=getattr(args, "line_start", None),
            line_end=getattr(args, "line_end", None),
            repo_sha=getattr(args, "repo_sha", None),
            key_version=getattr(args, "key_version", None),
            derived_from=getattr(args, "derived_from", None),
            refs=getattr(args, "refs", None),
        )
    finally:
        cx.close()


def purge_memory(args):
    """`args`-taking adapter onto `purge()` for `nightshift-ctl memory
    purge`. `args.artifact_id` is a repeatable CLI flag
    (`action="append"`), so it arrives here as a list -- every id named is
    purged in the SAME call, each independently verified the way `purge()`
    itself guarantees (H1), and the aggregate receipt never claims success
    for one id by reporting another's. Refuses (`memory_db_missing`)
    rather than silently creating an empty database for a purge call --
    there is nothing sensible to purge from a store that was never
    written to, and a missing store is far more likely a wrong
    `--state-root`/`--repository-id` pair than an empty, valid one.
    """
    artifact_ids = getattr(args, "artifact_id", None) or []
    if not artifact_ids:
        _refuse("missing_argument", {"argument": "artifact_id"})
    cx = connect(_effective_state_root(args), create=False)
    try:
        receipts = [purge(cx, artifact_id) for artifact_id in artifact_ids]
    finally:
        cx.close()
    return {"ok": True, "purged": receipts}
