#!/usr/bin/env python3
"""ADR-0080 -- per-tenant key custody, envelope encryption, and the exact
limits of what deleting a key does.

TWO HALVES, AND THE SECOND ONE IS THE POINT
===========================================
The first half is ordinary: a per-tenant key-encryption key (KEK), a
per-object data key (DEK) wrapped under it, resumable rotation, and
destruction. The second half is `deletion_receipt`, and it exists because
ADR-0080's central finding is a NEGATIVE one:

    "Shared-backup deletion completes only when the backup expires, with a
    declared maximum of 35 days. It is NOT cryptographic erasure."

So `deletion_receipt` is built so it CANNOT make the claim. `"cryptographic
_erasure"` is populated from the module constant
`CRYPTOGRAPHIC_ERASURE_CLAIM`, which is `False`, and the literal `True`
never appears against that key anywhere in this file. That is checked by a
test that reads this module's own source. A future editor who wants to
claim erasure has to change a named constant and delete a test, rather than
flip a boolean in a dict literal during a refactor.

THE CIPHER, AND WHY IT NEEDS OWNER SIGN-OFF
===========================================
ADR-0080 mandates "application envelope encryption for artifact bodies and
export bundles" and names NO cipher. Python's standard library ships no AES,
and this repository's CI is stdlib-only by design (`.github/workflows/
tests.yml`: "no pip install step exists on purpose"). So the T0 cipher is
built from `hmac`/`hashlib`:

    cipher_id = "hmac-sha256-ctr-etm-v1"
      * HKDF-SHA256 (RFC 5869) derives independent enc/mac subkeys.
      * SP 800-108 counter-mode KDF generates the keystream.
      * XOR keystream, then MAC the ciphertext (encrypt-then-MAC).
      * `hmac.compare_digest` on open -- never `==`.
      * 24-byte random nonce, never reused: it is BOTH the HKDF salt and
        the counter-mode context, so a repeated nonce under one key is the
        only way to repeat a keystream, and 192 bits of `os.urandom` makes
        that a non-event.

This is a real authenticated construction, not a placeholder -- but it is
NOT AES-GCM, and it is not FIPS-validated. Every stored envelope therefore
records its own `cipher_id`, and `CIPHERS` is a registry, so
`aes-256-gcm-v1` can be added later WITHOUT touching bytes already written.

ADR-0080's "One-way door? Yes" clause applies to data already admitted, so
the choice of T0 cipher needs the owner's explicit sign-off before any real
body is sealed. That is recorded as ADR-0083, status `proposed`.

WHY scrypt NEEDS AN EXPLICIT maxmem
===================================
`hashlib.scrypt(..., n=2**15, r=8, p=1)` raises
`[digital envelope routines] memory limit exceeded` on this build --
OpenSSL's default maxmem is 32 MiB and these parameters need marginally
more. Measured on Python 3.14.7 / this machine: with `maxmem=64 MiB` the
derivation succeeds in ~53 ms. Passing maxmem explicitly is required, not
optional, and dropping it does not fall back to weaker parameters -- it
raises.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import sqlite3
import struct
import time

#: ADR-0080's declared maximum shared-backup residency. Named, not inlined,
#: because `deletion_receipt` publishes it and a test asserts on it.
BACKUP_MAX_RESIDENCY_SECONDS = 35 * 86400

#: The one claim this module is not allowed to make. See the module
#: docstring; `deletion_receipt` reads this constant.
CRYPTOGRAPHIC_ERASURE_CLAIM = False

#: ADR-0080: "searchable embeddings, control metadata and shared database
#: pages cannot all be individually envelope-encrypted." Disclosed by name.
NOT_ENVELOPE_ENCRYPTED = ("embedding", "control_metadata", "index_pages")

DEFAULT_CIPHER_ID = "hmac-sha256-ctr-etm-v1"
ENVELOPE_MAGIC = b"NSENV1\n"

KEK_BYTES = 32
DEK_BYTES = 32
NONCE_BYTES = 24

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024        # see module docstring: REQUIRED
SCRYPT_SALT_BYTES = 16

MAX_ID = (1 << 63) - 1

REWRAP_BATCH = 64


class TenantKeyError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise TenantKeyError(reason_code, 3, details)


# ===========================================================================
# STEP 6 -- primitives
# ===========================================================================

def hkdf_sha256(ikm, salt, info, length):
    """RFC 5869 HKDF over HMAC-SHA256: extract then expand.

    Written out rather than reached for from a dependency because the whole
    construction below has to be stdlib-only. The extract step is what makes
    the two subkeys independent: XOR-ing a keystream derived from the same
    bytes that authenticate it is the classic way to make an
    encrypt-then-MAC construction not actually independent.
    """
    if not isinstance(ikm, (bytes, bytearray)) or not ikm:
        _refuse("hkdf_invalid_ikm")
    if not isinstance(length, int) or length <= 0 or length > 255 * 32:
        _refuse("hkdf_invalid_length")
    salt = bytes(salt or b"\x00" * 32)
    info = bytes(info or b"")
    prk = hmac.new(salt, bytes(ikm), hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_wrapping_key(passphrase, salt, *, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                        dklen=SCRYPT_DKLEN):
    """scrypt over the operator's wrapping secret.

    ADR-0080: the tenant key is "protected by a service hardware-backed key
    by default, an OS keystore/passphrase locally, or an approved
    customer-controlled key". This is the local-profile arm: a passphrase,
    stretched. `maxmem` is passed explicitly -- see the module docstring.
    """
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    if not isinstance(passphrase, (bytes, bytearray)) or not passphrase:
        _refuse("invalid_wrapping_secret")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 8:
        _refuse("invalid_kdf_salt")
    try:
        return hashlib.scrypt(bytes(passphrase), salt=bytes(salt), n=n, r=r, p=p,
                              dklen=dklen, maxmem=SCRYPT_MAXMEM)
    except (ValueError, MemoryError) as exc:
        raise TenantKeyError("kdf_unavailable", 1, {"error": str(exc)}) from exc


def _sp800_108_keystream(enc_key, nonce, length, label=b"nightshift-ctr"):
    """SP 800-108 counter-mode KDF used as a keystream generator.

    K(i) = HMAC-SHA256(enc_key, [i]_32 || label || 0x00 || nonce || [L]_32)

    The trailing length field is not decoration: fixing L in every block
    binds the whole keystream to one requested length, so a block generated
    for a 1 KiB body cannot be spliced into the keystream of a 1 MiB one.
    """
    out = bytearray()
    bits = struct.pack(">I", length * 8)
    counter = 1
    while len(out) < length:
        out += hmac.new(enc_key,
                        struct.pack(">I", counter) + label + b"\x00" + nonce + bits,
                        hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data, keystream):
    return bytes(a ^ b for a, b in zip(data, keystream))


def _subkeys(key, nonce, cipher_id):
    material = hkdf_sha256(key, nonce, b"nightshift|" + cipher_id.encode("ascii"), 64)
    return material[:32], material[32:]


def _mac_input(cipher_id, nonce, aad, ciphertext):
    """Length-prefixed, so no field can borrow bytes from its neighbour.
    Without the prefixes, (aad=b"ab", ct=b"c") and (aad=b"a", ct=b"bc")
    would MAC identically -- a canonicalisation bug that lets an attacker
    move bytes between the authenticated-but-public part and the encrypted
    part without changing the tag."""
    cid = cipher_id.encode("ascii")
    return b"".join([
        struct.pack(">I", len(cid)), cid,
        struct.pack(">I", len(nonce)), nonce,
        struct.pack(">I", len(aad)), aad,
        struct.pack(">Q", len(ciphertext)), ciphertext,
    ])


def _seal_v1(key, plaintext, aad, *, nonce=None):
    if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
        _refuse("invalid_cipher_key")
    plaintext = bytes(plaintext)
    aad = bytes(aad or b"")
    nonce = os.urandom(NONCE_BYTES) if nonce is None else bytes(nonce)
    if len(nonce) != NONCE_BYTES:
        _refuse("invalid_nonce")
    enc_key, mac_key = _subkeys(bytes(key), nonce, DEFAULT_CIPHER_ID)
    ciphertext = _xor(plaintext, _sp800_108_keystream(enc_key, nonce, len(plaintext)))
    tag = hmac.new(mac_key, _mac_input(DEFAULT_CIPHER_ID, nonce, aad, ciphertext),
                   hashlib.sha256).digest()
    return {"cipher_id": DEFAULT_CIPHER_ID,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii")}


def _open_v1(key, envelope, aad):
    if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
        _refuse("invalid_cipher_key")
    try:
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        tag = base64.b64decode(envelope["tag"], validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        _refuse("envelope_malformed")
    if len(nonce) != NONCE_BYTES:
        _refuse("envelope_malformed")
    aad = bytes(aad or b"")
    enc_key, mac_key = _subkeys(bytes(key), nonce, DEFAULT_CIPHER_ID)
    expected = hmac.new(mac_key, _mac_input(DEFAULT_CIPHER_ID, nonce, aad, ciphertext),
                        hashlib.sha256).digest()
    # compare_digest, never `==`: a short-circuiting compare on a MAC leaks
    # how many leading bytes were right, which is a forgery oracle.
    if not hmac.compare_digest(expected, tag):
        _refuse("envelope_auth_failed")
    return _xor(ciphertext, _sp800_108_keystream(enc_key, nonce, len(ciphertext)))


#: THE seam. A new cipher is a new entry here plus a new `cipher_id` on
#: newly written envelopes; envelopes already on disk keep naming the cipher
#: that wrote them and keep opening under it.
CIPHERS = {
    DEFAULT_CIPHER_ID: {"seal": _seal_v1, "open": _open_v1,
                        "key_bytes": 32, "nonce_bytes": NONCE_BYTES},
}


def seal(key, plaintext, aad, *, cipher_id=DEFAULT_CIPHER_ID, nonce=None):
    impl = CIPHERS.get(cipher_id)
    if impl is None:
        _refuse("unknown_cipher_id", {"cipher_id": cipher_id})
    return impl["seal"](key, plaintext, aad, nonce=nonce)


def open_sealed(key, envelope, aad):
    if not isinstance(envelope, dict):
        _refuse("envelope_malformed")
    cipher_id = envelope.get("cipher_id")
    impl = CIPHERS.get(cipher_id)
    if impl is None:
        _refuse("unknown_cipher_id", {"cipher_id": cipher_id})
    return impl["open"](key, envelope, aad)


def envelope_to_bytes(envelope):
    """Serialised form written to disk: a magic line then canonical JSON.
    The magic is what lets a reader tell a sealed object from a plaintext
    one WITHOUT a sidecar file -- a sidecar would not be removed by the
    path-scoped `purge_staged_object`, so the ciphertext would outlive its
    own deletion."""
    body = json.dumps(envelope, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":")).encode("ascii")
    return ENVELOPE_MAGIC + body


def is_sealed(raw):
    return isinstance(raw, (bytes, bytearray)) and bytes(raw[:len(ENVELOPE_MAGIC)]) == ENVELOPE_MAGIC


def envelope_from_bytes(raw):
    if not is_sealed(raw):
        _refuse("envelope_malformed")
    try:
        envelope = json.loads(bytes(raw[len(ENVELOPE_MAGIC):]).decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        _refuse("envelope_malformed")
    if not isinstance(envelope, dict):
        _refuse("envelope_malformed")
    return envelope


# ===========================================================================
# STEP 7 -- schema and lifecycle
# ===========================================================================

KEY_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tenant_key(
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  key_version INTEGER NOT NULL CHECK(key_version>=1),
  wrapped_kek BLOB NOT NULL,
  kdf_params_json TEXT NOT NULL,
  cipher_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  retired_at REAL,
  destroyed_at REAL,
  destroyed_reason TEXT,
  PRIMARY KEY(tenant_id, key_version));

CREATE TABLE IF NOT EXISTS object_key(
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  object_digest TEXT NOT NULL,
  key_version INTEGER NOT NULL,
  wrapped_dek BLOB NOT NULL,
  nonce BLOB NOT NULL,
  cipher_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(tenant_id, repository_id, object_digest));
CREATE INDEX IF NOT EXISTS object_key_by_version
  ON object_key(tenant_id, key_version, object_digest);

-- Rotation's resume point. One row per (tenant, target version): a rotation
-- that was killed resumes from `last_object_digest` instead of starting
-- over, and -- more importantly -- instead of stopping halfway with no
-- record of where halfway was.
CREATE TABLE IF NOT EXISTS rewrap_watermark(
  tenant_id INTEGER NOT NULL,
  to_key_version INTEGER NOT NULL,
  last_object_digest TEXT,
  updated_at REAL NOT NULL,
  PRIMARY KEY(tenant_id, to_key_version));

-- Same BEFORE-UPDATE-partition idiom as `source_snapshot_immutable` in
-- nightshift_provenance.py: name the transitions that ARE legal, ABORT
-- everything else. Exactly two are legal here.
--
--   RETIRE   retired_at NULL -> value, nothing else moves.
--   DESTROY  destroyed_at NULL -> value AND destroyed_reason NULL -> value
--            AND wrapped_kek emptied in the SAME statement.
--
-- The destroy transition REQUIRES the key material to go in the same
-- UPDATE. A row that said "destroyed" while still holding the wrapped KEK
-- would be a receipt for something that did not happen -- the exact species
-- of false evidence ADR-0080 exists to refuse.
CREATE TRIGGER IF NOT EXISTS tenant_key_destroy_is_one_way
BEFORE UPDATE ON tenant_key FOR EACH ROW
WHEN NOT (
      new.tenant_id IS old.tenant_id
  AND new.key_version IS old.key_version
  AND new.kdf_params_json IS old.kdf_params_json
  AND new.cipher_id IS old.cipher_id
  AND new.created_at IS old.created_at
  AND (
        (old.retired_at IS NULL AND new.retired_at IS NOT NULL
         AND new.destroyed_at IS old.destroyed_at
         AND new.destroyed_reason IS old.destroyed_reason
         AND new.wrapped_kek IS old.wrapped_kek)
     OR (old.destroyed_at IS NULL AND new.destroyed_at IS NOT NULL
         AND new.destroyed_reason IS NOT NULL
         AND length(new.wrapped_kek) = 0
         AND new.retired_at IS NOT NULL)
  )
)
BEGIN
  SELECT RAISE(ABORT, 'tenant_keys: only retire (retired_at NULL->value) and destroy (destroyed_at NULL->value with the wrapped KEK emptied) are legal updates to tenant_key');
END;
"""


