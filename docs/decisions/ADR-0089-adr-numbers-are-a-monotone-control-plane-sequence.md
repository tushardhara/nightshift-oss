# ADR-0089: ADR numbers come from a monotone control-plane counter; on-disk collision is detected by a separate hermetic check, never by the allocator

Date: 2026-09-03 · Status: accepted (amended 2026-09-03 pre-publication — see "identity rule") · Deciders: crew_alpha lead · Proposers: eng_claude, eng_codex
Task: `619-adr-allocator` · Issue: #619 · Base sha: `5eb1430fe6db5ae6723e3bdcdce9cd7451035703`
Discharges: ADR-0066's named follow-up ("extending `nightshift-ctl seq next` to cover an `adr`
sequence ... is the real structural fix and is out of scope for this ADR")

> **Sweep warning (filed as #656 — read before running any mechanical edit over this file).**
> This document deliberately contains live citations to **ADR-0073**, **ADR-0069**, **ADR-0018** and
> other numbers *as examples of damage*. They are not errors and must not be "corrected". A
> find-and-replace that rewrites them will destroy this ADR's description of the very defect it
> exists to record — which is #656's failure mode (a correction whose match set includes the text
> documenting the correction) reproduced on the document that explains it.

## Context

ADR numbers were allocated **from memory** — a human handover file carrying a `next ADR: NNNN` line.
Tonight that control failed in the way memory-based controls fail: silently, and with a result that
looks correct.

