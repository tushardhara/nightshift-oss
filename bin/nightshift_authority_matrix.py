#!/usr/bin/env python3
"""nightshift_authority_matrix.py -- #400 acceptance condition 4, verbatim:

    "Every data class names exactly one durable authority. Derived files
    and views are visibly projections and cannot override it."

WHAT THIS IS
============
A single Python dict, `DATA_CLASSES`, naming the durable authority (or the
projection, and what it projects) for every table this repository's stores
declare, plus the handful of file-based data classes CONTROL-PLANE.md
already recognises (`.omni/capacity.json`, the CAS object tree, migration
snapshot copies). It is the machine-checkable form of the table
`docs/CONTROL-PLANE.md` carries in prose.

WHY DERIVATION, NOT A HARDCODED COUNT
======================================
An earlier pass at this same epic hardcoded "24 inline CREATE TABLE
statements in bin/nightshift-ctl" as a fact about the repository. It was
already stale by the time three sibling streams landed their own tables.
This module never states a count. `discover_tables()` regex-extracts real
table names straight out of the live schema constants
(`nightshift_publication.PUBLICATION_SCHEMA`, `..._provenance.PROVENANCE_
SCHEMA`, `..._tenant.TENANT_SCHEMA`, `..._tenant_keys.KEY_SCHEMA`,
`..._authority.AUTHORITY_SCHEMA`, `..._memory.MEMORY_SCHEMA`) and out of
`bin/nightshift-ctl`'s own source text, and `verify_matrix()` diffs that
discovered set against `DATA_CLASSES` in BOTH directions:

  * a table that exists but has no entry here is `undeclared` -- someone
    added a table and forgot the matrix;
  * an entry here naming a table that no longer exists is `orphaned` --
    someone removed a table and left a stale claim behind.

Either non-empty means `verify_matrix(...).ok` is False. A future stream
that adds a table without touching this file gets a red CI run that names
the exact table and points here -- that is the intended failure mode, not
a bug in whoever added the table.

THE PROJECTION HALF -- WHY IT NEEDS BEHAVIOR, NOT JUST A LABEL
================================================================
Declaring `.omni/control-state.json` a projection of `worker_slots` proves
nothing by itself; a label is not a guarantee. The guarantee AC 4 actually
asks for -- "cannot override it" -- is a property of the CODE PATH that
reads a decision, not of this file. This module gives that code path
something to be checked against (`is_projection()`, and the DATA_CLASSES
entries below naming exactly what each projection projects onto); the
proof that `nightshift-ctl slot acquire` actually ignores a poisoned
`control-state.json` is a behavioral test, deliberately left to the
conformance suite that exercises the real binary -- see AC4-PROJECTION in
this stream's acceptance criteria.

CROSS-STREAM STATE AT THE TIME THIS WAS WRITTEN
=================================================
This module was built landing INTO `main` at a commit where three sibling
streams named in #400's coordination notes -- extrepo (ADR-0085),
165/repository-scope (ADR-0084), and 442/conformance-gate (ADR-0086) --
had NOT yet merged: `bin/nightshift_conformance.py` does not exist,
`nightshift_authority.classify_repository` does not exist, no
`repository_ownership` table exists anywhere in AUTHORITY_SCHEMA, and
control.db carries no `repository_id` column. Declaring matrix entries for
tables that are not yet real would be exactly the kind of unearned
"proven" this stream exists to refuse (see `docs/decisions/ADR-0087...md`
for the full account). See `_PENDING_CROSS_STREAM_DECLARATIONS` below for
the exact entries the next editor must add, by name, the moment those
streams' tables land -- `verify_matrix()` will refuse to stay green until
they do.
"""

from __future__ import annotations

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Table discovery
# ---------------------------------------------------------------------------

_TABLE_RE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

# A real SQL identifier never contains '.', '/', '*' or whitespace. Every
# file-based projection's `table` field below is deliberately spelled with
# one of those (".omni/capacity.json", ".omni/leases/*", ...) so this
# regex is what tells `verify_matrix()` "this declared entry is narrative,
# not a claim about a discoverable SQL table" -- it is never checked for
# orphaning.
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def discover_tables(sql_text):
    """Every table name (real or virtual) CREATEd in a chunk of SQL or a
    whole source file's text. The one parser both `verify_matrix()` call
    sites (a schema string constant, and bin/nightshift-ctl's inline
    SCHEMA) trust, so there is exactly one regex to keep honest."""
    return set(_TABLE_RE.findall(sql_text or ""))


