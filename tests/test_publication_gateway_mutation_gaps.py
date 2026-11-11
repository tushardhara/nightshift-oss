"""Issue #695 -- PG-1/PG-2 mutation-evidence gap closure.

Every test in this file exists because a NAMED defect was injected into
`bin/nightshift_publication.py` and the suite as it stood at #693/#694
stayed green. Each class names the mutation it was written to catch, so a
later reader can re-inject it and watch this file go red rather than take
the claim on trust. The mutation table itself is
`scratchpad/run_mutations.py`; the recipe for each is in the class
docstring below.

Nothing here is mocked, stubbed or monkeypatched. Real SQLite, real git,
real bare destination repositories. The one `git` shim (see
`_write_corrupting_readback_shim`) is a real executable on PATH that
executes the real git binary for every call except the single one it
exists to corrupt -- the same fault-injection pattern
`test_publication_gateway._write_concurrent_git_race_shim` already uses,
and the opposite of a test that works around the condition it detects.
"""

import os
import shutil
import shlex
import unittest

from test_publication_gateway import (
    NSP, PROV, SKIP_REASON, PublicationGatewayTestCase, REF, T0,
    _ref_sha, _commit_count, _seed_commit, _update_ref,
)
from publication_identities import producer_token  # noqa: E402


# ---------------------------------------------------------------------------
# A real `git` on PATH that lies on exactly one call: `cat-file blob`.
# The gateway hashes the bytes git hands back and compares them to the
# digest the reviewer authorized -- this shim is the only way to reach
# that comparison, since a healthy git never returns bytes other than the
# ones it was given.
# ---------------------------------------------------------------------------

_READBACK_SHIM_BODY = """
set -u
if [ "${NIGHTSHIFT_TEST_CORRUPT_READBACK:-}" = "1" ] && [ "${1:-}" != "" ]; then
    prev=""
    is_readback=""
    for arg in "$@"; do
        if [ "$prev" = "cat-file" ] && [ "$arg" = "blob" ]; then
            is_readback=1
        fi
        prev="$arg"
    done
    if [ "$is_readback" = "1" ]; then
        "$REAL_GIT" "$@" >/dev/null || exit $?
        printf 'CORRUPTED BY THE READBACK SHIM\\n'
        exit 0
    fi
fi
exec "$REAL_GIT" "$@"
"""


def _write_corrupting_readback_shim(bin_dir):
    real_git = shutil.which("git")
    assert real_git, "a real `git` must be on PATH to build the readback shim"
    script = "#!/usr/bin/env bash\nREAL_GIT=" + shlex.quote(real_git) + "\n" + _READBACK_SHIM_BODY
    shim_path = os.path.join(bin_dir, "git")
    with open(shim_path, "w") as fh:
        fh.write(script)
    os.chmod(shim_path, 0o755)
    return shim_path


class GapClosureTestCase(PublicationGatewayTestCase):
    """Shared helpers for planting rows the public verbs cannot produce.

    Planting is deliberate and narrow: the module's own docstring calls the
    `tenant_id` column "the secondary, re-checked-on-every-verb" guarantee,
    behind physical per-tenant file separation. A row carrying a foreign
    tenant_id therefore cannot arrive through any public verb -- the only
    way to test the re-check at all is to write the row the re-check exists
    to catch. The schema's own CHECK/triggers still apply to every insert
    below; nothing here bypasses SQLite.
    """

    FOREIGN_TENANT = 9999

    def _clone_row_with(self, cx, table, row_id, **overrides):
        cols = [r["name"] for r in cx.execute(f"PRAGMA table_info({table})")]
        row = cx.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        self.assertIsNotNone(row, f"{table} row {row_id} must exist to clone it")
        values, names = [], []
        for c in cols:
            if c == "id":
                continue
            names.append(c)
            values.append(overrides[c] if c in overrides else row[c])
        placeholders = ",".join("?" for _ in names)
        cur = cx.execute(
            f"INSERT INTO {table}({','.join(names)}) VALUES({placeholders})", values)
        return cur.lastrowid

    def _assert_destination_unmoved(self, fx, count_before):
        self.assertEqual(_ref_sha(fx["git_dir"], REF), fx["base_sha"],
                         "HEAD must not have moved")
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before,
                         "no commit may have been created")


