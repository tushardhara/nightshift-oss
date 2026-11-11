# Running the crew on a Ubuntu VPS, unattended, for days

> **Production runtime authority:** use
> [EXACT-SHA-CUTOVER.md](EXACT-SHA-CUTOVER.md). The generic bootstrap/start
> instructions below provision a box; they are not evidence that the served
> Program matches reviewed Git. Issues #107/#115 are accepted only by the
> exact-SHA receipt, never by merge/restart/CI or health alone.

Nightshift deploys to **any normal Ubuntu 24.04 machine** — Hostinger, AWS,
Hetzner, E2E, DigitalOcean, bare metal. The provider is an operational choice,
not an architectural dependency: everything below is provider-neutral, and the
small genuinely provider-specific parts (launch mechanics, provider firewall,
disk snapshots) live in `providers/<name>/`.

Mode chosen: **fully autonomous** · **subscription tokens on the box** ·
**Tailscale access**. Crews open PRs, a different crew reviews each one, and the
`release` integrator merges — no human approves anything. The configs in
`../program/` are already patched for it.

## What "unattended" changed in the bundle

| Change | Why |
|---|---|
| `UNATTENDED MODE` block in the program prompt | no approval gates; reversible-path rule; escalations go to `.omni/decisions.md`; status to `.omni/STATUS.md` |
| plan gates removed from `design-debate` / `roadmap` | the ADR is the gate now; PRs are what you review |
| `ask_thresholds_usd` removed everywhere | an ASK with nobody to answer parks the turn for `ask_timeout` (24h) and burns a day |
| cost policies became hard caps (`1000` session / `8333` daily / `667` per crew) | past the cap `cost_budget` DENYs only while on an *expensive* model, so the run degrades to a cheaper model instead of halting. **Notional dollars** — both vendors are subscriptions, nothing is metered; the caps read Claude Code's token-priced `total_cost_usd` and act as runaway detectors, not spend limits |
| `detect_thrashing` action `ASK` → `DENY` | a thrashing worker unattended should be stopped, not parked; the lead re-dispatches. **NOT ENFORCED** — declared per-seat as `stuck_detector`, and per-seat policies never reach the engine (#118, proven; `docs/audit/118-per-seat-policy-probe.md`). The `ASK` → `DENY` change is real in the YAML and inert at runtime |
| `yolo: false` on all Codex workers + bootstrap-written `~/.codex/config.toml` (`approval_policy="never"`, `sandbox_mode="workspace-write"`) | unattended AND sandboxed: nothing waits on a human, writes confined to worktree + declared caches, a blocked write fails visibly. `blast_radius` still DENYs force-push, `rm -rf /`, hard reset. Escape hatch for a build the sandbox genuinely cannot run: flip that one seat back to `yolo: true`, record it in `.omni/decisions.md` |
| `worker_semaphore` + `pr_close_keyword_guard` custom policies (deploy/policies/) | **NOT ENFORCED AT THE TOOL-CALL LAYER.** Both are declared on crew leads and workers, never at the bundle root, and per-seat policies never load (#118, proven). They are prompt and dispatch discipline today. What the policies were built to do, unchanged as intent: A slot is a one-dispatch capability (bound to its agent, consumed by the send it authorizes); the semaphore FAILS CLOSED when control.db is missing or SQLite errors (escape hatch: `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1`); PR bodies from files go through `nightshift-ctl pr-create`, raw `--body-file`/`--fill` is denied. Server needs `PYTHONPATH` from `server.env` (bootstrap writes it; preflight checks the import) |
| `external_repo_merge_fence` custom policy (deploy/policies/, ADR-0085) | **ENFORCED AT THE TOOL-CALL LAYER** — declared at the BUNDLE ROOT in `program/config.yaml`, unlike the row above. DENIES `gh pr merge` and the equivalent `gh api .../pulls/<n>/merge` unless `bin/nightshift_authority.py`'s `classify_repository` reads a present, declared `ownership='self'` row from `<state_root>/authority/<repository_id>/authority.db`, addressed by exactly `NIGHTSHIFT_STATE_ROOT`/`NIGHTSHIFT_REPOSITORY_ID` in `server.env` (bootstrap writes both when `STATE_ROOT`/`REPOSITORY_ID` are passed; preflight checks the import). FAILS CLOSED WITH NO ESCAPE HATCH — deliberately no `..._FAIL_OPEN` variable, because a wrongly-allowed merge into a repository this deployment does not own cannot be undone the way a re-queued dispatch can. Bootstrap: `docs/runbooks/repository-ownership-bootstrap.md`. |
| orchestrator terminals sandboxed (`type: auto` → bwrap) | `$HOME` never mounted: project commands run by program/leads cannot read `~/.ssh`, `~/.aws`, `~/.claude`, `~/.codex`, `~/.config` beyond the explicit gh grant |
| stuck-task rule in each lead | after 3 review loops a task is parked to `.omni/decisions.md` and the wave moves on |

Autonomy loop uses in-product primitives, so no external babysitter script:
`.omni/goal.md` plus `docs/prd/DEFINITION-OF-DONE.md` hold the completion
condition; `sys_scheduled_task_create` (`FREQ=HOURLY`) is what returns the program
to them after every turn; `sys_timer_set` sleeps out a vendor cooldown and wakes
the session.

**Not** Omnigent's Goal mode — that is Codex-only (`/v1/sessions/{id}/codex_goal`
answers a non-Codex session with `codex_goal is only supported for codex-native
sessions`), and the program runs `claude-sdk`. The file-plus-scheduled-task pair is
what makes this survive days, and it is also what makes it survive a rebuild: both
halves live outside the session.

## The box

| | |
|---|---|
| OS | Ubuntu 24.04 LTS |
| RAM | 16 GB minimum; the current 5-worker first trial is intended for the 32 GB primary VPS |
| Disk | 100 GB+ persistent VPS/local volume (worktrees + node_modules for days) |
| Network | a way in for ONE first SSH session; after that, Tailscale only |
| Current primary | Hostinger KVM 8, Mumbai (8 vCPU / 32 GB / 400 GB NVMe) — see `providers/hostinger/` |

Sizing driver: each `*-native` worker is a Node CLI plus a tmux pane (~0.5 GB),
the server idles ~275 MB RSS, Postgres wants its buffers, and your test suite
needs the rest. The current first-trial cap is 5 concurrent workers total — one global budget
in `.omni/capacity.json`, split across crews by per-wave grants, never per-crew
caps. **More RAM does not raise that budget**: the constraint is the two vendor
subscriptions, not the hardware.

`omnigent-host.service` pins `MemoryHigh`/`MemoryMax` so a runaway worker gets
killed instead of the box. The values are **derived from detected RAM** at
bootstrap (`lib/deploy-lib.sh`): a 16 GB box gets `11G`/`13G`, a 32 GB box
`24G`/`28G` — always leaving a reserve for the OS, Docker+Postgres, the omnigent
server, Tailscale and journald. Override with `NIGHTSHIFT_MEMORY_HIGH` /
`NIGHTSHIFT_MEMORY_MAX` at bootstrap if you know better; preflight warns when
the installed unit no longer matches the machine.

## What workers can build with

Provisioning installs the toolchains the agent prompts promise them, so a worker
never has to install a language mid-task:

| | |
|---|---|
| Go | upstream tarball in `/usr/local/go`, linked to `/usr/local/bin`. Latest stable by default; pin with `NIGHTSHIFT_GO_VERSION=x.y.z` |
| Rust | `rustup`, default profile (so `rustfmt` and clippy exist), owned by the runtime user, linked into `~/.local/bin` |
| Node 22 / Python 3.12 | the harnesses themselves, plus whatever the project needs |
| Docker + compose | Postgres, and whatever a test needs to stand up |
| floci | local cloud emulators — one binary, product groups: `floci start` (AWS, :4566), `floci az start` (:4577), `floci gcp start` (:4588), `floci oci start` (:4599) |

PATH is the subtle part: workers are launched by **systemd user units**, whose
`PATH` is set in the unit file — `/etc/profile.d` is never read. So each
toolchain is linked into a directory already on that PATH (`/usr/local/bin`,
`~/.local/bin`) rather than relying on a login shell. `/etc/profile.d/nightshift-toolchains.sh`
exists only so a human who SSHes in sees the same tools.

**floci is a security control, not just convenience.** Cloud code can be built
and integration-tested with *no real cloud credentials on the box* — which is
what preflight's cloud-credential check exists to keep true. An emulator-backed
result must be reported as emulator-backed: it proves nothing about IAM, quota,
regional behaviour or cost. The `architect`, `staff`, `platform`, `eng_*` and
`qa` prompts all carry both halves.

**Keeping the emulators private is enforced, not remembered.** floci drives
Docker containers and exposes no bind-address flag, so `floci start` would
publish on `0.0.0.0` by default — and a preflight check only notices *after*
the port is already public. Provisioning therefore sets Docker's daemon-level
default publish address:

```json
/* /etc/docker/daemon.json */
{ "ip": "127.0.0.1" }
```

Every published port that does not name a host IP now lands on loopback. The
setting is merged into an existing `daemon.json` rather than overwriting it, and
Docker is restarted only when the file actually changed. Its one limit, stated
plainly: an explicit `-p 0.0.0.0:X:Y` still wins, so preflight keeps checking
the live listeners *and* checks that the daemon default is in place.

Skip the whole section with `NIGHTSHIFT_SKIP_TOOLCHAINS=1` on a box that only
runs the control plane. Preflight then **warns** rather than fails — an older
install still runs the Program, it just cannot build Go/Rust or emulate a cloud.

## The layout and the runtime user

Everything runs as one dedicated **non-root** user — default name `nightshift`,
no sudo (by design: workers must not be able to escalate; see
`../docs/TRUST-BOUNDARY.md`):

| Path | What |
|---|---|
| `/home/nightshift` | credential homes (`~/.claude`, `~/.codex`, gh), CLIs (`~/.local/bin`), user systemd units, Postgres compose dir |
| `/srv/nightshift` | this bundle (agents, skills, policies, deploy) |
| `/srv/project` | the repo the crews build |

Older installs that used EC2's `ubuntu` user keep working: `provision-ubuntu.sh`
adopts the existing owner of `/srv/nightshift` instead of forcing a migration,
and every script derives paths from `$HOME`/`$USER`.

## Bring-up

```bash
# 1. as root, once (idempotent) — installs python 3.12, node 22, go, rust,
#    docker, tmux, bubblewrap, gh, tailscale, postgres client, floci (local
#    cloud emulators), fail2ban, creates the nightshift user + /srv/nightshift
#    + /srv/project, journald limits, disk guard, swap. Every download is
#    pinned and sha256-checked against deploy/lib/installer-pins.sh:
sudo bash deploy/provision-ubuntu.sh

# 2. as root: join the tailnet (Tailscale SSH on). The runtime user holds no
#    tailscale operator permission (see docs/TRUST-BOUNDARY.md). The UI is NOT
#    published to the tailnet by default — read "Control API" below first.
sudo tailscale up --ssh

# 3. put the bundle in place (owned by ROOT: it configures the fences the
#    runtime user is fenced by), then become the runtime user:
sudo rsync -a --chown=root:root ~/.nightshift/ /srv/nightshift/  # or git clone
sudo -iu nightshift

# 4. as nightshift:
/srv/nightshift/deploy/bootstrap.sh

# 5. as root: install the root-owned /etc/nightshift/server.env (bootstrap
#    prints this line with any --state-root/--repository-id it was given)
sudo /srv/nightshift/deploy/install-root-config.sh --user nightshift
```

### The three interactive steps

These need a human once, because subscriptions are OAuth, not keys:

```bash
claude setup-token     # long-lived subscription token; paste the browser code
codex login            # device auth for the Codex subscription
gh auth login          # scope: repo. Do NOT grant admin — agents must not merge
```

**No API keys are involved, for any seat.** `claude setup-token` is the only
Claude credential: it writes the OAuth token to `~/.claude/.credentials.json`, and
both `claude-sdk` and `claude-native` read it. The `claude-sdk` executor
deliberately strips `ANTHROPIC_API_KEY` before connecting so subscription auth is
never bypassed by a metered key — "SDK" names the Claude Code SDK, not an API-key
mode. Do not set `ANTHROPIC_API_KEY` on this box; it would be discarded anyway.

Then the project:

```bash
git clone <repo> /srv/project
/srv/nightshift/bin/nightshift-init.sh /srv/project
/srv/nightshift/bin/nightshift-tracker-init.sh /srv/project   # labels, templates, board
$EDITOR /srv/project/.omni/brief.md     # a paragraph — discovery derives the rest
/srv/nightshift/deploy/preflight.sh /srv/project
```

`preflight.sh` is not decoration — it catches the failure modes that waste a
whole day: a missing `codex` binary (silently drops cross-vendor review), a
`brief.md` still holding template text, missing branch protection, linger off (so
services die at logout), the server or Postgres bound to a public interface.

## Security posture

The rules, in order of importance:

1. **Port 6767 and Postgres must never be reachable from the public internet.**
   The omnigent server binds `127.0.0.1:6767` (it holds your subscription
   credentials) and the Postgres container publishes `127.0.0.1:5433` only.
   Never rebind either to `0.0.0.0`, never add a provider-firewall or port
   forward for them. Preflight FAILs if either listens publicly.
2. **Public SSH exists only for first provisioning.** After `tailscale up --ssh`
   works, close it (below).
3. **All human access rides the tailnet**: Tailscale SSH for the shell, and an
   SSH tunnel for the UI. Tailscale Serve only if you opt in (below).

### Control API (6767): no authentication

Read this before exposing the UI anywhere. The omnigent server on
`127.0.0.1:6767` is the UI **and** Nightshift's control API, and in this
deployment it has **no authentication**. Omnigent (at the commit pinned in
`omnigent-runtime.lock.json`) starts a loopback-bound `omnigent server` in
single-user mode — `OMNIGENT_LOCAL_SINGLE_USER` is set implicitly — so every
request without credentials is served as the `local` user. Anything that can
open a TCP connection to it can list and create sessions, send to them, and
run or pause scheduled tasks, which bypasses every tool-call fence. That is:

- every process on the box — every Claude seat, and project code run in the
  orchestrator terminals (the bwrap sandbox keeps the network on, and loopback
  is shared);
- every tailnet device your ACL lets reach it, **if** you publish it with
  `tailscale serve`.

**Default: loopback only.** The server binds `127.0.0.1` and provisioning does
not publish it. Reach the UI through Tailscale SSH:

```bash
ssh -L 6767:127.0.0.1:6767 nightshift@<machine>   # then open http://127.0.0.1:6767
```

**Opt in to Tailscale Serve** only together with an ACL that restricts the box
to you. Tag the machine (`sudo tailscale up --ssh --advertise-tags=tag:nightshift`),
then in the admin console's policy file allow only the owner to reach it, and
remove any broader rule (such as `"dst": ["*:*"]`) that also covers it:

```json
{
  "tagOwners": { "tag:nightshift": ["autogroup:admin"] },
  "acls": [
    { "action": "accept", "src": ["owner@example.com"], "dst": ["tag:nightshift:443"] }
  ],
  "ssh": [
    { "action": "check", "src": ["owner@example.com"], "dst": ["tag:nightshift"],
      "users": ["nightshift", "root"] }
  ]
}
```

Then publish it, as root (provisioning does this when re-run with the flag
after `tailscale up`):

```bash
sudo NIGHTSHIFT_TAILSCALE_SERVE=1 bash /srv/nightshift/deploy/provision-ubuntu.sh
# or by hand:
tailscale serve --bg 6767      # tailnet-only HTTPS -> http://127.0.0.1:6767
tailscale serve status         # confirm
tailscale serve reset          # undo
```

- `--bg` stores the config in tailscaled's state, so it **persists across
  reboots**. A default re-provision warns if an old serve config is still
  publishing 6767.
- The UI is then at `https://<machine>.<tailnet>.ts.net/` from the devices the
  ACL admits. An ACL does nothing about processes on the box itself.
- First use may prompt to enable HTTPS certificates / MagicDNS for the tailnet —
  accept.
- **Root-only, structurally.** The runtime user is deliberately NOT a
  tailscale operator: every Claude/Codex worker runs as that user, and
  operator rights would let a compromised worker reconfigure the tailnet —
  including `tailscale funnel`, the *public* variant. Keeping tailscale
  management root-only makes "never expose 6767" a structural property, not a
  documentation rule. Since Serve persists on its own, the runtime user has no
  ongoing need to touch tailscale at all.
- Preflight (running as the runtime user) verifies the proxy with read-only
  evidence — an end-to-end HTTPS probe of the machine's own ts.net name, or
  the 443 listener — and treats "cannot read serve config" as expected, not a
  failure. On a default, loopback-only box its "no serve proxy" warning is
  expected too.
- **Never use `tailscale funnel`** — Funnel is the *public* variant and would
  put the server on the open internet.

**Omnigent accounts mode (hardening, not validated with Nightshift).** Omnigent
has an experimental login mode: `OMNIGENT_AUTH_ENABLED=1`,
`OMNIGENT_ACCOUNTS_COOKIE_SECRET=$(openssl rand -hex 32)` and
`OMNIGENT_ACCOUNTS_BASE_URL=http://127.0.0.1:6767` in the server environment,
then `omnigent login http://127.0.0.1:6767` once as the runtime user so the
host daemon and `omnigent run` carry a bearer. Nightshift does not turn it on,
because the rest of the loop has not been run under it: session tokens expire
(`OMNIGENT_ACCOUNTS_SESSION_TTL_HOURS`, default 8); the scheduler probe sends
`OMNIGENT_REMOTE_AUTH_TOKEN` from its environment (never argv) but the
watchdog unit that runs it loads no environment file yet; and the scheduled-
run detector and exact-SHA release tooling send no credential. Even when it
works, a token the runtime user can read is readable by every Claude seat:
accounts mode closes the tailnet and terminal-sandbox paths, not the seats.

### Locking public SSH down

Only after you have verified Tailscale SSH works
(`ssh nightshift@<machine>.<tailnet>.ts.net` from your laptop — no key setup
needed, Tailscale authenticates):

- **Provider firewall (preferred first line)**: allow nothing inbound, or at
  most temporary port 22 from your own IP. Tailscale traffic does not need any
  inbound rule (it establishes outbound). On AWS this is the security group; on
  Hostinger the hPanel firewall — see `providers/`.
- **Ubuntu firewall (defense in depth, works on any provider)**:

  ```bash
  sudo ufw allow in on tailscale0
  sudo ufw default deny incoming
  sudo ufw default allow outgoing
  sudo ufw enable
  ```

  Keep the first Tailscale SSH session open while enabling, and verify a second
  session connects before closing it. To also stop sshd answering on the public
  interface entirely: `sudo systemctl disable --now ssh` (Tailscale SSH does not
  use sshd) — or leave sshd running and let the firewall do the blocking, which
  keeps a recovery path via the provider's web console.

`fail2ban` (installed by provisioning) covers the window while port 22 is still
public.

## Starting the run

```bash
systemctl --user start omnigent-server omnigent-host
```

Then open the UI (`http://127.0.0.1:6767` through the SSH tunnel, or the
Tailscale Serve address if you opted in — "Control API" above) and start a session on the `program` agent (it is pre-registered by `--agent`
in the unit). First message:

> Read `.omni/brief.md` and run the discovery skill: three blind drafts (product,
> plus architect on Codex AND staff on Claude each writing their own architecture),
> reconcile the two architectures decision by decision, write the PRD, the
> definition of done, the milestones and the boundary map, generate `.omni/goal.md`,
> then charter milestone 1.
> Unattended — do not wait for me. Create your hourly heartbeat task first.

At the end of discovery the program creates its own hourly scheduled task — that,
plus `.omni/goal.md`, is what makes it survive turn boundaries for days. Confirm it
exists before you walk away:

> List your scheduled tasks and confirm the hourly heartbeat is there.

If it is missing, the run is one turn away from stopping silently. (There is no
**Goal** button to press here: Goal mode is Codex-only and the program is
`claude-sdk`.)

To drive it entirely from the terminal instead:

```bash
cd /srv/project && PYTHONPATH=/srv/nightshift/deploy/policies omnigent run /srv/nightshift/program --server http://127.0.0.1:6767
```

## Watching it without babysitting it

| Want | Do |
|---|---|
| One-screen status | `cat /srv/project/.omni/STATUS.md` |
| What it decided and why | `ls /srv/project/docs/decisions/`, `tail -50 /srv/project/.omni/ledger.md` |
| What it could not decide | `cat /srv/project/.omni/decisions.md` |
| Work product | `gh pr list` — the actual deliverable |
| Live agents | web UI → **Agents** panel; open any worker's terminal, or take over |
| Logs | `journalctl --user -u omnigent-host -f` |
| From your phone | the mobile UI at the Tailscale Serve address, if you opted in |

Check `.omni/decisions.md` daily. Everything else can wait for the PR review.

### Scheduled-run failure detector

The hourly task firing is not proof that its agent turn succeeded. ADR-0017's
detector reads Omnigent's durable run history every five minutes and comments on
the configured GitHub issue when a known occurrence is `failed`, `missed`, or
`stuck`. The process contains no LLM call and never restarts a service or edits
the task.

Install it only from a reviewed, sealed exact-SHA checkout. Replace the example
values first; the two unit files must render `__TEAMS_DIR__` to the same absolute
release root as the detector and `nightshift-ctl`, never to `/srv/project`:

```bash
release_root=/srv/nightshift-releases/<reviewed-sha>/checkout
test "$(git -C "$release_root" rev-parse HEAD)" = "<reviewed-sha>"
test -z "$(git -C "$release_root" status --porcelain=v1)"

install -m 0600 "$release_root/deploy/scheduled-run-detector.env.example" \
  ~/.config/omnigent/scheduled-run-detector.env
${EDITOR:-vi} ~/.config/omnigent/scheduled-run-detector.env

for unit in nightshift-scheduled-run-detector.service \
            nightshift-scheduled-run-detector.timer; do
  sed -e "s#__TEAMS_DIR__#$release_root#g" \
      -e "s#__PROJECT_DIR__#/srv/project#g" \
      -e "s#__HOME__#$HOME#g" \
      "$release_root/deploy/systemd/$unit" \
    > "$HOME/.config/systemd/user/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now nightshift-scheduled-run-detector.timer
systemctl --user start nightshift-scheduled-run-detector.service
systemctl --user status nightshift-scheduled-run-detector.service \
  nightshift-scheduled-run-detector.timer
```

That is a production state change: prepare and review the rendered unit/env
first, then enable it only under an explicit maintenance GO. Rollback is
`systemctl --user disable --now nightshift-scheduled-run-detector.timer`; leave
the additive observation rows intact as incident evidence.

This is parts 1–3 of #132. It does not detect total VPS loss and cannot prove
that its own GitHub credential or timer is healthy. The off-box dead-man and
monitor-the-monitor canary remain separate production-readiness gates; do not
relabel the same-host Program watchdog as either one.

## Two caches and two clocks — what silently wastes a night

Four things on a running box behave differently from what the filesystem shows.
Each one cost hours on 2026-09-05 before it was understood.

### Restarting the server is not enough — the host caches too

`omnigent server` and `omnigent host` are **separate processes**, and both import
their Python modules once at boot. A change to `deploy/policies/*.py` or to
anything under omnigent's `site-packages` is not served until the process that
imported it restarts.

```bash
ss -lptnH 'sport = :6767'        # the server
pgrep -af 'omnigent host'        # the host — spawns every runner
```

The host is the one people forget. Its symptom is indistinguishable from the
patch having failed: the file on disk is correct, and the running system behaves
as if it is not. Kill both after any module change.

### Verifying a fence from the shell proves nothing about the server

On 2026-09-05 the server had been running since 2026-09-02 with a policy bundle
**1254 commits behind** — from before ADR-0085 existed. Every policy evaluation
raised `AttributeError: module 'omnipods_policies' has no attribute
'external_repo_merge_fence'`, surfacing to agents only as the generic
`Denied by policy (policy evaluation error)`.

Every fence was inert for four days. It was checked three ways beforehand —
reading the source, running `bin/nightshift_authority.py` from the CLI, and
running the unit suites — and all three passed, because **all three measure the
filesystem, not the serving process**. There is currently no way to ask a running
server which policy revision it loaded. Until there is, restart before trusting.

### The turn watchdogs are shorter than real CI

```bash
HARNESS_TURN_TIMEOUT_S=2700           # default 600  — idle watchdog
HARNESS_TURN_ABSOLUTE_TIMEOUT_S=14400 # default 3600 — absolute cap
```

Defined in `omnigent/runtime/harnesses/_scaffold.py:123` and `:130`. An agent
legitimately waiting on CI emits no events, so at 600s the run dies with
`turn exceeded the 600s harness idle watchdog (run_turn emitted no events for
600s; likely a wedged LLM or tool call)` — blaming the agent for waiting.

A 15-shard Cypress matrix outlasts both defaults. Any project whose CI runs
longer than ten quiet minutes needs these raised before launch, not after the
first kill.

### `omnigent run` needs a real pty

It is an interactive REPL. Launched with stdin from `/dev/null` it prints
`Warning: Input is not a terminal (fd=0)` and exits immediately. Run it under
`tmux`/`screen`, not bare `nohup`.

## Failure modes, and what handles them

| Failure | Handling |
|---|---|
| Server or host crash / OOM | `Restart=always`, `StartLimitIntervalSec=0`; state is in Postgres + `.omni/` files |
| Reboot | `loginctl enable-linger` + `WantedBy=default.target` |
| Both vendors rate-limited | program sets a timer (30 min, doubling, 4 h cap) and resumes itself |
| One vendor limited | wave moves to the other vendor; noted in `STATUS.md` |
| Worker thrashing | **Nothing automatic.** `detect_thrashing` is declared per-seat (`stuck_detector`, 9 seats) and per-seat policies never load (#118, proven). The lead noticing and re-dispatching is the whole control |
| Task cannot converge | parked to `.omni/decisions.md` after 3 review loops; wave continues |
| Agent tries something catastrophic | The **root** `blast_radius` DENY set — and that is the whole of the policy layer. `read_only_os` on reviewers and on `security`, `cd` blocked on crew leads (`stay_in_workspace`), and all 13 per-seat `blast_radius` blocks are **NOT ENFORCED**: they are declared per-seat and per-seat policies never reach the engine (#118, proven by live probe — `docs/audit/118-per-seat-policy-probe.md`). Root `blast_radius` does apply to every seat, so it is a uniform **floor**, not a per-seat ceiling: no seat is tighter than root, and on the running config that floor carries `gate_pushes: false` (#115, ADR-0011). Still real: path ownership (`docs/owners.md` + leases), branch protection, and the Codex sandbox (`sandbox_mode="workspace-write"`, which is not a policy) |
| Disk filling | hourly `omnigent-disk-guard` prunes worktrees, docker, npm cache at 85% |
| Runaway spend | hard caps degrade the model instead of stopping; the program routes cheaper models per task class from `.omni/models.json` |
| Machine or disk loss | `nightshift-state-backup.timer` snapshots `.omni/` + `docs/` to the `omni/state` branch every 30 min and pushes it ONLY to a remote named `nightshift-state`, which must be a **private** repository (a GitHub remote that is not `private` is refused; no remote means the snapshot stays on this disk). Never point it at a public project repo: the snapshot carries the control-plane dump and incident notes — see "Stop, restart, move" below |
| Heartbeat task silently lost (restart, wedged scheduler) | `nightshift-watchdog.timer` checks `.omni/` freshness every 30 min; after 3 silent hours it comments on the Program status issue (phone push) with the recovery one-liner. Alert-only — it never restarts services or kills in-flight turns |
| Scheduled heartbeat fires but its agent turn fails/misses/sticks | `nightshift-scheduled-run-detector.timer` reads Omnigent's durable run ledger every 5 min and comments directly on the status issue without an LLM. Same-host only; off-box dead-man and canary are still required (#132) |

## Kill switch

```bash
systemctl --user stop omnigent-host omnigent-server   # stops all agent work
omnigent host stop                                    # belt and braces
```
Nothing is lost: transcripts are in Postgres, decisions in git, and `.omni/` +
`docs/` are on the `omni/state` branch of the private `nightshift-state` remote
(pushed every 30 min, once you have configured one).

## Stop, restart, move

### Where the state actually lives

| Layer | What | Survives |
|---|---|---|
| **GitHub** | branches, PRs, issues, board, merged code; `omni/state` (`.omni/` + `docs/`, 30-min snapshots) on the separate private `nightshift-state` remote | anything — this is the recovery floor |
| **Host disk** (persistent VPS/local volume — EBS on AWS, NVMe on a Hostinger KVM) | Postgres (`pgdata`: conversation history, scheduled tasks, timers), `/srv/project` (checkout, worktrees, `.omni/` live copies), credential homes (`~/.claude`, `~/.codex`, gh) | stop/start, reboot, crash — not machine deletion without a provider snapshot |
| **RAM only** | the turn a worker is executing *right now* | nothing — and that is fine by design |

An interrupted turn is the one thing never recovered, and the org already
tolerates it: the hourly heartbeat reconciles registries against open PRs,
finds the task marked `building` with no worker behind it, and re-dispatches
the SAME `(agent, title)` — the conversation context is in Postgres and the
worktree with its partial work is on disk, so the worker resumes where it was
rather than starting over.

### Tier 1 — process crash / OOM
Nothing to do. `Restart=always` + `RestartSec=10` + `StartLimitIntervalSec=0`
brings both services back; Postgres has `restart: unless-stopped`.

### Tier 2 — reboot, or provider stop → start
Nothing to do. Linger + `WantedBy=default.target` starts the services at boot
without a login; the disk persists across stop/start, so Postgres, the repo, and
the credentials all come back. Tailscale reconnects (and Serve config persists),
so a changed public IP is irrelevant. The heartbeat re-dispatches whatever was
in flight.

### Tier 3 — machine dead, disk (or snapshot) alive
Provider-dependent — on AWS you can reattach the EBS volume or restore its
snapshot (see `providers/aws/README.md`); on providers with VPS snapshots,
restore the snapshot. Then run `bootstrap.sh` (idempotent), `preflight.sh`,
start the services. Everything resumes as in tier 2. If your provider offers
neither, tier 4 is your tier 3 — which is fine, that path is cheap.

### Tier 4 — machine AND disk gone (works on ANY provider)
This is what the `omni/state` branch exists for. On a fresh Ubuntu VPS:

```bash
sudo bash deploy/provision-ubuntu.sh       # after getting the bundle onto the box
sudo tailscale up --ssh                    # UI stays on loopback ("Control API")
sudo -iu nightshift
/srv/nightshift/deploy/bootstrap.sh
claude setup-token && codex login && gh auth login && gh auth refresh -s project

git clone <repo> /srv/project
cd /srv/project
git remote add nightshift-state <url-of-the-PRIVATE-state-repo>
git fetch nightshift-state omni/state
git restore --source=FETCH_HEAD -- .omni docs
/srv/nightshift/deploy/preflight.sh /srv/project
systemctl --user start omnigent-server omnigent-host
```

Then start a session on `program`: *"Resume from files: read `.omni/goal.md`,
`.omni/STATUS.md` and the registries, reconcile them against open PRs and the
board, and continue the current wave. Re-create the hourly heartbeat task and
confirm it with `sys_scheduled_task_list`."* Conversation histories are gone
(that was Postgres), which the constitution already treats as acceptable:
transcripts are the backup, files are the record. Cost: at most 30 minutes of
`.omni/` state plus the in-flight turns.

### One crew per box? Don't.

Omnigent's server/runner split does allow **multiple hosts against one
server** (`omnigent host --server http://<server>:6767` from any machine), so
scattering boxes is *possible*. It still buys nothing here, three reasons:

1. **The bottleneck is the subscriptions, not the CPU.** The global budget is
   4 concurrent workers because two subscriptions rate-limit there — a second
   box adds idle silicon to the same wall. One 16 GB VPS is sized for the
   whole org.
2. **There is no per-agent placement.** Nothing pins `crew_alpha` to box A and
   `crew_beta` to box B; hosts are a pool, not an address.
3. **The coordination fabric is one filesystem.** Leases, handoff files,
   `docs/owners.md`, worktrees — the entire "the file is the channel" design
   assumes every agent sees the same `/srv/project`. Split it and you need
   shared storage or constant push/pull, and the lease protocol races.

Multiple boxes make sense in exactly two shapes:
- **Split by PROJECT, not by crew**: each box runs the full org against its own
  repo, coordinated only through GitHub. That is just "run the bundle twice".
- **Disposable-compute posture**: move Postgres to a managed database
  (`OMNIGENT_DATABASE_URI` in the root-owned `/etc/nightshift/server.env` is the only
  change; its password goes in the `PGPASSFILE`, never in the URI, which is
  visible in `ps`) and keep `omni/state` pushing. The box then holds nothing but
  credentials, and tier 4 recovery shrinks to "re-auth and go".

## Before you walk away for a week

- [ ] `preflight.sh` exits clean
- [ ] branch protection has NO required human approvals (it would deadlock the
      integrator); required status checks are the right setting
- [ ] `.omni/brief.md` written and its TEMPLATE line deleted (preflight fails otherwise)
- [ ] hourly heartbeat task created AND confirmed present
      (`sys_scheduled_task_list`) — this is the whole autonomy mechanism; without
      it the run stops at the end of the current turn
- [ ] provider disk snapshot/backup enabled, if the provider offers one
      (EBS DLM on AWS, VPS snapshots on Hostinger) — tier 4 covers you either
      way, snapshots just make tier 3 cheaper
- [ ] `nightshift-state-backup.timer` active, a `nightshift-state` remote that is a
      PRIVATE repository, and `omni/state` visible on it
      (`git ls-remote nightshift-state omni/state`) — preflight checks all three
- [ ] public SSH locked down; the UI reachable over the SSH tunnel, or over
      Tailscale Serve only with the owner-only ACL in place ("Control API")
- [ ] `gh` token scoped to `repo` only, and no other repos reachable from it
- [ ] you know your Claude and Codex quota state (`claude` → `/usage`) — a run
      started at 90% consumed spends its first day asleep

## Provider notes

| Provider | File | What is genuinely specific |
|---|---|---|
| Hostinger (current primary) | `providers/hostinger/README.md` | hPanel firewall, initial root SSH, snapshot schedule |
| AWS EC2 | `providers/aws/README.md` | cloud-init user-data, security groups, IAM/instance role, EBS tiers, DLM |
| anything else | this file | nothing — provision, Tailscale, run |
