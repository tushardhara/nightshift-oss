# PIPELINE — GitHub/PR/review workflow, release gates, coverage

DoD condition 15 (`docs/prd/DEFINITION-OF-DONE.md` §D, `docs/prd/DOD-MANIFEST.json`
`rt_ids`/`preflight_checks`). Answers Q1–Q4, closes issue #479.

**Authored directly by the `crew_beta` lead, not by a dispatched engineer —
working agreement 5 (`.omni/working-agreements.md` item 5).** Grandchild
dispatch (`beta_eng_claude`/`beta_eng_codex`) was confirmed sterile for this
run before this document was started: the `nightshift-ctl` binary actually on
`$PATH` on this host (`/srv/nightshift-releases/71eee11dd391a14a0fa09018cade0cdeb0b13bc9/checkout/bin/nightshift-ctl`)
is missing origin/main's admission-derivation fix and is 15 `cmd_` handlers
behind a fresh checkout (56 vs 71 — verified live below), which is exactly the
defect tracked as issues **#489** and **#490**. A worker dispatched against
that stale binary cannot correctly acquire/consume the continuation slot this
task would need, so per WA-5 this crew builds under the slot it already holds
(control-plane task `479-pipeline-audit`, lease 54 on this exact path) instead
of routing to an engineer. In-crew review and in-crew QA did not happen for
this document. Cross-crew review (crew_alpha) is not customary here, it is
**mandatory**, and is the sole independence check this PR gets.

Audit is documentation only: no code changed, and condition 22's own
workflow-followed validator logic (`tests/dod_validate.py::check_d22`) is
cited, never re-implemented.

