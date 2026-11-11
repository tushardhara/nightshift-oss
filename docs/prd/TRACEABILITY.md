# Traceability matrix — every promise in the brief, mapped to its evidence

**DoD condition 24 (issue #485).** Date: 2026-09-02 · Crew: crew_alpha · Base SHA:
`681fcea47b626c638ad65cef4078891f27a5e278` (`origin/main`, post-#487/#506).

Raw obligations transcribed verbatim from `.omni/brief.md`: the **five confirmed bugs**
under "First priority" (bugs 1–4) plus the bwrap probe (*"Fifth confirmed bug, issue #5"*,
`docs/prd/DEFINITION-OF-DONE.md` condition 5) and the **thirteen audit areas** under
"Then audit". 5 + 13 = 18, matching `tests/dod_validate.py`'s
`_TRACEABILITY_OBLIGATION_COUNT`.

## WA-5 disclosure

This crew's dispatch to a counted worker (`eng_claude`/`eng_codex`) for this task is
sterile: `sys_session_send`'s live object-form `args` schema carries only
`input`/`purpose`/`model`/`cost_budget`/`file_ids` — no `slot_id`/`task_id`/`generation` —
so the classified-dispatch metadata ADR-0042/the admission policy requires cannot be
attached mechanically, reproducing the deployment-lag defect tracked at #489 (confirmed
independently this session for #478/PR #514, #480/PR #513, #481/PR #515, and again here
by re-reading the live tool schema rather than trusting the prior confirmations alone).
Per **WA-5** (`.omni/working-agreements.md` item 5) and the program's dispatch this
run (session `f5d02a88`, run_id `fixture-review-231`), the crew_alpha lead built this
document directly, single-author, under the control-plane hold for this task (lease 76,
`docs/prd/TRACEABILITY.md`, edit mode, granted 2026-09-02 under the same evidence-rows-
only carve-out crew_beta already applied to `docs/prd/DOD-MANIFEST.json`, PR #512 —
`docs/prd/**` is `product`-owned per `docs/owners.md`, but a citation matrix is not a
revision to the DoD/PRD prose itself). In-crew review and in-crew QA did not run.
**Cross-crew review (crew_beta) is therefore mandatory, not customary, for this PR.**

**Scope discipline, matching the carve-out exactly:** every row below *cites* an id,
a file, a PR or a verdict that already exists in another document this crew or crew_beta
authored under its own ADR/review. Nothing here restates, paraphrases or extends the
wording of any DoD condition — where a citation needed interpretation, it is attributed
to the source document's own verdict, not asserted fresh.

## Caveats a reader needs before trusting any row

1. **Four cited audit documents are not yet on `main`.** `docs/audit/SURVIVAL.md` (PR
   #514), `docs/audit/BOUNDARIES.md` (PR #513), `docs/audit/INSTALL.md` (PR #515) and
   `docs/audit/FINDINGS.md` (PR #511) are open, cross-crew-review-pending PRs as of the
   base SHA above; every citation into them below was read from their PR branch tip
   (`origin/crew_alpha/478-survival-audit`, `.../480-boundaries-audit`,
   `.../481-install-audit`, `.../482-findings-audit`), not from `main`. `D14`/`D16`/
   `D17`/`D18` correctly still `FAIL "<doc> not found"` on `main` today; that is
   `tests/dod_validate.py`'s real output at this SHA, not a defect in this matrix.
2. **A known finding-id collision, not a new one.** `docs/audit/BOUNDARIES.md` (PR #513)
   uses local ids `F-1`/`F-2` for its own two `CONFIRMED-BUG`s; `docs/audit/FINDINGS.md`
   (PR #511, the global index) already allocated `F-1` to a *different* finding from
   `docs/audit/CONCURRENCY.md` (PR #502, merged) and does not yet carry BOUNDARIES's or
   INSTALL's findings at all. crew_beta's review of PR #513 already flagged this and
   deferred reconciliation to #482's own binding step once #513/#515 land; rows 13–14 and
   18 below cite BOUNDARIES's/INSTALL's *local* ids as written, not a global id that does
   not exist yet.
3. **`RT-16`–`RT-19` are allocated but structurally unsatisfied.** Per
   `docs/prd/DOD-MANIFEST.json`, each carries `"test_node_id": null` and an
   `unsatisfied_reason` of "no test class or capture exists in the tree yet" (conditions
   6 and 7). Rows below cite them as unsatisfied, not as passing evidence — `D06`/`D07`
   correctly `FAIL` on those ids today.
4. **Evidence form.** `docs/prd/DEFINITION-OF-DONE.md`'s own manifest rule (top of file)
   requires citations to be id- or PR-qualified, never a bare `path:line` ("line numbers
   drift the moment a fix lands"). Every row cites ids/PRs/doc#Q-anchors for exactly that
   reason, not `file:line` pairs.

## Summary

| # | Obligation | DoD condition(s) | Source document | PR(s) |
|---|---|---|---|---|
| 1 | Go version discovery survives `set -o pipefail` (both sites) | 1 | `deploy/provision-ubuntu.sh` | #22 |
| 2 | A missing Go toolchain means "install Go", never "stop" | 2 | `deploy/provision-ubuntu.sh` | #22 |
| 3 | Unattended user services install without assuming `systemctl --user` works | 3 | `deploy/bootstrap.sh` | #18 |
| 4 | Watchdog liveness is a Program-owned signal, validated by content | 4 | `deploy/nightshift-watchdog.sh`, `bin/nightshift-ctl` | #24, #16 |
| 5 | Bubblewrap is probed by function, not by presence | 5 | `deploy/preflight.sh` et al. | #15 |
| 6 | duplicate work | 7, 13 | `docs/audit/CONCURRENCY.md` Q1 | #502 |
| 7 | deadlocks | 13 | `docs/audit/CONCURRENCY.md` Q3/Q4 | #502 |
| 8 | task claims and leases | 7, 13 | `docs/audit/CONCURRENCY.md` Q3 | #502 |
| 9 | worker-slot correctness | 6, 13 | `docs/audit/CONCURRENCY.md` Q2/Q5 | #502 |
| 10 | crash/restart recovery | 14 | `docs/audit/SURVIVAL.md` Q1/Q2 (PR #514, open) | #514 |
| 11 | vendor rate-limit and subscription exhaustion handling | 14 | `docs/audit/SURVIVAL.md` Q3 (PR #514, open) | #514 |
| 12 | unattended operation for 3–7 days | 14 | `docs/audit/SURVIVAL.md` Q4 (PR #514, open) | #514 |
| 13 | security boundaries | 16 | `docs/audit/BOUNDARIES.md` Q1/Q2/Q3 (PR #513, open) | #513 |
| 14 | GitHub/PR/review workflow | 15 | `docs/audit/PIPELINE.md` Q1/Q3 | #497 |
| 15 | deterministic release gates | 15 | `docs/audit/PIPELINE.md` Q2 | #497 |
| 16 | test coverage | 15 | `docs/audit/PIPELINE.md` Q4 | #497 |
| 17 | unnecessary complexity | 16 | `docs/audit/BOUNDARIES.md` Q4 (PR #513, open) | #513 |
| 18 | deployment/install reliability | 17 | `docs/audit/INSTALL.md` Q1–Q4 (PR #515, open) | #515 |

## Machine-readable matrix

```json
[
  {
    "obligation": "Go version discovery survives set -o pipefail -- at both sites",
    "condition": 1,
    "questions": ["docs/audit/INSTALL.md#Q1 (root-cause re-verification of all five confirmed bugs, PR #515, open)"],
    "findings": ["none -- condition 1 PASSes at HEAD 681fcea (tests/dod_validate.py D01 PASS); no open finding"],
    "rt_ids": ["RT-01", "RT-02", "RT-23"],
    "prs": ["#22"]
  },
  {
    "obligation": "A missing Go toolchain means \"install Go\", never \"stop\"",
    "condition": 2,
    "questions": ["docs/audit/INSTALL.md#Q1 (PR #515, open)"],
    "findings": ["none -- D02 PASS at HEAD 681fcea"],
    "rt_ids": ["RT-03", "RT-04"],
    "prs": ["#22"]
  },
  {
    "obligation": "Unattended user services install without assuming systemctl --user already works",
    "condition": 3,
    "questions": ["docs/audit/INSTALL.md#Q1 (PR #515, open)"],
    "findings": ["F-171 (docs/audit/INSTALL.md Q2, PR #515, open -- a LATENT instance in deploy/bootstrap.sh's linger/user-units gate reading bare $USER; the original condition-3 bug is fixed and D03 PASSes at HEAD 681fcea, F-171 is a related but distinct new finding from the install audit, not a reopening of condition 3)"],
    "rt_ids": ["RT-05", "RT-06", "RT-07"],
    "prs": ["#18"]
  },
  {
    "obligation": "Fix watchdog liveness correctness (Program-owned signal, validated by content)",
    "condition": 4,
    "questions": ["docs/audit/INSTALL.md#Q1 (PR #515, open)"],
    "findings": ["F-171 (docs/audit/INSTALL.md Q2, PR #515, open -- named by the audit as a dependency of this bug's own enable-units gate; D04 itself PASSes at HEAD 681fcea)"],
    "rt_ids": ["RT-08", "RT-09", "RT-10", "RT-11", "RT-12", "RT-21", "RT-22"],
    "prs": ["#24", "#16"]
  },
  {
    "obligation": "Bubblewrap is probed by function, not by presence (fifth confirmed bug, issue #5)",
    "condition": 5,
    "questions": ["docs/audit/INSTALL.md#Q1 (PR #515, open)"],
    "findings": ["none -- D05 PASS at HEAD 681fcea"],
    "rt_ids": ["RT-13", "RT-14", "RT-15"],
    "prs": ["#15"]
  },
  {
    "obligation": "duplicate work",
    "condition": [7, 13],
    "questions": ["docs/audit/CONCURRENCY.md#Q1"],
    "findings": ["F-1 (docs/audit/CONCURRENCY.md, PR #502, merged -- claim/slot/lease release verbs perform an unauthenticated hold-freeing transition; global disposition in docs/audit/FINDINGS.md id F-1, PR #511, open, still OPEN)"],
    "rt_ids": ["RT-18 (condition 7, unsatisfied -- test_node_id null in docs/prd/DOD-MANIFEST.json)", "RT-20"],
    "prs": ["#502", "#16"]
  },
  {
    "obligation": "deadlocks",
    "condition": 13,
    "questions": ["docs/audit/CONCURRENCY.md#Q3", "docs/audit/CONCURRENCY.md#Q4"],
    "findings": ["F-1 (Q3 verdict CONFIRMED-BUG, same finding as duplicate work); Q4 verdict SOUND, no finding"],
    "rt_ids": ["RT-19 (condition 7, unsatisfied -- docs/audit/CONCURRENCY.md's own \"RT id follow-up\" section names it as the closest relevant id)"],
    "prs": ["#502"]
  },
  {
    "obligation": "task claims and leases",
    "condition": [7, 13],
    "questions": ["docs/audit/CONCURRENCY.md#Q3"],
    "findings": ["F-1 (docs/audit/CONCURRENCY.md, PR #502, merged; global id F-1, docs/audit/FINDINGS.md, PR #511, open, disposition OPEN, tracked at issue #503)"],
    "rt_ids": ["RT-19 (condition 7, unsatisfied)"],
    "prs": ["#502"]
  },
  {
    "obligation": "worker-slot correctness",
    "condition": [6, 13],
    "questions": ["docs/audit/CONCURRENCY.md#Q2", "docs/audit/CONCURRENCY.md#Q5"],
    "findings": ["none -- Q2 and Q5 verdicts both SOUND"],
    "rt_ids": ["RT-16 (condition 6, unsatisfied)", "RT-17 (condition 6, unsatisfied)"],
    "prs": ["#502"]
  },
  {
    "obligation": "crash/restart recovery",
    "condition": 14,
    "questions": ["docs/audit/SURVIVAL.md#Q1 (PR #514, open)", "docs/audit/SURVIVAL.md#Q2 (PR #514, open)"],
    "findings": ["none -- Q1 and Q2 verdicts both SOUND"],
    "rt_ids": ["none allocated -- SURVIVAL.md's Q1/Q2 evidence is prose plus capture, not bound to a docs/prd/DOD-MANIFEST.json RT id"],
    "prs": ["#514"]
  },
  {
    "obligation": "vendor rate-limit and subscription exhaustion handling",
    "condition": 14,
    "questions": ["docs/audit/SURVIVAL.md#Q3 (PR #514, open)"],
    "findings": ["none -- Q3 verdict SOUND, with the recorded limitation that live-vendor behaviour is unobserved while Codex seats are down (B5)"],
    "rt_ids": ["none allocated"],
    "prs": ["#514"]
  },
  {
    "obligation": "unattended operation for 3-7 days",
    "condition": 14,
    "questions": ["docs/audit/SURVIVAL.md#Q4 (PR #514, open)"],
    "findings": ["none -- Q4 verdict SOUND"],
    "rt_ids": ["none allocated"],
    "prs": ["#514"]
  },
  {
    "obligation": "security boundaries",
    "condition": 16,
    "questions": ["docs/audit/BOUNDARIES.md#Q1 (PR #513, open)", "docs/audit/BOUNDARIES.md#Q2 (PR #513, open)", "docs/audit/BOUNDARIES.md#Q3 (PR #513, open)"],
    "findings": ["F-1 (BOUNDARIES.md's own local numbering, PR #513, open -- orchestrator terminal runs unsandboxed, contradicting docs/TRUST-BOUNDARY.md's own diagram; NOT the same finding as CONCURRENCY.md's global F-1, and not yet present in docs/audit/FINDINGS.md's global index -- see caveat 2 above)", "F-2 (BOUNDARIES.md local numbering, PR #513, open -- the live GitHub credential is a classic-scoped OAuth token, not the fine-grained repo-scoped PAT docs/TRUST-BOUNDARY.md requires)"],
    "rt_ids": ["none allocated"],
    "prs": ["#513"]
  },
  {
    "obligation": "GitHub/PR/review workflow",
    "condition": 15,
    "questions": ["docs/audit/PIPELINE.md#Q1", "docs/audit/PIPELINE.md#Q3"],
    "findings": ["F-101 (Q1, docs/audit/FINDINGS.md, PR #511, open, disposition OPEN -- branch protection cannot be enabled on this GitHub plan, no replacement mechanical enforcement)", "F-102 (Q1, disposition OPEN -- self-review/different-vendor-review independence not mechanically verifiable, one shared PAT identity)", "F-103 (Q3, disposition ACCEPTED-RISK, .omni/tech-debt.md 2026-08-30 row, owner crew_beta)"],
    "rt_ids": ["none newly allocated by this audit"],
    "prs": ["#497"]
  },
  {
    "obligation": "deterministic release gates",
    "condition": 15,
    "questions": ["docs/audit/PIPELINE.md#Q2"],
    "findings": ["none -- Q2 verdict SOUND"],
    "rt_ids": ["none newly allocated"],
    "prs": ["#497"]
  },
  {
    "obligation": "test coverage",
    "condition": 15,
    "questions": ["docs/audit/PIPELINE.md#Q4"],
    "findings": ["F-104 (Q4, disposition ACCEPTED-RISK, .omni/tech-debt.md 2026-08-22 row, owner program -- sub-agent transport truncation)"],
    "rt_ids": ["RT-01..RT-23 (the coverage map itself; docs/prd/DOD-MANIFEST.json is the full enumeration Q4 cites)"],
    "prs": ["#497"]
  },
  {
    "obligation": "unnecessary complexity",
    "condition": 16,
    "questions": ["docs/audit/BOUNDARIES.md#Q4 (PR #513, open)"],
    "findings": ["none -- Q4 verdict SOUND; nothing in the brief's protected set was proposed for removal"],
    "rt_ids": ["none allocated"],
    "prs": ["#513"]
  },
  {
    "obligation": "deployment/install reliability",
    "condition": 17,
    "questions": ["docs/audit/INSTALL.md#Q1 (PR #515, open)", "docs/audit/INSTALL.md#Q2 (PR #515, open)", "docs/audit/INSTALL.md#Q3 (PR #515, open)", "docs/audit/INSTALL.md#Q4 (PR #515, open)"],
    "findings": ["F-171 (Q2, PR #515, open -- deploy/bootstrap.sh reads bare $USER at two sites under set -euo pipefail, an abort hazard on non-login invocations; latent on the one documented invocation path, reachable on others)"],
    "rt_ids": ["RT-01..RT-15 (Q1 re-verifies root cause of all five confirmed bugs, commit-qualified, against the same ids each bug's own row above cites)"],
    "prs": ["#515"]
  }
]
```
