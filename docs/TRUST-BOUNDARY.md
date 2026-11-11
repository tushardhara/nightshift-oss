# Trust boundary — what is isolated, what is trusted, and why

One Ubuntu VPS (any provider — Hostinger, AWS, ...) runs orchestrators
(claude-sdk sessions), native workers (Claude Code and Codex CLIs), and every
arbitrary command a project's tests and builds pull in — all as one dedicated
non-root runtime user (default `nightshift`, no sudo). The credentials on that box are not equal: some must be
reachable for the org to function at all, others must never be reachable
from a `npm test` that executes project code. This file says exactly which
is which. It is a boundary statement, not a promise of perfection — the
residual risks are listed at the bottom, on purpose.

## The layers

```
┌─ systemd user units (host + server) ─ NoNewPrivileges, dedicated non-sudo user
│                                        (docker-group caveat: residual risk 1)
│
│  ┌─ orchestrator terminals (program, crew leads — sys_os_shell) ──────────┐
│  │  Omnigent sandbox, type auto → bwrap (Linux) / seatbelt (macOS)       │
│  │  READ+WRITE isolation: $HOME never mounted; no credential path is     │
│  │  granted; only cwd is writable, toolchains read-only, caches in /tmp  │
│  └───────────────────────────────────────────────────────────────────────┘
│
│  ┌─ Codex workers (engineers, qa, reviewers, architect, release) ────────┐
│  │  Codex's OWN sandbox: approval_policy=never + workspace-write         │
│  │  WRITE isolation: writes confined to worktree/cwd, tmp, declared      │
│  │  caches. Reads are NOT confined (see residual risks).                 │
│  └───────────────────────────────────────────────────────────────────────┘
│
│  ┌─ Claude workers (claude-native, permission_mode: auto) ───────────────┐
│  │  TRUSTED. Claude Code's Bash runs outside any omnigent sandbox —      │
│  │  the CLI needs its own credential home to exist at all.               │
│  └───────────────────────────────────────────────────────────────────────┘
└─ credential scoping (the layer that works on everything above)
```

## Credential inventory and its scoping rules

| Credential | Needed by | Scoping rule |
|---|---|---|
| Claude subscription (`~/.claude`) | Claude CLIs | Never granted into orchestrator-terminal sandboxes; masked there. Readable by Claude workers (unavoidable — it is their auth). |
| Codex subscription (`~/.codex`) | Codex CLIs | Same shape: masked in terminals, readable by Codex workers, write-protected by workspace-write. |
| GitHub (`~/.config/gh`) | everyone opening PRs / tracking issues | Use a **fine-grained PAT scoped to the ONE project repo** — then the worst any worker can do is damage the repo the org already owns, and branch protection on `main` bounds that. Never a classic PAT, never org-wide. On a headless box the token sits in `~/.config/gh/hosts.yml` in plaintext, so it is **never granted into the terminal sandbox**, read or write: `gh` and authenticated pushes run in the seat's own shell. If a seat ever genuinely needs `gh` inside the sandbox, broker it rather than mount the token: omnigent's `credential_proxy` with `egress_rules` gives the sandbox a placeholder and swaps in the real token only on egress to `api.github.com`. |
| Cloud provider (AWS/Hostinger/...) | **nothing in Nightshift** | **No static cloud credentials on the box at all.** State backup rides git (`omni/state` branch), so no cloud API access is needed. If the *project* needs cloud access on AWS, use an instance role scoped to exactly that; elsewhere, scope a key to the minimum and know workers can read it. `~/.aws/credentials` existing on the box is a finding (preflight warns). |
| SSH keys | nothing | The box clones over HTTPS with the PAT. `~/.ssh` should not contain private keys; if it does, the terminal sandbox masks it, Codex sandbox write-protects it, Claude workers can read it — so do not put keys on the box. |

## What each layer actually stops

- **Terminal sandbox (program, leads)** — the widest attack surface is an
  orchestrator running project commands (`npm test` executes arbitrary
  project code). Inside bwrap, `$HOME` is simply not mounted and no
  credential path is granted, so that code cannot read any credential
  above. The only writable host path is cwd. Toolchains are mounted
  read-only (`~/.cargo/bin`, `~/.rustup`; Go and Node live under `/usr`),
  and `CARGO_HOME`, `GOPATH`, `GOMODCACHE`, `GOCACHE`, `XDG_CACHE_HOME` and
  the npm cache point into the sandbox's private `/tmp`, a fresh tmpfs
  that dies with it. Until 2026-11 the sandbox wrote straight into
  `~/.config/gh`, `~/.cargo`, `~/go` and `~/.cache`: project code could
  read the PAT, replace `~/.cargo/bin/cargo` (linked from `~/.local/bin`),
  or poison the Go caches, and the next unsandboxed worker ran the result.
  Dotfiles not on the allowlist do not exist. Network stays on for package
  fetches; `gh` and `git push` belong to the seat's own shell.
