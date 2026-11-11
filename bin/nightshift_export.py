#!/usr/bin/env python3
"""nightshift_export.py -- #400 acceptance condition 17, verbatim:

    "Tenant export is portable and versioned, includes checksums and
    provenance, and can be verified without trusting the producing
    Nightshift instance."

WHAT THIS IS
============
`export_tenant()` writes a self-contained "nse-v1" bundle directory:

    <out_dir>/MANIFEST.json           format_version, tenant_id, repository_id
                                       (both integers -- see IDENTITY below),
                                       generated_at, and one {path, sha256,
                                       size} entry per OTHER file in the
                                       bundle, plus an `entries_digest` that
                                       is a hash of that entry list itself.
    <out_dir>/rows/source_snapshot.json   provenance rows, tenant+repository scoped
    <out_dir>/rows/closure_entry.json
    <out_dir>/rows/lineage_node.json
    <out_dir>/rows/derived_from.json
    <out_dir>/objects/<dd>/<digest>       verbatim copies of whatever this
                                           tenant/repository has staged in
                                           its content-addressed object tree
                                           (nightshift_provenance.stage_object)

`verify_export(out_dir)` checks the bundle. It is a PURE FUNCTION OF THE
BUNDLE: it opens no database, imports no sibling module's `connect()`, and
takes no `state_root` -- its only input is the directory. That is what
makes "verified without trusting the producing Nightshift instance" a
checkable claim rather than a slogan: the verifier cannot consult the
producer even if it wanted to, because nothing in this function's call
graph knows how.

IDENTITY -- repository_id, NOT a free-text slug
================================================
#400's cross-stream ruling on repository identity is binding here: the
manifest's `repository_id` field is the same positive Python int every
other #400 store uses (validated the same way, via
`nightshift_authority.validate_repository_id` when importable, falling
back to `nightshift_provenance.validate_repository_id` otherwise), never a
free-text "repository" name or slug. `repository_fingerprint`,
`commit_oid`/`tree_oid`, and `tenant_id` are separate content-identity axes
carried verbatim inside the exported `source_snapshot` rows -- this module
does not fold them into, or substitute them for, `repository_id`.

THE HONEST LIMIT, STATED HERE AND IN ADR-0087
================================================
Digest closure detects corruption and truncation of the bundle: a flipped
byte, a deleted row file, a manifest edited after the fact, or a
`derived_from` edge whose endpoint node was quietly removed. It does NOT
detect a producer that lies consistently -- a Nightshift instance that
fabricates a self-consistent set of rows and hashes its own fabrication
produces a bundle that verifies clean. There is no signing key
infrastructure a customer can independently root in this stdlib-only
system, so this is the honest T0 answer to the AC, not the strongest
possible reading of its words. See ADR-0087.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import nightshift_provenance as prov

try:
    import nightshift_authority as auth
except ImportError:                                        # pragma: no cover
    auth = None


EXPORT_FORMAT_VERSION = "nse-v1"
MANIFEST_FILENAME = "MANIFEST.json"
ROWS_DIRNAME = "rows"
OBJECTS_DIRNAME = "objects"
RECEIPTS_DIRNAME = "export_receipts"

_ROW_TABLES = ("source_snapshot", "closure_entry", "lineage_node", "derived_from")


class ExportError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(exc):
    payload = {"ok": False, "reason_code": exc.reason_code}
    if exc.details:
        payload["details"] = exc.details
    return payload


def _refuse(reason_code, details=None):
    raise ExportError(reason_code, 3, details)


def _usage_error(reason_code, details=None):
    raise ExportError(reason_code, 2, details)


def _operation_failed(reason_code="export_operation_failed", details=None):
    raise ExportError(reason_code, 1, details)


def _as_export_error(exc):
    """Re-raise a ProvenanceError (or AuthorityError) as an ExportError,
    preserving reason_code, exit_code and details verbatim -- the same
    idiom nightshift_publication._as_publication_error uses for the same
    reason: a caller of THIS module should only ever see ONE exception
    type, never need to know which sibling module a refusal originated in."""
    return ExportError(exc.reason_code, exc.exit_code, dict(exc.details))


def _validate_ids(tenant_id, repository_id):
    try:
        if auth is not None:
            try:
                auth.validate_repository_id(repository_id)
            except auth.AuthorityError as exc:
                raise _as_export_error(exc) from exc
        else:                                               # pragma: no cover
            prov.validate_repository_id(repository_id)
        prov.validate_tenant_id(tenant_id)
    except prov.ProvenanceError as exc:
        raise _as_export_error(exc) from exc


def _validate_out_dir(path):
    if (not isinstance(path, str) or not path or not path.startswith("/")
            or path != os.path.normpath(path)):
        _usage_error("invalid_out_dir", {"out_dir": path})
    return path


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _rows_for(cx, tenant_id, repository_id):
    rows = {"source_snapshot": prov.export_source_snapshots(cx, tenant_id, repository_id)}
    for table in ("closure_entry", "lineage_node"):
        cursor = cx.execute(
            f"SELECT * FROM {table} WHERE tenant_id=? AND repository_id=? "
            f"ORDER BY rowid", (tenant_id, repository_id))
        rows[table] = [dict(r) for r in cursor.fetchall()]
    # derived_from carries NO tenant_id/repository_id columns of its own --
    # PROVENANCE_SCHEMA scopes it only through its endpoints' lineage_node
    # rows (record_derived_from refuses an edge whose endpoints do not
    # already match one (tenant, repository)). Since provenance.db is
    # ITSELF one physically separate file per (tenant, repository)
    # (resolve_publication_root), every row already belongs to this scope
    # by construction -- there is no column to filter on and none is needed.
    rows["derived_from"] = [dict(r) for r in cx.execute(
        "SELECT * FROM derived_from ORDER BY rowid").fetchall()]
    return rows


def _staged_object_files(objects_root):
    """Every regular file physically present under this tenant/repository's
    `objects/` tree, as (relative_path, absolute_path) pairs. Walking the
    real directory -- rather than trying to infer which rows reference a
    staged object -- is deliberate: `closure_entry.digest` is a checked Git
    OID, not necessarily a `stage_object` content_digest, and inferring the
    wrong relationship would either miss real staged bytes or claim ones
    that were never written. Whatever physically exists here for this
    tenant/repository IS the CAS state to export."""
    found = []
    if not os.path.isdir(objects_root):
        return found
    for dirpath, _dirnames, filenames in os.walk(objects_root):
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, objects_root)
            found.append((os.path.join(OBJECTS_DIRNAME, rel_path), abs_path))
    return sorted(found)


def export_tenant(state_root, tenant_id, repository_id, out_dir, *, t=None):
    """Write an nse-v1 bundle to `out_dir` (created if absent, must be
    empty or absent) and return a receipt. Reads provenance.db for this
    tenant/repository (refuses `provenance_db_missing` if it does not
    exist) plus whatever is physically staged under this tenant/
    repository's CAS `objects/` tree."""
    _validate_ids(tenant_id, repository_id)
    out_dir = _validate_out_dir(out_dir)
    t = time.time() if t is None else t
    start = time.monotonic()

    if os.path.isdir(out_dir) and os.listdir(out_dir):
        _refuse("out_dir_not_empty", {"out_dir": out_dir})

    try:
        cx = prov.connect(state_root, tenant_id, repository_id, create=False)
        try:
            rows = _rows_for(cx, tenant_id, repository_id)
        finally:
            cx.close()
        root = prov.resolve_publication_root(state_root, tenant_id, repository_id)
    except prov.ProvenanceError as exc:
        raise _as_export_error(exc) from exc
    objects_root = os.path.join(root, OBJECTS_DIRNAME)
    object_files = _staged_object_files(objects_root)

    entries = []

    for table in _ROW_TABLES:
        payload = _canonical_json(rows[table])
        rel_path = os.path.join(ROWS_DIRNAME, f"{table}.json")
        _write_bytes(os.path.join(out_dir, rel_path), payload)
        entries.append({"path": rel_path, "sha256": _sha256_bytes(payload), "size": len(payload)})

    for rel_path, abs_path in object_files:
        digest = _sha256_file(abs_path)
        size = os.path.getsize(abs_path)
        dest = os.path.join(out_dir, rel_path)
        with open(abs_path, "rb") as fh:
            _write_bytes(dest, fh.read())
        entries.append({"path": rel_path, "sha256": digest, "size": size})

    entries.sort(key=lambda e: e["path"])
    entries_digest = _sha256_bytes(_canonical_json(entries))

    manifest = {
        "format_version": EXPORT_FORMAT_VERSION,
        "tenant_id": tenant_id,
        "repository_id": repository_id,
        "generated_at": t,
        "entries": entries,
        "entries_digest": entries_digest,
    }
    _write_bytes(os.path.join(out_dir, MANIFEST_FILENAME), _canonical_json(manifest))

    duration_seconds = time.monotonic() - start
    receipt = {
        "tenant_id": tenant_id,
        "repository_id": repository_id,
        "out_dir": out_dir,
        "generated_at": t,
        "duration_seconds": duration_seconds,
        "entries_count": len(entries),
        "entries_digest": entries_digest,
    }
    _record_export_receipt(state_root, tenant_id, repository_id, receipt)

    return {"ok": True, "out_dir": out_dir, "manifest_digest": entries_digest,
            "entries_count": len(entries), "duration_seconds": duration_seconds}