def connect(path, *, create=True):
    """Open the key database. `path` is a FILE path, chosen by the caller --
    ADR-0080 allows the tenant key to be held by an OS keystore or an
    approved customer-controlled key, so this module does not get to decide
    where custody lives."""
    if not isinstance(path, str) or not path.startswith("/"):
        _refuse("invalid_key_db_path")
    if not create and not os.path.isfile(path):
        _refuse("key_db_missing", {"expected_path": path})
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    cx = sqlite3.connect(path, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    # Freed pages are overwritten. A destroyed KEK whose ciphertext is still
    # legible in a freed page of the key database is not destroyed.
    cx.execute("PRAGMA secure_delete=ON")
    cx.executescript(KEY_SCHEMA)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return cx


def _validate_id(value, reason_code):
    if (isinstance(value, bool) or not isinstance(value, int)
            or value <= 0 or value > MAX_ID):
        _refuse(reason_code)
    return value


def _validate_digest(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)):
        _refuse("invalid_object_digest", {"object_digest": value})
    return value


def _kek_aad(tenant_id, key_version):
    """The KEK envelope is bound to its own tenant and version. Moving a
    wrapped KEK row from tenant A to tenant B, or renumbering its version,
    changes the AAD and the tag stops verifying -- so a row swap inside the
    key database is detected on open rather than silently decrypting."""
    return f"nightshift-kek|tenant={tenant_id}|version={key_version}".encode("ascii")


