"""NON-GATING live smoke check for `pr-head` (issue #92).

This file is deliberately NOT part of the gated suite. `tests/run.sh` runs
`unittest discover -s tests -p 'test_*.py'`, and discovery only descends into
importable packages -- this directory has no __init__.py, so it is skipped.
That is load-bearing, not incidental: adding an __init__.py here would drag
a network-dependent test back into the gated suite and re-open issue #92.
The gated suite must stay hermetic; see the docstring in tests/test_pr_head.py.

What this covers that the hermetic tests cannot: that the REAL `gh` binary,
against the REAL GitHub API, still returns the field names `pr-head` parses.
The hermetic tests fake `gh`, so a GitHub API rename of `headRefOid` would
slip past them. That is a genuine gap, and it is worth one non-blocking job.

Requires a credential. It SKIPS (never fails) when there is none, so running
it by hand on an unauthenticated box is quiet rather than noisy.

NOT ORPHANED -- and that distinction matters here (issue #74). `tests/linux/*.sh`
is the cautionary case: it sits outside the gated suite and NO workflow runs it,
so PR #71's silent no-op shipped past a test that existed. A non-gated test that
nothing schedules is not a safety net, it is decoration. This file is therefore
wired into .github/workflows/live-api-smoke.yml (workflow_dispatch + a daily
cron) at the same time it is created. If you ever delete that workflow, delete
this file with it rather than leaving it stranded.

Run it directly -- it is not discovered (and `discover -s tests/live` will
NOT work, precisely because this directory is not importable):

    PYTHONPATH=tests python3 tests/live/test_pr_head_live.py -v

It names NO branch. The old gated test pinned `alpha/64-rt25-vacuous`, which
any crew could delete at any time; asserting only on SHAPE keeps this check
from decaying the same way.
"""

import json
import os
import subprocess
import unittest

from ctlutil import CTL

# The PR this smoke check reads. Any PR that exists will do -- nothing is
# asserted about its branch, only about the SHAPE of the response.
LIVE_PR = os.environ.get("NIGHTSHIFT_LIVE_PR", "69")


def _has_credential():
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return True
    p = subprocess.run(["gh", "auth", "status"],
                       capture_output=True, text=True)
    return p.returncode == 0


@unittest.skipUnless(_has_credential(), "no GitHub credential; live check skipped")
class PrHeadLive(unittest.TestCase):
    def test_live_api_still_returns_the_fields_pr_head_parses(self):
        """The real API still speaks headRefOid/headRefName."""
        p = subprocess.run([CTL, "pr-head", LIVE_PR],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0,
                         f"live pr-head failed: {p.stderr[:400]!r}")
        body = json.loads(p.stdout)
        self.assertEqual(body["pr"], int(LIVE_PR))
        # SHAPE only -- deliberately no branch-name assertion.
        self.assertRegex(body["head"], r"^[0-9a-f]{40}$")
        self.assertTrue(body["ref"], "ref should be a non-empty branch name")


if __name__ == "__main__":
    unittest.main()
