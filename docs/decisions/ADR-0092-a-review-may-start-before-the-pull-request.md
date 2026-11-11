# ADR-0092: A review may start before the pull request, on the evidence the PR was standing in for

Date: 2026-09-07 · Status: accepted
Deciders: platform (owner asleep; decisions taken from the evidence recorded below and in #724)
Issue: #724, child of epic #716 (change 3 of six)
Supersedes: ADR-0033 §5, **only** on which evidence admits a `review` continuation. Its QA row,
its classification model, and everything else it decides stand unchanged.
Unaffected: ADR-0039's attested `receipt-record` path and ADR-0040's frozen eligibility contract.

## Context

#716 change 3: *"Reviewers were blocked until a pull request existed, so they could only work at
the very end, once. Their findings were the two hardest problems in the whole run."*

The block is real and was observed rather than theorised. In the #730 run both wave-1 reviewer
slot acquisitions were DENIED and engineer seats did the review instead — which is also why the
epic's first attribution pass concluded the reviewer seats were ceremony, and had to retract it.
The run's own ledger: *"This is the FIRST genuine cross-crew review of the run. It was
structurally impossible until PR #731 existed… Pushing is what unlocked it."*

**Why the check exists matters more than that it exists.** ADR-0033 §5 required "admitted task
and task PR" for QA and review continuations. It is not evidence-binding for a receipt — that
lives in ADR-0039 and keys on `(task_id, head_sha, domain)`, not on a PR. It is *admission*
proof. An implementer continuation is bound to an exact live `task_claims` row; ADR-0062 records
that QA/review continuation has none — *"`claim_id IS NULL` structurally excludes it"* — and the
PR was the substitute for that missing claim.

Which means the fix is not to delete the check. It is to supply what the check was substituting
for.

## Decision

A `review` continuation is admitted by **either** `task.pr_url` **or** an active `task_claims`
row for the task. QA keeps requiring the PR.

The claim is the right substitute for two reasons. It is the evidence ADR-0062 named as missing,
bound the way the implementer continuation binds its own — this task's implementer, this run, not
predating the admission — so a claim left over from an earlier run does not admit a review. And
it bounds the early window without inventing a timer: the implementer's claim ends, and with it
the early review.

**What it is not, corrected here because the first draft of this ADR claimed otherwise.** An
adversarial review of this change demonstrated that a reviewer seat can mint the claim that
authorises its own early review: `claim acquire --claimant` is free text, checked against nothing
but the string `program`, so any seat with a shell can create an active claim naming the
implementer's agent and title (both readable from `task show`). The sentence "control-plane state
the reviewer cannot assert for itself" was wrong and is withdrawn.

It is withdrawn rather than fatal because the evidence it replaces was never stronger:
`task upsert --pr-url` is an ungated settable field, so the PR that ADR-0033 required could be
forged by the same seat in the same way. Neither is an authentication boundary. ADR-0033's gate
is admission ACCOUNTING — it answers "is this continuation coherent with the task's state", not
"is this caller allowed to say so" — and against that question a bound live claim is at least as
good as a PR URL string.

The honest statement of what this change costs is therefore narrower and worth writing down: it
gives a reviewer seat a *reason* to forge a claim it could always have forged, and a forged claim
takes the `one_active_claim` index, which locks the real implementer out of its own claim-first
recovery. That denial-of-service was reachable before this change and is not introduced by it,
but this change makes it useful. Closing it means authenticating `claim acquire` against the
calling seat, which is a control-plane-wide change affecting every claim, not a review-routing
one — filed as #725 rather than smuggled in here.

The root policy re-derives the same rule at dispatch, rather than trusting the slot row. That is
what makes it a recheck: a claim can lapse between acquiring the slot and using it, and then
there is no longer a build in flight to review.

### No new `continuation_kind`, and that is the substantive design choice

The first implementation added `early_review` as a distinct kind. It failed against a CHECK
constraint on `worker_slots.continuation_kind`, which SQLite cannot alter without rebuilding the
table — and the failure was correct. An early review **is** a review continuation; what differs
is the evidence admitting it, not the thing it is. Encoding it as a separate kind would have
meant a table rebuild to store a fact the task row already holds (`pr_url` empty or not), a
second uniqueness index duplicating ADR-0062's, and every future reader of `continuation_kind`
learning a distinction that carries no information.

Keeping the kind also keeps ADR-0062's index covering this case unchanged, which is worth more
than the label: a new index created with `IF NOT EXISTS` is a no-op on any control plane where
the old one already exists, so a widened predicate would have been green in tests and absent in
production — this epic's own defect shape.

## The recording half, which is not optional

`review log` (ADR-0090) refused any entry whose `pr` was not a positive integer, and the retro
aggregates on `(repo, pr)`. A review that ran before a PR existed therefore could not be written
down, and #716's agreed measure — *problems found after we declared the work finished* — is
computed from those records.

Shipping only the slot change would have let reviewers run earlier and have their findings land
nowhere: a control that exists and does not do its job in the situation it was built for, which
is the sentence this epic opens with. So `pr` may be null, under two conditions that keep the
widening to exactly one value rather than loosening a type:

- `provenance` must be `local`. A `reconstructed` entry is replaying history whose reviews had
  PRs; an attested one needs a PR comment to attest against.
- `task_id` must be present, or the entry is unattributable and counts as a unit of nothing.

`review_unit()` then keys a PR-less entry on its task. Keying it on `(repo, None)` would merge
every pre-PR review in a repository into a single unit — one "PR" reviewed many times and passing
once, which does not lose an escape but *fabricates* one. The `task:` prefix keeps a task id from
colliding with a PR number.

## Options considered

### A. Drop the `pr_url` check for reviews (rejected)
The obvious reading of "let reviewers start earlier", and it removes an admission gate without
replacing it. ADR-0033 built that gate because this continuation kind has no claim to bind to;
deleting it leaves the kind with no admission evidence at all.

### B. Accept a caller-supplied head SHA as the evidence (rejected)
Superficially better — a real commit is a real thing to review. Rejected because it is evidence
supplied in the same breath as the request, with nothing in the control plane corroborating it,
which ADR-0033's own Context refuses: *"caller labels therefore cannot be treated as universal
admission proof."* A bound claim is corroborated by the admission and the run even though, as
recorded above, it is not authenticated.

### C. A new `early_review` continuation kind (rejected, after building it)
See above. Rejected on the evidence of trying it.

### D. Reuse PLANNING (rejected)
PLANNING already runs pre-PR, but excludes the reviewer role by name and is defined for
*pending, unadmitted* tasks that it never admits (ADR-0033 §3). Early review is the opposite: an
admitted, building task. Overloading it would blur a phase whose design intent is that it changes
nothing.

### E. Claim as evidence, kind unchanged, recording widened (chosen)

## What this does not do

- **QA is untouched.** #716 item I calls QA's PR requirement legitimate and scopes QA's
  improvement to a brief-template change, *"not a code ticket"*. QA needs a built artefact; a
  reviewer needs a diff to read.
- **No attested receipt.** An early review mints no `review_receipts` row bound to a PR comment,
  and `review_receipts.pr_url` stays `NOT NULL`. ADR-0090's two-tier model already covers this.
- **Nothing requires early review.** This makes it possible. Whether the planner should start
  requiring it is a routing question, and routing now runs through ADR-0091.

## Evidence against, weighed rather than omitted

The epic's own material argues timing is not the only lever:

- **The wallet retraction** — three checkers, two vendors, blind cross-review, all agreeing and
  all wrong on a shared premise. Earlier would have made them wrong sooner.
- **The ReDoS** — *"Five seats, a cross-crew review and nineteen automated review rounds did not
  find it; a different class of tool did."* A tool-diversity finding, not a timing one.

Neither refutes this change: both concern what review catches, while this concerns whether a
reviewer can run at all before the end. But they set the reversal trigger below.

## Acceptance boundary

Both directions on each, because a gate observed only permitting is not evidence:

- A reviewer acquires a slot with no `pr_url` and a live claim; the identical acquisition with no
  claim is refused, and refused again once the claim is released.
- The fence allows that dispatch, and denies it once the claim lapses — proving the rule is in
  both places and not only in the CLI.
- A QA continuation without a PR is still refused.
- A PR-less review is written to `.omni/reviews.md` and the retro's count moves.
- Two pre-PR reviews of different tasks are two units, and a task id cannot collide with a PR
  number.
- A malformed `pr` (`0`, `-1`, `"4211"`, `true`) is still refused: the contract widened by one
  value, not by type.
- An ordinary PR review renders and counts exactly as before.

## What the adversarial review changed

The change was reviewed with instructions to refute it, and three of its findings changed the
code rather than the prose:

- **A pre-PR entry minted a receipt with a fabricated `pull/None` URL**, and the no-overwrite
  rule then made that findings-free `pass` final for `(task_id, head_sha, domain)` — so the real
  post-PR review of the same head could no longer record its MERGE_BLOCKER. A passing receipt
  standing in front of a blocker is exactly what the overwrite rule exists to prevent, reached
  through the front door. A pre-PR review now mints no receipt at all and says so by name.
- **The escaped-defect metric could never score an early review.** Pre-PR entries and the PR's
  own entries were disjoint units, so a defect that passed the early review and surfaced at the
  PR scored zero — the instrument structurally unable to answer the question #716 asked. The unit
  is now resolved through the log's own task-to-PR mapping, which also stops one defect reported
  on both sides being counted twice, and restores the reach of an `overturned` disposition
  recorded after the PR opened.
- **The claim was accepted unbound**, so a claim from a previous run or naming another agent
  admitted a review. Now bound as the implementer continuation binds its own.

Five of the suite's tests were also passing for the wrong reason — asserting a refusal without
its reason, or asserting a rationale rather than a behaviour. That is the second time in two
changes; the pattern is mine, not the reviewer's, and it is worth naming: a test written
immediately after the code it covers tends to assert what the author already believes.

## Consequences

Review stops being a thing that can only happen at the end. The cheapest version of a reviewer's
finding — before the branch is finished, while the author is still holding the context — becomes
reachable for the first time.

The cost is a weaker admission signal for that window. A live claim proves a build is in flight;
it does not prove there is anything worth reading yet. A reviewer dispatched too early reviews an
empty branch and wastes a seat. That is a real cost, and it is bounded by being a waste rather
than an unsafety: nothing merges on the strength of an early review, because eligibility still
requires the frozen contract's receipts and those still require a PR.

## One-way door and reversal trigger

Not a one-way door. The rule is one disjunction in each of two files; restoring `task.pr_url` as
the sole admission evidence reverts it, and every existing PR-based flow is untouched throughout.

Reverse if the retro shows early reviews finding nothing the late ones did not — the wallet
retraction and the ReDoS both predict that timing alone may not move the number this epic agreed
to measure. That is the honest test of this change, and it cannot be run until the org runs.
