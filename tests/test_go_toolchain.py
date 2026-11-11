"""Go toolchain setup must survive a fresh box — DoD conditions 1 and 2, issue #8.

`deploy/provision-ubuntu.sh` runs under `set -euo pipefail`. Three defects on the
same fresh-box path, filed as one item because one PR should fix all of them:

**Bug 1, site A** — `go_want=$(curl -fsSL '…/VERSION?m=text' | head -1 | sed …)`.
`head` exits after line 1, `curl` gets a closed pipe and can exit 23, `pipefail`
fails the whole substitution even though the first line was perfectly valid, and
`set -e` aborts BEFORE the explicit empty-value diagnostic that follows it.

**Bug 1, site B** — the checksum lookup `curl … | jq … | head -1`. *Same defect,
second site*, found by the Codex architect during discovery. Fixing only site A
ships a half-fix that fails identically on the next box, and the DoD says so.

**Bug 2** — `go_have=$(/usr/local/go/bin/go version 2>/dev/null | awk … | sed …)`.
On a fresh box that path does not exist, the substitution returns non-zero, and
`set -e` exits before the comparison and install branch. The `${go_have:-0}`
fallback only *appears* to handle a fresh install — control never reaches it.

Requirement ids:
  RT-01  version discovery under `set -euo pipefail`, producer cut off by its
         consumer, resolves a version and exits 0            (DoD condition 1)
  RT-02  the checksum lookup, same                            (DoD condition 1)
  RT-03  no Go present -> the probe returns cleanly and provisioning proceeds
         to the install decision                              (DoD condition 2)
  RT-04  a genuinely unresolvable version still fails loudly, naming
         NIGHTSHIFT_GO_VERSION                                (DoD condition 2)

PINNED SINCE the public-release audit: provisioning no longer resolves "latest"
from go.dev nor takes its checksum from go.dev. Version and sha256 come from
deploy/lib/installer-pins.sh, and an override must bring its own sha256. The
library helpers keep their unit tests (RT-01/02/04 below); the integration
tests drive the pinned section instead.

ISOLATION, which DoD condition 2 requires the manifest to record: every test
passes an explicit binary/command path, so no test can read or write the host
`/usr/local/go`, and no test performs network I/O. The fake `curl` is a local
script; the "network" is a temp file. `HOST_GO_UNTOUCHED` asserts this rather
than asserting it in a comment.
"""

import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(REPO, "deploy")
LIB = os.path.join(DEPLOY, "lib", "deploy-lib.sh")
PINS = os.path.join(DEPLOY, "lib", "installer-pins.sh")
PROVISION = os.path.join(DEPLOY, "provision-ubuntu.sh")


def _pinned_go():
    with open(PINS) as fh:
        return re.search(r"^NS_PIN_GO_VERSION=(\S+)$", fh.read(), re.M).group(1)


PINNED_GO = _pinned_go()

# A `curl` that behaves like the real one against go.dev/VERSION?m=text: a valid
# first line, then a lot more body. `trap '' PIPE` turns SIGPIPE into a failed
# write so we can return curl's own exit 23 rather than dying on the signal —
# which is exactly the status that makes `pipefail` poison the substitution.
FAKE_CURL_VERSION = r"""#!/usr/bin/env bash
trap '' PIPE
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
emit() {
  printf 'go1.25.1\n'                        || exit 23
  printf 'time 2026-01-01T00:00:00Z\n'       || exit 23
  for i in $(seq 1 400); do
    printf 'padding %s %s\n' "$i" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" || exit 23
  done
}
if [ -n "$out" ]; then emit > "$out"; else emit; fi
exit 0
"""

# The go.dev release index. Two entries for the same filename on purpose, so a
# fix that forgets to take only the first is caught.
FAKE_CURL_JSON = r"""#!/usr/bin/env bash
trap '' PIPE
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
emit() {
  cat <<'JSON'
[{"version":"go1.25.1","files":[
   {"filename":"go1.25.1.linux-amd64.tar.gz","sha256":"aaaa1111"},
   {"filename":"go1.25.1.linux-arm64.tar.gz","sha256":"bbbb2222"}]},
 {"version":"go1.25.0","files":[
   {"filename":"go1.25.1.linux-amd64.tar.gz","sha256":"dddd4444"}]}]
JSON
  for i in $(seq 1 400); do
    printf '\n' || exit 23
  done
}
if [ -n "$out" ]; then emit > "$out"; else emit; fi
exit 0
"""

