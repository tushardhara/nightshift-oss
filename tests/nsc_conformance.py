"""NSC-v1 -- the adapter-agnostic authority traces, and the honest sliver of
#400 item 7 (managed-production PostgreSQL parity).

WHAT THIS FILE IS FOR
=====================
`tests/test_publication_gateway.py` and `tests/test_provenance.py` each
prove authority properties -- fencing, idempotent replay, concurrent
racers, crash recovery, tenant isolation, typed refusal -- but they prove
them ABOUT SQLite, inline, tangled up with git fixtures and publication
policy. Those six properties are not facts about SQLite. They are the
contract any storage backend has to satisfy before it can be an authority,
and #400 item 7 is exactly the question "does a second backend satisfy
them?".

So the six traces are lifted out here, expressed against a tiny adapter
protocol, and `run_traces(adapter_factory)` runs all of them against
anything that implements it.

WHAT THIS FILE DELIBERATELY DOES NOT CONTAIN
============================================
A PostgreSQL adapter. #400 item 7 is scoped OUT, and this is the recorded
refusal rather than a fake proof.

EVERY CLAIM BELOW WAS RE-MEASURED ON 2026-09-04, AND TWO OF THE FOUR
BLOCKERS THIS DECISION WAS ORIGINALLY WRITTEN ON TURNED OUT TO BE FALSE.
They are struck rather than quietly carried, because a refusal resting on
a stale measurement is the same species of unearned confidence as a fake
proof -- it just points the other way.

STRUCK (measured today, and NOT true):
  * "docker daemon unavailable, so there is no container fallback."
    `docker info` reports Docker Desktop, server 29.7.2, running.
  * "pgvector's vector.control is absent."
    `CREATE EXTENSION vector` SUCCEEDS against the local PostgreSQL 16.14.
    (`.../postgresql@16/share/postgresql@16/extension/vector.control` is
    indeed missing from that path, but the extension resolves elsewhere and
    installs -- so the conclusion drawn from that missing file was wrong.)

STANDING (measured today, and decisive):

  1. No driver, and CI forbids adding one.
     `import psycopg` / `psycopg2` / `pg8000` / `asyncpg` -> every one
     ModuleNotFoundError. `python3 -m pip list` shows 6 packages -- meson,
     pip, pypdf, repo-coach, setuptools, wheel -- and no database driver.
     `.github/workflows/tests.yml` states in its own header: "stdlib-only
     by design — no pip install step exists on purpose."
     THIS IS THE DECISIVE ONE. An adapter written here could never be
     exercised by the gate that decides green, so it would be a module CI
     never runs -- an unverified second authority backend, which is
     strictly worse than an absent one.

  2. Infrastructure the claims need does not exist here at any price.
     #400 conditions 16, 17 and 21 want HA failover, encrypted object
     storage, and restore evidence against a declared 35-day backup-expiry
     window. A single-node local instance cannot be evidence for "one HA
     PostgreSQL authority" no matter how well it passes a trace.

AND ONE HAZARD, which is not an availability blocker but shapes how any
future parity suite MUST be written -- reproduced here on 2026-09-04:

  3. The superuser false-green.
     Against the local PostgreSQL 16.14, as the TABLE OWNER, with
     `ALTER TABLE t FORCE ROW LEVEL SECURITY` and a
     `current_setting('app.tenant')` policy in place, the isolation trace's
     `SELECT count(*)` returned **2 of 2 rows** -- RLS silently bypassed,
     because `pg_roles` reports `rolsuper=t, rolbypassrls=t` for that role.
     The identical query as a freshly created `NOSUPERUSER LOGIN` role
     returned **1 of 2**, and with the tenant GUC unset it failed closed:
     `ERROR: unrecognized configuration parameter "app.tenant"`.
     A parity suite written without a non-superuser role and an explicit
     `rolbypassrls = false` assertion would report cross-tenant isolation
     PROVEN while proving nothing.

`require_adapter("postgres")` therefore RAISES, carrying all of the above
in its details. It is not `unittest.skip`. A skip is a silent green -- the
suite reports OK and a reader concludes the property holds. A raised,
asserted-upon refusal makes the absence of the proof itself the thing that
is proven, which is the only honest shape available here.

THE ADAPTER PROTOCOL
====================
Six callables. Deliberately tiny -- anything larger starts encoding
SQLite's idea of a transaction into what is supposed to be a
backend-neutral contract.

    open()                      -> handle
    close(handle)               -> None
    reopen(handle)              -> handle      (durability across restart)
    put(handle, tenant, key, value, expected_fence) -> bool
    get(handle, tenant, key)    -> (value, fence) or None
    describe()                  -> dict        (name, and how it isolates)
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile

TRACE_NAMES = ("fencing", "idempotency", "concurrency", "recovery",
               "isolation", "refusal")


class ConformanceError(Exception):
    def __init__(self, reason_code, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = dict(details or {})


class TraceFailure(AssertionError):
    def __init__(self, trace, message, details=None):
        super().__init__(f"[{trace}] {message}")
        self.trace = trace
        self.details = dict(details or {})


def _require(trace, condition, message, details=None):
    if not condition:
        raise TraceFailure(trace, message, details)


# ===========================================================================
# The six traces
# ===========================================================================

def trace_fencing(adapter):
    """A write carrying a stale fence loses, and the winner's value stands.

    This is the property `nightshift_publication.consume_publication_
    authorization` and `nightshift_authority.switch_authority` both rest on:
    a conditional UPDATE whose WHERE clause names the value it expects to
    replace. A backend that cannot express that has no way to be an
    authority.
    """
    h = adapter["open"]()
    try:
        _require("fencing", adapter["put"](h, 1, "k", b"v1", None),
                 "initial create must succeed")
        got = adapter["get"](h, 1, "k")
        _require("fencing", got is not None and got[0] == b"v1",
                 "value must read back", {"got": got})
        fence = got[1]
        _require("fencing", adapter["put"](h, 1, "k", b"v2", fence),
                 "write at the current fence must succeed")
        _require("fencing", not adapter["put"](h, 1, "k", b"v3", fence),
                 "write at a STALE fence must be refused")
        got = adapter["get"](h, 1, "k")
        _require("fencing", got[0] == b"v2",
                 "the refused write must not have landed", {"got": got})
        return {"trace": "fencing", "ok": True, "final_fence": got[1]}
    finally:
        adapter["close"](h)


def trace_idempotency(adapter):
    """Replaying an identical write at an already-consumed fence is refused
    and changes nothing -- not "succeeds again harmlessly". The distinction
    matters: a replay that silently succeeds would bump the fence, and the
    ORIGINAL writer's next fenced write would then fail for no reason the
    operator can see."""
    h = adapter["open"]()
    try:
        adapter["put"](h, 1, "k", b"v1", None)
        fence0 = adapter["get"](h, 1, "k")[1]
        adapter["put"](h, 1, "k", b"v2", fence0)
        after_first = adapter["get"](h, 1, "k")
        _require("idempotency", not adapter["put"](h, 1, "k", b"v2", fence0),
                 "replaying the same write at the same fence must be refused")
        after_replay = adapter["get"](h, 1, "k")
        _require("idempotency", after_first == after_replay,
                 "a refused replay must leave value AND fence untouched",
                 {"before": after_first, "after": after_replay})
        return {"trace": "idempotency", "ok": True}
    finally:
        adapter["close"](h)


def trace_concurrency(adapter):
    """Two writers race the same fence from two independent handles.
    EXACTLY one wins. Not "usually one" -- the losing writer must be told it
    lost, because a writer that believes it won and did not is the lost
    update every fenced CAS in this repository exists to prevent."""
    h1 = adapter["open"]()
    h2 = adapter["open"]()
    try:
        adapter["put"](h1, 1, "k", b"v1", None)
        fence = adapter["get"](h1, 1, "k")[1]
        first = adapter["put"](h1, 1, "k", b"a", fence)
        second = adapter["put"](h2, 1, "k", b"b", fence)
        _require("concurrency", [first, second].count(True) == 1,
                 "exactly one of two writers at the same fence must win",
                 {"first": first, "second": second})
        winner = b"a" if first else b"b"
        got = adapter["get"](h1, 1, "k")
        _require("concurrency", got[0] == winner,
                 "the stored value must be the winner's",
                 {"stored": got[0], "expected": winner})
        return {"trace": "concurrency", "ok": True, "winner": winner.decode()}
    finally:
        adapter["close"](h1)
        adapter["close"](h2)


def trace_recovery(adapter):
    """Committed state survives a close/reopen cycle, and the fence survives
    with it. A backend that resets its fence on restart would let every
    stale client's write suddenly succeed after a bounce."""
    h = adapter["open"]()
    adapter["put"](h, 1, "k", b"durable", None)
    fence = adapter["get"](h, 1, "k")[1]
    h = adapter["reopen"](h)
    try:
        got = adapter["get"](h, 1, "k")
        _require("recovery", got is not None and got[0] == b"durable",
                 "committed value must survive reopen", {"got": got})
        _require("recovery", got[1] == fence,
                 "the fence must survive reopen too",
                 {"before": fence, "after": got[1]})
        _require("recovery", not adapter["put"](h, 1, "k", b"x", fence - 1
                                                 if isinstance(fence, int) else None),
                 "a pre-restart stale fence must still lose after restart")
        return {"trace": "recovery", "ok": True}
    finally:
        adapter["close"](h)


