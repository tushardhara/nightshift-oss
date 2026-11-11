#!/usr/bin/env python3
"""Comment-density ceiling -- issue #715, epic #716.

Measured on the first external run: the package Nightshift wrote was **47%
comment lines** against a repository whose own baseline was 4-7%. Roughly
19,000 lines of prose against 21,000 lines of code.

Why that is a defect and not a taste question: a reviewer opening a
5,836-line package reads 2,753 lines of justification before reaching the
logic, and the prose is well written, which makes it worse -- it reads as
substance and consumes the attention the logic needed. Long comments also
rot. That run produced one whose stated reason named the wrong state
transition, in a package whose entire thesis was that inaccurate safety
claims are the defect class.

The instruction "match the surrounding comment density" already existed and
was followed at 47% against a 4% baseline, so this is that instruction as
arithmetic. The ceiling is a MULTIPLE of the repository's own style, computed
at run time, so a codebase that genuinely writes at 25% keeps its style and
the rule transfers to the next project without retuning.

**What an adversarial review had to fix in the first version, because each one
made the guard weaker than it looked:**

- Python prose lives in DOCSTRINGS, and the first version counted only `#`
  lines -- so on this repository, which is 48 Python files and no Go, a 47%
  package written in docstrings scored 0% and the gate was inert. It scored
  its own source at 2%.
- It judged whole touched FILES, so appending one line to a large code file
  diluted any amount of new prose below the ceiling. It now judges the lines
  the change ADDS, which is what the change is responsible for.
- It read the working tree while listing paths from a revision, so a dirty
  checkout silently changed the verdict. Content now comes from the revision
  under judgement.
- An empty baseline (a language new to the repository -- exactly "the first
  external run wrote a package") reported `[ok]` and judged nothing.
- Wrapping prose onto one enormous line defeated a line-based ratio. Lines are
  now counted in WRAPPED EQUIVALENTS: a line counts as one unit per 100
  characters, so ordinary code and prose count exactly as before and 20,000
  words on a single line count as the 200 lines they are. A second, separate
  character threshold was tried first and rejected -- comments are naturally
  longer per line than short code lines, so it refused honest work.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CEILING_MULTIPLE = 2.0

# No change is refused below this, whatever the house style. A repository that
# happens to sit at 1% would otherwise forbid ordinary explanatory comments
# entirely, and a gate that refuses honest work is removed rather than obeyed.
ABSOLUTE_FLOOR = 0.15

# Below this many added lines the ratio is noise: three added lines with two
# comments is 66% and means nothing.
MIN_ADDED_LINES = 60

# One "line" of prose, for the purpose of the ratio. Any line counts as at
# least one unit and as one more per this many characters, which makes the
# measure indifferent to how the author wrapped it.
WRAP_WIDTH = 100

LINE, BLOCK, DOC = "line", "block", "doc"

LANGUAGES = {
    ".go":   {"line": "//", "block": ("/*", "*/")},
    ".js":   {"line": "//", "block": ("/*", "*/")},
    ".jsx":  {"line": "//", "block": ("/*", "*/")},
    ".ts":   {"line": "//", "block": ("/*", "*/")},
    ".tsx":  {"line": "//", "block": ("/*", "*/")},
    ".java": {"line": "//", "block": ("/*", "*/")},
    ".rs":   {"line": "//", "block": ("/*", "*/")},
    ".c":    {"line": "//", "block": ("/*", "*/")},
    ".h":    {"line": "//", "block": ("/*", "*/")},
    ".cpp":  {"line": "//", "block": ("/*", "*/")},
    ".cs":   {"line": "//", "block": ("/*", "*/")},
    ".kt":   {"line": "//", "block": ("/*", "*/")},
    ".swift":{"line": "//", "block": ("/*", "*/")},
    ".php":  {"line": "//", "block": ("/*", "*/")},
    ".scala":{"line": "//", "block": ("/*", "*/")},
    ".py":   {"line": "#", "docs": [('"""', '"""'), (", ")]},
    ".rb":   {"line": "#", "docs": [("=begin", "=end")]},
    ".sh":   {"line": "#"},
    ".pl":   {"line": "#"},
    ".lua":  {"line": "--", "block": ("--[[", "]]")},
    ".sql":  {"line": "--", "block": ("/*", "*/")},
}

# Matched against PATH SEGMENTS, never as a substring: `latest_run.go` contains
# "test_" and `spec.go` contains "spec.", and the first version exempted both
# from judgement and from the baseline -- a one-rename bypass.
TEST_DIRS = {"test", "tests", "spec", "fixtures", "testdata", "vendor",
             "node_modules", "third_party"}
TEST_FILE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|^conftest\.)")


def is_source(path: str) -> bool:
    if Path(path).suffix not in LANGUAGES:
        return False
    parts = path.split("/")
    if any(part in TEST_DIRS for part in parts[:-1]):
        return False
    return not TEST_FILE.search(parts[-1])


def units(text: str) -> int:
    """A line's weight in wrapped equivalents -- at least 1, one more per
    WRAP_WIDTH characters. This is what makes the ratio indifferent to
    wrapping, so unwrapping prose onto one line cannot hide it."""
    return max(1, -(-len(text) // WRAP_WIDTH))


@dataclass(frozen=True)
class Density:
    comment_lines: int = 0
    code_lines: int = 0
    comment_units: int = 0
    code_units: int = 0

    def __add__(self, other):
        return Density(self.comment_lines + other.comment_lines,
                       self.code_lines + other.code_lines,
                       self.comment_units + other.comment_units,
                       self.code_units + other.code_units)

    @property
    def lines(self) -> int:
        return self.comment_lines + self.code_lines

    @property
    def total_units(self) -> int:
        return self.comment_units + self.code_units

    @property
    def line_ratio(self) -> float:
        return (self.comment_units / self.total_units
                if self.total_units else 0.0)


def classify(lines, suffix: str) -> Density:
    """Split lines into comment and code, carrying block/docstring state.

    Two rules chosen deliberately in the SAFE direction, meaning they can only
    ever under-report prose and never invent it:

    - a line that closes a block and then continues with code counts as CODE;
    - `x := 1 // why` is CODE, because trailing notes are what a house style
      is largely made of and counting them as prose would punish the very
      thing the baseline measures.

    An unterminated block or docstring stops at end of input rather than
    swallowing everything after it, so one stray `/*` cannot mark a whole file
    as prose and refuse honest work.
    """
    spec = LANGUAGES[suffix]
    line_marker = spec.get("line")
    block = spec.get("block")
    docs = spec.get("docs", [])
    state = None
    closer = None
    out = Density()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        size = units(line)
        if state is not None:
            after = line.split(closer, 1)[1].strip() if closer in line else ""
            if closer in line:
                state = None
                if after and not after.startswith(str(line_marker)):
                    out += Density(0, 1, 0, size)
                    continue
            out += Density(1, 0, size, 0)
            continue
        pair = next((d for d in docs if line.startswith(d[0])), None)
        if pair:
            opener, close = pair
            rest = line[len(opener):]
            if close in rest:
                # opened and closed on one line
                out += Density(1, 0, size, 0)
            else:
                state, closer = DOC, close
                out += Density(1, 0, size, 0)
            continue
        if block and line.startswith(block[0]):
            rest = line[len(block[0]):]
            if block[1] in rest:
                tail = rest.split(block[1], 1)[1].strip()
                if tail:
                    out += Density(0, 1, 0, size)
                else:
                    out += Density(1, 0, size, 0)
            else:
                state, closer = BLOCK, block[1]
                out += Density(1, 0, size, 0)
            continue
        if line_marker and line.startswith(line_marker):
            out += Density(1, 0, size, 0)
            continue
        out += Density(0, 1, 0, size)
    return out


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + ": " + proc.stderr[:200])
    return proc.stdout


def added_lines(repo_root: Path, base: str, head: str) -> dict:
    """The lines this change ADDS, per file.

    The change is responsible for what it adds, not for the files it happens
    to touch. Judging whole files let one appended line to a large code file
    dilute any amount of new prose below the ceiling; judging added lines also
    stops a change being blamed for prose that was already there.
    """
    diff = _git(repo_root, "diff", "--unified=0", "--no-color",
                "--diff-filter=d", f"{base}..{head}")
    per_file, current = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if line.startswith("+") and not line.startswith("+++") and current:
            per_file.setdefault(current, []).append(line[1:])
    return {p: v for p, v in per_file.items() if is_source(p)}


def baseline(repo_root: Path, rev: str, suffix: str, exclude: set) -> Density:
    """The repository's own house style for one language, at `rev`.

    Read from the revision under judgement rather than the working tree: the
    first version listed paths from one revision and read content from
    another, so an uncommitted edit silently changed the verdict.

    Files the change touches are excluded, or a large enough violation
    dominates the average and licenses itself.
    """
    total = Density()
    for path in _git(repo_root, "ls-tree", "-r", "--name-only", rev).splitlines():
        if not path or path in exclude or Path(path).suffix != suffix:
            continue
        if not is_source(path):
            continue
        try:
            text = _git(repo_root, "show", f"{rev}:{path}")
        except RuntimeError:
            continue
        total += classify(text.splitlines(), suffix)
    return total


@dataclass(frozen=True)
class Verdict:
    suffix: str
    added: Density
    house: Density
    files: tuple

    @property
    def ceiling(self) -> float:
        return max(self.house.line_ratio * CEILING_MULTIPLE, ABSOLUTE_FLOOR)

    @property
    def no_baseline(self) -> bool:
        return self.house.lines == 0

    @property
    def over(self) -> bool:
        if self.added.lines < MIN_ADDED_LINES:
            return False
        return self.added.line_ratio > self.ceiling

    def describe(self) -> str:
        house = ("no existing %s in this repository, so the ceiling is the "
                 "absolute floor" % self.suffix if self.no_baseline
                 else f"this repository's own style is "
                      f"{self.house.line_ratio:.0%}")
        return (
            f"{self.suffix}: the lines this change ADDS are "
            f"{self.added.line_ratio:.0%} comments "
            f"({self.added.comment_lines} of {self.added.lines} lines, "
            f"wrapped-equivalent {self.added.comment_units} of "
            f"{self.added.total_units}); "
            f"{house}. Ceiling {self.ceiling:.0%}.\n"
            f"    files: " + ", ".join(self.files[:6])
            + (f" (+{len(self.files) - 6} more)" if len(self.files) > 6 else ""))


def assess(repo_root: Path, base: str, head: str) -> list:
    per_file = added_lines(repo_root, base, head)
    by_suffix = {}
    for path, lines in per_file.items():
        by_suffix.setdefault(Path(path).suffix, {})[path] = lines

    verdicts = []
    for suffix, files in sorted(by_suffix.items()):
        added = Density()
        for lines in files.values():
            added += classify(lines, suffix)
        verdicts.append(Verdict(
            suffix=suffix, added=added,
            house=baseline(repo_root, head, suffix, set(files)),
            files=tuple(sorted(files))))
    return verdicts


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="comment-density ceiling")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    try:
        verdicts = assess(Path(args.repo), args.base, args.head)
    except RuntimeError as exc:
        print(f"comment-density guard could not run: {exc}")
        return 2
    over = [v for v in verdicts if v.over]
    for verdict in verdicts:
        print(f"[{'REFUSED' if verdict.over else 'ok'}] {verdict.describe()}")
    if over:
        print("\nWrite less prose, or put it in the decision record where it "
              "is reviewed and dated, rather than beside code where it rots "
              "(#715).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
