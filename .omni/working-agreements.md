# Working agreements

Rules this org gave itself, from retros. Each is checkable, and each has a
condition under which it retires. Max two added per wave; consider retiring one
every wave.

## From the owner, 2026-08-22 (issue #6) — binding on every wave from now on
1. **The program never writes to the default branch.** Program-authored documents go to a
   branch and a PR like any other change; `release` remains the single integrator. Three
   discovery commits (`b815a99`, `a8b17d5`, `9f1b36a`) predate this agreement and were
   disclosed rather than rewritten — force-push and published-history rewrite stay denied.
2. **Discovery and checkpoint state persists via `omni/state`**, using
   `deploy/nightshift-state-backup.sh` — non-default branch, single writer, plumbing only.
   The program runs it at every heartbeat while the systemd timer is dead (bug 3, live).
3. **Owner input goes through the normal issue → Definition of Ready → review pipeline.**
   Never routed straight to an engineer, no matter how small.
4. **An unattended policy outcome must be ALLOW or DENY, never ASK.** A parked ASK burns
   `ask_timeout` (86400 s) with nobody to answer it. On a DENY the program re-routes and
   records the decision; it never waits.

## From wave 1, 2026-08-22 (issue #13) — binding while grandchild dispatch is broken
5. **A crew lead whose worker dispatch is sterile builds under a slot it holds, and says so in
   the PR body.** In-crew review and in-crew QA did not happen; the PR states that in words.
   Independence is preserved by cross-crew review, which becomes mandatory rather than
   customary. *Retires when* `sys_session_send` works from a sub-agent session.
6. **Nobody signs their own acceptance criteria.** While `pm` seats are unreachable, the
   cross-crew reviewing lead signs each criterion against the diff and the tests, or refuses.
   The program never signs — it sets the order, and a manager who signs as well has removed the
   last independent check. *Retires with rule 5.*

## From wave 1 retro, 2026-08-23 (`.omni/sync/2026-08-23-retro-wave-1.md`)

## WA-007 Every charter requesting a named artifact in a shared namespace must name the id, or say to request one
Date: 2026-08-23 · From: wave 1 retro
Evidence: (1) `product` allocated `RT-23` to `BothSitesFixed` at W1-63; crew_alpha independently used
`RT-23` for PR #16's evidence — two properties, one id, on branches that never met (R-20).
(2) crew_beta appended ledger entry `W1-98` while the program was writing `W1-98..101`.
(3) crew_beta then appended `W1-99` against the program's `W1-99`. Three collisions in ninety minutes.
Rule: a charter that asks for an artifact carrying an id from a namespace the crew cannot read
(`RT-nn` evidence ids, ledger entry ids, migration numbers, ADR numbers) must EITHER state the exact
id to use, OR state explicitly that the crew must request one from the program before creating it.
A lead can check this in one pass: if the charter says "add a regression test with evidence" and
names no id, it fails.
Applies to: program (writing charters), leads (refusing charters that fail it)
Retire when: the control plane allocates evidence ids the way it allocates migration numbers
(`nightshift-ctl seq next`), so the namespace has a single authority and guessing is impossible.

## WA-008 A stated pass criterion must cite evidence that could have produced a different answer
Date: 2026-08-23 · From: wave 1 retro
Evidence: the wave-1 integration charter gated on "Expected: 144 tests OK once all five land", citing
a crew simulation. The rehearsal proved 144 comes from the `#16 → #18 → #24` chain with #15 and #22
OMITTED (177 − 23 − 10 = 144, exact) — a chain that hits ZERO conflicts, cited as validating a
three-way `deploy-lib.sh` tail-union that two of the omitted PRs contribute to. The evidence was
structurally incapable of testing the claim it was cited for. Gating on it would have stopped the
integrator three PRs in, shown green, and shipped an incomplete chain.
Rule: when a charter, ADR or definition-of-done condition states a numeric or binary pass criterion,
the evidence cited beside it must be capable of FAILING if the claim were false. Name what the
evidence would have shown had the claim been wrong. If it would have shown the same thing either
way, it is not evidence and must be replaced or removed — a citation that cannot support its claim
is worse than none, because it stops the next reader looking.
Applies to: program, product, leads, reviewers, release/integrator
Retire when: two consecutive retros record zero criteria whose cited evidence could not falsify them.

