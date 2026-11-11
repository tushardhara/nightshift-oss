"""Issue #694 -- the publication <-> provenance seam.

bin/nightshift_publication.py VALIDATED a `source_snapshot` (it refused one
missing `repository_fingerprint`, and refused `closure_incomplete`) while
`source_snapshot` rows are PRODUCED by bin/nightshift_provenance.py, in a
different database, by a module publication never imported. Publication was
validating an object nothing in its own process could create, and the two
modules used incompatible tenant_id types (TEXT here, INTEGER there), so the
same tenant id was accepted by one and refused by the other.

This file is the test that could not even be EXPRESSED before the fix:

  * `stage()` took a dict, so there was no snapshot_id to present and no
    way for tenant B to present tenant A's snapshot at all.
  * `prov.connect(tenant_id="tenant-a")` refused `invalid_tenant_id` while
    `pub.stage(tenant_id="tenant-a")` accepted it -- one process could not
    hold one tenant id that both modules agreed on.
  * `pub.resolve_publication_root` took (state_root, repository_id) and put
    publication.db under `publication/<repository_id>/`, nowhere near
    provenance.db, so there was nothing to ATTACH.
  * `lineage_chain_from_commit` did not exist, and could not have: the
    `input_snapshot` lineage parent was `'snapshot:' + sha256(<the caller's
    dict>)`, a reference to nothing.

Four cases, all against real `git init --bare` targets and a real SQLite
publication.db + provenance.db on disk. No mocks, no stubs, no
monkeypatching.

  1 POSITIVE  a real provenance snapshot -> stage -> review -> approve ->
              apply lands a real commit, and ONE query walks that landed
              commit_oid back across the file boundary to the snapshot's
              own commit_oid and repository_fingerprint.
  2 NEGATIVE  (the one that counts) tenant B, holding a genuine unexpired
              unconsumed authorization of its own, presents tenant A's
              snapshot_id. Refused `source_snapshot_not_found`; B's
              authorization is still live; the destination ref is
              byte-identical to the pre-attempt base sha.
  3 IDENTITY  `source_snapshot_incomplete` still fires -- against a row
              provenance itself recorded, because NOT NULL does not stop
              the empty string.
  4 CLOSURE   `closure_incomplete` fires when the closure_entry rows do not
              cover the snapshot's own frozen closure_manifest_digest.
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

import nightshift_publication as pub  # noqa: E402 -- see sys.path insert above
import nightshift_provenance as prov  # noqa: E402

T0 = 1_700_000_000.0
REF = "refs/heads/main"

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               GIT_TERMINAL_PROMPT="0")
COMMIT_ENV = dict(
    GIT_AUTHOR_NAME="seed", GIT_AUTHOR_EMAIL="seed@test.invalid",
    GIT_COMMITTER_NAME="seed", GIT_COMMITTER_EMAIL="seed@test.invalid",
    GIT_AUTHOR_DATE="@1600000000 +0000", GIT_COMMITTER_DATE="@1600000000 +0000")

IDENTITY = {
    "repository_fingerprint": "sha256:" + "a" * 64,
    "object_format": "sha1",
    "commit_oid": "b" * 40,
    "tree_oid": "c" * 40,
}


def _git(git_dir, args, *, input_bytes=None, env_extra=None, check=True):
    env = dict(GIT_ENV)
    env.update(env_extra or {})
    result = subprocess.run(["git", "--git-dir", git_dir] + list(args),
                             input=input_bytes, capture_output=True, env=env, timeout=30)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr.decode('utf-8', 'replace')}")
    return result


def _init_bare_with_seed(base_dir, name="dest.git"):
    """A real bare destination with one real commit on REF, built with
    plumbing only (the destination has no work tree)."""
    git_dir = os.path.join(base_dir, name)
    subprocess.run(["git", "init", "--quiet", "--bare", git_dir],
                    check=True, capture_output=True, env=GIT_ENV)
    index_path = os.path.join(base_dir, f"{name}.index")
    idx_env = {"GIT_INDEX_FILE": index_path}
    blob = _git(git_dir, ["hash-object", "-w", "--stdin"],
                 input_bytes=b"seed\n").stdout.strip().decode("ascii")
    _git(git_dir, ["update-index", "--add", "--cacheinfo", f"100644,{blob},README.md"],
          env_extra=idx_env)
    tree = _git(git_dir, ["write-tree"], env_extra=idx_env).stdout.strip().decode("ascii")
    commit = _git(git_dir, ["commit-tree", tree, "-m", "seed"],
                   env_extra=COMMIT_ENV).stdout.strip().decode("ascii")
    _git(git_dir, ["update-ref", REF, commit])
    os.remove(index_path)
    return git_dir, commit


def _ref_sha(git_dir, ref=REF):
    result = _git(git_dir, ["rev-parse", "--verify", ref], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip().decode("ascii")


class SeamTestCase(unittest.TestCase):
    """One state_root, N tenants. Every tenant gets its own provenance.db
    AND its own publication.db, in the SAME tenant-scoped directory --
    which is the layout change #694 makes, and is why both handles below
    come out of one call to `prov.resolve_publication_root`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-seam-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.repository_id = 7

    def _open_tenant(self, tenant_id, *, git_dir=None, reviewer="eng_bob",
                     token=b"tok-secret", path_allowlist=("docs/**",)):
        prov_cx = prov.connect(self.state_root, tenant_id, self.repository_id)
        self.addCleanup(prov_cx.close)
        root = pub.resolve_publication_root(self.state_root, tenant_id, self.repository_id)
        cx = pub.connect(root)
        self.addCleanup(cx.close)
        pub.policy_set(cx, tenant_id=tenant_id, repository_id=self.repository_id,
                        mode="zero_nightshift_file", path_allowlist=list(path_allowlist),
                        target_git_dir=git_dir, target_ref=REF if git_dir else None,
                        key_version=None, t=T0)
        pub.register_reviewer(cx, tenant_id=tenant_id, reviewer_identity=reviewer,
                               token_bytes=token, t=T0)
        return {"tenant_id": tenant_id, "prov_cx": prov_cx, "cx": cx, "root": root,
                "reviewer": reviewer, "token": token, "git_dir": git_dir}

    def _record_snapshot(self, tn, snapshot_id, **overrides):
        fields = dict(IDENTITY, captured_at=T0)
        fields.update(overrides)
        return prov.record_source_snapshot(
            tn["prov_cx"], tn["tenant_id"], self.repository_id, snapshot_id, fields)

    def _stage_review_approve(self, tn, *, snapshot_id, content, path, base_sha,
                               producer="eng_alice", run="run-1", reviewer_run="rev-1"):
        st = pub.stage(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                        content_bytes=content, destination_path=path, classification="report",
                        producer_identity=producer,
                        producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], producer), producer_run_id=run,
                        snapshot_id=snapshot_id, t=T0)
        rv = pub.review(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                         stage_id=st["stage_id"], reviewer_identity=tn["reviewer"],
                         reviewer_run_id=reviewer_run, token_bytes=tn["token"],
                         verdict="approve", t=T0)
        ap = pub.approve(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                          stage_id=st["stage_id"], review_id=rv["review_id"],
                          reviewer_identity=tn["reviewer"], reviewer_run_id=reviewer_run,
                          token_bytes=tn["token"], expected_base_sha=base_sha,
                          ttl=3600, t=T0)
        return st, rv, ap


