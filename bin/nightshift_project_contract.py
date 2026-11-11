#!/usr/bin/env python3
"""nightshift_project_contract.py -- deterministic project-stack discovery (#443).

Owner: this file only (Track B, #443 -- the detector).

Schema v2 (the seven unmet acceptance criteria closed in this revision):

  AC-1  Takes a repository path AND an exact commit SHA. Pure discovery: no
        network call, no git write. If the SHA is not present in the
        repository, REFUSE, naming the SHA -- never fall back to the
        working tree or to HEAD (the #103 defect shape). The only git entry
        point is `open_tree()` / `TreeAtSha`; the literal string "HEAD"
        never appears anywhere else in this module, and no detector ever
        receives a filesystem path -- every detector takes a `TreeAtSha`.

  AC-2  The contract attempts, at minimum, the 16 fields named in
        CONTRACT_FIELDS below: languages, runtime_version,
        dependency_manifests, lockfiles, build, test, integration, e2e,
        lint, format, typecheck, codegen, migrations, ci, ownership,
        excluded_dirs.

  AC-3  Every populated field carries non-empty `evidence` (repo-relative
        paths that exist in the tree at baseline_sha) and a `confidence` of
        exactly "observed" or "declared" -- never a float. A populated
        value with empty evidence is invalid and fails the run
        (validate_contract -> ContractInvalid -> exit 4).

  AC-4  A field that cannot be observed is `status: "absent"`, `value:
        null`, with a non-empty `reason` -- never inferred, never defaulted.

  AC-5  Where two sources disagree on a command, both are recorded as
        candidates and the field is marked `status: "conflict"` with
        `value: null` -- the tool never picks a winner.

  AC-6  The contract carries `baseline_sha` and a `contract_digest` (sha256
        over its own canonical form, with `contract_digest` and `local`
        excluded from that form) so a later consumer can prove which
        contract it read.

  AC-7  Re-running at the same SHA on the same tree is byte-identical.
        Integer scoring (no floats anywhere in the emitted JSON), sorted
        iteration everywhere a set or dict was previously walked, and
        json.dumps(..., sort_keys=True) make this true regardless of
        PYTHONHASHSEED or os.walk order -- see
        tests/test_project_contract.py's determinism test, which asserts
        it across a subprocess boundary rather than assuming it.

Design constraints carried over from v1, still non-negotiable:
  * Deterministic only. No LLM inference, no embeddings.
  * Evidence-based RANKED detection, never first-match.
  * Must report ambiguity rather than guess (a polyglot repo returns
    multiple language candidates, not a single silent pick).
  * stdlib only. No third-party YAML/TOML parser -- CI workflow command
    extraction is regex-based, on purpose (see _scan_ci_run_commands).

Usage:
    python3 nightshift_project_contract.py <repo_path> --sha <40-or-64-hex>

Library:
    from nightshift_project_contract import build_contract
    build_contract(repo_path, sha=sha) -> dict   # keyword-only, no default

Exit codes:
  0  contract emitted (including an all-absent contract for an unfamiliar
     repo layout -- AC-8, never a crash)
  2  usage error: repo_path is not a directory, or is not the repository's
     own toplevel
  3  ShaNotPresent -- the SHA refusal (AC-1), distinguishable from every
     other failure
  4  ContractInvalid -- validate_contract() failed (AC-3)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "2"

# The 16 envelope fields, in the exact order the schema names them. Tests
# and validate_contract() iterate this generically rather than hardcoding
# the field list twice.
CONTRACT_FIELDS: Tuple[str, ...] = (
    "languages", "runtime_version", "dependency_manifests", "lockfiles",
    "build", "test", "integration", "e2e", "lint", "format", "typecheck",
    "codegen", "migrations", "ci", "ownership", "excluded_dirs",
)

# contract_digest is excluded because a field can't be hashed over itself;
# local is excluded because it holds an absolute machine path and a locally
# installed git version -- environment, not contract, and digesting it
# would make the digest non-portable across clones of the identical tree.
DIGEST_EXCLUDED_TOP_LEVEL_KEYS: Tuple[str, ...] = ("contract_digest", "local")

SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ShaNotPresent(Exception):
    """The requested SHA does not resolve to a commit in this repository.

    Never caught anywhere in this module by falling back to HEAD or the
    working tree -- that fallback is the #103 defect shape this exception
    exists to make structurally impossible. Maps to CLI exit 3.
    """


class ContractInvalid(Exception):
    """validate_contract() found a shape violation in an assembled
    contract -- the run fails rather than emitting something that looks
    well-formed but violates the envelope's own state table (AC-3). Maps
    to CLI exit 4.
    """


# ---------------------------------------------------------------------------
# git plumbing -- the ONLY place this module talks to git. Every detector
# below receives a TreeAtSha and never a filesystem Path; the no-fallback
# property (AC-1) is enforced by the type of the argument, not discipline.
# ---------------------------------------------------------------------------

_GIT_ENV_DROP = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR", "GIT_NAMESPACE",
)


def _git_env() -> Dict[str, str]:
    """A scrubbed environment for every git subprocess this module runs.

    Drops every variable that could redirect git at a different repo, index
    or object store than the one `-C <repo>` names (plausible inside
    Nightshift's own worktrees/agent harnesses), and pins locale/config so
    output (ls-tree path quoting in particular) never depends on the
    caller's machine. GIT_TERMINAL_PROMPT=0 plus never invoking fetch/pull/
    ls-remote anywhere in this module is what makes "no network call" true.
    """
    env = dict(os.environ)
    for key in list(env):
        if key in _GIT_ENV_DROP or key.startswith("GIT_CONFIG"):
            env.pop(key, None)
    env.update({
        "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat", "PAGER": "cat",
    })
    return env


def _run_git(repo: Path, args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo)] + args,
        capture_output=True, env=_git_env(), timeout=timeout,
    )


def git_version() -> str:
    try:
        proc = subprocess.run(["git", "--version"], capture_output=True,
                               env=_git_env(), timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    m = re.search(r"(\d+\.\d+(?:\.\d+)*)", text)
    return m.group(1) if m else (text or "unknown")


# `git ls-tree -r -l -z --full-tree <sha>` record shape:
#   <mode> SP <type> SP <objsha> SP+ <size> TAB <path> NUL
# size is space-padded by porcelain; matched as \S+ then int()'d.
_LS_TREE_REC_RE = re.compile(
    rb"^(?P<mode>[0-7]+) (?P<type>\S+) (?P<sha>[0-9a-f]+)[ \t]+(?P<size>\S+)\t")


class TreeAtSha:
    """The only way a detector reads repo content. Backed by one long-lived
    `git cat-file --batch` process against `sha` -- never the working tree,
    never the index, never HEAD.

    Request/response are interleaved one at a time (write a query, flush,
    read its header + payload, then write the next) rather than batched --
    writing many requests before reading any response deadlocks once the
    pipe buffer fills (~64KB), and this class exists specifically to avoid
    per-file subprocess spawns, which do not scale to a 55k-file tree.
    """

    def __init__(self, repo: Path, sha: str, paths: Tuple[str, ...], sizes: Dict[str, int]):
        self.repo = repo
        self.sha = sha
        self.paths: Tuple[str, ...] = paths
        self.sizes: Dict[str, int] = sizes
        self._pathset = frozenset(paths)
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_git_env(),
        )

    def size(self, path: str) -> Optional[int]:
        return self.sizes.get(path)

    def read(self, path: str, max_bytes: int = 65536) -> Optional[bytes]:
        """Full blob content for `path` at this SHA, truncated to
        `max_bytes` -- but the FULL payload is always drained off the pipe
        first (a blob larger than max_bytes still desynchronizes the
        stream for every subsequent read if it isn't), keeping only the
        prefix requested.
        """
        if path not in self._pathset:
            return None
        # `--batch` reads one query per LINE: a path holding "\n" would split
        # into two queries and desync every later read, and a trailing "\r"
        # is stripped as CRLF and names a different path. Such paths are
        # skipped, never sent. Paths were decoded with surrogateescape, so
        # encoding the same way restores a non-UTF-8 name's exact bytes.
        if "\n" in path or "\r" in path:
            return None
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(
            f"{self.sha}:{path}\n".encode("utf-8", errors="surrogateescape"))
        self._proc.stdin.flush()
        header = self._proc.stdout.readline()
        if not header:
            return None
        parts = header.split()
        if len(parts) < 3 or parts[-1] == b"missing":
            return None
        try:
            size = int(parts[2])
        except ValueError:
            return None
        remaining = size
        kept = bytearray()
        while remaining > 0:
            chunk = self._proc.stdout.read(min(remaining, 1 << 20))
            if not chunk:
                break
            remaining -= len(chunk)
            if len(kept) < max_bytes:
                kept.extend(chunk[:max_bytes - len(kept)])
        self._proc.stdout.read(1)  # trailing newline after the object payload
        return bytes(kept)

    def read_text(self, path: str, max_bytes: int = 65536) -> Optional[str]:
        data = self.read(path, max_bytes=max_bytes)
        if data is None:
            return None
        # surrogateescape: a non-UTF8 tracked file must never crash
        # discovery; it decodes lossily and stays that way, matching how
        # the paths themselves were decoded in open_tree().
        return data.decode("utf-8", errors="surrogateescape")

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            pass
        try:
            if self._proc.stdout:
                self._proc.stdout.close()
        except OSError:
            pass

    def __enter__(self) -> "TreeAtSha":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def open_tree(repo_path: str, sha: str) -> TreeAtSha:
    """The sole git entry point. Every failure mode that would otherwise
    silently stamp the ambient checkout instead raises here, before any
    detector ever runs.
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"not a directory: {repo}")

    top = _run_git(repo, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        raise NotADirectoryError(f"not a git repository: {repo}")
    toplevel = Path(top.stdout.decode("utf-8", errors="replace").strip()).resolve()
    if toplevel != repo:
        raise NotADirectoryError(
            f"repo_path is not the repository toplevel: {repo} (toplevel is {toplevel}); "
            "pass the repository root, not a subdirectory")

    if not SHA_RE.match(sha):
        raise ShaNotPresent(
            f"not a well-formed 40 or 64 character lowercase hex commit SHA: {sha!r}")

    verify = _run_git(repo, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
    resolved = verify.stdout.decode("utf-8", errors="replace").strip()
    if verify.returncode != 0 or resolved != sha:
        raise ShaNotPresent(
            f"SHA not present in repository (does not resolve to a commit): {sha}")

    lst = _run_git(repo, ["ls-tree", "-r", "-l", "-z", "--full-tree", sha])
    if lst.returncode != 0:
        raise ShaNotPresent(
            f"failed to list tree at {sha}: {lst.stderr.decode('utf-8', 'replace').strip()}")

    paths: List[str] = []
    sizes: Dict[str, int] = {}
    for rec in lst.stdout.split(b"\x00"):
        if not rec:
            continue
        m = _LS_TREE_REC_RE.match(rec)
        if not m:
            continue
        if m.group("type") != b"blob":
            continue  # a "commit" type here is a submodule gitlink, never a blob to read
        path = rec[m.end():].decode("utf-8", errors="surrogateescape")
        paths.append(path)
        try:
            sizes[path] = int(m.group("size"))
        except ValueError:
            pass

    return TreeAtSha(repo, sha, tuple(sorted(paths)), sizes)


# ---------------------------------------------------------------------------
# The envelope -- identical 8 keys on every field, always all 8 present.
# These four constructors are the ONLY way a field's envelope is built; no
# detector hand-assembles one.
# ---------------------------------------------------------------------------

def observed(value: Dict[str, Any], evidence: List[str], candidates: List[Dict[str, Any]],
             rejected: List[Dict[str, Any]] = (), unverified: bool = False) -> Dict[str, Any]:
    return {
        "status": "observed", "value": value, "confidence": "observed",
        "evidence": sorted(set(evidence)), "reason": None,
        "unverified": bool(unverified),
        "candidates": list(candidates), "rejected": list(rejected),
    }


def declared(value: Dict[str, Any], evidence: List[str], candidates: List[Dict[str, Any]],
             rejected: List[Dict[str, Any]] = (), unverified: bool = False) -> Dict[str, Any]:
    return {
        "status": "declared", "value": value, "confidence": "declared",
        "evidence": sorted(set(evidence)), "reason": None,
        "unverified": bool(unverified),
        "candidates": list(candidates), "rejected": list(rejected),
    }


def conflict(candidates: List[Dict[str, Any]], reason: str,
             rejected: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
    confs = {c["confidence"] for c in candidates}
    conf = "declared" if "declared" in confs else "observed"
    evidence = sorted({p for c in candidates for p in c["evidence"]})
    return {
        "status": "conflict", "value": None, "confidence": conf,
        "evidence": evidence, "reason": reason, "unverified": False,
        "candidates": list(candidates), "rejected": list(rejected),
    }


def absent(reason: str, candidates: List[Dict[str, Any]] = (),
           rejected: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
    return {
        "status": "absent", "value": None, "confidence": None,
        "evidence": [], "reason": reason, "unverified": False,
        "candidates": list(candidates), "rejected": list(rejected),
    }


# source_kind -> confidence, the one AC-3 table, no exceptions. A file that
# literally states the command (ci_observed/makefile/manifest_script/
# config_file/lockfile/tree_layout) is "observed"; a manifest that asserts
# an ecosystem without this tool verifying the string it synthesized is
# "declared" (manifest_plugin/manifest_convention).
_SOURCE_KIND_CONFIDENCE: Dict[str, str] = {
    "ci_observed": "observed", "makefile": "observed", "manifest_script": "observed",
    "config_file": "observed", "lockfile": "observed", "tree_layout": "observed",
    "manifest_plugin": "declared", "manifest_convention": "declared",
}

# Ordering only, never emitted as confidence: a command copied verbatim out
# of the CI file that actually gates merges outranks a manifest-declared
# plugin default, which outranks this tool's own bare ecosystem convention.
_SOURCE_KIND_RANK: Dict[str, int] = {
    "ci_observed": 900, "makefile": 800, "manifest_script": 800,
    "config_file": 800, "lockfile": 800, "tree_layout": 750,
    "manifest_plugin": 700, "manifest_convention": 500,
}

_STANDARD_TOOLCHAIN_BINARIES = frozenset({
    "go", "mvn", "gradle", "cargo", "npm", "npx", "yarn", "python", "python3",
    "pip", "docker", "make", "git",
})

# A handful of root-level manifests worth a substring check when deciding
# whether a bare (non-path) binary name is "declared" by something tracked.
_DEP_DECLARATION_FILES = (
    "package.json", "pom.xml", "go.mod", "requirements.txt", "pyproject.toml",
    "Cargo.toml", "build.gradle", "build.gradle.kts", ".golangci.yml", ".golangci.yaml",
    "Gemfile", "composer.json",
)


def _first_token(cmd: str) -> str:
    parts = cmd.strip().split()
    return parts[0] if parts else ""


def _compute_unverified(cmd: Optional[str], tree: Optional[TreeAtSha]) -> bool:
    """True iff the command names a repo path not in the tree at
    baseline_sha, or a bare binary no tracked file declares. This slice
    never executes project commands -- it only checks whether the thing
    named is at least plausibly there.
    """
    if not cmd or tree is None:
        return False
    token = _first_token(cmd)
    if not token:
        return False
    if token.startswith("./") or token.startswith("/") or "/" in token:
        rel = token[2:] if token.startswith("./") else token.lstrip("/")
        return rel not in tree._pathset
    if token in _STANDARD_TOOLCHAIN_BINARIES:
        return False
    for manifest in _DEP_DECLARATION_FILES:
        text = tree.read_text(manifest, max_bytes=131072)
        if not text:
            continue
        # A manifest-sourced command (package.json's scripts.lint, say)
        # necessarily contains its OWN text verbatim in that same manifest
        # -- checking `token in text` unscrubbed would make every bare-
        # binary npm script trivially "declared" by reciting itself, which
        # is exactly the false-negative the issue's own example names
        # ("lint": "totally-absent-binary ."). Strip the command's own
        # occurrence first; what must survive is some OTHER part of the
        # manifest (a dependency, a comment, a different script) naming the
        # binary, not the command declaring itself.
        scrubbed = text.replace(cmd, "") if cmd in text else text
        if token in scrubbed:
            return False
    return True


def make_candidate(value: Dict[str, Any], source_kind: str, evidence: List[str],
                    notes: List[str] = (), unverified: bool = False,
                    rank: Optional[int] = None) -> Dict[str, Any]:
    return {
        "value": value,
        "confidence": _SOURCE_KIND_CONFIDENCE[source_kind],
        "evidence": sorted(set(evidence)),
        "source_kind": source_kind,
        "rank": rank if rank is not None else _SOURCE_KIND_RANK[source_kind],
        "unverified": bool(unverified),
        "notes": list(notes),
    }


def make_rejection(value: Dict[str, Any], source_kind: str, evidence: List[str],
                    reason: str, notes: List[str] = ()) -> Dict[str, Any]:
    return {
        "value": value, "source_kind": source_kind,
        "evidence": sorted(set(evidence)), "reason": reason, "notes": list(notes),
    }


def _raw_candidate(value: Dict[str, Any], source_kind: str, evidence: List[str],
                    lang: Optional[str], notes: List[str] = (),
                    unverified: Optional[bool] = None, rank: Optional[int] = None,
                    tree: Optional[TreeAtSha] = None) -> Dict[str, Any]:
    """A `make_candidate()` result plus an internal `lang` tag used for
    stack-gating (`_gate_lang`) and then stripped before the candidate is
    emitted -- see `_strip_lang`. `unverified`, when not given explicitly,
    is computed from the value's own `command` field via
    `_compute_unverified`.
    """
    if unverified is None:
        cmd = value.get("command") if isinstance(value, dict) else None
        unverified = _compute_unverified(cmd, tree)
    c = make_candidate(value, source_kind, evidence, notes=notes, unverified=unverified, rank=rank)
    c["lang"] = lang
    return c


def _strip_lang(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in c.items() if k != "lang"}


_WS_RE = re.compile(r"\s+")


def _normalize_value(value: Any) -> str:
    """Dedup/ordering key for candidate values. Command-shaped values
    compare on their COMMAND TEXT ONLY (casefold + whitespace-collapsed,
    flags preserved on purpose -- `go test ./...` vs `go test ./... -short`
    IS the disagreement AC-5 exists to surface); everything else compares
    on its full canonical JSON.
    """
    if isinstance(value, dict) and "command" in value and isinstance(value["command"], str):
        return _WS_RE.sub(" ", value["command"].strip()).casefold()
    return json.dumps(value, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Language identity tables (unchanged detection surface from v1).
# ---------------------------------------------------------------------------

EXTENSION_LANG: Dict[str, str] = {
    "go": "go", "py": "python", "pyi": "python",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "java": "java", "kt": "kotlin", "kts": "kotlin",
    "rs": "rust", "rb": "ruby", "php": "php", "cs": "csharp",
    "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp", "hpp": "cpp",
    "swift": "swift", "scala": "scala",
    "ex": "elixir", "exs": "elixir", "erl": "erlang", "clj": "clojure", "hs": "haskell",
}

ROOT_MANIFESTS: Dict[str, str] = {
    "go.mod": "go", "package.json": "javascript", "tsconfig.json": "typescript",
    "requirements.txt": "python", "pyproject.toml": "python", "setup.py": "python",
    "setup.cfg": "python", "Pipfile": "python",
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "kotlin",
    "Cargo.toml": "rust", "Gemfile": "ruby", "composer.json": "php", "mix.exs": "elixir",
    "*.csproj": "csharp", "*.sln": "csharp",
}

SHEBANG_LANG: Dict[str, str] = {
    "python3": "python", "python": "python", "python2": "python",
    "bash": "shell", "sh": "shell", "zsh": "shell",
    "node": "javascript", "ruby": "ruby", "perl": "perl",
}

_SHEBANG_RE = re.compile(r"^#!\s*(?:/usr/bin/env\s+)?(?:/[^\s]*/)?([A-Za-z0-9_]+)")

# Integer scoring only -- no float appears anywhere in the emitted JSON
# (AC-7: float summation order is not associative and previously perturbed
# the last bit of a rounded score across PYTHONHASHSEED values).
ROOT_MANIFEST_SCORE = 1_000_000_000
SHEBANG_FILE_SCORE = 5_000_000
FILE_COUNT_SCORE = 1_000_000
BYTE_SCORE_PER_KIB = 50_000

# 15% of the 0..1_000_000 share_ppm range -- same margin v1 used, just
# expressed in integer parts-per-million instead of a float gap.
AMBIGUITY_MARGIN_PPM = 150_000


def _detect_root_manifests(tracked_root: frozenset) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for name, lang in ROOT_MANIFESTS.items():
        if name.startswith("*."):
            suffix = name[1:]
            for p in tracked_root:
                if p.endswith(suffix):
                    found.setdefault(lang, []).append(p)
        elif name in tracked_root:
            found.setdefault(lang, []).append(name)
    for lang in found:
        found[lang] = sorted(found[lang])
    return found


def _detect_shebang_langs(tree: TreeAtSha) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for p in detection_paths(tree):
        base = p.rsplit("/", 1)[-1]
        if "." in base:
            continue
        text = tree.read_text(p, max_bytes=256)
        if not text:
            continue
        first_line = text.splitlines()[0] if text.splitlines() else ""
        m = _SHEBANG_RE.match(first_line)
        if not m:
            continue
        lang = SHEBANG_LANG.get(m.group(1))
        if lang:
            found.setdefault(lang, []).append(p)
    for lang in found:
        found[lang] = sorted(found[lang])
    return found


# ---------------------------------------------------------------------------
# Detection scope -- which tracked paths describe THIS project
# ---------------------------------------------------------------------------

FIXTURE_DIRNAMES: Tuple[str, ...] = (
    "fixtures", "__fixtures__", "test-fixtures", "test_fixtures",
    "testFixtures", "testdata", "test-data", "test_data",
)


def _is_out_of_detection_scope(path: str) -> bool:
    """True when `path` belongs to a vendored dependency or a test corpus.

    These files are real and tracked, but they describe some OTHER project --
    a fixture repository, a vendored library -- not the one being compiled.
    Counting them is how a stdlib-only Python repository that carries a Java
    fixture reports itself as a Maven/npm polyglot: exactly what landing
    `tests/fixtures/dogfood_b_java/` did to this repository's own contract.

    Only directory segments are examined, never the basename, so a file
    honestly named `fixtures.py` in real source is still counted.

    These paths are excluded from detection but NOT hidden: they remain in
    `tree.paths` and `build_excluded_dirs_field()` still reports them.
    """
    return any(seg in FIXTURE_DIRNAMES or seg in WELL_KNOWN_VENDOR_DIRNAMES
               for seg in path.split("/")[:-1])


def detection_paths(tree: TreeAtSha) -> Tuple[str, ...]:
    """The tracked paths that describe this project, at this SHA."""
    return tuple(p for p in tree.paths if not _is_out_of_detection_scope(p))


def detect_languages_value(tree: TreeAtSha) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Returns (value, evidence) for the `languages` field, or (None, [])
    when nothing was found at all (the field is then `absent`).
    """
    in_scope = detection_paths(tree)
    tracked_root = frozenset(p for p in in_scope if "/" not in p)
    manifests = _detect_root_manifests(tracked_root)
    shebangs = _detect_shebang_langs(tree)

    per_lang_files: Dict[str, List[str]] = {}
    per_lang_bytes: Dict[str, int] = {}
    for p in in_scope:
        base = p.rsplit("/", 1)[-1]
        if "." not in base:
            continue
        ext = base.rsplit(".", 1)[-1].lower()
        lang = EXTENSION_LANG.get(ext)
        if not lang:
            continue
        per_lang_files.setdefault(lang, []).append(p)
        per_lang_bytes[lang] = per_lang_bytes.get(lang, 0) + (tree.size(p) or 0)

    all_langs = sorted(set(manifests) | set(shebangs) | set(per_lang_files))
    if not all_langs:
        return None, []

    scores: Dict[str, int] = {}
    for lang in all_langs:
        score = 0
        if lang in manifests:
            score += ROOT_MANIFEST_SCORE * len(manifests[lang])
        if lang in shebangs:
            score += SHEBANG_FILE_SCORE * len(shebangs[lang])
        n_files = len(per_lang_files.get(lang, []))
        n_bytes = per_lang_bytes.get(lang, 0)
        score += FILE_COUNT_SCORE * n_files
        score += (n_bytes * BYTE_SCORE_PER_KIB) // 1024
        scores[lang] = score

    total = sum(scores.values()) or 1
    # Total order: score descending, then language name -- a real tie
    # (equal score) is broken by name, never by set/dict iteration order.
    ranked = sorted(all_langs, key=lambda l: (-scores[l], l))

    all_entries: List[Dict[str, Any]] = []
    evidence: List[str] = []
    for lang in ranked:
        share_ppm = (scores[lang] * 1_000_000) // total
        files = per_lang_files.get(lang, [])
        all_entries.append({
            "language": lang, "share_ppm": share_ppm,
            "has_root_manifest": lang in manifests,
            "files": len(files), "bytes": per_lang_bytes.get(lang, 0),
        })
        evidence.extend(manifests.get(lang, []))
        evidence.extend(shebangs.get(lang, []))
        evidence.extend(sorted(files)[:3])

    primary = all_entries[0]["language"]
    ambiguous = False
    if len(all_entries) > 1:
        gap = all_entries[0]["share_ppm"] - all_entries[1]["share_ppm"]
        top_has_manifest = all_entries[0]["has_root_manifest"]
        second_has_manifest = all_entries[1]["has_root_manifest"]
        if gap < AMBIGUITY_MARGIN_PPM and not (top_has_manifest and not second_has_manifest):
            ambiguous = True

    value = {"primary": primary, "ambiguous": ambiguous, "all": all_entries}
    return value, sorted(set(evidence))


def build_languages_field(tree: TreeAtSha) -> Dict[str, Any]:
    value, evidence = detect_languages_value(tree)
    if value is None:
        return absent(
            "no root manifest, no recognised source extension, and no shebang evidence "
            "found among tracked files at this SHA")
    candidate = make_candidate(
        value, "tree_layout", evidence,
        notes=[f"{len(value['all'])} language(s) ranked from root manifests, shebangs, "
               "and tracked file/byte counts"])
    return observed(value, evidence, [candidate])


# ---------------------------------------------------------------------------
# runtime_version
# ---------------------------------------------------------------------------

def build_runtime_version_field(tree: TreeAtSha, primary_lang: str) -> Dict[str, Any]:
    tracked_root = frozenset(p for p in tree.paths if "/" not in p)

    if primary_lang == "go":
        text = tree.read_text("go.mod", max_bytes=8192) if "go.mod" in tracked_root else None
        if text:
            m = re.search(r"(?m)^go[ \t]+(\d+\.\d+(?:\.\d+)?)", text)
            if m:
                value = {"language": "go", "version": m.group(1), "constraint": f"go {m.group(1)}"}
                return observed(value, ["go.mod"], [make_candidate(
                    value, "config_file", ["go.mod"], notes=["go.mod 'go' directive"])])
        return absent("go.mod not tracked at this SHA, or has no 'go <version>' directive")

    if primary_lang in ("java", "kotlin"):
        text = tree.read_text("pom.xml", max_bytes=2_000_000) if "pom.xml" in tracked_root else None
        if text:
            for tag in ("maven.compiler.release", "maven.compiler.target", "java.version"):
                m = re.search(rf"<{re.escape(tag)}>\s*([0-9.]+)\s*</{re.escape(tag)}>", text)
                if m:
                    version = m.group(1)
                    value = {"language": "java", "version": version, "constraint": version}
                    return observed(value, ["pom.xml"], [make_candidate(
                        value, "config_file", ["pom.xml"], notes=[f"<{tag}>"])])
        for gname in ("build.gradle", "build.gradle.kts"):
            if gname not in tracked_root:
                continue
            gtext = tree.read_text(gname, max_bytes=131072)
            if not gtext:
                continue
            m = re.search(
                r"sourceCompatibility\s*=?\s*['\"]?(?:JavaVersion\.VERSION_)?([0-9._]+)['\"]?",
                gtext)
            if m:
                version = m.group(1).replace("_", ".")
                value = {"language": "java", "version": version, "constraint": version}
                return observed(value, [gname], [make_candidate(
                    value, "config_file", [gname], notes=["sourceCompatibility"])])
        return absent(
            "no maven.compiler.release/target or java.version in pom.xml, and no "
            "sourceCompatibility in build.gradle(.kts), at this SHA")

    if primary_lang == "python":
        text = (tree.read_text("pyproject.toml", max_bytes=131072)
                if "pyproject.toml" in tracked_root else None)
        if text:
            m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
            if m:
                value = {"language": "python", "version": None, "constraint": m.group(1)}
                return observed(value, ["pyproject.toml"], [make_candidate(
                    value, "config_file", ["pyproject.toml"], notes=["requires-python"])])
        if ".python-version" in tracked_root:
            pv = tree.read_text(".python-version", max_bytes=64)
            if pv and pv.strip():
                version = pv.strip().splitlines()[0]
                value = {"language": "python", "version": version, "constraint": version}
                return observed(value, [".python-version"],
                                 [make_candidate(value, "config_file", [".python-version"])])
        return absent(
            "no requires-python in pyproject.toml and no .python-version file at this SHA")

    if primary_lang in ("javascript", "typescript"):
        for p in tree.paths:
            if p.rsplit("/", 1)[-1] != "package.json":
                continue
            text = tree.read_text(p, max_bytes=131072)
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            engines = data.get("engines")
            node = engines.get("node") if isinstance(engines, dict) else None
            if node:
                value = {"language": "node", "version": None, "constraint": str(node)}
                return observed(value, [p], [make_candidate(
                    value, "config_file", [p], notes=["engines.node"])])
        return absent("no package.json declares engines.node at this SHA")

    return absent(f"primary language is {primary_lang!r}; no runtime-version detector "
                  "implemented for it")


# ---------------------------------------------------------------------------
# dependency_manifests / lockfiles
# ---------------------------------------------------------------------------

_DEP_MANIFEST_ECOSYSTEM: Dict[str, str] = {
    "go.mod": "go", "package.json": "npm", "pyproject.toml": "python",
    "requirements.txt": "python", "setup.py": "python", "setup.cfg": "python",
    "Pipfile": "python", "pom.xml": "maven", "build.gradle": "gradle",
    "build.gradle.kts": "gradle", "Cargo.toml": "cargo", "Gemfile": "bundler",
    "composer.json": "composer", "mix.exs": "hex",
}
_DEP_MANIFEST_SUFFIX_ECOSYSTEM: Dict[str, str] = {".csproj": "nuget", ".sln": "nuget"}


def build_dependency_manifests_field(tree: TreeAtSha) -> Dict[str, Any]:
    files: List[Dict[str, str]] = []
    for p in detection_paths(tree):
        base = p.rsplit("/", 1)[-1]
        eco = _DEP_MANIFEST_ECOSYSTEM.get(base)
        if eco is None:
            for suffix, e in _DEP_MANIFEST_SUFFIX_ECOSYSTEM.items():
                if base.endswith(suffix):
                    eco = e
                    break
        if eco:
            files.append({"path": p, "ecosystem": eco})
    if not files:
        return absent(
            "no recognised dependency manifest (go.mod, package.json, pyproject.toml, "
            "requirements.txt, setup.py/.cfg, Pipfile, pom.xml, build.gradle(.kts), "
            "Cargo.toml, Gemfile, composer.json, mix.exs, *.csproj/*.sln) tracked at this SHA")
    files.sort(key=lambda f: f["path"])
    value = {"files": files}
    evidence = sorted({f["path"] for f in files})
    candidate = make_candidate(value, "tree_layout", evidence,
                                notes=[f"{len(files)} manifest file(s) matched by filename"])
    return observed(value, evidence, [candidate])


_LOCKFILE_ECOSYSTEM: Dict[str, str] = {
    "go.sum": "go", "package-lock.json": "npm", "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm", "poetry.lock": "poetry", "Pipfile.lock": "pipenv",
    "Cargo.lock": "cargo", "Gemfile.lock": "bundler", "composer.lock": "composer",
    "mix.lock": "hex",
}


def build_lockfiles_field(tree: TreeAtSha) -> Dict[str, Any]:
    files: List[Dict[str, str]] = []
    for p in detection_paths(tree):
        eco = _LOCKFILE_ECOSYSTEM.get(p.rsplit("/", 1)[-1])
        if eco:
            files.append({"path": p, "ecosystem": eco})
    if not files:
        return absent(
            "no recognised lockfile (go.sum, package-lock.json, yarn.lock, pnpm-lock.yaml, "
            "poetry.lock, Pipfile.lock, Cargo.lock, Gemfile.lock, composer.lock, mix.lock) "
            "tracked at this SHA")
    files.sort(key=lambda f: f["path"])
    value = {"files": files}
    evidence = sorted({f["path"] for f in files})
    candidate = make_candidate(value, "lockfile", evidence,
                                notes=[f"{len(files)} lockfile(s) matched by filename"])
    return observed(value, evidence, [candidate])


# ---------------------------------------------------------------------------
# CI system detection
# ---------------------------------------------------------------------------

CI_MARKERS: List[Tuple[str, str]] = [
    ("github_actions", ".github/workflows"),
    ("semaphore", ".semaphore"),
    ("gitlab_ci", ".gitlab-ci.yml"),
    ("circleci", ".circleci/config.yml"),
    ("travis", ".travis.yml"),
    ("azure_pipelines", "azure-pipelines.yml"),
    ("buildkite", ".buildkite/pipeline.yml"),
    ("jenkins", "Jenkinsfile"),
    ("drone", ".drone.yml"),
]


# `${{ ... }}` is a CI-expression placeholder that only the runner expands.
# A string containing one is a template, not a fact about this repository.
EXPRESSION_MARKER = "${{"


_JOBS_KEY_RE = re.compile(r"^jobs:[ \t]*(?:#.*)?$")
_JOB_ID_RE = re.compile(r"^(?P<indent>[ \t]+)(?P<id>[A-Za-z0-9_.-]+):[ \t]*(?:#.*)?$")
_JOB_NAME_RE = re.compile(r"^(?P<indent>[ \t]+)name:[ \t]*(?P<val>\S.*)$")


def _extract_job_check_names(text: str) -> List[str]:
    """The check names a GitHub Actions workflow's `jobs:` map actually
    produces -- a job's `name:` if it declares one, else the job id
    itself. This is what a required-status-check in branch protection
    keys against; the workflow-level `name:` (top of the file) is a
    different string and must not be substituted for it (a job with no
    `name:` -- the common case -- has no top-level `name:` occurrence at
    all at the job level, so the two are never interchangeable).

    Regex/indentation based, deliberately, matching this module's
    stdlib-only no-YAML-parser design (module docstring). Handles the
    common shape (a flat `jobs:` map, one `name:` at most per job, no
    matrix expansion) and is silent rather than wrong on anything more
    exotic -- reusable-workflow call jobs and matrix jobs produce
    additional/renamed checks this does not attempt to predict.
    """
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n and not _JOBS_KEY_RE.match(lines[i]):
        i += 1
    if i >= n:
        return []
    i += 1

    checks: List[str] = []
    job_indent: Optional[int] = None
    j = i
    while j < n:
        raw = lines[j]
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            j += 1
            continue
        cur_indent = len(raw) - len(raw.lstrip(" \t"))
        if job_indent is None:
            job_indent = cur_indent
        if cur_indent < job_indent:
            break
        if cur_indent != job_indent:
            j += 1
            continue
        m_id = _JOB_ID_RE.match(raw)
        if not m_id:
            j += 1
            continue
        job_id = m_id.group("id")

        # Body of this one job: every line more indented than job_indent,
        # up to the next line at job_indent or shallower. Job-level KEYS
        # (runs-on, steps, name, if, needs, ...) all share one indent --
        # the indent of the first non-blank body line -- so a `name:`
        # match at any DEEPER indent is a step's `name:`, not the job's,
        # and must not be mistaken for it.
        k = j + 1
        base_indent: Optional[int] = None
        job_name: Optional[str] = None
        while k < n:
            braw = lines[k]
            if braw.strip() == "" or braw.lstrip().startswith("#"):
                k += 1
                continue
            bindent = len(braw) - len(braw.lstrip(" \t"))
            if bindent <= job_indent:
                break
            if base_indent is None:
                base_indent = bindent
            if bindent == base_indent:
                m_name = _JOB_NAME_RE.match(braw)
                if m_name:
                    job_name = _strip_matching_quotes(m_name.group("val").strip())
            k += 1
        # A name carrying an unexpanded `${{ }}` expression is a matrix job:
        # the checks it actually produces are one per matrix leg, and the
        # literal template is not the name of any of them. Emitting it would
        # be worse than emitting nothing, because callers compare check names
        # for equality -- so stay silent, as the docstring promises.
        if job_name and EXPRESSION_MARKER in job_name:
            j = k
            continue
        checks.append(job_name if job_name else job_id)
        j = k
    return checks


def build_ci_field(tree: TreeAtSha) -> Dict[str, Any]:
    tracked_set = set(tree.paths)
    systems: List[Dict[str, Any]] = []
    evidence_all: List[str] = []

    for system, marker in CI_MARKERS:
        if marker in (".github/workflows", ".semaphore"):
            prefix = marker + "/"
            paths = sorted(p for p in tracked_set if p.startswith(prefix)
                            and (p.endswith(".yml") or p.endswith(".yaml")))
            if not paths:
                continue
            checks: List[str] = []
            for wf in paths:
                text = tree.read_text(wf, max_bytes=262144) or ""
                if system == "github_actions":
                    # The check GitHub Actions produces per job is the job's
                    # `name:` if it declares one, else the job id -- never
                    # the workflow-level `name:` at the top of the file
                    # (see _extract_job_check_names).
                    checks.extend(_extract_job_check_names(text))
                else:
                    m = re.search(r"(?m)^name:\s*(.+)$", text)
                    if m:
                        checks.append(m.group(1).strip().strip("\"'"))
            systems.append({"system": system, "workflows": paths, "checks": sorted(set(checks))})
            evidence_all.extend(paths)
        elif marker in tracked_set:
            systems.append({"system": system, "workflows": [marker], "checks": []})
            evidence_all.append(marker)

    if not systems:
        return absent(
            "no recognised CI system found (checked GitHub Actions, Semaphore, GitLab CI, "
            "CircleCI, Travis, Azure Pipelines, Buildkite, Jenkins, Drone) at this SHA")

    systems.sort(key=lambda s: s["system"])
    value = {"systems": systems}
    evidence = sorted(set(evidence_all))
    candidate = make_candidate(value, "config_file", evidence,
                                notes=[f"{len(systems)} CI system(s) detected by marker file "
                                       "presence"])
    return observed(value, evidence, [candidate])


# ---------------------------------------------------------------------------
# Command extraction from CI workflow text -- structural, regex-based on
# purpose (module docstring: stdlib only, no YAML parser). Unchanged from
# v1 except for being adapted to read via TreeAtSha instead of the disk.
# ---------------------------------------------------------------------------

# `run:` is a step KEY, and a GitHub Actions step is a YAML SEQUENCE ITEM. It is
# therefore written either as
#       - name: unit
#         run: go test ./... -short
# where `run:` follows plain indentation, or -- just as legally, and very
# commonly for unnamed steps -- with the sequence dash on the same line:
#       - run: go test ./... -short
#       - run: |
#           go test ./... -short
# The `(?:-[ \t]+)?` prefix accepts both. Without it the extractor only saw a
# `run:` that happened to be preceded by a `- name:`, which is why it read
# Nightshift's own workflow (every step named) perfectly and would have found
# NOTHING in a repository that does not name its steps -- the seat would then
# resolve no test command at all for the project it is supposed to be bound to.
#
# `indent` deliberately captures the WHOLE prefix including the dash, so its
# length is the column at which `run:` starts. Block-scalar bodies must be
# indented further than the key itself, so `_capture_indented_block` gets the
# correct base in both spellings.
_RUN_BLOCK_HEADER_RE = re.compile(r"^(?P<indent>[ \t]*(?:-[ \t]+)?)run:[ \t]*(?P<style>[|>])[+-]?[ \t]*(?:#.*)?$")
_RUN_INLINE_RE = re.compile(r"^(?P<indent>[ \t]*(?:-[ \t]+)?)run:[ \t]*(?P<rest>\S.*)$")
_COMMANDS_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)commands:[ \t]*$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]*(?P<rest>.*)$")
_LIST_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?[ \t]*(?:#.*)?$")


def _capture_indented_block(lines: List[str], start: int, base_indent: int
                             ) -> Tuple[List[Tuple[int, str]], int]:
    body: List[Tuple[int, str]] = []
    block_indent: Optional[int] = None
    j, n = start, len(lines)
    while j < n:
        raw = lines[j]
        if raw.strip() == "":
            body.append((j + 1, ""))
            j += 1
            continue
        cur_indent = len(raw) - len(raw.lstrip(" \t"))
        if block_indent is None:
            if cur_indent <= base_indent:
                break
            block_indent = cur_indent
        if cur_indent < block_indent:
            break
        body.append((j + 1, raw[block_indent:]))
        j += 1
    return body, j


def _extract_run_blocks(text: str) -> List[List[Tuple[int, str]]]:
    lines = text.splitlines()
    blocks: List[List[Tuple[int, str]]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        m_block = _RUN_BLOCK_HEADER_RE.match(line)
        if m_block:
            body, j = _capture_indented_block(lines, i + 1, len(m_block.group("indent")))
            if body:
                blocks.append(body)
            i = j
            continue

        m_inline = _RUN_INLINE_RE.match(line)
        if m_inline:
            blocks.append([(i + 1, m_inline.group("rest").strip())])
            i += 1
            continue

        m_commands = _COMMANDS_KEY_RE.match(line)
        if m_commands:
            base_indent = len(m_commands.group("indent"))
            j = i + 1
            while j < n:
                raw = lines[j]
                if raw.strip() == "":
                    j += 1
                    continue
                cur_indent = len(raw) - len(raw.lstrip(" \t"))
                if cur_indent <= base_indent:
                    break
                m_item = _LIST_ITEM_RE.match(raw)
                if not m_item:
                    j += 1
                    continue
                rest = m_item.group("rest").strip()
                if _LIST_BLOCK_SCALAR_RE.match(rest):
                    body, j = _capture_indented_block(lines, j + 1, cur_indent)
                    if body:
                        blocks.append(body)
                    continue
                if rest:
                    blocks.append([(j + 1, rest)])
                j += 1
            i = j
            continue

        i += 1
    return blocks


def _strip_inline_comment(line: str) -> str:
    in_single = in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or line[idx - 1] in " \t":
                return line[:idx].rstrip()
    return line


_ASSIGN_RE = re.compile(r"^[ \t]*(?:export[ \t]+|local[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SHELL_VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _collect_block_vars(body: List[Tuple[int, str]]) -> Dict[str, str]:
    var_map: Dict[str, str] = {}
    for _, raw in body:
        m = _ASSIGN_RE.match(_strip_inline_comment(raw))
        if not m:
            continue
        var_map[m.group(1)] = _strip_matching_quotes(m.group(2).strip())
    for _ in range(3):
        changed = False
        for name, value in list(var_map.items()):
            resolved, _unused = _substitute_vars(value, var_map)
            if resolved != value:
                var_map[name] = resolved
                changed = True
        if not changed:
            break
    return var_map


def _substitute_vars(text: str, var_map: Dict[str, str]) -> Tuple[str, List[str]]:
    result = text
    for _ in range(3):
        changed = False

        def repl(m: "re.Match[str]") -> str:
            nonlocal changed
            name = m.group(1) or m.group(2)
            if name in var_map:
                changed = True
                return var_map[name]
            return m.group(0)

        new_result = _SHELL_VAR_RE.sub(repl, result)
        if not changed:
            result = new_result
            break
        result = new_result
    unresolved = [
        (m.group(1) or m.group(2))
        for m in _SHELL_VAR_RE.finditer(result)
        if (m.group(1) or m.group(2)) not in var_map
    ]
    return result, unresolved


def _line_in_function_body(body: List[Tuple[int, str]]) -> Dict[int, bool]:
    depth = 0
    out: Dict[int, bool] = {}
    for lineno, raw in body:
        out[lineno] = depth > 0
        depth = max(0, depth + raw.count("{") - raw.count("}"))
    return out


_TRAILING_REDIRECT_RE = re.compile(r"(?:[ \t]*2>&1)+[ \t]*$")
_TRAILING_SEMI_RE = re.compile(r"[ \t]*;+[ \t]*$")


def _normalize_captured_cmd(cmd: str) -> str:
    cmd = _strip_inline_comment(cmd).rstrip()
    cmd = _TRAILING_REDIRECT_RE.sub("", cmd).rstrip()
    cmd = _TRAILING_SEMI_RE.sub("", cmd).rstrip()
    return cmd


def _quotes_balanced(s: str) -> bool:
    return s.count('"') % 2 == 0 and s.count("'") % 2 == 0


def _resolve_line(captured_cmd: str, in_function: bool,
                   line_unresolved: List[str]) -> Tuple[Optional[str], Optional[str]]:
    if in_function:
        return None, "captured_inside_shell_function_body"
    if "${{" in captured_cmd:
        return None, "unexpanded_gha_expression"
    if line_unresolved:
        return None, f"unresolved_shell_variable: ${line_unresolved[0]}"
    normalized = _normalize_captured_cmd(captured_cmd)
    if not normalized:
        return None, "empty_after_normalization"
    if normalized.endswith("\\"):
        return None, "line_continuation"
    if not _quotes_balanced(normalized):
        return None, "unbalanced_quotes"
    return normalized, None


def _grep_tracked(tree: TreeAtSha, prefix: str, needle: str,
                   max_files: int = 200) -> Optional[str]:
    """The first tracked `prefix*.py` path whose first 4KB contain `needle`,
    or None. Returns the matched PATH rather than a bare bool so a caller
    can cite it as real evidence, not just a corroborating fact with
    nothing behind it.
    """
    count = 0
    for p in tree.paths:
        if not p.startswith(prefix) or not p.endswith(".py"):
            continue
        count += 1
        if count > max_files:
            break
        text = tree.read_text(p, max_bytes=4096)
        if text and needle in text:
            return p
    return None


# ---------------------------------------------------------------------------
# Command patterns per category. Extended from v1's {test, build, lint} to
# all 8 command-shaped fields (AC-2's new fields). `lang` drives stack
# gating (`_gate_lang`); `None` is a deliberately language-agnostic entry
# point convention.
# ---------------------------------------------------------------------------

CATEGORY_VALUE_KEY: Dict[str, str] = {
    "test": "framework", "integration": "framework", "e2e": "framework",
    "build": "tool", "lint": "tool", "format": "tool", "typecheck": "tool", "codegen": "tool",
}


def _command_value(category: str, cmd: str, hint: Optional[str]) -> Dict[str, Any]:
    value: Dict[str, Any] = {"command": cmd, CATEGORY_VALUE_KEY[category]: hint}
    if category == "codegen":
        value["outputs"] = []
    return value


COMMAND_PATTERNS: Dict[str, List[Tuple[re.Pattern, str, Optional[str]]]] = {
    "test": [
        (re.compile(r"(go test\b[^\n|;]*)"), "go test", "go"),
        (re.compile(r"(pytest\b[^\n|;]*)"), "pytest", "python"),
        (re.compile(r"(python3?[ \t]+-m[ \t]+pytest[^\n|;]*)"), "pytest", "python"),
        (re.compile(r"(tests/run\.sh[^\n|;]*)"), "tests/run.sh", None),
        (re.compile(r"(python3?[ \t]+\S*run_suite\.py[^\n|;]*)"), "run_suite.py", "python"),
        (re.compile(r"(npm[ \t]+(run[ \t]+)?test\b[^\n|;]*)"), "npm test", "javascript"),
        (re.compile(r"(yarn[ \t]+(run[ \t]+)?test\b[^\n|;]*)"), "yarn test", "javascript"),
        (re.compile(r"(cargo[ \t]+test\b[^\n|;]*)"), "cargo test", "rust"),
        (re.compile(r"(mvn[ \t]+(-B[ \t]+)?test\b[^\n|;]*)"), "mvn test", "java"),
        (re.compile(r"(\.?/?gradlew?[ \t]+test\b[^\n|;]*)"), "gradle test", "java"),
        (re.compile(r"(bundle[ \t]+exec[ \t]+rspec[^\n|;]*)"), "rspec", "ruby"),
    ],
    "integration": [
        (re.compile(r"(\.?/?mvnw?[ \t]+(-B[ \t]+)?verify\b[^\n|;]*)"), "failsafe", "java"),
        (re.compile(r"(go test\b[^\n|;]*-tags[= \t][^\n|;]*integration[^\n|;]*)"),
         "go test (integration tag)", "go"),
        (re.compile(r"(pytest\b[^\n|;]*-m[ \t]+integration[^\n|;]*)"),
         "pytest (integration marker)", "python"),
        (re.compile(r"(npm[ \t]+run[ \t]+test:integration\b[^\n|;]*)"),
         "npm run test:integration", "javascript"),
    ],
    "e2e": [
        (re.compile(r"(npx[ \t]+cypress[ \t]+run[^\n|;]*)"), "cypress", "javascript"),
        (re.compile(r"(\bcypress[ \t]+run\b[^\n|;]*)"), "cypress", "javascript"),
        (re.compile(r"(npx[ \t]+playwright[ \t]+test[^\n|;]*)"), "playwright", "javascript"),
        (re.compile(r"(npm[ \t]+run[ \t]+e2e\b[^\n|;]*)"), "npm run e2e", "javascript"),
    ],
    "lint": [
        (re.compile(r"(golangci-lint[ \t]+run[^\n|;]*)"), "golangci-lint", "go"),
        (re.compile(r"(eslint\b[^\n|;]*)"), "eslint", "javascript"),
        (re.compile(r"(ruff[ \t]+check[^\n|;]*)"), "ruff", "python"),
        (re.compile(r"(flake8\b[^\n|;]*)"), "flake8", "python"),
        (re.compile(r"(pylint\b[^\n|;]*)"), "pylint", "python"),
        (re.compile(r"(go[ \t]+vet\b[^\n|;]*)"), "go vet", "go"),
        (re.compile(r"(\.?/?mvnw?[ \t]+checkstyle:check[^\n|;]*)"), "checkstyle", "java"),
        (re.compile(r"(\.?/?mvnw?[ \t]+pmd:check[^\n|;]*)"), "pmd", "java"),
    ],
    "format": [
        (re.compile(r"(black[ \t]+--check[^\n|;]*)"), "black --check", "python"),
        (re.compile(r"(\.?/?mvnw?[ \t]+spotless:(?:apply|check)[^\n|;]*)"), "spotless", "java"),
        (re.compile(r"(\.?/?gradlew?[ \t]+spotless(?:Check|Apply)?\b[^\n|;]*)"), "spotless", "java"),
        (re.compile(r"(npx[ \t]+prettier[ \t]+(?:--check|-c)\b[^\n|;]*)"), "prettier --check",
         "javascript"),
        (re.compile(r"(gofmt[ \t]+-l\b[^\n|;]*)"), "gofmt -l", "go"),
        (re.compile(r"(cargo[ \t]+fmt\b[^\n|;]*--check[^\n|;]*)"), "cargo fmt --check", "rust"),
    ],
    "typecheck": [
        (re.compile(r"(mypy\b[^\n|;]*)"), "mypy", "python"),
        (re.compile(r"(npx[ \t]+tsc\b[^\n|;]*--noEmit[^\n|;]*)"), "tsc --noEmit", "typescript"),
        (re.compile(r"(\btsc\b[^\n|;]*--noEmit[^\n|;]*)"), "tsc --noEmit", "typescript"),
        (re.compile(r"(pyright\b[^\n|;]*)"), "pyright", "python"),
    ],
    "build": [
        (re.compile(r"(go[ \t]+build\b[^\n|;]*)"), "go build", "go"),
        (re.compile(r"(npm[ \t]+run[ \t]+build\b[^\n|;]*)"), "npm run build", "javascript"),
        (re.compile(r"(yarn[ \t]+build\b[^\n|;]*)"), "yarn build", "javascript"),
        (re.compile(r"(mvn[ \t]+(-B[ \t]+)?package\b[^\n|;]*)"), "mvn package", "java"),
        (re.compile(r"(cargo[ \t]+build\b[^\n|;]*)"), "cargo build", "rust"),
        (re.compile(r"(\.?/?gradlew?[ \t]+build\b[^\n|;]*)"), "gradle build", "java"),
        (re.compile(r"((?:\./)?mvnw?[ \t]+[^\n|;]*?\b(?:install|package)\b[^\n|;]*)"),
         "mvn install", "java"),
    ],
    "codegen": [
        (re.compile(r"(protoc\b[^\n|;]*)"), "protoc", None),
        (re.compile(r"(buf[ \t]+generate\b[^\n|;]*)"), "buf generate", None),
        (re.compile(r"(sqlc[ \t]+generate\b[^\n|;]*)"), "sqlc generate", None),
        (re.compile(r"(openapi-generator(?:-cli)?[ \t]+generate\b[^\n|;]*)"),
         "openapi-generator", None),
    ],
}


def _scan_ci_run_commands(
        tree: TreeAtSha, workflow_paths: List[str]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """Structurally scan every CI workflow's shell-executing steps for
    known command signatures. Returns (accepted, rejected), both
    category -> list, matching the accepted/rejected shapes CANDIDATE and
    REJECTION define.
    """
    accepted: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}
    rejected: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}
    total_rejected = 0
    for wf in sorted(workflow_paths):
        text = tree.read_text(wf, max_bytes=262144)
        if not text:
            continue
        for body in _extract_run_blocks(text):
            var_map = _collect_block_vars(body)
            in_function = _line_in_function_body(body)
            for lineno, raw in body:
                stripped = raw.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                substituted, line_unresolved = _substitute_vars(stripped, var_map)
                for category, patterns in COMMAND_PATTERNS.items():
                    for pattern, label, lang in patterns:
                        m = pattern.search(substituted)
                        if not m:
                            continue
                        captured_cmd = (m.group(1) if m.groups() else m.group(0)).strip()
                        source = f"{wf}:{lineno}"
                        normalized, reason = _resolve_line(
                            captured_cmd, in_function[lineno], line_unresolved)
                        if reason is not None:
                            if total_rejected < 60:
                                rejected[category].append(make_rejection(
                                    _command_value(category, captured_cmd, label),
                                    "ci_observed", [wf], reason, notes=[source]))
                                total_rejected += 1
                            break
                        value = _command_value(category, normalized, label)
                        accepted[category].append(_raw_candidate(
                            value, "ci_observed", [wf], lang, notes=[source], tree=tree))
                        break
    return accepted, rejected


# ---------------------------------------------------------------------------
# package.json scripts
# ---------------------------------------------------------------------------

_KNOWN_JS_TEST_FRAMEWORKS = ("jest", "mocha", "vitest", "ava", "tape", "jasmine")


def _package_json_test_framework(tree: TreeAtSha, package_json_path: str) -> Optional[str]:
    text = tree.read_text(package_json_path, max_bytes=131072)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    deps: Dict[str, Any] = {}
    for key in ("devDependencies", "dependencies"):
        d = data.get(key)
        if isinstance(d, dict):
            deps.update(d)
    for name in _KNOWN_JS_TEST_FRAMEWORKS:
        if name in deps:
            return name
    return None


def _pkg_script_bucket(key_l: str) -> Optional[str]:
    if key_l == "test" or (key_l.startswith("test:") and "integration" not in key_l):
        return "test"
    if key_l in ("test:integration", "integration") or key_l.startswith("integration:"):
        return "integration"
    if key_l in ("e2e", "cypress", "cy:run") or key_l.startswith("e2e:") or key_l.startswith("cy:"):
        return "e2e"
    if key_l == "build" or key_l.startswith("build:"):
        return "build"
    if key_l in ("lint", "eslint") or key_l.startswith("lint:"):
        return "lint"
    if key_l in ("format", "fmt", "prettier") or key_l.startswith("format:"):
        return "format"
    if key_l in ("typecheck", "type-check", "tsc"):
        return "typecheck"
    if key_l in ("codegen", "generate"):
        return "codegen"
    return None


# The exact (never prefix-matched) key names _pkg_script_bucket above
# recognises for each category -- lifted from its own `==`/`in` branches so
# the two cannot drift apart. Used by `_select_pkg_entry` to prefer, e.g.,
# "test" over a merely prefix-matched "test:coverage", or "cy:run" over
# "cy:wallet", when one package.json declares more than one script that
# buckets into the same category.
_CANONICAL_PKG_SCRIPT_KEYS: Dict[str, frozenset] = {
    "test": frozenset({"test"}),
    "integration": frozenset({"test:integration", "integration"}),
    "e2e": frozenset({"e2e", "cypress", "cy:run"}),
    "build": frozenset({"build"}),
    "lint": frozenset({"lint", "eslint"}),
    "format": frozenset({"format", "fmt", "prettier"}),
    "typecheck": frozenset({"typecheck", "type-check", "tsc"}),
    "codegen": frozenset({"codegen", "generate"}),
}

_WATCH_FLAG_RE = re.compile(r"(?:^|\s)--watch\b")
_CYPRESS_OPEN_RE = re.compile(r"\bcypress\s+open\b")
_PLAYWRIGHT_UI_RE = re.compile(r"--ui\b")


def _pkg_script_terminates(category: str, key_l: str, cmd: str) -> bool:
    """False iff this script is a long-running or interactive process that
    does not exit on its own once started -- watch mode, or (for e2e)
    Cypress/Playwright's GUI runner. A seat told to run a *resolved*
    command verbatim must be able to run it to completion: the same
    "must be directly runnable" guarantee `cmd_text` generation above
    already enforces for shell syntax, extended here to the SCRIPT BODY
    a package.json actually declares. Detected only from evidence in the
    script's own key/body -- never a framework-name guess, and never
    disqualifying a category this script does not even belong to (a
    "--ui" flag only disqualifies e2e; it is meaningless noise elsewhere).
    """
    cmd_l = cmd.lower()
    if "watch" in key_l or _WATCH_FLAG_RE.search(cmd_l):
        return False
    if "nodemon" in cmd_l:
        return False
    if category == "e2e" and (_CYPRESS_OPEN_RE.search(cmd_l) or _PLAYWRIGHT_UI_RE.search(cmd_l)
                               or "show-report" in cmd_l):
        return False
    return True


def _package_json_scripts(tree: TreeAtSha) -> Dict[str, List[Dict[str, str]]]:
    result: Dict[str, List[Dict[str, str]]] = {cat: [] for cat in COMMAND_PATTERNS}
    for p in tree.paths:
        if p.rsplit("/", 1)[-1] != "package.json":
            continue
        text = tree.read_text(p, max_bytes=131072)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        scripts = data.get("scripts", {})
        if not isinstance(scripts, dict):
            continue
        for key, cmd in scripts.items():
            bucket = _pkg_script_bucket(str(key).lower())
            if bucket is None:
                continue
            result[bucket].append({"path": p, "script": str(key), "cmd": str(cmd)})
    return result


def _select_pkg_entry(category: str,
                       entries: List[Dict[str, str]]) -> Tuple[Optional[Dict[str, str]],
                                                                 List[Dict[str, str]]]:
    """From ONE package.json's own scripts already bucketed into `category`,
    pick the single script this source contributes as its candidate --
    never the first one declared (AC-2's own "never first-match" rule).

    Any script that cannot itself run to completion (`_pkg_script_terminates`
    false: watch mode, or e2e's interactive GUI opener) is excluded
    outright and returned as `excluded`, never offered as a candidate --
    a hanging process is not a legitimate observation of an automatable
    command, the same way a manifest script naming a missing binary is not
    (`_compute_unverified`). Among what remains, the script whose own key
    is one of the exact names `_pkg_script_bucket` treats as canonical for
    this category (e.g. "test", "cy:run") wins over a merely prefix-matched
    variant (e.g. "test:coverage", "cy:wallet"); any further tie breaks by
    original declaration order via a stable sort -- deterministic, never a
    set/dict-iteration pick. Returns (winner_or_None, excluded_entries),
    where `excluded_entries` holds only the non-terminating ones actually
    disqualified, not a merely-outranked-but-otherwise-fine sibling.
    """
    term_ids = set()
    terminating: List[Dict[str, str]] = []
    for e in entries:
        if _pkg_script_terminates(category, e["script"].lower(), e["cmd"]):
            terminating.append(e)
            term_ids.add(id(e))
    excluded = [e for e in entries if id(e) not in term_ids]
    if not terminating:
        return None, excluded
    canonical = _CANONICAL_PKG_SCRIPT_KEYS.get(category, frozenset())
    ranked = sorted(terminating, key=lambda e: 0 if e["script"].lower() in canonical else 1)
    return ranked[0], excluded


def _pkg_candidates(pkg: Dict[str, List[Dict[str, str]]],
                     tree: TreeAtSha) -> Tuple[Dict[str, List[Dict[str, Any]]],
                                                Dict[str, List[Dict[str, Any]]]]:
    out: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}
    rejected: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}
    for category, entries in pkg.items():
        if not entries:
            continue
        # Group by the declaring file first: each package.json is its own
        # source, and cross-FILE disagreement is what AC-5's conflict rule
        # exists to catch. Reducing multiple same-bucket scripts within a
        # SINGLE file down to one candidate is this function's own job --
        # it must never silently prefer whichever happened to be declared
        # first in that file's JSON key order.
        by_path: Dict[str, List[Dict[str, str]]] = {}
        for e in entries:
            by_path.setdefault(e["path"], []).append(e)
        for path in sorted(by_path):
            winner, excluded = _select_pkg_entry(category, by_path[path])
            for e in excluded:
                rejected[category].append(make_rejection(
                    _command_value(category, e["cmd"], None), "manifest_script", [e["path"]],
                    f"non_terminating: scripts.{e['script']} = {e['cmd']!r} at {e['path']} "
                    "never exits on its own (watch mode or an interactive GUI runner) and "
                    "cannot be a seat's verbatim resolved command",
                    notes=[f"package.json scripts.{e['script']} = {e['cmd']!r} at {e['path']}"]))
            if winner is None:
                continue
            entry = winner
            directory = entry["path"].rsplit("/", 1)[0] if "/" in entry["path"] else "."
            # Real, directly-executable shell text -- a seat is told to run this
            # verbatim (nightshift_seat_contract.py / ADR-0086), so it must be
            # valid syntax on its own, not a human-readable annotation. A plain
            # "npm run <script> (in <dir>)" string is NOT runnable: the
            # parenthesised aside is shell metacharacters, and `bash -c` (or
            # `sh -c`) on it fails with "syntax error near unexpected token
            # `('" before npm ever starts. `npm --prefix <dir> run <script>` is
            # npm's own documented way to invoke a script in a non-cwd package
            # directory and needs no `cd` or subshell.
            cmd_text = (f"npm run {entry['script']}" if directory == "."
                        else f"npm --prefix {directory} run {entry['script']}")
            hint: Optional[str] = None
            if category == "test":
                hint = _package_json_test_framework(tree, entry["path"]) or "npm-script"
            elif category == "e2e":
                low = entry["cmd"].lower()
                hint = "cypress" if "cypress" in low else "playwright" if "playwright" in low else "npm-script"
            value = _command_value(category, cmd_text, hint)
            # `cmd_text` is a synthesized, directly-runnable "npm run <script>"
            # (or "npm --prefix <dir> run <script>") string -- _compute_unverified
            # would tokenize that as `npm`, always a known toolchain binary, and
            # never flag anything. What must be checked is the SCRIPT BODY the
            # package.json actually declares (entry["cmd"]): a script naming a
            # missing binary or repo-relative path is exactly the "declared
            # command that does not exist" failure state the issue names, and it
            # must survive here, not just in npm's own wrapper text.
            out[category].append(_raw_candidate(
                value, "manifest_script", [entry["path"]], "javascript",
                notes=[f"package.json scripts.{entry['script']} = {entry['cmd']!r} at "
                       f"{entry['path']}"],
                unverified=_compute_unverified(entry["cmd"], tree),
                tree=tree))
    return out, rejected


# ---------------------------------------------------------------------------
# Maven pom.xml -- plugins, dependencies, parent (for Spring Boot / JUnit
# evidence, and for the format conflict testbed B actually carries).
# ---------------------------------------------------------------------------

_KNOWN_MAVEN_PLUGINS = (
    "maven-surefire-plugin", "maven-failsafe-plugin", "spotless-maven-plugin",
    "maven-pmd-plugin", "jacoco-maven-plugin", "maven-checkstyle-plugin",
)
_JUNIT_ARTIFACTS = frozenset({
    "junit", "junit-jupiter", "junit-jupiter-api", "junit-jupiter-engine",
    "junit-vintage-engine",
})
_SPRING_BOOT_MARKERS = frozenset({
    "spring-boot-starter-parent", "spring-boot-starter", "spring-boot-starter-web",
    "spring-boot-starter-test", "spring-boot-maven-plugin", "spring-boot-dependencies",
})


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _maven_pom_info(tree: TreeAtSha, tracked_root: frozenset
                     ) -> Tuple[List[str], List[str], Optional[str]]:
    """(plugin artifactIds, direct dependency artifactIds, parent artifactId)."""
    if "pom.xml" not in tracked_root:
        return [], [], None
    text = tree.read_text("pom.xml", max_bytes=2_000_000)
    if not text:
        return [], [], None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], [], None

    parent_artifact: Optional[str] = None
    for elem in root:
        if _local_tag(elem.tag) == "parent":
            for child in elem:
                if _local_tag(child.tag) == "artifactId":
                    parent_artifact = (child.text or "").strip()
            break

    plugins: List[str] = []
    for elem in root.iter():
        if _local_tag(elem.tag) != "artifactId":
            continue
        value = (elem.text or "").strip()
        if value in _KNOWN_MAVEN_PLUGINS and value not in plugins:
            plugins.append(value)

    deps: List[str] = []
    for deps_elem in root.iter():
        if _local_tag(deps_elem.tag) != "dependencies":
            continue
        for dep in deps_elem:
            if _local_tag(dep.tag) != "dependency":
                continue
            for child in dep:
                if _local_tag(child.tag) == "artifactId":
                    v = (child.text or "").strip()
                    if v and v not in deps:
                        deps.append(v)

    return plugins, deps, parent_artifact


def _maven_test_framework(deps: List[str], parent_artifact: Optional[str]) -> str:
    if any(d in _JUNIT_ARTIFACTS for d in deps):
        return "junit"
    if "testng" in deps:
        return "testng"
    return "maven-surefire"


def _maven_spring_boot_note(deps: List[str], parent_artifact: Optional[str]) -> List[str]:
    if parent_artifact in _SPRING_BOOT_MARKERS:
        return [f"pom.xml declares Spring Boot via parent {parent_artifact}"]
    hit = next((d for d in deps if d in _SPRING_BOOT_MARKERS), None)
    if hit:
        return [f"pom.xml declares Spring Boot via dependency {hit}"]
    return []


def _maven_plugin_candidates(plugins: List[str], deps: List[str], parent_artifact: Optional[str],
                              tracked_root: frozenset,
                              tree: TreeAtSha) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}
    if not plugins:
        return out
    wrapper = "./mvnw" if "mvnw" in tracked_root else "mvn"
    spring_notes = _maven_spring_boot_note(deps, parent_artifact)

    if "maven-surefire-plugin" in plugins:
        framework = _maven_test_framework(deps, parent_artifact)
        out["test"].append(_raw_candidate(
            _command_value("test", f"{wrapper} test", framework),
            "manifest_plugin", ["pom.xml"], "java",
            notes=["root pom.xml declares maven-surefire-plugin"] + spring_notes, tree=tree))
    if "maven-failsafe-plugin" in plugins:
        out["integration"].append(_raw_candidate(
            _command_value("integration", f"{wrapper} verify -Pit", "failsafe"),
            "manifest_plugin", ["pom.xml"], "java",
            notes=["root pom.xml declares maven-failsafe-plugin"], tree=tree))
    if "spotless-maven-plugin" in plugins:
        out["format"].append(_raw_candidate(
            _command_value("format", f"{wrapper} spotless:check", "spotless"),
            "manifest_plugin", ["pom.xml"], "java",
            notes=["root pom.xml declares spotless-maven-plugin"], tree=tree))
    if "maven-checkstyle-plugin" in plugins:
        out["lint"].append(_raw_candidate(
            _command_value("lint", f"{wrapper} checkstyle:check", "checkstyle"),
            "manifest_plugin", ["pom.xml"], "java",
            notes=["root pom.xml declares maven-checkstyle-plugin"], tree=tree))
    if "maven-pmd-plugin" in plugins:
        out["lint"].append(_raw_candidate(
            _command_value("lint", f"{wrapper} pmd:check", "pmd"),
            "manifest_plugin", ["pom.xml"], "java",
            notes=["root pom.xml declares maven-pmd-plugin"], tree=tree))
    return out


# ---------------------------------------------------------------------------
# Makefile targets
# ---------------------------------------------------------------------------

_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.\-]+)[ \t]*:(?!=)")

_MAKEFILE_TARGET_CATEGORY: Dict[str, str] = {
    "test": "test", "tests": "test",
    "integration-test": "integration", "integration": "integration",
    "e2e": "e2e",
    "build": "build", "all": "build", "compile": "build",
    "lint": "lint", "vet": "lint",
    "fmt": "format", "format": "format",
    "typecheck": "typecheck", "type-check": "typecheck",
    "generate": "codegen", "codegen": "codegen",
}


def _makefile_targets(tree: TreeAtSha, tracked_root: frozenset) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}
    name = next((n for n in ("Makefile", "GNUmakefile", "makefile") if n in tracked_root), None)
    if not name:
        return out
    text = tree.read_text(name, max_bytes=131072)
    if not text:
        return out
    seen: set = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw or raw[0] in ("\t", " ") or raw.lstrip().startswith("#"):
            continue
        m = _MAKE_TARGET_RE.match(raw)
        if not m:
            continue
        target = m.group(1)
        if target.startswith(".") or "%" in target:
            continue
        category = _MAKEFILE_TARGET_CATEGORY.get(target.lower())
        if not category or category in seen:
            continue
        seen.add(category)
        value = _command_value(category, f"make {target}", f"make {target}")
        out[category].append(_raw_candidate(
            value, "makefile", [name], None,
            notes=[f"Makefile target '{target}:' at {name}:{lineno}"], tree=tree))
    return out


# ---------------------------------------------------------------------------
# Per-language convention fallbacks -- the lowest-confidence tier, used
# only when nothing more specific survives. Every branch cites a real
# tracked file (AC-4: a guess with NO file behind it is forbidden and none
# exists here).
# ---------------------------------------------------------------------------

def _convention_candidates(tree: TreeAtSha, tracked_root: frozenset,
                            primary_lang: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in COMMAND_PATTERNS}

    def add(category: str, cmd: str, hint: Optional[str], lang: Optional[str],
            evidence: List[str], note: str) -> None:
        value = _command_value(category, cmd, hint)
        out[category].append(_raw_candidate(
            value, "manifest_convention", evidence, lang, notes=[note], tree=tree))

    mvnw = "./mvnw" if "mvnw" in tracked_root else "mvn"
    gradlew = "./gradlew" if "gradlew" in tracked_root else "gradle"

    if primary_lang == "rust" and "Cargo.toml" in tracked_root:
        add("test", "cargo test", "cargo test", "rust", ["Cargo.toml"],
            "Cargo.toml present at root; Rust-standard test invocation, no CI-sourced "
            "command found")
        add("build", "cargo build", None, "rust", ["Cargo.toml"],
            "Cargo.toml present at root; Rust-standard build invocation, no CI-sourced "
            "command found")
    if primary_lang == "java" and "pom.xml" in tracked_root:
        add("test", f"{mvnw} test", "maven-surefire", "java", ["pom.xml"],
            "pom.xml present at root; Maven-standard test invocation, no CI/plugin-sourced "
            "command found")
        add("build", f"{mvnw} package", "maven", "java", ["pom.xml"],
            "pom.xml present at root; Maven-standard build invocation, no CI-sourced command "
            "found")
    if primary_lang in ("java", "kotlin") and (
            "build.gradle" in tracked_root or "build.gradle.kts" in tracked_root):
        gfile = "build.gradle" if "build.gradle" in tracked_root else "build.gradle.kts"
        add("test", f"{gradlew} test", "gradle", "java", [gfile],
            "build.gradle(.kts) present at root; Gradle-standard test invocation")
        add("build", f"{gradlew} build", "gradle", "java", [gfile],
            "build.gradle(.kts) present at root; Gradle-standard build invocation")
    if primary_lang == "python" and "tests/run.sh" in set(tree.paths):
        if _grep_tracked(tree, "tests/", "import unittest"):
            add("test", "tests/run.sh", "unittest", None, ["tests/run.sh"],
                "tests/run.sh present at repo root; tests/*.py import unittest")
        elif _grep_tracked(tree, "tests/", "import pytest"):
            add("test", "tests/run.sh", "pytest", None, ["tests/run.sh"],
                "tests/run.sh present at repo root; tests/*.py import pytest")
        else:
            add("test", "tests/run.sh", "unknown-runner", None, ["tests/run.sh"],
                "tests/run.sh present at repo root; framework not identified from test file "
                "imports")
    if primary_lang == "go" and "go.mod" in tracked_root:
        add("test", "go test ./...", "go test", "go", ["go.mod"],
            "go.mod present at root; Go-standard test invocation, no CI-sourced command found")
        add("build", "go build ./...", None, "go", ["go.mod"],
            "go.mod present at root; Go-standard build invocation, no CI-sourced command found")
    if ".golangci.yml" in tracked_root or ".golangci.yaml" in tracked_root:
        cfg = ".golangci.yml" if ".golangci.yml" in tracked_root else ".golangci.yaml"
        add("lint", f"golangci-lint run ./... -c {cfg}", "golangci-lint", "go", [cfg],
            f"{cfg} present at root; no CI-sourced lint command found")

    return out


_E2E_CONFIG_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"^cypress\.config\.(js|ts|mjs|cjs)$"), "cypress", "npx cypress run"),
    (re.compile(r"^playwright\.config\.(js|ts|mjs|cjs)$"), "playwright", "npx playwright test"),
    (re.compile(r"^wdio\.conf\.(js|ts|mjs|cjs)$"), "webdriverio", "npx wdio run wdio.conf.js"),
]


def _e2e_config_candidates(tree: TreeAtSha, tracked_root: frozenset) -> List[Dict[str, Any]]:
    """A tracked `cypress.config.*` (etc) at root literally states the e2e
    framework in use, even with no CI step observed running it yet --
    source_kind "config_file", confidence "observed" per the AC-3 table.
    """
    out: List[Dict[str, Any]] = []
    for p in sorted(tracked_root):
        for pat, framework, cmd in _E2E_CONFIG_PATTERNS:
            if pat.match(p):
                value = _command_value("e2e", cmd, framework)
                out.append(_raw_candidate(
                    value, "config_file", [p], "javascript",
                    notes=[f"{p} present at root"], tree=tree))
    return out


def _gate_lang(lang: Optional[str], primary_lang: str, ambiguous_langs: set) -> bool:
    if lang is None or lang == primary_lang:
        return True
    if lang in ("java", "kotlin") and primary_lang in ("java", "kotlin"):
        return True
    return lang in ambiguous_langs


def _conflict_reason(category: str, candidates: List[Dict[str, Any]]) -> str:
    by_value: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        key = _normalize_value(c["value"])
        existing = by_value.get(key)
        if existing is None or c["rank"] > existing["rank"]:
            by_value[key] = c
    picked = sorted(by_value.values(), key=lambda c: (-c["rank"], c["source_kind"]))[:2]
    parts = []
    for c in picked:
        cmd = c["value"].get("command", json.dumps(c["value"], sort_keys=True))
        parts.append(f"{cmd!r} ({', '.join(c['evidence'])})")
    return f"conflict: {category} disagrees across sources -- " + " vs ".join(parts) + \
        " -- no winner picked"


def _field_absent_reason(category: str, ci_workflow_paths: List[str],
                          all_rejected: List[Dict[str, Any]], primary_lang: str,
                          extra_hint: Optional[str]) -> str:
    checked = []
    if ci_workflow_paths:
        checked.append(f"{len(ci_workflow_paths)} CI workflow file(s) scanned, no runnable "
                        f"{category} command survived")
    if all_rejected:
        sample = "; ".join(
            f"{r['reason']} ({_normalize_value(r['value'])[:60]!r})" for r in all_rejected[:3])
        checked.append(f"{len(all_rejected)} candidate(s) rejected: {sample}")
    if extra_hint:
        checked.append(extra_hint)
    checked.append(f"no Makefile target, manifest script/plugin, config file, or repo-"
                    f"convention evidence for {category} found (primary language: "
                    f"{primary_lang})")
    return "; ".join(checked)


def resolve_command_field(category: str, candidates: List[Dict[str, Any]],
                           rejected: List[Dict[str, Any]], ci_workflow_paths: List[str],
                           primary_lang: str, extra_hint: Optional[str] = None) -> Dict[str, Any]:
    """The AC-5 conflict rule: group surviving candidates by normalized
    command text; mark the field `conflict` iff two or more distinct
    values exist among candidates whose source_kind is not
    `manifest_convention` (this tool's own synthesized default -- almost
    every repo would "conflict" against it otherwise). Ties within one
    value break by rank, then source_kind, then the value's own text --
    a total order, never set/dict iteration.
    """
    stripped = [_strip_lang(c) for c in candidates]
    if not stripped:
        return absent(
            _field_absent_reason(category, ci_workflow_paths, rejected, primary_lang, extra_hint),
            rejected=rejected)

    non_conv = [c for c in stripped if c["source_kind"] != "manifest_convention"]
    pool_for_conflict = non_conv if non_conv else stripped
    distinct = {_normalize_value(c["value"]) for c in pool_for_conflict}
    ranked = sorted(stripped, key=lambda c: (-c["rank"], c["source_kind"], _normalize_value(c["value"])))

    if len(distinct) >= 2:
        reason = _conflict_reason(category, pool_for_conflict)
        return conflict(ranked, reason, rejected=rejected)

    winner = ranked[0]
    if winner["confidence"] == "observed":
        return observed(winner["value"], winner["evidence"], ranked, rejected=rejected,
                         unverified=winner["unverified"])
    return declared(winner["value"], winner["evidence"], ranked, rejected=rejected,
                     unverified=winner["unverified"])


def build_command_fields(tree: TreeAtSha, languages_value: Optional[Dict[str, Any]],
                          ci_value: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    primary_lang = (languages_value or {}).get("primary") or "unknown"
    ambiguous_langs: set = set()
    if languages_value and languages_value.get("ambiguous"):
        ambiguous_langs = {e["language"] for e in languages_value["all"][:2]}

    tracked_root = frozenset(p for p in tree.paths if "/" not in p)

    ci_workflow_paths = sorted({
        wp for s in (ci_value or {}).get("systems", []) for wp in s["workflows"]
        if wp.endswith((".yml", ".yaml"))
    })
    ci_pool, ci_rejected = _scan_ci_run_commands(tree, ci_workflow_paths)
    pkg = _package_json_scripts(tree)
    pkg_candidates, pkg_rejected = _pkg_candidates(pkg, tree)
    plugins, deps, parent_artifact = _maven_pom_info(tree, tracked_root)
    plugin_candidates = _maven_plugin_candidates(plugins, deps, parent_artifact, tracked_root, tree)
    make_candidates = _makefile_targets(tree, tracked_root)
    conv_candidates = _convention_candidates(tree, tracked_root, primary_lang)
    e2e_config = _e2e_config_candidates(tree, tracked_root)

    fields: Dict[str, Dict[str, Any]] = {}
    for category in COMMAND_PATTERNS:
        pool = list(ci_pool[category])
        pool += make_candidates[category]
        pool += pkg_candidates[category]
        pool += plugin_candidates[category]
        pool += conv_candidates[category]
        if category == "e2e":
            pool += e2e_config

        # e2e tooling is overwhelmingly JS-based (Cypress/Playwright)
        # regardless of the repo's PRIMARY source language -- a Go or Java
        # backend with a real Cypress suite under itf/ or e2e/ is the
        # common case, not an anomaly, so e2e is exempt from the stack
        # gate that build/test/lint/integration/format/typecheck/codegen
        # (all coupled to the primary toolchain) still enforce.
        if category == "e2e":
            gated = list(pool)
        else:
            gated = [c for c in pool if _gate_lang(c["lang"], primary_lang, ambiguous_langs)]
        gated_ids = {id(c) for c in gated}
        stack_rejected = [
            make_rejection(c["value"], c["source_kind"], c["evidence"],
                            f"stack_mismatch: implies {c['lang']}, repo is {primary_lang}",
                            notes=c["notes"])
            for c in pool if id(c) not in gated_ids
        ]

        extra_hint = None
        if category == "lint" and "pmd-ruleset.xml" in tracked_root and \
                "maven-pmd-plugin" not in plugins:
            extra_hint = ("tracked pmd-ruleset.xml exists at root but no maven-pmd-plugin "
                           "declared in pom.xml")

        all_rejected = list(ci_rejected[category]) + stack_rejected + pkg_rejected[category]
        fields[category] = resolve_command_field(
            category, gated, all_rejected, ci_workflow_paths, primary_lang, extra_hint=extra_hint)

    # A winning test candidate may just be an entry-point script (this
    # repo's own tests/run.sh, or a bare run_suite.py) rather than a named
    # framework -- refine by sniffing what the test files under that entry
    # point actually import, so "framework" answers "how do I write a new
    # test here" rather than echoing the launcher. Mutates the SAME dict
    # object that is both the envelope's `value` and the winning
    # candidate's `value` (resolve_command_field never copies it), so the
    # two stay identical rather than drifting apart.
    if primary_lang == "python" and fields["test"]["status"] in ("observed", "declared"):
        test_value = fields["test"]["value"]
        if test_value.get("framework") in ("tests/run.sh", "run_suite.py"):
            hit = _grep_tracked(tree, "tests/", "import unittest")
            refined = "unittest"
            if not hit:
                hit = _grep_tracked(tree, "tests/", "import pytest")
                refined = "pytest"
            if hit:
                test_value["framework"] = refined
                fields["test"]["evidence"] = sorted(set(fields["test"]["evidence"]) | {hit})

    return fields


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

_CODEOWNERS_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def build_ownership_field(tree: TreeAtSha) -> Dict[str, Any]:
    tracked_set = set(tree.paths)
    for loc in _CODEOWNERS_LOCATIONS:
        if loc not in tracked_set:
            continue
        text = tree.read_text(loc, max_bytes=131072)
        if not text:
            continue
        rules = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            rules.append({"path": parts[0], "owners": parts[1:]})
        if rules:
            value = {"source": loc, "rules": rules}
            candidate = make_candidate(value, "config_file", [loc],
                                        notes=[f"{len(rules)} rule(s) parsed"])
            return observed(value, [loc], [candidate])
    return absent("no CODEOWNERS file found at CODEOWNERS, .github/CODEOWNERS, or "
                  "docs/CODEOWNERS at this SHA")


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_MIGRATION_NAME_RE = re.compile(r"migrat", re.IGNORECASE)
_SQL_UPDOWN_RE = re.compile(r"\.(up|down)\.sql$")
_DJANGO_MIGRATION_RE = re.compile(r"^\d{4}_.*\.py$")


def build_migrations_field(tree: TreeAtSha) -> Dict[str, Any]:
    dirs: Dict[str, Dict[str, Any]] = {}

    def note(d: str, p: str) -> None:
        dirs.setdefault(d, {"path": d, "evidence": set()})
        dirs[d]["evidence"].add(p)

    for p in tree.paths:
        parts = p.split("/")
        base = parts[-1]
        for i, seg in enumerate(parts[:-1]):
            if _MIGRATION_NAME_RE.search(seg):
                note("/".join(parts[:i + 1]), p)
        parent = "/".join(parts[:-1]) if len(parts) > 1 else "."
        if _SQL_UPDOWN_RE.search(base):
            note(parent, p)
        elif parts[-2:-1] == ["migrations"] and _DJANGO_MIGRATION_RE.match(base):
            note(parent, p)

    if not dirs:
        return absent(
            "no directory name matching /migrat/i, no *.up.sql/*.down.sql pair, and no "
            "NNNN_*.py Django-style migration files found at this SHA")

    directories = sorted(dirs.keys())
    evidence = sorted({p for d in dirs.values() for p in d["evidence"]})

    any_django = any(
        _DJANGO_MIGRATION_RE.match(p.rsplit("/", 1)[-1]) for p in evidence)
    tool = "sql"
    tool_note = "generic SQL migration directory (name/content pattern only)"
    if any_django:
        tool = "django"
        tool_note = "NNNN_*.py Django-style migration files"
    else:
        pom_text = tree.read_text("pom.xml", max_bytes=2_000_000) or ""
        if "flyway" in pom_text.lower():
            tool = "flyway"
            tool_note = "pom.xml references flyway"
        elif any("alembic" in d.lower() for d in directories):
            tool = "alembic"
            tool_note = "directory name contains 'alembic'"

    value = {"tool": tool, "directories": directories}
    candidate = make_candidate(value, "tree_layout", evidence, notes=[tool_note])
    return observed(value, evidence, [candidate])


# ---------------------------------------------------------------------------
# Excluded (generated / vendored) directories
# ---------------------------------------------------------------------------

GENERATED_NAME_PATTERNS = [
    re.compile(r"\.pb\.go$"),
    re.compile(r"_pb2(_grpc)?\.py$"),
    re.compile(r"\.pb\.[a-z]+$"),
    re.compile(r"\.min\.(js|css)$"),
    re.compile(r"\.g\.dart$"),
    re.compile(r"\.generated\."),
]

GENERATED_CONTENT_MARKERS = (
    "code generated", "do not edit", "@generated", "auto-generated",
    "automatically generated", "autogenerated file",
)

# A tuple, not a set -- iterated in fixed literal order (a set iterated
# directly was a v1 determinism hazard even though it was later sorted;
# this removes the hazard at the source).
WELL_KNOWN_VENDOR_DIRNAMES: Tuple[str, ...] = (
    "node_modules", "vendor", "venv", ".venv", "__pycache__", "dist",
    "build", "target", ".tox", ".mypy_cache", ".pytest_cache", ".terraform",
    "bower_components", ".gradle", ".next", ".nuxt",
)

_GENERATED_SCAN_FILE_CAP = 20000
_GENERATED_DIR_RATIO_THRESHOLD = 0.5


def _parse_gitignore(text: str) -> Tuple[List[str], List[str]]:
    ignores, negations = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            negations.append(line[1:])
        else:
            ignores.append(line)
    return ignores, negations


def build_excluded_dirs_field(tree: TreeAtSha) -> Dict[str, Any]:
    tracked = tree.paths
    excluded: List[Dict[str, Any]] = []
    seen_keys: set = set()
    all_evidence: List[str] = []

    def add(path: str, reason: str, evidence_paths: List[str], note: str) -> None:
        key = (path, reason)
        if key in seen_keys:
            return
        seen_keys.add(key)
        ev = sorted(set(evidence_paths))
        excluded.append({"path": path, "reason": reason, "evidence": ev, "note": note})
        all_evidence.extend(ev)

    gitignore_text = tree.read_text(".gitignore") or ""
    if gitignore_text:
        ignores, _negations = _parse_gitignore(gitignore_text)
        for pat in sorted(set(ignores)):
            if pat.endswith("/*"):
                d = pat[:-2].lstrip("/")
            else:
                d = pat.rstrip("/").lstrip("/")
            if not d or any(ch in d for ch in "*?[]!"):
                continue
            prefix = d + "/"
            under = sorted(p for p in tracked if p == d or p.startswith(prefix))
            if not under:
                add(d, "generated_or_local_gitignored", [".gitignore"],
                    f".gitignore lists '{pat}'; 0 tracked files under it (confirmed excluded)")
            else:
                add(d, "partially_tracked_gitignored", [".gitignore", under[0]],
                    f".gitignore lists '{pat}' but {len(under)} file(s) are tracked under it "
                    f"via negation allowlist entries, e.g. {under[0]}")

    top_level_dirs = {p.split("/", 1)[0] for p in tracked if "/" in p}
    for name in WELL_KNOWN_VENDOR_DIRNAMES:
        if name in top_level_dirs:
            sample = sorted(p for p in tracked if p.startswith(name + "/"))[:1]
            add(name, "well_known_vendor_dirname", sample,
                f"top-level tracked directory named '{name}' matches common vendor/build-"
                "output convention")

    dir_totals: Dict[str, int] = {}
    for p in tracked:
        parent = p.rsplit("/", 1)[0] if "/" in p else "."
        dir_totals[parent] = dir_totals.get(parent, 0) + 1

    generated_by_dir: Dict[str, List[str]] = {}
    for p in tracked[:_GENERATED_SCAN_FILE_CAP]:
        base = p.rsplit("/", 1)[-1]
        name_match = any(pat.search(base) for pat in GENERATED_NAME_PATTERNS)
        text = tree.read_text(p, max_bytes=512)
        content_match = bool(text and any(marker in text.lower()
                                           for marker in GENERATED_CONTENT_MARKERS))
        if not (name_match or content_match):
            continue
        parent = p.rsplit("/", 1)[0] if "/" in p else "."
        generated_by_dir.setdefault(parent, []).append(p)

    for parent in sorted(generated_by_dir):
        files_sorted = sorted(generated_by_dir[parent])
        total_here = dir_totals.get(parent, len(files_sorted))
        if len(files_sorted) / total_here < _GENERATED_DIR_RATIO_THRESHOLD:
            continue
        add(parent, "generated_code", files_sorted,
            f"{len(files_sorted)} of {total_here} tracked file(s) here are generated, "
            f"e.g. {files_sorted[0]}")

    sonar_text = tree.read_text("sonar-project.properties")
    if sonar_text:
        m = re.search(r"sonar\.exclusions\s*=\s*(.+)", sonar_text)
        if m:
            add("(sonar.exclusions)", "declared_in_sonar_config", ["sonar-project.properties"],
                f"sonar-project.properties sonar.exclusions = {m.group(1).strip()}")

    if not excluded:
        return absent(
            "no gitignored-and-untracked directory, well-known vendor dirname, generated-"
            "file marker, or sonar.exclusions entry found at this SHA")

    excluded.sort(key=lambda d: (d["path"], d["reason"]))
    value = {"dirs": excluded}
    evidence = sorted(set(all_evidence))
    candidate = make_candidate(value, "tree_layout", evidence,
                                notes=[f"{len(excluded)} excluded dir(s) detected"])
    return observed(value, evidence, [candidate])


# ---------------------------------------------------------------------------
# validate_contract() -- the AC-3 state table, enforced, run-failing.
# ---------------------------------------------------------------------------

_ENVELOPE_KEYS = frozenset({
    "status", "value", "confidence", "evidence", "reason", "unverified",
    "candidates", "rejected",
})
_CANDIDATE_KEYS = frozenset({
    "value", "confidence", "evidence", "source_kind", "rank", "unverified", "notes",
})
_REJECTION_KEYS = frozenset({"value", "source_kind", "evidence", "reason", "notes"})
_STATUS_VALUES = frozenset({"observed", "declared", "conflict", "absent"})
_CONFIDENCE_VALUES = frozenset({"observed", "declared"})


def _validate_evidence_path(where: str, p: Any, valid_paths: frozenset) -> None:
    if not isinstance(p, str) or not p:
        raise ContractInvalid(f"{where}: evidence path is not a non-empty string: {p!r}")
    if p.startswith("/") or "\\" in p or any(seg == ".." for seg in p.split("/")):
        raise ContractInvalid(f"{where}: evidence path is not repo-relative posix: {p!r}")
    if p not in valid_paths:
        raise ContractInvalid(f"{where}: evidence path not present in tree at baseline_sha: {p!r}")


def validate_contract(contract: Dict[str, Any], tree: TreeAtSha) -> None:
    top_expected = {"schema_version", "baseline_sha", "contract_digest", "local", "tree",
                     *CONTRACT_FIELDS, "warnings"}
    actual_keys = set(contract)
    if actual_keys != top_expected:
        raise ContractInvalid(
            f"top-level keys mismatch: extra={sorted(actual_keys - top_expected)} "
            f"missing={sorted(top_expected - actual_keys)}")

    if not SHA_RE.match(str(contract.get("baseline_sha", ""))):
        raise ContractInvalid(f"baseline_sha is not a well-formed SHA: {contract.get('baseline_sha')!r}")

    valid_paths = frozenset(tree.paths)

    for field in CONTRACT_FIELDS:
        env = contract[field]
        if set(env) != _ENVELOPE_KEYS:
            raise ContractInvalid(f"{field}: envelope keys mismatch: {sorted(env)}")
        status = env["status"]
        if status not in _STATUS_VALUES:
            raise ContractInvalid(f"{field}: invalid status {status!r}")

        for i, c in enumerate(env["candidates"]):
            where = f"{field}.candidates[{i}]"
            if set(c) != _CANDIDATE_KEYS:
                raise ContractInvalid(f"{where}: keys mismatch: {sorted(c)}")
            if c["confidence"] not in _CONFIDENCE_VALUES:
                raise ContractInvalid(f"{where}: invalid confidence {c['confidence']!r}")
            if c["source_kind"] not in _SOURCE_KIND_CONFIDENCE:
                raise ContractInvalid(f"{where}: unknown source_kind {c['source_kind']!r}")
            if not c["evidence"]:
                raise ContractInvalid(f"{where}: empty evidence")
            if not isinstance(c["rank"], int) or isinstance(c["rank"], bool):
                raise ContractInvalid(f"{where}: rank must be an int, got {c['rank']!r}")
            for p in c["evidence"]:
                _validate_evidence_path(where, p, valid_paths)

        for i, r in enumerate(env["rejected"]):
            where = f"{field}.rejected[{i}]"
            if set(r) != _REJECTION_KEYS:
                raise ContractInvalid(f"{where}: keys mismatch: {sorted(r)}")
            for p in r["evidence"]:
                _validate_evidence_path(where, p, valid_paths)

        for p in env["evidence"]:
            _validate_evidence_path(field, p, valid_paths)
        if env["evidence"] != sorted(set(env["evidence"])):
            raise ContractInvalid(f"{field}: evidence not sorted-unique: {env['evidence']}")

        if status in ("observed", "declared"):
            if not isinstance(env["value"], dict):
                raise ContractInvalid(f"{field}: status {status} requires an object value, "
                                       f"got {type(env['value'])}")
            if env["confidence"] != status:
                raise ContractInvalid(f"{field}: status {status} requires confidence == "
                                       f"{status!r}, got {env['confidence']!r}")
            if not env["evidence"]:
                raise ContractInvalid(f"{field}: status {status} requires non-empty evidence")
            if env["reason"] is not None:
                raise ContractInvalid(f"{field}: status {status} requires reason is None")
            if len(env["candidates"]) < 1:
                raise ContractInvalid(f"{field}: status {status} requires >=1 candidate")
        elif status == "conflict":
            if env["value"] is not None:
                raise ContractInvalid(f"{field}: status conflict requires value is None")
            if not env["evidence"]:
                raise ContractInvalid(f"{field}: status conflict requires non-empty evidence")
            if not env["reason"]:
                raise ContractInvalid(f"{field}: status conflict requires non-empty reason")
            if len(env["candidates"]) < 2:
                raise ContractInvalid(f"{field}: status conflict requires >=2 candidates")
            distinct = {_normalize_value(c["value"]) for c in env["candidates"]}
            if len(distinct) < 2:
                raise ContractInvalid(f"{field}: status conflict requires >=2 distinct "
                                       "candidate values")
            confs = {c["confidence"] for c in env["candidates"]}
            expected_conf = "declared" if "declared" in confs else "observed"
            if env["confidence"] != expected_conf:
                raise ContractInvalid(f"{field}: conflict confidence should be "
                                       f"{expected_conf!r}, got {env['confidence']!r}")
        else:  # absent
            if env["value"] is not None:
                raise ContractInvalid(f"{field}: status absent requires value is None")
            if env["confidence"] is not None:
                raise ContractInvalid(f"{field}: status absent requires confidence is None")
            if env["evidence"] != []:
                raise ContractInvalid(f"{field}: status absent requires empty evidence")
            if not env["reason"]:
                raise ContractInvalid(f"{field}: status absent requires non-empty reason")
            if any(c["source_kind"] != "manifest_convention" for c in env["candidates"]):
                raise ContractInvalid(f"{field}: status absent candidates must all be "
                                       "manifest_convention")

    recomputed = "sha256:" + hashlib.sha256(canonical_bytes(contract)).hexdigest()
    if contract["contract_digest"] != recomputed:
        raise ContractInvalid("contract_digest does not match recomputed digest")

    try:
        round_tripped = json.loads(json.dumps(contract))
    except (TypeError, ValueError) as exc:
        raise ContractInvalid(f"contract is not plain JSON-serialisable: {exc}") from exc
    if round_tripped != contract:
        raise ContractInvalid("contract does not round-trip through json.dumps/json.loads "
                               "unchanged")


def canonical_bytes(contract: Dict[str, Any]) -> bytes:
    """The exact canonical form contract_digest is computed over (AC-6).
    ensure_ascii=True + allow_nan=False are both load-bearing: a
    surrogate-escaped path from an invalid-UTF8 filename survives
    ensure_ascii=True encoded as ascii, and NaN/Infinity are non-canonical
    JSON that allow_nan=False refuses to emit in the first place.
    """
    payload = {k: v for k, v in contract.items() if k not in DIGEST_EXCLUDED_TOP_LEVEL_KEYS}
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"),
    ).encode("ascii")


# ---------------------------------------------------------------------------
# contradicts() -- not enforcement (#443 is facts-only). The assertion
# Dogfood B writes now over the contract's own evidence, so a later
# conformance-gate child can call this directly instead of re-deriving the
# comparison.
# ---------------------------------------------------------------------------

def contradicts(contract: Dict[str, Any], claim: Dict[str, Dict[str, Any]]) -> List[str]:
    """`claim` is {field: {key: proposed_value, ...}, ...}, using the same
    nested keys as that field's `value` shape (e.g. {"languages":
    {"primary": "go"}, "e2e": {"framework": "playwright"}}).

    Returns one human-readable string per key the contract actually holds
    an observed/declared answer for that disagrees with the claim. A field
    the contract could not observe (absent) or could not resolve
    (conflict) contradicts nothing -- there is no answer to disagree with.
    """
    reasons: List[str] = []
    for field, claimed_fields in claim.items():
        env = contract.get(field)
        if not env or env.get("status") not in ("observed", "declared"):
            continue
        actual_value = env.get("value") or {}
        if not isinstance(claimed_fields, dict):
            continue
        for key, claimed in claimed_fields.items():
            actual = actual_value.get(key)
            if actual is None:
                continue
            mismatch = (actual.casefold() != claimed.casefold()
                        if isinstance(actual, str) and isinstance(claimed, str)
                        else actual != claimed)
            if mismatch:
                reasons.append(
                    f"{field}.{key}: contract observes {actual!r} (evidence: "
                    f"{env['evidence']}), claim proposes {claimed!r}")
    return reasons


# ---------------------------------------------------------------------------
# Contract assembly
# ---------------------------------------------------------------------------

def build_contract(repo_path: str, *, sha: str) -> Dict[str, Any]:
    """Compile the project contract for `repo_path` at the exact commit
    `sha`. Keyword-only, no default -- a caller that wants HEAD resolves it
    themselves (`git rev-parse HEAD`) and passes the result; this function
    never does that resolution itself (AC-1).

    Raises NotADirectoryError (usage error), ShaNotPresent (the SHA
    refusal), or ContractInvalid (a shape violation validate_contract()
    caught) -- see the module docstring's exit-code table.
    """
    tree = open_tree(repo_path, sha)
    try:
        warnings: List[str] = []

        languages_env = build_languages_field(tree)
        languages_value = languages_env["value"]
        if languages_env["status"] == "absent":
            warnings.append(f"languages: absent -- {languages_env['reason']}")
        elif languages_value.get("ambiguous"):
            top_two = ", ".join(
                f"{e['language']} ({e['share_ppm'] / 1_000_000:.2f})"
                for e in languages_value["all"][:2])
            warnings.append(f"AMBIGUOUS STACK: top candidates too close to call confidently: "
                             f"{top_two}")

        ci_env = build_ci_field(tree)
        if ci_env["status"] == "absent":
            warnings.append(f"ci: absent -- {ci_env['reason']}")

        cmd_fields = build_command_fields(tree, languages_value, ci_env["value"])
        for category in COMMAND_PATTERNS:
            env = cmd_fields[category]
            if env["status"] == "absent":
                warnings.append(f"{category}: absent -- {env['reason']}")
            elif env["status"] == "conflict":
                warnings.append(f"{category}: conflict -- {env['reason']}")
            elif env["unverified"]:
                warnings.append(f"{category}: {env['status']} but unverified -- "
                                 f"{json.dumps(env['value'], sort_keys=True)}")

        runtime_env = build_runtime_version_field(
            tree, (languages_value or {}).get("primary") or "unknown")
        dep_env = build_dependency_manifests_field(tree)
        lock_env = build_lockfiles_field(tree)
        ownership_env = build_ownership_field(tree)
        migrations_env = build_migrations_field(tree)
        excluded_env = build_excluded_dirs_field(tree)
        for name, env in (
            ("runtime_version", runtime_env), ("dependency_manifests", dep_env),
            ("lockfiles", lock_env), ("ownership", ownership_env),
            ("migrations", migrations_env), ("excluded_dirs", excluded_env),
        ):
            if env["status"] == "absent":
                warnings.append(f"{name}: absent -- {env['reason']}")

        fields: Dict[str, Any] = {
            "languages": languages_env,
            "runtime_version": runtime_env,
            "dependency_manifests": dep_env,
            "lockfiles": lock_env,
            "build": cmd_fields["build"],
            "test": cmd_fields["test"],
            "integration": cmd_fields["integration"],
            "e2e": cmd_fields["e2e"],
            "lint": cmd_fields["lint"],
            "format": cmd_fields["format"],
            "typecheck": cmd_fields["typecheck"],
            "codegen": cmd_fields["codegen"],
            "migrations": migrations_env,
            "ci": ci_env,
            "ownership": ownership_env,
            "excluded_dirs": excluded_env,
        }

        is_shallow_proc = _run_git(tree.repo, ["rev-parse", "--is-shallow-repository"])
        is_shallow = is_shallow_proc.stdout.decode("utf-8", errors="replace").strip() == "true"

        contract: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "baseline_sha": sha,
            # Placeholder: canonical_bytes() drops this key entirely before
            # hashing, so its value here never influences the real digest
            # computed below.
            "contract_digest": "sha256:" + ("0" * 64),
            "local": {"repo_path": str(tree.repo), "git_version": git_version()},
            "tree": {
                "files": len(tree.paths),
                "bytes": sum(tree.sizes.values()),
                "is_shallow": is_shallow,
            },
            **fields,
            "warnings": warnings,
        }

        contract["contract_digest"] = "sha256:" + hashlib.sha256(
            canonical_bytes(contract)).hexdigest()

        validate_contract(contract, tree)
        return contract
    finally:
        tree.close()


# ---------------------------------------------------------------------------
# Public library entry point -- kept as a thin, explicit alias. Nothing in
# this codebase currently calls it (grep-verified against build_contract(/
# detect_project_contract( across bin/ and tests/); build_contract() is the
# real interface tests/test_project_contract.py and every caller use.
# ---------------------------------------------------------------------------

def detect_project_contract(root: Any, *, sha: str) -> Dict[str, Any]:
    return build_contract(str(root), sha=sha)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nightshift_project_contract.py",
        description="Deterministic project-stack discovery (#443). Emits a JSON project "
                     "contract for the given repo path AT AN EXACT SHA to stdout.")
    parser.add_argument("repo_path", help="path to the repository root to inspect")
    parser.add_argument("--sha", required=True,
                         help="exact 40 or 64 character commit SHA to inspect. No default, "
                              "no --ref, no HEAD resolution -- a caller that wants HEAD runs "
                              "'git rev-parse HEAD' itself and passes the result here.")
    parser.add_argument("--compact", action="store_true",
                         help="emit compact JSON (default: pretty, indent=2)")
    args = parser.parse_args(argv)

    try:
        contract = build_contract(args.repo_path, sha=args.sha)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ShaNotPresent as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except ContractInvalid as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4

    for w in contract["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)

    if args.compact:
        print(json.dumps(contract, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    else:
        print(json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
