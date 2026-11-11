# Spec — 506-step2-manifest-binding: STEP 2 (D06 manifest wiring) as six backlog items

**Version 3 — 2026-09-02 (re-affirmation round, PR #624 @ `76f18e8`).** Reaffirms items 1–5 PASS
and item 6 PROVISIONAL at the new head; corrects every stale `ADR-0073` reference to `ADR-0074`
(filename-collision renumber, not a content change); records an independently-reproduced green
`dod_validate.py` D06 run. See §6.

**Version 2 — 2026-09-02 (acceptance round, PR #624 @ `4878fd9`).** Supersedes Version 1's
Finding B (partially false), item 1 criterion 1 (three nodes → four), item 2 criteria 2 and 6, item
3 criteria 2/3/4(a), item 5 criterion 3, and item 6's status (provisional, not settled). See §5 for
the full amendment record, written during acceptance sign-off against PR #624 / ADR-0073 (renamed
ADR-0074 as of `bd4f1a5` — see §6.1). Nothing
in §§1–4 below is edited in place — corrections are appended in §5 (and §6) so the record shows where each
mistake entered, per this crew's own sign-off discipline (a criteria change needs a visible new
version, not a silent rewrite).

Issue: #507. DoD condition: D06. Base SHA: `c34d23b93b7bb7e84f9919ccf9555ffa65114bd3` (= `origin/main`).
This spec decomposes the program's six binding conditions into six independently-dispatchable
backlog items (S/M each), runs Definition of Ready on each, and records the four assigned findings
(A–D) as directly re-verified evidence, not opinion. All verification below was run in a detached
worktree off base SHA; commands and outputs are quoted so anyone can reproduce them.

Related: `docs/specs/506-d06-rt16-rt17.md` (the prior task that built RT-16/RT-17 as real test
classes — that work is DONE; this spec is the follow-on STEP 2 manifest-wiring work plus three
review-filed gaps in the evidence it produced).

## 0. Ownership, confirmed by reading `docs/owners.md` at base SHA

- `docs/owners.md:15` — `bin/nightshift-ctl` → **crew_alpha (seam A)**.
- `docs/owners.md:16` — `deploy/policies/**` → **crew_alpha (seam A)**, explicit exception note:
  "the enforcement half of the kernel."
- `docs/owners.md:18` — "tests that drive `bin/nightshift-ctl`" → **crew_alpha (seam A)**,
  "ownership by what the test DRIVES, not by filename."
- `docs/owners.md:24` — `docs/prd/**` → **product**, edited "through its changelog, never
  edited silently." We hold **EDIT lease 122** on `docs/prd/DOD-MANIFEST.json` specifically (given
  in the charter); we do **not** hold a lease on `docs/prd/DEFINITION-OF-DONE.md` itself.
- `docs/owners.md:25` — `docs/decisions/**` → **the authoring crew for its own seam's decisions**.
  ADR-0069 was authored by crew_alpha (task `506-d06-rt16-rt17`) — ours to amend, subject to the
  standing rule in the same row: "every ADR still goes to `staff` before a crew builds on it."
- `docs/owners.md:26` — `docs/audit/**` → **the crew that captured the evidence**. Both
  `docs/audit/baseline/RT-16.json`/`.txt` and `RT-17.*` were captured by crew_alpha
  (ADR-0069, task `506-d06-rt16-rt17`) — ours.

So five of six items below touch only crew_alpha-owned files outright; only items 1, 2 and 6 touch
`docs/prd/DOD-MANIFEST.json`, where our authority is the granted EDIT lease, not blanket ownership.
No item needs a lease we don't already have.

## 1. Findings A–D — re-verified

### Finding A — condition 1's stated premise (ADR-0069 forbids list binding) is FALSE. Confirmed independently.

I read ADR-0069 in full at base SHA (`docs/decisions/ADR-0069-*.md`). Exact lines relied on:

- §2 Decision, point 2: *"RT-16 carries condition 6's mandatory regression baseline, at `6b52252`.
  **Its bound nodes are the two fixture-free bypass checks plus a positive control** that constructs
  a live slot using the current hermetic CLI and swaps only the policy module (Option D)."* — this
  is already a three-node, two-class binding, stated as the decision itself.
- Consequences, "Easy": *"RT-16's two bypass checks are immune to fixture drift, **while its
  positive control prevents an always-deny evaluator from satisfying the bound set**."* — the
  positive control (the second class, `RT16SlotStateCoverage`) is treated as part of the bound set,
  not excluded from it.
- The one real scope limit in §2 point 2: *"Other fixture-based coverage (consumed slot, TTL expiry,
  ambiguity, binding mismatch) remains unbound."* — this names the `SlotStateCoverage` sibling class
  (`tests/test_admission_derivation.py:271`, confirmed distinct from the bound
  `RT16SlotStateCoverage` at `:325` by `grep -n '^class ' tests/test_admission_derivation.py`) and
  the other unbound methods of `RT16DispatchRequiresLiveBoundUnconsumedSlot`
  (`test_already_consumed_slot_denies` etc., `:278-318`) as the boundary — never a prohibition on
  binding the two classes that already are captured.
- `docs/audit/baseline/RT-16.json`'s own `command` field, committed at base SHA, **already** runs
  all three nodes across both classes:
  `test_admission_derivation.RT16DispatchRequiresLiveBoundUnconsumedSlot.test_session_create_is_not_an_authorized_dispatch_form
  test_admission_derivation.RT16DispatchRequiresLiveBoundUnconsumedSlot.test_opaque_session_id_cannot_prove_the_target
  test_admission_derivation.RT16SlotStateCoverage.test_live_bound_slot_allows_once_and_records_dispatch`.

There is no sentence in ADR-0069 forbidding a list binding, and no sentence restricting RT-16 to one
class. **Item 1 below does not amend ADR-0069.** Editing a staff-reviewed ADR to fix a premise that
was never true would itself be a defect — see item 1's criteria.

### Finding B — condition 2 is a re-capture obligation, not a one-line manifest edit, and RT-17's own ADR needs the amendment condition 1 doesn't.

Confirmed: `tests/dod_validate.py:319` (`_validate_mutation`) — `if mut["command"] !=
baseline_command: return f"{label}: command is not byte-identical to the baseline command"`. RT-17
is a preservation record (`docs/audit/baseline/RT-17.json`, `"kind": "preservation"`) with two
mutants (M1, M2), each carrying its own `command` field that must byte-match `baseline.command`.
Adding `tests/test_authority_fence.py::SlotAcquireOncallTokenGate::test_denied_for_a_fenced_out_generation`
to `test_node_id` therefore forces: a new `baseline.command` string (three node ids instead of two),
a fresh green baseline capture under that string, and fresh M1/M2 captures under the same string —
three re-captures, not one manifest line.

Confirmed both classes exist at RT-17's `baseline_sha` (`f2abab84785412f7dabc8e05d870e55f189ce9c7`):

```
$ git cat-file -e f2abab84785412f7dabc8e05d870e55f189ce9c7:tests/test_authority_fence.py && echo EXISTS
EXISTS
$ git show f2abab84785412f7dabc8e05d870e55f189ce9c7:tests/test_authority_fence.py | grep -n "class SlotAcquireOncallTokenGate\|def test_denied_for_a_fenced_out_generation"
144:class SlotAcquireOncallTokenGate(AuthorityFixture):
162:    def test_denied_for_a_fenced_out_generation(self):
```

So the re-capture can stay pinned at the existing `baseline_sha` — no ancestor drift risk.

**Which mutant kills the new node — settled by direct execution, not inference.** I built two
scratch worktrees at HEAD (`c34d23b9`), applied `docs/audit/baseline/RT-17.mutant.patch` (M1) to one
and `RT-17.authority-mutant.patch` (M2) to the other, and ran all three candidate nodes together in
each:

```
# M1 (ceiling mutant: `granted = normal < max_w - reserve...` -> `normal < max_w...`)
test_concurrent_acquires_converge_on_one_normal_worker_ceiling ... FAIL  (7 != 5)
test_oncall_reserve_rejects_missing_and_wrong_authority_token ... ok
test_denied_for_a_fenced_out_generation ... ok
FAILED (failures=1)

# M2 (authority mutant: `if not _authority_token_matches(...)` -> `if False and not ...`)
test_concurrent_acquires_converge_on_one_normal_worker_ceiling ... ok
test_oncall_reserve_rejects_missing_and_wrong_authority_token ... FAIL  (0 != 3)
test_denied_for_a_fenced_out_generation ... FAIL  (0 != 3)
FAILED (failures=2)

# unmutated HEAD, all three
... ok / ok / ok  -- OK
```

