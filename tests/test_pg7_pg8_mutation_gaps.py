"""Issue #695 -- coverage the PG-7/PG-8 mutation audit proved was MISSING.

Every test in this file exists because a named defect was injected into the
real production code, the whole PG-4/6/7/8 suite (124 tests:
test_provenance, test_provenance_lineage, test_publication_gateway,
test_publication_footprint, test_publication_provenance_seam) was run, and
NOTHING WENT RED. The mutation id in each docstring is the one to re-inject
to watch that specific test fail again.

Mockless, in the style of the suites it supplements: real git repositories
built with real `git` subprocess calls in tmpdirs, real sqlite3 connections
to real on-disk provenance.db/publication.db files. Nothing is mocked,
stubbed or monkeypatched.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_provenance as prov  # noqa: E402 -- see sys.path insert above

from test_publication_gateway import (  # noqa: E402 -- sibling fixture reuse
    NSP, PublicationGatewayTestCase, T0,
)

PT0 = 1_700_000_000.0


def _git(repo, *args, env=None, check=True):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                             text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


_FIXED_ENV = dict(
    os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
    GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
    GIT_AUTHOR_DATE="1600000000 +0000", GIT_COMMITTER_DATE="1600000000 +0000")


def make_repo(tmp_root, name="repo", seed=b"hello\n", date="1600000000 +0000"):
    repo = os.path.join(tmp_root, name)
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester@example.invalid")
    _git(repo, "config", "user.name", "tester")
    with open(os.path.join(repo, "a.txt"), "wb") as fh:
        fh.write(seed)
    _git(repo, "add", "a.txt")
    env = dict(_FIXED_ENV, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    _git(repo, "commit", "-q", "-m", "init", env=env)
    return repo


def _identity(**overrides):
    fields = {
        "repository_fingerprint": "sha256:" + "a" * 64,
        "object_format": "sha1",
        "commit_oid": "b" * 40,
        "tree_oid": "c" * 40,
        "captured_at": PT0,
    }
    fields.update(overrides)
    return fields


# ===========================================================================
# PG-7 -- repository identity
# ===========================================================================

class RepositoryFingerprintIsAnIdentity(unittest.TestCase):
    """MUTATION PG7-M01: make `repository_fingerprint` hash only
    `object_format` (drop `root_commits` from the canonical JSON) and every
    repository on earth gets the same fingerprint -- the anti-substitution
    property #400 item 10 exists for is gone. The whole 124-test suite stayed
    GREEN, because the only fingerprint assertion anywhere was
    `test_repository_fingerprint_is_read_only`'s `fp1 == fp2`, which a
    constant satisfies perfectly.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-fp-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_two_unrelated_repositories_do_not_share_a_fingerprint(self):
        a = make_repo(self.tmp, name="a", seed=b"alpha\n", date="1600000000 +0000")
        b = make_repo(self.tmp, name="b", seed=b"beta\n", date="1600000001 +0000")
        self.assertNotEqual(prov.repository_fingerprint(a),
                            prov.repository_fingerprint(b),
                            "two unrelated repositories must not present the same "
                            "canonical identity -- substitution would be undetectable")

    def test_a_clone_of_the_same_repository_keeps_the_same_fingerprint(self):
        """The sibling control: the fingerprint must not merely be 'some
        number that differs', it must be STABLE across a genuinely different
        path/worktree for the same repository, or it could not be used to
        recognise the registered repository at all."""
        a = make_repo(self.tmp, name="orig")
        clone = os.path.join(self.tmp, "clone")
        subprocess.run(["git", "clone", "-q", a, clone], check=True,
                        capture_output=True)
        self.assertEqual(prov.repository_fingerprint(a),
                         prov.repository_fingerprint(clone))

    def test_a_further_commit_does_not_change_the_fingerprint(self):
        """Second control, this time pinning WHICH facts the fingerprint is
        allowed to depend on: it is rooted in the repository's root commits,
        so ordinary history growth must leave it untouched. A fingerprint
        that moved on every commit would be unusable as an identity."""
        a = make_repo(self.tmp, name="growing")
        before = prov.repository_fingerprint(a)
        with open(os.path.join(a, "b.txt"), "w") as fh:
            fh.write("more\n")
        _git(a, "add", "b.txt")
        _git(a, "commit", "-q", "-m", "second", env=_FIXED_ENV)
        self.assertEqual(prov.repository_fingerprint(a), before)


# ===========================================================================
# PG-7 -- closure discovery and the frozen closure manifest
# ===========================================================================

