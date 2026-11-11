#!/usr/bin/env python3
"""Issue #695 -- re-runnable MUTATION EVIDENCE for PG-7 (provenance and
lineage) and PG-8 (dirty checkout via external copy).

NOT a unittest module: the filename deliberately does not start with `test_`
so unittest discovery ignores it. It is the harness that produced the audit,
kept in the tree so the evidence can be RE-RUN rather than believed (#695
acceptance criterion 1, in the form #685 established).

WHAT IT DOES, per catalogued mutation:
  1. writes the named defect into the REAL production file,
  2. runs the PG-4/6/7/8 suite,
  3. restores the file and VERIFIES the restore by sha256,
  4. reports which tests went red, and compares that to the recorded
     expectation.

    python3 tests/pg7_pg8_mutation_evidence.py --list
    python3 tests/pg7_pg8_mutation_evidence.py --run PG8-M27
    python3 tests/pg7_pg8_mutation_evidence.py --all

`expect` is the set of tests observed to fail on macOS/py3.14 when the
mutation was injected. An EMPTY `expect` means the defect went undetected;
each such entry carries a `note` saying whether that is a closed gap
(superseded by a `...b` mutation), dead code, or a mutation with no
observable consequence. Every other empty case found during the audit has
been closed by tests/test_pg7_pg8_mutation_gaps.py.

Two caveats recorded for honesty, both from the audit run itself:

  * CROSS-CONTAMINATION IS REAL, AND IT HAPPENED. Six early runs each showed
    ONE extra red test in test_publication_gateway.RefusalSelfReview, always
    a different one, walking down that class in order. The cause was a
    CONCURRENT PG-3 audit running in the same working tree: its PG3-A/B/C
    mutations disable exactly those three self-review guards
    (tests/mutation_evidence_pg3_pg4.py, docs/audit/baseline/PG-3.mutation.txt).
    Those entries are excluded from `expect`. Two consequences worth keeping:
    a concurrent injection can only ADD red tests, never hide one, so no
    "nothing went red" conclusion here is weakened by it -- but ONLY ONE
    mutation may be live in a checkout at a time, or the audits corrupt each
    other's evidence. Run this harness with the tree to yourself.
  * tests/test_zero_footprint.py's
    `test_full_bootstrap_sync_search_verify_cycle_leaves_no_trace` was
    already failing on this machine before any mutation was injected (the
    borrowed testbed's `.git` mtime). It is outside PG-7/PG-8 and is not
    part of the scope this harness runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO, "tests")

SCOPE = [
    "test_provenance",
    "test_provenance_lineage",
    "test_publication_gateway",
    "test_publication_footprint",
    "test_publication_provenance_seam",
    "test_pg7_pg8_mutation_gaps",
]

# Each entry: the exact substring to replace in the production file, and the
# exact replacement. `old` must occur EXACTLY once or the harness refuses --
# a drifted anchor is a stale catalogue entry, not something to guess at.
MUTATIONS = [
    {
        "id": "PG7-M01",
        "name": "PG7-M01 repository_fingerprint ignores root commits (all repos same identity)",
        "file": "bin/nightshift_provenance.py",
        "old": "        {\"object_format\": fmt.stdout.strip(), \"root_commits\": root_commits},",
        "new": "        {\"object_format\": fmt.stdout.strip(), \"root_commits\": []},",
        "expect": [
            "test_pg7_pg8_mutation_gaps.RepositoryFingerprintIsAnIdentity.test_two_unrelated_repositories_do_not_share_a_fingerprint"
        ]
    },
    {
        "id": "PG7-M02",
        "name": "PG7-M02 repository_fingerprint uses a write-capable git call (drops read-only guard)",
        "file": "bin/nightshift_provenance.py",
        "old": "    fmt = customer_git(customer_path, [\"rev-parse\", \"--show-object-format\"])",
        "new": "    import subprocess as _sp\n    fmt = _sp.run([\"git\",\"-C\",customer_path,\"rev-parse\",\"--show-object-format\"],capture_output=True,text=True)",
        "expect": [],
        "note": "NO OBSERVABLE DIFFERENCE. Bypasses the customer_git() choke point, but the two commands repository_fingerprint issues (rev-parse, rev-list) are read-only either way, so nothing changes. The choke point itself IS mutation-proven -- see PG8-M22/PG8-M23."
    },
    {
        "id": "PG7-M03",
        "name": "PG7-M03 stage() drops the source_snapshot identity validation",
        "file": "bin/nightshift_publication.py",
        "old": "    _validate_source_snapshot_identity(source_snapshot)\n",
        "new": "",
        "expect": [
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_a_partially_populated_identity",
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_when_identity_fields_are_missing",
            "test_publication_provenance_seam.RefusalsRunAgainstRealProvenanceRows.test_source_snapshot_incomplete_fires_on_a_provenance_recorded_row"
        ]
    },
    {
        "id": "PG7-M04",
        "name": "PG7-M04 identity validation accepts empty strings (drops the truthiness half)",
        "file": "bin/nightshift_publication.py",
        "old": "               if not isinstance(source_snapshot.get(k), str) or not source_snapshot.get(k)]",
        "new": "               if not isinstance(source_snapshot.get(k), str)]",
        "expect": [
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_a_partially_populated_identity",
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_when_identity_fields_are_missing",
            "test_publication_provenance_seam.RefusalsRunAgainstRealProvenanceRows.test_source_snapshot_incomplete_fires_on_a_provenance_recorded_row"
        ]
    },
    {
        "id": "PG7-M05",
        "name": "PG7-M05 identity validation drops object_format check",
        "file": "bin/nightshift_publication.py",
        "old": "    if source_snapshot[\"object_format\"] not in (\"sha1\", \"sha256\"):\n        _refuse(\"source_snapshot_invalid\", {\"field\": \"object_format\"})",
        "new": "    pass",
        "expect": [
            "test_pg7_pg8_mutation_gaps.ObjectFormatBackstopIsReachable.test_a_legacy_table_without_the_check_still_hits_the_python_guard"
        ]
    },
    {
        "id": "PG7-M06",
        "name": "PG7-M06 discover_closure drops submodule (gitlink 160000) rows",
        "file": "bin/nightshift_provenance.py",
        "old": "        if mode == \"160000\":\n            entries.append({\"kind\": \"submodule\", \"path\": path, \"oid\": oid})",
        "new": "        pass",
        "expect": [
            "test_provenance.ClosureDiscoverySubmoduleAndLfs.test_require_closure_complete_passes_once_both_are_recorded",
            "test_provenance.ClosureDiscoverySubmoduleAndLfs.test_require_closure_complete_refuses_when_lfs_entry_is_missing",
            "test_provenance.ClosureDiscoverySubmoduleAndLfs.test_submodule_and_lfs_entries_discovered_read_only",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_discover_closure_finds_the_real_submodule_and_lfs_entries",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_record_closure_entry_persists_real_discovered_entries",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_require_closure_complete_passes_when_every_required_path_is_covered",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_require_closure_complete_refuses_when_a_required_path_is_missing"
        ]
    },
    {
        "id": "PG7-M07",
        "name": "PG7-M07 discover_closure drops LFS pointer entries",
        "file": "bin/nightshift_provenance.py",
        "old": "        if m_oid and m_size:\n            entries.append({\n                \"kind\": \"lfs\", \"path\": path, \"oid\": oid,\n                \"digest\": \"sha256:\" + m_oid.group(1), \"size\": int(m_size.group(1))})",
        "new": "        pass",
        "expect": [
            "test_provenance.ClosureDiscoverySubmoduleAndLfs.test_require_closure_complete_passes_once_both_are_recorded",
            "test_provenance.ClosureDiscoverySubmoduleAndLfs.test_submodule_and_lfs_entries_discovered_read_only",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_discover_closure_finds_the_real_submodule_and_lfs_entries",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_record_closure_entry_persists_real_discovered_entries",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_require_closure_complete_passes_when_every_required_path_is_covered",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_require_closure_complete_refuses_when_a_required_path_is_missing"
        ]
    },
    {
        "id": "PG7-M08",
        "name": "PG7-M08 require_closure_complete never refuses",
        "file": "bin/nightshift_provenance.py",
        "old": "    missing = sorted(set(required_paths) - covered)\n    if missing:\n        _refuse(\"closure_incomplete\", {\"missing\": missing})",
        "new": "    missing = []",
        "expect": [
            "test_provenance.ClosureDiscoverySubmoduleAndLfs.test_require_closure_complete_refuses_when_lfs_entry_is_missing",
            "test_provenance.ProvenanceLineage.test_closure_entries_recorded_and_required_paths_checked",
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_require_closure_complete_refuses_when_a_required_path_is_missing"
        ]
    },
    {
        "id": "PG7-M09",
        "name": "PG7-M09 verify_closure_complete drops the digest comparison",
        "file": "bin/nightshift_provenance.py",
        "old": "    if observed != recorded:\n        _refuse(\"closure_incomplete\", {",
        "new": "    if False:\n        _refuse(\"closure_incomplete\", {",
        "expect": [
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_closure_incomplete_when_a_required_path_is_missing",
            "test_publication_provenance_seam.RefusalsRunAgainstRealProvenanceRows.test_closure_incomplete_fires_when_entries_do_not_cover_the_frozen_manifest"
        ]
    },
    {
        "id": "PG7-M10",
        "name": "PG7-M10 verify_closure_complete drops the tenant/repository WHERE on closure_entry",
        "file": "bin/nightshift_provenance.py",
        "old": "        f\"SELECT kind, path, oid FROM {schema}.closure_entry \"\n        \"WHERE snapshot_id=? AND tenant_id=? AND repository_id=?\",\n        (snapshot_id, tenant_id, repository_id)).fetchall()",
        "new": "        f\"SELECT kind, path, oid FROM {schema}.closure_entry \"\n        \"WHERE snapshot_id=?\",\n        (snapshot_id,)).fetchall()",
        "expect": [
            "test_pg7_pg8_mutation_gaps.ClosureVerificationIsTenantScoped.test_another_repositorys_closure_entry_cannot_satisfy_this_one",
            "test_pg7_pg8_mutation_gaps.ClosureVerificationIsTenantScoped.test_another_tenants_closure_entry_cannot_satisfy_this_tenants_manifest"
        ]
    },
    {
        "id": "PG7-M11",
        "name": "PG7-M11 stage() drops the closure requirement entirely",
        "file": "bin/nightshift_publication.py",
        "old": "    _require_source_snapshot_closure(cx, tenant_id, repository_id, snapshot_id)\n",
        "new": "",
        "expect": [
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_closure_incomplete_when_a_required_path_is_missing",
            "test_publication_provenance_seam.RefusalsRunAgainstRealProvenanceRows.test_closure_incomplete_fires_when_entries_do_not_cover_the_frozen_manifest"
        ]
    },
    {
        "id": "PG7-M12",
        "name": "PG7-M12 closure_manifest_digest ignores oid (a moved submodule pin still matches)",
        "file": "bin/nightshift_provenance.py",
        "old": "        [str(e[\"kind\"]), str(e[\"path\"]), str(e[\"oid\"])] for e in entries)",
        "new": "        [str(e[\"kind\"]), str(e[\"path\"])] for e in entries)",
        "expect": [
            "test_pg7_pg8_mutation_gaps.ClosureManifestDigestPinsTheOid.test_a_submodule_repointed_at_a_different_commit_is_closure_incomplete",
            "test_pg7_pg8_mutation_gaps.ClosureManifestDigestPinsTheOid.test_an_lfs_object_swapped_at_the_same_path_is_closure_incomplete"
        ]
    },
    {
        "id": "PG7-M13",
        "name": "PG7-M13 record_derived_from drops the cross-tenant endpoint check",
        "file": "bin/nightshift_provenance.py",
        "old": "    if (parent[\"tenant_id\"], parent[\"repository_id\"]) != (tenant_id, repository_id) or \\\n       (child[\"tenant_id\"], child[\"repository_id\"]) != (tenant_id, repository_id):\n        _refuse(\"lineage_cross_tenant_refused\")",
        "new": "    pass",
        "expect": [
            "test_provenance.TenancyPhysicalSeparation.test_defense_in_depth_same_file_cross_tenant_edge_refused"
        ]
    },
    {
        "id": "PG7-M14",
        "name": "PG7-M14 record_derived_from drops the cycle check",
        "file": "bin/nightshift_provenance.py",
        "old": "    if _would_create_cycle(cx, parent_node_id, child_node_id):\n        _refuse(\"lineage_cycle_refused\")",
        "new": "    pass",
        "expect": [
            "test_provenance.ProvenanceLineage.test_cycle_refused",
            "test_provenance_lineage.DerivedFromChainRoundTrips.test_cycle_is_refused_not_silently_accepted"
        ]
    },
    {
        "id": "PG7-M15",
        "name": "PG7-M15 record_derived_from drops the self-loop check",
        "file": "bin/nightshift_provenance.py",
        "old": "    if parent_node_id == child_node_id:\n        _refuse(\"lineage_self_loop_refused\")",
        "new": "    pass",
        "expect": [
            "test_provenance.ProvenanceLineage.test_self_loop_refused",
            "test_provenance_lineage.DerivedFromChainRoundTrips.test_self_loop_is_refused"
        ]
    },
    {
        "id": "PG7-M16",
        "name": "PG7-M16 record_derived_from drops the endpoint-exists check",
        "file": "bin/nightshift_provenance.py",
        "old": "    if parent is None or child is None:\n        _refuse(\"lineage_node_missing\")",
        "new": "    pass",
        "expect": [
            "test_provenance.TenancyPhysicalSeparation.test_cross_tenant_derived_from_edge_refused"
        ]
    },
    {
        "id": "PG7-M17",
        "name": "PG7-M17 _parent_via ignores edge_kind (any edge closes any hop)",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"WHERE d.child_node_id=? AND d.edge_kind=? AND p.tenant_id=? AND p.repository_id=? AND p.node_kind=?\",\n        (child_node_id, edge_kind, tenant_id, repository_id, expected_kind)).fetchone()",
        "new": "        \"WHERE d.child_node_id=? AND d.edge_kind IS NOT ? AND p.tenant_id=? AND p.repository_id=? AND p.node_kind=?\",\n        (child_node_id, \"\\x00nope\", tenant_id, repository_id, expected_kind)).fetchone()",
        "expect": [
            "test_provenance_lineage.DerivedFromChainRoundTrips.test_edge_kind_must_match_the_expected_hop"
        ]
    },
    {
        "id": "PG7-M18",
        "name": "PG7-M18 _parent_via ignores node_kind (wrong-kind parent closes the hop)",
        "file": "bin/nightshift_provenance.py",
        "old": "AND p.tenant_id=? AND p.repository_id=? AND p.node_kind=?\",\n        (child_node_id, edge_kind, tenant_id, repository_id, expected_kind)).fetchone()",
        "new": "AND p.tenant_id=? AND p.repository_id=? AND p.node_kind IS NOT ?\",\n        (child_node_id, edge_kind, tenant_id, repository_id, \"\\x00nope\")).fetchone()",
        "expect": [
            "test_pg7_pg8_mutation_gaps.LineageChainChecksBothEdgeKindAndNodeKind.test_a_reviewed_patch_in_the_worker_output_slot_does_not_close_the_hop",
            "test_pg7_pg8_mutation_gaps.LineageChainChecksBothEdgeKindAndNodeKind.test_a_worker_output_in_the_reviewed_patch_slot_does_not_close_the_hop"
        ]
    },
    {
        "id": "PG7-M19",
        "name": "PG7-M19 lineage_chain reports closed even when the source_snapshot link is missing",
        "file": "bin/nightshift_provenance.py",
        "old": "    if snapshot is None:\n        return {\"closed\": False, \"reason\": \"lineage_incomplete\", \"missing\": \"source_snapshot\"}",
        "new": "    if snapshot is None:\n        snapshot = {\"node_id\": None}",
        "expect": [
            "test_provenance_lineage.DerivedFromChainRoundTrips.test_missing_source_snapshot_link"
        ]
    },
    {
        "id": "PG7-M20",
        "name": "PG7-M20 validate_node_id/validate_snapshot_id drop the reserved pub: namespace refusal",
        "file": "bin/nightshift_provenance.py",
        "old": "    if isinstance(value, str) and value.startswith(RESERVED_NODE_ID_PREFIX):\n        _refuse(\"reserved_node_id_namespace\",\n                {\"id\": value, \"reserved_prefix\": RESERVED_NODE_ID_PREFIX})",
        "new": "    pass",
        "expect": [
            "test_publication_provenance_seam.TheUnifiedNodeIdNamespaceIsDisjoint.test_provenance_refuses_to_mint_an_id_in_publications_namespace",
            "test_publication_provenance_seam.TheUnifiedNodeIdNamespaceIsDisjoint.test_stage_refuses_a_reserved_snapshot_id_with_the_destination_unmoved"
        ]
    },
    {
        "id": "PG7-M21",
        "name": "PG7-M21 publication_lineage_chain ignores edge_kind on every hop",
        "file": "bin/nightshift_publication.py",
        "old": "    if review_link is None or review_link[\"edge_kind\"] != \"review\":",
        "new": "    if review_link is None:",
        "expect": [
            "test_pg7_pg8_mutation_gaps.PublicationLineageChainChecksEveryEdgeKind.test_an_attempt_whose_parent_edge_is_a_stage_does_not_close_the_chain"
        ]
    },
    {
        "id": "PG7-M22",
        "name": "PG7-M22 record_publication_lineage drops the self-edge refusal",
        "file": "bin/nightshift_publication.py",
        "old": "    if parent_node_id == child_node_id:\n        _refuse(\"lineage_self_loop_refused\",\n                {\"node_id\": child_node_id})",
        "new": "    pass",
        "expect": [
            "test_publication_provenance_seam.TheUnifiedNodeIdNamespaceIsDisjoint.test_the_lineage_writer_refuses_a_reserved_parent_and_a_self_edge"
        ]
    },
    {
        "id": "PG7-M23",
        "name": "PG7-M23 record_publication_lineage drops the reserved-namespace refusal",
        "file": "bin/nightshift_publication.py",
        "old": "    if parent_kind == \"input_snapshot\" and parent_node_id.startswith(LINEAGE_NODE_PREFIX):\n        _refuse(\"reserved_node_id_namespace\",\n                {\"id\": parent_node_id, \"reserved_prefix\": LINEAGE_NODE_PREFIX})",
        "new": "    pass",
        "expect": [
            "test_publication_provenance_seam.TheUnifiedNodeIdNamespaceIsDisjoint.test_the_lineage_writer_refuses_a_reserved_parent_and_a_self_edge"
        ]
    },
    {
        "id": "PG7-M24",
        "name": "PG7-M24 mark_source_not_retained drops the one-use CAS predicate",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"WHERE snapshot_id=? AND tenant_id=? AND repository_id=? AND source_not_retained=0\",",
        "new": "        \"WHERE snapshot_id=? AND tenant_id=? AND repository_id=?\",",
        "expect": [
            "test_provenance.ProvenanceLineage.test_legal_transition_succeeds_and_is_one_use",
            "test_provenance_lineage.SourceNotRetainedRepresentable.test_repeat_call_is_refused_not_silently_reapplied"
        ]
    },
    {
        "id": "PG7-M25",
        "name": "PG7-M25 mark_source_not_retained drops the tenant/repository scope",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"WHERE snapshot_id=? AND tenant_id=? AND repository_id=? AND source_not_retained=0\",\n        (t, reason, snapshot_id, tenant_id, repository_id))",
        "new": "        \"WHERE snapshot_id=? AND (tenant_id=? OR 1=1) AND (repository_id=? OR 1=1) AND source_not_retained=0\",\n        (t, reason, snapshot_id, tenant_id, repository_id))",
        "expect": [
            "test_provenance_lineage.CrossTenantReadSearchExportDelete.test_cross_tenant_delete_fails_closed"
        ]
    },
    {
        "id": "PG7-M26",
        "name": "PG7-M26 mark_source_not_retained leaves retention_ref/pack_digest live",
        "file": "bin/nightshift_provenance.py",
        "old": "\"source_not_retained_reason=?, retention_state='expired', retention_ref=NULL, \"\n        \"pack_digest=NULL \"",
        "new": "\"source_not_retained_reason=?, retention_state='expired' \"",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SourceNotRetainedDropsTheRetentionHandles.test_expiry_clears_the_retention_ref_and_the_pack_digest"
        ]
    },
    {
        "id": "PG7-M27",
        "name": "PG7-M27 mark_source_not_retained ignores rowcount (no refusal on repeat)",
        "file": "bin/nightshift_provenance.py",
        "old": "    if cur.rowcount != 1:\n        _refuse(\"snapshot_not_found_or_already_expired\")",
        "new": "    pass",
        "expect": [
            "test_provenance.ProvenanceLineage.test_legal_transition_succeeds_and_is_one_use",
            "test_provenance_lineage.CrossTenantReadSearchExportDelete.test_cross_tenant_delete_fails_closed",
            "test_provenance_lineage.SourceNotRetainedRepresentable.test_repeat_call_is_refused_not_silently_reapplied"
        ]
    },
    {
        "id": "PG7-M28",
        "name": "PG7-M28 source_not_retained PROPAGATES: expiring a snapshot also purges descendants",
        "file": "bin/nightshift_provenance.py",
        "old": "    if cur.rowcount != 1:\n        _refuse(\"snapshot_not_found_or_already_expired\")\n    return True",
        "new": "    if cur.rowcount != 1:\n        _refuse(\"snapshot_not_found_or_already_expired\")\n    try:\n        purge_lineage_node(cx, tenant_id, repository_id, snapshot_id)\n    except Exception:\n        pass\n    return True",
        "expect": [],
        "note": "SUPERSEDED by PG7-M28b. This edit is a no-op: purge_lineage_node() is keyed on a lineage node_id and the snapshot_id is not one, so nothing is ever purged. Kept in the catalogue so a later reader does not repeat the mistake."
    },
    {
        "id": "PG7-M28b",
        "name": "PG7-M28b mark_source_not_retained PROPAGATES: cascade-purges the snapshot's lineage descendants",
        "file": "bin/nightshift_provenance.py",
        "old": "    if cur.rowcount != 1:\n        _refuse(\"snapshot_not_found_or_already_expired\")\n    return True",
        "new": "    if cur.rowcount != 1:\n        _refuse(\"snapshot_not_found_or_already_expired\")\n    for _r in cx.execute(\"SELECT node_id FROM lineage_node WHERE node_kind='source_snapshot' AND ref_id=? AND tenant_id=? AND repository_id=?\",\n                          (snapshot_id, tenant_id, repository_id)).fetchall():\n        purge_lineage_node(cx, tenant_id, repository_id, _r[\"node_id\"])\n    return True",
        "expect": [
            "test_provenance_lineage.SourceNotRetainedRepresentable.test_non_propagation_a_content_carrying_descendant_is_untouched"
        ]
    },
    {
        "id": "PG7-M29",
        "name": "PG7-M29 content_carrying_descendants follows non-content-carrying edges too",
        "file": "bin/nightshift_provenance.py",
        "old": "            \"WHERE d.parent_node_id=? AND d.content_carrying=1 AND c.tenant_id=? AND c.repository_id=?\",",
        "new": "            \"WHERE d.parent_node_id=? AND c.tenant_id=? AND c.repository_id=?\",",
        "expect": [
            "test_provenance.ProvenanceLineage.test_source_not_retained_does_not_propagate_to_derived_narrative"
        ]
    },
    {
        "id": "PG7-M30",
        "name": "PG7-M30 record_source_snapshot drops the required-identity-fields check",
        "file": "bin/nightshift_provenance.py",
        "old": "    if missing:\n        _refuse(\"source_snapshot_incomplete\", {\"missing\": missing})\n    columns =",
        "new": "    if False:\n        _refuse(\"source_snapshot_incomplete\", {\"missing\": missing})\n    columns =",
        "expect": [
            "test_publication_footprint.BorrowedTestbedReadOnly.test_read_only_pipeline_leaves_testbed_untouched"
        ]
    },
    {
        "id": "PG7-M31",
        "name": "PG7-M31 source_snapshot immutability trigger removed",
        "file": "bin/nightshift_provenance.py",
        "old": "CREATE TRIGGER IF NOT EXISTS source_snapshot_immutable",
        "new": "CREATE TRIGGER IF NOT EXISTS disabled_source_snapshot_immutable_DISABLED",
        "expect": [],
        "note": "SUPERSEDED by PG7-M31b. Renaming a trigger does not disable it -- the body still says BEFORE UPDATE ON source_snapshot and still fires. Kept so a later reader does not repeat the mistake."
    },
    {
        "id": "PG7-M31b",
        "name": "PG7-M31b source_snapshot immutability trigger never fires (WHEN 0 AND ...)",
        "file": "bin/nightshift_provenance.py",
        "old": "BEFORE UPDATE ON source_snapshot FOR EACH ROW\nWHEN NOT (",
        "new": "BEFORE UPDATE ON source_snapshot FOR EACH ROW\nWHEN 0 AND NOT (",
        "expect": [
            "test_provenance.ProvenanceLineage.test_arbitrary_update_refused_by_trigger",
            "test_provenance_lineage.SourceNotRetainedRepresentable.test_any_other_mutation_is_refused_by_the_immutability_trigger"
        ]
    },
    {
        "id": "PG7-M32",
        "name": "PG7-M32 lineage_node immutability trigger never fires",
        "file": "bin/nightshift_provenance.py",
        "old": "CREATE TRIGGER IF NOT EXISTS lineage_node_immutable\nBEFORE UPDATE ON lineage_node FOR EACH ROW\nBEGIN",
        "new": "CREATE TRIGGER IF NOT EXISTS lineage_node_immutable\nBEFORE UPDATE ON lineage_node FOR EACH ROW\nWHEN 0\nBEGIN",
        "expect": [
            "test_pg7_pg8_mutation_gaps.ProvenanceSchemaBackstopsTheValidator.test_lineage_node_rows_are_immutable"
        ]
    },
    {
        "id": "PG7-M33",
        "name": "PG7-M33 closure_entry immutability trigger never fires",
        "file": "bin/nightshift_provenance.py",
        "old": "CREATE TRIGGER IF NOT EXISTS closure_entry_immutable\nBEFORE UPDATE ON closure_entry FOR EACH ROW\nBEGIN",
        "new": "CREATE TRIGGER IF NOT EXISTS closure_entry_immutable\nBEFORE UPDATE ON closure_entry FOR EACH ROW\nWHEN 0\nBEGIN",
        "expect": [
            "test_provenance_lineage.ClosureDiscoveryAndRequirement.test_closure_entry_rows_are_immutable"
        ]
    },
    {
        "id": "PG7-M34",
        "name": "PG7-M34 derived_from immutability trigger never fires",
        "file": "bin/nightshift_provenance.py",
        "old": "CREATE TRIGGER IF NOT EXISTS derived_from_immutable\nBEFORE UPDATE ON derived_from FOR EACH ROW\nBEGIN",
        "new": "CREATE TRIGGER IF NOT EXISTS derived_from_immutable\nBEFORE UPDATE ON derived_from FOR EACH ROW\nWHEN 0\nBEGIN",
        "expect": [
            "test_pg7_pg8_mutation_gaps.ProvenanceSchemaBackstopsTheValidator.test_derived_from_edges_are_immutable"
        ]
    },
    {
        "id": "PG7-M35",
        "name": "PG7-M35 source_snapshot tenant_id typeof CHECK removed (schema backstop)",
        "file": "bin/nightshift_provenance.py",
        "old": "  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),\n  repository_id INTEGER NOT NULL,\n  repository_fingerprint TEXT NOT NULL,",
        "new": "  tenant_id INTEGER NOT NULL,\n  repository_id INTEGER NOT NULL,\n  repository_fingerprint TEXT NOT NULL,",
        "expect": [
            "test_pg7_pg8_mutation_gaps.ProvenanceSchemaBackstopsTheValidator.test_source_snapshot_tenant_id_check_refuses_a_path_shaped_string"
        ]
    },
    {
        "id": "PG7-M36",
        "name": "PG7-M36 object_format CHECK removed from source_snapshot",
        "file": "bin/nightshift_provenance.py",
        "old": "  object_format TEXT NOT NULL CHECK(object_format IN ('sha1','sha256')),",
        "new": "  object_format TEXT NOT NULL,",
        "expect": [
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_an_invalid_object_format",
            "test_publication_gateway.SourceSnapshotIdentityAndClosure.test_stage_refuses_when_identity_fields_are_missing"
        ]
    },
    {
        "id": "PG8-M01",
        "name": "PG8-M01 require_dirty_policy_allows accepts the default 'refuse' policy",
        "file": "bin/nightshift_provenance.py",
        "old": "    if policy != \"copy_external\":\n        _refuse(\"dirty_checkout_refused\", {\"policy\": policy})",
        "new": "    pass",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_policy_default_refuse_blocks_before_any_snapshot_work"
        ]
    },
    {
        "id": "PG8-M02",
        "name": "PG8-M02 require_dirty_policy_allows accepts an unknown policy string",
        "file": "bin/nightshift_provenance.py",
        "old": "    if policy not in DIRTY_CHECKOUT_POLICIES:\n        _refuse(\"invalid_dirty_checkout_policy\", {\"policy\": policy})",
        "new": "    pass",
        "expect": [
            "test_pg7_pg8_mutation_gaps.DirtyDetectionAndPolicyEdges.test_an_unknown_policy_string_is_refused_as_invalid_not_merely_refused"
        ]
    },
    {
        "id": "PG8-M03",
        "name": "PG8-M03 acquire_mirror uses `git clone --bare <customer>` (hardlinks customer loose objects)",
        "file": "bin/nightshift_provenance.py",
        "old": "    if not os.path.isdir(mirror_path) or not os.path.isdir(os.path.join(mirror_path, \"objects\")):\n        init = subprocess.run(\n            [\"git\", \"init\", \"--bare\", mirror_path],",
        "new": "    if not os.path.isdir(mirror_path) or not os.path.isdir(os.path.join(mirror_path, \"objects\")):\n        init = subprocess.run(\n            [\"git\", \"clone\", \"--bare\", customer_path, mirror_path],",
        "expect": [
            "test_provenance.MirrorAcquisitionIsNotClone.test_acquire_mirror_wrapped_in_footprint_proof_is_clean",
            "test_provenance.MirrorAcquisitionIsNotClone.test_clone_bare_hardlinks_but_acquire_mirror_does_not"
        ]
    },
    {
        "id": "PG8-M04",
        "name": "PG8-M04 capture_dirty_snapshot returns synthetic=0 (snapshot no longer marked)",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"synthetic\": 1,\n        \"synthetic_parent_commit_oid\": base,",
        "new": "        \"synthetic\": 0,\n        \"synthetic_parent_commit_oid\": base,",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_capture_dirty_snapshot_leaves_customer_repo_untouched",
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_synthetic_snapshot_is_marked_not_presented_as_upstream"
        ]
    },
    {
        "id": "PG8-M05",
        "name": "PG8-M05 capture_dirty_snapshot drops synthetic_parent_commit_oid (loses the upstream base)",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"synthetic\": 1,\n        \"synthetic_parent_commit_oid\": base,",
        "new": "        \"synthetic\": 1,\n        \"synthetic_parent_commit_oid\": None,",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_synthetic_snapshot_is_marked_not_presented_as_upstream"
        ]
    },
    {
        "id": "PG8-M06",
        "name": "PG8-M06 dirty_capture_method mislabels the capture as git_stash",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"dirty_capture_method\": \"external_copy\",",
        "new": "        \"dirty_capture_method\": \"git_stash\",",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_capture_dirty_snapshot_leaves_customer_repo_untouched"
        ]
    },
    {
        "id": "PG8-M07",
        "name": "PG8-M07 synthetic commit drops the FIXED dates (determinism lost)",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"GIT_AUTHOR_DATE\": capture_time, \"GIT_COMMITTER_DATE\": capture_time,\n    })",
        "new": "    })",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_determinism_same_base_and_dirty_bytes_same_oid_and_patch_digest"
        ]
    },
    {
        "id": "PG8-M08",
        "name": "PG8-M08 synthetic commit drops the FIXED author/committer identity",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"GIT_AUTHOR_NAME\": \"nightshift\", \"GIT_AUTHOR_EMAIL\": \"nightshift@invalid\",\n        \"GIT_COMMITTER_NAME\": \"nightshift\", \"GIT_COMMITTER_EMAIL\": \"nightshift@invalid\",\n",
        "new": "",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_synthetic_commit_is_authored_by_nightshift_not_the_operator"
        ]
    },
    {
        "id": "PG8-M09",
        "name": "PG8-M09 `git add -A` runs in the CUSTOMER checkout instead of the workspace",
        "file": "bin/nightshift_provenance.py",
        "old": "    add = _ws_git([\"add\", \"-A\"])",
        "new": "    subprocess.run([\"git\",\"-C\",customer_path,\"add\",\"-A\"],capture_output=True,env=identity_env,timeout=60)\n    add = _ws_git([\"add\", \"-A\"])",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_capture_dirty_snapshot_leaves_customer_repo_untouched"
        ]
    },
    {
        "id": "PG8-M10",
        "name": "PG8-M10 the retention ref is pushed into the CUSTOMER repo, not the mirror",
        "file": "bin/nightshift_provenance.py",
        "old": "        [\"git\", \"-C\", workspace_path, \"push\", mirror_path, f\"{synthetic_oid}:{retention_ref}\"],",
        "new": "        [\"git\", \"-C\", workspace_path, \"push\", customer_path, f\"{synthetic_oid}:{retention_ref}\"],",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_capture_dirty_snapshot_leaves_customer_repo_untouched",
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_determinism_same_base_and_dirty_bytes_same_oid_and_patch_digest",
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_synthetic_snapshot_is_marked_not_presented_as_upstream"
        ]
    },
    {
        "id": "PG8-M11",
        "name": "PG8-M11 capture_dirty_snapshot drops the not_dirty refusal",
        "file": "bin/nightshift_provenance.py",
        "old": "    if not dirty[\"dirty\"]:\n        _refuse(\"not_dirty\")",
        "new": "    pass",
        "expect": [
            "test_provenance.DirtyCheckoutSyntheticSnapshot.test_clean_repo_refuses_not_dirty"
        ]
    },
    {
        "id": "PG8-M12",
        "name": "PG8-M12 build_copy_list drops UNTRACKED (??) files -- dirty bytes silently missing",
        "file": "bin/nightshift_provenance.py",
        "old": "    files += [e[\"path\"] for e in dirty_status[\"tracked_or_untracked\"] if e[\"code\"] == \"??\"]",
        "new": "    pass",
        "expect": [
            "test_pg7_pg8_mutation_gaps.DirtyDetectionAndPolicyEdges.test_a_renamed_file_does_not_shift_the_porcelain_parse",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_reused_workspace_is_wiped_before_the_next_capture",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_symlink_is_captured_as_a_symlink_not_as_its_target",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_manifest_digest_moves_when_the_dirty_bytes_move",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_untracked_file_is_in_the_synthetic_commit"
        ]
    },
    {
        "id": "PG8-M13",
        "name": "PG8-M13 build_copy_list stops excluding DELETED tracked files",
        "file": "bin/nightshift_provenance.py",
        "old": "    deleted = {e[\"path\"] for e in dirty_status[\"tracked_or_untracked\"] if \"D\" in e[\"code\"]}",
        "new": "    deleted = set()",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_file_deleted_in_the_worktree_is_absent_from_the_synthetic_commit",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_reused_workspace_is_wiped_before_the_next_capture",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_symlink_is_captured_as_a_symlink_not_as_its_target",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_ignored_files_are_not_copied_by_default",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_include_ignored_copies_the_file_and_records_it_in_the_manifest",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_executable_bit_survives_into_the_tree_and_the_manifest",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_manifest_digest_moves_when_the_dirty_bytes_move",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_modified_tracked_bytes_are_in_the_synthetic_commit",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_synthetic_commit_is_authored_by_nightshift_not_the_operator",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_synthetic_commit_really_has_the_customer_head_as_its_parent",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_untracked_file_is_in_the_synthetic_commit"
        ]
    },
    {
        "id": "PG8-M14",
        "name": "PG8-M14 build_copy_list always includes IGNORED files regardless of the flag",
        "file": "bin/nightshift_provenance.py",
        "old": "    if include_ignored:\n        files += [e[\"path\"] for e in dirty_status[\"ignored\"]]",
        "new": "    files += [e[\"path\"] for e in dirty_status[\"ignored\"]]",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_ignored_files_are_not_copied_by_default"
        ]
    },
    {
        "id": "PG8-M15",
        "name": "PG8-M15 detect_dirty drops -uall (untracked files inside a new dir go unseen)",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"status\", \"--porcelain=v1\", \"-z\", \"-uall\", \"--ignored=matching\"])",
        "new": "        \"status\", \"--porcelain=v1\", \"-z\", \"--ignored=matching\"])",
        "expect": [
            "test_provenance.FootprintStatusInsufficiency.test_e6c_showuntrackedfiles_no_makes_bare_ignored_probe_fail_not_silently_clean"
        ]
    },
    {
        "id": "PG8-M16",
        "name": "PG8-M16 detect_dirty drops --ignored=matching (ignored bucket always empty)",
        "file": "bin/nightshift_provenance.py",
        "old": "        \"status\", \"--porcelain=v1\", \"-z\", \"-uall\", \"--ignored=matching\"])",
        "new": "        \"status\", \"--porcelain=v1\", \"-z\", \"-uall\"])",
        "expect": [
            "test_provenance.FootprintStatusInsufficiency.test_e6a_ignore_file_edit_hidden_from_bare_status_caught_by_ignored_matching",
            "test_provenance.FootprintStatusInsufficiency.test_e6b_core_excludesfile_hidden_from_bare_uall_caught_by_ignored_matching"
        ]
    },
    {
        "id": "PG8-M17",
        "name": "PG8-M17 detect_dirty counts IGNORED-only as dirty",
        "file": "bin/nightshift_provenance.py",
        "old": "    tracked_or_untracked = [e for e in entries if e[\"code\"] != \"!!\"]",
        "new": "    tracked_or_untracked = list(entries)",
        "expect": [
            "test_pg7_pg8_mutation_gaps.DirtyDetectionAndPolicyEdges.test_an_ignored_only_repository_is_not_dirty"
        ]
    },
    {
        "id": "PG8-M18",
        "name": "PG8-M18 copy_worktree_files loses the executable bit in the manifest",
        "file": "bin/nightshift_provenance.py",
        "old": "            \"mode\": \"100755\" if st.st_mode & 0o111 else \"100644\",",
        "new": "            \"mode\": \"100644\",",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_executable_bit_survives_into_the_tree_and_the_manifest"
        ]
    },
    {
        "id": "PG8-M19",
        "name": "PG8-M19 copy_worktree_files dereferences symlinks instead of recreating them",
        "file": "bin/nightshift_provenance.py",
        "old": "        if os.path.islink(src):",
        "new": "        if False:",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_symlink_is_captured_as_a_symlink_not_as_its_target"
        ]
    },
    {
        "id": "PG8-M20",
        "name": "PG8-M20 _wipe_workspace_worktree becomes a no-op (stale HEAD files leak into the synthetic tree)",
        "file": "bin/nightshift_provenance.py",
        "old": "    for name in os.listdir(workspace_path):\n        if name == \".git\":\n            continue",
        "new": "    for name in []:\n        if name == \".git\":\n            continue",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_reused_workspace_is_wiped_before_the_next_capture"
        ]
    },
    {
        "id": "PG8-M21",
        "name": "PG8-M21 worktree_manifest_digest ignores the manifest (constant digest)",
        "file": "bin/nightshift_provenance.py",
        "old": "    canonical = json.dumps(manifest, sort_keys=True, separators=(\",\", \":\"))\n    return hashlib.sha256(canonical.encode(\"utf-8\")).hexdigest()\n\n\ndef acquire_mirror",
        "new": "    canonical = json.dumps([], sort_keys=True, separators=(\",\", \":\"))\n    return hashlib.sha256(canonical.encode(\"utf-8\")).hexdigest()\n\n\ndef acquire_mirror",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_manifest_digest_moves_when_the_dirty_bytes_move"
        ]
    },
    {
        "id": "PG8-M22",
        "name": "PG8-M22 customer_git allowlist admits every write subcommand (choke point removed)",
        "file": "bin/nightshift_provenance.py",
        "old": "    if cmd not in _SIMPLE_ALLOWLIST:\n        _refuse(\"customer_repo_write_refused\", {\n            \"reason\": \"subcommand not allowlisted\", \"argv\": list(args)})",
        "new": "    pass",
        "expect": [
            "test_provenance.CustomerGitGuard.test_refuses_write_subcommands"
        ]
    },
    {
        "id": "PG8-M23",
        "name": "PG8-M23 customer_git stops refusing -c/--git-dir/--work-tree smuggling",
        "file": "bin/nightshift_provenance.py",
        "old": "        if any(arg == p or arg.startswith(p) for p in _FORBIDDEN_ARG_PREFIXES):\n            _refuse(\"customer_repo_write_refused\", {\n                \"reason\": \"forbidden flag\", \"flag\": arg})",
        "new": "        pass",
        "expect": [
            "test_provenance.CustomerGitGuard.test_refuses_smuggled_config_and_dir_overrides"
        ]
    },
    {
        "id": "PG8-M24",
        "name": "PG8-M24 capture_dirty_snapshot drops the requires-worktree guard",
        "file": "bin/nightshift_provenance.py",
        "old": "    if roots[\"worktree_root\"] is None:\n        _refuse(\"dirty_checkout_requires_worktree\")",
        "new": "    pass",
        "expect": [],
        "note": "DEAD CODE, deliberately left uncovered. `dirty_checkout_requires_worktree` is unreachable: worktree_root is None only for a bare repository, and detect_dirty() already refused footprint_probe_failed two lines earlier because `git status` fails in a bare repo. See test_pg7_pg8_mutation_gaps.DirtyDetectionAndPolicyEdges.test_a_bare_repository_is_refused_and_pins_an_unreachable_guard."
    },
    {
        "id": "PG8-M25",
        "name": "PG8-M25 _parse_porcelain_z stops skipping a rename entry's original-path field",
        "file": "bin/nightshift_provenance.py",
        "old": "        if code and code[0] in (\"R\", \"C\"):\n            i += 1  # the following element is the original path; skip it",
        "new": "        pass",
        "expect": [
            "test_pg7_pg8_mutation_gaps.DirtyDetectionAndPolicyEdges.test_a_renamed_file_does_not_shift_the_porcelain_parse"
        ]
    },
    {
        "id": "PG8-M26",
        "name": "PG8-M26 synthetic commit is made with NO parent (upstream base unlinked)",
        "file": "bin/nightshift_provenance.py",
        "old": "    ct = _ws_git([\"commit-tree\", tree_oid, \"-p\", base, \"-m\",",
        "new": "    ct = _ws_git([\"commit-tree\", tree_oid, \"-m\",",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_synthetic_commit_really_has_the_customer_head_as_its_parent"
        ]
    },
    {
        "id": "PG8-M27",
        "name": "PG8-M27 the dirty worktree is never copied in (synthetic tree == HEAD tree)",
        "file": "bin/nightshift_provenance.py",
        "old": "    _wipe_workspace_worktree(workspace_path)\n    manifest = copy_worktree_files(roots[\"worktree_root\"], file_list, workspace_path)",
        "new": "    manifest = copy_worktree_files(roots[\"worktree_root\"], [], workspace_path)",
        "expect": [
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_reused_workspace_is_wiped_before_the_next_capture",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_a_symlink_is_captured_as_a_symlink_not_as_its_target",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_include_ignored_copies_the_file_and_records_it_in_the_manifest",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_executable_bit_survives_into_the_tree_and_the_manifest",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_manifest_digest_moves_when_the_dirty_bytes_move",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_modified_tracked_bytes_are_in_the_synthetic_commit",
            "test_pg7_pg8_mutation_gaps.SyntheticSnapshotContainsTheDirtyTree.test_the_untracked_file_is_in_the_synthetic_commit"
        ]
    },
    {
        "id": "PG8-M28",
        "name": "PG8-M28 acquire_mirror/create_workspace drop the absolute-dir argument validation",
        "file": "bin/nightshift_provenance.py",
        "old": "def _validate_abs_dir_arg(path, reason_code):",
        "new": "def _validate_abs_dir_arg(path, reason_code):\n    return path",
        "expect": [
            "test_pg7_pg8_mutation_gaps.DirtyDetectionAndPolicyEdges.test_mirror_and_workspace_paths_must_be_absolute_normalised_directories"
        ]
    }
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _by_id(mutation_id):
    for m in MUTATIONS:
        if m["id"] == mutation_id:
            return m
    raise SystemExit("no such mutation: %s (try --list)" % mutation_id)


def run_one(m, *, modules=None):
    path = os.path.join(REPO, m["file"])
    with open(path) as fh:
        before = fh.read()
    before_sha = _sha(path)
    occurrences = before.count(m["old"])
    if occurrences != 1:
        raise SystemExit(
            "%s: anchor occurs %d times in %s, expected exactly 1 -- the "
            "catalogue entry has drifted from the code" %
            (m["id"], occurrences, m["file"]))
    try:
        with open(path, "w") as fh:
            fh.write(before.replace(m["old"], m["new"]))
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "-v"] + (modules or SCOPE),
            cwd=TESTS, capture_output=True, text=True)
        out = proc.stdout + proc.stderr
    finally:
        with open(path, "w") as fh:
            fh.write(before)
        if _sha(path) != before_sha:
            raise SystemExit("RESTORE FAILED for %s -- restore it by hand "
                             "before doing anything else" % path)

    observed = sorted({
        match.group(1)
        for match in re.finditer(r"^(?:FAIL|ERROR): \S+ \(([^)]+)\)", out, re.M)})
    expected = set(m["expect"])
    print("%s  %s" % (m["id"], m["name"]))
    print("  file    : %s" % m["file"])
    if m.get("note"):
        print("  note    : %s" % m["note"])
    print("  expected: %s" % (sorted(expected) or "-- nothing (see note) --"))
    print("  observed: %s" % (observed or "-- nothing went red --"))
    missing = sorted(expected - set(observed))
    if missing:
        print("  MISSING : %s" % missing)
    return not missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="print the mutation catalogue and exit")
    group.add_argument("--run", metavar="ID", help="inject one mutation")
    group.add_argument("--all", action="store_true",
                       help="inject every catalogued mutation, one at a time")
    parser.add_argument("--modules", nargs="*", default=None,
                        help="unittest modules to run (default: the PG-4/6/7/8 scope)")
    args = parser.parse_args(argv)

    if args.list:
        for m in MUTATIONS:
            flag = " " if m["expect"] else "!"
            print("%s %-10s %s" % (flag, m["id"], m["name"]))
        print("\n! = no test went red; read that entry's `note`.")
        return 0
    if args.run:
        return 0 if run_one(_by_id(args.run), modules=args.modules) else 1
    ok = True
    for m in MUTATIONS:
        ok = run_one(m, modules=args.modules) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
