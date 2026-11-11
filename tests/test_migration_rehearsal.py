"""ADR-0082 phases 1-4, rehearsed against real legacy material
(bin/nightshift_migrate.py).

THE FIXTURE IS THIS REPOSITORY'S OWN `.omni/`, COPIED
=====================================================
`control.db`, `ledger.md` and `decisions.md` -- the mixture of control
plane, private narrative and loose JSON that ADR-0082 phase 1 exists to
classify. Rehearsing against a synthetic three-file fixture would prove
the classifier can classify a fixture.

WHAT THIS SUITE MUST NOT DO IS DICTATE THE REPOSITORY'S CONTENTS. It used
to require more than 100 files, which meant 73 daily status reports could
never be untracked without turning this suite red -- a test holding run
exhaust in the tree so that it had something to copy. Seventy-three
reports are seventy-three instances of ONE class; they add no coverage
the other files do not already give. The floor is now the MIXTURE, which
is what the classifier is actually exercised by.

But the rehearsal runs against a COPY placed under the test's own
`state_root`, NEVER the live `.omni/`. Operating on the tracked original
would itself be the footprint violation `tests/test_zero_footprint.py`
exists to catch -- and 113 of those 114 files are tracked in git, so a
single stray write would show up in `git status` as the suite's own damage.
`SourceIsNeverTouched` proves the copy is intact byte-for-byte afterwards.

WHAT THIS SUITE PINS
====================
  Classification            the five classes, and that `unknown` is
                            reported rather than guessed or deleted.
  InventoryMutatesNothing   phase 1 over the real corpus.
  SnapshotIsVerified        per-file sha256 on both sides, high-water
                            sequence from control.db, and the quiesce rule.
  ShadowImportIsIdempotent  byte-identical on re-run, and structurally
                            command-denying.
  CompareNamesItsMismatches phase 4 over both sides, match and mismatch.
  SourceIsNeverTouched      the whole rehearsal, end to end, footprint-free.
  PhasesFeedTheSwitch       the compare verdict is what `switch_authority`
                            reads, so phases 1-4 really do gate phase 5.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
LIVE_OMNI = REPO / ".omni"
sys.path.insert(0, str(BIN))

import nightshift_authority as auth          # noqa: E402
import nightshift_migrate as migrate         # noqa: E402

T0 = 2_000_000.0
MIGRATION = "rehearsal-001"


def _manifest(root):
    """Path -> sha256 for every regular file under `root`, plus its mode."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            h = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(full, root)] = h.hexdigest()
    return out


class MigrationRehearsalTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not LIVE_OMNI.is_dir():
            raise unittest.SkipTest(f"no legacy material at {LIVE_OMNI}")
        cls.pristine = tempfile.mkdtemp(prefix="nightshift-omni-pristine-")
        # ONE read of the live tree, into a pristine copy every test then
        # copies FROM. The live `.omni/` is never touched again by this file.
        shutil.copytree(LIVE_OMNI, os.path.join(cls.pristine, ".omni"),
                        symlinks=True)
        cls._seed_control_db(os.path.join(cls.pristine, ".omni", "control.db"))

    @staticmethod
    def _seed_control_db(path):
        """Write a deterministic control.db into the fixture, ALWAYS --
        replacing any copied one rather than only filling a gap.

        `.omni/control.db*` is gitignored (.gitignore:11), so the live file
        exists on a developer's machine and does NOT exist in a CI checkout.
        Copying whatever happens to be there made these tests pass locally and
        fail in CI with `'control.db' not found in {}`.

        Filling in only when absent would be worse than useless: local and CI
        would then exercise DIFFERENT data, which is the same shape of defect.
        So the fixture owns its database outright. What these tests need from
        it is structural -- readable, more than one table, a real MAX(rowid) to
        serve as ADR-0082's high-water mark -- not this repository's actual
        rows.
        """
        if os.path.exists(path):
            os.remove(path)
        # Issue #719. `copytree` brings `control.db-wal`/`-shm` across when a
        # developer's live plane has them, and replacing only `control.db`
        # left those sidecars beside a database they do not belong to. Two
        # consequences, and the second is the worse one:
        #
        #   - `test_reading_the_high_water_never_writes_wal_files_into_the
        #     _source` asserts no `-wal`/`-shm` exist after the read. It was
        #     decided entirely by whether the developer's own control plane
        #     happened to have an open WAL: green in CI, where `.omni/control
        #     .db*` is gitignored and nothing is copied, red locally.
        #   - A WAL sidecar names the database it was written for. Left beside
        #     a reseeded `control.db`, it is incoherent state that SQLite may
        #     read -- so the fixture was not only flaky, it was not the
        #     deterministic fixture this function's docstring promises.
        #
        # Removed here rather than skipped in the copy, so the guarantee lives
        # with the seeding that creates the database it is about.
        for sidecar in (path + "-wal", path + "-shm"):
            if os.path.exists(sidecar):
                os.remove(sidecar)
        cx = sqlite3.connect(path)
        try:
            cx.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE tasks(
                    task_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    state TEXT NOT NULL);
                CREATE TABLE sequences(
                    name TEXT PRIMARY KEY, next_value INTEGER NOT NULL);
            """)
            cx.execute("INSERT INTO meta VALUES('schema_version','1')")
            cx.executemany(
                "INSERT INTO tasks(task_id,title,state) VALUES(?,?,?)",
                [(f"T-{i:03d}", f"legacy task {i}", "integrated")
                 for i in range(1, 25)])
            cx.executemany(
                "INSERT INTO sequences(name,next_value) VALUES(?,?)",
                [("adr", 84), ("task", 25)])
            cx.commit()
        finally:
            cx.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pristine, ignore_errors=True)

    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="nightshift-migrate-")
        os.chmod(self.state_root, 0o700)
        self.addCleanup(self._rmtree, self.state_root)
        self.repo_path = tempfile.mkdtemp(prefix="nightshift-legacy-repo-")
        self.addCleanup(self._rmtree, self.repo_path)
        shutil.copytree(os.path.join(self.pristine, ".omni"),
                        os.path.join(self.repo_path, ".omni"), symlinks=True)
        os.makedirs(os.path.join(self.repo_path, "docs"), exist_ok=True)
        with open(os.path.join(self.repo_path, "docs", "approved.md"), "w") as fh:
            fh.write("# reviewed document\n")

    @staticmethod
    def _rmtree(path):
        for dirpath, dirnames, filenames in os.walk(path):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700)
                except OSError:
                    pass
            try:
                os.chmod(dirpath, 0o700)
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=True)

    def _refusal(self, callable_, reason_code):
        with self.assertRaises(migrate.MigrationError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.reason_code, reason_code)
        return ctx.exception

    def _full_run(self):
        inv = migrate.inventory(self.repo_path, self.state_root,
                                migration_id=MIGRATION, t=T0)
        snap = migrate.snapshot(self.repo_path, self.state_root,
                                migration_id=MIGRATION, t=T0 + 1)
        imp = migrate.shadow_import(self.state_root, migration_id=MIGRATION, t=T0 + 2)
        cmp_ = migrate.compare(self.state_root, migration_id=MIGRATION, t=T0 + 3)
        return inv, snap, imp, cmp_


class Classification(MigrationRehearsalTestCase):

    def test_the_five_classes_land_where_ADR_0082_says_they_do(self):
        cases = {
            ".omni/control.db": "control",
            ".omni/control-backup.json": "control",
            ".omni/capacity.json": "control",
            ".omni/registry-crew_alpha.json": "control",
            ".omni/leases/anything.json": "control",
            ".omni/ledger.md": "memory",
            ".omni/decisions.md": "memory",
            ".omni/reviews.md": "memory",
            ".omni/handoffs/2026-08-01.md": "memory",
            ".omni/evidence/run-7/out.txt": "memory",
            "docs/decisions/ADR-0082-x.md": "approved_document",
            ".omni/__pycache__/x.pyc": "disposable",
            ".omni/scratch.tmp": "disposable",
            ".omni/.DS_Store": "disposable",
            ".omni/who-owns-this.json": "unknown",
            ".omni/mystery/thing.bin": "unknown",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(migrate.classify(path), expected)

    def test_every_class_is_one_of_the_five_declared_names(self):
        report = migrate.inventory(self.repo_path, self.state_root,
                                   migration_id=MIGRATION, t=T0)
        for entry in report["entries"]:
            self.assertIn(entry["class"], migrate.CLASSES)

    def test_unknown_is_reported_and_neither_guessed_nor_deleted(self):
        """ADR-0082: migrate "without inventing authority". Guessing
        unknown->disposable invents the authority to delete someone's file;
        guessing unknown->memory invents the authority to import it into a
        store with different retention."""
        stray = os.path.join(self.repo_path, ".omni", "who-owns-this.json")
        with open(stray, "w") as fh:
            fh.write('{"owner": null}\n')
        report = migrate.inventory(self.repo_path, self.state_root,
                                   migration_id=MIGRATION, t=T0)
        self.assertIn(".omni/who-owns-this.json", report["unknown"])
        self.assertTrue(os.path.isfile(stray), "an unknown file must survive")
        self.assertEqual(report["mutations"], [])
        self.assertIn("neither guessed nor deleted", report["note"])

    def test_the_module_contains_no_delete_of_source_material(self):
        """Structural: phase 1-4 has no authority to remove anything from
        the legacy tree. ADR-0082 puts deletion in phase 6, behind a
        reviewed Publication Gateway operation."""
        src = (BIN / "nightshift_migrate.py").read_text()
        for forbidden in ("os.remove(src", "os.unlink(src", "shutil.rmtree(repo_path",
                          "shutil.rmtree(legacy_dir"):
            self.assertNotIn(forbidden, src)


class InventoryMutatesNothing(MigrationRehearsalTestCase):

    def _assert_mixture(self, paths):
        """The corpus must contain each CLASS the classifier sorts, which is
        what makes this a rehearsal rather than a fixture exercise. A count
        cannot say that -- seventy-three daily reports are one class seventy-three
        times, and requiring a hundred files only forced the repository to keep
        them."""
        names = {str(p) for p in paths}
        for needle, what in (("control.db", "the control plane"),
                             ("ledger.md", "private narrative"),
                             (".json", "loose JSON")):
            self.assertTrue(any(needle in n for n in names),
                            f"corpus is missing {what} ({needle})")

    def test_inventory_over_the_real_corpus_leaves_it_byte_identical(self):
        before = _manifest(self.repo_path)
        self.assertGreaterEqual(len(before), 20,
                                "the fixture must be the real .omni/ corpus")
        self._assert_mixture(before)
        report = migrate.inventory(self.repo_path, self.state_root,
                                   migration_id=MIGRATION, t=T0)
        after = _manifest(self.repo_path)
        self.assertEqual(before, after)
        self.assertGreater(report["counts"]["control"], 0)
        self.assertGreater(report["counts"]["memory"], 0)
        self.assertGreaterEqual(report["file_count"], 20)

    def test_the_report_is_written_under_state_root_never_under_repo_path(self):
        """Writing the inventory into the tree being inventoried would
        change the thing being measured, and would be a footprint violation
        on the very first run."""
        report = migrate.inventory(self.repo_path, self.state_root,
                                   migration_id=MIGRATION, t=T0)
        self.assertTrue(report["report_path"].startswith(self.state_root + os.sep))
        self.assertFalse(report["report_path"].startswith(self.repo_path + os.sep))
        with open(report["report_path"]) as fh:
            self.assertEqual(json.load(fh)["migration_id"], MIGRATION)

    def test_worktrees_and_refs_are_enumerated_when_git_is_available(self):
        subprocess.run(["git", "init", "-q"], cwd=self.repo_path, check=True,
                       capture_output=True)
        report = migrate.inventory(self.repo_path, self.state_root,
                                   migration_id=MIGRATION, t=T0)
        self.assertTrue(report["worktrees"]["available"])
        self.assertTrue(any("worktree" in e for e in report["worktrees"]["entries"]))
        self.assertTrue(report["refs"]["available"])

    def test_a_non_git_tree_reports_unavailable_rather_than_claiming_zero(self):
        """"git is not here" and "there are no refs" are different answers,
        and only one of them means the tree has no refs."""
        report = migrate.inventory(self.repo_path, self.state_root,
                                   migration_id=MIGRATION, t=T0)
        self.assertFalse(report["refs"]["available"])
        self.assertIsNotNone(report["refs"]["error"])


class SnapshotIsVerified(MigrationRehearsalTestCase):

    def test_every_copied_file_is_rehashed_on_both_sides(self):
        report = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0)
        src_root = os.path.join(self.repo_path, ".omni")
        dst_root = os.path.join(report["snapshot_path"], ".omni")
        self.assertGreaterEqual(report["file_count"], 20)
        for rel, entry in report["manifest"].items():
            self.assertEqual(migrate.sha256_file(os.path.join(src_root, rel)),
                              entry["sha256"], rel)
            self.assertEqual(migrate.sha256_file(os.path.join(dst_root, rel)),
                              entry["sha256"], rel)

    def test_verify_copy_refuses_when_the_two_sides_really_differ(self):
        """The refusal exercised on real files with genuinely different
        bytes, not asserted about."""
        a = os.path.join(self.state_root, "a.bin")
        b = os.path.join(self.state_root, "b.bin")
        with open(a, "wb") as fh:
            fh.write(b"original")
        with open(b, "wb") as fh:
            fh.write(b"corrupted")
        exc = self._refusal(lambda: migrate.verify_copy(a, b, "a.bin"),
                             "snapshot_digest_mismatch")
        self.assertEqual(exc.details["path"], "a.bin")
        self.assertNotEqual(exc.details["source_digest"], exc.details["copy_digest"])
        # ... and returns the digest, unchanged, when they agree.
        shutil.copyfile(a, b)
        self.assertEqual(migrate.verify_copy(a, b, "a.bin"), migrate.sha256_file(a))

    def test_the_high_water_mark_is_a_real_sequence_from_control_db(self):
        """ADR-0082 phase 4 replays "from the high-water mark", so the mark
        has to be a sequence position, not a row count -- a count cannot say
        where to resume after rows were deleted."""
        report = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0)
        control = report["high_water"].get("control.db")
        self.assertIsNotNone(control, report["high_water"].keys())
        self.assertTrue(control["readable"], control)
        self.assertGreater(len(control["tables"]), 0)
        self.assertIn("max_rowid", control)
        self.assertTrue(any(v is not None for v in control["max_rowid"].values()),
                         "at least one real table must report a MAX(rowid)")

    def test_seeding_removes_sidecars_left_by_the_copied_tree(self):
        """Issue #719, tested on the function rather than on the environment.

        The ambient version of this check is only meaningful when the
        developer's own control plane happens to have an open WAL -- which is
        precisely the environment-decided outcome being fixed, and it would
        make this test pass for free on a machine where the bug cannot occur.
        So the condition is constructed: a `control.db` with both sidecars
        beside it, exactly what `copytree` produces from a live plane.
        """
        scratch = tempfile.mkdtemp(prefix="nightshift-719-")
        self.addCleanup(shutil.rmtree, scratch, True)
        db = os.path.join(scratch, "control.db")
        for name in (db, db + "-wal", db + "-shm"):
            with open(name, "wb") as handle:
                handle.write(b"stale bytes from another database")
        type(self)._seed_control_db(db)
        self.assertTrue(os.path.exists(db))
        self.assertFalse(os.path.exists(db + "-wal"), "the -wal survived")
        self.assertFalse(os.path.exists(db + "-shm"), "the -shm survived")

    def test_the_fixture_carries_no_sidecars_from_the_live_plane(self):
        """Issue #719: the premise the test below rests on.

        Without this, that test asserts an absence the fixture never
        guaranteed -- and its outcome is decided by whether the developer's
        own control plane happened to have an open WAL at copy time. It passed
        in CI, where `.omni/control.db*` is gitignored, and failed locally.
        """
        listing = os.listdir(os.path.join(self.repo_path, ".omni"))
        self.assertEqual(
            [n for n in listing if n.endswith(("-wal", "-shm"))], [],
            "the fixture inherited WAL sidecars from the live control plane")

    def test_reading_the_high_water_never_writes_wal_files_into_the_source(self):
        """`sqlite3.connect` on a WAL database creates `-shm`/`-wal`
        siblings. Doing that inside the source tree would be a write into
        the tree phases 1-2 promised not to touch."""
        before = sorted(os.listdir(os.path.join(self.repo_path, ".omni")))
        migrate.snapshot(self.repo_path, self.state_root,
                         migration_id=MIGRATION, t=T0)
        after = sorted(os.listdir(os.path.join(self.repo_path, ".omni")))
        self.assertEqual(before, after)
        self.assertFalse(any(n.endswith(("-wal", "-shm")) for n in after))

    def test_the_snapshot_is_marked_retained_and_read_only(self):
        report = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0)
        marker = os.path.join(report["snapshot_path"], auth.RETAINED_SNAPSHOT_MARKER)
        self.assertTrue(os.path.isfile(marker))
        sample = next(iter(report["manifest"]))
        mode = os.stat(os.path.join(report["snapshot_path"], ".omni",
                                    sample)).st_mode & 0o777
        self.assertEqual(mode, 0o400)

    def test_that_marker_is_exactly_what_rollback_refuses(self):
        """The two modules must agree on ONE spelling; a second spelling is
        a second, silently-divergent definition of "stale snapshot"."""
        self.assertEqual(migrate.RETAINED_SNAPSHOT_MARKER,
                          auth.RETAINED_SNAPSHOT_MARKER)
        report = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0)
        cx = auth.connect(self.state_root, 42)
        self.addCleanup(cx.close)
        auth.init_binding(cx, repository_id=42, t=T0)
        auth.record_compare_verdict(cx, migration_id=MIGRATION, verdict="match",
                                    mismatch_count=0, t=T0)
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        auth.switch_authority(cx, migration_id=MIGRATION, from_backend="legacy",
                              to_backend="external", from_epoch=1,
                              active_leases=[], t=T0)
        with self.assertRaises(auth.AuthorityError) as ctx:
            auth.rollback_authority(cx, migration_id="r",
                                     fresh_legacy_root=report["snapshot_path"],
                                     from_epoch=2, t=T0)
        self.assertEqual(ctx.exception.reason_code, "stale_snapshot_reopen")

    def test_snapshot_refuses_an_unquiesced_binding_when_one_is_supplied(self):
        """ADR-0082 phase 2 is "Briefly quiesce to capture a CONSISTENT
        snapshot". A snapshot of a store still accepting writes is not
        consistent, and calling it consistent is exactly the kind of claim
        this repository spent 2026-09-02 removing."""
        cx = auth.connect(self.state_root, 42)
        self.addCleanup(cx.close)
        auth.init_binding(cx, repository_id=42, t=T0)
        self._refusal(
            lambda: migrate.snapshot(self.repo_path, self.state_root,
                                      migration_id=MIGRATION, t=T0,
                                      authority_cx=cx),
            "snapshot_requires_quiesce")
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        report = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0, authority_cx=cx)
        self.assertTrue(report["quiesce_verified"])
        self.assertEqual(report["consistency"], "quiesced")

    def test_an_unquiesced_rehearsal_says_so_in_its_own_report(self):
        report = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0)
        self.assertFalse(report["quiesce_verified"])
        self.assertEqual(report["consistency"], "unquiesced_rehearsal")

    def test_a_second_snapshot_over_the_read_only_first_one_succeeds(self):
        first = migrate.snapshot(self.repo_path, self.state_root,
                                 migration_id=MIGRATION, t=T0)
        second = migrate.snapshot(self.repo_path, self.state_root,
                                  migration_id=MIGRATION, t=T0 + 1)
        self.assertEqual(first["manifest_digest"], second["manifest_digest"])


class ShadowImportIsIdempotent(MigrationRehearsalTestCase):

    def test_a_second_import_rewrites_nothing(self):
        """Idempotent means byte-identical, not merely "same count": the
        manifest digest is over content, so a re-import that silently
        rewrote a file would change it."""
        migrate.snapshot(self.repo_path, self.state_root,
                         migration_id=MIGRATION, t=T0)
        first = migrate.shadow_import(self.state_root, migration_id=MIGRATION, t=T0 + 1)
        second = migrate.shadow_import(self.state_root, migration_id=MIGRATION, t=T0 + 2)
        self.assertGreaterEqual(first["imported"], 20)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["skipped_already_identical"], first["file_count"])
        self.assertEqual(first["manifest_digest"], second["manifest_digest"])
        self.assertEqual(first["manifest_digest"], first["source_manifest_digest"])

    def test_the_shadow_namespace_denies_every_live_command(self):
        migrate.snapshot(self.repo_path, self.state_root,
                         migration_id=MIGRATION, t=T0)
        report = migrate.shadow_import(self.state_root, migration_id=MIGRATION,
                                       t=T0 + 1)
        self.assertFalse(report["accepts_live_commands"])
        with self.assertRaises(auth.AuthorityError) as ctx:
            auth.guard_connect(report["shadow_path"])
        self.assertEqual(ctx.exception.reason_code, "shadow_namespace_denies_commands")

    def test_the_denial_is_structural_not_a_policy_flag(self):
        """There is no `authority.db` anywhere under the migration
        workspace, and there is no code path that creates one there.
        Allowing commands would require a SECOND authority binding, which is
        the dual authority ADR-0082 forbids."""
        migrate.snapshot(self.repo_path, self.state_root,
                         migration_id=MIGRATION, t=T0)
        migrate.shadow_import(self.state_root, migration_id=MIGRATION, t=T0 + 1)
        root = migrate.migration_root(self.state_root, MIGRATION)
        found = []
        for dirpath, _dirnames, filenames in os.walk(root):
            found += [os.path.join(dirpath, n) for n in filenames
                      if n == auth.AUTHORITY_DB_FILENAME]
        self.assertEqual(found, [])
        # No CALL to either verb exists in the migration module. (Both
        # names appear in its docstring, saying that the flip lives
        # elsewhere -- which is the point, not a violation.)
        src = (BIN / "nightshift_migrate.py").read_text()
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith(("#", "--")))
        for call in ("init_binding(", "switch_authority(", "rollback_authority("):
            self.assertNotIn(call, code,
                              f"nightshift_migrate must never call {call}")

    def test_the_whole_migration_workspace_is_command_denying_from_phase_one(self):
        """The marker goes down when the workspace is created, not when the
        shadow import runs -- otherwise there is a window in which the
        workspace looks like an ordinary state root."""
        migrate.inventory(self.repo_path, self.state_root,
                          migration_id=MIGRATION, t=T0)
        root = migrate.migration_root(self.state_root, MIGRATION)
        self.assertTrue(os.path.isfile(auth.shadow_marker_path(root)))
        with self.assertRaises(auth.AuthorityError) as ctx:
            auth.guard_connect(os.path.join(root, "anything", "deeper"))
        self.assertEqual(ctx.exception.reason_code, "shadow_namespace_denies_commands")


class CompareNamesItsMismatches(MigrationRehearsalTestCase):

    def test_a_faithful_import_compares_match(self):
        _inv, _snap, _imp, report = self._full_run()
        self.assertEqual(report["verdict"], "match", report["mismatches"][:5])
        self.assertEqual(report["mismatch_count"], 0)
        self.assertGreaterEqual(report["legacy_snapshot"]["file_count"], 20)
        self.assertEqual(report["legacy_snapshot"]["manifest_digest"],
                          report["shadow"]["manifest_digest"])

    def test_nsc_v1_reads_cover_counts_checksums_samples_and_databases(self):
        self._full_run()
        reads = migrate.nsc_v1_reads(migrate.shadow_root(self.state_root, MIGRATION))
        for key in ("file_count", "total_bytes", "manifest_digest",
                    "sampled_bytes", "databases"):
            self.assertIn(key, reads)
        self.assertGreater(len(reads["sampled_bytes"]), 0)
        self.assertIn("control.db", reads["databases"])
        self.assertTrue(reads["databases"]["control.db"]["readable"])

    def test_one_flipped_byte_on_the_shadow_side_is_named(self):
        self._full_run()
        shadow = os.path.join(migrate.shadow_root(self.state_root, MIGRATION),
                              ".omni", "ledger.md")
        with open(shadow, "r+b") as fh:
            fh.seek(0)
            first = fh.read(1)
            fh.seek(0)
            fh.write(b"X" if first != b"X" else b"Y")
        report = migrate.compare(self.state_root, migration_id=MIGRATION, t=T0 + 9)
        self.assertEqual(report["verdict"], "mismatch")
        checks = {m["check"] for m in report["mismatches"]}
        self.assertIn("content_digest", checks)
        self.assertIn("manifest_digest", checks)
        named = [m for m in report["mismatches"] if m["check"] == "content_digest"]
        # Paths in a compare report are relative to the legacy directory
        # each side was read from (`<side>/.omni`), which is the only scope
        # in which they are unambiguous -- the two sides live under
        # different absolute roots by construction.
        self.assertEqual([m["path"] for m in named], ["ledger.md"],
                          "the mismatch must NAME the file, not just count one")

    def test_a_file_missing_from_the_shadow_side_is_named(self):
        self._full_run()
        victim = os.path.join(migrate.shadow_root(self.state_root, MIGRATION),
                              ".omni", "capacity.json")
        os.remove(victim)
        report = migrate.compare(self.state_root, migration_id=MIGRATION, t=T0 + 9)
        self.assertEqual(report["verdict"], "mismatch")
        missing = [m for m in report["mismatches"] if m["check"] == "missing_on_shadow"]
        self.assertEqual([m["path"] for m in missing], ["capacity.json"])

    def test_a_database_row_added_on_one_side_only_is_caught_by_the_nsc_read(self):
        """This is the check a plain file-digest comparison would miss if
        SQLite happened to reuse a page: the NSC-v1 read compares MAX(rowid)
        and per-table row counts, not just bytes."""
        self._full_run()
        shadow_db = os.path.join(migrate.shadow_root(self.state_root, MIGRATION),
                                 ".omni", "control.db")
        import sqlite3
        cx = sqlite3.connect(shadow_db)
        try:
            cx.execute("CREATE TABLE migration_probe(id INTEGER PRIMARY KEY, v TEXT)")
            cx.execute("INSERT INTO migration_probe(v) VALUES('divergence')")
            cx.commit()
        finally:
            cx.close()
        report = migrate.compare(self.state_root, migration_id=MIGRATION, t=T0 + 9)
        self.assertEqual(report["verdict"], "mismatch")
        db_checks = [m for m in report["mismatches"]
                     if m["check"] == "nsc_v1_database_read"]
        self.assertEqual([m["database"] for m in db_checks], ["control.db"])
        self.assertIn("migration_probe", db_checks[0]["shadow"]["tables"])
        self.assertNotIn("migration_probe", db_checks[0]["legacy"]["tables"])


class SourceIsNeverTouched(MigrationRehearsalTestCase):

    def test_the_whole_rehearsal_leaves_the_legacy_tree_byte_identical(self):
        before = _manifest(self.repo_path)
        self._full_run()
        after = _manifest(self.repo_path)
        self.assertEqual(before, after,
                          "phases 1-4 must not write a single byte into the "
                          "legacy tree")

    def test_the_live_repository_omni_is_never_the_operand(self):
        """Belt and braces on the rule that matters most for this file: the
        rehearsal's repo_path is a tempdir, never the checkout."""
        self.assertTrue(self.repo_path.startswith(tempfile.gettempdir())
                        or self.repo_path.startswith("/private/"))
        self.assertNotEqual(os.path.realpath(self.repo_path),
                            os.path.realpath(str(REPO)))
        live_before = _manifest(str(LIVE_OMNI))
        self._full_run()
        self.assertEqual(live_before, _manifest(str(LIVE_OMNI)))


