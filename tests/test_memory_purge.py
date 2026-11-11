"""Issue #400 -- narrative memory, the H1 deletion/leak regression
(PLAN-400 Track A, A4 hardening; PLAN-400 sec 4: "the leak regression").

The defect this suite exists to prevent, measured against a naive first
implementation (PLAN-400 sec 0 / A4, restated in
`bin/nightshift_memory.py`'s own module docstring): FTS5's DEFAULT
`integrity-check` does NOT compare the index to the content table -- it
always reports "ok" even when they have diverged. A redact-then-delete built
against that default check LOOKED complete -- the artifact row was gone, the
`DELETE` had committed -- and a secret (`AKIA_SUPERSECRET_TOKEN` in the
research capture) was STILL `MATCH`-able through the FTS index afterward.
Separately, plain SQLite rowid reuse was confirmed: delete row 1550, the
next insert gets 1550 back -- in a design where memory rows carried a tenant
column instead of living in one physically separate file per project, that
reused id is a cross-tenant leak vector (a stale index entry, or a citation
captured before the purge, now resolves to someone else's row).

`purge()`'s answer, all four parts of which this suite checks
INDEPENDENTLY of purge()'s own self-report (a test that only asserts
`receipt["ok"] is True` is exactly the trap the naive implementation fell
into -- its receipt looked fine too):

  (a) the FTS5 index and the content table agree -- checked with the
      `rank=1` integrity-check, not the default one.
  (b) a `MATCH` probe for the purged content's own terms returns NOTHING.
  (c) the purged plaintext is not merely unindexed but physically absent
      from the on-disk file bytes (`PRAGMA secure_delete=ON` + a WAL
      checkpoint, not just an unlinked b-tree entry still sitting in a
      freed page).
  (d) the freed id is never handed to a new row -- AUTOINCREMENT within one
      project's file, and, since the real design is ONE memory.db PER
      PROJECT rather than one shared database with a tenant column,
      independent verification that a second project's file can reuse the
      same small integer id with zero cross-contamination: querying one
      project's connection can never surface another project's (or a
      purged artifact's) content, because there is no cross-project rowid
      for the FTS index to reuse in the first place.

This suite is written to FAIL against a naive purge that does a plain
`DELETE FROM artifact WHERE id=?` (or that skips the `rank=1` probe and the
`secure_delete`/checkpoint step) -- see `NaiveIntegrityCheckDoesNotCatchIt`
below, which builds exactly that naive shape against the real schema and
demonstrates the default `integrity-check` passing over a real leak.

Same graceful-skip convention as `tests/test_memory_writer.py` /
`tests/test_project_contract.py`: skip cleanly, not a collection error, if
`bin/nightshift_memory.py` has not landed yet or fails to import.

Mockless: every assertion is against a real sqlite3.Connection over a real
file on disk, including direct byte-level reads of that file -- no mock, no
stub.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import traceback
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MODULE_PATH = BIN / "nightshift_memory.py"

# Distinctive enough that it cannot coincidentally appear anywhere else in a
# freshly created memory.db (schema DDL, SQLite's own housekeeping tables,
# etc.) -- a false negative on either the MATCH probe or the byte scan would
# be a bug in THIS suite, not in the module under test.
SECRET = "AKIA_NIGHTSHIFT_PURGE_LEAK_PROBE_7f2c9de1"


_IMPORT_ERROR = None

def _load_memory_module():
    """Import bin/nightshift_memory.py by path -- see
    test_memory_writer.py's identical helper for the full rationale
    (mirrors test_project_contract.py's technique for its sibling module)."""
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("nightshift_memory", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- see docstring: "not ready" too
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {MODULE_PATH} failed to import: {exc}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


MEMORY = _load_memory_module()
SKIP_REASON = (
    f"{MODULE_PATH} not implemented (or not currently importable) -- "
    "#400 Track A, A1/A4 (PLAN-400-memory-system.md). This suite is "
    "written against the interface documented in this file's module "
    "docstring.")


def _state_root():
    """See test_memory_writer.py's `_state_root` for why no chmod is
    needed: `tempfile.mkdtemp()` is documented to create at mode 0700."""
    path = tempfile.mkdtemp(prefix="nightshift-memory-purge-test-")
    assert oct(os.stat(path).st_mode)[-3:] == "700", path
    return path


def _raw_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


@unittest.skipUnless(MEMORY is not None, SKIP_REASON)
class PurgeIsProvablyComplete(unittest.TestCase):
    """H1, all four parts, each verified independently of `purge()`'s own
    receipt -- see the module docstring above for why that independence is
    the entire point."""

    def setUp(self):
        self.state_root = _state_root()
        self.cx = MEMORY.connect(self.state_root)
        self.addCleanup(self.cx.close)
        self.receipt = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard",
            body=f"leaked credential in this line: {SECRET} -- rotate it",
            ts=1000.0, src_path="config/secrets.env", line_start=3, line_end=3,
        )
        self.artifact_id = self.receipt["id"]

        # Prove the secret is actually there before purging it -- a suite
        # that never confirms its own fixture is present cannot tell "purge
        # worked" from "the row was never written".
        pre = self.cx.execute(
            "SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?",
            (SECRET,)).fetchall()
        self.assertEqual([r["rowid"] for r in pre], [self.artifact_id])

        self.purge_receipt = MEMORY.purge(self.cx, self.artifact_id)

    def test_purge_reports_success(self):
        self.assertTrue(self.purge_receipt["ok"])
        self.assertEqual(self.purge_receipt["status"], "purged")

    def test_a_rank1_integrity_check_passes(self):
        """The check the naive implementation's DEFAULT integrity-check
        does not perform. Run here as an INDEPENDENT probe, not by reading
        back whatever purge() self-reported -- if the FTS index and the
        content table have diverged, this raises sqlite3.DatabaseError."""
        try:
            self.cx.execute(
                "INSERT INTO artifact_fts(artifact_fts, rank) VALUES "
                "('integrity-check', 1)")
        except sqlite3.DatabaseError as exc:
            self.fail(f"rank=1 integrity-check failed post-purge: {exc}")

    def test_b_match_probe_for_the_secret_returns_nothing(self):
        """The exact measured H1 failure: a secret still `MATCH`-able after
        the row and bytes were supposedly gone."""
        hits = self.cx.execute(
            "SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?",
            (SECRET,)).fetchall()
        self.assertEqual(list(hits), [])

    def test_c_secret_bytes_are_absent_from_the_db_file_on_disk(self):
        """Not merely unindexed -- physically gone. Reads the raw file
        bytes directly, bypassing SQL entirely, because a b-tree entry can
        be unlinked from every index while the freed page it lived on still
        holds the plaintext until something (secure_delete) overwrites it.
        `purge()` runs `wal_checkpoint(TRUNCATE)` by default specifically
        so this check, run against the MAIN file, is meaningful -- WAL mode
        would otherwise leave the redaction sitting unmerged in `-wal`."""
        path = MEMORY.db_path(self.state_root)
        self.assertNotIn(SECRET.encode("utf-8"), _raw_bytes(path))

        wal_path = path + "-wal"
        if os.path.isfile(wal_path):
            self.assertNotIn(SECRET.encode("utf-8"), _raw_bytes(wal_path))

    def test_d_freed_id_is_not_reused_within_the_same_project(self):
        """AUTOINCREMENT, not a bare INTEGER PRIMARY KEY -- the freed id
        must never come back, even in the same file. This is the mechanism
        that makes a stale citation (a ref, a search result cached before
        the purge) fail closed -- pointing at nothing -- instead of
        silently resolving to whatever unrelated content the id was handed
        to next."""
        replacement = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard", body="unrelated later content",
            ts=2000.0)
        self.assertNotEqual(replacement["id"], self.artifact_id)
        self.assertGreater(replacement["id"], self.artifact_id)

        # the freed id resolves to nothing at all -- not to the new row
        self.assertIsNone(MEMORY.get(self.cx, self.artifact_id))
        gone = self.cx.execute(
            "SELECT COUNT(*) AS n FROM artifact WHERE id = ?",
            (self.artifact_id,)).fetchone()
        self.assertEqual(gone["n"], 0)

    def test_d_purged_content_never_surfaces_from_a_second_projects_db(self):
        """The real cross-tenant mitigation is physical file separation
        (PLAN-400: "one memory.db PER PROJECT ... makes cross-project
        leakage unrepresentable"), not an application-level tenant filter.
        Bind a second project fresh -- its file independently starts its
        own AUTOINCREMENT sequence, so it is entirely plausible for its
        first row to land on the SAME small integer id the purge above just
        freed -- and confirm that coincidence carries no content across:
        querying the second project's own connection for the purged
        secret's terms must find nothing, because there is no shared rowid
        space for a stale index entry to reuse."""
        other_root = _state_root()
        other_cx = MEMORY.connect(other_root)
        self.addCleanup(other_cx.close)
        other_receipt = MEMORY.put(
            other_cx, project="proj-b", kind="doc", classification="internal",
            retention_class="standard", body="proj-b's own unrelated content",
            ts=3000.0)

        self.assertNotEqual(
            MEMORY.db_path(self.state_root), MEMORY.db_path(other_root))

        cross_hits = other_cx.execute(
            "SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?",
            (SECRET,)).fetchall()
        self.assertEqual(list(cross_hits), [])
        # and proj-b's own row is unaffected by proj-a's purge, on whatever
        # id it happened to land on
        self.assertIsNotNone(MEMORY.get(other_cx, other_receipt["id"]))


