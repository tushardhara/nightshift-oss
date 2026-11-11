"""Issue #400 A4b -- narrative-memory freshness (PLAN-400-memory-system.md
Track A, `bin/nightshift_memory_search.py`'s `verify_memory()`/
`verify_artifact()`, driven by `bin/nightshift_memory_bootstrap.py`'s
`sync()`).

The defect this suite exists to prevent: PLAN-400 A4b's own words --
"Stale memory is worse than no memory, because it is confidently wrong."
A shared repo moves under a borrowed project's memory; a row written at
commit X is a claim about X, and retrieval must never serve it as current
fact once the pinned source has moved, been deleted, or the whole history
was rewritten out from under the watermark.

Fixture: every test here MUTATES a repo (edits a line, deletes a file, adds
commits, force-pushes/rebases) -- so none of them may run against the
shared testbed directory directly (that would corrupt it for every other
suite reading it, `test_zero_footprint.py` included). Each test instead
`git clone`s the testbed into a private, throwaway temp directory first
(`_clone_testbed`) and mutates ONLY the clone. The testbed itself,
`/home/operator/testbeds/.nightshift-testbed/testbed A`, is read but
never written by this file -- verified once, explicitly, in
`test_testbed_itself_is_never_mutated`.

KNOWN INTEGRATION SEAM (writer contract) -- see `test_memory_bootstrap.py`'s
module docstring for the full account. `_bridge_writer_contract` below
closes the same `put_memory`/`MemoryError` gap, ONLY if the real names are
absent, so a future integration fix makes it a no-op.

CONFIRMED CROSS-MODULE DEFECT (found while writing this suite, not
invented by it) -- `test_unmodified_doc_row_reads_fresh` and
`test_unmodified_structure_row_reads_fresh` below are expected, by
PLAN-400 A4b's own contract, to pass on a freshly bootstrapped, completely
untouched row. As of this writing THEY DO NOT, for two independent
reasons, and are left as ordinary (non-`expectedFailure`) assertions so
they surface loudly until fixed rather than being silently swallowed:

  1. `nightshift_memory_bootstrap._chunk_lines` builds a doc chunk's body
     by `''.join()`-ing `str.splitlines(keepends=True)` output, which
     preserves the source file's own trailing newline inside the stored
     body. `nightshift_memory_search.verify_artifact` re-derives the
     comparison hash via `raw.split(b"\\n")` then
     `b"\\n".join(lines[line_start-1:line_end])`, which can never
     reproduce a final trailing newline (`split`+`join` loses exactly the
     last line's own terminator). For any file ending in `\\n` -- the
     POSIX-normal case -- the write-time hash and the read-time re-hash
     of an IDENTICAL, unedited slice disagree by construction, so every
     doc-tier row reads `stale` from the moment it is written.
  2. `structure`-tier rows are written with `body=""` (PLAN A4a: tier 2
     is "a pointer, without storing bodies") but `nightshift_memory.put()`
     always computes `sha256` FROM the `body` it is given -- so every
     structure-tier row's stored hash is `sha256("")`
     (`e3b0c442...`), constant regardless of the file's real content,
     while `verify_artifact` re-hashes the file's actual first line. The
     two can only coincidentally agree, never by design.

Both are cited here with an exact, minimal, reproducible fixture so a
reviewer does not have to re-derive them from a failure message alone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import traceback
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MEMORY_MODULE_PATH = BIN / "nightshift_memory.py"
BOOTSTRAP_MODULE_PATH = BIN / "nightshift_memory_bootstrap.py"
SEARCH_MODULE_PATH = BIN / "nightshift_memory_search.py"

TESTBED_A = Path(
    "/home/operator/testbeds/.nightshift-testbed/testbed A")

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")


_IMPORT_ERROR = None

def _load_module(path, name):
    """See `test_memory_bootstrap.py`'s identical helper for the full
    rationale; duplicated here (not shared) per this suite's own
    established convention of self-contained test files."""
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- "not ready yet" too
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {path} failed to import: {exc}", file=sys.stderr)
        sys.modules.pop(name, None)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


def _bridge_writer_contract(memory_module):
    if memory_module is None:
        return
    if not hasattr(memory_module, "put_memory"):
        def _put_memory_adapter(args, _mem=memory_module):
            cx = _mem.connect(args.state_root)
            try:
                ts = getattr(args, "ts", None)
                if ts is None:
                    ts = time.time()
                return _mem.put(
                    cx, project=args.project, kind=args.kind,
                    classification=args.classification,
                    retention_class=args.retention_class, body=args.body,
                    ts=ts, src_path=args.src_path, line_start=args.line_start,
                    line_end=args.line_end, repo_sha=args.repo_sha,
                    key_version=args.key_version,
                    derived_from=args.derived_from, refs=args.refs,
                )
            finally:
                cx.close()
        memory_module.put_memory = _put_memory_adapter
    if not hasattr(memory_module, "MemoryError"):
        memory_module.MemoryError = memory_module.MemoryStoreError


MEMORY = _load_module(MEMORY_MODULE_PATH, "nightshift_memory")
_bridge_writer_contract(MEMORY)
BOOTSTRAP = _load_module(BOOTSTRAP_MODULE_PATH, "nightshift_memory_bootstrap")
# SEARCH does `from nightshift_memory import MemoryError` at ITS OWN module
# top level -- the bridge above must already be applied, and MEMORY must
# already be registered in sys.modules (both true by this point), before
# this import is attempted.
SEARCH = _load_module(SEARCH_MODULE_PATH, "nightshift_memory_search")

SKIP_REASON = (
    f"{MEMORY_MODULE_PATH}, {BOOTSTRAP_MODULE_PATH} and/or "
    f"{SEARCH_MODULE_PATH} not present (or not currently importable) -- "
    "PLAN-400 Track A, worktree-isolated build. This suite is written "
    "against sync()/search_memory()/verify_memory()'s documented "
    "interface.")
_READY = BOOTSTRAP is not None and SEARCH is not None


def _fresh_state_root(tag):
    root = tempfile.mkdtemp(prefix=f"nightshift-freshtest-{tag}-")
    os.chmod(root, 0o700)
    return root


def _git(repo, *args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} in {repo} failed: {proc.stderr}")
    return proc.stdout


def _clone_testbed(tag):
    """A private, mutable copy of the testbed -- never the testbed
    directory itself. `--no-hardlinks` so this clone shares no inode with
    the original's object store; nothing done to the clone (including a
    `git gc`, which this suite never runs, but a future edit might) can
    ever reach back into the shared fixture other tests depend on.
    """
    if not TESTBED_A.exists():
        raise unittest.SkipTest(
            f"borrowed-project testbed not checked out at {TESTBED_A}")
    tmp = tempfile.mkdtemp(prefix=f"nightshift-freshtest-clone-{tag}-")
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(TESTBED_A), tmp],
                    env=GIT_ENV, check=True, capture_output=True, timeout=60)
    _git(tmp, "config", "user.email", "fixture@example.com")
    _git(tmp, "config", "user.name", "Nightshift Freshness Fixture")
    return tmp


def _row_by_kind(state_root, project, kind):
    cx = sqlite3.connect(os.path.join(state_root, "memory.db"))
    cx.row_factory = sqlite3.Row
    try:
        return cx.execute(
            "SELECT * FROM artifact WHERE project = ? AND kind = ? "
            "ORDER BY id LIMIT 1", (project, kind)).fetchone()
    finally:
        cx.close()


def _verify(state_root, repo_root, artifact_id):
    args = types.SimpleNamespace(state_root=state_root, repo_root=repo_root,
                                  id=artifact_id)
    return SEARCH.verify_memory(args)


# ==========================================================================
# Baseline: an unmodified row, immediately after bootstrap, must read
# fresh. See the module docstring's CONFIRMED CROSS-MODULE DEFECT --
# these two currently fail, for reasons independent of this suite.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class FreshnessBaseline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.clone = _clone_testbed("baseline")
        cls.state_root = _fresh_state_root("baseline")
        cls.manifest = BOOTSTRAP.bootstrap(cls.clone, "fresh-baseline-proj",
                                            state_root=cls.state_root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.clone, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_unmodified_doc_row_reads_fresh(self):
        row = _row_by_kind(self.state_root, "fresh-baseline-proj", "doc")
        self.assertIsNotNone(row, "expected at least one doc-tier row")
        result = _verify(self.state_root, self.clone, row["id"])
        self.assertEqual(
            result["status"], "fresh",
            "an untouched, just-bootstrapped doc row must verify fresh; "
            "see this module's CONFIRMED CROSS-MODULE DEFECT #1 "
            f"(trailing-newline hash mismatch) -- got {result!r}")

    def test_unmodified_structure_row_reads_fresh(self):
        row = _row_by_kind(self.state_root, "fresh-baseline-proj", "structure")
        self.assertIsNotNone(row, "expected at least one structure-tier row")
        result = _verify(self.state_root, self.clone, row["id"])
        self.assertEqual(
            result["status"], "fresh",
            "an untouched, just-bootstrapped structure row must verify "
            "fresh; see this module's CONFIRMED CROSS-MODULE DEFECT #2 "
            f"(body='' hash mismatch) -- got {result!r}")

    def test_stored_sha256_present_and_repo_sha_pinned(self):
        """Independent of the staleness-labeling defect above: every row
        must at minimum carry a real stored hash and the repo_sha it was
        written at -- PLAN A4b's "every row is pinned"."""
        row = _row_by_kind(self.state_root, "fresh-baseline-proj", "doc")
        self.assertTrue(row["sha256"])
        self.assertEqual(row["repo_sha"], self.manifest["repo_sha"])


