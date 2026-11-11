#!/usr/bin/env bash
# Bootstrap the Omnigent crew box, after deploy/provision-ubuntu.sh (or a
# provider image) has installed the OS deps. Run as the non-root runtime user
# (default: `nightshift`; older EC2 installs used `ubuntu` — both work):
#   /srv/nightshift/deploy/bootstrap.sh
# Idempotent — safe to re-run after an upgrade.

set -euo pipefail

TEAMS_DIR="${TEAMS_DIR:-/srv/nightshift}"
PROJECT_DIR="${PROJECT_DIR:-/srv/project}"
# ADR-0021 state_root for narrative memory (#400), OUTSIDE every Git
# worktree by design -- so it is NOT derivable from PROJECT_DIR the way
# every other __PLACEHOLDER__ here is. No default, no ambient discovery
# (same posture ADR-0020 holds for state_root at `project bind` time):
# unset here means the state-backup unit's own STATE_ROOT-not-given
# branch runs (warns, still exits 0, still snapshots .omni + docs) --
# honest, not silent, exactly like running this host before memory ever
# existed on it. Set it explicitly (`STATE_ROOT=/path deploy/bootstrap.sh`)
# once `project bind` has produced one on this host, then re-run bootstrap
# to pick it up.
STATE_ROOT="${STATE_ROOT:-}"
# ADR-0085 -- the GitHub numeric repository id of the repo THIS deployment
# operates on. Same no-default, no-ambient-discovery posture as STATE_ROOT
# above, for the same reason: bootstrap prints the root command
# (deploy/install-root-config.sh) that writes it into the server environment
# as NIGHTSHIFT_REPOSITORY_ID, one of the exactly two variables
# `nightshift_authority.scope_from_env` reads to classify this deployment
# self vs external before a `gh pr merge` runs. Unset here means the merge
# fence has no scope to address and therefore denies every merge -- correct
# and unsurprising for a host that has not yet run
# `nightshift_authority.py ownership-declare` (see
# docs/runbooks/repository-ownership-bootstrap.md). Set it explicitly
# (`REPOSITORY_ID=12345 STATE_ROOT=/path deploy/bootstrap.sh`) once the
# authority registry at that STATE_ROOT has a declared ownership row.
REPOSITORY_ID="${REPOSITORY_ID:-}"
PATH="$HOME/.local/bin:$PATH"
# shellcheck source=lib/deploy-lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deploy-lib.sh"

say() { printf '\n=== %s\n' "$1"; }

# Everything here — CLIs, credentials, user units, Postgres — belongs to the
# unprivileged runtime user. Root would scatter state into /root and run
# agents with power they must not have.
[ "$(id -u)" -ne 0 ] || { echo "do not run bootstrap as root — run it as the runtime user (sudo -iu nightshift)" >&2; exit 1; }

# ---------------------------------------------------------------- CLIs -----
say "installing Omnigent + harness CLIs"
OMNIGENT_RUNTIME_LOCK="$TEAMS_DIR/deploy/omnigent-runtime.lock.json"
OMNIGENT_RUNTIME_IDENTITY="$TEAMS_DIR/deploy/omnigent-runtime-identity.py"
OMNIGENT_REQUIREMENT="$(
  python3 "$OMNIGENT_RUNTIME_IDENTITY" --lock "$OMNIGENT_RUNTIME_LOCK" requirement
)"
# Dependencies come from omnigent's own uv.lock at the pinned commit (see the
# lock file's header). `uv tool install` takes the versions but ignores
# hashes, so every dependency is then reinstalled under --require-hashes. A
# failure uninstalls the tool: unverified code must not stay on PATH.
OMNIGENT_DEPS_LOCK="$TEAMS_DIR/deploy/omnigent-deps.lock.txt"
uv tool install --python 3.12 --force "$OMNIGENT_REQUIREMENT" \
  --constraints "$OMNIGENT_DEPS_LOCK" \
  --build-constraints "$TEAMS_DIR/deploy/omnigent-build-deps.lock.txt"
OMNIPY="$(dirname "$(readlink -f "$(command -v omnigent)")")/python"
[ -x "$OMNIPY" ] || { echo "Omnigent tool interpreter is missing: $OMNIPY" >&2; exit 1; }
if ! uv pip install --python "$OMNIPY" --require-hashes --no-deps --reinstall \
       -r "$OMNIGENT_DEPS_LOCK" \
   || ! uv pip check --python "$OMNIPY"; then
  uv tool uninstall omnigent || true
  echo "Omnigent dependencies failed verification against $OMNIGENT_DEPS_LOCK — uninstalled" >&2
  exit 1