# ---------------------------------------------------------------------------
# 1. The AC-6 integration test: ONE flow, snapshot -> stage -> review ->
#    approve -> apply, asserting the landed commit and the closed chain.
# ---------------------------------------------------------------------------

class SnapshotToLandedCommitIsOneFlow(SeamTestCase):

    def test_snapshot_stage_review_approve_apply_lands_and_the_chain_closes(self):
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)

        # A REAL provenance snapshot, with a REAL predecessor edge, so the
        # walk below has something to cross the file boundary INTO.
        prior = self._record_snapshot(tn, "snap-0", commit_oid="d" * 40, tree_oid="e" * 40)
        snapshot_id = self._record_snapshot(tn, "snap-1")
        prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                  prior, "source_snapshot", "snapshot", prior, t=T0)
        prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                  snapshot_id, "source_snapshot", "snapshot", snapshot_id, t=T0)
        prov.record_derived_from(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                  prior, snapshot_id, "replaces", 0, t=T0)

        st, rv, ap = self._stage_review_approve(
            tn, snapshot_id=snapshot_id, content=b"published body\n",
            path="docs/report.md", base_sha=base_sha)
        result = pub.apply_publication(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
            idempotency_key="seam-idem-1", state_root=tn["root"], t=T0)

        # The commit really landed -- confirmed by git, outside the gateway.
        self.assertEqual(result["status"], "applied")
        self.assertEqual(_ref_sha(git_dir), result["commit_oid"])
        self.assertNotEqual(result["commit_oid"], base_sha)
        self.assertEqual(
            _git(git_dir, ["show", f"{result['commit_oid']}:docs/report.md"]).stdout,
            b"published body\n")

        # ONE query, landed commit -> the exact source snapshot, across the
        # publication.db / provenance.db boundary.
        chain = pub.lineage_chain_from_commit(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            commit_oid=result["commit_oid"])
        self.assertTrue(chain["closed"], chain)
        self.assertEqual(chain["snapshot_id"], snapshot_id)
        self.assertEqual(chain["source_commit_oid"], IDENTITY["commit_oid"])
        self.assertEqual(chain["repository_fingerprint"], IDENTITY["repository_fingerprint"])
        self.assertFalse(chain["source_not_retained"])
        # The walk really crossed the seam: it reached the PRIOR snapshot,
        # which exists only in provenance.db's derived_from graph.
        self.assertEqual(
            chain["chain"],
            [f"pub:attempt:{result['attempt_id']}", f"pub:review:{rv['review_id']}",
             f"pub:stage:{st['stage_id']}", snapshot_id, prior])

        # The pre-#694 walker agrees and now names a REAL snapshot id (it
        # used to name sha256 of a dict that was never a row anywhere).
        legacy = pub.publication_lineage_chain(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            attempt_id=result["attempt_id"])
        self.assertTrue(legacy["closed"], legacy)
        self.assertEqual([link["kind"] for link in legacy["chain"]],
                          ["attempt", "review", "stage", "input_snapshot"])
        self.assertEqual(legacy["chain"][3]["id"], snapshot_id)
        self.assertIsNotNone(prov.get_source_snapshot(
            tn["prov_cx"], tn["tenant_id"], self.repository_id, legacy["chain"][3]["id"]))

    def test_the_attachment_is_read_only_and_scoped_to_this_tenants_file(self):
        """The seam is a READ. Publication never writes provenance, which is
        what makes the absence of a cross-file transaction safe under WAL."""
        tn = self._open_tenant(1001)
        self._record_snapshot(tn, "snap-1")
        attached = {row[1]: row[2] for row in
                    tn["cx"].execute("PRAGMA database_list").fetchall()}
        self.assertEqual(
            os.path.realpath(attached["prov"]),
            os.path.realpath(os.path.join(tn["root"], prov.PROVENANCE_DB_FILENAME)))
        with self.assertRaises(Exception) as ctx:
            tn["cx"].execute(
                "INSERT INTO prov.source_snapshot(snapshot_id,tenant_id,repository_id,"
                "repository_fingerprint,object_format,commit_oid,tree_oid,captured_at) "
                "VALUES('smuggled',1001,7,'f','sha1','x','y',1)")
        self.assertIn("readonly", str(ctx.exception))

    def test_connect_refuses_when_the_provenance_database_is_absent(self):
        """The two-islands defect is now structurally unreachable: there is
        no way to stand publication up without the database that produces
        the snapshots it validates."""
        root = pub.resolve_publication_root(self.state_root, 1001, self.repository_id)
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.connect(root)
        self.assertEqual(ctx.exception.reason_code, "provenance_db_missing")
        self.assertEqual(ctx.exception.exit_code, 3)


