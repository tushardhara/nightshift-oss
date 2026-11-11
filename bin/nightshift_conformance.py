#!/usr/bin/env python3
"""nightshift_conformance.py -- the conformance gate (#442): refuse contract
violations, do not review them.

NOT "Nightshift Storage Contract" (tests/nsc_conformance.py, ADR-0078) --
that is an unrelated domain (storage traces). This module is #443's
Engineering Contract child: it consumes the already-built project contract
(bin/nightshift_project_contract.py) and DENIES a candidate change that
widens the declared stack beyond what is permitted.

WHAT THIS IS NOT
================
It adds zero detection logic. Every fact this module reasons about --
which languages, which test framework -- was already observed by
`nightshift_project_contract.build_contract()`. This module answers exactly
one question that #443 deliberately left unanswered: given two contracts
(BASE and CANDIDATE) and a declared rule set, is the candidate permitted?

THE AUTHORITY LAYERING THIS MODULE EXISTS TO ENFORCE
=====================================================
Three layers, cut by what each is PERMITTED to do:

  FACTS      deterministic, observed from the repo at an exact SHA -> BINDING
  RULES      declared, frozen, versioned                           -> BINDING
  EXPERIENCE learned across tasks, similar past changes             -> ADVISORY ONLY

**Advisory or learned state may be printed beside a decision and may never
enter the computation of what is permitted.** `evaluate()` and the two
`permitted_*` functions below accept ONLY a contract and a rule set --
there is no parameter through which a retrieval result, a memory hit, or a
"people seem to use this here" note could reach the permission computation.
Advisory content attaches strictly AFTER a decision exists, through
`attach_advisory()`, which re-verifies (by recomputing `decision_digest`
from scratch) that the verdict and violations it was handed are exactly
the verdict and violations it hands back. See ADR-0086.

CONTRACT-VS-CONTRACT, NOT CLAIM-VS-CONTRACT
============================================
The primary decision path (`evaluate`, `build_decision`, wired into CI)
compares two COMPILED CONTRACTS -- base and candidate -- as a set
difference: languages present in the candidate but not the base, test/
integration/e2e frameworks present in the candidate but not the base. This
is deliberately NOT built on top of
`nightshift_project_contract.contradicts()`: that function compares a
contract against a hand-written CLAIM and is documented as blind to any
contract field whose status is `absent` or `conflict` ("a field the
contract could not observe or could not resolve contradicts nothing").
Measured directly against a real fixture whose e2e field resolves to
`conflict` (`value: null`), a claim-comparison built on `contradicts()`
alone would see `{}` against `{}` and ALLOW the exact stack it should deny.
Reading the value SET from `candidates[*].value` (which survive `conflict`)
rather than from `value` (which is `None` under `conflict`) is what closes
that hole -- see `frameworks_of()` below.

`evaluate_claim()` keeps the ticket-level, claim-vs-contract shape and
DELEGATES to `contradicts()` rather than re-deriving its comparison, exactly
as #443 asked. It reports which claimed fields the contract could not
resolve (`claim_unevaluated_fields`) so a caller reads that as "unproven",
never as silent consent. It is a secondary, library-only path; the gate CI
actually runs is the contract-vs-contract path.

EXIT CODES (repo-wide vocabulary, ruled for this module)
==========================================================
  0  allow           -- no violation, permitted
  1  operation failed -- an unexpected internal error (never a verdict)
  2  usage error     -- bad CLI arguments / repo_path is not a repository
  3  refusal         -- either `deny` (verdict: deny, reason_code
                        project_contract_violation) or a bad RULES file
                        (verdict: rules_invalid, reason_code rules_invalid)
  4  indeterminate   -- the gate could not see what it was judging: an
                        unresolvable SHA, an invalid contract, an unreadable
                        rules file, a vacuous base==head comparison, or a
                        supplied contract file whose digest does not
                        recompute. NEVER reachable as an allow. The one new
                        exit code this module is granted; no other module
                        may use it.

Every non-zero exit prints exactly one JSON object to stdout carrying
`ok: false` and `reason_code`, alongside `verdict` and `violations` -- the
same flat shape `nightshift_authority.AuthorityError`/`error_payload` and
`nightshift_publication.PublicationError`/`error_payload` already use.

Library:
    from nightshift_conformance import evaluate, build_decision, load_rules
    rules = load_rules("program/conformance-rules.json")
    decision = build_decision(repo_path, base_sha=base, head_sha=head, rules=rules)

CLI:
    python3 nightshift_conformance.py <repo_path> --base-sha <sha> \\
        --head-sha <sha> --rules program/conformance-rules.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import nightshift_project_contract as pc

# Optional dependency, guarded on purpose: at the time this module was
# written, bin/nightshift_authority.py did not yet export `scope_from_env`
# (a sibling, concurrently-developed stream owns it). A bare
# `from nightshift_authority import scope_from_env` would make THIS ENTIRE
# MODULE fail to import until that stream lands -- worse than the thing it
# is guarding against, since it would take every unrelated test down with
# it. `_resolve_repository_id()` below degrades to
# `repository_id_source: "not_in_environment"` whenever the symbol is
# missing, exactly the same outcome as when the symbol exists but the two
# environment variables it reads are unset. Once the sibling stream lands,
# this becomes a live call with no code change required here.
try:
    from nightshift_authority import scope_from_env
except ImportError:
    scope_from_env = None


# ---------------------------------------------------------------------------
# Error vocabulary -- identical shape to AuthorityError/PublicationError.
# Every refusal or indeterminate outcome this module can produce funnels
# through here; main() is the only place that catches it.
# ---------------------------------------------------------------------------

class ConformanceError(Exception):
    def __init__(self, reason_code: str, exit_code: int = 1, details: Optional[dict] = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error: "ConformanceError") -> Dict[str, Any]:
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _usage_error(reason_code: str, details: Optional[dict] = None) -> "ConformanceError":
    return ConformanceError(reason_code, 2, details)


def _refuse(reason_code: str, details: Optional[dict] = None) -> "ConformanceError":
    return ConformanceError(reason_code, 3, details)


def _indeterminate(reason_code: str, details: Optional[dict] = None) -> "ConformanceError":
    return ConformanceError(reason_code, 4, details)


class AdvisoryEscalation(Exception):
    """Raised by `attach_advisory` when the decision it was handed back
    differs from the decision it was given. This can only fire if
    `attach_advisory` itself is broken -- it is the load-bearing proof
    that advisory content cannot widen a decision, not a runtime
    condition callers are expected to handle."""


# ---------------------------------------------------------------------------
# Repository identity -- the DEPLOYMENT axis, separate from baseline_sha /
# contract_digest (the CONTENT axis). Resolved from the environment only;
# never re-spelled here, never invented here. See ADR-0086.
# ---------------------------------------------------------------------------

def _resolve_repository_id() -> Tuple[Optional[int], str]:
    if scope_from_env is None:
        return None, "not_in_environment"
    try:
        scope = scope_from_env()
        repository_id = getattr(scope, "repository_id", None) if scope is not None else None
    except Exception:
        return None, "not_in_environment"
    if repository_id is None:
        return None, "not_in_environment"
    return repository_id, "environment"


# ---------------------------------------------------------------------------
# RULES -- declared, frozen, versioned. Loading is fail-closed: an unknown
# rule kind is REFUSED, never skipped; a rule that declares itself
# non-binding is REFUSED (a non-binding rule is EXPERIENCE, not a RULE, and
# that layering is enforced in the data, not just in code); a waiver
# without an ADR reference is REFUSED.
#
# The rule KINDS below are the only vocabulary this module understands.
# Deliberately generic: no ecosystem, language or tool name appears in this
# module or in program/conformance-rules.json (issue #439 -- the inward
# mirror -- scans bin/, deploy/, program/ for exactly those tokens; this
# file must carry none of them).
# ---------------------------------------------------------------------------

_LANGUAGE_FIELD = "languages"
_FRAMEWORK_FIELDS = ("test", "integration", "e2e")

KNOWN_RULE_KINDS: Dict[str, Dict[str, Any]] = {
    "no_undeclared_language": {
        "violation": "undeclared_language",
        "allowed_fields": frozenset({_LANGUAGE_FIELD}),
    },
    "no_undeclared_test_framework": {
        "violation": "undeclared_test_framework",
        "allowed_fields": frozenset(_FRAMEWORK_FIELDS),
    },
}

_KNOWN_VIOLATION_KINDS = frozenset(k["violation"] for k in KNOWN_RULE_KINDS.values())


def _rules_invalid(path: Any, detail: str, **extra: Any) -> "ConformanceError":
    return _refuse("rules_invalid", {"path": str(path), "detail": detail, **extra})


def load_rules(path: str) -> Dict[str, Any]:
    """Load and validate program/conformance-rules.json's shape. Returns a
    plain dict with exactly `schema_version`, `rules`, `waivers` -- the
    validated form `evaluate()`/`permitted_*` consume. Raises
    ConformanceError(reason_code="rules_unreadable", exit_code=4) when the
    file cannot be found or parsed as JSON (the gate cannot see what it is
    judging), and ConformanceError(reason_code="rules_invalid", exit_code=3)
    when the file parses but its content violates the RULES schema (a
    refusal, not a visibility failure -- these two are deliberately
    different reason codes and different exit codes).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise _indeterminate("rules_unreadable", {"path": str(path), "detail": str(exc)}) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _indeterminate("rules_unreadable", {"path": str(path), "detail": str(exc)}) from exc

    if not isinstance(data, dict):
        raise _rules_invalid(path, "top-level rules document must be a JSON object")

    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise _rules_invalid(path, f"unsupported schema_version: {schema_version!r}")

    rules_list = data.get("rules")
    if not isinstance(rules_list, list) or not rules_list:
        raise _rules_invalid(path, "'rules' must be a non-empty list")

    seen_ids: set = set()
    seen_kinds: set = set()
    validated_rules: List[Dict[str, Any]] = []
    for rule in rules_list:
        if not isinstance(rule, dict):
            raise _rules_invalid(path, "each rule must be a JSON object")

        rule_id = rule.get("id")
        if not rule_id or not isinstance(rule_id, str):
            raise _rules_invalid(path, "rule is missing a string 'id'")
        if rule_id in seen_ids:
            raise _rules_invalid(path, "duplicate rule id", rule_id=rule_id)
        seen_ids.add(rule_id)

        kind = rule.get("kind")
        known = KNOWN_RULE_KINDS.get(kind)
        if known is None:
            # Never `continue` here: a rule kind this module does not
            # recognise must stop the run, not be silently dropped -- a
            # silently-skipped rule is the "sign nobody enforces" failure
            # in miniature.
            raise _rules_invalid(path, "unrecognised rule kind", rule_id=rule_id, kind=kind)
        if kind in seen_kinds:
            raise _rules_invalid(path, "duplicate rule kind", kind=kind)
        seen_kinds.add(kind)

        binding = rule.get("binding")
        if binding is not True:
            # A rule that declares itself non-binding is EXPERIENCE, not a
            # RULE. Accepting it here would let the RULES layer smuggle in
            # exactly the advisory-widens-permission failure this module
            # exists to make impossible.
            raise _rules_invalid(path, "rule must declare binding: true", rule_id=rule_id)

        violation = rule.get("violation")
        if violation != known["violation"]:
            raise _rules_invalid(
                path, f"kind {kind!r} must declare violation {known['violation']!r}",
                rule_id=rule_id)

        contract_fields = rule.get("contract_fields")
        if not isinstance(contract_fields, list) or not contract_fields:
            raise _rules_invalid(path, "'contract_fields' must be a non-empty list", rule_id=rule_id)
        if not set(contract_fields) <= known["allowed_fields"]:
            raise _rules_invalid(
                path,
                f"contract_fields for kind {kind!r} must be a subset of "
                f"{sorted(known['allowed_fields'])}",
                rule_id=rule_id)

        validated_rules.append({
            "id": rule_id, "kind": kind, "binding": True,
            "violation": violation, "contract_fields": list(contract_fields),
        })

    waivers_in = data.get("waivers")
    if waivers_in is None:
        waivers_in = []
    if not isinstance(waivers_in, list):
        raise _rules_invalid(path, "'waivers' must be a list")

    validated_waivers: List[Dict[str, Any]] = []
    for waiver in waivers_in:
        if not isinstance(waiver, dict):
            raise _rules_invalid(path, "each waiver must be a JSON object")
        adr = waiver.get("adr")
        if not adr or not isinstance(adr, str):
            # AC-8: a waiver without an ADR reference is refused outright.
            raise _rules_invalid(path, "waiver is missing an 'adr' reference", waiver=waiver)
        w_kind = waiver.get("kind")
        if w_kind not in _KNOWN_VIOLATION_KINDS:
            raise _rules_invalid(path, "waiver 'kind' is not a recognised violation kind",
                                  waiver=waiver)
        value = waiver.get("value")
        if not value or not isinstance(value, str):
            raise _rules_invalid(path, "waiver is missing a string 'value'", waiver=waiver)
        validated_waivers.append({"adr": adr, "kind": w_kind, "value": value})

    return {"schema_version": 1, "rules": validated_rules, "waivers": validated_waivers}


