"""Private write-once project startup descriptors (ADR-0020)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import stat
import subprocess
import time
import unicodedata
from urllib.parse import quote, urlsplit

from nightshift_github import (
    CheckSetRefusal,
    GitHubUndetermined,
    check_run_sort_key,
    enumerate_check_runs,
    evaluate_required_checks,
    github_get_json,
)


MAX_ID = (1 << 63) - 1
MAX_DESCRIPTOR_BYTES = 1_048_576
GIT_BIN = "/usr/bin/git"
GIT_PROBE_TIMEOUT_SECONDS = 2.0
# No `safe.directory=*`: a `.git` owned by another uid now fails the probe
# (dubious ownership), which `_classify_git_worktree` already refuses.
GIT_HARDENING = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
GIT_PROBE_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
    "PAGER": "cat",
}
REPOSITORY_ID_RE = re.compile(r"[1-9][0-9]{0,19}\Z")
PROJECT_RUN_ID_RE = re.compile(r"prun_v1_[a-z2-7]{26}\Z")
REQUEST_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_OBJECT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")

TOP_LEVEL_FIELDS = {
    "schema_version", "kind", "project_run_id", "request_id",
    "request_facts_sha256", "observed_at", "nightshift_sha", "state_root",
    "requested_repository", "repository", "brief", "required_checks",
    "observed_check_runs", "branch_observations", "ancestry_trust",
}
ANCESTRY_BOUNDARY_FIELDS = {"depth", "path", "reason"}
ANCESTRY_REASONS = frozenset(
    {"foreign_owner", "other_writable", "group_writable"})
REPOSITORY_FIELDS = {
    "id", "owner_id", "owner_login", "owner_type", "name", "full_name",
    "html_url", "private", "disabled", "archived", "fork",
    "default_branch", "start_sha",
}
BRIEF_FIELDS = {"path", "blob_sha", "sha256", "size"}
REQUIRED_CHECK_FIELDS = {"app_id", "name"}
OBSERVED_CHECK_FIELDS = {
    "id", "app_id", "app_slug", "name", "status", "conclusion",
}
BRANCH_FIELDS = {"first", "final"}


class ProjectError(Exception):
    def __init__(self, reason_code, exit_code, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def _refuse(reason_code):
    raise ProjectError(reason_code, 3)


def _operation_failed(reason_code="state_operation_failed"):
    raise ProjectError(reason_code, 1)


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _parse_numeric_id(value, reason_code):
    if not isinstance(value, str) or not REPOSITORY_ID_RE.fullmatch(value):
        _refuse(reason_code)
    number = int(value)
    if number > MAX_ID or str(number) != value:
        _refuse(reason_code)
    return number


def _api_id(value):
    if type(value) is not int or not 0 < value <= MAX_ID:
        raise GitHubUndetermined()
    return value


def _safe_text(value, *, minimum=1, maximum=255):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    return not any(unicodedata.category(ch).startswith("C") for ch in value)


def _validate_scalar_text(value, reason_code, *, maximum=255):
    if not _safe_text(value, maximum=maximum):
        _refuse(reason_code)
    return value


def derive_project_run_id(repository_id, request_id):
    if type(repository_id) is not int or not 0 < repository_id <= MAX_ID:
        raise ValueError("invalid repository id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("invalid request id")
    preimage = (b"nightshift.project-run.v1\0"
                + str(repository_id).encode("ascii") + b"\0"
                + request_id.encode("ascii"))
    digest = hashlib.sha256(preimage).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return "prun_v1_" + encoded[:26]


def _parse_repository(value):
    if not isinstance(value, str) or not value or len(value) > 240:
        _refuse("invalid_repository")
    if "%" in value or "\\" in value or any(
            unicodedata.category(ch).startswith("C") for ch in value):
        _refuse("invalid_repository")
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            _refuse("invalid_repository")
        path = parsed.path
        if path.endswith(".git"):
            path = path[:-4]
        if not path.startswith("/") or path.count("/") != 2:
            _refuse("invalid_repository")
        value = path[1:]
    elif "://" in value or value.count("/") != 1 or value.startswith("git@"):
        _refuse("invalid_repository")
    owner, name = value.split("/", 1)
    if (not OWNER_RE.fullmatch(owner) or not REPOSITORY_RE.fullmatch(name)
            or name in (".", "..") or name.endswith(".git")):
        _refuse("invalid_repository")
    return owner.lower() + "/" + name.lower()


def _validate_branch(value):
    _validate_scalar_text(value, "invalid_default_branch", maximum=255)
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", value)
            or value.startswith("/") or value.endswith(("/", ".", ".lock"))
            or ".." in value or "//" in value or "@{" in value):
        _refuse("invalid_default_branch")
    return value


def _parse_required_checks(values):
    identities = []
    for value in values:
        if not isinstance(value, str) or ":" not in value:
            _refuse("invalid_required_check")
        app_text, name = value.split(":", 1)
        app_id = _parse_numeric_id(app_text, "invalid_required_check")
        _validate_scalar_text(name, "invalid_required_check", maximum=255)
        identities.append((app_id, name))
    if not identities:
        _refuse("invalid_required_check")
    if len(set(identities)) != len(identities):
        _refuse("duplicate_required_check")
    return sorted(identities)


def _validate_bind_args(args):
    repository_id = _parse_numeric_id(
        args.expect_repository_id, "invalid_repository_id")
    owner_id = _parse_numeric_id(args.expect_owner_id, "invalid_owner_id")
    if not REQUEST_ID_RE.fullmatch(args.request_id or ""):
        _refuse("invalid_request_id")
    repository = _parse_repository(args.repository)
    default_branch = _validate_branch(args.expect_default_branch)
    if not SHA_RE.fullmatch(args.expect_start_sha or ""):
        _refuse("invalid_start_sha")
    if not SHA256_RE.fullmatch(args.expect_brief_sha256 or ""):
        _refuse("invalid_brief_sha256")
    if not SHA_RE.fullmatch(args.nightshift_sha or ""):
        _refuse("invalid_nightshift_sha")
    required = _parse_required_checks(args.required_check)
    project_run_id = derive_project_run_id(repository_id, args.request_id)
    return {
        "repository_id": repository_id,
        "owner_id": owner_id,
        "request_id": args.request_id,
        "repository": repository,
        "default_branch": default_branch,
        "start_sha": args.expect_start_sha,
        "brief_sha256": args.expect_brief_sha256,
        "nightshift_sha": args.nightshift_sha,
        "required_checks": required,
        "project_run_id": project_run_id,
    }


def _canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _request_facts(values):
    return {
        "schema_version": 1,
        "kind": "nightshift.project-startup-request-facts",
        "project_run_id": values["project_run_id"],
        "request_id": values["request_id"],
        "repository": values["repository"],
        "repository_id": values["repository_id"],
        "owner_id": values["owner_id"],
        "default_branch": values["default_branch"],
        "start_sha": values["start_sha"],
        "brief_sha256": values["brief_sha256"],
        "required_checks": [
            {"app_id": app_id, "name": name}
            for app_id, name in values["required_checks"]
        ],
        "nightshift_sha": values["nightshift_sha"],
    }


def _facts_digest(facts):
    return hashlib.sha256(_canonical_bytes(facts)).hexdigest()


def _descriptor_values(descriptor):
    repository = descriptor["repository"]
    brief = descriptor["brief"]
    return {
        "repository_id": repository["id"],
        "owner_id": repository["owner_id"],
        "request_id": descriptor["request_id"],
        "repository": descriptor["requested_repository"],
        "default_branch": repository["default_branch"],
        "start_sha": repository["start_sha"],
        "brief_sha256": brief["sha256"],
        "nightshift_sha": descriptor["nightshift_sha"],
        "required_checks": [
            (item["app_id"], item["name"])
            for item in descriptor["required_checks"]
        ],
        "project_run_id": descriptor["project_run_id"],
    }


def _classify_git_worktree(returncode, stdout, stderr):
    if returncode == 0 and stderr == b"":
        if stdout == b"true\n":
            return True
        if stdout == b"false\n":
            return False
    if (returncode != 0 and stdout == b""
            and b"not a git repository" in stderr.lower()):
        return False
    _refuse("invalid_state_root")


def _is_git_worktree(directory_fd):
    """Ask Git from the already-open directory, never from a raced path."""
    try:
        process = subprocess.Popen(
            [GIT_BIN, "--no-pager", *GIT_HARDENING,
             "rev-parse", "--is-inside-work-tree"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=GIT_PROBE_ENV,
            preexec_fn=lambda: os.fchdir(directory_fd),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectError("state_operation_failed", 1) from exc

    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    streams = {
        process.stdout.fileno(): (process.stdout, stdout, 16),
        process.stderr.fileno(): (process.stderr, stderr, 256),
    }
    for fileobj, _, _ in streams.values():
        selector.register(fileobj, selectors.EVENT_READ)
    deadline = time.monotonic() + GIT_PROBE_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _operation_failed()
            events = selector.select(remaining)
            if not events:
                _operation_failed()
            for key, _ in events:
                fileobj, target, limit = streams[key.fd]
                chunk = os.read(key.fd, limit + 1 - len(target))
                if not chunk:
                    selector.unregister(fileobj)
                    fileobj.close()
                    continue
                target.extend(chunk)
                if len(target) > limit:
                    _refuse("invalid_state_root")
        returncode = process.wait(
            timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, ProjectError) as exc:
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError("state_operation_failed", 1) from exc
    finally:
        selector.close()
        for fileobj in (process.stdout, process.stderr):
            if fileobj is not None and not fileobj.closed:
                fileobj.close()

    return _classify_git_worktree(
        returncode, bytes(stdout), bytes(stderr))


def _check_git_boundary(directory_fd):
    try:
        marker = os.stat(".git", dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProjectError("state_root_ambiguous_git_marker", 3) from exc
    if not (stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode)):
        _refuse("state_root_ambiguous_git_marker")
    if _is_git_worktree(directory_fd):
        _refuse("invalid_state_root")


def _ancestry_failure_reason(uid, mode, effective_uid):
    """Return ADR-0021's deterministic reason for one opened directory."""
    if not stat.S_ISDIR(mode):
        raise ValueError("ancestry component is not a directory")
    if uid not in (0, effective_uid):
        return "foreign_owner"
    if mode & stat.S_IWOTH:
        return "other_writable"
    if mode & stat.S_IWGRP:
        return "group_writable"
    return None


