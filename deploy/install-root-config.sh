#!/usr/bin/env bash
# Install the files that configure Nightshift's fences where the runtime user
# can read them but not change them (audit C6). Run as ROOT, after
# bootstrap.sh, and again whenever an input below changes:
#   sudo /srv/nightshift/deploy/install-root-config.sh --user nightshift \
#     [--state-root DIR --repository-id N] [--own-bundle]
#
# Workers run as the runtime user. A server.env, policy module or authority
# registry that user can write is one a worker can rewrite, and the next
# legitimate restart (Restart=always) loads the worker's version. So:
#   /etc/nightshift/server.env        root:<group> 0640, written from flags
#   /etc/nightshift/watchdog.env      root:<group> 0640, the probe's token only
#   /var/lib/nightshift/authority-root  root-owned read-only copies of the
#                                     authority/tracker registries the fences
#                                     read (`--state-root` holds the originals,
#                                     which must stay runtime-owned)
# Nothing is copied from the runtime user's old ~/.config/omnigent/server.env:
# that file is worker-writable, so its lines are exactly what must not be
# trusted. Move any custom line across by hand, after reading it.
set -euo pipefail

CONFIG_DIR=/etc/nightshift
ANCHOR_ROOT=/var/lib/nightshift/authority-root
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAMS_DIR="$(dirname "$HERE")"
user="" state_root="" repository_id="" own_bundle=0 policy_dir=""

usage() {
  echo "usage: $0 --user NAME [--state-root DIR --repository-id N] [--policy-dir DIR] [--own-bundle]" >&2
  exit 2
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user) user="${2:-}"; shift 2 ;;
    --state-root) state_root="${2:-}"; shift 2 ;;
    --repository-id) repository_id="${2:-}"; shift 2 ;;
    --policy-dir) policy_dir="${2:-}"; shift 2 ;;
    --own-bundle) own_bundle=1; shift ;;
    *) usage ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "run as root: a file the runtime user installs is one it can rewrite" >&2; exit 1; }
[ -n "$user" ] && [ "$user" != root ] || usage
home=$(getent passwd "$user" | cut -d: -f6)
group=$(id -gn "$user")
[ -n "$home" ] || { echo "no such user: $user" >&2; exit 1; }
policy_dir="${policy_dir:-$TEAMS_DIR/deploy/policies}"
case "$policy_dir" in /*) ;; *) usage ;; esac
if [ -n "$state_root$repository_id" ]; then
  case "$repository_id" in ''|*[!0-9]*) usage ;; esac
  case "$state_root" in /*) ;; *) usage ;; esac
fi
pgpass="$home/.config/omnigent/pgpass"
[ -f "$pgpass" ] || { echo "$pgpass missing: run deploy/bootstrap.sh as $user first" >&2; exit 1; }

install -d -o root -g root -m 0755 "$CONFIG_DIR"
if [ "$own_bundle" = 1 ]; then
  chown -R root:root "$TEAMS_DIR"
  chmod -R go-w "$TEAMS_DIR"
fi
if [ -n "$repository_id" ]; then
  python3 "$HERE/lib/trust_anchors.py" promote --state-root "$state_root" \
    --anchor-root "$ANCHOR_ROOT" --repository-id "$repository_id"
fi

# Managed keys are rewritten from the flags above; any other line already in
# the ROOT-owned file (GH_TOKEN, OMNIGENT_REMOTE_AUTH_TOKEN, ...) is kept,
# except a fail-open switch, which is dropped.
managed='OMNIGENT_DATABASE_URI|PGPASSFILE|OMNIGENT_ANALYTICS|PYTHONPATH|NIGHTSHIFT_STATE_ROOT|NIGHTSHIFT_REPOSITORY_ID|[A-Za-z0-9_]*_FAIL_OPEN'
kept=""
[ -f "$CONFIG_DIR/server.env" ] \
  && kept=$(grep -vE "^[[:space:]]*(export[[:space:]]+)?($managed)[[:space:]]*=" "$CONFIG_DIR/server.env" \
            | grep -v '^# Managed by install-root-config.sh' || true)
write_root_file() {
  local target="$1" tmp
  tmp=$(mktemp "$CONFIG_DIR/.install.XXXXXX")
  cat > "$tmp"
  chown "root:$group" "$tmp"
  chmod 0640 "$tmp"
  mv -f "$tmp" "$target"
}
{
  echo "# Managed by install-root-config.sh; root-owned so workers cannot edit it."
  echo "OMNIGENT_DATABASE_URI=postgresql://omnigent@127.0.0.1:5433/omnigent"
  echo "PGPASSFILE=$pgpass"
  echo "OMNIGENT_ANALYTICS=0"
  echo "PYTHONPATH=$policy_dir"
  if [ -n "$repository_id" ]; then
    echo "NIGHTSHIFT_STATE_ROOT=$ANCHOR_ROOT"
    echo "NIGHTSHIFT_REPOSITORY_ID=$repository_id"
  fi
  [ -z "$kept" ] || printf '%s\n' "$kept"
} | write_root_file "$CONFIG_DIR/server.env"
grep -E '^OMNIGENT_REMOTE_AUTH_TOKEN=' "$CONFIG_DIR/server.env" \
  | write_root_file "$CONFIG_DIR/watchdog.env" || true
echo "installed $CONFIG_DIR/server.env and $CONFIG_DIR/watchdog.env (root:$group 0640)"
[ -n "$repository_id" ] \
  || echo "no --repository-id: the merge fence has no scope and DENIES every merge (docs/runbooks/repository-ownership-bootstrap.md)"

findings=$(python3 "$HERE/lib/trust_anchors.py" audit --user "$user" \
  "$CONFIG_DIR/server.env" --tree "$policy_dir" --tree "$TEAMS_DIR/program" \
  ${repository_id:+--tree "$ANCHOR_ROOT"}) || {
  printf '%s\n' "$findings" | head -5 >&2
  echo "STILL WRITABLE by $user (above). The bundle at $TEAMS_DIR must be root-owned:" \
       "re-run with --own-bundle, or run from a root-sealed release (nightshift-release seal)" >&2
  exit 1
}
echo "fence configuration is root-controlled; restart omnigent-server and omnigent-host as $user"