FAKE_CURL_HTTP_FAIL = """#!/usr/bin/env bash
echo "curl: (22) The requested URL returned error: 404" >&2
exit 22
"""

FAKE_CURL_EMPTY = r"""#!/usr/bin/env bash
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
if [ -n "$out" ]; then : > "$out"; fi
exit 0
"""

FAKE_GO_GOOD = """#!/usr/bin/env bash
echo "go version go1.24.3 linux/amd64"
"""

FAKE_GO_GARBAGE = """#!/usr/bin/env bash
echo "corrupted binary output <<>>"
"""


def _script(dirpath, name, body):
    p = os.path.join(dirpath, name)
    with open(p, "w") as fh:
        fh.write(body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def bash(snippet, strict=True):
    """Run a snippet against the lib under the SAME flags provisioning uses.

    `set -euo pipefail` is not decoration here: it is the precondition of the
    entire bug. A test that dropped it would pass against the broken code.
    """
    flags = "set -euo pipefail; " if strict else ""
    p = subprocess.run(
        ["bash", "-c", f'{flags}. "{LIB}"; {snippet}'],
        capture_output=True, text=True, timeout=120,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


# ---------------------------------------------------------------------------
# INTEGRATION HARNESS — drives the REAL deploy/provision-ubuntu.sh go section.
#
# crew_alpha refused the first evidence for this PR and was right to: every
# capture read `nightshift_go_resolve_version: command not found`, which proves
# only that a function added by the fix did not exist before. ANY refactor
# produces that, bug or no bug. There IS pre-fix behaviour to demonstrate here —
# `provision-ubuntu.sh` demonstrably ABORTS under `set -euo pipefail` at
# 139726e — so the baseline must show the abort, not the absence of a helper.
#
# ISOLATION, which DoD condition 2 requires the manifest to record: the block is
# extracted from the real script and every `/usr/local/` path is rewritten to a
# per-test fixture root before execution, so the test can neither read nor write
# the host `/usr/local/go` (which on the build host is a real Go 1.27.0 install
# and would otherwise make the result depend on the machine). `curl` and the Go
# binary are stubs on PATH. No network.
# ---------------------------------------------------------------------------

GO_BLOCK_START = 'say "go toolchain"'
GO_BLOCK_END = "ln -sfn /usr/local/go/bin/gofmt"

CURL_STUB = r"""#!/usr/bin/env bash
trap '' PIPE
out=""; prev=""; url=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  case "$a" in https://*) url="$a" ;; esac
  prev="$a"
done
[ -n "${NS_CURL_LOG:-}" ] && printf '%s\n' "$url" >> "$NS_CURL_LOG"
emit_big_version() {
  printf 'go1.25.1
' || exit 23
  for i in $(seq 1 400); do printf 'pad %s aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
' "$i" || exit 23; done
}
emit_big_json() {
  printf '[{"version":"go1.25.1","files":[{"filename":"%s","sha256":"%s"}]}' "$NS_GO_FILE" "$NS_TGZ_SHA" || exit 23
  for i in $(seq 1 400); do printf ',{"version":"x","files":[]}' || exit 23; done
  printf ']
' || exit 23
}
case "$url" in
  *VERSION*)
    case "${NS_CURL_VERSION_MODE:-big}" in
      fail)  echo "curl: (22) 404" >&2; exit 22 ;;
      empty) if [ -n "$out" ]; then : > "$out"; fi; exit 0 ;;
      *)     if [ -n "$out" ]; then emit_big_version > "$out"; else emit_big_version; fi ;;
    esac ;;
  *mode=json*)
    if [ "${NS_CURL_JSON_MODE:-ok}" = fail ]; then echo "curl: (22) 404" >&2; exit 22; fi
    if [ -n "$out" ]; then emit_big_json > "$out"; else emit_big_json; fi ;;
  *.tar.gz*)
    if [ -n "$out" ]; then printf '%s' "$NS_TGZ_BODY" > "$out"; fi ;;
esac
exit 0
"""

TAR_STUB = "#!/usr/bin/env bash\nexit 0\n"


def go_block(repo):
    """Extract the go-toolchain block from the real provisioning script."""
    with open(os.path.join(repo, "deploy", "provision-ubuntu.sh"),
              errors="replace") as fh:
        lines = fh.read().splitlines(True)
    a = next(i for i, l in enumerate(lines) if GO_BLOCK_START in l)
    b = next(i for i, l in enumerate(lines) if GO_BLOCK_END in l)
    return "".join(lines[a:b + 1])


def run_go_section(version_mode="big", override="", go_version=None,
                   json_mode="ok", sha_mode="none"):
    """Run the REAL go section against stubs.

    Returns (code, output, reached_end, urls_requested). `sha_mode` is what
    NIGHTSHIFT_GO_SHA256 carries: "none" (unset), "match" (the stub tarball's
    real sha256) or "wrong"."""
    import hashlib
    d = tempfile.mkdtemp()
    binp = os.path.join(d, "bin"); os.makedirs(binp)
    fake_root = os.path.join(d, "root"); os.makedirs(os.path.join(fake_root, "usr/local/bin"))
    curl_log = os.path.join(d, "curl.log")

    tgz_body = "fake-go-tarball-bytes"
    tgz_sha = hashlib.sha256(tgz_body.encode()).hexdigest()

    _script(binp, "curl", CURL_STUB)
    _script(binp, "tar", TAR_STUB)
    if go_version is not None:                       # a Go install exists
        gobin = os.path.join(fake_root, "usr/local/go/bin")
        os.makedirs(gobin)
        _script(gobin, "go", f'#!/usr/bin/env bash\necho "go version go{go_version} linux/amd64"\n')

    block = go_block(REPO).replace("/usr/local/", fake_root + "/usr/local/")
    arch = _arch()
    script = (
        'set -euo pipefail\n'
        f'. "{LIB}"\n'
        f'. "{PINS}"\n'
        f'ARCH={arch}\n'
        'say() { printf "=== %s\\n" "$1"; }' "\n"
        f'{block}\n'
        'echo "REACHED_END_OF_GO_SECTION"\n'
    )
    env = dict(os.environ)
    env["PATH"] = binp + os.pathsep + env["PATH"]
    env["NS_CURL_VERSION_MODE"] = version_mode
    env["NS_CURL_JSON_MODE"] = json_mode
    env["NS_CURL_LOG"] = curl_log
    env["NS_TGZ_BODY"] = tgz_body
    env["NS_TGZ_SHA"] = tgz_sha
    env["NS_GO_FILE"] = f"go1.25.1.linux-{arch}.tar.gz"
    env["NIGHTSHIFT_GO_VERSION"] = override
    env.pop("NIGHTSHIFT_GO_SHA256", None)
    if sha_mode == "match":
        env["NIGHTSHIFT_GO_SHA256"] = tgz_sha
    elif sha_mode == "wrong":
        env["NIGHTSHIFT_GO_SHA256"] = "0" * 64
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env=env, timeout=180)
    out = p.stdout + p.stderr
    urls = []
    if os.path.exists(curl_log):
        with open(curl_log) as fh:
            urls = [ln.strip() for ln in fh if ln.strip()]
    return p.returncode, out, "REACHED_END_OF_GO_SECTION" in out, urls


def _arch():
    return subprocess.run(["dpkg", "--print-architecture"],
                          capture_output=True, text=True).stdout.strip()


class RT01VersionDiscoverySurvivesPipefail(unittest.TestCase):
    """RT-01 — DoD condition 1, site A."""

    def test_rt01_the_real_provisioning_script_does_not_abort(self):
        """The real section, under `set -euo pipefail`, on a box whose Go is
        already at the pin: completes, and asks the network NOTHING -- there
        is no version discovery left to be cut off by its consumer."""
        code, out, reached, urls = run_go_section(go_version=PINNED_GO)
        self.assertTrue(reached,
                        f"the real go section aborted before completing:\n{out}")
        self.assertEqual(code, 0, out)
        self.assertEqual(urls, [], "a current pinned Go must not touch the network")


    def test_rt01_resolves_a_version_under_set_euo_pipefail(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_VERSION)
            code, out, err = bash(f'nightshift_go_resolve_version "{curl}"')
        self.assertEqual(code, 0, f"version discovery aborted under pipefail: {err}")
        self.assertEqual(out, "1.25.1")

    def test_rt01_the_producer_is_never_cut_off_by_its_consumer(self):
        # The mechanism, pinned: no early-closing consumer may sit downstream of
        # the network producer. Proven behaviourally — the fake curl returns 23
        # if anything closes the pipe on it, so a `| head -1` implementation
        # cannot pass this test.
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_VERSION)
            for _ in range(5):     # repeat: SIGPIPE timing is a race
                code, out, _ = bash(f'nightshift_go_resolve_version "{curl}"')
                self.assertEqual(code, 0)
                self.assertEqual(out, "1.25.1")


