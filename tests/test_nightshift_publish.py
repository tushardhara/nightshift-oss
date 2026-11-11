"""Hermetic contract tests for the sandboxed worker publication tool."""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (
    ROOT
    / "program/agents/crew_alpha/agents/eng_codex/tools/python/nightshift_publish.py"
)
REAL_GIT = shutil.which("git")
REPOSITORY = "example/nightshift"
TASK_ID = "alpha-190"
SLOT_ID = 190
BRANCH = "alpha/190-publish"
AUTHOR_NAME = "crew_alpha.eng_codex"
AUTHOR_EMAIL = "crew_alpha.eng_codex@nightshift.invalid"
TRAILER = "Co-authored-by: omnigent <noreply@omnigent.ai>"


def _load_tool_module():
    stub = types.ModuleType("omnigent_client")
    stub.tool = lambda function: function
    with mock.patch.dict(sys.modules, {"omnigent_client": stub}):
        spec = importlib.util.spec_from_file_location(
            "nightshift_publish_under_test", TOOL_PATH
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


MODULE = _load_tool_module()
PUBLISH = MODULE.nightshift_publish


def run(argv, *, cwd=None, env=None, input_text=None, check=True):
    completed = subprocess.run(
        [str(value) for value in argv],
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        raise AssertionError(
            f"command failed ({completed.returncode}): {argv!r}\n"
            f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
        )
    return completed


class Fixture:
    def __init__(self, *, tracked_codex_scratch=False, gitlink=False):
        self._temporary = tempfile.TemporaryDirectory()
        # macOS exposes /var as a symlink to /private/var.  The production
        # contract deliberately refuses a non-canonical workspace spelling,
        # so fixtures use the canonical spelling too.
        self.root = Path(self._temporary.name).resolve()
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.workspace = self.root / "workspace"
        self.release = self.root / "release"
        self.control = self.root / "control"
        self.contracts = self.control / "contracts"
        self.db = self.control / "control.db"
        self.contract_path = self.contracts / "alpha-190.json"
        self.bin = self.root / "fake-bin"
        self.gh_state = self.root / "gh-state.json"
        self.git_log = self.root / "git-calls.jsonl"
        self.env_log = self.root / "subprocess-env.jsonl"
        self.push_failure = self.root / "fail-push-once"
        self.tracked_codex_scratch = tracked_codex_scratch
        self.gitlink = gitlink
        self.vendor_source = self.root / "vendor-source"
        self.gh_url = "https://github.com/example/nightshift.git"
        self.contract = {
            "version": 1,
            "task_id": TASK_ID,
            "issue": 190,
            "repository": REPOSITORY,
            "default_branch": "main",
            "allowed_paths": ["work.txt"],
            "commit_subject": "Implement sandboxed publication",
            "pr_title": "Implement sandboxed publication",
            "pr_body": "Refs #190\n\nHermetic publication receipt.\n",
        }
        self.slot = {
            "id": SLOT_ID,
            "holder": "crew_alpha/eng_codex",
            "crew": "crew_alpha",
            "task_id": TASK_ID,
            "kind": "normal",
            "state": "active",
            "acquired_at": time.time() - 10,
            "expires_at": time.time() + 3600,
            "released_at": None,
            "dispatched_at": time.time() - 5,
            "bound_agent": "eng_codex",
            "bound_title": "190-worker-publish",
        }
        self.task = {
            "id": TASK_ID,
            "crew": "crew_alpha",
            "agent": "eng_codex",
            "title": "190-worker-publish",
            "issue": 190,
            "state": "building",
            "base_sha": None,
            "spec_ref": "alpha-190.json",
            "worktree": str(self.workspace),
            "branch": BRANCH,
            "pr_url": None,
            "created_at": time.time() - 20,
            "updated_at": time.time() - 5,
        }
        self._create_repository()
        self._create_release()
        self._create_shims()
        self._create_control_plane()
        self.write_contract()
        self.write_gh_state()
        (self.workspace / "work.txt").write_text("changed\n", encoding="utf-8")

    def close(self):
        self._temporary.cleanup()

    def git(self, *args, cwd=None, env=None, input_text=None, check=True):
        return run(
            [REAL_GIT, *args],
            cwd=cwd or self.workspace,
            env=env,
            input_text=input_text,
            check=check,
        )

    def _create_repository(self):
        self.seed.mkdir()
        self.git("init", "-b", "main", cwd=self.seed)
        (self.seed / "work.txt").write_text("base\n", encoding="utf-8")
        shutil.copy2(ROOT / ".gitignore", self.seed / ".gitignore")
        tracked_paths = []
        if self.tracked_codex_scratch:
            tracked = self.seed / ".codex-tmp" / "tracked-at-base.txt"
            tracked.parent.mkdir()
            tracked.write_text("tracked base\n", encoding="utf-8")
            tracked_paths.append(".codex-tmp/tracked-at-base.txt")
        (self.seed / "bin").mkdir()
        shutil.copy2(ROOT / "bin/nightshift-ctl", self.seed / "bin/nightshift-ctl")
        (self.seed / "bin/nightshift-ctl").chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "seed",
                "GIT_AUTHOR_EMAIL": "seed@example.test",
                "GIT_COMMITTER_NAME": "seed",
                "GIT_COMMITTER_EMAIL": "seed@example.test",
            }
        )
        self.git(
            "add", "--", ".gitignore", "work.txt", "bin/nightshift-ctl",
            cwd=self.seed,
        )
        if tracked_paths:
            self.git("add", "--force", "--", *tracked_paths, cwd=self.seed)
        if self.gitlink:
            self.vendor_source.mkdir()
            self.git("init", "-b", "main", cwd=self.vendor_source)
            (self.vendor_source / "lib.txt").write_text("lib\n", encoding="utf-8")
            self.git("add", "lib.txt", cwd=self.vendor_source)
            self.git("commit", "-m", "lib", cwd=self.vendor_source, env=env)
            self.vendor_sha = self.git(
                "rev-parse", "HEAD", cwd=self.vendor_source
            ).stdout.strip()
            self.git(
                "update-index", "--add", "--cacheinfo",
                f"160000,{self.vendor_sha},vendor/x", cwd=self.seed,
            )
        self.git("commit", "-m", "seed", cwd=self.seed, env=env)
        self.base_sha = self.git("rev-parse", "HEAD", cwd=self.seed).stdout.strip()
        self.git("clone", "--bare", str(self.seed), str(self.origin), cwd=self.root)
        self.git("clone", str(self.origin), str(self.workspace), cwd=self.root)
        self.git("checkout", "-b", BRANCH)
        self.git("remote", "set-url", "origin", self.gh_url)
        self.git("remote", "set-url", "--push", "origin", self.gh_url)
        marker = {
            "version": 1,
            "agent": "eng_codex",
            "root": str(self.root),
            "path": str(self.workspace),
            "base_sha": self.base_sha,
            "tool": "nightshift-workspace",
        }
        (self.workspace / ".git/nightshift-workspace.json").write_text(
            json.dumps(marker), encoding="utf-8"
        )
        self.task["base_sha"] = self.base_sha

    def _create_release(self):
        self.release.mkdir()
        self.git("init", "-b", "main", cwd=self.release)
        controller = self.release / "bin/nightshift-ctl"
        controller.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "bin/nightshift-ctl", controller)
        controller.chmod(0o755)
        tool_source = self.release / (
            "program/agents/crew_alpha/agents/eng_codex/tools/python/"
            "nightshift_publish.py"
        )
        tool_source.parent.mkdir(parents=True)
        shutil.copy2(TOOL_PATH, tool_source)
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "release",
                "GIT_AUTHOR_EMAIL": "release@example.test",
                "GIT_COMMITTER_NAME": "release",
                "GIT_COMMITTER_EMAIL": "release@example.test",
            }
        )
        self.git("add", "bin/nightshift-ctl", str(tool_source.relative_to(self.release)),
                 cwd=self.release)
        self.git("commit", "-m", "release", cwd=self.release, env=env)
        self.release_sha = self.git(
            "rev-parse", "HEAD", cwd=self.release
        ).stdout.strip()

    def _create_shims(self):
        self.bin.mkdir()
        git_shim = self.bin / "git"
        git_source = """#!/usr/bin/env python3
import json, os, sys
REAL_GIT = @@REAL_GIT@@
ORIGIN = @@ORIGIN@@
GH_URL = @@GH_URL@@
LOG = @@GIT_LOG@@
ENV_LOG = @@ENV_LOG@@
FAIL_PUSH = @@FAIL_PUSH@@
args = sys.argv[1:]
with open(LOG, 'a', encoding='utf-8') as f:
    f.write(json.dumps(args) + '\\n')
with open(ENV_LOG, 'a', encoding='utf-8') as f:
    f.write(json.dumps(dict(os.environ)) + '\\n')
if 'push' in args and os.path.exists(FAIL_PUSH):
    os.unlink(FAIL_PUSH)
    sys.exit(19)
translated = list(args)
if 'ls-remote' in translated or 'push' in translated:
    for index, value in enumerate(translated):
        if value == GH_URL:
            translated[index] = ORIGIN
os.execv(REAL_GIT, [REAL_GIT, *translated])
"""
        for marker, value in (
            ("@@REAL_GIT@@", REAL_GIT),
            ("@@ORIGIN@@", str(self.origin)),
            ("@@GH_URL@@", self.gh_url),
            ("@@GIT_LOG@@", str(self.git_log)),
            ("@@ENV_LOG@@", str(self.env_log)),
            ("@@FAIL_PUSH@@", str(self.push_failure)),
        ):
            git_source = git_source.replace(marker, repr(value))
        git_shim.write_text(git_source, encoding="utf-8")
        git_shim.chmod(0o755)
        gh_shim = self.bin / "gh"
        gh_source = """#!/usr/bin/env python3
import json, os, subprocess, sys
path = @@GH_STATE@@
env_log = @@ENV_LOG@@
real_git = @@REAL_GIT@@
with open(path, encoding='utf-8') as f:
    state = json.load(f)
args = sys.argv[1:]
state.setdefault('calls', []).append(args)
with open(path, 'w', encoding='utf-8') as f:
    json.dump(state, f)
with open(env_log, 'a', encoding='utf-8') as f:
    f.write(json.dumps(dict(os.environ)) + '\\n')
if args[:1] == ['api']:
    if state.get('fail_api'):
        sys.exit(20)
    print(state.get('api_full_name', 'example/nightshift'))
elif args[:2] == ['pr', 'list']:
    print(json.dumps(state.get('prs', [])))
elif args[:2] == ['pr', 'create']:
    if state.get('fail_create_once'):
        state['fail_create_once'] = False
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        sys.exit(21)
    def value(flag):
        return args[args.index(flag) + 1]
    oid = subprocess.check_output(
        [real_git, 'rev-parse', 'HEAD'], text=True
    ).strip()
    url = 'https://github.com/example/nightshift/pull/190'
    state.setdefault('prs', []).append({
        'url': url, 'state': 'OPEN', 'isDraft': True,
        'baseRefName': value('--base'),
        'headRefName': value('--head'), 'headRefOid': oid,
        'headRepository': {
            'id': 'R_example_nightshift',
            'name': 'nightshift',
        },
        'headRepositoryOwner': {'id': 'U_example', 'login': 'example'},
        'title': value('--title'),
        'body': open(value('--body-file'), encoding='utf-8').read(),
    })
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    print(url)
else:
    sys.exit(22)
"""
        for marker, value in (
            ("@@GH_STATE@@", str(self.gh_state)),
            ("@@ENV_LOG@@", str(self.env_log)),
            ("@@REAL_GIT@@", REAL_GIT),
        ):
            gh_source = gh_source.replace(marker, repr(value))
        gh_shim.write_text(gh_source, encoding="utf-8")
        gh_shim.chmod(0o755)

    def _create_control_plane(self):
        self.contracts.mkdir(parents=True)
        connection = sqlite3.connect(self.db)
        connection.executescript(
            """
            CREATE TABLE tasks(
              id TEXT PRIMARY KEY, crew TEXT, agent TEXT, title TEXT,
              issue INTEGER, state TEXT, base_sha TEXT, spec_ref TEXT,
              worktree TEXT, branch TEXT, pr_url TEXT,
              created_at REAL, updated_at REAL);
            CREATE TABLE worker_slots(
              id INTEGER PRIMARY KEY, holder TEXT, crew TEXT, task_id TEXT,
              kind TEXT, state TEXT, acquired_at REAL, expires_at REAL,
              released_at REAL, dispatched_at REAL,
              bound_agent TEXT, bound_title TEXT);
            """
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(self.task.values()),
        )
        connection.execute(
            "INSERT INTO worker_slots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(self.slot.values()),
        )
        connection.commit()
        connection.close()

    def sql(self, statement, parameters=()):
        connection = sqlite3.connect(self.db)
        connection.execute(statement, parameters)
        connection.commit()
        connection.close()

    def write_contract(self):
        self.contract_path.write_text(json.dumps(self.contract), encoding="utf-8")

    def write_gh_state(self, **updates):
        state = {
            "api_full_name": REPOSITORY,
            "prs": [],
            "calls": [],
        }
        state.update(updates)
        self.gh_state.write_text(json.dumps(state), encoding="utf-8")

    def read_gh_state(self):
        return json.loads(self.gh_state.read_text(encoding="utf-8"))

    def read_subprocess_envs(self):
        return [
            json.loads(line)
            for line in self.env_log.read_text(encoding="utf-8").splitlines()
        ]

    def env(self, **updates):
        env = {
            "_AP_WORKSPACE": str(self.workspace),
            "NIGHTSHIFT_CTL_DB": str(self.db),
            "NIGHTSHIFT_RELEASE_ROOT": str(self.release),
            "NIGHTSHIFT_RELEASE_SHA": self.release_sha,
        }
        env.update(updates)
        return env

    def publish(
        self,
        task_id=TASK_ID,
        slot_id=SLOT_ID,
        *,
        git_executable=None,
        gh_executable=None,
        python_executable=None,
        system_path=None,
        omit_env=(),
        **env_updates,
    ):
        git_executable = git_executable or str(self.bin / "git")
        gh_executable = gh_executable or str(self.bin / "gh")
        python_executable = python_executable or MODULE._PYTHON_EXECUTABLE
        system_path = system_path or f"{self.bin}:/usr/bin:/bin"
        environment = self.env(**env_updates)
        for name in omit_env:
            environment.pop(name, None)
        with mock.patch.object(MODULE, "_GIT_EXECUTABLE", git_executable), \
                mock.patch.object(MODULE, "_GH_EXECUTABLE", gh_executable), \
                mock.patch.object(
                    MODULE, "_PYTHON_EXECUTABLE", python_executable
                ), \
                mock.patch.object(MODULE, "_SYSTEM_PATH", system_path), \
                mock.patch.dict(os.environ, environment, clear=True):
            return json.loads(PUBLISH(task_id=task_id, slot_id=slot_id))

    def ordinary_index_tree(self):
        return self.git("write-tree").stdout.strip()

    def base_tree(self):
        return self.git("rev-parse", f"{self.base_sha}^{{tree}}").stdout.strip()

    def synthesize_task_commit(self):
        index = self.root / "synthetic-index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        self.git("read-tree", self.base_sha, env=env)
        self.git("add", "-A", "--", "work.txt", env=env)
        tree = self.git("write-tree", env=env).stdout.strip()
        identity_env = dict(env)
        identity_env.update(
            {
                "GIT_AUTHOR_NAME": AUTHOR_NAME,
                "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
                "GIT_COMMITTER_NAME": AUTHOR_NAME,
                "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
            }
        )
        message = f"{self.contract['commit_subject']}\n\n{TRAILER}\n"
        sha = self.git(
            "commit-tree",
            tree,
            "-p",
            self.base_sha,
            env=identity_env,
            input_text=message,
        ).stdout.strip()
        self.git("update-ref", f"refs/heads/{BRANCH}", sha, self.base_sha)
        return sha

    def push_sha(self, sha):
        self.git(
            "push",
            str(self.origin),
            f"{sha}:refs/heads/{BRANCH}",
        )

    def swap_git_config_to_attacker(self):
        attacker = self.root / "attacker.git"
        self.git(
            "init",
            "--bare",
            str(attacker),
            cwd=self.root,
            env={**os.environ, "HOME": str(self.root)},
        )
        attacker_url = attacker.as_uri()
        config = self.workspace / ".git/config"
        original = config.read_text(encoding="utf-8")
        replacement = original.replace(self.gh_url, attacker_url)
        if replacement == original:
            raise AssertionError("fixture origin URL was not present in .git/config")
        staged = config.with_name("config.attacker")
        staged.write_text(replacement, encoding="utf-8")
        os.replace(staged, config)
        return attacker, attacker_url

    def swap_git_config_with_url_rewrite(self, rewrite_key, *, via_include=False):
        attacker = self.root / "attacker.git"
        self.git(
            "init",
            "--bare",
            str(attacker),
            cwd=self.root,
            env={**os.environ, "HOME": str(self.root)},
        )
        attacker_url = attacker.as_uri()
        rewrite = (
            f'[URL "{attacker_url}"]\n'
            f"\t{rewrite_key} = {self.gh_url}\n"
        )
        config = self.workspace / ".git/config"
        replacement = config.read_text(encoding="utf-8")
        if via_include:
            included = self.workspace / ".git/attacker-rewrite.config"
            included.write_text(rewrite, encoding="utf-8")
            replacement += f"\n[include]\n\tpath = {included}\n"
        else:
            replacement += "\n" + rewrite
        staged = config.with_name("config.attacker")
        staged.write_text(replacement, encoding="utf-8")
        os.replace(staged, config)
        return attacker, attacker_url

    def plant_fsmonitor_submodule(self):
        """Populate the base commit's gitlink as the seat could: a nested
        repository whose own config names an fsmonitor command."""
        marker = self.root / "fsmonitor-ran"
        payload = self.root / "fsmonitor-payload"
        payload.write_text(f"#!/bin/sh\n: > {marker!s}\nexit 1\n", encoding="utf-8")
        payload.chmod(0o755)
        nested = self.workspace / "vendor/x"
        if nested.exists():
            nested.rmdir()
        self.git("clone", str(self.vendor_source), str(nested), cwd=self.root)
        self.git("config", "core.fsmonitor", str(payload), cwd=nested)
        (nested / "lib.txt").write_text("dirty\n", encoding="utf-8")
        return marker

    def set_pr(self, sha, **updates):
        row = {
            "url": "https://github.com/example/nightshift/pull/190",
            "state": "OPEN",
            "isDraft": True,
            "baseRefName": "main",
            "headRefName": BRANCH,
            "headRefOid": sha,
            "headRepository": {
                "id": "R_example_nightshift",
                "name": "nightshift",
            },
            "headRepositoryOwner": {"id": "U_example", "login": "example"},
            "title": self.contract["pr_title"],
            "body": self.contract["pr_body"],
        }
        row.update(updates)
        self.write_gh_state(prs=[row])

    def make_other_commit(self):
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "other",
                "GIT_AUTHOR_EMAIL": "other@example.test",
                "GIT_COMMITTER_NAME": "other",
                "GIT_COMMITTER_EMAIL": "other@example.test",
            }
        )
        return self.git(
            "commit-tree",
            self.base_tree(),
            "-p",
            self.base_sha,
            cwd=self.workspace,
            env=env,
            input_text="other\n",
        ).stdout.strip()


