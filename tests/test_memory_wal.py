"""Issue #400 -- narrative memory, H2 WAL starvation (PLAN-400 Track A, A3/A4).

The defect these tests exist to prevent: a lazily-iterated cursor --
`for row in cur:` with slow per-row work in the loop, the canonical RAG
idiom of "fetch matches, then call a model on each one" -- keeps its
backing SQLite read transaction open for as long as the loop runs. In WAL
mode that single live reader blocks every checkpoint (automatic or manual)
for the duration, so ordinary concurrent write traffic that would otherwise
self-checkpoint back down instead just keeps appending to `memory.db-wal`
with nothing able to reclaim it. PLAN-400 cites a measured 33-43x blow-up
(95MB, 138MB) from exactly this idiom, with "NO pragma able to bound it" --
the fix is not a setting, it is retrieval code that `fetchall()`s and
closes its connection BEFORE doing anything slow with the rows.

`bin/nightshift_memory_search.py`'s `search_memory()` is the module that
owns this obligation (`bin/nightshift_memory.py`'s own docstring says so
explicitly: "this module never exposes a bulk cursor, only single-row
get()" -- H2 is retrieval's job, not the writer's). This suite is split
into two halves for exactly that reason:

  * `LazyCursorReproducesH2Directly` needs only the writer module
    (`nightshift_memory.py`, `connect()`/`put()`) plus a hand-rolled raw
    connection that deliberately reproduces the bad idiom named above. It
    has no dependency on `nightshift_memory_search.py` at all, so it runs
    whenever the writer has landed -- its job is to prove the bound this
    suite checks against is not vacuous: something that actually holds a
    cursor open across a slow loop actually breaches it, with a real,
    concurrently-writing `memory.db`, not a mock.

  * `SearchMemoryRespectsTheWalBound` calls the real `search_memory()` and
    checks it stays on the right side of that same bound. Same graceful-
    skip convention `tests/test_project_contract.py` and
    `tests/test_memory_writer.py` use for their sibling modules: this class
    skips cleanly (not a collection error) if `nightshift_memory_search.py`
    hasn't landed, or fails to import.

    AS OF THIS WRITING that second condition is live, not hypothetical:
    `nightshift_memory_search.py` does `from nightshift_memory import
    MemoryError`, but `nightshift_memory.py` defines `MemoryStoreError`,
    never `MemoryError` -- confirmed by direct import against both files as
    they currently sit in this repo's own `bin/`. That is a cross-module
    integration defect between two concurrently-built PLAN-400 Track A
    pieces, not something this test-writing task owns fixing (each module
    is a different track's deliverable) -- it is called out here, loudly,
    because it means `SearchMemoryRespectsTheWalBound` currently SKIPS
    rather than exercising the real code, and whoever reconciles the two
    modules should know this suite is waiting to turn on the moment
    `search_memory()` becomes importable.

CALIBRATION, not guesswork: `WAL_GROWTH_BOUND_BYTES` was picked by actually
running both the good (`fetchall()` + close) and bad (`for row in cur:`)
idioms against the real `nightshift_memory.py` writer, under real
concurrent write load, and observing where each one lands --
`LazyCursorReproducesH2Directly`'s docstrings below report the measured
numbers. The bound sits at 2x SQLite's own default `wal_autocheckpoint`
threshold (1000 pages x 4096 bytes ~= 4.1MB): comfortably above where a
correct implementation's WAL naturally oscillates (it self-checkpoints at
that threshold the instant nothing blocks it, repeatedly, never
accumulating past one cycle's worth of overshoot), and comfortably below
where a blocked reader's WAL lands within the same ~1.5s window (measured
16-26MB across repeated runs, 4-6x over the bound) -- so the two cases do
not overlap even accounting for a slower or more loaded machine.

Mockless throughout: every assertion is against a real `sqlite3` connection
to a real `memory.db` on disk, with a real background `threading.Thread`
issuing real `nightshift_memory.put()` commits concurrently -- concurrency
is exactly the variable H2 depends on (a lazily-held reader only starves
the WAL when there is real write pressure behind it to starve).
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
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
SEARCH_MODULE_PATH = BIN / "nightshift_memory_search.py"

# Same yardstick for both halves of the suite (see module docstring's
# CALIBRATION note) -- the bad-idiom class proves this bound is breachable
# at all; the search-module class proves the real `search_memory()` stays
# under it. A single shared constant makes that "same yardstick" claim
# literal rather than two independently-tuned numbers that happen to agree.
WAL_GROWTH_BOUND_BYTES = 8 * 1024 * 1024  # 8 MiB

# Wall-clock shape of the "large result set with a delay inside the loop"
# scenario: 100 rows at 15ms/row is close to a second and a half, long
# enough for a blocked reader's WAL to clearly separate from the bound
# (measured 16-26MB, see above) without making the suite slow.
CONSUME_ROWS = 100
CONSUME_DELAY_S = 0.015

# Background writer cadence: fast enough that a blocked window accumulates
# real pressure, slow enough not to starve the reader thread's own CPU time
# on a loaded machine.
WRITER_INTERVAL_S = 0.002
WRITER_WARMUP_S = 0.15

# Issue #727. How much write pressure the blocked-reader case waits for,
# counted in COMMITS rather than in seconds.
#
# The wall-clock version failed twice on CI in one day (peak WAL 5.5MB and
# 6.8MB against an 8MiB bound) while passing on an idle machine. It was not
# measuring the property it names: it ran a writer for ~1.5s and asserted how
# far the WAL grew, which measures how much CPU the writer won. A negative
# control that only fails when the machine is fast enough is not a control --
# on a contended runner it produces the same green a correct implementation
# would.
#
# Commits are what WAL bytes are a function of, so the reader waits for a
# fixed number of them and a slow machine simply takes longer. Measured, not
# guessed: 400 commits under an open cursor cleared the bound with margin on
# an idle machine AND on one pinned to a single core against three spinners.
WRITER_COMMITS_FOR_BREACH = 400
WRITER_PRESSURE_TIMEOUT_S = 120.0


_IMPORT_ERROR = None

def _load_module(name, path):
    """Import a `bin/*.py` script by path, degrading to None (never
    raising) whether the file doesn't exist yet or exists but fails to
    import -- the same technique `tests/test_project_contract.py` and
    `tests/test_memory_writer.py` use for their own sibling modules, so a
    concurrent Track A build landing mid-edit is a clean skip here too, not
    a collection error.
    """
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- "not ready" is not a failure
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {path} failed to import: {exc}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


MEMORY = _load_module("nightshift_memory", MEMORY_MODULE_PATH)
SEARCH = _load_module("nightshift_memory_search", SEARCH_MODULE_PATH)

MEMORY_SKIP_REASON = (
    f"{MEMORY_MODULE_PATH} not implemented (or not currently importable) -- "
    "#400 Track A, A1 (PLAN-400-memory-system.md).")
SEARCH_SKIP_REASON = (
    f"{SEARCH_MODULE_PATH} not implemented (or not currently importable) -- "
    "#400 Track A, A3 (PLAN-400-memory-system.md). As of this suite being "
    "written, the module fails to import: it does `from nightshift_memory "
    "import MemoryError`, but nightshift_memory.py defines "
    "MemoryStoreError, never MemoryError. See this file's module "
    "docstring.")


def _state_root(prefix):
    """A fresh, private state_root `nightshift_memory.connect()` will
    accept -- `tempfile.mkdtemp()` is documented to create its directory at
    mode 0700 regardless of umask, exactly the precondition
    `_validate_state_root` enforces. Same fixture `test_memory_writer.py`
    uses."""
    path = tempfile.mkdtemp(prefix=prefix)
    assert oct(os.stat(path).st_mode)[-3:] == "700", path
    return path


def _wal_bytes(state_root):
    wal_path = MEMORY.db_path(state_root) + "-wal"
    return os.path.getsize(wal_path) if os.path.exists(wal_path) else 0


def _checkpoint_truncate(state_root):
    """Issue `PRAGMA wal_checkpoint(TRUNCATE)` from a FRESH, short-lived
    connection -- deliberately not reusing any connection a test already
    holds, so this probe never itself becomes the reader it is trying to
    measure around. Returns (busy, log_frames, checkpointed_frames), SQLite's
    own three-column reply to this pragma.
    """
    cx = sqlite3.connect(MEMORY.db_path(state_root), timeout=5, isolation_level=None)
    try:
        return cx.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        cx.close()


def _populate_baseline(state_root, project, count, term):
    """Insert `count` real artifact rows (via the writer module's own
    `put()`, never raw SQL) whose body includes `term`, so a caller can
    later retrieve "a large result set" by searching for it. Checkpoints
    immediately afterward so every test's timed window starts from a known,
    near-zero WAL baseline instead of whatever the population burst itself
    left behind.
    """
    cx = MEMORY.connect(state_root)
    try:
        for i in range(count):
            MEMORY.put(
                cx, project=project, kind="doc", classification="internal",
                retention_class="standard", ts=float(i),
                body=f"baseline artifact {i} {term} filler narrative text",
                src_path=f"baseline/{i}.md", line_start=1, line_end=1,
            )
    finally:
        cx.close()
    _checkpoint_truncate(state_root)


class _ConcurrentWriter:
    """A real background thread committing real `put()` transactions
    against `memory.db` until stopped -- the concurrent write pressure H2
    needs to be observable at all (PLAN-400: without it, even a cursor held
    open all day produces no growth to measure, because there is nothing
    trying to checkpoint in the first place). Runs on its OWN connection,
    opened inside the thread, since sqlite3 connections are not meant to be
    shared across threads.
    """

    def __init__(self, state_root, project, *, interval=WRITER_INTERVAL_S):
        self.state_root = state_root
        self.project = project
        self.interval = interval
        self.count = 0
        # Live, readable from the reader thread while the writer runs. `count`
        # is only set at stop(), too late for anything that needs to WAIT for
        # a given amount of write pressure (issue #727).
        self.committed = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        time.sleep(WRITER_WARMUP_S)  # let the writer get a few commits in
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)

    def _run(self):
        cx = MEMORY.connect(self.state_root)
        try:
            n = 0
            while not self._stop.is_set():
                MEMORY.put(
                    cx, project=self.project, kind="doc",
                    classification="internal", retention_class="standard",
                    ts=1000.0 + n, body=f"writer filler row {n} zephyrquartz",
                    src_path=f"writer/{n}.md", line_start=1, line_end=1,
                )
                n += 1
                self.committed = n
                time.sleep(self.interval)
            self.count = n
        finally:
            cx.close()


class _CheckpointProbe:
    """Polls `PRAGMA wal_checkpoint(TRUNCATE)` at a fixed interval from a
    background thread for as long as it runs, recording whether ANY poll
    saw `busy != 0` and the largest `memory.db-wal` size observed.

    Why polling, not a single before/after snapshot: a first cut of this
    suite checked checkpoint-busy and WAL size only once, right after
    `search_memory()` returned. Verified against a deliberately regressed
    `search_memory()` (a lazy `for row in cur:` with a per-row delay,
    exactly the idiom this suite exists to catch) that snapshot approach
    PASSED when it should have failed -- by the time the single
    post-return sample ran, the background writer's own very next commit
    had already triggered its own passive auto-checkpoint (nothing was
    blocking it any more) and silently reclaimed the evidence. Continuous
    polling for the ENTIRE duration a call under test is in flight closes
    that gap: it catches a blocked checkpoint wherever in the call it
    happens, not only at one snapshot instant chosen from outside the call.
    """

    def __init__(self, state_root, interval=0.02):
        self.state_root = state_root
        self.interval = interval
        self.any_busy = False
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)
        return self

    def _run(self):
        while not self._stop.is_set():
            self.peak_bytes = max(self.peak_bytes, _wal_bytes(self.state_root))
            busy, _log, _ckpt = _checkpoint_truncate(self.state_root)
            if busy:
                self.any_busy = True
            time.sleep(self.interval)


@unittest.skipUnless(MEMORY is not None, MEMORY_SKIP_REASON)
class LazyCursorReproducesH2Directly(unittest.TestCase):
    """Reproduces the exact bad idiom PLAN-400 names -- `for row in cur:`
    with slow work in the loop -- directly against a raw `sqlite3`
    connection, with no dependency on `nightshift_memory_search.py`. This
    class's job is not to test the writer module (that is
    `test_memory_writer.py`'s job); it is to prove the checks
    `SearchMemoryRespectsTheWalBound` applies to the real retrieval module
    are not toothless -- something that actually gets H2 wrong actually
    trips them.
    """

    def setUp(self):
        self.state_root = _state_root("nightshift-wal-bad-")
        _populate_baseline(self.state_root, "wal-bad", 200, "zephyrquartz")

    def test_holding_a_cursor_open_blocks_a_concurrent_checkpoint(self):
        """The mechanism, isolated: the instant a SELECT's cursor is opened
        (its first row fetched) and not yet exhausted, `PRAGMA
        wal_checkpoint(TRUNCATE)` -- issued from an entirely separate
        connection -- reports busy. This is the same pragma+mode
        `nightshift_memory.py`'s own `purge()` uses to reclaim space, so
        this is the literal mechanism by which a lazily-held cursor
        anywhere in the process denies reclaiming ANY of memory.db's WAL,
        not just the rows that cursor itself is reading.
        """
        writer = _ConcurrentWriter(self.state_root, "wal-bad").start()
        try:
            con = sqlite3.connect(
                MEMORY.db_path(self.state_root), timeout=5, isolation_level=None)
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT id, body FROM artifact WHERE project = ? ORDER BY id",
                ("wal-bad",))
            first = cur.fetchone()  # opens, and holds, the read transaction
            self.assertIsNotNone(first)

            busy, _log, _ckpt = _checkpoint_truncate(self.state_root)
            self.assertNotEqual(
                busy, 0,
                "a checkpoint issued while a cursor is open mid-iteration "
                "must be BLOCKED -- if it succeeds, the read transaction "
                "was not actually held open, and this test is not "
                "exercising the H2 idiom at all")

            cur.close()
            con.close()
        finally:
            writer.stop()

    def test_lazy_iteration_over_a_large_result_set_breaches_the_wal_bound(self):
        """The full scenario: iterate the large baseline result set with a
        per-row delay -- the canonical slow-model-call-per-row RAG shape --
        while a concurrent writer keeps committing. The read transaction
        stays open for the ENTIRE loop, so every one of the writer's
        commits during that window is denied its own automatic checkpoint.

        Issue #727: the window is counted in the writer's COMMITS, not in
        seconds, because the wall-clock version measured how much CPU the
        writer won rather than what holding a cursor open costs.

        Measured against this exact scenario (writer module, real sqlite3,
        400 commits held under an open cursor): peak WAL lands well over
        WAL_GROWTH_BOUND_BYTES
        (8MB), which is itself already 2x the ~4.1MB ceiling a correct,
        non-blocking implementation's WAL naturally self-corrects under
        (SQLite's default wal_autocheckpoint = 1000 pages). This is the
        "must fail against an implementation holding a cursor open" case:
        the same bound `SearchMemoryRespectsTheWalBound` requires the real
        `search_memory()` to stay under is breached here, on purpose, to
        prove that requirement is a real one.
        """
        writer = _ConcurrentWriter(self.state_root, "wal-bad").start()
        try:
            con = sqlite3.connect(
                MEMORY.db_path(self.state_root), timeout=5, isolation_level=None)
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT id, body FROM artifact WHERE project = ? ORDER BY id",
                ("wal-bad",))

            peak = _wal_bytes(self.state_root)
            seen = 0
            started = writer.committed
            deadline = time.monotonic() + WRITER_PRESSURE_TIMEOUT_S
            for _row in cur:  # <-- the H2 idiom: cursor stays open across...
                time.sleep(CONSUME_DELAY_S)  # ...this "slow per-row work"
                peak = max(peak, _wal_bytes(self.state_root))
                seen += 1
                if seen >= CONSUME_ROWS:
                    break
            # Issue #727: the cursor STAYS OPEN here -- the whole point of the
            # idiom -- until the writer has landed a fixed number of commits.
            # Stopping at `seen >= CONSUME_ROWS` alone made the assertion below
            # a measurement of the machine.
            while writer.committed - started < WRITER_COMMITS_FOR_BREACH:
                if time.monotonic() > deadline:
                    self.fail(
                        "the background writer produced only "
                        f"{writer.committed - started} commits in "
                        f"{WRITER_PRESSURE_TIMEOUT_S:.0f}s -- it is stuck or "
                        "starved, a real fixture problem that must not be "
                        "read as a WAL-growth result")
                time.sleep(CONSUME_DELAY_S)
                peak = max(peak, _wal_bytes(self.state_root))
            committed = writer.committed - started
            cur.close()
            con.close()
        finally:
            writer.stop()

        self.assertGreaterEqual(seen, CONSUME_ROWS)
        self.assertGreaterEqual(committed, WRITER_COMMITS_FOR_BREACH)
        self.assertGreater(
            peak, WAL_GROWTH_BOUND_BYTES,
            f"expected the blocked-reader idiom to breach the "
            f"{WAL_GROWTH_BOUND_BYTES} byte bound (peak WAL was only "
            f"{peak} bytes) after {committed} writer commits under an open "
            f"cursor. The load is counted in commits now, not seconds, so "
            f"this can no longer mean the machine was slow (issue #727) -- it "
            f"means WAL growth is bounded by something other than the reader "
            f"holding its read transaction open, which needs investigating, "
            f"not loosening this bound")


@unittest.skipUnless(
    MEMORY is not None and SEARCH is not None,
    SEARCH_SKIP_REASON if MEMORY is not None else MEMORY_SKIP_REASON)
class SearchMemoryRespectsTheWalBound(unittest.TestCase):
    """`search_memory()` itself, under the identical concurrent-write
    pressure `LazyCursorReproducesH2Directly` used to breach the bound.
    `nightshift_memory_search.py`'s own docstring states the obligation
    this class checks: "`search_memory` and `verify_memory` both
    `fetchall()`/`fetchone()` immediately and close the connection BEFORE
    any formatting, hashing, or freshness work happens."
    """

    def setUp(self):
        self.state_root = _state_root("nightshift-wal-good-")
        _populate_baseline(self.state_root, "wal-good", 200, "zephyrquartz")

    def _search_args(self, **overrides):
        args = types.SimpleNamespace(
            state_root=self.state_root, query="zephyrquartz",
            limit=CONSUME_ROWS + 50)
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_search_memory_never_blocks_a_checkpoint_for_the_call_or_after(self):
        """A `_CheckpointProbe` polls throughout the ENTIRE window -- the
        `search_memory()` call itself, plus the caller's own downstream
        slow processing of its (already-materialised) result list
        afterward -- with a real writer committing throughout. Two
        assertions, matching the task's two named checks directly:

          * `probe.any_busy` must stay False: no poll, at any point in that
            window, ever found a checkpoint blocked. This is what a
            before/after snapshot cannot guarantee (see `_CheckpointProbe`'s
            own docstring for the regression that exposed the gap) --
            continuous polling is what makes "search_memory never leaves a
            reader open, at any point" a claim this test actually backs.
          * `probe.peak_bytes` must stay at or under `WAL_GROWTH_BOUND_BYTES`
            -- the identical bound `LazyCursorReproducesH2Directly` proves
            breachable, so "search_memory keeps memory.db-wal bounded" and
            "the bad idiom does not" are checked against one shared
            yardstick, not two independently-tuned numbers.
        """
        writer = _ConcurrentWriter(self.state_root, "wal-good").start()
        probe = _CheckpointProbe(self.state_root).start()
        try:
            result = SEARCH.search_memory(self._search_args())
            rows = result["results"]
            self.assertGreater(result["count"], 0, "sanity: got real matches")
            self.assertGreaterEqual(len(rows), CONSUME_ROWS)

            # Downstream slow consumption of the MATERIALISED list -- no
            # database handle is touched here at all. If this were instead
            # `for row in cur:` against a live cursor, that would be a
            # second, independent way to reintroduce H2; this loop proves
            # that is not what is happening once search_memory() has
            # already returned.
            for _row in rows[:CONSUME_ROWS]:
                time.sleep(CONSUME_DELAY_S)
        finally:
            probe.stop()
            writer.stop()

        self.assertFalse(
            probe.any_busy,
            "a checkpoint was blocked at some point during (or immediately "
            "after) the search_memory() call -- something held a memory.db "
            "read transaction open across it, the H2 regression")
        self.assertLessEqual(
            probe.peak_bytes, WAL_GROWTH_BOUND_BYTES,
            f"memory.db-wal peaked at {probe.peak_bytes} bytes during a "
            f"search_memory() call and its caller's downstream processing "
            f"-- over the {WAL_GROWTH_BOUND_BYTES} byte bound the blocked-"
            f"reader idiom is required to breach in "
            f"LazyCursorReproducesH2Directly")

    def test_search_memory_closes_its_connection_before_returning(self):
        """A DECISIVE, query-shape-independent check of the same H2
        discipline the test above checks indirectly through WAL side
        effects -- added because, measured directly, those side effects do
        NOT manifest for search_memory()'s actual query shape (FTS5 MATCH
        + `bm25(...)` + `ORDER BY score` + `LIMIT`):

            con = sqlite3.connect(db_path, ...)
            cur = con.execute(<the exact SQL _build_search_sql produces>)
            cur.fetchone()                     # "opens" the cursor
            PRAGMA wal_checkpoint(TRUNCATE) from ANOTHER connection
            # -> busy = 0, even though `cur` is still open and
            #    mid-iteration -- confirmed by hand against this exact
            #    query shape. The same probe against the simpler
            #    `ORDER BY id` query LazyCursorReproducesH2Directly uses
            #    DOES report busy=1 in the identical situation: SQLite
            #    fully materialises an ORDER BY-on-an-expression result
            #    into a temp sorter before returning row 1, releasing the
            #    read snapshot over `artifact`/`artifact_fts` early. So a
            #    lazily-iterating regression of search_memory() would
            #    never trip `probe.any_busy` or breach `WAL_GROWTH_BOUND_
            #    BYTES` above, for this query shape, REGARDLESS of how the
            #    surrounding Python code behaves -- those two checks alone
            #    cannot tell a correct implementation from a regressed one
            #    here, so this method checks the actual required
            #    invariant directly instead of inferring it: is the
            #    sqlite3.Connection search_memory() opened for this call
            #    still usable after the call returns? A `close()`d
            #    connection raises `ProgrammingError` on any further use;
            #    one still open does not. See
            #    `SearchMemoryConnectionClosedCheckHasTeeth` below for
            #    proof this actually catches a lazy-iterate regression.
            """
        captured = []
        real_connect = sqlite3.connect

        def _spy_connect(*a, **kw):
            cx = real_connect(*a, **kw)
            # Only `_open_ro`'s read-only URI connection matches this
            # shape (`uri=True`, a "file:...?mode=ro" string) -- the
            # background `_ConcurrentWriter`'s own `MEMORY.connect()`
            # calls `sqlite3.connect(path, ...)` positionally with no
            # `uri=True`, so this filter never captures, or interferes
            # with, the writer thread's own connections.
            database = a[0] if a else kw.get("database", "")
            if kw.get("uri") and "mode=ro" in str(database):
                captured.append(cx)
            return cx

        writer = _ConcurrentWriter(self.state_root, "wal-good-closed").start()
        try:
            with unittest.mock.patch("sqlite3.connect", side_effect=_spy_connect):
                result = SEARCH.search_memory(self._search_args())
            self.assertGreater(result["count"], 0, "sanity: got real matches")
        finally:
            writer.stop()

        self.assertEqual(
            len(captured), 1,
            "expected exactly one read-only connection to be opened by "
            "search_memory() -- got a different count, so this probe is "
            "not observing what it thinks it is")
        with self.assertRaises(
                sqlite3.ProgrammingError,
                msg="search_memory()'s own memory.db connection is still "
                    "usable after the call returned -- it was not closed "
                    "before formatting/returning results, the H2 "
                    "regression this test exists to catch"):
            captured[0].execute("SELECT 1")


@unittest.skipUnless(
    MEMORY is not None and SEARCH is not None,
    SEARCH_SKIP_REASON if MEMORY is not None else MEMORY_SKIP_REASON)
class SearchMemoryConnectionClosedCheckHasTeeth(unittest.TestCase):
    """Not a test of the real `search_memory()` -- a standalone
    demonstration, built from `nightshift_memory_search`'s own
    query-building internals (`_build_search_sql`, `_open_ro`,
    `_memory_db_path`) so the SQL shape is IDENTICAL to what
    `search_memory()` itself runs, that `test_search_memory_closes_its_
    connection_before_returning` above actually has teeth: something that
    gets H2 wrong -- holds the cursor open and iterates it lazily instead
    of `fetchall()`ing and closing immediately -- is caught by "is the
    connection still open", deterministically, for THIS query shape, even
    though (see the test above) WAL growth and checkpoint-busy are not.
    """

    def setUp(self):
        self.state_root = _state_root("nightshift-wal-teeth-")
        _populate_baseline(self.state_root, "wal-teeth", 50, "zephyrquartz")

    def test_a_lazy_iterating_regression_is_still_open_mid_loop(self):
        db_path = SEARCH._memory_db_path(self.state_root)
        con = SEARCH._open_ro(db_path)
        sql, params = SEARCH._build_search_sql("zephyrquartz", None, False, 20)
        cur = con.execute(sql, params)
        first = cur.fetchone()  # <-- the H2 idiom: opens, but does not
        self.assertIsNotNone(first)  # exhaust or close, the cursor

        # The exact defect this suite exists to catch: a connection an
        # implementation is still lazily iterating over remains fully
        # usable -- proving "closed immediately" is a real, checkable
        # distinction a regressed implementation actually fails, not a
        # tautology that would pass no matter what the code under test does.
        con.execute("SELECT 1")  # must NOT raise -- still genuinely open

        cur.close()
        con.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")  # NOW closed, as the real
                                      # search_memory() must already be by
                                      # the time it returns to its caller



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