**The defect that forced this decision (#651).** A spec cited ADR-0073 meaning ADR-0074. Then #594
landed a real, unrelated `ADR-0073-f1-disposition-reads-integrated.md`. The stale citations did not
break — they began **resolving**, to a document that has an Option A, a rejected Option B, and
decisions 1 and 2. It **structurally corroborates while substantively contradicting**. A reader who
follows it finds a document whose shape matches the claim perfectly and whose content is about
something else. That is strictly worse than a dangling reference, which fails loudly. The mechanical
fix for those citations then rewrote its own warning label (#656).

**This is systemic, not a one-off.** Scanning `origin/main`, `.omni/adr-drafts/`, `.omni/salvage/`
and every worktree, **seven distinct numbers already carry two or more different documents**: 0001,
0022, 0040, 0046, 0055, **0069 (four documents)**, 0073.

> Correction recorded deliberately: the charter for this task, and my own first scan, said *nine*.
> Both counted pairwise conflict lines rather than distinct numbers. `eng_claude` refuted it against
> 253 worktrees and was right. The number is seven. It is recorded here because an ADR that quietly
> adopts its author's arithmetic teaches nobody.

**No single source shows the correct next number.** Verified live while writing this:

| Source | Highest ADR | 
|---|---|
| `origin/main` `docs/decisions/` | 0075 |
| the checked-out working tree at `/srv/project` | **0071** — stale by four |
| `.omni/adr-drafts/` | holds an 0073 that `main` does not |
| the human handover file | "next: 0076" |

Four sources, four answers. Per #619 the same shape produced the original incident: 0070 was absent
from `main` while held by open PR #562, so the correct value was one that **no single source showed**.
ADR-0066 had already diagnosed exactly this and imposed a wave-scoped policy — one seat (`program`)
mints, from `origin/main` plus every open PR's added ADR files — while naming the `seq next` extension
as the real fix. This ADR delivers that fix and is the condition for retiring the interim policy.

**The constraint that decides the allocation policy: the gaps are not free.** `origin/main` is
missing 0018, 0019, 0023, 0024, 0034, 0035, 0065 and 0070. `git log --all --diff-filter=A` shows
**every one has a prior add-event**. They are *retired* numbers, still cited by old reviews, specs
and ledgers — not unused ones.

## Options considered

### A — Monotone counter + separate hermetic duplicate check (proposed by `eng_claude`) — **TAKEN**

`seq next --name adr` issues strictly above the high-water mark of every number ever observed, and
never reads a disk. A *separate* check, living in the test file, hermetically asserts ADR-id
uniqueness on the merge commit. Plus three small hardening edits in `bin/nightshift-ctl`.

Its argument is the one that survived scrutiny: the design question is not "how do I stop two writers
picking the same integer" but **"which allocation policy minimises vacant-then-filled transitions?"**
A monotone counter makes exactly one number transition per allocation, at a point above everything
ever cited. That is what keeps a stale citation **failing loudly** instead of resolving falsely.

### B — Reconcile-then-seed, documentation and characterization tests only (proposed by `eng_codex`)

Identical diagnosis, identical seed value, identical rejection of disk-reading inside the allocator,
and correctly cautious about changing an administrative command on the live control plane: it wanted
`seq set` hardening separately contracted. Lost only on scope — see Decision.

### C — Allocate the lowest number absent from `docs/decisions/` (rejected early, by both proposers independently)

**Rejected because it manufactures #651 by construction.** It would issue 0018 tomorrow and convert
eight retired numbers from *permanently dangling* to *falsely resolving*, against citations already
written. Both vendors reached this independently from the add-event evidence. This is the clearest
agreement in the debate and the strongest single reason the counter must be monotone rather than
gap-filling.

### D — Have `cmd_seq_next` read `origin/main` and `gh pr list` to refuse an occupied number (rejected)

This is the literal reading of "refuse a number already occupied on disk". Rejected: it puts a
network call and a git read inside a `BEGIN IMMEDIATE` transaction on the live control plane; it is
unavailable to the depth-1 shallow CI checkout; and it still cannot see hand-typed numbers or the
253 worktrees. It buys partial protection at the price of making the allocator fallible and slow.

## Decision

**REFUSE and DETECT are both adopted, as two mechanisms separated by where the truth lives.** The
question as posed ("refuse, or detect after the fact?") contains a false choice, and the honest
answer is that a single mechanism cannot do both jobs:

1. **REFUSE — in the database, never touching a disk.** `seq next --name adr`, exactly as shipped.
   `--name` is already a free string; `adr` gets **no branch, no constant, no `choices=`** in the
   binary. Special-casing it would discard the generality that makes this an S-sized task.
2. **DETECT — in the test file, never touching the database.** A hermetic scan of the checked-out
   `docs/decisions/`, which on a `pull_request` event *is* the merge commit — the artifact that
   `git merge-tree` proved merges clean today with two `ADR-0069-*` blobs and no conflict.

**The allocator alone would not have prevented tonight's damage, and this ADR does not pretend
otherwise.** The counter can only see numbers it issued. It is blind to the 253 worktrees, to
branches cut before it existed, and to anyone who types a number by hand — and those will keep
producing PRs for weeks. DETECT is what covers that population. This is the substance of the
program's question, and the answer is: **refusal is structurally incapable of helping a citation
written yesterday, so the value of monotonicity is not collision-prevention but keeping the
already-written citation in its loud-failure mode.**

### Scope calls where the proposers disagreed

I take **A's scope over B's**, narrowly, on three points, because each closes a hole that makes the
allocator's guarantee *false* rather than merely incomplete:

- **`dump` must emit `sequences`.** `eng_claude` found `deploy/nightshift-state-backup.sh:29-30`
  documents sequences as riding the snapshot so a restored box can re-seed — and `dump` does not
  emit them. I verified it: the key is absent. **That restore path is broken today.** Once ADR
  numbering depends on this counter, a restore silently resets it, and the next bare call returns
  **1** — colliding with an ADR-0001 that already has two files. A silent reset to a
  plausible-looking number is precisely this ADR's failure mode, aimed at its own mechanism.
- **`seq set` must refuse to lower an existing counter** without `--allow-rewind`, and gains the same
  optional lease gate `seq next` already has. Verified by both QA and `eng_claude`: it is completely
  ungated, and `seq set --name adr --next 1` reissues 1. Shipping an allocator whose entire value is
  "numbers cannot be hand-picked" alongside an ungated sibling that hand-picks them is shipping a
  false guarantee. **Create-at-any-value stays legal** — only *lowering an existing row* needs the
  flag — which preserves the restore path and the escape hatch for a poisoned counter.
- **The duplicate-id check ships now, not later.** It is the only part that addresses citations
  already written.

`eng_codex`'s caution is not dismissed: this changes an administrative command on the live control
plane, so the PR goes to `staff` and `security` and says so explicitly rather than being folded in
quietly.

### The identity rule, forced by a file on main

An ADR is a file in `docs/decisions/` **whose H1 declares its number**, in one of the two separator
forms actually in use on `main` — `# ADR-NNNN: Title` or `# ADR-NNNN — Title`, where `NNNN` is
**four or more digits** on both sides exactly as in the candidate-file rule below; the number comes
from the H1 and the filename must agree. Not a filename regex. An `ADR-NNNN[-slug].md` whose H1 matches
**neither** form FAILS the check rather than being silently skipped.

**The separator set is exactly two characters: `:` (U+003A) and `—` (EM DASH, U+2014). The EN DASH
`–` (U+2013) is NOT accepted and must FAIL.** It is named here because it is the canonical
near-miss: visually almost identical to the em dash, absent from every ADR on `main` today, and
therefore exactly the shape the fail-don't-skip rule exists to catch. A checker that quietly
accepted it would be broader than its own spec — which is this ADR's failure mode pointed at its
own mechanism. `eng_claude` had defensively accepted the en dash and **reported the divergence
rather than leaving it dead**; the call recorded here is to tighten the code to the spec, not to
widen the spec to the code.

**Candidate-file rule.** A file is a candidate if it matches `^ADR-(\d{4,})(-.+)?\.md$` — the slug
is OPTIONAL and the number is **four or MORE digits**. A bare `ADR-0090.md` carrying no declaring
H1 must FAIL, not escape the scan by failing to look like a candidate. No such file exists today;
it is closed now because a hole that requires a future filename to expose it is one nobody will
remember to check.

**Why `\d{4,}` and not `\d{4}`.** Zero-padding to four digits remains the naming convention, but
the counter is monotone and **unbounded above**, so the check must be too. A `\d{4}` anchor would
make `ADR-10000-*.md` match neither pattern and therefore not be a candidate at all — **DETECT
would silently switch itself off at ADR-10000**, the round number where nobody would think to look.
An allocator whose duplicate check disables itself at a threshold, quietly, is this ADR's own
failure mode aimed at its own mechanism.

### The scan's total-classification contract

This is the rule that matters more than any individual shape, and it is what round-1 review
actually produced. **Every file under the ADR tree is classified into exactly one of
`counted` / `mismatched` / `unrecognised` / `not_an_adr` / `symlinked`. Nothing is ever silently
dropped**, and the scan fails if the buckets do not partition what it walked. `not_an_adr` is the
only quiet bucket, and it is governed by a REQUIREMENT rather than an enumeration:

> **A file may land in `not_an_adr` only if a deliberately OVER-BROAD gesture detector finds
> nothing ADR-shaped in either its name or (for Markdown) its first line. Identity stays strict;
> the gesture detector must be strictly broader than identity. When the two disagree — the file
> gestures but does not match — the scan is LOUD.**

This is stated as a requirement because three successive attempts to state it as an enumeration
were each proved false by the implementer or a reviewer within one round. The rule that survives
is the asymmetry, not the list: **near-misses must be noisy.** A non-Markdown file is never
opened, so **its first line can never make it loud — though an ADR-shaped NAME still does**
(`ADR-0090-data.bin` is `unrecognised`; `README.txt` is `not_an_adr`). That first-line exemption
is the only one, and it is safe because no `ADR-NNNN` citation can resolve to a `.txt`.

The gesture detector MUST treat all of the following as gesturing, and therefore loud when they
fail identity — every one was found by a gate hunting this bucket specifically:

- **Case variants.** `adr-0090-x.md` with `# adr-0090: title` is a near-miss, not prose.
- **A leading BOM.** `\ufeff# ADR-0093:` is a canonical declaration behind three invisible bytes.
  A BOM must never be the difference between loud and quiet.
- **Non-ASCII numerals.** Identity digits are **ASCII `[0-9]` only**, and the gesture detector
  spells its digits `\d` **deliberately**. This one asymmetry is the mechanism: Python's `\d` is
  Unicode-aware, so `ADR-٠٠٩٠` was counted as a *distinct* number from `ADR-0090` and the two
  did not register as duplicates. Non-ASCII numeral forms must be refused loudly, never counted.
  **NFKC does not fold Arabic-Indic digits to ASCII**, verified — so normalising cannot rescue this
  case and identity had to narrow. The gesture side additionally strips invisible characters
  anywhere in the string, applies NFKC and casefolds, so a BOM, a zero-width space, a fullwidth
  letter or a lowercase `a` can never be the difference between loud and quiet.

**Why this is the ADR's own failure mode, again.** Each of these is a document that structurally
corroborates — it looks like an ADR to a human reader — while substantively failing to be one to
the checker, and failing SILENTLY. A duplicate hidden behind a BOM or a lowercase `a` is exactly
the #651 shape this ADR exists to end, reproduced inside the mechanism built to end it.

Stating the contract this way is what makes a *fifth* unanticipated shape fail by construction
instead of requiring a fifth finding. Four were found by review at `ec3ce7f`, all of the same
family — a declaring H1 under a non-ADR filename, a nested directory, a five-digit id, and a
symlink — and all four had produced `mismatched: []`, `unrecognised: []`: swallowed without a
sound. The per-case fixes are not the lesson; the partition assertion is.

**Scope is recursive.** "A file in `docs/decisions/`" means the tree, not the top directory. A
nested duplicate is a duplicate.

**Hermeticity is a rule, not an adjective.** The scan **never follows symlinks — files or
directories**; they are recorded and refused. A non-Markdown file is classified without being
opened. A Markdown file that cannot be decoded fails loudly rather than being skipped. This ADR
previously called DETECT "hermetic" while the implementation followed symlinks and could read
outside the checkout entirely — **the claim was untrue when it was written**, and it is recorded
here rather than quietly corrected.

> **AMENDED 2026-09-03, second amendment, pre-publication, by the crew_alpha lead.** The four
> paragraphs above are new. Three close silences (recursion, symlinks, the classification
> contract); one closes a **live contradiction** — this ADR named the candidate regex
> character-for-character as `\d{4}` while the shipped checker uses `\d{4,}`. `eng_claude`
> reported that contradiction against its own work and explicitly refused to edit this file to
> resolve it, which is the only reason it was resolved in the right direction.
>
> **Third amendment, same session.** `eng_claude` then found this amendment's OWN description of
> `not_an_adr` to be a false universal — it claimed a bucket was unreachable in a case the code
> reaches — and again refused to conform the code to my prose. I reproduced it before amending:
> `notes.txt` beginning `# ADR-0091:` lands in `not_an_adr`, which the sentence called impossible.
> That is three corrections this document owes to an implementer contradicting its author, and it
> is recorded because an ADR about documents that corroborate structurally while contradicting
> substantively should not quietly contain one.
>
> **Fourth amendment.** Round-2 review found three more near-miss shapes landing quietly (BOM under
> a non-ADR filename, case variants, non-ASCII numerals). The reachability clause above has been
> replaced by a REQUIREMENT on the gesture detector, because stating it as an enumeration failed
> three times running. The lead's enumerations were the defect; the asymmetry is the rule.

> **AMENDED 2026-09-03, pre-publication, by the crew_alpha lead.** As first written this rule said
> `^# ADR-(\d{4}):` — colon only. That was **wrong**, and wrong in the direction that matters.
> `eng_claude` contradicted it during implementation instead of coding around it, and I verified the
> claim myself against `origin/main`: **eight genuine ADRs use the em-dash form** — 0013, 0014, 0015,
> 0016, 0017, 0020, 0021, 0064. A colon-only rule drops all eight out of the uniqueness scan — a hole
> in DETECT *precisely where a duplicate could hide* — or forces all eight onto the exemption list,
> which trips this ADR's own reversal trigger 3 on day one. The intent (identity declared by the H1,
> never inferred from the filename) is unchanged and still load-bearing; only the separator set is
> corrected. Pinned by `test_both_h1_separator_forms_are_recognised`. Recorded here rather than
> quietly fixed, because an ADR whose own checker would have skipped eight documents is exactly the
> "structurally corroborates while substantively contradicting" failure this ADR exists to name.

`eng_claude` found and I verified that `origin/main` already carries `ADR-0001-0003-staff-review.md`, whose H1 is
`# Staff review: ADR-0001, ADR-0002, ADR-0003` — **not an ADR**. Under a naive `ADR-(\d{4})` filename
rule it reads as a duplicate of `ADR-0001-stack-and-shape.md`, and the check would either fire
falsely on day one or need an exemption list that would eventually hide a real duplicate. A companion
assertion pins the trap: any `ADR-NNNN[-slug].md` without a matching H1 must appear on a short named
exemption list, which today has exactly one entry.

### Seeding: value, mechanism, and timing

**Seed value 76**, from `max(every number observed in any tree, in history, or in any open PR) + 1`.
Verified across four independent legs — `origin/main` tree: 0075; all-history add-events (catches
retired numbers a tree read misses): 0075; every open PR's added ADR files: 0070 (#562), below the
mark; drafts/salvage/worktrees: 0073. High-water **0075**. Both proposers reached 76 independently by
different derivations.

Note the handover file's "0076" was **right by luck**: its stated premise ("main ≤0072; open PRs hold
0073/0074/0075") had gone stale because all three merged. Finding the right answer from a false
premise is the failure this ADR exists to end.

**Seed with `seq next --name adr --start 76`, not `seq set`.** `--start` is honoured only on first
insert (`ON CONFLICT DO NOTHING`), so one call atomically creates the row at 76, returns 76, and
leaves `next=77` — with no window in which the row exists but is unallocated, and without using the
command this ADR is about to gate.

**Seeding happened before the PR, deliberately, and is already done.** The mechanism is live at base
and the row did not exist, so the first bare `seq next --name adr` would have created it at **1**,
after which `--start` is silently ignored forever and recovery requires the very ungated `seq set`
this ADR gates. That window was open while this ADR was being written. It is now closed:

```
$ nightshift-ctl seq next --name adr --start 76
{"name": "adr", "ok": true, "value": 76}      # sequences: adr -> next 77
```

**This ADR's own number is that allocation.** ADR-0089 is the allocator's first customer, minted by
the mechanism it describes.

## Consequences

**Makes easy.** Any seat can mint a number without asking `program` and without reading a tree that
may be stale by four. The counter is generic, so a future `--name` costs nothing. Duplicate ADR ids
become a red test rather than a human-shaped reading during an unrelated review gate — which is how
#594 was caught, by luck.

**Makes hard / costs.** The counter is authoritative only if it is never rewound and never lost;
that is why `dump` and the rewind gate are in scope rather than deferred. Numbers will be *consumed
and abandoned* when a draft ADR dies, so the sequence will grow gaps — **that is intended**, and
those gaps must never be reclaimed (option C).

**Forecloses.** Gap reuse, permanently. And it does **not** repair anything already written: #651,
the seven duplicated numbers, and 0069's four documents have no repair route from here, because
`program/config.yaml:414` forbids editing an accepted ADR. **This task makes #651 the last of its
kind; it fixes none of its predecessors.** Anyone reading this as retroactive relief is misreading it.

**Not solved, deliberately** — stale-citation *semantics*. A check that every `ADR-NNNN` citation in
prose still means what its author meant is a larger task over a different file tree. Uniqueness of
ids is not correctness of citations, and this ADR only buys the former.

## Reversal trigger

Revisit if any of these becomes observable:

1. **A duplicate ADR id reaches `main` while the DETECT check is green** — the identity rule is wrong,
   most likely via the exemption list hiding a real duplicate.
2. **The counter is rewound or lost in a restore** despite the `dump` fix and the rewind gate — the
   database is then the wrong home for this and the number must be derived from git.
3. **The exemption list grows past ~3 entries** — `ADR-NNNN[-slug].md` is being used for non-ADRs often
   enough that the filename convention, not the checker, is the defect.
4. **Numbers are still being hand-picked a wave after adoption** — the allocator is not the binding
   constraint and the enforcement (a PR check that the number was minted) is what is actually needed.

ADR-0066's interim "program mints" policy is the belt while the counter proves itself. Retire it only
after the DETECT check has been green on `main` for a full wave.
