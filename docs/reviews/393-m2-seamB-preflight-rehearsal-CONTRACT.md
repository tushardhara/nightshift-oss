> **SUPERSEDED 2026-08-30T16:45Z.** A different, independently-authored contract for this same
> work item (`docs/reviews/393-build-CONTRACT.md`, committed directly on
> `beta/393-preflight-rehearsal` at `b7ea06a` by a concurrent Program session, "added retroactively
> at the program's request") was discovered during `crew_alpha`'s cross-crew review of PR #404 --
> both existed independently, neither aware of the other, the exact #174 collision shape. Ruling:
> **`docs/reviews/393-build-CONTRACT.md` is authoritative going forward** -- it correctly requires
> the `Security` domain (this file did not, an under-scoping given the diff touches bwrap/subprocess
> execution), and it embeds proof-backed AC verification already gathered across three review
> rounds rather than a bare pointer. This file is kept, unedited below, as history -- per the
> "never edit a frozen contract in place" convention -- not deleted. Any reviewer arriving here
> should follow the pointer above, not this file's own (now-superseded) required-domains checklist.
> Recorded in `.omni/decisions.md`.

---

# Review contract

## Identity

- Work item: Issue #393 -- M2 seam-B close-out: D11 install rehearsal + D12 preflight check manifest
- Committed contract path and blob OID: docs/reviews/393-m2-seamB-preflight-rehearsal-CONTRACT.md (this file, v1)
- Contract version: 1
- Base SHA: 465985591144a9afeb8819cecf0e13cb950e827f
- ADR: ADR-0054 (docs/decisions/ADR-0054-m2-seamb-install-rehearsal-and-preflight-manifest.md)
- Review owner (cross-crew lead, not the author): crew_alpha (round-robin: crew_beta authored, seam A reviews seam B)
- Frozen at (UTC): 2026-08-30T15:40Z

## Acceptance criteria

Full detailed criteria (D11-1..7, D12-1..9, Both-10 -- 17 total) are already committed and
frozen in `docs/prd/393-m2-seamB-preflight-rehearsal.md` section "Detailed acceptance criteria"
(lines ~153-247), authored by crew_beta's pm (PR #396) and DoR-passed there. This contract
incorporates that section by reference rather than re-transcribing it, to avoid two documents
drifting; a reviewer must read that section as this contract's AC table.

| ID | Exact criterion | Verification |
|---|---|---|
| AC-ref | Every D11-*, D12-*, and Both-10 criterion in `docs/prd/393-m2-seamB-preflight-rehearsal.md` ("Detailed acceptance criteria" section, as committed at this contract's base SHA) | Per-criterion verification as stated in that section; overall gate is `python3 tests/dod_validate.py` printing `D11 PASS` and `D12 PASS` with every other of the 22 lines byte-identical to the pre-change baseline (criterion Both-10) |
| AC-CI | The PR's `tests` GitHub Actions check is green on the exact reviewed head SHA | `gh pr checks 404` |
| AC-WA5 | Any single-author (crew-lead-self-performed) change in this PR is explicitly disclosed in the PR body, citing issue #236 as the reason no classified sub-agent dispatch was available | Read PR #404's body |

## Explicitly out of scope

- Any change to `deploy/preflight.sh` semantics beyond what D12 requires (manifesting existing checks, not adding new ones) -- flag as MERGE_BLOCKER if found.
- `docs/prd/DOD-MANIFEST.json` sections other than `preflight_checks`.
- Anything already covered and closed under a different issue number.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review
- [x] QA
- [x] Cross-crew review
- [x] Staff / ADR consistency
- [ ] Security
- [ ] UX

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 |  |  |  |  |  | open |  |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
