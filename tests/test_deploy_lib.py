"""deploy/lib/deploy-lib.sh — the deterministic provisioning helpers.

Memory sizing and runtime-user resolution are deployment *policy*: they decide
how hard the kernel may squeeze the host cgroup and which Linux user owns the
org. Both must be reproducible on any Ubuntu VPS (Hostinger, AWS, ...), so the
suite drives the real bash functions through subprocess, same as the scripts do.
"""

import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, "deploy", "lib", "deploy-lib.sh")


def bash(snippet):
    """Source the lib, run a snippet, return (exit_code, stdout)."""
    p = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; . "{LIB}"; {snippet}'],
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout.strip()


def limits(total_gb):
    code, out = bash(f"nightshift_memory_limits {total_gb}")
    if code != 0:
        return code, None, None
    high, mmax = out.split()
    return code, int(high), int(mmax)


class MemoryLimits(unittest.TestCase):
    def test_16gb_box_keeps_the_original_ec2_sizing(self):
        # The old hardcoded unit said 11G/13G on the 16 GB m7i.xlarge; the
        # derivation must reproduce it exactly or existing installs change
        # behaviour on re-bootstrap.
        self.assertEqual(limits(16), (0, 11, 13))

    def test_32gb_box_gets_the_hostinger_kvm8_sizing(self):
        self.assertEqual(limits(32), (0, 24, 28))

    def test_64gb_box_scales_and_keeps_a_reserve(self):
        code, high, mmax = limits(64)
        self.assertEqual(code, 0)
        self.assertEqual(mmax, 56)  # reserve 64/8 = 8
        self.assertEqual(high, 48)

    def test_8gb_box_still_gets_sensible_lower_limits(self):
        self.assertEqual(limits(8), (0, 3, 5))

    def test_tiny_box_clamps_but_stays_a_valid_unit(self):
        # high >= 1, max >= 2, high < max: the unit must always be loadable.
        for total in (1, 2, 4):
            code, high, mmax = limits(total)
            self.assertEqual(code, 0)
            self.assertGreaterEqual(high, 1)
            self.assertGreaterEqual(mmax, 2)
            self.assertLess(high, mmax, f"high !< max at {total}G")

    def test_high_below_max_and_monotonic_across_the_range(self):
        prev_high = prev_max = 0
        for total in range(4, 129, 4):
            code, high, mmax = limits(total)
            self.assertEqual(code, 0)
            self.assertLess(high, mmax)
            self.assertLess(mmax, total, f"no OS reserve left at {total}G")
            self.assertGreaterEqual(high, prev_high)
            self.assertGreaterEqual(mmax, prev_max)
            prev_high, prev_max = high, mmax

    def test_garbage_input_fails_instead_of_emitting_a_broken_unit(self):
        for arg in ("", "abc", "16G", "-4"):
            code, _ = bash(f"nightshift_memory_limits '{arg}'")
            self.assertNotEqual(code, 0, f"accepted garbage: {arg!r}")


