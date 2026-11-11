"""Issue #400 -- Customer Publication Gateway, PG-6 (cross-tenant fail-closed)
and PG-7 (provenance/lineage) acceptance items, exercised end to end.

Two things this file proves that the sibling test_provenance.py (built by a
different agent, in a different worktree, and not yet merged into this
checkout) does not already cover in this exact shape:

1. The FULL derived_from CHAIN round-trips through its real node kinds --
   `source_snapshot -> worker_output -> reviewed_patch -> published_result`
   -- using bin/nightshift_provenance.py's actual `lineage_chain()` walk,
   including every one of its four "missing link" failure shapes, and that
   `source_not_retained` is representable (the ADR-0004 limit: identity and
   lineage survive; the bytes do not; the record must say so explicitly,
   never silently, and must never propagate onto its descendants).

2. Cross-tenant READ, SEARCH, EXPORT, PUBLISH and DELETE each fail closed,
   each with its OWN negative test, per #400 item 9. Read/search/export/
   delete exercise bin/nightshift_provenance.py (which physically separates
   tenants one directory level deeper than repository_id -- see
   `resolve_publication_root`). PUBLISH exercises the sibling
   bin/nightshift_publication.py. Since #694 that module DELEGATES to this
   one's `resolve_publication_root`, so publication.db is a sibling of
   provenance.db in the same tenant-scoped directory and both files have
   the same physical separation. The PUBLISH tests below therefore attack
   the row-level `tenant_id` re-check ON ITS OWN, by handing tenant B
   tenant A's already-open connection -- i.e. they assume physical
   separation has already been defeated and prove the second layer still
   fails closed.

Mockless throughout: real sqlite3 connections to real on-disk databases,
real git fixtures for repository_fingerprint. Nothing here is mocked,
stubbed or monkeypatched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_provenance as prov  # noqa: E402 -- see sys.path insert above
import nightshift_publication as pub  # noqa: E402

T0 = 1_700_000_000.0

# #400 item 10, structurally required by nightshift_publication.stage()
# itself since the PG-7 fix: every source_snapshot must carry these four
# identity fields. Fixed, well-formed placeholders for the sections below
# that exercise cross-tenant PUBLISH behaviour and are not themselves
# testing identity/closure content -- ClosureDiscoveryAndRequirement below
# uses REAL values computed from a real fixture repo instead.
PUB_FIXTURE_SNAPSHOT = {
    "repository_fingerprint": "sha256:" + "a" * 64,
    "object_format": "sha1",
    "commit_oid": "b" * 40,
    "tree_oid": "c" * 40,
}
# Since #694 nightshift_publication.stage() takes a snapshot_id naming a
# REAL row in the sibling provenance.db, not a dict.
PUB_FIXTURE_SNAPSHOT_ID = "snap-pub-fixture"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_repo(tmp_root, name="repo"):
    repo = os.path.join(tmp_root, name)
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "tester@example.invalid"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "tester"], check=True)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("hello\n")
    subprocess.run(["git", "-C", repo, "add", "a.txt"], check=True)
    fixed_env = dict(
        os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
        GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
        GIT_AUTHOR_DATE="1600000000 +0000", GIT_COMMITTER_DATE="1600000000 +0000")
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], env=fixed_env, check=True)
    return repo


def snapshot_fields(customer_path):
    """Minimal legal `fields` dict for record_source_snapshot -- real
    values, computed from a real fixture repo."""
    commit_oid = subprocess.run(
        ["git", "-C", customer_path, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    tree_oid = subprocess.run(
        ["git", "-C", customer_path, "rev-parse", "HEAD^{tree}"],
        capture_output=True, text=True, check=True).stdout.strip()
    return {
        "repository_fingerprint": prov.repository_fingerprint(customer_path),
        "object_format": "sha1",
        "commit_oid": commit_oid,
        "tree_oid": tree_oid,
        "captured_at": T0,
        "observed_ref": "refs/heads/main",
    }


_CLOSURE_COMMIT_ENV = dict(
    os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
    GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
    GIT_AUTHOR_DATE="1600000100 +0000", GIT_COMMITTER_DATE="1600000100 +0000")


def add_submodule_gitlink(repo, path, commit_oid):
    """Record a real mode-160000 gitlink tree entry via plumbing only -- no
    `git submodule add`/clone needed, matching discover_closure's own
    documented [VERIFIED] claim that it reads this straight off `ls-tree`
    without ever running `git submodule init`."""
    subprocess.run(["git", "-C", repo, "update-index", "--add", "--cacheinfo",
                     f"160000,{commit_oid},{path}"], check=True)


def add_lfs_pointer(repo, path, digest_hex, size):
    """Write a real Git LFS pointer file (the ~130-byte text blob Git
    actually tracks for an LFS path) plus the `.gitattributes` line that
    marks it `filter=lfs` -- no git-lfs binary needed, matching
    discover_closure's documented claim that check-attr + the pointer text
    alone is enough."""
    abs_path = os.path.join(repo, path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w") as fh:
        fh.write(f"version https://git-lfs.github.com/spec/v1\n"
                  f"oid sha256:{digest_hex}\nsize {size}\n")
    with open(os.path.join(repo, ".gitattributes"), "a") as fh:
        fh.write(f"{path} filter=lfs diff=lfs merge=lfs -text\n")
    subprocess.run(["git", "-C", repo, "add", ".gitattributes", path], check=True)


def commit_closure_fixture(repo, message="closure fixture"):
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", message],
                    env=_CLOSURE_COMMIT_ENV, check=True)


# ===========================================================================
# 1. derived_from chain round-trip + source_not_retained representability
# ===========================================================================

class DerivedFromChainRoundTrips(unittest.TestCase):
    """#400 items 10-11: input snapshot -> worker output -> reviewed patch
    -> published result, using the module's REAL node_kind/edge_kind
    vocabulary (`lineage_node.node_kind` is CHECK-constrained to exactly
    these four; `derived_from.edge_kind` to exactly `derives`/`reviews`/
    `publishes`/...), walked back to front by the real `lineage_chain()`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-lineage-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.tenant_id = 4001
        self.repository_id = 1
        self.cx = prov.connect(self.state_root, self.tenant_id, self.repository_id)
        self.addCleanup(self.cx.close)

    def _record_snapshot(self, snapshot_id="snap-1"):
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, snapshot_id,
            snapshot_fields(self.repo))
        return snapshot_id

    def test_full_chain_closes_and_returns_the_four_node_ids_in_order(self):
        snap_id = self._record_snapshot()
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-snapshot", "source_snapshot", "snapshot_id", snap_id, t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "worker_output", "run_id", "run-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "reviewed_patch", "stage_id", "stage-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-result", "published_result", "commit_oid", "deadbeef", t=T0)

        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-snapshot", "node-worker", "derives", 1, t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "node-patch", "reviews", 1, t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "node-result", "publishes", 1, t=T0)

        result = prov.lineage_chain(self.cx, self.tenant_id, self.repository_id, "node-result")
        self.assertTrue(result["closed"], result)
        self.assertEqual(result["chain"],
                          ["node-snapshot", "node-worker", "node-patch", "node-result"])

    def test_missing_published_result_node_itself(self):
        result = prov.lineage_chain(self.cx, self.tenant_id, self.repository_id, "no-such-node")
        self.assertFalse(result["closed"])
        self.assertEqual(result["missing"], "published_result")

    def test_missing_reviewed_patch_link(self):
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-result", "published_result", "commit_oid", "deadbeef", t=T0)
        result = prov.lineage_chain(self.cx, self.tenant_id, self.repository_id, "node-result")
        self.assertFalse(result["closed"])
        self.assertEqual(result["missing"], "reviewed_patch")

    def test_missing_worker_output_link(self):
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "reviewed_patch", "stage_id", "stage-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-result", "published_result", "commit_oid", "deadbeef", t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "node-result", "publishes", 1, t=T0)
        result = prov.lineage_chain(self.cx, self.tenant_id, self.repository_id, "node-result")
        self.assertFalse(result["closed"])
        self.assertEqual(result["missing"], "worker_output")

    def test_missing_source_snapshot_link(self):
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "worker_output", "run_id", "run-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "reviewed_patch", "stage_id", "stage-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-result", "published_result", "commit_oid", "deadbeef", t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "node-patch", "reviews", 1, t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "node-result", "publishes", 1, t=T0)
        result = prov.lineage_chain(self.cx, self.tenant_id, self.repository_id, "node-result")
        self.assertFalse(result["closed"])
        self.assertEqual(result["missing"], "source_snapshot")

    def test_edge_kind_must_match_the_expected_hop(self):
        """A `derives` edge into a `reviewed_patch` slot is not a `reviews`
        edge from `worker_output` -- lineage_chain must not accept the
        wrong edge_kind at a hop even if SOME edge exists."""
        snap_id = self._record_snapshot()
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-snapshot", "source_snapshot", "snapshot_id", snap_id, t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "worker_output", "run_id", "run-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "reviewed_patch", "stage_id", "stage-1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-result", "published_result", "commit_oid", "deadbeef", t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-snapshot", "node-worker", "derives", 1, t=T0)
        # Wrong edge_kind for this hop (should be "reviews").
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "node-patch", "derives", 1, t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-patch", "node-result", "publishes", 1, t=T0)
        result = prov.lineage_chain(self.cx, self.tenant_id, self.repository_id, "node-result")
        self.assertFalse(result["closed"])
        self.assertEqual(result["missing"], "worker_output")

    def test_cycle_is_refused_not_silently_accepted(self):
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "n1", "worker_output", "run_id", "r1", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "n2", "reviewed_patch", "stage_id", "s1", t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "n1", "n2", "reviews", 1, t=T0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                      "n2", "n1", "derives", 1, t=T0)
        self.assertEqual(ctx.exception.reason_code, "lineage_cycle_refused")

    def test_self_loop_is_refused(self):
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "n1", "worker_output", "run_id", "r1", t=T0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                      "n1", "n1", "derives", 1, t=T0)
        self.assertEqual(ctx.exception.reason_code, "lineage_self_loop_refused")