# ---------------------------------------------------------------------------
# PG-2. Three of the seven tuple elements are never exercised by
# `apply_publication`, because apply reads them straight off the very row
# the CAS then matches against (`reviewer_identity=auth["reviewer_identity"]`,
# `authorization_digest=auth["authorization_digest"]`, and tenant_id after
# an earlier equality refusal). Neutralising any of those three conjuncts
# left the whole suite green. They are live guards for any other caller of
# the CAS, so they are tested here at the function boundary that owns them.
#
# Mutations closed:
#   "AND tenant_id=?"            -> "AND (tenant_id=? OR 1=1)"
#   "AND reviewer_identity=?"    -> "AND (reviewer_identity=? OR 1=1)"
#   "AND authorization_digest=?" -> "AND (authorization_digest=? OR 1=1)"
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class CasTupleElementsApplyNeverExercises(GapClosureTestCase):

    def _live_authorization(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx)
        auth = fx["cx"].execute(
            "SELECT * FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()
        return fx, st, auth

    def _consume(self, fx, auth, **overrides):
        kwargs = dict(
            tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_digest=auth["content_digest"],
            destination_path=auth["destination_path"],
            observed_base_sha=auth["expected_base_sha"],
            reviewer_identity=auth["reviewer_identity"],
            authorization_digest=auth["authorization_digest"],
            attempt_id=1, t=T0 + 1,
        )
        kwargs.update(overrides)
        return NSP.consume_publication_authorization(fx["cx"], **kwargs)

    def test_the_unmodified_tuple_is_what_the_cas_consumes(self):
        """Control: without this the three negatives below would pass even
        if the CAS matched nothing at all."""
        fx, _st, auth = self._live_authorization()
        self.assertTrue(self._consume(fx, auth),
                        "the exact authorized 7-tuple must consume the row")
        after = fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (auth["id"],)).fetchone()
        self.assertIsNotNone(after["consumed_at"])

    def test_cas_refuses_a_tenant_id_the_authorization_row_does_not_carry(self):
        fx, _st, auth = self._live_authorization()
        self.assertFalse(self._consume(fx, auth, tenant_id=self.FOREIGN_TENANT),
                         "the CAS must not consume a row belonging to another tenant")
        self.assertIsNone(fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (auth["id"],)).fetchone()["consumed_at"])

    def test_cas_refuses_a_reviewer_identity_the_authorization_row_does_not_carry(self):
        fx, _st, auth = self._live_authorization()
        self.assertFalse(self._consume(fx, auth, reviewer_identity="eng_someone_else"),
                         "the CAS must bind the reviewer who approved the tuple")
        self.assertIsNone(fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (auth["id"],)).fetchone()["consumed_at"])

    def test_cas_refuses_an_authorization_digest_the_row_does_not_carry(self):
        fx, _st, auth = self._live_authorization()
        self.assertFalse(self._consume(fx, auth, authorization_digest="0" * 64),
                         "the CAS must bind the digest that seals the 7-tuple")
        self.assertIsNone(fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (auth["id"],)).fetchone()["consumed_at"])