def discover_ctl_tables(ctl_source_path):
    with open(ctl_source_path, "r", encoding="utf-8") as fh:
        return discover_tables(fh.read())


def _looks_like_sql_table(table_name):
    return bool(_SQL_IDENTIFIER_RE.match(table_name))


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------

DURABLE = "durable"
PROJECTION = "projection"


class DataClass:
    """One row of the matrix. `key` is a human-chosen label; `(store,
    table)` is the ONLY pair `verify_matrix()` matches against a
    discovered table name. Everything else is narrative for a human
    reader."""

    __slots__ = ("key", "store", "table", "kind", "projects_onto", "note")

    def __init__(self, key, *, store, table, kind, projects_onto=None, note=""):
        if kind not in (DURABLE, PROJECTION):
            raise ValueError(f"{key}: kind must be DURABLE or PROJECTION")
        if kind == PROJECTION and not projects_onto:
            raise ValueError(f"{key}: a projection must name what it projects onto")
        if kind == DURABLE and projects_onto:
            raise ValueError(f"{key}: a durable authority cannot itself project onto anything")
        self.key = key
        self.store = store
        self.table = table
        self.kind = kind
        self.projects_onto = projects_onto
        self.note = note

    def __repr__(self):
        return f"DataClass({self.key!r}, {self.store}.{self.table}, {self.kind})"


DATA_CLASSES = {}


def _register(entry):
    if entry.key in DATA_CLASSES:
        # Not merely unlikely -- AC 4 requires it be UNREPRESENTABLE for a
        # data class to name two authorities. This is the enforcement
        # point: two _register() calls for the same key fail at import
        # time, before any test even runs.
        raise ValueError(
            f"duplicate data class key {entry.key!r} -- a data class with "
            "two authority entries is exactly the shape AC 4 forbids")
    DATA_CLASSES[entry.key] = entry
    return entry


def _d(key, store, table, note):
    return _register(DataClass(key, store=store, table=table, kind=DURABLE, note=note))


def _p(key, store, table, projects_onto, note):
    return _register(DataClass(key, store=store, table=table, kind=PROJECTION,
                                projects_onto=projects_onto, note=note))