class SourceNotRetainedRepresentable(unittest.TestCase):
    """ADR-0004's explicit limit: when source retention expires before
    narrative retention, the snapshot still carries its exact OID and
    lineage, but bytes are gone and the record must say so -- and this
    must never silently rewrite anything else about the row, never
    propagate onto a content-carrying descendant (ADR-0005: "expiry of a
    Git source snapshot is non-propagating"), and never be repeatable."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-notretained-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.tenant_id = 4002
        self.repository_id = 1
        self.cx = prov.connect(self.state_root, self.tenant_id, self.repository_id)
        self.addCleanup(self.cx.close)
        prov.record_source_snapshot(self.cx, self.tenant_id, self.repository_id,
                                     "snap-x", snapshot_fields(self.repo))

    def test_mark_not_retained_sets_the_explicit_fields(self):
        before = prov.get_source_snapshot(self.cx, self.tenant_id, self.repository_id, "snap-x")
        self.assertEqual(before["source_not_retained"], 0)
        self.assertEqual(before["retention_state"], "retained")

        prov.mark_source_not_retained(self.cx, self.tenant_id, self.repository_id,
                                       "snap-x", "retention_window_expired", t=T0 + 100)

        after = prov.get_source_snapshot(self.cx, self.tenant_id, self.repository_id, "snap-x")
        self.assertEqual(after["source_not_retained"], 1)
        self.assertEqual(after["retention_state"], "expired")
        self.assertEqual(after["source_not_retained_reason"], "retention_window_expired")
        self.assertEqual(after["source_not_retained_at"], T0 + 100)
        # Identity and lineage-addressable facts survive untouched.
        self.assertEqual(after["commit_oid"], before["commit_oid"])
        self.assertEqual(after["tree_oid"], before["tree_oid"])
        self.assertEqual(after["repository_fingerprint"], before["repository_fingerprint"])

    def test_repeat_call_is_refused_not_silently_reapplied(self):
        prov.mark_source_not_retained(self.cx, self.tenant_id, self.repository_id,
                                       "snap-x", "first_reason", t=T0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.mark_source_not_retained(self.cx, self.tenant_id, self.repository_id,
                                           "snap-x", "second_reason", t=T0 + 1)
        self.assertEqual(ctx.exception.reason_code, "snapshot_not_found_or_already_expired")
        row = prov.get_source_snapshot(self.cx, self.tenant_id, self.repository_id, "snap-x")
        self.assertEqual(row["source_not_retained_reason"], "first_reason")

    def test_any_other_mutation_is_refused_by_the_immutability_trigger(self):
        """The row is immutable except the one legal transition -- verified
        by bypassing the module's own function and issuing the forbidden
        UPDATE directly, so this proves the DATABASE enforces it, not just
        application-level discipline."""
        with self.assertRaises(Exception) as ctx:
            self.cx.execute(
                "UPDATE source_snapshot SET commit_oid='deadbeefdeadbeefdeadbeefdeadbeefdeadbeef' "
                "WHERE snapshot_id='snap-x'")
        self.assertIn("immutable", str(ctx.exception))

    def test_non_propagation_a_content_carrying_descendant_is_untouched(self):
        """The non-propagation guarantee is structural: mark_source_not_retained
        touches exactly the one source_snapshot row and never walks
        derived_from at all, so a worker_output that `derives` from this
        snapshot is mechanically unaffected."""
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-snap", "source_snapshot", "snapshot_id", "snap-x", t=T0)
        prov.record_lineage_node(self.cx, self.tenant_id, self.repository_id,
                                  "node-worker", "worker_output", "run_id", "run-1", t=T0)
        prov.record_derived_from(self.cx, self.tenant_id, self.repository_id,
                                  "node-snap", "node-worker", "derives", 1, t=T0)
        worker_before = dict(self.cx.execute(
            "SELECT * FROM lineage_node WHERE node_id='node-worker'").fetchone())

        prov.mark_source_not_retained(self.cx, self.tenant_id, self.repository_id,
                                       "snap-x", "expired", t=T0)

        worker_after = dict(self.cx.execute(
            "SELECT * FROM lineage_node WHERE node_id='node-worker'").fetchone())
        self.assertEqual(worker_before, worker_after)
        # And the descendant is still reachable/undamaged via the normal walk.
        descendants = prov.content_carrying_descendants(
            self.cx, self.tenant_id, self.repository_id, "node-snap")
        self.assertEqual(descendants, {"node-snap", "node-worker"})


# ===========================================================================
# 2. Cross-tenant fail-closed: read, search, export, publish, delete
# ===========================================================================

class CrossTenantReadSearchExportDelete(unittest.TestCase):
    """#400 item 9, the bin/nightshift_provenance.py quarter. Each op gets
    its OWN test. Two layers are exercised for read/search/export: true
    PHYSICAL separation (tenant B's connect() opens a genuinely different
    file) and the row-level WHERE-clause defense (the SAME physical
    connection, presented the wrong tenant_id -- the scenario a scoping
    bug, not an attacker, would actually produce)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-crosstenant-prov-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.tenant_a = 5001
        self.tenant_b = 5002
        self.repository_id = 1
        self.cx_a = prov.connect(self.state_root, self.tenant_a, self.repository_id)
        self.addCleanup(self.cx_a.close)
        prov.record_source_snapshot(self.cx_a, self.tenant_a, self.repository_id,
                                     "snap-a", snapshot_fields(self.repo))

    def test_physical_separation_tenant_b_db_is_a_different_empty_file(self):
        root_a = prov.resolve_publication_root(self.state_root, self.tenant_a, self.repository_id)
        root_b = prov.resolve_publication_root(self.state_root, self.tenant_b, self.repository_id)
        self.assertNotEqual(root_a, root_b)
        cx_b = prov.connect(self.state_root, self.tenant_b, self.repository_id)
        self.addCleanup(cx_b.close)
        self.assertEqual(prov.list_source_snapshots(cx_b, self.tenant_b, self.repository_id), [])
        self.assertIsNone(prov.get_source_snapshot(cx_b, self.tenant_b, self.repository_id, "snap-a"))

    def test_cross_tenant_read_fails_closed(self):
        """READ: even against tenant A's OWN physical connection, a
        wrong-tenant argument to get_source_snapshot returns None -- the
        row-level defense-in-depth layer, independent of physical
        separation."""
        self.assertIsNone(
            prov.get_source_snapshot(self.cx_a, self.tenant_b, self.repository_id, "snap-a"))
        # Positive control: the real tenant really can read its own row.
        self.assertIsNotNone(
            prov.get_source_snapshot(self.cx_a, self.tenant_a, self.repository_id, "snap-a"))

    def test_cross_tenant_search_fails_closed(self):
        """SEARCH/LIST: a tenant-B-scoped list against tenant A's physical
        connection returns empty, never a tenant-A row."""
        results = prov.list_source_snapshots(self.cx_a, self.tenant_b, self.repository_id)
        self.assertEqual(results, [])
        self.assertEqual(
            len(prov.list_source_snapshots(self.cx_a, self.tenant_a, self.repository_id)), 1)

    def test_cross_tenant_export_fails_closed(self):
        """EXPORT: identical query shape to list -- cannot contain a
        tenant-A row when scoped to tenant B."""
        exported = prov.export_source_snapshots(self.cx_a, self.tenant_b, self.repository_id)
        self.assertEqual(exported, [])
        real_export = prov.export_source_snapshots(self.cx_a, self.tenant_a, self.repository_id)
        self.assertEqual([r["snapshot_id"] for r in real_export], ["snap-a"])

    def test_cross_tenant_delete_fails_closed(self):
        """DELETE (source_not_retained is this module's deletion concept
        for a source_snapshot -- ADR-0004): a wrong-tenant call matches
        zero rows via the CAS's own WHERE clause and is refused; the real
        owner's row is byte-identical afterward, still retained."""
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.mark_source_not_retained(self.cx_a, self.tenant_b, self.repository_id,
                                           "snap-a", "attempted_cross_tenant_delete", t=T0)
        self.assertEqual(ctx.exception.reason_code, "snapshot_not_found_or_already_expired")
        row = prov.get_source_snapshot(self.cx_a, self.tenant_a, self.repository_id, "snap-a")
        self.assertEqual(row["source_not_retained"], 0)
        self.assertEqual(row["retention_state"], "retained")
        # Positive control: the real tenant really can delete (mark
        # not-retained) its own row.
        prov.mark_source_not_retained(self.cx_a, self.tenant_a, self.repository_id,
                                       "snap-a", "real_deletion", t=T0)
        row2 = prov.get_source_snapshot(self.cx_a, self.tenant_a, self.repository_id, "snap-a")
        self.assertEqual(row2["source_not_retained"], 1)

    def test_cross_tenant_lineage_node_and_object_delete_also_fail_closed(self):
        """Bonus coverage on the same DELETE item: purge_lineage_node and
        purge_staged_object are the other two deletion primitives this
        module owns, and both are documented as tenant-scoped no-ops under
        the wrong tenant -- verified directly rather than trusted."""
        prov.record_lineage_node(self.cx_a, self.tenant_a, self.repository_id,
                                  "node-a", "worker_output", "run_id", "run-1", t=T0)
        deleted = prov.purge_lineage_node(self.cx_a, self.tenant_b, self.repository_id, "node-a")
        self.assertEqual(deleted, set())
        still_there = self.cx_a.execute(
            "SELECT 1 FROM lineage_node WHERE node_id='node-a'").fetchone()
        self.assertIsNotNone(still_there)

    def test_stage_object_cross_tenant_purge_is_a_no_op(self):
        import hashlib
        data = b"tenant a's staged bytes\n"
        digest = hashlib.sha256(data).hexdigest()
        path = prov.stage_object(self.state_root, self.tenant_a, self.repository_id, digest, data)
        self.assertTrue(os.path.exists(path))
        removed = prov.purge_staged_object(self.state_root, self.tenant_b, self.repository_id, digest)
        self.assertFalse(removed)
        self.assertTrue(os.path.exists(path), "cross-tenant purge must not delete the real object")
        self.assertTrue(prov.object_exists(self.state_root, self.tenant_a, self.repository_id, digest))
        self.assertFalse(prov.object_exists(self.state_root, self.tenant_b, self.repository_id, digest))