fi
"$OMNIPY" "$OMNIGENT_RUNTIME_IDENTITY" \
  --lock "$OMNIGENT_RUNTIME_LOCK" verify
# Global npm installs must land in a user-owned prefix: node comes from
# NodeSource under /usr (root-owned), and this script runs unprivileged, so a
# bare `npm install -g` dies with EACCES. $HOME/.local/bin is already on PATH.
npm config set prefix "$HOME/.local"
# The harness CLIs at exact versions, from the committed lockfile (clean install):
# every tarball, including the per-platform binary packages, must match the
# sha512 recorded in deploy/harness-clis/package-lock.json.
HARNESS_DIR="$HOME/.local/lib/nightshift-harness-clis"
mkdir -p "$HARNESS_DIR" "$HOME/.local/bin"
install -m 0644 "$TEAMS_DIR/deploy/harness-clis/package.json" \
  "$TEAMS_DIR/deploy/harness-clis/package-lock.json" "$HARNESS_DIR/"
( cd "$HARNESS_DIR" && npm ci --no-audit --no-fund )
ln -sfn "$HARNESS_DIR/node_modules/.bin/claude" "$HOME/.local/bin/claude"
ln -sfn "$HARNESS_DIR/node_modules/.bin/codex" "$HOME/.local/bin/codex"
# A pin the CLI can update past is not a pin: turn off Claude Code's
# self-updater for this user. An unparseable settings file is left alone.
python3 - <<'PY'
import json, os, sys
p = os.path.expanduser("~/.claude/settings.json")
os.makedirs(os.path.dirname(p), exist_ok=True)
try:
    with open(p) as f: cfg = json.load(f)
except FileNotFoundError:
    cfg = {}
except json.JSONDecodeError:
    print("WARN: ~/.claude/settings.json is not valid JSON; set env.DISABLE_AUTOUPDATER=1 by hand", file=sys.stderr)
    sys.exit(0)
if cfg.setdefault("env", {}).get("DISABLE_AUTOUPDATER") != "1":
    cfg["env"]["DISABLE_AUTOUPDATER"] = "1"
    with open(p, "w") as f: json.dump(cfg, f, indent=2)
PY

say "versions"
omnigent --version
# Both harness CLIs are the point of the box — fail loud, never WARN past them.
claude --version
codex --version
node --version; tmux -V
# bwrap by FUNCTION, not presence (DoD condition 5, issue #12). Severity stays
# a warning here, unchanged: provisioning already hard-fails on this, and
# bootstrap's job is to report the box's state to the operator running it.
# What changes is that the report is now TRUE on a 24.04 box.
bwrap_code=0
nightshift_bwrap_probe "${NIGHTSHIFT_BWRAP:-bwrap}" || bwrap_code=$?
if [ "$bwrap_code" -eq 0 ]; then
  echo "  $(nightshift_bwrap_diagnosis 0)"
else
  echo "WARN: $(nightshift_bwrap_diagnosis "$bwrap_code")" >&2
fi

# ------------------------------------------------ RTK token filter (opt-in) ----
# rtk (https://www.rtk-ai.app/) is the original author's personal tooling. Its
# PreToolUse hook in ~/.claude/settings.json REWRITES every Bash command a
# Claude worker runs into `rtk ...` and filters the output, which puts a
# third-party binary in the path of every worker command. So it is OFF unless
# NIGHTSHIFT_RTK=1, and bootstrap never installs rtk: put a build you trust on
# PATH first. Codex has no hook surface — Claude workers only.
say "rtk token filter (opt-in)"
if [ "${NIGHTSHIFT_RTK:-0}" != 1 ]; then
  echo "  off (default; NIGHTSHIFT_RTK=1 with rtk on PATH installs its hook)"
  if grep -qs '"rtk hook claude"' "$HOME/.claude/settings.json"; then
    echo "  NOTE: ~/.claude/settings.json still has an rtk hook from an earlier bootstrap — remove it to opt out" >&2
  fi
elif command -v rtk >/dev/null 2>&1; then
  python3 - <<'PY'
