#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 VPS for Nightshift — any provider (Hostinger,
# AWS, Hetzner, E2E, DigitalOcean, bare metal). This replaces what EC2
# cloud-init used to do, so the same bundle runs on any normal Ubuntu box.
#
# Run ONCE as root (re-running is safe — everything is idempotent):
#   sudo /srv/nightshift/deploy/provision-ubuntu.sh
#   # or, before the bundle exists on the box:
#   sudo bash deploy/provision-ubuntu.sh
#
# What it does:
#   - installs the OS-level prerequisites (python3.12, node 22, docker+compose,
#     tmux, bubblewrap, gh, tailscale, postgres client, fail2ban, ...)
#   - installs the language toolchains workers build with: go (upstream
#     tarball), rust (rustup), and floci (local AWS/Azure/GCP/OCI emulators,
#     so cloud code is testable with no real cloud credentials on the box)
#   - creates the dedicated non-root runtime user (default: nightshift) with
#     docker group membership, linger, NO sudo
#   - creates /srv/nightshift and /srv/project owned by that user
#   - journald size limits + the hourly disk guard
#
# What it deliberately does NOT do:
#   - credentials (claude setup-token / codex login / gh auth login are
#     interactive, run later as the runtime user — see deploy/README.md)
#   - tailscale up (interactive browser auth)
#   - firewall lockdown (do it AFTER Tailscale SSH works, never before —
#     see deploy/README.md "Security posture")
#
# Env overrides:
#   NIGHTSHIFT_USER=<name>   runtime user (default: adopt the existing owner
#                            of /srv/nightshift if any, else `nightshift`)
#   NIGHTSHIFT_SWAP_GB=<n>   swapfile size to create if the box has no swap
#                            (default 4; 0 disables)
#   NIGHTSHIFT_GO_VERSION=<x.y.z> NIGHTSHIFT_GO_SHA256=<hex>
#                            install a Go other than the pinned one; the
#                            sha256 you verified is REQUIRED with it
#   NIGHTSHIFT_SKIP_TOOLCHAINS=1
#                            skip go/rust/floci — a box that only runs the
#                            control plane does not need them
#   NIGHTSHIFT_TAILSCALE_SERVE=1
#                            publish the UI/control API (127.0.0.1:6767) to
#                            the tailnet. OFF by default: the API has no
#                            authentication — read deploy/README.md first
#
# Every download is pinned to a version and checked against a sha256 recorded
# in deploy/lib/installer-pins.sh; apt sources use vendor keys whose sha256 is
# pinned there too. Nothing is piped from curl into a shell.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deploy-lib.sh
. "$SCRIPT_DIR/lib/deploy-lib.sh"
# shellcheck source=lib/installer-pins.sh
. "$SCRIPT_DIR/lib/installer-pins.sh"

say() { printf '\n=== %s\n' "$1"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)" >&2; exit 1; }

# Ubuntu check: hard on non-Ubuntu (apt/package names assume it), soft on
# other Ubuntu releases (22.04/26.04 mostly work; 24.04 is what we test).
. /etc/os-release
[ "${ID:-}" = ubuntu ] || { echo "this script targets Ubuntu (got: ${ID:-unknown})" >&2; exit 1; }
[ "${VERSION_ID:-}" = "24.04" ] || echo "WARN: tested on Ubuntu 24.04, this is ${VERSION_ID:-unknown} — continuing"

export DEBIAN_FRONTEND=noninteractive
ARCH=$(dpkg --print-architecture)

# ------------------------------------------------------------ runtime user --
# Adopt an existing non-root owner of /srv/nightshift (upgrade path for boxes
# provisioned under EC2's `ubuntu` user), otherwise create `nightshift`.
existing_owner=""
[ -d /srv/nightshift ] && existing_owner=$(stat -c %U /srv/nightshift 2>/dev/null || true)
NS_USER=$(nightshift_resolve_user "${NIGHTSHIFT_USER:-}" "$existing_owner")

say "runtime user: $NS_USER"
if ! id -u "$NS_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$NS_USER"
  echo "  created $NS_USER (no sudo — by design, see docs/TRUST-BOUNDARY.md)"
