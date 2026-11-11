# ADR-0014 — Corrections to ADR-0013: a false premise, an undercount, and an ancestry gate that cannot pass

- **Date:** 2026-08-24
- **Status:** Accepted
- **Deciders:** `program` (heartbeat-0800, reconciliation and D10/ancestry findings), `staff` (Claude,
  mandatory ADR review under Rule 5, and the retraction of its own two errors), `architect` (Codex) and
  `security` (Claude) via their independent blocking reviews of PR #134.
- **Conversation:** program `heartbeat-0800`, run `run-20260822-selfdogfood`, wave 8.
- **Amends:** ADR-0013 D1, D5, D10, and Amendments A2.1 / A2.5. **Does not reverse any ADR-0013 decision.**
- **Sequencing note:** ADR-0015 supersedes C7 narrowly: only the reviewed
  `gate_pushes: false` compatibility value gates the horizontal cutover; the
  unconditional floor remains deferred work in #129.
- **Also requires a back-reference edit to ADR-0010** (a link, not a decision change — see C1).
- **Inputs, verbatim:**
  - `staff` ADR-0013 review — `#115#issuecomment-5392454978`
  - `architect` PR #134 cross-vendor review — `#134#issuecomment-5392441887`
  - `security` PR #134 gate — `#134`, comment of 2026-08-24 08:17:28Z

## Context

ADR-0013 was accepted before it had its mandatory `staff` review (Rule 5), because five consecutive attempts
at that review died to upstream 529 errors. The sixth succeeded. It returned `inconsistent-with ADR-0010`
and, more usefully, **retracted two of its own load-bearing claims** — the evidence for ADR-0013's D5 and D4
was `staff`'s, and `staff` is the seat that found it wrong.

Separately, the first of ADR-0013's two contracted changes (PR #134) was blocked by two vendors
independently, and that review surfaced a defect in D10's definition of change 1.

None of this reverses ADR-0013. Its shape — one sealed release root, five assertions, prove-or-kill before
live work — survives every correction below. What changed is that three of its factual premises were wrong
and one of its gates cannot pass as written.

## Corrections

### C1 — ADR-0010's "One-way door?" is not corrected where an operator will look. (From `staff`.)

ADR-0010 §"One-way door?" (`:464-467`) still reads *"reversal is removing the flag and restarting."*
ADR-0013 D6 establishes that this is **false** — the warm cache keeps serving the new bundle, so removing
`--agent` changes nothing, and rollback must be a re-registration of a pre-cutover bundle. But `grep 0013`
over ADR-0009, -0010, -0011 and -0012 returns **empty**: the correction exists only in the amending
document.

**Decision.** Add a back-reference in ADR-0010 pointing at ADR-0013 D6, and one in ADR-0011 pointing at
ADR-0013's amendments to (a) and (c). This is a link, not a decision edit, so it does not require a
superseding ADR. **An amendment that lives only in the amending document is one nobody finds during the
rollback it exists to govern** — and a rollback is precisely when a reader arrives at the *old* document.

### C2 — D5's premise is false. The systemd user bus is UP, and has been since before the ADR was written.

D5 states the user bus is **down** (`Failed to connect to bus: No medium found`) and reasons that the
installed unit is inert-but-armed, firing only on reboot. `staff` verified the opposite and retracted its
own claim: **`systemd --user` is pid 790, uid 1000, running since Aug 22 17:48:57.** The original reading
came from a shell with `XDG_RUNTIME_DIR` unset — *a tool's error message read as a fact about the system*.

The unit is `disabled` and `inactive`, so **D5's trigger already fired and nothing happened.** The hazard is
therefore not a reboot; it is one `systemctl --user enable --now`, after which `Restart=always` and
`WantedBy=default.target` make it permanent.

**Decision.** D5's conclusion stands and its severity is unchanged, but its mechanism is restated: the
remedy is **`systemctl --user mask`**, not "leave exactly one". Masking is the only state that survives an
`enable`. D5's original "make the launch contract identical in both paths, or leave exactly one" is retained
as the principle and `mask` becomes the mechanism.

**Rejected:** *leave the unit disabled and document it* — disabled is one command away from enabled, and the
whole class of defect this ADR addresses is a state that looks safe until someone runs the obvious command.

### C3 — D1 undercounts the stale-tree surface: four behaviours, not three.

D1 names three live behaviours fed by `/srv/nightshift` (policy import path, `--agent` target, `nightshift-ctl`
on `PATH`). `staff` found a fourth: **`nightshift-watchdog.service:14` execs `/srv/nightshift/deploy/nightshift-watchdog.sh`.**
Six user units exist in total.

**Decision.** The count is four. This matters beyond arithmetic: D5's remedy would have claimed "exactly one
supervision path" while a fourth exec from the stale tree remained, **falsifying its own completion
criterion at the moment it was declared done.** The cutover's completion check must enumerate the units
mechanically rather than assert a number.

### C4 — A2.1 and A2.5 are overclaimed. (Narrowly blocking; `staff` and `architect` agree independently.)

- **A2.1** claims `agent_id` survives re-registration unconditionally. The evidence supports it only for
  the pinned Omnigent build `2026-08-19T00:52:00Z`, via the **serial startup `--agent` registration path**.
  It establishes nothing about another registration path, concurrent registration, or a future build — and
  it **contradicts A2.2 in the same amendment**.
- **A2.5** is graded RED on controls that exit 0 on both inputs. By ADR-0013 D3's own rule, an unreadable
  operand is `undetermined` and **never inherited green**. A control that cannot fail is `undetermined`, not
  RED.

