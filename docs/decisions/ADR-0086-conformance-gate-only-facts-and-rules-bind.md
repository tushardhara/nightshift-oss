# ADR-0086: The conformance gate binds on FACTS and RULES only; EXPERIENCE is advisory and structurally cannot widen a decision

Date: 2026-09-05 · Status: **proposed**
Deciders: program · Proposers: eng_claude (platform/442-conformance-gate)
Conversation: n/a (single-agent worktree build) · Spec: issue #442 (epic), issue #443 (the detector, closed)

## Context

#443 shipped `bin/nightshift_project_contract.py`: a deterministic, evidence-backed
compiler that reads a repository at an exact commit SHA and emits an Engineering
Contract -- language, build system, test frameworks, commands, CI checks,
lockfiles, dependency manifests. It reads correctly. Nothing refuses anything.
Nightshift can know a repository is declared as one stack and nothing stops a
worker adding a second, undeclared one. A reviewer might catch it. No mechanism
denies it. The epic calls a fact nobody enforces "decoration."

This ADR covers the gate that consumes the contract and denies. Two questions
had to be answered before any code: (1) what is the shape of "permitted", and
(2) where, mechanically, can a denial actually stop a change today.

## Options considered

### A -- `evaluate(contract, rules, context)` with an advisory `context` parameter (rejected)

One function, three parameters, with `context` documented as "read-only, for
retrieval hints." This is the shape a retrieval/memory feature would naturally
reach for. It was rejected because it makes the correct behavior a matter of
discipline rather than of what the function can physically do: nothing stops a
future edit from reading `context["similar_past_changes"]` and unioning it into
the permitted set, and nothing in the function's shape would show that it had
happened. ADR-0009 already named this exact failure mode for a different guard:
"a guard that agents must remember to invoke cannot fix a discipline failure."
The same sentence applies to a guard whose signature merely *asks* callers not
to widen permission through a side channel.

### B -- FACTS and RULES have the only parameter slots; EXPERIENCE arrives after, and only additively (chosen)

`evaluate(*, base_contract, head_contract, rules)` takes exactly three
keyword-only parameters. `permitted_languages(base_contract, rules)` and
`permitted_test_frameworks(base_contract, rules)` -- the two functions that
compute what is allowed -- take exactly those two. There is no fourth argument
anywhere in the permission computation through which a retrieval result, a
memory hit, or a "people seem to use this here" note could enter. Advisory
content can only attach to a decision that already exists, through
`attach_advisory(decision, notes)`, which recomputes `decision_digest` from the
decision's own `verdict`/`violations`/contract- and rules-digests and raises
`AdvisoryEscalation` if the recomputed value does not match what it was handed.
This is checked by `inspect.signature` (parameter names, and that `evaluate`'s
three are all `KEYWORD_ONLY`), not by a comment or a docstring promise.

**The rule this ADR asserts for the wave:** advisory or learned state may be
printed beside a decision and may never enter the computation of what is
permitted.

### C -- Enforce the layering by policy/lint over PR content, not by function shape (rejected early)

Scan diffs for "did this function read from a memory/embedding module" as a
static check. Rejected: it would need to track data flow through the exact
kind of dynamic, cross-module call graph Python does not make easy to scan for,
and a determined (or merely careless) future edit could route around it through
an extra layer of indirection. Option B's guarantee is architectural -- no
parameter, no path in -- rather than something a linter has to keep re-deriving.

## What "violation" means: set widening between two contracts, not a claim comparison

