# What is actually enforced

Read this before building a guardrail.

Three tickets in one session — #722, #746 and half of #743 — were closed after
the work had started, because the thing they proposed to enforce was already
enforced, or could not be. That discovery cost more than the building would
have. This page exists so the next person spends minutes instead of hours.

Every row was produced by **running the policy**, not by reading it. Re-run the
probes at the bottom if you doubt a row; a page like this is worth nothing the
moment it is trusted rather than checked.

## Enforced by a root guardrail

These are registered in `program/config.yaml` under `guardrails.policies`. Only
root registrations govern — per-seat policy blocks are dead config (#118).

| Rule | Mechanism | Measured |
|---|---|---|
| A worker dispatch needs a live slot | `worker_semaphore` | named send to a counted seat with no slot → **DENY** |
| Opaque `session_id` continuations are not a dispatch form | `worker_semaphore` | → **DENY** (also with `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1`) |
| `sys_session_create` in any form | `worker_semaphore` | → **DENY** |
| One Program generation acts at a time | `program_authority_fence` | dispatch with no permit → **DENY** |
| No merging into a repository we do not own | `external_repo_merge_fence` | `gh pr merge` → **DENY** under tool names `Bash` and `bash`; fails closed, no escape hatch |
| No triggering GitHub Actions | `github_actions_fence` | `gh workflow run`, `gh run rerun`, workflow/repository dispatch → **DENY** under `Bash` and `bash` |
| Issue writes go only to the declared tracker | `tracker_issue_write_fence` | `gh issue create -R other/repo` → **DENY** under `Bash` and `bash` |

**Measure with the tool name the seat actually reports.** claude-native seats
report their shell tool as `Bash`; other harnesses report `bash`, `shell`,
`Shell`, `terminal`, `developer__shell`, `run_command` or `run_shell_command`.
Earlier versions of these rows were measured with lowercase `bash` only, and
were wrong for claude-native seats: the three shell fences matched
`("sys_os_shell", "shell", "bash")`, so `gh workflow run`, `gh pr merge` and
an off-tracker `gh issue create` sent as `Bash` were **ALLOWed**. The fences
now share one casefolded name set, omnigent's `SHELL_TOOLS` plus every
harness's shell tool, and the probes below send `Bash`.

Also registered and not probed here:
`release_provenance`, `spawn_bounds`, `blast_radius`, `session_downgrade_gate`,
`daily_downgrade_gate`.

## Enforced somewhere other than a policy

| Rule | Where |
|---|---|
| A published commit must be a direct **child** of `tasks.base_sha` | `nightshift_publish.py` — a rebase or one extra commit fails it |
| Every published path must be named in the contract's `allowed_paths` | `nightshift_publish.py` — exact match, so a directory entry admits nothing |
| A merge needs the review on the record | `review eligibility`, run by `release` |
| A filed finding cannot be weakened or deleted | #734, at the DELETE site |
| New prose must not exceed the repository's own comment density by 2× | `tests/comment_density_guard.py`, in CI — exit 1 on refusal |
| Source may not be authored by the coordinator | `tests/authorship_guard.py`, in CI |
| A preflight message must have a `DOD-MANIFEST.json` row | `test_729_invocation_guardrail`, both directions |
| No toolchain names in the org's own source or seat configs | `test_core_portability_guardrail`, `test_seat_contract` |
| Initialising a repository must not modify a tracked file | `tests/test_744_init_footprint.py` |

## NOT enforced — convention only

Say "convention", not "rule", when writing about these.

| Believed rule | Reality | Measured |
|---|---|---|
| Only the integrator writes to `main` | Convention **in our own repo**. No policy carries caller identity, and branch protection is unavailable on this plan (#743). Enforced on a *borrowed* repo by `external_repo_merge_fence`. | — |
| Nothing edits `.github/` | Only *running* Actions is fenced. | `echo x > .github/workflows/tests.yml` → **ALLOW** |
| The authority fence judges every dispatch | An `agent`-less continuation is waved through — but `worker_semaphore` denies it first, which is why #722 was not reproducible. | agent-less continuation → **ALLOW** |
| A seat is who it says it is | `claim acquire --claimant` and `task upsert` fields are caller-declared. All seats share one machine, one OS user, one control plane, so no CLI-time credential can distinguish them (#725). | — |
| The org reads its own pull requests | Only what `release` is *told* to read (#745). Prose in a prompt; nothing executes it. | — |

## Two rules for adding a guardrail

**Check this page first, then check it again by running something.** #722 proposed
gating a channel ADR-0033 had already closed three ADRs earlier. #746 proposed
binding a base commit that `nightshift_publish` already binds.

**If the thing you are checking is supplied by the party you are checking, you
have built nothing.** #721 gated on a `purpose` string the caller writes. #746
would have compared `tasks.base_sha` against a worktree derived from it. Both
pass their tests and refuse nothing.

## Re-running the probes

```
cd <repo> && PYTHONPATH=deploy/policies python3 - <<'EOF'
import sys; sys.path.insert(0, 'deploy/policies')
import omnipods_policies as P
from nightshift_actions_fence import github_actions_fence
from nightshift_issue_fence import tracker_issue_write_fence
# `Bash` is what a claude-native seat reports; repeat with "bash" for the rest.
sh = lambda c: {"type":"tool_call","data":{"name":"Bash","arguments":{"command":c}}}
send = lambda a: {"type":"tool_call","data":{"name":"sys_session_send","arguments":a}}
print(P.worker_semaphore(db=".omni/control.db")(send({"session_id":"s","args":{}})))
print(P.program_authority_fence(db=".omni/control.db")(send({"session_id":"s","args":{}})))
print(P.external_repo_merge_fence()(sh("gh pr merge 1 --merge")))
print(github_actions_fence()(sh("gh workflow run deploy.yml")))
print(tracker_issue_write_fence()(sh("gh issue create -R other/repo -t x")))
print(github_actions_fence()(sh("echo x > .github/workflows/tests.yml")))
EOF
```