@unittest.skipUnless(MEMORY is not None, SKIP_REASON)
class PurgeIsIdempotentAndCrashSafe(unittest.TestCase):
    """Calling purge() again on an id already fully removed is success
    (the row being gone is exactly what a repeat purge is asking for), and
    a purge that only got through phase 1 (redacted but not yet physically
    deleted -- the crash-resumed intermediate state) must still finish
    cleanly and pass the same independent checks as an uninterrupted one."""

    def setUp(self):
        self.state_root = _state_root()
        self.cx = MEMORY.connect(self.state_root)
        self.addCleanup(self.cx.close)

    def test_purging_an_id_that_never_existed_is_a_no_op_success(self):
        receipt = MEMORY.purge(self.cx, 999999)
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["status"], "absent")

    def test_purging_twice_is_idempotent(self):
        put_receipt = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard", body=f"second purge target {SECRET}",
            ts=1.0)
        first = MEMORY.purge(self.cx, put_receipt["id"])
        second = MEMORY.purge(self.cx, put_receipt["id"])
        self.assertEqual(first["status"], "purged")
        self.assertEqual(second["status"], "absent")

    def test_resuming_a_crash_after_redaction_but_before_delete_still_leaks_nothing(self):
        """Simulates a crash between purge()'s two phases by performing
        phase 1 (the redaction UPDATE) by hand, then letting purge() finish
        the job -- the row it finds is already tombstoned with body=''."""
        put_receipt = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard", body=f"crash-resumed target {SECRET}",
            ts=1.0)
        artifact_id = put_receipt["id"]

        self.cx.execute(
            "UPDATE artifact SET tombstoned = 1, body = '' "
            "WHERE id = ? AND tombstoned = 0", (artifact_id,))

        receipt = MEMORY.purge(self.cx, artifact_id)
        self.assertEqual(receipt["status"], "purged")

        hits = self.cx.execute(
            "SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?",
            (SECRET,)).fetchall()
        self.assertEqual(list(hits), [])
        self.assertIsNone(MEMORY.get(self.cx, artifact_id))


