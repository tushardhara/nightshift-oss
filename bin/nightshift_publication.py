#!/usr/bin/env python3
"""Publication Gateway (issue #400, ADR-0006): the only write path into a
customer repository.

Slice built here: PG-1 (the gateway IS the only write path -- stage ->
review -> approve -> apply, with `patch` as a never-writes escape hatch),
PG-2 (one-use authorization over the exact 7-tuple, consumed by a rowcount
CAS), PG-3 (the producing worker cannot approve its own publication), PG-5
(no-generated-file mode disables publication entirely and structurally
cannot even name an egress target), and PG-7's `publication_lineage` wiring
(every stage/review/attempt this module writes also writes its real
derived_from edge, in the SAME transaction as the row it describes -- see
the "Lineage" section below and `publication_lineage_chain`). PG-7 also
covers #400 items 10-11 STRUCTURALLY, not merely optionally: `stage()`
takes a `snapshot_id` naming a REAL `source_snapshot` row produced by
`nightshift_provenance.record_source_snapshot`, refuses that row if it is
missing `repository_fingerprint`, `object_format`, `commit_oid` or
`tree_oid` (item 10 -- see `_validate_source_snapshot_identity`), and
refuses `closure_incomplete` when provenance's own recorded closure
manifest digest does not match the `closure_entry` rows that database
actually holds (item 11 -- see `_require_source_snapshot_closure`, which
delegates to `nightshift_provenance.verify_closure_complete`). Every real
publication therefore carries these identity/closure facts by
construction; a caller cannot stage one without them, and cannot mint
them either -- they are read out of provenance.db, never accepted from
the caller. The closure half has one honest limit, spelled out on
`_require_source_snapshot_closure`: the frozen claim is always CHECKED,
but how good the claim is depends on the capture that froze it.

THE PROVENANCE SEAM (issue #694). This module IMPORTS
`nightshift_provenance` and delegates to it. There is exactly ONE
tenant_id validator (`prov.validate_tenant_id` -- INTEGER, positive,
bounded), ONE physical-layout authority (`prov.resolve_publication_root`)
and ONE lineage graph -- whose two halves meet on the SNAPSHOT ID: a
publication `input_snapshot` parent is the provenance snapshot_id, and a
provenance `source_snapshot` lineage node is addressed by its `ref_id`,
never by whatever it was NAMED (see LINEAGE_EDGE_VIEW, and the ADR-0079
amendment). `publication.db` is a SIBLING of `provenance.db`
inside `state_root/tenants/<tenant_id>/repos/<repository_id>/`, and
`connect()` ATTACHes provenance.db READ-ONLY (`mode=ro`) so this module
can READ `source_snapshot`, `closure_entry`, `lineage_node` and
`derived_from` but can never write them (a write through the attachment
fails with "attempt to write a readonly database"). Because this module
never writes provenance.db, no transaction here ever spans two database
files -- which matters, since both run `journal_mode=WAL` and SQLite
provides no atomic multi-database commit under WAL.

Explicitly OUT OF SCOPE for this module (left to sibling work):
zero-footprint harnessing (PG-4), cross-tenant negative-test breadth
beyond the physical separation + tenant_id re-check this module already
does (PG-6), dirty-checkout handling (PG-8), tenancy encryption at rest
beyond a `key_version` column (ADR-0005), migration authority epochs
(ADR-0007), and PostgreSQL parity (#400 item 7 -- where durable ids
become UUIDv7 per the 400-memory-architecture OVERVIEW; the INTEGER
tenant_id below is the LOCAL profile's canonical type, and moving to
UUIDv7 is an ADR-0082 authority-epoch migration, not an edit here).
Merging this module's `publication_lineage` graph with provenance's
`lineage_node`/`derived_from` graph is NO LONGER out of scope: it is
done, as the `lineage_edge` TEMP view created in `connect()`.

PHYSICAL BOUNDARY: `publication.db` lives at
`state_root/tenants/<tenant_id>/repos/<repository_id>/publication.db`,
mode 0700, beside `provenance.db`. The directory is computed by
`prov.resolve_publication_root` -- ONE function, so the two files can
never drift apart onto two different layouts. Physical
per-tenant-per-repository separation is the PRIMARY cross-tenant
guarantee; the `tenant_id` column recorded on every row is the secondary,
re-checked-on-every-verb one. This module never opens `control.db` -- no
transaction can ever span a publication decision and a control-plane
grant, for the same reason `nightshift_memory.py` stays plane-free.

CLOCK: every entrypoint here takes `t` (a caller-supplied timestamp), never
`time.time()` directly. Publication has TTLs; tests move `t` explicitly
(matching the `--now` seam `bin/nightshift-ctl` already uses) rather than
sleeping.

THE ONE-USE CAS (PG-2): `consume_publication_authorization` is a single
`UPDATE ... WHERE ... AND consumed_at IS NULL AND revoked_at IS NULL`
guarded by `cur.rowcount == 1`, the same shape as `nightshift-ctl`'s
`consume_program_action_permit` (ADR-0044). Diagnostic SELECTs exist ONLY to
pick a human-legible reason_code for the caller -- the CAS itself is the
sole authorizer. `ok` is never returned when the CAS returns 0 rows, and the
CAS is never skipped on a "clearly valid" fast path. This is the single
highest-risk line in this file; do not restructure it without re-reading
this paragraph.

stdlib only. No third-party deps, no network calls.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
import json
import os
import posixpath
import re
import sqlite3
import stat
import subprocess
import sys
import time
import unicodedata

import nightshift_provenance as prov


PUBLICATION_DB_FILENAME = "publication.db"

# Bumped by #694: tenant_id is INTEGER, publication.db sits under
# tenants/<tenant_id>/repos/<repository_id>/, and publication_stage carries
# a real provenance snapshot_id instead of a caller-supplied JSON blob.
# `connect()` refuses a populated version-0 file rather than guessing.
PUBLICATION_SCHEMA_VERSION = 2

# #400 item 14: classifications that must never leave the engine boundary,
# regardless of review outcome. Open-world blocklist (not an allowlist) --
# any OTHER classification (adr, prd, release_note, design_doc, ...) is
# publishable subject to the rest of the pipeline.
DENIED_CLASSIFICATIONS = frozenset({
    "transcript", "prompt", "evidence", "credential",
    "internal_reasoning", "operational_memory", "unreviewed_memory",
})

MAX_CONTENT_BYTES = 2_000_000       # this slice: UTF-8 text documents only
MAX_PATH_BYTES = 4096
DEFAULT_MAX_TTL_SECONDS = 3600      # a week-long approval is an ambient capability
ANCESTRY_WALK_LIMIT = 64            # bound on derived_from_stage_id chain walks

# Bounds on the ONE cross-file lineage walk (`lineage_chain_from_commit`).
# Separate from ANCESTRY_WALK_LIMIT on purpose: that one bounds a walk over
# THIS file's stage rows, these bound a recursive CTE over the unified view
# that spans provenance's graph too, where the row count is a product of
# the graph, not a single chain. Both are needed:
#   * LINEAGE_WALK_DEPTH_LIMIT stops the recursion from descending forever
#     (a cycle re-enters at depth+1, and `UNION` dedupes on the whole row,
#     so (node_id, depth) is always new -- the walk never converges).
#   * LINEAGE_WALK_ROW_LIMIT is the hard stop. A depth cap alone bounds the
#     work at (reachable nodes x depth), not at a constant: one node can be
#     reached at many different depths, so an 8k-node graph produced 32,389
#     rows under the depth cap alone. SQLite halts a recursive CTE once the
#     outer LIMIT is satisfied (the documented "infinite series" idiom),
#     which is why the outer select must NOT sort -- a sort would force the
#     whole result before the limit applied. Ordering happens in Python
#     instead, on rows already in hand.
# A walk that hits either bound reports `truncated: True` and, if the
# snapshot was not reached first, `closed: False`. It never reports a
# snapshot it did not actually reach.
LINEAGE_WALK_DEPTH_LIMIT = 256
LINEAGE_WALK_ROW_LIMIT = 4096

# Publication owns the `pub:` namespace in the shared node-id vocabulary
# (see LINEAGE_EDGE_VIEW). Provenance refuses to mint an id in it, so the
# two halves of the unified view are disjoint by construction. The
# equality is asserted rather than assumed: if either module's constant
# ever moves, this import fails loudly instead of re-opening the aliasing
# hole that made a walk return the wrong snapshot.
LINEAGE_NODE_PREFIX = "pub:"
if LINEAGE_NODE_PREFIX != prov.RESERVED_NODE_ID_PREFIX:      # pragma: no cover
    raise RuntimeError(
        "publication/provenance disagree on the reserved lineage node prefix: "
        "%r vs %r" % (LINEAGE_NODE_PREFIX, prov.RESERVED_NODE_ID_PREFIX))

# Heuristic secret scan, re-run at stage/approve/apply (per-module design).
# Not a substitute for a real secret-scanning service -- named as such.
_SECRET_PATTERNS = (
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}")),
    ("inline_secret_assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*"
        r"['\"]?[A-Za-z0-9_\-]{16,}")),
)


# ---------------------------------------------------------------------------
# Error boundary -- duck-types the shape _run_memory_command already accepts
# in bin/nightshift-ctl (reason_code / exit_code / details), so CLI wiring
# for this module can reuse that exact wrapper idiom without needing to know
# this module's exception class by name.
# ---------------------------------------------------------------------------

class PublicationError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    payload = {"ok": False, "reason_code": error.reason_code}
    if error.details:
        payload["details"] = error.details
    return payload


def _refuse(reason_code, details=None):
    """Exit code 3: a well-formed request the gateway declines. Every path
    this module can reach that is not a usage error or an internal failure
    must funnel through here, never through a bare `raise` or `assert`."""
    raise PublicationError(reason_code, 3, details)


def _usage_error(reason_code, details=None):
    """Exit code 2: malformed/missing input, before any policy question is
    even reachable."""
    raise PublicationError(reason_code, 2, details)


def _operation_failed(reason_code="publication_operation_failed", details=None):
    """Exit code 1: the request was valid but the operation itself failed
    (e.g. a git subprocess errored, a filesystem write failed)."""
    raise PublicationError(reason_code, 1, details)


# ---------------------------------------------------------------------------
# Clock -- injectable, argv-only (see module docstring). Deliberately not a
# bare time.time() call anywhere below this block.
# ---------------------------------------------------------------------------

_NOW_OVERRIDE = None


def set_clock_override(value):
    global _NOW_OVERRIDE
    _NOW_OVERRIDE = value


def now() -> float:
    if _NOW_OVERRIDE is not None:
        return _NOW_OVERRIDE
    return time.time()


def out(obj, code=0):
    print(json.dumps(obj, sort_keys=True))
    sys.exit(code)


# ---------------------------------------------------------------------------
# Scope validation -- ONE validator, shared with nightshift_provenance.
#
# Before #694 this module's only tenant check was `if not tenant_id` inside
# stage(); policy_set/register_reviewer/review/approve/apply/patch validated
# tenant_id not at all, and stage() accepted '../other_tenant', 'tenant-alpha'
# and a SQL-injection-shaped string alike. It now delegates to
# `prov.validate_tenant_id`, re-raising the refusal as this module's own
# error type with the IDENTICAL reason_code. That is the point: an id
# rejected by provenance is rejected here by construction, not because two
# copies of a validator were kept in sync by hand.
#
# `_validate_state_root` below stays local (it asks a different question:
# "is this directory a legal place for MY database file", checked on a root
# provenance has already computed).
# ---------------------------------------------------------------------------

def _as_publication_error(exc):
    """Re-raise a ProvenanceError as a PublicationError, preserving
    reason_code, exit_code and details verbatim. Every refusal a caller of
    this module sees is a PublicationError, exactly as
    `_run_publication_command` and the CLI boundary assume -- but the
    DECISION was made once, in provenance."""
    return PublicationError(exc.reason_code, exc.exit_code, dict(exc.details))


def _validate_tenant_id(value):
    try:
        return prov.validate_tenant_id(value)
    except prov.ProvenanceError as exc:
        raise _as_publication_error(exc) from exc


def _validate_repository_id(value):
    try:
        return prov.validate_repository_id(value)
    except prov.ProvenanceError as exc:
        raise _as_publication_error(exc) from exc


def _validate_state_root(path):
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
        raise PublicationError("invalid_state_root", 3) from exc
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        _refuse("invalid_state_root")


LEGACY_PUBLICATION_DIRNAME = "publication"   # the pre-#694 location


def resolve_publication_root(state_root, tenant_id, repository_id):
    """Compute (and create, mode 0700) the physically TENANT-and-repository
    scoped directory publication.db lives in, by delegating outright to
    `prov.resolve_publication_root`. publication.db therefore lands at
    `<state_root>/tenants/<tenant_id>/repos/<repository_id>/publication.db`,
    a sibling of provenance.db.

    Before #694 this computed `<state_root>/publication/<repository_id>/`
    -- repository-scoped only -- so two tenants sharing a repository_id
    shared ONE publication.db and the row-level tenant_id re-check was the
    entire cross-tenant defence at this layer (tests/test_provenance_
    lineage.py's CrossTenantPublishFailsClosed documents exactly that).
    Delegating here upgrades publication to the same physical separation
    provenance already had, and does it by CALLING that code rather than
    copying it, so the two files can never drift onto two layouts.

    ANTI-SILENT-COEXISTENCE (#694: "silent coexistence of both types is not
    acceptable"): if the pre-#694 file still exists on disk, refuse rather
    than quietly writing to the new one and leaving an operator with a
    TEXT-tenant database and an INTEGER-tenant database, both live, neither
    obviously stale.
    """
    try:
        root = prov.resolve_publication_root(state_root, tenant_id, repository_id)
    except prov.ProvenanceError as exc:
        raise _as_publication_error(exc) from exc
    legacy = os.path.join(state_root, LEGACY_PUBLICATION_DIRNAME,
                           str(repository_id), PUBLICATION_DB_FILENAME)
    if os.path.isfile(legacy):
        _refuse("publication_legacy_root_present", {
            "legacy_path": legacy, "current_root": root,
            "remedy": "pre-#694 publication.db is development-only and is dropped, "
                      "not migrated (ADR-0081 amendment); remove it deliberately",
        })
    return root


def db_path(state_root):
    return os.path.join(state_root, PUBLICATION_DB_FILENAME)


# ---------------------------------------------------------------------------
# Schema -- 7 tables. See module docstring; triggers enforce append-only on
# `publication_stage`, restrict `publication_authorizations` UPDATEs to
# exactly the consume/revoke transitions, and restrict `publication_attempts`
# UPDATEs to the one intent -> terminal transition.
#
# ORDERING OF TENANT DEFENCES (#694), stated so a reviewer cannot mistake
# the backstop for the primary check. `_validate_tenant_id` ->
# `prov.validate_tenant_id` is the PRIMARY validator and runs in Python at
# every entrypoint, before the id can reach a path join or a WHERE clause.
# `CHECK(typeof(tenant_id)='integer' AND tenant_id>0)` on every
# tenant-bearing table below is a SECOND line, for a code path that forgot
# to call it -- necessary because declaring a column INTEGER does NOT make
# SQLite store integers (an `INTEGER NOT NULL` column happily stores
# '../other_tenant' as text). It is NOT a substitute: the CHECK coerces the
# string '7' to integer 7 and accepts it, which validate_tenant_id does not.
# ---------------------------------------------------------------------------

PUBLICATION_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS publication_policy(
  id INTEGER PRIMARY KEY CHECK(id = 1),
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('zero_nightshift_file','no_generated_file')),
  path_allowlist_json TEXT NOT NULL,
  path_allowlist_digest TEXT NOT NULL,
  target_git_dir TEXT,
  target_ref TEXT,
  policy_version INTEGER NOT NULL,
  key_version INTEGER,
  updated_at REAL NOT NULL,
  -- PG-5 structural short-circuit: strict mode cannot even NAME an egress
  -- target. There is no destination to write to even if every other check
  -- in this module passed.
  CHECK(mode <> 'no_generated_file' OR (target_git_dir IS NULL AND target_ref IS NULL))
);

CREATE TABLE IF NOT EXISTS publication_reviewer(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  reviewer_identity TEXT NOT NULL,   -- canonical form (NFC/strip/casefold)
  token_hash TEXT NOT NULL,
  registered_at REAL NOT NULL,
  revoked_at REAL,
  revoked_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS publication_reviewer_live
  ON publication_reviewer(tenant_id, reviewer_identity) WHERE revoked_at IS NULL;

-- The stage row names a REAL provenance snapshot (source_snapshot_id) and
-- keeps a DENORMALIZED copy of that snapshot's identity fields. The copy is
-- not redundancy for its own sake: ADR-0079's explicit limit is that
-- `mark_source_not_retained` may expire the snapshot's bytes, so a stage row
-- that only held a foreign key would stop being able to describe what it was
-- built from. The copy is taken once, at stage time, from the immutable
-- provenance row -- never from the caller.
CREATE TABLE IF NOT EXISTS publication_stage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  content_digest TEXT NOT NULL,
  content_size INTEGER NOT NULL,
  content BLOB NOT NULL,
  destination_path TEXT NOT NULL,
  classification TEXT NOT NULL,
  producer_identity TEXT NOT NULL,
  producer_run_id TEXT NOT NULL,
  source_snapshot_id TEXT NOT NULL,
  repository_fingerprint TEXT NOT NULL,
  snapshot_commit_oid TEXT NOT NULL,
  snapshot_tree_oid TEXT NOT NULL,
  snapshot_object_format TEXT NOT NULL,
  derived_from_stage_id INTEGER,
  memory_artifact_id INTEGER,
  policy_version INTEGER NOT NULL,
  key_version INTEGER,
  -- ADR-0080 STEP 9: which cipher sealed this body. Recorded PER STAGE ROW,
  -- not read from a module constant at open time, because `CIPHERS` in
  -- nightshift_tenant_keys is a REGISTRY: adding `aes-256-gcm-v1` later
  -- must not change how bytes already written are interpreted. A row that
  -- did not name its own cipher would be reinterpreted by whatever the
  -- default happened to be on the day someone read it.
  cipher_id TEXT,
  staged_at REAL NOT NULL,
  UNIQUE(tenant_id, repository_id, content_digest, destination_path)
);

CREATE TABLE IF NOT EXISTS publication_review(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_id INTEGER NOT NULL,
  reviewer_identity TEXT NOT NULL,
  reviewer_run_id TEXT NOT NULL,
  reviewed_digest TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('approve','reject')),
  notes TEXT,
  reviewed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS publication_review_by_stage ON publication_review(stage_id);

-- THE 7-TUPLE. One-use.
CREATE TABLE IF NOT EXISTS publication_authorizations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id     INTEGER NOT NULL,
  content_digest    TEXT    NOT NULL,
  destination_path  TEXT    NOT NULL,
  expected_base_sha TEXT    NOT NULL,
  reviewer_identity TEXT    NOT NULL,
  expires_at        REAL    NOT NULL,
  authorization_digest TEXT NOT NULL,
  stage_id INTEGER NOT NULL,
  review_id INTEGER NOT NULL,
  policy_version INTEGER NOT NULL,
  path_allowlist_digest TEXT NOT NULL,
  mode TEXT NOT NULL,
  target_git_dir TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  git_object_format TEXT NOT NULL,
  issued_at REAL NOT NULL,
  consumed_at REAL,
  consumed_by_attempt INTEGER,
  revoked_at REAL,
  revoked_reason TEXT
);
-- Two LIVE approvals for one identical tuple would defeat one-use by
-- covering a replay after the first is consumed; a NEW approval issued
-- after consumption (a fresh review) is correctly allowed.
CREATE UNIQUE INDEX IF NOT EXISTS publication_authorizations_live
  ON publication_authorizations(authorization_digest)
  WHERE consumed_at IS NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS publication_authorizations_by_stage
  ON publication_authorizations(stage_id);

CREATE TABLE IF NOT EXISTS publication_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  authorization_id INTEGER NOT NULL,
  stage_id INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL,
  observed_base_sha TEXT NOT NULL,
  target_git_dir TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('intent','applied','conflict','failed')),
  commit_oid TEXT, tree_oid TEXT, blob_oid TEXT, git_object_format TEXT,
  reason_code TEXT,
  started_at REAL NOT NULL,
  applied_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS publication_attempts_idem
  ON publication_attempts(tenant_id, repository_id, idempotency_key);

-- PG-7: populated by `stage`/`review`/`apply_publication` themselves (see
-- `record_publication_lineage`), each in the same transaction as the row
-- it describes -- defined here so a second lineage table is never invented
-- by whoever builds provenance next.
-- repository_id (added by #694) is not decoration: the unified
-- `lineage_edge` view below spans this table and provenance's derived_from,
-- and a walk across that seam must filter on BOTH scope axes or a tenant's
-- repository could reach another repository's edges under one tenant.
CREATE TABLE IF NOT EXISTS publication_lineage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL CHECK(typeof(repository_id)='integer' AND repository_id>0),
  child_kind TEXT NOT NULL CHECK(child_kind IN ('stage','review','patch','attempt')),
  child_id INTEGER NOT NULL,
  parent_kind TEXT NOT NULL CHECK(parent_kind IN
    ('input_snapshot','worker_output','stage','review','patch','attempt')),
  parent_ref TEXT NOT NULL,
  source_not_retained INTEGER NOT NULL DEFAULT 0,
  recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS publication_lineage_by_child
  ON publication_lineage(tenant_id, repository_id, child_kind, child_id);

CREATE TRIGGER IF NOT EXISTS publication_stage_no_update
BEFORE UPDATE ON publication_stage FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'publication: stage rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS publication_stage_no_delete
BEFORE DELETE ON publication_stage FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'publication: stage rows are immutable');
END;

-- Only the consume transition (consumed_at/consumed_by_attempt NULL->value)
-- or the revoke transition (revoked_at/revoked_reason NULL->value) may ever
-- touch an authorization row after it is issued.
CREATE TRIGGER IF NOT EXISTS publication_authorizations_restricted_update
BEFORE UPDATE ON publication_authorizations FOR EACH ROW
WHEN NOT (
  (new.consumed_at IS NOT old.consumed_at OR new.consumed_by_attempt IS NOT old.consumed_by_attempt
   OR new.revoked_at IS NOT old.revoked_at OR new.revoked_reason IS NOT old.revoked_reason)
  AND new.tenant_id IS old.tenant_id AND new.repository_id IS old.repository_id
  AND new.content_digest IS old.content_digest AND new.destination_path IS old.destination_path
  AND new.expected_base_sha IS old.expected_base_sha AND new.reviewer_identity IS old.reviewer_identity
  AND new.expires_at IS old.expires_at AND new.authorization_digest IS old.authorization_digest
  AND new.stage_id IS old.stage_id AND new.review_id IS old.review_id
  AND new.policy_version IS old.policy_version AND new.path_allowlist_digest IS old.path_allowlist_digest
  AND new.mode IS old.mode AND new.target_git_dir IS old.target_git_dir
  AND new.target_ref IS old.target_ref AND new.git_object_format IS old.git_object_format
  AND new.issued_at IS old.issued_at
)
BEGIN
  SELECT RAISE(ABORT, 'publication: authorization rows may only be consumed or revoked');
END;

-- Only the one intent -> terminal transition (state='intent' -> a terminal
-- state, applied_at unset beforehand) may update an attempt row.
CREATE TRIGGER IF NOT EXISTS publication_attempts_restricted_update
BEFORE UPDATE ON publication_attempts FOR EACH ROW
WHEN NOT (old.state = 'intent' AND old.applied_at IS NULL AND new.state <> 'intent')
BEGIN
  SELECT RAISE(ABORT, 'publication: attempt rows may only move from intent to a terminal state once');
END;

PRAGMA user_version = %d;
""" % PUBLICATION_SCHEMA_VERSION      # one source of truth for the version


# The ONE lineage graph (#694). Publication's own edges and provenance's
# derived_from edges, expressed in a single node-id vocabulary:
#   * a publication row is `pub:<kind>:<id>`  (e.g. pub:attempt:1)
#   * an input_snapshot parent is the provenance snapshot_id VERBATIM, and
#   * a provenance `source_snapshot` node enters the same vocabulary under
#     its `ref_id` -- the column that means "which snapshot" -- NOT under
#     its node_id.
# That last line is the seam, and it is the one the first cut of #694 got
# wrong. It joined `prov.derived_from` to `prov.lineage_node` on
# `n.node_id = d.parent_node_id` and compared THAT to the snapshot id, so
# `pub:stage:1 -> snap-1` continued into provenance only when a lineage
# node happened to be NAMED `snap-1`. Nothing required that, and
# provenance's own established convention is the opposite -- node_id is
# free-form (`n_snap`, `node-snapshot`) with the snapshot id in ref_id
# (see prov.record_lineage_node, and test_provenance_lineage's chains).
# Under that convention the walk stopped dead at the file boundary and
# still answered closed=True with truncated=False, because the snapshot
# was resolved from the source_snapshot TABLE: a broken join was
# indistinguishable from a complete one. Joining on `ref_id` removes the
# convention entirely -- both namings now produce the identical chain, and
# there is nothing left for a caller to violate.
# Both halves carry (tenant_id, repository_id) so one walk can filter both
# scope axes in one predicate.
#
# One namespace, two producers, therefore ONE disjointness rule, and it is
# enforced on both write paths rather than assumed:
#   * `pub:` is reserved -- provenance refuses to mint a snapshot id or a
#     lineage node id in it (prov.RESERVED_NODE_ID_PREFIX), and since the
#     seam now reads `ref_id`, a source_snapshot node's ref_id is validated
#     as a snapshot id there too. `record_publication_lineage` refuses an
#     input_snapshot parent_ref in it. Without that rule a snapshot could
#     be NAMED `pub:stage:1` and alias publication's stage 1: a walk from a
#     landed commit then returned a different snapshot's commit_oid with
#     closed=True.
#   * the SNAPSHOT-ID half of the namespace is disjoint in the same way and
#     for the same reason: `prov.record_lineage_node` refuses a
#     non-source_snapshot node named with an existing snapshot id, and
#     `prov.record_source_snapshot` refuses a snapshot id already held by
#     such a node. Either would alias the boundary node and hand a landed
#     commit some other snapshot's identity.
#   * The same alias also produced a self-edge, which an uncapped recursive
#     CTE walks forever. `record_publication_lineage` now refuses a
#     self-edge (as prov.record_derived_from already did) and
#     `lineage_chain_from_commit` walks under explicit bounds.
# `lineage_chain_from_commit` additionally REFUSES, rather than answers, if
# it ever meets a node claimed by both graphs -- a `pub:`-prefixed node
# provenance also holds, or a snapshot id that is also a lineage node of
# some other kind. The only way either can now happen is a row written
# around both validators.
#
# TEMP is not a style choice: SQLite REFUSES a persistent view over an
# attached schema ("view lineage_edge cannot reference objects in database
# prov") and accepts the identical TEMP view. The consequence, stated so it
# is never discovered by surprise: `lineage_edge` exists only on connections
# opened through `connect()`. Opening publication.db with a bare
# sqlite3.connect() yields "no such table: lineage_edge". There is
# deliberately no second documented way to open this file.
# The `pub:` literals below are FORMATTED IN from LINEAGE_NODE_PREFIX
# rather than typed twice: the view's node-id spelling and the Python
# guards that depend on it are then the same string by construction.
LINEAGE_EDGE_VIEW = """
CREATE TEMP VIEW IF NOT EXISTS lineage_edge AS
SELECT
  tenant_id,
  repository_id,
  CASE WHEN parent_kind='input_snapshot' THEN parent_ref
       ELSE '%(prefix)s'||parent_kind||':'||parent_ref END AS parent_node_id,
  '%(prefix)s'||child_kind||':'||child_id             AS child_node_id,
  parent_kind                                        AS edge_kind,
  source_not_retained                                AS source_not_retained,
  id                                                 AS edge_seq,
  'publication'                                      AS origin
FROM main.publication_lineage
UNION ALL
SELECT
  p.tenant_id,
  p.repository_id,
  CASE WHEN p.node_kind='source_snapshot' THEN p.ref_id ELSE p.node_id END AS parent_node_id,
  CASE WHEN c.node_kind='source_snapshot' THEN c.ref_id ELSE c.node_id END AS child_node_id,
  d.edge_kind,
  0            AS source_not_retained,
  NULL         AS edge_seq,
  'provenance' AS origin
FROM prov.derived_from d
JOIN prov.lineage_node p ON p.node_id = d.parent_node_id
JOIN prov.lineage_node c ON c.node_id = d.child_node_id
""" % {"prefix": LINEAGE_NODE_PREFIX}

# Every table that carries a tenant_id, i.e. everything the pre-#694 ->
# #694 type change touches. `connect()` refuses rather than guesses when it
# finds rows in any of them under schema version 0.
_TENANT_BEARING_TABLES = (
    "publication_policy", "publication_reviewer", "publication_stage",
    "publication_authorizations", "publication_attempts", "publication_lineage",
)


def _refuse_or_drop_pre_694(cx):
    """Migration (c): pre-#694 publication.db is DROPPED, never migrated --
    see the ADR-0081 amendment. Evidence for that decision: `nightshift-ctl`
    wires neither this module nor provenance, no publication.db exists
    anywhere in the tree, and the only writers are this module's own main()
    and the tests, which build in tempdirs torn down per test. No row was
    ever written through a supported operator path.

    Enforced, not merely asserted: a version-0 file with rows in ANY
    tenant-bearing table refuses `publication_schema_pre_694` and names the
    tables. A version-0 file that is EMPTY (schema created, nothing written)
    is dropped and rebuilt at the current version -- adopting its tables
    would silently keep `tenant_id TEXT` and `source_snapshot_json`, which
    is precisely the silent coexistence #694 forbids.
    """
    version = cx.execute("PRAGMA user_version").fetchone()[0]
    if version >= PUBLICATION_SCHEMA_VERSION:
        return
    tables = {row[0] for row in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    present = [name for name in _TENANT_BEARING_TABLES if name in tables]
    if not present:
        return                       # brand-new file: nothing to adopt
    populated = []
    for name in present:
        count = cx.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        if count:
            populated.append({"table": name, "rows": count})
    if populated:
        _refuse("publication_schema_pre_694", {
            "user_version": version, "populated": populated,
            "remedy": "pre-#694 publication.db is development-only and is dropped, "
                      "not migrated (ADR-0081 amendment)",
        })
    for name in present + ["publication_review"]:
        cx.execute(f"DROP TABLE IF EXISTS {name}")


def _authority_guard(state_root):
    """ADR-0082's client gate (STEP 5 of the #400 close-out).

    `state_root` here is ALREADY the tenant-scoped root
    `<...>/tenants/<tenant_id>/repos/<repository_id>`, so
    `nightshift_authority.derive_scope` recovers both the real state root
    and the repository_id from it -- no new parameter, and the frozen
    signature documented below stays frozen.

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
        raise PublicationError(exc.reason_code, exc.exit_code, exc.details) from exc


def connect(state_root, *, create=True):
    """Open THIS tenant+repository's publication.db and ATTACH its sibling
    provenance.db READ-ONLY.

    The signature is deliberately FROZEN at `(state_root, *, create)`: the
    provenance path is DERIVED from the already-tenant-scoped root, never
    taken as a parameter, so there is no argument through which tenant B
    could name tenant A's provenance.db. `state_root` here is the value
    `resolve_publication_root` returned, i.e. it is already
    `<...>/tenants/<tenant_id>/repos/<repository_id>`.
    """
    _validate_state_root(state_root)
    _authority_guard(state_root)
    path = db_path(state_root)
    if not create and not os.path.isfile(path):
        _refuse("publication_db_missing")
    provenance_path = os.path.join(state_root, prov.PROVENANCE_DB_FILENAME)
    if not os.path.isfile(provenance_path):
        # #694: publication VALIDATES a source_snapshot, and source_snapshot
        # rows are produced by nightshift_provenance. Standing this module up
        # without its provenance database would recreate exactly the
        # two-islands defect -- a module validating an object nothing in its
        # own process can create. Open provenance.db first
        # (prov.connect(state_root, tenant_id, repository_id)).
        _refuse("provenance_db_missing", {"expected_path": provenance_path})
    # uri=True sets SQLITE_OPEN_URI on the connection, which is what makes
    # the `file:...?mode=ro` ATTACH below actually parse as a URI. Without
    # it, whether URIs are honoured depends on how the local SQLite was
    # compiled (SQLITE_USE_URI), and the failure is SILENT: the ATTACH would
    # create a new, writable file literally named "file:...?mode=ro". The
    # main filename is unaffected -- SQLite treats a name as a URI only when
    # it begins with "file:", which an absolute path never does.
    cx = sqlite3.connect(path, timeout=30, isolation_level=None, uri=True)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    cx.execute("PRAGMA foreign_keys=ON")
    try:
        _refuse_or_drop_pre_694(cx)
        ensure_schema(cx)
        cx.execute("ATTACH DATABASE ? AS prov",
                    ("file:" + provenance_path + "?mode=ro",))
        # Positive check that the URI was honoured rather than taken as a
        # literal filename: `database_list` reports the file each schema
        # actually resolved to.
        attached = {row[1]: row[2] for row in cx.execute("PRAGMA database_list").fetchall()}
        if os.path.realpath(attached.get("prov") or "") != os.path.realpath(provenance_path):
            _operation_failed("provenance_attach_failed", {
                "expected_path": provenance_path, "attached_path": attached.get("prov")})
        cx.executescript(LINEAGE_EDGE_VIEW)
    except Exception:
        cx.close()
        raise
    return cx


def _ensure_column(cx, table, column, decl):
    """Additive, idempotent column migration.

    `CREATE TABLE IF NOT EXISTS` does NOT add a column to a table that
    already exists, so a schema edit alone would silently apply to fresh
    databases and silently NOT apply to existing ones -- the two would
    diverge with no error anywhere. This adds the column when it is absent
    and does nothing when it is present. It is deliberately non-destructive:
    no row is rewritten and no existing value is touched, so it is not the
    pre-#694 drop path and must never become one.
    """
    have = {row[1] for row in cx.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in have:
        cx.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure_schema(cx):
    cx.executescript(PUBLICATION_SCHEMA)
    _ensure_column(cx, "publication_stage", "cipher_id", "TEXT")


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def canonicalize_identity(value):
    """NFC-normalize, strip, casefold -- a raw string compare on identity is
    bypassable (`Eng_Claude` vs `eng_claude`); every self-review and reviewer
    comparison in this module goes through this first."""
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value)).strip().casefold()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(obj) -> bytes:
    """Same canonical form nightshift_project_contract.canonical_bytes
    already establishes -- reused here for authorization_digest so the two
    hardened digest surfaces in this codebase agree on one JSON dialect."""
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"),
    ).encode("ascii")


def authorization_tuple_digest(*, tenant_id, repository_id, content_digest,
                                destination_path, expected_base_sha,
                                reviewer_identity, expires_at):
    payload = {
        "tenant_id": tenant_id,
        "repository_id": repository_id,
        "content_digest": content_digest,
        "destination_path": destination_path,
        "expected_base_sha": expected_base_sha,
        "reviewer_identity": reviewer_identity,
        "expires_at": expires_at,
    }
    return sha256_hex(canonical_json_bytes(payload))


def _glob_allows(pattern, path):
    """True when `path` (a concrete, no-wildcard path) falls under `pattern`.
    Same dialect as bin/nightshift-ctl's `_pattern_overlaps`/`scopes_overlap`
    (`src/**` or `src/` contains `src/foo/x.py`) -- deliberately NOT plain
    fnmatch alone, whose `*` crosses `/` and would make the allowlist far
    wider than a reviewer believes."""
    if fnmatch.fnmatch(path, pattern):
        return True
    base = pattern.rstrip("*").rstrip("/")
    if base and ("*" in pattern or pattern.endswith("/")) and (path == base or path.startswith(base + "/")):
        return True
    return False


def path_allowlisted(path, patterns):
    return any(_glob_allows(pattern, path) for pattern in patterns)


def _detect_secret(text):
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _validate_destination_path_structure(path):
    if not path:
        _refuse("destination_path_empty")
    if len(path.encode("utf-8")) > MAX_PATH_BYTES:
        _refuse("destination_path_too_long")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        _refuse("destination_path_control_characters")
    if path.startswith("/") or path.startswith("~") or (len(path) >= 2 and path[1] == ":"):
        _refuse("destination_path_not_relative")
    normalized = posixpath.normpath(path)
    if normalized != path or normalized in (".", ".."):
        _refuse("destination_path_not_normalized")
    parts = path.split("/")
    if ".." in parts or "" in parts:
        _refuse("destination_path_escapes_repository")
    if ".git" in parts:
        _refuse("destination_path_in_git_dir")
    return path


# ---------------------------------------------------------------------------
# git plumbing -- every call explicit about --git-dir, never relies on cwd
# discovery. See module docstring's "apply plumbing" note: GIT_INDEX_FILE is
# always pointed outside every repository, and the destination is always
# required to be a bare publication target, never a customer's live
# checkout (hash-object -w mutates .git/objects while leaving `git status`
# clean and `.git/index` untouched -- measured; see PLAN doc).
# ---------------------------------------------------------------------------

def _git(git_dir, args, *, input_bytes=None, env_extra=None, check=True):
    cmd = ["git", "--git-dir", git_dir] + list(args)
    env = dict(os.environ)
    env.update(env_extra or {})
    try:
        result = subprocess.run(
            cmd, input=input_bytes, capture_output=True, env=env, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _operation_failed("git_command_failed", {"cmd": args, "error": str(exc)})
    if check and result.returncode != 0:
        raise PublicationError("git_command_failed", 1, {
            "cmd": list(args), "returncode": result.returncode,
            "stderr": result.stderr.decode("utf-8", "replace")[:2000],
        })
    return result


def _is_bare_repository(git_dir):
    result = _git(git_dir, ["rev-parse", "--is-bare-repository"], check=False)
    return result.returncode == 0 and result.stdout.strip() == b"true"


def _rev_parse_commit(git_dir, ref):
    result = _git(git_dir, ["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip().decode("ascii", "replace")


def _cat_file_exists_commit(git_dir, sha):
    result = _git(git_dir, ["cat-file", "-e", f"{sha}^{{commit}}"], check=False)
    return result.returncode == 0


def _ls_tree_entry(git_dir, base_sha, path):
    """One entry of `base_sha`'s tree at `path`, or None. Returns
    (mode_str, type_str, oid) -- used for the symlink/gitlink-ancestor check
    (PG-8/PG-4 handoff note: must be checked against the BASE TREE, never
    the filesystem, or a bare destination silently passes)."""
    result = _git(git_dir, ["ls-tree", base_sha, "--", path], check=False)
    if result.returncode != 0:
        return None
    line = result.stdout.decode("utf-8", "replace").strip()
    if not line:
        return None
    meta, _sep, _name = line.partition("\t")
    mode, obj_type, oid = meta.split()
    return mode, obj_type, oid


def _check_path_against_base_tree(git_dir, base_sha, path):
    """Refuses destination_path_traverses_symlink / _collides_with_existing_type
    / _is_directory_in_base by walking the BASE TREE, per the architect's
    handoff note: a filesystem check would silently pass against a bare
    destination that has no working tree at all."""
    parts = path.split("/")
    for i in range(1, len(parts)):
        ancestor = "/".join(parts[:i])
        entry = _ls_tree_entry(git_dir, base_sha, ancestor)
        if entry is None:
            continue
        mode, _obj_type, _oid = entry
        if mode in ("120000", "160000"):
            _refuse("destination_path_traverses_symlink", {"ancestor": ancestor})
        if mode in ("100644", "100755"):
            _refuse("destination_path_collides_with_existing_type", {"ancestor": ancestor})
    full = _ls_tree_entry(git_dir, base_sha, path)
    if full is not None:
        mode, _obj_type, _oid = full
        if mode == "040000":
            _refuse("destination_path_is_directory_in_base")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def get_policy(cx):
    row = cx.execute("SELECT * FROM publication_policy WHERE id = 1").fetchone()
    return dict(row) if row is not None else None


def policy_set(cx, *, tenant_id, repository_id, mode, path_allowlist,
               target_git_dir, target_ref, key_version, t):
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    if mode not in ("zero_nightshift_file", "no_generated_file"):
        _usage_error("invalid_mode", {"mode": mode})
    if mode == "no_generated_file" and (target_git_dir or target_ref):
        _refuse("egress_target_forbidden_in_strict_mode")
    patterns = sorted(set(p.strip() for p in (path_allowlist or []) if p.strip()))
    allowlist_json = json.dumps(patterns, sort_keys=True)
    allowlist_digest = sha256_hex(canonical_json_bytes(patterns))

    cx.execute("BEGIN IMMEDIATE")
    try:
        existing = cx.execute("SELECT * FROM publication_policy WHERE id = 1").fetchone()
        next_version = (existing["policy_version"] + 1) if existing else 1
        switching_to_strict = mode == "no_generated_file" and (
            existing is None or existing["mode"] != "no_generated_file")
        cx.execute(
            "INSERT INTO publication_policy(id,tenant_id,repository_id,mode,"
            "path_allowlist_json,path_allowlist_digest,target_git_dir,target_ref,"
            "policy_version,key_version,updated_at) VALUES(1,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET tenant_id=excluded.tenant_id,"
            "repository_id=excluded.repository_id,mode=excluded.mode,"
            "path_allowlist_json=excluded.path_allowlist_json,"
            "path_allowlist_digest=excluded.path_allowlist_digest,"
            "target_git_dir=excluded.target_git_dir,target_ref=excluded.target_ref,"
            "policy_version=excluded.policy_version,key_version=excluded.key_version,"
            "updated_at=excluded.updated_at",
            (tenant_id, repository_id, mode, allowlist_json, allowlist_digest,
             target_git_dir, target_ref, next_version, key_version, t),
        )
        revoked = 0
        if switching_to_strict:
            cur = cx.execute(
                "UPDATE publication_authorizations SET revoked_at=?, revoked_reason='mode_changed' "
                "WHERE tenant_id=? AND repository_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
                (t, tenant_id, repository_id),
            )
            revoked = cur.rowcount
        cx.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        cx.execute("ROLLBACK")
        _refuse("policy_check_failed", {"error": str(exc)})
    except Exception:
        cx.execute("ROLLBACK")
        raise
    return {"ok": True, "status": "set", "policy_version": next_version,
            "mode": mode, "revoked_authorizations": revoked}


# ---------------------------------------------------------------------------
# Reviewers
# ---------------------------------------------------------------------------

def register_reviewer(cx, *, tenant_id, reviewer_identity, token_bytes, t):
    _validate_tenant_id(tenant_id)
    if not reviewer_identity:
        _usage_error("reviewer_identity_required")
    if not token_bytes:
        _usage_error("reviewer_token_required")
    canonical = canonicalize_identity(reviewer_identity)
    token_hash = sha256_hex(token_bytes)
    cx.execute("BEGIN IMMEDIATE")
    try:
        cur = cx.execute(
            "INSERT INTO publication_reviewer(tenant_id,reviewer_identity,token_hash,registered_at) "
            "VALUES(?,?,?,?)",
            (tenant_id, canonical, token_hash, t),
        )
        cx.execute("COMMIT")
    except sqlite3.IntegrityError:
        cx.execute("ROLLBACK")
        _refuse("reviewer_already_registered", {"reviewer_identity": canonical})
    return {"ok": True, "reviewer_id": cur.lastrowid, "reviewer_identity": canonical}


def _authenticate_reviewer(cx, tenant_id, reviewer_identity, token_bytes, role="reviewer"):
    """Prove `reviewer_identity` holds its registered token. `publication_reviewer`
    is the tenant's ONE identity registry: a producer authenticates against it
    too (`role="producer"`), so the two sides of PG-3's comparison are both
    credentials rather than one credential and one claim."""
    if not reviewer_identity:
        _usage_error(f"{role}_identity_required")
    if not token_bytes:
        _usage_error(f"{role}_identity_required", {"missing": "token"})
    canonical = canonicalize_identity(reviewer_identity)
    live = cx.execute(
        "SELECT * FROM publication_reviewer WHERE tenant_id=? AND reviewer_identity=? "
        "AND revoked_at IS NULL", (tenant_id, canonical),
    ).fetchone()
    if live is None:
        any_row = cx.execute(
            "SELECT 1 FROM publication_reviewer WHERE tenant_id=? AND reviewer_identity=?",
            (tenant_id, canonical),
        ).fetchone()
        if any_row is not None:
            _refuse(f"{role}_revoked", {f"{role}_identity": canonical})
        _refuse(f"{role}_not_registered", {f"{role}_identity": canonical})
    if not hmac.compare_digest(sha256_hex(token_bytes), live["token_hash"]):
        _refuse(f"{role}_not_authenticated", {f"{role}_identity": canonical})
    return canonical


# ---------------------------------------------------------------------------
# Lineage (PG-7, #400 items 10-11, ADR-0004) -- ONE graph across two files.
#
# Before #694 this module kept its own `publication_lineage` graph and
# `nightshift_provenance.py` kept a separate lineage_node/derived_from
# graph, and they never met: the `input_snapshot` parent_ref recorded here
# was `'snapshot:' + sha256(<a dict the caller made up>)`, which referenced
# NOTHING -- not a row in this database, not a row in provenance's. The
# stated reason was an incompatible tenant_id scoping scheme (TEXT here,
# INTEGER there). #694 resolves that by making INTEGER the one canonical
# type and this module a client of provenance's validator, so the join key
# is now real:
#
#   stage() records parent_ref = <the provenance snapshot_id>, and the
#   `lineage_edge` TEMP view (see LINEAGE_EDGE_VIEW above) UNIONs this
#   table with prov.derived_from under one node-id vocabulary. A single
#   recursive CTE therefore walks
#       pub:attempt:N -> pub:review:N -> pub:stage:N -> <snapshot_id>
#       -> <prior snapshot_id> ...
#   crossing the file boundary mid-walk, and returns nothing at all under
#   the wrong tenant_id.
#
# One vocabulary, two producers, so the `pub:` prefix has ONE owner: this
# module. Provenance's charset ([A-Za-z0-9_.:-]) would otherwise admit
# `pub:stage:1` as a snapshot id -- and it did, which is how a snapshot came
# to alias a stage node and a walk answered with the wrong source. Both
# modules now refuse an id in that namespace on their write paths
# (prov.RESERVED_NODE_ID_PREFIX / LINEAGE_NODE_PREFIX above).
#
# What is enforced at stage() time (#400 items 10-11) now reads
# provenance's rows rather than re-implementing provenance's rules:
# `_validate_source_snapshot_identity` runs against the source_snapshot ROW,
# and `_require_source_snapshot_closure` delegates outright to
# `prov.verify_closure_complete`, through the READ-ONLY attachment.
# ---------------------------------------------------------------------------

_SOURCE_SNAPSHOT_IDENTITY_FIELDS = (
    "repository_fingerprint", "object_format", "commit_oid", "tree_oid")


def _validate_source_snapshot_identity(source_snapshot):
    """#400 item 10: "every ... publication records stable repository
    identity plus exact commit/tree OIDs" -- structurally required on every
    `stage()` call, not merely possible if a caller happens to populate it.

    Since #694 the argument is the source_snapshot ROW read out of
    provenance.db, not a dict the caller assembled. It stays genuinely
    reachable rather than decorative: `prov.record_source_snapshot` accepts
    `repository_fingerprint=''`, `commit_oid=''` and `tree_oid=''` -- NOT
    NULL does not stop the empty string -- so provenance can record a
    snapshot with no identity, and this refuses to publish from it.

    `object_format` is the one field provenance's own CHECK constraint
    (`object_format IN ('sha1','sha256')`) makes structurally impossible to
    get wrong, so the `source_snapshot_invalid` branch below is now a
    backstop against a schema regression rather than a live path. That is
    the correct direction of travel: the check moved EARLIER, to the write.
    """
    missing = [k for k in _SOURCE_SNAPSHOT_IDENTITY_FIELDS
               if not isinstance(source_snapshot.get(k), str) or not source_snapshot.get(k)]
    if missing:
        _refuse("source_snapshot_incomplete", {"missing": missing})
    if source_snapshot["object_format"] not in ("sha1", "sha256"):
        _refuse("source_snapshot_invalid", {"field": "object_format"})


def _require_source_snapshot_closure(cx, tenant_id, repository_id, snapshot_id):
    """#400 item 11: "provenance closure covers required submodule commits
    and Git LFS objects."

    `prov.verify_closure_complete` recomputes the closure manifest digest
    from the `closure_entry` rows provenance.db actually holds and compares
    it to the digest frozen on the immutable source_snapshot row. This
    module reads those rows through the READ-ONLY attachment and computes
    nothing itself. The reason_code stays the literal string
    `closure_incomplete`.

    WHAT THIS DOES AND DOES NOT PROVE -- the earlier wording here claimed
    more than the code delivered, so it is spelled out. Pre-#694 the caller
    passed `closure_required_paths` plus the `closure` list checked against
    it, and a caller that declared nothing was never refused. Moving the
    requirement into provenance did NOT by itself change that: the column
    is NULLable, and `verify_closure_complete` returned True on NULL, so
    "declared nothing" still sailed through -- the omission had only moved
    from this module's kwargs to provenance's optional column.

    What actually closes it is on the provenance side, and it is two
    changes, not one: `record_source_snapshot` now freezes the
    empty-manifest digest when the caller supplies none, so no snapshot
    carries "no claim"; and `verify_closure_complete` refuses a NULL
    column outright instead of passing it. This guard therefore fires on
    EVERY snapshot: it compares a frozen, immutable claim against the rows
    that exist, for all of them.

    The remaining limit is honest and is not this module's to close: a
    frozen "nothing was required" is still only as good as the capture
    that produced it. `prov.capture_dirty_snapshot` discovers the closure
    from the snapshot's own tree for that reason; a caller that hand-builds
    a snapshot row and discovers nothing gets a truthful record of having
    claimed nothing, and publication accepts it."""
    try:
        return prov.verify_closure_complete(
            cx, tenant_id, repository_id, snapshot_id, schema="prov")
    except prov.ProvenanceError as exc:
        raise _as_publication_error(exc) from exc


def _load_source_snapshot(cx, tenant_id, repository_id, snapshot_id):
    """Read the snapshot this publication claims to derive from, out of the
    READ-ONLY provenance attachment, scoped by BOTH tenant_id and
    repository_id.

    There is deliberately NO working-tree fallback and no "synthesize one
    from what the caller told me" path (#103's shape): a snapshot that is
    not in provenance.db does not exist as far as publication is concerned.
    The refusal is also uniform -- "no such snapshot" and "that snapshot
    belongs to another tenant" are indistinguishable, matching
    `prov.get_source_snapshot`'s deliberate no-oracle rule, so a caller
    cannot probe for the existence of another tenant's snapshot ids."""
    try:
        prov.validate_snapshot_id(snapshot_id)
    except prov.ProvenanceError as exc:
        raise _as_publication_error(exc) from exc
    row = cx.execute(
        "SELECT * FROM prov.source_snapshot "
        "WHERE snapshot_id=? AND tenant_id=? AND repository_id=?",
        (snapshot_id, tenant_id, repository_id),
    ).fetchone()
    if row is None:
        _refuse("source_snapshot_not_found", {
            "snapshot_id": snapshot_id, "repository_id": repository_id})
    return dict(row)


def _lineage_node_id(kind, ref):
    """The Python mirror of the node-id expression in LINEAGE_EDGE_VIEW.
    Kept beside the guards that use it so the two cannot drift: an
    input_snapshot parent IS the provenance snapshot_id, everything else is
    `pub:<kind>:<ref>`.

    The other side of that identity lives in the view: a provenance
    `source_snapshot` node is addressed by its `ref_id`, so this bare
    snapshot id meets it there whatever the node was NAMED."""
    if kind == "input_snapshot":
        return str(ref)
    return "%s%s:%s" % (LINEAGE_NODE_PREFIX, kind, ref)


def record_publication_lineage(cx, *, tenant_id, repository_id, child_kind, child_id,
                                parent_kind, parent_ref, t, source_not_retained=False):
    """The ONLY writer of publication_lineage. Two structural refusals live
    here rather than in the callers, because the graph's soundness is a
    property of the edge, not of who wrote it:

      * an `input_snapshot` parent_ref may not sit in the reserved `pub:`
        namespace -- provenance already refuses to mint such an id, and
        this is the second lock on the same door, on the side that would
        actually store the aliased edge;
      * no self-edge, matching `prov.record_derived_from`'s
        `lineage_self_loop_refused`. A self-edge is not merely useless: the
        unified walk re-enters it at depth+1 forever.
    """
    child_node_id = _lineage_node_id(child_kind, child_id)
    parent_node_id = _lineage_node_id(parent_kind, parent_ref)
    if parent_kind == "input_snapshot" and parent_node_id.startswith(LINEAGE_NODE_PREFIX):
        _refuse("reserved_node_id_namespace",
                {"id": parent_node_id, "reserved_prefix": LINEAGE_NODE_PREFIX})
    if parent_node_id == child_node_id:
        _refuse("lineage_self_loop_refused",
                {"node_id": child_node_id})
    cx.execute(
        "INSERT INTO publication_lineage(tenant_id,repository_id,child_kind,child_id,"
        "parent_kind,parent_ref,source_not_retained,recorded_at) VALUES(?,?,?,?,?,?,?,?)",
        (tenant_id, repository_id, child_kind, child_id, parent_kind, parent_ref,
         1 if source_not_retained else 0, t),
    )


def _lineage_parent_edge(cx, tenant_id, repository_id, child_node_id):
    """One hop backward through the UNIFIED graph. Reads `lineage_edge`, so
    a walk started in publication continues into provenance without the
    caller knowing there are two files. `edge_seq` preserves the pre-#694
    "oldest edge first" tie-break for a stage that has both an
    input_snapshot parent and a derived-from-stage parent."""
    return cx.execute(
        "SELECT * FROM lineage_edge WHERE tenant_id=? AND repository_id=? AND child_node_id=? "
        "ORDER BY edge_seq IS NULL, edge_seq ASC LIMIT 1",
        (tenant_id, repository_id, child_node_id),
    ).fetchone()


def publication_lineage_chain(cx, *, tenant_id, repository_id, attempt_id):
    """Walk the real derived_from chain backward from a published attempt:
    attempt -> review -> stage -> input_snapshot. Returns
    `{"closed": bool, "chain": [...]}`, oldest link last is never true here
    -- `chain` is ordered attempt-first (newest to oldest), mirroring
    `nightshift_provenance.lineage_chain`'s own return shape so a caller
    reading both isn't handed two different conventions. `closed` is True
    only when every hop down to `input_snapshot` was found; a missing hop
    stops the walk and reports which link is missing, exactly like
    `nightshift_provenance.lineage_chain` does for its own graph.

    Since #694 every hop is a query against the unified `lineage_edge`
    view, and the `input_snapshot` link's id is a REAL provenance
    snapshot_id (it used to be a digest of a caller-supplied dict, which
    referenced nothing). The return shape is unchanged."""
    attempt = cx.execute(
        "SELECT * FROM publication_attempts WHERE id=? AND tenant_id=? AND repository_id=?",
        (attempt_id, tenant_id, repository_id),
    ).fetchone()
    if attempt is None:
        return {"closed": False, "missing": "attempt", "chain": []}
    chain = [{"kind": "attempt", "id": attempt_id}]

    review_link = _lineage_parent_edge(cx, tenant_id, repository_id,
                                        _lineage_node_id("attempt", attempt_id))
    if review_link is None or review_link["edge_kind"] != "review":
        return {"closed": False, "missing": "review", "chain": chain}
    review_id = int(review_link["parent_node_id"].rpartition(":")[2])
    chain.append({"kind": "review", "id": review_id})

    stage_link = _lineage_parent_edge(cx, tenant_id, repository_id,
                                       _lineage_node_id("review", review_id))
    if stage_link is None or stage_link["edge_kind"] != "stage":
        return {"closed": False, "missing": "stage", "chain": chain}
    stage_id = int(stage_link["parent_node_id"].rpartition(":")[2])
    chain.append({"kind": "stage", "id": stage_id})

    snapshot_link = _lineage_parent_edge(cx, tenant_id, repository_id,
                                          _lineage_node_id("stage", stage_id))
    if snapshot_link is None or snapshot_link["edge_kind"] != "input_snapshot":
        return {"closed": False, "missing": "input_snapshot", "chain": chain}
    chain.append({"kind": "input_snapshot", "id": snapshot_link["parent_node_id"],
                  "source_not_retained": bool(snapshot_link["source_not_retained"])})

    return {"closed": True, "chain": chain}


def lineage_chain_from_commit(cx, *, tenant_id, repository_id, commit_oid):
    """#694's headline query: from a commit that LANDED in the customer
    repository, back to the exact source snapshot it was built from -- in
    ONE recursive statement over the unified graph, crossing from
    publication.db into provenance.db mid-walk.

    Returns `{"closed", "chain", "truncated", "snapshot_id",
    "source_commit_oid", "repository_fingerprint", "source_not_retained"}`.
    `chain` is oldest-last (attempt first), the same convention
    `publication_lineage_chain` uses, with each node appearing once at the
    shallowest depth it was reached. Under a tenant_id that does not own
    the commit the seed row matches nothing and `chain` is empty -- the
    scope filter is on the seed AND on every recursive hop, so a walk can
    never step sideways into another tenant's edges.

    `truncated` is True when the walk REACHED LINEAGE_WALK_DEPTH_LIMIT or
    LINEAGE_WALK_ROW_LIMIT -- either it was cut short there, or the graph
    ended exactly on the bound and the walk cannot tell those apart, so it
    reports the pessimistic answer. It is reported, never hidden: a
    truncated walk that did not reach a snapshot returns `closed: False`,
    so a bound is always visible as "I did not finish", never as "there is
    nothing there".

    Refuses `lineage_namespace_collision` if a node id in the walk is
    claimed by both graphs (see LINEAGE_EDGE_VIEW). That cannot be produced
    through either module's write path any more; if it is ever seen, the id
    has two meanings and the honest answer is a refusal, not a guess."""
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    # Still ONE recursive statement over the unified view -- but a BOUNDED
    # one. The bounds are not decoration: `UNION` dedupes on the whole row,
    # so a cycle (a self-edge is the smallest one) re-enters at depth+1
    # forever and the uncapped form never returns. `w.depth <` bounds the
    # descent; the outer LIMIT bounds the total, which a depth cap alone
    # does not -- one node can be reached at many different depths, so the
    # depth cap alone leaves the work proportional to the graph.
    # The outer select deliberately does NOT sort: SQLite only halts the
    # recursion early for an unsorted LIMIT, so the ordering the caller
    # sees (depth first, then node id -- the pre-existing convention) is
    # applied below, on rows already in hand.
    rows = cx.execute(
        "WITH RECURSIVE walk(node_id, depth) AS ("
        "  SELECT '" + LINEAGE_NODE_PREFIX + "attempt:'||id, 0 FROM publication_attempts"
        "   WHERE commit_oid=? AND tenant_id=? AND repository_id=? AND state='applied'"
        "  UNION"
        "  SELECT e.parent_node_id, w.depth+1 FROM lineage_edge e"
        "    JOIN walk w ON e.child_node_id = w.node_id"
        "   WHERE e.tenant_id=? AND e.repository_id=? AND w.depth < ?"
        ") SELECT node_id, depth FROM walk LIMIT ?",
        (commit_oid, tenant_id, repository_id, tenant_id, repository_id,
         LINEAGE_WALK_DEPTH_LIMIT, LINEAGE_WALK_ROW_LIMIT + 1),
    ).fetchall()
    truncated = len(rows) > LINEAGE_WALK_ROW_LIMIT
    rows = rows[:LINEAGE_WALK_ROW_LIMIT]
    truncated = truncated or any(row["depth"] >= LINEAGE_WALK_DEPTH_LIMIT for row in rows)
    # One node, one entry: a node reachable by several paths is one node,
    # and it is reported at the shallowest depth it was reached at.
    chain, seen = [], set()
    for row in sorted(rows, key=lambda r: (r["depth"], r["node_id"])):
        if row["node_id"] in seen:
            continue
        seen.add(row["node_id"])
        chain.append(row["node_id"])

    # Disjointness, checked rather than trusted. Both write paths refuse a
    # `pub:`-prefixed id on the provenance side, so a node that is BOTH a
    # publication node id and a provenance row can now only come from a row
    # written around those validators. If one is ever met, the walk has two
    # different meanings for one id and cannot say which source a commit
    # came from -- so it refuses instead of picking one, which is exactly
    # the silent wrong answer this refusal replaces.
    # The predicate is a RANGE over the reserved prefix, not `LIKE 'pub:%'`
    # and not an IN list built from the chain: a range uses the primary-key
    # index on either table, costs nothing when (as designed) there are no
    # such rows, and its parameter count does not grow with the walk.
    collisions = set()
    pub_nodes = {node_id for node_id in chain if node_id.startswith(LINEAGE_NODE_PREFIX)}
    if pub_nodes:
        low = LINEAGE_NODE_PREFIX
        high = LINEAGE_NODE_PREFIX[:-1] + chr(ord(LINEAGE_NODE_PREFIX[-1]) + 1)
        claimed = cx.execute(
            "SELECT node_id FROM prov.lineage_node "
            " WHERE tenant_id=? AND repository_id=? AND node_id>=? AND node_id<? "
            "UNION "
            "SELECT snapshot_id FROM prov.source_snapshot "
            " WHERE tenant_id=? AND repository_id=? AND snapshot_id>=? AND snapshot_id<?",
            (tenant_id, repository_id, low, high,
             tenant_id, repository_id, low, high),
        ).fetchall()
        collisions.update(pub_nodes.intersection(row[0] for row in claimed))
    # The SNAPSHOT-ID half of the same namespace. A publication
    # `input_snapshot` parent enters the walk as a bare snapshot id, so a
    # provenance lineage_node of any OTHER kind carrying that id would be
    # walked as if it were the snapshot -- and the `snapshot = ...` loop
    # below would then hand a landed commit that snapshot's identity while
    # having actually traversed some worker_output's ancestry. Both write
    # paths refuse the row (prov.record_lineage_node /
    # prov.record_source_snapshot); this is the read-side backstop for a
    # row written around them, and it costs one indexed join that returns
    # nothing in the designed case.
    aliased = cx.execute(
        "SELECT n.node_id FROM prov.lineage_node n "
        "JOIN prov.source_snapshot s ON s.snapshot_id = n.node_id "
        " AND s.tenant_id = n.tenant_id AND s.repository_id = n.repository_id "
        "WHERE n.tenant_id=? AND n.repository_id=? AND n.node_kind<>'source_snapshot'",
        (tenant_id, repository_id),
    ).fetchall()
    collisions.update({row[0] for row in aliased}.intersection(chain))
    if collisions:
        _refuse("lineage_namespace_collision", {
            "node_ids": sorted(collisions),
            "reserved_prefix": LINEAGE_NODE_PREFIX})

    snapshot = None
    for node_id in chain:
        # Sound because `pub:` is reserved and the collision check above
        # just proved it: a node outside that namespace is provenance's or
        # nothing at all.
        if node_id.startswith(LINEAGE_NODE_PREFIX):
            continue
        candidate = cx.execute(
            "SELECT * FROM prov.source_snapshot "
            "WHERE snapshot_id=? AND tenant_id=? AND repository_id=?",
            (node_id, tenant_id, repository_id),
        ).fetchone()
        if candidate is not None:
            snapshot = candidate
            break
    if snapshot is None:
        return {"closed": False, "chain": chain, "truncated": truncated,
                "snapshot_id": None,
                "source_commit_oid": None, "repository_fingerprint": None,
                "source_not_retained": None}
    return {
        "closed": True,
        "chain": chain,
        "truncated": truncated,
        "snapshot_id": snapshot["snapshot_id"],
        "source_commit_oid": snapshot["commit_oid"],
        "repository_fingerprint": snapshot["repository_fingerprint"],
        "source_not_retained": bool(snapshot["source_not_retained"]),
    }


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------

def get_stage(cx, stage_id):
    row = cx.execute("SELECT * FROM publication_stage WHERE id=?", (stage_id,)).fetchone()
    return dict(row) if row is not None else None


def _ancestry_producer_identities(cx, stage_id):
    seen = set()
    current = get_stage(cx, stage_id)
    hops = 0
    while current is not None and current.get("derived_from_stage_id") and hops < ANCESTRY_WALK_LIMIT:
        parent_id = current["derived_from_stage_id"]
        if parent_id in seen:
            break
        seen.add(parent_id)
        parent = get_stage(cx, parent_id)
        if parent is None:
            break
        yield parent["producer_identity"]
        current = parent
        hops += 1


def stage(cx, *, tenant_id, repository_id, content_bytes, destination_path,
          classification, producer_identity, producer_run_id, snapshot_id,
          producer_token_bytes=None,
          derived_from_stage_id=None, memory_artifact_id=None,
          content_digest_supplied=None, key_version=None, cipher_id=None, t):
    """#694: `snapshot_id` names a REAL row in provenance.db. The old
    `source_snapshot` dict parameter is gone -- a caller can no longer
    describe the source it read, only point at the snapshot provenance
    recorded. `source_not_retained` is likewise no longer a caller kwarg:
    it is read off that row, so the lineage edge cannot claim the bytes are
    retained when provenance says they were expired.

    `producer_identity` is authenticated with `producer_token_bytes`, the
    same way a reviewer is. As a bare claim, a worker holding a reviewer
    token could stage its own work as someone else's and then approve it,
    because PG-3 compares the reviewer against whatever the stage row says."""
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    if content_digest_supplied is not None:
        _refuse("digest_must_not_be_supplied")
    destination_path = _validate_destination_path_structure(destination_path)
    if not isinstance(content_bytes, (bytes, bytearray)):
        _usage_error("content_not_readable")
    if len(content_bytes) == 0:
        _refuse("content_empty")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        _refuse("content_too_large", {"size": len(content_bytes), "max": MAX_CONTENT_BYTES})
    try:
        text = bytes(content_bytes).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _refuse("content_not_utf8")
    if not classification:
        _refuse("classification_required")
    if classification.strip().lower() in DENIED_CLASSIFICATIONS:
        _refuse("classification_not_publishable", {"classification": classification})
    secret = _detect_secret(text)
    if secret:
        _refuse("secret_detected", {"pattern": secret})
    if not producer_identity:
        _refuse("producer_identity_required")
    _authenticate_reviewer(cx, tenant_id, producer_identity, producer_token_bytes,
                           role="producer")
    if not producer_run_id:
        _refuse("producer_run_id_required")
    if not snapshot_id:
        _refuse("source_snapshot_required")
    source_snapshot = _load_source_snapshot(cx, tenant_id, repository_id, snapshot_id)
    _validate_source_snapshot_identity(source_snapshot)
    _require_source_snapshot_closure(cx, tenant_id, repository_id, snapshot_id)
    source_not_retained = bool(source_snapshot["source_not_retained"])
    if derived_from_stage_id is not None and get_stage(cx, derived_from_stage_id) is None:
        _refuse("source_snapshot_invalid", {"derived_from_stage_id": derived_from_stage_id,
                                             "reason": "no such stage"})

    content_digest = sha256_hex(bytes(content_bytes))
    policy = get_policy(cx)
    policy_version = policy["policy_version"] if policy else 0

    # ADR-0080 STEP 9: encryption is DECLARED BY POLICY, enforced here.
    # A `publication_policy` row carrying a `key_version` is the operator
    # saying "bodies for this tenant/repository are envelope-encrypted".
    # Once that is said, a stage that carries no key version is refused --
    # otherwise the very first caller who forgot the argument would write a
    # plaintext body into an encrypted repository and nothing would notice.
    policy_key_version = policy["key_version"] if policy else None
    if policy_key_version is not None and key_version is None:
        _refuse("encryption_required_key_version_missing", {
            "policy_key_version": policy_key_version,
            "remedy": "pass key_version= from "
                      "nightshift_tenant_keys.wrap_object_key()"})
    if key_version is not None:
        if isinstance(key_version, bool) or not isinstance(key_version, int) or key_version < 1:
            _refuse("invalid_key_version", {"key_version": key_version})
        if cipher_id is None:
            _refuse("cipher_id_required_with_key_version", {
                "key_version": key_version,
                "remedy": "pass the cipher_id the envelope was sealed under "
                          "(nightshift_tenant_keys.DEFAULT_CIPHER_ID)"})
    if cipher_id is not None and not isinstance(cipher_id, str):
        _refuse("invalid_cipher_id", {"cipher_id": cipher_id})

    cx.execute("BEGIN IMMEDIATE")
    try:
        try:
            cur = cx.execute(
                "INSERT INTO publication_stage(tenant_id,repository_id,content_digest,"
                "content_size,content,destination_path,classification,producer_identity,"
                "producer_run_id,source_snapshot_id,repository_fingerprint,"
                "snapshot_commit_oid,snapshot_tree_oid,snapshot_object_format,"
                "derived_from_stage_id,memory_artifact_id,policy_version,key_version,"
                "cipher_id,staged_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, repository_id, content_digest, len(content_bytes),
                 sqlite3.Binary(bytes(content_bytes)), destination_path, classification,
                 producer_identity, producer_run_id, snapshot_id,
                 source_snapshot["repository_fingerprint"], source_snapshot["commit_oid"],
                 source_snapshot["tree_oid"], source_snapshot["object_format"],
                 derived_from_stage_id, memory_artifact_id, policy_version, key_version,
                 cipher_id, t),
            )
            new_stage_id = cur.lastrowid
            # PG-7: real derived_from edge, recorded in the SAME transaction
            # as the stage row it describes -- a stage can never exist
            # without its input_snapshot lineage edge, or vice versa. Since
            # #694 the parent_ref IS the provenance snapshot_id, so this
            # edge joins the two graphs instead of pointing at a digest of
            # something that was never a row anywhere.
            record_publication_lineage(
                cx, tenant_id=tenant_id, repository_id=repository_id,
                child_kind="stage", child_id=new_stage_id,
                parent_kind="input_snapshot", parent_ref=snapshot_id,
                t=t, source_not_retained=source_not_retained,
            )
            if derived_from_stage_id is not None:
                record_publication_lineage(
                    cx, tenant_id=tenant_id, repository_id=repository_id,
                    child_kind="stage", child_id=new_stage_id,
                    parent_kind="stage", parent_ref=str(derived_from_stage_id), t=t,
                )
        except sqlite3.IntegrityError:
            existing = cx.execute(
                "SELECT * FROM publication_stage WHERE tenant_id=? AND repository_id=? "
                "AND content_digest=? AND destination_path=?",
                (tenant_id, repository_id, content_digest, destination_path),
            ).fetchone()
            cx.execute("ROLLBACK")
            if existing is None:
                _operation_failed("stage_write_failed")
            if (existing["classification"] != classification
                    or existing["producer_identity"] != producer_identity
                    or existing["producer_run_id"] != producer_run_id):
                _refuse("stage_immutable", {"stage_id": existing["id"],
                                             "reason": "identical bytes/path, conflicting metadata"})
            return {"ok": True, "status": "existing", "stage_id": existing["id"],
                    "content_digest": content_digest}
        cx.execute("COMMIT")
    except PublicationError:
        raise
    except Exception:
        cx.execute("ROLLBACK")
        raise
    return {"ok": True, "status": "staged", "stage_id": cur.lastrowid,
            "content_digest": content_digest, "destination_path": destination_path}


# ---------------------------------------------------------------------------
# Review (PG-3: self-review detection, first line of defense)
# ---------------------------------------------------------------------------

def _assert_not_self_review(cx, stage_row, reviewer_canonical, reviewer_run_id):
    if reviewer_canonical == canonicalize_identity(stage_row["producer_identity"]):
        _refuse("self_review", {"stage_id": stage_row["id"]})
    if reviewer_run_id and reviewer_run_id == stage_row["producer_run_id"]:
        _refuse("self_review_same_run", {"stage_id": stage_row["id"]})
    for ancestor_identity in _ancestry_producer_identities(cx, stage_row["id"]):
        if reviewer_canonical == canonicalize_identity(ancestor_identity):
            _refuse("self_review_via_lineage", {"stage_id": stage_row["id"]})


def review(cx, *, tenant_id, repository_id, stage_id, reviewer_identity,
           reviewer_run_id, token_bytes, verdict, notes=None, t):
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    if verdict not in ("approve", "reject"):
        _usage_error("invalid_verdict", {"verdict": verdict})
    stage_row = get_stage(cx, stage_id)
    if stage_row is None:
        _refuse("stage_not_found", {"stage_id": stage_id})
    if stage_row["tenant_id"] != tenant_id:
        _refuse("tenant_mismatch")
    if stage_row["repository_id"] != repository_id:
        _refuse("repository_mismatch")
    if not reviewer_run_id:
        _usage_error("reviewer_identity_required", {"missing": "reviewer_run_id"})

    # Registration/authentication FIRST (PG-3 check 4) -- an unregistered
    # caller learns nothing else about the stage.
    canonical = _authenticate_reviewer(cx, tenant_id, reviewer_identity, token_bytes)
    # Checks 1-3.
    _assert_not_self_review(cx, stage_row, canonical, reviewer_run_id)

    reviewed_digest = sha256_hex(bytes(stage_row["content"]))
    if reviewed_digest != stage_row["content_digest"]:
        _operation_failed("reviewed_digest_mismatch", {"stage_id": stage_id})

    cx.execute("BEGIN IMMEDIATE")
    try:
        cur = cx.execute(
            "INSERT INTO publication_review(stage_id,reviewer_identity,reviewer_run_id,"
            "reviewed_digest,verdict,notes,reviewed_at) VALUES(?,?,?,?,?,?,?)",
            (stage_id, canonical, reviewer_run_id, reviewed_digest, verdict, notes, t),
        )
        # PG-7: reviewed patch derives from worker output (the stage row),
        # recorded regardless of verdict -- a rejection is still a real
        # review of that stage and belongs in the chain.
        record_publication_lineage(
            cx, tenant_id=tenant_id, repository_id=repository_id,
            child_kind="review", child_id=cur.lastrowid,
            parent_kind="stage", parent_ref=str(stage_id), t=t,
        )
        cx.execute("COMMIT")
    except Exception:
        cx.execute("ROLLBACK")
        raise
    return {"ok": True, "review_id": cur.lastrowid, "stage_id": stage_id,
            "verdict": verdict, "reviewed_digest": reviewed_digest,
            "reviewer_identity": canonical}


# ---------------------------------------------------------------------------
# Approve -> one-use authorization over the 7-tuple (PG-2)
# ---------------------------------------------------------------------------

def approve(cx, *, tenant_id, repository_id, stage_id, review_id,
            reviewer_identity, reviewer_run_id, token_bytes,
            expected_base_sha, ttl, t):
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    stage_row = get_stage(cx, stage_id)
    if stage_row is None:
        _refuse("stage_not_found", {"stage_id": stage_id})
    if stage_row["tenant_id"] != tenant_id or stage_row["repository_id"] != repository_id:
        _refuse("tenant_mismatch" if stage_row["tenant_id"] != tenant_id else "repository_mismatch")

    review_row = cx.execute(
        "SELECT * FROM publication_review WHERE id=?", (review_id,)).fetchone()
    if review_row is None or review_row["stage_id"] != stage_id:
        _refuse("review_not_found", {"review_id": review_id})
    latest_id = cx.execute(
        "SELECT MAX(id) AS latest FROM publication_review WHERE stage_id=?",
        (stage_id,)).fetchone()["latest"]
    if latest_id != review_id:
        _refuse("review_superseded", {"review_id": review_id, "latest_review_id": latest_id})
    if review_row["verdict"] != "approve":
        _refuse("review_verdict_reject", {"review_id": review_id})

    canonical = _authenticate_reviewer(cx, tenant_id, reviewer_identity, token_bytes)
    if canonical != review_row["reviewer_identity"] or reviewer_run_id != review_row["reviewer_run_id"]:
        _refuse("review_reviewer_mismatch", {"review_id": review_id})
    # Re-assert self-review at approve (design: checked at review AND approve AND apply).
    _assert_not_self_review(cx, stage_row, canonical, reviewer_run_id)

    reviewed_digest = sha256_hex(bytes(stage_row["content"]))
    if reviewed_digest != review_row["reviewed_digest"] or reviewed_digest != stage_row["content_digest"]:
        _operation_failed("reviewed_digest_mismatch", {"stage_id": stage_id})

    policy = get_policy(cx)
    if policy is None:
        _refuse("policy_not_initialised")
    if policy["mode"] == "no_generated_file":
        _refuse("publication_disabled_no_generated_file")
    if not policy["target_git_dir"] or not policy["target_ref"]:
        _refuse("egress_target_not_configured")

    if not expected_base_sha:
        _refuse("expected_base_sha_required")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_base_sha or ""):
        _refuse("invalid_expected_base_sha", {"expected_base_sha": expected_base_sha})
    if ttl is None:
        _refuse("expiry_required")
    try:
        ttl = float(ttl)
    except (TypeError, ValueError):
        _refuse("invalid_expiry", {"ttl": ttl})
    if not (ttl == ttl and ttl not in (float("inf"), float("-inf"))) or ttl <= 0 or ttl > DEFAULT_MAX_TTL_SECONDS:
        _refuse("invalid_expiry", {"ttl": ttl, "max": DEFAULT_MAX_TTL_SECONDS})

    target_git_dir = policy["target_git_dir"]
    target_ref = policy["target_ref"]
    if not os.path.isdir(target_git_dir):
        _refuse("target_git_dir_invalid", {"target_git_dir": target_git_dir})
    if not _cat_file_exists_commit(target_git_dir, expected_base_sha):
        _refuse("expected_base_sha_unknown_to_destination", {"expected_base_sha": expected_base_sha})
    current = _rev_parse_commit(target_git_dir, target_ref)
    if current is None:
        _refuse("target_ref_missing", {"target_ref": target_ref})
    if current != expected_base_sha:
        _refuse("expected_base_sha_stale_at_approval", {
            "expected_base_sha": expected_base_sha, "current_base_sha": current})

    destination_path = stage_row["destination_path"]
    allowlist_patterns = json.loads(policy["path_allowlist_json"])
    if not path_allowlisted(destination_path, allowlist_patterns):
        _refuse("destination_path_not_allowlisted", {"destination_path": destination_path})
    _check_path_against_base_tree(target_git_dir, expected_base_sha, destination_path)

    expires_at = t + ttl
    digest = authorization_tuple_digest(
        tenant_id=tenant_id, repository_id=repository_id,
        content_digest=stage_row["content_digest"], destination_path=destination_path,
        expected_base_sha=expected_base_sha, reviewer_identity=canonical, expires_at=expires_at,
    )
    cx.execute("BEGIN IMMEDIATE")
    try:
        try:
            cur = cx.execute(
                "INSERT INTO publication_authorizations(tenant_id,repository_id,content_digest,"
                "destination_path,expected_base_sha,reviewer_identity,expires_at,"
                "authorization_digest,stage_id,review_id,policy_version,path_allowlist_digest,"
                "mode,target_git_dir,target_ref,git_object_format,issued_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, repository_id, stage_row["content_digest"], destination_path,
                 expected_base_sha, canonical, expires_at, digest, stage_id, review_id,
                 policy["policy_version"], policy["path_allowlist_digest"], policy["mode"],
                 target_git_dir, target_ref, "sha1", t),
            )
        except sqlite3.IntegrityError:
            cx.execute("ROLLBACK")
            _refuse("duplicate_live_authorization", {"authorization_digest": digest})
        cx.execute("COMMIT")
    except PublicationError:
        raise
    except Exception:
        cx.execute("ROLLBACK")
        raise
    return {"ok": True, "authorization_id": cur.lastrowid, "authorization_digest": digest,
            "expires_at": expires_at, "expected_base_sha": expected_base_sha,
            "destination_path": destination_path}


