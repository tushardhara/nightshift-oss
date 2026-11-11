"""Issues #210/#211 and ADR-0031/ADR-0032: task facts and read readiness."""

import contextlib
import importlib.machinery
import importlib.util
import sqlite3
import unittest

from ctlutil import CTL, Ctl, run_parallel
from test_repository_scope import bind


def dependency_count(db):
    with contextlib.closing(sqlite3.connect(db)) as cx, cx:
        return cx.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0]


def load_ctl():
    spec = importlib.util.spec_from_loader(
        "task_graph_ctl",
        importlib.machinery.SourceFileLoader("task_graph_ctl", CTL),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allow_null_task_state(db):
    """Rebuild the temp fixture as a damaged legacy schema for fail-closed tests.

    Column list mirrors `ensure_schema`'s cumulative `tasks` shape exactly
    (id .. extended_at as before, plus issue #223/ADR-0060's `ready_at`,
    `pr_opened_at`, `integrated_at`, issue #541's `paths`, and issue #165/
    ADR-0084's `repository_id`, appended in their `add_column` order) --
    `INSERT INTO tasks_nullable SELECT * FROM tasks` is positional, so this
    list must track every column `ensure_schema` has ever added, not just
    the ones this file's own tests exercise. `repository_id` keeps its real
    `NOT NULL DEFAULT 0 CHECK(...)` declaration (unlike every other column
    here, relaxed on purpose to simulate a damaged legacy schema) because
    the positional INSERT below only ever copies a value `ensure_schema`
    itself already wrote (every row's repository_id is 0, from `Ctl.
    __init__`'s own `nightshift-ctl init`), so the real constraint is never
    exercised by this rebuild and there is nothing to relax.
    """
    with contextlib.closing(sqlite3.connect(db)) as cx, cx:
        cx.executescript(
            """
            CREATE TABLE tasks_nullable(
              id TEXT PRIMARY KEY,
              crew TEXT, agent TEXT, title TEXT, issue INTEGER,
              state TEXT DEFAULT 'pending',
              base_sha TEXT, spec_ref TEXT, worktree TEXT, branch TEXT,
              pr_url TEXT, size TEXT CHECK(size IN ('S','M','L')),
              created_at REAL NOT NULL, updated_at REAL NOT NULL,
              started_at REAL, budget_seconds REAL,
              extension_seconds REAL, extension_reason TEXT,
              extended_at REAL,
              ready_at REAL, pr_opened_at REAL, integrated_at REAL,
              paths TEXT,
              repository_id NOT NULL DEFAULT 0
                CHECK(typeof(repository_id)='integer' AND repository_id>=0));
            INSERT INTO tasks_nullable SELECT * FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE tasks_nullable RENAME TO tasks;
            """
        )


class TaskSizeTest(unittest.TestCase):
    def test_valid_sizes_are_persisted(self):
        c = Ctl()
        for size in ("S", "M", "L"):
            task_id = "task-" + size
            code, _ = c.run("task", "upsert", "--id", task_id, "--size", size)
            self.assertEqual(code, 0)
            code, shown = c.run("task", "show", "--id", task_id)
            self.assertEqual(code, 0)
            self.assertEqual(shown["task"]["size"], size)

    def test_invalid_size_create_and_update_write_nothing(self):
        c = Ctl()
        for value in ("XL", "s"):
            missing = "missing-" + value
            code, _ = c.run("task", "upsert", "--id", missing,
                            "--size", value)
            self.assertEqual(code, 2)
            code, _ = c.run("task", "show", "--id", missing)
            self.assertEqual(code, 3)

        c.run("task", "upsert", "--id", "existing", "--size", "S")
        for value in ("XL", "s"):
            code, _ = c.run("task", "upsert", "--id", "existing",
                            "--size", value)
            self.assertEqual(code, 2)
            _, shown = c.run("task", "show", "--id", "existing")
            self.assertEqual(shown["task"]["size"], "S")

    def test_database_check_rejects_invalid_size(self):
        c = Ctl()
        c.run("task", "upsert", "--id", "A")
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute("UPDATE tasks SET size='XL' WHERE id='A'")


class TaskDependencyTest(unittest.TestCase):
    def setUp(self):
        self.c = Ctl()
        for task_id in ("A", "B", "C"):
            self.c.run("task", "upsert", "--id", task_id, "--size", "S")

    def test_empty_and_one_dependency(self):
        _, shown = self.c.run("task", "show", "--id", "B")
        self.assertEqual(shown["task"]["depends_on"], [])

        code, added = self.c.run("task", "dependency-add", "--id", "B",
                                 "--depends-on", "A")
        self.assertEqual(code, 0)
        self.assertTrue(added["created"])
        _, shown = self.c.run("task", "show", "--id", "B")
        self.assertEqual(shown["task"]["depends_on"], ["A"])

    def test_multiple_dependencies_are_sorted_not_insertion_order(self):
        self.c.run("task", "dependency-add", "--id", "C", "--depends-on", "B")
        self.c.run("task", "dependency-add", "--id", "C", "--depends-on", "A")
        _, shown = self.c.run("task", "show", "--id", "C")
        self.assertEqual(shown["task"]["depends_on"], ["A", "B"])

    def test_self_dependency_is_usage_error_and_writes_nothing(self):
        code, refused = self.c.run("task", "dependency-add", "--id", "A",
                                   "--depends-on", "A")
        self.assertEqual(code, 2)
        self.assertFalse(refused["ok"])
        self.assertEqual(dependency_count(self.c.db), 0)

    def test_unknown_endpoints_are_refused_without_partial_rows(self):
        code, refused = self.c.run("task", "dependency-add", "--id", "missing",
                                   "--depends-on", "A")
        self.assertEqual(code, 3)
        self.assertEqual(refused["reason"], "unknown task")
        self.assertEqual(dependency_count(self.c.db), 0)

        code, refused = self.c.run("task", "dependency-add", "--id", "A",
                                   "--depends-on", "missing")
        self.assertEqual(code, 3)
        self.assertEqual(refused["reason"], "unknown prerequisite task")
        self.assertEqual(dependency_count(self.c.db), 0)

    def test_duplicate_is_idempotent(self):
        _, first = self.c.run("task", "dependency-add", "--id", "B",
                              "--depends-on", "A")
        code, second = self.c.run("task", "dependency-add", "--id", "B",
                                  "--depends-on", "A")
        self.assertTrue(first["created"])
        self.assertEqual(code, 0)
        self.assertFalse(second["created"])
        self.assertEqual(dependency_count(self.c.db), 1)

    def test_concurrent_duplicate_adds_store_one_edge(self):
        commands = [
            self.c.cmdline("task", "dependency-add", "--id", "B",
                           "--depends-on", "A")
            for _ in range(12)
        ]
        results = run_parallel(commands)
        self.assertTrue(all(code == 0 for code, _ in results), results)
        self.assertEqual(sum(bool(body["created"]) for _, body in results), 1)
        self.assertEqual(dependency_count(self.c.db), 1)

    def test_database_check_rejects_self_edge(self):
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute(
                    "INSERT INTO task_dependencies(task_id,depends_on_task_id) "
                    "VALUES('A','A')"
                )

    def test_dependency_survives_fresh_cli_process(self):
        self.c.run("task", "dependency-add", "--id", "B", "--depends-on", "A")
        code, shown = self.c.run("task", "show", "--id", "B")
        self.assertEqual(code, 0)
        self.assertEqual(shown["task"]["depends_on"], ["A"])


class TaskGraphMigrationAndDumpTest(unittest.TestCase):
    def test_pre_change_database_migrates_without_rewriting_task(self):
        c = Ctl()
        c.run("task", "upsert", "--id", "legacy", "--state", "building")
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            cx.execute("DROP TABLE task_dependencies")
            cx.execute("ALTER TABLE tasks DROP COLUMN size")

        code, shown = c.run("task", "show", "--id", "legacy")
        self.assertEqual(code, 0)
        self.assertEqual(shown["task"]["state"], "building")
        self.assertIsNone(shown["task"]["size"])
        self.assertEqual(shown["task"]["depends_on"], [])
        with contextlib.closing(sqlite3.connect(c.db)) as cx, cx:
            columns = {row[1] for row in cx.execute("PRAGMA table_info(tasks)")}
            tables = {row[0] for row in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertIn("size", columns)
        self.assertIn("task_dependencies", tables)

    def test_dump_contains_dependencies_in_stable_order(self):
        c = Ctl()
        for task_id in ("A", "B", "C"):
            c.run("task", "upsert", "--id", task_id)
        c.run("task", "dependency-add", "--id", "C", "--depends-on", "B")
        c.run("task", "dependency-add", "--id", "C", "--depends-on", "A")
        c.run("task", "dependency-add", "--id", "B", "--depends-on", "A")
        _, dumped = c.run("dump")
        # Issue #165 / ADR-0084: `dump`'s task_dependencies projection now
        # names `repository_id` alongside its two original columns, so the
        # projection stays faithful to what the row actually carries (every
        # row here is SCOPE_HOME/0 -- this plane was never bound). The
        # STABLE ORDER this test is actually about is unchanged.
        self.assertEqual(dumped["task_dependencies"], [
            {"task_id": "B", "depends_on_task_id": "A", "repository_id": 0},
            {"task_id": "C", "depends_on_task_id": "A", "repository_id": 0},
            {"task_id": "C", "depends_on_task_id": "B", "repository_id": 0},
        ])


class TaskReadinessTest(unittest.TestCase):
    def setUp(self):
        self.c = Ctl()

    def upsert(self, task_id, size=None, state=None):
        args = ["task", "upsert", "--id", task_id]
        if size is not None:
            args.extend(("--size", size))
        if state is not None:
            args.extend(("--state", state))
        return self.c.run(*args)

    def show(self, task_id):
        code, body = self.c.run("task", "show", "--id", task_id)
        self.assertEqual(code, 0, body)
        return body["task"]

    def edge(self, task_id, prerequisite):
        code, body = self.c.run(
            "task", "dependency-add", "--id", task_id,
            "--depends-on", prerequisite,
        )
        self.assertEqual(code, 0, body)

    def test_pending_s_and_m_without_dependencies_are_ready(self):
        for size in ("S", "M"):
            with self.subTest(size=size):
                task_id = "ready-" + size
                self.upsert(task_id, size=size)
                task = self.show(task_id)
                self.assertEqual(task["depends_on"], [])
                self.assertEqual(task["blocked_by"], [])
                self.assertTrue(task["dispatchable"])
                self.assertEqual(task["reason_code"], "ready")

    def test_prerequisite_transition_changes_only_derived_verdict(self):
        """Issue #223/ADR-0060 amendment: `ready_at` is now a real,
        persisted first-observed-ready side effect of this same
        `_task_readiness_snapshot` read path (AC1), not a purely derived
        value -- so it is deliberately excluded from the "nothing else on
        the raw row changed" comparison below and asserted on directly
        instead. Every other raw column (state/size/pr_opened_at/
        integrated_at/etc.) is still untouched by a mere prerequisite
        transition, which is what this test otherwise still guards."""
        self.upsert("A", size="S")
        self.upsert("B", size="M")
        self.edge("B", "A")
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.row_factory = sqlite3.Row
            before = cx.execute("SELECT * FROM tasks WHERE id='B'").fetchone()
        self.assertIsNone(before["ready_at"])

        blocked = self.show("B")
        self.assertFalse(blocked["dispatchable"])
        self.assertEqual(blocked["reason_code"], "blocked_dependency")
        self.assertEqual(blocked["blocked_by"], ["A"])
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.row_factory = sqlite3.Row
            still_blocked = cx.execute(
                "SELECT * FROM tasks WHERE id='B'"
            ).fetchone()
        # still blocked -> never observed ready -> ready_at still unset,
        # and nothing else on the raw row moved either.
        self.assertIsNone(still_blocked["ready_at"])
        self.assertEqual(dict(before), dict(still_blocked))

        self.upsert("A", state="integrated")
        ready = self.show("B")
        self.assertTrue(ready["dispatchable"])
        self.assertEqual(ready["reason_code"], "ready")
        self.assertEqual(ready["blocked_by"], [])
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.row_factory = sqlite3.Row
            after = cx.execute("SELECT * FROM tasks WHERE id='B'").fetchone()
        # AC1: first observed-ready moment -> ready_at now set, and it is
        # the ONLY raw column this prerequisite transition moved.
        self.assertIsNotNone(after["ready_at"])
        before_sans_ready_at = {k: v for k, v in dict(before).items()
                                 if k != "ready_at"}
        after_sans_ready_at = {k: v for k, v in dict(after).items()
                                if k != "ready_at"}
        self.assertEqual(before_sans_ready_at, after_sans_ready_at)
        self.assertEqual(blocked["updated_at"], ready["updated_at"])

    def test_only_exact_integrated_satisfies_a_dependency(self):
        self.upsert("A", size="S")
        self.upsert("B", size="S")
        self.edge("B", "A")
        unsatisfied = (
            "pending", "dropped", "superseded", "ready", "building",
            "done", "merged", "qa", "unknown-state",
        )
        for state in unsatisfied:
            with self.subTest(state=state):
                self.upsert("A", state=state)
                task = self.show("B")
                self.assertFalse(task["dispatchable"])
                self.assertEqual(task["reason_code"], "blocked_dependency")
                self.assertEqual(task["blocked_by"], ["A"])
        self.upsert("A", state="integrated")
        self.assertEqual(self.show("B")["reason_code"], "ready")

    def test_multiple_blockers_are_filtered_and_sorted(self):
        for task_id, state in (("Z", "dropped"), ("M", "integrated"),
                               ("A", "building")):
            self.upsert(task_id, size="S", state=state)
        self.upsert("C", size="S")
        for prerequisite in ("Z", "M", "A"):
            self.edge("C", prerequisite)
        task = self.show("C")
        self.assertEqual(task["depends_on"], ["A", "M", "Z"])
        self.assertEqual(task["blocked_by"], ["A", "Z"])
        self.assertEqual(task["reason_code"], "blocked_dependency")

    def test_large_and_missing_or_invalid_size_fail_closed(self):
        self.upsert("large", size="L")
        large = self.show("large")
        self.assertEqual(large["reason_code"], "needs_split")
        self.assertFalse(large["dispatchable"])
        self.assertEqual(large["blocked_by"], [])

        self.upsert("legacy")
        legacy = self.show("legacy")
        self.assertEqual(legacy["reason_code"], "missing_size")
        self.assertFalse(legacy["dispatchable"])

        self.upsert("damaged", size="S")
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("PRAGMA ignore_check_constraints=ON")
            cx.execute("UPDATE tasks SET size='XL' WHERE id='damaged'")
        damaged = self.show("damaged")
        self.assertEqual(damaged["reason_code"], "invalid_size")
        self.assertFalse(damaged["dispatchable"])

    def test_every_non_pending_own_state_fails_closed(self):
        states = (
            "building", "ready", "integrated", "dropped", "superseded",
            "done", "merged", "unknown-state",
        )
        for state in states:
            with self.subTest(state=state):
                task_id = "task-" + state
                self.upsert(task_id, size="S", state=state)
                task = self.show(task_id)
                self.assertFalse(task["dispatchable"])
                self.assertEqual(task["reason_code"], "not_pending")

        self.upsert("null-state", size="S")
        allow_null_task_state(self.c.db)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("UPDATE tasks SET state=NULL WHERE id='null-state'")
        self.assertEqual(self.show("null-state")["reason_code"], "not_pending")

    def test_null_prerequisite_state_does_not_satisfy_edge(self):
        self.upsert("A", size="S")
        self.upsert("B", size="S")
        self.edge("B", "A")
        allow_null_task_state(self.c.db)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute("UPDATE tasks SET state=NULL WHERE id='A'")
        task = self.show("B")
        self.assertEqual(task["reason_code"], "blocked_dependency")
        self.assertEqual(task["blocked_by"], ["A"])

    def test_two_and_three_node_cycles_fail_closed(self):
        for task_id in ("A", "B", "C", "D", "E"):
            self.upsert(task_id, size="S")
        self.edge("A", "B")
        self.edge("B", "A")
        self.edge("C", "D")
        self.edge("D", "E")
        self.edge("E", "C")
        for task_id in ("A", "B", "C", "D", "E"):
            with self.subTest(task=task_id):
                task = self.show(task_id)
                self.assertFalse(task["dispatchable"])
                self.assertEqual(task["reason_code"], "dependency_cycle")

    def test_mixed_cycle_is_detected_with_no_direct_blocker(self):
        self.upsert("integrated", size="S", state="integrated")
        self.upsert("pending", size="S")
        self.edge("integrated", "pending")
        self.edge("pending", "integrated")
        task = self.show("pending")
        self.assertEqual(task["depends_on"], ["integrated"])
        self.assertEqual(task["blocked_by"], [])
        self.assertEqual(task["reason_code"], "dependency_cycle")
        self.assertFalse(task["dispatchable"])

    def test_cycle_elsewhere_does_not_taint_ready_task(self):
        for task_id in ("A", "B", "ready"):
            self.upsert(task_id, size="S")
        self.edge("A", "B")
        self.edge("B", "A")
        self.assertEqual(self.show("ready")["reason_code"], "ready")

    def test_orphan_edge_is_an_explicit_blocker(self):
        self.upsert("A", size="S")
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "INSERT INTO task_dependencies(task_id,depends_on_task_id) "
                "VALUES('A','missing')"
            )
        task = self.show("A")
        self.assertEqual(task["depends_on"], ["missing"])
        self.assertEqual(task["blocked_by"], ["missing"])
        self.assertEqual(task["reason_code"], "blocked_dependency")

    def test_frozen_reason_precedence_preserves_blocker_evidence(self):
        self.upsert("prerequisite", size="S")
        self.upsert("started", state="building")
        self.edge("started", "prerequisite")
        started = self.show("started")
        self.assertEqual(started["reason_code"], "not_pending")
        self.assertEqual(started["blocked_by"], ["prerequisite"])

        self.upsert("started-large", size="L", state="building")
        self.edge("started-large", "prerequisite")
        started_large = self.show("started-large")
        self.assertFalse(started_large["dispatchable"])
        self.assertEqual(started_large["reason_code"], "not_pending")
        self.assertEqual(started_large["blocked_by"], ["prerequisite"])

        self.upsert("large", size="L")
        self.upsert("peer", size="S")
        self.edge("large", "peer")
        self.edge("peer", "large")
        large = self.show("large")
        self.assertEqual(large["reason_code"], "needs_split")
        self.assertEqual(large["blocked_by"], ["peer"])

    def test_task_ready_returns_same_enriched_objects_in_id_order(self):
        fixtures = (
            ("z-blocked", "S", "pending"),
            ("prerequisite", "S", "building"),
            ("a-ready", "S", "pending"),
            ("m-ready", "M", "pending"),
            ("integrated-prerequisite", "S", "integrated"),
            ("l-large", "L", "pending"),
            ("t-terminal", "S", "integrated"),
            ("n-null", None, "pending"),
        )
        for task_id, size, state in fixtures:
            self.upsert(task_id, size=size, state=state)
        self.edge("z-blocked", "prerequisite")
        self.edge("m-ready", "integrated-prerequisite")

        code, body = self.c.run("task", "ready")
        self.assertEqual(code, 0)
        self.assertEqual([task["id"] for task in body["tasks"]],
                         ["a-ready", "m-ready"])
        for task in body["tasks"]:
            self.assertTrue(task["dispatchable"])
            self.assertEqual(task["reason_code"], "ready")
            self.assertEqual(task, self.show(task["id"]))

    def test_task_ready_empty_is_success(self):
        self.upsert("large", size="L")
        self.upsert("legacy")
        self.upsert("finished", size="S", state="integrated")
        code, body = self.c.run("task", "ready")
        self.assertEqual(code, 0)
        self.assertEqual(body, {"tasks": []})

    def test_readiness_uses_one_joined_read_and_persists_only_ready_at(self):
        """Issue #223/ADR-0060 amendment: `_task_readiness_snapshot` is no
        longer a fully pure read -- AC1 requires it to persist `ready_at`
        (guarded, first-observed-ready only) the first time it sees a task's
        reason_code as "ready". The single-joined-read invariant this test
        originally asserted still holds (the guarded ready_at write below is
        an UPDATE against the rows already fetched by that one SELECT, not a
        second SELECT), and "not persisted" narrows to exactly the column
        ADR-0060 deliberately persists -- nothing else on any row moves, and
        no schema/dependency-edge write occurs from a read."""
        self.upsert("A", size="S", state="integrated")
        self.upsert("B", size="M")
        self.edge("B", "A")
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.row_factory = sqlite3.Row
            raw_before = cx.execute("SELECT * FROM tasks ORDER BY id").fetchall()
            edges_before = cx.execute(
                "SELECT * FROM task_dependencies ORDER BY task_id,depends_on_task_id"
            ).fetchall()
            schema_before = cx.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        before_by_id = {row["id"]: dict(row) for row in raw_before}
        # A is already 'integrated' (terminal, reason_code="not_pending"):
        # never observed ready, so its ready_at stays unset throughout.
        self.assertIsNone(before_by_id["A"]["ready_at"])
        self.assertIsNone(before_by_id["B"]["ready_at"])

        ctl = load_ctl()
        cx = ctl.connect(self.c.db)
        statements = []
        cx.set_trace_callback(statements.append)
        snapshot = ctl._task_readiness_snapshot(cx)
        cx.close()
        selects = [statement for statement in statements
                   if statement.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1, selects)
        self.assertIn("LEFT JOIN task_dependencies", selects[0])
        self.assertIn("LEFT JOIN tasks", selects[0])
        self.assertEqual(snapshot["B"]["reason_code"], "ready")
        # AC1: B's first-ever observed-ready moment, right here.
        self.assertIsNotNone(snapshot["B"]["ready_at"])
        self.assertIsNone(snapshot["A"]["ready_at"])

        self.show("B")
        self.c.run("task", "ready")
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.row_factory = sqlite3.Row
            raw_after = cx.execute("SELECT * FROM tasks ORDER BY id").fetchall()
            edges_after = cx.execute(
                "SELECT * FROM task_dependencies ORDER BY task_id,depends_on_task_id"
            ).fetchall()
            schema_after = cx.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        after_by_id = {row["id"]: dict(row) for row in raw_after}
        # AC5: the two further reads above (show/task ready) find ready_at
        # already set and do not move it again.
        self.assertIsNone(after_by_id["A"]["ready_at"])
        self.assertIsNotNone(after_by_id["B"]["ready_at"])
        self.assertEqual(after_by_id["B"]["ready_at"], snapshot["B"]["ready_at"])
        # Nothing else, on either row, moved -- ready_at is the ONE column
        # a bare read is now allowed to persist.
        for task_id in before_by_id:
            b = {k: v for k, v in before_by_id[task_id].items() if k != "ready_at"}
            a = {k: v for k, v in after_by_id[task_id].items() if k != "ready_at"}
            self.assertEqual(b, a, task_id)
        self.assertEqual(edges_before, edges_after)
        self.assertEqual(schema_before, schema_after)

        _, dumped = self.c.run("dump")
        derived = {"depends_on", "dispatchable", "reason_code", "blocked_by"}
        self.assertTrue(all(derived.isdisjoint(task) for task in dumped["tasks"]))
        self.assertTrue(derived.isdisjoint(dumped))


class ReadinessLadderDoesNotCrossRepositories(unittest.TestCase):
    """Issue #700 follow-up: `_task_readiness_snapshot`'s single joined
    read (see `TaskReadinessTest.test_readiness_uses_one_joined_read_and_
    persists_only_ready_at` above, unchanged by this fix) must not let a
    dependency edge span two repositories -- that would be a silent
    cross-repository coupling in the verb that decides what is
    dispatchable. `task dependency-add` already refuses to CREATE such an
    edge (test_repository_scope.Dependencies), so every edge here is
    forged with one direct SQL INSERT against the test's own temp DB.
    Fixture state is built entirely from CLI calls plus that one INSERT --
    no git ref, no gitignored file, nothing outside the worktree (#698)."""

    def setUp(self):
        self.c = Ctl()
        code, body, _ = bind(self.c, 7)
        self.assertEqual(code, 0, body)

    def upsert(self, task_id, *, repository_id, state=None):
        args = ["task", "upsert", "--id", task_id, "--size", "S",
                "--repository-id", str(repository_id)]
        if state is not None:
            args += ["--state", state]
        code, body = self.c.run(*args)
        self.assertEqual(code, 0, body)
        return body["task"]

    def show(self, task_id):
        code, body = self.c.run("task", "show", "--id", task_id)
        self.assertEqual(code, 0, body)
        return body["task"]

    def forge_edge(self, task_id, depends_on, repository_id):
        """Bypass `task dependency-add`'s cross-repository refusal with a
        direct INSERT -- the exact shape a future write path that forgot
        that guard would produce."""
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "INSERT INTO task_dependencies"
                "(task_id,depends_on_task_id,repository_id) VALUES(?,?,?)",
                (task_id, depends_on, repository_id))

    def test_forged_cross_repository_edge_blocks_and_never_resolves(self):
        self.upsert("A", repository_id=7)
        self.upsert("B", repository_id=9)
        self.forge_edge("A", "B", 7)

        a = self.show("A")
        self.assertEqual(a["reason_code"], "blocked_dependency", a)
        self.assertEqual(a["blocked_by"], ["B"], a)
        self.assertFalse(a["dispatchable"], a)

        code, body = self.c.run("task", "upsert", "--id", "B",
                                "--state", "integrated")
        self.assertEqual(code, 0, body)

        # Repo 9's task reaching `integrated` must not unblock repo 7's --
        # this is the exact defect merged main has today (I reproduced it).
        a = self.show("A")
        self.assertEqual(a["reason_code"], "blocked_dependency", a)
        self.assertEqual(a["blocked_by"], ["B"], a)
        self.assertFalse(a["dispatchable"], a)

        code, body = self.c.run("task", "ready")
        self.assertEqual(code, 0, body)
        self.assertNotIn("A", {t["id"] for t in body["tasks"]})

    def test_forged_cross_repository_cycle_is_not_a_dependency_cycle(self):
        self.upsert("A", repository_id=7)
        self.upsert("B", repository_id=9)
        self.forge_edge("A", "B", 7)
        self.forge_edge("B", "A", 9)

        a = self.show("A")
        b = self.show("B")
        self.assertNotEqual(a["reason_code"], "dependency_cycle", a)
        self.assertNotEqual(b["reason_code"], "dependency_cycle", b)
        self.assertEqual(a["reason_code"], "blocked_dependency", a)
        self.assertEqual(b["reason_code"], "blocked_dependency", b)

    def test_snapshot_default_stays_plane_global_and_single_read(self):
        """The new keyword-only `repository_id` parameter must not disturb
        the one-positional-arg call form `slot acquire`'s NEW gate, the
        continuation-budget gate, `task extend`, and
        deploy/policies/omnipods_policies.py's re-exported alias all use,
        nor the plane-global default those call sites rely on, nor the
        single-joined-read invariant the existing test above already
        pins."""
        self.upsert("A", repository_id=7)
        self.upsert("B", repository_id=9)

        ctl = load_ctl()
        cx = ctl.connect(self.c.db)
        statements = []
        cx.set_trace_callback(statements.append)
        snapshot = ctl._task_readiness_snapshot(cx)  # one positional arg
        selects = [s for s in statements
                  if s.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1, selects)
        self.assertEqual(set(snapshot), {"A", "B"})

        statements.clear()
        scoped = ctl._task_readiness_snapshot(cx, repository_id=9)
        selects = [s for s in statements
                  if s.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1, selects)
        self.assertEqual(set(scoped), {"B"})
        cx.close()


