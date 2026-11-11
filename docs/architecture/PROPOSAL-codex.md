# Independent architecture proposal: Codex read

> **Provenance.** Blind discovery draft by `architect` (Codex), written without sight of
> `PROPOSAL-claude.md` or the product draft. Delivered as chat text and committed verbatim by
> the program because Codex's command sandbox cannot start on this host (issue #5); the
> transport truncates long messages, so the document was reassembled from chunked resends.
> Kept unedited alongside the Claude draft so "did anyone consider X?" stays answerable.

## Evidence boundary

This is a blind draft. I did not inspect the Claude-side architecture or the product draft.

The execution sandbox was unavailable, so this analysis is limited to the owner-supplied excerpts and environment facts. Claims supported by those excerpts carry file-and-line citations. Everything requiring broader source inspection is explicitly marked **unverified — needs file access**.

## Executive judgment

Nightshift is not primarily an agent framework. It is a single-host, subscription-constrained orchestration system with four layers:

1. A transactional coordination kernel in `bin/nightshift-ctl`, backed by SQLite/WAL.
2. A file-based organizational protocol under `program/`, `docs/`, and `.omni/`.
3. A host installation and service-management layer under `deploy/`.
4. External execution and integration boundaries: Claude/Codex subscriptions, systemd user services, GitHub, and bubblewrap.

The hard part is not storing coordination state. SQLite is adequate. The hard part is establishing truthful liveness across process, login-session, sandbox, vendor, and GitHub boundaries. The confirmed defects all sit at those boundaries.

The system currently confuses four different propositions:

- a file changed;
- the Program completed a useful control-loop iteration;
- an agent process exists;
- a worker still owns and is progressing its task.

Those are not interchangeable. The watchdog makes them interchangeable, and that is the most dangerous correctness error in the supplied material.

# THE SEAM COUNT: TWO

There are exactly **two** clean, independently executable workstreams.

More than two crews would manufacture contention rather than parallelism. "Watchdog," "provisioning," "control plane," "agent organization," and "GitHub workflow" are concerns, but they are not five disjoint implementation seams.

## Seam 1: Host integration and recovery

Owned paths:

- `deploy/**`
- the deployment-specific tests in `tests/test_deploy_lib.py`, `tests/test_recovery.py`, and `tests/test_rollback.py`
- `tests/linux/**`
- host-facing portions of `docs/TRUST-BOUNDARY.md`

Publishes:

- a correctly provisioned `nightshift` runtime account;
- a usable Go/toolchain installation;
- an actually executable bubblewrap boundary;
- a reachable systemd user manager;
- installed and enabled user units;
- an explicit Program heartbeat contract;
- backup and recovery behavior.

Consumes:

- the `nightshift-ctl` CLI and database location;
- the control-plane definitions of slot expiry and run identity;
- the Program heartbeat format;
- repository paths and GitHub credentials established outside the scripts.

Why it is one seam: bootstrap, provisioning, sandbox validation, systemd session setup, watchdog behavior, and backup behavior share the same acceptance environment. Splitting them would cause both crews to edit `deploy/`, `docs/TRUST-BOUNDARY.md`, and the Linux smoke harness while arguing over one host lifecycle.

## Seam 2: Coordination and workflow semantics

Owned paths:

- `bin/nightshift-ctl`
- `program/**`
- control-plane tests such as `tests/test_slots.py`, `test_claims.py`, `test_leases.py`, `test_checkpoint.py`, `test_vendor.py`, `test_issue_semantics.py`, `test_policy.py`, `test_pr_create.py`, and `test_stress.py`
- `docs/CONTROL-PLANE.md`
- `docs/design/SYSTEM.md`
- control-plane portions of `docs/TRUST-BOUNDARY.md`

Publishes:

- the CLI contract for slots, claims, leases, sequences, checkpoints, tasks, runs, vendor availability, PR linting, reaping, backup dumps, and self-test;
- SQLite schema and transaction semantics;
- worker and Program heartbeat semantics;
- vendor availability semantics;
- deterministic PR and release preconditions.

