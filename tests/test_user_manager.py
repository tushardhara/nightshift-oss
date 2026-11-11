"""`systemctl --user` must not be ASSUMED to work after `su - nightshift`.

DoD condition 3, issue #9. Brief bug 3, and the one that did the most damage:
`deploy/bootstrap.sh` dies at `systemctl --user daemon-reload` under
`set -euo pipefail`, which sits BEFORE the `enable --now` calls — so on this very
box neither `nightshift-watchdog.timer` nor `nightshift-state-backup.timer` was
ever enabled. Bug 3 silently disabled the recovery layer that exists to catch
bug 4.

Root cause, proven live on the build host and NOT "there is no session":

    $ env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS systemctl --user daemon-reload
    Failed to connect to bus: No medium found            # exit 1
    $ XDG_RUNTIME_DIR=/run/user/1000 \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user ...
                                                          # exit 0

Linger is on, `/run/user/1000` exists and `user@1000.service` is active. Only the
two environment variables were missing. Per ADR reconciliation A11 the capability
is established as ROOT in provisioning; bootstrap PROBES and fails with an
instruction aimed at the root step. No sudo: the runtime account has none by design.

Requirement ids (DoD condition 3):
  RT-05  capability absent -> user services end up installed and enabled
  RT-06  capability unobtainable -> non-zero exit naming the ROOT remediation,
         and NONE of the six named units left enabled
  RT-07  re-run is a no-op

WHAT THESE TESTS DO NOT PROVE, stated up front because the architect flagged it
and the program accepted the flag: they do not prove the install "survives logout
and reboot via linger". That needs a real user manager and a real reboot; a
container has neither. What is tested is the capability PROBE and its OUTCOME —
that the reachable case enables exactly the right units, and that the unreachable
case fails loudly, names the root fix, and leaves nothing half-installed. The
linger call itself is asserted to be issued by the ROOT step, not that the kernel
honoured it.

Every `systemctl`/`loginctl` call is routed through an INSTRUMENTED command
boundary (a stub on PATH that records argv), so "which units got enabled" is
observed rather than grepped for, and the assertions cannot be satisfied by a
different spelling of the same command.
"""

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(REPO, "deploy")
LIB = os.path.join(DEPLOY, "lib", "deploy-lib.sh")

# The exact set DoD condition 3 names, so "half-installed" is decidable.
SIX_UNITS = (
    "omnigent-server.service",
    "omnigent-host.service",
    "nightshift-state-backup.service",
    "nightshift-state-backup.timer",
    "nightshift-watchdog.service",
    "nightshift-watchdog.timer",
)

# An instrumented `systemctl`. It refuses exactly the way the real one does when
# the bus environment is missing, and it JOURNALS every call so the test can ask
# "what did you actually enable?" instead of grepping the script for a spelling.
FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
log="$NS_TEST_LOG"
printf '%s\n' "$*" >> "$log"

mode=user
for a in "$@"; do [ "$a" = "--user" ] && mode=user; done

# The real failure: no bus environment -> cannot reach the user manager.
if [ "$mode" = user ]; then
  if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    echo "Failed to connect to bus: No medium found" >&2; exit 1
  fi
  # Environment present, but the manager itself may still be down.
  if [ ! -f "$NS_TEST_BUS_OK" ]; then
    echo "Failed to connect to bus: No medium found" >&2; exit 1
  fi
fi

verb=""
for a in "$@"; do
  case "$a" in --*) ;; *) verb="$a"; break ;; esac
done

case "$verb" in
  enable)
    for a in "$@"; do
      case "$a" in --*|enable) ;; *) echo "$a" >> "$NS_TEST_ENABLED" ;; esac
    done ;;
  disable)
    for a in "$@"; do
      case "$a" in --*|disable) ;;
        *) [ -f "$NS_TEST_ENABLED" ] && grep -vxF "$a" "$NS_TEST_ENABLED" \
             > "$NS_TEST_ENABLED.tmp" 2>/dev/null; mv -f "$NS_TEST_ENABLED.tmp" \
             "$NS_TEST_ENABLED" 2>/dev/null || true ;;
      esac
    done ;;
  is-enabled)
    for a in "$@"; do
      case "$a" in --*|is-enabled) ;;
        *) grep -qxF "$a" "$NS_TEST_ENABLED" 2>/dev/null || exit 1 ;;
      esac
    done ;;