# ---------------------------------------------------------------------------
# 2. The negative that counts.
# ---------------------------------------------------------------------------

class TenantBCannotPresentTenantAsSnapshot(SeamTestCase):

    def test_a_live_authorization_does_not_let_tenant_b_reach_tenant_as_snapshot(self):
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tenant_a = self._open_tenant(1001, git_dir=git_dir)
        tenant_b = self._open_tenant(1002, git_dir=git_dir)

        alpha = self._record_snapshot(tenant_a, "snap-alpha")
        beta = self._record_snapshot(tenant_b, "snap-beta")

        # Tenant B is NOT an outsider: it holds a genuine, unexpired,
        # unconsumed authorization over its own content, issued by its own
        # registered reviewer. The refusal below is therefore about the
        # snapshot, not about B lacking capability.
        st_b, rv_b, ap_b = self._stage_review_approve(
            tenant_b, snapshot_id=beta, content=b"tenant b body\n",
            path="docs/b.md", base_sha=base_sha)
        live = tenant_b["cx"].execute(
            "SELECT * FROM publication_authorizations WHERE id=?",
            (ap_b["authorization_id"],)).fetchone()
        self.assertIsNone(live["consumed_at"])
        self.assertIsNone(live["revoked_at"])
        self.assertGreater(live["expires_at"], T0)

        head_before = _ref_sha(git_dir)
        self.assertEqual(head_before, base_sha)

        with self.assertRaises(pub.PublicationError) as ctx:
            pub.stage(tenant_b["cx"], tenant_id=tenant_b["tenant_id"],
                       repository_id=self.repository_id,
                       content_bytes=b"tenant b body via tenant a's provenance\n",
                       destination_path="docs/stolen.md", classification="report",
                       producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tenant_b["cx"], tenant_b["tenant_id"], "eng_alice"), producer_run_id="run-steal",
                       snapshot_id=alpha, t=T0)
        self.assertEqual(ctx.exception.reason_code, "source_snapshot_not_found")
        self.assertEqual(ctx.exception.exit_code, 3)

        # No oracle: the refusal is identical for an id that never existed,
        # so B cannot probe for A's snapshot ids.
        with self.assertRaises(pub.PublicationError) as ctx2:
            pub.stage(tenant_b["cx"], tenant_id=tenant_b["tenant_id"],
                       repository_id=self.repository_id, content_bytes=b"x\n",
                       destination_path="docs/nope.md", classification="report",
                       producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tenant_b["cx"], tenant_b["tenant_id"], "eng_alice"), producer_run_id="run-probe",
                       snapshot_id="snap-never-existed", t=T0)
        self.assertEqual(ctx2.exception.reason_code, ctx.exception.reason_code)

        # Physical separation, not merely a WHERE clause: tenant B's own
        # read-only attachment cannot see the row at all.
        self.assertEqual(
            tenant_b["cx"].execute(
                "SELECT COUNT(*) AS n FROM prov.source_snapshot WHERE snapshot_id=?",
                (alpha,)).fetchone()["n"], 0)

        # B's authorization is untouched -- the refusal did not burn it...
        after = tenant_b["cx"].execute(
            "SELECT * FROM publication_authorizations WHERE id=?",
            (ap_b["authorization_id"],)).fetchone()
        self.assertIsNone(after["consumed_at"])
        self.assertIsNone(after["revoked_at"])

        # ...and nothing was written to the destination repository.
        self.assertEqual(_ref_sha(git_dir), base_sha)
        self.assertEqual(
            int(_git(git_dir, ["rev-list", "--count", REF]).stdout.strip()), 1)
        self.assertEqual(
            tenant_b["cx"].execute(
                "SELECT COUNT(*) AS n FROM publication_stage WHERE destination_path='docs/stolen.md'"
            ).fetchone()["n"], 0)

        # Positive control: B's own authorization really was usable, so the
        # refusal above was guarding a real capability, not an inert one.
        applied = pub.apply_publication(
            tenant_b["cx"], tenant_id=tenant_b["tenant_id"], repository_id=self.repository_id,
            authorization_id=ap_b["authorization_id"], stage_id=st_b["stage_id"],
            idempotency_key="seam-b-1", state_root=tenant_b["root"], t=T0)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(_ref_sha(git_dir), applied["commit_oid"])

    def test_the_two_modules_accept_and_refuse_the_same_tenant_id(self):
        """AC1, made true by construction rather than by two validators
        staying in sync: publication delegates to provenance's validator and
        re-raises with the same reason_code. Before #694, 'tenant-a' was
        REFUSED by prov.connect and ACCEPTED by pub.stage."""
        for bad in ("tenant-a", "../other_tenant", "1", 0, -1, True, 1.5, None):
            with self.subTest(tenant_id=bad):
                with self.assertRaises(prov.ProvenanceError) as p_ctx:
                    prov.validate_tenant_id(bad)
                with self.assertRaises(pub.PublicationError) as b_ctx:
                    pub._validate_tenant_id(bad)
                self.assertEqual(b_ctx.exception.reason_code, p_ctx.exception.reason_code)
                self.assertEqual(b_ctx.exception.reason_code, "invalid_tenant_id")

    def test_the_schema_backstops_a_forgotten_validator_call(self):
        """Second line only -- see the schema comment. It refuses the values
        an INTEGER-declared column would otherwise store happily."""
        tn = self._open_tenant(1001)
        for bad in ("../other_tenant", 0, -1, 1.5):
            with self.subTest(tenant_id=bad):
                with self.assertRaises(Exception) as ctx:
                    tn["cx"].execute(
                        "INSERT INTO publication_lineage(tenant_id,repository_id,child_kind,"
                        "child_id,parent_kind,parent_ref,source_not_retained,recorded_at) "
                        "VALUES(?,1,'stage',1,'input_snapshot','s',0,1)", (bad,))
                self.assertIn("CHECK", str(ctx.exception).upper())


