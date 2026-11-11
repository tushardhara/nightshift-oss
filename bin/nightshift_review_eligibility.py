#!/usr/bin/env python3
"""Compute review eligibility: direct-or-reused receipts, one digest.

Issue #232 (#228C), ADR-0040 (+ addendum, PM-acceptance round 1). Deliberately
its OWN module, not folded into `bin/nightshift_review_receipts.py`: #231's own
frozen contract (AC-5) requires that module to never import
`bin/nightshift_review_plan.py` and to never carry a `plan_digest` field
(`tests/test_review_receipts.py::ConsolidationIsPureTest
.test_module_does_not_import_the_shadow_planner`,
`::ParseRequiredDomainsTest.test_no_field_named_plan_digest_anywhere`) --
consolidation must never reach for #228A's diagnostic-only `suggested_domains`
as if it were authoritative. Eligibility legitimately needs both: the persisted
receipts (`nightshift_review_receipts`) AND the live planner's diff/classification
primitives (`nightshift_review_plan`), so it lives beside them as a third,
separate module rather than weakening either one's own frozen boundary.

Everything below recomputes scope and blob OIDs live from Git and the CURRENTLY
COMMITTED `program/review-risk.json` ruleset on every call. No new persistence
for scope/blob-OID data -- `base_sha`/`head_sha` are immutable, reachable
commits (no-force-push is a standing invariant, ADR-0026), so that data is
always exactly reconstructable. Ruleset DRIFT (AC-2d/AC-4b/AC-6) is the one
thing that genuinely cannot be reconstructed after the fact -- nothing records
what ruleset was current when a past receipt was validated -- so `review
receipt-record` additively stamps `review_receipts.rules_digest` (migration seq
2) at validation time, and eligibility compares it against a freshly computed
digest of today's ruleset. A NULL (pre-migration row) or mismatched digest is
treated identically: fail closed, the domain is not satisfied. Pure read
otherwise: never writes to `control.db`, never touches GitHub (AC-3).
"""

import sqlite3

import nightshift_review_plan as plan
import nightshift_review_receipts as receipts


ReceiptDenied = receipts.ReceiptDenied
FULL_SHA = receipts.FULL_SHA
canonical_json = receipts.canonical_json
digest = receipts.digest
parse_required_domains = receipts.parse_required_domains

WHOLE_DIFF_DOMAINS = frozenset({"pm_acceptance", "code_review", "cross_crew_review"})
SCOPED_DOMAINS = frozenset({"security", "staff_adr", "qa", "ux"})

# Mechanical translation of bin/nightshift_review_plan.py's _classify extra-domain
# logic (ADR-0040 "Domain -> covered-scope mapping" table): which review_receipts
# domain a triggered high_risk_globs group maps to. Kept as an explicit constant,
# not derived from _classify itself, because _classify's own return contract is
# frozen (#228A shadow-only authority) -- see ADR-0040 Option A risk (2) and its
# named reversal trigger.
_HIGH_RISK_TO_DOMAIN = {
    "control_plane": ("security", "staff_adr"),
    "workflow": ("security",),
    "security_path": ("security",),
    "constitution": ("staff_adr",),
    "schema_migration": ("staff_adr",),
    "dependency": ("staff_adr",),
    "public_contract": ("staff_adr",),
}


def diff_domain_scope(rules, repo, entries):
    """path -> frozenset(scoped domains it triggers), and the full path set.

    Mirrors bin/nightshift_review_plan.py's `_classify` per-path trigger loop
    (high_risk_globs, conditional_globs, sensitive-content scan) but is a pure
    function of (rules, entries) with no task/crew/ownership concerns -- those
    don't affect covered scope for reuse purposes.
    """
    domains_by_path = {}
    all_paths = set()
    blob_text_cache = {}
    for entry in entries:
        side_paths = [entry[f"{side}_path"] for side in ("old", "new")
                     if entry[f"{side}_path"] is not None]
        needs_scan = any(
            plan._matches(glob, path) for path in side_paths
            for glob in rules["content_scan_globs"])
        for side in ("old", "new"):
            path = entry[f"{side}_path"]
            if path is None:
                continue
            all_paths.add(path)
            domains = set(domains_by_path.get(path, ()))
            for group, globs in rules["high_risk_globs"].items():
                if any(plan._matches(glob, path) for glob in globs):
                    domains.update(_HIGH_RISK_TO_DOMAIN.get(group, ()))
            for cond_domain, globs in rules["conditional_globs"].items():
                if any(plan._matches(glob, path) for glob in globs):
                    domains.add(cond_domain)
            if needs_scan:
                oid = entry[f"{side}_blob_oid"]
                if oid is not None:
                    if oid not in blob_text_cache:
                        raw = plan._blob(repo, oid, rules["limits"]["max_blob_bytes"])
                        try:
                            text = raw.decode("utf-8").lower() if raw is not None else None
                        except UnicodeDecodeError:
                            text = None
                        blob_text_cache[oid] = text
                    text = blob_text_cache[oid]
                    if text is not None and any(
                            term in text for term in rules["sensitive_terms"]):
                        domains.add("security")
            domains_by_path[path] = frozenset(domains)
    return domains_by_path, frozenset(all_paths)


def covered_scope(domain, scope_by_path, all_paths):
    if domain in WHOLE_DIFF_DOMAINS:
        return all_paths
    return frozenset(path for path, domains in scope_by_path.items()
                     if domain in domains)


def _blob_oid_or_none(repo, commit, path):
    try:
        return plan._blob_oid(repo, commit, path)
    except plan.PlanDenied:
        return None


