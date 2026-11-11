# ADR-0008: The refuse-invalid-input convention applies control-plane-wide, at every ttl-taking verb

Date: 2026-08-23 · Status: **proposed** — awaiting `staff` review
Deciders: crew_alpha lead (implementing program decision **R-55**, `.omni/decisions.md`) · Proposers: crew_alpha lead (see "On the missing debate")
Conversation: 0a5a14788cd54345a980b25c84906340 · Spec: none — issues #88 and #86 are the spec
Issues: #88, #86 · Wave 3 · Base `c4a21f5394449d137473e2ede3e3fb00c537290f` · PR #90

**Extends [ADR-0007](ADR-0007-refuse-invalid-control-plane-input.md).** ADR-0007's
Decision is unchanged and is not rewritten by this ADR. ADR-0007 carries a
back-link to this one in its scope note and debt table.

## Context

ADR-0007 established that invalid control-plane input is refused loudly rather
than clamped or silently accepted. It was written at **control-plane altitude**
— "the control plane refuses invalid input" — but `staff` raised a blocking
`inconsistent-with` finding because the code enforced it at **two-verb
altitude**: only `claim renew --ttl` and `checkpoint create --ref`. crew_alpha
resolved that finding the honest way, by NARROWING the ADR to the two verbs it
actually shipped and registering the remaining sites as debt (ADR-0007, rows for
`cmd_slot_acquire` and `cmd_lease_acquire`).

**This ADR pays that debt.** Rather than lowering the principle to match the
code, the code rises to the altitude the principle was always written at. That
is the point worth recording: an ADR narrowed under review is a promissory note,
and this is the payment.

`staff` reproduced the identical unguarded `t + ttl` shape live at five more
sites. They split into two failure modes with one root cause — `ttl` is
arithmetic input that was never validated:

**Renewal verbs** (`slot heartbeat` ~578, `slot renew-active` ~626) compute
`expires_at = t + ttl` unconditionally. A negative ttl back-dates it, so the
call that was meant to EXTEND a lifetime instead expires the row at the instant
it is granted — **while returning `ok: true`**. This is not theoretical: R-26's
published hourly ttl formula is negative in the last 180 seconds of every hour.

`slot heartbeat` is the ADR-0002-mandated path **every worker calls every
turn**, and `slot renew-active` renews **every active slot in one call**. A
negative ttl there silently releases live workers' slots, which is confirmed
defect 6 ("the worker budget is not global") re-entering through the renewal
path. That is what makes this urgent rather than tidy.

**Acquire verbs** (`slot acquire` ~510, `lease acquire` ~745, `claim acquire`
~658) used `t + ttl if ttl > 0 else None`, where NULL means never-expires. A
non-positive ttl therefore fell back to an **immortal row** that `reap` can
never free, so every future acquire on that id returns exit 3 forever — a
permanently wedged task or resource. It is the exact inverse failure: the
renewal verbs expire too eagerly, the acquire verbs never expire at all.

## Options considered

### A — Guard all five sites, refuse with exit 2 (**CHOSEN**)
Validate the resolved ttl at each site; on a non-positive or non-finite value,
emit a reasoned JSON refusal and exit 2, matching ADR-0007's existing
convention (2 = usage error; 3 stays reserved for denied/held/cooling).

### B — Clamp a bad ttl to a sane default (**REJECTED**)
Superficially attractive: no caller ever breaks, the control plane "self-heals".
Rejected because **clamping hides the caller's bug forever**. The caller most
likely to produce a negative ttl is R-26's own published formula, and a clamp
would convert a systematically wrong formula into an invisible one — the
control plane would quietly paper over a defect in the org's documented
procedure, and nobody would ever be told to fix the formula. This project has
already shipped eight confident wrong answers of exactly that shape: a control
accepting malformed input and reporting success anyway. A clamp would be a
ninth. The whole value of a control is that it says no.