**Decision.** Rescope A2.1 to the pinned build and startup path, and require the capability test to re-run on
any dependency or path change. Regrade A2.5 from RED to **`undetermined`** until its controls can fail. The
positive finding — that `agent_id` did survive, on that build, by that path — stands on its raw transcript
and is not withdrawn.

### C5 — "Change 4" and "D-step 4" are different steps and must not be conflated.

`program` conflated them in a charter and was corrected. **"Change 4" (dead per-seat policies, #118) is
correctly split out** of the repo work: `runtime/policies/builder.py`, `routes_hooks.py` and
`_sessions/orchestration.py` live in the installed `omnigent` package and exist in **no** repository, so
"prove LOAD and APPLY by execution" is unreachable from a repo PR. **"D9 step 4" (the in-process import and
DENY-canary proof) is NOT deferred** — it is the proof the entire DENY floor rests on.

**Decision.** Recorded here because acting on the conflation would have deleted the load-bearing proof while
believing it was deferring unrelated work. D9 holds in full; #118 was never on its critical path.

### C6 — D10's ancestry gate cannot pass from the delivery branch. (Found by `staff`, verified by `program`.)

D10 requires the cutover SHA to be a descendant of `main` `c28d3147cc85f58dd186f7fca8429afe5fdcec1c`.
Verified directly:

| check | result |
|---|---|
| `git merge-base --is-ancestor c28d3147 origin/main` | exit **0** — is an ancestor |
| `git merge-base --is-ancestor c28d3147 origin/program/discovery-state` | exit **1** — is **not** |
| `git merge-base --is-ancestor c28d3147 c2522222` (PR #134 head) | exit **1** — is **not** |
| `git rev-list --left-right --count origin/program/discovery-state...origin/main` | **7 ahead, 8 behind** |
| merge-base | `2f0b31bc085b65d9ce9a43cb68b1bf597ccb1e2a` |

`main` and the delivery branch have **diverged**, because `release` promotes with merge commits that are
never brought back. **No SHA on the delivery branch can ever satisfy D10's own gate.**

**Decision.** The cutover SHA is taken from **`main`**, after both ADR-0013 changes are promoted there. The
cutover is gated on **two testbed A, not one**.

**Rejected:** *weaken the gate to accept a delivery-branch SHA* — the gate exists because `main` is the only
branch with a merge gate in front of it, and adjusting a check so that current state passes is exactly the
failure mode ADR-0013 was written to end. *Rebase the delivery branch onto `main`* — rewrites published
history every agent works from; denied by policy and undesirable regardless.

### C7 — D10's definition of change 1 is incomplete. (From the PR #134 reviews.)

D10 defines change 1 as repo-authoritative `gate_pushes: false` plus an unconditional DENY floor outside the
gated branch. Two vendors, reviewing blind, showed that satisfying that sentence literally still leaves the
org worse off:

- **The floor is declared at a displaceable level.** Per-seat `guardrails.policies` **replace** rather than
  union (`builder.py:358-359,393-394`); the floor is in the root spec and in **none of the 10 seats**. It
  works today only because #118 prevents per-seat policies loading at all. **Fixing #118 would silently
  delete the floor everywhere.**
- **"Outside the gated branch" is necessary but not sufficient.** The tokeniser defaults to ALLOW on
  anything it cannot classify, and is *narrower in reach* than the naive regexes it replaces: 20 cases
  regress ASK → ALLOW, including `terraform apply`, `kubectl apply` and `databricks bundle deploy`.

**Decision.** Change 1 additionally requires: (i) the floor declared at a site no per-seat block can
displace — the server `--config` top-level `default_policies` — or, failing that, in all 10 seat blocks plus
a corpus test asserting every spec carries it; (ii) no net ASK → ALLOW regression on the irreversible set,
measured by an enumerated before/after battery rather than asserted; (iii) the classifier fails **closed** on
constructs it cannot parse.

**Rejected:** *merge change 1 now and repair reach in change 2* — `deploy/README.md:317` already tells
operators in present tense that the always-wrong subset is denied. Shipping a floor with known holes behind
documentation that says otherwise is a worse failure than shipping no floor.

## Consequences

- Cutover change 1 slips a wave. Both PR #134 reviewers re-check before it is handed to `release`.
- The cutover requires two testbed A to `main` before a candidate SHA exists.
- ADR-0010 and ADR-0011 gain back-references; neither decision changes.
- ADR-0013 A2.5 is regraded `undetermined`, so any claim resting on it is `undetermined` until its controls
  can fail.
- The completion check for D5 must enumerate systemd units mechanically, not assert a count.

## Reversal trigger

- **C2:** if masking the unit blocks a legitimate future supervision path, revisit — but only with the
  replacement path already proven, never by unmasking first.
- **C4:** when a capability test can fail against a broken build, A2.1 may be re-widened to whatever that
  test actually covers, and A2.5 regraded RED once its controls can fail.
- **C6:** if promotion ever becomes a fast-forward rather than a merge commit, the delivery branch becomes a
  true ancestor of `main` and the gate may again be checked on either branch.
- **C7:** if declaring the floor in `default_policies` proves impossible in this engine, fall back to all 10
  seat blocks plus the corpus test — and treat the corpus test, not the declaration, as the guarantee.

## Note on process, recorded deliberately

Three of this ADR's seven corrections come from `staff` **retracting its own prior claims**, one comes from
`program` verifying a report rather than relaying it, and two come from a blind cross-vendor review that
neither author could see. The two-vendor rule and the mandatory ADR review both paid for themselves here,
and the specific mechanism that produced the value was **a seat being asked to check work it had authored
without being told it had authored it**.
