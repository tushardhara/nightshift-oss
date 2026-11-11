# ADR-0061: `reap` gets a delete path for stale worktrees and merged branches (#433)

Date: 2026-09-01 · Status: proposed -- awaiting `staff` review
Deciders: crew_alpha lead, single-author under WA-5 · Proposers: crew_alpha lead
Conversation: crew_alpha lead session, task `433-build`
Spec: GitHub issue #433, DoR verdict comment 2026-08-31 (`state:ready` applied),
tightened AC-1/AC-5 accepted contract; `.omni/decisions.md` 2026-08-31
"#433 DoR: reconciled BLOCKED→READY" entry

## Process -- why this is single-author (WA-5), and why it is not re-litigated this turn

Per the mandatory design-debate process, a two-vendor engineer proposal step
would normally precede this ADR. It cannot run: issue #236 (still open) is
the standing finding that `sys_session_send`'s `args` schema cannot carry the
`slot_id`/`task_id`/`generation` `worker_semaphore` requires for
`COUNTED_AGENTS` targets (`eng_claude`/`eng_codex`/`qa`/`reviewer_*`), so
in-crew dispatch for a proposal round is structurally sterile, not merely
inconvenient this wave. This is the same standing fallback disclosed for
#174, #217, #390 and #449 (see `docs/decisions/ADR-0043` through `ADR-0047`
for the pattern and the live reproductions of the rejection).

Prior instances of this fallback each re-confirmed the rejection live before
relying on it, per crew_alpha's own rule against inferring WA-5 from
precedent alone. This turn's charter (task `433-build`) explicitly instructs
"WA-5 ... you don't need to re-litigate this" and directs a single-author
build -- a Program-level waiver of the per-task reconfirmation step for this
specific task, not a claim that #236 is closed. The fallback is disclosed
here, and will be disclosed again in the final report exactly as #174/#217/
#390/#449 did.

What substitutes for the missing second proposer: this ADR is checked
against the DoR verdict's own independent, parallel `architect` (Codex) /
`staff` (Claude) reads of #433 -- both converged on the same two gaps this
ADR must close (see Context) before Program tightened the accepted
contract -- and `staff` review is the mandatory gate before crew_alpha
builds on this ADR, exactly as the charter requires. No code is written
against this ADR until that verdict lands in this conversation.

## Context

Issue #433, DoR-tightened: `reap` finds stale worktrees and dead remote
branches (20+ and 57 respectively, measured 2026-08-30) but has no delete
path -- every heartbeat re-detects and re-reports the same debris it cannot
act on. `audit_worktrees` (`bin/nightshift-ctl:4426-4503`) already computes
everything a delete decision needs: primary-worktree status, live-task
correlation via `_owning_task` (path-then-branch matching, all-matches, live
wins unconditionally -- `bin/nightshift-ctl:4360-4423`), and dirt counts via
`_worktree_dirt`. What is missing is a verb that acts on a `reapable`
verdict instead of only reporting it.

**The standing decision this issue asks to reverse.** The block comment
directly above `_worktrees` states, verbatim
(`bin/nightshift-ctl:4237-4243`):

> It uses `_git_ro` (defined with the spec helpers below) for every git
> call: rev-parse, status --porcelain, worktree list. Nothing here moves a
> HEAD, writes an index, or updates a ref -- it aims at OTHER agents' live
> checkouts, so read-only is the whole safety argument. Do not add a verb
> here that writes. Removing a flagged worktree stays a human's decision:
> the tool's job is that instance four is caught by a machine, not that a
> machine deletes it.

That comment's premise is that a human sits downstream of the flag and
decides. **That premise does not hold in this org.** This is an unattended,
ADR-driven operating model: `reap` runs on every heartbeat with no human
approving individual removals, `STATUS.md` has recorded "8 dirty worktrees
flagged (informational only, no delete subcommand ... none touched)" across
multiple heartbeats, and the 20+ stale worktrees / 57 dead branches this
issue responds to are the measured, compounding cost of a decision that
was correct for a human-in-the-loop system and is a no-op in this one --
cleanup nobody with standing to act ever performs is not a safety control,
it is debris with an excuse. This ADR reverses that comment's conclusion
(machine may delete) while keeping, and sharpening, its actual safety
argument (read-only detection stays separate from, and prior to, any
write). The replacement control is not "trust the audit blindly": it is
the tightened, opt-in, dry-run-capable, per-condition-explaining `--delete`
below, which is *more* conservative than the comment's own four informal
conditions because it closes two schema-correlation gaps the original
issue text left implicit (see next).

