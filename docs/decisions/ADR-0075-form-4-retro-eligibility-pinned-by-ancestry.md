# ADR-0075: Bound form (4) retro-attestation eligibility by ancestry through a pinned SHA

Date: 2026-09-02 · Status: accepted

Deciders: program · Findings contributed by: architect (Codex), security, crew_alpha, crew_beta

Conversation: `e7d174d324df4629b35eb1d67253cc95` · Task: `520-d22-review-form` ·
Issue: #520 · PR: #626

Pinned SHA: `00eb294508ccf10658e333840854993242dd7243`

Advancement trigger: the pin moves only when the audited corpus is re-run at a later tip, and
never to make a newer PR attestable.

## Context

Condition 22 of the definition of done requires a recorded cross-crew review for every change
this project produced. `tests/dod_validate.py`'s `check_d22` accepts four evidence paths. The
fourth — form (4), a structured retroactive review form — exists because `security`'s replay
classified roughly 48 code-touching PRs that carry a real, substantive cross-crew review
(shortest 655 bytes, median 5,628) which the validator could not bind to the PR. Those are a
tooling gap, not a review gap. They are distinct from the roughly 51 code-touching PRs with no
review evidence anywhere, which are a remediation backlog.

Form (4) is an attestation filed after the fact by whoever types it. It is strictly weaker than
an ADR-0039 receipt: no control-plane-minted marker, written after the review rather than during
it, and it cannot prove the filer differs from the author (F-102). It therefore needs a bound on
*which* PRs it may speak about, or it stops being a remediation instrument and becomes a
permanent second route around ADR-0039.

The first implementation had no such bound. It accepted on `filed-at > mergedAt` with no upper
limit on `mergedAt` anywhere in the acceptance path. Four domains found this independently by
four different methods, and `crew_beta` named the shape exactly:

> `filed-at > mergedAt` proves POSTERIORITY, not RETROACTIVITY — the same shape as the withdrawn
> form (3)'s reachability proving ancestry, not review.

A PR merging next December, with ADR-0039 receipts available and simply unused, was rescued on
identical terms to the 48 genuine historical cases, while the emitted disclosure line stayed
honest and the population it described grew without limit. That is the decay the honesty
requirement exists to prevent, which is why it was a merge blocker.

**The audited corpus this decision is about.** `security` replayed **224 in-range PRs** against
their **real head SHAs and real merge commits** with `main` at
`00eb294508ccf10658e333840854993242dd7243`, scoring every probe that carries no per-PR
information at 0/224 and confirming the population splits into roughly 120 `.omni/**`
bookkeeping PRs, roughly 48 with a real but unbindable review, and roughly 51 with no evidence
found. That replay, at that tip, is the identity of the set form (4) is permitted to attest.

`staff`'s D22 scope ruling accepted retro-review of the ~51 no-evidence population. Any bound
adopted here has to keep those PRs remediable, or it overturns that ruling by implementation.

## Options considered

### A — Ancestry through a pinned SHA (chosen)

A PR is eligible for form (4) if and only if its merge commit is an ancestor of
`00eb294508ccf10658e333840854993242dd7243`:

```
git merge-base --is-ancestor <mergeCommit> 00eb294508ccf10658e333840854993242dd7243
```

The pin is the tip `main` stood at when the corpus above was audited, so **the eligible set is
the audited set**. Merging more work moves `main` and never moves the pin, so the eligible set
cannot grow. Only git's own `rc=0` counts as eligible: an absent merge commit, a SHA git does
not know (`rc=128`), or git being unavailable are all unanswered questions, and an unanswered
question is not a satisfied bound — *no ancestry, no evidence*, the same rule as the
posteriority check's *no clock, no evidence*.

It also reads **neither clock**. Round 1 established that git's committer clock and GitHub's
merge clock disagree by one second on the very commit that would define a date boundary
(`git log -1 --format=%cI 00eb294` is `2026-09-02T18:08:26Z`; `gh pr view 587 --json mergedAt`
is `...:27Z`), so any date anchor requires an argument about skew that an ancestry test never
has to have.

Verified before adoption: PRs #1, #564, #582 and #587 all resolve ELIGIBLE, and
`git rev-list --count 00eb294..origin/main` was 0 at adoption. #582 is the case that decides it —
merged directly, that same day, with no review, and in the ~51 no-evidence population.

### B — A calendar cutoff (`mergedAt < 2026-09-03T00:00:00Z`) (rejected)

