"""Issue #400 -- direct tests against the 23-condition acceptance contract
named in the epic body (`gh issue view 400`), plus INDEX-CANNOT-LIE: the
meta-guard over the contract itself.

WHERE THE 23 CONDITIONS COME FROM. `gh issue view 400` (checked directly
against the live issue, not a cached copy) carries the epic's own
"Non-negotiable acceptance contract", 23 numbered items. A PREVIOUS
session's working notes described a DIFFERENT artifact -- a 14-item list
with `how_proven` fields naming exact test classes -- but that list was
never committed anywhere in this repository: `grep -rn "INDEX-CANNOT-LIE"`
across every worktree and `gh issue view 400 --comments` both return
nothing; the phrase's only appearance anywhere is PR #702's own body
("Known majors -- disclosed, not fixed", item 1), reporting the gap, not
defining the list it refers to. So `ACCEPTANCE_INDEX` below is built over
the 23 conditions the issue body actually states, each transcribed
verbatim, rather than over a list that does not exist to check against.

Condition03StrictModeReturnsReviewedPatches -- acceptance condition 3,
verbatim: "A stricter no-generated-file mode disables publication and
returns reviewed patches for the customer to apply." Before an earlier
stream's fix (`bin/nightshift_publication.py`, `patch_stage`),
no_generated_file mode's `patch` required only a stage row and happily
emitted an UNREVIEWED diff, contradicting the AC's own word "reviewed".
These tests exercise the real gate against a real SQLite publication.db +
real git fixture, reusing `tests/test_publication_gateway.py`'s fixture
helpers.

INDEX-CANNOT-LIE (`AcceptanceIndexCannotLie` / `IndexAuditorDetectsRigging`
below) is the anti-rigging meta-guard PR #702 disclosed as never having
been built: the guard whose entire job is to stop the acceptance list
overstating itself. `audit_acceptance_index()` re-imports and RE-RUNS, in
process, every test any entry below names as its proof -- a claim that
merely resolves but does not execute green (skipped, failed, errored, or
never collected) is caught exactly like a claim naming a test that does
not exist at all, because both of those happened in this repository in
the same day (see PR #702's body and this stream's own brief).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_provenance as prov          # noqa: E402
import nightshift_tenant_keys as keys         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_publication_gateway import (  # noqa: E402
    NSP, PublicationGatewayTestCase, T0,
)

TESTS_DIR = Path(__file__).resolve().parent


# ===========================================================================
# INDEX-CANNOT-LIE -- the acceptance index, and the guard over it.
# ===========================================================================
#
# ACEntry is DELIBERATELY unvalidated at construction time: every invariant
# a real entry must satisfy is enforced by `audit_acceptance_index()`
# below, never by `__init__`, so a test can build a lying entry at all in
# order to prove the auditor catches it (the same shape
# nightshift_authority_matrix.DataClass uses for a DIFFERENT purpose --
# there, construction-time enforcement is exactly the point, because AC 4
# needs "two authorities for one data class" to be unrepresentable; here,
# the guard's OWN test coverage needs rigged entries to be representable).

STATUS_MET = "met"
STATUS_UNMET_PINNED = "unmet_pinned"
STATUS_NOT_PROVEN = "not_proven"
STATUS_REFUSED = "refused"
_STATUSES = (STATUS_MET, STATUS_UNMET_PINNED, STATUS_NOT_PROVEN, STATUS_REFUSED)

#: The two guard classes below, by name. A `proven_by` id whose class is
#: one of these is refused BEFORE any attempt to resolve or run it -- doing
#: otherwise is an infinite loop (the guard would execute itself auditing
#: itself auditing itself...).
_GUARD_CLASSES = frozenset({"AcceptanceIndexCannotLie", "IndexAuditorDetectsRigging"})


class ACEntry:
    """One row of the acceptance index. `ac_id` is `"AC<N>"`, N in 1..23,
    matching #400's own numbering exactly (see the module docstring for
    where the 23 conditions come from). `statement` is transcribed
    verbatim from the issue body -- NOT machine-checked against GitHub
    (tests must not read state git does not carry, #698); see
    `test_the_verbatim_statements_are_NOT_machine_checked_against_the_epic`
    below for that stated limit, recorded rather than silent.

    `status` is one of:
      met            -- the AC holds; `proven_by` names >=1 real, running,
                         green test.
      unmet_pinned   -- the AC does NOT hold today; `proven_by` names a
                         real, running, green test that PINS the exact
                         deviation, and `deviation` states it in one line.
      not_proven     -- honestly withdrawn: `proven_by` MUST be empty, and
                         `reason` says why this guard does not back it.
      refused        -- formally declined by an ADR: `proven_by` MUST be
                         empty, and `refused_by` names the ADR file (under
                         docs/decisions/) that must exist on disk.
    """

    __slots__ = ("ac_id", "statement", "statement_source", "status",
                 "proven_by", "reason", "deviation", "refused_by")

    def __init__(self, ac_id, statement, status, *, proven_by=(), reason=None,
                 deviation=None, refused_by=None,
                 statement_source="issue #400 body, transcribed"):
        self.ac_id = ac_id
        self.statement = statement
        self.statement_source = statement_source
        self.status = status
        self.proven_by = tuple(proven_by)
        self.reason = reason
        self.deviation = deviation
        self.refused_by = refused_by


def _ac(ac_id, statement, status, **kw):
    return ACEntry(ac_id, statement, status, **kw)


# ---------------------------------------------------------------------------
# The 23 entries. Every `proven_by` id is a full `module.Class.method`
# unittest test id -- exactly the string `unittest.defaultTestLoader.
# loadTestsFromName()` accepts, so the auditor never has to re-derive it.
# Every one of these was independently located AND RUN GREEN by hand
# before being added here (see this stream's own report); the live audit
# test below (`test_the_real_acceptance_index_is_honest`) re-proves every
# single one of these claims every time the suite runs, so a future rename
# or deletion of any named test turns this red rather than staying a
# comment nobody re-checks.
# ---------------------------------------------------------------------------

ACCEPTANCE_INDEX = (
    _ac("AC1",
        "A fresh run against a clean customer repository produces no "
        "unrequested file, staging, ref, index, worktree-metadata, "
        "alternates, lock-file, ignore-file, or `.git` change.",
        STATUS_MET,
        proven_by=(
            # Pinned to the deterministic-fixture twin, NOT
            # ZeroFootprintAgainstTheBorrowedTestbed: that class's own
            # setUpClass skips (unittest.SkipTest) when the machine-local,
            # gitignored /home/operator/testbeds/.nightshift-testbed
            # checkout is absent -- every CI runner and every other
            # engineer's machine -- and this guard's own auditor scores a
            # skip as unexecuted_claim (#698: a proven_by test must not
            # read state git does not carry). The deterministic twin
            # proves the identical property against a throwaway repo it
            # builds itself, so it is guaranteed to exist and run green
            # everywhere. See tests/test_zero_footprint.py's
            # ZeroFootprintAgainstADeterministicFixture docstring.
            "test_zero_footprint.ZeroFootprintAgainstADeterministicFixture."
            "test_full_bootstrap_sync_search_verify_cycle_leaves_no_trace",
        )),
    _ac("AC2",
        "Zero-Nightshift-file mode is the default for new customer "
        "installations.",
        STATUS_UNMET_PINNED,
        deviation=(
            "bin/nightshift_publication.py's `--mode` CLI flag is "
            "required=True and `policy_set()`'s `mode` keyword has no "
            "default either -- no code path puts a fresh installation on "
            "zero_nightshift_file without a separate, explicit policy-set "
            "call."),
        proven_by=(
            "test_400_acceptance_conditions.Condition02ZeroFileModeIsNotADefault."
            "test_policy_set_requires_an_explicit_mode_so_no_default_exists",
            "test_400_acceptance_conditions.Condition02ZeroFileModeIsNotADefault."
            "test_the_function_layer_also_has_no_default_mode_keyword",
        )),
    _ac("AC3",
        "A stricter no-generated-file mode disables publication and "
        "returns reviewed patches for the customer to apply.",
        STATUS_MET,
        proven_by=(
            "test_400_acceptance_conditions.Condition03StrictModeReturnsReviewedPatches."
            "test_strict_mode_patch_without_a_review_is_refused",
        )),
    _ac("AC4",
        "Every data class names exactly one durable authority. Derived "
        "files and views are visibly projections and cannot override it.",
        STATUS_MET,
        proven_by=(
            "test_authority_matrix.MatrixCoversEveryTable."
            "test_every_real_table_has_exactly_one_declared_authority",
            "test_authority_matrix.ProjectionsCannotOverrideAuthority."
            "test_a_poisoned_control_state_projection_does_not_change_a_slot_verdict",
        )),
    _ac("AC5",
        "Control decisions are computed only from transactional control "
        "rows inside the authorizing transaction.",
        STATUS_MET,
        proven_by=(
            "test_slots.RT17ConcurrentAcquiresShareOneBudget."
            "test_concurrent_acquires_converge_on_one_normal_worker_ceiling",
        )),
    _ac("AC6",
        "Missing, stale, duplicated, reordered, poisoned, or adversarial "
        "semantic retrieval cannot change scheduling, capacity, claim, "
        "lease, checkpoint, or publication authorization.",
        STATUS_MET,
        proven_by=(
            "test_memory_boundary.MemoryDbNeverSharesATransactionWithControlDb."
            "test_connect_accepts_no_parameter_that_could_carry_a_control_plane_handle",
        )),
    _ac("AC7",
        "Local SQLite and production PostgreSQL return normalized "
        "equivalent outcomes for fencing, idempotency, concurrency, "
        "recovery, isolation, and refusal cases.",
        STATUS_REFUSED,
        refused_by="ADR-0083-cipher-selection-and-deferred-postgres-parity.md"),
    _ac("AC8",
        "Large artifacts use content-addressed storage with tenant, "
        "classification, checksum, size, provenance, retention, key "
        "version, and immutable object reference in the manifest.",
        STATUS_MET,
        proven_by=(
            "test_publication_gateway.SourceSnapshotIdentityAndClosure."
            "test_stage_records_the_identity_fields_verbatim_on_a_real_publication",
        )),
    _ac("AC9",
        "Cross-tenant reads, searches, deduplication, exports, "
        "publications, and deletions fail closed and have negative tests.",
        STATUS_MET,
        proven_by=(
            "test_provenance.TenancyPhysicalSeparation."
            "test_cross_tenant_dedupe_two_independent_locators_no_oracle",
            "test_provenance_lineage.CrossTenantReadSearchExportDelete."
            "test_cross_tenant_delete_fails_closed",
            "test_provenance_lineage.CrossTenantReadSearchExportDelete."
            "test_cross_tenant_export_fails_closed",
            "test_provenance_lineage.CrossTenantPublishFailsClosed."
            "test_apply_under_a_different_tenant_is_refused_and_authorization_stays_live",
        )),
    _ac("AC10",
        "Every run, artifact, decision, evidence item, and publication "
        "records stable repository identity plus exact commit/tree OIDs "
        "and lineage.",
        STATUS_MET,
        proven_by=(
            "test_publication_provenance_seam.SnapshotToLandedCommitIsOneFlow."
            "test_snapshot_stage_review_approve_apply_lands_and_the_chain_closes",
        )),
    _ac("AC11",
        "Provenance closure covers required submodule commits and Git "
        "LFS objects.",
        STATUS_MET,
        proven_by=(
            "test_provenance_lineage.ClosureDiscoveryAndRequirement."
            "test_discover_closure_finds_the_real_submodule_and_lfs_entries",
        )),
    _ac("AC12",
        "A permitted dirty checkout is copied externally and represented "
        "by a marked synthetic snapshot; Nightshift does not mutate "
        "customer Git metadata to describe it.",
        STATUS_MET,
        proven_by=(
            "test_provenance.DirtyCheckoutSyntheticSnapshot."
            "test_synthetic_snapshot_is_marked_not_presented_as_upstream",
        )),
    _ac("AC13",
        "Publication requires a one-use authorization bound to tenant, "
        "repository, exact content digest, destination path, expected "
        "base SHA, reviewer, and expiry. Any change invalidates it.",
        STATUS_MET,
        proven_by=(
            "test_publication_gateway.RefusalReuseOfConsumedAuthorization."
            "test_second_apply_with_same_authorization_is_refused_and_writes_nothing",
        )),
    _ac("AC14",
        "The producing worker cannot approve its own publication. "
        "Internal prompts, transcripts, credentials, hidden reasoning, "
        "and unreviewed operational memory are denied by the normal "
        "publication path.",
        STATUS_MET,
        proven_by=(
            "test_publication_gateway.RefusalSelfReview."
            "test_producer_cannot_review_own_publication_and_writes_nothing",
            "test_400_acceptance_conditions."
            "Condition14CredentialAndTranscriptClassificationsAreDenied."
            "test_every_denied_classification_is_refused_at_stage",
        )),
    _ac("AC15",
        "Retention and deletion walk `derived_from` lineage across "
        "source bodies, chunks, summaries, embeddings, caches, and "
        "exports. Completion is auditable and idempotent.",
        STATUS_UNMET_PINNED,
        deviation=(
            "purge_lineage_node()'s own docstring, verbatim: \"No production "
            "caller today can honestly pass a full `covers`, so every real "
            "deletion receipts `closed=0`\" -- bin/nightshift_tenant_keys.py:983 "
            "is the ONLY production caller (purge_lineage_with_keys) and it "
            "calls positionally, passing neither `state_root` nor `covers`, so "
            "the real path walks only source-body content_carrying_descendants "
            "(one of the six named classes) and receipts "
            "cas_scope='not_attempted_no_state_root', closed=0, "
            "covered_classes=[], uncovered_classes=all six. The six-class walk "
            "and closed=1 are real and tested, but only reachable through a "
            "direct call this stream's own tests make -- never through the "
            "real production path. Auditability holds either way (the receipt "
            "honestly names what it did NOT reach); the AC's own completeness "
            "claim does not, today."),
        proven_by=(
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_a_purge_writes_one_durable_receipt_naming_every_deleted_node",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_a_purge_receipt_cannot_be_updated_or_deleted",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_a_purge_with_a_state_root_deletes_the_cas_bodies_it_receipts",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_without_a_state_root_the_receipt_says_not_attempted_rather_than_zero_removed",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_a_second_purge_changes_nothing_and_leaves_exactly_one_receipt",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_closed_is_false_when_a_named_class_was_not_walked",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_full_coverage_and_verified_cas_absence_receipts_closed_true",
            "test_400_acceptance_conditions.Condition15DeletionIsAuditableAndIdempotent."
            "test_the_real_composed_deletion_path_receipts_closed_false",
        )),
    _ac("AC16",
        "Backup-resident data follows a declared maximum expiry window; "
        "the product must not falsely report instantaneous physical "
        "erasure.",
        STATUS_MET,
        proven_by=(
            "test_tenant_keys.DeletionReceiptCannotLie."
            "test_the_receipt_publishes_the_35_day_backup_window",
        )),
    _ac("AC17",
        "Tenant export is portable and versioned, includes checksums "
        "and provenance, and can be verified without trusting the "
        "producing Nightshift instance.",
        STATUS_MET,
        proven_by=(
            "test_tenant_export.VerifyNeedsOnlyTheBundle."
            "test_verification_succeeds_after_the_state_root_is_deleted",
        )),
    _ac("AC18",
        "Migration preserves exactly one authority: inventory, "
        "snapshot, shadow import, compare, quiesce, change one authority "
        "epoch, verify, and only then decommission. Rollback must not "
        "merge divergent dual-writer histories.",
        STATUS_MET,
        proven_by=(
            "test_authority_epoch.QuiescedSwitch."
            "test_the_one_flip_increments_the_epoch_and_logs_it",
            "test_authority_epoch.RollbackIsNotAToggle."
            "test_rollback_into_a_fresh_copy_advances_a_new_epoch",
        )),
    _ac("AC19",
        "Numeric capacity and latency triggers govern any move to "
        "Redis, a broker, dedicated vector storage, separated services, "
        "or higher infrastructure tiers.",
        STATUS_MET,
        proven_by=(
            "test_scale_ladder_triggers.EveryLadderRowHasANumericTrigger."
            "test_every_row_switch_when_cell_carries_a_real_number",
        )),
    _ac("AC20",
        "The design remains a modular monolith at the initial measured "
        "load.",
        STATUS_MET,
        proven_by=(
            "test_scale_ladder_triggers.NoTierOneDependencyIsImported."
            "test_no_bin_file_imports_a_tier_one_dependency",
        )),
    _ac("AC21",
        "Operations expose volume, latency, outbox age, "
        "retention/deletion backlog, export duration, publication queue "
        "time, isolation failures, and restore evidence.",
        STATUS_MET,
        proven_by=(
            "test_observability.TheSignalSetIsExactlyTheEightNamed."
            "test_required_signals_matches_the_eight_named_in_the_ac_verbatim",
        )),
    _ac("AC22",
        "Existing control-plane capacity, holder binding, one-dispatch "
        "capability, on-call reserve, and fail-closed policy behavior "
        "remain unchanged unless a separately reviewed contract "
        "explicitly supersedes them.",
        STATUS_MET,
        proven_by=(
            "test_slots.RT32UnauthenticatedReleaseCannotDuplicateDispatch."
            "test_slot_release_wrong_holder_denied_no_duplicate_row",
            "test_slots.GlobalSemaphore.test_oncall_reserve_usable_only_by_oncall",
        )),
    _ac("AC23",
        "Cleanup of legacy `.omni`, refs, clones, or worktrees occurs "
        "only after the new authority, export, rollback, and "
        "exact-footprint proof succeed.",
        STATUS_MET,
        proven_by=(
            "test_400_acceptance_conditions.Condition23CleanupIsGated."
            "test_no_module_deletes_legacy_material_ungated",
            "test_authority_matrix.CleanupGateRefusesByDefault."
            "test_all_four_true_is_the_only_passing_combination",
        )),
)


class IndexReport:
    """Every bucket below is a list of human-readable strings naming the
    offending AC id and the specific problem; ANY non-empty bucket means
    `.ok` is False. There is deliberately no bucket that is impossible to
    populate through this module's own public surface -- every one has a
    detector test below proving a rigged entry lands in it."""

    _BUCKETS = ("coverage", "missing_test", "not_method_level", "empty_claim",
                "unexecuted_claim", "overclaimed_status", "unbacked_refusal",
                "missing_reason", "self_reference", "nondeterministic_claim")

    def __init__(self, **buckets):
        for name in self._BUCKETS:
            setattr(self, name, sorted(buckets.get(name, ())))

    @property
    def ok(self):
        return not any(getattr(self, name) for name in self._BUCKETS)

    def as_dict(self):
        return {"ok": self.ok, **{name: getattr(self, name) for name in self._BUCKETS}}


def _manifest_test_ids():
    """Every test id ALLOWED to skip under the host-UID-remap sandbox
    (tests/host-uid-skip-manifest.json) -- a tracked file inside this
    worktree, read directly, never a git ref (#698)."""
    data = json.loads((TESTS_DIR / "host-uid-skip-manifest.json").read_text(encoding="utf-8"))
    return frozenset(data["tests"])


def _run_named_test(test_id):
    """Resolve and RUN `test_id` (`module.Class.method`) in process with a
    fresh `TestResult`. Returns `(bucket, detail)`:
      (None, None)             -- resolved, ran, exactly one green test.
      ('not_method_level', .)  -- fewer than 3 dot-separated segments.
      ('missing_test', .)      -- import/getattr/loadTestsFromName failed;
                                   `detail` carries the real exception text,
                                   never swallowed by a bare `except: pass`.
      ('unexecuted_claim', .)  -- resolved, but did not run as exactly one
                                   green test (skipped, failed, errored, or
                                   the id matched zero or >1 tests).
    """
    parts = test_id.split(".")
    if len(parts) != 3:
        return "not_method_level", test_id
    module_name, class_name, method_name = parts
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        getattr(cls, method_name)
    except Exception as exc:
        return "missing_test", f"{test_id}: {type(exc).__name__}: {exc}"
    suite = unittest.defaultTestLoader.loadTestsFromName(test_id)
    result = unittest.TestResult()
    suite.run(result)
    if result.testsRun != 1 or result.skipped or result.failures or result.errors:
        return "unexecuted_claim", (
            f"{test_id}: testsRun={result.testsRun} skipped={len(result.skipped)} "
            f"failures={len(result.failures)} errors={len(result.errors)}")
    return None, None


def audit_acceptance_index(index, *, adr_dir=None, manifest_ids=None, expected_ids=None):
    """The entire INDEX-CANNOT-LIE check. Returns an `IndexReport`.

    `adr_dir` / `manifest_ids` / `expected_ids` are injectable ONLY so the
    synthetic detector tests below never have to touch the real
    docs/decisions tree, the real manifest file, or the real 23-condition
    universe to prove the CHECKS work in isolation -- the live audit
    (`test_the_real_acceptance_index_is_honest`) calls this with none of
    them, so it always runs against the real repository, on disk, over
    the real AC1..AC23 universe.
    """
    adr_dir = (REPO / "docs" / "decisions") if adr_dir is None else adr_dir
    manifest_ids = _manifest_test_ids() if manifest_ids is None else manifest_ids
    expected = {f"AC{i}" for i in range(1, 24)} if expected_ids is None else set(expected_ids)

    counts = {}
    for e in index:
        counts[e.ac_id] = counts.get(e.ac_id, 0) + 1
    coverage = (
        [f"missing:{ac_id}" for ac_id in sorted(expected - set(counts))]
        + [f"duplicate:{ac_id}" for ac_id in sorted(k for k, c in counts.items() if c > 1)]
        + [f"unexpected:{ac_id}" for ac_id in sorted(set(counts) - expected)]
    )

    buckets = {name: [] for name in IndexReport._BUCKETS}
    buckets["coverage"] = coverage

    for e in index:
        if e.status not in _STATUSES:
            buckets["missing_test"].append(f"{e.ac_id}: unknown status {e.status!r}")
            continue

        # A `deviation` is a confession that the AC does NOT hold as
        # stated -- `unmet_pinned` is the only status allowed to carry
        # one. Any other status pairing a deviation with a status word
        # that says otherwise (most concretely: `met` claiming the AC
        # holds while its own deviation field says it does not) is an
        # overclaim exactly as real as naming a nonexistent test, and
        # unlike topical relevance (see
        # `test_a_pinned_tests_topical_relevance_to_its_ac_is_NOT_machine_checked`
        # below for that residual, unclosable gap) this one is cheap and
        # mechanical to catch outright.
        if e.deviation and e.status != STATUS_UNMET_PINNED:
            buckets["overclaimed_status"].append(
                f"{e.ac_id}: status={e.status} carries a deviation "
                f"{e.deviation!r} -- deviation is unmet_pinned-only")

        claims_a_test = e.status in (STATUS_MET, STATUS_UNMET_PINNED)
        if claims_a_test:
            if not e.proven_by:
                buckets["empty_claim"].append(e.ac_id)
            if e.status == STATUS_UNMET_PINNED and not e.deviation:
                buckets["missing_reason"].append(
                    f"{e.ac_id}: unmet_pinned with no deviation stated")
        else:
            if e.proven_by:
                buckets["overclaimed_status"].append(
                    f"{e.ac_id}: status={e.status} names {list(e.proven_by)}")
            if e.status == STATUS_NOT_PROVEN and not e.reason:
                buckets["missing_reason"].append(
                    f"{e.ac_id}: not_proven with no reason stated")
            if e.status == STATUS_REFUSED:
                if not e.refused_by:
                    buckets["unbacked_refusal"].append(f"{e.ac_id}: refused with no ADR named")
                elif not (adr_dir / e.refused_by).is_file():
                    buckets["unbacked_refusal"].append(
                        f"{e.ac_id}: refused_by {e.refused_by!r} not on disk")

        for test_id in e.proven_by:
            id_parts = test_id.split(".")
            class_name = id_parts[1] if len(id_parts) > 1 else ""
            if class_name in _GUARD_CLASSES:
                buckets["self_reference"].append(f"{e.ac_id}: {test_id}")
                continue
            if test_id in manifest_ids:
                buckets["nondeterministic_claim"].append(f"{e.ac_id}: {test_id}")
                continue
            if not claims_a_test:
                continue  # already named in overclaimed_status; do not also run it
            bucket, detail = _run_named_test(test_id)
            if bucket is not None:
                buckets[bucket].append(f"{e.ac_id}: {detail}")

    return IndexReport(**buckets)


class Condition03StrictModeReturnsReviewedPatches(PublicationGatewayTestCase):

    def test_strict_mode_patch_without_a_review_is_refused(self):
        fx = self._new_fixture(mode="no_generated_file")
        st = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"unreviewed content\n", destination_path="docs/s.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        with self.assertRaises(NSP.PublicationError) as ctx:
            NSP.patch_stage(fx["cx"], stage_id=st["stage_id"], tenant_id=fx["tenant_id"],
                             repository_id=fx["repository_id"])
        self.assertEqual(ctx.exception.reason_code, "patch_requires_review")
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_strict_mode_patch_after_a_rejected_review_is_still_refused(self):
        fx = self._new_fixture(mode="no_generated_file")
        cx = fx["cx"]
        st = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"content\n", destination_path="docs/s.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        NSP.review(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                   stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
                   reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
                   verdict="reject", t=T0)
        with self.assertRaises(NSP.PublicationError) as ctx:
            NSP.patch_stage(cx, stage_id=st["stage_id"], tenant_id=fx["tenant_id"],
                             repository_id=fx["repository_id"])
        self.assertEqual(ctx.exception.reason_code, "patch_requires_review")

    def test_the_producer_cannot_review_its_own_strict_mode_patch(self):
        """`review()` itself already refuses a same-identity review at
        insert time (proven elsewhere, RefusalSelfReview). This test
        proves patch_stage's OWN re-assertion (the "checked at every gate"
        line the fix adds) actually runs, by inserting a self-reviewing
        row DIRECTLY -- bypassing review()'s own guard entirely, the only
        way to reach patch_stage with such a row on the books at all --
        and confirming patch_stage still refuses rather than trusting the
        row's verdict blindly."""
        fx = self._new_fixture(mode="no_generated_file")
        cx = fx["cx"]
        st = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"content\n", destination_path="docs/s.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        cx.execute(
            "INSERT INTO publication_review(stage_id,reviewer_identity,reviewer_run_id,"
            "reviewed_digest,verdict,notes,reviewed_at) VALUES(?,?,?,?,?,?,?)",
            (st["stage_id"], "eng_alice", "run-1", st["content_digest"], "approve",
             "direct-insert bypassing review()", T0))
        cx.commit()
        with self.assertRaises(NSP.PublicationError) as ctx:
            NSP.patch_stage(cx, stage_id=st["stage_id"], tenant_id=fx["tenant_id"],
                             repository_id=fx["repository_id"])
        self.assertEqual(ctx.exception.reason_code, "self_review")

    def test_strict_mode_patch_with_a_genuine_approving_review_succeeds(self):
        fx = self._new_fixture(mode="no_generated_file")
        cx = fx["cx"]
        st = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"reviewed content\n", destination_path="docs/s.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        NSP.review(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                   stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
                   reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
                   verdict="approve", t=T0)
        patch = NSP.patch_stage(cx, stage_id=st["stage_id"], tenant_id=fx["tenant_id"],
                                 repository_id=fx["repository_id"])
        self.assertTrue(patch["ok"])
        self.assertIn("+reviewed content", patch["patch"])

    def test_default_mode_patch_is_unchanged(self):
        """zero_nightshift_file mode: patch remains the never-writes
        preview it always was -- no review is required to preview a diff.
        This is the sibling-case proof that the AC-3 gate is scoped to
        no_generated_file ONLY."""
        fx = self._new_fixture(mode="zero_nightshift_file")
        st = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"default mode content\n", destination_path="docs/s.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        patch = NSP.patch_stage(fx["cx"], stage_id=st["stage_id"], tenant_id=fx["tenant_id"],
                                 repository_id=fx["repository_id"])
        self.assertTrue(patch["ok"])
        self.assertIn("+default mode content", patch["patch"])


