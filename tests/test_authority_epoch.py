"""ADR-0082 -- the external authority-epoch registry (bin/nightshift_authority.py).

Mockless: real sqlite3 over real authority.db files in real tempdirs, real
`os.kill(SIGKILL)` against a real subprocess for the crash-decidability
case, and the client-gate assertions run through the actual `connect()`
functions of the three adapters rather than against a stub.

WHAT THIS SUITE PINS
====================
  BindingBootstrap          epoch 1 / legacy / not quiesced, and the
                            external location that makes a crash decidable.
  MonotoneEpoch             the trigger, exercised through the RAW
                            connection so it proves the DATABASE refuses.
  AppendOnlyEpochLog        UPDATE and DELETE both ABORT.
  RequireEpochRefusals      the three ADR-named refusals plus the
                            newer-than-binding case.
  AbsentRegistryIsLegacy    the fail-closed rule, both directions.
  QuiescedSwitch            the single rowcount-CAS flip, its two
                            preconditions, and the two-racer case.
  RollbackIsNotAToggle      quiesce required, fresh copy required, retained
                            snapshot refused.
  ShadowNamespaceDenies     structural denial via the marker.
  ClientGateAtEveryConnect  the four wired call sites.
  CrashMidCutoverIsDecidable a real SIGKILL between quiesce and switch, and
                            an operator answering "did the flip happen?"
                            from OUTSIDE both stores.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_authority as auth          # noqa: E402
import nightshift_memory as memory           # noqa: E402
import nightshift_provenance as prov         # noqa: E402
import nightshift_publication as pub         # noqa: E402

T0 = 2_000_000.0
MIGRATION = "mig-001"


class AuthorityTestCase(unittest.TestCase):
    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="nightshift-authority-")
        os.chmod(self.state_root, 0o700)
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.repository_id = 42
        self._clear_env()

    def _clear_env(self):
        for name in (auth.ENV_BACKEND, auth.ENV_EPOCH):
            os.environ.pop(name, None)
        self.addCleanup(lambda: [os.environ.pop(n, None)
                                 for n in (auth.ENV_BACKEND, auth.ENV_EPOCH)])

    def _cx(self):
        cx = auth.connect(self.state_root, self.repository_id)
        self.addCleanup(cx.close)
        return cx

    def _bootstrapped(self):
        cx = self._cx()
        auth.init_binding(cx, repository_id=self.repository_id, t=T0)
        return cx

    def _refusal(self, callable_, reason_code):
        with self.assertRaises(auth.AuthorityError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.reason_code, reason_code)
        self.assertEqual(ctx.exception.exit_code, 3)
        return ctx.exception

    def _reach_match_verdict(self, cx, migration_id=MIGRATION):
        auth.record_compare_verdict(cx, migration_id=migration_id, verdict="match",
                                    mismatch_count=0, t=T0)


class BindingBootstrap(AuthorityTestCase):

    def test_registry_lives_outside_control_db_and_outside_omni(self):
        """ADR-0082: "A crash consults the bootstrap binding." A binding
        stored inside either store would be destroyed by the same crash that
        made the question urgent."""
        path = auth.bootstrap_path(self.state_root, self.repository_id)
        self.assertEqual(path, os.path.join(self.state_root, "authority",
                                            str(self.repository_id), "authority.db"))
        self.assertNotIn("/tenants/", path)
        self.assertNotIn("/.omni/", path)
        self.assertNotIn("/memory/", path)
        self._bootstrapped()
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)

    def test_init_seeds_epoch_one_on_legacy_not_quiesced(self):
        cx = self._bootstrapped()
        binding = auth.read_binding(cx)
        self.assertEqual(binding["active_backend"], "legacy")
        self.assertEqual(binding["authority_epoch"], 1)
        self.assertEqual(binding["phase"], "inventory")
        self.assertEqual(binding["quiesced"], 0)
        self.assertEqual(binding["repository_id"], self.repository_id)

    def test_init_is_idempotent(self):
        cx = self._bootstrapped()
        again = auth.init_binding(cx, repository_id=self.repository_id, t=T0 + 500)
        self.assertEqual(again["authority_epoch"], 1)
        self.assertEqual(len(auth.epoch_log(cx)), 1)

    def test_a_second_binding_row_is_unrepresentable(self):
        """Not "unlikely" -- a second row is a second answer to "which store
        is authoritative", i.e. the dual authority ADR-0082 forbids."""
        cx = self._bootstrapped()
        with self.assertRaises(sqlite3.IntegrityError):
            cx.execute("INSERT INTO authority_binding(id, repository_id, "
                       "active_backend, authority_epoch, phase, quiesced, updated_at) "
                       "VALUES(2, 42, 'external', 9, 'switch', 0, 1.0)")

    def test_the_registry_refuses_to_be_repointed_at_another_repository(self):
        cx = self._bootstrapped()
        self._refusal(lambda: auth.init_binding(cx, repository_id=99, t=T0),
                      "authority_repository_mismatch")

    def test_epoch_zero_is_reserved_for_the_absent_registry(self):
        """Keeping 0 distinct from 1 means "nobody bootstrapped this yet"
        and "bootstrapped and still on legacy" are different readable
        states, not one ambiguous default."""
        absent = auth.guard(self.state_root, self.repository_id)
        self.assertEqual(absent["authority_epoch"], 0)
        self.assertEqual(absent["registry"], "absent")
        self._bootstrapped()
        present = auth.guard(self.state_root, self.repository_id)
        self.assertEqual(present["authority_epoch"], 1)
        self.assertEqual(present["registry"], "present")


