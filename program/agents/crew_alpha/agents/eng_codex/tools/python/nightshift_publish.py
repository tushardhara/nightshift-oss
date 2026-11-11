"""Publish one bound crew_alpha.eng_codex task without broader Git access."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import pwd
import re
import shlex
import sqlite3
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from omnigent_client import tool


_AGENT = "eng_codex"
_CREW = "crew_alpha"
_HOLDER = "crew_alpha/eng_codex"
_IDENTITY_NAME = "crew_alpha.eng_codex"
_IDENTITY_EMAIL = "crew_alpha.eng_codex@nightshift.invalid"
_TRAILER = "Co-authored-by: omnigent <noreply@omnigent.ai>"
_GIT_EXECUTABLE = "/usr/bin/git"
_GH_EXECUTABLE = "/usr/bin/gh"
_SYSTEM_PYTHON_ALIAS = "/usr/bin/python3"
_CONTROL_DB_ENV = "NIGHTSHIFT_CTL_DB"


def _resolve_fixed_executable_alias(path: str) -> str:
    """Resolve one reviewed system alias; callers still validate the target."""
    return os.path.realpath(path)


# Ubuntu 24.04 packages /usr/bin/python3 as a root-managed symlink to the
# versioned regular binary. Pin the canonical target rather than accepting an
# arbitrary symlink at the subprocess boundary.
_PYTHON_EXECUTABLE = _resolve_fixed_executable_alias(_SYSTEM_PYTHON_ALIAS)
_SYSTEM_PATH = "/usr/bin:/bin"
_TOOL_RELATIVE_PATH = (
    "program/agents/crew_alpha/agents/eng_codex/tools/python/"
    "nightshift_publish.py"
)
_CONTROLLER_RELATIVE_PATH = "bin/nightshift-ctl"
_LOCAL_MUTATION_RUNWAY_SECONDS = 5.0
_REMOTE_MUTATION_RUNWAY_SECONDS = 65.0
_IN_FLIGHT = frozenset(("building", "review", "fixing"))
_PINNED_ORIGIN_FIELDS = frozenset(("origin_fetch_url", "origin_push_url"))
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_REPOSITORY_RE = re.compile(
    r"\A[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?/"
    r"[A-Za-z0-9_.-]+\Z"
)
_CLOSING_RE = re.compile(
    r"\b(clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))\b"
    r"\s*:?\s+([\w./-]+)?#(\d+)",
    re.IGNORECASE,
)
_REQUIRED_CONTRACT_FIELDS = frozenset(
    (
        "task_id",
        "issue",
        "repository",
        "default_branch",
        "allowed_paths",
        "commit_subject",
        "pr_title",
        "pr_body",
    )
)
_INTERNAL_ENV_NAMES = frozenset(
    (
        "GIT_INDEX_FILE",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    )
)
# The seat can write any `<gitlink>/.git/config`, and git recurses into a
# populated submodule to judge dirtiness -- running its `core.fsmonitor`
# here, outside the sandbox.  Every Git call carries these flags, the
# environment drops global/system config, and worktree diffs never recurse.
_GIT_HARDENING = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
_IGNORE_SUBMODULES = "--ignore-submodules=all"
_GITLINK_MODE = "160000"


class _Refusal(Exception):
    """An expected fail-closed result."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _result(ok: bool, **fields: Any) -> str:
    return json.dumps({"ok": ok, **fields}, sort_keys=True, separators=(",", ":"))


def _refuse(reason: str) -> None:
    raise _Refusal(reason)


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    try:
        trusted_home = pwd.getpwuid(os.geteuid()).pw_dir
    except (KeyError, OSError):
        _refuse("subprocess-failed")
    if not isinstance(trusted_home, str) or not os.path.isabs(trusted_home):
        _refuse("subprocess-failed")
    env = {
        "HOME": trusted_home,
        "PATH": _SYSTEM_PATH,
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GCM_INTERACTIVE": "Never",
        "GH_PROMPT_DISABLED": "1",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
    }
    if extra:
        if not set(extra).issubset(_INTERNAL_ENV_NAMES):
            _refuse("subprocess-failed")
        env.update(extra)
    return env


def _validate_executable(path: str) -> None:
    if (
        not os.path.isabs(path)
        or os.path.realpath(path) != path
        or not _regular_nonsymlink(path)
        or not os.access(path, os.X_OK)
    ):
        _refuse("executable-unavailable")


