---
name: retro
description: Wave-boundary retrospective that changes how the org works — reads the wave's own evidence, finds the pattern, and writes a binding rule into .omni/working-agreements.md that the next wave's charters must carry.
---

# retro — the only mechanism that makes the next wave better

Humans run retros badly because they rely on memory and social comfort. You have
neither problem: the whole wave is on disk. Use it. The output is not a feelings
summary — it is an edit to how the next wave runs.

## Inputs (read these, do not recall them)

- `.omni/registry-crew_*.json` — every task, its assignee, its state
- `gh pr list --state all --search "merged:>=<wave-start>"` plus each PR's review
  loop count
- `docs/decisions/` — ADRs written this wave, and `staff`'s verdicts on them
- `.omni/incidents/` — anything that broke, and its "what would have caught this
  earlier" line
- `.omni/decisions.md` — what could not be decided
- QA reports — criteria that could not be tested, flakiness rates
- `.omni/handoffs/` — did a consumer hit a gap the producer did not declare?

## Measure four things

Compute them, do not estimate them, and put last wave's number beside this one:

1. **Review loops per task** (from the registry). Rising means contracts are
   getting vaguer or the ADR is not being read.
2. **Escaped defects** — bugs found by QA or an incident AFTER a reviewer passed
   the diff. The single best signal that review is theatre rather than a gate.
   Compute it, do not read the file by eye:

   ```
   nightshift-ctl review escaped-defects            # whole log
   nightshift-ctl review escaped-defects --since <wave-start>
   ```

   With `--since`, `escaped` IS the wave's number — the whole-log figures
   move to `whole_log` rather than the other way round. It prints `escaped`,
   the split by severity, and every defect with the PR, the reviewing seat and
   why it counted (`after_pass`, or `declared:merge`).
   **Check `excluded_overturned` before you quote the number.** It counts
   blocking findings removed from the total because somebody marked them
   overturned — the one lever that lowers this metric without anything being
   fixed. A wave where it rises is a finding in its own right.

   **Put `entries_by_provenance` and `entries_by_independence` next to the
   number every time.** They say how strong the evidence behind that reading
   is — how many entries are `attested` rather than self-reported, and how
   many could not say whether the reviewer differed from the author. A number
   quoted without them is the kind of claim #716 found unsupported.
   **A refusal is a finding, but read WHICH refusal.** Five are different:

   - `no_entries` — the file exists and holds nothing at all. Reviews that
     happened were not recorded. Report that as the wave's result.
   - `entries_unreadable` — reviews ARE recorded and this reader cannot parse
     them; `unreadable_entries` says why for each. **Never report this as
     "not recorded."** It is a reader problem, not a recording problem.
   - `no_entries_in_window` — the log has entries, none in your window. A
     quiet wave, not an empty log.
   - `since_invalid` — your `--since` is not an instant. Fix the argument.
   - `legacy_entries_only` — the file holds reviews in the pre-ADR-0090
     format, and the count beside it says how many. **They were recorded.**
     Saying otherwise about a file full of real reviews is the very mistake
     this instrument exists to stop, and this reader made it once already.
   - `reviews_missing` — no file. Check you are in the org home before
     concluding anything.

   Reading a blank as a zero is what #717 exists to prevent, and it has
   already cost one org redesign.

   Also read `unreadable_entries`, `duplicate_entries` and
   `entries_without_fence` in the output. A non-zero `entries_without_fence`
   means an entry a person can read is not being counted.
3. **Rework** — tasks that reopened after being marked ready, and why.
4. **Blocked time** — how long items sat waiting on a handoff, a decision, or a
   rate-limited vendor. Compute it from the tracker: `blocked-by` label applied →
   removed per issue, plus lease grant→release from `.omni/leases/`. Estimated
   blocked time is a feeling; label timestamps are a number.

## Find the pattern, not the incident

One task going badly is noise. The retro's job is the repeat: the third time a
contract omitted the failure states, the second incident caused by an unflagged
migration, the recurring handoff that says "not guaranteed: nothing". State the
pattern in one sentence with the specific instances behind it.

Distinguish honestly between:
- a **process gap** — nobody was supposed to catch it (fix the process)
- an **execution miss** — someone was supposed to and did not (fix the contract or
  the prompt)
- **bad luck** — genuinely one-off (change nothing, and say so)

Attributing bad luck to process is how a working process accretes junk rules.

**Audit the skills too.** The skills are process encoded as files, and they rot
like any process. For each skill exercised this wave, ask: was any instruction
in it ignored, worked around, or followed into a bad outcome? A skill step every
agent skips is dead weight; one that misfired is a defect. You do not edit skill
files — name the skill, the line, and the evidence in the retro output so the
human can fix the bundle, exactly as with standing-prompt changes.

## Write the change

Append to `.omni/working-agreements.md`:

```
## WA-NNN <one-line rule>
Date: YYYY-MM-DD · From: wave <n> retro
Evidence: <the 2-3 instances that forced this>
Rule: <what must now happen, phrased so a lead can check it>
Applies to: <program | leads | engineers | qa | reviewers | release>
Retire when: <the observable condition that makes this rule unnecessary>
```

Rules that matter are specific and checkable: "every implement contract must name
the failure states from the spec" beats "write better contracts". A rule you
cannot verify in a charter is a wish.

**Cap it.** At most two new working agreements per wave, and every retro must also
consider RETIRING one: a rule whose `Retire when` has come true, or that has not
prevented anything. An ever-growing rulebook slows every future wave and is the
most common way process becomes the problem.

## Then make it binding

- Next wave's charters quote the working agreements that apply to them.
- If a rule belongs in a role's standing behaviour rather than a charter, say so
  explicitly in the retro output so the human can move it into that agent's prompt
  — you do not edit agent configs yourself.
- Anything that needs product prioritisation (missing monitoring, a test-infra
  gap, debt with a trigger that fired) goes to `pm` as a backlog item, and to
  `staff` if it is a convention question.

## Report

One screen into `.omni/sync/<date>-retro-wave-<n>.md`: the four metrics with
deltas, the patterns found, the working agreements added and retired, and the
backlog items filed. Then say the one thing you expect to be measurably different
next wave — and check that claim in the next retro. A retro that never checks its
own prediction is just paperwork.
