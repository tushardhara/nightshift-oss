#!/usr/bin/env python3
"""Customer Publication Gateway -- provenance, footprint, tenancy, dirty
checkout (issue #400, ADR-0004, ADR-0005, ADR-0006).

THIS MODULE'S BOUNDARY. Four of the eight PG-* acceptance items the
Publication Gateway epic decomposes into:

  PG-4 zero-footprint proof   -- capture_footprint()/compare_footprint(),
                                  the sole read-only exec choke point
                                  customer_git(), and the self-proving
                                  instrument that refuses its own evidence
                                  when its own commands perturbed the repo.
  PG-6 cross-tenant fail-closed -- resolve_publication_root() (physical
                                  per-tenant-per-repository directory
                                  separation, the same idiom
                                  nightshift_memory.resolve_memory_root
                                  already established for per-project
                                  isolation) plus query-level tenant/
                                  repository filters as defense in depth.
  PG-7 provenance + lineage  -- the provenance.db schema (source_snapshot,
                                  closure_entry, lineage_node, derived_from),
                                  repository_fingerprint(), and the
                                  source_not_retained transition.
  PG-8 dirty checkout        -- external mirror acquisition (fetch, never
                                  hardlink-clone), an external workspace
                                  copy of the dirty tree, and a marked
                                  synthetic commit -- the customer checkout
                                  and its .git are never touched.

NOT BUILT HERE (a separate, dependent task -- see
bin/nightshift_publication.py):
  PG-1 stage->classify->review->authorize->apply pipeline
  PG-2 the one-use 7-tuple approval CAS and its five invalidation tests
  PG-3 self-review refusal
  PG-5 no-generated-file mode
This module supplies the primitives those items need to reuse (the
tenant-in-the-WHERE-clause CAS shape in mark_source_not_retained(), the
default-off wiring implied by customer_git()'s allowlist, and the receipt-
shaped fields lineage_chain() checks), but does not implement the approval
ledger itself. A cross-tenant test for the *approval* operation therefore
lives with that module, not this one.

THE SEAM WITH nightshift_publication.py (issue #694). That module IMPORTS
this one and delegates to it; the dependency runs in exactly one direction
and this module must never import it back.
  - `validate_tenant_id` / `validate_repository_id` here are the SINGLE
    validator for both modules. Publication re-raises the refusal as its
    own error type with the identical reason_code, so an id rejected here
    is rejected there by construction rather than by two copies staying in
    sync.
  - `resolve_publication_root` here is the SINGLE physical layout
    authority. publication.db is a sibling of provenance.db inside
    `<state_root>/tenants/<tenant_id>/repos/<repository_id>/`.
  - Publication ATTACHes provenance.db READ-ONLY (`mode=ro`) and reads
    `source_snapshot` / `closure_entry` / `derived_from` / `lineage_node`
    through it. It NEVER writes this database, which is why no transaction
    ever has to span the two files -- SQLite provides no atomic
    multi-database commit under `journal_mode=WAL`, which both databases
    use. If a future change adds a publication -> provenance write, that
    assumption must be re-examined, not inherited.
  - `verify_closure_complete` is called through that read-only attachment
    with schema="prov"; publication never computes closure itself.

ALSO NOT BUILT HERE, stated plainly (matches the architecture note this
module was commissioned from):
  - Tenant encryption at rest beyond recording `key_version` on rows.
    Bodies are plaintext on disk in this module.
  - Migration authority epochs (ADR-0007).
  - The managed-production PostgreSQL profile (#400 item 7 parity).

EVIDENCE BASE. Every behavioral claim in this module's comments that says
"[VERIFIED]" was measured by actually running git 2.33.0 / Python 3.14.7 on
macOS arm64 against throwaway fixtures and, read-only, against the sealed
testbed A/testbed B testbeds, by the architecture pass that designed this
module. The eight load-bearing findings, condensed:

  E1 Plain `git status --porcelain` REWRITES `.git/index` (content AND
     mtime) when the stat-cache is stale. `--no-optional-locks` (which
     customer_git() injects unconditionally) makes it byte- and
     mtime-clean. This is why a naive footprint harness is itself a
     violation of the property it is supposed to prove.
  E2 `git clone --bare <customer>` (the default local transport)
     HARDLINKS the customer's loose objects -- st_nlink changes, bytes and
     mtimes do not. A content-hash-only manifest misses this, which is why
     every file entry below also records st_nlink and why acquire_mirror()
     uses `fetch` into a pre-created bare mirror, never `clone --bare`.
  E3 `git fetch <customer> '+refs/heads/*:refs/heads/*'` run FROM a
     pre-created external bare mirror leaves the customer .git identical
     on content, mtime and nlink. This is the acquisition primitive.
  E5 Positive controls: `git stash push -u`, `git worktree add` and
     `git add -A` each mutate exactly the .git categories item 1 names.
     These are the tests that prove the harness can actually fail.
  E6 `git status` alone is defeated three ways: an ignore-file edit
     (`.git/info/exclude`) that plain `status --porcelain` shows as empty
     but `-uall --ignored=matching` catches; `core.excludesFile` pointing
     outside the repo, which `-uall` alone still misses but
     `--ignored=matching` catches; and `status.showUntrackedFiles=no`
     making a bare `--ignored` invocation exit non-zero (a probe failure,
     never silently "no changes"). Hence: `-uall` is always explicit on
     argv, never inherited from config, and a filesystem walk -- not git's
     view -- is the proof of record.
  E7 A full sha256 of an entire .git directory is *cheap* even at scale
     (0.14s-0.32s on the 311MB/653MB sealed testbeds) because .git is
     packed. There is no excuse to sample it. The worktree is the
     expensive half (content-hashing 71k files took 5.4s on testbed B), so
     tiering (tier="A" vs tier="B") applies only there.
  E8 Synthetic commits with fixed author/committer identity and fixed
     dates are deterministic: two independent runs over the same dirty
     tree produce the identical commit OID and patch digest.

stdlib only. No third-party deps, no network calls at runtime (mirror
acquisition talks to a local filesystem path, never a remote URL).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time


PROVENANCE_DB_FILENAME = "provenance.db"
MAX_ID = (1 << 63) - 1

# ---------------------------------------------------------------------------
# Error shape -- mirrors nightshift_memory.MemoryStoreError /
# nightshift_project.ProjectError exactly, so a future CLI wiring can reuse
# the same `_run_*_command` boundary idiom already established there.
# ---------------------------------------------------------------------------


class ProvenanceError(Exception):
    def __init__(self, reason_code, exit_code=1, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code
        self.details = dict(details or {})


def error_payload(error):
    return {"ok": False, "reason_code": error.reason_code, **error.details}


def _refuse(reason_code, details=None):
    raise ProvenanceError(reason_code, 3, details)


def _probe_failed(reason_code, details=None):
    raise ProvenanceError(reason_code, 1, details)


# ===========================================================================
# PG-6 -- tenancy: physical separation first, query filters as defense in
# depth (ADR-0005: cross-tenant dedupe is forbidden; #400 item 9).
# ===========================================================================


def _validate_scope_id(value, reason_code):
    """Same strictness nightshift_memory.resolve_memory_root already
    applies to repository_id, applied identically to tenant_id: reject a
    bool (Python's bool is an int subclass -- `True == 1` would silently
    scope a tenant to id 1), a non-int, a non-positive value, or one bigger
    than any real id could be. Refused BEFORE it can reach a path join --
    an unvalidated id reaching os.path.join is the textbook path-traversal
    opening a per-tenant directory tree exists to prevent, and a value like
    `"../other_tenant"` never survives an isinstance(value, int) check to
    begin with.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > MAX_ID:
        _refuse(reason_code)
    return value


def validate_tenant_id(tenant_id):
    return _validate_scope_id(tenant_id, "invalid_tenant_id")


def validate_repository_id(repository_id):
    return _validate_scope_id(repository_id, "invalid_repository_id")


def _validate_abs_dir_arg(path, reason_code):
    if (not isinstance(path, str) or not path
            or len(path.encode("utf-8")) > 4096
            or not path.startswith("/")
            or path != os.path.normpath(path)
            or path.startswith("//")):
        _refuse(reason_code)
    return path