def _dek_aad(tenant_id, repository_id, object_digest):
    return (f"nightshift-dek|tenant={tenant_id}|repo={repository_id}"
            f"|object={object_digest}").encode("ascii")


def create_tenant_kek(cx, tenant_id, *, wrapping_secret, t=None,
                      cipher_id=DEFAULT_CIPHER_ID):
    _validate_id(tenant_id, "invalid_tenant_id")
    if cipher_id not in CIPHERS:
        _refuse("unknown_cipher_id", {"cipher_id": cipher_id})
    t = time.time() if t is None else float(t)
    row = cx.execute(
        "SELECT MAX(key_version) FROM tenant_key WHERE tenant_id=?",
        (tenant_id,)).fetchone()
    key_version = (row[0] or 0) + 1
    salt = os.urandom(SCRYPT_SALT_BYTES)
    wrapping_key = derive_wrapping_key(wrapping_secret, salt)
    kek = os.urandom(KEK_BYTES)
    envelope = seal(wrapping_key, kek, _kek_aad(tenant_id, key_version),
                    cipher_id=cipher_id)
    kdf_params = {"kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                  "dklen": SCRYPT_DKLEN, "maxmem": SCRYPT_MAXMEM,
                  "salt": base64.b64encode(salt).decode("ascii")}
    cx.execute(
        "INSERT INTO tenant_key(tenant_id, key_version, wrapped_kek, "
        "kdf_params_json, cipher_id, created_at) VALUES(?,?,?,?,?,?)",
        (tenant_id, key_version, envelope_to_bytes(envelope),
         json.dumps(kdf_params, sort_keys=True), cipher_id, t))
    return {"tenant_id": tenant_id, "key_version": key_version,
            "cipher_id": cipher_id, "created_at": t}