def trace_isolation(adapter):
    """Tenant B cannot read tenant A's key, and writing the same key under B
    does not disturb A's value. ADR-0080: "Plaintext content hashes
    deduplicate only inside one tenant; cross-tenant dedupe is forbidden" --
    identical keys under two tenants are two rows, never one."""
    h = adapter["open"]()
    try:
        adapter["put"](h, 1, "shared-key", b"tenant-one", None)
        _require("isolation", adapter["get"](h, 2, "shared-key") is None,
                 "tenant 2 must not see tenant 1's key")
        _require("isolation", adapter["put"](h, 2, "shared-key", b"tenant-two", None),
                 "tenant 2 must be able to create the same key independently")
        a = adapter["get"](h, 1, "shared-key")
        b = adapter["get"](h, 2, "shared-key")
        _require("isolation", a[0] == b"tenant-one" and b[0] == b"tenant-two",
                 "identical keys under two tenants must be two rows",
                 {"tenant1": a, "tenant2": b})
        return {"trace": "isolation", "ok": True}
    finally:
        adapter["close"](h)


def trace_refusal(adapter):
    """Refusals are typed and fail CLOSED. An unknown key answers None, not
    a guess; an invalid tenant raises rather than resolving to some default
    scope."""
    h = adapter["open"]()
    try:
        _require("refusal", adapter["get"](h, 1, "never-written") is None,
                 "an unknown key must answer None, not a fabricated row")
        for bad in (0, -1, "1", True, None):
            try:
                adapter["get"](h, bad, "k")
            except Exception:
                continue
            raise TraceFailure("refusal",
                               f"invalid tenant {bad!r} was accepted",
                               {"tenant": repr(bad)})
        return {"trace": "refusal", "ok": True}
    finally:
        adapter["close"](h)


