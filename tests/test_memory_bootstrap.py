"""Issue #400 A4a -- narrative-memory onboarding (PLAN-400-memory-system.md
Track A, `bin/nightshift_memory_bootstrap.py`'s `bootstrap()`/`sync()`).

The defect this suite exists to prevent: a borrowed-project onboarding that
LOOKS complete but silently missed files, double-counted a re-run, lost
progress on a crash, or let a second project's rows leak into the first
one's memory.db. PLAN-400 A4a's own words: "Correctness is provable, not
asserted" -- `indexed + skipped + failed == tracked` must hold exactly or
`bootstrap()` fails loudly (`BootstrapError('coverage_manifest_unbalanced')`).
This suite proves the arithmetic actually balances against a REAL borrowed
repo, not merely that the internal check exists.

Fixture: `/home/operator/testbeds/.nightshift-testbed/testbed A` -- the
isolated, zero-remote testbed copy (PLAN-400 sec 1: Go-primary polyglot, 856
tracked files / 9.2 MB, deliberately unlike this repo). NEVER the owner's
real company repo. Bootstrap/sync only ever run read-only git plumbing
(`ls-files`, `rev-parse`, `log`, `diff --name-only`, `merge-base
--is-ancestor` -- see `nightshift_memory_bootstrap.py`'s own module comment)
against `repo`, so running them against the testbed directly is safe; tests
that need to MUTATE a repo (kill-mid-run aside, which only needs read
access) create their own throwaway `git init` fixture instead, exactly as
`tests/test_project_contract.py` already does for its synthetic cases.

KNOWN INTEGRATION SEAM -- read before assuming a failure here is this
suite's bug. `nightshift_memory_bootstrap.py` was built in worktree
isolation against an ASSUMED writer contract (its own module docstring):

    from nightshift_memory import put_memory
    put_memory(args) -> dict

The `nightshift_memory.py` actually delivered (also worktree-isolated, same
PLAN-400 A1/A4a fan-out) exposes `put(cx, *, project, kind, ...)` -- a
connection-taking function, not `put_memory(args)`. `nightshift_memory_
search.py` (A3) separately imports `from nightshift_memory import
MemoryError`, but the delivered module defines `MemoryStoreError`, not
`MemoryError` (deliberately -- see that module's own docstring: "the latter
shadows a Python builtin"). Both gaps are exactly what each module's own
docstring already flags as the other track's-to-resolve seam ("only
`_load_writer` and `_write_artifact` ... need to change" / "needs
cross-checking at integration"), not something this test file invented.

`_bridge_writer_contract` below closes both gaps with the narrowest
possible adapter -- ONLY if the real names are absent, so a future
integration fix that adds real `put_memory`/`MemoryError` makes this a
no-op rather than silently shadowing the real implementation. Without this
bridge, `bootstrap()`/`sync()` raise `BootstrapError('memory_writer_
unavailable')` before a single one of this suite's own assertions about
coverage arithmetic, idempotency, resume or isolation could even run.
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
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MEMORY_MODULE_PATH = BIN / "nightshift_memory.py"
BOOTSTRAP_MODULE_PATH = BIN / "nightshift_memory_bootstrap.py"

TESTBED_A = Path(
    "/home/operator/testbeds/.nightshift-testbed/testbed A")

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")


_IMPORT_ERROR = None

def _load_module(path, name):
    """Import a loose bin/ script by path -- same technique
    `test_project_contract.py`'s `_load_contract_module()` uses for
    `nightshift_project_contract.py`. Registers into `sys.modules` under
    `name` (not just returned) so a sibling module's own top-level or
    lazy `from <name> import X` resolves to THIS loaded copy rather than
    trying (and failing) a real package import.

    Degrades to None -- never raises -- both when the file doesn't exist
    yet and when it exists but fails to import: Track A landing mid-edit
    under a concurrent build agent is expected, not a defect in this file.
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


def _bridge_writer_contract(memory_module, *, delay=0.0):
    """See the module docstring's KNOWN INTEGRATION SEAM. Idempotent and
    additive-only: never overwrites a `put_memory`/`MemoryError` that
    genuinely exists, so this shrinks to nothing the moment integration
    supplies the real names.

    `delay`, when nonzero, sleeps before every write -- used only by the
    kill-mid-bootstrap fixture below to make a crash-resume window
    reliably observable without depending on real-world write speed
    racing a `kill -9`.
    """
    if memory_module is None:
        return
    if not hasattr(memory_module, "put_memory"):
        def _put_memory_adapter(args, _mem=memory_module):
            if delay:
                time.sleep(delay)
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

# D3's own module (A3 retrieval/freshness), loaded the same optional,
# never-raises way -- used only by the D1/D2 regression guard below that
# needs `verify_artifact` (CONFIRMED CROSS-MODULE DEFECT #2: a truncated
# stored body reads `stale` forever). Same KNOWN INTEGRATION SEAM as
# MEMORY/BOOTSTRAP above: absent or not-yet-importable just skips the
# one class that needs it, same as `test_memory_freshness.py` does.
SEARCH_MODULE_PATH = BIN / "nightshift_memory_search.py"
SEARCH = _load_module(SEARCH_MODULE_PATH, "nightshift_memory_search")

