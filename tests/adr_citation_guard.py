"""ADR citation integrity guard.

Nightshift's code comments cite ADR decisions by name -- "ADR-0086's
greenfield rule", "ADR-0002 section 3" -- as a shorthand for "this behaviour
is sanctioned by that decision record." Three of those citations have turned
out to be fabricated: a rule that was never written into the ADR it claimed,
an ADR number that names the wrong decision entirely (right concept, wrong
file), and a rule spelled two different ways in the same file, only one of
which is correct. A reader trusts the citation instead of the ADR; the ADR
never gets checked.

This module extracts citations of three MECHANICALLY DECIDABLE shapes from
source comments and resolves each one against the ADR it names:

  * ``ADR-NNNN``                     -- a bare reference. A file
                                        ``docs/decisions/ADR-NNNN-*.md`` must
                                        exist.
  * ``ADR-NNNN's <token> rule`` (or ``section``/``clause``/``principle``/
    ``requirement`` in place of ``rule``)
                                    -- ``<token>`` must appear verbatim,
                                        casefolded and whitespace-normalised,
                                        somewhere in that ADR's text.
  * ``ADR-NNNN §N`` / ``ADR-NNNN section N``
                                    -- N must be a real numbered decision
                                        item in that ADR: a ``### N.``
                                        heading, a bold ``**N.`` opener, or a
                                        bare ``N.`` list opener. A trailing
                                        lower-case letter as in ``§4a`` is
                                        accepted in the citation but only the
                                        digit is checked against -- see LIMITS
                                        below.

Before any of the three shapes is matched, SOURCE text is run through
``_prepare_source_for_scan``: a ``#`` marker (with its indentation) starting a
continuation line is stripped, then every remaining whitespace run --
including the newline itself -- collapses to one space. This is the same
normalisation ``_normalize`` already applies to the ADR side. Without it, a
possessive citation whose token wraps across a line -- a Python comment
continued on a fresh ``#`` line, or an ordinary multi-line docstring
paragraph -- silently failed to match at all when the wrap landed INSIDE the
token (``\\s+`` around the token already tolerated a plain newline at the
token's edge; a newline, or a newline-plus-``#``, landing between two words
of the token itself did not). This found a real, previously-unchecked
citation in the tree (``nightshift_memory.py``'s ``ADR-0021's own\n    state_
root requirement``, wrapped mid-token in a docstring) whose ADR number turned
out to be wrong once checked -- fixed alongside this guard.

NOT checked, deliberately, rather than silently left uncovered:

  * Bare prose ("per ADR-0086", "see ADR-0086", "ADR-0086 says X") gets
    existence-checking only -- there is no token in that shape to resolve.
  * A possessive citation whose "token" IS the category word, with nothing
    naming a specific rule before it (e.g. "ADR-0082's requirement to stop
    ...") -- there is nothing distinct to resolve there either, and the
    regex below does not match this shape at all (by construction, not by
    special-casing it away).
  * Whether a resolved token sits in the ADR's actual DECISION as opposed to
    a REJECTED option it also happens to describe. This is a
    verbatim-substring check, not comprehension of what the ADR decided.
  * A lettered sub-item on its own: ``§4a`` is checked against numbered
    item ``4``, not against a distinct item ``4a`` -- a real but narrow gap,
    named here rather than pretended away.
  * Any citation form not listed above (e.g. a rule number attributed
    to no ADR at all, or a token spelled after rather than before the
    category noun). A future author can invent an unchecked lie in an
    unchecked shape; this guard closes the shapes above, not the concept.
  * The source-side line-joining above is unconditional -- it does not
    verify that a continuation ``#`` line is actually a continuation of the
    SAME comment rather than a second, unrelated ``#`` comment that merely
    follows it. Joining two unrelated lines could in principle glue extra
    words onto a token; what keeps this from producing a false positive is
    that the token match is already non-greedy and stops at the nearest
    category word (``rule``/``section``/...), so an unrelated FOLLOWING line
    only gets pulled in if the citation's own category word hasn't appeared
    yet -- the same bound that already lets a citation wrap across a
    paragraph break. A literal ``#`` character inside a genuine token (not a
    comment marker) would also be stripped by this pass; no citation in this
    tree needs one.

Mirrors ``tests/core_portability_guardrail.py``'s shape: ``list_files`` and
``read_file`` are injectable so fixture tests can point the same scan logic
at a real (non-git) temp directory. The real-tree scan uses ``git ls-files``
against the current worktree -- no ref is resolved, so it reads only state a
CI checkout is guaranteed to carry (#698); ADR files are discovered by a
plain filesystem glob under ``docs/decisions``, which is likewise
worktree-state, not a git ref. Because it is INDEX contents (not a working-
tree walk), a brand-new file added under a source root is invisible to a
local run of this guard until it is at least ``git add``-ed; CI always runs
against a full checkout of committed state, so this is a local-feedback gap
only, not a coverage hole in CI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
DEFAULT_SOURCE_ROOTS = ("bin", "program")

ListFiles = Callable[[Iterable[str], Path], List[str]]
ReadFile = Callable[[str, Path], str]

# --- citation shapes -------------------------------------------------------

_ADR_REF_RE = re.compile(r"ADR-(\d{4})")

# "ADR-0086's `absent-base-field` rule" / "ADR-0080's "One-way door? Yes" clause"
# The token is bounded and non-greedy so the match stops at the FIRST point
# where an optional quote/backtick, whitespace, and one of the category
# words line up -- it does not go hunting arbitrarily far into the file.
_TOKEN_CHARS = r"[A-Za-z0-9_./()?!:,\- ]"
_POSSESSIVE_RE = re.compile(
    r"ADR-(\d{4})'s\s+[\"'`]?(" + _TOKEN_CHARS + r"{2,80}?)[\"'`]?\s+"
    r"(rule|section|clause|principle|requirement)\b"
)

# "ADR-0002 §3" / "ADR-0002 §4a" / "ADR-0020 section 4"
_SECTION_RE = re.compile(
    r"ADR-(\d{4})\s+(?:§\s*(\d+)[a-z]?|[Ss]ection\s+(\d+)[a-z]?)\b"
)

# A numbered decision item: "### 4. ...", "**3. ...", or a bare "1. ..."
# list opener at the start of a (possibly indented) line. Deliberately loose
# about WHICH section of the ADR the number lives in -- see LIMITS above.
_NUMBERED_ITEM_RE = re.compile(
    r"^[ \t]*(?:#{2,4}[ \t]*|\*\*)?(\d+)[.)]", re.MULTILINE
)


class CitationScanError(Exception):
    """A scanned file could not be read as text. Fails loud, never skips --
    a source file the scan cannot read is a scan the guard cannot vouch for,
    not a silently-passing one (see FAIL-SOFT rule)."""


def git_ls_files(roots: Iterable[str], repo_root: Path) -> List[str]:
    """Tracked file paths under `roots`, from the CURRENT worktree's index.
    No ref is named or resolved -- this is `git ls-files`, not `git show
    <ref>:...` -- so it reads only state guaranteed present after a plain
    `actions/checkout` (#698)."""
    proc = subprocess.run(
        ["git", "ls-files", "--", *roots],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return [line for line in proc.stdout.splitlines() if line]


def walk_files(roots: Iterable[str], repo_root: Path) -> List[str]:
    """Plain filesystem walk, for fixture tests against a temp directory
    that is not (and must not need to be) a git repo."""
    out: List[str] = []
    for base in roots:
        root_dir = repo_root / base
        if not root_dir.is_dir():
            continue
        for p in sorted(root_dir.rglob("*")):
            if p.is_file():
                out.append(str(p.relative_to(repo_root)))
    return out


def default_read_file(rel_path: str, repo_root: Path) -> str:
    return (repo_root / rel_path).read_text(encoding="utf-8")


def _normalize(text: str) -> str:
    """Casefold and collapse all whitespace runs (including newlines) to a
    single space, so a token that reads naturally across a paragraph break
    (a heading followed by its answer, e.g.) still resolves as one phrase."""
    return re.sub(r"\s+", " ", text).casefold()


# A `#` marker (with its indentation) that starts a continuation line, e.g.
# the second line of:
#     # An absent base field is exempt here per ADR-0086's
#     # greenfield rule.
_COMMENT_CONTINUATION_RE = re.compile(r"\n[ \t]*#[ \t]*")


def _prepare_source_for_scan(text: str) -> str:
    """Mirror `_normalize` on the SOURCE side before any citation regex runs
    against it: strip a `#` continuation marker (with its indentation) from
    the start of a line, then collapse every remaining whitespace run --
    including the newline itself -- to a single space.

    Without this, a possessive citation whose token wraps across a line --
    a Python comment continued on a fresh `#` line, or an ordinary
    multi-line docstring paragraph -- silently failed to match at all when
    the wrap fell INSIDE the token. `\\s+` around the token already
    tolerates a plain newline that falls at the token's EDGE (between
    "ADR-0086's" and the token, or between the token and its category
    word); what it cannot do is match across a `#` marker (not whitespace),
    or contribute a newline to `_TOKEN_CHARS` (which excludes `\\n` so the
    token can't run away across the whole file). Stripping the marker and
    collapsing first removes both obstacles before the citation regexes see
    the text at all -- see module docstring for the residual, deliberate
    limits of this join.

    Only used to build the text these regexes scan; `problems`/`checked`
    report file + ADR + token, never a line number, so discarding line
    structure here costs nothing.
    """
    text = _COMMENT_CONTINUATION_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text)


def _adr_paths(repo_root: Path) -> Dict[str, Path]:
    """{4-digit number string: path} for every docs/decisions/ADR-NNNN-*.md
    under repo_root, found by plain filesystem glob (worktree state, not a
    git ref)."""
    decisions_dir = repo_root / "docs" / "decisions"
    out: Dict[str, Path] = {}
    if not decisions_dir.is_dir():
        return out
    for p in sorted(decisions_dir.glob("ADR-*.md")):
        m = re.match(r"ADR-(\d{4})-", p.name)
        if m:
            out[m.group(1)] = p
    return out


def scan(
    source_roots: Iterable[str] = DEFAULT_SOURCE_ROOTS,
    *,
    repo_root: Path = REPO_ROOT,
    list_files: ListFiles = git_ls_files,
    read_file: ReadFile = default_read_file,
) -> Tuple[List[str], List[dict]]:
    """Scan `source_roots` for ADR citations; check the ones that are
    mechanically checkable (see module docstring for the three shapes and
    their limits).

    Returns `(problems, checked)`:
      * `problems` -- human-readable strings, one per citation that failed
        to resolve, each prefixed `existence:`, `possessive:` or `section:`
        so a caller can filter by check kind. Empty means every checkable
        citation resolved.
      * `checked`  -- one dict per citation this scan actually looked at
        (`file`, `adr`, `kind` in {"existence", "possessive", "section"},
        `detail`), REGARDLESS of whether it resolved. A caller should assert
        this is non-empty and contains an expected entry -- an empty
        `problems` list from an empty `checked` list is a scan that found
        nothing to check, not a scan that found everything correct.
    """
    problems: List[str] = []
    checked: List[dict] = []

    adr_paths = _adr_paths(repo_root)
    adr_text_cache: Dict[str, Optional[str]] = {}
    adr_items_cache: Dict[str, set] = {}

    def get_text(num: str) -> Optional[str]:
        if num not in adr_text_cache:
            path = adr_paths.get(num)
            if path is None:
                adr_text_cache[num] = None
            else:
                rel = str(path.relative_to(repo_root))
                adr_text_cache[num] = read_file(rel, repo_root)
        return adr_text_cache[num]

    def get_items(num: str) -> set:
        if num not in adr_items_cache:
            text = get_text(num)
            items: set = set()
            if text is not None:
                for m in _NUMBERED_ITEM_RE.finditer(text):
                    items.add(int(m.group(1)))
            adr_items_cache[num] = items
        return adr_items_cache[num]

    for rel_path in list_files(source_roots, repo_root):
        try:
            raw_text = read_file(rel_path, repo_root)
        except (OSError, UnicodeDecodeError) as e:
            raise CitationScanError(
                f"{rel_path}: cannot read as text for citation scan -- {e}") from e
        text = _prepare_source_for_scan(raw_text)

        for m in _ADR_REF_RE.finditer(text):
            num = m.group(1)
            checked.append({"file": rel_path, "adr": num, "kind": "existence", "detail": ""})
            if num not in adr_paths:
                problems.append(
                    f"existence: {rel_path}: cites ADR-{num}, but no docs/decisions/"
                    f"ADR-{num}-*.md exists")

        for m in _POSSESSIVE_RE.finditer(text):
            num, token, category = m.group(1), m.group(2), m.group(3)
            checked.append({"file": rel_path, "adr": num, "kind": "possessive", "detail": token})
            adr_text = get_text(num)
            if adr_text is None:
                continue  # already reported above as a missing-ADR-file problem
            if _normalize(token) not in _normalize(adr_text):
                problems.append(
                    f"possessive: {rel_path}: cites ADR-{num}'s {token!r} {category}, but "
                    f"that token does not appear in ADR-{num}")

        for m in _SECTION_RE.finditer(text):
            num = m.group(1)
            sect = m.group(2) or m.group(3)
            checked.append({"file": rel_path, "adr": num, "kind": "section", "detail": sect})
            adr_text = get_text(num)
            if adr_text is None:
                continue  # already reported above as a missing-ADR-file problem
            if int(sect) not in get_items(num):
                problems.append(
                    f"section: {rel_path}: cites ADR-{num} §{sect}, but ADR-{num} "
                    f"has no numbered item {sect}")

    return problems, checked
