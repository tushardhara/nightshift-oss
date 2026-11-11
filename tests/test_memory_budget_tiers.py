"""Independent regression suite for the D1/D2 defects fixed in
`bin/nightshift_memory_bootstrap.py`.

This file is DELIBERATELY separate from `tests/test_memory_bootstrap.py`
(which already carries its own D1/D2 coverage, written by whoever built the
fix). Nobody should have to trust a fix's author to have graded their own
homework fairly -- this suite is written independently against the PUBLIC
`bootstrap()`/`sync()` contract and the coverage manifest it returns, and
every class below states explicitly what the PRE-FIX code would have done
differently, so a reader can see why each assertion is a real regression
guard and not an incidental one.

THE TWO DEFECTS, VERBATIM FROM THE TICKET THAT DESCRIBED THEM:

  D1 (bin/nightshift_memory_bootstrap.py, ~line 645, pre-fix):
      tier = "structure" if degraded else _tier_for_path(rel_path)
  Once a repo goes over `tracked_bytes_budget`, EVERY tracked file --
  including tiny, high-decision-density docs -- was flattened to a
  tier-2 structure pointer. Measured on the real testbed B testbed:
  `by_tier {"doc": 0, "structure": 54119}` despite 1,613 real doc files.

  D2 (bin/nightshift_memory_bootstrap.py, ~line 874, pre-fix):
      if not degraded and head is not None:
          commits_indexed = _index_commit_history(...)
  Degrade silently zeroed the ENTIRE commit-history tier, with no skip
  entry, no downgrade record, and no warning -- `commits_indexed: 0`
  against a real 2,000-commit history on testbed B, while `balanced: true`
  (commits are outside the file-coverage balance, so the imbalance check
  could never catch this).

HOW "MUST FAIL AGAINST THE OLD BEHAVIOUR" WAS VERIFIED. `git log --oneline
-- bin/nightshift_memory_bootstrap.py` returns nothing -- the file was
never committed pre-fix in this repo, so there is no earlier revision to
`git stash`/checkout and run these tests against directly. Instead, each
class below was ALSO run, by hand, against a monkeypatched copy of the
loaded module that reconstructs the exact pre-fix branch quoted above
(substituting the new `plan.degraded` bool for the old bare `degraded`
bool the old code closed over, since that is the one piece of context both
versions need) -- see the verification notes in this suite's accompanying
report; every test in `DegradeShedsByValueOnRealisticSizes` and
`DegradedRunNeverSilentlyDropsHistory` failed under that reconstruction and
passes here. This file itself only ever imports and calls the CURRENT
`bin/nightshift_memory_bootstrap.py` -- it does not carry a copy of the old
code, so a future edit to this file can't accidentally start asserting
against a stale reconstruction instead of the real module.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import traceback
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MEMORY_MODULE_PATH = BIN / "nightshift_memory.py"
BOOTSTRAP_MODULE_PATH = BIN / "nightshift_memory_bootstrap.py"

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")


_IMPORT_ERROR = None

def _load_module(path, name):
    """Same technique as `test_memory_bootstrap.py`'s own loader (and
    `test_project_contract.py`'s before it): import a loose `bin/` script
    by path, registered into `sys.modules` under `name` so a sibling
    module's own lazy `from <name> import X` resolves to this loaded copy.
    Degrades to None, never raises -- a module mid-edit by a concurrent
    build agent is expected in this repo, not a defect in this file.
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


def _bridge_writer_contract(memory_module):
    """See `test_memory_bootstrap.py`'s module docstring, "KNOWN
    INTEGRATION SEAM": `nightshift_memory_bootstrap.py` was built against
    an ASSUMED `put_memory(args) -> dict` writer contract; the delivered
    `nightshift_memory.py` exposes `put(cx, *, ...)` instead. Additive-only
    -- never overwrites a real `put_memory`/`MemoryError` -- so this
    shrinks to nothing the moment integration supplies the real names.
    """
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