**The two gaps independent review found (both converged, `architect` and
`staff`, 2026-08-31).** The issue's original AC-1 ("not task-live, no
claim, no lease, no uncommitted changes") is not resolvable from the schema
as written: `task_claims.worktree` is populated only when a caller passes
`--worktree` (sparse in practice), and `leases.resource` is a file-path
glob with no defined worktree-key semantics -- there is no direct
worktree→claim or worktree→lease mapping to join on. The only unambiguous
mechanism already in the file is `_owning_task`'s existing task-id
correlation (worktree-path-then-branch matching, every match considered,
live wins unconditionally -- the same conservative asymmetry
`audit_worktrees` already applies for reporting). AC-1 also lacked an
explicit, unconditional primary-worktree exclusion: the four conditions as
literally written pass trivially for `/srv/project` itself in the window
between a Program commit and its next write, because no task row owns the
primary checkout. `audit_worktrees` already guards this
(`wt["primary"]` short-circuits to `verdict="primary"`,
`bin/nightshift-ctl:4465-4469`) -- the gap was that AC-1 didn't cite it as
a condition a delete path must also enforce, first, unconditionally.

Program reconciled both gaps into a tightened accepted contract (below,
"Decision") rather than leaving the issue BLOCKED, because closing them
needed a specification decision within Program's authority, not owner
input, a credential, or a purchase. Full reasoning:
`.omni/decisions.md`, 2026-08-31, "#433 DoR: reconciled BLOCKED→READY".

## Options considered

### A -- Leave `reap` report-only forever (status quo; rejected)

Keep `bin/nightshift-ctl:4237-4243`'s conclusion as written. Rejected: this
is the status quo the issue is filed against. The audit has run at every
heartbeat and correctly identified reapable debris for weeks; nothing
downstream of the flag has ever acted on it, because nothing downstream of
the flag exists. The cost is not hypothetical -- it is the 20+/57 counts
the issue measures, growing every heartbeat this stays true.

### B -- Delete path gated behind a human-approval step (rejected)

Add `--delete`, but require an external approval artifact (an issue
comment, a manual flag file, an owner sign-off recorded somewhere) before a
given worktree or branch may actually be removed. This is the most literal
repair of the original comment's premise -- keep the human, just make the
audit trigger the ask instead of silently logging it.

Rejected: this org has no step in its unattended loop where a human
reviews and approves individual removals -- Program itself runs
heartbeats without a human present, and `reap` is invoked automatically,
not by a person deciding "now". An approval gate with no approver is not a
safer version of automatic deletion; it is automatic *non*-deletion with
extra ceremony, i.e. option A wearing a flag. It would also invert AC-3's
tightened requirement that undecorated `reap` stay byte-for-byte unchanged
-- an approval-queue side effect on every heartbeat is itself a behavior
change to the no-flag path.

### C -- Opt-in `--delete` with machine-provable, multi-condition guardrails per resource (accepted)

Replace "a human decides" with "a delete-eligible resource must satisfy
every one of a closed, machine-checkable condition set, evaluated fresh,
with the failing condition named when it does not qualify" -- for
worktrees, the tightened AC-1(a-e); for branches, the tightened AC-5(f-h).
Opt-in (`--delete`), dry-run-capable (`--dry-run` lists without removing,
uniformly across both resource kinds), and undecorated `reap` is provably
unchanged (AC-3) because `--delete` is a strictly additive code path
gated behind a flag `cmd_reap`'s existing unconditional call to
`audit_worktrees` does not set.

Accepted. This is the design both independent DoR reviewers converged on
once AC-1/AC-5's ambiguities were resolved -- it does not invent a new
safety mechanism, it composes existing ones (`_owning_task` correlation,
`_task_is_live`, `_worktree_dirt`, the primary-worktree guard, a
freshly-fetched merge check) behind conditions that are each independently
falsifiable and independently reported.

## Decision

Ship `nightshift-ctl reap --delete` (and `--dry-run`, composable with
`--delete`) as the additive, opt-in path. Two resource kinds, two closed
condition sets, both evaluated fresh at delete time (not reused from an
earlier scan in the same run, to bound TOCTOU):

**Worktrees -- delete-eligible only if ALL hold (accepted AC-1):**

  a. **Unconditional, evaluated first:** `wt["primary"]` is `False`. This
     exclusion overrides all other conditions -- the primary worktree
     (`/srv/project`) is never delete-eligible regardless of how (b)-(e)
     evaluate. This is the existing `audit_worktrees` guard
     (`bin/nightshift-ctl:4465-4469`), cited here as a delete
     precondition rather than left as an assumed invariant -- the gap
     both independent reviewers found.
  b. No task row correlated via `_owning_task`'s existing path-then-branch
     matching (every match, not just the representative) is live per
     `_task_is_live`.
  c. No `state='active'` row in `task_claims` exists for any of those same
     correlated `task_id`s -- joined on `task_claims.task_id`, never on
     the sparse `task_claims.worktree` column alone.
  d. No `state='active'` row in `leases` exists held for any of those same
     correlated `task_id`s -- joined on `leases.task_id`, never on
     `leases.resource`, which has no defined worktree-matching semantics.
  e. Clean per `_worktree_dirt` (staged/unstaged/untracked all zero). A
     worktree whose git state is `unreadable` is retained, never treated
     as satisfying (e) -- absence of proof is not proof of absence.