class MonotoneEpoch(AuthorityTestCase):

    def test_the_database_refuses_an_epoch_that_goes_backwards(self):
        cx = self._bootstrapped()
        cx.execute("UPDATE authority_binding SET authority_epoch=5, updated_at=? "
                   "WHERE id=1", (T0,))
        with self.assertRaises(sqlite3.IntegrityError):
            cx.execute("UPDATE authority_binding SET authority_epoch=4 WHERE id=1")
        self.assertEqual(auth.read_binding(cx)["authority_epoch"], 5)

    def test_an_epoch_may_stay_the_same_or_rise(self):
        cx = self._bootstrapped()
        cx.execute("UPDATE authority_binding SET phase='compare', updated_at=? "
                   "WHERE id=1", (T0,))
        cx.execute("UPDATE authority_binding SET authority_epoch=2, updated_at=? "
                   "WHERE id=1", (T0,))
        self.assertEqual(auth.read_binding(cx)["authority_epoch"], 2)

    def test_repository_id_is_fixed_by_the_same_trigger(self):
        cx = self._bootstrapped()
        with self.assertRaises(sqlite3.IntegrityError):
            cx.execute("UPDATE authority_binding SET repository_id=99 WHERE id=1")


class AppendOnlyEpochLog(AuthorityTestCase):

    def test_update_aborts(self):
        cx = self._bootstrapped()
        with self.assertRaises(sqlite3.IntegrityError):
            cx.execute("UPDATE authority_epoch_log SET reason='rewritten'")

    def test_delete_aborts(self):
        cx = self._bootstrapped()
        with self.assertRaises(sqlite3.IntegrityError):
            cx.execute("DELETE FROM authority_epoch_log")
        self.assertEqual(len(auth.epoch_log(cx)), 1)