def current_key_version(cx, tenant_id):
    _validate_id(tenant_id, "invalid_tenant_id")
    row = cx.execute(
        "SELECT key_version FROM tenant_key WHERE tenant_id=? "
        "AND retired_at IS NULL AND destroyed_at IS NULL "
        "ORDER BY key_version DESC LIMIT 1", (tenant_id,)).fetchone()
    if row is None:
        _refuse("tenant_kek_missing", {"tenant_id": tenant_id})
    return int(row["key_version"])


def get_tenant_key(cx, tenant_id, key_version):
    row = cx.execute(
        "SELECT * FROM tenant_key WHERE tenant_id=? AND key_version=?",
        (tenant_id, key_version)).fetchone()
    if row is None:
        _refuse("tenant_kek_missing",
                {"tenant_id": tenant_id, "key_version": key_version})
    return dict(row)


def unwrap_tenant_kek(cx, tenant_id, key_version, *, wrapping_secret):
    row = get_tenant_key(cx, tenant_id, key_version)
    if row["destroyed_at"] is not None:
        _refuse("tenant_keys_destroyed", {
            "tenant_id": tenant_id, "key_version": key_version,
            "destroyed_at": row["destroyed_at"],
            "destroyed_reason": row["destroyed_reason"]})
    params = json.loads(row["kdf_params_json"])
    salt = base64.b64decode(params["salt"])
    wrapping_key = derive_wrapping_key(
        wrapping_secret, salt, n=params["n"], r=params["r"], p=params["p"],
        dklen=params["dklen"])
    return open_sealed(wrapping_key, envelope_from_bytes(row["wrapped_kek"]),
                       _kek_aad(tenant_id, key_version))


def wrap_object_key(cx, tenant_id, repository_id, object_digest, *,
                    wrapping_secret, key_version=None, t=None, dek=None):
    """Mint (or return) the DEK for one object.

    Returns the PLAINTEXT DEK so the caller can `seal()` the body with it.
    Re-calling for an object that already has a key returns that key rather
    than minting a second one: two DEKs for one object means one of them is
    unreachable, and the object sealed under it is unreadable forever.
    """
    _validate_id(tenant_id, "invalid_tenant_id")
    _validate_id(repository_id, "invalid_repository_id")
    _validate_digest(object_digest)
    t = time.time() if t is None else float(t)
    existing = cx.execute(
        "SELECT * FROM object_key WHERE tenant_id=? AND repository_id=? "
        "AND object_digest=?", (tenant_id, repository_id, object_digest)).fetchone()
    if existing is not None:
        return {"key_version": int(existing["key_version"]),
                "cipher_id": existing["cipher_id"],
                "dek": unwrap_object_key(cx, tenant_id, repository_id,
                                          object_digest,
                                          wrapping_secret=wrapping_secret),
                "created": False}
    if key_version is None:
        key_version = current_key_version(cx, tenant_id)
    kek = unwrap_tenant_kek(cx, tenant_id, key_version,
                            wrapping_secret=wrapping_secret)
    dek = os.urandom(DEK_BYTES) if dek is None else bytes(dek)
    envelope = seal(kek, dek, _dek_aad(tenant_id, repository_id, object_digest))
    cx.execute(
        "INSERT INTO object_key(tenant_id, repository_id, object_digest, "
        "key_version, wrapped_dek, nonce, cipher_id, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (tenant_id, repository_id, object_digest, key_version,
         envelope_to_bytes(envelope), base64.b64decode(envelope["nonce"]),
         envelope["cipher_id"], t))
    return {"key_version": key_version, "cipher_id": envelope["cipher_id"],
            "dek": dek, "created": True}


