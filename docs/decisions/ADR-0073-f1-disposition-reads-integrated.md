# ADR-0073: F-1's disposition states the integrated truth; the WA-008 rationale survives, narrowed to F-101/F-102

Date: 2026-09-02 · Status: accepted · Deciders: crew_alpha lead · Proposers: eng_codex (sole proposal — see "Process deviation")
Conversation: crew_alpha / 569-f1-disposition · Issue: #569 · Spec: `pm` backlog item, this conversation

## Context

DoD condition 18 (`tests/dod_validate.py::check_d18`) requires every `CONFIRMED-BUG`
record in `docs/audit/FINDINGS.md` to carry a disposition naming both an integrated PR
and an RT id that resolves to a real `rt_ids` entry with a valid baseline.

`docs/audit/FINDINGS.md` was written (issue #482) at a moment when finding **F-1** — the
`worker_slots`/claims/leases identity gap from `docs/audit/CONCURRENCY.md`, issue #477 —
genuinely had no integrated fix. The document said so, at length and on purpose, in five
separate places, and explicitly declined to fabricate a closing citation. That was the
right call then.

It is now stale. Two facts forced this decision, both verified rather than assumed:

- **PR #530 merged** 2026-09-02T04:08:45Z (merge commit `6075e6f`, branch
  `crew_alpha/508-claimant-identity`), integrating the claimant-identity fix.
- **PR #502 merged** 2026-09-01T19:46:08Z (`d59ef1f` and `96ce65a` are both ancestors of
  HEAD), so the header's description of `CONCURRENCY.md` as "in review … not yet merged"
  is stale by the same mechanism.

Two further facts reshaped the item away from what issue #569 assumed:

- **The gate is already green for F-1.** F-1's existing disposition already contains the
  strings `PR #530` and `RT-32`, so `check_d18()` at HEAD already returns FAIL naming
  **F-101**, not F-1. Issue #569's AC-2 and AC-3 were satisfied before a byte changed.
  This change therefore moves no gate; its entire value is truthfulness.
- **The document is already self-contradicting at HEAD.** Its own "Per-record schema
  validation" fence quotes `F-1 CONFIRMED-BUG FAIL …` as that snippet's output; re-running
  the snippet actually prints `F-1 CONFIRMED-BUG PASS`. Found by `pm` while verifying, not
  by the issue.

### Which RT id evidences PR #530 — the question the issue told us not to guess

**RT-32.** Not RT-34. Chain of evidence:

| Check | RT-32 | RT-34 |
|---|---|---|
| Manifest note | verbatim *"Closes docs/audit/FINDINGS.md F-1 (issue #503) — reproduces docs/audit/CONCURRENCY.md Q1/Q3's two-simultaneous-ALLOW scenario end-to-end and asserts it is now refused"* | *"Covers condition 7's 'sequential re-execution without a recorded recovery generation is refused' property, issue #508"* |
| DoD condition | 18 | 7 |
| Bound symbols | `cmd_slot_release`, `cmd_lease_release` — exactly F-1's sites | `cmd_claim_acquire` — a different site |
| `validate_rt_baseline` | returns `None` (clean) | returns `None` (clean) |

Both RT ids ride in PR #530 and **both baselines validate cleanly**, so "it validates" does
not discriminate between them — the binding note and the bound symbols do. The first commit
to introduce the string `RT-32` into `docs/prd/DOD-MANIFEST.json` is `2374c39`
*"fix(ctl): require/verify caller identity on claim/slot/lease release+renew"* — the
claimant-identity fix itself. Baseline `docs/audit/baseline/RT-32.txt`, captured at
`2d1813f`, shows exit 1 pre-fix (`assertEqual(code, 3)` got `0`: the unauthenticated release
succeeded), i.e. the test was shown failing before the change.

Issue #569 pointed at RT-34 via #531 and asked us to confirm the binding. Confirmed as
**wrong for F-1**; RT-34 evidences a real property, just not this one.

## Options considered

### A — Retitle and narrow the "Why … stay open" section (proposed by eng_codex) — **taken**
Retitle `## Why F-1/F-101/F-102 stay open rather than being force-closed` to name only
F-101/F-102, and rewrite its paragraph for those two. The section carries the document's
WA-008 rationale — *a citation that cannot support its claim is worse than none* — which is
durable audit guidance that outlives F-1's membership in the group.

### B — Delete the section, fold its point into the intro (rejected)
Rejected for the reason eng_codex gave and I agree with: the WA-008 rationale is the
document's spine, not an explanation of F-1. Deleting it to save a paragraph would remove
the reasoning that stops the *next* author fabricating a citation — precisely the failure
this ADR exists to avoid repeating.