class RT02ChecksumLookupSurvivesPipefail(unittest.TestCase):
    """RT-02 — DoD condition 1, site B. The half-fix guard."""

    def test_rt02_the_real_script_completes_the_checksum_path(self):
        """Site B on the happy path: an OLD Go is present, so control enters the
        download + checksum branch and must come out the other side."""
        code, out, reached, _ = run_go_section(override="1.25.1", go_version="1.20.0",
                                               sha_mode="match")
        self.assertTrue(reached,
                        f"the real go section aborted in the checksum path:\n{out}")
        self.assertEqual(code, 0, out)
        self.assertIn("sha256 verified", out)

    def test_rt02_the_real_script_reaches_its_unverified_toolchain_refusal(self):
        """Site B's REACHABLE defect, and the honest one.

        Measured against the live go.dev index: every filename appears exactly
        once, so `jq` emits ~65 bytes and `head -1` never fills the 64 KB pipe
        buffer. SIGPIPE at this site needs >=100 duplicate entries and is
        therefore NOT reproducible with real data - it is a latent instance of
        the shape, which is exactly the word the DoD uses, and the structural
        assertion in BothSitesFixed is its proof.

        What IS reachable here is the same consequence as site A: when the
        request fails, `pipefail` poisons the substitution and `set -e` kills
        the script BEFORE the explicit refusal below it, so an operator sees a
        bare non-zero exit instead of "refusing to install an unverified
        toolchain". That diagnostic must actually be emitted.

        Pinned form: a tarball that does not match the operator-supplied hash
        is refused, with the refusal printed."""
        code, out, reached, _ = run_go_section(override="1.25.1", go_version="1.20.0",
                                               sha_mode="wrong")
        self.assertNotEqual(code, 0, "a checksum mismatch must remain a hard failure")
        self.assertFalse(reached, "it must not install an unverified toolchain")
        self.assertIn("sha256 mismatch", out)
        self.assertIn("refusing to install an unverified toolchain", out,
                      f"the refusal never printed - `set -e` killed the script "
                      f"above it:\n{out}")


    def test_an_override_without_its_own_hash_is_refused_before_download(self):
        """The hash for a non-pinned version is the operator's to supply.
        Fetching it from go.dev would reintroduce the same-origin check."""
        code, out, reached, urls = run_go_section(override="1.25.1", go_version="1.20.0")
        self.assertNotEqual(code, 0)
        self.assertFalse(reached)
        self.assertIn("NIGHTSHIFT_GO_SHA256", out)
        self.assertEqual(urls, [], "nothing may be downloaded without a hash to check it")

    def test_rt02_resolves_a_checksum_under_set_euo_pipefail(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_JSON)
            code, out, err = bash(
                f'nightshift_go_checksum "go1.25.1.linux-amd64.tar.gz" "{curl}"')
        self.assertEqual(code, 0, f"checksum lookup aborted under pipefail: {err}")
        self.assertEqual(out, "aaaa1111", "must take the FIRST matching entry")

    def test_rt02_a_missing_checksum_entry_is_a_hard_failure(self):
        # Silently installing an unverified toolchain is the outcome the
        # original check exists to prevent; the fix must not soften it.
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_JSON)
            code, out, _ = bash(
                f'nightshift_go_checksum "go9.9.9.linux-amd64.tar.gz" "{curl}"')
        self.assertNotEqual(code, 0, "an unknown file must not yield a checksum")
        self.assertEqual(out, "")

    def test_rt02_http_failure_still_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_HTTP_FAIL)
            code, out, _ = bash(
                f'nightshift_go_checksum "go1.25.1.linux-amd64.tar.gz" "{curl}"')
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")


