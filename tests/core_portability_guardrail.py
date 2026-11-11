"""Core-portability guardrail -- issue #439, docs/specs/439-guardrail.md.

Nightshift Core (`bin/`, `deploy/`, `program/`) is meant to work on any
project. This module scans that path set for project-specific tokens
(toolchain/runner names, the concrete suite path `tests/run.sh`, the
`CANONICAL_DOMAINS` review-domain literal) and audits the hits against a
checked-in exemption manifest (`core_portability_manifest.json`), the same
shape as the #245 host-uid-skip-manifest precedent (`host_uid_ancestry.py`
+ `run_suite.py`): any hit not on the manifest fails, any manifest entry
that no longer matches anything fails, and every entry must be classified
`debt` (must carry an owning issue) or `structural` (must sit inside AC-11's
closed, path-enumerated allowlist -- a mechanical path check, not semantic
reasoning about intent, matching AC-2/AC-4's own "no comment-aware
special-casing" design).

The scan set is git-tracked blobs only (AC-2 v2): a plain filesystem walk
picks up `__pycache__`, build artifacts, and other untracked cruft, which is
exactly what made two independent scans against v1 of this spec disagree
with each other and with the repo (see docs/specs/439-guardrail.md v1
supersession notes). `list_files`/`read_file` are injectable so fixture
tests can point the same audit logic at a temp directory without needing a
real git repo there.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
MANIFEST_PATH = TESTS_DIR / "core_portability_manifest.json"

ListFiles = Callable[[Iterable[str], Path, str], list]
ReadFile = Callable[[str, Path], str]


class ManifestError(Exception):
    """Manifest missing/unparseable, a declared core path that doesn't exist,
    or Core content that can't be read as text -- all fail closed (spec S3)."""


@dataclass(frozen=True)
class Entry:
    file: str
    token: str
    category: Optional[str]
    reason: str
    issue: Optional[int]


@dataclass
class AuditResult:
    problems: list
    total: int
    debt: int
    structural: int

    @property
    def ok(self) -> bool:
        return not self.problems


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(f"{path}: cannot read manifest -- {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path}: unparseable JSON -- {e}") from e


def parse_entries(data: dict) -> list:
    return [
        Entry(
            file=raw["file"],
            token=raw["token"],
            category=raw.get("category"),
            reason=raw.get("reason", ""),
            issue=raw.get("issue"),
        )
        for raw in data.get("entries", [])
    ]


def _check_core_paths_exist(core_paths: Iterable[str], repo_root: Path) -> None:
    missing = [p for p in core_paths if not (repo_root / p).is_dir()]
    if missing:
        raise ManifestError(f"declared core path(s) do not exist: {sorted(missing)}")


def git_list_files(core_paths: Iterable[str], repo_root: Path, ref: str = "HEAD") -> list:
    """Default AC-2 scan-set: git-tracked blobs under core_paths at `ref`."""
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "--", *core_paths],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return [line for line in proc.stdout.splitlines() if line]


def walk_files(core_paths: Iterable[str], repo_root: Path, ref: str = "HEAD") -> list:
    """Plain filesystem walk, for fixture tests that don't need a git repo."""
    out = []
    for base in core_paths:
        for p in sorted((repo_root / base).rglob("*")):
            if p.is_file():
                out.append(str(p.relative_to(repo_root)))
    return out


def default_read_file(rel_path: str, repo_root: Path) -> str:
    return (repo_root / rel_path).read_text(encoding="utf-8")


def scan_hits(
    files: Iterable[str],
    tokens: Iterable[str],
    repo_root: Path,
    read_file: ReadFile = default_read_file,
) -> set:
    """AC-2: whole-file, literal, case-sensitive substring match. One hit per
    distinct (file, token) pair regardless of how many times a token repeats
    in that file (AC-2's hit-granularity rule)."""
    tokens = list(tokens)
    hits = set()
    for rel_path in files:
        try:
            text = read_file(rel_path, repo_root)
        except (OSError, UnicodeDecodeError) as e:
            raise ManifestError(f"{rel_path}: cannot read as text for scanning -- {e}") from e
        for tok in tokens:
            if tok in text:
                hits.add((rel_path, tok))
    return hits


def is_structural_allowed(file: str, allowlist: dict) -> bool:
    if any(file.startswith(prefix) for prefix in allowlist.get("path_prefixes", [])):
        return True
    return file in allowlist.get("paths", [])


def audit(
    data: dict,
    *,
    repo_root: Path = REPO_ROOT,
    ref: str = "HEAD",
    list_files: ListFiles = git_list_files,
    read_file: ReadFile = default_read_file,
) -> AuditResult:
    core_paths = data["core_paths"]
    tokens = data["tokens"]
    allowlist = data.get("structural_allowlist", {})
    entries = parse_entries(data)

    _check_core_paths_exist(core_paths, repo_root)
    files = list_files(core_paths, repo_root, ref)
    actual_hits = scan_hits(files, tokens, repo_root, read_file=read_file)
    manifest_pairs = {(e.file, e.token) for e in entries}

    problems = []

    for file, token in sorted(actual_hits - manifest_pairs):
        problems.append(f"unlisted hit not in manifest: {file!r} contains {token!r}")

    for file, token in sorted(manifest_pairs - actual_hits):
        problems.append(
            f"stale manifest entry no longer matches anything: {file!r} / {token!r}")

    debt = 0
    structural = 0
    for e in entries:
        if e.category == "debt":
            debt += 1
            if not e.issue:
                problems.append(f"debt entry missing owning issue: {e.file!r} / {e.token!r}")
        elif e.category == "structural":
            structural += 1
            if not is_structural_allowed(e.file, allowlist):
                problems.append(
                    f"structural claim outside AC-11 allowlist: {e.file!r} / {e.token!r}")
        else:
            problems.append(
                f"entry has no valid category (debt|structural), needs human "
                f"classification: {e.file!r} / {e.token!r}")

    return AuditResult(problems=problems, total=len(entries), debt=debt, structural=structural)


def summary_line(result: AuditResult) -> str:
    return (
        f"{MANIFEST_PATH.name}: {result.total} entries total, "
        f"{result.debt} debt (must only go down), {result.structural} structural"
    )


def regenerate(
    data: dict,
    *,
    repo_root: Path = REPO_ROOT,
    ref: str = "HEAD",
    list_files: ListFiles = git_list_files,
    read_file: ReadFile = default_read_file,
) -> dict:
    """AC-12: diff-based regeneration. Hand-set fields (category/reason/issue)
    are preserved on entries whose (file, token) pair still matches a real
    hit; brand-new hits land with category=None so `audit()` reports them as
    needing human classification (rather than silently landing as valid);
    entries that no longer match anything are dropped (AC-5)."""
    core_paths = data["core_paths"]
    tokens = data["tokens"]
    by_pair = {(e.file, e.token): e for e in parse_entries(data)}

    _check_core_paths_exist(core_paths, repo_root)
    files = list_files(core_paths, repo_root, ref)
    actual_hits = scan_hits(files, tokens, repo_root, read_file=read_file)

    new_entries = []
    for file, token in sorted(actual_hits):
        existing = by_pair.get((file, token))
        if existing is not None:
            new_entries.append({
                "file": existing.file, "token": existing.token,
                "category": existing.category, "reason": existing.reason,
                "issue": existing.issue,
            })
        else:
            new_entries.append({
                "file": file, "token": token,
                "category": None, "reason": "", "issue": None,
            })

    new_data = dict(data)
    new_data["entries"] = new_entries
    return new_data