# ---------------------------------------------------------------------------
# The one-use CAS (PG-2). See module docstring -- this is the sole
# authorizer; everything else in `apply_publication` around it exists only
# to produce a legible reason_code when this returns 0.
# ---------------------------------------------------------------------------

def consume_publication_authorization(cx, *, tenant_id, repository_id, content_digest,
                                       destination_path, observed_base_sha,
                                       reviewer_identity, authorization_digest,
                                       attempt_id, t):
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    cur = cx.execute(
        "UPDATE publication_authorizations SET consumed_at=?, consumed_by_attempt=? "
        "WHERE tenant_id=? AND repository_id=? AND content_digest=? "
        "AND destination_path=? AND expected_base_sha=? AND reviewer_identity=? "
        "AND authorization_digest=? AND expires_at>? "
        "AND consumed_at IS NULL AND revoked_at IS NULL",
        (t, attempt_id, tenant_id, repository_id, content_digest, destination_path,
         observed_base_sha, reviewer_identity, authorization_digest, t),
    )
    return cur.rowcount == 1


def _diagnose_authorization_failure(cx, *, authorization_id, tenant_id, repository_id,
                                     presented_digest, presented_path, observed_base_sha,
                                     authorization_digest_presented, t):
    row = cx.execute(
        "SELECT * FROM publication_authorizations WHERE id=?", (authorization_id,)).fetchone()
    if row is None:
        return "authorization_not_found", {}
    if row["tenant_id"] != tenant_id:
        return "tenant_mismatch", {}
    if row["repository_id"] != repository_id:
        return "repository_mismatch", {}
    if row["consumed_at"] is not None:
        return "authorization_already_consumed", {"consumed_at": row["consumed_at"],
                                                    "consumed_by_attempt": row["consumed_by_attempt"]}
    if row["revoked_at"] is not None:
        return "authorization_revoked", {"revoked_reason": row["revoked_reason"]}
    if row["expires_at"] <= t:
        return "authorization_expired", {"expires_at": row["expires_at"], "now": t}
    if row["content_digest"] != presented_digest:
        return "content_digest_mismatch", {"authorized": row["content_digest"], "presented": presented_digest}
    if row["destination_path"] != presented_path:
        return "destination_path_mismatch", {"authorized": row["destination_path"], "presented": presented_path}
    if row["expected_base_sha"] != observed_base_sha:
        return "base_moved", {"authorized_base": row["expected_base_sha"], "observed_base": observed_base_sha}
    if row["authorization_digest"] != authorization_digest_presented:
        return "authorization_digest_mismatch", {}
    return "authorization_consume_failed", {}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _attempt_receipt(row):
    return {"ok": row["state"] == "applied", "status": row["state"], "attempt_id": row["id"],
            "commit_oid": row["commit_oid"], "tree_oid": row["tree_oid"], "blob_oid": row["blob_oid"],
            "reason_code": row["reason_code"]}