# ---------------------------------------------------------------------------
# 3 + 4. The two refusals publication used to run against a caller's dict,
#        now run against provenance's own rows.
# ---------------------------------------------------------------------------

class RefusalsRunAgainstRealProvenanceRows(SeamTestCase):

    def test_source_snapshot_incomplete_fires_on_a_provenance_recorded_row(self):
        """AC3: reachable against a row provenance ITSELF produced.
        `record_source_snapshot` accepts repository_fingerprint='' because
        NOT NULL does not stop the empty string -- so this refusal is a live
        path, not a decorative one."""
        tn = self._open_tenant(1001)
        blank = self._record_snapshot(tn, "snap-blank-identity", repository_fingerprint="")
        row = prov.get_source_snapshot(tn["prov_cx"], tn["tenant_id"], self.repository_id, blank)
        self.assertEqual(row["repository_fingerprint"], "",
                          "the defective row really is in provenance.db")
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.stage(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                       content_bytes=b"body\n", destination_path="docs/a.md",
                       classification="report", producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], "eng_alice"),
                       producer_run_id="run-1", snapshot_id=blank, t=T0)
        self.assertEqual(ctx.exception.reason_code, "source_snapshot_incomplete")
        self.assertEqual(ctx.exception.details["missing"], ["repository_fingerprint"])
        self.assertEqual(
            tn["cx"].execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"], 0)

    def test_closure_incomplete_fires_when_entries_do_not_cover_the_frozen_manifest(self):
        """AC3's other half: completeness is DISCOVERED from provenance's
        own rows against a digest frozen on the immutable snapshot row --
        there is no `closure_required_paths` a caller can decline to
        declare."""
        tn = self._open_tenant(1001)
        manifest = [
            {"kind": "submodule", "path": "vendor/sub", "oid": "1" * 40},
            {"kind": "lfs", "path": "assets/large.bin", "oid": "2" * 40},
        ]
        snapshot = self._record_snapshot(
            tn, "snap-closure",
            closure_manifest_digest=prov.closure_manifest_digest(manifest))
        prov.record_closure_entry(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                   snapshot, "submodule", "vendor/sub", "1" * 40, t=T0)

        with self.assertRaises(pub.PublicationError) as ctx:
            pub.stage(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                       content_bytes=b"body\n", destination_path="docs/a.md",
                       classification="report", producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], "eng_alice"),
                       producer_run_id="run-1", snapshot_id=snapshot, t=T0)
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["covered_paths"], ["vendor/sub"])
        self.assertEqual(
            tn["cx"].execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"], 0)

        # Persist the missing entry and the SAME snapshot now publishes --
        # the refusal tracked the rows, not a caller's claim.
        prov.record_closure_entry(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                   snapshot, "lfs", "assets/large.bin", "2" * 40, t=T0)
        staged = pub.stage(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            content_bytes=b"body\n", destination_path="docs/a.md", classification="report",
            producer_identity="eng_alice",
            producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], "eng_alice"), producer_run_id="run-1",
            snapshot_id=snapshot, t=T0)
        self.assertEqual(staged["status"], "staged")


# ---------------------------------------------------------------------------
# Migration: no silent coexistence of a TEXT-tenant and an INTEGER-tenant
# publication.db (#694: "silent coexistence of both types is not acceptable").
# ---------------------------------------------------------------------------

