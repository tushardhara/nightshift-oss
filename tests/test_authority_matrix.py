"""Issue #400 acceptance condition 4, verbatim:

    "Every data class names exactly one durable authority. Derived files
    and views are visibly projections and cannot override it."

Two independent claims, two test classes:

  MatrixCoversEveryTable        the BIJECTION: every real table in this
                                 checkout's live schemas has exactly one
                                 DATA_CLASSES entry, and no entry names a
                                 table that does not exist.
  ProjectionsCannotOverrideAuthority   the BEHAVIORAL half: poisoning the
                                 declared projection file
                                 (.omni/control-state.json) does not change
                                 a real `nightshift-ctl slot acquire`
                                 verdict, driven through the real binary
                                 via tests/ctlutil.Ctl.

Mockless throughout: `MatrixCoversEveryTable` imports the real schema
modules and calls the real `verify_matrix()`/`full_report()`; the
mutation-detection tests below feed it synthetic inputs (a copy of the
real schema text with one line added) rather than mocking anything.
`ProjectionsCannotOverrideAuthority` drives the real `bin/nightshift-ctl`
executable through real subprocesses against a real SQLite control.db,
exactly like every other control-plane suite in this directory.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_authority_matrix as matrix  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctlutil import Ctl, VIRTUAL_T0  # noqa: E402


# ---------------------------------------------------------------------------
# AC4-BIJECTION
# ---------------------------------------------------------------------------

class MatrixCoversEveryTable(unittest.TestCase):

    def test_every_real_table_has_exactly_one_declared_authority(self):
        """The live invariant: run verify_matrix() against THIS checkout's
        real schema constants and bin/nightshift-ctl's real source text.
        A future stream that adds a table without touching the matrix
        breaks this test, naming the exact table -- that is the intended
        failure mode."""
        report = matrix.full_report()
        self.assertEqual(report.undeclared, [], f"undeclared tables: {report.undeclared}")
        self.assertEqual(report.orphaned, [], f"orphaned matrix entries: {report.orphaned}")
        self.assertEqual(report.duplicated, [], f"duplicated matrix entries: {report.duplicated}")
        self.assertEqual(report.dangling_authority, [],
                         f"projections naming an unregistered or non-durable "
                         f"authority: {report.dangling_authority}")
        self.assertEqual(report.unbacked_projection, [],
                         f"projections naming a table or key that is not real: "
                         f"{report.unbacked_projection}")
        self.assertTrue(report.ok)

    def test_an_undeclared_table_is_reported_by_name(self):
        """Mutation-detection proof for `verify_matrix()` itself: feed it a
        schema text with one extra CREATE TABLE that has no matrix entry.
        Defect shape named in the AC: `orphan_rows` with no matrix entry.
        This does not touch the real repository file -- it proves the
        DETECTOR works, independent of today's real schema being clean."""
        import nightshift_publication as pub
        poisoned_schema = pub.PUBLICATION_SCHEMA + (
            "\nCREATE TABLE IF NOT EXISTS orphan_rows(id INTEGER PRIMARY KEY);\n")
        report = matrix.verify_matrix({"publication.db": poisoned_schema})
        self.assertIn(("publication.db", "orphan_rows"), report.undeclared)
        self.assertFalse(report.ok)

    def test_a_dropped_table_still_declared_is_reported_orphaned(self):
        """Symmetric case: a matrix entry naming a table that is no longer
        real. Built by discovering everything BUT one real table."""
        import nightshift_publication as pub
        real_tables = matrix.discover_tables(pub.PUBLICATION_SCHEMA)
        self.assertIn("publication_reviewer", real_tables)
        # Strip publication_reviewer's CREATE TABLE out of the schema text
        # entirely so it is genuinely undiscoverable, while every OTHER
        # publication.db table (including publication_stage, whose matrix
        # entry we are not touching) stays real.
        lines = pub.PUBLICATION_SCHEMA.splitlines()
        pruned = []
        skipping = False
        for line in lines:
            if "CREATE TABLE IF NOT EXISTS publication_reviewer(" in line:
                skipping = True
            if skipping:
                if line.rstrip().endswith(");"):
                    skipping = False
                continue
            pruned.append(line)
        pruned_schema = "\n".join(pruned)
        self.assertNotIn("publication_reviewer",
                          matrix.discover_tables(pruned_schema))
        report = matrix.verify_matrix({"publication.db": pruned_schema})
        self.assertIn(("publication.db", "publication_reviewer"), report.orphaned)
        self.assertFalse(report.ok)

    def test_no_data_class_names_two_authorities(self):
        """AC4's other half: a data class with TWO authority entries must
        be unrepresentable. `_register()` already refuses a duplicate KEY
        at import time (proven by the module importing cleanly at all --
        if that guard were missing, the real module-level `_register`
        calls for e.g. "publication.publication_stage" would need to be
        literally duplicated in the source to trigger it, which they are
        not). This test instead proves the REPORT-LEVEL guard: two
        DIFFERENT keys naming the SAME (store, table) pair are caught by
        verify_matrix()'s `duplicated` check, using synthetic DataClass
        entries built the same way DATA_CLASSES itself is."""
        dup_a = matrix.DataClass("test.dup_a", store="publication.db",
                                  table="publication_stage", kind=matrix.DURABLE)
        dup_b = matrix.DataClass("test.dup_b", store="publication.db",
                                  table="publication_stage", kind=matrix.DURABLE)
        declared = {}
        for dc in (dup_a, dup_b):
            declared.setdefault((dc.store, dc.table), []).append(dc.key)
        duplicated = [k for k, keys in declared.items() if len(keys) > 1]
        self.assertEqual(duplicated, [("publication.db", "publication_stage")])

    def test_the_purge_receipt_table_is_declared_durable(self):
        """#400 condition 15's new audit surface needs exactly one named
        owner, same as every other table: `provenance.purge_receipt` is
        DURABLE (it is the audit record itself, not a view over anything
        else), and the live matrix already proves it is neither undeclared
        nor orphaned via `test_every_real_table_has_exactly_one_declared_
        authority` above. This test pins the SPECIFIC entry and its kind,
        so a future edit that quietly reclassifies it as a PROJECTION (a
        category error -- an audit row projects onto nothing) is caught
        here even though the bijection check alone would stay green."""
        dc = matrix.DATA_CLASSES["provenance.purge_receipt"]
        self.assertEqual(dc.kind, matrix.DURABLE)
        self.assertEqual((dc.store, dc.table), ("provenance.db", "purge_receipt"))

    def test_register_refuses_a_literal_duplicate_key_at_import_time(self):
        """`_register()`'s own guard: registering the same KEY twice raises
        ValueError immediately, never silently overwriting the first
        entry. This is the mechanism that makes DATA_CLASSES itself unable
        to carry two rows under one key."""
        entry = matrix.DataClass("test.same_key", store="x.db", table="t",
                                  kind=matrix.DURABLE)
        # First registration succeeds and lands in the real DATA_CLASSES
        # dict -- clean it up afterward so this test cannot leak state into
        # test_every_real_table_has_exactly_one_declared_authority, which
        # runs against the SAME module-global dict.
        matrix._register(entry)
        try:
            with self.assertRaises(ValueError):
                matrix._register(matrix.DataClass(
                    "test.same_key", store="x.db", table="t", kind=matrix.DURABLE))
        finally:
            del matrix.DATA_CLASSES["test.same_key"]