def _receipt_rules_digest(cx, task_id, head_sha, domain):
    """NULL/missing column both read as None -- caller treats either as drift."""
    try:
        row = cx.execute(
            "SELECT rules_digest FROM review_receipts"
            " WHERE task_id=? AND head_sha=? AND domain=?",
            (task_id, head_sha, domain)).fetchone()
    except sqlite3.OperationalError:
        return None  # pre-migration 231-only schema, column doesn't exist yet
    return row["rules_digest"] if row is not None else None


def content_unchanged(rules, repo, base, h1, h2, domain, scope2, all2):
    """AC-2(c) only: every path in D's covered scope at h2 has an identical
    blob OID at h1 and h2. AC-2(a)/(b) (verdict/findings) and AC-2(d)/AC-4(b)
    (ruleset drift, via rules_digest) are checked by the caller against DB
    rows -- see `compute_eligibility`."""
    covered = covered_scope(domain, scope2, all2)
    if not covered:
        return True  # nothing in this domain's scope changed at h2 (vacuous)
    for path in covered:
        if _blob_oid_or_none(repo, h1, path) != _blob_oid_or_none(repo, h2, path):
            return False
    return True


def compute_eligibility(cx, repo, task_id, head_sha, contract_text):
    """Pure read. Raises ReceiptDenied (caller maps to exit 3) on any fail-closed
    branch (AC-6); otherwise returns the full eligibility body plus its digest
    (AC-5), including 'missing_domains' naming only the domains needing rerun."""
    required = parse_required_domains(contract_text)  # raises contract_* reasons

    if not isinstance(head_sha, str) or not FULL_SHA.fullmatch(head_sha):
        raise ReceiptDenied("head_sha_invalid")

    try:
        task = plan._read_task(cx, task_id)
    except plan.PlanDenied as exc:
        raise ReceiptDenied(exc.reason) from exc
    base = task["base_sha"]
    if not isinstance(base, str) or not plan.FULL_OID.fullmatch(base):
        raise ReceiptDenied("base_invalid")

    try:
        repo_path, _base_tree, head_tree = plan._target(repo, base, head_sha)
    except plan.PlanDenied as exc:
        raise ReceiptDenied(exc.reason) from exc

    # Fresh plan every call (AC-4b): this is what makes ruleset drift visible
    # in the OUTPUT digest -- but the actual drift GATE that decides
    # eligibility is the rules_digest comparison below, not this field.
    try:
        report = plan.build_plan(cx, task_id, repo_path, head_sha)
    except plan.PlanDenied as exc:
        raise ReceiptDenied(exc.reason) from exc
    fresh_plan_digest = report["suggested_plan"]["plan_digest"]
    try:
        rules, authority = plan._bundle_rules()
    except plan.PlanDenied as exc:
        raise ReceiptDenied(exc.reason) from exc
    current_rules_digest = authority["rules_digest"]

    entries2, _, _ = plan._changes(repo_path, base, head_sha,
                                   rules["limits"]["max_files"])
    scope2, all2 = diff_domain_scope(rules, repo_path, entries2)

    satisfied = {}
    missing = []
    for domain in required:
        row = cx.execute(
            "SELECT verdict FROM review_receipts"
            " WHERE task_id=? AND head_sha=? AND domain=?",
            (task_id, head_sha, domain)).fetchone()
        if (row is not None and row["verdict"] == "pass"
                and _receipt_rules_digest(cx, task_id, head_sha, domain)
                == current_rules_digest):
            blocker = cx.execute(
                "SELECT 1 FROM review_findings WHERE task_id=? AND head_sha=?"
                " AND domain=? AND severity='MERGE_BLOCKER' LIMIT 1",
                (task_id, head_sha, domain)).fetchone()
            if blocker is None:
                satisfied[domain] = {
                    "domain": domain, "satisfied_by": "direct",
                    "head_sha": head_sha, "verdict": "pass"}
                continue

        reused_from = None
        candidates = cx.execute(
            "SELECT DISTINCT head_sha FROM review_receipts"
            " WHERE task_id=? AND domain=? AND verdict='pass' AND head_sha != ?"
            " ORDER BY head_sha", (task_id, domain, head_sha)).fetchall()
        for candidate in candidates:
            h1 = candidate["head_sha"]
            if _receipt_rules_digest(cx, task_id, h1, domain) != current_rules_digest:
                continue  # AC-2(d)/AC-4(b): ruleset drift since h1 was validated
            any_finding = cx.execute(
                "SELECT 1 FROM review_findings"
                " WHERE task_id=? AND head_sha=? AND domain=? LIMIT 1",
                (task_id, h1, domain)).fetchone()
            if any_finding is not None:
                continue  # AC-2(b): any finding, any severity, blocks reuse
            try:
                if content_unchanged(rules, repo_path, base, h1, head_sha,
                                     domain, scope2, all2):
                    reused_from = h1
                    break
            except plan.PlanDenied:
                continue
        if reused_from is not None:
            satisfied[domain] = {
                "domain": domain, "satisfied_by": "reused",
                "head_sha": reused_from, "verdict": "pass"}
        else:
            missing.append(domain)

    body = {
        "task_id": task_id,
        "plan_digest": fresh_plan_digest,
        "rules_digest": current_rules_digest,
        "head_sha": head_sha,
        "head_tree_oid": head_tree,
        "eligible": not missing,
        "domains": [satisfied[d] for d in sorted(satisfied)],
        "missing_domains": sorted(missing),
    }
    return {**body, "eligibility_digest": digest(canonical_json(body))}