### C — Fix only the two #88 renewal verbs, defer the three acquire verbs (**REJECTED**)
Tempting on scope grounds: #88 is the P1 with the live blast radius, and the
acquire verbs "only" wedge a resource. Rejected for two reasons. First, it is
the *same one-line guard* — deferring costs more in coordination than it saves
in work. Second, and decisively, **leaving siblings in place is precisely how
this defect family keeps re-entering**: PR #85 fixed one site, and the family
came straight back at five more. A defect family is closed at every site in one
change or it is not closed.

### D — Validate centrally in argparse via a custom `type=` (**REJECTED**)
Attractive because it would be one line touching one place, and would guard
every current and future `--ttl` automatically. Rejected on two concrete
findings, the second of which we hit empirically while writing the tests:

1. **It guards the wrong value.** `ttl` is `args.ttl if args.ttl is not None
   else cap["<x>_ttl_seconds"]` — an argparse `type=` never sees the
   `capacity.json`-derived default, so a corrupt capacity file would flow
   straight through unguarded. The guard must sit *after* default resolution,
   which is where this ADR puts it.
2. **It breaks the machine-readable contract.** argparse failures print human
   prose to stderr and exit 2 with no JSON body. Every caller of this CLI parses
   stdout as JSON. We observed this directly: `--ttl -inf` is parsed by argparse
   as an unknown *option* (it does not match argparse's negative-number
   pattern), producing exit 2 with no JSON — an exit code that *looks* like a
   correct refusal while the guard was never reached. A test asserting only the
   exit code would have passed vacuously. The tests therefore spell the flag
   `--ttl=<v>`, and this is recorded here because it is a trap for the next
   person.

### E — Five inline copies of the guard vs one shared helper (**shared helper chosen**)
Five copies would drift. One `require_valid_ttl(ttl, verb, subject)` helper is
called from five sites. The mutation-testing requirement is still met at
per-site granularity because the mutation applied is **deletion of one call
site**, not corruption of the helper: defeating the guard at one verb fails only
that verb's tests and leaves the other four passing (evidence below).

## Decision

**Every ttl-taking control-plane verb refuses a non-positive or non-finite ttl,
with exit 2 and a reasoned message. No clamping. No silent never-expire
fallback.**

