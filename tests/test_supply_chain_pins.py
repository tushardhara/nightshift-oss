"""Supply-chain pins for everything the deploy path downloads.

Public-release audit, low "supply chain": the installers piped curl into a root
shell, the harness CLIs and Go floated to "latest", Go's checksum came from the
same origin as the tarball, and Postgres was a mutable tag. Each test here pins
one of those shut, by reading the file the installer actually runs.
"""

import hashlib
import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(REPO, "deploy")
PINS = os.path.join(DEPLOY, "lib", "installer-pins.sh")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Serves $NS_BODY for any URL and logs each URL it was asked for.
CURL_STUB = r"""#!/usr/bin/env bash
out=""; prev=""; url=""
for a in "$@"; do
  [ "$prev" = "-o" ] && out="$a"
  case "$a" in https://*) url="$a" ;; esac
  prev="$a"
done
printf '%s\n' "$url" >> "$NS_CURL_LOG"
[ -n "$out" ] && printf '%s' "$NS_BODY" > "$out"
exit 0
"""


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def code_lines(text):
    """Non-comment lines, so a pin named only in a comment does not count."""
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


class PostgresImageIsPinnedByDigest(unittest.TestCase):

    def test_every_image_carries_a_sha256_digest(self):
        images = [ln.split("image:", 1)[1].strip()
                  for ln in code_lines(read("deploy", "docker-compose.postgres.yml"))
                  if ln.strip().startswith("image:")]
        self.assertTrue(images, "compose file declares no image")
        for image in images:
            ref, sep, digest = image.partition("@sha256:")
            self.assertEqual(sep, "@sha256:", f"{image!r} is a mutable tag, not a digest")
            self.assertRegex(digest, SHA256)
            self.assertTrue(ref.startswith("postgres:17"), image)


def joined_code(path):
    """Code lines with comments dropped and backslash continuations joined."""
    out, buf = [], ""
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            code = ln.rstrip("\n")
            if code.lstrip().startswith("#"):
                continue
            if code.rstrip().endswith("\\"):
                buf += code.rstrip()[:-1] + " "
                continue
            out.append(buf + code)
            buf = ""
    return out


class ProvisioningDownloadsOnlyVerifiedArtifacts(unittest.TestCase):
    PROVISION = os.path.join(DEPLOY, "provision-ubuntu.sh")

    def test_nothing_is_piped_into_a_shell(self):
        offenders = [c for c in joined_code(self.PROVISION)
                     if re.search(r"\|\s*(sudo\s+)?(ba)?sh\b", c)]
        self.assertEqual(offenders, [], "curl|sh is back:\n" + "\n".join(offenders))

    def test_every_download_goes_through_the_verifier(self):
        # A bare curl here would be a download no pinned hash checks.
        offenders = [c for c in joined_code(self.PROVISION)
                     if re.search(r"\bcurl\s+(-|https?:|[\"'$])", c)]
        self.assertEqual(offenders, [], "unverified download:\n" + "\n".join(offenders))
        self.assertIn("nightshift_fetch_verified", "\n".join(joined_code(self.PROVISION)))

    def test_nodesource_and_tailscale_use_signed_apt_repositories(self):
        code = "\n".join(joined_code(self.PROVISION))
        for keyring, repo in (
                ("/usr/share/keyrings/nodesource.gpg", "https://deb.nodesource.com/node_22.x"),
                ("/usr/share/keyrings/tailscale-archive-keyring.gpg",
                 "https://pkgs.tailscale.com/stable/ubuntu")):
            self.assertRegex(code, re.escape(f"signed-by={keyring}]") + r"\s+" + re.escape(repo))
        for name in ("DOCKER", "GH", "NODESOURCE", "TAILSCALE"):
            self.assertIn(f"$NS_PIN_KEY_{name}_SHA256", code, f"{name} key is not hash-checked")

    def test_every_pin_is_a_full_sha256_for_both_architectures(self):
        text = read("deploy", "lib", "installer-pins.sh")
        pins = dict(re.findall(r"^(NS_PIN_\w+_SHA256\w*)=(\S*)$", text, re.M))
        self.assertTrue(pins)
        for name, value in pins.items():
            self.assertRegex(value, SHA256, name)
        for artifact in ("GO", "UV", "RUSTUP", "FLOCI"):
            for arch in ("amd64", "arm64"):
                self.assertIn(f"NS_PIN_{artifact}_SHA256_{arch}", pins)
            self.assertTrue(re.search(rf"^NS_PIN_{artifact}_VERSION=\d+\.\d+\.\d+$", text, re.M),
                            f"{artifact} has no exact version pin")