def unwrap_object_key(cx, tenant_id, repository_id, object_digest, *,
                      wrapping_secret):
    _validate_id(tenant_id, "invalid_tenant_id")
    _validate_id(repository_id, "invalid_repository_id")
    _validate_digest(object_digest)
    row = cx.execute(
        "SELECT * FROM object_key WHERE tenant_id=? AND repository_id=? "
        "AND object_digest=?", (tenant_id, repository_id, object_digest)).fetchone()
    if row is None:
        # The tenant-level state is checked FIRST and reported in preference
        # to "missing", because after a crypto-shred the honest answer is
        # "the key was destroyed", not "there was never a key".
        destroyed = cx.execute(
            "SELECT COUNT(*) FROM tenant_key WHERE tenant_id=? "
            "AND destroyed_at IS NOT NULL", (tenant_id,)).fetchone()[0]
        live = cx.execute(
            "SELECT COUNT(*) FROM tenant_key WHERE tenant_id=? "
            "AND destroyed_at IS NULL", (tenant_id,)).fetchone()[0]
        if destroyed and not live:
            _refuse("tenant_keys_destroyed", {"tenant_id": tenant_id})
        _refuse("object_key_missing", {
            "tenant_id": tenant_id, "repository_id": repository_id,
            "object_digest": object_digest})
    kek = unwrap_tenant_kek(cx, tenant_id, int(row["key_version"]),
                            wrapping_secret=wrapping_secret)
    return open_sealed(kek, envelope_from_bytes(row["wrapped_dek"]),
                       _dek_aad(tenant_id, repository_id, object_digest))


def rotate_tenant_kek(cx, tenant_id, *, wrapping_secret, t=None,
                      batch=REWRAP_BATCH, progress=None):
    """ADR-0080: "Key rotation is resumable and audited."

    Resumable in the only way that matters: the DEK rewrap and the watermark
    advance happen in ONE transaction per object digest. A `kill -9` between
    two objects therefore leaves every object with exactly one readable key
    -- the already-rewrapped ones under the new KEK, the rest under the old
    one, which is still live because the old KEK is retired only AFTER the
    last object moves. There is no instant at which an object has no
    readable key.

    `progress` is an optional callback invoked after each committed digest.
    It exists so a crash test can be a REAL `kill -9` at a REAL point in the
    loop rather than a simulated early return.
    """
    _validate_id(tenant_id, "invalid_tenant_id")
    t = time.time() if t is None else float(t)

    # RESUME DETECTION, derived from state rather than from a flag.
    #
    # A rotation is UNFINISHED exactly when a newer KEK version exists and
    # either some object is still wrapped under an older one, or an older
    # version has not been retired yet. Deriving it this way (instead of
    # trusting the watermark row's existence) covers the narrower crash
    # window too: a `kill -9` landing between minting the new version and
    # writing the first watermark leaves no watermark at all, and a
    # detection that keyed off the watermark would mint a THIRD version and
    # strand the second.
    versions = [int(r[0]) for r in cx.execute(
        "SELECT key_version FROM tenant_key WHERE tenant_id=? "
        "AND destroyed_at IS NULL ORDER BY key_version", (tenant_id,)).fetchall()]
    if not versions:
        _refuse("tenant_kek_missing", {"tenant_id": tenant_id})
    top = versions[-1]
    remaining_on_older = cx.execute(
        "SELECT COUNT(*) FROM object_key WHERE tenant_id=? AND key_version<>?",
        (tenant_id, top)).fetchone()[0]
    unretired_older = cx.execute(
        "SELECT COUNT(*) FROM tenant_key WHERE tenant_id=? AND key_version<? "
        "AND retired_at IS NULL AND destroyed_at IS NULL",
        (tenant_id, top)).fetchone()[0]

    if len(versions) > 1 and (remaining_on_older or unretired_older):
        to_version = top
        from_version = versions[-2]
        created = False
        row = cx.execute(
            "SELECT last_object_digest FROM rewrap_watermark WHERE tenant_id=? "
            "AND to_key_version=?", (tenant_id, to_version)).fetchone()
        # A missing watermark simply restarts the sweep. That is safe, not
        # lossy: the rewrap query selects only objects NOT already on the
        # target version, so re-running it skips everything already moved.
        watermark = None if row is None else row["last_object_digest"]
    else:
        from_version = current_key_version(cx, tenant_id)
        to_version = create_tenant_kek(cx, tenant_id,
                                       wrapping_secret=wrapping_secret,
                                       t=t)["key_version"]
        watermark = None
        created = True
        cx.execute(
            "INSERT OR REPLACE INTO rewrap_watermark(tenant_id, to_key_version, "
            "last_object_digest, updated_at) VALUES(?,?,NULL,?)",
            (tenant_id, to_version, t))

    new_kek = unwrap_tenant_kek(cx, tenant_id, to_version,
                               wrapping_secret=wrapping_secret)
    rewrapped = 0
    while True:
        if watermark is None:
            digests = [r[0] for r in cx.execute(
                "SELECT DISTINCT object_digest FROM object_key WHERE tenant_id=? "
                "AND key_version<>? ORDER BY object_digest LIMIT ?",
                (tenant_id, to_version, batch)).fetchall()]
        else:
            digests = [r[0] for r in cx.execute(
                "SELECT DISTINCT object_digest FROM object_key WHERE tenant_id=? "
                "AND key_version<>? AND object_digest>? ORDER BY object_digest LIMIT ?",
                (tenant_id, to_version, watermark, batch)).fetchall()]
        if not digests:
            break
        for digest in digests:
            rows = cx.execute(
                "SELECT * FROM object_key WHERE tenant_id=? AND object_digest=? "
                "AND key_version<>?", (tenant_id, digest, to_version)).fetchall()
            cx.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    old_kek = unwrap_tenant_kek(cx, tenant_id,
                                                 int(row["key_version"]),
                                                 wrapping_secret=wrapping_secret)
                    aad = _dek_aad(tenant_id, int(row["repository_id"]), digest)
                    dek = open_sealed(old_kek,
                                      envelope_from_bytes(row["wrapped_dek"]), aad)
                    envelope = seal(new_kek, dek, aad)
                    cx.execute(
                        "UPDATE object_key SET key_version=?, wrapped_dek=?, "
                        "nonce=?, cipher_id=? WHERE tenant_id=? AND "
                        "repository_id=? AND object_digest=?",
                        (to_version, envelope_to_bytes(envelope),
                         base64.b64decode(envelope["nonce"]),
                         envelope["cipher_id"], tenant_id,
                         int(row["repository_id"]), digest))
                    rewrapped += 1
                cx.execute(
                    "INSERT OR REPLACE INTO rewrap_watermark(tenant_id, "
                    "to_key_version, last_object_digest, updated_at) VALUES(?,?,?,?)",
                    (tenant_id, to_version, digest, t))
                cx.execute("COMMIT")
            except Exception:
                cx.execute("ROLLBACK")
                raise
            watermark = digest
            if progress is not None:
                progress({"tenant_id": tenant_id, "to_key_version": to_version,
                          "object_digest": digest, "rewrapped": rewrapped})

    # Only now is the old KEK retired -- after every object it protected has
    # a key under the new one.
    for row in cx.execute(
            "SELECT key_version FROM tenant_key WHERE tenant_id=? "
            "AND key_version<? AND retired_at IS NULL AND destroyed_at IS NULL",
            (tenant_id, to_version)).fetchall():
        cx.execute("UPDATE tenant_key SET retired_at=? WHERE tenant_id=? "
                   "AND key_version=?", (t, tenant_id, int(row[0])))
    return {"tenant_id": tenant_id, "from_key_version": from_version,
            "to_key_version": to_version, "rewrapped": rewrapped,
            "minted_new_version": created, "resumed": not created}