# ---------------------------------------------------------------------------
# PG-2, seventh invalidation route: `revoked_at IS NULL`. `policy_set`
# revokes every live authorization when the mode switches to strict, and
# the schema's restricted-update trigger explicitly permits the revoke
# transition -- so a revoked row is a real state this module produces.
# Neutralising the conjunct left the suite green.
#
# Mutation closed:
#   "AND revoked_at IS NULL" -> "AND (revoked_at IS NULL OR 1=1)"
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class RefusalRevokedAuthorization(GapClosureTestCase):

    def test_apply_with_a_revoked_authorization_is_refused_and_writes_nothing(self):
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        count_before = _commit_count(fx["git_dir"], REF)

        # The same transition `policy_set` performs on a switch to strict
        # mode, and the only one the authorization trigger allows besides
        # consumption.
        fx["cx"].execute(
            "UPDATE publication_authorizations SET revoked_at=?, revoked_reason='mode_changed' "
            "WHERE id=?", (T0 + 1, ap["authorization_id"]))
        fx["cx"].commit()

        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st["stage_id"], idempotency_key="idem-revoked",
                                t=T0 + 2),
            "authorization_revoked")
        self._assert_destination_unmoved(fx, count_before)

    def test_policy_set_switching_to_strict_revokes_the_live_authorization(self):
        """The producer of that state, so the guard above is not defended
        against a condition nothing creates."""
        fx = self._new_fixture()
        _st, _rv, ap = self._stage_review_approve(fx)
        result = NSP.policy_set(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            mode="no_generated_file", path_allowlist=["**"], target_git_dir=None,
            target_ref=None, key_version=None, t=T0 + 1)
        self.assertEqual(result["revoked_authorizations"], 1)
        row = fx["cx"].execute(
            "SELECT revoked_at, revoked_reason FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()
        self.assertIsNotNone(row["revoked_at"])
        self.assertEqual(row["revoked_reason"], "mode_changed")


# ---------------------------------------------------------------------------
# PG-2/PG-3. Self-review is documented as checked at review AND approve AND
# apply. Deleting the apply-time re-assert left the suite green: every
# existing self-review test is caught at `review()`, so the two later
# re-asserts were carrying no test weight at all.
#
# The apply-time re-assert is genuinely reachable, because apply takes a
# `stage_id` argument that may name a DIFFERENT stage from the one the
# authorization was issued against -- the module's own comment says so.
#
# Mutation closed:
#   `_assert_not_self_review(cx, presented_stage, auth["reviewer_identity"], None)`
#   -> `pass`
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class SelfReviewIsReassertedAgainstThePresentedStage(GapClosureTestCase):

    def test_apply_refuses_a_presented_stage_produced_by_the_authorizing_reviewer(self):
        fx = self._new_fixture()
        # Authorization issued over a stage eng_alice produced, approved
        # by eng_bob -- entirely legitimate.
        st_ok, _rv, ap = self._stage_review_approve(
            fx, content=b"alice content\n", path="docs/report.md", producer="eng_alice")
        # A second stage, produced by the very reviewer who holds the
        # authorization. `review()` never saw this one.
        st_self = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"alice content\n", destination_path="docs/self.md",
            classification="report", producer_identity=fx["reviewer_identity"], producer_token_bytes=fx["reviewer_token"],
            producer_run_id="run-self", snapshot_id=fx["snapshot_id"], t=T0)
        count_before = _commit_count(fx["git_dir"], REF)

        exc = self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st_self["stage_id"],
                                idempotency_key="idem-self-at-apply", t=T0 + 1),
            "self_review")
        self.assertEqual(exc.details.get("stage_id"), st_self["stage_id"])
        self._assert_destination_unmoved(fx, count_before)
        # And the authorization is still live -- refused before the CAS,
        # so a legitimate apply of the correct stage is still possible.
        self.assertIsNone(fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()["consumed_at"])
        self.assertIsNotNone(st_ok["stage_id"])

    def test_the_identical_flow_with_a_third_party_producer_reaches_the_cas(self):
        """Control. Byte-for-byte the same manoeuvre, changing only the
        producer of the substituted stage: the refusal must then come from
        the CAS (`destination_path_mismatch`), not from self-review. That
        is what makes the test above evidence about the producer identity
        rather than about "apply refuses any stage it was not approved
        against"."""
        fx = self._new_fixture()
        _st_ok, _rv, ap = self._stage_review_approve(
            fx, content=b"alice content\n", path="docs/report.md", producer="eng_alice")
        st_other = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"alice content\n", destination_path="docs/self.md",
            classification="report", producer_identity="eng_carol",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_carol"),
            producer_run_id="run-carol", snapshot_id=fx["snapshot_id"], t=T0)
        count_before = _commit_count(fx["git_dir"], REF)
        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st_other["stage_id"],
                                idempotency_key="idem-third-party", t=T0 + 1),
            "destination_path_mismatch")
        self._assert_destination_unmoved(fx, count_before)


