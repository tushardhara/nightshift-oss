# ADR-0015 — Prove horizontal exact-SHA delivery before expanding the policy floor

- **Date:** 2026-08-24
- **Status:** Accepted
- **Decider:** owner directive, executed by the controlled maintenance driver
- **Supersedes narrowly:** ADR-0013 D10 and ADR-0014 C7 on sequencing and the
  contents of cutover change 1. Their security findings remain valid.
- **Issues:** #107, #115, #129

## Context

PR #134 began as a reconciliation of the live Program's required
`gate_pushes: false` value. Review expanded it into a server launch-config
change and a command-language classifier that blocks legitimate agent
workflows, including inline interpreter execution and runtime-selected Git
arguments. That is a behavioral breaking change and is not the smallest path
to proving the P0 runtime cutover.

The live Program already runs with `gate_pushes: false`. Making that value
reviewed does not loosen the running system. Deploying the repository's current
`true` value would instead turn every ordinary push and merge into an ASK with
no human responder and a 24-hour timeout.

## Decision

Ship one compatibility reconciliation before the exact-SHA cutover:

1. Declare and test `gate_pushes: false` in the reviewed Program bundle.
2. Do not add the new server policy config, policy parser, or shell-surface
   restrictions in this change.
3. Keep #129 and PR #134 open as deferred vertical security work; do not claim
   their unconditional policy floor is deployed or complete.
4. Continue #107/#115 with the sealed exact-SHA release root. Its horizontal
   acceptance remains: registered bundle, served bundle, policy-origin proof
   for the policy actually loaded, Claude execution, Codex execution, and
   durable control-plane continuity.

## Accepted risk

With `gate_pushes: false`, upstream ASK-tier infrastructure mutations are not
an enforced unattended boundary. This is already the live posture, so the
horizontal cutover preserves rather than widens production behavior. Until
#129 lands, operators must not interpret the exact-SHA cutover as proof of a
complete production-command sandbox.

## Rejected options

- Merge PR #134 as-is: rejected because its opaque-execution fail-closed rules
  break ordinary agent workflows and its latest Claude review did not complete.
- Deploy `gate_pushes: true`: rejected because it deadlocks unattended delivery.
- Claim #129 done after the value reconciliation: rejected; the policy floor is
  explicitly still open.

## Reversal trigger

If the exact-SHA rehearsal cannot execute normal Claude and Codex delivery
commands safely with this value, stop the cutover. If a seat needs authority to
mutate external infrastructure before #129 is complete, finish a narrower
reviewed enforcement design first rather than bypassing policy at runtime.