def destroy_object_keys(cx, tenant_id, repository_id, object_digests, *,
                        reason, t=None):
    """The companion to `nightshift_provenance.purge_lineage_node`.

    Unlinking a lineage node makes the bytes unreachable through the graph;
    destroying the DEK makes them unreadable. ADR-0080 requires the second:
    deletion "walks typed `derived_from` lineage and removes every
    content-carrying descendant ... before deleting body/chunk bytes and
    keys". Call the two together or the descendant bytes survive as
    decryptable ciphertext with nothing pointing at them -- which is worse
    than either failure alone, because nothing lists them any more either.
    """
    _validate_id(tenant_id, "invalid_tenant_id")
    _validate_id(repository_id, "invalid_repository_id")
    if not isinstance(reason, str) or not reason:
        _refuse("destroy_reason_required")
    t = time.time() if t is None else float(t)
    destroyed = 0
    for digest in object_digests:
        _validate_digest(digest)
        cur = cx.execute(
            "DELETE FROM object_key WHERE tenant_id=? AND repository_id=? "
            "AND object_digest=?", (tenant_id, repository_id, digest))
        destroyed += cur.rowcount
    return {"destroyed": destroyed, "reason": reason, "at": t}


def destroy_tenant_keys(cx, tenant_id, *, reason, t=None):
    """The crypto-shred. Retires then destroys every KEK version, emptying
    the wrapped key material in the same UPDATE the trigger demands, and
    removes every wrapped DEK.

    What this DOES accomplish: envelope-encrypted bodies become unreadable
    immediately, including the copies already in a backup.
    What it does NOT accomplish is in `deletion_receipt`, and this function
    deliberately does not say a word about it -- the receipt is the only
    place the claim is made, so there is one place to audit.
    """
    _validate_id(tenant_id, "invalid_tenant_id")
    if not isinstance(reason, str) or not reason:
        _refuse("destroy_reason_required")
    t = time.time() if t is None else float(t)
    versions = [int(r[0]) for r in cx.execute(
        "SELECT key_version FROM tenant_key WHERE tenant_id=? "
        "AND destroyed_at IS NULL ORDER BY key_version", (tenant_id,)).fetchall()]
    for version in versions:
        cx.execute("UPDATE tenant_key SET retired_at=COALESCE(retired_at,?) "
                   "WHERE tenant_id=? AND key_version=? AND retired_at IS NULL",
                   (t, tenant_id, version))
        cx.execute(
            "UPDATE tenant_key SET destroyed_at=?, destroyed_reason=?, "
            "wrapped_kek=X'' WHERE tenant_id=? AND key_version=?",
            (t, reason, tenant_id, version))
    deks = cx.execute("DELETE FROM object_key WHERE tenant_id=?", (tenant_id,)).rowcount
    cx.execute("DELETE FROM rewrap_watermark WHERE tenant_id=?", (tenant_id,))
    # WAL: freed pages only reach the main database file at a checkpoint.
    # Without this, the secure_delete overwrite is still sitting in the -wal.
    cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"tenant_id": tenant_id, "key_versions_destroyed": versions,
            "object_keys_destroyed": deks, "reason": reason, "at": t}