This was implemented and pushed before being rejected. Its residual — merges inside the bound
that were not in the audited corpus — measured **0**, and that measurement was true only at the
instant it was taken. Five PRs (#626, #627, #624, #594, #596) were due to merge that same night,
before the constant's own instant, each with ADR-0039 receipts available. By the following
morning the residual would have been **5**, and five PRs that could have used the stronger path
would have become retro-attestable. That is the original merge blocker re-entering through the
anchor rather than through the ordering.

A calendar anchor at a *past* instant has no residual but strands real work: any anchor
excluding "today" would put #582 permanently beyond remediation, since it merged that day with
no review and is in the population `staff` ruled must remain remediable.

### C — An explicit allowlist of the 48 (rejected)

Enumerate the PR numbers already classified as carrying a genuine but unbindable review, and
permit form (4) only for those. This is the most selective option available and it was offered
as a preference by `architect`.

Rejected for two reasons. It duplicates a bound the pin already provides, with a maintenance
burden and a second thing to get wrong. More importantly it is **inconsistent with `staff`'s
D22 scope ruling**: an allowlist of the 48 would forbid retro-review of the ~51 no-evidence
PRs, which that ruling explicitly accepted. It would settle a policy question by omission from
a list.

### D — Leave the pin and its reversal rule in implementation comments (rejected)

This is what rounds 1 and 2 shipped, and `architect` returned FAIL on it. Every test written
against the pin stayed green if someone advanced the constant while updating or deleting the
comments that justified it. The protection was exactly as strong as a comment, which is to say
not a protection. This project's rule is that non-obvious decisions and their reversal triggers
live in an ADR, never only in code or chat. This ADR is that fix, and
`test_the_pin_matches_the_unique_accepted_adr_that_carries_one` binds the implementation constant to the SHA
recorded above, so advancing the pin requires superseding an accepted ADR rather than editing a
comment.

## Decision

Form (4) eligibility is bounded by ancestry through the pinned SHA
`00eb294508ccf10658e333840854993242dd7243`, recorded in this ADR and asserted equal to
`tests/dod_validate.py`'s `FORM_4_ELIGIBLE_THROUGH` by a test that reads this file.

The pin moves **only** when the audited corpus is re-run at a later tip. When it moves, this ADR
is superseded by one naming the new tip, the new corpus and who re-ran it, and the DoD changelog
records the move. **The pin must never be advanced to make a newer PR attestable.** Advancing it
without a re-run of the audit is precisely the failure this bound exists to prevent, and it would
be indistinguishable in the diff from the legitimate move — which is why the justification lives
here rather than beside the constant.

Eligibility is one of three independent bounds, all required together: this ancestry test; a
posteriority test measured on the comment's own GitHub `createdAt` rather than the `filed-at`
the filer types; and the SHA binding requiring the reviewed SHA to be one of the PR's own
commits (reachable from the head, not from `mergeCommit^1`).

## Consequences

**Stated plainly, because it is the part that could be read as a regression and is not.**
Ancestry is **stronger** than the calendar cutoff it replaced: it admits none of the post-audit
merges a date anchor would have admitted, and it has no clock ambiguity to argue about. It is
**weaker than an allowlist in selectivity**: every merge inside the pinned ancestry is eligible,
including the ~51 no-evidence remediation population, not only the ~48 already classified as
genuinely reviewed.

**That is an explicit policy choice, not a recurrence of the original merge blocker.** The
blocker was an *unbounded* population that grew without limit while the disclosure text stayed
honest. This population is bounded, finite, and fixed at a named audit. The choice is that the
~51 must remain remediable, which is what `staff`'s D22 scope ruling requires; a bound that
excluded them would settle that question by implementation instead of by ruling.

What this makes easy: a closed, enumerable eligible set that cannot grow, verifiable with one
git command, with no dependency on either clock.

What it makes hard, deliberately: advancing the pin. It now requires superseding an accepted
ADR, which is the entire point of MB-3.

What it forecloses: using form (4) on anything merged after the audit. That is intended — such a
PR had ADR-0039 receipts available and must use them.

Filing forms against the eligible population remains a separate decision with its own disclosure
requirements. This ADR bounds who *may* be attested; it attests nobody, and no form is filed in
the repository at this commit.

## Cost

Tier-0 local-file infrastructure. One `git merge-base --is-ancestor` invocation per PR whose
stronger evidence paths have already declined — no service, no recurring monetary cost now or at
100×, no new operational dependency. The operational surface added is this ADR and the test that
binds the constant to it.

## One-way door?

No. The pin can be moved by superseding this ADR, and form (4) can be removed entirely without
migration — it accepts nothing that is not also disclosed in the emitted verdict line, so
removing it can only make condition 22 stricter.

## Scale tier

None yet. This is a bound on a historical corpus of 224 PRs and does not scale with anything.

## Reversal trigger

Supersede this ADR if any of the following becomes observably true:

1. **The audited corpus is re-run at a later tip.** The pin moves to that tip; the superseding
   ADR names the new corpus, its size, and who replayed it.
2. **A form (4) attestation is found to have laundered a PR that was not genuinely reviewed.**
   The remedy is to narrow eligibility — an allowlist becomes the right answer despite option C's
   rejection — or to withdraw form (4) as form (3) was withdrawn.
3. **F-102 is closed** and reviewer identity becomes mechanically provable (ADR-0072's direction).
   A retroactive attestation whose filer can be authenticated is a materially different
   instrument and this bound should be re-derived rather than inherited.

A preference for attesting a newer PR is **not** a reversal trigger. It is the condition this ADR
exists to refuse.
