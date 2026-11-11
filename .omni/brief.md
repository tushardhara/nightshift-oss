# Nightshift Self-Dogfood

Audit and improve Nightshift itself for reliable multi-day unattended software delivery.

## First priority: bugs found during this real Hostinger installation

1. Fresh Go provisioning failed because Go version discovery used a pipeline
   that can fail under `set -o pipefail`.

2. Fresh Go provisioning failed when `/usr/local/go/bin/go` did not yet exist.
   Missing Go must mean "install Go", not terminate provisioning.

3. bootstrap.sh assumes `systemctl --user` works after `su - nightshift`.
   On this Ubuntu VPS there was no usable user DBus/XDG runtime session and
   bootstrap failed with:
   "Failed to connect to bus: No medium found"

4. Fix watchdog liveness correctness.
   Backups update `.omni/control-backup.json` every 30 minutes, while watchdog
   currently treats general `.omni` activity as proof the Program is alive.
   A dead Program must not remain healthy because a backup file changed.

## Then audit

- duplicate work
- deadlocks
- task claims and leases
- worker-slot correctness
- crash/restart recovery
- vendor rate-limit and subscription exhaustion handling
- unattended operation for 3-7 days
- security boundaries
- GitHub/PR/review workflow
- deterministic release gates
- test coverage
- unnecessary complexity
- deployment/install reliability

For every confirmed bug:
- identify root cause with evidence
- add a regression test
- implement the smallest safe fix
- run relevant tests
- use normal branch/PR/review workflow

Do not weaken worker limits, ownership leases, task claims, review requirements,
credential boundaries, sandboxing, branch protections, or release gates merely
to make the run easier.

The controller at /srv/nightshift is frozen.
The repository being improved is /srv/project.
Never modify /srv/nightshift during this run.

Continue autonomously without waiting for human approval unless an operation
would require secrets, destructive external actions, or weakening a safety
boundary.