def apply_publication(cx, *, tenant_id, repository_id, authorization_id, stage_id,
                       idempotency_key, state_root, t, commit_message=None):
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    if not idempotency_key:
        _usage_error("missing_argument", {"argument": "idempotency_key"})

    # Idempotency checked FIRST, before the authorization is ever touched.
    existing_attempt = cx.execute(
        "SELECT * FROM publication_attempts WHERE tenant_id=? AND repository_id=? AND idempotency_key=?",
        (tenant_id, repository_id, idempotency_key),
    ).fetchone()
    if existing_attempt is not None:
        if existing_attempt["state"] == "applied":
            return _attempt_receipt(existing_attempt)
        _refuse("attempt_in_doubt", {"attempt_id": existing_attempt["id"],
                                      "state": existing_attempt["state"]})

    auth = cx.execute(
        "SELECT * FROM publication_authorizations WHERE id=?", (authorization_id,)).fetchone()
    if auth is None:
        _refuse("authorization_not_found", {"authorization_id": authorization_id})
    if auth["tenant_id"] != tenant_id:
        _refuse("tenant_mismatch")
    if auth["repository_id"] != repository_id:
        _refuse("repository_mismatch")

    presented_stage = get_stage(cx, stage_id)
    if presented_stage is None:
        _refuse("stage_not_found", {"stage_id": stage_id})
    if presented_stage["tenant_id"] != tenant_id or presented_stage["repository_id"] != repository_id:
        _refuse("tenant_mismatch" if presented_stage["tenant_id"] != tenant_id else "repository_mismatch")

    # Re-assert self-review against whatever is actually about to be written
    # (the PRESENTED stage, which may legitimately differ from the stage
    # that was originally approved -- that drift is exactly what the CAS
    # below is there to catch).
    _assert_not_self_review(cx, presented_stage, auth["reviewer_identity"], None)

    presented_digest = sha256_hex(bytes(presented_stage["content"]))
    presented_path = presented_stage["destination_path"]

    policy = get_policy(cx)
    if policy is None:
        _refuse("policy_not_initialised")
    if policy["mode"] != auth["mode"]:
        _refuse("mode_changed_since_approval", {"authorized_mode": auth["mode"], "current_mode": policy["mode"]})
    if policy["mode"] == "no_generated_file":
        _refuse("no_egress_credential")
    if policy["policy_version"] != auth["policy_version"]:
        _refuse("policy_version_changed_since_approval")
    if policy["path_allowlist_digest"] != auth["path_allowlist_digest"]:
        _refuse("path_allowlist_changed_since_approval")

    target_git_dir = auth["target_git_dir"]
    target_ref = auth["target_ref"]
    if not os.path.isdir(target_git_dir):
        _refuse("target_git_dir_invalid", {"target_git_dir": target_git_dir})
    if not os.access(target_git_dir, os.W_OK):
        _refuse("target_not_writable", {"target_git_dir": target_git_dir})
    if not _is_bare_repository(target_git_dir):
        # The destination must never be a live checkout (see module
        # docstring / PG-4 handoff): hash-object -w mutates .git/objects
        # while `git status --porcelain` stays clean, so a working-tree
        # repo is refused outright rather than trusted to "look clean".
        _refuse("target_is_source_checkout", {"target_git_dir": target_git_dir})

    # L1 OBSERVE, immediately before the transaction -- the observed value,
    # never a caller claim, is what the CAS matches against.
    observed_base_sha = _rev_parse_commit(target_git_dir, target_ref)
    if observed_base_sha is None:
        _refuse("target_ref_missing", {"target_ref": target_ref})

    # T1
    cx.execute("BEGIN IMMEDIATE")
    try:
        try:
            cur = cx.execute(
                "INSERT INTO publication_attempts(tenant_id,repository_id,authorization_id,"
                "stage_id,idempotency_key,observed_base_sha,target_git_dir,target_ref,"
                "state,started_at) VALUES(?,?,?,?,?,?,?,?,'intent',?)",
                (tenant_id, repository_id, authorization_id, stage_id, idempotency_key,
                 observed_base_sha, target_git_dir, target_ref, t),
            )
        except sqlite3.IntegrityError:
            cx.execute("ROLLBACK")
            _refuse("attempt_in_doubt", {"idempotency_key": idempotency_key,
                                          "reason": "concurrent submission"})
        attempt_id = cur.lastrowid

        ok = consume_publication_authorization(
            cx, tenant_id=tenant_id, repository_id=repository_id,
            content_digest=presented_digest, destination_path=presented_path,
            observed_base_sha=observed_base_sha, reviewer_identity=auth["reviewer_identity"],
            authorization_digest=auth["authorization_digest"], attempt_id=attempt_id, t=t,
        )
        if not ok:
            reason_code, details = _diagnose_authorization_failure(
                cx, authorization_id=authorization_id, tenant_id=tenant_id,
                repository_id=repository_id, presented_digest=presented_digest,
                presented_path=presented_path, observed_base_sha=observed_base_sha,
                authorization_digest_presented=auth["authorization_digest"], t=t,
            )
            cx.execute("ROLLBACK")
            _refuse(reason_code, details)
        cx.execute("COMMIT")
    except PublicationError:
        raise
    except Exception:
        cx.execute("ROLLBACK")
        raise

    # Apply: git plumbing, outside any SQLite transaction (design section 9).
    tmp_dir = os.path.join(state_root, "tmp")
    os.makedirs(tmp_dir, mode=0o700, exist_ok=True)
    index_path = os.path.join(tmp_dir, f"index-{attempt_id}")

    def _fail(reason_code, details=None, terminal_state="failed"):
        cx.execute("BEGIN IMMEDIATE")
        cx.execute(
            "UPDATE publication_attempts SET state=?, reason_code=?, applied_at=? "
            "WHERE id=? AND applied_at IS NULL",
            (terminal_state, reason_code, t, attempt_id),
        )
        cx.execute("COMMIT")
        try:
            if os.path.exists(index_path):
                os.remove(index_path)
        except OSError:
            pass
        if terminal_state == "conflict":
            _refuse(reason_code, details)
        _operation_failed(reason_code, details)

    try:
        content_bytes = bytes(presented_stage["content"])
        blob_result = _git(target_git_dir, ["-c", "core.autocrlf=false", "hash-object", "-w", "--stdin"],
                            input_bytes=content_bytes)
        blob_oid = blob_result.stdout.strip().decode("ascii")

        readback = _git(target_git_dir, ["cat-file", "blob", blob_oid])
        if sha256_hex(readback.stdout) != presented_digest:
            _fail("written_blob_digest_mismatch", {"blob_oid": blob_oid})

        index_env = {"GIT_INDEX_FILE": index_path}
        _git(target_git_dir, ["read-tree", observed_base_sha], env_extra=index_env)
        _git(target_git_dir, ["update-index", "--add", "--cacheinfo",
                               f"100644,{blob_oid},{presented_path}"], env_extra=index_env)
        tree_oid = _git(target_git_dir, ["write-tree"], env_extra=index_env).stdout.strip().decode("ascii")

        author_env = {
            "GIT_AUTHOR_NAME": "Nightshift Publication Gateway",
            "GIT_AUTHOR_EMAIL": "publication-gateway@nightshift.invalid",
            "GIT_COMMITTER_NAME": "Nightshift Publication Gateway",
            "GIT_COMMITTER_EMAIL": "publication-gateway@nightshift.invalid",
            "GIT_AUTHOR_DATE": f"@{int(t)} +0000",
            "GIT_COMMITTER_DATE": f"@{int(t)} +0000",
        }
        message = commit_message or f"publication: stage {stage_id} -> {presented_path}"
        commit_oid = _git(
            target_git_dir, ["commit-tree", tree_oid, "-p", observed_base_sha, "-m", message],
            env_extra=author_env,
        ).stdout.strip().decode("ascii")

        # L3: git-level CAS. MUST carry the old-value argument -- omitting
        # it reopens the TOCTOU window between L1 and this write, which the
        # SQLite CAS structurally cannot close on its own (two Nightshift
        # processes with different state_roots pointed at one destination
        # share no SQLite lock).
        update_ref = _git(target_git_dir, ["update-ref", target_ref, commit_oid, observed_base_sha],
                           check=False)
        if update_ref.returncode != 0:
            _fail("base_moved_during_apply", {
                "stderr": update_ref.stderr.decode("utf-8", "replace")[:2000]}, terminal_state="conflict")
    except PublicationError:
        raise
    finally:
        try:
            if os.path.exists(index_path):
                os.remove(index_path)
        except OSError:
            pass

    # T2
    cx.execute("BEGIN IMMEDIATE")
    cx.execute(
        "UPDATE publication_attempts SET state='applied', commit_oid=?, tree_oid=?, blob_oid=?, "
        "git_object_format=?, applied_at=? WHERE id=? AND applied_at IS NULL",
        (commit_oid, tree_oid, blob_oid, "sha1", t, attempt_id),
    )
    # PG-7: published result derives from the reviewed patch (the approval's
    # review_id) -- recorded only once the commit is actually landed, in the
    # same transaction that marks the attempt applied.
    record_publication_lineage(
        cx, tenant_id=tenant_id, repository_id=repository_id,
        child_kind="attempt", child_id=attempt_id,
        parent_kind="review", parent_ref=str(auth["review_id"]), t=t,
    )
    cx.execute("COMMIT")
    return {"ok": True, "status": "applied", "attempt_id": attempt_id, "commit_oid": commit_oid,
            "tree_oid": tree_oid, "blob_oid": blob_oid, "observed_base_sha": observed_base_sha}


