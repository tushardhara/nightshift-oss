"""ADR-0080 -- key custody, envelope encryption, and the limits of deletion
(bin/nightshift_tenant_keys.py).

Mockless: real `hashlib.scrypt` and real `hmac`/`hashlib` primitives, real
sqlite3 over a real key database, real staged object files on disk, and a
real `SIGKILL` against a real subprocess for the resumable-rotation case.
Nothing below is stubbed, and no assertion is made about a value this file
computed a second way -- ciphertext is checked by looking at the bytes on
disk.

WHAT THIS SUITE PINS
====================
  CipherV1                  RFC 5869 HKDF, encrypt-then-MAC, the AAD bind,
                            tamper detection, nonce uniqueness, and the
                            registry seam that lets AES be added later.
  KeyLifecycle              KEK create / DEK wrap / unwrap, and that the
                            wrapped KEK is bound to its own tenant+version.
  DestroyIsOneWay           the trigger, exercised through the RAW
                            connection.
  RotationIsResumable       a real kill -9 mid-rotation leaves NO object
                            without a readable key, and the resume finishes.
  CryptoShred               after destruction the bytes on disk are
                            unreadable, not merely unlinked.
  DeletionReceiptCannotLie  the disclosure surface #400 condition 16 asks
                            for, including a read of this module's own
                            source.
  Canaries                  per-adapter zero, and the honest refusal to
                            certify an adapter the caller did not scope.
  LineagePurgeTakesTheKey   the companion call site: unlink AND unreadable.
"""

from __future__ import annotations

import base64
import hashlib
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
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_provenance as prov         # noqa: E402
import nightshift_publication as pub         # noqa: E402
import nightshift_tenant as tenant           # noqa: E402
import nightshift_tenant_keys as keys        # noqa: E402

T0 = 2_000_000.0
SECRET = b"operator-wrapping-passphrase"
TENANT = 1001
REPOSITORY = 7


def _digest(n):
    return hashlib.sha256(f"object-{n}".encode()).hexdigest()


class KeysTestCase(unittest.TestCase):
    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="nightshift-keys-")
        os.chmod(self.state_root, 0o700)
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.key_db = os.path.join(self.state_root, "keys", "tenant_keys.db")
        self.cx = keys.connect(self.key_db)
        self.addCleanup(self.cx.close)

    def _refusal(self, callable_, reason_code):
        with self.assertRaises(keys.TenantKeyError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.reason_code, reason_code)
        return ctx.exception

    def _kek(self, tenant_id=TENANT):
        return keys.create_tenant_kek(self.cx, tenant_id, wrapping_secret=SECRET, t=T0)