class MemTotalDetection(unittest.TestCase):
    def meminfo(self, kib):
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".meminfo", delete=False, dir=tempfile.gettempdir()
        )
        f.write(f"MemTotal:       {kib} kB\nMemFree:        1024 kB\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_rounds_kernel_reserved_ram_back_to_nominal_size(self):
        # A "32 GB" VPS reports less than 32 GiB because the kernel reserves
        # memory before /proc/meminfo; nearest-GiB rounding must recover the
        # nominal size the sizing rule was designed around.
        cases = [
            (16 * 1048576 - 300_000, 16),  # 16 GiB minus ~293 MiB kernel reserve
            (32 * 1048576 - 500_000, 32),  # 32 GiB minus ~488 MiB
            (8 * 1048576, 8),              # exact
            (512 * 1024, 1),               # 0.5 GiB clamps up to 1
        ]
        for kib, want in cases:
            code, out = bash(f"nightshift_mem_total_gb {self.meminfo(kib)}")
            self.assertEqual(code, 0)
            self.assertEqual(int(out), want, f"MemTotal {kib} kB")

    def test_missing_meminfo_fails_loud(self):
        code, _ = bash("nightshift_mem_total_gb /nonexistent/meminfo")
        self.assertNotEqual(code, 0)


class ResolveUser(unittest.TestCase):
    def resolve(self, explicit, owner):
        code, out = bash(f"nightshift_resolve_user '{explicit}' '{owner}'")
        self.assertEqual(code, 0)
        return out

    def test_default_is_nightshift_on_a_fresh_box(self):
        self.assertEqual(self.resolve("", ""), "nightshift")

    def test_adopts_existing_owner_for_legacy_ec2_installs(self):
        # /srv/nightshift owned by `ubuntu` (old cloud-init) -> keep it,
        # never force a migration on an install that already works.
        self.assertEqual(self.resolve("", "ubuntu"), "ubuntu")

    def test_never_adopts_root(self):
        # root-owned /srv/nightshift means a root-run rsync, not a runtime
        # user — provisioning must pick nightshift and chown, not run as root.
        self.assertEqual(self.resolve("", "root"), "nightshift")

    def test_explicit_request_beats_everything(self):
        self.assertEqual(self.resolve("crewops", "ubuntu"), "crewops")
        self.assertEqual(self.resolve("crewops", "root"), "crewops")


class ClassifyServe(unittest.TestCase):
    """Preflight runs as the runtime user, which is deliberately NOT a
    tailscaled operator (operator rights would let any worker reconfigure the
    tailnet, including public `funnel`). So a permission refusal on the serve
    config read is EXPECTED on a locked-down box and must classify as a
    distinct verdict — a skip/warn, never a false FAIL."""

    def classify(self, code, out):
        rc, verdict = bash(
            f"nightshift_classify_serve {code} {out!r}"
        )
        self.assertEqual(rc, 0)
        return verdict

    def test_configured_when_config_proxies_6767(self):
        out = "https://box.tail1234.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:6767"
        self.assertEqual(self.classify(0, out), "configured")
        self.assertEqual(self.classify(0, "proxy http://localhost:6767"), "configured")

    def test_unconfigured_when_serve_exists_but_not_for_6767(self):
        self.assertEqual(self.classify(0, "No serve config"), "unconfigured")
        self.assertEqual(
            self.classify(0, "|-- / proxy http://127.0.0.1:3000"), "unconfigured"
        )

    def test_denied_on_permission_style_refusals(self):
        # The runtime user is not root/operator — these are the healthy-box
        # shapes of a config read it is not allowed to make.
        for msg in (
            "Access denied: serve config denied",
            "permission denied connecting to local tailscaled",
            "this action is not allowed",
            "use 'sudo tailscale' or set the operator user",
        ):
            self.assertEqual(self.classify(1, msg), "denied", msg)

    def test_error_when_failure_is_not_permission_shaped(self):
        # e.g. an old CLI without the serve subcommand, or the daemon down —
        # preflight should say "could not determine", not invent a denial.
        self.assertEqual(self.classify(1, "unknown subcommand: serve"), "error")
        self.assertEqual(self.classify(2, ""), "error")

    def test_success_output_never_classifies_as_denied(self):
        # A serve config that merely MENTIONS an operator-ish word must not
        # flip a successful read into a denial.
        self.assertEqual(
            self.classify(0, "operator note: proxy http://127.0.0.1:6767"),
            "configured",
        )


class DockerBindDefault(unittest.TestCase):
    """The deterministic half of "emulator ports never go public".

    `floci start` publishes through Docker and has no bind-address flag, so the
    guarantee lives in /etc/docker/daemon.json. Verified against a real dockerd
    (tests/linux/toolchain-smoke.sh); these tests pin the file-editing logic,
    where the dangerous failure is clobbering an operator's config.
    """

    def bind_json(self, existing):
        return bash(f"nightshift_docker_bind_json '{existing}'")

    def bind_ok(self, content):
        code, out = bash(
            f"""nightshift_docker_bind_ok '{content}' && echo yes || echo no"""
        )
        self.assertEqual(code, 0)
        return out.strip() == "yes"

    def test_fresh_box_gets_the_loopback_default(self):
        code, out = self.bind_json("")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["ip"], "127.0.0.1")

    def test_merge_preserves_unrelated_operator_settings(self):
        existing = '{"log-driver":"json-file","log-opts":{"max-size":"10m"}}'
        code, out = self.bind_json(existing)
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertEqual(got["ip"], "127.0.0.1")
        self.assertEqual(got["log-driver"], "json-file")
        self.assertEqual(got["log-opts"]["max-size"], "10m")

    def test_existing_wrong_ip_is_corrected(self):
        code, out = self.bind_json('{"ip":"0.0.0.0"}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["ip"], "127.0.0.1")

    def test_invalid_json_fails_so_the_caller_leaves_the_file_alone(self):
        # Clobbering a config we cannot parse is worse than not setting the
        # default: provisioning must warn and move on.
        code, _ = self.bind_json("not json at all")
        self.assertEqual(code, 1)

    def test_bind_ok_detects_the_control_and_its_absence(self):
        self.assertTrue(self.bind_ok('{"ip":"127.0.0.1"}'))
        self.assertTrue(self.bind_ok('{"log-driver":"json-file","ip":"127.0.0.1"}'))
        self.assertFalse(self.bind_ok('{"ip":"0.0.0.0"}'))
        self.assertFalse(self.bind_ok('{"log-driver":"json-file"}'))
        self.assertFalse(self.bind_ok(""))
        self.assertFalse(self.bind_ok("not json"))


class VersionGe(unittest.TestCase):
    """Toolchain version compare — decides whether provisioning re-downloads Go.

    Getting this wrong is not cosmetic: a false "up to date" leaves workers on a
    Go too old for the project's go.mod, and a false "too old" re-downloads a
    toolchain on every provisioning run.
    """

    def ge(self, have, want):
        code, _ = bash(f'nightshift_version_ge "{have}" "{want}" && echo yes || echo no')
        self.assertEqual(code, 0)
        return _.strip() == "yes"

    def test_equal_versions_are_good_enough(self):
        self.assertTrue(self.ge("1.25.0", "1.25.0"))

    def test_newer_patch_and_minor_satisfy_an_older_want(self):
        self.assertTrue(self.ge("1.25.1", "1.25.0"))
        self.assertTrue(self.ge("1.26.0", "1.25.9"))

    def test_older_version_is_rejected(self):
        self.assertFalse(self.ge("1.24.9", "1.25.0"))
        self.assertFalse(self.ge("1.9.0", "1.10.0"))

    def test_missing_fields_count_as_zero_not_as_newer(self):
        # Ubuntu's `go version` prints "go1.25" with no patch field.
        self.assertTrue(self.ge("1.25", "1.25.0"))
        self.assertFalse(self.ge("1.25", "1.25.1"))

    def test_absent_toolchain_reads_as_too_old(self):
        # provision-ubuntu.sh passes "0" when /usr/local/go/bin/go is missing.
        self.assertFalse(self.ge("0", "1.25.0"))

    def test_garbled_version_is_replaced_not_trusted(self):
        # A broken `go version` must not read as newer than what we want.
        self.assertFalse(self.ge("devel+abc", "1.25.0"))
        self.assertFalse(self.ge("", "1.25.0"))

    def test_double_digit_fields_compare_numerically_not_lexically(self):
        self.assertTrue(self.ge("1.100.0", "1.99.0"))


class GithubVisibility(unittest.TestCase):
    """Audit B4: the state backup and preflight refuse a public destination,
    so the URL parse and the visibility read must not fail open."""

    def slug(self, url):
        return bash(f"nightshift_github_slug '{url}'")

    def test_github_url_shapes_resolve_to_owner_repo(self):
        for url in ("https://github.com/o/r.git", "https://github.com/o/r",
                    "git@github.com:o/r.git", "ssh://git@github.com/o/r.git"):
            with self.subTest(url=url):
                self.assertEqual(self.slug(url), (0, "o/r"))

    def test_non_github_and_malformed_urls_are_not_slugs(self):
        for url in ("/srv/backup.git", "https://gitlab.example/o/r.git",
                    "https://github.com/o", "https://github.com/o/r/x",
                    "https://github.com.evil.example/o/r"):
            with self.subTest(url=url):
                self.assertNotEqual(self.slug(url)[0], 0)

    def _visibility(self, gh_body):
        with tempfile.TemporaryDirectory() as tmp:
            gh = os.path.join(tmp, "gh")
            with open(gh, "w") as fh:
                fh.write("#!/bin/sh\n" + gh_body)
            os.chmod(gh, 0o755)
            return bash(f"nightshift_repo_visibility o/r '{gh}'")

    def test_visibility_is_github_s_answer(self):
        self.assertEqual(self._visibility("echo private\n"), (0, "private"))
        self.assertEqual(self._visibility("echo public\n"), (0, "public"))

    def test_an_unreadable_answer_is_a_failure_not_a_default(self):
        self.assertNotEqual(self._visibility("exit 1\n")[0], 0)
        self.assertNotEqual(self._visibility("echo '{\"message\":\"Not Found\"}'\n")[0], 0)


if __name__ == "__main__":
    unittest.main()