# ---- control.db (bin/nightshift-ctl) --------------------------------------
# One-line summaries drawn from the schema's own inline comments and from
# docs/CONTROL-PLANE.md's "What lives where" table -- not guessed.
for _key, _table, _note in (
    ("control.meta", "meta", "control.db's own schema/bookkeeping key-value store."),
    ("control.tasks", "tasks",
     "Task lifecycle, size and prerequisites (id, state, size, base_sha, worktree, branch, pr_url)."),
    ("control.task_dependencies", "task_dependencies",
     "Prerequisite edges between tasks; unique, non-self-referencing."),
    ("control.task_claims", "task_claims",
     "At most one active recovery claim per task (partial unique index one_active_claim)."),
    ("control.worker_slots", "worker_slots",
     "The global worker semaphore, one-dispatch binding (dispatched_at/bound_agent/bound_title), "
     "and ADR-0002 fencing (generation/run_id)."),
    ("control.task_admissions", "task_admissions",
     "Immutable proof that first implementation was admitted for a task."),
    ("control.leases", "leases",
     "Shared-path locks; at most one active lease per resource (partial unique index one_active_lease)."),
    ("control.sequences", "sequences", "Atomically allocated counters (e.g. migration sequence numbers)."),
    ("control.checkpoints", "checkpoints", "Wave checkpoints: a recorded, ref-reachable base SHA."),
    ("control.agent_runs", "agent_runs",
     "Which session held which slot for which task; TTL-expiry marks a run dead."),
    ("control.vendors", "vendors",
     "Vendor rate-limit cooldown state (base 30 min, doubling per consecutive limit, 4 h cap)."),
    ("control.scheduled_run_detector_state", "scheduled_run_detector_state",
     "ADR-0017 detector checkpoint per task: last/next expected run, consecutive-failure count."),
    ("control.scheduled_run_observations", "scheduled_run_observations",
     "ADR-0017 per-run observation: status, failure class, delivery acknowledgement."),
    ("control.review_receipts", "review_receipts",
     "One row per (task, head_sha, domain) review verdict. `source=attested` is "
     "the exact-SHA review proof (ADR-0039); `source=local`/`reconstructed` is a "
     "recorded review with no comment attestation behind it (ADR-0090)."),
    ("control.review_findings", "review_findings",
     "Findings recorded against a task/head/domain review (ADR-0039)."),
    ("control.review_dispatch_markers", "review_dispatch_markers",
     "Which review marker was minted for a task/head/domain, so a re-dispatch is detectable (ADR-0039)."),
    ("control.program_authority", "program_authority",
     "At most one active Program-role session (ADR-0044); generation is never reused (no ABA)."),
    ("control.program_action_permits", "program_action_permits",
     "One-use, short-TTL permit for one exact call, consumed by a compare-and-set UPDATE (ADR-0044)."),
    ("control.bookkeeping_receipts", "bookkeeping_receipts",
     "Append-only exact-SHA proof of a classify-then-merge bookkeeping-only PR (issue #286, ADR-0052)."),
    ("control.heartbeat_persist_decisions", "heartbeat_persist_decisions",
     "Append-only should_persist decision per heartbeat outcome, consumed once by pr-create (issue #390, ADR-0053)."),
    ("control.issue_close_dispositions", "issue_close_dispositions",
     "Append-only disposition per issue-close attempt: delivered/superseded/deferred (issue #424, ADR-0055)."),
    ("control.dod_conditions", "dod_conditions",
     "Last-ingested per-condition DoD verdict snapshot (D01..D24), UPSERTed by condition_id (issue #474, ADR-0063)."),
    ("control.task_dod_links", "task_dod_links",
     "Many-to-many task-to-DoD-condition linkage (issue #474, ADR-0063)."),
    ("control.dod_heartbeat_observations", "dod_heartbeat_observations",
     "Append-only per-heartbeat DoD pass-count delta observation (issue #474, ADR-0063)."),
    ("control.review_gate_degradations", "review_gate_degradations",
     "Append-only record of a dispatch to a review seat that proceeded WITHOUT a "
     "bound review-plan verdict -- degraded planner, foreign repository, or a "
     "declared non-review. The count is the measure of how often the gate was "
     "deciding anything at all (issue #720, ADR-0091)."),
):
    _d(_key, "control.db", _table, _note)

_d("control.capacity_json", "control.db", ".omni/capacity.json",
   "Configured limits (max workers, reserve, TTLs). CONTROL-PLANE.md's own "
   "'What lives where' table names this human-editable FILE itself as the "
   "authority -- not a database row -- and this matrix mirrors that "
   "exactly rather than reinterpreting it.")

for _key, _table, _onto, _note in (
    ("control.control_state_json", ".omni/control-state.json", "control.worker_slots",
     "`nightshift-ctl dump --write` snapshot of worker_slots/task_admissions/task_claims. "
     "AC4-PROJECTION: poisoning this file must not change a real slot verdict."),
    ("control.leases_dir", ".omni/leases/*", "control.leases", "One-line per-lease marker files."),
    ("control.migrations_dirnames", "db/migrations/*", "control.sequences",
     "Migration filenames encode an allocated sequence number."),
    ("control.registry_base_sha", ".omni/registry-*.json (base_sha fields)", "control.checkpoints",
     "Checkpoint SHAs echoed into registry narrative."),
    ("control.registry_narrative", ".omni/registry-*.json (criteria/verdicts/links)", "control.tasks",
     "Narrative around task rows; state/size/prerequisites are never authoritative here."),
    ("control.dump_scheduled_run_detector", "dump.scheduled_run_detector output",
     "control.scheduled_run_detector_state", "Read projection of the detector tables."),
):
    _p(_key, "control.db", _table, _onto, _note)

# ---- memory.db (bin/nightshift_memory.py) ---------------------------------
_d("memory.artifact", "memory.db", "artifact",
   "Narrative memory bodies and their classification/retention/redaction metadata. "
   "Advisory by ADR-0077 -- mechanically unable to grant a control decision (AC 6).")
