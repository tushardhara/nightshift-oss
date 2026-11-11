#!/usr/bin/env python3
"""nightshift_observability.py -- #400 acceptance condition 21, verbatim:

    "Operations expose volume, latency, outbox age, retention/deletion
    backlog, export duration, publication queue time, isolation failures,
    and restore evidence."

WHAT THIS IS
============
A read-only collector, `collect(state_root, tenant_id, repository_id)`,
that reports exactly those eight named signals for one tenant/repository
scope. Nothing here writes to any store.

THE CLOSED SET, AND WHY A MISSING SIGNAL RAISES INSTEAD OF RETURNING NULL
==========================================================================
Of the eight, four are computable today from rows that already exist:

  volume                     row counts across publication.db + provenance.db
  latency                    publication_attempts.applied_at - started_at
  publication_queue_time     publication_attempts.started_at - publication_stage.staged_at
  retention_deletion_backlog source_snapshot retention_state counts +
                              tenant.status='deleting' count

Four are not, because the mechanism they would report on does not exist in
this repository at the commit this module was built against:

  outbox_age            no outbox table exists in any schema (`grep -i
                         outbox bin/*.py` names only this file and ADR-0076's
                         prose, which describes an outbox as future design,
                         not a built mechanism)
  isolation_failures     cross-tenant refusals are DELIBERATELY designed to
                         leave no row (see tests asserting "leaves no
                         attempt row and no commit" against the publication
                         gateway) -- there is nothing to count
  restore_evidence       no backup-restore drill is recorded anywhere;
                         `nightshift_tenant_keys`'s 35-day backup-expiry
                         receipt proves a WINDOW, not that a restore was
                         ever exercised
  export_duration        MEASURED once `nightshift_export.py` has produced
                         at least one receipt for this tenant/repository;
                         REFUSED (`export_has_no_receipt`) until then

A signal this module cannot back with a real row RAISES `ObservabilityError`
with a typed `reason_code` -- it never silently returns `None` or `null`.
The distinction matters operationally: a dashboard full of nulls reads as
"healthy, nothing to report"; a dashboard full of typed refusals reads as
"this is not built yet", which is the true state. The day someone adds an
outbox table, the `outbox_age` branch stops raising -- and any code (or
test) that hardcoded the CURRENT refusal set breaks loudly, which is the
intended, forcing failure, not a bug.

THIS SET IS CLOSED. Do not add a ninth signal (a repository-scope or
ownership signal, for instance) -- AC 21 names exactly eight, and
`REQUIRED_SIGNALS` below is deliberately exhaustive and exact, not a
lower bound.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import nightshift_provenance as prov
import nightshift_publication as pub
import nightshift_tenant as tenant

try:
    import nightshift_authority as auth
except ImportError:                                       # pragma: no cover
    auth = None


REQUIRED_SIGNALS = (
    "volume",
    "latency",
    "outbox_age",
    "retention_deletion_backlog",
    "export_duration",
    "publication_queue_time",
    "isolation_failures",
    "restore_evidence",
)


class ObservabilityError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(exc):
    payload = {"ok": False, "reason_code": exc.reason_code}
    if exc.details:
        payload["details"] = exc.details
    return payload


def _refuse(reason_code, details=None):
    """Exit 3: the signal cannot be measured -- a well-formed request this
    collector declines to fabricate an answer for."""
    raise ObservabilityError(reason_code, 3, details)


def _usage_error(reason_code, details=None):
    raise ObservabilityError(reason_code, 2, details)


def _operation_failed(reason_code="observability_operation_failed", details=None):
    raise ObservabilityError(reason_code, 1, details)


def _validate_ids(tenant_id, repository_id):
    if auth is not None:
        try:
            auth.validate_repository_id(repository_id)
        except auth.AuthorityError as exc:
            raise ObservabilityError(exc.reason_code, exc.exit_code, exc.details) from exc
    else:                                                  # pragma: no cover
        prov.validate_repository_id(repository_id)
    try:
        prov.validate_tenant_id(tenant_id)
    except prov.ProvenanceError as exc:
        raise ObservabilityError(exc.reason_code, exc.exit_code, exc.details) from exc


def _percentiles(values):
    """count/p50/p95/max over a sorted-ascending sequence of non-negative
    durations. Returns nulls only inside a MEASURED value when there is
    genuinely no data yet -- that is an honest "zero observations", not a
    disguised refusal."""
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    n = len(ordered)

    def _pct(p):
        idx = min(n - 1, int(round(p * (n - 1))))
        return ordered[idx]

    return {"count": n, "p50": _pct(0.50), "p95": _pct(0.95), "max": ordered[-1]}


# ---------------------------------------------------------------------------
# Signal functions. Each either returns a JSON-serialisable value (measured)
# or raises ObservabilityError (refused). `collect()` is the single place
# that turns either outcome into the report's per-signal shape.
#
# A store that has never been written to (no publication.db / provenance.db
# file yet for this tenant/repository) is a REAL, MEASURED zero -- not a
# refusal, and not created on the fly just to read it: this collector opens
# an existing file read-only and never calls a sibling module's connect()
# with create=True, which is what makes "nothing here writes to any store"
# in the module docstring literally true rather than aspirational.
# ---------------------------------------------------------------------------

def _pub_connect_ro(pub_root):
    path = os.path.join(pub_root, pub.PUBLICATION_DB_FILENAME)
    if not os.path.isfile(path):
        return None
    return pub.connect(pub_root, create=False)


def _prov_connect_ro(state_root, tenant_id, repository_id):
    root = prov.resolve_publication_root(state_root, tenant_id, repository_id)
    path = os.path.join(root, prov.PROVENANCE_DB_FILENAME)
    if not os.path.isfile(path):
        return None
    return prov.connect(state_root, tenant_id, repository_id, create=False)


def _signal_volume(state_root, tenant_id, repository_id):
    counts = {}
    pub_root = pub.resolve_publication_root(state_root, tenant_id, repository_id)
    pub_tables = ("publication_policy", "publication_reviewer", "publication_stage",
                  "publication_review", "publication_authorizations",
                  "publication_attempts", "publication_lineage")
    pub_cx = _pub_connect_ro(pub_root)
    if pub_cx is None:
        counts.update({t: 0 for t in pub_tables})
    else:
        try:
            for table in pub_tables:
                counts[table] = pub_cx.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            pub_cx.close()
    prov_tables = ("source_snapshot", "closure_entry", "lineage_node", "derived_from")
    prov_cx = _prov_connect_ro(state_root, tenant_id, repository_id)
    if prov_cx is None:
        counts.update({t: 0 for t in prov_tables})
    else:
        try:
            for table in prov_tables:
                counts[table] = prov_cx.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            prov_cx.close()
    return counts


def _signal_latency(state_root, tenant_id, repository_id):
    pub_root = pub.resolve_publication_root(state_root, tenant_id, repository_id)
    cx = _pub_connect_ro(pub_root)
    if cx is None:
        return _percentiles([])
    try:
        rows = cx.execute(
            "SELECT started_at, applied_at FROM publication_attempts "
            "WHERE applied_at IS NOT NULL").fetchall()
    finally:
        cx.close()
    return _percentiles([r["applied_at"] - r["started_at"] for r in rows])


def _signal_publication_queue_time(state_root, tenant_id, repository_id):
    pub_root = pub.resolve_publication_root(state_root, tenant_id, repository_id)
    cx = _pub_connect_ro(pub_root)
    if cx is None:
        return _percentiles([])
    try:
        rows = cx.execute(
            "SELECT a.started_at AS started_at, s.staged_at AS staged_at "
            "FROM publication_attempts a JOIN publication_stage s ON s.id = a.stage_id"
        ).fetchall()
    finally:
        cx.close()
    return _percentiles([r["started_at"] - r["staged_at"] for r in rows])


def _signal_retention_deletion_backlog(state_root, tenant_id, repository_id):
    prov_cx = _prov_connect_ro(state_root, tenant_id, repository_id)
    if prov_cx is None:
        retained, expired = 0, 0
    else:
        try:
            retained = prov_cx.execute(
                "SELECT COUNT(*) FROM source_snapshot WHERE retention_state='retained'").fetchone()[0]
            expired = prov_cx.execute(
                "SELECT COUNT(*) FROM source_snapshot WHERE retention_state='expired'").fetchone()[0]
        finally:
            prov_cx.close()
    tenants_deleting = None
    registry_path = tenant.registry_path(state_root)
    if os.path.isfile(registry_path):
        tenant_cx = tenant.connect(state_root, create=False)
        try:
            tenants_deleting = tenant_cx.execute(
                "SELECT COUNT(*) FROM tenant WHERE status='deleting'").fetchone()[0]
        finally:
            tenant_cx.close()
    return {
        "source_snapshot_retained": retained,
        "source_snapshot_expired": expired,
        "tenants_status_deleting": tenants_deleting,
    }


def _signal_export_duration(state_root, tenant_id, repository_id):
    try:
        import nightshift_export as exp
    except ImportError as exc:                             # pragma: no cover
        _refuse("export_module_unavailable", {"error": str(exc)})
    receipts = exp.list_export_receipts(state_root, tenant_id, repository_id)
    if not receipts:
        _refuse("export_has_no_receipt", {
            "reason": "no tenant export has been produced for this tenant/repository yet"})
    return _percentiles([r["duration_seconds"] for r in receipts])


def _signal_outbox_age(state_root, tenant_id, repository_id):
    _refuse("no_outbox_table", {
        "reason": "no outbox table exists in any Nightshift schema; ADR-0076 "
                  "describes an outbox as target design, not a built mechanism"})


def _signal_isolation_failures(state_root, tenant_id, repository_id):
    _refuse("isolation_refusals_not_persisted", {
        "reason": "cross-tenant refusals are deliberately designed to leave no "
                  "attempt row and no commit -- there is nothing to count "
                  "without a new append-only refusal-audit table, which is a "
                  "design change outside this collector's scope"})


def _signal_restore_evidence(state_root, tenant_id, repository_id):
    _refuse("no_restore_drill_recorded", {
        "reason": "no backup-restore drill is recorded anywhere in this repository; "
                  "the tenant-key deletion receipt proves a maximum backup-expiry "
                  "WINDOW, not that a restore was ever exercised"})


_SIGNAL_FUNCS = {
    "volume": _signal_volume,
    "latency": _signal_latency,
    "outbox_age": _signal_outbox_age,
    "retention_deletion_backlog": _signal_retention_deletion_backlog,
    "export_duration": _signal_export_duration,
    "publication_queue_time": _signal_publication_queue_time,
    "isolation_failures": _signal_isolation_failures,
    "restore_evidence": _signal_restore_evidence,
}

assert set(_SIGNAL_FUNCS) == set(REQUIRED_SIGNALS)


def collect(state_root, tenant_id, repository_id):
    """The eight-signal report. Every key in REQUIRED_SIGNALS is present in
    the result, always, with `status` either "measured" (a real `value`,
    itself never a bare None at the top level) or "refused" (a `reason_code`
    and `message`, never a partial/None value).

    A signal function may raise `ObservabilityError` (a deliberate refusal --
    see the module docstring) OR a sibling module's own error
    (`nightshift_publication.PublicationError`, `nightshift_provenance.
    ProvenanceError`) if, for example, `state_root` itself is malformed.
    Both are converted here into the SAME per-signal refused shape -- a
    caller of `collect()` never sees a raw sibling exception leak through
    and never gets fewer than eight keys back."""
    _validate_ids(tenant_id, repository_id)
    report = {}
    for name in REQUIRED_SIGNALS:
        fn = _SIGNAL_FUNCS[name]
        try:
            value = fn(state_root, tenant_id, repository_id)
        except (ObservabilityError, pub.PublicationError, prov.ProvenanceError) as exc:
            report[name] = {
                "status": "refused",
                "reason_code": exc.reason_code,
                "message": exc.details.get("reason", exc.reason_code),
            }
        else:
            report[name] = {"status": "measured", "value": value}
    assert set(report) == set(REQUIRED_SIGNALS)
    return report


# ---------------------------------------------------------------------------
# CLI -- mirrors nightshift_publication.py's own build_parser()/main()
# precedent so bin/nightshift-ctl is never touched for this.
# ---------------------------------------------------------------------------

def out(obj, code=0):
    print(json.dumps(obj, sort_keys=True))
    sys.exit(code)


def cmd_report(args):
    return {"ok": True, "signals": collect(args.state_root, args.tenant_id, args.repository_id)}


def build_parser():
    p = argparse.ArgumentParser(prog="nightshift_observability")
    sub = p.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="collect the eight AC-21 signals for one tenant/repository")
    report.add_argument("--state-root", required=True)
    report.add_argument("--tenant-id", required=True, type=int)
    report.add_argument("--repository-id", required=True, type=int)
    report.set_defaults(func=cmd_report)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except ObservabilityError as exc:
        out(error_payload(exc), exc.exit_code)
    except Exception:
        out({"ok": False, "reason_code": "observability_operation_failed"}, 1)
    out(result)


if __name__ == "__main__":
    main()
