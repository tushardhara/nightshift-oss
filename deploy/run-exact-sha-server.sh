#!/bin/bash
# Launch one Omnigent server with every repo-owned runtime path derived from a
# sealed release root. This is invoked by nightshift-release; do not compose the
# environment by hand.
set -euo pipefail
export PATH=/usr/bin:/bin
unset GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_REPLACE_REF_BASE GIT_COMMON_DIR \
  GIT_INDEX_FILE
for git_config_name in ${!GIT_CONFIG@}; do
  unset "$git_config_name"
done
export GIT_NO_REPLACE_OBJECTS=1

usage() {
  echo "usage: $0 --release-root DIR --expected-sha SHA --project DIR --control-db FILE --server-env FILE --agent-root DIR --policy-dir DIR --server URL --omnigent-bin FILE" >&2
  exit 2
}

release_root= expected_sha= project= control_db= server_env= agent_root= policy_dir= server= omnigent_bin=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --release-root) release_root="${2:-}"; shift 2 ;;
    --expected-sha) expected_sha="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --control-db) control_db="${2:-}"; shift 2 ;;
    --server-env) server_env="${2:-}"; shift 2 ;;
    --agent-root) agent_root="${2:-}"; shift 2 ;;
    --policy-dir) policy_dir="${2:-}"; shift 2 ;;
    --server) server="${2:-}"; shift 2 ;;
    --omnigent-bin) omnigent_bin="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || usage
case "$server" in http://127.0.0.1:*) ;; *) usage ;; esac
server_port=${server##*:}
case "$server_port" in ''|*[!0-9]*) usage ;; esac
for value in "$release_root" "$project" "$control_db" "$server_env" "$agent_root" "$policy_dir" "$omnigent_bin"; do
  case "$value" in /*) ;; *) usage ;; esac
done
[ -d "$release_root/.git" ] || { echo "release root is not a Git checkout" >&2; exit 3; }
[ -d "$project" ] || { echo "project missing: $project" >&2; exit 3; }
[ -f "$control_db" ] && [ ! -L "$control_db" ] \
  || { echo "canonical control DB missing or symlinked: $control_db" >&2; exit 3; }
[ -f "$server_env" ] || { echo "server environment missing: $server_env" >&2; exit 3; }
[ -f "$agent_root/config.yaml" ] || { echo "agent root missing: $agent_root" >&2; exit 3; }
[ -f "$policy_dir/omnipods_policies.py" ] || { echo "policy module missing: $policy_dir" >&2; exit 3; }
[ -x "$omnigent_bin" ] || { echo "pinned Omnigent executable missing: $omnigent_bin" >&2; exit 3; }
# The environment file configures the fences, so it must be exactly as
# trusted as the sealed release: same owner (root, once sealed), and nobody
# else may write it or its directory (audit C6). Otherwise a worker edits it
# and the next legitimate restart loads the worker's values.
release_owner=$(stat -c %u -- "$release_root")
for anchor in "$server_env" "$(dirname -- "$server_env")"; do
  read -r anchor_owner anchor_mode < <(stat -L -c '%u %a' -- "$anchor")
  if [ "$anchor_owner" != "$release_owner" ] || (( 8#$anchor_mode & 8#022 )); then
    echo "server environment $anchor must be owned by uid $release_owner (the" \
         "release owner) and not group- or world-writable" >&2
    exit 3
  fi
done
# Git refuses a repository another user owns; a root-sealed release is the
# one foreign owner trusted here.
git_trust=()
[ "$release_owner" = 0 ] && [ "$(id -u)" != 0 ] \
  && git_trust=(-c "safe.directory=$release_root")
current_sha=$(/usr/bin/git --no-replace-objects ${git_trust[@]+"${git_trust[@]}"} \
  -C "$release_root" rev-parse --verify 'HEAD^{commit}') \
  || { echo "release HEAD is unreadable" >&2; exit 3; }
[ "$current_sha" = "$expected_sha" ] \
  || { echo "release HEAD does not match authorized SHA" >&2; exit 3; }

set -a
# shellcheck disable=SC1090 -- release-owner file, path and ownership validated above
unset OMNIGENT_DATABASE_URI
. "$server_env"
set +a
# Managed exact-SHA releases never inherit a fail-open escape hatch from the
# environment file or the launching shell: every *_FAIL_OPEN (semaphore,
# authority, and any added later) turns a fence into an ALLOW.
for fail_open_name in $(compgen -v); do
  case "$fail_open_name" in *_FAIL_OPEN) unset "$fail_open_name" ;; esac
done
[ -n "${OMNIGENT_DATABASE_URI:-}" ] \
  || { echo "server environment missing OMNIGENT_DATABASE_URI" >&2; exit 3; }
# The URI becomes argv below, readable by every local user through ps, so it
# must not carry the password; libpq reads that from PGPASSFILE.
if [[ "$OMNIGENT_DATABASE_URI" =~ ^[A-Za-z][A-Za-z0-9+.-]*://[^/@]*:[^/@]*@ \
      || "$OMNIGENT_DATABASE_URI" =~ [?\&]password= ]]; then
  echo "OMNIGENT_DATABASE_URI carries the database password, which would be" \
       "visible in ps; move it to the file named by PGPASSFILE" >&2
  exit 3
fi
export NIGHTSHIFT_RELEASE_ROOT="$release_root"
export NIGHTSHIFT_RELEASE_SHA="$expected_sha"
unset NIGHTSHIFT_CONTROL_DB
export NIGHTSHIFT_CTL_DB="$control_db"
export PYTHONPATH="$policy_dir"
export PATH="$release_root/bin:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
cd "$project"
exec "$omnigent_bin" server \
  --host 127.0.0.1 \
  --port "$server_port" \
  --database-uri "$OMNIGENT_DATABASE_URI" \
  --execution-timeout 21600 \
  --no-open \
  --agent "$agent_root"