import json, os, sys
p = os.path.expanduser("~/.claude/settings.json")
os.makedirs(os.path.dirname(p), exist_ok=True)
try:
    with open(p) as f: cfg = json.load(f)
except FileNotFoundError:
    cfg = {}
except json.JSONDecodeError:
    print("WARN: ~/.claude/settings.json is not valid JSON; rtk hook not installed", file=sys.stderr)
    sys.exit(0)
hook = {"type": "command", "command": "rtk hook claude"}
pre = cfg.setdefault("hooks", {}).setdefault("PreToolUse", [])
if not any(h.get("command") == "rtk hook claude"
           for m in pre for h in m.get("hooks", [])):
    pre.append({"matcher": "Bash", "hooks": [hook]})
    with open(p, "w") as f: json.dump(cfg, f, indent=2)
    print("  rtk hook installed into ~/.claude/settings.json")
else:
    print("  rtk hook already present")
PY
  rtk --version
else
  echo "WARN: NIGHTSHIFT_RTK=1 but rtk is not on PATH — install it yourself, then re-run" >&2
fi

# ------------------------------------------------- control plane CLI -------
# nightshift-ctl is the deterministic side of the org: worker semaphore, task
# claims, leases, sequences, checkpoints, vendor cooldowns. Agents call it by
# bare name, so it must be on PATH. stdlib-only python3 — nothing to build.
say "nightshift-ctl control plane"
ln -sf "$TEAMS_DIR/bin/nightshift-ctl" "$HOME/.local/bin/nightshift-ctl"
nightshift-ctl --db /tmp/nightshift-ctl-boot-check.db selftest && rm -f /tmp/nightshift-ctl-boot-check.db*

# --------------------------------------------------- Codex sandbox config ---
# Codex workers run yolo:false — Omnigent's full bypass
# (--dangerously-bypass-approvals-and-sandbox) is OFF, so the CLI falls back
# to this config: never ask (unattended box), confine WRITES to the
# worktree/cwd + tmp + the caches below, keep network (git push, package
# installs). A write the sandbox blocks fails visibly instead of stalling.
# See docs/TRUST-BOUNDARY.md. Only written if absent — your edits survive.
say "codex sandbox config"
if [ ! -f "$HOME/.codex/config.toml" ]; then
  mkdir -p "$HOME/.codex"
  cat > "$HOME/.codex/config.toml" <<'EOF'
# Written by nightshift bootstrap. Unattended posture:
# never ask a human; sandbox writes to the workspace; network on.
approval_policy = "never"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
# Toolchain homes are NOT granted: a writable ~/.cargo, ~/.rustup, ~/go,
# ~/.cache or ~/.npm let a worker replace a binary or poison a cache that an
# unsandboxed seat runs next. Codex workers get their own homes and caches
# under /tmp (shell_environment_policy below) and use the installed
# toolchains read-only. ~/.floci stays: the local cloud emulators keep state
# there and a missing root FAILS a build mid-task.
#
# ~/.local/state was REMOVED (F2, `security` on PR #35). Evidence for removing
# it outright rather than narrowing it: nothing in this repo writes there. The
# only references were the omnigent state dir itself, the attestation write
# below, and this grant; the comment justifying the grant covers only
# cargo/rustup/go/floci, none of which use it. And on the live box
# `~/.local/state` DOES NOT EXIST AT ALL, so no consumer can have been relying
# on it. It was granting agent workers write access to the installer
# attestation -- the one fact the watchdog trusts to decide whether silence is
# healthy -- in exchange for nothing.
writable_roots = ["~/.floci"]

[shell_environment_policy]
set = { CARGO_HOME = "/tmp/nightshift-codex-toolchain/cargo", GOPATH = "/tmp/nightshift-codex-toolchain/go", GOMODCACHE = "/tmp/nightshift-codex-toolchain/go/pkg/mod", GOCACHE = "/tmp/nightshift-codex-toolchain/go-build", XDG_CACHE_HOME = "/tmp/nightshift-codex-toolchain/cache", npm_config_cache = "/tmp/nightshift-codex-toolchain/npm" }
EOF
  echo "  wrote ~/.codex/config.toml (approval never, workspace-write)"