else
  echo "  $NS_USER exists"
fi
NS_HOME=$(getent passwd "$NS_USER" | cut -d: -f6)

# --------------------------------------------------------------- packages ---
say "apt packages"
apt-get update -y
apt-get install -y \
  python3.12 python3.12-venv \
  tmux \
  bubblewrap \
  git curl ca-certificates gnupg openssl \
  jq ripgrep \
  build-essential pkg-config \
  postgresql-client \
  unattended-upgrades fail2ban

# bubblewrap is MANDATORY on Linux: native harness terminals wrap each agent
# in bwrap. A bwrap that cannot RUN fails the launch exactly as hard as a
# missing one, so this is a functional probe, not a presence check (DoD
# condition 5, issue #12).
#
# Probed AS $NS_USER on purpose: this script runs as root, and root can create
# user namespaces even where kernel.apparmor_restrict_unprivileged_userns
# refuses them to everyone else. A root-run probe would report success on a
# box where every worker still dies at its first tool call.
bwrap_code=0
nightshift_bwrap_probe "${NIGHTSHIFT_BWRAP:-bwrap}" "$NS_USER" || bwrap_code=$?
if [ "$bwrap_code" -ne 0 ]; then
  nightshift_bwrap_diagnosis "$bwrap_code" "$NS_USER" >&2
  exit 1
fi
echo "  $(nightshift_bwrap_diagnosis 0 "$NS_USER")"

# Security auto-updates on an unattended multi-day box:
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now fail2ban >/dev/null 2>&1 || true

# ----------------------------------------------------------------- docker ---
say "docker + compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  nightshift_fetch_verified https://download.docker.com/linux/ubuntu/gpg \
    "$NS_PIN_KEY_DOCKER_SHA256" /etc/apt/keyrings/docker.asc || exit 1
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
else
  echo "  docker present: $(docker --version)"
  docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# Published container ports default to 127.0.0.1, not 0.0.0.0.
#
# This is the DETERMINISTIC half of the emulator exposure rule. `floci start`
# publishes its port through Docker and exposes no bind-address flag, so
# "remember to bind localhost" is not something a worker can be trusted to do
# and a preflight check only notices AFTER the port is already public. Docker's
# daemon-level default host IP moves the guarantee before the mistake.
#
# Limit, stated honestly: this sets the DEFAULT for publishes that name no host
# IP. An explicit `-p 0.0.0.0:X:Y` still wins — preflight keeps checking for
# that. Postgres already pins 127.0.0.1 in its compose file, so nothing here
# changes it.
docker_daemon_json=/etc/docker/daemon.json
docker_bind_changed=0
docker_existing=""
[ -f "$docker_daemon_json" ] && docker_existing=$(cat "$docker_daemon_json")
if nightshift_docker_bind_ok "$docker_existing"; then
  echo "  docker default publish IP already 127.0.0.1"
elif merged=$(nightshift_docker_bind_json "$docker_existing"); then
  # Merge, never overwrite: the file may carry log or registry settings.
  install -d /etc/docker
  printf '%s\n' "$merged" > "$docker_daemon_json"
  docker_bind_changed=1
else
  echo "  WARN: $docker_daemon_json is not valid JSON — leaving it alone." >&2
  echo "        Add '\"ip\": \"127.0.0.1\"' by hand or emulator ports may bind publicly." >&2
fi
if [ "$docker_bind_changed" = 1 ]; then
  echo "  docker default publish IP -> 127.0.0.1"
  # Restart only when the file actually changed. Containers with
  # restart:unless-stopped (Postgres) come back on their own; already-running
  # containers keep their existing port bindings until they are recreated.
  systemctl restart docker >/dev/null 2>&1 || true
fi
# Docker-group membership is root-EQUIVALENT (the socket can start a container
# that mounts / as root) — NoNewPrivileges and "no sudo" do not bound it. This
# is an accepted, documented residual risk; see docs/TRUST-BOUNDARY.md for the
# statement and the intended future direction (rootless Docker or a
# root-managed Postgres unit).
usermod -aG docker "$NS_USER"