class ClosureManifestDigestPinsTheOid(unittest.TestCase):
    """MUTATION PG7-M12: drop `oid` from the triple
    `closure_manifest_digest` hashes, so the frozen manifest pins only
    (kind, path). A submodule repointed at a DIFFERENT commit at the same
    path then verifies as complete. The suite stayed GREEN: every existing
    closure test varies the PATH SET, never the OID at a fixed path -- but
    #400 item 11 is about the submodule COMMITS, not the directory names.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-closure-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.tenant_id = 6951
        self.repository_id = 1
        self.cx = prov.connect(self.state_root, self.tenant_id, self.repository_id)
        self.addCleanup(self.cx.close)

    def test_a_submodule_repointed_at_a_different_commit_is_closure_incomplete(self):
        pinned = [{"kind": "submodule", "path": "vendor/sub", "oid": "d" * 40}]
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, "snap-pin",
            _identity(closure_manifest_digest=prov.closure_manifest_digest(pinned)))
        # Same kind, same path -- a DIFFERENT commit.
        prov.record_closure_entry(
            self.cx, self.tenant_id, self.repository_id, "snap-pin",
            "submodule", "vendor/sub", "e" * 40, t=PT0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.verify_closure_complete(
                self.cx, self.tenant_id, self.repository_id, "snap-pin")
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertNotEqual(ctx.exception.details["recorded_manifest_digest"],
                            ctx.exception.details["observed_manifest_digest"])

    def test_an_lfs_object_swapped_at_the_same_path_is_closure_incomplete(self):
        pinned = [{"kind": "lfs", "path": "assets/big.bin", "oid": "1" * 40}]
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, "snap-lfs",
            _identity(closure_manifest_digest=prov.closure_manifest_digest(pinned)))
        prov.record_closure_entry(
            self.cx, self.tenant_id, self.repository_id, "snap-lfs",
            "lfs", "assets/big.bin", "2" * 40, t=PT0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.verify_closure_complete(
                self.cx, self.tenant_id, self.repository_id, "snap-lfs")
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")

    def test_the_exact_pin_still_verifies(self):
        """Sibling control -- the check above must be catching the OID
        change, not refusing everything."""
        pinned = [{"kind": "submodule", "path": "vendor/sub", "oid": "d" * 40}]
        prov.record_source_snapshot(
            self.cx, self.tenant_id, self.repository_id, "snap-ok",
            _identity(closure_manifest_digest=prov.closure_manifest_digest(pinned)))
        prov.record_closure_entry(
            self.cx, self.tenant_id, self.repository_id, "snap-ok",
            "submodule", "vendor/sub", "d" * 40, t=PT0)
        self.assertTrue(prov.verify_closure_complete(
            self.cx, self.tenant_id, self.repository_id, "snap-ok"))


class ClosureVerificationIsTenantScoped(unittest.TestCase):
    """MUTATION PG7-M10: drop `AND tenant_id=? AND repository_id=?` from
    `verify_closure_complete`'s closure_entry SELECT. The suite stayed GREEN
    because no test ever puts two tenants' closure_entry rows in ONE
    database file -- yet
    `test_provenance.test_defense_in_depth_same_file_cross_tenant_edge_refused`
    establishes that exactly that scenario (a scoping bug, not an attacker)
    is the one the row-level filters exist for. This is that test's missing
    twin for closure verification.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-closure-tenant-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.cx = prov.connect(self.state_root, 6952, 1)
        self.addCleanup(self.cx.close)

    def test_another_tenants_closure_entry_cannot_satisfy_this_tenants_manifest(self):
        required = [{"kind": "submodule", "path": "vendor/sub", "oid": "d" * 40}]
        prov.record_source_snapshot(
            self.cx, 6952, 1, "snap-shared",
            _identity(closure_manifest_digest=prov.closure_manifest_digest(required)))
        # Tenant 6953's row, same snapshot_id, in the SAME physical file --
        # written around the scoped writer exactly as the sibling
        # defense-in-depth test does.
        self.cx.execute(
            "INSERT INTO closure_entry(snapshot_id,tenant_id,repository_id,kind,path,oid,"
            "digest,size,verified_at) VALUES(?,?,?,?,?,?,NULL,NULL,?)",
            ("snap-shared", 6953, 1, "submodule", "vendor/sub", "d" * 40, PT0))
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.verify_closure_complete(self.cx, 6952, 1, "snap-shared")
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["covered_paths"], [],
                         "the other tenant's entry must not even be counted")

    def test_another_repositorys_closure_entry_cannot_satisfy_this_one(self):
        required = [{"kind": "lfs", "path": "assets/big.bin", "oid": "1" * 40}]
        prov.record_source_snapshot(
            self.cx, 6952, 1, "snap-repo",
            _identity(closure_manifest_digest=prov.closure_manifest_digest(required)))
        self.cx.execute(
            "INSERT INTO closure_entry(snapshot_id,tenant_id,repository_id,kind,path,oid,"
            "digest,size,verified_at) VALUES(?,?,?,?,?,?,NULL,NULL,?)",
            ("snap-repo", 6952, 2, "lfs", "assets/big.bin", "1" * 40, PT0))
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.verify_closure_complete(self.cx, 6952, 1, "snap-repo")
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")


# ===========================================================================
# PG-7 -- the derived_from chain
# ===========================================================================