**Reference commit for every citation below**, unless a citation states a
different SHA explicitly: `f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b` (this
worktree's base, `crew_beta/479-pipeline-audit`, checkpoint 36). Live
`gh`/`git` commands were re-run today (2026-09-01) against the real repo and
report their own timestamp implicitly by being live.

Evidence types below follow condition D's rule: `permalink` = path + symbol +
SHA, `command` = the exact command plus its captured result, `test` = an `RT`
id. `F-nnn` finding ids are minted by *this* document (docs/audit/FINDINGS.md,
issue #482, does not exist as a file yet — #482 itself is open but unbuilt at
the time of writing, so these ids are proposed here and should be reconciled,
not silently renumbered, when #482 is built).

---

```json
[
  {
    "id": "Q1",
    "verdict": "CONFIRMED-BUG",
    "evidence": [
      {"type": "permalink", "path": "deploy/policies/omnipods_policies.py", "symbol": "worker_semaphore, continuation_kind=='review' branch", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "valid = role=='reviewer' and purpose=='review' and task['pr_url'] — no comparison of the reviewer's crew or vendor against the implementer's anywhere in this function or file"},
      {"type": "permalink", "path": "tests/dod_validate.py", "symbol": "check_d22", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "only asserts data.get('reviews') is truthy (>=1 review recorded); never reads a reviewer login, crew label, or vendor, so it cannot and does not distinguish a self-review or a same-crew review from an independent one"},
      {"type": "command", "cmd": "grep -n \"pr merge\\|self.review\\|different.crew\\|different_crew\" deploy/policies/omnipods_policies.py", "result": "no match — no policy evaluator in the file inspects a `gh pr merge` call, or compares actor identity to author/reviewer identity, anywhere"},
      {"type": "command", "cmd": "gh api repos/tushardhara/nightshift --jq .permissions.push (implicit via TRUST-BOUNDARY.md credential table); gh auth status", "result": "every seat authenticates through one repo-scoped PAT (`~/.config/gh`) with contents:write + pull_requests:write; GitHub therefore records ONE identity (`tushardhara`) as both author and any reviewer/merger for every PR"},
      {"type": "permalink", "path": ".omni/tech-debt.md", "symbol": "row dated 2026-08-22, \"main has no branch protection and cannot have it on this repo's plan\"", "sha": "7a776da", "detail": "pre-existing program finding naming this exact document/question and instructing a CONFIRMED-BUG verdict"},
      {"type": "permalink", "path": ".omni/tech-debt.md", "symbol": "row dated 2026-08-22, \"Review independence is not mechanically verifiable\"", "sha": "a48c0c0", "detail": "pre-existing product finding naming this exact document/question: a validator can prove a review exists, that the body lints, and that gates ran — it cannot prove the reviewer was a different crew or vendor"}
    ],
    "findings": ["F-101", "F-102"]
  },
  {
    "id": "Q2",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": ".github/workflows/tests.yml", "symbol": "jobs.tests.steps", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "unit suite (tests/run.sh), policy module import, `bash -n` over every shell script — three deterministic, same-input-same-output gates"},
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "lint_body / cmd_pr_lint", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "pure regex checks (CLOSING_RE, REFS_RE) over the PR body text — deterministic"},
      {"type": "permalink", "path": "bin/nightshift_review_plan.py", "symbol": "module (ADR-0036/ADR-0042 classifier)", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "which review domains a diff requires (code_review/qa always; security/ux/staff/cross_crew conditionally) is computed from a committed path-glob ruleset (program/review-risk.json) plus exact-SHA diff paths — deterministic routing, tested by tests/test_233_review_routing_activation.py"},
      {"type": "permalink", "path": "tests/dod_validate.py", "symbol": "check_d22", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "\"a review is recorded\" and \"gate results are attached\" are deterministic presence checks; whether the recorded review's CONTENT was correct (PASS/MERGE_BLOCKER judgement) is not machine-checked at all — that is a model's judgement call, made once, by the reviewing agent"},
      {"type": "permalink", "path": ".omni/tech-debt.md", "symbol": "row dated 2026-08-30, \"A qualifying diff … is not mechanically checked for an actual corresponding ADR\"", "sha": "49d0095", "detail": "the ROUTING to `staff` is mechanical (program/review-risk.json + tests/test_233_review_routing_activation.py); whether an ADR actually exists/is consistent is judged entirely by the `staff` agent reading docs/reviews/REVIEW-CONTRACT-TEMPLATE.md — model judgement, no second machine check"}
    ]
  },
  {
    "id": "Q3",
    "verdict": "CONFIRMED-BUG",
    "evidence": [
      {"type": "permalink", "path": "bin/nightshift-ctl", "symbol": "cmd_issue_close (ADR-0055, #424)", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "deterministic disposition gateway: grammar check, actor-format check (F-1 in that file's own inline numbering, a DIFFERENT namespace from this doc's F-101/102), milestone-registry check for 'deferred', live (never cached) GitHub read, refuses on already-closed — every refusal path is a pure function of its inputs"},
      {"type": "permalink", "path": "deploy/preflight.sh", "symbol": "branch-protection block, lines 249-265", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "reads `gh api repos/{owner}/{repo}/branches/${branch}/protection`; if `required_approving_review_count` >= 1 it reports `bad` (FAIL, the deadlock case); if protection is present without that it reports `ok`; if the API call returns nothing it only `warn`s — the detection logic for the exact configuration Q3 asks about IS present and correctly shaped"},
      {"type": "permalink", "path": "docs/prd/DOD-MANIFEST.json", "symbol": "preflight_checks[] id=\"branch protection requires human approvals — the integrator has no approver and every merge will deadlock. …\"", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "severity FAIL, matching preflight.sh's `bad(...)` call exactly; carries `fixture_exempt_reason: \"depends on a live `gh api` branch-protection response\"` — i.e. this check's id is asserted PRESENT by tests/test_preflight.py's structural scan, but its own bad/warn branch logic has never been exercised by any hermetic test"},
      {"type": "command", "cmd": "gh api repos/tushardhara/nightshift/branches/main/protection", "result": "HTTP 403: \"Upgrade to GitHub Pro or make this repository public to enable this feature.\" (run live today) — this repo is PRIVATE (`gh repo view --json isPrivate` -> true) on a plan where branch protection cannot currently be enabled AT ALL, so the human-approval-deadlock configuration is not merely unconfigured, it is presently unreachable on this box regardless of intent"},
      {"type": "permalink", "path": "docs/prd/DEFINITION-OF-DONE.md", "symbol": "B5 (Verified baselines and assumptions table)", "sha": "f82559a7cd3a5a23d30d9a15c5cf84f6f05cf30b", "detail": "every Codex seat (architect/release/eng_codex/qa/reviewer_codex) is down on this host, so nothing merges to main today regardless of branch protection — condition 22 is written to gate on the workflow being followed, not on integration having happened, precisely because of this fact"},
      {"type": "permalink", "path": ".omni/tech-debt.md", "symbol": "row dated 2026-08-30, \"26 of … 40 FAIL-severity preflight_checks entries … carry fixture_exempt_reason instead of a hermetic tests/test_preflight.py fixture\"", "sha": "0bf29e0", "detail": "names the exact class this branch-protection check belongs to: completeness (the id exists) is mechanically enforced, correctness of the check's own logic is not"},
      {"type": "command", "cmd": "grep -A1 \"2026-08-31.*Issue #424's .nightshift-ctl issue close. gateway\" .omni/tech-debt.md", "result": "row present in the live host's .omni/tech-debt.md (dated 2026-08-31): the Program's own heartbeat prompt still issues a raw `gh issue close`, writing no `issue_close_dispositions` row — a live, disclosed bypass of the deterministic gateway described above. NOT reachable via git from origin/main or this PR's own base f82559a7 — committed only in a local, unpushed main commit (93cd778; `git branch -r --contains 93cd778` returns none) — so this is cited by content and date, not by a resolvable SHA, until that commit is pushed"},
      {"type": "command", "cmd": "grep -A1 \"2026-08-31.*commit-message keyword accidentally auto-closed #449\" .omni/tech-debt.md", "result": "row present in the live host's .omni/tech-debt.md (dated 2026-08-31): GitHub's own closing-keyword detection at merge time closed #449 outside the gateway entirely, from an unrelated commit's message — a second, independent bypass the gateway itself cannot see or refuse. Same reachability caveat as the row immediately above: local, unpushed commit 93cd778 only, not origin/main or this PR's base — cited by content and date, not by SHA"}
    ],
    "findings": ["F-103"]
  },
  {
    "id": "Q4",
    "verdict": "CONFIRMED-BUG",
    "evidence": [
      {"type": "command", "cmd": "python3 -c \"import json;d=json.load(open('docs/prd/DOD-MANIFEST.json'));print(len(d['rt_ids']))\"", "result": "23 RT ids allocated (RT-01..RT-23), bound to DoD conditions 1, 2, 3, 4, 5, 6, 7 only — conditions 9-24 (which includes 13/CONCURRENCY.md and 14/SURVIVAL.md, the control-plane/deploy-safety audits) have zero RT ids allocated as of this commit"},
      {"type": "command", "cmd": "python3 -c \"import json;d=json.load(open('docs/prd/DOD-MANIFEST.json'));[print(k,v['test_node_id']) for k,v in d['rt_ids'].items() if v.get('test_node_id') is None]\"", "result": "RT-16, RT-17 (condition 6, worker-budget-is-global), RT-18, RT-19 (condition 7, duplicate-dispatch-is-enforced) are allocated ids with NO bound test_node_id — a gap recorded in the manifest's own data, not inferred"},
      {"type": "command", "cmd": "ls tests/*.py | wc -l", "result": "72 test modules exist covering control-plane surface with no RT id at all: worker_semaphore/slots (test_slots.py, test_stress.py), program-authority fence (test_authority.py, test_authority_fence.py), pr-close-keyword guard (test_policy.py), issue-close gateway (test_issue_close_gateway.py, test_issue_semantics.py), leases (test_leases.py), claims (test_claims.py), checkpoints (test_checkpoint.py), recovery (test_recovery.py), review receipts/eligibility/contract (test_review_receipts.py, test_review_eligibility.py, test_review_contract.py), review-risk routing (test_233_review_routing_activation.py, test_shadow_risk_plan.py), bookkeeping fast release (test_bookkeeping_release.py, test_exact_sha_release.py), heartbeat-persist gate (test_heartbeat_persistence.py, test_heartbeat_persistence_replay.py, test_heartbeat_producer.py), worktree reap (test_worktree_reap.py) — all real, all green in tests/run.sh, none carry an RT id because the RT/baseline scheme (condition 8) is scoped to section A's five confirmed bugs plus two duplicate-work defects, not to the whole suite"},
      {"type": "command", "cmd": "grep -rl \"nightshift-state-backup\" tests/*.py", "result": "3 matches (tests/dod_validate.py, tests/test_user_manager.py, tests/test_watchdog.py), all incidental — systemd unit-name strings and docstring/comment context only (verified by grep -n on each: dod_validate.py:476 lists the unit name in a preflight-service enumeration; test_user_manager.py references the unit name in a capability list and a docstring; test_watchdog.py references the script's role in comments). None of the three invokes or exercises deploy/nightshift-state-backup.sh's own logic — the script itself has no dedicated test, hermetic or otherwise"},
      {"type": "permalink", "path": ".omni/tech-debt.md", "symbol": "row dated 2026-08-22, \"Sub-agent results are silently truncated in transit\"", "sha": "a8b17d5", "detail": "a 40k-character document arrived cut at ~24.7k with no error, and sys_session_get_history truncates the same message again; the child is never told its output was cut. Confirmed, unfixed, zero RT id, zero automated test — workaround in live use is a manual chunked-resend protocol ([CONTINUES]/[END] markers), not a fix"},
      {"type": "permalink", "path": ".omni/flaky.md", "symbol": "entry dated 2026-08-22, tests/run.sh wave 1", "sha": "7a2b7d5", "detail": "one unattributed full-suite failure, not reproduced in 14 subsequent runs including under 8x CPU contention; prime suspect named as the sleep-based TTL tests in tests/test_slots.py; recorded, not quarantined, owner crew_alpha — the flaky-inventory table itself has this single row and is otherwise empty"}
    ],
    "findings": ["F-104"]
  }
]
```

## Q1 — enforcement vs instruction, answered separately for the three named properties

**Different-crew review.** *Instructed, not enforced.* The dispatch-time policy
gate (`worker_semaphore` in `deploy/policies/omnipods_policies.py`, the ONLY
code that runs before a reviewer sub-agent is dispatched) checks role,
purpose, and that the task carries a `pr_url`. It never reads which crew or
vendor the *implementer* was, so it cannot refuse a same-crew or same-vendor
reviewer. What actually routes review to a DIFFERENT crew is prompt text:
crew lead instructions ("pick the other vendor") and
`program/skills/cross-crew-review/SKILL.md`. `docs/architecture/H1-WALKING-SKELETON-GAP.md`
independently records the same gap ("cross-crew review routes to a different
crew … With one crew it explicitly has no cross-crew review"). The one
mechanically-enforced piece adjacent to this is *routing*: ADR-0036/ADR-0042's
`bin/nightshift_review_plan.py` deterministically decides which review
*domains* (`code_review`, `qa`, and conditionally `security`/`ux`/`staff`/
`cross_crew`) a diff requires, from a committed glob ruleset — but it decides
domains, not identities, and does not verify that the domain it names was
actually staffed by a different crew.

**Self-review prohibition.** *Instructed, not enforced.* Same evidence as
above: nothing in `worker_semaphore`, nothing in
`tests/dod_validate.py::check_d22` (which only requires `reviews` to be a
non-empty array), and nothing in `pr_close_keyword_guard` inspects whether the
account that opened the PR is the same account reviewing or merging it. It
structurally cannot, for the reason below.

**Release-only merge.** *Instructed, not enforced — and currently not
enforceable by GitHub either.* Every seat (`release` included) authenticates
through one repo-scoped PAT (`docs/TRUST-BOUNDARY.md`'s credential table);
`gh pr merge` from any worker's shell would succeed at the GitHub-permissions
layer, because GitHub sees one identity for the whole org. No policy in
`deploy/policies/omnipods_policies.py` inspects a `gh pr merge`/`gh api …
pulls/…/merge` command the way `pr_close_keyword_guard` inspects `gh pr
create`/`gh pr edit` — there is no merge-time guard at all. The one
mechanism that COULD enforce this deterministically, GitHub branch
protection, cannot currently be turned on for this repository: a live `gh api
repos/tushardhara/nightshift/branches/main/protection` call today returns
HTTP 403 "Upgrade to GitHub Pro or make this repository public to enable this
feature." So "only `release` writes to `main`" is presently upheld by nothing
but every seat's own prompt discipline. This matches `.omni/tech-debt.md`'s
2026-08-22 row verbatim, which already names this exact document and
instructs a `CONFIRMED-BUG` verdict rather than a softened one.

One further, narrower mechanism exists and is worth naming precisely so it is
not conflated with the above: `program_authority_fence`
(`deploy/policies/omnipods_policies.py`, ADR-0044) gates which SESSION the
*Program* may dispatch to among its direct children (`product`, `architect`,
`crew_alpha`, `crew_beta`, `ux`, `staff`, `platform`, `security`, `release`,
`oncall`) at most once per authority generation. That is real, tested
(`tests/test_authority_fence.py`), and prevents two concurrent Program
sessions from double-dispatching the *same* seat — it says nothing about who
may run `gh pr merge` once dispatched, and is not a release-only-merge
control.

## Q2 — deterministic gates vs model-judgement gates

**Deterministic (same inputs → same verdict), each independently checkable
by a stranger:**
- CI (`.github/workflows/tests.yml`): the stdlib unit suite (`tests/run.sh`),
  a standalone policy-module import, and `bash -n` over every shipped shell
  script.
- PR-body lint (`bin/nightshift-ctl pr-lint`, `lint_body`): pure regex over
  the body text (closing-keyword detection, `Refs #n` presence).
