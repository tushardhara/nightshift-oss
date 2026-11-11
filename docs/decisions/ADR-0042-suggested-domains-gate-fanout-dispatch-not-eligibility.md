# ADR-0042: `suggested_domains` may narrow which review domains a crew dispatches, but never what review eligibility requires for merge

Date: 2026-08-28 · Status: accepted
Deciders: program (routes to staff for the mandatory consistency review before crew_alpha builds against this)
Proposers: crew_alpha lead (single-author, see §Context)
Conversation: crew_alpha lead session, run `fixture-review-231` · Spec: issue #233 (#228D) DoR judgment,
`.omni/reports/2026-08-28-1520Z-233-dor.md` and https://github.com/tushardhara/nightshift/issues/233#issuecomment-5454613003
Supersedes: ADR-0036 and ADR-0040, **only** on the narrow question named in Decision below. Every
other claim in both (exact-SHA classification, canonical output shape, ADR-0040's frozen
required-domain contract for review ELIGIBILITY) stands as written and is unaffected.

## Context

Issue #233 (#228D, "Activate risk-based review routing and thin Release") is next in the owner's
sequencing contract (#228A → #231 → #232 → #233 → #213…#223); its three dependencies (#228A, #231,
#232) are merged and `main` is green. `crew_alpha`'s `pm` ran Definition-of-Ready against it
(artifact linked above) and returned **NOT READY**, naming two blocking prerequisites: an
ownership/lease question for the `program/**` paths #233 needs edited (P-A, tracked separately,
routed to and resolved by program in `.omni/decisions.md`, PR #261), and this ADR (P-B) —
resolving a direct conflict between #233's stated intent and the currently accepted authority
boundary for the shadow risk planner's output.

`nightshift-ctl review plan` (ADR-0036, accepted) computes `suggested_domains` and marks it
`authority: "shadow_only"`: "The shadow plan must not cause a dispatch, skipped gate... Each
deferred capability requires separately ready tracked work and, where it changes authority, a
successor ADR. No later slice may treat the shadow report as an authorization receipt without that
explicit decision." ADR-0040 (accepted, review eligibility) goes further for a specific consumer:
required review domains for MERGE eligibility come only from the frozen per-work-item contract,
"never #228A's `suggested_domains`," enforced today by
`tests/test_review_receipts.py:457-463` (`test_module_does_not_import_the_shadow_planner`), and
ADR-0040 explicitly anticipated #233 and declined to authorize it ("The exact wire shape for
#233's eventual consumption is explicitly not frozen here").

#233's must-haves are narrower than "make `suggested_domains` authoritative" in the abstract. Read
literally: "dispatch only mechanically suggested domains" while "keep one different-vendor code
review and every mechanically triggered Security, UX, QA, Staff, and cross-crew domain." Today,
`crew_alpha`/`crew_beta`'s fanout and Phase-3-verify instructions dispatch every review domain for
every PR unconditionally, out of caution, regardless of whether the diff mechanically warrants it.
#233 wants to prune that blanket dispatch down to exactly what the ruleset
(`program/review-risk.json`, as applied by `bin/nightshift_review_plan.py`'s `_classify`) actually
triggers for the given diff — never to add a domain the ruleset would not otherwise trigger, and
never to drop code review or QA, which stay unconditional per the issue text itself.

**Single-author flag.** `design-debate`'s normal procedure — two independent, different-vendor
engineer proposals reconciled by the lead — could not run: issue #236 (P0, confirmed OPEN at
`origin/main@5fe047d` and again at the SHA this ADR is cut from) means every `sys_session_send`
dispatch to a `COUNTED_AGENTS` seat (`eng_claude`, `eng_codex`, `qa`, `reviewer_claude`,
`reviewer_codex`) is rejected by `worker_semaphore`'s schema mismatch. `pm` is exempt and already
did the DoR pass; drafting an ADR is Markdown, squarely inside what a crew lead may author
directly (this repo's own contract for the lead role). This is the same deviation ADR-0040 took
for the same reason, flagged the same way. **Do not treat `proposed` as `accepted`** — per the
routing decision in `.omni/decisions.md` (PR #261), program routes this to `staff` for the
mandatory consistency review before crew_alpha builds against it, and `staff`'s pass substitutes
for the missing cross-vendor engineer debate on this specific question.

## Options considered

### A — Full supersession: `suggested_domains` gates every review-domain question, including merge eligibility (rejected)
Would let `suggested_domains` decide not just who gets dispatched but whether a PR is eligible to
merge, replacing ADR-0040's frozen required-domain contract outright. Rejected: removes the safety
net ADR-0036 deliberately built in. A staleness bug, a ruleset regression, or a malformed shadow
report would silently narrow what merge requires, not just what gets proactively dispatched —
exactly the "authorization receipt" role ADR-0036 forbids. Also directly re-opens ADR-0040's
frozen contract, which #233's own out-of-scope list ("no new... state machine," "#213–#223 out")
gives no mandate to touch.

### B — Narrow supersession, dispatch only, eligibility untouched (chosen)
Split the two questions ADR-0036/ADR-0040 conflated risk of conflating:
- **Dispatch** ("who gets asked to review this diff, mechanically") — today: everyone, always.
  Under this ADR: exactly the domains `review plan`'s current-head-SHA `suggested_domains` names,
  computed fresh for that head, is the crew's single source of truth for which domains
  `program/agents/crew_alpha/config.yaml` §Phase 3 / `crew_alpha/skills/fanout/SKILL.md` §4 (and
  the mirrored `crew_beta` files) actually dispatch beyond the two always-mandatory checks (one
  different-vendor code review, QA).
- **Eligibility** ("is this PR mergeable") — entirely untouched. `bin/nightshift_review_eligibility.py`'s
  frozen required-domain contract (ADR-0040) remains sole authority for what `review eligibility`
  requires a receipt for, and `program/agents/release/config.yaml`'s merge preconditions 2/4/5
  (different-vendor review, security, cross-crew) are edited only to consume that digest directly
  (#233 AC-2/AC-5) instead of re-deriving it — never to consume `suggested_domains` instead of the
  frozen contract.

Because dispatch only ever narrows (never widens) what full fanout already covers, and eligibility
keeps demanding the frozen contract's full required set regardless of what was dispatched
voluntarily, a stale/missing/malformed `suggested_domains` cannot silently under-review a PR that
reaches merge: eligibility still blocks on any required domain missing a receipt. It can only make
the crew ask fewer OPTIONAL questions upfront — recoverable by fail-closed (below), not merge-unsafe
even if fail-closed were skipped, because eligibility is the actual gate, not dispatch.

### C — Defer #233 entirely, keep `suggested_domains` shadow-only (rejected as the default; kept as fallback)
Simplest and safest, but #233's own dependencies are met and the owner's sequencing contract has it
next. Not chosen as the primary decision, but this ADR's fail-closed default (below) means the
system behaves exactly like Option C — full fanout, no gating — whenever the mechanical signal
can't be trusted, so Option C is never fully foreclosed; it's the safe floor Option B falls back to.

## Decision

Adopt **Option B**. `suggested_domains` (from `nightshift-ctl review plan`, computed at the PR's
current head SHA) becomes authoritative for exactly one narrow question: **which review domains,
beyond the two unconditional checks (one different-vendor code review, QA), a crew's fanout
proactively dispatches for a given diff.** It remains non-authoritative for everything ADR-0036
and ADR-0040 already said it was non-authoritative for: it is not an authorization receipt, it
cannot satisfy `review eligibility`'s frozen required-domain contract, and `bin/nightshift_review_receipts.py`
and `bin/nightshift_review_eligibility.py` continue to never import or reference it (the existing
`test_module_does_not_import_the_shadow_planner` conformance test is unaffected and stays green
unmodified — this ADR touches no file that test inspects).

**Exact `program/**` paths this authorizes changing** (naming them here is what P-A's routing
decision requires before program grants a lease or charters platform to implement; line numbers
verified against `origin/main`, see Addendum below for the round-1 correction trail):
- `program/agents/crew_alpha/config.yaml` §Phase 3 verify section (the `SECURITY GATE` paragraph
  and its neighbors, today ~lines 132–135) and `program/agents/crew_alpha/skills/fanout/SKILL.md`
  §4 ("Gate every PR") — replace unconditional "dispatch every domain" with "dispatch code review +
  QA unconditionally, plus exactly the domains in this head's `suggested_domains`."
- `program/agents/crew_alpha/config.yaml` §Phase 4 ("PR, then cross-crew review", today lines
  146–150) — today's trigger ("tell the program the PR is ready for CROSS-CREW REVIEW") is
  unconditional. Make it domain-conditional: fire only when `suggested_domains` includes
  `cross_crew` — the actual emitted value (confirmed against platform's shipped PR #265 and
  `tests/test_233_review_routing_activation.py`; ADR-0040's own table at line 142 names this rule
  `cross_crew_review`, but the shadow planner emits `cross_crew`, and this ADR gates on what is
  actually emitted), mirroring ADR-0040's own mapping ("added whenever `profile == HIGH_RISK`,
  i.e. whenever anything below is non-empty"). Without this, Phase 3's narrowing alone cannot
  reduce cross-crew review volume, which this ADR's own §Consequences claims as a benefit — the
  claim is false without this edit.
- `program/agents/crew_beta/config.yaml` (§Phase 3 and §Phase 4) and
  `program/agents/crew_beta/skills/fanout/SKILL.md` §4 — mirrored change, same rule, seam B.
- `program/agents/release/config.yaml` — merge precondition **2** (today line 30, different-vendor
  review) and precondition **5** (today lines 35–39, CROSS-CREW REVIEW — this is the one AC-2/AC-5
  actually need, and it was misfiled under the wrong line range in this ADR's first draft) —
  replace independent re-verification with direct consumption of `review eligibility`'s
  `eligibility_digest`/`eligible` fields. Precondition **4** (today lines 33–34, `security`) is
  conditional already ("if it touched the sensitive set") and stays as-is — it is not part of
  AC-2/AC-5's scope and this ADR does not authorize touching it. Preconditions 1 (pm sign-off, line
  29), 3 (QA pre-fail proof, lines 31–32), 6 (ADR reference, line 40) are untouched — they are not
  review-domain routing.
- **New paragraphs, mirroring `SECURITY GATE`'s shape** (`program/agents/crew_alpha/config.yaml`
  and the `crew_beta` mirror, placed alongside the existing `SECURITY GATE` paragraph): today there
  is no mechanical dispatch-side gate for `ux` or `staff` — §"What lives outside the crew"
  (lines 44–54) lists both as "ask the program" on crew-lead judgment only, unlike `security`'s
  explicit mandatory paragraph. `program/review-risk.json`'s ruleset already classifies both
  mechanically (ADR-0040's table calls the second group `staff_adr`; the actual emitted
  `suggested_domains` value — confirmed against PR #265's shipped implementation — is `staff`):
  `ux` — paths matching `conditional_globs["ux"]`; `staff` — paths whose `high_risk_globs`
  matches include `constitution`, `control_plane`,
  `schema_migration`, `dependency`, or `public_contract`), so the input this ADR needs already
  exists; only the dispatch-side gate paragraph does not. Authorize adding:
  - `UX GATE` — if the diff touches any path matching `program/review-risk.json`'s
    `conditional_globs["ux"]`, tell the program it needs `ux` before that PR is called ready.
  - `STAFF GATE` — if the diff touches any path matching `program/review-risk.json`'s
    `constitution`, `control_plane`, `schema_migration`, `dependency`, or `public_contract`
    `high_risk_globs` categories, tell the program it needs `staff` before that PR is called ready.

**Fail-closed default (issue #233's own requirement, made concrete per DoR AC-7/AC-8):** whenever,
for the PR's current head SHA, `suggested_domains` is absent, stale relative to that head, or
malformed, OR this ADR's activation flag is off, every one of the paths above falls back to its
own pre-#233 default — never to zero, never to a partial guess:
- Phase 3 dispatch narrowing and Phase 4's cross-crew trigger fall back to **today's unconditional
  full behavior**: every domain, always ask for cross-crew review.
- The new `UX GATE`/`STAFF GATE` paragraphs, which have no mechanical "today" to fall back into
  (today is lead judgment, not a gate), fall back to **always asking the program for `ux`/`staff`**
  on that PR — the same wider-net principle applied to a mechanism that is new rather than narrowed.
- `release/config.yaml`'s preconditions 2/5 fall back to independently re-verifying, exactly as they
  do today, rather than trusting a digest that can't be proven fresh.
This is the wider net, not the narrower one, on every failure path, for every path this ADR
touches. The fallback reason (`suggestion_missing` / `suggestion_stale` / `suggestion_malformed` /
`activation_disabled`) is written into the existing dispatch/admission log entry for that PR, not a
new subsystem, so the crew lead and program heartbeat can see why full fanout fired without
re-deriving it (AC-8).

## Consequences

**Makes easy:** review capacity stops being spent on domains a diff mechanically can't trigger
(e.g. a docs-only PR no longer automatically pulls Security/UX); Release's merge-precondition
prompt shrinks to one digest read instead of six independent re-derivations, matching #233's "thin
Release" goal without touching what it actually gates on.

**Makes hard:** two program/** instruction sets (crew_alpha, crew_beta) now have a live dependency
on `review plan`'s output freshness at dispatch time — a `review plan` regression that under-reports
domains is now a capacity/thoroughness bug (caught by fail-closed, not silent), where before it
would have been inert (shadow-only, nothing consumed it for dispatch).

**Forecloses:** nothing at the eligibility/merge layer — ADR-0040's frozen contract is completely
untouched and remains the only thing `release` gates a merge on. Does not authorize any future slice
to let `suggested_domains` satisfy eligibility; that would need its own successor ADR, same as
ADR-0036 always required.

## Cost

No new infrastructure, no new dependency, no recurring spend. Operational surface added: the
fallback-reason field in the existing dispatch/admission log (append to an existing record, not a
new table/subsystem) and the conformance tests named in DoR AC-7/AC-8. Negligible at today's volume
and at 100×; this is a routing rule over data already computed on every PR.

## One-way door?

No. The fail-closed default means removing/reverting this ADR (flip the activation flag off) drops
the system straight back to Option C's behavior (full fanout, `suggested_domains` fully inert for
dispatch) with no data migration, no schema change, and no PR left in an inconsistent state — every
merged PR under this ADR still passed the same eligibility gate a PR merged under the old behavior
would have.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`). Trigger to revisit: if `review plan` invocation
volume at dispatch time (one call per PR per crew, in addition to today's eligibility-time call)
becomes a measurable bottleneck, revisit caching per ADR-0040's own reversal trigger (2), not by
hand-optimizing this ADR's dispatch path separately.

## Reversal trigger

(1) If a `review plan` regression or staleness incident causes fail-closed to fire more than
rarely in production (i.e., the mechanical signal is unreliable in practice, not just in theory),
revisit whether dispatch-time gating is worth the operational surface versus keeping full fanout
permanently. (2) If a future slice wants `suggested_domains` to influence eligibility (not just
dispatch), that is explicitly out of scope here and needs its own successor ADR — this ADR must
not be read as precedent for that question. (3) If issue #236 clears, any follow-up build on this
ADR should still run a real different-vendor design-debate before implementation, per normal
process — the single-author deviation here is scoped to drafting the ADR itself, not to the
Phase 2 build that follows it.

## Addendum — staff consistency-review round 1 findings, fixed same branch

`staff`'s consistency review returned **CONSISTENT on substance** (the eligibility/dispatch split,
the fail-closed direction, and the single-author flag are all sound and don't conflict with
ADR-0036/0038/0040) but held the charter-to-platform step on three accuracy defects in §Decision's
"exact `program/**` paths" list, since platform scopes its edit verbatim against that text. Fixed
in place above rather than as a new ADR number, same discipline ADR-0040's own round-1 fix used —
this PR has not merged.

1. **`release/config.yaml` line citation was wrong.** Original text cited "lines ~28–33
   (preconditions 2, 4, 5)." Traced against `origin/main`: precondition 2 is line 30, precondition
   4 is lines 33–34, and precondition 5 — the one AC-2/AC-5 actually need (cross-crew review) — is
   at lines 35–39, outside the cited range entirely. The cited range instead covered preconditions
   1 and 3, which this ADR's own text said were untouched. Fixed: precondition 5's real lines are
   now cited; precondition 4 is explicitly excluded from this ADR's authorized scope (it's already
   conditional on the sensitive set and AC-2/AC-5 don't touch it); 1/3/6 remain named as untouched
   for clarity rather than silently dropped.

2. **Missing authorized edit: `config.yaml` §Phase 4, not just §Phase 3.** Phase 4's cross-crew
   review trigger (today unconditional) has no domain condition, so §Phase 3's dispatch narrowing
   alone cannot deliver the cross-crew-volume reduction this ADR's own §Consequences claimed. Added
   Phase 4 (both crews) to the authorized path list, gated on `suggested_domains` including
   `cross_crew` (the actually emitted value; ADR-0040's own table names the rule
   `cross_crew_review`/`HIGH_RISK`, matched here in substance), rather than inventing a new rule.

3. **`ux`/`staff` have no mechanical dispatch-side gate to narrow today** — unlike `security`'s
   `SECURITY GATE` paragraph, both are "ask the program" on lead judgment only
   (`config.yaml:44–54`), even though `program/review-risk.json` already classifies both
   mechanically (ADR-0040's table). Chose to authorize new `UX GATE`/`STAFF GATE` paragraphs
   (mirroring `SECURITY GATE`'s shape, trigger conditions named above) rather than scope them out as
   follow-up: #233's own must-have text ("keep... every mechanically triggered Security, UX, QA,
   Staff, and cross-crew domain") requires UX/Staff to already be mechanically operative, and there
   is no existing gate for platform to narrow — so this ADR has to author the gate itself, or #233
   can't actually satisfy its own must-have for those two domains. Fail-closed for these two
   specifically falls back to "always ask" (there is no narrower "today" to fall back into), the
   same wider-net principle applied to a mechanism that's new rather than narrowed.

No other section of this ADR changed. §Decision's core split (dispatch vs. eligibility), §Options,
§Consequences' substance, §Cost, §One-way-door, §Scale-tier, and §Reversal-trigger are all
unaffected — this addendum is a citation/scope-completeness fix, not a re-decision.

## Acceptance

`staff` reviewed for consistency against ADR-0036, ADR-0038, and ADR-0040 twice: round 1
(commit `583e419`) returned **consistent on substance**, holding the charter step on three
accuracy defects in this ADR's own `program/**` path list (a wrong `release/config.yaml` line
range that missed precondition 5, a missing Phase 4 authorized path, and no named mechanism for
the new `ux`/`staff_adr` gates). crew_alpha fixed all three in the addendum above (commit
`c5dffac`). Round 2 (targeted recheck, same commit) independently re-verified every corrected
citation against the actual file bytes on this branch and returned **CONSISTENT — cleared for
charter**, confirming no conflict with ADR-0036/0038/0040's eligibility boundary and that the
path list is accurate and sufficient for `platform` to implement against as written.

Status changed `proposed` → `accepted` by `program` on this basis. `platform` is chartered to
implement the named `program/**` paths against this ADR's spec; `crew_alpha` builds the rest of
issue #233 in its own seam A workspace in parallel, disjoint files, no serialization required.

Reviewer: `staff` (session `96b7de44a2ee493a9b655b71fd08c76d`, run `fixture-review-231`).

## Addendum — wording correction, post-merge (platform's PR #265 shipped)

Non-blocking follow-up (task `233-adr-0042-wording-followup`), filed after ADR-0042 merged
(`fab54c0`) and platform's `program/**` implementation merged (PR #265, `b8f7e2f`). Pure wording
fix, no semantic change — no staff re-review requested for this round, per the same discretion
ADR-0040's own round-1 fix used for citation-only corrections; §Decision's actual authorization
(which paths, which gate shapes, fail-closed direction) is unchanged, and this addendum does not
reopen §Acceptance above or the `accepted` status.

**Finding:** §Decision's prose named the gating `suggested_domains` values as `cross_crew_review`
and `staff_adr` in a few places, borrowed directly from ADR-0040's own table (line 142, line 144).
But the real emitted values — confirmed against `tests/test_shadow_risk_plan.py:205`
(`self.assertIn("staff", report["suggested_domains"])`) and platform's shipped PR #265
(`tests/test_233_review_routing_activation.py`, `program/agents/crew_alpha/config.yaml`'s merged
`STAFF GATE`/Phase 4 text) — are `staff` and `cross_crew`. ADR-0040's table names were never what
the shadow planner actually emits; ADR-0042 inherited the mismatch by quoting ADR-0040 verbatim
instead of checking the emitted value. Platform's implementation correctly used the real values,
so nothing shipped was wrong — only this ADR's own prose was stale relative to what it authorized.

**Fixed:** every place §Decision names the value it gates dispatch on now reads `cross_crew` and
`staff`, matching PR #265. Where the text still says `cross_crew_review` / `staff_adr`, it is now
explicit that those are ADR-0040's *table label* for the rule, not the emitted value, so a future
reader isn't misled a second time. This ADR-0040/shipped-code naming drift is itself out of this
addendum's scope — ADR-0040's own table wording is unchanged and not this task's concern; flagging
it here only so the discrepancy is visible in the audit trail, not asking anyone to act on it.

No other section changed. §Options, §Consequences' substance, §Cost, §One-way-door, §Scale-tier,
§Reversal-trigger, the round-1 Addendum, and §Acceptance above are all unaffected.