class NoSilentCoexistenceWithThePre694Layout(SeamTestCase):

    def test_a_leftover_pre_694_database_refuses_rather_than_being_ignored(self):
        legacy_dir = os.path.join(self.state_root, "publication", str(self.repository_id))
        os.makedirs(legacy_dir, mode=0o700)
        legacy_db = os.path.join(legacy_dir, pub.PUBLICATION_DB_FILENAME)
        with open(legacy_db, "wb") as fh:
            fh.write(b"")
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.resolve_publication_root(self.state_root, 1001, self.repository_id)
        self.assertEqual(ctx.exception.reason_code, "publication_legacy_root_present")
        self.assertEqual(ctx.exception.details["legacy_path"], legacy_db)

        os.remove(legacy_db)
        root = pub.resolve_publication_root(self.state_root, 1001, self.repository_id)
        self.assertTrue(root.endswith(os.path.join("tenants", "1001", "repos",
                                                    str(self.repository_id))))

    def test_a_populated_version_0_database_refuses_rather_than_being_adopted(self):
        """Migration (c): pre-#694 rows are DROPPED, never migrated -- but
        never SILENTLY. A version-0 file with rows in a tenant-bearing table
        stops the gateway and names the table."""
        import sqlite3
        tn = self._open_tenant(1001)
        tn["cx"].close()
        db = os.path.join(tn["root"], pub.PUBLICATION_DB_FILENAME)
        raw = sqlite3.connect(db, isolation_level=None)
        raw.execute("PRAGMA user_version = 0")
        raw.close()

        with self.assertRaises(pub.PublicationError) as ctx:
            pub.connect(tn["root"])
        self.assertEqual(ctx.exception.reason_code, "publication_schema_pre_694")
        self.assertIn("publication_policy",
                       [entry["table"] for entry in ctx.exception.details["populated"]])


# ---------------------------------------------------------------------------
# Round 2 (#694 verification): the two defects found in the ONE-graph claim.
#
#   (a) NAMESPACE COLLISION. Publication node ids are `pub:<kind>:<id>` but
#       an `input_snapshot` parent entered the unified view as the raw
#       snapshot_id, and provenance happily minted the id `pub:stage:1`.
#       A stage built from a snapshot so named aliased publication's stage 1
#       and `lineage_chain_from_commit` reported a DIFFERENT snapshot's
#       commit_oid with closed=True -- silently the wrong source.
#   (b) SELF-LOOP + UNBOUNDED WALK. The same alias made a self-edge, and the
#       recursive CTE had no bound: `UNION` dedupes on (node_id, depth) and
#       depth always differs, so the walk never converged.
# ---------------------------------------------------------------------------