_d("memory.ref", "memory.db", "ref",
   "Issue/PR/ADR/commit/crew/path references for an artifact -- a real, separately "
   "authored row set, not a view over artifact.")
_p("memory.artifact_fts", "memory.db", "artifact_fts", "memory.artifact",
   "FTS5 index over artifact.body, content='' (contentless): the module's own schema "
   "comment states artifact.body is the one authoritative copy.")
_p("memory.artifact_fts_vocab", "memory.db", "artifact_fts_vocab", "memory.artifact",
   "FTS5 vocabulary shadow table for artifact_fts; same projection relationship.")

# ---- authority.db (bin/nightshift_authority.py, ADR-0082) -----------------
_d("authority.authority_binding", "authority.db", "authority_binding",
   "The ONE per-repository answer to 'which store is authoritative right now' "
   "(active_backend/authority_epoch/phase). CHECK(id=1): a second row is not "
   "unusual, it is a second authority, and ADR-0082 forbids that outright.")
_d("authority.authority_epoch_log", "authority.db", "authority_epoch_log",
   "Append-only, immutable record of every authority-epoch flip.")
_d("authority.migration_compare", "authority.db", "migration_compare",
   "Phase-4 compare verdicts, kept beside the binding so switch_authority can "
   "read them in the same transaction as the flip.")
_d("authority.repository_ownership", "authority.db", "repository_ownership",
   "Single-row, write-once per-repository OWNERSHIP axis ('self'/'external'), "
   "independent of authority_binding.active_backend -- see ADR-0087. Declared "
   "once #699 (ADR-0085) made the table real; before that a declaration would "
   "have been evidence for something that did not exist.")
_p("control.meta_repository_id", "control.db", "meta.repository_id",
   "authority.authority_binding",
   "meta.repository_id is a PROJECTION of the authority.db repository binding -- "
   "`repo bind` refuses an id the authority registry has not attested. Declared "
   "once #700 (ADR-0084) made the binding real. The five control.db tables 165 "
   "also scoped need no new entry: a column is not a new data class.")

# ---- tenants/registry.db (bin/nightshift_tenant.py) -----------------------
_d("tenant.tenant", "tenants/registry.db", "tenant",
   "The tenant registry: which tenant_uid maps to which tenant_id, and the "
   "one-way active->deleting->deleted status ratchet.")

# ---- tenant key custody (bin/nightshift_tenant_keys.py; ADR-0080) ---------
# `connect(path)` takes a CALLER-CHOSEN file path (ADR-0080 explicitly allows
# custody in an OS keystore or a customer-controlled KMS), so there is no
# fixed location to name here the way every other store has one -- the store
# label below is the module's own name, not a path.
_d("tenant_keys.tenant_key", "tenant_keys.db", "tenant_key",
   "Wrapped per-tenant KEKs and their retire/destroy state. destroy is one-way "
   "and empties wrapped_kek in the SAME update that sets destroyed_at (ADR-0080).")
_d("tenant_keys.object_key", "tenant_keys.db", "object_key",
   "Wrapped per-object DEKs, keyed by (tenant_id, repository_id, object_digest, key_version).")
_d("tenant_keys.rewrap_watermark", "tenant_keys.db", "rewrap_watermark",
   "Per-(tenant, target key_version) rotation resume point -- the only record of "
   "how far a rotation got, so a killed rotation resumes instead of restarting.")

# ---- provenance.db (bin/nightshift_provenance.py; ADR-0079) ---------------
_d("provenance.source_snapshot", "provenance.db", "source_snapshot",
   "Canonical repository identity, exact commit/tree OIDs, object format, retention "
   "state, and the single legal source_not_retained transition (#400 items 10, 16).")
_d("provenance.closure_entry", "provenance.db", "closure_entry",
   "Submodule/LFS closure entries pinned to a snapshot (#400 item 11).")
_d("provenance.lineage_node", "provenance.db", "lineage_node",
   "Nodes in the provenance half of the lineage graph (source_snapshot/worker_output/"
   "reviewed_patch/published_result).")
_d("provenance.derived_from", "provenance.db", "derived_from",
   "THE lineage authority for content-carrying and non-content-carrying edges "
   "(#400 items 10, 11, 15). A published_result's two real parents live here, "
   "never inferred from either side alone.")
