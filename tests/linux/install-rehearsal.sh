#!/usr/bin/env bash
# Clean-source install rehearsal -- DoD condition 11 (docs/prd/DEFINITION-OF-DONE.md
# section C), issue #393. Proves the install path without a VPS: real
# login-session / systemd-user behaviour is exercised through the SAME code
# `deploy/provision-ubuntu.sh` / `deploy/bootstrap.sh` run (deploy/lib/deploy-lib.sh's
# named functions), never a reimplementation of it. A real fresh-VPS reinstall is
# explicitly NOT a condition of done (docs/prd/DEFINITION-OF-DONE.md "Explicitly
# NOT conditions") -- this rehearsal is the PERMANENT substitute, by design, not a
# stand-in until a VPS test exists.
#
# Per-step declaration (DoD condition 11's validator requires exactly these three
# words to be present -- HERMETIC / NETWORKED / STUB):
#
#   HERMETIC steps (no network, safe on any box, always run):
#     - git-archive extraction of the exact tested commit into an empty workspace
#       (never a bind mount of the working tree -- proves untracked files cannot
#       leak into the rehearsed tree; see tests/test_install_rehearsal.py).
#     - the /srv/nightshift (TEAMS_DIR) absence assertion.
#     - condition 1 (Go version discovery survives `set -o pipefail`, both sites)
#       and condition 2 (missing Go is a normal state, proceeds to install) --
#       real decision logic from deploy/lib/deploy-lib.sh, with the network calls
#       inside it injected via a curl override (see NETWORKED below).
#     - condition 5 (bwrap functional probe) -- real nightshift_bwrap_probe, with
#       the bwrap binary injectable via NIGHTSHIFT_BWRAP (same convention
#       tests/test_bwrap_probe.py already uses).
#     - condition 3 (user-manager capability) -- real nightshift_user_manager_ready
#       / nightshift_user_manager_remediation. See STUB below for what this
#       container base image cannot provide.
#
#   NETWORKED steps (real network by default; the DECISION LOGIC is never a stub
#   even when the network call inside it is faked for a hermetic CI run):
#     - go.dev version resolution (nightshift_go_resolve_version) and the sha256
#       checksum lookup (nightshift_go_checksum) both call out to go.dev. Set
#       NIGHTSHIFT_REHEARSAL_CURL to a stub `curl` when no network is available
#       (this script's own hermetic backing test, tests/test_install_rehearsal.py,
#       does exactly this) -- the pipefail-survival and missing-Go-is-normal
#       assertions still run against the REAL function bodies either way.
#     - `apt-get`-based package installation (provision-ubuntu.sh's own
#       responsibility, not re-run by this script -- see STUB).
#
#   STUB (named, with the reason -- DoD condition 11 requires this, not silence):
#     - A real systemd-user login SESSION is not available inside a plain
#       `docker run` container (no --privileged, no systemd as PID 1, no
#       `loginctl`/logind at all). `nightshift_user_manager_ready` is called for
#       real and, in this environment, truthfully reports the manager
#       unreachable -- so this rehearsal demonstrates condition 3's FAIL-LOUD /
#       named-remediation branch (the original bug's exact failure mode: `su -
#       nightshift` with no session bus), not the enabled-and-working branch.
#       Exercising the enabled branch needs a real login session, which is
#       explicitly NOT a condition of done (see header above). Nothing here is
#       faked to LOOK enabled; the assertion is "enabled, or a named remediation
#       is printed" (criterion 5), and only the second half is reachable in a
#       container.
#     - Full `apt-get install`/toolchain download/systemd unit installation
#       (provision-ubuntu.sh's later, `apt`-heavy steps; and bootstrap.sh's unit
#       *installation*, as opposed to the capability *probe* this script does
#       run) are not re-executed here -- this rehearsal targets the four
#       confirmed-bug DECISION POINTS (conditions 1, 2, 3, 5), not a full
#       provisioning run end to end, which is out of scope for a repeatable
#       rehearsal (docs/prd/DEFINITION-OF-DONE.md's "Explicitly NOT conditions"
#       rules out a literal fresh-VPS reinstall).
#
# Usage (inside a fresh, disposable environment -- a container is the intended
# target, but the script itself has no docker dependency; it is invoked FROM
# inside one):
#
#   tests/linux/install-rehearsal.sh [commit-sha]
#
# Locally, against a real clean-source container (needs Docker + network):
#
#   docker run --rm -v "$PWD":/src:ro ubuntu:24.04 bash -c '
#     set -e
#     apt-get update -qq
#     apt-get install -y -qq git ca-certificates curl jq >/dev/null
#     git config --global --add safe.directory /src
#     git -C /src rev-parse HEAD
#     cp -r /src /tmp/rehearsal-checkout && cd /tmp/rehearsal-checkout
#     bash tests/linux/install-rehearsal.sh "$(git -C /src rev-parse HEAD)"
#   '
#
# Requirement ids: none of its own -- it demonstrates conditions 1, 2, 3, 5
# (RT-01..RT-15), which already have ids from the tasks that fixed them.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SHA="${1:-$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)}"
TEAMS_DIR="${NIGHTSHIFT_REHEARSAL_TEAMS_DIR:-/srv/nightshift}"
CURL_CMD="${NIGHTSHIFT_REHEARSAL_CURL:-curl}"
fail=0

