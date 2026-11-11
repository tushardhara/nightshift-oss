# Architecture proposal — Claude side (blind draft)

Author: `staff`, acting as Claude-side architect for discovery. Written from
`.omni/brief.md` and the `/srv/project` tree only. Not reconciled against the
Codex draft; do not read it as a review of anything.

**Seam count: TWO.** Read §5 before sizing the org. Everything else in this
document is evidence for that number and for the sequencing in §6.

---

## 1. What this system actually is

Nightshift is four layers on one Ubuntu box, held together by one SQLite file
and one hourly scheduled task.

| Layer | Where | What it really is |
|---|---|---|
| Control plane | `bin/nightshift-ctl` (1073 lines, stdlib Python), `deploy/policies/omnipods_policies.py`, `.omni/control.db` | A semaphore, two partial-unique-index mutexes (claims, leases), a sequence allocator, a checkpoint log, and a vendor backoff table. All mutation is `BEGIN IMMEDIATE` on one WAL SQLite file (`nightshift-ctl:143-148`, `:253`). The policy module is the only *enforced* coupling between the LLM layer and the DB: a crew lead's `sys_session_send` to a worker is DENIED unless it carries an unconsumed, correctly-bound slot (`omnipods_policies.py:97-179`). |
| Agent org | `program/config.yaml` + `program/agents/**` + `program/skills/**` | ~3,000 lines of prompt. The program is a single long-lived `claude-sdk` session; crews are leads that fan out to native Claude Code / Codex CLI workers in git worktrees. Everything in this layer is *advisory* unless a policy backs it. |
| Deploy / provision | `deploy/provision-ubuntu.sh` (root), `deploy/bootstrap.sh` (runtime user), `deploy/preflight.sh`, `deploy/lib/deploy-lib.sh`, `deploy/systemd/*` | Shell. ~1,250 lines. Six user-scope systemd units (server, host, backup timer+service, watchdog timer+service). All four confirmed bugs live here. |
| Recovery | `deploy/nightshift-watchdog.sh`, `deploy/nightshift-state-backup.sh`, TTL expiry inside `nightshift-ctl` | A dead-man's switch (alerts on a GitHub issue), a 30-minute git-plumbing snapshot of `.omni/`+`docs/` to `omni/state`, and lazy TTL expiry of slots/claims/edit-leases. |
| GitHub workflow | `.github/workflows/tests.yml`, `release` agent prompt, `pr_close_keyword_guard` policy, `nightshift-ctl pr-create`/`pr-lint` | One CI job (stdlib tests + `bash -n`), one policy that denies raw `gh pr create` with closing keywords / `--body-file` / `--fill`, and a release *prompt* that tags, merges, re-verifies, reverts. |

**Where the real complexity is.** Exactly one place: the *liveness/ownership
story across a process boundary the DB cannot see*. The DB knows who holds a
slot; it does not know whether the worker behind the slot is alive. Everything
that tries to bridge that gap — TTLs, `renew-active`, the watchdog's mtime
heuristic, the program's "claim-first re-dispatch" — is where the design is
currently wrong (§2, §3.4). This is a genuinely hard distributed-systems
problem (failure detection with no heartbeat from the failing party) hiding
inside a 96-line shell script.

**Where the accidental complexity is.**
- Three projections of the same DB state: `.omni/control-state.json` (`dump --write`, `nightshift-ctl:947-951`), `.omni/control-backup.json` (`nightshift-state-backup.sh:127-129`), and hand-mirrored `.omni/leases/` one-liners (`program/config.yaml:170`). The third is written by an LLM from memory and is the only one that can drift.
- `agent_runs` table: no prompt anywhere calls `run start`/`run end` (grep of `program/`, `docs/`, `AGENTS.md` finds only the table description). It is dead schema and the `expire_stale` code that updates it (`nightshift-ctl:223-227`) is dead code.
- `consume_slot` exists twice, logic-for-logic: `nightshift-ctl:150-167` and `omnipods_policies.py:135-158`. The comment admits it. Convergent duplication already present at day one.
- `rtk`/Homebrew section in `bootstrap.sh:46-72`: optional, depends on Homebrew-on-Linux, mutates `~/.claude/settings.json` with a Python heredoc. Not a Nightshift concern.
- `max_items_in_flight` in `capacity.json` is read by nobody (`load_capacity` only copies `CAP_DEFAULTS` keys, `nightshift-ctl:174-176`). A configured number that does nothing is a lie to the operator.

---

## 2. Correctness analysis of the named invariants

Format per invariant: **guaranteed** / **appears guaranteed** / **failure mode**.

