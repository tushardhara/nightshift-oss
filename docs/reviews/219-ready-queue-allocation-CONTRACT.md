# Review contract

## Identity

- Work item: issue #219 — "Make Program pull implementation work from the
  READY queue" (task `219-build`)
- Committed contract path and blob OID: `docs/reviews/219-ready-queue-allocation-CONTRACT.md`,
  blob OID recorded in the freezing commit (this file's first commit on this
  build branch) — the freezing commit's own git blob hash is the authority,
  not this prose copy
- Contract version: 2 (see Contract amendments)
- Base SHA: `57fc0d0c59e93598d9dc3066d86b0e446a6808e0` (`origin/main` tip at
  checkpoint 18, wave `219`)
- ADR: `docs/decisions/ADR-0048-ready-queue-gates-implementation-allocation.md`.
  Cites and is bound by ADR-0032 (`task ready` derivation and precedence) and
  ADR-0031 (`tasks.size`, `task_dependencies`); amends neither.
- Review owner (cross-crew lead, not the author): crew_alpha (round-robin —
  crew_beta reviewed #218/PR #355 most recently, so crew_alpha is next)
- Frozen at (UTC): 2026-08-30, before the first implementation commit on this
  branch (`platform/219-build`)

**Process note.** This work item is chartered directly to the program
session, not to a crew: its expected code seams (`program/config.yaml`,
`program/skills/roadmap/SKILL.md`) are exactly `docs/owners.md`'s
`program/**` row — no crew owns this path, changes require an ADR. Edit
leases `10`/`11` (holder `platform`, task `219-build`) were acquired at
charter time, before dispatch — the sequencing fix #218's own retro
recommended (lease-at-charter, not after). See
`docs/decisions/ADR-0048-...md`'s §Process for why this build is
single-author without invoking Working Agreement 5 (this is not a
sterile-dispatch fallback; it is program's ordinary "author Markdown only"
job for a path no crew owns).

## Acceptance criteria

Verbatim from the GitHub issue body (`gh issue view 219`), plus the DoR
follow-up resolution this session's own pass settled before treating the
item as buildable (recorded in ADR-0048 §Context/§Decision).

| ID | Exact criterion | Verification |
|---|---|---|
| AC1 | Program queries `nightshift-ctl task ready` before normal implementation allocation. | `tests/test_ready_queue_allocation.py::TaskReadyQueriedBeforeAllocation` — PLAN AND ALLOCATE (step 4) and the `NEVER IDLE` ad hoc path both require a fresh `task ready` read before any normal-implementation charter |
| AC2 | Implementation charters are created only for tasks returned as READY. | Same class — "charter ONLY the task ids it returns"; a candidate absent from that set (unregistered, blocked, cycle-bound, or pending `L`) is not chartered "however finished its GH backlog prose looks" |
| AC3 | Program keeps the existing priority order among READY tasks: unblocks-other-work > user-visible-breakage > committed-outcome > new-value > cleanup. | `tests/test_ready_queue_allocation.py::PriorityOrderIsKeptNotReinvented` — the exact tag string is reused verbatim from `program/agents/crew_*/agents/*pm/config.yaml` (the pre-existing source of this vocabulary), not redefined |
| AC4 | If no eligible READY implementation work exists, Program waits, performs already-required review/handoff work, or reports the blocker; it does not invent unrelated implementation solely to keep agents busy. | `tests/test_ready_queue_allocation.py::EmptyReadySetDoesNotInventBusywork` — `NEVER IDLE` bullet names the three legitimate fallbacks (review/QA/staff/handoff work, `sys_timer_set`, or a first-line blocker report) and explicitly forbids a busywork substitute |
| AC5 | A newly integrated prerequisite can unlock work on the next scheduling pass without rewriting dependency prose. | `tests/test_ready_queue_allocation.py::UnlockOnNextReadNeedsNoProseRewrite` — relies on ADR-0032's derived-at-read-time semantics (unchanged by this ADR); dependency edges live in the control plane, not in charter/skill prose |
| AC6 | Existing global worker capacity remains authoritative after task selection. | `tests/test_ready_queue_allocation.py::WorkerCapacityStaysAuthoritativeAfterSelection` — `task ready` selects WHICH item; `slot acquire` (the pre-existing semaphore, `allocate` skill, unchanged) still gates whether a worker can actually start |

**DoR resolution (settled by this session's own pass, binding on this
contract).** AC1/AC2, read literally, require a task row to exist before the
charter that would be its first control-plane mention — but the only
existing writer of `task upsert --state pending --size` is the *owning
crew*, at Phase 1 (`design-debate`), which happens *after* Program has
already chartered that crew for the item. Resolution (ADR-0048 Option C):
Program itself registers a `pending` task row (id `<issue>-build`, size from
the issue's `size:` label, dependency edges from `blocked-by:#<n>` labels)
the moment an item passes DoR, at the existing TRACKER step — strictly
*before* it is a charter candidate at PLAN AND ALLOCATE. The owning crew's
existing Phase 1 `task upsert` call becomes an idempotent re-affirmation of
the same row (verified live this session: re-upserting `219-build` updates
the existing row, not a duplicate or conflict), so no crew-config
(`program/agents/crew_alpha/config.yaml`, `crew_beta/config.yaml`) edit is
required. A dependent whose prerequisite has no task row yet is left
unregistered — invisible to `task ready`'s universe, exactly as blocked as
`blocked_dependency` would read — rather than mis-encoded.

## Explicitly out of scope

Issue's own list, carried forward verbatim: automatic `L` decomposition;
big-PRD decomposition; a critical-path algorithm/service; dynamic crew
creation; changing control-plane readiness semantics. Structurally
guaranteed additions:

- No change to `bin/nightshift-ctl` — `task upsert`, `task dependency-add`,
  and `task ready` all pre-exist from #211/#212 (ADR-0031/ADR-0032) and are
  reused, not modified.
- No change to `program/agents/crew_alpha/config.yaml` or
  `program/agents/crew_beta/config.yaml` — their existing Phase 1 `task
  upsert` call is reused as-is (idempotent re-affirmation, see DoR
  resolution above), matching the issue's "smallest current scheduling
  seam" framing.
- No new priority-ranking algorithm — the five-tag order is read from, not
  computed over, the same vocabulary `program/agents/crew_*/agents/*pm/
  config.yaml` already defines.
- No schema change, no new CLI surface.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review (delegate: crew_alpha, standing in for
      in-crew review/QA per the same program-authored, no-crew-owner
      situation ADR-0048 §Process describes — there is no `pm`/`eng_*` seat
      inside program's own toolset for a `program/**`-owned item)
- [x] QA (delegate: crew_alpha, same substitution)
- [x] Cross-crew review
- [x] Staff / ADR consistency (new ADR this round — ADR-0048 — cites
      ADR-0031/ADR-0032 only; staff review is also mandatory per
      `docs/owners.md`'s `program/**` row, independent of `suggested_domains`)
- [x] Security — `nightshift-ctl review plan --task 219-build --repo . --head-sha
      3541c6521816873d4743481458fd176677d1a66f` (ADR-0042, run against this
      PR's exact, final head) returned `suggested_domains: [code_review,
      cross_crew, pm, qa, security, staff]`, `profile: HIGH_RISK`
      (`program/config.yaml`'s pre-existing sensitive-content terms
      auth/session/token — same trigger pattern PR #355/#218 hit, not newly
      introduced by this diff) — security required
- [ ] UX — not in `suggested_domains`; no path under `conditional_globs["ux"]`
      touched

**Additional domain beyond the seven canonical rows: `platform`.** The
leases (`10`/`11`) covering both edited paths were acquired at charter time
with `holder=platform` — the same role this build is performed under.
`platform` review is requested alongside `staff`, given the ownership
question `docs/owners.md`'s `program/**` row raises for any change to this
seam, matching the precedent set for PR #355 (#218).

**Machine-readable checklist (ADR-0039, issue #231).** Per
`bin/nightshift_review_receipts.parse_required_domains()`: the seven
canonical `[x]`/`[ ]` rows above are the parsed, authoritative set. The
`platform` addition is recorded in prose only, outside the seven canonical
prefixes, for the same reason PR #355's contract recorded it that way.

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| | | | | | |

## Contract amendments

**v2, 2026-08-30.** v1 (frozen before the first implementation commit)
guessed `security` was not expected, before the PR's real head SHA existed
to check against. Once head `3541c6521816873d4743481458fd176677d1a66f` was
pushed, `nightshift-ctl review plan` was run against it and returned
`security` in `suggested_domains` — v2 corrects the Security row from
`[ ]` to `[x]` with that evidence. This adds a required domain rather than
removing one, before any attestation or review round began against v1; no
other row, criterion, or scope statement changes.