# ---------------------------------------------------------------------------
# Condition 2, verbatim: "Zero-Nightshift-file mode is the default for new
# customer installations."
#
# Unmet as written. `bin/nightshift_publication.py`'s CLI declares `--mode`
# with `required=True` (no fallback value at the argparse layer), and
# `policy_set()` takes `mode` as a required keyword with no default either
# -- there is no code path that puts a fresh installation on
# `zero_nightshift_file` without an explicit, separate `policy-set` call.
# `get_policy()` returning None (no row at all) is not the SAME thing as
# "zero_nightshift_file is the default": it means several call sites
# (`stage`, `patch_stage`) fall back to treating an absent policy as
# effectively `zero_nightshift_file`-shaped behavior by omission, while
# `apply()` refuses outright (`policy_not_initialised`) rather than
# defaulting to anything. Two different behaviors for "no policy set" is
# not a stated default; it is two different silences. Pinned here rather
# than silently claimed met.
# ---------------------------------------------------------------------------

class Condition02ZeroFileModeIsNotADefault(unittest.TestCase):

    def test_policy_set_requires_an_explicit_mode_so_no_default_exists(self):
        """The REAL CLI layer -- `nightshift_publication.build_parser()`
        itself, never a hand-rebuilt copy of it (a copy stays green even
        after the real parser grows a default: that is precisely the
        vacuous-test shape a mutation check on `bin/nightshift_publication.py`
        caught here -- flipping the real `--mode` to `default=...` left an
        inline-rebuilt parser oblivious). `policy-set` has no default for
        `--mode` -- omitting it is an argparse usage error (SystemExit(2)),
        never a silent fall-through to `zero_nightshift_file`. If a future
        change adds a real default, THIS assertion is what breaks, and the
        index entry above must move from unmet_pinned to met at that point
        -- the deviation cannot rot into a false claim in either
        direction."""
        import io
        import contextlib
        parser = NSP.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args([
                    "policy-set",
                    "--state-root", "/tmp/does-not-need-to-exist",
                    "--tenant-id", "1", "--repository-id", "1",
                ])
        self.assertEqual(ctx.exception.code, 2)

    def test_the_function_layer_also_has_no_default_mode_keyword(self):
        """`policy_set()` itself: `mode` carries no default value in the
        function signature, so a caller cannot omit it either -- confirmed
        against the REAL function object, not a description of it."""
        import inspect
        sig = inspect.signature(NSP.policy_set)
        self.assertIs(sig.parameters["mode"].default, inspect.Parameter.empty,
                       "policy_set's mode parameter must have NO default for "
                       "this AC to stay unmet -- if this ever gets a default, "
                       "the acceptance index entry must be revisited, not just "
                       "this test")


