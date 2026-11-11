# ADR-0047: Program's operating rule for a `budget_expired` scheduling verdict

Date: 2026-08-30 · Status: accepted · Deciders: crew_alpha lead · Proposers: none (see §Process — cross-vendor debate blocked)
Conversation: crew_alpha session (title `218-build`) · Spec: `.omni/backlog.md` (crew_alpha section, branch `crew_alpha/218-dor`), full DoR reasoning captured in the `pm`/`218-dor` transcript
Amends: none (additive prose in `program/config.yaml`'s `prompt:` block; touches no schema, no `bin/nightshift-ctl` code path)

## Process — cross-vendor debate could not run (WA-5)

Per the mandatory design-debate process, a `qa` planning slot was acquired
this session (`nightshift-ctl slot acquire --holder crew_alpha/qa --crew
crew_alpha --task 218-debate --agent qa --title 218-debate-plan --purpose
explore`, granted `slot_id=20`, `generation=1`) and dispatched for a test
plan with `task_id`/`slot_id`/`generation` on the `sys_session_send` call, as
the contract requires. It was rejected, reproduced live this session:

```
Input validation error: Additional properties are not allowed
('generation', 'slot_id', 'task_id' were unexpected)
```

`sys_session_send`'s own JSON schema declares `args` with
`additionalProperties: false` and does not list `slot_id`/`task_id`/
`generation` at all, so no argument shape can satisfy `worker_semaphore`
(`deploy/policies/omnipods_policies.py`), which requires them for any
`COUNTED_AGENTS` target (`eng_claude`, `eng_codex`, `qa`, `reviewer_claude`,
`reviewer_codex`). This is issue #236 (P0, open at time of writing), the same
defect ADR-0043/ADR-0044/ADR-0045/ADR-0046 hit on issues #214/#174/#215/#217.
The unused slot was released (`slot release --slot 20`) rather than left to
expire.

`pm` dispatch is unaffected (`pm` is in `EXEMPT_AGENTS`, not classified
through `worker_semaphore`), so the Definition-of-Ready pass that produced
this ticket's frozen 7 acceptance criteria proceeded normally; only the
two-vendor **build** debate is blocked. Since `worker_semaphore` gates the
whole `COUNTED_AGENTS` set on the same missing schema fields, one live
reproduction against `qa` is treated as sufficient evidence that `eng_claude`/
`eng_codex` dispatch is equally sterile — the same precedent ADR-0045/ADR-0046
followed rather than re-triggering the identical failure per vendor.

Per **Working Agreement 5** (`.omni/working-agreements.md`, item 5 — *"a crew
lead whose worker dispatch is sterile builds under a slot it holds, and says
so in the PR body... independence is preserved by cross-crew review, which
becomes mandatory"*), this ADR is **single-author**, and the implementation
and its tests are authored directly by the crew_alpha lead rather than by
`eng_claude`/`eng_codex`/`qa`. That is a real gap, not a formality — flagged
here, in the PR body, and in the report to the program. Cross-crew review
(crew_beta, round-robin) is the mandatory independent check for the
resulting diff, standing in for both in-crew review and QA this round. Not a
standing authorization for future tasks — reconfirmed live, for this task
specifically, per crew_alpha's own rule against inferring WA-5 from
precedent alone.

## Context

Issue #218 asks for a Program-level operating rule: once #216's enforcement
(`reason_code=budget_expired`) and #217's escape hatch (`task extend`, one
bounded extension) exist in the control plane, Program itself still has no
instruction for what to *do* when it observes `budget_expired` — without one,
an expired task can leave a run stuck, or Program can keep re-attempting a
slot/claim acquire the control plane will keep denying. `pm`'s DoR pass
(read fresh this session, not assumed) confirmed `program/config.yaml`
currently has **zero** mentions of `budget_expired`, `reason_code`, `task
extend`, or `EXTEND_ONCE`/`SPLIT`/`BLOCK`/`FAIL` — this is genuinely net-new
prose, not a reword of anything already there — and identified one real gap
before the ticket could be called READY: AC6 (`FAIL` records why the current
approach cannot satisfy the frozen contract) and AC7 (Program reports the
chosen action "in the normal human-first status format") are in tension,
because ADR-0027's `STATUSES` tuple (`docs/HUMAN-COMMUNICATION.md`, enforced
by `tests/test_human_first_communication.py`) is closed — `On track`,
`Blocked`, `Ready for review`, `Ready to merge`, `Merged`, `Superseded`,
`Done`, `Closed without completion` — and none of those words is an obvious
fit for "this approach failed."

