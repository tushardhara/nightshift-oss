#!/usr/bin/env bash
# Verify the box can actually run an unattended multi-day wave.
# Run before starting the program agent, and after any upgrade.
#   /srv/nightshift/deploy/preflight.sh [project-dir]

set -uo pipefail
PROJECT_DIR="${1:-/srv/project}"
TEAMS_DIR="${TEAMS_DIR:-/srv/nightshift}"
# Root-owned (deploy/install-root-config.sh). Overridable only so fixtures can
# point it at a temp file, like NIGHTSHIFT_RELEASES_ROOT below.
SERVER_ENV="${NIGHTSHIFT_SERVER_ENV:-/etc/nightshift/server.env}"
env_value() {
  sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*//p" \
    "$SERVER_ENV" 2>/dev/null | tail -1 | tr -d "\"'"
}
PATH="$HOME/.local/bin:$PATH"
DEPLOY_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/deploy-lib.sh
. "$DEPLOY_LIB/deploy-lib.sh" 2>/dev/null || true
fail=0

ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$1"; }
head_() { printf '\n%s\n' "$1"; }

# Provider only gates ADVICE WORDING (e.g. AWS instance-role guidance); no
# check depends on being on any particular cloud. Override: NIGHTSHIFT_PROVIDER.
PROVIDER="${NIGHTSHIFT_PROVIDER:-}"
if [ -z "$PROVIDER" ]; then
  case "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)" in
    *[Aa]mazon*) PROVIDER=aws ;;
    *)           PROVIDER=generic ;;
  esac
fi

head_ "runtime user"
# The runtime user is derived from the effective uid, NOT from $USER. This
# script runs under `set -u`, and the seats that matter most run it unattended
# (release, cron, systemd units) with no USER entry in the environment at all —
# `id -un` still answers correctly there, $USER aborts the script (#121).
# $USER/$LOGNAME remain as fallbacks only for the case where `id` is unavailable.
runtime_user="$(id -un 2>/dev/null || true)"
if [ -z "$runtime_user" ]; then
  runtime_user="${USER:-${LOGNAME:-uid $(id -u)}}"
fi
if [ "$(id -u)" -eq 0 ]; then
  bad "running as root — Nightshift (CLIs, services, Postgres access) must run as the non-root runtime user"
else
  # Note this is the SUCCESS branch. The root branch above never touched $USER,
  # so the old bug fired only when the box was otherwise correct.
  ok "non-root user: $runtime_user"
  # Non-root is worth nothing if every worker can `sudo -n` its way back.
  if command -v nightshift_passwordless_sudo >/dev/null 2>&1 \
     && nightshift_passwordless_sudo; then
    bad "runtime user $runtime_user can sudo without a password — every worker runs as this user, so that is root for any worker; as root, remove its NOPASSWD sudoers entry or run Nightshift as a dedicated user (deploy/providers/aws/README.md)"
  else
    ok "no passwordless sudo for $runtime_user"
  fi
fi

head_ "binaries (a missing one silently drops a worker from the roster)"
for b in omnigent claude codex node tmux git gh jq; do
  if command -v "$b" >/dev/null 2>&1; then ok "$b -> $(command -v "$b")"; else bad "$b missing"; fi
done
OMNIGENT_RUNTIME_LOCK="$TEAMS_DIR/deploy/omnigent-runtime.lock.json"
OMNIGENT_RUNTIME_IDENTITY="$TEAMS_DIR/deploy/omnigent-runtime-identity.py"
if command -v omnigent >/dev/null 2>&1; then
  OMNIPY_IDENTITY="$(dirname "$(readlink -f "$(command -v omnigent)")")/python"
  if [ -x "$OMNIPY_IDENTITY" ] && identity=$(
    "$OMNIPY_IDENTITY" "$OMNIGENT_RUNTIME_IDENTITY" \
      --lock "$OMNIGENT_RUNTIME_LOCK" verify 2>&1
  ); then
    ok "Omnigent runtime matches the reviewed Git lock"
  else
    bad "Omnigent runtime identity mismatch: ${identity:-verification could not run}"
  fi
fi

head_ "control-plane version drift (a stale install silently removes subcommands)"
# Today's failure mode, in one check. On 2026-09-01 this host carried FOUR
# copies of nightshift-ctl at once -- 1073, 2906, 4114 and 6147 lines -- and
# nothing compared them. `authority` and `bookkeeping` were absent from the
# copy agents actually ran, so ADR-0044's Program fence and the #286/#388 fast
# path could not execute however correct `main` was. See issue #495.
#
# Two assertions, and the FIRST one is why this check exists at all:
#   1. Resolve nightshift-ctl the way a WORKER does, not the way this script
#      does. Line 9 prepends $HOME/.local/bin, which on that host symlinked to
#      the OLDEST copy on the box. A login shell and a worker disagreed by ~5000
#      lines, which is precisely how the drift survived every manual check.
#   2. Every release SHA the box references -- running process, systemd units,
#      deployed policy -- must be the SAME release. Internal consistency, not
#      "matches main": main is legitimately ahead between deploys.
# Overridable so the drift checks below are fixture-testable rather than
# fixture-exempt; nothing but the tests ever sets it.
releases_root="${NIGHTSHIFT_RELEASES_ROOT:-/srv/nightshift-releases}"
release_re="$releases_root/[0-9a-f]{40}"
running_release=$(ps -eo args 2>/dev/null | grep -oE "$release_re" | head -1)
if [ -z "$running_release" ]; then
  warn "no omnigent server running from a release checkout — drift checks skipped (start the run, then re-run preflight)"
