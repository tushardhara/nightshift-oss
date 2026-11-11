# shellcheck shell=bash
# Deterministic helpers shared by provision-ubuntu.sh, bootstrap.sh and
# preflight.sh. Pure functions only — no side effects, no sudo, no network —
# so tests/test_deploy_lib.py can pin the behaviour down.
#
# Source it:   . "$(dirname "$0")/lib/deploy-lib.sh"

# --------------------------------------------------------------- memory ----
# nightshift_mem_total_gb [meminfo_path]
#   Nominal host RAM in GiB, rounded to the nearest GiB. The kernel reserves
#   a few hundred MB before /proc/meminfo, so a "32 GB" VPS reports ~31.4 GiB;
#   nearest-GiB rounding recovers the size the machine was sold as.
nightshift_mem_total_gb() {
  local meminfo="${1:-/proc/meminfo}" kib
  kib=$(awk '/^MemTotal:/{print $2}' "$meminfo" 2>/dev/null)
  [ -n "$kib" ] || return 1
  local gb=$(( (kib + 524288) / 1048576 ))
  [ "$gb" -ge 1 ] || gb=1
  echo "$gb"
}

# nightshift_memory_limits <total_gb>
#   Derive the omnigent-host cgroup budget from host RAM. Prints:
#       <MemoryHigh_gb> <MemoryMax_gb>
#   The host cgroup holds every native worker (Node CLI + tmux pane), the
#   runners, and the project's own test runs — the bulk of the box. Everything
#   OUTSIDE the cgroup (kernel, systemd, Docker+Postgres, omnigent server,
#   Tailscale, journald, sshd) needs the reserve.
#
#   Rule (integer GiB): reserve max(3, total/8) for the OS layer, cap the
#   cgroup at total-reserve, and start reclaim (MemoryHigh) max(2, total/8)
#   below the cap. Anchors this reproduces exactly:
#       16 GB -> high 11 / max 13   (the original EC2 m7i.xlarge sizing)
#       32 GB -> high 24 / max 28   (leaves 4 GB for OS/Docker/Postgres/&c.)
#   Small boxes clamp to high>=1, max>=2 so the unit stays valid; running on
#   them is preflight's problem, not this function's.
nightshift_memory_limits() {
  local total="$1" reserve sub mmax mhigh
  case "$total" in (''|*[!0-9]*) return 1 ;; esac
  reserve=$(( total / 8 )); [ "$reserve" -ge 3 ] || reserve=3
  mmax=$(( total - reserve )); [ "$mmax" -ge 2 ] || mmax=2
  sub=$(( total / 8 )); [ "$sub" -ge 2 ] || sub=2
  mhigh=$(( mmax - sub )); [ "$mhigh" -ge 1 ] || mhigh=1
  echo "$mhigh $mmax"
}

# ----------------------------------------------------------------- user ----
# nightshift_resolve_user <explicit_user> <existing_owner_of_srv_nightshift>
#   Which Linux user owns the runtime. Precedence:
#     1. an explicit request (NIGHTSHIFT_USER) always wins;
#     2. else adopt the existing non-root owner of /srv/nightshift — this is
#        the upgrade path for a box provisioned under EC2's `ubuntu` user.
#        Adoption does not vet that user's sudo rights; preflight FAILs if it
#        can sudo without a password (`ubuntu` can, by cloud-init default);
#     3. else the default, `nightshift`.
nightshift_resolve_user() {
  local explicit="${1:-}" owner="${2:-}"
  if [ -n "$explicit" ]; then echo "$explicit"; return; fi
  if [ -n "$owner" ] && [ "$owner" != "root" ]; then echo "$owner"; return; fi
  echo "nightshift"
}

# nightshift_passwordless_sudo [sudo_cmd]
#   Exit 0 when the CURRENT user can run anything through sudo without a
#   password. Every worker runs as the runtime user, so for them that is root.
#   `sudo -n -l` lists without a password only when some entry is NOPASSWD
#   (sudoers' default listpw=any); `sudo -n true` catches listpw=never and a
#   cached credential. Read-only: it never runs anything but `true`.
nightshift_passwordless_sudo() {
  local sudo_cmd="${1:-sudo}"
  command -v "$sudo_cmd" >/dev/null 2>&1 || return 1
  case "$("$sudo_cmd" -n -l 2>/dev/null)" in *NOPASSWD*) return 0 ;; esac
  "$sudo_cmd" -n true >/dev/null 2>&1
}

