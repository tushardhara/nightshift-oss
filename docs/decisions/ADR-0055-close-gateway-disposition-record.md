# ADR-0055: A ticket closes only through a gateway that records its disposition first

Date: 2026-08-31 · Status: accepted (staff-reviewed; the one wider question staff
raised was settled by `program` — see "Program-owned close paths" under Consequences)
· Deciders: crew_alpha lead, with `staff` (design review) and `program` (the
`program/config.yaml` sequencing call)
Proposers: **none — see "Process deviation" below**
Conversation: `d7d3faa281f443b5a2bffffaa42326b8`
Issue: #424 · Base SHA: `53905e39544bbb00552719d1d41569f65bb0952c` (checkpoint 29, wave `424-build`)
Spec: issue #424's body plus its DoR comment (run_id `fixture-review-231`) — the accepted contract

> **This ADR is a gate, not a plan.** No implementation may start against it until
> `staff` has reviewed it, because it introduces a new `control.db` migration and a
> new table. That is the standing rule and it is the entire reason this dispatch
> exists.

## Process deviation — read this first

The mandatory `design-debate` phase could **not** be run. Both vendor binaries
resolved in preflight (`claude`, `codex`), so cross-vendor debate was expected to be
available, but **every** worker dispatch is currently impossible from a crew-lead
session:

- The `sys_session_send` tool schema declares `additionalProperties: false` and
  rejects `slot_id` / `task_id` / `generation`:
  `Input validation error: Additional properties are not allowed ('slot_id' was unexpected)`
- The admission policy simultaneously **requires** that field:
  `Denied by policy: args.slot_id must be a strict positive integer`

These two rules cannot both be satisfied. This is live defect **#236** (re-filed from
#13, root-caused verbatim in #178's final comment) — the exact "diagnosed twice,
paid for twice" pathology that issue #424 itself cites as motivation. Planning slot 40
was acquired, proved unusable, and released without being consumed.

Consequences for this document, stated plainly so a reader can discount it correctly:

1. **No independent second opinion was obtained.** The options below are the crew
   lead's own analysis of the codebase, not two vendors proposing independently.
   Nothing here should be read as having survived an adversarial review.
2. **No QA test plan informed the design.** The testability seams below were derived
   by reading the existing suite directly. QA has not confirmed that AC-8 is
   satisfiable in the shape proposed.
3. `staff` review is therefore the **first and only** independent check this design
   will have received before build. It should be weighted accordingly — and QA should
   be asked to confirm the "Testability seams" section before an engineer is dispatched.

This deviation is disclosed rather than papered over. Proceeding to draft was the
right call because the deliverable is docs-only and reversible, and the staff gate
still stands between this document and any code.

## Context

