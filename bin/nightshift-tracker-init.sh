#!/usr/bin/env bash
# Set up GitHub Issues + Projects as the org's tracker.
#   nightshift-tracker-init.sh [repo-path] [canonical-project-title]
# Idempotent — re-run after adding a crew.
#
# Needs the `project` scope, which `gh auth login` does NOT grant by default:
#   gh auth refresh -s project

set -euo pipefail

# Resolve the bundle root BEFORE any cd — $0 is relative for relative
# invocations and silently points nowhere after cd'ing into the repo.
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

REPO_DIR="${1:-$PWD}"
REQUESTED_PROJECT_TITLE="${2:-}"
cd "$REPO_DIR"

command -v gh >/dev/null || { echo "error: gh not installed" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: gh not authenticated" >&2; exit 1; }
# Check the scopes line, not the whole output — a repo/account name containing
# "project" must not pass a token that lacks the scope.
if ! gh auth status 2>&1 | grep -i "token scopes" | \
     grep -Eq "(^|[[:space:],'])project([[:space:],']|$)"; then
  echo "error: token lacks the 'project' scope. Run: gh auth refresh -s project" >&2
  exit 1
fi

OWNER=$(gh repo view --json owner --jq .owner.login)
REPO=$(gh repo view --json name --jq .name)
CREWS=$(ls -1 "$BUNDLE_DIR/program/agents" | grep '^crew_')
PROJECT_TITLE="Delivery — $REPO"
[ -z "$REQUESTED_PROJECT_TITLE" ] || [ "$REQUESTED_PROJECT_TITLE" = "$PROJECT_TITLE" ] || {
  echo "error: project title must be '$PROJECT_TITLE'" >&2
  exit 1
}

say() { printf '\n=== %s\n' "$1"; }

# ---------------------------------------------------------------- labels ----
say "labels"
mklabel() { gh label create "$1" --color "$2" --description "$3" --force >/dev/null && printf '  %s\n' "$1"; }

for crew in $CREWS; do mklabel "$crew" "5b21b6" "Owned by $crew"; done
mklabel "P0"            "b91c1c" "Drop everything"
mklabel "P1"            "ea580c" "This wave"
mklabel "P2"            "ca8a04" "Soon"
mklabel "P3"            "65a30d" "Someday"
mklabel "size:S"        "e5e7eb" "Hours"
mklabel "size:M"        "d1d5db" "A day or two"
mklabel "size:L"        "9ca3af" "Needs splitting"
mklabel "state:ready"   "16a34a" "Passed Definition of Ready — dispatchable"
mklabel "from-owner"    "0e7490" "Owner-filed idea or request — program triages at next heartbeat"
mklabel "state:blocked" "6b7280" "Needs something the org cannot obtain"
mklabel "needs-dor"     "f59e0b" "Written but not yet Ready"
mklabel "needs-owner"   "d97706" "Needs one owner decision before work can continue"
mklabel "incident"      "dc2626" "Interrupt lane — outranks the roadmap"
mklabel "tech-debt"     "78716c" "Knowingly deferred, has a trigger"
mklabel "security"      "7f1d1d" "Touches the sensitive set"
mklabel "adr"           "1d4ed8" "Decision record attached"

# ------------------------------------------------------- issue templates ----
say "issue templates"
mkdir -p .github/ISSUE_TEMPLATE
# GitHub applies a template's `labels:` for ANY filer, so a stranger could put
# an issue straight into the owner or incident lane. The templates below carry
# none; earlier copies already in this repository are flagged, not rewritten.
for tpl in .github/ISSUE_TEMPLATE/*.md; do
  [ -f "$tpl" ] || continue
  if awk 'NR == 1 && $0 == "---" { f = 1; next } f && $0 == "---" { exit } f' "$tpl" \
      | grep -q '^labels:'; then
    echo "  warn: $tpl applies labels for any filer; delete its labels: line" >&2
  fi
done

if [ ! -f .github/ISSUE_TEMPLATE/backlog-item.md ]; then
  cat > .github/ISSUE_TEMPLATE/backlog-item.md <<'EOF'
---
name: Backlog item
about: A unit of work a crew can be dispatched on
---

## Problem
<!-- What is wrong or missing, in plain words? -->

## Impact
<!-- Who is affected, and what can they not do? -->

## Done when
<!-- Observable behaviour, not a solution. "Invoices show tax per line", not "add a tax field". -->

## Not included
<!-- Explicit boundaries for this item. -->

## Detailed acceptance criteria
<!-- Numbered, each checkable by a test or a named manual step.
     No criterion may contain "properly", "correctly", or "as expected". -->
1.
2.

## Failure and empty states
<!-- What the user sees when it breaks, and when there is nothing to show. -->

## Dependencies
<!-- Which issue or handoff must land first. -->

## Open questions
<!-- Each with the decision it blocks. Mark any that do not block this item. -->

---
### Definition of Ready — the crew's `pm` checks these before dispatch
- [ ] Done when describes observable behaviour
- [ ] Acceptance criteria numbered and individually checkable
- [ ] Failure and empty states specified
- [ ] Not included is explicit
- [ ] Dependencies named
- [ ] Open questions answered, or marked non-blocking

*All six checked → swap `needs-dor` for `state:ready`.*
EOF
  echo "  backlog-item.md"
fi

if [ ! -f .github/ISSUE_TEMPLATE/incident.md ]; then
  cat > .github/ISSUE_TEMPLATE/incident.md <<'EOF'
---
name: Incident
about: Something is broken — goes to oncall, outranks the roadmap
---
<!-- Add the `incident` and `P0` labels yourself. The template applies no labels on
     purpose: it would apply them for anyone, and the program only trusts labels on
     issues filed by the owner, members or collaborators. -->

## Problem
<!-- What is broken, in plain words? -->

## Impact
<!-- Who or what is affected right now? -->

## Done when
<!-- The observable recovery condition. -->

## Not included
<!-- Boundaries of the immediate incident response. -->

## Since when / suspected trigger

## Reproduction
<!-- Exact command and its output. An incident nobody can reproduce is an investigation. -->
## What would have caught this earlier
<!-- Filled in at close. This is what turns an incident into a permanent improvement. -->
EOF
  echo "  incident.md"
fi

if [ ! -f .github/ISSUE_TEMPLATE/owner-request.md ]; then
  cat > .github/ISSUE_TEMPLATE/owner-request.md <<'EOF'
---
name: Owner request
about: An idea or change from the owner — picked up at the program's next heartbeat, never interrupts in-flight work
---
<!-- Add the `from-owner` label yourself. The template applies no labels on purpose:
     it would apply them for anyone, and the program only trusts labels on issues
     filed by the owner, members or collaborators. -->

## Problem
<!-- What is missing or difficult today, in plain words? -->

## Impact
<!-- Who benefits and what improves? Helps the program rank it. -->

## Done when
<!-- The observable outcome you want, not the implementation. -->

## Not included
<!-- Anything you explicitly do not want this request to grow into. -->

## Urgency
<!-- "next wave" / "this milestone" / "whenever it fits". Something actually broken
     is an Incident, not an owner request. -->
EOF
  echo "  owner-request.md"
fi

# --------------------------------------------------------------- project ----
say "project board"
if [ -f .omni/tracker.json ]; then
  CONFIG_OWNER=$(jq -r '.owner // empty' .omni/tracker.json)
  CONFIG_REPO=$(jq -r '.repo // empty' .omni/tracker.json)
  NUM=$(jq -r '.project_number // empty' .omni/tracker.json)
  [ "$CONFIG_OWNER" = "$OWNER" ] && [ "$CONFIG_REPO" = "$REPO" ] && [ -n "$NUM" ] || {
    echo "error: .omni/tracker.json does not identify this repository" >&2
    exit 1
  }
  CONFIG_TITLE=$(jq -r '.project_title // empty' .omni/tracker.json)
  case "$CONFIG_TITLE" in
    "$PROJECT_TITLE"|Delivery|"$REPO — delivery") ;;
    *)
      echo "error: configured project title '$CONFIG_TITLE' is an unrecognized Delivery title" >&2
      exit 1
      ;;
  esac
  echo "  reusing configured project #$NUM"
else
  PROJECT_LIST=$(gh project list --owner "$OWNER" --limit 1000 --closed --format json)
  printf '%s' "$PROJECT_LIST" | jq -e '
    (.projects | type == "array") and
    (.totalCount | type == "number" and . >= 0 and floor == .) and
    ([.projects[] | select(
      (.number | type) != "number" or
      (.title | type) != "string" or
      (.closed | type) != "boolean"
    )] | length == 0)
  ' >/dev/null || {
    echo "error: GitHub returned malformed Project discovery state" >&2
    exit 1
  }
  PROJECT_COUNT=$(printf '%s' "$PROJECT_LIST" | jq '.projects | length')
  PROJECT_TOTAL=$(printf '%s' "$PROJECT_LIST" | jq '.totalCount')
  [ "$PROJECT_COUNT" -eq "$PROJECT_TOTAL" ] || {
    echo "error: Project discovery is incomplete ($PROJECT_COUNT of $PROJECT_TOTAL); refusing to guess" >&2
    exit 1
  }

  CANONICAL_COUNT=$(printf '%s' "$PROJECT_LIST" | jq --arg title "$PROJECT_TITLE" \
    '[.projects[] | select(.title == $title)] | length')
  [ "$CANONICAL_COUNT" -le 1 ] || {
    echo "error: found $CANONICAL_COUNT Projects named '$PROJECT_TITLE'; refusing ambiguous reuse" >&2
    exit 1
  }
  if [ "$CANONICAL_COUNT" -eq 1 ]; then
    NUM=$(printf '%s' "$PROJECT_LIST" | jq -r --arg title "$PROJECT_TITLE" \
      '.projects[] | select(.title == $title) | .number')
    PROJECT_CLOSED=$(printf '%s' "$PROJECT_LIST" | jq -r --arg title "$PROJECT_TITLE" \
      '.projects[] | select(.title == $title) | .closed')
    [ "$PROJECT_CLOSED" = false ] || {
      echo "error: Project #$NUM named '$PROJECT_TITLE' is closed; refusing to reuse it" >&2
      exit 1
    }
  else
    LEGACY_TITLE="$REPO — delivery"
    LEGACY_COUNT=$(printf '%s' "$PROJECT_LIST" | jq --arg title "$LEGACY_TITLE" \
      '[.projects[] | select(.title == $title)] | length')
    GENERIC_COUNT=$(printf '%s' "$PROJECT_LIST" | jq \
      '[.projects[] | select(.title == "Delivery")] | length')
    [ "$LEGACY_COUNT" -le 1 ] || {
      echo "error: found $LEGACY_COUNT Projects named '$LEGACY_TITLE'; refusing ambiguous reuse" >&2
      exit 1
    }
    [ "$GENERIC_COUNT" -eq 0 ] || {
      echo "error: an unconfigured Project named 'Delivery' cannot be safely tied to this repository; restore .omni/tracker.json" >&2
      exit 1
    }
    if [ "$LEGACY_COUNT" -eq 1 ]; then
      NUM=$(printf '%s' "$PROJECT_LIST" | jq -r --arg title "$LEGACY_TITLE" \
        '.projects[] | select(.title == $title) | .number')
      PROJECT_CLOSED=$(printf '%s' "$PROJECT_LIST" | jq -r --arg title "$LEGACY_TITLE" \
        '.projects[] | select(.title == $title) | .closed')
      [ "$PROJECT_CLOSED" = false ] || {
        echo "error: legacy Project #$NUM is closed; refusing to reuse it" >&2
        exit 1
      }
    else
      NUM=""
    fi
  fi

  if [ -z "${NUM:-}" ]; then
    NUM=$(gh project create --owner "$OWNER" --title "$PROJECT_TITLE" --format json --jq .number)
    echo "  created project #$NUM"
  else
    echo "  reusing project #$NUM"
  fi
fi
PROJECT_STATE=$(gh project view "$NUM" --owner "$OWNER" --format json)
LIVE_PROJECT_TITLE=$(printf '%s' "$PROJECT_STATE" | jq -r '.title // empty')
case "$LIVE_PROJECT_TITLE" in
  "$PROJECT_TITLE"|Delivery|"$REPO — delivery") ;;
  *)
    echo "error: project #$NUM has unrecognized title '$LIVE_PROJECT_TITLE'; refusing to rename a custom board" >&2
    exit 1
    ;;
esac
gh project link "$NUM" --owner "$OWNER" --repo "$OWNER/$REPO" >/dev/null

have_field() { gh project field-list "$NUM" --owner "$OWNER" --format json --jq '.fields[].name' 2>/dev/null | grep -qx "$1"; }

# Existing legacy Stage/Crew/Wave fields are left untouched. New Projects use
# only the five outcome fields below, avoiding two competing stage systems.
if ! have_field "Delivery stage"; then
  gh project field-create "$NUM" --owner "$OWNER" --name "Delivery stage" --data-type SINGLE_SELECT \
    --single-select-options "Shape,Ready,Doing,Checking,Finished" >/dev/null
  echo "  field: Delivery stage"
fi
if ! have_field "Lane"; then
  gh project field-create "$NUM" --owner "$OWNER" --name "Lane" --data-type SINGLE_SELECT \
    --single-select-options "Program/shared,crew_alpha,crew_beta,Cross-lane/Unassigned" >/dev/null
  echo "  field: Lane"
fi
if ! have_field "Active roles"; then
  gh project field-create "$NUM" --owner "$OWNER" --name "Active roles" --data-type TEXT >/dev/null
  echo "  field: Active roles"
fi
if ! have_field "Current action"; then
  gh project field-create "$NUM" --owner "$OWNER" --name "Current action" --data-type TEXT >/dev/null
  echo "  field: Current action"
fi
if ! have_field "Activity"; then
  gh project field-create "$NUM" --owner "$OWNER" --name "Activity" --data-type SINGLE_SELECT \
    --single-select-options "Active,Idle" >/dev/null
  echo "  field: Activity"
fi

# ---------------------------------------------------- pinned status issue ----
say "status issue (where the daily report lands)"
# Reuse only an exact-title issue this identity filed itself. Anyone can open an
# issue called "Program status" on a public repository, and whichever one is
# adopted here receives every report.
ME=$(gh api user --jq .login 2>/dev/null || true)
case "$ME" in
  ''|*[!A-Za-z0-9-]*) echo "error: cannot resolve the authenticated GitHub login" >&2; exit 1 ;;
esac
EXISTING=$(gh issue list --repo "$OWNER/$REPO" --search "Program status in:title" --state open \
  --author "$ME" --json number,title,author \
  --jq "[.[] | select(.title == \"Program status\" and .author.login == \"$ME\")][0].number // empty" \
  2>/dev/null || true)
if [ -z "${EXISTING:-}" ]; then
  BODY=$(mktemp)
  cat > "$BODY" <<'BODYEOF'
The program posts its wave and daily reports here as comments, so a phone
notification replaces reading a file.

Every later status comment begins with a plain-language summary of no more than
180 words, using: Status; What changed; Live impact; Still not done; Next step;
Owner action. Status is one of On track, Blocked, Ready for review, Ready to
merge, Merged, Superseded, Done, or Closed without completion. Live impact says
what changed in live systems and live data. Detailed evidence follows the
summary; internal IDs and hashes never lead it.

Live agent view (not here): Omnigent's web/mobile UI on the host, over Tailscale.
BODYEOF
  # gh issue create has no --json flag; it prints the issue URL, so take the number
  # off the end of it. --repo is explicit here even though $OWNER/$REPO already
  # match gh's own cwd resolution at this point (we are still cd'd into
  # $REPO_DIR) -- defense in depth, and consistent with every issue write this
  # bundle now performs being explicitly repo-scoped, never implicit.
  N=$(gh issue create --repo "$OWNER/$REPO" --title "Program status" --body-file "$BODY" | grep -oE '[0-9]+$' | tail -1)
  rm -f "$BODY"
  if [ -n "${N:-}" ]; then
    gh issue pin --repo "$OWNER/$REPO" "$N" >/dev/null 2>&1 || true
    echo "  created and pinned issue #$N"
  else
    echo "  warn: could not create the status issue — create one titled 'Program status' by hand"
  fi
else
  echo "  reusing issue #$EXISTING"
  N="$EXISTING"
fi
if [ -n "${N:-}" ]; then
  echo "  pin the alert target in ~/.config/omnigent/scheduled-run-detector.env:"
  echo "    NIGHTSHIFT_ALERT_REPO=$OWNER/$REPO NIGHTSHIFT_ALERT_ISSUE=$N"
fi

# ------------------------------------------------------------ local note ----
mkdir -p .omni
if [ ! -f .omni/tracker.json ]; then
cat > .omni/tracker.json <<EOF
{
  "provider": "github",
  "owner": "$OWNER",
  "repo": "$REPO",
  "project_number": $NUM,
  "project_title": "$PROJECT_TITLE",
  "status_issue": "search: Program status",
  "notes": "Issues are the backlog — the single source of truth. .omni/backlog.md is a cached mirror for offline resume only."
}
EOF
else
  echo "  keeping existing .omni/tracker.json (delete it to regenerate)"
fi

# ---------------------------------------------------------- saved views ----
# View lookup is GraphQL because gh project has no view-list command. Creation
# uses GitHub's official REST endpoints (API 2026-03-10), whose grouping fields
# are numeric database ids rather than GraphQL node ids.
say "project views"
PROJECT_NODE_ID=$(gh project view "$NUM" --owner "$OWNER" --format json --jq .id)
VIEW_QUERY='query($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      views(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id number name layout filter
          fields(first: 100) {
            nodes { ... on ProjectV2FieldCommon { id databaseId name dataType } }
          }
          groupByFields(first: 10) {
            nodes { ... on ProjectV2FieldCommon { id databaseId name dataType } }
          }
          verticalGroupByFields(first: 10) {
            nodes { ... on ProjectV2FieldCommon { id databaseId name dataType } }
          }
        }
      }
      fields(first: 100) {
        nodes { ... on ProjectV2FieldCommon { id databaseId name dataType } }
      }
    }
  }
}'
VIEW_STATE=$(gh api graphql -f query="$VIEW_QUERY" -F projectId="$PROJECT_NODE_ID")

verify_complete_view_state() {
  printf '%s' "$VIEW_STATE" | jq -e \
    '.data.node.views.pageInfo.hasNextPage == false' >/dev/null || {
      echo "error: project view state is incomplete; refusing naming changes" >&2
      return 1
    }
}

verify_complete_view_state

field_database_id() {
  local name=$1 count value
  count=$(printf '%s' "$VIEW_STATE" | jq --arg name "$name" \
    '[.data.node.fields.nodes[] | select(.name == $name)] | length')
  [ "$count" = 1 ] || {
    echo "error: project must contain exactly one field named '$name' (found $count)" >&2
    return 1
  }
  value=$(printf '%s' "$VIEW_STATE" | jq -r --arg name "$name" \
    '.data.node.fields.nodes[] | select(.name == $name) | .databaseId')
  case "$value" in
    ''|null|*[!0-9]*)
      echo "error: project field '$name' lacks a numeric database id" >&2
      return 1
      ;;
  esac
  printf '%s\n' "$value"
}

DELIVERY_STAGE_DBID=$(field_database_id "Delivery stage")
LANE_DBID=$(field_database_id "Lane")
ACTIVE_ROLES_DBID=$(field_database_id "Active roles")
CURRENT_ACTION_DBID=$(field_database_id "Current action")
ACTIVITY_DBID=$(field_database_id "Activity")
TITLE_DBID=$(field_database_id "Title")
VISIBLE_DBIDS=$(jq -nc \
  --argjson title "$TITLE_DBID" --argjson activity "$ACTIVITY_DBID" \
  --argjson stage "$DELIVERY_STAGE_DBID" --argjson lane "$LANE_DBID" \
  --argjson roles "$ACTIVE_ROLES_DBID" --argjson action "$CURRENT_ACTION_DBID" \
  '[$title, $activity, $stage, $lane, $roles, $action]')

OWNER_INFO=$(gh api "users/$OWNER")
OWNER_TYPE=$(printf '%s' "$OWNER_INFO" | jq -r .type)
case "$OWNER_TYPE" in
  Organization)
    VIEW_ENDPOINT="orgs/$OWNER/projectsV2/$NUM/views"
    PROJECT_URL="https://github.com/orgs/$OWNER/projects/$NUM"
    ;;
  User)
    VIEW_ENDPOINT="users/$OWNER/projectsV2/$NUM/views"
    PROJECT_URL="https://github.com/users/$OWNER/projects/$NUM"
    ;;
  *) echo "error: unsupported project owner type: $OWNER_TYPE" >&2; exit 1 ;;
esac

view_count() {
  printf '%s' "$VIEW_STATE" | jq --arg name "$1" \
    '[.data.node.views.nodes[] | select(.name == $name)] | length'
}

view_json() {
  printf '%s' "$VIEW_STATE" | jq -c --arg name "$1" \
    '[.data.node.views.nodes[] | select(.name == $name)][0]'
}

verify_view_or_missing() {
  local name=$1 layout=$2 filter=$3 horizontal=$4 vertical=$5 count
  count=$(view_count "$name")
  [ "$count" -le 1 ] || {
    echo "error: project has $count views named '$name'; refusing an ambiguous update" >&2
    return 1
  }
  [ "$count" = 1 ] || return 0
  printf '%s' "$VIEW_STATE" | jq -e \
    --arg name "$name" --arg layout "$layout" --arg filter "$filter" \
    --arg horizontal "$horizontal" --arg vertical "$vertical" \
    --argjson visible "$VISIBLE_DBIDS" '
    [.data.node.views.nodes[] | select(.name == $name)][0] as $view |
    $view.layout == $layout and $view.filter == $filter and
    ([$view.fields.nodes[].databaseId] | sort) == ($visible | sort) and
    (if $horizontal == "" then
       ($view.groupByFields.nodes | length) == 0
     else
       [$view.groupByFields.nodes[].databaseId] == [($horizontal | tonumber)]
     end) and
    (if $vertical == "" then
       ($view.verticalGroupByFields.nodes | length) == 0
     else
       [$view.verticalGroupByFields.nodes[].databaseId] == [($vertical | tonumber)]
     end)
  ' >/dev/null || {
    echo "error: view '$name' exists with a different configuration; refusing to replace it" >&2
    return 1
  }
}

create_view_if_missing() {
  local name=$1 layout=$2 filter=$3 horizontal=$4 vertical=$5
  if [ "$(view_count "$name")" = 1 ]; then
    echo "  reusing verified view: $name"
    return
  fi
  local -a request=(api --method POST
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "$VIEW_ENDPOINT" -f name="$name" -f layout="$layout" -f filter="$filter" \
    -F "visible_fields[]=$TITLE_DBID" -F "visible_fields[]=$ACTIVITY_DBID" \
    -F "visible_fields[]=$DELIVERY_STAGE_DBID" -F "visible_fields[]=$LANE_DBID" \
    -F "visible_fields[]=$ACTIVE_ROLES_DBID" -F "visible_fields[]=$CURRENT_ACTION_DBID")
  [ -z "$horizontal" ] || request+=(-F "group_by[]=$horizontal")
  [ -z "$vertical" ] || request+=(-F "vertical_group_by[]=$vertical")
  gh "${request[@]}" >/dev/null
  echo "  created view: $name"
}

# Validate the complete current view state before the first view/title write. A
# colliding, renamed or drifted view must not cause a half-applied migration.
VIEW_ONE_COUNT=$(view_count "View 1")
ALL_WORK_COUNT=$(view_count "All work")
NUMBER_ONE_COUNT=$(printf '%s' "$VIEW_STATE" | jq \
  '[.data.node.views.nodes[] | select(.number == 1)] | length')
[ "$NUMBER_ONE_COUNT" = 1 ] || {
  echo "error: project must have exactly one view numbered 1" >&2
  exit 1
}
if [ "$VIEW_ONE_COUNT" = 1 ] && [ "$ALL_WORK_COUNT" = 0 ]; then
  DEFAULT_VIEW_BEFORE=$(view_json "View 1")
  DEFAULT_VIEW_ID=$(printf '%s' "$DEFAULT_VIEW_BEFORE" | jq -r .id)
  DEFAULT_VIEW_NUMBER=$(printf '%s' "$DEFAULT_VIEW_BEFORE" | jq -r .number)
  [ -n "$DEFAULT_VIEW_ID" ] && [ "$DEFAULT_VIEW_ID" != null ] \
    && [ "$DEFAULT_VIEW_NUMBER" = 1 ] || {
      echo "error: 'View 1' is not the unique project view number 1" >&2
      exit 1
    }
  RENAME_DEFAULT_VIEW=1
elif [ "$VIEW_ONE_COUNT" = 0 ] && [ "$ALL_WORK_COUNT" = 1 ]; then
  DEFAULT_VIEW_BEFORE=$(view_json "All work")
  DEFAULT_VIEW_ID=$(printf '%s' "$DEFAULT_VIEW_BEFORE" | jq -r .id)
  DEFAULT_VIEW_NUMBER=$(printf '%s' "$DEFAULT_VIEW_BEFORE" | jq -r .number)
  [ -n "$DEFAULT_VIEW_ID" ] && [ "$DEFAULT_VIEW_ID" != null ] \
    && [ "$DEFAULT_VIEW_NUMBER" = 1 ] || {
      echo "error: 'All work' is not the unique project view number 1" >&2
      exit 1
    }
  RENAME_DEFAULT_VIEW=0
else
  echo "error: expected exactly one project view number 1 named 'View 1' or 'All work'" >&2
  exit 1
fi

verify_view_or_missing "Delivery now" BOARD_LAYOUT 'is:issue' '' "$DELIVERY_STAGE_DBID"
verify_view_or_missing "Agent activity" TABLE_LAYOUT \
  'activity:Active' "$LANE_DBID" ''

create_view_if_missing "Delivery now" board 'is:issue' '' "$DELIVERY_STAGE_DBID"
create_view_if_missing "Agent activity" table \
  'activity:Active' "$LANE_DBID" ''

# Read back and verify the complete configuration. View creation is additive;
# the one default-view name migration happens only after this preflight.
VIEW_STATE=$(gh api graphql -f query="$VIEW_QUERY" -F projectId="$PROJECT_NODE_ID")
verify_complete_view_state
verify_view_or_missing "Delivery now" BOARD_LAYOUT 'is:issue' '' "$DELIVERY_STAGE_DBID"
verify_view_or_missing "Agent activity" TABLE_LAYOUT \
  'activity:Active' "$LANE_DBID" ''

DELIVERY_VIEW_ID=$(view_json "Delivery now" | jq -r .id)
ACTIVITY_VIEW_ID=$(view_json "Agent activity" | jq -r .id)

if [ "$RENAME_DEFAULT_VIEW" = 1 ]; then
  UPDATE_VIEW_MUTATION='mutation($viewId: ID!, $name: String!) {
    updateProjectV2View(input: {viewId: $viewId, name: $name}) {
      projectV2View { id number name }
    }
  }'
  gh api graphql -f query="$UPDATE_VIEW_MUTATION" \
    -F viewId="$DEFAULT_VIEW_ID" -f name="All work" >/dev/null
  echo "  renamed view: View 1 -> All work"
fi

# Read back the rename. The view identity, number and every non-name setting
# must be unchanged; the two delivery views retain both identity and config.
VIEW_STATE=$(gh api graphql -f query="$VIEW_QUERY" -F projectId="$PROJECT_NODE_ID")
verify_complete_view_state
[ "$(view_count "View 1")" = 0 ] && [ "$(view_count "All work")" = 1 ] || {
  echo "error: default view rename did not converge to one 'All work' view" >&2
  exit 1
}
DEFAULT_VIEW_AFTER=$(view_json "All work")
printf '%s' "$DEFAULT_VIEW_AFTER" | jq -e \
  --arg id "$DEFAULT_VIEW_ID" --argjson before "$DEFAULT_VIEW_BEFORE" '
    .id == $id and .number == 1 and .name == "All work" and
    (del(.name) == ($before | del(.name)))
  ' >/dev/null || {
    echo "error: default view changed beyond its name; refusing" >&2
    exit 1
  }
verify_view_or_missing "Delivery now" BOARD_LAYOUT 'is:issue' '' "$DELIVERY_STAGE_DBID"
verify_view_or_missing "Agent activity" TABLE_LAYOUT \
  'activity:Active' "$LANE_DBID" ''
[ "$(view_json "Delivery now" | jq -r .id)" = "$DELIVERY_VIEW_ID" ] \
  && [ "$(view_json "Agent activity" | jq -r .id)" = "$ACTIVITY_VIEW_ID" ] || {
    echo "error: a delivery view identity changed during naming migration" >&2
    exit 1
  }

# Rename the Project only after every view preflight and read-back succeeds.
if [ "$LIVE_PROJECT_TITLE" != "$PROJECT_TITLE" ]; then
  gh project edit "$NUM" --owner "$OWNER" --title "$PROJECT_TITLE" >/dev/null
  echo "  renamed project: $LIVE_PROJECT_TITLE -> $PROJECT_TITLE"
fi
FINAL_PROJECT_TITLE=$(gh project view "$NUM" --owner "$OWNER" --format json --jq .title)
[ "$FINAL_PROJECT_TITLE" = "$PROJECT_TITLE" ] || {
  echo "error: project title did not converge to '$PROJECT_TITLE'" >&2
  exit 1
}

# ------------------------------------------------------- tracker binding ----
# The script that CREATES the tracker is the script that RECORDS which
# repository it is -- closing the bootstrap circularity: `nightshift_issue_
# fence.tracker_issue_write_fence` (deploy/policies/) and `nightshift-file-
# issue` both refuse to guess Nightshift's own tracker repository from
# anything cwd-shaped, so something has to declare it once, explicitly, and
# this is that operator act.
#
# ADR-0088: the value DECLARED is `NIGHTSHIFT_TRACKER_SLUG` -- a THIRD,
# separately-named environment variable an operator sets ONLY when this
# specific run is bootstrapping Nightshift's OWN tracker -- and NEVER
# `$OWNER/$REPO` (this script's own cwd/`gh repo view`-derived values, used
# everywhere else above for labels/templates/project/status-issue, which is
# exactly the source `nightshift_issue_fence.py`'s own module docstring
# disclaims: "never git remote, never os.getcwd(), never .omni/tracker.json
# ... derived from gh repo view at whatever directory happens to be checked
# out"). NIGHTSHIFT_STATE_ROOT/NIGHTSHIFT_REPOSITORY_ID are ambient on the
# runtime host (a root-owned systemd EnvironmentFile, per ADR-0085) --
# present regardless of which repository this script happens to be run
# against. Plenty of valid uses of this script (a customer's own board, a
# dry run) run with those two set but NIGHTSHIFT_TRACKER_SLUG unset, and
# must NOT auto-declare that customer's repository as Nightshift's own
# tracker just because the ambient scope variables were present -- a
# mistake here is permanent (the binding is write-once) and, once made,
# makes the fence ALLOW writes to the customer's tracker and DENY writes to
# Nightshift's real one. Silently skipped -- not a hard error -- whenever
# any of the three is unset.
say "tracker binding (Nightshift's own issue-write fence)"
if [ -n "${NIGHTSHIFT_STATE_ROOT:-}" ] && [ -n "${NIGHTSHIFT_REPOSITORY_ID:-}" ] \
   && [ -n "${NIGHTSHIFT_TRACKER_SLUG:-}" ]; then
  DECLARE_OUT=$(python3 "$BUNDLE_DIR/bin/nightshift_tracker_authority.py" declare \
    --state-root "$NIGHTSHIFT_STATE_ROOT" \
    --repository-id "$NIGHTSHIFT_REPOSITORY_ID" \
    --tracker "$NIGHTSHIFT_TRACKER_SLUG" --declared-by "nightshift-tracker-init.sh") || {
    echo "error: could not declare the tracker binding: $DECLARE_OUT" >&2
    exit 1
  }
  echo "  declared tracker: $NIGHTSHIFT_TRACKER_SLUG"
else
  echo "  NIGHTSHIFT_TRACKER_SLUG not set (or state root/repository id absent) -- skipped."
  echo "  This is deliberate: the binding must never be derived from this"
  echo "  script's cwd or 'gh repo view' (here, that would be $OWNER/$REPO)."
  echo "  If this repository IS Nightshift's own tracker, an operator must"
  echo "  still run, once, on the authoritative runtime host, either setting"
  echo "  NIGHTSHIFT_TRACKER_SLUG=$OWNER/$REPO before re-running this script, or:"
  echo "    nightshift_tracker_authority.py declare --state-root \$NIGHTSHIFT_STATE_ROOT \\"
  echo "      --repository-id \$NIGHTSHIFT_REPOSITORY_ID --tracker $OWNER/$REPO \\"
  echo "      --declared-by <operator>"
fi

cat <<EOF

tracker ready
  board   : $PROJECT_URL
  issues  : https://github.com/$OWNER/$REPO/issues
  docs    : https://github.com/$OWNER/$REPO/tree/HEAD/docs
  config  : .omni/tracker.json

runtime reconciliation is deliberately not run by this initializer.
On the authoritative runtime host, run exactly:
EOF
printf '  %q --repo %q --db %q --apply\n' \
  "$BUNDLE_DIR/bin/nightshift-tracker-sync" "$REPO_DIR" "$REPO_DIR/.omni/control.db"
cat <<EOF

on your phone: GitHub Mobile shows the board, the issues, the PRs and the diffs,
and pushes a notification when the program comments on "Program status".
EOF
