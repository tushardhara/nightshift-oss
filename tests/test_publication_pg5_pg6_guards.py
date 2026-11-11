"""Issue #695 -- mutation-evidence gap closure for PG-5 and PG-6.

Every test in this file exists because a NAMED defect was injected into the
real production code, the whole 108-test PG-5/PG-6 baseline was re-run, and
NOTHING went red. Each test names the mutation it kills in its docstring, so
a later reader can re-run the injection and watch this file catch it.

Two structural rules this file follows deliberately, both of which the
existing suites do not:

1. **The production modules are imported HARD.** `tests/test_publication_
   gateway.py` wraps its import in `try/except` and, on failure, prints a
   warning and sets `NSP = None`; its base class then carries
   `@unittest.skipIf(NSP is None, ...)`, so a `bin/nightshift_publication.py`
   that does not import turns all 25 of its tests into SKIPS and the suite
   still exits 0 reporting OK. That is the exact "coverage lost silently"
   failure #695 was filed about. Here a broken module is an ERROR, and
   `ProductionModulesActuallyLoaded` asserts it positively.

2. **Refusals are asserted by reason_code, not merely by "it raised".** Most
   surviving mutants in the audit survived because two independent guards
   emit the SAME reason_code, so removing either one left an existing test
   green. Where two layers overlap, the tests below pin each layer on its
   own -- by driving it through the one entry point where the other layer
   cannot reach.

Mockless: real `git` subprocesses against real bare repositories, real
on-disk SQLite for both provenance.db and publication.db.
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
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

# Deliberately NOT guarded by try/except -- see rule 1 in the module
# docstring. If either module stops importing, this file must go red.
import nightshift_publication as pub          # noqa: E402
import nightshift_provenance as prov          # noqa: E402

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               GIT_TERMINAL_PROMPT="0")
AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "pg-guard-seed", "GIT_AUTHOR_EMAIL": "seed@test.invalid",
    "GIT_COMMITTER_NAME": "pg-guard-seed", "GIT_COMMITTER_EMAIL": "seed@test.invalid",
    "GIT_AUTHOR_DATE": "@1700000000 +0000", "GIT_COMMITTER_DATE": "@1700000000 +0000",
}
T0 = 2_000_000.0
REF = "refs/heads/main"
SNAPSHOT_ID = "snap-pg5-pg6"
SNAPSHOT_IDENTITY = {
    "repository_fingerprint": "sha256:" + "a" * 64,
    "object_format": "sha1",
    "commit_oid": "b" * 40,
    "tree_oid": "c" * 40,
}


def _git(git_dir, args, *, input_bytes=None, env_extra=None):
    env = dict(GIT_ENV)
    env.update(env_extra or {})
    r = subprocess.run(["git", "--git-dir", git_dir] + list(args), input=input_bytes,
                       capture_output=True, env=env, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr.decode('utf-8', 'replace')}")
    return r


def _seed_bare_repo(base_dir):
    """A real bare repo with one real commit on REF. Returns (git_dir, sha)."""
    git_dir = os.path.join(base_dir, "dest.git")
    subprocess.run(["git", "init", "--quiet", "--bare", git_dir], check=True,
                   capture_output=True, env=GIT_ENV)
    index_path = os.path.join(base_dir, "seed-index")
    idx = {"GIT_INDEX_FILE": index_path}
    blob = _git(git_dir, ["hash-object", "-w", "--stdin"],
                input_bytes=b"seed\n").stdout.strip().decode("ascii")
    _git(git_dir, ["update-index", "--add", "--cacheinfo", f"100644,{blob},README.md"],
         env_extra=idx)
    tree = _git(git_dir, ["write-tree"], env_extra=idx).stdout.strip().decode("ascii")
    sha = _git(git_dir, ["commit-tree", tree, "-m", "seed"],
               env_extra=AUTHOR_ENV).stdout.strip().decode("ascii")
    _git(git_dir, ["update-ref", REF, sha])
    os.remove(index_path)
    return git_dir, sha


def _head(git_dir):
    r = subprocess.run(["git", "--git-dir", git_dir, "rev-parse", "--verify", REF],
                       capture_output=True, env=GIT_ENV, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


class GuardTestCase(unittest.TestCase):
    """One tenant, one real bare destination, one registered reviewer."""

    MODE = "zero_nightshift_file"
    TENANT = 7001
    REPOSITORY = 11

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ns-pg5pg6-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.state_root = os.path.join(self.base, "state")
        os.makedirs(self.state_root, mode=0o700)
        self.git_dir, self.base_sha = _seed_bare_repo(self.base)

        self.prov_cx = prov.connect(self.state_root, self.TENANT, self.REPOSITORY)
        self.addCleanup(self.prov_cx.close)
        prov.record_source_snapshot(self.prov_cx, self.TENANT, self.REPOSITORY,
                                    SNAPSHOT_ID, dict(SNAPSHOT_IDENTITY, captured_at=T0))

        self.root = pub.resolve_publication_root(self.state_root, self.TENANT, self.REPOSITORY)
        self.cx = pub.connect(self.root)
        self.addCleanup(self.cx.close)

        strict = self.MODE == "no_generated_file"
        pub.policy_set(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                       mode=self.MODE, path_allowlist=["docs/**"],
                       target_git_dir=None if strict else self.git_dir,
                       target_ref=None if strict else REF, key_version=None, t=T0)
        pub.register_reviewer(self.cx, tenant_id=self.TENANT, reviewer_identity="eng_bob",
                              token_bytes=b"tok-bob", t=T0)

    def _refusal(self, fn, expected):
        with self.assertRaises(pub.PublicationError) as ctx:
            fn()
        self.assertEqual(ctx.exception.reason_code, expected)
        return ctx.exception

    def _stage(self, *, content=b"guard content\n", path="docs/g.md",
               producer="eng_alice", run="run-1"):
        return pub.stage(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                         content_bytes=content, destination_path=path,
                         classification="report", producer_identity=producer,
                         producer_token_bytes=producer_token(pub, self.cx, self.TENANT, producer),
                         producer_run_id=run, snapshot_id=SNAPSHOT_ID, t=T0)

    def _stage_review_approve(self, **kw):
        st = self._stage(**kw)
        rv = pub.review(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                        stage_id=st["stage_id"], reviewer_identity="eng_bob",
                        reviewer_run_id="rr-1", token_bytes=b"tok-bob",
                        verdict="approve", t=T0)
        ap = pub.approve(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                         stage_id=st["stage_id"], review_id=rv["review_id"],
                         reviewer_identity="eng_bob", reviewer_run_id="rr-1",
                         token_bytes=b"tok-bob", expected_base_sha=self.base_sha,
                         ttl=3600, t=T0)
        return st, rv, ap


# ===========================================================================
# Anti-self-heal
# ===========================================================================

class ProductionModulesActuallyLoaded(unittest.TestCase):
    """Kills mutation SELFHEAL-import-break.

    Injecting an undefined-name call at module scope in
    bin/nightshift_publication.py makes tests/test_publication_gateway.py
    report `Ran 25 tests ... OK (skipped=25)` and exit 0. This test turns
    that same injection into a red run.
    """

    def test_both_modules_are_real_modules_from_bin(self):
        for mod, name in ((pub, "nightshift_publication.py"),
                          (prov, "nightshift_provenance.py")):
            self.assertIsNotNone(mod)
            self.assertEqual(os.path.realpath(mod.__file__),
                             os.path.realpath(str(BIN / name)))

    def test_the_gateway_suite_would_not_silently_skip_itself(self):
        """The named entry points must all be present and callable. If the
        module half-loaded, or a verb was deleted, this is an error here --
        not 25 quiet skips over in test_publication_gateway.py."""
        for verb in ("policy_set", "register_reviewer", "stage", "review",
                     "approve", "apply_publication", "patch_stage",
                     "consume_publication_authorization"):
            self.assertTrue(callable(getattr(pub, verb, None)), verb)


# ===========================================================================
# PG-5 -- no_generated_file disables publication
# ===========================================================================

class StrictModeEgressTargetIsRefusedByTheAPI(GuardTestCase):
    """Kills mutation PG5-C-policyset-egress-target-guard.

    `test_publication_gateway.NoGeneratedFileMode.
    test_policy_cannot_name_an_egress_target_in_strict_mode` reaches the
    schema CHECK with a raw `cx.execute("UPDATE publication_policy ...")`,
    bypassing `policy_set()` entirely. So deleting the Python guard
    `egress_target_forbidden_in_strict_mode` from policy_set() left the
    whole 108-test baseline green. This drives the SAME rule through the
    public API and pins the reason_code, which the schema backstop alone
    cannot produce (it surfaces as `policy_check_failed`).
    """

    def test_policy_set_refuses_a_target_git_dir_in_strict_mode(self):
        self._refusal(
            lambda: pub.policy_set(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                mode="no_generated_file", path_allowlist=["docs/**"],
                target_git_dir=self.git_dir, target_ref=None, key_version=None, t=T0),
            "egress_target_forbidden_in_strict_mode")

    def test_policy_set_refuses_a_target_ref_in_strict_mode(self):
        self._refusal(
            lambda: pub.policy_set(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                mode="no_generated_file", path_allowlist=["docs/**"],
                target_git_dir=None, target_ref=REF, key_version=None, t=T0),
            "egress_target_forbidden_in_strict_mode")

    def test_the_refused_policy_set_did_not_change_the_stored_policy(self):
        before = dict(pub.get_policy(self.cx))
        self._refusal(
            lambda: pub.policy_set(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                mode="no_generated_file", path_allowlist=["docs/**"],
                target_git_dir=self.git_dir, target_ref=REF, key_version=None, t=T0),
            "egress_target_forbidden_in_strict_mode")
        self.assertEqual(dict(pub.get_policy(self.cx)), before)


class PolicyModeIsAClosedEnum(GuardTestCase):
    """Kills mutations PG5-F-policyset-mode-allowlist, PG5-G-schema-mode-enum
    and the combined PG5-FG-both-mode-enum-guards.

    Removing BOTH the Python allowlist in policy_set() and the schema
    `CHECK(mode IN (...))` left the entire baseline green -- an arbitrary
    mode string could be persisted with nothing noticing. That matters
    beyond hygiene: every downstream decision is written
    `if policy["mode"] == "no_generated_file"`, so an unrecognised mode
    behaves as the PERMISSIVE mode. Publication would be enabled, not
    disabled, by a typo.
    """

    def test_policy_set_refuses_an_unknown_mode(self):
        self._refusal(
            lambda: pub.policy_set(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                mode="no_generated_files", path_allowlist=["docs/**"],
                target_git_dir=self.git_dir, target_ref=REF, key_version=None, t=T0),
            "invalid_mode")
        self.assertEqual(pub.get_policy(self.cx)["mode"], "zero_nightshift_file")

    def test_policy_set_refuses_an_empty_mode(self):
        self._refusal(
            lambda: pub.policy_set(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                mode="", path_allowlist=["docs/**"],
                target_git_dir=self.git_dir, target_ref=REF, key_version=None, t=T0),
            "invalid_mode")

    def test_schema_backstop_refuses_an_unknown_mode_written_by_raw_sql(self):
        """Second layer, independently: even a direct UPDATE that routes
        around policy_set() cannot park an unrecognised mode in the row."""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.cx.execute("UPDATE publication_policy SET mode='publish_everything' WHERE id=1")
        self.assertIn("CHECK", str(ctx.exception).upper())
        self.assertEqual(pub.get_policy(self.cx)["mode"], "zero_nightshift_file")


class SwitchingToStrictRevokesLiveAuthorizations(GuardTestCase):
    """Kills mutation PG5-H-strict-switch-revokes-auths.

    `policy_set()` revokes every live authorization when the policy moves
    into `no_generated_file`. Replacing that with `switching_to_strict =
    False` left the baseline green, so the revocation -- the thing that
    stops an approval minted under the permissive mode from being redeemed
    after publication is turned off -- had no coverage at all.
    """

    def test_a_live_authorization_is_revoked_when_strict_mode_is_set(self):
        _st, _rv, ap = self._stage_review_approve()
        row = self.cx.execute("SELECT revoked_at, revoked_reason FROM "
                              "publication_authorizations WHERE id=?",
                              (ap["authorization_id"],)).fetchone()
        self.assertIsNone(row["revoked_at"], "precondition: approval starts live")

        result = pub.policy_set(
            self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
            mode="no_generated_file", path_allowlist=["docs/**"],
            target_git_dir=None, target_ref=None, key_version=None, t=T0 + 1)
        self.assertEqual(result["revoked_authorizations"], 1)

        row = self.cx.execute("SELECT revoked_at, revoked_reason FROM "
                              "publication_authorizations WHERE id=?",
                              (ap["authorization_id"],)).fetchone()
        self.assertIsNotNone(row["revoked_at"])
        self.assertEqual(row["revoked_reason"], "mode_changed")

    def test_the_revoked_authorization_cannot_be_applied_and_head_does_not_move(self):
        st, _rv, ap = self._stage_review_approve()
        pub.policy_set(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                       mode="no_generated_file", path_allowlist=["docs/**"],
                       target_git_dir=None, target_ref=None, key_version=None, t=T0 + 1)
        with self.assertRaises(pub.PublicationError):
            pub.apply_publication(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                idempotency_key="after-strict-switch", state_root=self.root, t=T0 + 2)
        self.assertEqual(_head(self.git_dir), self.base_sha)


class ApplyRefusesStrictModeEvenIfAnAuthorizationExists(GuardTestCase):
    """Kills mutation PG5-B-apply-no-egress-credential.

    `apply_publication()` carries `if policy["mode"] == "no_generated_file":
    _refuse("no_egress_credential")`. Deleting it left the baseline green,
    because no test can reach it through the front door: `approve()` refuses
    first in strict mode, so a strict-mode authorization is never minted.

    That makes this a genuine defence-in-depth backstop rather than dead
    code -- it is the layer that still refuses if the approve-time guard is
    ever bypassed. This test reproduces exactly that state, in the same
    "assume the outer layer already failed" style
    `test_provenance.TenancyPhysicalSeparation.
    test_defense_in_depth_same_file_cross_tenant_edge_refused` already uses:
    the authorization row is inserted directly, then the real
    apply_publication() is called.
    """

    def _plant_strict_mode_authorization(self):
        """Reproduce the state approve() would have left behind if its
        strict-mode guard had not fired: a LIVE authorization whose recorded
        mode is `no_generated_file`, against a strict policy.

        The row is INSERTed, not UPDATEd -- `publication_authorizations`
        carries a trigger ("authorization rows may only be consumed or
        revoked") that already refuses arbitrary mutation of an existing
        row, which is itself a guard worth not routing around.
        """
        st = self._stage()
        rv = pub.review(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                        stage_id=st["stage_id"], reviewer_identity="eng_bob",
                        reviewer_run_id="rr-1", token_bytes=b"tok-bob",
                        verdict="approve", t=T0)
        pub.policy_set(self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                       mode="no_generated_file", path_allowlist=["docs/**"],
                       target_git_dir=None, target_ref=None, key_version=None, t=T0 + 1)
        policy = pub.get_policy(self.cx)
        stage_row = pub.get_stage(self.cx, st["stage_id"])
        cur = self.cx.execute(
            "INSERT INTO publication_authorizations(tenant_id,repository_id,content_digest,"
            "destination_path,expected_base_sha,reviewer_identity,expires_at,"
            "authorization_digest,stage_id,review_id,policy_version,path_allowlist_digest,"
            "mode,target_git_dir,target_ref,git_object_format,issued_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'no_generated_file',?,?,'sha1',?)",
            (self.TENANT, self.REPOSITORY, stage_row["content_digest"],
             stage_row["destination_path"], self.base_sha, "eng_bob", T0 + 10_000,
             "forged-digest-for-defence-in-depth", st["stage_id"], rv["review_id"],
             policy["policy_version"], policy["path_allowlist_digest"],
             self.git_dir, REF, T0 + 1))
        self.cx.commit()
        return st, {"authorization_id": cur.lastrowid}

    def test_apply_refuses_no_egress_credential_and_writes_nothing(self):
        st, ap = self._plant_strict_mode_authorization()
        self._refusal(
            lambda: pub.apply_publication(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                idempotency_key="strict-backstop", state_root=self.root, t=T0 + 2),
            "no_egress_credential")
        self.assertEqual(_head(self.git_dir), self.base_sha)

    def test_the_authorization_is_not_consumed_by_the_refusal(self):
        st, ap = self._plant_strict_mode_authorization()
        with self.assertRaises(pub.PublicationError):
            pub.apply_publication(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                idempotency_key="strict-backstop-2", state_root=self.root, t=T0 + 2)
        row = self.cx.execute("SELECT consumed_at FROM publication_authorizations WHERE id=?",
                              (ap["authorization_id"],)).fetchone()
        self.assertIsNone(row["consumed_at"])


# ===========================================================================
# PG-6 -- cross-tenant fail-closed
# ===========================================================================

class TenantIdIsValidatedByThePublicationModuleItself(GuardTestCase):
    """Kills mutation PG6-M-publication-tenant-validator.

    `nightshift_publication._validate_tenant_id` delegates to
    `prov.validate_tenant_id`. Replacing its body with `return value` left
    the baseline green: no test drives a malformed tenant_id in through a
    publication verb. The delegation is the PRIMARY defence named in the
    module's own schema comment ("runs in Python at every entrypoint,
    before the id can reach a path join or a WHERE clause"), so it needs
    its own evidence and not just the schema CHECK's.
    """

    BAD_IDS = (True, False, -1, 0, "1", "../other_tenant", None, 1.5)

    def test_stage_refuses_every_malformed_tenant_id(self):
        for bad in self.BAD_IDS:
            with self.subTest(bad=bad):
                self._refusal(
                    lambda b=bad: pub.stage(
                        self.cx, tenant_id=b, repository_id=self.REPOSITORY,
                        content_bytes=b"x\n", destination_path="docs/x.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(pub, self.cx, b, "eng_alice"),
                        producer_run_id="r", snapshot_id=SNAPSHOT_ID, t=T0),
                    "invalid_tenant_id")

    def test_patch_refuses_every_malformed_tenant_id(self):
        st = self._stage()
        for bad in self.BAD_IDS:
            with self.subTest(bad=bad):
                self._refusal(
                    lambda b=bad: pub.patch_stage(
                        self.cx, stage_id=st["stage_id"], tenant_id=b,
                        repository_id=self.REPOSITORY),
                    "invalid_tenant_id")

    def test_policy_set_refuses_every_malformed_tenant_id(self):
        for bad in self.BAD_IDS:
            with self.subTest(bad=bad):
                self._refusal(
                    lambda b=bad: pub.policy_set(
                        self.cx, tenant_id=b, repository_id=self.REPOSITORY,
                        mode="zero_nightshift_file", path_allowlist=["docs/**"],
                        target_git_dir=self.git_dir, target_ref=REF,
                        key_version=None, t=T0),
                    "invalid_tenant_id")

    def test_no_stage_row_was_written_by_any_refused_call(self):
        for bad in self.BAD_IDS:
            with self.assertRaises(pub.PublicationError):
                pub.stage(self.cx, tenant_id=bad, repository_id=self.REPOSITORY,
                          content_bytes=b"x\n", destination_path="docs/x.md",
                          classification="report", producer_identity="eng_alice",
                          producer_token_bytes=producer_token(pub, self.cx, bad, "eng_alice"),
                          producer_run_id="r", snapshot_id=SNAPSHOT_ID, t=T0)
        n = self.cx.execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"]
        self.assertEqual(n, 0)


class EachCrossTenantApplyLayerRefusesOnItsOwn(GuardTestCase):
    """Kills mutations PG6-H-apply-auth-tenant-mismatch,
    PG6-I-apply-presented-stage-tenant, PG6-K-consume-cas-drops-tenant and
    the combined PG6-HI-both-apply-tenant-guards.

    `apply_publication()` has THREE independent cross-tenant defences: the
    authorization's tenant check, the presented stage's tenant check, and
    the tenant column in the one-use consume CAS. All three emit -- or
    diagnose to -- the same `tenant_mismatch`, so the existing
    `test_provenance_lineage.CrossTenantPublishFailsClosed.
    test_apply_under_a_different_tenant_is_refused_and_authorization_stays_live`
    stayed green with any ONE of them deleted, and stayed green with the
    first TWO deleted together. Only removing all three turned it red.

    The boundary held throughout -- this is redundancy working, not a hole.
    But redundancy nobody tests individually decays silently, so each layer
    is pinned below by arranging the state where the other two cannot fire.
    """

    OTHER_TENANT = 7002

    def _clone_stage_to_other_tenant(self, stage_id):
        self.cx.execute(
            "INSERT INTO publication_stage(tenant_id,repository_id,content_digest,"
            "content_size,content,destination_path,classification,producer_identity,"
            "producer_run_id,source_snapshot_id,repository_fingerprint,snapshot_commit_oid,"
            "snapshot_tree_oid,snapshot_object_format,policy_version,staged_at) "
            "SELECT ?,repository_id,content_digest,content_size,content,destination_path,"
            "classification,producer_identity,producer_run_id,source_snapshot_id,"
            "repository_fingerprint,snapshot_commit_oid,snapshot_tree_oid,"
            "snapshot_object_format,policy_version,staged_at "
            "FROM publication_stage WHERE id=?",
            (self.OTHER_TENANT, stage_id))
        new_id = self.cx.execute("SELECT MAX(id) AS m FROM publication_stage").fetchone()["m"]
        self.cx.commit()
        return new_id

    def test_authorization_layer_refuses_before_the_destination_is_resolved(self):
        """Kills PG6-H specifically.

        Auth belongs to tenant A; the stage presented belongs to the CALLER
        (tenant B), so the presented-stage layer passes. Asserting the
        reason_code alone is NOT enough here -- with the authorization layer
        deleted the consume CAS still refuses, and it diagnoses to the very
        same `tenant_mismatch`. The observable that separates the two layers
        is WHEN the refusal happens: the authorization's tenant is checked
        before apply resolves `auth["target_git_dir"]` at all.

        That ordering is the property worth keeping. The destination here has
        been moved away, so a run that got as far as resolving it would
        report `target_git_dir_invalid` -- i.e. a foreign caller would learn
        whether another tenant's destination repository exists. PG-6's whole
        claim is that no such cross-tenant oracle exists.
        """
        st_a, _rv, ap_a = self._stage_review_approve()
        foreign_stage_id = self._clone_stage_to_other_tenant(st_a["stage_id"])
        moved = self.git_dir + ".moved-away"
        os.rename(self.git_dir, moved)
        self.addCleanup(lambda: os.path.exists(moved) and os.rename(moved, self.git_dir))

        self._refusal(
            lambda: pub.apply_publication(
                self.cx, tenant_id=self.OTHER_TENANT, repository_id=self.REPOSITORY,
                authorization_id=ap_a["authorization_id"], stage_id=foreign_stage_id,
                idempotency_key="auth-layer", state_root=self.root, t=T0 + 1),
            "tenant_mismatch")
        self.assertFalse(os.path.exists(self.git_dir),
                         "the destination must still be absent -- nothing recreated it")

    def test_authorization_layer_leaves_no_attempt_row_and_no_commit(self):
        st_a, _rv, ap_a = self._stage_review_approve()
        foreign_stage_id = self._clone_stage_to_other_tenant(st_a["stage_id"])
        self._refusal(
            lambda: pub.apply_publication(
                self.cx, tenant_id=self.OTHER_TENANT, repository_id=self.REPOSITORY,
                authorization_id=ap_a["authorization_id"], stage_id=foreign_stage_id,
                idempotency_key="auth-layer-2", state_root=self.root, t=T0 + 1),
            "tenant_mismatch")
        n = self.cx.execute("SELECT COUNT(*) AS n FROM publication_attempts").fetchone()["n"]
        self.assertEqual(n, 0)
        self.assertEqual(_head(self.git_dir), self.base_sha)

    def test_presented_stage_layer_refuses_when_only_the_stage_is_foreign(self):
        """Caller and authorization are both tenant A; the stage_id handed
        in belongs to tenant B. The authorization layer therefore passes,
        so only the presented-stage layer can produce the refusal."""
        st_a, _rv, ap_a = self._stage_review_approve()
        foreign_stage_id = self._clone_stage_to_other_tenant(st_a["stage_id"])
        self._refusal(
            lambda: pub.apply_publication(
                self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
                authorization_id=ap_a["authorization_id"], stage_id=foreign_stage_id,
                idempotency_key="stage-layer", state_root=self.root, t=T0 + 1),
            "tenant_mismatch")
        self.assertEqual(_head(self.git_dir), self.base_sha)

    def test_consume_cas_is_tenant_scoped_on_its_own(self):
        """The third layer, unit-tested directly, because the two above
        refuse before apply_publication() can ever reach it. A CAS that
        matched cross-tenant would be a one-use token redeemable by the
        wrong tenant."""
        st, _rv, ap = self._stage_review_approve()
        auth = self.cx.execute("SELECT * FROM publication_authorizations WHERE id=?",
                               (ap["authorization_id"],)).fetchone()

        wrong = pub.consume_publication_authorization(
            self.cx, tenant_id=self.OTHER_TENANT, repository_id=self.REPOSITORY,
            content_digest=auth["content_digest"], destination_path=auth["destination_path"],
            observed_base_sha=auth["expected_base_sha"],
            reviewer_identity=auth["reviewer_identity"],
            authorization_digest=auth["authorization_digest"], attempt_id=1, t=T0 + 1)
        self.assertFalse(wrong, "the CAS must not match under a foreign tenant_id")
        still_live = self.cx.execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()
        self.assertIsNone(still_live["consumed_at"])

        # Positive control: the real tenant's identical call DOES match, so
        # the assertion above is about the tenant column and nothing else.
        right = pub.consume_publication_authorization(
            self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY,
            content_digest=auth["content_digest"], destination_path=auth["destination_path"],
            observed_base_sha=auth["expected_base_sha"],
            reviewer_identity=auth["reviewer_identity"],
            authorization_digest=auth["authorization_digest"], attempt_id=1, t=T0 + 1)
        self.assertTrue(right)
        self.assertUsedOnce = self.cx.execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()
        self.assertIsNotNone(self.assertUsedOnce["consumed_at"])

    def test_consume_cas_is_repository_scoped_on_its_own(self):
        st, _rv, ap = self._stage_review_approve()
        auth = self.cx.execute("SELECT * FROM publication_authorizations WHERE id=?",
                               (ap["authorization_id"],)).fetchone()
        wrong = pub.consume_publication_authorization(
            self.cx, tenant_id=self.TENANT, repository_id=self.REPOSITORY + 1,
            content_digest=auth["content_digest"], destination_path=auth["destination_path"],
            observed_base_sha=auth["expected_base_sha"],
            reviewer_identity=auth["reviewer_identity"],
            authorization_digest=auth["authorization_digest"], attempt_id=1, t=T0 + 1)
        self.assertFalse(wrong)


if __name__ == "__main__":
    unittest.main()