Consumes:

- host-provided process execution and clock behavior;
- an operational sandbox;
- GitHub availability;
- authenticated Claude and Codex subscription seats;
- the heartbeat written by the Program and worker processes.

The seam between the two must be written down before implementation. At minimum it needs:

- the exact heartbeat record and writer;
- whether worker slots are renewed by workers, the Program, or both;
- which exit codes mean unavailable, retryable, or permanently incompatible;
- how provisioning proves that `nightshift-ctl`, bubblewrap, systemd user services, and GitHub are usable by the runtime account.

Documentation files that contain both sides should not become casually shared paths. Assign sections or let one crew propose a handoff while the owning crew applies the final documentation edit.

# What the system actually is

The control plane is a local transactional authority. It appears to serialize access to worker slots, task claims, ownership leases, migration-like sequences, checkpoints, task state, agent runs, and vendor cooldowns in one SQLite database using WAL. That is the right shape for one machine and four workers.

The agent organization is a policy machine layered above it. It uses specialized seats, cross-vendor review, crew ownership, and one release integrator. Much of this organization is enforced narratively rather than transactionally. That creates accidental complexity when many named roles map onto only two subscription credentials and one host.

The deployment layer is more load-bearing than its apparent size suggests. It must establish:

- binaries;
- sandbox capability;
- the runtime user;
- lingering and a user manager;
- user-service environment;
- timers;
- filesystem ownership;
- repository access;
- subscription authentication.

A binary being installed is not proof that any of those capabilities works.

The watchdog and backup layer is supposed to survive failure outside the agent loop. That is correct architecture. Its implementation, however, derives liveness from a broad filesystem side effect generated by the backup mechanism itself. This collapses the independence of the monitor.

The GitHub layer provides durable external visibility and review workflow. Its exact implementation is **unverified — needs file access**. The architectural concern is that GitHub must not be the sole source of coordination truth, while local success must not be reported as integrated until GitHub has accepted the relevant operation.

The largest essential complexity lies at boundaries: process identity, TTL ownership, crash recovery, vendor availability, systemd user sessions, sandbox capability, and GitHub state. The largest accidental complexity is the number of organizational seats and file projections used to describe a system with three ordinary concurrent workers, two vendor subscriptions, and two clean code seams.

# Confirmed defects

## Finding 1: Go version discovery is incompatible with `pipefail`

### Mechanism

`deploy/provision-ubuntu.sh` enables `set -euo pipefail`, then obtains the Go version with:

```bash
curl ... | head -1 | sed ...
```

at `deploy/provision-ubuntu.sh:220-225`.

`head` exits after the first line. If `curl` is still writing, it receives a closed pipe and can exit with code 23. With `pipefail`, the command substitution is unsuccessful even though the first line was valid. With `set -e`, provisioning terminates before the explicit empty-value diagnostic runs.

The same defect pattern appears in checksum discovery:

```bash
curl ... | jq ... | head -1
```

at `deploy/provision-ubuntu.sh:241-243`. Fixing only line 221 leaves a second latent instance.

### Smallest safe fix

Do not put an early-closing consumer after a network producer under `pipefail`.

Download each response completely into a temporary file, then parse it locally:

- fetch the version response to a file;
- read and validate its first line from the completed file;
- fetch the release JSON to a file;
- use one `jq` expression such as `first(...)` to return the matching checksum without a trailing `head`.

Keep `curl -f` and the explicit non-empty validation. Do not "fix" this by disabling `pipefail` around the pipelines; that would hide genuine network failures.

### Regression test shape

A shell-level provisioning test should place a fake `curl` first in `PATH`. The fake should:

- emit a valid first line;
- continue emitting enough data that an early consumer closes the pipe;
- return the same kind of write error real `curl` produces after SIGPIPE.

The test should assert that version resolution succeeds after the fix. A second case must cover the JSON/checksum path. Separate cases should assert that an HTTP failure and an empty or unmatched response still fail with the intended diagnostic.

