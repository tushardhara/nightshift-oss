# ADR-0082: Migrate by authority epoch with one quiesced switch

Date: 2026-08-29 · Status: accepted  
Deciders: program · Proposers: architect (Codex), staff (Claude)  
Conversation: Codex architecture draft `95365315f4ca420c902b8a44a502e78a`; Claude architecture draft `6666d9e6384e43abbc7dbfca6c254b10`; DoD critique `a406245afc7c42059abe529c7e91bbb5` · Spec: docs/architecture/OVERVIEW.md


> **Provenance note.** This ADR was written on 2026-08-29 as part of the #400
> memory-architecture design, in a self-contained planning directory that
> numbered its decisions from ADR-0001. Those numbers collided with unrelated,
> already-accepted ADRs of the same number in `docs/decisions/`. On 2026-09-04 the
> set was renumbered into this repository's real sequence (ADR-0076 - ADR-0082) and
> landed unchanged apart from those number references. Original filename:
> `ADR-0007-migration-authority-epoch-quiesced-switch.md`.

## Context

Today's `.omni/`, generated documents and worktrees mix control, private
narrative and customer-visible files in one repository. Migration must classify
and preserve them without inventing authority, losing concurrent changes, or
allowing old and new stores to accept commands at the same time. It must be
crash-decidable and reversible through a soak.

## Options considered

### A — Freeze `.omni/`, then run new stores through a shadow period (proposed by staff (Claude))

Claude proposed inventory, import, then a shadow period in which new runs write
to the ledger and memory plane while `.omni/` remains frozen read-only, followed
by a cleanup change through the Publication Gateway and optional
re-publication. The gateway cleanup is excellent dogfooding and was accepted.
The shadow period as described was rejected because authority moves when new
runs begin, without an external epoch binding that makes the winning side
atomic and crash-decidable. Rolling back would require converting the delta
back into the legacy representation and risks two sides appearing writable.

### B — External authority epoch and single quiesced switch (proposed by architect (Codex))

Codex proposed inventory, consistent snapshot, command-disabled shadow import,
comparison, one quiesced atomic switch, reversible soak and decommission. An
external bootstrap registry records `active_backend` and monotone
`authority_epoch`; clients reject a stale epoch. Legacy remains the sole
authority through comparison, and the new adapter becomes sole authority at one
binding flip. This won, with Claude's cleanup operation made part of
decommission and the Publication Gateway's first real customer.

### C — Stop once and directly convert in place (rejected early)

A direct conversion would shorten the procedure but provide no shadow query
comparison, no checksummed high-water replay, and no safe decision after a crash
mid-cutover. With about 8,000 new rows/day and 280 artifact manifests/day even at
day one, an unverified partial import can silently lose state or provenance. It
also makes rollback depend on reopening stale `.omni/` files.

## Decision

Use a per-repository external bootstrap registry with `active_backend` and a
monotone `authority_epoch`. Migration has six phases:

1. Inventory and externally classify all legacy material without mutation.
2. Briefly quiesce to capture a consistent snapshot, high-water sequence, counts
   and checksums; then resume legacy authority.
3. Idempotently shadow-import into a namespace that denies live commands,
   including narrative redaction/finalization and pinned source closure.
4. Replay from the high-water mark and compare state, graph, claims, sequences,
   runs, artifact counts, sampled bytes and NSC-v1 query results until no
   unexplained mismatch remains.
5. Quiesce once, drain or expire workers, import the final delta, verify no
   active legacy lease, increment the epoch and atomically flip the backend.
   Retain the old snapshot read-only and journal post-switch commands during a
   reversible soak.
6. Decommission through reviewed cleanup, restore drill and policy-based legacy
   deletion.

There is **never an intentional dual authority**. Before the flip, legacy wins;
after the flip, new clients accept only the new epoch. A crash consults the
bootstrap binding. Rollback after the flip requires another quiesce, replay into
a fresh legacy copy, validation, a new epoch and another single switch—never
reopening the stale files directly.

The commit removing `.omni/` and unapproved generated documents is itself a
Publication Gateway operation: the gateway's **first real customer**. It uses
the same exact-byte, path, base-SHA, reviewer and expiry approval as every later
customer publication. Reverting that commit restores files as customer Git
history but does not restore their authority.

## Consequences

At every instant an operator can answer which store is authoritative and which
epoch a command must carry. Import and comparison can be repeated without
accepting commands in the new namespace. The reversible soak preserves a
validated path back without dual writes.

Cutover requires a real quiescence window and stopping or revoking legacy
processes that do not understand epochs. Rollback after the flip is a controlled
migration rather than a configuration toggle. Decommission waits for acceptance
and a restore drill.

## Cost

There is no ongoing infrastructure charge after decommission beyond the chosen
T0 stack. During migration, old and imported snapshots coexist, so storage may
temporarily approach 2 times the repository's control, narrative and retained
source footprint. The procedure adds inventory, comparison, journal, restore-
drill and review labor, plus one bounded quiescence window.

## One-way door?

No through the reversible soak: rollback is explicit, quiesced and validated.
After legacy snapshots and credentials are deleted, reversal requires restore
and import from an export and is therefore expensive, but the authority epoch
and migration receipts remain intelligible.

## Scale tier

T0 migration machinery. It uses the same control, body, source and publication
adapters selected for T0. Dataset size changes batch sizes and quiescence
planning, not the single-authority rule.

## Reversal trigger

Stop and redesign before cutover if the bootstrap registry cannot make one
backend/epoch flip atomic, if any active legacy writer cannot be revoked, if any
unexplained comparison mismatch remains after 2 complete replay attempts, or if
the measured final-delta import exceeds the approved 60-minute quiescence
window. None of these conditions authorizes a dual-authority shadow period.