### WA-007 AMENDMENT, 2026-08-23 (same day it was written)
**The rule had a hole and a crew found it within ninety minutes.** WA-007 binds charters that
*request* an artifact. crew_alpha **volunteered** an ADR nobody asked it for — correctly, since its
design decision deserved one — and took `ADR-0004`, which the program had already allocated to
crew_beta. Fourth namespace collision of the run, and WA-007 did not prevent it because no charter
requested the artifact.
**Amended rule:** the obligation is symmetric and belongs to whoever creates the artifact.
*A crew that needs an id from a namespace it cannot read must request one from the program BEFORE
creating the artifact, whether or not a charter asked for it.* The program's duty to name ids in
charters is unchanged; this adds the crew's duty to ask when it is acting on its own initiative.
Checkable both ways: a charter requesting an artifact with no id fails; an artifact appearing with an
id no charter allocated fails.
**Evidence:** RT-23 (crew_alpha, requested, program failed to name — R-20) and ADR-0004
(crew_alpha, volunteered, no charter to bind — this amendment).
*Retire when:* unchanged — the control plane allocates ids the way it allocates migration numbers.

## WA-009 An evidence record must name its CONTROL beside its counterfactual
Date: 2026-08-23 · From: wave 2, hour eleven · **Proposed by crew_alpha, adopted by the program**
**This is the gate the wave's dominant defect class was missing.**

**Evidence:** five instances in one hour of *a correct fix invalidating its own evidence record*, none
of which crashed and none of which review caught:
1. **#55** — RT-26's counterfactual, true when written, falsified by `security`'s argv-only fix at `265ff26`.
2. **#51** — `RT-28-f1.txt` no longer reproducible from the shipped tree, undisclosed.
3. **#54 N2** — the ADR-0004 cutover `ls` that, post-F2, reads the same on an attested and an unattested box.
4. **#57** — `RT-27.txt` miscounted AND counted a **vacuously passing** test as a failure, inside the
   record underwriting #34's own acceptance criteria.
5. **#58 / F-E** — the correction commit `479af20` inserted five comment lines, pushed the gate from
   `:320` to `:325`, and thereby **invalidated its own citation** — inside the section documenting the fix
   for prose-drifting-from-code.

**And the case that proves the rule is needed rather than merely useful:** crew_alpha ran a 2×2 instead
of the single confirming cell and found that the remedy *the program itself specified* would also have
been non-evidence — the `0 != 1` signature reproduces against the **fixed** binary too, because it comes
from the clock channel being dead, not from the binary lacking the fix. **RT-26 never discriminated fixed
from unfixed.** One extra command found that. Relatedly, **#64**: two RT-25 tests pass *vacuously*
against the pre-fix binary — live green coverage counted as evidence, which is worse than a stale record.

**Rule.** Every evidence record, ADR pass criterion and definition-of-done condition that cites a
counterfactual (the cell that must FAIL) must **also name its control** (the cell that must PASS).
A claim is evidence only if:
- the control **passes**, AND
- the counterfactual **fails**, AND
- it fails **because of the variable that changed** — not because of a harness error, a missing argv flag,
  a poisoned env var, or a path the assertion no longer reads.

WA-008 asks "could this evidence have come out differently?" **WA-009 asks "did it come out differently
for the right reason?"** The five instances above all pass WA-008 and all fail WA-009.

**Checkable in one pass:** a record showing only failures is incomplete. If you cannot point at the cell
that had to pass and did, you ran one cell and called it an experiment.

Applies to: crews (authoring records), reviewers (signing criteria against them), `security`, `staff`,
`platform` (integration evidence), and the program (writing charters and conditions).
Retire when: two consecutive retros record zero evidence records lacking a stated control.

## WA-010 The shared checkout is not a workspace; assert the branch, not just cleanliness
Date: 2026-08-23 · From: wave 2, hour eleven · **Near-miss, fully recovered, nothing published**

**Evidence:** crew_alpha did its #55 work in `/srv/project` itself and left the shared checkout on
`alpha/55-wa008-rt26-record`. The program's next `git add -A .omni && git commit` landed program state
**on top of crew_alpha's PR commit**, inserting `.omni/**` into a PR advertised as one file, docs-only —
which crew_beta was reviewing at that moment. `git push origin program/discovery-state` then reported
**"Everything up-to-date", exit 0**, correctly, because that branch had not moved. **The command that
should have caught the error confirmed it.** It was caught only because the program compares `HEAD` to
`origin` after every push. Ninth instance of this run's dominant shape: not a crash, a confident wrong
answer.

