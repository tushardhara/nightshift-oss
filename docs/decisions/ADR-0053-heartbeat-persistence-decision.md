# ADR-0053: A pure, fail-closed persistence decision gates every heartbeat-attributed `pr-create`

Date: 2026-08-30 · Status: proposed
Deciders: crew_alpha lead (single-author under disclosed WA-5, see Process note) ·
Proposers of record: none dispatched this round (see Process note)
Conversation: `390-build` · Spec: `docs/prd/390-heartbeat-persistence-dor.md` (PR #397,
accepted DoR, verdict READY for the trimmed AC-1/AC-4–AC-13 scope)
Issue: #390 · Refs #174 (reads only, no fence changes), #286/#388/PR #387 (delegated to,
not duplicated) · Follow-up: #398 (AC-2/AC-3/AC-14, blocked-by #390) · Supersedes: none

## Process note — single-author under WA-5, disclosed

This crew's charter requires two independently-dispatched, different-vendor engineers to
propose before any ADR. The charter for this exact task pre-declared **WA-5 applies**
("single-author under a slot you hold, disclose in the PR body, cross-crew review
mandatory"), citing this run's standing issue #236 (grandchild/slot-bearing
`sys_session_send` dispatch is sterile — reconfirmed structurally this session by
inspecting the live `sys_session_send` tool schema itself: its `args` object has no
`task_id`/`slot_id`/`generation` properties, `additionalProperties: false`, the exact
shape every recent Seam A ADR in this repo's history — ADR-0044, ADR-0052 — hit
identically). Per Working Agreement #5/#6: the lead builds directly under a held slot
(`crew_alpha/eng_claude`, task `390-build`, slot 24, generation 1), in-crew
QA/review did not happen, and cross-crew review is mandatory rather than customary — the
PR this ADR ships with states so explicitly, and nobody on this crew signs its own
acceptance criteria.

## Context

Issue #390's own DoR (PR #397) already reduced the design space to one concrete question:
where does the mechanical "may this heartbeat write to Git" check actually live, given the
real code at checkpoint 24 (`22d68169…`)? Two things forced a specific answer rather than
the DoR's own illustrative sketch:

1. **`cmd_pr_create` is deliberately `PLANE_FREE` today**, and for a real, load-bearing
   reason recorded in `bin/nightshift-ctl`'s own comment: it "closes the --body-file hole"
   and "a linter must run anywhere… including a checkout with no plane at all." Any design
   that makes `pr-create` unconditionally plane-required breaks that guarantee for every
   ordinary engineer PR, which is exactly what AC-12 forbids.
2. **The DoR's own two CLI verbs (`decide`, `record`) needed a real division of labor**,
   not just two names: AC-13 requires a telemetry row for *every* heartbeat outcome,
   no-op included, which means something has to persist unconditionally; AC-1's "zero Git
   artifacts" wrapper-level test is cleanest when that same command is provably incapable
   of touching Git or the plane at all.

## Options considered

Two-vendor proposals were not obtained (see Process note). The two live design questions
below are recorded as a single-author choice with the specific reasoning, per this crew's
disclosed fallback — the same substitute ADR-0044 §3b and ADR-0052 used under the identical
constraint.

### A — Make `pr-create` unconditionally plane-required once #390 lands (rejected)

Simplest to implement: always open `control.db` in `cmd_pr_create`, always check for a
receipt. **Rejected**: breaks the documented reason `pr-create` is plane-free today (must
run from a bare worktree with no control plane), and fails AC-12's "byte-for-byte
unaffected when the marker is absent" bar outright — every ordinary PR would suddenly
require a control plane it never needed before.

### B — Gate `pr-create`'s plane requirement on the *parsed args*, not the static group (adopted)

`is_plane_free(args)` already exists as a function of the fully-parsed `args`, called after
argparse has already resolved every flag on this specific invocation. Making it check
`args.heartbeat_persist_receipt` — present → plane-required for *this call only*; absent →
identical to pre-#390 behavior — needs no new mechanism, reuses the exact attachment point
this codebase already trusts (`is_plane_free`, `PLANE_FREE_COMMANDS`/`PLANE_FREE_VERBS`),
and is a one-line, additive, easily-reviewed change. Adopted.

### C — A separate `pr-create-heartbeat` command instead of one flag on the existing verb (rejected)

