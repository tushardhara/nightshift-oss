"""Deterministic, offline tests for the exact-SHA release state machine."""

from __future__ import annotations

import importlib.util
import ast
import io
import json
import os
import re
import shlex
import stat
import subprocess
import tarfile
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "deploy" / "exact_sha_release.py"
BROKER = (
    REPO
    / "program/agents/crew_alpha/agents/eng_codex/tools/python/"
    / "nightshift_publish.py"
)
SPEC = importlib.util.spec_from_file_location("exact_sha_release", MODULE)
assert SPEC and SPEC.loader
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def empty_supervision(mode: str = "bootstrap", **overrides) -> dict:
    supervision = {
        "schema": R.SUPERVISION_SCHEMA,
        "mode": mode,
        "predecessor": None,
        "inherited_masked": [],
        "to_mask": [],
    }
    supervision.update(overrides)
    return supervision


def bundle_tree_bytes(root: Path) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            archive.add(path, arcname=path.relative_to(root), recursive=False)
    return output.getvalue()


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo,
                       check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo,
                       check=True)
        (self.repo / "program").mkdir()
        (self.repo / "program" / "config.yaml").write_text("name: program\n")
        executable = self.repo / "program" / "tool.sh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        subprocess.run(["git", "add", "program"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo,
                       check=True)
        self.sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def test_git_object_and_filesystem_manifests_match(self):
        git = R.git_tree_manifest(self.repo, self.sha)
        filesystem = R.filesystem_manifest(self.repo / "program")
        self.assertEqual(git["digest"], filesystem["digest"])
        self.assertEqual([e["mode"] for e in git["entries"]],
                         ["100644", "100755"])

    def test_one_uncommitted_byte_turns_the_content_gate_red(self):
        git = R.git_tree_manifest(self.repo, self.sha)
        (self.repo / "program" / "config.yaml").write_text("name: wrong\n")
        filesystem = R.filesystem_manifest(self.repo / "program")
        self.assertNotEqual(git["digest"], filesystem["digest"])

    def test_git_manifest_ignores_replacement_objects(self):
        original_oid = subprocess.check_output(
            ["git", "rev-parse", f"{self.sha}:program/config.yaml"],
            cwd=self.repo,
            text=True,
        ).strip()
        replacement = self.repo / "replacement"
        replacement.write_text("name: replaced\n")
        replacement_oid = subprocess.check_output(
            ["git", "hash-object", "-w", str(replacement)],
            cwd=self.repo,
            text=True,
        ).strip()
        subprocess.run(
            ["git", "replace", original_oid, replacement_oid],
            cwd=self.repo,
            check=True,
        )
        (self.repo / "program" / "config.yaml").write_text("name: replaced\n")

        git = R.git_tree_manifest(self.repo, self.sha)
        filesystem = R.filesystem_manifest(self.repo / "program")

        self.assertNotEqual(git["digest"], filesystem["digest"])
        config = next(row for row in git["entries"]
                      if row["path"] == "config.yaml")
        self.assertEqual(
            config["sha256"], R._sha256(b"name: program\n")
        )

    def test_tracked_symlink_inside_tree_is_allowed_escape_is_refused(self):
        (self.repo / "program" / "inside").symlink_to("config.yaml")
        subprocess.run(["git", "add", "program/inside"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "inside link"], cwd=self.repo,
                       check=True)
        R.reject_escaping_tracked_symlinks(self.repo)
        (self.repo / "program" / "escape").symlink_to("../../outside")
        subprocess.run(["git", "add", "program/escape"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "escape link"], cwd=self.repo,
                       check=True)
        with self.assertRaisesRegex(R.ReleaseError, "escapes checkout"):
            R.reject_escaping_tracked_symlinks(self.repo)


def _test_uid_stands_in_for_root(case):
    # CI cannot create root-owned files. Fixtures that are about something
    # other than ownership let this uid count as the sealed owner; the
    # SealOwnership tests below exercise the real {root} set.
    patcher = mock.patch.object(R, "_sealed_owner_uids",
                                return_value={R.SEALED_OWNER_UID, os.geteuid()})
    patcher.start()
    case.addCleanup(patcher.stop)


class ReleaseSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _test_uid_stands_in_for_root(self)

    def _cutover_candidate(self, name):
        root = self.root / name
        release = root / "release"
        (release / "program").mkdir(parents=True)
        (release / "deploy" / "policies").mkdir(parents=True)
        (release / "program" / "config.yaml").write_text("name: program\n")
        (release / "deploy" / "policies" / "policy.py").write_text(
            "VALUE = 'reviewed'\n"
        )
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=release,
                       check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                       cwd=release, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=release,
                       check=True)
        subprocess.run(["git", "add", "program", "deploy/policies"],
                       cwd=release, check=True)
        subprocess.run(["git", "commit", "-qm", "candidate"], cwd=release,
                       check=True)
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=release, text=True
        ).strip()
        state_dir = root / "state"
        state_dir.mkdir()
        plan = {
            "plan_digest": "d" * 64,
            "sha": sha,
            "release_root": str(release),
            "state_dir": str(state_dir),
            "legacy_units": [],
            "supervision": empty_supervision(),
            "program_git_manifest": R.git_tree_manifest(release, sha),
            "program_filesystem_manifest": R.filesystem_manifest(
                release / "program"
            ),
            "policy_git_manifest": R.git_tree_manifest(
                release, sha, "deploy/policies"
            ),
            "policy_filesystem_manifest": R.filesystem_manifest(
                release / "deploy" / "policies"
            ),
        }
        R.seal_tree(release)
        plan_path = root / "plan.json"
        plan_path.write_text("{}")
        authorization = {
            "old_pid": 10,
            "old_pid_epoch": "server-epoch",
            "old_host_pid": 11,
            "old_host_pid_epoch": "host-epoch",
            "old_runner_pids": [12],
        }
        authorization_path = root / "authorization.json"
        authorization_path.write_text(json.dumps(authorization))
        args = mock.Mock(
            plan=str(plan_path), authorization=str(authorization_path),
            owner_go=plan["plan_digest"],
        )
        return release, plan, authorization, args

    @staticmethod
    def _make_tree_writable(root):
        for path in [root, *root.rglob("*")]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IWUSR)

    def test_seal_removes_every_write_bit(self):
        tree = self.root / "tree"
        (tree / "nested").mkdir(parents=True)
        (tree / "nested" / "file").write_text("bytes")
        R.seal_tree(tree)
        self.assertTrue(R.tree_is_write_protected(tree))
        self.assertTrue(R.tree_is_sealed(tree))
        self.assertEqual((tree / "nested" / "file").stat().st_mode & 0o222, 0)


    def test_control_db_must_be_absolute_canonical_regular_and_not_symlinked(self):
        db = (self.root / "control.db").resolve()
        db.write_bytes(b"sqlite placeholder")
        identity = R._control_identity(db)
        self.assertEqual(identity["path"], str(db))
        link = self.root / "link.db"
        link.symlink_to(db)
        with self.assertRaisesRegex(R.ReleaseError, "symlink"):
            R._control_identity(link)
        with self.assertRaisesRegex(R.ReleaseError, "absolute"):
            R._control_identity(Path("relative.db"))

    def test_explicit_omnigent_python_preserves_virtualenv_symlink(self):
        system_python = self.root / "usr" / "bin" / "python3.12"
        system_python.parent.mkdir(parents=True)
        system_python.write_text("#!/bin/sh\nexit 0\n")
        system_python.chmod(0o755)
        tool_bin = self.root / "uv" / "tools" / "omnigent" / "bin"
        tool_bin.mkdir(parents=True)
        interpreter = tool_bin / "python"
        interpreter.symlink_to(system_python)
        launcher = tool_bin / "omnigent"
        launcher.write_text(f"#!{interpreter}\n")
        launcher.chmod(0o755)

        selected = R._omnigent_python(str(interpreter))

        self.assertEqual(selected, str(interpreter))
        self.assertNotEqual(selected, str(system_python))
        self.assertEqual(
            (Path(selected).parent / "omnigent").resolve(strict=True),
            launcher.resolve(strict=True),
        )

    def test_explicit_omnigent_python_rejects_relative_and_broken_paths(self):
        with self.assertRaisesRegex(R.ReleaseError, "absolute"):
            R._omnigent_python("relative/bin/python")
        broken = self.root / "venv" / "bin" / "python"
        broken.parent.mkdir(parents=True)
        broken.symlink_to(self.root / "missing-python")
        with self.assertRaisesRegex(R.ReleaseError, "executable file"):
            R._omnigent_python(str(broken))

    def test_runtime_identity_uses_candidate_lock_and_target_interpreter(self):
        checkout = self.root / "checkout"
        deploy = checkout / "deploy"
        deploy.mkdir(parents=True)
        helper = deploy / "omnigent-runtime-identity.py"
        lock = deploy / "omnigent-runtime.lock.json"
        helper.write_text("# verifier\n")
        lock.write_text("{}\n")
        identity = {"matched": True, "observed": {"commit": "a" * 40}}
        with mock.patch.object(R, "_run", return_value=json.dumps(identity)) as run:
            observed = R._omnigent_runtime_identity("/venv/bin/python", checkout)
        self.assertEqual(observed, identity)
        run.assert_called_once_with(
            [
                "/venv/bin/python",
                str(helper),
                "--lock",
                str(lock),
                "verify",
            ],
            cwd=checkout,
        )

    def test_runtime_identity_reverification_refuses_drift(self):
        expected = {"matched": True, "observed": {"commit": "a" * 40}}
        plan = {
            "omnigent_python": "/venv/bin/python",
            "release_root": str(self.root),
            "omnigent_runtime_identity": expected,
        }
        with mock.patch.object(
            R,
            "_omnigent_runtime_identity",
            return_value={"matched": True, "observed": {"commit": "b" * 40}},
        ):
            with self.assertRaisesRegex(R.ReleaseError, "changed after prepare"):
                R._reverify_omnigent_runtime(plan)

    def test_identity_failure_prevents_candidate_bundle_import(self):
        with (
            mock.patch.object(
                R,
                "_omnigent_runtime_identity",
                side_effect=R.ReleaseError("unreviewed runtime"),
            ),
            mock.patch.object(R, "_load_bundle") as load_bundle,
        ):
            with self.assertRaisesRegex(R.ReleaseError, "unreviewed runtime"):
                R._verify_runtime_then_load_bundle("/venv/bin/python", self.root)
        load_bundle.assert_not_called()

    def test_runtime_start_reverifies_identity_before_tmux(self):
        with (
            mock.patch.object(
                R,
                "_reverify_omnigent_runtime",
                side_effect=R.ReleaseError("runtime drift"),
            ),
            mock.patch.object(R, "_start_tmux") as start,
        ):
            with self.assertRaisesRegex(R.ReleaseError, "runtime drift"):
                R._start_runtime_pair({}, rollback=False)
        start.assert_not_called()

    def test_postflight_identity_failure_prevents_durable_proof_work(self):
        plan = {
            "state_dir": str(self.root),
            "plan_digest": "d" * 64,
            "omnigent_runtime_identity": {"matched": True},
        }
        with (
            mock.patch.object(
                R,
                "_reverify_omnigent_runtime",
                side_effect=R.ReleaseError("runtime drift"),
            ),
            mock.patch.object(R, "_serving_pid") as serving_pid,
            mock.patch.object(R, "_new_session") as new_session,
            mock.patch.object(R, "_json_request") as policy_request,
            mock.patch.object(R.subprocess, "run") as smoke,
        ):
            receipt = R.postflight(plan, {})
        self.assertFalse(receipt["accepted"])
        self.assertEqual(
            receipt["proofs"]["runtime_environment"]["verdict"], "red"
        )
        self.assertIn("runtime drift", receipt["fatal"])
        serving_pid.assert_not_called()
        new_session.assert_not_called()
        policy_request.assert_not_called()
        smoke.assert_not_called()

    def test_unsafe_rollback_archive_never_extracts(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            info = tarfile.TarInfo("../escape")
            payload = b"bad"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        with self.assertRaisesRegex(R.ReleaseError, "unsafe"):
            R._safe_extract_tar(archive.getvalue(), self.root / "extract")
        self.assertFalse((self.root / "escape").exists())

    def test_special_rollback_archive_member_never_extracts(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            info = tarfile.TarInfo("program/fifo")
            info.type = tarfile.FIFOTYPE
            tar.addfile(info)
        with self.assertRaisesRegex(R.ReleaseError, "special member"):
            R._safe_extract_tar(archive.getvalue(), self.root / "special")

    def test_every_proof_starts_undetermined_and_acceptance_false(self):
        receipt = R._receipt("a" * 64)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(set(receipt["proofs"]), set(R.PROOFS))
        self.assertTrue(all(item["verdict"] == "undetermined"
                            for item in receipt["proofs"].values()))

    def test_edited_plan_is_refused(self):
        body = {"schema": "nightshift-exact-sha-plan-v1", "sha": "a" * 40}
        body["plan_digest"] = R._sha256(R._canonical(body))
        body["sha"] = "b" * 40
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(body))
        with self.assertRaisesRegex(R.ReleaseError, "digest mismatch"):
            R._load_plan(plan)

    def test_short_sha_fails_before_any_clone_or_live_read(self):
        probes = [
            mock.patch.object(R, "_legacy_unit_inventory"),
            mock.patch.object(R, "_serving_pid"),
            mock.patch.object(R, "_control_snapshot"),
            mock.patch.object(R, "_run"),
        ]
        mocks = [patcher.start() for patcher in probes]
        self.addCleanup(lambda: [patcher.stop() for patcher in reversed(probes)])
        code = R.main([
            "prepare", "--source", str(self.root), "--sha", "abc",
            "--floor-sha", "0" * 40, "--scheduled-task-id", "task",
        ])
        self.assertEqual(code, 3)
        self.assertTrue(all(not probe.called for probe in mocks))

    def test_bundle_marker_is_only_in_the_claude_seat_prompt(self):
        architect = (REPO / "program" / "agents" / "architect" / "config.yaml").read_text()
        staff = (REPO / "program" / "agents" / "staff" / "config.yaml").read_text()
        root = (REPO / "program" / "config.yaml").read_text()
        self.assertIn(R.CLAUDE_MARKER, staff)
        self.assertNotIn(R.CLAUDE_MARKER, architect)
        self.assertNotIn(R.CLAUDE_MARKER, root)
        smoke_tree = ast.parse((REPO / "deploy" / "exact_sha_smoke.py").read_text())
        smoke_prompts = {
            node.targets[0].id: node.value.value
            for node in smoke_tree.body
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"PROMPT", "CONTINUE_PROMPT"}
                and isinstance(node.value, ast.Constant))
        }
        self.assertNotIn(R.CLAUDE_MARKER, smoke_prompts["PROMPT"])
        self.assertNotIn(R.CLAUDE_MARKER, smoke_prompts["CONTINUE_PROMPT"])

    def test_schedule_prompt_is_rewritten_one_for_one_to_release(self):
        release = self.root / "releases" / ("a" * 40) / "checkout"
        prompt = "first ./bin/nightshift-ctl status; then ./bin/nightshift-ctl slot list"
        pinned, count = R._pin_schedule_prompt(prompt, release)
        expected = str(release / "bin" / "nightshift-ctl")
        self.assertEqual(count, 2)
        self.assertEqual(pinned.count(expected), 2)
        self.assertNotIn("./bin/nightshift-ctl", pinned)

    def test_legacy_inventory_includes_required_and_discovered_stale_units(self):
        unit_dir = self.root / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        names = set(R.REQUIRED_LEGACY_UNITS) | {
            "extra-stale.service", "extra-stale.timer", "release-safe.service",
        }
        for name in names:
            (unit_dir / name).write_text("[Unit]\nDescription=test\n")
        (unit_dir / "extra-stale.service").write_text(
            "[Service]\nExecStart=/srv/nightshift/deploy/helper.sh\n"
        )
        (unit_dir / "extra-stale.timer").write_text(
            "[Timer]\nUnit=extra-stale.service\n"
        )
        (unit_dir / "release-safe.service").write_text(
            "[Service]\nExecStart=/srv/nightshift-releases/current/helper.sh\n"
        )

        def systemctl(*args, **_kwargs):
            if args[0] == "list-unit-files":
                return "\n".join(f"{name} disabled" for name in sorted(names))
            name = args[1]
            return (
                "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
                f"UnitFileState=disabled\nFragmentPath={unit_dir / name}"
            )

        with (mock.patch.object(R.Path, "home", return_value=self.root),
              mock.patch.object(R, "_systemctl_user", side_effect=systemctl)):
            inventory = R._legacy_unit_inventory()
        selected = {row["name"] for row in inventory}
        self.assertTrue(R.REQUIRED_LEGACY_UNITS.issubset(selected))
        self.assertIn("extra-stale.service", selected)
        self.assertIn("extra-stale.timer", selected)
        self.assertNotIn("release-safe.service", selected)
        extra = next(row for row in inventory if row["name"] == "extra-stale.service")
        self.assertEqual(
            extra["sha256"], R._sha256((unit_dir / "extra-stale.service").read_bytes())
        )

    def test_systemctl_failure_and_incomplete_state_fail_closed(self):
        proc = mock.Mock(returncode=1, stderr="no user bus", stdout="")
        with mock.patch.object(R.subprocess, "run", return_value=proc) as run:
            with self.assertRaisesRegex(R.ReleaseError, "no user bus"):
                R._systemctl_user("show", "x.service")
        self.assertEqual(
            run.call_args.kwargs["env"]["XDG_RUNTIME_DIR"],
            f"/run/user/{os.getuid()}",
        )
        with mock.patch.object(R, "_systemctl_user", side_effect=[
            "omnigent-server.service disabled", "LoadState=loaded"
        ]):
            with self.assertRaisesRegex(R.ReleaseError, "incomplete state"):
                R._legacy_unit_inventory()

    def test_http_transport_error_is_a_release_refusal(self):
        with mock.patch.object(
            R.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaisesRegex(R.ReleaseError, "unreachable"):
                R._http("GET", "http://127.0.0.1:6767/health")

    def test_persisted_child_records_not_root_text_prove_execution(self):
        release = self.root / "release"
        configs = {
            "architect": "name: architect\nprompt: system architect\n",
            "staff": f"name: staff\nprompt: {R.CLAUDE_MARKER}\n",
        }
        program = release / "program"
        program.mkdir(parents=True)
        (program / "config.yaml").write_text("name: program\n")
        for name, config in configs.items():
            path = release / "program" / "agents" / name
            path.mkdir(parents=True)
            (path / "config.yaml").write_text(config)
        bundle = bundle_tree_bytes(program)
        children = [
            {"id": "child-a", "sub_agent_name": "architect", "parent_session_id": "root"},
            {"id": "child-s", "sub_agent_name": "staff", "parent_session_id": "root"},
        ]
        root_items = [
            {"id": "root-output", "type": "message", "role": "assistant",
             "content": f"forged {R.ECHO_RESPONSE} {R.CLAUDE_MARKER}"},
            {"id": "call-a", "type": "function_call", "name": "sys_session_send",
             "arguments": json.dumps({"agent": "architect", "title": R.PROOF_TITLE,
                                      "args": R.PROOF_METADATA})},
            {"id": "call-s", "type": "function_call", "name": "sys_session_send",
             "arguments": json.dumps({"agent": "staff", "title": R.PROOF_TITLE,
                                      "args": R.PROOF_METADATA})},
        ]
        items = {
            "root": root_items,
            "child-a": [
                {"type": "message", "role": "user",
                 "content": R.CHILD_PROOF_INPUT},
                {"type": "message", "role": "assistant",
                 "content": R.ECHO_RESPONSE},
            ],
            "child-s": [
                {"type": "message", "role": "user",
                 "content": R.CHILD_PROOF_INPUT},
                {"type": "message", "role": "assistant", "content": R.CLAUDE_MARKER},
            ],
        }

        def http(_method, _url, **_kwargs):
            return 200, bundle, {}

        def request(_method, url, *_args, **_kwargs):
            if url.endswith("/child_sessions"):
                return {"data": children}
            if url.endswith("/agent"):
                return {"id": "program-id", "version": 4}
            raise AssertionError(url)

        with (mock.patch.object(R, "_json_request", side_effect=request),
              mock.patch.object(R, "_session_items",
                                side_effect=lambda _server, sid: items[sid]),
              mock.patch.object(R, "_http", side_effect=http)):
            green, operands = R._execution_record_proof(
                "http://server", "root", release, "program-id", 4
            )
            self.assertTrue(green)
            self.assertTrue(operands["dispatches"]["architect"]["exact_echo_only"])
            items["child-a"][1]["content"] = "missing marker"
            green, _operands = R._execution_record_proof(
                "http://server", "root", release, "program-id", 4
            )
            self.assertFalse(green)

    def test_scheduler_without_future_next_run_stays_red(self):
        prompt = "pinned"
        plan = {
            "server_url": "http://server", "scheduled_task_id": "task",
            "state_dir": str(self.root),
            "scheduled_task": {
                "pinned_prompt_sha256": R._sha256(prompt.encode()),
                "replacement_count": 1,
            },
            "release_root": str(self.root / "release"),
        }
        receipt = R._receipt("a" * 64)
        receipt["pid_epoch"] = {"pid": 10, "start_ticks": "epoch"}
        receipt["proofs"]["runtime_environment"] = {
            "verdict": "green", "reason": "", "operands": {
                "host_pid": 20, "runner_pids": [30],
            },
        }
        with (mock.patch.object(R, "_serving_pid", return_value=10),
              mock.patch.object(R, "_pid_epoch", return_value="epoch"),
              mock.patch.object(R, "_host_status", return_value={"pid": 20}),
              mock.patch.object(R, "_runner_pids", return_value=[30]),
              mock.patch.object(R, "_patch_scheduled_task", return_value={
                  "id": "task", "state": "active", "prompt": prompt,
                  "next_run_at": None,
              })):
            with self.assertRaisesRegex(R.ReleaseError, "scheduler continuity"):
                R._activate_pinned_schedule(plan, receipt)
        self.assertEqual(receipt["proofs"]["scheduler_continuity"]["verdict"], "red")

    def test_rollback_can_recover_when_server_listener_is_absent(self):
        plan = {
            "server_url": "http://server",
            "launch_argv": ["tmux", "-s", "forward-server"],
            "rollback_argv": ["tmux", "-s", "rollback-server"],
            "host_launch_argv": ["tmux", "-s", "forward-host"],
            "rollback_host_argv": ["tmux", "-s", "rollback-host"],
        }
        verified = {"green": True}
        with (mock.patch.object(R, "_host_status", side_effect=R.ReleaseError("down")),
              mock.patch.object(R, "_serving_pid", side_effect=R.ReleaseError("down")),
              mock.patch.object(R, "_kill_own_tmux"),
              mock.patch.object(R, "_wait_for_health", return_value=True),
              mock.patch.object(R, "_start_runtime_pair") as start,
              mock.patch.object(R, "_restore_legacy_units_if_masked"),
              mock.patch.object(R, "_restore_scheduled_task"),
              mock.patch.object(R, "_verify_rollback", return_value=verified)):
            self.assertEqual(R._rollback_live(plan), verified)
        start.assert_called_once_with(plan, rollback=True)

    def test_stop_failure_runs_verified_prestart_recovery(self):
        plan_path = self.root / "plan.json"
        plan_path.write_text("{}")
        auth_path = self.root / "authorization.json"
        auth_path.write_text("{}")
        plan = {
            "plan_digest": "d" * 64, "state_dir": str(self.root),
            "release_root": str(self.root / "release"), "legacy_units": [],
            "supervision": empty_supervision(),
        }
        args = mock.Mock(plan=str(plan_path), authorization=str(auth_path),
                         owner_go=plan["plan_digest"])
        recovered = {"green": True}
        with (mock.patch.object(R, "_load_plan", return_value=plan),
              mock.patch.object(R, "_fresh_cutover_gate"),
              mock.patch.object(R, "_mask_legacy_units", return_value={"units": []}),
              mock.patch.object(R, "_reverify_release_candidate"),
              mock.patch.object(R, "_stop_runtime_pair",
                                side_effect=R.ReleaseError("stop failed")),
              mock.patch.object(R, "_recover_before_candidate_start",
                                return_value=recovered) as recover):
            with self.assertRaisesRegex(R.ReleaseError, "verified restored"):
                R.cutover(args)
        recover.assert_called_once_with(plan, {})

    def test_final_release_check_failure_precedes_every_live_mutation(self):
        plan_path = self.root / "plan.json"
        plan_path.write_text("{}")
        auth_path = self.root / "authorization.json"
        auth_path.write_text("{}")
        plan = {
            "plan_digest": "d" * 64,
            "state_dir": str(self.root),
            "release_root": str(self.root / "release"),
            "legacy_units": [],
            "supervision": empty_supervision(),
        }
        args = mock.Mock(
            plan=str(plan_path),
            authorization=str(auth_path),
            owner_go=plan["plan_digest"],
        )
        with (
            mock.patch.object(R, "_load_plan", return_value=plan),
            mock.patch.object(R, "_fresh_cutover_gate"),
            mock.patch.object(
                R,
                "_reverify_release_candidate",
                side_effect=R.ReleaseError("candidate changed"),
            ),
            mock.patch.object(R, "_mask_legacy_units") as mask,
            mock.patch.object(R, "_stop_runtime_pair") as stop,
            mock.patch.object(R, "_start_runtime_pair") as start,
        ):
            with self.assertRaisesRegex(R.ReleaseError, "candidate changed"):
                R.cutover(args)

        mask.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()
        attempt = json.loads((self.root / "cutover-attempt.json").read_text())
        self.assertEqual(attempt["status"], "refused_before_mutation")

    def test_postflight_evidence_failure_runs_verified_rollback(self):
        plan_path = self.root / "plan.json"
        plan_path.write_text("{}")
        auth_path = self.root / "authorization.json"
        authorization = {
            "old_pid": 10,
            "old_pid_epoch": "old-server-epoch",
            "old_host_pid": 11,
            "old_host_pid_epoch": "old-host-epoch",
            "old_runner_pids": [12],
        }
        auth_path.write_text(json.dumps(authorization))
        plan = {
            "plan_digest": "d" * 64,
            "state_dir": str(self.root),
            "release_root": str(self.root / "release"),
            "legacy_units": [],
            "supervision": empty_supervision(),
        }
        args = mock.Mock(plan=str(plan_path), authorization=str(auth_path),
                         owner_go=plan["plan_digest"])
        restored = {"green": True}
        runtime = {"server_pid": 20, "host": {"pid": 21},
                   "runner_pids": [22]}
        with (mock.patch.object(R, "_load_plan", return_value=plan),
              mock.patch.object(R, "_fresh_cutover_gate"),
              mock.patch.object(R, "_mask_legacy_units", return_value={"units": []}),
              mock.patch.object(R, "_reverify_release_candidate"),
              mock.patch.object(R, "_stop_runtime_pair"),
              mock.patch.object(R, "_start_runtime_pair", return_value=runtime),
              mock.patch.object(R, "_pid_epoch", return_value="epoch"),
              mock.patch.object(R, "_install_pinned_schedule"),
              mock.patch.object(R, "postflight",
                                side_effect=OSError("receipt disk full")),
              mock.patch.object(R, "_rollback_live",
                                return_value=restored) as rollback):
            with self.assertRaisesRegex(R.ReleaseError,
                                        "verified rollback restored"):
                R.cutover(args)
        rollback.assert_called_once_with(plan)
        attempt = json.loads((self.root / "cutover-attempt.json").read_text())
        self.assertEqual(attempt["status"],
                         "rolled_back_postflight_evidence_failure")
        self.assertEqual(attempt["rollback"], restored)

    def test_cutover_refuses_head_or_tree_swap_before_old_runtime_stop(self):
        for kind in ("head", "tree"):
            with self.subTest(swap=kind):
                release, plan, _authorization, args = \
                    self._cutover_candidate(kind)

                def swap_candidate(_state_dir, _legacy_units):
                    self._make_tree_writable(release)
                    target = release / "program" / "config.yaml"
                    target.write_text(f"name: swapped-{kind}\n")
                    if kind == "head":
                        subprocess.run(
                            ["git", "add", "program/config.yaml"],
                            cwd=release,
                            check=True,
                        )
                        subprocess.run(
                            ["git", "commit", "-qm", "swap candidate"],
                            cwd=release,
                            check=True,
                        )
                    return {"units": []}

                with (
                    mock.patch.object(R, "_load_plan", return_value=plan),
                    mock.patch.object(R, "_fresh_cutover_gate"),
                    mock.patch.object(
                        R, "_mask_legacy_units", side_effect=swap_candidate
                    ),
                    mock.patch.object(R, "_stop_runtime_pair") as stop,
                    mock.patch.object(
                        R, "_recover_before_candidate_start",
                        return_value={"green": True},
                    ),
                ):
                    with self.assertRaisesRegex(R.ReleaseError, "verified restored"):
                        R.cutover(args)
                stop.assert_not_called()

    def test_provenance_parser_finds_only_machine_readable_canary_data(self):
        proof = {"kind": "nightshift_release_provenance", "pid": 42}
        nested = {"result": "POLICY_ACTION_DENY",
                  "reason": json.dumps(proof, separators=(",", ":"))}
        self.assertEqual(R._extract_provenance(nested), proof)
        self.assertIsNone(R._extract_provenance({"reason": "ordinary denial"}))

    def test_stale_authorization_refuses_before_any_live_probe(self):
        with self.assertRaisesRegex(R.ReleaseError, "older than 10 minutes"):
            R._fresh_cutover_gate(
                {"plan_digest": "a" * 64},
                {"plan_digest": "a" * 64, "authorized_at": time.time() - 601},
            )

    def test_launch_wrapper_has_one_agent_root_and_no_global_stop(self):
        wrapper = (REPO / "deploy" / "run-exact-sha-server.sh").read_text()
        host_wrapper = (REPO / "deploy" / "run-exact-sha-host.sh").read_text()
        self.assertIn('export NIGHTSHIFT_CTL_DB="$control_db"', wrapper)
        self.assertIn('export PYTHONPATH="$policy_dir"', wrapper)
        self.assertIn('--agent "$agent_root"', wrapper)
        self.assertIn('--database-uri "$OMNIGENT_DATABASE_URI"', wrapper)
        self.assertIn('--expected-sha', wrapper)
        self.assertIn('/usr/bin/git --no-replace-objects', wrapper)
        self.assertIn("unset GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY", wrapper)
        self.assertIn("${!GIT_CONFIG@}", wrapper)
        self.assertLess(
            wrapper.index("export PATH=/usr/bin:/bin"),
            wrapper.index("/usr/bin/git --no-replace-objects"),
        )
        self.assertNotIn("omnigent stop", wrapper)
        self.assertIn('export NIGHTSHIFT_CTL_DB="$control_db"', host_wrapper)
        self.assertIn('--expected-sha', host_wrapper)
        self.assertIn('/usr/bin/git --no-replace-objects', host_wrapper)
        self.assertIn(
            "unset GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY", host_wrapper
        )
        self.assertIn("${!GIT_CONFIG@}", host_wrapper)
        self.assertLess(
            host_wrapper.index("export PATH=/usr/bin:/bin"),
            host_wrapper.index("/usr/bin/git --no-replace-objects"),
        )
        self.assertEqual(
            host_wrapper.count("export HARNESS_CODEX_DISABLE_NATIVE_TOOLS=1"), 1)
        self.assertEqual(
            host_wrapper.count("export HARNESS_CODEX_ENABLE_WEB_SEARCH=0"), 1)
        self.assertIn('exec "$omnigent_bin" host --server "$server"', host_wrapper)
        self.assertNotIn("omnigent stop", host_wrapper)

    def test_wrappers_and_broker_share_only_the_canonical_control_db_name(self):
        tree = ast.parse(BROKER.read_text(encoding="utf-8"))
        broker_names = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_CONTROL_DB_ENV"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ]
        self.assertEqual(broker_names, ["NIGHTSHIFT_CTL_DB"])

        for relative in (
            "deploy/run-exact-sha-server.sh",
            "deploy/run-exact-sha-host.sh",
        ):
            wrapper = (REPO / relative).read_text(encoding="utf-8")
            exported = re.findall(
                r'^export (NIGHTSHIFT_[A-Z_]*DB)="\$control_db"$',
                wrapper,
                re.MULTILINE,
            )
            self.assertEqual(exported, broker_names, relative)
            self.assertNotIn("export NIGHTSHIFT_CONTROL_DB", wrapper)
            self.assertEqual(wrapper.count("unset NIGHTSHIFT_CONTROL_DB"), 1)

    def test_runtime_environment_proof_requires_live_codex_host_bindings(self):
        release_bin = "/srv/releases/exact/bin"
        expected = {
            "NIGHTSHIFT_RELEASE_ROOT": "/srv/releases/exact",
            "NIGHTSHIFT_RELEASE_SHA": "a" * 40,
            "NIGHTSHIFT_CTL_DB": "/srv/state/control.db",
            "PYTHONPATH": "/srv/releases/exact/deploy/policies",
        }
        binding = {
            "HARNESS_CODEX_DISABLE_NATIVE_TOOLS": "1",
            "HARNESS_CODEX_ENABLE_WEB_SEARCH": "0",
        }
        common = {**expected, "PATH": release_bin + ":/usr/bin:/bin"}
        environments = {
            "server": dict(common),
            "host": {**common, **binding},
            "runner": {**common, **binding},
        }

        self.assertTrue(R._runtime_environments_match(
            environments, expected, release_bin, binding))
        for process in ("host", "runner"):
            for key in binding:
                hostile = {name: dict(values)
                           for name, values in environments.items()}
                hostile[process].pop(key)
                self.assertFalse(R._runtime_environments_match(
                    hostile, expected, release_bin, binding),
                    f"{process} without {key} must fail live proof")
        for process in environments:
            hostile = {name: dict(values)
                       for name, values in environments.items()}
            hostile[process]["NIGHTSHIFT_CONTROL_DB"] = "/stale/control.db"
            self.assertFalse(
                R._runtime_environments_match(
                    hostile, expected, release_bin, binding
                ),
                f"{process} must not expose the rejected legacy DB alias",
            )
            fail_open = {name: dict(values)
                         for name, values in environments.items()}
            fail_open[process]["NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"] = "1"
            self.assertFalse(
                R._runtime_environments_match(
                    fail_open, expected, release_bin, binding
                ),
                f"{process} must not retain managed semaphore fail-open",
            )

    def test_postflight_selector_captures_and_rejects_legacy_control_db(self):
        release_root = str(self.root / "release")
        release_bin = str(Path(release_root) / "bin")
        omnigent_bin = str(Path(release_bin) / "omnigent")
        expected = {
            "NIGHTSHIFT_RELEASE_ROOT": release_root,
            "NIGHTSHIFT_RELEASE_SHA": "a" * 40,
            "NIGHTSHIFT_CTL_DB": str(self.root / "control.db"),
            "PYTHONPATH": str(Path(release_root) / "deploy" / "policies"),
        }
        binding = {
            "HARNESS_CODEX_DISABLE_NATIVE_TOOLS": "1",
            "HARNESS_CODEX_ENABLE_WEB_SEARCH": "0",
        }
        plan = {
            "plan_digest": "d" * 64,
            "state_dir": str(self.root),
            "server_url": "http://server",
            "sha": expected["NIGHTSHIFT_RELEASE_SHA"],
            "release_root": release_root,
            "omnigent_bin": omnigent_bin,
            "omnigent_runtime_identity": {"matched": True},
            "control_db": {
                "path": expected["NIGHTSHIFT_CTL_DB"],
                "device": 1,
                "inode": 2,
            },
            "control_before": {"task_count": 1, "agent_run_count": 1},
            "durable_session": {"id": "durable", "host_id": "host-1"},
        }
        authorization = {
            "pre_registration": {"id": "program-id", "version": 1}
        }
        pids = {"server": 10, "host": 20, "runner": 30}

        def receipt_for(legacy_process=None, fail_open_process=None,
                        runtime_drift=False):
            common = {**expected, "PATH": release_bin + ":/usr/bin:/bin"}
            environments = {
                pids["server"]: dict(common),
                pids["host"]: {**common, **binding},
                pids["runner"]: {**common, **binding},
            }
            if legacy_process is not None:
                environments[pids[legacy_process]]["NIGHTSHIFT_CONTROL_DB"] = (
                    "/stale/control.db"
                )
            if fail_open_process is not None:
                environments[pids[fail_open_process]][
                    "NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"
                ] = "1"

            def read_process_file(path):
                match = re.fullmatch(r"/proc/(\d+)/(environ|cmdline)", str(path))
                if match is None:
                    raise AssertionError(f"unexpected process read: {path}")
                pid = int(match.group(1))
                if match.group(2) == "cmdline":
                    return f"{omnigent_bin}\0serve\0".encode()
                return b"\0".join(
                    f"{key}={value}".encode()
                    for key, value in environments[pid].items()
                ) + b"\0"

            def verify_runtime_identity(_plan):
                if runtime_drift:
                    raise R.ReleaseError("installed runtime drift")
                return {"matched": True}

            with (
                mock.patch.object(R, "_serving_pid", return_value=pids["server"]),
                mock.patch.object(R, "_pid_epoch", return_value="epoch"),
                mock.patch.object(R, "_list_data", return_value=[]),
                mock.patch.object(R, "_host_status", return_value={
                    "pid": pids["host"],
                    "process": "online",
                    "host_status": "online",
                    "host_id": "host-1",
                }),
                mock.patch.object(
                    R, "_runner_pids", return_value=[pids["runner"]]
                ),
                mock.patch.object(
                    R.Path, "read_bytes", autospec=True,
                    side_effect=read_process_file,
                ),
                mock.patch.object(
                    R, "_control_identity", return_value={"device": 1, "inode": 2}
                ),
                mock.patch.object(R, "_control_snapshot", return_value={
                    "task_count": 1,
                    "agent_run_count": 1,
                }),
                mock.patch.object(R, "_json_request", return_value={"id": "durable"}),
                mock.patch.object(
                    R,
                    "_reverify_omnigent_runtime",
                    side_effect=verify_runtime_identity,
                ),
            ):
                return R.postflight(plan, authorization)

        canonical = receipt_for()
        self.assertEqual(
            canonical["proofs"]["runtime_environment"]["verdict"], "green"
        )
        drifted = receipt_for(runtime_drift=True)
        drift_proof = drifted["proofs"]["runtime_environment"]
        self.assertEqual(drift_proof["verdict"], "red")
        self.assertIn("installed runtime drift", drift_proof["operands"][
            "omnigent_runtime_identity_error"
        ])
        for process in ("server", "host", "runner"):
            with self.subTest(process=process):
                receipt = receipt_for(process)
                proof = receipt["proofs"]["runtime_environment"]
                self.assertEqual(proof["verdict"], "red")
                self.assertEqual(
                    proof["operands"]["environments"][process][
                        "NIGHTSHIFT_CONTROL_DB"
                    ],
                    "/stale/control.db",
                )
                fail_open_receipt = receipt_for(fail_open_process=process)
                fail_open_proof = fail_open_receipt["proofs"][
                    "runtime_environment"
                ]
                self.assertEqual(fail_open_proof["verdict"], "red")
                self.assertEqual(
                    fail_open_proof["operands"]["environments"][process][
                        "NIGHTSHIFT_SEMAPHORE_FAIL_OPEN"
                    ],
                    "1",
                )

    def test_postflight_enforcement_binds_controller_path_digest_and_git_bytes(self):
        release = (self.root / "canary-release").resolve()
        controller = release / "bin" / "nightshift-ctl"
        policy = release / "deploy" / "policies" / "omnipods_policies.py"
        controller.parent.mkdir(parents=True)
        policy.parent.mkdir(parents=True)
        reviewed_controller = b"reviewed standalone controller\n"
        controller.write_bytes(reviewed_controller)
        policy.write_bytes(b"reviewed root policy\n")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=release,
                       check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                       cwd=release, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=release,
                       check=True)
        subprocess.run(["git", "add", "bin", "deploy"], cwd=release,
                       check=True)
        subprocess.run(["git", "commit", "-qm", "reviewed release"],
                       cwd=release, check=True)
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=release, text=True,
        ).strip()
        state = self.root / "canary-state"
        state.mkdir()
        control_db = self.root / "control.db"
        control_db.write_bytes(b"db")
        plan = {
            "plan_digest": "d" * 64,
            "state_dir": str(state),
            "server_url": "http://server",
            "sha": sha,
            "release_root": str(release),
            "omnigent_bin": str(release / "bin" / "omnigent"),
            "omnigent_python": str(release / "bin" / "missing-python"),
            "omnigent_runtime_identity": {"matched": True},
            "project": str(self.root / "project"),
            "control_db": {"path": str(control_db), "device": 1, "inode": 2},
            "control_before": {"task_count": 1, "agent_run_count": 1},
            "durable_session": {"id": "durable", "host_id": "host-1"},
        }
        authorization = {
            "pre_registration": {"id": "program-id", "version": 1},
        }
        pids = {"server": 10, "host": 20, "runner": 30}
        reviewed_digest = R._sha256(reviewed_controller)
        policy_digest = R._sha256(policy.read_bytes())

        def receipt_for(*, observed_path=None, observed_digest=None,
                        filesystem_bytes=None):
            controller.write_bytes(
                reviewed_controller if filesystem_bytes is None
                else filesystem_bytes
            )
            provenance = {
                "kind": "nightshift_release_provenance",
                "module": str(policy),
                "module_sha256": policy_digest,
                "admission_controller": str(
                    controller if observed_path is None else observed_path
                ),
                "admission_controller_sha256": (
                    R._sha256(controller.read_bytes())
                    if observed_digest is None else observed_digest
                ),
                "pid": pids["server"],
                "process_start_ticks": "epoch",
            }

            def request(method, url, *args, **kwargs):
                if url.endswith("/policies/evaluate"):
                    return {"result": "POLICY_ACTION_DENY",
                            "reason": json.dumps(provenance)}
                if url.endswith("/agent"):
                    return {"id": "program-id", "version": 2}
                if url.endswith("/sessions/durable"):
                    return {"id": "durable"}
                raise AssertionError(f"unexpected request: {method} {url}")

            expected_env = {
                "NIGHTSHIFT_RELEASE_ROOT": str(release),
                "NIGHTSHIFT_RELEASE_SHA": sha,
                "NIGHTSHIFT_CTL_DB": str(control_db),
                "PYTHONPATH": str(release / "deploy" / "policies"),
                "PATH": str(release / "bin") + ":/usr/bin:/bin",
            }

            def process_env(pid):
                environment = dict(expected_env)
                if pid in (pids["host"], pids["runner"]):
                    environment.update({
                        "HARNESS_CODEX_DISABLE_NATIVE_TOOLS": "1",
                        "HARNESS_CODEX_ENABLE_WEB_SEARCH": "0",
                    })
                return environment

            with (
                mock.patch.object(R, "_reverify_omnigent_runtime",
                                  return_value={"matched": True}),
                mock.patch.object(R, "_serving_pid", return_value=pids["server"]),
                mock.patch.object(R, "_pid_epoch", return_value="epoch"),
                mock.patch.object(R, "_list_data", return_value=[{
                    "id": "program-id", "version": 2, "name": "program",
                    "builtin": False,
                }]),
                mock.patch.object(R, "_new_session", return_value={"id": "proof"}),
                mock.patch.object(R, "_json_request", side_effect=request),
                mock.patch.object(R, "_http",
                                  side_effect=R.ReleaseError("skip served bundle")),
                mock.patch.object(R, "_host_status", return_value={
                    "pid": pids["host"], "process": "online",
                    "host_status": "online", "host_id": "host-1",
                }),
                mock.patch.object(R, "_runner_pids", return_value=[pids["runner"]]),
                mock.patch.object(R, "_selected_process_env",
                                  side_effect=process_env),
                mock.patch.object(R, "_control_identity",
                                  return_value={"device": 1, "inode": 2}),
                mock.patch.object(R, "_control_snapshot", return_value={
                    "task_count": 1, "agent_run_count": 1,
                }),
            ):
                return R.postflight(plan, authorization)

        canonical = receipt_for()
        self.assertEqual(
            canonical["proofs"]["enforced"]["verdict"], "green",
            canonical["proofs"]["enforced"],
        )

        wrong_path = receipt_for(observed_path=release / "bin" / "other")
        self.assertEqual(wrong_path["proofs"]["enforced"]["verdict"], "red")

        wrong_digest = receipt_for(observed_digest="0" * 64)
        self.assertEqual(wrong_digest["proofs"]["enforced"]["verdict"], "red")

        replaced = b"filesystem bytes not present in reviewed Git\n"
        git_drift = receipt_for(filesystem_bytes=replaced)
        drift_proof = git_drift["proofs"]["enforced"]
        self.assertEqual(drift_proof["verdict"], "red")
        self.assertEqual(
            drift_proof["operands"]["expected_admission_controller_sha256"],
            R._sha256(replaced),
        )
        self.assertEqual(
            drift_proof["operands"]["git_admission_controller_sha256"],
            reviewed_digest,
        )
        self.assertNotEqual(
            drift_proof["operands"]["expected_admission_controller_sha256"],
            drift_proof["operands"]["git_admission_controller_sha256"],
        )

    def _server_wrapper_fixture(self, database_line: str):
        release = self.root / "release"
        release.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=release,
                       check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                       cwd=release, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=release,
                       check=True)
        (release / "tracked").write_text("fixture")
        subprocess.run(["git", "add", "tracked"], cwd=release, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=release,
                       check=True)
        project = self.root / "project"
        project.mkdir()
        control_db = self.root / "control.db"
        control_db.write_bytes(b"sqlite fixture")
        server_env = self.root / "server.env"
        server_env.write_text(database_line)
        agent_root = self.root / "program"
        agent_root.mkdir()
        (agent_root / "config.yaml").write_text("name: program\n")
        policy_dir = self.root / "policies"
        policy_dir.mkdir()
        (policy_dir / "omnipods_policies.py").write_text("# fixture\n")
        argv_path = self.root / "omnigent.argv"
        env_path = self.root / "omnigent.env"
        omnigent = self.root / "omnigent"
        omnigent.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(argv_path))}\n"
            "printf '%s\\n' \"$NIGHTSHIFT_CTL_DB\" \"$PYTHONPATH\" "
            '"$NIGHTSHIFT_RELEASE_ROOT" "$NIGHTSHIFT_RELEASE_SHA" '
            '"${HARNESS_CODEX_DISABLE_NATIVE_TOOLS-}" '
            '"${HARNESS_CODEX_ENABLE_WEB_SEARCH-}" '
            '"${NIGHTSHIFT_CONTROL_DB-unset}" '
            '"${NIGHTSHIFT_SEMAPHORE_FAIL_OPEN-unset}" '
            f"> {shlex.quote(str(env_path))}\n"
        )
        omnigent.chmod(0o755)
        release_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=release, text=True
        ).strip()
        argv = [
            str(REPO / "deploy" / "run-exact-sha-server.sh"),
            "--release-root", str(release), "--expected-sha", release_sha,
            "--project", str(project),
            "--control-db", str(control_db), "--server-env", str(server_env),
            "--agent-root", str(agent_root), "--policy-dir", str(policy_dir),
            "--server", "http://127.0.0.1:6767",
            "--omnigent-bin", str(omnigent),
        ]
        return argv, argv_path, env_path, control_db, policy_dir

    def _hostile_git_overrides(self):
        redirect = self.root / "redirect.git"
        subprocess.run(["git", "init", "-q", "--bare", str(redirect)], check=True)
        hostile_config = self.root / "hostile.gitconfig"
        hostile_config.write_text("[core]\n\trepositoryformatversion = 99\n")
        return {
            "GIT_DIR": str(redirect),
            "GIT_WORK_TREE": str(self.root),
            "GIT_OBJECT_DIRECTORY": str(redirect / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.root / "missing-objects"),
            "GIT_REPLACE_REF_BASE": "refs/hostile/replace/",
            "GIT_CONFIG": str(hostile_config),
            "GIT_CONFIG_GLOBAL": str(hostile_config),
            "GIT_CONFIG_SYSTEM": str(hostile_config),
            "GIT_CONFIG_NOSYSTEM": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
            "GIT_CONFIG_VALUE_0": "99",
            "GIT_CONFIG_PARAMETERS": "'core.repositoryformatversion=99'",
        }

    def test_server_wrapper_refuses_an_environment_others_can_write(self):
        # Audit C6: server.env sets PYTHONPATH, the authority scope and the
        # fail-open switches. If anyone but the release owner can write it,
        # or the directory holding it, a worker chooses the next restart's
        # fences.
        for target in ("file", "directory"):
            with self.subTest(target=target):
                base = self.root / f"case-{target}"
                base.mkdir()
                saved, self.root = self.root, base
                try:
                    argv, argv_path, _env, _db, _policy = self._server_wrapper_fixture(
                        "OMNIGENT_DATABASE_URI=postgresql://t@127.0.0.1:5432/o\n")
                finally:
                    self.root = saved
                server_env = base / "server.env"
                (server_env if target == "file" else base).chmod(0o777 if target == "directory" else 0o666)
                proc = subprocess.run(argv, env=dict(os.environ), text=True,
                                      capture_output=True, check=False)
                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertIn("not group- or world-writable", proc.stderr)
                self.assertFalse(argv_path.exists())

    @unittest.skipUnless(os.geteuid() == 0, "chown needs root")
    def test_server_wrapper_refuses_a_runtime_owned_env_for_a_root_sealed_release(self):
        argv, argv_path, _env, _db, _policy = self._server_wrapper_fixture(
            "OMNIGENT_DATABASE_URI=postgresql://t@127.0.0.1:5432/o\n")
        os.chown(self.root / "server.env", 65534, 65534)
        proc = subprocess.run(argv, env=dict(os.environ), text=True,
                              capture_output=True, check=False)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("must be owned by uid 0", proc.stderr)
        self.assertFalse(argv_path.exists())

    def test_server_wrapper_refuses_missing_database_uri_before_exec(self):
        argv, argv_path, _env_path, _control_db, _policy_dir = \
            self._server_wrapper_fixture("OMNIGENT_DATABASE_URI=\n")
        environment = dict(os.environ)
        environment["OMNIGENT_DATABASE_URI"] = \
            "postgresql://stale:stale@127.0.0.1:5432/wrong"

        proc = subprocess.run(argv, env=environment, text=True,
                              capture_output=True, check=False)

        self.assertEqual(proc.returncode, 3)
        self.assertIn("missing OMNIGENT_DATABASE_URI", proc.stderr)
        self.assertFalse(argv_path.exists())

    def test_server_wrapper_refuses_a_password_in_the_database_uri(self):
        # Audit low: the URI is exec'd as `--database-uri`, so a password in
        # it was readable by every local user through ps. It now has to come
        # from PGPASSFILE, and the old fixture value is refused, not passed.
        base = self.root
        self.addCleanup(setattr, self, "root", base)
        for index, uri in enumerate((
                "postgresql://test:test@127.0.0.1:5432/omnigent",
                "postgresql://test@127.0.0.1:5432/omnigent?password=test")):
            with self.subTest(uri=uri):
                self.root = base / f"case-{index}"
                self.root.mkdir()
                argv, argv_path, _env, _db, _policy = \
                    self._server_wrapper_fixture(f"OMNIGENT_DATABASE_URI={uri}\n")
                proc = subprocess.run(argv, env=dict(os.environ), text=True,
                                      capture_output=True, check=False)
                self.assertEqual(proc.returncode, 3)
                self.assertIn("PGPASSFILE", proc.stderr)
                self.assertFalse(argv_path.exists())

    def test_server_wrapper_passes_database_uri_and_release_environment(self):
        database_uri = "postgresql://test@127.0.0.1:5432/omnigent"
        argv, argv_path, env_path, control_db, policy_dir = \
            self._server_wrapper_fixture(
                f"OMNIGENT_DATABASE_URI={database_uri}\n"
            )

        environment = dict(os.environ)
        environment["NIGHTSHIFT_CONTROL_DB"] = "/stale/control.db"
        proc = subprocess.run(
            argv, env=environment, text=True, capture_output=True, check=False
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        launched = argv_path.read_text().splitlines()
        self.assertEqual(launched.count("--database-uri"), 1)
        database_index = launched.index("--database-uri")
        self.assertEqual(launched[database_index + 1], database_uri)
        self.assertEqual(launched.count("--agent"), 1)
        environment = env_path.read_text().splitlines()
        self.assertEqual(
            environment,
            [
                str(control_db), str(policy_dir), str(self.root / "release"),
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.root / "release", text=True,
                ).strip(),
                "", "", "unset", "unset",
            ],
        )

    def test_server_wrapper_strips_fail_open_loaded_from_server_env(self):
        database_uri = "postgresql://test@127.0.0.1:5432/omnigent"
        argv, argv_path, env_path, _control_db, _policy_dir = \
            self._server_wrapper_fixture(
                f"OMNIGENT_DATABASE_URI={database_uri}\n"
                "export NIGHTSHIFT_SEMAPHORE_FAIL_OPEN='1'\n"
            )

        proc = subprocess.run(
            argv, env=dict(os.environ), text=True,
            capture_output=True, check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(argv_path.exists())
        self.assertEqual(env_path.read_text().splitlines()[-1], "unset")

    def test_wrappers_strip_every_fail_open_from_file_and_shell(self):
        # Audit low: only the semaphore's switch was unset, so
        # NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1 reached the policy evaluator.
        argv, _argv_path, _env_path, control_db, policy_dir = \
            self._server_wrapper_fixture(
                "OMNIGENT_DATABASE_URI=postgresql://test@127.0.0.1:5432/omnigent\n"
                "NIGHTSHIFT_AUTHORITY_FAIL_OPEN=1\n"
                "export SOME_FUTURE_FENCE_FAIL_OPEN=1\n"
            )
        dump = self.root / "omnigent.environ"
        omnigent = self.root / "omnigent"
        omnigent.write_text(f"#!/bin/sh\nenv > {shlex.quote(str(dump))}\n")
        environment = dict(os.environ, NIGHTSHIFT_AUTHORITY_FAIL_OPEN="1",
                           NIGHTSHIFT_SEMAPHORE_FAIL_OPEN="1")
        release = self.root / "release"
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=release,
                                      text=True).strip()
        host_argv = [
            str(REPO / "deploy" / "run-exact-sha-host.sh"),
            "--release-root", str(release), "--expected-sha", sha,
            "--project", str(self.root / "project"),
            "--control-db", str(control_db), "--policy-dir", str(policy_dir),
            "--server", "http://127.0.0.1:6767", "--omnigent-bin", str(omnigent),
        ]
        for label, command in (("server", argv), ("host", host_argv)):
            with self.subTest(wrapper=label):
                proc = subprocess.run(command, env=environment, text=True,
                                      capture_output=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                leaked = [line for line in dump.read_text().splitlines()
                          if line.split("=", 1)[0].endswith("_FAIL_OPEN")]
                self.assertEqual(leaked, [])

    def test_host_wrapper_exports_codex_runtime_confinement_before_exec(self):
        _server_argv, argv_path, env_path, control_db, policy_dir = \
            self._server_wrapper_fixture("OMNIGENT_DATABASE_URI=unused\n")
        release = self.root / "release"
        project = self.root / "project"
        omnigent = self.root / "omnigent"
        argv = [
            str(REPO / "deploy" / "run-exact-sha-host.sh"),
            "--release-root", str(release),
            "--expected-sha", subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=release, text=True
            ).strip(),
            "--project", str(project),
            "--control-db", str(control_db), "--policy-dir", str(policy_dir),
            "--server", "http://127.0.0.1:6767",
            "--omnigent-bin", str(omnigent),
        ]
        environment = dict(os.environ)
        environment["HARNESS_CODEX_DISABLE_NATIVE_TOOLS"] = "0"
        environment["HARNESS_CODEX_ENABLE_WEB_SEARCH"] = "1"
        environment["NIGHTSHIFT_CONTROL_DB"] = "/stale/control.db"
        environment.update(self._hostile_git_overrides())

        proc = subprocess.run(
            argv, env=environment, text=True, capture_output=True, check=False)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            env_path.read_text().splitlines(),
            [
                str(control_db), str(policy_dir), str(release),
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=release, text=True
                ).strip(),
                "1", "0", "unset", "unset",
            ],
        )
        self.assertEqual(
            argv_path.read_text().splitlines(),
            ["host", "--server", "http://127.0.0.1:6767", "--non-interactive"],
        )
        argv_path.unlink()
        (release / "tracked").write_text("moved for host\n")
        subprocess.run(["git", "add", "tracked"], cwd=release, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "move host head"], cwd=release,
            check=True,
        )
        refused = subprocess.run(
            argv, env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(refused.returncode, 3)
        self.assertIn("does not match authorized SHA", refused.stderr)
        self.assertFalse(argv_path.exists())

    def test_server_wrapper_ignores_hostile_path_and_refuses_head_swap(self):
        argv, argv_path, _env_path, _control_db, _policy_dir = \
            self._server_wrapper_fixture(
                "OMNIGENT_DATABASE_URI=postgresql://fixture\n"
            )
        hostile = self.root / "hostile-bin"
        hostile.mkdir()
        sentinel = self.root / "hostile-git-ran"
        hostile_git = hostile / "git"
        hostile_git.write_text(
            f"#!/bin/sh\n: > {shlex.quote(str(sentinel))}\nexit 99\n"
        )
        hostile_git.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(hostile)
        environment.update(self._hostile_git_overrides())

        first = subprocess.run(
            argv, env=environment, text=True, capture_output=True, check=False)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(sentinel.exists())
        argv_path.unlink()
        release = self.root / "release"
        (release / "tracked").write_text("moved\n")
        subprocess.run(["git", "add", "tracked"], cwd=release, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "move head"], cwd=release, check=True
        )

        second = subprocess.run(
            argv, env=environment, text=True, capture_output=True, check=False)

        self.assertEqual(second.returncode, 3)
        self.assertIn("does not match authorized SHA", second.stderr)
        self.assertFalse(argv_path.exists())
        self.assertFalse(sentinel.exists())

    def test_cutover_runbook_keeps_cleanup_after_acceptance(self):
        runbook = (REPO / "deploy" / "EXACT-SHA-CUTOVER.md").read_text()
        self.assertIn("all eight proof blocks green", runbook)
        self.assertIn("Do not delete `/home/nightshift/nightshift-runtime`", runbook)
        self.assertIn("#129 is explicitly not proven", runbook)