# ------------------------------------------------------------- tailscale ----
# nightshift_classify_serve <exit_code> <output...>
#   Classify a `tailscale serve status` attempt. The runtime user is NOT the
#   tailscaled operator (deliberately — operator rights would let any worker
#   reconfigure the tailnet, including `funnel`), so on a correctly locked-down
#   box this command may fail with an access error. That is EXPECTED and must
#   degrade to a skip/warn, never a false FAIL and never advice to grant
#   operator. Verdicts:
#     configured    exit 0 and the config proxies to 127.0.0.1:6767
#     unconfigured  exit 0 but no 6767 proxy (root must run: tailscale serve --bg 6767)
#     denied        permission/operator-style refusal (verify from a root shell)
#     error         anything else (old CLI, daemon down mid-command, ...)
nightshift_classify_serve() {
  local code="$1"; shift
  local out="$*"
  if [ "$code" -eq 0 ]; then
    case "$out" in
      *127.0.0.1:6767*|*localhost:6767*) echo configured ;;
      *)                                 echo unconfigured ;;
    esac
    return 0
  fi
  case "$out" in
    *[Aa]ccess\ denied*|*[Pp]ermission\ denied*|*not\ allowed*|*[Oo]perator*)
      echo denied ;;
    *)
      echo error ;;
  esac
}

# ---------------------------------------------------------------- github ----
# nightshift_github_slug <remote_url>
#   `owner/repo` for a github.com remote (https, ssh, scp-style), exit 1 for
#   anything else, so callers can tell "not GitHub" from "GitHub, public".
nightshift_github_slug() {
  local url="${1:-}" rest
  case "$url" in
    https://github.com/*)        rest="${url#https://github.com/}" ;;
    ssh://git@github.com/*)      rest="${url#ssh://git@github.com/}" ;;
    git@github.com:*)            rest="${url#git@github.com:}" ;;
    *) return 1 ;;
  esac
  rest="${rest%/}"; rest="${rest%.git}"
  case "$rest" in
    */*/*|*[!A-Za-z0-9._/-]*|/*|*/) return 1 ;;
    ?*/?*) printf '%s\n' "$rest" ;;
    *) return 1 ;;
  esac
}

# nightshift_repo_visibility <owner/repo> [gh_cmd]
#   GitHub's own answer: private, internal or public. Exit non-zero (and no
#   output) when it cannot be read; callers must treat that as NOT private.
nightshift_repo_visibility() {
  local slug="$1" gh_cmd="${2:-gh}" out
  out=$("$gh_cmd" api "repos/$slug" --jq .visibility 2>/dev/null) || return 1
  case "$out" in
    private|internal|public) printf '%s\n' "$out" ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------- docker ----
# nightshift_docker_bind_json [existing_json]
#   Produce the /etc/docker/daemon.json content that makes published container
#   ports default to 127.0.0.1. This is the deterministic half of the emulator
#   exposure rule: `floci start` publishes through Docker and has no
#   bind-address flag, so the guarantee has to sit below it.
#
#   Merges into whatever is already there (log-driver, registry mirrors, ...)
#   rather than overwriting. Prints the merged JSON on success. Exit 1 means the
#   input was not valid JSON — the caller must then leave the file ALONE and
#   warn, never clobber a config it could not parse.
#   Verified end to end against a real dockerd: an unqualified `-p 4566:80`
#   lands on 127.0.0.1. An explicit `-p 0.0.0.0:...` still wins, which is why
#   preflight also scans live listeners.
nightshift_docker_bind_json() {
  local existing="${1:-}"
  if [ -z "$existing" ]; then
    printf '{\n  "ip": "127.0.0.1"\n}\n'
    return 0
  fi
  printf '%s' "$existing" | jq '. + {ip: "127.0.0.1"}' 2>/dev/null || return 1
}

# nightshift_docker_bind_ok <daemon_json_content>
#   True when the config already pins the default publish IP to loopback.
#   Used by provisioning to skip a needless docker restart, and by preflight to
#   assert the control is in place instead of only auditing its effect.
nightshift_docker_bind_ok() {
  local content="${1:-}"
  [ -n "$content" ] || return 1
  [ "$(printf '%s' "$content" | jq -r '.ip // empty' 2>/dev/null)" = "127.0.0.1" ]
}