_d("provenance.purge_receipt", "provenance.db", "purge_receipt",
   "#400 condition 15: one immutable, durable row per (tenant, repository, "
   "root node) a lineage purge ever ran against -- named nodes/digests, CAS "
   "scope, and which of the six AC15 classes the purge did and did not "
   "cover. The audit record of a deletion, not itself deletable (see the "
   "table's own BEFORE UPDATE and BEFORE DELETE triggers).")

_d("provenance.objects_cas", "provenance.db (filesystem)", "tenants/<tid>/repos/<rid>/objects/<dd>/<digest>",
   "Content-addressed staging for large bodies (stage_object/stage_object_path). "
   "Path is content-addressed on the PLAINTEXT digest even when the stored bytes "
   "are an envelope (ADR-0080) -- content addressing must never move to ciphertext.")

# ---- publication.db (bin/nightshift_publication.py; ADR-0081) -------------
for _key, _table, _note in (
    ("publication.publication_policy", "publication_policy",
     "The one policy row per (tenant, repository): mode, path allowlist, egress target. "
     "Strict mode structurally cannot even name an egress target (schema CHECK)."),
    ("publication.publication_reviewer", "publication_reviewer",
     "Registered reviewer identities and their token hashes; revocable, never deleted."),
    ("publication.publication_stage", "publication_stage",
     "Staged content, immutable by trigger, keyed by (tenant, repository, content_digest, "
     "destination_path). Carries the eight AC-8 manifest fields directly on the row: "
     "tenant_id, classification, content_digest, content_size, source_snapshot identity "
     "(provenance), retention (via the snapshot it names), key_version, and cipher_id."),
    ("publication.publication_review", "publication_review",
     "Review verdicts against a stage, including the self-review defence's inputs."),
    ("publication.publication_authorizations", "publication_authorizations",
     "THE 7-tuple one-use authorization (tenant, repository, content_digest, "
     "destination_path, expected_base_sha, reviewer_identity, expiry), consumed by a "
     "rowcount compare-and-set (#400 item 13)."),
    ("publication.publication_attempts", "publication_attempts",
     "One row per apply attempt, idempotency-keyed, intent->terminal exactly once."),
    ("publication.publication_lineage", "publication_lineage",
     "Publication's half of the lineage graph -- stage/review/patch/attempt nodes and "
     "their parent edges, joined to provenance.derived_from by the shared lineage_edge view."),
):
    _d(_key, "publication.db", _table, _note)

# ---- migration rehearsal (bin/nightshift_migrate.py; ADR-0082) ------------
_d("migrate.snapshot_files", "nightshift_migrate (filesystem)",
   "<state_root>/migration/<migration_id>/{inventory,snapshot,shadow}.json + copied trees",
   "The actual copied bytes and manifests a migration rehearsal produces. Distinct from "
   "authority.migration_compare, which records only the VERDICT about them, not the bytes.")