class CipherV1(KeysTestCase):

    def test_hkdf_matches_rfc_5869_test_vector_1(self):
        """RFC 5869 Appendix A.1 -- the published vector, so the
        implementation is checked against the standard rather than against
        itself."""
        ikm = bytes.fromhex("0b" * 22)
        salt = bytes.fromhex("000102030405060708090a0b0c")
        info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
        expected = ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56"
                    "ecc4c5bf34007208d5b887185865")
        self.assertEqual(keys.hkdf_sha256(ikm, salt, info, 42).hex(), expected)

    def test_a_sealed_body_does_not_contain_its_plaintext(self):
        key = os.urandom(32)
        body = b"CONFIDENTIAL-CANARY-STRING-0123456789" * 8
        raw = keys.envelope_to_bytes(keys.seal(key, body, b"aad"))
        self.assertNotIn(body, raw)
        self.assertNotIn(b"CONFIDENTIAL-CANARY", raw)
        self.assertEqual(keys.open_sealed(key, keys.envelope_from_bytes(raw), b"aad"),
                          body)

    def test_a_flipped_ciphertext_byte_is_detected_not_decrypted(self):
        key = os.urandom(32)
        env = keys.seal(key, b"payload", b"aad")
        ct = bytearray(base64.b64decode(env["ciphertext"]))
        ct[0] ^= 0x01
        env["ciphertext"] = base64.b64encode(bytes(ct)).decode("ascii")
        self._refusal(lambda: keys.open_sealed(key, env, b"aad"), "envelope_auth_failed")

    def test_a_flipped_tag_byte_is_detected(self):
        key = os.urandom(32)
        env = keys.seal(key, b"payload", b"aad")
        tag = bytearray(base64.b64decode(env["tag"]))
        tag[-1] ^= 0x80
        env["tag"] = base64.b64encode(bytes(tag)).decode("ascii")
        self._refusal(lambda: keys.open_sealed(key, env, b"aad"), "envelope_auth_failed")

    def test_the_wrong_aad_fails_closed(self):
        key = os.urandom(32)
        env = keys.seal(key, b"payload", b"tenant=1")
        self._refusal(lambda: keys.open_sealed(key, env, b"tenant=2"),
                      "envelope_auth_failed")

    def test_the_wrong_key_fails_closed(self):
        env = keys.seal(os.urandom(32), b"payload", b"aad")
        self._refusal(lambda: keys.open_sealed(os.urandom(32), env, b"aad"),
                      "envelope_auth_failed")

    def test_the_mac_covers_the_aad_boundary(self):
        """(aad=b"ab", ct=b"c") and (aad=b"a", ct=b"bc") must not MAC
        identically -- without length prefixes they would, which lets an
        attacker move bytes between the public and encrypted parts without
        changing the tag."""
        a = keys._mac_input("c", b"n" * 24, b"ab", b"c")
        b = keys._mac_input("c", b"n" * 24, b"a", b"bc")
        self.assertNotEqual(a, b)

    def test_nonces_are_unique_across_seals_of_identical_plaintext(self):
        key = os.urandom(32)
        nonces = {keys.seal(key, b"same", b"aad")["nonce"] for _ in range(64)}
        self.assertEqual(len(nonces), 64)
        cts = {keys.seal(key, b"same", b"aad")["ciphertext"] for _ in range(64)}
        self.assertEqual(len(cts), 64,
                          "identical plaintext must not produce identical "
                          "ciphertext -- that is what a fresh nonce buys")

    def test_the_empty_body_round_trips(self):
        key = os.urandom(32)
        env = keys.seal(key, b"", b"aad")
        self.assertEqual(keys.open_sealed(key, env, b"aad"), b"")

    def test_the_cipher_registry_is_the_seam_for_adding_aes_later(self):
        """Every stored envelope names its own cipher, so adding
        `aes-256-gcm-v1` cannot change how bytes already written are
        interpreted."""
        self.assertIn(keys.DEFAULT_CIPHER_ID, keys.CIPHERS)
        env = keys.seal(os.urandom(32), b"x", b"a")
        self.assertEqual(env["cipher_id"], keys.DEFAULT_CIPHER_ID)
        self._refusal(lambda: keys.seal(os.urandom(32), b"x", b"a",
                                         cipher_id="aes-256-gcm-v1"),
                      "unknown_cipher_id")
        self._refusal(
            lambda: keys.open_sealed(os.urandom(32),
                                      dict(env, cipher_id="aes-256-gcm-v1"), b"a"),
            "unknown_cipher_id")

    def test_scrypt_is_available_and_needs_the_explicit_maxmem(self):
        """Measured, not assumed: the declared parameters exceed OpenSSL's
        default 32 MiB maxmem on this build, so passing maxmem is REQUIRED
        and its absence raises rather than silently weakening anything."""
        salt = os.urandom(16)
        derived = keys.derive_wrapping_key(SECRET, salt)
        self.assertEqual(len(derived), 32)
        self.assertEqual(derived, keys.derive_wrapping_key(SECRET, salt),
                          "the KDF must be deterministic for one (secret, salt)")
        self.assertNotEqual(derived, keys.derive_wrapping_key(SECRET, os.urandom(16)))
        with self.assertRaises(ValueError):
            hashlib.scrypt(SECRET, salt=salt, n=keys.SCRYPT_N, r=keys.SCRYPT_R,
                           p=keys.SCRYPT_P, dklen=32)


