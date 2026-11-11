# ADR-0090: Record what a review found independently of whether it can be attested

Date: 2026-09-07 · Status: proposed
Deciders: program · Spec: GitHub issue #717 (parent epic #716)
Depends on: ADR-0039 (persist review receipts), ADR-0040 (review eligibility),
ADR-0026 (frozen review contract)
Related: ADR-0072 (bind receipts to authority-attested dispatches — proposed, not
landed; this ADR is what gives it something to bind to)

## ELI5

Reviewers were finding real bugs and there was no line anywhere saying so. The
file that scores reviewing — `.omni/reviews.md` — was written by nothing at all,
and the database table that gates merges had zero rows after months of running.
So when somebody asked "are reviewers worth it?", the honest answer available was
a blank, and a blank reads like a zero. This ADR makes the act of finishing a
review write both records, and makes reading a blank an error instead of a score.

## Context

`.omni/reviews.md` states its own purpose in its first three lines: *"The retro
reads this file to compute escaped defects — the measure of whether the gate is
real or theatre."* `program/agents/release/config.yaml` preconditions 2 and 5 gate
crew-branch merges on `code_review` and `cross_crew_review` being absent from
`missing_domains` — not every merge: the same file exempts `oncall` INCIDENT and
`platform` shared-path branches, which "structurally cannot satisfy" the crew
preconditions.

Neither instrument worked:

- **Nothing in the repository wrote to `.omni/reviews.md`.** Not
  `bin/nightshift-ctl`, not a policy, not a hook. `bin/nightshift-init.sh` created
  it and that was the end of the write path. The instruction to append a line
  existed only as prose inside `program/skills/cross-crew-review/SKILL.md`.
- **`review_receipts` held 0 rows.** Not because reviews did not happen, but
  because ADR-0039's only ingestion path requires a fresh, unedited GitHub PR
  comment carrying an opaque per-dispatch marker. That is a hard dependency on a
  transport the org does not always own: a workflow triggered by `issue_comment`
  turns posting a review into starting a CI run on somebody else's repository, so
  the reviews were written into the PR body instead and the receipt path was
  structurally unreachable for the whole run. Whatever the next external
  repository's reason turns out to be, it will have one.

The cost is on the record. A 12-hour run against an external repository produced
two genuine cross-crew reviews. The run's four reviewer seats together produced
hundreds of items, two verified `MERGE_BLOCKER`s and nine `PRE_DEPLOYMENT` —
**that total is the four seats', not the two cross-crew reviews'**, and it cannot
be split between them from any surviving record, which is itself a consequence of
the defect described here. One of the two blockers is a time-of-check/time-of-use
defect in the permit gate, a role demotion between validation and execution, which
the run's own ledger notes *"neither the author nor security found."*
`.omni/reviews.md` was byte-identical before and after.

An org redesign was then drafted on that empty reading, deleting the four reviewer
seats as ceremony because they held no slots and produced no receipts — the two
records structurally guaranteed to be empty. It would have removed the seats that
produced the two hardest findings of the run. It was caught by an independent
review and is retracted in #716.

That is the second time the same error has been made in this project against a
reviewer: Program previously discounted a finding from a seat whose sandbox could
not read outside its own worktree, and correctly retracted. **Absence of evidence
read as evidence of absence** is the defect class, and a recorder that writes
nothing manufactures it.

## Options considered

### A — Make the ADR-0039 comment path work everywhere (rejected)

Get a receipt for every review by making the PR comment always postable: a
separate bot identity, a comment marker that suppresses the workflow, or posting
to a Nightshift-owned mirror.

Rejected because it fixes the wrong layer and does not generalise. The comment is
unpostable for reasons that belong to the customer's repository, and the next
customer will have a different reason. Worse, it leaves the record contingent on a
transport: the review still produces nothing if GitHub is down, if the token loses
scope, or if the seat runs outside a slot — which is what actually happened.

### B — Weaken the receipt so anything can mint one (rejected)

Drop the marker and freshness checks so a seat can assert a receipt directly.

Rejected because it destroys the one property ADR-0039 bought. A receipt would
become a claim, and the release gate would be reading claims. The problem is that
findings are unrecorded, not that receipts are too strict.