class TheUnifiedNodeIdNamespaceIsDisjoint(SeamTestCase):

    def test_provenance_refuses_to_mint_an_id_in_publications_namespace(self):
        """The root of (a): the id vocabulary is shared, so it needs ONE
        owner per prefix. Provenance never needed to mint a `pub:` id --
        publication's rows live in publication.db and are read from there."""
        tn = self._open_tenant(1001)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.validate_snapshot_id("pub:stage:1")
        self.assertEqual(ctx.exception.reason_code, "reserved_node_id_namespace")

        with self.assertRaises(prov.ProvenanceError) as ctx:
            self._record_snapshot(tn, "pub:stage:1")
        self.assertEqual(ctx.exception.reason_code, "reserved_node_id_namespace")
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                      "pub:attempt:1", "published_result", "attempt", "1", t=T0)
        self.assertEqual(ctx.exception.reason_code, "reserved_node_id_namespace")

        # And nothing else moved: an ordinary id is still accepted.
        self.assertEqual(prov.validate_snapshot_id("snap-ordinary"), "snap-ordinary")
        self.assertEqual(self._record_snapshot(tn, "snap-ordinary"), "snap-ordinary")

    def test_stage_refuses_a_reserved_snapshot_id_with_the_destination_unmoved(self):
        """Publication's half of the same rule, on the side that would
        actually store the aliasing edge."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.stage(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                       content_bytes=b"body\n", destination_path="docs/a.md",
                       classification="report", producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], "eng_alice"),
                       producer_run_id="run-1", snapshot_id="pub:stage:1", t=T0)
        self.assertEqual(ctx.exception.reason_code, "reserved_node_id_namespace")
        self.assertEqual(_ref_sha(git_dir), base_sha)
        self.assertEqual(
            tn["cx"].execute("SELECT COUNT(*) FROM publication_stage").fetchone()[0], 0)

    def test_the_lineage_writer_refuses_a_reserved_parent_and_a_self_edge(self):
        """`record_publication_lineage` is the only writer of the table, so
        both structural rules belong there rather than in each caller.
        `lineage_self_loop_refused` is the reason code
        `prov.record_derived_from` already used for the same shape."""
        tn = self._open_tenant(1001)
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.record_publication_lineage(
                tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                child_kind="stage", child_id=1,
                parent_kind="input_snapshot", parent_ref="pub:stage:1", t=T0)
        self.assertEqual(ctx.exception.reason_code, "reserved_node_id_namespace")
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.record_publication_lineage(
                tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                child_kind="stage", child_id=1, parent_kind="stage", parent_ref="1", t=T0)
        self.assertEqual(ctx.exception.reason_code, "lineage_self_loop_refused")
        self.assertEqual(
            tn["cx"].execute("SELECT COUNT(*) FROM publication_lineage").fetchone()[0], 0)


class TheCrossFileWalkIsBounded(SeamTestCase):

    def _landed_commit(self, tn, git_dir, base_sha):
        snapshot = self._record_snapshot(tn, "snap-ordinary")
        st, rv, ap = self._stage_review_approve(
            tn, snapshot_id=snapshot, content=b"published body\n",
            path="docs/report.md", base_sha=base_sha)
        applied = pub.apply_publication(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
            idempotency_key="bounded-1", state_root=tn["root"], t=T0)
        self.assertEqual(applied["status"], "applied")
        return applied["commit_oid"], st["stage_id"]

    def test_a_self_edge_written_around_the_validators_does_not_hang_the_walk(self):
        """Defect (b), from the only direction that can still produce the
        row: raw SQL into the table. The walk must TERMINATE and say it was
        cut short, and must still not report a snapshot it did not reach."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        commit_oid, stage_id = self._landed_commit(tn, git_dir, base_sha)
        tn["cx"].execute(
            "INSERT INTO publication_lineage(tenant_id,repository_id,child_kind,child_id,"
            "parent_kind,parent_ref,source_not_retained,recorded_at) "
            "VALUES(?,?,'stage',?,'stage',?,0,?)",
            (tn["tenant_id"], self.repository_id, stage_id, str(stage_id), T0))
        self.assertEqual(tn["cx"].execute(
            "SELECT COUNT(*) FROM lineage_edge WHERE parent_node_id=child_node_id"
        ).fetchone()[0], 1, "the self-edge really is in the unified view")

        chain = pub.lineage_chain_from_commit(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            commit_oid=commit_oid)
        self.assertTrue(chain["truncated"], chain)
        self.assertEqual(chain["snapshot_id"], "snap-ordinary")
        self.assertEqual(len(chain["chain"]), len(set(chain["chain"])),
                          "one node, one entry")

    def test_a_walk_cut_short_before_the_snapshot_reports_it_rather_than_closing(self):
        """A bound must never read as "there is nothing there": if the walk
        stops before a snapshot, `closed` is False and `truncated` is True."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        commit_oid, stage_id = self._landed_commit(tn, git_dir, base_sha)
        # Detach the real snapshot edge and put a long stage chain in its
        # place, longer than the depth bound.
        tn["cx"].execute("DELETE FROM publication_lineage WHERE parent_kind='input_snapshot'")
        rows = [(tn["tenant_id"], self.repository_id, stage_id, str(9000))]
        for i in range(pub.LINEAGE_WALK_DEPTH_LIMIT + 10):
            rows.append((tn["tenant_id"], self.repository_id, 9000 + i, str(9000 + i + 1)))
        tn["cx"].executemany(
            "INSERT INTO publication_lineage(tenant_id,repository_id,child_kind,child_id,"
            "parent_kind,parent_ref,source_not_retained,recorded_at) "
            "VALUES(?,?,'stage',?,'stage',?,0,%r)" % T0, rows)
        chain = pub.lineage_chain_from_commit(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            commit_oid=commit_oid)
        self.assertTrue(chain["truncated"], chain)
        self.assertFalse(chain["closed"], chain)
        self.assertIsNone(chain["snapshot_id"])
        # It stopped at the depth bound itself -- depths 0..LIMIT -- rather
        # than at the end of a graph that happened to be short.
        self.assertEqual(len(chain["chain"]), pub.LINEAGE_WALK_DEPTH_LIMIT + 1)

    def test_an_id_claimed_by_both_graphs_refuses_instead_of_answering(self):
        """The last line: if a `pub:` node ever also exists in provenance
        -- now only reachable by writing around both validators -- the id
        has two meanings, and a walk that picked one would be exactly the
        silent wrong answer this replaces."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        commit_oid, stage_id = self._landed_commit(tn, git_dir, base_sha)
        tn["prov_cx"].execute(
            "INSERT INTO lineage_node(node_id,tenant_id,repository_id,node_kind,ref_kind,"
            "ref_id,content_digest,created_at) VALUES(?,?,?,'source_snapshot','snapshot',"
            "'x',NULL,?)",
            (f"pub:stage:{stage_id}", tn["tenant_id"], self.repository_id, T0))
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.lineage_chain_from_commit(
                tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                commit_oid=commit_oid)
        self.assertEqual(ctx.exception.reason_code, "lineage_namespace_collision")
        self.assertEqual(ctx.exception.details["node_ids"], [f"pub:stage:{stage_id}"])


# ---------------------------------------------------------------------------
# Round 3 (#694 verification): the ONE-graph claim rested on an UNENFORCED
# naming convention, and provenance's own convention contradicted it.
#
# The unified view used to close the seam with
#   FROM prov.derived_from d JOIN prov.lineage_node n ON n.node_id = d.parent_node_id
# and compare that node_id to publication's `input_snapshot` parent, which
# IS the snapshot_id. So `pub:stage:1 -> snap-1` continued into provenance
# only if some lineage node happened to be NAMED `snap-1`. Nothing required
# it: `prov.record_lineage_node` accepts any node_id, and provenance's own
# established convention is node_id free-form with the snapshot id in
# `ref_id` (test_provenance_lineage's chains use `node-snapshot`/snap_id;
# test_provenance.py uses `n_snap`/`snap1`). Under THAT convention the walk
# stopped at the file boundary -- and still said closed=True, truncated=
# False, because the snapshot was resolved from the source_snapshot TABLE
# rather than from the walk. A broken join was indistinguishable from a
# complete one.
#
# The fix joins on `ref_id`, the column that means "which snapshot". These
# tests pin BOTH namings to the SAME chain, so the seam can no longer be
# closed (or opened) by choosing a name.
# ---------------------------------------------------------------------------