## Finding 2: A missing Go binary aborts the installer before installation

### Mechanism

The script probes the installed version using:

```bash
go_have=$(/usr/local/go/bin/go version ...)
```

at `deploy/provision-ubuntu.sh:227`.

On a fresh machine, `/usr/local/go/bin/go` does not exist. A simple assignment containing a failing command substitution returns that command's nonzero status. Under `set -e`, the script exits before reaching the comparison and installation branch at lines 228-258.

The fallback `${go_have:-0}` at line 228 therefore provides only the appearance of fresh-install handling. Control never reaches it.

### Smallest safe fix

Make absence an explicit normal state:

```bash
go_have=
if [ -x /usr/local/go/bin/go ]; then
    go_have=$(/usr/local/go/bin/go version | ...)
fi
```

If the file exists but cannot report a parseable version, treat the installation as invalid and reinstall or stop with a precise diagnostic. Do not append `|| true` to the assignment, because that would also conceal corrupt binaries and unexpected execution failures.

### Regression test shape

Run the toolchain phase in an isolated filesystem or disposable Ubuntu VM with `/usr/local/go/bin/go` absent. Serve or mock a pinned Go archive and checksum. Assert that:

- provisioning reaches the download path;
- the archive is verified;
- Go is installed;
- `/usr/local/bin/go` is linked;
- a second run recognizes the installed version and is idempotent.

Add a distinct case where a present executable returns malformed output. That case should not be silently accepted as version zero without an intentional policy.

## Finding 3: Bootstrap assumes a user bus that `su -` does not create

### Mechanism

`deploy/bootstrap.sh` runs:

```bash
systemctl --user daemon-reload
```

at line 176, before it checks or enables lingering at lines 181-184.

A login shell produced by `su - nightshift` does not necessarily have:

- `XDG_RUNTIME_DIR=/run/user/1000`;
- `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`;
- a running `user@1000.service`;
- a reachable user D-Bus.

Linger permits a user manager to persist or start independently of an interactive login; it does not retroactively construct the current shell's environment. The later `systemctl --user` calls at lines 189-197 have the same dependency.

Provisioning reportedly already enables linger, which makes the bootstrap fallback both too late and conceptually misplaced.

### Smallest safe fix

Provisioning, while still root, must:

1. enable linger for the runtime user;
2. ensure the user manager is started;
3. verify that `/run/user/<uid>` and its bus are usable by that user.

Bootstrap must then derive the runtime directory from `id -u`, set the standard user-bus environment when absent, and perform an explicit reachability probe before its first mutating `systemctl --user` call. If the manager is unavailable, it should fail with an instruction directed at the root provisioning step.

Do not make bootstrap opportunistically invoke passwordless sudo. The supplied environment deliberately gives the runtime user no sudo, and that is a good boundary.

### Regression test shape

Use a fresh Ubuntu VM and execute bootstrap exactly through the documented transition:

```text
root provisioning → su - nightshift → bootstrap
```

Start with `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` absent. Assert that:

- the user manager is reachable;
- `daemon-reload` succeeds;
- both timers are enabled and active;
- the service units are enabled;
- the setup survives logout and reboot because linger is active.

A container test without a real user manager is insufficient as the only regression test.

## Finding 4: Backup activity impersonates Program liveness

### Mechanism

The watchdog defines freshness as the newest modification time of any file below `.omni` (`deploy/nightshift-watchdog.sh:50-54`). It treats an age under three hours as proof of health (`:57-63`).

The backup timer runs every 30 minutes and writes:

```bash
nightshift-ctl ... dump > .omni/control-backup.json
```

at `deploy/nightshift-state-backup.sh:31-33`.

Even if the Program has been dead for days, that redirection refreshes a file under `.omni` every 30 minutes. The watchdog therefore sees a permanently fresh organization.