class KeyLifecycle(KeysTestCase):

    def test_create_wrap_and_unwrap_round_trip(self):
        self._kek()
        wrapped = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                                        wrapping_secret=SECRET, t=T0)
        self.assertTrue(wrapped["created"])
        self.assertEqual(len(wrapped["dek"]), 32)
        self.assertEqual(
            keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                                    wrapping_secret=SECRET),
            wrapped["dek"])

    def test_wrapping_an_object_twice_returns_the_same_dek(self):
        """Two DEKs for one object means one of them is unreachable, and the
        body sealed under it is unreadable forever."""
        self._kek()
        first = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                                      wrapping_secret=SECRET, t=T0)
        second = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                                       wrapping_secret=SECRET, t=T0 + 1)
        self.assertFalse(second["created"])
        self.assertEqual(first["dek"], second["dek"])

    def test_the_wrong_wrapping_secret_cannot_open_the_kek(self):
        self._kek()
        keys.wrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                             wrapping_secret=SECRET, t=T0)
        self._refusal(
            lambda: keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                                            wrapping_secret=b"wrong-passphrase"),
            "envelope_auth_failed")

    def test_the_wrapped_kek_is_bound_to_its_own_tenant_and_version(self):
        """Moving a wrapped KEK row from tenant A to tenant B changes the
        AAD, so the tag stops verifying and a row swap INSIDE the key
        database is detected on open rather than silently decrypting."""
        self._kek(TENANT)
        self._kek(TENANT + 1)
        row = self.cx.execute("SELECT * FROM tenant_key WHERE tenant_id=?",
                              (TENANT,)).fetchone()
        self.cx.execute("DELETE FROM tenant_key WHERE tenant_id=?", (TENANT + 1,))
        self.cx.execute(
            "INSERT INTO tenant_key(tenant_id, key_version, wrapped_kek, "
            "kdf_params_json, cipher_id, created_at) VALUES(?,?,?,?,?,?)",
            (TENANT + 1, row["key_version"], row["wrapped_kek"],
             row["kdf_params_json"], row["cipher_id"], row["created_at"]))
        self._refusal(
            lambda: keys.unwrap_tenant_kek(self.cx, TENANT + 1, row["key_version"],
                                            wrapping_secret=SECRET),
            "envelope_auth_failed")

    def test_a_dek_cannot_be_replayed_onto_another_object(self):
        self._kek()
        keys.wrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                             wrapping_secret=SECRET, t=T0)
        row = self.cx.execute(
            "SELECT * FROM object_key WHERE object_digest=?", (_digest(1),)).fetchone()
        self.cx.execute(
            "INSERT INTO object_key(tenant_id, repository_id, object_digest, "
            "key_version, wrapped_dek, nonce, cipher_id, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (TENANT, REPOSITORY, _digest(2), row["key_version"], row["wrapped_dek"],
             row["nonce"], row["cipher_id"], row["created_at"]))
        self._refusal(
            lambda: keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, _digest(2),
                                            wrapping_secret=SECRET),
            "envelope_auth_failed")

    def test_a_missing_kek_is_refused_not_invented(self):
        self._refusal(lambda: keys.current_key_version(self.cx, TENANT),
                      "tenant_kek_missing")

    def test_invalid_ids_and_digests_are_refused(self):
        self._kek()
        for bad in (0, -1, True, "1", None):
            with self.subTest(tenant=bad):
                self._refusal(
                    lambda b=bad: keys.wrap_object_key(self.cx, b, REPOSITORY,
                                                        _digest(1),
                                                        wrapping_secret=SECRET),
                    "invalid_tenant_id")
        self._refusal(
            lambda: keys.wrap_object_key(self.cx, TENANT, REPOSITORY, "not-a-digest",
                                          wrapping_secret=SECRET),
            "invalid_object_digest")