class CrossTenantPublishFailsClosed(unittest.TestCase):
    """#400 item 9, the PUBLISH case, exercised against
    bin/nightshift_publication.py.

    Before #694 that module separated tenants by `repository_id` ONLY, so
    two tenants sharing one repository_id genuinely shared ONE physical
    publication.db and the row-level tenant_id check on every verb was the
    entire cross-tenant defense at that layer. #694 gave publication the
    same physical per-tenant separation provenance already had
    (`tenants/<tenant_id>/repos/<repository_id>/publication.db`), so tenant
    B now has to reach across a FILE boundary as well.

    That makes the attack below harder to mount, not weaker: it is
    deliberately kept as a SAME-CONNECTION attack -- tenant B is handed
    tenant A's own open handle on tenant A's own database, i.e. physical
    separation is assumed already defeated -- so what it still proves is
    exactly what it proved before, that the row-level tenant_id re-check
    fails closed on its own."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-crosstenant-pub-",)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Build a real bare publication target so a genuine apply is
        # reachable as the positive control.
        seed = os.path.join(self.tmp, "seed")
        os.makedirs(seed)
        subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
        subprocess.run(["git", "-C", seed, "config", "user.email", "t@example.invalid"], check=True)
        subprocess.run(["git", "-C", seed, "config", "user.name", "t"], check=True)
        with open(os.path.join(seed, "x.txt"), "w") as fh:
            fh.write("seed\n")
        subprocess.run(["git", "-C", seed, "add", "x.txt"], check=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
                   GIT_AUTHOR_DATE="1600000000 +0000", GIT_COMMITTER_DATE="1600000000 +0000")
        subprocess.run(["git", "-C", seed, "commit", "-q", "-m", "seed"], env=env, check=True)
        self.target = os.path.join(self.tmp, "target.git")
        subprocess.run(["git", "clone", "-q", "--bare", seed, self.target], check=True)
        self.base_sha = subprocess.run(
            ["git", "--git-dir", self.target, "rev-parse", "refs/heads/main"],
            capture_output=True, text=True, check=True).stdout.strip()

        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.repository_id = 42
        self.tenant_a = 1001
        self.tenant_b = 1002
        self.prov_cx = prov.connect(self.state_root, self.tenant_a, self.repository_id)
        self.addCleanup(self.prov_cx.close)
        self.snapshot_id = prov.record_source_snapshot(
            self.prov_cx, self.tenant_a, self.repository_id, PUB_FIXTURE_SNAPSHOT_ID,
            dict(PUB_FIXTURE_SNAPSHOT, captured_at=T0))
        self.root = pub.resolve_publication_root(
            self.state_root, self.tenant_a, self.repository_id)
        self.cx = pub.connect(self.root)
        self.addCleanup(self.cx.close)

        pub.policy_set(self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
                        mode="zero_nightshift_file", path_allowlist=["docs/**"],
                        target_git_dir=self.target, target_ref="refs/heads/main",
                        key_version=None, t=T0)
        pub.register_reviewer(self.cx, tenant_id=self.tenant_a, reviewer_identity="reviewer_bob",
                               token_bytes=b"tok-a", t=T0)
        self.stage_result = pub.stage(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            content_bytes=b"tenant a content\n", destination_path="docs/x.md",
            classification="design_doc", producer_identity="worker_alice",
            producer_token_bytes=producer_token(pub, self.cx, self.tenant_a, "worker_alice"),
            producer_run_id="run-1", snapshot_id=self.snapshot_id, t=T0)
        self.review_result = pub.review(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            stage_id=self.stage_result["stage_id"], reviewer_identity="reviewer_bob",
            reviewer_run_id="rr-1", token_bytes=b"tok-a", verdict="approve", t=T0)
        self.approve_result = pub.approve(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            stage_id=self.stage_result["stage_id"], review_id=self.review_result["review_id"],
            reviewer_identity="reviewer_bob", reviewer_run_id="rr-1", token_bytes=b"tok-a",
            expected_base_sha=self.base_sha, ttl=60, t=T0)

    def test_apply_under_a_different_tenant_is_refused_and_authorization_stays_live(self):
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.apply_publication(
                self.cx, tenant_id=self.tenant_b, repository_id=self.repository_id,
                authorization_id=self.approve_result["authorization_id"],
                stage_id=self.stage_result["stage_id"], idempotency_key="cross-tenant-1",
                state_root=self.root, t=T0)
        self.assertEqual(ctx.exception.reason_code, "tenant_mismatch")

        # The authorization must NOT have been consumed by the refused attempt.
        auth_row = self.cx.execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (self.approve_result["authorization_id"],)).fetchone()
        self.assertIsNone(auth_row["consumed_at"])

        # And the target's git-dir was not touched by the refused attempt.
        head_after_refusal = subprocess.run(
            ["git", "--git-dir", self.target, "rev-parse", "refs/heads/main"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(head_after_refusal, self.base_sha)

        # Positive control: the REAL tenant can still apply it for real.
        result = pub.apply_publication(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            authorization_id=self.approve_result["authorization_id"],
            stage_id=self.stage_result["stage_id"], idempotency_key="same-tenant-1",
            state_root=self.root, t=T0)
        self.assertEqual(result["status"], "applied")

    def test_review_under_a_different_tenant_is_refused(self):
        stage2 = pub.stage(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            content_bytes=b"second doc\n", destination_path="docs/y.md",
            classification="design_doc", producer_identity="worker_alice",
            producer_token_bytes=producer_token(pub, self.cx, self.tenant_a, "worker_alice"),
            producer_run_id="run-2", snapshot_id=self.snapshot_id, t=T0)
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.review(
                self.cx, tenant_id=self.tenant_b, repository_id=self.repository_id,
                stage_id=stage2["stage_id"], reviewer_identity="reviewer_bob",
                reviewer_run_id="rr-2", token_bytes=b"tok-a", verdict="approve", t=T0)
        self.assertEqual(ctx.exception.reason_code, "tenant_mismatch")
        no_review = self.cx.execute(
            "SELECT COUNT(*) AS n FROM publication_review WHERE stage_id=?",
            (stage2["stage_id"],)).fetchone()
        self.assertEqual(no_review["n"], 0)

    def test_approve_under_a_different_tenant_is_refused(self):
        stage3 = pub.stage(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            content_bytes=b"third doc\n", destination_path="docs/z.md",
            classification="design_doc", producer_identity="worker_alice",
            producer_token_bytes=producer_token(pub, self.cx, self.tenant_a, "worker_alice"),
            producer_run_id="run-3", snapshot_id=self.snapshot_id, t=T0)
        review3 = pub.review(
            self.cx, tenant_id=self.tenant_a, repository_id=self.repository_id,
            stage_id=stage3["stage_id"], reviewer_identity="reviewer_bob",
            reviewer_run_id="rr-3", token_bytes=b"tok-a", verdict="approve", t=T0)
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.approve(
                self.cx, tenant_id=self.tenant_b, repository_id=self.repository_id,
                stage_id=stage3["stage_id"], review_id=review3["review_id"],
                reviewer_identity="reviewer_bob", reviewer_run_id="rr-3", token_bytes=b"tok-a",
                expected_base_sha=self.base_sha, ttl=60, t=T0)
        self.assertEqual(ctx.exception.reason_code, "tenant_mismatch")
        no_auth = self.cx.execute(
            "SELECT COUNT(*) AS n FROM publication_authorizations WHERE stage_id=?",
            (stage3["stage_id"],)).fetchone()
        self.assertEqual(no_auth["n"], 0)

    def test_patch_under_a_different_tenant_is_refused_and_bytes_are_not_leaked(self):
        """#400 item 9 names EXPORTS explicitly, and `patch_stage` IS the
        export path (PG-5's no_generated_file output: the reviewed diff
        handed to the customer to apply by hand). Physical separation here
        is by repository_id ONLY (see class docstring), so this row-level
        tenant_id check is the entire defense -- there is no third-layer
        backstop the way apply()'s CAS predicate provides. Assert not just
        a refusal, but that a positive control on the SAME stage really
        does carry tenant A's real bytes, so the refusal above is proven
        to be guarding real content, not an already-empty payload."""
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.patch_stage(
                self.cx, stage_id=self.stage_result["stage_id"],
                tenant_id=self.tenant_b, repository_id=self.repository_id)
        self.assertEqual(ctx.exception.reason_code, "tenant_mismatch")

        # Positive control: the real tenant's patch really does contain
        # tenant A's content -- so the refusal above is guarding actual
        # bytes, not nothing.
        patch = pub.patch_stage(
            self.cx, stage_id=self.stage_result["stage_id"],
            tenant_id=self.tenant_a, repository_id=self.repository_id)
        self.assertIn("tenant a content", patch["patch"])


# ===========================================================================
# 3. Closure discovery + requirement (#400 item 11) -- real git fixtures.
#
# discover_closure/record_closure_entry/require_closure_complete have real,
# working implementations in nightshift_provenance.py that carried ZERO
# test coverage before this class: nothing in the shipped suite ever built
# a real submodule or LFS fixture and drove them. This class does, against
# a real submodule gitlink and a real Git LFS pointer file, exercising all
# three functions plus closure_entry's cross-tenant scoping and its
# immutability trigger. Separately, tests/test_publication_gateway.py's
# SourceSnapshotIdentityAndClosure class proves the SAME
# closure_required_paths/closure contract is reachable from
# nightshift_publication.stage() -- the real stage -> review -> approve ->
# apply entrypoint -- so item 11 is both tested here and wired into the
# real publish path, not merely present in this module alone.
# ===========================================================================

class ClosureDiscoveryAndRequirement(unittest.TestCase):

    SUBMODULE_OID = "1" * 40
    LFS_DIGEST_HEX = "f" * 64

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-closure-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)
        add_submodule_gitlink(self.repo, "vendor/sub", self.SUBMODULE_OID)
        add_lfs_pointer(self.repo, "assets/large.bin", self.LFS_DIGEST_HEX, 999)
        commit_closure_fixture(self.repo)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.tenant_id = 4201
        self.tenant_b = 4202
        self.repository_id = 1
        self.cx = prov.connect(self.state_root, self.tenant_id, self.repository_id)
        self.addCleanup(self.cx.close)

    def test_discover_closure_finds_the_real_submodule_and_lfs_entries(self):
        entries = prov.discover_closure(self.repo)
        self.assertEqual(len(entries), 2, entries)
        by_kind = {e["kind"]: e for e in entries}
        self.assertEqual(by_kind["submodule"]["path"], "vendor/sub")
        self.assertEqual(by_kind["submodule"]["oid"], self.SUBMODULE_OID)
        self.assertEqual(by_kind["lfs"]["path"], "assets/large.bin")
        self.assertEqual(by_kind["lfs"]["digest"], "sha256:" + self.LFS_DIGEST_HEX)
        self.assertEqual(by_kind["lfs"]["size"], 999)

    def test_discover_closure_on_a_repo_with_neither_returns_empty(self):
        plain = make_repo(self.tmp, name="plain")
        self.assertEqual(prov.discover_closure(plain), [])

    def test_require_closure_complete_passes_when_every_required_path_is_covered(self):
        entries = prov.discover_closure(self.repo)
        self.assertTrue(prov.require_closure_complete(
            ["vendor/sub", "assets/large.bin"], entries))

    def test_require_closure_complete_refuses_when_a_required_path_is_missing(self):
        entries = prov.discover_closure(self.repo)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_closure_complete(
                ["vendor/sub", "assets/large.bin", "vendor/other-sub"], entries)
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["missing"], ["vendor/other-sub"])

    def test_record_closure_entry_persists_real_discovered_entries(self):
        snap_id = "snap-closure-1"
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, snap_id, snapshot_fields(self.repo))
        entries = prov.discover_closure(self.repo)
        for entry in entries:
            prov.record_closure_entry(
                self.cx, self.tenant_id, self.repository_id, snap_id,
                entry["kind"], entry["path"], entry["oid"],
                digest=entry.get("digest"), size=entry.get("size"), t=T0)
        rows = self.cx.execute(
            "SELECT * FROM closure_entry WHERE snapshot_id=? AND tenant_id=? AND repository_id=? "
            "ORDER BY path", (snap_id, self.tenant_id, self.repository_id)).fetchall()
        self.assertEqual(len(rows), 2, [dict(r) for r in rows])
        self.assertEqual(rows[0]["path"], "assets/large.bin")
        self.assertEqual(rows[0]["kind"], "lfs")
        self.assertEqual(rows[0]["size"], 999)
        self.assertEqual(rows[1]["path"], "vendor/sub")
        self.assertEqual(rows[1]["kind"], "submodule")

    def test_closure_entry_rows_are_immutable(self):
        """Same DATABASE-enforced-not-just-application-discipline shape as
        SourceNotRetainedRepresentable's own immutability test above."""
        snap_id = "snap-closure-2"
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, snap_id, snapshot_fields(self.repo))
        row_id = prov.record_closure_entry(
            self.cx, self.tenant_id, self.repository_id, snap_id,
            "submodule", "vendor/sub", self.SUBMODULE_OID, t=T0)
        with self.assertRaises(Exception) as ctx:
            self.cx.execute("UPDATE closure_entry SET oid='changed' WHERE id=?", (row_id,))
        self.assertIn("immutable", str(ctx.exception))

    def test_cross_tenant_closure_entries_are_invisible_through_the_scoped_query(self):
        """#400 item 9, extended to closure_entry: physical separation is
        the primary guarantee (tenant B's provenance.db is a different
        file one directory level deeper -- resolve_publication_root),
        re-checked here at the query layer the same shape every other
        cross-tenant test in this file uses."""
        snap_id = "snap-closure-3"
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, snap_id, snapshot_fields(self.repo))
        prov.record_closure_entry(
            self.cx, self.tenant_id, self.repository_id, snap_id,
            "submodule", "vendor/sub", self.SUBMODULE_OID, t=T0)
        cx_b = prov.connect(self.state_root, self.tenant_b, self.repository_id)
        self.addCleanup(cx_b.close)
        rows_b = cx_b.execute(
            "SELECT * FROM closure_entry WHERE tenant_id=? AND repository_id=?",
            (self.tenant_b, self.repository_id)).fetchall()
        self.assertEqual(len(rows_b), 0)


