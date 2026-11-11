# ADR-0083: Register a stdlib T0 cipher behind a registry, and defer PostgreSQL parity

Date: 2026-09-04 · Status: **proposed**
Deciders: owner (pending) · Proposers: staff (Claude)
Relates to: ADR-0080 (tenancy, encryption, limits of deletion), ADR-0082 (migration by
authority epoch), ADR-0078 (NSC-v1, one contract two adapters) · Issue: #400

> **Status is `proposed`, not `accepted`, deliberately.** Both decisions below are
> the owner's to make, not the implementer's. The first commits bytes to a cipher
> under ADR-0080's own "One-way door? Yes" clause; the second leaves an epic
> condition unmet. Neither is a call an agent gets to close.

## Context

The #400 close-out built two of the epic's three remaining items: tenancy key
custody and the limits of deletion (ADR-0080), and migration authority epochs with
one quiesced switch (ADR-0082). Building them surfaced exactly two questions that
the accepted ADRs do not answer and that this work must not answer on its own
authority.

## Decision 1 — `hmac-sha256-ctr-etm-v1` as the T0 cipher, behind a `CIPHERS` registry

### The gap

ADR-0080 mandates "application envelope encryption for artifact bodies and export
bundles" and names **no cipher**. Python's standard library ships no AES, and this
repository's CI is stdlib-only by design — `.github/workflows/tests.yml` states it
in its own header: "stdlib-only by design — no pip install step exists on purpose."
So there is no path to AES-GCM that the gate deciding green can execute.

### What was built

`bin/nightshift_tenant_keys.py` registers one cipher:

    cipher_id = "hmac-sha256-ctr-etm-v1"
      HKDF-SHA256 (RFC 5869) derives independent enc/mac subkeys from the key
        and the nonce. Verified against RFC 5869 Appendix A.1's published vector.
      SP 800-108 counter-mode KDF generates the keystream, with the requested
        output length bound into every block.
      XOR keystream, then MAC the ciphertext — encrypt-then-MAC.
      hmac.compare_digest on open, never `==`.
      24-byte nonce from os.urandom, fresh per seal; it is both the HKDF salt
        and the counter-mode context, so a repeated keystream requires a
        repeated 192-bit nonce.
      Length-prefixed MAC input, so no field can borrow bytes from its neighbour.

This is a real authenticated construction. It is **not** AES-GCM and it is **not**
FIPS-validated.

### The seam

`CIPHERS` is a dict keyed by `cipher_id`, and every stored envelope records the
`cipher_id` it was sealed under — in the envelope itself, in `tenant_key.cipher_id`,
in `object_key.cipher_id`, and now in `publication_stage.cipher_id`. Adding
`aes-256-gcm-v1` later is a new registry entry plus a new `cipher_id` on newly
written envelopes. **Bytes already on disk keep naming the cipher that wrote them
and keep opening under it.** Nothing already sealed has to be rewritten.

### Why this needs sign-off before any real body is sealed

ADR-0080's own "One-way door?" section says: *"Yes for data already admitted under
the 35-day shared-backup promise."* The registry makes the cipher choice reversible
for FUTURE writes; it does not make it reversible for bytes already committed to a
backup under this construction. The owner should decide, before the first real
artifact body is sealed, whether:

- (a) `hmac-sha256-ctr-etm-v1` is acceptable at T0, or
- (b) the encryption path stays dormant until a vetted AES-GCM implementation is
  available, which requires either relaxing the stdlib-only CI rule or vendoring
  an implementation — both of which are their own decisions.

Nothing in the current tree seals a real body. `nightshift_publication.stage()`
refuses `encryption_required_key_version_missing` only when a `publication_policy`
row declares a `key_version`, and no policy row in this repository does.

## Decision 2 — PostgreSQL parity (#400 item 7) is deferred, with a recorded refusal

### What was built instead

`tests/nsc_conformance.py` lifts the six adapter-agnostic authority traces —
fencing, idempotency, concurrency, recovery, isolation, refusal — out of the
inline SQLite tests and expresses them against a six-callable adapter protocol.
`tests/test_nsc_conformance.py` asserts SQLite passes all six, asserts the traces
actually *discriminate* (a deliberately broken adapter fails them), and asserts
`require_adapter("postgres")` **raises** `postgres_adapter_unavailable`.

No PostgreSQL adapter was written. The refusal is asserted rather than skipped: a
`unittest.skip` prints OK and lets a reader conclude the property holds, which is
precisely the vacuous green this repository spent 2026-09-02 removing.

### The evidence, re-measured on 2026-09-04

