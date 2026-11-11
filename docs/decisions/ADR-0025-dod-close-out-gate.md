# ADR-0025: The DoD close-out gate reports an honest verdict per condition, and can never be satisfied by a stub

Date: 2026-08-28 (re-verified and formally adopted; originally authored 2026-08-25) · Status: accepted
Deciders: crew_alpha lead · Proposers: eng_claude (A), eng_codex (B), 2026-08-25 debate
Issue: #186 (P0) · Base: `3e3dd8fdd2c1893a672755011a35885a2818db49` (checkpoint 4, `origin/main`, verified `git ls-remote origin main`)
Spec: `docs/prd/DEFINITION-OF-DONE.md` v2 (24 conditions) · Manifest: `docs/prd/DOD-MANIFEST.json` (to be created)
Conversation: `186-dod-close-out-gate` (crew_alpha lead) · PM DoR: https://github.com/tushardhara/nightshift/issues/186#issuecomment-5455339921

## Provenance note (read this first)

This ADR number and most of its content are **not new**. A genuine two-vendor debate on
this exact question ran on 2026-08-25 (`eng_claude` vs `eng_codex`, both vendors live
that day) and produced `docs/decisions/ADR-0025-dod-close-out-gate.md` on the abandoned
branch `alpha/186-dod-close-out-gate` (commit `4b9adcf`), 204 lines, real independent
proposals, a real disagreement, a real resolution. Issue #186 was then closed
2026-08-26 with zero comments, no PR, no ledger entry — an administrative failure, not
a design failure — and reopened 2026-08-28 (see `.omni/decisions.md` "D — Reopened
issue #186"). The branch's *code* is genuinely unusable (109 commits of unrelated
drift since its base `dba8a373`), but its ADR is a clean, isolated, still-valid
204-line file with no such drift, and discarding real cross-vendor reasoning to
re-derive the same conclusions alone would be worse evidence than reusing it honestly.

**What changed in this re-adoption vs. the 2025-08-25 original, and why:**
- Worker dispatch is confirmed sterile this session (issue #236, live tool-schema
  mismatch with `worker_semaphore`; reproduced directly this session, see the crew's
  report). This is a **different** failure mode than the original's Context section
  described (that noted both vendors' *seats* were live; #236 is a policy/schema
  incompatibility on top of live seats). No fresh cross-vendor proposal was obtainable
  today — Working Agreement #5 governs: the lead re-verifies and adopts under a slot
  it holds, flagged here, with cross-crew review as the required independent check
  before merge.
- All measured facts below are re-taken fresh at today's base, not copied from
  2026-08-25 — several drifted materially (test count, runtime, CI headroom; see
  Context).
- One consideration the original genuinely did not have to address: `docs/prd/**` is
  `product`-owned, single-writer (`docs/owners.md:23`), and `docs/prd/DOD-MANIFEST.json`
  is in scope. The original's own abandoned branch flagged this as `UNRESOLVED`
  (commit `478fdfb6`) under an edit lease that has since expired. This ADR does not
  resolve ownership — that is `product`'s and the program's call — but names the
  consequence explicitly in Consequences below, per PM's Definition-of-Ready pass.
- `DEFINITION-OF-DONE.md` has had no changelog rows since 2026-08-23; the spec this
  ADR reads against is unchanged from the one the original authors read.

## Context

`docs/prd/DEFINITION-OF-DONE.md` names exactly one close-out gate — `tests/dod_validate.py`,
stdlib Python 3, no dependencies — and binds every requirement id through
`docs/prd/DOD-MANIFEST.json`. Verified today at `3e3dd8f`: **neither file exists**
(`git ls-tree -r origin/main --name-only | grep -iE 'dod_validate|DOD-MANIFEST'` → no match).

The consequence is not cosmetic. The DoD says the project is finished when all 24
conditions report `PASS` via this validator. With no validator there is no `PASS`, so
there is no reachable end state, and no "N of 24" figure anyone has quoted is a
measurement. That is the whole of issue #186.