`bin/nightshift_project_contract.py` already ships `contradicts(contract,
claim)`, built for exactly this seam ("so a later conformance-gate child can
call this directly instead of re-deriving the comparison"). It was measured
directly against a real, checked-in fixture
(`tests/fixtures/dogfood_b_java/`, extended here with an added Go service and
a Playwright config) and found blind exactly where this gate needs it: its own
docstring says "a field the contract could not observe (absent) or could not
resolve (conflict) contradicts nothing." When the fixture's own `e2e` field
resolves to `status: conflict` (`value: null` -- two configs, Cypress and
Playwright, both tracked at root), `contradicts()` against a claim naming
`{"e2e": {"framework": "playwright"}}` returns nothing for that key. A claim
comparison built on `contradicts()` alone would see `{}` against `{}` and
ALLOW the introduction it exists to catch.

The primary decision path in this module therefore compares two COMPILED
CONTRACTS -- base and candidate -- as a set difference, and reads the value set
from `candidates[*].value` (which survive `status: conflict`, each candidate
carrying its own evidence) rather than from the resolved `value` (which is
`None` under `conflict` by construction -- see
`nightshift_project_contract.conflict()`). Measured on the same fixture: the
candidate contract's `e2e` field is itself `status: conflict` (Cypress vs.
Playwright, both present), and the gate still correctly denies on
`undeclared_test_framework: playwright`, with the introducing file
(`playwright.config.ts`) as evidence. `evaluate_claim()` keeps the
`contradicts()`-based, ticket-level shape #443 asked to be inherited rather
than re-derived, and reports which claimed fields the contract could not
resolve (`claim_unevaluated_fields`) so a caller reads that gap as "unproven,"
never as silent consent. It is library-only; the check CI actually runs is the
contract-vs-contract path.

A benign, in-stack change (another file in an already-declared language, another
spec in an already-declared framework) is not a violation: the comparison is
set widening, not volume of change. Measured: an added Java file plus an added
Cypress spec against the same base contract computes an empty widened set and
returns `allow`.

FACTS are pinned to the base commit specifically. `build_decision` compiles the
base contract at `base_sha` and the candidate at `head_sha`, never the reverse,
and `evaluate` separately refuses `base_contract["baseline_sha"] ==
head_contract["baseline_sha"]` as `vacuous_comparison` (indeterminate, never
allow). Measured: a candidate that deletes the Java/Maven/Cypress manifests and
keeps only a newly-added Go service still denies, because the base contract is
read from git's tree at `base_sha` regardless of what the working tree (or a
later commit) does to those files. A defect that made `build_decision` compile
the base contract at `head_sha` instead was injected directly: because that
collapses `base_sha == head_sha`, the module's own `vacuous_comparison` guard
independently catches it too, turning the defect into `indeterminate` rather
than a silent `allow`. Two independent mechanisms protect this property, not
one.

### The absent-base-field rule: an unobserved base declares nothing, so every value in the candidate is new

`languages_of()` and `frameworks_of()` read a field with `status: absent`
(missing, or malformed) as contributing the empty set. `permitted_languages()`
and `permitted_test_frameworks()` read the BASE contract through those same
two functions, so on the base side that empty set doubles as the *permitted*
set. The consequence is deliberate, not incidental: a language or test/
integration/e2e framework introduced against a field the base never observed
has nothing to be a subset of, so it registers as NEW and the gate DENIES it
-- the base contributing nothing is what makes the rule bind maximally against
an unobserved field, not what exempts one.

The rejected alternative was a `status == "absent"` bailout at each call site
in `_language_violations`/`_framework_violations`: treat an absent base field
as `not_applicable` and skip the diff for that field entirely, the same shape
`build_decision` already uses for a field neither side could resolve. Measured
directly against this repository's own contract: `integration` and `e2e` are
both `absent` on nightshift's base contract today. A bailout there would
silently ALLOW the introduction of the first-ever test/integration/e2e
framework this repository adopts -- exactly the epic's own motivating example
("nothing stops a worker adding a second, undeclared [stack]"), reproduced by
the one field this gate exists to guard. Measured: with the bailout in place,
a base contract carrying `e2e: {status: absent}` and a candidate adding
`playwright.config.js` computes `not_applicable`, not `deny`; with the bailout
removed, the same inputs compute `deny` on `undeclared_test_framework:
playwright`, matched by the real-CLI subprocess case in
`tests/test_conformance_gate.py`.

An absent field is therefore NOT treated the way `evaluate`'s own
`vacuous_comparison` guard treats a base and head that are otherwise
indistinguishable (see "FACTS are pinned to the base commit specifically,"
above) -- that guard exists to refuse a comparison that cannot possibly
detect anything; the absent-base-field rule instead makes a comparison that
CAN detect something detect it. The two are not in tension: one guards
against a degenerate comparison, the other guards against a degenerate
permitted set.

