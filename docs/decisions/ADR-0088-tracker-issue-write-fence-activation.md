# ADR-0088: Activate the tracker issue-write fence at the bundle root

Date: 2026-09-05 · Status: proposed
Deciders: program · Proposers: platform (Claude)
Relates to: ADR-0085 (external-repository PR-only merge fence — the sibling
mechanism this decision matches in posture and, previously, in gap: a fence
that existed but was never wired in) · ADR-0057 (FOLLOW_UP tickets queued,
not dispatched — the traffic this fence protects)
Spec: the accompanying verifier report on stream "Follow-up tickets can be
filed into the CUSTOMER's tracker"

## Context

A prior change on this same branch (`fix(tracker): bind every gh issue
write to Nightshift's own tracker, mechanically`) built the full mechanism:
`bin/nightshift_tracker_authority.py` (a write-once tracker-repository
binding), `deploy/policies/nightshift_issue_fence.py`
(`tracker_issue_write_fence`, a Service-Policies-V0 tool-call evaluator),
and `bin/nightshift-file-issue` (the sanctioned wrapper). It rewrote the
three prompt/skill files it owned to name the wrapper, and disclosed —
honestly, in its own commit message and in three separate docstrings — two
gaps it could not close itself: `program/config.yaml` is `platform`
single-writer per `docs/owners.md`, outside that change's file ownership,
and:

1. carries four more bare `gh issue edit`/`gh issue close` prompt lines
   (lines 628/631/648/658 at the time) that were never rewritten, and
2. is the **only** attachment point that makes any Service-Policies-V0
   policy live for tool calls at all — #118 proved a per-seat declaration
   never loads — so without an entry here, `tracker_issue_write_fence` is
   importable and fully tested but never actually intercepts anything a
   running agent does.

The adversarial verifier that reviewed that change called both gaps
blockers: "nothing in the running system denies anything," and "the gap
statement's DONE WHEN … is true only in a unit test, not in the
deployment." `docs/owners.md`'s own text for `program/**` requires an ADR
for exactly this reason — an unreviewed edit to the org's own constitution
is how a run silently weakens its own controls — so this decision is that
ADR, and makes the two edits it authorizes in the same change.