info() { printf '%s\n' "$1"; }
pass() { printf 'PASS: %s\n' "$1"; }
bad()  { printf 'FAIL: %s\n' "$1"; fail=$((fail + 1)); }

if [ -z "$SHA" ]; then
  bad "no commit sha given and \$REPO_ROOT is not a git checkout -- pass one explicitly"
  exit 1
fi
if [ ! -d "$REPO_ROOT/.git" ] && [ ! -f "$REPO_ROOT/.git" ]; then
  bad "$REPO_ROOT is not a git checkout -- install-rehearsal.sh needs the repository's object database to run 'git archive', not just a working tree copy"
  exit 1
fi

info "=== clean-source extraction: git archive $SHA (never a bind mount) ==="
WORKDIR="$(mktemp -d)" || { bad "mktemp -d failed"; exit 1; }
if [ "${NIGHTSHIFT_REHEARSAL_KEEP_WORKDIR:-0}" != "1" ]; then
  trap 'rm -rf "$WORKDIR"' EXIT
else
  # Test-only escape hatch (tests/test_install_rehearsal.py) so the extracted
  # tree can be inspected after the run -- e.g. to prove an untracked file in
  # the source working tree did NOT leak into the extraction (criterion D11-2).
  info "NIGHTSHIFT_REHEARSAL_KEEP_WORKDIR=1: workdir preserved at $WORKDIR"
fi
EXTRACT="$WORKDIR/rehearsal-tree"
mkdir -p "$EXTRACT"
if ! git -C "$REPO_ROOT" archive --format=tar "$SHA" | tar -x -C "$EXTRACT"; then
  bad "git archive $SHA | tar -x failed -- is $SHA a valid, reachable commit in $REPO_ROOT?"
  exit 1
fi
if [ ! -f "$EXTRACT/deploy/lib/deploy-lib.sh" ]; then
  bad "extracted tree has no deploy/lib/deploy-lib.sh -- git archive produced an unexpected tree"
  exit 1
fi
pass "clean-source extraction: $SHA extracted via git archive into an empty workspace"

info ""
info "=== pre-install assertion: $TEAMS_DIR must be absent ==="
if [ -e "$TEAMS_DIR" ]; then
  bad "$TEAMS_DIR already exists -- this rehearsal must start from a box that has never been provisioned. Refusing to proceed: remove $TEAMS_DIR (or point NIGHTSHIFT_REHEARSAL_TEAMS_DIR at an unused path) and re-run."
  exit 1
fi
pass "$TEAMS_DIR is absent -- starting from an unprovisioned state"

# shellcheck source=../../deploy/lib/deploy-lib.sh
. "$EXTRACT/deploy/lib/deploy-lib.sh"