# ---------------------------------------------------------------------------
# PG-1. `approve` is the step that turns a review into a write credential.
# Three of its bindings to the review row carried no test weight at all:
# a review belonging to another stage, a rejected review, and a superseded
# review each produced a real authorization when the guard was removed.
#
# Mutations closed:
#   `review_row is None or review_row["stage_id"] != stage_id` -> `review_row is None`
#   `review_row["verdict"] != "approve"` -> `False and ...`
#   `latest_id != review_id` -> `False and ...`
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class ApproveIsBoundToItsOwnReviewRow(GapClosureTestCase):

    def _stage_and_review(self, fx, *, path, producer, run, verdict="approve",
                          reviewer_run="rev-run-1"):
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"c\n", destination_path=path, classification="report",
            producer_identity=producer,
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], producer), producer_run_id=run,
            snapshot_id=fx["snapshot_id"], t=T0)
        rv = NSP.review(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
            reviewer_run_id=reviewer_run, token_bytes=fx["reviewer_token"],
            verdict=verdict, t=T0)
        return st, rv

    def _approve(self, fx, stage_id, review_id, reviewer_run="rev-run-1"):
        return NSP.approve(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            stage_id=stage_id, review_id=review_id,
            reviewer_identity=fx["reviewer_identity"], reviewer_run_id=reviewer_run,
            token_bytes=fx["reviewer_token"], expected_base_sha=fx["base_sha"],
            ttl=3600, t=T0)

    def _assert_no_authorization(self, fx):
        count = fx["cx"].execute(
            "SELECT COUNT(*) FROM publication_authorizations").fetchone()[0]
        self.assertEqual(count, 0, "no write credential may exist after a refused approve")

    def test_approve_refuses_a_review_row_that_belongs_to_another_stage(self):
        fx = self._new_fixture()
        _st_a, rv_a = self._stage_and_review(fx, path="docs/a.md", producer="eng_alice",
                                             run="run-a")
        st_b, _rv_b = self._stage_and_review(fx, path="docs/b.md", producer="eng_alice",
                                             run="run-b", reviewer_run="rev-run-2")
        self._assert_refusal(
            lambda: self._approve(fx, st_b["stage_id"], rv_a["review_id"]),
            "review_not_found")
        self._assert_no_authorization(fx)

    def test_approve_refuses_a_rejected_review(self):
        fx = self._new_fixture()
        st, rv = self._stage_and_review(fx, path="docs/a.md", producer="eng_alice",
                                        run="run-a", verdict="reject")
        self._assert_refusal(
            lambda: self._approve(fx, st["stage_id"], rv["review_id"]),
            "review_verdict_reject")
        self._assert_no_authorization(fx)

    def test_approve_refuses_a_review_a_later_review_of_the_same_stage_superseded(self):
        fx = self._new_fixture()
        st, rv_first = self._stage_and_review(fx, path="docs/a.md", producer="eng_alice",
                                              run="run-a")
        NSP.review(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
            reviewer_run_id="rev-run-2", token_bytes=fx["reviewer_token"],
            verdict="reject", t=T0)
        exc = self._assert_refusal(
            lambda: self._approve(fx, st["stage_id"], rv_first["review_id"]),
            "review_superseded")
        self.assertNotEqual(exc.details.get("latest_review_id"), rv_first["review_id"])
        self._assert_no_authorization(fx)


