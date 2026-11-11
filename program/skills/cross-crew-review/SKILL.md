---
name: cross-crew-review
description: Route every PR to a different crew for independent review before integration — round-robin assignment, frozen-contract review, one consolidated finding stream, disputes settled by the program.
---

# cross-crew-review — an independent org unit checks the work

Inside a crew, a different *vendor* reviews the diff. That catches defects. It does
not catch a crew that has convinced itself its plan is fine, because everyone in the
review chain shares that plan. A reviewer from another crew has no stake in it.

This is the last gate before the integrator merges. It is also the cheapest place
to catch a design that only makes sense from inside one crew's context.

**Note a divergence, so nobody builds on the wrong half of it.** This skill says
every pull request gets one. The risk classifier disagrees: it asks for a
cross-crew review only on its highest-risk profile. Nothing consults the
classifier before dispatching a crew review today, so the two never meet — but
gating dispatch on it would refuse this review on most pull requests, which is
why #721 was built, measured and withdrawn. Settle which rule is real before
writing anything that routes this review by risk profile.

## Assignment: round-robin, no negotiation

Order the crews as they appear in your tool list. Crew *i*'s PR is reviewed by crew
*i+1*, wrapping around. With three crews: alpha → beta, beta → gamma, gamma → alpha.

Fixed rotation on purpose — it spreads load evenly, it is predictable, and nobody
negotiates who reviews whom. Two exceptions, both narrow:

- The next crew authored a change this PR depends on and would be reviewing its own
  interface. Skip to the following crew.
- The next crew has a grant of zero this wave and is idle-by-design. Use it anyway —
  reviewing is cheap and it is the one thing an unstarted crew can usefully do.

With only ONE crew configured there is no cross-crew review. Say that explicitly in
`STATUS.md` rather than letting the in-crew review quietly stand in for it.

**With exactly TWO crews — the shipped default — the rotation degenerates**: alpha
reviews beta and beta reviews alpha, mutually, every wave, which is precisely the
reciprocity structure the rotation exists to prevent. It cannot be fixed by
assignment at N=2, so compensate for it:

- **Staff spot-audit.** Route every third integrated PR to `staff` for a
  post-merge audit against its criteria and ADR. Staff is outside both crews and
  has no PR of its own in the game; an escaped defect it finds is charged to the
  reviewing crew in `.omni/reviews.md`, not just the authoring one.
- **Alternate the reviewing vendor** inside the reviewing crew wave by wave
  (reviewer_claude ↔ reviewer_codex), so the same two model-vendor pairs do not
  settle into a stable mutual pattern.
- **Watch the retro numbers.** Falling blocking-counts with stable escaped
  defects is the rubber-stamp signature; the retro reads `.omni/reviews.md` for
  exactly this, and at N=2 it is the primary defence, not a backstop.

## Frozen input and review ownership

Before the first implementation commit, copy
`docs/reviews/REVIEW-CONTRACT-TEMPLATE.md` into a committed work-item/spec artifact
and freeze it at the wave checkpoint. The PR links and may mirror that exact
committed version; its mutable body is never the authority. The artifact carries
stable criteria, explicit exclusions, base SHA, contract
version, ADR, required domains, and one review owner. A criteria, scope, ADR, or
domain change after that point creates a visible new version and invalidates every
affected approval; it never quietly edits the version reviewers already used.

The assigned cross-crew lead is the review owner. It collects every domain verdict
and publishes one deduplicated fix packet per round. A severity change retains the
original finding and gives a visible rationale. If the author disputes what remains,
the program decides once in `.omni/decisions.md`; parallel blocker streams do not go
back to the implementer.

## What the reviewing crew gets

Dispatch it with `purpose: "review"` and exactly this:

- the diff (`gh pr diff <n>`) as text
- the frozen review contract, including acceptance criteria and exclusions
- the ADR the work was built on
- the PR body

Never the implementer's transcript, never a pointer to their worktree, never your
own summary of what they were trying to do. The independence is the entire value,
and a helpful preamble from you contaminates it.

Every domain uses exactly `MERGE_BLOCKER`, `PRE_DEPLOYMENT`, or `FOLLOW_UP`:

- `MERGE_BLOCKER` means the diff violates a frozen criterion or
  introduces/activates a correctness, security, data-loss, or
  default-branch-integrity failure. It cites the criterion or protected invariant,
  exact changed hunk, reachable execution/data path, and a concrete failure scenario
  or safe deterministic reproduction. Never execute a reproduction against a live
  system. Missing the proof makes it unproven, not a blocker.
- `PRE_DEPLOYMENT` means source may merge but cutover awaits named live, migration,
  rollout, authorization, or environment evidence. It never blocks source merge.
- `FOLLOW_UP` means pre-existing or unreachable behaviour, or requests for unrelated
  polish, convention, scale, or scope expansion. Changed behaviour that violates an
  explicit exclusion can still be a `MERGE_BLOCKER` with the required causal proof.
  Route genuine follow-up work to a separate item; it never holds this PR.

After round one, a new `MERGE_BLOCKER` must point to bytes or criteria changed since
that round, or name a direct causal dependency the earlier review demonstrably
missed. The same reviewer rechecks the same contract and states which case applies.

## Routing the verdict

- **Clean** → record a scoped attestation: reviewer, domain, reviewed head SHA/tree,
  files, blob OIDs, declared dependency seams, and evidence. The PR is eligible for
  integration only when its frozen contract's required domains are clean.
- **`MERGE_BLOCKER` findings** → back to the authoring crew, which routes them to the SAME
  implementer, on the SAME PR. Then the same reviewing crew re-checks — a fresh
  reviewer would re-litigate settled ground.