# ---------------------------------------------------------------------------
# Patch -- PG-5's output in no_generated_file mode; never writes; available
# in both modes.
# ---------------------------------------------------------------------------

def _latest_review(cx, stage_id):
    return cx.execute(
        "SELECT * FROM publication_review WHERE stage_id=? AND id="
        "(SELECT MAX(id) FROM publication_review WHERE stage_id=?)",
        (stage_id, stage_id)).fetchone()


def patch_stage(cx, *, stage_id, tenant_id, repository_id):
    _validate_tenant_id(tenant_id)
    _validate_repository_id(repository_id)
    stage_row = get_stage(cx, stage_id)
    if stage_row is None:
        _refuse("patch_requires_stage", {"stage_id": stage_id})
    if stage_row["tenant_id"] != tenant_id or stage_row["repository_id"] != repository_id:
        _refuse("tenant_mismatch" if stage_row["tenant_id"] != tenant_id else "repository_mismatch")

    policy = get_policy(cx)

    # ADR-0081: "No-generated-file mode wires only the reviewed-patch
    # adapter" -- AC 3 says this mode "returns REVIEWED patches for the
    # customer to apply", not the same never-reviewed preview `patch` is
    # elsewhere. Before this check, no_generated_file mode's patch_stage
    # required only a stage row and happily emitted an unreviewed diff,
    # contradicting both the AC and this module's own ADR. The gate is
    # scoped to no_generated_file ONLY: in zero_nightshift_file mode (the
    # default), `patch` remains the never-writes preview tool it always
    # was -- apply()'s own review/approve gate is what protects that mode's
    # real write path, and callers there have never been required to stage
    # a review before previewing a diff.
    if policy and policy["mode"] == "no_generated_file":
        review_row = _latest_review(cx, stage_id)
        if review_row is None or review_row["verdict"] != "approve":
            _refuse("patch_requires_review", {"stage_id": stage_id})
        if review_row["reviewed_digest"] != stage_row["content_digest"]:
            _operation_failed("reviewed_digest_mismatch", {"stage_id": stage_id})
        # Re-assert self-review at patch, same "checked at every gate"
        # design approve() already documents for itself.
        _assert_not_self_review(cx, stage_row, review_row["reviewer_identity"],
                                 review_row["reviewer_run_id"])

    new_text = bytes(stage_row["content"]).decode("utf-8", "replace")
    new_lines = new_text.splitlines(keepends=True)
    old_lines = []
    if policy and policy["target_git_dir"] and policy["target_ref"]:
        base_sha = _rev_parse_commit(policy["target_git_dir"], policy["target_ref"])
        if base_sha is None:
            _refuse("patch_base_unavailable", {"target_ref": policy["target_ref"]})
        entry = _ls_tree_entry(policy["target_git_dir"], base_sha, stage_row["destination_path"])
        if entry is not None:
            _mode, obj_type, oid = entry
            if obj_type == "blob":
                old_bytes = _git(policy["target_git_dir"], ["cat-file", "blob", oid]).stdout
                old_lines = old_bytes.decode("utf-8", "replace").splitlines(keepends=True)

    import difflib
    diff = "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{stage_row['destination_path']}", tofile=f"b/{stage_row['destination_path']}",
    ))
    return {"ok": True, "stage_id": stage_id, "destination_path": stage_row["destination_path"],
            "content_digest": stage_row["content_digest"], "patch": diff}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_publication_command(fn, args):
    """Mirrors bin/nightshift-ctl's `_run_memory_command` boundary. Never
    leaks a local path or a raw traceback through this security-sensitive
    surface."""
    try:
        result = fn(args)
    except PublicationError as exc:
        out(error_payload(exc), exc.exit_code)
    except Exception:
        out({"ok": False, "reason_code": "publication_operation_failed"}, 1)
    out(result)