class RequireEpochRefusals(AuthorityTestCase):

    def test_a_stale_epoch_is_refused(self):
        cx = self._bootstrapped()
        cx.execute("UPDATE authority_binding SET authority_epoch=3, updated_at=? "
                   "WHERE id=1", (T0,))
        exc = self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=2, backend="legacy"),
            "authority_epoch_stale")
        self.assertEqual(exc.details["authority_epoch"], 3)
        self.assertEqual(exc.details["presented_epoch"], 2)

    def test_an_epoch_ahead_of_the_binding_is_refused_too(self):
        """ADR-0082 names only the stale direction, but "newer than the
        binding" is a real state: this process is reading a registry that
        has been rolled back or restored, and running against it would be
        running against a store the rest of the fleet has already left."""
        cx = self._bootstrapped()
        self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=9, backend="legacy"),
            "authority_epoch_ahead")

    def test_a_backend_mismatch_is_refused(self):
        cx = self._bootstrapped()
        exc = self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=1, backend="external"),
            "authority_backend_mismatch")
        self.assertEqual(exc.details["active_backend"], "legacy")

    def test_a_quiesced_binding_refuses_every_command(self):
        cx = self._bootstrapped()
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=1, backend="legacy"),
            "authority_quiesced")
        auth.end_quiesce(cx, migration_id=MIGRATION, t=T0 + 1)
        self.assertEqual(
            auth.require_epoch(cx, presented_epoch=1, backend="legacy")["quiesced"], 0)

    def test_the_matching_epoch_and_backend_pass(self):
        cx = self._bootstrapped()
        binding = auth.require_epoch(cx, presented_epoch=1, backend="legacy")
        self.assertEqual(binding["authority_epoch"], 1)

    def test_an_invalid_backend_or_epoch_is_refused_not_coerced(self):
        cx = self._bootstrapped()
        self._refusal(lambda: auth.require_epoch(cx, presented_epoch=1,
                                                  backend="postgres"),
                      "invalid_backend")
        self._refusal(lambda: auth.require_epoch(cx, presented_epoch="1",
                                                  backend="legacy"),
                      "invalid_presented_epoch")


class AbsentRegistryIsLegacy(AuthorityTestCase):

    def test_an_absent_registry_lets_a_legacy_client_proceed(self):
        binding = auth.guard(self.state_root, self.repository_id, backend="legacy")
        self.assertEqual(binding["active_backend"], "legacy")
        self.assertEqual(binding["registry"], "absent")

    def test_an_absent_registry_refuses_an_external_claim(self):
        """This is the fail-closed direction, and it is ADR-0082's phase 1-4
        posture written down: absence of a registry IS the legacy binding,
        because no flip has ever been recorded."""
        exc = self._refusal(
            lambda: auth.guard(self.state_root, self.repository_id, backend="external"),
            "authority_backend_mismatch")
        self.assertEqual(exc.details["registry"], "absent")

    def test_an_absent_registry_refuses_a_nonzero_epoch_claim(self):
        self._refusal(
            lambda: auth.guard(self.state_root, self.repository_id,
                                presented_epoch=1, backend="legacy"),
            "authority_epoch_ahead")

    def test_a_bootstrapped_but_empty_registry_file_behaves_the_same(self):
        cx = self._cx()          # creates the file, never bootstraps a row
        self.assertIsNone(auth.read_binding(cx))
        self.assertEqual(auth.guard(self.state_root, self.repository_id)["registry"],
                          "unbootstrapped")
        self._refusal(
            lambda: auth.guard(self.state_root, self.repository_id, backend="external"),
            "authority_backend_mismatch")