class FetchVerifiedRefusesWhatDoesNotMatch(unittest.TestCase):
    """nightshift_fetch_verified, run for real against a stub curl."""

    def run_fetch(self, pinned, body="artifact-bytes"):
        d = tempfile.mkdtemp()
        curl = os.path.join(d, "curl")
        with open(curl, "w") as fh:
            fh.write(CURL_STUB)
        os.chmod(curl, os.stat(curl).st_mode | stat.S_IEXEC)
        dest = os.path.join(d, "artifact")
        env = dict(os.environ, NS_CURL_LOG=os.path.join(d, "log"), NS_BODY=body)
        p = subprocess.run(
            ["bash", "-c", f'set -euo pipefail; . "{PINS}"; '
                           f'nightshift_fetch_verified https://example.invalid/a "{pinned}" "{dest}" "{curl}"'],
            capture_output=True, text=True, env=env, timeout=60)
        urls = []
        if os.path.exists(env["NS_CURL_LOG"]):
            with open(env["NS_CURL_LOG"]) as fh:
                urls = fh.read().split()
        return p.returncode, p.stderr, dest, urls

    def test_a_matching_artifact_lands_at_dest(self):
        good = hashlib.sha256(b"artifact-bytes").hexdigest()
        code, err, dest, _ = self.run_fetch(good)
        self.assertEqual(code, 0, err)
        with open(dest) as fh:
            self.assertEqual(fh.read(), "artifact-bytes")

    def test_a_mismatch_leaves_nothing_behind(self):
        code, err, dest, _ = self.run_fetch("0" * 64)
        self.assertNotEqual(code, 0)
        self.assertIn("sha256 mismatch", err)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_a_missing_or_malformed_pin_refuses_before_downloading(self):
        for pinned in ("", "abc", "Z" * 64):
            code, err, dest, urls = self.run_fetch(pinned)
            self.assertNotEqual(code, 0, pinned)
            self.assertIn("refusing", err)
            self.assertEqual(urls, [], "it downloaded with nothing to check against")
            self.assertFalse(os.path.exists(dest))