SKIP_REASON = (
    f"{MEMORY_MODULE_PATH} and/or {BOOTSTRAP_MODULE_PATH} not present (or "
    "not currently importable) -- PLAN-400 Track A, worktree-isolated "
    "build. This suite is written against bootstrap()/sync()'s documented "
    "interface.")

SEARCH_SKIP_REASON = (
    f"{SEARCH_MODULE_PATH} not present, not importable, or missing "
    "verify_artifact -- same KNOWN INTEGRATION SEAM as SKIP_REASON above, "
    "D3's own worktree-isolated track.")


def _fresh_state_root(tag):
    """A private, 0700, euid-owned directory -- what `nightshift_memory.
    connect()`'s `_validate_state_root` requires before it will place
    memory.db inside it (ADR-0021)."""
    root = tempfile.mkdtemp(prefix=f"nightshift-memtest-{tag}-")
    os.chmod(root, 0o700)
    return root


def _artifact_rows(state_root, *, project=None):
    cx = sqlite3.connect(os.path.join(state_root, "memory.db"))
    try:
        if project is None:
            return cx.execute("SELECT id, project, src_path, kind, "
                               "tombstoned FROM artifact").fetchall()
        return cx.execute(
            "SELECT id, project, src_path, kind, tombstoned FROM artifact "
            "WHERE project = ?", (project,)).fetchall()
    finally:
        cx.close()


