"""The unauthenticated control API stays off the tailnet unless the operator opts in.

Public-release audit C5: the omnigent server on 127.0.0.1:6767 runs in single-
user mode (no credential) and can create sessions and fire runs past every
tool-call fence. Provisioning used to tell every operator to publish it
tailnet-wide with `tailscale serve`. Now that is opt-in (NIGHTSHIFT_TAILSCALE_SERVE=1)
and the docs carry the ACL that has to come with it.

The tailscale-serve block is cut from the REAL provision-ubuntu.sh and run with
a stub `tailscale` that records every invocation.
"""

import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVISION = os.path.join(REPO, "deploy", "provision-ubuntu.sh")
LIB = os.path.join(REPO, "deploy", "lib", "deploy-lib.sh")

TAILSCALE_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TS_LOG"
case "$1 ${2:-}" in
  "status ") [ "${TS_UP:-1}" = 1 ] && exit 0 || { echo "Logged out." >&2; exit 1; } ;;
  "serve status") printf '%s\n' "${TS_SERVE_STATUS:-No serve config}"; exit 0 ;;
esac
exit 0
"""


def serve_block():
    with open(PROVISION, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    a = next(i for i, ln in enumerate(lines) if ln.startswith('say "tailscale serve'))
    b = next(i for i, ln in enumerate(lines) if i > a and ln.startswith("# ---") and "report" in ln)
    return "\n".join(lines[a:b])


def run_serve(opt_in, up=True, serve_status=None):
    d = tempfile.mkdtemp()
    stub = os.path.join(d, "tailscale")
    with open(stub, "w") as fh:
        fh.write(TAILSCALE_STUB)
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
    log = os.path.join(d, "ts.log")
    env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], TS_LOG=log,
               TS_UP="1" if up else "0")
    env.pop("NIGHTSHIFT_TAILSCALE_SERVE", None)
    if opt_in:
        env["NIGHTSHIFT_TAILSCALE_SERVE"] = "1"
    if serve_status is not None:
        env["TS_SERVE_STATUS"] = serve_status
    script = (f'set -euo pipefail\n. "{LIB}"\nsay() {{ echo "=== $1"; }}\n'
              f'{serve_block()}\nprintf "%s\\n" "$serve_step"\n')
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env=env, timeout=60)
    calls = []
    if os.path.exists(log):
        with open(log) as fh:
            calls = [ln.strip() for ln in fh if ln.strip()]
    return p, calls


def publishes(calls):
    return [c for c in calls
            if (c.startswith("serve") and c != "serve status") or c.startswith("funnel")]


class ServeIsOptIn(unittest.TestCase):

    def test_default_provisioning_never_publishes_the_api(self):
        p, calls = run_serve(opt_in=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(publishes(calls), [], calls)
        self.assertNotIn("serve --bg", p.stdout, "the next steps still tell everyone to publish it")
        self.assertIn("ssh -L 6767:127.0.0.1:6767", p.stdout)

    def test_default_warns_about_a_serve_left_by_an_earlier_install(self):
        p, calls = run_serve(opt_in=False,
                             serve_status="https://box.example.ts.net (tailnet only)\n"
                                          "|-- / proxy http://127.0.0.1:6767")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("still publishes 127.0.0.1:6767", p.stderr)
        self.assertEqual(publishes(calls), [], "a warning must not reconfigure the tailnet")

    def test_opt_in_publishes_once_tailscale_is_up(self):
        p, calls = run_serve(opt_in=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("serve --bg 6767", calls)
        self.assertIn("ACL", p.stdout)

    def test_opt_in_before_tailscale_up_defers_instead_of_failing(self):
        p, calls = run_serve(opt_in=True, up=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(publishes(calls), [])
        self.assertIn("re-run with NIGHTSHIFT_TAILSCALE_SERVE=1", p.stdout)

    def test_nothing_outside_the_opt_in_block_publishes_or_funnels(self):
        with open(PROVISION, encoding="utf-8") as fh:
            text = fh.read()
        outside = text.replace(serve_block(), "")
        code = [ln for ln in outside.splitlines() if not ln.lstrip().startswith("#")]
        self.assertFalse([ln for ln in code if re.search(r"tailscale\s+(serve\s+--bg|funnel)", ln)
                          and "never" not in ln.lower()])


class DocsStateTheExposurePlainly(unittest.TestCase):

    def read(self, *parts):
        with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_deploy_readme_says_there_is_no_auth_and_carries_an_acl(self):
        text = self.read("deploy", "README.md")
        self.assertIn("NIGHTSHIFT_TAILSCALE_SERVE=1", text)
        self.assertRegex(text, r"no authentication")
        self.assertIn('"acls"', text, "opting in must come with an owner-only ACL")
        self.assertIn("OMNIGENT_REMOTE_AUTH_TOKEN", text)

    def test_root_readme_warns_before_anything_else(self):
        text = self.read("README.md")
        warn = text.find("## Security model")
        self.assertGreater(warn, 0, "no security section")
        for later in ("## How a piece of work travels", "## Start a project",
                      "## Running it on a server"):
            idx = text.find(later)
            if idx > 0:
                self.assertLess(warn, idx, f"the warning comes after {later!r}")
        section = text[warn:text.find("\n## ", warn + 1)]
        for must in ("permission_mode: auto", "yolo: false", "docs/TRUST-BOUNDARY.md",
                     "dedicated host", "author-trust", "token"):
            self.assertIn(must, section, must)



if __name__ == "__main__":
    unittest.main()