# ---------------------------------------------------------------------------
# Export receipts -- Nightshift's OWN internal record that an export ran,
# kept beside this tenant/repository's other state (NOT inside the bundle
# handed to the customer -- the bundle's manifest is self-describing and
# does not need to know how many other exports were ever produced). This is
# what makes AC 21's export_duration MEASURED rather than REFUSED once at
# least one export exists.
# ---------------------------------------------------------------------------

def _receipts_dir(state_root, tenant_id, repository_id):
    root = prov.resolve_publication_root(state_root, tenant_id, repository_id)
    return os.path.join(root, RECEIPTS_DIRNAME)


def _record_export_receipt(state_root, tenant_id, repository_id, receipt):
    directory = _receipts_dir(state_root, tenant_id, repository_id)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    name = "%020.6f-%s.json" % (receipt["generated_at"], hashlib.sha256(
        _canonical_json(receipt)).hexdigest()[:12])
    _write_bytes(os.path.join(directory, name), _canonical_json(receipt))


def list_export_receipts(state_root, tenant_id, repository_id):
    """Read-only: every export receipt recorded for this tenant/repository,
    oldest first. Returns `[]` (not an error) when no export has ever run --
    that is what lets nightshift_observability distinguish "zero exports so
    far" from "the receipts directory is unreadable"."""
    directory = _receipts_dir(state_root, tenant_id, repository_id)
    if not os.path.isdir(directory):
        return []
    receipts = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name), "r", encoding="utf-8") as fh:
            receipts.append(json.load(fh))
    receipts.sort(key=lambda r: r["generated_at"])
    return receipts