# ---------------------------------------------------------------------------
# PENDING CROSS-STREAM DECLARATIONS -- not yet real, therefore not declared
# ---------------------------------------------------------------------------
#
# #400's coordination notes require this matrix to name, BY NAME, tables and
# columns three sibling streams add. As of the commit this module was built
# against, none of them exist yet in `main` (confirmed by grep -- see the
# module docstring). Declaring a DataClass for a table that is not real would
# make `verify_matrix()`'s own `orphaned` check fail on this file the moment
# it is imported, which is a worse outcome than an honest gap: it would be
# exactly the "evidence for something that did not happen" shape #400 exists
# to refuse. Instead, the exact entries required are written out here so the
# next edit is a copy/paste, not a redesign:
#
#   extrepo (ADR-0085) adds `repository_ownership` to AUTHORITY_SCHEMA --
#     _d("authority.repository_ownership", "authority.db", "repository_ownership",
#        "Single-row, write-once per-repository OWNERSHIP axis ('self'/'external'), "
#        "independent of authority_binding.active_backend -- see ADR-0087.")
#
#   165/repository-scope (ADR-0084) adds `repository_id` to control.db's tasks,
#   task_dependencies, task_claims, worker_slots and leases, plus meta.repository_id --
#     for each of those five tables, the SAME control.db authority entry above
#     already covers the new column (a column is not a new data class); add ONE
#     new entry for the projection:
#     _p("control.meta_repository_id", "control.db", "meta.repository_id",
#        "authority.authority_binding",
#        "meta.repository_id is a PROJECTION of the authority.db repository "
#        "binding -- `repo bind` refuses an id the authority registry has not "
#        "attested.")
#
#   442/conformance-gate (ADR-0086) adds bin/nightshift_conformance.py. It owns
#   no new table (evaluate/permitted_*/attach_advisory are pure functions over
#   (base_contract, rules)), so no new DATA_CLASSES entry is needed for it --
#   only the AC-6 import-graph check (owned by the test suite, not this module)
#   needs to add it to the set of modules asserted to import no memory module.
#
# Whoever lands the merge that brings these in updates this block in the SAME
# PR that adds the table -- not as a follow-up.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class MatrixReport:
    """`undeclared`: real tables with no DATA_CLASSES entry.
    `orphaned`: DATA_CLASSES SQL-table entries naming a table that is not
    real (stale declaration -- someone dropped the table).
    `duplicated`: kept for report-shape honesty; `_register()` already
    makes this unreachable through this module's own API (see `_register`),
    but a report that could never be non-empty here would be a hidden
    assumption rather than a documented one.
    """

    def __init__(self, undeclared, orphaned, duplicated,
                 dangling_authority=(), unbacked_projection=()):
        self.undeclared = sorted(undeclared)
        self.orphaned = sorted(orphaned)
        self.duplicated = sorted(duplicated)
        # A projection whose `projects_onto` does not resolve to a registered
        # DURABLE data class. Until this existed, `_p()` carried no guarantee
        # at all: a projection could name an authority that was never declared
        # and the suite stayed green, which is the "evidence for something that
        # did not happen" shape this module exists to refuse.
        self.dangling_authority = sorted(dangling_authority)
        # A key-value-form projection target (`<table>.<key>`) whose table is
        # not real, or whose key appears in no source text.
        self.unbacked_projection = sorted(unbacked_projection)

    @property
    def ok(self):
        return not (self.undeclared or self.orphaned or self.duplicated
                    or self.dangling_authority or self.unbacked_projection)

    def as_dict(self):
        return {
            "ok": self.ok,
            "undeclared": [f"{s}.{t}" for s, t in self.undeclared],
            "orphaned": [f"{s}.{t}" for s, t in self.orphaned],
            "duplicated": [f"{s}.{t}" for s, t in self.duplicated],
            "dangling_authority": list(self.dangling_authority),
            "unbacked_projection": list(self.unbacked_projection),
        }


def verify_matrix(schema_sources, ctl_source_text=None):
    """`schema_sources`: mapping of store-label -> schema SQL text, e.g.
    `{"publication.db": nightshift_publication.PUBLICATION_SCHEMA, ...}`.
    `ctl_source_text`: bin/nightshift-ctl's own source text (its schema is
    inline in the file, not an importable constant, so the caller reads the
    file and passes the text here rather than this module reaching for a
    path it was told not to hardcode).

    Returns a `MatrixReport`. This is the entire AC-4 bijection check: every
    real table declared exactly once, nothing declared that is not real.
    """
    discovered = set()
    for store, sql in (schema_sources or {}).items():
        for table in discover_tables(sql):
            discovered.add((store, table))
    if ctl_source_text:
        for table in discover_tables(ctl_source_text):
            discovered.add(("control.db", table))

    declared = {}
    for dc in DATA_CLASSES.values():
        declared.setdefault((dc.store, dc.table), []).append(dc.key)

    undeclared = [k for k in discovered if k not in declared]
    orphaned = [
        k for k, keys in declared.items()
        if k not in discovered and _looks_like_sql_table(k[1])
    ]
    duplicated = [k for k, keys in declared.items() if len(keys) > 1]

    # --- the projection half -------------------------------------------
    # `_d()` entries were always checked against the real schema; `_p()`
    # entries were not checked at all. Both halves of this module's
    # vocabulary must carry a guarantee or the matrix is only half a proof.
    sources = list((schema_sources or {}).values())
    if ctl_source_text:
        sources.append(ctl_source_text)

    dangling_authority = []
    unbacked_projection = []
    for dc in DATA_CLASSES.values():
        if dc.kind != PROJECTION:
            continue
        # (a) the authority it claims to project onto must be a registered
        #     DURABLE class. Catches a projection pointing at a name nobody
        #     ever declared.
        target = DATA_CLASSES.get(dc.projects_onto)
        if target is None or target.kind != DURABLE:
            dangling_authority.append(
                f"{dc.key} -> {dc.projects_onto!r} "
                f"({'unknown' if target is None else 'not durable'})")
        # (b) key-value-form targets (`<table>.<key>`, no path separator)
        #     must name a real table AND a key that appears in some source.
        #     Deliberately narrow: glob/path/prose targets such as
        #     `.omni/leases/*` are NOT verified here and are listed in the
        #     module docstring as remaining unchecked. Claiming to verify
        #     them with a fuzzy match would be the same overstatement this
        #     check exists to prevent.
        name = dc.table
        if "/" in name or "*" in name or " " in name or "." not in name:
            continue
        base, _, key = name.rpartition(".")
        if (dc.store, base) not in discovered:
            unbacked_projection.append(f"{dc.key}: no such table {dc.store}.{base}")
        elif not any(key in src for src in sources):
            unbacked_projection.append(
                f"{dc.key}: key {key!r} appears in no source text")

    return MatrixReport(undeclared, orphaned, duplicated,
                        dangling_authority, unbacked_projection)


