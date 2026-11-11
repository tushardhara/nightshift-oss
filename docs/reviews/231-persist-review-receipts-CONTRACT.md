# Review contract

Copy this template into a committed work-item/spec artifact and freeze that file at
the wave checkpoint before the first implementation commit. The delivery PR links
and may mirror the exact committed version; its mutable body is never the authority.
Do not edit a frozen version in place.

## Identity

- Work item: GitHub issue #231, "#228B — Persist review receipts and consolidate
  findings" (tushardhara/nightshift). state:ready, 5 DoR rounds complete, architect
  and staff both signed off.
- Committed contract path and blob OID: `docs/reviews/231-persist-review-receipts-CONTRACT.md`;
  blob OID is the exact-content hash of this file at the freezing commit — read it
  with `git rev-parse <freezing-commit>:docs/reviews/231-persist-review-receipts-CONTRACT.md`
  (recorded verbatim in the freezing commit message and in
  `.omni/registry-crew_alpha.json`, since a self-referential hash cannot be embedded
  in the hashed content itself).
- Contract version: v1.
- Base SHA: `186eedb98b1a9a82b04db108fb120374a14d13d8` (checkpoint id 2, current
  `main` tip at freeze time, includes #228A).
- ADR: `docs/decisions/ADR-0039-persist-review-receipts.md` (authored in this work
  item, reviewed by `staff` before merge).
- Review owner: crew_beta (cross-crew, round-robin from crew_alpha as implementer).
- Frozen at (UTC): 2026-08-28T09:29:46Z.

## Acceptance criteria

| ID | Exact criterion | Verification |
|---|---|---|
| AC-1 | Minimal durable review round + receipt + finding records. Reuse existing `tasks`, `task_claims`, `worker_slots`, `task_admissions`, static roster. No new admission system, no per-seat semaphore. | Schema diff adds only `review_receipts`, `review_findings`, `review_dispatch_markers`; no change to admission/semaphore tables or `COUNTED_AGENTS`/`EXEMPT_AGENTS`; ADR §Decision states the reuse explicitly. |
| AC-2 | Receipts bind to `(task_id, exact head SHA, domain, PR-comment permalink)` — NOT `agent_runs`/`worker_slots`. | `review_receipts` primary key is `(task_id, head_sha, domain)` plus a stored `comment_url` column; no foreign key or join to `agent_runs`/`worker_slots`; test asserts the column set. |
| AC-3 | Receipt validation at persistence time: (a) comment body contains a structured, machine-parseable block with exact fields `domain`/`task_id`/`head_sha`/`verdict`, exact match required; (b) comment's repository and PR number match the task's exact `pr_url`, no exceptions; (c) comment body contains a Program-minted opaque per-dispatch marker matching what Program recorded for that `(task, head, domain)` tuple; (d) fresh GitHub API fetch at validation time (not cached) — if `updated_at != created_at` (edited) or the comment no longer exists, fail closed, must be re-captured. | `tests/test_review_receipts.py`: one test per fail-closed branch (malformed block, extra/missing field, domain/task_id/head_sha mismatch, wrong repo, wrong PR number, missing marker, marker mismatch, edited comment, deleted/unreadable comment, unparseable verdict) plus one happy-path test proving persistence only on full pass. |
| AC-4 | On successful validation, persist a content digest (sha256 of the canonical structured block, same discipline as #228A's planner). From that point on, this persisted receipt+digest is authoritative — consolidation is a pure DB read, NEVER a live GitHub call. | `review_receipts.report_digest` computed via `sha256(canonical_json(block))` (`canonical_json` = `json.dumps(sort_keys=True, separators=(",", ":"))`, matching `bin/nightshift_review_plan.py`); `review consolidate` command contains no GitHub/subprocess call — proven by a conformance test that inspects the command's import graph / mocks `subprocess` to assert zero invocations. |
| AC-5 | Consolidation waits for every domain in ADR-0026's frozen per-work-item required-review-domains set (this contract's own checklist below, made machine-readable per AC-9) — NEVER #228A's `suggested_domains` (diagnostic/advisory only per ADR-0036). | `review consolidate` reads required domains only via `nightshift_review_receipts.parse_required_domains()` against a committed contract file; test proves it never imports or calls `nightshift_review_plan`. |
| AC-6 | Findings use exactly `MERGE_BLOCKER` / `PRE_DEPLOYMENT` / `FOLLOW_UP` as a DB CHECK constraint, ordered `MERGE_BLOCKER > PRE_DEPLOYMENT > FOLLOW_UP`. | `review_findings.severity` has `CHECK(severity IN (...))`; test inserts an invalid severity directly via sqlite3 and asserts `IntegrityError`; `consolidate_findings()` sort order test. |
| AC-7 | Equivalent findings across domains consolidate by a deterministic fingerprint that EXCLUDES `domain` as an input — keep the highest severity on collision. | `fingerprint()` takes only normalized `criterion`+`summary`, never `domain`; test feeds two findings with identical normalized text and different domains/severities and asserts one consolidated row at the higher severity, with both contributing domains listed. |
| AC-8 | Use `report_digest` (not `plan_digest`) for exact-head binding — it invalidates conservatively on any review-relevant change. | Column is named `report_digest`; grep test in the test suite asserts `plan_digest` does not appear in `bin/nightshift_review_receipts.py` or the new schema block. |
| AC-9 | Make REVIEW-CONTRACT-TEMPLATE.md's required-domains checklist machine-readable (new, in-scope — needed for AC-5, doesn't reuse evidence across heads (#232), doesn't gate dispatch (#233)). | `docs/reviews/REVIEW-CONTRACT-TEMPLATE.md` gains a short, precise parsing-rule paragraph under "Required review domains"; `nightshift_review_receipts.parse_required_domains(text)` implements it exactly; tests cover this contract file, the bare template, an unrecognized label, and a duplicate label (all fail closed except the two valid documents). |
| AC-10 | Schema change via migration sequence 1 (already allocated), documented in the successor ADR. | ADR §Decision cites `nightshift-ctl seq next --name migrations --resource 'bin/nightshift-ctl' --holder crew_alpha` → 1; `ensure_schema()` comment cites the same. |
| AC-11 | Document explicitly in the ADR (and add a line to `.omni/tech-debt.md` beside the existing shared-PAT entry) that this mechanism protects against accidental misattribution and stale/copy-pasted receipts, NOT a malicious or compromised agent forging another domain's receipt — that would need per-seat credentials, out of scope, named as a reversal trigger. | ADR §Consequences states the threat model boundary; `.omni/tech-debt.md` has a new dated row cross-referencing the shared-PAT row and this ADR. |

## Explicitly out of scope

- Evidence reuse after a changed head (#232).
- Aggregate Release eligibility.
- Activating conditional routing (#233).
- Publication, transport, retries, tags, integration, rollback.
- Sandbox/process recovery, workflow changes, deployment, VPS changes.
- #213–#223.
- Any change to `program/**` prompts, `deploy/**` (outside `deploy/policies/**`), or
  `agent_runs`/`worker_slots`/`task_admissions` schema or semantics.

## Required review domains

- [x] PM acceptance -- already done via the 5-round DoR thread on issue #231
- [x] Different-vendor code review
- [x] QA -- behaviorally meaningful; needs real test coverage of the fail-closed
      matrix in AC-3
- [x] Cross-crew review -- crew_beta is review owner
- [x] Staff / ADR consistency -- new ADR (schema + migration) needs the staff gate
- [x] Security -- findings/receipts are external-input-shaped (GitHub comment
      bodies are attacker-adjacent) and may carry secrets/PII per the architect's
      DoR finding
- [ ] UX -- no human-facing flow or operator interface changes; this is data-layer
      only

Machine-readable parsing of this checklist follows the rule added to
`docs/reviews/REVIEW-CONTRACT-TEMPLATE.md` under its own "Required review domains"
section (AC-9): each row is matched by a fixed canonical-label PREFIX
(`PM acceptance`, `Different-vendor code review`, `QA`, `Cross-crew review`,
`Staff / ADR consistency`, `Security`, `UX`), independent of any parenthetical or
`--`-delimited rationale that follows; `[x]`/`[X]` means required, `[ ]` means not
required for this work item. `nightshift_review_receipts.parse_required_domains()`
fails closed (raises) on an unrecognized label, a label appearing twice, or a
missing canonical row — it never guesses.

Removing a domain after freeze requires a new contract version and rationale.

## Finding receipt

Every finding uses one severity from ADR-0026.

| ID | Domain | Severity | Criterion or invariant | Changed hunk and causal path | Failure scenario or safe reproduction and evidence | Status | Resolution rationale |
|---|---|---|---|---|---|---|---|
| F-1 |  | `MERGE_BLOCKER` / `PRE_DEPLOYMENT` / `FOLLOW_UP` |  |  |  | open |  |

The review owner publishes one deduplicated fix packet per round. A severity change
must retain the original finding and record the rationale.

Never execute a reproduction against a live system. A production-only security or
data-loss risk may be proven by a concrete causal failure scenario and safe evidence.

## Scoped attestations

| Domain | Reviewer | Verdict | Head SHA / tree | Blob OIDs and dependency seams | Evidence reference |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

Content evidence may transfer only across identical reviewed blobs with unchanged
dependency seams. Re-run CI, provenance, PR-body, authorization, and live/runtime
checks.

## Contract amendments

Never rewrite the frozen rows above. Create the next version and record:

- previous version and reason;
- criteria, domains, ADR, or scope changed;
- attestations invalidated by that change;
- implementation work that must be reconsidered.

No amendments yet. This is v1, frozen at the timestamp in Identity above.
