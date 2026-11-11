# ADR-0069: RT-16 asserts the in-repo semaphore property (not the harness roster), and carries condition 6's mandatory regression baseline at an ancestor

Date: 2026-09-02 · Status: accepted · Deciders: crew_alpha lead · Proposers: eng_codex (proposal B), qa (test plan); eng_claude (proposal A) **unavailable — see "Vendor availability"**
Task: `506-d06-rt16-rt17` · Issue: #507 · DoD condition: D06
Base SHA: `f2abab84785412f7dabc8e05d870e55f189ce9c7`
Spec: `docs/specs/506-d06-rt16-rt17.md`

**Amended by ADR-0074 (2026-09-02)** — §§2 and 5 are **extended** (RT-16 gains a fourth bound
node; RT-17 gains the fenced-out-generation node), and §3 is **corrected**: the `baseline_sha`
`f2abab8` recorded there does not reproduce — `test_slots.RT17ConcurrentAcquiresShareOneBudget`
does not exist at that commit, so the declared command yields exit 1 against a declared exit 0,
from which `baseline_kind()` would derive `regression`. ADR-0074 decision 5 re-pins RT-17's
record to `c34d23b`. The Decision text below is **not** edited (cf. ADR-0002:8, ADR-0006:9,
ADR-0010:7).

## Context

DoD condition 6 ("The worker budget is global") allocates two requirement ids that
have never been built. Both are `test_node_id: null` in `docs/prd/DOD-MANIFEST.json`,
and `python3 tests/dod_validate.py` reports `D06 FAIL RT-16: unsatisfied`.

- **RT-16** — "a program-level worker dispatch without a live, bound, unconsumed slot is denied"
- **RT-17** — independently produced concurrent acquires converge on one atomic budget; normal acquires cannot consume the reserve, and on-call reserve access requires Program authority

Three constraints were real on this date and together determine the design.

**(1) The measured program-level fact.** A real program-level dispatch naming a
counted worker never reaches the slot semaphore. It is refused earlier, by the
harness roster/schema layer:
`Input validation error: 'eng_claude' is not one of ['architect', 'crew_alpha', 'crew_beta', 'oncall', 'platform', 'product', 'release', 'security', 'staff', 'ux']`.
Relatedly, `sys_session_send`'s own schema is `additionalProperties: false`, so
`task_id`/`slot_id`/`generation` cannot be passed in `args` at all — the fact that
forced ADR-0062's derived admission. So "what RT-16 asserts" is a genuine fork, not
a wording detail.

**(2) Condition 6 may not rest on preservation records alone.** `tests/dod_validate.py`
derives baseline *kind* from `exit_code` (non-zero ⇒ `regression`, zero ⇒
`preservation`; never declared, only computed — `validate_rt_baseline`), and
`check_d08` fails outright with *"condition N would be evidenced by preservation
records alone"*. RT-16 and RT-17 are the **only** condition-6 ids, so at least one
must be a true regression capture at a real pre-fix ancestor.

**(3) Baseline captures can be contaminated.** `eng_codex` empirically reproduced a
spurious `0 != 3` failure by pointing today's classified, task-bound acquire fixture
at an older CLI. A capture that fails because the harness could not run is
`exit_code != 0` — and therefore *looks* like valid regression evidence — while
proving nothing. The rename at `38fe32c` (2026-08-21, `bin/omni-ctl` →
`bin/nightshift-ctl`) plus later `tests/ctlutil.py` growth (`VIRTUAL_T0`, `clock=`,
`normal_slot_cmdline`, `acquire_normal`, `seed_admission`) makes every CLI-driving
test unportable to older ancestors.

## Options considered

### A — RT-16 asserts the harness **roster** property (rejected)
Assert that the program cannot even name a counted worker. Rejected on three
independent grounds, and both proposers rejected it too:
- The roster enum is **external harness state**, not a file in this repo, so it
  cannot satisfy condition 8's `binding.file` requirement (the file must exist).
