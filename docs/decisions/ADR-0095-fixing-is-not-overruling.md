# ADR-0095: A recorded blocker is not weakened by re-attesting the head it was raised against

Date: 2026-09-07 · Status: accepted
Deciders: platform (owner asleep; decisions taken from the evidence recorded below)
Issue: #734, child of epic #716 (change 6 of six — the last)
Supersedes: nothing. Enforces ADR-0026's existing rule, which was prose.
Unaffected: ADR-0039's attested receipt path apart from this check, ADR-0040's frozen contract.

## Context

#716 change 6: *"A team may not overrule the reviewer checking its work. Both teams downgraded
findings raised against them. That is not a coverage problem and no amount of extra reviewing
fixes it."*

The run's own record: *"BOTH CREWS DOWNGRADED THEIR OWN REVIEWERS — crew_alpha ranked three of
`reviewer_codex`'s four raised blockers DOWN; crew_beta downgraded one of its reviewing seat's
MERGE_BLOCKERs to PRE_DEPLOYMENT."*

The rule already existed in four places — ADR-0026's *"it cannot silently downgrade a finding"*,
the cross-crew-review skill, and both crew configs. None of it was checked by anything, which is
the sentence this epic opens with, arriving one last time.

## The first implementation was refuted, and that is the useful part

It gated `review log`. An adversarial review showed **the gate was on a door that was already
locked**: `cmd_review_log` refuses to overwrite an existing receipt, so a second logged entry for
one `(task, head, domain)` never reaches `_write_review_findings` at all. Net effect on the
gating table: **zero**. Every downgrade attempt in its own test suite was returning
`receipt_skipped_reason: existing_receipt_from_local`, and its one end-to-end test passed with
the gate deleted.

Meanwhile the door that is open was untouched. `_write_review_findings` DELETEs every row for
`(task, head, domain)` and re-inserts what the new payload carries, and its other caller is
`review receipt-record` — the **attested** path, the higher-trust one. `review marker-mint` is
unauthenticated, so the crew under review can mint a marker for its own task and head, post its
own comment, and rewrite the table. Demonstrated end to end at exit 0:

```
1) crew_beta attests MERGE_BLOCKER      -> rows: [("MERGE_BLOCKER","the fence is inert")]
2) crew_alpha re-attests it FOLLOW_UP   -> exit 0, rows: [("FOLLOW_UP","the fence is inert")]
3) crew_alpha attests with NO findings  -> exit 0, rows: []
```

Recorded at length because the failure was not a bug in the check but a wrong belief about where
the damage happens — and that belief is easy to re-form from reading either call site alone.

## Decision

At the point where the DELETE happens, keyed on **what the DELETE is about to remove**: a
re-attestation may not drop a recorded MERGE_BLOCKER for that head, lower it, or mark it
`fixed`/`overturned`. Refused before the receipt is upserted, so a rejected re-attestation leaves
both the findings and the verdict untouched.

### It needs no seat identity, and that is what makes it enforceable

The first version asked *"is the party filing this the crew under review?"* — which the attested
path cannot answer (it carries no seat) and which #725 says means nothing anyway.

It is not needed. **On a given head a finding never legitimately gets weaker.** Eligibility gates
per head, so a genuine fix produces a new commit, a new head, and a fresh review recorded against
that head; the old head's blocker does not follow the code forward. Nothing has to be cleared for
the fix loop to work — which a test proves by clearing a blocker the only honest way, at a new
head.

This is the first gate in the epic that does not end at #725's wall. Changes 5 and 6's first
drafts both did, because both asked *who* is acting. Asking *what is being removed* needs no
identity at all.

### Only MERGE_BLOCKERs are protected

Guarding every severity refused a reviewer revising its own review: an existing test
(`test_recapture_after_remint_replaces_the_receipt_and_its_findings`) re-mints and re-posts a
corrected block that drops a FOLLOW_UP and adds a MERGE_BLOCKER — the record getting *stronger* —
and the broad rule refused it. That is the false refusal that gets a gate deleted rather than
argued with. A MERGE_BLOCKER is the only severity that stops a merge, so it is the only one whose
removal changes what ships.

### One call site, not two

`_write_review_findings` has two callers and gating both looked obviously right. It is not:
`review_findings` rows are only ever written alongside a receipt, and `review log` refuses once a
receipt exists — so a logged entry can never reach a head that already carries findings. A gate
there is code nothing can invoke, which #729 exists to refuse. A mutation confirmed it by failing
no test. The asymmetry is pinned by a test, because it is not visible from either call site.

## What this does not do

- **`fixed` on the attested path was never expressible.** It looked like the cheapest bypass —
  `_write_review_findings` skips `fixed` and `overturned` identically and nothing verifies a fix
  happened — but the attested block schema does not accept a `disposition` key, so the move is
  refused as malformed before this check runs. A test pins that, as a tripwire for the day that
  validation widens.
- **It does not check who reviewed.** Nothing verifies the attesting comment's author against the
  seat that was dispatched. That is a real and larger gap; it is about who *did* the review
  rather than who erases it, and it needs #720's dispatch record joined to the attestation path.
- **It does not stop a blocker being avoided by never recording one.** This protects a finding
  once written down. Changes 1 to 4 are what make it get written down.

## Acceptance boundary

Both directions on each, driven through the real binary:

- Re-attesting a recorded blocker lower is refused; re-attesting it unchanged is fine.
- Re-attesting with the blocker omitted is refused — *"a finding is not withdrawn by omission"*.
- The receipt is not written either, so the verdict cannot flip to pass while the blocker stands.
- **A fix clears it at a new head** — the load-bearing control, because the rule is only
  defensible if the fix loop still works.
- A lesser finding may still be revised away; another domain's identically worded finding is
  untouched.
- Adding a second blocker is fine.

## Consequences

The oldest written rule in the review system becomes one a machine checks, on the path that
actually rewrites the table.

The cost is that a genuinely mistaken blocker now needs a new head to clear — a crew cannot
retract it in place even when everyone agrees it was wrong. That is a real friction and the right
default: the epic's finding was not that crews are always wrong to disagree, it was that nothing
distinguished the times they were.

## One-way door and reversal trigger

Not a one-way door: one function and one call site.

Reverse if refusals cluster on genuinely mistaken findings rather than on real overrules. The fix
then is a way to retract a finding that requires a second party — not a weakening of this rule,
and not a return to letting the head under review rewrite its own record.