class DestroyIsOneWay(KeysTestCase):

    def test_the_database_refuses_to_rewrite_key_material(self):
        self._kek()
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE tenant_key SET wrapped_kek=X'00' "
                            "WHERE tenant_id=?", (TENANT,))

    def test_the_database_refuses_a_destroy_that_keeps_the_key(self):
        """A row saying "destroyed" while still holding the wrapped KEK
        would be a receipt for something that did not happen."""
        self._kek()
        self.cx.execute("UPDATE tenant_key SET retired_at=? WHERE tenant_id=?",
                        (T0, TENANT))
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute(
                "UPDATE tenant_key SET destroyed_at=?, destroyed_reason='x' "
                "WHERE tenant_id=?", (T0, TENANT))

    def test_the_database_refuses_to_undo_a_destruction(self):
        self._kek()
        keys.destroy_tenant_keys(self.cx, TENANT, reason="tenant-delete", t=T0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE tenant_key SET destroyed_at=NULL "
                            "WHERE tenant_id=?", (TENANT,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE tenant_key SET retired_at=NULL "
                            "WHERE tenant_id=?", (TENANT,))

    def test_retire_is_the_other_legal_transition(self):
        self._kek()
        self.cx.execute("UPDATE tenant_key SET retired_at=? WHERE tenant_id=?",
                        (T0, TENANT))
        self.assertEqual(
            keys.get_tenant_key(self.cx, TENANT, 1)["retired_at"], T0)


class RotationIsResumable(KeysTestCase):

    OBJECTS = 40

    def _seed(self):
        self._kek()
        deks = {}
        for i in range(self.OBJECTS):
            d = _digest(i)
            deks[d] = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, d,
                                            wrapping_secret=SECRET, t=T0)["dek"]
        return deks

    def test_rotation_rewraps_every_dek_and_none_change_value(self):
        deks = self._seed()
        report = keys.rotate_tenant_kek(self.cx, TENANT, wrapping_secret=SECRET,
                                         t=T0 + 1)
        self.assertEqual(report["to_key_version"], 2)
        self.assertEqual(report["rewrapped"], self.OBJECTS)
        for digest, dek in deks.items():
            self.assertEqual(
                keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                        wrapping_secret=SECRET),
                dek,
                "rotation must re-WRAP the same DEK, never mint a new one -- "
                "a new DEK would orphan every body already sealed")
        versions = {r[0] for r in self.cx.execute(
            "SELECT DISTINCT key_version FROM object_key WHERE tenant_id=?",
            (TENANT,)).fetchall()}
        self.assertEqual(versions, {2})
        self.assertIsNotNone(keys.get_tenant_key(self.cx, TENANT, 1)["retired_at"])

    DRIVER = """
import os, sys, time
sys.path.insert(0, {bin!r})
import nightshift_tenant_keys as keys
cx = keys.connect({db!r})
seen = [0]
def progress(info):
    seen[0] += 1
    with open({flag!r}, "w") as fh:
        fh.write(str(seen[0]))
    # Slow enough that the parent can land a SIGKILL in the MIDDLE of the
    # loop, which is the whole point -- a rotation that finished before the
    # kill would prove nothing about resuming.
    time.sleep(0.05)
keys.rotate_tenant_kek(cx, {tenant!r}, wrapping_secret={secret!r}, t=3.0,
                       batch=8, progress=progress)
"""

    def test_a_real_kill_9_mid_rotation_leaves_no_object_without_a_key(self):
        """ADR-0080: "Key rotation is resumable and audited."

        A REAL SIGKILL against a REAL subprocess -- the same idiom
        tests/test_memory_bootstrap.py's BootstrapCrashResume establishes.
        `proc.kill()`, not `terminate()`, so no handler runs.

        Two properties, and the second is the one that matters:
          1. the resume picks up from the watermark and finishes;
          2. at the MOMENT OF THE CRASH every object already had exactly one
             readable key -- the rewrapped ones under the new KEK, the rest
             under the old one, which is still live because the old KEK is
             retired only after the last object moves.
        """
        deks = self._seed()
        self.cx.close()
        flag = os.path.join(self.state_root, "progress.flag")
        driver = os.path.join(self.state_root, "_rotate.py")
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write(self.DRIVER.format(bin=str(BIN), db=self.key_db, flag=flag,
                                         tenant=TENANT, secret=SECRET))
        proc = subprocess.Popen([sys.executable, driver],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 30.0
            progressed = 0
            while time.monotonic() < deadline:
                if os.path.exists(flag):
                    try:
                        with open(flag) as fh:
                            progressed = int(fh.read() or 0)
                    except ValueError:
                        progressed = 0
                    if 3 <= progressed < self.OBJECTS:
                        break
                time.sleep(0.01)
            self.assertGreaterEqual(progressed, 3,
                                    "the rotation never got far enough to be "
                                    "killed mid-flight")
            self.assertLess(progressed, self.OBJECTS,
                            "the rotation finished before the kill -- the "
                            "resume path was not exercised")
            os.kill(proc.pid, signal.SIGKILL)
        finally:
            proc.wait(timeout=30)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

        self.cx = keys.connect(self.key_db)
        self.addCleanup(self.cx.close)

        # (2) EVERY object is readable right now, mid-rotation.
        for digest, dek in deks.items():
            self.assertEqual(
                keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                        wrapping_secret=SECRET),
                dek, f"{digest} lost its readable key across the crash")
        split = {r[0] for r in self.cx.execute(
            "SELECT DISTINCT key_version FROM object_key WHERE tenant_id=?",
            (TENANT,)).fetchall()}
        self.assertEqual(split, {1, 2},
                          "the crash must land with objects on BOTH versions; "
                          "otherwise this is not a mid-rotation state")
        watermark = self.cx.execute(
            "SELECT last_object_digest FROM rewrap_watermark WHERE tenant_id=?",
            (TENANT,)).fetchone()
        self.assertIsNotNone(watermark[0], "the resume point must be recorded")

        # (1) The resume finishes, and does not mint a third KEK version.
        report = keys.rotate_tenant_kek(self.cx, TENANT, wrapping_secret=SECRET,
                                         t=T0 + 9)
        self.assertTrue(report["resumed"])
        self.assertFalse(report["minted_new_version"])
        self.assertEqual(report["to_key_version"], 2)
        self.assertEqual(
            {r[0] for r in self.cx.execute(
                "SELECT DISTINCT key_version FROM object_key WHERE tenant_id=?",
                (TENANT,)).fetchall()},
            {2})
        self.assertEqual(
            self.cx.execute("SELECT COUNT(*) FROM tenant_key WHERE tenant_id=?",
                            (TENANT,)).fetchone()[0], 2)
        for digest, dek in deks.items():
            self.assertEqual(
                keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                        wrapping_secret=SECRET), dek)


