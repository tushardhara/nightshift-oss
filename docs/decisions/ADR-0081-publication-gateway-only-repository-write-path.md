# ADR-0081: Make the Publication Gateway the only customer-repository write path

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: discovery product draft `aebc671e0c534bb393f8f8ad8caf85c4`; Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0006-publication-gateway-only-repository-write-path.md`.

## Context

Nightshift must keep customer repositories free of operational clutter while
still allowing a reviewed ADR, PRD, release note or other deliberate document to
become customer-owned. Publishing must never happen as a side effect of a
worker, memory indexer, source mirror, export or migration. “Approved” is
insufficient if approval remains valid after the bytes or target change.

## Options considered

### A — Ledger approval, path allowlist and single egress credential (proposed by staff (Claude))

Claude proposed a Publication Gateway holding the only customer-repository write
credential, refusing publication without a ledger approval decision, and
enforcing a per-tenant path allowlist. This cleanly separated Git ingress from
egress and was accepted. A decision ID alone did not bind authorization to the
actual bytes, path or base being written.

### B — One-use approval bound to the publication tuple (proposed by architect (Codex))

Codex proposed a one-use capability bound to exact content digest, target path,
expected base SHA, reviewer and expiry; any byte, path or base change
invalidates it, and the producing worker can never be the reviewer. This is the
stronger mechanism that makes accidental drift impossible. It was merged with
Claude's allowlist and single-credential framing.

### C — Let workers write branches or files after general review (rejected early)

A worker-held Git credential or reusable “document approved” flag makes
publication an ambient capability. It can write a different byte stream, choose
a different path, or race a changed base. Day one produces about 280 artifact
manifests/day; even one accidental write violates the customer-cleanliness
boundary. A later pull request does not undo an unauthorized repository write.

## Decision

PublicationPort is the **only write path into a customer repository** and holds
the only tenant-scoped egress credential. All producers stage an immutable
finalized artifact and have no direct repository-write capability. The gateway
enforces a per-tenant target-path allowlist.

An authenticated reviewer who is not the producing worker issues a one-use
approval bound to the **exact bytes through their content digest, exact target
path, expected base SHA, reviewer identity, policy version and expiry**. A change
to any field requires a new review. Publish observes and compares the customer
base, atomically consumes the approval for the attempt, and records the
resulting full commit and tree OIDs. A base mismatch is a conflict, never a
silent rebase. Identical retries return the idempotency receipt; approval cannot
authorize a second distinct publication.

Normal zero-file mode may wire the customer Git adapter because an accepted
document becomes customer-owned. No-generated-file mode wires only the reviewed-
patch adapter and gives Nightshift no repository write credential.

## Consequences

Workers, retrieval, source mirroring, exports and migrations cannot silently
place bytes in customer Git. Reviewers see the exact tuple they authorize, and
publication audit can prove what base and bytes produced a resulting commit.
Customer edits after merge remain customer content and are never overwritten
automatically.

Every content, path or base update requires review again. Publication may
conflict under normal repository movement and must be restaged. The gateway is a
high-value credential boundary that needs tight monitoring and rotation.

## Cost

The gateway is a module in the one T0 process and adds no separate service to the
approximately $150/month day-one or $2,000/month 100× estimate. It adds a scoped
Git credential, review state, allowlist policy, immutable receipts and audit
monitoring. Human review latency is intentionally measured in seconds or
minutes, not interactive milliseconds.

## One-way door?

No for the gateway adapter: another document or VCS system can implement the
same port. A completed customer commit and its immutable receipt are historical
facts; they are reversed by a new customer Git commit, never rewritten in the
audit trail.

## Scale tier

T0. Publication remains a reviewed, low-throughput operation at every modeled
scale. Scaling application processes does not distribute or weaken the one-use
authorization check.

## Reversal trigger

Add or replace a PublicationPort adapter when 1 signed customer requirement
names a non-Git authoritative document system, or when publish queue p95 exceeds
10 minutes for 7 days because gateway execution—not human review—is saturated.
Any observed publication whose bytes, path, base, reviewer or expiry differ from
its approval is a severity-one contract failure and blocks further egress; it
does not justify weakening the binding.

## Amendment (issue #694): pre-#694 `publication.db` is dropped, not migrated

Issue #694 makes `tenant_id` INTEGER across both `bin/nightshift_publication.py`
and `bin/nightshift_provenance.py`, moves `publication.db` beside `provenance.db`
under `<state_root>/tenants/<tenant_id>/repos/<repository_id>/`, and replaces the
caller-supplied `source_snapshot_json` column with a real provenance
`snapshot_id`. None of that is byte-compatible with a database written before it.
No data migration is provided, and none is owed: `bin/nightshift-ctl` wires
neither module, no `publication.db` or `provenance.db` exists anywhere in the
tree, and the only writers are the two modules' own `main()` and the test suites,
which build in temporary directories torn down per test. No row was ever written
through a supported operator path, so a pre-#694 `publication.db` is a
development artefact.

Being development-only is not a licence to be silent about it. The drop is
enforced by two mechanisms in code, not by this paragraph:

1. `resolve_publication_root` refuses `publication_legacy_root_present` (exit 3,
   naming the file) if the pre-#694 `<state_root>/publication/<repository_id>/publication.db`
   still exists. An operator can never end up with a TEXT-tenant database and an
   INTEGER-tenant database both live and neither obviously stale.
2. `connect()` refuses `publication_schema_pre_694` when it opens a
   `publication.db` whose `PRAGMA user_version` is 0 and which holds any row in a
   tenant-bearing table, naming the tables and their row counts. A version-0 file
   that is empty is dropped and rebuilt at the current version rather than
   adopted, because adopting it would silently keep the old column types.

One consequence to record explicitly, because it looks like a semantic change and
is not: `authorization_tuple_digest` canonical-JSONs `{"tenant_id": ...}`, and
`json.dumps(1)` differs from `json.dumps("1")`, so every computed authorization
digest changes value. The 7-tuple's field set, its CAS shape and all six refusal
cases are untouched — but a digest computed after #694 is not comparable with one
stored before it. That is safe only because this amendment holds. If the
drop decision is ever reversed, the digest change becomes a blocking migration,
not a footnote.