The dominant design risk is not difficulty, it is *dishonesty*: 24 heterogeneous
conditions, most of whose evidence does not exist in the tree, create constant pressure
to return `PASS` from a check nobody finished. Issue #186's AC7 (and the PM's AC7 in the
2026-08-28 DoR pass) exists to name that failure mode; this ADR's job is to make it
structurally unavailable rather than merely forbidden.

Constraints real on this date (re-measured today, in an isolated worktree cut from the
checkpoint — **not** the shared `/srv/project` checkout, which currently carries another
session's uncommitted staged work and would have poisoned these numbers):
- `tests/run.sh` → **`Ran 950 tests in 480.430s … OK`**, clean, at `3e3dd8f`. (The
  2026-08-25 original measured 618 tests / 220.5s; the 2026-08-22 DoD baseline B1
  recorded 83 tests / 18s. The suite has grown ~11× since B1 and ~54% since the
  original ADR, three days apart — growth is not slowing.)
- `.github/workflows/tests.yml`: unchanged, `timeout-minutes: 15` (900s), one job
  (`tests`), stdlib-only, no install step. Suite now consumes ~53% of the CI ceiling
  (up from ~24% three days ago); **headroom for the validator is ~420s**, down from the
  original's ~680s estimate. This is the DoD's only timing constraint ("the full suite
  plus the validator must stay runnable in one CI job's timeout") and it is tightening,
  not loosening — a fact the implementer must carry forward, not just the original's
  now-stale number.
- `docs/audit/baseline/` today holds real captures for `RT-01…RT-15`, `RT-20…RT-22`,
  `RT-24`, `RT-27`, `RT-28`, `RT-28-r22` (22 files). **`RT-16…RT-19`, `RT-23`, `RT-25`,
  `RT-26` have no capture and no test reference anywhere in `tests/` today** — checked
  by direct grep this session, not assumed. This is materially more "unsatisfied" RT
  ids than the original authors had in front of them, which makes decision §3 below
  (explicit unsatisfied records) load-bearing rather than a hedge against a rare case.
- Both `claude` and `codex` binaries resolve on this host today (`command -v claude
  codex`), so the *seats* the original ADR's Context relied on are still live — the
  sterility is `worker_semaphore`'s dispatch-time schema mismatch (#236), not a dead
  vendor. Recorded because the two failure modes need different reversal triggers (see
  below).

## Options considered

### A — Registry of checks, evidence removed in place for self-test (proposed by eng_claude, 2026-08-25)

A `@register("D01")` decorator over 24 functions `(repo_root, manifest) -> Verdict`, each
declaring a required instrument (`FS`/`GIT`/`SUBPROCESS`/`GITHUB`). Test nodes run batched
one `unittest` invocation per *module*, parsing the human `unittest -v` summary for
`OK`/`FAILED`/`skipped=`. `--self-test` `os.rename`s a check's evidence into a temp dir,
re-runs the check in-process, asserts not-`PASS`, restores. An `RT` id with no capture is
**absent** from the manifest, and the referencing condition fails with "referenced but
absent".

Won on: the instrument enum; the containment of every check in `try/except`; and one rule
that is the strongest anti-stub device either vendor proposed (see Decision, §5).

Lost on: parsing human-readable `unittest` output is brittle and cannot produce an exact
skip count, which condition 8 demands ("reports **zero skips**"); rename-and-restore
mutates the real working tree and leaves it corrupt if the process dies mid-run; and its
runtime argument rested on the stale "83 tests / 18s" figure, a factual error settled by
fresh measurement rather than by argument — twice now, three days apart.

### B — Framework with a private node-runner and an isolated self-test tree (proposed by eng_codex, 2026-08-25)

A frozen `Check` dataclass tuple registering exactly `D01`…`D24`, validated *at startup*
(24 entries, ids in order, only D08/D21 manual, every check has a real callback and a real
evidence-remover, manifest condition ids identical). Test nodes run through a private
`--run-node` mode that loads via `unittest.defaultTestLoader.loadTestsFromName()` and emits
one compact JSON object (`testsRun/failures/errors/skipped/successful`), cached per node
id. `--self-test` builds a fresh isolated fixture from `git archive HEAD` per condition, so
the source checkout is never mutated. An `RT` id with no capture is present as an
**explicit unsatisfied record** carrying `unsatisfied_reason`.

Won on: exact machine-readable skip counts; isolation that cannot corrupt the checkout
(load-bearing given today's confirmed shared-checkout contamination risk — see Context);
startup validation of the registry itself; and a sharper reading of the `MANUAL` rule.

Lost on: nothing structural — but its unsatisfied-record shape carries the risk A named
(a record that exists but binds nothing looks like progress that isn't).

### C — YAML/DSL-driven declarative checker (rejected early, by both)

Conditions expressed as declarative queries over the tree. Rejected because several checks
need real control flow — the mutation-patch-touches-a-subset-of-declared-production-files
derivation, and the self-test evidence-removal loop — and forcing them into a query
language yields a worse, less testable Python interpreter. The DoD also fixes the set at 24
by its own change-control rules, so extensibility buys nothing here.

## Decision

Build B's structure, with A's two best mechanisms grafted on, and one invariant that
resolves the single genuine A/B disagreement. (Unchanged from the 2026-08-25 original —
re-verified against today's facts and found still correct; the three additions this
re-adoption makes are marked **[2026-08-28]**.)

**1. `FAIL` vs `BLOCKED` — the rule.** Both vendors proposed materially the same rule
independently. That agreement is the cheapest confidence signal available and it is taken
verbatim:

> `BLOCKED` iff the **instrument itself** did not answer. `FAIL` iff the instrument
> answered and the answer does not satisfy the condition.

"Instrument" means the observing facility — GitHub, `docker`, a required executable — never
the evidence it was supposed to observe. Filesystem and git reads inside the checkout are
always reachable, so a missing document, a dangling `RT` id, an empty array or an absent
capture is **always `FAIL`, never `BLOCKED`**. This is derived from the DoD's own words
("Unreachable GitHub, a dead vendor seat, or a missing tool yields `BLOCKED` with a reason"
— the reason must *name the unreachable instrument*). Worked consequences: a missing
`docs/audit/CONCURRENCY.md` is `D13 FAIL`, because `open()` worked and found nothing;
`gh` transport failure is `D10 BLOCKED github: …`; `gh` answering with a red or
wrong-SHA run is `D10 FAIL`.

The corollary that matters most: **"I did not finish building this check" is never
`BLOCKED`.** Difficulty is not unreachability. A check that is not implemented reports
`FAIL not implemented: <what it would have to observe>`.

**2. `MANUAL` is a verdict of success, not of absence.** `MANUAL` is admissible for
conditions 8 and 21 only, and only when the declared evidence is *present and resolvable* —
`reviewed_by` populated with a resolvable URL for 8, `frozen_path_control` populated with an
existing evidence file for 21. Absent evidence on 8 or 21 is `FAIL`, not `MANUAL`. This
follows the DoD's exact wording ("not `PASS` or a declared `MANUAL` **with its evidence
present**"). A third condition declaring `manual` in the manifest is a hard error that names
the offending condition and exits non-zero.

**3. Unsatisfied `RT` records — the one real disagreement, and the call.** Take **B's
explicit records** (an allocated-but-unbuilt id such as `RT-16`…`RT-19`, `RT-23`, `RT-25`,
`RT-26` — **7 of the ~30 allocated ids as measured today, up from the original's smaller,
unenumerated set [2026-08-28]** — appears in the manifest with `test_node_id: null`,
`baseline: null`, and an `unsatisfied_reason`), because it lets the validator distinguish
"allocated, known, unfinished" from "typo or unknown id" and emit a different, honest
reason for each — which serves the honesty requirement better than a single
undifferentiated "absent".

But A's objection is correct and becomes a **binding invariant**, not a preference:

> Satisfaction is computed **only** from `test_node_id` resolving to a test node that
> exists *and* a baseline record that validates. `unsatisfied_reason` is an output-only
> field. It is never an input to any verdict, and a record carrying one can only ever
> produce `FAIL`.

A manifest entry that exists but binds nothing is exactly "the stub returns `PASS`" moved
one layer down into the data, and it is prohibited by construction rather than by review
diligence. The self-test must contain a case that populates an unsatisfied record with
plausible-looking values and asserts its condition still reports `FAIL`.

**4. Test-node execution.** B's private `--run-node` mode emitting one JSON object, over A's
parsing of human `unittest -v` output. Condition 8 requires an exact **zero-skip** assertion
and a node that loads zero tests must fail; both need structured counts. Results are cached
by node id for the run, so a node bound by several conditions executes once. The validator
does **not** re-run `tests/run.sh` as a whole. Budget: **~420s of headroom [2026-08-28,
re-measured — was ~680s]** inside the 900s CI ceiling; the implementer must re-measure at
merge time and report the number, never assume either ADR's figure still holds.

**5. `--self-test` — B's isolation, A's teeth.** Each condition's self-test builds a fresh
fixture from `git archive HEAD` into a temp tree, applies that check's evidence-remover
there, runs only that check, and asserts the verdict is not `PASS` with the expected failure
category. The source checkout is never mutated — **load-bearing, not theoretical: this
session independently hit real corruption risk in the shared checkout from a concurrent
session's uncommitted work [2026-08-28]**, which is exactly the class of damage a
mutate-in-place self-test (Option A's original mechanism) could inflict on a real checkout
if interrupted. Grafted from A, and this is the rule that does the real work against
stubbing:

> A check that cannot declare an evidence-remover — that has nothing whose removal would
> break it — is an **authoring-time hard error**, not a silent exclusion from the
> self-test.

A check with nothing to remove is a check that asserts nothing. It fails the build of the
validator itself.

**6. Registry validated at startup.** Exactly 24 entries, ids exactly `D01`…`D24` in order,
only D08 and D21 `manual`, every check carrying both a real callback and a real
evidence-remover, and the manifest declaring the identical condition id set. Every check
runs inside `try/except`; an exception becomes `FAIL validator error: <type>` and never
terminates the loop, so all 24 lines always print.

**7. Orphaned captures stay orphaned.** `docs/audit/baseline/` holds captures (today:
`RT-24`, `RT-27`, `RT-28`, `RT-28-r22`, plus `RT-29`'s non-manifest record) which no v2
condition text references. Both vendors independently refused to bind them to conditions,
and both cited the DoD's own prohibition on inferring condition membership from allocation
order ("Read the condition to learn which ids it requires; never infer it from the
number"). They are omitted from the manifest. No fifth verdict is invented for them.

**8. No DoD text is edited.** Not one condition, not one word, for any reason including
difficulty. Anything genuinely unverifiable as written is reported to the program with its
line reference, and left standing.

**9. [2026-08-28, new] `docs/prd/DOD-MANIFEST.json`'s content is built and committed inside
this PR's branch — per `MILESTONES.md:131`'s "crews propose rows through their PRs" — but
its merge into `main` is a separate, named dependency, not part of this design decision.**
`docs/owners.md` assigns `docs/prd/**` to `product`, single-writer, with no exception row for
this file; the abandoned attempt's own commit `478fdfb6` reached the identical conclusion
and left it explicitly `UNRESOLVED` under a since-expired edit lease. This ADR does not
adjudicate that ownership question — it is `product`'s and the program's to rule on — but
states plainly that the PR built under this ADR can be opened, gated, and reviewed in full
with the manifest content present, while the merge step for that one file waits on the
ruling. This mirrors the pattern condition 22 itself already uses for gates that are
temporarily down: the work does not wait, the merge does.

## Consequences

Makes easy: an honest, reproducible number; a validator whose own failure modes are
testable; adding a condition check later without touching the other 23; and a manifest that
doubles as the `RT` allocation ledger.

Makes hard — deliberately: shipping a green validator. Under this design the number at
`3e3dd8f` will be bad, and worse than either vendor estimated three days ago given the
measured growth in unsatisfied RT ids: with `RT-16…RT-19`, `RT-23`, `RT-25`, `RT-26`
confirmed unsatisfied today and conditions 6/7/11–24 mostly unimplemented, expect roughly
2–4 `PASS`, 2 `MANUAL`-eligible-if-evidenced, and the remainder `FAIL`/`BLOCKED`. The
measured distribution at merge time, not this estimate, is the deliverable.

Forecloses: any later argument that a condition is `BLOCKED` because its check was hard to
write. §1's corollary closes that door explicitly, and §5 makes an assertionless check fail
at authoring time.

Does not split into parallel tasks. Both vendors agreed in the original debate, and nothing
about today's facts changes it: `dod_validate.py` is one single-writer file and concurrent
authors would only conflict on it. **One engineer, one worktree, one PR** — which, under
Working Agreement #5, is the crew_alpha lead itself this session, building under a held
slot, with cross-crew review as the required independent check since in-crew review/QA
cannot run (#236).

`docs/prd/DOD-MANIFEST.json`'s merge is gated on a ruling this ADR does not make (§9) —
that ruling is a dependency of the item, tracked in `.omni/registry-crew_alpha.json`, not a
blocker on writing or reviewing the code.

## Cost

No new operational surface: stdlib-only, no service, no dependency, runs in CI inside the
existing job. Ongoing cost is maintenance — every future DoD changelog row or new baseline
capture is a manifest edit, and `RT` id allocation must keep following WA-007 (request from
the program, never self-numbered).

## One-way door?

No. `tests/dod_validate.py` and `docs/prd/DOD-MANIFEST.json` are both replaceable in a
later PR without data migration — the manifest is a JSON ledger, not a schema with external
consumers yet. The `unsatisfied_reason` invariant (§3) is the closest thing to a one-way
commitment: relaxing it later (letting an unsatisfied record satisfy a condition) would be
a direct reversal of this ADR's central anti-stub guarantee and should require a new ADR
that supersedes this one, not a quiet edit.

## Scale tier

None yet — this is a repo-local, single-CI-job tool with no external consumers.

## Reversal trigger

Revisit if any of these becomes observable:
- The validator's own runtime plus `tests/run.sh` exceeds `tests.yml`'s 15-minute ceiling
  on a clean runner. Headroom has already shrunk from ~680s to ~420s in three days; if that
  trend continues the manifest moves to coarser but still exact suite/class nodes —
  **never** to skipping execution.
- A condition is shown to be undecidable as written by a validator, rather than merely
  unsatisfied by today's evidence. That is a `product` changelog matter under the DoD's
  change control, and this ADR does not pre-authorise it.
- `BLOCKED` starts appearing on any condition whose instrument is the local filesystem or
  git. That means §1's rule has leaked and the validator has begun excusing itself.
- Issue #236 is fixed and worker dispatch is live again for a subsequent change to this
  validator: the *next* change should get a fresh two-vendor debate rather than another
  single-lead re-adoption, since the sterility that justified this one will no longer hold.
- `product` rules on `docs/prd/DOD-MANIFEST.json` ownership (§9) in a way that requires a
  different manifest authorship/merge flow than "crew proposes in its PR, product takes
  authorship at merge" — that ruling supersedes §9 specifically, not the rest of this ADR.