class CryptoShred(KeysTestCase):

    def test_a_staged_object_is_unreadable_after_the_keys_are_destroyed(self):
        """The bytes stay on disk; what changes is that nothing can open
        them. That is the whole claim ADR-0080 lets this design make."""
        self._kek()
        body = b"tenant-confidential-report-body\n" * 40
        digest = hashlib.sha256(body).hexdigest()
        wrapped = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                        wrapping_secret=SECRET, t=T0)
        aad = keys._dek_aad(TENANT, REPOSITORY, digest)
        sealed = keys.envelope_to_bytes(keys.seal(wrapped["dek"], body, aad))
        path = prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body,
                                 key_version=wrapped["key_version"], sealed=sealed)

        with open(path, "rb") as fh:
            on_disk = fh.read()
        self.assertNotIn(b"tenant-confidential-report-body", on_disk)
        raw, is_sealed = prov.read_staged_object(self.state_root, TENANT,
                                                  REPOSITORY, digest)
        self.assertTrue(is_sealed)
        self.assertEqual(
            keys.open_sealed(
                keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                        wrapping_secret=SECRET),
                keys.envelope_from_bytes(raw), aad),
            body)

        keys.destroy_tenant_keys(self.cx, TENANT, reason="tenant-delete", t=T0 + 1)
        self.assertTrue(os.path.isfile(path),
                         "the ciphertext is still on disk -- that is exactly why "
                         "the receipt may not claim erasure")
        self._refusal(
            lambda: keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                            wrapping_secret=SECRET),
            "tenant_keys_destroyed")
        self.assertEqual(
            self.cx.execute("SELECT length(wrapped_kek) FROM tenant_key "
                            "WHERE tenant_id=?", (TENANT,)).fetchone()[0], 0)

    def test_stage_object_refuses_a_sealed_body_with_no_key_version(self):
        body = b"body"
        digest = hashlib.sha256(body).hexdigest()
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body,
                              sealed=keys.envelope_to_bytes(
                                  keys.seal(os.urandom(32), body, b"a")))
        self.assertEqual(ctx.exception.reason_code,
                          "sealed_object_requires_key_version")

    def test_stage_object_refuses_an_envelope_that_leaks_its_plaintext(self):
        """The encryption claim made falsifiable AT THE WRITE, not at some
        later audit."""
        body = b"plaintext-body"
        digest = hashlib.sha256(body).hexdigest()
        fake = prov.SEALED_OBJECT_MAGIC + b'{"ciphertext":"plaintext-body"}'
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body,
                              key_version=1, sealed=fake)
        self.assertEqual(ctx.exception.reason_code, "sealed_object_leaks_plaintext")

    def test_stage_object_refuses_a_key_version_with_no_sealed_body(self):
        body = b"body"
        digest = hashlib.sha256(body).hexdigest()
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body,
                              key_version=1)
        self.assertEqual(ctx.exception.reason_code, "key_version_without_sealed_body")

    def test_plain_staging_is_unchanged(self):
        """The path-scoped helpers keep working untouched, which is why
        `object_exists` and `purge_staged_object` needed no change."""
        body = b"ordinary"
        digest = hashlib.sha256(body).hexdigest()
        prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body)
        self.assertTrue(prov.object_exists(self.state_root, TENANT, REPOSITORY, digest))
        raw, is_sealed = prov.read_staged_object(self.state_root, TENANT,
                                                  REPOSITORY, digest)
        self.assertFalse(is_sealed)
        self.assertEqual(raw, body)
        self.assertTrue(prov.purge_staged_object(self.state_root, TENANT,
                                                  REPOSITORY, digest))