class QuiescedSwitch(AuthorityTestCase):

    def _ready(self):
        cx = self._bootstrapped()
        self._reach_match_verdict(cx)
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        return cx

    def test_the_one_flip_increments_the_epoch_and_logs_it(self):
        cx = self._ready()
        after = auth.switch_authority(
            cx, migration_id=MIGRATION, from_backend="legacy", to_backend="external",
            from_epoch=1, active_leases=[], t=T0 + 10)
        self.assertEqual(after["active_backend"], "external")
        self.assertEqual(after["authority_epoch"], 2)
        log = auth.epoch_log(cx)
        self.assertEqual(log[-1]["epoch"], 2)
        self.assertEqual(log[-1]["from_backend"], "legacy")
        self.assertEqual(log[-1]["to_backend"], "external")
        self.assertEqual(log[-1]["migration_id"], MIGRATION)

    def test_after_the_flip_a_legacy_client_is_refused_and_external_passes(self):
        cx = self._ready()
        auth.switch_authority(cx, migration_id=MIGRATION, from_backend="legacy",
                              to_backend="external", from_epoch=1, active_leases=[],
                              t=T0 + 10)
        auth.end_quiesce(cx, migration_id=MIGRATION, t=T0 + 11)
        self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=2, backend="legacy"),
            "authority_backend_mismatch")
        self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=1, backend="external"),
            "authority_epoch_stale")
        self.assertEqual(
            auth.require_epoch(cx, presented_epoch=2, backend="external")["authority_epoch"],
            2)

    def test_switch_refuses_without_a_quiesce(self):
        cx = self._bootstrapped()
        self._reach_match_verdict(cx)
        self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="external", from_epoch=1, active_leases=[], t=T0),
            "authority_not_quiesced")
        self.assertEqual(auth.read_binding(cx)["active_backend"], "legacy")

    def test_switch_refuses_an_active_legacy_lease(self):
        cx = self._ready()
        exc = self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="external", from_epoch=1,
                active_leases=["lease-7"], t=T0 + 10),
            "active_legacy_lease")
        self.assertEqual(exc.details["count"], 1)
        self.assertEqual(auth.read_binding(cx)["authority_epoch"], 1,
                          "a refused switch must not move the epoch")

    def test_switch_refuses_when_compare_did_not_match(self):
        cx = self._bootstrapped()
        auth.record_compare_verdict(cx, migration_id=MIGRATION, verdict="mismatch",
                                    mismatch_count=3, t=T0)
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        exc = self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="external", from_epoch=1, active_leases=[], t=T0 + 10),
            "compare_not_matched")
        self.assertEqual(exc.details["verdict"], "mismatch")

    def test_switch_refuses_when_no_compare_ran_at_all(self):
        cx = self._bootstrapped()
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        exc = self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="external", from_epoch=1, active_leases=[], t=T0 + 10),
            "compare_not_matched")
        self.assertIsNone(exc.details["verdict"])

    def test_two_racing_operators_produce_exactly_one_flip(self):
        """The same rowcount-CAS shape as
        `consume_publication_authorization`: the loser is TOLD it lost, and
        the epoch advances exactly once -- because
        `authority_epoch=authority_epoch+1` is evaluated by SQLite inside
        the same statement that tested `authority_epoch=?`."""
        cx = self._ready()
        first = auth.switch_authority(
            cx, migration_id=MIGRATION, from_backend="legacy", to_backend="external",
            from_epoch=1, active_leases=[], t=T0 + 10)
        self.assertEqual(first["authority_epoch"], 2)
        exc = self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="external", from_epoch=1, active_leases=[], t=T0 + 11),
            "authority_switch_refused")
        self.assertEqual(exc.details["actual_epoch"], 2)
        self.assertEqual(auth.read_binding(cx)["authority_epoch"], 2)
        self.assertEqual(len([e for e in auth.epoch_log(cx) if e["epoch"] == 2]), 1)

    def test_a_switch_to_the_same_backend_is_refused_as_a_noop(self):
        cx = self._ready()
        self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="legacy", from_epoch=1, active_leases=[], t=T0),
            "authority_switch_is_a_noop")

    def test_active_leases_must_be_supplied_explicitly(self):
        """`None` is refused rather than treated as "no leases". A caller
        that forgot to look is not a caller that looked and found none."""
        cx = self._ready()
        self._refusal(
            lambda: auth.switch_authority(
                cx, migration_id=MIGRATION, from_backend="legacy",
                to_backend="external", from_epoch=1, active_leases=None, t=T0),
            "active_leases_not_supplied")


