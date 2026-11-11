# ADR-0074: RT-17 earns its current-generation clause, and F-1's kill moves from prose to persisted state — without changing the policy contract

Date: 2026-09-02 · Status: accepted · Deciders: crew_alpha lead · Proposers: eng_claude (proposal A), eng_codex (proposal B); qa (test plan), pm (spec)
Task: `506-step2-manifest-binding` · Issue: #507 · DoD condition: D06
Base SHA: `c34d23b93b7bb7e84f9919ccf9555ffa65114bd3`
Spec: `docs/specs/506-step2-manifest-binding.md` (PR #623)
Amends ADR-0069 §§2, 3, 5 — **extends §§2 and 5, and CORRECTS §3.** ADR-0069's accepted
Decision text is left byte-unchanged. But §3 is not merely extended: the `baseline_sha`
`f2abab8` it declares for RT-17 is **false as published** — `test_slots.RT17ConcurrentAcquiresShareOneBudget`
does not exist at that commit (count 0), so the declared command there yields exit 1, not the
declared exit 0. Decision 5 below re-pins it to `c34d23b`. Every other claim ADR-0069 makes
remains true. See C4 and decision 5.

## Context

D06 STEP 1 (PR #585, ADR-0069) landed the RT-16/RT-17 classes and baseline captures but
deliberately deferred the `docs/prd/DOD-MANIFEST.json` binding to STEP 2, which waited on
#611 (merged as `c34d23b`). STEP 2 is expected to flip D06 from FAIL to PASS. The program's
standing instruction governs every choice here: *"I would rather it stay red than flip on
evidence that cannot fail."*

Six conditions were handed down. Investigation established that two of them rested on
premises that do not hold, and that a third was materially larger than stated. All findings
below were reproduced by at least two independent agents, or measured directly.

**C1 — ADR-0069 does not forbid a list binding.** The premise that it does is false. §2
already describes RT-16's bound set as "the two fixture-free bypass checks plus a positive
control" — three nodes across two classes — says "bound node**s**" in the plural, and
`RT-16.json`'s committed baseline `command` already runs all three. RT-19/32/34 already bind
lists. Confirmed independently by the lead, `pm`, and both proposers. **No amendment is owed
on this ground.** What *is* owed is an extension for the nodes newly added below.

**C2 — `unsatisfied_reason` is dead the instant an id binds.** `validate_rt_baseline`
(`tests/dod_validate.py:361-366`) returns before `baseline` is read when `test_node_id is
None`, and `:431` is its only print site. Every satisfied sibling (RT-15, RT-19, RT-32)
carries no such field. Confirmed by the lead, `qa`, `pm` and both proposers.

**C3 — the mutation command is byte-locked.** `_validate_mutation` (`:325-327`) rejects any
mutation whose `command` is not byte-identical to `baseline.command`. Any change to a
preservation id's bound node list therefore forces re-capture of the baseline *and* every
mutant.

**C4 — RT-17's committed record is not merely hybrid; it is false.** `RT-17.json` declares
`baseline_sha: f2abab8` and `exit_code: 0`. A stranger executing the declared command at the
declared SHA gets `AttributeError: module 'test_slots' has no attribute
'RT17ConcurrentAcquiresShareOneBudget'` — exit 1, a load error. The class was renamed in
PR #585 round 3 and does not exist there. `baseline_kind()` would derive `regression` from
that exit code. This is exactly the "capture failed because the harness could not run"
contamination class that ADR-0069's own constraint (3) exists to prevent, and it is the same
disease `staff` F-8 raised against RT-16. Measured by `eng_claude`; the absence at
`f2abab8` was independently confirmed by `qa` and the lead.

**C5 — the #607 problem is real, and deeper than the issue states.** `qa` executed the
allow-once flow under production and under the F-1 mutant (delete `dispatched_at IS NULL`
from the derivation `SELECT`) and diffed persisted state: `worker_slots.dispatched_at`,
`bound_agent`, `bound_title`, `state`, `phase`, the single `task_admissions` row and
`tasks.state` are **all identical**. Only the human-readable reason differs. So rewording
that string silently resurrects the mutant, and no assertion inside the *existing* scenario
can prevent it.

**C6 — and the reason C5 is true bounds what any fix can honestly claim.**
`worker_semaphore` carries `dispatched_at IS NULL` **twice**: in the derivation `SELECT`
(`omnipods_policies.py:288`) and again in the one-use compare-and-set `UPDATE` (`:449`,
`:320` on the oncall path). Both proposers found this independently. The consequence is that
**F-1 can never produce a duplicate dispatch in any scenario** — the CAS forecloses it. There
is no safety-face divergence to find. The lead verified all three code sites directly.

## Options considered

### A — change the scenario, assert on persisted state (proposed by eng_claude) — CHOSEN
F-1 makes consumed rows candidates for derivation again. The observable consequence is not a
duplicate dispatch but a **false ambiguity denial on the index-sanctioned identity-reuse
path**: `one_undispatched_dispatch_identity` is partial on `state='active' AND dispatched_at
IS NULL`, so a dispatched slot's `(expected_agent, expected_title)` is legitimately free for
reuse — a path `M2CrossTaskDispatchIdentityGuard.test_dispatch_clears_the_identity_for_
legitimate_reuse` already sanctions. `eng_claude` built the scenario (dispatch task A's slot
on a tuple; acquire task B's slot on the same tuple; dispatch again) and measured divergence:

| | production | F-1 mutant |
|---|---|---|
| second dispatch | `ALLOW` | `DENY — ambiguous dispatch identity: 2 active undispatched slots match` |
| slot 2 row | `dispatched_at` set, `bound_agent`/`bound_title` populated | `dispatched_at=null`, both null |

The prototype node passes at `c34d23b`, FAILS under the F-1 mutant at an assertion on
`result` backed by three persisted-state assertions, and FAILS at ancestor `6b52252`.
End-to-end: D06 **PASS** in a full dry run, and **FAIL** under F-1 with `failures=2`.
Costs no production change.

### B — add a machine-readable `reason_code` to the denial contract (proposed by eng_codex) — REJECTED
Add `reason_code: no_active_undispatched_slot`, set structurally at the zero-row branch, and
assert on it. Technically sound, and `reason_code` is vocabulary this module already speaks
(`task_readiness_snapshot(...)["reason_code"] == "budget_expired"`, noted by the lead).
`eng_codex` checked in-repo consumers and found no exact-dict comparison that an additive key
would break; `eng_claude` independently agreed in-repo consumers are additive-safe
(`deploy/preflight.sh:311` reads only `.get("result")`; ~129 `["reason"]` reads in tests are
all `assertIn`).

**Rejected on consumer risk, not on correctness.** `omnipods_policies.py:9` declares the
contract as `Service-Policies-V0 … evaluator(event) -> {"result": …, "reason": …}`, and its
real consumer is the **external, commit-pinned, non-vendored** omnigent engine
(`deploy/omnigent-runtime.lock.json`). Nothing in this repository defines that engine's
validation, so no in-tree evidence can establish that it tolerates an extra key. Option B
would stake the **root admission gate's return shape** on an unverifiable assumption in order
to buy a property Option A delivers at zero production risk. `eng_codex` reached the same
conclusion from the other side, listing "the policy host could enforce an undocumented closed
result schema" as its own first risk and conceding the external engine "remains an unverified
consumer" needing integration review.

**Note on B's impossibility argument.** B argued Option A was impossible. B was right about
what it actually proved — F-1 cannot persist a *duplicate dispatch*, because of C6 — but that
is a narrower claim than "no persisted-state divergence exists". A found a divergence on a
different face. The disagreement was settled by A having built and run the thing, not by
preference. This is recorded because B's reasoning was sound and its conclusion still wrong;
a future reader should not treat B's proof as discredited, only as scoped.

### C — decline the claim (rejected early)
Keep the prose assertion, record that the kill could not be made fallible, land D06 green.
Rejected: #607 is a program-visible objection with a standing instruction attached, and
landing green while conceding the evidence cannot fail is precisely the reading that
instruction forbids. Option A removes the need to make the concession.

### D — bind whole classes instead of node lists (rejected early)
Both proposers rejected it for the same reason, independently: `RT16SlotStateCoverage`
contains only the positive control today, but class-level binding would silently absorb any
future sibling method into D06's evidence and quietly invalidate the byte-stable capture's
meaning.

## Decision

1. **Take Option A.** No change to `deploy/policies/omnipods_policies.py`. The F-1 kill is
   carried by a new test node asserting persisted `worker_slots` state on the identity-reuse
   path.

2. **State the claim narrowly, in these words, wherever it is recorded.** *"F-1 makes a
   consumed slot a derivation candidate; the observable consequence is a false ambiguity
   denial on the index-sanctioned reuse path, asserted on persisted `worker_slots` columns.
   F-1 cannot produce a duplicate dispatch: the one-use compare-and-set independently
   forecloses it."* The kill is on the **liveness** face, not the safety face. A reviewer who
   expects "the mutant lets a bad dispatch through" is right to reject that framing, because
   no such scenario exists. Additionally: at ancestor `6b52252` the new node fails at its
   *first* assertion for the same reason the existing positive control does (`args.slot_id`
   required), so it contributes a **mutation kill at HEAD, not independent ancestor
   regression evidence**. Both sentences go in the RT-16 record's note.

3. **RT-16 binds four nodes as a list**, `cwd: "tests"`, binding unchanged
   (`deploy/policies/omnipods_policies.py`, symbol `worker_semaphore`): the two fixture-free
   bypass checks, the existing positive control, and the new reuse-path node. Re-captured at
   `6b52252` — measured `FAILED (failures=4)`, exit 1.

4. **RT-17 binds three nodes**, adding
   `test_authority_fence.SlotAcquireOncallTokenGate.test_denied_for_a_fenced_out_generation`.
   This is not optional: #611's merged D06 changelog row makes it "a condition of D06 going
   green" in product's own words. **No third mutant** — M1 kills the ceiling node, M2 kills
   both token nodes *and* the fence node. Measured independently three times (`qa`, `pm`,
   `eng_claude`), all agreeing: baseline exit 0, M1 exit 1, M2 exit 1.

5. **RT-17's `baseline_sha` moves `f2abab8` → `c34d23b`, and both mutants' `applied_to_sha`
   with it.** This fixes C4 and makes the record self-reproducing: the extended command in a
   clean `c34d23b` worktree yields exit 0. Preferred over documenting the hybridity, because
   documenting it would preserve a record whose declared exit code contradicts what the
   declared command actually produces.

6. **RT-16's record keeps a hybrid procedure, because it genuinely is one, and states it
   fully**: test-tree SHA `c34d23b`, swapped file `deploy/policies/omnipods_policies.py` from
   `6b52252` into the same path of a repo-shaped tree of the test SHA, the exact extraction
   command, the exact byte-identical four-node command, per-node expected failure reason, and
   an assertion that the output contains no `setUp`/import/loader/CLI-init/fixture error —
   the discriminator that distinguishes "failed at the property" from "failed in setUp",
   which is `staff` F-8's entire point. **Reproduction trap that must be recorded:** the
   current policy calls `_load_attested_controller()` at import, opening
   `parents[2]/bin/nightshift-ctl` with `O_NOFOLLOW`, so a symlinked or bare-directory swap
   fails with `ImportError: cannot open admission controller`. The `6b52252` policy has no
   such loader, so swapping *to* the ancestor works — but only inside a full repo-shaped tree.

7. **Leave `tests/test_admission_derivation.py` lines 296, 393 and 853 alone.** Measured
   under the F-1 mutant: all three still pass, i.e. they are F-1-insensitive (296's row is
   reaped to `state='reaped'` before the SELECT; 393 has no rows at all; 853's
   `expected_purpose` never matches). Rewriting them is churn on a frozen contract's test file
   for zero evidence gain. This overrides proposal B's reasoned recommendation to convert all
   four, on the strength of A's measurement.

8. **Line 286 shares line 339's defect but is out of scope here.** Under F-1 it fails with
   `'no active undispatched slot matches' not found in 'slot lost its one-use race'` — a
   prose-only kill. ADR-0069 scopes `SlotStateCoverage` out of RT-16's evidence, so it is not
   D06 evidence and is not bound. It is honest coverage that stays; a `tech-debt` row is filed
   rather than widening this change.

9. **Delete `unsatisfied_reason` from RT-16 and RT-17 on binding** (C2), matching every
   satisfied sibling. **This one decision is provisional and is product's to make**, not
   crew_alpha's: `docs/prd/**` is product-owned and #611's D06 changelog row instructs that the
   manifest "must carry the amended text when the bindings land". `qa`, `pm` and both proposers
   independently recommend deletion, because a satisfied entry carrying an `unsatisfied_reason`
   is dead, self-contradicting metadata. `eng_codex` proposed the compromise that best honours
   both: delete the false field and carry #611's amended RT-17 wording in a truthfully-named
   field or baseline note — which requires product's approval to add. **Escalated to the
   program; implementation proceeds on the other eight points and this field lands last.**

## Consequences

**Easy.** D06's flip is carried by evidence that is demonstrably fallible: every newly bound
node has a named mechanism that reddens it. The policy contract is untouched, so nothing
depends on an unverifiable assumption about the external omnigent engine. RT-17 becomes
self-reproducing for the first time.

**Hard.** RT-16 remains a hybrid capture and always will be — its regression evidence is only
meaningful against an ancestor policy module, so its record's honesty depends on prose that no
validator checks. The `O_NOFOLLOW` loader makes the swap fragile to how the tree is
constructed. Items 3–6 of the spec (#607, #608, F-8, F-5) are **not** mechanically enforced by
`dod_validate.py` — `pm` established that RT-16's mutation check is skipped for
`regression`-kind entries and that F-8/F-5 touch fields the validator never reads once bound.
They are held by discipline alone, which is precisely why they are written down here.

**Forecloses.** F-1 will never be evidence about duplicate dispatch (C6). Anyone who needs
that property must mutate the compare-and-set instead — and should know that `eng_claude`
measured the CAS predicate to be **unobservable at 8-way concurrency** (`BEGIN IMMEDIATE`
serializes writers: 1 allow / 7 denies / 1 admission row, with and without the CAS guard), so
the derivation `SELECT` is the load-bearing one-dispatch guard and the CAS is defence in
depth. A mutant on the CAS alone would survive, and that is a fact about the system, not a
gap in this evidence.

## Reversal trigger

Revisit if any of these becomes observable:
- The partial index `one_undispatched_dispatch_identity` stops being partial on
  `dispatched_at IS NULL`, or identity reuse after dispatch stops being sanctioned — the
  Option A scenario would lose its divergence and #607 would reopen.
- SQLite write serialization changes (e.g. WAL with concurrent writers) such that the CAS
  predicate becomes observable — the "defence in depth" claim above would need re-measuring.
- The external omnigent engine's result-shape validation becomes verifiable from this
  repository — Option B stops carrying unverifiable risk and becomes the cleaner answer.
- Any future sibling method is added to `RT16SlotStateCoverage` or
  `RT17ConcurrentAcquiresShareOneBudget` — the node lists are explicit precisely so that this
  is a deliberate act, and the captures must be re-run when it happens.

## Provenance

Both proposals were obtained independently, same brief, neither shown the other's output —
the cross-vendor debate ADR-0069 could not have (its §"Vendor availability" records
`eng_claude` failing to start twice; counted Claude seats are working again as of this date).
`qa` is a codex-native seat, so with `eng_claude` implementing, both `qa` and
`reviewer_codex` are cross-vendor to the implementer. `eng_claude`'s proposal was truncated in
transit at ~11k characters (the known sub-agent truncation risk); the recovered portion is
complete through §4, and the decisions taken from it above are all within the recovered text.
Sections lost to truncation were not relied upon.