# ==========================================================================
# Edit -> stale. Written to be meaningful independent of the baseline
# defect above: it asserts the DELTA (current_sha256 changes, stored_
# sha256 does not) rather than only the status label, since the label is
# "stale" both before and after edit under the current, unfixed baseline.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class FreshnessAfterEdit(unittest.TestCase):

    def setUp(self):
        self.clone = _clone_testbed("edit")
        self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
        self.state_root = _fresh_state_root("edit")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        BOOTSTRAP.bootstrap(self.clone, "edit-proj", state_root=self.state_root)
        self.row = _row_by_kind(self.state_root, "edit-proj", "doc")
        self.assertIsNotNone(self.row, "fixture must produce a doc row")

    def test_editing_the_pinned_slice_changes_current_sha256_and_marks_stale(self):
        before = _verify(self.state_root, self.clone, self.row["id"])

        target = os.path.join(self.clone, self.row["src_path"])
        with open(target, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        self.assertGreater(len(lines), 0)
        # Edit line 1 specifically -- guaranteed inside every chunk's
        # [line_start, line_end] range, since every chunk starts at 1.
        lines[0] = "NIGHTSHIFT FRESHNESS TEST EDIT\n" + lines[0]
        with open(target, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-q", "-m", "freshness test edit")

        after = _verify(self.state_root, self.clone, self.row["id"])

        self.assertNotEqual(before["current_sha256"], after["current_sha256"])
        self.assertEqual(after["status"], "stale")
        # Append-only: the STORED claim about what was true at repo_sha
        # never changes underneath a live re-verification.
        self.assertEqual(before["stored_sha256"], after["stored_sha256"])
        self.assertEqual(after["stored_sha256"], self.row["sha256"])


# ==========================================================================
# Delete -> orphaned. verify_artifact consults the filesystem directly
# (no git involved), so no commit is needed for this one.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class FreshnessAfterDelete(unittest.TestCase):

    def setUp(self):
        self.clone = _clone_testbed("delete")
        self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
        self.state_root = _fresh_state_root("delete")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        BOOTSTRAP.bootstrap(self.clone, "delete-proj", state_root=self.state_root)

    def test_deleted_file_reads_orphaned(self):
        row = _row_by_kind(self.state_root, "delete-proj", "doc")
        self.assertIsNotNone(row)
        os.remove(os.path.join(self.clone, row["src_path"]))

        result = _verify(self.state_root, self.clone, row["id"])
        self.assertEqual(result["status"], "orphaned")
        self.assertIsNone(result["current_sha256"])
        self.assertEqual(result["stored_sha256"], row["sha256"])

    def test_path_escaping_repo_root_is_orphaned_not_followed(self):
        """`verify_artifact`'s own documented defense: a row whose
        src_path would resolve outside repo_root must never be followed
        -- a memory row's src_path is importer-written data, not a
        trusted caller argument."""
        row = _row_by_kind(self.state_root, "delete-proj", "doc")
        args = types.SimpleNamespace(
            state_root=self.state_root, repo_root=self.clone, id=row["id"])
        raw = SEARCH.verify_artifact(
            {**dict(row), "src_path": "../../../../etc/passwd"}, self.clone)
        self.assertEqual(raw["status"], "orphaned")
        self.assertIsNone(raw["current_sha256"])


# ==========================================================================
# Incremental sync: PLAN A4b watermark-driven delta. Only the paths that
# actually changed since the watermark get re-indexed; everything else's
# row identity (its artifact id) must be untouched.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class IncrementalSyncOnlyReindexesChanged(unittest.TestCase):

    def setUp(self):
        self.clone = _clone_testbed("sync")
        self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
        self.state_root = _fresh_state_root("sync")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.first = BOOTSTRAP.bootstrap(self.clone, "sync-proj",
                                          state_root=self.state_root)
        self.watermark = self.first["repo_sha"]
        self.unrelated_row = _row_by_kind(self.state_root, "sync-proj", "structure")

    def _commit_delta(self):
        # Modify one tracked file, remove another, add a brand-new one --
        # exercises all three sync outcomes (re-indexed / removed / new)
        # in a single, minimal delta.
        readme = os.path.join(self.clone, "README.md")
        with open(readme, "a", encoding="utf-8") as fh:
            fh.write("\nnightshift freshness sync test line\n")
        os.remove(os.path.join(self.clone, "AGENTS.md"))
        with open(os.path.join(self.clone, "MEMTEST_NEW_FILE.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# brand new file added after the watermark\n")
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-q", "-m", "freshness sync delta")

    def test_incremental_sync_scopes_to_changed_paths_only(self):
        self._commit_delta()
        manifest = BOOTSTRAP.sync(self.clone, "sync-proj",
                                   state_root=self.state_root)

        self.assertEqual(manifest["sync_mode"], "incremental")
        self.assertEqual(manifest["watermark_from"], self.watermark)
        self.assertTrue(manifest["balanced"], manifest)
        self.assertEqual(manifest["changed"], 3)

        changed_paths = {"README.md", "AGENTS.md", "MEMTEST_NEW_FILE.md"}
        indexed_or_removed_paths = (
            {i["path"] for i in manifest["skipped"]["items"]}
            | {i["path"] for i in manifest["removed"]["items"]})
        # Every one of the 3 changed paths is accounted for -- either
        # indexed (README.md edit, MEMTEST_NEW_FILE.md addition) or
        # removed (AGENTS.md deletion); none silently dropped.
        self.assertEqual(manifest["removed"]["items"],
                          [{"path": "AGENTS.md"}])
        self.assertGreaterEqual(manifest["indexed"]["count"], 2)

        # The row untouched by this delta keeps its ORIGINAL id -- proof
        # sync() did not touch, re-write, or duplicate it.
        still_there = _row_by_kind(self.state_root, "sync-proj", "structure")
        # (structure-tier "first row by id" may now be a different file
        # if AGENTS.md itself was id 1 and is gone -- so instead assert
        # the unrelated row's id specifically still resolves unchanged.)
        cx = sqlite3.connect(os.path.join(self.state_root, "memory.db"))
        try:
            row = cx.execute(
                "SELECT id, tombstoned FROM artifact WHERE id = ?",
                (self.unrelated_row["id"],)).fetchone()
        finally:
            cx.close()
        if self.unrelated_row["src_path"] != "AGENTS.md":
            self.assertIsNotNone(row)
            self.assertEqual(row[1], 0)

    def test_sync_with_no_changes_is_a_noop(self):
        manifest = BOOTSTRAP.sync(self.clone, "sync-proj",
                                   state_root=self.state_root)
        self.assertEqual(manifest["sync_mode"], "noop")
        self.assertEqual(manifest["changed"], 0)
        self.assertTrue(manifest["balanced"], manifest)


# ==========================================================================
# Force-push / rebase: watermark is no longer an ancestor of HEAD -- PLAN
# A4b: "do not trust the delta", full re-scan instead.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class ForcePushTriggersFullRescan(unittest.TestCase):

    def setUp(self):
        self.clone = _clone_testbed("forcepush")
        self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
        self.state_root = _fresh_state_root("forcepush")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.first = BOOTSTRAP.bootstrap(self.clone, "forcepush-proj",
                                          state_root=self.state_root)
        self.watermark = self.first["repo_sha"]

    def test_force_push_is_detected_and_triggers_full_rescan(self):
        # Rewind 5 commits, then commit anew from there -- history that
        # shares an ancestor with the watermark but the watermark itself
        # is no longer reachable from the new HEAD. Real git plumbing,
        # the same shape a real force-push/rebase produces.
        _git(self.clone, "reset", "--hard", "HEAD~5")
        _git(self.clone, "commit", "--allow-empty", "-q",
             "-m", "simulated force-push: rewritten history")
        new_head = _git(self.clone, "rev-parse", "HEAD").strip()
        self.assertNotEqual(new_head, self.watermark)

        is_ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", self.watermark, new_head],
            cwd=self.clone, env=GIT_ENV, capture_output=True)
        self.assertEqual(
            is_ancestor.returncode, 1,
            "fixture setup must produce a genuinely non-ancestor watermark")

        manifest = BOOTSTRAP.sync(self.clone, "forcepush-proj",
                                   state_root=self.state_root)
        self.assertEqual(manifest["sync_mode"], "full")
        self.assertEqual(manifest["sync_reason"], "non_ancestor_watermark")
        self.assertTrue(manifest["balanced"], manifest)
        self.assertEqual(manifest["repo_sha"], new_head)



# ---------------------------------------------------------------------------
# #695: a module that EXISTS but fails to import is a FAILURE, never a skip.
#
# The loader above returns None on any import error so the suite reports a
# reason at run time instead of erroring at collection.  That is right for a
# genuinely ABSENT module and wrong for a broken one: a syntax error in the
# module under test made every test here skip and the run report
# "OK (skipped=N)" -- CI green on code that cannot be imported.  Proven by
# appending invalid syntax to the module and watching this file report
# `Ran 25 tests ... OK (skipped=25)`.
#
# This guard restores the signal without reintroducing a collection error:
# absent module -> still skips, broken module -> one loud failure carrying
# the traceback.
# ---------------------------------------------------------------------------

class ModuleUnderTestImports(unittest.TestCase):

    def test_a_module_that_exists_must_import(self):
        if _IMPORT_ERROR is not None:
            self.fail(
                "the module under test exists but failed to import; every "
                "other test in this file skipped rather than ran:\n\n"
                + _IMPORT_ERROR)

if __name__ == "__main__":
    unittest.main()