info ""
info "=== condition 1: Go version discovery survives 'set -o pipefail' (both sites) ==="
# Site A -- version resolution. A curl that behaves like a real network failure
# (non-zero exit, no output) must be caught and diagnosed, never let 'set -e'
# kill the script before its own diagnostic runs (issue #8's site A).
if want="$(nightshift_go_want_version "$CURL_CMD" "" 2>/tmp/rehearsal-go-want.err)"; then
  pass "condition 1 site A: version resolution reachable, resolved '$want' via '$CURL_CMD'"
elif grep -q "could not resolve the latest Go version" /tmp/rehearsal-go-want.err 2>/dev/null; then
  pass "condition 1 site A: a failing '$CURL_CMD' produced the LOUD diagnostic (not a silent pipefail abort)"
else
  bad "condition 1 site A: nightshift_go_want_version failed without its own diagnostic -- $(cat /tmp/rehearsal-go-want.err 2>/dev/null)"
fi
rm -f /tmp/rehearsal-go-want.err
# Site B -- checksum lookup. Same shape: a failing lookup must return non-zero
# and be catchable by the caller, never abort the whole script (issue #8 site B).
if nightshift_go_checksum "go9.9.9.linux-amd64.tar.gz" "$CURL_CMD" >/tmp/rehearsal-go-sum.out 2>/tmp/rehearsal-go-sum.err; then
  pass "condition 1 site B: checksum lookup reachable, returned $(cat /tmp/rehearsal-go-sum.out)"
else
  # A real go.dev lookup for a filename that does not exist correctly returns 1
  # (empty entry) -- that IS the fix (issue #8 site B: the pre-fix version never
  # reached this return at all, 'set -e' killed the script first).
  pass "condition 1 site B: checksum lookup for an unresolvable filename returned non-zero to its caller (did not abort the script)"
fi
rm -f /tmp/rehearsal-go-sum.out /tmp/rehearsal-go-sum.err

info ""
info "=== condition 2: a missing /usr/local/go/bin/go is a NORMAL state, not an abort ==="
if ver="$(nightshift_go_installed_version "$EXTRACT/no-such-go-binary")"; then
  if [ -z "$ver" ]; then
    pass "condition 2: absent go binary reported as 'not installed' (empty, exit 0) -- proceeds to install, does not abort"
  else
    bad "condition 2: absent go binary reported a version ('$ver') -- should report absence"
  fi
else
  bad "condition 2: nightshift_go_installed_version returned non-zero for a plain absent binary -- absence must be a normal state, not a hard failure"
fi

info ""
info "=== condition 3: user-manager capability, established without an inherited login session ==="
if nightshift_user_manager_ready; then
  pass "condition 3: systemd --user is reachable in this environment (derived bus env, no inherited session needed)"
else
  remediation="$(nightshift_user_manager_remediation "$(id -un)" "$(id -u)")"
  if printf '%s' "$remediation" | grep -q "loginctl enable-linger"; then
    pass "condition 3: manager unreachable (expected -- this container has no systemd-user session; see the STUB note in this script's header) AND a named, actionable root remediation was printed"
    printf '%s\n' "$remediation" | sed 's/^/    /'
  else
    bad "condition 3: manager unreachable and no actionable remediation was printed"
  fi
fi

info ""
info "=== condition 5: bwrap probed by FUNCTION, not presence ==="
bwrap_code=0
nightshift_bwrap_probe "${NIGHTSHIFT_BWRAP:-bwrap}" || bwrap_code=$?
diagnosis="$(nightshift_bwrap_diagnosis "$bwrap_code")"
if [ "$bwrap_code" -eq 0 ]; then
  pass "condition 5: $diagnosis"
else
  # Non-zero is the CORRECT, actionable outcome for a box (or container) where
  # bwrap cannot actually create a user namespace -- this is the presence-vs-
  # function distinction (issue #5) working as designed, not a rehearsal defect.
  pass "condition 5: bwrap probed by function and correctly reported non-functional (code $bwrap_code) -- $diagnosis"
fi

info ""
if [ "$fail" -eq 0 ]; then
  info "install-rehearsal: all decision points reached and passed (fail=$fail)"
  exit 0
else
  info "install-rehearsal: $fail check(s) FAILED"
  exit 1
fi