class TheSeamJoinsOnRefIdNotOnANamingConvention(SeamTestCase):

    def _flow_with_node_naming(self, tenant_id, name, prior_node_id, snap_node_id):
        """One real flow -- snapshot -> stage -> review -> approve -> apply
        -> walk -- differing ONLY in what the two provenance lineage nodes
        are NAMED."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp, name=name)
        tn = self._open_tenant(tenant_id, git_dir=git_dir)
        prior = self._record_snapshot(tn, "snap-0", commit_oid="d" * 40, tree_oid="e" * 40)
        snapshot_id = self._record_snapshot(tn, "snap-1")
        prov.record_lineage_node(tn["prov_cx"], tenant_id, self.repository_id,
                                  prior_node_id, "source_snapshot", "snapshot_id", prior, t=T0)
        prov.record_lineage_node(tn["prov_cx"], tenant_id, self.repository_id,
                                  snap_node_id, "source_snapshot", "snapshot_id", snapshot_id, t=T0)
        prov.record_derived_from(tn["prov_cx"], tenant_id, self.repository_id,
                                  prior_node_id, snap_node_id, "replaces", 0, t=T0)
        st, rv, ap = self._stage_review_approve(
            tn, snapshot_id=snapshot_id, content=b"published body\n",
            path="docs/report.md", base_sha=base_sha)
        applied = pub.apply_publication(
            tn["cx"], tenant_id=tenant_id, repository_id=self.repository_id,
            authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
            idempotency_key="naming-1", state_root=tn["root"], t=T0)
        self.assertEqual(applied["status"], "applied")
        return pub.lineage_chain_from_commit(
            tn["cx"], tenant_id=tenant_id, repository_id=self.repository_id,
            commit_oid=applied["commit_oid"])

    def test_both_established_node_namings_produce_the_same_cross_file_chain(self):
        """The regression this class exists for. `same` is the naming the
        first seam test picked (node_id == snapshot_id, the only one under
        which the old join worked); `own` is provenance's own convention,
        which used to stop the walk dead at the boundary with closed=True.
        They must now be indistinguishable."""
        same = self._flow_with_node_naming(1001, "same.git", "snap-0", "snap-1")
        own = self._flow_with_node_naming(1002, "own.git", "n_snap0", "n_snap1")

        expected = ["pub:attempt:1", "pub:review:1", "pub:stage:1", "snap-1", "snap-0"]
        self.assertEqual(same["chain"], expected, same)
        self.assertEqual(own["chain"], expected, own)
        for result in (same, own):
            self.assertTrue(result["closed"], result)
            self.assertFalse(result["truncated"], result)
            self.assertEqual(result["snapshot_id"], "snap-1")
            self.assertEqual(result["source_commit_oid"], IDENTITY["commit_oid"])
        # Five hops, and the last one exists ONLY in provenance.db.
        self.assertEqual(len(own["chain"]), 5)
        self.assertNotIn("n_snap0", own["chain"])
        self.assertNotIn("n_snap1", own["chain"])

    def test_a_source_snapshot_nodes_ref_id_may_not_be_minted_in_the_pub_namespace(self):
        """`ref_id` is what now enters the shared vocabulary, so it carries
        the same reservation `node_id` and `snapshot_id` already did --
        otherwise the fix would have opened a new aliasing route."""
        tn = self._open_tenant(1001)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                      "n-alias", "source_snapshot", "snapshot_id",
                                      "pub:stage:1", t=T0)
        self.assertEqual(ctx.exception.reason_code, "reserved_node_id_namespace")

    def test_one_source_snapshot_node_per_snapshot(self):
        """Two nodes for one snapshot collapse to one id in the unified
        view, and an edge between them appears there as the self-edge
        `record_derived_from` already refuses."""
        tn = self._open_tenant(1001)
        snapshot = self._record_snapshot(tn, "snap-dup")
        prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                  "n-first", "source_snapshot", "snapshot_id", snapshot, t=T0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                      "n-second", "source_snapshot", "snapshot_id", snapshot, t=T0)
        self.assertEqual(ctx.exception.reason_code, "lineage_snapshot_node_duplicate")
        self.assertEqual(ctx.exception.details["existing_node_id"], "n-first")


class TheSnapshotIdHalfOfTheNamespaceIsDisjointToo(SeamTestCase):
    """The other half of the mis-attribution the naming convention allowed:
    publication's `input_snapshot` parent enters the walk as a BARE
    snapshot id, so a provenance lineage_node of any other kind carrying
    that id aliases the boundary -- the walk traverses that node's
    ancestry and the answer names a snapshot it never reached. `pub:` was
    already reserved on both write paths; the snapshot-id half now is
    too."""

    def test_a_non_snapshot_node_may_not_be_named_with_an_existing_snapshot_id(self):
        tn = self._open_tenant(1001)
        snapshot = self._record_snapshot(tn, "snap-claimed")
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                      snapshot, "worker_output", "run_id", "run-1", t=T0)
        self.assertEqual(ctx.exception.reason_code, "lineage_namespace_collision")
        self.assertEqual(ctx.exception.details["also_a"], "source_snapshot")

    def test_a_snapshot_id_may_not_be_claimed_by_an_existing_non_snapshot_node(self):
        """Same rule from the other side -- creation order must not decide
        whether the alias is possible."""
        tn = self._open_tenant(1001)
        prov.record_lineage_node(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                  "n-worker", "worker_output", "run_id", "run-1", t=T0)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            self._record_snapshot(tn, "n-worker")
        self.assertEqual(ctx.exception.reason_code, "lineage_namespace_collision")

    def test_a_boundary_alias_written_around_the_validators_refuses_the_walk(self):
        """The read-side backstop, from the only direction that can still
        produce the row: raw SQL. The walk must refuse rather than answer
        with a snapshot whose ancestry it did not actually traverse."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        snapshot = self._record_snapshot(tn, "snap-ordinary")
        st, rv, ap = self._stage_review_approve(
            tn, snapshot_id=snapshot, content=b"published body\n",
            path="docs/report.md", base_sha=base_sha)
        applied = pub.apply_publication(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
            idempotency_key="alias-1", state_root=tn["root"], t=T0)
        # Before the row exists the walk answers normally.
        self.assertTrue(pub.lineage_chain_from_commit(
            tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
            commit_oid=applied["commit_oid"])["closed"])
        tn["prov_cx"].execute(
            "INSERT INTO lineage_node(node_id,tenant_id,repository_id,node_kind,ref_kind,"
            "ref_id,content_digest,created_at) VALUES(?,?,?,'worker_output','run_id',"
            "'run-1',NULL,?)",
            (snapshot, tn["tenant_id"], self.repository_id, T0))
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.lineage_chain_from_commit(
                tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                commit_oid=applied["commit_oid"])
        self.assertEqual(ctx.exception.reason_code, "lineage_namespace_collision")
        self.assertEqual(ctx.exception.details["node_ids"], [snapshot])