# ------------------------------------------------------------ toolchains ----
# nightshift_version_ge <have> <want>
#   Dotted numeric version compare — true when have >= want. Used to decide
#   whether an already-installed toolchain (go, rust) is new enough, so a
#   re-run of provisioning is a no-op instead of a re-download. Missing fields
#   count as 0 (1.25 >= 1.25.0), non-numeric junk sorts as 0 so a garbled
#   `go version` reads as "too old" and gets replaced rather than trusted.
nightshift_version_ge() {
  local have="${1:-0}" want="${2:-0}" i h w
  local -a H W
  IFS=. read -r -a H <<<"$have"
  IFS=. read -r -a W <<<"$want"
  for ((i=0; i<${#W[@]} || i<${#H[@]}; i++)); do
    h="${H[i]:-0}"; w="${W[i]:-0}"
    case "$h" in (''|*[!0-9]*) h=0 ;; esac
    case "$w" in (''|*[!0-9]*) w=0 ;; esac
    if [ "$h" -gt "$w" ]; then return 0; fi
    if [ "$h" -lt "$w" ]; then return 1; fi
  done
  return 0
}

# ------------------------------------------------------------ bubblewrap ----
# nightshift_bwrap_probe [bwrap_cmd] [as_user]
#   Is bubblewrap USABLE? Not "is the binary there" — that question is what
#   issue #12 is about. On stock Ubuntu 24.04
#   (`kernel.apparmor_restrict_unprivileged_userns=1`, and a bubblewrap package
#   that ships no AppArmor profile) bwrap installs perfectly and cannot create
#   a namespace, so every Codex seat dies at its first tool call while
#   `command -v` reports success.
#
#   `--ro-bind / / /bin/true` is the cheapest complete exercise of the thing
#   that actually breaks: it sets up the uid map and a mount namespace, runs a
#   no-op, and mutates nothing (the only bind is read-only).
#
#   `as_user` matters and is not optional politeness: provisioning runs as
#   ROOT, and root can create user namespaces even when the restriction denies
#   them to everyone else. A root-run probe therefore passes on a box where
#   every worker will still die. Probe as the account that will run the agents.
#
#   Exit codes are distinct because the remediations are different:
#     0  usable
#     1  installed, but cannot run   -> AppArmor/userns; needs root, see below
#     2  not installed               -> apt install bubblewrap (provisioning)
#     3  cannot probe as `as_user`   -> runuser missing
nightshift_bwrap_probe() {
  local bw="${1:-bwrap}" as_user="${2:-}"
  command -v "$bw" >/dev/null 2>&1 || return 2
  if [ -n "$as_user" ] && [ "$as_user" != "$(id -un)" ]; then
    command -v runuser >/dev/null 2>&1 || return 3
    runuser -u "$as_user" -- "$bw" --ro-bind / / /bin/true >/dev/null 2>&1 || return 1
    return 0
  fi
  "$bw" --ro-bind / / /bin/true >/dev/null 2>&1 || return 1
  return 0
}

# nightshift_bwrap_diagnosis <probe_exit_code> [as_user]
#   One line, actionable, naming the cause AND the exact remediation command —
#   the standard DoD condition 12 sets for every FAIL branch. Kept beside the
#   probe so the wording cannot drift away from the exit codes it explains.
nightshift_bwrap_diagnosis() {
  local code="${1:-1}" who="${2:-}" as=""
  [ -n "$who" ] && as=" as $who"
  case "$code" in
    0) echo "bwrap functional probe$as: 'bwrap --ro-bind / / /bin/true' exits 0" ;;
    2) echo "bwrap missing$as - bubblewrap is not installed; native harness terminals and every Codex seat fail at launch. Remediation: re-run deploy/provision-ubuntu.sh as root (it apt-installs bubblewrap)" ;;
    3) echo "bwrap functional probe could not be run$as - runuser is missing, so the probe cannot drop to the runtime user and a root-only result would be misleading. Remediation: apt install util-linux, then re-run" ;;
    *) echo "bwrap is INSTALLED BUT CANNOT RUN$as - 'bwrap --ro-bind / / /bin/true' exits non-zero. Cause: unprivileged user namespaces are refused host-wide (kernel.apparmor_restrict_unprivileged_userns=1, the Ubuntu 24.04 default) and this bubblewrap ships no AppArmor profile. Consequence: every Codex seat dies at its first tool call and bwrap-sandboxed terminals fail, while the binary looks present. Remediation as root (.omni/blocked.md): write /etc/apparmor.d/bwrap granting 'userns' for /usr/bin/bwrap, then 'apparmor_parser -r /etc/apparmor.d/bwrap'; verify with 'bwrap --ro-bind / / /bin/true; echo \$?' printing 0" ;;
  esac
}