# ---------------------------------------------------------------------------
# Condition 14, verbatim: "The producing worker cannot approve its own
# publication. Internal prompts, transcripts, credentials, hidden
# reasoning, and unreviewed operational memory are denied by the normal
# publication path."
#
# Two clauses. The self-review half has always been tested
# (RefusalSelfReview, in test_publication_gateway.py). The SECOND half --
# that `stage()` refuses the denied classifications this AC names by name
# -- had a real mechanism (`nightshift_publication.DENIED_CLASSIFICATIONS`
# / the `classification_not_publishable` refusal in `stage()`) that NOTHING
# in the suite exercised: a search for every literal denied-classification
# string across `tests/` before this class was added returned zero hits.
# That was the genuine, previously-silent gap this condition's entry
# closes.
# ---------------------------------------------------------------------------

class Condition14CredentialAndTranscriptClassificationsAreDenied(PublicationGatewayTestCase):

    def test_every_denied_classification_is_refused_at_stage(self):
        fx = self._new_fixture()
        self.assertTrue(NSP.DENIED_CLASSIFICATIONS, "the denylist itself must be non-empty")
        for classification in sorted(NSP.DENIED_CLASSIFICATIONS):
            with self.subTest(classification=classification):
                with self.assertRaises(NSP.PublicationError) as ctx:
                    NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                              content_bytes=b"internal content\n",
                              destination_path=f"docs/{classification}.md",
                              classification=classification, producer_identity="eng_alice",
                              producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                              producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
                self.assertEqual(ctx.exception.reason_code, "classification_not_publishable")

    def test_an_ordinary_classification_is_unaffected(self):
        """The blocklist is open-world (#400's own wording: "any OTHER
        classification ... is publishable subject to the rest of the
        pipeline") -- this pins that the check does not accidentally
        swallow a legitimate classification too."""
        fx = self._new_fixture()
        st = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"a real doc\n", destination_path="docs/x.md",
                        classification="adr", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        row = NSP.get_stage(fx["cx"], st["stage_id"])
        self.assertEqual(row["classification"], "adr")


