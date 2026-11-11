# Backlog

Owned by each crew's `pm`. Ordered — the top of a section is what that crew builds
next. Nothing is dispatchable until it passes Definition of Ready.

Each crew owns its own section and never edits the other's. Cross-crew ordering is
the program's call.

## crew_alpha
| id | outcome | why now | pri | size | status | spec / ADR |
|---|---|---|---|---|---|---|

## crew_beta
| id | outcome | why now | pri | size | status | spec / ADR |
|---|---|---|---|---|---|---|
| #492 | omnigent server launches (both the exact-SHA release path and the systemd unit) never put the Postgres URI/password on argv — `ps aux` / `/proc/<pid>/cmdline` show no `postgresql://` or `--database-uri` on either launch path, and the server still serves against the real configured Postgres DB | live, currently-exploitable credential exposure on the deployment host (owner audit #205); security assessed + APPROVED the `-c/--config` mechanism (issuecomment-5502856485, 5502934678) | P1 | M | state:ready | docs/specs/492-credential-exposure-fix.md v1 |
| #388 | eligible bookkeeping-only PR observably skips full suite, reaches main within AC-8 window | proves #286 AC-3/AC-8 in observed practice | P2 | S | needs-dor (blocked: cross-crew, see .omni/decisions.md 2026-09-01) | docs/reviews/286-bookkeeping-fast-path-CONTRACT.md |

<!-- 2026-09-02 pm/crew_beta: added #492 at the top of crew_beta's queue, ahead of #388, because it's a live security exposure (P1) vs. #388's P2 bookkeeping-fast-path proof; not a reorder of existing items relative to each other, just where a new P1 lands per the stated priority order (unblocks-other-work > user-visible-breakage > ...). Mechanism fully security-approved before this item was written (issuecomment-5502856485, 5502934678); spec is docs/specs/492-credential-exposure-fix.md v1. DoR sign-off posted as issue #492 comment; labels swapped needs-dor -> state:ready same session. -->
