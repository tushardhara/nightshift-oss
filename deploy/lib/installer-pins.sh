#!/usr/bin/env bash
# Versions and sha256 of every artifact deploy/provision-ubuntu.sh downloads.
# Sourced, never executed.
#
# The hashes live HERE, in the repository, on purpose. A checksum fetched at
# install time from the origin that serves the artifact proves only that the
# origin agrees with itself; a compromised origin or CDN serves both halves.
# A hash recorded in review is the one thing such an attacker cannot change.
#
# Bumping a pin: download the artifact over HTTPS, compare its sha256 with the
# publisher's (go.dev/dl JSON, the GitHub release .sha256 / sha256sums.txt,
# static.rust-lang.org .sha256), and only then edit the value here. Apt keys:
# check `gpg --show-keys` fingerprints against the vendor's documentation.
# A mismatch at install time is a hard stop, never a retry.

# Go: https://go.dev/dl/go${VER}.linux-${ARCH}.tar.gz (released 2026-11-10)
NS_PIN_GO_VERSION=1.27.4
NS_PIN_GO_SHA256_amd64=7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840
NS_PIN_GO_SHA256_arm64=3392582988a273a87ab5043ee1b2c9c17a2c3c00580bf9326c47a2e0aaaeac92

# uv: github.com/astral-sh/uv/releases/download/${VER}/uv-${TRIPLE}.tar.gz
NS_PIN_UV_VERSION=0.13.6
NS_PIN_UV_SHA256_amd64=f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645
NS_PIN_UV_SHA256_arm64=229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d

# rustup-init: static.rust-lang.org/rustup/archive/${VER}/${TRIPLE}/rustup-init.
# rustup then fetches the pinned toolchain and checks each component against
# the sha256 in that release's dist manifest (TLS-trusted, not pinned here).
NS_PIN_RUSTUP_VERSION=1.29.3
NS_PIN_RUSTUP_SHA256_amd64=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55
NS_PIN_RUSTUP_SHA256_arm64=9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282
NS_PIN_RUST_TOOLCHAIN=1.99.1

# floci (third party): github.com/floci-io/floci-cli/releases/download/${VER}/floci-linux-${ARCH}
NS_PIN_FLOCI_VERSION=0.4.0
NS_PIN_FLOCI_SHA256_amd64=cdf4603e169a030b57d18b2fff6ef3f6a4ad45a2bbf137992cb9cf691c4620f8
NS_PIN_FLOCI_SHA256_arm64=62b2d1c202f5041d545e5ae681a38dc9dc148fb355a0b8b203dcae0b0951637b

# Apt repository signing keys, as served. Fingerprints at time of pinning:
#   docker     9DC858229FC7DD38854AE2D88D81803C0EBFCD88
#   gh         2C6106201985B60E6C7AC87323F3D4EA75716059, 7F38BBB59D064DBCB3D84D725612B36462313325
#   nodesource 6F71F525282841EEDAF851B42F59B5F99B1BE0B4
#   tailscale  2596A99EAAB33821893C0A79458CA832957F5868 (same file for noble and jammy)
NS_PIN_KEY_DOCKER_SHA256=1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570
NS_PIN_KEY_GH_SHA256=6084d5d7bd8e288441e0e94fc6275570895da18e6751f70f057485dc2d1a811b
NS_PIN_KEY_NODESOURCE_SHA256=b42e0321dabdc24e892115da705cf061167eac12a317f23d329862d0aa0a271d
NS_PIN_KEY_TAILSCALE_SHA256=3e03dacf222698c60b8e2f990b809ca1b3e104de127767864284e6c228f1fb39

# nightshift_pin_sha256 <NAME> <dpkg-arch>  -> the pinned hash, or "" if none
nightshift_pin_sha256() {
  local var="NS_PIN_${1}_SHA256_${2}"
  printf '%s\n' "${!var:-}"
}

# nightshift_rust_triple <dpkg-arch>  -> the Rust/uv target triple
nightshift_rust_triple() {
  case "$1" in
    amd64) echo x86_64-unknown-linux-gnu ;;
    arm64) echo aarch64-unknown-linux-gnu ;;
    *) echo "no pinned artifacts for architecture '$1'" >&2; return 1 ;;
  esac
}

# nightshift_fetch_verified <url> <sha256> <dest> [curl_cmd]
#   Download to <dest>.part, check it against the pinned sha256, and only then
#   move it to <dest>. On any failure nothing is left at <dest> and the reason
#   is printed. An empty or malformed pin refuses before any download.
nightshift_fetch_verified() {
  local url="$1" want="$2" dest="$3" curl_cmd="${4:-curl}" got
  if [ "${#want}" -ne 64 ] || [ -n "${want//[0-9a-f]/}" ]; then
    echo "no valid pinned sha256 for $url — refusing to install an unverified artifact" >&2
    return 1
  fi
  rm -f "$dest.part"
  if ! "$curl_cmd" -fsSL "$url" -o "$dest.part"; then
    rm -f "$dest.part"
    echo "download failed: $url" >&2
    return 1
  fi
  got="$(sha256sum "$dest.part" | awk '{print $1}')"
  if [ "$got" != "$want" ]; then
    rm -f "$dest.part"
    echo "sha256 mismatch for $url — refusing to install it (corrupted, tampered with, or the pin needs a reviewed bump)" >&2
    echo "  pinned:   $want" >&2
    echo "  received: $got" >&2
    return 1
  fi
  mv -f "$dest.part" "$dest"
}