- **Codex workspace-write** — a malicious or confused build script running
  under a Codex worker cannot overwrite `~/.codex`, `~/.config/gh`, system
  paths, or another crew's workspace; it CAN still read them. Approval policy
  `never` keeps it unattended: a blocked write fails visibly instead of
  parking the turn on a question nobody will answer.
- **systemd `NoNewPrivileges`** — nothing in the tree can escalate via
  setuid; the service user has no sudo. One named exception punches straight
  through this layer: docker-group membership (residual risk 1 — it is
  root-equivalent, and neither NoNewPrivileges nor "no sudo" bounds it). Deeper unit-level sandboxing
  (`ProtectSystem=strict` etc.) is deliberately NOT applied: these are user
  units, and user-manager namespace options fail on Ubuntu 24.04's
  unprivileged-userns restrictions in ways that brick the service at boot —
  the wrong trade for an unattended box. bwrap carries the mount-namespace
  duty instead — **but only where the platform actually lets it run, which
  stock Ubuntu 24.04 does not.** Until 2026-08-22 this file asserted that
  bubblewrap ships its own AppArmor profile on that release and therefore
  keeps working. It does not: `bubblewrap 0.9.0-1ubuntu0.1` ships no profile
  at all, and with `kernel.apparmor_restrict_unprivileged_userns=1` (the
  24.04 default) `bwrap --ro-bind / / /bin/true` exits non-zero, so bwrap
  creates no namespace and this layer confines **nothing** until an operator
  installs a profile as root (`.omni/blocked.md`). That retracted claim was
  load-bearing — it was the stated reason bwrap was preferred over systemd
  namespace options — so on any box where the probe fails, treat the
  mount-namespace duty as **UNMET** and the orchestrator-terminal isolation
  above as **not in force**. `deploy/preflight.sh` now FAILs on exactly that
  condition instead of reporting the binary present (issue #12,
  DoD condition 5).
- **Repo-scoped PAT + branch protection** — bounds the blast radius of every
  layer above to: contents of one repository, on non-protected refs.
- **Root-owned fence configuration** — the files that decide what the fences
  allow are owned by root and only readable by the runtime user, because
  every worker runs as that user and anything it can write, a worker can
  rewrite for the next restart (`Restart=always`) to load:
  `/etc/nightshift/server.env` (policy path, merge-fence scope; written by
  `deploy/install-root-config.sh`, never by `bootstrap.sh`), the bundle at
  `/srv/nightshift` or a release root-sealed by `nightshift-release seal`,
  and a read-only copy of the authority/tracker registries under
  `/var/lib/nightshift/authority-root` that the fences read instead of the
  runtime-owned originals. `preflight.sh` FAILs while any of them, or a
  directory above them, is writable by the runtime user, and the exact-SHA
  server wrapper refuses an environment file the release owner does not own.
  This stops a worker from changing what a *legitimate* restart loads. It
  does not stop a worker that is already running hostile code as the
  runtime user: it can kill the server, rewrite its own systemd **user**
  units and drop-ins (the user manager belongs to that uid; preflight FAILs
  on a unit pointing elsewhere or carrying a drop-in, but only when run), or
  reach root through the Docker socket (residual risk 1). Closing that needs
  the server under its own uid or as a system unit.

## What remains trusted, explicitly

1. **Claude-native workers.** Their Bash tool executes with the service
   user's full environment. Omnigent's sandbox does not wrap native harness
   internals, and guessing Claude Code's own sandbox config into a headless
   box was judged riskier than naming the trust. Mitigations that do apply:
   blast_radius policy (denies force-push, `rm -rf /`, hard reset), no
   static cloud creds to steal, repo-scoped PAT, no sudo.
2. **Codex workers' reads.** workspace-write confines writes, not reads.
3. **The omnigent server/host processes themselves**, and the Postgres they
   use for session state (loopback-only).
4. **Package installs.** Project-local installs (npm/pip/cargo into cwd, or
   into the per-sandbox caches in `/tmp`) work everywhere; inside the
   terminal sandbox they start cold every time, the price of not sharing a
   writable cache with the next worker. SYSTEM package installs (`apt`) are
   bootstrap's job, not a worker's — by doctrine, not by mechanism.