# ------------------------------------------------------- user manager ------
# The `systemctl --user` capability, and why it is three functions.
#
# `su - nightshift` gives you a shell but NOT a session bus: `XDG_RUNTIME_DIR`
# and `DBUS_SESSION_BUS_ADDRESS` are simply absent, and every `systemctl --user`
# call then dies with `Failed to connect to bus: No medium found`. Under
# `set -euo pipefail` that killed bootstrap.sh at its first `daemon-reload` —
# BEFORE the `enable --now` lines — so on the live box neither the watchdog
# timer nor the state-backup timer was ever enabled. Bug 3 silently disabled the
# recovery layer that exists to catch bug 4.
#
# Confirmed on the host: linger was ON, /run/user/1000 existed and
# user@1000.service was ACTIVE. Only the two variables were missing. So this is
# not session creation, and it must not become session creation. In the Codex
# architect's words: "Linger permits a user manager to persist or start
# independently of an interactive login; it does not retroactively construct the
# current shell's environment."
#
# Split of duties is ADR reconciliation A11: the ROOT provisioning step
# ESTABLISHES the capability; the user-run bootstrap PROBES it and fails with an
# instruction aimed at the root step. The runtime account has no sudo by design,
# so bootstrap must never reach for one.

# nightshift_user_runtime_dir [uid]
nightshift_user_runtime_dir() {
  local uid="${1:-}"
  [ -n "$uid" ] || uid="$(id -u)"
  echo "/run/user/$uid"
}

# nightshift_user_bus_env [uid]
#   The two variables, DERIVED from the uid rather than inherited from a login
#   shell that may never have existed. Prints VAR=VALUE lines.
nightshift_user_bus_env() {
  local rt; rt="$(nightshift_user_runtime_dir "${1:-}")"
  printf 'XDG_RUNTIME_DIR=%s\n' "$rt"
  printf 'DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus\n' "$rt"
}

# nightshift_user_manager_ready [uid]
#   Set the bus environment WHEN ABSENT (an existing, working session is never
#   overwritten), then ask the manager a READ-ONLY question. Exit 0 = reachable.
#   `show-environment` mutates nothing, so a probe can never be the thing that
#   half-installs the box.
nightshift_user_manager_ready() {
  local rt; rt="$(nightshift_user_runtime_dir "${1:-}")"
  : "${XDG_RUNTIME_DIR:=$rt}"
  : "${DBUS_SESSION_BUS_ADDRESS:=unix:path=${XDG_RUNTIME_DIR}/bus}"
  export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
  systemctl --user show-environment >/dev/null 2>&1
}

# nightshift_user_manager_remediation [user] [uid]
#   Actionable diagnosis aimed at the ROOT step (DoD condition 12). It names the
#   exact commands, because one safe deterministic command exists here. It must
#   never advise privilege escalation from the runtime account, which has none.
nightshift_user_manager_remediation() {
  local user="${1:-}" uid="${2:-}"
  [ -n "$user" ] || user="${USER:-$(id -un)}"
  [ -n "$uid" ] || uid="$(id -u)"
  cat <<EOF
FAIL: the systemd user manager for '$user' is not reachable, so 'systemctl --user'
      cannot run and NO user units have been installed or enabled.
      This is fixed in the ROOT provisioning step, not here: the runtime account
      is intentionally unprivileged and cannot establish this capability itself.

      As root on this host:
          loginctl enable-linger $user
          systemctl start user@${uid}.service

      Then re-run this script as $user. Verify from a root shell with:
          runuser -u $user -- env XDG_RUNTIME_DIR=/run/user/${uid} \\
              DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus \\
              systemctl --user is-system-running
EOF
}