# ---------------------------------------------------------------- node 22 ---
say "node 22 (Claude Code and Codex CLIs are npm packages)"
node_major=$(node --version 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || true)
if [ "${node_major:-0}" -lt 22 ]; then
  # NodeSource's signed apt repo, not its setup script piped into root bash.
  # Same keyring and list paths the setup script uses, so a box that once ran
  # it ends up with one consistent Signed-By rather than two conflicting ones.
  ns_key=$(mktemp)
  nightshift_fetch_verified https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    "$NS_PIN_KEY_NODESOURCE_SHA256" "$ns_key" || exit 1
  gpg --dearmor --yes -o /usr/share/keyrings/nodesource.gpg "$ns_key"
  rm -f "$ns_key"
  chmod a+r /usr/share/keyrings/nodesource.gpg
  echo "deb [arch=$ARCH signed-by=/usr/share/keyrings/nodesource.gpg] \
https://deb.nodesource.com/node_22.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
  apt-get update -y
  apt-get install -y nodejs
else
  echo "  node present: $(node --version)"
fi

# ------------------------------------------------------------- github cli ---
say "github cli"
if ! command -v gh >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  nightshift_fetch_verified https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    "$NS_PIN_KEY_GH_SHA256" /etc/apt/keyrings/githubcli.gpg || exit 1
  chmod a+r /etc/apt/keyrings/githubcli.gpg
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/githubcli.gpg] \
https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -y && apt-get install -y gh
else
  echo "  gh present: $(gh --version | head -1)"
fi

# --------------------------------------------------------------- tailscale --
# Installed here; joined later ('tailscale up --ssh' is interactive). The only
# way in after lockdown — the UI and SSH both ride the tailnet.
say "tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  # Tailscale's signed apt repo (the paths its install.sh writes), not the
  # script itself.
  nightshift_fetch_verified "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" \
    "$NS_PIN_KEY_TAILSCALE_SHA256" /usr/share/keyrings/tailscale-archive-keyring.gpg || exit 1
  chmod a+r /usr/share/keyrings/tailscale-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] \
https://pkgs.tailscale.com/stable/ubuntu ${VERSION_CODENAME} main" \
    > /etc/apt/sources.list.d/tailscale.list
  apt-get update -y && apt-get install -y tailscale
else
  echo "  tailscale present: $(tailscale version | head -1)"
fi
systemctl enable --now tailscaled >/dev/null 2>&1 || true

# --------------------------------------------------------------------- uv ---
say "uv (installs omnigent from wheels, as $NS_USER)"
# The pinned release tarball, verified as root, then installed owned by
# $NS_USER into the same ~/.local/bin the upstream installer used. A uv at any
# other version (including one that self-updated) is replaced with the pin.
uv_have=$("$NS_HOME/.local/bin/uv" --version 2>/dev/null | awk '{print $2}' || true)
if [ "$uv_have" != "$NS_PIN_UV_VERSION" ]; then
  uv_triple=$(nightshift_rust_triple "$ARCH")
  uv_tmp=$(mktemp -d)
  nightshift_fetch_verified \
    "https://github.com/astral-sh/uv/releases/download/${NS_PIN_UV_VERSION}/uv-${uv_triple}.tar.gz" \
    "$(nightshift_pin_sha256 UV "$ARCH")" "$uv_tmp/uv.tgz" || { rm -rf "$uv_tmp"; exit 1; }
  tar -C "$uv_tmp" -xzf "$uv_tmp/uv.tgz"
  install -d -o "$NS_USER" -g "$NS_USER" "$NS_HOME/.local" "$NS_HOME/.local/bin"
  install -o "$NS_USER" -g "$NS_USER" -m 0755 \
    "$uv_tmp/uv-${uv_triple}/uv" "$uv_tmp/uv-${uv_triple}/uvx" "$NS_HOME/.local/bin/"
  rm -rf "$uv_tmp"
  echo "  installed uv ${NS_PIN_UV_VERSION}, sha256 verified"