A closed issue in this repo does not mean delivered work. Measured 2026-08-30 across
165 issues: 58 `COMPLETED`, 79 `NOT_PLANNED`, 12 `DUPLICATE` — and **62 issues closed
inside one 60-second window** on 2026-08-26. That batch swept live defects, including
two P0 security issues (#118, #129) and a P0 enforcement gap (#158) closed with no
closing comment at all.

The cost is not tidiness. The Program cannot distinguish an abandoned defect from a
consolidated one, so it re-discovers P0s it already diagnosed (#13 → #236; #178's
diagnosis re-paid three days later). Any future throughput claim is unmeasurable
because the denominator is untrustworthy.

Constraints that were real on this date:

- **GitHub's `stateReason` vocabulary is fixed** at `COMPLETED` / `NOT_PLANNED` /
  `DUPLICATE` and is out of scope to change. Our three dispositions must therefore
  live *beside* it, not inside it.
- **This repo has zero GitHub Milestone objects** (`gh api .../milestones` → `[]`).
  The only real milestone registry is the `## M<N>` headers in
  `docs/prd/MILESTONES.md` (confirmed: M1–M5 exist).
- **Comment text is not queryable.** AC-7 wants counts by disposition over a time
  range; scraping every issue's comments per report is O(all issues) and needs the
  network. A local record is required.
- **The control plane has exactly one transactional mutation boundary** — the CLI.
  Nothing else opens `control.db`.
- Migration sequences 1, 3 and 4 are consumed (`bin/nightshift-ctl:320,390,409`).
  This change needs a new one, allocated via `seq next` under a serialize lease **at
  build time, not now**.

## Options considered

### A — Comment text is the only record; the report scrapes GitHub *(rejected)*

Enforce the disposition grammar at close time, write nothing locally, and have AC-7's
report read comments back from the API.

Rejected for three independent reasons, any one sufficient:
- The report becomes network-dependent and O(issues) per invocation, so "check the
  COMPLETED share on demand" becomes a minutes-long operation nobody runs.
- AC-6's "more than 3 closes by one actor in any 60-second window" needs the *recent
  close history* at decision time. Reconstructing that from the API on every close is
  both slow and racy.
- It cannot distinguish "nothing closed in range" from "the query failed" — which the
  issue's empty-state requirement explicitly demands.

### B — Record locally, then close on GitHub, single-phase *(rejected as stated)*

Insert the disposition row, commit, then perform the GitHub write.

This is close to right and is the basis of the decision, but as stated it is wrong:
if the GitHub write fails after the commit, a **phantom** row exists for an issue that
is still open. That row inflates the AC-7 report and silently consumes the actor's
AC-6 bulk-close budget. Rejected in favour of D's two-phase form.

### C — Close on GitHub first, record afterwards *(rejected)*

The natural ordering, and precisely the failure mode #424 exists to eliminate: a
crash between the two steps produces a closed issue with no disposition — an
undisposed close, created by the very tool meant to prevent them. It also violates the
issue's own failure state ("a close that cannot be recorded with its disposition must
not happen"). Rejected outright.

### D — Two-phase: record *intent*, write to GitHub, then confirm *(chosen)*

Insert the row with `applied_at` NULL, perform the GitHub write, then set `applied_at`
exactly once by compare-and-set. This is not invented here: it is the one-shot idiom
already established in this same file by `program_action_permits` (ADR-0044) and
`heartbeat_persist_decisions` (#390), both of which use a `consumed_at`-style column
set once by a CAS `UPDATE`.

It costs one extra write and one reconciliation concept, and buys: no phantom
deliveries in the report, no undisposed closes, and a crash window that is *visible*
(a stale intent row) rather than silent.

### E — Validate GitHub state from a cache *(rejected)*

AC-2 requires that issue N "exists and is open **at the time of the close**". A cache
makes that claim unfalsifiable and would let a close name an issue that closed a
minute ago. The API is consulted live; if it is unreachable the close is **refused**,
never assumed.

## Decision

### 1. New table (migration sequence: allocate at build time via `seq next`)

Append-only, additive only, no change to any existing table — the same discipline as
`bookkeeping_receipts`.

```sql
CREATE TABLE IF NOT EXISTS issue_close_dispositions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issue INTEGER NOT NULL,
  disposition TEXT NOT NULL CHECK(disposition IN (
    'delivered','superseded','deferred')),
  superseded_by INTEGER,          -- set iff disposition='superseded'
  deferred_to TEXT,               -- set iff disposition='deferred'
  evidence TEXT,                  -- required iff sensitive=1 (AC-5)
  sensitive INTEGER NOT NULL CHECK(sensitive IN (0,1)),
  state_reason TEXT NOT NULL CHECK(state_reason IN (
    'COMPLETED','NOT_PLANNED','DUPLICATE')),
  comment_body TEXT NOT NULL,     -- the exact body posted to GitHub
  actor TEXT NOT NULL,
  recorded_at REAL NOT NULL,      -- intent time; drives the AC-6 window
  applied_at REAL,                -- NULL until the GitHub write is confirmed
  CHECK((disposition='superseded') = (superseded_by IS NOT NULL)),
  CHECK((disposition='deferred')   = (deferred_to   IS NOT NULL)),
  CHECK(sensitive=0 OR evidence IS NOT NULL));
CREATE INDEX IF NOT EXISTS issue_close_dispositions_by_actor
  ON issue_close_dispositions(actor, recorded_at);
CREATE INDEX IF NOT EXISTS issue_close_dispositions_by_issue
  ON issue_close_dispositions(issue, recorded_at);
```

The paired `CHECK` constraints make an incoherent row (a `deferred` close carrying a
`superseded_by`, or a sensitive close with no evidence) *unrepresentable* rather than
merely discouraged. No unique index on `issue`: an issue may legitimately be reopened
and re-closed, and the history is the point.

### 2. Disposition grammar (AC-1)

Exactly one line matching this anchored, case-sensitive pattern must appear in the
closing comment body, in `MULTILINE` mode:

```
^Disposition: (?:delivered|superseded-by #(\d+)|deferred-to (M\d+))$
```

The three legal forms, verbatim:

| Form | Meaning |
|---|---|
| `Disposition: delivered` | the work shipped |
| `Disposition: superseded-by #236` | folded into issue 236 |
| `Disposition: deferred-to M4` | parked, still owed at milestone M4 |

Zero matches → refuse `disposition_missing`. Two or more → refuse
`disposition_ambiguous` (AC-1 says *exactly one*). The operator passes the
disposition string to the CLI and **the CLI composes the comment**, so the posted
text and the stored row can never disagree — they are generated from one value.

### 3. The close gateway (AC-1/2/3/5/6)

```
nightshift-ctl issue close --issue N --disposition '<string>' --actor <who>
                           [--evidence '<string>'] [--state-reason <R>] [--now T]
```

`--state-reason` defaults by disposition: `delivered` → `COMPLETED`,
`superseded` → `NOT_PLANNED`, `deferred` → `NOT_PLANNED`.

> **Amended at build time (F-2, cross-crew review of PR #450).** This line originally
> read `superseded` → `DUPLICATE`, and the shipped code deliberately does not do that.
> `gh issue close --reason` accepts only `{completed|not planned}` (gh 2.45.0, checked
> at build time) and GitHub's REST `state_reason` enum is `{completed, not_planned,
> reopened}` — **neither can express DUPLICATE**, so the drafted mapping was
> unimplementable, not merely inconvenient. Writing "not planned" to GitHub while
> recording `DUPLICATE` locally would have broken the one invariant this whole command
> is built on. `DUPLICATE` therefore stays legal in the table's CHECK constraint (the 12
> historical `DUPLICATE` closes are real and must remain representable) but is **refused
> at the door** as `state_reason_unsupported` rather than silently downgraded. Recorded
> here rather than left as a discrepancy for the next reader to trip over: an ADR whose
> Decision text disagrees with the shipped code teaches the reader to distrust the ADR.

Order of operations is the load-bearing part. Every refusal is `out(..., 3)` — the
established "denied" exit code — and performs **no GitHub write**:

1. **Parse** `--disposition` against the grammar above → `disposition_missing` /
   `disposition_ambiguous` / `disposition_unparseable`.
2. **Milestone (AC-3):** for `deferred-to M<N>`, require a `## M<N>` header in
   `docs/prd/MILESTONES.md` → `milestone_unknown`, listing the milestones that do
   exist. Explicitly **not** GitHub Milestones, which do not exist in this repo.
3. **Live GitHub read.** Fetch the target issue's `state` and `labels`; for
   `superseded-by #N`, fetch issue N too. Any failure refuses and never assumes:
   `gh_binary_missing` (FileNotFoundError/OSError), `gh_api_failed`
   (CalledProcessError), `parse_failed` — the exact triad already used at
   `bin/nightshift-ctl:3994-4006`.
4. **AC-2:** issue N must exist and be **open now** → `supersede_target_missing` /
   `supersede_target_closed`. A self-reference (`N == issue`) is refused as
   `supersede_self`.
5. **AC-5:** if the target carries label `P0` or `security`, `--evidence` is
   mandatory and must match one of: a 40-hex commit OID (reuse the existing
   `FULL_OID`), `#<pr-number>`, or `test:<id>`. Missing → `evidence_required`;
   unparseable → `evidence_unparseable`. Evidence must come from *this* close's
   comment; nothing is inherited from an earlier one.
6. **AC-6:** count rows with `actor = --actor` and `recorded_at > now - 60`. If that
   count is already >= 3, this close would be the 4th — refuse
   `bulk_close_refused`, naming the limit and the window in the payload:
   `{"error":"bulk_close_refused","limit":3,"window_seconds":60,"actor":...,"recent":[...]}`.
   The window slides and is anchored at each attempt. It counts **intents**, applied
   or not, so a retry loop cannot bypass the limit.
7. **Insert** the row with `applied_at` NULL, and `COMMIT`.
8. **Write to GitHub:** post the composed comment, then close with `--state-reason`.
9. **Confirm:** set `applied_at` once by compare-and-set (`WHERE id=? AND applied_at
   IS NULL`). If step 8 failed, `applied_at` stays NULL and the command exits
   non-zero; the stale intent row is what the undisposed-close scan reports.

### 4. `issue closable` excludes deferred work (AC-4)

`_issue_status` (`bin/nightshift-ctl:2266`) gains a disposition read: if the most
recent **applied** disposition for the issue is `deferred`, `closable` is `False` with
`"reason": "deferred-stays-in-queue"`. `cmd_issue_closable` (`:2280`) inherits this
for free, since it already filters on `_issue_status(...)["closable"]`.

**Lead judgment call, flagged for staff.** AC-4's "whose *only qualifying disposition
would be* `deferred-to`" is prospective in its wording, and `ctl` cannot know a
disposition that has not been chosen yet. I read it as its only implementable and
coherent meaning: an issue already dispositioned `deferred` stays in the queue scan
instead of being reported as closable. This is consistent with AC-3's "a deferred
issue is not counted as closed by any Nightshift-side reporting." If staff reads
AC-4 differently, this is the sentence to overrule.

### 5. Disposition report (AC-7)

```
nightshift-ctl issue disposition-report --since <ts> --until <ts>
```

Counts only rows with `applied_at IS NOT NULL` — intents that never reached GitHub
are not closes. Output always carries all three keys, so zero is explicit and
distinguishable from a failed query (the issue's second empty state):

```json
{"since": 1.0, "until": 2.0,
 "counts": {"delivered": 0, "superseded": 0, "deferred": 0},
 "closed_total": 0, "deferred_total": 0}
```

`closed_total` is `delivered + superseded` **only**. Deferred work is reported but
never counted as closed, per AC-3.

### 6. Undisposed-close scan (failure state 3)

Open question 2 asked whether this belongs here or in the heartbeat. Both placements
satisfy the criteria, so take the reversible split: the **query** lands here as
`nightshift-ctl issue undisposed --since <ts>` — listing issues closed on GitHub with
no applied disposition row, plus stale intent rows from a step-8 crash — and the
**scheduling** stays the heartbeat's, which merely calls it. Moving a caller later is
cheap; entangling the scan with heartbeat internals is not.

### 7. Open question 1 — hard refusal, no override

AC-6 is a hard refusal with no override flag, exactly as the issue directs ("build the
refusal first; an override can be added without reworking it"). An override is
additive later and nothing here forecloses it.

## Testability seams (constrains the implementation; QA has **not** confirmed this)

- **GitHub:** injected via `$NIGHTSHIFT_CTL_GH` (`bin/nightshift-ctl:3022,3984`).
  Tests point it at a fake `gh` script to drive open/closed/absent targets, label
  sets, and the unreachable path — no network, no real closes. **All GitHub access in
  this command must go through that variable**; a hard-coded `gh` would make AC-2 and
  the API-unreachable failure state untestable.
- **Clock:** injected via `--now` on argv (`bin/nightshift-ctl:459`, `tests/ctlutil.py`
  `Ctl(clock=VIRTUAL_T0)`). AC-6's 60-second window is therefore proven by advancing
  virtual time, never by sleeping. Deliberately **not** an env var — see the clock
  comment in `tests/ctlutil.py` before considering one.
- Tests drive the real CLI through subprocess, per the existing suite.

## Consequences

**Easy.** The COMPLETED share becomes a single local query. A P0 cannot be swept
without naming evidence. A bulk close is mechanically impossible. Deferred work stays
visible instead of vanishing. The 2026-08-26 batch could not happen again.

**Hard.** Every close must now go through `ctl`; a human closing in the GitHub UI
bypasses all of it, which is why the undisposed scan exists — detection, not
prevention, because we cannot gate GitHub's own UI. Closing gains a network
round-trip and can now fail.

**Foreclosed.** Nothing structurally: the table is additive, the grammar is additive,
and an AC-6 override remains addable. The 79 already-closed `NOT_PLANNED` issues are
explicitly **not** retro-dispositioned — this sets the rule going forward and supplies
the audit command, as the issue's "Not included" section directs.

**Program-owned close paths that still bypass this gateway.** `staff` review returned
NEEDS-A-WIDER-DECISION on exactly one point, and it is worth stating by name rather than
leaving a reader to discover it: **this gateway does not, on merge, become the only way an
issue in this repo gets closed.** Two program-owned paths close issues with a direct
`gh issue close` today and will keep doing so until `program` separately rewires its own
configuration:

- **`program/config.yaml:529`** — the HEARTBEAT bullet instructs the Program, every hour,
  to "close any backlog issue `nightshift-ctl issue closable` lists whose pm acceptance is
  recorded (comment the verified criteria, then `gh issue close`)". This is the
  **highest-volume closer in the org**, and it is the closest living relative of the
  2026-08-26 batch-close incident this ADR exists to prevent. Note the specific irony: that
  bullet already calls `nightshift-ctl issue closable`, so it will inherit AC-4's
  deferred-work exclusion for free — but its actual close is still a raw `gh issue close`,
  so it records no disposition, is counted by no AC-7 report, and is refused by no AC-6
  bulk-close limit.
- **`deploy/policies/omnipods_policies.py:580` (`pr_close_keyword_guard`)** — the guard
  itself is a PR-body linter and never closes anything, but its denial text is where this
  repo's close protocol is actually written down: "the program closes the issue only after
  every task PR has integrated and pm acceptance passed." That documented protocol is an
  out-of-band direct close, not a gateway call. Until the guard's message and the heartbeat
  bullet both point at `nightshift-ctl issue close`, the prose that teaches the org how to
  close an issue teaches it to bypass this ADR.

The two bypasses are **not** the same kind of gap, and it is worth being exact rather than
lumping them together:

- `program/config.yaml` is genuinely outside crew_alpha's reach. `docs/owners.md` assigns
  `program/**` to `platform` with the explicit note "**no crew owns this** … Changes require
  an ADR." `program` has settled the sequencing (`.omni/decisions.md`, "2026-08-31 … #424
  ADR-0055 staff verdict settled"): the heartbeat bullet is rewired to call this gateway
  **after** this primitive merges to `origin/main`, as program's own follow-up. Doing it
  inside this build would wire the heartbeat to a command that does not yet exist on `main`
  and break every close until merge landed — an outage bought for no benefit.
- `deploy/policies/**` **is ours.** `docs/owners.md` carries it as an explicit exception row
  — "a `deploy/` path owned by the control-plane crew … `worker_semaphore` and
  `pr_close_keyword_guard` are meaningless apart from ctl's tables." So the guard's denial
  text is crew_alpha's to change, and leaving it unchanged here is a **scope and sequencing
  choice, not an ownership impossibility**. It is deliberately deferred for the same reason
  as the heartbeat bullet and must land in the same follow-up: prose telling the org to call
  `nightshift-ctl issue close` would point readers at a command that does not exist until
  this primitive is on `main`. An earlier draft of this paragraph asserted the guard was
  "not ours" — a convenient error, corrected here rather than inherited.

The honest reading of this ADR on the day it merges is therefore: **a built and tested
primitive whose principal intended caller does not call it yet.** That gap is tracked as a
`.omni/tech-debt.md` row, written into the run record rather than into this PR: `docs/owners.md`
assigns `.omni/**` to `program` and states it is "persisted off-box via the `omni/state` branch,
**never via `main`**", so a feature PR to `main` is the wrong carrier for it. The row closes only
when a heartbeat is observed closing an issue through `nightshift-ctl issue close` — not when the
config edit is made. This is
the same scope-boundary disclosure ADR-0052 (§Consequences, `release`'s merge procedure never
wired to `fast_path_eligible`) and ADR-0053 (§Consequences, the Program's scheduled-task
prompt never wired to `heartbeat-persist record`) each made for their own primitive, and it
carries the same standing warning: a primitive nobody calls is not a delivered control.

## Reversal trigger

Revisit if any of these become observably true:

- The gateway refuses a *legitimate* close more than about once a week — the grammar
  or the AC-6 limit is mis-tuned and should be loosened before it teaches people to
  route around `ctl`.
- Undisposed closes keep appearing, meaning humans are closing in the UI anyway and
  enforcement-at-the-CLI is the wrong layer.
- This repo adopts real GitHub Milestones, which would retire the
  `docs/prd/MILESTONES.md` header parsing in step 2 of the gateway.
- Step 8 failures produce stale intent rows often enough to need automated
  reconciliation rather than a report.

## Follow-ups this ADR does not resolve

- **#236 blocks the crew, not just this task.** No crew lead can dispatch any worker
  until the `slot_id` schema/policy contradiction is fixed. Escalated to the program.
- QA must confirm the "Testability seams" section and produce the AC-8 plan before an
  engineer is dispatched.
- **`program` rewires `program/config.yaml`'s heartbeat close step** to call
  `nightshift-ctl issue close` once this merges, and updates `pr_close_keyword_guard`'s
  denial text to name the gateway as the sanctioned close path. Program-owned, program-
  actioned, tracked by the tech-debt row described under Consequences. Not crew_alpha's.
- Migration sequence allocated at build time under the serialize lease (lease 26,
  `bin/nightshift-ctl`, holder `crew_alpha`): `seq next` returned **8**. Recorded here
  because the prediction while drafting was 5 — three further sequences were consumed by
  other work between drafting and build, which is precisely why the number is allocated
  rather than read off the file.