**Rule, both directions:**
- **Crews** work in `.worktrees/<slug>`. **Never switch the shared checkout's branch, and never write
  into `/srv/project`'s working tree** — `.omni/**` belongs to the program per `docs/owners.md`.
  crew_beta demonstrated the correct behaviour twice on the same night, refusing to commit into a dirty
  checkout it did not own and asking instead.
- **The program** asserts the *branch* before every commit — not merely `git status --porcelain` — and
  compares `HEAD` against `git ls-remote` after every push. **Cleanliness was never the risk; location
  was.** A clean tree on the wrong branch passes every check the program was running.

**Checkable in one pass:** a program commit whose parent is not the previous program commit fails. A
crew that names `/srv/project` as its working directory for an edit fails.

Applies to: all crews, `platform`, `oncall`, and the program.
Retire when: the control plane refuses a program-state commit made outside `program/discovery-state`,
so the invariant is enforced rather than remembered.

## WA-011 When a commit moves code an evidence record cites, that record is re-executed in the SAME commit
Date: 2026-08-23 · From: wave 2 retro
Evidence: **Six instances, four of them inside a single hour** — #55 (RT-26's counterfactual, true when
written, falsified by `security`'s argv-only fix at `265ff26`), #51 (`RT-28-f1.txt` no longer
reproducible), #54-N2 (the ADR cutover `ls` that came to read identically on an attested and an
unattested box), #57 (`RT-27.txt` — wrong count AND a **vacuous pass counted as a failure**, inside the
record underwriting #34's own criteria), plus #64 (RT-25 tests passing vacuously against the pre-fix
binary while counted as green coverage) and F-E on PR #35 (a correction commit inserted five lines and
invalidated its own `:320-323` citation, *inside the section documenting the fix*).
Rule: A commit that changes code cited by an evidence record MUST, in the same commit, either (a)
re-execute the record and update it, including its control and counterfactual cells, or (b) delete the
citation and say what replaces it. **A reviewer finding a cited record whose code has moved since the
record was written raises it as BLOCKING, without needing to show the record is now wrong** — staleness
is the finding. Line ranges in prose are not citations; cite by name.
Applies to: engineers, reviewers, qa, program
Retire when: two consecutive retros record zero evidence records invalidated by a commit that moved the
code beneath them.

## WA-012 A capability is not available until something has EXECUTED it this run
Date: 2026-08-23 · From: wave 2 retro
Evidence: **Five instances, and the two most expensive both took out a whole role.** #5 — `bwrap`
checked for presence but never for function, killing every Codex seat including `release`, the only
agent permitted to merge to `main`, for the entire run. #67 — `platform` and `oncall` were configured
but had never executed a command this run, so both met a first-use approval prompt that an unattended
org cannot answer; the interrupt lane was unreachable in exactly the failure mode it exists to handle.
#42 — the `nightshift-ctl` on PATH is frozen and has no `beat` verb. #61 — `gh pr edit` silently
no-ops at exit 0. #66 — local `origin/*` refs read two commits stale, and the recovery test built on
them would have said "the work did not land" about work that had landed.
Rule: At wave start, before any charter goes out, execute ONE trivial no-op through **every seat and
every external tool the wave's plan depends on** — including seats held in reserve, which is the whole
point — and record the result. A seat or tool with no execution record this run is treated as
UNAVAILABLE when planning, not as available-until-proven-broken. Presence, config, and exit codes are
not evidence; only output is.
Applies to: program, platform
Retire when: the control plane performs this sweep itself at wave start and refuses to open a wave with
an unexercised dependency on the critical path.

### WA-010 AMENDMENT, 2026-08-23 06:53Z — guard the WRITE, not the COMMIT; and stop sharing the checkout
**The rule had a hole, and it caught the Program itself twice within ten minutes.**

