#!/usr/bin/env python3
"""ADR-0082 phases 1-4: inventory, snapshot, shadow import, compare.

NO CUTOVER LIVES HERE. The flip is `nightshift_authority.switch_authority`
and nothing in this module can perform it. That separation is deliberate:
phases 1-4 are repeatable and mutate nothing that anyone is reading, while
phase 5 is the single irreversible-until-rollback moment. Putting them in
one module would put a `switch` one typo away from a `compare`.

WHAT "LEGACY MATERIAL" MEANS HERE
=================================
ADR-0082: "Today's `.omni/`, generated documents and worktrees mix control,
private narrative and customer-visible files in one repository." Phase 1
classifies all of it "without mutation". The five classes are:

  control            the operational plane -- control.db and the JSON that
                     shadows it. Authority for this moves at the switch.
  memory             private narrative -- ledger, decisions, reviews,
                     handoffs, evidence. Imported, never published.
  approved_document  customer-visible files that went through review.
  disposable         build residue: *.pyc, __pycache__, .DS_Store, *.tmp.
  unknown            everything else.

`unknown` IS THE POINT. It is not a bucket for things the classifier could
not be bothered with; it is the honest answer for a file whose owner nobody
recorded. ADR-0082 says migration must proceed "without inventing
authority". Guessing `unknown` -> `disposable` invents the authority to
delete someone's file; guessing `unknown` -> `memory` invents the authority
to import it into a store with different retention. So `unknown` is
reported, counted, and left exactly where it is, and the operator decides.
This module contains no code path that deletes anything.

ZERO FOOTPRINT ON THE SOURCE
============================
Every read of `repo_path` here is a read. Notably the source SQLite files
are NEVER opened by this process: opening a SQLite database can create
`-wal` and `-shm` siblings, which is a WRITE into the source tree and
exactly the kind of footprint `tests/test_zero_footprint.py` exists to
catch. Instead the database files are byte-copied first and every query
runs against the COPY. Git is likewise only ever invoked through
`_git_read`, whose environment sets `GIT_OPTIONAL_LOCKS=0` so a read
command cannot opportunistically rewrite `.git/index`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time

CLASSES = ("control", "memory", "approved_document", "unknown", "disposable")

MIGRATION_DIRNAME = "migration"
SNAPSHOT_DIRNAME = "snapshot"
SHADOW_DIRNAME = "shadow"

#: Imported from the authority module so there is ONE spelling of each
#: marker in the tree; a second spelling is a second, silently-divergent
#: definition of "this namespace denies commands".
try:                                                    # pragma: no cover
    import nightshift_authority as _authority
except ImportError:                                     # pragma: no cover
    _authority = None

SHADOW_MARKER_FILENAME = (getattr(_authority, "SHADOW_MARKER_FILENAME", None)
                          or ".nightshift-shadow-namespace")
RETAINED_SNAPSHOT_MARKER = (getattr(_authority, "RETAINED_SNAPSHOT_MARKER", None)
                            or ".nightshift-retained-snapshot")

LEGACY_DIRNAME = ".omni"

_CONTROL_NAMES = frozenset({
    "control.db", "control.db-wal", "control.db-shm", "control.db-journal",
    "control-backup.json", "capacity.json", "tracker.json", "models.json",
})
_CONTROL_GLOBS = ("registry-*.json",)
_CONTROL_DIRS = ("leases",)

_MEMORY_NAMES = frozenset({
    "STATUS.md", "backlog.md", "blocked.md", "brief.md", "decisions.md",
    "flaky.md", "goal.md", "integration-log.md", "ledger.md", "reviews.md",
    "tech-debt.md", "working-agreements.md",
})
_MEMORY_DIRS = ("charters", "evidence", "handoffs", "reports", "sync")

_DISPOSABLE_GLOBS = ("*.pyc", "*.pyo", "*.tmp", "*.swp", "*~", ".DS_Store")
_DISPOSABLE_DIRS = ("__pycache__",)

#: How many files `compare` reads bytes from, and the digest window at each
#: end. Sampling is deterministic (sorted relpath order, fixed stride) so
#: two runs compare the SAME files -- a random sample that happened to miss
#: a corrupted file would report `match` and mean nothing.
SAMPLE_COUNT = 24
SAMPLE_WINDOW = 4096


class MigrationError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise MigrationError(reason_code, 3, details)


def _failed(reason_code, details=None):
    raise MigrationError(reason_code, 1, details)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _validate_abs_dir(path, reason_code):
    if (not isinstance(path, str) or not path
            or len(path.encode("utf-8")) > 4096
            or not path.startswith("/")
            or path != os.path.normpath(path)
            or path.startswith("//")):
        _refuse(reason_code)
    return path


def validate_migration_id(value):
    """A path segment. Refused with the same strictness a tenant id gets,
    because it becomes a directory name under `state_root/migration/`."""
    if (not isinstance(value, str) or not value or len(value) > 128
            or value in (".", "..")
            or not all(ch.isalnum() or ch in "-_." for ch in value)
            or ".." in value):
        _refuse("invalid_migration_id", {"migration_id": value})
    return value


def migration_root(state_root, migration_id, *, create=False):
    _validate_abs_dir(state_root, "invalid_state_root")
    validate_migration_id(migration_id)
    root = os.path.join(state_root, MIGRATION_DIRNAME, migration_id)
    if create:
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        os.chmod(os.path.dirname(root), 0o700)
        # The marker goes down at workspace-creation time, not at
        # shadow-import time. From the first byte written, the ENTIRE
        # migration workspace denies live commands
        # (`nightshift_authority.guard` walks up and finds this). Writing it
        # later would leave a window in which the workspace looked like an
        # ordinary state root.
        marker = os.path.join(root, SHADOW_MARKER_FILENAME)
        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write("ADR-0082 phase 3: this namespace has no authority "
                         "binding and denies live commands.\n")
            os.chmod(marker, 0o600)
    return root


def snapshot_root(state_root, migration_id, *, create=False):
    root = os.path.join(migration_root(state_root, migration_id, create=create),
                        SNAPSHOT_DIRNAME)
    if create:
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def shadow_root(state_root, migration_id, *, create=False):
    root = os.path.join(migration_root(state_root, migration_id, create=create),
                        SHADOW_DIRNAME)
    if create:
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
    return root


# ---------------------------------------------------------------------------
# Read-only helpers
# ---------------------------------------------------------------------------

def _git_env():
    env = dict(os.environ)
    # GIT_OPTIONAL_LOCKS=0 is what stops a READ command (`status`,
    # `ls-files`, `for-each-ref`) from opportunistically rewriting
    # `.git/index`'s stat cache -- a write into the source tree that
    # `find -newer` would catch and that nothing here is entitled to make.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def _git_read(repo_path, args, *, timeout=30):
    try:
        result = subprocess.run(["git", *args], cwd=repo_path, env=_git_env(),
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, result.stderr.strip()
    return result.stdout, None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_files(root):
    """Every regular file under `root`, as sorted relative paths. Symlinks
    are reported but never followed -- following one would let a link inside
    the legacy tree pull an arbitrary file into the snapshot."""
    files, links = [], []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                links.append(rel)
            elif os.path.isfile(full):
                files.append(rel)
    return files, links


# ---------------------------------------------------------------------------
# Phase 1 -- inventory
# ---------------------------------------------------------------------------

def classify(relpath):
    """Classify ONE path relative to the repository root. Pure, so the
    classification table is testable without a repository."""
    parts = relpath.split(os.sep)
    name = parts[-1]

    for pattern in _DISPOSABLE_GLOBS:
        if fnmatch.fnmatch(name, pattern):
            return "disposable"
    if any(part in _DISPOSABLE_DIRS for part in parts):
        return "disposable"

    if parts[0] == LEGACY_DIRNAME:
        inner = parts[1:]
        if not inner:
            return "unknown"
        head = inner[0]
        if len(inner) > 1:
            if head in _CONTROL_DIRS:
                return "control"
            if head in _MEMORY_DIRS:
                return "memory"
            return "unknown"
        if head in _CONTROL_NAMES:
            return "control"
        if any(fnmatch.fnmatch(head, g) for g in _CONTROL_GLOBS):
            return "control"
        if head in _MEMORY_NAMES:
            return "memory"
        return "unknown"

    if parts[0] == "docs":
        return "approved_document"
    return "unknown"


def inventory(repo_path, state_root, *, migration_id, t=None):
    """Phase 1. Classifies every legacy file, worktree and ref. MUTATES
    NOTHING under `repo_path`.

    The report is written under `state_root`, never under `repo_path` --
    writing the inventory into the tree being inventoried would change the
    thing being measured, and would be a footprint violation on the first
    run.
    """
    _validate_abs_dir(repo_path, "invalid_repo_path")
    validate_migration_id(migration_id)
    t = time.time() if t is None else float(t)
    if not os.path.isdir(repo_path):
        _refuse("repo_path_missing", {"path": repo_path})

    legacy_dir = os.path.join(repo_path, LEGACY_DIRNAME)
    entries = []
    counts = {name: 0 for name in CLASSES}
    bytes_by_class = {name: 0 for name in CLASSES}
    symlinks = []

    roots = []
    if os.path.isdir(legacy_dir):
        roots.append((LEGACY_DIRNAME, legacy_dir))
    docs_dir = os.path.join(repo_path, "docs")
    if os.path.isdir(docs_dir):
        roots.append(("docs", docs_dir))

    for prefix, root in roots:
        files, links = _walk_files(root)
        for rel in links:
            symlinks.append(os.path.join(prefix, rel))
        for rel in files:
            relpath = os.path.join(prefix, rel)
            klass = classify(relpath)
            try:
                size = os.path.getsize(os.path.join(root, rel))
            except OSError:
                size = None
            counts[klass] += 1
            if size:
                bytes_by_class[klass] += size
            entries.append({"path": relpath, "class": klass, "size": size})

    tracked = _tracked_paths(repo_path)
    if tracked is not None:
        for entry in entries:
            entry["tracked"] = entry["path"] in tracked

    report = {
        "migration_id": migration_id,
        "phase": "inventory",
        "repo_path": repo_path,
        "generated_at": t,
        "counts": counts,
        "bytes_by_class": bytes_by_class,
        "file_count": len(entries),
        "symlinks": symlinks,
        "unknown": [e["path"] for e in entries if e["class"] == "unknown"],
        "worktrees": list_worktrees(repo_path),
        "refs": list_refs(repo_path),
        "entries": entries,
        "mutations": [],
        "note": "unknown entries are neither guessed nor deleted "
                "(ADR-0082: migrate without inventing authority)",
    }
    root = migration_root(state_root, migration_id, create=True)
    path = os.path.join(root, "inventory.json")
    _write_json(path, report)
    report["report_path"] = path
    return report


def _tracked_paths(repo_path):
    stdout, err = _git_read(repo_path, ["ls-files", "-z"])
    if stdout is None:
        return None
    return {p for p in stdout.split("\0") if p}


def list_worktrees(repo_path):
    stdout, err = _git_read(repo_path, ["worktree", "list", "--porcelain"])
    if stdout is None:
        return {"available": False, "error": err, "entries": []}
    entries, current = [], {}
    for line in stdout.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return {"available": True, "error": None, "entries": entries}


def list_refs(repo_path):
    stdout, err = _git_read(
        repo_path, ["for-each-ref", "--format=%(refname) %(objectname) %(objecttype)"])
    if stdout is None:
        return {"available": False, "error": err, "entries": []}
    entries = []
    for line in stdout.splitlines():
        parts = line.split(" ")
        if len(parts) == 3:
            entries.append({"refname": parts[0], "oid": parts[1], "type": parts[2]})
    return {"available": True, "error": None, "entries": entries}


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def read_report(state_root, migration_id, name):
    path = os.path.join(migration_root(state_root, migration_id), name)
    if not os.path.isfile(path):
        _refuse("migration_report_missing", {"expected_path": path})
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Phase 2 -- consistent snapshot
# ---------------------------------------------------------------------------

def snapshot(repo_path, state_root, *, migration_id, t=None, authority_cx=None):
    """Phase 2: the consistent copy plus high-water sequence, per-file
    sha256 and counts. Refuses on digest mismatch.

    `authority_cx`, when supplied, is REQUIRED to be quiesced. ADR-0082
    phase 2 is "Briefly quiesce to capture a consistent snapshot" -- a
    snapshot of a store that is still accepting writes is not consistent,
    and calling it consistent is the kind of claim this repository spent
    2026-09-02 removing. Passing no `authority_cx` is allowed only for a
    rehearsal against a store nobody is writing (which is exactly what the
    test does, against a COPY); the returned report records which of the two
    it was, so a reader can never mistake one for the other.
    """
    _validate_abs_dir(repo_path, "invalid_repo_path")
    validate_migration_id(migration_id)
    t = time.time() if t is None else float(t)

    quiesce_verified = False
    if authority_cx is not None:
        if _authority is None:                       # pragma: no cover
            _failed("authority_module_unavailable")
        binding = _authority.read_binding(authority_cx)
        if binding is None:
            _refuse("authority_binding_missing")
        if not binding["quiesced"]:
            _refuse("snapshot_requires_quiesce",
                    {"migration_id": binding["migration_id"]})
        quiesce_verified = True

    legacy_dir = os.path.join(repo_path, LEGACY_DIRNAME)
    if not os.path.isdir(legacy_dir):
        _refuse("legacy_dir_missing", {"path": legacy_dir})

    dest_root = snapshot_root(state_root, migration_id, create=True)
    dest_legacy = os.path.join(dest_root, LEGACY_DIRNAME)
    if os.path.isdir(dest_legacy):
        # A previous snapshot left this tree chmod'ed read-only on purpose;
        # restore write permission on OUR OWN copy before replacing it, so a
        # re-snapshot does not fail on the protection it installed itself.
        _make_tree_writable(dest_legacy)
        shutil.rmtree(dest_legacy)
    stale_marker = os.path.join(dest_root, RETAINED_SNAPSHOT_MARKER)
    if os.path.isfile(stale_marker):
        os.chmod(stale_marker, 0o600)
        os.remove(stale_marker)
    os.makedirs(dest_legacy, mode=0o700, exist_ok=True)

    files, links = _walk_files(legacy_dir)
    manifest = {}
    total_bytes = 0
    for rel in files:
        src = os.path.join(legacy_dir, rel)
        dst = os.path.join(dest_legacy, rel)
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        source_digest = sha256_file(src)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o600)
        verify_copy(src, dst, rel, source_digest=source_digest)
        size = os.path.getsize(dst)
        total_bytes += size
        manifest[rel] = {"sha256": source_digest, "size": size}

    high_water = _high_water(dest_legacy)

    report = {
        "migration_id": migration_id,
        "phase": "snapshot",
        "repo_path": repo_path,
        "snapshot_path": dest_root,
        "captured_at": t,
        "quiesce_verified": quiesce_verified,
        "consistency": ("quiesced" if quiesce_verified
                        else "unquiesced_rehearsal"),
        "file_count": len(manifest),
        "total_bytes": total_bytes,
        "symlinks": links,
        "manifest_digest": manifest_digest(manifest),
        "high_water": high_water,
        "manifest": manifest,
    }
    _write_json(os.path.join(migration_root(state_root, migration_id),
                             "snapshot.json"), report)

    # The retained snapshot is read-only evidence from here on. The marker
    # is what `nightshift_authority.rollback_authority` refuses, so it must
    # be written by the snapshot itself and never by the caller.
    marker = os.path.join(dest_root, RETAINED_SNAPSHOT_MARKER)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("ADR-0082: retained read-only snapshot. Rollback replays "
                 "into a FRESH legacy copy; this tree is never reopened as "
                 "the live store.\n")
    os.chmod(marker, 0o400)
    _make_tree_read_only(dest_legacy)
    return report


def verify_copy(src, dst, rel, *, source_digest=None):
    """Re-hash BOTH sides and refuse `snapshot_digest_mismatch` if they
    differ.

    A named function rather than four inline lines so the refusal is
    reachable, and therefore testable, on real files with genuinely
    different bytes -- a snapshot whose digests were never actually checked
    is a snapshot that cannot be evidence for anything downstream, and a
    check nothing exercises is indistinguishable from that.
    """
    source_digest = sha256_file(src) if source_digest is None else source_digest
    copy_digest = sha256_file(dst)
    if copy_digest != source_digest:
        _refuse("snapshot_digest_mismatch", {
            "path": rel, "source_digest": source_digest,
            "copy_digest": copy_digest})
    return source_digest


def manifest_digest(manifest):
    """Order-independent digest over (path, sha256, size). Same canonical
    JSON dialect `nightshift_publication.canonical_json_bytes` uses, so the
    two hardened digest surfaces in this tree agree."""
    payload = sorted((path, entry["sha256"], entry["size"])
                     for path, entry in manifest.items())
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(blob).hexdigest()


def _make_tree_writable(root):
    for dirpath, dirnames, filenames in os.walk(root):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
        for name in dirnames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o700)
            except OSError:
                pass
        for name in filenames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o600)
            except OSError:
                pass


def _make_tree_read_only(root):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o400)
            except OSError:
                pass
        for name in dirnames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o500)
            except OSError:
                pass
    try:
        os.chmod(root, 0o500)
    except OSError:
        pass


def _high_water(legacy_dir):
    """Per-table MAX(rowid) plus `sqlite_sequence`, read from a THROWAWAY
    copy of each database file.

    ADR-0082 phase 4 replays "from the high-water mark", so the mark has to
    be a real sequence position, not a row count -- a count cannot tell you
    where to resume after rows were deleted.

    Two reasons the read happens on a copy in a tempdir and never in place:

      1. Footprint. `sqlite3.connect` on a WAL database creates `-shm` and
         `-wal` siblings. Doing that inside `repo_path` writes into the tree
         phase 1 and 2 promised not to touch.
      2. Comparability. The retained snapshot is chmod'ed read-only, the
         shadow namespace is not. Reading in place would fail on one side
         and succeed on the other, and `compare` would report a mismatch
         that is an artefact of file permissions rather than of the data --
         a false negative dressed up as evidence.
    """
    result = {}
    names = sorted(n for n in os.listdir(legacy_dir) if n.endswith(".db"))
    if not names:
        return result
    work = tempfile.mkdtemp(prefix="nightshift-migrate-read-")
    try:
        for name in names:
            src = os.path.join(legacy_dir, name)
            dst = os.path.join(work, name)
            try:
                shutil.copyfile(src, dst)
                os.chmod(dst, 0o600)
                for suffix in ("-wal", "-shm"):
                    sidecar = src + suffix
                    if os.path.isfile(sidecar):
                        shutil.copyfile(sidecar, dst + suffix)
                        os.chmod(dst + suffix, 0o600)
                cx = sqlite3.connect(dst, timeout=10)
            except (sqlite3.Error, OSError) as exc:
                result[name] = {"readable": False, "error": str(exc)}
                continue
            try:
                tables = [r[0] for r in cx.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
                marks, counts = {}, {}
                for table in tables:
                    quoted = '"' + table.replace('"', '""') + '"'
                    try:
                        marks[table] = cx.execute(
                            f"SELECT MAX(rowid) FROM {quoted}").fetchone()[0]
                        counts[table] = cx.execute(
                            f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                    except sqlite3.Error as exc:
                        marks[table] = None
                        counts[table] = {"error": str(exc)}
                try:
                    sequences = {r[0]: r[1] for r in cx.execute(
                        "SELECT name, seq FROM sqlite_sequence").fetchall()}
                except sqlite3.Error:
                    sequences = {}
                result[name] = {"readable": True, "tables": tables,
                                "max_rowid": marks, "row_counts": counts,
                                "sqlite_sequence": sequences}
            finally:
                cx.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


# ---------------------------------------------------------------------------
# Phase 3 -- shadow import
# ---------------------------------------------------------------------------

def shadow_import(state_root, *, migration_id, t=None):
    """Phase 3. Idempotent import of the retained snapshot into
    `<state_root>/migration/<migration_id>/shadow/`.

    That namespace has NO `authority.db` and never gets one, so
    `nightshift_authority.guard` refuses every live command against it with
    `shadow_namespace_denies_commands`. The denial is STRUCTURAL: allowing a
    command there would require creating a second authority binding, which
    is the dual authority ADR-0082 forbids. There is no flag to flip.

    Idempotent means byte-identical on a second run, not merely
    "same count": the returned `manifest_digest` is over content, so a
    re-import that silently rewrote a file would change it.
    """
    validate_migration_id(migration_id)
    t = time.time() if t is None else float(t)
    snap_report = read_report(state_root, migration_id, "snapshot.json")
    src_legacy = os.path.join(snapshot_root(state_root, migration_id),
                              LEGACY_DIRNAME)
    if not os.path.isdir(src_legacy):
        _refuse("snapshot_missing", {"expected_path": src_legacy})

    dest = shadow_root(state_root, migration_id, create=True)
    dest_legacy = os.path.join(dest, LEGACY_DIRNAME)

    files, _links = _walk_files(src_legacy)
    imported, skipped = 0, 0
    manifest = {}
    for rel in files:
        src = os.path.join(src_legacy, rel)
        dst = os.path.join(dest_legacy, rel)
        digest = sha256_file(src)
        if os.path.isfile(dst) and sha256_file(dst) == digest:
            skipped += 1
        else:
            os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
            tmp = dst + ".tmp"
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, dst)
            imported += 1
        manifest[rel] = {"sha256": digest, "size": os.path.getsize(dst)}

    report = {
        "migration_id": migration_id,
        "phase": "shadow_import",
        "shadow_path": dest,
        "imported_at": t,
        "imported": imported,
        "skipped_already_identical": skipped,
        "file_count": len(manifest),
        "manifest_digest": manifest_digest(manifest),
        "source_manifest_digest": snap_report["manifest_digest"],
        "shadow_marker": os.path.join(
            migration_root(state_root, migration_id), SHADOW_MARKER_FILENAME),
        "accepts_live_commands": False,
        "manifest": manifest,
    }
    _write_json(os.path.join(migration_root(state_root, migration_id),
                             "shadow_import.json"), report)
    return report


# ---------------------------------------------------------------------------
# Phase 4 -- compare
# ---------------------------------------------------------------------------

def nsc_v1_reads(root):
    """The adapter-agnostic read set both sides must answer identically.

    Every entry is a READ an ordinary client would make, not an
    implementation detail of either side: how many files, how big, what
    their content digests are, which tables exist, how many rows each holds,
    and the actual bytes at both ends of a deterministic sample.
    """
    legacy = os.path.join(root, LEGACY_DIRNAME)
    if not os.path.isdir(legacy):
        return {"present": False}
    files, links = _walk_files(legacy)
    manifest = {}
    total = 0
    for rel in files:
        full = os.path.join(legacy, rel)
        size = os.path.getsize(full)
        total += size
        manifest[rel] = {"sha256": sha256_file(full), "size": size}
    sampled = {}
    ordered = sorted(manifest)
    if ordered:
        stride = max(1, len(ordered) // SAMPLE_COUNT)
        for rel in ordered[::stride][:SAMPLE_COUNT]:
            full = os.path.join(legacy, rel)
            with open(full, "rb") as fh:
                head = fh.read(SAMPLE_WINDOW)
                fh.seek(max(0, os.path.getsize(full) - SAMPLE_WINDOW))
                tail = fh.read(SAMPLE_WINDOW)
            sampled[rel] = hashlib.sha256(head + b"\x00" + tail).hexdigest()
    return {
        "present": True,
        "file_count": len(manifest),
        "total_bytes": total,
        "symlinks": sorted(links),
        "manifest_digest": manifest_digest(manifest),
        "manifest": manifest,
        "sampled_bytes": sampled,
        "databases": _high_water(legacy),
    }


def compare(state_root, *, migration_id, t=None):
    """Phase 4: compare counts, checksums, sampled bytes and NSC-v1 read
    outcomes on BOTH sides.

    Returns `{"verdict": "match"|"mismatch", "mismatches": [...]}`. A
    mismatch is always NAMED -- which check, which path, which two values --
    because ADR-0082's reversal trigger is "any unexplained comparison
    mismatch", and a bare boolean can never be explained.
    """
    validate_migration_id(migration_id)
    t = time.time() if t is None else float(t)
    left_root = snapshot_root(state_root, migration_id)
    right_root = shadow_root(state_root, migration_id)
    left = nsc_v1_reads(left_root)
    right = nsc_v1_reads(right_root)

    mismatches = []
    if not left.get("present"):
        mismatches.append({"check": "side_present", "side": "legacy_snapshot"})
    if not right.get("present"):
        mismatches.append({"check": "side_present", "side": "shadow"})
    if mismatches:
        return _compare_report(state_root, migration_id, t, "mismatch",
                               mismatches, left, right)

    for key in ("file_count", "total_bytes", "manifest_digest", "symlinks"):
        if left[key] != right[key]:
            mismatches.append({"check": key, "legacy": left[key], "shadow": right[key]})

    left_paths, right_paths = set(left["manifest"]), set(right["manifest"])
    for path in sorted(left_paths - right_paths):
        mismatches.append({"check": "missing_on_shadow", "path": path})
    for path in sorted(right_paths - left_paths):
        mismatches.append({"check": "extra_on_shadow", "path": path})
    for path in sorted(left_paths & right_paths):
        if left["manifest"][path]["sha256"] != right["manifest"][path]["sha256"]:
            mismatches.append({
                "check": "content_digest", "path": path,
                "legacy": left["manifest"][path]["sha256"],
                "shadow": right["manifest"][path]["sha256"]})

    for path, digest in sorted(left["sampled_bytes"].items()):
        if right["sampled_bytes"].get(path) != digest:
            mismatches.append({"check": "sampled_bytes", "path": path,
                               "legacy": digest,
                               "shadow": right["sampled_bytes"].get(path)})

    for db_name in sorted(set(left["databases"]) | set(right["databases"])):
        lhs = left["databases"].get(db_name)
        rhs = right["databases"].get(db_name)
        if lhs != rhs:
            mismatches.append({"check": "nsc_v1_database_read", "database": db_name,
                               "legacy": lhs, "shadow": rhs})

    verdict = "match" if not mismatches else "mismatch"
    return _compare_report(state_root, migration_id, t, verdict, mismatches,
                           left, right)


def _compare_report(state_root, migration_id, t, verdict, mismatches, left, right):
    report = {
        "migration_id": migration_id,
        "phase": "compare",
        "compared_at": t,
        "verdict": verdict,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "legacy_snapshot": {k: v for k, v in left.items() if k != "manifest"},
        "shadow": {k: v for k, v in right.items() if k != "manifest"},
    }
    _write_json(os.path.join(migration_root(state_root, migration_id, create=True),
                             "compare.json"), report)
    return report