5. **The Docker socket.** The runtime user is in the `docker` group so
   bootstrap can start the Postgres container — and Docker's own docs are
   blunt that this is **root-equivalent**: any docker-group member can start
   a container that bind-mounts `/` read-write as root. This is stated here
   plainly because it means the "dedicated non-sudo user" line above is a
   containment story for *accidents*, not for a *hostile* Claude worker.
   Intended future direction: rootless Docker, or the Postgres container
   behind a root-managed systemd system unit so the runtime user never holds
   the socket.
6. **floci, and the socket it passes on.** The local cloud emulators
   (`floci`, `floci az|gcp|oci`) are Docker containers, and the floci container
   **bind-mounts the host's `/var/run/docker.sock`** so it can manage its own
   service containers. Two consequences, stated rather than glossed:
   - Any worker that can run `floci start` is exercising the docker-group
     privilege in item 5. It is not a new hole — a worker already had the
     socket — but it is a *convenient, documented, everyday* path to it, so
     "nobody would think to abuse the Docker socket" is not a defence.
     Whatever runs inside that container inherits root-equivalent reach over
     the host daemon.
   - The emulators answer **unauthenticated** AWS/Azure/GCP/OCI API calls.
     Anything that reaches the port can create, read and delete emulator
     state, so port exposure is the security property that matters. That is
     why the loopback bind is enforced at the daemon
     (`/etc/docker/daemon.json` → `{"ip": "127.0.0.1"}`) rather than left to a
     worker to remember: `floci start` publishes through Docker and offers no
     bind-address flag of its own. Preflight then checks both the control and
     the live listeners, because an explicit `-p 0.0.0.0:...` still overrides
     the daemon default.

   What floci genuinely *removes* from the trust surface: no real cloud
   credentials need ever exist on this box for cloud code to be built and
   tested. That is the trade — a documented socket path in exchange for no
   live cloud keys — and it is the right one for a box whose project code is
   assumed untrusted. It does not make the emulators a sandbox: they are a
   convenience inside the trust boundary, not a boundary of their own.
7. **Tailscale is root-managed, on purpose.** Serve is configured once by root
   (`--bg` persists it) and workers hold **no direct Tailscale operator
   permission**. That removes normal and accidental tailnet reconfiguration —
   in particular `tailscale funnel`, which would publish 6767 to the open
   internet, is not something a worker can reach by ordinary means, rather than
   being merely forbidden by documentation. It is **not** an absolute
   guarantee, and this file will not claim one: the accepted docker-socket
   root-equivalence in item 5 is an escape from the host boundary, and anything
   that becomes root can reconfigure tailscaled regardless of operator
   settings. Removing the operator grant raises the bar and shrinks the
   accident surface; only fixing item 5 would close the route.

## Residual risks (ranked)

1. **Docker-group membership is root-equivalent.** A compromised dependency
   under a Claude worker (unsandboxed, runs as the runtime user) can use the
   Docker socket to become root on the box — bypassing NoNewPrivileges, file
   permissions, and every mask above. Accepted for now: the box is
   single-purpose and this file already assumes project code is untrusted;
   the blast radius stays what the box can reach (one repo-scoped PAT,
   revocable subscription tokens, tailnet access as this node). Do not reuse
   this machine for anything you would not hand to project code. Future
   direction: rootless Docker or a root-managed Postgres unit.
   **floci widens the path, not the hole:** its container mounts the host
   Docker socket by design, so the root-equivalent route is now something a
   worker walks down routinely and legitimately. Any future fix must cover
   floci as well as Postgres — under rootless Docker the emulators would need
   the rootless socket, and a root-managed-Postgres-only fix would leave this
   item exactly as it is.
2. A compromised dependency executed by a **Claude worker** can read the gh
   PAT and both subscription tokens → bounded by repo-scoped PAT + branch
   protection + vendor-side session revocation; not bounded on quota burn.
3. Same code under a **Codex worker** can read (not write) the same set.
4. Exfiltration over the network is possible from any worker — network is
   deliberately open for the job. If a project is untrusted enough that this
   matters, do not run it on a box whose credentials you care about.
5. The policy evaluator (omnigent server) runs as the same uid as the
   workers it fences. Root-owned configuration keeps a restart honest; it
   cannot keep a hostile worker from replacing the running server, or from
   editing its own user units between two preflight runs.
6. The terminal-sandbox dotfile allowlist is curated; an exotic project
   dotfile not on it will be invisible to lead-run gates (visible failure,
   add it to the list) — the failure mode is a broken build, never a leak.

## The fallback, named

If a project's build genuinely cannot run inside Codex's workspace-write
sandbox (rare: system daemons, docker-in-build), flip that one seat back to
`yolo: true` in its config — restoring the previous fully-bypassed behaviour
for that seat only — and record it in `.omni/decisions.md`. That is a
scoped, recorded regression, not a silent default.
