# ADR-0016 — Bind server, host/runner, and heartbeat to one exact-SHA release

- **Date:** 2026-08-24
- **Status:** Proposed
- **Decider:** maintenance implementation for owner-directed issues #107/#115;
  acceptance requires `staff` and cross-vendor review
- **Amends:** ADR-0013 D1/D3/D7/D10 and ADR-0014 C2/C3; it does not reverse
  their sealed-root, startup-registration, or supervision decisions

## Context

The live inspection found that restarting only the Omnigent server would leave
the execution plane divergent. The host daemon and its runner zygote inherited
neither `NIGHTSHIFT_RELEASE_ROOT` nor `NIGHTSHIFT_CTL_DB`, and their `PATH` did
not begin at the sealed release. The durable hourly prompt also contained three
`./bin/nightshift-ctl` calls, so its result still depended on `/srv/project` even
after an otherwise-correct server cutover.

ADR-0013 requires agent source, policy source, and control-plane tool to derive
from one root. Applying that requirement only to the server process would make
the receipt green while normal child shell execution still resolved stale
bytes.

## Decision

1. Authorization records and rechecks exact PID epochs for the server, host,
   and the host's single runner zygote. A live cutover sends `SIGTERM` only to
   the authorized server and host PIDs; it never calls global `omnigent stop`
   and never signals the zygote separately.
2. Start the server and host from separate reviewed wrappers, but derive both
   environments from the same sealed checkout and canonical control DB, and
   invoke one resolved Omnigent executable by absolute path. The existing
   Omnigent host id must survive the restart.
3. Add a required `runtime_environment` receipt proof. It reads only selected
   non-secret variables and requires the server, host, and runner to expose the
   exact release root/SHA, canonical control DB, sealed policy directory, and
   release `bin` as the first `PATH` entry.
4. Preserve the complete scheduled-task prompt and its digest during
   preparation. While the task is paused, replace only exact
   `./bin/nightshift-ctl` occurrences with the absolute sealed-release path and
   require a one-for-one replacement. Reactivation is the required
   `scheduler_continuity` proof and happens only after every core proof is
   green.
5. Rollback restores the preserved Program bundle, policy directory, original
   scheduled-task prompt/state, stable host identity, and control-DB identity.
   The preserved policy source must equal the live server's pre-cutover
   `PYTHONPATH`. Missing host/runner identity, a changed prompt, or an
   unverified policy digest is a refusal, not a best effort. Rollback works
   even when the candidate never opened a listener.
6. Enumerate every loaded user service/timer/socket/path fragment through
   systemd, select required supervision plus every unit with an exact stale-root
   slash boundary and its dependants, and preserve both fragment and override
   evidence. All selected units must be inactive before masking. A successful
   candidate leaves them masked; verified rollback restores their exact
   pre-cutover override state but does not start them.
7. The Claude/Codex execution proof trusts neither Program's final prose nor a
   token it could read from the checkout. It requires exactly two persisted
   `sys_session_send` calls with one exact inert payload and metadata, no root
   filesystem/shell/timer calls, exact persisted child assistant records, zero
   child tool calls, stable Program id/version on both children, and each
   child's served Program snapshot matching the sealed Git bundle. The native
   Codex child must echo the challenge; the Claude child must return its public
   bundle marker. Omnigent 0.10 exposes the Program registration—not a separate
   child registration—at a sub-agent's `/agent` endpoint, so the proof records
   that API contract rather than inventing a child-agent id.

The receipt therefore has eight required blocks: the five ADR-0013 facts plus
`runtime_environment`, `durable_continuity`, and `scheduler_continuity`.

## Rejected options

- **Restart only the server.** Rejected because the host/runner keeps the stale
  executable search path that launches worker shell commands.
- **Depend on the server's environment reaching the runner.** Rejected because
  the live host is an independent daemon and direct `/proc` evidence shows it
  did not inherit the server environment.
- **Leave relative heartbeat paths and rely on `/srv/project` CWD.** Rejected
  because it makes mutable project state an authority source again.
- **Put the exact release in a mutable user-level symlink.** Rejected because
  the symlink becomes a second deploy state not covered by the Git/content
  proofs.
- **Resume the heartbeat before runtime proofs.** Rejected because a failed
  cutover could fan out work from a partially changed execution plane.

## Cost, reversibility, and trigger

This remains tier-0 single-VPS operation and adds no service or recurring
infrastructure cost. It adds one bounded host restart to the maintenance window
and receipt checks. The change is reversible through the preserved bundle,
policy copy, prompt, supervision inventory, and state; it does not delete either
stale checkout. While the candidate is accepted, the former watchdog and state
backup units intentionally remain masked so they cannot resurrect mutable-root
processes; replacement supervision is follow-up work, not hidden in this cutover.

Revisit the host restart only when Omnigent exposes a reviewed mechanism that
atomically replaces a live host's child environment and proves the runner
epoch. Revisit prompt rewriting when the scheduled-task schema supports a
structured executable reference rather than free text. `platform` owns both
capability checks; failed release receipts are the trigger artifact.