def resolve_publication_root(state_root, tenant_id, repository_id):
    """Compute (and create, mode 0700) the physically tenant-and-repository
    scoped directory `<state_root>/tenants/<tenant_id>/repos/<repository_id>/`
    that provenance.db (and, from the sibling module that owns it,
    publication.db) live inside.

    This is the SAME mechanism nightshift_memory.resolve_memory_root uses
    for per-project isolation, extended with one more path segment: tenant
    A's rows live in a FILE tenant B's resolver cannot name, so cross-
    tenant leakage is unrepresentable, not merely filtered (PLAN-400's own
    argument for `memory/<repository_id>/`, repeated here one level up).
    `tenants/` is a new sibling directory next to the existing `memory/`
    tree -- this function must never move, rename or re-key anything
    already living under `<state_root>/memory/<repository_id>/`.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    _validate_abs_dir_arg(state_root, "invalid_state_root")
    tenant_dir = os.path.join(state_root, "tenants", str(tenant_id))
    root = os.path.join(tenant_dir, "repos", str(repository_id))
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        # os.makedirs' mode argument is subject to umask; force every
        # directory on the new path down to the exact mode, including the
        # tenant-level directory itself, so `ls` from a sibling tenant's
        # process cannot even enumerate this tenant's repository ids.
        os.chmod(root, 0o700)
        os.chmod(os.path.join(tenant_dir, "repos"), 0o700)
        os.chmod(tenant_dir, 0o700)
        tenants_dir = os.path.join(state_root, "tenants")
        if os.path.isdir(tenants_dir):
            os.chmod(tenants_dir, 0o700)
    except OSError as exc:
        raise ProvenanceError("state_root_unwritable", 3, {
            "state_root": state_root, "error": str(exc)}) from exc
    return root


def _validate_scoped_root(path):
    """Same final-directory guarantee nightshift_memory._validate_state_root
    enforces (absolute, normalized, no symlink hop at the final component,
    owned by this process, mode exactly 0700) -- re-implemented locally
    rather than imported, so this module stays independently testable and
    an accidental edit to nightshift_memory.py cannot silently change what
    provenance.db is allowed to sit inside.
    """
    _validate_abs_dir_arg(path, "invalid_state_root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ProvenanceError("invalid_state_root", 3) from exc
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        _refuse("invalid_state_root")


# ===========================================================================
# PG-4 -- the sole read-only exec choke point.
# ===========================================================================

# Global env every customer_git() invocation runs with -- [VERIFIED]
# non-mutating on content/mtime/nlink under this exact combination.
_READ_ONLY_ENV_OVERRIDES = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "true",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
}


def _sanitized_git_env():
    env = {"GIT_PAGER": "cat", "PAGER": "cat"}
    for key in ("PATH", "HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(_READ_ONLY_ENV_OVERRIDES)
    return env


# Subcommands whose entire surface is read-only regardless of further
# flags. `log`/`diff` are included because a future caller (the reviewed-
# patch adapter, or advisory patch-id equivalence per ADR-0004) needs a
# read-only route to them through this same choke point; capture_footprint
# below does not itself call them.
_SIMPLE_ALLOWLIST = frozenset({
    "rev-parse", "status", "for-each-ref", "show-ref", "symbolic-ref",
    "ls-files", "ls-tree", "cat-file", "check-attr", "log", "diff",
    "count-objects", "var", "ls-remote", "rev-list",
})

# Subcommands that have a write-capable surface under the SAME first
# token, so the guard must also inspect the second token, not just argv[0]
# (the "denylist-shaped problem in disguise" risk this design flags:
# `git worktree add` and `git worktree list` share argv[0]).
_MULTI_WORD_ALLOWLIST = {
    "worktree": frozenset({"list"}),
    "submodule": frozenset({"status"}),
    "stash": frozenset({"list"}),
    "config": frozenset({"--get", "--list"}),
}

# Any argument matching one of these prefixes can smuggle write behaviour
# or an escape from the registered customer path past a subcommand-name
# check (`git -c core.hooksPath=... status`, `git --git-dir=/elsewhere
# status`) -- refused unconditionally, on every subcommand, regardless of
# allowlist membership.
_FORBIDDEN_ARG_PREFIXES = (
    "-c", "--git-dir", "--work-tree", "--exec-path", "--namespace",
    "--config", "--config-env", "--super-prefix", "-C", "--bare",
    "--literal-pathspecs",
)


def _guard_customer_git_args(args):
    if not isinstance(args, (list, tuple)) or not args or not all(
            isinstance(a, str) for a in args):
        _refuse("customer_repo_write_refused", {"reason": "malformed argv"})
    for arg in args:
        if any(arg == p or arg.startswith(p) for p in _FORBIDDEN_ARG_PREFIXES):
            _refuse("customer_repo_write_refused", {
                "reason": "forbidden flag", "flag": arg})
    cmd = args[0]
    if cmd in _MULTI_WORD_ALLOWLIST:
        second = args[1] if len(args) > 1 else None
        if second not in _MULTI_WORD_ALLOWLIST[cmd]:
            _refuse("customer_repo_write_refused", {
                "reason": "subcommand not allowlisted", "argv": list(args)})
        return
    if cmd not in _SIMPLE_ALLOWLIST:
        _refuse("customer_repo_write_refused", {
            "reason": "subcommand not allowlisted", "argv": list(args)})


def customer_git(customer_path, args, *, timeout=30):
    """The ONLY way any code in this module runs git against a registered
    customer path. Refuses any subcommand outside the read-only allowlist
    and any flag that could redirect --git-dir/--work-tree or inject write-
    affecting config (customer_repo_write_refused); always runs with
    `--no-optional-locks` and the read-only env above; always uses `cwd=`,
    never `-C`/`--git-dir`, so a caller cannot smuggle a different target
    repository past this function even by accident.

    Returns the completed subprocess.CompletedProcess. Callers decide how
    to treat a non-zero exit (a footprint probe treats it as a hard
    failure; other callers, e.g. an empty-repository `rev-list`, may treat
    a specific known-benign non-zero exit as a legitimate answer -- see
    collect_git_views()'s `symbolic-ref -q` handling for the one case this
    module carves out, [VERIFIED] a normal detached-HEAD/unborn-branch
    signal, not an error).

    known limit (not solved here): a subcommand like `log`/`diff` can
    still invoke a textconv/pager/diff.external the CUSTOMER'S OWN repo
    config names, since we do not strip the customer repo's own on-disk
    config (only forbid *our* callers from injecting `-c`/`--config`). That
    is the customer's own configuration risk domain, not an escape past
    this guard into a Nightshift-controlled write.

    acquire_mirror() below is this guard's one deliberate exception: it
    never calls this function, and is the only place a `git` subprocess
    treats a customer path as a fetch SOURCE rather than a `cwd`.
    """
    _validate_abs_dir_arg(customer_path, "invalid_customer_path")
    _guard_customer_git_args(args)
    full = ["git", "--no-optional-locks", *args]
    try:
        return subprocess.run(
            full, cwd=customer_path, env=_sanitized_git_env(),
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProvenanceError("footprint_probe_failed", 1, {
            "reason": "timeout", "argv": list(args)}) from exc
    except OSError as exc:
        raise ProvenanceError("footprint_probe_failed", 1, {
            "reason": str(exc), "argv": list(args)}) from exc


def resolve_customer_roots(customer_path):
    """Resolve GITDIR and COMMON dir FIRST, never hardcode `<repo>/.git` --
    a customer checkout may be a linked worktree (whose `.git` is a FILE:
    `gitdir: <path>/.git/worktrees/<name>`, [VERIFIED]) or a submodule
    (same file-pointer shape, into `<super>/.git/modules/<path>`,
    [VERIFIED]). Walking COMMON (not just GITDIR) automatically covers
    every submodule object store for free, because `objects/modules/<path>`
    lives inside it.
    """
    _validate_abs_dir_arg(customer_path, "invalid_customer_path")
    gd = customer_git(customer_path, ["rev-parse", "--absolute-git-dir"])
    if gd.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "rev-parse --absolute-git-dir",
            "returncode": gd.returncode, "stderr": gd.stderr[:500]})
    common = customer_git(customer_path, [
        "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "rev-parse --git-common-dir",
            "returncode": common.returncode, "stderr": common.stderr[:500]})
    bare = customer_git(customer_path, ["rev-parse", "--is-bare-repository"])
    is_bare = bare.returncode == 0 and bare.stdout.strip() == "true"
    worktree_root = None
    if not is_bare:
        top = customer_git(customer_path, ["rev-parse", "--show-toplevel"])
        if top.returncode != 0:
            _probe_failed("footprint_probe_failed", {
                "command": "rev-parse --show-toplevel",
                "returncode": top.returncode, "stderr": top.stderr[:500]})
        worktree_root = os.path.realpath(top.stdout.strip())
    return {
        "gitdir": os.path.realpath(gd.stdout.strip()),
        "common_dir": os.path.realpath(common.stdout.strip()),
        "worktree_root": worktree_root,
        "is_bare": is_bare,
    }


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_manifest(root, *, hash_files=True, exclude_realpaths=None):
    """The proof of record: a filesystem manifest, not a git view
    (git status alone is defeated three independent ways -- E6). Full
    sha256 for every file by default -- [VERIFIED] free even on a 55k-file
    .git (0.14s on the testbed B testbed) because .git is packed, so there is
    no excuse to sample it; `hash_files=False` is only ever used for the
    (expensive, unpacked) worktree half under tier "A".

    st_nlink is mandatory on every file entry -- [VERIFIED] `git clone
    --bare` changes st_nlink on customer loose objects while leaving bytes
    and mtimes untouched (E2); a manifest that omitted it would silently
    pass that violation.

    `exclude_realpaths` prunes matching directories from the walk (used to
    keep the worktree walk from re-walking the already-separately-walked
    .git directory it contains). Deterministic: os.walk(followlinks=False)
    with dirnames/filenames sorted before descent/reporting.
    """
    root = os.path.realpath(root)
    exclude = {os.path.realpath(p) for p in (exclude_realpaths or ())}
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        pruned = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if os.path.realpath(full) in exclude:
                continue
            pruned.append(d)
        dirnames[:] = pruned
        for d in dirnames:
            full = os.path.join(dirpath, d)
            try:
                st = os.lstat(full)
            except OSError as exc:
                _probe_failed("footprint_probe_failed", {
                    "reason": str(exc), "path": full})
            rel = os.path.relpath(full, root).replace(os.sep, "/") + "/"
            entries.append({
                "path": rel, "type": "dir", "mode": oct(stat.S_IMODE(st.st_mode))})
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
            except OSError as exc:
                _probe_failed("footprint_probe_failed", {
                    "reason": str(exc), "path": full})
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if stat.S_ISLNK(st.st_mode):
                entries.append({
                    "path": rel, "type": "link",
                    "mode": oct(stat.S_IMODE(st.st_mode)),
                    "target": os.readlink(full),
                    "mtime_ns": st.st_mtime_ns})
            else:
                entry = {
                    "path": rel, "type": "file",
                    "mode": oct(stat.S_IMODE(st.st_mode)),
                    "size": st.st_size, "nlink": st.st_nlink,
                    "mtime_ns": st.st_mtime_ns}
                if hash_files:
                    entry["sha256"] = _sha256_file(full)
                entries.append(entry)
    entries.sort(key=lambda e: e["path"])
    return entries


def _strip_mtime(entries):
    return [{k: v for k, v in e.items() if k != "mtime_ns"} for e in entries]


def _benign_touch(before, after):
    """Entries identical on every field except mtime_ns -- REPORT severity
    (1.6): a same-bytes rewrite is still evidence and belongs in the
    receipt, but asserting on it in an acceptance test invites exactly the
    flake that gets the whole harness disabled.
    """
    before_by_path = {e["path"]: e for e in before}
    after_by_path = {e["path"]: e for e in after}
    touched = []
    for path, b in before_by_path.items():
        a = after_by_path.get(path)
        if a is None:
            continue
        if _strip_mtime([b]) == _strip_mtime([a]) and b.get("mtime_ns") != a.get("mtime_ns"):
            touched.append(path)
    return sorted(touched)


# Corroborating git views (1.3) -- every one [VERIFIED] non-mutating on
# content/mtime/nlink under the env customer_git() injects. `-uall` is
# EXPLICIT on argv (never inherited from repo config -- E6c: with
# status.showUntrackedFiles=no set, a bare `--ignored` invocation exits
# non-zero with "fatal: Unsupported combination of ignored and
# untracked-files arguments"; with -uall explicit it correctly reports the
# untracked file regardless of that config).
_GIT_VIEW_COMMANDS = {
    "status": ["status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],
    "head": ["rev-parse", "HEAD"],
    "symref": ["symbolic-ref", "-q", "HEAD"],
    "refs": ["for-each-ref", "--format=%(objectname) %(objecttype) %(refname)"],
    # the honest stand-in for `git ls-refs` -- ls-refs is a protocol-v2
    # SERVER command with no standalone client invocation; `ls-remote
    # --symref .` spawns upload-pack against the customer repo itself and
    # is [VERIFIED] non-mutating on all three axes.
    "advertised": ["ls-remote", "--symref", "."],
    "index": ["ls-files", "-s", "-z"],
    "stash": ["stash", "list"],
    "worktrees": ["worktree", "list", "--porcelain"],
    "config": ["config", "--list", "--show-origin"],
    "objects": ["count-objects", "-v"],
    "objformat": ["rev-parse", "--show-object-format"],
    "submodule_tree": ["ls-tree", "-r", "-t", "HEAD"],
    "submodule_status": ["submodule", "status"],
}


def collect_git_views(customer_path):
    """Run every corroborating view in `_GIT_VIEW_COMMANDS` and return
    {name: stdout}. A non-zero exit is a footprint_probe_failed for every
    view except `symref` on a detached HEAD or unborn branch, where `git
    symbolic-ref -q HEAD` exiting 1 with empty stdout is [VERIFIED] the
    normal "no symbolic ref" signal, not an error -- treating it as a probe
    failure would make every detached-HEAD customer checkout unusable.
    `submodule_tree`/`head` on a genuinely empty repository (`HEAD` does
    not resolve to a commit yet) legitimately fail too; callers that need
    to tolerate a brand-new empty repository should check
    `roots`/`rev-parse --is-bare-repository` first, this function does not
    special-case it -- an empty repo is rare enough in a "customer
    repository" context that silently swallowing the error would hide a
    genuine misconfiguration more often than it would help.
    """
    views = {}
    for name, args in _GIT_VIEW_COMMANDS.items():
        proc = customer_git(customer_path, args)
        if proc.returncode != 0:
            if name == "symref" and proc.returncode == 1:
                views[name] = ""
                continue
            _probe_failed("footprint_probe_failed", {
                "command": name, "argv": args,
                "returncode": proc.returncode, "stderr": proc.stderr[:1000]})
        views[name] = proc.stdout
    return views


def capture_footprint(customer_path, *, tier="A"):
    """Self-proving footprint capture (1.5): fs_walk -> git_views ->
    fs_walk_again over GITDIR/COMMON, bracketing the git-views collection.
    If the capture's OWN commands perturbed .git (content/mode/size/nlink,
    ignoring mtime -- see _benign_touch), the capture is refused as
    invalid (footprint_capture_invalid) rather than reported as a
    footprint violation of whatever operation the caller was trying to
    measure -- this is the mechanism that would have caught E1
    automatically had it not already been fixed by `--no-optional-locks`.

    tier="A" (the default, cheap enough to run before AND after every
    gateway operation): GITDIR+COMMON always full-hash (E7: ~0.14s on a
    311MB .git), worktree STAT-ONLY (no content hash). tier="B" (the
    acceptance proof / `publication footprint` verb): tier A plus a
    worktree CONTENT-hash pass. The .git proof never downgrades between
    tiers -- item 1 names .git explicitly and it is cheap; only the
    (expensive, unpacked) worktree half is tiered.
    """
    if tier not in ("A", "B"):
        _refuse("invalid_footprint_tier", {"tier": tier})
    roots = resolve_customer_roots(customer_path)
    exclude = {roots["gitdir"]}
    if roots["common_dir"] != roots["gitdir"]:
        exclude.add(roots["common_dir"])

    gitdir_before = walk_manifest(roots["gitdir"], hash_files=True)
    common_before = (walk_manifest(roots["common_dir"], hash_files=True)
                      if roots["common_dir"] != roots["gitdir"] else None)

    views = collect_git_views(customer_path)

    gitdir_after = walk_manifest(roots["gitdir"], hash_files=True)
    common_after = (walk_manifest(roots["common_dir"], hash_files=True)
                     if roots["common_dir"] != roots["gitdir"] else None)

    self_proof_clean = _strip_mtime(gitdir_before) == _strip_mtime(gitdir_after)
    benign = list(_benign_touch(gitdir_before, gitdir_after))
    if common_before is not None:
        self_proof_clean = self_proof_clean and (
            _strip_mtime(common_before) == _strip_mtime(common_after))
        benign += _benign_touch(common_before, common_after)

    if not self_proof_clean:
        raise ProvenanceError("footprint_capture_invalid", 1, {
            "reason": "capture's own git commands perturbed the repository"})

    worktree_manifest = None
    if roots["worktree_root"] is not None:
        worktree_manifest = walk_manifest(
            roots["worktree_root"], hash_files=(tier == "B"),
            exclude_realpaths=exclude)

    return {
        "captured_at": time.time(),
        "tier": tier,
        "roots": roots,
        "worktree_manifest": worktree_manifest,
        "gitdir_manifest": gitdir_after,
        "common_manifest": common_after,
        "git_views": views,
        "self_proof_clean": True,
        "self_proof_benign_touch": sorted(set(benign)),
    }


def compare_footprint(before, after):
    """Three severities (1.6), never two: FAIL on any add/remove/change of
    content, mode, size or st_nlink in any manifest, or any change in a
    git view's output; REPORT (never FAIL) an mtime-only change on an
    otherwise byte-identical entry. Returns
    {"verdict": "clean"|"violation", "violations": [...], "benign_touch": [...]}.
    """
    violations = []
    benign = []

    def _diff_manifest(name, m1, m2):
        m1 = m1 or []
        m2 = m2 or []
        by_path_1 = {e["path"]: e for e in m1}
        by_path_2 = {e["path"]: e for e in m2}
        for path in sorted(set(by_path_1) | set(by_path_2)):
            e1 = by_path_1.get(path)
            e2 = by_path_2.get(path)
            if e1 is None:
                violations.append({"manifest": name, "path": path, "kind": "added"})
                continue
            if e2 is None:
                violations.append({"manifest": name, "path": path, "kind": "removed"})
                continue
            e1c, e2c = _strip_mtime([e1])[0], _strip_mtime([e2])[0]
            if e1c != e2c:
                violations.append({
                    "manifest": name, "path": path, "kind": "changed",
                    "before": e1c, "after": e2c})
            elif e1.get("mtime_ns") != e2.get("mtime_ns"):
                benign.append({"manifest": name, "path": path})

    _diff_manifest("worktree", before.get("worktree_manifest"), after.get("worktree_manifest"))
    _diff_manifest("gitdir", before.get("gitdir_manifest"), after.get("gitdir_manifest"))
    _diff_manifest("common", before.get("common_manifest"), after.get("common_manifest"))

    views_before = before.get("git_views") or {}
    views_after = after.get("git_views") or {}
    for name in sorted(set(views_before) | set(views_after)):
        if views_before.get(name) != views_after.get(name):
            violations.append({
                "manifest": "git_view", "path": name, "kind": "changed",
                "before": views_before.get(name), "after": views_after.get(name)})

    return {
        "verdict": "violation" if violations else "clean",
        "violations": violations,
        "benign_touch": benign,
    }


# ===========================================================================
# PG-7 -- provenance, closure and lineage.
# ===========================================================================

PROVENANCE_SCHEMA = """
PRAGMA journal_mode=WAL;

