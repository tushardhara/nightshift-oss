"""Authorship guard -- issue #732, epic #716 change 5.

#716 item F: *"Program authored ~93% of the commits"* against its own Hard
Rule 1 (`program/config.yaml:90`): *"NO CODE. Source and tests belong to
crews, platform, or oncall -- always. You author Markdown only."* The epic
asks for **a rule that is enforced rather than stated**.

**What could not be built, and why it is written here.** The obvious
enforcement is a tool-call policy denying `git commit` of source. ADR-0009
already tried exactly that, and removed it after the review found the policy
layer *"sees no authoritative cwd or resolved repository target -- `git add --
.omni/STATUS.md` run in `/srv/project` and the identical command run in a
dedicated worktree are indistinguishable to the policy."* The event shape is
`{"type": "tool_call", "data": {"name", "arguments"}}` and carries no caller
identity either, so such a policy could not deny Program without denying the
crews it exists to protect. That path is closed, and re-opening it needs new
evidence rather than a fresh attempt.

So this guard works on the record instead of the act: the commits a branch
proposes. It is a **merge-time** gate, not a moment-of-action one, and it is
honest about the difference.

**What it can and cannot prove.** Author identity is forgeable -- nothing
stops a seat passing `--author`, and #725 records that seat identity at the
CLI is unauthenticated. What this guard changes is the DEFAULT: in the run
that produced #716, 114 source commits carried the box's fallback git identity
rather than any seat's, and that is what made "who wrote this" unanswerable
after the fact. Refusing the coordinator's own name and the unattributed
fallbacks turns the violation from something that happens by omission into
something that requires an affirmative lie. That is a real change in cost and
a real change in what the record means; it is not a proof of authorship, and
this docstring is the place that says so.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

# Hard Rule 1 reserves source and tests for "crews, platform, or oncall".
# These are the trees that rule is about, plus the three files under program/
# that ARE code or ARE the rules themselves -- an adversarial review pointed
# out that the coordinator could otherwise rewrite Hard Rule 1, the conformance
# ruleset, and the publish tool that hardcodes the crews' git identity, with
# this gate silent. Seat CONFIGS under program/agents/*/config.yaml stay
# unguarded: a seat editing its own charter is not the thing being stopped, and
# guarding all of program/** would block it.
SOURCE_PREFIXES = ("bin/", "deploy/", "tests/", ".github/")
SOURCE_FILES = ("program/config.yaml", "program/conformance-rules.json")


def is_source(path: str) -> bool:
    if path.startswith(SOURCE_PREFIXES) or path in SOURCE_FILES:
        return True
    # program/agents/<crew>/agents/<seat>/tools/** -- real code, e.g. the
    # 1400-line nightshift_publish.py the crews commit through.
    return path.startswith("program/agents/") and "/tools/" in path


# The seat roster, from `program/agents/`. Written out rather than globbed so
# that deleting a seat directory cannot silently shrink what this refuses.
ENTITLED_SEATS = frozenset({"crew_alpha", "crew_beta", "platform", "oncall"})
NON_ENTITLED_SEATS = frozenset({
    "architect", "product", "release", "security", "staff", "ux",
    "program",
})

# Names that identify no seat: the machine's default identity, and the tool
# identities that commit on somebody's behalf without saying whose.
UNATTRIBUTED_EXACT = frozenset({"ci", "unknown", ""})
UNATTRIBUTED_PREFIX = "nightshift"


def seat_of(author: str) -> str:
    """The seat an author name refers to, normalised.

    Seats commit under several spellings of one identity -- `crew_alpha`,
    `crew_alpha lead`, `crew_alpha.eng_codex` -- and a review found `Program`
    with a capital P already in this history, passing a case-sensitive check.
    So: lowercase, and take the leading token before a dot or a space.
    """
    name = (author or "").strip().lower()
    for separator in (".", " "):
        if separator in name:
            name = name.split(separator, 1)[0]
    return name


@dataclass(frozen=True)
class Violation:
    sha: str
    author: str
    paths: tuple
    reason: str


class GuardError(Exception):
    """A git query failed. Distinct from a violation, so a broken invocation
    is never reported as a clean scan."""


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GuardError(
            "git " + " ".join(args) + " failed: " + proc.stderr.strip()[:300])
    return proc.stdout


def commits_in(repo_root: Path, base: str, head: str) -> list:
    out = _git(repo_root, "log", "--no-merges", "--format=%H%x00%an",
               f"{base}..{head}")
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, _, author = line.partition("\0")
        rows.append((sha, author))
    return rows


def touched_source(repo_root: Path, sha: str) -> tuple:
    """Paths this commit writes that Hard Rule 1 reserves.

    `-z` because git C-quotes a non-ASCII path by default
    (`"bin/caf\303\251.py"`) and the leading quote defeated a startswith
    check -- a review demonstrated a commit adding `bin/café.py` passing
    unflagged. NUL-separated output is not quoted, so `-z` alone fixes it; a
    `core.quotePath=false` was tried alongside and removed once a mutation
    showed no test could tell the difference, which makes it configuration
    nobody can verify.

    `--no-renames` because rename detection prints only the destination, so
    `git mv bin/b.py moved.py` reported a path outside the guarded trees and
    every later edit to it was then invisible.
    """
    out = _git(repo_root, "show", "-z", "--name-only", "--no-renames",
               "--format=", sha)
    return tuple(sorted(
        path for path in out.split("\0") if path and is_source(path)))


def scan(repo_root: Path, base: str, head: str) -> list:
    """Commits in `base..head` that write source under a name that may not.

    Merges are skipped: a merge carries the union of its parents' paths under
    an author who wrote none of them, so judging one reports the integrator for
    the whole branch.

    An author this does not recognise PASSES. That is deliberate: the refused
    set is derived from the seat roster, and a human contributor is not on it.
    Refusing unknown names instead would mean guessing at identity, and a gate
    that blocks a new contributor's first commit gets switched off rather than
    fixed.
    """
    violations = []
    for sha, author in commits_in(repo_root, base, head):
        paths = touched_source(repo_root, sha)
        if not paths:
            continue
        seat = seat_of(author)
        if seat in UNATTRIBUTED_EXACT or seat.startswith(UNATTRIBUTED_PREFIX):
            reason = (f"authored by {author.strip()!r}, which names no seat -- "
                      "a source commit carrying the machine's default identity "
                      "or a tool's own name cannot be attributed to anyone, and "
                      "that is how #716's 114 unreviewed commits stayed "
                      "unattributable")
        elif seat in NON_ENTITLED_SEATS:
            reason = (f"authored by {author.strip()!r}: Hard Rule 1 reserves "
                      "source and tests for crews, platform and oncall, and "
                      f"{seat!r} is not one of them")
        else:
            continue
        violations.append(Violation(sha=sha, author=author.strip(),
                                    paths=paths, reason=reason))
    return violations


def format_violations(violations: Iterable) -> str:
    lines = []
    for v in violations:
        shown = ", ".join(v.paths[:4])
        if len(v.paths) > 4:
            shown += f" (+{len(v.paths) - 4} more)"
        lines.append(f"{v.sha[:12]} {v.reason}\n    touches: {shown}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    try:
        violations = scan(Path(args.repo), args.base, args.head)
    except GuardError as exc:
        # Fail closed, and say which it is: a reader must not have to guess
        # whether the gate refused a commit or could not run.
        print(f"authorship gate could not run: {exc}")
        return 2
    if violations:
        print(format_violations(violations))
        print(f"\n{len(violations)} source commit(s) authored by a name that "
              "may not write source (#716 change 5, ADR-0094).")
        return 1
    print("no source commits authored by the coordinator or an "
          "unattributed identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