def _effective_state_root(args):
    state_root = getattr(args, "state_root", None)
    if not state_root:
        _usage_error("missing_argument", {"argument": "state_root"})
    repository_id = getattr(args, "repository_id", None)
    if repository_id is None:
        return state_root
    return resolve_publication_root(state_root, getattr(args, "tenant_id", None), repository_id)


def _read_file_bytes(path):
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        _usage_error("content_not_readable", {"path": path, "error": str(exc)})


def cmd_policy_set(args):
    t = now()
    cx = connect(_effective_state_root(args))
    try:
        patterns = []
        for chunk in (args.path_allowlist or "").split(","):
            chunk = chunk.strip()
            if chunk:
                patterns.append(chunk)
        return policy_set(
            cx, tenant_id=args.tenant_id, repository_id=args.repository_id, mode=args.mode,
            path_allowlist=patterns, target_git_dir=args.target_git_dir, target_ref=args.target_ref,
            key_version=args.key_version, t=t,
        )
    finally:
        cx.close()


def cmd_policy_show(args):
    cx = connect(_effective_state_root(args), create=False)
    try:
        policy = get_policy(cx)
        if policy is None:
            _refuse("policy_not_initialised")
        return {"ok": True, "policy": policy}
    finally:
        cx.close()


def cmd_reviewer_add(args):
    t = now()
    token_bytes = _read_file_bytes(args.token_file)
    cx = connect(_effective_state_root(args))
    try:
        return register_reviewer(
            cx, tenant_id=args.tenant_id, reviewer_identity=args.reviewer_identity,
            token_bytes=token_bytes, t=t,
        )
    finally:
        cx.close()


