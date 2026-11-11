# ADR-0051: Close #228's four remaining regression-proof gaps with targeted tests, not new mechanism

Date: 2026-08-30 · Status: proposed
Deciders: crew_alpha (implementer, single-author under disclosed WA-5/#236),
staff (gate, constitution/control_plane paths touched)
Spec: GitHub issue #277; DoR `.omni/reports/2026-08-30-277-dor-verdict.md`;
frozen contract `docs/reviews/277-close-228-regression-proof-gaps-CONTRACT.md` v1
Depends on: ADR-0036 (shadow risk plan), ADR-0039 (persist review receipts),
ADR-0040 (review eligibility), ADR-0042 (suggested_domains gates fanout
dispatch) — extends none of their frozen contracts, adds test coverage only

## ELI5

#228 shipped a system that reviews small changes lightly and risky changes
heavily. #228 itself listed 15 things a working version of that system must
be able to prove, and closed with 4 of those 15 unproven — not broken, just
never checked by an executed test. This ADR is the record of exactly how each
of those 4 gets checked (or, in one narrow case, is explicitly declared "not
this issue's job, tracked separately") and why.

## Context

Issue #228's "Definition of finished" named 15 "Required regression proofs."
It shipped via #231/#232/#233 plus its own direct PR (#239); a full
verification pass on 2026-08-29 found 4 of the 15 unconfirmed. Issue #277
asks this crew to close each one with a real, executed test, or explicitly
scope it out with a recorded reason — via this issue's own DoR, which is
where the actual investigation happened (the issue body's one-line
description of each gap turned out to be right for three of the four and
narrower than described for the fourth; see the DoR document for the full
re-derivation).

Engineer/PM/QA/reviewer dispatch is sterile this run (issue #236, WA-5,
unchanged across the entire project record through 2026-08-30 08:30Z). This
ADR is authored directly by the crew_alpha lead, per the crew's own charter
and working-agreements.md rules 5/6. The debate this ADR would otherwise owe
two independent engineers is instead recorded below as an explicit two-option
call per decision, by the same author, mirroring what a second vendor would
most plausibly have pushed back on — the same substitute ADR-0039 used under
the identical constraint.

## Options considered

### D1 — Proof #2 (STANDARD concurrent dispatch): what does "regression-proof" mean when the mechanism is LLM-turn behavior, not code?

**A — Assert the one code-level artifact the dispatch procedure actually reads
(`suggested_plan.human_review.mode`/`wave`), extended to STANDARD/HIGH_RISK
fixtures, chosen.** `build_plan()` already emits this field unconditionally;
before this ADR only the FAST fixture checked it. This cannot prove an agent
will actually dispatch concurrently in a given turn — no test can, since
that is a live model's behavior — but it does prove the one deterministic
signal that behavior is contracted against has not silently reverted to a
sequential-wave shape. Paired with a structural test of the fanout SKILL.md's
"emit ... in THIS turn" instruction (same non-code-but-committed-artifact
category `test_233_review_routing_activation.py` already established as a
valid regression-test target for LLM-consumed prose).

**B — Do nothing further; leave this as "prose enforces it," matching the
issue's own framing of the gap. Rejected.** This is the status quo the issue
was filed to fix. If `human_review`'s literal ever changes to special-case a
profile, nothing today would notice, and the same is true of a future edit to
the SKILL.md paragraph.

**C — Build a real integration test that spins up two sub-agent sessions and
measures wall-clock overlap. Rejected.** Sub-agent dispatch is sterile this
session (#236) and, more fundamentally, out of budget/scope for a size:M
regression-closure issue — this would be a new test-infrastructure capability
(`platform`-owned), not a targeted fix, and risks exactly the "re-litigating
already-shipped design" this issue's own "Not included" section forbids by
implication (it would effectively define a new automated dispatch-concurrency
harness beyond #228's scope).

### D2 — Proof #7 (byte-subset causal invalidation): prove it with a new fixture shape, or trust existing coverage?

**A — Add one fixture with two differently-scoped, previously-reviewed paths
in one diff, then a fix touching only one of them, chosen.** Every existing
`test_review_eligibility.py` case changes one path, or an unrelated path —
never two REVIEWED paths with different domain scopes where only one gets
fixed. This is the literal shape of "a fix that changes only a subset of
reviewed bytes," and a live negative control (collapsing `covered_scope` to
whole-diff granularity) proves this exact new test — and only this one, not
the pre-existing suite — catches that specific regression class.

**B — Treat this as already covered by `test_ac2_reuse_across_an_unrelated_change`/`test_ac2b`. Rejected.**
Those tests use an UNRELATED added file, never a second REVIEWED path in the
SAME original diff — they cannot distinguish "changed vs. unrelated" from
"changed a subset of a diff that had multiple reviewed regions," which is
what proof #7 actually asks about. The negative control run for this ADR
confirms the distinction is real: the new test fails under a regression the
existing suite's own negative controls (recorded in prior ADRs) never
exercised.

### D3 — Proof #10 (ADR required only for qualifying changes): extend the mechanical gate's test coverage, or build citation enforcement?

**A — Add one test proving the mechanical staff/ADR gate bidirectionally
under REALISTIC classification, and explicitly scope out ADR-citation
enforcement as a separate follow-up, chosen.** Investigation during DoR found
the mechanical direction ("does a qualifying diff route to `staff`, does a
routine one not") already has strong, if synthetic-fixture-only, coverage in
`test_233_review_routing_activation.py`. The genuinely open piece — whether a
qualifying diff's `docs/decisions/**` actually contains a corresponding ADR —
has no schema support (`tasks` has no `adr` column; ADR-0039's receipt JSON
schema is frozen to a closed key set) and would need a new field or a
receipt-schema change to enforce mechanically.

**B — Add the schema/receipt change now (a `staff_adr` receipt requires an
`adr_ids` array cross-checked against `docs/decisions/**` file presence in
the diff). Rejected for this issue.** This is a real, useful idea, but it is
a NEW design decision on top of ADR-0039's already-accepted, frozen contract
(AC-3's exact JSON key set) and #228/ADR-0042's already-shipped shape — both
of which this issue's "Not included" section says not to re-litigate. It also
has a real false-positive risk this ADR should not wave away: a qualifying
change can legitimately cite a PRE-EXISTING ADR (as THIS very PR does, citing
ADR-0051 for its own test additions, none of which touch `docs/decisions/**`
again after this ADR's own commit) without adding a new file in the same
diff — a same-diff-file-presence heuristic would misfire on exactly that
common, correct case. Recorded as a named follow-up in `.omni/tech-debt.md`
rather than built hastily.

### D4 — Proof #15 (dispatch-count/model-turn fixtures): what quantity stands in for "dispatch count" and "model turns"?

**A — `dispatch_count = len(suggested_domains)`; `model_turns = 1 +
dispatch_count`, both asserted across 5 chained, incremental (single-commit)
fixtures spanning FAST/STANDARD(×2)/HIGH_RISK(×2), chosen.** `suggested_domains`
is already the deterministic, code-computed set the fanout procedure dispatches
one crew-lead-initiated turn per entry for; the two deterministic gates cost no
model turn. Two STANDARD fixtures with different counts (2 vs. 3) prove the
assertion tracks the real trigger (a `qa` conditional glob), not a fixed
per-profile constant a "universal max review" regression could satisfy by
accident. A live negative control (hardcoding all 6 domains regardless of
profile — the literal named failure mode) fails 5 of the new test's
assertions.

**B — Count actual historical dispatch log rows from `.omni/decisions.md`/`.omni/ledger.md`
entries. Rejected.** Those are free-text prose logs, not a queryable schema;
building a parser against them is disproportionate to a regression-test
requirement and would itself need its own design review (parsing
free-text operational logs as an authority source is exactly the kind of
fragile heuristic ADR-0039's own Q1 rejected for a related reason).

## Decision

Adopt D1-A, D2-A, D3-A (with D3's citation-enforcement half explicitly
deferred, not built), and D4-A. Four new/extended tests land in this PR:

1. `tests/test_shadow_risk_plan.py::test_standard_and_high_risk_domains_share_one_parallel_wave` (proof #2)
2. `tests/test_review_eligibility.py::test_ac7_byte_subset_partial_fix_invalidates_only_the_touched_domain` (proof #7)
3. `tests/test_277_228_regression_proofs.py` — new file, two classes: `Proof2ConcurrentDispatchProseTest` (proof #2's prose half) and `Proof10AdrGateBidirectionalTest` (proof #10's mechanical-gate half)
4. `tests/test_shadow_risk_plan.py::test_dispatch_counts_stay_narrow_and_distinct_across_risk_tiers` (proof #15)

No production code changes ship with this ADR. Every new test was proven to
fail against a live, hand-introduced regression matching the exact failure
mode its proof number names, then reverted before commit (recorded in the DoR
document and the PR body, not merely asserted).

## Consequences

Makes it possible to catch, mechanically, at CI time: a fanout dispatch
schema that special-cases STANDARD/HIGH_RISK into sequential waves; an
eligibility granularity regression that collapses scoped domains to
whole-diff invalidation; a STAFF GATE that stops firing for a qualifying
category or starts firing for routine work; and a classifier that silently
drifts toward dispatching every domain for every change. Does NOT make it
possible to catch a qualifying change that ships without ever authoring an
ADR (D3-B's deferred scope) — that remains a reviewer-judgment gap, named
explicitly rather than assumed closed.

## Cost

Four new/extended unit tests, all using the existing bundle/subprocess
fixture pattern already paid for by `test_shadow_risk_plan.py`/
`test_review_eligibility.py`. Suite runtime cost: the new tests add under 10
seconds to the existing files' run time (measured locally). No new
infrastructure, no new dependency, no schema migration.

## One-way door?

No. These are additive tests against existing, already-accepted contracts
(ADR-0036/ADR-0039/ADR-0040). Removing or loosening any of them later is a
normal test-suite change, not a data or interface migration.

## Scale tier

Tier 0, same as the ADRs it extends test coverage for (ADR-0036/ADR-0039).
No new operational surface.

## Reversal trigger

If a future ADR extends `tasks` with an ADR-citation field or reopens
ADR-0039's receipt schema (closing D3-B), this ADR's "Consequences" section
should be updated to remove the named remaining gap rather than left stale.
If sub-agent dispatch (#236) is fixed, D1's "no live-concurrency integration
test" limitation should be revisited — a real multi-session timing test
becomes possible and cheap at that point, where today it is neither.