def _git(repo, *args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout


def _write_fixture(files):
    """A small, real, one-commit git repo -- same shape as
    `test_project_contract.py`'s `_write_fixture`, kept local to this file
    per this suite's own convention of not sharing test-only helpers
    across files.
    """
    root = tempfile.mkdtemp(prefix="nightshift-bootstrap-fixture-")
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for cmd in (["init", "-q"], ["config", "user.email", "fixture@example.com"],
                ["config", "user.name", "Nightshift Bootstrap Fixture"],
                ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        _git(root, *cmd)
    return root


def _write_fixture_with_commits(files, extra_commit_count):
    """Like `_write_fixture` above, plus `extra_commit_count` additional
    real, empty commits on top -- for the D2 (tier-3 history) tests
    below, which need a KNOWN total commit count; `_write_fixture` alone
    always produces exactly one.
    """
    root = _write_fixture(files)
    for i in range(extra_commit_count):
        _git(root, "commit", "-q", "--allow-empty", "-m", f"extra commit {i}")
    return root


# ==========================================================================
# Coverage arithmetic, generated-dir exclusion, tier-3 commit history --
# all against the real testbed, one bootstrap run shared by the class
# (mirrors test_project_contract.py's `cls.contract = detect(TESTBED_A)`).
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class BootstrapCoverageOnTestbedA(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not TESTBED_A.exists():
            raise unittest.SkipTest(
                f"borrowed-project testbed not checked out at {TESTBED_A}")
        cls.state_root = _fresh_state_root("coverage")
        # protos/ carries 4 files (2 hand-written .proto, 2 generated
        # .pb.go) -- real content on the testbed, not synthetic. The
        # #443 project_contract is what would normally supply this
        # reason in production (PLAN-400: "pass them via generated_dirs=
        # at the call site"); this suite supplies it directly to prove
        # the exclusion mechanism itself, independent of Track B.
        cls.reason = ("generated: hand-written .proto next to generated "
                       ".pb.go bindings")
        cls.manifest = BOOTSTRAP.bootstrap(
            str(TESTBED_A), "coverage-proj", state_root=cls.state_root,
            generated_dirs={"protos": cls.reason})
        cls.tracked_ground_truth = sorted(
            p for p in _git(TESTBED_A, "ls-files").splitlines() if p)
        cls.commit_ground_truth = int(
            _git(TESTBED_A, "rev-list", "--count", "HEAD").strip())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_tracked_count_matches_independent_git_ls_files(self):
        """The manifest's own `tracked` figure must agree with `git
        ls-files` run independently by this test, not merely with
        whatever bootstrap() itself believes -- otherwise a bug that
        under- or over-counts tracked files would be invisible to a
        check that only re-derives the same number the same way.
        """
        self.assertEqual(self.manifest["tracked"],
                          len(self.tracked_ground_truth))

    def test_coverage_arithmetic_balances_exactly(self):
        """PLAN-400 A4a: indexed + skipped + failed == tracked, exactly --
        the difference between "memory was built" and "memory is
        correct". `bootstrap()` itself raises loudly on imbalance, but
        this asserts the RETURNED manifest's own numbers foot, in case a
        future edit changes what gets returned without changing what
        gets raised.
        """
        self.assertTrue(self.manifest["balanced"], self.manifest)
        total = (self.manifest["indexed"]["count"]
                  + self.manifest["skipped"]["count"]
                  + self.manifest["failed"]["count"])
        self.assertEqual(total, self.manifest["tracked"])
        self.assertEqual(self.manifest["failed"]["count"], 0, self.manifest)

    def test_generated_dir_excluded_with_recorded_reason(self):
        """PLAN-400 A4a: "Explicit exclusions, recorded not assumed." All
        4 real protos/ files must be skipped, each carrying the exact
        reason this test supplied -- never blank, never silently dropped
        from the skip bucket into failed or indexed.
        """
        protos_files = [p for p in self.tracked_ground_truth
                         if p.startswith("protos/")]
        self.assertEqual(len(protos_files), 4, protos_files)
        skipped_by_path = {
            item["path"]: item["reason"]
            for item in self.manifest["skipped"]["items"]
        }
        for path in protos_files:
            self.assertIn(path, skipped_by_path)
            self.assertEqual(skipped_by_path[path], self.reason)

    def test_commit_history_tier3_indexed(self):
        """PLAN A4a tier 3: "commit messages -- where the WHY actually
        lives." Every one of the testbed's real commits should land,
        supplementary to (not counted inside) the per-file balance.
        """
        self.assertEqual(self.manifest["commits_indexed"],
                          self.commit_ground_truth)

    def test_repo_sha_pins_to_real_head(self):
        head = _git(TESTBED_A, "rev-parse", "HEAD").strip()
        self.assertEqual(self.manifest["repo_sha"], head)

    def test_bounded_by_tracked_bytes_not_working_tree_or_dot_git(self):
        """PLAN-400 sec 1: 9.2 MB tracked inside a working tree/`.git`
        that (on the real testbed A this testbed is cloned from) run
        into the GB range. A design that walked disk instead of `git
        ls-files` would be orders of magnitude slower -- wall-clock is
        the cheapest externally observable proxy, generous enough not to
        flake, tight enough to catch a full-tree walk.
        """
        timing_root = _fresh_state_root("coverage-timing")
        self.addCleanup(shutil.rmtree, timing_root, ignore_errors=True)
        started = time.monotonic()
        BOOTSTRAP.bootstrap(
            str(TESTBED_A), "coverage-timing-proj", state_root=timing_root)
        self.assertLess(time.monotonic() - started, 30.0)

    def test_manifest_is_plain_json_data(self):
        """A coverage manifest a seat/operator reads must be plain data,
        not an object a caller needs special knowledge to unpack."""
        json.dumps(self.manifest)


# ==========================================================================
# Idempotency: PLAN-400 A4a's "row identity ... means a re-run inserts
# nothing new" -- and A1's own `put()` docstring: idempotent by
# (project, src_path, line_start, sha256).
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class BootstrapRerunIsIdempotent(unittest.TestCase):

    def setUp(self):
        if not TESTBED_A.exists():
            self.skipTest(
                f"borrowed-project testbed not checked out at {TESTBED_A}")
        self.state_root = _fresh_state_root("rerun")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)

    def test_rerun_inserts_zero_new_rows(self):
        first = BOOTSTRAP.bootstrap(str(TESTBED_A), "rerun-proj",
                                     state_root=self.state_root)
        self.assertTrue(first["balanced"], first)
        rows_after_first = _artifact_rows(self.state_root)

        second = BOOTSTRAP.bootstrap(str(TESTBED_A), "rerun-proj",
                                      state_root=self.state_root)
        rows_after_second = _artifact_rows(self.state_root)

        self.assertTrue(second["balanced"], second)
        self.assertEqual(second["tracked"], first["tracked"])
        self.assertEqual(len(rows_after_second), len(rows_after_first))
        # Not just the same COUNT -- the exact same set of ids, proving
        # the second pass truly wrote nothing (not "deleted 3, added 3").
        self.assertEqual({r[0] for r in rows_after_second},
                          {r[0] for r in rows_after_first})


# ==========================================================================
# Crash-resume: PLAN-400 A4a "a crash mid-bootstrap resumes from the
# watermark rather than starting over." A real SIGKILL against a real
# subprocess, not a controlled early return -- the resume path only
# matters if it survives an actual crash.
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class BootstrapCrashResume(unittest.TestCase):

    DRIVER_TEMPLATE = """
import sys, time
sys.path.insert(0, {bin!r})
import nightshift_memory as nm

def _put_memory_adapter(args, _mem=nm):
    time.sleep({delay!r})
    cx = _mem.connect(args.state_root)
    try:
        ts = getattr(args, "ts", None)
        if ts is None:
            ts = time.time()
        return _mem.put(
            cx, project=args.project, kind=args.kind,
            classification=args.classification,
            retention_class=args.retention_class, body=args.body, ts=ts,
            src_path=args.src_path, line_start=args.line_start,
            line_end=args.line_end, repo_sha=args.repo_sha,
            key_version=args.key_version, derived_from=args.derived_from,
            refs=args.refs,
        )
    finally:
        cx.close()

if not hasattr(nm, "put_memory"):
    nm.put_memory = _put_memory_adapter
else:
    _orig_put_memory = nm.put_memory
    def _slowed(args, _orig=_orig_put_memory):
        time.sleep({delay!r})
        return _orig(args)
    nm.put_memory = _slowed
if not hasattr(nm, "MemoryError"):
    nm.MemoryError = nm.MemoryStoreError

import nightshift_memory_bootstrap as bs
bs.bootstrap({repo!r}, {project!r}, state_root={state_root!r},
             flush_every={flush_every!r})
"""

    def setUp(self):
        if not TESTBED_A.exists():
            self.skipTest(
                f"borrowed-project testbed not checked out at {TESTBED_A}")
        self.state_root = _fresh_state_root("kill")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)

    def test_kill_mid_bootstrap_then_resume_completes(self):
        driver_path = os.path.join(self.state_root, "_driver.py")
        with open(driver_path, "w", encoding="utf-8") as fh:
            fh.write(self.DRIVER_TEMPLATE.format(
                bin=str(BIN), delay=0.02, repo=str(TESTBED_A),
                project="kill-proj", state_root=self.state_root,
                flush_every=5))

        proc = subprocess.Popen([sys.executable, driver_path],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        state_path = os.path.join(self.state_root,
                                   "memory_bootstrap_state.json")
        deadline = time.monotonic() + 20.0
        cursor_seen = 0
        try:
            while time.monotonic() < deadline:
                if os.path.exists(state_path):
                    try:
                        with open(state_path, encoding="utf-8") as fh:
                            state = json.load(fh)
                    except ValueError:
                        state = None
                    if (state and state.get("status") == "in_progress"
                            and state.get("cursor", 0) > 0):
                        cursor_seen = state["cursor"]
                        break
                time.sleep(0.02)
        finally:
            # SIGKILL, not terminate(): the resume path must survive a
            # real crash (no atexit/signal handler runs), not a clean
            # shutdown -- that is the entire property under test.
            proc.kill()
            proc.wait(timeout=10)

        self.assertGreater(
            cursor_seen, 0,
            "subprocess never reported in-progress state before the "
            "20s deadline -- kill-mid-bootstrap could not be exercised")

        with open(state_path, encoding="utf-8") as fh:
            crashed_state = json.load(fh)
        self.assertEqual(crashed_state["status"], "in_progress")
        self.assertLess(
            crashed_state["cursor"], crashed_state.get("cursor", 0) + 856,
            "sanity: cursor field present")
        rows_after_crash = _artifact_rows(self.state_root, project="kill-proj")
        self.assertGreater(len(rows_after_crash), 0)

        # Resume in-process, no artificial delay -- this must pick up
        # from the crashed cursor and finish, not restart from zero.
        resumed = BOOTSTRAP.bootstrap(str(TESTBED_A), "kill-proj",
                                       state_root=self.state_root)
        self.assertTrue(resumed["balanced"], resumed)

        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        self.assertEqual(final_state["status"], "complete")

        # Reference: an uninterrupted bootstrap of the same repo into a
        # fresh project must land the exact same indexed-by-tier counts
        # as the crashed-then-resumed one -- proving resume did not
        # under- or over-count relative to a clean run.
        clean_root = _fresh_state_root("kill-reference")
        try:
            clean = BOOTSTRAP.bootstrap(str(TESTBED_A), "kill-reference-proj",
                                         state_root=clean_root)
        finally:
            shutil.rmtree(clean_root, ignore_errors=True)
        self.assertEqual(resumed["indexed"]["by_tier"],
                          clean["indexed"]["by_tier"])
        self.assertEqual(resumed["tracked"], clean["tracked"])


# ==========================================================================
# Per-project physical isolation: PLAN-400's central architectural claim
# -- ONE memory.db per project, so cross-project leakage is
# unrepresentable rather than merely policy-enforced. A small synthetic
# fixture (not the 856-file testbed) keeps two REAL, concurrent bootstrap
# runs fast and deterministic.
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class BootstrapTwoProjectsNeverShareARow(unittest.TestCase):

    def setUp(self):
        self.repo = _write_fixture({
            "README.md": "# fixture\n\nshared content both projects index.\n",
            "docs/notes.md": "design notes for the fixture repo.\n",
            "src/main.go": "package main\n\nfunc main() {}\n",
            "src/helper.go": "package main\n\nfunc helper() int { return 1 }\n",
        })
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.root_a = _fresh_state_root("iso-a")
        self.root_b = _fresh_state_root("iso-b")
        self.addCleanup(shutil.rmtree, self.root_a, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root_b, ignore_errors=True)

    def test_two_projects_bound_simultaneously_never_share_a_row(self):
        results = {}
        errors = {}

        def _run(tag, project, state_root):
            try:
                results[tag] = BOOTSTRAP.bootstrap(
                    self.repo, project, state_root=state_root)
            except Exception as exc:  # noqa: BLE001 -- surfaced via errors
                errors[tag] = exc

        t_a = threading.Thread(target=_run, args=("a", "alpha", self.root_a))
        t_b = threading.Thread(target=_run, args=("b", "beta", self.root_b))
        t_a.start()
        t_b.start()
        t_a.join(timeout=30)
        t_b.join(timeout=30)

        self.assertEqual(errors, {}, errors)
        self.assertTrue(results["a"]["balanced"], results["a"])
        self.assertTrue(results["b"]["balanced"], results["b"])

        db_a = os.path.join(self.root_a, "memory.db")
        db_b = os.path.join(self.root_b, "memory.db")
        self.assertNotEqual(db_a, db_b)
        self.assertTrue(os.path.isfile(db_a))
        self.assertTrue(os.path.isfile(db_b))

        rows_a = _artifact_rows(self.root_a)
        rows_b = _artifact_rows(self.root_b)
        self.assertGreater(len(rows_a), 0)
        self.assertGreater(len(rows_b), 0)
        self.assertEqual({r[1] for r in rows_a}, {"alpha"})
        self.assertEqual({r[1] for r in rows_b}, {"beta"})
        # Same fixture repo indexed under both projects -- symmetric
        # outcome is the expected, uncontaminated result, not a
        # coincidence: neither run should have seen the other's data.
        self.assertEqual(len(rows_a), len(rows_b))
        self.assertEqual({r[2] for r in rows_a}, {r[2] for r in rows_b})


# ==========================================================================
# D1/D2 fix verification -- before this section, `grep -nE 'degrad|budget'
# tests/test_memory_bootstrap.py` returned NOTHING: the degrade branch
# had zero test coverage, which is exactly why it shipped shedding the
# wrong tier. Synthetic fixtures throughout (not the 856/55,235-file
# testbeds) so `tracked_bytes_budget` / `doc_tier_bytes_budget` /
# `structure_tier_bytes_budget` / `max_commits` can be forced to tiny
# values and every degrade/cap branch exercised cheaply and
# deterministically, independent of either real testbed's actual size.
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class DegradeShedsByValueNotByTier(unittest.TestCase):
    """The exact inversion of the pre-fix defect -- measured on the real
    testbed B testbed as `by_tier {"doc": 0, "structure": 54119}` and
    `commits_indexed: 0` despite 1,613 real doc files and 2,000 real
    commits, with the manifest's own "downgraded" list left empty the
    whole time. A tiny synthetic repo reproduces the same shape: force
    `degraded=True` via a near-zero `tracked_bytes_budget` (independent
    of the fixture's real size) and assert docs and history both still
    land, not silently vanish.
    """

    def setUp(self):
        self.repo = _write_fixture_with_commits({
            "README.md": "# fixture\n\ndoc tier content.\n" * 5,
            "docs/adr-0001.md": "# ADR 0001\n\ndecision record.\n" * 5,
            "src/main.go": "package main\n\nfunc main() {}\n",
        }, extra_commit_count=3)
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state_root = _fresh_state_root("degrade-value")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.commit_ground_truth = int(
            _git(self.repo, "rev-list", "--count", "HEAD").strip())
        # tracked_bytes_budget=1 forces degraded=True regardless of how
        # tiny this fixture actually is -- the same lever a real
        # 591 MB-tracked repo pulls against the 200 MB default.
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "degrade-proj", state_root=self.state_root,
            tracked_bytes_budget=1)

    def test_degraded_flag_is_set(self):
        self.assertTrue(self.manifest["budget"]["degraded"], self.manifest)

    def test_docs_are_indexed_as_doc_tier_not_flattened(self):
        self.assertGreater(
            self.manifest["indexed"]["by_tier"]["doc"], 0, self.manifest)

    def test_history_still_indexed_when_degraded(self):
        self.assertEqual(self.manifest["commits_indexed"],
                          self.commit_ground_truth)
        self.assertEqual(self.manifest["history"]["indexed"],
                          self.commit_ground_truth)
        self.assertIsNone(self.manifest["history"]["reason"])

    def test_coverage_balances_exactly(self):
        self.assertTrue(self.manifest["balanced"], self.manifest)
        total = (self.manifest["indexed"]["count"]
                  + self.manifest["skipped"]["count"]
                  + self.manifest["failed"]["count"])
        self.assertEqual(total, self.manifest["tracked"])

    def test_rerun_is_idempotent_under_degrade(self):
        rows_after_first = _artifact_rows(self.state_root,
                                           project="degrade-proj")
        second = BOOTSTRAP.bootstrap(
            self.repo, "degrade-proj", state_root=self.state_root,
            tracked_bytes_budget=1)
        rows_after_second = _artifact_rows(self.state_root,
                                            project="degrade-proj")
        self.assertEqual(second["indexed"]["by_tier"],
                          self.manifest["indexed"]["by_tier"])
        self.assertEqual(len(rows_after_first), len(rows_after_second))


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class DocBudgetShedsLargestFirst(unittest.TestCase):
    """When even the doc tier's OWN independent budget is exceeded (not
    merely the overall tracked-bytes budget), the largest docs are shed
    to a tier-2 pointer first, smallest-first-kept, and every shed path
    is recorded in `manifest["downgraded"]` with reason
    "doc_budget_exceeded" -- PLAN A4a's "recorded not assumed". No real
    testbed comes anywhere near exercising this branch (testbed B's real
    doc tier measured at 10.33 MB against a 64 MB default budget), so
    this is otherwise untested by construction.
    """

    def setUp(self):
        self.repo = _write_fixture({
            "small.md": "small doc.\n" * 5,                    # ~55 B
            "medium.md": "medium doc line.\n" * 200,           # ~3.6 KB
            "big.md": "large doc content line.\n" * 2000,      # ~48 KB
            "src/main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state_root = _fresh_state_root("doc-budget-shed")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        # A 4 KB doc-tier budget fits small.md + medium.md but not
        # big.md -- one deterministic shed candidate, smallest-first.
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "doc-budget-proj", state_root=self.state_root,
            tracked_bytes_budget=1, doc_tier_bytes_budget=4000)

    def test_largest_doc_shed_smallest_docs_kept(self):
        downgraded_paths = {d["path"] for d in self.manifest["downgraded"]}
        self.assertIn("big.md", downgraded_paths, self.manifest)
        self.assertNotIn("small.md", downgraded_paths, self.manifest)
        self.assertNotIn("medium.md", downgraded_paths, self.manifest)
        reasons = {d["path"]: d["reason"]
                   for d in self.manifest["downgraded"]}
        self.assertEqual(reasons["big.md"], "doc_budget_exceeded")

    def test_coverage_still_balances(self):
        self.assertTrue(self.manifest["balanced"], self.manifest)


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class StructureFirstLineCapSkipsHugeSingleLinePointers(unittest.TestCase):
    """Applies UNCONDITIONALLY, not only when degraded -- measured on
    testbed B: 247 structure-tier files are single lines over 1 MB
    (minified JS, an ML model's JSON) and together account for 87.8% of
    ALL structure-tier stored bytes. A synthetic single 5,000-byte line
    (no trailing newline) with a small explicit cap reproduces the same
    shape cheaply, on an otherwise tiny, never-degraded fixture.
    """

    def setUp(self):
        self.repo = _write_fixture({
            "src/normal.go": "package main\n\nfunc main() {}\n",
            "src/blob.min.js": "x" * 5000,  # one line, no "\n"
        })
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state_root = _fresh_state_root("structure-cap")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "structure-cap-proj", state_root=self.state_root,
            structure_max_first_line_bytes=100)

    def test_huge_single_line_skipped_not_truncated(self):
        skipped_by_path = {
            item["path"]: item["reason"]
            for item in self.manifest["skipped"]["items"]
        }
        self.assertIn("src/blob.min.js", skipped_by_path, self.manifest)
        self.assertTrue(
            skipped_by_path["src/blob.min.js"].startswith(
                "structure_first_line_too_large"),
            skipped_by_path["src/blob.min.js"])

    def test_normal_file_still_indexed_never_degraded(self):
        self.assertFalse(self.manifest["budget"]["degraded"], self.manifest)
        self.assertGreater(
            self.manifest["indexed"]["by_tier"]["structure"], 0,
            self.manifest)

    def test_coverage_still_balances_exactly(self):
        self.assertTrue(self.manifest["balanced"], self.manifest)
        total = (self.manifest["indexed"]["count"]
                  + self.manifest["skipped"]["count"]
                  + self.manifest["failed"]["count"])
        self.assertEqual(total, self.manifest["tracked"])


@unittest.skipUnless(
    BOOTSTRAP is not None and SEARCH is not None
    and hasattr(SEARCH, "verify_artifact"),
    SEARCH_SKIP_REASON)
class StructureCapNeverTruncatesAStoredRow(unittest.TestCase):
    """The regression guard the D1/D2 design doc calls out by name:
    CONFIRMED CROSS-MODULE DEFECT #2. `put()` computes a row's sha256
    FROM the stored body, and `verify_artifact` re-hashes that SAME
    full stored slice -- so a first line stored right at (not over) the
    cap must verify `fresh`, proving the cap SKIPS an oversized line
    rather than ever truncating one that IS stored.
    """

    def setUp(self):
        self.repo = _write_fixture({
            "src/at_cap.go": "package main // short first line\n",
        })
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state_root = _fresh_state_root("structure-cap-fresh")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "structure-cap-fresh-proj", state_root=self.state_root,
            structure_max_first_line_bytes=4096)

    def test_first_line_under_cap_verifies_fresh(self):
        con = sqlite3.connect(os.path.join(self.state_root, "memory.db"))
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT * FROM artifact WHERE src_path = ?",
                ("src/at_cap.go",)).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row, self.manifest)
        result = SEARCH.verify_artifact(dict(row), self.repo)
        self.assertEqual(result["status"], "fresh", result)


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class CommitHistoryCapNeverSilent(unittest.TestCase):
    """PLAN A4a's "no silent caps: log what was dropped" -- asserted
    against the manifest's own numbers, not merely described. Measured
    basis: DEFAULT_MAX_COMMITS=2,000 bounds RAM at the owner's real,
    171,651-commit testbed B; a tiny explicit cap (5) here reproduces the
    same cap/capped/not_indexed contract cheaply and deterministically.
    """

    def setUp(self):
        self.repo = _write_fixture_with_commits(
            {"README.md": "# fixture\n"}, extra_commit_count=7)  # 8 total
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state_root = _fresh_state_root("commit-cap")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.available = int(
            _git(self.repo, "rev-list", "--count", "HEAD").strip())
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "commit-cap-proj", state_root=self.state_root,
            max_commits=5)

    def test_capped_at_exactly_max_commits(self):
        self.assertEqual(self.available, 8, "sanity: fixture has 8 commits")
        self.assertEqual(self.manifest["history"]["indexed"], 5, self.manifest)
        self.assertEqual(self.manifest["commits_indexed"], 5, self.manifest)
        self.assertTrue(self.manifest["history"]["capped"], self.manifest)
        self.assertEqual(self.manifest["history"]["cap"], 5, self.manifest)
        self.assertEqual(self.manifest["history"]["not_indexed"], 3,
                          self.manifest)
        self.assertEqual(self.manifest["history"]["reason"], "cap_reached",
                          self.manifest)
        self.assertEqual(self.manifest["history"]["available"], 8,
                          self.manifest)

    def test_newest_commits_kept_not_oldest(self):
        # `git log -n 5` walks backwards from HEAD -- the newest 5, not
        # an arbitrary 5 -- so `newest_sha` must equal real HEAD.
        head = _git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(self.manifest["history"]["newest_sha"], head)


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class ShallowCloneRecordedInHistoryManifest(unittest.TestCase):
    """Both real sealed testbeds are shallow clones (measured: `git
    rev-parse --is-shallow-repository` is TRUE on both testbed B at 2,000
    commits and testbed A at 545) -- without this flag, a reader
    cannot tell "we capped at max_commits" from "the clone itself only
    ever had this many commits". A local `--depth` clone reproduces a
    real shallow repo without touching either testbed.
    """

    def setUp(self):
        origin = _write_fixture_with_commits(
            {"README.md": "# fixture\n"}, extra_commit_count=4)  # 5 commits
        self.addCleanup(shutil.rmtree, origin, ignore_errors=True)
        parent = tempfile.mkdtemp(prefix="nightshift-bootstrap-shallow-")
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        self.repo = os.path.join(parent, "clone")
        # --no-local: a same-filesystem `git clone` silently IGNORES
        # --depth by default ("--depth is ignored in local clones; use
        # file:// instead") and clones full history -- confirmed by
        # running this exact command without --no-local first, which
        # produced a non-shallow 5-commit clone instead of the intended
        # 2. --no-local (or a file:// URL) forces the real shallow-clone
        # code path so this fixture actually reproduces what both real
        # testbeds are.
        _git(parent, "clone", "-q", "--no-local", "--depth", "2", origin,
             self.repo)
        self.state_root = _fresh_state_root("shallow")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "shallow-proj", state_root=self.state_root)

    def test_shallow_flag_recorded_true(self):
        self.assertIs(self.manifest["history"]["shallow"], True, self.manifest)

    def test_available_bounded_by_clone_depth(self):
        # The clone's own idea of "available" is bounded by --depth 2 --
        # a reader must see `shallow: true` alongside it (asserted
        # above) or "available" alone would silently overstate how much
        # real history exists.
        self.assertLessEqual(self.manifest["history"]["available"], 2)


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class EmptyRepoHistoryReasonNotBareZero(unittest.TestCase):
    """A real git repo with staged-but-never-committed content (HEAD
    unresolved -- `_head_sha` returns None) must still bootstrap the
    file tier, and `history` must say WHY it's zero
    ("repo_has_no_commits"), not just report a bare 0 indistinguishable
    from a capped or shallow-limited zero.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="nightshift-bootstrap-empty-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        with open(os.path.join(self.repo, "README.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# fixture, never committed\n")
        for cmd in (["init", "-q"],
                    ["config", "user.email", "fixture@example.com"],
                    ["config", "user.name", "Nightshift Bootstrap Fixture"],
                    ["add", "-A"]):
            _git(self.repo, *cmd)  # deliberately no "commit" -- HEAD unresolved
        self.state_root = _fresh_state_root("empty-repo")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.manifest = BOOTSTRAP.bootstrap(
            self.repo, "empty-repo-proj", state_root=self.state_root)

    def test_head_is_none_and_history_reason_explicit(self):
        self.assertIsNone(self.manifest["repo_sha"], self.manifest)
        self.assertEqual(self.manifest["history"]["indexed"], 0, self.manifest)
        self.assertEqual(self.manifest["history"]["reason"],
                          "repo_has_no_commits", self.manifest)
        self.assertEqual(self.manifest["commits_indexed"], 0, self.manifest)

    def test_file_tier_still_indexed_despite_no_commits(self):
        self.assertEqual(self.manifest["tracked"], 1, self.manifest)
        self.assertTrue(self.manifest["balanced"], self.manifest)


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class SyncSharesBootstrapsBudgetPlan(unittest.TestCase):
    """The third latent bug found while designing D1, distinct from the
    bootstrap()-side flattening: sync() used to hardcode `degraded=
    False` unconditionally, so a doc shed by a degraded bootstrap would
    be silently RE-PROMOTED to full tier-1 fidelity the moment it was
    merely touched by the next sync -- making the corpus internally
    inconsistent with nothing in either manifest saying so. Proven here:
    the same doc, shed on bootstrap, is shed AGAIN on sync under the
    identical budget.
    """

    def setUp(self):
        self.repo = _write_fixture({
            "small.md": "small doc.\n" * 5,
            # First line carries a revision marker deliberately -- a
            # shed doc is stored as a structure-tier POINTER, whose row
            # identity is (project, src_path, line_start=1, sha256-of-
            # first-line-ONLY). Found by running this test with an
            # unchanged first line first: nightshift_memory.put()'s own,
            # separate, already-correct contract refuses a same-identity
            # write whose repo_sha differs from the existing row's
            # ("memory_put_conflict") -- exactly right for THAT module,
            # but it would make this test collide with pre-existing
            # writer semantics unrelated to what sync/bootstrap plan
            # parity is testing. Changing the first line on every edit
            # keeps this test isolated to what it actually verifies.
            "big.md": "big.md revision 1\n" + "large doc content line.\n" * 2000,
            "src/main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state_root = _fresh_state_root("sync-plan-parity")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.budget_kwargs = dict(tracked_bytes_budget=1,
                                   doc_tier_bytes_budget=4000)
        self.first = BOOTSTRAP.bootstrap(
            self.repo, "sync-parity-proj", state_root=self.state_root,
            **self.budget_kwargs)

    def test_big_doc_shed_on_initial_bootstrap(self):
        downgraded_paths = {d["path"] for d in self.first["downgraded"]}
        self.assertIn("big.md", downgraded_paths, self.first)

    def test_big_doc_stays_shed_after_touching_sync(self):
        # Touch big.md (still large, still over the doc budget) so it
        # lands in sync's `changed` set.
        big_path = os.path.join(self.repo, "big.md")
        with open(big_path, "r", encoding="utf-8") as fh:
            body = fh.read()
        body = body.replace("big.md revision 1", "big.md revision 2", 1)
        with open(big_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        for cmd in (["add", "-A"], ["commit", "-q", "-m", "touch big.md"]):
            _git(self.repo, *cmd)

        second = BOOTSTRAP.sync(
            self.repo, "sync-parity-proj", state_root=self.state_root,
            **self.budget_kwargs)
        self.assertEqual(second["sync_mode"], "incremental", second)
        downgraded_paths = {d["path"] for d in second["downgraded"]}
        self.assertIn("big.md", downgraded_paths, second)
        self.assertEqual(second["indexed"]["by_tier"]["doc"], 0, second)



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

class GitIsHardenedNotWildcardTrusted(unittest.TestCase):
    """Audit low: `-c safe.directory=*` made git honour a repository owned
    by another uid, config included. It is gone from all three modules, and
    the flags that replace it keep a repository's own fsmonitor from
    running."""

    MODULES = ("nightshift_memory_bootstrap.py", "nightshift_memory_search.py",
               "nightshift_project.py")

    def test_no_module_trusts_every_directory(self):
        for name in self.MODULES:
            with self.subTest(module=name):
                self.assertNotIn('"safe.directory=*"', (BIN / name).read_text())

    @unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
    def test_bootstrap_git_never_runs_a_repository_fsmonitor(self):
        tmp = Path(tempfile.mkdtemp(prefix="memory-fsmonitor-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo, marker = tmp / "repo", tmp / "fsmonitor-ran"
        payload = tmp / "payload"
        payload.write_text(f"#!/bin/sh\n: > {marker}\nexit 1\n")
        payload.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=GIT_ENV)
        (repo / "a.txt").write_text("a\n")
        subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True,
                       env=GIT_ENV)
        subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor",
                        str(payload)], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", str(repo), "status"], capture_output=True,
                       env=GIT_ENV)
        self.assertTrue(marker.exists(), "the fixture never armed fsmonitor")
        marker.unlink()
        BOOTSTRAP._run_git(str(repo), ["status", "--porcelain"])
        self.assertFalse(marker.exists())


class ModuleUnderTestImports(unittest.TestCase):

    def test_a_module_that_exists_must_import(self):
        if _IMPORT_ERROR is not None:
            self.fail(
                "the module under test exists but failed to import; every "
                "other test in this file skipped rather than ran:\n\n"
                + _IMPORT_ERROR)

if __name__ == "__main__":
    unittest.main()
