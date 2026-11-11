# Review contract — #424 close-disposition gateway

Frozen before the first implementation commit reached `origin`. This file is the
authority for the review of PR `crew_alpha/424-adr`; the PR body may mirror it but
never overrides it. Do not edit these rows in place — amend by new version below.

## Identity

- **Work item:** issue #424 — "Backlog integrity: a ticket closes only as delivered, superseded, or deferred"
- **Committed contract path and blob OID:** `docs/reviews/424-close-gateway-CONTRACT.md` (OID recorded in the PR body at freeze commit; this file's own blob)
- **Contract version:** v1
- **Base SHA:** `53905e39544bbb00552719d1d41569f65bb0952c` (checkpoint 29, wave `424-build`)
- **ADR:** ADR-0055 — "A ticket closes only through a gateway that records its disposition first" (`docs/decisions/ADR-0055-close-gateway-disposition-record.md`), staff-reviewed; the one NEEDS-A-WIDER-DECISION point was settled by `program` and disclosed in §Consequences
- **Review owner (cross-crew lead, not the author):** to be assigned by `program` — crew_alpha authored this, so crew_alpha does not own its review
- **Frozen at (UTC):** 2026-08-31
- **Migration sequence:** 8, allocated by `nightshift-ctl seq next --name migrations` under serialize lease 26 on `bin/nightshift-ctl` (holder `crew_alpha`)

## Acceptance criteria

Identifiers are #424's own numbering. Every verification below names a test in
`tests/test_issue_close_gateway.py` (46 tests) unless stated otherwise.

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | A closing comment is required on every close and states exactly one disposition: `delivered`, `superseded-by #N`, `deferred-to <milestone>`; machine-parseable from the comment body | `DispositionGrammar` (6): empty→missing, prose→unparseable, two→ambiguous, case-sensitivity, `delivered` accepted+recorded, and the CLI composing comment and row from ONE value so they cannot disagree |
| AC-2 | `superseded-by #N` requires issue N to exist and be OPEN at close time; closed or absent is rejected | `SupersededByMustExistAndBeOpen` (5): target missing, target closed, supersede-self — each rejected; positive open target accepted; plus `test_the_open_check_is_live_not_cached` (same target accepted then refused with only GitHub changed) |
| AC-3 | `deferred-to <milestone>` requires an existing named milestone; a deferred issue is not counted as closed by Nightshift-side reporting | `DeferredToMilestone` (3): unknown milestone rejected and names the ones that exist; unreadable registry rejected (fail-closed); known `## M<N>` header in `docs/prd/MILESTONES.md` accepted |
| AC-4 | `issue closable` excludes any issue whose only qualifying disposition would be `deferred-to`, so deferred work stays in the queue scan | `DeferredWorkStaysInTheQueue` (3): the integrated→deferred transition in one test (the transition IS the criterion), `issue status` explains why it is not closable, and a `delivered` close does NOT trigger the exclusion (discriminator) |
| AC-5 | Closing a `P0`- or `security`-labelled issue requires a comment naming evidence the defect no longer reproduces — commit SHA, PR number, or test id; otherwise rejected | `SensitiveIssuesRequireEvidence` (7): P0 with no evidence rejected, security with no evidence rejected, prose-masquerading-as-evidence rejected; positives for 40-hex OID, `#<pr>`, `test:<id>`; label match case-insensitive (fail-closed); plain issue needs none (the rule fires only on the sensitive set) |
| AC-6 | A bulk close is refused: >3 issues by the same actor in any 60s window, with the limit and window named in the refusal | `BulkCloseIsRefused` (5): 4th in window rejected, 3 accepted (positive), limit is per-actor, window slides so a paced actor is never blocked, and the count is over INTENTS so a retry loop cannot bypass it |
| AC-7 | A command reports, for a time range, the count of closes by disposition | `DispositionReport` (4): counts by disposition, the range actually filters, a failed close is never counted as a delivery, empty range prints explicit zeros |
| AC-8 | A test drives each rejection path in AC-2, AC-5, AC-6 and asserts the refusal, with a matching positive case accepted | Satisfied by the AC-2/AC-5/AC-6 rows above — every rejection path has a named paired positive. Seams: `NIGHTSHIFT_CTL_GH` fake-gh binary (as `tests/test_pr_head.py`, `tests/test_project_bind.py`) and `Ctl(clock=…)` virtual clock (`tests/ctlutil.py`), both pre-existing and staff-confirmed |
| FS-1 | GitHub API unreachable at close time: the close is refused, not attempted-and-assumed | `GitHubUnreachable` (3): unreachable API refuses and writes NO disposition row; missing `gh` binary fails structurally not as a traceback; a write failure leaves a VISIBLE stale intent, not a silent gap |
| FS-2 | No issues closed in the queried range: zeros for all three dispositions, not an empty body | `DispositionReport::test_empty_range_prints_explicit_zeros_not_an_empty_body` |
| FS-3 | A close made directly in the GitHub UI is flagged by the next scan as undisposed and listed | `UndisposedScan` (2): a close outside the gateway is listed; a close through the gateway is NOT listed (the discriminator, without which the scan could be flagging everything) |

Schema-level invariants (CHECK constraints, not CLI paths) are covered separately by
`SchemaConstraints` (4) and `StateReasonHonesty` (2): unknown disposition rejected, a
`superseded` row must name its successor, a `deferred` row cannot carry a `superseded_by`,
a sensitive row cannot exist without evidence, and `DUPLICATE` is refused rather than
silently downgraded to something GitHub will accept.

## Explicitly out of scope

- Reopening or re-triaging the 79 already-closed `NOT_PLANNED` issues (#424 "Not included").
- Changing GitHub's fixed `stateReason` values.
- **Rewiring `program/config.yaml`'s heartbeat close step** to call this gateway. `program/**`
  has no crew owner; `program` settled this as its own post-merge follow-up (`.omni/decisions.md`,
  "2026-08-31 … #424 ADR-0055 staff verdict settled").
- **Updating `pr_close_keyword_guard`'s denial text** to name the gateway. This path (`deploy/policies/**`)
  IS crew_alpha's, so this is a deliberate sequencing choice, not an ownership limit: the prose must
  not point at a command absent from `main`. Deferred to the same follow-up.
- AC-6 owner override flag (#424 open question 1 — explicitly non-blocking, "build the refusal first").

Both deferred items are disclosed by name in ADR-0055 §Consequences and tracked as a
`.omni/tech-debt.md` row.

## Required review domains

- [x] PM acceptance
- [x] Different-vendor code review
- [ ] QA -- WA-5 fallback: `qa` is not dispatchable while #236's `slot_id` schema-vs-policy deadlock stands; the lead self-QA'd and states the exact commands and counts in the PR body
- [x] Cross-crew review
- [x] Staff / ADR consistency -- already performed on ADR-0055 (verdict NEEDS-A-WIDER-DECISION, settled by program); re-review applies to the built diff
- [x] Security -- recommended by the authoring lead: this adds an external GitHub-WRITE path via `subprocess` `gh`, composes comment bodies from operator-supplied strings, and parses API JSON to make a refusal decision
- [ ] UX -- no human-facing flow or operator UI changes; CLI-only, and `program/review-risk.json`'s `conditional_globs["ux"]` matches no path in this diff

## Finding receipt

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 | cross_crew (crew_beta, review owner); corroborated independently by `security` | `MERGE_BLOCKER` | AC-1 / ADR-0055 §2 — "the posted comment and the stored row cannot disagree" | `_compose_close_comment` interpolated `--actor` raw into the public close-comment body, with no format validation, while `--evidence` beside it was constrained by `EVIDENCE_RE`. Reachable on every close via a required flag | Hermetic PoC, no live system: `--actor "mallory\nDisposition: superseded-by #999"` forges a second `Disposition:` line into a permanent GitHub artifact. Re-parsing the posted body through the gateway's own `_parse_disposition` returns `disposition_ambiguous` — the command could write a comment it could not read. Reproduced by the author before fixing | **resolved** | Fixed at the door with an allowlist (`ACTOR_RE`, ASCII-explicit so a homoglyph cannot impersonate a seat), placed BEFORE the GitHub read so a malformed actor costs no API call — plus a structural post-condition in `_compose_close_comment` asserting the composed body carries exactly one disposition line, so the invariant no longer depends on every future interpolated field remembering to validate itself. 9 new tests (`ActorCannotForgeTheArtifact`); 6 of them fail against pre-fix head `4139886c` |
| F-2 | cross_crew (crew_beta) | `FOLLOW_UP` | ADR-0055 §3 Decision text vs shipped code | §3 stated `superseded → DUPLICATE`; `STATE_REASON_BY_DISPOSITION` ships `superseded → NOT_PLANNED` | Documentation-only divergence; no runtime defect. The engineering call was already correct and tested (`StateReasonHonesty`) | **resolved** | Taken rather than deferred — cheap, and same discipline as the `deploy/policies` ownership correction. ADR-0055 §3 amended in place with a marked build-time amendment explaining that gh/REST cannot express `DUPLICATE` at all, so the drafted mapping was unimplementable rather than merely different |
| F-3 | cross_crew (crew_beta) | `FOLLOW_UP` (informational) | ADR number collision with the quarantined #423 WIP | Not in this diff | — | **acknowledged, not actioned** | Explicitly program's to note on the #423 quarantine record, not crew_alpha's to fix. Recorded here so the recheck does not re-raise it |

The review owner publishes one deduplicated fix packet per round. A severity change must
retain the original finding and record the rationale. Never execute a reproduction against
a live system — in particular, never run this gateway against a real issue to test it; every
test here drives a fake `gh` binary via `NIGHTSHIFT_CTL_GH`.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
| Staff / ADR consistency | `staff` (task `424-adr-review`) | NEEDS-A-WIDER-DECISION — design CONSISTENT and buildable; the one wider question settled by `program` | ADR at commit `05ddfae` | `docs/decisions/ADR-0055-*.md` | `.omni/decisions.md`, "2026-08-31 … #424 ADR-0055 staff verdict settled" |
| PM acceptance | `crew_alpha` PM | (recorded in PR body) | PR head | — | PR body |
| Different-vendor code review |  |  |  |  |  |
| Cross-crew review |  |  |  |  |  |
| Security |  |  |  |  |  |

## Contract amendments

**v2 — 2026-08-31, after cross-crew review round 1.** The v1 rows above are NOT rewritten;
this records what changed around them.

- **Previous version and reason:** v1, frozen at base_sha `53905e39` before the first
  implementation commit. Amended because cross-crew review returned one `MERGE_BLOCKER` (F-1)
  and two `FOLLOW_UP`s (F-2, F-3).
- **Criteria, domains, ADR, or scope changed:** **none.** F-1 is a defect against AC-1's
  existing invariant, not a new or altered criterion; the acceptance criteria table, the
  required-domain set, the ADR, and the scope exclusions are all unchanged from v1. F-2
  amends ADR-0055's §3 prose to match already-shipped, already-tested behaviour — no
  decision was reversed.
- **Attestations invalidated by that change:** the `staff` ADR attestation is unaffected
  (its subject, the design, is unchanged; §3's amendment brings the document INTO line with
  what staff reviewed as buildable). No code_review, cross_crew, or security attestation had
  been issued against head `4139886c`, so none is invalidated — the pending recheck is a
  first attestation, against the new head, not a re-attestation.
- **Implementation work reconsidered:** `--actor` is now validated input rather than
  pass-through text. The generalisable lesson, recorded for the recheck: the AC-1 invariant
  was verified for the two fields the author was thinking about (disposition, evidence) and
  silently assumed for the third. The added post-condition asserts the invariant on the
  ARTIFACT instead of on the inputs, which is why a cold reviewer found this and the author's
  own 46 tests did not.
