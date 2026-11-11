"""Issue #400 close-out, STEP 1 -- one tenant identity (bin/nightshift_tenant.py).

Mockless: every assertion below is against a REAL sqlite3 connection to a
REAL registry.db in a REAL tempdir, and the trigger assertions go through
the raw connection so they prove the DATABASE refuses, not that the Python
wrapper declined to ask.

WHAT THIS SUITE PINS
====================
  TenantIdentityIsOneThing   the uid->id mapping, and that the INTEGER
                             surrogate is machine-allocated so a caller
                             cannot choose it.
  IdsAreNeverReissued        AUTOINCREMENT's high-water mark, which is what
                             stops a new tenant inheriting a deleted
                             tenant's directory and wrapped keys.
  StatusIsAOneWayRatchet     enforced by trigger, not by the wrapper.
  UidsAreCanonicalised       `Tenant-Alpha` and `tenant-alpha` are ONE
                             tenant, not two directories and two key sets.
  UidNeverReachesAPath       the traversal-shaped names that must be refused.

A NOTE ON WHY THIS MODULE EXISTS AT ALL, since the tree moved underneath
its original brief. The #400 task named a divergence -- publication
declaring `tenant_id TEXT` while provenance declared INTEGER -- and that
divergence is ALREADY CLOSED in the working tree by the #694 seam work:
both schemas now carry
`INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0)`, and
`nightshift_publication._validate_tenant_id` delegates outright to
`prov.validate_tenant_id`. What was NOT provided by that work, and is
provided here, is the other half: humans, contracts and support tickets
name a tenant with a STRING, and something has to map that string onto the
integer surrogate without ever letting the string reach a path join.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_tenant as tenant           # noqa: E402
import nightshift_provenance as prov         # noqa: E402
import nightshift_publication as pub         # noqa: E402

T0 = 2_000_000.0


class TenantRegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="nightshift-tenant-")
        os.chmod(self.state_root, 0o700)
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.cx = tenant.connect(self.state_root)
        self.addCleanup(self.cx.close)

    def _refusal(self, callable_, reason_code):
        with self.assertRaises(tenant.TenantError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.reason_code, reason_code)
        self.assertEqual(ctx.exception.exit_code, 3)
        return ctx.exception


class TenantIdentityIsOneThing(TenantRegistryTestCase):

    def test_uid_maps_to_a_machine_allocated_integer_surrogate(self):
        row = tenant.register(self.cx, "tenant-alpha", T0)
        self.assertIsInstance(row["tenant_id"], int)
        self.assertGreater(row["tenant_id"], 0)
        self.assertEqual(row["tenant_uid"], "tenant-alpha")
        self.assertEqual(row["status"], "active")
        self.assertEqual(tenant.resolve_id(self.cx, "tenant-alpha"), row["tenant_id"])
        self.assertEqual(tenant.resolve_uid(self.cx, row["tenant_id"]), "tenant-alpha")

    def test_the_surrogate_satisfies_provenances_own_validator(self):
        """The whole point: the id this registry hands out must be usable,
        unchanged, by the module that owns the strict validator."""
        tid = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        self.assertEqual(prov.validate_tenant_id(tid), tid)
        self.assertEqual(pub._validate_tenant_id(tid), tid)

    def test_the_text_uid_is_refused_by_that_same_validator(self):
        """Confirms the two halves are genuinely different types, so the
        registry is a mapping and not a rename."""
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.validate_tenant_id("tenant-alpha")
        self.assertEqual(ctx.exception.reason_code, "invalid_tenant_id")

    def test_register_is_idempotent_by_uid(self):
        first = tenant.register(self.cx, "tenant-alpha", T0)
        second = tenant.register(self.cx, "tenant-alpha", T0 + 100)
        self.assertEqual(first["tenant_id"], second["tenant_id"])
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertEqual(len(tenant.list_tenants(self.cx)), 1)

    def test_two_uids_get_two_ids(self):
        a = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        b = tenant.register(self.cx, "tenant-beta", T0)["tenant_id"]
        self.assertNotEqual(a, b)

    def test_unknown_uid_is_refused_not_silently_none(self):
        self._refusal(lambda: tenant.resolve_id(self.cx, "no-such-tenant"),
                      "tenant_uid_unknown")
        self._refusal(lambda: tenant.resolve_uid(self.cx, 999), "tenant_id_unknown")

    def test_registry_db_lands_at_the_declared_path_with_0700_directory(self):
        expected = os.path.join(self.state_root, "tenants", "registry.db")
        self.assertEqual(tenant.registry_path(self.state_root), expected)
        self.assertTrue(os.path.isfile(expected))
        mode = os.stat(os.path.dirname(expected)).st_mode & 0o777
        self.assertEqual(mode, 0o700, "tenants/ must not be group/other readable")

    def test_the_registry_sits_beside_the_per_tenant_directories(self):
        """`prov.resolve_publication_root` creates `<state_root>/tenants/
        <tenant_id>/...`. The registry is a sibling INSIDE the same
        `tenants/` directory, so one chmod covers both."""
        tid = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        root = prov.resolve_publication_root(self.state_root, tid, 1)
        self.assertTrue(root.startswith(os.path.join(self.state_root, "tenants",
                                                      str(tid)) + os.sep))
        self.assertEqual(os.stat(os.path.join(self.state_root, "tenants")).st_mode & 0o777,
                          0o700)


class IdsAreNeverReissued(TenantRegistryTestCase):

    def test_a_deleted_tenants_id_is_never_handed_to_a_new_tenant(self):
        """The leak this defends against is concrete: `tenants/<tenant_id>/`
        is a real directory holding a real tenant's objects, and
        `tenant_key` is keyed by the same integer. Reissuing the id points
        a NEW tenant at a DELETED tenant's bytes and wrapped DEKs."""
        first = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        tenant.set_status(self.cx, first, "deleting", T0 + 1)
        tenant.set_status(self.cx, first, "deleted", T0 + 2)
        second = tenant.register(self.cx, "tenant-beta", T0 + 3)["tenant_id"]
        self.assertGreater(second, first)
        self.assertNotEqual(second, first)

    def test_the_row_of_a_deleted_tenant_cannot_be_removed(self):
        tid = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("DELETE FROM tenant WHERE tenant_id=?", (tid,))
        self.assertEqual(len(tenant.list_tenants(self.cx)), 1)

    def test_registering_a_deleted_uid_returns_the_dead_row_not_a_live_one(self):
        tid = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        tenant.set_status(self.cx, tid, "deleting", T0 + 1)
        tenant.set_status(self.cx, tid, "deleted", T0 + 2)
        again = tenant.register(self.cx, "tenant-alpha", T0 + 3)
        self.assertEqual(again["tenant_id"], tid)
        self.assertEqual(again["status"], "deleted",
                          "re-registering must NOT resurrect a shredded tenant")