def _classify_ancestry(rows, effective_uid):
    """Pure TRUSTED_DIR/deepest-boundary predicate from ADR-0021."""
    boundary = None
    for depth, uid, mode in rows:
        if type(depth) is not int or depth < 0:
            raise ValueError("invalid ancestry depth")
        reason = _ancestry_failure_reason(uid, mode, effective_uid)
        if reason is not None:
            boundary = {"depth": depth, "reason": reason}
    if boundary is None:
        return "full", None
    return "partial", boundary


def _validate_state_root_path(path):
    if (not isinstance(path, str) or not _safe_text(path, maximum=4096)
            or len(path.encode("utf-8")) > 4096
            or not path.startswith("/")
            or path != os.path.normpath(path) or path.startswith("//")
            or path == "/srv/project" or path.startswith("/srv/project/")):
        _refuse("invalid_state_root")


def _open_state_root(path, *, require_trusted_ancestry=False):
    _validate_state_root_path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    components = [component for component in path.split("/") if component]
    paths = ["/"]
    rows = []
    current = None
    anchor = None
    suffix = []
    try:
        current = os.open("/", flags)
        info = os.fstat(current)
        rows.append((0, info.st_uid, info.st_mode))
        anchor = os.dup(current)
        prefix = ""
        if _ancestry_failure_reason(info.st_uid, info.st_mode,
                                    os.geteuid()) is not None:
            suffix = []
        for depth, component in enumerate(components, 1):
            child = os.open(component, flags | nofollow, dir_fd=current)
            os.close(current)
            current = child
            info = os.fstat(current)
            rows.append((depth, info.st_uid, info.st_mode))
            prefix += "/" + component
            paths.append(prefix)
            if _ancestry_failure_reason(
                    info.st_uid, info.st_mode, os.geteuid()) is not None:
                os.close(anchor)
                anchor = os.dup(current)
                suffix = []
            else:
                suffix.append(component)
        info = os.fstat(current)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            _refuse("invalid_state_root")

        trust, boundary = _classify_ancestry(rows, os.geteuid())
        observation = {"ancestry_trust": trust}
        if boundary is not None:
            if boundary["depth"] > 255:
                _refuse("invalid_state_root")
            boundary = {**boundary, "path": paths[boundary["depth"]]}
            observation["ancestry_trust_boundary"] = boundary

        # The anchor is the deepest untrusted component, or / for a full
        # chain. Only the strict suffix below it is classified. Holding the
        # anchor fd makes each subsequent no-follow open relative to the exact
        # boundary that produced this observation.
        os.close(current)
        current = anchor
        anchor = None
        if trust == "full":
            _check_git_boundary(current)
        for component in suffix:
            child = os.open(component, flags | nofollow, dir_fd=current)
            os.close(current)
            current = child
            _check_git_boundary(current)
        info = os.fstat(current)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            _refuse("invalid_state_root")
        if require_trusted_ancestry and trust == "partial":
            raise ProjectError(
                "state_root_untrusted_ancestry", 3, {
                    "ancestry_trust": "partial",
                    "ancestry_trust_boundary": boundary,
                })
        result_fd = current
        current = None
        return result_fd, observation
    except ProjectError:
        raise
    except (OSError, ValueError) as exc:
        raise ProjectError("invalid_state_root", 3) from exc
    finally:
        for descriptor in (current, anchor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _open_private_dir(parent_fd, name, *, create):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        return_fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise ProjectError("state_operation_failed", 1) from exc
        try:
            return_fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise ProjectError("invalid_state_root", 3) from exc
    except OSError as exc:
        raise ProjectError("invalid_state_root", 3) from exc
    if created:
        try:
            os.fchmod(return_fd, 0o700)
            os.fsync(return_fd)
            os.fsync(parent_fd)
        except OSError as exc:
            os.close(return_fd)
            raise ProjectError("state_operation_failed", 1) from exc
    info = os.fstat(return_fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        os.close(return_fd)
        _refuse("invalid_state_root")
    return return_fd


def _read_all(file_fd, size):
    data = bytearray()
    while len(data) <= size:
        chunk = os.read(file_fd, min(65_536, size + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _validate_tree(value):
    if value is None or type(value) in (bool, int):
        return
    if isinstance(value, str):
        if not _safe_text(value, minimum=0, maximum=65_536):
            raise ValueError("invalid text")
        return
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError("list too large")
        for item in value:
            _validate_tree(item)
        return
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("object too large")
        for key, item in value.items():
            if not _safe_text(key, maximum=128):
                raise ValueError("invalid key")
            _validate_tree(item)
        return
    raise ValueError("unsupported JSON type")


def _exact_fields(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("wrong fields")


def _validate_descriptor(descriptor, raw, *, state_root, repository_id,
                         project_run_id):
    _validate_tree(descriptor)
    trust = descriptor.get("ancestry_trust") if isinstance(descriptor, dict) else None
    expected_fields = set(TOP_LEVEL_FIELDS)
    if trust == "partial":
        expected_fields.add("ancestry_trust_boundary")
    _exact_fields(descriptor, expected_fields)
    if descriptor["schema_version"] != 1 or descriptor["kind"] != "nightshift.project-startup":
        raise ValueError("wrong schema")
    if _canonical_bytes(descriptor) != raw:
        raise ValueError("noncanonical")
    if descriptor["state_root"] != state_root:
        raise ValueError("wrong state root")
    _validate_state_root_path(state_root)
    if trust not in ("full", "partial"):
        raise ValueError("invalid ancestry trust")
    if trust == "partial":
        boundary = descriptor["ancestry_trust_boundary"]
        _exact_fields(boundary, ANCESTRY_BOUNDARY_FIELDS)
        depth = boundary["depth"]
        boundary_path = boundary["path"]
        reason = boundary["reason"]
        components = [item for item in state_root.split("/") if item]
        if (type(depth) is not int or not 0 <= depth <= 255
                or depth >= len(components)):
            raise ValueError("invalid ancestry boundary depth")
        if (not _safe_text(boundary_path, maximum=4096)
                or len(boundary_path.encode("utf-8")) > 4096
                or not boundary_path.startswith("/")
                or boundary_path.startswith("//")
                or boundary_path != os.path.normpath(boundary_path)):
            raise ValueError("invalid ancestry boundary path")
        boundary_components = [
            item for item in boundary_path.split("/") if item]
        if depth != len(boundary_components):
            raise ValueError("ancestry boundary depth mismatch")
        prefix = "/" if boundary_path == "/" else boundary_path + "/"
        if boundary_path == state_root or not state_root.startswith(prefix):
            raise ValueError("ancestry boundary is not a proper prefix")
        if reason not in ANCESTRY_REASONS:
            raise ValueError("invalid ancestry boundary reason")
    if descriptor["project_run_id"] != project_run_id:
        raise ValueError("wrong project id")
    if not PROJECT_RUN_ID_RE.fullmatch(project_run_id):
        raise ValueError("invalid project id")
    if not REQUEST_ID_RE.fullmatch(descriptor["request_id"]):
        raise ValueError("invalid request id")
    if not SHA_RE.fullmatch(descriptor["nightshift_sha"]):
        raise ValueError("invalid source sha")
    if (type(descriptor["observed_at"]) is not int
            or not 1 <= descriptor["observed_at"] <= MAX_ID):
        raise ValueError("invalid timestamp")
    if not SHA256_RE.fullmatch(descriptor["request_facts_sha256"]):
        raise ValueError("invalid facts digest")

    repository = descriptor["repository"]
    _exact_fields(repository, REPOSITORY_FIELDS)
    if repository["id"] != repository_id or _api_id(repository["id"]) != repository_id:
        raise ValueError("wrong repository id")
    _api_id(repository["owner_id"])
    for key in ("owner_login", "name", "full_name", "html_url",
                "default_branch"):
        if not _safe_text(repository[key], maximum=255):
            raise ValueError("bad repository text")
    if repository["owner_type"] != "User":
        raise ValueError("bad owner type")
    for key in ("private", "disabled", "archived", "fork"):
        if type(repository[key]) is not bool:
            raise ValueError("bad repository state")
    if (repository["private"] is not True or repository["disabled"]
            or repository["archived"] or repository["fork"]):
        raise ValueError("unusable repository")
    if not SHA_RE.fullmatch(repository["start_sha"]):
        raise ValueError("bad start sha")
    requested = _parse_repository(descriptor["requested_repository"])
    if requested != descriptor["requested_repository"]:
        raise ValueError("noncanonical slug")
    requested_owner, requested_name = requested.split("/", 1)
    if repository["full_name"].lower() != requested:
        raise ValueError("wrong full name")
    if (repository["owner_login"].lower() != requested_owner
            or repository["name"].lower() != requested_name):
        raise ValueError("wrong repository identity")
    if repository["html_url"].lower() != "https://github.com/" + requested:
        raise ValueError("wrong URL")
    try:
        _validate_branch(repository["default_branch"])
    except ProjectError as exc:
        raise ValueError("bad default branch") from exc

    brief = descriptor["brief"]
    _exact_fields(brief, BRIEF_FIELDS)
    if (brief["path"] != ".omni/brief.md"
            or not GIT_OBJECT_RE.fullmatch(brief["blob_sha"])
            or not SHA256_RE.fullmatch(brief["sha256"])
            or type(brief["size"]) is not int
            or not 1 <= brief["size"] <= 65_536):
        raise ValueError("bad brief")

    required = descriptor["required_checks"]
    if not isinstance(required, list) or not required:
        raise ValueError("bad required checks")
    identities = []
    for item in required:
        _exact_fields(item, REQUIRED_CHECK_FIELDS)
        _api_id(item["app_id"])
        if not _safe_text(item["name"], maximum=255):
            raise ValueError("bad check name")
        identities.append((item["app_id"], item["name"]))
    if identities != sorted(set(identities)):
        raise ValueError("noncanonical checks")

    observed = descriptor["observed_check_runs"]
    if not isinstance(observed, list) or len(observed) > 100:
        raise ValueError("bad observations")
    for item in observed:
        _exact_fields(item, OBSERVED_CHECK_FIELDS)
        _api_id(item["id"])
        _api_id(item["app_id"])
        for key in ("app_slug", "name", "status"):
            if not _safe_text(item[key], maximum=255):
                raise ValueError("bad observation")
        if item["conclusion"] is not None and not _safe_text(
                item["conclusion"], maximum=64):
            raise ValueError("bad conclusion")
    if observed != sorted(observed, key=check_run_sort_key):
        raise ValueError("noncanonical observations")
    # This validates that the immutable record contains publishable bind-time
    # evidence; it does not establish current CI freshness. ADR-0020 section 4
    # assigns every later freshness gate to item 4's CAS publication guard.
    evaluate_required_checks(observed, identities)

    branches = descriptor["branch_observations"]
    _exact_fields(branches, BRANCH_FIELDS)
    if branches["first"] != repository["start_sha"] or branches["final"] != repository["start_sha"]:
        raise ValueError("branch mismatch")

    values = _descriptor_values(descriptor)
    if derive_project_run_id(repository_id, descriptor["request_id"]) != project_run_id:
        raise ValueError("derived id mismatch")
    if _facts_digest(_request_facts(values)) != descriptor["request_facts_sha256"]:
        raise ValueError("facts digest mismatch")


def _read_descriptor_from_repo(repo_fd, filename, *, state_root,
                               repository_id, project_run_id, missing_ok):
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(filename, flags, dir_fd=repo_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        _refuse("descriptor_not_found")
    except OSError as exc:
        raise ProjectError("startup_corrupt", 3) from exc
    try:
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 1 <= info.st_size <= MAX_DESCRIPTOR_BYTES):
            _refuse("startup_corrupt")
        raw = _read_all(file_fd, info.st_size)
        if len(raw) != info.st_size:
            _refuse("startup_corrupt")
    except OSError as exc:
        raise ProjectError("state_operation_failed", 1) from exc
    finally:
        os.close(file_fd)
    try:
        descriptor = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        _validate_descriptor(
            descriptor, raw, state_root=state_root,
            repository_id=repository_id, project_run_id=project_run_id,
        )
    except ProjectError as exc:
        raise ProjectError("startup_corrupt", 3) from exc
    except Exception as exc:
        raise ProjectError("startup_corrupt", 3) from exc
    return descriptor, raw, info


def _read_descriptor(root_fd, *, state_root, repository_id, project_run_id,
                     missing_ok):
    startups_fd = _open_private_dir(root_fd, "startups", create=False)
    if startups_fd is None:
        if missing_ok:
            return None
        _refuse("descriptor_not_found")
    try:
        repository_fd = _open_private_dir(
            startups_fd, str(repository_id), create=False)
        if repository_fd is None:
            if missing_ok:
                return None
            _refuse("descriptor_not_found")
        try:
            return _read_descriptor_from_repo(
                repository_fd, project_run_id + ".json",
                state_root=state_root, repository_id=repository_id,
                project_run_id=project_run_id, missing_ok=missing_ok,
            )
        finally:
            os.close(repository_fd)
    finally:
        os.close(startups_fd)


def _existing_result(existing, requested_facts, observed_ancestry):
    descriptor, raw, _ = existing
    existing_facts = _request_facts(_descriptor_values(descriptor))
    if existing_facts != requested_facts:
        _refuse("startup_conflict")
    return {
        "ok": True,
        "status": "existing",
        "reason_code": "descriptor_existing",
        "project_run_id": descriptor["project_run_id"],
        "repository_id": descriptor["repository"]["id"],
        "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_ancestry_trust": observed_ancestry["ancestry_trust"],
    }


def _write_all(file_fd, data):
    offset = 0
    while offset < len(data):
        written = os.write(file_fd, data[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _publish(root_fd, *, state_root, repository_id, project_run_id,
             descriptor, requested_facts, observed_ancestry):
    startups_fd = _open_private_dir(root_fd, "startups", create=True)
    try:
        repository_fd = _open_private_dir(
            startups_fd, str(repository_id), create=True)
        try:
            filename = project_run_id + ".json"
            existing = _read_descriptor_from_repo(
                repository_fd, filename, state_root=state_root,
                repository_id=repository_id, project_run_id=project_run_id,
                missing_ok=True,
            )
            if existing is not None:
                return _existing_result(
                    existing, requested_facts, observed_ancestry)

            raw = _canonical_bytes(descriptor)
            try:
                _validate_descriptor(
                    descriptor, raw, state_root=state_root,
                    repository_id=repository_id,
                    project_run_id=project_run_id,
                )
            except Exception as exc:
                raise ProjectError("project_operation_failed", 1) from exc
            temporary = ".startup-tmp-" + secrets.token_hex(16)
            temp_fd = None
            temp_exists = False
            try:
                flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
                         | getattr(os, "O_NOFOLLOW", 0))
                temp_fd = os.open(temporary, flags, 0o600, dir_fd=repository_fd)
                temp_exists = True
                os.fchmod(temp_fd, 0o600)
                _write_all(temp_fd, raw)
                os.fsync(temp_fd)
                os.close(temp_fd)
                temp_fd = None
                try:
                    os.link(
                        temporary, filename,
                        src_dir_fd=repository_fd, dst_dir_fd=repository_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    os.unlink(temporary, dir_fd=repository_fd)
                    temp_exists = False
                    os.fsync(repository_fd)
                    existing = _read_descriptor_from_repo(
                        repository_fd, filename, state_root=state_root,
                        repository_id=repository_id,
                        project_run_id=project_run_id, missing_ok=False,
                    )
                    return _existing_result(
                        existing, requested_facts, observed_ancestry)
                os.fsync(repository_fd)
                os.unlink(temporary, dir_fd=repository_fd)
                temp_exists = False
                os.fsync(repository_fd)
                return {
                    "ok": True,
                    "status": "created",
                    "reason_code": "descriptor_created",
                    "project_run_id": project_run_id,
                    "repository_id": repository_id,
                    "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
                    "observed_ancestry_trust":
                        observed_ancestry["ancestry_trust"],
                }
            except ProjectError:
                raise
            except OSError as exc:
                raise ProjectError("state_operation_failed", 1) from exc
            finally:
                if temp_fd is not None:
                    os.close(temp_fd)
                if temp_exists:
                    try:
                        os.unlink(temporary, dir_fd=repository_fd)
                        os.fsync(repository_fd)
                    except OSError:
                        pass
        finally:
            os.close(repository_fd)
    finally:
        os.close(startups_fd)


def _remote_json(endpoint):
    try:
        return github_get_json(endpoint)
    except GitHubUndetermined as exc:
        raise ProjectError("repository_undetermined", 2) from exc


def _remote_text(value, *, maximum=255):
    if not _safe_text(value, maximum=maximum):
        raise GitHubUndetermined()
    return value


def _observation_time(args):
    raw_now = getattr(args, "now", None)
    raw_now = time.time() if raw_now is None else raw_now
    if (type(raw_now) not in (int, float)
            or (type(raw_now) is float and not math.isfinite(raw_now))):
        _refuse("invalid_observed_at")
    observed_at = int(raw_now)
    if not 1 <= observed_at <= MAX_ID:
        _refuse("invalid_observed_at")
    return observed_at


def _observe_repository(values):
    slug = values["repository"]
    try:
        data = _remote_json("repos/" + slug)
        if not isinstance(data, dict) or not isinstance(data.get("owner"), dict):
            raise GitHubUndetermined()
        repository_id = _api_id(data.get("id"))
        owner_id = _api_id(data["owner"].get("id"))
        name = _remote_text(data.get("name"), maximum=100)
        full_name = _remote_text(data.get("full_name"), maximum=240)
        html_url = _remote_text(data.get("html_url"), maximum=300)
        owner_login = _remote_text(data["owner"].get("login"), maximum=100)
        owner_type = _remote_text(data["owner"].get("type"), maximum=64)
        default_branch = _remote_text(data.get("default_branch"), maximum=255)
        states = {key: data.get(key) for key in
                  ("private", "disabled", "archived", "fork")}
        if any(type(item) is not bool for item in states.values()):
            raise GitHubUndetermined()
    except GitHubUndetermined as exc:
        raise ProjectError("repository_undetermined", 2) from exc

    if repository_id != values["repository_id"]:
        _refuse("repository_id_mismatch")
    if owner_id != values["owner_id"]:
        _refuse("owner_id_mismatch")
    expected_owner, expected_name = slug.split("/", 1)
    if (full_name.lower() != slug or owner_login.lower() != expected_owner
            or name.lower() != expected_name):
        _refuse("repository_identity_mismatch")
    if html_url.lower() != "https://github.com/" + slug:
        _refuse("repository_identity_mismatch")
    if owner_type != "User":
        _refuse("repository_unusable")
    if (states["private"] is not True or states["disabled"]
            or states["archived"] or states["fork"]):
        _refuse("repository_unusable")
    if default_branch != values["default_branch"]:
        _refuse("default_branch_mismatch")
    return {
        "id": repository_id,
        "owner_id": owner_id,
        "owner_login": owner_login,
        "owner_type": owner_type,
        "name": name,
        "full_name": full_name,
        "html_url": html_url,
        "private": states["private"],
        "disabled": states["disabled"],
        "archived": states["archived"],
        "fork": states["fork"],
        "default_branch": default_branch,
        "start_sha": values["start_sha"],
    }


def _observe_branch(values):
    endpoint = ("repos/" + values["repository"] + "/branches/"
                + quote(values["default_branch"], safe=""))
    data = _remote_json(endpoint)
    try:
        if (not isinstance(data, dict) or not isinstance(data.get("commit"), dict)
                or data.get("name") != values["default_branch"]):
            raise GitHubUndetermined()
        sha = data["commit"].get("sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise GitHubUndetermined()
        return sha
    except GitHubUndetermined as exc:
        raise ProjectError("repository_undetermined", 2) from exc


def _observe_brief(values):
    endpoint = ("repos/" + values["repository"]
                + "/contents/.omni/brief.md?ref=" + values["start_sha"])
    data = _remote_json(endpoint)
    try:
        if (not isinstance(data, dict) or data.get("type") != "file"
                or data.get("path") != ".omni/brief.md"
                or data.get("encoding") != "base64"):
            raise GitHubUndetermined()
        content = data.get("content")
        if not isinstance(content, str) or len(content) > 100_000:
            raise GitHubUndetermined()
        if any(ch.isspace() and ch not in "\r\n" for ch in content):
            raise GitHubUndetermined()
        decoded = base64.b64decode(
            content.replace("\r", "").replace("\n", ""), validate=True)
        size = data.get("size")
        blob_sha = data.get("sha")
        if (type(size) is not int or size != len(decoded)
                or not 1 <= size <= 65_536
                or not isinstance(blob_sha, str)
                or not GIT_OBJECT_RE.fullmatch(blob_sha)):
            raise GitHubUndetermined()
        decoded.decode("utf-8", "strict")
    except (GitHubUndetermined, binascii.Error, UnicodeDecodeError) as exc:
        raise ProjectError("repository_undetermined", 2) from exc
    digest = hashlib.sha256(decoded).hexdigest()
    if digest != values["brief_sha256"]:
        _refuse("brief_sha256_mismatch")
    return {
        "path": ".omni/brief.md",
        "blob_sha": blob_sha,
        "sha256": digest,
        "size": size,
    }


def bind_project(args):
    values = _validate_bind_args(args)
    observed_at = _observation_time(args)
    requested_facts = _request_facts(values)
    state_root = args.state_root

    root_fd, observed_ancestry = _open_state_root(
        state_root,
        require_trusted_ancestry=getattr(
            args, "require_trusted_ancestry", False))
    try:
        existing = _read_descriptor(
            root_fd, state_root=state_root,
            repository_id=values["repository_id"],
            project_run_id=values["project_run_id"], missing_ok=True,
        )
        if existing is not None:
            return _existing_result(
                existing, requested_facts, observed_ancestry)
    finally:
        os.close(root_fd)

    repository = _observe_repository(values)
    first_branch = _observe_branch(values)
    if first_branch != values["start_sha"]:
        _refuse("start_sha_mismatch")
    brief = _observe_brief(values)
    try:
        observed_checks = enumerate_check_runs(
            values["repository"], values["start_sha"])
        evaluate_required_checks(observed_checks, values["required_checks"])
    except CheckSetRefusal as exc:
        raise ProjectError(exc.reason_code, 3) from exc
    except GitHubUndetermined as exc:
        raise ProjectError("repository_undetermined", 2) from exc
    final_branch = _observe_branch(values)
    if final_branch != first_branch:
        _refuse("branch_drift")
    if final_branch != values["start_sha"]:
        _refuse("start_sha_mismatch")

    descriptor = {
        "schema_version": 1,
        "kind": "nightshift.project-startup",
        "project_run_id": values["project_run_id"],
        "request_id": values["request_id"],
        "request_facts_sha256": _facts_digest(requested_facts),
        "observed_at": observed_at,
        "nightshift_sha": values["nightshift_sha"],
        "state_root": state_root,
        "requested_repository": values["repository"],
        "repository": repository,
        "brief": brief,
        "required_checks": requested_facts["required_checks"],
        "observed_check_runs": observed_checks,
        "branch_observations": {
            "first": first_branch,
            "final": final_branch,
        },
    }

    root_fd, observed_ancestry = _open_state_root(
        state_root,
        require_trusted_ancestry=getattr(
            args, "require_trusted_ancestry", False))
    try:
        descriptor["ancestry_trust"] = observed_ancestry["ancestry_trust"]
        if observed_ancestry["ancestry_trust"] == "partial":
            descriptor["ancestry_trust_boundary"] = \
                observed_ancestry["ancestry_trust_boundary"]
        return _publish(
            root_fd, state_root=state_root,
            repository_id=values["repository_id"],
            project_run_id=values["project_run_id"],
            descriptor=descriptor, requested_facts=requested_facts,
            observed_ancestry=observed_ancestry,
        )
    finally:
        os.close(root_fd)


def show_project(args):
    repository_id = _parse_numeric_id(args.repository_id, "invalid_repository_id")
    project_run_id = args.project_run_id
    if not isinstance(project_run_id, str) or not PROJECT_RUN_ID_RE.fullmatch(project_run_id):
        _refuse("invalid_project_run_id")
    state_root = args.state_root
    root_fd, observed_ancestry = _open_state_root(
        state_root,
        require_trusted_ancestry=getattr(
            args, "require_trusted_ancestry", False))
    try:
        descriptor, raw, _ = _read_descriptor(
            root_fd, state_root=state_root, repository_id=repository_id,
            project_run_id=project_run_id, missing_ok=False,
        )
    finally:
        os.close(root_fd)
    return {
        "ok": True,
        "status": "found",
        "reason_code": "descriptor_found",
        "project_run_id": project_run_id,
        "repository_id": repository_id,
        "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_ancestry_trust": observed_ancestry["ancestry_trust"],
        "descriptor": descriptor,
    }