class RT03MissingGoIsANormalState(unittest.TestCase):
    """RT-03 — DoD condition 2. Absence means "install Go", never "stop"."""

    def test_rt03_the_real_script_installs_instead_of_aborting(self):
        """Bug 2 against the real script: with NO Go present the pre-fix
        substitution returned non-zero and `set -e` exited before the install
        branch could run."""
        code, out, reached, _ = run_go_section(override="1.25.1", go_version=None,
                                               sha_mode="match")
        self.assertTrue(reached,
                        f"a fresh box aborted provisioning:\n{out}")
        self.assertEqual(code, 0, out)
        self.assertIn("installed go 1.25.1", out)


    def test_rt03_absent_binary_probes_cleanly_under_pipefail(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "no-go-here", "bin", "go")
            code, out, err = bash(f'nightshift_go_installed_version "{missing}"')
        self.assertEqual(code, 0,
                         f"a fresh box must not abort the installer: {err}")
        self.assertEqual(out, "", "nothing installed means empty, not an error")

    def test_rt03_provisioning_proceeds_to_the_install_decision(self):
        # The composed sequence, exactly as provisioning runs it: probe, then
        # decide. Pre-fix, control never reached the decision at all.
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "absent", "go")
            code, out, err = bash(
                f'have="$(nightshift_go_installed_version "{missing}")"; '
                f'if nightshift_version_ge "${{have:-0}}" "1.25.1"; '
                f'then echo DECISION=skip; else echo DECISION=install; fi')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "DECISION=install")

    def test_rt03_an_up_to_date_install_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            go = _script(d, "go", FAKE_GO_GOOD)
            code, out, err = bash(
                f'have="$(nightshift_go_installed_version "{go}")"; '
                f'if nightshift_version_ge "${{have:-0}}" "1.24.0"; '
                f'then echo DECISION=skip; else echo DECISION=install; fi')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "DECISION=skip")

    def test_rt03_a_present_but_unparseable_binary_is_invalid_not_trusted(self):
        # Issue #8: do NOT append `|| true` — that would conceal corrupt
        # binaries. A garbled `go version` must be distinguishable from absence.
        with tempfile.TemporaryDirectory() as d:
            go = _script(d, "go", FAKE_GO_GARBAGE)
            code, out, _ = bash(f'nightshift_go_installed_version "{go}" || echo BROKEN')
        self.assertEqual(code, 0)
        self.assertIn("BROKEN", out,
                      "a corrupt toolchain must not read as a valid version")