-- ORDERING OF DEFENCES, stated so a reviewer cannot mistake the backstop
-- for the primary check: `validate_tenant_id` (above) is the PRIMARY
-- validator -- it runs in Python, before the id can reach os.path.join, and
-- is what makes "../other_tenant" unrepresentable as a directory name. The
-- CHECK below is a SECOND line only, for a code path that forgot to call
-- it: declaring a column INTEGER does NOT make SQLite store integers (an
-- INTEGER NOT NULL column accepts '../other_tenant' and stores it as text),
-- so typeof() is the only column-level way to say what this column means.
-- It is deliberately NOT a replacement: it coerces the string '7' to
-- integer 7 and accepts it, which validate_tenant_id does not.
CREATE TABLE IF NOT EXISTS source_snapshot(
  snapshot_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  repository_fingerprint TEXT NOT NULL,
  object_format TEXT NOT NULL CHECK(object_format IN ('sha1','sha256')),
  commit_oid TEXT NOT NULL,
  tree_oid TEXT NOT NULL,
  observed_ref TEXT,
  retention_ref TEXT,
  pack_digest TEXT,
  closure_manifest_digest TEXT,
  captured_at REAL NOT NULL,
  synthetic INTEGER NOT NULL DEFAULT 0 CHECK(synthetic IN (0,1)),
  synthetic_parent_commit_oid TEXT,
  patch_digest TEXT,
  dirty_capture_method TEXT,
  worktree_file_manifest_digest TEXT,
  included_ignored INTEGER NOT NULL DEFAULT 0 CHECK(included_ignored IN (0,1)),
  retention_state TEXT NOT NULL DEFAULT 'retained' CHECK(retention_state IN ('retained','expired')),
  source_not_retained INTEGER NOT NULL DEFAULT 0 CHECK(source_not_retained IN (0,1)),
  source_not_retained_at REAL,
  source_not_retained_reason TEXT,
  key_version INTEGER,
  CHECK ((synthetic=0 AND synthetic_parent_commit_oid IS NULL AND dirty_capture_method IS NULL)
      OR (synthetic=1 AND synthetic_parent_commit_oid IS NOT NULL AND dirty_capture_method IS NOT NULL)));
CREATE INDEX IF NOT EXISTS source_snapshot_scope ON source_snapshot(tenant_id, repository_id);

CREATE TABLE IF NOT EXISTS closure_entry(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('submodule','lfs')),
  path TEXT NOT NULL,
  oid TEXT NOT NULL,
  digest TEXT,
  size INTEGER,
  verified_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS closure_entry_by_snapshot ON closure_entry(snapshot_id, tenant_id, repository_id);