A third, independent finding from the same review is folded in here rather
than opened as a separate ADR because it touches the same file this
decision already has to justify editing carefully: `bin/nightshift-
tracker-init.sh`'s own tracker-binding step (added by the same prior
change) declared the binding using `$OWNER/$REPO` — values read via `gh
repo view` from whatever directory the script happened to be invoked
against — whenever `NIGHTSHIFT_STATE_ROOT`/`NIGHTSHIFT_REPOSITORY_ID` were
set in the environment. Those two variables are ambient on the runtime
host (systemd `EnvironmentFile`, per ADR-0085's own scope definition) —
present regardless of which repository this bootstrap script is being run
against. A disclosed, legitimate use of this script is bootstrapping a
**customer's own** GitHub Project board while those two variables are
simply present in the environment; running it there would have
auto-declared the customer's repository as Nightshift's own tracker,
permanently (the binding is write-once), after which the fence this
decision activates would ALLOW writes to the customer's tracker and DENY
writes to Nightshift's real one. This is precisely the cwd/`git
remote`-derived source `deploy/policies/nightshift_issue_fence.py`'s own
docstring disclaims as a value source ("never `git remote`, never
`os.getcwd()`, never `.omni/tracker.json`") — the bootstrap script had
reintroduced it one layer up.

## Options considered

### A — Declare the fence, rewrite the four remaining lines, and require an explicit non-cwd signal before the init script auto-declares (proposed by platform (Claude), chosen)

Add `tracker_issue_write_fence` to `program/config.yaml` at the bundle
root, in the same style and immediately beside `external_repo_merge_fence`
(ADR-0085) — both are FAILS-CLOSED, no-escape-hatch policies over the same
`gh` surface. Rewrite the four bare `gh issue edit`/`gh issue close` lines
in the DoR/heartbeat prose to name `nightshift-file-issue` instead, exactly
as the three already-owned prompt files were rewritten. Change
`bin/nightshift-tracker-init.sh`'s auto-declare step to require a THIRD,
explicit environment variable, `NIGHTSHIFT_TRACKER_SLUG` — a value an
operator sets ONLY when this specific run is bootstrapping Nightshift's
own tracker, entirely independent of `$OWNER`/`$REPO` — rather than firing
on the ambient presence of the other two.

Chosen because it closes exactly the three named gaps with the smallest
possible diff to a single-writer file, reuses an already-accepted pattern
(ADR-0085's own bundle-root declaration) rather than inventing a new one,
and because the third fix removes a real write-once foot-gun without
removing the convenience the original author of that step was going for —
an operator who deliberately means to bootstrap Nightshift's own tracker
still gets it declared automatically, in the same run, by setting one more
variable they already know the value of.

### B — Declare the fence only; leave the four prompt lines and the init-script issue for a later, separate ADR (rejected)

Splitting the fence declaration from the prompt rewrite would leave a
`tracker_issue_write_fence` that DENIES the four lines' bare `gh issue
edit`/`gh issue close` calls at runtime with no rewritten instruction
telling the agent what to do instead — trading a silent gap for a loud,
confusing one on the very next heartbeat triage pass. The two edits belong
in the same change because the mechanism and the prompt that must agree
with it are the same four numbered DoR/heartbeat steps.

### C — Auto-declare the tracker-init binding whenever the state-root/repository-id scope is set, but only when the CLI is invoked with no repo-path argument (defaults to `$PWD`) (rejected)

This was considered as a narrower signal than a fourth env var — "only
auto-declare when the operator ran the script bare, from inside the
checkout, with no explicit path" — but it does not actually distinguish
anything: an operator bootstrapping a customer's board from inside that
customer's own checkout, which is the disclosed common case, would also
invoke the script with no path argument. The signal has to name the
*intent* ("this is Nightshift's own tracker"), not the *invocation shape*,
which is why the decision uses an explicit, separately-named variable
instead.

### D — Do nothing further; leave the mechanism built but undeclared (rejected — the status quo the verifier flagged)

This is the state the prior change left the repository in, and the
verifier's report is direct about the consequence: "today an agent inside
a customer checkout that types `gh issue create --title x` meets zero
mechanical resistance." Prose-only enforcement is exactly the class of
thing ADR-0085's own Context section already named as a failed control.

## Decision

Three edits, in this change:

1. **`program/config.yaml`** gains one policy declaration, placed
   immediately after `external_repo_merge_fence` (both are issue/PR-write
   fences over the same `gh` surface, both fail closed with no
   `..._FAIL_OPEN` escape hatch):

   ```yaml
   tracker_issue_write_fence:
     type: function
     on: [tool_call]
     function:
       path: nightshift_issue_fence.tracker_issue_write_fence
       arguments: {}
   ```

   and its four numbered DoR/heartbeat-triage lines that named a bare `gh
   issue edit`/`gh issue close` (previously at 628/631/648/658) are
   rewritten to name `nightshift-file-issue edit`/`nightshift-file-issue
   close` instead — the same substitution the three already-owned prompt
   files received in the prior change.

2. **`bin/nightshift-tracker-init.sh`**'s tracker-binding step now declares
   only when `NIGHTSHIFT_STATE_ROOT`, `NIGHTSHIFT_REPOSITORY_ID`, **and**
   `NIGHTSHIFT_TRACKER_SLUG` are all set, and declares the value of
   `NIGHTSHIFT_TRACKER_SLUG` itself — never `$OWNER/$REPO`. When the third
   variable is absent (the two ambient ones may still be set, for the
   disclosed customer-board/dry-run uses), the step is skipped and the
   script prints the same manual-declare instructions it already printed
   for the "nothing set" case, naming `$OWNER/$REPO` only as text an
   operator reads before deciding, never as a value this script writes
   itself.

3. **`deploy/policies/nightshift_issue_fence.py`**'s embedded-payload scan
   (`_expand_embedded_gh_payloads`) is widened to recognise `-c` however
   many ordinary preamble flags precede it (`sh -e -c`, `bash --norc -c`,
   `bash -o pipefail -c`) and however it is fused into a leading
   short-option cluster (`bash -lc`, `zsh -ic` — the single most common
   agent shell-tool wrapper shape). The prior version required `-c` to sit
   literally adjacent to the interpreter word, which the verifier
   reproduced as a live regression against the immediately preceding
   commit on this same branch. See that module's own docstring for the
   full mechanism and the one narrower gap (an unenumerated
   value-consuming preamble flag) this widening still discloses rather
   than claims closed.

`tests/test_tracker_issue_fence.py::CallSitesAgreeWithMechanism` is
extended to cover `program/config.yaml` the same way
`tests/test_external_repo_merge_fence.py::ReleaseConfigAgreesWithMechanism`
already covers it for `external_repo_merge_fence`: the qualified function
path must appear at the bundle root and must not appear on any seat
config, and the same bare-write-verb regex that scans the three
already-owned prompt files now also scans `program/config.yaml`.
`tests/test_tracker_init.py` gains coverage for the new three-variable
gate, including the case that most directly proves independence from cwd:
`NIGHTSHIFT_TRACKER_SLUG` set to a value that DIFFERS from the fake `gh`
harness's own `$OWNER/$REPO`, asserting the DIFFERENT, explicitly-set
value is what gets declared.

## Consequences

**Easier:** the fence built in the prior change actually does something.
An agent working inside a customer-shaped checkout that types a bare `gh
issue create` (or has it typed for it by a rewritten heartbeat-triage step
that regressed back to the bare form) is mechanically denied, not merely
discouraged by prompt text the same worker's own pull request could edit.

**Harder / new cost:** every deployment that already runs
`nightshift-tracker-init.sh` with `NIGHTSHIFT_STATE_ROOT`/
`NIGHTSHIFT_REPOSITORY_ID` set and relied on the (buggy) auto-declare must
now also set `NIGHTSHIFT_TRACKER_SLUG` to get the same automatic behavior —
an intentional breaking change on upgrade, for the same reason ADR-0085
made its own ownership declaration an intentional breaking change: the
alternative (keep deriving from cwd) is the bug, not a feature worth
preserving compatibility for.

**Foreclosed, on purpose:** a first run of the initializer inside the
wrong checkout can no longer permanently bind Nightshift's tracker to a
repository Nightshift does not own, just because the two ambient scope
variables happened to be set.

**Named and not claimed fixed:**
- `deploy/nightshift-watchdog.sh`'s own bare `gh issue comment` (a
  real, executing runtime call, resolved from `$PROJECT_DIR`) is outside
  this decision's scope: `deploy/**` (except `policies/`) is `crew_beta`'s
  seam, single-writer, and this ADR does not claim a lease there. Recorded
  so it is not silently lost; whoever owns that path decides whether it
  needs its own fix.
- The env-variable overrides `gh` itself documents (`GH_HOST`, `GH_REPO`)
  are not inspected by the fence's own argv classification, only by the
  wrapper. This is a pre-existing gap, not introduced or closed by this
  decision; see the fence module's own disclosed limits for the current
  state.
- Every limit `deploy/policies/nightshift_issue_fence.py`'s own docstring
  already names — the uid that owns `state_root` can still overwrite
  `tracker.db` directly; an interpreter running arbitrary code that itself
  shells out is not seen through — is unchanged by this decision.

## Cost

**Today:** zero new infrastructure. One additional bundle-root policy
entry evaluated per tool call (a cheap `shlex` pass that returns
immediately for the overwhelming majority of calls, which name no `gh`
invocation at all). One additional required environment variable for
deployments that want the tracker-init script's automatic declare
behavior; the manual `nightshift_tracker_authority.py declare` path this
decision does not change remains available unconditionally.

**At 100×:** unchanged, for the same reason ADR-0085 gives: the policy's
cost per tool call does not scale with dispatch volume, PR volume, or
worker count.

## One-way door?

No. The bundle-root declaration is one line, removable in one line. The
prompt rewrite is a text substitution, reversible the same way. The
`NIGHTSHIFT_TRACKER_SLUG` gate on the init script changes an operator
runbook step, not any persisted data shape — the write-once tracker
binding itself was already one-way (by ADR, in the prior change), and this
decision does not alter that.

## Scale tier

None yet (T0), per `docs/architecture/SCALE-LADDER.md`. Same tier and same
trigger as ADR-0085: unchanged unless a single deployment needs to operate
on more than one tracker repository concurrently, which neither this
decision nor the prior change's design supports.

## Reversal trigger

Revisit this decision if: a legitimate, correctly `--repo`-pinned issue
write is denied once this fence is live (a false positive means the argv
classifier has a bug, not that activating the fence was wrong); or if an
operator reports the `NIGHTSHIFT_TRACKER_SLUG` gate as a real workflow
burden significant enough to warrant a different, still non-cwd-derived
signal.