The blast radius is worse than missed alerting. When the false liveness condition holds, the watchdog invokes `nightshift-ctl slot renew-active` (`deploy/nightshift-watchdog.sh:60-62`). According to the supplied comment, this extends in-flight slot TTLs. A dead Program can therefore be made to look alive by backup activity, and that false Program liveness can keep dead worker capacity from expiring.

The watchdog's independence is defeated: the recovery mechanism generates the evidence that suppresses recovery.

The `STATUS.md` shortcut is also unsafe. A first line containing "complete" causes monitoring to stop (`deploy/nightshift-watchdog.sh:45-48`), but completion text is not a cryptographically or transactionally authoritative terminal state.

### Correct liveness design

There must be separate signals for separate owners:

- **Program heartbeat:** written only by the Program after completing a meaningful control-loop iteration.
- **Worker heartbeat:** written transactionally by each worker or by a supervisor that has positively verified that specific worker.
- **Terminal project state:** read from an authoritative control-plane state transition, not substring matching in Markdown.
- **Backup health:** monitored independently; it must never count as Program health.

The Program heartbeat should be a small structured record containing at least:

- schema version;
- run ID;
- Program process/session identity;
- wall-clock timestamp;
- host boot ID;
- last successfully completed loop sequence;
- optional current phase.

It should be written atomically using temporary-file-plus-rename, or stored as one transactional row in the control database. The watchdog should inspect the record's content, not its file mtime. It must reject:

- the wrong run ID;
- a heartbeat from a previous boot;
- timestamps materially in the future;
- non-increasing loop sequence;
- malformed records.

A local SQLite row is preferable if the watchdog already depends on `nightshift-ctl`; a dedicated heartbeat file is acceptable if recovery must still work when SQLite is damaged. In either case, backup must not update it.

Most importantly, Program liveness must not blanket-renew worker slots. Workers should renew their own slots. A Program recovery pass may reconcile a particular slot only after positively verifying its bound agent run. `slot renew-active` should be deleted or narrowed to a per-slot operation with proof of the corresponding live run.

### Regression test shape

Use a fake clock or short TTLs and cover these cases:

1. Program heartbeat advances; watchdog stays quiet.
2. Backup runs repeatedly while the Program heartbeat stays fixed; watchdog alerts after the threshold.
3. Program remains alive while one worker stops heartbeating; that worker's slot expires.
4. A stale heartbeat from a previous boot does not suppress an alert.
5. A future-dated or malformed heartbeat does not suppress an alert.
6. A terminal control-plane state stops monitoring; the word "complete" in Markdown does not.
7. A watchdog invocation cannot renew all active slots merely because the Program heartbeat is fresh.

This needs unit tests for record validation plus a systemd-timer integration test. There is currently no named watchdog test in the supplied test inventory.

## Finding 5: Installation checks for bubblewrap's name, not its capability

### Mechanism

On the confirmed Ubuntu 24.04 host:

- unprivileged user namespaces are restricted by AppArmor;
- the installed bubblewrap package has no applicable AppArmor profile;
- `bwrap` and bare `unshare -U -r` fail while writing the UID map.

`deploy/preflight.sh:37` reportedly checks only `command -v bwrap`. `deploy/provision-ubuntu.sh:88-90` installs or requires the binary but does not execute a representative sandbox. `docs/TRUST-BOUNDARY.md:67` states that bubblewrap ships an AppArmor profile on Ubuntu 24.04 and carries the mount-namespace responsibility. That statement is false on the observed stock system and is load-bearing.

The result is not a degraded optional feature. Every Codex-native command dies before execution. Because `qa` and `release` are both Codex-native, the defect simultaneously removes the QA gate and the only role authorized to merge.

### Smallest safe fix

Preflight must execute a minimal representative bubblewrap sandbox as the actual runtime user. The probe must exercise user-namespace creation and the required bind/mount behavior, not merely `bwrap --version`.

Provisioning should fail closed with a precise diagnosis if the probe fails. If Nightshift chooses to install an AppArmor profile, that is a security-sensitive host mutation and must be:

