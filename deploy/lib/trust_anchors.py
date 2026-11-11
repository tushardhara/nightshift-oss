#!/usr/bin/env python3
"""Who can change the files that configure Nightshift's fences (audit C6).

Workers run as the runtime uid. Anything that uid can rewrite -- the server
environment, the policy modules, the agent bundle, the authority registry --
is something a worker can rewrite, and the next legitimate restart loads it.
`chmod a-w` is not protection: the owner chmods it back.

`audit` reports every path the uid could change: owned by it, writable by it
through group or other bits, or replaceable because a directory above it is.
`promote` copies the authority/tracker registries into a root-owned anchor the
fences read, since the registry's own state root must stay runtime-owned.
Stdlib only; preflight and install-root-config.sh both call it.
"""

from __future__ import annotations

import argparse
import grp
import os
import pwd
import sqlite3
import stat
import sys
import tempfile

REGISTRY_FILES = ("authority.db", "tracker.db")


def _writable(st: os.stat_result, uid: int, gids: frozenset[int]) -> str | None:
    if st.st_uid == uid:
        return "owned by the runtime uid"
    if st.st_mode & stat.S_IWOTH:
        return "world-writable"
    if st.st_mode & stat.S_IWGRP and st.st_gid in gids:
        return "group-writable by a group the runtime user is in"
    return None


def _chain(path: str) -> list[str]:
    """`path` and every directory above it, nearest first."""
    out = [path]
    while True:
        parent = os.path.dirname(out[-1])
        if parent == out[-1]:
            return out
        out.append(parent)


def path_findings(path: str, uid: int, gids: frozenset[int]) -> list[str]:
    """Why `uid` could change what `path` names, or [] when it cannot.

    Checks the lexical path and the resolved one, because a symlink anywhere
    on the way is only as safe as the directory holding it.
    """
    if uid == 0:
        return [f"{path}: the runtime uid is root"]
    findings: list[str] = []
    seen: set[str] = set()
    for start in dict.fromkeys((os.path.abspath(path), os.path.realpath(path))):
        child_uid = None
        for index, node in enumerate(_chain(start)):
            try:
                st = os.lstat(node)
            except FileNotFoundError:
                child_uid = None
                continue
            reason = _writable(st, uid, gids)
            if index and reason and stat.S_ISDIR(st.st_mode) \
                    and st.st_uid != uid and st.st_mode & stat.S_ISVTX \
                    and child_uid is not None and child_uid != uid:
                # A sticky directory stops a non-owner renaming entries it
                # does not own, which is how /tmp protects root's files.
                reason = None
            if reason and node not in seen:
                seen.add(node)
                where = "it" if index == 0 else "a directory above it"
                findings.append(f"{node} ({where}): {reason}")
            child_uid = st.st_uid
    return findings


def tree_findings(root: str, uid: int, gids: frozenset[int]) -> list[str]:
    """`path_findings` for a directory plus every entry inside it."""
    findings = path_findings(root, uid, gids)
    if not os.path.isdir(root) or os.path.islink(root):
        return findings
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            node = os.path.join(base, name)
            try:
                reason = _writable(os.lstat(node), uid, gids)
            except FileNotFoundError:
                continue
            if reason:
                findings.append(f"{node}: {reason}")
    return findings


def user_ids(name: str) -> tuple[int, frozenset[int]]:
    entry = pwd.getpwnam(name)
    gids = {entry.pw_gid}
    gids.update(g.gr_gid for g in grp.getgrall() if name in g.gr_mem)
    return entry.pw_uid, frozenset(gids)


def _read_regular(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise SystemExit(f"not a regular file: {path}")
        return handle.read()


def promote(state_root: str, anchor_root: str, repository_id: int) -> list[str]:
    """Copy this repository's registries from the runtime-owned state root
    into `<anchor_root>/authority/<id>/`, read-only and in DELETE journal
    mode so a reader without write access still opens them. Returns the files
    written; as root they are root-owned.

    The source is copied byte-for-byte into a private directory before SQLite
    opens it, so root never creates a -shm sidecar in the runtime user's tree
    and never follows a symlink planted there."""
    repo = str(int(repository_id))
    source_dir = os.path.join(state_root, "authority", repo)
    real_root = os.path.realpath(state_root)
    if os.path.commonpath([real_root, os.path.realpath(source_dir)]) != real_root:
        raise SystemExit(f"registry directory escapes the state root: {source_dir}")
    target_dir = os.path.join(anchor_root, "authority", repo)
    dirs = (anchor_root, os.path.dirname(target_dir), target_dir)
    for directory in dirs:
        os.makedirs(directory, mode=0o755, exist_ok=True)
        os.chmod(directory, 0o755)
    written = []
    with tempfile.TemporaryDirectory() as scratch:
        for name in REGISTRY_FILES:
            source = os.path.join(source_dir, name)
            if not os.path.lexists(source):
                continue
            copy = os.path.join(scratch, name)
            for suffix in ("", "-wal"):
                try:
                    data = _read_regular(source + suffix)
                except FileNotFoundError:
                    if suffix:
                        continue
                    raise
                with open(copy + suffix, "wb") as handle:
                    handle.write(data)
            staged = os.path.join(target_dir, f".{name}.staged")
            for leftover in (staged, staged + "-journal"):
                if os.path.lexists(leftover):
                    os.unlink(leftover)
            reader = sqlite3.connect(copy)
            writer = sqlite3.connect(staged)
            try:
                reader.execute("PRAGMA trusted_schema=OFF")
                reader.backup(writer)
                writer.execute("PRAGMA journal_mode=DELETE")
            finally:
                writer.close()
                reader.close()
            os.chmod(staged, 0o644)
            if os.geteuid() == 0:
                os.chown(staged, 0, 0)
            target = os.path.join(target_dir, name)
            for sidecar in (target + "-wal", target + "-shm"):
                if os.path.lexists(sidecar):
                    os.unlink(sidecar)
            os.replace(staged, target)
            written.append(target)
    if os.geteuid() == 0:
        for directory in dirs:
            os.chown(directory, 0, 0)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="paths the runtime user could change")
    who = audit.add_mutually_exclusive_group(required=True)
    who.add_argument("--user")
    who.add_argument("--uid", type=int)
    audit.add_argument("--tree", action="append", default=[],
                       help="a directory whose every entry must be protected")
    audit.add_argument("paths", nargs="*")
    up = sub.add_parser("promote", help="install root-owned registry copies")
    up.add_argument("--state-root", required=True)
    up.add_argument("--anchor-root", required=True)
    up.add_argument("--repository-id", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "promote":
        for path in promote(args.state_root, args.anchor_root, args.repository_id):
            print(path)
        return 0
    if args.user:
        uid, gids = user_ids(args.user)
    else:
        uid = args.uid
        gids = frozenset(os.getgroups()) | {os.getegid()} \
            if uid == os.geteuid() else frozenset()
    findings = [f for p in args.paths for f in path_findings(p, uid, gids)]
    findings += [f for t in args.tree for f in tree_findings(t, uid, gids)]
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
