#!/usr/bin/env bash
# Snapshot the org's file state (.omni/ + docs/) to the `omni/state` branch and
# push it, WITHOUT touching the checkout, the index, or the current branch —
# plumbing only, so it can run every 30 minutes while agents are mid-wave.
#
# Why: the design's recovery story is "a fresh session resumes from files
# alone" (.omni/goal.md, registries, ledger, decisions). Those files live only
# on this host's disk (a persistent VPS/local volume — EBS on AWS, NVMe on a
# Hostinger KVM) until release merges something. This pushes them off-box on a
# timer, so a dead machine costs at most 30 minutes of state.
#
# Single writer: only this box writes omni/state, so pushes always fast-forward.
#
# WHERE it pushes is explicit configuration, and the default is nowhere. The
# snapshot holds the control-plane dump and incident notes, so it must never
# land on a branch strangers can read; it used to go to `origin` unasked,
# which is public the moment the project repo is. Only a git remote named
# `nightshift-state` receives it, and a GitHub remote must answer
# `visibility: private` to `gh api` first -- anything else is refused. With no
# such remote the snapshot is still committed locally to refs/heads/omni/state.
#   git remote add nightshift-state <url-of-a-PRIVATE-repository>
# Restore on a new box:
#   git fetch nightshift-state omni/state
#   git restore --source=FETCH_HEAD -- .omni docs
#
# H4 (#400): narrative memory's memory.db lives under an ADR-0021
# state_root, a directory OUTSIDE this checkout BY DESIGN -- `git add --
# .omni docs` below has never been able to see it and never will. Before
# this fix that made every run of this script quietly cover zero bytes of
# memory once it existed, with nothing here or in `preflight.sh`'s
# timer-active check (it only checks the ref exists, not what is in it)
# saying so. STATE_ROOT is the fix's other half: a second, explicit,
# no-default argument -- never an env fallback, the same "no ambient
# discovery" posture ADR-0020 holds for state_root at bind time -- so the
# coverage gap is a loud, visible fact on every run instead of an
# assumption. It is metadata-only (see below): full memory.db backup is a
# separate, not-yet-built mechanism, disclosed as a gap here rather than
# silently claimed away.

set -euo pipefail
PROJECT_DIR="${1:-/srv/project}"
STATE_ROOT="${2:-}"
BRANCH="refs/heads/omni/state"
BACKUP_REMOTE="nightshift-state"
FAILED=0
# shellcheck source=lib/deploy-lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deploy-lib.sh"

cd "$PROJECT_DIR"
[ -d .git ] || { echo "not a git repo: $PROJECT_DIR" >&2; exit 1; }

export GIT_AUTHOR_NAME="nightshift-state" GIT_AUTHOR_EMAIL="nightshift-state@localhost"
export GIT_COMMITTER_NAME="nightshift-state" GIT_COMMITTER_EMAIL="nightshift-state@localhost"

# Control-plane state rides the snapshot as JSON: control.db itself is
# gitignored runtime state (live slots/claims are meaningless on a new box),
# but sequences, checkpoints and task lifecycle matter for recovery — a
# restored box re-seeds from this file (`nightshift-ctl seq set`, `task upsert`).
command -v nightshift-ctl >/dev/null 2>&1 && [ -f .omni/control.db ] \
  && nightshift-ctl --db .omni/control.db dump > .omni/control-backup.json 2>/dev/null \
  || true

if [ -z "$STATE_ROOT" ]; then
  echo "warn: STATE_ROOT not given — this snapshot covers .omni + docs ONLY." \
       "If narrative memory (issue #400) exists on this host, its state" \
       "under an ADR-0021 state_root is NOT captured by this run. Pass it" \
       "as the second argument (see nightshift-state-backup.service) once" \
       "memory is deployed here." >&2
elif [ ! -d "$STATE_ROOT" ]; then
  echo "error: STATE_ROOT does not exist or is not a directory:" \
       "$STATE_ROOT — .omni + docs are still snapshotted below, but" \
       "memory state is NOT, and this run exits non-zero so that gap is" \
       "visible instead of silently green." >&2
  FAILED=1