- explicit;
- versioned;
- narrowly scoped to the installed bubblewrap path and required namespace behavior;
- loaded and then tested;
- documented as part of the trust boundary.

The system must not recommend `danger-full-access` or disabling the host restriction as its normal workaround.

`docs/TRUST-BOUNDARY.md:67` must describe a tested capability, not a package assumption.

### Regression test shape

Run provisioning and preflight on a stock Ubuntu 24.04 VM with `kernel.apparmor_restrict_unprivileged_userns=1`.

Test two states:

- bubblewrap installed but no permitting profile: preflight must fail with a diagnostic that identifies the user-namespace/AppArmor cause;
- the supported Nightshift host configuration: the representative sandbox must execute successfully as `nightshift`.

The final smoke test must dispatch a harmless command through the same Codex execution path used in production. A synthetic `bwrap --version` test would preserve the defect.

# Sequencing

## First: make the execution substrate truthful

Fix finding 5 first. Until the sandbox capability is established, Codex-native QA, review, engineering, architecture, and release seats are unavailable. Testing higher-level workflow while half the organization cannot execute would validate a configuration that cannot finish.

At the same time, temporarily route critical roles by capability rather than vendor label. The current assignment puts both QA and the sole release integrator on the same unavailable runtime. Cross-vendor independence is valuable, but correlated total unavailability is worse. The permanent design should support a declared hard-down vendor and an explicit emergency reassignment policy without pretending the unavailable vendor is merely cooling down.

## Second: make fresh installation deterministic

Fix findings 1-3 together as one fresh-host acceptance slice:

- safe network parsing under `pipefail`;
- absent-tool handling;
- user-manager establishment;
- bootstrap from the documented account transition.

These are prerequisites for reproducing any unattended run.

## Third: repair liveness before testing long runs

Replace the `.omni` mtime heuristic and remove blanket slot renewal. A seven-day soak test before this change is almost worthless: backup activity can make a dead run appear healthy for the entire test.

## Fourth: validate recovery and workflow semantics

After the substrate and liveness signals are truthful, test:

- Program crash and restart;
- individual worker crash;
- vendor hard-down and rate-limit cooldown;
- stale claims and leases;
- GitHub outage and retry;
- release-gate unavailability;
- backup restoration.

# One-way doors

Almost nothing here should be a one-way door.

The closest one-way decisions are:

- distributing a custom host AppArmor policy;
- changing the persisted SQLite schema without backward-compatible migration;
- changing the public semantics of slot renewal or heartbeat ownership;
- relying on a particular systemd user-session model in all deployments.

Treat each as versioned and reversible. Back up the control database before migrations, retain the previous AppArmor profile, and support one schema version of rollback where feasible.

Role reassignment, heartbeat-file location, timer intervals, and vendor hard-down state are reversible.

Disabling sandboxing is not an acceptable reversible experiment. It invalidates the stated trust boundary.

# Capacity arithmetic before technology

## Target load

Configured capacity is:

- four maximum concurrent workers;
- one reserved for on-call;
- therefore three ordinary workers under normal operation;
- six maximum items in flight;
- three to seven days per unattended run.

Assume four live slots heartbeat once per minute. That produces:

- 4 writes/minute;
- 240 writes/hour;
- 5,760 writes/day;
- 40,320 writes over seven days.

Even adding one Program heartbeat per minute gives only 10,080 additional writes over seven days. Claims, leases, task transitions, checkpoints, vendor state changes, and PR events should be orders of magnitude less frequent than heartbeats. A conservative tenfold allowance is still below 10 writes per second at bursts and far below one write per second averaged over the run.

A single SQLite database in WAL mode is appropriate by a wide margin.

The important contention property is not record count but transaction duration. No command should hold a write transaction while:

- launching an agent;
- calling GitHub;
- waiting on a vendor;
- reading a large transcript;
- running tests;
- sleeping for backoff.

Acquire the transaction, check and mutate local state, commit, then perform external work. **Unverified — needs file access:** whether every `nightshift-ctl` command obeys this rule.

## Storage

