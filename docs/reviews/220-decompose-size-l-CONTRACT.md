# Review contract

## Identity

- Work item: issue #220 — "Require size:L work to decompose into S/M tasks
  before implementation" (task `220-build`)
- Committed contract path and blob OID: `docs/reviews/220-decompose-size-l-CONTRACT.md`,
  blob OID recorded in the freezing commit (this file's first commit on this
  build branch) — the freezing commit's own git blob hash is the authority,
  not this prose copy
- Contract version: 1
- Base SHA: `93b81e376c2bd0eb5f067bd8835f40cf5306a072` (`origin/main` tip at
  charter time, before this build's first commit)
- ADR: `docs/decisions/ADR-0049-decompose-size-l-before-implementation.md`.
  Codifies the ad hoc split crew_alpha already did live for issue #174 into a
  documented rule; does not amend ADR-0031/ADR-0032/ADR-0048.
- Review owner (cross-crew lead, not the author): crew_beta (round-robin —
  crew_alpha reviewed #219/PR #362 most recently, so crew_beta is next)
- Frozen at (UTC): 2026-08-30, after the build's implementation commit
  (`248f9c1e2501ed51a9aefb4262b702f2b0607e34`) but **before dispatching any
  reviewer** — applying #218/#219's own retro finding to mint the contract
  before the review round, not after. This is a Program-authored follow-up
  commit on the same build branch, since the build itself (like #219's) is a
  direct, single-author `platform` dispatch on a `program/**` path no crew
  owns, and this charter did not itself instruct `platform` to freeze the
  contract inline before building — recorded here as a process gap to close
  for the next `program/**` charter (see Contract amendments).

**Process note.** This work item is chartered directly to `platform`, not to
a crew: its expected code seams (`program/skills/roadmap/SKILL.md`,
`program/agents/crew_alpha/agents/pm/config.yaml`,
`program/agents/crew_beta/agents/beta_pm/config.yaml`) are exactly
`docs/owners.md`'s `program/**` row — no crew owns this path, changes
require an ADR. Edit leases `12`/`13`/`14` (holder `platform`, task
`220-build`) were acquired at charter time, before dispatch. **Correction
found during review-contract freeze:** lease 14 was acquired against
`program/agents/crew_beta/agents/pm/config.yaml`, which does not exist —
crew_beta's real PM config is at
`program/agents/crew_beta/agents/beta_pm/config.yaml`. `platform` caught and
corrected this itself during the build and edited the real file; Program
broke the stale lease 14 on the nonexistent path once the PR was already
open (`nightshift-ctl lease break --resource
'program/agents/crew_beta/agents/pm/config.yaml' --reason ...`). No lease
ever covered the real edited path — a genuine gap, though no contention
occurred (nothing else was concurrently touching that file). Filed as a
retro item: charter-time lease paths must be verified against the actual
filesystem, not assumed from an issue's prose "expected seams" list, before
`lease acquire`.

## Acceptance criteria

Verbatim from the GitHub issue body (`gh issue view 220`).

| ID | Exact criterion | Verification |
|---|---|---|
| AC1 | No `size:L` item is passed to an engineer. | `tests/test_size_l_decomposition.py` |
| AC2 | L decomposition produces only S/M executable leaves. | `tests/test_size_l_decomposition.py` |
| AC3 | Child tasks have observable outcomes, acceptance criteria and explicit exclusions using the existing Definition of Ready. | `tests/test_size_l_decomposition.py` |
| AC4 | Ordering dependencies are persisted through the control-plane dependency mechanism from #210 (`nightshift-ctl task dependency-add`). | `tests/test_size_l_decomposition.py` |
| AC5 | Independent children may run in parallel only when their dependencies/file scopes allow it. | `tests/test_size_l_decomposition.py` |
| AC6 | Parent L issue/item remains a planning container; completion is based on its child outcomes, not a giant implementation PR. | `tests/test_size_l_decomposition.py` |
| AC7 | Do not create children merely to mirror architecture layers; each child should be independently reviewable/mergeable where practical. | `tests/test_size_l_decomposition.py` |

Reviewer note: `tests/test_size_l_decomposition.py` is a stdlib
text-conformance regression test over both crew PM prompts and the roadmap
skill text (17 tests, confirmed red against base SHA before the change,
green after, per the build's own report). Cross-crew review should confirm
each AC actually maps to an assertion, not just that the suite passes.

## Explicitly out of scope

Issue's own list, carried forward verbatim: decomposing the entire
top-level PRD; new project object/table; dynamic crew creation;
critical-path optimizer; changes to readiness/budget enforcement.
Structurally guaranteed additions per the build's own PR description:

- No change to `bin/nightshift-ctl` — `task ready`, `task upsert`, `task
  dependency-add` all pre-exist (#211/#212, ADR-0031/ADR-0032) and are
  reused, not modified.
- No schema change.
- No change to ADR-0032's readiness semantics — `task ready` already
  refuses `L` mechanically; this PR only defines what a PM does with a
  blocked `L` candidate.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review (delegate: crew_beta, standing in for
      in-crew review per the same program-authored, no-crew-owner situation
      ADR-0048/#219's contract describes — there is no `pm`/`eng_*` seat
      inside program's own toolset for a `program/**`-owned item)
- [x] QA (delegate: crew_beta, same substitution)
- [x] Cross-crew review
- [x] Staff / ADR consistency (new ADR this round — ADR-0049 — cites no
      other ADR as amended; staff review is also mandatory per
      `docs/owners.md`'s `program/**` row, independent of
      `suggested_domains`)
- [x] Security — `nightshift-ctl review plan --task 220-build --repo . --head-sha
      248f9c1e2501ed51a9aefb4262b702f2b0607e34` (ADR-0042, run against this
      PR's exact, final head) returned `suggested_domains: [code_review,
      cross_crew, pm, qa, security, staff]`, `profile: HIGH_RISK`
      (`program/config.yaml`'s pre-existing sensitive-content term `auth`,
      matched in boilerplate prose across the touched PM configs and
      roadmap skill — same trigger pattern PR #362/#219 and PR #355/#218
      hit, not newly introduced by this diff) — security required
- [ ] UX — not in `suggested_domains`; no path under `conditional_globs["ux"]`
      touched

**Additional domain beyond the seven canonical rows: `platform`.** The
leases (`12`/`13`/`14`, one corrected post-hoc — see Identity) covering the
edited paths were acquired at charter time with `holder=platform` — the
same role this build was performed under. `platform` review is requested
alongside `staff`, matching the precedent set for PR #362 (#219) and PR
#355 (#218).

**Machine-readable checklist (ADR-0039, issue #231).** Per
`bin/nightshift_review_receipts.parse_required_domains()`: the seven
canonical `[x]`/`[ ]` rows above are the parsed, authoritative set. The
`platform` addition is recorded in prose only, outside the seven canonical
prefixes, for the same reason PR #362/#355's contracts recorded it that
way.

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| | | | | | |

## Contract amendments

None yet. v1 frozen after the build's own implementation commit but before
any reviewer was dispatched, per the standing retro discipline
(#218/#219's own recommendation, restated in Identity above).