class PublicationEncryptionPolicy(KeysTestCase):
    """STEP 9's other half: `nightshift_publication.stage()` refuses a
    plaintext stage once the policy row DECLARES encryption, and records the
    cipher that sealed the body.

    Real publication.db beside a real provenance.db, real policy and real
    snapshot rows -- the same fixture shape tests/test_publication_gateway.py
    builds, minus the git destination, because `stage()` never touches git.
    """

    SNAPSHOT = {
        "repository_fingerprint": "sha256:" + "a" * 64,
        "object_format": "sha1",
        "commit_oid": "b" * 40,
        "tree_oid": "c" * 40,
    }

    def _fixture(self, *, policy_key_version=None):
        pcx = prov.connect(self.state_root, TENANT, REPOSITORY)
        self.addCleanup(pcx.close)
        prov.record_source_snapshot(pcx, TENANT, REPOSITORY, "snap-1",
                                    dict(self.SNAPSHOT, captured_at=T0))
        root = pub.resolve_publication_root(self.state_root, TENANT, REPOSITORY)
        cx = pub.connect(root)
        self.addCleanup(cx.close)
        pub.policy_set(cx, tenant_id=TENANT, repository_id=REPOSITORY,
                       mode="no_generated_file", path_allowlist=["**"],
                       target_git_dir=None, target_ref=None,
                       key_version=policy_key_version, t=T0)
        return cx

    def _stage(self, cx, **kwargs):
        return pub.stage(cx, tenant_id=TENANT, repository_id=REPOSITORY,
                         content_bytes=b"report body\n",
                         destination_path="docs/report.md",
                         classification="report", producer_identity="eng_alice",
                         producer_token_bytes=producer_token(pub, cx, TENANT, "eng_alice"),
                         producer_run_id="run-1", snapshot_id="snap-1", t=T0,
                         **kwargs)

    def _pub_refusal(self, callable_, reason_code):
        with self.assertRaises(pub.PublicationError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.reason_code, reason_code)
        self.assertEqual(ctx.exception.exit_code, 3)
        return ctx.exception

    def test_a_policy_without_a_key_version_stages_plaintext_unchanged(self):
        """The default path is byte-for-byte what it was: encryption is opt
        in, declared by policy, never inferred."""
        cx = self._fixture()
        result = self._stage(cx)
        self.assertEqual(result["status"], "staged")
        row = pub.get_stage(cx, result["stage_id"])
        self.assertIsNone(row["key_version"])
        self.assertIsNone(row["cipher_id"])

    def test_a_policy_declaring_encryption_refuses_a_plaintext_stage(self):
        cx = self._fixture(policy_key_version=1)
        exc = self._pub_refusal(lambda: self._stage(cx),
                                 "encryption_required_key_version_missing")
        self.assertEqual(exc.details["policy_key_version"], 1)
        self.assertEqual(
            cx.execute("SELECT COUNT(*) FROM publication_stage").fetchone()[0], 0,
            "a refused stage must write no row")

    def test_a_key_version_without_a_cipher_id_is_refused(self):
        """A stage row naming a key but not a cipher would be reinterpreted
        by whatever the default cipher happened to be on the day someone
        read it -- which is exactly what the CIPHERS registry exists to
        prevent."""
        cx = self._fixture(policy_key_version=1)
        self._pub_refusal(lambda: self._stage(cx, key_version=1),
                           "cipher_id_required_with_key_version")

    def test_an_invalid_key_version_is_refused_not_coerced(self):
        cx = self._fixture(policy_key_version=1)
        for bad in (0, -1, True, "1"):
            with self.subTest(key_version=bad):
                self._pub_refusal(
                    lambda b=bad: self._stage(cx, key_version=b,
                                               cipher_id=keys.DEFAULT_CIPHER_ID),
                    "invalid_key_version")

    def test_the_cipher_id_is_recorded_on_the_stage_row(self):
        cx = self._fixture(policy_key_version=1)
        self._kek()
        wrapped = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, _digest(1),
                                        wrapping_secret=SECRET, t=T0)
        result = self._stage(cx, key_version=wrapped["key_version"],
                             cipher_id=wrapped["cipher_id"])
        row = pub.get_stage(cx, result["stage_id"])
        self.assertEqual(row["key_version"], wrapped["key_version"])
        self.assertEqual(row["cipher_id"], keys.DEFAULT_CIPHER_ID)

    def test_the_cipher_id_column_is_added_to_a_pre_existing_database(self):
        """`CREATE TABLE IF NOT EXISTS` does not add a column to a table
        that already exists, so a schema edit alone would apply to fresh
        databases and silently NOT apply to existing ones. The additive
        migration is what stops those two diverging with no error anywhere.
        """
        cx = self._fixture()
        cx.execute("ALTER TABLE publication_stage DROP COLUMN cipher_id")
        self.assertNotIn("cipher_id", {r[1] for r in cx.execute(
            "PRAGMA table_info(publication_stage)").fetchall()})
        pub.ensure_schema(cx)
        self.assertIn("cipher_id", {r[1] for r in cx.execute(
            "PRAGMA table_info(publication_stage)").fetchall()})
        pub.ensure_schema(cx)          # idempotent