**M2 already kills the new node.** No third mutant is needed — this settles the task's open
question by direct measurement. (Why: M2 disables the same `if not _authority_token_matches(...)`
branch at `bin/nightshift-ctl:1489` that gates *every* oncall acquire, current-generation or
fenced-out alike — `auth_row` is `None` for a fenced-out generation too, so the same bypassed check
covers both cases.)

**This appears to contradict two written records** — `docs/prd/DEFINITION-OF-DONE.md`'s 2026-09-02
D06 changelog row ("M2 kills only the missing/wrong-token refusal") and `crew_beta`'s PR #611 review
(same claim, `PRE_DEPLOYMENT` F-2). Reconciled, not actually a conflict: both statements describe
M2's effect against RT-17's **currently bound** two-node command, where the fence node isn't run at
all — trivially true, since M2 can't kill a test that isn't executed. Neither record makes a
predictive claim about what happens once the node is added; my measurement above is the first direct
test of that, and it resolves cleanly in item 2's favor (no third mutant to author).

**The ADR amendment condition 1 doesn't need is the one condition 2 does need.** ADR-0069 §5 states,
scrupulously: *"RT-17 proves both acceptance of the valid token and deterministic refusal of missing
and wrong tokens"* — silent on stale/fenced-out generations. Binding the fence node **extends** that
decision (a new node, a new demonstrated property, on the same production symbol). Per this crew's
own ADR discipline, that is an amendment, and it is item 2's, not item 1's.

### Finding C — condition 6 as literally stated is a no-op; this is a product decision, not ours to make unilaterally.

Confirmed: `tests/dod_validate.py:363-366` — `validate_rt_baseline` only reads `unsatisfied_reason`
inside the `if node is None:` branch. `tests/dod_validate.py:431` (inside `generic_rt_condition`) —
`return verdict_fail(f"{rt_id}: unsatisfied ({entry.get('unsatisfied_reason')})")`, same branch. The
moment `test_node_id` is non-null (which items 1 and 2 make true for RT-16 and RT-17 respectively),
`unsatisfied_reason` is never read by the validator again. Confirmed every already-satisfied sibling
in condition 6/7's neighborhood (`RT-15`, `RT-18`, `RT-19`, `RT-22`, `RT-32`, `RT-34`) carries **no**
`unsatisfied_reason` key at all once bound (verified directly against `docs/prd/DOD-MANIFEST.json`
during preparation of this spec — none of the bound entries printed have the key).

Also confirmed, exactly as the charter states: `docs/prd/DEFINITION-OF-DONE.md`'s 2026-09-02 D06
changelog row contains the sentence *"The manifest's `RT-17` `unsatisfied_reason` still quotes the
retired wording and must carry the amended text when the bindings land."* — a written product
instruction, on a product-owned file we don't hold a lease on.

Recommendation (not a unilateral decision — see item 6's DoR verdict): **delete the field**,
matching every satisfied sibling and the fact that it becomes genuinely unread dead data the instant
binding happens. Tradeoff in one paragraph: deleting is honest about what the validator actually
checks and avoids inventing a shape (`unsatisfied_reason` on a *satisfied* entry) that appears
nowhere else in the manifest and that nothing reads — but it means not following product's own
written 2026-09-02 instruction to the letter, which was written by the same crew that owns this
file and presumably knows it's dead code (the instruction may be there for **human-audit** value —
a reader scanning the manifest sees *why* RT-16/RT-17 were hard to bind — even though `dod_validate.py`
never looks at it again). This is genuinely product's call between "validator-truthful" and
"audit-readable," and item 6 below is written to stay dispatchable either way by defaulting to the
written instruction (rewrite, not delete) while flagging the tension for product to revisit — see
item 6's Open Questions and the `.omni/decisions.md` entry filed alongside this spec.

### Finding D — condition 3's real acceptance test is a double proof; confirmed both directions were the actual mechanism at play in #607.

Confirmed via `gh issue view 607` (full body): the fragility is real and precisely as filed — the
positive control's second-call denial reason string is checked with `assertIn`, but under the 3-site
`dispatched_at IS NULL` mutant (`deploy/policies/omnipods_policies.py:288,320,449`), the second call
is *still* denied — just via a different code path (the NEW-phase precondition,
`omnipods_policies.py:359-374`, since `task["state"]` has moved off `"pending"` by then) — and
`dispatched_at` is still written on the first call regardless. So `result == "DENY"` is unaffected by
the mutant; only the reason-string substring distinguishes real one-use enforcement from this
coincidental alternate denial. Item 3's criteria require, exactly as the charter's finding D says:
**(a)** reword the production reason string and the fixed test must still pass (no prose coupling),
and **(b)** re-apply the 3-site mutant and the fixed test must still fail (the kill survives). Neither
alone is sufficient and the criteria say so explicitly.

## 2. Backlog items

Each item below is written as it would appear as a GitHub issue body (title, outcome, numbered
acceptance criteria, failure/empty states, out of scope, dependencies, open questions, DoR verdict).
Files touched are named per item so the shared-file sequencing in §3 is checkable.

---

### Item 1 — Bind RT-16's two classes (three nodes) into `docs/prd/DOD-MANIFEST.json`

**Files touched:** `docs/prd/DOD-MANIFEST.json` only.

**Outcome:** `docs/prd/DOD-MANIFEST.json`'s `rt_ids.RT-16` entry has a non-null `test_node_id`
resolving to the same three nodes already captured and green, and `python3 tests/dod_validate.py`'s
`generic_rt_condition(6)` no longer fails on `RT-16: unsatisfied`.

**Acceptance criteria:**

1. `rt_ids.RT-16.test_node_id` is set to the list
   `["test_admission_derivation.RT16DispatchRequiresLiveBoundUnconsumedSlot.test_session_create_is_not_an_authorized_dispatch_form",
   "test_admission_derivation.RT16DispatchRequiresLiveBoundUnconsumedSlot.test_opaque_session_id_cannot_prove_the_target",
   "test_admission_derivation.RT16SlotStateCoverage.test_live_bound_slot_allows_once_and_records_dispatch"]`
   — the exact three nodes already in `docs/audit/baseline/RT-16.json`'s `command`, in that order.
2. `rt_ids.RT-16.cwd` is set to `"tests"`, matching every other bound RT id's convention
   (`RT-18`/`RT-19`/`RT-22`/`RT-32`/`RT-34`).
3. `rt_ids.RT-16.binding` is set to `{"file": "deploy/policies/omnipods_policies.py", "symbol":
   "worker_semaphore"}`, copied from `docs/audit/baseline/RT-16.json`'s `production` field.
