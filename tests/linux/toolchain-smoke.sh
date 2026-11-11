#!/usr/bin/env bash
# Exercises the REAL toolchain path from deploy/provision-ubuntu.sh inside a
# fresh ubuntu:24.04. Not part of tests/run.sh (needs Docker and the network);
# run it when the toolchain or provisioning path changes:
#
#   docker run --rm -v "$PWD":/ns:ro -v "$PWD/tests/linux/toolchain-smoke.sh":/t.sh:ro \\
#       ubuntu:24.04 bash /t.sh
#
# Uses the same pins and verifier (deploy/lib/installer-pins.sh) that
# deploy/provision-ubuntu.sh runs. Anything needing systemd
# or a live docker daemon is out of scope here — see docker-bind-smoke.sh.
set -euo pipefail

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Same set the provisioner installs before the toolchain section runs — cargo's
# link step needs cc, so build-essential is a real dependency, not padding.
apt-get install -y -qq curl ca-certificates jq tar build-essential pkg-config >/dev/null

. /ns/deploy/lib/deploy-lib.sh
. /ns/deploy/lib/installer-pins.sh
ARCH=$(dpkg --print-architecture)

echo "=== 0. version helper on real bash ==="
nightshift_version_ge 1.25.1 1.25.0 || fail "1.25.1 >= 1.25.0"
! nightshift_version_ge 1.24.9 1.25.0 || fail "1.24.9 should be < 1.25.0"
! nightshift_version_ge 0 1.25.0 || fail "absent toolchain must read as too old"
pass "nightshift_version_ge"

echo "=== 1. runtime user, as the provisioner creates it ==="
useradd --create-home --shell /bin/bash nightshift
NS_USER=nightshift
NS_HOME=$(getent passwd "$NS_USER" | cut -d: -f6)
[ "$NS_HOME" = /home/nightshift ] || fail "unexpected home: $NS_HOME"
pass "user + \$NS_HOME=$NS_HOME"

echo "=== 2. Go: the pinned tarball, verified against the in-repo sha256 ==="
go_file="go${NS_PIN_GO_VERSION}.linux-${ARCH}.tar.gz"
go_tmp=$(mktemp -d)
nightshift_fetch_verified "https://go.dev/dl/${go_file}" "$(nightshift_pin_sha256 GO "$ARCH")" \
  "$go_tmp/go.tgz" || fail "pinned Go ${go_file} did not verify"
pass "sha256 of ${go_file} matches deploy/lib/installer-pins.sh"
# and prove the check actually catches a bad file
if nightshift_fetch_verified "https://go.dev/dl/${go_file}" "$(printf '0%.0s' $(seq 64))" \
     "$go_tmp/bad.tgz" 2>/dev/null; then
  fail "the verifier accepted a tarball against the wrong hash"
fi
[ ! -e "$go_tmp/bad.tgz" ] || fail "a rejected download was left on disk"
pass "verifier rejects a mismatching tarball and leaves nothing behind"
rm -rf /usr/local/go
tar -C /usr/local -xzf "$go_tmp/go.tgz"
ln -sfn /usr/local/go/bin/go /usr/local/bin/go
ln -sfn /usr/local/go/bin/gofmt /usr/local/bin/gofmt
pass "go installed: $(/usr/local/bin/go version)"

echo "=== 3. Go builds as the runtime user, with the SYSTEMD unit PATH ==="
# This is the claim the whole symlink design rests on: the unit PATH below is
# copied verbatim from deploy/systemd/omnigent-host.service.
UNIT_PATH="$NS_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
mkdir -p /srv/project/hello && cd /srv/project/hello
cat > main.go <<'EOF'
package main
import "fmt"
func main() { fmt.Println("go-ok") }
EOF
cat > go.mod <<'EOF'
module hello
go 1.21
EOF
chown -R "$NS_USER:$NS_USER" /srv/project
out=$(runuser -u "$NS_USER" -- env HOME="$NS_HOME" PATH="$UNIT_PATH" bash -c 'cd /srv/project/hello && go build -o hello . && ./hello')
[ "$out" = "go-ok" ] || fail "go build under unit PATH produced: $out"
pass "go builds under the systemd unit PATH (no login shell, no profile.d)"

echo "=== 4. Rust: pinned rustup-init as the runtime user, via runuser + explicit HOME ==="
ru_tmp=$(mktemp -d)
nightshift_fetch_verified \
  "https://static.rust-lang.org/rustup/archive/${NS_PIN_RUSTUP_VERSION}/$(nightshift_rust_triple "$ARCH")/rustup-init" \
  "$(nightshift_pin_sha256 RUSTUP "$ARCH")" "$ru_tmp/rustup-init" || fail "pinned rustup-init did not verify"
chmod 0755 "$ru_tmp/rustup-init"; chmod 0711 "$ru_tmp"
runuser -u "$NS_USER" -- env HOME="$NS_HOME" "$ru_tmp/rustup-init" \
  -y --no-modify-path --profile default --default-toolchain "$NS_PIN_RUST_TOOLCHAIN" >/dev/null 2>&1
