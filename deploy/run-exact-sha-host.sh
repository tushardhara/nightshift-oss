#!/bin/bash
# Launch one Omnigent host/runner with every repo-owned runtime path derived
# from the same sealed release root as the server.
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
  echo "usage: $0 --release-root DIR --expected-sha SHA --project DIR --control-db FILE --policy-dir DIR --server URL --omnigent-bin FILE" >&2
  exit 2
}

release_root= expected_sha= project= control_db= policy_dir= server= omnigent_bin=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --release-root) release_root="${2:-}"; shift 2 ;;
    --expected-sha) expected_sha="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --control-db) control_db="${2:-}"; shift 2 ;;
    --policy-dir) policy_dir="${2:-}"; shift 2 ;;
    --server) server="${2:-}"; shift 2 ;;
    --omnigent-bin) omnigent_bin="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || usage
for value in "$release_root" "$project" "$control_db" "$policy_dir" "$omnigent_bin"; do
  case "$value" in /*) ;; *) usage ;; esac
done
case "$server" in http://127.0.0.1:*) ;; *) usage ;; esac
[ -d "$release_root/.git" ] || { echo "release root is not a Git checkout" >&2; exit 3; }
[ -d "$project" ] || { echo "project missing: $project" >&2; exit 3; }
[ -f "$control_db" ] && [ ! -L "$control_db" ] \
  || { echo "canonical control DB missing or symlinked: $control_db" >&2; exit 3; }
[ -f "$policy_dir/omnipods_policies.py" ] \
  || { echo "policy module missing: $policy_dir" >&2; exit 3; }
[ -x "$omnigent_bin" ] || { echo "pinned Omnigent executable missing: $omnigent_bin" >&2; exit 3; }
# A root-sealed release is the one foreign owner Git is told to trust here.
git_trust=()
[ "$(stat -c %u -- "$release_root")" = 0 ] && [ "$(id -u)" != 0 ] \
  && git_trust=(-c "safe.directory=$release_root")
current_sha=$(/usr/bin/git --no-replace-objects ${git_trust[@]+"${git_trust[@]}"} \
  -C "$release_root" rev-parse --verify 'HEAD^{commit}') \
  || { echo "release HEAD is unreadable" >&2; exit 3; }
[ "$current_sha" = "$expected_sha" ] \
  || { echo "release HEAD does not match authorized SHA" >&2; exit 3; }

export NIGHTSHIFT_RELEASE_ROOT="$release_root"
export NIGHTSHIFT_RELEASE_SHA="$expected_sha"
for fail_open_name in $(compgen -v); do
  case "$fail_open_name" in *_FAIL_OPEN) unset "$fail_open_name" ;; esac
done
unset NIGHTSHIFT_CONTROL_DB
export NIGHTSHIFT_CTL_DB="$control_db"
export PYTHONPATH="$policy_dir"
export HARNESS_CODEX_DISABLE_NATIVE_TOOLS=1
export HARNESS_CODEX_ENABLE_WEB_SEARCH=0
export PATH="$release_root/bin:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
cd "$project"
exec "$omnigent_bin" host --server "$server" --non-interactive