else
  echo "  uv ${uv_have} present (pinned)"
fi

# --------------------------------------------------------- toolchains: go ---
# Workers must be able to build and test what they write without asking a
# human, so the languages live on the box rather than in a per-task install.
# Ubuntu's golang-go trails upstream by releases, so take the official tarball.
#
# PATH matters more than usual here: workers are launched by systemd USER
# units, and a unit's PATH is set in the unit file — /etc/profile.d is never
# read. So each toolchain is linked into a directory already on that PATH
# (/usr/local/bin for root-owned, $NS_HOME/.local/bin for user-owned) instead
# of relying on a login shell. profile.d is written too, but only for humans.
if [ "${NIGHTSHIFT_SKIP_TOOLCHAINS:-0}" = 1 ]; then
  say "toolchains: skipped (NIGHTSHIFT_SKIP_TOOLCHAINS=1)"
else

say "go toolchain"
# Version AND sha256 come from deploy/lib/installer-pins.sh. Nothing is asked
# of go.dev but the tarball itself: "latest" moved under every re-provision,
# and a checksum served by the same origin as the file it checks proves
# nothing against that origin. An override must bring the hash it verified.
go_want="${NIGHTSHIFT_GO_VERSION:-$NS_PIN_GO_VERSION}"
go_sha="$(nightshift_pin_sha256 GO "$ARCH")"
if [ "$go_want" != "$NS_PIN_GO_VERSION" ]; then
  go_sha="${NIGHTSHIFT_GO_SHA256:-}"
  if [ -z "$go_sha" ]; then
    echo "NIGHTSHIFT_GO_VERSION=${go_want} is not the pinned ${NS_PIN_GO_VERSION}: set NIGHTSHIFT_GO_SHA256 to the sha256 of go${go_want}.linux-${ARCH}.tar.gz that you verified — refusing to install an unverified toolchain" >&2
    exit 1
  fi
fi

# Bug 2: a missing /usr/local/go/bin/go is a NORMAL state on a fresh box, not a
# reason to abort. The old substitution returned non-zero and `set -e` exited
# before this comparison, so the `${go_have:-0}` fallback was never reached.
go_have=""
if ! go_have="$(nightshift_go_installed_version /usr/local/go/bin/go)"; then
  echo "  /usr/local/go/bin/go exists but reports an unparseable version — treating the install as invalid and reinstalling" >&2
  go_have=""
fi
if [ -n "$go_have" ] && nightshift_version_ge "$go_have" "$go_want"; then
  echo "  go present: ${go_have} (>= ${go_want})"
else
  # Go's tarballs use dpkg's own arch names (amd64, arm64) — no mapping needed.
  # The tarball becomes /usr/local/go and every worker's compiler, so any
  # mismatch is a HARD stop before /usr/local/go is touched.
  go_file="go${go_want}.linux-${ARCH}.tar.gz"
  go_tmp=$(mktemp -d)
  nightshift_fetch_verified "https://go.dev/dl/${go_file}" "$go_sha" "$go_tmp/go.tgz" || {
    rm -rf "$go_tmp"
    echo "refusing to install an unverified toolchain (${go_file})" >&2
    exit 1
  }
  rm -rf /usr/local/go          # Go does not support extracting over itself
  tar -C /usr/local -xzf "$go_tmp/go.tgz"
  rm -rf "$go_tmp"
  echo "  installed go ${go_want} (${ARCH}) to /usr/local/go, sha256 verified"
fi
ln -sfn /usr/local/go/bin/go    /usr/local/bin/go
ln -sfn /usr/local/go/bin/gofmt /usr/local/bin/gofmt
# GOPATH stays the default ($HOME/go) and GOCACHE the default ($HOME/.cache):
# both are already Codex sandbox writable_roots, so no policy change is needed.