### C — Separate the record from the attestation (chosen)

Two ingestion paths writing one log, with the strength of the evidence carried in
the row:

- `review receipt-record` (unchanged contract) keeps ADR-0039's full validation
  and now also writes the log entry, labelled `attested`.
- `review log` records a review with no PR comment and no marker, labelled `local`
  (or `reconstructed` for a historical replay). It writes the file first and mints
  the receipt second.

`review_receipts` gains a nullable `source` column so the two are never
indistinguishable in the table.

## Decision

**1. The finding outranks the accounting.** `review log` writes
`.omni/reviews.md` before it touches the control plane, and a missing task row, a
bypassed semaphore or an unusable comment channel changes what is *recorded about*
the review, never whether it is recorded. The output names what could not be
minted (`receipt_skipped_reason`) instead of exiting quietly.

**2. Weaker evidence stays labelled, never silently equal.** `source` is
`attested` only for a receipt that passed ADR-0039's matrix. `review log` refuses
`provenance: attested` outright — the strong label is not purchasable with a JSON
key. `comment_url` on an unattested row is an `omni://reviews.md#<entry_id>` URI
that cannot be mistaken for a GitHub comment.

**3. An attested receipt cannot exist without its log entry.** The append
happens in `receipt-record` **before** the transaction opens, and a failed
append refuses the whole command. Two independent steps is precisely how the
two records drifted apart; the only failure direction left open is
entry-written / commit-failed, which loses the accounting and keeps the
finding.

*Amended: the first implementation put the append INSIDE `BEGIN IMMEDIATE`.
That held the control-plane write lock across a filesystem write, and one
blocked path froze the plane for every other seat — an unrelated `task upsert`
measured 32 seconds and then died on `database is locked`. Moving the append
out keeps the invariant that matters and gives up only the reverse. Recorded
because this document prescribed the deadlock for several hours after the code
had rejected it.*

