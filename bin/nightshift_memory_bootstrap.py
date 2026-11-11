"""Onboarding and freshness for narrative memory (#400 A4a / A4b).

Owned by this file only: `bootstrap()` and `sync()`. Neither one opens
`memory.db`, defines a table, or runs SQL -- that is `bin/nightshift_memory.py`
(Track A / A1)'s job, imported lazily as "the writer" (see `_load_writer`
below). This module's job is everything *before* a row exists: walking a
borrowed repo's TRACKED content only (never the working tree), deciding what
each tracked path is worth indexing as, and proving the result is complete.

Design points carried over from PLAN-400-memory-system.md section A4a/A4b,
kept here as comments at each decision site rather than restated in one
block: bootstrap is bounded by tracked bytes not disk size; every tracked
file lands in exactly one of indexed/skipped/failed so the coverage
manifest either balances exactly or the run fails loudly; row identity is
(project, src_path, line_start, sha256), which the WRITER enforces, not
this module -- calling it twice must be safe by construction, not by this
module remembering what it already sent.

ASSUMED WRITER CONTRACT (no `nightshift_memory.py` exists yet at the time
this module was written -- Track A is a separate, worktree-isolated build
in parallel). Per the recon brief that both tracks were handed:

    from nightshift_memory import put_memory
    put_memory(args) -> dict            # a plain JSON-able receipt,
                                         # never prints, never exits.
    args is argparse.Namespace-shaped (plain attribute access, mirroring
    every other nightshift-ctl verb) and idempotent by
    (args.project, args.src_path, args.line_start, args.sha256).

`_load_writer` and `_write_artifact` are the only two places that touch
that boundary. If A1 lands with a different name or shape, those are the
only functions that need to change.

PROJECT ARGUMENT. `project` accepts three shapes, most-to-least specific:
  1. the ADR-0020 project descriptor itself -- what `nightshift_project.
     show_project(args)["descriptor"]` returns, or what is written to
     `state_root/startups/<repository_id>/<project_run_id>.json`: a dict
     with "state_root" and a nested "repository" dict carrying "id" and
     "full_name" (verified by reading bind_project()'s descriptor
     construction in bin/nightshift_project.py).
  2. that same show_project() call's full envelope, `{"descriptor": {...},
     ...}` -- unwrapped automatically.
  3. a bare project-id string, in which case `state_root=` must be passed
     explicitly (there is nothing else to derive it from).
`bind_project(args)`'s own return does NOT carry state_root/repository
(the caller already supplied both in args) -- pass `state_root=` alongside
a bare id in that case, or fetch the descriptor via `show_project` first.

NOT this module's job, deliberately: taking the per-project sync lease
PLAN A4b calls for ("two seats indexing the same project must not
double-write") is a control-plane concern (`nightshift-ctl lease
acquire`) -- the caller takes it before calling `sync()`, this module has
no access to control.db (memory is plane-free by design) and does not
attempt its own locking beyond the resume-cursor bookkeeping below, which
only protects against wasted re-work after a crash, not against two
concurrent callers.

Sidecar files this module creates, both inside the caller's `state_root`
alongside (but never touching) `memory.db`:
  memory_bootstrap_state.json     resume cursor + last-completed watermark
  memory_bootstrap_manifest.json  the most recent coverage manifest, for
                                   operator inspection between runs
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import subprocess
import time
import types


# --------------------------------------------------------------------------
# Writer-facing defaults. The outer DESIGN schema requires classification,
# retention_class and key_version on every row; this module does not know
# nightshift_memory.py's real enum for the first two (that enum is Track
# A's to define), so they are overridable kwargs with a documented
# placeholder default rather than a value baked in silently.
# --------------------------------------------------------------------------
DEFAULT_CLASSIFICATION = "internal"
DEFAULT_RETENTION_CLASS = "standard"
DEFAULT_KEY_VERSION = 1

# PLAN A4a: "If tracked content exceeds a threshold (say 200 MB), it
# degrades to tiers 1-3 and says so in the manifest rather than silently
# truncating." Measured basis for the *un*-degraded case: testbed A is
# 9.2 MB of tracked content, the same order as Nightshift's own 5.2 MB.
DEFAULT_TRACKED_BYTES_BUDGET = 200_000_000

# D1 fix (defect measured on the testbed B testbed: tracked_bytes over
# budget flattened the ENTIRE doc tier -- 1,613 files / 10.33 MB -- into
# tier-2 pointers, while keeping all 54,119 structure pointers). Degrade
# must shed by VALUE, not flatten everything: these three budgets are
# independent per-tier backstops applied only once DEFAULT_TRACKED_BYTES_
# BUDGET above is exceeded (see _plan_budget). Tier 1 docs are tiny and
# carry the highest decision density per byte (PLAN A4a tier 1) -- 64 MB
# gives testbed B's real 10.33 MB doc tier ~6x headroom, so this is a
# backstop for a pathologically doc-heavy repo, not something either
# testbed is expected to hit.
DEFAULT_DOC_TIER_BYTES_BUDGET = 64_000_000

# Tier 2 structure rows store only a first-line pointer (see
# _index_one_file), never the full file -- measured on testbed B: 52,506
# structure files' first lines sum to 18.89 MB, falling to 2.56 MB once
# DEFAULT_STRUCTURE_MAX_FIRST_LINE_BYTES below is applied. This is a
# second, independent backstop above that per-row cap, sized generously
# against the same measurement.
DEFAULT_STRUCTURE_TIER_BYTES_BUDGET = 32_000_000

# Measured on the testbed B testbed: 247 of 52,506 structure-tier files
# (0.47%) are SINGLE LINES over 1 MB each (minified JS, ML model JSON)
# and together account for 87.8% of all structure-tier stored bytes -- a
# multi-megabyte "pointer" row is a defect at any budget, not only a
# degraded one. Applied unconditionally (never only when degraded): on
# testbed B this drops 121 files (0.23%) and collapses stored structure
# bytes 18.89 MB -> 2.56 MB; on testbed A it drops 0; on Nightshift it
# drops exactly 1 (a generated one-line JSON blob). SKIPPED, never
# truncated -- put()/verify_artifact both re-hash the FULL stored body
# (see _index_one_file's CONFIRMED CROSS-MODULE DEFECT #2 comment
# below), so truncating any stored body makes that row read `stale`
# forever.
DEFAULT_STRUCTURE_MAX_FIRST_LINE_BYTES = 4_096

# D2 fix (defect measured on testbed B: degrade silently zeroed
# commits_indexed with no skip entry, no downgrade record, no warning --
# tier 3 is where the WHY lives in a shared repo, PLAN A4a). This cap is
# independent of degrade and applies to EVERY history walk (bootstrap's
# full log and sync's watermark..head delta alike), recorded in the
# manifest's "history" block rather than silently capped. Measured basis:
# at testbed B's mean 3,987 B/commit body (2,000-commit shallow clone),
# `_git_log_records` peaks at ~38,540 B/commit of Python object overhead
# (tracemalloc, 77.1 MB for 2,000 commits) -- uncapped against the
# owner's real, non-shallow 171,651-commit testbed B that extrapolates to
# ~6.62 GB peak RSS, an OOM rather than a slowdown. 2,000 is also exactly
# the depth both testbeds actually carry, so this bound is provable here,
# not merely asserted. Deliberately does NOT cap the commit BODY --
# truncating an immutable object with no record of the truncation is
# worse than the count cap alone, which already bounds the tier.
DEFAULT_MAX_COMMITS = 2_000

# Keeps a single chunk row safely under the ~100 KB SQLite inline/CAS
# crossover the #400 arithmetic measured (median body 4,218 B, max
# 55,033 B in the existing corpus), while still holding a realistic doc
# file as one row in the common case. A single line longer than this
# still produces one over-budget chunk -- chunking only ever splits on a
# line boundary, never mid-line, so line_start/line_end stay meaningful
# provenance; see _chunk_lines.
DEFAULT_MAX_CHUNK_BYTES = 60_000

# How often the resume cursor is fsync'd to the state sidecar while a
# bootstrap run is in progress. Idempotency (via the writer) is what
# makes a re-run safe; this is purely how much re-work a crash costs.
DEFAULT_FLUSH_EVERY = 25

GIT_BIN = "/usr/bin/git"
# Mirrors bin/nightshift_project.py's GIT_PROBE_ENV: no global/system git
# config leakage, no interactive prompts, no pager blocking a pipe, C
# locale for stable machine-readable output. Not imported from there
# directly -- that module's constant is tuned for one quick `rev-parse`
# probe with a 2s timeout; the commands here (`ls-files`, `log` over
# hundreds of commits) want a longer budget, so this module keeps its own
# copy rather than a cross-module coupling on an unrelated detail.
#
# No `safe.directory=*`: it made git honour a repository owned by ANOTHER
# uid, config and all. git's ownership refusal stands, and fsmonitor and
# hooks stay off even in a repository we do own.
GIT_HARDENING = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
    "PAGER": "cat",
}
GIT_TIMEOUT_SECONDS = 60.0
GIT_SHORT_TIMEOUT_SECONDS = 10.0

STATE_FILENAME = "memory_bootstrap_state.json"
MANIFEST_FILENAME = "memory_bootstrap_manifest.json"

# Tier 1 (PLAN A4a): "Docs, ADRs, README, CONTRIBUTING, schema,
# migrations -- highest decision density per byte." Extension/basename
# catch prose docs; the directory-segment list additionally catches
# non-markdown decision-dense content (schema/migration SQL) that PLAN
# explicitly puts in tier 1 even though it isn't markdown.
DOC_EXTENSIONS = {".md", ".markdown", ".mdx", ".rst", ".adoc", ".txt"}
DOC_BASENAME_STEMS = {
    "readme", "contributing", "changelog", "license", "licence",
    "code_of_conduct", "codeowners", "authors", "notice", "maintainers",
}
DOC_DIR_SEGMENTS = {
    "docs", "doc", "documentation", "adr", "adrs", "decisions", "rfcs",
    "schema", "migrations",
}

# Generated/vendored directories skipped by default, WITH a recorded
# reason per PLAN A4a ("Explicit exclusions, recorded not assumed").
# Deliberately does NOT include repo-specific calls like testbed A'
# graphify-out/ or protos/ -- PLAN attributes those specifically to the
# #443 project_contract (a different track's output, not yet mergeable
# here); pass them via `generated_dirs=` at the call site once that
# contract is available, rather than this module guessing. Also
# deliberately excludes ambiguous names like "bin"/"obj" that are
# legitimate hand-written source directories in some stacks (this very
# repo has one) even though they are common build-output names in others.
DEFAULT_GENERATED_DIRS = {
    "node_modules": "package-manager dependency cache",
    "vendor": "vendored third-party dependency copy",
    "vendored": "vendored third-party dependency copy",
    "dist": "build output",
    "build": "build output",
    "out": "build output",
    "target": "build output (cargo/maven convention)",
    "__pycache__": "python bytecode cache",
    ".venv": "python virtualenv",
    "venv": "python virtualenv",
    ".next": "framework build output",
    ".nuxt": "framework build output",
    ".tox": "python test-matrix cache",
    ".mypy_cache": "type-checker cache",
    ".pytest_cache": "test-runner cache",
    "coverage": "test coverage report output",
}


class BootstrapError(Exception):
    """Raised for whole-run conditions, never for one bad file.

    A single unreadable doc, a single failed write, a single
    undecodable commit message must not abort indexing the other 855
    tracked files -- those are recorded in the failed/skipped buckets of
    the coverage manifest instead. This is reserved for conditions that
    make the *run itself* meaningless to continue: git unusable, the
    writer unimportable, state_root unwritable, or (PLAN A4a's "FAILS
    LOUDLY") a coverage manifest that does not balance.
    """

    def __init__(self, reason_code, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = dict(details or {})


_Outcome = collections.namedtuple(
    "_Outcome", "bucket tier reason downgraded_reason")

# D1: the plan is computed ONCE, up front, from stat-only data (see
# bootstrap()'s pass-1 loop) and threaded into every _index_one_file call
# as a single pure value -- never a mutable "degraded" bool recomputed or
# re-guessed per file. `doc_shed`/`structure_shed` are frozensets of
# rel_path, EMPTY when `degraded` is False (see _plan_budget), so an
# under-budget repo (both testbeds today: testbed A 9.2 MB, Nightshift
# ~8 MB) takes byte-identical code paths to before this fix.
_BudgetPlan = collections.namedtuple(
    "_BudgetPlan",
    "degraded tracked_bytes doc_shed structure_shed max_first_line_bytes")


# --------------------------------------------------------------------------
# git plumbing. Every call here reads FROM git's own view of the
# repository (ls-files, log, diff, merge-base) -- never os.walk / os.scandir
# over the working tree, per PLAN A4a: "Any design that walks the working
# tree is wrong: it would index build output, caches and .git internals."
# Reading the CONTENTS of a path that git ls-files already named is not
# walking -- it is the only way to get a tracked file's bytes.
# --------------------------------------------------------------------------

def _run_git(repo, args, *, timeout=GIT_TIMEOUT_SECONDS):
    try:
        return subprocess.run(
            [GIT_BIN, *GIT_HARDENING, *args],
            cwd=repo, env=GIT_ENV, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError("git_timeout", {"args": list(args)}) from exc
    except OSError as exc:
        raise BootstrapError(
            "git_unavailable", {"args": list(args), "error": str(exc)},
        ) from exc


def _require_dir(repo):
    if not os.path.isdir(repo):
        raise BootstrapError("repo_not_a_directory", {"repo": repo})


def _tracked_files(repo):
    """The entire memory surface: TRACKED files only, NUL-delimited so a
    path containing a newline or space can never be mis-split."""
    proc = _run_git(repo, ["ls-files", "-z"])
    if proc.returncode != 0:
        raise BootstrapError("git_ls_files_failed", {
            "returncode": proc.returncode,
            "stderr": proc.stderr.decode("utf-8", "replace")[:2000],
        })
    paths = [
        p.decode("utf-8", "surrogateescape")
        for p in proc.stdout.split(b"\x00") if p
    ]
    return sorted(paths)


def _head_sha(repo):
    """None means a real, valid repo with zero commits yet -- not an
    error; bootstrap still runs (indexing whatever is staged/tracked),
    it just has no repo_sha to pin rows to and skips tier 3."""
    proc = _run_git(
        repo, ["rev-parse", "--verify", "-q", "HEAD"],
        timeout=GIT_SHORT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("ascii").strip()


def _is_ancestor(repo, ancestor_sha, descendant_sha):
    """git's own contract for --is-ancestor: 0 = true, 1 = false, other
    = actual error (bad object, etc) -- must not be folded into "false"."""
    proc = _run_git(
        repo, ["merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        timeout=GIT_SHORT_TIMEOUT_SECONDS,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise BootstrapError("git_merge_base_failed", {
        "returncode": proc.returncode,
        "stderr": proc.stderr.decode("utf-8", "replace")[:2000],
    })


def _changed_paths_since(repo, watermark_sha, head_sha):
    proc = _run_git(repo, ["diff", "--name-only", f"{watermark_sha}..{head_sha}"])
    if proc.returncode != 0:
        raise BootstrapError("git_diff_failed", {
            "returncode": proc.returncode,
            "stderr": proc.stderr.decode("utf-8", "replace")[:2000],
        })
    text = proc.stdout.decode("utf-8", "surrogateescape")
    return sorted(p for p in text.split("\n") if p)


def _git_log_records(repo, *, since, until, max_records=None):
    """Yields (sha, author_epoch, message) for until, or since..until.

    Delimited with \\x02 (record) / \\x01 (field, maxsplit=2 so a stray
    \\x01 inside a commit body -- vanishingly unlikely, never expected --
    still lands in the message rather than desyncing later records).

    D2 fix: `max_records`, when given, is passed to git itself as `-n
    <max_records>` -- NOT applied as a post-hoc `records[:max_records]`
    slice. Measured: this function already decodes the whole log output,
    splits it, and holds the complete record list in memory at once
    (tracemalloc: ~38,540 B/commit peak for a 2,000-commit walk); a slice
    taken after the fact would still have materialized every commit
    first, which is exactly the OOM this cap exists to prevent at the
    owner's real, 171,651-commit testbed B (extrapolated: ~6.62 GB peak
    RSS uncapped). `-n` bounds what git itself ever formats and pipes
    back, so memory stays flat regardless of true repo depth.
    """
    rev_range = until if since is None else f"{since}..{until}"
    argv = ["log", "--no-renames"]
    if max_records is not None:
        argv += ["-n", str(max_records)]
    argv += ["--format=%x02%H%x01%at%x01%B", rev_range]
    proc = _run_git(repo, argv)
    if proc.returncode != 0:
        raise BootstrapError("git_log_failed", {
            "returncode": proc.returncode,
            "stderr": proc.stderr.decode("utf-8", "replace")[:2000],
        })
    text = proc.stdout.decode("utf-8", "replace")
    records = []
    for chunk in text.split("\x02"):
        if not chunk:
            continue
        sha, epoch_str, body = chunk.split("\x01", 2)
        records.append((sha, int(epoch_str), body.rstrip("\n")))
    return records


def _rev_list_count(repo, rev_range):
    """`git rev-list --count <rev_range>` -- cheap (measured 0.062s on
    the testbed B testbed) whole-history-size fact, used only to report
    "how many commits exist" alongside how many were actually indexed.
    None (not 0, not raised) on any git failure -- this is a supplementary
    fact for the manifest's "history" block, never allowed to fail the
    run the way an unbalanced file-coverage manifest does.
    """
    proc = _run_git(repo, ["rev-list", "--count", rev_range],
                     timeout=GIT_SHORT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.decode("ascii", "replace").strip())
    except ValueError:
        return None


def _is_shallow_repo(repo):
    """`git rev-parse --is-shallow-repository` -- measured TRUE on BOTH
    sealed testbeds (testbed B at 2,000 commits, testbed A at 545). On a
    shallow clone, `_rev_list_count`'s "available" figure describes the
    CLONE's depth, not the real repository's true history depth, so a
    reader cannot tell "we capped at max_commits" from "the clone only
    has this many commits" unless this is recorded too. None (not a
    guessed bool) on any git failure, same non-fatal contract as
    `_rev_list_count`.
    """
    proc = _run_git(repo, ["rev-parse", "--is-shallow-repository"],
                     timeout=GIT_SHORT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("ascii", "replace").strip() == "true"


# --------------------------------------------------------------------------
# Classification -- pure functions, no I/O, easy to unit test standalone.
# --------------------------------------------------------------------------

def _is_generated(path, generated_dirs):
    """First matching ancestor directory wins, checked shallow-to-deep, so
    a nested override (e.g. a caller-supplied entry deeper than a
    built-in one) is not shadowed by a shallower built-in match unless it
    truly is shallower.

    Two independent key vocabularies share one dict, disambiguated by
    whether a key contains "/":
      * a bare segment name -- the built-in DEFAULT_GENERATED_DIRS
        convention ("node_modules", "vendor", ...) -- matches that
        directory name at ANY depth.
      * a "/"-joined relative path -- the #443 project_contract's own
        vocabulary (its `excluded_dirs` entries are full paths like
        "protos/type_coupons", "graphify-out/cache", not bare segments) --
        matches only that EXACT path, as a prefix of `path`.
    Both were silently incompatible before this: a multi-segment
    project_contract entry passed straight through as `generated_dirs`
    never matched anything here (`generated_dirs.get(seg)` only ever looks
    up single segments), so #443's own exclusions were silently indexed
    anyway. See `generated_dirs_from_contract` below for the other half
    (converting a contract's `excluded_dirs` list into a dict this
    function can use at all).
    """
    prefix = ""
    for seg in path.split("/")[:-1]:
        reason = generated_dirs.get(seg)
        if reason is not None:
            return reason
        prefix = f"{prefix}/{seg}" if prefix else seg
        if prefix != seg:  # a real multi-segment prefix, not a repeat lookup
            reason = generated_dirs.get(prefix)
            if reason is not None:
                return reason
    return None


def generated_dirs_from_contract(contract):
    """Convert a #443 `nightshift_project_contract.build_contract()` result
    into the `Dict[str, str]` shape `bootstrap()`/`sync()`'s `generated_dirs`
    kwarg (and `_is_generated` above) expect.

    Two integration gaps this closes, both silent before: `contract[
    "excluded_dirs"]["dirs"]` is a LIST of `{"path", "reason", "evidence"}`
    dicts, and passing that list itself as `generated_dirs` (the natural
    thing to try) raises `ValueError: dictionary update sequence element
    #0 has length 3; 2 is required` the instant `_is_generated` calls
    `generated_dirs.get(...)` on it -- a dict is genuinely required, not
    merely documented as one. And a handful of contract entries are not
    real filesystem paths at all -- `(sonar.exclusions)` is a literal,
    human-readable placeholder for a corroborating sonar-project.properties
    line, not a directory -- which would otherwise become a `generated_dirs`
    key that can never match a real tracked path and clutters the dict for
    no purpose; skipped here rather than left for `_is_generated` to
    silently never match.

    Value is `"{reason}: {evidence}"` when there's room for both (kept
    under a generous cap so one long evidence string can't blow past a
    coverage manifest's own item size norms), falling back to `reason`
    alone -- always non-empty, matching PLAN-400 A4a's own "explicit
    exclusions, recorded not assumed" (a caller inspecting the coverage
    manifest's skip reasons always finds real text, never a blank).
    """
    # #443 schema v2 wraps excluded_dirs in the envelope
    # ({"status", "value": {"dirs": [...]}, ...}); v1 returned {"dirs":
    # [...]} directly. Accept both so this function works against either a
    # v1 contract still in flight somewhere and a v2 one, rather than
    # breaking the instant the producer's shape changes out from under it.
    block = contract.get("excluded_dirs") or {}
    dirs = (block.get("value") or {}).get("dirs") if isinstance(block.get("value"), dict) else None
    if dirs is None:
        dirs = block.get("dirs") or []

    result = {}
    for entry in dirs:
        path = str(entry.get("path") or "").strip("/")
        if not path or path.startswith("("):
            continue  # not a real path -- e.g. the "(sonar.exclusions)" placeholder
        reason = str(entry.get("reason") or "").strip()
        # v2 also carries a human-readable `note` (v1 had no such field,
        # only a prose `evidence` string; v2's own `evidence` is a list of
        # repo-relative paths, not prose) -- prefer it, fall back to
        # v1's string evidence, then a joined v2 evidence-path list.
        detail = str(entry.get("note") or "").strip()
        if not detail:
            raw_evidence = entry.get("evidence")
            if isinstance(raw_evidence, list):
                detail = "; ".join(raw_evidence)
            else:
                detail = str(raw_evidence or "").strip()
        if reason and detail:
            result[path] = f"{reason}: {detail}"[:300]
        else:
            result[path] = (reason or detail or "generated_or_excluded")[:300]
    return result


def _tier_for_path(path):
    parts = path.split("/")
    stem, ext = os.path.splitext(parts[-1])
    if ext.lower() in DOC_EXTENSIONS or stem.lower() in DOC_BASENAME_STEMS:
        return "doc"
    if {p.lower() for p in parts[:-1]} & DOC_DIR_SEGMENTS:
        return "doc"
    return "structure"


def _structure_first_line_bytes(repo, candidates, max_first_line_bytes):
    """I/O helper, deliberately kept SEPARATE from `_plan_budget` (which
    stays pure) -- bounded reads (at most `max_first_line_bytes + 1`
    bytes per file, never the whole file), so this stays cheap even over
    tens of thousands of files: measured 4.4s over testbed B's 53,621
    structure-tier candidates.

    Why this exists at all, found by RUNNING the first version of this
    fix, not anticipated: a structure-tier row's STORED cost is its
    first line only (see _index_one_file), which raw `os.path.getsize()`
    cannot tell you -- measured mismatch on testbed B: 563 MB of raw
    structure-tier bytes on disk vs 2.6 MB ever actually stored. Using
    raw disk size as a stand-in for stored size in `_plan_budget` sheds
    the vast majority of structure-tier files for a budget reason that
    has nothing to do with what they would actually have cost --
    confirmed by running the disk-size version end to end against
    testbed B: it shed 25,274 of 52,507 structure candidates, a regression
    (skipping cheap pointers that used to be indexed), not the fix D1
    was for. This function makes `_plan_budget`'s structure-tier
    accounting match what will actually be written.

    A file whose real first line is LONGER than `max_first_line_bytes`
    contributes NOTHING here (never 0-clamped to the cap) -- it will be
    skipped outright by _index_one_file's own always-on cap (see
    DEFAULT_STRUCTURE_MAX_FIRST_LINE_BYTES), so it never gets stored and
    must not consume any of the structure tier's budget either.
    """
    out = {}
    for rel_path in candidates:
        full = os.path.join(repo, rel_path)
        try:
            if os.path.islink(full):
                out[rel_path] = 0
                continue
            with open(full, "rb") as fh:
                chunk = fh.read(max_first_line_bytes + 1)
        except OSError:
            continue  # unreadable -- not a shed candidate; _index_one_file's
            # own exists/open handling is what records the real failure.
        first_line = chunk.split(b"\n", 1)[0]
        if len(first_line) > max_first_line_bytes:
            continue  # will be skipped outright at index time -- 0 bytes
            # ever stored, so it must not count against the budget.
        out[rel_path] = len(first_line)
    return out


def _plan_budget(tracked, sizes, structure_stored_sizes, gen_dirs, *,
                  tracked_bytes_budget, doc_budget, structure_budget,
                  max_first_line_bytes):
    """Pure -- no I/O -- so the exact same plan can be recomputed by both
    bootstrap() and sync() (the latter previously hardcoded `degraded=
    False`, its own separate latent bug -- see sync()'s docstring) without
    persisting it: stat is cheap (measured 0.21s over testbed B's 55,235
    tracked files), so recomputing beats trusting a stale cached decision.
    The I/O needed for accurate structure-tier accounting is done by the
    caller, ONLY when degraded, via `_structure_first_line_bytes` above,
    and passed in as `structure_stored_sizes` -- kept out of this function
    so it stays a pure, standalone-testable planner.

    D1 fix: replaces the old `degraded -> everything becomes tier 2`
    flattening with shed-by-VALUE. Candidates within each tier are sorted
    ascending by (size, rel_path) -- size first so the smallest, cheapest
    rows are KEPT and the largest are shed; rel_path as a tiebreaker so a
    re-run at an unchanged tree always produces an IDENTICAL shed set
    (required for resume correctness -- see bootstrap()'s resume-state
    handling and D1's "Resume correctness" risk note). Generated/excluded
    paths are never candidates: _index_one_file skips them before tier or
    budget is ever consulted, so counting their bytes here would only
    consume budget on content that was never going to be stored anyway.

    Doc-tier candidates are sized by `sizes` (raw tracked-file bytes) --
    correct, since a doc row stores its FULL body, chunked, never
    truncated. Structure-tier candidates are sized by
    `structure_stored_sizes` (bounded first-line bytes) instead, NOT
    `sizes` -- see `_structure_first_line_bytes`'s docstring for the
    measured reason raw disk size is the wrong unit there.

    Returns an empty-shed plan whenever `tracked_bytes_budget` is not
    exceeded -- both real testbeds (testbed A 9.2 MB, Nightshift ~8 MB)
    take this branch today, so this fix changes nothing observable for
    them; only a repo shaped like testbed B (591 MB tracked) exercises the
    shed logic below.
    """
    tracked_bytes = sum(sizes.values())
    degraded = tracked_bytes > tracked_bytes_budget
    if not degraded:
        return _BudgetPlan(False, tracked_bytes, frozenset(), frozenset(),
                            max_first_line_bytes)

    def _shed_over_budget(paths, size_of, budget):
        kept_bytes = 0
        shed = set()
        for p in paths:
            size = size_of[p]
            if kept_bytes + size <= budget:
                kept_bytes += size
            else:
                shed.add(p)
        return shed

    doc_candidates = [
        p for p in tracked
        if p in sizes and _is_generated(p, gen_dirs) is None
        and _tier_for_path(p) == "doc"
    ]
    doc_candidates.sort(key=lambda p: (sizes[p], p))
    doc_shed = _shed_over_budget(doc_candidates, sizes, doc_budget)

    # `structure_stored_sizes` is already the fully-filtered candidate
    # set (generated paths and over-cap first lines excluded by
    # `_structure_first_line_bytes`) -- no second filter pass needed.
    structure_candidates = sorted(
        structure_stored_sizes, key=lambda p: (structure_stored_sizes[p], p))
    structure_shed = _shed_over_budget(
        structure_candidates, structure_stored_sizes, structure_budget)

    return _BudgetPlan(True, tracked_bytes, frozenset(doc_shed),
                        frozenset(structure_shed), max_first_line_bytes)


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _errno_reason(exc):
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, FileNotFoundError):
        return "working_tree_missing"
    if isinstance(exc, IsADirectoryError):
        return "is_a_directory"
    return f"os_error: {exc.strerror or exc}"[:200]


def _chunk_lines(text, max_bytes):
    """Split on line boundaries only, each chunk <= max_bytes where
    possible (a single line longer than max_bytes still becomes one
    over-budget chunk rather than being split mid-line, which would make
    line_start/line_end stop meaning what they say). Total and
    deterministic: every line appears in exactly one chunk, in order, so
    re-chunking identical bytes always produces identical
    (line_start, line_end) pairs -- required for the writer's
    (src_path, line_start, sha256) row identity to actually deduplicate
    across repeated runs.

    Chunk text is built with EXACTLY `nightshift_memory_search.
    verify_artifact`'s own re-hash convention: split on plain "\\n"
    boundaries (not `str.splitlines`, which also breaks on \\r, \\v, \\f
    and several Unicode line separators verify_artifact's raw
    `bytes.split(b"\\n")` does not treat as boundaries at all), and each
    chunk's text is `"\\n".join(...)` of its lines -- NO trailing newline
    ever appended, even for the chunk that ends the file. Getting this
    wrong is exactly the bug tests/test_memory_freshness.py's module
    docstring names as CONFIRMED CROSS-MODULE DEFECT #1: the previous
    `"".join(str.splitlines(keepends=True))` construction preserved each
    line's own terminator, so a file ending in "\\n" (the POSIX-normal
    case) produced a stored body byte-for-byte different from what
    verify_artifact recomputes for an IDENTICAL, unedited slice -- every
    doc-tier row read `stale` from the moment it was written. `put()`
    computes a row's sha256 FROM the body it is given (never trusted from
    a caller, by design -- see nightshift_memory.py's own docstring), so
    matching verify_artifact's convention here, at the only place chunk
    text is built, is the one place this needs fixing.
    """
    raw_lines = text.split("\n")
    # A trailing "\n" at EOF produces one extra, empty trailing element
    # from str.split that is not a real line -- verify_artifact's own
    # `bytes.split(b"\n")` produces the identical extra element and never
    # lets a valid line_end include it (`line_end <= len(lines)`, and
    # `lines[start-1:end]` with end capped at the real line count never
    # reaches it either). Dropping it here keeps line numbering identical
    # between write time and read time without changing what content is
    # chunked; a file with NO trailing newline has no such phantom element
    # to drop, so this is a no-op for it.
    if len(raw_lines) > 1 and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]
    if not raw_lines:
        return [(1, 1, "")]
    chunks = []
    start = 0
    cur, cur_bytes = [], 0
    for line in raw_lines:
        line_bytes = len(line.encode("utf-8")) + 1  # +1: the "\n" joiner
        if cur and cur_bytes + line_bytes > max_bytes:
            chunks.append((start + 1, start + len(cur), "\n".join(cur)))
            start += len(cur)
            cur, cur_bytes = [], 0
        cur.append(line)
        cur_bytes += line_bytes
    if cur:
        chunks.append((start + 1, start + len(cur), "\n".join(cur)))
    return chunks


# --------------------------------------------------------------------------
# Writer boundary -- the only two functions that know about
# nightshift_memory.py. See the ASSUMED WRITER CONTRACT note at module top.
# --------------------------------------------------------------------------

def _load_writer():
    try:
        from nightshift_memory import put_memory
    except ImportError as exc:
        raise BootstrapError("memory_writer_unavailable", {
            "detail": str(exc),
        }) from exc
    return put_memory


def _write_artifact(put_memory, *, state_root, project, kind, classification,
                     src_path, line_start, line_end, sha256_hex, size,
                     repo_sha, retention_class, key_version, body,
                     refs=None, ts=None):
    args = types.SimpleNamespace(
        state_root=state_root,
        project=project,
        kind=kind,
        classification=classification,
        src_path=src_path,
        line_start=line_start,
        line_end=line_end,
        sha256=sha256_hex,
        size=size,
        repo_sha=repo_sha,
        retention_class=retention_class,
        key_version=key_version,
        derived_from=None,
        body=body,
        refs=list(refs or ()),
    )
    if ts is not None:
        args.ts = ts
    return put_memory(args)


# --------------------------------------------------------------------------
# Sidecar state -- this module's own bookkeeping, never memory.db's schema.
# --------------------------------------------------------------------------

def _state_path(state_root):
    return os.path.join(state_root, STATE_FILENAME)


def _load_state(state_root):
    try:
        with open(_state_path(state_root), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # A corrupt or torn sidecar (e.g. a crash mid-write, before
        # os.replace ever ran -- so this should be rare) must not crash
        # the run or lie about a resume point that never fully landed.
        # Treat it as "nothing usable" and let the writer's own
        # idempotency absorb the cost of a full re-pass.
        return None


def _save_state(state_root, state):
    path = _state_path(state_root)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise BootstrapError("state_root_unwritable", {
            "state_root": state_root, "error": str(exc),
        }) from exc


def _save_manifest(state_root, manifest):
    # Best-effort operator visibility only -- the manifest is already the
    # function's return value, so a failure writing this copy must not
    # turn an otherwise-successful run into a raised error.
    try:
        path = os.path.join(state_root, MANIFEST_FILENAME)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, sort_keys=True, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def _project_identity(project, state_root_override, repository_id=None):
    """Normalize the three accepted `project` shapes -- see the
    "PROJECT ARGUMENT" note in the module docstring. Verified against
    the real ADR-0020 descriptor shape by reading bind_project()'s
    descriptor construction in bin/nightshift_project.py: top-level
    "state_root", nested "repository": {"id", "full_name", ...}.

    `repository_id`, when given, additionally scopes the resolved root
    through `nightshift_memory.resolve_memory_root` -- see that function's
    own docstring for why this partitioning happens here rather than
    inside `connect()`/`db_path()`. Optional and `None` by default so
    every existing single-project-per-state_root caller (this module's own
    test suite included) keeps its exact current behavior: a bare
    ADR-0021 state_root is only NOT 1:1 with one project when it is
    shared across repository_ids, which is precisely the case
    `repository_id` exists to disambiguate.
    """
    if isinstance(project, str):
        project_id, descriptor = project, {}
    elif isinstance(project, dict):
        maybe = project.get("descriptor")
        descriptor = maybe if isinstance(maybe, dict) else project
        repository = descriptor.get("repository") or {}
        project_id = (
            descriptor.get("project")
            or repository.get("full_name")
            or repository.get("id")
            or project.get("repository_id")
        )
    else:
        raise BootstrapError("unsupported_project_value", {
            "type": type(project).__name__,
        })

    root = state_root_override or descriptor.get("state_root")
    if not project_id:
        raise BootstrapError("project_identity_unresolvable", {
            "project": repr(project)[:200],
        })
    if not root:
        raise BootstrapError("state_root_required", {
            "project": str(project_id),
        })
    if repository_id is not None:
        # Lazy, same as `_load_writer` below -- this module never assumes
        # nightshift_memory.py is importable until it actually needs
        # something from it.
        import nightshift_memory
        try:
            root = nightshift_memory.resolve_memory_root(root, repository_id)
        except nightshift_memory.MemoryStoreError as exc:
            raise BootstrapError(exc.reason_code, exc.details) from exc
    return str(project_id), root


# --------------------------------------------------------------------------
# Per-file classify-and-index -- shared by bootstrap() and sync().
# --------------------------------------------------------------------------

def _index_one_file(put_memory, *, full_path, rel_path, project, state_root,
                     head_sha, classification, retention_class, key_version,
                     max_chunk_bytes, generated_dirs, plan):
    reason = _is_generated(rel_path, generated_dirs)
    if reason is not None:
        return _Outcome("skipped", None, reason, None)

    # Never dereference a tracked symlink -- it may point outside the
    # repo entirely. Recorded as a deliberate, reasoned skip (matching
    # PLAN A4a's "recorded not assumed"), not silently followed.
    if os.path.islink(full_path):
        return _Outcome("skipped", None, "symlink", None)

    if not os.path.exists(full_path):
        return _Outcome("failed", None, "working_tree_missing", None)

    # D1 fix: tier is ALWAYS the real classification -- degrade no longer
    # overwrites it. `_tier_for_path` decides WHAT a path is worth;
    # `plan` (a _BudgetPlan, always provided even when not degraded, see
    # _plan_budget) decides only whether THIS path's tier-appropriate
    # storage form is affordable this run. Measured basis: the old
    # `"structure" if degraded else _tier_for_path(...)` flattened
    # testbed B's entire 1,613-file / 10.33 MB doc tier into pointers while
    # keeping all 54,119 structure pointers -- discarding the highest
    # decision-density tier (PLAN A4a tier 1) to keep the cheapest one.
    tier = _tier_for_path(rel_path)
    downgraded_reason = None
    if tier == "doc" and rel_path in plan.doc_shed:
        # Doc tier itself is over its own (independent) budget -- shed to
        # a tier-2 pointer, same as the pre-existing not_utf8 downgrade
        # below, and recorded through that SAME manifest channel (see
        # bootstrap()'s loop -> manifest["downgraded"]) rather than a new
        # one. In practice this budget is generous enough (64 MB against
        # testbed B's measured 10.33 MB doc tier) that this almost never
        # fires on a real repo; it exists as a backstop, not the common
        # case.
        tier = "structure"
        downgraded_reason = "doc_budget_exceeded"
    elif tier == "structure" and rel_path in plan.structure_shed:
        # Structure tier's OWN independent budget is exceeded -- unlike
        # the doc-budget case above, there is no cheaper tier left to
        # fall back to (structure rows are already first-line-only
        # pointers), so this is a skip, not a downgrade.
        return _Outcome("skipped", None, "structure_budget_exceeded", None)

    try:
        with open(full_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return _Outcome("failed", None, _errno_reason(exc), None)

    text = None
    if tier == "doc":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # A doc-shaped path that isn't actually UTF-8 text is still
            # a real, indexable file -- just not one with a storable
            # body. Downgrade to a pointer row rather than failing it.
            tier = "structure"
            downgraded_reason = "not_utf8"

    try:
        if tier == "doc":
            for line_start, line_end, chunk_text in _chunk_lines(text, max_chunk_bytes):
                chunk_bytes = chunk_text.encode("utf-8")
                _write_artifact(
                    put_memory, state_root=state_root, project=project,
                    kind="doc", classification=classification,
                    src_path=rel_path, line_start=line_start, line_end=line_end,
                    sha256_hex=_sha256_bytes(chunk_bytes), size=len(chunk_bytes),
                    repo_sha=head_sha, retention_class=retention_class,
                    key_version=key_version, body=chunk_text,
                )
        else:
            # Tier 2 is "a pointer, without storing bodies" -- but `put()`
            # always computes a row's sha256 FROM the body it stores
            # (never trusted from a caller, H1), so a literally empty body
            # makes every structure-tier row's stored hash a constant,
            # sha256(""), regardless of the file's real content: it can
            # only coincidentally agree with a later re-hash, never by
            # design. See tests/test_memory_freshness.py's module
            # docstring, CONFIRMED CROSS-MODULE DEFECT #2. The fix stores
            # just the file's first line (matching line_start=line_end=1,
            # and matching verify_artifact's own re-hash convention
            # exactly: `raw.split(b"\n")[0]`) -- a small, bounded amount of
            # real content, not the whole file, so this stays a pointer in
            # spirit while making the hash mean something a re-verify can
            # actually confirm.
            first_line = raw.split(b"\n", 1)[0]
            # D1 fix: measured on testbed B -- 247 structure-tier files
            # (0.47%) are SINGLE LINES over 1 MB (minified JS, an ML
            # model's JSON, a source map) and together account for 87.8%
            # of all structure-tier stored bytes. A multi-megabyte
            # "pointer" row defeats the point of tier 2 at ANY budget, so
            # this applies always, not only when degraded (plan.
            # max_first_line_bytes is set unconditionally by
            # _plan_budget). SKIPPED, never truncated -- see the
            # CONFIRMED CROSS-MODULE DEFECT #2 note just below: put()/
            # verify_artifact both re-hash the FULL stored body, so a
            # truncated first line would make this row read `stale`
            # forever, not merely smaller.
            if (plan.max_first_line_bytes is not None
                    and len(first_line) > plan.max_first_line_bytes):
                return _Outcome(
                    "skipped", None,
                    f"structure_first_line_too_large:{len(first_line)}",
                    None)
            try:
                first_line_text = first_line.decode("utf-8")
            except UnicodeDecodeError:
                # A binary file's first line has no faithful `str` -- put()
                # requires a str body (its sha256 is computed from body,
                # never trusted from a caller). "replace" degrades this one
                # row's freshness check (a byte-for-byte identical re-hash
                # is no longer guaranteed), but the row is still indexed
                # and pinned, which a hard failure here would prevent.
                first_line_text = first_line.decode("utf-8", "replace")
            _write_artifact(
                put_memory, state_root=state_root, project=project,
                kind="structure", classification=classification,
                src_path=rel_path, line_start=1, line_end=1,
                sha256_hex=_sha256_bytes(raw), size=len(raw),
                repo_sha=head_sha, retention_class=retention_class,
                key_version=key_version, body=first_line_text,
            )
    except Exception as exc:  # noqa: BLE001 - one bad write must not
        # abort the other tracked files; record and move on.
        return _Outcome("failed", None, f"writer_error: {exc}"[:300], None)

    return _Outcome("indexed", tier, None, downgraded_reason)


def _index_commit_history(put_memory, *, repo, project, state_root,
                           classification, retention_class, key_version,
                           since, until, max_commits=DEFAULT_MAX_COMMITS):
    """Tier 3: commit messages, "where the WHY actually lives" (PLAN
    A4a). Supplementary, not part of the per-file coverage balance --
    one bad commit message must not fail the run the way an unbalanced
    file manifest does.

    D2 fix: returns a dict, not a bare int -- PLAN A4a's "no silent caps:
    log what was dropped" requires more than a count. `available` (from
    `git rev-list --count`) and `shallow` (from `git rev-parse
    --is-shallow-repository`) are both best-effort facts (None on any git
    failure, never fatal here) that let a reader distinguish "capped at
    max_commits of a much longer real history" from "a shallow clone's
    entire available history was indexed" from "this repo has no commits
    at all" -- three states the old bare `commits_indexed: 0`/`: N`
    collapsed into indistinguishable numbers. `write_failed` counts
    attempted-but-failed writes separately from `not_indexed` (commits
    never even attempted because `max_commits` bounded the git walk
    itself, per `_git_log_records`) -- the old code silently `continue`d
    past write failures with no record at all.
    """
    rev_range = until if since is None else f"{since}..{until}"
    available = _rev_list_count(repo, rev_range)
    shallow = _is_shallow_repo(repo)

    if available == 0:
        return {
            "indexed": 0, "available": 0, "shallow": shallow,
            "cap": max_commits, "capped": False,
            "newest_sha": None, "oldest_sha": None,
            "not_indexed": 0, "write_failed": 0,
            "reason": "repo_has_no_commits",
        }

    records = _git_log_records(repo, since=since, until=until,
                                max_records=max_commits)

    indexed = 0
    write_failed = 0
    for sha, epoch, body in records:
        body_bytes = body.encode("utf-8")
        try:
            _write_artifact(
                put_memory, state_root=state_root, project=project,
                kind="commit", classification=classification,
                # Synthetic path: no real tracked file can collide with
                # it (git never tracks a path under a literal
                # ".git-log/" segment), so it is a safe, self-describing
                # provenance key distinct from every src_path tier 1/2
                # ever produce.
                src_path=f".git-log/{sha}", line_start=1,
                line_end=max(1, body.count("\n") + 1),
                sha256_hex=_sha256_bytes(body_bytes), size=len(body_bytes),
                repo_sha=sha, retention_class=retention_class,
                key_version=key_version, body=body,
                refs=[("commit", sha)], ts=epoch,
            )
        except Exception:
            write_failed += 1
            continue
        indexed += 1

    # `available > max_commits` (not `len(records) == max_commits`) is
    # what proves the walk was actually truncated by `-n`, rather than
    # coincidentally returning exactly `max_commits` records because that
    # IS the repo's whole available history.
    capped = (max_commits is not None and len(records) == max_commits
              and (available is None or available > max_commits))
    not_indexed = max(0, available - len(records)) if available is not None else None
    if capped:
        reason = "cap_reached"
    elif shallow:
        reason = "shallow_clone"
    else:
        reason = None

    return {
        "indexed": indexed, "available": available, "shallow": shallow,
        "cap": max_commits, "capped": capped,
        "newest_sha": records[0][0] if records else None,
        "oldest_sha": records[-1][0] if records else None,
        "not_indexed": not_indexed, "write_failed": write_failed,
        "reason": reason,
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def bootstrap(repo, project, *, state_root=None, repository_id=None,
              classification=DEFAULT_CLASSIFICATION,
              retention_class=DEFAULT_RETENTION_CLASS,
              key_version=DEFAULT_KEY_VERSION,
              generated_dirs=None,
              max_chunk_bytes=DEFAULT_MAX_CHUNK_BYTES,
              tracked_bytes_budget=DEFAULT_TRACKED_BYTES_BUDGET,
              doc_tier_bytes_budget=DEFAULT_DOC_TIER_BYTES_BUDGET,
              structure_tier_bytes_budget=DEFAULT_STRUCTURE_TIER_BYTES_BUDGET,
              structure_max_first_line_bytes=DEFAULT_STRUCTURE_MAX_FIRST_LINE_BYTES,
              max_commits=DEFAULT_MAX_COMMITS,
              flush_every=DEFAULT_FLUSH_EVERY):
    """Index `repo`'s tracked content into `project`'s memory.db from
    zero. Returns the coverage manifest dict. Raises BootstrapError
    (never a partial/lying manifest) if indexed+skipped+failed != tracked,
    if the writer can't be reached, or if state_root can't be written to.

    Idempotent by construction: every call re-derives the full tracked
    set from git and re-attempts every path; the writer (not this
    function) is what makes a repeat call insert nothing new. Resumable:
    if a prior call crashed mid-run at the same HEAD, this call picks up
    from its last-flushed cursor instead of re-reading/re-hashing/
    re-writing everything before it -- a performance optimization on top
    of that idempotency, not a substitute for it.

    `repository_id`, when given, physically scopes memory.db under
    `state_root` via `nightshift_memory.resolve_memory_root` -- see
    `_project_identity`'s docstring. Optional and `None` by default: every
    call that does not pass it keeps writing to the bare `state_root`
    exactly as before (this is what every test in this repo's own suite
    does, each against a private per-test tempdir already dedicated to one
    project); `nightshift-ctl memory bootstrap` always passes it, since it
    requires `--repository-id` on every memory subcommand.
    """
    project_id, root = _project_identity(project, state_root, repository_id)
    put_memory = _load_writer()
    gen_dirs = dict(DEFAULT_GENERATED_DIRS)
    if generated_dirs:
        gen_dirs.update(generated_dirs)

    _require_dir(repo)
    head = _head_sha(repo)
    tracked = _tracked_files(repo)

    # Pass 1: stat every tracked file (cheap -- no bodies read yet) so
    # the budget/degrade decision is made once, up front, for the whole
    # run. PLAN A4a: "say so in the manifest rather than silently
    # truncating" -- an upfront decision is what makes that possible;
    # discovering the budget was blown halfway through would mean some
    # files got full bodies and others didn't, for no principled reason.
    sizes, stat_failed = {}, {}
    for path in tracked:
        full = os.path.join(repo, path)
        try:
            sizes[path] = 0 if os.path.islink(full) else os.path.getsize(full)
        except OSError as exc:
            stat_failed[path] = _errno_reason(exc)
    tracked_bytes = sum(sizes.values())
    # The accurate structure-tier accounting pass (bounded reads, see
    # _structure_first_line_bytes) only runs when actually over budget --
    # both real testbeds today (testbed A 9.2 MB, Nightshift ~8 MB) skip
    # this entirely, so this fix costs them nothing extra.
    structure_stored_sizes = {}
    if tracked_bytes > tracked_bytes_budget:
        structure_candidates = [
            p for p in tracked
            if p in sizes and _is_generated(p, gen_dirs) is None
            and _tier_for_path(p) == "structure"
        ]
        structure_stored_sizes = _structure_first_line_bytes(
            repo, structure_candidates, structure_max_first_line_bytes)
    plan = _plan_budget(
        tracked, sizes, structure_stored_sizes, gen_dirs,
        tracked_bytes_budget=tracked_bytes_budget,
        doc_budget=doc_tier_bytes_budget,
        structure_budget=structure_tier_bytes_budget,
        max_first_line_bytes=structure_max_first_line_bytes,
    )
    degraded = plan.degraded

    # Resume state carries not just a cursor but the accumulated
    # indexed/skipped/failed buckets for everything before it. Without
    # this, a resumed run's manifest would only balance against the
    # SUFFIX it actually re-walked (len(tracked) - cursor), not the full
    # tracked count PLAN A4a requires -- the prefix's outcomes are
    # trusted from the crashed run's own last flush rather than
    # re-derived, which is the entire point of resuming instead of
    # restarting.
    prior = _load_state(root)
    resume_from = 0
    indexed_by_tier = {"doc": 0, "structure": 0}
    skipped, failed, downgraded = [], [], []
    if (prior and prior.get("status") == "in_progress"
            and prior.get("started_at_sha") == head):
        resume_from = min(prior.get("cursor", 0), len(tracked))
        progress = prior.get("progress") or {}
        indexed_by_tier.update(progress.get("indexed_by_tier") or {})
        skipped = list(progress.get("skipped") or [])
        failed = list(progress.get("failed") or [])
        downgraded = list(progress.get("downgraded") or [])

    def _progress_snapshot():
        return {
            "indexed_by_tier": dict(indexed_by_tier),
            "skipped": skipped, "failed": failed, "downgraded": downgraded,
        }

    state = {
        "schema_version": 1, "project": project_id, "status": "in_progress",
        "started_at_sha": head, "cursor": resume_from, "updated_ts": time.time(),
        "progress": _progress_snapshot(),
    }
    _save_state(root, state)

    for idx in range(resume_from, len(tracked)):
        path = tracked[idx]
        if path in stat_failed:
            failed.append({"path": path, "reason": stat_failed[path]})
        else:
            outcome = _index_one_file(
                put_memory, full_path=os.path.join(repo, path), rel_path=path,
                project=project_id, state_root=root, head_sha=head,
                classification=classification, retention_class=retention_class,
                key_version=key_version, max_chunk_bytes=max_chunk_bytes,
                generated_dirs=gen_dirs, plan=plan,
            )
            if outcome.bucket == "skipped":
                skipped.append({"path": path, "reason": outcome.reason})
            elif outcome.bucket == "failed":
                failed.append({"path": path, "reason": outcome.reason})
            else:
                indexed_by_tier[outcome.tier] += 1
                if outcome.downgraded_reason:
                    downgraded.append(
                        {"path": path, "reason": outcome.downgraded_reason})

        if (idx + 1) % flush_every == 0:
            state["cursor"] = idx + 1
            state["updated_ts"] = time.time()
            state["progress"] = _progress_snapshot()
            _save_state(root, state)

    # D2 fix: history is indexed regardless of `degraded` -- the old
    # `not degraded and head is not None` guard silently zeroed tier 3
    # (measured on testbed B: commits_indexed=0 despite 2,000 real commits,
    # with no skip entry, no downgrade record, no warning). `max_commits`
    # bounds memory/time instead (see DEFAULT_MAX_COMMITS), and every
    # outcome -- capped, shallow-clone-limited, or genuinely empty -- is
    # recorded in `history` below, never silently.
    if head is not None:
        history = _index_commit_history(
            put_memory, repo=repo, project=project_id, state_root=root,
            classification=classification, retention_class=retention_class,
            key_version=key_version, since=None, until=head,
            max_commits=max_commits,
        )
    else:
        history = {
            "indexed": 0, "available": 0, "shallow": None,
            "cap": max_commits, "capped": False,
            "newest_sha": None, "oldest_sha": None,
            "not_indexed": None, "write_failed": 0,
            "reason": "repo_has_no_commits",
        }
    commits_indexed = history["indexed"]

    indexed_count = sum(indexed_by_tier.values())
    total = indexed_count + len(skipped) + len(failed)
    manifest = {
        "project": project_id, "repo": repo, "repo_sha": head,
        "tracked": len(tracked),
        "indexed": {"count": indexed_count, "by_tier": indexed_by_tier},
        "skipped": {"count": len(skipped), "items": skipped},
        "failed": {"count": len(failed), "items": failed},
        "downgraded": downgraded,
        # `commits_indexed` kept as a bare int -- existing back-compat
        # surface (tests/test_memory_bootstrap.py and the CLI both read
        # it directly). `history` is the D2 addition: the same count PLUS
        # enough context (available/shallow/cap/capped/reason) that a
        # reader can tell "capped" from "shallow clone" from "no
        # commits" apart, none of which a bare int can express. History
        # rows are deliberately OUTSIDE the tracked/indexed/skipped/failed
        # balance below -- commits are not tracked files, so folding them
        # in would break `indexed + skipped + failed == tracked`.
        "commits_indexed": commits_indexed,
        "history": history,
        "budget": {
            "tracked_bytes": tracked_bytes,
            "threshold_bytes": tracked_bytes_budget,
            "degraded": degraded,
            "doc_budget_bytes": doc_tier_bytes_budget,
            "structure_budget_bytes": structure_tier_bytes_budget,
            "max_first_line_bytes": structure_max_first_line_bytes,
            "doc_shed_count": len(plan.doc_shed),
            "structure_shed_count": len(plan.structure_shed),
            "shed_order": ["structure_body", "doc_body"],
        },
        "balanced": total == len(tracked),
    }

    # PLAN A4a: "indexed + skipped + failed == tracked EXACTLY, or
    # bootstrap FAILS LOUDLY." A mismatch here means this function's own
    # per-file loop above has a gap -- every path takes exactly one of
    # the three branches by construction, so this is a defensive net,
    # not an expected outcome.
    if total != len(tracked):
        raise BootstrapError("coverage_manifest_unbalanced", {
            "tracked": len(tracked), "accounted_for": total,
            "manifest": manifest,
        })

    _save_state(root, {
        "schema_version": 1, "project": project_id, "status": "complete",
        "repo_sha": head, "started_at_sha": head, "cursor": len(tracked),
        "updated_ts": time.time(),
    })
    _save_manifest(root, manifest)
    return manifest


def sync(repo, project, *, state_root=None, repository_id=None,
          classification=DEFAULT_CLASSIFICATION,
          retention_class=DEFAULT_RETENTION_CLASS,
          key_version=DEFAULT_KEY_VERSION,
          generated_dirs=None,
          max_chunk_bytes=DEFAULT_MAX_CHUNK_BYTES,
          tracked_bytes_budget=DEFAULT_TRACKED_BYTES_BUDGET,
          doc_tier_bytes_budget=DEFAULT_DOC_TIER_BYTES_BUDGET,
          structure_tier_bytes_budget=DEFAULT_STRUCTURE_TIER_BYTES_BUDGET,
          structure_max_first_line_bytes=DEFAULT_STRUCTURE_MAX_FIRST_LINE_BYTES,
          max_commits=DEFAULT_MAX_COMMITS):
    """Re-index only what changed since the last completed bootstrap/sync
    watermark. Falls back to a full `bootstrap()` (and says so in the
    returned manifest's "sync_reason") when there is no prior watermark,
    or when history moved in a way a diff can't be trusted for:

        git merge-base --is-ancestor <watermark> HEAD

    False means force-push / rebase / squash -- PLAN A4b: "do not trust
    the delta." A normal day's sync is the changed-paths branch; the
    full re-scan is the rare fallback, not the loop.

    `repository_id`: see `bootstrap()`'s docstring -- identical contract.
    Resolved into `root` once, here, before `common_kwargs` is built below,
    so the full-rescan fallback's own `bootstrap(repo, project,
    **common_kwargs)` call passes the ALREADY-scoped path as `state_root`
    without `repository_id` (avoiding a caller-visible double-resolution).
    """
    project_id, root = _project_identity(project, state_root, repository_id)
    _require_dir(repo)
    head = _head_sha(repo)
    if head is None:
        raise BootstrapError("repo_has_no_commits", {"repo": repo})

    state = _load_state(root)
    watermark = state.get("repo_sha") if state and state.get("status") == "complete" else None

    common_kwargs = dict(
        state_root=root, classification=classification,
        retention_class=retention_class, key_version=key_version,
        generated_dirs=generated_dirs, max_chunk_bytes=max_chunk_bytes,
        tracked_bytes_budget=tracked_bytes_budget,
        doc_tier_bytes_budget=doc_tier_bytes_budget,
        structure_tier_bytes_budget=structure_tier_bytes_budget,
        structure_max_first_line_bytes=structure_max_first_line_bytes,
        max_commits=max_commits,
    )

    if watermark is None:
        manifest = bootstrap(repo, project, **common_kwargs)
        return {**manifest, "sync_mode": "full", "sync_reason": "no_prior_watermark"}

    if watermark == head:
        return {
            "project": project_id, "repo": repo,
            "watermark_from": watermark, "watermark_to": head,
            "sync_mode": "noop", "changed": 0,
            "indexed": {"count": 0, "by_tier": {}},
            "skipped": {"count": 0, "items": []},
            "failed": {"count": 0, "items": []},
            "removed": {"count": 0, "items": []},
            "commits_indexed": 0, "balanced": True,
        }

    if not _is_ancestor(repo, watermark, head):
        manifest = bootstrap(repo, project, **common_kwargs)
        return {**manifest, "sync_mode": "full", "sync_reason": "non_ancestor_watermark"}

    put_memory = _load_writer()
    gen_dirs = dict(DEFAULT_GENERATED_DIRS)
    if generated_dirs:
        gen_dirs.update(generated_dirs)

    changed = _changed_paths_since(repo, watermark, head)
    tracked_now_list = sorted(_tracked_files(repo))
    tracked_now = set(tracked_now_list)

    # D1.5 fix: sync() used to hardcode `degraded=False` unconditionally,
    # a THIRD latent bug distinct from D1's bootstrap()-side flattening --
    # on a repo shaped like testbed B, a degraded bootstrap stores every doc
    # as a tier-2 pointer, and the very next sync would then re-index any
    # CHANGED doc at full tier-1 fidelity, silently making the corpus
    # internally inconsistent (some docs full body, others pointers, with
    # nothing in either manifest saying so). Recomputing the SAME
    # `_plan_budget` here (over the CURRENT tracked set, not the changed
    # subset -- the shed decision is a whole-repo budget, not a per-diff
    # one) keeps bootstrap and sync from ever diverging on what a given
    # path is worth. The extra stat pass this costs is the same one
    # bootstrap() already pays: measured 0.21s over testbed B's 55,235 files,
    # negligible next to a normal day's small `changed` set.
    sizes_now = {}
    for path in tracked_now_list:
        full = os.path.join(repo, path)
        try:
            sizes_now[path] = 0 if os.path.islink(full) else os.path.getsize(full)
        except OSError:
            pass  # not fatal here -- _index_one_file's own exists/open
            # checks catch a genuinely missing/unreadable file per-path;
            # a stat failure only means this path can't be a shed
            # candidate (excluded from `sizes_now`), never that the run
            # aborts.
    # Same accurate-structure-accounting rule as bootstrap(): the bounded
    # first-line reads only happen when actually over budget, so a normal
    # sync (both testbeds today) pays nothing extra beyond the stat pass
    # above. See _structure_first_line_bytes's docstring for why raw
    # disk size is the wrong unit for a tier that only ever stores a
    # first-line pointer.
    structure_stored_sizes_now = {}
    if sum(sizes_now.values()) > tracked_bytes_budget:
        structure_candidates_now = [
            p for p in tracked_now_list
            if p in sizes_now and _is_generated(p, gen_dirs) is None
            and _tier_for_path(p) == "structure"
        ]
        structure_stored_sizes_now = _structure_first_line_bytes(
            repo, structure_candidates_now, structure_max_first_line_bytes)
    plan = _plan_budget(
        tracked_now_list, sizes_now, structure_stored_sizes_now, gen_dirs,
        tracked_bytes_budget=tracked_bytes_budget,
        doc_budget=doc_tier_bytes_budget,
        structure_budget=structure_tier_bytes_budget,
        max_first_line_bytes=structure_max_first_line_bytes,
    )

    indexed_by_tier = {"doc": 0, "structure": 0}
    skipped, failed, removed, downgraded = [], [], [], []

    for path in changed:
        if path not in tracked_now:
            # Deleted (or renamed away) since the watermark. Nothing new
            # to write; the surviving old row becomes "orphaned" the
            # next time retrieval re-hashes it (A4b) -- that is a
            # read-time concern, not sync's.
            removed.append({"path": path})
            continue
        outcome = _index_one_file(
            put_memory, full_path=os.path.join(repo, path), rel_path=path,
            project=project_id, state_root=root, head_sha=head,
            classification=classification, retention_class=retention_class,
            key_version=key_version, max_chunk_bytes=max_chunk_bytes,
            generated_dirs=gen_dirs, plan=plan,
        )
        if outcome.bucket == "skipped":
            skipped.append({"path": path, "reason": outcome.reason})
        elif outcome.bucket == "failed":
            failed.append({"path": path, "reason": outcome.reason})
        else:
            indexed_by_tier[outcome.tier] += 1
            if outcome.downgraded_reason:
                # D1.5 fix: sync() now shares bootstrap()'s real `plan`
                # (see above), so a changed doc CAN be shed to a
                # tier-2 pointer here too -- record it the same way
                # bootstrap() does, or "no silent caps" would hold for
                # a fresh bootstrap but silently stop holding the
                # moment the SAME path was merely touched by a sync.
                downgraded.append(
                    {"path": path, "reason": outcome.downgraded_reason})

    # D2 fix: same cap as bootstrap() (see DEFAULT_MAX_COMMITS) -- a
    # normal `watermark..head` delta is a handful of commits, but after a
    # long outage or a long-lived-branch merge it can be unbounded, the
    # same OOM shape `_git_log_records`'s module comment measures for the
    # full-history walk.
    history = _index_commit_history(
        put_memory, repo=repo, project=project_id, state_root=root,
        classification=classification, retention_class=retention_class,
        key_version=key_version, since=watermark, until=head,
        max_commits=max_commits,
    )
    commits_indexed = history["indexed"]

    indexed_count = sum(indexed_by_tier.values())
    total = indexed_count + len(skipped) + len(failed) + len(removed)
    manifest = {
        "project": project_id, "repo": repo,
        "watermark_from": watermark, "watermark_to": head,
        "sync_mode": "incremental", "changed": len(changed),
        "indexed": {"count": indexed_count, "by_tier": indexed_by_tier},
        "skipped": {"count": len(skipped), "items": skipped},
        "failed": {"count": len(failed), "items": failed},
        "removed": {"count": len(removed), "items": removed},
        "downgraded": downgraded,
        # See bootstrap()'s manifest for why `commits_indexed` stays a
        # bare int (back-compat) alongside the richer `history` dict, and
        # why both stay outside the changed/indexed/skipped/failed/
        # removed balance below.
        "commits_indexed": commits_indexed,
        "history": history,
        "budget": {
            "tracked_bytes": plan.tracked_bytes,
            "threshold_bytes": tracked_bytes_budget,
            "degraded": plan.degraded,
            "doc_budget_bytes": doc_tier_bytes_budget,
            "structure_budget_bytes": structure_tier_bytes_budget,
            "max_first_line_bytes": structure_max_first_line_bytes,
            "doc_shed_count": len(plan.doc_shed),
            "structure_shed_count": len(plan.structure_shed),
            "shed_order": ["structure_body", "doc_body"],
        },
        "balanced": total == len(changed),
    }
    if total != len(changed):
        raise BootstrapError("sync_manifest_unbalanced", {
            "changed": len(changed), "accounted_for": total,
            "manifest": manifest,
        })

    _save_state(root, {
        "schema_version": 1, "project": project_id, "status": "complete",
        "repo_sha": head, "started_at_sha": head, "cursor": 0,
        "updated_ts": time.time(),
    })
    _save_manifest(root, manifest)
    return manifest


# --------------------------------------------------------------------------
# CLI-shaped entrypoints -- `command(args) -> dict`, matching the boundary
# `bin/nightshift-ctl`'s `_run_memory_command` calls (same shape as
# `nightshift_memory.py`'s `put_memory`/`purge_memory` and
# `nightshift_memory_search.py`'s `search_memory`/`verify_memory`).
# `bootstrap()`/`sync()` above stay the connection-free, args-free library
# API; these three are thin adapters onto them plus one read-back.
# --------------------------------------------------------------------------

def bootstrap_memory(args):
    """Required: args.state_root, args.repository_id. Optional: args.repo
    (default: the current working directory -- the local checkout to read
    tracked content from, distinct from --state-root, which must stay
    outside every worktree), args.project (default: `str(args.
    repository_id)` -- `nightshift-ctl memory bootstrap` has no separate
    `--project` flag, since repository_id is already the real partition
    key PLAN-400 keys memory.db off; see `resolve_memory_root`).
    """
    repo = getattr(args, "repo", None) or os.getcwd()
    project = getattr(args, "project", None) or str(args.repository_id)
    return bootstrap(repo, project, state_root=args.state_root,
                      repository_id=args.repository_id)


def sync_memory(args):
    """Same argument contract as `bootstrap_memory` above, for
    `nightshift-ctl memory sync`."""
    repo = getattr(args, "repo", None) or os.getcwd()
    project = getattr(args, "project", None) or str(args.repository_id)
    return sync(repo, project, state_root=args.state_root,
                repository_id=args.repository_id)


def coverage_memory(args):
    """Read back the most recent bootstrap/sync coverage manifest (the
    `_save_manifest` sidecar file) for `nightshift-ctl memory coverage`.
    Required: args.state_root, args.repository_id. Never runs a fresh
    bootstrap/sync itself -- this is "read back what the last run already
    proved", not a new indexing pass.
    """
    import nightshift_memory  # lazy, same as _load_writer / _project_identity
    root = nightshift_memory.resolve_memory_root(
        args.state_root, args.repository_id)
    path = os.path.join(root, MANIFEST_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError as exc:
        raise BootstrapError("memory_coverage_not_found", {
            "state_root": args.state_root,
            "repository_id": args.repository_id,
        }) from exc
    except (OSError, ValueError) as exc:
        raise BootstrapError("memory_coverage_unreadable", {
            "error": str(exc),
        }) from exc
    return {"ok": True, "manifest": manifest}