- Review-domain routing (`bin/nightshift_review_plan.py`, ADR-0036/ADR-0042):
  a path-glob classifier over the exact-SHA diff against a committed
  ruleset — the same diff always names the same required domains.
- `tests/dod_validate.py::check_d22`'s three PRESENCE checks: PR body lints
  clean, `reviews` array non-empty, `statusCheckRollup` non-empty. These are
  deterministic in the narrow sense of "did something happen", not "was it
  correct" — see below.
- The issue-close disposition gateway (`cmd_issue_close`, ADR-0055): grammar,
  actor format, milestone lookup, already-closed refusal are all pure
  functions of their inputs plus one live (never cached) GitHub read.

**Model-judgement only, with no second machine check behind it:**
- The actual PASS/FAIL/MERGE_BLOCKER content of any review — cross-crew,
  in-crew, security, staff-ADR-consistency — is a judgement call made once by
  the reviewing agent. `check_d22` proves a review *exists*; nothing proves
  it was *right*, or even that it addressed the diff.
- `staff`'s ADR-consistency verdict specifically: `.omni/tech-debt.md`
  (2026-08-30 row) already records that nothing checks a qualifying diff
  actually carries a corresponding ADR — the routing to `staff` is
  mechanical, the verdict itself is not machine-checked at all.
