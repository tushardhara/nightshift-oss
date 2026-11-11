# PRD: #430 — Retire the shared checkout: one working tree per seat, preflight fails closed

Status: **L parent, decomposed per ADR-0049 — this PR delivers child 1 of 5**
Owner: crew_alpha PM · Seam: **A** (`bin/nightshift-ctl` / `bin/nightshift-workspace` /
`deploy/policies/**` / coordination kernel, per `docs/owners.md`)
Base: `origin/main @ adcef2fca434e390f0574968341c8f6a70910f21` (same SHA the
2026-08-31T14:22:40Z DoR verdict on #430 was verified against)
Refs #430 (L, tracking container — never gets `state:ready`, never its own
`task upsert` row, per ADR-0049 §Decision). This doc is the single source of
truth for the split; `#430`'s own body/comments are superseded here wherever
they differ, per the DoR verdict's own supersession rule.

---

## 0. Grounding — what's actually in the repo today

- `bin/nightshift-workspace` (819 lines) already implements `create` /
  `verify` / `recheck` for independent per-agent clones
  (`git clone --reference /srv/project`, `--no-checkout` then
  `checkout --detach <explicit-sha>`), fchdir/O_NOFOLLOW race-safe creation,
  a write marker, and a `verify` that positively rejects a linked worktree
  (`git_dir_private` check, `bin/nightshift-workspace:565-568`) as well as a
  generic shared checkout wearing a private `.git` (the marker check,
  `:576-593`). It is additive; **nothing calls it yet** (module docstring,
  `bin/nightshift-workspace:1-56`).
- `cmd_create`'s destination is `root/agent`
  (`bin/nightshift-workspace:403-437`, `resolve_destination`). Given
  `--root /home/nightshift/work/<seat>` and `--agent nightshift`, the
  existing, unmodified path-resolution code already produces exactly the
  DoR's canonical path `/home/nightshift/work/<seat>/nightshift` — **no
  change needed to the path-computation logic itself.**
- **Gap, confirmed by reading, not assumed:** `cmd_create` never calls
  `git config user.name` / `user.email` anywhere in the file (`grep -n
  "user\.name\|user\.email" bin/nightshift-workspace` → 0 hits outside a
  comment at `:106`). AC-1's identity clause is genuinely new work, not
  "just wiring."
- `bin/nightshift-ctl:1828` `cmd_checkpoint_create`: when `--sha` is not
  passed, line ~1839 runs `sha = _git(args.repo, "rev-parse", "HEAD")` —
  ambient HEAD of `args.repo`, never `args.ref`. Confirmed exactly as the
  lead described. `--ref` (`bin/nightshift-ctl:4689`) is otherwise only used
  for the `merge-base --is-ancestor` containment check
  (`:1855-1867`), not for resolving the recorded sha.
- `tests/test_checkpoint.py::Checkpoints::test_worktree_pinned_to_checkpoint_sha`
  (`:33-49`) calls `checkpoint create` **without** `--sha`. It currently
  passes only because the test never diverges `HEAD` from `--ref main` before
  that call — it is not a real test of ref-resolution, it happens to get the
  right answer by coincidence of both pointing at the same commit. This is
  the "existing regression test [that] needs updating" the DoR flagged: it
  must be extended (or a sibling test added) so `HEAD` and `--ref` diverge
  and the assertion is on the `--ref`-resolved sha, not on an ambient state
  that happens to match.
- `deploy/policies/omnipods_policies.py` gives the established shape for a
  new dispatch-time policy hook: `worker_semaphore` (`:178-...`) fails
  **closed** on a missing/unreadable control-plane db
  (`:238-247`, unless the explicit `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1` escape
  hatch), loads its trusted roster from the attested `nightshift-ctl`
  controller rather than hand-copying it (`:93-95`, `_load_attested_controller`),
  and returns a structured `_deny(reason)` rather than raising. AC-2's and
  AC-3's control-plane checks should follow this exact pattern, not invent a
  new one.
- `docs/owners.md`: `bin/nightshift-ctl`, `bin/nightshift-workspace`,
  `deploy/policies/**`, `docs/CONTROL-PLANE.md` are crew_alpha's, no lease
  needed. `deploy/**` (except `policies/`) and `tests/linux/**` are
  crew_beta's — this PR does not touch them.
- `tests/host-uid-skip-manifest.json` names exactly 12
  `test_project_bind.ProjectTrustedAncestry.*` ids as the only tests
  permitted to skip under the uid-remap harness. No AC in this PR touches
  that class; no new manifest entries are expected.