# ------------------------------------------------------- toolchains: rust ---
# rustup rather than apt, so a worker can add a toolchain or target itself.
# Installed as $NS_USER — cargo state belongs to the runtime user, not root.
# --no-modify-path because we own the PATH story above; the default profile
# (not minimal) so rustfmt and clippy exist — an agent that cannot fmt or lint
# opens noisy PRs.
say "rust toolchain (rustup, as $NS_USER)"
# rustup-init is the pinned, sha256-verified binary (not sh.rustup.rs piped
# into a shell), and the default toolchain is pinned too.
if [ ! -x "$NS_HOME/.cargo/bin/cargo" ]; then
  ru_triple=$(nightshift_rust_triple "$ARCH")
  ru_tmp=$(mktemp -d)
  nightshift_fetch_verified \
    "https://static.rust-lang.org/rustup/archive/${NS_PIN_RUSTUP_VERSION}/${ru_triple}/rustup-init" \
    "$(nightshift_pin_sha256 RUSTUP "$ARCH")" "$ru_tmp/rustup-init" || { rm -rf "$ru_tmp"; exit 1; }
  chmod 0755 "$ru_tmp/rustup-init"
  chmod 0711 "$ru_tmp"          # $NS_USER executes it; mktemp -d is 0700 root
  runuser -u "$NS_USER" -- env HOME="$NS_HOME" "$ru_tmp/rustup-init" \
    -y --no-modify-path --profile default --default-toolchain "$NS_PIN_RUST_TOOLCHAIN"
  rm -rf "$ru_tmp"
else
  echo "  rust present: $("$NS_HOME/.cargo/bin/rustc" --version 2>/dev/null || echo unknown)"
fi
install -d -o "$NS_USER" -g "$NS_USER" "$NS_HOME/.local/bin"
for b in cargo rustc rustup rustfmt cargo-clippy clippy-driver; do
  if [ -e "$NS_HOME/.cargo/bin/$b" ]; then
    ln -sfn "$NS_HOME/.cargo/bin/$b" "$NS_HOME/.local/bin/$b"
    chown -h "$NS_USER:$NS_USER" "$NS_HOME/.local/bin/$b"
  fi
done

# ------------------------------------------------------ toolchains: floci ---
# Local cloud emulators (https://floci.io). ONE binary with product groups —
# `floci` / `floci aws`, `floci az`, `floci gcp`, `floci oci` — not four
# commands. It drives Docker containers, so the daemon-level 127.0.0.1 publish
# default configured above is what actually keeps its ports private; floci
# itself exposes no bind-address flag.
#
# This is a security control as much as a convenience: workers build and test
# cloud code with NO real cloud credentials on the box — exactly what
# preflight's cloud-credential check exists to keep true.
#
# Installed as ROOT into /usr/local/bin (already on the systemd unit PATH): the
# pinned release binary, checked against the sha256 in installer-pins.sh. Its
# upstream installer is NOT run: it resolved "latest" and verified against a
# checksum file served by the same release.
say "floci (local cloud emulators)"
if command -v floci >/dev/null 2>&1; then
  echo "  floci present: $(floci version 2>/dev/null | head -1 || echo installed)"
else
  # Non-fatal: losing the emulators degrades a worker's cloud testing, it does
  # not stop the box from running the Program. A mismatch installs nothing.
  fl_tmp=$(mktemp -d)
  if nightshift_fetch_verified \
       "https://github.com/floci-io/floci-cli/releases/download/${NS_PIN_FLOCI_VERSION}/floci-linux-${ARCH}" \
       "$(nightshift_pin_sha256 FLOCI "$ARCH")" "$fl_tmp/floci"; then
    install -m 0755 "$fl_tmp/floci" /usr/local/bin/floci
    echo "  installed floci ${NS_PIN_FLOCI_VERSION}, sha256 verified"
  else
    echo "  WARN: floci not installed — workers lose the local cloud emulators." >&2
  fi
  rm -rf "$fl_tmp"
fi
# floci keeps profiles in ~/.floci; make sure it belongs to the runtime user
# even if a root-run command created it first.
install -d -o "$NS_USER" -g "$NS_USER" "$NS_HOME/.floci"

# Humans who SSH in get the same toolchains a worker sees (systemd units set
# their own PATH and never read this file):
cat > /etc/profile.d/nightshift-toolchains.sh <<'EOF'
# Written by nightshift provisioning. Interactive/login shells only.
case ":$PATH:" in
  *:/usr/local/go/bin:*) ;;
  *) PATH="$PATH:/usr/local/go/bin" ;;