At tens of thousands of small state transitions, the live database should remain small. Inbox, transcript, report, and checkpoint sizes are **unverified — needs file access**, but they should not be stored as ever-growing blobs in hot SQLite rows. The database should hold identifiers, offsets, hashes, and state; large human-readable artifacts belong in files or Git.

WAL growth must be bounded by regular successful checkpoints and by ensuring no process holds a read transaction for hours. Backup should use SQLite's consistent backup mechanism or `nightshift-ctl dump`, not copy a live database file.

## GitHub API rate

Four workers do not create meaningful GitHub throughput pressure. Even an aggressive wave with six items might generate tens of API operations for issues, PR creation, reviews, comments, and status checks — not thousands per minute.

The risk is retry behavior, not nominal volume. A failed GitHub request must not be retried in a tight loop, and a local state transition must be idempotently reconcilable after an ambiguous remote result. Exact call behavior is **unverified — needs file access**.

## Vendor quota

Vendor consumption dominates infrastructure capacity. With one login per vendor, the practical capacity is not "four workers"; it is whatever concurrent and cumulative token usage those two subscriptions tolerate.

A wave with three ordinary workers plus an on-call interruption can put four active conversations onto one or two credentials. Model tier, prompt size, transcript growth, and review loops matter more than SQLite writes.

The control plane models cooldown from 30 minutes, doubling to four hours. That handles temporary rate limiting, but not environmental incompatibility or revoked authentication. The system needs at least these vendor states:

- available;
- cooldown until a known time;
- hard-down with reason;
- probe-required;
- administratively disabled.

A hard-down state is worth adding because today's bubblewrap failure disables every Codex-native seat indefinitely. Encoding that as repeated cooldown wastes time and produces misleading recovery behavior.

## SQLite verdict and ceiling

**SQLite holds. Keep it.**

Do not propose a server database, queue, distributed lock service, or event bus.

The reason to revisit SQLite would not be "seven days" or "more agents" in the abstract. Revisit only after measured evidence such as:

- sustained write demand approaching the host's serialized transaction capacity;
- repeated `SQLITE_BUSY` after short, bounded retries;
- p95 write-lock wait above roughly 100 ms during normal waves;
- WAL growth that cannot checkpoint because of long-lived readers;
- coordination across multiple hosts requiring a single authoritative writer;
- database size or dump time interfering with recovery objectives.

At the present target, the actual ceiling is external: subscription quota, GitHub behavior, process supervision, and truthful recovery.

# Invariant analysis

## Duplicate work

The control plane has task claims and reportedly permits at most one active claim per task. That is the correct primitive.

What it may only appear to guarantee: uniqueness in SQLite does not prevent duplicate external work if a worker performs an action, crashes before recording it, and another worker later reclaims the task.

Concrete failure mode: worker A creates a PR or sends a GitHub comment, loses the response, and dies before persisting success. Worker B reacquires the expired claim and repeats the operation.

Required property: every external action needs an idempotency key or reconciliation step. Claim ownership prevents simultaneous local execution; it does not provide exactly-once effects.

Transaction and schema details are **unverified — needs file access**.

## Deadlock

Leases provide exclusive ownership, but multiple independently acquired leases can deadlock if workers obtain them in different orders.

Concrete failure mode:

- task A holds resource X and waits for Y;
- task B holds Y and waits for X;
- both continue heartbeating, so TTL never resolves the cycle.

At this scale, do not build a generic deadlock detector. Require workers to acquire multiple resources in one transaction or in a canonical sorted order. Better still, redesign tasks so one task needs one shared edit lease.

Whether lease acquisition supports atomic sets is **unverified — needs file access**.

## Task claims

A unique active claim plus TTL provides crash recovery only if:

- acquisition and expiry checks are atomic;
- renewal proves the same owner;
- clocks are handled consistently;
- a released or expired owner cannot still commit task state.

The last property requires fencing. A worker should carry a monotonically increasing claim generation, and completion should succeed only if that generation is still current.

