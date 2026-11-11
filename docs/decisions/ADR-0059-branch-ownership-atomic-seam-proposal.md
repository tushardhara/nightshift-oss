# ADR-0059: Atomic branch-ownership record for #430 AC-4 (PROPOSAL, not built this wave -- routes to `staff` before any crew builds on it)

Date: 2026-08-31 · Status: proposed -- awaiting `staff` review
Deciders: crew_alpha lead · Proposers: crew_alpha lead (single-author; see note below)
Conversation: `6f984e1f398145f581d196fcc9e64e8f` (crew_alpha lead session, task `430-build`)
Spec: GitHub issue #430, DoR verdict comment 2026-08-31T14:22:40Z, AC-4
Issue: #430 AC-4 -- explicitly excluded from every child of the ADR-0049
decomposition (issues #454-#458); this ADR is the prerequisite for a FUTURE,
not-yet-filed follow-up issue, never itself a build authorization.

## Process note -- why this is single-author, and why that does not skip the gate

Per this org's standing rule, a new architecture-shaped ADR is not built on
until `staff` reviews it. #430's own DoR verdict is explicit that AC-4
"needs a new atomic seam" -- a real design decision, not a restatement of an
existing mechanism -- and the crew's charter requires this ADR be written
and routed to `staff` BEFORE any crew builds AC-4. This ADR is written now,
single-author by the crew_alpha lead, specifically so `staff` has a concrete
proposal to react to when a follow-up issue for AC-4 is eventually filed --
it is NOT a build authorization, and no code in this wave depends on it.
Unlike ADR-0058 (this same wave), AC-4 did not go through the two-vendor
design-debate proposal step, because nothing in this wave builds against it
-- spending two engineers' planning capacity debating an implementation that
cannot be built until `staff` clears the ADR would be capacity spent on a
decision with no near-term consumer. If AC-4 is chartered as its own issue
later, that charter's design-debate phase should treat this ADR's Decision
as one candidate, not as settled -- see Reversal trigger.

## Context

DoR-accepted AC-4 (2026-08-31T14:22:40Z verdict on #430): "before checkout,
branch creation, ref update, or push, consult an atomic control-plane
branch-ownership record keyed by (repo identity, fully-qualified ref);
refuse if another live seat holds it, naming holder and ref. Acquisition/
mutation must be atomic/CAS-based -- a check-then-act filesystem scan is
insufficient and explicitly rejected as satisfying this AC. Implementer must
also state whether `release`'s integration ref is exempt from or
exclusively owned under this same mechanism."

Grounding: `bin/nightshift-ctl`'s control plane already has a CAS-safe
uniqueness primitive proven in production for a structurally identical
problem -- the `leases` table:

```sql
CREATE TABLE IF NOT EXISTS leases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'edit',
  holder TEXT NOT NULL, task_id TEXT, note TEXT,
  state TEXT NOT NULL DEFAULT 'active',
  acquired_at REAL NOT NULL, expires_at REAL, released_at REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_lease
  ON leases(resource) WHERE state='active';
```

"At most one active row per resource string" is enforced by SQLite's own
unique-index constraint under `BEGIN IMMEDIATE`, not by a check-then-act
read followed by a write -- the same shape `task_claims`'s partial unique
index (`task_id WHERE state='active'`) uses for "at most one active claim
per task." `worker_semaphore` (`deploy/policies/omnipods_policies.py`) reads
this database directly, inside a `BEGIN IMMEDIATE` transaction, and fails
CLOSED when the db is missing or unreadable (`_fail_open()` requires an
explicit opt-out env var). AC-4 needs the identical shape for a different
key: `(repo_identity, ref)` instead of `resource` (an arbitrary path glob).

The `release`'s integration ref question (`INTEGRATION_REF =
"origin/program/discovery-state"`, `bin/nightshift-ctl` module scope) is a
single, well-known ref that one seat (`release`) advances routinely as part
of its own automated flow. #430's whole premise is that ref/branch
collisions between seats are the root cause of eleven traced P0/P1s (#65,
#66, #75, #89, #98, #101, #103, #107, #115, #120, #126) -- carving out an
exemption for exactly the ref most central to cross-crew integration would
reopen the class of bug this issue exists to close, for the ref where a
collision would be most damaging.

## Options considered

### A -- New `branch_ownership` table, same CAS pattern as `leases` (PROPOSED, tentative)

```sql
CREATE TABLE IF NOT EXISTS branch_ownership(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_identity TEXT NOT NULL,   -- e.g. resolved remote URL or a stable repo id
  ref TEXT NOT NULL,             -- fully-qualified, e.g. refs/heads/crew_alpha/454-build
  holder TEXT NOT NULL,          -- seat name
  task_id TEXT,
  state TEXT NOT NULL DEFAULT 'active',
  acquired_at REAL NOT NULL, expires_at REAL, released_at REAL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_branch_owner
  ON branch_ownership(repo_identity, ref) WHERE state='active';
```

A new `nightshift-ctl branch acquire/release` pair, structurally parallel to
`lease acquire/release`, called by a git wrapper before checkout/branch-
create/ref-update/push. Refusal names the holder and ref (`exit 3`,
`reason_code: ref_held`). Fails closed on a missing/unreadable db, matching
`worker_semaphore`'s existing discipline -- explicitly required by AC-2's
sibling wording, and the natural default for AC-4 too.

**`release`'s integration ref: NOT exempt, NOT permanently reserved either
-- participates in the same table like any other ref, but `release` is the
only seat whose normal workflow ever requests it.** The mechanism does not
special-case `release`; it protects `origin/program/discovery-state` the
same way it protects any feature branch, which is the more conservative and
more consistent answer given #430's own motivating incidents. A permanent
reserved-holder row (never released) was considered and rejected: it would
mean the ref is never actually checked for collision, which is exactly the
"exempt" framing the DoR comment asked to be ruled out, just spelled
differently.

### B -- Reuse the existing `leases` table directly, `mode='ref'`

Add a third `mode` value to `leases` (today: `edit`, `serialize`) instead of
a new table, keyed by `resource = "<repo_identity>#<ref>"` (a composed
string) rather than a real two-column key.

Rejected: `leases` is documented and consumed elsewhere (`docs/owners.md`,
`docs/CONTROL-PLANE.md`) as a PATH-ownership mechanism with two release
disciplines (PR-open vs PR-merged) that do not map onto "a seat is
currently using this ref for git operations" -- a ref ownership record needs
to be released on a different event (work complete / seat goes idle), not
on PR-open or PR-merge. Composing `resource` from two fields defeats the
column-level uniqueness the DoR's "keyed by (repo identity, fully-qualified
ref)" wording asks for -- a string-composition key is a weaker, string-
matching version of the two-column key, and is exactly the kind of ad hoc
resource-string convention `leases` already has to document carefully once;
a second such convention for a semantically different resource is a
maintenance cost with no offsetting benefit over a dedicated table.

## Decision (tentative -- gated on `staff`, not final until reviewed)

Option A: a new `branch_ownership` table structurally parallel to `leases`,
same `BEGIN IMMEDIATE` + partial unique index CAS discipline, own
acquire/release verb pair. `release`'s integration ref participates in the
mechanism like any other ref -- not exempt, not permanently reserved.

This Decision is explicitly NOT authorization to build. It is the concrete
proposal `staff` reviews. An `inconsistent-with` verdict supersedes this
section; a clean verdict still requires its own charter (a new, not-yet-
filed GitHub issue, its own DoR pass, its own task id) before any crew
writes code against it.

## Consequences

Makes easy: a proven CAS pattern (already load-bearing for `leases` and
`task_claims`) gets a third application, so reviewers and implementers have
two working precedents to check the new table against, not a novel
mechanism to evaluate from scratch. `release`'s workflow requires zero
special-casing in the acquire/release verbs themselves.

Makes hard: every git-mutating call site (checkout, branch-create, ref-
update, push) across every seat's tooling needs to route through the new
acquire/release calls for AC-4 to actually close the collision class -- this
ADR proposes the STORAGE and CAS mechanism, not the call-site wiring, which
is real, non-trivial follow-up work (structurally similar to the "preflight
before work starts needs launcher/dispatch integration, not a standalone
checker nobody calls" caveat #430's own DoR verdict already flags for AC-2).

Forecloses (for now): treating ref ownership as derivable from filesystem
state (a `git branch --list` scan or similar) -- the DoR verdict already
rules this out explicitly ("a check-then-act filesystem scan is
insufficient"), and this proposal does not reopen that question.

## Reversal trigger

If, when AC-4 is actually chartered as its own issue, a two-vendor design
debate (which this ADR did not run -- see Process note) surfaces a
materially better mechanism, or finds `branch_ownership` as specified here
does not compose cleanly with the actual git-wrapper call sites once they
are inventoried in detail, supersede this ADR rather than silently
reinterpreting it. This ADR's Decision is a starting proposal for that
debate, not a conclusion that pre-empts it.