## RULES: declared, frozen, versioned, and fail-closed on load

`program/conformance-rules.json` declares two rule KINDS this wave --
`no_undeclared_language` and `no_undeclared_test_framework` -- each carrying
`binding: true` and the contract fields it reads. Loading is fail-closed by
construction, verified by injected defect:

* An unrecognised rule `kind` is REFUSED (`reason_code: rules_invalid`, exit 3),
  never silently skipped. A version of the loader that `continue`s past an
  unknown kind instead of refusing would load successfully and simply never
  know about the rule it dropped -- the "sign nobody enforces" failure this
  epic exists to close, reproduced in miniature inside the gate's own
  configuration if this were allowed.
* A rule declaring `binding: false` is REFUSED. A non-binding rule is
  EXPERIENCE, not a RULE, and this layering is enforced in the data file's
  own shape, not only in code.
* A waiver is REFUSED unless it names an `adr` reference, and a waiver permits
  ONLY the exact `{kind, value}` pair it names -- measured directly: a waiver
  naming `{kind: undeclared_test_framework, value: playwright}` clears the
  Playwright violation on the worked fixture above while leaving the
  `undeclared_language: go` violation on the SAME candidate fully denied. This
  is the epic's own escape hatch: migrating a test suite from one framework to
  another routes through an ADR, supersedes the contract for that one value,
  and only then is the new framework permitted -- never the whole violation
  kind, never a value the waiver did not name.

`program/conformance-rules.json` names no language, build tool, test runner or
package manager. It cannot: issue #439's core-portability guardrail scans
`bin/`, `deploy/`, `program/` for exactly twelve such tokens and fails on any
unmanifested hit, and this gate adding zero entries to
`tests/core_portability_manifest.json` is one of its own acceptance criteria.
Generic rule KINDS whose permitted set is *derived* from the base contract's
own FACTS (never a hardcoded allowed-stack list) is therefore not only the
layering-correct design, it is the only one #439 would let land unmanifested.
Verified directly: `bin/nightshift_conformance.py` and
`program/conformance-rules.json` contain none of the twelve scanned tokens
(checked by direct substring search against the same literal list
`tests/core_portability_manifest.json` carries).

## Where a denial can actually stop something today

The epic records, as a known constraint, that worker-level enforcement may be
impossible and the gate may only be buildable at release time. That framing was
checked against the code as it stands now, not assumed:

* **#236, #395, #423 and #439 -- the constraints the epic names as blocking
  worker-level dispatch -- are closed.** That half of the epic's framing has
  expired.
* **A new, harder, structural constraint exists that the epic does not
  record.** The strongest fail-closed worker-side gate today is
  `program/agents/crew_alpha/agents/eng_codex/tools/python/nightshift_publish.py`'s
  `_make_commit`, which refuses a commit unless `nightshift-ctl guard-commit`
  returns a specific attested-fd payload. That controller runs as
  `[_PYTHON_EXECUTABLE, "-I", "-", *args]` (line 404) with the ctl source
  piped in on stdin, and `_subprocess_env` (line 106) refuses any environment
  key outside a fixed allowlist of git-plumbing variables (line 79). Probed
  directly: `python3 -I -c "import sys; print(sys.path[:3])"` shows no script
  directory and no current-working-directory on `sys.path` under `-I`, and no
  `PYTHONPATH` can be injected past `_subprocess_env`'s allowlist. **A
  contract-consuming module cannot be imported at that chokepoint.** This is a
  verified import-boundary fact, not a policy choice, and it forecloses the
  strongest worker-side gate as a home for this check regardless of how #236
  or #439 resolved.
* The pre-commit hook (`nightshift-ctl guard-commit` / `hook install`,
  `bin/nightshift-ctl` ~5285-5390) is ADR-0009's designated safety boundary and
  DOES resolve imports normally as an ordinary process exec -- but nothing
  installs it (`hook install` appears only in `nightshift-ctl` itself, tests,
  and one deploy comment; `bin/nightshift-init.sh` never calls it), and the one
  seat with a publish tool bypasses git hooks by construction (`_push` sets
  `core.hooksPath=/dev/null`; `_make_commit` writes via `commit-tree`, which
  never fires a hook at all).