# ===========================================================================
# 4. The capture path DISCOVERS the closure (#694 round 3).
#
# `verify_closure_complete` compares a digest frozen on the immutable
# snapshot row against the closure_entry rows provenance actually holds.
# That check was unreachable in practice: the column was NULLable, NULL
# meant "pass", and NOTHING in bin/ ever computed it -- `discover_closure`
# had no production caller, and `capture_dirty_snapshot`, whose return dict
# is documented as feeding straight into `record_source_snapshot`'s fields,
# did not include it. The guard therefore could not fire on any snapshot a
# real capture produced.
#
# It now can: capture discovers the closure of the SYNTHETIC commit it
# writes (not of customer HEAD -- a dirty tree can add or drop a gitlink),
# and `record_source_snapshot` refuses to leave the claim unrecorded.
# ===========================================================================

class CaptureDiscoversTheClosureItFreezes(unittest.TestCase):

    LFS_DIGEST_HEX = "e" * 64

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-capture-closure-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)
        add_lfs_pointer(self.repo, "assets/large.bin", self.LFS_DIGEST_HEX, 4242)
        commit_closure_fixture(self.repo)
        # Make the checkout dirty, which is the precondition
        # capture_dirty_snapshot exists for.
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("hello modified\n")
        self.mirror = os.path.join(self.tmp, "mirror")
        prov.acquire_mirror(self.repo, self.mirror)
        self.ws = os.path.join(self.tmp, "ws")
        prov.create_workspace(self.mirror, self.ws)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.tenant_id = 4301
        self.repository_id = 1
        self.cx = prov.connect(self.state_root, self.tenant_id, self.repository_id)
        self.addCleanup(self.cx.close)

    def _capture(self, snapshot_id="snap-dirty"):
        return prov.capture_dirty_snapshot(
            self.repo, self.mirror, self.ws,
            snapshot_id=snapshot_id, capture_time="1700000000 +0000")

    def test_capture_returns_the_discovered_entries_and_their_digest(self):
        result = self._capture()
        paths = sorted(e["path"] for e in result["closure_entries"])
        self.assertEqual(paths, ["assets/large.bin"], result["closure_entries"])
        self.assertEqual(result["closure_entries"][0]["kind"], "lfs")
        self.assertEqual(result["closure_entries"][0]["digest"],
                          "sha256:" + self.LFS_DIGEST_HEX)
        self.assertEqual(result["closure_manifest_digest"],
                          prov.closure_manifest_digest(result["closure_entries"]))
        self.assertNotEqual(result["closure_manifest_digest"],
                            prov.closure_manifest_digest([]),
                            "a repo with LFS content must not freeze an empty claim")

    def test_the_closure_is_read_from_the_synthetic_tree_not_from_customer_head(self):
        """A dirty tree can add content HEAD does not have. The frozen
        digest must describe the tree the snapshot row names."""
        add_lfs_pointer(self.repo, "assets/added.bin", "d" * 64, 7)  # dirty, uncommitted
        result = self._capture("snap-added")
        paths = sorted(e["path"] for e in result["closure_entries"])
        self.assertEqual(paths, ["assets/added.bin", "assets/large.bin"])
        self.assertNotEqual(
            result["closure_manifest_digest"],
            prov.closure_manifest_digest(prov.discover_closure(self.repo)),
            "customer HEAD does not know about the uncommitted LFS path")

    def test_a_captured_snapshot_missing_one_entry_is_refused_by_the_verifier(self):
        """End to end, on a snapshot a REAL capture produced: freeze the
        discovered digest, persist none of the entries, and the guard that
        used to be inert now refuses."""
        result = self._capture()
        fields = dict(
            repository_fingerprint=prov.repository_fingerprint(self.repo),
            object_format="sha1",
            commit_oid=result["commit_oid"], tree_oid=result["tree_oid"],
            captured_at=T0, synthetic=result["synthetic"],
            synthetic_parent_commit_oid=result["synthetic_parent_commit_oid"],
            patch_digest=result["patch_digest"],
            dirty_capture_method=result["dirty_capture_method"],
            worktree_file_manifest_digest=result["worktree_file_manifest_digest"],
            included_ignored=result["included_ignored"],
            retention_ref=result["retention_ref"], pack_digest=result["pack_digest"],
            closure_manifest_digest=result["closure_manifest_digest"])
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, "snap-dirty", fields)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.verify_closure_complete(
                self.cx, self.tenant_id, self.repository_id, "snap-dirty")
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["covered_paths"], [])

        # Persist what capture discovered and the same snapshot verifies.
        for entry in result["closure_entries"]:
            prov.record_closure_entry(
                self.cx, self.tenant_id, self.repository_id, "snap-dirty",
                entry["kind"], entry["path"], entry["oid"],
                digest=entry.get("digest"), size=entry.get("size"), t=T0)
        self.assertTrue(prov.verify_closure_complete(
            self.cx, self.tenant_id, self.repository_id, "snap-dirty"))

if __name__ == "__main__":
    unittest.main()
