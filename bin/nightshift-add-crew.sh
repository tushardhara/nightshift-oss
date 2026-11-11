#!/usr/bin/env bash
# Add a crew. Clones an existing crew's bundle and registers it with the program.
#   nightshift-add-crew.sh <new_crew_name> [source_pod]
# e.g.
#   nightshift-add-crew.sh crew_gamma
#
# Adding a crew does NOT add capacity: workers come from one global budget in
# .omni/capacity.json, so a new crew means the program has one more claimant on the
# same parallelism. Raise max_concurrent_workers only if no vendor is hitting limits.

set -euo pipefail

NEW="${1:-}"
SRC="${2:-crew_alpha}"
TEAMS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$TEAMS_DIR/program/agents"

if [ -z "$NEW" ]; then
  echo "usage: $(basename "$0") <new_crew_name> [source_pod]" >&2
  echo "existing crews:" >&2
  ls -1 "$AGENTS" | grep '^crew_' | sed 's/^/  /' >&2
  exit 1
fi

case "$NEW" in
  crew_*) ;;
  *) echo "error: crew names must start with 'crew_' (the program finds crews by that prefix)" >&2; exit 1 ;;
esac

[ -d "$AGENTS/$SRC" ]  || { echo "error: source crew $SRC not found in $AGENTS" >&2; exit 1; }
[ ! -d "$AGENTS/$NEW" ] || { echo "error: $NEW already exists" >&2; exit 1; }

cp -R "$AGENTS/$SRC" "$AGENTS/$NEW"

# Rename every occurrence in the clone. Today the crew's name appears only in
# its own config.yaml, but a sub-agent config or skill may gain it later —
# grep finds them all instead of trusting a hardcoded file list.
FILES=$(grep -rl "$SRC" "$AGENTS/$NEW" || true)
for f in $FILES; do
  if [ "$(uname)" = "Darwin" ]; then
    sed -i '' "s/${SRC}/${NEW}/g" "$f"
  else
    sed -i "s/${SRC}/${NEW}/g" "$f"
  fi
done

# Register it in the program's roster, right after the last existing crew entry.
PROG="$TEAMS_DIR/program/config.yaml"
if grep -q "^    - ${NEW}$" "$PROG"; then
  echo "note: $NEW already listed in program roster"
else
  python3 - "$PROG" "$NEW" <<'PY'
import sys, re, pathlib
prog, new = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = prog.read_text().splitlines(keepends=True)
last = max(i for i, l in enumerate(lines) if l.startswith("    - crew_"))
lines.insert(last + 1, f"    - {new}\n")
prog.write_text("".join(lines))
PY
  echo "registered $NEW in program/config.yaml"
fi

cat <<EOF

added $NEW (cloned from $SRC)
  $AGENTS/$NEW/

still to do, by hand:
  1. give it a slice — edit the crew boundary map in .omni/goal.md so its owned
     paths are disjoint from every other crew's
  2. add its rows to docs/owners.md for the paths it now owns
  3. create .omni/registry-${NEW}.json  ->  printf '{\n  "tasks": []\n}\n' > .omni/registry-${NEW}.json
  4. add a "## ${NEW}" section to .omni/backlog.md
  5. re-check .omni/capacity.json — more crews on the same budget means smaller
     grants, not more workers

verify the bundle still parses:
  PYTHONPATH=$TEAMS_DIR/deploy/policies omnigent run $TEAMS_DIR/program -p "list your crews and each one's grant"
EOF