else
  ok "running release: $(basename "$running_release")"

  # (1) the binary a WORKER resolves, taken from a live worker's own environ
  worker_path=""
  for pid in $(pgrep -f 'local/bin/claude|local/bin/codex|omnigent.runner' 2>/dev/null | head -5); do
    p=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^PATH=//p' | head -1)
    [ -n "$p" ] && { worker_path="$p"; break; }
  done
  release_ctl="$running_release/checkout/bin/nightshift-ctl"
  if [ -z "$worker_path" ]; then
    warn "no live worker to read PATH from — falling back to the release checkout"
    worker_ctl="$release_ctl"
  else
    worker_ctl=""
    IFS=: read -r -a _dirs <<< "$worker_path"
    for d in "${_dirs[@]}"; do
      [ -x "$d/nightshift-ctl" ] && { worker_ctl="$d/nightshift-ctl"; break; }
    done
  fi
  if [ -z "$worker_ctl" ]; then
    bad "workers have no nightshift-ctl on PATH — every control-plane call fails closed"
  elif [ ! -e "$release_ctl" ]; then
    bad "running release has no bin/nightshift-ctl: $release_ctl"
  elif ! cmp -s "$worker_ctl" "$release_ctl"; then
    worker_lines=$(wc -l < "$worker_ctl")
    release_lines=$(wc -l < "$release_ctl")
    bad "workers resolve a DIFFERENT nightshift-ctl than the running release: $worker_ctl at $worker_lines lines vs $release_ctl at $release_lines lines"
  else
    ok "workers resolve the running release nightshift-ctl, $(wc -l < "$worker_ctl") lines"
  fi

  # this script's own PATH is not a worker's; say so when they differ
  own_ctl=$(command -v nightshift-ctl 2>/dev/null)
  if [ -n "$own_ctl" ] && [ -n "$worker_ctl" ] && ! cmp -s "$own_ctl" "$worker_ctl"; then
    warn "this shell resolves a different nightshift-ctl than workers do ($own_ctl) — trust the worker path above, not this one"
  fi

  # (2) capability floor: every verb the charters call, that the CLI can
  #     actually define, must exist in the installed copy. Candidate verbs are
  #     scraped from prose, so they are INTERSECTED with the subcommand list a
  #     current CLI advertises -- otherwise "nightshift-ctl always ..." in a
  #     sentence is reported as a missing subcommand.
  advertised() { python3 "$1" --help 2>/dev/null \
    | grep -oE '\{[a-z0-9,_-]+\}' | head -1 | tr -d '{}' | tr ',' '\n'; }
  if [ -n "$worker_ctl" ]; then
    reference_ctl="$PROJECT_DIR/bin/nightshift-ctl"
    [ -e "$reference_ctl" ] || reference_ctl="$worker_ctl"
    known=$(advertised "$reference_ctl")
    have=$(advertised "$worker_ctl")
    missing=""
    charter_root="$running_release/checkout"
    for verb in $(grep -rhoE 'nightshift-ctl [a-z][a-z-]+' "$charter_root/program" "$charter_root/AGENTS.md" 2>/dev/null \
                    | awk '{print $2}' | sort -u); do
      printf '%s\n' "$known" | grep -qx "$verb" || continue      # not a real subcommand -- prose
      printf '%s\n' "$have"  | grep -qx "$verb" && continue      # present, fine
      missing="$missing $verb"
    done
    if [ -n "$missing" ]; then
      bad "installed nightshift-ctl is missing subcommands the charters call:$missing"
    else
      ok "installed nightshift-ctl exposes every subcommand the charters call"
    fi
  fi

  # (3) no systemd unit may pin a different release than the one running
  unit_drift=""
  for u in "$HOME"/.config/systemd/user/*.service; do
    [ -e "$u" ] || continue
    pinned=$(grep -hoE "$release_re" "$u" 2>/dev/null | sort -u)
    for p in $pinned; do
      [ "$p" = "$running_release" ] || unit_drift="$unit_drift $(basename "$u")->$(basename "$p" | cut -c1-8)"
    done
  done
  if [ -n "$unit_drift" ]; then
    bad "systemd units pin a release the server is not running — a restart silently changes capability:$unit_drift"
  else
    ok "every systemd unit pins the running release"
  fi

  # (4) the deployed policy must come from that same checkout
  live_policy="$TEAMS_DIR/deploy/policies/omnipods_policies.py"
  rel_policy="$running_release/checkout/deploy/policies/omnipods_policies.py"
  if [ -e "$live_policy" ] && [ -e "$rel_policy" ] && ! cmp -s "$live_policy" "$rel_policy"; then
    bad "deployed policy differs from the running release's copy — dispatch admission may not match the reviewed code"
  elif [ -e "$rel_policy" ]; then
    ok "deployed policy matches the running release"
  fi
fi
# bubblewrap is deliberately NOT in the loop above. It is the one binary whose
# presence proves nothing: on stock Ubuntu 24.04 it installs fine and cannot
# run, and a box in exactly that state used to pass this check clean while
# every Codex seat died at its first tool call (DoD condition 5, issue #12).
# Probed by FUNCTION, and a failure is a FAIL, not a warn.
# NIGHTSHIFT_BWRAP overrides the binary, same convention as NIGHTSHIFT_PROVIDER.
if command -v nightshift_bwrap_probe >/dev/null 2>&1; then
  bwrap_code=0
  nightshift_bwrap_probe "${NIGHTSHIFT_BWRAP:-bwrap}" || bwrap_code=$?
  if [ "$bwrap_code" -eq 0 ]; then
    ok "$(nightshift_bwrap_diagnosis 0)"
  else
    bad "$(nightshift_bwrap_diagnosis "$bwrap_code")"
  fi
else
  bad "deploy/lib/deploy-lib.sh did not load - the bubblewrap functional check could not run"
fi
# Opt-in (NIGHTSHIFT_RTK=1): strips tool-output tokens for Claude workers, so
# its absence is only worth a word when the operator asked for it.
if command -v rtk >/dev/null 2>&1; then
  ok "rtk -> $(command -v rtk) (token filter hooked for Claude workers)"
elif [ "${NIGHTSHIFT_RTK:-0}" = 1 ]; then
  warn "rtk missing — Claude workers pay full token price for reads/tests/diffs (optional; https://www.rtk-ai.app/)"
fi

# Language toolchains: WARN, not FAIL. A box provisioned before these landed
# still runs the Program fine — it just cannot build Go/Rust or exercise cloud
# code locally, and the agent prompts promise workers that it can. Fix by
# re-running deploy/provision-ubuntu.sh as root (idempotent).
head_ "toolchains workers are told they have (warn only — re-run provisioning to fix)"
for b in go cargo rustc floci; do
  if command -v "$b" >/dev/null 2>&1; then
    ok "$b -> $(command -v "$b")"
  else
    case "$b" in
      floci) warn "floci missing — QA/eng prompts promise local AWS/Azure/GCP/OCI emulators; cloud code falls back to mocks" ;;
      *)     warn "$b missing — workers cannot build $b code (deploy/provision-ubuntu.sh installs it)" ;;
    esac
  fi
done
# The Codex sandbox must be able to write toolchain state that lives OUTSIDE the
# worktree, or a Rust/Go build fails mid-task with a confusing permission error.
if [ -f "$HOME/.codex/config.toml" ]; then
  missing=""
  for root in .cargo .rustup go .floci; do
    grep -q "~/$root" "$HOME/.codex/config.toml" || missing="$missing ~/$root"
  done
  [ -z "$missing" ] \
    && ok "codex writable_roots cover the toolchain caches" \
    || warn "codex writable_roots missing:$missing — add them to ~/.codex/config.toml"
fi

head_ "control plane (the deterministic side: slots, claims, leases, checkpoints)"
if command -v nightshift-ctl >/dev/null 2>&1; then
  ok "nightshift-ctl -> $(command -v nightshift-ctl)"
  if nightshift-ctl --db /tmp/nightshift-ctl-preflight.db selftest >/dev/null 2>&1; then
    ok "nightshift-ctl selftest (WAL + partial unique indexes enforcing)"
  else
    bad "nightshift-ctl selftest failed — SQLite too old or filesystem refuses WAL"
  fi
  rm -f /tmp/nightshift-ctl-preflight.db*
else
  bad "nightshift-ctl not on PATH — worker semaphore, claims and leases are all prompt-only (re-run bootstrap.sh)"
fi
if [ -f "$PROJECT_DIR/.omni/control.db" ]; then
  ok ".omni/control.db initialised"
  if python3 - "$PROJECT_DIR/.omni/control.db" <<'PY' >/dev/null 2>&1
import sqlite3, sys
cx = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
cx.row_factory = sqlite3.Row
tables = {row[0] for row in cx.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
columns = {row[1] for row in cx.execute("PRAGMA table_info(worker_slots)")}
if "task_admissions" not in tables or "phase" not in columns:
    raise SystemExit(1)
legacy = cx.execute(
    "SELECT COUNT(*) FROM worker_slots WHERE state='active' AND phase IS NULL"
).fetchone()[0]
unfinished = cx.execute(
    "SELECT COUNT(*) FROM tasks t WHERE (state NOT IN "
    "('pending','integrated','done','merged','dropped','superseded') "
    "OR (state='pending' AND pr_url IS NOT NULL AND pr_url<>'')) "
    "AND NOT EXISTS (SELECT 1 FROM task_admissions a WHERE a.task_id=t.id)"
).fetchone()[0]
raise SystemExit(0 if legacy == 0 and unfinished == 0 else 1)
PY
  then
    ok "ADR-0033 schema present; no active legacy slots or unfinished unadmitted tasks"
  else
    bad "worker admission cutover needs migration/quiescence or reviewed legacy reconciliation"
  fi
else
  bad ".omni/control.db missing — the semaphore FAILS CLOSED, so EVERY worker dispatch will be DENIED (run bin/nightshift-init.sh)"
fi
# Every *_FAIL_OPEN switch (semaphore, authority, any added later) turns a
# fence into an ALLOW, so none may be set anywhere the server can inherit it
# from: this shell, server.env, the user units and their drop-ins, the user
# manager's environment, or the server process already running.
fail_open_sources=("$SERVER_ENV" "$HOME/.config/omnigent/server.env")
for u in omnigent-server omnigent-host; do
  fail_open_sources+=("$HOME/.config/systemd/user/$u.service")
  for d in "$HOME/.config/systemd/user/$u.service.d"/*.conf; do
    [ -e "$d" ] && fail_open_sources+=("$d")
  done
done
fail_open_live=$(
  for n in $(compgen -e); do
    case "$n" in (*_FAIL_OPEN) printf 'environment: %s=%s\n' "$n" "${!n}" ;; esac
  done
  systemctl --user show-environment 2>/dev/null | sed 's/^/user manager: /'
  for pid in $(pgrep -f 'omnigent server' 2>/dev/null); do
    tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed "s/^/server pid $pid: /"
  done
)
fail_open_hits=$(FAIL_OPEN_LIVE="$fail_open_live" python3 - "${fail_open_sources[@]}" <<'PY' 2>/dev/null
import os, re, sys
pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*_FAIL_OPEN)\s*=\s*(['\"]?)([^\s'\"#]*)\2")
hits = []
def scan(where, lines):
    for line in lines:
        for name, _quote, value in pattern.findall(line.split("#", 1)[0]):
            if value == "1":
                hits.append(f"{name}=1 ({where})")
for line in os.environ.get("FAIL_OPEN_LIVE", "").splitlines():
    where, _, rest = line.partition(": ")
    scan(where, [rest])
for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            scan(path, handle.read().splitlines())
    except OSError:
        pass
print("\n".join(hits))
raise SystemExit(0 if hits else 1)
PY
)
if [ -n "$fail_open_hits" ]; then
  fail_open_hits=$(printf '%s\n' "$fail_open_hits" | sort -u | tr '\n' ';')
  bad "fail-open escape hatch set: ${fail_open_hits%;} — every *_FAIL_OPEN is forbidden for a managed run"
else
  ok "no *_FAIL_OPEN escape hatch set (admission, authority and merge fences fail closed)"
fi
# The custom policies must import inside omnigent's own interpreter, or the
# server refuses to start the bundle (fail-loud at boot, by design).
OMNIPY_CP="$(dirname "$(readlink -f "$(command -v omnigent)")")/python"
[ -x "$OMNIPY_CP" ] || OMNIPY_CP=python3
if PYTHONPATH="$TEAMS_DIR/deploy/policies" "$OMNIPY_CP" -c \
     "from omnipods_policies import external_repo_merge_fence" 2>/dev/null; then
  ok "custom policies import (worker_semaphore, pr_close_keyword_guard, external_repo_merge_fence)"
else
  bad "omnipods_policies does not import in omnigent's python — the bundle will fail at spec load"
fi
if grep -q '^[[:space:]]*worker_semaphore:' "$TEAMS_DIR/program/config.yaml" \
  && ! grep -q '^[[:space:]]*worker_semaphore:' \
       "$TEAMS_DIR/program/agents/crew_alpha/config.yaml" \
       "$TEAMS_DIR/program/agents/crew_beta/config.yaml" 2>/dev/null \
  && PYTHONPATH="$TEAMS_DIR/deploy/policies" "$OMNIPY_CP" - \
       "$PROJECT_DIR/.omni/control.db" <<'PY' >/dev/null 2>&1
import sys
from omnipods_policies import worker_semaphore

gate = worker_semaphore(db=sys.argv[1])
for agent, crew in (("eng_claude", "crew_alpha"),
                    ("beta_eng_claude", "crew_beta")):
    event = {"type": "tool_call", "data": {
        "name": "sys_session_send",
        "arguments": {"agent": agent, "title": "managed-preflight",
                      "args": {"task_id": "@managed-preflight",
                               "purpose": "implement", "slot_id": 2147483647,
                               "generation": 1, "crew": crew}},
    }}
    if gate(event).get("result") != "DENY":
        raise SystemExit(1)
PY
then
  ok "root worker_semaphore executed fail-closed for crew_alpha and crew_beta"
else
  bad "root worker_semaphore is absent, duplicated, or did not deny nested crew probes"
fi
grep -q '^PYTHONPATH=' "$SERVER_ENV" 2>/dev/null \
  && ok "PYTHONPATH in server.env (policies reachable from systemd)" \
  || bad "server.env has no PYTHONPATH — systemd-started server cannot load the custom policies (as root: deploy/install-root-config.sh)"
# The URI becomes `--database-uri` on argv, which every local user can read.
db_password_re='^[A-Za-z][A-Za-z0-9+.-]*://[^/@]*:[^/@]*@|[?&]password='
db_uri=$(env_value OMNIGENT_DATABASE_URI)
if printf '%s\n' "$db_uri" | grep -qE "$db_password_re"; then
  bad "OMNIGENT_DATABASE_URI in server.env carries the database password — it becomes the server's argv, readable by every local user through ps; move the password to a PGPASSFILE (re-run bootstrap.sh)"
fi
if ps -eo args 2>/dev/null | grep -oE -- '--database-uri[ =][^ ]+' | sed -E 's/^--database-uri[ =]//' \
   | grep -qE "$db_password_re"; then
  bad "a running process has the database password in its --database-uri argument — restart it from a server.env whose URI has no password"
fi

head_ "fence configuration (root-owned; workers run as $runtime_user)"
# server.env, the policy modules, the agent bundle and the authority
# registries decide what the fences allow. Anything the runtime user can
# write, a worker can rewrite, and the next restart loads it (audit C6).
if [ ! -f "$SERVER_ENV" ]; then
  bad "server environment $SERVER_ENV missing — omnigent-server will not start; as root run deploy/install-root-config.sh --user $runtime_user (bootstrap prints the full line)"
fi
anchor_args=("$SERVER_ENV")
policy_path=$(env_value PYTHONPATH); policy_path="${policy_path%%:*}"
[ -n "$policy_path" ] && anchor_args+=(--tree "$policy_path")
if [ -n "$running_release" ]; then
  anchor_args+=(--tree "$running_release/checkout")
else
  anchor_args+=(--tree "$TEAMS_DIR/program")
fi
registry_root=$(env_value NIGHTSHIFT_STATE_ROOT); registry_id=$(env_value NIGHTSHIFT_REPOSITORY_ID)
[ -n "$registry_root" ] && [ -n "$registry_id" ] \
  && anchor_args+=(--tree "$registry_root/authority/$registry_id")
anchor_findings=$(python3 "$DEPLOY_LIB/trust_anchors.py" audit --uid "$(id -u)" "${anchor_args[@]}" 2>&1)
if [ -n "$anchor_findings" ]; then
  anchor_count=$(printf '%s\n' "$anchor_findings" | wc -l | tr -d ' ')
  anchor_first=$(printf '%s\n' "$anchor_findings" | head -1)
  bad "$anchor_count fence-configuration path(s) writable by $runtime_user, first: $anchor_first — a worker can change what the next restart loads; as root run deploy/install-root-config.sh (--own-bundle), or run a root-sealed release"
else
  ok "server.env, policies, bundle and registry are not writable by $runtime_user"
fi
unit_problems=""
for u in omnigent-server omnigent-host; do
  unit="$HOME/.config/systemd/user/$u.service"
  [ -e "$unit" ] || continue
  [ "$(sed -n 's/^EnvironmentFile=//p' "$unit")" = "$SERVER_ENV" ] \
    || unit_problems="$unit_problems $u:EnvironmentFile"
  [ -e "$unit.d" ] && unit_problems="$unit_problems $u:drop-in"
done
if [ -n "$unit_problems" ]; then
  bad "user units load configuration other than $SERVER_ENV:$unit_problems — the runtime user can edit those; re-run bootstrap.sh and delete the drop-in directory"
fi
[ -f "$HOME/.config/omnigent/server.env" ] \
  && warn "~/.config/omnigent/server.env is no longer read — move any custom line into $SERVER_ENV by hand, then delete it (it may hold the database password)"
if [ -f "$HOME/.codex/config.toml" ] && grep -q '^approval_policy *= *"never"' "$HOME/.codex/config.toml"; then
  ok "codex config: approval_policy=never (yolo:false workers cannot stall)"
else
  bad "~/.codex/config.toml missing approval_policy=\"never\" — Codex workers run yolo:false and WILL stall on their first approval (re-run bootstrap.sh)"
fi
grep -q '^sandbox_mode *= *"workspace-write"' "$HOME/.codex/config.toml" 2>/dev/null \
  && ok "codex config: workspace-write sandbox" \
  || warn "codex sandbox not workspace-write — workers run with whatever the CLI defaults to"
# Static cloud credentials are readable by workers (docs/TRUST-BOUNDARY.md);
# nothing in Nightshift itself needs cloud keys — state backup rides git.
if [ -f "$HOME/.aws/credentials" ]; then
  if [ "$PROVIDER" = aws ]; then
    warn "~/.aws/credentials exists — static AWS keys are readable by workers; use an instance role if the project needs AWS (docs/TRUST-BOUNDARY.md)"
  else
    warn "~/.aws/credentials exists — static cloud keys are readable by workers; remove them unless the project itself needs them (docs/TRUST-BOUNDARY.md)"
  fi
else
  ok "no static cloud credentials on the box"
fi
# An instance role is no better than static keys if IMDS hands it out: every
# worker is a host process, so IMDSv2's hop limit (which stops containers)
# does not stop them. IMDSv1 needs not even a token.
if [ "$PROVIDER" = aws ]; then
  imds=http://169.254.169.254/latest
  if curl -sf -m 2 -o /dev/null "$imds/meta-data/" 2>/dev/null; then
    bad "IMDSv1 answers on this instance — any worker can read instance-role credentials with one unauthenticated GET; require IMDSv2 with hop limit 1 (deploy/providers/aws/README.md)"
  fi
  imds_token=$(curl -sf -m 2 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' "$imds/api/token" 2>/dev/null)
  imds_role=""
  [ -n "$imds_token" ] && imds_role=$(curl -sf -m 2 -H "X-aws-ec2-metadata-token: $imds_token" \
    "$imds/meta-data/iam/security-credentials/" 2>/dev/null | head -1)
  if [ -n "$imds_role" ]; then
    warn "instance role $imds_role is served by IMDS — every worker can read its credentials; scope it to exactly what the project needs, or detach it and disable IMDS (deploy/providers/aws/README.md)"
  elif [ -n "$imds_token" ]; then
    ok "IMDS answers but serves no instance role (disable it entirely if nothing needs it)"
  else
    ok "IMDS disabled or unreachable from the runtime user"
  fi
fi

head_ "credentials"
if omnigent config list 2>/dev/null | grep -qi 'claude'; then
  omnigent config list 2>/dev/null | sed -n '/Credentials/,$p' | sed 's/^/    /'
else
  bad "omnigent config list shows no credentials"
fi
# Cross-vendor review needs BOTH vendors; one vendor means no independent review.
creds=$(omnigent config list 2>/dev/null)
echo "$creds" | grep -A1 -i '^  Claude' | grep -qi 'subscription\|api\|gateway' && ok "claude credential present" || bad "no claude credential"
echo "$creds" | grep -A1 -i '^  Codex'  | grep -qi 'subscription\|api\|gateway' && ok "codex credential present"  || bad "no codex credential (cross-vendor review impossible)"

head_ "github (crews open PRs; only the integrator merges)"
if gh auth status >/dev/null 2>&1; then
  ok "gh authenticated"
  if [ -d "$PROJECT_DIR/.git" ]; then
    repo=$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null)
    [ -n "$repo" ] && ok "origin: $repo" || bad "no origin remote in $PROJECT_DIR"
    branch=$(git -C "$PROJECT_DIR" symbolic-ref --short HEAD 2>/dev/null)
    ok "default branch checked out: ${branch:-unknown}"
    # Full autonomy: `release` merges with no human approver, so protection that
    # REQUIRES a human review will deadlock every merge. Protection that only
    # requires status checks is ideal — it blocks a red merge without needing a person.
    prot=$(gh api "repos/{owner}/{repo}/branches/${branch}/protection" 2>/dev/null || true)
    if [ -z "$prot" ]; then
      warn "no branch protection on ${branch} — required status checks would stop a red merge without needing a human. Consider enabling just that."
    elif printf '%s' "$prot" | grep -q '"required_approving_review_count": *[1-9]'; then
      bad "branch protection requires human approvals — the integrator has no approver and every merge will deadlock. Drop required reviews, keep required status checks."
    else
      ok "branch protection present without required human approvals"
    fi
    if gh api "repos/{owner}/{repo}" --jq '.permissions.push' 2>/dev/null | grep -q true; then
      ok "token can push (integrator can merge)"
    else
      bad "token cannot push — the integrator cannot merge anything"
    fi
  else
    bad "$PROJECT_DIR is not a git repo"
  fi
else
  bad "gh not authenticated (agents cannot open PRs)"
fi

head_ "tracker (GitHub Issues + Projects)"
# Scopes line only — the whole output can contain "project" in a repo name.
if gh auth status 2>&1 | grep -i "token scopes" | grep -qw "project"; then
  ok "gh token has the 'project' scope"
else
  bad "gh token lacks 'project' scope — run: gh auth refresh -s project, then bin/nightshift-tracker-init.sh"
fi
if [ -f "$PROJECT_DIR/.omni/tracker.json" ]; then
  pnum=$(jq -r '.project_number // empty' "$PROJECT_DIR/.omni/tracker.json" 2>/dev/null)
  [ -n "$pnum" ] && ok "project board #$pnum configured" || bad "tracker.json has no project_number"
  # grep -c already prints 0 on no match (with exit 1) — `|| echo 0` would emit a second line
  labels=$(gh label list --limit 200 --json name --jq '.[].name' 2>/dev/null | grep -c '^state:ready$' || true)
  [ "$labels" -ge 1 ] && ok "labels bootstrapped" || bad "labels missing — run bin/nightshift-tracker-init.sh"
  gh issue list --search "Program status in:title" --state open --json number --jq '.[0].number' >/dev/null 2>&1 \
    && ok "status issue present (reports land there)" || warn "no pinned 'Program status' issue — reports will only hit files"
else
  bad ".omni/tracker.json missing — run bin/nightshift-tracker-init.sh in the repo"
fi

head_ "project scaffolding"
for f in .omni/brief.md .omni/ledger.md .omni/capacity.json docs/owners.md docs/decisions; do
  [ -e "$PROJECT_DIR/$f" ] && ok "$f" || bad "$f missing (run bin/nightshift-init.sh)"
done
# The BRIEF is the human input; the goal is generated by discovery.
if [ -f "$PROJECT_DIR/.omni/brief.md" ]; then
  if grep -q '^<!-- TEMPLATE' "$PROJECT_DIR/.omni/brief.md"; then
    bad "brief.md is still the untouched template — write your brief and delete the TEMPLATE line"
  else
    words=$(grep -vE '^\s*(#|<!--|-->|$)' "$PROJECT_DIR/.omni/brief.md" | wc -w | tr -d ' ')
    if [ "${words:-0}" -ge 25 ]; then
      ok "brief.md written (${words} words)"
    else
      bad "brief.md has only ${words} words of actual content — discovery needs something to work with"
    fi
  fi
fi
if [ -f "$PROJECT_DIR/docs/prd/DEFINITION-OF-DONE.md" ]; then
  n=$(grep -cE '^[0-9]+\.' "$PROJECT_DIR/docs/prd/DEFINITION-OF-DONE.md" 2>/dev/null || true)
  ok "definition of done exists — $n conditions (discovery has run)"
else
  warn "no definition of done yet — the program's first act is the discovery skill"
fi
# Either location counts. `nightshift-init.sh` writes to .git/info/exclude
# (never .gitignore, which is tracked and belongs to the repository's owner --
# #744), while repositories initialised before that change, and this one, carry
# the rule in .gitignore. Checking only one of them warns about a correctly
# configured repository.
if grep -q '^\.worktrees/' "$PROJECT_DIR/.gitignore" 2>/dev/null \
   || grep -q '^\.worktrees/' "$PROJECT_DIR/.git/info/exclude" 2>/dev/null; then
  ok ".worktrees excluded from git"
else
  warn ".worktrees not excluded (neither .gitignore nor .git/info/exclude)"
fi

head_ "agent bundle"
# omnigent is installed in an isolated uv venv, so its package is NOT importable
# from the system python3 — parse with the interpreter that ships beside the
# omnigent binary itself.
OMNIPY="$(dirname "$(readlink -f "$(command -v omnigent)")")/python"
[ -x "$OMNIPY" ] || OMNIPY=python3
if "$OMNIPY" - "$TEAMS_DIR" <<'PY'
import sys, pathlib
from omnigent.spec import parser
root = pathlib.Path(sys.argv[1])/"program"
spec = parser.parse(root)
crews = [t.name for t in spec.sub_agents]
print(f"    program -> {crews}")
for t in spec.sub_agents:
    print(f"    {t.name} -> {[w.name for w in t.sub_agents]}")
PY
then ok "agent specs parse"; else bad "agent specs do not parse"; fi

head_ "services"
for u in omnigent-server omnigent-host; do
  state=$(systemctl --user is-active "$u" 2>/dev/null)
  [ "$state" = active ] && ok "$u active" || warn "$u $state"
  # enabled is what survives a reboot; active only survives until one
  if systemctl --user is-enabled "$u" >/dev/null 2>&1; then
    ok "$u enabled (starts at boot)"
  else
    bad "$u not enabled — a reboot silently ends the run (systemctl --user enable $u)"
  fi
done
if systemctl --user is-active nightshift-state-backup.timer >/dev/null 2>&1; then
  ok "state-backup timer active (.omni + docs -> omni/state every 30 min)"
else
  warn "state-backup timer not running — losing this host's disk then loses .omni state since the last merge"
fi
if systemctl --user is-active nightshift-watchdog.timer >/dev/null 2>&1; then
  ok "watchdog timer active (alerts the phone if the heartbeat goes silent)"
else
  warn "watchdog timer not running — a lost heartbeat task ends the run SILENTLY (systemctl --user enable --now nightshift-watchdog.timer)"
fi
# The snapshot carries the control dump and incidents, so it goes only to an
# explicitly configured remote, and a GitHub one must be private (audit B4).
backup_url=$(git -C "$PROJECT_DIR" config --get remote.nightshift-state.url 2>/dev/null)
backup_slug=$(nightshift_github_slug "$backup_url" 2>/dev/null)
if [ -z "$backup_url" ]; then
  warn "no nightshift-state remote — .omni snapshots stay on this disk; add one that points at a PRIVATE repository (deploy/README.md, Machine or disk loss)"
elif [ -n "$backup_slug" ] && [ "$(nightshift_repo_visibility "$backup_slug" 2>/dev/null)" != private ]; then
  bad "state-backup remote $backup_slug is not a private repository — the snapshot carries the control dump and incidents; point nightshift-state at a private repository"
elif git -C "$PROJECT_DIR" ls-remote --exit-code nightshift-state refs/heads/omni/state >/dev/null 2>&1; then
  ok "omni/state exists on the private nightshift-state remote (off-box state copy confirmed)"
else
  warn "no omni/state branch on the nightshift-state remote yet — run deploy/nightshift-state-backup.sh once to verify the push works"
fi
curl -sf http://127.0.0.1:6767/health >/dev/null 2>&1 && ok "server /health responding" || warn "server not responding on :6767"
docker compose -f "$HOME/omnigent-db/docker-compose.yml" ps 2>/dev/null | grep -q 'healthy\|running' \
  && ok "postgres up" || warn "postgres not running"
loginctl show-user "$runtime_user" 2>/dev/null | grep -q 'Linger=yes' \
  && ok "linger enabled (services survive logout)" || bad "linger off — services die when your SSH session ends"

head_ "crews and capacity"
crews=$(ls -1 "$TEAMS_DIR/program/agents" 2>/dev/null | grep -c '^crew_' || true)
crews=${crews:-0}
budget=$(jq -r '.max_concurrent_workers // empty' "$PROJECT_DIR/.omni/capacity.json" 2>/dev/null)
[ "${crews:-0}" -ge 1 ] && ok "${crews} crew(s) configured" || bad "no crews found in the bundle"
[ "${crews:-0}" -ge 2 ] || warn "only one crew — no cross-crew review is possible; say so in STATUS.md"
if [ -n "${budget:-}" ]; then
  ok "global worker budget: ${budget} (shared by all ${crews} crews, not per crew)"
  [ "$budget" -le 6 ] || warn "budget ${budget} is high for two subscriptions; expect rate limits"
else
  bad ".omni/capacity.json has no max_concurrent_workers"
fi

head_ "host capacity for a multi-day run"
mem=$(free -g | awk '/^Mem:/{print $2}')
[ "${mem:-0}" -ge 15 ] && ok "${mem}G RAM" || warn "${mem}G RAM — 4 concurrent native workers wants 16G"
# The host cgroup budget is derived from RAM at bootstrap; a unit sized for a
# different box (image cloned, RAM resized) wastes or overcommits memory.
HOST_UNIT="$HOME/.config/systemd/user/omnigent-host.service"
if [ -f "$HOST_UNIT" ] && command -v nightshift_memory_limits >/dev/null 2>&1; then
  unit_max=$(sed -n 's/^MemoryMax=//p' "$HOST_UNIT")
  if [ "$unit_max" = "__MEMORY_MAX__" ]; then
    bad "omnigent-host.service still has the __MEMORY_MAX__ placeholder — re-run bootstrap.sh"
  else
    total_gb=$(nightshift_mem_total_gb 2>/dev/null)
    expect="$(nightshift_memory_limits "${total_gb:-0}" 2>/dev/null | awk '{print $2}')G"
    if [ -n "${total_gb:-}" ] && [ "$unit_max" != "$expect" ]; then
      warn "omnigent-host MemoryMax=$unit_max but this ${total_gb}G box derives $expect — re-run bootstrap.sh (or keep your NIGHTSHIFT_MEMORY_MAX override)"
    else
      ok "omnigent-host MemoryMax=$unit_max matches ${total_gb:-?}G RAM"
    fi
  fi
fi
disk=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
[ "${disk:-0}" -ge 40 ] && ok "${disk}G free disk" || bad "${disk}G free — worktrees and node_modules will fill this"
swap=$(free -g | awk '/^Swap:/{print $2}')
[ "${swap:-0}" -ge 1 ] || warn "no swap: an OOM kill takes a worker mid-task"

head_ "exposure (public internet must never reach 6767 or Postgres)"
if ss -ltn 2>/dev/null | grep -q '0.0.0.0:6767\|\[::\]:6767'; then
  bad "server bound to a public interface — it holds your subscription credentials"
else
  ok "server not listening on a public interface"
fi
if ss -ltn 2>/dev/null | grep -E '0\.0\.0\.0:(5432|5433)|\[::\]:(5432|5433)' -q; then
  bad "Postgres bound to a public interface — must stay 127.0.0.1 (docker-compose.postgres.yml pins it)"
else
  ok "postgres not listening on a public interface"
fi
# floci's emulators answer unauthenticated cloud API calls, so a public bind
# hands the internet a writable AWS/Azure/GCP/OCI surface on this box. Same rule
# as 6767 and Postgres.
#
# Two checks, because catching this on the live listener is catching it too
# late: the daemon default is the control, the listener scan is the audit.
if nightshift_docker_bind_ok "$(cat /etc/docker/daemon.json 2>/dev/null)"; then
  ok "docker publishes to 127.0.0.1 by default (emulator ports cannot land public by accident)"
else
  bad "docker's default publish IP is not 127.0.0.1 — \`floci start\` will expose the emulator on 0.0.0.0 (re-run deploy/provision-ubuntu.sh)"
fi
if ss -ltn 2>/dev/null | grep -E '0\.0\.0\.0:(4566|4577|4588|4599)|\[::\]:(4566|4577|4588|4599)' -q; then
  bad "a floci emulator is bound to a public interface — emulators accept unauthenticated calls; bind 127.0.0.1"
else
  ok "cloud emulators not listening on a public interface"
fi

if command -v tailscale >/dev/null 2>&1; then
  ts_out=$(tailscale status 2>&1); ts_code=$?
  if [ "$ts_code" -eq 0 ]; then
    ok "tailscale up"
    # The UI path: the server binds 127.0.0.1 only; Tailscale Serve — set once
    # BY ROOT, persisted in tailscaled state by --bg — bridges the tailnet to
    # it. This user is deliberately NOT a tailscale operator (workers run as
    # this user and must not be able to reconfigure the tailnet, e.g. funnel),
    # so prefer READ-ONLY evidence, and treat an access denial on the config
    # read as expected — a skip/warn with the reason, never a FAIL, and never
    # advice to grant operator.
    ts_dns=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' 2>/dev/null | sed 's/\.$//')
    ts_ip=$(tailscale ip -4 2>/dev/null | head -1)
    # Serve is opt-in: :6767 is unauthenticated, so publishing it tailnet-wide
    # is a choice, and its absence is the safe default, not a finding.
    if [ "${NIGHTSHIFT_TAILSCALE_SERVE:-0}" != 1 ]; then
      ok "tailscale serve not expected (opt-in: NIGHTSHIFT_TAILSCALE_SERVE=1)"
    elif [ -n "$ts_dns" ] && curl -sf --max-time 5 "https://$ts_dns/health" -o /dev/null 2>/dev/null; then
      ok "serve end-to-end: https://$ts_dns/health answers (tailnet -> 127.0.0.1:6767)"
    elif [ -n "$ts_ip" ] && ss -ltn 2>/dev/null | grep -qF "$ts_ip:443"; then
      ok "serve listener on $ts_ip:443 (UI at https://${ts_dns:-<machine>.<tailnet>.ts.net}/ once the server is started)"
    else
      serve_out=$(tailscale serve status 2>&1); serve_code=$?
      verdict=error
      command -v nightshift_classify_serve >/dev/null 2>&1 \
        && verdict=$(nightshift_classify_serve "$serve_code" "$serve_out")
      case "$verdict" in
        configured)
          ok "tailscale serve -> 127.0.0.1:6767 (UI at https://${ts_dns:-<machine>.<tailnet>.ts.net}/)" ;;
        unconfigured)
          warn "tailscale serve not proxying 6767 — no private UI path; FROM A ROOT SHELL run: tailscale serve --bg 6767 (never 'funnel')" ;;
        denied)
          warn "cannot read serve config as $runtime_user (expected — this user is not a tailscale operator, by design) and no listener on ${ts_ip:-<ts-ip>}:443; verify from a root shell: tailscale serve status" ;;
        *)
          warn "could not determine serve config (tailscale serve status exit $serve_code) — verify from a root shell: tailscale serve status" ;;
      esac
    fi
    # First-provisioning SSH should be closed once Tailscale SSH works.
    if ss -ltn 2>/dev/null | grep -qE '0\.0\.0\.0:22|\[::\]:22'; then
      warn "public sshd still listening on :22 — after verifying Tailscale SSH, lock it down (deploy/README.md 'Security posture')"
    fi
  else
    verdict=error
    command -v nightshift_classify_serve >/dev/null 2>&1 \
      && verdict=$(nightshift_classify_serve "$ts_code" "$ts_out")
    if [ "$verdict" = denied ]; then
      warn "cannot query tailscaled as $runtime_user — read-only status is normally allowed; verify from a root shell: tailscale status"
    else
      warn "tailscale installed but down (as root: tailscale up --ssh)"
    fi
  fi
else
  warn "tailscale not installed — no private path for SSH or the UI (deploy/provision-ubuntu.sh installs it)"
fi

# --- omnigent local patches -------------------------------------------------
# uv tool upgrade replaces site-packages wholesale and erases these silently.
# The symptom is indistinguishable from the original bug, so check rather than
# assume. See deploy/patches/ for what each one does and why it is not upstream.
patch_script="$(dirname "$0")/patches/omnigent_session_send_args.py"
if [ -f "$patch_script" ]; then
  if python3 "$patch_script" --check >/dev/null 2>&1; then
    ok "omnigent sys_session_send dispatch-metadata patch applied"
  else
    bad "omnigent sys_session_send patch MISSING — counted-seat dispatch will fail with 'Additional properties are not allowed'. Fix: python3 deploy/patches/omnigent_session_send_args.py --apply, then restart BOTH the server and the omnigent host daemon"
  fi
else
  warn "deploy/patches/omnigent_session_send_args.py not found — cannot verify the dispatch-metadata patch"
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mpreflight clean\033[0m — safe to start the run\n'
else
  printf '\033[31m%d blocking problem(s)\033[0m — fix before starting a multi-day run\n' "$fail"
  exit 1
fi
