# ADR-0039: Persist review receipts and consolidate findings by content, not by call

Date: 2026-08-28 · Status: accepted
Deciders: crew_alpha (implementer), staff (gate), security (gate, sensitive-input surface)
Spec: GitHub issue #231 ("#228B"); frozen contract
`docs/reviews/231-persist-review-receipts-CONTRACT.md` v1
Depends on: ADR-0026 (frozen review contract), ADR-0036 (shadow risk plan — explicitly
NOT an authority input here)

## ELI5

A reviewer posts a PR comment saying "domain X passed/failed for this exact commit."
Right now that comment is the only record — if the PR page changes, gets edited, or
nobody reads it again, the decision is gone or has to be re-derived by re-reading
GitHub. This ADR adds three small tables so Nightshift can durably record "domain X
said Y about exactly this commit, and we checked the comment was genuine and fresh
when we recorded it," and can later ask "have all the domains this work item needs
weighed in on this exact commit yet?" by reading the database only — no second trip
to GitHub, ever, once a receipt is captured.

## Context

Issue #228's original design (superseded by ADR-0036 into small shadow-mode slices)
included a durable review ledger. #228A shipped only the diagnostic risk planner.
This is #228B: the first slice that actually stores review outcomes. Five rounds of
DoR settled the shape (recorded verbatim in the frozen contract's acceptance
criteria); this ADR records the concrete schema and validation algorithm that
satisfy it.