- **Dispute** → the authoring crew may push back once, in a paragraph. You settle it,
  in writing, in `.omni/decisions.md`, naming which side you took and why. Do not
  bounce it between crews: two crews arguing through a relay burns a wave.
- **`PRE_DEPLOYMENT` findings** → into the release/cutover plan, not the source
  merge gate.
- **`FOLLOW_UP` findings** → a separate work item, not another review loop. The
  implementer-facing fix packet never carries one (#222, ADR-0057): before
  filing, check this round's `nightshift-ctl review consolidate` output so a
  finding two or more domains surfaced identically (same fingerprint) files as
  one backlog issue, not one per domain — in-round dedup only.
- **Findings that are really product questions** → to the authoring crew's `pm`, not
  to the implementer.

Content review may transfer to another head only for identical blob OIDs whose
declared dependency seams are unchanged. CI, commit/authorship provenance, PR body,
authorization, and installed/live-runtime evidence are always rerun; matching tree
or blob content never transfers those gates.

## Anti-patterns this exists to prevent

- **Rubber-stamping.** A review that reports nothing must still say what it checked
  and what it deliberately did not. "Looks good" is not a verdict.
- **Style wars.** The reviewer judges against the author's ADR and criteria, not
  against its own crew's preferences. Convention disagreements go to `staff`, which
  owns conventions, and never block a PR that follows the written ones.
- **Scope creep by review.** A reviewer asking for work outside the frozen criteria
  is filing a `FOLLOW_UP`, not a `MERGE_BLOCKER`. Route it as such.
- **Reciprocity trading.** Rotation is fixed precisely so nobody can soften a review
  in exchange for a soft one back. If you see two crews consistently clearing each
  other with thin verdicts, that is a retro finding with names and numbers attached.

## Recording it

**Run this. Do not hand-edit the file.** A completed review is recorded with one
command, which appends the entry to `.omni/reviews.md` and mints the
`review_receipts` row the release gate reads:

```
nightshift-ctl review log --report-json - <<'JSON'
{"repo": "<owner>/<name>", "pr": <n>, "task_id": "<task>", "head_sha": "<40-hex>",
 "domain": "cross_crew_review",
 "author_seat": "<crew>/<agent>", "reviewer_seat": "<crew>/<agent>",
 "vendor": "<Claude|Codex|...>", "verdict": "pass|fail", "loops": <n>,
 "independent": "yes|no|unknown",
 "slot_state": "held|absent|unknown",
 "slot_id": "<id — ONLY with slot_state held>",
 "slot_absence_reason": "<why — ONLY with slot_state absent>",
 "findings": [{"severity": "MERGE_BLOCKER|PRE_DEPLOYMENT|FOLLOW_UP",
               "criterion": "<AC or invariant>", "location": "<file:line>",
               "summary": "<what is wrong>",
               "disposition": "open|accepted|fixed|overturned|upheld|deferred",
               "disposition_note": "<required for the last three>"}]}
JSON
```

Those two `slot_*` fields are mutually exclusive — sending both is refused
(`slot_id_contradicts_state`), and so is sending neither with `absent`.

Then check it landed: `nightshift-ctl review escaped-defects` must show your
entry in `entries`. It will not show up in `escaped` — that counts defects that
got past a gate, and a clean review contributes zero to it. Both halves of the
file matter: the fenced block is what the retro parses, the table above it is
what a person reads.

**Record the review even when the accounting is broken.** A review that ran
outside the semaphore, or on a repository where posting a PR comment would
trigger a workflow, still gets logged — set `slot_state` to `absent` and say why,
or `unknown` if you genuinely cannot tell. `review log` will tell you if the
receipt could not be minted; that is information to report, never a reason to
skip the entry. This whole section exists because a 12-hour run with two real
cross-crew reviews left this file byte-identical, and an org redesign was then
drafted on the empty reading (#717, #716).

**Record the disposition, not just the severity.** A finding the author
disputed and lost (`upheld`), one that turned out to be wrong (`overturned`),
and one deliberately filed rather than worked (`deferred`) are three different
outcomes, and the retro's escaped-defect count treats them differently —
`overturned` is the only one that removes a finding from the total, which is
why it must carry a reason. Default is `open`; leaving it there says the
finding was raised and its outcome is not recorded, which is honest.

**A review kind the domain list does not name still gets recorded.** The frozen
set is `pm_acceptance`, `code_review`, `qa`, `cross_crew_review`, `staff_adr`,
`security`, `ux`. If what you ran is none of those — an architecture review, a
rollout sequence, an on-call runbook — add `"domain_outside_vocabulary": true`
beside your own `domain` name. The entry lands in the file and no receipt is
minted, which `review log` says by name. Do not reach for this to work around a
typo: without the flag an unrecognised domain is refused, and that is the point.

**A review that came through `nightshift-ctl review receipt-record` is already
logged** — that command writes the entry itself. Pass it BOTH `--author-seat`
and `--reviewer-seat` (plus `--independent yes`), or the entry records
`independent: unknown`, because nothing else in the system can prove who
reviewed whom (ADR-0072 F-102) and a guess is worse than a blank. `--vendor`
and `--slot-id` are recorded when given but do not affect that; only the two
seats do. If any of them is malformed the receipt is still persisted and the
entry is written with the bad field dropped — `entry_degraded_reason` says
which — because validated evidence is not worth losing over metadata.

The retro reads this file to compute escaped defects — bugs found after a
cross-crew review passed — which is the single best measure of whether this gate
is real or theatre.
