"""Audit C6 -- deploy/lib/trust_anchors.py decides whether the runtime user
(every worker's uid) can change a file that configures the fences, and
promotes the authority registries into a root-owned copy the fences read.

The audit is checked against a uid that is NOT the test's own, because CI
cannot create root-owned files: from that uid's point of view the test's
files are as foreign as root's are to a worker.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "trust_anchors", REPO / "deploy" / "lib" / "trust_anchors.py")
TA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TA)
sys.path.insert(0, str(REPO / "bin"))
import nightshift_authority as authority  # noqa: E402

OTHER = 4242
NO_GROUPS = frozenset()


class PathFindings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "etc"
        self.dir.mkdir(mode=0o755)
        self.file = self.dir / "server.env"
        self.file.write_text("X=1\n")
        self.file.chmod(0o644)

    def test_the_owner_can_always_change_it_whatever_the_mode(self):
        self.file.chmod(0o444)
        findings = TA.path_findings(str(self.file), os.geteuid(), NO_GROUPS)
        self.assertTrue(any("owned by the runtime uid" in f for f in findings))

    def test_a_foreign_read_only_file_in_a_foreign_dir_is_safe(self):
        self.assertEqual(TA.path_findings(str(self.file), OTHER, NO_GROUPS), [])

    def test_world_or_member_group_write_is_a_finding(self):
        self.file.chmod(0o666)
        self.assertTrue(TA.path_findings(str(self.file), OTHER, NO_GROUPS))
        self.file.chmod(0o664)
        gid = self.file.stat().st_gid
        self.assertTrue(TA.path_findings(str(self.file), OTHER, frozenset({gid})))
        self.assertEqual(TA.path_findings(str(self.file), OTHER, NO_GROUPS), [])

    def test_a_writable_directory_above_lets_it_be_replaced(self):
        self.dir.chmod(0o777)
        findings = TA.path_findings(str(self.file), OTHER, NO_GROUPS)
        self.assertTrue(any("a directory above it" in f for f in findings), findings)

    def test_a_sticky_directory_protects_entries_the_uid_does_not_own(self):
        self.dir.chmod(0o1777)
        self.assertEqual(TA.path_findings(str(self.file), OTHER, NO_GROUPS), [])

    def test_root_as_the_runtime_uid_is_always_a_finding(self):
        self.assertTrue(TA.path_findings(str(self.file), 0, NO_GROUPS))

    def test_tree_findings_reach_every_entry(self):
        inner = self.dir / "policies"
        inner.mkdir(mode=0o755)
        (inner / "p.py").write_text("")
        (inner / "p.py").chmod(0o666)
        findings = TA.tree_findings(str(self.dir), OTHER, NO_GROUPS)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("p.py", findings[0])

    def test_cli_exit_code_follows_the_findings(self):
        script = REPO / "deploy" / "lib" / "trust_anchors.py"
        clean = subprocess.run([sys.executable, str(script), "audit", "--uid",
                                str(OTHER), str(self.file)], capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stdout)
        own = subprocess.run([sys.executable, str(script), "audit", "--uid",
                              str(os.geteuid()), str(self.file)], capture_output=True, text=True)
        self.assertEqual(own.returncode, 1)


class RegistryPromotion(unittest.TestCase):
    """The fences read the promoted copy; it must answer read-only exactly as
    the runtime-owned original does, and later edits to the original must
    not reach it until root promotes again."""

    REPO_ID = 987654

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "state")
        os.mkdir(self.state, 0o700)
        self.anchor = os.path.join(tmp.name, "anchor")
        cx = authority.connect(self.state, self.REPO_ID)
        authority.declare_repository_ownership(
            cx, repository_id=self.REPO_ID, ownership="self", declared_by="fixture")
        cx.close()

    def _lock(self):
        for base, dirs, files in os.walk(self.anchor):
            for name in files:
                os.chmod(os.path.join(base, name), 0o444)
            os.chmod(base, 0o555)
        self.addCleanup(lambda: [os.chmod(b, 0o755) for b, _d, _f in os.walk(self.anchor)])

    def test_promoted_copy_classifies_self_while_read_only(self):
        written = TA.promote(self.state, self.anchor, self.REPO_ID)
        self.assertEqual([os.path.basename(p) for p in written], ["authority.db"])
        target = Path(written[0])
        self.assertFalse(Path(str(target) + "-wal").exists())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self._lock()
        self.assertEqual(
            authority.classify_repository(self.anchor, self.REPO_ID)["ownership"], "self")
        self.assertEqual(sorted(os.listdir(target.parent)), ["authority.db"],
                         "a read must not leave sidecars in the root-owned copy")

    def test_a_registry_symlinked_out_of_the_state_root_is_refused(self):
        os.makedirs(os.path.join(self.anchor + "-elsewhere", str(self.REPO_ID)))
        os.rename(os.path.join(self.state, "authority"),
                  os.path.join(self.state, "authority-real"))
        os.symlink(self.anchor + "-elsewhere", os.path.join(self.state, "authority"))
        with self.assertRaises(SystemExit):
            TA.promote(self.state, self.anchor, self.REPO_ID)


class RootStepWiring(unittest.TestCase):
    """The runtime user no longer writes the server environment; root does,
    and both units read root's copy."""

    def test_bootstrap_does_not_write_a_server_env(self):
        text = (REPO / "deploy" / "bootstrap.sh").read_text()
        self.assertNotIn('> "$ENVFILE"', text)
        self.assertNotIn("which is root-installed\n# and 0600", text)
        self.assertIn("install-root-config.sh", text)

    def test_units_read_the_root_owned_file(self):
        for unit in ("omnigent-server.service", "omnigent-host.service"):
            text = (REPO / "deploy" / "systemd" / unit).read_text()
            with self.subTest(unit=unit):
                self.assertIn("EnvironmentFile=/etc/nightshift/server.env\n", text)
                self.assertNotIn(".config/omnigent/server.env", text)

    @unittest.skipIf(os.geteuid() == 0, "needs a non-root uid")
    def test_root_step_refuses_to_run_unprivileged(self):
        proc = subprocess.run(
            ["bash", str(REPO / "deploy" / "install-root-config.sh"), "--user", "nobody"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("run as root", proc.stderr)


if __name__ == "__main__":
    unittest.main()
