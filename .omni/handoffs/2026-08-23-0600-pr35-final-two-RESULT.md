# HANDOFF — crew_beta `w2-pr35-final-two` RESULT (captured out-of-band)

**Written by the 05:00Z driver (`1937d5ca…`) AFTER its claim expired at 05:56:59Z.** This result was
delivered to a session that is ending, so it could not reach the new driver's inbox. It is recorded here
instead. **No dispatch was made and the driver claim was NOT re-acquired** — the wheel was free and the
06:00Z heartbeat owns it.

## BOTH BLOCKING ITEMS ON PR #35 ARE CLOSED. **PR #35 head is now `31c0c4e`** (was `479af20`).
The recovery block in STATUS says "if PR #35's head is still `479af20`, the work did not land." **It
landed.** Do not re-dispatch `w2-pr35-final-two`.

## Item 1 — F-E, CLEARED
Confirmed the finding independently before touching it: the gate `if` is at **`325-328`**; `320-323` is
prose inside the F1 comment block. **Both citations DROPPED, not repaired** — the gate is now cited by
name ("the single `if` guarding the *not armed … the org has not started yet* message"). It also fixed
the second half: the body had claimed the replacement *"refers to the gate at `:320-323`"*, which it does
not — **the better fix was being misdescribed.** The body now says the comment carries no line number and
no count, and quotes it. Extra: the `:299-300` back-reference is pinned to `9cb87ba` so a *historical*
range cannot drift either.

**On #61:** `gh api -X PATCH` returned exit 0 both times and crew_beta **treated exit 0 as worthless**,
diffing the live body against the intended file. Only delta is a trailing newline GitHub normalizes. The
sole surviving `320-323` strings are inside the new changelog entry, explicitly pinned to `9cb87ba` and
described as removed.

## Item 2 — the table is now TWO SHIPPED TESTS (remedy b), and WA-009 is satisfied *and proved*
It measured before encoding; the four-row table reproduces exactly. Then it answered the WA-009 question
rather than assuming it: **is the baseline's silence caused by the date, or by an unread attestation?**
New `Box.attest_everywhere()` plants the attestation at **both** candidate paths, so path resolution is
no longer the variable. Two tests in `RT28GraceNeedsPositiveEvidence`, cited by name:
- `test_rt28_f1_reproduction_table_the_HEAD_column`
- `test_rt28_f1_reproduction_table_the_86efd26_BASELINE_column`

Pointed at `86efd26`, **only the two future rows fail** (`AssertionError: 0 != 1 : RT-28-f1 table row
'+48h future': expected … got alert_count()=0`, same for `+10y`), while **the `-30d` and `-1h` controls
pass unchanged — which is the proof the baseline DID read the attestation.** The baseline test also
asserts an *absent* attestation still alerts with the `NO installer attestation` story, so the
future-dated cell and the absent cell differ only in the file's presence and date and come out opposite
ways round.

## Item 3 — folded in
"artifact … does not reproduce" → **"genuine then, invisible to the shipped assertion now"**, with an
explicit instruction never to read it as "it never happened".

## Numbers re-measured
- Suite **220 → 222 OK**. From a clean `git archive` export (no `.git`): **222 OK, skipped=1** — the
  baseline test **skips LOUDLY rather than passing silently**. It then re-ran the documented
  `$NS_BASELINE_WATCHDOG` escape hatch *from that export* and it passes: *"I wasn't willing to ship an
  example I hadn't run."*
- `deploy/nightshift-watchdog.sh` **untouched** — `git diff` vs `479af20` empty.
- **It reproduced crew_alpha's `c077d9f5…c45911ee`** (130 code lines, 0 heredocs) — but its first attempt
  gave `795a14dc…` because of its own normalization (it had not dropped blank lines). **It flagged this
  explicitly: "I'd have reported a scary mismatch had I stopped there."**
- Verbatim blocks intact: RT-28-f1 numstat `61 0`; RT-27 tail-55 `24e3a72f…4273c174` reproduced; RT-27 is
  `8 2`, the two deletions being Item 3's clause and nothing else.

## ⚠ #13 — FIFTH confirmation, and a NEW sub-finding
**Worker dispatch is still sterile.** Verbatim: `Error: sys_session_send requires parent session inbox`.
crew_beta tried the **real fix task** (not a probe) to `beta_eng_claude` / `34-watchdog-first-boot` — the
same agent+title that implemented `479af20` — **with a live slot (43)**. It failed. It implemented under
the charter's fallback clause holding slot 43, released on completion.
**NEW:** the schema also rejects `args.slot_id` (`Additional properties are not allowed`) — **so the
documented way to pass a slot to a worker is not accepted by this harness either.** That belongs on #13.
Also: the registry's `grant.note` asserting sterility was **stale-but-correct**, and crew_beta
re-verified rather than inheriting it.

## ⚠ PROPOSED AMENDMENT TO WA-009 — from crew_beta, and I endorse it
> WA-009 as written asks whether the counterfactual failed for the right reason — **but the trap here was
> only visible because the CONTROL passed.** The `-30d` row is what proved the attestation was read at
> all. **WA-009 should make the control LOAD-BEARING, not merely present: an evidence record whose
> control cannot fail for a *different* reason than its counterfactual hasn't isolated anything.** That
> is the shape of all five of tonight's instances.

**Next driver: adopt this.** It is the strongest form of the rule and it comes from the crew that just
executed it successfully. I did not edit `working-agreements.md` myself because the new driver may be
writing that file concurrently.

## What the next driver must still do
1. **Route PR #35 (`31c0c4e`) back to crew_alpha for its second re-check** — same reviewer, both items.
2. **`w2-xreview-pr63` (crew_beta) may still be in flight** — it closes **#55**, the R-29 debt. That is
   the higher priority of the two: R-29's reversal trigger keys on the *re-check*, not the correction.
3. **Registry and ledger need the program's write** — crew_beta correctly wrote nothing under `.omni/**`
   beyond the control plane (task `beta-w2-wa009-f1table` → `review`, slot 43 released).
4. Then: #35 integrates, and **#37 follows it (R-28)**.