class SealOwnership(unittest.TestCase):
    """Audit C6: `chmod a-w` applied by the runtime uid was the whole seal,
    and that uid can chmod its own files writable again. A release is only
    sealed when root owns it and nothing above it lets anyone else swap it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "base" / "sha" / "checkout"
        (self.tree / "program").mkdir(parents=True)
        (self.tree / "program" / "config.yaml").write_text("x")
        R.seal_tree(self.root / "base" / "sha")

    def _owners(self, *uids):
        return mock.patch.object(R, "_sealed_owner_uids", return_value=set(uids))

    @unittest.skipIf(os.geteuid() == 0, "needs a non-root uid")
    def test_a_write_protected_tree_the_runtime_uid_owns_is_not_sealed(self):
        self.assertTrue(R.tree_is_write_protected(self.tree))
        self.assertFalse(R.tree_is_sealed(self.tree))

    def test_a_writable_directory_above_the_tree_unseals_it(self):
        with self._owners(0, os.geteuid()):
            self.assertTrue(R.tree_is_sealed(self.tree))
            (self.root / "base").chmod(0o777)
            self.assertFalse(R.tree_is_sealed(self.tree), "base allows a rename")
            (self.root / "base").chmod(0o1777)
            self.assertTrue(R.tree_is_sealed(self.tree), "sticky base protects it")

    @unittest.skipUnless(os.geteuid() == 0, "the root seal path needs root")
    def test_root_seal_hands_every_entry_to_root(self):
        tree = self.root / "runtime-owned"
        (tree / "d").mkdir(parents=True)
        (tree / "d" / "f").write_text("x")
        (tree / "link").symlink_to("d/f")
        for path in (tree, tree / "d", tree / "d" / "f", tree / "link"):
            os.lchown(path, 65534, 65534)
            if not path.is_symlink():
                path.chmod(0o777)
        R.seal_tree(tree)
        with self._owners(0):
            self.assertTrue(R.tree_is_sealed(tree))
        for path in (tree, tree / "d", tree / "d" / "f", tree / "link"):
            self.assertEqual(os.lstat(path).st_uid, 0, path)

    def test_an_owner_outside_the_trusted_set_unseals_it(self):
        with self._owners(4242):
            self.assertFalse(R.tree_is_sealed(self.tree))

    @unittest.skipIf(os.geteuid() == 0, "needs a non-root uid")
    def test_seal_command_refuses_to_run_as_the_runtime_user(self):
        with self.assertRaisesRegex(R.ReleaseError, "must run as root"):
            R.seal_command(mock.Mock(plan=str(self.root / "plan.json"),
                                     release_base=str(self.root / "base")))

    def test_sealed_checkout_owned_by_root_gets_a_safe_directory_for_git(self):
        with mock.patch.object(R.os, "lstat",
                               return_value=mock.Mock(st_uid=0)), \
             mock.patch.object(R.os, "geteuid", return_value=1000):
            self.assertEqual(R._root_sealed_trust(self.tree),
                             ["-c", f"safe.directory={self.tree}"])
        if os.geteuid():
            self.assertEqual(R._root_sealed_trust(self.tree), [],
                             "a tree this uid owns needs no exception")


class _FakeSystemd:
    """A minimal systemd-user simulation over real filesystem state.

    `_legacy_unit_records`, `_mask_legacy_units`, and `_restore_legacy_units`
    all read/write real paths (symlinks, regular "fragment" files) under
    `unit_dir`. Backing `_systemctl_user` with a fake that reports LoadState
    from that same real state lets the ADR-0064 acceptance proof exercise the
    production masking/restoring code paths, not a re-implementation of them.
    """

    def __init__(self, unit_dir: Path):
        self.unit_dir = unit_dir
        self.active: set[str] = set()

    def __call__(self, *args, **_kwargs):
        if args[0] == "list-unit-files":
            names = sorted(p.name for p in self.unit_dir.iterdir())
            return "\n".join(f"{name} disabled" for name in names)
        if args[0] == "daemon-reload":
            return ""
        if args[0] == "show":
            name = args[1]
            path = self.unit_dir / name
            active = "active" if name in self.active else "inactive"
            if path.is_symlink() and os.readlink(path) == "/dev/null":
                return (f"LoadState=masked\nActiveState={active}\nSubState=dead\n"
                        "UnitFileState=masked\nFragmentPath=")
            if path.is_file():
                return (f"LoadState=loaded\nActiveState={active}\nSubState=dead\n"
                        f"UnitFileState=disabled\nFragmentPath={path}")
            return (f"LoadState=not-found\nActiveState={active}\nSubState=dead\n"
                    "UnitFileState=not-found\nFragmentPath=")
        if args[0] == "is-enabled":
            name = args[1]
            path = self.unit_dir / name
            if path.is_symlink() and os.readlink(path) == "/dev/null":
                return "masked"
            return "disabled"
        raise AssertionError(("unexpected systemctl call", args))


class SubsequentSupervisionUnitTests(unittest.TestCase):
    """ADR-0064 Decision §2/§3: split inherited masks from newly discovered units."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.unit_dir = self.root / "home" / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True)
        self.fake = _FakeSystemd(self.unit_dir)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(R.Path, "home", return_value=self.root / "home").start()
        mock.patch.object(R, "_systemctl_user", side_effect=self.fake).start()
        self.required = sorted(R.REQUIRED_LEGACY_UNITS)
        for name in self.required:
            (self.unit_dir / name).write_text(f"[Unit]\nDescription={name}\n")

    def _mask(self, names):
        for name in names:
            path = self.unit_dir / name
            if path.is_symlink() or path.exists():
                path.unlink()
            path.symlink_to("/dev/null")

    def test_bootstrap_inventory_is_unaffected_by_the_refactor(self):
        inventory = R._legacy_unit_inventory()
        self.assertEqual({row["name"] for row in inventory}, set(self.required))
        self.assertTrue(all(row["load_state"] == "loaded" for row in inventory))

    def test_inherited_masked_units_do_not_need_a_readable_fragment(self):
        # Reproduces the issue #490 boundary: unmasking any of these would
        # show LoadState=not-found because no lower-priority fragment exists
        # (the fake's masked units report FragmentPath empty, exactly like
        # real `systemctl show` on a masked unit) -- but subsequent mode must
        # accept them anyway, since they stay masked and untouched.
        self._mask(self.required)
        with self.assertRaisesRegex(R.ReleaseError, "unreadable"):
            R._legacy_unit_inventory()  # bootstrap mode: still strictly refuses
        inherited_masked, to_mask = R._subsequent_unit_transition(set(self.required))
        self.assertEqual(to_mask, [])
        self.assertEqual({row["name"] for row in inherited_masked}, set(self.required))
        self.assertTrue(all(row["load_state"] == "masked" for row in inherited_masked))

    def test_newly_discovered_stale_unit_still_requires_a_readable_fragment(self):
        # A required unit outside the inherited lineage (e.g. never masked by
        # the predecessor) must still be loaded/readable, exactly like
        # bootstrap mode -- subsequent mode only softens the inherited names.
        inherited = self.required[:3]
        self._mask(inherited)
        missing = self.required[3]
        (self.unit_dir / missing).unlink()
        with self.assertRaisesRegex(R.ReleaseError, f"unreadable.*{re.escape(missing)}"):
            R._subsequent_unit_transition(set(inherited))

    def test_newly_discovered_stale_unit_becomes_a_to_mask_row(self):
        self._mask(self.required)
        (self.unit_dir / "extra-stale.service").write_text(
            "[Service]\nExecStart=/srv/nightshift/deploy/helper.sh\n"
        )
        inherited_masked, to_mask = R._subsequent_unit_transition(set(self.required))
        self.assertEqual({row["name"] for row in inherited_masked}, set(self.required))
        self.assertEqual([row["name"] for row in to_mask], ["extra-stale.service"])
        self.assertEqual(to_mask[0]["load_state"], "loaded")

    def test_replaced_inherited_mask_is_refused(self):
        self._mask(self.required)
        replaced = self.required[0]
        (self.unit_dir / replaced).unlink()
        (self.unit_dir / replaced).write_text("[Unit]\nDescription=resurrected\n")
        with self.assertRaisesRegex(R.ReleaseError, "mask is not intact"):
            R._subsequent_unit_transition(set(self.required))

    def test_active_inherited_unit_is_refused(self):
        self._mask(self.required)
        self.fake.active.add(self.required[0])
        with self.assertRaisesRegex(R.ReleaseError, "mask is not intact"):
            R._subsequent_unit_transition(set(self.required))

    def test_predecessor_masked_names_reads_both_plan_schemas(self):
        legacy_style = {"legacy_units": [{"name": "a.service"}, {"name": "b.service"}]}
        self.assertEqual(R._predecessor_masked_names(legacy_style), {"a.service", "b.service"})
        supervision_style = {
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA,
                "inherited_masked": [{"name": "a.service"}],
                "to_mask": [{"name": "c.service"}],
            }
        }
        self.assertEqual(
            R._predecessor_masked_names(supervision_style), {"a.service", "c.service"}
        )

    def test_current_legacy_transition_refuses_unsupported_schema(self):
        with self.assertRaisesRegex(R.ReleaseError, "supervision transition schema"):
            R._current_legacy_transition({"supervision": {"schema": "some-other-v9"}})
        with self.assertRaisesRegex(R.ReleaseError, "unsupported supervision mode"):
            R._current_legacy_transition(
                {"supervision": {"schema": R.SUPERVISION_SCHEMA, "mode": "eventual"}}
            )