class RT04UnresolvableVersionFailsLoudly(unittest.TestCase):
    """RT-04 — DoD condition 2. Loud, and it names the override."""

    def test_rt04_the_real_script_reaches_its_loud_diagnostic(self):
        """The default path against the IN-REPO pin: on a fresh box it fetches
        exactly the pinned tarball -- no VERSION lookup, no checksum index --
        and a body that does not hash to the pin is refused out loud."""
        code, out, reached, urls = run_go_section(go_version=None)
        self.assertNotEqual(code, 0, "a tarball that misses the pin must fail")
        self.assertFalse(reached, "it must not install an unverified toolchain")
        self.assertIn("sha256 mismatch", out,
                      f"the diagnostic never printed - `set -e` killed the "
                      f"script above it:\n{out}")
        self.assertEqual(urls, [f"https://go.dev/dl/go{PINNED_GO}.linux-{_arch()}.tar.gz"])


    def test_rt04_empty_response_fails(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_EMPTY)
            code, out, _ = bash(f'nightshift_go_resolve_version "{curl}"')
        self.assertNotEqual(code, 0, "an empty response must not resolve")
        self.assertEqual(out, "")

    def test_rt04_http_failure_fails(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_HTTP_FAIL)
            code, out, _ = bash(f'nightshift_go_resolve_version "{curl}"')
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_rt04_the_diagnostic_is_REACHED_and_names_the_override(self):
        """The heart of bug 1, and the reason this assertion is behavioural.

        Pre-fix the diagnostic existed in the script and was simply
        UNREACHABLE: `set -e` killed the substitution above it. Asserting the
        text exists in the file would therefore have passed against the broken
        code — it did, which is why this test was rewritten. What must be
        proven is that the message is actually PRINTED on the failure path.
        """
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_HTTP_FAIL)
            code, out, err = bash(f'nightshift_go_want_version "{curl}" ""')
        self.assertNotEqual(code, 0, "an unresolvable version must fail")
        self.assertIn("NIGHTSHIFT_GO_VERSION", err,
                      f"the diagnostic must be reached and name the override; "
                      f"stderr was: {err!r}")
        self.assertEqual(out, "")

    def test_rt04_an_explicit_override_short_circuits_the_network(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_HTTP_FAIL)
            code, out, err = bash(f'nightshift_go_want_version "{curl}" "1.23.4"')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "1.23.4")

    def test_rt04_the_happy_path_still_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            curl = _script(d, "curl", FAKE_CURL_VERSION)
            code, out, err = bash(f'nightshift_go_want_version "{curl}" ""')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "1.25.1")


