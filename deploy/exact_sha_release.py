#!/usr/bin/env python3
"""Deterministic exact-Git-SHA release preparation and cutover proofs.

The live window is deliberately split from preparation. ``prepare`` only
materialises and seals a reviewed checkout, preserves the currently served
bundle for rollback, and writes an all-undetermined receipt. ``authorize`` is
read-only and prints the one cutover command an owner may approve. No command
uses ``omnigent stop`` and no command edits an Omnigent cache.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CORE_PROOFS = (
    "registered",
    "served",
    "content_at_sha",
    "enforced",
    "runtime_environment",
    "executed",
    "durable_continuity",
)
PROOFS = (*CORE_PROOFS, "scheduler_continuity")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GIT_EXECUTABLE = "/usr/bin/git"
GIT_SYSTEM_PATH = "/usr/bin:/bin"
BUSY_SESSION_STATES = {
    "queued", "starting", "running", "streaming", "waiting",
    "action_required", "cancelling",
}
CANARY_COMMAND = "nightshift-release-provenance-canary"
ECHO_RESPONSE = "NIGHTSHIFT_BUNDLE_ECHO"
CHILD_PROOF_INPUT = (
    "For the public bundle echo protocol, use your configured response. "
    "If no different response is configured, reply `NIGHTSHIFT_BUNDLE_ECHO`. "
    "Do not use tools or add other text."
)
CLAUDE_MARKER = "NS-EXEC-ADR0013-CLAUDE-4C2D8F70"
PROOF_TITLE = "bundle-echo-proof"
PROOF_METADATA = {
    "input": CHILD_PROOF_INPUT,
    "purpose": "explore",
    "cost_budget": {"max_cost_usd": 2},
}
REQUIRED_LEGACY_UNITS = {
    "omnigent-server.service",
    "omnigent-host.service",
    "nightshift-watchdog.service",
    "nightshift-state-backup.service",
}
UNIT_SUFFIXES = (".service", ".timer", ".socket", ".path")
SUPERVISION_SCHEMA = "nightshift-exact-sha-supervision-v1"


class ReleaseError(RuntimeError):
    """A fail-closed release refusal."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("wb") as fh:
        fh.write(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _run(argv: list[str], *, cwd: Path | None = None,
         env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        argv, cwd=str(cwd) if cwd else None, env=env,
        text=True, capture_output=True, check=False,
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise ReleaseError(f"command failed ({proc.returncode}): "
                           f"{shlex.join(argv)}: {detail}")
    return proc.stdout


def _systemctl_user(*args: str, check: bool = True) -> str:
    env = dict(os.environ)
    # Never inherit the exact false-premise failure from ADR-0014 C2: the
    # runtime directory is a fact derived from the executing uid.
    env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    proc = subprocess.run(
        ["systemctl", "--user", *args], env=env,
        text=True, capture_output=True, check=False,
    )
    if check and proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise ReleaseError(f"systemctl --user {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _legacy_unit_records(
    extra_names: Iterable[str] = (),
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    listed = _systemctl_user(
        "list-unit-files", "--type=service", "--type=timer",
        "--type=socket", "--type=path", "--no-legend", "--no-pager",
    )
    names = {line.split()[0] for line in listed.splitlines() if line.split()}
    names.update(REQUIRED_LEGACY_UNITS)
    names.update(extra_names)
    records: dict[str, dict[str, Any]] = {}
    contents: dict[str, bytes] = {}
    for name in sorted(names):
        if "/" in name or not name.endswith(UNIT_SUFFIXES):
            raise ReleaseError(f"unsafe legacy unit name: {name}")
        mask_path = unit_dir / name
        if mask_path.is_symlink():
            local_target = os.readlink(mask_path)
            local_digest = None
        elif mask_path.is_file():
            local_target = None
            local_digest = _sha256(mask_path.read_bytes())
        elif mask_path.exists():
            raise ReleaseError(f"unit override is not a regular file: {mask_path}")
        else:
            local_target = None
            local_digest = None
        show = _systemctl_user(
            "show", name,
            "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath",
            "--no-pager",
        )
        state: dict[str, str] = {}
        for line in show.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                state[key] = value
        required_fields = {"LoadState", "ActiveState", "SubState", "UnitFileState",
                           "FragmentPath"}
        if not required_fields.issubset(state):
            raise ReleaseError(f"systemd returned incomplete state for {name}")
        fragment = Path(state["FragmentPath"]) if state["FragmentPath"] else None
        if fragment is not None:
            try:
                content = fragment.read_bytes()
            except OSError as exc:
                raise ReleaseError(f"cannot read unit fragment: {fragment}") from exc
            fragment_digest = _sha256(content)
        else:
            content = b""
            fragment_digest = None
        contents[name] = content
        records[name] = {
            "name": name,
            "path": str(mask_path),
            "sha256": local_digest,
            "symlink_target": local_target,
            "fragment_path": str(fragment) if fragment else None,
            "fragment_sha256": fragment_digest,
            "load_state": state["LoadState"],
            "active_state": state["ActiveState"],
            "sub_state": state["SubState"],
            "unit_file_state": state["UnitFileState"],
        }

    return records, contents


def _select_stale_legacy_units(
    records: dict[str, dict[str, Any]], contents: dict[str, bytes]
) -> set[str]:
    selected = set(REQUIRED_LEGACY_UNITS)
    for name, content in contents.items():
        # `/srv/nightshift-releases/` is deliberately not matched: only the
        # old mutable root with a slash boundary is stale.
        if (b"/srv/nightshift/" in content
                or b"/home/nightshift/nightshift-runtime/" in content):
            selected.add(name)
    changed = True
    while changed:
        changed = False
        for name, content in contents.items():
            if name not in selected and any(
                    selected_name.encode() in content for selected_name in selected):
                selected.add(name)
                changed = True
    return selected


def _legacy_unit_inventory() -> list[dict[str, Any]]:
    """Bootstrap-mode inventory: every mechanically selected unit must be loaded.

    Valid only when there is no verified accepted predecessor (ADR-0064 Decision
    §1). Use `_subsequent_unit_transition` once a predecessor generation exists.
    """
    records, contents = _legacy_unit_records()
    selected = _select_stale_legacy_units(records, contents)
    missing = [name for name in selected
               if records[name]["load_state"] != "loaded"
               or not records[name]["fragment_path"]]
    if missing:
        raise ReleaseError(f"legacy supervision units are unreadable: {missing}")
    return [records[name] for name in sorted(selected)]


def _subsequent_unit_transition(
    inherited_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the current host inventory into inherited masks and new units.

    ADR-0064 Decision §2 item 4 / §3: units named in `inherited_names` must
    still be mechanically masked at their recorded `/dev/null` path and
    inactive -- their pre-cutover state is not re-derived, only re-verified.
    Every other mechanically discovered/required unit is a newly discovered
    `to_mask` candidate and must be loaded from a readable fragment, exactly
    like bootstrap mode.
    """
    records, contents = _legacy_unit_records(inherited_names)
    discovered = _select_stale_legacy_units(records, contents)
    to_mask_names = sorted(discovered - inherited_names)
    missing_to_mask = [name for name in to_mask_names
                       if records[name]["load_state"] != "loaded"
                       or not records[name]["fragment_path"]]
    if missing_to_mask:
        raise ReleaseError(f"legacy supervision units are unreadable: {missing_to_mask}")
    unmasked = [name for name in sorted(inherited_names)
               if records[name]["load_state"] != "masked"
               or records[name]["symlink_target"] != "/dev/null"
               or records[name]["active_state"] == "active"]
    if unmasked:
        raise ReleaseError(
            f"inherited legacy supervision unit mask is not intact: {unmasked}"
        )
    inherited_masked = [records[name] for name in sorted(inherited_names)]
    to_mask = [records[name] for name in to_mask_names]
    return inherited_masked, to_mask


def _predecessor_masked_names(predecessor_plan: dict[str, Any]) -> set[str]:
    """Every legacy unit name masked by a predecessor plan, of either schema."""
    supervision = predecessor_plan.get("supervision")
    if isinstance(supervision, dict) and supervision.get("schema") == SUPERVISION_SCHEMA:
        return ({row["name"] for row in supervision.get("inherited_masked", [])}
                | {row["name"] for row in supervision.get("to_mask", [])})
    return {row["name"] for row in predecessor_plan.get("legacy_units", [])}


def _current_legacy_transition(
    plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-derive the live (inherited_masked, to_mask) split for an existing plan."""
    supervision = plan.get("supervision")
    if not isinstance(supervision, dict) or supervision.get("schema") != SUPERVISION_SCHEMA:
        raise ReleaseError("plan has no supported supervision transition schema")
    mode = supervision.get("mode")
    if mode == "bootstrap":
        return [], _legacy_unit_inventory()
    if mode != "subsequent":
        raise ReleaseError(f"unsupported supervision mode: {mode!r}")
    inherited_names = {row["name"] for row in supervision["inherited_masked"]}
    return _subsequent_unit_transition(inherited_names)


def _verify_predecessor(predecessor_plan_path: Path, *, control_identity: dict[str, Any],
                        server_env: dict[str, str], server_cmdline: str,
                        registration: dict[str, Any], server_url: str) -> dict[str, Any]:
    """Validate a claimed predecessor generation (ADR-0064 Decision §2, items 1-3, 5).

    Item 4 (inherited masks still intact) is verified separately by
    `_subsequent_unit_transition`, since it depends on live host state that
    changes independently of this predecessor's own recorded evidence.
    """
    try:
        plan_path = predecessor_plan_path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(f"predecessor plan is unreadable: {predecessor_plan_path}") from exc
    predecessor_plan = _load_plan(plan_path)  # item 1: plan_digest self-validates
    # state_dir is derived from where the plan file actually lives, never from
    # the plan's own (externally-supplied, forgeable) `state_dir` field -- a
    # plan whose `state_dir` field points elsewhere (e.g. an attacker-writable
    # directory with a forged receipt.json) is refused outright.
    state_dir = plan_path.parent
    if str(state_dir) != predecessor_plan["state_dir"]:
        raise ReleaseError(
            "predecessor plan's state_dir field disagrees with its own file location"
        )
    receipt_path = state_dir / "receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"predecessor receipt is unreadable: {receipt_path}") from exc
    if receipt.get("schema") != "nightshift-exact-sha-receipt-v1":
        raise ReleaseError("predecessor receipt has an unsupported schema")
    if receipt.get("plan_digest") != predecessor_plan["plan_digest"]:
        raise ReleaseError("predecessor receipt does not name the predecessor plan digest")
    if receipt.get("accepted") is not True:
        raise ReleaseError("predecessor receipt is not accepted")
    proofs = receipt.get("proofs", {})
    if not all(proofs.get(name, {}).get("verdict") == "green" for name in PROOFS):
        raise ReleaseError("predecessor receipt has a proof that is not green")

    if (control_identity["device"] != predecessor_plan["control_db"]["device"]
            or control_identity["inode"] != predecessor_plan["control_db"]["inode"]):
        raise ReleaseError("current control DB identity does not match the predecessor")
    registered_operands = proofs.get("registered", {}).get("operands", {})
    if (registration.get("id") != registered_operands.get("actual_id")
            or registration.get("version") != registered_operands.get("actual_version")):
        raise ReleaseError(
            "currently registered Program does not match the predecessor's accepted receipt"
        )
    content_operands = proofs.get("content_at_sha", {}).get("operands", {})
    if (content_operands.get("sha") != predecessor_plan["sha"]
            or content_operands.get("git_object_digest")
               != predecessor_plan["program_git_manifest"]["digest"]):
        raise ReleaseError("predecessor content-at-SHA proof does not match its own plan")
    expected_policy_dir = str(Path(predecessor_plan["release_root"]) / "deploy" / "policies")
    if server_env.get("PYTHONPATH") != expected_policy_dir:
        raise ReleaseError(
            "currently live server PYTHONPATH does not match the predecessor release root"
        )
    if predecessor_plan["omnigent_bin"] not in server_cmdline:
        raise ReleaseError(
            "currently live server is not running the predecessor's accepted Omnigent binary"
        )
    # Item 3, live re-derivation: the receipt-vs-plan comparison above is only
    # self-consistency on the predecessor's own stored history. Independently
    # extract-and-hash the bundle the server is *currently* serving -- the
    # same live check postflight's content_at_sha proof performs -- and
    # confirm it still matches the predecessor's reviewed Git object.
    session = _new_session(
        server_url, registration["id"],
        f"Nightshift predecessor content check {predecessor_plan['sha'][:12]}",
        predecessor_plan["project"],
    )
    _bundle_bytes, live_digest = _download_and_hash_served_bundle(server_url, session["id"])
    if live_digest != predecessor_plan["program_git_manifest"]["digest"]:
        raise ReleaseError(
            "currently served content does not match the predecessor's accepted Program digest"
        )
    if (state_dir / "rollback-receipt.json").exists():
        raise ReleaseError(
            "predecessor state_dir has a rollback-receipt.json; a rolled-back "
            "generation cannot serve as subsequent-mode evidence"
        )
    return {"plan": predecessor_plan, "receipt": receipt, "state_dir": state_dir,
            "plan_path": plan_path}


def _predecessor_evidence_digest(predecessor: dict[str, Any]) -> str:
    """Digest a verified predecessor's evidence (its full accepted receipt),
    bound into a candidate plan at prepare time so authorize/cutover can
    detect it changing underneath them (ADR-0064 Decision §2, item 3/5)."""
    return _sha256(_canonical(predecessor["receipt"]))


def _reverify_predecessor_evidence(plan: dict[str, Any], *, control_identity: dict[str, Any],
                                   server_env: dict[str, str], server_cmdline: str,
                                   registration: dict[str, Any], server_url: str) -> None:
    """Re-run the full predecessor check bound into `plan` at prepare time.

    `prepare` verifies its named predecessor once; without this, a
    predecessor's receipt could be rewritten, its proofs turn red, or a
    rollback-receipt appear for it, *after* prepare but before authorize or
    cutover, and the candidate would still reach live mutation. No-ops for
    bootstrap-mode plans, which name no predecessor.
    """
    predecessor_info = (plan.get("supervision") or {}).get("predecessor")
    if predecessor_info is None:
        return
    predecessor = _verify_predecessor(
        Path(predecessor_info["plan_path"]),
        control_identity=control_identity,
        server_env=server_env,
        server_cmdline=server_cmdline,
        registration=registration,
        server_url=server_url,
    )
    if predecessor["plan"]["plan_digest"] != predecessor_info["plan_digest"]:
        raise ReleaseError("predecessor plan digest changed since preparation")
    if _predecessor_evidence_digest(predecessor) != predecessor_info.get("evidence_digest"):
        raise ReleaseError("predecessor evidence changed since preparation")


def _git_env() -> dict[str, str]:
    try:
        trusted_home = pwd.getpwuid(os.geteuid()).pw_dir
    except (KeyError, OSError) as exc:
        raise ReleaseError("cannot resolve trusted release HOME") from exc
    if not isinstance(trusted_home, str) or not os.path.isabs(trusted_home):
        raise ReleaseError("trusted release HOME is not absolute")
    return {
        "HOME": trusted_home,
        "PATH": GIT_SYSTEM_PATH,
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def _root_sealed_trust(repo: Path) -> list[str]:
    # A sealed checkout belongs to root, which Git calls dubious ownership.
    # Root is the one owner trusted more than ourselves, so name that exact
    # path as safe; any other foreign owner stays refused.
    try:
        owned_by_root = os.lstat(repo).st_uid == 0 != os.geteuid()
    except OSError:
        return []
    return ["-c", f"safe.directory={repo}"] if owned_by_root else []


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    # Read a sealed checkout without Git trying to refresh its index or create
    # an optional lock inside the read-only .git directory. Replacement refs
    # are disabled both by argv and environment so manifests describe the
    # authorized object graph, never a mutable refs/replace overlay.
    proc = subprocess.run(
        [
            GIT_EXECUTABLE,
            "--no-replace-objects",
            *_root_sealed_trust(repo),
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        check=False,
        env=_git_env(),
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).decode(errors="replace").strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout if binary else proc.stdout.decode()


def _git_success(repo: Path, *args: str) -> bool:
    return subprocess.run(
        [
            GIT_EXECUTABLE,
            "--no-replace-objects",
            *_root_sealed_trust(repo),
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        check=False,
        env=_git_env(),
    ).returncode == 0


def git_tree_manifest(repo: Path, sha: str,
                      subtree: str = "program") -> dict[str, Any]:
    """Digest Git blobs directly; this never reads the checkout filesystem."""
    raw = _git(repo, "ls-tree", "-rz", "--full-tree", sha, "--", subtree,
               binary=True)
    assert isinstance(raw, bytes)
    entries: list[dict[str, str]] = []
    prefix = subtree.rstrip("/") + "/"
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path_b = record.split(b"\t", 1)
        mode_b, kind_b, oid_b = meta.split(b" ", 2)
        if kind_b != b"blob":
            continue
        path = path_b.decode()
        if not path.startswith(prefix):
            raise ReleaseError(f"Git returned path outside {subtree}: {path}")
        blob = _git(repo, "cat-file", "blob", oid_b.decode(), binary=True)
        assert isinstance(blob, bytes)
        entries.append({
            "path": path[len(prefix):],
            "mode": mode_b.decode(),
            "sha256": _sha256(blob),
        })
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise ReleaseError(f"{sha}:{subtree} has no files")
    return {"algorithm": "sha256-path-mode-content-v1", "entries": entries,
            "digest": _sha256(_canonical(entries))}


def filesystem_manifest(root: Path) -> dict[str, Any]:
    """Digest a filesystem tree independently of Git's object traversal."""
    if not root.is_dir():
        raise ReleaseError(f"tree is not a directory: {root}")
    entries: list[dict[str, str]] = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(d for d in dirs if (Path(base) / d).is_symlink()):
            path = Path(base) / name
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "mode": "120000",
                "sha256": _sha256(os.readlink(path).encode()),
            })
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for name in sorted(files):
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                mode, content = "120000", os.readlink(path).encode()
            else:
                bits = path.stat().st_mode
                mode = "100755" if bits & stat.S_IXUSR else "100644"
                content = path.read_bytes()
            entries.append({"path": rel, "mode": mode,
                            "sha256": _sha256(content)})
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise ReleaseError(f"filesystem tree has no files: {root}")
    return {"algorithm": "sha256-path-mode-content-v1", "entries": entries,
            "digest": _sha256(_canonical(entries))}


def reject_escaping_tracked_symlinks(checkout: Path) -> None:
    raw = _git(checkout, "ls-files", "-s", "-z", binary=True)
    assert isinstance(raw, bytes)
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path_b = record.split(b"\t", 1)
        mode, oid, _stage = meta.decode().split()
        if mode != "120000":
            continue
        rel = PurePosixPath(path_b.decode())
        target_b = _git(checkout, "cat-file", "blob", oid, binary=True)
        assert isinstance(target_b, bytes)
        target = PurePosixPath(target_b.decode())
        if target.is_absolute():
            raise ReleaseError(f"tracked symlink is absolute: {rel} -> {target}")
        depth = 0
        for part in rel.parent.joinpath(target).parts:
            if part in ("", "."):
                continue
            if part == "..":
                depth -= 1
            else:
                depth += 1
            if depth < 0:
                raise ReleaseError(f"tracked symlink escapes checkout: "
                                   f"{rel} -> {target}")


SEALED_OWNER_UID = 0


def _sealed_owner_uids() -> set[int]:
    return {SEALED_OWNER_UID}


def _seal_fd(dir_fd: int) -> None:
    # Root-own and write-protect a directory, then its entries, through file
    # descriptors only: a rename racing the walk cannot point root's chown or
    # chmod outside the tree, and the parent is locked before its entries.
    os.fchown(dir_fd, 0, 0)
    os.fchmod(dir_fd, stat.S_IMODE(os.fstat(dir_fd).st_mode) & ~0o222)
    for name in os.listdir(dir_fd):
        if stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
            os.chown(name, 0, 0, dir_fd=dir_fd, follow_symlinks=False)
            continue
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, dir_fd=dir_fd)
        try:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                _seal_fd(fd)
            else:
                os.fchown(fd, 0, 0)
                os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) & ~0o222)
        finally:
            os.close(fd)


def seal_tree(root: Path) -> None:
    """Remove every write bit; as root, also hand the whole tree to root.

    `chmod a-w` alone is not a seal: its owner chmods it straight back, and
    the owner is the runtime uid every worker runs as (audit C6). Only a
    root-owned tree passes `tree_is_sealed`; `seal` is the root step."""
    if os.geteuid() == 0:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                     | os.O_CLOEXEC)
        try:
            _seal_fd(fd)
        finally:
            os.close(fd)
        return
    for base, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in [*files, *dirs]:
            path = Path(base) / name
            if not path.is_symlink():
                os.chmod(path, path.stat().st_mode & ~0o222)
    os.chmod(root, root.stat().st_mode & ~0o222)


def tree_is_write_protected(root: Path) -> bool:
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(base) / name
            if not path.is_symlink() and path.stat().st_mode & 0o222:
                return False
    return not bool(root.stat().st_mode & 0o222)


def tree_is_sealed(root: Path) -> bool:
    """Write-protected, owned by root throughout, and not replaceable: no
    directory above it may let anyone else rename it away (a sticky
    directory may be group-writable while the entry itself is root's)."""
    owners = _sealed_owner_uids()
    if not tree_is_write_protected(root):
        return False
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            if os.lstat(os.path.join(base, name)).st_uid not in owners:
                return False
    node = Path(os.path.realpath(root))
    child_uid = os.lstat(node).st_uid
    if child_uid not in owners:
        return False
    for parent in node.parents:
        info = os.lstat(parent)
        if info.st_uid not in owners:
            return False
        if info.st_mode & 0o022 and not (
                info.st_mode & stat.S_ISVTX and child_uid in owners):
            return False
        child_uid = info.st_uid
    return True


def _safe_extract_tar(data: bytes, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ReleaseError(f"unsafe rollback bundle member: {member.name}")
            if member.issym() or member.islnk():
                raise ReleaseError(f"rollback bundle contains a link: {member.name}")
            if not member.isdir() and not member.isfile():
                raise ReleaseError(
                    f"rollback bundle contains a special member: {member.name}"
                )
        archive.extractall(destination, members=members)
    roots = [p.parent for p in destination.rglob("config.yaml")
             if p.parent == destination or p.parent.parent == destination]
    roots = list(dict.fromkeys(roots))
    if len(roots) != 1:
        raise ReleaseError("served rollback bundle has no unambiguous root config.yaml")
    return roots[0]


def _http(method: str, url: str, body: Any | None = None,
          timeout: float = 30.0) -> tuple[int, bytes, dict[str, str]]:
    data = None if body is None else _canonical(body)
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except urllib.error.URLError as exc:
        raise ReleaseError(f"{method} {url} is unreachable: {exc.reason}") from exc


def _json_request(method: str, url: str, body: Any | None = None) -> Any:
    status, raw, _headers = _http(method, url, body)
    if status < 200 or status >= 300:
        raise ReleaseError(f"{method} {url} -> {status}: "
                           f"{raw.decode(errors='replace')[:500]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"{method} {url} returned non-JSON") from exc


def _list_data(server_url: str, endpoint: str) -> list[dict[str, Any]]:
    sep = "&" if "?" in endpoint else "?"
    obj = _json_request("GET", f"{server_url.rstrip('/')}/v1/{endpoint}{sep}limit=1000")
    data = obj.get("data") if isinstance(obj, dict) else obj
    if not isinstance(data, list):
        raise ReleaseError(f"unexpected list response from {endpoint}")
    return [item for item in data if isinstance(item, dict)]


def _download_and_hash_served_bundle(server: str, session_id: str) -> tuple[bytes, str]:
    """Extract-and-hash whatever bundle a session's Program is currently
    serving. Shared by `postflight`'s content_at_sha proof and
    `_verify_predecessor`'s live re-derivation -- both need the identical,
    independent check: never trust a stored digest, always re-download."""
    status, bundle_bytes, _headers = _http(
        "GET", f"{server}/v1/sessions/{session_id}/agent/contents", timeout=60,
    )
    if status != 200:
        raise ReleaseError(f"served contents returned HTTP {status}")
    with tempfile.TemporaryDirectory(prefix="nightshift-served-") as tmp:
        root = _safe_extract_tar(bundle_bytes, Path(tmp) / "bundle")
        served = filesystem_manifest(root)
    return bundle_bytes, served["digest"]


def _control_identity(path: Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise ReleaseError("NIGHTSHIFT_CTL_DB must be an absolute path")
    if path.is_symlink():
        raise ReleaseError("NIGHTSHIFT_CTL_DB must not be a symlink")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise ReleaseError(f"NIGHTSHIFT_CTL_DB is not canonical: {path} -> {resolved}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseError(f"control DB is not a regular file: {path}")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "size": info.st_size}


def _control_snapshot(path: Path) -> dict[str, Any]:
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
    cx = sqlite3.connect(uri, uri=True)
    cx.row_factory = sqlite3.Row
    try:
        active = {}
        for table in ("worker_slots", "task_claims", "leases"):
            active[table] = [dict(row) for row in cx.execute(
                f"SELECT * FROM {table} WHERE state='active'"  # table allowlist above
            ).fetchall()]
        tables = {row[0] for row in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        slot_columns = {row[1] for row in cx.execute(
            "PRAGMA table_info(worker_slots)"
        ).fetchall()}
        admission_schema_ready = (
            "task_admissions" in tables
            and {"phase", "expected_agent", "expected_title",
                 "expected_purpose", "generation", "run_id"} <= slot_columns
        )
        admissions = ([dict(row) for row in cx.execute(
            "SELECT * FROM task_admissions ORDER BY task_id"
        ).fetchall()] if "task_admissions" in tables else [])
        admitted = {row["task_id"] for row in admissions}
        unfinished_without_admission = [dict(row) for row in cx.execute(
            "SELECT id,state FROM tasks WHERE (state NOT IN "
            "('pending','integrated','done','merged','dropped','superseded') "
            "OR (state='pending' AND pr_url IS NOT NULL AND pr_url<>'')) "
            "ORDER BY id"
        ).fetchall() if row["id"] not in admitted]
        active_legacy_slots = [
            row for row in active["worker_slots"]
            if "phase" not in row or row.get("phase") is None
        ]
        checkpoints = [dict(row) for row in cx.execute(
            "SELECT * FROM checkpoints ORDER BY id DESC LIMIT 5"
        ).fetchall()]
        tasks = cx.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        runs = cx.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        return {"active": active, "checkpoints": checkpoints,
                "admission_schema_ready": admission_schema_ready,
                "task_admissions": admissions,
                "active_legacy_slots": active_legacy_slots,
                "unfinished_without_admission": unfinished_without_admission,
                "task_count": tasks, "agent_run_count": runs}
    finally:
        cx.close()


def _omnigent_python(explicit: str | None) -> str:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            raise ReleaseError("--omnigent-python must be an absolute path")
        # Preserve the lexical virtual-environment path.  Resolving a uv/venv
        # `bin/python` symlink to the system interpreter loses both Python's
        # virtual-environment discovery and the sibling Omnigent launcher.
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ReleaseError(
                f"--omnigent-python does not resolve to an executable file: {path}"
            )
        return str(path)
    launcher = shutil.which("omnigent")
    if not launcher:
        raise ReleaseError("omnigent is not on PATH")
    first = Path(launcher).resolve().read_text(errors="replace").splitlines()[0]
    if first.startswith("#!"):
        candidate = Path(first[2:].strip().split()[0])
        if candidate.is_file():
            return str(candidate)
    sibling = Path(launcher).resolve().parent / "python"
    if sibling.is_file():
        return str(sibling)
    raise ReleaseError("cannot resolve Omnigent's pinned Python interpreter")


def _omnigent_runtime_identity(interpreter: str, checkout: Path) -> dict[str, Any]:
    """Verify the interpreter's PEP 610 identity using the sealed checkout."""
    helper = checkout / "deploy" / "omnigent-runtime-identity.py"
    lock = checkout / "deploy" / "omnigent-runtime.lock.json"
    if not helper.is_file() or not lock.is_file():
        raise ReleaseError("candidate has no Omnigent runtime identity verifier or lock")
    output = _run(
        [interpreter, str(helper), "--lock", str(lock), "verify"],
        cwd=checkout,
    )
    try:
        identity = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ReleaseError("Omnigent runtime identity verifier returned non-JSON") from exc
    if not isinstance(identity, dict) or identity.get("matched") is not True:
        raise ReleaseError("Omnigent runtime identity verifier did not confirm a match")
    return identity


def _reverify_omnigent_runtime(plan: dict[str, Any]) -> dict[str, Any]:
    """Refuse runtime drift from the exact identity sealed into the plan."""
    observed = _omnigent_runtime_identity(
        plan["omnigent_python"], Path(plan["release_root"])
    )
    if observed != plan.get("omnigent_runtime_identity"):
        raise ReleaseError("installed Omnigent runtime identity changed after prepare")
    return observed


def _load_bundle(interpreter: str, checkout: Path) -> dict[str, Any]:
    snippet = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from omnigent.spec import load\n"
        "s=load(Path(sys.argv[1]))\n"
        "print(json.dumps({'name':s.name,'seats':sorted(x.name for x in s.sub_agents)}))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(checkout / "deploy" / "policies")
    out = _run([interpreter, "-c", snippet, str(checkout / "program")],
               cwd=checkout, env=env)
    try:
        loaded = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ReleaseError("Omnigent bundle loader returned non-JSON") from exc
    if loaded.get("name") != "program":
        raise ReleaseError(f"bundle root name is not program: {loaded.get('name')!r}")
    if not loaded.get("seats"):
        raise ReleaseError("Program bundle has no direct seats")
    return loaded


def _verify_runtime_then_load_bundle(
    interpreter: str, checkout: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prove the runtime identity before importing any candidate bundle code."""
    identity = _omnigent_runtime_identity(interpreter, checkout)
    return identity, _load_bundle(interpreter, checkout)


def _receipt(plan_digest: str) -> dict[str, Any]:
    return {
        "schema": "nightshift-exact-sha-receipt-v1",
        "plan_digest": plan_digest,
        "pid_epoch": None,
        "proofs": {name: {"verdict": "undetermined", "operands": {},
                          "reason": "not evaluated"} for name in PROOFS},
        "accepted": False,
        "updated_at": int(time.time()),
    }


def _render_server_start(wrapper: Path, *, release_root: Path, project: Path,
                         control_db: Path, server_env: Path, agent_root: Path,
                         policy_dir: Path, server_url: str, omnigent_bin: Path,
                         expected_sha: str, session_name: str) -> list[str]:
    return [
        "tmux", "new-session", "-d", "-s", session_name, "--",
        str(wrapper), "--release-root", str(release_root),
        "--expected-sha", expected_sha,
        "--project", str(project), "--control-db", str(control_db),
        "--server-env", str(server_env), "--agent-root", str(agent_root),
        "--policy-dir", str(policy_dir), "--server", server_url,
        "--omnigent-bin", str(omnigent_bin),
    ]


def _render_host_start(wrapper: Path, *, release_root: Path, project: Path,
                       control_db: Path, policy_dir: Path, server_url: str,
                       omnigent_bin: Path, expected_sha: str,
                       session_name: str) -> list[str]:
    return [
        "tmux", "new-session", "-d", "-s", session_name, "--",
        str(wrapper), "--release-root", str(release_root),
        "--expected-sha", expected_sha,
        "--project", str(project), "--control-db", str(control_db),
        "--policy-dir", str(policy_dir), "--server", server_url,
        "--omnigent-bin", str(omnigent_bin),
    ]


def _pin_schedule_prompt(prompt: str, release_root: Path) -> tuple[str, int]:
    relative = "./bin/nightshift-ctl"
    count = prompt.count(relative)
    if count < 1:
        raise ReleaseError(
            "heartbeat prompt contains no reviewed relative nightshift-ctl path"
        )
    absolute = str(release_root / "bin" / "nightshift-ctl")
    pinned = prompt.replace(relative, absolute)
    if relative in pinned or pinned.count(absolute) != count:
        raise ReleaseError("heartbeat prompt exact-path rewrite was not one-for-one")
    return pinned, count


def _render_rollback_command(plan: dict[str, Any], plan_path: Path) -> list[str]:
    return [
        str(Path(plan["release_root"]) / "bin" / "nightshift-release"),
        "rollback", "--plan", str(plan_path),
        "--owner-go", plan["plan_digest"],
    ]


def prepare(args: argparse.Namespace) -> Path:
    sha = args.sha.lower()
    if not FULL_SHA.fullmatch(sha):
        raise ReleaseError("--sha must be a full lowercase 40-hex Git SHA")
    floor = args.floor_sha.lower()
    if not FULL_SHA.fullmatch(floor):
        raise ReleaseError("--floor-sha must be a full lowercase 40-hex Git SHA")
    release_base = Path(args.release_base)
    state_base = Path(args.state_base)
    project = Path(args.project)
    control_db = Path(args.control_db)
    server_env = Path(args.server_env)
    rollback_policy_dir = Path(args.rollback_policy_dir)
    server = args.server_url.rstrip("/")
    for label, path in (("release base", release_base), ("state base", state_base),
                        ("project", project), ("server env", server_env),
                        ("rollback policy dir", rollback_policy_dir)):
        if not path.is_absolute():
            raise ReleaseError(f"{label} must be absolute: {path}")
    if (not project.is_dir() or not server_env.is_file()
            or not rollback_policy_dir.is_dir()
            or rollback_policy_dir.resolve(strict=True) != rollback_policy_dir):
        raise ReleaseError("project, server environment, or rollback policy path is invalid")
    control_identity = _control_identity(control_db)
    control_before = _control_snapshot(control_db)
    pre_pid = _serving_pid(server)
    pre_runtime = {
        "pid": pre_pid,
        "pid_epoch": _pid_epoch(pre_pid),
        "cwd": str(Path(f"/proc/{pre_pid}/cwd").resolve(strict=True)),
        "cmdline": Path(f"/proc/{pre_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(),
        "environment": _selected_process_env(pre_pid),
    }
    if pre_runtime["cwd"] != str(project):
        raise ReleaseError("live Omnigent server CWD differs from durable project")
    if pre_runtime["environment"].get("PYTHONPATH") != str(rollback_policy_dir):
        raise ReleaseError(
            "--rollback-policy-dir does not match the live server PYTHONPATH"
        )

    predecessor: dict[str, Any] | None = None
    if getattr(args, "predecessor_plan", None):
        predecessor_rows = [row for row in _list_data(server, "agents")
                            if row.get("name") == "program" and not row.get("builtin")]
        if len(predecessor_rows) != 1:
            raise ReleaseError(
                "expected exactly one durable Program registration to check "
                f"against a predecessor, got {len(predecessor_rows)}"
            )
        predecessor_registration = {key: predecessor_rows[0].get(key) for key in
                                    ("id", "name", "version", "created_at", "updated_at")}
        predecessor = _verify_predecessor(
            Path(args.predecessor_plan),
            control_identity=control_identity,
            server_env=pre_runtime["environment"],
            server_cmdline=pre_runtime["cmdline"],
            registration=predecessor_registration,
            server_url=server,
        )
    if predecessor is None:
        mode = "bootstrap"
        inherited_masked: list[dict[str, Any]] = []
        to_mask = _legacy_unit_inventory()
    else:
        mode = "subsequent"
        inherited_masked, to_mask = _subsequent_unit_transition(
            _predecessor_masked_names(predecessor["plan"])
        )
    legacy_units = sorted(inherited_masked + to_mask, key=lambda row: row["name"])
    supervision = {
        "schema": SUPERVISION_SCHEMA,
        "mode": mode,
        "predecessor": None if predecessor is None else {
            "plan_path": str(predecessor["plan_path"]),
            "state_dir": str(predecessor["state_dir"]),
            "plan_digest": predecessor["plan"]["plan_digest"],
            "sha": predecessor["plan"]["sha"],
            # Bound at prepare time so authorize/cutover can refuse if the
            # predecessor's evidence (its receipt, or a rollback-receipt
            # appearing) changes underneath this candidate later.
            "evidence_digest": _predecessor_evidence_digest(predecessor),
        },
        "inherited_masked": inherited_masked,
        "to_mask": to_mask,
    }

    release_dir = release_base / sha
    checkout = release_dir / "checkout"
    staging = release_base / f".{sha}.staging.{os.getpid()}"
    state_dir = state_base / sha
    existing_plan = state_dir / "plan.json"
    if existing_plan.is_file():
        plan = _load_plan(existing_plan)
        if (plan.get("sha") != sha or plan.get("floor_sha") != floor
                or plan.get("release_root") != str(checkout)
                or plan.get("control_db", {}).get("path") != str(control_db)
                or plan.get("server_url") != server
                or plan.get("project") != str(project)
                or plan.get("scheduled_task_id") != args.scheduled_task_id
                or plan.get("rollback", {}).get("policy_source") != str(rollback_policy_dir)
                or plan.get("source") != args.source
                or (plan.get("supervision") or {}).get("mode") != mode
                or ((plan.get("supervision") or {}).get("predecessor") or {}).get(
                    "plan_digest"
                ) != (predecessor["plan"]["plan_digest"] if predecessor else None)):
            raise ReleaseError("existing plan does not match the requested operands")
        _reverify_omnigent_runtime(plan)
        print(f"exact-SHA release already prepared: {sha}")
        print(f"plan: {existing_plan}")
        print(f"plan digest: {plan['plan_digest']}")
        print(f"forward launch: {shlex.join(plan['launch_argv'])}")
        print(f"forward host: {shlex.join(plan['host_launch_argv'])}")
        print(f"rollback: {shlex.join(_render_rollback_command(plan, existing_plan))}")
        return existing_plan
    release_base.mkdir(parents=True, exist_ok=True)
    interpreter = _omnigent_python(args.omnigent_python)
    omnigent_launcher = Path(interpreter).parent / "omnigent"
    try:
        omnigent_bin = omnigent_launcher.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(
            f"Omnigent launcher beside the pinned interpreter is unreadable: "
            f"{omnigent_launcher}"
        ) from exc
    if not omnigent_bin.is_file() or not os.access(omnigent_bin, os.X_OK):
        raise ReleaseError(
            f"Omnigent launcher is not an executable file: {omnigent_bin}"
        )
    if release_dir.exists():
        if not checkout.is_dir() or not (checkout / ".git").is_dir():
            raise ReleaseError(f"existing release is not a whole-tree checkout: {release_dir}")
        if _git(checkout, "rev-parse", "HEAD").strip() != sha:
            raise ReleaseError("existing release HEAD does not match requested SHA")
        if _git(checkout, "status", "--porcelain=v1").strip():
            raise ReleaseError("existing release checkout is dirty")
        reject_escaping_tracked_symlinks(checkout)
        git_program = git_tree_manifest(checkout, sha)
        fs_program = filesystem_manifest(checkout / "program")
        if git_program["digest"] != fs_program["digest"]:
            raise ReleaseError("existing Git-object and filesystem Program digests differ")
        git_policy = git_tree_manifest(checkout, sha, "deploy/policies")
        fs_policy = filesystem_manifest(checkout / "deploy" / "policies")
        if git_policy["digest"] != fs_policy["digest"]:
            raise ReleaseError("existing Git-object and filesystem policy digests differ")
        omnigent_runtime_identity, bundle = _verify_runtime_then_load_bundle(
            interpreter, checkout
        )
        if not tree_is_write_protected(checkout):
            raise ReleaseError("existing release checkout is not sealed")
    else:
        if staging.exists():
            raise ReleaseError(f"staging path already exists: {staging}")
        staging.mkdir(mode=0o700)
        staged_checkout = staging / "checkout"
        try:
            _run(
                [
                    GIT_EXECUTABLE,
                    "--no-replace-objects",
                    "clone",
                    "--no-checkout",
                    "--origin",
                    "origin",
                    args.source,
                    str(staged_checkout),
                ],
                env=_git_env(),
            )
            _git(staged_checkout, "fetch", "--force", "origin",
                 "refs/heads/main:refs/remotes/origin/main")
            _git(staged_checkout, "cat-file", "-e", f"{sha}^{{commit}}")
            if not _git_success(
                    staged_checkout, "merge-base", "--is-ancestor",
                    sha, "origin/main"):
                raise ReleaseError("candidate SHA is not on origin/main")
            if not _git_success(
                    staged_checkout, "merge-base", "--is-ancestor",
                    floor, sha):
                raise ReleaseError("candidate SHA is below the release floor")
            _git(staged_checkout, "checkout", "--detach", sha)
            if _git(staged_checkout, "rev-parse", "HEAD").strip() != sha:
                raise ReleaseError("detached checkout HEAD does not match requested SHA")
            if _git(staged_checkout, "status", "--porcelain=v1").strip():
                raise ReleaseError("candidate checkout is dirty immediately after checkout")
            reject_escaping_tracked_symlinks(staged_checkout)
            git_program = git_tree_manifest(staged_checkout, sha)
            fs_program = filesystem_manifest(staged_checkout / "program")
            if git_program["digest"] != fs_program["digest"]:
                raise ReleaseError("Git-object and filesystem Program digests differ")
            git_policy = git_tree_manifest(staged_checkout, sha, "deploy/policies")
            fs_policy = filesystem_manifest(staged_checkout / "deploy" / "policies")
            if git_policy["digest"] != fs_policy["digest"]:
                raise ReleaseError("Git-object and filesystem policy digests differ")
            omnigent_runtime_identity, bundle = _verify_runtime_then_load_bundle(
                interpreter, staged_checkout
            )
            seal_tree(staged_checkout)
            os.replace(staging, release_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    checkout = release_dir / "checkout"
    if not tree_is_write_protected(checkout):
        raise ReleaseError("release checkout did not seal read-only")

    rows = [row for row in _list_data(server, "agents")
            if row.get("name") == "program" and not row.get("builtin")]
    if len(rows) != 1:
        raise ReleaseError(f"expected exactly one durable Program registration, got {len(rows)}")
    registration = {key: rows[0].get(key) for key in
                    ("id", "name", "version", "created_at", "updated_at")}
    scheduled_task = _json_request(
        "GET", f"{server}/v1/scheduled-tasks/{args.scheduled_task_id}"
    )
    original_prompt = scheduled_task.get("prompt")
    if not isinstance(original_prompt, str) or not original_prompt:
        raise ReleaseError("heartbeat scheduled task has no durable prompt")
    pinned_prompt, prompt_replacements = _pin_schedule_prompt(
        original_prompt, checkout
    )
    sessions = [row for row in _list_data(server, "sessions")
                if row.get("agent_id") == registration["id"]]
    if not sessions:
        raise ReleaseError("no persisted Program session exists for rollback/continuity proof")
    sessions.sort(key=lambda row: (row.get("created_at") or 0), reverse=True)
    durable_session = sessions[0]
    if not durable_session.get("host_id"):
        raise ReleaseError("durable Program session has no production host binding")
    contents_url = f"{server}/v1/sessions/{durable_session['id']}/agent/contents"
    status, rollback_bytes, _headers = _http("GET", contents_url, timeout=60)
    if status != 200 or not rollback_bytes.startswith(b"\x1f\x8b"):
        raise ReleaseError(f"served rollback bundle download failed: HTTP {status}")

    if state_dir.exists():
        raise ReleaseError(f"release state already exists; refusing overwrite: {state_dir}")
    state_dir.mkdir(parents=True, mode=0o700)
    rollback_tar = state_dir / "rollback-agent.tar.gz"
    rollback_tar.write_bytes(rollback_bytes)
    os.chmod(rollback_tar, 0o400)
    rollback_extract = state_dir / "rollback-bundle"
    rollback_root = _safe_extract_tar(rollback_bytes, rollback_extract)
    rollback_manifest = filesystem_manifest(rollback_root)
    rollback_policy_source = rollback_policy_dir
    rollback_policy_dir = state_dir / "rollback-policies"
    shutil.copytree(rollback_policy_source, rollback_policy_dir)
    rollback_policy_manifest = filesystem_manifest(rollback_policy_dir)
    seal_tree(rollback_extract)
    seal_tree(rollback_policy_dir)

    fetched_main = _git(checkout, "rev-parse", "origin/main").strip()
    omnigent_version = _run([str(omnigent_bin), "--version"]).strip()
    server_wrapper = checkout / "deploy" / "run-exact-sha-server.sh"
    host_wrapper = checkout / "deploy" / "run-exact-sha-host.sh"
    launch = _render_server_start(
        server_wrapper, release_root=checkout, project=project, control_db=control_db,
        server_env=server_env, agent_root=checkout / "program",
        policy_dir=checkout / "deploy" / "policies", server_url=server,
        omnigent_bin=omnigent_bin,
        expected_sha=sha,
        session_name=f"nightshift-release-{sha[:12]}",
    )
    host_launch = _render_host_start(
        host_wrapper, release_root=checkout, project=project,
        control_db=control_db, policy_dir=checkout / "deploy" / "policies",
        server_url=server, omnigent_bin=omnigent_bin,
        expected_sha=sha,
        session_name=f"nightshift-release-host-{sha[:12]}",
    )
    rollback = _render_server_start(
        server_wrapper, release_root=checkout, project=project, control_db=control_db,
        server_env=server_env, agent_root=rollback_root,
        policy_dir=rollback_policy_dir, server_url=server,
        omnigent_bin=omnigent_bin,
        expected_sha=sha,
        session_name=f"nightshift-rollback-{sha[:12]}",
    )
    rollback_host = _render_host_start(
        host_wrapper, release_root=checkout, project=project,
        control_db=control_db, policy_dir=rollback_policy_dir,
        server_url=server, omnigent_bin=omnigent_bin,
        expected_sha=sha,
        session_name=f"nightshift-rollback-host-{sha[:12]}",
    )
    plan: dict[str, Any] = {
        "schema": "nightshift-exact-sha-plan-v1",
        "created_at": int(time.time()),
        "sha": sha,
        "floor_sha": floor,
        "fetched_main_sha": fetched_main,
        "source": args.source,
        "release_root": str(checkout),
        "state_dir": str(state_dir),
        "project": str(project),
        "control_db": control_identity,
        "control_before": control_before,
        "server_env": str(server_env),
        "server_url": server,
        "pre_runtime": pre_runtime,
        "legacy_units": legacy_units,
        "supervision": supervision,
        "scheduled_task_id": args.scheduled_task_id,
        "scheduled_task": {
            "id": scheduled_task.get("id"),
            "state": scheduled_task.get("state"),
            "next_run_at": scheduled_task.get("next_run_at"),
            "original_prompt": original_prompt,
            "original_prompt_sha256": _sha256(original_prompt.encode()),
            "pinned_prompt": pinned_prompt,
            "pinned_prompt_sha256": _sha256(pinned_prompt.encode()),
            "replacement_count": prompt_replacements,
        },
        "omnigent_python": interpreter,
        "omnigent_bin": str(omnigent_bin),
        "omnigent_version": omnigent_version,
        "omnigent_runtime_identity": omnigent_runtime_identity,
        "bundle": bundle,
        "program_git_manifest": git_program,
        "program_filesystem_manifest": fs_program,
        "policy_git_manifest": git_policy,
        "policy_filesystem_manifest": fs_policy,
        "pre_registration": registration,
        "pre_registration_row_count": len(rows),
        "durable_session": {key: durable_session.get(key) for key in
                            ("id", "status", "created_at", "updated_at", "title",
                             "host_id", "runner_id")},
        "rollback": {
            "bundle_tar": str(rollback_tar),
            "bundle_sha256": _sha256(rollback_bytes),
            "agent_root": str(rollback_root),
            "policy_dir": str(rollback_policy_dir),
            "policy_source": str(rollback_policy_source),
            "policy_manifest": rollback_policy_manifest,
            "manifest": rollback_manifest,
        },
        "expected_responses": {"architect": ECHO_RESPONSE,
                               "staff": CLAUDE_MARKER},
        "launch_argv": launch,
        "host_launch_argv": host_launch,
        "rollback_argv": rollback,
        "rollback_host_argv": rollback_host,
    }
    plan_digest = _sha256(_canonical(plan))
    plan["plan_digest"] = plan_digest
    plan_path = state_dir / "plan.json"
    _atomic_json(plan_path, plan)
    _atomic_json(state_dir / "receipt.json", _receipt(plan_digest))
    print(f"prepared exact-SHA release: {sha}")
    print(f"plan: {plan_path}")
    print(f"plan digest: {plan_digest}")
    print(f"forward launch: {shlex.join(launch)}")
    print(f"forward host: {shlex.join(host_launch)}")
    print(f"rollback: {shlex.join(_render_rollback_command(plan, plan_path))}")
    print(f"seal (as root, before authorize): {checkout}/bin/nightshift-release "
          f"seal --plan {plan_path}")
    print("LIVE STATE UNCHANGED. Run authorize only at the quiesced window.")
    return plan_path


def _load_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read plan: {path}") from exc
    digest = plan.pop("plan_digest", None)
    actual = _sha256(_canonical(plan))
    plan["plan_digest"] = digest
    if not isinstance(digest, str) or digest != actual:
        raise ReleaseError("plan digest mismatch; refusing stale or edited plan")
    return plan


def _pid_epoch(pid: int) -> str:
    try:
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1]
        return tail.split()[19]
    except (OSError, IndexError) as exc:
        raise ReleaseError(f"cannot read server PID epoch for {pid}") from exc


def _serving_pid(server_url: str) -> int:
    parsed = urllib.parse.urlparse(server_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise ReleaseError(f"server URL is not an explicit loopback port: {server_url}")
    port = parsed.port
    out = _run(["ss", "-H", "-ltnp", f"sport = :{port}"])
    pids = {int(value) for value in re.findall(r"pid=(\d+)", out)}
    if len(pids) != 1:
        raise ReleaseError(f"expected one process listening on :{port}, got {sorted(pids)}")
    return next(iter(pids))


def _host_status(plan: dict[str, Any]) -> dict[str, Any]:
    omnigent = plan["omnigent_bin"]
    raw = _run([
        omnigent, "host", "status", "--server", plan["server_url"], "--json",
    ])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseError("omnigent host status returned non-JSON") from exc
    daemons = [
        row for row in payload.get("daemons", [])
        if isinstance(row, dict)
        and row.get("server_url", "").rstrip("/") == plan["server_url"].rstrip("/")
    ]
    if len(daemons) != 1:
        raise ReleaseError(
            f"expected one host-daemon record for server, got {len(daemons)}"
        )
    return daemons[0]


def _runner_pids(host_pid: int) -> list[int]:
    runners: list[int] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            status = (proc / "status").read_text()
            match = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeDecodeError):
            continue
        if match and int(match.group(1)) == host_pid and "omnigent.runner._zygote" in cmdline:
            runners.append(int(proc.name))
    return sorted(runners)


def _selected_process_env(pid: int) -> dict[str, str]:
    selected = {
        "HOME", "PATH", "PYTHONPATH", "NIGHTSHIFT_CTL_DB",
        "NIGHTSHIFT_CONTROL_DB",
        "NIGHTSHIFT_RELEASE_ROOT", "NIGHTSHIFT_RELEASE_SHA",
        "NIGHTSHIFT_SEMAPHORE_FAIL_OPEN",
        "HARNESS_CODEX_DISABLE_NATIVE_TOOLS", "HARNESS_CODEX_ENABLE_WEB_SEARCH",
    }
    try:
        items = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError as exc:
        raise ReleaseError(f"cannot inspect process environment for PID {pid}") from exc
    result: dict[str, str] = {}
    for item in items:
        if b"=" not in item:
            continue
        key_b, value_b = item.split(b"=", 1)
        key = key_b.decode(errors="replace")
        if key in selected:
            result[key] = value_b.decode(errors="replace")
    return result


def _runtime_environments_match(
        environments: dict[str, dict[str, str]],
        expected: dict[str, str], release_bin: str,
        codex_host_binding: dict[str, str]) -> bool:
    """Verify live server/host/runner bindings, including Codex confinement."""
    if set(environments) != {"server", "host", "runner"}:
        return False
    if any("NIGHTSHIFT_CONTROL_DB" in environment
           for environment in environments.values()):
        return False
    if any("NIGHTSHIFT_SEMAPHORE_FAIL_OPEN" in environment
           for environment in environments.values()):
        return False
    if not all(
            all(environment.get(key) == value
                for key, value in expected.items())
            and environment.get("PATH", "").split(":", 1)[0] == release_bin
            for environment in environments.values()):
        return False
    return all(
        all(environments[process].get(key) == value
            for key, value in codex_host_binding.items())
        for process in ("host", "runner")
    )


def _wait_for_host(plan: dict[str, Any], *, wanted: bool,
                   timeout: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = _host_status(plan)
            online = status.get("process") == "online" and status.get("host_status") == "online"
            if online == wanted:
                return status
        except ReleaseError:
            if not wanted:
                return None
        time.sleep(0.5)
    return None


def authorize(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.plan).resolve(strict=True)
    plan = _load_plan(plan_path)
    release_root = Path(plan["release_root"])
    sha = plan["sha"]
    if _git(release_root, "rev-parse", "HEAD").strip() != sha:
        raise ReleaseError("release checkout HEAD changed")
    if _git(release_root, "status", "--porcelain=v1").strip():
        raise ReleaseError("release checkout is dirty")
    if not tree_is_sealed(release_root):
        raise ReleaseError(
            "release checkout is writable or not root-owned; as root run: "
            f"{release_root}/bin/nightshift-release seal --plan {plan_path}")
    current_inherited, current_to_mask = _current_legacy_transition(plan)
    if (current_inherited != plan["supervision"]["inherited_masked"]
            or current_to_mask != plan["supervision"]["to_mask"]):
        raise ReleaseError("legacy supervision unit inventory changed since preparation")
    current_units = sorted(current_inherited + current_to_mask, key=lambda row: row["name"])
    active_units = [row["name"] for row in current_to_mask
                    if row.get("active_state") == "active"]
    if active_units:
        raise ReleaseError(f"legacy supervision units are active: {active_units}")
    fs = filesystem_manifest(release_root / "program")
    if fs["digest"] != plan["program_git_manifest"]["digest"]:
        raise ReleaseError("sealed Program digest no longer matches plan")
    policy_fs = filesystem_manifest(release_root / "deploy" / "policies")
    if policy_fs["digest"] != plan["policy_git_manifest"]["digest"]:
        raise ReleaseError("sealed policy digest no longer matches reviewed Git objects")
    db = Path(plan["control_db"]["path"])
    identity = _control_identity(db)
    if any(identity[key] != plan["control_db"][key] for key in ("device", "inode")):
        raise ReleaseError("control DB device/inode changed since preparation")
    control = _control_snapshot(db)
    if not control["admission_schema_ready"]:
        raise ReleaseError("ADR-0033 admission schema is not migrated")
    if control["active_legacy_slots"]:
        raise ReleaseError("active legacy worker slots require operator reconciliation")
    if control["unfinished_without_admission"]:
        ids = [row["id"] for row in control["unfinished_without_admission"]]
        raise ReleaseError(f"unfinished legacy tasks lack admission receipts: {ids}")
    held = {name: rows for name, rows in control["active"].items() if rows}
    if held:
        raise ReleaseError(f"control plane is not quiesced: {sorted(held)}")
    server = plan["server_url"]
    rows = [row for row in _list_data(server, "agents")
            if row.get("name") == "program" and not row.get("builtin")]
    if (len(rows) != plan["pre_registration_row_count"]
            or len(rows) != 1
            or rows[0].get("id") != plan["pre_registration"]["id"]):
        raise ReleaseError("Program registration id/row count drifted since preparation")
    current_registration = {key: rows[0].get(key) for key in
                            ("id", "name", "version", "created_at", "updated_at")}
    if current_registration.get("version") != plan["pre_registration"].get("version"):
        rollback_receipt = Path(plan["state_dir"]) / "rollback-receipt.json"
        try:
            rollback = json.loads(rollback_receipt.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError(
                "Program version drift has no verified rollback receipt; prepare a new plan"
            ) from exc
        if (not rollback.get("green")
                or rollback.get("expected_digest") != plan["rollback"]["manifest"]["digest"]
                or rollback.get("served_digest") != plan["rollback"]["manifest"]["digest"]):
            raise ReleaseError("Program version drift is not explained by a verified rollback")
    sessions = _list_data(server, "sessions")
    busy = [{"id": row.get("id"), "status": row.get("status"),
             "title": row.get("title")} for row in sessions
            if row.get("status") in BUSY_SESSION_STATES]
    if busy:
        raise ReleaseError(f"Omnigent sessions are still active: {busy[:10]}")
    task = _json_request(
        "GET", f"{server}/v1/scheduled-tasks/{plan['scheduled_task_id']}"
    )
    if task.get("state") != "paused" or task.get("next_run_at") is not None:
        raise ReleaseError("heartbeat scheduler is not paused")
    prompt = task.get("prompt")
    if (not isinstance(prompt, str)
            or _sha256(prompt.encode()) != plan["scheduled_task"]["original_prompt_sha256"]):
        raise ReleaseError("heartbeat prompt changed since release preparation")
    pid = _serving_pid(plan["server_url"])
    epoch = _pid_epoch(pid)
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    host = _host_status(plan)
    if host.get("process") != "online" or host.get("host_status") != "online":
        raise ReleaseError("production host daemon is not online")
    if host.get("host_id") != plan["durable_session"]["host_id"]:
        raise ReleaseError("online host id differs from durable Program session")
    host_pid = int(host["pid"])
    host_epoch = _pid_epoch(host_pid)
    runners = _runner_pids(host_pid)
    if len(runners) != 1:
        raise ReleaseError(f"expected one host runner zygote, got {runners}")
    _reverify_predecessor_evidence(
        plan, control_identity=identity, server_env=_selected_process_env(pid),
        server_cmdline=cmdline, registration=current_registration, server_url=server,
    )
    authorization = {
        "authorized_at": int(time.time()),
        "plan_digest": plan["plan_digest"],
        "old_pid": pid,
        "old_pid_epoch": epoch,
        "old_cmdline": cmdline,
        "old_host_pid": host_pid,
        "old_host_pid_epoch": host_epoch,
        "old_host_id": host.get("host_id"),
        "old_runner_pids": runners,
        "control": control,
        "pre_registration": current_registration,
        "legacy_units": current_units,
        "busy_sessions": busy,
        "scheduled_task": {key: task.get(key) for key in
                           ("id", "state", "last_run_conversation_id", "next_run_at")},
    }
    auth_path = Path(plan["state_dir"]) / "authorization.json"
    _atomic_json(auth_path, authorization)
    cutover = [str(release_root / "bin" / "nightshift-release"), "cutover",
               "--plan", str(plan_path), "--authorization", str(auth_path),
               "--owner-go", plan["plan_digest"]]
    rollback = _render_rollback_command(plan, plan_path)
    print("AUTHORIZED: no live mutation performed")
    print(f"server PID epoch: {pid}/{epoch}")
    print(f"host PID epoch: {host_pid}/{host_epoch}; runner PID: {runners[0]}")
    print(f"cutover (requires owner GO): {shlex.join(cutover)}")
    print(f"rollback: {shlex.join(rollback)}")
    return authorization


def _health(server: str) -> bool:
    status, _raw, _headers = _http("GET", f"{server.rstrip('/')}/health", timeout=3)
    return status == 200


def _wait_for_health(server: str, *, wanted: bool, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _health(server) == wanted:
                return True
        except (OSError, ReleaseError):
            if not wanted:
                return True
        time.sleep(0.5)
    return False


def _stop_exact_pid(pid: int, epoch: str, timeout: float = 60.0,
                    label: str = "process") -> None:
    if _pid_epoch(pid) != epoch:
        raise ReleaseError(f"{label} PID epoch changed; refusing to signal a reused PID")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        try:
            if _pid_epoch(pid) != epoch:
                return
        except ReleaseError:
            return
        time.sleep(0.5)
    raise ReleaseError(f"{label} PID {pid} did not exit after SIGTERM; no SIGKILL sent")


def _mask_legacy_units(state_dir: Path,
                       expected: list[dict[str, Any]]) -> dict[str, Any]:
    """Mask exactly `expected` (the plan's `to_mask` delta). Inherited masks
    are never touched here -- ADR-0064 Decision §4."""
    names = {row["name"] for row in expected}
    records, _contents = _legacy_unit_records(names)
    current = [records[name] for name in sorted(names)]
    if current != expected:
        raise ReleaseError("legacy unit inventory changed before masking")
    active = [row["name"] for row in current
              if row.get("active_state") == "active"]
    if active:
        raise ReleaseError(f"legacy units became active before masking: {active}")
    backup_dir = state_dir / "supervision"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for row in current:
        path = Path(row["path"])
        backup = backup_dir / f"{row['name']}.pre-cutover"
        if path.is_file() and not path.is_symlink():
            if backup.exists() and backup.read_bytes() != path.read_bytes():
                raise ReleaseError(f"legacy unit backup differs: {backup}")
            if not backup.exists():
                shutil.copy2(path, backup)
                os.chmod(backup, 0o400)

    def restore() -> None:
        for row in current:
            path = Path(row["path"])
            if path.is_symlink() or path.exists():
                path.unlink()
            backup = backup_dir / f"{row['name']}.pre-cutover"
            if row.get("sha256") is not None:
                shutil.copy2(backup, path)
            elif row.get("symlink_target") is not None:
                path.symlink_to(row["symlink_target"])
        _systemctl_user("daemon-reload")

    results: list[dict[str, Any]] = []
    try:
        _atomic_json(backup_dir / "mask-started.json", {
            "started_at": int(time.time()),
            "units": [row["name"] for row in current],
        })
        for row in current:
            path = Path(row["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or path.exists():
                path.unlink()
            path.symlink_to("/dev/null")
        _systemctl_user("daemon-reload")
        for row in current:
            enabled = _systemctl_user("is-enabled", row["name"], check=False)
            if enabled != "masked":
                raise ReleaseError(
                    f"legacy unit did not become masked: {row['name']}={enabled!r}"
                )
            results.append({
                "name": row["name"], "path": row["path"],
                "backup": str(backup_dir / f"{row['name']}.pre-cutover"),
                "state": enabled, "previous_active": row.get("active_state"),
            })
    except Exception:
        restore()
        raise
    return {"units": results}


def _restore_legacy_units(state_dir: Path,
                          expected: list[dict[str, Any]]) -> None:
    backup_dir = state_dir / "supervision"
    for row in expected:
        path = Path(row["path"])
        if not path.is_symlink() or os.readlink(path) != "/dev/null":
            raise ReleaseError(f"refusing to restore an unexpectedly changed unit: {path}")
        backup = backup_dir / f"{row['name']}.pre-cutover"
        if (row.get("sha256") is not None
                and (not backup.is_file()
                     or _sha256(backup.read_bytes()) != row["sha256"])):
            raise ReleaseError(f"legacy unit backup is missing or changed: {backup}")
    for row in expected:
        path = Path(row["path"])
        path.unlink()
        backup = backup_dir / f"{row['name']}.pre-cutover"
        if row.get("sha256") is not None:
            shutil.copy2(backup, path)
        elif row.get("symlink_target") is not None:
            path.symlink_to(row["symlink_target"])
    _systemctl_user("daemon-reload")
    names = {row["name"] for row in expected}
    records, _contents = _legacy_unit_records(names)
    current = [records[name] for name in sorted(names)]
    if current != expected:
        raise ReleaseError("restored legacy supervision inventory differs from preparation")


def _legacy_units_masked(expected: list[dict[str, Any]]) -> bool:
    return all(
        Path(row["path"]).is_symlink()
        and os.readlink(Path(row["path"])) == "/dev/null"
        for row in expected
    )


def _restore_legacy_units_if_masked(plan: dict[str, Any]) -> None:
    """Restore only the plan's `to_mask` delta -- inherited masks are left
    untouched either way (ADR-0064 Decision §4)."""
    supervision = plan.get("supervision")
    expected = supervision["to_mask"] if isinstance(supervision, dict) else plan["legacy_units"]
    if _legacy_units_masked(expected):
        _restore_legacy_units(Path(plan["state_dir"]), expected)
    else:
        names = {row["name"] for row in expected}
        records, _contents = _legacy_unit_records(names)
        current = [records[name] for name in sorted(names)]
        if current != expected:
            raise ReleaseError("legacy units are neither fully masked nor at prepared state")


def _start_tmux(argv: list[str]) -> None:
    session_name = argv[argv.index("-s") + 1]
    exists = subprocess.run(["tmux", "has-session", "-t", session_name],
                            capture_output=True, check=False).returncode == 0
    if exists:
        raise ReleaseError(f"tmux session already exists: {session_name}")
    _run(argv)


def _kill_own_tmux(argv: list[str]) -> None:
    session_name = argv[argv.index("-s") + 1]
    subprocess.run(["tmux", "kill-session", "-t", session_name],
                   capture_output=True, check=False)


def _wait_pids_gone(pids: list[int], timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.remove(pid)
        if remaining:
            time.sleep(0.5)
    return not remaining


def _start_runtime_pair(plan: dict[str, Any], *, rollback: bool) -> dict[str, Any]:
    _reverify_omnigent_runtime(plan)
    server_argv = plan["rollback_argv" if rollback else "launch_argv"]
    host_argv = plan["rollback_host_argv" if rollback else "host_launch_argv"]
    _start_tmux(server_argv)
    if not _wait_for_health(plan["server_url"], wanted=True, timeout=120):
        _kill_own_tmux(server_argv)
        raise ReleaseError("server did not become healthy")
    try:
        _start_tmux(host_argv)
        host = _wait_for_host(plan, wanted=True, timeout=120)
        if host is None:
            raise ReleaseError("host daemon did not become online")
        if host.get("host_id") != plan["durable_session"]["host_id"]:
            raise ReleaseError("restarted host did not preserve production host id")
        host_pid = int(host["pid"])
        runners: list[int] = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            runners = _runner_pids(host_pid)
            if len(runners) == 1:
                break
            time.sleep(0.5)
        if len(runners) != 1:
            raise ReleaseError(f"restarted host has unexpected runner zygotes: {runners}")
        return {"server_pid": _serving_pid(plan["server_url"]),
                "host": host, "runner_pids": runners}
    except Exception:
        _kill_own_tmux(host_argv)
        _kill_own_tmux(server_argv)
        _wait_for_host(plan, wanted=False, timeout=20)
        if not _wait_for_health(plan["server_url"], wanted=False, timeout=20):
            raise ReleaseError("failed runtime pair did not release its server socket")
        raise


def _stop_runtime_pair(plan: dict[str, Any], *, server_pid: int,
                       server_epoch: str, host_pid: int, host_epoch: str,
                       runner_pids: list[int], rollback: bool) -> None:
    host_argv = plan["rollback_host_argv" if rollback else "host_launch_argv"]
    server_argv = plan["rollback_argv" if rollback else "launch_argv"]
    _stop_exact_pid(host_pid, host_epoch, label="host")
    if not _wait_pids_gone(runner_pids):
        raise ReleaseError(
            f"runner zygote did not exit with host: {runner_pids}; no extra signal sent"
        )
    _wait_for_host(plan, wanted=False, timeout=20)
    _kill_own_tmux(host_argv)
    _stop_exact_pid(server_pid, server_epoch, label="server")
    if not _wait_for_health(plan["server_url"], wanted=False, timeout=20):
        raise ReleaseError("server socket stayed healthy after server PID exited")
    _kill_own_tmux(server_argv)


def _reverify_release_candidate(plan: dict[str, Any]) -> None:
    """Rebind the sealed filesystem and original Git objects to the plan."""
    release_root = Path(plan["release_root"])
    sha = plan.get("sha")
    if not isinstance(sha, str) or not FULL_SHA.fullmatch(sha):
        raise ReleaseError("release plan SHA is invalid")
    head = _git(release_root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if head != sha:
        raise ReleaseError("release checkout HEAD changed before cutover")
    if _git(release_root, "status", "--porcelain=v1").strip():
        raise ReleaseError("release checkout became dirty before cutover")

    program_git = git_tree_manifest(release_root, sha)
    program_fs = filesystem_manifest(release_root / "program")
    policy_git = git_tree_manifest(release_root, sha, "deploy/policies")
    policy_fs = filesystem_manifest(release_root / "deploy" / "policies")
    expected_program_git = plan.get("program_git_manifest", {}).get("digest")
    expected_program_fs = plan.get("program_filesystem_manifest", {}).get("digest")
    expected_policy_git = plan.get("policy_git_manifest", {}).get("digest")
    expected_policy_fs = plan.get("policy_filesystem_manifest", {}).get("digest")
    if (
        program_git["digest"] != expected_program_git
        or program_fs["digest"] != expected_program_fs
        or program_git["digest"] != program_fs["digest"]
    ):
        raise ReleaseError("release Program manifest changed before cutover")
    if (
        policy_git["digest"] != expected_policy_git
        or policy_fs["digest"] != expected_policy_fs
        or policy_git["digest"] != policy_fs["digest"]
    ):
        raise ReleaseError("release policy manifest changed before cutover")
    if not tree_is_sealed(release_root):
        raise ReleaseError("release checkout is no longer sealed")


def _fresh_cutover_gate(plan: dict[str, Any], authorization: dict[str, Any]) -> None:
    if authorization.get("plan_digest") != plan["plan_digest"]:
        raise ReleaseError("authorization belongs to another plan")
    if time.time() - float(authorization.get("authorized_at", 0)) > 600:
        raise ReleaseError("authorization is older than 10 minutes; run authorize again")
    pid = _serving_pid(plan["server_url"])
    if pid != authorization.get("old_pid") or _pid_epoch(pid) != authorization.get("old_pid_epoch"):
        raise ReleaseError("serving PID epoch changed after authorization")
    host = _host_status(plan)
    host_pid = int(host.get("pid") or 0)
    if (host.get("process") != "online" or host.get("host_status") != "online"
            or host.get("host_id") != authorization.get("old_host_id")
            or host_pid != authorization.get("old_host_pid")
            or _pid_epoch(host_pid) != authorization.get("old_host_pid_epoch")):
        raise ReleaseError("host PID/identity changed after authorization")
    if _runner_pids(host_pid) != authorization.get("old_runner_pids"):
        raise ReleaseError("runner zygote changed after authorization")
    current_inherited, current_to_mask = _current_legacy_transition(plan)
    units = sorted(current_inherited + current_to_mask, key=lambda row: row["name"])
    if units != authorization.get("legacy_units"):
        raise ReleaseError("legacy supervision inventory changed after authorization")
    if any(row.get("active_state") == "active" for row in current_to_mask):
        raise ReleaseError("a legacy supervision unit became active after authorization")
    identity = _control_identity(Path(plan["control_db"]["path"]))
    if any(identity[key] != plan["control_db"][key] for key in ("device", "inode")):
        raise ReleaseError("control DB identity changed after authorization")
    control = _control_snapshot(Path(plan["control_db"]["path"]))
    if (not control["admission_schema_ready"]
            or control["active_legacy_slots"]
            or control["unfinished_without_admission"]):
        raise ReleaseError("worker admission cutover facts changed after authorization")
    if any(control["active"].values()):
        raise ReleaseError("control plane acquired active work after authorization")
    busy = [row for row in _list_data(plan["server_url"], "sessions")
            if row.get("status") in BUSY_SESSION_STATES]
    if busy:
        raise ReleaseError("an Omnigent session became active after authorization")
    rows = [row for row in _list_data(plan["server_url"], "agents")
            if row.get("name") == "program" and not row.get("builtin")]
    expected_registration = authorization.get("pre_registration", {})
    if (len(rows) != 1 or rows[0].get("id") != expected_registration.get("id")
            or rows[0].get("version") != expected_registration.get("version")):
        raise ReleaseError("Program registration changed after authorization")
    task = _json_request(
        "GET", f"{plan['server_url']}/v1/scheduled-tasks/{plan['scheduled_task_id']}"
    )
    if task.get("state") != "paused" or task.get("next_run_at") is not None:
        raise ReleaseError("heartbeat scheduler resumed after authorization")
    prompt = task.get("prompt")
    if (not isinstance(prompt, str)
            or _sha256(prompt.encode()) != plan["scheduled_task"]["original_prompt_sha256"]):
        raise ReleaseError("heartbeat prompt changed after authorization")
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    current_registration = {key: rows[0].get(key) for key in
                            ("id", "name", "version", "created_at", "updated_at")}
    _reverify_predecessor_evidence(
        plan, control_identity=identity, server_env=_selected_process_env(pid),
        server_cmdline=cmdline, registration=current_registration,
        server_url=plan["server_url"],
    )
    _reverify_release_candidate(plan)
    _reverify_omnigent_runtime(plan)


def _new_session(server: str, agent_id: str, title: str,
                 workspace: str) -> dict[str, Any]:
    result = _json_request("POST", f"{server.rstrip('/')}/v1/sessions", {
        "agent_id": agent_id, "title": title, "workspace": workspace,
        "labels": {"nightshift.proof": "exact-sha"},
    })
    if not isinstance(result, dict) or not result.get("id"):
        raise ReleaseError("session creation returned no session id")
    return result


def _extract_provenance(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str) and "nightshift_release_provenance" in value:
        for candidate in (value, value[value.find("{"):value.rfind("}") + 1]):
            try:
                decoded = json.loads(candidate)
                if isinstance(decoded, dict) and decoded.get("kind") == "nightshift_release_provenance":
                    return decoded
            except json.JSONDecodeError:
                pass
    if isinstance(value, dict):
        for item in value.values():
            found = _extract_provenance(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _extract_provenance(item)
            if found:
                return found
    return None


def _session_items(server: str, session_id: str) -> list[dict[str, Any]]:
    obj = _json_request(
        "GET", f"{server.rstrip('/')}/v1/sessions/{session_id}/items?limit=1000"
    )
    data = obj.get("data") if isinstance(obj, dict) else obj
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ReleaseError(f"session items are unreadable for {session_id}")
    return data


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


def _call_arguments(item: dict[str, Any]) -> Any:
    value = item.get("arguments")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ReleaseError("persisted function-call arguments are not JSON") from exc
    return value


def _message_text(items: list[dict[str, Any]], role: str) -> str:
    parts: list[str] = []
    for item in items:
        if item.get("type") == "message" and item.get("role") == role:
            content = item.get("content")
            blocks = content if isinstance(content, list) else [content]
            for block in blocks:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return "\n".join(parts)


def _exact_echo_dispatch(arguments: Any, agent: str) -> bool:
    """Accept only one named child dispatch carrying the inert echo string."""
    if not isinstance(arguments, dict):
        return False
    return (
        arguments == {
            "agent": agent,
            "title": PROOF_TITLE,
            "args": PROOF_METADATA,
        }
        and CLAUDE_MARKER not in "\n".join(_strings(arguments))
    )


def _execution_record_proof(server: str, root_session_id: str,
                            release_root: Path, program_id: str,
                            program_version: int) -> tuple[bool, dict[str, Any]]:
    """Prove execution from persisted dispatch, child, and served-bundle facts."""
    children_obj = _json_request(
        "GET", f"{server}/v1/sessions/{root_session_id}/child_sessions"
    )
    children = (children_obj.get("data", children_obj)
                if isinstance(children_obj, dict) else children_obj)
    if (not isinstance(children, list)
            or any(not isinstance(row, dict) for row in children)):
        raise ReleaseError("persisted child-session list is unreadable")
    child_by_name = {
        str(row.get("tool") or row.get("agent_name")
            or row.get("sub_agent_name") or ""): row
        for row in children
    }
    root_items = _session_items(server, root_session_id)
    root_calls = [item for item in root_items
                  if item.get("type") == "function_call"]
    allowed_root_calls = {
        "sys_session_send", "sys_read_inbox", "sys_session_list",
        "sys_session_get_info", "sys_session_get_history",
    }
    disallowed_root_calls = [item.get("name") for item in root_calls
                             if item.get("name") not in allowed_root_calls]
    send_calls = [item for item in root_calls
                  if item.get("name") == "sys_session_send"]
    dispatches: dict[str, dict[str, Any]] = {}
    dispatch_green = len(send_calls) == 2
    for name in ("architect", "staff"):
        calls = []
        for item in send_calls:
            arguments = _call_arguments(item)
            if isinstance(arguments, dict) and arguments.get("agent") == name:
                calls.append(item)
        exact_payload = (
            len(calls) == 1
            and _exact_echo_dispatch(_call_arguments(calls[0]), name)
        )
        dispatch_green = dispatch_green and exact_payload
        dispatches[name] = {
            "count": len(calls),
            "call_id": calls[0].get("call_id") if len(calls) == 1 else None,
            "arguments_sha256": _sha256(_canonical(_call_arguments(calls[0])))
            if len(calls) == 1 else None,
            "exact_echo_only": exact_payload,
        }

    child_proofs: dict[str, dict[str, Any]] = {}
    child_green = len(children) == 2 and set(child_by_name) == {"architect", "staff"}
    expected_manifest = filesystem_manifest(release_root / "program")
    for name, expected_response in (
        ("architect", ECHO_RESPONSE),
        ("staff", CLAUDE_MARKER),
    ):
        child = child_by_name.get(name) or {}
        child_id = child.get("id")
        if not child_id:
            child_green = False
            child_proofs[name] = {"session_id": None, "missing": True}
            continue
        items = _session_items(server, child_id)
        user_text = _message_text(items, "user").strip()
        assistant_text = _message_text(items, "assistant").strip()
        child_calls = [item.get("name") for item in items
                       if item.get("type") == "function_call"]
        bound = _json_request("GET", f"{server}/v1/sessions/{child_id}/agent")
        status, child_bundle, _headers = _http(
            "GET", f"{server}/v1/sessions/{child_id}/agent/contents", timeout=60
        )
        if status != 200:
            raise ReleaseError(f"child served contents returned HTTP {status}")
        with tempfile.TemporaryDirectory(prefix=f"nightshift-{name}-served-") as tmp:
            child_root = _safe_extract_tar(child_bundle, Path(tmp) / "bundle")
            child_manifest = filesystem_manifest(child_root)
            child_bytes = b"\n".join(
                path.read_bytes() for path in child_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
        green_child = (
            user_text == CHILD_PROOF_INPUT
            and assistant_text == expected_response
            and not child_calls
            and bound.get("id") == program_id
            and bound.get("version") == program_version
            and child_manifest["digest"] == expected_manifest["digest"]
        )
        child_green = child_green and green_child
        child_proofs[name] = {
            "session_id": child_id,
            "parent_session_id": child.get("parent_session_id"),
            "user_message_sha256": _sha256(user_text.encode()),
            "assistant_message_sha256": _sha256(assistant_text.encode()),
            "expected_response_sha256": _sha256(expected_response.encode()),
            "assistant_record_is_exact_expected_response": (
                assistant_text == expected_response
            ),
            "child_tool_calls": child_calls,
            "bound_program_id": bound.get("id"),
            "bound_program_version": bound.get("version"),
            "served_bundle_sha256": _sha256(child_bundle),
            "served_manifest_digest": child_manifest["digest"],
            "expected_manifest_digest": expected_manifest["digest"],
            "served_bundle_bytes": len(child_bytes),
        }
    green = dispatch_green and not disallowed_root_calls and child_green
    return green, {
        "session_id": root_session_id,
        "root_item_ids": [item.get("id") for item in root_items],
        "dispatches": dispatches,
        "disallowed_root_tool_calls": disallowed_root_calls,
        "children": child_proofs,
    }


def _set_proof(receipt: dict[str, Any], name: str, verdict: str,
               operands: dict[str, Any], reason: str) -> None:
    receipt["proofs"][name] = {"verdict": verdict, "operands": operands,
                                "reason": reason}
    receipt["updated_at"] = int(time.time())


def postflight(plan: dict[str, Any], authorization: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every proof fresh for the current serving PID epoch."""
    state_dir = Path(plan["state_dir"])
    receipt_path = state_dir / "receipt.json"
    receipt = _receipt(plan["plan_digest"])
    try:
        entry_runtime_identity = _reverify_omnigent_runtime(plan)
    except Exception as exc:  # noqa: BLE001 - fail before durable proof work
        reason = f"{type(exc).__name__}: {exc}"
        _set_proof(
            receipt,
            "runtime_environment",
            "red",
            {
                "expected_omnigent_runtime_identity": plan.get(
                    "omnigent_runtime_identity"
                ),
                "observed_omnigent_runtime_identity": None,
                "omnigent_runtime_identity_error": reason,
            },
            "installed Omnigent runtime identity drifted before postflight",
        )
        receipt["fatal"] = reason
        _atomic_json(receipt_path, receipt)
        return receipt
    try:
        pid = _serving_pid(plan["server_url"])
        epoch = _pid_epoch(pid)
        receipt["pid_epoch"] = {"pid": pid, "start_ticks": epoch}
    except Exception as exc:  # noqa: BLE001 - receipt must survive every failure
        receipt["fatal"] = f"{type(exc).__name__}: {exc}"
        _atomic_json(receipt_path, receipt)
        return receipt

    server = plan["server_url"]
    proof_session: dict[str, Any] | None = None
    current_row: dict[str, Any] | None = None
    try:
        rows = [row for row in _list_data(server, "agents")
                if row.get("name") == "program" and not row.get("builtin")]
        expected = authorization["pre_registration"]
        row = rows[0] if len(rows) == 1 else {}
        operands = {
            "row_count": len(rows), "expected_id": expected["id"],
            "actual_id": row.get("id"), "expected_version": expected["version"] + 1,
            "actual_version": row.get("version"), "name": row.get("name"),
        }
        green = (len(rows) == 1 and row.get("id") == expected["id"]
                 and row.get("version") == expected["version"] + 1)
        _set_proof(receipt, "registered", "green" if green else "red", operands,
                   "stable Program id and exact +1 version" if green
                   else "Program registration id/count/version mismatch")
        if green:
            current_row = row
    except Exception as exc:  # noqa: BLE001
        _set_proof(receipt, "registered", "undetermined", {},
                   f"{type(exc).__name__}: {exc}")

    served_digest: str | None = None
    if current_row is not None:
        try:
            proof_session = _new_session(
                server, current_row["id"], f"Nightshift exact-SHA proof {plan['sha'][:12]}",
                plan["project"],
            )
            bound = _json_request("GET", f"{server}/v1/sessions/{proof_session['id']}/agent")
            bundle_bytes, served_digest = _download_and_hash_served_bundle(
                server, proof_session["id"]
            )
            operands = {
                "session_id": proof_session["id"], "session_created_at": proof_session.get("created_at"),
                "agent_id": bound.get("id"), "agent_version": bound.get("version"),
                "bundle_sha256": _sha256(bundle_bytes), "served_program_digest": served_digest,
                "pid": pid, "process_start_ticks": epoch,
            }
            green = (bound.get("id") == current_row["id"]
                     and bound.get("version") == current_row["version"])
            _set_proof(receipt, "served", "green" if green else "red", operands,
                       "fresh production-route session bound to registered Program" if green
                       else "fresh session bound to another Program row/version")
        except Exception as exc:  # noqa: BLE001
            _set_proof(receipt, "served", "undetermined", {},
                       f"{type(exc).__name__}: {exc}")

    if served_digest is not None:
        expected_digest = plan["program_git_manifest"]["digest"]
        green = served_digest == expected_digest
        _set_proof(receipt, "content_at_sha", "green" if green else "red", {
            "git_object_digest": expected_digest,
            "sealed_filesystem_digest": plan["program_filesystem_manifest"]["digest"],
            "served_bundle_digest": served_digest,
            "sha": plan["sha"],
        }, "independent Git, sealed filesystem, and served bundle digests match"
           if green else "served Program bytes do not match the reviewed Git object")

    if proof_session is not None:
        try:
            event = {
                "event": {"type": "PHASE_TOOL_CALL", "data": {
                    "name": "sys_os_shell", "arguments": {"command": CANARY_COMMAND},
                }}
            }
            response = _json_request(
                "POST", f"{server}/v1/sessions/{proof_session['id']}/policies/evaluate",
                event,
            )
            provenance = _extract_provenance(response)
            if provenance is None:
                raise ReleaseError("canary denial carried no provenance JSON")
            module = Path(str(provenance.get("module", "")))
            expected_module = Path(plan["release_root"]) / "deploy" / "policies" / "omnipods_policies.py"
            module_digest = _sha256(expected_module.read_bytes())
            expected_controller = (
                Path(plan["release_root"]) / "bin" / "nightshift-ctl"
            )
            if expected_controller.resolve(strict=True) != expected_controller:
                raise ReleaseError("release admission controller is not canonical")
            controller_digest = _sha256(expected_controller.read_bytes())
            controller_git_bytes = _git(
                Path(plan["release_root"]), "cat-file", "blob",
                f"{plan['sha']}:bin/nightshift-ctl", binary=True,
            )
            controller_git_digest = _sha256(controller_git_bytes)
            observed_controller = Path(str(
                provenance.get("admission_controller", "")
            ))
            operands = {"session_id": proof_session["id"], "response": response,
                        "provenance": provenance, "expected_module": str(expected_module),
                        "expected_module_sha256": module_digest,
                        "expected_admission_controller": str(expected_controller),
                        "expected_admission_controller_sha256": controller_digest,
                        "git_admission_controller_sha256": controller_git_digest,
                        "serving_pid": pid, "serving_start_ticks": epoch}
            green = (
                module == expected_module
                and provenance.get("module_sha256") == module_digest
                and observed_controller == expected_controller
                and provenance.get("admission_controller_sha256")
                    == controller_digest
                and controller_digest == controller_git_digest
                and provenance.get("pid") == pid
                and provenance.get("process_start_ticks") == epoch
            )
            _set_proof(receipt, "enforced", "green" if green else "red", operands,
                       "serving process denied canary from sealed policy and "
                       "held admission-controller bytes" if green
                       else "canary came from the wrong module, controller, or PID epoch")
        except Exception as exc:  # noqa: BLE001
            _set_proof(receipt, "enforced", "undetermined", {},
                       f"{type(exc).__name__}: {exc}")

    try:
        host = _host_status(plan)
        host_pid = int(host.get("pid") or 0)
        runners = _runner_pids(host_pid)
        if len(runners) != 1:
            raise ReleaseError(f"expected one runner zygote, got {runners}")
        runtime_identity = entry_runtime_identity
        runtime_identity_error: str | None = None
        expected = {
            "NIGHTSHIFT_RELEASE_ROOT": plan["release_root"],
            "NIGHTSHIFT_RELEASE_SHA": plan["sha"],
            "NIGHTSHIFT_CTL_DB": plan["control_db"]["path"],
            "PYTHONPATH": str(Path(plan["release_root"]) / "deploy" / "policies"),
        }
        codex_host_binding = {
            "HARNESS_CODEX_DISABLE_NATIVE_TOOLS": "1",
            "HARNESS_CODEX_ENABLE_WEB_SEARCH": "0",
        }
        environments = {
            "server": _selected_process_env(pid),
            "host": _selected_process_env(host_pid),
            "runner": _selected_process_env(runners[0]),
        }
        cmdlines = {
            "server": Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(),
            "host": Path(f"/proc/{host_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(),
            "runner": Path(f"/proc/{runners[0]}/cmdline").read_bytes().replace(b"\0", b" ").decode(),
        }
        release_bin = str(Path(plan["release_root"]) / "bin")
        green = (
            host.get("process") == "online"
            and host.get("host_status") == "online"
            and host.get("host_id") == plan["durable_session"]["host_id"]
            and _runtime_environments_match(
                environments, expected, release_bin, codex_host_binding)
            and plan["omnigent_bin"] in cmdlines["server"]
            and plan["omnigent_bin"] in cmdlines["host"]
            and runtime_identity is not None
        )
        _set_proof(receipt, "runtime_environment", "green" if green else "red", {
            "server_pid": pid,
            "host_pid": host_pid,
            "host_id": host.get("host_id"),
            "runner_pids": runners,
            "expected": expected,
            "expected_codex_host_binding": codex_host_binding,
            "expected_path_first": release_bin,
            "environments": environments,
            "cmdlines": cmdlines,
            "omnigent_bin": plan["omnigent_bin"],
            "expected_omnigent_runtime_identity": plan.get(
                "omnigent_runtime_identity"
            ),
            "observed_omnigent_runtime_identity": runtime_identity,
            "omnigent_runtime_identity_error": runtime_identity_error,
        }, "live server, host, and runner environments bind the sealed SHA; "
           "live host and runner disable native Codex tools; installed Omnigent "
           "matches its exact Git lock"
           if green else "a live runtime environment retained a stale path or "
           "runtime identity, or did not disable native Codex tools")
    except Exception as exc:  # noqa: BLE001
        _set_proof(receipt, "runtime_environment", "undetermined", {},
                   f"{type(exc).__name__}: {exc}")

    if current_row is not None:
        try:
            smoke = subprocess.run(
                [plan["omnigent_python"],
                 str(Path(plan["release_root"]) / "deploy" / "exact_sha_smoke.py"),
                 "--server", server, "--agent-id", current_row["id"],
                 "--workspace", plan["project"],
                 "--host-id", plan["durable_session"]["host_id"]],
                text=True, capture_output=True, timeout=900, check=False,
            )
            if smoke.returncode:
                raise ReleaseError(f"execution smoke exited {smoke.returncode}: "
                                   f"{smoke.stderr[-1000:]}")
            result = json.loads(smoke.stdout)
            root_session_id = result["session_id"]
            green, operands = _execution_record_proof(
                server, root_session_id, Path(plan["release_root"]),
                current_row["id"], current_row["version"],
            )
            _set_proof(receipt, "executed", "green" if green else "red", operands,
                       "persisted child records prove exact-payload Claude and Codex execution"
                       if green else
                       "registered/served bundles may be correct, but executing child records "
                       "do not match them — suspect an agent_id/cache split")
        except Exception as exc:  # noqa: BLE001
            _set_proof(receipt, "executed", "undetermined", {},
                       f"{type(exc).__name__}: {exc}")

    try:
        identity = _control_identity(Path(plan["control_db"]["path"]))
        current = _control_snapshot(Path(plan["control_db"]["path"]))
        old_session = _json_request(
            "GET", f"{server}/v1/sessions/{plan['durable_session']['id']}"
        )
        before = plan["control_before"]
        green = (
            identity["device"] == plan["control_db"]["device"]
            and identity["inode"] == plan["control_db"]["inode"]
            and current["task_count"] >= before["task_count"]
            and current["agent_run_count"] >= before["agent_run_count"]
            and old_session.get("id") == plan["durable_session"]["id"]
        )
        _set_proof(receipt, "durable_continuity", "green" if green else "red", {
            "control_identity_before": plan["control_db"],
            "control_identity_after": identity,
            "control_snapshot_before": before,
            "control_snapshot_after": current,
            "preserved_session_id": old_session.get("id"),
        }, "control DB inode/state and pre-cutover durable session survived" if green
           else "durable control/session identity did not survive")
    except Exception as exc:  # noqa: BLE001
        _set_proof(receipt, "durable_continuity", "undetermined", {},
                   f"{type(exc).__name__}: {exc}")

    # Scheduler continuity is completed by cutover only after every core proof
    # is green; until then the durable heartbeat remains paused.
    receipt["accepted"] = False
    receipt["updated_at"] = int(time.time())
    _atomic_json(receipt_path, receipt)
    return receipt


def _patch_scheduled_task(plan: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    result = _json_request(
        "PATCH",
        f"{plan['server_url']}/v1/scheduled-tasks/{plan['scheduled_task_id']}",
        body,
    )
    if not isinstance(result, dict):
        raise ReleaseError("scheduled-task update returned no task object")
    return result


def _install_pinned_schedule(plan: dict[str, Any]) -> dict[str, Any]:
    task = _json_request(
        "GET", f"{plan['server_url']}/v1/scheduled-tasks/{plan['scheduled_task_id']}"
    )
    prompt = task.get("prompt")
    prompt_hash = _sha256(prompt.encode()) if isinstance(prompt, str) else None
    allowed = {
        plan["scheduled_task"]["original_prompt_sha256"],
        plan["scheduled_task"]["pinned_prompt_sha256"],
    }
    if prompt_hash not in allowed:
        raise ReleaseError("heartbeat prompt changed outside the reviewed cutover plan")
    if task.get("state") != "paused":
        raise ReleaseError("heartbeat must remain paused while its path is pinned")
    if prompt_hash != plan["scheduled_task"]["pinned_prompt_sha256"]:
        task = _patch_scheduled_task(
            plan, {"prompt": plan["scheduled_task"]["pinned_prompt"]}
        )
    prompt = task.get("prompt")
    if (not isinstance(prompt, str)
            or _sha256(prompt.encode()) != plan["scheduled_task"]["pinned_prompt_sha256"]
            or "./bin/nightshift-ctl" in prompt):
        raise ReleaseError("heartbeat did not persist the exact-SHA controller path")
    return task


def _restore_scheduled_task(plan: dict[str, Any]) -> dict[str, Any]:
    task = _patch_scheduled_task(plan, {
        "prompt": plan["scheduled_task"]["original_prompt"],
        "state": plan["scheduled_task"]["state"],
    })
    prompt = task.get("prompt")
    if (not isinstance(prompt, str)
            or _sha256(prompt.encode()) != plan["scheduled_task"]["original_prompt_sha256"]
            or task.get("state") != plan["scheduled_task"]["state"]):
        raise ReleaseError("rollback could not restore the original scheduled task")
    return task


def _activate_pinned_schedule(plan: dict[str, Any],
                              receipt: dict[str, Any]) -> dict[str, Any]:
    receipt_pid = receipt.get("pid_epoch") or {}
    current_pid = _serving_pid(plan["server_url"])
    runtime_operands = receipt["proofs"]["runtime_environment"]["operands"]
    current_host = _host_status(plan)
    current_host_pid = int(current_host.get("pid") or 0)
    if (current_pid != receipt_pid.get("pid")
            or _pid_epoch(current_pid) != receipt_pid.get("start_ticks")
            or current_host_pid != runtime_operands.get("host_pid")
            or _runner_pids(current_host_pid) != runtime_operands.get("runner_pids")):
        raise ReleaseError("server/host/runner epoch changed after core proofs")
    task = _patch_scheduled_task(plan, {"state": "active"})
    prompt = task.get("prompt")
    prompt_hash = _sha256(prompt.encode()) if isinstance(prompt, str) else None
    next_run = _timestamp_seconds(task.get("next_run_at"))
    green = (
        task.get("state") == "active"
        and prompt_hash == plan["scheduled_task"]["pinned_prompt_sha256"]
        and "./bin/nightshift-ctl" not in (prompt or "")
        and next_run is not None
        and next_run > time.time()
        and _serving_pid(plan["server_url"]) == current_pid
        and _pid_epoch(current_pid) == receipt_pid.get("start_ticks")
    )
    _set_proof(receipt, "scheduler_continuity", "green" if green else "red", {
        "task_id": task.get("id"),
        "state": task.get("state"),
        "next_run_at": task.get("next_run_at"),
        "next_run_epoch": next_run,
        "prompt_sha256": prompt_hash,
        "expected_prompt_sha256": plan["scheduled_task"]["pinned_prompt_sha256"],
        "absolute_controller": str(Path(plan["release_root"]) / "bin" / "nightshift-ctl"),
        "replacement_count": plan["scheduled_task"]["replacement_count"],
    }, "durable heartbeat resumed with exact-SHA controller paths" if green
       else "durable heartbeat did not resume on the pinned prompt")
    receipt["accepted"] = all(
        receipt["proofs"][name]["verdict"] == "green" for name in PROOFS
    )
    receipt["updated_at"] = int(time.time())
    _atomic_json(Path(plan["state_dir"]) / "receipt.json", receipt)
    if not receipt["accepted"]:
        raise ReleaseError("scheduler continuity proof is red")
    return task


def _verify_rollback(plan: dict[str, Any]) -> dict[str, Any]:
    server = plan["server_url"]
    if not _wait_for_health(server, wanted=True, timeout=120):
        raise ReleaseError("rollback server did not become healthy")
    rows = [row for row in _list_data(server, "agents")
            if row.get("name") == "program" and not row.get("builtin")]
    if len(rows) != 1 or rows[0].get("id") != plan["pre_registration"]["id"]:
        raise ReleaseError("rollback did not restore the stable Program registration")
    session = _new_session(server, rows[0]["id"],
                           f"Nightshift rollback proof {plan['sha'][:12]}", plan["project"])
    status, bundle_bytes, _headers = _http(
        "GET", f"{server}/v1/sessions/{session['id']}/agent/contents", timeout=60
    )
    if status != 200:
        raise ReleaseError(f"rollback served contents returned HTTP {status}")
    with tempfile.TemporaryDirectory(prefix="nightshift-rollback-") as tmp:
        root = _safe_extract_tar(bundle_bytes, Path(tmp) / "bundle")
        digest = filesystem_manifest(root)["digest"]
    expected = plan["rollback"]["manifest"]["digest"]
    identity = _control_identity(Path(plan["control_db"]["path"]))
    pid = _serving_pid(server)
    rollback_env = _selected_process_env(pid)
    policy_digest = filesystem_manifest(
        Path(plan["rollback"]["policy_dir"])
    )["digest"]
    green = (digest == expected
             and identity["device"] == plan["control_db"]["device"]
             and identity["inode"] == plan["control_db"]["inode"]
             and rollback_env.get("PYTHONPATH") == plan["rollback"]["policy_dir"]
             and policy_digest == plan["rollback"]["policy_manifest"]["digest"])
    result = {"verified_at": int(time.time()), "green": green,
              "session_id": session["id"], "served_digest": digest,
              "expected_digest": expected, "control_identity": identity,
              "policy_digest": policy_digest,
              "expected_policy_digest": plan["rollback"]["policy_manifest"]["digest"],
              "runtime_environment": rollback_env}
    _atomic_json(Path(plan["state_dir"]) / "rollback-receipt.json", result)
    if not green:
        raise ReleaseError("rollback server is healthy but served/durable proof is red")
    return result


def _rollback_live(plan: dict[str, Any]) -> dict[str, Any]:
    try:
        host = _host_status(plan)
    except ReleaseError:
        host = {}
    if host.get("process") == "online":
        host_pid = int(host.get("pid") or 0)
        runners = _runner_pids(host_pid)
        _stop_exact_pid(host_pid, _pid_epoch(host_pid), label="host")
        if not _wait_pids_gone(runners):
            raise ReleaseError(
                f"runner zygotes did not exit before rollback: {runners}"
            )
    for key in ("host_launch_argv", "rollback_host_argv"):
        _kill_own_tmux(plan[key])
    try:
        server_pid = _serving_pid(plan["server_url"])
    except ReleaseError:
        server_pid = None
    if server_pid is not None:
        _stop_exact_pid(server_pid, _pid_epoch(server_pid), label="server")
    for key in ("launch_argv", "rollback_argv"):
        _kill_own_tmux(plan[key])
    if not _wait_for_health(plan["server_url"], wanted=False, timeout=20):
        raise ReleaseError("server socket is still occupied; rollback did not start")
    _start_runtime_pair(plan, rollback=True)
    _restore_legacy_units_if_masked(plan)
    _restore_scheduled_task(plan)
    return _verify_rollback(plan)


def _recover_before_candidate_start(plan: dict[str, Any],
                                    authorization: dict[str, Any]) -> dict[str, Any]:
    """Recover a failure while the authorized old server may still be alive."""
    if not _wait_for_health(plan["server_url"], wanted=True, timeout=5):
        return _rollback_live(plan)
    pid = _serving_pid(plan["server_url"])
    if (pid != authorization["old_pid"]
            or _pid_epoch(pid) != authorization["old_pid_epoch"]):
        raise ReleaseError("unexpected server epoch during pre-start recovery")
    try:
        host = _host_status(plan)
    except ReleaseError:
        host = {}
    if (host.get("process") == "online"
            and host.get("host_status") == "online"
            and (int(host.get("pid") or 0) != authorization["old_host_pid"]
                 or _pid_epoch(int(host["pid"]))
                 != authorization["old_host_pid_epoch"])):
        raise ReleaseError("unexpected host epoch during pre-start recovery")
    if host.get("process") != "online" or host.get("host_status") != "online":
        _kill_own_tmux(plan["rollback_host_argv"])
        _start_tmux(plan["rollback_host_argv"])
        host = _wait_for_host(plan, wanted=True, timeout=120) or {}
    if (host.get("host_status") != "online"
            or host.get("host_id") != plan["durable_session"]["host_id"]):
        raise ReleaseError("old server survived but production host could not be restored")
    _restore_legacy_units_if_masked(plan)
    _restore_scheduled_task(plan)
    rows = [row for row in _list_data(plan["server_url"], "agents")
            if row.get("name") == "program" and not row.get("builtin")]
    if len(rows) != 1 or rows[0].get("id") != plan["pre_registration"]["id"]:
        raise ReleaseError("old server recovery found Program registration drift")
    session = _new_session(
        plan["server_url"], rows[0]["id"],
        f"Nightshift pre-start recovery proof {plan['sha'][:12]}", plan["project"],
    )
    status, bundle_bytes, _headers = _http(
        "GET", f"{plan['server_url']}/v1/sessions/{session['id']}/agent/contents",
        timeout=60,
    )
    if status != 200:
        raise ReleaseError("old server recovery could not read served Program")
    with tempfile.TemporaryDirectory(prefix="nightshift-prestart-recovery-") as tmp:
        root = _safe_extract_tar(bundle_bytes, Path(tmp) / "bundle")
        digest = filesystem_manifest(root)["digest"]
    identity = _control_identity(Path(plan["control_db"]["path"]))
    env = _selected_process_env(pid)
    green = (
        digest == plan["rollback"]["manifest"]["digest"]
        and env.get("PYTHONPATH") == plan["rollback"]["policy_source"]
        and identity["device"] == plan["control_db"]["device"]
        and identity["inode"] == plan["control_db"]["inode"]
    )
    result = {
        "verified_at": int(time.time()), "green": green,
        "kind": "authorized_old_server_survived",
        "server_pid": pid, "server_pid_epoch": _pid_epoch(pid),
        "host_pid": host.get("pid"), "session_id": session["id"],
        "served_digest": digest,
        "expected_digest": plan["rollback"]["manifest"]["digest"],
        "runtime_environment": env, "control_identity": identity,
    }
    _atomic_json(Path(plan["state_dir"]) / "prestart-recovery.json", result)
    if not green:
        raise ReleaseError("authorized old server survived but recovery proof is red")
    return result


def cutover(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve(strict=True)
    plan = _load_plan(plan_path)
    if args.owner_go != plan["plan_digest"]:
        raise ReleaseError("--owner-go must equal the printed plan digest")
    rollback_line = shlex.join(_render_rollback_command(plan, plan_path))
    print(f"rollback: {rollback_line}")
    authorization = json.loads(Path(args.authorization).read_text())
    _fresh_cutover_gate(plan, authorization)
    state_dir = Path(plan["state_dir"])
    attempt: dict[str, Any] = {"started_at": int(time.time()),
                              "plan_digest": plan["plan_digest"],
                              "status": "starting"}
    attempt_path = state_dir / "cutover-attempt.json"
    _atomic_json(attempt_path, attempt)
    mutation_phase = False
    try:
        _reverify_release_candidate(plan)
        mask_result = _mask_legacy_units(state_dir, plan["supervision"]["to_mask"])
        mutation_phase = True
        attempt["supervision"] = mask_result
        _atomic_json(attempt_path, attempt)
        _reverify_release_candidate(plan)
        _stop_runtime_pair(
            plan, server_pid=authorization["old_pid"],
            server_epoch=authorization["old_pid_epoch"],
            host_pid=authorization["old_host_pid"],
            host_epoch=authorization["old_host_pid_epoch"],
            runner_pids=authorization["old_runner_pids"], rollback=False,
        )
        _reverify_release_candidate(plan)
        runtime = _start_runtime_pair(plan, rollback=False)
    except Exception as exc:
        mutation_phase = mutation_phase or (
            state_dir / "supervision" / "mask-started.json"
        ).is_file()
        failure = f"{type(exc).__name__}: {exc}"
        if not mutation_phase:
            attempt.update(status="refused_before_mutation", failure=failure,
                           finished_at=int(time.time()))
            _atomic_json(attempt_path, attempt)
            raise
        try:
            recovery = _recover_before_candidate_start(plan, authorization)
        except Exception as recovery_exc:
            attempt.update(
                status="prestart_recovery_failed", failure=failure,
                recovery_failure=f"{type(recovery_exc).__name__}: {recovery_exc}",
                finished_at=int(time.time()),
            )
            _atomic_json(attempt_path, attempt)
            raise ReleaseError(
                "candidate startup failed and automatic rollback could not be proven; "
                f"run the printed rollback command: {rollback_line}"
            ) from recovery_exc
        attempt.update(status="rolled_back_prestart_failure", recovery=recovery,
                       failure=failure, finished_at=int(time.time()))
        _atomic_json(attempt_path, attempt)
        raise ReleaseError(
            "candidate could not start; the preserved runtime was verified restored"
        ) from exc
    new_pid = int(runtime["server_pid"])
    new_epoch = _pid_epoch(new_pid)
    attempt.update(status="postflight", new_pid=new_pid, new_pid_epoch=new_epoch,
                   new_host=runtime["host"], runner_pids=runtime["runner_pids"])
    _atomic_json(attempt_path, attempt)
    try:
        _install_pinned_schedule(plan)
    except Exception as exc:
        rollback = _rollback_live(plan)
        attempt.update(status="rolled_back_schedule_pin_failure", rollback=rollback,
                       failure=f"{type(exc).__name__}: {exc}",
                       finished_at=int(time.time()))
        _atomic_json(attempt_path, attempt)
        raise ReleaseError("heartbeat path pinning failed; verified rollback restored")
    try:
        receipt = postflight(plan, authorization)
    except Exception as exc:
        rollback = _rollback_live(plan)
        attempt.update(
            status="rolled_back_postflight_evidence_failure",
            rollback=rollback,
            failure=f"{type(exc).__name__}: {exc}",
            finished_at=int(time.time()),
        )
        _atomic_json(attempt_path, attempt)
        raise ReleaseError(
            "postflight evidence could not be completed; verified rollback restored"
        ) from exc
    core_green = all(
        receipt["proofs"][name]["verdict"] == "green" for name in CORE_PROOFS
    )
    if not core_green:
        rollback = _rollback_live(plan)
        attempt.update(status="rolled_back_postflight_failure", receipt=receipt,
                       rollback=rollback, finished_at=int(time.time()))
        _atomic_json(attempt_path, attempt)
        raise ReleaseError("candidate proof was not all green; verified rollback restored")
    try:
        _activate_pinned_schedule(plan, receipt)
    except Exception as exc:
        rollback = _rollback_live(plan)
        attempt.update(status="rolled_back_scheduler_resume_failure", receipt=receipt,
                       rollback=rollback, failure=f"{type(exc).__name__}: {exc}",
                       finished_at=int(time.time()))
        _atomic_json(attempt_path, attempt)
        raise ReleaseError("scheduler resume proof failed; verified rollback restored")
    attempt.update(status="accepted", receipt=receipt, finished_at=int(time.time()))
    _atomic_json(attempt_path, attempt)
    print(f"ACCEPTED exact-SHA deployment: {plan['sha']}")
    print(f"receipt: {Path(plan['state_dir']) / 'receipt.json'}")


def seal_command(args: argparse.Namespace) -> None:
    """Root step between prepare and authorize: give the release to root so
    the runtime uid can no longer chmod it writable, then re-prove it.

    The plan file is runtime-writable, so nothing in it is trusted to choose
    what root seals: the target must be exactly <release-base>/<sha>."""
    if os.geteuid() != 0:
        raise ReleaseError("seal must run as root; a seal the runtime user "
                           "applies is one it can undo")
    plan = _load_plan(Path(args.plan).resolve(strict=True))
    sha = plan.get("sha")
    base = Path(args.release_base)
    if not isinstance(sha, str) or not FULL_SHA.fullmatch(sha) or not base.is_absolute():
        raise ReleaseError("plan SHA or release base is invalid")
    release_dir = base / sha
    checkout = release_dir / "checkout"
    if plan.get("release_root") != str(checkout) or os.path.realpath(checkout) != str(checkout):
        raise ReleaseError(f"plan release_root is not {checkout}")
    base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        # The base stays creatable for the next prepare, but sticky: nobody
        # but root may rename or delete a release root owns.
        info = os.fstat(base_fd)
        os.fchown(base_fd, 0, info.st_gid)
        mode = stat.S_IMODE(info.st_mode) & ~0o002
        os.fchmod(base_fd, (mode | stat.S_ISVTX) if mode & 0o020 else mode)
    finally:
        os.close(base_fd)
    seal_tree(release_dir)
    for key, subtree in (("program_filesystem_manifest", "program"),
                         ("policy_filesystem_manifest", "deploy/policies")):
        if filesystem_manifest(checkout / subtree)["digest"] != plan[key]["digest"]:
            raise ReleaseError(f"{subtree} changed after prepare; do not authorize this release")
    if not tree_is_sealed(checkout):
        raise ReleaseError("release is still replaceable by a non-root user")
    print(f"SEALED root-owned: {release_dir}")


def rollback_command(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve(strict=True)
    plan = _load_plan(plan_path)
    if args.owner_go != plan["plan_digest"]:
        raise ReleaseError("--owner-go must equal the printed plan digest")
    print(f"rollback: {shlex.join(_render_rollback_command(plan, plan_path))}")
    control = _control_snapshot(Path(plan["control_db"]["path"]))
    if any(control["active"].values()):
        raise ReleaseError("control plane is active; quiesce before explicit rollback")
    if _wait_for_health(plan["server_url"], wanted=True, timeout=3):
        busy = [row for row in _list_data(plan["server_url"], "sessions")
                if row.get("status") in BUSY_SESSION_STATES]
        if busy:
            raise ReleaseError(
                "Omnigent sessions are active; quiesce before explicit rollback"
            )
        task = _json_request(
            "GET", f"{plan['server_url']}/v1/scheduled-tasks/"
            f"{plan['scheduled_task_id']}"
        )
        if task.get("state") != "paused" or task.get("next_run_at") is not None:
            raise ReleaseError("pause the heartbeat before explicit rollback")
    result = _rollback_live(plan)
    print(f"ROLLBACK VERIFIED: {json.dumps(result, sort_keys=True)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="materialise and seal an exact SHA")
    prep.add_argument("--source", required=True)
    prep.add_argument("--sha", required=True)
    prep.add_argument("--floor-sha", required=True)
    prep.add_argument("--release-base", default="/srv/nightshift-releases")
    prep.add_argument("--state-base", default="/srv/nightshift-release-state")
    prep.add_argument("--project", default="/srv/project")
    prep.add_argument("--control-db", default="/srv/project/.omni/control.db")
    prep.add_argument("--server-env", default="/etc/nightshift/server.env")
    prep.add_argument("--server-url", default="http://127.0.0.1:6767")
    prep.add_argument("--scheduled-task-id", required=True)
    prep.add_argument("--rollback-policy-dir",
                      default="/srv/nightshift/deploy/policies")
    prep.add_argument("--omnigent-python")
    prep.add_argument(
        "--predecessor-plan",
        help="path to an accepted predecessor plan.json; enters subsequent "
             "supervision mode (ADR-0064) instead of bootstrap mode",
    )
    prep.set_defaults(func=prepare)
    auth = sub.add_parser("authorize", help="read-only quiescence gate")
    auth.add_argument("--plan", required=True)
    auth.set_defaults(func=authorize)
    live = sub.add_parser("cutover", help="apply an authorized live cutover")
    live.add_argument("--plan", required=True)
    live.add_argument("--authorization", required=True)
    live.add_argument("--owner-go", required=True)
    live.set_defaults(func=cutover)
    seal = sub.add_parser("seal", help="as root: make a prepared release root-owned")
    seal.add_argument("--plan", required=True)
    seal.add_argument("--release-base", default="/srv/nightshift-releases")
    seal.set_defaults(func=seal_command)
    rollback = sub.add_parser("rollback", help="re-register and verify preserved bundle")
    rollback.add_argument("--plan", required=True)
    rollback.add_argument("--owner-go", required=True)
    rollback.set_defaults(func=rollback_command)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        args.func(args)
        return 0
    except ReleaseError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