class DeletionReceiptCannotLie(KeysTestCase):

    def test_the_receipt_never_claims_cryptographic_erasure(self):
        self._kek()
        keys.destroy_tenant_keys(self.cx, TENANT, reason="tenant-delete", t=T0)
        receipt = keys.deletion_receipt(self.cx, TENANT, t=T0 + 1)
        self.assertIs(receipt["cryptographic_erasure"], False)

    def test_the_literal_true_never_appears_against_that_key_in_the_source(self):
        """Read the module's own source. A future editor who wants to claim
        erasure has to change a NAMED constant and delete this test, rather
        than flip a boolean in a dict literal during a refactor."""
        src = (BIN / "nightshift_tenant_keys.py").read_text()
        self.assertIs(keys.CRYPTOGRAPHIC_ERASURE_CLAIM, False)
        for forbidden in ('"cryptographic_erasure": True',
                          "'cryptographic_erasure': True",
                          "cryptographic_erasure=True",
                          "CRYPTOGRAPHIC_ERASURE_CLAIM = True"):
            self.assertNotIn(forbidden, src)

    def test_the_receipt_publishes_the_35_day_backup_window(self):
        self.assertEqual(keys.BACKUP_MAX_RESIDENCY_SECONDS, 35 * 86400)
        receipt = keys.deletion_receipt(self.cx, TENANT, t=T0)
        self.assertEqual(receipt["backup_residency_until"], T0 + 35 * 86400)
        self.assertEqual(receipt["backup_max_residency_seconds"], 35 * 86400)

    def test_the_receipt_names_what_key_destruction_does_not_cover(self):
        receipt = keys.deletion_receipt(self.cx, TENANT, t=T0)
        self.assertEqual(receipt["not_envelope_encrypted"],
                          ["embedding", "control_metadata", "index_pages"])
        self.assertIn("NOT erase", receipt["disclosure"])
        self.assertIn("35 days", receipt["disclosure"])

    def test_the_receipt_reports_which_versions_were_actually_destroyed(self):
        self._kek()
        keys.rotate_tenant_kek(self.cx, TENANT, wrapping_secret=SECRET, t=T0)
        before = keys.deletion_receipt(self.cx, TENANT, t=T0)
        self.assertEqual(before["key_versions_destroyed"], [])
        keys.destroy_tenant_keys(self.cx, TENANT, reason="tenant-delete", t=T0 + 1)
        after = keys.deletion_receipt(self.cx, TENANT, t=T0 + 1)
        self.assertEqual(after["key_versions_destroyed"], [1, 2])
        self.assertEqual(after["live_object_keys"], 0)


class Canaries(KeysTestCase):

    def _populate(self):
        pcx = prov.connect(self.state_root, TENANT, REPOSITORY)
        self.addCleanup(pcx.close)
        prov.record_source_snapshot(pcx, TENANT, REPOSITORY, "snap-1", {
            "repository_fingerprint": "sha256:" + "a" * 64,
            "object_format": "sha1", "commit_oid": "b" * 40,
            "tree_oid": "c" * 40, "captured_at": T0})
        prov.record_lineage_node(pcx, TENANT, REPOSITORY, "node-1", "source_snapshot",
                                 "snapshot", "snap-1", content_digest=None, t=T0)
        self._kek()
        body = b"body"
        digest = hashlib.sha256(body).hexdigest()
        keys.wrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                             wrapping_secret=SECRET, t=T0)
        prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body)
        return pcx, digest

    def test_every_adapter_is_walked_and_nonzero_blocks_completion(self):
        self._populate()
        report = keys.enumerate_canaries(self.state_root, self.cx, TENANT,
                                          memory_projects=[])
        for name in ("provenance_db", "publication_db", "staged_objects",
                      "key_custody", "memory_db"):
            self.assertIn(name, report["adapters"])
        self.assertGreater(report["adapters"]["provenance_db"]["residual"], 0)
        self.assertGreater(report["adapters"]["staged_objects"]["residual"], 0)
        self.assertGreater(report["adapters"]["key_custody"]["residual"], 0)
        self.assertFalse(report["complete"])
        self.assertIn("provenance_db", report["nonzero_adapters"])

    def test_deletion_is_complete_only_when_every_adapter_returns_zero(self):
        pcx, digest = self._populate()
        pcx.execute("DELETE FROM lineage_node WHERE tenant_id=?", (TENANT,))
        pcx.execute("DELETE FROM source_snapshot WHERE tenant_id=?", (TENANT,))
        prov.purge_staged_object(self.state_root, TENANT, REPOSITORY, digest)
        keys.destroy_tenant_keys(self.cx, TENANT, reason="tenant-delete", t=T0)
        report = keys.enumerate_canaries(self.state_root, self.cx, TENANT,
                                          memory_projects=[])
        self.assertEqual(report["nonzero_adapters"], {})
        self.assertEqual(report["unscoped_adapters"], [])
        self.assertTrue(report["complete"], report["adapters"])

    def test_an_unscoped_memory_adapter_blocks_completion_rather_than_reporting_zero(self):
        """`memory.db` is project-scoped and its `artifact` table has NO
        `tenant_id` column. An adapter you did not scope is an adapter you
        cannot certify, and reporting it as zero would be exactly the
        vacuous evidence this repository already rejected once."""
        keys.destroy_tenant_keys(self.cx, TENANT, reason="x", t=T0)
        report = keys.enumerate_canaries(self.state_root, self.cx, TENANT)
        self.assertIsNone(report["adapters"]["memory_db"]["residual"])
        self.assertEqual(report["adapters"]["memory_db"]["reason"],
                          "memory_scope_not_supplied")
        self.assertIn("memory_db", report["unscoped_adapters"])
        self.assertFalse(report["complete"])

    def test_another_tenants_rows_are_not_counted_as_this_tenants_residue(self):
        self._populate()
        other = TENANT + 1
        ocx = prov.connect(self.state_root, other, REPOSITORY)
        self.addCleanup(ocx.close)
        prov.record_source_snapshot(ocx, other, REPOSITORY, "snap-other", {
            "repository_fingerprint": "sha256:" + "d" * 64,
            "object_format": "sha1", "commit_oid": "e" * 40,
            "tree_oid": "f" * 40, "captured_at": T0})
        report = keys.enumerate_canaries(self.state_root, self.cx, other,
                                          memory_projects=[])
        self.assertEqual(report["tenant_id"], other)
        self.assertEqual(report["adapters"]["key_custody"]["residual"], 0,
                          "tenant B has no keys; tenant A's must not be counted")