# ---------------------------------------------------------------------------
# Condition 23, verbatim: "Cleanup of legacy `.omni`, refs, clones, or
# worktrees occurs only after the new authority, export, rollback, and
# exact-footprint proof succeed."
#
# cleanup_gate() itself, and its refuse-by-default behavior, is covered
# with full mutation evidence in tests/test_authority_matrix.py
# (CleanupGateRefusesByDefault). This test covers the OTHER half named in
# the AC's own "how_proven": a repo-wide guard that a deletion call
# targeting legacy material is never introduced without the enclosing
# function also naming `cleanup_gate`.
#
# DELETE-CALL SHAPES COVERED. Not just the Python-native `shutil.rmtree` /
# `os.remove` / `os.unlink` triad -- this codebase's real write-path for
# deleting refs/clones/worktrees is `_git_rw`, a thin `subprocess.run(["git",
# ...])` wrapper (`bin/nightshift-ctl`'s ADR-0061 `reap --delete` path), so a
# detector that only pattern-matches Python-native calls is blind to the
# actual deletion mechanism this repo uses. Also covered: a raw shell `rm
# -rf`. A previous version of this detector matched only the Python-native
# triad and reported zero offenders while never having looked at the
# git-subprocess call shape at all -- an unproven guard on a destructive
# path. See test_the_detector_catches_a_synthetic_git_subprocess_deletion*
# below for the mutation evidence that this blind spot is now closed.
#
# WHAT COUNTS AS "LEGACY". The AC's own wording is "legacy `.omni`, refs,
# clones, or worktrees" -- "legacy" qualifies all four nouns. A bare,
# unqualified deletion of an ORDINARY worktree/clone/ref is not this AC's
# concern: ADR-0061's `reap_delete_worktrees` (bin/nightshift-ctl) already
# deletes worktrees via `_git_rw(repo, "worktree", "remove", ...)`, but it is
# routine per-task hygiene -- recycling one finished task's already-clean
# worktree, gated by its OWN five-condition eligibility check
# (`_worktree_delete_eligible`, re-verified immediately before the delete) --
# and has nothing to do with the omni-to-authority-matrix migration
# cleanup_gate exists for. Treating every deletion that merely mentions the
# word "worktree" as AC-23 material would be a false positive against that
# unrelated, already-safety-gated, pre-#400 feature (ADR-0061/#433), and
# fixing the git-subprocess blind spot above would otherwise turn that false
# positive into a spurious failure on working, correct code. So: `.omni` on
# its own is unambiguous (nothing else in this repo is named that), but
# "worktree"/"clone"/"refs/" only count as a legacy target when the word
# "legacy" also appears nearby -- see
# test_the_detector_does_not_flag_an_ordinary_non_legacy_worktree_deletion
# below, which pins this exact boundary using the real ADR-0061 call shape.
# ---------------------------------------------------------------------------

