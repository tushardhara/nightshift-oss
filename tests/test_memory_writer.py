"""Issue #400 -- narrative memory, the writer half (PLAN-400 Track A, A1).

The defect these tests exist to prevent: `.omni/`'s existing narrative is
5.2 MB of markdown, hand-edited in place -- any line can be silently
rewritten or deleted by whoever next opens the file, and there is no way to
ask "what did this artifact actually say at the moment it was written".
`bin/nightshift_memory.py` replaces that with a second SQLite database
(`memory.db`, separate file from `control.db`, physically outside every git
worktree) whose `artifact` table is append-only BY CONSTRUCTION -- three
`BEFORE UPDATE`/`BEFORE DELETE` triggers, not application discipline, refuse
every rewrite and every untombstoned delete. This suite exercises exactly
that: append-only enforcement, refs round-tripping as real provenance
(`file:line` + issue/PR/ADR/commit references), and that editing the same
location produces a NEW row (content hash changes identity) rather than
mutating history.

`purge()` -- the one sanctioned, audited path off this append-only
guarantee, and the H1 rowid/leak regression it must survive -- is
`tests/test_memory_purge.py`, not here.

Same graceful-skip convention `tests/test_project_contract.py` uses for its
sibling module: `bin/nightshift_memory.py` is built by a concurrent worktree
agent (PLAN-400 sec 3, "2 Build A"), so this suite must degrade to a clean
skip -- not a collection error -- if the module has not landed yet, or lands
mid-edit and fails to import.

INTERFACE (`bin/nightshift_memory.py`, PLAN-400 A1), the parts this suite
exercises:

    MEMORY_DB_FILENAME = "memory.db"
    TRUST_TAG = "untrusted_advisory"
    class MemoryStoreError(Exception): .reason_code, .exit_code, .details

    connect(state_root, *, create=True) -> sqlite3.Connection
        Validates state_root (absolute, normalized, mode exactly 0700,
        owned by this uid), opens memory.db with row_factory=sqlite3.Row,
        WAL + secure_delete=ON, and ensures schema.

    put(cx, *, project, kind, classification, retention_class, body, ts,
        src_path=None, line_start=None, line_end=None, repo_sha=None,
        key_version=None, derived_from=None, refs=None) -> dict
        Inserts one immutable row. sha256 is computed HERE from `body`,
        never trusted from a caller. Row identity is
        (project, src_path, line_start, sha256): a retry with an identical
        identity AND matching kind/classification/retention_class/repo_sha
        returns the ORIGINAL receipt (status="existing"); a retry with the
        same identity but disagreeing metadata is refused
        (`memory_put_conflict`). `refs` is an iterable of (kind, val) pairs,
        inserted alongside the artifact in the same transaction.

    get(cx, artifact_id) -> dict | None
        Full row plus `refs: [{"kind", "val"}, ...]` and `trust: TRUST_TAG`
        (H3: every artifact this module returns is tagged advisory, never
        silently handed back as a bare fact).

Mockless, per repo convention: every assertion below is against a real
sqlite3.Connection opened by the module's own `connect()` against a real
file on disk -- no mock, no stub, no monkeypatched cursor.
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


_IMPORT_ERROR = None

def _load_memory_module():
    """Import bin/nightshift_memory.py by path.

    Same technique `tests/test_project_contract.py` uses for
    `nightshift_project_contract.py` -- it's a loose bin/ script, not a
    package, so sys.path is touched only for the duration of the import.
    Returns None (never raises) both when the module doesn't exist yet AND
    when it exists but fails to import, so the suite can skip with a reason
    instead of erroring at collection either way.
    """
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
    "#400 Track A, A1 (PLAN-400-memory-system.md). This suite is written "
    "against the interface documented in this file's module docstring.")


def _state_root():
    """A fresh, private state_root directory `connect()` will accept.

    `tempfile.mkdtemp()` is documented to create its directory at mode 0700
    regardless of umask -- exactly the "owned by this uid, mode exactly
    0700" precondition `_validate_state_root` enforces before it will place
    memory.db inside. No explicit chmod needed; asserted once here so a
    platform where that stdlib guarantee ever changed would fail loudly at
    the fixture, not as a confusing failure three tests downstream.
    """
    path = tempfile.mkdtemp(prefix="nightshift-memory-test-")
    assert oct(os.stat(path).st_mode)[-3:] == "700", path
    return path


@unittest.skipUnless(MEMORY is not None, SKIP_REASON)
class AppendOnlyEnforcement(unittest.TestCase):
    """The `artifact` table refuses rewrites and deletes BY TRIGGER.

    Every case here bypasses `put()`/`purge()` and issues raw SQL directly
    against the connection `connect()` handed back -- the point is that the
    refusal is a property of the SCHEMA, not of the module's Python
    functions choosing to behave. A caller who opens memory.db some other
    way (a future ad-hoc script, a different language) gets the same
    guarantee for free.
    """

    def setUp(self):
        self.cx = MEMORY.connect(_state_root())
        self.addCleanup(self.cx.close)
        self.receipt = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard", body="original body text",
            ts=1000.0, src_path="README.md", line_start=1, line_end=1,
        )
        self.artifact_id = self.receipt["id"]

    def test_update_rewriting_body_of_a_live_row_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute(
                "UPDATE artifact SET body = 'rewritten' WHERE id = ?",
                (self.artifact_id,))
        # the refusal must be transactional, not merely raised-and-ignored --
        # the original text must still be there afterward
        row = self.cx.execute(
            "SELECT body FROM artifact WHERE id = ?", (self.artifact_id,)
        ).fetchone()
        self.assertEqual(row["body"], "original body text")

    def test_delete_of_a_live_untombstoned_row_is_refused(self):
        """"REFUSE ... untombstoned deletes", literally -- a bare DELETE
        against a live row must be structurally impossible, not merely
        discouraged. The only sanctioned path off this table is `purge()`
        (see test_memory_purge.py), which tombstones first."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("DELETE FROM artifact WHERE id = ?", (self.artifact_id,))
        still_there = self.cx.execute(
            "SELECT COUNT(*) AS n FROM artifact WHERE id = ?", (self.artifact_id,)
        ).fetchone()
        self.assertEqual(still_there["n"], 1)

    def test_metadata_only_update_is_allowed(self):
        """Body is append-only; ordinary columns (e.g. a later row's
        `superseded_by` pointing back at this one) are not frozen too --
        only the three triggers' narrow refusals apply."""
        self.cx.execute(
            "UPDATE artifact SET superseded_by = NULL WHERE id = ?",
            (self.artifact_id,))  # must not raise
        row = self.cx.execute(
            "SELECT body FROM artifact WHERE id = ?", (self.artifact_id,)
        ).fetchone()
        self.assertEqual(row["body"], "original body text")

    def test_tombstone_transition_may_only_redact_body(self):
        """The one narrow UPDATE `purge()` is allowed to make -- tombstoned
        0->1 with body cleared -- must not be smuggled together with a
        change to any OTHER column in the same statement."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute(
                "UPDATE artifact SET tombstoned = 1, body = '', kind = 'structure' "
                "WHERE id = ?", (self.artifact_id,))
        row = self.cx.execute(
            "SELECT tombstoned, body, kind FROM artifact WHERE id = ?",
            (self.artifact_id,)).fetchone()
        self.assertEqual(row["tombstoned"], 0)
        self.assertEqual(row["body"], "original body text")
        self.assertEqual(row["kind"], "doc")

    def test_tombstoned_row_is_immutable_even_to_metadata_updates(self):
        """Once redacted, a row is frozen outright -- not merely "body is
        still append-only", but no further UPDATE of any kind succeeds."""
        self.cx.execute(
            "UPDATE artifact SET tombstoned = 1, body = '' "
            "WHERE id = ? AND tombstoned = 0", (self.artifact_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute(
                "UPDATE artifact SET superseded_by = 42 WHERE id = ?",
                (self.artifact_id,))


@unittest.skipUnless(MEMORY is not None, SKIP_REASON)
class ProvenanceRoundTrip(unittest.TestCase):
    """`ref(artifact_id, kind, val)` -- issue/PR/ADR/commit/crew/path -- is
    how a memory row cites where it came from. `put()` writes refs in the
    same transaction as the artifact; `get()` must hand back exactly what
    was written, in insertion order, with nothing dropped or merged."""

    def setUp(self):
        self.cx = MEMORY.connect(_state_root())
        self.addCleanup(self.cx.close)

    def test_refs_round_trip_through_put_and_get(self):
        receipt = MEMORY.put(
            self.cx, project="proj-a", kind="commit", classification="internal",
            retention_class="standard", body="fix: tighten the scope predicate",
            ts=2000.0, repo_sha="deadbeef",
            refs=[("issue", "541"), ("pr", "612"), ("path", "bin/nightshift-ctl")],
        )
        result = MEMORY.get(self.cx, receipt["id"])
        self.assertIsNotNone(result)
        self.assertEqual(
            result["refs"],
            [{"kind": "issue", "val": "541"},
             {"kind": "pr", "val": "612"},
             {"kind": "path", "val": "bin/nightshift-ctl"}])
        # provenance fields pinned alongside the refs (A4b freshness columns)
        self.assertEqual(result["src_path"], None)
        self.assertEqual(result["repo_sha"], "deadbeef")
        self.assertEqual(result["trust"], MEMORY.TRUST_TAG)

    def test_omitted_refs_round_trip_as_an_empty_list(self):
        receipt = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard", body="no refs on this one", ts=2001.0)
        result = MEMORY.get(self.cx, receipt["id"])
        self.assertEqual(result["refs"], [])

    def test_duplicate_kind_val_pairs_are_each_preserved(self):
        """One artifact commonly carries several `path` refs, or the same
        issue ref extracted twice from two different source lines -- a
        naive PRIMARY KEY on (artifact_id, kind, val) would silently
        collapse these. `ref` has a surrogate id precisely so it does not."""
        receipt = MEMORY.put(
            self.cx, project="proj-a", kind="doc", classification="internal",
            retention_class="standard", body="touches two files", ts=2002.0,
            refs=[("path", "a.py"), ("path", "b.py"), ("path", "a.py")],
        )
        result = MEMORY.get(self.cx, receipt["id"])
        self.assertEqual(
            [r["val"] for r in result["refs"] if r["kind"] == "path"],
            ["a.py", "b.py", "a.py"])

    def test_get_of_an_id_that_never_existed_returns_none(self):
        self.assertIsNone(MEMORY.get(self.cx, 999999))


@unittest.skipUnless(MEMORY is not None, SKIP_REASON)
class HashDetectsRewrite(unittest.TestCase):
    """Row identity is `(project, src_path, line_start, sha256)`, with
    sha256 computed from `body` inside `put()` -- never trusted from a
    caller. That single design choice is what makes "editing" a line
    produce a NEW row instead of a silent in-place rewrite: append-only
    history, not because the code refuses an UPDATE (that's
    AppendOnlyEnforcement above), but because there is no operation that
    even asks for one -- a changed body is, definitionally, a different
    identity.
    """

    def setUp(self):
        self.cx = MEMORY.connect(_state_root())
        self.addCleanup(self.cx.close)

    def test_editing_the_same_location_creates_a_new_row_not_a_rewrite(self):
        first = MEMORY.put(
            self.cx, project="p", kind="doc", classification="internal",
            retention_class="standard", body="the original claim",
            ts=1.0, src_path="F.md", line_start=10, line_end=10)
        second = MEMORY.put(
            self.cx, project="p", kind="doc", classification="internal",
            retention_class="standard", body="the corrected claim",
            ts=2.0, src_path="F.md", line_start=10, line_end=10)

        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["sha256"], second["sha256"])
        # append-only: the old claim is not deleted or overwritten by the
        # new one -- both are live, independently readable rows
        old = MEMORY.get(self.cx, first["id"])
        new = MEMORY.get(self.cx, second["id"])
        self.assertEqual(old["body"], "the original claim")
        self.assertFalse(old["tombstoned"])
        self.assertEqual(new["body"], "the corrected claim")

    def test_identical_retry_is_idempotent_not_a_duplicate_row(self):
        """A2/A4a's resumability contract: a re-run of the same content at
        the same identity inserts nothing new -- required for a crash-
        resumed bootstrap or a re-triggered sync to be safe to just re-run."""
        first = MEMORY.put(
            self.cx, project="p", kind="doc", classification="internal",
            retention_class="standard", body="stable content",
            ts=1.0, src_path="F.md", line_start=5)
        retry = MEMORY.put(
            self.cx, project="p", kind="doc", classification="internal",
            retention_class="standard", body="stable content",
            ts=999.0, src_path="F.md", line_start=5)

        self.assertEqual(retry["status"], "existing")
        self.assertEqual(retry["id"], first["id"])
        count = self.cx.execute(
            "SELECT COUNT(*) AS n FROM artifact WHERE sha256 = ?",
            (first["sha256"],)).fetchone()
        self.assertEqual(count["n"], 1)

    def test_same_identity_disagreeing_metadata_is_refused_not_merged(self):
        """A retry that collides on (project, src_path, line_start, sha256)
        but disagrees on kind/classification/retention_class/repo_sha is a
        DIFFERENT logical write pretending to be a retry -- refused, not
        silently answered for."""
        first = MEMORY.put(
            self.cx, project="p", kind="doc", classification="internal",
            retention_class="standard", body="shared content",
            ts=1.0, src_path="F.md", line_start=5)
        with self.assertRaises(MEMORY.MemoryStoreError) as cm:
            MEMORY.put(
                self.cx, project="p", kind="structure", classification="internal",
                retention_class="standard", body="shared content",
                ts=2.0, src_path="F.md", line_start=5)
        self.assertEqual(cm.exception.reason_code, "memory_put_conflict")
        self.assertEqual(cm.exception.details.get("id"), first["id"])

    def test_non_string_body_is_refused_not_coerced(self):
        with self.assertRaises(MEMORY.MemoryStoreError) as cm:
            MEMORY.put(
                self.cx, project="p", kind="doc", classification="internal",
                retention_class="standard", body=12345, ts=1.0)
        self.assertEqual(cm.exception.reason_code, "memory_put_invalid_body")



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