The central risk DoR surfaced is misattribution, not forgery: every seat currently
authenticates to GitHub through one shared PAT (`.omni/tech-debt.md`, "Review
independence is not mechanically verifiable"), so nothing in this design can prove
*which agent* posted a comment — only that a well-formed, fresh, correctly-scoped,
correctly-marked comment exists. That is a deliberate, named boundary, not an
oversight (see Consequences).

Engineer dispatch is broken org-wide (D-054/#236, platform still working it), same
as when #228A shipped. This ADR and its implementation are authored directly by the
crew_alpha lead, per the same fallback the crew's charter names explicitly. The
one-paragraph debate this still owes: the two live design questions below were
each considered as an explicit two-option decision by the same author, mirroring
what a second vendor would most plausibly have pushed back on, and are recorded as
such rather than skipped.

### Revision note (round-1 review fixes, 2026-08-28)

Cross-crew (crew_beta) and security both PASSed round 1; staff and architect
(different-vendor code review) found three `MERGE_BLOCKER`s, all fixed in place on
this same PR/branch (this ADR is not yet accepted, so a direct correction is
appropriate, matching ADR-0036's own precedent for a pre-acceptance fix):

1. **(staff) Missing AC-4/AC-5 conformance test.** Added: a static introspection
   test asserting `consolidate_findings`/`parse_required_domains` reference no
   network/subprocess names, plus a black-box CLI test that runs `review
   consolidate` against a fake `gh` that logs every invocation and asserts the
   log is never written, in both the not-ready and ready-with-findings cases.
2. **(architect) A spoofed caller permalink was persisted verbatim.**
   `validate_receipt` discarded the caller's owner/repo but kept persisting the
   caller's raw `--comment-url` string as `receipt.comment_url` — so a decoy
   `https://github.com/attacker/decoy/...` string survived into the durable row
   even though the fetch itself was correctly scoped to the task's real repo,
   violating AC-2's "binds to ... the PR-comment permalink." Fixed: the fetched
   object's own numeric comment id is now verified against the id parsed from
   the caller's URL (`comment_id_mismatch` if it disagrees — defense in depth
   against `fetch_comment` ever resolving the wrong comment), and the persisted
   `comment_url` is a canonical string built ONLY from the authoritative
   owner/repo/PR-number (already re-derived from the fetched object for the
   `pr_mismatch` check) plus that verified id. The caller's string is now used
   for nothing but routing which id to fetch.
3. **(architect) TOCTOU between the marker check and the write.** The marker
   comparison happened inside `validate_receipt`, which commits and returns
   before `cmd_review_receipt_record` opens its own write transaction — a
   concurrent `review marker-mint` for the exact same `(task, head, domain)` in
   that gap could replace the marker after it was checked but before the row
   landed. Fixed: `cmd_review_receipt_record` now re-reads the current marker
   and compares it to the validated value INSIDE the same `BEGIN IMMEDIATE`
   transaction as the `INSERT` (rolling back and denying `marker_mismatch` on a
   mismatch found there). `BEGIN IMMEDIATE` acquires SQLite's write lock
   immediately, so a concurrent `review marker-mint`'s own `BEGIN IMMEDIATE`
   cannot proceed until this transaction commits or rolls back — the recheck
   and the write are now atomic with respect to each other. A new test proves
   the underlying mechanism (two real connections, one holding the lock while
   the other's `marker-mint` blocks) rather than just asserting the outcome.

Folded into the same pass, all `FOLLOW_UP` and non-blocking: explicit
`MAX_FINDINGS_PER_RECEIPT`/`MAX_SUMMARY_LEN`/`MAX_CRITERION_LEN` caps (security —
the 1MB whole-comment fetch bound only constrained `findings[]` transitively); a
documented, deliberate note on `_normalize`'s NFKC choice (crew_beta — compatibility
folding can map visually distinct code points onto the same fingerprint, accepted
because findings text is already behind the marker/freshness/PR-identity gate and
the worst case is an unwanted merge, not a fabricated pass); and a wording fix in
`.omni/tech-debt.md` (staff — a marker is "valid until re-minted," not "single-use").

**Deliberately NOT folded in:** consolidating this module's bounded-GitHub-fetch
duplication into `bin/nightshift_github.py` (staff FOLLOW_UP). Staff is correct that
a second consumer of bounded GitHub fetches now exists, which is this ADR's own
Q3 reversal trigger — but `docs/owners.md` still has no explicit owner row for
`bin/nightshift_github.py` distinct from "the coordination kernel," and folding a
cross-file refactor into a blocker-fix pass, under an active `serialize` lease
scoped to `bin/nightshift-ctl`, is exactly the scope-creep this crew's own
process exists to avoid. Recorded as a separate, explicit FOLLOW_UP in
`.omni/decisions.md` rather than done silently or dropped silently.

## Decision

### Schema (migration sequence 1, allocated to this lease)

Three new tables, additive only, no change to any existing table:

```sql
CREATE TABLE IF NOT EXISTS review_receipts(
  task_id TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  domain TEXT NOT NULL CHECK(domain IN (
    'pm_acceptance','code_review','qa','cross_crew_review','staff_adr',
    'security','ux')),
  verdict TEXT NOT NULL CHECK(verdict IN ('pass','fail')),
  pr_url TEXT NOT NULL,
  comment_url TEXT NOT NULL,
  marker TEXT NOT NULL,
  report_digest TEXT NOT NULL,
  comment_created_at TEXT NOT NULL,
  validated_at REAL NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(task_id, head_sha, domain));

CREATE TABLE IF NOT EXISTS review_findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  domain TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN (
    'MERGE_BLOCKER','PRE_DEPLOYMENT','FOLLOW_UP')),
  fingerprint TEXT NOT NULL,
  criterion TEXT,
  summary TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS review_findings_by_task_head
  ON review_findings(task_id, head_sha);
CREATE INDEX IF NOT EXISTS review_findings_by_fingerprint
  ON review_findings(fingerprint);

CREATE TABLE IF NOT EXISTS review_dispatch_markers(
  task_id TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  domain TEXT NOT NULL,
  marker TEXT NOT NULL,
  minted_at REAL NOT NULL,
  PRIMARY KEY(task_id, head_sha, domain));
```

`review_receipts` is keyed exactly `(task_id, head_sha, domain)` per AC-2 — never
`agent_runs`/`worker_slots`/`task_admissions`, which do not exist per-domain for
most review types (confirmed during DoR: `qa`, `cross_crew_review`, `staff_adr`,
`security`, `ux` have no per-domain slot or admission row today). `domain` is a
closed CHECK set matching the seven canonical labels in
`docs/reviews/REVIEW-CONTRACT-TEMPLATE.md`'s checklist (AC-9). `review_findings`
is not foreign-keyed to `review_receipts` (SQLite FKs are off in this control
plane and this migration does not turn them on); the persistence command is the
only writer and always writes a receipt and its findings in the same transaction,
so the pairing is an application invariant, stated here rather than silently
assumed.

`review_dispatch_markers` is the minimal new state AC-3(c) requires: Program mints
one opaque token per `(task, head, domain)` dispatch (`review marker-mint`,
`secrets.token_hex(16)`) and embeds it in the existing dispatch instruction text —
no new dispatch mechanism, just one more fact in text a worker already receives.
`INSERT ... ON CONFLICT DO UPDATE` means re-dispatching the same tuple invalidates
the previous marker, which is intentional: a stale marker from an earlier, possibly
abandoned dispatch must not validate a copy-pasted old comment.

### Receipt persistence algorithm (`review receipt record`, AC-3/AC-4)

Given `--task`, `--head-sha`, `--domain`, `--comment-url`, in one read-then-validate
pass with NOTHING written until every check passes (`bin/nightshift_review_receipts.py:validate_receipt`):

1. `domain` is in the closed set; `head_sha` is 40 lowercase hex.
2. Read the task row; its `pr_url` must parse as
   `https://github.com/{owner}/{repo}/pull/{n}`.
3. Fetch the comment fresh via `gh api repos/{owner}/{repo}/issues/comments/{id}`
   (bounded subprocess, same discipline as `bin/nightshift_github.py`'s
   `_run_bounded`/`github_get_json` — deliberately NOT imported, to avoid a new
   cross-file ownership seam on a file this ADR does not otherwise touch; the ~30
   lines of bounding logic are duplicated with a comment pointing at the original).
   Any GitHub error, timeout, or non-2xx is `comment_unreadable` and fails closed.
4. The comment id in `--comment-url` is only a routing hint. The AUTHORITATIVE
   repo/PR/comment identity is re-derived from the fetched object's own
   `html_url`/`issue_url` fields and compared byte-for-byte against the task's
   `pr_url` (AC-3b) — a caller cannot claim a comment belongs to the right PR by
   constructing a URL string that merely looks right. The fetched object's own
   numeric `id` is also verified against the id parsed from `--comment-url`
   (`comment_id_mismatch` on any disagreement). The `comment_url` this receipt
   ends up persisting is a CANONICAL string built only from this authoritative
   owner/repo/PR-number and verified id — never the caller's original string
   (round-1 fix; see Revision note above).
5. `created_at != updated_at` on the fetched object → `comment_edited`, fail
   closed (AC-3d). This is Option A below.
6. Extract the first ` ```nightshift-review ` fenced block from the comment body;
   parse as JSON with EXACTLY the keys `{domain, task_id, head_sha, verdict,
   marker}` plus an optional `findings` array. Any missing/extra top-level key,
   any of `domain`/`task_id`/`head_sha` not matching the command's own arguments
   exactly, `verdict` not in `{pass, fail}`, or `marker` not matching the current
   value in `review_dispatch_markers` for this exact tuple — all fail closed with
   a distinct `reason_code` (AC-3a, AC-3c).
7. Each `findings[]` entry must have `severity` in the closed set and a non-empty
   `summary`; malformed findings fail closed the whole receipt (a partially valid
   receipt is not persisted — AC-1's "minimal" reading is that failure is atomic,
   not that findings are optional plumbing).
8. `report_digest = sha256(canonical_json(block))`, `canonical_json` byte-identical
   to `bin/nightshift_review_plan.py`'s (`sort_keys=True, separators=(",", ":")`) —
   AC-4's "same discipline," reused by rule, not by import (the planner module is
   diagnostic-only per ADR-0036 and must not become an import dependency of an
   authoritative path).
9. Only now: one `BEGIN IMMEDIATE` transaction RE-READS the current marker for
   this exact `(task_id, head_sha, domain)` and compares it to the validated
   value — closing the gap between step 6's check and this write (round-1 fix;
   see Revision note above) — then, only if it still matches, upserts the
   `review_receipts` row, deletes any prior `review_findings` rows for this
   exact tuple, and inserts the new ones. Re-validating the same exact
   head/domain is idempotent capture, not append.

### Consolidation (`review consolidate`, AC-5/AC-7)

Pure DB read, given `--task`, `--head-sha`, `--contract <path>`:

1. `nightshift_review_receipts.parse_required_domains()` reads the contract file's
   "Required review domains" checklist (AC-9's prefix-match rule, specified in the
   frozen contract and in the template).
2. A domain counts as satisfied only if a `review_receipts` row exists for the
   EXACT `(task_id, head_sha, domain)` — a receipt from an earlier head never
   counts, by construction of the primary key.
3. If any required domain is missing, report `ready: false` and which domains are
   missing. No GitHub call occurs on this path, ever (AC-4's back half).
4. If ready, read all `review_findings` for `(task_id, head_sha)`, group by
   `fingerprint = sha256(canonical_json({criterion: normalize(criterion),
   summary: normalize(summary)}))` — `normalize` is casefold + collapse
   whitespace + strip, and `domain` is never a fingerprint input (AC-7). Within a
   fingerprint group, keep the highest-severity row
   (`MERGE_BLOCKER > PRE_DEPLOYMENT > FOLLOW_UP`, AC-6) and list every contributing
   domain for traceability. Output is sorted by severity rank then fingerprint for
   determinism.

## Options considered

### Q1 — how does the persistence command prove a comment is genuinely fresh?

**A — fail closed on any edit, chosen.** `updated_at != created_at` refuses
unconditionally; a domain must re-post (or the operator must re-trigger validation
against a new, unedited comment) rather than the tool trying to decide which edits
are "safe." This is the DoR-settled AC-3(d) reading. It costs a re-post on a typo
fix, which is the correct price for a receipt that later becomes unreviewable
DB-only authority.

**B — allow edits within a short grace window after `created_at`.** Rejected: a
window is a second clock to get right, invites exactly the "which edits are
review-relevant" judgment call AC-3(d) exists to avoid, and DoR explicitly settled
on the simpler unconditional rule.

### Q2 — how does the required-domains checklist become machine-readable?

**A — parse the existing checkbox markdown by fixed canonical-label prefix,
chosen.** No new file, no new format; the same document a human reads is the one
the tool reads. Cost: prefix matching is slightly fuzzier than a dedicated
structured field, mitigated by failing closed on anything it cannot recognize
(unrecognized label, duplicate label, missing canonical row).

**B — add a parallel JSON/YAML sidecar per contract file declaring required
domains explicitly.** Rejected: doubles the surface a contract author must keep in
sync (the checklist a human fills and a machine block that could silently
disagree with it), which is exactly the "two blocker streams" failure mode ADR-0026
was written to close. AC-9 also names this file as the intended target
("REVIEW-CONTRACT-TEMPLATE.md's ... checklist"), not a new file.

### Q3 — reuse `bin/nightshift_github.py` for the comment fetch, or duplicate the bounded-subprocess helper?

**Duplicate, chosen**, for the reason in the algorithm section: `nightshift_github.py`
has no owner row in `docs/owners.md` distinct from "the coordination kernel," but
this ADR's schema/migration lease is scoped exactly to `bin/nightshift-ctl`, and
minimizing the touched-file set during an active `serialize` lease is the safer
default. The duplicated block is ~30 lines with a comment pointing at the source
of truth; a future ADR can consolidate once ownership of that file is explicit.

## Consequences

This makes "has every required domain weighed in on this exact commit" a fast,
offline, deterministic question, and makes a stale or copy-pasted comment from an
earlier head or the wrong PR provably unusable as a receipt.

**Named threat-model boundary (AC-11):** this mechanism defends against accidental
misattribution and stale/copy-pasted receipts — a domain's comment landing on the
wrong task, an old comment being reused after the code changed, a comment edited
after the fact. It does **not** defend against a malicious or compromised agent
that deliberately forges another domain's receipt: every seat shares one GitHub
PAT today (`.omni/tech-debt.md`, dated 2026-08-22), so nothing here can attribute a
comment to a specific agent identity, only to a well-formed, fresh, correctly
scoped, correctly marked comment. Closing that gap needs per-seat credentials,
which is out of scope for #231 and is this ADR's own reversal trigger.

This adds one more thing schema migrations must keep additive (`ensure_schema`'s
existing race-safe discipline covers it) and one more read path (`review
consolidate`) that must never regress into a live call — the conformance test in
the frozen contract's AC-4/AC-5 verification column exists specifically to catch
that regression before it ships.

## Cost

No infrastructure or subscription cost. Three small tables and two new CLI
subcommands plus a marker-mint command; SQLite storage growth is linear in review
volume, materially smaller than the diffs and comments it describes. Revisit only
if a work item accumulates enough receipt churn (many heads × many domains) to
make consolidation's per-task scan non-trivial, which is not observed at current
volume.

## One-way door?

No. The tables are additive and unused rows can be ignored by any future design;
nothing downstream is contracted to read them yet (activation is #233, explicitly
out of scope here). The marker-minting convention could be replaced without a data
migration, since old markers simply stop matching.

## Scale tier

Tier 0 process metadata, same tier as ADR-0026 and ADR-0036. Revisit if
consolidation must run across more than one host, or if per-task receipt volume
makes the `(task_id, head_sha)` scan measurable.

## Reversal trigger

Supersede this ADR's threat-model boundary the day per-seat GitHub credentials
exist and reviewer identity becomes provable — at that point this mechanism should
be extended to check the comment's actual author, not just its content and
freshness. Separately, reconsider Q3's duplication if a second consumer of bounded
GitHub fetches appears and ownership of `bin/nightshift_github.py` is made
explicit in `docs/owners.md`.