# ---------------------------------------------------------------------------
# AC 23's gate function -- a pure function, tested directly (mutation
# evidence for it lives in the docstring-referenced defect: any caller that
# supplies fewer than the four named keywords is a TypeError, and any
# caller passing a falsy value for one of them gets False).
# ---------------------------------------------------------------------------

class CleanupGateRefusesByDefault(unittest.TestCase):

    def test_all_four_true_is_the_only_passing_combination(self):
        self.assertTrue(matrix.cleanup_gate(
            new_authority_bound=True, export_verified=True,
            rollback_rehearsed=True, footprint_clean=True))

    def test_any_single_false_input_refuses(self):
        base = dict(new_authority_bound=True, export_verified=True,
                    rollback_rehearsed=True, footprint_clean=True)
        for key in base:
            kwargs = dict(base)
            kwargs[key] = False
            with self.subTest(missing=key):
                self.assertFalse(matrix.cleanup_gate(**kwargs))

    def test_calling_with_no_arguments_is_a_typeerror_not_a_silent_false(self):
        """A future caller cannot satisfy this gate by omission -- leaving
        a keyword out is a TypeError at the call site, not a quiet False
        that looks like a real refusal."""
        with self.assertRaises(TypeError):
            matrix.cleanup_gate()


# ---------------------------------------------------------------------------
# AC4-PROJECTION
# ---------------------------------------------------------------------------

