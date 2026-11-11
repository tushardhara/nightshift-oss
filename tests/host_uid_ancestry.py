"""Shared host-UID-ancestry precondition — ADR-0041, issue #245.

`tests.test_project_bind.ProjectTrustedAncestry` asserts real host `stat()`
ownership of `/`, `/home`, and the live ancestry chain — that is ADR-0021's
production predicate, and it is only true outside a sandbox that remaps host
UIDs into an unprivileged user namespace (bwrap `--unshare-user` with no
mapping for uid 0; see issue #245). Inside one, every root-owned host path —
including `/`, which is root-owned on any normal Linux filesystem — reports
as the kernel overflow uid instead of 0, and that class cannot pass.

ADR-0041 keeps that class mandatory everywhere real UID visibility exists
(the bare host, GitHub Actions) and skips it, by name, against a checked-in
manifest, only where it doesn't — `run_suite.py` audits the manifest so the
skip stays exact and visible instead of silently swallowing the gap, and
everything else in the suite still runs and must still pass.
"""

import os
import unittest

HOST_UID_REMAP_REASON_MARKER = "issue #245"

# The only class this decorator is applied to. Bare substring matching on
# HOST_UID_REMAP_REASON_MARKER alone would let any test anywhere reuse the
# same marker and slip past run_suite.py's audit undetected; every marked
# skip and every manifest entry must additionally live under this prefix.
HOST_UID_REMAP_TEST_PREFIX = "test_project_bind.ProjectTrustedAncestry."

HOST_UID_REMAP_SKIP_REASON = (
    "host-uid-remap: this process does not see `/` as uid 0, so it cannot "
    f"prove truthful host UID ancestry ({HOST_UID_REMAP_REASON_MARKER}, ADR-0041)")


def uid_remapped():
    """True when this process cannot see `/` as uid 0."""
    try:
        return os.stat("/").st_uid != 0
    except OSError:
        return True


def skip_under_uid_remap(cls):
    """Class decorator: skip, with the fixed reason above, under a remap.

    The fixed reason is load-bearing, not cosmetic — `run_suite.py`'s
    manifest audit matches on `HOST_UID_REMAP_REASON_MARKER` to tell this
    specific, expected skip apart from any other skip in the suite.
    """
    return unittest.skipIf(uid_remapped(), HOST_UID_REMAP_SKIP_REASON)(cls)
