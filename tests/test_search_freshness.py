"""D3 (#400 scale-testing) -- verify-on-read for `nightshift_memory_search.
search_memory()`, PLAN-400-memory-system.md's "Retrieval never serves an
unverified claim as fact" (A4b) enforced for every SEARCH result, not just
the single-id `verify_memory` path.

The defect this suite exists to prevent, exactly as scale-testing on the
testbed B testbed found it: `search_memory` used to hand back
`freshness: "unknown"` for every row whenever a caller omitted
`--repo-root` -- which is what every real caller through `nightshift-ctl`
did (the flag's own default was `None`), so in practice NO row was ever
verified by the retrieval path callers actually use. A stale or deleted
memory row could rank #1 and be handed to an agent with no signal that the
underlying source had moved. Fixed in `bin/nightshift_memory_search.py`
by (1) auto-resolving a repo root from the sidecar
`memory_bootstrap_manifest.json` bootstrap/sync already write, and (2)
verifying every returned, LIMIT-bounded row before it leaves the function --
never a separate opt-in call.

This file is the retrieval-side counterpart to `test_memory_freshness.py`
(which drives `verify_memory`/`verify_artifact` directly): every test here
goes through `search_memory()` itself, and the edit/delete tests
deliberately issue the SAME search TWICE with no `verify_memory` call in
between, to prove the flip happens on read, not on request.

Fixture discipline matches `test_memory_freshness.py` exactly and for the
same reason: every test that mutates a repo (`git commit`, `rm`) clones the
shared borrowed-project testbed into a private, throwaway directory first
(`_clone_testbed`, `--no-hardlinks`) and mutates ONLY the clone -- never
`/home/operator/testbeds/.nightshift-testbed/testbed A` itself, which
other suites (`test_zero_footprint.py` included) read concurrently.

H2 (WAL starvation, PLAN-400's own measured 33-43x blow-up from a lazily
iterated cursor) is `test_memory_wal.py`'s primary suite, and that
coverage is not duplicated wholesale here. What IS specific to this file:
`SearchFreshnessDoesNotReintroduceH2` runs the WAL-bound and
connection-closed checks against a search whose repo root is genuinely
RESOLVED (via a real bootstrap manifest), so the freshness work the D3 fix
adds -- real file reads for doc/structure rows, one batched `git
cat-file --batch` subprocess for commit rows -- actually executes on the
call under test. `test_memory_wal.py`'s own suite notes this gap in its
`SearchMemoryRespectsTheWalBound` docstrings are written against synthetic
rows with no bootstrap manifest, which take the cheap "unresolved ->
orphaned" shortcut and so never exercise that I/O at all.

Mockless throughout: real `git` subprocesses, a real SQLite `memory.db`
via `nightshift_memory.py`'s own writer, a real background
`threading.Thread` committing real `put()` calls for the H2 class, and (for
commit-row freshness) the real `git cat-file --batch` subprocess
`nightshift_memory_search._git_cat_file_batch` shells out to.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import traceback
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
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

# Same yardstick, and same calibration reasoning, as
# `test_memory_wal.py`'s `WAL_GROWTH_BOUND_BYTES` -- duplicated, not
# imported, per this suite's own established convention of self-contained
# test files (see that module's docstring). 2x SQLite's default
# `wal_autocheckpoint` threshold (1000 pages x 4096 bytes ~= 4.1MB):
# comfortably above where a correct, non-blocking implementation's WAL
# naturally oscillates, comfortably below where a genuinely blocked reader
# lands within a comparable window.
WAL_GROWTH_BOUND_BYTES = 8 * 1024 * 1024  # 8 MiB

WRITER_INTERVAL_S = 0.002
WRITER_WARMUP_S = 0.15
PROBE_INTERVAL_S = 0.02
CONSUME_ROWS = 50
CONSUME_DELAY_S = 0.01

# Words too common (or, for AND/OR/NOT/NEAR, syntactically special to
# FTS5's default query grammar) to make a reliable, single-token,
# literal-match query out of.
_FTS_STOPWORDS = {
    "and", "the", "not", "or", "near", "for", "are", "was", "you", "that",
    "with", "this", "from", "have", "will", "your",
}


_IMPORT_ERROR = None

def _load_module(path, name):
    """Import a `bin/*.py` script by path; return None (never raise)
    whether it doesn't exist yet or exists but fails to import -- the
    same technique `test_memory_freshness.py` and `test_memory_wal.py`
    use for their own sibling modules, so a concurrent build landing
    mid-edit is a clean skip here too, not a collection error.
    """
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


MEMORY = _load_module(MEMORY_MODULE_PATH, "nightshift_memory")
BOOTSTRAP = _load_module(BOOTSTRAP_MODULE_PATH, "nightshift_memory_bootstrap")
# SEARCH does `from nightshift_memory import MemoryStoreError` at ITS OWN
# module top level -- MEMORY must already be registered in sys.modules
# (true by this point) before this import is attempted.
SEARCH = _load_module(SEARCH_MODULE_PATH, "nightshift_memory_search")

_READY = MEMORY is not None and BOOTSTRAP is not None and SEARCH is not None
SKIP_REASON = (
    f"{MEMORY_MODULE_PATH}, {BOOTSTRAP_MODULE_PATH} and/or "
    f"{SEARCH_MODULE_PATH} not present (or not currently importable) -- "
    "PLAN-400 Track A. This suite is written against search_memory()'s "
    "documented D3 freshness-on-read contract.")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _git(repo, *args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} in {repo} failed: {proc.stderr}")
    return proc.stdout


def _clone_testbed(tag):
    """A private, mutable copy of the testbed -- never the testbed
    directory itself. See this module's docstring: `--no-hardlinks` so
    nothing done to the clone can ever reach back into the shared fixture
    other suites depend on.
    """
    if not TESTBED_A.exists():
        raise unittest.SkipTest(
            f"borrowed-project testbed not checked out at {TESTBED_A}")
    tmp = tempfile.mkdtemp(prefix=f"nightshift-searchfresh-clone-{tag}-")
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(TESTBED_A), tmp],
                    env=GIT_ENV, check=True, capture_output=True, timeout=60)
    _git(tmp, "config", "user.email", "fixture@example.com")
    _git(tmp, "config", "user.name", "Nightshift Search-Freshness Fixture")
    return tmp


def _fresh_state_root(tag):
    root = tempfile.mkdtemp(prefix=f"nightshift-searchfresh-state-{tag}-")
    os.chmod(root, 0o700)
    return root


def _row_by_kind(state_root, project, kind):
    cx = sqlite3.connect(os.path.join(state_root, "memory.db"))
    cx.row_factory = sqlite3.Row
    try:
        return cx.execute(
            "SELECT * FROM artifact WHERE project = ? AND kind = ? "
            "ORDER BY id LIMIT 1", (project, kind)).fetchone()
    finally:
        cx.close()


def _pick_query_term(body):
    """A short, FTS5-safe literal token drawn from `body` itself, so a
    query built from it is GUARANTEED to match the row it came from (the
    FTS index holds the body verbatim, and nothing in these tests ever
    re-syncs or re-indexes after the initial bootstrap -- editing or
    deleting the live file changes what freshness verification finds on
    disk, never what got indexed). Skips common words and the handful of
    tokens FTS5's default query grammar treats as operators.
    """
    for word in re.findall(r"[A-Za-z]{4,}", body):
        lower = word.lower()
        if lower not in _FTS_STOPWORDS:
            return lower
    return None


def _search(state_root, query, **overrides):
    args = types.SimpleNamespace(
        state_root=state_root, query=query, limit=overrides.pop("limit", 50))
    for key, value in overrides.items():
        setattr(args, key, value)
    return SEARCH.search_memory(args)


def _result_for_id(result, artifact_id):
    for row in result["results"]:
        if row["artifact_id"] == artifact_id:
            return row
    return None


# ==========================================================================
# Every returned row carries a real fresh|stale|orphaned status -- never
# the literal string "unknown" -- whether or not the caller passes
# --repo-root, and across every kind search_memory returns (doc, structure,
# commit).
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class SearchNeverReturnsUnknownFreshness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.clone = _clone_testbed("never-unknown")
        cls.state_root = _fresh_state_root("never-unknown")
        cls.manifest = BOOTSTRAP.bootstrap(
            cls.clone, "never-unknown-proj", state_root=cls.state_root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.clone, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_broad_mixed_kind_query_carries_no_unknown_freshness(self):
        """`"the"` is common enough in commit messages and markdown prose
        to pull back a real mix of kinds in one response -- confirmed
        against this fixture to return doc, structure, AND commit rows
        together, so this one assertion covers all three kinds the D3 fix
        touches (commit rows take the batched-git-verify path; doc/
        structure rows take the file-read path).
        """
        result = _search(self.state_root, "the", limit=100)
        self.assertGreater(result["count"], 0, "sanity: got real matches")
        kinds_seen = {row["kind"] for row in result["results"]}
        self.assertGreaterEqual(
            len(kinds_seen), 2,
            f"expected a mix of kinds from a broad query, only saw "
            f"{kinds_seen} -- fixture assumption may have drifted")

        for row in result["results"]:
            self.assertIn(
                row["freshness"], ("fresh", "stale", "orphaned"),
                f"row {row['artifact_id']} ({row['kind']}) carries an "
                f"unrecognised freshness value {row['freshness']!r}")
            self.assertNotEqual(
                row["freshness"], "unknown",
                "PLAN-400 A4b: retrieval never serves an unverified claim "
                "as fact -- 'unknown' must never reach a caller")

    def test_commit_rows_specifically_are_verified_not_unknown(self):
        """The trap the D3 fix names explicitly: a `kind='commit'` row's
        `src_path` is a synthetic `.git-log/<sha>` no working tree
        contains, so naive filesystem verification would turn every
        history row into a false `orphaned` (or, before this fix, a bare
        `unknown`) the instant verify-by-default landed. These must be
        genuinely `fresh` -- verified via `git`, not the filesystem --
        immediately after an untouched bootstrap.
        """
        result = _search(self.state_root, "the", kind="commit", limit=50)
        self.assertGreater(result["count"], 0, "sanity: got real commit rows")
        for row in result["results"]:
            self.assertEqual(row["kind"], "commit")
            self.assertEqual(
                row["freshness"], "fresh",
                f"untouched commit row {row['artifact_id']} did not verify "
                f"fresh: {row!r}")

    def test_freshness_is_populated_without_an_explicit_repo_root(self):
        """The exact regression: every real caller through `nightshift-ctl`
        omits `--repo-root`. Confirms auto-resolution via the sidecar
        `memory_bootstrap_manifest.json` actually engaged (not just that
        freshness happens to be populated some other way).
        """
        result = _search(self.state_root, "package", kind="structure", limit=50)
        self.assertGreater(result["count"], 0, "sanity: got structure rows")
        self.assertEqual(result["repo_root_source"], "bootstrap_manifest")
        self.assertEqual(result["repo_root"], self.clone)
        for row in result["results"]:
            self.assertNotEqual(row["freshness"], "unknown")

    def test_unresolvable_repo_root_reads_orphaned_never_a_guessed_fresh(self):
        """An explicit `--repo-root` always wins on WHICH root to try
        (`_resolve_repo_root`'s documented precedence -- `repo_root_source`
        stays `"argument"` even for a bogus path, since a caller pointing
        at a specific checkout on purpose is never second-guessed), but a
        path that resolves to nothing on disk still means there is nothing
        to verify against: every row must read `orphaned`, never a guessed
        `fresh`, and the call must not crash.
        """
        bogus = "/nonexistent/nightshift-search-freshness"
        result = _search(self.state_root, "the", limit=30, repo_root=bogus)
        self.assertGreater(result["count"], 0)
        self.assertEqual(result["repo_root_source"], "argument")
        self.assertEqual(result["repo_root"], bogus)
        for row in result["results"]:
            self.assertEqual(row["freshness"], "orphaned")
            self.assertIsNone(row["current_sha256"])


# ==========================================================================
# Edit -> stale, observed on the VERY NEXT search_memory() call -- no
# verify_memory()/verify_artifact() call anywhere in these tests.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class SearchFreshnessFlipsOnEditWithoutSeparateVerify(unittest.TestCase):

    def setUp(self):
        self.clone = _clone_testbed("edit")
        self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
        self.state_root = _fresh_state_root("edit")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        BOOTSTRAP.bootstrap(self.clone, "search-edit-proj",
                             state_root=self.state_root)

    def _assert_flips_to_stale(self, kind):
        row = _row_by_kind(self.state_root, "search-edit-proj", kind)
        self.assertIsNotNone(row, f"fixture must produce a {kind} row")
        term = _pick_query_term(row["body"])
        self.assertIsNotNone(term, f"could not derive a query term from "
                              f"{row['src_path']}'s body: {row['body']!r}")

        before = _search(self.state_root, term, kind=kind)
        before_row = _result_for_id(before, row["id"])
        self.assertIsNotNone(
            before_row, f"query {term!r} did not surface artifact "
            f"{row['id']} ({row['src_path']}) before the edit")
        self.assertEqual(before_row["freshness"], "fresh")

        # Edit line 1 of the row's OWN src_path -- guaranteed inside every
        # chunk's [line_start, line_end] range, since every chunk starts
        # at line 1 (same technique test_memory_freshness.py's
        # FreshnessAfterEdit uses, verified against this same testbed).
        target = os.path.join(self.clone, row["src_path"])
        with open(target, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        self.assertGreater(len(lines), 0)
        lines[0] = "NIGHTSHIFT SEARCH FRESHNESS TEST EDIT\n" + lines[0]
        with open(target, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        _git(self.clone, "add", "-A")
        _git(self.clone, "commit", "-q", "-m", "search freshness test edit")

        # THE SAME query, called again -- no bootstrap/sync re-run, no
        # verify_memory() call. Only the second search_memory() call
        # itself is what is expected to notice the source moved.
        after = _search(self.state_root, term, kind=kind)
        after_row = _result_for_id(after, row["id"])
        self.assertIsNotNone(
            after_row, f"artifact {row['id']} vanished from results after "
            f"the edit -- it must still be returned (with a flipped "
            f"freshness), not silently dropped")

        self.assertEqual(after_row["freshness"], "stale")
        self.assertNotEqual(before_row["current_sha256"],
                             after_row["current_sha256"])
        # Append-only: the STORED claim about repo_sha never changes
        # underneath a live re-verification.
        self.assertEqual(before_row["sha256"], after_row["sha256"])
        self.assertEqual(after_row["sha256"], row["sha256"])

    def test_doc_row_flips_to_stale_on_next_search(self):
        self._assert_flips_to_stale("doc")

    def test_structure_row_flips_to_stale_on_next_search(self):
        """D1's structure tier now stores a real first-line body (not the
        constant `body=""` the pre-fix cross-module defect produced), so
        it, too, must participate in verify-on-read -- not just the doc
        tier.
        """
        self._assert_flips_to_stale("structure")


# ==========================================================================
# Delete -> orphaned, same "no separate verify call" discipline.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class SearchFreshnessFlipsOnDeleteWithoutSeparateVerify(unittest.TestCase):

    def setUp(self):
        self.clone = _clone_testbed("delete")
        self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
        self.state_root = _fresh_state_root("delete")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        BOOTSTRAP.bootstrap(self.clone, "search-delete-proj",
                             state_root=self.state_root)

    def test_deleted_file_row_flips_to_orphaned_on_next_search(self):
        row = _row_by_kind(self.state_root, "search-delete-proj", "doc")
        self.assertIsNotNone(row, "fixture must produce a doc row")
        term = _pick_query_term(row["body"])
        self.assertIsNotNone(term)

        before = _search(self.state_root, term, kind="doc")
        before_row = _result_for_id(before, row["id"])
        self.assertIsNotNone(before_row)
        self.assertEqual(before_row["freshness"], "fresh")

        os.remove(os.path.join(self.clone, row["src_path"]))
        # Deliberately NOT committing the deletion -- verify_artifact
        # checks the WORKING TREE (os.path.isfile), not git status, so an
        # uncommitted delete is enough and keeps this test symmetric with
        # test_memory_freshness.py's FreshnessAfterDelete.

        after = _search(self.state_root, term, kind="doc")
        after_row = _result_for_id(after, row["id"])
        self.assertIsNotNone(
            after_row, "the row must still be RETURNED (its stored text "
            "is still a real search hit) with a flipped freshness, not "
            "silently dropped from results")
        self.assertEqual(after_row["freshness"], "orphaned")
        self.assertIsNone(after_row["current_sha256"])
        self.assertEqual(after_row["sha256"], row["sha256"])


# ==========================================================================
# H2 -- proving the D3 verify-on-read work does not reintroduce the WAL
# starvation idiom. See this module's docstring for why this is not a
# duplicate of test_memory_wal.py's own suite: this exercises a REALLY
# resolved repo_root, so the freshness file reads and the batched git
# subprocess actually run on the call under test, not the cheap
# unresolved -> orphaned shortcut.
# ==========================================================================

@unittest.skipUnless(_READY, SKIP_REASON)
class SearchFreshnessDoesNotReintroduceH2(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.clone = _clone_testbed("h2")
        cls.state_root = _fresh_state_root("h2")
        cls.manifest = BOOTSTRAP.bootstrap(
            cls.clone, "search-h2-proj", state_root=cls.state_root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.clone, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def _wal_bytes(self):
        wal_path = MEMORY.db_path(self.state_root) + "-wal"
        return os.path.getsize(wal_path) if os.path.exists(wal_path) else 0

    def _checkpoint_truncate(self):
        cx = sqlite3.connect(MEMORY.db_path(self.state_root), timeout=5,
                              isolation_level=None)
        try:
            return cx.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            cx.close()

    def _start_writer(self):
        stop = threading.Event()
        state = {"count": 0}

        def _run():
            cx = MEMORY.connect(self.state_root)
            try:
                n = 0
                while not stop.is_set():
                    MEMORY.put(
                        cx, project="search-h2-writer", kind="doc",
                        classification="internal", retention_class="standard",
                        ts=2000.0 + n, body=f"h2 writer filler row {n} quartzite",
                        src_path=f"h2-writer/{n}.md", line_start=1, line_end=1,
                    )
                    n += 1
                    time.sleep(WRITER_INTERVAL_S)
                state["count"] = n
            finally:
                cx.close()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(WRITER_WARMUP_S)
        return thread, stop

    def test_checkpoint_never_blocked_with_a_real_resolved_repo_root(self):
        """A continuous poller (not a single before/after snapshot -- see
        `test_memory_wal.py`'s `_CheckpointProbe` docstring for why a
        snapshot approach is known to miss this) checks
        `PRAGMA wal_checkpoint(TRUNCATE)` throughout the ENTIRE window: the
        `search_memory()` call itself, plus the caller's own downstream
        processing of the (already materialised) result list, with a real
        concurrent writer committing throughout. Because `repo_root` here
        resolves for real (via the bootstrap manifest, confirmed below),
        every returned row's freshness re-hash actually runs -- disk reads
        for doc/structure, one batched `git cat-file --batch` for commit
        rows -- so this is the D3 code path under WAL pressure, not the
        cheap unresolved shortcut.
        """
        writer_thread, writer_stop = self._start_writer()
        any_busy = [False]
        peak_bytes = [0]
        probe_stop = threading.Event()

        def _probe():
            while not probe_stop.is_set():
                peak_bytes[0] = max(peak_bytes[0], self._wal_bytes())
                busy, _log, _ckpt = self._checkpoint_truncate()
                if busy:
                    any_busy[0] = True
                time.sleep(PROBE_INTERVAL_S)

        probe_thread = threading.Thread(target=_probe, daemon=True)
        probe_thread.start()
        try:
            result = _search(self.state_root, "the", limit=CONSUME_ROWS + 20)
            self.assertGreater(result["count"], 0, "sanity: got real matches")
            self.assertEqual(result["repo_root_source"], "bootstrap_manifest")
            self.assertGreaterEqual(len(result["results"]), CONSUME_ROWS)

            # Downstream slow consumption of the MATERIALISED list -- no
            # database handle is touched here.
            for _row in result["results"][:CONSUME_ROWS]:
                time.sleep(CONSUME_DELAY_S)
        finally:
            probe_stop.set()
            probe_thread.join(timeout=10)
            writer_stop.set()
            writer_thread.join(timeout=10)

        self.assertFalse(
            any_busy[0],
            "a checkpoint was blocked at some point during (or immediately "
            "after) a search_memory() call that actually resolved a real "
            "repo_root -- something held memory.db's read transaction open "
            "across the D3 freshness-verification work, the H2 regression")
        self.assertLessEqual(
            peak_bytes[0], WAL_GROWTH_BOUND_BYTES,
            f"memory.db-wal peaked at {peak_bytes[0]} bytes during a "
            f"search_memory() call with real freshness verification -- "
            f"over the {WAL_GROWTH_BOUND_BYTES} byte bound")

    def test_read_only_connection_is_closed_before_freshness_work_runs(self):
        """Decisive, not inferential: capture the exact read-only `mode=ro`
        connection `search_memory()` opens and assert it raises
        `sqlite3.ProgrammingError` on any further use once the call has
        returned -- proving the connection was closed BEFORE the D3
        freshness work (file reads, the batched git subprocess) ran, not
        merely that WAL growth happened to stay low this time.
        """
        captured = []
        real_connect = sqlite3.connect

        def _spy_connect(*a, **kw):
            cx = real_connect(*a, **kw)
            database = a[0] if a else kw.get("database", "")
            if kw.get("uri") and "mode=ro" in str(database):
                captured.append(cx)
            return cx

        with unittest.mock.patch("sqlite3.connect", side_effect=_spy_connect):
            result = _search(self.state_root, "the", limit=30)
        self.assertGreater(result["count"], 0, "sanity: got real matches")
        self.assertEqual(result["repo_root_source"], "bootstrap_manifest")

        self.assertEqual(
            len(captured), 1,
            "expected exactly one read-only connection opened by "
            "search_memory()")
        with self.assertRaises(
                sqlite3.ProgrammingError,
                msg="search_memory()'s memory.db connection is still "
                    "usable after the call returned -- it was not closed "
                    "before the freshness-verification work ran, the H2 "
                    "regression this test exists to catch"):
            captured[0].execute("SELECT 1")



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