**Remote branches -- deletable only if ALL hold (accepted AC-5):**

  f. Not `main`/`origin/main`, not `origin/HEAD` or any symref, and not
     `release`'s integration ref (`INTEGRATION_REF`,
     `bin/nightshift-ctl:3771`).
  g. Fully merged into `origin/main`'s tip, re-verified immediately before
     deletion -- not reused from an earlier scan in the same run. Reuses
     this codebase's existing merged-check idiom verbatim rather than a
     second mechanism: `_git_ro(repo, "merge-base", "--is-ancestor",
     branch_sha, main_sha)[0] == 0` (already load-bearing at
     `bin/nightshift-ctl:1944, 4034, 4036, 5406`), preceded by a fresh
     fetch of `origin/main`. Bounds the TOCTOU window between enumeration
     and delete.
  h. Name does not correlate (same name-matching `_owning_task` uses) to
     any task row whose state is NOT in `READINESS_TERMINAL_STATES`
     (`bin/nightshift-ctl:2041-2043` = `{"done", "merged", "dropped",
     "superseded", "integrated"}`) -- a branch shared by an earlier
     merged round and a later live round (a documented, routine pattern
     per `_owning_task`'s own docstring) is retained.

     **Why `READINESS_TERMINAL_STATES`, not `TERMINAL_TASK_STATES`
     (`bin/nightshift-ctl:4249`), for this specific condition.** The two
     sets answer different questions and are NOT interchangeable:
     `TERMINAL_TASK_STATES` is the worktree-litter set and deliberately
     excludes `integrated` (per the comment at `bin/nightshift-ctl:2029-
     2040`, `integrated`'s worktree question is out of that set's scope).
     `READINESS_TERMINAL_STATES` is ADR-0032's completion set and does
     include `integrated`. Issue #215 / ADR-0045 MERGE_BLOCKER-1 (staff,
     round 1) is the direct precedent for this exact confusion: the
     `budget_expired` branch reused `TERMINAL_TASK_STATES` for a
     completion check and so incorrectly let an ordinary, fully-shipped
     `integrated` task read `budget_expired`. Condition (h) asks "has this
     branch's owning work fully finished" -- a completion question, not a
     worktree-litter question -- so it must use `READINESS_TERMINAL_STATES`.
     Reusing `TERMINAL_TASK_STATES` here would repeat ADR-0045's mistake
     in the opposite direction: it would permanently RETAIN every branch
     whose owning task reached `integrated` (the single most common
     shipped end-state) because `integrated` reads as non-terminal under
     that set, silently defeating AC-5 for the common case rather than
     over-triggering it as the original MERGE_BLOCKER-1 did.

`--dry-run` applies uniformly to both paths: it lists exactly what
`--delete` would remove, without removing it. A worktree or branch failing
any one condition is retained, with the *specific* failing condition named
in the output (AC-2), matching the existing `verdict`/`note` convention
`audit_worktrees` already uses. "Nothing reclaimable" reports zero removed
and exits 0 (distinguishable from a failed scan); a partial removal
failure (permissions, open handles) is reported per-resource and the
command continues with the rest rather than aborting.

## AC-5's relationship to ADR-0059 -- explicitly independent, not deferred to it

ADR-0059 (proposed, not accepted, not a build authorization) proposes a
`branch_ownership` CAS table to arbitrate **writes** to a branch/ref by
live seats that might collide while both are mid-operation on the same
name (checkout, branch-create, ref-update, push). It is a concurrency
control for simultaneous *use*.

AC-5 as tightened here does not need that mechanism, because it is
narrower than what ADR-0059 protects: condition (h) already requires that
the branch correlate to **no task row in a non-terminal state** before it
is even considered for deletion, and condition (g) requires it be fully
merged into `main`'s freshly-fetched tip. A branch meeting both conditions
simultaneously cannot have a live seat mid-write on it -- "no live task is
using this name" and "fully merged" together rule out exactly the
collision shape ADR-0059 exists to arbitrate. ADR-0059's ownership record
would add a check against a hazard that AC-5(h)/(g) have already excluded
by construction; requiring it here would gate a narrower, delete-only
operation behind a broader, not-yet-accepted, write-arbitration mechanism
built for a different problem.