class StatusIsAOneWayRatchet(TenantRegistryTestCase):

    def setUp(self):
        super().setUp()
        self.tid = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]

    def test_active_to_deleting_to_deleted_is_allowed(self):
        self.assertEqual(tenant.set_status(self.cx, self.tid, "deleting", T0)["status"],
                          "deleting")
        self.assertEqual(tenant.set_status(self.cx, self.tid, "deleted", T0)["status"],
                          "deleted")

    def test_active_cannot_skip_straight_to_deleted(self):
        self._refusal(lambda: tenant.set_status(self.cx, self.tid, "deleted", T0),
                      "tenant_status_transition_refused")

    def test_deleted_cannot_walk_back_to_active_through_the_raw_connection(self):
        """Through the RAW connection, so this proves the DATABASE refuses.
        The reason is not tidiness: ADR-0080's deletion path destroys the
        tenant KEK, and no status update brings a destroyed key back. A
        resurrected tenant would be one whose rows are unreadable ciphertext
        and whose operator believes it is live."""
        tenant.set_status(self.cx, self.tid, "deleting", T0)
        tenant.set_status(self.cx, self.tid, "deleted", T0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE tenant SET status='active' WHERE tenant_id=?",
                            (self.tid,))
        self.assertEqual(tenant.get(self.cx, self.tid)["status"], "deleted")

    def test_deleting_cannot_walk_back_to_active(self):
        tenant.set_status(self.cx, self.tid, "deleting", T0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE tenant SET status='active' WHERE tenant_id=?",
                            (self.tid,))

    def test_the_uid_cannot_be_rewritten_onto_another_name(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE tenant SET tenant_uid='someone-else' "
                            "WHERE tenant_id=?", (self.tid,))

    def test_require_active_is_the_command_gate(self):
        self.assertEqual(tenant.require_active(self.cx, self.tid)["status"], "active")
        tenant.set_status(self.cx, self.tid, "deleting", T0)
        exc = self._refusal(lambda: tenant.require_active(self.cx, self.tid),
                             "tenant_not_active")
        self.assertEqual(exc.details["status"], "deleting")


class UidsAreCanonicalised(TenantRegistryTestCase):

    def test_case_and_whitespace_variants_are_one_tenant(self):
        a = tenant.register(self.cx, "tenant-alpha", T0)["tenant_id"]
        for variant in ("Tenant-Alpha", "  tenant-alpha  ", "TENANT-ALPHA"):
            self.assertEqual(tenant.resolve_id(self.cx, variant), a,
                              f"{variant!r} must resolve to the same tenant")
        self.assertEqual(len(tenant.list_tenants(self.cx)), 1)

    def test_the_canonical_form_is_what_is_stored(self):
        row = tenant.register(self.cx, "  Tenant-Alpha ", T0)
        self.assertEqual(row["tenant_uid"], "tenant-alpha")


class UidNeverReachesAPath(TenantRegistryTestCase):

    def test_traversal_shaped_names_are_refused(self):
        for bad in ("../other_tenant", "..", ".", "a/../b", "tenant/../..",
                    "/etc/passwd", "tenant\x00alpha", "tenant\nalpha", "",
                    "   ", "-leading-dash-is-fine" * 40):
            with self.subTest(uid=bad):
                self._refusal(lambda b=bad: tenant.validate_tenant_uid(b),
                              "invalid_tenant_uid")

    def test_non_string_uids_are_refused(self):
        for bad in (1, None, True, [], {}):
            with self.subTest(uid=bad):
                self._refusal(lambda b=bad: tenant.validate_tenant_uid(b),
                              "invalid_tenant_uid")

    def test_bool_is_refused_as_a_tenant_id(self):
        """`isinstance(True, int)` is True in Python, so an unguarded check
        would silently scope `True` to tenant 1."""
        self._refusal(lambda: tenant.validate_tenant_id(True), "invalid_tenant_id")
        self._refusal(lambda: tenant.validate_tenant_id(0), "invalid_tenant_id")
        self._refusal(lambda: tenant.validate_tenant_id(-1), "invalid_tenant_id")
        self._refusal(lambda: tenant.validate_tenant_id("1"), "invalid_tenant_id")


if __name__ == "__main__":
    unittest.main()