else
  echo "  ~/.codex/config.toml exists — leaving it alone"
  # Upgrade hint: floci keeps emulator state in ~/.floci, and a config written
  # before it landed fails those builds mid-task.
  grep -q "~/.floci" "$HOME/.codex/config.toml" \
    || echo "  WARN: writable_roots is missing ~/.floci — add it, or Codex workers cannot write there"
  # A config from before toolchain homes were withdrawn still lets a Codex
  # worker replace what unsandboxed seats execute.
  for probe in .cargo/bin/cargo .rustup/settings.toml go/bin .cache/go-build .npm/_cacache; do
    if covering_root=$(nightshift_sandbox_grants_path "$HOME/$probe" "$HOME/.codex/config.toml"); then
      echo "  WARN: ~/.codex/config.toml grants writable_root '$covering_root', which covers ~/$probe." >&2
      echo "        FIX: drop it and set the toolchain homes under /tmp as bootstrap now writes them." >&2
    fi
  done
  # INVERSE hint, and the load-bearing half of F2 on an EXISTING box. The
  # heredoc above is only written when the config is ABSENT, so narrowing it
  # changes nothing on a box that already has the wide grant -- which is
  # exactly where `security` measured this. Ask the file on disk whether it
  # still grants a root covering the installer attestation, and say so loudly.
  #
  # WARN rather than rewrite, deliberately: the contract a few lines up is that
  # this file is written only if absent and the operator's edits survive.
  # Silently rewriting somebody's sandbox policy is its own defect, and a
  # sandbox is exactly the file an operator is entitled to have opinions about.
  if covering_root=$(nightshift_sandbox_grants_path \
       "$HOME/.config/omnigent/bootstrapped-at" "$HOME/.codex/config.toml"); then
    echo "  WARN: your ~/.codex/config.toml grants writable_root '$covering_root'," >&2
    echo "        which CONTAINS \$HOME/.config/omnigent — the installer attestation" >&2
    echo "        the watchdog trusts to tell a fresh install from a restored control" >&2
    echo "        plane. A Codex worker running approval_policy=never could write it," >&2
    echo "        date its own install, and keep a dead Program silent indefinitely." >&2
    echo "        FIX: remove or narrow that root in ~/.codex/config.toml." >&2
  fi
fi

# ------------------------------------------------------------- Postgres ----
say "starting Postgres (session history must survive restarts)"
mkdir -p "$HOME/omnigent-db"
# install (not cp -n): an upgraded compose file must propagate on re-run,
# or "idempotent — safe to re-run after an upgrade" is false for this file.
install -m 0644 "$TEAMS_DIR/deploy/docker-compose.postgres.yml" "$HOME/omnigent-db/docker-compose.yml"
if [ ! -f "$HOME/omnigent-db/.env" ]; then
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > "$HOME/omnigent-db/.env"
  chmod 600 "$HOME/omnigent-db/.env"
fi
( cd "$HOME/omnigent-db" && docker compose up -d )

PGPASS=$(grep POSTGRES_PASSWORD "$HOME/omnigent-db/.env" | cut -d= -f2)
# The password never goes into the URI: the URI becomes the server's
# `--database-uri` argument, and argv is readable by every local user through
# ps. libpq reads it from this 0600 file instead, named by PGPASSFILE.
PGPASSFILE_PATH="$HOME/.config/omnigent/pgpass"
mkdir -p "$HOME/.config/omnigent"
( umask 077
  printf '127.0.0.1:5433:omnigent:omnigent:%s\n' "$PGPASS" > "$PGPASSFILE_PATH.tmp" )
mv -f "$PGPASSFILE_PATH.tmp" "$PGPASSFILE_PATH"

# ----------------------------------------------------------- server env ---
# server.env sets PYTHONPATH (the policy modules), the ADR-0085 merge-fence
# scope (NIGHTSHIFT_STATE_ROOT/NIGHTSHIFT_REPOSITORY_ID) and must never carry
# a *_FAIL_OPEN switch. This script used to write it into $HOME and call it
# "root-installed and 0600"; it was neither. It ran as the runtime user, the
# same uid every worker runs as, so any worker could repoint the scope at a
# forged ownership=self registry or re-arm a fail-open switch, effective at
# the next Restart=always. It is now /etc/nightshift/server.env, written by
# root with deploy/install-root-config.sh; this unprivileged script can only
# print the command (audit C6).
say "server environment (root step)"
SERVER_ENV=/etc/nightshift/server.env
root_cmd="$TEAMS_DIR/deploy/install-root-config.sh --user $USER"
[ -n "$STATE_ROOT" ] && [ -n "$REPOSITORY_ID" ] \
  && root_cmd="$root_cmd --state-root $STATE_ROOT --repository-id $REPOSITORY_ID"