# ===========================================================================
# STEP 8 -- the honesty surface (#400 condition 16)
# ===========================================================================

def deletion_receipt(cx, tenant_id, *, t=None):
    """The receipt ADR-0080 exists for.

    It is STRUCTURALLY unable to claim erasure: `cryptographic_erasure` is
    populated from `CRYPTOGRAPHIC_ERASURE_CLAIM`, a module constant whose
    value is `False`, and the literal `True` appears nowhere against that
    key in this module. `tests/test_tenant_keys.py` reads this file's own
    source and asserts that.
    """
    _validate_id(tenant_id, "invalid_tenant_id")
    t = time.time() if t is None else float(t)
    keys = [dict(r) for r in cx.execute(
        "SELECT key_version, created_at, retired_at, destroyed_at, "
        "destroyed_reason FROM tenant_key WHERE tenant_id=? ORDER BY key_version",
        (tenant_id,)).fetchall()]
    live_object_keys = cx.execute(
        "SELECT COUNT(*) FROM object_key WHERE tenant_id=?", (tenant_id,)).fetchone()[0]
    return {
        "tenant_id": tenant_id,
        "issued_at": t,
        "key_versions": keys,
        "key_versions_destroyed": [k["key_version"] for k in keys
                                   if k["destroyed_at"] is not None],
        "live_object_keys": live_object_keys,
        # ---- the disclosure -------------------------------------------
        "cryptographic_erasure": CRYPTOGRAPHIC_ERASURE_CLAIM,
        "backup_residency_until": t + BACKUP_MAX_RESIDENCY_SECONDS,
        "backup_max_residency_seconds": BACKUP_MAX_RESIDENCY_SECONDS,
        "not_envelope_encrypted": list(NOT_ENVELOPE_ENCRYPTED),
        "disclosure": (
            "Destroying the tenant key makes envelope-encrypted artifact "
            "bodies unreadable. It does NOT erase data already written to "
            "shared backups: that completes when the backup expires, with a "
            "declared maximum of 35 days. Searchable embeddings, control "
            "metadata and shared database pages are not individually "
            "envelope-encrypted and are therefore NOT covered by key "
            "destruction (ADR-0080)."),
    }


