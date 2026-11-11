"""Invocation guardrail -- issue #729, epic #716 change 4.

#716's rule: *whoever adds a test or a check must show what invokes it*. The
run that produced that rule contained three artefacts that were written,
looked correct, and were executed by nothing -- and the cheapest question
available, "does this actually run?", was never asked of them.

This module asks it mechanically. It inventories every test-shaped artefact
under ``tests/`` and classifies each one as:

- **discovered** -- reached by ``run_suite.py``'s ``unittest`` discovery, which
  is what the gated suite actually executes;
- **invoked** -- named by a workflow, deploy script or bin/ script, so
  something outside the suite runs it on purpose;
- **neither** -- which requires a manifest entry, and a ``debt`` entry
  requires an owning issue.

The manifest contract is #439's, deliberately: unlisted artefact fails, stale
entry fails (so a fixed item must leave the file rather than accumulate), debt
without an issue fails. That guardrail has already proved it works in this
repo -- it is what caught PR #718 -- so this borrows the mechanism rather than
inventing a second one with different failure modes.

**What this is not.** It does not check that a test is any good, or that an
invoked script can fail. `tests/linux/*.sh` (issue #728) is the standing
reminder that being scheduled is necessary and not sufficient. It also does
not police non-test checks: a policy factory nobody registers is the same
disease, but "what counts as an invoker" is a different question there and is
left to its own change.

The inventory is git-tracked blobs only, for the reason #439 records: a
filesystem walk picks up ``__pycache__`` and other untracked cruft, and two
scans of the same tree then disagree with each other. One consequence worth
knowing: an unrun test that has been written but not yet committed is invisible
to a local run of this audit, and first appears in CI. That is the same
blind spot the portability guardrail has, and it is why #718's own breach was
found by CI rather than locally.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
MANIFEST_PATH = TESTS_DIR / "invocation_manifest.json"

# Where a DECLARED invoker may live. A closed set of places that actually
# execute things. Deliberately excludes `.github/ISSUE_TEMPLATE/` and
# `.github/pull_request_template.md`: a checklist item asking a human to run
# something is the PR-template design this ADR rejected, and an earlier draft
# of this file accepted it as an invoker by accident.
INVOKER_PREFIXES = (".github/workflows/", "deploy/", "bin/")


class ManifestError(Exception):
    """Manifest missing, unparseable, or describing a tree that is not there.
    Every case fails closed."""


@dataclass(frozen=True)
class Entry:
    path: str
    category: str | None
    reason: str
    issue: int | None
    invoked_by: str | None


@dataclass(frozen=True)
class AuditResult:
    problems: list
    total: int
    debt: int
    structural: int
    invoked: int = 0


def git_list_files(repo_root: Path, ref: str = "HEAD") -> list:
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "--", "tests"],
        cwd=repo_root, capture_output=True, text=True, check=True)
    return [line for line in proc.stdout.splitlines() if line]


def is_test_shaped(rel_path: str) -> bool:
    """What counts as claiming to be a test.

    Two shapes, both of which a reader would call a test on sight: a Python
    module named ``test_*.py`` anywhere under ``tests/``, and any shell script
    under ``tests/`` (by suffix -- the executable bit is not consulted, since a
    script run as ``bash x.sh`` needs no such bit and one that has it may still
    be a helper). Fixtures are excluded -- ``tests/fixtures/``
    holds sample projects that exist to be SCANNED, not run, and a Cypress spec
    in there executing nowhere is the point of the fixture rather than a defect.
    """
    if rel_path.startswith("tests/fixtures/"):
        return False
    name = rel_path.rsplit("/", 1)[-1]
    if name.startswith("test_") and name.endswith(".py"):
        return True
    return rel_path.startswith("tests/") and name.endswith(".sh")


def discovered_modules(tests_dir: Path = TESTS_DIR) -> set:
    """Exactly what the gated suite runs -- asked of `unittest`, not guessed.

    `run_suite.py` discovers with pattern ``test_*.py`` from ``tests/``. Re-
    deriving that rule by hand here would let the guardrail and the suite drift,
    and the guardrail would then be certifying a suite that is not the one that
    runs.
    """
    import unittest

    def walk(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from walk(item)
            else:
                yield item

    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_dir), pattern="test_*.py")
    names = set()
    for test in walk(suite):
        module = type(test).__module__
        # A module that fails to import is reported by discovery as a
        # synthetic `_FailedTest` living in `unittest.loader`, never under the
        # module's own name -- so dropping those names is what keeps an
        # unimportable module out of `discovered`, and it is then correctly
        # reported as undiscovered. That is the exact shape of "247 tests
        # SKIPped because the module would not import, and the run reported
        # OK", and it has its own test
        # (`test_a_module_that_cannot_be_imported_is_not_credited_as_running`)
        # because a comment claiming this is not evidence of it.
        if module and not module.startswith("unittest"):
            names.add(module)
    return names


def default_read_file(rel_path: str, repo_root: Path) -> str:
    return (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")


def default_read_file(rel_path: str, repo_root: Path) -> str:
    return (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")


def classify(repo_root: Path = REPO_ROOT, ref: str = "HEAD",
             list_files: Callable = git_list_files,
             tests_dir: Path | None = None) -> dict:
    """Every test-shaped artefact, and whether the gated suite reaches it.

    Only two answers, deliberately: `discovered`, or not. Whether something
    else runs it is NOT inferred -- an earlier version of this file inferred
    it by looking for the artefact's path in `.github/`, `deploy/` and `bin/`,
    and a review demonstrated that a comment, a `bash -n` lint, a
    pull-request-template checkbox and a `.bak` suffix all satisfied it. An
    inference that a PR checklist can satisfy is the exact "prose is enough"
    failure #716 exists to close, so the inference is gone. Anything discovery
    does not reach must be DECLARED, and the declaration is verified.
    """
    artefacts = [p for p in list_files(repo_root, ref) if is_test_shaped(p)]
    discovered = discovered_modules(tests_dir or (repo_root / "tests"))
    out = {}
    for rel in sorted(artefacts):
        out[rel] = "discovered" if module_name(rel) in discovered else "undiscovered"
    return out


def module_name(rel_path: str) -> str:
    """The dotted name `unittest` discovery would import this file under.

    Dotted, not a bare basename: discovery reports `sub.test_inner` for a test
    inside a package, and comparing that against `test_inner` reported a test
    that genuinely runs as one that does not.
    """
    if not rel_path.endswith(".py"):
        return rel_path
    inner = rel_path[len("tests/"):] if rel_path.startswith("tests/") else rel_path
    return inner[:-3].replace("/", ".")


def verify_declaration(entry, repo_root: Path, ref: str = "HEAD",
                       list_files: Callable = None,
                       read_file: Callable = default_read_file) -> list:
    """Check that a declared invoker exists, may execute, and names this thing.

    The declaration is the author's claim; this is the part that makes it more
    than a note. A named invoker must be a real tracked file, must live under
    a root that actually runs things, and must mention either the artefact or
    the directory holding it -- the directory case because the natural way to
    run a set of scripts is a glob, and a gate that only accepted literal paths
    would push people towards the worse fix.
    """
    problems = []
    invoker = entry.invoked_by
    if not invoker:
        return [f"invoked entry must name what runs it: {entry.path!r}"]
    if not any(invoker.startswith(prefix) for prefix in INVOKER_PREFIXES):
        problems.append(
            f"{entry.path!r} names {invoker!r} as its invoker, which is not "
            f"under {INVOKER_PREFIXES} -- a file that does not execute things "
            "cannot be what runs a test")
        return problems
    tracked = set(list_files(repo_root, ref)) if list_files else None
    path = repo_root / invoker
    if not path.exists():
        problems.append(f"{entry.path!r} names a missing invoker: {invoker!r}")
        return problems
    try:
        text = read_file(invoker, repo_root)
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"cannot read declared invoker {invoker!r}: {exc}")
        return problems
    parent = entry.path.rsplit("/", 1)[0] + "/"
    if entry.path not in text and parent not in text:
        problems.append(
            f"{invoker!r} is declared to run {entry.path!r} but does not "
            "mention it or its directory")
    return problems


def parse_entries(data: dict) -> list:
    entries = []
    for raw in data.get("entries", []):
        if not isinstance(raw, dict) or "path" not in raw:
            raise ManifestError(f"malformed manifest entry: {raw!r}")
        entries.append(Entry(
            path=raw["path"], category=raw.get("category"),
            reason=raw.get("reason", ""), issue=raw.get("issue"),
            invoked_by=raw.get("invoked_by")))
    return entries


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ManifestError(f"{path}: {e}") from e


def audit(data: dict, *, repo_root: Path = REPO_ROOT, ref: str = "HEAD",
          list_files: Callable = git_list_files,
          read_file: Callable = default_read_file,
          tests_dir: Path | None = None) -> AuditResult:
    entries = parse_entries(data)
    state = classify(repo_root=repo_root, ref=ref, list_files=list_files,
                     tests_dir=tests_dir)
    undiscovered = {rel for rel, how in state.items() if how == "undiscovered"}
    listed = [e.path for e in entries]
    listed_set = set(listed)

    problems = []
    for rel in sorted(undiscovered - listed_set):
        problems.append(
            f"the gated suite does not run this and nothing declares what "
            f"does: {rel!r} -- name its invoker, or record why nothing runs "
            "it with an owning issue (#716 change 4)")
    for rel in sorted(listed_set - undiscovered):
        problems.append(
            f"stale manifest entry -- {rel!r} is discovered now (or is gone), "
            "so its entry must be removed rather than left to accumulate")
    for rel in sorted({p for p in listed if listed.count(p) > 1}):
        problems.append(f"duplicate manifest entry: {rel!r}")

    debt = structural = invoked = 0
    for e in entries:
        if e.category == "debt":
            debt += 1
            if not e.issue:
                problems.append(f"debt entry missing owning issue: {e.path!r}")
            if not e.reason:
                problems.append(f"debt entry missing a reason: {e.path!r}")
        elif e.category == "invoked":
            invoked += 1
            problems.extend(verify_declaration(
                e, repo_root, ref, list_files=list_files, read_file=read_file))
        elif e.category == "structural":
            structural += 1
            if not e.reason:
                problems.append(
                    f"structural entry must say why nothing runs it: {e.path!r}")
        else:
            problems.append(
                f"entry has no valid category (invoked|debt|structural): "
                f"{e.path!r}")
    return AuditResult(problems=problems, total=len(entries), debt=debt,
                       structural=structural, invoked=invoked)


def summary_line(result: AuditResult) -> str:
    return (f"{MANIFEST_PATH.name}: {result.total} entries total, "
            f"{result.invoked} declared-invoked, "
            f"{result.debt} debt (must only go down), "
            f"{result.structural} structural")