* The Publication Gateway (`bin/nightshift_publication.py`, ADR-0081) has the
  right refusal shape (`PublicationError`/`error_payload`, the exact vocabulary
  this module borrows) but no live caller outside its own CLI and tests today,
  and its unit is one staged document, not a diff -- not yet the right object
  to hand a contract comparison.
* `review plan` is report-only by ADR-0036; `dod` is explicitly non-gating by
  ADR-0063; `deploy/policies/omnipods_policies.py` judges tool-call arguments,
  and ADR-0009 C3 already ruled that layer cannot judge filesystem results.

**The strongest chokepoint that actually exists today is the `control-plane
tests` GitHub Actions check** (`.github/workflows/tests.yml`). It runs on every
pull request and every push to `main`, checks out with `fetch-depth: 0` (both
merge-base endpoints are present without an extra fetch), and already carries
non-suite steps in the same job. This ADR wires one more: `project conformance
gate`, PR-only, computing the comparison range with `git merge-base` (not
`github.event.pull_request.base.sha` directly, which is the base branch tip at
PR-creation time and would misattribute another PR's changes on a stale
branch) and passing both SHAs through `env:` rather than shell interpolation.

**This is a procedural block, not a mechanical one, and this ADR states that
rather than overclaiming it.** `.github/workflows/tests.yml` already records
that branch protection and rulesets both return 403 on this repository's plan,
so nothing in GitHub itself refuses a merge over a red check. What makes it
binding is `program/agents/release/config.yaml`'s own release procedure
requiring the check green, asserted by `tests/test_ci_workflow.py`. A worker
publishing through `nightshift_publish.py` reaches an open pull request
completely ungated by this module; it is caught only once the PR exists and CI
runs on it. That gap is real, it is later than the epic wants, and it is
recorded here rather than discovered later: the gate is shipped as a library
first (`evaluate`, `build_decision`, `load_rules`) precisely so the pre-commit
hook, the Publication Gateway, or a later dispatch-time binding can each adopt
the same decision function without re-deriving the comparison, the moment
their own chokepoint becomes real.

## Decision

Ship `bin/nightshift_conformance.py` (library + CLI, stdlib-only, no new
detection logic -- it imports `nightshift_project_contract` and adds nothing to
its 16-field envelope) and `program/conformance-rules.json` (the RULES layer).
`evaluate`/`permitted_languages`/`permitted_test_frameworks` accept only FACTS
and RULES; `attach_advisory` is the only path EXPERIENCE-layer content can take
onto a decision, and it is digest-verified to be inert. A base field with
`status: absent` contributes the empty set to the permitted side rather than
exempting itself from the diff (the absent-base-field rule, above), so the
first-ever introduction of a language or framework the base never observed
is a DENY like any other widening, not a free pass. Wire one step into the
existing `control-plane tests` CI job, PR-only. Exit codes: `0` allow, `1`
unexpected operation failure, `2` usage error, `3` refusal (`verdict: deny`,
`reason_code: project_contract_violation`, for an actual contract violation;
`verdict: rules_invalid`, `reason_code: rules_invalid`, for a malformed RULES
file -- a different failure in kind, sharing the exit tier the repo-wide
vocabulary assigns to refusals), and **`4`, indeterminate** -- the one new exit
code this wave grants, meaning specifically "the gate could not see what it was
judging" (an unresolvable SHA, an invalid contract, an unreadable rules file, a
vacuous base-equals-head comparison, or a supplied contract file whose digest
does not recompute). `indeterminate` must never be reachable as an `allow`; no
module besides this one may use exit code `4`.

`repository_id` is attached to every decision as the DEPLOYMENT identity,
resolved through `nightshift_authority.scope_from_env()` -- never re-spelling
`ENV_STATE_ROOT`/`ENV_REPOSITORY_ID` here, never inventing a second repository
identifier. It is excluded from `decision_digest`, which stays over exactly
`(verdict, violations, base contract_digest, head contract_digest,
rules_digest)` -- the CONTENT identity, environment-independent.
`baseline_sha`/`contract_digest` remain the content axis; `repository_id` is
the deployment axis; neither is coerced into the other.