esac
exit 0
"""

FAKE_LOGINCTL = r"""#!/usr/bin/env bash
printf 'loginctl %s\n' "$*" >> "$NS_TEST_LOG"
case "${1:-}" in
  show-user) [ -f "$NS_TEST_LINGER" ] && echo "Linger=yes" || echo "Linger=no" ;;
  enable-linger) touch "$NS_TEST_LINGER" ;;
esac
exit 0
"""

# If anything reaches for sudo, fail loudly rather than silently succeeding.
# The runtime user has no sudo BY DESIGN and both architects said so.
FAKE_SUDO = r"""#!/usr/bin/env bash
printf 'SUDO-INVOKED %s\n' "$*" >> "$NS_TEST_LOG"
echo "sudo: a password is required" >&2
exit 1
"""


class Harness:
    """A fake host: instrumented systemctl/loginctl/sudo, no real system touched."""

    def __init__(self, bus_ok=True, linger=True):
        self.dir = tempfile.mkdtemp()
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.dir, "calls.log")
        self.enabled = os.path.join(self.dir, "enabled.txt")
        self.bus_ok = os.path.join(self.dir, "BUS_OK")
        self.linger = os.path.join(self.dir, "LINGER")
        for name, body in (("systemctl", FAKE_SYSTEMCTL),
                           ("loginctl", FAKE_LOGINCTL),
                           ("sudo", FAKE_SUDO)):
            p = os.path.join(self.bin, name)
            with open(p, "w") as fh:
                fh.write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        open(self.log, "w").close()
        open(self.enabled, "w").close()
        if bus_ok:
            open(self.bus_ok, "w").close()
        if linger:
            open(self.linger, "w").close()

    def env(self, strip_bus_env=True):
        e = dict(os.environ)
        e["PATH"] = self.bin + os.pathsep + e.get("PATH", "")
        e["NS_TEST_LOG"] = self.log
        e["NS_TEST_ENABLED"] = self.enabled
        e["NS_TEST_BUS_OK"] = self.bus_ok
        e["NS_TEST_LINGER"] = self.linger
        if strip_bus_env:
            # The bug's precondition, exactly: `su - nightshift` gives you
            # neither of these.
            e.pop("XDG_RUNTIME_DIR", None)
            e.pop("DBUS_SESSION_BUS_ADDRESS", None)
        return e

    def bash(self, snippet, strip_bus_env=True):
        p = subprocess.run(
            ["bash", "-c", f'set -euo pipefail; . "{LIB}"; {snippet}'],
            capture_output=True, text=True, env=self.env(strip_bus_env), timeout=60,
        )
        return p.returncode, p.stdout + p.stderr

    def enabled_units(self):
        with open(self.enabled) as fh:
            return {ln.strip() for ln in fh if ln.strip()}

    def calls(self):
        with open(self.log) as fh:
            return [ln.rstrip("\n") for ln in fh]


class RT05CapabilityAbsentButObtainable(unittest.TestCase):
    """RT-05 — the bug's exact precondition: bus env absent, manager healthy.

    Outcome required, mechanism not prescribed: the user services end up
    installed and enabled.
    """

    def test_rt05_units_end_up_enabled_when_only_the_env_was_missing(self):
        h = Harness(bus_ok=True)
        code, out = h.bash("nightshift_install_user_units")
        self.assertEqual(code, 0, f"install failed with a healthy manager:\n{out}")

        enabled = h.enabled_units()
        for unit in ("nightshift-watchdog.timer", "nightshift-state-backup.timer",
                     "omnigent-server.service", "omnigent-host.service"):
            self.assertIn(unit, enabled,
                          f"{unit} was not enabled — this is the timer that went "
                          f"missing on the live box. Enabled: {sorted(enabled)}")

    def test_rt05_the_probe_precedes_every_mutation(self):
        # Ordering is the safety property: probing after daemon-reload would
        # already have aborted under `set -e`, which is the original bug.
        h = Harness(bus_ok=True)
        h.bash("nightshift_install_user_units")
        calls = h.calls()
        mutating = [i for i, c in enumerate(calls)
                    if re.search(r"\b(daemon-reload|enable)\b", c)]
        probing = [i for i, c in enumerate(calls)
                   if re.search(r"\b(show-environment|is-system-running)\b", c)]
        self.assertTrue(probing, f"no read-only probe was ever issued: {calls}")
        self.assertTrue(mutating, f"nothing was ever enabled: {calls}")
        self.assertLess(min(probing), min(mutating),
                        f"the first mutation came before the first probe: {calls}")

    def test_rt05_no_sudo_is_ever_invoked(self):
        h = Harness(bus_ok=True)
        h.bash("nightshift_install_user_units")
        self.assertFalse([c for c in h.calls() if c.startswith("SUDO-INVOKED")],
                         "the runtime user has no sudo by design (ADR A11)")


class RT06CapabilityUnobtainable(unittest.TestCase):
    """RT-06 — manager genuinely unreachable: fail loudly, install nothing."""

    def test_rt06_exits_non_zero(self):
        h = Harness(bus_ok=False)
        code, _ = h.bash("nightshift_install_user_units")
        self.assertNotEqual(code, 0, "an unreachable user manager must fail loudly")

    def test_rt06_message_names_the_ROOT_remediation(self):
        h = Harness(bus_ok=False)
        _code, out = h.bash("nightshift_install_user_units 2>&1 || true")
        low = out.lower()
        self.assertIn("root", low, f"message must aim the fix at the root step: {out!r}")
        self.assertIn("enable-linger", low, f"must name the root command: {out!r}")
        self.assertIn("user@", low,
                      f"must name starting the user manager, e.g. user@<uid>.service: {out!r}")
        # ... and must NOT tell the runtime user to use sudo.
        self.assertNotIn("sudo", low,
                         f"must not send a sudo-less account to sudo: {out!r}")

    def test_rt06_leaves_none_of_the_six_named_units_enabled(self):
        # DoD condition 3 names this exact set so "half-installed" is decidable.
        h = Harness(bus_ok=False)
        h.bash("nightshift_install_user_units")
        left = h.enabled_units() & set(SIX_UNITS)
        self.assertEqual(left, set(),
                         f"failure path left units enabled: {sorted(left)}")

    def test_rt06_bootstrap_never_mutates_before_it_probes(self):
        """The ordering defect itself, asserted against the real script.

        Pre-fix `bootstrap.sh` runs `systemctl --user daemon-reload` at the top
        of the unit block with no reachability check anywhere before it. Under
        `set -euo pipefail` that call ABORTS the script, which is why the two
        timers below it were never enabled on the live box. This assertion is
        about the production file, so it cannot pass vacuously.
        """
        path = os.path.join(DEPLOY, "bootstrap.sh")
        with open(path, errors="replace") as fh:
            lines = fh.readlines()
        MUTATING = re.compile(r"systemctl\s+--user\s+(daemon-reload|enable|start|restart)")
        PROBING = re.compile(r"nightshift_install_user_units|nightshift_user_manager_ready"
                             r"|systemctl\s+--user\s+(show-environment|is-system-running)")
        first_mutation = first_probe = None
        for n, ln in enumerate(lines, 1):
            code = ln.split("#", 1)[0]
            if first_probe is None and PROBING.search(code):
                first_probe = n
            if first_mutation is None and MUTATING.search(code):
                first_mutation = n
        if first_mutation is None:
            return  # nothing mutates directly; the helper owns it
        self.assertIsNotNone(
            first_probe,
            f"bootstrap.sh:{first_mutation} mutates the user manager with no "
            f"reachability probe anywhere before it — under `set -e` this is the "
            f"line that aborted the script and left both safety timers disabled",
        )
        self.assertLess(
            first_probe, first_mutation,
            f"bootstrap.sh probes at line {first_probe} but already mutated at "
            f"line {first_mutation}",
        )

    def test_rt06_does_not_fall_back_to_sudo(self):
        h = Harness(bus_ok=False)
        h.bash("nightshift_install_user_units")
        self.assertFalse([c for c in h.calls() if c.startswith("SUDO-INVOKED")],
                         "bootstrap must not reach for sudo when the bus is down")


class RT07Idempotent(unittest.TestCase):
    """RT-07 — re-running is a no-op, not an error and not a duplicate."""

    def test_rt07_second_run_succeeds_with_the_same_end_state(self):
        h = Harness(bus_ok=True)
        code1, out1 = h.bash("nightshift_install_user_units")
        first = h.enabled_units()
        code2, out2 = h.bash("nightshift_install_user_units")
        second = h.enabled_units()
        self.assertEqual(code1, 0, out1)
        self.assertEqual(code2, 0, f"re-run must be a no-op, not an error:\n{out2}")
        self.assertEqual(first, second, "re-run changed the enabled set")


class BusEnvironmentDerivation(unittest.TestCase):
    """The two variables, derived from `id -u` rather than inherited."""

    def test_runtime_dir_is_derived_from_the_uid(self):
        h = Harness()
        code, out = h.bash("nightshift_user_bus_env 4242")
        self.assertEqual(code, 0, out)
        self.assertIn("XDG_RUNTIME_DIR=/run/user/4242", out)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/4242/bus", out)

    def test_an_existing_bus_environment_is_not_overwritten(self):
        # If the caller already has a working session, respect it.
        h = Harness(bus_ok=True)
        code, out = h.bash(
            'XDG_RUNTIME_DIR=/custom/rt '
            'DBUS_SESSION_BUS_ADDRESS=unix:path=/custom/rt/bus '
            'nightshift_user_manager_ready >/dev/null 2>&1; '
            'echo "$XDG_RUNTIME_DIR"',
            strip_bus_env=False)
        self.assertEqual(code, 0, out)
        self.assertIn("/custom/rt", out)


class ScriptsWireItUp(unittest.TestCase):
    """The scripts must actually use it, and the sudo fallback must be gone."""

    def test_bootstrap_probes_before_touching_the_user_manager(self):
        with open(os.path.join(DEPLOY, "bootstrap.sh"), errors="replace") as fh:
            body = fh.read()
        self.assertTrue(
            "nightshift_install_user_units" in body,
            "deploy/bootstrap.sh must install user units through the probing "
            "helper, not by calling `systemctl --user daemon-reload` blind",
        )

    def test_bootstrap_has_no_sudo_fallback(self):
        # Both architects independently said to delete it: the runtime account
        # has no sudo, and the fallback makes responsibility ambiguous.
        with open(os.path.join(DEPLOY, "bootstrap.sh"), errors="replace") as fh:
            lines = fh.readlines()

        # Strip comments AND string literals first. `bootstrap.sh:21` legitimately
        # prints "run it as the runtime user (sudo -iu nightshift)" — that is
        # operator guidance for becoming the runtime user, not the script
        # escalating its own privileges. Scanning raw text cannot tell those
        # apart; scanning executable positions can. This is a sharpening of the
        # check, not a loosening: a real `|| sudo -n loginctl ...` is still caught.
        strip_strings = re.compile(r"'[^']*'|\"[^\"]*\"")
        invocation = re.compile(r"(?:^|[|&;(]|\|\||&&)\s*sudo\s")
        offenders = []
        for n, ln in enumerate(lines, 1):
            code = strip_strings.sub("", ln.split("#", 1)[0])
            if invocation.search(code):
                offenders.append(f"{n}: {ln.strip()}")
        self.assertEqual(offenders, [],
                         "bootstrap.sh INVOKES sudo — the runtime account has no "
                         "elevated rights by design (ADR A11):\n  "
                         + "\n  ".join(offenders))

    def test_legacy_host_unit_disables_codex_native_tools_and_web_search(self):
        path = os.path.join(DEPLOY, "systemd", "omnigent-host.service")
        with open(path, errors="replace") as fh:
            lines = [line.strip() for line in fh]
        self.assertEqual(
            lines.count("Environment=HARNESS_CODEX_DISABLE_NATIVE_TOOLS=1"), 1)
        self.assertEqual(
            lines.count("Environment=HARNESS_CODEX_ENABLE_WEB_SEARCH=0"), 1)

    def test_provisioning_establishes_the_capability_as_root(self):
        # ADR reconciliation A11: capability established as root, verified as
        # the user. Linger alone is not enough — the manager must be started.
        with open(os.path.join(DEPLOY, "provision-ubuntu.sh"), errors="replace") as fh:
            body = fh.read()
        self.assertIn("enable-linger", body)
        self.assertTrue(
            "user@" in body,
            "provisioning must start the user manager (user@<uid>.service), not "
            "merely enable linger — linger permits a manager to persist, it does "
            "not construct one",
        )
        self.assertTrue(
            "nightshift_user_manager_ready" in body or "runuser" in body,
            "provisioning must VERIFY the capability as the runtime user",
        )


if __name__ == "__main__":
    unittest.main()