class PredecessorVerificationTests(unittest.TestCase):
    """ADR-0064 Decision §2, items 1-3 and 5 -- `_verify_predecessor`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "state" / ("a" * 40)
        self.state_dir.mkdir(parents=True)
        self.release_root = self.root / "release-a"
        (self.release_root / "deploy" / "policies").mkdir(parents=True)
        self.control_identity = {"device": 7, "inode": 42}
        self.registration = {"id": "program-1", "version": 5}
        self.plan = {
            "sha": "a" * 40,
            "state_dir": str(self.state_dir),
            "release_root": str(self.release_root),
            "omnigent_bin": str(self.release_root / "omnigent"),
            "control_db": dict(self.control_identity),
            "program_git_manifest": {"digest": "digest-a"},
            "project": str(self.root / "project-a"),
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "bootstrap", "predecessor": None,
                "inherited_masked": [], "to_mask": [{"name": "omnigent-server.service"}],
            },
        }
        self.plan["plan_digest"] = R._sha256(R._canonical(self.plan))
        (self.state_dir / "plan.json").write_text(json.dumps(self.plan))
        self.receipt = {
            "schema": "nightshift-exact-sha-receipt-v1",
            "plan_digest": self.plan["plan_digest"],
            "accepted": True,
            "proofs": {
                name: {"verdict": "green", "operands": {}, "reason": ""}
                for name in R.PROOFS
            },
        }
        self.receipt["proofs"]["registered"]["operands"] = {
            "actual_id": self.registration["id"],
            "actual_version": self.registration["version"],
        }
        self.receipt["proofs"]["content_at_sha"]["operands"] = {
            "sha": self.plan["sha"],
            "git_object_digest": self.plan["program_git_manifest"]["digest"],
        }
        (self.state_dir / "receipt.json").write_text(json.dumps(self.receipt))
        self.server_env = {"PYTHONPATH": str(self.release_root / "deploy" / "policies")}
        self.server_cmdline = f"{self.plan['omnigent_bin']} --serve"
        self.server_url = "http://127.0.0.1:6767"
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(
            R, "_new_session", return_value={"id": "predecessor-check-session"}
        ).start()
        mock.patch.object(
            R, "_download_and_hash_served_bundle",
            return_value=(b"bundle-bytes", self.plan["program_git_manifest"]["digest"]),
        ).start()

    def _verify(self, **overrides):
        kwargs = dict(
            control_identity=self.control_identity,
            server_env=self.server_env,
            server_cmdline=self.server_cmdline,
            registration=self.registration,
            server_url=self.server_url,
        )
        kwargs.update(overrides)
        return R._verify_predecessor(self.state_dir / "plan.json", **kwargs)

    def test_happy_path_returns_predecessor_evidence(self):
        result = self._verify()
        self.assertEqual(result["plan"]["plan_digest"], self.plan["plan_digest"])
        self.assertEqual(result["state_dir"], self.state_dir)

    def test_edited_plan_digest_is_refused(self):
        body = json.loads((self.state_dir / "plan.json").read_text())
        body["sha"] = "b" * 40
        (self.state_dir / "plan.json").write_text(json.dumps(body))
        with self.assertRaisesRegex(R.ReleaseError, "digest mismatch"):
            self._verify()

    def test_receipt_not_naming_the_plan_digest_is_refused(self):
        receipt = dict(self.receipt, plan_digest="f" * 64)
        (self.state_dir / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaisesRegex(R.ReleaseError, "does not name the predecessor plan digest"):
            self._verify()

    def test_forged_accepted_true_with_a_red_proof_is_refused(self):
        receipt = json.loads(json.dumps(self.receipt))
        receipt["proofs"]["executed"]["verdict"] = "red"
        (self.state_dir / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaisesRegex(R.ReleaseError, "proof that is not green"):
            self._verify()

    def test_missing_proof_is_refused(self):
        receipt = json.loads(json.dumps(self.receipt))
        del receipt["proofs"]["scheduler_continuity"]
        (self.state_dir / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaisesRegex(R.ReleaseError, "proof that is not green"):
            self._verify()

    def test_receipt_not_accepted_is_refused(self):
        receipt = dict(self.receipt, accepted=False)
        (self.state_dir / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaisesRegex(R.ReleaseError, "not accepted"):
            self._verify()

    def test_current_runtime_not_matching_predecessor_is_refused(self):
        with self.assertRaisesRegex(R.ReleaseError, "control DB identity"):
            self._verify(control_identity={"device": 99, "inode": 1})
        with self.assertRaisesRegex(R.ReleaseError, "currently registered Program"):
            self._verify(registration={"id": "program-1", "version": 999})
        with self.assertRaisesRegex(R.ReleaseError, "live server PYTHONPATH"):
            self._verify(server_env={"PYTHONPATH": "/somewhere/else"})
        with self.assertRaisesRegex(R.ReleaseError, "accepted Omnigent binary"):
            self._verify(server_cmdline="/usr/bin/something-else")

    def test_live_served_content_not_matching_predecessor_digest_is_refused(self):
        # MERGE_BLOCKER 2: the receipt-vs-plan comparison above is only
        # self-consistency on stored history. This is the independent, live
        # re-derivation -- extract-and-hash whatever is *currently* served --
        # matching what postflight's content_at_sha proof already does.
        with mock.patch.object(
            R, "_download_and_hash_served_bundle",
            return_value=(b"drifted-bytes", "some-other-digest"),
        ):
            with self.assertRaisesRegex(
                R.ReleaseError,
                "currently served content does not match the predecessor's accepted Program digest",
            ):
                self._verify()

    def test_forged_state_dir_field_is_refused(self):
        # MERGE_BLOCKER 1: a plan's own `state_dir` field is externally
        # supplied and forgeable. state_dir must be derived from where the
        # plan file actually lives (plan_path.parent), never trusted from
        # the plan body -- otherwise a plan at the trusted path with
        # `state_dir` redirected elsewhere could point at an
        # attacker-writable receipt.json.
        forged = self.root / "attacker-writable"
        forged.mkdir()
        (forged / "receipt.json").write_text(json.dumps(self.receipt))
        body = json.loads((self.state_dir / "plan.json").read_text())
        body["state_dir"] = str(forged)
        del body["plan_digest"]
        body["plan_digest"] = R._sha256(R._canonical(body))
        (self.state_dir / "plan.json").write_text(json.dumps(body))
        with self.assertRaisesRegex(
            R.ReleaseError,
            "state_dir field disagrees with its own file location",
        ):
            self._verify()

    def test_nonexistent_predecessor_plan_path_is_a_release_error(self):
        # FOLLOW_UP: `main()` only catches ReleaseError; an unreadable
        # --predecessor-plan path must not surface a raw FileNotFoundError.
        with self.assertRaises(R.ReleaseError):
            self._verify_at(self.root / "does-not-exist" / "plan.json")

    def _verify_at(self, path, **overrides):
        kwargs = dict(
            control_identity=self.control_identity,
            server_env=self.server_env,
            server_cmdline=self.server_cmdline,
            registration=self.registration,
            server_url=self.server_url,
        )
        kwargs.update(overrides)
        return R._verify_predecessor(path, **kwargs)

    def test_receipt_file_missing_is_refused(self):
        (self.state_dir / "receipt.json").unlink()
        with self.assertRaisesRegex(R.ReleaseError, "predecessor receipt is unreadable"):
            self._verify()

    def test_receipt_invalid_json_is_refused(self):
        (self.state_dir / "receipt.json").write_text("{not json")
        with self.assertRaisesRegex(R.ReleaseError, "predecessor receipt is unreadable"):
            self._verify()

    def test_receipt_missing_accepted_key_is_refused(self):
        receipt = json.loads(json.dumps(self.receipt))
        del receipt["accepted"]
        (self.state_dir / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaisesRegex(R.ReleaseError, "not accepted"):
            self._verify()

    def test_rollback_receipt_present_refuses_even_on_a_coincidental_match(self):
        # ADR-0065's OQ-2, folded into ADR-0064 item 5: a partially-failed
        # rollback can leave on-disk state and backup hash matching a stale
        # snapshot exactly. The tool's own rollback-receipt record must still
        # win over that coincidental match.
        (self.state_dir / "rollback-receipt.json").write_text(json.dumps({
            "verified_at": 1, "green": True,
            "expected_digest": "digest-a", "served_digest": "digest-a",
        }))
        with self.assertRaisesRegex(R.ReleaseError, "rollback-receipt.json"):
            self._verify()


class PredecessorEvidenceReverificationTests(unittest.TestCase):
    """MERGE_BLOCKER 3 (PR #494 round 1): `prepare` verifies its named
    predecessor once, but a subsequent-mode candidate must not be able to
    reach `authorize` or `cutover` if that predecessor's evidence (its
    receipt, or a rollback-receipt appearing for it) changes afterward.
    `authorize()` and `_fresh_cutover_gate()` must both re-run the full
    predecessor check via `_reverify_predecessor_evidence`.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(mock.patch.stopall)
        _test_uid_stands_in_for_root(self)

        # A real predecessor (generation A), same shape as
        # PredecessorVerificationTests.
        self.state_dir_a = self.root / "state" / ("a" * 40)
        self.state_dir_a.mkdir(parents=True)
        self.release_root_a = self.root / "release-a"
        (self.release_root_a / "deploy" / "policies").mkdir(parents=True)
        self.control_identity = {"device": 7, "inode": 42}
        self.registration = {"id": "program-1", "name": "program", "version": 5,
                             "created_at": 0, "updated_at": 0}
        self.plan_a = {
            "sha": "a" * 40,
            "state_dir": str(self.state_dir_a),
            "release_root": str(self.release_root_a),
            "omnigent_bin": str(self.release_root_a / "omnigent"),
            "control_db": dict(self.control_identity),
            "program_git_manifest": {"digest": "digest-a"},
            "project": str(self.root / "project-a"),
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "bootstrap", "predecessor": None,
                "inherited_masked": [], "to_mask": [],
            },
        }
        self.plan_a["plan_digest"] = R._sha256(R._canonical(self.plan_a))
        (self.state_dir_a / "plan.json").write_text(json.dumps(self.plan_a))
        self.receipt_a = {
            "schema": "nightshift-exact-sha-receipt-v1",
            "plan_digest": self.plan_a["plan_digest"],
            "accepted": True,
            "proofs": {name: {"verdict": "green", "operands": {}, "reason": ""}
                      for name in R.PROOFS},
        }
        self.receipt_a["proofs"]["registered"]["operands"] = {
            "actual_id": self.registration["id"],
            "actual_version": self.registration["version"],
        }
        self.receipt_a["proofs"]["content_at_sha"]["operands"] = {
            "sha": self.plan_a["sha"],
            "git_object_digest": self.plan_a["program_git_manifest"]["digest"],
        }
        self._write_receipt_a(self.receipt_a)

        # The real cmdline of *this* test process -- used so authorize()/
        # _fresh_cutover_gate()'s inline `/proc/<pid>/cmdline` read (there is
        # no mockable seam for it) sees real bytes; the predecessor's
        # `omnigent_bin` is pinned to a substring of it so `_verify_predecessor`
        # accepts it as "the accepted Omnigent binary".
        self.real_pid = os.getpid()
        real_cmdline = Path(f"/proc/{self.real_pid}/cmdline").read_bytes(
        ).replace(b"\0", b" ").decode()
        self.plan_a["omnigent_bin"] = real_cmdline.split()[0]
        self.plan_a["plan_digest"] = R._sha256(R._canonical(
            {k: v for k, v in self.plan_a.items() if k != "plan_digest"}
        ))
        (self.state_dir_a / "plan.json").write_text(json.dumps(self.plan_a))
        self.receipt_a["plan_digest"] = self.plan_a["plan_digest"]
        self._write_receipt_a(self.receipt_a)

        self.expected_policy_dir = str(self.release_root_a / "deploy" / "policies")
        mock.patch.object(
            R, "_new_session", return_value={"id": "predecessor-check-session"}
        ).start()
        mock.patch.object(
            R, "_download_and_hash_served_bundle",
            return_value=(b"bundle-bytes", self.plan_a["program_git_manifest"]["digest"]),
        ).start()
        mock.patch.object(
            R, "_selected_process_env",
            return_value={"PYTHONPATH": self.expected_policy_dir},
        ).start()

        # Candidate B, subsequent mode, bound to A's verified evidence exactly
        # as `prepare()` would bind it.
        self.control_db_b = self.root / "control.db"
        self.release_root_b = self.root / "release-b"
        (self.release_root_b / "program").mkdir(parents=True)
        (self.release_root_b / "deploy" / "policies").mkdir(parents=True)
        self.state_dir_b = self.root / "state" / ("b" * 40)
        self.state_dir_b.mkdir(parents=True)
        self.evidence_digest = R._predecessor_evidence_digest(
            {"receipt": self.receipt_a}
        )
        self.plan_b = {
            "sha": "b" * 40,
            "floor_sha": "0" * 40,
            "state_dir": str(self.state_dir_b),
            "release_root": str(self.release_root_b),
            "control_db": {**self.control_identity, "path": str(self.control_db_b)},
            "server_url": "http://127.0.0.1:6767",
            "project": str(self.root / "project-b"),
            "program_git_manifest": {"digest": "digest-b"},
            "policy_git_manifest": {"digest": "policy-digest-b"},
            "scheduled_task_id": "task-1",
            "scheduled_task": {
                "original_prompt_sha256": R._sha256(b"original prompt"),
            },
            "pre_registration": dict(self.registration),
            "pre_registration_row_count": 1,
            "rollback": {"manifest": {"digest": "rollback-digest-b"}},
            "durable_session": {"host_id": "host-1"},
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "subsequent",
                "predecessor": {
                    "plan_path": str(self.state_dir_a / "plan.json"),
                    "state_dir": str(self.state_dir_a),
                    "plan_digest": self.plan_a["plan_digest"],
                    "sha": self.plan_a["sha"],
                    "evidence_digest": self.evidence_digest,
                },
                "inherited_masked": [], "to_mask": [],
            },
        }
        self.plan_b["plan_digest"] = R._sha256(R._canonical(self.plan_b))
        self.plan_b_path = self.state_dir_b / "plan.json"
        self.plan_b_path.write_text(json.dumps(self.plan_b))

    def _write_receipt_a(self, receipt):
        (self.state_dir_a / "receipt.json").write_text(json.dumps(receipt))

    def _corrupt_predecessor_receipt(self):
        """Simulate the predecessor's evidence changing after B was prepared,
        in a way `_verify_predecessor`'s own individual field checks (green
        verdicts, registered/content_at_sha operands, ...) do NOT inspect --
        isolating the *evidence-digest* binding (item 3/5) as the thing that
        must catch it, exactly as MERGE_BLOCKER 3 requires."""
        receipt = json.loads(json.dumps(self.receipt_a))
        receipt["proofs"]["executed"]["reason"] = "quietly rewritten after prepare"
        self._write_receipt_a(receipt)

    def test_authorize_refuses_when_predecessor_receipt_changed_since_prepare(self):
        current_registration_row = {
            "id": self.plan_b["pre_registration"]["id"], "name": "program",
            "version": self.plan_b["pre_registration"]["version"],
            "created_at": 0, "updated_at": 0,
        }

        def list_data(_server, endpoint):
            if endpoint == "agents":
                return [current_registration_row]
            if endpoint == "sessions":
                return []
            raise AssertionError(endpoint)

        def json_request(_method, url):
            if "scheduled-tasks" in url:
                return {"state": "paused", "next_run_at": None,
                        "prompt": "original prompt"}
            raise AssertionError(url)

        def filesystem_manifest(path):
            if path.name == "program":
                return {"digest": self.plan_b["program_git_manifest"]["digest"]}
            return {"digest": self.plan_b["policy_git_manifest"]["digest"]}

        real_load_plan = R._load_plan

        def load_plan(path):
            # Only stub authorize()'s own top-level load of plan B -- the
            # nested `_verify_predecessor` call inside
            # `_reverify_predecessor_evidence` must still load the *real*
            # predecessor plan A from disk (that's the whole point of this
            # test), or a blanket patch here would silently make it "verify"
            # plan B against itself.
            if Path(path) == self.plan_b_path:
                return dict(self.plan_b)
            return real_load_plan(path)

        patches = (
            mock.patch.object(R, "_load_plan", side_effect=load_plan),
            mock.patch.object(R, "_git", side_effect=lambda _root, *a: (
                self.plan_b["sha"] if a[:1] == ("rev-parse",) else ""
            )),
            mock.patch.object(R, "tree_is_sealed", return_value=True),
            mock.patch.object(R, "_current_legacy_transition", return_value=([], [])),
            mock.patch.object(R, "filesystem_manifest", side_effect=filesystem_manifest),
            mock.patch.object(R, "_control_identity",
                              return_value=dict(self.plan_b["control_db"])),
            mock.patch.object(R, "_control_snapshot", return_value={
                "admission_schema_ready": True, "active_legacy_slots": [],
                "unfinished_without_admission": [], "active": {},
            }),
            mock.patch.object(R, "_list_data", side_effect=list_data),
            mock.patch.object(R, "_json_request", side_effect=json_request),
            mock.patch.object(R, "_serving_pid", return_value=self.real_pid),
            mock.patch.object(R, "_pid_epoch", return_value="epoch"),
            mock.patch.object(R, "_host_status", return_value={
                "process": "online", "host_status": "online",
                "host_id": self.plan_b["durable_session"]["host_id"], "pid": 99999,
            }),
            mock.patch.object(R, "_runner_pids", return_value=[99998]),
        )
        args = mock.Mock(plan=str(self.plan_b_path))

        # Sanity: unmodified predecessor evidence authorizes cleanly.
        with (patches[0], patches[1], patches[2], patches[3], patches[4],
              patches[5], patches[6], patches[7], patches[8], patches[9],
              patches[10], patches[11], patches[12]):
            authorization = R.authorize(args)
        self.assertEqual(authorization["plan_digest"], self.plan_b["plan_digest"])

        # The predecessor's receipt is rewritten (e.g. a later, unrelated
        # process partially/maliciously mutates it) between prepare and
        # authorize -- authorize must re-detect this and refuse.
        self._corrupt_predecessor_receipt()
        with (patches[0], patches[1], patches[2], patches[3], patches[4],
              patches[5], patches[6], patches[7], patches[8], patches[9],
              patches[10], patches[11], patches[12]):
            with self.assertRaisesRegex(
                R.ReleaseError, "predecessor evidence changed since preparation"
            ):
                R.authorize(args)

    def test_cutover_gate_refuses_when_predecessor_receipt_changed_since_authorize(self):
        authorization = {
            "plan_digest": self.plan_b["plan_digest"],
            "authorized_at": int(time.time()),
            "old_pid": self.real_pid, "old_pid_epoch": "epoch",
            "old_host_pid": 99999, "old_host_pid_epoch": "epoch",
            "old_host_id": self.plan_b["durable_session"]["host_id"],
            "old_runner_pids": [99998],
            "legacy_units": [],
            "pre_registration": {"id": self.plan_b["pre_registration"]["id"],
                                 "version": self.plan_b["pre_registration"]["version"]},
        }
        current_registration_row = {
            "id": self.plan_b["pre_registration"]["id"], "name": "program",
            "version": self.plan_b["pre_registration"]["version"],
            "created_at": 0, "updated_at": 0,
        }

        def list_data(_server, endpoint):
            if endpoint == "agents":
                return [current_registration_row]
            if endpoint == "sessions":
                return []
            raise AssertionError(endpoint)

        def json_request(_method, url):
            if "scheduled-tasks" in url:
                return {"state": "paused", "next_run_at": None,
                        "prompt": "original prompt"}
            raise AssertionError(url)

        patches = (
            mock.patch.object(R, "_serving_pid", return_value=self.real_pid),
            mock.patch.object(R, "_pid_epoch", return_value="epoch"),
            mock.patch.object(R, "_host_status", return_value={
                "process": "online", "host_status": "online",
                "host_id": self.plan_b["durable_session"]["host_id"], "pid": 99999,
            }),
            mock.patch.object(R, "_runner_pids", return_value=[99998]),
            mock.patch.object(R, "_current_legacy_transition", return_value=([], [])),
            mock.patch.object(R, "_control_identity",
                              return_value=dict(self.plan_b["control_db"])),
            mock.patch.object(R, "_control_snapshot", return_value={
                "admission_schema_ready": True, "active_legacy_slots": [],
                "unfinished_without_admission": [], "active": {},
            }),
            mock.patch.object(R, "_list_data", side_effect=list_data),
            mock.patch.object(R, "_json_request", side_effect=json_request),
            mock.patch.object(R, "_reverify_release_candidate"),
            mock.patch.object(R, "_reverify_omnigent_runtime"),
        )

        # Sanity: unmodified predecessor evidence passes the gate cleanly.
        with (patches[0], patches[1], patches[2], patches[3], patches[4],
              patches[5], patches[6], patches[7], patches[8], patches[9],
              patches[10]):
            R._fresh_cutover_gate(dict(self.plan_b), authorization)

        # The predecessor's receipt changes between authorize and cutover
        # (e.g. a rollback-receipt appears, or its proofs turn red) --
        # _fresh_cutover_gate must re-detect this and refuse mutation.
        self._corrupt_predecessor_receipt()
        with (patches[0], patches[1], patches[2], patches[3], patches[4],
              patches[5], patches[6], patches[7], patches[8], patches[9],
              patches[10]):
            with self.assertRaisesRegex(
                R.ReleaseError, "predecessor evidence changed since preparation"
            ):
                R._fresh_cutover_gate(dict(self.plan_b), authorization)