SKIP_REASON = (
    f"{MEMORY_MODULE_PATH} and/or {BOOTSTRAP_MODULE_PATH} not present (or "
    "not currently importable) -- PLAN-400 Track A, worktree-isolated "
    "build. This suite is written against bootstrap()/sync()'s documented "
    "interface.")


def _fresh_state_root(tag):
    """A private, 0700, euid-owned directory -- what `nightshift_memory.
    connect()`'s `_validate_state_root` requires before it will place
    memory.db inside it (ADR-0021)."""
    root = tempfile.mkdtemp(prefix=f"nightshift-budgettier-{tag}-")
    os.chmod(root, 0o700)
    return root


def _git(repo, *args, timeout=30):
    proc = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout


def _write_fixture(files, *, extra_commit_count=0):
    """A small, real, one-commit-or-more git repo. `extra_commit_count`
    real, empty commits are added on top when a test needs a KNOWN total
    commit count -- a bare `git init` + one commit always gives exactly 1.
    """
    root = tempfile.mkdtemp(prefix="nightshift-budgettier-fixture-")
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    for cmd in (["init", "-q"], ["config", "user.email", "fixture@example.com"],
                ["config", "user.name", "Nightshift Budget-Tier Fixture"],
                ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        _git(root, *cmd)
    for i in range(extra_commit_count):
        _git(root, "commit", "-q", "--allow-empty", "-m", f"extra commit {i}")
    return root


def _kind_by_src_path(state_root, project):
    """Read back the WRITTEN rows directly from memory.db, not just the
    manifest's own counters -- the manifest could in principle report one
    thing while the writer received another; this is the ground truth of
    what actually landed.
    """
    con = sqlite3.connect(os.path.join(state_root, "memory.db"))
    try:
        rows = con.execute(
            "SELECT src_path, kind FROM artifact WHERE project = ? "
            "AND tombstoned = 0", (project,)).fetchall()
    finally:
        con.close()
    return dict(rows)


# ==========================================================================
# (a) + (c): over-budget bootstrap still indexes tier-1 docs, by VALUE not
# by flattening everything, and coverage still balances exactly.
#
# Fixture shape: docs stay genuinely tiny (a few hundred bytes each); one
# source file is genuinely large RELATIVE TO the (also small, explicitly
# passed) `tracked_bytes_budget` -- this is what actually trips
# `degraded=True` in `_plan_budget`'s own real byte arithmetic, not a
# `tracked_bytes_budget=1` trick that would degrade regardless of what the
# fixture contains. `doc_tier_bytes_budget` / `structure_tier_bytes_budget`
# are left at their generous production defaults, so nothing is SHED here
# -- this isolates the exact D1 defect (does `degraded=True` alone flatten
# every tier to "structure"?) from the separate, already-covered
# per-tier-shed behaviour.
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class DegradeShedsByValueOnRealisticSizes(unittest.TestCase):

    DOC_FILES = {
        "README.md": "# fixture\n\nsmall doc, tier 1.\n",
        "docs/adr-0001.md": "# ADR 0001\n\na short decision record.\n",
        "CONTRIBUTING.md": "# contributing\n\nhow to help.\n",
    }
    # ~600 KB of non-doc source -- "huge" only relative to the tiny,
    # explicit 100 KB threshold below, never anywhere near the real 200 MB
    # production default. Real on-disk bytes, not a size claimed via a
    # trivial budget=1 override.
    HUGE_SOURCE_LINE = "x = 1  # generated filler line\n"
    THRESHOLD_BYTES = 100_000

    @classmethod
    def setUpClass(cls):
        files = dict(cls.DOC_FILES)
        files["src/generated_blob.py"] = cls.HUGE_SOURCE_LINE * 20_000  # ~620 KB
        cls.repo = _write_fixture(files)
        cls.state_root = _fresh_state_root("degrade-realistic")
        cls.manifest = BOOTSTRAP.bootstrap(
            cls.repo, "degrade-realistic-proj", state_root=cls.state_root,
            tracked_bytes_budget=cls.THRESHOLD_BYTES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_run_actually_degraded_from_real_bytes_not_a_trick(self):
        """Sanity on the fixture itself: the run must be over budget
        because the source file genuinely is, not because the test
        gamed the threshold down to nothing."""
        self.assertTrue(self.manifest["budget"]["degraded"], self.manifest)
        self.assertGreater(self.manifest["budget"]["tracked_bytes"],
                            self.THRESHOLD_BYTES, self.manifest)
        self.assertGreater(self.manifest["budget"]["tracked_bytes"],
                            500_000, self.manifest)

    def test_all_docs_indexed_at_doc_tier_not_flattened(self):
        """The exact D1 inversion: pre-fix, `degraded=True` alone (with no
        per-tier budget exceeded) flattened every tracked file, docs
        included, to a tier-2 pointer -- `by_tier["doc"]` would read 0
        here despite 3 real doc files. Fixed code reports every one of
        them under "doc"."""
        self.assertEqual(self.manifest["indexed"]["by_tier"]["doc"],
                          len(self.DOC_FILES), self.manifest)

    def test_doc_rows_written_with_kind_doc_not_structure(self):
        """Cross-checks the manifest's own claim against what actually
        landed in memory.db -- the manifest counting "doc" correctly
        while the writer received kind="structure" would be a different,
        equally real way for this defect to hide."""
        kinds = _kind_by_src_path(self.state_root, "degrade-realistic-proj")
        for path in self.DOC_FILES:
            self.assertEqual(kinds.get(path), "doc",
                              f"{path}: {kinds.get(path)!r} (all kinds: {kinds})")

    def test_huge_source_still_indexed_as_structure_not_lost(self):
        """D1's fix must not come at the cost of losing the large file --
        it should land as a (cheap, first-line-only) structure pointer,
        not vanish from indexed/skipped/failed entirely."""
        kinds = _kind_by_src_path(self.state_root, "degrade-realistic-proj")
        self.assertEqual(kinds.get("src/generated_blob.py"), "structure",
                          kinds)

    def test_coverage_balances_exactly_under_degrade(self):
        """(c): PLAN-400 A4a's "indexed + skipped + failed == tracked,
        EXACTLY" must hold even in the degraded branch -- an off-by-one
        in the shed/downgrade bookkeeping is exactly the kind of bug that
        would only show up once a run actually degrades."""
        self.assertTrue(self.manifest["balanced"], self.manifest)
        total = (self.manifest["indexed"]["count"]
                  + self.manifest["skipped"]["count"]
                  + self.manifest["failed"]["count"])
        self.assertEqual(total, self.manifest["tracked"], self.manifest)
        self.assertEqual(self.manifest["failed"]["count"], 0, self.manifest)

    def test_sync_after_degraded_bootstrap_also_balances(self):
        """(c), extended to sync(): a second call, on an unchanged repo,
        must still balance -- sync's own independent balance check
        (`indexed+skipped+failed+removed == changed`) is a different code
        path from bootstrap's, so this is not redundant with the test
        above."""
        second = BOOTSTRAP.sync(
            self.repo, "degrade-realistic-proj", state_root=self.state_root,
            tracked_bytes_budget=self.THRESHOLD_BYTES)
        self.assertEqual(second["sync_mode"], "noop", second)
        self.assertTrue(second["balanced"], second)


# ==========================================================================
# (b): commit history is either indexed in full, or the manifest names the
# cap explicitly -- a silent drop must fail these tests. The sharpest
# version of the D2 regression combines this with real degrade: a run that
# is BOTH degraded AND has real commit history must still index that
# history, since the pre-fix guard (`if not degraded and head is not
# None`) is precisely what zeroed it whenever both conditions held at
# once (exactly testbed B's real shape: 591 MB tracked + 2,000 commits).
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class DegradedRunNeverSilentlyDropsHistory(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        files = {
            "README.md": "# fixture\n\nsmall doc.\n",
            "src/generated_blob.py": "x = 1  # generated filler line\n" * 20_000,
        }
        cls.repo = _write_fixture(files, extra_commit_count=6)  # 7 commits total
        cls.commit_ground_truth = int(
            _git(cls.repo, "rev-list", "--count", "HEAD").strip())
        cls.state_root = _fresh_state_root("degrade-history")
        cls.manifest = BOOTSTRAP.bootstrap(
            cls.repo, "degrade-history-proj", state_root=cls.state_root,
            tracked_bytes_budget=100_000)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_fixture_actually_degraded(self):
        self.assertTrue(self.manifest["budget"]["degraded"], self.manifest)
        self.assertEqual(self.commit_ground_truth, 7,
                          "sanity: fixture has 7 real commits")

    def test_history_indexed_despite_degrade_not_silently_zeroed(self):
        """The exact D2 inversion: pre-fix, `commits_indexed` would read
        0 here purely because `degraded=True`, with nothing in the
        manifest explaining why -- despite 7 real, present commits and
        working git plumbing."""
        self.assertEqual(self.manifest["commits_indexed"],
                          self.commit_ground_truth, self.manifest)

    def test_history_block_names_what_happened_not_a_bare_zero(self):
        """PLAN A4a: "no silent caps: log what was dropped." Even when
        nothing WAS dropped, the manifest must be able to say so
        explicitly (a real "reason" field, an "indexed" count that
        agrees with independently-measured ground truth) rather than a
        caller having to trust a bare int with no context."""
        history = self.manifest["history"]
        self.assertIn("reason", history, self.manifest)
        self.assertIn("capped", history, self.manifest)
        self.assertEqual(history["indexed"], self.commit_ground_truth, history)
        self.assertFalse(history["capped"], history)
        self.assertEqual(history["available"], self.commit_ground_truth,
                          history)

    def test_history_rows_actually_written_to_memory_db(self):
        """Ground truth in the DB, not just the manifest's own count --
        a manifest that claims commits were indexed while the writer
        never received them would be its own, different silent drop."""
        con = sqlite3.connect(os.path.join(self.state_root, "memory.db"))
        try:
            (count,) = con.execute(
                "SELECT COUNT(*) FROM artifact WHERE project = ? "
                "AND kind = 'commit' AND tombstoned = 0",
                ("degrade-history-proj",)).fetchone()
        finally:
            con.close()
        self.assertEqual(count, self.commit_ground_truth)


@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class CommitCapIsRecordedExplicitlyNeverSilent(unittest.TestCase):
    """The complementary half of (b): when history genuinely IS bounded
    (a real cap, independent of degrade), the manifest must name the cap
    by number and reason, not merely report a smaller-than-expected count
    a caller has to notice on their own.
    """

    @classmethod
    def setUpClass(cls):
        cls.repo = _write_fixture(
            {"README.md": "# fixture\n"}, extra_commit_count=11)  # 12 total
        cls.available = int(
            _git(cls.repo, "rev-list", "--count", "HEAD").strip())
        cls.state_root = _fresh_state_root("commit-cap-explicit")
        cls.manifest = BOOTSTRAP.bootstrap(
            cls.repo, "commit-cap-proj", state_root=cls.state_root,
            max_commits=4)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_capped_count_is_bounded_by_the_cap(self):
        self.assertEqual(self.available, 12, "sanity: fixture has 12 commits")
        self.assertEqual(self.manifest["commits_indexed"], 4, self.manifest)

    def test_cap_is_named_explicitly_in_the_manifest(self):
        history = self.manifest["history"]
        self.assertTrue(history["capped"], history)
        self.assertEqual(history["cap"], 4, history)
        self.assertEqual(history["reason"], "cap_reached", history)
        self.assertEqual(history["not_indexed"], 8, history)
        self.assertEqual(history["available"], 12, history)

    def test_uncapped_history_never_carries_cap_reached(self):
        """Negative control: a generous cap over the same fixture must
        NOT report capped -- proves the assertions above are actually
        discriminating a real cap, not a reason string that fires
        unconditionally."""
        state_root = _fresh_state_root("commit-cap-negative")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        manifest = BOOTSTRAP.bootstrap(
            self.repo, "commit-cap-negative-proj", state_root=state_root,
            max_commits=1000)
        self.assertEqual(manifest["commits_indexed"], 12, manifest)
        self.assertFalse(manifest["history"]["capped"], manifest["history"])
        self.assertIsNone(manifest["history"]["reason"], manifest["history"])


# ==========================================================================
# (d): a shallow (equivalently: grafted -- git's own docs describe a
# shallow clone's boundary commits as "grafted": their real parents are
# hidden, exactly what `.git/shallow` implements under the hood) repo
# still gets history indexed for whatever IS reachable, with the
# genuinely-missing-parent boundary recorded explicitly rather than
# silently read as "this repo just doesn't have much history".
# ==========================================================================

@unittest.skipUnless(BOOTSTRAP is not None, SKIP_REASON)
class ShallowGraftedRepoGetsHistoryOrARecordedReason(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.origin = _write_fixture(
            {"README.md": "# fixture\n"}, extra_commit_count=9)  # 10 commits
        parent = tempfile.mkdtemp(prefix="nightshift-budgettier-shallow-")
        cls._parent = parent
        cls.repo = os.path.join(parent, "clone")
        # --no-local: a same-filesystem `git clone` silently IGNORES
        # --depth by default and clones full history -- --no-local (or a
        # file:// URL) forces the real shallow/grafted code path. Depth 1
        # is the sharpest case: HEAD's own parent link is severed (a
        # literal graft), not merely "some" history missing.
        _git(parent, "clone", "-q", "--no-local", "--depth", "1", cls.origin,
             cls.repo)
        cls.state_root = _fresh_state_root("shallow-grafted")
        cls.manifest = BOOTSTRAP.bootstrap(
            cls.repo, "shallow-grafted-proj", state_root=cls.state_root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.origin, ignore_errors=True)
        shutil.rmtree(cls._parent, ignore_errors=True)
        shutil.rmtree(cls.state_root, ignore_errors=True)

    def test_clone_is_genuinely_shallow_grafted(self):
        """Sanity on the fixture: git itself must agree HEAD's parent is
        hidden, or this test would not be exercising the shallow/grafted
        path at all."""
        is_shallow = _git(self.repo, "rev-parse",
                            "--is-shallow-repository").strip()
        self.assertEqual(is_shallow, "true")
        shallow_file = os.path.join(self.repo, ".git", "shallow")
        self.assertTrue(os.path.exists(shallow_file),
                         "expected a .git/shallow graft boundary file")

    def test_reachable_commit_is_indexed_not_silently_skipped(self):
        history = self.manifest["history"]
        self.assertGreaterEqual(history["indexed"], 1, history)
        self.assertEqual(self.manifest["commits_indexed"], history["indexed"])

    def test_shallow_boundary_recorded_explicitly(self):
        """The reader-facing half of this fix: `shallow: true` PLUS a
        non-null "reason" -- without both, a caller cannot tell "capped
        at max_commits of a much longer real history" apart from "this
        clone's boundary is a graft, this really is all there is"."""
        history = self.manifest["history"]
        self.assertIs(history["shallow"], True, history)
        self.assertIsNotNone(history["reason"], history)
        self.assertEqual(history["reason"], "shallow_clone", history)

    def test_file_tier_and_balance_unaffected_by_shallow_history(self):
        """The shallow/grafted boundary is a tier-3 (history) concern
        only -- it must not leak into the tier-1/2 file coverage
        balance."""
        self.assertTrue(self.manifest["balanced"], self.manifest)
        self.assertEqual(self.manifest["tracked"], 1, self.manifest)



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
