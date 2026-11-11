"""H1 item 1: hermetic project startup binding (ADR-0020).

Every GitHub response comes from a fake selected by NIGHTSHIFT_CTL_GH.  The
suite drives the public CLI, including its filesystem publication boundary;
there is no remote repository, credential, control database, or live service.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
CTL = Path(os.environ.get("NIGHTSHIFT_TEST_CTL", BIN / "nightshift-ctl"))
VECTORS = REPO / "docs" / "architecture" / "H1-ITEM1-ID-VECTORS.json"
DESCRIPTOR_VECTORS = (
    REPO / "docs" / "architecture" / "H1-ITEM1-DESCRIPTOR-VECTORS.json")

REPOSITORY_ID = 1_344_257_718
OWNER_ID = 17_000_001
REQUEST_ID = "h1-102-item1-20260825-a"
REPOSITORY = "tushardhara/nightshift-h1-target-08232203"
START_SHA = "a" * 40
NIGHTSHIFT_SHA = "b" * 40
BRIEF = b"# H1 target\n\nBuild the walking skeleton.\n"
BRIEF_SHA256 = hashlib.sha256(BRIEF).hexdigest()
CHECK_APP_ID = 15_355
CHECK_NAME = "tests"

sys.path.insert(0, str(BIN))
try:
    import nightshift_project as PROJECT
finally:
    sys.path.remove(str(BIN))

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
try:
    from host_uid_ancestry import skip_under_uid_remap
finally:
    sys.path.remove(str(TESTS_DIR))


FAKE_GH = r'''#!/usr/bin/env python3
import fcntl
import json
import os
import sys

scenario_path = os.environ["NS_PROJECT_SCENARIO"]
log_path = os.environ["NS_PROJECT_GH_LOG"]
argv = sys.argv[1:]
encoded = (json.dumps(argv, separators=(",", ":")) + "\n").encode()
fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
try:
    os.write(fd, encoded)
finally:
    os.close(fd)

if len(argv) != 4 or argv[:3] != ["api", "--method", "GET"]:
    print("mutation or non-GET invocation refused", file=sys.stderr)
    sys.exit(97)

with open(scenario_path, encoding="utf-8") as handle:
    scenario = json.load(handle)
endpoint = argv[3]
slug = scenario["repository"]
sha = scenario["start_sha"]
branch = scenario["default_branch"]

if scenario.get("oversized_output") and endpoint == "repos/" + slug:
    sys.stdout.write("x" * 1100000)
    sys.exit(0)

if endpoint == "repos/" + slug:
    if scenario.get("repository_undetermined"):
        print(scenario.get("secret_stderr", "private repository not found"),
              file=sys.stderr)
        sys.exit(1)
    body = {
        "id": scenario["repository_id"],
        "name": slug.split("/", 1)[1],
        "full_name": slug,
        "html_url": "https://github.com/" + slug,
        "private": scenario.get("private", True),
        "disabled": scenario.get("disabled", False),
        "archived": scenario.get("archived", False),
        "fork": scenario.get("fork", False),
        "default_branch": branch,
        "owner": {
            "id": scenario["owner_id"],
            "login": slug.split("/", 1)[0],
            "type": scenario.get("owner_type", "User"),
        },
    }
elif endpoint == "repos/" + slug + "/branches/" + branch:
    branch_sha = sha
    if scenario.get("branch_drift"):
        counter_path = scenario_path + ".branch-count"
        counter = os.open(counter_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(counter, fcntl.LOCK_EX)
            raw = os.read(counter, 64)
            count = int(raw or b"0")
            os.lseek(counter, 0, os.SEEK_SET)
            os.ftruncate(counter, 0)
            os.write(counter, str(count + 1).encode())
        finally:
            os.close(counter)
        if count:
            branch_sha = scenario.get("final_sha", "c" * 40)
    body = {"name": branch, "commit": {"sha": branch_sha}}
elif endpoint == "repos/" + slug + "/contents/.omni/brief.md?ref=" + sha:
    brief = scenario["brief"].encode()
    content = scenario.get("brief_content_b64")
    if content is None:
        content = __import__("base64").b64encode(brief).decode()
    body = {
        "type": "file",
        "path": ".omni/brief.md",
        "encoding": "base64",
        "content": content,
        "size": scenario.get("brief_size", len(brief)),
        "sha": scenario.get("brief_blob_sha", "d" * 40),
    }
elif endpoint == ("repos/" + slug + "/commits/" + sha
                  + "/check-runs?filter=all&per_page=100"):
    runs = scenario["check_runs"]
    body = {"total_count": scenario.get("total_count", len(runs)),
            "check_runs": runs}
elif "/check-runs" in endpoint:
    # Deliberately red: omitting filter=all or per_page=100 cannot pass by
    # accidentally seeing GitHub's default, latest-only view.
    body = {"total_count": 1, "check_runs": [{
        "id": 999, "name": "default-filter-trap", "status": "completed",
        "conclusion": "failure", "app": {"id": 1, "slug": "trap"}}]}
else:
    print("unexpected endpoint", file=sys.stderr)
    sys.exit(98)

if scenario.get("malformed_json") and endpoint == "repos/" + slug:
    print("not-json")
else:
    print(json.dumps(body, separators=(",", ":")))
'''


def _check(run_id=101, *, status="completed", conclusion="success",
           app_id=CHECK_APP_ID, name=CHECK_NAME, slug="github-actions"):
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": app_id, "slug": slug},
    }


class ProjectHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nightshift-project-bind-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # realpath matters on macOS, where /var is a symlink to /private/var.
        self.state_root = os.path.realpath(os.path.join(self.tmp, "state"))
        os.mkdir(self.state_root, 0o700)
        os.chmod(self.state_root, 0o700)
        self.fake_gh = os.path.join(self.tmp, "gh")
        with open(self.fake_gh, "w", encoding="utf-8") as handle:
            handle.write(FAKE_GH)
        os.chmod(self.fake_gh, 0o755)
        self.scenario_path = os.path.join(self.tmp, "scenario.json")
        self.log_path = os.path.join(self.tmp, "gh.jsonl")
        self.scenario = {
            "repository": REPOSITORY,
            "repository_id": REPOSITORY_ID,
            "owner_id": OWNER_ID,
            "default_branch": "main",
            "start_sha": START_SHA,
            "brief": BRIEF.decode(),
            "check_runs": [_check()],
        }
        self.write_scenario()
        self.env = dict(os.environ)
        self.env.update({
            "NIGHTSHIFT_CTL_GH": self.fake_gh,
            "NS_PROJECT_SCENARIO": self.scenario_path,
            "NS_PROJECT_GH_LOG": self.log_path,
            # Sentinels must never cross into output or the descriptor.
            "GH_TOKEN": "secret-token-must-not-leak",
            "GITHUB_TOKEN": "second-secret-must-not-leak",
        })

    def write_scenario(self):
        with open(self.scenario_path, "w", encoding="utf-8") as handle:
            json.dump(self.scenario, handle)

    def bind_argv(self, **changes):
        injected_now = changes.pop("now", None)
        strict = changes.pop("strict", False)
        values = {
            "state_root": self.state_root,
            "request_id": REQUEST_ID,
            "repository": REPOSITORY,
            "repository_id": str(REPOSITORY_ID),
            "owner_id": str(OWNER_ID),
            "default_branch": "main",
            "start_sha": START_SHA,
            "brief_sha256": BRIEF_SHA256,
            "nightshift_sha": NIGHTSHIFT_SHA,
            "checks": [f"{CHECK_APP_ID}:{CHECK_NAME}"],
        }
        values.update(changes)
        argv = [
            str(CTL), "--db", os.path.join(self.tmp, "must-not-exist.db"),
        ]
        if injected_now is not None:
            argv += ["--now=" + str(injected_now)]
        argv += [
            "project", "bind", "--state-root", values["state_root"],
            "--request-id", values["request_id"],
            "--repository", values["repository"],
            "--expect-repository-id", values["repository_id"],
            "--expect-owner-id", values["owner_id"],
            "--expect-default-branch", values["default_branch"],
            "--expect-start-sha", values["start_sha"],
            "--expect-brief-sha256", values["brief_sha256"],
        ]
        for identity in values["checks"]:
            argv += ["--required-check", identity]
        argv += ["--nightshift-sha", values["nightshift_sha"]]
        if strict:
            argv.append("--require-trusted-ancestry")
        return argv

    def show_argv(self, project_run_id, repository_id=str(REPOSITORY_ID),
                  state_root=None, strict=False):
        argv = [
            str(CTL), "--db", os.path.join(self.tmp, "must-not-exist.db"),
            "project", "show", "--state-root", state_root or self.state_root,
            "--repository-id", repository_id,
            "--project-run-id", project_run_id,
        ]
        if strict:
            argv.append("--require-trusted-ancestry")
        return argv

    def invoke(self, argv, *, env=None):
        return subprocess.run(
            argv, capture_output=True, text=True, env=env or self.env,
            cwd=self.tmp,
        )

    def run_json(self, argv, *, env=None):
        result = self.invoke(argv, env=env)
        try:
            body = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not one JSON object: {exc}: {result.stdout!r}; "
                      f"stderr={result.stderr[:400]!r}")
        return result, body

    def calls(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def clear_calls(self):
        try:
            os.unlink(self.log_path)
        except FileNotFoundError:
            pass

    def descriptor_path(self, project_run_id, *, state_root=None,
                        repository_id=REPOSITORY_ID):
        return os.path.join(
            state_root or self.state_root, "startups", str(repository_id),
            project_run_id + ".json")

    def ancestry_observation(self, path):
        """Independent fd-walk oracle for portable ancestry expectations."""
        canonical = os.path.realpath(path)
        components = [item for item in canonical.split("/") if item]
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        rows = []
        paths = ["/"]
        try:
            info = os.fstat(current)
            rows.append((0, info.st_uid, info.st_mode))
            prefix = ""
            for depth, component in enumerate(components, 1):
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current)
                os.close(current)
                current = child
                info = os.fstat(current)
                rows.append((depth, info.st_uid, info.st_mode))
                prefix += "/" + component
                paths.append(prefix)
        finally:
            os.close(current)
        trust, boundary = PROJECT._classify_ancestry(rows, os.geteuid())
        observed = {"ancestry_trust": trust}
        if boundary is not None:
            observed["ancestry_trust_boundary"] = {
                **boundary, "path": paths[boundary["depth"]]}
        return observed

    def opened_trust_fact(self, path):
        """Record the predicate inputs/result for one named chain component."""
        canonical = os.path.realpath(path)
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in canonical.split("/")[1:]:
                if not component:
                    continue
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current)
                os.close(current)
                current = child
            info = os.fstat(current)
        finally:
            os.close(current)
        reason = PROJECT._ancestry_failure_reason(
            info.st_uid, info.st_mode, os.geteuid())
        return {
            "path": canonical,
            "st_uid": info.st_uid,
            "st_mode": info.st_mode,
            "trusted": reason is None,
            "reason": reason,
        }

    def make_fully_trusted_root(self, name):
        home = os.path.realpath(str(Path.home()))
        trust_facts = [
            self.opened_trust_fact("/"),
            self.opened_trust_fact(os.path.dirname(home)),
            self.opened_trust_fact(home),
        ]
        self.assertTrue(all(item["trusted"] for item in trust_facts),
                        trust_facts)
        base = tempfile.mkdtemp(
            prefix=".nightshift-project-trusted-", dir=home)
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        os.chmod(base, 0o700)
        state_root = os.path.join(base, name)
        os.mkdir(state_root, 0o700)
        observation = self.ancestry_observation(state_root)
        self.assertEqual(observation, {"ancestry_trust": "full"})
        return base, state_root

    def bind_ok(self, **changes):
        result, body = self.run_json(self.bind_argv(**changes))
        self.assertEqual(result.returncode, 0, result.stderr)
        return body


class ProjectIdentityAndDispatch(ProjectHarness):
    def test_committed_id_vectors_are_consumed(self):
        with open(VECTORS, encoding="utf-8") as handle:
            vectors = json.load(handle)["vectors"]
        self.assertGreaterEqual(len(vectors), 3)
        for vector in vectors:
            preimage = (b"nightshift.project-run.v1\0"
                        + str(vector["repository_id"]).encode("ascii") + b"\0"
                        + vector["request_id"].encode("ascii"))
            self.assertEqual(preimage.hex(), vector["canonical_preimage_hex"])
            self.assertEqual(
                hashlib.sha256(preimage).hexdigest(), vector["sha256_hex"])
            self.assertEqual(PROJECT.derive_project_run_id(
                vector["repository_id"], vector["request_id"]),
                vector["project_run_id"])

    def test_committed_descriptor_vectors_are_canonical_and_valid(self):
        with open(DESCRIPTOR_VECTORS, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            document["kind"],
            "nightshift.project-startup-descriptor-vectors")
        self.assertEqual([item["name"] for item in document["vectors"]],
                         ["full", "partial"])
        for vector in document["vectors"]:
            descriptor = vector["descriptor"]
            raw = PROJECT._canonical_bytes(descriptor)
            self.assertEqual(raw.hex(), vector["canonical_bytes_hex"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             vector["sha256_hex"])
            PROJECT._validate_descriptor(
                descriptor, raw, state_root=descriptor["state_root"],
                repository_id=descriptor["repository"]["id"],
                project_run_id=descriptor["project_run_id"])

    def test_environmental_ancestry_inputs_do_not_change_request_facts(self):
        project_id = PROJECT.derive_project_run_id(
            REPOSITORY_ID, REQUEST_ID)
        base = {
            "repository_id": REPOSITORY_ID,
            "owner_id": OWNER_ID,
            "request_id": REQUEST_ID,
            "repository": REPOSITORY,
            "default_branch": "main",
            "start_sha": START_SHA,
            "brief_sha256": BRIEF_SHA256,
            "nightshift_sha": NIGHTSHIFT_SHA,
            "required_checks": [(CHECK_APP_ID, CHECK_NAME)],
            "project_run_id": project_id,
        }
        expected = {
            "schema_version": 1,
            "kind": "nightshift.project-startup-request-facts",
            "project_run_id": project_id,
            "request_id": REQUEST_ID,
            "repository": REPOSITORY,
            "repository_id": REPOSITORY_ID,
            "owner_id": OWNER_ID,
            "default_branch": "main",
            "start_sha": START_SHA,
            "brief_sha256": BRIEF_SHA256,
            "required_checks": [
                {"app_id": CHECK_APP_ID, "name": CHECK_NAME}],
            "nightshift_sha": NIGHTSHIFT_SHA,
        }
        expected_raw = (json.dumps(
            expected, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False) + "\n").encode("utf-8")
        expected_digest = hashlib.sha256(expected_raw).hexdigest()
        variants = (
            base,
            {**base, "ancestry_trust": "full",
             "require_trusted_ancestry": False},
            {**base, "ancestry_trust": "partial",
             "ancestry_trust_boundary": {
                 "depth": 1, "path": "/tmp",
                 "reason": "other_writable"},
             "require_trusted_ancestry": True},
        )
        for values in variants:
            with self.subTest(values=sorted(set(values) - set(base))):
                facts = PROJECT._request_facts(values)
                self.assertEqual(facts, expected)
                self.assertEqual(PROJECT._canonical_bytes(facts), expected_raw)
                self.assertEqual(PROJECT._facts_digest(facts), expected_digest)

    def test_plane_free_dispatch_is_verb_granular_and_safe_without_cmd(self):
        namespace = runpy.run_path(str(CTL), run_name="nightshift_ctl_test")
        is_plane_free = namespace["is_plane_free"]
        self.assertTrue(is_plane_free(types.SimpleNamespace(group="project", cmd="bind")))
        self.assertTrue(is_plane_free(types.SimpleNamespace(group="project", cmd="show")))
        self.assertFalse(is_plane_free(types.SimpleNamespace(group="project", cmd="start")))
        self.assertTrue(is_plane_free(types.SimpleNamespace(group="pr-head")))
        self.assertNotIn("project", namespace["PLANE_FREE_COMMANDS"])

        parsed = types.SimpleNamespace(
            now=None, db=None, group="project", cmd="start", func=mock.Mock())
        connection = mock.Mock()
        connect = mock.Mock(return_value=connection)
        ensure_schema = mock.Mock()
        main = namespace["main"]
        with mock.patch.object(
                namespace["argparse"].ArgumentParser, "parse_args",
                return_value=parsed), mock.patch.dict(main.__globals__, {
                    "resolve_db_path": mock.Mock(
                        return_value=("/fake/control.db", "test")),
                    "connect": connect,
                    "ensure_schema": ensure_schema,
                }):
            main([])
        connect.assert_called_once_with("/fake/control.db", create=False)
        ensure_schema.assert_called_once_with(connection)
        parsed.func.assert_called_once_with(connection, parsed)
        connection.close.assert_called_once_with()

    def test_usage_error_and_remote_undetermined_are_distinct_exit_twos(self):
        usage = self.invoke([str(CTL), "project", "show", "--state-root",
                             self.state_root])
        self.assertEqual(usage.returncode, 2)
        self.assertEqual(usage.stdout, "")
        self.assertIn("usage:", usage.stderr)

        self.scenario["repository_undetermined"] = True
        self.scenario["secret_stderr"] = "404 secret-token-must-not-leak"
        self.write_scenario()
        remote, body = self.run_json(self.bind_argv())
        self.assertEqual(remote.returncode, 2)
        self.assertEqual(body["reason_code"], "repository_undetermined")
        self.assertEqual(remote.stderr, "")
        self.assertNotIn("secret-token", remote.stdout)

    def test_local_operational_error_is_structured_exit_one(self):
        namespace = runpy.run_path(str(CTL), run_name="nightshift_ctl_test")
        sys.path.insert(0, str(BIN))
        try:
            import nightshift_project as project
            with mock.patch.object(
                    project, "bind_project",
                    side_effect=project.ProjectError(
                        "state_operation_failed", 1)):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), \
                        self.assertRaises(SystemExit) as caught:
                    namespace["cmd_project_bind"](None, types.SimpleNamespace())
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(json.loads(stdout.getvalue()), {
                "ok": False, "reason_code": "state_operation_failed"})
        finally:
            sys.path.remove(str(BIN))

    def test_parsed_invalid_ids_are_structured_exit_three_before_path_use(self):
        for field, value, reason in (
            ("repository_id", "0", "invalid_repository_id"),
            ("repository_id", "01", "invalid_repository_id"),
            ("repository_id", "9223372036854775808", "invalid_repository_id"),
            ("owner_id", "x", "invalid_owner_id"),
        ):
            with self.subTest(field=field, value=value):
                result, body = self.run_json(self.bind_argv(**{field: value}))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)
        result, body = self.run_json(self.show_argv("../escape"))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "invalid_project_run_id")
        valid_project_id = "prun_v1_" + "a" * 26
        for repository_id in (
                "..", "../..", "/etc", "%2e%2e", "1344257718/../999"):
            with self.subTest(repository_id=repository_id):
                result, body = self.run_json(self.show_argv(
                    valid_project_id, repository_id=repository_id))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(
                    body["reason_code"], "invalid_repository_id")
        self.assertEqual(self.calls(), [])


class ProjectHappyPath(ProjectHarness):
    def test_observed_at_uses_the_global_clock_override(self):
        injected_now = 1_787_643_000.75
        body = self.bind_ok(
            request_id="observed-at-clock-seam", now=injected_now)
        descriptor = json.loads(Path(
            self.descriptor_path(body["project_run_id"])).read_text())
        self.assertEqual(descriptor["observed_at"], int(injected_now))

    def test_invalid_clock_overrides_refuse_before_io(self):
        for index, injected_now in enumerate((
                float("nan"), float("inf"), float("-inf"), 0, -1,
                PROJECT.MAX_ID + 1)):
            with self.subTest(injected_now=injected_now):
                result, body = self.run_json(self.bind_argv(
                    request_id=f"invalid-clock-{index}", now=injected_now))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], "invalid_observed_at")
                self.assertEqual(self.calls(), [])
                self.assertFalse(os.path.exists(os.path.join(
                    self.state_root, "startups")))

    def test_observed_at_accepts_signed_epoch_boundaries(self):
        for value in (1, PROJECT.MAX_ID):
            with self.subTest(value=value):
                self.assertEqual(
                    PROJECT._observation_time(
                        types.SimpleNamespace(now=value)), value)

    def test_bind_records_exact_snapshot_and_show_is_bounded_and_plane_free(self):
        result, body = self.run_json(self.bind_argv())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body["status"], "created")
        self.assertEqual(body["reason_code"], "descriptor_created")
        self.assertEqual(len(self.calls()), 5)
        endpoints = [call[3] for call in self.calls()]
        self.assertEqual(endpoints, [
            "repos/" + REPOSITORY,
            "repos/" + REPOSITORY + "/branches/main",
            "repos/" + REPOSITORY + "/contents/.omni/brief.md?ref=" + START_SHA,
            f"repos/{REPOSITORY}/commits/{START_SHA}/check-runs"
            "?filter=all&per_page=100",
            "repos/" + REPOSITORY + "/branches/main",
        ])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "must-not-exist.db")))

        path = self.descriptor_path(body["project_run_id"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(os.path.dirname(path))).st_mode), 0o700)
        with open(path, "rb") as handle:
            raw = handle.read()
        descriptor = json.loads(raw)
        expected_ancestry = self.ancestry_observation(self.state_root)
        self.assertEqual(body["observed_ancestry_trust"],
                         expected_ancestry["ancestry_trust"])
        self.assertEqual(descriptor["ancestry_trust"],
                         expected_ancestry["ancestry_trust"])
        self.assertEqual(
            descriptor.get("ancestry_trust_boundary"),
            expected_ancestry.get("ancestry_trust_boundary"))
        self.assertEqual(raw, (json.dumps(descriptor, sort_keys=True,
                                         separators=(",", ":"),
                                         ensure_ascii=False) + "\n").encode())
        self.assertEqual(descriptor["repository"]["id"], REPOSITORY_ID)
        self.assertEqual(len(descriptor["observed_check_runs"]), 1)
        self.assertNotIn("run_id", descriptor)
        forbidden = ("secret-token", "second-secret", BRIEF.decode().strip())
        for needle in forbidden:
            self.assertNotIn(needle, raw.decode())
            self.assertNotIn(needle, result.stdout)
            self.assertNotIn(needle, result.stderr)

        self.clear_calls()
        hostile = dict(self.env, NIGHTSHIFT_CTL_GH="/must/not/run")
        shown, shown_body = self.run_json(
            self.show_argv(body["project_run_id"]), env=hostile)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown_body["descriptor_sha256"], body["descriptor_sha256"])
        self.assertEqual(shown_body["descriptor"], descriptor)
        self.assertEqual(shown_body["observed_ancestry_trust"],
                         expected_ancestry["ancestry_trust"])
        self.assertEqual(self.calls(), [])

    def test_exact_replay_is_network_free_and_preserves_inode_bytes_and_mtime(self):
        first = self.bind_ok()
        path = self.descriptor_path(first["project_run_id"])
        before_stat = os.stat(path)
        before = Path(path).read_bytes()
        self.clear_calls()
        replay_env = dict(self.env, NIGHTSHIFT_CTL_GH="/must/not/run")
        result, replay = self.run_json(self.bind_argv(), env=replay_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(replay["status"], "existing")
        self.assertEqual(replay["descriptor_sha256"], first["descriptor_sha256"])
        self.assertEqual(
            replay["observed_ancestry_trust"],
            self.ancestry_observation(self.state_root)["ancestry_trust"])
        after_stat = os.stat(path)
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertEqual(after_stat.st_ino, before_stat.st_ino)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(self.calls(), [])

    def test_same_repository_request_changed_fact_conflicts_without_network(self):
        first = self.bind_ok()
        path = self.descriptor_path(first["project_run_id"])
        before = Path(path).read_bytes()
        self.clear_calls()
        result, body = self.run_json(
            self.bind_argv(nightshift_sha="c" * 40),
            env=dict(self.env, NIGHTSHIFT_CTL_GH="/must/not/run"))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "startup_conflict")
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_show_missing_is_deterministic_and_never_scans_other_repositories(self):
        wanted = "prun_v1_" + "a" * 26
        startups = os.path.join(self.state_root, "startups")
        os.mkdir(startups, 0o700)
        os.chmod(startups, 0o700)
        other = os.path.join(startups, "999")
        os.mkdir(other, 0o700)
        os.chmod(other, 0o700)
        Path(other, wanted + ".json").write_text("trap", encoding="utf-8")
        result, body = self.run_json(self.show_argv(wanted))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "descriptor_not_found")
        self.assertEqual(self.calls(), [])

    def test_same_request_id_under_another_repository_is_a_separate_namespace(self):
        first = self.bind_ok()
        other_id = REPOSITORY_ID + 1
        other_repo = "tushardhara/other-private-target"
        self.scenario.update(repository=other_repo, repository_id=other_id)
        self.write_scenario()
        second = self.bind_ok(repository=other_repo, repository_id=str(other_id))
        self.assertNotEqual(first["project_run_id"], second["project_run_id"])
        self.assertTrue(os.path.exists(os.path.join(
            self.state_root, "startups", str(other_id),
            second["project_run_id"] + ".json")))

    def test_bare_and_both_https_repository_forms_are_equivalent(self):
        forms = [
            REPOSITORY,
            "https://github.com/" + REPOSITORY,
            "https://github.com/" + REPOSITORY + ".git",
        ]
        for index, repository in enumerate(forms):
            with self.subTest(repository=repository):
                body = self.bind_ok(
                    repository=repository, request_id=f"repository-form-{index}")
                descriptor = json.loads(Path(
                    self.descriptor_path(body["project_run_id"])).read_text())
                self.assertEqual(descriptor["requested_repository"], REPOSITORY)


class ProjectConcurrency(ProjectHarness):
    def _parallel(self, argvs):
        processes = [subprocess.Popen(argv, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True,
                                      env=self.env, cwd=self.tmp)
                     for argv in argvs]
        raw_results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            raw_results.append((process.returncode, stdout, stderr))
        return [(code, json.loads(stdout), stderr)
                for code, stdout, stderr in raw_results]

    def test_twenty_identical_binds_publish_once(self):
        results = self._parallel([self.bind_argv() for _ in range(20)])
        self.assertEqual([code for code, _, _ in results], [0] * 20)
        statuses = [body["status"] for _, body, _ in results]
        self.assertEqual(statuses.count("created"), 1)
        self.assertEqual(statuses.count("existing"), 19)
        ids = {body["project_run_id"] for _, body, _ in results}
        self.assertEqual(len(ids), 1)
        finals = list(Path(self.state_root, "startups", str(REPOSITORY_ID))
                      .glob("prun_v1_*.json"))
        self.assertEqual(len(finals), 1)

    def test_conflicting_concurrent_binds_have_one_immutable_winner(self):
        a = self.bind_argv(nightshift_sha="b" * 40)
        b = self.bind_argv(nightshift_sha="c" * 40)
        results = self._parallel([a if i % 2 == 0 else b for i in range(20)])
        statuses = [body.get("status") for _, body, _ in results]
        reasons = [body["reason_code"] for _, body, _ in results]
        self.assertEqual(statuses.count("created"), 1)
        self.assertEqual(statuses.count("existing"), 9)
        self.assertEqual(reasons.count("startup_conflict"), 10)
        self.assertEqual(sum(code == 0 for code, _, _ in results), 10)
        self.assertEqual(sum(code == 3 for code, _, _ in results), 10)


class ProjectRemoteValidation(ProjectHarness):
    def test_all_duplicate_check_runs_are_retained_and_any_non_green_refuses(self):
        self.scenario["check_runs"] = [
            _check(101),
            _check(102, conclusion="failure"),
        ]
        self.write_scenario()
        result, body = self.run_json(self.bind_argv())
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "check_not_successful")

    def test_successful_same_named_duplicate_runs_are_all_persisted(self):
        self.scenario["check_runs"] = [_check(101), _check(102)]
        self.write_scenario()
        body = self.bind_ok()
        descriptor = json.loads(Path(
            self.descriptor_path(body["project_run_id"])).read_text())
        self.assertEqual(
            [item["id"] for item in descriptor["observed_check_runs"]],
            [101, 102])

    def test_missing_pending_and_skipped_checks_refuse(self):
        cases = [
            ([_check(app_id=999, name="other")], "required_check_missing"),
            ([_check(status="in_progress", conclusion=None)], "check_not_successful"),
            ([_check(conclusion="skipped")], "check_not_successful"),
            ([], "check_set_empty"),
        ]
        for runs, reason in cases:
            with self.subTest(reason=reason):
                self.scenario["check_runs"] = runs
                self.scenario.pop("total_count", None)
                self.write_scenario()
                result, body = self.run_json(self.bind_argv(request_id=reason))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)

    def test_check_set_must_be_complete_and_at_most_one_hundred(self):
        for total, reason in ((2, "check_set_incomplete"),
                              (101, "check_set_too_large")):
            with self.subTest(total=total):
                self.scenario["check_runs"] = [_check()]
                self.scenario["total_count"] = total
                self.write_scenario()
                result, body = self.run_json(
                    self.bind_argv(request_id=f"count-{total}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)

    def test_branch_drift_refuses_publication(self):
        self.scenario["branch_drift"] = True
        self.write_scenario()
        result, body = self.run_json(self.bind_argv())
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "branch_drift")
        project_id = body.get("project_run_id", "")
        if project_id:
            self.assertFalse(os.path.exists(self.descriptor_path(project_id)))

    def test_repository_brief_and_declared_identity_mismatches_refuse(self):
        cases = [
            ({"repository_id": REPOSITORY_ID + 9}, {}, "repository_id_mismatch"),
            ({"owner_id": OWNER_ID + 9}, {}, "owner_id_mismatch"),
            ({"private": False}, {}, "repository_unusable"),
            ({"brief": "different"}, {}, "brief_sha256_mismatch"),
            ({}, {"checks": [f"{CHECK_APP_ID}:other"]}, "required_check_missing"),
        ]
        for scenario_change, argv_change, reason in cases:
            with self.subTest(reason=reason):
                original = dict(self.scenario)
                self.scenario.update(scenario_change)
                self.write_scenario()
                result, body = self.run_json(self.bind_argv(
                    request_id="case-" + reason, **argv_change))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)
                self.scenario = original

    def test_brief_empty_oversize_invalid_utf8_and_size_mismatch_fail_closed(self):
        cases = [
            ({"brief": ""}, "repository_undetermined", 2),
            ({"brief": "x" * 65_537}, "repository_undetermined", 2),
            ({"brief_content_b64": "/w==", "brief_size": 1},
             "repository_undetermined", 2),
            ({"brief_size": len(BRIEF) + 1}, "repository_undetermined", 2),
        ]
        for index, (change, reason, code) in enumerate(cases):
            with self.subTest(change=sorted(change)):
                original = dict(self.scenario)
                self.scenario.update(change)
                self.write_scenario()
                result, body = self.run_json(
                    self.bind_argv(request_id=f"bad-brief-{index}"))
                self.assertEqual(result.returncode, code)
                self.assertEqual(body["reason_code"], reason)
                self.scenario = original

    def test_expected_default_branch_and_start_sha_are_proven(self):
        cases = [
            ({"default_branch": "trunk"}, "default_branch_mismatch"),
            ({"start_sha": "c" * 40}, "start_sha_mismatch"),
        ]
        for index, (change, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                original = dict(self.scenario)
                self.scenario.update(change)
                self.write_scenario()
                result, body = self.run_json(
                    self.bind_argv(request_id=f"remote-pin-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)
                self.scenario = original

    def test_malformed_github_data_is_sanitized_undetermined(self):
        self.scenario["malformed_json"] = True
        self.write_scenario()
        result, body = self.run_json(self.bind_argv())
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body["reason_code"], "repository_undetermined")
        self.assertNotIn("not-json", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_oversized_github_output_is_bounded_and_sanitized(self):
        self.scenario["oversized_output"] = True
        self.write_scenario()
        result, body = self.run_json(self.bind_argv())
        self.assertEqual(result.returncode, 2)
        self.assertEqual(body["reason_code"], "repository_undetermined")
        self.assertLess(len(result.stdout), 256)
        self.assertEqual(result.stderr, "")


class ProjectFilesystemSafety(ProjectHarness):
    def test_state_root_beneath_system_temp_is_allowed(self):
        body = self.bind_ok()
        self.assertEqual(body["status"], "created")

    def test_bare_git_name_in_ancestor_is_allowed(self):
        ancestor = os.path.realpath(os.path.join(self.tmp, "bare-git-name"))
        os.mkdir(ancestor, 0o700)
        Path(ancestor, ".git").mkdir()
        state_root = os.path.join(ancestor, "state")
        os.mkdir(state_root, 0o700)
        body = self.bind_ok(
            state_root=state_root, request_id="bare-git-name-is-not-repo")
        self.assertEqual(body["status"], "created")

    def test_forged_head_and_objects_marker_is_allowed(self):
        ancestor = os.path.realpath(os.path.join(self.tmp, "forged-git"))
        os.mkdir(ancestor, 0o700)
        Path(ancestor, ".git", "objects").mkdir(parents=True)
        Path(ancestor, ".git", "HEAD").touch()
        state_root = os.path.join(ancestor, "state")
        os.mkdir(state_root, 0o700)
        verdict = subprocess.run(
            ["git", "-C", state_root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True)
        self.assertNotEqual(verdict.returncode, 0)
        body = self.bind_ok(
            state_root=state_root, request_id="forged-git-is-not-repo")
        self.assertEqual(body["status"], "created")

    def test_non_git_marker_types_in_trusted_segment_are_ambiguous(self):
        for index, marker_type in enumerate(("symlink", "fifo")):
            with self.subTest(marker_type=marker_type):
                ancestor = os.path.realpath(os.path.join(
                    self.tmp, f"non-git-marker-{marker_type}"))
                os.mkdir(ancestor, 0o700)
                marker = os.path.join(ancestor, ".git")
                if marker_type == "symlink":
                    target = os.path.join(ancestor, "empty-target")
                    os.mkdir(target, 0o700)
                    os.symlink(target, marker)
                else:
                    os.mkfifo(marker, 0o600)
                    directory_fd = os.open(
                        ancestor, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        with mock.patch.object(
                                PROJECT, "_is_git_worktree") as git_probe, \
                                self.assertRaises(PROJECT.ProjectError) as caught:
                            PROJECT._check_git_boundary(directory_fd)
                        git_probe.assert_not_called()
                        self.assertEqual(
                            caught.exception.reason_code,
                            "state_root_ambiguous_git_marker")
                    finally:
                        os.close(directory_fd)
                state_root = os.path.join(ancestor, "state")
                os.mkdir(state_root, 0o700)
                result, body = self.run_json(self.bind_argv(
                    state_root=state_root,
                    request_id=f"non-git-marker-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(
                    body["reason_code"], "state_root_ambiguous_git_marker")

    def test_real_git_directory_gitdir_file_and_symlink_are_refused(self):
        git_root = os.path.realpath(os.path.join(self.tmp, "git-root"))
        subprocess.run(
            ["git", "init", "--quiet", git_root], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.chmod(git_root, 0o700)
        git_file_root = os.path.realpath(
            os.path.join(self.tmp, "git-file-root"))
        os.mkdir(git_file_root, 0o700)
        Path(git_file_root, ".git").write_text(
            "gitdir: " + os.path.join(git_root, ".git") + "\n",
            encoding="utf-8")
        git_symlink_root = os.path.realpath(
            os.path.join(self.tmp, "git-symlink-root"))
        os.mkdir(git_symlink_root, 0o700)
        os.symlink(os.path.join(git_root, ".git"),
                   os.path.join(git_symlink_root, ".git"))
        roots = []
        for index, (ancestor, reason) in enumerate((
                (git_root, "invalid_state_root"),
                (git_file_root, "invalid_state_root"),
                (git_symlink_root, "state_root_ambiguous_git_marker"))):
            state_root = os.path.join(ancestor, "private")
            os.mkdir(state_root, 0o700)
            trust_fact = self.opened_trust_fact(ancestor)
            self.assertTrue(trust_fact["trusted"], trust_fact)
            observation = self.ancestry_observation(state_root)
            if observation["ancestry_trust"] == "partial":
                self.assertLess(
                    observation["ancestry_trust_boundary"]["depth"],
                    len([part for part in ancestor.split("/") if part]),
                    observation)
            verdict = subprocess.run(
                ["git", "-C", state_root, "rev-parse",
                 "--is-inside-work-tree"],
                capture_output=True, text=True)
            self.assertEqual(verdict.returncode, 0, verdict.stderr)
            self.assertEqual(verdict.stdout, "true\n")
            roots.append((index, state_root, reason))
        for index, state_root, reason in roots:
            with self.subTest(state_root=state_root):
                result, body = self.run_json(self.bind_argv(
                    state_root=state_root,
                    request_id=f"real-git-marker-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)

    def test_outer_worktree_is_not_masked_by_an_inner_git_marker(self):
        for index, inner_marker in enumerate(("fifo", "repository")):
            with self.subTest(inner_marker=inner_marker):
                outer = os.path.realpath(os.path.join(
                    self.tmp, f"outer-worktree-{index}"))
                subprocess.run(
                    ["git", "init", "--quiet", outer], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                os.chmod(outer, 0o700)
                inner = os.path.join(outer, "inner")
                os.mkdir(inner, 0o700)
                if inner_marker == "fifo":
                    os.mkfifo(os.path.join(inner, ".git"), 0o600)
                else:
                    subprocess.run(
                        ["git", "init", "--quiet", inner], check=True,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True)
                    os.chmod(inner, 0o700)
                state_root = os.path.join(inner, "state")
                os.mkdir(state_root, 0o700)
                for component in (outer, inner):
                    trust_fact = self.opened_trust_fact(component)
                    self.assertTrue(trust_fact["trusted"], trust_fact)
                observation = self.ancestry_observation(state_root)
                if observation["ancestry_trust"] == "partial":
                    self.assertLess(
                        observation["ancestry_trust_boundary"]["depth"],
                        len([part for part in outer.split("/") if part]),
                        observation)
                result, body = self.run_json(self.bind_argv(
                    state_root=state_root,
                    request_id=f"outer-worktree-mask-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], "invalid_state_root")

    def test_git_result_classification_is_not_full_message_dependent(self):
        old_git = (b"fatal: not a git repository "
                   b"(or any of the parent directories): .git\n")
        reworded = b"fatal: not a git repository: discovery stopped\n"
        self.assertFalse(PROJECT._classify_git_worktree(128, b"", old_git))
        self.assertFalse(PROJECT._classify_git_worktree(
            129, b"", reworded))
        self.assertTrue(PROJECT._classify_git_worktree(0, b"true\n", b""))
        self.assertFalse(PROJECT._classify_git_worktree(
            0, b"false\n", b""))

        newer_git = b"fatal: invalid gitfile format: /tmp/.git\n"
        with self.assertRaises(PROJECT.ProjectError) as caught:
            PROJECT._classify_git_worktree(128, b"", newer_git)
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(caught.exception.reason_code, "invalid_state_root")

    def test_git_probe_is_fd_bound_and_failures_are_operational(self):
        anchored = os.path.realpath(os.path.join(self.tmp, "anchored"))
        os.mkdir(anchored, 0o700)
        anchored_fd = os.open(anchored, os.O_RDONLY | os.O_DIRECTORY)
        moved = anchored + "-moved"
        os.rename(anchored, moved)
        os.symlink(REPO, anchored)
        try:
            path_verdict = subprocess.run(
                ["git", "-C", anchored, "rev-parse",
                 "--is-inside-work-tree"],
                capture_output=True, text=True)
            self.assertEqual(path_verdict.stdout, "true\n")
            with mock.patch.dict(os.environ, {
                    "GIT_DIR": str(REPO / ".git"),
                    "GIT_WORK_TREE": str(REPO),
                    "GIT_CONFIG_GLOBAL": "/must/not/be/read",
            }, clear=False):
                self.assertFalse(PROJECT._is_git_worktree(anchored_fd))
        finally:
            os.close(anchored_fd)

        unexpected = os.path.join(self.tmp, "unexpected-git")
        Path(unexpected).write_text(
            "#!" + sys.executable + "\nprint('unexpected')\n",
            encoding="utf-8")
        os.chmod(unexpected, 0o755)
        timeout = os.path.join(self.tmp, "timeout-git")
        Path(timeout).write_text(
            "#!" + sys.executable + "\nimport time\ntime.sleep(10)\n",
            encoding="utf-8")
        os.chmod(timeout, 0o755)

        directory_fd = os.open(self.state_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            cases = (
                (os.path.join(self.tmp, "missing-git"), 0.1),
                (timeout, 0.05),
            )
            for git_bin, probe_timeout in cases:
                with self.subTest(git_bin=git_bin), \
                        mock.patch.object(PROJECT, "GIT_BIN", git_bin), \
                        mock.patch.object(
                            PROJECT, "GIT_PROBE_TIMEOUT_SECONDS",
                            probe_timeout), self.assertRaises(
                                PROJECT.ProjectError) as caught:
                    PROJECT._is_git_worktree(directory_fd)
                self.assertEqual(caught.exception.exit_code, 1)
                self.assertEqual(
                    caught.exception.reason_code, "state_operation_failed")

            with mock.patch.object(PROJECT, "GIT_BIN", unexpected), \
                    self.assertRaises(PROJECT.ProjectError) as caught:
                PROJECT._is_git_worktree(directory_fd)
            self.assertEqual(caught.exception.exit_code, 3)
            self.assertEqual(
                caught.exception.reason_code, "invalid_state_root")
        finally:
            os.close(directory_fd)

    def test_git_probe_times_out_on_blocking_repository_config(self):
        git_root = os.path.realpath(os.path.join(self.tmp, "blocking-config"))
        subprocess.run(
            ["git", "init", "--quiet", git_root], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        fifo = os.path.join(git_root, "config-fifo")
        os.mkfifo(fifo, 0o600)
        Path(git_root, ".git", "config").write_text(
            "[include]\n\tpath = " + fifo + "\n", encoding="utf-8")
        state_root = os.path.join(git_root, "private")
        os.mkdir(state_root, 0o700)
        directory_fd = os.open(state_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(
                    PROJECT, "GIT_PROBE_TIMEOUT_SECONDS", 0.05), \
                    self.assertRaises(PROJECT.ProjectError) as caught:
                PROJECT._is_git_worktree(directory_fd)
            self.assertEqual(caught.exception.exit_code, 1)
            self.assertEqual(
                caught.exception.reason_code, "state_operation_failed")
        finally:
            os.close(directory_fd)

    def test_wrong_mode_symlink_and_srv_project_are_refused(self):
        bad_mode = os.path.realpath(os.path.join(self.tmp, "bad-mode"))
        os.mkdir(bad_mode, 0o755)
        os.chmod(bad_mode, 0o755)
        symlink = os.path.join(self.tmp, "state-link")
        os.symlink(self.state_root, symlink)
        for root in (bad_mode, symlink, "/srv/project"):
            with self.subTest(root=root):
                result, body = self.run_json(
                    self.bind_argv(state_root=root,
                                   request_id="unsafe-" + hashlib.sha1(
                                       root.encode()).hexdigest()[:8]))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], "invalid_state_root")

    def test_show_rejects_self_consistent_identity_tampering(self):
        for index, mutation in enumerate(("repository_id", "request_id")):
            with self.subTest(mutation=mutation):
                body = self.bind_ok(request_id=f"identity-guard-{index}")
                path = self.descriptor_path(body["project_run_id"])
                descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
                if mutation == "repository_id":
                    descriptor["repository"]["id"] += 1
                else:
                    descriptor["request_id"] = "tampered-request-id"
                descriptor["request_facts_sha256"] = PROJECT._facts_digest(
                    PROJECT._request_facts(
                        PROJECT._descriptor_values(descriptor)))
                Path(path).write_bytes(PROJECT._canonical_bytes(descriptor))
                result, shown = self.run_json(
                    self.show_argv(body["project_run_id"]))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(shown["reason_code"], "startup_corrupt")

    def test_corrupt_existing_record_is_never_adopted_or_repaired(self):
        first = self.bind_ok()
        path = self.descriptor_path(first["project_run_id"])
        Path(path).write_text('{"corrupt":true}\n', encoding="utf-8")
        before = Path(path).read_bytes()
        self.clear_calls()
        result, body = self.run_json(self.bind_argv())
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "startup_corrupt")
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertEqual(self.calls(), [])

    def test_noncanonical_or_unknown_descriptor_field_is_corrupt(self):
        first = self.bind_ok()
        path = self.descriptor_path(first["project_run_id"])
        descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
        descriptor["unexpected"] = "field"
        Path(path).write_text(json.dumps(descriptor, sort_keys=True,
                                        separators=(",", ":")) + "\n",
                              encoding="utf-8")
        result, body = self.run_json(
            self.show_argv(first["project_run_id"]))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "startup_corrupt")

    def test_pretty_printed_noncanonical_descriptor_is_corrupt(self):
        first = self.bind_ok()
        path = self.descriptor_path(first["project_run_id"])
        descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
        Path(path).write_text(json.dumps(descriptor, indent=2) + "\n",
                              encoding="utf-8")
        result, body = self.run_json(self.show_argv(first["project_run_id"]))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "startup_corrupt")

    def test_reordered_observed_checks_are_noncanonical_and_corrupt(self):
        self.scenario["check_runs"] = [_check(101), _check(102)]
        self.write_scenario()
        first = self.bind_ok(request_id="reordered-check-observations")
        path = self.descriptor_path(first["project_run_id"])
        descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
        descriptor["observed_check_runs"].reverse()
        Path(path).write_text(json.dumps(
            descriptor, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8")
        result, body = self.run_json(self.show_argv(first["project_run_id"]))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"], "startup_corrupt")

    def test_wrong_ids_digest_mode_and_symlink_final_are_corrupt(self):
        mutations = ("repository_id", "project_name", "owner_login",
                     "project_run_id", "facts_digest", "mode", "symlink")
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                body = self.bind_ok(request_id=f"corruption-{index}")
                path = self.descriptor_path(body["project_run_id"])
                raw = Path(path).read_bytes()
                descriptor = json.loads(raw)
                if mutation == "repository_id":
                    descriptor["repository"]["id"] += 1
                elif mutation == "project_name":
                    descriptor["repository"]["name"] = "another-repository"
                elif mutation == "owner_login":
                    descriptor["repository"]["owner_login"] = "another-owner"
                elif mutation == "project_run_id":
                    descriptor["project_run_id"] = "prun_v1_" + "a" * 26
                elif mutation == "facts_digest":
                    descriptor["request_facts_sha256"] = "0" * 64
                elif mutation == "mode":
                    os.chmod(path, 0o644)
                elif mutation == "symlink":
                    backup = path + ".target"
                    os.rename(path, backup)
                    os.symlink(backup, path)
                if mutation in ("repository_id", "project_name", "owner_login",
                                "project_run_id", "facts_digest"):
                    Path(path).write_text(json.dumps(
                        descriptor, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8")
                result, shown = self.run_json(
                    self.show_argv(body["project_run_id"]))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(shown["reason_code"], "startup_corrupt")

    def test_wrong_file_owner_and_symlink_intermediate_are_refused(self):
        body = self.bind_ok()
        sys.path.insert(0, str(BIN))
        try:
            import nightshift_project as project
            args = types.SimpleNamespace(
                state_root=self.state_root,
                repository_id=str(REPOSITORY_ID),
                project_run_id=body["project_run_id"],
            )
            real_fstat = os.fstat
            def wrong_file_owner(fd):
                info = real_fstat(fd)
                if stat.S_ISREG(info.st_mode):
                    values = list(info)
                    values[4] = info.st_uid + 1
                    return os.stat_result(values)
                return info
            with mock.patch.object(project.os, "fstat",
                                   side_effect=wrong_file_owner):
                with self.assertRaises(project.ProjectError) as caught:
                    project.show_project(args)
            self.assertEqual(caught.exception.reason_code, "startup_corrupt")
        finally:
            sys.path.remove(str(BIN))

        alternate = os.path.realpath(os.path.join(self.tmp, "alternate"))
        os.mkdir(alternate, 0o700)
        os.chmod(alternate, 0o700)
        os.symlink(os.path.join(self.state_root, "startups"),
                   os.path.join(alternate, "startups"))
        result, shown = self.run_json(self.show_argv(
            body["project_run_id"], state_root=alternate))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(shown["reason_code"], "invalid_state_root")

    def test_wrong_intermediate_mode_is_refused(self):
        body = self.bind_ok()
        repository_dir = os.path.dirname(
            self.descriptor_path(body["project_run_id"]))
        os.chmod(repository_dir, 0o755)
        result, shown = self.run_json(self.show_argv(body["project_run_id"]))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(shown["reason_code"], "invalid_state_root")

    def test_file_exists_race_preserves_the_conflicting_winner(self):
        request_id = "deterministic-conflicting-winner"
        competitor_root = os.path.realpath(os.path.join(
            self.tmp, "competitor-state"))
        os.mkdir(competitor_root, 0o700)
        competitor = self.bind_ok(
            state_root=competitor_root, request_id=request_id,
            nightshift_sha="c" * 40)
        competitor_path = os.path.join(
            competitor_root, "startups", str(REPOSITORY_ID),
            competitor["project_run_id"] + ".json")
        descriptor = json.loads(Path(competitor_path).read_text())
        descriptor["state_root"] = self.state_root
        competing_raw = PROJECT._canonical_bytes(descriptor)
        self.clear_calls()

        parser_args = types.SimpleNamespace(
            state_root=self.state_root,
            request_id=request_id,
            repository=REPOSITORY,
            expect_repository_id=str(REPOSITORY_ID),
            expect_owner_id=str(OWNER_ID),
            expect_default_branch="main",
            expect_start_sha=START_SHA,
            expect_brief_sha256=BRIEF_SHA256,
            required_check=[f"{CHECK_APP_ID}:{CHECK_NAME}"],
            nightshift_sha=NIGHTSHIFT_SHA,
            now=1_787_643_000,
        )

        def publish_competitor_then_lose(
                _source, destination, *, dst_dir_fd, **_kwargs):
            winner_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600, dir_fd=dst_dir_fd)
            try:
                os.fchmod(winner_fd, 0o600)
                PROJECT._write_all(winner_fd, competing_raw)
                os.fsync(winner_fd)
            finally:
                os.close(winner_fd)
            raise FileExistsError(destination)

        with mock.patch.dict(os.environ, self.env, clear=False), \
                mock.patch.object(
                    PROJECT.os, "link",
                    side_effect=publish_competitor_then_lose), \
                self.assertRaises(PROJECT.ProjectError) as caught:
            PROJECT.bind_project(parser_args)
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(caught.exception.reason_code, "startup_conflict")
        final_path = self.descriptor_path(competitor["project_run_id"])
        self.assertEqual(Path(final_path).read_bytes(), competing_raw)

    def test_hardlink_failure_is_local_exit_one_and_leaves_no_final(self):
        sys.path.insert(0, str(BIN))
        try:
            import nightshift_project as project
            parser_args = types.SimpleNamespace(
                state_root=self.state_root,
                request_id="hardlink-failure",
                repository=REPOSITORY,
                expect_repository_id=str(REPOSITORY_ID),
                expect_owner_id=str(OWNER_ID),
                expect_default_branch="main",
                expect_start_sha=START_SHA,
                expect_brief_sha256=BRIEF_SHA256,
                required_check=[f"{CHECK_APP_ID}:{CHECK_NAME}"],
                nightshift_sha=NIGHTSHIFT_SHA,
            )
            with mock.patch.dict(os.environ, self.env, clear=False), \
                    mock.patch.object(project.os, "link",
                                      side_effect=OSError("unsupported")):
                with self.assertRaises(project.ProjectError) as caught:
                    project.bind_project(parser_args)
            self.assertEqual(caught.exception.exit_code, 1)
            self.assertEqual(caught.exception.reason_code,
                             "state_operation_failed")
            project_id = project.derive_project_run_id(REPOSITORY_ID,
                                                       "hardlink-failure")
            self.assertFalse(os.path.exists(self.descriptor_path(project_id)))
        finally:
            sys.path.remove(str(BIN))

    def test_post_link_cleanup_failure_leaves_complete_accepted_record(self):
        sys.path.insert(0, str(BIN))
        try:
            import nightshift_project as project
            request_id = "post-link-cleanup-failure"
            parser_args = types.SimpleNamespace(
                state_root=self.state_root,
                request_id=request_id,
                repository=REPOSITORY,
                expect_repository_id=str(REPOSITORY_ID),
                expect_owner_id=str(OWNER_ID),
                expect_default_branch="main",
                expect_start_sha=START_SHA,
                expect_brief_sha256=BRIEF_SHA256,
                required_check=[f"{CHECK_APP_ID}:{CHECK_NAME}"],
                nightshift_sha=NIGHTSHIFT_SHA,
            )
            with mock.patch.dict(os.environ, self.env, clear=False), \
                    mock.patch.object(project.os, "unlink",
                                      side_effect=OSError("crash window")):
                with self.assertRaises(project.ProjectError) as caught:
                    project.bind_project(parser_args)
            self.assertEqual(caught.exception.exit_code, 1)
            self.assertEqual(caught.exception.reason_code,
                             "state_operation_failed")

            project_id = project.derive_project_run_id(
                REPOSITORY_ID, request_id)
            final_path = self.descriptor_path(project_id)
            self.assertTrue(os.path.isfile(final_path))
            self.clear_calls()
            result, shown = self.run_json(self.show_argv(project_id))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(shown["status"], "found")
            self.assertEqual(shown["project_run_id"], project_id)
            self.assertEqual(self.calls(), [])
        finally:
            sys.path.remove(str(BIN))


@skip_under_uid_remap
class ProjectTrustedAncestry(ProjectHarness):
    """Asserts truthful host UID ancestry (ADR-0021's production predicate).

    Skipped, by exact test id against tests/host-uid-skip-manifest.json, when
    this process cannot see `/` as uid 0 -- see host_uid_ancestry.py and
    ADR-0041. Remains mandatory everywhere real host UID visibility exists,
    including GitHub Actions.
    """

    def test_full_chain_fixture_records_root_home_parent_and_home_trust(self):
        home = os.path.realpath(str(Path.home()))
        expected_paths = ["/", os.path.dirname(home), home]
        facts = [self.opened_trust_fact(path) for path in expected_paths]
        self.assertEqual([item["path"] for item in facts], expected_paths)
        for fact in facts:
            with self.subTest(path=fact["path"]):
                self.assertTrue(fact["trusted"], fact)
                self.assertIsNone(fact["reason"], fact)
                self.assertTrue(stat.S_ISDIR(fact["st_mode"]))
                self.assertIn(fact["st_uid"], (0, os.geteuid()))
                self.assertEqual(
                    fact["st_mode"] & (stat.S_IWGRP | stat.S_IWOTH), 0)

    def test_trusted_directory_predicate_table_and_deepest_boundary(self):
        directory = stat.S_IFDIR
        effective_uid = os.geteuid()
        foreign_uid = effective_uid + 1
        cases = (
            ("root-755", [(0, 0, directory | 0o755)], "full", None),
            ("root-775", [(0, 0, directory | 0o775)], "partial",
             {"depth": 0, "reason": "group_writable"}),
            ("root-777", [(0, 0, directory | 0o777)], "partial",
             {"depth": 0, "reason": "other_writable"}),
            ("root-1777", [(0, 0, directory | 0o1777)], "partial",
             {"depth": 0, "reason": "other_writable"}),
            ("euid-700", [(0, effective_uid, directory | 0o700)],
             "full", None),
            ("euid-750", [(0, effective_uid, directory | 0o750)],
             "full", None),
            ("euid-770", [(0, effective_uid, directory | 0o770)],
             "partial", {"depth": 0, "reason": "group_writable"}),
            ("euid-777", [(0, effective_uid, directory | 0o777)],
             "partial", {"depth": 0, "reason": "other_writable"}),
            ("foreign-755", [(0, foreign_uid, directory | 0o755)],
             "partial", {"depth": 0, "reason": "foreign_owner"}),
            ("foreign-777", [(0, foreign_uid, directory | 0o777)],
             "partial", {"depth": 0, "reason": "foreign_owner"}),
            ("all-trusted", [
                (0, 0, directory | 0o755),
                (1, effective_uid, directory | 0o750),
                (2, effective_uid, directory | 0o700),
            ], "full", None),
            ("deepest", [
                (0, 0, directory | 0o777),
                (1, effective_uid, directory | 0o700),
                (2, effective_uid, directory | 0o770),
                (3, effective_uid, directory | 0o700),
            ], "partial", {"depth": 2, "reason": "group_writable"}),
        )
        for name, rows, trust, boundary in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    PROJECT._classify_ancestry(rows, effective_uid),
                    (trust, boundary))

    def test_portable_temp_default_and_strict_match_opened_chain(self):
        expected = self.ancestry_observation(self.state_root)
        created = self.bind_ok(request_id="portable-temp-default")
        descriptor = json.loads(Path(self.descriptor_path(
            created["project_run_id"])).read_text(encoding="utf-8"))
        self.assertEqual(descriptor["ancestry_trust"],
                         expected["ancestry_trust"])
        self.assertEqual(descriptor.get("ancestry_trust_boundary"),
                         expected.get("ancestry_trust_boundary"))
        self.assertEqual(created["observed_ancestry_trust"],
                         expected["ancestry_trust"])

        self.clear_calls()
        result, body = self.run_json(self.bind_argv(
            request_id="portable-temp-strict", strict=True))
        if expected["ancestry_trust"] == "partial":
            self.assertEqual(result.returncode, 3)
            self.assertEqual(body, {
                "ok": False,
                "reason_code": "state_root_untrusted_ancestry",
                "ancestry_trust": "partial",
                "ancestry_trust_boundary":
                    expected["ancestry_trust_boundary"],
            })
            self.assertEqual(self.calls(), [])
        else:
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "literal /tmp acceptance evidence is Linux-only")
    def test_linux_literal_tmp_acceptance_evidence(self):
        self.assertEqual(os.path.realpath("/tmp"), "/tmp")
        state_root = tempfile.mkdtemp(
            prefix="nightshift-optionf-linux-", dir="/tmp")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        os.chmod(state_root, 0o700)
        expected = {
            "ancestry_trust": "partial",
            "ancestry_trust_boundary": {
                "depth": 1, "path": "/tmp",
                "reason": "other_writable",
            },
        }
        self.assertEqual(self.ancestry_observation(state_root), expected)
        created = self.bind_ok(
            state_root=state_root, request_id="linux-literal-tmp")
        descriptor = json.loads(Path(self.descriptor_path(
            created["project_run_id"], state_root=state_root)).read_text())
        self.assertEqual(descriptor["ancestry_trust"], "partial")
        self.assertEqual(descriptor["ancestry_trust_boundary"],
                         expected["ancestry_trust_boundary"])
        self.clear_calls()
        result, body = self.run_json(self.bind_argv(
            state_root=state_root, request_id="linux-literal-tmp-strict",
            strict=True))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"],
                         "state_root_untrusted_ancestry")
        self.assertEqual(body["ancestry_trust_boundary"],
                         expected["ancestry_trust_boundary"])
        self.assertEqual(self.calls(), [])

    def test_fully_trusted_default_and_strict_replay_are_identical(self):
        _, state_root = self.make_fully_trusted_root("state")
        created = self.bind_ok(
            state_root=state_root, request_id="full-default-strict")
        path = self.descriptor_path(
            created["project_run_id"], state_root=state_root)
        before = Path(path).read_bytes()
        before_stat = os.stat(path)
        descriptor = json.loads(before)
        self.assertEqual(descriptor["ancestry_trust"], "full")
        self.assertNotIn("ancestry_trust_boundary", descriptor)
        self.clear_calls()
        result, replay = self.run_json(self.bind_argv(
            state_root=state_root, request_id="full-default-strict",
            strict=True), env=dict(self.env, NIGHTSHIFT_CTL_GH="/must/not/run"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(replay["status"], "existing")
        self.assertEqual(replay["observed_ancestry_trust"], "full")
        self.assertEqual(Path(path).read_bytes(), before)
        after_stat = os.stat(path)
        self.assertEqual((after_stat.st_ino, after_stat.st_mtime_ns),
                         (before_stat.st_ino, before_stat.st_mtime_ns))
        self.assertEqual(self.calls(), [])

    def test_trusted_marker_types_fail_closed_without_following_symlink(self):
        git_target = tempfile.mkdtemp(
            prefix=".nightshift-git-target-", dir=str(Path.home()))
        self.addCleanup(shutil.rmtree, git_target, ignore_errors=True)
        subprocess.run(["git", "init", "--quiet", git_target], check=True,
                       capture_output=True, text=True)
        cases = (
            ("fifo", "state_root_ambiguous_git_marker"),
            ("symlink", "state_root_ambiguous_git_marker"),
            ("repository", "invalid_state_root"),
        )
        for index, (marker_type, expected_reason) in enumerate(cases):
            with self.subTest(marker_type=marker_type):
                base, state_root = self.make_fully_trusted_root(
                    f"state-{index}")
                marker = os.path.join(base, ".git")
                if marker_type == "fifo":
                    os.mkfifo(marker, 0o600)
                elif marker_type == "symlink":
                    os.symlink(os.path.join(git_target, ".git"), marker)
                else:
                    subprocess.run(["git", "init", "--quiet", base],
                                   check=True, capture_output=True, text=True)
                result, body = self.run_json(self.bind_argv(
                    state_root=state_root,
                    request_id=f"trusted-marker-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], expected_reason)
                self.assertEqual(self.calls(), [])

    def test_non_enoent_marker_stat_failure_is_ambiguous(self):
        directory_fd = os.open(self.state_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(
                    PROJECT.os, "stat", side_effect=PermissionError()), \
                    self.assertRaises(PROJECT.ProjectError) as caught:
                PROJECT._check_git_boundary(directory_fd)
            self.assertEqual(caught.exception.exit_code, 3)
            self.assertEqual(caught.exception.reason_code,
                             "state_root_ambiguous_git_marker")
        finally:
            os.close(directory_fd)

    def test_marker_at_deepest_untrusted_boundary_is_not_classified(self):
        _, trusted = self.make_fully_trusted_root("trusted")
        boundary = os.path.join(trusted, "boundary")
        os.mkdir(boundary, 0o777)
        os.chmod(boundary, 0o777)
        os.mkfifo(os.path.join(boundary, ".git"), 0o600)
        suffix = os.path.join(boundary, "suffix")
        state_root = os.path.join(suffix, "state")
        os.mkdir(suffix, 0o700)
        os.mkdir(state_root, 0o700)
        expected = self.ancestry_observation(state_root)
        self.assertEqual(expected, {
            "ancestry_trust": "partial",
            "ancestry_trust_boundary": {
                "depth": len([part for part in boundary.split("/") if part]),
                "path": boundary,
                "reason": "other_writable",
            },
        })
        created = self.bind_ok(
            state_root=state_root, request_id="boundary-marker-default")
        descriptor = json.loads(Path(self.descriptor_path(
            created["project_run_id"], state_root=state_root)).read_text())
        self.assertEqual(descriptor["ancestry_trust_boundary"],
                         expected["ancestry_trust_boundary"])
        self.clear_calls()
        result, body = self.run_json(self.bind_argv(
            state_root=state_root, request_id="boundary-marker-strict",
            strict=True))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(body["reason_code"],
                         "state_root_untrusted_ancestry")
        self.assertEqual(self.calls(), [])

    def test_ancestry_schema_corruption_is_never_adopted_or_repaired(self):
        mutations = (
            "missing", "full-with-boundary", "partial-without-boundary",
            "unknown-reason", "non-prefix", "wrong-depth", "depth-overflow",
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                _, state_root = self.make_fully_trusted_root(f"state-{index}")
                created = self.bind_ok(
                    state_root=state_root,
                    request_id=f"ancestry-corruption-{index}")
                path = self.descriptor_path(
                    created["project_run_id"], state_root=state_root)
                descriptor = json.loads(Path(path).read_text())
                if mutation == "missing":
                    descriptor.pop("ancestry_trust")
                elif mutation == "full-with-boundary":
                    descriptor["ancestry_trust_boundary"] = {
                        "depth": 0, "path": "/", "reason": "foreign_owner"}
                else:
                    descriptor["ancestry_trust"] = "partial"
                    descriptor["ancestry_trust_boundary"] = {
                        "depth": 0, "path": "/", "reason": "foreign_owner"}
                    if mutation == "partial-without-boundary":
                        descriptor.pop("ancestry_trust_boundary")
                    elif mutation == "unknown-reason":
                        descriptor["ancestry_trust_boundary"]["reason"] = "banana"
                    elif mutation == "non-prefix":
                        descriptor["ancestry_trust_boundary"]["path"] = "/var"
                    elif mutation == "wrong-depth":
                        descriptor["ancestry_trust_boundary"]["depth"] = 1
                    elif mutation == "depth-overflow":
                        descriptor["ancestry_trust_boundary"]["depth"] = 256
                Path(path).write_bytes(PROJECT._canonical_bytes(descriptor))
                before = Path(path).read_bytes()
                result, body = self.run_json(self.show_argv(
                    created["project_run_id"], state_root=state_root))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], "startup_corrupt")
                self.assertEqual(Path(path).read_bytes(), before)

    def test_replay_reports_live_drift_but_preserves_recorded_bytes(self):
        base, state_root = self.make_fully_trusted_root("state")
        created = self.bind_ok(
            state_root=state_root, request_id="live-ancestry-drift")
        path = self.descriptor_path(
            created["project_run_id"], state_root=state_root)
        before = Path(path).read_bytes()
        before_stat = os.stat(path)
        self.assertEqual(json.loads(before)["ancestry_trust"], "full")
        os.chmod(base, 0o777)
        expected = self.ancestry_observation(state_root)
        self.assertEqual(expected["ancestry_trust"], "partial")

        self.clear_calls()
        result, replay = self.run_json(self.bind_argv(
            state_root=state_root, request_id="live-ancestry-drift"),
            env=dict(self.env, NIGHTSHIFT_CTL_GH="/must/not/run"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(replay["status"], "existing")
        self.assertEqual(replay["observed_ancestry_trust"], "partial")
        self.assertEqual(Path(path).read_bytes(), before)
        after_stat = os.stat(path)
        self.assertEqual((after_stat.st_ino, after_stat.st_mtime_ns),
                         (before_stat.st_ino, before_stat.st_mtime_ns))
        self.assertEqual(self.calls(), [])

        shown, shown_body = self.run_json(self.show_argv(
            created["project_run_id"], state_root=state_root))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown_body["observed_ancestry_trust"], "partial")
        self.assertEqual(
            shown_body["descriptor"]["ancestry_trust"], "full")
        self.assertNotIn(
            "ancestry_trust_boundary", shown_body["descriptor"])
        self.assertEqual(Path(path).read_bytes(), before)

        args = types.SimpleNamespace(
            state_root=state_root, repository_id=str(REPOSITORY_ID),
            project_run_id=created["project_run_id"],
            require_trusted_ancestry=True)
        with mock.patch.object(PROJECT, "_read_descriptor") as read_record, \
                self.assertRaises(PROJECT.ProjectError) as caught:
            PROJECT.show_project(args)
        read_record.assert_not_called()
        self.assertEqual(caught.exception.reason_code,
                         "state_root_untrusted_ancestry")
        self.assertEqual(Path(path).read_bytes(), before)

        bind_args = types.SimpleNamespace(
            state_root=state_root, request_id="strict-before-bind-lookup",
            repository=REPOSITORY,
            expect_repository_id=str(REPOSITORY_ID),
            expect_owner_id=str(OWNER_ID), expect_default_branch="main",
            expect_start_sha=START_SHA,
            expect_brief_sha256=BRIEF_SHA256,
            required_check=[f"{CHECK_APP_ID}:{CHECK_NAME}"],
            nightshift_sha=NIGHTSHIFT_SHA, now=1_787_643_000,
            require_trusted_ancestry=True)
        with mock.patch.object(PROJECT, "_read_descriptor") as read_record, \
                self.assertRaises(PROJECT.ProjectError) as caught:
            PROJECT.bind_project(bind_args)
        read_record.assert_not_called()
        self.assertEqual(caught.exception.reason_code,
                         "state_root_untrusted_ancestry")

    def test_trusted_marker_mutation_has_only_bounded_contract_outcomes(self):
        base, state_root = self.make_fully_trusted_root("state")
        real_marker = os.path.join(base, ".git-real")
        fifo_marker = os.path.join(base, ".git-fifo")
        subprocess.run(["git", "init", "--quiet", base], check=True,
                       capture_output=True, text=True)
        os.rename(os.path.join(base, ".git"), real_marker)
        os.mkfifo(fifo_marker, 0o600)
        marker = os.path.join(base, ".git")
        stop = threading.Event()

        def mutate():
            while not stop.is_set():
                for parked in (real_marker, fifo_marker):
                    if stop.is_set():
                        return
                    try:
                        os.rename(parked, marker)
                        time.sleep(0.0005)
                        os.rename(marker, parked)
                    except FileNotFoundError:
                        pass

        thread = threading.Thread(target=mutate, daemon=True)
        thread.start()
        allowed = {
            (0, "descriptor_created"),
            (0, "descriptor_existing"),
            (3, "invalid_state_root"),
            (3, "state_root_ambiguous_git_marker"),
        }
        try:
            for _ in range(200):
                result, body = self.run_json(self.bind_argv(
                    state_root=state_root,
                    request_id="trusted-marker-mutation"))
                self.assertIn((result.returncode, body.get("reason_code")),
                              allowed, (result.stdout, result.stderr))
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("ancestor_not_quiescent", result.stdout)
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_rejected_quiescence_option_is_absent(self):
        source = Path(BIN, "nightshift_project.py").read_text(encoding="utf-8")
        self.assertNotIn("ancestor_not_quiescent", source)
        self.assertNotIn("quiescence", source.lower())


class ProjectInputValidation(ProjectHarness):
    def test_state_root_traversal_and_noncanonical_paths_refuse_before_io(self):
        roots = (
            "relative/state",
            self.state_root + "/../state",
            "/" + self.state_root,
            "/srv/project/private-state",
        )
        for index, state_root in enumerate(roots):
            with self.subTest(state_root=state_root):
                result, body = self.run_json(self.bind_argv(
                    state_root=state_root,
                    request_id=f"unsafe-state-path-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], "invalid_state_root")
        self.assertEqual(self.calls(), [])

    def test_unsafe_repository_forms_are_refused_before_github(self):
        unsafe = [
            "http://github.com/owner/repo",
            "https://user:pass@github.com/owner/repo",
            "https://github.com/owner/repo?token=x",
            "https://github.com/owner/repo#fragment",
            "https://github.com/owner%2Frepo",
            "https://github.com/owner/../repo",
            "git@github.com:owner/repo.git",
            "ssh://git@github.com/owner/repo",
            "github.com/owner/repo",
        ]
        for index, repository in enumerate(unsafe):
            with self.subTest(repository=repository):
                result, body = self.run_json(self.bind_argv(
                    repository=repository, request_id=f"unsafe-url-{index}"))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], "invalid_repository")
        self.assertEqual(self.calls(), [])

    def test_duplicate_required_check_and_invalid_expected_pins_refuse(self):
        cases = [
            ({"checks": [f"{CHECK_APP_ID}:{CHECK_NAME}"] * 2},
             "duplicate_required_check"),
            ({"default_branch": "bad..branch"}, "invalid_default_branch"),
            ({"start_sha": "A" * 40}, "invalid_start_sha"),
            ({"brief_sha256": "z" * 64}, "invalid_brief_sha256"),
            ({"nightshift_sha": "short"}, "invalid_nightshift_sha"),
        ]
        for index, (change, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                result, body = self.run_json(self.bind_argv(
                    request_id=f"invalid-pin-{index}", **change))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(body["reason_code"], reason)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