def cmd_stage(args):
    t = now()
    content_bytes = _read_file_bytes(args.content_file)
    token_bytes = _read_file_bytes(args.producer_token_file)
    cx = connect(_effective_state_root(args))
    try:
        return stage(
            cx, tenant_id=args.tenant_id, repository_id=args.repository_id,
            content_bytes=content_bytes, destination_path=args.destination_path,
            classification=args.classification, producer_identity=args.producer_identity,
            producer_token_bytes=token_bytes,
            producer_run_id=args.producer_run_id, snapshot_id=args.snapshot_id,
            derived_from_stage_id=args.derived_from_stage_id,
            memory_artifact_id=args.memory_artifact_id, key_version=args.key_version, t=t,
        )
    finally:
        cx.close()


def cmd_review(args):
    t = now()
    token_bytes = _read_file_bytes(args.token_file)
    cx = connect(_effective_state_root(args))
    try:
        return review(
            cx, tenant_id=args.tenant_id, repository_id=args.repository_id,
            stage_id=args.stage_id, reviewer_identity=args.reviewer_identity,
            reviewer_run_id=args.run_id, token_bytes=token_bytes, verdict=args.verdict,
            notes=args.notes, t=t,
        )
    finally:
        cx.close()


def cmd_approve(args):
    t = now()
    token_bytes = _read_file_bytes(args.token_file)
    cx = connect(_effective_state_root(args))
    try:
        return approve(
            cx, tenant_id=args.tenant_id, repository_id=args.repository_id,
            stage_id=args.stage_id, review_id=args.review_id,
            reviewer_identity=args.reviewer_identity, reviewer_run_id=args.run_id,
            token_bytes=token_bytes, expected_base_sha=args.expected_base_sha,
            ttl=args.ttl, t=t,
        )
    finally:
        cx.close()