4. `rt_ids.RT-16.baseline` is set from `docs/audit/baseline/RT-16.json` verbatim: `baseline_sha`,
   `command` (the same three-node string as criterion 1, `cd tests && python3 -m unittest -v ...`),
   `exit_code: 1`, `kind: "regression"`, `output_path: "docs/audit/baseline/RT-16.txt"`. If item 4
   (below) has already landed and `docs/audit/baseline/RT-16.json` carries a `mutations` array at
   binding time, copy that array into the manifest's `baseline.mutations` too, for parity with how
   `RT-18`/`RT-19` are recorded — not required by `_validate_mutation` for a `"regression"`-kind
   entry (`tests/dod_validate.py`'s mutation check only fires when `derived_kind == "preservation"`),
   but avoids the exact gap #608 was filed to close (evidence that exists only in a crew-owned side
   file, absent from the record product's validator reads first).
5. `python3 tests/dod_validate.py` re-run after this change: `RT-16` no longer appears in any FAIL
   line naming it.
6. **`docs/decisions/ADR-0069-*.md` is not edited by this item.** Per Finding A, its Decision and
   Consequences sections already describe and endorse exactly this three-node, two-class binding;
   editing a staff-reviewed ADR to correct a premise that was never true would itself be a defect.
7. No other `rt_ids` entry, no `conditions` entry, and no file outside
   `docs/prd/DOD-MANIFEST.json` changes.

**Failure / empty states:**

- If the three live-run node ids do not match `docs/audit/baseline/RT-16.json`'s `command` exactly
  (e.g. a typo, wrong module path), `generic_rt_condition(6)`'s call to `_test_run_green` will report
  `load_errors` or `testsRun == 0` — treat either as a hard failure of this item, not a partial pass.
- If `docs/audit/baseline/RT-16.txt`'s recorded `exit_code` (1) doesn't match what
  `validate_rt_baseline` derives (`"regression"`) against a `declared kind` mismatch, the validator
  fails with an explicit "declared kind disagrees with derived kind" message — this item's `kind`
  field must be `"regression"`, not `"preservation"`.

**Out of scope:** Editing ADR-0069 (Finding A). Adding any node beyond the three already captured
(that is item 2's or a future item's job for RT-17; `SlotStateCoverage`'s other methods stay unbound
per ADR-0069 §2's explicit scope line). Resolving `unsatisfied_reason` (item 6).

**Dependencies:** None blocking a start. Recommended-but-not-required to land after item 4 (RT-16
mutation record) so this item's copy-in of `docs/audit/baseline/RT-16.json` picks up the mutations
array in one pass rather than two (see criterion 4) — see §3 for the full ordering discussion.
Shares `docs/prd/DOD-MANIFEST.json` with items 2 and 6: per the "shared file is always sequential"
rule, those three items may not be worked in parallel worktrees against that file.

**Open questions:** None outstanding — Finding A resolves the only real one (does this need an ADR
amendment: no).

**DoR verdict: PASS.** All seven hold: outcome is observable (validator line disappears, specific
manifest fields set to named values); criteria are numbered and each checkable against a file or a
command's exit behavior; failure states named; out of scope named explicitly, including the
ADR-non-edit; dependencies named (file-sharing sequencing, one soft recommendation); no open
questions; size S (mechanical field-copy from an already-captured, already-green record).

---

### Item 2 — Bind the fenced-out-generation node into RT-17; re-capture baseline + both mutants; amend ADR-0069 §5

**Files touched:** `docs/prd/DOD-MANIFEST.json`, `docs/audit/baseline/RT-17.json`,
`docs/audit/baseline/RT-17.txt`, `docs/audit/baseline/RT-17.mutation.txt`,
`docs/audit/baseline/RT-17.authority-mutation.txt`, `docs/decisions/ADR-0069-*.md`. (The two
`.patch` files themselves are unchanged — same mutants, same target lines — only the `command` each
runs and the resulting `.txt` captures change.)

**Outcome:** `docs/prd/DOD-MANIFEST.json`'s `rt_ids.RT-17.test_node_id` includes
`tests/test_authority_fence.py::SlotAcquireOncallTokenGate::test_denied_for_a_fenced_out_generation`
alongside its existing two nodes, with a baseline and both mutation records re-captured under the
new byte-identical three-node command, and ADR-0069 records this as an amendment to §5's scope
rather than silently widening what was previously decided.

**Acceptance criteria:**

1. `rt_ids.RT-17.test_node_id` becomes the three-element list: the existing
   `test_slots.RT17ConcurrentAcquiresShareOneBudget.test_concurrent_acquires_converge_on_one_normal_worker_ceiling`,
   the existing
   `test_slots.RT17ConcurrentAcquiresShareOneBudget.test_oncall_reserve_rejects_missing_and_wrong_authority_token`,
   plus
   `test_authority_fence.SlotAcquireOncallTokenGate.test_denied_for_a_fenced_out_generation`.
2. `docs/audit/baseline/RT-17.json`'s `baseline.command` is updated to the byte-identical three-node
   `python3 -m unittest -v` invocation (same node order as criterion 1), staying pinned at the
   existing `baseline_sha` `f2abab84785412f7dabc8e05d870e55f189ce9c7` (Finding B: both classes exist
   there, no ancestor drift). `baseline.exit_code` stays `0`, `kind` stays `"preservation"`.
   `docs/audit/baseline/RT-17.txt` is regenerated by actually running the new command and is
   non-empty, showing 3 tests, `OK`.
3. Both `mutations[].command` fields (M1 and M2) are updated to the **exact same** byte-identical
   three-node string as criterion 2 (`_validate_mutation`, `tests/dod_validate.py:319`, requires
   byte-identity to `baseline.command`). Neither mutant `.patch` file itself changes.
4. `docs/audit/baseline/RT-17.mutation.txt` (M1) is regenerated by re-running M1 under the new
   command: expected result is 1 failure (the ceiling clause, `7 != 5` at hermetic 7/2), the other
   two nodes (missing/wrong-token refusal, fenced-out-generation refusal) passing — reproducing this
   spec's Finding B measurement.
5. `docs/audit/baseline/RT-17.authority-mutation.txt` (M2) is regenerated by re-running M2 under the
   new command: expected result is 2 failures (missing/wrong-token refusal AND fenced-out-generation
   refusal, both `0 != 3`) — reproducing this spec's Finding B measurement. **No third mutant is
   authored** — Finding B's direct execution already shows M2 kills the new node.
6. `docs/decisions/ADR-0069-*.md` gets a dated addendum (2026-09-02 or later, whenever this item
   lands) stating: the fenced-out-generation node is now bound to RT-17; §5's sentence "RT-17 proves
   both acceptance of the valid token and deterministic refusal of missing and wrong tokens" is
   superseded to add "and refusal of a stale/fenced-out-generation token, via the same
   `_authority_token_matches` gate M2 already kills"; and an explicit statement that this does not
   reopen or contradict the original Decision (RT-16 unaffected; RT-17's M1/clause-1 evidence
   unaffected). This addendum goes to `staff` per the standing "every ADR still goes to staff before
   a crew builds on it" rule (`docs/owners.md:25`) before item 2 is considered mergeable.
7. `rt_ids.RT-17.baseline` in `docs/prd/DOD-MANIFEST.json` is updated to mirror the re-captured
   `docs/audit/baseline/RT-17.json` exactly (all of: `command`, both mutation `command`/`exit_code`/
   `output_path` entries).
8. `python3 tests/dod_validate.py` re-run after this change: `RT-17` no longer appears in any FAIL
   line naming it, and the manifest's mutation-validation branch (`_validate_mutation`, invoked
   because `kind == "preservation"`) passes for both M1 and M2 under the new command.

**Failure / empty states:**

- If the re-capture's M2 run does **not** show 2 failures (i.e. contradicts Finding B's direct
  measurement above), this item is blocked — stop and re-verify against `bin/nightshift-ctl:1487-1489`
  rather than assume the earlier measurement was environment-specific.
- If the ADR addendum is not staff-gated before merge, treat the PR as not mergeable per the standing
  rule — this is not optional documentation, it is the discipline that makes crew-authored ADRs safe
  to build on (`docs/owners.md:25`).

**Out of scope:** Authoring a third mutant (Finding B settles this: not needed). Touching RT-16's
manifest entry or files (item 1). Resolving `unsatisfied_reason` (item 6). Any change to
`bin/nightshift-ctl` or `tests/test_authority_fence.py`/`tests/test_slots.py` production or test
bodies — this item is capture + manifest + ADR only.