CREATE TABLE IF NOT EXISTS lineage_node(
  node_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  node_kind TEXT NOT NULL CHECK(node_kind IN (
    'source_snapshot','worker_output','reviewed_patch','published_result')),
  ref_kind TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  content_digest TEXT,
  created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS lineage_node_scope ON lineage_node(tenant_id, repository_id);

-- THE lineage authority. A published_result legitimately has TWO parents
-- (the reviewed_patch and the input source_snapshot) -- nightshift_memory.
-- py's `artifact.derived_from` is a single-parent INTEGER column and stays
-- that way; widening it would be a regression surface on working, verified
-- memory code. This edge table is deliberately separate.
CREATE TABLE IF NOT EXISTS derived_from(
  parent_node_id TEXT NOT NULL REFERENCES lineage_node(node_id),
  child_node_id TEXT NOT NULL REFERENCES lineage_node(node_id),
  edge_kind TEXT NOT NULL CHECK(edge_kind IN ('derives','reviews','publishes','replaces','redacts')),
  content_carrying INTEGER NOT NULL CHECK(content_carrying IN (0,1)),
  recorded_at REAL NOT NULL,
  PRIMARY KEY(parent_node_id, child_node_id, edge_kind),
  CHECK(parent_node_id <> child_node_id));
CREATE INDEX IF NOT EXISTS derived_from_by_parent ON derived_from(parent_node_id);
CREATE INDEX IF NOT EXISTS derived_from_by_child ON derived_from(child_node_id);

-- source_snapshot is immutable except the single legal 0->1
-- source_not_retained transition (ADR-0004's explicit limit). Same
-- BEFORE-UPDATE-partition idiom nightshift_memory.py's
-- artifact_tombstone_is_redaction_only trigger already established: any
-- UPDATE that is not exactly this transition, on exactly these columns,
-- is refused outright. retention_ref/pack_digest/retention_state/the
-- three source_not_retained_* columns are the only fields allowed to move.
CREATE TRIGGER IF NOT EXISTS source_snapshot_immutable
BEFORE UPDATE ON source_snapshot FOR EACH ROW
WHEN NOT (
  old.source_not_retained = 0 AND new.source_not_retained = 1
  AND new.retention_state = 'expired'
  AND new.snapshot_id IS old.snapshot_id
  AND new.tenant_id IS old.tenant_id
  AND new.repository_id IS old.repository_id
  AND new.repository_fingerprint IS old.repository_fingerprint
  AND new.object_format IS old.object_format
  AND new.commit_oid IS old.commit_oid
  AND new.tree_oid IS old.tree_oid
  AND new.observed_ref IS old.observed_ref
  AND new.closure_manifest_digest IS old.closure_manifest_digest
  AND new.captured_at IS old.captured_at
  AND new.synthetic IS old.synthetic
  AND new.synthetic_parent_commit_oid IS old.synthetic_parent_commit_oid
  AND new.patch_digest IS old.patch_digest
  AND new.dirty_capture_method IS old.dirty_capture_method
  AND new.worktree_file_manifest_digest IS old.worktree_file_manifest_digest
  AND new.included_ignored IS old.included_ignored
  AND new.key_version IS old.key_version
)
BEGIN
  SELECT RAISE(ABORT, 'provenance: source_snapshot is immutable except the single source_not_retained 0->1 transition');
END;

CREATE TRIGGER IF NOT EXISTS closure_entry_immutable
BEFORE UPDATE ON closure_entry FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'provenance: closure_entry rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS lineage_node_immutable
BEFORE UPDATE ON lineage_node FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'provenance: lineage_node rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS derived_from_immutable
BEFORE UPDATE ON derived_from FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'provenance: derived_from edges are immutable');
END;

-- #400 acceptance condition 15, verbatim: "Retention and deletion walk
-- `derived_from` lineage across source bodies, chunks, summaries,
-- embeddings, caches, and exports. Completion is auditable and
-- idempotent." purge_lineage_node()'s own walk reaches exactly one of
-- those six named classes (source bodies, via lineage_node/derived_from
-- content_carrying edges) -- this table is the durable, immutable record
-- of what one purge actually did, INCLUDING which of the six classes it
-- did NOT reach, so "auditable" cannot be satisfied by a return value that
-- vanishes with the process. One row per (tenant, repository, root node)
-- -- a second purge of the same root re-affirms the SAME row rather than
-- writing a second one (see purge_lineage_node's own docstring for the
-- exact idempotence contract).
CREATE TABLE IF NOT EXISTS purge_receipt(
  receipt_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL CHECK(typeof(tenant_id)='integer' AND tenant_id>0),
  repository_id INTEGER NOT NULL,
  root_node_id TEXT NOT NULL,
  purged_node_count INTEGER NOT NULL CHECK(purged_node_count>=0),
  purged_nodes_json TEXT NOT NULL,
  content_digests_json TEXT NOT NULL,
  cas_scope TEXT NOT NULL CHECK(cas_scope IN ('removed','not_attempted_no_state_root')),
  cas_objects_removed INTEGER NOT NULL CHECK(cas_objects_removed>=0),
  covered_classes_json TEXT NOT NULL,
  uncovered_classes_json TEXT NOT NULL,
  closed INTEGER NOT NULL CHECK(closed IN (0,1)),
  reason TEXT,
  purged_at REAL NOT NULL,
  UNIQUE(tenant_id, repository_id, root_node_id));
CREATE INDEX IF NOT EXISTS purge_receipt_scope ON purge_receipt(tenant_id, repository_id);

-- An audit row that can be rewritten is not an audit row: unlike
-- lineage_node/derived_from (whose *rows* are the sanctioned target of a
-- gated DELETE elsewhere in this module), purge_receipt records the FACT
-- that a deletion happened, so this trigger blocks both UPDATE and DELETE
-- -- the one table in this schema where even removal must be refused.
CREATE TRIGGER IF NOT EXISTS purge_receipt_immutable
BEFORE UPDATE ON purge_receipt FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'provenance: purge_receipt rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS purge_receipt_no_delete
BEFORE DELETE ON purge_receipt FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'provenance: purge_receipt rows are immutable');
END;
"""


def _authority_guard(state_root, repository_id):
    """ADR-0082's client gate (STEP 5 of the #400 close-out).

    Fail-closed and NON-BREAKING at the same time, because the two are the
    same rule here: an ABSENT authority registry IS the legacy binding at
    epoch 0, so a client claiming the `external` backend is refused and a
    `legacy` client proceeds. That is ADR-0082's phase 1-4 posture ("Legacy
    remains the sole authority through comparison") written down, not a
    convenience default -- there is no path from "no registry" to the new
    store that does not go through an explicit, recorded, monotone flip.

    The AuthorityError is re-raised as a ProvenanceError carrying the SAME
    reason_code, so every existing `except ProvenanceError` boundary keeps
    working and the refusal payload keeps this module's shape.
    """
    try:
        import nightshift_authority as _nsa
    except ImportError:                                   # pragma: no cover
        return None
    try:
        # `state_root` is passed BOTH as the path to shadow-check and as the
        # explicit scope. Unlike publication and memory, this module is
        # handed the TOP-level state root, from which `derive_scope` cannot
        # (and must not) infer a repository_id -- but the caller already
        # named one, so the registry is addressable and the FULL check runs.
        return _nsa.guard_connect(state_root, repository_id=repository_id,
                                   state_root=state_root)
    except _nsa.AuthorityError as exc:
        raise ProvenanceError(exc.reason_code, exc.exit_code, exc.details) from exc


def connect(state_root, tenant_id, repository_id, *, create=True):
    """Open THIS tenant+repository's provenance.db, scoped by
    resolve_publication_root(). Physically separate from control.db and
    memory.db by construction -- this function never opens either.
    """
    _authority_guard(state_root, repository_id)
    root = resolve_publication_root(state_root, tenant_id, repository_id)
    _validate_scoped_root(root)
    path = os.path.join(root, PROVENANCE_DB_FILENAME)
    if not create and not os.path.isfile(path):
        _refuse("provenance_db_missing")
    cx = sqlite3.connect(path, timeout=30, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=30000")
    cx.execute("PRAGMA secure_delete=ON")
    cx.executescript(PROVENANCE_SCHEMA)
    return cx


def _validate_text_id(value, reason_code, *, maximum=200):
    if (not isinstance(value, str) or not value
            or len(value) > maximum
            or not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,%d}" % maximum, value)):
        _refuse(reason_code)
    return value


# `pub:` is a RESERVED prefix in the id vocabulary shared across the seam.
#
# `nightshift_publication.py` mints a node id per publication row as
# `pub:<kind>:<id>` and its `lineage_edge` view UNIONs those ids with the
# node ids in THIS file's lineage_node/derived_from graph, so both graphs
# live in ONE namespace. One namespace is only sound while the two halves
# are disjoint. Before this constant existed the docstring below explicitly
# blessed `pub:stage:1` as a legal snapshot id here, and the collision was
# real, not theoretical: a snapshot named `pub:stage:1` aliases publication
# stage 1's node, and a walk from a landed commit then reported a DIFFERENT
# snapshot's commit_oid with closed=True -- silently the wrong source. The
# same alias also makes a self-edge (`pub:stage:1 -> pub:stage:1`).
#
# Nothing in this file ever needed to mint such an id: publication's rows
# live in publication.db, and publication only ever READS this file. So the
# prefix is refused on every id write path here, and the disjointness the
# view depends on is a property of the data, not a convention.
RESERVED_NODE_ID_PREFIX = "pub:"


def _refuse_reserved_id(value):
    if isinstance(value, str) and value.startswith(RESERVED_NODE_ID_PREFIX):
        _refuse("reserved_node_id_namespace",
                {"id": value, "reserved_prefix": RESERVED_NODE_ID_PREFIX})
    return value


def validate_snapshot_id(snapshot_id):
    """Public name for the snapshot-id charset check every write path here
    already applies, so the sibling publication module can validate an id
    before looking it up without reaching into a private helper. Same
    charset on both sides of the seam ([A-Za-z0-9_.:-]) MINUS the reserved
    `pub:` prefix, which publication owns -- see RESERVED_NODE_ID_PREFIX
    above for why an id in that namespace is refused rather than accepted
    as "the same vocabulary"."""
    _validate_text_id(snapshot_id, "invalid_snapshot_id")
    return _refuse_reserved_id(snapshot_id)


def validate_node_id(node_id):
    """Lineage node ids obey the same rule as snapshot ids: a node in THIS
    graph may never be named in publication's `pub:` namespace, or the
    unified view would alias two different nodes."""
    _validate_text_id(node_id, "invalid_node_id")
    return _refuse_reserved_id(node_id)


def repository_fingerprint(customer_path):
    """Canonical, OFFLINE repository identity (#400: "not only a mutable
    URL or branch name"; runtime has no network, so the GitHub-API
    identity nightshift_project.py already computes cannot be the runtime
    check here). sha256 of the canonical JSON of {object_format, sorted
    root commit OIDs} -- [VERIFIED] read-only.

    HONEST LIMIT, stated in code because it is easy to misuse later: forks
    share root commits, and a repository with zero commits has none. This
    is an ANTI-SUBSTITUTION check ("the path handed to me is not a
    different repository from the one registered"), never a global unique
    id -- the registered repository_id remains the authority, and the fork
    case is covered by resolve_publication_root's physical separation and
    (in the sibling module) the approval tuple, not by this hash.
    """
    fmt = customer_git(customer_path, ["rev-parse", "--show-object-format"])
    if fmt.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "rev-parse --show-object-format",
            "returncode": fmt.returncode, "stderr": fmt.stderr[:500]})
    roots = customer_git(customer_path, ["rev-list", "--max-parents=0", "--all"])
    if roots.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "rev-list --max-parents=0 --all",
            "returncode": roots.returncode, "stderr": roots.stderr[:500]})
    root_commits = sorted(l for l in roots.stdout.splitlines() if l.strip())
    canonical = json.dumps(
        {"object_format": fmt.stdout.strip(), "root_commits": root_commits},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_source_snapshot(cx, tenant_id, repository_id, snapshot_id, fields):
    """Insert one immutable source_snapshot row. `fields` carries every
    column beyond the scope/id ones this function fixes itself --
    repository_fingerprint, object_format, commit_oid, tree_oid and
    captured_at are required; everything else defaults per schema.

    ONE exception to "defaults per schema": `closure_manifest_digest` is
    filled in with `closure_manifest_digest([])` when the caller supplies
    none. That is not a convenience -- it is what makes
    `verify_closure_complete` reachable at all. The column is NULLable and
    frozen by the immutability trigger, so a snapshot recorded without one
    used to carry NO closure claim, and the verifier's only honest
    response to "no claim" was to pass. Every snapshot this function
    writes now carries an explicit, immutable claim instead: either the
    digest the caller DISCOVERED (see `capture_dirty_snapshot`, which
    computes it from the snapshot's own tree), or the digest of the empty
    manifest, which says "nothing was required" out loud and is then
    checked against the `closure_entry` rows like any other claim.

    HONEST LIMIT, so the guarantee is not overstated: this makes the claim
    EXPLICIT and IMMUTABLE, not TRUE. A caller that captures a repository
    with two submodules and never discovers its closure still records "no
    closure required", and publication still accepts it. Only the capture
    path can close that -- which is why `capture_dirty_snapshot` now
    discovers the closure rather than leaving the field to the caller.

    The snapshot id also enters the UNIFIED lineage namespace that the
    sibling publication module walks (`lineage_edge`), where publication's
    `input_snapshot` parent IS this id. A lineage_node of any other kind
    already carrying this id would alias that boundary, so it is refused
    here rather than resolved by guesswork later -- the mirror of the
    check in `record_lineage_node`.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    validate_snapshot_id(snapshot_id)
    required = ("repository_fingerprint", "object_format", "commit_oid", "tree_oid", "captured_at")
    missing = [k for k in required if k not in fields]
    if missing:
        _refuse("source_snapshot_incomplete", {"missing": missing})
    clash = cx.execute(
        "SELECT node_id FROM lineage_node WHERE node_id=? AND tenant_id=? AND repository_id=? "
        "AND node_kind<>'source_snapshot'",
        (snapshot_id, tenant_id, repository_id)).fetchone()
    if clash is not None:
        _refuse("lineage_namespace_collision", {
            "id": snapshot_id,
            "also_a": "lineage_node of another kind"})
    fields = dict(fields)
    if fields.get("closure_manifest_digest") is None:
        fields["closure_manifest_digest"] = closure_manifest_digest([])
    columns = ["snapshot_id", "tenant_id", "repository_id"] + list(fields.keys())
    values = [snapshot_id, tenant_id, repository_id] + list(fields.values())
    placeholders = ",".join("?" for _ in columns)
    try:
        cx.execute(
            f"INSERT INTO source_snapshot({','.join(columns)}) VALUES({placeholders})",
            values)
    except sqlite3.IntegrityError as exc:
        _refuse("source_snapshot_invalid", {"error": str(exc)})
    return snapshot_id


def mark_source_not_retained(cx, tenant_id, repository_id, snapshot_id, reason, *, t=None):
    """The single legal 0->1 transition, as a rowcount CAS -- same shape as
    bin/nightshift-ctl's consume_program_action_permit. A repeat call (row
    already expired, or wrong tenant/repository/snapshot) matches zero rows
    and is refused; any OTHER mutation attempt on the row is refused by the
    source_snapshot_immutable trigger, not merely by this function's own
    discipline.

    NON-PROPAGATING (ADR-0005, verbatim: "expiry of a Git source snapshot
    is non-propagating"): this function touches exactly one row and never
    walks derived_from at all, so a worker_output/lineage_node that
    `derives` from this snapshot is mechanically untouched by this call --
    see content_carrying_descendants()'s docstring for the edge-typing
    that keeps a deliberate redaction walk from ever reaching backward into
    an *expired* snapshot's dependents through this path either.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    validate_snapshot_id(snapshot_id)
    t = time.time() if t is None else t
    cur = cx.execute(
        "UPDATE source_snapshot SET source_not_retained=1, source_not_retained_at=?, "
        "source_not_retained_reason=?, retention_state='expired', retention_ref=NULL, "
        "pack_digest=NULL "
        "WHERE snapshot_id=? AND tenant_id=? AND repository_id=? AND source_not_retained=0",
        (t, reason, snapshot_id, tenant_id, repository_id))
    if cur.rowcount != 1:
        _refuse("snapshot_not_found_or_already_expired")
    return True


def get_source_snapshot(cx, tenant_id, repository_id, snapshot_id):
    """Cross-tenant read (#400 item 9): scoped by tenant_id+repository_id
    in the WHERE clause. Returns None uniformly whether the id truly does
    not exist OR belongs to a different tenant -- by construction there is
    no second code path that could answer differently, so there is no
    "exists but forbidden" oracle a caller could probe for.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    row = cx.execute(
        "SELECT * FROM source_snapshot WHERE snapshot_id=? AND tenant_id=? AND repository_id=?",
        (snapshot_id, tenant_id, repository_id)).fetchone()
    return dict(row) if row else None


def list_source_snapshots(cx, tenant_id, repository_id):
    """Cross-tenant search/list (#400 item 9): a query scoped to tenant B
    can never return, or even count, a tenant A row -- the WHERE clause
    filters both scope columns unconditionally.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    rows = cx.execute(
        "SELECT * FROM source_snapshot WHERE tenant_id=? AND repository_id=? ORDER BY captured_at",
        (tenant_id, repository_id)).fetchall()
    return [dict(r) for r in rows]


def export_source_snapshots(cx, tenant_id, repository_id):
    """Cross-tenant export (#400 item 9): identical query shape to
    list_source_snapshots -- an export scoped to tenant B structurally
    cannot contain a tenant A row or object reference, because it is
    produced by the same tenant/repository-filtered SELECT.
    """
    return list_source_snapshots(cx, tenant_id, repository_id)


def record_closure_entry(cx, tenant_id, repository_id, snapshot_id, kind, path, oid,
                          *, digest=None, size=None, t=None):
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    validate_snapshot_id(snapshot_id)
    if kind not in ("submodule", "lfs"):
        _refuse("invalid_closure_kind", {"kind": kind})
    t = time.time() if t is None else t
    cur = cx.execute(
        "INSERT INTO closure_entry(snapshot_id,tenant_id,repository_id,kind,path,oid,digest,size,verified_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (snapshot_id, tenant_id, repository_id, kind, path, oid, digest, size, t))
    return cur.lastrowid


def discover_closure(customer_path, *, rev="HEAD"):
    """Read-only submodule + Git LFS closure discovery (#400 item 11).

    `rev` names the tree to discover, and defaults to HEAD. It exists so
    `capture_dirty_snapshot` can discover the closure of the SYNTHETIC
    commit it just wrote (whose tree is the dirty tree, which may add or
    drop a gitlink relative to HEAD) rather than of HEAD, which would
    freeze a digest describing a different tree than the snapshot row.

    Submodules: `git ls-tree -r -t <rev>` mode-160000 gitlink rows --
    [VERIFIED] no `git submodule init` needed, so this stays read-only
    against the customer repo.

    Git LFS: `git check-attr filter -- <path>` == lfs identifies an LFS
    path, then `git cat-file blob <oid>` reads the pointer text and this
    function parses `oid sha256:<hex>` / `size <n>` out of it --
    [VERIFIED] this works without git-lfs installed, because the blob
    tracked in the tree for an LFS path is always the ~130-byte pointer,
    never the payload. That also means "a superproject pinned but missing
    LFS bytes" is detectable from the pointer alone, without fetching
    anything.

    NOT VERIFIED AT SCALE: this calls `check-attr` once per tracked blob,
    which is fine for the fixtures this module was tested against but is
    an unmeasured cost on a repository with very many tracked files; a
    production caller may want to batch via `check-attr --stdin`.
    """
    tree = customer_git(customer_path, ["ls-tree", "-r", "-t", rev])
    if tree.returncode != 0:
        if "bad revision" in tree.stderr.lower() or "not a valid object" in tree.stderr.lower():
            return []  # brand-new, commit-less repository: no closure to discover
        _probe_failed("footprint_probe_failed", {
            "command": "ls-tree -r -t %s" % rev,
            "returncode": tree.returncode, "stderr": tree.stderr[:500]})
    entries = []
    rows = []
    for line in tree.stdout.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        mode, obj_type, oid = meta.split(" ")
        rows.append((mode, obj_type, oid, path))
        if mode == "160000":
            entries.append({"kind": "submodule", "path": path, "oid": oid})
    for mode, obj_type, oid, path in rows:
        if obj_type != "blob" or mode == "160000":
            continue
        attr = customer_git(customer_path, ["check-attr", "filter", "--", path])
        if attr.returncode != 0 or ": lfs" not in attr.stdout:
            continue
        blob = customer_git(customer_path, ["cat-file", "blob", oid])
        if blob.returncode != 0:
            continue
        m_oid = re.search(r"^oid sha256:([0-9a-f]{64})$", blob.stdout, re.MULTILINE)
        m_size = re.search(r"^size (\d+)$", blob.stdout, re.MULTILINE)
        if m_oid and m_size:
            entries.append({
                "kind": "lfs", "path": path, "oid": oid,
                "digest": "sha256:" + m_oid.group(1), "size": int(m_size.group(1))})
    return entries


def require_closure_complete(required_paths, closure_entries):
    covered = {e["path"] for e in closure_entries}
    missing = sorted(set(required_paths) - covered)
    if missing:
        _refuse("closure_incomplete", {"missing": missing})
    return True


def closure_manifest_digest(entries):
    """The canonical digest of a closure MANIFEST -- the exact set of
    (kind, path, oid) triples a snapshot's source tree requires, as
    `discover_closure` returns them.

    Recorded ONCE, at `record_source_snapshot` time, into the snapshot's
    own `closure_manifest_digest` column (already in the schema and in the
    immutability trigger's frozen set, so it can never be edited after the
    fact). `verify_closure_complete` below recomputes it from the
    `closure_entry` rows actually persisted and compares.

    What that does and does not buy, stated precisely because the earlier
    wording here overclaimed it: the caller cannot LATER assert "nothing
    was required" for a snapshot it captured with two submodules, because
    the digest is frozen at capture time and the trigger refuses to move
    it. It can still assert that AT capture time, by never discovering the
    closure -- `record_source_snapshot` then freezes the empty-manifest
    digest. Freezing an empty claim is a weaker fact than a discovered
    one; the difference lives entirely in the capture path, not here.

    Same canonical JSON dialect as `repository_fingerprint` above (sorted,
    tight separators) so this codebase keeps ONE hashing convention.
    """
    triples = sorted(
        [str(e["kind"]), str(e["path"]), str(e["oid"])] for e in entries)
    canonical = json.dumps(triples, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_closure_complete(cx, tenant_id, repository_id, snapshot_id, *, schema="main"):
    """#400 item 11, DISCOVERED rather than DECLARED: recompute the closure
    manifest digest from the `closure_entry` rows this database actually
    holds for `snapshot_id`, and compare it to the digest frozen on the
    snapshot row. Any shortfall -- a submodule whose entry was never
    persisted, an LFS object dropped between capture and publication --
    changes the recomputed digest and refuses `closure_incomplete`.

    NULL is not a pass. Every row `record_source_snapshot` writes carries
    a digest (the empty-manifest one when nothing was discovered), so a
    NULL column means the row was written around that function -- raw SQL,
    or a database predating the default. Such a row carries no closure
    claim at all, and a verifier cannot certify a claim that was never
    made, so it refuses `closure_incomplete` with
    `reason: no_closure_claim_recorded`. Before this, `NULL -> True` made
    the whole check inert for every snapshot that simply declined to
    declare one.

    `schema` names the SQLite database these tables live in. The sibling
    publication module ATTACHes provenance.db READ-ONLY as `prov` and
    passes schema="prov"; provenance's own callers use the default. It is
    an internal schema selector, never a caller-supplied tenant scope --
    the tenant scope is the WHERE clause, and it is not optional.

    HONEST LIMIT: a digest cannot name the paths it is missing. The
    refusal reports the digest that was frozen, the digest observed now,
    and the paths that ARE covered, which is everything recoverable from a
    digest. Naming the absentees would need the manifest itself stored
    beside its digest -- deliberately not added here, because the frozen
    column that exists is exactly the one ADR-0079 reserved for this.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    validate_snapshot_id(snapshot_id)
    if schema not in ("main", "prov", "temp"):
        _refuse("invalid_schema_name", {"schema": schema})
    row = cx.execute(
        f"SELECT closure_manifest_digest FROM {schema}.source_snapshot "
        "WHERE snapshot_id=? AND tenant_id=? AND repository_id=?",
        (snapshot_id, tenant_id, repository_id)).fetchone()
    if row is None:
        _refuse("source_snapshot_not_found", {"snapshot_id": snapshot_id})
    recorded = row["closure_manifest_digest"]
    rows = cx.execute(
        f"SELECT kind, path, oid FROM {schema}.closure_entry "
        "WHERE snapshot_id=? AND tenant_id=? AND repository_id=?",
        (snapshot_id, tenant_id, repository_id)).fetchall()
    entries = [{"kind": r["kind"], "path": r["path"], "oid": r["oid"]} for r in rows]
    observed = closure_manifest_digest(entries)
    if recorded is None:
        _refuse("closure_incomplete", {
            "snapshot_id": snapshot_id,
            "reason": "no_closure_claim_recorded",
            "recorded_manifest_digest": None,
            "observed_manifest_digest": observed,
            "covered_paths": sorted({e["path"] for e in entries}),
        })
    if observed != recorded:
        _refuse("closure_incomplete", {
            "snapshot_id": snapshot_id,
            "recorded_manifest_digest": recorded,
            "observed_manifest_digest": observed,
            "covered_paths": sorted({e["path"] for e in entries}),
        })
    return True


def record_lineage_node(cx, tenant_id, repository_id, node_id, node_kind, ref_kind, ref_id,
                         *, content_digest=None, t=None):
    """Insert one lineage_node row.

    `node_id` is free-form within the charset -- this module has always
    let a caller name a source_snapshot node anything (`n_snap`,
    `node-snapshot`) and carry the snapshot id in `ref_id`, and
    `lineage_chain` reports node ids, so that stays. What is NOT free-form
    is the SEAM: the sibling publication module's unified `lineage_edge`
    view addresses a `source_snapshot` node by its `ref_id`, because that
    -- not the node id -- is the column that means "which snapshot". Two
    consequences are enforced here rather than left to convention:

      * for a `source_snapshot` node, `ref_id` is a snapshot id and is
        validated as one (charset, and NOT publication's reserved `pub:`
        namespace), because it is the id that crosses into the shared
        namespace;
      * at most ONE `source_snapshot` node per (tenant, repository,
        ref_id). Two nodes for one snapshot would collapse to a single id
        in the unified view and, if edged to each other, would appear
        there as a self-edge -- the shape `record_derived_from` already
        refuses.

    And, symmetrically to `record_source_snapshot`: a node of any OTHER
    kind may not be NAMED with an existing snapshot id, or it would alias
    the boundary node publication's `input_snapshot` parent points at.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    validate_node_id(node_id)
    if node_kind not in ("source_snapshot", "worker_output", "reviewed_patch", "published_result"):
        _refuse("invalid_node_kind", {"node_kind": node_kind})
    if node_kind == "source_snapshot":
        validate_snapshot_id(ref_id)
        dup = cx.execute(
            "SELECT node_id FROM lineage_node WHERE tenant_id=? AND repository_id=? "
            "AND node_kind='source_snapshot' AND ref_id=?",
            (tenant_id, repository_id, ref_id)).fetchone()
        if dup is not None:
            _refuse("lineage_snapshot_node_duplicate",
                    {"ref_id": ref_id, "existing_node_id": dup["node_id"]})
    else:
        clash = cx.execute(
            "SELECT snapshot_id FROM source_snapshot "
            "WHERE snapshot_id=? AND tenant_id=? AND repository_id=?",
            (node_id, tenant_id, repository_id)).fetchone()
        if clash is not None:
            _refuse("lineage_namespace_collision", {
                "id": node_id, "also_a": "source_snapshot", "node_kind": node_kind})
    t = time.time() if t is None else t
    try:
        cx.execute(
            "INSERT INTO lineage_node(node_id,tenant_id,repository_id,node_kind,ref_kind,ref_id,content_digest,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (node_id, tenant_id, repository_id, node_kind, ref_kind, ref_id, content_digest, t))
    except sqlite3.IntegrityError as exc:
        _refuse("lineage_node_exists", {"error": str(exc)})
    return node_id


def _would_create_cycle(cx, parent_node_id, child_node_id):
    row = cx.execute(
        "WITH RECURSIVE reachable(node_id) AS ("
        "  SELECT child_node_id FROM derived_from WHERE parent_node_id = ?"
        "  UNION"
        "  SELECT df.child_node_id FROM derived_from df"
        "  JOIN reachable r ON df.parent_node_id = r.node_id"
        ") SELECT 1 FROM reachable WHERE node_id = ? LIMIT 1",
        (child_node_id, parent_node_id)).fetchone()
    return row is not None


def record_derived_from(cx, tenant_id, repository_id, parent_node_id, child_node_id,
                         edge_kind, content_carrying, *, t=None):
    """Insert one derived_from edge. Refuses (lineage_cross_tenant_refused)
    unless both endpoints already exist as lineage_node rows sharing the
    EXACT (tenant_id, repository_id) passed in -- lineage cannot cross a
    tenant, and cannot silently span two different registered
    repositories under one tenant either. Refuses a cycle
    (lineage_cycle_refused) via a recursive-CTE reachability check before
    insert, same shape the control-plane dependency-cycle check already
    uses elsewhere in this repo.

    `content_carrying` is the one column that makes ADR-0080's
    `non-propagating` rule mechanical rather than a promise: only edges with
    content_carrying=1 are ever followed by content_carrying_descendants()
    below. A `derives` edge from a source_snapshot to narrative MUST be
    recorded with content_carrying=0 by the caller -- this function does
    not infer it, because inferring it from node_kind alone would hide the
    exact place a future edge_kind could get this wrong.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    validate_node_id(parent_node_id)
    validate_node_id(child_node_id)
    if edge_kind not in ("derives", "reviews", "publishes", "replaces", "redacts"):
        _refuse("invalid_edge_kind", {"edge_kind": edge_kind})
    if content_carrying not in (0, 1):
        _refuse("invalid_content_carrying", {"content_carrying": content_carrying})
    if parent_node_id == child_node_id:
        _refuse("lineage_self_loop_refused")
    parent = cx.execute(
        "SELECT tenant_id, repository_id FROM lineage_node WHERE node_id=?",
        (parent_node_id,)).fetchone()
    child = cx.execute(
        "SELECT tenant_id, repository_id FROM lineage_node WHERE node_id=?",
        (child_node_id,)).fetchone()
    if parent is None or child is None:
        _refuse("lineage_node_missing")
    if (parent["tenant_id"], parent["repository_id"]) != (tenant_id, repository_id) or \
       (child["tenant_id"], child["repository_id"]) != (tenant_id, repository_id):
        _refuse("lineage_cross_tenant_refused")
    if _would_create_cycle(cx, parent_node_id, child_node_id):
        _refuse("lineage_cycle_refused")
    t = time.time() if t is None else t
    try:
        cx.execute(
            "INSERT INTO derived_from(parent_node_id,child_node_id,edge_kind,content_carrying,recorded_at) "
            "VALUES(?,?,?,?,?)",
            (parent_node_id, child_node_id, edge_kind, content_carrying, t))
    except sqlite3.IntegrityError as exc:
        _refuse("derived_from_edge_exists", {"error": str(exc)})


def _parent_via(cx, tenant_id, repository_id, child_node_id, edge_kind, expected_kind):
    row = cx.execute(
        "SELECT p.* FROM derived_from d JOIN lineage_node p ON p.node_id = d.parent_node_id "
        "WHERE d.child_node_id=? AND d.edge_kind=? AND p.tenant_id=? AND p.repository_id=? AND p.node_kind=?",
        (child_node_id, edge_kind, tenant_id, repository_id, expected_kind)).fetchone()
    return dict(row) if row else None


def lineage_chain(cx, tenant_id, repository_id, published_result_node_id):
    """Walk the required chain (#400 items 10-11) BACKWARDS as a shape, not
    a hope: source_snapshot --derives--> worker_output --reviews-->
    reviewed_patch --publishes--> published_result. Returns
    {"closed": True, "chain": [snapshot_id, worker_id, patch_id, result_id]}
    or {"closed": False, "reason": "lineage_incomplete", "missing": <which link>}.
    A publish attempt whose receipt cannot close this chain is refused
    with `lineage_incomplete` by whatever calls this (the sibling
    publication module, not built here).
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    result = cx.execute(
        "SELECT * FROM lineage_node WHERE node_id=? AND tenant_id=? AND repository_id=? AND node_kind='published_result'",
        (published_result_node_id, tenant_id, repository_id)).fetchone()
    if result is None:
        return {"closed": False, "reason": "lineage_incomplete", "missing": "published_result"}
    patch = _parent_via(cx, tenant_id, repository_id, published_result_node_id, "publishes", "reviewed_patch")
    if patch is None:
        return {"closed": False, "reason": "lineage_incomplete", "missing": "reviewed_patch"}
    worker = _parent_via(cx, tenant_id, repository_id, patch["node_id"], "reviews", "worker_output")
    if worker is None:
        return {"closed": False, "reason": "lineage_incomplete", "missing": "worker_output"}
    snapshot = _parent_via(cx, tenant_id, repository_id, worker["node_id"], "derives", "source_snapshot")
    if snapshot is None:
        return {"closed": False, "reason": "lineage_incomplete", "missing": "source_snapshot"}
    return {
        "closed": True,
        "chain": [snapshot["node_id"], worker["node_id"], patch["node_id"], published_result_node_id],
    }


def content_carrying_descendants(cx, tenant_id, repository_id, node_id):
    """BFS forward following ONLY content_carrying=1 edges -- the exact set
    a redaction/deletion must also remove (chunks, embeddings, summaries,
    snippets -- ADR-0005). Includes `node_id` itself in the returned set.

    This is never invoked as a side effect of mark_source_not_retained();
    the non-propagation guarantee is structural (that function never calls
    this one), not merely "this walk happens to stop here". A `derives`
    edge recorded from a source_snapshot to a narrative node with
    content_carrying=0 additionally ensures that even an explicit walk
    STARTING elsewhere and passing through that edge does not continue
    past it -- belt and suspenders.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    seen = {node_id}
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        rows = cx.execute(
            "SELECT child_node_id FROM derived_from d "
            "JOIN lineage_node c ON c.node_id = d.child_node_id "
            "WHERE d.parent_node_id=? AND d.content_carrying=1 AND c.tenant_id=? AND c.repository_id=?",
            (current, tenant_id, repository_id)).fetchall()
        for row in rows:
            child = row["child_node_id"]
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return seen


#: #400 condition 15's own six named classes, verbatim: "source bodies,
#: chunks, summaries, embeddings, caches, and exports." `purge_lineage_node`
#: mechanically walks exactly one of these (source bodies, via
#: content_carrying_descendants + the CAS bodies those nodes name) --
#: `covers` is how a CALLER attests it separately walked the other five
#: (a memory-store purge, an export-cache eviction, ...) in the SAME
#: logical deletion; naming a class here that was not actually walked is a
#: caller bug this module has no way to detect, exactly like `reason` for
#: `destroy_object_keys` -- it is recorded, not independently verified.
AC15_LINEAGE_CLASSES = frozenset({
    "source_bodies", "chunks", "summaries", "embeddings", "caches", "exports",
})


def _purge_receipt_id(tenant_id, repository_id, root_node_id):
    """Deterministic PRIMARY KEY over (tenant, repository, root node) ONLY
    -- deliberately NOT over the victim set, so a replay against an
    already-purged root computes the SAME id regardless of what
    (necessarily empty, by then) victims it would recompute. That is what
    lets a second call's INSERT OR IGNORE collide with the first call's row
    on purpose."""
    return hashlib.sha256(
        f"purge|{tenant_id}|{repository_id}|{root_node_id}".encode("utf-8")).hexdigest()


def lineage_purge_receipt(cx, tenant_id, repository_id, root_node_id):
    """The audit-path reader: the one durable purge_receipt row for this
    (tenant, repository, root node), or None if this root was never
    purged. Cross-tenant-scoped in the WHERE clause like every other
    reader in this module."""
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    row = cx.execute(
        "SELECT * FROM purge_receipt WHERE tenant_id=? AND repository_id=? AND root_node_id=?",
        (tenant_id, repository_id, root_node_id)).fetchone()
    return dict(row) if row is not None else None


def purge_lineage_node(cx, tenant_id, repository_id, node_id, *,
                        state_root=None, covers=(), reason=None, t=None):
    """Cross-tenant-safe deletion (#400 item 9): deletes `node_id` and its
    content-carrying descendants, scoped to tenant_id+repository_id at
    every step. A purge attempt under the wrong tenant matches zero rows
    (the node lookup inside content_carrying_descendants() is itself
    tenant-scoped) and the real owner's rows are byte-identical afterward.
    Returns the set of deleted node_ids (empty if the node did not belong
    to this tenant/repository, indistinguishable from "did not exist").

    All four new parameters are keyword-only with defaults, and the
    positional call shape and return value are UNCHANGED -- three test
    modules this stream does not own, and
    bin/nightshift_tenant_keys.py's purge_lineage_with_keys, call this
    positionally and consume the returned set.

    #400 acceptance condition 15 ("Completion is auditable and
    idempotent"): every call that finds a real owner writes exactly one
    immutable `purge_receipt` row (see AC15_LINEAGE_CLASSES for the
    six-class vocabulary `covers` draws from). `state_root`, when given,
    also deletes the CAS body for every content digest the purged nodes
    carried (`cas_scope='removed'`); with no `state_root` the receipt
    honestly records `cas_scope='not_attempted_no_state_root'` -- never a
    misleading "0 removed" that reads as "verified absent". `closed` is a
    real computation, not a constant: it is 1 only when `covers` names
    every one of the six classes AND every receipted digest is verifiably
    absent from CAS afterward (vacuously true when there were no digests
    to remove at all). No production caller today can honestly pass a
    full `covers`, so every real deletion receipts `closed=0` with the
    unreached classes named in `uncovered_classes_json` -- that is AC15's
    boundary stated as data, not silence.

    IDEMPOTENT REPLAY: a second call naming the same (tenant, repository,
    node_id) after the first one already ran finds no owning row (the
    node is gone) and, because a receipt already exists for this root,
    re-affirms that SAME row -- `INSERT OR IGNORE` against the receipt's
    PRIMARY KEY is a true no-op, so the original receipt's bytes (its
    real purged_nodes/purged_at) never move -- and returns the ORIGINAL
    victim set the first call recorded, not an empty one that would be
    indistinguishable from "there was never anything to purge here". A
    call naming a node that never belonged to this tenant at all (no
    receipt either) still returns plain `set()`, unchanged from before.
    """
    validate_tenant_id(tenant_id)
    validate_repository_id(repository_id)
    t = time.time() if t is None else float(t)
    covers = frozenset(covers or ())
    unknown = sorted(covers - AC15_LINEAGE_CLASSES)
    if unknown:
        _refuse("unknown_lineage_class", {"unknown": unknown})

    receipt_id = _purge_receipt_id(tenant_id, repository_id, node_id)
    existing = lineage_purge_receipt(cx, tenant_id, repository_id, node_id)

    owner = cx.execute(
        "SELECT 1 FROM lineage_node WHERE node_id=? AND tenant_id=? AND repository_id=?",
        (node_id, tenant_id, repository_id)).fetchone()

    if owner is None:
        if existing is None:
            # Cross-tenant, or a node_id that never existed here at all --
            # unchanged from the pre-AC15 behavior.
            return set()
        # Idempotent replay of an already-purged root: re-affirm the same
        # receipt row (OR IGNORE makes this a true no-op against the
        # PRIMARY KEY) and hand back what was ACTUALLY purged, per this
        # function's own docstring promise that completion is auditable.
        cx.execute(
            "INSERT OR IGNORE INTO purge_receipt(receipt_id,tenant_id,repository_id,"
            "root_node_id,purged_node_count,purged_nodes_json,content_digests_json,"
            "cas_scope,cas_objects_removed,covered_classes_json,uncovered_classes_json,"
            "closed,reason,purged_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, tenant_id, repository_id, node_id, 0, "[]", "[]",
             "not_attempted_no_state_root", 0, "[]", "[]", 0, reason, t))
        return set(json.loads(existing["purged_nodes_json"]))

    victims = content_carrying_descendants(cx, tenant_id, repository_id, node_id)
    placeholders = ",".join("?" for _ in victims)
    digests = set()
    if victims:
        for row in cx.execute(
                f"SELECT content_digest FROM lineage_node WHERE node_id IN ({placeholders}) "
                f"AND tenant_id=? AND repository_id=?",
                list(victims) + [tenant_id, repository_id]).fetchall():
            if row["content_digest"]:
                digests.add(row["content_digest"])

    cx.execute(
        f"DELETE FROM derived_from WHERE (parent_node_id IN ({placeholders}) OR child_node_id IN ({placeholders}))",
        list(victims) * 2)
    cx.execute(
        f"DELETE FROM lineage_node WHERE node_id IN ({placeholders}) AND tenant_id=? AND repository_id=?",
        list(victims) + [tenant_id, repository_id])

    cas_objects_removed = 0
    if state_root is not None:
        cas_scope = "removed"
        for digest in sorted(digests):
            if purge_staged_object(state_root, tenant_id, repository_id, digest):
                cas_objects_removed += 1
    else:
        cas_scope = "not_attempted_no_state_root"

    if digests:
        cas_all_absent = cas_scope == "removed" and all(
            not os.path.exists(stage_object_path(state_root, tenant_id, repository_id, d))
            for d in digests)
    else:
        cas_all_absent = True  # nothing to remove -- vacuously satisfied.
    uncovered = sorted(AC15_LINEAGE_CLASSES - covers)
    closed = 1 if (not uncovered and cas_all_absent) else 0

    cx.execute(
        "INSERT OR IGNORE INTO purge_receipt(receipt_id,tenant_id,repository_id,"
        "root_node_id,purged_node_count,purged_nodes_json,content_digests_json,"
        "cas_scope,cas_objects_removed,covered_classes_json,uncovered_classes_json,"
        "closed,reason,purged_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (receipt_id, tenant_id, repository_id, node_id, len(victims),
         json.dumps(sorted(victims), sort_keys=True, separators=(",", ":")),
         json.dumps(sorted(digests), sort_keys=True, separators=(",", ":")),
         cas_scope, cas_objects_removed,
         json.dumps(sorted(covers), sort_keys=True, separators=(",", ":")),
         json.dumps(uncovered, sort_keys=True, separators=(",", ":")),
         closed, reason, t))
    return victims


# ---------------------------------------------------------------------------
# Cross-tenant content-addressed staging (PG-6 dedup case). Physical
# separation IS the dedup boundary: two tenants staging identical bytes
# resolve to two different paths (different tenant_id segment), so there
# is no shared pool and no "already staged" existence oracle across
# tenants -- ADR-0005: "cross-tenant dedupe is forbidden".
# ---------------------------------------------------------------------------


def stage_object_path(state_root, tenant_id, repository_id, content_digest):
    if not re.fullmatch(r"[0-9a-f]{64}", content_digest or ""):
        _refuse("invalid_content_digest")
    root = resolve_publication_root(state_root, tenant_id, repository_id)
    return os.path.join(root, "objects", content_digest[:2], content_digest)


#: Envelope framing, mirrored from nightshift_tenant_keys so this module can
#: RECOGNISE a sealed object without importing key custody. ADR-0080 allows
#: the tenant key to live in an OS keystore or a customer-controlled KMS, so
#: a hard import edge would make the staging tree unreadable wherever
#: custody is unavailable -- and merely telling sealed bytes from plaintext
#: needs no key at all.
SEALED_OBJECT_MAGIC = b"NSENV1\n"


def is_sealed_object(raw):
    return (isinstance(raw, (bytes, bytearray))
            and bytes(raw[:len(SEALED_OBJECT_MAGIC)]) == SEALED_OBJECT_MAGIC)


def stage_object(state_root, tenant_id, repository_id, content_digest, data,
                 *, key_version=None, sealed=None):
    """Stage one content-addressed object, optionally ENVELOPE-ENCRYPTED
    (ADR-0080: "application envelope encryption for artifact bodies").

    `data` is always the PLAINTEXT, and `content_digest` is always the
    plaintext digest -- content addressing must not move to the ciphertext,
    or the same body sealed twice under two nonces would resolve to two
    different paths and dedupe inside a tenant would silently stop working.

    `sealed` is the serialised envelope
    (`nightshift_tenant_keys.envelope_to_bytes(...)`) for exactly those
    plaintext bytes. When it is given, the BYTES ON DISK ARE THE ENVELOPE
    and the plaintext is never written.

    The `key_version` is carried INSIDE the envelope's own accounting (the
    `object_key` row keyed by this same digest), never in a sidecar file:
    `purge_staged_object` is path-scoped and would not remove a sidecar, so
    a sidecar would let key metadata outlive the object it describes.
    """
    path = stage_object_path(state_root, tenant_id, repository_id, content_digest)
    if hashlib.sha256(data).hexdigest() != content_digest:
        _refuse("content_digest_mismatch")
    payload = data
    if sealed is not None:
        if key_version is None:
            # A sealed body with no recorded key version is a body nobody
            # can ever open again -- the unwrap path has no version to look
            # the DEK up under. Refuse rather than write it.
            _refuse("sealed_object_requires_key_version",
                    {"content_digest": content_digest})
        if not is_sealed_object(sealed):
            _refuse("sealed_object_malformed", {"content_digest": content_digest})
        payload = bytes(sealed)
        if data and bytes(data) in payload:
            # Cheap, decisive self-check: if the plaintext is literally
            # present in the bytes about to be written, the "envelope" did
            # not encrypt anything. Refusing here is what makes the
            # encryption claim falsifiable at the write, not at some later
            # audit.
            _refuse("sealed_object_leaks_plaintext",
                    {"content_digest": content_digest})
    elif key_version is not None:
        _refuse("key_version_without_sealed_body",
                {"content_digest": content_digest})
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return path


def read_staged_object(state_root, tenant_id, repository_id, content_digest):
    """Return `(raw_bytes, sealed)` for a staged object. `sealed` is True
    when the bytes on disk are an envelope; the caller then needs the DEK
    (`nightshift_tenant_keys.unwrap_object_key`) to recover the body. This
    function deliberately cannot decrypt -- it holds no key material."""
    path = stage_object_path(state_root, tenant_id, repository_id, content_digest)
    if not os.path.exists(path):
        _refuse("staged_object_missing", {"content_digest": content_digest})
    with open(path, "rb") as fh:
        raw = fh.read()
    return raw, is_sealed_object(raw)


def object_exists(state_root, tenant_id, repository_id, content_digest):
    return os.path.exists(stage_object_path(state_root, tenant_id, repository_id, content_digest))


def purge_staged_object(state_root, tenant_id, repository_id, content_digest):
    """Cross-tenant-safe object deletion: resolves to a tenant-scoped path,
    so a purge attempted under the wrong tenant simply names a path that
    was never written and deletes nothing, leaving the real owner's object
    byte-identical.
    """
    path = stage_object_path(state_root, tenant_id, repository_id, content_digest)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ===========================================================================
# PG-8 -- dirty checkout: external copy + marked synthetic snapshot.
# ===========================================================================

DIRTY_CHECKOUT_POLICIES = frozenset({"refuse", "copy_external"})


def require_dirty_policy_allows(policy):
    """Policy-first, fail closed (2.1). Default is 'refuse' -- this
    function itself does not default anything; the caller (tenant record)
    must explicitly pass 'copy_external' to proceed. Wiring the tenant
    record's actual default is the sibling publication module's job; this
    is the fail-closed gate it must call before ever invoking
    capture_dirty_snapshot().
    """
    if policy not in DIRTY_CHECKOUT_POLICIES:
        _refuse("invalid_dirty_checkout_policy", {"policy": policy})
    if policy != "copy_external":
        _refuse("dirty_checkout_refused", {"policy": policy})
    return True


def _parse_porcelain_z(stdout):
    """Parse `git status --porcelain=v1 -z` output into a list of
    {"code": "XY", "path": ...} dicts, correctly skipping the extra
    NUL-terminated original-path field a rename/copy entry (code starting
    R or C) carries.
    """
    parts = stdout.split("\0")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    entries = []
    i = 0
    while i < len(parts):
        raw = parts[i]
        code = raw[:2]
        path = raw[3:]
        entries.append({"code": code, "path": path})
        if code and code[0] in ("R", "C"):
            i += 1  # the following element is the original path; skip it
        i += 1
    return entries


def detect_dirty(customer_path):
    """Read-only dirty-checkout detection: `-uall --ignored=matching`
    EXPLICIT on argv (E6c). `dirty` is True iff any tracked-modified or
    untracked entry exists; an ignored-only repository is NOT considered
    dirty for this function's purposes (ignored files are excluded from
    the copy by default -- see build_copy_list's `include_ignored`).
    """
    proc = customer_git(customer_path, [
        "status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"])
    if proc.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "status --porcelain -uall --ignored=matching",
            "returncode": proc.returncode, "stderr": proc.stderr[:500]})
    entries = _parse_porcelain_z(proc.stdout)
    tracked_or_untracked = [e for e in entries if e["code"] != "!!"]
    ignored = [e for e in entries if e["code"] == "!!"]
    return {
        "dirty": bool(tracked_or_untracked),
        "tracked_or_untracked": tracked_or_untracked,
        "ignored": ignored,
    }


def build_copy_list(customer_path, dirty_status, *, include_ignored=False):
    """Build the copy list from READ-ONLY git output only -- never from
    `git stash`/`git add`/`git archive`, all [VERIFIED] to mutate the
    customer repository (or, for archive, to silently drop the exact dirty
    bytes it exists to capture, since archive always writes HEAD/index
    content).
    """
    lf = customer_git(customer_path, ["ls-files", "-z"])
    if lf.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "ls-files -z",
            "returncode": lf.returncode, "stderr": lf.stderr[:500]})
    tracked = [p for p in lf.stdout.split("\0") if p]
    deleted = {e["path"] for e in dirty_status["tracked_or_untracked"] if "D" in e["code"]}
    files = [p for p in tracked if p not in deleted]
    files += [e["path"] for e in dirty_status["tracked_or_untracked"] if e["code"] == "??"]
    if include_ignored:
        files += [e["path"] for e in dirty_status["ignored"]]
    return sorted(set(files))