# ---------------------------------------------------------------------------
# PG-1. The path allowlist is the policy statement of WHERE the gateway may
# write. Removing its enforcement at `approve` left the suite green: every
# existing fixture uses the `**` allowlist, so no test ever presented a
# destination the allowlist excludes.
#
# Mutation closed:
#   `if not path_allowlisted(destination_path, allowlist_patterns):` -> `if False and ...`
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class PathAllowlistIsEnforcedAtApprove(GapClosureTestCase):

    def test_approve_refuses_a_destination_outside_the_allowlist_and_writes_nothing(self):
        fx = self._new_fixture(path_allowlist=("docs/**",))
        count_before = _commit_count(fx["git_dir"], REF)
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"escaped\n", destination_path="src/main.py",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
            producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        rv = NSP.review(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
            reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
            verdict="approve", t=T0)
        exc = self._assert_refusal(
            lambda: NSP.approve(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], review_id=rv["review_id"],
                reviewer_identity=fx["reviewer_identity"], reviewer_run_id="rev-run-1",
                token_bytes=fx["reviewer_token"], expected_base_sha=fx["base_sha"],
                ttl=3600, t=T0),
            "destination_path_not_allowlisted")
        self.assertEqual(exc.details.get("destination_path"), "src/main.py")
        self.assertEqual(
            fx["cx"].execute("SELECT COUNT(*) FROM publication_authorizations").fetchone()[0], 0)
        self._assert_destination_unmoved(fx, count_before)

    def test_a_destination_inside_the_same_allowlist_is_still_approved(self):
        """Control: the refusal above is the allowlist, not approve being
        broken for this fixture shape."""
        fx = self._new_fixture(path_allowlist=("docs/**",))
        _st, _rv, ap = self._stage_review_approve(fx, path="docs/report.md")
        self.assertTrue(ap["ok"])


# ---------------------------------------------------------------------------
# PG-1/PG-5. `policy_set`'s own strict-mode guard. The existing test for
# this reason_code drives a raw SQL UPDATE and asserts the SCHEMA CHECK
# fires -- it never calls `policy_set` with strict mode plus a target, so
# deleting the application-level refusal left the suite green. With the
# guard gone the call still fails, but as `policy_check_failed` from the
# IntegrityError path: the caller loses the reason_code that names what
# they actually did wrong.
#
# Mutation closed:
#   `if mode == "no_generated_file" and (target_git_dir or target_ref):
#        _refuse("egress_target_forbidden_in_strict_mode")` -> `pass`
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class StrictModePolicyRefusesAnEgressTargetByItsOwnName(GapClosureTestCase):

    def test_policy_set_refuses_a_target_git_dir_in_strict_mode(self):
        fx = self._new_fixture()
        self._assert_refusal(
            lambda: NSP.policy_set(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                mode="no_generated_file", path_allowlist=["**"],
                target_git_dir=fx["git_dir"], target_ref=None, key_version=None, t=T0 + 1),
            "egress_target_forbidden_in_strict_mode")

    def test_policy_set_refuses_a_target_ref_in_strict_mode(self):
        fx = self._new_fixture()
        self._assert_refusal(
            lambda: NSP.policy_set(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                mode="no_generated_file", path_allowlist=["**"],
                target_git_dir=None, target_ref=REF, key_version=None, t=T0 + 1),
            "egress_target_forbidden_in_strict_mode")

    def test_the_policy_row_is_unchanged_after_the_refusal(self):
        fx = self._new_fixture()
        before = dict(NSP.get_policy(fx["cx"]))
        self._assert_refusal(
            lambda: NSP.policy_set(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                mode="no_generated_file", path_allowlist=["**"],
                target_git_dir=fx["git_dir"], target_ref=REF, key_version=None, t=T0 + 1),
            "egress_target_forbidden_in_strict_mode")
        self.assertEqual(dict(NSP.get_policy(fx["cx"])), before)