# ---------------------------------------------------------------------------
# Round 3, second defect: `closure_incomplete` was INERT for every snapshot
# that simply declined to declare a closure. `closure_manifest_digest` is
# NULLable, `verify_closure_complete` returned True on NULL, and nothing in
# bin/ ever computed the column -- so the guard could not fire on anything
# a capture path produced. Moving the requirement from publication's kwargs
# into provenance's optional column had not changed that; it had only moved
# where the omission lived.
# ---------------------------------------------------------------------------

class EveryRecordedSnapshotCarriesAFrozenClosureClaim(SeamTestCase):

    def test_record_source_snapshot_freezes_a_claim_even_when_none_is_supplied(self):
        tn = self._open_tenant(1001)
        snapshot = self._record_snapshot(tn, "snap-nodigest")
        recorded = tn["prov_cx"].execute(
            "SELECT closure_manifest_digest FROM source_snapshot WHERE snapshot_id=?",
            (snapshot,)).fetchone()[0]
        self.assertIsNotNone(recorded, "a snapshot with NO closure claim must not exist")
        self.assertEqual(recorded, prov.closure_manifest_digest([]),
                          "the default claim is the explicit 'nothing was required'")

    def test_the_default_claim_is_checked_like_any_other_and_can_refuse(self):
        """The frozen empty claim is not a bypass: once provenance holds a
        closure_entry the snapshot did not claim, the digest no longer
        matches and publication refuses -- with the destination unmoved."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        snapshot = self._record_snapshot(tn, "snap-nodigest")
        prov.record_closure_entry(tn["prov_cx"], tn["tenant_id"], self.repository_id,
                                   snapshot, "submodule", "vendor/sub", "1" * 40, t=T0)
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.stage(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                       content_bytes=b"body\n", destination_path="docs/a.md",
                       classification="report", producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], "eng_alice"),
                       producer_run_id="run-1", snapshot_id=snapshot, t=T0)
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["covered_paths"], ["vendor/sub"])
        self.assertEqual(_ref_sha(git_dir), base_sha)
        self.assertEqual(
            tn["cx"].execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"], 0)

    def test_a_row_with_no_closure_claim_at_all_is_refused_not_passed(self):
        """The pre-fix behaviour, pinned as a refusal. Such a row can now
        only be written around `record_source_snapshot` -- and a verifier
        cannot certify a claim that was never made."""
        git_dir, base_sha = _init_bare_with_seed(self.tmp)
        tn = self._open_tenant(1001, git_dir=git_dir)
        tn["prov_cx"].execute(
            "INSERT INTO source_snapshot(snapshot_id,tenant_id,repository_id,"
            "repository_fingerprint,object_format,commit_oid,tree_oid,captured_at,"
            "closure_manifest_digest) VALUES('snap-null',?,?,?,?,?,?,?,NULL)",
            (tn["tenant_id"], self.repository_id, IDENTITY["repository_fingerprint"],
             IDENTITY["object_format"], IDENTITY["commit_oid"], IDENTITY["tree_oid"], T0))
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.verify_closure_complete(
                tn["prov_cx"], tn["tenant_id"], self.repository_id, "snap-null")
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["reason"], "no_closure_claim_recorded")

        with self.assertRaises(pub.PublicationError) as ctx:
            pub.stage(tn["cx"], tenant_id=tn["tenant_id"], repository_id=self.repository_id,
                       content_bytes=b"body\n", destination_path="docs/a.md",
                       classification="report", producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, tn["cx"], tn["tenant_id"], "eng_alice"),
                       producer_run_id="run-1", snapshot_id="snap-null", t=T0)
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(_ref_sha(git_dir), base_sha)
        self.assertEqual(
            tn["cx"].execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"], 0)

if __name__ == "__main__":
    unittest.main()