Without fencing, a paused worker can resume after its claim expired and overwrite the replacement worker's result.

Generation-token support is **unverified — needs file access**.

## Ownership leases

The distinction between edit and serialize leases is sensible. Serialize leases held until merge protect migration or ordering-sensitive resources.

The danger is liveness: a serialize lease whose owner disappears can block the project indefinitely, while an automatically expired serialize lease can permit conflicting publication.

Break-glass behavior must record who broke the lease, why, and what remote state was inspected first. Exact behavior of `lease break` is **unverified — needs file access**.

## Worker-slot correctness

The reserved on-call slot is the right capacity rule. Slot consumption at dispatch is stronger than advisory accounting.

The supplied watchdog violates ownership by renewing all active slots based on Program-level activity (`deploy/nightshift-watchdog.sh:60-62`). This converts a per-worker lease into an organization-wide lease.

A slot must be renewed by its bound worker or a supervisor that positively verifies that worker. Slot records need a binding to run ID and claim generation. Expired workers must be fenced from later completion.

## Crash and restart recovery

SQLite/WAL and TTL-based ownership are appropriate. Recovery correctness depends on distinguishing:

- process crashed before external effect;
- external effect succeeded but local commit did not;
- local commit succeeded but projection files did not;
- host reboot changed process identities and invalidated heartbeats.

The heartbeat record must include boot and run identity. Recovery should reconcile remote GitHub state before replaying operations.

Backup every 30 minutes is useful for state loss, but it is not a liveness signal.

Detailed recovery behavior is **unverified — needs file access**.

## Vendor exhaustion and environmental failure

Cooldown with exponential backoff is appropriate for temporary quota exhaustion. It is insufficient for hard environmental failure.

Today every Codex-native seat is dead because sandbox creation fails. The current vendor model has no way to represent that fact. Retrying after cooldown cannot help.

This is especially severe because both QA and release share the failed runtime. One environmental defect removes two consecutive mandatory gates and the only default-branch writer.

Add explicit hard-down state and capability-based routing. Preserve cross-vendor review where both vendors are available, but do not encode irreplaceable operational authority in one runtime without a documented reassignment mechanism.

## Unattended three-to-seven-day operation

The top risks are:

1. false liveness caused by backup activity;
2. subscription or runtime hard-down with no accurate availability state;
3. loss of the sole QA/release path because correlated roles share Codex-native execution.

Long unattended operation also requires bounded logs, repository-disk monitoring, WAL checkpoint health, clock sanity, GitHub retry limits, and authentication-expiry detection. Coverage is **unverified — needs file access**.

## Security boundaries

The documented bubblewrap boundary is currently asserted rather than proven. `docs/TRUST-BOUNDARY.md:67` reportedly relies on a package-level AppArmor claim contradicted by the observed host.

A security boundary exists only if a representative operation is tested under the runtime identity. Binary presence, package version, and documentation are not evidence of enforcement.

Installing a broad AppArmor exception or disabling the user-namespace restriction would weaken the host. Any supported profile must be narrow and threat-modeled.

Other security behavior is **unverified — needs file access**.

## GitHub, PR, and review workflow

The organization requires cross-vendor and cross-crew review plus a single release integrator. That can produce strong independence, but only if the roles are operational.

With Codex unavailable:

- Codex QA cannot execute;
- Codex reviews cannot execute;
- release cannot execute;
- the project cannot legitimately finish.

This is a system availability defect, not merely a staffing inconvenience.

Exact PR linting, closing-keyword enforcement, review identity, and remote reconciliation are **unverified — needs file access**.

## Deterministic release gates

A gate is deterministic only if it records:

- exact commit or merge-result SHA;
- exact command;
- toolchain version;
- exit status;
- artifact or log reference.

A role saying "tests passed" is not a deterministic gate. The release integrator must test the actual merge result, not the PR head alone.

Current implementation is **unverified — needs file access**.

## Test coverage

The supplied inventory shows useful focused tests for claims, leases, slots, recovery, policy, PR creation, stress, and vendor handling.

