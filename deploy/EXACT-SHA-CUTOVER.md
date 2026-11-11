# Exact-SHA Nightshift cutover

This is the operator path for ADR-0013/0014 and issues #107/#115. A merge,
restart, health check, or clean CI run is not a deployment claim. Acceptance is
one receipt with all eight proof blocks green: `registered`, `served`,
`content_at_sha`, `enforced`, `runtime_environment`, `executed`,
`durable_continuity`, and `scheduler_continuity`.

## Before the window

Use the reviewed merge SHA from `main`, not a branch name and not a mutable
checkout. The SHA must descend from the reviewed floor recorded in the issue.
Run as the non-root Nightshift runtime user:

```bash
/path/to/reviewed/checkout/bin/nightshift-release prepare \
  --source https://github.com/tushardhara/nightshift-oss.git \
  --sha <40-hex-reviewed-main-sha> \
  --floor-sha f14640b59a002d7a6c49864f3e4076517b11d3e8 \
  --scheduled-task-id <nightshift-heartbeat-task-id>
```

`prepare` creates `/srv/nightshift-releases/<sha>/checkout` as a detached,
clean, whole-tree Git checkout and removes every write bit. Mutable plans,
receipts, and the pre-cutover served rollback bundle live separately under
`/srv/nightshift-release-state/<sha>/`. It validates Program parsing with the
installed Omnigent interpreter, compares independent Git-object and filesystem
manifests, records the exact control-DB device/inode, and prints forward and
rollback commands. It never signals a process, edits a unit, registers an
agent, or touches an Omnigent cache.

Removing write bits is not a seal on its own: the runtime user owns the tree
and can chmod it back, and every worker runs as that user. So the next step
runs as root, and `authorize` refuses a release until it has:

```bash
sudo /srv/nightshift-releases/<sha>/checkout/bin/nightshift-release seal \
  --plan /srv/nightshift-release-state/<sha>/plan.json
```

`seal` accepts only `/srv/nightshift-releases/<sha>/checkout` for the plan's
SHA, hands that release to root through file descriptors (no symlink or
rename race can redirect it), makes the release base sticky so nobody else
can rename a sealed release away, and re-checks the Program and policy
digests against the plan.

## Quiescence and owner GO

Pause the heartbeat scheduler through its API and let active turns finish. Do
not use `omnigent stop`. Then run:

```bash
/srv/nightshift-releases/<sha>/checkout/bin/nightshift-release authorize \
  --plan /srv/nightshift-release-state/<sha>/plan.json
```

Authorization refuses active control-plane slots/claims/leases, active
Omnigent sessions, a live scheduler, a changed control DB inode, a writable or
dirty release, a changed server/host/runner PID epoch, or a changed heartbeat
prompt. It is read-only and expires after ten minutes. It prints the exact
`cutover` line. The owner approves only that printed line by supplying its plan
digest; nobody recomposes it.

`prepare` discovers all loaded user service/timer/socket/path fragments that
reference an exact stale mutable-root boundary, plus required supervision and
its dependants. It preserves their fragment/override evidence. The cutover
masks them only while all are inactive. It then sends SIGTERM only to the
authorized server and host PID epochs and lets the host's one runner zygote
exit naturally. It starts the server with
`--agent <sealed-release>/program`, then restarts the host/runner with the same
release-first environment and stable host id. `PYTHONPATH`, `nightshift-ctl`,
the pinned Omnigent executable, the agent source, and `NIGHTSHIFT_CTL_DB` all
derive from the one release/plan. It never calls `omnigent stop`, never resets
`/srv/project`, and never edits a cache.

On acceptance, the selected legacy units—including the old watchdog and state
backup units—remain masked so they cannot resurrect a mutable-root runtime.
Rollback restores their exact pre-cutover override state; because authorization
requires them to be inactive, rollback does not start any unit. Replacement
watchdog/backup supervision is deliberately outside #107/#115 and must be a
separate reviewed change.

## Second and later cutovers (ADR-0064)

The first exact-SHA cutover on a host is **bootstrap mode**: every required and
mechanically discovered legacy unit must be `loaded` from a readable fragment,
exactly as described above. That is also the permanently retired end state —
those units stay masked on purpose, so a plain re-run of `prepare` on an
already-cut-over host will always refuse with `legacy supervision units are
unreadable`. That refusal is correct, not a bug; it is not itself the redeploy
path.

To redeploy an already-cut-over host, pass the accepted predecessor's plan to
`prepare`:

