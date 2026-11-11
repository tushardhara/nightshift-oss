---
name: nightshift
description: Drive a Nightshift run against a repository — preflight the fences, launch the program on a pty, observe the seats correctly, and stop safely. Use when asked to run Nightshift on a ticket, check what a run is doing, restart the omnigent server or host, or diagnose why a dispatch, publish or fence is not behaving. Also use before concluding that any part of Nightshift is broken.
---

# Driving Nightshift

Nightshift is an agent org that works a ticket end to end: reads the issue, cuts a
worktree, builds, opens a PR, reads CI. It runs under omnigent, on a VPS or locally.

**The single most important rule in this file:** four separate sessions have
concluded "dispatch is permanently blocked" from signals none of them validated.
All four were wrong. Before reporting anything as broken, check §5.

---

## 1. Where things are

| | |
|---|---|
| VPS | `ssh <admin-user>@<vps-host>`, then `sudo -iu nightshift <cmd>` |
| Bundle (VPS) | `/srv/nightshift` — agents, skills, policies, deploy |
| Project (VPS) | `/srv/project` — the repo the crews build. **This is what the server is bound to**, not `/srv/nightshift`. |
| Bundle (Mac) | `/home/operator/testbeds/engine/nightshift` |
| omnigent server | `127.0.0.1:6767` — find with `ss -lptnH 'sport = :6767'` |
| omnigent host | **separate process** — `pgrep -af 'omnigent host'` |
| State root | `$NIGHTSHIFT_STATE_ROOT`, must resolve **outside** every repo worktree |
| Helper scripts | `/home/nightshift/restart-omnigent-server.sh`, `start-728-run.sh`, `crew-snapshot.py` |

Both processes cache their Python imports at boot. See §5.

---

## 2. Preflight — in this order, stop on the first failure

```bash
# a. server healthy
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6767/health   # want 200

# b. ownership declared for the target repo, and merging refused if external
python3 bin/nightshift_authority.py ownership-show \
  --state-root "$NIGHTSHIFT_STATE_ROOT" --repository-id <GITHUB_NUMERIC_ID>
python3 bin/nightshift_authority.py merge-preflight \
  --state-root "$NIGHTSHIFT_STATE_ROOT" --repository-id <GITHUB_NUMERIC_ID>
# exit 3 + merge_forbidden_external_repository == correct for a customer repo

# c. the customer tree is clean BEFORE you start, and you record the SHA
git -C <project> status --porcelain     # must be empty
git -C <project> rev-parse HEAD
```

Get the repository id with `gh api repos/<owner>/<repo> -q .id`.

`ownership-declare --ownership external` is **write-once**. Declaring external is
always the safe direction; the default with no declaration is already external.

**A fence verified from the shell is not verified.** See §5.

---

## 3. Launching

`omnigent run` is an interactive REPL. With stdin from `/dev/null` it prints
`Warning: Input is not a terminal (fd=0)` and exits immediately. Give it a pty:

```bash
tmux new-session -d -s ns -x 200 -y 50 -c <project> \
  "env PYTHONPATH=/srv/nightshift/deploy/policies \
       NIGHTSHIFT_STATE_ROOT=<state-root> \
       NIGHTSHIFT_REPOSITORY_ID=<repo-id> \
       HARNESS_TURN_TIMEOUT_S=2700 \
       HARNESS_TURN_ABSOLUTE_TIMEOUT_S=14400 \
       omnigent run /srv/nightshift/program --server http://127.0.0.1:6767"
```

**Both timeouts are mandatory for any repo with slow CI.** Defaults are 600s idle
and 3600s absolute (`omnigent/runtime/harnesses/_scaffold.py:123`, `:130`). An
agent legitimately waiting on a CI matrix emits no events and gets killed, with the
log blaming *"a wedged LLM or tool call"*. A 15-shard Cypress run outlasts both.

Wait for `program · ready` in the pane before sending anything. Then
`tmux send-keys -t ns "<message>"`, `sleep 3`, `tmux send-keys -t ns Enter`.

The brief the program reads is `<project>/.omni/brief.md`. That is the real input;
the kickoff message just points at it.

---

## 4. Working on a repo Nightshift does not own

The customer tree must be byte-identical when you finish. `bin/nightshift-init.sh`
writes `.omni/` plus several `docs/` files and appends to the tracked `.gitignore`.
Compensate by hand:

```bash
git -C <project> hash-object .gitignore          # record before
/srv/nightshift/bin/nightshift-init.sh <project>
git -C <project> checkout -- .gitignore          # revert the tracked change
printf '\n.omni/\n.worktrees/\n' >> <project>/.git/info/exclude   # local mask only
git -C <project> status --porcelain              # MUST be empty
```