esac
case ":$PATH:" in
  *:"$HOME/.cargo/bin":*) ;;
  *) PATH="$HOME/.cargo/bin:$PATH" ;;
esac
export PATH
EOF
chmod 0644 /etc/profile.d/nightshift-toolchains.sh

fi  # NIGHTSHIFT_SKIP_TOOLCHAINS

# ------------------------------------------------------------- workspace ----
say "workspace: /srv/nightshift (root) /srv/project ($NS_USER)"
# The bundle holds the fence configuration (program/, deploy/policies), so
# root owns it and the runtime user, which every worker runs as, only reads
# it (audit C6). Releases live under a sticky base the runtime user can add
# to but not rename root's sealed releases out of. Never a blanket chown of /srv.
install -d -o root -g root -m 0755 /srv/nightshift /etc/nightshift
[ "$(stat -c %U /srv/nightshift)" = root ] || chown -R root:root /srv/nightshift
chmod -R go-w /srv/nightshift
install -d -o root -g "$NS_USER" -m 1775 /srv/nightshift-releases
install -d -o "$NS_USER" -g "$NS_USER" -m 0700 /srv/nightshift-release-state
install -d -o "$NS_USER" -g "$NS_USER" /srv/project
[ "$(stat -c %U /srv/project)" = "$NS_USER" ] || chown -R "$NS_USER:$NS_USER" /srv/project

# User systemd services must outlive SSH sessions and start at boot.
#
# Linger alone is NOT the capability (issue #9, ADR reconciliation A11). It
# permits a user manager to persist or start without an interactive login; it
# does not construct one, and it does not give a later `su - $NS_USER` shell the
# bus environment. So root does all three things here, while it still can:
#   1. enable linger
#   2. actually START the user manager
#   3. VERIFY, as $NS_USER, that `systemctl --user` answers
# Bootstrap then only has to probe. Doing it the other way round is what left
# this box with no watchdog timer and no state-backup timer.
loginctl enable-linger "$NS_USER"

NS_UID="$(id -u "$NS_USER")"
systemctl start "user@${NS_UID}.service" || true
# Give the manager a moment to publish its bus before the verification below;
# `systemctl start` returns once the unit is active, not once dbus is listening.
for _ in 1 2 3 4 5; do
  [ -S "/run/user/${NS_UID}/bus" ] && break
  sleep 1
done
if runuser -u "$NS_USER" -- env \
     XDG_RUNTIME_DIR="/run/user/${NS_UID}" \
     DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${NS_UID}/bus" \
     systemctl --user show-environment >/dev/null 2>&1; then
  echo "  systemctl --user reachable as $NS_USER (uid ${NS_UID})"
else
  echo "WARN: could not verify 'systemctl --user' as $NS_USER. bootstrap.sh will" >&2
  echo "      FAIL loudly rather than half-install units. Investigate with:" >&2
  echo "        systemctl status user@${NS_UID}.service" >&2
  echo "        loginctl show-user $NS_USER" >&2
fi

# ---------------------------------------------------------------- journald --
say "journald limits (multi-day runs must not eat the disk)"
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/omnigent.conf <<'EOF'
[Journal]
SystemMaxUse=2G
MaxRetentionSec=2week
EOF
systemctl restart systemd-journald || true

# --------------------------------------------------------------- disk guard --
say "hourly disk guard"
cat > /etc/cron.hourly/omnigent-disk-guard <<EOF
#!/bin/sh
# Agents create worktrees and node_modules for days; prune before it hurts.
USE=\$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "\$USE" -ge 85 ]; then
  logger -t omnigent-disk-guard "root fs at \${USE}% — pruning caches"
  su - $NS_USER -c 'git -C /srv/project worktree prune' 2>/dev/null || true
  docker system prune -f 2>/dev/null || true
  find $NS_HOME/.npm/_cacache -type f -mtime +3 -delete 2>/dev/null || true