```bash
/path/to/reviewed/checkout/bin/nightshift-release prepare \
  --source https://github.com/tushardhara/nightshift-oss.git \
  --sha <next-40-hex-reviewed-main-sha> \
  --floor-sha f14640b59a002d7a6c49864f3e4076517b11d3e8 \
  --scheduled-task-id <nightshift-heartbeat-task-id> \
  --predecessor-plan /srv/nightshift-release-state/<predecessor-sha>/plan.json
```

This enters **subsequent mode** only when the predecessor plan and its
`receipt.json` pass every one of:

1. the predecessor plan validates against its own embedded `plan_digest`;
2. its receipt names that plan digest, is `accepted: true`, and every one of
   the eight proofs is green;
3. the currently served registration, content digest, release root/SHA,
   policy path, control DB identity, and PID/runtime environment agree with
   the accepted predecessor's own recorded operands;
4. every legacy unit inherited from the predecessor (transitively, across
   however many prior generations) is still mechanically masked at its
   recorded `/dev/null` path, inactive, and not replaced by a newly loaded
   fragment; and
5. the predecessor's own `state_dir` has no `rollback-receipt.json` — its mere
   presence disqualifies that whole generation as evidence, even if a unit's
   on-disk mask and backup hash still happen to match a stale, supposedly
   rolled-back snapshot exactly.

Any failure refuses preparation; there is no partial or best-effort subsequent
mode. Directory order, mtime, or "the latest receipt on disk" are never
authority — the predecessor must be named explicitly and verified in full.

Given a verified predecessor, the plan's supervision transition splits into
two disjoint groups instead of one flat inventory: `inherited_masked` (every
unit masked by the predecessor lineage — carried forward untouched) and
`to_mask` (units discovered loaded this run that were not already part of that
lineage; these still need a readable fragment, exactly like bootstrap mode).
Cutover masks only `to_mask`; `inherited_masked` is re-verified but never
re-masked. A failed subsequent cutover's rollback restores only `to_mask`'s
exact pre-run state — inherited masks are never unmasked or reconstructed, so
a failed second (or Nth) cutover cannot resurrect the original mutable-root
supervision that bootstrap mode retired.

This does not change bootstrap mode, the eight-proof acceptance bar, or the
first-cutover masking decision (ADR-0013 D5, ADR-0014 C2) in any way — it only
defines what a verified *subsequent* cutover means. No live host action, no
placeholder unit file, and no operator override flag is part of this ADR.

The heartbeat remains paused through registration and all seven core proofs.
The cutover replaces only its reviewed `./bin/nightshift-ctl` occurrences with
the absolute sealed-release path, verifies the persisted prompt hash, and
reactivates the task as the eighth proof. Startup, path-pinning, proof, or resume
failure restores the original prompt/state, re-registers the preserved
pre-cutover bundle, and verifies its served digest and control-DB identity
before reporting rollback. The rollback command can recover a missing server
listener, and a failure between masking and candidate startup first proves
either the still-authorized old PID epoch or the restarted preserved runtime.

## Reading the receipt

Every proof begins `undetermined`. Missing data, exceptions, timeouts, or an
unreadable operand stay `undetermined`; mismatches are `red`. No old green
verdict is reused across a PID epoch. `accepted` is true only when every proof
is freshly green in the same receipt.

The execution proof creates one normal Program session. Its prompt contains no
response-marker value; Program sends the same inert
`NIGHTSHIFT_BUNDLE_ECHO` child input and exact bounded metadata to `architect`
(Codex) and `staff` (Claude). The native Codex child must echo the inert input;
the Claude child must return its distinct public bundle marker. Acceptance uses
the persisted parent dispatch calls, persisted child user/assistant records,
zero child tool calls, stable Program id/version, and exact served Program
snapshot manifests; Program's final prose is not an operand. The enforcement
proof sends one inert command through the session policy gate and requires a
provenance-bearing denial from the sealed module, current PID, and current
process start epoch.

The runtime-environment proof reads only selected non-secret variables from the
server, host, and runner processes and requires the exact SHA/root, canonical
control DB, sealed policy path, and release `bin` as the first `PATH` entry.

Do not delete `/home/nightshift/nightshift-runtime`, reset `/srv/project`, clear
`program/discovery-state`, remove the release state, or clean old clones and
worktrees until the accepted receipt and a resumed durable heartbeat are both
archived. Broader policy-floor work in #129 is explicitly not proven here.