def rules_digest(rules: Dict[str, Any]) -> str:
    blob = json.dumps(rules, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _rules_index(rules: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {r["kind"]: r for r in rules.get("rules", [])}


def _is_waived(waivers: List[Dict[str, Any]], kind: str, value: str) -> bool:
    """A waiver permits ONLY the exact {kind, value} pair it names -- never
    the whole violation kind, and never a value it does not literally
    name (AC-8)."""
    value_cf = str(value).casefold()
    for w in waivers:
        if w["kind"] == kind and w["value"].casefold() == value_cf:
            return True
    return False


# ---------------------------------------------------------------------------
# FACTS -- reading the contract's own fields. No new detection: every value
# read here already exists on a contract `nightshift_project_contract.py`
# built.
# ---------------------------------------------------------------------------

def languages_of(field_env: Optional[Dict[str, Any]]) -> FrozenSet[str]:
    """The set of languages the `languages` field's own evidence names,
    casefolded for set algebra. `absent` (or a missing/malformed field)
    contributes nothing. On the BASE side that empty set IS the permitted
    set, so a language introduced against a never-observed base registers
    as NEW and DENIES rather than passing unnoticed -- ADR-0086's
    absent-base-field rule."""
    if not field_env:
        return frozenset()
    value = field_env.get("value")
    if not isinstance(value, dict):
        return frozenset()
    entries = value.get("all") or []
    return frozenset(
        str(e["language"]).casefold() for e in entries
        if isinstance(e, dict) and e.get("language"))


def _language_display(field_env: Optional[Dict[str, Any]], key: str) -> str:
    if field_env:
        value = field_env.get("value") or {}
        for e in value.get("all") or []:
            if isinstance(e, dict) and str(e.get("language", "")).casefold() == key:
                return str(e["language"])
    return key


def frameworks_of(field_env: Optional[Dict[str, Any]]) -> FrozenSet[str]:
    """The set of frameworks a test/integration/e2e field's evidence names.

    Reads `candidates[*].value.framework` (which survive `status: conflict`
    with their own individual evidence) UNIONED with `value.framework`
    (populated when the field resolved to `observed`/`declared`). This is
    the one load-bearing reading choice in this module: a `value`-only
    reading returns nothing for a `conflict` field (`value` is `None`
    there by construction -- see `nightshift_project_contract.conflict()`),
    which would silently ALLOW a new framework introduced exactly when the
    base could not resolve a single winner among the ones it already had.
    `absent`'s candidates are unobserved `manifest_convention` conventions
    with no evidence backing them (`validate_contract` guarantees this),
    so an absent field still safely contributes nothing here.
    """
    if not field_env:
        return frozenset()
    names = set()
    value = field_env.get("value")
    if isinstance(value, dict):
        fw = value.get("framework")
        if fw:
            names.add(str(fw).casefold())
    for cand in field_env.get("candidates") or []:
        cand_value = cand.get("value") if isinstance(cand, dict) else None
        if isinstance(cand_value, dict):
            fw = cand_value.get("framework")
            if fw:
                names.add(str(fw).casefold())
    return frozenset(names)


def _framework_evidence(field_env: Optional[Dict[str, Any]], key: str) -> Tuple[str, List[str]]:
    """(display value, evidence paths) for the framework matching casefold
    `key` inside `field_env`, preferring a candidate's own evidence
    (precise) and falling back to the field's own evidence (whatever
    survived onto the resolved value) if no candidate happens to carry it."""
    if not field_env:
        return key, []
    for cand in field_env.get("candidates") or []:
        cand_value = cand.get("value") if isinstance(cand, dict) else None
        if isinstance(cand_value, dict):
            fw = cand_value.get("framework")
            if fw and str(fw).casefold() == key:
                return str(fw), sorted(cand.get("evidence") or [])
    value = field_env.get("value")
    if isinstance(value, dict):
        fw = value.get("framework")
        if fw and str(fw).casefold() == key:
            return str(fw), sorted(field_env.get("evidence") or [])
    return key, sorted(field_env.get("evidence") or [])


# ---------------------------------------------------------------------------
# Permitted sets -- FACTS and RULES ONLY. No parameter here through which
# advisory or learned input could reach a permission computation (AC-5).
# ---------------------------------------------------------------------------

def permitted_languages(base_contract: Dict[str, Any], rules: Dict[str, Any]) -> FrozenSet[str]:
    rules_index = _rules_index(rules)
    if "no_undeclared_language" not in rules_index:
        return frozenset()
    return languages_of(base_contract.get(_LANGUAGE_FIELD))


def permitted_test_frameworks(base_contract: Dict[str, Any], rules: Dict[str, Any]) -> FrozenSet[str]:
    rules_index = _rules_index(rules)
    rule = rules_index.get("no_undeclared_test_framework")
    if rule is None:
        return frozenset()
    result: set = set()
    for field in rule["contract_fields"]:
        result |= frameworks_of(base_contract.get(field))
    return frozenset(result)


# ---------------------------------------------------------------------------
# Violation computation -- set widening between BASE and CANDIDATE, never a
# volume-of-change measure (AC-3: another file in an already-declared
# language/framework is not a violation).
# ---------------------------------------------------------------------------

def _language_violations(
        base_contract: Dict[str, Any], head_contract: Dict[str, Any],
        rules_index: Dict[str, Dict[str, Any]], waivers: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rule = rules_index.get("no_undeclared_language")
    if rule is None:
        return [], []

    base_env = base_contract.get(_LANGUAGE_FIELD)
    head_env = head_contract.get(_LANGUAGE_FIELD)

    # No `status == "absent"` bailout here: `languages_of()` already reads
    # an absent field as contributing the empty set (see its own
    # docstring), which is precisely what makes a language introduced
    # against a never-observed base register as NEW and DENY. Treating
    # `absent` as "not_applicable" instead -- skipping the diff entirely --
    # would silently ALLOW the first-ever introduction of a language nobody
    # declared, which is the exact violation this rule exists to catch.
    base_langs = languages_of(base_env)
    head_langs = languages_of(head_env)
    new_langs = sorted(head_langs - base_langs)
    if not new_langs:
        return [], []

    base_evidence = set(base_env.get("evidence") or []) if base_env else set()
    head_evidence = set(head_env.get("evidence") or []) if head_env else set()
    # Field-level delta: languages.value.all carries file/byte COUNTS, not
    # per-language paths, so a per-language evidence list cannot be derived
    # without new detection. Slightly over-inclusive (any new file under
    # any tracked extension shows up here, not only the introducing one);
    # marked `evidence_attribution: "field_delta"` so no caller mistakes
    # this for per-language precision.
    evidence_delta = sorted(head_evidence - base_evidence)

    violations = []
    for lang_key in new_langs:
        if _is_waived(waivers, "undeclared_language", lang_key):
            continue
        violations.append({
            "kind": "undeclared_language",
            "value": _language_display(head_env, lang_key),
            "rule_id": rule["id"],
            "evidence": evidence_delta,
            "evidence_attribution": "field_delta",
        })
    return violations, []


def _framework_violations(
        base_contract: Dict[str, Any], head_contract: Dict[str, Any],
        rules_index: Dict[str, Dict[str, Any]], waivers: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rule = rules_index.get("no_undeclared_test_framework")
    if rule is None:
        return [], []

    not_applicable: List[Dict[str, Any]] = []
    new_keys: set = set()
    head_envs: Dict[str, Optional[Dict[str, Any]]] = {}

    for field in rule["contract_fields"]:
        base_env = base_contract.get(field)
        head_env = head_contract.get(field)
        head_envs[field] = head_env

        # No `status == "absent"` bailout here -- same reasoning as
        # `_language_violations` above. `frameworks_of()` already reads an
        # absent field as contributing the empty set, so a framework
        # introduced against a never-observed base (e.g. `e2e` absent, head
        # adds Playwright) registers as NEW and DENIES. Skipping the field
        # as "not_applicable" instead was this module's one hole: on
        # nightshift's own contract `integration` and `e2e` are both
        # absent, so it let through the epic's own motivating example.
        base_fws = frameworks_of(base_env)
        head_fws = frameworks_of(head_env)
        new_keys |= (head_fws - base_fws)

    violations = []
    for key in sorted(new_keys):
        if _is_waived(waivers, "undeclared_test_framework", key):
            continue
        # A framework can be introduced through more than one field (e.g.
        # both `test` and `e2e` name it); aggregate evidence across every
        # field it actually appears new in, on the winning candidate's own
        # evidence (precise), not the field-level delta.
        display = key
        evidence: set = set()
        for field in rule["contract_fields"]:
            head_env = head_envs.get(field)
            if head_env is None:
                continue
            if key in frameworks_of(head_env) and key not in frameworks_of(base_contract.get(field)):
                shown, ev = _framework_evidence(head_env, key)
                display = shown
                evidence.update(ev)
        violations.append({
            "kind": "undeclared_test_framework",
            "value": display,
            "rule_id": rule["id"],
            "evidence": sorted(evidence),
            "evidence_attribution": "candidate",
        })
    return violations, not_applicable


# ---------------------------------------------------------------------------
# The decision digest -- computed BEFORE any advisory content exists, over
# exactly (verdict, violations, base contract_digest, head contract_digest,
# rules_digest). repository_id is deliberately excluded: it is the
# DEPLOYMENT identity (which environment ran this), not the CONTENT
# identity the digest exists to pin.
# ---------------------------------------------------------------------------

def _decision_digest(verdict: str, violations: List[Dict[str, Any]],
                      base_contract_digest: Any, head_contract_digest: Any,
                      rules_dig: str) -> str:
    payload = {
        "verdict": verdict, "violations": violations,
        "base_contract_digest": base_contract_digest,
        "head_contract_digest": head_contract_digest,
        "rules_digest": rules_dig,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def evaluate(*, base_contract: Dict[str, Any], head_contract: Dict[str, Any],
             rules: Dict[str, Any]) -> Dict[str, Any]:
    """The decision function. Exactly three keyword-only parameters --
    FACTS (two contracts) and RULES. There is no fourth parameter through
    which advisory or learned content could reach this computation.

    Raises ConformanceError(reason_code="vacuous_comparison", exit_code=4)
    when base and candidate share the same baseline_sha: FACTS are pinned
    to the base specifically so a candidate cannot widen what it is
    permitted to do by deleting the manifests that declared the base stack
    (AC-4) -- comparing a contract against itself would defeat that pin
    silently rather than refusing it.
    """
    base_sha = base_contract.get("baseline_sha")
    head_sha = head_contract.get("baseline_sha")
    if base_sha == head_sha:
        raise _indeterminate("vacuous_comparison", {"baseline_sha": base_sha})

    rules_index = _rules_index(rules)
    waivers = rules.get("waivers") or []

    violations: List[Dict[str, Any]] = []
    not_applicable: List[Dict[str, Any]] = []

    lang_v, lang_na = _language_violations(base_contract, head_contract, rules_index, waivers)
    violations.extend(lang_v)
    not_applicable.extend(lang_na)

    fw_v, fw_na = _framework_violations(base_contract, head_contract, rules_index, waivers)
    violations.extend(fw_v)
    not_applicable.extend(fw_na)

    verdict = "deny" if violations else "allow"
    rules_dig = rules_digest(rules)
    base_digest = base_contract.get("contract_digest")
    head_digest = head_contract.get("contract_digest")
    digest = _decision_digest(verdict, violations, base_digest, head_digest, rules_dig)

    repository_id, repository_id_source = _resolve_repository_id()

    return {
        "ok": verdict == "allow",
        "verdict": verdict,
        "reason_code": "project_contract_violation" if verdict == "deny" else None,
        "violations": violations,
        "not_applicable": not_applicable,
        "base_baseline_sha": base_sha,
        "head_baseline_sha": head_sha,
        "base_contract_digest": base_digest,
        "head_contract_digest": head_digest,
        "rules_digest": rules_dig,
        "decision_digest": digest,
        "repository_id": repository_id,
        "repository_id_source": repository_id_source,
        "advisory": [],
    }


def attach_advisory(decision: Dict[str, Any], notes: List[Any]) -> Dict[str, Any]:
    """Attach EXPERIENCE-layer notes to an already-computed decision.
    Never widens, never clears, never re-labels: the returned decision's
    `verdict`, `violations` and `decision_digest` are verified byte-
    identical to the input's before this returns -- if a future change to
    this function ever lets advisory content shift any of the three, this
    raises `AdvisoryEscalation` instead of returning a wrong answer."""
    original_digest = decision.get("decision_digest")
    original_verdict = decision.get("verdict")
    original_violations = decision.get("violations")

    updated = dict(decision)
    updated["advisory"] = [dict(n) if isinstance(n, dict) else n for n in notes]

    if updated.get("verdict") != original_verdict or updated.get("violations") != original_violations:
        raise AdvisoryEscalation(
            "attach_advisory must not alter verdict or violations")

    recomputed = _decision_digest(
        updated.get("verdict"), updated.get("violations"),
        updated.get("base_contract_digest"), updated.get("head_contract_digest"),
        updated.get("rules_digest"))
    if recomputed != original_digest or updated.get("decision_digest") != original_digest:
        raise AdvisoryEscalation(
            "attach_advisory must not alter decision_digest")

    return updated


# ---------------------------------------------------------------------------
# The claim path -- delegates to nightshift_project_contract.contradicts(),
# kept for the ticket-level ("worker proposes X+Y before any code exists")
# shape. Library-only: the gate CI actually runs is the contract-vs-contract
# path above.
# ---------------------------------------------------------------------------

def evaluate_claim(*, base_contract: Dict[str, Any], claim: Dict[str, Dict[str, Any]],
                    rules: Dict[str, Any]) -> Dict[str, Any]:
    reasons = pc.contradicts(base_contract, claim)

    unevaluated: List[str] = []
    for field, claimed_fields in claim.items():
        if not isinstance(claimed_fields, dict):
            continue
        env = base_contract.get(field)
        if not env or env.get("status") not in ("observed", "declared"):
            unevaluated.extend(f"{field}.{k}" for k in claimed_fields)
            continue
        actual_value = env.get("value") or {}
        for key in claimed_fields:
            if actual_value.get(key) is None:
                unevaluated.append(f"{field}.{key}")

    verdict = "deny" if reasons else "allow"
    repository_id, repository_id_source = _resolve_repository_id()
    return {
        "ok": verdict == "allow",
        "verdict": verdict,
        "reason_code": "project_contract_violation" if verdict == "deny" else None,
        # NOTE: these are the raw human-readable strings contradicts()
        # returns, not the structured {kind, value, evidence} shape
        # evaluate()'s violations carry -- restructuring them would be
        # re-deriving the comparison contradicts() already made, which is
        # exactly what delegation means not doing.
        "violations": reasons,
        "claim_unevaluated_fields": sorted(set(unevaluated)),
        "base_baseline_sha": base_contract.get("baseline_sha"),
        "base_contract_digest": base_contract.get("contract_digest"),
        "rules_digest": rules_digest(rules),
        "repository_id": repository_id,
        "repository_id_source": repository_id_source,
        "advisory": [],
    }


# ---------------------------------------------------------------------------
# Contract acquisition -- compile fresh from a repo at an exact SHA (the CI
# path), or load a digest-verified contract file (AC-10: a later slice can
# hand this gate a stored contract without an interface change; #400 owns
# where contracts eventually live -- this module always verifies, never
# trusts a file's own claimed digest).
# ---------------------------------------------------------------------------

def _compile_contract(repo_path: str, sha: str) -> Dict[str, Any]:
    try:
        return pc.build_contract(repo_path, sha=sha)
    except pc.ShaNotPresent as exc:
        raise _indeterminate("sha_not_present", {"sha": sha}) from exc
    except pc.ContractInvalid as exc:
        raise _indeterminate("contract_invalid", {"sha": sha}) from exc


def _verify_contract_sha(contract: Dict[str, Any], expected_sha: str, path: str) -> None:
    """A digest-valid contract file supplied for the wrong SHA is real, but
    it is not an answer to the question this run was asked -- refuse rather
    than silently compare against whatever it happens to describe."""
    actual = contract.get("baseline_sha")
    if actual != expected_sha:
        raise _indeterminate("contract_invalid", {
            "path": str(path), "expected_baseline_sha": expected_sha,
            "actual_baseline_sha": actual,
        })


def load_contract_file(path: str) -> Dict[str, Any]:
    """Load a previously-compiled contract from disk and verify its own
    `contract_digest` recomputes over its own canonical bytes before it is
    trusted (AC-10) -- a file this module cannot even read or parse is
    exactly as unusable as one whose digest does not check out, so both
    raise the same `contract_invalid` reason (not `rules_unreadable`,
    which names a different file -- the RULES file, not a contract)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            contract = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise _indeterminate("contract_invalid", {"path": str(path), "detail": str(exc)}) from exc

    claimed = contract.get("contract_digest") if isinstance(contract, dict) else None
    recomputed = None
    if isinstance(contract, dict):
        recomputed = "sha256:" + hashlib.sha256(pc.canonical_bytes(contract)).hexdigest()
    if not claimed or claimed != recomputed:
        raise _indeterminate("contract_digest_mismatch", {"path": str(path)})
    return contract


def build_decision(repo_path: str, *, base_sha: str, head_sha: str,
                    rules: Dict[str, Any]) -> Dict[str, Any]:
    """Compile the base and candidate contracts fresh, at their own exact
    SHAs, and evaluate. FACTS are pinned to the base SHA specifically --
    the base contract is never compiled at head_sha (AC-4)."""
    base_contract = _compile_contract(repo_path, base_sha)
    head_contract = _compile_contract(repo_path, head_sha)
    return evaluate(base_contract=base_contract, head_contract=head_contract, rules=rules)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_INDETERMINATE_REASONS = frozenset({
    "sha_not_present", "contract_invalid", "rules_unreadable",
    "vacuous_comparison", "contract_digest_mismatch",
})


def _verdict_for_reason(reason_code: str) -> Optional[str]:
    if reason_code == "rules_invalid":
        return "rules_invalid"
    if reason_code in _INDETERMINATE_REASONS:
        return "indeterminate"
    return None


def _render_human(decision: Dict[str, Any]) -> str:
    lines = [str(decision.get("verdict") or "unknown").upper()]
    reason_code = decision.get("reason_code")
    if reason_code:
        lines.append(f"reason: {reason_code}")
    violations = decision.get("violations") or []
    if violations:
        lines.append("violations:")
        for v in violations:
            if isinstance(v, dict):
                lines.append(f"  - {v.get('kind')}: {v.get('value')}")
            else:
                lines.append(f"  - {v}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nightshift_conformance.py",
        description="The conformance gate (#442): denies a candidate commit whose "
                     "compiled project contract widens the declared stack beyond what "
                     "the base contract and the frozen RULES file permit. FACTS and "
                     "RULES bind; nothing else does.")
    parser.add_argument("repo_path", help="path to the repository root to inspect")
    parser.add_argument("--base-sha", required=True,
                         help="exact commit SHA the candidate is proposed against")
    parser.add_argument("--head-sha", required=True,
                         help="exact commit SHA of the candidate")
    parser.add_argument("--rules", required=True,
                         help="path to the RULES file (program/conformance-rules.json)")
    parser.add_argument("--contract-base",
                         help="digest-verified base contract JSON file, used in place "
                              "of compiling repo_path at --base-sha")
    parser.add_argument("--contract-head",
                         help="digest-verified candidate contract JSON file, used in "
                              "place of compiling repo_path at --head-sha")
    parser.add_argument("--compact", action="store_true",
                         help="emit compact JSON (default: pretty, indent=2)")
    args = parser.parse_args(argv)

    def emit(obj: Dict[str, Any], code: int) -> int:
        if args.compact:
            print(json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
        else:
            print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True))
        return code

    try:
        rules = load_rules(args.rules)

        if args.contract_base or args.contract_head:
            if args.contract_base:
                base_contract = load_contract_file(args.contract_base)
                _verify_contract_sha(base_contract, args.base_sha, args.contract_base)
            else:
                base_contract = _compile_contract(args.repo_path, args.base_sha)
            if args.contract_head:
                head_contract = load_contract_file(args.contract_head)
                _verify_contract_sha(head_contract, args.head_sha, args.contract_head)
            else:
                head_contract = _compile_contract(args.repo_path, args.head_sha)
            decision = evaluate(base_contract=base_contract, head_contract=head_contract,
                                 rules=rules)
        else:
            decision = build_decision(args.repo_path, base_sha=args.base_sha,
                                       head_sha=args.head_sha, rules=rules)
    except ConformanceError as exc:
        repository_id, repository_id_source = _resolve_repository_id()
        payload = {
            "verdict": _verdict_for_reason(exc.reason_code), "violations": [],
            "repository_id": repository_id, "repository_id_source": repository_id_source,
            **error_payload(exc),
        }
        print(_render_human(payload), file=sys.stderr)
        return emit(payload, exc.exit_code)
    except NotADirectoryError:
        # Report the path as the caller gave it, never the exception's own
        # resolved absolute path -- no local machine path in the payload.
        payload = {"ok": False, "reason_code": "usage_error", "verdict": None,
                    "violations": [], "repo_path": args.repo_path}
        return emit(payload, 2)
    except Exception:  # noqa: BLE001 -- last resort; never leak a traceback here.
        payload = {"ok": False, "reason_code": "conformance_operation_failed",
                   "verdict": None, "violations": []}
        return emit(payload, 1)

    print(_render_human(decision), file=sys.stderr)
    return emit(decision, 0 if decision["verdict"] == "allow" else 3)


if __name__ == "__main__":
    sys.exit(main())