class RollbackIsNotAToggle(AuthorityTestCase):

    def _switched(self):
        cx = self._bootstrapped()
        self._reach_match_verdict(cx)
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        auth.switch_authority(cx, migration_id=MIGRATION, from_backend="legacy",
                              to_backend="external", from_epoch=1, active_leases=[],
                              t=T0 + 10)
        return cx

    def _fresh_copy(self):
        path = os.path.join(self.state_root, "fresh-legacy")
        os.makedirs(path, mode=0o700, exist_ok=True)
        with open(os.path.join(path, "control.db"), "wb") as fh:
            fh.write(b"replayed")
        return path

    def _retained_snapshot(self):
        path = os.path.join(self.state_root, "retained")
        os.makedirs(path, mode=0o700, exist_ok=True)
        with open(os.path.join(path, auth.RETAINED_SNAPSHOT_MARKER), "w") as fh:
            fh.write("retained\n")
        return path

    def test_rollback_into_a_fresh_copy_advances_a_new_epoch(self):
        cx = self._switched()
        after = auth.rollback_authority(cx, migration_id="mig-001-rollback",
                                         fresh_legacy_root=self._fresh_copy(),
                                         from_epoch=2, t=T0 + 20)
        self.assertEqual(after["active_backend"], "legacy")
        self.assertEqual(after["authority_epoch"], 3,
                          "rollback is a NEW epoch, never a decrement")
        self.assertEqual(auth.epoch_log(cx)[-1]["to_backend"], "legacy")

    def test_rollback_refuses_the_retained_read_only_snapshot(self):
        """ADR-0082: rollback replays "into a fresh legacy copy ... never
        reopening the stale files directly". The check is against a marker
        the snapshot itself wrote, so an operator under cutover pressure who
        points this at the read-only snapshot gets a refusal."""
        cx = self._switched()
        exc = self._refusal(
            lambda: auth.rollback_authority(
                cx, migration_id="r", fresh_legacy_root=self._retained_snapshot(),
                from_epoch=2, t=T0 + 20),
            "stale_snapshot_reopen")
        self.assertIn("ADR-0082", exc.details["reason"])
        self.assertEqual(auth.read_binding(cx)["active_backend"], "external")

    def test_rollback_refuses_without_a_quiesce(self):
        cx = self._switched()
        auth.end_quiesce(cx, migration_id=MIGRATION, t=T0 + 15)
        self._refusal(
            lambda: auth.rollback_authority(
                cx, migration_id="r", fresh_legacy_root=self._fresh_copy(),
                from_epoch=2, t=T0 + 20),
            "authority_not_quiesced")

    def test_rollback_refuses_an_empty_or_missing_fresh_root(self):
        cx = self._switched()
        empty = os.path.join(self.state_root, "empty")
        os.makedirs(empty, mode=0o700)
        self._refusal(
            lambda: auth.rollback_authority(cx, migration_id="r",
                                             fresh_legacy_root=empty,
                                             from_epoch=2, t=T0),
            "fresh_legacy_root_empty")
        self._refusal(
            lambda: auth.rollback_authority(
                cx, migration_id="r",
                fresh_legacy_root=os.path.join(self.state_root, "nope"),
                from_epoch=2, t=T0),
            "fresh_legacy_root_missing")

    def test_rollback_is_not_applicable_while_legacy_is_already_authority(self):
        cx = self._bootstrapped()
        auth.begin_quiesce(cx, migration_id="r", t=T0)
        self._refusal(
            lambda: auth.rollback_authority(cx, migration_id="r",
                                             fresh_legacy_root=self._fresh_copy(),
                                             from_epoch=1, t=T0),
            "authority_rollback_not_applicable")


class ShadowNamespaceDenies(AuthorityTestCase):

    def _shadow(self):
        root = os.path.join(self.state_root, "migration", MIGRATION)
        os.makedirs(root, mode=0o700, exist_ok=True)
        with open(auth.shadow_marker_path(root), "w") as fh:
            fh.write("shadow\n")
        return root

    def test_a_path_inside_the_shadow_namespace_denies_commands(self):
        root = self._shadow()
        deep = os.path.join(root, "shadow", "tenants", "1", "repos", "1")
        os.makedirs(deep, mode=0o700, exist_ok=True)
        exc = self._refusal(lambda: auth.assert_not_shadow_namespace(deep),
                             "shadow_namespace_denies_commands")
        self.assertIn("ADR-0082", exc.details["reason"])

    def test_the_denial_is_structural_there_is_no_authority_db_to_flip(self):
        """The namespace has no binding of its own and never gets one:
        allowing a command there would require creating a second authority
        binding, which is the dual authority the ADR forbids."""
        root = self._shadow()
        self.assertFalse(os.path.exists(os.path.join(root, "authority.db")))
        self.assertFalse(os.path.isdir(os.path.join(root, "authority")))
        self._refusal(lambda: auth.guard_connect(root),
                      "shadow_namespace_denies_commands")

    def test_an_ordinary_state_root_is_unaffected(self):
        self.assertIsNone(auth.find_shadow_marker(self.state_root))
        auth.assert_not_shadow_namespace(self.state_root)