`pm` resolved this during DoR follow-up (documented in its own verdict,
verified independently this session against `docs/HUMAN-COMMUNICATION.md`
and `docs/decisions/ADR-0028-outcome-kanban-projection.md` directly, not
taken on citation): `FAIL` is not one fixed status word. Its disposition is
exactly one of the two negative/terminal cases already in the frozen
vocabulary, chosen by what Program actually decided to do next — drop the
task, or replace it with a new approach under a new ADR — with `SPLIT`
keeping sole ownership of "cut scope and keep going" so the four top-level
actions stay mutually exclusive (the issue's own requirement: "Program
chooses exactly one"). This ADR treats that resolution as binding, not
reopened, consistent with the precedent ADR-0045 set for a materially
identical DoR-follow-up resolution on issue #215.

## Options considered

No competing vendor proposals exist (see §Process). The options below are
the lead's own analysis, covering the seams the DoR verdict left open.

### A — One new prose bullet in `program/config.yaml`'s `UNATTENDED MODE` section, immediately after "RE-DISPATCH IS CLAIM-FIRST" (chosen)

`RE-DISPATCH IS CLAIM-FIRST...` already covers the adjacent-but-distinct case
of a stalled `building` task with no result (assume the worker died, reclaim,
resume). `budget_expired` is different: the task may be perfectly alive, it
has simply run past its allotted time, and the control plane's own denial is
the considered signal, not a crash to recover from. Placing the new bullet
immediately after keeps the two "what do I do about a stuck-looking task"
rules adjacent and lets the new one explicitly say how it differs, rather
than scattering budget logic elsewhere in a 600-line prompt.

### B — A new top-level `## BUDGET DECISIONS` section (rejected)

Rejected as disproportionate for one bullet's worth of instruction — the
existing section structure (`## UNATTENDED MODE`, `## The interrupt lane`,
etc.) reflects genuinely distinct operating modes; `budget_expired` handling
is a variation on "what Program does when a nightshift-ctl call comes back
exit 3," which `UNATTENDED MODE` already owns for every other case (claim
denial, vendor cooldown, capacity denial). A new top-level section would
duplicate that framing instead of extending it.

### C — Encode the four actions as a `nightshift-ctl` subcommand or state machine (rejected, and out of scope)

The issue's own "Not included" list rules this out directly ("changing
control-plane budget math," "automatically choosing split boundaries with a
new service," "changing review rules"). Program's decision among
`EXTEND_ONCE`/`SPLIT`/`BLOCK`/`FAIL` is a judgment call informed by the
concrete situation (what's blocking it, whether the work is genuinely too
big, whether the contract is still satisfiable) — exactly the kind of call
`UNATTENDED MODE`'s existing "DECIDE, DON'T QUEUE" bullet already assigns to
Program's own reasoning, not to a deterministic function. Automating the
choice would also remove the human-readable reason AC5/AC6 require.

### D — `FAIL`'s status mapping: reuse `Blocked`, invent a new status, or split across the two existing negative/terminal statuses by disposition (chosen: split by disposition)