_CALL_START_RE = re.compile(
    r"\b(?:shutil\.rmtree|os\.remove|os\.unlink|_git_rw|subprocess\.\w+|os\.system)\s*\(")

_DELETE_SHAPE_RE = re.compile(
    r"shutil\.rmtree\s*\("
    r"|os\.remove\s*\("
    r"|os\.unlink\s*\("
    r"|_git_rw\s*\([^)]*?[\"']worktree[\"']\s*,\s*[\"']remove[\"']"
    r"|_git_rw\s*\([^)]*?[\"']update-ref[\"']\s*,\s*[\"']-d[\"']"
    r"|_git_rw\s*\([^)]*?[\"']branch[\"']\s*,\s*[\"']-D[\"']"
    r"|subprocess\.\w+\s*\([^)]*?[\"']rm[\"']\s*,\s*[\"']-rf[\"']"
    r"|os\.system\s*\([^)]*?rm\s+-rf"
)

_OMNI_RE = re.compile(r"\.omni\b", re.IGNORECASE)
_LEGACY_WORD_RE = re.compile(r"\blegacy\b", re.IGNORECASE)
_LEGACY_NOUN_RE = re.compile(r"\bworktree\b|\bclone\b|\brefs/", re.IGNORECASE)
_DEF_RE = re.compile(r"^\s*def\s+\w+")

_LOOKBACK_WINDOW = 4   # lines before the delete call also checked for a legacy reference
_LOOKAHEAD_WINDOW = 3  # lines after a call's opening line, for multi-line call arguments
                       # (the real `_git_rw(repo, "worktree", "remove", "--force",\n  entry["path"])`
                       # shape splits its argument list across two lines)


def _bin_files():
    for path in sorted(BIN.iterdir()):
        if path.is_file():
            try:
                yield path, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


def _is_legacy_target(text):
    return bool(_OMNI_RE.search(text)) or (
        bool(_LEGACY_WORD_RE.search(text)) and bool(_LEGACY_NOUN_RE.search(text)))


def _ungated_legacy_deletions(files):
    """(filename, line_number) for every deletion call -- Python-native
    (`shutil.rmtree`/`os.remove`/`os.unlink`), git-subprocess (`_git_rw(...,
    "worktree", "remove", ...)` / `..., "update-ref", "-d", ...` / `...,
    "branch", "-D", ...`), or a raw `rm -rf` shellout -- that ALSO targets
    material this AC calls LEGACY (`.omni`, or `legacy` co-occurring with
    worktree/clone/refs -- see the module-level comment above) on the same
    line or in a window around it (the common `path = os.path.join(...)`
    lookback, or a multi-line call's own argument list, lookahead), where
    the enclosing function (scanning backward to the nearest `def`) never
    mentions `cleanup_gate` anywhere in its body."""
    offenders = []
    for path, text in files:
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not _CALL_START_RE.search(line):
                continue
            call_text = "\n".join(lines[idx:idx + 1 + _LOOKAHEAD_WINDOW])
            if not _DELETE_SHAPE_RE.search(call_text):
                continue
            target_text = "\n".join(
                lines[max(0, idx - _LOOKBACK_WINDOW):idx + 1 + _LOOKAHEAD_WINDOW])
            if not _is_legacy_target(target_text):
                continue
            # Find the enclosing function's start.
            start = idx
            while start > 0 and not _DEF_RE.match(lines[start]):
                start -= 1
            # Find the enclosing function's end (next top-level def/EOF).
            end = idx
            while end < len(lines) - 1:
                end += 1
                if _DEF_RE.match(lines[end]) and not lines[end].startswith((" ", "\t")):
                    break
            body = "\n".join(lines[start:end])
            if "cleanup_gate" not in body:
                offenders.append((path.name, idx + 1, line.strip()))
    return offenders