class BootstrapInstallsOnlyLockedPackages(unittest.TestCase):
    BOOTSTRAP = os.path.join(DEPLOY, "bootstrap.sh")

    def code(self):
        return "\n".join(joined_code(self.BOOTSTRAP))

    def test_harness_clis_come_from_the_committed_lockfile(self):
        code = self.code()
        self.assertRegex(code, r"npm ci\b")
        self.assertIn("deploy/harness-clis/package-lock.json", code)
        self.assertNotRegex(code, r"npm install -g\s+@", "a floating global install is back")
        self.assertIn("DISABLE_AUTOUPDATER", code, "the CLI could update past its pin")

    def test_harness_lockfile_pins_exact_versions_with_integrity(self):
        import json
        pkg = json.loads(read("deploy", "harness-clis", "package.json"))
        lock = json.loads(read("deploy", "harness-clis", "package-lock.json"))
        deps = pkg["dependencies"]
        self.assertEqual(set(deps), {"@anthropic-ai/claude-code", "@openai/codex"})
        for name, version in deps.items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", f"{name} is a range, not a pin")
            self.assertEqual(lock["packages"][f"node_modules/{name}"]["version"], version)
        for path, entry in lock["packages"].items():
            if path:
                self.assertTrue(entry.get("integrity", "").startswith("sha512-"), path)
        for platform in ("linux-x64", "linux-arm64"):
            self.assertIn(f"node_modules/@anthropic-ai/claude-code-{platform}", lock["packages"])
            self.assertIn(f"node_modules/@openai/codex-{platform}", lock["packages"])

    def test_omnigent_dependencies_are_reinstalled_under_require_hashes(self):
        code = self.code()
        self.assertIn('--constraints "$OMNIGENT_DEPS_LOCK"', code)
        self.assertIn("--build-constraints", code)
        self.assertRegex(code, r"uv pip install [^\n]*--require-hashes[^\n]*-r \"\$OMNIGENT_DEPS_LOCK\"")
        self.assertIn("uv tool uninstall omnigent", code, "a failed check must not leave the tool installed")
        self.assertLess(code.index("--require-hashes"), code.index(" verify"))

    def test_omnigent_deps_lock_hashes_every_requirement_for_the_pinned_commit(self):
        import json
        text = read("deploy", "omnigent-deps.lock.txt")
        runtime = json.loads(read("deploy", "omnigent-runtime.lock.json"))
        self.assertIn(f"omnigent commit: {runtime['commit']}", text,
                      "the runtime lock moved but the dependency lock was not regenerated")
        entries = re.split(r"\n(?=[A-Za-z0-9])", text.split("\n\n", 1)[-1].strip())
        reqs = [e for e in entries if re.match(r"^[A-Za-z0-9][\w.-]*==", e)]
        self.assertGreater(len(reqs), 50)
        for entry in reqs:
            self.assertRegex(entry, r"--hash=sha256:[0-9a-f]{64}", entry.splitlines()[0])
        self.assertNotRegex(text, r"(?m)^(omnigent|omnigent-client|omnigent-ui-sdk)==")

    def test_build_backends_are_exact_versions(self):
        lines = [ln for ln in read("deploy", "omnigent-build-deps.lock.txt").splitlines()
                 if ln.strip() and not ln.startswith("#")]
        self.assertTrue(lines)
        for ln in lines:
            self.assertRegex(ln, r"^[a-z0-9-]+==[0-9.]+$")


class RtkIsOptIn(unittest.TestCase):
    """The rtk hook rewrites every worker command; it must be the operator's choice."""

    def run_block(self, opt_in):
        with open(os.path.join(DEPLOY, "bootstrap.sh")) as fh:
            lines = fh.read().splitlines()
        a = next(i for i, ln in enumerate(lines) if ln.startswith('say "rtk token filter'))
        b = next(i for i, ln in enumerate(lines) if "control plane CLI" in ln)
        d = tempfile.mkdtemp()
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        rtk = os.path.join(bindir, "rtk")
        with open(rtk, "w") as fh:
            fh.write("#!/usr/bin/env bash\necho rtk 0.0.0\n")
        os.chmod(rtk, 0o755)
        env = dict(os.environ, HOME=d, PATH=bindir + os.pathsep + os.environ["PATH"])
        env.pop("NIGHTSHIFT_RTK", None)
        if opt_in:
            env["NIGHTSHIFT_RTK"] = "1"
        script = "set -euo pipefail\nsay() { echo \"=== $1\"; }\n" + "\n".join(lines[a:b])
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env=env, timeout=60)
        settings = os.path.join(d, ".claude", "settings.json")
        body = ""
        if os.path.exists(settings):
            with open(settings) as fh:
                body = fh.read()
        return p, body

    def test_default_installs_no_hook_even_with_rtk_on_path(self):
        p, body = self.run_block(opt_in=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("rtk hook claude", body)

    def test_explicit_opt_in_installs_the_hook(self):
        p, body = self.run_block(opt_in=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("rtk hook claude", body)

    def test_bootstrap_never_installs_rtk_itself(self):
        code = "\n".join(joined_code(os.path.join(DEPLOY, "bootstrap.sh")))
        self.assertNotRegex(code, r"brew install")


if __name__ == "__main__":
    unittest.main()
