"""Issue #695 -- independent mutation-driven audit of PG-3 ("the producing
worker cannot approve its own publication").

Written as an AUDIT of `bin/nightshift_publication.py`, not by its authors.
Every test here exists because a named defect was injected into the real
production code and the pre-existing suite stayed green. The three gaps
this file closes, with the exact injection that proved each one:

  1. `approve()`'s re-assertion of the guard.
     INJECT: delete the `_assert_not_self_review(cx, stage_row, canonical,
     reviewer_run_id)` call under the comment "Re-assert self-review at
     approve".  RESULT before this file: 124 tests, OK.
     Reachability: NOT reachable through review() -> approve(), and that is
     provable rather than assumed --  `publication_stage` carries
     `publication_stage_no_update` / `publication_stage_no_delete`
     triggers, so a stage's producer_identity / producer_run_id /
     derived_from_stage_id cannot move between the two calls, and approve()
     itself pins `canonical == review_row["reviewer_identity"]` and
     `reviewer_run_id == review_row["reviewer_run_id"]`. The guard is
     therefore a backstop against a `publication_review` row that did NOT
     come from `review()` -- `publication_review` has no immutability
     trigger, so a row can be planted by any writer holding the sqlite
     file. That is the shape this file exercises: not dead code, but code
     whose only reachable caller is a tampered database.

  2. `apply_publication()`'s re-assertion of the guard.
     INJECT: delete `_assert_not_self_review(cx, presented_stage,
     auth["reviewer_identity"], None)`.  RESULT before this file: OK.
     Reachability: fully reachable through the public API -- apply takes
     `stage_id` as a parameter independent of the authorization, and the
     module's own comment says the presented stage "may legitimately differ
     from the stage that was originally approved". Presenting a stage the
     AUTHORIZING REVIEWER produced is the laundering move that guard is
     for, and it fires before the CAS, so the refusal is `self_review`,
     not `content_digest_mismatch`.

  3. The ancestry walk beyond ONE hop.
     INJECT: in `_ancestry_producer_identities`, replace `current = parent`
     with `current = None`, so the walk yields only the immediate parent.
     RESULT before this file: OK -- the existing
     `test_self_review_via_lineage_is_refused_and_writes_nothing` launders
     through exactly one hop, so a two-hop chain (alice -> bob -> carol,
     alice reviews carol) was accepted.

Every test asserts the git-visible invariant as well as the refusal code:
HEAD and the commit count on the real bare destination, read with a git
subprocess that never goes through the gateway.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import unittest

from test_publication_gateway import (  # noqa: E402
    NSP, PROV, REF, T0, PublicationGatewayTestCase, _commit_count, _ref_sha,
)
from publication_identities import producer_token  # noqa: E402


@unittest.skipIf(NSP is None, "bin/nightshift_publication.py not importable")
class SelfReviewIsReAssertedAtApprove(PublicationGatewayTestCase):
    """Gap 1. `review()` is the first line of defence; the module documents
    the guard as "checked at review AND approve AND apply". Nothing tested
    the second of those three."""

    def test_approve_refuses_a_self_review_row_planted_around_review(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)

        # eng_bob is both the producer and the registered reviewer.
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"bob's own work", destination_path="docs/report.md",
            classification="report", producer_identity="eng_bob", producer_token_bytes=fx["reviewer_token"],
            producer_run_id="run-bob", snapshot_id=fx["snapshot_id"], t=T0)

        # review() refuses this outright -- confirmed here so the planted
        # row below is unambiguously a row review() would never have made.
        self._assert_refusal(
            lambda: NSP.review(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], reviewer_identity="eng_bob",
                reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
                verdict="approve", t=T0),
            "self_review")

        # Plant the review row directly, the way a second writer holding
        # publication.db could: `publication_review` has no immutability
        # trigger and no producer/reviewer CHECK.
        digest = NSP.sha256_hex(b"bob's own work")
        cur = fx["cx"].execute(
            "INSERT INTO publication_review(stage_id,reviewer_identity,reviewer_run_id,"
            "reviewed_digest,verdict,notes,reviewed_at) VALUES(?,?,?,?,?,?,?)",
            (st["stage_id"], NSP.canonicalize_identity("eng_bob"), "rev-run-1",
             digest, "approve", "planted around review()", T0))
        fx["cx"].commit()
        planted_review_id = cur.lastrowid

        self._assert_refusal(
            lambda: NSP.approve(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], review_id=planted_review_id,
                reviewer_identity="eng_bob", reviewer_run_id="rev-run-1",
                token_bytes=fx["reviewer_token"], expected_base_sha=fx["base_sha"],
                ttl=3600, t=T0),
            "self_review")

        auth_count = fx["cx"].execute(
            "SELECT COUNT(*) FROM publication_authorizations").fetchone()[0]
        self.assertEqual(auth_count, 0,
                         "no authorization may be minted from a planted self-review")
        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)

    def test_approve_refuses_a_planted_same_run_review(self):
        """Same planting route, second of the three guards: producer and
        reviewer are different names but share a run id."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)

        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"same-ci-job content", destination_path="docs/report.md",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
            producer_run_id="ci-run-77", snapshot_id=fx["snapshot_id"], t=T0)

        digest = NSP.sha256_hex(b"same-ci-job content")
        cur = fx["cx"].execute(
            "INSERT INTO publication_review(stage_id,reviewer_identity,reviewer_run_id,"
            "reviewed_digest,verdict,notes,reviewed_at) VALUES(?,?,?,?,?,?,?)",
            (st["stage_id"], NSP.canonicalize_identity("eng_bob"), "ci-run-77",
             digest, "approve", "planted around review()", T0))
        fx["cx"].commit()

        self._assert_refusal(
            lambda: NSP.approve(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], review_id=cur.lastrowid,
                reviewer_identity="eng_bob", reviewer_run_id="ci-run-77",
                token_bytes=fx["reviewer_token"], expected_base_sha=fx["base_sha"],
                ttl=3600, t=T0),
            "self_review_same_run")
        self.assertEqual(
            fx["cx"].execute("SELECT COUNT(*) FROM publication_authorizations").fetchone()[0], 0)
        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)

    def test_a_genuinely_independent_planted_review_still_approves(self):
        """Sibling control -- without it, an approve() that refused every
        planted row (or every row at all) would look identical to one whose
        self-review guard works."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        st, rv, ap = self._stage_review_approve(
            fx, content=b"content A", path="docs/report.md", producer="eng_alice")
        self.assertTrue(ap["authorization_id"])


@unittest.skipIf(NSP is None, "bin/nightshift_publication.py not importable")
class SelfReviewIsReAssertedAtApply(PublicationGatewayTestCase):
    """Gap 2. `apply_publication()` accepts `stage_id` independently of the
    authorization; the reviewer who authorized one stage must not be able
    to present a stage they produced themselves."""

    def test_apply_refuses_a_presented_stage_the_authorizing_reviewer_produced(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)

        # A completely legitimate flow: alice produces, bob reviews and
        # approves. The authorization is real and unconsumed.
        st_alice, rv, ap = self._stage_review_approve(
            fx, content=b"alice's approved content", path="docs/report.md",
            producer="eng_alice")

        # Bob then stages his OWN content -- legal on its own, nothing has
        # reviewed it.
        st_bob = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"bob's unreviewed content", destination_path="docs/report.md",
            classification="report", producer_identity="eng_bob", producer_token_bytes=fx["reviewer_token"],
            producer_run_id="run-bob", snapshot_id=fx["snapshot_id"], t=T0)
        self.assertNotEqual(st_bob["stage_id"], st_alice["stage_id"])

        # ...and presents it under alice's authorization. The CAS would
        # refuse this too (different digest), but the self-review guard
        # runs FIRST and must be what answers: the distinction matters,
        # because a caller who can also control the bytes would defeat the
        # digest check and not the identity one.
        exc = self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st_bob["stage_id"],
                                idempotency_key="idem-launder-1", t=T0),
            "self_review")
        self.assertEqual(exc.details.get("stage_id"), st_bob["stage_id"])

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before,
                         "HEAD moved on a refused apply")
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)
        consumed = fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()[0]
        self.assertIsNone(consumed,
                          "a refused apply must not consume the authorization")

    def test_apply_refuses_when_the_presented_stage_descends_from_the_reviewers_work(self):
        """The laundering variant at apply time: the presented stage's
        PRODUCER is someone else, but its lineage runs back to the
        authorizing reviewer."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)

        st_alice, rv, ap = self._stage_review_approve(
            fx, content=b"alice's approved content", path="docs/report.md",
            producer="eng_alice")

        st_bob = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"bob's draft", destination_path="docs/report.md",
            classification="report", producer_identity="eng_bob", producer_token_bytes=fx["reviewer_token"],
            producer_run_id="run-bob", snapshot_id=fx["snapshot_id"], t=T0)
        st_carol = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"carol's polish of bob's draft",
            destination_path="docs/report.md", classification="report",
            producer_identity="eng_carol",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_carol"), producer_run_id="run-carol",
            derived_from_stage_id=st_bob["stage_id"],
            snapshot_id=fx["snapshot_id"], t=T0)

        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st_carol["stage_id"],
                                idempotency_key="idem-launder-2", t=T0),
            "self_review_via_lineage")
        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)

    def test_presenting_an_unrelated_third_party_stage_is_refused_by_the_cas_not_the_guard(self):
        """Sibling control: the self-review guard at apply must not be a
        blanket 'presented stage must equal approved stage' check. A stage
        by an unrelated producer reaches the CAS and is refused there, with
        a DIFFERENT reason code -- which is what proves the two refusals
        above really came from the identity guard."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        st_alice, rv, ap = self._stage_review_approve(
            fx, content=b"alice's approved content", path="docs/report.md",
            producer="eng_alice")
        st_dave = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"dave's unrelated content", destination_path="docs/report.md",
            classification="report", producer_identity="eng_dave",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_dave"),
            producer_run_id="run-dave", snapshot_id=fx["snapshot_id"], t=T0)

        exc = self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st_dave["stage_id"],
                                idempotency_key="idem-third-party", t=T0),
            "content_digest_mismatch")
        self.assertIsNotNone(exc)


@unittest.skipIf(NSP is None, "bin/nightshift_publication.py not importable")
class SelfReviewLineageWalkIsDeeperThanOneHop(PublicationGatewayTestCase):
    """Gap 3. The existing lineage test launders through one hop; the walk
    is bounded at ANCESTRY_WALK_LIMIT hops and nothing pinned hop 2."""

    def _chain(self, fx, hops):
        """alice at the root, then `hops` further stages each deriving from
        the previous one, none of them produced by alice."""
        parent = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"alice's draft", destination_path="docs/report.md",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
            producer_run_id="run-alice", snapshot_id=fx["snapshot_id"], t=T0)
        for i in range(hops):
            parent = NSP.stage(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                content_bytes=f"hop {i} rewrite".encode(),
                destination_path="docs/report.md", classification="report",
                producer_identity=f"eng_hop{i}",
                producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], f"eng_hop{i}"), producer_run_id=f"run-hop{i}",
                derived_from_stage_id=parent["stage_id"],
                snapshot_id=fx["snapshot_id"], t=T0)
        return parent

    def _alice_reviews(self, fx, stage_id, alice_token):
        return lambda: NSP.review(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            stage_id=stage_id, reviewer_identity="eng_alice",
            reviewer_run_id="rev-run-alice", token_bytes=alice_token,
            verdict="approve", t=T0)

    def test_two_hop_laundering_is_refused(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)
        alice_token = producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice", t=T0)
        leaf = self._chain(fx, hops=2)
        self._assert_refusal(self._alice_reviews(fx, leaf["stage_id"], alice_token),
                             "self_review_via_lineage")
        self.assertEqual(
            fx["cx"].execute("SELECT COUNT(*) FROM publication_review WHERE stage_id=?",
                             (leaf["stage_id"],)).fetchone()[0], 0)
        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)

    # The documented bound, written as a LITERAL on purpose. Deriving the
    # chain depth from NSP.ANCESTRY_WALK_LIMIT would make this test move
    # whenever the constant moves -- i.e. it would silently accommodate the
    # exact regression it exists to catch. [INJECTED `ANCESTRY_WALK_LIMIT
    # = 4`: with the constant read from the module, 9/9 still passed.]
    DOCUMENTED_ANCESTRY_WALK_LIMIT = 64

    def test_the_documented_ancestry_walk_limit_has_not_shrunk(self):
        self.assertGreaterEqual(
            NSP.ANCESTRY_WALK_LIMIT, self.DOCUMENTED_ANCESTRY_WALK_LIMIT,
            "ANCESTRY_WALK_LIMIT was reduced -- a chain longer than the new "
            "value can now launder a self-review past the lineage guard")

    def test_laundering_at_the_last_hop_the_walk_is_allowed_to_see_is_refused(self):
        """Bound check: an ancestor DOCUMENTED_ANCESTRY_WALK_LIMIT links
        back must still be seen."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        alice_token = producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice", t=T0)
        leaf = self._chain(fx, hops=self.DOCUMENTED_ANCESTRY_WALK_LIMIT - 1)
        self._assert_refusal(self._alice_reviews(fx, leaf["stage_id"], alice_token),
                             "self_review_via_lineage")

    def test_a_deep_chain_that_alice_is_not_in_still_reviews_cleanly(self):
        """Sibling control for both of the above: a chain of the same
        depth with no alice anywhere must NOT be refused, or these tests
        would pass against a guard that simply rejects deep lineages."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        parent = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"root draft", destination_path="docs/report.md",
            classification="report", producer_identity="eng_root",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_root"),
            producer_run_id="run-root", snapshot_id=fx["snapshot_id"], t=T0)
        for i in range(3):
            parent = NSP.stage(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                content_bytes=f"clean hop {i}".encode(),
                destination_path="docs/report.md", classification="report",
                producer_identity=f"eng_clean{i}",
                producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], f"eng_clean{i}"), producer_run_id=f"run-clean{i}",
                derived_from_stage_id=parent["stage_id"],
                snapshot_id=fx["snapshot_id"], t=T0)
        rv = NSP.review(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            stage_id=parent["stage_id"], reviewer_identity="eng_bob",
            reviewer_run_id="rev-run-bob", token_bytes=fx["reviewer_token"],
            verdict="approve", t=T0)
        self.assertEqual(rv["verdict"], "approve")


@unittest.skipIf(NSP is None, "bin/nightshift_publication.py not importable")
class ProducerIdentityIsAuthenticated(PublicationGatewayTestCase):
    """Audit C8. `--producer-identity` was a bare argv string and PG-3 compared
    the authenticated reviewer against it, so a worker holding a reviewer
    token could stage its own work as someone else's and approve it."""

    def stage_as(self, fx, identity, token):
        return lambda: NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"bob's own work", destination_path="docs/report.md",
            classification="report", producer_identity=identity,
            producer_token_bytes=token, producer_run_id="run-bob",
            snapshot_id=fx["snapshot_id"], t=T0)

    def staged_rows(self, fx):
        return fx["cx"].execute("SELECT COUNT(*) FROM publication_stage").fetchone()[0]

    def test_a_reviewer_cannot_stage_its_own_work_as_someone_else(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice", t=T0)
        self._assert_refusal(self.stage_as(fx, "eng_alice", fx["reviewer_token"]),
                             "producer_not_authenticated")
        self._assert_refusal(self.stage_as(fx, "eng_nobody", fx["reviewer_token"]),
                             "producer_not_registered")
        with self.assertRaises(NSP.PublicationError) as ctx:
            self.stage_as(fx, "eng_alice", None)()
        self.assertEqual(ctx.exception.reason_code, "producer_identity_required")
        self.assertEqual(self.staged_rows(fx), 0)

    def test_staging_under_its_own_credential_still_refuses_the_self_review(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        st = self.stage_as(fx, "eng_bob", fx["reviewer_token"])()
        self._assert_refusal(
            lambda: NSP.review(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], reviewer_identity="eng_bob",
                reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
                verdict="approve", t=T0),
            "self_review")

    def test_a_revoked_producer_cannot_stage(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        token = producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice", t=T0)
        fx["cx"].execute("UPDATE publication_reviewer SET revoked_at=? "
                         "WHERE reviewer_identity='eng_alice'", (T0,))
        fx["cx"].commit()
        self._assert_refusal(self.stage_as(fx, "eng_alice", token), "producer_revoked")

    def test_the_cli_takes_the_producer_credential_from_a_file_not_argv(self):
        parser = NSP.build_parser()
        base = ["stage", "--state-root", "/x", "--tenant-id", "1", "--repository-id", "1",
                "--content-file", "c", "--destination-path", "d", "--classification", "r",
                "--producer-identity", "eng_alice", "--producer-run-id", "r1",
                "--snapshot-id", "s"]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(base)
        args = parser.parse_args(base + ["--producer-token-file", "t"])
        self.assertEqual(args.producer_token_file, "t")

    def test_token_digests_are_compared_in_constant_time(self):
        source = inspect.getsource(NSP._authenticate_reviewer)
        self.assertIn("hmac.compare_digest(", source)
        self.assertNotIn("sha256_hex(token_bytes) !=", source)


if __name__ == "__main__":
    unittest.main()