class Condition23CleanupIsGated(unittest.TestCase):

    def test_no_module_deletes_legacy_material_ungated(self):
        offenders = _ungated_legacy_deletions(_bin_files())
        self.assertEqual(offenders, [],
                          f"deletion of legacy .omni/worktree/clone/refs material "
                          f"(Python-native, git-subprocess, or `rm -rf`) with no "
                          f"cleanup_gate reference in the enclosing function: {offenders}")

    def test_the_detector_catches_a_synthetic_ungated_deletion(self):
        """Mutation-evidence proof for the detector itself, per the AC's
        own defect shape: `shutil.rmtree(os.path.join(repo, '.omni'))`
        added to a function that never mentions cleanup_gate."""
        synthetic = (
            "def cmd_something(args):\n"
            "    legacy = os.path.join(repo, '.omni')\n"
            "    shutil.rmtree(legacy)\n"
            "def cmd_other(args):\n"
            "    pass\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0][0], "fake_module.py")
        self.assertEqual(offenders[0][1], 3)

    def test_the_detector_accepts_a_synthetic_gated_deletion(self):
        synthetic = (
            "def cmd_something(args):\n"
            "    if not matrix.cleanup_gate(new_authority_bound=True, export_verified=True,\n"
            "                               rollback_rehearsed=True, footprint_clean=True):\n"
            "        return\n"
            "    legacy = os.path.join(repo, '.omni')\n"
            "    shutil.rmtree(legacy)\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(offenders, [])

    def test_the_detector_catches_a_synthetic_git_subprocess_worktree_removal(self):
        """Mutation-evidence for the blind spot the review found: a FUTURE
        legacy-cleanup path written with the real `_git_rw` git-subprocess
        wrapper (mirroring bin/nightshift-ctl's own two-line call shape,
        argument list split across lines) rather than `shutil.rmtree`, with
        no cleanup_gate reference, must still be caught."""
        synthetic = (
            "def cmd_migrate_cleanup(args):\n"
            "    # delete the legacy .omni-era worktree, now that the new\n"
            "    # authority owns everything it used to\n"
            "    rc, sout, serr = _git_rw(repo, \"worktree\", \"remove\", \"--force\",\n"
            "                             legacy_path)\n"
            "def cmd_other(args):\n"
            "    pass\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0][0], "fake_module.py")
        self.assertEqual(offenders[0][1], 4)

    def test_the_detector_catches_a_synthetic_git_subprocess_ref_deletion(self):
        """Same blind spot, `update-ref -d` shape."""
        synthetic = (
            "def cmd_migrate_cleanup(args):\n"
            "    # legacy refs left over from the pre-authority-matrix era\n"
            "    rc, sout, serr = _git_rw(repo, \"update-ref\", \"-d\",\n"
            "                             \"refs/legacy/nightshift/\" + old_ref)\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0][1], 3)

    def test_the_detector_catches_a_synthetic_rm_rf_shellout(self):
        """Same blind spot, a raw `rm -rf` subprocess shellout instead of
        any Python-native or git-native call."""
        synthetic = (
            "def cmd_migrate_cleanup(args):\n"
            "    # wipe the legacy .omni directory tree entirely\n"
            "    subprocess.run([\"rm\", \"-rf\", os.path.join(repo, \".omni\")])\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0][1], 3)

    def test_the_detector_accepts_a_synthetic_gated_git_subprocess_deletion(self):
        """Sibling of test_the_detector_accepts_a_synthetic_gated_deletion,
        for the git-subprocess shape: cleanup_gate present in the enclosing
        function clears it."""
        synthetic = (
            "def cmd_migrate_cleanup(args):\n"
            "    if not matrix.cleanup_gate(new_authority_bound=True, export_verified=True,\n"
            "                               rollback_rehearsed=True, footprint_clean=True):\n"
            "        return\n"
            "    # legacy .omni worktree\n"
            "    rc, sout, serr = _git_rw(repo, \"worktree\", \"remove\", \"--force\",\n"
            "                             legacy_path)\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(offenders, [])

    def test_the_detector_does_not_flag_an_ordinary_non_legacy_worktree_deletion(self):
        """Pins the AC's own qualifier -- "legacy `.omni`, refs, clones, or
        worktrees" -- against a false positive: this is bin/nightshift-ctl's
        REAL ADR-0061 `reap_delete_worktrees` call shape verbatim (a clean,
        finished task's worktree, recycled by routine housekeeping that
        predates #400 and is unrelated to the omni-to-authority-matrix
        migration), with no mention of "legacy" or ".omni" anywhere near it.
        It must NOT be reported: cleanup_gate governs the migration
        decommission, not this repo's ordinary per-task worktree reaping,
        which already has its own five-condition eligibility gate."""
        synthetic = (
            "def reap_delete_worktrees(cx, repo, wt_report, dry_run):\n"
            "    for entry in wt_report.get(\"worktrees\", []):\n"
            "        eligible, reason = _worktree_delete_eligible(cx, entry)\n"
            "        if not eligible:\n"
            "            continue\n"
            "        rc, sout, serr = _git_rw(repo, \"worktree\", \"remove\", \"--force\",\n"
            "                                 entry[\"path\"])\n"
        )
        offenders = _ungated_legacy_deletions([(Path("fake_module.py"), synthetic)])
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------------------
# Condition 15, verbatim: "Retention and deletion walk `derived_from`
# lineage across source bodies, chunks, summaries, embeddings, caches, and
# exports. Completion is auditable and idempotent."
#
# PR #702's own disclosed major (item 2): `purge_lineage_node` emitted no
# receipt, had no `closed=False` boundary field, and never deleted the CAS
# body. bin/nightshift_provenance.py now writes one immutable
# `purge_receipt` row per (tenant, repository, root node) -- see that
# function's docstring for the exact idempotence and `closed` contract.
# Mockless: a real provenance.db (and, for the composed-path test, a real
# tenant_keys.db) on disk.
# ---------------------------------------------------------------------------

class Condition15DeletionIsAuditableAndIdempotent(unittest.TestCase):

    TENANT = 5001
    REPOSITORY = 42

    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="ns-ac15-")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.cx = prov.connect(self.state_root, self.TENANT, self.REPOSITORY)
        self.addCleanup(self.cx.close)

    def _seed(self, *, with_digest=True):
        """One root node with one content-carrying child, so a purge of
        the root has a real two-node victim set to receipt. When
        `with_digest` the root's content_digest names a real object
        already staged in the CAS, so the CAS-deletion half is exercised
        too."""
        digest = None
        if with_digest:
            body = b"content-carrying body"
            digest = hashlib.sha256(body).hexdigest()
            prov.stage_object(self.state_root, self.TENANT, self.REPOSITORY, digest, body)
        prov.record_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root",
                                  "worker_output", "artifact", "a-root",
                                  content_digest=digest, t=1.0)
        prov.record_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "child",
                                  "reviewed_patch", "artifact", "a-child",
                                  content_digest=digest, t=1.0)
        prov.record_derived_from(self.cx, self.TENANT, self.REPOSITORY, "root", "child",
                                  "derives", 1, t=1.0)
        return digest

    def test_a_purge_writes_one_durable_receipt_naming_every_deleted_node(self):
        self._seed()
        victims = prov.purge_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root",
                                           reason="test-delete", t=10.0)
        self.assertEqual(victims, {"root", "child"})
        receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        self.assertIsNotNone(receipt, "a purge that found a real owner must leave a receipt")
        self.assertEqual(set(json.loads(receipt["purged_nodes_json"])), {"root", "child"})
        self.assertEqual(receipt["purged_node_count"], 2)
        self.assertEqual(receipt["reason"], "test-delete")
        self.assertEqual(receipt["purged_at"], 10.0)

    def test_a_purge_receipt_cannot_be_updated_or_deleted(self):
        """Database-enforced, not just application discipline -- same shape
        as source_snapshot_immutable's own test, plus a DELETE half no
        other table in this schema needs: purge_receipt is the record OF a
        deletion, so deleting IT would defeat the audit."""
        self._seed()
        prov.purge_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root", t=10.0)
        with self.assertRaises(Exception) as ctx:
            self.cx.execute("UPDATE purge_receipt SET closed=1 WHERE root_node_id='root'")
        self.assertIn("immutable", str(ctx.exception))
        with self.assertRaises(Exception) as ctx2:
            self.cx.execute("DELETE FROM purge_receipt WHERE root_node_id='root'")
        self.assertIn("immutable", str(ctx2.exception))

    def test_a_purge_with_a_state_root_deletes_the_cas_bodies_it_receipts(self):
        digest = self._seed()
        path = prov.stage_object_path(self.state_root, self.TENANT, self.REPOSITORY, digest)
        self.assertTrue(os.path.exists(path))
        prov.purge_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root",
                                 state_root=self.state_root, t=10.0)
        self.assertFalse(os.path.exists(path), "the CAS body must actually be gone")
        receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        self.assertEqual(receipt["cas_scope"], "removed")
        self.assertEqual(receipt["cas_objects_removed"], 1)

    def test_without_a_state_root_the_receipt_says_not_attempted_rather_than_zero_removed(self):
        digest = self._seed()
        path = prov.stage_object_path(self.state_root, self.TENANT, self.REPOSITORY, digest)
        prov.purge_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root", t=10.0)
        self.assertTrue(os.path.exists(path), "no state_root -- the body must be untouched")
        receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        self.assertEqual(receipt["cas_scope"], "not_attempted_no_state_root")
        self.assertEqual(receipt["cas_objects_removed"], 0)
        self.assertEqual(receipt["closed"], 0)

    def test_a_second_purge_changes_nothing_and_leaves_exactly_one_receipt(self):
        self._seed()
        first = prov.purge_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root",
                                         state_root=self.state_root, reason="first", t=10.0)
        first_receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        second = prov.purge_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "root",
                                          state_root=self.state_root, reason="second", t=99.0)
        second_receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        self.assertEqual(second, first, "a replay must hand back the ORIGINAL victim set")
        self.assertEqual(second_receipt, first_receipt,
                          "the receipt must be byte-identical after a replay")
        count = self.cx.execute("SELECT COUNT(*) FROM purge_receipt").fetchone()[0]
        self.assertEqual(count, 1)

    def test_closed_is_false_when_a_named_class_was_not_walked(self):
        self._seed()
        prov.purge_lineage_node(
            self.cx, self.TENANT, self.REPOSITORY, "root", state_root=self.state_root,
            covers=("source_bodies", "chunks", "summaries", "embeddings", "caches"),
            t=10.0)  # "exports" left out on purpose
        receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        self.assertEqual(receipt["closed"], 0)
        self.assertEqual(json.loads(receipt["uncovered_classes_json"]), ["exports"])

    def test_full_coverage_and_verified_cas_absence_receipts_closed_true(self):
        self._seed()
        prov.purge_lineage_node(
            self.cx, self.TENANT, self.REPOSITORY, "root", state_root=self.state_root,
            covers=prov.AC15_LINEAGE_CLASSES, t=10.0)
        receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "root")
        self.assertEqual(receipt["closed"], 1)
        self.assertEqual(json.loads(receipt["uncovered_classes_json"]), [])

    def test_the_real_composed_deletion_path_receipts_closed_false(self):
        """Drives the REAL production call site
        (bin/nightshift_tenant_keys.py's `purge_lineage_with_keys`) rather
        than this test's own direct call -- it does not pass `state_root`
        or `covers` through to `purge_lineage_node`, so its receipt
        honestly says `cas_scope='not_attempted_no_state_root'` and
        `closed=0`, even though `purge_lineage_with_keys`'s OWN separate
        loop removes the CAS bytes a moment later. That undercount is a
        real, stated gap (see this stream's PR body), owed to a one-line
        change in a file this stream does not own -- not papered over
        here."""
        key_db = os.path.join(self.state_root, "keys", "tenant_keys.db")
        kcx = keys.connect(key_db)
        self.addCleanup(kcx.close)
        keys.create_tenant_kek(kcx, self.TENANT, wrapping_secret=b"s3cr3t", t=1.0)
        body = b"composed-path body"
        digest = hashlib.sha256(body).hexdigest()
        wrapped = keys.wrap_object_key(kcx, self.TENANT, self.REPOSITORY, digest,
                                        wrapping_secret=b"s3cr3t", t=1.0)
        sealed = keys.envelope_to_bytes(keys.seal(
            wrapped["dek"], body, keys._dek_aad(self.TENANT, self.REPOSITORY, digest)))
        prov.stage_object(self.state_root, self.TENANT, self.REPOSITORY, digest, body,
                           key_version=wrapped["key_version"], sealed=sealed)
        prov.record_lineage_node(self.cx, self.TENANT, self.REPOSITORY, "node-1",
                                  "worker_output", "artifact", "a1",
                                  content_digest=digest, t=1.0)

        keys.purge_lineage_with_keys(prov, self.cx, kcx, self.state_root,
                                      self.TENANT, self.REPOSITORY, "node-1",
                                      reason="tenant-delete", t=2.0)
        receipt = prov.lineage_purge_receipt(self.cx, self.TENANT, self.REPOSITORY, "node-1")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["cas_scope"], "not_attempted_no_state_root")
        self.assertEqual(receipt["closed"], 0)
        self.assertFalse(
            prov.object_exists(self.state_root, self.TENANT, self.REPOSITORY, digest),
            "the composed call site's OWN loop removes the body regardless of the receipt")