### 2.1 Duplicate work
- **Guaranteed:** at most one *active claim* per `task_id` (`one_active_claim` partial unique index, `nightshift-ctl:65-66`; `claim acquire` maps `IntegrityError` to exit 3 with the winner's `(agent,title)`, `:381-395`). The slot semaphore is atomic and fails closed (`omnipods_policies.py:108-116, :162-168`).
- **Appears guaranteed:** that a task cannot have two workers. A claim is only consulted by the *program's prompt* ("RE-DISPATCH IS CLAIM-FIRST", `program/config.yaml:375-387`). The policy checks slot validity, slot→agent binding, and one-dispatch — it never looks at `task_claims`, and never checks whether another *dispatched, active* slot already carries the same `task_id`. Two leads (or one lead on two heartbeats) can each `slot acquire --task T` and both dispatches are ALLOWED.
- **Failure mode:** two workers on one branch, conflicting diffs, the exact outcome `config.yaml:385-387` warns about — prevented by prose, not by the DB. Cheap fix, Seam A: in the policy's existing `BEGIN IMMEDIATE`, deny if `SELECT 1 FROM worker_slots WHERE task_id=? AND state='active' AND dispatched_at IS NOT NULL AND id<>?` returns a row whose `bound_agent`/`bound_title` differ (same `(agent,title)` = legitimate resume).

### 2.2 Deadlock
- **Guaranteed:** every ctl transaction is single-statement-short and `BEGIN IMMEDIATE`; SQLite cannot deadlock two writers, it serialises them (30 s `busy_timeout`, `:148`). No lease is ever acquired *inside* another ctl transaction.
- **Appears guaranteed:** no cross-crew lease deadlock. Two crews each holding one edit lease and queued on the other's is possible; nothing detects it. The edit TTL is 24 h (`CAP_DEFAULTS`, `:50`) and serialize leases never expire (`:428`), so the cycle resolves only when the program notices and `lease break`s.
- **Failure mode:** a wave stalls up to 24 h. Real but rare (leases are for `db/migrations`, lockfiles, `.github/workflows` per `docs/owners.md`), and the program is already the arbiter. **Do not build detection**; lower `lease_ttl_seconds` to 4 h and have the hourly heartbeat list leases older than 2 h in its report. See §7.

### 2.3 Task claims
- **Guaranteed:** uniqueness, lazy expiry (`expire_stale`, `:200-206`), TTL 1 h.
- **Appears guaranteed:** that a claim means anything at dispatch time. It does not (§2.1). Also `claim renew` and `claim release` take only `--task`, no `--claimant` (`:398-420`): any process can release anyone's claim. Holder check exists on `lease release` (`:470-473`) but not on claims — an inconsistency in the same file.
- **Failure mode:** a stale heartbeat turn releases a claim the current recoverer holds; the next turn re-claims and dispatches a second worker. Fix: `claim release/renew --claimant` required, holder-checked, same shape as leases.

### 2.4 Ownership leases
- **Guaranteed:** one active holder per resource string (`one_active_lease`, `:79-80`); serialize mode refuses early release (`:474-479`); `seq next --resource` allocates only to the holder (`:513-522`).
- **Appears guaranteed:** that the resource *string* matches the files. Leases are on free-text globs (`--resource '<glob>'`). `db/migrations/**` and `db/migrations/*` are two leases on the same directory. Nothing normalises. Nothing checks a PR's diff against active leases.
- **Failure mode:** two crews "hold" the same directory under two spellings; both allocate from `seq next --name migrations` (the sequence is global, so numbers still do not collide — but ordering/serialisation does). Medium severity. Fix: normalise to the `docs/owners.md` row key (make owners.md the enum) rather than a glob; cheap.

### 2.5 Worker-slot correctness
- **Guaranteed:** `active_normal ≤ max − reserve`, `active_total ≤ max`, atomically (`:258-268`); `tests/test_stress.py` samples this under 6 crews × 4 tasks with 25 % crash injection. One-dispatch CAS (`:159-163`). Fail-closed.
- **Appears guaranteed:** that the budget is *global*. `worker_semaphore` is attached only to crew leads (`crew_alpha/config.yaml:341-345`). The program's own dispatches — `product`, `architect`, `staff`, `ux`, `platform`, `security`, `release`, `oncall` — are real Claude/Codex sessions and are bounded only by `spawn_bounds max_dispatches_per_turn: 4` (`program/config.yaml:471-477`). So true vendor concurrency is **up to 4 worker slots + up to 4 program-level children + the program session itself = 9**, not 4. `capacity.json`'s note "one budget for ALL crews" is true and irrelevant: crews are not the only consumers.
- **Failure mode:** the rate-limit cooldown path fires far more often than the 4-worker arithmetic predicts (§4), and every cooldown costs a 30 min→4 h sleep. This is the single largest gap between the documented and actual model. Fix options, in order of cheapness: (a) attach `worker_semaphore` to the program too, with `pm`-style exemptions only for seats that are not CLI workers; (b) lower `max_concurrent_workers` to what the vendors actually tolerate. (a) is a YAML change plus one policy argument.
- **Leak:** `slot acquire` → dispatch DENIED by *another* policy (`spawn_bounds`, `cost_budget`, `headless_subagent_purpose_guard`) leaves an active, never-dispatched slot. Its TTL is 4 h, but `renew-active` (`:296-312`) renews **every** active slot regardless of `dispatched_at`, so the leak lives 24 h (`--max-age` default). With 3 normal slots, one leak = 33 % of capacity for a day. Fix: `renew-active` must add `AND dispatched_at IS NOT NULL`, and un-dispatched slots should get a short TTL (10 min) at acquire.

### 2.6 Crash / restart recovery
- **Guaranteed:** all control state is rows; a restarted process sees the same picture (`tests/test_recovery.py`). Server and host have `Restart=always`, `StartLimitIntervalSec=0` (`systemd/omnigent-server.service:424-428`).
- **Appears guaranteed:** that a dead worker's slot "frees itself the moment anyone else asks" (`nightshift-ctl:24-26`). It does not, because of `renew-active` + the watchdog liveness bug (§3.4). After a host restart (which kills every worker in the cgroup — `KillMode` default), all N slots stay active and are renewed every 30 min for up to 24 h, because the backup timer keeps `.omni/` fresh. The program's reconciliation at the next heartbeat runs `reap` and gets `slots_reaped: 0`. Capacity after a reboot is therefore **zero for up to a day** unless the program infers death from registries and releases slots by hand.
- **Failure mode:** the run resumes after a reboot with no ability to dispatch; the watchdog sees files moving and never alerts. Deterministic fix, Seam B + A: `omnigent-host.service` gets `ExecStartPre=nightshift-ctl slot reap-all --reason host-restart` — a host start is, by construction, proof that no worker is alive. This is the one place a "mark everything dead" is *correct* rather than heuristic.

### 2.7 Vendor rate-limit and subscription exhaustion
- **Guaranteed:** backoff arithmetic is in one place, doubling to a 4 h cap (`:745-762`); `check --all` returns the sleep (`:779-798`).
- **Appears guaranteed:** that anything *enforces* a cooldown. The policy never consults `vendors`; a lead told "re-dispatch to the other vendor" (`crew_alpha/config.yaml:231-233`) can dispatch to a cooling vendor. Also the cap (4 h) is shaped for per-window rate limits, not for a **weekly subscription cap**: once the weekly cap is hit, the program wakes hourly, gets `usageLimited`, records another consecutive limit, sleeps 4 h, repeats — burning turns for days with nothing to show.
- **Failure mode:** silent quota burn and a degraded run reported as "cooling". Fix: (1) `vendor check` consulted inside `worker_semaphore` (one extra SELECT in the same transaction; deny with `min_wait_seconds`); (2) after N consecutive limits on the cap (say 3 × 4 h), set `state:blocked` on the status issue and stop dispatching to that vendor until `vendor ok` — this is the brief's "needs owner" path and it must reach the phone.

### 2.8 Unattended 3–7 day operation
- **Guaranteed:** journald capped 2 G/2 weeks; hourly disk guard at 85 %/93 % (`provision-ubuntu.sh:347-372`); swap; `MemoryMax` on the host cgroup.
- **Appears guaranteed:** that the watchdog will notice a dead program. It cannot (§3.4). And the hourly heartbeat is a row the program created in the omnigent server's Postgres; its survival across server restarts is an Omnigent property this repo cannot test (frozen controller).
- **Failure mode:** the run dies on day 2 and nobody is told for 5 days. See §6 risks.

### 2.9 Security boundaries
- **Guaranteed:** terminal sandboxes mask `$HOME` (`program/config.yaml:430-470`); Codex workspace-write; `NoNewPrivileges`; repo-scoped PAT; loopback Docker publish default. `docs/TRUST-BOUNDARY.md` is unusually honest.
- **Appears guaranteed:** that "only `release` writes to main" is a mechanism. It is not: every worker holds the same `~/.config/gh` PAT with PR-write + contents (`TRUST-BOUNDARY.md` credential table), so any worker can `gh pr merge`. Branch protection (require PR + status checks) is the only deterministic gate, and it is configured out-of-band.
- **Failure mode:** a confused worker merges its own PR. Fix is not in this repo: require a review from a *different* GitHub identity would need a second PAT — a one-way door, do not take it this run. Record as accepted residual risk #2b.
- Also: `pr_close_keyword_guard` is a regex on the *command string* (`omnipods_policies.py:190-192`): `gh api repos/…/pulls -f body=…`, `$G pr create`, or a body written by `printf > f` then `gh pr create -F f` in a second command — the last is denied, the first two pass. Backstop is `release`'s prompt-level lint. Make the backstop deterministic: a CI job that runs `nightshift-ctl pr-lint` on the PR body and is a required status check. Free.

### 2.10 GitHub / PR / review workflow
- **Guaranteed:** `Refs #n` discipline via lint + wrapper; merge commits (so `revert -m 1` works); `integ/<ts>-pre` tag before every candidate.
- **Appears guaranteed:** cross-crew review independence. It is round-robin by prompt; nothing records who reviewed what except `.omni/reviews.md`, written by the program.
- **Failure mode:** rubber-stamping, which the retro skill already watches. Not a mechanism problem; leave it.

### 2.11 Deterministic release gates
- **Guaranteed:** `tests.yml` on every push/PR: unit suite, policy import, `bash -n`.
- **Appears guaranteed:** that the release process is deterministic. It is 160 lines of prompt (`release/config.yaml:61-95`). The only machine gates are CI green + branch protection.
- **Failure mode:** release skips a step under context pressure. Cheapest hardening: move steps 1–3 of the release prompt (tag, scratch merge, run suite) into `bin/nightshift-candidate.sh` so the agent runs one command and reports its JSON. Seam B (it is a bin script with no ctl coupling) — but see §7: lower value than it looks; defer.

### 2.12 Test coverage
- **Guaranteed:** control plane is well covered — races, TTL, restart, stress, policy, PR-create, rollback (`tests/test_*.py`, 1,200 lines).
- **Not covered at all:** `provision-ubuntu.sh`, `bootstrap.sh`, `preflight.sh`, `nightshift-watchdog.sh`, `nightshift-state-backup.sh`. Only `deploy-lib.sh`'s pure functions are tested (`tests/test_deploy_lib.py`). All four confirmed bugs are in the untested 1,100 lines. `bash -n` in CI proves syntax, not behaviour.
- **Failure mode:** exactly what happened on the Hostinger install.

### 2.13 Install / deploy reliability
- `set -euo pipefail` + pipelines that legitimately close early (`head -1`) or legitimately fail (probe for an absent binary) is the root pattern behind bugs 1 and 2; it recurs at `provision-ubuntu.sh:221`, `:227`, `:241-243`. Bug 3 is a session-model assumption (§3.3). `bootstrap.sh` is claimed idempotent (`:6`) but line 80 writes `/tmp/nightshift-ctl-boot-check.db` and `rm -f …*` only on success — a failed selftest leaves the glob behind and the second run's `selftest` reuses it. Trivial, but "idempotent" should be tested, not asserted.

---

## 3. Root-cause reads of the four confirmed bugs

### 3.1 Go version discovery fails under `set -o pipefail`
- **File/mechanism:** `deploy/provision-ubuntu.sh:221` —
  `go_want=$(curl -fsSL 'https://go.dev/VERSION?m=text' | head -1 | sed 's/^go//')`
  under `set -euo pipefail` (`:41`). `go.dev/VERSION?m=text` returns **two** lines (`go1.xx.y` then a `time …` line). `head -1` exits after the first; if curl has not finished writing, it gets `SIGPIPE`/exit 23, `pipefail` makes the pipeline non-zero, the assignment inherits that status, and `errexit` terminates the script — *before* the friendly "pin NIGHTSHIFT_GO_VERSION" message at `:222-225` can print. Racy by nature (small body usually lands in one write), which is why it reads as flaky. Same pattern at `:241-243` (`jq … | head -1`).
- **Smallest safe fix:** never put an early-exiting consumer after a network producer under pipefail: `sed -n '1{s/^go//p;q}'` still quits early; use `awk 'NR==1{sub(/^go/,""); print}'` (consumes all input) or capture first then slice: `body=$(curl …) ; go_want=${body%%$'\n'*}`.
- **Regression test shape:** `tests/deploy/test_provision_go.sh`: put a stub `curl` on `PATH` that prints line 1, `sleep 0.2`, prints line 2 (forces the SIGPIPE window); run the Go-discovery block via `bash -euo pipefail`; assert exit 0 and `go_want` equals the stubbed version. Requires extracting the discovery into a function in `deploy-lib.sh` (`nightshift_go_latest <curl-cmd>`), which is also what makes it testable in CI without network.

### 3.2 Missing `/usr/local/go/bin/go` terminates provisioning
- **File/mechanism:** `deploy/provision-ubuntu.sh:227` —
  `go_have=$(/usr/local/go/bin/go version 2>/dev/null | awk '{print $3}' | sed 's/^go//')`. On a fresh box the binary does not exist → bash reports 127 for the first pipeline stage → `pipefail` → non-zero assignment → `errexit`. The install branch at `:230-258` is never reached. The very line meant to detect "not installed" is fatal on "not installed". The `${go_have:-0}` default at `:228` shows the author expected an empty string, not an exit.
- **Smallest safe fix:** `go_have=""; if [ -x /usr/local/go/bin/go ]; then go_have=$(/usr/local/go/bin/go version 2>/dev/null | awk '{print $3}' | sed 's/^go//') || go_have=""; fi`. Keep the `|| go_have=""` — a corrupt binary (exit ≠ 0, e.g. wrong arch) must also route to "install", which is the documented intent of `nightshift_version_ge` (`deploy-lib.sh:281-284`).
- **Regression test shape:** parameterise the root (`NIGHTSHIFT_GO_ROOT=${NIGHTSHIFT_GO_ROOT:-/usr/local/go}`), run the block against an empty tmp root with stubbed `curl`/`sha256sum`/`tar`, assert the install branch is taken; then against a root containing a stub `go` that prints `go version go1.99.0 linux/amd64`, assert the skip branch. Both under `bash -euo pipefail`.

### 3.3 `systemctl --user` after `su - nightshift` — "Failed to connect to bus: No medium found"
- **File/mechanism:** `deploy/bootstrap.sh:176-197` (`daemon-reload`, `enable --now`, `enable`) assumes a user D-Bus session. `systemctl --user` needs `DBUS_SESSION_BUS_ADDRESS` or, failing that, `XDG_RUNTIME_DIR` pointing at `/run/user/<uid>` where `systemd --user` has put its bus socket. `su -` (and `sudo -iu`, which `deploy/README.md:146,374` recommends) runs PAM but **not** `pam_systemd`'s logind session in a way that exports those variables; the user manager may already be running (linger was enabled at `provision-ubuntu.sh:345`) but the shell cannot find it. `docs/providers/hostinger/README.md:70` literally instructs `su - nightshift`. The same latent assumption is in `nightshift-watchdog.sh:72-73` and `preflight.sh:227-244`, which happen to work only because they run *inside* user units / Tailscale-SSH sessions.
- **Smallest safe fix:** at the top of `bootstrap.sh` (and `preflight.sh`): if `DBUS_SESSION_BUS_ADDRESS` is unset, `export XDG_RUNTIME_DIR="/run/user/$(id -u)"; export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"`; then `[ -S "$XDG_RUNTIME_DIR/bus" ] || { echo "no user bus — run as root: loginctl enable-linger $USER && sleep 2, then re-run; or log in via Tailscale SSH"; exit 1; }`. Put the two exports in `deploy-lib.sh` as `nightshift_user_bus_env` so all three scripts share one copy. Change the docs to `machinectl shell nightshift@` or Tailscale SSH; drop `su -`/`sudo -iu` as the documented path.
- **Regression test shape:** `env -i HOME=/tmp/h USER=x bash -euo pipefail` running the unit-install section with a stub `systemctl` on `PATH` that exits 1 with "Failed to connect to bus" unless `DBUS_SESSION_BUS_ADDRESS` is set and its socket path exists (create it with `python3 -c 'socket…bind'` in the test); assert the section succeeds with the socket present and fails with the *new* message (not the bus message) without it.

### 3.4 Watchdog liveness — a design question, not a patch
- **File/mechanism:** `deploy/nightshift-watchdog.sh:53` — liveness = newest mtime of *any file* under `.omni/`. Three non-Program writers keep that fresh forever:
  1. `nightshift-state-backup.sh:127-129` rewrites `.omni/control-backup.json` every 30 min (`dump` embeds `generated_at`, so the bytes always change even if nothing did);
  2. `.omni/control.db`, `control.db-wal`, `control.db-shm` live under `.omni/` and are gitignored but not `find`-ignored — **the watchdog's own `slot renew-active` (`:61`) writes the WAL**, so the watchdog refreshes its own liveness signal;
  3. any worker or lead calling `nightshift-ctl` (same WAL write).
  Consequences: (a) the watchdog can never alert — its only output is a false "healthy"; (b) because "healthy" also means "renew all slots", dead workers' slots are renewed for 24 h (§2.5, §2.6). The backup timer and watchdog both fire every 30 min with `OnUnitActiveSec`, so the freshest file is routinely < 30 min old.
- **Why a one-line exclusion is not enough:** excluding `control-backup.json` and `control.db*` leaves (3), and leaves the deeper error — *"files changed" is not "the Program ran"*. Leads and workers write `.omni/` too. Any mtime heuristic measures "somebody", not "the Program".
- **The correct liveness signal:** an explicit, attributed beat written by the Program at the one point that proves the autonomy loop is working — the start of each heartbeat turn — and read by name, not inferred.
  - **Write side (Seam A):** `nightshift-ctl beat --who program` → `INSERT OR REPLACE INTO meta(key,value) VALUES('beat:program', json{at, session_id, turn})`. The `meta` table already exists (`nightshift-ctl:53`), so no schema migration. Also expose `nightshift-ctl beat show --who program` → `{age_seconds, at, session_id}` exit 0 fresh / 3 stale / 3 absent. The hourly heartbeat prompt gains one line: "first command of the turn: `nightshift-ctl beat --who program`". It goes in `meta`, not a file, because a file under `.omni/` is exactly what the backup snapshot and worktree sandboxes already write and mask.
  - **Read side (Seam B):** the watchdog calls `beat show` and uses *only* `age_seconds`; delete lines 50-56. Staleness stays at 3 h (three missed hourly beats). Absent beat on an initialised project = stale (a program that never beat is a program that never ran).
  - **Slot renewal must stop keying off org liveness.** "The Program is alive" says nothing about *this worker*. Renew only slots that are dispatched (`dispatched_at IS NOT NULL`) **and** whose holder's crew lead has beaten within 2 × the renew interval (`beat --who crew_alpha` at every lead turn, same mechanism; leads are also Omnigent sessions with `sys_read_inbox` loops). Keep `--max-age 24h` as the backstop. Under this rule a host restart, a lead crash, or a silent lead all stop renewal, and TTL recovery takes over within one slot TTL — which is what the docstring at `nightshift-ctl:26` already claims.
  - **Self-write exclusion:** the watchdog's own ctl calls update the WAL; that is fine once mtime is gone. The backup timer's `dump` is fine too. Nothing in Seam B may ever write a beat.
- **Regression test shape:** `tests/deploy/test_watchdog.sh`: tmp project dir with `.omni/`, stub `nightshift-ctl` on `PATH` whose `beat show` returns a configurable age, stub `gh` that records calls. Cases: (1) beat age 1 h + `control-backup.json` touched 1 min ago → no alert, renew called; (2) beat age 4 h + every file under `.omni/` touched now (including `control.db-wal`) → **alert posted, renew NOT called**; (3) no beat row → alert; (4) STATUS.md first line `complete` → exit 0, nothing called. Case (2) is the regression for this bug; it must fail against the current script.

---

## 4. Capacity arithmetic (before any technology claim)

Inputs: `capacity.json` = 4 concurrent workers, 1 reserved; run = 3–7 days; 2 crews; vendors = one Claude subscription + one Codex subscription.

**Control-plane write rate.** Per worker dispatch: `slot acquire` (1 write), policy consume (1), `task upsert` (1), on result `slot release` (1), occasionally `claim acquire/release` (2) → ≤ 6 writes. A worker turn is 10–90 min; 4 slots → ≤ 24 dispatches/h → ≤ 150 writes/h. Background: watchdog `renew-active` 2/h, backup `dump` 2/h (read), heartbeat `reap`+`dump` ~2/h, leads' `slot list`/`lease` calls ~10/h. Total **< 200 transactions/hour ≈ 0.05/s**, each a single-row `BEGIN IMMEDIATE` lasting ~1 ms. `test_stress.py` drives 6 crews × 4 tasks with 24 threads and a 10 ms sampler and observes no violation — that is ≈ 1,000× the production rate. Lock contention with `busy_timeout=30 s` is unreachable; the 5 s timeout in the policy (`omnipods_policies.py:131`) is unreachable too.

**Rows / size over 7 days.** ≤ 24 dispatches/h × 168 h ≈ 4,000 slot rows, ~4,000 task rows (upserts, fewer), a few hundred claims/leases, ≤ 50 checkpoints. **< 10,000 rows, < 5 MB** including WAL. `dump`'s `SELECT * FROM tasks` (unbounded, `nightshift-ctl:941`) returns ≤ 4,000 rows into a JSON the backup commits every 30 min — a 1–2 MB file, committed 336 times → the `omni/state` branch grows ~300–600 MB of *deltas* over a week (git compresses JSON well; realistically 50–100 MB). Not a correctness problem; a cleanliness one (§7: stop embedding `generated_at`; `dump` only tasks not `integrated`/`dropped`, or cap with `ORDER BY updated_at DESC LIMIT 500`).

**Checkpoints / inbox.** ≤ 1 checkpoint per wave; `sys_read_inbox` payloads are worker reports (KB each); no control-plane involvement.

**GitHub API.** Per task: create PR (1), ~5 views/edits, review comments (~5), merge (1), issue comments (2) ≈ 15 calls; 24 tasks/day → 360/day. Heartbeat: issue list/search + status comment ≈ 10/h → 240/day. Watchdog: ≤ 2/h (only when stale). CI: free. **≈ 700 calls/day against a 5,000/h PAT limit** — 0.6 % of budget. Secondary "content creation" limits (~80 comments/min) are unreachable.

**Vendor quota per wave — this is the real constraint.** Actual concurrency is up to 9 sessions (§2.5): 1 program (claude-sdk, continuous), up to 4 program-level children, 4 worker slots, split roughly 5 Claude / 4 Codex. A Claude Code session at full tilt consumes a Max-20× 5-hour window in roughly 2–3 session-hours on Opus-class models; two to three continuous Claude sessions therefore hit the window limit every 5 h, and the **weekly** cap plausibly by day 3–4 of a 7-day run. The cooldown ladder (30 min → 4 h) handles window limits correctly; it has no concept of a weekly cap (§2.7). Codex plans behave similarly with different numbers. Conclusion: **4 is already optimistic; 9 is unbudgeted.**

**Does SQLite hold?** Yes, plainly, by three orders of magnitude. It stops holding when either (a) a second host needs the same control plane (SQLite over NFS is not a database) or (b) a human needs to query it from elsewhere while the run is live. Neither is on the 7-day horizon. The only trigger on the ladder for this concern is "a second box"; until then the stdlib-only, zero-build choice in `docs/CONTROL-PLANE.md` is correct and must not be revisited. No replacement technology is named here because the arithmetic does not demonstrate the need for one.

**Monthly cost.** Today: one 32 GB KVM VPS (~$25–45/mo) + two subscriptions (~$200 + ~$200) + GitHub (free/Team) ≈ **$450–500/mo**, 90 % subscriptions. At 100×: this is a single-box, single-project system; 100× means 100 boxes and 200 subscriptions ≈ $45k/mo, all linear, no shared infrastructure to amortise. The only thing that would change the shape is the vendors offering pooled enterprise quota — not an engineering question.

---

## 5. THE SEAM COUNT — **two**

Two workstreams with disjoint file scope exist. A third can be manufactured from the prompt/YAML tree but it has almost no tests, no deterministic acceptance, and would spend worker slots producing prose; the program should own it directly (it already writes Markdown) or grant it zero.

### Seam A — Control plane
- **Owns:** `bin/nightshift-ctl`, `deploy/policies/omnipods_policies.py`, `tests/test_*.py` except `test_deploy_lib.py`, `tests/ctlutil.py`, `tests/run.sh`, `docs/CONTROL-PLANE.md`.
- **Publishes (handoff `.omni/handoffs/ctl-beat.md`):** `nightshift-ctl beat --who <name>` / `beat show --who <name>` JSON contract and exit codes; `slot reap-all --reason <r>`; `renew-active` semantics (dispatched-only, lead-beat-gated). Existing CLI contract is stable and versioned by `spec_version` in `meta` (add `schema_version` to `meta` now — free, ruinous later).
- **Consumes:** nothing from B. Consumes one prompt line from the program (beat at heartbeat), which the program itself edits.
- **Work:** §2.1 task-duplicate deny, §2.3 claimant-checked claim release, §2.5 renew/leak fix, §2.7 vendor-aware semaphore, §3.4 beat, §2.6 reap-all, dead `agent_runs` removal, `dump` bounding.

### Seam B — Deploy / provision / recovery
- **Owns:** `deploy/**` except `deploy/policies/`, `tests/test_deploy_lib.py`, new `tests/deploy/**` (bash tests), `tests/linux/**`, `.github/workflows/tests.yml` (add the bash-test job and a `pr-lint` required check), `deploy/README*.md`, `deploy/providers/**`.
- **Publishes:** nothing other crews consume in this run.
- **Consumes:** Seam A's `beat show` and `reap-all` contracts — **by handoff file only**, and B can stub both (`tests/deploy/stubs/nightshift-ctl`) and proceed before A merges. That is the property that makes these two seams genuinely parallel: B's tests never exec the real ctl.
- **Work:** bugs 1–3, watchdog read side (3.4), `ExecStartPre` reap-all on host, user-bus helper in `deploy-lib.sh`, delete the rtk section, bash regression tests for all five scripts, fix `su -`/`sudo -iu` docs.

### Not a seam — Org prompts and skills
`program/**`, `AGENTS.md`, `README.md`, `docs/TRUST-BOUNDARY.md`. Touched by the program itself for the 1–2 prompt lines A and B need (beat call; `pm`-style semaphore attachment on the program's own dispatches). Giving this a crew would inflate the count to three and buy lease contention on `program/config.yaml`, which both A and B's handoffs reference.

Shared-file contention check: A and B touch no common path. `.github/workflows/tests.yml` is B's (it is `platform`-owned per `docs/owners.md`; treat B as platform for this run). `docs/CONTROL-PLANE.md` is A's. `AGENTS.md` is nobody's this run.

---

## 6. Sequencing and risk

**First, because everything depends on it:** bugs 1–3 (Seam B, one PR). Until a fresh box provisions, nothing in this run can be verified end-to-end on a clean clone, and the Definition of Done should require exactly that.

**Second, in parallel:** Seam A's beat + renew/leak + reap-all; Seam B's watchdog read side against a stub. These are the 7-day-survival fixes (§2.5, §2.6, §3.4). Merge A first, then B flips from stub to real in its CI job.

**Third:** §2.1 (task-duplicate deny), §2.3, §2.7 — correctness hardening that is cheap now.

**Fourth:** the program-level semaphore attachment (YAML; program-owned) — one-line change, large effect on vendor behaviour.

**One-way doors (take none of them this run):** a second GitHub identity for reviews; rootless Docker; changing the `omni/state` branch layout (restore instructions are printed in the script and in READMEs); renaming any ctl subcommand (prompts across two frozen controllers reference them by name — `/srv/nightshift` is frozen and runs *this* ctl by symlink, `bootstrap.sh:79`). Adding subcommands and `meta` keys is additive and reversible.

**Reversible:** every prompt change; TTL numbers in `capacity.json`; watchdog thresholds; CI jobs.

**Top three risks to a 3–7 day unattended run, ranked:**
1. **Liveness lie + slot renewal** (§3.4, §2.5, §2.6): the org dies or loses capacity and the only detector reports healthy. Probability over 7 days: high (one reboot, one OOM, or one leaked slot is enough).
2. **Subscription exhaustion** (§2.7, §4): the real concurrency is ~2× the documented budget; the weekly cap has no handling and no owner notification. Probability by day 4: medium-high.
3. **The hourly heartbeat row living in the frozen controller's Postgres**: if the server restart loses it, the program's own instruction to "verify after any restart" cannot execute (the watchdog header at `nightshift-watchdog.sh:5-12` says this correctly). The fixed watchdog turns this from silent into a phone alert within 3 h — but it is still a stop, not a resume. Out of scope to fix (frozen controller); in scope to detect.

---

## 7. What to cut

**Lower value than it looks in the brief's audit list**
- *Deadlocks:* theoretical lease cycle, program already arbitrates; do not build detection. Shorten the edit TTL and report old leases. One hour of work, not an audit.
- *Security boundaries:* `TRUST-BOUNDARY.md` already names the docker-socket root-equivalence and declines to fix it. Re-auditing produces a document that says the same thing. Spend nothing here beyond the CI `pr-lint` check.
- *Deterministic release gates beyond CI:* a `nightshift-candidate.sh` is nice; it does not change 7-day survival. Defer to a later wave if the run is healthy.
- *Test coverage as a number:* the control plane is already well tested; the deploy layer is untested. Target the deploy layer only; do not write more ctl tests unless they accompany a ctl change.
- *GitHub/PR/review workflow audit:* the mechanisms that exist work; the gaps are social (rubber-stamping) and the retro already measures them.

**Delete rather than test**
- `agent_runs` table, `run start/end` subcommands, and the `agent_runs` update in `expire_stale` (`nightshift-ctl:223-227`, `:712-735`). Nobody writes it. Migration: leave the table in old DBs, stop touching it.
- `.omni/leases/` hand-mirror (`program/config.yaml:170`, `allocate/SKILL.md:95`, `retro/SKILL.md:35`): a projection written by an LLM from memory is the one that will be wrong. `nightshift-ctl lease list` is the record; `dump --write` is the projection.
- One of `control-state.json` / `control-backup.json`. Keep the backup one (it rides `omni/state`); delete `dump --write`.
- `bootstrap.sh:39-72` (rtk via Homebrew). Not Nightshift's concern; a user-installable extra with a README line.
- `max_items_in_flight` from `capacity.json` and `nightshift-init.sh:197`, or make ctl read it. A number nothing reads is a false control.
- `generated_at` in `dump` output, so the backup snapshot is a no-op when nothing changed (`nightshift-state-backup.sh:142-144` already short-circuits on identical trees — it just never gets the chance).
- The duplicated `consume_slot` (`nightshift-ctl:150-167` vs `omnipods_policies.py:135-158`): not deletable this run (`nightshift-ctl` has no `.py` extension to import from, and the policy must not `exec` a subprocess per tool call). Register in `.omni/tech-debt.md` with trigger "a third copy, or either copy changes".