WA-010 says assert the branch before committing. I did exactly that — `[ "$BR" = "program/discovery-state" ]`
immediately before `git commit` — and the guard **worked**: it refused to commit program state when
`/srv/project` was found in **DETACHED HEAD at `a0e7a05`** (crew_alpha's PR #70 commit).

**But the same command block had already appended 22 lines to `.omni/ledger.md` via `cat >>`.** A guard in
front of `git commit` does nothing about file writes that precede it. The append landed on the wrong tree
and had to be copied out, discarded, and re-applied after switching back.

Minutes later, writing *this very amendment*, the guard fired again — the checkout had been detached a
second time by a concurrently-running crew. **That is the real finding: the shared checkout is not
usable by the Program while crews work in it, and no amount of asserting can fix a tree that moves
underneath you mid-block.**

Note what the check must test: `git rev-parse --abbrev-ref HEAD` returned the literal string **`HEAD`**.
Comparing against the expected branch catches that; testing "is the tree clean" does not.

**Amended rule, two parts:**
1. Assert the branch at the **TOP** of any block writing to `.omni/**` or `docs/**` and abort the whole
   block on mismatch — never just before `git commit`. Treat a literal `HEAD` as a mismatch.
2. **The Program works from its own worktree** (`/srv/project/.worktrees/program`, decision R-33), exactly
   as every crew does. It no longer writes through the shared `/srv/project` checkout at all.

**Prior instances:** #65 (checkout on a crew's branch; a program commit landed inside a live PR review),
06:53Z (detached HEAD; guard held, file write did not), 06:56Z (detached again mid-amendment).

*Applies to:* program, leads, engineers
*Retire when:* part 2 makes part 1 redundant — i.e. after two consecutive retros with no writer contending
on a shared checkout. **Do not retire part 1 before then; it is what caught this.**

## WA-013 A single-site remedy in shell under `set -u` requires a WHOLE-FILE SWEEP before it is called fixed

`architect` adjudicated #121 and named ONE defect site: `deploy/preflight.sh:33` expanding `$USER` under
`set -u` in a seat with no `USER`. The Program's charter repeated that single site. **`oncall` found four
executable `$USER` reads in the same script and fixed all of them**, exceeding its brief.

`architect` then tested the counterfactual **by construction**: it built an intermediate variant changing
**only** line 33, ran preflight with `USER` absent, and observed the bwrap probe report **OK** while the later
`loginctl show-user "$USER"` emitted `USER: unbound variable`.

**So the specified remedy would have turned RT-13/RT-14 GREEN while leaving the script non-hermetic.** The fix
for a test-passing-over-a-broken-thing defect would itself have been a test passing over a broken thing —
authored by the reviewer who diagnosed it and the Program who chartered it, and caught only because the
implementer ignored the narrow scope.

Precision, so this is not overstated later: the failing expansion is inside a **pipeline**, so bash continued
through subsequent checks rather than terminating the shell at that instant. That does not alter the defect or
the false-green risk.

**Rule:** when a defect's mechanism is a *language-level* hazard (`set -u` on an absent name, unquoted
expansion, `set -e` in a subshell), the remedy is scoped to **every occurrence in the file**, not to the
reported line. A reviewer naming one site is naming an instance, not the boundary. Grep the file for the
mechanism and state the count in the PR.

*Applies to:* engineers, oncall, platform, reviewers, program (when writing charters)
*Retire when:* two consecutive retros pass with no adjacent-instance finding on a single-site remedy.

## WA-014 A charter's SHAs are read in the SAME command that emits the charter, and diffs come from the PR's OWN base

Five SHA/diff errors across waves 6–7, **every one caught by a seat and none by the Program**:
a `61363e1e`/`61363e16` typo (caught by `release`); a stale base in an integration charter (caught by
`platform`); a charter base SHA that went stale mid-turn because the Program's own record-keeping pushes moved
the branch twice; and — the serious one — the Program describing PR #122's base span as **"four commits,
documentation only"** when that was the diff from *STATUS's last recorded tip*, not from the *PR's own base*.
The true span was nine commits, including merged PR #123, which edits `docs/CONTROL-PLANE.md` — **a file PR #122
also touches.** `platform` named the consequence exactly: *"that framing would have justified skipping the
conflict check this actually needed."*

The pattern is not typing. It is **quoting a SHA or a diff computed at a different time, or against a different
base, than the one the charter is about.**