TRACES = {
    "fencing": trace_fencing,
    "idempotency": trace_idempotency,
    "concurrency": trace_concurrency,
    "recovery": trace_recovery,
    "isolation": trace_isolation,
    "refusal": trace_refusal,
}


def run_traces(adapter_factory, *, names=TRACE_NAMES):
    """Run every named trace against one adapter. Returns a list of result
    dicts; raises `TraceFailure` on the first trace that fails."""
    results = []
    for name in names:
        adapter = adapter_factory()
        try:
            results.append(TRACES[name](adapter))
        finally:
            teardown = adapter.get("teardown")
            if teardown is not None:
                teardown()
    return results


# ===========================================================================
# The one adapter that exists on this machine
# ===========================================================================

_SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS cell(
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  key TEXT NOT NULL,
  value BLOB NOT NULL,
  fence INTEGER NOT NULL,
  PRIMARY KEY(tenant_id, key));
"""


def _validate_tenant(tenant):
    if (isinstance(tenant, bool) or not isinstance(tenant, int) or tenant <= 0):
        raise ConformanceError("invalid_tenant_id", {"tenant": repr(tenant)})
    return tenant


def sqlite_adapter_factory():
    """A REAL SQLite adapter over a REAL file in a REAL tempdir. No mocks,
    no in-memory shortcut: `:memory:` would make the recovery trace
    vacuous, because there is nothing to reopen."""
    directory = tempfile.mkdtemp(prefix="nsc-sqlite-")
    path = os.path.join(directory, "nsc.db")

    def _open():
        cx = sqlite3.connect(path, timeout=10, isolation_level=None)
        cx.execute("PRAGMA busy_timeout=10000")
        cx.executescript(_SQLITE_SCHEMA)
        return cx

    def _close(cx):
        cx.close()

    def _reopen(cx):
        cx.close()
        return _open()

    def _put(cx, tenant, key, value, expected_fence):
        _validate_tenant(tenant)
        if expected_fence is None:
            try:
                cx.execute("INSERT INTO cell(tenant_id, key, value, fence) "
                           "VALUES(?,?,?,1)", (tenant, key, sqlite3.Binary(value)))
                return True
            except sqlite3.IntegrityError:
                return False
        cur = cx.execute(
            "UPDATE cell SET value=?, fence=fence+1 "
            "WHERE tenant_id=? AND key=? AND fence=?",
            (sqlite3.Binary(value), tenant, key, expected_fence))
        return cur.rowcount == 1

    def _get(cx, tenant, key):
        _validate_tenant(tenant)
        row = cx.execute(
            "SELECT value, fence FROM cell WHERE tenant_id=? AND key=?",
            (tenant, key)).fetchone()
        return None if row is None else (bytes(row[0]), int(row[1]))

    return {
        "open": _open, "close": _close, "reopen": _reopen,
        "put": _put, "get": _get,
        "describe": lambda: {
            "name": "sqlite",
            "isolation": "primary-key scoping on tenant_id, plus a "
                         "typeof()='integer' CHECK; the Python validator "
                         "refuses a bool/str/non-positive id before it can "
                         "reach a query",
            "path": path},
        "teardown": lambda: shutil.rmtree(directory, ignore_errors=True),
    }


#: The blockers, each RE-MEASURED on 2026-09-04 (see the module docstring),
#: carried in the refusal so a reader never has to take "unavailable" on
#: trust -- and so a reader can check whether they are still true.
POSTGRES_BLOCKERS = [
    {"blocker": "no_driver_and_ci_forbids_one",
     "status": "standing",
     "measured_on": "2026-09-04",
     "decisive": True,
     "evidence": "import psycopg / psycopg2 / pg8000 / asyncpg -> "
                 "ModuleNotFoundError for all four; python3 -m pip list shows "
                 "6 packages (meson, pip, pypdf, repo-coach, setuptools, "
                 "wheel) and no database driver; "
                 ".github/workflows/tests.yml: 'stdlib-only by design - no "
                 "pip install step exists on purpose'. An adapter CI can "
                 "never run is an unverified second authority backend, which "
                 "is worse than an absent one."},
    {"blocker": "ha_backup_and_restore_claims_have_no_infrastructure",
     "status": "standing",
     "measured_on": "2026-09-04",
     "decisive": True,
     "evidence": "#400 conditions 16, 17 and 21 require HA failover, "
                 "encrypted object storage and restore evidence against a "
                 "declared 35-day backup-expiry window. A single-node local "
                 "instance is not evidence for 'one HA PostgreSQL "
                 "authority' however well it passes a trace."},
    {"blocker": "superuser_rls_false_green",
     "status": "hazard",
     "measured_on": "2026-09-04",
     "decisive": False,
     "evidence": "Reproduced on local PostgreSQL 16.14: as the table owner "
                 "(pg_roles rolsuper=t, rolbypassrls=t) with FORCE ROW LEVEL "
                 "SECURITY and a current_setting('app.tenant') policy, "
                 "SELECT count(*) returned 2 of 2 -- RLS bypassed. The same "
                 "query as a fresh NOSUPERUSER LOGIN role returned 1 of 2; "
                 "with the GUC unset it failed closed with 'unrecognized "
                 "configuration parameter \"app.tenant\"'. Not an "
                 "availability blocker -- a design constraint on any future "
                 "parity suite: it MUST use a non-superuser role and assert "
                 "rolbypassrls = false, or it proves nothing."},
]

#: Claims this refusal was ORIGINALLY written on that turned out to be
#: false when re-measured. Recorded, not deleted: a struck blocker is
#: evidence about how carefully the standing ones should be read.
POSTGRES_BLOCKERS_STRUCK = [
    {"blocker": "docker_daemon_unavailable",
     "status": "struck",
     "measured_on": "2026-09-04",
     "evidence": "FALSE: `docker info` reports Docker Desktop, server "
                 "29.7.2, running. A container fallback does exist."},
    {"blocker": "pgvector_absent",
     "status": "struck",
     "measured_on": "2026-09-04",
     "evidence": "FALSE: `CREATE EXTENSION vector` SUCCEEDS against the "
                 "local PostgreSQL 16.14. The file "
                 "postgresql@16/share/postgresql@16/extension/vector.control "
                 "is indeed absent from that path, but the extension "
                 "resolves elsewhere and installs, so the conclusion drawn "
                 "from the missing file was wrong."},
]


def available_adapters():
    """Adapters that can actually be RUN here. PostgreSQL is absent by
    decision, not by oversight -- see `require_adapter`."""
    return {"sqlite": sqlite_adapter_factory}


def require_adapter(name):
    """Return the factory for `name`, or RAISE.

    Never `unittest.skip`. A skip prints OK and lets a reader conclude the
    property holds; a raised refusal that a test asserts on makes the
    ABSENCE of the proof the thing that is proven.
    """
    adapters = available_adapters()
    if name in adapters:
        return adapters[name]
    if name == "postgres":
        raise ConformanceError("postgres_adapter_unavailable", {
            "adapter": "postgres",
            "scoped_out": "#400 item 7 (managed-production PostgreSQL parity)",
            "decision": "recorded refusal, not a fake adapter -- see "
                        "docs/decisions/ADR-0083",
            "blockers": POSTGRES_BLOCKERS,
            "struck_blockers": POSTGRES_BLOCKERS_STRUCK,
        })
    raise ConformanceError("unknown_adapter", {"adapter": name,
                                                "available": sorted(adapters)})