- QA's own coverage judgement (whether new/changed tests are adequate) has
  no deterministic backstop beyond the suite passing.
- Reviewer identity independence (Q1) is, by construction, judged by nobody
  and checked by nothing — it is asserted by the lead who dispatches, which
  is precisely why Q1 returns `CONFIRMED-BUG` rather than `SOUND`.

## Q3 — issue-close semantics, the branch-protection deadlock question, and whether preflight detects it

**Issue-close semantics.** ADR-0055 (#424) gives `nightshift-ctl issue close`
as the sole SANCTIONED path: it records an append-only
`issue_close_dispositions` row, requires a well-formed `Disposition:` grammar,
validates `deferred`-to milestones against `docs/prd/MILESTONES.md`, reads
GitHub live (never a cache) and refuses on an already-closed issue. This is
deterministic and tested (`tests/test_issue_close_gateway.py`,
`tests/test_issue_semantics.py`). It is, however, **not the only path a
close can take**, and both real bypasses are already disclosed and dated
(2026-08-31) in the live host's `.omni/tech-debt.md` — not yet reachable
from origin/main or this PR's own base, committed only in a local, unpushed
commit, so cited by content/date rather than by SHA (see the Q3 evidence
block above): the Program's own heartbeat prompt still issues a raw
`gh issue close` (writing no disposition row at all), and GitHub's own
closing-keyword scanner closed issue #449 automatically at merge time from an
unrelated commit message, entirely outside the gateway. Neither bypass is
policy-blocked — there is no analogue of `pr_close_keyword_guard` for `gh
issue close`.

**Would branch protection requiring human approval deadlock every merge?**
Yes, in principle: this org has no human approver in its normal operating
loop (`release` merges unattended), so a branch rule with
`required_approving_review_count >= 1` would mean every PR queues forever.
Condition 22's B5 finding already establishes a milder version of the same
symptom today (Codex seats down ⇒ `release`/`qa` cannot run ⇒ nothing merges),
independent of branch protection entirely. **But the deadlock configuration
is not merely absent, it is currently impossible to create on this specific
repository**: a live branch-protection API call returns HTTP 403 because this
private repo is not on a GitHub plan that supports the feature at all. So the
honest answer has two parts that must not be collapsed into one: (a) the
mechanism, if it could be turned on, would deadlock merges exactly as
condition 22 fears, and (b) it cannot be turned on right now, by anyone, on
this repo.

**Does `deploy/preflight.sh` actually detect it?** The check exists, is
correctly shaped, and matches the FAIL-severity entry already enumerated in
`docs/prd/DOD-MANIFEST.json`'s `preflight_checks` (`"branch protection
requires human approvals — the integrator has no approver and every merge
will deadlock. …"`, cross-referenced above): it inspects
`required_approving_review_count` from the live protection payload and
reports `bad` when it is ≥1, `ok` when protection exists without it, `warn`
when protection is entirely absent. The verdict here is `CONFIRMED-BUG`, and
the reason is *not* "the logic is wrong" — it is that **the logic has never
once been exercised by a hermetic test**. Its own manifest entry carries
`fixture_exempt_reason: "depends on a live gh api branch-protection
response"`, and `.omni/tech-debt.md`'s 2026-08-30 row names this as one of 26
of 40 FAIL-severity preflight checks in exactly that state: the check's *id*
is asserted present by `tests/test_preflight.py`'s structural scan, but a
regression that silently inverted the `bad`/`ok` branches, or read the wrong
JSON key, would pass every existing test. Given B5 and the 403 above, that
regression is not observable live on this box today either — which is what
makes it a genuine, silent gap rather than a softened one.

## Q4 — coverage map and the environment-dependent/flaky inventory

**RT-id-pinned coverage** (from `docs/prd/DOD-MANIFEST.json`'s `rt_ids`, 23
entries, conditions 1–7 only):

| Invariant (condition) | RT ids | Test node(s) | Status |
|---|---|---|---|
| Go version/checksum discovery survives `set -o pipefail` (1) | RT-01, RT-02, RT-23 | `tests.test_go_toolchain.*` | bound |
| Missing Go toolchain installs rather than aborts (2) | RT-03, RT-04 | `tests.test_go_toolchain.*` | bound |
| Unattended user-service install without an assumed `systemctl --user` bus (3) | RT-05, RT-06, RT-07 | `tests.test_user_manager.*` | bound |
| Watchdog liveness is content-validated, not mtime-heuristic (4) | RT-08–RT-12, RT-21, RT-22 | `tests.test_watchdog.*`, `test_beat`, `test_slots.GlobalSemaphore.*` | bound |
| Bubblewrap probed by function, not presence (5) | RT-13, RT-14, RT-15 | `tests.test_bwrap_probe.*` | bound |
| Worker budget is global (6) | RT-16, RT-17 | *(none)* | **allocated, unbound — recorded gap** |
| Duplicate dispatch is enforced (7) | RT-18, RT-19, RT-20 | RT-20→`test_fencing`; RT-18/19 unbound | **partial — recorded gap** |

**Control-plane and deploy-safety invariants with real, green test coverage
but NO `RT` id at all** — the RT/baseline scheme (condition 8) is scoped to
section A's five confirmed bugs plus the two duplicate-work defects, not to
the rest of the control plane, so every one of these is a structural gap
against Q4's own coverage-map ask, not a defect in the code being tested:

| Invariant | Mechanism | Test file(s) |
|---|---|---|
| Global semaphore admission (slot acquire/consume, atomic budget) | `worker_semaphore` | `test_slots.py`, `test_stress.py` |
| Program-authority single-dispatch fence (ADR-0044) | `program_authority_fence` | `test_authority.py`, `test_authority_fence.py` |
| PR-body closing-keyword guard | `pr_close_keyword_guard` | `test_policy.py` |
| Issue-close disposition gateway (ADR-0055, #424) | `cmd_issue_close` | `test_issue_close_gateway.py`, `test_issue_semantics.py` |
| Ownership leases (one holder, serialize/normal) | `lease acquire/release` | `test_leases.py` |
| Task claims / recovery | `task_claims` | `test_claims.py`, `test_recovery.py` |
| Checkpoints | `checkpoint` | `test_checkpoint.py` |
| Worktree reap | `worktree reap` | `test_worktree_reap.py` |
| Review receipts / eligibility / contract (ADR-0039) | `review_receipts` | `test_review_receipts.py`, `test_review_eligibility.py`, `test_review_contract.py` |
| Review-risk domain routing (ADR-0036/0042) | `nightshift_review_plan.py` | `test_233_review_routing_activation.py`, `test_shadow_risk_plan.py` |
| Bookkeeping-only fast release path (ADR-0052) | `deploy/exact_sha_release.py` | `test_bookkeeping_release.py`, `test_exact_sha_release.py` |
| Heartbeat-persist gate (ADR-0053) | `heartbeat_persist_decisions` | `test_heartbeat_persistence.py`, `test_heartbeat_persistence_replay.py`, `test_heartbeat_producer.py` |
| Preflight deploy-safety checks (69 entries, 26 fixture-exempt) | `deploy/preflight.sh` | `test_preflight.py` (structural only — see Q3) |

**Deploy-safety scripts with NO test of their own logic:** `deploy/nightshift-state-backup.sh`
(`omni/state` persistence, run every heartbeat) is never invoked or exercised
by any test. `grep -rl nightshift-state-backup tests/*.py` does return 3
matches (`tests/dod_validate.py`, `tests/test_user_manager.py`,
`tests/test_watchdog.py`), but each is incidental — a systemd unit-name
string or a docstring/comment mentioning the script's role, never a call
into the script itself. No test drives its behavior.

**Environment-dependent / flaky inventory:**
1. **Sub-agent transport truncation (recorded, unfixed).**
   `.omni/tech-debt.md` (2026-08-22): a 40k-character document arrived
   truncated at ~24.7k with no error signal, and `sys_session_get_history`
   truncates the same content again on read-back — the sending child is never
   told its own output was cut. No RT id, no automated test exists; the only
   mitigation in live use is a manual chunked-resend protocol
   (`[CONTINUES]`/`[END]` markers). This document itself was written by
   direct file I/O rather than returned through a child session specifically
   to avoid re-triggering this defect while producing it.
2. **One unreproduced `tests/run.sh` failure, wave 1 (`.omni/flaky.md`,
   2026-08-22).** A single full-suite run returned one unattributed failure;
   not reproduced across 14 subsequent runs including three under 8x CPU
   contention. Prime suspect named as the sleep-based TTL tests in
   `tests/test_slots.py`. Recorded, not quarantined; owner `crew_alpha`.
   `.omni/flaky.md`'s table has exactly this one row.
3. **Branch-protection preflight checks (Q3).** Environment-dependent by
   construction (`fixture_exempt_reason: "depends on a live gh api
   branch-protection response"`) and, on this repo/plan, currently
   unobservable live at all (403). Listed here rather than only under Q3
   because it is also, mechanically, an untested code path.

---

## Findings summary (see `docs/audit/FINDINGS.md` / issue #482 once built)

- **F-101** — Release-only-merge onto `main` is enforced by prompt discipline
  alone: every seat shares one GitHub PAT capable of `gh pr merge`, no policy
  evaluator inspects a merge command, and GitHub branch protection — the one
  mechanism that could enforce it deterministically — cannot currently be
  enabled on this private repository's plan (live 403). Q1.
- **F-102** — Self-review and different-crew/different-vendor review
  independence are not mechanically verifiable: `worker_semaphore`'s review
  continuation branch and `check_d22` both stop at "a review exists", never
  at "the reviewer differs from the author." Root cause is structural (one
  shared PAT records one GitHub identity for every actor), matching
  `.omni/tech-debt.md`'s 2026-08-22 and 2026-08-28 rows. Q1.
- **F-103** — 26 of `docs/prd/DOD-MANIFEST.json`'s 40 FAIL-severity
  `preflight_checks` — including both branch-protection checks Q3 depends
  on — are asserted present by `tests/test_preflight.py`'s structural scan
  but have never had their own bad/warn branch logic exercised by a
  hermetic test; a regression in exactly that logic would currently go
  undetected by CI. Q3.
- **F-104** — The sub-agent transport truncation defect
  (`.omni/tech-debt.md`, 2026-08-22): large child-to-parent results are
  silently cut in transit with no error to either party, and the same
  content truncates again on `sys_session_get_history` read-back. No RT id,
  no automated test, live workaround only. Q4.

None of the four are remediated here — remediation is explicitly out of this
issue's scope (issue #479, "Not included / scope boundary") and is tracked
through `docs/audit/FINDINGS.md`/#482 once that document exists.