def copy_worktree_files(worktree_root, file_list, dest_dir):
    """Copy by FILESYSTEM, never by git (2.3) -- shutil, not `git stash`/
    `git worktree add`/`git archive`. Records a per-file manifest entry
    (path, mode, size, sha256) for the aggregate
    worktree_file_manifest_digest.
    """
    manifest = []
    for rel in file_list:
        src = os.path.join(worktree_root, rel)
        dst = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            target = os.readlink(src)
            if os.path.lexists(dst):
                os.remove(dst)
            os.symlink(target, dst)
            manifest.append({
                "path": rel, "mode": "120000", "size": len(target),
                "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest()})
            continue
        st = os.lstat(src)
        shutil.copy2(src, dst)
        manifest.append({
            "path": rel,
            "mode": "100755" if st.st_mode & 0o111 else "100644",
            "size": st.st_size, "sha256": _sha256_file(dst)})
    manifest.sort(key=lambda e: e["path"])
    return manifest


def worktree_manifest_digest(manifest):
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def acquire_mirror(customer_path, mirror_path):
    """The ONE named exception to customer_git()'s read-only-only
    discipline: acquires an external bare mirror by FETCHING from the
    customer path, never by `git clone --bare <customer>` (E2: hardlinks
    customer loose objects, changing their st_nlink and coupling their
    lifetime to the mirror's -- a `git gc` on the customer repo could no
    longer reclaim them, and deleting the mirror would mutate the
    customer's link counts back).

    `git init --bare <mirror>` first (a write to the NEW mirror path, not
    to the customer repo), then `git fetch <customer> '+refs/heads/*:refs/heads/*'`
    run FROM the mirror -- [VERIFIED, E3] leaves the customer .git
    identical on content, mtime and nlink. Tags are fetched too so a
    detached-tag dirty base still resolves.
    """
    _validate_abs_dir_arg(customer_path, "invalid_customer_path")
    _validate_abs_dir_arg(mirror_path, "invalid_mirror_path")
    if not os.path.isdir(mirror_path) or not os.path.isdir(os.path.join(mirror_path, "objects")):
        init = subprocess.run(
            ["git", "init", "--bare", mirror_path],
            capture_output=True, text=True, env=_sanitized_git_env())
        if init.returncode != 0:
            _refuse("mirror_init_failed", {"stderr": init.stderr[:500]})
    fetch = subprocess.run(
        ["git", "--no-optional-locks", "-C", mirror_path, "fetch", customer_path,
         "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
        capture_output=True, text=True, env=_sanitized_git_env(), timeout=120)
    if fetch.returncode != 0:
        _refuse("mirror_fetch_failed", {"stderr": fetch.stderr[:1000]})
    return mirror_path


def create_workspace(mirror_path, workspace_path):
    """`git clone --shared <mirror> <workspace>` -- [VERIFIED] the
    alternates file `--shared` creates lives in the WORKSPACE (an external
    tmpdir) and points at the MIRROR (also external); neither write
    touches the customer .git. An alternates file appearing INSIDE a
    customer .git would be a FAIL under capture_footprint's `common`
    manifest -- it never happens here because the clone target is the
    workspace, not the customer path.
    """
    _validate_abs_dir_arg(mirror_path, "invalid_mirror_path")
    _validate_abs_dir_arg(workspace_path, "invalid_workspace_path")
    clone = subprocess.run(
        ["git", "clone", "--shared", mirror_path, workspace_path],
        capture_output=True, text=True, env=_sanitized_git_env(), timeout=120)
    if clone.returncode != 0:
        _refuse("workspace_clone_failed", {"stderr": clone.stderr[:1000]})
    return workspace_path


def _wipe_workspace_worktree(workspace_path):
    for name in os.listdir(workspace_path):
        if name == ".git":
            continue
        full = os.path.join(workspace_path, name)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full)
        else:
            os.remove(full)


def capture_dirty_snapshot(customer_path, mirror_path, workspace_path, *, snapshot_id,
                            capture_time, include_ignored=False):
    """The PG-8 mechanism: copy the dirty tree EXTERNALLY and represent it
    with a marked SYNTHETIC snapshot, without mutating customer git
    metadata to describe it (#400 item 12).

      1. detect_dirty()/build_copy_list() -- read-only against customer.
      2. copy_worktree_files() -- filesystem copy into the EXTERNAL
         workspace (already cloned --shared from the EXTERNAL mirror).
      3. `git add -A` / `write-tree` / `commit-tree` INSIDE THE WORKSPACE
         (never the customer repo) with FIXED author/committer identity
         and FIXED dates (`capture_time`, a git date string e.g.
         "<epoch> +0000") -- [VERIFIED, E8] deterministic: the same dirty
         tree captured twice produces the identical commit OID and patch
         digest, which is what makes a synthetic snapshot reproducible
         evidence rather than a nonce.
      4. push the synthetic OID to the retention ref
         `refs/nightshift/snapshots/<snapshot_id>` IN THE MIRROR, and
         bundle it there (`--not <base>`).

      5. discover_closure() against the SYNTHETIC commit, in the
         workspace, and freeze its digest. This is the step that makes
         `verify_closure_complete` mean something: without it the snapshot
         row would carry the empty-manifest digest by default -- a
         truthful "no claim was discovered", but never a refusal. The
         closure is read from the synthetic tree, not from customer HEAD,
         because a dirty tree can add or drop a gitlink.

    Returns a dict shaped to feed directly into record_source_snapshot's
    `fields`: synthetic=1, synthetic_parent_commit_oid, patch_digest,
    dirty_capture_method='external_copy', worktree_file_manifest_digest,
    included_ignored, retention_ref, pack_digest, closure_manifest_digest,
    plus commit_oid/tree_oid for the synthetic commit itself.

    `closure_entries` and `bundle_path`/`worktree_manifest` are NOT
    columns: they are returned beside the fields so the caller can persist
    the entries with `record_closure_entry` (the digest above is the
    digest OF those entries, so a caller that freezes the digest and drops
    the entries is exactly what `verify_closure_complete` refuses).

    Caller's responsibility, not this function's: wrapping the whole call
    in capture_footprint()/compare_footprint() before and after to prove
    the customer checkout stayed untouched, and calling
    require_dirty_policy_allows() first so a 'refuse'-policy tenant never
    reaches this function at all.
    """
    dirty = detect_dirty(customer_path)
    if not dirty["dirty"]:
        _refuse("not_dirty")
    roots = resolve_customer_roots(customer_path)
    if roots["worktree_root"] is None:
        _refuse("dirty_checkout_requires_worktree")
    head = customer_git(customer_path, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        _probe_failed("footprint_probe_failed", {
            "command": "rev-parse HEAD", "returncode": head.returncode,
            "stderr": head.stderr[:500]})
    base = head.stdout.strip()

    file_list = build_copy_list(customer_path, dirty, include_ignored=include_ignored)
    _wipe_workspace_worktree(workspace_path)
    manifest = copy_worktree_files(roots["worktree_root"], file_list, workspace_path)
    manifest_digest = worktree_manifest_digest(manifest)

    identity_env = dict(_sanitized_git_env())
    identity_env.update({
        "GIT_AUTHOR_NAME": "nightshift", "GIT_AUTHOR_EMAIL": "nightshift@invalid",
        "GIT_COMMITTER_NAME": "nightshift", "GIT_COMMITTER_EMAIL": "nightshift@invalid",
        "GIT_AUTHOR_DATE": capture_time, "GIT_COMMITTER_DATE": capture_time,
    })

    def _ws_git(args, timeout=60):
        return subprocess.run(
            ["git", "-C", workspace_path, *args],
            capture_output=True, env=identity_env, timeout=timeout)

    add = _ws_git(["add", "-A"])
    if add.returncode != 0:
        _refuse("synthetic_snapshot_failed", {"step": "add", "stderr": add.stderr.decode(errors="replace")[:500]})
    wt = _ws_git(["write-tree"])
    if wt.returncode != 0:
        _refuse("synthetic_snapshot_failed", {"step": "write-tree", "stderr": wt.stderr.decode(errors="replace")[:500]})
    tree_oid = wt.stdout.decode().strip()
    ct = _ws_git(["commit-tree", tree_oid, "-p", base, "-m",
                   f"nightshift: synthetic dirty snapshot {snapshot_id}"])
    if ct.returncode != 0:
        _refuse("synthetic_snapshot_failed", {"step": "commit-tree", "stderr": ct.stderr.decode(errors="replace")[:500]})
    synthetic_oid = ct.stdout.decode().strip()

    diff = _ws_git(["diff", "--binary", "--no-color", "--no-ext-diff", "--full-index", base, synthetic_oid])
    if diff.returncode != 0:
        _refuse("synthetic_snapshot_failed", {"step": "diff", "stderr": diff.stderr.decode(errors="replace")[:500]})
    patch_digest = hashlib.sha256(diff.stdout).hexdigest()

    retention_ref = f"refs/nightshift/snapshots/{snapshot_id}"
    push = subprocess.run(
        ["git", "-C", workspace_path, "push", mirror_path, f"{synthetic_oid}:{retention_ref}"],
        capture_output=True, text=True, env=identity_env, timeout=60)
    if push.returncode != 0:
        _refuse("mirror_push_failed", {"stderr": push.stderr[:1000]})

    bundle_path = os.path.normpath(os.path.join(mirror_path, "..", f"{snapshot_id}.bundle"))
    bundle = subprocess.run(
        ["git", "-C", mirror_path, "bundle", "create", bundle_path, retention_ref, "--not", base],
        capture_output=True, text=True, timeout=60)
    if bundle.returncode != 0:
        _refuse("bundle_create_failed", {"stderr": bundle.stderr[:1000]})
    pack_digest = _sha256_file(bundle_path)

    closure_entries = discover_closure(workspace_path, rev=synthetic_oid)

    return {
        "commit_oid": synthetic_oid,
        "tree_oid": tree_oid,
        "synthetic": 1,
        "synthetic_parent_commit_oid": base,
        "patch_digest": patch_digest,
        "dirty_capture_method": "external_copy",
        "worktree_file_manifest_digest": manifest_digest,
        "included_ignored": 1 if include_ignored else 0,
        "retention_ref": retention_ref,
        "pack_digest": pack_digest,
        "closure_manifest_digest": closure_manifest_digest(closure_entries),
        "closure_entries": closure_entries,
        "bundle_path": bundle_path,
        "worktree_manifest": manifest,
    }