class BothSitesFixed(unittest.TestCase):
    """DoD condition 1 explicitly: fixing only the first site is a half-fix."""

    PIPE_TO_EARLY_CONSUMER = re.compile(r"curl[^\n|]*\|[^\n]*\bhead\b")

    def test_no_curl_pipes_into_an_early_closing_consumer(self):
        with open(PROVISION, errors="replace") as fh:
            lines = fh.readlines()
        # join continuations so a pipeline split over two lines is still seen
        joined, buf = [], ""
        for n, ln in enumerate(lines, 1):
            code = ln.split("#", 1)[0].rstrip("\n")
            if code.rstrip().endswith("\\"):
                buf += code.rstrip()[:-1] + " "
                continue
            joined.append((n, buf + code))
            buf = ""
        offenders = [f"{n}: {c.strip()}" for n, c in joined
                     if self.PIPE_TO_EARLY_CONSUMER.search(c)]
        self.assertEqual(
            offenders, [],
            "a network producer still feeds an early-closing consumer under "
            "`pipefail` — this is the defect, at whichever site:\n  "
            + "\n  ".join(offenders))

    def test_pipefail_is_not_disabled_anywhere(self):
        # Explicitly forbidden by issue #8: it would hide genuine network failures.
        with open(PROVISION, errors="replace") as fh:
            lines = fh.readlines()
        offenders = [f"{n}: {ln.strip()}" for n, ln in enumerate(lines, 1)
                     if re.search(r"set\s+\+o\s+pipefail|set\s+\+e\b",
                                  ln.split("#", 1)[0])]
        self.assertEqual(offenders, [],
                         "pipefail/errexit disabled — hides real failures:\n  "
                         + "\n  ".join(offenders))

    def test_provisioning_uses_the_tested_helpers(self):
        with open(PROVISION, errors="replace") as fh:
            body = fh.read()
        for fn in ("nightshift_go_installed_version",
                   "nightshift_pin_sha256",
                   "nightshift_fetch_verified"):
            self.assertTrue(fn in body,
                            f"provision-ubuntu.sh must go through {fn}()")

    def test_provisioning_never_asks_go_dev_for_a_version_or_a_checksum(self):
        with open(PROVISION, errors="replace") as fh:
            code = "\n".join(ln.split("#", 1)[0] for ln in fh)
        for same_origin in ("go.dev/VERSION", "mode=json",
                            "nightshift_go_want_version", "nightshift_go_checksum"):
            self.assertNotIn(same_origin, code,
                             f"provisioning still consults go.dev via {same_origin!r}")


class HostGoUntouched(unittest.TestCase):
    """The isolation claim, asserted rather than promised (DoD condition 2)."""

    def test_no_test_here_references_the_host_go_path(self):
        with open(os.path.abspath(__file__), errors="replace") as fh:
            body = fh.read()
        # The only permitted mention is the default argument documented in the
        # library, never a path a test actually drives.
        # Look for an actual CALL against the host path, not a mention of it.
        # (An earlier version of this check matched its own source line, which
        # is a good reminder that a scanner must be tested like anything else.)
        call = re.compile(r"nightshift_go_installed_version\s+\"?/usr/local/go")
        drives = [ln.strip() for ln in body.splitlines() if call.search(ln)]
        self.assertEqual(drives, [],
                         f"a test drives the host Go install: {drives}")
        # Prove the scanner can actually fire. The probe is assembled at runtime
        # so this line does not itself look like an offender — a scanner whose
        # positive control trips it is a scanner that cannot be trusted either way.
        probe = "nightshift_go_installed_version " + chr(34) + "/usr/local/" + "go/bin/go"
        self.assertTrue(call.search(probe),
                        "the isolation scanner matches nothing — it would pass vacuously")


if __name__ == "__main__":
    unittest.main()
