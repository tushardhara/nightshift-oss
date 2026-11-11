# Review contract

Copy this template into a committed work-item/spec artifact and freeze that file at
the wave checkpoint before the first implementation commit. The delivery PR links
and may mirror the exact committed version; its mutable body is never the authority.
Do not edit a frozen version in place.

## Identity

- Work item: GitHub issue #233, "#228D — Activate risk-based review routing and thin
  Release" (tushardhara/nightshift). `state:ready`; pm Definition-of-Ready verdict
  READY posted https://github.com/tushardhara/nightshift/issues/233#issuecomment-5455186606
  (re-check, 2026-08-28), original pass
  https://github.com/tushardhara/nightshift/issues/233#issuecomment-5454613003.
- Committed contract path and blob OID: `docs/reviews/233-activate-review-routing-CONTRACT.md`;
  blob OID is the exact-content hash of this file at the freezing commit — read it
  with `git rev-parse <freezing-commit>:docs/reviews/233-activate-review-routing-CONTRACT.md`.
- Contract version: v1.
- Base SHA: `fab54c012c5af34891f1443f44db8de859454e46` (origin/main tip at freeze
  time, includes ADR-0042/PR #263 and the round-1 staff-fix addendum).
- ADR: `docs/decisions/ADR-0042-suggested-domains-gate-fanout-dispatch-not-eligibility.md`
  (`Status: accepted`; supersedes ADR-0036/ADR-0040 only on the dispatch-gating
  question named there; staff consistency review CONSISTENT on round 2).
- Implementer: `platform`, chartered directly by `program` (task
  `233-program-fanout-gates`) — every path ADR-0042 authorizes changing is
  `program/**`, which `crew_alpha` does not own and cannot lease per
  `docs/owners.md:22`. This is NOT a `crew_alpha` Working-Agreement-#5 build: WA-5
  (`.omni/working-agreements.md`, wave 1, issue #13/#236) authorizes a crew lead to
  build directly when its OWN worker dispatch is sterile and the work is inside its
  OWN file scope; it does not create ownership `crew_alpha` doesn't otherwise have.
  `crew_alpha` traced every AC below against `docs/owners.md` and found no
  `bin/nightshift-ctl` / `deploy/policies/**` / crew_alpha-owned artifact left to
  build for #233 (see per-AC notes) — this contract is `crew_alpha`'s full
  contribution to #233 beyond the ADR itself.
- Review owner: not yet assigned by `program` — the normal alpha/beta
  implementer/reviewer rotation (`crew_alpha` implements, `crew_beta` reviews, as
  #231/#232 used) does not apply cleanly since the implementer is `platform`, not a
  crew. `program` should name the cross-crew reviewer once `platform`'s PR opens;
  recommend `crew_beta`, since `program/agents/crew_beta/**` is itself part of the
  diff and crew_beta has direct stake in verifying its own mirrored instructions
  land correctly — the diff should still get an independent read, not just
  self-interested verification, so name a specific reviewer rather than defaulting.
- Frozen at (UTC): 2026-08-28 (run `fixture-review-231`).

## Acceptance criteria

Verbatim from the pm's Definition-of-Ready re-check verdict on issue #233
(#issuecomment-5455186606). IDs are stable identifiers, not reordered.

| ID | Exact criterion | Verification | Owner of the implementing artifact |
|---|---|---|---|
| AC-1 | Fanout dispatch is authorized before it is built: ADR-0042 `accepted` and staff-reviewed, exact `program/**` paths named, before any Alpha/Beta fanout instruction file is edited. | ADR file exists with `Status: accepted` (it does, `fab54c0`); staff verdict recorded (`.omni/decisions.md`); implementation PR diff touches only the paths ADR-0042 §Decision names, nothing else. | `platform` (implementation); already satisfied for the authorization half. |
| AC-2 | Release's authority to gate is narrowed to the exact eligibility aggregate: `program/agents/release/config.yaml`'s merge-precondition prompt has precondition 2 (line 30) and precondition 5 (lines 35–39) replaced by one `review eligibility` consumption step; precondition 4 (security, conditional) and preconditions 1/3/6 stay untouched. | Diff of `program/agents/release/config.yaml` shows exactly preconditions 2 and 5 replaced; a fixture test (see AC-3 below) asserts `release`'s only review-domain input for those two is `eligibility_digest`/`eligible`. | `platform` — `program/**`, no `bin/nightshift-ctl` involvement (`review eligibility` already exists, read-only, unchanged by #233). |
| AC-3 | Fanout change is additive-safe: Phase 3 dispatch narrowing, Phase 4's now-conditional cross-crew trigger, and the new `UX GATE`/`STAFF GATE` paragraphs never skip a domain `program/review-risk.json`'s ruleset mechanically triggers for the given diff; code review and QA stay unconditional. | A fixture test that, for every rule group in `program/review-risk.json` (`conditional_globs`, `high_risk_globs`), constructs a diff tripping that rule and asserts the corresponding domain is still asked for under the new instructions. Because the "asking" behavior lives in prompt text consumed by an LLM crew lead, not deterministic code, this test's shape follows `tests/test_review_receipts.py:457-463`'s own pattern (structural/textual assertion against the committed file, e.g. asserting the `UX GATE`/`STAFF GATE` paragraphs exist and name the exact `review-risk.json` trigger keys) rather than executing an agent. | `platform` — the test drives `program/**` content, and per `docs/owners.md`'s "ownership by what the test DRIVES" rule (row A5), that test belongs to whoever owns the file it drives. `crew_alpha` does not claim this test. |
| AC-4 | Zero behavior change to worker capacity, task identity, #212 NEW/CONTINUATION admission, claims, one-dispatch enforcement. | `tests/test_admission_gate.py` and existing #212 NEW/CONTINUATION tests run unmodified, green, zero diff to assertions. | **No implementation needed** — #233's own must-have text says "remain unchanged"; this is a regression-proof against already-existing `bin/nightshift-ctl` behavior, not a build item. |
| AC-5 | Release consumes the exact current eligibility aggregate; no new semantic review. | Same authorized edit and fixture test as AC-2. | `platform`, same as AC-2. |
| AC-6 | Reuse existing Release merge-candidate testing, normal protected merge, post-main verification, revert behavior — no changes. | `bin/nightshift-release` diff (if any) touches only the review-domain-consumption prompt lines identified in AC-2/AC-5; every other function byte-identical. | **No implementation needed** — explicit reuse, not a build item. |
| AC-7 | Activation is fail-closed with named conformance tests: absent/stale/malformed `suggested_domains`, or the activation flag off, falls back to full unconditional dispatch (or, for the new `UX GATE`/`STAFF GATE`, to "always ask" — there is no narrower "today" to fall back into for those two). | Named tests per ADR-0042's "Fail-closed default" paragraph: `suggestion_missing`, `suggestion_stale`, `suggestion_malformed`, `activation_disabled` — each asserting the dispatched/asked domain set equals the pre-#233 full set, never a subset. Staleness is checkable today without new code: `nightshift-ctl review plan`'s existing output already includes `target.head_sha` (`bin/nightshift_review_plan.py`); "stale" means the instructions compare that field to the PR's actual current head before trusting `suggested_domains`, a prompt-level check, not a new CLI capability. Malformed/absent already fail closed via `review plan`'s existing `PlanDenied`/exit-3 path. | `platform` (instructions + textual conformance tests, same ownership reasoning as AC-3). `crew_alpha` verified by direct read of `bin/nightshift_review_plan.py:505-524` and `bin/nightshift-ctl:3458-3475` that no new `bin/nightshift-ctl` field or subcommand is needed for staleness/malformed detection — the data this criterion needs already exists. |
| AC-8 | Fail-closed fallback is observable: the fallback reason (`suggestion_missing` / `suggestion_stale` / `suggestion_malformed` / `activation_disabled`) is visible in the existing dispatch/admission log, not a new subsystem. | Fixture test asserts the reason string is present in the crew lead's existing dispatch report to `.omni/decisions.md` for each of AC-7's four triggering conditions. | **No `bin/nightshift-ctl` schema change** — `crew_alpha` checked two candidate mechanisms and ruled both out: `task_admissions` has no reason column and AC-4 forbids touching it anyway; `review_dispatch_markers.marker` is a `secrets.token_hex(16)` opaque anti-replay token (ADR-0039 AC-3c) and reusing it for a human-readable reason would break its anti-forgery contract — wrong tool. The "existing dispatch/admission log" this AC means is `.omni/decisions.md`, the prose log every crew lead already appends dispatch/routing decisions to (see this very work item's own transcript for the pattern) — a process/instruction requirement (`program/**`, i.e. `platform`'s prompt text telling the lead to log the reason), not a code artifact. |
| AC-9 | Out of scope, locked: no new publication/integration state machine; no custom Git push transport, lost-response reconciliation, tags controller, rollback controller, Bubblewrap, crash/PID recovery, workflow redesign, deployment, VPS changes; #213–#223 untouched. | Verbatim from the issue; PR diff touches no path outside ADR-0042's named `program/**` list. | N/A — a boundary, not a build item. |

## Explicitly out of scope

- Any `bin/nightshift-ctl`, `deploy/policies/**`, or other `crew_alpha`-owned code
  change — traced against every AC above and found unnecessary (AC-4/AC-6 are
  explicit "unchanged" requirements; AC-7/AC-8's apparent code needs are already
  satisfied by existing `review plan` output and existing `.omni/decisions.md`
  logging discipline respectively).
- Any change to ADR-0040's frozen required-domain contract or review eligibility's
  merge-gating logic — ADR-0042 §Decision is explicit that this is untouched.
- Any change to #212 admission, claims, worker capacity, or one-dispatch
  enforcement (AC-4).
- #213–#223, deployment, VPS changes, any new state machine (issue #233's own
  out-of-scope list, carried forward unchanged).

## Required review domains

- [x] PM acceptance — done via the DoR re-check thread on issue #233
      (#issuecomment-5455186606, verdict READY)
- [x] Different-vendor code review — `platform`'s implementation touches
      structured YAML/prompt files with mechanical trigger conditions; review
      against this contract's AC table, not free-form
- [x] QA — the fixture/conformance tests named in AC-3/AC-7/AC-8
- [x] Cross-crew review — implementer is `platform`, not a crew; `program` to name
      the reviewing crew (see §Identity) since both `crew_alpha` and `crew_beta`
      instruction files are in scope and neither should self-review its own mirror
- [x] Staff / ADR consistency — already satisfied pre-implementation: ADR-0042
      accepted, round-2 staff verdict CONSISTENT
- [ ] Security — `program/**` prompt/config changes only, no auth/PII/SQL/
      subprocess/crypto/dependency surface touched by ADR-0042's authorized path
      list; flagging as not-required rather than silently omitting, since
      `program/**` is itself constitution-sensitive (`docs/owners.md:22`) — if
      `program` or `security` disagrees with this scoping once the actual diff
      exists, escalate before merge, not after
- [ ] UX — no human-facing flow, copy, or operator-facing interface change; this
      is org/dispatch-routing tooling

Machine-readable parsing of this checklist follows the rule in
`docs/reviews/REVIEW-CONTRACT-TEMPLATE.md`'s "Required review domains" section
(per #231 AC-9): each row matched by a fixed canonical-label PREFIX, independent of
any parenthetical rationale; `[x]`/`[X]` means required, `[ ]` means not required.

Removing a domain after freeze requires a new contract version and rationale.

## Finding receipt

Every finding uses one severity from ADR-0026.

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 |  | `MERGE_BLOCKER` / `PRE_DEPLOYMENT` / `FOLLOW_UP` |  |  |  | open |  |

The review owner publishes one deduplicated fix packet per round. A severity change
must retain the original finding and record the rationale.

Never execute a reproduction against a live system. A production-only security or
data-loss risk may be proven by a concrete causal failure scenario and safe evidence.