Would keep `pr-create` untouched at the cost of a second, nearly-identical body-lint/exec
path to keep in sync forever — exactly the "two places doing one job" drift this org's own
review conventions (`bin/nightshift_review_receipts.py`'s docstring, cited in ADR-0052)
warn against. One flag on one verb, gated by option B's mechanism, is strictly less
surface.

### D — Consume the receipt by `input_digest` string match (rejected) vs. by row `id` (adopted)

The DoR's own illustrative text names `--heartbeat-persist-receipt <digest>`. In practice,
`input_digest = sha256(canonical_json(outcome))` is **not guaranteed unique per row** — the
same outcome recorded twice (e.g. two heartbeats observing the identical no-op state)
produces the identical digest, so consuming "the row with this digest" is ambiguous the
moment two rows share one. The `heartbeat_persist_decisions` table already has an
`AUTOINCREMENT` primary key with no such collision risk, and the codebase's own established
one-shot-consumption idiom (`program_action_permits`) already keys on something
call-site-unique, not a content hash. **Adopted: the receipt is the row's `id`.** The
digest is still computed, returned, and stored — it remains useful as an audit/debugging
correlator — but it is not the consuming key.

## Decision

1. **New module** `bin/nightshift_heartbeat_persistence.py` — pure, no I/O, no `cx`,
   mirroring `nightshift_bookkeeping_release.py`'s split. `decide(outcome) -> {should_
   persist_to_git, reason_code, route, input_digest}`. Two closed enums
   (`OBSERVATION_CODES`, 16 members verbatim from issue #390's own list;
   `DURABLE_DELTA_REASON_CODES`, 3 members: `governed_pr_ready`,
   `adr_accepted_new_content`, `bookkeeping_only_diff_present`). Fail-closed default:
   `should_persist_to_git=False` unless `durable_delta_reason_code` is a **recognized**
   member AND `diff_is_timestamp_only` is not `True`; `authority_status != "granted"`
   (including malformed values) short-circuits to `False` before anything else is
   evaluated, matching ADR-0044 §7b step 1's "zero dispatches" rule for a losing Program.
   Never raises on structurally malformed input.
2. **New `nightshift-ctl heartbeat-persist {decide, record}` group.** `decide` is wired
   into `PLANE_FREE_VERBS` (verb-granular, same shape as `bookkeeping verify-head`) — it
   cannot touch `control.db` even in principle. `record` is plane-required: it computes the
   same decision and inserts exactly one append-only row into the new
   `heartbeat_persist_decisions` table (migration sequence 4, allocated under a `serialize`
   lease on `bin/nightshift-ctl`, matching PR #387's own precedent for a schema-adding
   change to this file) — for every outcome, no-op or durable-delta alike (AC-13). Its
   response carries `receipt_id`.
3. **`cmd_pr_create` gains one optional flag**, `--heartbeat-persist-receipt <id>`. Absent:
   zero behavior change (AC-12, structurally proven — `cx` is `None` for the entire call,
   exactly as before this issue). Present: `is_plane_free` returns `False` for *this specific
   invocation only* (Option B), and before `gh` is ever exec'd, a compare-and-set `UPDATE …
   WHERE id=? AND should_persist=1 AND consumed_at IS NULL` must affect exactly one row or
   the call is denied (exit 3, no bytes reach GitHub) — the identical one-shot idiom
   `program_action_permits` already established for ADR-0044's dispatch permits.
4. **Delegation, not duplication (AC-11):** `route="bookkeeping_fast_path"` is purely
   informational for the caller — it names which existing, already-shipped chain
   (`nightshift-ctl bookkeeping plan → verify-head → receipt-record`, PR #387) to call
   next. The enforcement gate on `pr-create` does not inspect `route` at all — only
   `should_persist=1` and one-shot consumption — so the gate applies uniformly regardless
   of which downstream path a granted decision recommends.
5. **No new authority read.** `decide()`'s `authority_status` input is expected to be
   sourced from the existing, unmodified `nightshift-ctl authority acquire/show` /
   `schedule observe --status standdown` surfaces (ADR-0044). This ADR adds no column, no
   verb, and no argument to any of ADR-0044's tables or commands.

## Consequences

**Makes easy:** a mechanical, testable answer to "does this heartbeat need a Git change at
all", enforced at the one place every heartbeat-authored PR in this repo's own cited
history (#389, #382, #373, …) actually goes through (`cmd_pr_create`), not merely narrated
in a prompt. A no-op heartbeat's telemetry row and a durable-delta heartbeat's telemetry
row are now the same table, queryable, so AC-13's invariant is a `SELECT`, not a claim.

**Makes hard / forecloses:** does not itself wire the Program's own scheduled-task prompt
to call `heartbeat-persist decide`/`record` before dispatching — only the program can edit
that prompt (`sys_scheduled_task_update`), exactly the same boundary ADR-0044 §7 drew for
its own analogous case. Named as a `.omni/tech-debt.md` row, not silently assumed. Does not
gate `cmd_checkpoint_create` — the DoR left this open and the historical evidence (§0 of the
DoR) shows every cited no-op PR went through `pr-create`, so this ADR ships the one
mechanically-justified hook and leaves a second as a named, non-blocking option for a future
ADR if evidence ever shows checkpoint creation is itself part of a no-op heartbeat's Git
footprint.

## Cost

One new SQLite table inside the existing `control.db` (append-only, growth bounded by
heartbeat frequency), one new ~200-line Python module, two new CLI verbs, one new optional
flag on an existing verb. No new dependency, no new service, no new scheduled job.

## One-way door?

No. Additive only — no existing table altered, no existing command's default behavior
changed for a caller that never passes the new flag. Reversible by dropping the table and
the flag; nothing durable depends on `heartbeat_persist_decisions` rows outliving a single
heartbeat's audit trail.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — one row per heartbeat run, bounded by the
scheduler's own cadence, not by request volume.

## Reversal trigger

Revisit if: (a) issue #236 clears — this ADR's single-author process gets a real two-vendor
dispatch on its next non-trivial change, per this org's standing WA-5 reversal rule; (b) the
program's own scheduled-task prompt wiring (named above, out of scope here) finds
`cmd_pr_create` is not actually the only mutating surface a heartbeat reaches — e.g. if a
future heartbeat shape opens a PR through some path other than `nightshift-ctl pr-create`,
this gate would need a second attachment point; (c) `DURABLE_DELTA_REASON_CODES`'s three
members prove insufficient in practice and need a fourth — extend the closed set in this
module, never by accepting a free-text reason at a call site.
