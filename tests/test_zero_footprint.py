"""Issue #400 -- the zero-footprint guarantee (PLAN-400-memory-system.md
sec 1 and sec 4's own named test): "Nightshift writes nothing into
[the borrowed project] -- no `.omni/`, no branch, no ref, no commit, no
`.git` mutation. Enforced by the zero-footprint test, not by care."

This is the one file in this suite that runs bootstrap/sync/search/verify
directly against the shared testbed directory itself
(`/home/operator/testbeds/.nightshift-testbed/testbed A`), never a
clone -- because the property under test is specifically "did operating on
the real thing touch the real thing", which a clone cannot answer.
`test_memory_bootstrap.py` and `test_memory_freshness.py` clone precisely
because they need to MUTATE a repo; this file needs the opposite guarantee
and only reads.

Three independent before/after checks, matching PLAN-400 sec 4's own
named triplet exactly ("Compare `git status --porcelain`, `git rev-parse
HEAD`, and a `find -newer` sweep before/after"), plus a fourth (plain
sorted file listing) for redundancy against a tool-specific blind spot in
any one of them:

  1. `git status --porcelain`  -- catches any tracked-file modification,
     any new untracked file, any staged change.
  2. `git rev-parse HEAD`      -- catches a commit, a checkout, a reset --
     anything that would move the ref.
  3. `find <repo> -newer <marker>` -- catches ANY file under the repo
     (`.git` internals included) whose mtime moved forward, even one
     `git status` itself would not surface (e.g. `.git/index`'s stat
     cache being opportunistically rewritten by a read command -- exactly
     why `nightshift_memory_bootstrap.py`'s own `GIT_ENV` sets
     `GIT_OPTIONAL_LOCKS=0`, disabling precisely that kind of
     read-triggered write).
  4. `find <repo> -type f | sort` -- a plain path listing, independent
     confirmation that no file was added or removed.

Why this is expected to hold at all: every git command
`nightshift_memory_bootstrap.py` runs (`ls-files`, `rev-parse --verify`,
`log`, `diff --name-only <sha>..<sha>`, `merge-base --is-ancestor`) reads
committed objects and refs, never the working tree's mutable state, and
never writes one. `nightshift_memory_search.py` never shells out to git at
all -- it reads `memory.db` and, for `verify_memory`, opens a source file
with a plain `open(..., "rb")`, never in a write mode.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
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
    """See test_memory_bootstrap.py's module docstring for the full
    rationale -- duplicated here per this suite's self-contained-file
    convention. Additive only: a real `put_memory`/`MemoryError` from a
    future integration fix is never overwritten."""
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
SEARCH = _load_module(SEARCH_MODULE_PATH, "nightshift_memory_search")

SKIP_REASON = (
    f"{MEMORY_MODULE_PATH}, {BOOTSTRAP_MODULE_PATH} and/or "
    f"{SEARCH_MODULE_PATH} not present (or not currently importable) -- "
    "PLAN-400 Track A, worktree-isolated build.")
_READY = BOOTSTRAP is not None and SEARCH is not None


def _git(*args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=str(TESTBED_A), env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout


def _file_listing():
    proc = subprocess.run(["find", str(TESTBED_A), "-type", "f"],
                           capture_output=True, text=True, timeout=60,
                           check=True)
    return sorted(proc.stdout.splitlines())


def _fresh_state_root(tag):
    root = tempfile.mkdtemp(prefix=f"nightshift-footprint-{tag}-")
    os.chmod(root, 0o700)
    return root


@unittest.skipUnless(_READY, SKIP_REASON)
class ZeroFootprintAgainstTheBorrowedTestbed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not TESTBED_A.exists():
            raise unittest.SkipTest(
                f"borrowed-project testbed not checked out at {TESTBED_A}")

    def test_full_bootstrap_sync_search_verify_cycle_leaves_no_trace(self):
        # `git status`/`git rev-parse` are measurement, not mutation, but
        # `git status` itself is empirically confirmed (locally, on this
        # checkout) to rewrite `.git/index`'s stat cache on EVERY
        # invocation, which bumps `.git`'s own directory mtime -- a
        # measurement artifact, not a Nightshift footprint. So the
        # before/after git snapshots run OUTSIDE the marker-bounded
        # window entirely (before-snapshot precedes marker creation,
        # after-snapshot follows the `find -newer` sweep) -- only
        # bootstrap/sync/search/verify sit inside that window, and only
        # THEIR footprint (if any) can appear in the sweep.
        status_before = _git("status", "--porcelain")
        head_before = _git("rev-parse", "HEAD").strip()
        listing_before = _file_listing()

        marker_dir = tempfile.mkdtemp(prefix="nightshift-footprint-marker-")
        self.addCleanup(shutil.rmtree, marker_dir, ignore_errors=True)
        marker = os.path.join(marker_dir, "marker")
        with open(marker, "w", encoding="utf-8"):
            pass
        time.sleep(0.3)  # safety margin ahead of the mtime comparison

        state_root = _fresh_state_root("cycle")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)

        manifest = BOOTSTRAP.bootstrap(str(TESTBED_A), "footprint-proj",
                                        state_root=state_root)
        self.assertTrue(manifest["balanced"], manifest)

        sync_manifest = BOOTSTRAP.sync(str(TESTBED_A), "footprint-proj",
                                        state_root=state_root)
        self.assertEqual(sync_manifest["sync_mode"], "noop")

        search_args = types.SimpleNamespace(
            state_root=state_root, query="testbed A", limit=5)
        search_result = SEARCH.search_memory(search_args)
        self.assertTrue(search_result["ok"])
        self.assertEqual(search_result["trust"], "untrusted")

        # verify_memory, with repo_root pointed at the testbed itself --
        # the one operation in this whole suite most likely to touch a
        # source file's access pattern, since it opens and reads it.
        import sqlite3
        cx = sqlite3.connect(os.path.join(state_root, "memory.db"))
        artifact_id = cx.execute(
            "SELECT id FROM artifact WHERE project = ? LIMIT 1",
            ("footprint-proj",)).fetchone()[0]
        cx.close()
        verify_args = types.SimpleNamespace(
            state_root=state_root, repo_root=str(TESTBED_A), id=artifact_id)
        verify_result = SEARCH.verify_memory(verify_args)
        self.assertIn(verify_result["status"], ("fresh", "stale", "orphaned"))

        find_newer = subprocess.run(
            ["find", str(TESTBED_A), "-newer", marker],
            capture_output=True, text=True, timeout=60, check=True)
        touched = [ln for ln in find_newer.stdout.splitlines() if ln]

        # These run AFTER the sweep on purpose (see the comment above) --
        # their own `.git/index` housekeeping must never be mistaken for
        # a footprint left by the memory operations under test.
        status_after = _git("status", "--porcelain")
        head_after = _git("rev-parse", "HEAD").strip()
        listing_after = _file_listing()

        self.assertEqual(
            status_before, status_after,
            "git status --porcelain changed -- the testbed was mutated")
        self.assertEqual(
            head_before, head_after,
            "HEAD moved -- a commit, checkout or reset touched the testbed")
        self.assertEqual(
            listing_before, listing_after,
            "the testbed's file listing changed -- a file was added or "
            "removed")
        self.assertEqual(
            touched, [],
            "find -newer <pre-run marker> found files touched under the "
            f"testbed during the cycle: {touched!r}")

    def test_bare_bootstrap_call_alone_leaves_no_trace(self):
        """Isolates bootstrap() specifically (no sync/search/verify) --
        so a footprint introduced by bootstrap() alone is not masked by,
        or confused with, one from a later step in the combined cycle
        above."""
        head_before = _git("rev-parse", "HEAD").strip()
        status_before = _git("status", "--porcelain")

        state_root = _fresh_state_root("bootstrap-only")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        BOOTSTRAP.bootstrap(str(TESTBED_A), "footprint-solo-proj",
                             state_root=state_root)

        self.assertEqual(_git("status", "--porcelain"), status_before)
        self.assertEqual(_git("rev-parse", "HEAD").strip(), head_before)


# ---------------------------------------------------------------------------
# #400 round-1 verifier blocker: `test_400_acceptance_conditions.
# ACCEPTANCE_INDEX` originally pinned AC1's `proven_by` to
# `ZeroFootprintAgainstTheBorrowedTestbed` above alone. That class's own
# `setUpClass` raises `unittest.SkipTest` when `TESTBED_A` -- a
# machine-local, gitignored checkout OUTSIDE this worktree -- is absent,
# which is every CI runner and every machine but the one that testbed was
# cloned on. INDEX-CANNOT-LIE's auditor (`_run_named_test`) scores any
# non-green outcome, skip included, as `unexecuted_claim`: a legal skip
# elsewhere in the suite became a hard `test_the_real_acceptance_index_is_
# honest` failure the moment AC1 cited it, on every machine without that
# directory. That is exactly what #698 forbids -- a `proven_by` test must
# not read state git does not carry.
#
# This class proves the SAME "did operating on the real thing touch the
# real thing" property (see the module docstring above) against a
# throwaway repo it builds itself -- same one-commit `git init` fixture
# shape `test_memory_bootstrap.py`'s `_write_fixture` and
# `test_project_contract.py` already use for their own synthetic cases --
# so the proof is guaranteed to exist and run green everywhere, CI
# included, with no dependency on any pre-existing local checkout.
# `ZeroFootprintAgainstTheBorrowedTestbed` stays exactly as it is (never
# weakened, never deleted -- rule 7): opportunistic extra coverage against
# real-world content on whatever machine happens to have the testbed. AC1
# is now pinned to THIS class instead.
# ---------------------------------------------------------------------------

def _git_at(repo, *args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=str(repo), env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout


def _file_listing_at(repo):
    proc = subprocess.run(["find", str(repo), "-type", "f"],
                           capture_output=True, text=True, timeout=60,
                           check=True)
    return sorted(proc.stdout.splitlines())


def _write_zero_footprint_fixture():
    """A small, real, one-commit throwaway git repo, built fresh in a
    tempdir -- deliberately NOT a clone (see the module docstring: the
    property under test is "did operating on the real thing touch the
    real thing", which a clone cannot answer). A freshly `git init`'d
    repo IS the real thing, directly, from the moment it is created --
    it answers the same question a borrowed checkout does, without
    requiring one to exist on disk beforehand.
    """
    root = tempfile.mkdtemp(prefix="nightshift-footprint-fixture-")
    files = {
        "README.md": "# footprint fixture\n\nA throwaway repo for AC1.\n",
        "src/main.go": "package main\n\nfunc main() {}\n",
        "docs/notes.md": "Notes about the zero-footprint property.\n",
    }
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for cmd in (["init", "-q"], ["config", "user.email", "fixture@example.com"],
                ["config", "user.name", "Nightshift Footprint Fixture"],
                ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        _git_at(root, *cmd)
    return root


@unittest.skipUnless(_READY, SKIP_REASON)
class ZeroFootprintAgainstADeterministicFixture(unittest.TestCase):

    def setUp(self):
        self.repo = _write_zero_footprint_fixture()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def test_full_bootstrap_sync_search_verify_cycle_leaves_no_trace(self):
        # Same measurement-artifact reasoning as the borrowed-testbed
        # twin above: before/after git snapshots sit OUTSIDE the
        # marker-bounded window so `git status`/`rev-parse`'s own
        # `.git/index` stat-cache housekeeping is never mistaken for a
        # footprint left by the memory operations under test.
        status_before = _git_at(self.repo, "status", "--porcelain")
        head_before = _git_at(self.repo, "rev-parse", "HEAD").strip()
        listing_before = _file_listing_at(self.repo)

        marker_dir = tempfile.mkdtemp(prefix="nightshift-footprint-marker-")
        self.addCleanup(shutil.rmtree, marker_dir, ignore_errors=True)
        marker = os.path.join(marker_dir, "marker")
        with open(marker, "w", encoding="utf-8"):
            pass
        time.sleep(0.3)  # safety margin ahead of the mtime comparison

        state_root = _fresh_state_root("deterministic-cycle")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)

        manifest = BOOTSTRAP.bootstrap(self.repo, "footprint-proj",
                                        state_root=state_root)
        self.assertTrue(manifest["balanced"], manifest)

        sync_manifest = BOOTSTRAP.sync(self.repo, "footprint-proj",
                                        state_root=state_root)
        self.assertEqual(sync_manifest["sync_mode"], "noop")

        search_args = types.SimpleNamespace(
            state_root=state_root, query="footprint", limit=5)
        search_result = SEARCH.search_memory(search_args)
        self.assertTrue(search_result["ok"])
        self.assertEqual(search_result["trust"], "untrusted")

        # verify_memory, with repo_root pointed at the fixture itself --
        # the one operation in this whole suite most likely to touch a
        # source file's access pattern, since it opens and reads it.
        import sqlite3
        cx = sqlite3.connect(os.path.join(state_root, "memory.db"))
        artifact_id = cx.execute(
            "SELECT id FROM artifact WHERE project = ? LIMIT 1",
            ("footprint-proj",)).fetchone()[0]
        cx.close()
        verify_args = types.SimpleNamespace(
            state_root=state_root, repo_root=self.repo, id=artifact_id)
        verify_result = SEARCH.verify_memory(verify_args)
        self.assertIn(verify_result["status"], ("fresh", "stale", "orphaned"))

        find_newer = subprocess.run(
            ["find", self.repo, "-newer", marker],
            capture_output=True, text=True, timeout=60, check=True)
        touched = [ln for ln in find_newer.stdout.splitlines() if ln]

        # These run AFTER the sweep on purpose (see the comment above).
        status_after = _git_at(self.repo, "status", "--porcelain")
        head_after = _git_at(self.repo, "rev-parse", "HEAD").strip()
        listing_after = _file_listing_at(self.repo)

        self.assertEqual(
            status_before, status_after,
            "git status --porcelain changed -- the fixture was mutated")
        self.assertEqual(
            head_before, head_after,
            "HEAD moved -- a commit, checkout or reset touched the fixture")
        self.assertEqual(
            listing_before, listing_after,
            "the fixture's file listing changed -- a file was added or "
            "removed")
        self.assertEqual(
            touched, [],
            "find -newer <pre-run marker> found files touched under the "
            f"fixture during the cycle: {touched!r}")



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