# ---------------------------------------------------------------------------
# PG-1/PG-6 seam. `apply_publication` re-checks tenant_id on BOTH the
# authorization row and the presented stage row. Neither re-check had a
# test: physical per-tenant file separation means no public verb can put a
# foreign-tenant row in this database, so the re-check is only reachable
# against a row that got there another way -- which is precisely the
# scenario the module docstring says the column exists for.
#
# Mutations closed:
#   `if auth["tenant_id"] != tenant_id: _refuse("tenant_mismatch")` -> `pass`
#   `if presented_stage["tenant_id"] != tenant_id or ...` -> `if False:`
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class ApplyRechecksTenantOnEveryRowItReads(GapClosureTestCase):

    def test_apply_refuses_a_foreign_tenant_authorization_before_it_touches_the_target(self):
        """The reason_code alone cannot carry this: the CAS's own
        `tenant_id=?` conjunct also diagnoses as `tenant_mismatch`, so
        deleting the early guard leaves the message identical. What
        changes is HOW FAR the gateway gets first. Without the early
        refusal, a planted row's `target_git_dir` -- a path this tenant
        never authorized -- is fed to `os.path.isdir`, `os.access`,
        `git rev-parse --is-bare-repository` and `git rev-parse` before
        anything notices whose row it is. So the assertion is ordering:
        the identity refusal must come out, not the target one."""
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        count_before = _commit_count(fx["git_dir"], REF)
        foreign_auth = self._clone_row_with(
            fx["cx"], "publication_authorizations", ap["authorization_id"],
            tenant_id=self.FOREIGN_TENANT,
            authorization_digest="f" * 64,  # the live-tuple unique index
            target_git_dir=os.path.join(self._tmp(), "a-path-this-tenant-never-authorized"))
        fx["cx"].commit()
        exc = self._assert_refusal(
            lambda: self._apply(fx, authorization_id=foreign_auth,
                                stage_id=st["stage_id"], idempotency_key="idem-foreign-auth",
                                t=T0 + 1),
            "tenant_mismatch")
        self.assertNotIn("target_git_dir", exc.details,
                         "the foreign row's target must never have been examined")
        self._assert_destination_unmoved(fx, count_before)

    def test_apply_refuses_a_stage_row_carrying_another_tenant(self):
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        count_before = _commit_count(fx["git_dir"], REF)
        foreign_stage = self._clone_row_with(
            fx["cx"], "publication_stage", st["stage_id"],
            tenant_id=self.FOREIGN_TENANT)
        fx["cx"].commit()
        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=foreign_stage, idempotency_key="idem-foreign-stage",
                                t=T0 + 1),
            "tenant_mismatch")
        self._assert_destination_unmoved(fx, count_before)
        self.assertIsNone(fx["cx"].execute(
            "SELECT consumed_at FROM publication_authorizations WHERE id=?",
            (ap["authorization_id"],)).fetchone()["consumed_at"])