Applied at all five remaining sites — `slot heartbeat`, `slot renew-active`,
`slot acquire`, `lease acquire`, `claim acquire` — joining `claim renew`
(ADR-0007 / PR #85). Together these are every `--ttl` in the CLI.

The predicate is `not (ttl > 0) or not math.isfinite(ttl)`:

- `not (ttl > 0)` rather than `ttl <= 0` — deliberate, because it also catches
  **NaN**, for which every comparison is False.
- `math.isfinite` is a separate clause because **`inf > 0` is True**, so a `> 0`
  test alone passes an infinite ttl through and `expires_at` becomes infinite —
  the never-expire wedge by another route. This is the subtlety PR #85 found for
  `claim renew`; it is preserved verbatim here.

The `t + ttl if ttl > 0 else None` fallback is **removed** from the three
acquire verbs. With the guard in front of it, that branch is unreachable, and
leaving dead never-expire code in the file is an invitation to restore the bug.

### The serialize carve-out
`lease acquire --mode serialize` sets `expires = None` **legitimately**: a
serialize lease is held until its PR merges or is dropped, precisely because the
ordering it protects runs through review. The guard is therefore scoped to the
ttl branch only and must never fire in serialize mode. This is the one place in
the control plane where never-expires is correct, and it is pinned by a control
test so a future tightening cannot silently break it.

## Consequences

**Easier.** A bad ttl now fails at its source with a message naming the verb and
the value, instead of corrupting a row and reporting success. The class of bug
where a worker keeps running while its slot has been handed to someone else is
closed at every known site.

**Harder.** Any caller passing a non-positive ttl now gets a hard failure where
it previously got `ok: true`. **This is a deliberate cross-seam behaviour
change** and the real question on this PR.

The program's call (R-55), which I agree with and record here as the reasoning
rather than merely the ruling: a worker that today passes a bad ttl **already**
loses its slot — silently, while being told it succeeded. After this change it
gets exit 2 and a message. **A loud refusal is strictly safer than a silent slot
release**: it can only convert an invisible failure into a visible one. There is
no caller that is better off with the old behaviour, because the old behaviour
did not do what the caller asked either.

`crew_beta` consumes ADR-0002's liveness semantics and is the cross-crew
reviewer for PR #90. If it disagrees, that objection lands as a blocking review
finding — the right channel — rather than as a design round-trip before the work
exists.

**Foreclosed.** "Never expires" is no longer expressible for slots, claims, or
edit leases. Serialize leases remain the single sanctioned never-expires path.

## Cost

Zero marginal cost: five call sites and one helper, no new dependency, no new
process, nothing to run, patch or monitor. At 100× volume it is still five
comparisons per command. The only operational surface is the possibility of a
caller newly failing loudly — which is the intended effect, not a side effect.

## One-way door?

**No.** Reverting is deleting six lines. No data shape changes, no migration, no
persisted format change. Rows written before and after this change are
indistinguishable except that invalid ones are no longer written.

## Scale tier

None yet. This is input validation on a single-box SQLite control plane and does
not sit on the scale ladder.

## Reversal trigger

Revisit if **either** holds:

1. A legitimate caller emerges that genuinely needs an unbounded lifetime for a
   slot, claim, or edit lease — i.e. a real use case for never-expires outside
   `--mode serialize`. The correct response would be an explicit
   `--no-expiry` flag, never a resurrection of `ttl <= 0` as a magic value.
2. The refusal is observed to break more runs than the silent corruption did —
   concretely, if exit-2 refusals from these five verbs appear in the ledger
   more often than the slot-release and wedged-resource incidents they replaced.
   Measure before reverting; the pre-change failures were invisible by
   construction, so a naive count will be biased toward the old behaviour.

## Evidence

Full commands and output are in PR #90's comments.

- **Pre-fix counterfactual (WA-008).** The pre-fix binary is extracted from
  base `c4a21f5` and driven via `NIGHTSHIFT_TEST_CTL`. All 25 refusal cases
  (5 sites × `-5`, `0`, `inf`, `-inf`, `nan`) and every behavioural harm test
  FAIL against it; all pass against the fixed binary.
- **Control (WA-009).** The `Controls` class — valid ttl at all five sites,
  omitted ttl using the capacity default, the serialize carve-out, and ordinary
  TTL expiry — PASSES against **both** binaries. That is what makes the
  failures above attributable to the guard rather than to a broken harness.
- **Mutation (per site).** Deleting one guard call site at a time yields five
  mutants; each fails **only** its own verb's test class and leaves the other
  four passing.
- **Suite.** 282 tests OK at base `c4a21f5`; 298 OK on this branch. Delta of
  exactly +16, all in `tests/test_ttl_family.py`.

## On the missing debate

Phase 1 of this crew's charter requires two different-vendor engineers to
propose competing approaches before the lead reconciles them. **That did not
happen, and could not.** Grandchild dispatch is broken (#13). `sys_session_send`
to `pm` was re-probed once, fresh, in this session — not assumed from an earlier
wave — and returned verbatim:

    Error: sys_session_send requires parent session inbox

Under the charter's fallback clause the lead builds directly, holding the
pre-granted worker slot 83 and registry edit lease 4. **Stated plainly: there
was no in-crew PM sign-off, no in-crew reviewer, and no in-crew QA gate on
PR #90. Independence is supplied solely by cross-crew review.** This is
disclosed in the PR body as well as here, rather than papered over by claiming
gates that did not run.

What stands in for a second proposer is executed evidence: every claim above
that could have gone the other way is backed by a command and its output, and
option D was rejected on a trap we hit empirically rather than on taste.