def is_projection(key):
    """True if the named data class is a projection (never authoritative).
    Raises KeyError for an unknown key -- silently returning False for a
    typo'd key would let a caller mistake 'I don't recognise this' for
    'this is durable'."""
    return DATA_CLASSES[key].kind == PROJECTION


def authority_of(key):
    """The data class a projection projects onto, or the class itself if it
    IS the durable authority. Follows at most one hop -- this matrix never
    declares a projection of a projection; every `projects_onto` above
    names a DURABLE entry, which `_register`/DataClass.__init__ does not
    enforce structurally but every entry above satisfies by construction."""
    dc = DATA_CLASSES[key]
    return dc if dc.kind == DURABLE else DATA_CLASSES[dc.projects_onto]


# ---------------------------------------------------------------------------
# AC 23 -- the cleanup gate
# ---------------------------------------------------------------------------

def cleanup_gate(*, new_authority_bound, export_verified, rollback_rehearsed, footprint_clean):
    """#400 acceptance condition 23, verbatim: "Cleanup of legacy `.omni`,
    refs, clones, or worktrees occurs only after the new authority, export,
    rollback, and exact-footprint proof succeed."

    Returns True only when EVERY one of the four named proofs is True.
    There is no default that lets a caller satisfy this by omission --
    calling with zero arguments is a TypeError, not a silent False, so a
    future cleanup path cannot forget to name all four."""
    return bool(new_authority_bound and export_verified and rollback_rehearsed and footprint_clean)


# ---------------------------------------------------------------------------
# Smoke entry point -- manual inspection only. No CLI verb is needed for
# this module (nothing else calls it as a subprocess); this is just enough
# to run `python3 bin/nightshift_authority_matrix.py` by hand and see the
# real report against this checkout's actual schemas.
# ---------------------------------------------------------------------------

def full_report():
    import nightshift_publication as _pub
    import nightshift_provenance as _prov
    import nightshift_tenant as _tenant
    import nightshift_tenant_keys as _keys
    import nightshift_authority as _auth
    import nightshift_memory as _mem

    schema_sources = {
        "publication.db": _pub.PUBLICATION_SCHEMA,
        "provenance.db": _prov.PROVENANCE_SCHEMA,
        "tenants/registry.db": _tenant.TENANT_SCHEMA,
        "tenant_keys.db": _keys.KEY_SCHEMA,
        "authority.db": _auth.AUTHORITY_SCHEMA,
        "memory.db": _mem.MEMORY_SCHEMA,
    }
    ctl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nightshift-ctl")
    ctl_source = None
    if os.path.isfile(ctl_path):
        with open(ctl_path, "r", encoding="utf-8") as fh:
            ctl_source = fh.read()
    return verify_matrix(schema_sources, ctl_source_text=ctl_source)


def main(argv=None):
    report = full_report()
    print(json.dumps(report.as_dict(), sort_keys=True))
    sys.exit(0 if report.ok else 3)


if __name__ == "__main__":
    main()