This scoping is a Program decision, recorded in `.omni/decisions.md`
(2026-08-31, "#433 DoR" entry), not a call made unilaterally by this ADR.

**Reversal trigger (specific to this scoping):** if ADR-0059 is later
accepted with a broader ref-lifecycle mandate that covers more than
concurrent-write arbitration -- e.g. if it comes to also govern *deletion*
of refs, not only contested writes to them -- revisit AC-5's deletion path
(condition h in particular) against it before further branch-deletion work
ships, rather than assuming this ADR's independence argument still holds
unchanged.

## Consequences

**Makes easy:** the org's actual debris (20+ worktrees, 57 branches,
growing every heartbeat) becomes something a machine can clear instead of
only narrate. `reap`'s existing report-only invocation is unchanged
(AC-3), so nothing currently depending on `reap`'s current output shape or
exit codes for the undecorated path is affected. Every condition an
eligible resource must satisfy is drawn from mechanisms `audit_worktrees`
already computes or (for branches) a fresh, cheap git query -- no new
schema, no new table, no new dependency.

**Makes hard:** correctness now depends on `_owning_task`'s correlation
being right, in a context (deletion) where a false "reapable" is
destructive and irreversible in a way a false "reapable" in a
report-only audit never was -- the audit could be wrong and nothing was
lost; `--delete` acting on the same wrong verdict removes someone's
worktree or a branch. This is why AC-1(b)-(d) insist on joining through
`_owning_task`'s existing, already-battle-tested correlation (the same
one `audit_worktrees` uses for reporting, with the same live-wins,
all-matches conservatism) rather than inventing a second, delete-specific
correlation that could disagree with the audit's own verdict.

**Forecloses (for now):** treating branch or worktree liveness as
derivable from anything other than the task table and git's own state --
no heuristic based on age, naming convention, or "looks abandoned." Also
forecloses folding AC-5 into ADR-0059's not-yet-accepted mechanism (see
above) as a precondition for shipping this narrower delete-only case.

## Cost

No new table, no new dependency, no new operational surface to run, patch
or monitor -- `--delete` and `--dry-run` are additive branches in
`bin/nightshift-ctl`'s existing `cmd_reap`/`audit_worktrees` machinery,
plus one new read-only-until-delete branch-scan function. Marginal
compute cost is the same git shell-outs `audit_worktrees` already pays for
worktrees, plus one fetch of `origin/main` and one `_git_ro(repo,
"merge-base", "--is-ancestor", ...)` call per candidate branch (condition
(g)'s exact mechanism, not a second parallel one), at heartbeat frequency.
At 100x today's debris volume (thousands of worktrees/branches) the linear
git-shell-out cost is the only thing that grows; nothing about the design
changes shape, though a very large fleet might eventually want the branch
scan pipelined rather than one-ref-at-a-time (not needed at today's scale
-- noted for the record, not built).

## One-way door?

Mixed. The **mechanism** is not a one-way door: `--delete` is opt-in, can
be disabled by omitting the flag with zero migration, and this ADR itself
can be superseded if `staff` or operational experience finds the
conditions insufficiently conservative. The **effect of a single delete
call** is a one-way door at the instance level: a removed worktree's
uncommitted state (already excluded by condition (e)) and a deleted
branch ref are not recoverable by this tool once gone, though a deleted
branch's commits remain reachable through `main`'s history since deletion
requires "fully merged" (condition g) -- the ref name is lost, the
content is not. This asymmetry is exactly why the conditions are a closed,
named, machine-checkable set rather than a heuristic, and why AC-6/AC-7
require fixtures for every retention case, not only the happy path.

## Scale tier

None yet, per `docs/architecture/SCALE-LADDER.md`. Trigger to move up: a
multi-repo fleet where `reap` must operate against repositories it cannot
git-shell into directly (today's whole safety argument, both for the
existing read-only audit and for this delete path, rests on `_git_ro`
being a real, local, inspectable git call); or a volume where the
per-branch freshly-fetched merge re-check (condition g) becomes a
heartbeat-latency bottleneck.

## Reversal trigger

Two, matching `.omni/decisions.md`'s own recorded reversal conditions for
#433:

1. If `staff`'s review of this ADR finds the human-decision-reversal
   argument (Context) or the ADR-0059-independence argument (dedicated
   section above) insufficient, that finding governs and this ADR is
   superseded or amended accordingly -- not silently reinterpreted.
2. If ADR-0059 is later accepted with a broader ref-lifecycle mandate,
   revisit AC-5's deletion path (condition h) against it before further
   branch-deletion work ships.