Build in `<project>/.worktrees/<task>`, never in the shared checkout.

**The publish contract cannot be written in advance.** `allowed_paths` is an exact
file list — no globs, no prefixes (`nightshift_publish.py:751` does
`normalized.issubset(set(allowed))`). Generate `.omni/contracts/<task_id>.json`
from `git diff --name-only` at publish time, then
`nightshift-ctl task upsert --id <task> --spec-ref <task>.json`.

---

## 5. Observing — and the four ways to be fooled

### Seats are real. Query them correctly.

```bash
# WRONG — kind defaults to "default", which EXCLUDES sub-agent sessions
curl -s 'http://127.0.0.1:6767/v1/sessions?limit=1000'

# RIGHT
curl -s 'http://127.0.0.1:6767/v1/sessions?limit=1000&kind=sub_agent'
curl -s 'http://127.0.0.1:6767/v1/sessions/<id>'   # -> real agent_name, sub_agent_name, parent_session_id
```

`agent_name` in the **list** view is the agent row, and seat sessions carry the
parent's `agent_id` — so the list says `program` whether seats work perfectly or
not at all. It cannot distinguish those states. Use the detail endpoint.

### `Sub-agent 'X' not found under spec 'X'` is ~98% noise

The resolver is not idempotent; once the runner narrows its cache to the child,
every later turn re-applies the same name and correctly misses.

```bash
# noise (same name both sides)
grep -rhE "Sub-agent '([a-z_]+)' not found under spec '\1'" ~/.omnigent/logs/runner/ | wc -l
# the real hazard
grep -rh "falling back to the parent spec" ~/.omnigent/logs/runner/ | wc -l
```

### A module change is not served until the owning process restarts

Server and host cache imports **independently**. On 2026-09-05 the server served a
policy bundle 1254 commits stale for four days — every fence inert — while three
filesystem-level checks (reading source, running the CLI, running unit suites) all
passed. After any change under `deploy/policies/` or omnigent's `site-packages`:

```bash
kill $(ss -lptnH 'sport = :6767' | grep -oE 'pid=[0-9]+' | cut -d= -f2)
kill $(pgrep -f 'omnigent host')
/home/nightshift/restart-omnigent-server.sh
```

The host is the one people forget, and its symptom is identical to the patch
having failed.

### Success can be a lie

A dispatch that returns success without producing a session, a mutation that broke
compilation rather than the logic, a test that passed because everything skipped —
all read as green. Prove the thing itself: a session id, a sha256 that changed, a
count of pass/fail/skip/error.

---

## 6. Guards that must hold

- **Never cause a GitHub Actions run.** No `gh workflow run|enable|disable`, no
  `gh run rerun|cancel`, no `workflow_dispatch` or `repository_dispatch`, never
  edit `.github/workflows/`. Reading is fine:
  `gh api repos/<o>/<r>/commits/<sha>/check-runs`. Fenced at the tool layer by
  `deploy/policies/nightshift_actions_fence.py`.
  **Watch for the indirect route:** on some repos a PR comment triggers a workflow
  (`vision-ai-review.yml` fires on `issue_comment`), so posting findings to a PR
  *causes* a run. Write to local files instead.
- **Never merge into a repo Nightshift does not own.** `merge-preflight` exits 3.
  Leave the PR open, record the task `delivered`, never `integrated`.
- **Never** tag, push to a default branch, force-push, or rewrite published history.
- **Don't trust a red you have not read.** Open the PR's real check list before
  acting on any failure.
- **`skipped` and `neutral` are not failures.** Path-filtered monorepos skip jobs
  routinely.

---

## 7. Reporting

Counts, never a colour. `pass / fail / skip / error` from `go test -json` events or
equivalent, not from the word "success". A suite that passes because everything
skipped is a failure.

If anything correctness-critical was touched, mutation-test it: inject the defect,
name the test that catches it, revert — and prove with sha256 that the mutant
actually applied. **Check the mutant did not simply break compilation**; a build
failure produces zero test failures and reads exactly like a surviving mutant.

Say plainly whether the review was independent. A seat reviewing its own work is
not a second opinion, and should be labelled as such.

---

## 8. Stop and hand back

A fence that errors rather than denies · any need to run or re-run an Action ·
lint red after two attempts · a publish refusal that is not
`path-outside-allowlist` · a dispatch that returns success without producing a
session · the customer tree dirty in a way you did not create.

Report the verbatim error. Do not paraphrase it, and do not work around it.