Two of the four blockers this deferral was originally proposed on were **false**
when re-measured, and are recorded as struck rather than quietly carried:

| Claim | Verdict today | Measurement |
|---|---|---|
| docker daemon unavailable | **STRUCK — false** | `docker info` → Docker Desktop, server 29.7.2, running |
| pgvector's `vector.control` absent | **STRUCK — false** | `CREATE EXTENSION vector` **succeeds** on the local PostgreSQL 16.14. The file is genuinely missing from `postgresql@16/share/postgresql@16/extension/`, but the extension resolves elsewhere and installs — the conclusion drawn from the missing file was wrong. |

The deferral rests on the two that stand:

1. **No driver, and CI forbids adding one — decisive.**
   `import psycopg` / `psycopg2` / `pg8000` / `asyncpg` → `ModuleNotFoundError` for
   all four. `python3 -m pip list` shows 6 packages (meson, pip, pypdf, repo-coach,
   setuptools, wheel) and no database driver. CI has no `pip install` step, by
   stated design. An adapter written here is a module the gate that decides green
   can never execute — an **unverified second authority backend**, which under
   ADR-0082's single-authority rule is strictly worse than an absent one.

2. **The claims need infrastructure absent here at any price — decisive.**
   #400 conditions 16, 17 and 21 want HA failover, encrypted object storage, and
   restore evidence against a declared 35-day backup-expiry window. A single-node
   local instance cannot be evidence for "one HA PostgreSQL authority" however well
   it passes a trace.

And one **hazard**, reproduced on 2026-09-04, which is not an availability blocker
but constrains how any future parity suite must be written:

3. **The superuser false-green.** Against the local PostgreSQL 16.14, as the table
   owner, with `ALTER TABLE t FORCE ROW LEVEL SECURITY` and a
   `current_setting('app.tenant')` policy in place, `SELECT count(*)` returned
   **2 of 2 rows** — RLS silently bypassed, because `pg_roles` reports
   `rolsuper=t, rolbypassrls=t` for that role. The identical query as a freshly
   created `NOSUPERUSER LOGIN` role returned **1 of 2**, and with the GUC unset it
   failed closed: `ERROR: unrecognized configuration parameter "app.tenant"`.
   A parity suite written without a non-superuser role and an explicit
   `rolbypassrls = false` assertion would report cross-tenant isolation PROVEN
   while proving nothing.

All of the above — standing, struck and hazard — is carried in the raised
refusal's `details`, so a reader never has to take "unavailable" on trust and can
check whether it is still true.

## Consequences

**#400 items 1 and 2 close on this work. Item 7 does not. The epic's "Definition of
finished" is therefore NOT met and #400 stays open.**

What is now provable locally and is proven by tests in this tree:

- one tenant identity, uid → integer surrogate, ids never reissued
  (`tests/test_tenant_registry.py`)
- authority epochs, the single quiesced rowcount-CAS switch, rollback into a fresh
  copy only, and crash-decidability under a real `SIGKILL`
  (`tests/test_authority_epoch.py`)
- migration phases 1–4 rehearsed against a **copy** of this repository's own
  `.omni/` — 114 files, 2.3 MB — with the source proven byte-identical afterwards
  (`tests/test_migration_rehearsal.py`)
- key custody, envelope encryption, resumable rotation under a real `SIGKILL`,
  crypto-shred, per-adapter canaries, and a deletion receipt that structurally
  cannot claim erasure (`tests/test_tenant_keys.py`)
- NSC-v1 conformance on SQLite, and the recorded PostgreSQL refusal
  (`tests/test_nsc_conformance.py`)

What remains open for #400: item 7, and everything that depends on a
managed-production profile.

## One-way door?

**Decision 1: partly.** The registry keeps the cipher choice reversible for future
writes. It cannot make it reversible for bytes already written into a shared backup
— that is ADR-0080's own clause, and it is why sign-off must precede the first real
seal.

**Decision 2: no.** Deferring costs nothing that a later adapter cannot recover.
The six NSC-v1 traces are already written and backend-neutral, so the day a driver
and real infrastructure exist, the parity work is "implement six callables and run
`run_traces`", not "design a conformance suite".

## Reversal trigger

Revisit Decision 1 if a vetted AES-GCM implementation becomes reachable without
breaking the stdlib-only CI rule, or before the first real artifact body is sealed
— whichever comes first.

Revisit Decision 2 when a PostgreSQL driver is available to the CI gate **and** an
HA/backup environment exists to test against. Re-measure all five recorded claims
before reusing any of them; two were already false within a week of being written.