class LineagePurgeTakesTheKey(KeysTestCase):

    def test_purging_a_lineage_node_destroys_the_dek_and_the_bytes(self):
        """ADR-0080's deletion path in one call: unlinking makes the bytes
        unreachable through the graph; destroying the DEK makes them
        unreadable. Doing only the first leaves decryptable ciphertext with
        nothing pointing at it -- worse than either failure alone, because
        nothing lists it any more either."""
        pcx = prov.connect(self.state_root, TENANT, REPOSITORY)
        self.addCleanup(pcx.close)
        self._kek()
        body = b"derived-narrative-body"
        digest = hashlib.sha256(body).hexdigest()
        wrapped = keys.wrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                        wrapping_secret=SECRET, t=T0)
        sealed = keys.envelope_to_bytes(keys.seal(
            wrapped["dek"], body, keys._dek_aad(TENANT, REPOSITORY, digest)))
        prov.stage_object(self.state_root, TENANT, REPOSITORY, digest, body,
                          key_version=wrapped["key_version"], sealed=sealed)
        prov.record_lineage_node(pcx, TENANT, REPOSITORY, "node-1", "worker_output",
                                 "artifact", "a1", content_digest=digest, t=T0)

        report = keys.purge_lineage_with_keys(
            prov, pcx, self.cx, self.state_root, TENANT, REPOSITORY, "node-1",
            reason="tenant-delete", t=T0 + 1)
        self.assertEqual(report["purged_nodes"], ["node-1"])
        self.assertEqual(report["content_digests"], [digest])
        self.assertEqual(report["object_keys_destroyed"], 1)
        self.assertEqual(report["staged_objects_removed"], 1)
        self.assertFalse(prov.object_exists(self.state_root, TENANT, REPOSITORY,
                                             digest))
        self._refusal(
            lambda: keys.unwrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                                            wrapping_secret=SECRET),
            "object_key_missing")

    def test_the_bare_purge_alone_would_leave_the_key_behind(self):
        """States plainly WHY the companion exists: `purge_lineage_node` on
        its own is not a deletion under ADR-0080."""
        pcx = prov.connect(self.state_root, TENANT, REPOSITORY)
        self.addCleanup(pcx.close)
        self._kek()
        body = b"body"
        digest = hashlib.sha256(body).hexdigest()
        keys.wrap_object_key(self.cx, TENANT, REPOSITORY, digest,
                             wrapping_secret=SECRET, t=T0)
        prov.record_lineage_node(pcx, TENANT, REPOSITORY, "node-1", "worker_output",
                                 "artifact", "a1", content_digest=digest, t=T0)
        prov.purge_lineage_node(pcx, TENANT, REPOSITORY, "node-1")
        self.assertEqual(
            self.cx.execute("SELECT COUNT(*) FROM object_key WHERE tenant_id=?",
                            (TENANT,)).fetchone()[0], 1,
            "the DEK survives a bare lineage purge -- which is precisely why "
            "purge_lineage_with_keys exists")


if __name__ == "__main__":
    unittest.main()