## 1. Outcome (unchanged from #430, restated for traceability)

No two seats resolve to the same working tree; an attempt to run one where
they would is refused before any work starts; `/srv/project` remains a
read-only reference/object-donor forever. #430's Done-when is not satisfied,
and #430 is not closable, until #459 lands alongside #454-458 — the five
children here deliver the check logic and identity seam, but #459 is the
only tracked item that wires that logic into the actual startup-time refusal
this Done-when requires.

## 2. Accepted contract — status of each tightened AC against this split

| AC | Tightened summary | Status this issue |
|----|---|---|
| AC-1 | canonical path + independent clone + repo-local identity + configured/live uniqueness | **IN — this PR (child 1/5)** |
| AC-2 | live-seat collision **check logic**, control-plane-backed, realpath-canonicalized | Child 3/5 (standalone subcommand only; wiring into `deploy/preflight.sh`'s startup call tracked as **#459** (crew_beta-owned, not a child of this decomposition) |
| AC-3 | dispatch-time identity assertion via throwaway commit + policy hook | Child 2/5 |
| AC-4 | atomic branch-ownership CAS record | **Excluded from #430 entirely.** Needs a new ADR (the DoR verdict itself calls this "a new atomic seam"), which per this org's standing rule must clear `staff` review before any crew builds on it. Tracked as a new linked follow-up issue, not a child of this decomposition. |
| AC-5 | `checkpoint create --ref` records the resolved ref, not ambient HEAD | Child 4/5 |
| AC-6 | #89-shaped regression fixture against the new arrangement | Child 5/5 |
| AC-7 | canonical full-suite command, host + uid-remap, no new off-manifest skip | Applies to **every** child's own PR individually (each child's PR description must cite the literal commands run for both), not a separate task |

## 3. Decomposition (ADR-0049) — 5 children, real dependency edges only

`#430` stays open as the tracking container. It gets **no** `state:ready`
label, **no** size relabel off `L`, and **no** `task upsert` row of its own.
Each child below gets its own GitHub issue (Backlog-item template),
`crew_alpha` + `P1` + size labels, and passes DoR independently before it is
`state:ready`.