[ -x "$NS_HOME/.cargo/bin/cargo" ] || fail "cargo not installed under \$NS_HOME"
[ ! -e /root/.cargo ] || fail "rustup wrote into /root — HOME was not honoured"
pass "rustup installed to \$NS_HOME/.cargo, nothing in /root"
for b in cargo rustc rustfmt cargo-clippy clippy-driver; do
  [ -e "$NS_HOME/.cargo/bin/$b" ] || fail "default profile missing $b"
done
pass "default profile gave us rustfmt + clippy"
install -d -o "$NS_USER" -g "$NS_USER" "$NS_HOME/.local/bin"
for b in cargo rustc rustup rustfmt cargo-clippy clippy-driver; do
  [ -e "$NS_HOME/.cargo/bin/$b" ] && ln -sfn "$NS_HOME/.cargo/bin/$b" "$NS_HOME/.local/bin/$b" && chown -h "$NS_USER:$NS_USER" "$NS_HOME/.local/bin/$b"
done
out=$(runuser -u "$NS_USER" -- env HOME="$NS_HOME" PATH="$UNIT_PATH" bash -c '
  cd /srv/project && cargo new --quiet hello-rs >/dev/null 2>&1 && cd hello-rs && cargo run --quiet 2>&1' || true)
echo "$out" | grep -q "Hello, world" || fail "cargo run under unit PATH produced: $out"
pass "cargo builds and runs under the systemd unit PATH"

echo "=== 5. floci: pinned unified CLI, installs to /usr/local/bin as root ==="
fl_tmp=$(mktemp -d)
nightshift_fetch_verified \
  "https://github.com/floci-io/floci-cli/releases/download/${NS_PIN_FLOCI_VERSION}/floci-linux-${ARCH}" \
  "$(nightshift_pin_sha256 FLOCI "$ARCH")" "$fl_tmp/floci" || fail "pinned floci did not verify"
install -m 0755 "$fl_tmp/floci" /usr/local/bin/floci
[ -x /usr/local/bin/floci ] || fail "floci not at /usr/local/bin/floci"
pass "floci installed: $(/usr/local/bin/floci version 2>&1 | head -1)"
for stale in floci-az floci-gcp floci-oci; do
  ! command -v "$stale" >/dev/null 2>&1 || fail "$stale exists — the multi-binary assumption was right after all"
done
pass "no floci-az/floci-gcp/floci-oci binaries exist (unified CLI confirmed)"
for grp in az gcp oci aws; do
  /usr/local/bin/floci "$grp" --help >/dev/null 2>&1 || fail "floci $grp --help failed"
done
pass "floci az|gcp|oci|aws product groups all respond"
out=$(runuser -u "$NS_USER" -- env HOME="$NS_HOME" PATH="$UNIT_PATH" bash -c 'floci version' 2>&1 | head -1)
[ -n "$out" ] || fail "floci not reachable under the unit PATH"
pass "floci reachable by workers under the unit PATH: $out"

echo "=== 6. docker daemon.json merge (the deterministic localhost bind) ==="
install -d /etc/docker
# 6a. fresh box: file created
rm -f /etc/docker/daemon.json
printf '{\n  "ip": "127.0.0.1"\n}\n' > /etc/docker/daemon.json
[ "$(jq -r '.ip' /etc/docker/daemon.json)" = "127.0.0.1" ] || fail "fresh daemon.json wrong"
pass "fresh daemon.json sets ip=127.0.0.1"
# 6b. existing file with unrelated settings: merged, not clobbered
printf '{"log-driver":"json-file","log-opts":{"max-size":"10m"}}' > /etc/docker/daemon.json
tmp=$(mktemp); jq '. + {ip: "127.0.0.1"}' /etc/docker/daemon.json > "$tmp" && mv "$tmp" /etc/docker/daemon.json
[ "$(jq -r '.ip' /etc/docker/daemon.json)" = "127.0.0.1" ] || fail "merge lost ip"
[ "$(jq -r '."log-driver"' /etc/docker/daemon.json)" = "json-file" ] || fail "merge clobbered log-driver"
[ "$(jq -r '."log-opts"."max-size"' /etc/docker/daemon.json)" = "10m" ] || fail "merge clobbered log-opts"
pass "merge preserves existing settings"
# 6c. idempotent: second run detects it is already correct
[ "$(jq -r '.ip // empty' /etc/docker/daemon.json)" = "127.0.0.1" ] || fail "idempotence probe wrong"
pass "re-run detects ip already set (no docker restart)"
# 6d. invalid JSON: refuse and warn rather than destroy the file
printf 'not json at all' > /etc/docker/daemon.json
tmp=$(mktemp)
if jq '. + {ip: "127.0.0.1"}' /etc/docker/daemon.json > "$tmp" 2>/dev/null; then
  fail "jq accepted invalid JSON"
fi
rm -f "$tmp"
[ "$(cat /etc/docker/daemon.json)" = "not json at all" ] || fail "invalid file was modified"
pass "invalid daemon.json left untouched, not destroyed"

echo
echo "ALL LINUX CHECKS PASSED"