The highest-value gaps are explicit:

- no watchdog test;
- no full provisioning test;
- no fresh Ubuntu 24.04 sandbox-capability test;
- no `su - nightshift` systemd-user bootstrap test.

The current 1,353 test lines are not evidence of insufficient or sufficient coverage by themselves. Counts should not drive the plan. The missing boundary scenarios should.

## Install and deploy reliability

Installation currently fails in four independent fresh-host paths:

- network parsing under `pipefail`;
- missing Go binary;
- absent systemd user bus;
- unusable bubblewrap despite installed binary.

This is enough evidence that deployment is testing package presence rather than operational capability. The acceptance test must be a fresh VM from provisioning through a harmless worker dispatch, timer execution, backup, watchdog observation, restart, and recovery.

# Highest risks to a seven-day run

## 1. False liveness preserves a dead system

The backup updates `.omni` every 30 minutes, the watchdog interprets that as Program activity, and then renews active slots. The monitor can suppress both alerting and capacity recovery indefinitely.

## 2. Vendor hard-down is represented as temporary cooldown

A deterministic environmental incompatibility will never recover after 30 minutes or four hours. Without a hard-down state, the Program can repeatedly queue impossible work or wait forever.

## 3. Mandatory gates share one failed execution substrate

QA and release are both Codex-native. One bubblewrap failure removes verification and integration together. The organization's nominal redundancy therefore masks a correlated single point of failure.

The single thing most likely to break a seven-day unattended run is **the watchdog accepting backup-generated `.omni` activity as Program liveness and renewing slots on that false signal**.

# What to cut

## Delete blanket slot renewal

`slot renew-active` should not exist as a watchdog response. It violates per-worker ownership and hides dead workers. Replace it with worker-owned renewal plus explicit reconciliation.

## Delete `.omni`-wide mtime liveness

Do not refine the `find` expression with exclusions. Any directory-wide activity heuristic will regress when a new timer or report writer appears. Replace the entire mechanism with an explicit heartbeat contract.

## Delete Markdown substring completion detection

A first line containing "complete" is not an authoritative terminal state. Use a control-plane transition tied to the current run and definition-of-done evidence.

## Delete runtime sudo fallback from bootstrap

The runtime account intentionally has no sudo. The fallback at `deploy/bootstrap.sh:182-184` makes responsibility ambiguous. Root provisioning should establish linger; bootstrap should verify it and fail clearly.

## Delete package-presence claims from the trust model

"Installed" and "ships a profile" are not security properties. Document and test capabilities.

## Do not build a distributed coordination stack

The audit should not spend time evaluating replacement databases, distributed queues, leader election, Kubernetes, or multi-region recovery. Four workers on one VM do not justify them.

## Deprioritize exhaustive unit coverage of wrapper code

Fresh-host integration tests are more valuable than exhaustive tests of every shell helper. The confirmed defects arise from shell execution semantics and host state that mocked unit tests often erase.

## Do not add more crews

Two seams exist. Additional crews would queue on `deploy/`, `bin/nightshift-ctl`, shared documentation, and the same Linux acceptance environment while consuming the same two subscriptions.

## Avoid a generic deadlock subsystem

Canonical lease ordering, atomic multi-resource acquisition, and smaller task scopes are cheaper and more reliable at this scale.

# Proposed outcome

Keep the single-host architecture and SQLite control plane. Make its liveness and ownership semantics narrower.

The immediate program should deliver:

1. a proven bubblewrap execution boundary on stock Ubuntu 24.04;
2. a fresh-host provisioning test covering Go absence and `pipefail`;
3. a verified systemd user manager after the documented `su -` transition;
4. an explicit Program heartbeat independent of backup activity;
5. worker-owned slot renewal with fencing;
6. a hard-down vendor state distinct from cooldown;
7. a capability-based contingency for QA and release when one runtime is unavailable.

The architecture does not need more technology. It needs fewer inferred signals and more executable proofs.