class TaskBudgetExpiryTest(unittest.TestCase):
    """Issue #215 / ADR-0045: tasks.budget_seconds and reason_code=budget_expired.

    Per the DoR verdict's own testability note (.omni/reports/
    2026-08-29-215-dor-verdict.md, section 7), fixtures write started_at/
    budget_seconds directly via sqlite3 -- this file's existing convention
    (see e.g. TaskSizeTest.test_database_check_rejects_invalid_size) -- rather
    than driving real admission through omnipods_policies.worker_semaphore,
    which is tests/test_task_started_at.py's job. No new CLI verb is used or
    needed to reach a "started" fixture row.
    """

    def setUp(self):
        self.c = Ctl()

    def show(self, task_id, now=None):
        code, body = self.c.run("task", "show", "--id", task_id, now=now)
        self.assertEqual(code, 0, body)
        return body["task"]

    def seed_started(self, task_id, *, state, started_at, budget_seconds,
                      size="S"):
        """Create a task, then stamp started_at/budget_seconds directly.

        `task upsert` cannot set started_at (ADR-0043) and validates
        budget_seconds (require_valid_budget_seconds) -- writing both
        directly via sqlite3 is how this suite reaches a fixture row a real
        admission would produce, and also how AC9's damaged-value tests
        reach a value the CLI itself would have refused.
        """
        code, _ = self.c.run("task", "upsert", "--id", task_id,
                             "--size", size, "--state", state)
        self.assertEqual(code, 0)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE tasks SET started_at=?, budget_seconds=? WHERE id=?",
                (started_at, budget_seconds, task_id),
            )

    # --- AC1: additive, nullable schema -------------------------------

    def test_budget_seconds_column_is_additive_and_nullable(self):
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cols = {r[1]: r for r in cx.execute("PRAGMA table_info(tasks)")}
        self.assertIn("budget_seconds", cols)
        _, _, coltype, notnull, default, _ = cols["budget_seconds"]
        self.assertEqual(coltype, "REAL")
        self.assertEqual(notnull, 0)
        self.assertIsNone(default)

        self.c.run("task", "upsert", "--id", "no-budget", "--size", "S")
        self.assertIsNone(self.show("no-budget")["budget_seconds"])

    # --- AC2: settable via `task upsert`, refused loudly like --ttl ----

    def test_budget_seconds_settable_on_create_and_update(self):
        code, _ = self.c.run("task", "upsert", "--id", "T",
                             "--size", "S", "--budget-seconds", "3600")
        self.assertEqual(code, 0)
        self.assertEqual(self.show("T")["budget_seconds"], 3600.0)

        code, _ = self.c.run("task", "upsert", "--id", "T",
                             "--budget-seconds", "60")
        self.assertEqual(code, 0)
        self.assertEqual(self.show("T")["budget_seconds"], 60.0)

    def test_invalid_budget_seconds_create_and_update_write_nothing(self):
        for value in ("0", "-5", "inf", "-inf", "nan"):
            with self.subTest(value=value, op="create"):
                task_id = "bad-create-" + value.replace("-", "neg")
                # `--budget-seconds=<v>`, never `--budget-seconds <v>`: a
                # leading `-` (as in "-5"/"-inf") parses as an unknown
                # OPTION and argparse exits 2 on its own before this test
                # ever reaches require_valid_budget_seconds -- same
                # convention tests/test_ttl_family.py documents for --ttl.
                code, body = self.c.run(
                    "task", "upsert", "--id", task_id,
                    "--size", "S", f"--budget-seconds={value}",
                )
                self.assertEqual(code, 2)
                self.assertFalse(body["ok"])
                # The whole call wrote nothing, including --size: the task
                # does not exist at all, not merely "exists without a
                # budget".
                code, _ = self.c.run("task", "show", "--id", task_id)
                self.assertEqual(code, 3)

        code, _ = self.c.run("task", "upsert", "--id", "existing",
                             "--size", "S", "--budget-seconds", "100")
        self.assertEqual(code, 0)
        for value in ("0", "-5", "inf", "-inf", "nan"):
            with self.subTest(value=value, op="update"):
                code, body = self.c.run(
                    "task", "upsert", "--id", "existing",
                    "--size", "M", f"--budget-seconds={value}",
                )
                self.assertEqual(code, 2)
                self.assertFalse(body["ok"])
                shown = self.show("existing")
                # Neither the bad budget NOR the accompanying --size='M'
                # landed -- one refusal, zero partial writes.
                self.assertEqual(shown["size"], "S")
                self.assertEqual(shown["budget_seconds"], 100.0)

    # --- AC3: a pending task never accrues expiry -----------------------

    def test_pending_task_never_accrues_expiry_no_matter_how_long_it_waits(self):
        self.c.run("task", "upsert", "--id", "queued", "--size", "S",
                   "--budget-seconds", "1")
        # started_at is NULL (never admitted) -- read arbitrarily far in the
        # future, using the same injectable clock the expiry check itself
        # uses (ADR-0005 D4).
        far_future = 10_000_000_000.0
        task = self.show("queued", now=far_future)
        self.assertTrue(task["dispatchable"])
        self.assertEqual(task["reason_code"], "ready")

    # --- AC4: expiry = now() >= started_at + budget_seconds, injectable --

    def test_expiry_boundary_uses_the_injectable_clock(self):
        self.seed_started("boundary", state="building",
                          started_at=1_000.0, budget_seconds=100.0)
        # Control: one second before the boundary, still within budget.
        before = self.show("boundary", now=1_099.0)
        self.assertEqual(before["reason_code"], "not_pending")
        self.assertFalse(before["dispatchable"])
        # Counterfactual: AC4 states `>=`, so the exact boundary instant
        # already counts as expired.
        at_boundary = self.show("boundary", now=1_100.0)
        self.assertEqual(at_boundary["reason_code"], "budget_expired")
        self.assertFalse(at_boundary["dispatchable"])
        after = self.show("boundary", now=1_100.5)
        self.assertEqual(after["reason_code"], "budget_expired")

    # --- AC5: new precedence row for non-terminal, started, over-budget --

    def test_budget_expired_replaces_not_pending_for_non_terminal_states(self):
        for state in ("building", "ready", "fixing"):
            with self.subTest(state=state):
                task_id = "expired-" + state
                self.seed_started(task_id, state=state, started_at=0.0,
                                  budget_seconds=10.0)
                task = self.show(task_id, now=10.0)
                self.assertFalse(task["dispatchable"])
                self.assertEqual(task["reason_code"], "budget_expired")

    def test_every_other_non_pending_case_keeps_not_pending(self):
        # No budget set at all -- today's exact behavior (AC7's own claim,
        # exercised directly here too).
        self.c.run("task", "upsert", "--id", "no-budget-building",
                   "--size", "S", "--state", "building")
        self.assertEqual(self.show("no-budget-building")["reason_code"],
                         "not_pending")

        # Budget set, but not started (started_at NULL) -- a task cannot
        # normally be non-pending with no started_at under the real
        # admission path, but the readiness check must still fail closed to
        # not_pending rather than crash or expire it.
        self.c.run("task", "upsert", "--id", "no-start-building",
                   "--size", "S", "--state", "building",
                   "--budget-seconds", "10")
        self.assertEqual(
            self.show("no-start-building", now=1_000_000.0)["reason_code"],
            "not_pending")

        # Budget set, started, still inside the window.
        self.seed_started("in-budget", state="building", started_at=0.0,
                          budget_seconds=1000.0)
        self.assertEqual(self.show("in-budget", now=1.0)["reason_code"],
                         "not_pending")

    def test_budget_expired_task_never_appears_in_task_ready(self):
        self.seed_started("expired-ready-check", state="building",
                          started_at=0.0, budget_seconds=1.0)
        code, body = self.c.run("task", "ready", now=100.0)
        self.assertEqual(code, 0)
        self.assertNotIn(
            "expired-ready-check",
            [task["id"] for task in body["tasks"]],
        )

    # --- AC6: terminal states always keep not_pending -------------------

    def test_terminal_states_never_report_budget_expired(self):
        # "done"/"merged"/"dropped"/"superseded" are TERMINAL_TASK_STATES
        # (the worktree-reap set); "integrated" is ADR-0032's own
        # completion state and is deliberately NOT in TERMINAL_TASK_STATES
        # (MERGE_BLOCKER-1, staff, round 1: the readiness check must use
        # ADR-0032's completion set, not the worktree-reap set, or an
        # ordinary fully-shipped `integrated` task incorrectly reads
        # `budget_expired` once its budget window elapses).
        for state in ("done", "merged", "dropped", "superseded",
                      "integrated"):
            with self.subTest(state=state):
                task_id = "terminal-" + state
                self.seed_started(task_id, state=state, started_at=0.0,
                                  budget_seconds=1.0)
                task = self.show(task_id, now=1_000_000.0)
                self.assertFalse(task["dispatchable"])
                self.assertEqual(task["reason_code"], "not_pending")

    # --- AC9: a damaged budget_seconds is read as NULL, never raises -----

    def test_damaged_budget_seconds_is_treated_as_null_and_never_raises(self):
        # MERGE_BLOCKER-2 (crew_beta, round 1): the read-time guard must
        # reject <=0 too, matching require_valid_budget_seconds's write-time
        # predicate exactly -- 0/negative is exactly the "write-time guard
        # bypassed" scenario AC9 describes, not just nan/inf/non-numeric.
        for damaged_value in ("not-a-number", float("nan"), float("inf"),
                              float("-inf"), 0.0, -50.0):
            with self.subTest(damaged_value=damaged_value):
                task_id = "damaged-" + re_safe(damaged_value)
                code, _ = self.c.run("task", "upsert", "--id", task_id,
                                     "--size", "S", "--state", "building")
                self.assertEqual(code, 0)
                with contextlib.closing(
                    sqlite3.connect(self.c.db)
                ) as cx, cx:
                    cx.execute(
                        "UPDATE tasks SET started_at=0.0, "
                        "budget_seconds=? WHERE id=?",
                        (damaged_value, task_id),
                    )
                # Must not raise (code would be 1, an uncaught traceback,
                # if it did) and must not manufacture budget_expired.
                task = self.show(task_id, now=1_000_000.0)
                self.assertEqual(task["reason_code"], "not_pending")
                self.assertFalse(task["dispatchable"])


