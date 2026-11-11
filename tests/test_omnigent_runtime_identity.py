"""Focused tests for the exact Omnigent Git-runtime identity contract."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "deploy" / "omnigent-runtime-identity.py"
LOCK = REPO / "deploy" / "omnigent-runtime.lock.json"
SIGNED_SHA = "ef39b91aff4050567ca0c49b303794faf7889b0c"
SPEC = importlib.util.spec_from_file_location("omnigent_runtime_identity", MODULE)
assert SPEC and SPEC.loader
I = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(I)


class FakeDistribution:
    def __init__(self, version: str, direct_url: object):
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        if filename != "direct_url.json" or self._direct_url is None:
            return None
        if isinstance(self._direct_url, str):
            return self._direct_url
        return json.dumps(self._direct_url)


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.lock = I.load_lock(LOCK)
        self.direct_url = {
            "url": self.lock["repository"],
            "vcs_info": {
                "vcs": "git",
                "requested_revision": self.lock["commit"],
                "commit_id": self.lock["commit"],
            },
        }

    def test_committed_lock_selects_signed_reviewed_commit(self):
        self.assertEqual(self.lock["commit"], SIGNED_SHA)
        self.assertEqual(self.lock["version"], "0.12.0.dev0")
        self.assertEqual(
            I.requirement(self.lock),
            "omnigent @ git+https://github.com/tushardhara/omnigent.git@"
            + SIGNED_SHA,
        )

    def test_exact_pep610_identity_is_accepted_and_reported(self):
        distribution = FakeDistribution(self.lock["version"], self.direct_url)
        with mock.patch.object(
            I.importlib.metadata, "distribution", return_value=distribution
        ):
            identity = I.inspect_installed(self.lock)
        self.assertTrue(identity["matched"])
        self.assertEqual(identity["lock"], self.lock)
        self.assertEqual(identity["observed"]["commit"], SIGNED_SHA)

    def test_every_identity_seam_fails_closed(self):
        cases = {
            "version": ("9.9.9", self.direct_url),
            "repository": (
                self.lock["version"],
                {
                    **self.direct_url,
                    "url": "https://github.com/omnigent-ai/omnigent.git",
                },
            ),
            "vcs": (
                self.lock["version"],
                {
                    **self.direct_url,
                    "vcs_info": {**self.direct_url["vcs_info"], "vcs": "hg"},
                },
            ),
            "requested revision": (
                self.lock["version"],
                {
                    **self.direct_url,
                    "vcs_info": {
                        **self.direct_url["vcs_info"],
                        "requested_revision": "a" * 40,
                    },
                },
            ),
            "resolved commit": (
                self.lock["version"],
                {
                    **self.direct_url,
                    "vcs_info": {
                        **self.direct_url["vcs_info"],
                        "commit_id": "b" * 40,
                    },
                },
            ),
        }
        for label, (version, direct_url) in cases.items():
            with self.subTest(seam=label), mock.patch.object(
                I.importlib.metadata,
                "distribution",
                return_value=FakeDistribution(version, direct_url),
            ):
                with self.assertRaisesRegex(I.IdentityError, "identity mismatch"):
                    I.inspect_installed(self.lock)

    def test_missing_or_malformed_direct_url_fails_closed(self):
        for direct_url in (None, "not-json", [], {"url": self.lock["repository"]}):
            with self.subTest(direct_url=direct_url), mock.patch.object(
                I.importlib.metadata,
                "distribution",
                return_value=FakeDistribution(self.lock["version"], direct_url),
            ):
                with self.assertRaises(I.IdentityError):
                    I.inspect_installed(self.lock)

    def test_lock_rejects_unknown_fields_and_unsafe_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            for mutation in (
                {**self.lock, "extra": "drift"},
                {**self.lock, "repository": "https://token@github.com/x/y.git"},
                {**self.lock, "commit": "short"},
            ):
                path.write_text(json.dumps(mutation))
                with self.subTest(mutation=mutation), self.assertRaises(I.IdentityError):
                    I.load_lock(path)

    def test_deploy_entrypoints_install_and_verify_the_same_lock(self):
        bootstrap = (REPO / "deploy" / "bootstrap.sh").read_text()
        preflight = (REPO / "deploy" / "preflight.sh").read_text()
        self.assertIn(
            'OMNIGENT_RUNTIME_LOCK="$TEAMS_DIR/deploy/omnigent-runtime.lock.json"',
            bootstrap,
        )
        self.assertIn(
            'uv tool install --python 3.12 --force "$OMNIGENT_REQUIREMENT"',
            bootstrap,
        )
        self.assertLess(
            bootstrap.index(" verify\n"), bootstrap.index("npm config set prefix")
        )
        self.assertIn("omnigent-runtime-identity.py", preflight)
        self.assertIn("Omnigent runtime identity mismatch", preflight)


if __name__ == "__main__":
    unittest.main()