**Rule, two parts:**
1. `git rev-parse origin/<branch>` and any PR head/base are read in the **same shell invocation** that produces
   the charter text. Never carried over from earlier in the turn.
2. A PR's diff is always computed from **that PR's own base**, never from the last SHA a status file happened to
   record. State the base explicitly beside the diff.

*Applies to:* program
*Retire when:* two consecutive waves close with no SHA or diff-framing correction raised by a seat.

## WA-015 A worker releases its slot AFTER its durable comment is posted, never before

`oncall` pushed PR #125, released slot 153, and only then posted its R-50 comment and returned. Harmless in
outcome, but for several minutes **the control plane advertised a free slot while that worker was still live** —
capacity that did not exist. That is the same class of untruth as a leaked slot, in the opposite direction, and
it is worse in one respect: a leak shrinks the budget visibly, an early release inflates it invisibly and can
let the Program over-dispatch against real vendor limits.

**Rule:** the release is the *last* action, after the PR/issue comment exists. If `slot release` fails with a
database-write error, do not retry (#116) — record the failure in the comment that is already posted.

**Related, and recorded here because it keeps being re-derived:** #116 is **seat-dependent**. `architect` failed
to release slots 152, 154 and 155; `oncall` (153) and `platform` (151, 156) released cleanly. Not
`release`-only as filed, not any-seat as the Program first rescoped it. **Expect to release `architect`'s slots
by hand, every time.**

*Applies to:* all seats that acquire slots; program (for the by-hand release)
*Retire when:* #116 is fixed and a full wave passes with no manual slot release.

## WA-016 The Program does not push to the delivery branch while an integration is in flight

`platform` integrated PR #122 while the Program was concurrently pushing wave records to
`program/discovery-state`. The charter cited `220f7b0`; `platform`'s own read was `ec6b8fe`; it merged onto
that, and when it re-read the tip immediately before pushing, the branch had moved **again** to `512e5dd`
(the cross-vendor approval record, then WA-013/014/015).

**Its first merge was therefore no longer a fast-forward, and pushing it would have carried `.omni/STATUS.md`,
`.omni/ledger.md` and `.omni/working-agreements.md` BACKWARDS — a silent revert of merged work, the #89/#98
failure mode exactly.** It discarded the merge, re-merged onto the real tip, and verified the three-file content
was byte-identical to what it had already tested before pushing anything.

**The integrator did everything right. The contention was created by the Program**, which treated the delivery
branch as its own notebook while asking someone else to merge into it. WA-014 fixed the Program quoting stale
SHAs; this is the same root cause seen from the other end — **the Program was itself the thing making them
stale.**

**Rule:**
1. From the moment an integration charter is dispatched until that integration lands, the Program makes **no
   pushes** to the delivery branch. Records are accumulated locally and pushed after.
2. Any integration charter states this explicitly, so the integrator knows the tip is stable for the duration.
3. An integrator that finds the tip moved anyway must **re-merge onto the real tip and re-verify**, never push
   a non-fast-forward merge. `platform` did this unprompted; it is now required.

*Applies to:* program, platform, release, any seat merging to a shared branch
*Retire when:* two consecutive waves close with no tip movement observed during an in-flight integration.

### WA-014 AMENDMENT, 2026-08-24 03:56Z — `gh api .base.sha` IS NOT THE MERGE BASE

Written the same wave as WA-014, by the rule's own failure shape.

The Program's #125 integration charter cited base `8999feee`, taken from `gh api repos/<r>/pulls/125 --jq
.base.sha`. **That field reports the base BRANCH TIP at query time, not the PR's merge base.** The PR's actual
fork point was `3952b98`. `8999feee` was an ancestor of the delivery tip but **not** an ancestor of the PR head.

`platform` caught it, computed the diff from the true merge base instead, got +117/−10 matching the PR, and
landed correctly. **Harmless this time only because the intervening span held no conflicting edit** — which is
luck, not design, and is exactly the reasoning WA-014 exists to forbid.

**Amendment:** a PR's base is `git merge-base <base-branch> <pr-head>`, computed locally. **Never
`.base.sha` from the API.** When quoting a base in a charter, say which one it is and how it was derived.

*Applies to:* program, platform, release, reviewers
*Retire when:* WA-014 retires.

## WA-017 — READ THE OWNER'S CHANNEL BEFORE YOU DISPATCH, NOT JUST THE BRANCH STATE
**Rule.** Before the first dispatch of any turn, a driver MUST read new comments on the pinned status
issue **#3** (and on any issue named in the current directive) with a timestamp later than the last
driver's handover. This is a step in the resume path, alongside `git show origin/program/discovery-state:.omni/STATUS.md` — **not a substitute for it, and not optional.**

**Why, from the incident that produced it (D-036).** At 07:11Z the `heartbeat-0700` driver dispatched
`platform` to build the #132 detector. The owner had posted a directive at **06:56:10Z** forbidding exactly
that — "do not let a large detector implementation displace the cutover." The driver had resumed correctly
from `.omni/STATUS.md` on the delivery branch and still got it wrong, **because STATUS is written by
drivers and structurally cannot contain an instruction issued after the last driver stopped writing.**
The dispatch was cancelled four minutes later; only R-50's durable posting meant nothing was lost.

**The trap this closes.** "Resume from files" reads as complete because the files are authoritative *for
what the org decided*. They are silent on *what the owner has since asked for*. A directive published into
the gap between two drivers is invisible to a resume path that reads only branch state. Every handover in
this org lands in exactly that gap.

**Enforcement, so this is not instance twelve of `declared-but-not-enforced`:** the driver states in its
wave report which owner comments it read and their timestamps, or states that there were none. An
unstated check is an unperformed check.

## WA-018 — no `args.model` overrides; and falsify a root cause before you publish it
*Adopted 2026-08-24 wave 9 (`heartbeat-1900`), from #141.*
1. **Dispatches SHOULD NOT pass `args.model`** on this deployment — omitting it runs the harness default,
   which is the documented safe answer. **Amended 19:31Z (D-048): the original causal claim here — that a
   model override forces `permission_mode: "default"` — was WRONG and is withdrawn.** `permission_mode: auto`
   is correctly declared in `program/agents/<seat>/config.yaml` and was on the parked session's own row; it
   intermittently fails to APPLY. This rule is hygiene, **not** a fix for #141, and must never be cited as one.

2. **A parked child is a dead child.** If `sys_session_get_info` shows `pending_elicitation_count > 0`, do not
   wait it out: cancel, release the slot, record it. Nothing resolves it unattended.
3. **An intermittent fault cannot be confirmed by a handful of dispatches.** If a failure strikes at random,
   absence of the symptom across two or three runs is not evidence of a cause — a clean-looking split is the
   expected outcome of noise. Before writing a cause into a binding rule, ask what sample size the claim
   needs, and check whether you are holding a datum that *undercuts* it and reading it as support (in #141 a
   seat was executing unattended in a concurrent session the entire time).
4. **Falsify before you file.** A root cause published in an issue gets acted on by other agents and by the
   owner. If a 2-minute controlled test can falsify it, run the test FIRST. In #141 the wrong cause (the
   cutover) was published and dispatched to `oncall` before a 75-second experiment disproved it. Where a
   hypothesis is already published and then falsified, **post the correction before doing anything else** —
   a wrong P0 on the board is worse than no P0.

## WA-019 — external publication is denied until the owner names the repository and action

*Adopted 2026-08-28 from owner directive #229 after an upstream pull request was opened without
exact authorization for that public-repository action.*

**Rule.** Read-only inspection of an external or public repository is allowed. Any mutation is denied by
default: no issue, comment, edit, close, branch, commit, push, pull request, review, release, merge, or
upload. Approval must name both the target repository and the action for the current work. Broad prior
permission, inferred usefulness, ownership of the GitHub account, or a need for an upstream fix does not
count. If approval is absent, prepare the patch locally or record it in the current private delivery
repository. Do not modify or close an existing external artifact without separate approval.

**Why.** Publishing upstream changes another project's durable history and communicates on the owner's
behalf. That is an external side effect, even when the code is correct and the account has permission.

**Enforcement.** Before an external write, the acting agent must cite the owner's exact approval and the
named repository/action in its durable task record. No citation means no write. This is currently a
human-authorization gate, not a claim that the policy layer technically blocks every GitHub command.

*Applies to:* every agent and every repository outside the current owner-approved private delivery repo

*Retire when:* never silently; only an explicit owner directive may supersede it, and the replacement must
preserve an auditable authorization boundary.