**Dependencies:** Shares `docs/prd/DOD-MANIFEST.json` with items 1 and 6 — sequential, not parallel,
against that file (see §3). No dependency on items 3/4/5 (disjoint files: RT-16's test file and
audit records vs. RT-17's). ADR-0069 addendum requires a `staff` gate before merge (own dependency,
external to this crew's DoR mechanics but real).

**Open questions:** None outstanding — Finding B settles both "does this force a re-capture" (yes,
all three artifacts) and "does RT-17 need a third mutant" (no, M2 already kills the new node,
measured directly).

**DoR verdict: PASS.** Outcome observable (specific node id added, specific files re-captured,
specific ADR addendum). Eight numbered criteria, each checkable against a file diff or a command's
captured output. Failure states named. Out of scope named. Dependencies named, including the
external staff gate. No open questions. Size M (three re-captures plus one ADR addendum plus a
manifest mirror-update — more than item 1, but no decomposition needed: every sub-step is mechanical
once Finding B's measurement is in hand).

---

### Item 3 — #607: make RT-16's positive-control kill structural, not prose-dependent

**Files touched:** `tests/test_admission_derivation.py` only (the body of
`RT16SlotStateCoverage.test_live_bound_slot_allows_once_and_records_dispatch`, currently at
`tests/test_admission_derivation.py:332-345`).

**Outcome:** The F-1 kill (3-site `dispatched_at IS NULL` removal at
`deploy/policies/omnipods_policies.py:288,320,449`) is detected by a structural assertion that does
not depend on the wording of any denial-reason string.

**Acceptance criteria:**

1. The test retains its existing structural assertions unchanged: `first["result"] == "ALLOW"`,
   `second["result"] == "DENY"`, `slot["dispatched_at"] is not None`, `slot["bound_agent"] ==
   "eng_claude"`, `slot["bound_title"] == "ship-T"`.
2. A new, reason-string-independent assertion is added: after the second (denied) call, the test
   directly re-runs the same identity-tuple lookup the production code uses
   (`state='active' AND dispatched_at IS NULL AND kind=? AND expected_agent=? AND expected_title=?
   AND expected_purpose=?`, matching `deploy/policies/omnipods_policies.py:286-292`) against the
   fixture DB and asserts it returns **zero rows** — proving the consumed slot can no longer be
   derived/re-matched, independent of what reason string the policy happened to raise (#607's own
   proposed fix, quoted verbatim in the issue body).
3. The existing `self.assertIn("no active undispatched slot matches", second["reason"])` line is
   **removed or downgraded to non-load-bearing** (e.g. kept as an informational check the test does
   not fail on) — the kill must not still depend on it per criterion 4 below.
4. **Double-proof, both directions required (Finding D):**
   (a) With the production reason string at `deploy/policies/omnipods_policies.py:294` reworded to
   different text (any wording change, behavior unchanged), the updated test still passes — proving
   no prose dependency remains.
   (b) With the existing 3-site `dispatched_at IS NULL` mutant re-applied
   (`deploy/policies/omnipods_policies.py:288,320,449`, the same mutant #608 references), the updated
   test still fails — proving the kill survives. Both (a) and (b) must be demonstrated in the PR
   description with actual command output, not asserted.
5. `python3 -m unittest -v test_admission_derivation.RT16SlotStateCoverage.test_live_bound_slot_allows_once_and_records_dispatch`
   (`cwd: tests`) is green at HEAD after this change, zero skips.
6. No other test method in `tests/test_admission_derivation.py` changes.

**Failure / empty states:**

- If criterion 4(a) fails (the reworded-string test also fails), the new assertion still has a
  hidden prose dependency — not acceptable, criterion 2 must be revised.
- If criterion 4(b) fails (the mutant no longer causes a failure), the kill was lost, not fixed —
  not acceptable regardless of how clean the new assertion reads.

**Out of scope:** Touching `deploy/policies/omnipods_policies.py` itself (the reword in criterion
4(a) is a throwaway verification step, never committed). Touching any other RT-16 test method.
Committing the mutation record (#608, item 4) or the swap-procedure note (staff F-8, item 5) — this
item is the test-file fix only.

**Dependencies:** None blocking a start. Item 4 depends on this item landing first (the mutation
record's evidence should reflect the fixed, structural assertion — see §3). Shares
`tests/test_admission_derivation.py` and downstream `docs/audit/baseline/RT-16.*` with items 4 and 5
— sequential against those files.

**Open questions:** None.

**DoR verdict: PASS.** Outcome is observable (a specific assertion in a specific test method,
double-proved by two named, reproducible commands). Six numbered criteria, each checkable. Failure
states named for both halves of the double proof. Out of scope named. Dependencies named (item 4
depends on this). No open questions. Size S — one test method, one new assertion.

---

### Item 4 — #608: commit RT-16's mutation record

**Files touched:** `docs/audit/baseline/RT-16.mutant.patch` (new), `docs/audit/baseline/RT-16.mutation.txt`
(new), `docs/audit/baseline/RT-16.json` (add `mutations` array).

**Outcome:** RT-16 carries a committed mutant patch and kill capture in the same shape RT-17 and
RT-18 already use, closing the asymmetry #608 names (RT-17 ships two proofs, RT-16 shipped none).

**Acceptance criteria:**

1. `docs/audit/baseline/RT-16.mutant.patch` is committed: the 3-site `dispatched_at IS NULL` removal
   at `deploy/policies/omnipods_policies.py:288,320,449` (the derivation SELECT, the oncall CAS, the
   generation CAS — exactly the reproduction #608's own body specifies), applied against HEAD (or
   whatever `applied_to_sha` this item's implementer pins, provided it's an ancestor of HEAD per
   `tests/dod_validate.py`'s `merge-base --is-ancestor` check).
2. `docs/audit/baseline/RT-16.mutation.txt` is committed: non-empty, captured by actually re-running
   RT-16's bound three-node command (item 1's criterion 1 list) against the mutated tree — expected
   `FAILED (failures=1)` per #608's own reproduction note, using item 3's now-structural assertion
   (this item lands after item 3 — see §3), so the captured failure demonstrates the real kill, not
   the fragile prose one.
3. `docs/audit/baseline/RT-16.json` gains a `mutations` array, one entry, matching the shape RT-17
   and RT-18 already use: `id`, `target_path`, `patch_path`, `mutation` (description), `command`
   (byte-identical to `RT-16.json`'s own `baseline.command`), `exit_code` (non-zero), `output_path`,
   `applied_to_sha`, `committed_to_branch: false`.
4. The patch's touched-files set is exactly `{"deploy/policies/omnipods_policies.py"}` — matching
   RT-16's declared `binding.file`, so `_validate_mutation`'s subset check
   (`tests/dod_validate.py:344-345`) would pass if this were ever validated (see criterion 5 note).
5. **Documented, not silently skipped:** because RT-16 is a `"regression"`-kind record,
   `tests/dod_validate.py`'s `validate_rt_baseline` only invokes `_validate_mutation` when
   `derived_kind == "preservation"` (`tests/dod_validate.py:397-403`) — so this mutation record is
   **not mechanically checked** by the validator and does not gate D06's flip. State this plainly in
   the PR description so nobody claims false validator coverage for it. It is committed because
   #608 asked for it as audit evidence, matching RT-17/RT-18's evidentiary bar, not because
   `dod_validate.py` requires it for `RT-16`.
6. Unmutated control: re-running the same three-node command against the unmutated tree still shows
   `OK`, 3 tests, zero failures (sanity check that the mutant, not something else, causes criterion
   2's failure).

**Failure / empty states:**

- If the mutant produces more than 1 failure or fewer, or an error rather than a clean failure, the
  capture in criterion 2 must record whatever actually happened — never edited to match an assumed
  `failures=1`.

**Out of scope:** Touching `tests/test_admission_derivation.py` (item 3's job). Touching
`docs/prd/DOD-MANIFEST.json` (item 1's job, though item 1 may copy this item's `mutations` array in —
see item 1 criterion 4).

**Dependencies:** Depends on item 3 (the mutation capture should reflect the fixed structural
assertion, not the prose-fragile one — landing this first would mean re-capturing again once item 3
lands, wasted work). Shares `docs/audit/baseline/RT-16.json` with item 5 — sequential.

**Open questions:** None.

**DoR verdict: PASS.** Outcome observable (specific files exist with specific, checkable shapes).
Six numbered criteria. Failure state named. Out of scope named. Dependency on item 3 named and
justified. No open questions. Size S — one mutant already known (from #608's own reproduction),
capture is mechanical.

---

### Item 5 — staff F-8: document RT-16's hybrid-tree capture composition and swap procedure

**Files touched:** `docs/audit/baseline/RT-16.json` only (adds fields/notes; no existing field's
*meaning* changes).

**Outcome:** `docs/audit/baseline/RT-16.json` states, in the record itself, that its capture was
produced in a hybrid tree (current tests + current CLI + the ancestor policy module), which file was
swapped, from which SHA, and the exact command — so a reader (or `dod_validate.py`, if this is ever
made machine-checked) can tell a genuine ancestor regression apart from a fixture/harness-mismatch
failure at the same exit code, which staff's F-8 finding showed are otherwise indistinguishable from
the recorded fields alone.

**Acceptance criteria:**

1. `docs/audit/baseline/RT-16.json` gains a field (e.g. `capture_composition`, exact key name at
   implementer's discretion) stating explicitly: test file (`tests/test_admission_derivation.py`) at
   HEAD/current tree; CLI (`bin/nightshift-ctl`) at HEAD/current tree; policy module
   (`deploy/policies/omnipods_policies.py`) swapped in from ancestor
   `6b52252ff8c4975ffc30a59b3142ae6b86a90875`.
2. The same field states the **exact command** used to assemble the hybrid tree and run the capture,
   reproducible by a fresh reader, in the shape: check out the current tree, then
   `git show 6b52252ff8c4975ffc30a59b3142ae6b86a90875:deploy/policies/omnipods_policies.py >
   deploy/policies/omnipods_policies.py`, then run the (item 1/3/4-updated) bound-node command from
   `tests/` — matching the procedure ADR-0069 Option D already describes in prose
   (`docs/decisions/ADR-0069-*.md`, Decision §2's table and surrounding text) but which the JSON
   record itself does not currently carry.
3. **Staff's reproduction is named and its outcome distinguished from ours, not silently
   contradicted:** the note states that running `baseline_sha` + `command` alone (no swap, i.e. a
   *pure* `6b52252` tree) reproduces a superficially identical exit code (1, `failures=3`) but for a
   different reason — the third failure occurring in `setUp` (`AssertionError: 2 != 0`, the ancestor
   CLI rejecting today's `argv` shape) rather than at the recorded property assertion — and that this
   is why the swap procedure matters: a validator or reviewer re-running the bare command pair alone
   cannot tell the two apart without this note.
4. This item is landed **after** item 4 (§3), so the composition note describes the final, post-3,
   post-4 capture state of `docs/audit/baseline/RT-16.json` rather than an intermediate one requiring
   a second edit.
5. No field's prior *meaning* changes — `baseline_sha`, `command`, `exit_code`, `kind`,
   `output_path` are unchanged in value; this item only adds explanatory provenance.

**Failure / empty states:** N/A — this is a documentation-only addition to an existing record; there
is no runtime failure mode. The only "failure" is an inaccurate or non-reproducible swap-procedure
description, which criterion 2's reproducibility requirement is meant to prevent.

**Out of scope:** Re-running the pure-ancestor reproduction as new committed evidence (staff already
did this per the finding; this item documents the swap, not staff's separate negative-control run).
Any change to `deploy/policies/omnipods_policies.py`, `bin/nightshift-ctl`, or any test file.

**Dependencies:** Depends on item 4 (same file, and the note should describe the final capture — see
criterion 4). Transitively depends on item 3.

**Open questions:** None — the exact swap procedure is fully specified by ADR-0069's own Option D
description; this item just moves it into the machine-readable record staff's finding says is
missing it.

**DoR verdict: PASS.** Outcome observable (specific field, specific content, in a specific file).
Five numbered criteria. Failure state addressed (documentation-only, explained). Out of scope named.
Dependency on item 4 named. No open questions. Size S — one explanatory field, content already fully
determined by ADR-0069's existing prose.

---

### Item 6 — staff F-5: resolve `unsatisfied_reason` on RT-16 and RT-17 once bound

**Files touched:** `docs/prd/DOD-MANIFEST.json` only.

**Outcome:** `docs/prd/DOD-MANIFEST.json`'s `rt_ids.RT-16` and `rt_ids.RT-17` entries no longer carry
a stale `unsatisfied_reason` once bound (items 1 and 2), resolved per product's existing written
instruction pending product's own reconsideration.

**Acceptance criteria:**

1. **Default (dispatchable) resolution — follow the existing written instruction:**
   `rt_ids.RT-17.unsatisfied_reason` is rewritten to quote the amended id text landed by PR #611,
   i.e. the `docs/prd/DEFINITION-OF-DONE.md` condition-6 *Required tests* clause for RT-17 verbatim:
   *"independently produced concurrent acquires converge on one atomic budget: the number granted at
   once never exceeds `max_concurrent_workers − reserve_for_oncall`, no normal acquire reaches the
   reserve even at saturation, and the reserve is reachable only by an on-call acquire presenting a
   live, current-generation Program authority token."*
2. `rt_ids.RT-16.unsatisfied_reason` is rewritten analogously, to the condition-6 *Required tests*
   clause for RT-16 verbatim: *"a program-level worker dispatch without a live, bound, unconsumed
   slot is denied."* (RT-16's own id text is unamended per ADR-0069, so this is the same text the
   field already substantially carries today, minus the now-false "no test class or capture exists
   in the tree yet" trailing clause.)
3. **Flagged for product, non-blocking on this item's dispatch:** a paragraph is added to this
   item's PR description (not to the manifest itself) stating Finding C's technical objection —
   that both fields become genuinely unread by `tests/dod_validate.py` the instant `test_node_id` is
   non-null (`tests/dod_validate.py:363-366`, `:431`), that every other already-bound sibling in
   conditions 6/7 carries no such field at all, and that deletion (matching siblings) was crew_alpha's
   engineering recommendation — recorded in `.omni/decisions.md` alongside this spec for product to
   revisit at its discretion. This item does **not** wait on that reconsideration; it implements the
   already-written instruction now.
4. `python3 tests/dod_validate.py` re-run after this change: no FAIL line's message differs in
   observable behavior because of this item (the field is provably unread once bound — criterion 3's
   own citation) — this item changes only what a human reader sees when scanning the manifest, never
   validator output.

**Failure / empty states:** N/A — same reasoning as item 5; this is a text-content change to a field
the validator does not read once the entries are bound.

**Out of scope:** Deciding between rewrite-vs-delete unilaterally (Finding C) — this item follows the
existing written instruction and surfaces the tension for product, it does not resolve it. Any change
to `docs/prd/DEFINITION-OF-DONE.md` itself.

**Dependencies:** Depends on items 1 and 2 (must know the final bound state and the final amended id
text — item 2's ADR addendum and the underlying `DEFINITION-OF-DONE.md` text it's copying from —
before writing these fields). Shares `docs/prd/DOD-MANIFEST.json` with items 1 and 2 — last in that
file's sequential chain (see §3).

**Open questions:** **Rewrite vs. delete `unsatisfied_reason` once bound** — resolved non-blocking:
default to the existing written instruction (rewrite, criteria 1-2) for this item's own dispatch;
crew_alpha's recommendation (delete, matching every other bound sibling) is logged to
`.omni/decisions.md` for product to revisit at its own discretion, not blocking here.

**DoR verdict: PASS.** Outcome observable (two specific field values, or their absence, depending on
product's eventual call — default path fully specified). Four numbered criteria. Failure states
addressed. Out of scope named, including the explicit non-decision on rewrite-vs-delete. Dependencies
named (items 1, 2). Open question named and explicitly marked non-blocking, per the Definition of
Ready's own allowance for that. Size S.

## 3. Recommended dispatch order

Two independent chains, forced by file-sharing (never parallel worktrees on the same file) plus one
real evidentiary dependency inside each:

- **Chain A (RT-16 evidence hygiene):** item 3 → item 4 → item 5. All three touch
  `tests/test_admission_derivation.py` and/or `docs/audit/baseline/RT-16.*`; item 4's captured kill
  should reflect item 3's fixed assertion, and item 5's swap-procedure note should describe item 4's
  final capture state.
- **Chain B (manifest wiring):** item 1 → item 2 → item 6. All three touch
  `docs/prd/DOD-MANIFEST.json`; item 6 needs both prior items' final bound state to know what text to
  write.

Chains A and B are file-disjoint and may run in parallel worktrees. Within chain B, item 1 is
recommended (not required) to land after chain A completes, so its one-time copy of
`docs/audit/baseline/RT-16.json` (criterion 4) picks up item 4's `mutations` array without a second
edit — pure efficiency, not a correctness dependency (a `"regression"`-kind manifest entry is valid
with or without a `mutations` array, per `tests/dod_validate.py:397`'s `if derived_kind ==
"preservation":` gate).

No item here is `size:L`; none needs further decomposition.

## 4. Does D06 legitimately flip once all six land?

Yes, mechanically: `tests/dod_validate.py:1368` — `Check("D06", 6, generic_rt_condition(6),
break_generic(6))` — and `generic_rt_condition` (`tests/dod_validate.py:415-436`) requires, for every
`rt_ids` entry with `condition == 6`: a structurally valid baseline record (item 1, item 2), the live
test node(s) green with zero skips (already true today for all five nodes — verified directly in
Finding B's measurements above, and item 3 keeps RT-16's positive control green throughout its
rewrite per its own criterion 5), and not every satisfied id being `"preservation"`-kind (already
true: RT-16 is `"regression"`, unaffected by any item here). Items 4, 5 and 6 do not gate
`generic_rt_condition(6)` at all (item 4's mutation array is unchecked for a regression-kind entry
per its own criterion 5; items 5 and 6 touch fields the validator never reads once bound) — they are
owed as audit/evidence-integrity closures (#607, #608, staff F-8, staff F-5), not as D06
preconditions. So the flip is earned by items 1 and 2 alone; items 3-6 make the evidence underneath
that flip honest rather than merely present, closing the exact "evidence that cannot fail, relocated"
risk this charter opened with. I have not re-run `tests/dod_validate.py` end-to-end after a real
implementation of all six (nothing is implemented yet — this is a spec), so this is a structural
argument from reading the validator's own logic, not a claim to have observed the green run.

## 5. Version 2 — acceptance-round amendments (PR #624 @ `4878fd9`, ADR-0073)

Written during acceptance sign-off, PM pass, against the frozen review contract
`.omni/review-contracts/506-step2-manifest-binding-4878fd9.md`. Every correction below was
independently re-run by me, not taken from the engineer's PR description or ADR-0073's prose —
commands and results are quoted. Where my measurement matches the engineer's exactly, I say so;
none of the corrections below rest on the engineer's word alone.

### 5.1 Finding B was partially wrong — the mistake is mine, named plainly

**Version 1 claimed:** *"Confirmed both classes exist at RT-17's `baseline_sha`
(`f2abab84785412f7dabc8e05d870e55f189ce9c7`)"* — supported by one quoted command, against
`tests/test_authority_fence.py` only.

**That claim is false for the other class.** Independently re-run:

```
$ git show f2abab84785412f7dabc8e05d870e55f189ce9c7:tests/test_slots.py | grep -c "RT17ConcurrentAcquiresShareOneBudget"
0
```

`tests/test_slots.py::RT17ConcurrentAcquiresShareOneBudget` — the class carrying RT-17's *other two*
already-bound nodes — does not exist at `f2abab8` at all (renamed in PR #585 round 3). I ran the
declared baseline command at the declared SHA myself and reproduced the engineer's exact result:

```
$ cd tests && python3 -m unittest -v test_slots.RT17ConcurrentAcquiresShareOneBudget.test_concurrent_acquires_converge_on_one_normal_worker_ceiling test_slots.RT17ConcurrentAcquiresShareOneBudget.test_oncall_reserve_rejects_missing_and_wrong_authority_token
AttributeError: module 'test_slots' has no attribute 'RT17ConcurrentAcquiresShareOneBudget'  (x2)
FAILED (errors=2)
```

So the *committed, pre-existing* `RT-17.json` (before this PR) declared `baseline_sha: f2abab8`,
`exit_code: 0` for a command that, run at that SHA, actually errors. That is false evidence, sitting
in the tree since ADR-0069, undetected until this PR's re-verification (ADR-0073 §C4) — and Version
1 of this spec repeated the error by generalizing from a check of one file to a claim about both.

**Where the mistake entered:** I ran and quoted a command against `tests/test_authority_fence.py`
only, confirmed `SlotAcquireOncallTokenGate` exists at `f2abab8`, and wrote "both classes exist
there" without running the equivalent check against `tests/test_slots.py`. The fence class's
existence is true; the generalization to "both" is not, and nothing in Finding B as written
actually verifies the second class. This is exactly the gap the engineer's PR description names,
and their measurement is correct.

**Amendment — item 2 criterion 2, corrected:** `docs/audit/baseline/RT-17.json`'s `baseline_sha`
moves `f2abab8` → `c34d23b93b7bb7e84f9919ccf9555ffa65114bd3` (the task's own base SHA, where both
classes are confirmed to exist and the extended three-node command runs green), and both mutants'
`applied_to_sha` move with it. This is **not** ancestor drift in the sense Finding B worried about —
it corrects a record that was already wrong, rather than introducing new risk. PR #624 implements
this correctly; the original criterion (pin at `f2abab8`) is superseded and should not be re-applied
by any future rework of this item.

### 5.2 Item 5 criterion 3's predicted negative-control failure mode was wrong

**Version 1 predicted:** running the bare `(baseline_sha, command)` pair on a *pure* `6b52252`
checkout reproduces `exit 1, failures=3`, with the third failure inside `setUp`
(`AssertionError: 2 != 0`, the ancestor CLI rejecting today's `argv` shape).

**Independently re-measured, not taken from the PR:**

```
$ git cat-file -e 6b52252ff8c4975ffc30a59b3142ae6b86a90875:tests/test_admission_derivation.py
fatal: ... exists on disk, but not in '6b52252ff8c4975ffc30a59b3142ae6b86a90875'

$ git worktree add --detach /tmp/verify624-6b52252 6b52252ff8c4975ffc30a59b3142ae6b86a90875
$ cd tests && python3 -m unittest -v <RT-16's four bound nodes>
ModuleNotFoundError: No module named 'test_admission_derivation'  (x4)
Ran 4 tests in 0.000s
FAILED (errors=4)
```

`tests/test_admission_derivation.py` does not exist at `6b52252` at all — it postdates that SHA —
so no test body runs and the file cannot even be imported. This exactly matches what the engineer
recorded in `RT-16.json`'s `capture.negative_control.measured` field and their PR description, word
for word on the mechanism (`ModuleNotFoundError`, `errors=4` not `failures=3`). My original
prediction assumed the file existed at the ancestor with a different, weaker setUp fixture; it does
not exist there at all, which is a *stronger* discriminator than I predicted (`failures=0/errors=4`
against the real hybrid capture's `failures=4/errors=0` — an even cleaner FAILURE-vs-ERROR split
than the scenario I wrote).

**Amendment — item 5 criterion 3, corrected:** replace the predicted text with: *"the note states
that running `baseline_sha` + `command` alone (no swap, i.e. a pure `6b52252` tree) reproduces the
same exit code (1) via `FAILED (errors=4)`, `ModuleNotFoundError: No module named
'test_admission_derivation'` — the test file postdates `6b52252` and cannot be imported at all, so
zero test bodies run — and that this is why the swap procedure matters: a reviewer re-running the
bare command pair alone cannot tell 'genuine regression' (failures>0, errors=0) apart from 'nothing
ran' (failures=0, errors>0) without this note."* PR #624's `RT-16.json.capture.negative_control`
implements exactly this, including the FAILURE-vs-ERROR discriminator language, and additionally
notes the discrepancy against this spec by name rather than silently overwriting it — that
transparency is itself worth recording as good practice.

### 5.3 Item 3 — accepted as met-differently-than-specified; criteria 2/3/4(a) superseded

Version 1 specified rewriting the *existing* `test_live_bound_slot_allows_once_and_records_dispatch`
in place: add a re-derivation-query assertion inside that method, and remove or downgrade its
`assertIn("no active undispatched slot matches", ...)` line (criteria 2–3).

**PR #624 (ADR-0073 Option A) instead adds a wholly new sibling test node**,
`RT16SlotStateCoverage.test_consumed_slot_is_excluded_from_derivation_so_a_reused_identity_still_dispatches`,
and leaves the original method — including its `assertIn` at `:341` — untouched. Independently
re-verified, both directions:

```
# (a) production string reworded (no behavior change), unmutated tree:
$ sed -i 's/no active undispatched slot matches/derivation query returned zero candidate rows/' deploy/policies/omnipods_policies.py
$ python3 -m unittest -v <RT-16's four bound nodes>
test_session_create_is_not_an_authorized_dispatch_form ... ok
test_opaque_session_id_cannot_prove_the_target ... ok
test_live_bound_slot_allows_once_and_records_dispatch ... FAIL   <- the OLD node, :341, reddens
test_consumed_slot_is_excluded_from_derivation_so_a_reused_identity_still_dispatches ... ok   <- the NEW node passes
FAILED (failures=1)

# (b) mutant re-applied (RT-16.mutant.patch, unreworded string):
FAILED (failures=2)   <- both the old node (via its prose) AND the new node (via persisted-state
                          assertions) catch it; the new node's kill does not depend on (a)'s string.
```

Both measurements match the engineer's PR description and `.omni/tech-debt.md`'s 2026-09-02 row
exactly. The new node satisfies the *outcome* Version 1's item 3 exists for — "the F-1 kill is
detected by a structural assertion that does not depend on the wording of any denial-reason string"
— because a structural, reason-string-independent kill now exists in RT-16's bound set, verified by
me in both directions of the double proof. It does **not** satisfy criteria 2/3 literally: the old
node's `assertIn` was not touched, is still fully load-bearing, and is now measurably *more*
fragile than before in one narrow sense — an unrelated wording change reddens it, which is a
different failure mode than #607's (silent survival), and is the safe direction, not the dangerous
one. This is exactly the tradeoff the task asked me to adjudicate.

**Ruling: ACCEPTED as met-differently-than-specified.** Grounds: (1) ADR-0073 is a staff-gated,
dated, versioned decision record that names this choice explicitly (Option A vs the rejected
Option B, decision 1, decision 2) — it functions as the "new visible contract version" the
acceptance framework requires before an alternate solution can be signed off; (2) the residual
over-sensitivity at `:341` is independently documented, not swept under — `.omni/tech-debt.md`'s
2026-09-02 row names the exact two prose-mediated sites (`:341`, `:286`), quotes the same
measurement I ran, and states correctly that "D06's flip no longer depends on either site"; (3) my
own out-of-scope line for this item ("touching any other RT-16 test method" is out of scope) is,
read literally, satisfied by *not* touching `:341` — adding a new method is not "touching another
method," so criterion 6 ("no other test method changes") is in fact met by this approach, which I
had not anticipated when I wrote it as a constraint aimed the other direction.

**Amendment:** item 3 criteria 2 and 3 are superseded by: *"a new test node exists in
`RT16SlotStateCoverage`, bound alongside the existing positive control, whose kill of the 3-site
`dispatched_at IS NULL` mutant is demonstrated on persisted `worker_slots` columns
(`dispatched_at`, `bound_agent`, `bound_title` on two distinct slot rows) with no assertion on any
denial-reason string."* Criterion 4(a) is amended to read "the new node" rather than "the updated
test" throughout. Criterion 3 (downgrade the old `assertIn`) is dropped; the residual fragility at
`:341`/`:286` is accepted as a documented, bounded, FOLLOW_UP-severity item, not a merge blocker —
already tracked in `.omni/tech-debt.md`'s 2026-09-02 row, which needs no separate backlog item.

### 5.4 Item 2 criterion 6 — accepted as met-differently-than-specified

Version 1 asked for a dated addendum appended into `docs/decisions/ADR-0069-*.md` itself. PR #624
instead leaves ADR-0069 byte-unchanged (independently verified: `git diff c34d23b..4878fd9 --
docs/decisions/ADR-0069*.md` is empty) and adds a new record, ADR-0073, whose header states
"Supersedes in part: ADR-0069 §§2, 3, 5 — extends, does not overturn."

**Accepted.** A new ADR is a stronger amendment mechanism than an in-place addendum: it leaves a
staff-reviewed, accepted decision immutable (exactly the caution Finding A raised about editing
ADR-0069 on a false premise), while still being a dated, versioned, staff-gated record that
supersedes the specific sections it extends. The underlying requirement — the extension is
recorded as a decision, not silently folded into the binding — is met, and met more cleanly than
Version 1's literal ask. Item 2 criterion 6 is amended to: *"the extension to RT-17's scope is
recorded in a dated, staff-gated ADR that names ADR-0069 §§2/3/5 as partially superseded and states
ADR-0069's Decision text is left unchanged"* — satisfied by ADR-0073 as committed.

### 5.5 Item 1 — four nodes, not three; accepted

Item 3's accepted shape change (§5.3) adds a fourth RT-16 node. PR #624 binds all four in the
manifest, not the three Version 1's item 1 criterion 1 named. This follows directly from §5.3 and
is not a separate error: the fourth node belongs to `RT16SlotStateCoverage`, the same
already-bound class, so it stays inside ADR-0069 §2's scope line ("Other fixture-based coverage
... remains unbound" — this is not that; it is a new method on an already-in-scope class,
explicitly named as such by ADR-0073 decision 3 and its own reversal trigger, which requires
re-running the capture "any future sibling method is added to `RT16SlotStateCoverage`" — which is
exactly what happened, and exactly what was done). Item 1 criterion 1's node list is amended to the
four-element list PR #624 uses.

### 5.6 Item 6 status: PROVISIONAL, not settled — no amendment, a status correction

ADR-0073 decision 9 chose **deletion** of `unsatisfied_reason` from both RT-16 and RT-17, not
Version 1's default (rewrite), isolated in a single commit (`447fe00`) stated to be independently
revertible, and explicitly escalated to the program — matching crew_alpha's own recommendation in
this spec's Finding C, but not the default path item 6's criteria 1–2 described as the dispatchable
default. **This is not a spec error and needs no amendment**: item 6 was explicitly written to stay
dispatchable on *either* answer, with rewrite as the default and deletion logged as the
recommendation for product to revisit. The engineer's PR description states "left to the lead" on
logging the corresponding decisions.md update — that update is filed separately (see the
`.omni/decisions.md` entry dated 2026-09-02, amended in this same acceptance round). **Item 6 is
not signed off as closed in this round** — see §6.6 below.

## 6. Version 3 — re-affirmation at PR #624 head `76f18e8` (ADR renumber + re-check)

Written during a second acceptance pass, PM role, after two commits landed on top of the `4878fd9`
head this spec's §5 was written against: `bd4f1a5` (ADR-0073→ADR-0074 renumber, forced by a filename
collision — PR #594, a different `crew_alpha` branch, independently claimed `ADR-0073` for an
unrelated decision 64 minutes earlier than this task's ADR was authored; docstring updates in
`tests/test_admission_derivation.py`; RT-16 evidence re-capture) and `76f18e8` (ADR-0074's header
corrected to state it *extends and corrects* ADR-0069 §3, plus a back-pointer added to ADR-0069
itself). `staff` independently confirmed via `git diff --raw bd4f1a5..76f18e8` that exactly two
blobs changed between those two commits, both under `docs/decisions/` — nothing else moved. Every
finding below is my own re-run against this exact head, not carried over from §5's citations.

### 6.1 The ADR renumber — reference fix, not a substantive amendment

Every `ADR-0073` name in §5 above refers to the same document now filed as `ADR-0074-rt17-current-
generation-and-fallible-f1-evidence.md`. Confirmed directly: `git show 76f18e8:docs/decisions/` lists
no `ADR-0073-*` file at all, and `ADR-0074-rt17-current-generation-and-fallible-f1-evidence.md`
opens with the same task/issue/DoD-condition header (`506-step2-manifest-binding`, #507, D06) and the
same content shape §5 describes. This is a filename collision fix, not a content dispute — nothing
in ADR-0074 contradicts what §5 quoted from "ADR-0073". Per this spec's own discipline (§5's header:
corrections are appended, not silently rewritten in place), §5's text is left as originally written
rather than retroactively edited; this note is the record of where the identifier changed and why.
Going forward (§6 onward, and any future citation), the correct name is **ADR-0074**.

### 6.2 Items 1, 2, 4 — reaffirmed, no change

- **Item 1** (RT-16, four nodes, two classes): `docs/prd/DOD-MANIFEST.json` at `76f18e8` binds
  `test_node_id` as a 4-element list under `RT-16.test_node_id` — the two fixture-free bypass checks,
  the positive control, and the consumed-slot-exclusion node added under item 3's accepted shape
  change (§5.3/§5.5) — spanning `RT16DispatchRequiresLiveBoundUnconsumedSlot` (2 nodes) and
  `RT16SlotStateCoverage` (2 nodes). Matches §5.5's amended criterion exactly. **PASS.**
- **Item 2** (RT-17 fenced-out-generation node bound, re-capture, ADR amendment): `RT-17.test_node_id`
  is a 3-element list including `test_authority_fence.SlotAcquireOncallTokenGate.
  test_denied_for_a_fenced_out_generation`; `baseline_sha` is `c34d23b93b7bb7e84f9919ccf9555ffa
  65114bd3` (re-pinned per §5.1, not the stale `f2abab8`); both `RT-17.M1` and `RT-17.M2` mutation
  records are present in `docs/audit/baseline/RT-17.json` with `command` byte-identical to
  `baseline.command` at every one of the three sites I compared by eye. ADR-0074 §C4 documents the
  re-pin with the exact `grep -c` command and result. **PASS.**
- **Item 4** (#608, RT-16 mutation record committed): `docs/audit/baseline/RT-16.mutant.patch` and
  `RT-16.mutation.txt` are present and tracked (`ls docs/audit/baseline/` at this head). **PASS.**

### 6.3 Item 3 — reaffirmed as met-differently-than-specified, unchanged from §5.3

Re-read `tests/test_admission_derivation.py` at `76f18e8`: line 341's existing node
(`test_live_bound_slot_allows_once_and_records_dispatch`) still carries `self.assertIn("no active
undispatched slot matches", second["reason"])` verbatim — untouched, exactly as §5.3 accepted. The
new node `test_consumed_slot_is_excluded_from_derivation_so_a_reused_identity_still_dispatches`
(lines 348–396) asserts structurally on persisted `worker_slots.dispatched_at`, not on any reason
string. The only change since `4878fd9` is the docstring's ADR citation moving from ADR-0073 to
ADR-0074 (`bd4f1a5`) — no assertion text moved. §5.3's ruling stands. **PASS
(met-differently-than-specified, as previously accepted).**

### 6.4 Item 5 — reaffirmed, RT-17's own false-record correction still present

`docs/audit/baseline/RT-16.json` still carries the full hybrid-tree swap procedure (`composition`,
`swapped_file: deploy/policies/omnipods_policies.py`, `swapped_from_sha: 6b52252...`, the
`trap_o_nofollow` note, and the negative-control block whose `note_on_the_spec` field explicitly
flags §5.2's superseded prediction to `pm` — consistent with, not contradicting, what §5.2 already
recorded). `docs/audit/baseline/RT-17.json` still carries `baseline_sha_correction`, independently
re-confirming the `f2abab8`/`c34d23b` re-pin with the same `grep -c` command §5.1 used. Item 5's
growth to cover RT-17's own record (criterion set expanded in the original spec's §2 item 5, per the
program's condition 5 restatement) is satisfied by this field. **PASS.**

### 6.5 dod_validate.py re-run at this head — D06 PASS, independently reproduced

Ran `python3 tests/dod_validate.py --json` myself in a clean detached worktree at `76f18e8`
(`git worktree add --detach`, no shared-tree contamination). Result: `{"id": "D06", "verdict":
"PASS", "reason": "", "blocking_artifact": null}`. Full run: 24 conditions, `dod_complete: false`
only because `D08`/`D21`/`D22` are `unallocatable` for reasons unrelated to this task (manual/queued
conditions this PR does not touch). This matches the PM's own report ("RT-16 four nodes OK, RT-17
three nodes OK") and `staff_adr`/`cross_crew_review` both already `PASS`. D06 legitimately flips, and
now I have observed the green run directly rather than arguing it structurally, as §4 flagged I had
not yet done.

### 6.6 Item 6 — re-ruled: STILL PROVISIONAL, not settled by this head

Checked for new evidence that the program (not just this branch) has actually resolved the
escalation §5.6 raised: `git log bd4f1a5..76f18e8 -- docs/prd/DEFINITION-OF-DONE.md .omni/decisions.md`
is **empty** — neither file moved between the two commits `staff`'s two-blob diff already bounds.
`docs/prd/DEFINITION-OF-DONE.md`'s 2026-09-02 D06 changelog row still reads, unchanged, "The
manifest's `RT-17` `unsatisfied_reason` still quotes the retired wording and must carry the amended
text when the bindings land" — i.e. product's own written instruction in the product-owned file
still says *rewrite*, not *delete*, and nothing in this diff range touches that file to update it.
`grep` for any settling entry (`506-step2`, `unsatisfied_reason`, `item 6`) in this branch's
`.omni/decisions.md` returns nothing — this branch does not even carry the escalation entry from the
prior round. [Corrected in §6.7: the clause originally here claimed the entry "lives on
`pm/506-step2-manifest-binding`, a different branch, not yet merged here" — that was wrong on both
counts. This branch *is* `pm/506-step2-manifest-binding`, and at this PR's current head it does not
carry `.omni/decisions.md` at all, by design.]

So: the deletion at this head is the *same* action §5.6 already reviewed (same commit `447fe00`,
same isolation, same revert path), not a new product ruling on top of it. "The program has deleted
condition 6 as a no-op" describes what the branch does, not a decision the program has made about
whether that was the right call against product's written instruction — those are different claims,
and only the first one is true as measured. **Ruling unchanged: item 6 criteria 1–2 (rewrite-per-
written-instruction) are NOT MET; criteria 3–4 (isolated, revertible, escalated) are MET. Item 6 does
not gate D06 (§6.5 confirms D06 PASS independent of it) and remains open, pending an actual product
answer — not a silent settlement by inaction.**

**Overall: items 1–5 PASS at `76f18e8`, item 6 PROVISIONAL. D06 flip confirmed by direct
observation, not inference.**

[Corrected in §6.7 below: the ruling exists and is implemented; product's objection window is open.]

### 6.7 Correction (PR #623 fix packet, cross-crew review MB-1): the program ruled on the merits and #507 remains open for product's objection — §6.6 was repository-only blind

§6.6 above, and the "Overall" line closing this section, concluded item 6 "remains open, pending an
actual product answer" / "PROVISIONAL". **That conclusion was wrong when §6.6 was written, and
neither §6.6 nor the Overall line above should be read as still asserting it.**

The ruling exists. Issue **#507, comment id `5513759961`**, posted by `tushardhara` at
**2026-09-02T17:37:48Z**, opens *"Posted here because it is mid-turn; this is the authoritative
version"* and contains **`### Condition 6 — RULING: DELETE the field`**, reasoning that
`unsatisfied_reason` is read only when `test_node_id is None` and so is dead the instant RT-17 is
bound ("A field the gate never reads is a claim nobody checks"). It closes to `product`: *"The
ruling is recorded, not imposed — object and I will settle it once. `crew_alpha` should not block
on it."* Commit `447fe00` ("prd(F-5): delete unsatisfied_reason from the now-bound RT-16 and
RT-17") lands at **2026-09-02T18:18:13Z** (`git show -s --format=%cI 447fe00`) — **40m25s after
the ruling** (17:37:48Z → 18:18:13Z) — and implements it. It merges into `main` as `76de09d` in
PR #624 at **2026-09-02T22:04:14Z** (`git show -s --format=%cI` reports this commit's committer
date; converted to UTC here, since the rest of §6.7 is written entirely in `Z`; `git log
--ancestry-path --merges 447fe00..origin/main` confirms `76de09d` is the earliest merge carrying
it, so no earlier merge could make an earlier claim true). §6.6 was authored at commit `db30a6f`,
**2026-09-02T20:57:21Z**
(`git show -s --format=%cI db30a6f`) — **3h19m33s after the ruling** (17:37:48Z → 20:57:21Z) —
**after the ruling and after `447fe00` implemented it, though before #624 merged** (20:57:21Z
precedes 22:04:14Z by 1h06m53s) — and still concluded item 6 was unsettled.

**Why:** §6.6's check was `git log bd4f1a5..76f18e8 -- docs/prd/DEFINITION-OF-DONE.md
.omni/decisions.md`, plus a `grep` of this branch's own `.omni/decisions.md`. Both are
repository-surface checks; the ruling lives only as an issue comment, which neither can see. A
second, independent reviewer (`staff`) made the identical mistake three hours later for the same
reason — checking `DOD-MANIFEST.json` and `DEFINITION-OF-DONE.md`, initially finding them still in
contradiction — then caught itself: *"That conclusion reproduced SS6.6's own defect: I searched
repository surfaces only. The ruling was not in the repository."* (`staff`'s words, relayed;
the earliest occurrence on this PR is comment `5517860302`, 2026-09-02T23:24:19Z, which is why
it is cited here.) Two independent reviewers, two
vendors, the same methodological gap: a Program ruling that lives only as an issue comment is
invisible to anyone checking the repository. (See the correction at §6.6's `grep` paragraph above
for the specific false claim this gap produced about which branch carries the escalation entry.)

**Corrected status.** Item 6 criteria 1–2 (rewrite-per-written-instruction) are superseded by the
program's ruling to delete, not met-as-originally-stated; criteria 3–4 (isolated, revertible,
escalated) remain MET, unchanged from §6.6. **The program ruled DELETE on the merits (#507 comment
`5513759961`, 2026-09-02T17:37:48Z), recorded not imposed, and `447fe00` implements it, merged as
`76de09d` in #624. #507 remains open as the item's home, because product's own objection window is
still open on the ruling's own terms ("object and I will settle it once") — that is a real,
outstanding condition, not a formality, and this section does not call it closed.** What is
outstanding, besides that objection window, is the changelog sentence at
`docs/prd/DEFINITION-OF-DONE.md` — *"The manifest's `RT-17` `unsatisfied_reason` still quotes
the retired wording and must carry the amended text when the bindings land"* — which is stale
against the ruling (delete, not rewrite), verified against `origin/main` myself (that file is
product-owned and is not edited by this correction), and is now tracked as **issue #649**.

**Corrected Overall (supersedes the Overall line above): items 1 and 2 PASS as measured** by
running `dod_validate` in a clean detached worktree at `76f18e8` (§6.5) — per §4,
`generic_rt_condition(6)`'s own requirements (structurally valid baseline, live nodes green
with zero skips) are what that run checks, and "the flip is earned by items 1 and 2 alone."
That measurement is
scoped to the tree and validator as they stood at `76f18e8`; it is not a claim that today's
validator on `main` would reproduce it —
`git diff --stat 76f18e8 origin/main -- tests/dod_validate.py` shows **845 insertions, 19
deletions across 10 commits** since. **Items 3,
4 and 5 PASS as established structurally, not by that run's greenness alone** — per §4, "items
3-6 make the evidence underneath that flip honest rather than merely present" rather than earning
the flip themselves — established in §§6.2-6.4. **On `main` (`af77726`), `76f18e8` is confirmed an ancestor**
(`git merge-base --is-ancestor 76f18e8 origin/main`), **and the binding artifacts those items
assert are present, verified structurally rather than by re-running the gate** — no run at `main` was performed or is
claimed here: `git show origin/main:docs/prd/DOD-MANIFEST.json` shows both `RT-16.test_node_id`
and `RT-17.test_node_id` as non-null lists with no `unsatisfied_reason` key on either entry, and
`git ls-tree -r --name-only origin/main` lists
`docs/decisions/ADR-0074-rt17-current-generation-and-fallible-f1-evidence.md`,
`docs/audit/baseline/RT-16.mutant.patch` and `docs/audit/baseline/RT-16.mutation.txt` all
present. **This PR's own tree carries none of that** — it is cut from `c34d23b`, before
RT-16/RT-17 bound: `git show HEAD:docs/prd/DOD-MANIFEST.json` shows both `test_node_id` fields
`null`, both entries still carrying `unsatisfied_reason`, and `git ls-tree -r --name-only HEAD`
lists none of the three files above. **Item
6's deletion is ruled on the merits and implemented (`447fe00`/`76de09d`/#624), with product's
objection window still open and #507 open as its home; the stale `DEFINITION-OF-DONE.md`
sentence is tracked as #649. Neither gates D06. D06 flip confirmed by direct observation (§6.5),
not inference.**