# ===========================================================================
# INDEX-CANNOT-LIE's own tests. Two classes: the LIVE checks (this class'
# name is one of `_GUARD_CLASSES` above -- see `test_an_entry_naming_the_
# guard_itself_is_reported_as_self_reference` for why that matters), and
# the synthetic mutation-evidence detector tests.
# ===========================================================================

class AcceptanceIndexCannotLie(unittest.TestCase):

    def test_the_index_covers_all_twenty_three_conditions_exactly_once(self):
        expected = {f"AC{i}" for i in range(1, 24)}
        ids = [e.ac_id for e in ACCEPTANCE_INDEX]
        self.assertEqual(len(ids), 23, f"expected exactly 23 entries, got {len(ids)}")
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(set(ids)), "a duplicated ac_id")

    def test_the_verbatim_statements_are_NOT_machine_checked_against_the_epic(self):
        """Scope honesty, same shape as nightshift_authority_matrix's own
        `test_path_and_glob_projections_are_NOT_claimed_to_be_verified`:
        every entry's `statement` is transcribed by hand from the issue
        body and is NOT diffed against a live `gh issue view 400` here --
        doing that would mean a test reads GitHub state, which #698
        forbids. This test pins that every entry at least CARRIES a
        non-empty statement and declares its provenance honestly, so a
        future entry cannot go silent on where its wording came from."""
        for e in ACCEPTANCE_INDEX:
            with self.subTest(ac_id=e.ac_id):
                self.assertTrue(e.statement.strip())
                self.assertEqual(e.statement_source, "issue #400 body, transcribed")

    def test_no_claimed_test_is_on_the_host_uid_skip_manifest(self):
        manifest_ids = _manifest_test_ids()
        self.assertTrue(manifest_ids, "the real manifest must be non-empty for this to mean anything")
        offenders = [(e.ac_id, t) for e in ACCEPTANCE_INDEX for t in e.proven_by
                     if t in manifest_ids]
        self.assertEqual(offenders, [])

    def test_the_auditor_refuses_an_entry_naming_the_guard_itself(self):
        offenders = [(e.ac_id, t) for e in ACCEPTANCE_INDEX for t in e.proven_by
                     if t.split(".")[1] in _GUARD_CLASSES]
        self.assertEqual(offenders, [])

    def test_the_real_acceptance_index_is_honest(self):
        """THE live check: every met/unmet_pinned entry's tests resolve
        and run as exactly one green test each, every not_proven entry
        carries a reason and no test, every refused entry names an ADR
        file that really exists, and the 23 ids are exactly AC1..AC23.
        This is INDEX-CANNOT-LIE's entire reason to exist -- everything
        else in this class documents one slice of it; this proves all of
        it, together, against the real repository, every time the suite
        runs."""
        report = audit_acceptance_index(ACCEPTANCE_INDEX)
        self.assertTrue(report.ok, json.dumps(report.as_dict(), indent=2, sort_keys=True))