# nightshift_install_user_units [uid] [user]
#   Probe, THEN mutate — the ordering is the entire fix. Nothing below the probe
#   is reachable unless the manager answered, so the unreachable case cannot
#   leave the five units named below half-enabled. If a later step fails anyway,
#   what this call enabled is rolled back (and STOPPED), so "half-installed"
#   stays undecidable-free.
#
#   Five, not eight: bootstrap.sh INSTALLS eight unit files, but three of them
#   are the .service units that their timers activate, which are never enabled
#   directly. This function enables three timers (--now) and two services.
nightshift_install_user_units() {
  local uid="${1:-}" user="${2:-}" u
  local -a _ns_enabled=()

  if ! nightshift_user_manager_ready "$uid"; then
    nightshift_user_manager_remediation "$user" "$uid" >&2
    return 1
  fi

  _ns_units_rollback() {
    [ "${#_ns_enabled[@]}" -gt 0 ] || return 0
    # --now is required: these timers were enabled with --now, and a bare
    # `disable` leaves them RUNNING for the rest of the boot, so the rollback
    # would not actually undo what it started.
    systemctl --user disable --now "${_ns_enabled[@]}" >/dev/null 2>&1 || true
  }

  systemctl --user daemon-reload || { _ns_units_rollback; return 1; }

  # Timers first: these three ARE the recovery layer, and they are what bug 3
  # silently removed from this run.
  for u in nightshift-state-backup.timer nightshift-watchdog.timer nightshift-heartbeat.timer; do
    systemctl --user enable --now "$u" || { _ns_units_rollback; return 1; }
    _ns_enabled+=("$u")
  done
  # Enable (not start): WantedBy=default.target then survives a reboot. These
  # are started later, after the interactive credential logins exist.
  for u in omnigent-server.service omnigent-host.service; do
    systemctl --user enable "$u" || { _ns_units_rollback; return 1; }
    _ns_enabled+=("$u")
  done
  return 0
}

# ------------------------------------------------------- go toolchain ------
# Bug 1 (two sites) and bug 2 of the brief, DoD conditions 1 and 2, issue #8.
#
# The shape that breaks: a NETWORK PRODUCER piped into an EARLY-CLOSING CONSUMER
# under `set -euo pipefail`.
#
#     go_want=$(curl -fsSL '.../VERSION?m=text' | head -1 | sed 's/^go//')
#
# `head -1` exits after the first line; curl then writes to a closed pipe and
# can exit 23; `pipefail` promotes that to the pipeline's status; `set -e` kills
# the script — even though the first line was perfectly valid, and BEFORE the
# explicit diagnostic on the next line could ever run.
#
# The fix is structural, not a `|| true` and NOT `set +o pipefail` (which would
# hide genuine network failures): fetch the response to a temp file, then parse
# the file locally. A file read cannot SIGPIPE its producer. For JSON, one `jq`
# expression with `first(...)` replaces the trailing `head -1` for the same
# reason.
#
# Every function takes its curl/go command as a parameter so the tests can drive
# them without network access and without touching the host /usr/local/go.

# nightshift_go_resolve_version [curl_cmd]
#   Latest Go version, e.g. "1.25.1". Empty/unmatched/HTTP failure -> exit 1.
# --------------------------------------------------------------------------
# Does the Codex sandbox config $2 grant a writable root that CONTAINS $1?
#
# Why this exists: `deploy/bootstrap.sh` only writes ~/.codex/config.toml when
# it is ABSENT ("your edits survive"), so narrowing the heredoc does nothing on
# a box that already has the wide config -- which is precisely where `security`
# measured the vulnerability. The existing-config branch therefore has to ask
# this question of the file already on disk, and WARN. It must not rewrite it.
#
# Containment, not string prefix: `~/.configuration` does not contain
# `~/.config/omnigent`, and a naive prefix test would report a grant that does
# not exist. A warning that cries wolf is a warning operators learn to skip.
#
#   $1  absolute path to test (the attestation, or any file that buys silence)
#   $2  path to a config.toml
#   exit 0 = GRANTED (bad for the attestation); prints the covering root
#   exit 1 = not granted
#   exit 2 = no config, or no writable_roots in it (cannot answer)
nightshift_sandbox_grants_path() {
  local target="$1" config="$2" line root granted=1
  [ -f "$config" ] || return 2
  line=$(sed -n 's/^[[:space:]]*writable_roots[[:space:]]*=[[:space:]]*\[\(.*\)\].*/\1/p' \
         "$config" | head -1)
  [ -n "${line:-}" ] || return 2
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    # `~` is expanded by the CLI reading the config, not by the shell, since it
    # is inside quotes there. Expand it the same way here or every root misses.
    case "$root" in
      "~")   root="$HOME" ;;
      "~/"*) root="$HOME/${root#\~/}" ;;
    esac
    while [ "${root%/}" != "$root" ]; do root="${root%/}"; done
    [ -n "$root" ] || continue
    if [ "$target" = "$root" ] || [ "${target#"$root"/}" != "$target" ]; then
      printf '%s\n' "$root"
      granted=0
      break
    fi
  done <<EOF