- It proves only that *today's* roster lacks `eng_claude`. It would stay green even
  if `worker_semaphore` allowed `sys_session_create` or opaque `session_id` bypasses
  — i.e. green while condition 6's actual required behaviour is violated.
- It does not test "every dispatch path that starts a worker consumes a slot", which
  is what condition 6 requires.

### B — RT-16 asserts the in-repo **semaphore** property (chosen)
Assert what condition 6 actually requires: the root gate denies any counted-worker
dispatch it cannot back with a live, bound, unconsumed slot. RT-16's own wording —
"without a **live, bound, unconsumed** slot" — is literally about slot state, which
only the semaphore has. Binds to `deploy/policies/omnipods_policies.py`, a real file.

### C — RT-16 regression captured at `45fa9ac` (rejected, proposed by eng_codex)
`45fa9ac` (parent of `6197d4b "enforce classified worker admission"`) lacks both
bypass guards and is post-rename, which is attractive. **Rejected on a fact I
verified directly:** at `45fa9ac` the missing-database case *already* fails closed
(`return _deny("control plane not initialised …")`, with the `_fail_open()` hatch
already present). Making the bypass assertions fail there therefore requires a
populated database, which reimports exactly the fixture-contamination risk of
constraint (3).

### D — RT-16 regression captured at `6b52252`, with fixture-free bypass checks and a hermetic positive control (chosen)
`6b52252` (= `0945f07^`, the commit before *"slots are one-dispatch capabilities;
semaphore fails closed"*) is a confirmed ancestor of the base SHA. It fails **open**
unconditionally (`if not os.path.exists(db_path): return _ALLOW  # control plane not
initialised — degrade openly`, and `except sqlite3.Error: return _ALLOW`) and has no
`dispatched_at`/`bound_agent` CAS at all.

Chosen because the two bypass assertions need **no database, no CLI and no `ctlutil`
import** — the guards they exercise all run before any DB access. The bound positive
control uses today's hermetic `Ctl` fixture and CLI to construct a valid live slot,
then swaps only the policy module to the ancestor. This avoids invoking the renamed
ancestor CLI while proving fixture setup completed before the policy assertion.

## Decision

1. **RT-16 asserts the semaphore property (Option B), not the roster property.**
   "Program-level" is honoured as *the root gate is caller-agnostic*: `worker_semaphore`
   is a root policy keyed on the **agent name**, not on who is calling, so the program
   path and the lead path are the same enforced code path. The two schema-reachable
   ways a caller could start a worker while bypassing slot derivation — `sys_session_create`
   and an opaque `session_id` send — are exactly "a dispatch without a live, bound,
   unconsumed slot", and are asserted explicitly.

2. **RT-16 carries condition 6's mandatory regression baseline, at `6b52252`**. Its
   bound nodes are the two fixture-free bypass checks plus a positive control that
   constructs a live slot using the current hermetic CLI and swaps only the policy
   module (Option D). Verified by direct execution of both policy versions:

   | assertion | base `f2abab8` | ancestor `6b52252` |
   |---|---|---|
   | `sys_session_create` is not a dispatch form | DENY | **ALLOW** |
   | opaque `session_id` cannot prove the target | DENY | **ALLOW** |
   | live matching slot ⇒ allow once and record dispatch | ALLOW then DENY | **DENY before first allow** (`args.slot_id` required) |

   The ancestor policy module imports standalone, and the positive control's current
   fixture completes before the assertion: it fails because the old evaluator ignores
   the live derived binding and requires schema-invalid caller-supplied `args.slot_id`,
   not because the database or CLI failed. The missing-control-plane assertion is not
   bound because the task spec §3 explicitly excludes that path. Other fixture-based
   coverage (consumed slot, TTL expiry, ambiguity, binding mismatch) remains unbound.

3. **RT-17 is a preservation record at the base SHA, with two committed mutants**
   on `bin/nightshift-ctl`: M1 deletes `- reserve` from the normal ceiling; M2 bypasses
   the Program authority-token predicate for on-call reserve acquisition. M1 kills the
   shared budget/reserve assertion, while M2 independently kills the missing/wrong-token
   refusal assertion. Condition 6 is not preservation-only because RT-16 is a regression
   record.

4. **The existing `GlobalSemaphore.test_concurrent_acquires_respect_budget_and_reserve`
   is NOT rebound to RT-17.** It covers the basic ceiling, but RT-17 requires explicit
   hermetic capacity 7/2, at least twelve independently produced subprocess attempts
   spanning multiple crews, both directions of reserve enforcement, and mutation evidence.
   No task, title, or holder label encodes a producer identity the normal-slot code cannot
   observe.

5. **Recorded limitation:** normal slot allocation is *not* caller-identity keyed.
   RT-17 therefore proves only that independently produced acquisitions converge on one
   atomic budget; it does not encode or claim a Program-vs-lead distinction. The one real
   caller-identity axis is on-call reserve acquisition: `cmd_slot_acquire` requires a live,
   current-generation Program authority token, and RT-17 proves both acceptance of the
   valid token and deterministic refusal of missing and wrong tokens. Whether the external
   harness routes every producer through this CLI remains a deployment/integration concern.

## Vendor availability

`eng_claude` failed to start twice ("Native Claude terminal failed to start"), so
proposal A was never obtained and this ADR rests on one engineer proposal (`eng_codex`)
plus an independent QA test plan. Both are Codex-vendor. **No independent
cross-vendor second opinion was obtained at proposal time**, contrary to this crew's
normal rule; it is flagged in `STATUS.md` and reported to the program. Where the two
Codex-side inputs disagreed (baseline SHA: `6b52252` vs `45fa9ac`) the lead settled it
by direct measurement rather than preference, and the measurement is in the table above.

**Root cause, established after this ADR was first written:** the failure was not
transient. *Counted worker seats* on the Claude vendor (`eng_claude`, `beta_qa`,
`reviewer_claude`) are dead org-wide pending #495. *Lead-level* Claude seats are
unaffected and are working normally.

**Standing limitation, not a blocker.** This is recorded as a known limitation of
ADR-0069's provenance rather than a reason to withhold the decision. The mitigation is
that cross-vendor scrutiny arrives at review time instead of proposal time: the
different-vendor review of this work comes from `crew_beta` at lead level. A reader
auditing this ADR should therefore treat the *proposal* stage as single-vendor and look
to the PR's cross-crew review for the independent second opinion. The two load-bearing
factual claims were in any case settled by direct execution, not by model preference —
the `6b52252` ancestry and the ALLOW/DENY table above are reproducible by anyone.

## Consequences

**Easy:** RT-16's two bypass checks are immune to fixture drift, while its positive
control prevents an always-deny evaluator from satisfying the bound set. Condition 6
gets a regression record, satisfying `check_d08`. Both bindings point at real files
(`deploy/policies/omnipods_policies.py`, `bin/nightshift-ctl`).

**Hard:** RT-16's positive control depends on the current hermetic fixture remaining
able to create a schema that the ancestor evaluator can read. Any fixture change must
re-run the capture and confirm setup completes before the expected policy failure.

**Forecloses:** RT-16 will never prove anything about the harness roster. If the roster
is the real control someone is relying on, it needs its own id under a condition that
can bind to harness state — not this one.

## Reversal trigger

Revisit if any of these becomes observable:
- `worker_semaphore`'s pre-DB guard ordering changes such that either fixture-free
  bypass assertion starts needing a database, or the positive-control fixture can no
  longer construct state readable by the ancestor (the capture would need re-derivation).
- The harness gains a dispatch form that starts a worker without passing
  `sys_session_send`/`sys_session_create` — RT-16's case list would then be incomplete.
- Slot allocation becomes caller-identity keyed, at which point RT-17's recorded
  limitation (§5) is fixable and should be tightened into a real assertion.
