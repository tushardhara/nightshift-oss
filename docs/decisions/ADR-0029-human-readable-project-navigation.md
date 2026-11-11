# ADR-0029: Human-readable Project navigation

Date: 2026-08-27 · Status: accepted
Deciders: program · Proposers: issue #203 owner-directed naming review
Supersedes: ADR-0028 only where it preserves the names `Delivery`,
`<repo> — delivery`, and `View 1`

## Context

The Project is read mainly on a phone. `Delivery` does not identify its
repository, and GitHub's generated `View 1` name does not explain what it
contains. ADR-0028 correctly made Project updates additive, but its blanket
rule against renaming `View 1` also preserves this avoidable ambiguity.

## Decision

The canonical Project title is `Delivery — <repo>`; this repository therefore
tracks `Delivery — nightshift`. A configured or live existing Project is
eligible for migration only when its title is already canonical or is exactly
one of the legacy generated names: `Delivery` or `<repo> — delivery`. Any other
title is treated as human-owned and refused.

Without tracked configuration, discovery enumerates the complete Project list
and reuses exactly one canonical match. A unique repository-specific legacy
`<repo> — delivery` match is also safe to adopt. Multiple exact matches,
incomplete discovery, a closed match, or an unbound generic `Delivery` board
fails closed; the generic title does not prove which repository it belongs to.

Before changing a name, the initializer reads and validates the complete view
state. It renames only the unique view with number `1` and name `View 1`, and
only when no `All work` view exists. An existing `All work` is reusable only
when it is the unique view number `1`. The GraphQL mutation contains only
`viewId` and `name`; no view is deleted or reordered. Read-back must prove the
same id, number and configuration, and unchanged ids/configuration for
`Delivery now` and `Agent activity`. The Project title changes last, so a
failed run can safely converge on rerun.

## Rejected options

- Keep generic names: rejected because the owner still has to open the board to
  discover its repository and guess what `View 1` means.
- Rename every matching-looking view: rejected because a custom name or view
  number is human-owned state, not migration input.
- Delete and recreate the default view: rejected because it can reorder views
  and discard settings.

## Review evidence

Staff accepted this narrow naming decision on 2026-08-27. The independent
cross-vendor review was reconciled finding by finding: complete Project
discovery, ambiguous-title refusal, view-identity preservation, recovery order,
and idempotent read-back are explicit acceptance conditions with tests.

## Capacity, cost and operations

This remains tier 0: it adds no service, storage, dependency, recurring monetary
cost, or paid API product. It uses the existing bounded GitHub initializer API
reads and at most two name-only writes; at 100x the current repository or issue
volume, that read/write shape is unchanged. The only added operational surface
is validation within the existing GitHub initializer. The change is reversible
by another validated name-only mutation and does not alter Project content.

## Reversal trigger

Supersede this ADR if GitHub stops preserving view identity/configuration on a
name-only `updateProjectV2View`, or if Project number `1` is no longer the
generated default view. Also reverse the canonical names if owner testing shows
they make phone navigation less clear. Until then, any ambiguous or custom state
fails closed.