class NaiveIntegrityCheckDoesNotCatchIt(unittest.TestCase):
    """Not a test of `bin/nightshift_memory.py` at all -- a standalone
    demonstration, against the SAME fts5(content='') schema shape, that the
    DEFAULT `integrity-check` (no `rank=1`) is the trap H1 measured: it
    reports success while a real divergence between the FTS index and the
    content table sits right there. This is why
    `PurgeIsProvablyComplete.test_a_rank1_integrity_check_passes` insists on
    `rank=1` specifically, and why this whole suite probes with a raw
    `MATCH` query rather than trusting any integrity-check's verdict alone.

    Runs unconditionally -- it exercises only stdlib sqlite3 against a
    schema fragment written inline, not `bin/nightshift_memory.py`, so it
    has nothing to skip even before that module exists.
    """

    def test_default_integrity_check_passes_over_a_real_fts_divergence(self):
        path = os.path.join(tempfile.mkdtemp(prefix="naive-purge-"), "naive.db")
        cx = sqlite3.connect(path)
        self.addCleanup(cx.close)
        cx.executescript(
            "CREATE TABLE artifact(id INTEGER PRIMARY KEY, body TEXT);"
            "CREATE VIRTUAL TABLE artifact_fts USING fts5("
            "  body, content='', contentless_delete=1);")
        cx.execute("INSERT INTO artifact(id, body) VALUES (1, ?)", (SECRET,))
        cx.execute("INSERT INTO artifact_fts(rowid, body) VALUES (1, ?)", (SECRET,))
        cx.commit()

        # The naive purge this suite must outperform: delete the content
        # row, but never tell the FTS index -- no redact-then-delete
        # trigger, no explicit `DELETE FROM artifact_fts`.
        cx.execute("DELETE FROM artifact WHERE id = 1")
        cx.commit()

        # The DEFAULT integrity-check (no rank=1) passes anyway -- this is
        # the exact false confidence H1 measured.
        cx.execute("INSERT INTO artifact_fts(artifact_fts) VALUES ('integrity-check')")

        # And the secret is still sitting there, MATCH-able, despite the
        # content row -- and the naive purge's own "it worked" signal --
        # both looking clean.
        hits = cx.execute(
            "SELECT rowid FROM artifact_fts WHERE artifact_fts MATCH ?",
            (SECRET,)).fetchall()
        self.assertEqual([r[0] for r in hits], [1],
                          "fixture invariant broken: this naive shape was "
                          "supposed to still leak the secret")



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