$(printf '%s' "$line" | tr ',' '\n' \
  | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//')
EOF
  return "$granted"
}

nightshift_go_resolve_version() {
  local curl_cmd="${1:-curl}" tmp out rc=0
  tmp="$(mktemp)" || return 1
  "$curl_cmd" -fsSL 'https://go.dev/VERSION?m=text' -o "$tmp" || rc=$?
  if [ "$rc" -ne 0 ]; then rm -f "$tmp"; return 1; fi
  # Line 1 only, read from a FILE: no consumer can close a pipe on curl here.
  out="$(sed -n '1s/^go//p' "$tmp")"
  rm -f "$tmp"
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}

# nightshift_go_want_version [curl_cmd] [override]
#   The version provisioning should install: an explicit override wins,
#   otherwise go.dev. On failure it prints the LOUD diagnostic naming the
#   override variable and returns 1.
#
#   This exists as its own function because the pre-fix diagnostic was present
#   in the script and UNREACHABLE — `set -e` killed the substitution above it.
#   Keeping the failure message inside the tested unit is what makes
#   "fails loudly" an assertable property instead of a line of dead text.
nightshift_go_want_version() {
  local curl_cmd="${1:-curl}" override="${2-}" v
  [ -n "$override" ] || override="${NIGHTSHIFT_GO_VERSION:-}"
  if [ -n "$override" ]; then printf '%s\n' "$override"; return 0; fi
  if v="$(nightshift_go_resolve_version "$curl_cmd")" && [ -n "$v" ]; then
    printf '%s\n' "$v"; return 0
  fi
  echo "could not resolve the latest Go version from go.dev (network failure or unexpected response)." >&2
  echo "Pin it explicitly and re-run:  NIGHTSHIFT_GO_VERSION=x.y.z deploy/provision-ubuntu.sh" >&2
  return 1
}

# nightshift_go_installed_version [go_binary]
#   ABSENCE IS A NORMAL STATE. On a fresh box /usr/local/go/bin/go does not
#   exist; the pre-fix substitution returned non-zero and `set -e` exited before
#   the comparison and install branch, so the `${go_have:-0}` fallback was never
#   reached. Three outcomes, deliberately distinguishable:
#     exit 0, output ""       -> not installed; install it
#     exit 0, output "1.24.3" -> installed and readable
#     exit 1                  -> present but UNPARSEABLE; the install is invalid
#   No `|| true` anywhere: that would conceal a corrupt binary, which issue #8
#   explicitly forbids.
nightshift_go_installed_version() {
  local go_bin="${1:-/usr/local/go/bin/go}" raw ver
  [ -x "$go_bin" ] || return 0
  raw="$("$go_bin" version 2>/dev/null)" || return 1
  ver="$(printf '%s\n' "$raw" | awk '{print $3}' | sed 's/^go//')"
  case "$ver" in
    ''|*[!0-9.]*) return 1 ;;
  esac
  printf '%s\n' "$ver"
}

# nightshift_go_checksum <go_file> [curl_cmd]
#   sha256 go.dev publishes for exactly that filename. Site B of bug 1: the old
#   `curl | jq | head -1` has the same early-closing-consumer defect. `jq` reads
#   the fetched FILE, and `first(...)` replaces the trailing `head -1`.
#   A missing entry stays a HARD failure: installing an unverified toolchain is
#   the outcome this check exists to prevent.
nightshift_go_checksum() {
  local go_file="${1:?go filename required}" curl_cmd="${2:-curl}" tmp sha rc=0
  tmp="$(mktemp)" || return 1
  "$curl_cmd" -fsSL 'https://go.dev/dl/?mode=json&include=all' -o "$tmp" || rc=$?
  if [ "$rc" -ne 0 ]; then rm -f "$tmp"; return 1; fi
  sha="$(jq -r --arg f "$go_file" \
    'first(.[].files[]? | select(.filename == $f) | .sha256) // empty' \
    "$tmp" 2>/dev/null)" || { rm -f "$tmp"; return 1; }
  rm -f "$tmp"
  [ -n "$sha" ] || return 1
  printf '%s\n' "$sha"
}