@contextmanager
def fixture(**kwargs):
    value = Fixture(**kwargs)
    try:
        yield value
    finally:
        value.close()


class NightshiftPublishTests(unittest.TestCase):
    def assert_refused(self, result, reason=None):
        self.assertFalse(result.get("ok"), result)
        if reason is not None:
            self.assertEqual(result.get("reason"), reason)

    def assert_no_publication_mutation(self, f, head_before, index_before):
        self.assertEqual(f.git("rev-parse", "HEAD").stdout.strip(), head_before)
        self.assertEqual(f.ordinary_index_tree(), index_before)
        remote = f.git(
            "--git-dir",
            str(f.origin),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{BRANCH}",
            check=False,
        )
        self.assertNotEqual(remote.returncode, 0)
        self.assertEqual(f.read_gh_state()["prs"], [])
        self.assertEqual(f.read_gh_state()["calls"], [])

    def assert_no_ref_remote_or_pr_mutation(self, f, head_before, index_before):
        self.assertEqual(f.git("rev-parse", "HEAD").stdout.strip(), head_before)
        self.assertEqual(f.ordinary_index_tree(), index_before)
        remote = f.git(
            "--git-dir",
            str(f.origin),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{BRANCH}",
            check=False,
        )
        self.assertNotEqual(remote.returncode, 0)
        state = f.read_gh_state()
        self.assertEqual(state["prs"], [])
        self.assertFalse(
            [call for call in state["calls"] if call[:2] == ["pr", "create"]]
        )

    def test_successful_first_publication_has_fixed_identity_and_untouched_index(self):
        with fixture() as f:
            before = f.ordinary_index_tree()
            result = f.publish(
                GIT_AUTHOR_NAME="attacker",
                GIT_AUTHOR_EMAIL="attacker@example.test",
                GIT_COMMITTER_NAME="attacker",
                GIT_COMMITTER_EMAIL="attacker@example.test",
            )
            self.assertTrue(result.get("ok"), result)
            sha = result["commit"]
            self.assertEqual(f.ordinary_index_tree(), before)
            self.assertEqual(before, f.base_tree())
            record = f.git(
                "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", sha
            ).stdout
            self.assertEqual(
                record,
                f"{AUTHOR_NAME}\n{AUTHOR_EMAIL}\n{AUTHOR_NAME}\n"
                f"{AUTHOR_EMAIL}\n{f.contract['commit_subject']}\n\n{TRAILER}\n\n",
            )
            remote = f.git(
                "--git-dir", str(f.origin), "rev-parse", f"refs/heads/{BRANCH}"
            ).stdout.strip()
            self.assertEqual(remote, sha)
            self.assertTrue(f.read_gh_state()["prs"][0]["isDraft"])

    def test_commit_only_retry_pushes_and_opens_pr_without_index_mutation(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            before = f.ordinary_index_tree()
            result = f.publish()
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result["commit"], sha)
            self.assertEqual(f.ordinary_index_tree(), before)
            self.assertEqual(before, f.base_tree())

    def test_codex_runtime_scratch_ignore_is_anchored_to_repository_root(self):
        with fixture() as f:
            outside_secret = f.root / "host-secret"
            outside_secret.write_text("must-not-be-staged\n", encoding="utf-8")
            paths = {
                ".codex-tmp/session/auth.json": True,
                ".codex-tmp/session/config.toml": True,
                ".codex-tmp/session/host-secret-link": True,
                ".codex-tmpish/session/auth.json": False,
                ".codex-tmpfile": False,
                "src/.codex-tmp/session/auth.json": False,
            }
            for path, should_ignore in paths.items():
                candidate = f.workspace / path
                candidate.parent.mkdir(parents=True, exist_ok=True)
                if path.endswith("host-secret-link"):
                    candidate.symlink_to(outside_secret)
                else:
                    candidate.write_text("runtime-secret\n", encoding="utf-8")
                ignored = f.git("check-ignore", "--quiet", path, check=False)
                self.assertEqual(
                    ignored.returncode == 0,
                    should_ignore,
                    f"unexpected ignore result for {path}",
                )

    def test_codex_runtime_scratch_preserves_base_and_existing_commit_paths(self):
        for state in ("base", "existing-commit"):
            with self.subTest(state=state), fixture() as f:
                expected_commit = None
                if state == "existing-commit":
                    expected_commit = f.synthesize_task_commit()
                index_before = f.ordinary_index_tree()
                scratch = f.workspace / ".codex-tmp" / "session"
                scratch.mkdir(parents=True)
                (scratch / "auth.json").write_text(
                    "runtime-secret\n", encoding="utf-8"
                )
                host_config = f.root / f"{state}-host-config.toml"
                host_config.write_text("host-config-secret\n", encoding="utf-8")
                shutil.copy2(host_config, scratch / "config.toml")
                host_auth = f.root / f"{state}-host-auth.json"
                host_auth.write_text("host-auth-secret\n", encoding="utf-8")
                (scratch / "host-auth-link").symlink_to(host_auth)

                result = f.publish()

                self.assertTrue(result.get("ok"), result)
                if expected_commit is not None:
                    self.assertEqual(result["commit"], expected_commit)
                self.assertEqual(f.ordinary_index_tree(), index_before)
                for relative in (
                    ".codex-tmp/session/auth.json",
                    ".codex-tmp/session/config.toml",
                    ".codex-tmp/session/host-auth-link",
                ):
                    scratch_in_commit = f.git(
                        "cat-file", "-e", f"{result['commit']}:{relative}",
                        check=False,
                    )
                    self.assertNotEqual(scratch_in_commit.returncode, 0)

    def test_codex_runtime_scratch_without_project_edit_is_no_changes(self):
        with fixture() as f:
            f.git("restore", "--", "work.txt")
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            scratch = f.workspace / ".codex-tmp" / "session"
            scratch.mkdir(parents=True)
            (scratch / "auth.json").write_text(
                "runtime-secret\n", encoding="utf-8"
            )

            self.assert_refused(f.publish(), "no-changes")
            self.assert_no_publication_mutation(f, head_before, index_before)

    def test_existing_commit_still_refuses_adjacent_untracked_path(self):
        with fixture() as f:
            f.synthesize_task_commit()
            scratch = f.workspace / ".codex-tmp" / "session"
            scratch.mkdir(parents=True)
            (scratch / "auth.json").write_text("runtime\n", encoding="utf-8")
            adjacent = f.workspace / ".codex-tmp-adjacent"
            adjacent.write_text("not approved\n", encoding="utf-8")
            ignored = f.git(
                "check-ignore", "--quiet", adjacent.name, check=False
            )
            self.assertNotEqual(ignored.returncode, 0)

            self.assert_refused(f.publish(), "changed-tree-after-commit")

    def test_push_only_retry_opens_pr(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            result = f.publish()
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result["commit"], sha)
            self.assertEqual(result["state"], "published")

    def test_existing_draft_pr_is_idempotent(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.set_pr(sha)
            result = f.publish()
            self.assertEqual(
                result,
                {
                    "ok": True,
                    "state": "existing-draft-pr",
                    "commit": sha,
                    "url": "https://github.com/example/nightshift/pull/190",
                },
            )
            creates = [
                call
                for call in f.read_gh_state()["calls"]
                if call[:2] == ["pr", "create"]
            ]
            self.assertEqual(creates, [])
            lists = [
                call
                for call in f.read_gh_state()["calls"]
                if call[:2] == ["pr", "list"]
            ]
            self.assertEqual(len(lists), 1)
            self.assertEqual(lists[0][lists[0].index("--head") + 1], BRANCH)
            self.assertNotIn(f"example:{BRANCH}", lists[0])

    def test_newly_added_file_is_idempotent_with_ordinary_index_at_base(self):
        with fixture() as f:
            (f.workspace / "work.txt").write_text("base\n", encoding="utf-8")
            (f.workspace / "new.txt").write_text("new\n", encoding="utf-8")
            f.contract["allowed_paths"] = ["new.txt"]
            f.write_contract()

            first = f.publish()
            self.assertTrue(first.get("ok"), first)
            status = f.git(
                "status", "--porcelain=v1", "--untracked-files=all"
            ).stdout.splitlines()
            self.assertIn("D  new.txt", status)
            self.assertIn("?? new.txt", status)

            second = f.publish()
            self.assertEqual(
                second,
                {
                    "ok": True,
                    "state": "existing-draft-pr",
                    "commit": first["commit"],
                    "url": first["url"],
                },
            )

    def test_real_commit_only_failure_then_retry(self):
        with fixture() as f:
            # A recognized commit with a one-shot push failure is retry state 2.
            sha = f.synthesize_task_commit()
            f.push_failure.touch()
            self.assert_refused(f.publish(), "subprocess-failed")
            result = f.publish()
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result["commit"], sha)

    def test_real_push_only_failure_then_retry(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.write_gh_state(fail_create_once=True)
            self.assert_refused(f.publish(), "subprocess-failed")
            result = f.publish()
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result["commit"], sha)

    def test_invalid_public_inputs_and_missing_environment(self):
        cases = [
            (("", SLOT_ID), "invalid-task-id"),
            (("bad\x00task", SLOT_ID), "invalid-task-id"),
            ((TASK_ID, True), "invalid-slot-id"),
            ((TASK_ID, 0), "invalid-slot-id"),
            ((TASK_ID, "190"), "invalid-slot-id"),
        ]
        for (task_id, slot_id), reason in cases:
            with self.subTest(reason=reason, task_id=task_id, slot_id=slot_id):
                self.assert_refused(
                    json.loads(PUBLISH(task_id=task_id, slot_id=slot_id)), reason
                )
        with mock.patch.dict(
            os.environ,
            {"_AP_WORKSPACE": "", "NIGHTSHIFT_CTL_DB": ""},
            clear=True,
        ):
            self.assert_refused(
                json.loads(PUBLISH(task_id=TASK_ID, slot_id=SLOT_ID)),
                "missing-runner-binding",
            )

    def test_missing_task_and_slot_are_refused_without_subprocess(self):
        with fixture() as f:
            before = f.git_log.read_text() if f.git_log.exists() else ""
            self.assert_refused(f.publish(task_id="missing"), "binding-mismatch")
            self.assert_refused(f.publish(slot_id=999), "binding-mismatch")
            after = f.git_log.read_text() if f.git_log.exists() else ""
            self.assertEqual(after, before)

    def test_control_db_environment_has_one_canonical_source(self):
        with self.subTest(case="canonical-only"), fixture() as f:
            result = f.publish()
            self.assertTrue(result.get("ok"), result)

        with self.subTest(case="canonical-missing"), fixture() as f:
            self.assert_refused(
                f.publish(omit_env=("NIGHTSHIFT_CTL_DB",)),
                "missing-runner-binding",
            )

        with self.subTest(case="canonical-empty"), fixture() as f:
            self.assert_refused(
                f.publish(NIGHTSHIFT_CTL_DB=""),
                "missing-runner-binding",
            )

        for legacy_value in (str(Path("/tmp") / "legacy-control.db"), ""):
            with self.subTest(case="legacy-only", value=legacy_value), fixture() as f:
                self.assert_refused(
                    f.publish(
                        omit_env=("NIGHTSHIFT_CTL_DB",),
                        NIGHTSHIFT_CONTROL_DB=legacy_value,
                    ),
                    "missing-runner-binding",
                )

        with self.subTest(case="canonical-wins"), fixture() as f:
            hostile = f.root / "hostile-control.db"
            hostile.write_bytes(b"not sqlite")
            result = f.publish(NIGHTSHIFT_CONTROL_DB=str(hostile))
            self.assertTrue(result.get("ok"), result)

        with self.subTest(case="canonical-bad-no-legacy-fallback"), fixture() as f:
            self.assert_refused(
                f.publish(
                    NIGHTSHIFT_CTL_DB=str(f.root / "missing-control.db"),
                    NIGHTSHIFT_CONTROL_DB=str(f.db),
                ),
                "control-db-unavailable",
            )

    def test_slot_binding_refusal_matrix(self):
        cases = [
            ("state='released'", (), "released"),
            ("expires_at=?", (time.time() - 1,), "expired"),
            ("expires_at=NULL", (), "missing-expiry"),
            ("expires_at='not-a-timestamp'", (), "invalid-expiry"),
            ("expires_at=?", (float("inf"),), "non-finite-expiry"),
            ("dispatched_at=NULL", (), "unconsumed"),
            ("bound_agent='eng_claude'", (), "wrong-seat"),
            ("bound_title='other'", (), "wrong-title"),
            ("holder='crew_beta/eng_codex'", (), "wrong-holder"),
            ("task_id='other'", (), "wrong-task"),
        ]
        for assignment, parameters, label in cases:
            with self.subTest(case=label), fixture() as f:
                f.sql(
                    f"UPDATE worker_slots SET {assignment} WHERE id=?",
                    (*parameters, SLOT_ID),
                )
                self.assert_refused(f.publish(), "slot-not-bound")

    def test_task_binding_refusal_matrix(self):
        cases = [
            ("crew='crew_beta'", (), "crew"),
            ("agent='eng_claude'", (), "agent"),
            ("state='ready'", (), "state"),
            ("worktree=?", ("/tmp/not-the-workspace",), "workspace"),
        ]
        for assignment, parameters, label in cases:
            with self.subTest(case=label), fixture() as f:
                f.sql(
                    f"UPDATE tasks SET {assignment} WHERE id=?",
                    (*parameters, TASK_ID),
                )
                expected = (
                    "workspace-binding-mismatch"
                    if label == "workspace"
                    else "task-not-in-flight"
                )
                self.assert_refused(f.publish(), expected)

    def test_control_db_symlink_is_refused(self):
        with fixture() as f:
            link = f.root / "db-link"
            link.symlink_to(f.db)
            self.assert_refused(
                f.publish(
                    NIGHTSHIFT_CTL_DB=str(link),
                    NIGHTSHIFT_CONTROL_DB=str(f.db),
                ),
                "control-db-unavailable",
            )

    def test_release_binding_shape_and_identity_refusal_matrix(self):
        cases = [
            ({"NIGHTSHIFT_RELEASE_ROOT": ""}, "missing-release-binding"),
            ({"NIGHTSHIFT_RELEASE_SHA": ""}, "missing-release-binding"),
            ({"NIGHTSHIFT_RELEASE_SHA": "abc"}, "invalid-release-sha"),
            ({"NIGHTSHIFT_RELEASE_SHA": "0" * 40}, "release-head-mismatch"),
        ]
        for environment, reason in cases:
            with self.subTest(reason=reason), fixture() as f:
                head_before = f.git("rev-parse", "HEAD").stdout.strip()
                index_before = f.ordinary_index_tree()
                self.assert_refused(f.publish(**environment), reason)
                self.assert_no_publication_mutation(f, head_before, index_before)

        with fixture() as f:
            release_link = f.root / "release-link"
            release_link.symlink_to(f.release, target_is_directory=True)
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            self.assert_refused(
                f.publish(NIGHTSHIFT_RELEASE_ROOT=str(release_link)),
                "invalid-release-root",
            )
            self.assert_no_publication_mutation(f, head_before, index_before)

        with fixture() as f:
            noncanonical = str(f.release.parent / "workspace" / ".." / "release")
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            self.assert_refused(
                f.publish(NIGHTSHIFT_RELEASE_ROOT=noncanonical),
                "invalid-release-root",
            )
            self.assert_no_publication_mutation(f, head_before, index_before)

        with fixture() as f:
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            self.assert_refused(
                f.publish(
                    NIGHTSHIFT_RELEASE_ROOT=str(f.workspace),
                    NIGHTSHIFT_RELEASE_SHA=f.base_sha,
                ),
                "release-root-overlap",
            )
            self.assert_no_publication_mutation(f, head_before, index_before)

    def test_release_tool_and_controller_bytes_must_match_git(self):
        relative_tool = Path(
            "program/agents/crew_alpha/agents/eng_codex/tools/python/"
            "nightshift_publish.py"
        )
        cases = [
            (relative_tool, "release-tool-mismatch"),
            (Path("bin/nightshift-ctl"), "release-controller-mismatch"),
        ]
        for relative, reason in cases:
            with self.subTest(reason=reason), fixture() as f:
                target = f.release / relative
                target.write_bytes(target.read_bytes() + b"\n# mismatch\n")
                if reason == "release-tool-mismatch":
                    f.git("add", str(relative), cwd=f.release)
                    identity = dict(os.environ)
                    identity.update({
                        "GIT_AUTHOR_NAME": "release",
                        "GIT_AUTHOR_EMAIL": "release@example.test",
                        "GIT_COMMITTER_NAME": "release",
                        "GIT_COMMITTER_EMAIL": "release@example.test",
                    })
                    f.git(
                        "commit", "-m", "mismatched release tool",
                        cwd=f.release, env=identity,
                    )
                    f.release_sha = f.git(
                        "rev-parse", "HEAD", cwd=f.release
                    ).stdout.strip()
                head_before = f.git("rev-parse", "HEAD").stdout.strip()
                index_before = f.ordinary_index_tree()
                self.assert_refused(f.publish(), reason)
                self.assert_no_publication_mutation(f, head_before, index_before)

    def test_release_attestation_ignores_git_replacement_objects(self):
        with fixture() as f:
            controller = f.release / "bin/nightshift-ctl"
            controller.write_bytes(controller.read_bytes() + b"\n# changed\n")
            original_oid = f.git(
                "rev-parse", f"{f.release_sha}:bin/nightshift-ctl", cwd=f.release
            ).stdout.strip()
            replacement_oid = f.git(
                "hash-object", "-w", "bin/nightshift-ctl", cwd=f.release
            ).stdout.strip()
            f.git("replace", original_oid, replacement_oid, cwd=f.release)
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()

            self.assert_refused(f.publish(), "release-controller-mismatch")

            self.assert_no_publication_mutation(f, head_before, index_before)
            release_calls = [
                json.loads(line)
                for line in f.git_log.read_text(encoding="utf-8").splitlines()
                if str(f.release) in line
            ]
            self.assertTrue(release_calls)
            self.assertTrue(
                all(call[0] == "--no-replace-objects"
                    for call in release_calls)
            )

    def test_workspace_controller_is_never_executed(self):
        with fixture() as f:
            sentinel = f.root / "workspace-controller-ran"
            workspace_controller = f.workspace / "bin/nightshift-ctl"
            workspace_controller.write_text(
                f"#!/bin/sh\n: > {sentinel!s}\nexit 99\n", encoding="utf-8"
            )
            workspace_controller.chmod(0o755)
            f.contract["allowed_paths"] = ["work.txt", "bin/nightshift-ctl"]
            f.write_contract()

            result = f.publish()

            self.assertTrue(result.get("ok"), result)
            self.assertFalse(sentinel.exists())

    def test_attested_controller_fd_survives_pathname_swap_and_is_reused(self):
        with fixture() as f:
            sentinel = f.root / "swapped-controller-ran"
            controller = f.release / "bin/nightshift-ctl"
            held_path = f.release / "bin/nightshift-ctl.attested"
            controller_fds = []
            original_run_controller = MODULE._run_controller

            def run_controller(controller_fd, workspace, args, **kwargs):
                controller_fds.append(controller_fd)
                if len(controller_fds) == 1:
                    controller.rename(held_path)
                    controller.write_text(
                        f"#!/bin/sh\n: > {sentinel!s}\nexit 99\n",
                        encoding="utf-8",
                    )
                    controller.chmod(0o755)
                return original_run_controller(
                    controller_fd, workspace, args, **kwargs
                )

            with mock.patch.object(
                MODULE, "_run_controller", side_effect=run_controller
            ):
                result = f.publish()

            self.assertTrue(result.get("ok"), result)
            self.assertFalse(sentinel.exists())
            self.assertEqual(len(controller_fds), 2)
            self.assertEqual(len(set(controller_fds)), 1)

    def test_attested_controller_uses_shipping_python_source_stdin(self):
        with fixture() as f, mock.patch.object(
            MODULE, "_run", wraps=MODULE._run
        ) as run_call:
            result = f.publish()

            self.assertTrue(result.get("ok"), result)
            controller_calls = [
                call for call in run_call.call_args_list
                if call.args and call.args[0][:3] == [
                    MODULE._PYTHON_EXECUTABLE, "-I", "-"
                ]
            ]
            self.assertEqual(len(controller_calls), 2)
            controller_fds = [call.kwargs.get("stdin_fd")
                              for call in controller_calls]
            self.assertNotIn(None, controller_fds)
            self.assertEqual(len(set(controller_fds)), 1)
            for call in controller_calls:
                self.assertNotIn("/proc/self/fd", " ".join(call.args[0]))
                self.assertNotIn("/dev/fd", " ".join(call.args[0]))

    def test_controller_isolated_mode_ignores_workspace_stdlib_shadow(self):
        with fixture() as f:
            sentinel = f.root / "workspace-argparse-ran"
            (f.workspace / "argparse.py").write_text(
                f"open({str(sentinel)!r}, 'w').write('ran')\n"
                "raise RuntimeError('workspace argparse imported')\n",
                encoding="utf-8",
            )
            f.contract["allowed_paths"] = ["work.txt", "argparse.py"]
            f.write_contract()

            result = f.publish()

            self.assertTrue(result.get("ok"), result)
            self.assertFalse(sentinel.exists())

    def test_guard_commit_requires_exact_success_receipt_before_mutation(self):
        cases = [
            (lambda _f: "", "empty"),
            (lambda _f: "{", "malformed"),
            (lambda _f: json.dumps({
                "ok": True,
                "guard": "staged-deletions",
                "ref_sha": "f" * 40,
                "deletions": [],
            }), "wrong-ref"),
            (lambda f: json.dumps({
                "ok": True,
                "guard": "wrong-guard",
                "ref_sha": f.base_sha,
                "deletions": [],
            }), "wrong-guard"),
            (lambda f: json.dumps({
                "ok": True,
                "guard": "staged-deletions",
                "ref_sha": f.base_sha,
                "deletions": {},
            }), "wrong-deletions-shape"),
            (lambda f: json.dumps({
                "ok": False,
                "guard": "staged-deletions",
                "ref_sha": f.base_sha,
                "deletions": [],
            }), "not-ok"),
        ]
        for stdout_for, label in cases:
            with self.subTest(case=label), fixture() as f:
                head_before = f.git("rev-parse", "HEAD").stdout.strip()
                index_before = f.ordinary_index_tree()
                receipt = subprocess.CompletedProcess(
                    args=[
                        MODULE._PYTHON_EXECUTABLE,
                        "-I",
                        "-",
                        "guard-commit",
                    ],
                    returncode=0,
                    stdout=stdout_for(f),
                    stderr="",
                )
                with mock.patch.object(
                    MODULE, "_run_controller", return_value=receipt
                ):
                    self.assert_refused(f.publish(), "guard-commit-invalid")
                self.assert_no_ref_remote_or_pr_mutation(
                    f, head_before, index_before
                )

    def test_live_authorization_is_rechecked_at_every_mutation_boundary(self):
        with self.subTest(boundary="commit-ref"), fixture() as f:
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            original = MODULE._run_controller

            def release_after_guard(controller_fd, workspace, args, **kwargs):
                completed = original(controller_fd, workspace, args, **kwargs)
                if args[0] == "guard-commit":
                    f.sql(
                        "UPDATE worker_slots SET state='released' WHERE id=?",
                        (SLOT_ID,),
                    )
                return completed

            with mock.patch.object(
                MODULE, "_run_controller", side_effect=release_after_guard
            ):
                self.assert_refused(f.publish(), "slot-not-bound")
            self.assert_no_ref_remote_or_pr_mutation(f, head_before, index_before)

        with self.subTest(boundary="push"), fixture() as f:
            original = MODULE._make_commit

            def expire_after_commit(*args, **kwargs):
                commit = original(*args, **kwargs)
                f.sql(
                    "UPDATE worker_slots SET expires_at=? WHERE id=?",
                    (time.time() - 1, SLOT_ID),
                )
                return commit

            with mock.patch.object(
                MODULE, "_make_commit", side_effect=expire_after_commit
            ):
                self.assert_refused(f.publish(), "slot-not-bound")
            self.assertNotEqual(
                f.git("rev-parse", "HEAD").stdout.strip(), f.base_sha
            )
            remote = f.git(
                "--git-dir", str(f.origin), "show-ref", "--verify", "--quiet",
                f"refs/heads/{BRANCH}", check=False,
            )
            self.assertNotEqual(remote.returncode, 0)
            self.assertEqual(f.read_gh_state()["prs"], [])

        with self.subTest(boundary="pr-create"), fixture() as f:
            original = MODULE._push

            def terminalize_after_push(*args, **kwargs):
                original(*args, **kwargs)
                f.sql(
                    "UPDATE tasks SET state='integrated' WHERE id=?", (TASK_ID,)
                )

            with mock.patch.object(
                MODULE, "_push", side_effect=terminalize_after_push
            ):
                self.assert_refused(f.publish(), "task-not-in-flight")
            remote = f.git(
                "--git-dir", str(f.origin), "rev-parse",
                f"refs/heads/{BRANCH}",
            ).stdout.strip()
            self.assertEqual(remote, f.git("rev-parse", "HEAD").stdout.strip())
            self.assertEqual(f.read_gh_state()["prs"], [])

    def test_control_rows_are_loaded_from_one_read_snapshot(self):
        with fixture() as f:
            real_connect = sqlite3.connect
            connection = real_connect(f.db)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.close()
            update_ran = []

            class CursorAfterFetch:
                def __init__(self, cursor, callback=None):
                    self.cursor = cursor
                    self.callback = callback

                def fetchone(self):
                    row = self.cursor.fetchone()
                    if self.callback is not None:
                        callback, self.callback = self.callback, None
                        callback()
                    return row

            class SnapshotConnection:
                def __init__(self, *args, **kwargs):
                    self.connection = real_connect(*args, **kwargs)

                @property
                def row_factory(self):
                    return self.connection.row_factory

                @row_factory.setter
                def row_factory(self, value):
                    self.connection.row_factory = value

                def execute(self, statement, parameters=()):
                    callback = None
                    if "FROM worker_slots" in statement:
                        callback = update_between_selects
                    return CursorAfterFetch(
                        self.connection.execute(statement, parameters), callback
                    )

                def close(self):
                    self.connection.close()

            def update_between_selects():
                writer = real_connect(f.db)
                writer.execute(
                    "UPDATE worker_slots SET bound_title='replacement' WHERE id=?",
                    (SLOT_ID,),
                )
                writer.execute(
                    "UPDATE tasks SET title='replacement' WHERE id=?", (TASK_ID,)
                )
                writer.commit()
                writer.close()
                update_ran.append(True)

            with mock.patch.object(
                MODULE.sqlite3, "connect", side_effect=SnapshotConnection
            ):
                slot, task = MODULE._load_rows(str(f.db), TASK_ID, SLOT_ID)

            self.assertEqual(update_ran, [True])
            self.assertEqual(slot["bound_title"], "190-worker-publish")
            self.assertEqual(task["title"], "190-worker-publish")

    def test_contract_and_pr_url_are_immutable_at_first_mutation_boundary(self):
        cases = ("contract", "pr-url")
        for changed in cases:
            with self.subTest(changed=changed), fixture() as f:
                head_before = f.git("rev-parse", "HEAD").stdout.strip()
                index_before = f.ordinary_index_tree()
                original = MODULE._run_controller

                def change_after_guard(controller_fd, workspace, args, **kwargs):
                    completed = original(controller_fd, workspace, args, **kwargs)
                    if args[0] == "guard-commit":
                        if changed == "contract":
                            f.contract["pr_title"] = "Replacement title"
                            f.write_contract()
                        else:
                            f.sql(
                                "UPDATE tasks SET pr_url=? WHERE id=?",
                                ("https://github.com/example/nightshift/pull/999", TASK_ID),
                            )
                    return completed

                with mock.patch.object(
                    MODULE, "_run_controller", side_effect=change_after_guard
                ):
                    self.assert_refused(
                        f.publish(),
                        "contract-binding-changed"
                        if changed == "contract"
                        else "task-binding-changed",
                    )
                self.assert_no_ref_remote_or_pr_mutation(
                    f, head_before, index_before
                )

    def test_existing_draft_success_revalidates_live_authorization(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.set_pr(sha)
            original = MODULE._existing_pr_url

            def revoke_after_inspection(*args, **kwargs):
                url = original(*args, **kwargs)
                f.sql(
                    "UPDATE worker_slots SET state='released' WHERE id=?",
                    (SLOT_ID,),
                )
                return url

            with mock.patch.object(
                MODULE, "_existing_pr_url", side_effect=revoke_after_inspection
            ):
                self.assert_refused(f.publish(), "slot-not-bound")

            self.assertEqual(f.git("rev-parse", "HEAD").stdout.strip(), sha)
            self.assertEqual(
                f.git(
                    "--git-dir", str(f.origin), "rev-parse", f"refs/heads/{BRANCH}"
                ).stdout.strip(),
                sha,
            )
            state = f.read_gh_state()
            self.assertEqual(len(state["prs"]), 1)
            self.assertFalse(
                [call for call in state["calls"] if call[:2] == ["pr", "create"]]
            )

    def test_near_expiry_refuses_before_first_publication_mutation(self):
        with fixture() as f:
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            f.sql(
                "UPDATE worker_slots SET expires_at=? WHERE id=?",
                (time.time() + 2, SLOT_ID),
            )

            self.assert_refused(f.publish(), "slot-not-bound")
            self.assert_no_ref_remote_or_pr_mutation(f, head_before, index_before)

    def test_contract_symlink_escape_and_malformed_are_refused(self):
        with fixture() as f:
            outside = f.root / "outside.json"
            outside.write_text(json.dumps(f.contract), encoding="utf-8")
            f.contract_path.unlink()
            f.contract_path.symlink_to(outside)
            self.assert_refused(f.publish(), "invalid-contract-ref")
        with fixture() as f:
            f.sql("UPDATE tasks SET spec_ref=? WHERE id=?", ("../escape.json", TASK_ID))
            self.assert_refused(f.publish(), "invalid-contract-ref")
        with fixture() as f:
            f.contract_path.write_text("{", encoding="utf-8")
            self.assert_refused(f.publish(), "invalid-contract")

    def test_contract_binding_and_ref_refusals(self):
        cases = [
            (lambda f: f.contract.update(task_id="other"), "contract-task-mismatch"),
            (lambda f: f.contract.update(issue=191), "contract-issue-mismatch"),
            (lambda f: f.contract.update(repository="--upload-pack=evil"), "invalid-repository"),
            (lambda f: f.contract.update(default_branch=BRANCH), "default-branch-refused"),
            (lambda f: f.contract.update(default_branch="bad ref"), "invalid-default-branch"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason), fixture() as f:
                mutate(f)
                f.write_contract()
                self.assert_refused(f.publish(), reason)
        with fixture() as f:
            f.sql("UPDATE tasks SET branch=? WHERE id=?", ("--force", TASK_ID))
            self.assert_refused(f.publish())
        with fixture() as f:
            f.sql("UPDATE tasks SET base_sha='abc' WHERE id=?", (TASK_ID,))
            self.assert_refused(f.publish(), "invalid-base-sha")

    def test_allowed_path_refusal_matrix(self):
        cases = [
            ([], "empty"),
            (["work.txt", "work.txt"], "duplicate"),
            (["../work.txt"], "parent"),
            (["a/./b"], "dot"),
            ([".git/config"], "git"),
            ([".codex-tmp"], "codex-runtime-root"),
            ([".codex-tmp/auth.json"], "codex-runtime-child"),
            (["/work.txt"], "absolute"),
        ]
        for allowed, label in cases:
            with self.subTest(case=label), fixture() as f:
                f.contract["allowed_paths"] = allowed
                f.write_contract()
                self.assert_refused(f.publish(), "invalid-allowed-path")
                self.assertFalse(f.git_log.exists())
                self.assertEqual(f.read_gh_state()["calls"], [])

        with fixture(tracked_codex_scratch=True) as f:
            f.contract["allowed_paths"] = [
                ".codex-tmp/tracked-at-base.txt"
            ]
            f.write_contract()

            self.assert_refused(f.publish(), "invalid-allowed-path")
            self.assertFalse(f.git_log.exists())
            self.assertEqual(f.read_gh_state()["calls"], [])

    def test_pr_body_and_prose_refusal_matrix(self):
        cases = [
            (lambda c: c.update(pr_body="Fixes #190"), "invalid-pr-body"),
            (lambda c: c.update(pr_body="No issue reference"), "invalid-pr-body"),
            (lambda c: c.update(commit_subject="subject\nspoof"), "invalid-publication-prose"),
            (lambda c: c.update(pr_title="title\n--head evil"), "invalid-publication-prose"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason), fixture() as f:
                mutate(f.contract)
                f.write_contract()
                self.assert_refused(f.publish(), reason)

    def test_workspace_and_git_dir_shapes_are_refused(self):
        with fixture() as f:
            link = f.root / "workspace-link"
            link.symlink_to(f.workspace, target_is_directory=True)
            self.assert_refused(
                f.publish(_AP_WORKSPACE=str(link)), "workspace-not-private"
            )
        with fixture() as f:
            shutil.rmtree(f.workspace / ".git")
            (f.workspace / ".git").write_text("gitdir: /shared/git\n", encoding="utf-8")
            self.assert_refused(f.publish(), "git-dir-not-private")
        with fixture() as f:
            real_git_dir = f.root / "moved-git"
            (f.workspace / ".git").rename(real_git_dir)
            (f.workspace / ".git").symlink_to(real_git_dir, target_is_directory=True)
            self.assert_refused(f.publish(), "git-dir-not-private")
        with fixture() as f:
            (f.workspace / ".git/nightshift-workspace.json").unlink()
            self.assert_refused(f.publish(), "workspace-marker-invalid")

    def test_marker_refusal_matrix(self):
        marker_path = ".git/nightshift-workspace.json"
        cases = [
            ({"version": 1, "path": "/moved", "base_sha": "x" * 40}, "moved"),
            ({"version": 1, "path": "WORKSPACE", "base_sha": "f" * 40}, "base"),
            ({"version": 1, "agent": "eng_claude", "path": "WORKSPACE", "base_sha": "BASE"}, "agent"),
            ({"version": 2, "path": "WORKSPACE", "base_sha": "BASE"}, "version"),
        ]
        for marker, label in cases:
            with self.subTest(case=label), fixture() as f:
                marker = dict(marker)
                if marker["path"] == "WORKSPACE":
                    marker["path"] = str(f.workspace)
                if marker["base_sha"] == "BASE":
                    marker["base_sha"] = f.base_sha
                (f.workspace / marker_path).write_text(json.dumps(marker), encoding="utf-8")
                self.assert_refused(f.publish(), "workspace-marker-invalid")

    def test_origin_fetch_and_push_spoof_are_refused(self):
        with fixture() as f:
            f.git("remote", "set-url", "origin", "https://github.com/evil/repo.git")
            self.assert_refused(f.publish(), "origin-mismatch")
        with fixture() as f:
            f.git(
                "remote",
                "set-url",
                "--push",
                "origin",
                "https://github.com/evil/repo.git",
            )
            self.assert_refused(f.publish(), "origin-mismatch")

    def test_git_config_swap_before_remote_inspection_cannot_redirect(self):
        with fixture() as f:
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            original = MODULE._github_preflight
            attack = {}

            def swap_after_preflight(*args, **kwargs):
                original(*args, **kwargs)
                attacker, attacker_url = f.swap_git_config_to_attacker()
                attack.update(attacker=attacker, url=attacker_url)

            with mock.patch.object(
                MODULE, "_github_preflight", side_effect=swap_after_preflight
            ):
                self.assert_refused(f.publish(), "origin-mismatch")

            self.assertEqual(f.git("rev-parse", "HEAD").stdout.strip(), head_before)
            attacker_ref = f.git(
                "--git-dir",
                str(attack["attacker"]),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
                check=False,
            )
            self.assertNotEqual(attacker_ref.returncode, 0)
            remote_calls = [
                json.loads(line)
                for line in f.git_log.read_text(encoding="utf-8").splitlines()
                if "ls-remote" in line or '"push"' in line
            ]
            self.assertFalse(
                any(attack["url"] in argument for call in remote_calls for argument in call)
            )

    def test_git_config_swap_before_push_cannot_redirect(self):
        with fixture() as f:
            original = MODULE._make_commit
            attack = {}

            def swap_after_commit(*args, **kwargs):
                commit = original(*args, **kwargs)
                attacker, attacker_url = f.swap_git_config_to_attacker()
                attack.update(attacker=attacker, url=attacker_url)
                return commit

            with mock.patch.object(
                MODULE, "_make_commit", side_effect=swap_after_commit
            ):
                self.assert_refused(f.publish(), "origin-mismatch")

            attacker_ref = f.git(
                "--git-dir",
                str(attack["attacker"]),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
                check=False,
            )
            self.assertNotEqual(attacker_ref.returncode, 0)
            remote_calls = [
                json.loads(line)
                for line in f.git_log.read_text(encoding="utf-8").splitlines()
                if "ls-remote" in line or '"push"' in line
            ]
            self.assertFalse(
                any(attack["url"] in argument for call in remote_calls for argument in call)
            )

    def test_included_insteadof_swap_before_remote_inspection_is_refused(self):
        with fixture() as f:
            original = MODULE._github_preflight
            attack = {}

            def swap_after_preflight(*args, **kwargs):
                original(*args, **kwargs)
                attacker, attacker_url = f.swap_git_config_with_url_rewrite(
                    "InsteadOf", via_include=True
                )
                attack.update(attacker=attacker, url=attacker_url)

            with mock.patch.object(
                MODULE, "_github_preflight", side_effect=swap_after_preflight
            ):
                result = f.publish()

            self.assertEqual(
                result, {"ok": False, "reason": "url-rewrite-config"}
            )
            self.assertNotIn(attack["url"], json.dumps(result))
            attacker_ref = f.git(
                "--git-dir",
                str(attack["attacker"]),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
                check=False,
            )
            self.assertNotEqual(attacker_ref.returncode, 0)

    def test_pushinsteadof_swap_before_push_is_refused(self):
        with fixture() as f:
            original = MODULE._make_commit
            attack = {}

            def swap_after_commit(*args, **kwargs):
                commit = original(*args, **kwargs)
                attacker, attacker_url = f.swap_git_config_with_url_rewrite(
                    "pushInsteadOf"
                )
                attack.update(attacker=attacker, url=attacker_url)
                return commit

            with mock.patch.object(
                MODULE, "_make_commit", side_effect=swap_after_commit
            ):
                result = f.publish()

            self.assertEqual(
                result, {"ok": False, "reason": "url-rewrite-config"}
            )
            self.assertNotIn(attack["url"], json.dumps(result))
            attacker_ref = f.git(
                "--git-dir",
                str(attack["attacker"]),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
                check=False,
            )
            self.assertNotEqual(attacker_ref.returncode, 0)

    def test_stale_local_default_is_refused(self):
        with fixture() as f:
            other = f.make_other_commit()
            f.git("update-ref", "refs/remotes/origin/main", other)
            self.assert_refused(f.publish(), "stale-base")

    def test_stale_live_remote_default_is_refused(self):
        with fixture() as f:
            other = f.make_other_commit()
            f.git("push", str(f.origin), f"{other}:refs/heads/main")
            self.assert_refused(f.publish(), "stale-base")

    def test_dirty_empty_out_of_scope_and_symlink_escape_changes(self):
        with fixture() as f:
            f.git("add", "--", "work.txt")
            self.assert_refused(f.publish(), "dirty-index")
        with fixture() as f:
            (f.workspace / "work.txt").write_text("base\n", encoding="utf-8")
            self.assert_refused(f.publish(), "no-changes")
        with fixture() as f:
            (f.workspace / "outside.txt").write_text("outside\n", encoding="utf-8")
            self.assert_refused(f.publish(), "path-outside-allowlist")
        with fixture() as f:
            (f.workspace / "work.txt").write_text("base\n", encoding="utf-8")
            outside = f.root / "outside.txt"
            outside.write_text("secret\n", encoding="utf-8")
            (f.workspace / "escape").symlink_to(outside)
            f.contract["allowed_paths"] = ["escape"]
            f.write_contract()
            self.assert_refused(f.publish(), "workspace-path-escape")

    def test_allowlisted_tracked_deletion_refuses_without_publication_mutation(self):
        with fixture() as f:
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()
            (f.workspace / "work.txt").unlink()

            self.assert_refused(f.publish(), "deletion-refused")
            self.assertEqual(f.git("rev-parse", "HEAD").stdout.strip(), head_before)
            self.assertEqual(f.ordinary_index_tree(), index_before)
            remote = f.git(
                "--git-dir",
                str(f.origin),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
                check=False,
            )
            self.assertNotEqual(remote.returncode, 0)
            self.assertEqual(f.read_gh_state()["prs"], [])
            self.assertEqual(f.read_gh_state()["calls"], [])
            git_calls = [
                json.loads(line)
                for line in f.git_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertFalse(any("push" in call for call in git_calls))

        with fixture() as f:
            (f.workspace / "work.txt").rename(f.workspace / "renamed.txt")
            f.contract["allowed_paths"] = ["work.txt", "renamed.txt"]
            f.write_contract()
            self.assert_refused(f.publish(), "deletion-refused")

    def test_recognized_commit_with_deletion_is_refused_before_remote_or_pr_mutation(self):
        with fixture() as f:
            (f.workspace / "work.txt").unlink()
            sha = f.synthesize_task_commit()
            index_before = f.ordinary_index_tree()

            self.assert_refused(f.publish(), "deletion-refused")
            self.assertEqual(f.git("rev-parse", "HEAD").stdout.strip(), sha)
            self.assertEqual(f.ordinary_index_tree(), index_before)
            remote = f.git(
                "--git-dir",
                str(f.origin),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
                check=False,
            )
            self.assertNotEqual(remote.returncode, 0)
            self.assertEqual(f.read_gh_state()["prs"], [])
            self.assertEqual(f.read_gh_state()["calls"], [])

    def test_remote_branch_mismatch_is_refused(self):
        with fixture() as f:
            f.push_sha(f.make_other_commit())
            self.assert_refused(f.publish(), "remote-branch-mismatch")

    def test_pr_refusal_matrix(self):
        cases = [
            ({"state": "CLOSED"}, "closed-pr"),
            ({"isDraft": False}, "non-draft-pr"),
            ({"headRefOid": "f" * 40}, "pr-mismatch"),
            ({"headRefName": "other"}, "pr-mismatch"),
            ({"url": ""}, "pr-mismatch"),
            ({"url": None}, "pr-mismatch"),
        ]
        for updates, reason in cases:
            with self.subTest(reason=reason), fixture() as f:
                sha = f.synthesize_task_commit()
                f.push_sha(sha)
                f.set_pr(sha, **updates)
                self.assert_refused(f.publish(), reason)
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.set_pr(sha)
            state = f.read_gh_state()
            state["prs"].append(dict(state["prs"][0]))
            f.gh_state.write_text(json.dumps(state), encoding="utf-8")
            self.assert_refused(f.publish(), "multiple-prs")

    def test_existing_draft_requires_exact_reviewed_identity_tuple(self):
        def replace(field, value):
            return lambda row: row.__setitem__(field, value)

        def remove(field):
            return lambda row: row.pop(field)

        def replace_nested(container, field, value):
            def mutate(row):
                nested = dict(row[container])
                nested[field] = value
                row[container] = nested
            return mutate

        def remove_nested(container, field):
            def mutate(row):
                nested = dict(row[container])
                nested.pop(field)
                row[container] = nested
            return mutate

        cases = [
            (
                "wrong-url-host",
                replace("url", "https://attacker.example/example/nightshift/pull/190"),
            ),
            (
                "wrong-url-repository",
                replace("url", "https://github.com/example/other/pull/190"),
            ),
            (
                "noncanonical-url-zero",
                replace("url", "https://github.com/example/nightshift/pull/0"),
            ),
            (
                "noncanonical-url-leading-zero",
                replace("url", "https://github.com/example/nightshift/pull/0190"),
            ),
            (
                "url-extra-path",
                replace("url", "https://github.com/example/nightshift/pull/190/files"),
            ),
            (
                "url-query",
                replace("url", "https://github.com/example/nightshift/pull/190?diff=split"),
            ),
            (
                "url-fragment",
                replace("url", "https://github.com/example/nightshift/pull/190#discussion"),
            ),
            (
                "url-credentials",
                replace("url", "https://token@github.com/example/nightshift/pull/190"),
            ),
            ("wrong-base", replace("baseRefName", "release")),
            ("missing-base", remove("baseRefName")),
            ("null-base", replace("baseRefName", None)),
            ("missing-head-repository", remove("headRepository")),
            ("null-head-repository", replace("headRepository", None)),
            ("string-head-repository", replace("headRepository", REPOSITORY)),
            (
                "missing-head-repository-name",
                remove_nested("headRepository", "name"),
            ),
            (
                "null-head-repository-name",
                replace_nested("headRepository", "name", None),
            ),
            (
                "wrong-head-repository-name",
                replace_nested("headRepository", "name", "other"),
            ),
            ("missing-head-owner", remove("headRepositoryOwner")),
            ("null-head-owner", replace("headRepositoryOwner", None)),
            (
                "string-head-owner",
                replace("headRepositoryOwner", "example"),
            ),
            (
                "missing-head-owner-login",
                remove_nested("headRepositoryOwner", "login"),
            ),
            (
                "null-head-owner-login",
                replace_nested("headRepositoryOwner", "login", None),
            ),
            (
                "wrong-head-owner",
                replace_nested("headRepositoryOwner", "login", "fork-owner"),
            ),
            ("wrong-title", replace("title", "Unreviewed replacement title")),
            ("missing-title", remove("title")),
            (
                "closing-keyword-body",
                replace("body", "Refs #190\n\nFixes #190\n"),
            ),
            ("missing-body", remove("body")),
        ]
        for label, mutate in cases:
            with self.subTest(case=label), fixture() as f:
                sha = f.synthesize_task_commit()
                f.push_sha(sha)
                f.set_pr(sha)
                state = f.read_gh_state()
                mutate(state["prs"][0])
                f.gh_state.write_text(json.dumps(state), encoding="utf-8")

                self.assert_refused(f.publish(), "pr-mismatch")
                calls = f.read_gh_state()["calls"]
                self.assertFalse(
                    [call for call in calls if call[:2] == ["pr", "create"]]
                )

    def test_existing_draft_task_pr_url_must_match_exactly(self):
        url = "https://github.com/example/nightshift/pull/190"
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.set_pr(sha)
            f.sql("UPDATE tasks SET pr_url=? WHERE id=?", (url, TASK_ID))

            result = f.publish()

            self.assertEqual(result["state"], "existing-draft-pr")
            self.assertEqual(result["url"], url)

        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.set_pr(sha)
            f.sql(
                "UPDATE tasks SET pr_url=? WHERE id=?",
                ("https://github.com/example/nightshift/pull/191", TASK_ID),
            )

            self.assert_refused(f.publish(), "pr-mismatch")
            self.assertFalse(
                [
                    call
                    for call in f.read_gh_state()["calls"]
                    if call[:2] == ["pr", "create"]
                ]
            )

    def test_recorded_task_pr_url_refuses_when_github_has_no_pr(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.sql(
                "UPDATE tasks SET pr_url=? WHERE id=?",
                ("https://github.com/example/nightshift/pull/190", TASK_ID),
            )

            self.assert_refused(f.publish(), "pr-mismatch")
            self.assertFalse(
                [
                    call
                    for call in f.read_gh_state()["calls"]
                    if call[:2] == ["pr", "create"]
                ]
            )

    def test_existing_draft_normalizes_only_repository_case(self):
        with fixture() as f:
            sha = f.synthesize_task_commit()
            f.push_sha(sha)
            f.set_pr(
                sha,
                url="https://github.com/EXAMPLE/NIGHTSHIFT/pull/190",
                headRepository={
                    "id": "R_example_nightshift",
                    "name": "NIGHTSHIFT",
                },
                headRepositoryOwner={"id": "U_example", "login": "EXAMPLE"},
            )

            result = f.publish()

            self.assertEqual(result["state"], "existing-draft-pr")
            self.assertEqual(result["commit"], sha)
            self.assertEqual(
                result["url"], "https://github.com/EXAMPLE/NIGHTSHIFT/pull/190"
            )

    def test_pr_inspection_requests_the_full_github_identity_shape(self):
        with fixture() as f:
            result = f.publish()
            self.assertTrue(result.get("ok"), result)
            list_calls = [
                call
                for call in f.read_gh_state()["calls"]
                if call[:2] == ["pr", "list"]
            ]
            self.assertTrue(list_calls)
            expected = (
                "url,state,isDraft,baseRefName,headRefName,headRefOid,"
                "headRepository,headRepositoryOwner,title,body"
            )
            for call in list_calls:
                self.assertEqual(call[call.index("--json") + 1], expected)

    def test_subprocess_failure_is_concise_json(self):
        with fixture() as f:
            f.write_gh_state(fail_api=True)
            result = f.publish()
            self.assertEqual(result, {"ok": False, "reason": "subprocess-failed"})

    def test_contended_task_lock_refuses_before_remote_inspection(self):
        with fixture() as f:
            lock_path = Path(MODULE._lock_path(str(f.control), TASK_ID))
            with MODULE._publication_lock(str(f.control), TASK_ID):
                self.assert_refused(f.publish(), "publication-busy")
                self.assertFalse(f.git_log.exists())
            self.assertTrue(lock_path.is_file())

    def test_pinned_executables_reject_symlinks(self):
        self.assertEqual(MODULE._GIT_EXECUTABLE, "/usr/bin/git")
        self.assertEqual(MODULE._GH_EXECUTABLE, "/usr/bin/gh")
        self.assertEqual(MODULE._SYSTEM_PYTHON_ALIAS, "/usr/bin/python3")
        self.assertEqual(
            MODULE._PYTHON_EXECUTABLE,
            os.path.realpath(MODULE._SYSTEM_PYTHON_ALIAS),
        )
        for keyword, target in (
            ("git_executable", "git"),
            ("gh_executable", "gh"),
            ("python_executable", "python"),
        ):
            with self.subTest(executable=target), fixture() as f:
                executable_link = f.root / f"{target}-link"
                source = (
                    Path(MODULE._PYTHON_EXECUTABLE)
                    if target == "python"
                    else f.bin / target
                )
                executable_link.symlink_to(source)
                self.assert_refused(
                    f.publish(**{keyword: str(executable_link)}),
                    "executable-unavailable",
                )
                self.assertFalse(f.git_log.exists())

    def test_fixed_python_alias_accepts_ubuntu_versioned_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            binary = root / "usr/bin/python3.12"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            alias = binary.with_name("python3")
            alias.symlink_to(binary.name)

            resolved = MODULE._resolve_fixed_executable_alias(str(alias))

            self.assertEqual(resolved, str(binary))
            MODULE._validate_executable(resolved)

    def test_fixed_python_alias_bad_targets_fail_closed(self):
        for case in ("dangling", "non-executable", "directory"):
            with self.subTest(case=case), fixture() as f:
                binary_dir = f.root / "usr/bin"
                binary_dir.mkdir(parents=True)
                alias = binary_dir / "python3"
                target = binary_dir / "python3.12"
                if case == "non-executable":
                    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                elif case == "directory":
                    target.mkdir()
                alias.symlink_to(target.name)
                resolved = MODULE._resolve_fixed_executable_alias(str(alias))

                self.assert_refused(
                    f.publish(python_executable=resolved),
                    "executable-unavailable",
                )
                self.assertFalse(f.git_log.exists())

    def test_hostile_ambient_path_and_auth_environment_are_not_used(self):
        with fixture() as f:
            hostile_bin = f.root / "hostile-bin"
            hostile_bin.mkdir()
            sentinel = f.root / "caller-path-executed"
            for name in ("git", "gh"):
                executable = hostile_bin / name
                executable.write_text(
                    f"#!/bin/sh\n: > {sentinel!s}\nexit 99\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
            forbidden = {
                "GH_TOKEN": "hostile-gh-token",
                "GITHUB_TOKEN": "hostile-github-token",
                "GH_ENTERPRISE_TOKEN": "hostile-enterprise-token",
                "GH_CONFIG_DIR": str(f.root / "hostile-gh-config"),
                "GIT_DIR": str(f.root / "hostile-git-dir"),
                "GIT_WORK_TREE": str(f.root / "hostile-work-tree"),
                "GIT_CONFIG_GLOBAL": str(f.root / "hostile-gitconfig"),
                "GIT_SSH_COMMAND": "hostile-ssh-command",
                "CALLER_SECRET": "must-not-cross-boundary",
            }
            hostile_home = str(f.root / "hostile-home")
            result = f.publish(PATH=str(hostile_bin), HOME=hostile_home, **forbidden)
            self.assertTrue(result.get("ok"), result)
            self.assertFalse(sentinel.exists())
            environments = f.read_subprocess_envs()
            self.assertTrue(environments)
            expected_path = f"{f.bin}:/usr/bin:/bin"
            for child_env in environments:
                self.assertEqual(child_env.get("PATH"), expected_path)
                self.assertEqual(
                    child_env.get("HOME"), pwd.getpwuid(os.geteuid()).pw_dir
                )
                self.assertNotEqual(child_env.get("HOME"), hostile_home)
                self.assertEqual(child_env.get("GIT_TERMINAL_PROMPT"), "0")
                self.assertEqual(child_env.get("GIT_NO_REPLACE_OBJECTS"), "1")
                self.assertEqual(child_env.get("GH_PROMPT_DISABLED"), "1")
                # The caller's global config is replaced, not inherited.
                self.assertEqual(child_env.get("GIT_CONFIG_GLOBAL"), "/dev/null")
                self.assertEqual(child_env.get("GIT_CONFIG_NOSYSTEM"), "1")
                for name in forbidden:
                    if name != "GIT_CONFIG_GLOBAL":
                        self.assertNotIn(name, child_env)
                self.assertNotIn("_AP_WORKSPACE", child_env)
                self.assertNotIn("NIGHTSHIFT_CTL_DB", child_env)
                self.assertNotIn("NIGHTSHIFT_CONTROL_DB", child_env)
                self.assertNotIn("NIGHTSHIFT_RELEASE_ROOT", child_env)
                self.assertNotIn("NIGHTSHIFT_RELEASE_SHA", child_env)

    def test_populated_gitlink_fsmonitor_never_runs_and_is_refused(self):
        with fixture(gitlink=True) as f:
            marker = f.plant_fsmonitor_submodule()
            # The plant is live: an ordinary diff runs the seat's command.
            f.git("diff", "--name-only", "HEAD")
            self.assertTrue(marker.exists(), "the fixture never armed fsmonitor")
            marker.unlink()
            head_before = f.git("rev-parse", "HEAD").stdout.strip()
            index_before = f.ordinary_index_tree()

            self.assert_refused(f.publish(), "populated-gitlink")

            self.assertFalse(marker.exists())
            self.assert_no_publication_mutation(f, head_before, index_before)

    def test_hardened_git_alone_never_runs_a_submodule_fsmonitor(self):
        # Without the refusal, every later diff, add and index refresh still
        # meets the planted repository: first publication, then retry.
        for retry in (False, True):
            with self.subTest(retry=retry), fixture(gitlink=True) as f:
                if retry:
                    f.synthesize_task_commit()
                marker = f.plant_fsmonitor_submodule()
                with mock.patch.object(MODULE, "_refuse_populated_gitlinks"):
                    result = f.publish()
                self.assertTrue(result.get("ok"), result)
                self.assertFalse(marker.exists())

    def test_repository_pre_push_hook_cannot_run(self):
        with fixture() as f:
            sentinel = f.root / "pre-push-ran"
            hook = f.workspace / ".git/hooks/pre-push"
            hook.write_text(
                f"#!/bin/sh\n: > {sentinel!s}\nexit 99\n", encoding="utf-8"
            )
            hook.chmod(0o755)
            result = f.publish()
            self.assertTrue(result.get("ok"), result)
            self.assertFalse(sentinel.exists())

    def test_changed_local_state_after_task_commit_is_refused(self):
        with fixture() as f:
            f.synthesize_task_commit()
            (f.workspace / "work.txt").write_text("changed again\n", encoding="utf-8")
            self.assert_refused(f.publish(), "changed-tree-after-commit")
        with fixture() as f:
            f.synthesize_task_commit()
            f.git("add", "--", "work.txt")
            self.assert_refused(f.publish(), "changed-index-after-commit")

    def test_public_inputs_cannot_inject_argv_identity_destination_or_force(self):
        with fixture() as f:
            sentinel = f.root / "injected"
            result = f.publish(task_id=f"{TASK_ID};touch {sentinel}")
            self.assert_refused(result, "binding-mismatch")
            self.assertFalse(sentinel.exists())
            self.assertFalse(f.git_log.exists())
        with fixture() as f:
            sha = f.synthesize_task_commit()
            result = f.publish(
                GIT_AUTHOR_NAME="spoof",
                GIT_AUTHOR_EMAIL="spoof@example.test",
                GIT_COMMITTER_NAME="spoof",
                GIT_COMMITTER_EMAIL="spoof@example.test",
                NIGHTSHIFT_CTL_GH=str(f.root / "attacker-program"),
            )
            self.assertTrue(result.get("ok"), result)
            record = f.git("show", "-s", "--format=%an%n%ae%n%cn%n%ce", sha).stdout
            self.assertEqual(
                record,
                f"{AUTHOR_NAME}\n{AUTHOR_EMAIL}\n{AUTHOR_NAME}\n{AUTHOR_EMAIL}\n",
            )
            git_calls = [
                json.loads(line)
                for line in f.git_log.read_text(encoding="utf-8").splitlines()
            ]
            pushes = [call for call in git_calls if "push" in call]
            self.assertEqual(
                pushes,
                [[
                    "--no-replace-objects",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "credential.helper=",
                    "-c",
                    f"credential.helper=!{f.bin / 'gh'} auth git-credential",
                    "push",
                    "--recurse-submodules=no",
                    f.gh_url,
                    f"HEAD:refs/heads/{BRANCH}",
                ]],
            )
            self.assertNotIn("--force", pushes[0])
            gh_calls = f.read_gh_state()["calls"]
            creates = [call for call in gh_calls if call[:2] == ["pr", "create"]]
            self.assertEqual(len(creates), 1)
            self.assertEqual(creates[0][creates[0].index("--repo") + 1], REPOSITORY)
            self.assertEqual(creates[0][creates[0].index("--head") + 1], BRANCH)


if __name__ == "__main__":
    unittest.main()