# ---------------------------------------------------------------------------
# PG-1. Two guards on the git side of apply, neither of which had a test.
#
#  * The blob readback: the gateway hashes what git hands BACK and refuses
#    if it is not what the reviewer authorized. Only reachable with a git
#    that lies, hence the shim.
#  * `attempt_in_doubt`: an idempotency key whose attempt exists but did
#    NOT reach 'applied' must never be replayed -- the gateway cannot know
#    whether the earlier attempt's ref write landed. Removing the refusal
#    made a replay proceed to a second write; no test noticed.
#
# Mutations closed:
#   `if sha256_hex(readback.stdout) != presented_digest:` -> `if False and ...`
#   `_refuse("attempt_in_doubt", ...)` on the existing-attempt branch -> `pass`
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class WhatGitStoredIsReadBackAndVerified(GapClosureTestCase):

    def _apply_under_corrupting_git(self, fx, *, authorization_id, stage_id,
                                    idempotency_key, t):
        bin_dir = self._tmp()
        _write_corrupting_readback_shim(bin_dir)
        original_path = os.environ.get("PATH", "")
        original_flag = os.environ.get("NIGHTSHIFT_TEST_CORRUPT_READBACK")
        os.environ["PATH"] = bin_dir + os.pathsep + original_path
        os.environ["NIGHTSHIFT_TEST_CORRUPT_READBACK"] = "1"
        try:
            with self.assertRaises(NSP.PublicationError) as ctx:
                self._apply(fx, authorization_id=authorization_id, stage_id=stage_id,
                            idempotency_key=idempotency_key, t=t)
            return ctx.exception
        finally:
            os.environ["PATH"] = original_path
            if original_flag is None:
                os.environ.pop("NIGHTSHIFT_TEST_CORRUPT_READBACK", None)
            else:
                os.environ["NIGHTSHIFT_TEST_CORRUPT_READBACK"] = original_flag

    def test_a_git_that_returns_different_bytes_on_readback_is_refused(self):
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        count_before = _commit_count(fx["git_dir"], REF)
        exc = self._apply_under_corrupting_git(
            fx, authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
            idempotency_key="idem-readback", t=T0 + 1)
        self.assertEqual(exc.reason_code, "written_blob_digest_mismatch")
        self._assert_destination_unmoved(fx, count_before)
        attempt = fx["cx"].execute(
            "SELECT state, reason_code FROM publication_attempts WHERE idempotency_key=?",
            ("idem-readback",)).fetchone()
        self.assertEqual(attempt["state"], "failed")
        self.assertEqual(attempt["reason_code"], "written_blob_digest_mismatch")

    def test_the_same_idempotency_key_is_never_replayed_over_a_failed_attempt(self):
        """The attempt above is terminal but NOT applied. A replay must be
        refused in doubt -- the gateway cannot know whether the earlier
        attempt's ref write landed, so it must never try a second one."""
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        self._apply_under_corrupting_git(
            fx, authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
            idempotency_key="idem-doubt", t=T0 + 1)
        count_before = _commit_count(fx["git_dir"], REF)
        exc = self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st["stage_id"], idempotency_key="idem-doubt",
                                t=T0 + 2),
            "attempt_in_doubt")
        self.assertEqual(exc.details.get("state"), "failed")
        self._assert_destination_unmoved(fx, count_before)


# ---------------------------------------------------------------------------
# The suite must not be able to go green because the module under test did
# not load. `_load_publication_module` is deliberately fail-soft -- it
# returns None on any import error so the suite "skips with a reason
# instead of erroring at collection" -- and every class in this file and in
# test_publication_gateway.py is `skipIf(NSP is None)`. Measured:
#
#   $ printf '\nthis is not valid python(\n' >> bin/nightshift_publication.py
#   $ python3 -m unittest test_publication_gateway
#   OK (skipped=25)                     # exit status 0
#
# That is the #695 failure mode exactly: the one code path allowed to write
# into a customer repository stopped parsing, and the run reported success.
# A full-suite run does catch it today, but only by accident --
# test_publication_footprint.py happens to import the module at module
# scope, so it hard-errors. Any targeted run is green.
#
# This class is NOT skipIf-guarded, so the skip becomes a failure.
# ---------------------------------------------------------------------------

class TheModuleUnderTestMustActuallyHaveLoaded(unittest.TestCase):

    def test_nightshift_publication_imported(self):
        self.assertIsNotNone(
            NSP,
            "bin/nightshift_publication.py did not import, so every publication "
            "test in this tree just SKIPPED rather than ran. Reason: " + str(SKIP_REASON))

    def test_nightshift_provenance_is_reachable_through_it(self):
        self.assertIsNotNone(NSP, "nightshift_publication did not import")
        self.assertIsNotNone(
            PROV, "nightshift_provenance is not reachable through the module under "
                  "test; the #694 seam tests would silently skip")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# PG-1/PG-2/PG-3 backstops. Two guards survived every mutation above with