**Known integration dependency, stated rather than discovered:** at the time
this module was built, `bin/nightshift_authority.py` (a concurrently-developed
sibling stream's deliverable this wave) did not yet export `scope_from_env`.
The import is guarded (`try: from nightshift_authority import scope_from_env
except ImportError: scope_from_env = None`) specifically so this module stays
fully importable and testable before that stream lands; `_resolve_repository_id`
degrades to `repository_id: null, repository_id_source: "not_in_environment"`
in that case -- the identical outcome as when the symbol exists but the two
environment variables are simply unset. Once the sibling stream lands, this
becomes a live call with no code change required here. Verified: the module
imports and every scenario above runs correctly today, with `repository_id`
correctly null throughout.

## Consequences

A pull request that introduces an undeclared language or an undeclared test/
integration/e2e framework is denied by name, with the evidence paths that
introduced it, before human review has to notice it by reading a diff. A rule
change (adding a third rule kind, tightening a field list) is a reviewed,
digested, single-file change, not a code change. A legitimate stack migration
requires an ADR and a named waiver rather than a silent contract update. A
repository that has never declared a language, build tool, or test/
integration/e2e framework at all gets no grace period for its first one --
the absent-base-field rule denies that introduction exactly as it would deny
a second, undeclared addition to an already-populated field.

It does not stop such a change from reaching an open pull request in the first
place (see "Where a denial can actually stop something," above) -- only from
merging cleanly, and only as far as this repository's release procedure
actually enforces a red check. It does not evaluate module-dependency or
migration-pattern rules (a different, later rule KIND). It does not detect a
language or framework being silently REMOVED from the declared set -- only
widening is checked this wave; narrowing is a symmetric, deliberately
undecided question left for a human to rule on (see Reversal trigger).

## Cost

No new process, no new dependency, no new database. Two contract compiles per
gate run (base + candidate), measured at 0.45s each against this repository's
own ~1,200 tracked files -- roughly 1s added to a CI run that already clones
the full history. Cost scales with repository size (a 55k-file repository
compiles slower); the mitigation when it bites is #400's stored-contract
boundary plus the `--contract-base`/`--contract-head` file arguments this CLI
already accepts and digest-verifies, not an optimization inside the gate
itself.

## One-way door?

No. The RULES file is additive and versioned (`schema_version`); a rule kind
can be added at the end without disturbing existing ones, per this repository's
own "never renumber, never silently retire" convention for stable identifiers.
The CI step can be removed or relocated to a different chokepoint (the pre-
commit hook, the Publication Gateway) without changing `evaluate`'s decision
function, which is the entire point of shipping it as a library first.

## Scale tier

Below the smallest named tier in the scale ladder: single-process, no shared
state, no new storage, cost bounded by two contract compiles per PR push. The
trigger that would move it: a repository large enough (tens of thousands of
tracked files) that two full compiles per push become the dominant cost of the
CI job, at which point #400's stored-contract boundary is the intended next
step, not a change to this module's shape.

## Reversal trigger

Revisit if: (a) `nightshift_publish.py`'s stdin-controller import boundary
changes (a fixed `PYTHONPATH` becomes injectable, or the controller stops
running under `python3 -I -`), which would make worker-level enforcement
possible and this module's CI-only wiring insufficient by comparison; (b) a
repository's detection scope produces a false-positive `undeclared_language`
or `undeclared_test_framework` from a fixture or vendored directory outside
`nightshift_project_contract.py`'s known exclusion list (`FIXTURE_DIRNAMES` /
`WELL_KNOWN_VENDOR_DIRNAMES`) -- exactly the failure shape commit `8541e10`
already fixed once for the detector itself, now inherited one layer up; or
(c) a human rules on the open question of whether narrowing the declared
stack (removing a language or framework the base contract named) should also
be a violation -- symmetric to widening, not built this wave, and not decided
here.