def cmd_apply(args):
    t = now()
    state_root = _effective_state_root(args)
    cx = connect(state_root)
    try:
        return apply_publication(
            cx, tenant_id=args.tenant_id, repository_id=args.repository_id,
            authorization_id=args.authorization_id, stage_id=args.stage_id,
            idempotency_key=args.idempotency_key, state_root=state_root, t=t,
        )
    finally:
        cx.close()


def cmd_patch(args):
    cx = connect(_effective_state_root(args), create=False)
    try:
        return patch_stage(cx, stage_id=args.stage_id, tenant_id=args.tenant_id,
                            repository_id=args.repository_id)
    finally:
        cx.close()


def cmd_inspect(args):
    cx = connect(_effective_state_root(args), create=False)
    try:
        if args.attempt_id is not None:
            row = cx.execute("SELECT * FROM publication_attempts WHERE id=?",
                              (args.attempt_id,)).fetchone()
            return {"ok": row is not None, "attempt": dict(row) if row else None}
        if args.stage_id is not None:
            row = get_stage(cx, args.stage_id)
            if row is not None:
                row = {k: v for k, v in row.items() if k != "content"}
            return {"ok": row is not None, "stage": row}
        if args.authorization_id is not None:
            row = cx.execute("SELECT * FROM publication_authorizations WHERE id=?",
                              (args.authorization_id,)).fetchone()
            return {"ok": row is not None, "authorization": dict(row) if row else None}
        _usage_error("missing_argument", {"argument": "attempt_id|stage_id|authorization_id"})
    finally:
        cx.close()