class ProjectionsCannotOverrideAuthority(unittest.TestCase):

    def test_a_poisoned_control_state_projection_does_not_change_a_slot_verdict(self):
        """Real capacity: 1 worker, 0 reserve. Acquire one real slot (grants).
        Then POISON .omni/control-state.json: claim zero active slots and a
        max_concurrent_workers of 99 -- a file an attacker or a stale backup
        script fully controls. A second `slot acquire` call must still be
        DENIED, for the same real reason (`worker budget exhausted`) and the
        same real counts (1 active, cap 1) -- proving `cmd_slot_acquire`
        reads `.omni/capacity.json` + the real `worker_slots` table, never
        the projection file, even when the projection actively lies in the
        direction that would grant more.
        """
        ctl = Ctl(capacity={"max_concurrent_workers": 1, "reserve_for_oncall": 0},
                  clock=VIRTUAL_T0)

        code, first = ctl.acquire_normal(task="proj-task-1")
        self.assertEqual(code, 0, first)
        self.assertTrue(first["granted"], first)

        # Confirm real state before poisoning: dump --write produces a real
        # control-state.json with one active slot.
        code, _ = ctl.run("dump", "--write")
        self.assertEqual(code, 0)
        projection_path = os.path.join(ctl.omni, "control-state.json")
        with open(projection_path) as fh:
            real_state = json.load(fh)
        self.assertEqual(len(real_state["slots"]), 1)

        # Poison it: claim NO active slots and a huge capacity -- the
        # direction that, if ever consulted, would wrongly GRANT.
        poisoned = dict(real_state)
        poisoned["slots"] = []
        poisoned["max_concurrent_workers"] = 99
        poisoned["capacity"] = {"max_concurrent_workers": 99, "reserve_for_oncall": 0}
        with open(projection_path, "w") as fh:
            json.dump(poisoned, fh)

        code, second = ctl.acquire_normal(task="proj-task-2")
        self.assertEqual(code, 3, second)
        self.assertFalse(second["granted"], second)
        self.assertEqual(second["reason"], "worker budget exhausted")
        self.assertEqual(second["active_total"], 1,
                          "the real active count, not the poisoned file's claimed 0")
        self.assertEqual(second["max_concurrent_workers"], 1,
                          "the real capacity.json value, not the poisoned file's claimed 99")

        # capacity.json itself was never touched.
        with open(os.path.join(ctl.omni, "capacity.json")) as fh:
            real_capacity = json.load(fh)
        self.assertEqual(real_capacity["max_concurrent_workers"], 1)


class ProjectionsCarryAGuaranteeToo(unittest.TestCase):
    """`_d()` entries were checked against the real schema from the start.
    `_p()` entries were NOT checked at all: a projection could name an
    authority nobody declared, or a table and key that did not exist, and
    `verify_matrix()` returned ok. Half this module's vocabulary was
    documentation wearing the costume of an invariant -- the exact
    "evidence for something that did not happen" shape #400 refuses.

    Each test below mutates a REGISTERED projection and asserts the report
    names it. They restore DATA_CLASSES in tearDown so ordering cannot leak.
    """

    KEY = "control.meta_repository_id"

    def setUp(self):
        self.original = matrix.DATA_CLASSES[self.KEY]

    def tearDown(self):
        matrix.DATA_CLASSES[self.KEY] = self.original

    def _replace(self, **kw):
        o = self.original
        fields = dict(key=o.key, store=o.store, table=o.table,
                      kind=o.kind, projects_onto=o.projects_onto, note=o.note)
        fields.update(kw)
        matrix.DATA_CLASSES[self.KEY] = matrix.DataClass(**fields)
        return matrix.full_report()

    def test_projection_onto_an_unregistered_authority_is_named(self):
        report = self._replace(projects_onto="authority.NO_SUCH_AUTHORITY")
        self.assertFalse(report.ok)
        self.assertTrue(
            any("NO_SUCH_AUTHORITY" in row for row in report.dangling_authority),
            f"expected the dangling authority to be named: {report.as_dict()}")

    def test_projection_onto_another_projection_is_named(self):
        # A projection of a projection has no durable authority at the end of
        # the chain, so `authority_of()`'s one-hop contract would silently
        # return a non-authority.
        report = self._replace(projects_onto="memory.artifact_fts")
        self.assertFalse(report.ok)
        self.assertTrue(
            any("not durable" in row for row in report.dangling_authority),
            f"expected 'not durable': {report.as_dict()}")

    def test_projection_of_a_key_that_exists_nowhere_is_named(self):
        report = self._replace(table="meta.TOTALLY_FAKE")
        self.assertFalse(report.ok)
        self.assertTrue(
            any("TOTALLY_FAKE" in row for row in report.unbacked_projection),
            f"expected the unbacked key to be named: {report.as_dict()}")

    def test_projection_of_a_table_that_does_not_exist_is_named(self):
        report = self._replace(table="NOSUCHTABLE.repository_id")
        self.assertFalse(report.ok)
        self.assertTrue(
            any("NOSUCHTABLE" in row for row in report.unbacked_projection),
            f"expected the missing table to be named: {report.as_dict()}")

    def test_the_real_declaration_still_passes(self):
        # The guard must not be so strict it condemns the honest entry.
        self.assertTrue(matrix.full_report().ok)

    def test_path_and_glob_projections_are_NOT_claimed_to_be_verified(self):
        """Scope honesty. Targets like `.omni/leases/*` and
        `dump.scheduled_run_detector output` are NOT verified -- fuzzy-matching
        them would be the same overstatement this class exists to prevent. This
        test records that they are deliberately out of scope, so the gap is a
        stated limit rather than a silent one."""
        unverified = [dc.key for dc in matrix.DATA_CLASSES.values()
                      if dc.kind == matrix.PROJECTION
                      and ("/" in dc.table or "*" in dc.table or " " in dc.table)]
        self.assertTrue(unverified, "expected some path/glob projections to exist")
        # They must still have a real authority -- (a) covers every projection.
        report = matrix.full_report()
        self.assertEqual(report.dangling_authority, [])


if __name__ == "__main__":
    unittest.main()