class LineageChainChecksBothEdgeKindAndNodeKind(unittest.TestCase):
    """MUTATION PG7-M18: drop `AND p.node_kind=?` from `_parent_via`. The
    suite stayed GREEN -- `test_edge_kind_must_match_the_expected_hop`
    covers the edge half, and nothing covers the node half, so a
    `worker_output` sitting in the `reviewed_patch` slot closed the hop.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-lineage-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.cx = prov.connect(self.state_root, 6954, 1)
        self.addCleanup(self.cx.close)

    def _node(self, node_id, kind):
        prov.record_lineage_node(self.cx, 6954, 1, node_id, kind, "ref", node_id, t=PT0)

    def test_a_worker_output_in_the_reviewed_patch_slot_does_not_close_the_hop(self):
        self._node("n-worker", "worker_output")
        self._node("n-result", "published_result")
        # Right EDGE kind for the hop, wrong NODE kind at the other end.
        prov.record_derived_from(self.cx, 6954, 1, "n-worker", "n-result",
                                  "publishes", 1, t=PT0)
        result = prov.lineage_chain(self.cx, 6954, 1, "n-result")
        self.assertFalse(result["closed"], result)
        self.assertEqual(result["missing"], "reviewed_patch",
                         "a worker_output must not be accepted as the reviewed patch")

    def test_a_reviewed_patch_in_the_worker_output_slot_does_not_close_the_hop(self):
        self._node("n-patch", "reviewed_patch")
        self._node("n-patch2", "reviewed_patch")
        self._node("n-result", "published_result")
        prov.record_derived_from(self.cx, 6954, 1, "n-patch", "n-result",
                                  "publishes", 1, t=PT0)
        # `reviews` edge into the patch, but from another reviewed_patch
        # rather than from a worker_output.
        prov.record_derived_from(self.cx, 6954, 1, "n-patch2", "n-patch",
                                  "reviews", 1, t=PT0)
        result = prov.lineage_chain(self.cx, 6954, 1, "n-result")
        self.assertFalse(result["closed"], result)
        self.assertEqual(result["missing"], "worker_output")


class SourceNotRetainedDropsTheRetentionHandles(unittest.TestCase):
    """MUTATION PG7-M26: stop setting `retention_ref=NULL, pack_digest=NULL`
    in `mark_source_not_retained`. The suite stayed GREEN -- the existing
    tests assert the flag, the state and the reason, and separately that the
    IDENTITY columns survive, but nothing asserts that the handles to the
    bytes that are gone are dropped. A row that still names a retention ref
    and a pack digest after expiry is a provenance record that says the
    bytes are still fetchable (ADR-0005: they are not).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-expiry-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.cx = prov.connect(self.state_root, 6955, 1)
        self.addCleanup(self.cx.close)
        prov.record_source_snapshot(
            self.cx, 6955, 1, "snap-r",
            _identity(retention_ref="refs/nightshift/snapshots/snap-r",
                      pack_digest="f" * 64))

    def test_expiry_clears_the_retention_ref_and_the_pack_digest(self):
        before = prov.get_source_snapshot(self.cx, 6955, 1, "snap-r")
        self.assertEqual(before["retention_ref"], "refs/nightshift/snapshots/snap-r")
        self.assertEqual(before["pack_digest"], "f" * 64)

        prov.mark_source_not_retained(self.cx, 6955, 1, "snap-r", "ttl", t=PT0)

        after = prov.get_source_snapshot(self.cx, 6955, 1, "snap-r")
        self.assertIsNone(after["retention_ref"],
                          "an expired snapshot must not still name a retention ref")
        self.assertIsNone(after["pack_digest"],
                          "an expired snapshot must not still name a pack digest")
        # Control: the identity half is untouched, so this is not just
        # 'expiry blanks the row'.
        self.assertEqual(after["commit_oid"], before["commit_oid"])
        self.assertEqual(after["tree_oid"], before["tree_oid"])