def _run(
    argv: list[str],
    workspace: str,
    *,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    stdin_fd: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    if stdin is not None and stdin_fd is not None:
        _refuse("subprocess-failed")
    try:
        kwargs: dict[str, Any] = {
            "cwd": workspace,
            "env": env if env is not None else _subprocess_env(),
            "text": True,
            "capture_output": True,
            "timeout": 60,
            "check": False,
        }
        if stdin_fd is None:
            kwargs["input"] = stdin
        else:
            kwargs["stdin"] = stdin_fd
        completed = subprocess.run(argv, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        _refuse("subprocess-failed")
    if check and completed.returncode != 0:
        _refuse("subprocess-failed")
    return completed


def _git_argv(args: list[str]) -> list[str]:
    return [_GIT_EXECUTABLE, "--no-replace-objects", *_GIT_HARDENING, *args]


def _git_credential_flags() -> list[str]:
    # Global config is dropped, so the network verbs authenticate through the
    # pinned gh: the same credential this tool's own gh calls already use.
    helper = f"!{shlex.quote(_GH_EXECUTABLE)} auth git-credential"
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def _git(
    workspace: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> str:
    return _run(
        _git_argv(args),
        workspace,
        env=env,
        stdin=stdin,
        check=True,
    ).stdout.strip()


def _git_quiet(
    workspace: str, args: list[str], *, env: dict[str, str] | None = None
) -> bool:
    completed = _run(
        _git_argv(args),
        workspace,
        env=env,
        check=False,
    )
    if completed.returncode not in (0, 1):
        _refuse("subprocess-failed")
    return completed.returncode == 0


def _regular_nonsymlink(path: str) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


def _directory_nonsymlink(path: str) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((os.path.realpath(child), parent)) == parent
    except (OSError, ValueError):
        return False


def _lock_path(db_dir: str, task_id: str) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return os.path.join(db_dir, f"nightshift-publish-{digest}.lock")


@contextmanager
def _publication_lock(db_dir: str, task_id: str):
    path = _lock_path(db_dir, task_id)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        _refuse("publication-lock-unavailable")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _refuse("publication-lock-unavailable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                _refuse("publication-busy")
            _refuse("publication-lock-unavailable")
        yield
    finally:
        os.close(fd)


def _paths_overlap(first: str, second: str) -> bool:
    try:
        common = os.path.commonpath((first, second))
    except ValueError:
        return True
    return common in (first, second)


def _release_git_text(release_root: str, args: list[str]) -> str:
    completed = _run(
        _git_argv(["-C", release_root, *args]),
        release_root,
        check=False,
    )
    if completed.returncode != 0:
        _refuse("release-attestation-failed")
    return completed.stdout.strip()


def _release_git_blob(release_root: str, release_sha: str, relative: str) -> bytes:
    try:
        completed = subprocess.run(
            _git_argv(
                ["-C", release_root, "cat-file", "blob", f"{release_sha}:{relative}"]
            ),
            cwd=release_root,
            env=_subprocess_env(),
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _refuse("release-attestation-failed")
    if completed.returncode != 0:
        _refuse("release-attestation-failed")
    return completed.stdout


def _read_fd(fd: int) -> bytes:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        _refuse("release-attestation-failed")
    return b"".join(chunks)


@contextmanager
def _trusted_controller(
    release_root_raw: str,
    release_sha: str,
    workspace: str,
    db_dir: str,
):
    if not isinstance(release_sha, str) or not _SHA_RE.fullmatch(release_sha):
        _refuse("invalid-release-sha")
    if not isinstance(release_root_raw, str) or not release_root_raw:
        _refuse("invalid-release-root")
    release_root_abs = os.path.abspath(release_root_raw)
    release_root = os.path.realpath(release_root_raw)
    if (
        not os.path.isabs(release_root_raw)
        or release_root_raw != release_root_abs
        or release_root_abs != release_root
        or not _directory_nonsymlink(release_root)
        or not _directory_nonsymlink(os.path.join(release_root, ".git"))
    ):
        _refuse("invalid-release-root")
    if _paths_overlap(release_root, workspace) or _paths_overlap(
        release_root, db_dir
    ):
        _refuse("release-root-overlap")
    top_level = _release_git_text(release_root, ["rev-parse", "--show-toplevel"])
    head = _release_git_text(release_root, ["rev-parse", "HEAD"])
    if top_level != release_root or head != release_sha:
        _refuse("release-head-mismatch")

    tool_path = os.path.abspath(__file__)
    if not _regular_nonsymlink(tool_path):
        _refuse("release-tool-mismatch")
    try:
        running_tool = Path(tool_path).read_bytes()
    except OSError:
        _refuse("release-tool-mismatch")
    if running_tool != _release_git_blob(
        release_root, release_sha, _TOOL_RELATIVE_PATH
    ):
        _refuse("release-tool-mismatch")

    controller_blob = _release_git_blob(
        release_root, release_sha, _CONTROLLER_RELATIVE_PATH
    )
    controller_path = os.path.join(release_root, _CONTROLLER_RELATIVE_PATH)
    if os.path.realpath(controller_path) != controller_path:
        _refuse("release-controller-mismatch")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        controller_fd = os.open(controller_path, flags)
    except OSError:
        _refuse("release-controller-mismatch")
    try:
        controller_stat = os.fstat(controller_fd)
        if (
            not stat.S_ISREG(controller_stat.st_mode)
            or not controller_stat.st_mode & 0o111
            or _read_fd(controller_fd) != controller_blob
        ):
            _refuse("release-controller-mismatch")
        yield controller_fd
    finally:
        os.close(controller_fd)


def _run_controller(
    controller_fd: int,
    workspace: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        os.lseek(controller_fd, 0, os.SEEK_SET)
    except OSError:
        _refuse("controller-fd-unavailable")
    return _run(
        [_PYTHON_EXECUTABLE, "-I", "-", *args],
        workspace,
        env=env,
        stdin_fd=controller_fd,
    )


def _validate_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _refuse("invalid-allowed-path")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        _refuse("invalid-allowed-path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _refuse("invalid-allowed-path")
    if parts[0] == ".codex-tmp":
        _refuse("invalid-allowed-path")
    if any(part.lower() == ".git" for part in parts):
        _refuse("invalid-allowed-path")
    if os.path.normpath(value) != value:
        _refuse("invalid-allowed-path")
    return value


def _load_rows(db_path: str, task_id: str, slot_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if not os.path.isabs(db_path) or not _regular_nonsymlink(db_path):
        _refuse("control-db-unavailable")
    uri = "file:{}?mode=ro".format(quote(db_path, safe="/"))
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        slot = connection.execute(
            "SELECT * FROM worker_slots WHERE id=?", (slot_id,)
        ).fetchone()
        task = connection.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    except sqlite3.Error:
        _refuse("control-db-unavailable")
    finally:
        if "connection" in locals():
            connection.close()
    if slot is None or task is None:
        _refuse("binding-mismatch")
    return dict(slot), dict(task)


_TASK_BINDING_FIELDS = (
    "id",
    "crew",
    "agent",
    "title",
    "issue",
    "base_sha",
    "spec_ref",
    "worktree",
    "branch",
    "pr_url",
)


def _validate_binding(
    slot: dict[str, Any],
    task: dict[str, Any],
    task_id: str,
    workspace: str,
    runway_seconds: float,
) -> None:
    now = time.time()
    expires_at = slot.get("expires_at")
    if (
        slot.get("state") != "active"
        or slot.get("dispatched_at") is None
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(expires_at)
        or expires_at <= now + runway_seconds
        or slot.get("task_id") != task_id
        or slot.get("bound_agent") != _AGENT
        or slot.get("bound_title") != task.get("title")
        or slot.get("holder") != _HOLDER
        or slot.get("crew") not in (None, _CREW)
    ):
        _refuse("slot-not-bound")
    if (
        task.get("id") != task_id
        or task.get("crew") != _CREW
        or task.get("agent") != _AGENT
        or task.get("state") not in _IN_FLIGHT
    ):
        _refuse("task-not-in-flight")
    task_workspace = task.get("worktree")
    if not isinstance(task_workspace, str) or os.path.realpath(task_workspace) != workspace:
        _refuse("workspace-binding-mismatch")


def _revalidate_authorization(
    db_path: str,
    task_id: str,
    slot_id: int,
    workspace: str,
    bound_task: dict[str, Any],
    publication: dict[str, Any],
    runway_seconds: float,
) -> dict[str, Any]:
    slot, task = _load_rows(db_path, task_id, slot_id)
    _validate_binding(slot, task, task_id, workspace, runway_seconds)
    if any(task.get(field) != bound_task.get(field)
           for field in _TASK_BINDING_FIELDS):
        _refuse("task-binding-changed")
    contract, _ = _load_contract(db_path, task.get("spec_ref"))
    current_publication = _validate_contract(contract, task, task_id)
    if (
        set(publication) != set(current_publication) | _PINNED_ORIGIN_FIELDS
        or any(
            publication.get(field) != value
            for field, value in current_publication.items()
        )
    ):
        _refuse("contract-binding-changed")
    return task


def _load_contract(db_path: str, spec_ref: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(spec_ref, str) or not spec_ref or "\x00" in spec_ref:
        _refuse("invalid-contract-ref")
    db_dir = os.path.realpath(os.path.dirname(db_path))
    contracts_dir = os.path.realpath(os.path.join(db_dir, "contracts"))
    if os.path.isabs(spec_ref):
        candidate = os.path.normpath(spec_ref)
    else:
        parts = Path(spec_ref).parts
        if parts and parts[0] == "contracts":
            candidate = os.path.join(db_dir, spec_ref)
        else:
            candidate = os.path.join(contracts_dir, spec_ref)
    contract_path = os.path.realpath(candidate)
    if (
        not _inside(contract_path, contracts_dir)
        or contract_path == contracts_dir
        or not _regular_nonsymlink(candidate)
        or os.path.realpath(candidate) != os.path.abspath(candidate)
    ):
        _refuse("invalid-contract-ref")
    try:
        with open(candidate, "r", encoding="utf-8") as handle:
            contract = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _refuse("invalid-contract")
    if not isinstance(contract, dict):
        _refuse("invalid-contract")
    if contract.get("version") != 1 or not _REQUIRED_CONTRACT_FIELDS.issubset(contract):
        _refuse("invalid-contract")
    return contract, db_dir


def _normalize_repository(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, str) or not _REPOSITORY_RE.fullmatch(value):
        _refuse("invalid-repository")
    owner, repository = value.split("/", 1)
    if repository.lower().endswith(".git"):
        _refuse("invalid-repository")
    return value, owner, value.lower()


def _normalize_origin_url(value: str) -> tuple[str, str]:
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:") :]
        scheme = "ssh"
    else:
        parsed = urlparse(value)
        if (
            parsed.scheme not in ("https", "ssh")
            or (parsed.hostname or "").lower() != "github.com"
            or parsed.password is not None
            or parsed.port is not None
        ):
            _refuse("origin-mismatch")
        if (
            (parsed.scheme == "https" and parsed.username is not None)
            or (parsed.scheme == "ssh" and parsed.username != "git")
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
        ):
            _refuse("origin-mismatch")
        path = parsed.path[1:]
        scheme = parsed.scheme
    if path.endswith(".git"):
        path = path[:-4]
    if not _REPOSITORY_RE.fullmatch(path):
        _refuse("origin-mismatch")
    if scheme == "ssh":
        normalized = f"ssh://git@github.com/{path}.git"
    else:
        normalized = f"https://github.com/{path}.git"
    return normalized, path.lower()


def _validate_contract(contract: dict[str, Any], task: dict[str, Any], task_id: str) -> dict[str, Any]:
    if contract.get("task_id") != task_id:
        _refuse("contract-task-mismatch")
    issue = contract.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
        _refuse("invalid-contract")
    if task.get("issue") is not None and task.get("issue") != issue:
        _refuse("contract-issue-mismatch")
    repository, owner, repository_key = _normalize_repository(contract.get("repository"))
    default_branch = contract.get("default_branch")
    branch = task.get("branch")
    if not isinstance(default_branch, str) or not default_branch or "\x00" in default_branch:
        _refuse("invalid-default-branch")
    if not isinstance(branch, str) or not branch or "\x00" in branch:
        _refuse("invalid-task-branch")
    if branch == default_branch:
        _refuse("default-branch-refused")
    base_sha = task.get("base_sha")
    if not isinstance(base_sha, str) or not _SHA_RE.fullmatch(base_sha):
        _refuse("invalid-base-sha")
    allowed = contract.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed:
        _refuse("invalid-allowed-path")
    allowed_paths = tuple(_validate_relative_path(path) for path in allowed)
    if len(set(allowed_paths)) != len(allowed_paths):
        _refuse("invalid-allowed-path")
    commit_subject = contract.get("commit_subject")
    pr_title = contract.get("pr_title")
    pr_body = contract.get("pr_body")
    if (
        not isinstance(commit_subject, str)
        or not commit_subject.strip()
        or any(char in commit_subject for char in ("\r", "\n", "\x00"))
        or not isinstance(pr_title, str)
        or not pr_title.strip()
        or any(char in pr_title for char in ("\r", "\n", "\x00"))
        or not isinstance(pr_body, str)
        or "\x00" in pr_body
    ):
        _refuse("invalid-publication-prose")
    if _CLOSING_RE.search(pr_body) or not re.search(
        rf"\bRefs\s+#{issue}\b", pr_body, re.IGNORECASE
    ):
        _refuse("invalid-pr-body")
    return {
        "repository": repository,
        "repository_key": repository_key,
        "owner": owner,
        "default_branch": default_branch,
        "branch": branch,
        "base_sha": base_sha,
        "allowed_paths": allowed_paths,
        "commit_subject": commit_subject,
        "message": f"{commit_subject}\n\n{_TRAILER}\n",
        "pr_title": pr_title,
        "pr_body": pr_body,
        "task_pr_url": task.get("pr_url"),
    }


def _validate_ref(workspace: str, value: str, reason: str) -> None:
    completed = _run(
        _git_argv(["check-ref-format", "--branch", value]),
        workspace,
        check=False,
    )
    if completed.returncode != 0:
        _refuse(reason)


def _nul_paths(output: str) -> set[str]:
    paths = output.split("\x00")
    if paths and paths[-1] == "":
        paths.pop()
    if any(not path for path in paths):
        _refuse("invalid-changed-path")
    return set(paths)


def _gitlinks(output: str) -> set[str]:
    links = set()
    for record in _nul_paths(output):
        meta, separator, path = record.partition("\t")
        if not separator or not meta:
            _refuse("invalid-changed-path")
        if meta.split()[0] == _GITLINK_MODE:
            links.add(path)
    return links


def _refuse_populated_gitlinks(workspace: str, treeishes: tuple[str, ...]) -> None:
    """Refuse before any worktree diff when a gitlink holds a `.git` entry.

    A populated submodule is a repository the seat wrote, config included.
    The hardened flags already stop Git from running its hooks, but a nested
    repository has no business in a publication, so it is refused outright.
    """
    links = _gitlinks(_git(workspace, ["ls-files", "--stage", "-z"]))
    for treeish in treeishes:
        links |= _gitlinks(
            _git(workspace, ["ls-tree", "-r", "-z", "--full-tree", treeish, "--"])
        )
    for path in links:
        if os.path.lexists(os.path.join(workspace, path, ".git")):
            _refuse("populated-gitlink")


def _untracked_paths(
    workspace: str, *, env: dict[str, str] | None = None
) -> set[str]:
    return _nul_paths(
        _git(
            workspace,
            ["ls-files", "--others", "--exclude-standard", "-z"],
            env=env,
        )
    )


def _working_paths(workspace: str, reference: str) -> set[str]:
    tracked = _nul_paths(
        _git(
            workspace,
            ["diff", _IGNORE_SUBMODULES, "--name-only", "-z", reference, "--"],
        )
    )
    return tracked | _untracked_paths(workspace)


def _deleted_worktree_paths(workspace: str, reference: str) -> set[str]:
    return _nul_paths(
        _git(
            workspace,
            [
                "diff",
                _IGNORE_SUBMODULES,
                "--no-renames",
                "--diff-filter=D",
                "--name-only",
                "-z",
                reference,
                "--",
            ],
        )
    )


def _deleted_commit_paths(workspace: str, base_sha: str, sha: str) -> set[str]:
    return _nul_paths(
        _git(
            workspace,
            [
                "diff-tree",
                "--no-renames",
                "--diff-filter=D",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                base_sha,
                sha,
                "--",
            ],
        )
    )


def _validate_changed_paths(paths: set[str], allowed: tuple[str, ...], workspace: str) -> None:
    if not paths:
        _refuse("no-changes")
    normalized = {_validate_relative_path(path) for path in paths}
    if normalized != paths or not normalized.issubset(set(allowed)):
        _refuse("path-outside-allowlist")
    for path in normalized:
        absolute = os.path.join(workspace, path)
        if os.path.lexists(absolute) and not _inside(absolute, workspace):
            _refuse("workspace-path-escape")


def _commit_record(workspace: str, sha: str) -> dict[str, Any]:
    raw = _run(_git_argv(["cat-file", "commit", sha]), workspace, check=True).stdout
    headers, separator, message = raw.partition("\n\n")
    if not separator:
        _refuse("unrecognized-task-commit")
    record: dict[str, Any] = {"parents": [], "message": message}
    for line in headers.splitlines():
        if line.startswith("tree "):
            record["tree"] = line[5:]
        elif line.startswith("parent "):
            record["parents"].append(line[7:])
        elif line.startswith("author "):
            record["author"] = line[7:]
        elif line.startswith("committer "):
            record["committer"] = line[10:]
    return record


def _identity_matches(value: Any) -> bool:
    prefix = f"{_IDENTITY_NAME} <{_IDENTITY_EMAIL}> "
    return isinstance(value, str) and value.startswith(prefix)


def _validate_existing_commit(
    workspace: str, sha: str, publication: dict[str, Any], db_dir: str
) -> set[str]:
    record = _commit_record(workspace, sha)
    if (
        record.get("parents") != [publication["base_sha"]]
        or record.get("message") != publication["message"]
        or not _identity_matches(record.get("author"))
        or not _identity_matches(record.get("committer"))
        or not isinstance(record.get("tree"), str)
        or not _SHA_RE.fullmatch(record["tree"])
    ):
        _refuse("unrecognized-task-commit")
    if _deleted_commit_paths(workspace, publication["base_sha"], sha):
        _refuse("deletion-refused")
    changed = _nul_paths(
        _git(
            workspace,
            [
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                publication["base_sha"],
                sha,
                "--",
            ],
        )
    )
    _validate_changed_paths(changed, publication["allowed_paths"], workspace)
    index_fd = -1
    index_path = ""
    try:
        index_fd, index_path = tempfile.mkstemp(
            prefix="nightshift-publish-retry-index-", dir=db_dir
        )
        os.close(index_fd)
        index_fd = -1
        index_env = _subprocess_env({"GIT_INDEX_FILE": index_path})
        _git(workspace, ["read-tree", sha], env=index_env)
        refresh = _run(
            _git_argv(["update-index", "--ignore-submodules", "--refresh", "--"]),
            workspace,
            env=index_env,
            check=False,
        )
        if refresh.returncode not in (0, 1):
            _refuse("subprocess-failed")
        if not _git_quiet(
            workspace,
            ["diff-files", _IGNORE_SUBMODULES, "--quiet", "--"],
            env=index_env,
        ):
            _refuse("changed-tree-after-commit")
        if _untracked_paths(workspace, env=index_env):
            _refuse("changed-tree-after-commit")
    finally:
        if index_fd >= 0:
            os.close(index_fd)
        if index_path:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass
    if not _git_quiet(
        workspace,
        ["diff", "--cached", "--quiet", publication["base_sha"], "--"],
    ):
        _refuse("changed-index-after-commit")
    return changed


def _current_origin_urls(workspace: str) -> tuple[str, str]:
    fetch_value = _git(workspace, ["remote", "get-url", "origin"])
    push_value = _git(workspace, ["remote", "get-url", "--push", "origin"])
    fetch_url, _ = _normalize_origin_url(fetch_value)
    push_url, _ = _normalize_origin_url(push_value)
    return fetch_url, push_url


def _refuse_effective_url_rewrites(workspace: str) -> None:
    completed = _run(
        _git_argv(["config", "--includes", "--null", "--name-only", "--list"]),
        workspace,
        check=False,
    )
    if completed.returncode != 0:
        _refuse("subprocess-failed")
    for name in completed.stdout.split("\x00"):
        normalized = name.casefold()
        if normalized.startswith("url.") and normalized.endswith(
            (".insteadof", ".pushinsteadof")
        ):
            _refuse("url-rewrite-config")


def _revalidate_pinned_origins(
    workspace: str, publication: dict[str, Any]
) -> None:
    _refuse_effective_url_rewrites(workspace)
    if _current_origin_urls(workspace) != (
        publication.get("origin_fetch_url"),
        publication.get("origin_push_url"),
    ):
        _refuse("origin-mismatch")
    # Repeat after the origin comparison so a rewrite already present at this
    # publication boundary is refused without exposing its configured value.
    _refuse_effective_url_rewrites(workspace)


def _remote_branch_sha(
    workspace: str, publication: dict[str, Any]
) -> str | None:
    _revalidate_pinned_origins(workspace, publication)
    ref = f"refs/heads/{publication['branch']}"
    output = _git(
        workspace,
        [
            *_git_credential_flags(),
            "ls-remote",
            "--refs",
            publication["origin_fetch_url"],
            ref,
        ],
    )
    if not output:
        return None
    lines = output.splitlines()
    if len(lines) != 1:
        _refuse("remote-branch-mismatch")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != ref or not _SHA_RE.fullmatch(fields[0]):
        _refuse("remote-branch-mismatch")
    return fields[0]


def _inspect_prs(workspace: str, publication: dict[str, Any]) -> list[dict[str, Any]]:
    completed = _run(
        [
            _GH_EXECUTABLE,
            "pr",
            "list",
            "--repo",
            publication["repository"],
            "--state",
            "all",
            "--head",
            publication["branch"],
            "--limit",
            "100",
            "--json",
            (
                "url,state,isDraft,baseRefName,headRefName,headRefOid,"
                "headRepository,headRepositoryOwner,title,body"
            ),
        ],
        workspace,
    )
    try:
        rows = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _refuse("pr-inspection-failed")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _refuse("pr-inspection-failed")
    return rows


def _is_canonical_pr_url(value: Any, repository_key: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    parts = parsed.path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or len(parts) != 5
        or parts[0] != ""
        or parts[3] != "pull"
        or re.fullmatch(r"[1-9][0-9]*", parts[4]) is None
    ):
        return False
    try:
        url_repository_key = _normalize_repository(f"{parts[1]}/{parts[2]}")[2]
    except _Refusal:
        return False
    return url_repository_key == repository_key


def _existing_pr_url(
    rows: list[dict[str, Any]], publication: dict[str, Any], commit_sha: str | None
) -> str | None:
    if not rows:
        if publication["task_pr_url"] is not None:
            _refuse("pr-mismatch")
        return None
    if len(rows) != 1:
        _refuse("multiple-prs")
    row = rows[0]
    if row.get("state") != "OPEN":
        _refuse("closed-pr")
    if row.get("isDraft") is not True:
        _refuse("non-draft-pr")
    head_repository = row.get("headRepository")
    head_repository_owner = row.get("headRepositoryOwner")
    head_repository_name = (
        head_repository.get("name")
        if isinstance(head_repository, dict)
        else None
    )
    head_repository_owner_login = (
        head_repository_owner.get("login")
        if isinstance(head_repository_owner, dict)
        else None
    )
    head_repository_key = None
    if (
        isinstance(head_repository_name, str)
        and isinstance(head_repository_owner_login, str)
    ):
        try:
            head_repository_key = _normalize_repository(
                f"{head_repository_owner_login}/{head_repository_name}"
            )[2]
        except _Refusal:
            pass
    url = row.get("url")
    if (
        commit_sha is None
        or row.get("baseRefName") != publication["default_branch"]
        or row.get("headRefName") != publication["branch"]
        or row.get("headRefOid") != commit_sha
        or head_repository_key != publication["repository_key"]
        or row.get("title") != publication["pr_title"]
        or row.get("body") != publication["pr_body"]
        or not _is_canonical_pr_url(url, publication["repository_key"])
        or (
            publication["task_pr_url"] is not None
            and url != publication["task_pr_url"]
        )
    ):
        _refuse("pr-mismatch")
    return url


def _validate_workspace(
    workspace_raw: str, task: dict[str, Any], publication: dict[str, Any]
) -> dict[str, Any]:
    workspace_abs = os.path.abspath(workspace_raw)
    workspace = os.path.realpath(workspace_raw)
    if (
        workspace_abs != workspace
        or not _directory_nonsymlink(workspace_abs)
        or os.path.realpath(task["worktree"]) != workspace
    ):
        _refuse("workspace-not-private")
    git_dir = os.path.join(workspace, ".git")
    if not _directory_nonsymlink(git_dir):
        _refuse("git-dir-not-private")
    absolute_git_dir = _git(workspace, ["rev-parse", "--absolute-git-dir"])
    if os.path.realpath(absolute_git_dir) != git_dir or absolute_git_dir != git_dir:
        _refuse("git-dir-not-private")
    marker_path = os.path.join(git_dir, "nightshift-workspace.json")
    if not _regular_nonsymlink(marker_path):
        _refuse("workspace-marker-invalid")
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _refuse("workspace-marker-invalid")
    if (
        not isinstance(marker, dict)
        or marker.get("version") != 1
        or marker.get("agent") != _AGENT
        or os.path.realpath(marker.get("path", "")) != workspace
        or marker.get("base_sha") != publication["base_sha"]
    ):
        _refuse("workspace-marker-invalid")
    branch = _git(workspace, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if branch != publication["branch"]:
        _refuse("branch-mismatch")
    _validate_ref(workspace, branch, "invalid-task-branch")
    _validate_ref(workspace, publication["default_branch"], "invalid-default-branch")
    _refuse_effective_url_rewrites(workspace)
    origin_fetch, origin_push = _current_origin_urls(workspace)
    if (
        _normalize_origin_url(origin_fetch)[1] != publication["repository_key"]
        or _normalize_origin_url(origin_push)[1] != publication["repository_key"]
    ):
        _refuse("origin-mismatch")
    pinned_publication = {
        **publication,
        "origin_fetch_url": origin_fetch,
        "origin_push_url": origin_push,
    }
    remote_base = _git(
        workspace,
        ["rev-parse", "--verify", f"refs/remotes/origin/{publication['default_branch']}^{{commit}}"],
    )
    if remote_base != publication["base_sha"]:
        _refuse("stale-base")
    default_ref = f"refs/heads/{publication['default_branch']}"
    _revalidate_pinned_origins(workspace, pinned_publication)
    live_default = _git(
        workspace,
        [
            *_git_credential_flags(),
            "ls-remote",
            "--refs",
            pinned_publication["origin_fetch_url"],
            default_ref,
        ],
    )
    live_lines = live_default.splitlines()
    if len(live_lines) != 1:
        _refuse("stale-base")
    live_fields = live_lines[0].split()
    if (
        len(live_fields) != 2
        or live_fields[1] != default_ref
        or not _SHA_RE.fullmatch(live_fields[0])
        or live_fields[0] != publication["base_sha"]
    ):
        _refuse("stale-base")
    return pinned_publication


def _github_preflight(workspace: str, publication: dict[str, Any]) -> None:
    completed = _run(
        [
            _GH_EXECUTABLE,
            "api",
            f"repos/{publication['repository']}",
            "--jq",
            ".full_name",
        ],
        workspace,
    )
    if completed.stdout.strip().lower() != publication["repository_key"]:
        _refuse("github-repository-mismatch")


def _make_commit(
    workspace: str,
    db_path: str,
    db_dir: str,
    task_id: str,
    slot_id: int,
    bound_task: dict[str, Any],
    controller_fd: int,
    publication: dict[str, Any],
    changed_paths: set[str],
) -> str:
    index_fd = -1
    index_path = ""
    try:
        index_fd, index_path = tempfile.mkstemp(prefix="nightshift-publish-index-", dir=db_dir)
        os.close(index_fd)
        index_fd = -1
        index_env = _subprocess_env({"GIT_INDEX_FILE": index_path})
        _git(workspace, ["read-tree", publication["base_sha"]], env=index_env)
        _git(
            workspace,
            ["add", "-A", "--", *publication["allowed_paths"]],
            env=index_env,
        )
        staged = _nul_paths(
            _git(
                workspace,
                ["diff", "--cached", "--name-only", "-z", publication["base_sha"], "--"],
                env=index_env,
            )
        )
        if staged != changed_paths:
            _refuse("staged-path-mismatch")
        guard = _run_controller(
            controller_fd,
            workspace,
            [
                "guard-commit",
                "--repo",
                workspace,
                "--ref",
                publication["base_sha"],
            ],
            env=index_env,
        )
        try:
            receipt = json.loads(guard.stdout)
        except json.JSONDecodeError:
            _refuse("guard-commit-invalid")
        if (
            not isinstance(receipt, dict)
            or receipt.get("ok") is not True
            or receipt.get("guard") != "staged-deletions"
            or receipt.get("ref_sha") != publication["base_sha"]
            or receipt.get("deletions") != []
        ):
            _refuse("guard-commit-invalid")
        _revalidate_authorization(
            db_path,
            task_id,
            slot_id,
            workspace,
            bound_task,
            publication,
            _LOCAL_MUTATION_RUNWAY_SECONDS,
        )
        tree = _git(workspace, ["write-tree"], env=index_env)
        identity_env = dict(index_env)
        identity_env.update(
            {
                "GIT_AUTHOR_NAME": _IDENTITY_NAME,
                "GIT_AUTHOR_EMAIL": _IDENTITY_EMAIL,
                "GIT_COMMITTER_NAME": _IDENTITY_NAME,
                "GIT_COMMITTER_EMAIL": _IDENTITY_EMAIL,
            }
        )
        commit_sha = _git(
            workspace,
            ["commit-tree", tree, "-p", publication["base_sha"]],
            env=identity_env,
            stdin=publication["message"],
        )
        if not _SHA_RE.fullmatch(commit_sha):
            _refuse("commit-creation-failed")
        _revalidate_authorization(
            db_path,
            task_id,
            slot_id,
            workspace,
            bound_task,
            publication,
            _LOCAL_MUTATION_RUNWAY_SECONDS,
        )
        _git(
            workspace,
            [
                "update-ref",
                f"refs/heads/{publication['branch']}",
                commit_sha,
                publication["base_sha"],
            ],
        )
        return commit_sha
    finally:
        if index_fd >= 0:
            os.close(index_fd)
        if index_path:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass


def _push(workspace: str, publication: dict[str, Any]) -> None:
    _revalidate_pinned_origins(workspace, publication)
    _git(
        workspace,
        [
            *_git_credential_flags(),
            "push",
            "--recurse-submodules=no",
            publication["origin_push_url"],
            f"HEAD:refs/heads/{publication['branch']}",
        ],
    )


def _create_pr(
    workspace: str,
    db_dir: str,
    controller_fd: int,
    publication: dict[str, Any],
) -> None:
    body_fd = -1
    body_path = ""
    try:
        body_fd, body_path = tempfile.mkstemp(
            prefix="nightshift-publish-pr-body-", suffix=".md", dir=db_dir
        )
        with os.fdopen(body_fd, "w", encoding="utf-8") as handle:
            body_fd = -1
            handle.write(publication["pr_body"])
        _run_controller(
            controller_fd,
            workspace,
            [
                "pr-create",
                "--kind",
                "task",
                "--",
                "--repo",
                publication["repository"],
                "--head",
                publication["branch"],
                "--base",
                publication["default_branch"],
                "--draft",
                "--title",
                publication["pr_title"],
                "--body-file",
                body_path,
            ],
        )
    finally:
        if body_fd >= 0:
            os.close(body_fd)
        if body_path:
            try:
                os.unlink(body_path)
            except FileNotFoundError:
                pass


def _publish_locked(
    workspace_raw: str,
    workspace: str,
    db_path: str,
    db_dir: str,
    task_id: str,
    slot_id: int,
    task: dict[str, Any],
    publication: dict[str, Any],
    controller_fd: int,
) -> str:
    publication = _validate_workspace(workspace_raw, task, publication)

    head_sha = _git(workspace, ["rev-parse", "--verify", "HEAD^{commit}"])
    if not _SHA_RE.fullmatch(head_sha):
        _refuse("invalid-head")
    _refuse_populated_gitlinks(workspace, (head_sha, publication["base_sha"]))
    if head_sha == publication["base_sha"]:
        local_commit = None
        if not _git_quiet(
            workspace, ["diff", "--cached", "--quiet", "HEAD", "--"]
        ):
            _refuse("dirty-index")
        changed_paths = _working_paths(workspace, "HEAD")
        if _deleted_worktree_paths(workspace, "HEAD"):
            _refuse("deletion-refused")
        _validate_changed_paths(
            changed_paths, publication["allowed_paths"], workspace
        )
    else:
        local_commit = head_sha
        changed_paths = _validate_existing_commit(
            workspace, head_sha, publication, db_dir
        )

    _github_preflight(workspace, publication)
    remote_sha = _remote_branch_sha(workspace, publication)
    prs = _inspect_prs(workspace, publication)
    existing_url = _existing_pr_url(prs, publication, local_commit)
    if existing_url is not None:
        if remote_sha != local_commit:
            _refuse("remote-branch-mismatch")
        _revalidate_authorization(
            db_path,
            task_id,
            slot_id,
            workspace,
            task,
            publication,
            0.0,
        )
        return _result(
            True, state="existing-draft-pr", commit=local_commit, url=existing_url
        )

    if local_commit is None:
        if remote_sha not in (None, publication["base_sha"]):
            _refuse("remote-branch-mismatch")
    elif remote_sha not in (None, publication["base_sha"], local_commit):
        _refuse("remote-branch-mismatch")

    # Every contract, binding, workspace, Git, remote, auth, and PR
    # preflight above is complete before this first Git metadata write.
    if local_commit is None:
        local_commit = _make_commit(
            workspace,
            db_path,
            db_dir,
            task_id,
            slot_id,
            task,
            controller_fd,
            publication,
            changed_paths,
        )
    if remote_sha != local_commit:
        _revalidate_authorization(
            db_path,
            task_id,
            slot_id,
            workspace,
            task,
            publication,
            _REMOTE_MUTATION_RUNWAY_SECONDS,
        )
        _push(workspace, publication)
    _revalidate_authorization(
        db_path,
        task_id,
        slot_id,
        workspace,
        task,
        publication,
        _REMOTE_MUTATION_RUNWAY_SECONDS,
    )
    _create_pr(workspace, db_dir, controller_fd, publication)

    final_remote_sha = _remote_branch_sha(workspace, publication)
    if final_remote_sha != local_commit:
        _refuse("remote-branch-mismatch")
    final_prs = _inspect_prs(workspace, publication)
    url = _existing_pr_url(final_prs, publication, local_commit)
    if url is None:
        _refuse("pr-creation-failed")
    return _result(True, state="published", commit=local_commit, url=url)


@tool
def nightshift_publish(task_id: str, slot_id: int) -> str:
    """Commit, push, and open the one Draft PR authorized by a live task binding."""
    try:
        if not isinstance(task_id, str) or not task_id or "\x00" in task_id:
            _refuse("invalid-task-id")
        if isinstance(slot_id, bool) or not isinstance(slot_id, int) or slot_id <= 0:
            _refuse("invalid-slot-id")
        workspace_raw = os.environ.get("_AP_WORKSPACE")
        db_raw = os.environ.get(_CONTROL_DB_ENV)
        if not workspace_raw or not db_raw:
            _refuse("missing-runner-binding")
        release_root_raw = os.environ.get("NIGHTSHIFT_RELEASE_ROOT")
        release_sha = os.environ.get("NIGHTSHIFT_RELEASE_SHA")
        if not release_root_raw or not release_sha:
            _refuse("missing-release-binding")
        db_path = os.path.abspath(db_raw)
        slot, task = _load_rows(db_path, task_id, slot_id)
        workspace = os.path.realpath(workspace_raw)
        _validate_binding(slot, task, task_id, workspace, 0.0)
        contract, db_dir = _load_contract(db_path, task.get("spec_ref"))
        publication = _validate_contract(contract, task, task_id)
        _validate_executable(_GIT_EXECUTABLE)
        _validate_executable(_GH_EXECUTABLE)
        _validate_executable(_PYTHON_EXECUTABLE)
        with _publication_lock(db_dir, task_id):
            with _trusted_controller(
                release_root_raw, release_sha, workspace, db_dir
            ) as controller_fd:
                return _publish_locked(
                    workspace_raw,
                    workspace,
                    db_path,
                    db_dir,
                    task_id,
                    slot_id,
                    task,
                    publication,
                    controller_fd,
                )
    except _Refusal as exc:
        return _result(False, reason=exc.reason)
    except Exception:
        return _result(False, reason="internal-error")