# ---------------------------------------------------------------------------
# Verify -- a PURE function of the bundle. No import of nightshift_provenance
# is used below this line; nothing here can reach a database even by
# accident.
# ---------------------------------------------------------------------------

def verify_export(out_dir):
    """Verify an nse-v1 bundle using ONLY the files under `out_dir`.
    Returns `{"ok": True, "verified_files": N, "manifest_digest": ...}` or
    raises `ExportError` with one of:

      manifest_missing / manifest_malformed
      unsupported_format_version
      manifest_entries_digest_mismatch   -- the entries list itself was edited
      file_missing                       -- a manifest-listed file is absent
                                             (this is what "a removed row" or
                                             "a truncated manifest" looks like
                                             from the verifier's side)
      file_digest_mismatch               -- a manifest-listed file's bytes
                                             changed (a flipped byte)
      dangling_derived_from_parent       -- a derived_from edge names a
                                             lineage_node id absent from the
                                             bundle's own lineage_node.json
    """
    manifest_path = os.path.join(out_dir, MANIFEST_FILENAME)
    if not os.path.isfile(manifest_path):
        raise ExportError("manifest_missing", 3, {"out_dir": out_dir})
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        entries = manifest["entries"]
        entries_digest = manifest["entries_digest"]
        format_version = manifest["format_version"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ExportError("manifest_malformed", 3, {"error": str(exc)}) from exc

    if format_version != EXPORT_FORMAT_VERSION:
        raise ExportError("unsupported_format_version", 3, {"format_version": format_version})

    recomputed_entries_digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if recomputed_entries_digest != entries_digest:
        raise ExportError("manifest_entries_digest_mismatch", 3, {
            "recorded": entries_digest, "recomputed": recomputed_entries_digest})

    verified = 0
    for entry in entries:
        path = os.path.join(out_dir, entry["path"])
        if not os.path.isfile(path):
            raise ExportError("file_missing", 3, {"path": entry["path"]})
        actual_size = os.path.getsize(path)
        if actual_size != entry["size"]:
            raise ExportError("file_digest_mismatch", 3, {
                "path": entry["path"], "reason": "size_mismatch",
                "expected_size": entry["size"], "actual_size": actual_size})
        actual_digest = _sha256_file(path)
        if actual_digest != entry["sha256"]:
            raise ExportError("file_digest_mismatch", 3, {
                "path": entry["path"], "expected": entry["sha256"], "actual": actual_digest})
        verified += 1

    # Referential integrity, entirely inside the bundle: every derived_from
    # edge endpoint must be a real lineage_node id THIS bundle also carries.
    lineage_path = os.path.join(out_dir, ROWS_DIRNAME, "lineage_node.json")
    derived_path = os.path.join(out_dir, ROWS_DIRNAME, "derived_from.json")
    if os.path.isfile(lineage_path) and os.path.isfile(derived_path):
        with open(lineage_path, "r", encoding="utf-8") as fh:
            node_ids = {row["node_id"] for row in json.load(fh)}
        with open(derived_path, "r", encoding="utf-8") as fh:
            edges = json.load(fh)
        for edge in edges:
            for side in ("parent_node_id", "child_node_id"):
                if edge[side] not in node_ids:
                    raise ExportError("dangling_derived_from_parent", 3, {
                        "edge": edge, "missing_node_id": edge[side]})

    return {"ok": True, "verified_files": verified, "manifest_digest": entries_digest}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def out(obj, code=0):
    print(json.dumps(obj, sort_keys=True))
    sys.exit(code)


def cmd_export(args):
    return export_tenant(args.state_root, args.tenant_id, args.repository_id, args.out_dir)


def cmd_verify(args):
    return verify_export(args.out_dir)


def build_parser():
    p = argparse.ArgumentParser(prog="nightshift_export")
    sub = p.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="write an nse-v1 export bundle")
    exp.add_argument("--state-root", required=True)
    exp.add_argument("--tenant-id", required=True, type=int)
    exp.add_argument("--repository-id", required=True, type=int)
    exp.add_argument("--out-dir", required=True)
    exp.set_defaults(func=cmd_export)

    ver = sub.add_parser("verify", help="verify an nse-v1 bundle -- reads only the bundle")
    ver.add_argument("--out-dir", required=True)
    ver.set_defaults(func=cmd_verify)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except ExportError as exc:
        out(error_payload(exc), exc.exit_code)
    except Exception:
        out({"ok": False, "reason_code": "export_operation_failed"}, 1)
    out(result)


if __name__ == "__main__":
    main()