### C — Fix only F-1's JSON `disposition`, leave all narrative prose (rejected)
The literal minimum reading of issue #569's AC-4 ("no other finding record edited").
Rejected on `pm`'s scope ruling: AC-4 names the *JSON finding objects* as the protected
boundary, while the issue's Scope section says "`docs/audit/FINDINGS.md` only" — prose is in
scope. Decisively, fixing the machine-checked string while leaving the human-readable
demonstration two paragraphs below asserting the opposite would make the file *more*
misleading, and the contradiction would be newly self-inflicted rather than merely stale.

### D — Build a general "executable documentation fence" checker now (rejected → FOLLOW_UP)
QA's proposal for catching this rot class permanently: label fences `verify:d18` etc. and
byte-compare captured stdout. Rejected for *this* PR by QA's own reasoning — it needs test
tooling outside the one-file scope, an ownership review, and a convention; one occurrence
does not justify it. Filed as FOLLOW_UP, not carried on this PR (ADR-0057: the
implementer-facing packet is MERGE_BLOCKER-only).

## Decision

1. F-1's `disposition` states the fix is **integrated**, cites **PR #530** (with merge
   commit `6075e6f`) and **RT-32**, keeps `verdict: CONFIRMED-BUG` (the historical verdict
   is a fact about what was found, not about whether it is fixed — and `check_d18` requires
   the PR+RT pair precisely *because* the verdict stays `CONFIRMED-BUG`), and states plainly
   what RT-32 does **not** prove. The current text's claim that RT-32 covers "all four call
   sites" is an overclaim and goes; RT-32 binds two.
2. The `## Why …` section is **retitled and narrowed to F-101/F-102**, rationale preserved.
3. Both self-referential fences are **regenerated from actual output after the final edit**,
   never inferred. The `(expected for F-1/F-101/F-102: no integrated fix yet)` suffix is
   **hardcoded in the document's own inline snippet** — the fenced block introduced by the
   "Per-record citation-presence check" paragraph. Cited by anchor, not by line number,
   because the very edit this ADR authorises moves that snippet: it was at line 151 when
   this ADR was drafted and sits at line 171 in the delivered file, so an absolute line
   number here is guaranteed to be stale by the time anyone acts on it —
   so the implementer must edit that snippet, and no file outside the scope changes.
4. **Added beyond `pm`'s enumerated criteria** (criterion 11, flagged to `pm` for sign-off
   rather than assumed): the header's "in review … pull request 502, not yet merged" is
   corrected to "merged". Same defect class, same file, independently verified. `pm` may
   reject it; I did not fold it in silently.
5. The "Per-record schema validation" heading is corrected — that snippet is a
   regex/citation-*presence* check, not schema validation, and `check_d18` now resolves far
   more (source-doc ids, RT baselines, manifest-tracked PRs). Overstating it is the same
   citation-integrity defect in miniature.
6. F-101/F-102 JSON objects stay **byte-identical**; `docs/prd/DOD-MANIFEST.json` is not
   touched (product-single-writer, under lease 110).

## Consequences

**Easy:** the file stops lying about F-1; D18's remaining failure (F-101) is the honest
one, traceable to open issues #557 and #561.
**Hard:** the two self-referential fences must be regenerated on every future edit to this
file — a manual invariant with no mechanical guard until FOLLOW_UP D lands. This ADR
knowingly leaves that trap armed and documents it rather than pretending otherwise.
**Forecloses:** nothing. D18 still cannot pass until #557 and #561 land integrated fixes.

**Process deviation (stated so a human can overrule it):** only ONE proposal was obtained,
from `eng_codex`, not the usual two. This is the `design-debate` skill's sanctioned
compressed path for a trivial item (one file, prose-only, size S, moves no gate). The
single proposal was cross-checked against two other independent voices — `pm`'s scope
ruling and `qa`'s verification plan — and its two load-bearing factual claims (PR #502's
merge status; the suffix being hardcoded in the document's own inline snippet — at line 151
of the pre-edit file, line 171 as delivered) were re-verified by the lead directly
rather than accepted as prose. The implementer is `eng_claude`, a different vendor from the
proposer and from `qa`; review goes to `reviewer_codex`.

## Reversal trigger

Revisit if any of: a second executable-output fence rots undetected anywhere in
`docs/audit/**` (then FOLLOW_UP D stops being optional and gets built); or `check_d18`
gains real resolution of narrative prose, making hand-maintained fences redundant; or a
future ADR supersedes RT-32's binding to F-1 in `docs/prd/DOD-MANIFEST.json`, in which case
this disposition's citation must move with it.