class ClientGateAtEveryConnect(AuthorityTestCase):
    """STEP 5: the gate is wired into all four call sites and REALLY runs.

    The claim under test is not "the function exists" but "opening the
    adapter goes through it", so each case opens the adapter for real.
    """

    def _scoped_root(self, tenant_id=1001, repository_id=None):
        repository_id = self.repository_id if repository_id is None else repository_id
        return prov.resolve_publication_root(self.state_root, tenant_id, repository_id)

    def test_provenance_connect_refuses_a_client_claiming_external(self):
        os.environ[auth.ENV_BACKEND] = "external"
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.connect(self.state_root, 1001, self.repository_id)
        self.assertEqual(ctx.exception.reason_code, "authority_backend_mismatch")

    def test_provenance_connect_proceeds_for_a_legacy_client(self):
        cx = prov.connect(self.state_root, 1001, self.repository_id)
        self.addCleanup(cx.close)
        self.assertIsNotNone(cx)

    def test_publication_connect_derives_its_scope_and_refuses(self):
        """publication's `connect(state_root)` is handed the ALREADY
        tenant-scoped root, so `derive_scope` recovers both the real state
        root and the repository_id from it -- no new parameter, and the
        frozen signature stays frozen."""
        root = self._scoped_root()
        pcx = prov.connect(self.state_root, 1001, self.repository_id)
        self.addCleanup(pcx.close)
        derived = auth.derive_scope(root)
        self.assertEqual(derived, (self.state_root, self.repository_id))
        os.environ[auth.ENV_BACKEND] = "external"
        with self.assertRaises(pub.PublicationError) as ctx:
            pub.connect(root)
        self.assertEqual(ctx.exception.reason_code, "authority_backend_mismatch")

    def test_memory_connect_refuses_without_widening_its_frozen_signature(self):
        import inspect
        self.assertEqual(list(inspect.signature(memory.connect).parameters),
                          ["state_root", "create"])
        root = memory.resolve_memory_root(self.state_root, self.repository_id)
        os.environ[auth.ENV_BACKEND] = "external"
        with self.assertRaises(memory.MemoryStoreError) as ctx:
            memory.connect(root)
        self.assertEqual(ctx.exception.reason_code, "authority_backend_mismatch")

    def test_after_a_real_flip_the_epoch_unaware_legacy_client_is_refused(self):
        """The property ADR-0082 phase 5 actually needs: legacy processes
        that do not understand epochs stop working AT the cutover, without
        anyone remembering to kill them."""
        cx = self._bootstrapped()
        self._reach_match_verdict(cx)
        auth.begin_quiesce(cx, migration_id=MIGRATION, t=T0)
        auth.switch_authority(cx, migration_id=MIGRATION, from_backend="legacy",
                              to_backend="external", from_epoch=1, active_leases=[],
                              t=T0 + 10)
        auth.end_quiesce(cx, migration_id=MIGRATION, t=T0 + 11)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.connect(self.state_root, 1001, self.repository_id)
        self.assertEqual(ctx.exception.reason_code, "authority_backend_mismatch")
        os.environ[auth.ENV_BACKEND] = "external"
        os.environ[auth.ENV_EPOCH] = "2"
        reopened = prov.connect(self.state_root, 1001, self.repository_id)
        self.addCleanup(reopened.close)

    def test_a_malformed_epoch_in_the_environment_is_refused_not_ignored(self):
        """Ignoring `EPOCH=seven` would silently demote an epoch-AWARE
        client into an epoch-unaware one -- exactly the class of client
        phase 5 says must be stopped."""
        os.environ[auth.ENV_EPOCH] = "seven"
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.connect(self.state_root, 1001, self.repository_id)
        self.assertEqual(ctx.exception.reason_code, "invalid_presented_epoch")

    def test_ctl_carries_the_gate_for_every_non_plane_free_verb(self):
        """Read the wiring rather than assert on a stub: the gate call must
        sit immediately after the `is_plane_free` early return, so it covers
        exactly the verbs that open the legacy control plane."""
        src = (BIN / "nightshift-ctl").read_text()
        self.assertIn("def authority_client_gate(args):", src)
        marker = "    if is_plane_free(args):\n        args.func(None, args)\n        return\n"
        self.assertIn(marker, src)
        after = src.split(marker, 1)[1]
        self.assertIn("authority_client_gate(args)", after.split("\n\n\n")[0])

    def test_ctl_refuses_a_control_plane_verb_when_the_client_claims_external(self):
        db_dir = os.path.join(self.state_root, "omni")
        os.makedirs(db_dir, mode=0o700, exist_ok=True)
        db = os.path.join(db_dir, "control.db")
        env = dict(os.environ, **{auth.ENV_BACKEND: "external"})
        result = subprocess.run(
            [sys.executable, str(BIN / "nightshift-ctl"), "--db", db, "slot", "list"],
            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 3, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"], "authority_backend_mismatch")
        self.assertIs(payload["ok"], False)


class CrashMidCutoverIsDecidable(AuthorityTestCase):
    """ADR-0082: "A crash consults the bootstrap binding."

    A REAL `SIGKILL` against a REAL subprocess, using the same idiom
    tests/test_memory_bootstrap.py's BootstrapCrashResume already
    establishes -- `proc.kill()`, not `terminate()`, so no atexit or signal
    handler runs and the recovery path faces an actual crash.
    """

    DRIVER = """
import os, sys, time
sys.path.insert(0, {bin!r})
import nightshift_authority as auth
cx = auth.connect({state_root!r}, {repo!r})
auth.init_binding(cx, repository_id={repo!r}, t=1.0)
auth.record_compare_verdict(cx, migration_id={mig!r}, verdict="match",
                            mismatch_count=0, t=1.0)
auth.begin_quiesce(cx, migration_id={mig!r}, t=2.0)
with open({flag!r}, "w") as fh:
    fh.write("quiesced")
# Hang here, holding the quiesce, until the parent kills us. This is the
# window a real cutover crash lands in: quiesced, compare matched, flip not
# yet issued.
while True:
    time.sleep(0.02)
"""

    def test_a_crash_between_quiesce_and_flip_is_decidable_from_outside(self):
        flag = os.path.join(self.state_root, "quiesced.flag")
        driver = os.path.join(self.state_root, "_driver.py")
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write(self.DRIVER.format(bin=str(BIN), state_root=self.state_root,
                                         repo=self.repository_id, mig=MIGRATION,
                                         flag=flag))
        proc = subprocess.Popen([sys.executable, driver],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline and not os.path.exists(flag):
                time.sleep(0.02)
            self.assertTrue(os.path.exists(flag),
                             "subprocess never reached the quiesced state")
            os.kill(proc.pid, signal.SIGKILL)
        finally:
            proc.wait(timeout=30)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

        # The operator's question, answered from OUTSIDE both stores.
        cx = auth.connect(self.state_root, self.repository_id, create=False)
        self.addCleanup(cx.close)
        binding = auth.read_binding(cx)
        self.assertEqual(binding["quiesced"], 1,
                          "the crash left the world quiesced, and it says so")
        self.assertEqual(binding["active_backend"], "legacy")
        self.assertEqual(binding["authority_epoch"], 1)
        self.assertEqual([e["epoch"] for e in auth.epoch_log(cx)], [1],
                          "no flip was logged, so the flip did not happen")

        # And commands are refused until an operator deliberately resumes --
        # the crash does not leave a half-open world that quietly accepts
        # writes to a store that may be about to lose authority.
        self._refusal(
            lambda: auth.require_epoch(cx, presented_epoch=1, backend="legacy"),
            "authority_quiesced")
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.connect(self.state_root, 1001, self.repository_id)
        self.assertEqual(ctx.exception.reason_code, "authority_quiesced")

        # Recovery is a decision, not a guess: the operator either resumes
        # legacy or completes the flip, and both are one call.
        auth.end_quiesce(cx, migration_id=MIGRATION, t=T0)
        resumed = prov.connect(self.state_root, 1001, self.repository_id)
        self.addCleanup(resumed.close)
        self.assertEqual(auth.read_binding(cx)["active_backend"], "legacy")


if __name__ == "__main__":
    unittest.main()
