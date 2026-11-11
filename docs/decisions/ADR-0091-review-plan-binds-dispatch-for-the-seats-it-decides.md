# ADR-0091: The review plan binds dispatch for the three seats it actually decides

Date: 2026-09-07 · Status: accepted
Deciders: owner (four decisions, recorded below), platform
Issue: #720, child of epic #716 (change 2 of six)
Supersedes: ADR-0042, **only** on the question of whether `suggested_domains` narrows
dispatch by convention or by machinery. ADR-0042's boundary is otherwise kept exactly:
dispatch may narrow, eligibility may not. ADR-0036's `authority: "shadow_only"` stamp on the
report is unchanged — the report authorises nothing; a permit does.
Unaffected: ADR-0040's frozen required-domain contract for merge eligibility, in full.

## Context

ADR-0036 shipped a planner that reads a diff and decides which review domains it needs, and
deliberately marked the answer diagnostic. ADR-0042 then allowed that answer to narrow which
domains a crew dispatches — and wrote that permission into `crew_alpha`/`crew_beta`'s
`config.yaml` and `fanout/SKILL.md`, which are prose. Nothing in `bin/`, `deploy/` or
`.github/` has ever called `review plan`.

The first external-repository run (#730/#731) is what made that matter. Twelve hours, ~77
seats, 20 reviewer rounds, and the fanout was blanket throughout: every domain dispatched for
every PR, because the instruction to narrow was a paragraph an agent could read past. #716
names the shape — *presence is not function* — and this is an instance of it in the routing
layer.

## The four decisions

Taken by the owner on 2026-09-07, after the constraints in the next section were surfaced:

1. **Dispatch only, mechanized.** Not eligibility. ADR-0042's split stands; what changes is
   that the dispatch half becomes machinery instead of prose.
2. **The invoker is the `nightshift-ctl` dispatch path.** Not CI — a CI check can only run
   once a PR exists, which is the lateness #716's change 3 exists to fix.
3. **A planner that cannot answer falls back to full fanout, and says so.** Coverage is never
   reduced by a broken gate. But a control that degrades to "allow everything" silently is the
   defect class this epic is about, so each degradation is one countable row.
4. **Binding on this repository, shadow on a customer's.**

## Three constraints, each of which removed a larger design

**A. Gating `code_review` or `pm` is vacuous.** `_classify` seeds `domains = {"pm",
"code_review"}` before it examines anything. A gate on either can never refuse. An ALLOW with
no reachable DENY is not a gate — it is a check that reports success, which is worse than no
check because it also reports coverage.

**B. The seats worth gating hold no slot.** `security`, `ux` and `staff` are `EXEMPT_AGENTS`;
`slot acquire` — the one dispatch chokepoint that exists — never sees them. What it does see
is the `reviewer_*` seats, carrying exactly the domain constraint A rules out. So the obvious
place to put this gate is the one place it could not work.

**C. The fence does see them.** All three are in `PROGRAM_DIRECT_CHILDREN`, and ADR-0044's
root policy already denies any dispatch to them without a permit for that exact
`(tool, agent, title, args)` digest. That is a mandatory chokepoint, already enforced, already
reviewed. The gap was on the mint side: `authority permit-dispatch` took an opaque `--digest`
and could not know which seat it was authorising.

## Decision

`authority permit-dispatch` learns the call it authorises, and refuses to mint for a gated
review seat whose domain the plan for that exact head does not name.

- The mint accepts `--tool/--agent/--title/--args-json` and computes the digest through the
  same `canonical_dispatch_digest` the root policy calls. One definition of "this exact call",
  not two that drift.
- The gated seat set is `security`, `ux`, `staff` — constraints A and B, exactly. `qa` and the
  `reviewer_*` seats stay unconditional, as ADR-0042's own text requires.
- For a gated seat the mint calls `review plan` for the task's current head **before** opening
  its transaction, and refuses when the plan omits that domain.
- Permits carry `review_gate`: the domain the mint verified, or NULL when minted blind through
  `--digest`. The root policy requires a matching non-NULL gate when the dispatched agent is a
  gated seat.

That last point is the load-bearing half. Without it the mint gate is advisory: Program could
mint the same digest through the blind path and dispatch anyway. With it, the blind path
remains available for every other seat — the runbook, the fence cutover and Program's existing
prompt keep working — and simply cannot authorise a review seat.

## The three seats do more than review, so the exception is declared

`ux` is dispatched for design input **before** a crew is chartered (`program/config.yaml`
step 3) and `staff` writes a blind architecture draft — both at a point where no PR, no head
and often no task exist. There is no diff to plan against, and refusing those dispatches would
kill the earliest and cheapest design work the org does, which is the opposite of what #716's
change 3 asks for.

`--not-a-review REASON` states the exception. It cannot be taken by omitting the review facts
— that still fails closed — it names a reason, and every use is written to
`review_gate_degradations` alongside the genuine degradations. The permit stores
`review_gate = 'exempt'`, which the fence accepts.

That last point is a real concession and is recorded as one: the fence cannot distinguish a
design-input dispatch from a review dispatch, because both are `sys_session_send` to the same
seat. The mint-time declaration is the only signal that exists. What makes it tolerable is
that it is explicit, attributable to a token holder, and counted — if the exception becomes
the common path, the count says so, which is precisely what a blanket fanout never did.

## Why `cross_crew` is not gated

It is dispatched under the `crew_alpha`/`crew_beta` agent names, which are also the names a
build dispatch uses. The permit boundary cannot distinguish a cross-crew review from a build,
so gating it would mean refusing build dispatches on a review plan. It needs a distinguishable
signal first, and is filed as #721 rather than approximated here.

## Why the two vocabularies are not reconciled

The planner says `staff`, `cross_crew`, `pm`; the frozen contract says `staff_adr`,
`cross_crew_review`, `pm_acceptance`. A mapping table between them would be the first step of
carrying `suggested_domains` into eligibility by accident, which ADR-0042 §A rejected outright.
The gate speaks only the planner's vocabulary, and a test asserts it does not learn the frozen
one.

## Options considered

### A. Gate at `slot acquire` (rejected)
The natural-looking chokepoint. It reaches only the `reviewer_*` seats, whose domain is
unconditional — constraint A. It would have shipped a gate with a green test suite and no
reachable refusal.

### B. Gate only at the mint (rejected)
Half the work, and defeated by one flag. `--digest` would have remained a complete bypass.

### C. Gate at mint, bind at consumption (chosen)
The mint decides; the fence refuses to accept a permit that never decided. Both halves are
required for either to mean anything.

### D. Make `suggested_domains` authoritative for eligibility too (rejected)
ADR-0042 already rejected this as option A, and decision 1 declines it again. A ruleset
regression would then narrow what merge REQUIRES, not merely what gets asked proactively.

## What an adversarial review of this change found, and what was done

The change was reviewed with instructions to refute it. Four findings changed the code:

- **The gate was opt-out.** Every planner failure returned the domain, so a caller supplying a
  junk `--head-sha` was told the planner was degraded and got a permit indistinguishable from a
  verified one. Fixed two ways: the gate now validates the caller's own facts itself
  (`names_a_real_commit`) and refuses a head that is not a commit, because the planner reports
  a nonexistent object and a broken git identically as `git_unreadable`; and a fallback mint
  now stores `review_gate = 'fallback'`, never the domain, so the two are distinguishable in
  the table whose job is telling them apart.
- **`--not-a-review` could be appended to a call that had just been refused**, overriding facts
  it contradicted. It is now refused as a contradiction: a dispatch either reviews a specific
  head or it does not.
- **`--digest` silently ignored every gate argument.** It now names each supplied argument and
  refuses, rather than handing back an ungated permit and saying nothing.
- **Two ADR-0044 §7b sequence tests asserted that a blind permit dispatches `staff`.** That is
  exactly the behaviour this ADR changes, so they were retargeted at `platform` — a seat the
  review gate does not claim — keeping them about the fence sequence, with the gated behaviour
  covered by its own suite instead of one rule quietly masking the other.

## The conformance test ADR-0036 left behind

`test_no_authorizing_subsystem_imports_shadow_planner` asserts that nothing under `.github/`,
`deploy/` or `program/` so much as names the planner, with ADR-0042's two crew configs
exempted. This change adds two names to that list — the fence's denial message and Program's
instruction for how to mint — and narrows what being on the list buys: an exempt file may NAME
the planner and still may not IMPORT it, now checked rather than assumed. An allowlist whose
entries are otherwise unconstrained is a hole with a list of names on it.

The mint itself (`bin/nightshift-ctl`) does import the planner, and that is the supersession:
ADR-0036 said the shadow report authorises nothing, and for three seats it now authorises
exactly one thing. `bin/` was never in that test's scan, so the rule was narrower than its name
suggested — worth knowing before relying on it elsewhere.

## Two limitations this change does not remove

Recorded because they bound what the gate is worth, and a reader who discovers them later
should find them already written down rather than think they were missed.

**The gate is off while the controller is being edited.** The planner refuses to run unless the
executing bundle's `bin/nightshift-ctl`, `bin/nightshift_review_plan.py` and
`program/review-risk.json` match their committed blobs. Any uncommitted edit to those three
produces `bundle_identity_unproved`, which is a degradation, which mints. So during exactly the
work that touches the control plane, dispatch falls back to full fanout. That is the correct
behaviour under decision 3 — an unproved bundle must not be trusted to make routing decisions —
but it means the gate's coverage is not uniform in time, and the degradation count is what makes
that visible rather than assumed.

**A permit is one-shot; a session is not.** The fence allows a `sys_session_send` that carries no
`agent` — a continuation of an already-open session (ADR-0044's own scope). One gated mint
therefore buys any number of later turns into that session with no plan consulted again. Under
ADR-0044 that was a narrow race over an already-permitted dispatch; under this ADR it is a
durable amplification of a single exemption. Filed as #722 rather than fixed here: it is ADR-0044's
boundary, not this one's, and narrowing it touches every seat rather than three.

## Acceptance boundary

Every criterion is proved in both directions, because a gate is only shown to work by a
refusal that can be made to become a permit and back:

- `security` at a head whose plan names it mints; `ux` at the **same head** is refused.
- A blind permit for `security` is denied by the fence; the same permit minted gated is
  allowed; a permit gated for another domain does not transfer.
- A blind permit for `release` — not a gated seat — dispatches exactly as before.
- The degradation count is zero after a healthy binding mint and one after a planner that
  cannot answer, so the count distinguishes the two rather than merely existing.
- A refused mint writes nothing at all: no permit, and no degradation row. Refusals are the
  gate working, and must not inflate the count that measures it failing.
- The degradation table cannot be appended to without the authority token.
- `bin/nightshift_review_eligibility.py` is unedited and still does not import this gate.

## Consequences

The planner stops being advice. For three seats, a dispatch that the ruleset does not call for
cannot be authorised, and the failure is a refusal at the mint with a reason naming the domain
— not a paragraph an agent read past.

It also creates a new way to be wrong: a ruleset that under-triggers now silently prevents a
review that a human would have wanted. That is the trade decision 1 accepts and bounds —
eligibility still demands the frozen contract's full required set for merge, so a domain the
plan failed to trigger blocks the PR at the gate rather than shipping unreviewed.

## One-way door and reversal trigger

Not a one-way door. Reverting the `review_gate` clause in `consume_program_action_permit`
restores the pre-#720 fence exactly, and every existing blind permit keeps working throughout —
which is what makes the rollback a one-line change rather than a migration.

Reverse if the degradation count shows the planner is unusable more often than it is usable,
or if a refusal is ever traced to a ruleset gap rather than a genuinely unneeded review.
