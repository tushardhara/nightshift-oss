"""ADR-0002 fencing schema, with ADR-0033 worker-admission enforcement.

The migration remains additive. ADR-0033 now binds each classified worker
reservation to a positive generation and current durable Program run; focused
stale-generation refusal lives in ``test_admission_gate.py``.
"""

import sqlite3
import unittest

from ctlutil import Ctl


FENCED = {
    "worker_slots": ("generation", "run_id"),
    "task_claims": ("generation", "run_id"),
}


def cols(db, table):
    cx = sqlite3.connect(db)
    try:
        return {r[1] for r in cx.execute(f"PRAGMA table_info({table})")}
    finally:
        cx.close()


class FencingMigrationTest(unittest.TestCase):

    def test_fresh_database_has_the_fencing_columns(self):
        c = Ctl()
        for table, expected in FENCED.items():
            have = cols(c.db, table)
            for col in expected:
                self.assertIn(col, have, f"{table}.{col}")

    def test_migration_is_additive_on_a_pre_existing_database(self):
        """The dispatched_at pattern (bin/nightshift-ctl ensure_schema): an
        older database gains the columns in place, keeping its rows."""
        c = Ctl()
        _, slot = c.acquire_normal(task="t1", title="fencing-t1", ttl="600")
        c.seed_admission(
            task="t1", slot_id=slot["slot_id"], title="fencing-t1",
        )
        c.run("claim", "acquire", "--task", "t1", "--claimant",
              "crew_alpha/eng", "--agent", "eng_claude", "--title",
              "fencing-t1")
        # simulate a database created before the fencing migration
        cx = sqlite3.connect(c.db)
        for table, expected in FENCED.items():
            for col in expected:
                cx.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        cx.commit()
        cx.close()
        for table, expected in FENCED.items():
            for col in expected:
                self.assertNotIn(col, cols(c.db, table))
        # any command re-runs ensure_schema and migrates in place
        code, listing = c.run("slot", "list")
        self.assertEqual(code, 0)
        for table, expected in FENCED.items():
            have = cols(c.db, table)
            for col in expected:
                self.assertIn(col, have, f"{table}.{col} after migration")
        # the pre-existing rows survived the migration
        self.assertEqual(len(listing["active"]), 1)
        code, claim = c.run("claim", "show", "--task", "t1")
        self.assertEqual(code, 0)
        self.assertTrue(claim["claimed"])
        self.assertEqual(claim["holder"]["claimant"], "crew_alpha/eng")

    def test_classified_slot_generation_and_run_are_durable(self):
        """ADR-0033 turns the old placeholder columns into exact binding."""
        c = Ctl()
        code, g = c.acquire_normal(task="t1", title="fencing-t1", ttl="600")
        self.assertEqual(code, 0)
        cx = sqlite3.connect(c.db)
        row = cx.execute("SELECT generation,run_id FROM worker_slots WHERE id=?",
                         (g["slot_id"],)).fetchone()
        cx.close()
        self.assertGreater(row[0], 0)
        self.assertEqual(row[1], "fixture-run")


if __name__ == "__main__":
    unittest.main()



def load_ctl():
    """Import bin/nightshift-ctl as a module (it has no .py extension)."""
    import importlib.util
    from ctlutil import CTL
    spec = importlib.util.spec_from_loader(
        "nsctl", importlib.machinery.SourceFileLoader("nsctl", CTL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MigrationConcurrencyTest(unittest.TestCase):
    """SECURITY B3 — the additive migration must not crash when it loses a race.

    `ensure_schema` runs on EVERY invocation, outside any transaction
    (`connect` sets `isolation_level=None`), so `PRAGMA table_info` and
    `ALTER TABLE` race. With N agents hitting a pre-migration database at once,
    several see the column missing and several ALTERs land; the losers raised
    `sqlite3.OperationalError: duplicate column name` and exited **1**.

    Exit 1 is not exit 3. A worker's `slot heartbeat` in that window gets no
    verdict at all — it crashes — and `deploy/nightshift-watchdog.sh:61`'s
    `|| true` swallows it silently. This migration has not yet run against the
    live `.omni/control.db`, and it will run there for the first time with many
    agents invoking ctl at once: precisely the situation it fails in.

    The first two tests are DETERMINISTIC and are the regression evidence: they
    put the loser in exactly the state losing the race leaves it in. The
    parallel test below is a probabilistic integration guard and is labelled as
    such — it passed against the pre-fix code on some runs, so on its own it
    would have proved nothing.
    """

    def test_losing_the_add_column_race_is_not_an_error(self):
        """The loser's PRAGMA snapshot said the column was missing; by the time
        it issues its ALTER, the winner has landed. That is a SUCCESS — the
        column exists, which is all the caller wanted."""
        ctl = load_ctl()
        c = Ctl()
        cx = ctl.connect(c.db)
        self.assertIn("generation", cols(c.db, "worker_slots"))
        # pre-fix this raised OperationalError: duplicate column name -> exit 1
        ctl.add_column(cx, "worker_slots", "generation",
                       "INTEGER NOT NULL DEFAULT 0")
        ctl.add_column(cx, "task_claims", "run_id", "TEXT")
        self.assertIn("generation", cols(c.db, "worker_slots"))
        cx.close()

    def test_a_genuinely_different_schema_error_still_propagates(self):
        """Swallowing every OperationalError would turn this fix into a much
        worse bug: a migration that silently does nothing."""
        ctl = load_ctl()
        c = Ctl()
        cx = ctl.connect(c.db)
        with self.assertRaises(sqlite3.OperationalError):
            ctl.add_column(cx, "no_such_table_at_all", "x", "TEXT")
        cx.close()

    def test_losing_the_rename_race_is_not_an_error(self):
        """The pod -> crew RENAME loop is the same race in the same function.
        Fixing only the ADD COLUMN loops would leave a latent instance — the
        half-fix shape this project was already caught by once (bug 1's two
        pipefail sites). The loser finds `crew` already present and `pod` gone,
        and issues the RENAME it had already decided on."""
        ctl = load_ctl()
        c = Ctl()
        cx = ctl.connect(c.db)
        self.assertIn("crew", cols(c.db, "worker_slots"))
        ctl.rename_column(cx, "worker_slots", "pod", "crew")   # must not raise
        self.assertIn("crew", cols(c.db, "worker_slots"))
        cx.close()

    def test_parallel_invocations_against_a_pre_migration_db_never_crash(self):
        """Integration guard, PROBABILISTIC by nature: whether the losers
        actually collide depends on process scheduling. It is here to catch a
        regression end-to-end, not to prove the fix — the deterministic tests
        above do that."""
        from ctlutil import run_parallel

        c = Ctl()
        cx = sqlite3.connect(c.db)
        for table, expected in FENCED.items():
            for col in expected:
                cx.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        cx.commit(); cx.close()

        n = 24
        results = run_parallel([c.cmdline("slot", "list") for _ in range(n)])
        crashed = [(code, body) for code, body in results if code not in (0, 3)]
        self.assertEqual(
            crashed, [],
            f"{len(crashed)} of {n} concurrent invocations crashed instead of "
            f"returning a verdict: {crashed[:3]}",
        )
        for table, expected in FENCED.items():
            have = cols(c.db, table)
            for col in expected:
                self.assertIn(col, have, f"{table}.{col}")


if __name__ == "__main__":
    unittest.main()