**4. Every entry is both human-readable and machine-readable.** A markdown table
plus findings prose for the reader, and a ```` ```nightshift-review-log ```` fenced
JSON object for `review escaped-defects`. The fence carries an `entry_id` over the
entry's own content, so an edited severity is detected on read rather than scored.

**5. What cannot be known is recorded as unknown.** `independent` and `slot_state`
are tri-state (`yes`/`no`/`unknown`, `held`/`absent`/`unknown`). `receipt-record`
knows the domain and the verdict but not which seat reviewed which, and ADR-0072
forbids deriving the vendor from `.omni/models.json` or `tasks.crew`. Forcing a
boolean would make every such entry assert something the recorder cannot know. An
entry claiming `independent: yes` must name both seats, and is refused if they are
the same seat.

**6. Contradictions are recorded, not refused.** A `pass` carrying an unresolved
`MERGE_BLOCKER`, or one seat named as both author and reviewer, is written into
the entry's `contradictions` list. Refusing it would send the caller away with
nothing written — this ticket's own defect, wearing a smaller costume.

**7. A review kind the frozen vocabulary does not name is still recordable,
but only deliberately.** The ticket asks whether ADR-0039's seven domains cover
what actually ran. **They do not.** The run produced an architecture review, an
adversarial attack on that architecture, a rollout/cutover sequence, an on-call
runbook and a customer-experience analysis as separate seats' output, and a PR
to add an eighth domain has been open since before that run. Refusing all of those is
this ADR's own defect narrowed to a tuple, so an entry may carry a
non-canonical `domain` when it also sets `domain_outside_vocabulary` — which
keeps a typo like `cross_crew_reveiw` failing loudly instead of quietly minting
a domain that gates nothing. No receipt is minted for one: `review_receipts.domain`
is a closed `CHECK` set and widening it is not this ADR's to do. `review log`
reports `receipt_skipped_reason: domain_outside_vocabulary` rather than raising
an `IntegrityError` the caller has to interpret.

**8. A reconstruction may say its head is unrecoverable; nothing else may.**
A historical review whose exact reviewed head is not in any surviving record
carries the literal `unrecoverable` in `head_sha`, accepted only from a
`reconstructed` entry. Borrowing a nearby commit would assert a binding nobody
checked, and would let a reconstruction satisfy an exact-head gate it never
passed — the sentinel matches no SHA, so it can never satisfy an exact-head
gate. It is not inert in the TABLE: `review_receipts.head_sha` has no `CHECK`
constraint, so a reconstruction with a real task row does mint a row keyed on the
literal string. That row is visibly `source='reconstructed'` and matches no head
any gate will ask about, which is the intended reading, but "inert everywhere"
would overstate it.

**9. An empty read is an error.** `review escaped-defects` exits 3 with
`no_entries` or `reviews_missing` rather than printing `{"escaped": 0}`. A count of
zero out of N parsed entries is an answer; zero entries is the reading that nearly
deleted the reviewer seats.

**10. Findings carry a disposition, and the one that lowers the number carries
a reason.** `open` (default) / `accepted` / `fixed` / `overturned` / `upheld` /
`deferred`. The run this ADR comes from had all of the interesting cases: a
finding the author disputed and lost, one that was wrong because the reviewing
seat's sandbox could not read outside its own worktree, and a block deliberately
recorded and not worked. Severity alone cannot tell those apart.
`overturned` is the only disposition that removes a finding from the
escaped-defect total, so it requires a `disposition_note` and its use is
reported as `excluded_overturned` beside the total it changed — a lever that
lowers a metric without fixing anything must be at least as visible as the
metric.

**Escaped defect** is defined as a `MERGE_BLOCKER` or `PRE_DEPLOYMENT` finding
that is either declared with `found_after` (a post-merge staff audit, an incident,
a bot on an approved head — ordering cannot infer these) or recorded later than
the first `pass` on the same PR. `FOLLOW_UP` is excluded by definition. Deduplicated
per PR by the same content fingerprint `review consolidate` already uses. The split
by severity is reported alongside the total so a reader who disagrees with the line
can recompute rather than re-derive.

Schema change is migration sequence 13, allocated with
`nightshift-ctl seq next --name migrations --resource 'bin/nightshift-ctl'
--holder program/717` under that resource's serialize lease: one nullable
`source` column on `review_receipts`, additive, no PK change, no data rewrite.

## Amended 2026-09-07, after four adversarial reviews of the first implementation

Four independent channels — one reading the code, one executing it, one reading
only these claims, one mutating the tests — each returned REFUTED. Sixteen
defects were confirmed. **Every one of the five that mattered produced a FALSE
ZERO**, which is the failure this ADR exists to eliminate, so they are recorded
here rather than quietly fixed.

**11. The reader must not manufacture the blank.** Five separate paths did:

- **The window filtered the input.** `escaped-defects --since <wave-start>` —
  the retro's own documented invocation — dropped the `pass` entries predating
  the window, so a blocker inside it was no longer "after a pass" and reported
  zero. The window now filters the ANSWER; every defect is computed against the
  full log's passes.
- **Whole-second timestamps collided.** Two `review log` calls 200ms apart
  normalized to the same instant and the strict comparison scored nothing —
  measured wrong twenty times out of twenty on the default path. Timestamps now
  carry milliseconds, and within one log the append order breaks a genuine tie.
- **Timestamps were never parsed.** A regex fast-path returned the caller's
  string, so `2026-02-30`, `2026-13-01` and `99:99:99` were stored verbatim and
  sorted lexicographically among real instants. A regex is a shape check; only
  the calendar knows whether a date exists.
- **A deleted or truncated fence silently rescored.** The `entry_id` digest
  catches modification and not deletion. Entry headings and fence openers are
  now counted and compared.
- **A file of pre-ADR-0090 rows read as "nobody recorded anything."** This
  repository's own log holds 23 recorded reviews in the old format; the reader
  reported `no_entries` and the retro skill told the reader to conclude nobody
  had recorded them. `legacy_entries_only` and a row count now say otherwise.
  This one is the ADR's own thesis committed against 630 lines of real record.

**12. Not recovered is not zero.** A reconstruction may set
`findings_recovered: false` and `verdict: "unknown"`. Without them the replay
encoded *findings unknown* as `"findings":[]` in the half the retro reads, and
had to invent a `pass` — creating the `first_pass` every escape is measured
against. An `unknown` verdict mints no receipt and establishes no pass.

**13. The escape fingerprint is not `review consolidate`'s.** That one excludes
`domain` deliberately, which is right for consolidating one head. Reused here it
also collapsed two blockers at different LOCATIONS and on different HEADS into a
single escape, and dropped the second from the report. Location and head are now
part of the identity; domain still is not, and severity is upgraded on collision
to agree with `consolidate_findings` rather than contradict it.

**14. Disposition is resolved across the whole log.** Entries are immutable, so
an overturn discovered after the review was recorded can only arrive as a later
entry naming the same finding — the one reachable case, which the first
implementation ignored.

**15. The prose half needed the same care as the fence.** A `note` was written
raw into the region a person reads, so one `review log` call could forge a
visually complete second entry: an `attested` `security` pass by a seat that
does not exist. Free text is now quoted line-by-line, control characters and
backticks are refused, and `escaped-defects` reports
`unverified_attested_entries` — `attested` claims in the file with no
`source='attested'` row behind them. `entry_id` is a digest, not a signature;
the control plane is the only corroboration available.

**16. A weak record must not destroy a strong one.** `review log` shared the
upsert with the ADR-0039 path, so it could overwrite an attested receipt's
verdict and marker and DELETE its findings. It now refuses that receipt by name
and still writes the entry. Conversely a validated receipt is no longer lost to
bad caller metadata: the entry degrades and says which field was dropped.

**17. The log must be a regular file, and the write must complete.**
`--reviews-path /dev/null` returned success, recorded nothing, and minted a
receipt pointing at an entry that existed nowhere; a fifo blocked forever. The
append is now a single `O_APPEND` write whose byte count is checked — the
previous version created the file on a separate non-append descriptor and
buffered its header to offset 0, which could overwrite a concurrent writer's
entry with both writers reporting success.

## Consequences

**Easier.** The retro can compute its second measure. #716's attribution table can
be recomputed against recorded data. Change 2 of #716 — promoting
`nightshift_review_plan.py` from `shadow_only` to binding — becomes evaluable,
because a router whose decisions are never recorded cannot be judged either.

**Harder, and named rather than implied.** A `local` or `reconstructed`
receipt satisfies `review consolidate`'s `missing_domains` — `consolidate`
selects on `domain` with no source filter, so both weak sources count there, which is what issue #717 AC-2 asks for
and is a real reduction in what that field proves. Before this change the field
was unreachable and proved nothing at all, so the comparison is against zero, not
against a working gate — but a release agent that treats `missing_domains` as
proof of attestation will now be wrong in a way it was not before. **`source` is
the field that distinguishes them and `review eligibility` does not yet read it.**
That is the first follow-up out of this change and it is deliberately not folded in
here: eligibility is ADR-0040's contract and changing what it gates belongs in its
own review. Checked rather than assumed — `eligibility_digest` is a hash of the
entire result body, and `tests/test_review_eligibility.py` asserts that
identity over every key except `eligibility_digest` and `ok`. So even an
advisory `unattested_domains` field cannot be bolted on here without editing
that test, which is exactly the signal that the change is ADR-0040's to make.

**Unchanged.** ADR-0039's threat model boundary. Every seat still authenticates
through one shared PAT, so nothing here proves *which agent* recorded an entry —
`local` provenance is honest about being a record, not a proof. ADR-0072 is the
decision that closes that gap; this ADR gives it a place to write its result.

## Cost

None monetary. Operationally: one file that now grows monotonically (append-only,
~25 lines per review) and one more column. `O_APPEND` single-write is the
concurrency discipline — read-modify-write would silently lose one of two
simultaneous reviews, which is this ticket's defect at a smaller scale.

## One-way door?

No. The log is append-only text with a versioned `schema` field; the column is
nullable and additive. Removing the mechanism leaves the file readable and the
column ignorable.

## Scale tier

None yet. A single markdown file is adequate to thousands of entries; the trigger
to move is `review escaped-defects` taking long enough that a retro skips it,
which is when the entries belong in the plane with the file as a projection.

## Reversal trigger

Revisit if a `local` receipt is ever used to justify a merge that an attested
receipt would have blocked. That is the failure this design accepts in exchange
for recording anything at all, and if it happens once, `review eligibility` must
start refusing unattested sources rather than counting them.
