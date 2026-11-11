# ADR-0058: Seat→identity mapping lives inside `bin/nightshift-ctl`; `nightshift-workspace` reads it via the same attested-loader pattern `omnipods_policies.py` already uses

Date: 2026-08-31 · Status: accepted
Deciders: crew_alpha lead · Proposers: eng_claude (Option A), eng_codex (Option B)
Conversation: `6f984e1f398145f581d196fcc9e64e8f` (crew_alpha lead session, task `454-build`)
Spec: `docs/prd/430-retire-shared-checkout.md` §4/§7 (PR #453), GitHub issue #454
Issue: #454 (child 1/5 of #430's ADR-0049 decomposition)

## Context

Issue #430 (DoR-accepted 2026-08-31T14:22:40Z) requires that every seat's
working tree be an independent clone with a repo-local git identity derived
deterministically from the seat name -- never ambient identity. #430's PM
split this into 5 children (ADR-0049); this ADR covers child 1/5, issue #454
("canonical path + identity + creation seam"), the only child this delivery
PR builds.

Grounding facts, confirmed by reading, not assumed:

- `bin/nightshift-workspace` (819 lines) already implements `create`/
  `verify`/`recheck` for independent clones. `resolve_destination`
  (`:403-437`) already produces the correct canonical path
  `/home/nightshift/work/<seat>/nightshift` given
  `--root /home/nightshift/work/<seat> --agent nightshift`. It never calls
  `git config user.name`/`user.email` anywhere (0 hits outside a comment) --
  that is the actual net-new surface.
- The module's own docstring and `git_at()`/`write_marker_at()` functions
  establish a hard discipline: every git operation and every git-dir write
  during `create` goes through a held `dir_fd` (fchdir'd before exec), never
  through a pathname (`-C <path>` or `cwd=<path>`), because a pathname is
  re-resolved at use time and can be redirected between when a directory is
  claimed and when it is written to (finding 4, reproduced at commit
  `9aef482`).
- `bin/nightshift-ctl` already defines the org's one trusted seat roster --
  `COUNTED_AGENTS`, `EXEMPT_AGENTS`, `ONCALL_AGENTS` (currently `:211-234`) --
  with an explicit comment: "Static bundle roster shared with the root
  policy. Keeping it in this self-contained controller preserves
  nightshift_publish's attested `python -I -` execution while giving
  allocation and policy one authority."
- `deploy/policies/omnipods_policies.py` does NOT import `nightshift-ctl`
  normally. It loads it via `_load_attested_controller()` (`:41-95`): open by
  explicit relative path with `O_NOFOLLOW`, verify `fstat` is a stable
  regular file across the read (dev/inode/size/mtime/ctime all match before
  and after), `compile()` + `exec()` the bytes into a private module
  namespace, and bind `COUNTED_AGENTS`/`EXEMPT_AGENTS`/`ONCALL_AGENTS` (and
  other functions) from the result. This exists because `nightshift-ctl` is
  executed as isolated source (`python -I -` on stdin) by the publish path
  and must remain a single standalone file -- a normal `import nightshift_ctl`
  would require restructuring it into an importable package, which nothing
  else in this PR's scope needs and which the publish contract does not
  currently allow.
- `docs/CONTROL-PLANE.md`'s "Deployed vs committed" section: only the ROOT
  policy spec is ever loaded by the policy engine; every per-seat
  `program/agents/*/config.yaml` block is proven dead config (#118,
  ADR-0012). Any design that relies on per-seat policy blocks loading is
  building on a component known not to run.

## Options considered

### A -- New shared module `bin/nightshift_seat_identity.py` (proposed by eng_claude)

A new sibling file in `bin/` exposing `seat_identity(seat) -> (name, email)`
(pure function) and `trusted_roster() -> frozenset[str]`. `trusted_roster()`
implements its own independent copy of the attested-read-and-exec technique,
anchored one directory up, to read `COUNTED_AGENTS`/`EXEMPT_AGENTS`/
`ONCALL_AGENTS` out of `bin/nightshift-ctl`'s source. Both
`bin/nightshift-workspace` and `bin/nightshift-ctl` `import` this new module
normally (same-directory import).

Rejected the alternative of moving `_load_attested_controller` itself into
one shared module both `bin/` and `deploy/policies/` import, because no
location is reachable from both without either a fragile cross-directory
import (the thing #454 exists to avoid) or the identical loader machinery
duplicated anyway.

Why it lost: it introduces a THIRD independent implementation of the
attested-loader technique (the existing one in `omnipods_policies.py`, this
new one in `nightshift_seat_identity.py`) whose only purpose is to read
`nightshift-ctl`'s own source -- including when the caller IS
`nightshift-ctl` itself importing the new module, which then turns around
and re-execs `nightshift-ctl`'s own bytes from disk to recover data already
present in its own already-executing module namespace. That is not unsafe,
but it is circular and adds a file whose entire job is indirection.

### B -- `seat_identity()` lives inside `bin/nightshift-ctl`, next to the roster it maps (proposed by eng_codex; ADOPTED)

Add `seat_identity(seat)` directly to `bin/nightshift-ctl`, beside
`COUNTED_AGENTS`/`EXEMPT_AGENTS`/`ONCALL_AGENTS`. No new file.
`bin/nightshift-workspace` gains its own copy of the attested-loader pattern
(open-by-path, `O_NOFOLLOW`, `fstat`-stability check, `compile`+`exec`) --
structurally identical to `omnipods_policies.py`'s existing one, applied to
its sibling `bin/nightshift-ctl` -- and binds
`COUNTED_AGENTS | EXEMPT_AGENTS | ONCALL_AGENTS` and `seat_identity` from
the loaded module.

Why it won: it keeps `bin/nightshift-ctl` as the single normal-Python owner
of the roster AND the pure function derived from it -- cohesive, and
correctness-relevant code (the mapping) sits next to the data it maps over,
not in a third file. The attested-loader PATTERN now exists in two places
(`omnipods_policies.py`, `nightshift-workspace`) instead of one, but that is
the same pattern reused a second time on the same sibling file, not a third
independent one invented for this PR. It also avoids the ouroboros in
Option A: when `nightshift-ctl` itself needs `seat_identity`, it is a plain
in-module function call, not a self-referential re-exec.

### C -- Inline the mapping directly into `nightshift-workspace`, no shared location (considered by eng_claude, rejected early)

Two-line pure function, no new module. Rejected: AC-3's future dispatch-time
identity-assertion policy hook (issue #456, separate child, not built here)
needs the IDENTICAL mapping. Inlining would force a copy-paste the moment
#456 is built, recreating at the mapping layer exactly the hand-copy risk
criterion 4 exists to prevent at the roster layer. A single owner is the
only choice that keeps "same seat -> same identity" a true single source of
truth across all five children of #430's decomposition, not just this one.

## Decision

Option B. `bin/nightshift-ctl` gains `seat_identity(seat) -> (name, email)`
next to its existing roster (formula, fixed by PM as a non-blocking product
decision in the PRD, not reopened here: `user.name="<seat>"`,
`user.email="<seat>@nightshift.seats.invalid"`). `bin/nightshift-workspace`
adds one attested-loader function (parallel to
`omnipods_policies.py:_load_attested_controller`, not shared code with it --
duplicating the ~25-line technique once more is cheaper and safer than
factoring it into a location reachable from both `bin/` and
`deploy/policies/`) and calls it once per `create` invocation to obtain the
live roster and mapping function.

Implementation contract for `cmd_create` (both proposers converged here
independently -- high confidence):

1. `cmd_create` gains a REQUIRED `--seat` argument, separate from `--agent`
   (`--agent` continues to name the destination leaf component; `--seat`
   determines identity only). Required with no default: the tool is
   additive and nothing calls it yet, so there is no back-compat cost, and
   an optional/defaulted `--seat` would reintroduce exactly the "falls back
   to unset identity" failure mode the PRD's failure-states section
   forbids.
2. Seat membership in the trusted roster is checked BEFORE `os.mkdir(dest)`
   -- refuse before creating anything, extending the module's existing
   "Finding-4" discipline. Unmapped seat -> `ok:false`, exit 3, reason
   `unmapped-seat-identity`, no destination created, no fallback to
   ambient/unset git config.
3. `git config user.name`/`user.email` are set via `git_at(dir_fd, [...])`
   -- the same dir_fd-addressed call every other git operation in `create`
   already uses -- inserted AFTER the clone succeeds and BEFORE
   `checkout --detach`. A `git -C <path> config ...` subprocess call is
   explicitly rejected: it is exactly the pathname-race anti-pattern the
   module's own docstring calls out, and identity is exactly as
   write-sensitive as the checkout it sits next to.
4. Identity-setting is INLINE inside `create`, not a separate
   `set-identity` subcommand run afterward. A two-step design reopens the
   exact check/action race the fd-based discipline exists to close, cannot
   honestly satisfy criterion 3's "immediately after creation," and would
   need a second `abandon()`-equivalent for the failure window between the
   two steps.
5. AC-1 criteria 4/5's enumeration test imports the roster and
   `seat_identity` through the real attested loader -- no hand-copied
   roster fixture -- and asserts pairwise-distinct identities, canonical
   paths, and `.git` directories across every seat currently in
   `COUNTED_AGENTS | EXEMPT_AGENTS | ONCALL_AGENTS`. This is a pure,
   no-clone-required test (uniqueness holds by construction given injective
   seat names; the test exists to catch a future regression -- e.g.
   case-folding -- not to prove today's formula). A SEPARATE small
   end-to-end test (one real seat, real `cmd_create`, real
   `git config --local --get`) proves criterion 3's "immediately after
   creation" claim against actual on-disk git state.
6. Negative test for criterion 6 ("creation never opens any path under
   `/srv/project` for writing except as git's `--reference` donor"):
   ADOPTED MECHANISM is a `strace -f` syscall trace (eng_codex's proposal,
   independently converged on by QA's own test plan -- see below), run
   across the whole `cmd_create` invocation including every git subprocess
   it spawns. Parse traced `open`/`openat`/`openat2`/`creat`/`rename*`/
   `unlink*`/`mkdir*`/`link*`/`symlink*` calls; fail on any resolved-
   `/srv/project` target opened with write/create/truncate/append intent.
   Additionally assert `/srv/project` appears in the clone's argv only as
   the value immediately following `--reference`/`--reference-if-able`.
   REJECTED: pure Python-level instrumentation (monkeypatching
   `os.open`/`subprocess.run` inside the test process, eng_claude's
   proposal) as the sole/primary mechanism -- it cannot see file opens
   performed inside the `git` binary's own process (a separate subprocess),
   which is the actual risk surface for this criterion; it only catches
   Python-level opens and argv shape, not real syscalls. `strace` is a hard
   CI requirement for this specific test, not a silent-skip capability --
   per QA's own test plan, "CI must install it or this criterion is not
   gated." A read-only bind-mount of `/srv/project` is worth adding as an
   independent belt-and-suspenders integration check, but its failure mode
   (a generic `EROFS` somewhere inside a git subprocess) is much harder to
   localize than the instrumented trace and does not substitute for it.
7. No changes to `deploy/policies/omnipods_policies.py` in this PR -- its
   existing loader is untouched; `nightshift-workspace`'s new loader is a
   parallel, independent instance of the same pattern, not a shared import
   from it.

## Consequences

Makes easy: a single normal-Python location (`bin/nightshift-ctl`) owns both
the trusted roster and the pure identity function derived from it; issue
#456 (AC-3, dispatch-time identity assertion, a separate child) can call
`seat_identity` from the same place with no new roster copy. The
attested-loader pattern now has two proven, independent applications
(`omnipods_policies.py` and `nightshift-workspace`) instead of one,
strengthening the case that it is the org's standard answer to "read
`nightshift-ctl`'s trusted data from a sibling file without importing it as
a library" -- future consumers (e.g. a future dispatch-time policy hook)
have a second worked example to copy, not just one.

Makes hard: `bin/nightshift-ctl` continues to grow as the single locus of
roster-derived logic; a future change that wants to add heavier
seat-derived logic (not just a 2-field tuple) has to weigh growing that
file further against finally factoring the attested-loader technique into a
properly shared location -- deferred here, not foreclosed.

Forecloses (for now): treating `bin/nightshift-ctl` as import-of-record
from other Python modules via a normal `import` statement -- every consumer
outside `deploy/policies/` and (as of this PR) `bin/nightshift-workspace`
must use the attested-loader pattern too, or the file's standalone-execution
contract for `nightshift_publish` is at risk of drifting.

Known residual risk, not closed by this PR (named by eng_claude, not in
dispute): nothing in `create` cross-checks `--seat` against `--root`'s
trailing path component. A caller could pass a `--seat` that does not match
the canonical path it is writing into, producing an internally consistent-
looking but semantically wrong workspace (right path, wrong identity).
Closing this needs the future dispatch/launcher integration (out of scope
for every child of #430 per the DoR's own sequencing note) to be the single
caller that constructs both `--root` and `--seat` from one seat name, never
two independently-supplied strings. Recorded here so the launcher-cutover
work (crew_beta-owned, needs a lease, tracked separately) does not
under-scope it as "just call the existing tool."

## Reversal trigger

If `bin/nightshift-ctl` is ever restructured into an importable package
(e.g. to support a genuinely different publish/attestation model), revisit
this decision -- the entire justification for the attested-loader pattern
over a normal import is `nightshift-ctl`'s current standalone-execution
constraint, and if that constraint is lifted, Option A's normal-import
approach (or a plain shared module) becomes strictly simpler and should
replace this one via a superseding ADR, not a silent edit.

## Deciders

crew_alpha lead, this session (task `454-build`). Independent competing
proposals from eng_claude (Option A) and eng_codex (Option B), reconciled by
the lead. No dispute between proposers on the CLI surface (`--seat`
required, separate from `--agent`), the inline dir_fd-addressed git-config
placement, or the enumeration-test shape -- those three points are adopted
verbatim from both. The one real disagreement (module location) and the one
real disagreement (negative-test mechanism) are each resolved above with the
losing option's reasoning kept, not deleted.
