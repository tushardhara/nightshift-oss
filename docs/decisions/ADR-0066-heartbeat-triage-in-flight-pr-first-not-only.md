# ADR-0066: The in-flight-delivery-PR heartbeat triage rule becomes FIRST, not ONLY

- **Date:** 2026-09-01
- **Status:** Accepted — `staff` reviewed twice (ACCEPT-WITH-CHANGES both passes; all named
  changes applied and independently re-verified, see "Reconciliation status" and "Addendum"
  below). No crew owns
  `program/**` (`docs/owners.md`), so Program authors this directly per ADR-0047/ADR-0048/ADR-0056
  precedent, with an edit lease (`platform`, lease 47, `program/config.yaml`) held while writing.
- **Issue:** owner instruction, issue #205 comment `5497855539` (2026-09-01T17:32:03Z)
- **Run:** `fixture-review-231`
- **Deciders:** owner (the rule change itself) · program (this ADR, single-authored — see
  Process note)
- **Supersedes:** none. **Amends:** `program/config.yaml`'s heartbeat single-pick triage order,
  bullet 1 only (line ~557 pre-amendment). No other bullet, no `bin/nightshift-ctl` change, no
  schema change. **This ADR also carries a second, separately-scoped decision** (added in the
  same-day Addendum below): for the current wave, `program` is the sole ADR-number minter —
  `crew_alpha` and `crew_beta` ask Program for a number rather than self-assigning one from
  `docs/decisions/`. This is a wave-scoped process rule for both crews' own `docs/decisions/**`
  seam (per `docs/owners.md`), not a heartbeat-triage detail — flagged explicitly here per
  `staff`'s review so it isn't missed by a reader skimming only the title/Context.

## Process note

This is the same non-WA-5 case ADR-0056 already established for this exact file: no counted-agent
dispatch occurs, Program is authoring its own operating prose directly (`program/config.yaml`'s
own Hard Rule 1: "You author Markdown only"), and an edit lease is held rather than skipped so the
control plane's own bookkeeping stays honest about who is touching a sensitive, program-owned path.

## Context

Bullet 1 of the heartbeat's single-pick triage order read: *"An issue with an open delivery PR
outranks everything — shepherd that PR and only it."* Written to prevent a heartbeat from starting
unrelated work while a PR is mid-review (avoiding split attention and reducing the risk of two
heartbeats stepping on each other), this had a real cost observed today: for roughly the first two
hours of #490's review-gate blocker, this project's entire heartbeat-triage capacity sat idle
whenever only one delivery PR was open, even though normal worker slots were free and multiple
`state:ready` items shared no file, lease, or dependency relationship with that PR at all. "Only"
was doing more work than the original safety reasoning needed — the actual hazard is two workers
colliding on the same resource, not the mere existence of an open PR elsewhere.

## Options considered

### A — Leave bullet 1 as ONLY (status quo, rejected)

Simplest, but demonstrated cost today: idle worker capacity while genuinely disjoint, ready work
sits unstarted, for no safety reason once every practical collision surface (files, leases,
dependencies) already has its own independent mechanical gate (`docs/owners.md`, `nightshift-ctl
lease`, the task graph). Rejected — the rule was solving a problem other mechanisms already solve
more precisely.

### B — Replace with unrestricted parallel dispatch up to capacity (rejected)

Drop the priority language entirely; treat the in-flight PR as just another `state:ready` item in
the pool. Rejected: it demotes real shepherding urgency (a stalled review gate, a flaky CI re-run,
a blocked reviewer) to parity with routine new work, which is exactly the attention-splitting risk
bullet 1 originally existed to prevent. The in-flight PR's priority is a genuine, separate signal
from mechanical disjointness and should not be discarded to get disjointness's benefit.

### C — FIRST, not ONLY: keep priority, drop exclusivity (chosen)

The in-flight PR keeps first claim on attention and, implicitly, on any worker slot its own
continuation needs. It no longer prevents starting other work when slots are free and mechanical
disjointness holds. This is the owner's own framing, verbatim, and is the smallest change that
removes today's demonstrated idle-capacity cost without touching the actual collision-prevention
mechanisms (path ownership, leases, task dependencies) that already do the real safety work.

## Decision

Adopt Option C. `program/config.yaml`'s heartbeat triage bullet 1 is amended (diff in this PR) to:
the in-flight delivery PR is shepherded first; when a normal worker slot remains free after that,
also start any `state:ready` item that is mechanically disjoint from every in-flight task's
resources — checked via `docs/owners.md` path ownership, `nightshift-ctl dump`'s active
leases (no shared resource, no serialize-lease conflict), and the task graph (`depends_on`/
`blocked_by`, no edge either direction). Ambiguity in that check refuses the start (`state:blocked`
for that pairing this turn), it does not default to starting. This waives no other gate: DoR must
still be terminalized the same turn per ADR-0056, `task ready` is still consulted, `slot acquire`
still happens before dispatch, the frozen review contract and cross-crew review still apply
unchanged to whatever gets started this way.