if [ -f "$SERVER_ENV" ]; then
  echo "  $SERVER_ENV present; after changing STATE_ROOT/REPOSITORY_ID, as root re-run:"
else
  echo "  $SERVER_ENV missing; omnigent-server will not start until, as root:"
fi
echo "    $root_cmd"
if [ -z "$STATE_ROOT" ] || [ -z "$REPOSITORY_ID" ]; then
  echo "  STATE_ROOT/REPOSITORY_ID not both set -> the merge fence has no scope and will DENY every merge until both are passed to that command after 'nightshift_authority.py ownership-declare --ownership self' (docs/runbooks/repository-ownership-bootstrap.md)"
fi
[ -f "$HOME/.config/omnigent/server.env" ] \
  && echo "  WARN: $HOME/.config/omnigent/server.env is no longer read; move any custom line to $SERVER_ENV by hand, then delete it" >&2

# --------------------------------------------------------------- units -----
say "installing systemd units"
# The host cgroup budget derives from detected RAM (deploy-lib.sh): a 16G box
# gets 11G/13G, a 32G box 24G/28G — override with NIGHTSHIFT_MEMORY_HIGH /
# NIGHTSHIFT_MEMORY_MAX (values like "24G") if you know better.
total_gb=$(nightshift_mem_total_gb)
read -r mem_high_gb mem_max_gb <<<"$(nightshift_memory_limits "$total_gb")"
MEMORY_HIGH="${NIGHTSHIFT_MEMORY_HIGH:-${mem_high_gb}G}"
MEMORY_MAX="${NIGHTSHIFT_MEMORY_MAX:-${mem_max_gb}G}"
echo "  ${total_gb}G RAM detected -> omnigent-host MemoryHigh=$MEMORY_HIGH MemoryMax=$MEMORY_MAX"
if [ -n "$STATE_ROOT" ]; then
  echo "  STATE_ROOT=$STATE_ROOT -> nightshift-state-backup will also cover narrative memory (#400)"
else
  echo "  STATE_ROOT not set -> nightshift-state-backup covers .omni + docs ONLY (no narrative memory on this host yet, or STATE_ROOT was not passed)"
fi

mkdir -p "$HOME/.config/systemd/user"
for unit in omnigent-server.service omnigent-host.service \
            nightshift-state-backup.service nightshift-state-backup.timer \
            nightshift-watchdog.service nightshift-watchdog.timer \
            nightshift-heartbeat.service nightshift-heartbeat.timer; do
  install -m 0644 "$TEAMS_DIR/deploy/systemd/$unit" "$HOME/.config/systemd/user/$unit"
done
sed -i "s#__TEAMS_DIR__#$TEAMS_DIR#g; s#__PROJECT_DIR__#$PROJECT_DIR#g; s#__HOME__#$HOME#g; \
        s#__MEMORY_HIGH__#$MEMORY_HIGH#g; s#__MEMORY_MAX__#$MEMORY_MAX#g; \
        s#__STATE_ROOT__#$STATE_ROOT#g" \
  "$HOME/.config/systemd/user/"omnigent-*.service \
  "$HOME/.config/systemd/user/"nightshift-*.service "$HOME/.config/systemd/user/"nightshift-*.timer

# Linger keeps user units alive after logout and starts them at boot without a
# login. provision-ubuntu.sh (root) is what establishes it; this is a courtesy
# retry for older installs, and it is NOT allowed to escalate: the runtime
# account has no elevated rights by design, so a failure here becomes a loud
# instruction aimed at root rather than a password prompt nobody will answer.
if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  loginctl enable-linger "$USER" 2>/dev/null \
    || echo "WARN: linger is off — as root run: loginctl enable-linger $USER (preflight will FAIL until then)" >&2
fi