class TaskExtendTest(unittest.TestCase):
    """Issue #217 / ADR-0046: one durable, bounded extension for an expired
    task (`task extend`), and the amended budget_expired expiry formula.

    Fixtures reuse TaskBudgetExpiryTest's own `seed_started` idiom (direct
    sqlite3 writes to started_at/budget_seconds) -- `task extend` itself is
    the CLI surface under test, but reaching a "currently budget_expired"
    fixture row still goes through the same direct-write convention this
    file already established, not a real admission grant.
    """

    def setUp(self):
        self.c = Ctl()

    def show(self, task_id, now=None):
        code, body = self.c.run("task", "show", "--id", task_id, now=now)
        self.assertEqual(code, 0, body)
        return body["task"]

    def seed_started(self, task_id, *, state, started_at, budget_seconds,
                      size="S"):
        code, _ = self.c.run("task", "upsert", "--id", task_id,
                             "--size", size, "--state", state)
        self.assertEqual(code, 0)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE tasks SET started_at=?, budget_seconds=? WHERE id=?",
                (started_at, budget_seconds, task_id),
            )

    def extend(self, task_id, *, seconds, reason, now=None):
        # `--seconds=<value>` (one token), not `--seconds`, `<value>` (two
        # tokens): argparse's negative-number heuristic only recognizes a
        # bare `-50`-shaped token after an option, not `-inf` -- same
        # workaround tests/test_claims.py's ClaimRenewRejectsGarbageTTL
        # already needs for `--ttl=-inf`.
        return self.c.run("task", "extend", "--id", task_id,
                          f"--seconds={seconds}", "--reason", reason,
                          now=now)

    def raw_columns(self, task_id):
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx:
            cx.row_factory = sqlite3.Row
            return dict(cx.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone())

    # --- AC1: additive, nullable schema, excluded from task upsert ------

    def test_extension_columns_are_additive_and_nullable(self):
        self.c.run("task", "upsert", "--id", "no-ext", "--size", "S")
        cols = self.raw_columns("no-ext")
        for col in ("extension_seconds", "extension_reason", "extended_at"):
            self.assertIn(col, cols)
            self.assertIsNone(cols[col])

    def test_task_upsert_cannot_set_extension_fields(self):
        """There is no --extension-seconds/--extension-reason/--extended-at
        flag; `task upsert`'s generic whitelist stays structurally
        incapable of writing them (same guarantee ADR-0043 established for
        started_at)."""
        for flag, value in (("--extension-seconds", "900"),
                            ("--extension-reason", "x"),
                            ("--extended-at", "1")):
            with self.subTest(flag=flag):
                code, body = self.c.run(
                    "task", "upsert", "--id", "t-noflag", flag, value,
                )
                self.assertNotEqual(code, 0)
                self.assertIsNone(body)

    # --- AC0: CLI surface -------------------------------------------------

    def test_extend_requires_id_seconds_and_reason(self):
        for argv in (
            ("task", "extend", "--seconds", "60", "--reason", "x"),
            ("task", "extend", "--id", "t", "--reason", "x"),
            ("task", "extend", "--id", "t", "--seconds", "60"),
        ):
            with self.subTest(argv=argv):
                code, body = self.c.run(*argv)
                self.assertNotEqual(code, 0)
                self.assertIsNone(body)

    # --- AC2: --seconds must be finite and positive ----------------------

    def test_seconds_must_be_finite_and_positive(self):
        self.seed_started("bad-seconds", state="building", started_at=0.0,
                          budget_seconds=1.0)
        for bad in ("0", "-50", "nan", "inf", "-inf"):
            with self.subTest(bad=bad):
                code, body = self.extend("bad-seconds", seconds=bad,
                                         reason="x", now=100.0)
                self.assertEqual(code, 2, body)
                self.assertFalse(body["ok"])
                # Writes nothing.
                self.assertIsNone(
                    self.raw_columns("bad-seconds")["extension_seconds"])

    def test_valid_seconds_control(self):
        self.seed_started("good-seconds", state="building", started_at=0.0,
                          budget_seconds=1.0)
        code, body = self.extend("good-seconds", seconds="900",
                                 reason="tests green", now=100.0)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])

    # --- AC3: --reason is required and non-empty --------------------------

    def test_reason_must_be_non_empty(self):
        self.seed_started("bad-reason", state="building", started_at=0.0,
                          budget_seconds=1.0)
        for bad in ("", "   "):
            with self.subTest(bad=repr(bad)):
                code, body = self.extend("bad-reason", seconds="60",
                                         reason=bad, now=100.0)
                self.assertEqual(code, 2, body)
                self.assertFalse(body["ok"])
                self.assertIsNone(
                    self.raw_columns("bad-reason")["extension_seconds"])

    def test_reason_is_stored_verbatim(self):
        self.seed_started("store-reason", state="building", started_at=0.0,
                          budget_seconds=1.0)
        code, body = self.extend(
            "store-reason", seconds="60",
            reason="tests green; one review fix remains", now=100.0)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["extension_reason"],
                         "tests green; one review fix remains")
        self.assertEqual(
            self.raw_columns("store-reason")["extension_reason"],
            "tests green; one review fix remains")

    # --- AC4c: unknown task -------------------------------------------

    def test_unknown_task_is_denied_exit_3(self):
        code, body = self.extend("does-not-exist", seconds="60",
                                 reason="x")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["ok"])
        self.assertFalse(body["granted"])

    # --- AC4d: value-shape checked before any transaction/DB lookup ----

    def test_bad_seconds_wins_over_unknown_task(self):
        """A request invalid in shape is refused before the task lookup
        even runs -- exit 2, not exit 3, for a task that doesn't exist."""
        code, body = self.extend("also-does-not-exist", seconds="-1",
                                 reason="x")
        self.assertEqual(code, 2, body)

    # --- AC4b: only a CURRENTLY budget_expired task may be extended -----

    def test_extend_denied_when_not_currently_budget_expired(self):
        # Pending (never started).
        self.c.run("task", "upsert", "--id", "still-pending", "--size", "S")
        code, body = self.extend("still-pending", seconds="60", reason="x")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "not_budget_expired")

        # Started, but still inside budget.
        self.seed_started("in-budget", state="building", started_at=0.0,
                          budget_seconds=1000.0)
        code, body = self.extend("in-budget", seconds="60", reason="x",
                                 now=1.0)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "not_budget_expired")

        # Terminal state, budget lapsed -- still not_budget_expired
        # (terminal always wins, per AC6/ADR-0045's own precedence order).
        self.seed_started("terminal-done", state="done", started_at=0.0,
                          budget_seconds=1.0)
        code, body = self.extend("terminal-done", seconds="60", reason="x",
                                 now=1_000_000.0)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "not_budget_expired")

        # None of the above wrote anything.
        for task_id in ("still-pending", "in-budget", "terminal-done"):
            self.assertIsNone(
                self.raw_columns(task_id)["extension_seconds"])

    # --- AC5: started_at is never touched --------------------------------

    def test_extend_never_writes_started_at(self):
        self.seed_started("keep-started-at", state="building",
                          started_at=42.0, budget_seconds=1.0)
        code, body = self.extend("keep-started-at", seconds="60",
                                 reason="x", now=100.0)
        self.assertEqual(code, 0, body)
        self.assertEqual(
            self.raw_columns("keep-started-at")["started_at"], 42.0)

    # --- AC6: effective budget = original + extension, at the boundary --

    def test_effective_budget_extends_the_expiry_window_at_the_boundary(self):
        self.seed_started("extend-boundary", state="building",
                          started_at=1_000.0, budget_seconds=100.0)
        # Control: expired under the ORIGINAL budget alone.
        self.assertEqual(
            self.show("extend-boundary", now=1_100.0)["reason_code"],
            "budget_expired")
        code, body = self.extend("extend-boundary", seconds="50",
                                 reason="tests green", now=1_100.0)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["effective_budget_seconds"], 150.0)
        # One second before the NEW boundary (1000 + 100 + 50 = 1150):
        # back to eligible/not_pending, not budget_expired.
        still_ok = self.show("extend-boundary", now=1_149.0)
        self.assertEqual(still_ok["reason_code"], "not_pending")
        # Exactly at the extended boundary: expired again (AC7).
        at_boundary = self.show("extend-boundary", now=1_150.0)
        self.assertEqual(at_boundary["reason_code"], "budget_expired")

    # --- AC8: no extension leaves readiness byte-identical ----------------

    def test_extension_seconds_null_leaves_readiness_unaffected(self):
        self.seed_started("no-ext-readiness", state="building",
                          started_at=1_000.0, budget_seconds=100.0)
        self.assertIsNone(
            self.raw_columns("no-ext-readiness")["extension_seconds"])
        before = self.show("no-ext-readiness", now=1_099.0)
        self.assertEqual(before["reason_code"], "not_pending")
        at_boundary = self.show("no-ext-readiness", now=1_100.0)
        self.assertEqual(at_boundary["reason_code"], "budget_expired")

    # --- AC6 (damaged-value guard, mirrors #215's own AC9) ----------------

    def test_damaged_extension_seconds_is_treated_as_no_extension(self):
        self.seed_started("damaged-ext", state="building", started_at=0.0,
                          budget_seconds=10.0)
        with contextlib.closing(sqlite3.connect(self.c.db)) as cx, cx:
            cx.execute(
                "UPDATE tasks SET extension_seconds=? WHERE id=?",
                (float("nan"), "damaged-ext"),
            )
        # Must not raise and must not extend the window: still expired at
        # exactly the ORIGINAL boundary, as if no extension existed.
        task = self.show("damaged-ext", now=10.0)
        self.assertEqual(task["reason_code"], "budget_expired")
        self.assertFalse(task["dispatchable"])

    # --- AC4a / AC7: exactly one extension, ever, even across a second ---
    # --- expiry of the extended deadline (the highest-risk seam) ---------

    def test_second_extension_attempt_is_refused(self):
        self.seed_started("one-shot", state="building", started_at=0.0,
                          budget_seconds=10.0)
        first = self.extend("one-shot", seconds="5", reason="first",
                            now=10.0)
        self.assertEqual(first[0], 0, first[1])
        # Immediately retrying, while still within the extended window,
        # is refused as "not currently budget_expired" (AC4b gates first).
        code, body = self.extend("one-shot", seconds="5", reason="again",
                                 now=12.0)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "not_budget_expired")

    def test_expired_again_after_extension_still_refuses_a_second_extension(
            self):
        """AC7, the ticket's own named highest-risk seam: once the
        EXTENDED deadline also lapses, readiness reports budget_expired
        again, but that must not read as a fresh eligibility window -- a
        second `task extend` call still hits AC4a's denial, not a fresh
        grant. Asserts both facts together, not just one, per the DoR
        verdict's own AC7 requirement."""
        self.seed_started("expire-twice", state="building", started_at=0.0,
                          budget_seconds=10.0)
        first = self.extend("expire-twice", seconds="5", reason="first",
                            now=10.0)
        self.assertEqual(first[0], 0, first[1])
        # Advance past the EXTENDED boundary (0 + 10 + 5 = 15).
        task = self.show("expire-twice", now=15.0)
        self.assertEqual(task["reason_code"], "budget_expired")
        self.assertFalse(task["dispatchable"])
        code, body = self.extend("expire-twice", seconds="5",
                                 reason="second", now=15.0)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason_code"], "already_extended")
        # Still exactly one extension recorded -- the second attempt wrote
        # nothing new.
        row = self.raw_columns("expire-twice")
        self.assertEqual(row["extension_seconds"], 5.0)
        self.assertEqual(row["extension_reason"], "first")

    # --- AC9: success payload surfaces what was recorded -------------------

    def test_success_payload_surfaces_the_recorded_extension(self):
        self.seed_started("payload", state="building", started_at=0.0,
                          budget_seconds=10.0)
        code, body = self.extend("payload", seconds="30",
                                 reason="one review fix remains", now=10.0)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["granted"])
        self.assertEqual(body["task_id"], "payload")
        self.assertEqual(body["extension_seconds"], 30.0)
        self.assertEqual(body["extension_reason"],
                         "one review fix remains")
        self.assertEqual(body["effective_budget_seconds"], 40.0)
        self.assertIsInstance(body["extended_at"], float)


def re_safe(value):
    """A filesystem/task-id-safe token for a damaged test value."""
    return str(value).replace(".", "_").replace("-", "neg")


if __name__ == "__main__":
    unittest.main()
