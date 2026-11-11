#!/usr/bin/env python3
"""Verify the installed Omnigent distribution against its exact Git lock."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any


SCHEMA = "nightshift-omnigent-runtime-lock-v1"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_KEYS = {"schema", "distribution", "repository", "commit", "version"}


class IdentityError(RuntimeError):
    """The lock or installed PEP 610 identity is absent or mismatched."""


def load_lock(path: Path) -> dict[str, str]:
    """Load and strictly validate the committed runtime lock."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityError(f"cannot read runtime lock {path}: {exc}") from exc
    if not isinstance(value, dict) or set(value) != EXPECTED_KEYS:
        raise IdentityError("runtime lock has missing or unknown fields")
    if any(not isinstance(value.get(key), str) for key in EXPECTED_KEYS):
        raise IdentityError("runtime lock fields must all be strings")
    lock = {key: value[key] for key in EXPECTED_KEYS}
    if lock["schema"] != SCHEMA:
        raise IdentityError(f"unsupported runtime lock schema: {lock['schema']!r}")
    if lock["distribution"] != "omnigent":
        raise IdentityError("runtime lock distribution must be 'omnigent'")
    if not FULL_SHA.fullmatch(lock["commit"]):
        raise IdentityError("runtime lock commit must be a full lowercase Git SHA")
    if not lock["version"] or any(ch.isspace() for ch in lock["version"]):
        raise IdentityError("runtime lock version is empty or contains whitespace")
    parsed = urllib.parse.urlsplit(lock["repository"])
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(".git")
    ):
        raise IdentityError(
            "runtime lock repository must be an uncredentialed HTTPS GitHub URL"
        )
    return lock


def requirement(lock: dict[str, str]) -> str:
    """Return the exact PEP 508 VCS requirement selected by the lock."""
    return f"{lock['distribution']} @ git+{lock['repository']}@{lock['commit']}"


def inspect_installed(lock: dict[str, str]) -> dict[str, Any]:
    """Read version and PEP 610 provenance from the active interpreter."""
    try:
        distribution = importlib.metadata.distribution(lock["distribution"])
    except importlib.metadata.PackageNotFoundError as exc:
        raise IdentityError("Omnigent distribution is not installed") from exc
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        raise IdentityError("installed Omnigent has no PEP 610 direct_url.json")
    try:
        direct_url = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdentityError("installed Omnigent direct_url.json is malformed") from exc
    if not isinstance(direct_url, dict):
        raise IdentityError("installed Omnigent direct_url.json is not an object")
    vcs = direct_url.get("vcs_info")
    if not isinstance(vcs, dict):
        raise IdentityError("installed Omnigent has no PEP 610 VCS identity")
    observed = {
        "version": distribution.version,
        "repository": direct_url.get("url"),
        "vcs": vcs.get("vcs"),
        "requested_revision": vcs.get("requested_revision"),
        "commit": vcs.get("commit_id"),
    }
    expected = {
        "version": lock["version"],
        "repository": lock["repository"],
        "vcs": "git",
        "requested_revision": lock["commit"],
        "commit": lock["commit"],
    }
    mismatches = [
        f"{key}: expected {expected[key]!r}, observed {observed[key]!r}"
        for key in expected
        if observed[key] != expected[key]
    ]
    if mismatches:
        raise IdentityError(
            "installed Omnigent identity mismatch: " + "; ".join(mismatches)
        )
    return {
        "schema": SCHEMA,
        "requirement": requirement(lock),
        "lock": lock,
        "observed": observed,
        "matched": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).with_name("omnigent-runtime.lock.json"),
    )
    parser.add_argument("command", choices=("requirement", "verify"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lock = load_lock(args.lock)
        if args.command == "requirement":
            print(requirement(lock))
        else:
            print(json.dumps(inspect_installed(lock), sort_keys=True))
    except IdentityError as exc:
        print(f"omnigent runtime identity error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