fi
if [ "\$USE" -ge 93 ]; then
  logger -t omnigent-disk-guard "root fs at \${USE}% — CRITICAL"
fi
EOF
chmod 0755 /etc/cron.hourly/omnigent-disk-guard

# --------------------------------------------------------------------- swap --
# An OOM kill takes a worker mid-task; a little swap absorbs the spike.
SWAP_GB="${NIGHTSHIFT_SWAP_GB:-4}"
if [ "$SWAP_GB" -gt 0 ] && [ "$(awk '/^SwapTotal:/{print $2}' /proc/meminfo)" -eq 0 ]; then
  say "swapfile (${SWAP_GB}G)"
  fallocate -l "${SWAP_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --------------------------------------------------------- tailscale serve --
# 127.0.0.1:6767 is the UI AND Nightshift's control API, and in the default
# deployment it has NO authentication: whatever reaches it can create sessions
# and fire runs, past every tool-call fence. `tailscale serve` hands it to every
# tailnet device the ACL lets through, so publishing it is opt-in, and an ACL
# restricting it to the owner is part of opting in (deploy/README.md).
say "tailscale serve (UI + control API): opt-in"
serve_step="  2. UI: ssh -L 6767:127.0.0.1:6767 <you>@<this box>, open http://127.0.0.1:6767
     (tailnet-wide 'tailscale serve' is opt-in: read deploy/README.md \"Control API\")"
if [ "${NIGHTSHIFT_TAILSCALE_SERVE:-0}" = 1 ]; then
  if tailscale status >/dev/null 2>&1; then
    tailscale serve --bg 6767
    echo "  127.0.0.1:6767 is now on the tailnet — apply the owner-only ACL in deploy/README.md"
  else
    echo "  tailscale is not up yet: after 'tailscale up --ssh', re-run with NIGHTSHIFT_TAILSCALE_SERVE=1"
  fi
  serve_step="  2. NIGHTSHIFT_TAILSCALE_SERVE=1: 'tailscale serve --bg 6767' (as root, once up) +
     the owner-only Tailscale ACL from deploy/README.md. Never 'tailscale funnel'."
else
  echo "  off (default; NIGHTSHIFT_TAILSCALE_SERVE=1 publishes it to the tailnet)"
  serve_code=0
  serve_out=$(tailscale serve status 2>&1) || serve_code=$?
  if [ "$(nightshift_classify_serve "$serve_code" "$serve_out")" = configured ]; then
    echo "WARN: an earlier 'tailscale serve' still publishes 127.0.0.1:6767 to the tailnet." >&2
    echo "      Remove it with 'tailscale serve reset', or keep it deliberately with" >&2
    echo "      NIGHTSHIFT_TAILSCALE_SERVE=1 and the owner-only ACL (deploy/README.md)." >&2
  fi
fi

# ------------------------------------------------------------------ report --
say "provisioned. host RAM sizing preview"
total_gb=$(nightshift_mem_total_gb)
read -r mem_high mem_max <<<"$(nightshift_memory_limits "$total_gb")"
echo "  detected ${total_gb}G RAM -> omnigent-host MemoryHigh=${mem_high}G MemoryMax=${mem_max}G (applied by bootstrap.sh)"

say "next steps"
cat <<EOF
  1. tailscale up --ssh          # as root: join the tailnet; enables Tailscale SSH.
                                 # $NS_USER is deliberately NOT a tailscale operator:
                                 # workers must not be able to reconfigure the tailnet.
${serve_step}
  3. put the bundle at /srv/nightshift (rsync/git), owned by ROOT
  4. AS $NS_USER (runuser -u $NS_USER -- bash -l, or Tailscale SSH in):
       /srv/nightshift/deploy/bootstrap.sh
     then as root: /srv/nightshift/deploy/install-root-config.sh --user $NS_USER
  5. only AFTER Tailscale SSH works: lock public SSH down (deploy/README.md,
     "Security posture") — never expose 6767 or Postgres publicly
See deploy/README.md for the full flow, and deploy/providers/ for
provider-specific notes (AWS, Hostinger).
EOF