class ObjectFormatBackstopIsReachable(unittest.TestCase):
    """`_validate_source_snapshot_identity`'s `object_format` branch is
    documented as "a backstop against a schema regression rather than a live
    path", and MUTATION PG7-M05 (delete it) turns the 124-test suite GREEN,
    which is what an unreachable guard looks like from the outside.

    It is NOT dead code, and this test is the evidence: recreate the exact
    condition it names -- a `source_snapshot` table without the
    `CHECK(object_format IN ('sha1','sha256'))` constraint, which
    `CREATE TABLE IF NOT EXISTS` will happily leave in place -- and the
    branch fires. The guard is reachable; it simply has no live caller
    while the current schema holds.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-backstop-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_legacy_table_without_the_check_still_hits_the_python_guard(self):
        state_root = os.path.join(self.tmp, "state")
        os.makedirs(state_root, mode=0o700)
        root = prov.resolve_publication_root(state_root, 6956, 1)
        os.makedirs(root, mode=0o700, exist_ok=True)
        db = os.path.join(root, prov.PROVENANCE_DB_FILENAME)

        # A pre-existing table WITHOUT the object_format CHECK: exactly the
        # "schema regression" the docstring names. `CREATE TABLE IF NOT
        # EXISTS` in PROVENANCE_SCHEMA leaves it alone.
        legacy = sqlite3.connect(db)
        legacy.execute(
            "CREATE TABLE source_snapshot("
            "  snapshot_id TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL,"
            "  repository_id INTEGER NOT NULL, repository_fingerprint TEXT NOT NULL,"
            "  object_format TEXT NOT NULL, commit_oid TEXT NOT NULL,"
            "  tree_oid TEXT NOT NULL, observed_ref TEXT, retention_ref TEXT,"
            "  pack_digest TEXT, closure_manifest_digest TEXT, captured_at REAL NOT NULL,"
            "  synthetic INTEGER NOT NULL DEFAULT 0, synthetic_parent_commit_oid TEXT,"
            "  patch_digest TEXT, dirty_capture_method TEXT,"
            "  worktree_file_manifest_digest TEXT, included_ignored INTEGER NOT NULL DEFAULT 0,"
            "  retention_state TEXT NOT NULL DEFAULT 'retained',"
            "  source_not_retained INTEGER NOT NULL DEFAULT 0,"
            "  source_not_retained_at REAL, source_not_retained_reason TEXT,"
            "  key_version INTEGER)")
        legacy.commit()
        legacy.close()

        cx = prov.connect(state_root, 6956, 1)
        self.addCleanup(cx.close)
        prov.record_source_snapshot(cx, 6956, 1, "snap-legacy",
                                    _identity(object_format="sha3"))
        row = prov.get_source_snapshot(cx, 6956, 1, "snap-legacy")
        self.assertEqual(row["object_format"], "sha3",
                         "precondition: the legacy table really did accept it")

        with self.assertRaises(NSP.PublicationError) as ctx:
            NSP._validate_source_snapshot_identity(row)
        self.assertEqual(ctx.exception.reason_code, "source_snapshot_invalid")
        self.assertEqual(ctx.exception.details["field"], "object_format")


class PublicationLineageChainChecksEveryEdgeKind(PublicationGatewayTestCase):
    """MUTATION PG7-M21: drop the `!= "review"` half of
    `publication_lineage_chain`'s first hop test. The suite stayed GREEN --
    every existing walk test drives the real pipeline, which only ever
    writes the correct edge kinds, so the guard against a WRONG edge kind
    was never exercised on this side of the seam (provenance's own
    `_parent_via` has `test_edge_kind_must_match_the_expected_hop`; the
    publication half had no equivalent).
    """

    def test_an_attempt_whose_parent_edge_is_a_stage_does_not_close_the_chain(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"edge kind probe",
                                                 path="docs/ek.md")
        result = self._apply(fx, authorization_id=ap["authorization_id"],
                              stage_id=st["stage_id"], idempotency_key="idem-ek", t=T0)
        self.assertTrue(result["ok"])
        chain = NSP.publication_lineage_chain(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            attempt_id=result["attempt_id"])
        self.assertTrue(chain["closed"], "precondition: the real run closes")

        # Rewrite the attempt's ONLY parent edge with a legal-but-wrong
        # parent_kind: a `stage` parent where a `review` belongs. Written
        # around the writer on purpose -- this is the corrupt-row case the
        # walk's edge_kind test exists for.
        fx["cx"].execute(
            "DELETE FROM publication_lineage WHERE child_kind='attempt' AND child_id=?",
            (result["attempt_id"],))
        fx["cx"].execute(
            "INSERT INTO publication_lineage(tenant_id,repository_id,child_kind,child_id,"
            "parent_kind,parent_ref,source_not_retained,recorded_at) "
            "VALUES(?,?,'attempt',?,'stage',?,0,?)",
            (fx["tenant_id"], fx["repository_id"], result["attempt_id"],
             str(st["stage_id"]), T0))

        chain = NSP.publication_lineage_chain(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            attempt_id=result["attempt_id"])
        self.assertFalse(chain["closed"], chain)
        self.assertEqual(chain["missing"], "review",
                         "a `stage` parent must not be accepted as the review hop")



class ProvenanceSchemaBackstopsTheValidator(unittest.TestCase):
    """MUTATIONS PG7-M32 / PG7-M34 / PG7-M35. The 124-test suite stayed
    GREEN for all three:

      * PG7-M35 -- delete `CHECK(typeof(tenant_id)='integer' AND
        tenant_id>0)` from provenance's `source_snapshot`. The seam suite's
        `test_the_schema_backstops_a_forgotten_validator_call` covers
        publication's `publication_lineage` CHECK and nothing covers
        provenance's own, even though the schema comment above it is an
        explicit argument for why that column-level CHECK exists ("declaring
        a column INTEGER does NOT make SQLite store integers ... an INTEGER
        NOT NULL column accepts '../other_tenant' and stores it as text").
      * PG7-M32 / PG7-M34 -- make the `lineage_node` / `derived_from`
        immutability triggers never fire. `source_snapshot` and
        `closure_entry` each have a test that issues the forbidden UPDATE
        directly; the two lineage tables did not.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-schema-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.cx = prov.connect(self.state_root, 6957, 1)
        self.addCleanup(self.cx.close)

    def test_source_snapshot_tenant_id_check_refuses_a_path_shaped_string(self):
        for bad in ("../other_tenant", 0, -1, 1.5):
            with self.subTest(tenant_id=bad):
                with self.assertRaises(Exception) as ctx:
                    self.cx.execute(
                        "INSERT INTO source_snapshot(snapshot_id,tenant_id,repository_id,"
                        "repository_fingerprint,object_format,commit_oid,tree_oid,captured_at) "
                        "VALUES('s-'||?,?,1,'fp','sha1','b','c',1.0)", (str(bad), bad))
                self.assertIn("CHECK", str(ctx.exception).upper())

    def test_lineage_node_rows_are_immutable(self):
        prov.record_lineage_node(self.cx, 6957, 1, "n-imm", "worker_output",
                                  "run_id", "r1", t=PT0)
        with self.assertRaises(Exception) as ctx:
            self.cx.execute(
                "UPDATE lineage_node SET ref_id='r2' WHERE node_id='n-imm'")
        self.assertIn("immutable", str(ctx.exception))
        row = self.cx.execute(
            "SELECT ref_id FROM lineage_node WHERE node_id='n-imm'").fetchone()
        self.assertEqual(row["ref_id"], "r1")

    def test_derived_from_edges_are_immutable(self):
        prov.record_lineage_node(self.cx, 6957, 1, "n-a", "worker_output",
                                  "run_id", "r1", t=PT0)
        prov.record_lineage_node(self.cx, 6957, 1, "n-b", "reviewed_patch",
                                  "stage_id", "s1", t=PT0)
        prov.record_derived_from(self.cx, 6957, 1, "n-a", "n-b", "reviews", 1, t=PT0)
        with self.assertRaises(Exception) as ctx:
            self.cx.execute(
                "UPDATE derived_from SET content_carrying=0 "
                "WHERE parent_node_id='n-a' AND child_node_id='n-b'")
        self.assertIn("immutable", str(ctx.exception))
        row = self.cx.execute(
            "SELECT content_carrying FROM derived_from "
            "WHERE parent_node_id='n-a' AND child_node_id='n-b'").fetchone()
        self.assertEqual(row["content_carrying"], 1)


# ===========================================================================
# PG-8 -- dirty checkout: the synthetic snapshot must actually BE the
# dirty tree, and must be marked as synthetic without touching the customer.
# ===========================================================================

class DirtyCheckoutFixture(unittest.TestCase):
    """A real dirty checkout with every shape the copy path has to get
    right: a modified tracked file, an untracked file, a DELETED tracked
    file, an ignored file, an executable file and a symlink.

    Existing PG-8 coverage checks the customer repo is untouched, that the
    result dict says synthetic=1, and that two independent runs agree. None
    of it ever opens the synthetic commit and looks at what is inside it --
    which is why `PG8-M12` (drop untracked files) and `PG8-M27` (never copy
    the dirty tree at all) both left the suite green.
    """

    def _make_dirty_repo(self, tmp):
        repo = os.path.join(tmp, "customer")
        os.makedirs(repo)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "tester@example.invalid")
        _git(repo, "config", "user.name", "tester")
        for name, body in (("tracked.txt", "committed\n"),
                            ("doomed.txt", "will be deleted\n"),
                            ("run.sh", "#!/bin/sh\necho hi\n")):
            with open(os.path.join(repo, name), "w") as fh:
                fh.write(body)
        os.chmod(os.path.join(repo, "run.sh"), 0o755)
        with open(os.path.join(repo, ".gitignore"), "w") as fh:
            fh.write("secret.env\n")
        _git(repo, "add", "tracked.txt", "doomed.txt", "run.sh", ".gitignore")
        _git(repo, "commit", "-q", "-m", "init", env=_FIXED_ENV)

        # Now make it dirty, in five different ways at once.
        with open(os.path.join(repo, "tracked.txt"), "w") as fh:
            fh.write("MODIFIED IN THE WORKTREE\n")
        os.remove(os.path.join(repo, "doomed.txt"))
        with open(os.path.join(repo, "untracked.txt"), "w") as fh:
            fh.write("UNTRACKED DIRTY BYTES\n")
        with open(os.path.join(repo, "secret.env"), "w") as fh:
            fh.write("API_TOKEN=should-not-be-captured\n")
        os.symlink("tracked.txt", os.path.join(repo, "link.txt"))
        return repo

    def _capture(self, *, include_ignored=False, snapshot_id="snap-dirty", tag="x"):
        tmp = tempfile.mkdtemp(prefix=f"ns-695-dirty-{tag}-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = self._make_dirty_repo(tmp)
        mirror = os.path.join(tmp, "mirror")
        prov.acquire_mirror(repo, mirror)
        ws = os.path.join(tmp, "ws")
        prov.create_workspace(mirror, ws)
        result = prov.capture_dirty_snapshot(
            repo, mirror, ws, snapshot_id=snapshot_id,
            capture_time="1700000000 +0000", include_ignored=include_ignored)
        return {"tmp": tmp, "repo": repo, "mirror": mirror, "ws": ws,
                "result": result}

    # -- helpers that read the SYNTHETIC COMMIT out of the external mirror --

    def _tree_entries(self, fx):
        """`path -> mode` for every entry in the synthetic commit's tree,
        read out of the MIRROR (never the customer repo)."""
        out = _git(fx["mirror"], "ls-tree", "-r", fx["result"]["commit_oid"]).stdout
        entries = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            meta, _, path = line.partition("\t")
            mode, _, _ = meta.split(" ")
            entries[path] = mode
        return entries

    def _blob(self, fx, path):
        return _git(fx["mirror"], "cat-file", "blob",
                    f"{fx['result']['commit_oid']}:{path}").stdout


class SyntheticSnapshotContainsTheDirtyTree(DirtyCheckoutFixture):

    def test_the_modified_tracked_bytes_are_in_the_synthetic_commit(self):
        """MUTATION PG8-M27 -- skip the wipe+copy entirely so the synthetic
        commit is just the clone of HEAD. Every existing PG-8 test stayed
        green: the customer repo is still untouched, `synthetic` is still 1,
        and two runs still agree, because they agree on the WRONG tree."""
        fx = self._capture(tag="mod")
        self.assertEqual(self._blob(fx, "tracked.txt"),
                         "MODIFIED IN THE WORKTREE\n",
                         "the synthetic commit must carry the WORKTREE bytes, "
                         "not the committed ones")

    def test_the_untracked_file_is_in_the_synthetic_commit(self):
        """MUTATION PG8-M12 -- drop the `??` entries from build_copy_list."""
        fx = self._capture(tag="untracked")
        self.assertIn("untracked.txt", self._tree_entries(fx))
        self.assertEqual(self._blob(fx, "untracked.txt"), "UNTRACKED DIRTY BYTES\n")

    def test_a_file_deleted_in_the_worktree_is_absent_from_the_synthetic_commit(self):
        """MUTATIONS PG8-M13 (stop excluding deletions) and PG8-M20 (make
        `_wipe_workspace_worktree` a no-op, so the clone's own copy of the
        file survives into `git add -A`). Both left the suite green."""
        fx = self._capture(tag="deleted")
        self.assertNotIn("doomed.txt", self._tree_entries(fx),
                         "a file the operator deleted must not reappear in the "
                         "snapshot of their dirty tree")

    def test_ignored_files_are_not_copied_by_default(self):
        """MUTATION PG8-M14 -- copy ignored files unconditionally. This is
        the shape that quietly picks up `.env`/credential files nobody asked
        for; nothing in the suite noticed.

        Asserted against the WORKTREE MANIFEST, not only the commit tree:
        the manifest is what `copy_worktree_files` actually wrote into the
        external workspace, and it is the digest provenance freezes on the
        row. A file can reach the workspace (and the manifest) without
        reaching the commit -- see
        `test_include_ignored_does_not_reach_the_synthetic_commit` below."""
        fx = self._capture(tag="ignored-off")
        manifest_paths = {e["path"] for e in fx["result"]["worktree_manifest"]}
        self.assertNotIn("secret.env", manifest_paths,
                         "an ignored file must not be copied out of the "
                         "customer worktree unless it was asked for")
        self.assertNotIn("secret.env", self._tree_entries(fx))
        self.assertEqual(fx["result"]["included_ignored"], 0)
        self.assertFalse(os.path.exists(os.path.join(fx["ws"], "secret.env")),
                         "and it must not be left lying in the external workspace")

    def test_include_ignored_copies_the_file_and_records_it_in_the_manifest(self):
        """Sibling control for the test above: `include_ignored=True` is a
        real option that really changes the copy, so the default is a CHOICE
        rather than a capability that does not exist."""
        fx = self._capture(tag="ignored-on", include_ignored=True,
                            snapshot_id="snap-ign")
        manifest_paths = {e["path"] for e in fx["result"]["worktree_manifest"]}
        self.assertIn("secret.env", manifest_paths)
        self.assertEqual(fx["result"]["included_ignored"], 1)
        self.assertTrue(os.path.exists(os.path.join(fx["ws"], "secret.env")))

    @unittest.expectedFailure
    def test_include_ignored_does_not_reach_the_synthetic_commit(self):
        """FINDING (#695 audit, PG-8): `include_ignored=True` does not do
        what it says. `copy_worktree_files` puts the ignored file into the
        external workspace and `worktree_file_manifest_digest` is computed
        OVER IT -- but the workspace carries the customer's own copied
        `.gitignore`, so `capture_dirty_snapshot`'s plain `git add -A`
        (bin/nightshift_provenance.py, `_ws_git(["add", "-A"])`) skips it
        again. The synthetic commit therefore does NOT contain the file, and
        provenance freezes a manifest digest describing a tree the snapshot
        does not have.

        Observed, on a real fixture, unmutated:
            manifest paths  ['.gitignore', 'secret.env', 'tracked.txt', ...]
            synthetic tree  ['.gitignore', 'tracked.txt', ...]

        Marked `expectedFailure` rather than asserted-as-is on purpose: the
        assertion below states the CORRECT behaviour, so the day
        `add -A` gains `--force` for this mode this test reports an
        unexpected success instead of quietly continuing to pass a wrong
        claim. Do not "fix" it by inverting the assertion."""
        fx = self._capture(tag="ignored-commit", include_ignored=True,
                            snapshot_id="snap-ign2")
        self.assertIn("secret.env", self._tree_entries(fx))

    def test_the_executable_bit_survives_into_the_tree_and_the_manifest(self):
        """MUTATION PG8-M18 -- hardcode mode 100644 in the manifest."""
        fx = self._capture(tag="mode")
        self.assertEqual(self._tree_entries(fx)["run.sh"], "100755")
        modes = {e["path"]: e["mode"] for e in fx["result"]["worktree_manifest"]}
        self.assertEqual(modes["run.sh"], "100755")
        self.assertEqual(modes["tracked.txt"], "100644",
                         "control: not everything is marked executable")

    def test_a_symlink_is_captured_as_a_symlink_not_as_its_target(self):
        """MUTATION PG8-M19 -- take the `os.path.islink` branch out, so the
        link is dereferenced and the snapshot silently duplicates the target
        bytes under the link's name."""
        fx = self._capture(tag="symlink")
        self.assertEqual(self._tree_entries(fx)["link.txt"], "120000")
        self.assertEqual(self._blob(fx, "link.txt"), "tracked.txt")
        modes = {e["path"]: e["mode"] for e in fx["result"]["worktree_manifest"]}
        self.assertEqual(modes["link.txt"], "120000")

    def test_the_synthetic_commit_really_has_the_customer_head_as_its_parent(self):
        """MUTATION PG8-M26 -- build the commit with no `-p <base>` at all.
        The result dict still REPORTS `synthetic_parent_commit_oid`, and
        `test_synthetic_snapshot_is_marked_not_presented_as_upstream` only
        ever compares the dict to the row it just wrote from that same dict,
        so provenance recorded a parent the commit object does not have."""
        fx = self._capture(tag="parent")
        head = _git(fx["repo"], "rev-parse", "HEAD").stdout.strip()
        parents = _git(fx["mirror"], "rev-list", "--parents", "-n", "1",
                        fx["result"]["commit_oid"]).stdout.split()
        self.assertEqual(parents[1:], [head],
                         "the synthetic commit's real parent must be the "
                         "customer HEAD the row claims it is")
        self.assertEqual(fx["result"]["synthetic_parent_commit_oid"], head)

    def test_the_synthetic_commit_is_authored_by_nightshift_not_the_operator(self):
        """MUTATION PG8-M08 -- drop the FIXED GIT_AUTHOR_*/GIT_COMMITTER_*
        identity. Determinism survives (both runs pick up the same ambient
        `user.name`), so `test_determinism_...` stays green -- while every
        synthetic commit is now attributed to whoever happens to be running
        the process. §4's "AI never impersonates the human" is exactly this.
        """
        fx = self._capture(tag="identity")
        who = _git(fx["mirror"], "show", "-s",
                    "--format=%an|%ae|%cn|%ce", fx["result"]["commit_oid"]).stdout.strip()
        self.assertEqual(
            who, "nightshift|nightshift@invalid|nightshift|nightshift@invalid")

    def test_a_reused_workspace_is_wiped_before_the_next_capture(self):
        """MUTATION PG8-M20 -- make `_wipe_workspace_worktree` a no-op. The
        124-test suite stayed green, and so did the first version of this
        file: on a FRESH workspace the wipe has nothing to do, because
        `acquire_mirror`'s `git init --bare` leaves the mirror's HEAD on the
        ambient `init.defaultBranch` (here `master`) while the fetch created
        `refs/heads/main`, so `git clone --shared` checks nothing out.

        The wipe only ever matters on the path this test drives: the SAME
        external workspace used for a SECOND capture. Without it, a file
        that was dirty during capture 1 and is gone by capture 2 is still
        sitting in the workspace, `git add -A` picks it up, and snapshot 2
        claims content the customer's tree no longer has."""
        tmp = tempfile.mkdtemp(prefix="ns-695-reuse-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo = self._make_dirty_repo(tmp)
        mirror = os.path.join(tmp, "mirror")
        prov.acquire_mirror(repo, mirror)
        ws = os.path.join(tmp, "ws")
        prov.create_workspace(mirror, ws)

        first = prov.capture_dirty_snapshot(
            repo, mirror, ws, snapshot_id="reuse-1",
            capture_time="1700000000 +0000")
        self.assertIn("untracked.txt", _git(
            mirror, "ls-tree", "-r", "--name-only", first["commit_oid"]).stdout.split())
        self.assertTrue(os.path.exists(os.path.join(ws, "untracked.txt")),
                        "precondition: capture 1 really left it in the workspace")

        # The operator removes the untracked file and captures again into
        # the SAME workspace.
        os.remove(os.path.join(repo, "untracked.txt"))
        second = prov.capture_dirty_snapshot(
            repo, mirror, ws, snapshot_id="reuse-2",
            capture_time="1700000000 +0000")
        names = _git(mirror, "ls-tree", "-r", "--name-only",
                      second["commit_oid"]).stdout.split()
        self.assertNotIn("untracked.txt", names,
                         "the previous capture's file must not survive into the "
                         "next snapshot of the same workspace")
        manifest_paths = {e["path"] for e in second["worktree_manifest"]}
        self.assertNotIn("untracked.txt", manifest_paths)

    def test_the_manifest_digest_moves_when_the_dirty_bytes_move(self):
        """MUTATION PG8-M21 -- make `worktree_manifest_digest` a constant.
        The existing determinism test compares two runs over the SAME bytes,
        which a constant satisfies; nothing compared two runs over DIFFERENT
        bytes."""
        fx1 = self._capture(tag="digest-a", snapshot_id="snap-d1")
        with open(os.path.join(fx1["repo"], "untracked.txt"), "w") as fh:
            fh.write("DIFFERENT UNTRACKED DIRTY BYTES\n")
        mirror2 = os.path.join(fx1["tmp"], "mirror2")
        prov.acquire_mirror(fx1["repo"], mirror2)
        ws2 = os.path.join(fx1["tmp"], "ws2")
        prov.create_workspace(mirror2, ws2)
        second = prov.capture_dirty_snapshot(
            fx1["repo"], mirror2, ws2, snapshot_id="snap-d2",
            capture_time="1700000000 +0000")
        self.assertNotEqual(fx1["result"]["worktree_file_manifest_digest"],
                            second["worktree_file_manifest_digest"])
        self.assertNotEqual(fx1["result"]["commit_oid"], second["commit_oid"])
        self.assertNotEqual(fx1["result"]["patch_digest"], second["patch_digest"])


class DirtyDetectionAndPolicyEdges(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-695-dirtyedge-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_an_unknown_policy_string_is_refused_as_invalid_not_merely_refused(self):
        """MUTATION PG8-M02 -- delete the membership check, leaving only
        `policy != "copy_external"`. Still fail-closed, so nothing went red,
        but every unknown/misspelt policy then reports
        `dirty_checkout_refused` -- indistinguishable from a tenant that
        deliberately chose `refuse`. The two are triaged differently."""
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_dirty_policy_allows("copy_externl")
        self.assertEqual(ctx.exception.reason_code, "invalid_dirty_checkout_policy")
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_dirty_policy_allows(None)
        self.assertEqual(ctx.exception.reason_code, "invalid_dirty_checkout_policy")
        # Control: the deliberate refusal keeps its own distinct code.
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_dirty_policy_allows("refuse")
        self.assertEqual(ctx.exception.reason_code, "dirty_checkout_refused")

    def test_an_ignored_only_repository_is_not_dirty(self):
        """MUTATION PG8-M17 -- count `!!` entries as dirty. A repository
        whose only "change" is an ignored build artifact would then be
        captured as a dirty checkout, copying files the default policy
        deliberately excludes."""
        repo = make_repo(self.tmp, name="ignored-only")
        with open(os.path.join(repo, ".gitignore"), "w") as fh:
            fh.write("build/\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-q", "-m", "ignore build", env=_FIXED_ENV)
        os.makedirs(os.path.join(repo, "build"))
        with open(os.path.join(repo, "build", "out.o"), "w") as fh:
            fh.write("artifact\n")

        status = prov.detect_dirty(repo)
        self.assertFalse(status["dirty"],
                         "an ignored-only repository is not a dirty checkout")
        self.assertTrue(status["ignored"], "precondition: the artifact WAS seen")

    def test_a_bare_repository_is_refused_and_pins_an_unreachable_guard(self):
        """FINDING (#695 audit, PG-8): `capture_dirty_snapshot`'s
        `dirty_checkout_requires_worktree` guard is UNREACHABLE -- MUTATION
        PG8-M24 deletes it and the whole 124-test suite stays green, because
        nothing can ever reach it.

        `worktree_root` is None only for a bare repository
        (`resolve_customer_roots` sets it from `--show-toplevel` unless
        `--is-bare-repository` said true), and `capture_dirty_snapshot`
        calls `detect_dirty()` FIRST -- `git status` in a bare repository
        exits non-zero ("this operation must be run in a work tree"), so the
        function has already refused `footprint_probe_failed` two lines
        earlier.

        The system still fails CLOSED, which is why this test asserts the
        refusal it actually gets rather than the one the dead guard names.
        The guard should be removed or moved ABOVE `detect_dirty()`; leaving
        an unreachable guard in a security boundary reads as protection that
        is not there (#695, "Failure and empty states")."""
        src = make_repo(self.tmp, name="src")
        bare = os.path.join(self.tmp, "bare.git")
        subprocess.run(["git", "clone", "-q", "--bare", src, bare], check=True,
                        capture_output=True)
        mirror = os.path.join(self.tmp, "bare-mirror")
        prov.acquire_mirror(bare, mirror)
        ws = os.path.join(self.tmp, "bare-ws")
        prov.create_workspace(mirror, ws)

        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.capture_dirty_snapshot(bare, mirror, ws, snapshot_id="snap-bare",
                                         capture_time="1700000000 +0000")
        self.assertEqual(ctx.exception.reason_code, "footprint_probe_failed")

        # The evidence that the later guard is dead, not merely bypassed
        # here: the state it tests for IS the state of this repository.
        roots = prov.resolve_customer_roots(bare)
        self.assertTrue(roots["is_bare"])
        self.assertIsNone(roots["worktree_root"])

    def test_a_renamed_file_does_not_shift_the_porcelain_parse(self):
        """MUTATION PG8-M25 -- stop skipping the extra NUL-terminated
        original-path field a rename entry carries, and every entry after
        the rename is read one field out of step: the ORIGINAL path is
        parsed as if it were a status line, producing a garbage `code` and a
        truncated `path`."""
        repo = make_repo(self.tmp, name="renamed")
        with open(os.path.join(repo, "z_after.txt"), "w") as fh:
            fh.write("comes after the rename alphabetically\n")
        _git(repo, "mv", "a.txt", "b.txt")
        status = prov.detect_dirty(repo)
        codes = {e["path"]: e["code"] for e in status["tracked_or_untracked"]}
        # EXACT set, not `assertIn`: the defect's whole signature is one
        # EXTRA, phantom entry parsed out of the rename's original-path
        # field ("a.txt" read as code "a." + path "xt").
        self.assertEqual(set(codes), {"b.txt", "z_after.txt"}, codes)
        self.assertEqual(codes["z_after.txt"], "??")
        self.assertTrue(codes["b.txt"].startswith("R"), codes)
        copy_list = prov.build_copy_list(repo, status)
        self.assertIn("z_after.txt", copy_list)
        self.assertIn("b.txt", copy_list)
        self.assertNotIn("a.txt", copy_list,
                         "the pre-rename path must not be copied -- it is gone")

    def test_mirror_and_workspace_paths_must_be_absolute_normalised_directories(self):
        """MUTATION PG8-M28 -- make `_validate_abs_dir_arg` a pass-through.
        `acquire_mirror` and `create_workspace` are the two places this
        module runs `git` with a path as an ARGUMENT rather than as `cwd`,
        so they are the two places a relative or `..`-bearing path could
        escape the intended external location."""
        src = make_repo(self.tmp, name="absrc")
        # Containment for the mutation run itself: with the validator
        # disabled, `git init --bare relative/mirror` resolves against the
        # PROCESS cwd -- which is the repository's own tests/ directory.
        # Injecting PG8-M28 while cwd is the checkout leaves `tests/relative/`
        # behind, so the probe runs from inside its own tmpdir.
        previous_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, previous_cwd)
        for bad, reason in ((os.path.join(self.tmp, "m", "..", "m"), "invalid_mirror_path"),
                             ("relative/mirror", "invalid_mirror_path"),
                             ("", "invalid_mirror_path")):
            with self.subTest(mirror_path=bad):
                with self.assertRaises(prov.ProvenanceError) as ctx:
                    prov.acquire_mirror(src, bad)
                self.assertEqual(ctx.exception.reason_code, reason)
        good_mirror = os.path.join(self.tmp, "goodmirror")
        prov.acquire_mirror(src, good_mirror)
        for bad in (os.path.join(self.tmp, "w", "..", "w"), "relative/ws", ""):
            with self.subTest(workspace_path=bad):
                with self.assertRaises(prov.ProvenanceError) as ctx:
                    prov.create_workspace(good_mirror, bad)
                self.assertEqual(ctx.exception.reason_code, "invalid_workspace_path")


if __name__ == "__main__":
    unittest.main()
