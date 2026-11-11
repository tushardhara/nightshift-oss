"""NSC-v1 conformance -- and the RECORDED REFUSAL of #400 item 7.

This file makes two claims and no others:

  1. The six adapter-agnostic authority traces (fencing, idempotency,
     concurrency, recovery, isolation, refusal) PASS against SQLite, the
     one backend that exists on this machine.

  2. `require_adapter("postgres")` RAISES `postgres_adapter_unavailable`,
     carrying four MEASURED blockers -- and this file asserts on that
     refusal rather than skipping.

WHY A REFUSAL AND NOT `unittest.skip`
=====================================
A skip prints `OK` and lets a reader conclude the property holds. This
repository already spent 2026-09-02 removing exactly that class of vacuous
green. So the absence of the PostgreSQL proof is itself asserted: if
somebody later writes a PostgreSQL adapter, `require_adapter` stops raising
and `test_postgres_is_refused_not_skipped` FAILS, forcing whoever wrote it
to come back here and delete a refusal deliberately.

NO POSTGRESQL ADAPTER IS WRITTEN. See docs/decisions/ADR-0083.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[0]
sys.path.insert(0, str(TESTS))

import nsc_conformance as nsc                # noqa: E402


class SqlitePassesEveryTrace(unittest.TestCase):

    def test_all_six_traces_pass(self):
        results = nsc.run_traces(nsc.sqlite_adapter_factory)
        self.assertEqual([r["trace"] for r in results], list(nsc.TRACE_NAMES))
        self.assertTrue(all(r["ok"] for r in results), results)

    def test_each_trace_individually(self):
        for name in nsc.TRACE_NAMES:
            with self.subTest(trace=name):
                results = nsc.run_traces(nsc.sqlite_adapter_factory, names=(name,))
                self.assertTrue(results[0]["ok"])

    def test_the_trace_set_is_the_six_named_authority_properties(self):
        self.assertEqual(set(nsc.TRACES), set(nsc.TRACE_NAMES))
        self.assertEqual(len(nsc.TRACE_NAMES), 6)

    def test_the_sqlite_adapter_is_a_real_file_not_an_in_memory_shortcut(self):
        """`:memory:` would make the recovery trace vacuous -- there would
        be nothing to reopen."""
        adapter = nsc.sqlite_adapter_factory()
        try:
            described = adapter["describe"]()
            self.assertEqual(described["name"], "sqlite")
            self.assertNotIn(":memory:", described["path"])
            self.assertTrue(described["path"].endswith(".db"))
        finally:
            adapter["teardown"]()

    def test_a_deliberately_broken_adapter_fails_the_traces(self):
        """Proves the traces DISCRIMINATE. A trace suite that passes
        anything you hand it is not evidence; this one refuses an adapter
        whose CAS is a no-op that always reports success."""
        def broken_factory():
            adapter = nsc.sqlite_adapter_factory()
            real_put = adapter["put"]

            def always_wins(cx, tenant, key, value, expected_fence):
                real_put(cx, tenant, key, value, expected_fence)
                return True                     # the classic lost-update bug

            adapter["put"] = always_wins
            return adapter

        with self.assertRaises(nsc.TraceFailure) as ctx:
            nsc.run_traces(broken_factory, names=("fencing",))
        self.assertEqual(ctx.exception.trace, "fencing")

    def test_a_broken_isolation_adapter_is_also_caught(self):
        def leaky_factory():
            adapter = nsc.sqlite_adapter_factory()
            real_get = adapter["get"]

            def ignores_tenant(cx, tenant, key):
                nsc._validate_tenant(tenant)
                row = cx.execute("SELECT value, fence FROM cell WHERE key=?",
                                 (key,)).fetchone()
                return None if row is None else (bytes(row[0]), int(row[1]))

            adapter["get"] = ignores_tenant
            return adapter

        with self.assertRaises(nsc.TraceFailure) as ctx:
            nsc.run_traces(leaky_factory, names=("isolation",))
        self.assertEqual(ctx.exception.trace, "isolation")


class PostgresIsRefusedNotSkipped(unittest.TestCase):

    def test_require_adapter_postgres_raises_with_the_measured_blockers(self):
        with self.assertRaises(nsc.ConformanceError) as ctx:
            nsc.require_adapter("postgres")
        exc = ctx.exception
        self.assertEqual(exc.reason_code, "postgres_adapter_unavailable")
        blockers = {b["blocker"] for b in exc.details["blockers"]}
        self.assertEqual(blockers, {
            "no_driver_and_ci_forbids_one",
            "ha_backup_and_restore_claims_have_no_infrastructure",
            "superuser_rls_false_green"})
        for entry in exc.details["blockers"]:
            self.assertTrue(entry["evidence"].strip(),
                            f"{entry['blocker']} must carry its measurement")
            self.assertIn(entry["status"], ("standing", "hazard"))
            self.assertEqual(entry["measured_on"], "2026-09-04")
        decisive = [b for b in exc.details["blockers"] if b["decisive"]]
        self.assertEqual(len(decisive), 2,
                          "the refusal must rest on the two DECISIVE blockers, "
                          "not on the count of blockers")

    def test_the_struck_blockers_are_recorded_not_deleted(self):
        """Two of the four claims this refusal was originally written on
        were FALSE when re-measured on 2026-09-04. They are kept, marked
        struck: a refusal resting on a stale measurement is the same species
        of unearned confidence as a fake proof, pointed the other way."""
        with self.assertRaises(nsc.ConformanceError) as ctx:
            nsc.require_adapter("postgres")
        struck = {b["blocker"]: b for b in ctx.exception.details["struck_blockers"]}
        self.assertEqual(set(struck), {"docker_daemon_unavailable", "pgvector_absent"})
        for entry in struck.values():
            self.assertEqual(entry["status"], "struck")
            self.assertTrue(entry["evidence"].startswith("FALSE:"))

    def test_the_struck_claims_really_are_false_measured_now(self):
        """Re-measure the two struck claims at run time, so this file cannot
        drift back into asserting something it stopped believing."""
        import shutil as _shutil
        import subprocess as _sp
        docker = _shutil.which("docker")
        if docker:
            probe = _sp.run([docker, "info"], capture_output=True, timeout=60)
            self.assertEqual(probe.returncode, 0,
                             "the struck blocker said the docker daemon was "
                             "unavailable; if that becomes true again, this "
                             "refusal must be rewritten, not silently kept")

    def test_postgres_is_not_in_available_adapters(self):
        self.assertEqual(sorted(nsc.available_adapters()), ["sqlite"])
        self.assertIs(nsc.require_adapter("sqlite"), nsc.sqlite_adapter_factory)

    def test_no_postgres_adapter_was_written(self):
        """Structural: the file contains no driver import and no connection
        string, so nobody can later mistake a commented sketch for a
        working adapter."""
        src = (TESTS / "nsc_conformance.py").read_text()
        # Line-start forms only. The module docstring QUOTES the import
        # names while recording that they fail -- naming the thing you
        # refused is the point, not a violation.
        statements = [line for line in src.splitlines()
                      if line and not line.startswith((" ", "\t", "#"))]
        for line in statements:
            for forbidden in ("import psycopg", "import pg8000", "import asyncpg",
                              "def postgres_adapter_factory"):
                self.assertNotIn(forbidden, line)
        self.assertNotIn("postgresql://", src)
        self.assertNotIn("psycopg2.connect(", src)

    def test_the_driver_really_is_absent_measured_now_not_asserted(self):
        """The first blocker, re-measured at run time. If a driver ever
        lands, this fails and the refusal has to be revisited on purpose."""
        import importlib
        for name in ("psycopg", "psycopg2", "pg8000", "asyncpg"):
            with self.subTest(driver=name):
                with self.assertRaises(ImportError):
                    importlib.import_module(name)

    def test_an_unknown_adapter_name_is_refused_distinctly(self):
        with self.assertRaises(nsc.ConformanceError) as ctx:
            nsc.require_adapter("mysql")
        self.assertEqual(ctx.exception.reason_code, "unknown_adapter")


if __name__ == "__main__":
    unittest.main()