Covered in full under §Context. Reusing `Blocked` was rejected because it
collides with `BLOCK`'s own, semantically distinct lane (external/dependency
reason vs. a judgment that the approach itself is wrong) and would make the
two actions indistinguishable in a status report. Inventing a new status
word was rejected because it requires a new ADR-0027 contract version — a
disproportionate, cross-cutting change for one ticket's FAIL branch, and
none of #218's "Expected code seams" name touching that contract. Splitting
by disposition (`Closed without completion` when Program actually closes the
issue `NOT_PLANNED`; `Superseded` when a new task/ADR replaces this one)
uses only vocabulary and mechanisms that already exist and are already
frozen: `docs/decisions/ADR-0028-outcome-kanban-projection.md` already ties
`Closed without completion` exactly to a `NOT_PLANNED` closure, and
`superseded` is already an ordinary terminal `tasks.state` value
(`bin/nightshift-ctl`'s `TERMINAL_TASK_STATES`/`READINESS_TERMINAL_STATES`),
not a new concept introduced by this ADR.

## Decision

Option A + Option D. Add one new bullet to `program/config.yaml`'s
`UNATTENDED MODE` section, `BUDGET_EXPIRED IS A SCHEDULING VERDICT, NOT A
CRASH`, placed immediately after `RE-DISPATCH IS CLAIM-FIRST...`. The bullet:

1. States that `reason_code=budget_expired` (from `task show`, or a denied
   `claim acquire`/`slot acquire`) is the control plane's own considered
   answer, never infrastructure failure or a reason to retry the same call
   (AC1, AC2).
2. On the first expiry (`extension_seconds` still null), requires Program to
   choose exactly one of `EXTEND_ONCE` / `SPLIT` / `BLOCK` / `FAIL`:
   - `EXTEND_ONCE`, gated on `extension_seconds` reading null, runs
     `nightshift-ctl task extend --id <id> --seconds <n> --reason "<why>"`
     (#217's mechanism; `--reason` already required at the argparse level) —
     a denial (`already_extended`/`not_budget_expired`) is treated as proof
     this branch was mischosen, recorded in `.omni/decisions.md`, never
     retried (AC3).
   - `SPLIT` dispatches the owning crew's `pm` to create smaller, bounded
     (S/M) work items against the same checkpoint `base_sha`, explicitly
     "rather than continuing the same oversized task unchanged" (AC4).
   - `BLOCK` records the concrete external/dependency reason in
     `.omni/blocked.md` and the ledger, and sets `state:blocked` on the
     issue (AC5).
   - `FAIL` records in `.omni/decisions.md` why the current approach cannot
     satisfy the frozen contract (AC6), then applies exactly one
     disposition: drop it (close the GitHub issue `NOT_PLANNED`, report
     `Closed without completion`) or re-plan under a new ADR (mark this
     task `state=superseded`, name the successor, report `Superseded`) —
     never "cut scope," which stays `SPLIT`'s job.
3. After the one extension, restricts the same task to exactly one of
   `SPLIT` / `BLOCK` / `FAIL` — `EXTEND_ONCE` is explicitly named as
   unavailable a second time, backed by the control plane's own
   `already_extended` refusal as a second layer, not Program's prose alone
   (AC3 second half, AC2).
4. Requires the chosen action and its one-line reason to be the first thing
   in that task's next status report, using the existing human-first
   six-field first screen (AC7) — never a bare action token.

A focused regression test, `tests/test_budget_expired_operating_rule.py`,
follows the established pattern (`tests/test_review_contract.py`,
`tests/test_human_first_communication.py`) of a stdlib text-conformance gate
over `program/config.yaml`'s parsed `prompt:` string — Program's decision
procedure is prose the agent reads, not code with a call graph, so a
behavioral test cannot exist for it; a text gate is the correct and only
adequate seam. It isolates the new bullet (bounded by the next top-level `-
` item so assertions cannot pass by matching unrelated prose elsewhere),
covers AC1–AC7 individually, and cross-checks its own local copy of the
`STATUSES` tuple against `docs/HUMAN-COMMUNICATION.md` so the two cannot
silently drift apart.

## Consequences

**Easier:** Program has one deterministic, three-times-referenced (once per
possible discovery point: `task show`, a denied `claim acquire`, a denied
`slot acquire`) rule to fall back on instead of re-deriving a policy from
first principles on the day a task actually runs long. `EXTEND_ONCE`/`SPLIT`/
`BLOCK` all keep the outcome moving; only `FAIL` is genuinely terminal for
the current approach, and even then the issue itself (as opposed to the
current task/PR) is not assumed closed unless Program actively decides that.

**Harder:** nothing new operationally — this is pure prose, no new
`nightshift-ctl` surface, no schema change, no new test infrastructure
beyond one file in an already-established pattern.

**Foreclosed:** nothing new by this increment specifically. The issue's own
out-of-scope list (control-plane budget math, worker cancellation,
auto-choosing split boundaries, PRD decomposition, review-rule changes) is
untouched — this ADR adds no code path that could reach any of them.

## One-way door?

No. This is prose in an agent's own operating instructions; a future ADR can
revise or supersede this bullet with no migration, no data shape, and no
public contract at stake.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — this changes no
infrastructure, cost, or throughput characteristic.

## Addendum, 2026-08-30 — security MERGE_BLOCKER on PR #355 (`FAIL` → drop
must not bypass the TRACKER close gate)

**Finding.** `security`'s review of PR #355 found that the `FAIL` → drop
disposition, as originally written ("drop it — close the GitHub issue with
reason `NOT_PLANNED` and report status `Closed without completion`"), closed
the issue unconditionally. `program/config.yaml` already states, earlier in
the same prompt (the TRACKER step), the org's one issue-closing rule: *"YOU
close an issue, and only when `nightshift-ctl issue status --issue <n>` says
closable AND that crew's pm has signed acceptance."* The original `FAIL` →
drop bullet did not carry that check forward — a structurally real gap, not
a style nit: a task that hit `FAIL` never reaches `integrated`, so it could
close an issue while sibling work tied to the same issue was still open, with
no independent (pm-equivalent) sign-off at all.

**Why `closable` cannot simply be quoted as-is for this branch, and why that
matters.** `bin/nightshift-ctl`'s `_issue_status` computes `active = [s for s
in states.values() if s != "dropped"]` then `closable = bool(active) and
all(s == "integrated" for s in active)`. Marking the failed task itself
`state=dropped` (the natural terminal state for this disposition) excludes
it from `active` — correctly, since the point of `FAIL` → drop is that this
task did NOT integrate and never will. But `bool(active)` also requires at
least one REMAINING active task, so `closable` reads true for this path only
when some OTHER task filed against the same issue already reached
`integrated` — i.e. the issue is genuinely satisfied by different work, not
by this one. A lone, unaccompanied task that fails and drops will read
`closable: false` forever; nothing will ever make that boolean flip, because
there is nothing else left to integrate.

**Resolution (Option (a) from the finding, adapted to that structural fact).**
The bullet now: (1) marks the task `state=dropped` with the recorded reason
before touching the issue at all; (2) closes the GitHub issue `NOT_PLANNED`
ONLY when the SAME `nightshift-ctl issue status --issue <n>` call the TRACKER
step already requires reports `closable`, AND that crew's `pm` has signed off
on the drop specifically (concurrence that abandoning is correct, not
acceptance of criteria met — there is nothing to accept when nothing was
completed); (3) if `closable` reads false — the common case for a
stand-alone dropped task, and also the case where other work on the same
issue is still genuinely in flight — Program does NOT close the GitHub
issue. It reports status `Blocked` (an existing, already-frozen ADR-0027
status, not a new word), records the reason plus the closability gap in
`.omni/blocked.md`, and leaves the actual close to a later human-equivalent
pm/product call, exactly the discipline the TRACKER step already applies to
every other issue closure. This deliberately means a stand-alone `FAIL` →
drop task's issue does not auto-close by itself in the ordinary case — that
is the correct, conservative behavior: `NOT_PLANNED` closure is a judgment
call about abandoning outcome-shaped work entirely, and per Working Agreement
from the owner (`.omni/working-agreements.md` item 3, "owner input goes
through the normal issue → Definition of Ready → review pipeline"), that
judgment does not get a FAIL-specific shortcut around the same gate every
other closure already answers to.

**Evidence, control and counterfactual (WA-009).** Five new tests in
`FailDropRoutesThroughTheExistingTrackerCloseGate`
(`tests/test_budget_expired_operating_rule.py`) were run against the
pre-fix bullet text (`git show HEAD:program/config.yaml` at the PR's prior
head, `691dc2805a7c5c2f9d92ded2d96234e67db51328`) and against the fixed
text: all 5 FAIL pre-fix (the gate language does not exist yet) and all 5
PASS post-fix, for the reason the assertion names — not a harness artifact.
The pre-existing 14 tests are unaffected (still pass unchanged); full new
count 19/19 OK.

**Scope.** Documentation- and prose-only, same as the rest of this ADR — no
`bin/nightshift-ctl` change. `docs/reviews/218-budget-expired-operating-rule-CONTRACT.md`
is amended to version 2 to record this finding and its resolution.

## Reversal trigger

If a future ticket needs Program to choose among more than these four
actions, or needs a `FAIL` disposition that doesn't fit either
`Closed without completion` or `Superseded`, that is a new, explicit
decision under a new ADR that revises this rule — never a silent edit to
this bullet. If issue #236 clears, any follow-up change to Program's
operating instructions should get a real two-vendor design debate before
being treated as satisfying the design-debate precondition; this ADR's
single-author status is a property of #236 being open on 2026-08-30, not a
new norm for this crew (same WA-5 reversal trigger as ADR-0043/0044/0045/0046).
