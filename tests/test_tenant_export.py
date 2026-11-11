"""Issue #400 acceptance condition 17, verbatim:

    "Tenant export is portable and versioned, includes checksums and
    provenance, and can be verified without trusting the producing
    Nightshift instance."

`bin/nightshift_export.py`'s `verify_export()` is claimed to be a PURE
FUNCTION OF THE BUNDLE: no database, no `state_root`, only the directory.
This suite proves that claim the only convincing way -- by deleting the
producing `state_root` entirely and verifying the copied bundle anyway --
and then proves the four tamper-rejection paths, including one
SELF-CONSISTENT tamper (content changed AND its manifest hash updated to
match) that isolates referential-integrity checking from digest checking.

Mockless: real sqlite3 provenance.db, real files under a real state_root,
real `shutil.copytree`/`shutil.rmtree`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_export as exp        # noqa: E402
import nightshift_provenance as prov   # noqa: E402

T0 = 2_000_000.0
TENANT_ID = 1001
REPOSITORY_ID = 1

IDENTITY = {
    "repository_fingerprint": "sha256:" + "a" * 64,
    "object_format": "sha1",
    "commit_oid": "b" * 40,
    "tree_oid": "c" * 40,
    "captured_at": T0,
}


class TenantExportTestCase(unittest.TestCase):

    def _tmp(self):
        d = tempfile.mkdtemp(prefix="nightshift-export-test-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _build_fixture(self):
        """One tenant/repository: a source_snapshot, two lineage nodes, one
        content-carrying derived_from edge, and one real staged CAS object.
        Returns (state_root, out_dir) with the export already written."""
        state_root = self._tmp()
        cx = prov.connect(state_root, TENANT_ID, REPOSITORY_ID)
        prov.record_source_snapshot(cx, TENANT_ID, REPOSITORY_ID, "snap-1", IDENTITY)
        prov.record_lineage_node(cx, TENANT_ID, REPOSITORY_ID, "node-src",
                                  "source_snapshot", "snapshot", "snap-1", t=T0)
        content = b"exported worker output body\n"
        digest = hashlib.sha256(content).hexdigest()
        prov.stage_object(state_root, TENANT_ID, REPOSITORY_ID, digest, content)
        prov.record_lineage_node(cx, TENANT_ID, REPOSITORY_ID, "node-out",
                                  "worker_output", "output", "out-ref-1",
                                  content_digest=digest, t=T0)
        prov.record_derived_from(cx, TENANT_ID, REPOSITORY_ID, "node-src", "node-out",
                                  "derives", content_carrying=1, t=T0)
        cx.close()

        out_dir = os.path.join(self._tmp(), "bundle")
        receipt = exp.export_tenant(state_root, TENANT_ID, REPOSITORY_ID, out_dir)
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(receipt["entries_count"], 5)  # 4 row files + 1 CAS object
        return state_root, out_dir

    def _fresh_tampered_copy(self, out_dir):
        """A copy of a clean bundle, so each tamper test starts from a
        known-good state and cannot leak into another test."""
        copy_dir = os.path.join(self._tmp(), "tampered")
        shutil.copytree(out_dir, copy_dir)
        return copy_dir

    def _resign_manifest_for(self, out_dir, changed_rel_paths):
        """Self-consistent tamper helper: recompute sha256/size for the
        named files and the whole-list entries_digest, EXACTLY the way
        export_tenant() itself does. This is what makes a tamper
        'self-consistent' -- the digest checks all pass; only referential
        integrity (or a check outside the manifest's own math) can catch
        it."""
        manifest_path = os.path.join(out_dir, exp.MANIFEST_FILENAME)
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        for entry in manifest["entries"]:
            if entry["path"] in changed_rel_paths:
                abs_path = os.path.join(out_dir, entry["path"])
                with open(abs_path, "rb") as fh:
                    data = fh.read()
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                entry["size"] = len(data)
        manifest["entries_digest"] = hashlib.sha256(
            json.dumps(manifest["entries"], sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest()
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh)


class VerifyNeedsOnlyTheBundle(TenantExportTestCase):

    def test_verification_succeeds_after_the_state_root_is_deleted(self):
        state_root, out_dir = self._build_fixture()
        shutil.rmtree(state_root)  # the producer is GONE
        result = exp.verify_export(out_dir)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verified_files"], 5)


class RejectsTamper(TenantExportTestCase):

    def test_a_flipped_body_byte_is_rejected(self):
        _, out_dir = self._build_fixture()
        tampered = self._fresh_tampered_copy(out_dir)
        row_path = os.path.join(tampered, "rows", "source_snapshot.json")
        with open(row_path, "rb") as fh:
            data = bytearray(fh.read())
        data[0] ^= 0xFF
        with open(row_path, "wb") as fh:
            fh.write(bytes(data))
        with self.assertRaises(exp.ExportError) as ctx:
            exp.verify_export(tampered)
        self.assertEqual(ctx.exception.reason_code, "file_digest_mismatch")

    def test_a_removed_row_file_is_rejected(self):
        _, out_dir = self._build_fixture()
        tampered = self._fresh_tampered_copy(out_dir)
        os.remove(os.path.join(tampered, "rows", "closure_entry.json"))
        with self.assertRaises(exp.ExportError) as ctx:
            exp.verify_export(tampered)
        self.assertEqual(ctx.exception.reason_code, "file_missing")

    def test_a_truncated_manifest_entries_digest_is_rejected(self):
        """A manifest edited after the fact -- the entries list no longer
        matches its own recorded digest. This is what a truncated/edited
        manifest looks like from the verifier's side."""
        _, out_dir = self._build_fixture()
        tampered = self._fresh_tampered_copy(out_dir)
        manifest_path = os.path.join(tampered, exp.MANIFEST_FILENAME)
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        manifest["entries"] = manifest["entries"][:-1]  # drop the last entry
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh)
        with self.assertRaises(exp.ExportError) as ctx:
            exp.verify_export(tampered)
        self.assertEqual(ctx.exception.reason_code, "manifest_entries_digest_mismatch")

    def test_a_self_consistent_dangling_derived_from_parent_is_rejected(self):
        """The strongest tamper: change a derived_from edge's parent to a
        node id that does not exist in this bundle's own lineage_node.json,
        then RESIGN the manifest so every digest check passes. Only
        referential integrity -- not byte-checking -- can catch this,
        proving verify_export does that check and not merely digest
        checking."""
        _, out_dir = self._build_fixture()
        tampered = self._fresh_tampered_copy(out_dir)
        derived_path = os.path.join(tampered, "rows", "derived_from.json")
        with open(derived_path) as fh:
            edges = json.load(fh)
        self.assertEqual(len(edges), 1)
        edges[0]["parent_node_id"] = "node-does-not-exist"
        with open(derived_path, "w") as fh:
            json.dump(edges, fh)
        self._resign_manifest_for(tampered, {"rows/derived_from.json"})

        # Sanity: the resign made every per-file digest check pass; only
        # the referential-integrity check is left to catch this.
        result_before_check = None
        try:
            result_before_check = exp.verify_export(tampered)
        except exp.ExportError as exc:
            self.assertEqual(exc.reason_code, "dangling_derived_from_parent")
            self.assertEqual(exc.details["missing_node_id"], "node-does-not-exist")
        else:
            self.fail(f"expected dangling_derived_from_parent, got {result_before_check}")


if __name__ == "__main__":
    unittest.main()