```
430-1 canonical-path + identity + creation seam (AC-1)         [M]  — no deps
   │
   ├──▶ 430-3 live-seat uniqueness check logic (AC-2, standalone) [S]  deps: 430-1
   │        │
   │        └──▶ 430-2 dispatch-time identity assertion (AC-3)    [M]  deps: 430-1, 430-3
   │                 │
   │                 └──▶ 430-4 checkpoint --ref fix (AC-5)        [S]  deps: 430-2
   │
   └───────────────────────────────────────────▶ 430-5 #89 regression fixture (AC-6) [M]
                                                        deps: 430-1, 430-2, 430-3
```
(`430-N` above are placeholder slugs — real GitHub issue numbers are minted
in §8 and substituted there. `430-4`'s dependency on `430-2` and `430-3`→`430-2`'s
ordering are **not** functional dependencies — AC-5's fix is code-isolated
from AC-1/2/3 — they are file-sharing sequencing edges: `430-2`, `430-3`, and
`430-4` all add a new subcommand/hook inside `bin/nightshift-ctl` and/or
`deploy/policies/omnipods_policies.py`, and per the #174 precedent
ADR-0049 itself cites, "anything sharing a file goes sequentially, not in
parallel worktrees." `430-5`'s dependency on `430-1`/`430-2`/`430-3` is a real
interface dependency: its fixture needs actual per-seat clones, real
identities, and the live-check logic to exist before it can reproduce the
#89 shape against the *new* arrangement.)

This order matches the DoR verdict's own non-blocking sequencing note:
*canonical path/identity → creation/preflight → dispatch/launcher cutover →
checkpoint correction → #89 regression.*

## 4. THIS PR's scope — child 1/5: canonical path, identity, creation seam

**Delivers AC-1 in full.** Size **M**.

### 4.1 Numbered acceptance criteria

1. **(AC-1 clause: canonical path.)** For a seat name `S` valid under the
   existing seat-name validation in `bin/nightshift-workspace`
   (`AGENT_RE`), invoking the new creation seam for `S` produces (or, if a
   valid one already exists, leaves byte-for-byte unchanged) a working tree
   whose `os.path.realpath` is exactly `/home/nightshift/work/<S>/nightshift`.
   Checkable: unit test asserting the realpath equality for a sample seat.
2. **(AC-1 clause: independent clone, not a linked worktree.)** The created
   tree's absolute `.git` directory resolves beneath that same canonical
   path. Checkable directly by running the existing
   `bin/nightshift-workspace verify --path <canonical path> --expect-sha
   <sha>` against the result and asserting `ok: true` and the payload's
   `git_dir_private: true` — this is exactly what `verify`'s existing
   `git_dir_private` check (`bin/nightshift-workspace:565-568`) already
   proves; this PR needs a caller and a test that exercises the seam
   end-to-end, not a new proof.
3. **(AC-1 clause: repo-local identity.)** Immediately after creation, the
   tree's repo-local `user.name` and `user.email` (`git config
   --local --get user.name` / `user.email`, run inside the created tree)
   equal the values produced by this PR's documented seat→identity mapping
   (§7) for `S`.
4. **(AC-1 clause: mapping is a documented, deterministic pure function.)**
   Calling the mapping for the same `S` twice returns identical values; a
   test enumerates every seat name in the roster the control plane already
   trusts (`COUNTED_AGENTS` / `ONCALL_AGENTS` / `EXEMPT_AGENTS`,
   `deploy/policies/omnipods_policies.py:93-95` — reused, not hand-copied,
   per the same principle line 103 of that file already states) and asserts
   all resulting `(user.name, user.email)` pairs are pairwise distinct.
5. **(AC-1 clause: configured-seat uniqueness.)** The same enumeration test
   asserts all resulting canonical paths are pairwise distinct and all
   resulting `.git` directories are pairwise distinct. (This holds by
   construction given criterion 1's formula and injective seat names; the
   test exists to catch a future change that breaks that property, e.g. a
   case-folding filesystem or a seat-name collision, not because the
   current formula is expected to fail it.)
6. **(#107's fix-shape guardrail, negative criterion.)** Creation never
   reads or writes any path under `/srv/project` except as `git`'s
   `--reference` object-donor argument to the clone itself — checkable by
   asserting the created tree's `git rev-parse --show-toplevel` is under
   `/home/nightshift/work/<S>/nightshift` and never `/srv/project`, and by
   a test that fails if creation opens any `/srv/project` path for writing.
7. **(Preserve existing failure semantics.)** A partial-create failure
   (disk, permission, clone failure) removes only the newly-created partial
   directory for `S`, exits non-zero, and names the seat, the path, and the
   reason — this is `bin/nightshift-workspace`'s existing `abandon()`
   behavior (`:481-509`); this PR's wrapper must surface those same fields
   unmodified, not re-wrap or swallow them.

### 4.2 Failure and empty states

- **Partial-create failure:** as criterion 7 above — no fallback to a
  shared tree, ever.
- **Path already occupied by something that isn't a valid workspace for
  that seat:** refused (`ok:false`, exit 3), reusing
  `bin/nightshift-workspace`'s existing `workspace-exists` /
  `workspace-path-is-symlink` / `workspace-escapes-root` reasons —
  no new reason vocabulary invented where an existing one already fits.
- **Seat name has no entry in the identity mapping** (a name that passes
  `AGENT_RE` but isn't in the trusted roster): refused (`ok:false`, exit 3,
  reason `unmapped-seat-identity`) — never falls back to ambient/unset git
  config.
- **"Stale" classification (canonical path exists, no live run owns it):**
  **explicitly out of scope for this child.** "Live" is a control-plane
  question (AC-2), not yet built. This child only ever answers "does a
  valid workspace already exist for this seat," never "is it currently in
  use" — conflating the two here would be answering a question this PR
  cannot yet ground truthfully. Closed by child `430-3`.

### 4.3 Out of scope (explicit)

- Live-seat collision detection (AC-2 logic) — child `430-3`.
- Dispatch-time identity assertion via throwaway commit (AC-3) — child `430-2`.
- `checkpoint create --ref` fix (AC-5) — child `430-4`.
- #89 regression fixture (AC-6) — child `430-5`.
- Atomic branch-ownership CAS record (AC-4) — excluded from #430 entirely
  pending a new ADR + `staff` review (see §2).
- Wiring any live-seat check into `deploy/preflight.sh` / `deploy/bootstrap.sh`'s
  actual startup call path, and the horizontal dispatch/launcher cutover —
  both need a crew_beta-granted lease on `deploy/**` (not `policies/`),
  requested separately (see §9); not assumed here.
- Migrating existing ad hoc paths (`/home/nightshift/staff-176/`,
  `/home/nightshift/work/121-preflight`) onto this convention — a data
  migration, not a code change; deliberately not bundled into this PR's
  diff.
- Deleting or reclaiming stale working trees — #433's, unchanged.

### 4.4 Dependencies

None. This is the first child in build order — it unblocks `430-2`,
`430-3`, and `430-5`.

### 4.5 Open questions

1. Exact seat→identity mapping formula (domain suffix for synthetic
   emails). **Resolved by this PM, non-blocking** — see §7. A technical
   naming choice with no customer-facing or legal exposure; revisable by a
   later PR without migration cost since it is a pure function, not stored
   state.
2. Source of the seat-enumeration list for criteria 4/5's test.
   **Resolved:** reuse the existing attested roster
   (`deploy/policies/omnipods_policies.py:93-95`) rather than a second
   hand-copied list — non-blocking.

## 5. Not in this PR — full list, with reasons

- **AC-4 (branch-ownership CAS), in full.** The DoR verdict itself says
  this "needs a new atomic seam" and a written decision on whether
  `release`'s integration ref is exempt from or exclusively owned under it.
  Per this org's standing rule, a new ADR goes to `staff` before any crew
  builds on it — building past that gate now would be exactly the
  "just charter it anyway" bypass ADR-0049 exists to prevent. Tracked as a
  new linked follow-up issue once the ADR is written and reviewed.
- **AC-2's / AC-4's wiring into `deploy/preflight.sh` or
  `deploy/bootstrap.sh`'s actual startup/dispatch call path**, and the
  **horizontal dispatch/launcher cutover** the DoR's sequencing note calls
  for. `deploy/**` (except `policies/`) is crew_beta's, single-writer, per
  `docs/owners.md`. This PR (and its four siblings) deliver the crew_alpha
  side — the check logic itself, standalone and tested — but do not call
  it from crew_beta's files. Requesting a lease is the lead's/program's
  call, not mine to assume; flagged in §9.
- **Children `430-2` through `430-5`** are fully specified in this PRD
  (§3, plus each gets its own GitHub issue) but are not this PR's diff —
  each becomes `state:ready` in its own turn, gated by its own dependency
  edge above.

## 6. DoR self-check — child `430-1` (the task this PR delivers)

1. Outcome as observable behaviour, not a solution — **pass** (§4.1
   criteria are all "resolves to X" / "config equals Y" / "test asserts Z",
   no "add a field").
2. Numbered acceptance criteria, each checkable by a test or a named manual
   step, none containing "properly"/"correctly"/"as expected" — **pass**
   (§4.1, 7 criteria, each with a stated check).
3. Failure and empty states specified — **pass** (§4.2, four states named
   including the explicit out-of-scope "stale" boundary).
4. Out of scope listed explicitly — **pass** (§4.3, eight items named with
   reasons).
5. Dependencies named — **pass** (§4.4: none; first in build order).
6. Open questions answered or marked non-blocking — **pass** (§4.5, both
   resolved by PM, non-blocking, revisable).

**Verdict: READY.** No gap found against my own six criteria.

## 7. Product decision made here, non-blocking (§4.5.1)

Seat→identity mapping, documented as the single source of truth for this
and all later children that read it:

    user.name  = "<seat>"
    user.email = "<seat>@nightshift.seats.invalid"

`.invalid` is the RFC 2606 reserved TLD reserved exactly for identifiers
that must never resolve or deliver — appropriate here since these are git
commit identities, not contact addresses. Pure function of the seat name
alone; no lookup table to keep in sync as seats are added or renamed. This
is an internal identifier, not a customer-facing name/pricing/legal
decision, so it does not go to `.omni/decisions.md` — I own it as a normal
technical call and it is cheaply revisable (no stored state migrates; the
next commit made under a changed formula just carries the new identity).

## 8. Task IDs and sizes (see report back to lead)

See the accompanying report for the minted GitHub issue numbers, their
`<issue>-build` task ids, and which single task id is this turn's delivery
task, per the charter's "exactly one task id this turn" requirement.

## 9. Flag to the lead — nothing under- or over-scoped found

Reviewed the lead's proposed slice against the DoR-accepted contract and
the actual code (§0). I agree with it as stated, with one addition: the
lead's slice described AC-1 as "wiring bin/nightshift-workspace as the
creation seam," which reads as though the path-resolution half were the
open item. Reading the code (§0) shows path-resolution needs no change at
all — the entire net-new surface in AC-1 is the identity-configuration half
(`git config user.name`/`user.email`), which the current tool does not do
anywhere. Not a scope change, just naming the actual delta precisely so the
implementer doesn't spend time re-deriving a path scheme that already
works. No AC I'd add now that isn't already in the lead's IN list; no AC I'd
pull out of it either.