class IndexAuditorDetectsRigging(unittest.TestCase):
    """Mutation-evidence for `audit_acceptance_index()` itself: each test
    feeds it a deliberately rigged entry (or pair of entries) and asserts
    the specific bucket names the specific lie. None of these touch the
    REAL `ACCEPTANCE_INDEX` or the real repository state (`adr_dir`/
    `manifest_ids`/`expected_ids` are all overridden), so a rigged entry
    here can never leak into the live check above.
    """

    def _register_probe_module(self, name, testcase_cls):
        mod = types.ModuleType(name)
        setattr(mod, testcase_cls.__name__, testcase_cls)
        sys.modules[name] = mod
        self.addCleanup(sys.modules.pop, name, None)

    def _audit(self, index, **kw):
        kw.setdefault("adr_dir", TESTS_DIR)  # never touch docs/decisions/ from here
        kw.setdefault("manifest_ids", frozenset())
        kw.setdefault("expected_ids", {e.ac_id for e in index})
        return audit_acceptance_index(index, **kw)

    # -- coverage: lying by omission or duplication -------------------

    def test_missing_and_duplicated_ac_ids_are_reported_as_coverage_problems(self):
        index = (
            ACEntry("AC1", "s", STATUS_NOT_PROVEN, reason="synthetic"),
            ACEntry("AC1", "s", STATUS_NOT_PROVEN, reason="synthetic"),
        )
        report = audit_acceptance_index(index, adr_dir=TESTS_DIR, manifest_ids=frozenset(),
                                         expected_ids={"AC1", "AC2"})
        self.assertIn("missing:AC2", report.coverage)
        self.assertIn("duplicate:AC1", report.coverage)
        self.assertFalse(report.ok)

    # -- a claim naming a test that does not exist ---------------------

    def test_an_entry_naming_a_nonexistent_method_on_a_real_class_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=(
            "test_authority_matrix.MatrixCoversEveryTable.test_this_method_does_not_exist",)),)
        report = self._audit(index)
        self.assertTrue(any("test_this_method_does_not_exist" in x for x in report.missing_test))
        self.assertFalse(report.ok)

    def test_an_entry_naming_a_nonexistent_class_in_a_real_module_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=(
            "test_authority_matrix.ThisClassDoesNotExist.test_whatever",)),)
        report = self._audit(index)
        self.assertTrue(any("ThisClassDoesNotExist" in x for x in report.missing_test))

    def test_an_entry_naming_a_nonexistent_module_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=(
            "totally_fake_module_xyz.SomeClass.some_method",)),)
        report = self._audit(index)
        self.assertTrue(any("totally_fake_module_xyz" in x for x in report.missing_test))

    def test_a_class_level_claim_without_a_method_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=(
            "test_authority_matrix.MatrixCoversEveryTable",)),)
        report = self._audit(index)
        self.assertIn("AC1: test_authority_matrix.MatrixCoversEveryTable",
                      report.not_method_level)

    # -- a claim naming a test that resolves but never runs green -------

    def test_an_entry_whose_named_test_is_skipped_is_reported(self):
        class _SkippedProbe(unittest.TestCase):
            @unittest.skip("synthetic probe -- always skips")
            def test_it(self):
                self.fail("must never actually run")

        self._register_probe_module("ac400_probe_skip", _SkippedProbe)
        index = (ACEntry("AC1", "s", STATUS_MET,
                          proven_by=("ac400_probe_skip._SkippedProbe.test_it",)),)
        report = self._audit(index)
        self.assertTrue(any("ac400_probe_skip._SkippedProbe.test_it" in x
                             for x in report.unexecuted_claim))
        self.assertFalse(report.ok)

    def test_an_entry_whose_named_test_fails_is_reported(self):
        class _FailingProbe(unittest.TestCase):
            def test_it(self):
                self.fail("synthetic probe -- always fails")

        self._register_probe_module("ac400_probe_fail", _FailingProbe)
        index = (ACEntry("AC1", "s", STATUS_MET,
                          proven_by=("ac400_probe_fail._FailingProbe.test_it",)),)
        report = self._audit(index)
        self.assertTrue(any("ac400_probe_fail._FailingProbe.test_it" in x
                             for x in report.unexecuted_claim))

    def test_an_entry_whose_named_test_errors_is_reported(self):
        class _ErroringProbe(unittest.TestCase):
            def test_it(self):
                raise RuntimeError("synthetic probe -- always errors")

        self._register_probe_module("ac400_probe_error", _ErroringProbe)
        index = (ACEntry("AC1", "s", STATUS_MET,
                          proven_by=("ac400_probe_error._ErroringProbe.test_it",)),)
        report = self._audit(index)
        self.assertTrue(any("ac400_probe_error._ErroringProbe.test_it" in x
                             for x in report.unexecuted_claim))

    def test_an_entry_whose_named_test_genuinely_passes_is_not_reported(self):
        """The positive control for the whole skipped/failed/errored
        trio above: a probe that actually passes must NOT land in
        unexecuted_claim -- otherwise this guard would be indistinguishable
        from one that always reports a problem regardless of input."""
        class _PassingProbe(unittest.TestCase):
            def test_it(self):
                self.assertEqual(1, 1)

        self._register_probe_module("ac400_probe_pass", _PassingProbe)
        index = (ACEntry("AC1", "s", STATUS_MET,
                          proven_by=("ac400_probe_pass._PassingProbe.test_it",)),)
        report = self._audit(index)
        self.assertEqual(report.missing_test, [])
        self.assertEqual(report.unexecuted_claim, [])
        self.assertEqual(report.not_method_level, [])

    def test_a_pinned_tests_topical_relevance_to_its_ac_is_NOT_machine_checked(self):
        """Scope honesty, same shape as
        `test_the_verbatim_statements_are_NOT_machine_checked_against_the_epic`
        and nightshift_authority_matrix's own
        `test_path_and_glob_projections_are_NOT_claimed_to_be_verified`: this
        guard proves a `proven_by` test resolves, runs, and passes green --
        it has no way to check that the test's assertions actually bear on
        the AC's own English statement. `test_the_function_layer_also_has_
        no_default_mode_keyword` is a real, green test about argparse
        defaults (AC2's territory); pinned here to an AC about submodule
        commits and Git LFS objects, this guard accepts it exactly as
        happily as the real, on-topic test it stands in for -- a prior
        verifier round rigged this exact swap against the REAL index and it
        went undetected. This is a genuine, currently-open residual limit
        of INDEX-CANNOT-LIE, not a hypothetical one: recorded here,
        executably, so a green suite is never mistaken for 'every pinned
        test proves what its AC says'. Judging whether a test's CONTENT
        supports an AC's CLAIM is the reviewing human's job; this guard
        only ever checks that the test exists and runs green."""
        index = (ACEntry(
            "AC1",
            "Provenance closure covers required submodule commits and Git "
            "LFS objects.",
            STATUS_MET,
            proven_by=(
                "test_400_acceptance_conditions.Condition02ZeroFileModeIsNotADefault."
                "test_the_function_layer_also_has_no_default_mode_keyword",
            )),)
        report = self._audit(index)
        self.assertTrue(report.ok, report.as_dict())

    # -- a claim with no test at all, or a withdrawal that still has one -

    def test_a_met_entry_with_no_test_at_all_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=()),)
        report = self._audit(index)
        self.assertIn("AC1", report.empty_claim)

    def test_an_unmet_pinned_entry_with_no_test_at_all_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_UNMET_PINNED, proven_by=(), deviation="x"),)
        report = self._audit(index)
        self.assertIn("AC1", report.empty_claim)

    def test_a_not_proven_entry_that_still_names_a_test_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_NOT_PROVEN, reason="synthetic", proven_by=(
            "test_authority_matrix.CleanupGateRefusesByDefault."
            "test_all_four_true_is_the_only_passing_combination",)),)
        report = self._audit(index)
        self.assertTrue(any(x.startswith("AC1: status=not_proven")
                             for x in report.overclaimed_status))

    def test_a_refused_entry_that_still_names_a_test_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_REFUSED, refused_by="ADR-0083"
                          "-cipher-selection-and-deferred-postgres-parity.md", proven_by=(
            "test_authority_matrix.CleanupGateRefusesByDefault."
            "test_all_four_true_is_the_only_passing_combination",)),)
        report = self._audit(index, adr_dir=REPO / "docs" / "decisions")
        self.assertTrue(any(x.startswith("AC1: status=refused")
                             for x in report.overclaimed_status))

    # -- withdrawals and refusals that do not justify themselves --------

    def test_a_not_proven_entry_with_no_reason_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_NOT_PROVEN, reason=None),)
        report = self._audit(index)
        self.assertIn("AC1: not_proven with no reason stated", report.missing_reason)

    def test_an_unmet_pinned_entry_with_no_deviation_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_UNMET_PINNED, deviation=None, proven_by=(
            "test_authority_matrix.CleanupGateRefusesByDefault."
            "test_all_four_true_is_the_only_passing_combination",)),)
        report = self._audit(index)
        self.assertIn("AC1: unmet_pinned with no deviation stated", report.missing_reason)

    # -- a status word that contradicts its own recorded deviation ------

    def test_a_met_entry_that_still_carries_a_deviation_is_reported(self):
        """The exact rig a prior verifier round demonstrated against the
        REAL index: flip an `unmet_pinned` entry to `met` while leaving
        its deviation text (and its own deviation-pinning tests) in
        place -- a status word claiming the AC holds, sitting next to a
        sentence saying it does not. `deviation` is unmet_pinned-only;
        this must be caught regardless of whether `proven_by` still
        resolves and runs green."""
        index = (ACEntry("AC1", "s", STATUS_MET, deviation="known gap, not fixed",
                          proven_by=(
                              "test_authority_matrix.CleanupGateRefusesByDefault."
                              "test_all_four_true_is_the_only_passing_combination",)),)
        report = self._audit(index)
        self.assertTrue(any(x.startswith("AC1: status=met carries a deviation")
                             for x in report.overclaimed_status))
        self.assertFalse(report.ok)

    def test_a_not_proven_entry_that_carries_a_deviation_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_NOT_PROVEN, reason="withdrawn",
                          deviation="should not coexist with not_proven"),)
        report = self._audit(index)
        self.assertTrue(any(x.startswith("AC1: status=not_proven carries a deviation")
                             for x in report.overclaimed_status))

    def test_an_unmet_pinned_entry_with_a_deviation_is_not_reported_for_that(self):
        """Positive control for the two tests above: the one status word
        `deviation` IS allowed on must not itself trip the new check."""
        index = (ACEntry("AC1", "s", STATUS_UNMET_PINNED, deviation="real gap", proven_by=(
            "test_authority_matrix.CleanupGateRefusesByDefault."
            "test_all_four_true_is_the_only_passing_combination",)),)
        report = self._audit(index)
        self.assertEqual(report.overclaimed_status, [])
        self.assertTrue(report.ok, report.as_dict())

    def test_a_refused_entry_naming_an_adr_that_does_not_exist_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_REFUSED, refused_by="ADR-9999-does-not-exist.md"),)
        report = self._audit(index, adr_dir=REPO / "docs" / "decisions")
        self.assertIn("AC1: refused_by 'ADR-9999-does-not-exist.md' not on disk",
                      report.unbacked_refusal)

    def test_a_refused_entry_with_no_adr_named_is_reported(self):
        index = (ACEntry("AC1", "s", STATUS_REFUSED, refused_by=None),)
        report = self._audit(index)
        self.assertIn("AC1: refused with no ADR named", report.unbacked_refusal)

    def test_a_refused_entry_naming_a_real_adr_is_accepted(self):
        """Positive control for the refusal path: a real ADR file that
        really exists on disk must NOT be reported."""
        index = (ACEntry("AC1", "s", STATUS_REFUSED,
                          refused_by="ADR-0083-cipher-selection-and-deferred-postgres-parity.md"),)
        report = self._audit(index, adr_dir=REPO / "docs" / "decisions")
        self.assertEqual(report.unbacked_refusal, [])

    # -- determinism: the host-UID skip manifest, and self-reference ----

    def test_a_claimed_test_on_the_host_uid_skip_manifest_is_reported(self):
        """Uses a REAL manifest id, read from the real
        tests/host-uid-skip-manifest.json in this worktree (never a git
        ref -- #698) -- `ProjectTrustedAncestry` legitimately SKIPS under
        this repo's own uid-remap sandbox and legitimately RUNS in CI, so
        an acceptance entry naming it would be a claim that passes here
        and might silently skip somewhere else. It must never be an
        acceptable proof, regardless of whether it happens to be green in
        THIS process right now."""
        manifest_ids = _manifest_test_ids()
        real_manifest_id = (
            "test_project_bind.ProjectTrustedAncestry."
            "test_linux_literal_tmp_acceptance_evidence")
        self.assertIn(real_manifest_id, manifest_ids,
                      "the real manifest must still name this id for the test to mean anything")
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=(real_manifest_id,)),)
        report = audit_acceptance_index(index, adr_dir=TESTS_DIR, manifest_ids=manifest_ids,
                                         expected_ids={"AC1"})
        self.assertIn(f"AC1: {real_manifest_id}", report.nondeterministic_claim)

    def test_an_entry_naming_the_guard_itself_is_reported_as_self_reference_not_a_hang(self):
        index = (ACEntry("AC1", "s", STATUS_MET, proven_by=(
            "test_400_acceptance_conditions.AcceptanceIndexCannotLie."
            "test_the_real_acceptance_index_is_honest",)),)
        report = self._audit(index)  # must return, not recurse forever
        self.assertIn(
            "AC1: test_400_acceptance_conditions.AcceptanceIndexCannotLie."
            "test_the_real_acceptance_index_is_honest",
            report.self_reference)
        self.assertEqual(report.missing_test, [])
        self.assertEqual(report.unexecuted_claim, [])

    # -- positive control: a well-formed index is genuinely reported ok -

    def test_a_well_formed_synthetic_index_is_reported_ok(self):
        """If this guard reported a problem no matter what it was given,
        every test above would be meaningless. One met entry (a real,
        fast, green test), one not_proven entry (with a reason), one
        refused entry (a real ADR) -- covering the full expected_ids
        universe, nothing rigged -- must come back clean."""
        index = (
            ACEntry("AC1", "s", STATUS_MET, proven_by=(
                "test_authority_matrix.CleanupGateRefusesByDefault."
                "test_all_four_true_is_the_only_passing_combination",)),
            ACEntry("AC2", "s", STATUS_NOT_PROVEN, reason="out of scope for this control"),
            ACEntry("AC3", "s", STATUS_REFUSED,
                    refused_by="ADR-0083-cipher-selection-and-deferred-postgres-parity.md"),
        )
        report = audit_acceptance_index(
            index, adr_dir=REPO / "docs" / "decisions", manifest_ids=frozenset(),
            expected_ids={"AC1", "AC2", "AC3"})
        self.assertTrue(report.ok, report.as_dict())


if __name__ == "__main__":
    unittest.main()
