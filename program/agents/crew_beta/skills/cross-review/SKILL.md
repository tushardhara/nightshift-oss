---
name: cross-review
description: Verify an implementer's diff against its frozen contract with an independent, different-vendor reviewer plus QA; route proven merge blockers to the same worker.
---

# cross-review — nobody signs off on their own work

The implementer never approves its own diff. A different vendor judges it, and QA
tries to break it. Both run read-mostly and report; only the implementer edits.

## Procedure

1. **Get the diff**: `sys_os_shell("gh pr diff <pr>")`, or diff against the
   repo's default branch — never assume it is `main`:
   `git -C .worktrees/<task_id> diff origin/HEAD...HEAD`
   (if `origin/HEAD` is unset, run `git remote set-head origin -a` once).

2. **Deterministic gates first.** Run tests, lint, and typecheck yourself. If
   red, send it back to the implementer to drive green before spending a
   reviewer on it. For pytest counts, get ground truth with
   `python -m pytest --collect-only -q <same files>` — never `grep -c 'def test_'`,
   which counts functions and misses parametrized expansion.

3. **Dispatch the opposite vendor's reviewer plus QA, in the same turn:**
   First acquire one classified continuation slot per target with the exact
   task, agent, title and purpose; pass each grant's `slot_id` and `generation`
   plus `task_id` in the matching send. Reviewer slots use `purpose review`;
   QA uses `purpose implement` and requires the recorded task PR.
   - Claude implemented → `beta_reviewer_codex`. Codex implemented → `beta_reviewer_claude`.
   - `sys_session_send(agent="beta_reviewer_codex", title="review-<task_slug>",
     args={purpose: "review", task_id: "<task_id>", slot_id: <id>, generation: <generation>, input: "<the diff> + <the frozen review contract>. Review ONLY against that contract. Report MERGE_BLOCKER / PRE_DEPLOYMENT / FOLLOW_UP with the evidence ADR-0026 requires. Do not edit."})`
   - `sys_session_send(agent="beta_qa", title="test-<task_slug>",
     args={purpose: "implement", task_id: "<task_id>", slot_id: <id>, generation: <generation>, input: "<criteria> + <the diff> + the test plan it wrote in phase 1 + test-only scope + the worktree path"})`
     Reuse the SAME title QA used for this task's plan, so it still has its own
     plan in context and can maintain the cases it already wrote.
     QA works in the task's worktree and COMMITS its tests to the task branch
     (push included), so they ride the same PR — it never amends, rebases, or
     reverts the implementer's commits, and the implementer's next fix-task pulls
     before pushing.

   Give the reviewer the diff as TEXT — never point it at the implementer's
   worktree, and never hand it the implementer's transcript. Then end the turn and
   collect both reports from `sys_read_inbox`.

4. **Reviewers surface; they do not fix.** Never ask a reviewer to patch what it
   found.

5. **Route `MERGE_BLOCKER` issues back safely.** For a mutable branch, acquire an
   exact task claim first, then a fresh implementer-continuation slot, and send
   the SAME agent/title with task_id, purpose, slot_id and generation. Release
   claim and slot after the result. For an ADR-0022 broker-frozen or
   post-publication Codex branch, create a superseding task/worktree/branch and
   pass NEW instead. Log each blocker, then loop to step 1.

6. **Pass condition**: gates green, zero `MERGE_BLOCKER` issues, every acceptance
   criterion covered by a test QA verified fails against the pre-change code, and
   any test case QA changed carrying its authorising ADR. Mark ready in the
   registry with the PR url; the merge belongs to the `release` integrator, after
   cross-crew review passes — never to this crew.

7. **Non-convergence**: after three loops, stop. Escalate to the program with the
   specific issues that keep reopening — a task that cannot converge is usually a
   wrong contract or a wrong ADR, not a lazy worker.

## Finding and evidence contract

- A `MERGE_BLOCKER` must cite a frozen criterion or protected correctness,
  security, data-loss, or default-branch-integrity invariant, plus the exact changed
  hunk, reachable causal path, and concrete failure scenario or safe deterministic
  reproduction. Never execute a reproduction against a live system. Without that
  proof it is not a blocker.
- `PRE_DEPLOYMENT` blocks cutover only. Pre-existing or unreachable defects and
  requests for unrelated polish, conventions, scale work, or scope expansion are
  `FOLLOW_UP`. Changed behaviour that violates an explicit exclusion can still be a
  `MERGE_BLOCKER` when the required causal proof exists.
- After round one, a new blocker must trace to changed bytes or criteria, or to a
  direct dependency the same reviewer demonstrably missed. Rechecks use the same
  reviewer, title, and frozen contract.
- Bind each pass to domain, reviewer, head SHA/tree, files, blob OIDs, dependency
  seams, and evidence. Identical blobs may reuse content review only when their
  seams are unchanged. CI, provenance, PR-body, authorization, and live/runtime
  evidence are always rerun.

## Notes

- Cross-review needs two available vendors. If the preflight found only one,
  report to the program that independent cross-vendor review is unavailable and
  put it in `STATUS.md`; the program decides whether the wave proceeds and records
  it in `.omni/decisions.md`. Never dispatch a same-vendor reviewer and call it
  independent.
- `PRE_DEPLOYMENT` goes to the cutover plan. `FOLLOW_UP` goes to the registry as a
  separate work item. Neither starts another source-review loop.
- Reviewers are read-only by policy, so a stray reviewer edit can never reach the
  deliverable. That is deliberate — do not "fix" it by loosening the policy.
- Both reviewer and QA dispatches are cheap relative to an implementer.
  When budget is tight, cut fan-out width, not verification.