class TwoGenerationCutoverAcceptance(unittest.TestCase):
    """ADR-0064 acceptance proof: a disposable, fully simulated two-generation
    release sequence (no live host action, no real systemd mutation)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.unit_dir = self.root / "home" / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True)
        self.fake = _FakeSystemd(self.unit_dir)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(R.Path, "home", return_value=self.root / "home").start()
        mock.patch.object(R, "_systemctl_user", side_effect=self.fake).start()
        self.required = sorted(R.REQUIRED_LEGACY_UNITS)
        for name in self.required:
            (self.unit_dir / name).write_text(f"[Unit]\nDescription={name}\n")

    def _green_receipt(self, plan_digest, *, registration, sha, content_digest):
        receipt = {
            "schema": "nightshift-exact-sha-receipt-v1",
            "plan_digest": plan_digest,
            "accepted": True,
            "proofs": {name: {"verdict": "green", "operands": {}, "reason": ""}
                      for name in R.PROOFS},
        }
        receipt["proofs"]["registered"]["operands"] = {
            "actual_id": registration["id"], "actual_version": registration["version"],
        }
        receipt["proofs"]["content_at_sha"]["operands"] = {
            "sha": sha, "git_object_digest": content_digest,
        }
        return receipt

    def test_second_cutover_succeeds_without_a_readable_legacy_fragment(self):
        state_base = self.root / "state"
        release_root = self.root / "release-a"
        (release_root / "deploy" / "policies").mkdir(parents=True)
        registration = {"id": "program-1", "version": 5}
        control_identity = {"device": 7, "inode": 42}

        # --- Step 1: bootstrap generation A: prepare -> mask (cutover). ---
        inventory = R._legacy_unit_inventory()
        self.assertEqual({row["name"] for row in inventory}, set(self.required))
        state_dir_a = state_base / ("a" * 40)
        mask_result = R._mask_legacy_units(state_dir_a, inventory)
        self.assertEqual({u["name"] for u in mask_result["units"]}, set(self.required))
        for name in self.required:
            self.assertEqual(os.readlink(self.unit_dir / name), "/dev/null")

        plan_a = {
            "sha": "a" * 40,
            "state_dir": str(state_dir_a),
            "release_root": str(release_root),
            "omnigent_bin": str(release_root / "omnigent"),
            "control_db": dict(control_identity),
            "program_git_manifest": {"digest": "digest-a"},
            "project": str(self.root / "project"),
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "bootstrap", "predecessor": None,
                "inherited_masked": [], "to_mask": inventory,
            },
        }
        plan_a["plan_digest"] = R._sha256(R._canonical(plan_a))
        (state_dir_a / "plan.json").write_text(json.dumps(plan_a))
        receipt_a = self._green_receipt(
            plan_a["plan_digest"], registration=registration, sha=plan_a["sha"],
            content_digest="digest-a",
        )
        (state_dir_a / "receipt.json").write_text(json.dumps(receipt_a))

        # --- Step 2: the masked units' local override is `/dev/null`, so
        # unmasking any of them would show LoadState=not-found -- no
        # lower-priority fragment exists, exactly the issue #490
        # reproduction. A second bootstrap-style prepare on this host can now
        # never succeed again: this is the pre-change RED case, and stays RED
        # after this fix too, since bootstrap mode is deliberately left
        # strict (ADR-0064 Decision §1). Only subsequent mode (step 3) is new. ---
        with self.assertRaisesRegex(R.ReleaseError, "unreadable"):
            R._legacy_unit_inventory()

        # --- Step 3: prepare & cutover candidate B using A's verified
        # predecessor evidence. Must accept without recreating or unmasking
        # any legacy fragment. ---
        with (
            mock.patch.object(
                R, "_new_session", return_value={"id": "predecessor-check-session"}
            ),
            mock.patch.object(
                R, "_download_and_hash_served_bundle",
                return_value=(b"bundle-bytes", "digest-a"),
            ),
        ):
            predecessor = R._verify_predecessor(
                state_dir_a / "plan.json",
                control_identity=control_identity,
                server_env={"PYTHONPATH": str(release_root / "deploy" / "policies")},
                server_cmdline=f"{plan_a['omnigent_bin']} --serve",
                registration=registration,
                server_url="http://127.0.0.1:6767",
            )
        inherited_names = R._predecessor_masked_names(predecessor["plan"])
        inherited_masked, to_mask = R._subsequent_unit_transition(inherited_names)
        self.assertEqual(to_mask, [])
        self.assertEqual({u["name"] for u in inherited_masked}, set(self.required))

        state_dir_b = state_base / ("b" * 40)
        plan_b = {
            "sha": "b" * 40,
            "state_dir": str(state_dir_b),
            "release_root": str(release_root),
            "omnigent_bin": str(release_root / "omnigent"),
            "control_db": dict(control_identity),
            "program_git_manifest": {"digest": "digest-b"},
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "subsequent",
                "predecessor": {"plan_path": str(state_dir_a / "plan.json"),
                                "plan_digest": plan_a["plan_digest"], "sha": plan_a["sha"]},
                "inherited_masked": inherited_masked, "to_mask": to_mask,
            },
        }
        plan_b["plan_digest"] = R._sha256(R._canonical(plan_b))
        (state_dir_b).mkdir()
        (state_dir_b / "plan.json").write_text(json.dumps(plan_b))

        # cutover B: mask only the (empty) to_mask delta -- a no-op that must
        # not touch, unmask, or recreate any inherited unit.
        b_mask_result = R._mask_legacy_units(state_dir_b, plan_b["supervision"]["to_mask"])
        self.assertEqual(b_mask_result["units"], [])
        for name in self.required:
            self.assertEqual(os.readlink(self.unit_dir / name), "/dev/null")

        current_inherited, current_to_mask = R._current_legacy_transition(plan_b)
        self.assertEqual(current_inherited, inherited_masked)
        self.assertEqual(current_to_mask, [])
        receipt_b = self._green_receipt(
            plan_b["plan_digest"], registration=registration, sha=plan_b["sha"],
            content_digest="digest-b",
        )
        (state_dir_b / "receipt.json").write_text(json.dumps(receipt_b))
        self.assertTrue(receipt_b["accepted"])

    def test_rollback_restores_only_the_to_mask_delta_not_inherited_masks(self):
        # A already masked and accepted (as above), but generation B also
        # discovers one newly loaded stale unit this run.
        for name in self.required:
            path = self.unit_dir / name
            path.unlink()
            path.symlink_to("/dev/null")
        extra = "extra-stale.service"
        extra_fragment = "[Service]\nExecStart=/srv/nightshift/deploy/helper.sh\n"
        (self.unit_dir / extra).write_text(extra_fragment)

        inherited_masked, to_mask = R._subsequent_unit_transition(set(self.required))
        self.assertEqual([row["name"] for row in to_mask], [extra])

        state_dir_b = self.root / "state" / ("b" * 40)
        state_dir_b.mkdir(parents=True)
        mask_result = R._mask_legacy_units(state_dir_b, to_mask)
        self.assertEqual([u["name"] for u in mask_result["units"]], [extra])
        self.assertEqual(os.readlink(self.unit_dir / extra), "/dev/null")
        for name in self.required:
            self.assertEqual(os.readlink(self.unit_dir / name), "/dev/null")

        # Simulate a failed cutover: restore only the to_mask delta.
        plan_b = {
            "state_dir": str(state_dir_b),
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "subsequent",
                "predecessor": {}, "inherited_masked": inherited_masked, "to_mask": to_mask,
            },
        }
        R._restore_legacy_units_if_masked(plan_b)

        self.assertEqual(
            (self.unit_dir / extra).read_text(), extra_fragment,
            "to_mask delta must be restored to its exact pre-run state",
        )
        for name in self.required:
            self.assertEqual(
                os.readlink(self.unit_dir / name), "/dev/null",
                "inherited masks must never be touched by a to_mask-only rollback",
            )


class _FakeWorld:
    """The live host/server/scheduler state backing every mocked I/O seam in
    `EndToEndCutoverAcceptance`. Only the true OS/process/HTTP boundary is
    faked (tmux, tmux-adjacent PID/epoch bookkeeping, the Omnigent HTTP API,
    process introspection); every orchestration function in
    `exact_sha_release.py` itself (`prepare`, `authorize`, `cutover`,
    `_fresh_cutover_gate`, `_stop_runtime_pair`, `_start_runtime_pair`,
    `_rollback_live`, `_restore_legacy_units_if_masked`,
    `_restore_scheduled_task`, `_verify_rollback`, `_verify_predecessor`,
    `_reverify_predecessor_evidence`) runs unmodified.
    """

    def __init__(self, *, real_pid: int, host_id: str):
        self.real_pid = real_pid
        self.host_id = host_id
        self.generation = "A"  # "A" -> "B" (candidate up) -> "rolled_back"
        self.server_online = True
        self.host_online = True
        self._boot = 0
        self.host_pid = 5001
        self.runner_pid = 5002
        self.epoch = {
            self.real_pid: "epoch-A-server-0",
            self.host_pid: "epoch-A-host-0",
            self.runner_pid: "epoch-A-runner-0",
        }

    def _bump(self, pid: int, label: str) -> str:
        self._boot += 1
        epoch = f"epoch-{label}-{self._boot}"
        self.epoch[pid] = epoch
        return epoch

    # -- tmux layer --------------------------------------------------
    def start_tmux(self, argv: list[str], *, plan: dict) -> None:
        if argv == plan["launch_argv"]:
            self._bump(self.real_pid, "B-server")
            self.server_online = True
        elif argv == plan["rollback_argv"]:
            self._bump(self.real_pid, "rollback-server")
            self.server_online = True
            self.generation = "rolled_back"
        elif argv == plan["host_launch_argv"]:
            self.host_pid, self.runner_pid = 6001, 6002
            self._bump(self.host_pid, "B-host")
            self._bump(self.runner_pid, "B-runner")
            self.host_online = True
            self.generation = "B"
        elif argv == plan["rollback_host_argv"]:
            self.host_pid, self.runner_pid = 8001, 8002
            self._bump(self.host_pid, "rollback-host")
            self._bump(self.runner_pid, "rollback-runner")
            self.host_online = True
            self.generation = "rolled_back"
        else:
            raise AssertionError(("unexpected tmux argv", argv))

    # -- process/epoch layer -----------------------------------------
    def serving_pid(self) -> int:
        if not self.server_online:
            raise R.ReleaseError("no process is listening")
        return self.real_pid

    def pid_epoch(self, pid: int) -> str:
        epoch = self.epoch.get(pid)
        if epoch is None:
            raise R.ReleaseError(f"cannot read PID epoch for {pid}")
        return epoch

    def host_status(self) -> dict:
        state = "online" if self.host_online else "offline"
        return {"process": state, "host_status": state,
                "host_id": self.host_id, "pid": self.host_pid}

    def runner_pids(self, host_pid: int) -> list[int]:
        if self.host_online and host_pid == self.host_pid:
            return [self.runner_pid]
        return []

    def stop_exact_pid(self, pid: int, epoch: str, label: str) -> None:
        if self.epoch.get(pid) != epoch:
            raise R.ReleaseError(f"{label} PID epoch changed; refusing to signal a reused PID")
        if pid == self.host_pid:
            self.host_online = False
        elif pid == self.real_pid:
            self.server_online = False
        else:
            raise AssertionError(("stop_exact_pid: unexpected pid", pid, label))

    def wait_for_health(self, *, wanted: bool) -> bool:
        return self.server_online == wanted

    def wait_for_host(self, *, wanted: bool):
        return self.host_status() if self.host_online == wanted else None

    def selected_process_env(self, policy_dirs: dict) -> dict:
        return {"PYTHONPATH": policy_dirs[self.generation]}


class EndToEndCutoverAcceptance(unittest.TestCase):
    """MERGE_BLOCKER 4 (PR #494 round 1): the acceptance proof must run the
    real `prepare() -> authorize() -> cutover()` entry points for a
    subsequent-mode candidate B against a verified predecessor A, and must
    inject a real post-mutation failure to prove `_rollback_live` actually
    restores runtime A -- not call `_verify_predecessor`/`_mask_legacy_units`/
    etc. directly, the way the pre-existing `TwoGenerationCutoverAcceptance`
    tests do.

    Every true OS/process/HTTP boundary is mocked (`_FakeWorld`, `_FakeSystemd`,
    the Omnigent HTTP API); the real Git clone/checkout/seal machinery, and
    every orchestration function in the module under test, run unmodified.
    """

    ORIGINAL_PROMPT = "Run the heartbeat via ./bin/nightshift-ctl status\n"
    HOST_ID = "durable-host-1"
    REGISTRATION = {"id": "program-1", "name": "program", "version": 5,
                    "created_at": 0, "updated_at": 0}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(mock.patch.stopall)
        _test_uid_stands_in_for_root(self)

        # A real, disposable Git repo as the release source -- two commits on
        # `main` so `prepare()`'s real clone/checkout/manifest/seal machinery
        # for candidate B runs against genuine Git state, no network needed
        # (`git clone --no-checkout <local-path>` uses the file transport).
        self.source = self.root / "source"
        self._init_release_commit(self.source, generation="A")
        self.sha_a = self._git_rev_parse(self.source, "HEAD")
        self._advance_release_commit(self.source, generation="B")
        self.sha_b = self._git_rev_parse(self.source, "HEAD")

        # Legacy units: the required set, already masked by generation A
        # (real `_mask_legacy_units`, same as `TwoGenerationCutoverAcceptance`).
        self.unit_dir = self.root / "home" / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True)
        self.fake_systemd = _FakeSystemd(self.unit_dir)
        mock.patch.object(R.Path, "home", return_value=self.root / "home").start()
        mock.patch.object(R, "_systemctl_user", side_effect=self.fake_systemd).start()
        self.required = sorted(R.REQUIRED_LEGACY_UNITS)
        for name in self.required:
            (self.unit_dir / name).write_text(f"[Unit]\nDescription={name}\n")
        inventory = R._legacy_unit_inventory()
        R._mask_legacy_units(self.root / "state-a-supervision", inventory)
        for name in self.required:
            self.assertEqual(os.readlink(self.unit_dir / name), "/dev/null")

        # Generation A: a hand-built predecessor fixture (bootstrap mode),
        # same shape as `TwoGenerationCutoverAcceptance` -- it's the fixture
        # baseline, not the thing under test. Its Git manifests are real,
        # taken from the shared source repo's first commit.
        self.state_dir_a = self.root / "state" / self.sha_a
        self.state_dir_a.mkdir(parents=True)
        self.release_root_a = self.root / "release-a"
        (self.release_root_a / "deploy" / "policies").mkdir(parents=True)
        self.control_identity = {"device": 7, "inode": 42}
        self.real_pid = os.getpid()
        real_cmdline = Path(f"/proc/{self.real_pid}/cmdline").read_bytes(
        ).replace(b"\0", b" ").decode()
        self.plan_a = {
            "sha": self.sha_a,
            "state_dir": str(self.state_dir_a),
            "release_root": str(self.release_root_a),
            "omnigent_bin": real_cmdline.split()[0],
            "control_db": dict(self.control_identity),
            "program_git_manifest": R.git_tree_manifest(self.source, self.sha_a),
            "project": str(self.root / "project-a"),
            "supervision": {
                "schema": R.SUPERVISION_SCHEMA, "mode": "bootstrap", "predecessor": None,
                "inherited_masked": [], "to_mask": inventory,
            },
        }
        self.plan_a["plan_digest"] = R._sha256(R._canonical(self.plan_a))
        (self.state_dir_a / "plan.json").write_text(json.dumps(self.plan_a))
        self.receipt_a = {
            "schema": "nightshift-exact-sha-receipt-v1",
            "plan_digest": self.plan_a["plan_digest"],
            "accepted": True,
            "proofs": {name: {"verdict": "green", "operands": {}, "reason": ""}
                      for name in R.PROOFS},
        }
        self.receipt_a["proofs"]["registered"]["operands"] = {
            "actual_id": self.REGISTRATION["id"], "actual_version": self.REGISTRATION["version"],
        }
        self.receipt_a["proofs"]["content_at_sha"]["operands"] = {
            "sha": self.sha_a, "git_object_digest": self.plan_a["program_git_manifest"]["digest"],
        }
        (self.state_dir_a / "receipt.json").write_text(json.dumps(self.receipt_a))

        # A's real served bundle bytes -- extracted straight from the Git
        # commit via `git archive`, so `filesystem_manifest` of the extracted
        # tar equals `program_git_manifest["digest"]` exactly like a real
        # deployed checkout would. Every "/agent/contents" download in this
        # test's reachable code paths (prepare()'s own rollback-bundle
        # preservation, `_verify_predecessor`'s live check, `_verify_rollback`
        # after recovery) is served while generation A is live, so one fixed
        # payload correctly backs every one of them.
        self.bundle_a_bytes = self._archive_program_tree(self.source, self.sha_a)

        self.world = _FakeWorld(real_pid=self.real_pid, host_id=self.HOST_ID)
        self.policy_dirs = {
            "A": str(self.release_root_a / "deploy" / "policies"),
            "B": None,  # never read while B is "live" in this test's reachable paths
            "rolled_back": None,  # filled in once plan_b (with its rollback dir) exists
        }

        self._install_common_mocks()

        # Candidate B's environment: control DB path, project cwd (real
        # `/proc/<pid>/cwd` is read inline by `prepare()`, so `--project`
        # must equal this test process's actual cwd), toolchain, policy dir.
        self.control_db_path = self.root / "control.db"
        self.control_db_path.write_bytes(b"sqlite placeholder")
        self.server_env_path = self.root / "server.env"
        self.server_env_path.write_text("")
        self.rollback_policy_dir = self.release_root_a / "deploy" / "policies"
        (self.rollback_policy_dir / "policy.py").write_text("VALUE = 'reviewed-a'\n")
        toolchain = self.root / "toolchain"
        interpreter = toolchain / "python"
        toolchain.mkdir()
        interpreter.write_text("#!/bin/sh\nexit 0\n")
        interpreter.chmod(0o755)
        launcher = toolchain / "omnigent"
        launcher.write_text("#!/bin/sh\necho 1.0.0-test\n")
        launcher.chmod(0o755)
        self.omnigent_python = interpreter

        self.args_prepare = mock.Mock(
            source=str(self.source), sha=self.sha_b, floor_sha=self.sha_a,
            release_base=str(self.root / "releases"),
            state_base=str(self.root / "state"),
            project=str(Path.cwd()),
            control_db=str(self.control_db_path),
            server_env=str(self.server_env_path),
            server_url="http://127.0.0.1:6767",
            scheduled_task_id="task-1",
            rollback_policy_dir=str(self.rollback_policy_dir),
            omnigent_python=str(self.omnigent_python),
            predecessor_plan=str(self.state_dir_a / "plan.json"),
        )

    # -- fixture helpers ---------------------------------------------
    @staticmethod
    def _git(repo, *args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    @staticmethod
    def _git_rev_parse(repo, ref) -> str:
        return subprocess.check_output(
            ["git", "rev-parse", ref], cwd=repo, text=True
        ).strip()

    def _init_release_commit(self, repo: Path, *, generation: str) -> None:
        (repo / "program").mkdir(parents=True)
        (repo / "deploy" / "policies").mkdir(parents=True)
        (repo / "bin").mkdir(parents=True)
        (repo / "program" / "config.yaml").write_text(f"name: program\ngen: {generation}\n")
        (repo / "deploy" / "policies" / "policy.py").write_text("VALUE = 'reviewed'\n")
        (repo / "bin" / "nightshift-ctl").write_text("#!/bin/sh\nexit 0\n")
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.email", "test@example.invalid")
        self._git(repo, "config", "user.name", "test")
        self._git(repo, "add", "program", "deploy/policies", "bin")
        self._git(repo, "commit", "-qm", f"generation {generation}")

    def _advance_release_commit(self, repo: Path, *, generation: str) -> None:
        (repo / "program" / "config.yaml").write_text(f"name: program\ngen: {generation}\n")
        self._git(repo, "add", "program")
        self._git(repo, "commit", "-qm", f"generation {generation}")

    @staticmethod
    def _archive_program_tree(repo: Path, sha: str) -> bytes:
        raw = subprocess.check_output(
            ["git", "archive", "--format=tar", f"{sha}:program"], cwd=repo
        )
        output = io.BytesIO()
        with (tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as src,
              tarfile.open(fileobj=output, mode="w:gz") as dst):
            for member in src.getmembers():
                dst.addfile(member, src.extractfile(member) if member.isfile() else None)
        return output.getvalue()

    def _install_common_mocks(self):
        world = self
        # Stateful, not a static fixture: `_install_pinned_schedule` really
        # PATCHes this to the pinned prompt before the injected failure, and
        # `_restore_scheduled_task` (inside the real `_rollback_live`) must
        # really PATCH it back -- proving actual restoration, not merely
        # that a fixed GET response was never disturbed.
        self.schedule = {"state": "paused", "next_run_at": None,
                         "prompt": self.ORIGINAL_PROMPT}

        def http(method, url, body=None, timeout=30.0):
            if url.endswith("/agent/contents"):
                return 200, self.bundle_a_bytes, {}
            if "/scheduled-tasks/" in url and method == "GET":
                return 200, json.dumps(self.schedule).encode(), {}
            if "/scheduled-tasks/" in url and method == "PATCH":
                patch = json.loads(body) if isinstance(body, (bytes, str)) else body
                self.schedule.update(patch or {})
                return 200, json.dumps(self.schedule).encode(), {}
            if url.endswith("/v1/agents?limit=1000"):
                return 200, json.dumps({"data": [dict(self.REGISTRATION)]}).encode(), {}
            if url.endswith("/v1/sessions?limit=1000"):
                return 200, json.dumps({"data": [{
                    "id": "durable-session-1", "agent_id": self.REGISTRATION["id"],
                    "host_id": self.HOST_ID, "created_at": 0, "status": "idle",
                    "title": "durable",
                }]}).encode(), {}
            raise AssertionError(("unexpected _http call", method, url))

        mock.patch.object(R, "_http", side_effect=http).start()
        mock.patch.object(
            R, "_new_session", return_value={"id": "session-x", "created_at": 0}
        ).start()
        mock.patch.object(
            R, "_verify_runtime_then_load_bundle",
            return_value=({"kind": "fixed-test-identity"}, {"name": "program", "seats": ["eng"]}),
        ).start()
        mock.patch.object(
            R, "_reverify_omnigent_runtime",
            return_value={"kind": "fixed-test-identity"},
        ).start()
        mock.patch.object(
            R, "_control_identity",
            side_effect=lambda path: {**self.control_identity, "path": str(path)},
        ).start()
        mock.patch.object(R, "_control_snapshot", return_value={
            "admission_schema_ready": True, "active_legacy_slots": [],
            "unfinished_without_admission": [], "active": {},
        }).start()
        mock.patch.object(
            R, "_serving_pid", side_effect=lambda _url: self.world.serving_pid()
        ).start()
        mock.patch.object(
            R, "_pid_epoch", side_effect=lambda pid: self.world.pid_epoch(pid)
        ).start()
        mock.patch.object(
            R, "_host_status", side_effect=lambda _plan: self.world.host_status()
        ).start()
        mock.patch.object(
            R, "_runner_pids", side_effect=lambda host_pid: self.world.runner_pids(host_pid)
        ).start()
        mock.patch.object(
            R, "_stop_exact_pid",
            side_effect=lambda pid, epoch, timeout=60.0, label="process":
                self.world.stop_exact_pid(pid, epoch, label),
        ).start()
        mock.patch.object(R, "_wait_pids_gone", return_value=True).start()
        mock.patch.object(
            R, "_wait_for_health",
            side_effect=lambda _server, *, wanted, timeout: self.world.wait_for_health(wanted=wanted),
        ).start()
        mock.patch.object(
            R, "_wait_for_host",
            side_effect=lambda _plan, *, wanted, timeout: self.world.wait_for_host(wanted=wanted),
        ).start()
        mock.patch.object(R, "_start_tmux", side_effect=self._start_tmux).start()
        mock.patch.object(R, "_kill_own_tmux").start()
        mock.patch.object(
            R, "_selected_process_env",
            side_effect=lambda _pid: self.world.selected_process_env(self.policy_dirs),
        ).start()

    def _start_tmux(self, argv):
        self.world.start_tmux(argv, plan=self._current_plan)

    def test_cutover_failure_after_candidate_start_runs_verified_rollback_of_runtime_a(self):
        # --- prepare() candidate B: the real entry point, subsequent mode
        # against A's verified predecessor evidence. ---
        plan_path = R.prepare(self.args_prepare)
        plan_b = R._load_plan(plan_path)
        self.assertEqual(plan_b["supervision"]["mode"], "subsequent")
        self.assertEqual(
            {row["name"] for row in plan_b["supervision"]["inherited_masked"]},
            set(self.required),
        )
        self.assertEqual(plan_b["supervision"]["to_mask"], [])
        self.assertIn("evidence_digest", plan_b["supervision"]["predecessor"])
        for name in self.required:
            self.assertEqual(
                os.readlink(self.unit_dir / name), "/dev/null",
                "prepare() must not touch inherited masks",
            )
        self._current_plan = plan_b
        self.policy_dirs["rolled_back"] = plan_b["rollback"]["policy_dir"]

        # --- authorize() candidate B: the real, read-only entry point. ---
        auth_args = mock.Mock(plan=str(plan_path))
        authorization = R.authorize(auth_args)
        auth_path = Path(plan_b["state_dir"]) / "authorization.json"
        self.assertTrue(auth_path.is_file())

        # --- cutover() candidate B: the real entry point. The candidate
        # genuinely starts and the heartbeat is genuinely re-pinned to it
        # (a real mutation, via the real `_install_pinned_schedule`) before a
        # real failure is injected at postflight, which must trigger a REAL
        # `_rollback_live` -- not a mock of it -- that genuinely stops B and
        # restarts A via the rollback argv, restores the paused/un-pinned
        # schedule, and re-verifies the restored runtime's served content
        # against A's original digest. ---
        cutover_args = mock.Mock(
            plan=str(plan_path),
            authorization=str(auth_path),
            owner_go=plan_b["plan_digest"],
        )
        prompt_at_failure = {}

        def failing_postflight(_plan, _authorization):
            # Captured mid-flight: the heartbeat really was pinned to
            # candidate B's exact-SHA path before this failure, so the
            # eventual restore-to-original assertion below is meaningful.
            prompt_at_failure["prompt"] = self.schedule["prompt"]
            raise RuntimeError("simulated postflight transport failure")

        with mock.patch.object(R, "postflight", side_effect=failing_postflight):
            with self.assertRaisesRegex(R.ReleaseError, "verified rollback restored"):
                R.cutover(cutover_args)

        self.assertEqual(
            prompt_at_failure["prompt"], plan_b["scheduled_task"]["pinned_prompt"],
        )
        attempt = json.loads(
            (Path(plan_b["state_dir"]) / "cutover-attempt.json").read_text()
        )
        self.assertEqual(attempt["status"], "rolled_back_postflight_evidence_failure")
        self.assertTrue(attempt["rollback"]["green"])

        # Runtime A is genuinely restored: content digest re-derived live
        # from the (mocked) server matches A's original manifest, and the
        # policy directory actually copied to disk at prepare time.
        self.assertEqual(
            attempt["rollback"]["served_digest"], plan_b["rollback"]["manifest"]["digest"]
        )
        self.assertTrue(self.world.server_online)
        self.assertEqual(self.world.generation, "rolled_back")

        # Inherited masks (generation A's required units) were never
        # recreated, re-masked, or unmasked at any point across the whole
        # candidate-B lifecycle and its rollback.
        for name in self.required:
            self.assertEqual(
                os.readlink(self.unit_dir / name), "/dev/null",
                "inherited masks must survive a candidate's full failure+rollback",
            )

        # The heartbeat schedule was restored to its pre-pin state (still
        # paused, original un-pinned prompt) via a real `_restore_scheduled_task`.
        _status, restored_task_raw, _headers = R._http(
            "GET", f"{plan_b['server_url']}/v1/scheduled-tasks/task-1"
        )
        self.assertEqual(
            json.loads(restored_task_raw)["prompt"], self.ORIGINAL_PROMPT
        )


if __name__ == "__main__":
    unittest.main()
