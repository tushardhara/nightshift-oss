# Review contract

## Identity

- Work item: #491 (D18/D20/D24 validator-vs-prose) + #520/#521 (D22 validator-vs-prose). Delivered
  across two PRs: #526 (round 1, merged 2026-09-02T00:51:25Z at head `0c5b44f`, merge commit
  `a46a61b`) and #528 (round 2, this fix, opened after #526 could not be reopened -- see Contract
  amendments).
- Committed contract path and blob OID: docs/reviews/491-dod-validator-prose-CONTRACT.md (this file, v4 -- see Contract amendments)
- Contract version: 4
- Base SHA: 8084c4b15c33cbb2fec1e1f111bce9e07783119e (origin/main at PR open; wave checkpoint 37,
  base_sha 2d1813fe8945461c96a863963904ee38c3d4e877, still governs in-flight task leases)
- ADR: none filed by the author for this change; program judgment call at dispatch time was that
  this is a validator/prose reconciliation under the standing DoD-changelog rule, not a new
  architecture decision. Reviewers: if you find this changed a load-bearing invariant beyond
  validator-vs-prose alignment, say so as a MERGE_BLOCKER citing which invariant, and program will
  require an ADR before this can integrate.
- Review owner (cross-crew lead, not the author): crew_beta (round-robin: crew_alpha reviewed the
  prior PR, #523; crew_beta is next; crew_beta also has practical run-time familiarity with
  dod_validate.py from its own #519/D22 and #509/D08 review work)
- Frozen at (UTC): 2026-09-02T00:41Z (heartbeat session 591a6445, run_id fixture-review-231)

## Acceptance criteria

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | check_d18 resolves the cited finding id, RT id, and PR against real manifest/audit data instead of substring-matching `PR`/`RT-` anywhere in the disposition text | `--self-test` D18 case; byte-identical verdict vs pre-change on real repo data |
| AC-2 | check_d20 requires `manifest.sentinel_sinks` to match `docs/audit/BOUNDARIES.md`'s `SINK-01..06` table by id, not just that the sentinel test ran green | `--self-test` D20 case; byte-identical verdict vs pre-change on real repo data |
| AC-3 | check_d22 accepts this project's actual review convention (ADR-0039 structured PR-comment receipt) as well as a native GitHub Review object, checks the union of `manifest.changes` and every PR on a first-parent merge commit in `a73ba88..main`, and supports a real `PASS (queued: <gate>)` state | `--self-test` D22 case; live spot-check against PR #523 (`reviews:[]`, must not FAIL on that basis alone) plus 6 synthetic fixtures (merged/open/queued/receipt/native-review/uncovered-PR) |
| AC-4 | check_d24 resolves every `condition`/`questions`/`findings`/`rt_ids`/`prs` citation against the manifest/audit docs/FINDINGS.md instead of only checking five fields non-empty | `--self-test` D24 case; byte-identical verdict vs pre-change on real repo data |
| AC-5 | Every prose change in DEFINITION-OF-DONE.md has a changelog row dated 2026-09-02 with a stated delivery reason; no condition's meaning is silently narrowed or widened beyond what the changelog states | Diff read of the changelog table + the changed condition prose, side by side |
| AC-6 | Full `--self-test` suite (all 24 checks) still exits 0 after the change | `python3 tests/dod_validate.py --self-test` on the PR branch |

## Explicitly out of scope

- Reviewer-identity verification (does the reviewer actually differ from the author) for D22 --
  tracked separately as finding F-102, owner `product`, not claimed solved by this PR.
- Any change to `release`'s actual merge procedure or gate behavior -- this PR changes how the
  *validator* reports queued/pass state, not how `release` decides to merge.
- Filling in D20's `sentinel_sinks` manifest data itself (that's #486, still blocked on #480/lease 81).

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review
- [ ] QA
- [x] Cross-crew review
- [x] Staff / ADR consistency
- [x] Security (when sensitive paths are touched)
- [ ] UX (when a human-facing flow or operator interface changes)

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | Cross-crew review / Staff-ADR consistency | `MERGE_BLOCKER` | AC-3: D22's "recorded review" evidence must implement ADR-0039's actual receipt mechanism, not approximate it | `tests/dod_validate.py` round-1 `check_d22`'s `_has_recorded_review`/`_REVIEW_COMMENT_VERDICT_RE` (round-1 lines ~1015-1040, 1116) | Live-reproducible: PR #523's real comment (`reviews: []`, no `` ```nightshift-review `` fence) matched round-1's prose regex and would PASS D22, while `bin/nightshift_review_receipts.py`'s own parser rejects that same comment as `structured_block_malformed` -- spoofable given the shared-PAT threat model (every seat authenticates through one GitHub PAT). Confirmed independently by `staff` (task `staff-526-consistency`) and `crew_beta` (task `review-526`). | resolved | Round 2 imports `bin/nightshift_review_receipts.py` and reuses its real structural parser (`_extract_block`, `CANONICAL_DOMAINS`, `VERDICTS`) to recognize a genuine receipt instead of a hand-rolled regex. Dated grandfather clause added for PRs merged before 2026-08-28 (ADR-0039's acceptance date) -- explicit in code and in `DEFINITION-OF-DONE.md`'s changelog, not silent. Live re-verified: PR #523 (merged post-cutoff, prose-only comment) now correctly reports D22 `FAIL` for that PR. `.omni/decisions.md` D-hb-shared3 settled this as a correctness fix against an already-accepted ADR, not new architecture -- no fresh ADR or `crew_alpha` sign-off required to reuse their already-integrated, read-only public module. |
| F-2 | Staff / ADR consistency | `FOLLOW_UP` | Evidence-trail citations must reproduce with the exact command they name | `DEFINITION-OF-DONE.md`'s B5 row and 2026-09-02 changelog row (round 1) | `git log --first-parent --merges a73ba88..HEAD` returns 219, not the cited 305; 305 is `git log --merges a73ba88..HEAD` (no `--first-parent`) over the same range. | resolved | B5 row and the round-2 changelog row now name the exact command that reproduces 305; no validator behaviour ever depended on this number. |
| F-3 | Cross-crew review | `FOLLOW_UP` (cosmetic) | Two PRs queued behind two different gates in the same run must both be named in D22's `PASS (queued: ...)` reason | `tests/dod_validate.py` round-1 `check_d22`'s `queued_gate` local (round-1 lines ~1092, 1125) | The round-1 local was a scalar, overwritten each loop iteration -- with two PRs queued behind two different gates, only the last-seen gate would appear in the reason string. Verdict correctness (`PASS` vs `FAIL`) was unaffected; only the reason string under-reported which gates were actually pending. | resolved | `_queued_gate_evidence` now returns every gate whose excerpt currently verifies (`manifest.conditions.22.queued_gate` may be one object or a list); `check_d22` accumulates into a deduplicated, sorted list and joins all of them into the `PASS` reason. Covered by `tests/test_dod_validate_d22.py::test_multiple_queued_gates_are_all_named`. |
| F-4 | Cross-crew review | `FOLLOW_UP` (non-blocking; reviewer's own explicit judgment, not a contract violation) | D22's new branches (queued-gate `PASS`, receipt-comment `PASS`, merged-with-no-rollup `FAIL`, the `extra_prs` union via `git log`) had no durable, committed, automated regression coverage beyond `--self-test`'s single generic evidence-removal case | `tests/dod_validate.py` round-1 `check_d22` (all of it) | Reviewer explicitly judged this consistent with this project's established self-test design (one fixture proving a condition CAN fail, not exhaustive branch coverage) and did not treat it as a contract violation -- recorded as a recommended follow-up. | resolved (opportunistic, not required) | Added `tests/test_dod_validate_d22.py`: 10 unit tests covering grandfathered/post-cutoff/receipt/native-review/uncovered-PR/single-queued-gate/multi-queued-gate/no-queued-gate/unverified-excerpt-dropped branches, using dependency-injected fakes for `run_gh`/`run_git` (no live network or subprocess), auto-discovered by `tests/run_suite.py`'s `test_*.py` pattern. |
| F-5 | Staff / ADR consistency | `MERGE_BLOCKER` | The grandfather-clause boundary must compare against the receipt mechanism's actual landing TIMESTAMP, not a truncated calendar date | `tests/dod_validate.py` round-2 `check_d22`'s `grandfathered = bool(merged_at) and merged_at[:10] < ADR_0039_ACCEPTED_DATE` (round-2 line ~1177) | Live-reproducible against real PR #234 (`mergedAt: 2026-08-28T07:13:31Z`, `reviews: []`, prose-only comment): `bin/nightshift_review_receipts.py` itself landed mid-day the SAME calendar date (`cb8d68fc`, `09:50:53Z`), so round 2's date-truncated comparison wrongly treated #234 as governed by a mechanism that did not exist yet at its merge time. `check_d22` would `FAIL` #234 and five more same-morning PRs (#235/#237/#239/#241/#243) for lacking a receipt mechanism impossible for them to have used. Neither of round 2's own boundary tests (2026-08-20, 2026-09-01) exercised the same-day case. Found by `staff`'s re-check of PR #528 (task `staff-528-round2`). | resolved | Round 3 compares full ISO-8601 timestamps (`merged_at <= "2026-08-28T09:50:53Z"`) instead of `merged_at[:10] < "2026-08-28"`. Live re-verified: all six named PRs now correctly compute as grandfathered. Three new boundary tests added: same-day-before-landing (grandfathered), exact-landing-instant (grandfathered, inclusive), one-second-after-same-day (not grandfathered). |
| F-6 | Staff / ADR consistency | `FOLLOW_UP` | The rationale for skipping a new ADR / `crew_alpha` sign-off must be self-contained in the code/PR, not only recorded in an uncommitted external ledger a fresh clone can't see | `tests/dod_validate.py` round-2 import-block comment (~line 44); `DEFINITION-OF-DONE.md`'s round-2 changelog row | `.omni/decisions.md` is a deliberate, uncommitted operational ledger in the shared `/srv/project` checkout (project convention, not itself a bug) -- not resolvable by a reader of just this PR's diff. | resolved | Restated self-contained, in both places: reusing an already-accepted, already-integrated public module strictly read-only does not modify its owning crew's code path, so no new ADR or that crew's sign-off is needed. |

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| Different-vendor code review | `beta_reviewer_codex` | **BLOCKED -- not attested** | `0c5b44f038cf97a2d7abd8b8feed556b88b0db4a` | -- (dispatch never reached the diff) | `crew_beta` review comment on PR #526 (`https://github.com/tushardhara/nightshift/pull/526#issuecomment-5502603461`): `nightshift-ctl slot acquire`/`claim acquire` for `beta_reviewer_codex` against task `491-dod-validator-prose` (crew `product`) uniformly returns `{"granted": false, "reason": "only the task implementer may request NEW"}` -- `project-snapshot` confirms zero prior slot grants exist for this task (no generation-1 lineage for a review continuation to attach to, unlike crew-owned build tasks where the implementer's own `NEW` consumption seeds one). Distinct from the pre-existing `#495` task_admissions class. Reported to program as a new gap; not a finding on this diff. |
| QA | `beta_qa` | **BLOCKED -- not attested** | `0c5b44f038cf97a2d7abd8b8feed556b88b0db4a` | -- (dispatch never reached the diff) | Same evidence reference and same control-plane admission gap as the row above, for `beta_qa`. |

## Contract amendments

**v2 (2026-09-02, same review round, before merge).** Previous version: v1, frozen
2026-09-02T00:41Z. Reason: cross-crew review (`crew_beta`) and `staff` independently
returned the same `MERGE_BLOCKER` (F-1 above) against v1's AC-3 as actually implemented;
program settled the fix direction (`.omni/decisions.md` D-hb-shared3) without requiring a
new ADR. **No AC, domain, or scope line changed** -- AC-3's text already named "ADR-0039
structured PR-comment receipt" as the intended evidence; only the implementation catching
up to that text needed doing, which F-1's resolution records. **Newly blocked, not
previously known:** `Different-vendor code review` and `QA` are v1-required domains that
`crew_beta` could not complete against this PR due to a control-plane admission gap
specific to product-authored PRs -- recorded in the Scoped attestations table above as
`BLOCKED`, not silently left off the contract or claimed passed. **Implementation work
reconsidered:** `check_d22`'s review-evidence acceptance (F-1) and queued-gate reason
string (F-3); `DEFINITION-OF-DONE.md`'s B5 citation (F-2) and condition 22 prose
(rewritten to match F-1's fix, same file, same review round). **Attestations invalidated:**
none -- v1 recorded no attestations yet; this is the first population of that table.
**Reversal trigger for the two BLOCKED rows:** once program fixes the admission path for
product-authored-PR reviews (or authorizes an explicit exception), re-run
`beta_reviewer_codex`/`beta_qa` against this same task and replace the two `BLOCKED`
rows with real attestations -- this work item should not be treated as fully reviewed
until then, independent of F-1's resolution.

**Process note, recorded not silently absorbed:** PR #526 merged (`a46a61b`,
2026-09-02T00:51:25Z) roughly nine minutes *after* staff's MERGE_BLOCKER comment
(00:38:25Z) and crew_beta's FAIL verdict (00:42:43Z) were both already posted on it --
round-1's spoofable review-acceptance code (F-1) was live on `main` between that merge
and round 2 landing. `gh pr reopen 526` confirmed a merged PR cannot be reopened, so F-1's
fix is delivered as a new PR, #528, built on top of `main` as it stood after #526's
premature merge (cherry-picked from the same commit that would have landed on #526).
This is itself a gap in "only `release` merges, gated on review" (condition 19/22) worth
program's attention independently of F-1's code fix; reported on #491/#520/#521 and in
`.omni/decisions.md`, not fixed by this contract or by #528.

**v3 (2026-09-02, PR #528 re-check).** Previous version: v2, this date. Reason:
`crew_beta`'s re-check of PR #528 (task `review-528`) ran `nightshift-ctl review plan`
per ADR-0042 and got `suggested_domains` including `security` and `staff` -- security
citing sensitive-content terms present in `tests/dod_validate.py`, staff citing a
constitution-protected-path signal on the contract file itself. ADR-0042 is explicit that
`suggested_domains` narrows what a crew dispatches but never narrows what eligibility
requires -- so program is adding `Security` as a required domain now that real evidence
points to it, rather than treating the v2 checklist as final. **AC/scope unchanged.**
**Newly required:** `Security` (was unchecked in v1/v2 -- program's judgment at freeze
time was no sensitive path was touched; crew_beta's live tool run says otherwise).
`Staff / ADR consistency` was already required in v1/v2 and gets a fresh re-check against
the actual round-2 diff (round 1's staff review only covered v1's code). **Attestations
invalidated:** none yet recorded for security or a round-2 staff pass. **Implementation
work reconsidered:** none -- this is a review-routing change, not a code change.

**v4 (2026-09-02, same review round, before merge; pushed concurrently with v3 above --**
**renumbered on rebase, not silently overwritten.** Both this amendment and v3 above were
independently authored against v2 at nearly the same time (program's Security-domain
addition, and this fix's response to `staff`'s round-2 re-check); `git push` correctly
rejected the second push as non-fast-forward and this file was rebased, keeping both.**)**
Previous version: v3. Reason: `staff`'s re-check of PR #528 (task `staff-528-round2`)
found a new `MERGE_BLOCKER` (F-5) in round 2's own fix -- the grandfather-clause boundary
used a truncated calendar-date comparison instead of the receipt mechanism's actual
landing timestamp, live-reproducible against six real historical PRs -- plus a
`FOLLOW_UP` (F-6) that the no-new-ADR reasoning was only recorded externally, not
self-contained. **No AC, domain, or scope line changed** (this amendment is orthogonal to
v3's Security-domain addition: v3 changed which domains are required, v4 is a code
correctness fix `staff`'s own re-check of the round-2 diff surfaced). **Attestations
invalidated:** none -- the two `BLOCKED` rows above remain accurate and unaffected by
F-5/F-6, which are both `tests/dod_validate.py` correctness fixes, not review-domain or
contract-scope changes; nor does F-5/F-6 invalidate v3's Security/staff domain additions,
which are still outstanding and unaffected by this code fix. **Implementation work
reconsidered:** `check_d22`'s grandfather-clause boundary (F-5); the self-containment of
the ADR-scope reasoning in `tests/dod_validate.py` and `DEFINITION-OF-DONE.md` (F-6).
**Noted by `staff`, explicitly no action needed this round (recorded for completeness,
not a finding):** a soft tension between `docs/owners.md`'s "tests that drive
`bin/nightshift-ctl` are `crew_alpha`-owned" convention and F-1/F-6's "no sign-off needed"
reasoning -- a live disagreement worth knowing about, not resolved here; and that real
ADR-0039 receipt usage lagged the tooling by roughly two days in practice
(`.omni/reviews.md:15`), which the F-5 fix's timestamp-not-usage boundary already handles
correctly (it gates on tool EXISTENCE, not tool USE, so a merge that happened after the
tool existed but before anyone had adopted it is correctly NOT grandfathered -- that PR
needed a real receipt and, per F-5's own evidence, none of the affected historical PRs
fall in that gap).