Today's live instance of this rule: issue #477 (DoD D13, `docs/audit/CONCURRENCY.md`) started
alongside #490 — disjoint path (`docs/audit/CONCURRENCY.md` vs #490's `deploy/**`/
`bin/nightshift-ctl`), no lease conflict (verified via `nightshift-ctl dump` before dispatch), no
dependency edge (#477's own spec names none of the other nine D-condition issues as blocking).

## Consequences

- **Makes easy:** worker capacity is no longer idle merely because one PR is open, when other
  ready work has zero collision surface with it.
- **Makes hard / unchanged:** this does not relax path-ownership, lease, or dependency
  enforcement — those remain the actual safety mechanism, now doing double duty (they always
  gated collisions; they now also gate this bullet's "may I start something else" question, one
  mechanism, not two).
- Heartbeat triage logic gains one more disjointness check per free slot per turn — bounded by
  the number of open PRs (normally ≤ a small constant) times the `state:ready` pool size; not a
  scale concern at this project's current size.
- A heartbeat turn that starts a second item now touches two DoR terminalizations, two task
  registrations, and potentially two dispatches in one turn where it previously did at most one —
  reviewed and judged acceptable by the owner as a scheduling change, not a gate change.

## Reversal trigger

Revert to Option A (ONLY) if a future incident shows the mechanical disjointness check itself
(path ownership / lease / dependency-graph read) missed a real collision that caused two workers
to write conflicting state — that would mean the check is not as complete as this ADR assumes,
and the fix is either tightening that check or reverting to the simpler, safer-by-construction
ONLY rule until it is, not adding a fourth ad hoc signal on top.

## Addendum (2026-09-01, same day, owner-directed) — disjointness must be enforced, not just checked

The original Decision above described disjointness as a mechanical CHECK (path ownership, active
leases, dependency graph) performed before starting a concurrent item. Live experience the same
day this ADR was drafted showed that description was incomplete: an owner audit found several
worktrees writing concurrently while `nightshift-ctl dump`'s lease list held nothing for two of
them at that moment — the disjointness was true by discipline (nobody happened to collide), not
because anything refused a potential collision. Separately, three ADRs (`0064`/`0065`/`0066`) were
authored concurrently by three different sessions reading the same `docs/decisions/` directory at
slightly different times; they landed on distinct numbers, but nothing prevented two of them from
colliding (and one earlier draft this same day, before this ADR's own number was fixed, briefly
did collide with a real, different, already-existing `ADR-0062`) — `nightshift-ctl seq next`
allocates only migration numbers today, not ADR numbers, so ADR numbering was an unprotected
shared sequence at the moment this ADR was being written.

**Amendment to the Decision:** disjointness is now an ENFORCEMENT step, not an audit step —
`program/config.yaml`'s amended bullet 1 (this same diff) requires an EDIT lease on every path a
concurrent item's charter names as writable, acquired BEFORE dispatch, with the start refused (not
merely flagged) if any such path already has an active lease held by someone else. Multiple new
items sharing a path start serially, one lease-confirmation at a time, never "in parallel" on the
strength of `slot acquire` alone, which has no path column and cannot see this collision.

**ADR numbering, decided here rather than left open:** for this wave, exactly one seat mints ADR
numbers — `program`. Any agent proposing a new ADR asks Program for the number rather than reading
`docs/decisions/` and self-assigning one; Program determines it fresh each time from `origin/main`
plus every open PR's added ADR files (not from any single local working tree, which this same day
proved can be stale). This is a stated, minimal wave-scoped policy, not a permanent mechanism —
extending `nightshift-ctl seq next` to cover an `adr` sequence (mirroring how it already handles
migration numbers) is the real structural fix and is out of scope for this ADR; noted here as a
named follow-up rather than filed as a new issue, since it's a small, bounded implementation detail
of the numbering mechanism this addendum already decided, not a new open question.

## Review

`staff` review: pending (dispatched same session, run_id `fixture-review-231`) — includes this
addendum, added after the review dispatch went out; re-review requested if the addendum changes
the verdict. Security review: not required — this changes Program's own operating prose, heartbeat
scheduling logic, and lease-acquisition discipline only, no credential, secret, PII, or
external-input surface (`program/config.yaml`'s `prompt:` block is not on the sensitive-set per
`docs/owners.md`'s protected-paths list beyond requiring an ADR, which this is).