else
  # Metadata only (path/size/mtime) — never opens memory.db itself. A
  # live WAL-mode database has no safe, lock-free way to be read from
  # outside the process that owns it (H2's WAL-starvation finding is
  # exactly about an outside reader interacting badly with it), and a
  # raw sqlite file is not sensible content for a git-commit-based
  # snapshot built for small JSON/docs. This is a recovery breadcrumb —
  # "memory existed, here is roughly how big and how fresh" — not a
  # substitute for memory.db's own backup mechanism, which does not
  # exist yet.
  if ! python3 - "$STATE_ROOT" > .omni/memory-state-manifest.json <<'PY'
import json
import os
import sys
import time

state_root = sys.argv[1]
found = []
for dirpath, _dirnames, filenames in os.walk(state_root):
    for name in filenames:
        if name != "memory.db" and not name.startswith("memory.db-"):
            continue
        path = os.path.join(dirpath, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        found.append({
            "path": os.path.relpath(path, state_root),
            "size": st.st_size,
            "mtime": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
        })
found.sort(key=lambda row: row["path"])
json.dump({
    "state_root": state_root,
    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "files": found,
    "note": "metadata only (path/size/mtime) — memory.db bytes are not "
            "read or captured by this script",
}, sys.stdout, indent=2, sort_keys=True)
print()
PY
  then
    echo "warn: failed to write memory-state manifest for" \
         "STATE_ROOT=$STATE_ROOT — snapshot proceeds without it" >&2
    FAILED=1
  fi
fi

# Build the snapshot in a throwaway index so agents' git activity is untouched.
GIT_INDEX_FILE="$(mktemp)"
export GIT_INDEX_FILE
trap 'rm -f "$GIT_INDEX_FILE"' EXIT

git read-tree --empty
# .gitignore still applies: worktrees and transcripts stay out by design.
git add -- .omni docs 2>/dev/null || true
TREE=$(git write-tree)

PARENT=$(git rev-parse -q --verify "$BRANCH" || true)
if [ -n "$PARENT" ] && [ "$(git rev-parse "$PARENT^{tree}")" = "$TREE" ]; then
  exit "$FAILED"  # content unchanged since the last snapshot; the STATE_ROOT
                   # gap above (if any) must still surface, not disappear
                   # behind this shortcut
fi

COMMIT=$(git commit-tree "$TREE" ${PARENT:+-p "$PARENT"} \
  -m "state snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ)")
git update-ref "$BRANCH" "$COMMIT"

if ! configured=$(git config --get-all "remote.$BACKUP_REMOTE.url"); then
  echo "warn: no '$BACKUP_REMOTE' remote — snapshot kept locally in $BRANCH only." \
       "Off-box backup is disabled until you add one that points at a PRIVATE" \
       "repository: git remote add $BACKUP_REMOTE <url>" >&2
  exit "$FAILED"
fi
# Both the URL as written and every URL a push actually goes to after
# insteadOf/pushurl rewriting: either one can be the public repository.
while IFS= read -r url; do
  slug=$(nightshift_github_slug "$url") || continue
  visibility=$(nightshift_repo_visibility "$slug" || true)
  if [ "$visibility" != private ]; then
    echo "error: refusing to push .omni to $slug: visibility is" \
         "'${visibility:-unknown}', not 'private'. The snapshot carries the" \
         "control dump and incident notes; point '$BACKUP_REMOTE' at a private" \
         "repository. Snapshot kept locally in $BRANCH." >&2
    exit 1
  fi
done <<EOF
$configured
$(git remote get-url --push --all "$BACKUP_REMOTE")
EOF

# A failed push (offline, token expired) keeps the snapshot locally and retries
# on the next timer tick — never fail loudly into the journal for a blip.
git push -q "$BACKUP_REMOTE" "$BRANCH:$BRANCH" \
  || echo "warn: push failed — snapshot kept locally, will retry" >&2

exit "$FAILED"