# the whole suite green, and neither is reachable through ANY sequence of
# public verbs:
#
#  * `approve`'s self-review re-assert is fed inputs it has already proven
#    identical to the ones `review()` checked (approve refuses unless the
#    authenticated identity and run id equal the review row's), and
#    `_ancestry_producer_identities` walks `publication_stage.
#    derived_from_stage_id`, which is immutable. Same inputs, same
#    immutable data, so it can never reach a different verdict than
#    `review()` did.
#  * `apply`'s `no_egress_credential` needs policy.mode == auth.mode ==
#    "no_generated_file", but `approve` is the only writer of auth.mode and
#    refuses outright in strict mode, so auth.mode can never hold that
#    value. `mode_changed_since_approval`, one line earlier, always fires
#    first.
#
# They are NOT dead: both fire against a row that did not come from the
# verb that normally writes it. That is the same threat model the module
# docstring names for the tenant_id column -- "the secondary,
# re-checked-on-every-verb" guarantee behind physical file separation --
# so they are pinned here at the level where they are real, and the
# shadowing order is pinned too, so a reordering shows up as a failure
# rather than as silence.
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class BackstopsAgainstRowsTheirOwnVerbNeverWrote(GapClosureTestCase):

    def test_approve_reasserts_self_review_on_a_review_row_review_never_wrote(self):
        fx = self._new_fixture()
        # A stage produced by the registered reviewer. `review()` would
        # refuse this outright, so the review row is planted instead --
        # exactly the case approve's re-assert exists for.
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"self produced\n", destination_path="docs/self.md",
            classification="report", producer_identity=fx["reviewer_identity"], producer_token_bytes=fx["reviewer_token"],
            producer_run_id="run-self", snapshot_id=fx["snapshot_id"], t=T0)
        self._assert_refusal(
            lambda: NSP.review(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
                reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
                verdict="approve", t=T0),
            "self_review")

        digest = NSP.sha256_hex(b"self produced\n")
        cur = fx["cx"].execute(
            "INSERT INTO publication_review(stage_id,reviewer_identity,reviewer_run_id,"
            "reviewed_digest,verdict,notes,reviewed_at) VALUES(?,?,?,?,?,?,?)",
            (st["stage_id"], fx["reviewer_identity"], "rev-run-1", digest,
             "approve", None, T0))
        fx["cx"].commit()

        self._assert_refusal(
            lambda: NSP.approve(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], review_id=cur.lastrowid,
                reviewer_identity=fx["reviewer_identity"], reviewer_run_id="rev-run-1",
                token_bytes=fx["reviewer_token"], expected_base_sha=fx["base_sha"],
                ttl=3600, t=T0),
            "self_review")
        self.assertEqual(
            fx["cx"].execute(
                "SELECT COUNT(*) FROM publication_authorizations").fetchone()[0], 0,
            "no write credential may be issued over a self-reviewed stage")

    def test_apply_refuses_egress_when_policy_and_authorization_are_both_strict(self):
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        count_before = _commit_count(fx["git_dir"], REF)
        # Strict mode. This revokes the live authorization, so a planted
        # copy carrying mode='no_generated_file' is what reaches apply.
        NSP.policy_set(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            mode="no_generated_file", path_allowlist=["**"], target_git_dir=None,
            target_ref=None, key_version=None, t=T0 + 1)
        policy = NSP.get_policy(fx["cx"])
        strict_auth = self._clone_row_with(
            fx["cx"], "publication_authorizations", ap["authorization_id"],
            mode="no_generated_file", authorization_digest="e" * 64,
            policy_version=policy["policy_version"],
            path_allowlist_digest=policy["path_allowlist_digest"])
        fx["cx"].commit()
        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=strict_auth,
                                stage_id=st["stage_id"], idempotency_key="idem-strict",
                                t=T0 + 2),
            "no_egress_credential")
        self._assert_destination_unmoved(fx, count_before)

    def test_mode_changed_since_approval_shadows_it_on_every_public_path(self):
        """The ordering that makes the guard above unreachable in normal
        operation. Pinned so a reorder is a test failure, not a silent
        change in which refusal a caller sees."""
        fx = self._new_fixture()
        st, _rv, ap = self._stage_review_approve(fx)
        NSP.policy_set(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            mode="no_generated_file", path_allowlist=["**"], target_git_dir=None,
            target_ref=None, key_version=None, t=T0 + 1)
        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                stage_id=st["stage_id"], idempotency_key="idem-shadow",
                                t=T0 + 2),
            "mode_changed_since_approval")