# Install the user units. This PROBES the user manager before touching it.
#
# The old code called `systemctl --user daemon-reload` here, blind. After
# `su - nightshift` there is no session bus, so that call failed, and under
# `set -euo pipefail` it aborted the script BEFORE the `enable --now` lines
# below it. Result on the live box: the watchdog timer and the state-backup
# timer were never enabled, so this run had no dead-man's switch and no off-box
# state. See issue #9, DoD condition 3, ADR reconciliation A11.
#
# ---------------------------------------- installer attestation (R-22) -----
# Written BEFORE the units are enabled, and that ordering is the point: the
# watchdog timer must never be armed while this file is missing, or the very
# first tick reads "no installer attestation" and alerts on a healthy install.
#
# What it is: positive evidence that THE INSTALLER RAN, and when. The watchdog
# uses it to tell a genuinely fresh box (no heartbeat yet, correctly) from a
# control plane that was RESTORED onto an existing box (no heartbeat because the
# records are gone). Those are identical in the control plane and opposite in
# meaning — one is healthy, the other is a dead Program.
#
# Why a FILE and not a `meta` row in control.db: `nightshift-ctl dump` exports
# slots, claims, leases, tasks, checkpoints and vendors — NOT `meta` (issue
# #41). So `meta` is destroyed by exactly the event this fact must survive, and
# storing it there would make it disappear in lockstep with the heartbeat it is
# supposed to discriminate against. Independent writers are not enough; the fact
# must have an independent DESTRUCTION mode.
#
# This does not write a heartbeat and is not a liveness signal. It attests
# something that is true (the installer ran) and is read by no liveness reader,
# so ADR-0002 §1 — no signal may stand in for another — is untouched.
#
# Refreshed on every run, deliberately: bootstrap is a manual, idempotent
# operator action, and on a re-run "the installer ran just now" is simply true.
# The restore path does NOT run bootstrap (it is `git restore` plus a
# nightshift-ctl re-seed), so a restore leaves this file OLD — which is exactly
# the discrimination the watchdog needs.
# HARDCODED to the same literal the watchdog hardcodes, and the XDG
# indirection is dropped on purpose. This script runs under an interactive
# login shell; the watchdog runs under the systemd user manager, which exports
# only HOME and PATH. Resolving through XDG_STATE_HOME meant the two halves of
# this producer/consumer pair could land in different directories on a box
# whose operator sets it -- and the failure that produces is a HEALTHY install
# alerting "the control plane was RESTORED". Two processes that cannot disagree
# beat two asserted to agree. The location also moved OUT of the Codex sandbox
# writable_roots (F2, above): an attestation an agent can author attests
# nothing.
NIGHTSHIFT_STATE_DIR="$HOME/.config/omnigent"
mkdir -p "$NIGHTSHIFT_STATE_DIR"
date +%s > "$NIGHTSHIFT_STATE_DIR/bootstrapped-at"
echo "  installer attestation -> $NIGHTSHIFT_STATE_DIR/bootstrapped-at"

# This installs eight unit files. nightshift_install_user_units then ENABLES the
# five that are enabled directly (three timers with --now, two services); the
# other three are timer-activated .service units.
# On the unreachable path it enables NONE of them and prints the root fix.
if ! nightshift_install_user_units "$(id -u)" "$USER"; then
  exit 1
fi

say "next steps (interactive, once)"
cat <<'EOF'
  1. claude setup-token          # long-lived subscription token, paste from browser
  2. codex login                 # device auth for the Codex subscription
  3. gh auth login               # scope: repo (contents + PR write). NOT admin.
  4. gh auth refresh -s project  # Projects board scope — login does not grant it
  5. git -C /srv/project clone … # or clone your repo into /srv/project
  6. /srv/nightshift/bin/nightshift-init.sh /srv/project
  7. /srv/nightshift/bin/nightshift-tracker-init.sh /srv/project   # labels, board, status issue
  8. $EDITOR /srv/project/.omni/brief.md   # the ONLY file you write — goal.md is generated
  9. as root: /srv/nightshift/deploy/install-root-config.sh --user <this user>
     (root-owned server.env; add --state-root/--repository-id once declared)
 10. the UI stays on 127.0.0.1:6767, which has NO authentication: reach it
     with 'ssh -L 6767:127.0.0.1:6767' over Tailscale SSH. Publishing it to
     the tailnet is opt-in, root-only, and needs the owner-only ACL
     (deploy/README.md "Control API"). Never 'tailscale funnel'.
 11. /srv/nightshift/deploy/preflight.sh    # verify everything
 12. systemctl --user start omnigent-server omnigent-host
 13. launch the program agent (see deploy/README.md "Starting the run")
EOF
