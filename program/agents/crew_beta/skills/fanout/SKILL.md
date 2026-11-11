---
name: fanout
description: Run parallel-safe subtasks — one git worktree and one engineer per task, each cut from the wave checkpoint and holding a control-plane slot, each opening its own PR — then send every PR through in-crew cross-review, the QA gate, and cross-crew review.
---

# fanout — parallel work without collisions

Use ONLY for subtasks that are parallel-safe: disjoint file scopes, no ordering
dependency. Anything sharing a file goes sequentially in one worktree.

The control plane (`nightshift-ctl`) is part of this procedure, not decoration: the
slot is what keeps N crews inside one global budget (the policy layer denies a
dispatch without one), and the checkpoint SHA is what guarantees the worker
sees the exact spec it was contracted against.

## Procedure

1. **Worktree per task, cut from the checkpoint.** Your charter names the
   wave's `base_sha` (re-read it with `nightshift-ctl checkpoint latest`). Then:
   `sys_os_shell("git worktree add .worktrees/<task_id> -b <crew>/<task_id> <base_sha>")`.
   Never create a worktree from bare HEAD — HEAD moves while a wave is in
   flight, and two workers contracted against different spec versions is a
   bug no reviewer can see. Record worktree, branch and base_sha in
   `.omni/registry-<crew>.json`, and mirror the row:
   `nightshift-ctl task upsert --id <task_id> --crew <crew> --agent <agent>
   --title <task_slug> --issue <n> --base-sha <sha> --state pending --size <S|M>
   --worktree .worktrees/<task_id> --branch <crew>/<task_id>`.

2. **Slot, then dispatch — one engineer per task**, alternating vendors across
   the wave so neither subscription carries the whole burn:

   ```
   nightshift-ctl slot acquire --holder <crew>/<agent> --crew <crew> --task <task_id> \
     --agent <agent> --title <task_slug> --purpose implement
   # exit 3 → budget full: queue this task, dispatch nothing, move on
   sys_session_send(agent="beta_eng_claude"|"beta_eng_codex", title="<task_slug>",
     args={purpose: "implement", task_id: "<task_id>", input: "<contract>",
           slot_id: <granted id>, generation: <granted generation>,
           cost_budget: {max_cost_usd: 67}})
   ```

   The contract in `input` must carry: the task, its worktree path and
   base_sha, the ADR id, the acceptance criteria it must satisfy, the exact
   green gates (test, lint, typecheck commands), the file scope it must not
   exceed, the GitHub issue number, and the PR requirement — body says
   `Refs #<n>`, never a closing keyword; it uses the six-field summary from
   `docs/HUMAN-COMMUNICATION.md` before technical evidence; and the PR is opened through
   `nightshift-ctl pr-create -- <gh args>` (lints the real body, then calls gh;
   raw `gh pr create --body-file/--fill` is policy-denied). A vague contract
   produces a diff nobody can review.

   Emit the worktree, slot and dispatch calls in THIS turn — never end a turn
   having only said you will dispatch. Dispatch every parallel-safe task the
   semaphore grants a slot for, then end the turn. Do not poll.

3. **Collect** with `sys_read_inbox` as workers finish. For each result:
   release its slot (`nightshift-ctl slot release --slot <id>`), record the PR url
   and conversation id in the registry, and update the control plane
   (`nightshift-ctl task upsert --id <task_id> --state ready --pr-url <url>`). Use
   `sys_session_get_history` only when a result is empty or unclear.

4. **Gate every PR**: run the deterministic gates yourself, then dispatch
   `cross-review` (different vendor from the implementer) and `beta_qa` on it —
   each of those dispatches takes a slot too. Both are unconditional
   (ADR-0042 changes neither) and both must come back clean.

   Then check the SAME `suggested_domains` result Phase 3 computed for this
   exact head SHA (reuse it, do not recompute — a second call risks reading a
   different, now-stale head). If it includes `cross_crew`, tell the program
   it is ready for cross-crew review. FAIL-CLOSED DEFAULT: if that
   `suggested_domains` result was missing, stale, or malformed for this head,
   or ADR-0042's activation is off, treat cross-crew review as required
   regardless, exactly as if ADR-0042 did not exist, and log the fallback
   reason (`suggestion_missing` / `suggestion_stale` / `suggestion_malformed` /
   `activation_disabled`) the same way config.yaml's Phase 4 does.

5. **Close.** The PR is your deliverable and the integrator's input — you never
   merge, and neither does any crew. Mark the task ready in the registry with
   its PR url and ADR id. Remove a worktree (`git worktree remove`) only once
   its PR is open and review is clean, and never while fix-tasks are
   outstanding.

## Notes

- Respect the per-turn dispatch cap (policy-enforced). The worker budget is
  also policy-enforced now — a denied dispatch names what was missing. Neither
  is negotiable from inside a crew.
- Keep parallel tasks' file scopes disjoint. Because nobody merges here,
  conflicts surface at the integrator's merge — disjoint scope is what keeps
  that rare, and the integrator drops the offender rather than resolving it
  for you.
- A runaway or superseded worker: `sys_cancel_task` with its recorded
  `conversation_id` before dispatching a replacement, then release its slot.
- A worker that returns dark or failed: CLAIM FIRST —
  `nightshift-ctl claim acquire --task <task_id> --claimant <crew>/lead --agent
  <agent> --title <task_slug> --reason "no result behind building row"`.
  Exit 3 means a program heartbeat already owns this recovery: stand down.
  On a grant: drain the inbox once more, acquire a fresh classified slot with
  that same task/agent/title and `--purpose implement`, then re-dispatch to the
  SAME (agent, title) with task_id, slot_id, generation and purpose — it resumes
  that worker's conversation and worktree — and
  release the claim when the result lands. Never open a fresh title for a
  claimed task: two workers on one task write conflicting diffs to one branch.
- A worker that reports a rate limit or `usageLimited`: record it —
  `nightshift-ctl vendor limited --vendor <claude|codex>` — move the task to the
  other vendor, and tell the program which vendor is cooling off.
- Every mutable in-place fix CLAIMS FIRST, then goes to the SAME agent + title
  with a fresh implementer-continuation slot. For an ADR-0022 broker-frozen or
  post-publication Codex branch, create a superseding task/worktree/branch and
  use NEW instead. A new title on the old task is never recovery evidence.