def _count_scoped(db_path, tables, tenant_id):
    """Count residual rows for one tenant in one SQLite adapter. A table the
    adapter does not have is reported as absent, never silently as zero --
    "the table isn't there" and "the table is empty" are different answers
    and only one of them means deletion worked."""
    if not os.path.isfile(db_path):
        return {"present": False, "residual": 0, "tables": {}}
    cx = sqlite3.connect(db_path, timeout=30)
    try:
        have = {r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        counts, residual = {}, 0
        for table in tables:
            if table not in have:
                counts[table] = None
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            n = cx.execute(f"SELECT COUNT(*) FROM {quoted} WHERE tenant_id=?",
                           (tenant_id,)).fetchone()[0]
            counts[table] = n
            residual += n
        return {"present": True, "residual": residual, "tables": counts}
    finally:
        cx.close()


_PROVENANCE_TENANT_TABLES = ("source_snapshot", "closure_entry", "lineage_node")
_PUBLICATION_TENANT_TABLES = (
    "publication_policy", "publication_reviewer", "publication_stage",
    "publication_authorizations", "publication_attempts", "publication_lineage")


def enumerate_canaries(state_root, cx, tenant_id, *, memory_projects=None,
                       memory_state_root=None):
    """ADR-0080: deletion "verifies per-adapter canaries". Deletion is not
    complete until EVERY adapter returns zero.

    Adapters walked:
      provenance.db, publication.db  -- per repository, tenant-scoped rows
      staged objects                 -- <tenant>/repos/*/objects/**
      key custody                    -- tenant_key / object_key
      memory.db                      -- see the caveat below

    THE MEMORY CAVEAT, stated rather than papered over: `memory.db` is
    project-scoped (`<state_root>/memory/<repository_id>/`) and its
    `artifact` table has NO `tenant_id` column, so this function cannot
    derive which of its rows belong to a tenant. `memory_projects` must be
    supplied explicitly. Passing `None` (the default) yields
    `complete: False` with `memory_scope_not_supplied` -- an adapter you did
    not scope is an adapter you cannot certify, and reporting it as zero
    would be exactly the vacuous evidence this repository already rejected
    once.
    """
    _validate_id(tenant_id, "invalid_tenant_id")
    if not isinstance(state_root, str) or not state_root.startswith("/"):
        _refuse("invalid_state_root")
    adapters = {}

    tenant_dir = os.path.join(state_root, "tenants", str(tenant_id))
    repos_dir = os.path.join(tenant_dir, "repos")
    repo_ids = []
    if os.path.isdir(repos_dir):
        repo_ids = sorted(name for name in os.listdir(repos_dir)
                          if name.isdigit())

    prov_total, pub_total, obj_total = 0, 0, 0
    prov_detail, pub_detail, obj_detail = {}, {}, {}
    for repo in repo_ids:
        root = os.path.join(repos_dir, repo)
        prov = _count_scoped(os.path.join(root, "provenance.db"),
                             _PROVENANCE_TENANT_TABLES, tenant_id)
        pub = _count_scoped(os.path.join(root, "publication.db"),
                            _PUBLICATION_TENANT_TABLES, tenant_id)
        prov_detail[repo] = prov
        pub_detail[repo] = pub
        prov_total += prov["residual"]
        pub_total += pub["residual"]
        objects_dir = os.path.join(root, "objects")
        count = 0
        for dirpath, _dirnames, filenames in os.walk(objects_dir):
            count += len([f for f in filenames if not f.endswith(".tmp")])
        obj_detail[repo] = count
        obj_total += count

    adapters["provenance_db"] = {"residual": prov_total, "detail": prov_detail}
    adapters["publication_db"] = {"residual": pub_total, "detail": pub_detail}
    adapters["staged_objects"] = {"residual": obj_total, "detail": obj_detail}

    live_keks = cx.execute(
        "SELECT COUNT(*) FROM tenant_key WHERE tenant_id=? AND destroyed_at IS NULL",
        (tenant_id,)).fetchone()[0]
    live_deks = cx.execute(
        "SELECT COUNT(*) FROM object_key WHERE tenant_id=?", (tenant_id,)).fetchone()[0]
    adapters["key_custody"] = {
        "residual": live_keks + live_deks,
        "detail": {"live_keks": live_keks, "wrapped_deks": live_deks}}

    mem_root = memory_state_root or state_root
    if memory_projects is None:
        adapters["memory_db"] = {
            "residual": None, "scoped": False,
            "reason": "memory_scope_not_supplied",
            "detail": {"note": "memory.db has no tenant_id column; the caller "
                               "must name this tenant's projects"}}
    else:
        residual, detail = 0, {}
        memory_dir = os.path.join(mem_root, "memory")
        if os.path.isdir(memory_dir):
            for name in sorted(os.listdir(memory_dir)):
                db = os.path.join(memory_dir, name, "memory.db")
                if not os.path.isfile(db):
                    continue
                mcx = sqlite3.connect(db, timeout=30)
                try:
                    have = {r[0] for r in mcx.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                    if "artifact" not in have:
                        continue
                    n = 0
                    for project in memory_projects:
                        n += mcx.execute(
                            "SELECT COUNT(*) FROM artifact WHERE project=?",
                            (project,)).fetchone()[0]
                    detail[name] = n
                    residual += n
                finally:
                    mcx.close()
        adapters["memory_db"] = {"residual": residual, "scoped": True,
                                 "projects": list(memory_projects),
                                 "detail": detail}

    unscoped = [name for name, a in adapters.items() if a.get("residual") is None]
    nonzero = {name: a["residual"] for name, a in adapters.items()
               if a.get("residual")}
    return {
        "tenant_id": tenant_id,
        "state_root": state_root,
        "adapters": adapters,
        "unscoped_adapters": unscoped,
        "nonzero_adapters": nonzero,
        "complete": not unscoped and not nonzero,
    }


def purge_lineage_with_keys(prov_module, prov_cx, keys_cx, state_root,
                            tenant_id, repository_id, node_id, *, reason,
                            t=None):
    """THE call site ADR-0080's deletion path requires: purge the lineage
    node AND destroy the data keys for every content digest it carried, AND
    remove the staged bytes.

    This lives here rather than in `nightshift_provenance` on purpose. The
    provenance module must not import key custody: ADR-0080 allows the
    tenant key to live in an OS keystore or a customer-controlled KMS, and a
    hard import edge from the provenance module would make the lineage graph
    unopenable wherever custody is unavailable. So the composition happens
    one level up, in the module that already depends on both.
    """
    if not isinstance(reason, str) or not reason:
        _refuse("destroy_reason_required")
    t = time.time() if t is None else float(t)
    victims = prov_module.content_carrying_descendants(
        prov_cx, tenant_id, repository_id, node_id)
    digests = set()
    if victims:
        placeholders = ",".join("?" for _ in victims)
        for row in prov_cx.execute(
                f"SELECT content_digest FROM lineage_node WHERE node_id IN "
                f"({placeholders}) AND tenant_id=? AND repository_id=?",
                list(victims) + [tenant_id, repository_id]).fetchall():
            if row[0]:
                digests.add(row[0])
    purged = prov_module.purge_lineage_node(prov_cx, tenant_id, repository_id, node_id)
    keys = destroy_object_keys(keys_cx, tenant_id, repository_id, sorted(digests),
                               reason=reason, t=t)
    removed = 0
    for digest in sorted(digests):
        if prov_module.purge_staged_object(state_root, tenant_id, repository_id, digest):
            removed += 1
    return {"purged_nodes": sorted(purged), "content_digests": sorted(digests),
            "object_keys_destroyed": keys["destroyed"],
            "staged_objects_removed": removed, "reason": reason, "at": t}