def build_parser():
    p = argparse.ArgumentParser(prog="nightshift_publication",
                                 description="Publication Gateway -- issue #400, ADR-0006")
    p.add_argument("--now", type=float, default=None,
                   help="override the clock, epoch seconds (test seam, argv only)")
    sub = p.add_subparsers(dest="group", required=True)

    def common(sp, need_repo=True):
        sp.add_argument("--state-root", required=True)
        # #694: INTEGER, matching --repository-id's existing type=int on the
        # next line and prov.validate_tenant_id's contract. argparse rejects
        # a non-integer before any code in this module runs; the Python
        # validator then rejects 0, negatives and anything past MAX_ID.
        sp.add_argument("--tenant-id", required=True, type=int)
        if need_repo:
            sp.add_argument("--repository-id", required=True, type=int)

    ps = sub.add_parser("policy-set")
    common(ps)
    ps.add_argument("--mode", required=True, choices=("zero_nightshift_file", "no_generated_file"))
    ps.add_argument("--path-allowlist", default="")
    ps.add_argument("--target-git-dir", default=None)
    ps.add_argument("--target-ref", default=None)
    ps.add_argument("--key-version", type=int, default=None)
    ps.set_defaults(func=cmd_policy_set)

    pshow = sub.add_parser("policy-show")
    common(pshow)
    pshow.set_defaults(func=cmd_policy_show)

    ra = sub.add_parser("reviewer-add")
    common(ra)
    ra.add_argument("--reviewer-identity", required=True)
    ra.add_argument("--token-file", required=True)
    ra.set_defaults(func=cmd_reviewer_add)

    st = sub.add_parser("stage")
    common(st)
    st.add_argument("--content-file", required=True)
    st.add_argument("--destination-path", required=True)
    st.add_argument("--classification", required=True)
    st.add_argument("--producer-identity", required=True)
    st.add_argument("--producer-token-file", required=True,
                     help="token registered for --producer-identity (reviewer-add)")
    st.add_argument("--producer-run-id", required=True)
    st.add_argument("--snapshot-id", required=True,
                     help="snapshot_id of a REAL source_snapshot row in the sibling "
                          "provenance.db (nightshift_provenance.record_source_snapshot)")
    st.add_argument("--derived-from-stage-id", type=int, default=None)
    st.add_argument("--memory-artifact-id", type=int, default=None)
    st.add_argument("--key-version", type=int, default=None)
    st.set_defaults(func=cmd_stage)

    rv = sub.add_parser("review")
    common(rv)
    rv.add_argument("--stage-id", required=True, type=int)
    rv.add_argument("--reviewer-identity", required=True)
    rv.add_argument("--token-file", required=True)
    rv.add_argument("--run-id", required=True)
    rv.add_argument("--verdict", required=True, choices=("approve", "reject"))
    rv.add_argument("--notes", default=None)
    rv.set_defaults(func=cmd_review)

    ap = sub.add_parser("approve")
    common(ap)
    ap.add_argument("--stage-id", required=True, type=int)
    ap.add_argument("--review-id", required=True, type=int)
    ap.add_argument("--reviewer-identity", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--expected-base-sha", required=True)
    ap.add_argument("--ttl", required=True, type=float)
    ap.set_defaults(func=cmd_approve)

    apl = sub.add_parser("apply")
    common(apl)
    apl.add_argument("--authorization-id", required=True, type=int)
    apl.add_argument("--stage-id", required=True, type=int)
    apl.add_argument("--idempotency-key", required=True)
    apl.set_defaults(func=cmd_apply)

    pt = sub.add_parser("patch")
    common(pt)
    pt.add_argument("--stage-id", required=True, type=int)
    pt.set_defaults(func=cmd_patch)

    ins = sub.add_parser("inspect")
    common(ins)
    ins.add_argument("--attempt-id", type=int, default=None)
    ins.add_argument("--stage-id", type=int, default=None)
    ins.add_argument("--authorization-id", type=int, default=None)
    ins.set_defaults(func=cmd_inspect)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.now is not None:
        set_clock_override(args.now)
    _run_publication_command(args.func, args)


if __name__ == "__main__":
    main()
