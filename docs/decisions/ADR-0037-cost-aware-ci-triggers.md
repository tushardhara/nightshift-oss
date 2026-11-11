# ADR-0037: Test pull requests and the integrated main branch

Status: accepted by owner on 2026-08-28

## Context

The control-plane suite runs on every push to every branch and again for the
same commit when it belongs to an open pull request. Pure Program bookkeeping
commits under `.omni/` therefore launched two GitHub-hosted runners without
changing executable behaviour. Run `33150793142` and PR run `33150795967`, for
the same `.omni/ledger.md` commit, are a concrete example.

The Definition of Done requires pull-request evidence and a successful completed
workflow at the exact current `main` SHA. Its original wording also required
pushes on all branches, creating the duplicate. Some repository documents are
executable test inputs, so path exclusions would hide real regressions.

## Decision

Run automatically for `pull_request` and for pushes to `main`, plus explicit
manual dispatch. A feature-branch push starts no workflow until a PR exists; an
open PR receives exactly one `pull_request` run for its new head. Group runs by
workflow and PR/ref with `cancel-in-progress: true`, so a new PR head cancels its
older in-progress run and a newer run for the same ref cancels its predecessor.

> **Amended 2026-09-02 (owner, Tushar).** The second half of that grouping rule
> is withdrawn: pushes now group by **commit SHA**, and `cancel-in-progress`
> applies to `pull_request` events only. The PR half is unchanged and still
> correct.
>
> The rule as written defeated this ADR's own guarantee one paragraph below --
> "every `main` SHA still receives post-merge verification". Keying pushes on
> the ref put every merge commit into a single group, so each merge cancelled
> the previous merge's run. Observed 2026-09-02: `41f2f90` (#499), `9c215e5`
> (#497), `2dd0dd2` (#500) and `1f3066b` (#493) were cancelled in sequence --
> one of them 7 seconds in -- and `main` held no post-integration run at all
> between `3fe45f1` and `1f3066b`.
>
> This is the same harm the Rejected options section already refused under
> "Ignore bookkeeping paths": *"a skipped `main` run would leave the latest
> completed workflow SHA behind the current `main`, breaking the fixed-tip
> proof."* The concurrency rule inflicted it by another route, and silently,
> because a cancelled check renders neutral rather than red.
>
> Nothing else in this ADR changes. Every path still triggers CI; path
> exclusions remain rejected and still require the Definition of Done revision
> named under Reversal triggers.

Every path continues to trigger CI. The surviving PR run must belong to the exact
current PR head, and every `main` SHA still receives post-merge verification.
Release's local candidate and post-merge verification remain mandatory.

## Rejected options

- **Keep pushes on every branch:** duplicates the pull-request run for normal
  same-repository delivery.
- **Ignore bookkeeping paths:** a skipped `main` run would leave the latest completed
  workflow SHA behind the current `main`, breaking the fixed-tip proof.
- **Ignore all Markdown, `docs/**`, or `.omni/**`:** several tests read those
  contracts and operational configuration.
- **Rely on commit-message skip markers:** advisory and easy for autonomous
  sessions to apply inconsistently.

## Reversal triggers

Revisit path exclusions only through an explicit Definition of Done revision
that preserves exact-main verification. Restore all-branch pushes if GitHub
stops emitting `pull_request` synchronization events for new PR heads.