class PhasesFeedTheSwitch(MigrationRehearsalTestCase):

    def test_the_compare_verdict_is_what_gates_the_flip(self):
        """Phases 1-4 are not decoration: `switch_authority` reads the
        recorded verdict, so a mismatched compare structurally cannot be
        followed by a cutover."""
        cx = auth.connect(self.state_root, 42)
        self.addCleanup(cx.close)
        auth.init_binding(cx, repository_id=42, t=T0)

        migrate.snapshot(self.repo_path, self.state_root, migration_id=MIGRATION, t=T0)
        migrate.shadow_import(self.state_root, migration_id=MIGRATION, t=T0 + 1)
        victim = os.path.join(migrate.shadow_root(self.state_root, MIGRATION),
                              ".omni", "capacity.json")
        os.remove(victim)
        bad = migrate.compare(self.state_root, migration_id=MIGRATION, t=T0 + 2)
        auth.record_compare_verdict(cx, migration_id=MIGRATION,
                                    verdict=bad["verdict"],
                                    mismatch_count=bad["mismatch_count"],
                                    detail_json=json.dumps(bad["mismatches"][:4]),
                                    t=T0 + 2)
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0 + 3)
        with self.assertRaises(auth.AuthorityError) as ctx:
            auth.switch_authority(cx, migration_id=MIGRATION, from_backend="legacy",
                                   to_backend="external", from_epoch=1,
                                   active_leases=[], t=T0 + 4)
        self.assertEqual(ctx.exception.reason_code, "compare_not_matched")
        self.assertEqual(auth.read_binding(cx)["authority_epoch"], 1)

        # Repair the shadow side, re-compare, and only now may the flip run.
        migrate.shadow_import(self.state_root, migration_id=MIGRATION, t=T0 + 5)
        good = migrate.compare(self.state_root, migration_id=MIGRATION, t=T0 + 6)
        self.assertEqual(good["verdict"], "match")
        auth.record_compare_verdict(cx, migration_id=MIGRATION, verdict="match",
                                    mismatch_count=0, t=T0 + 6)
        after = auth.switch_authority(cx, migration_id=MIGRATION,
                                       from_backend="legacy", to_backend="external",
                                       from_epoch=1, active_leases=[], t=T0 + 7)
        self.assertEqual(after["active_backend"], "external")
        self.assertEqual(after["authority_epoch"], 2)


if __name__ == "__main__":
    unittest.main()
