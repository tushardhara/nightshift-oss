# #118 — per-seat policies: live probe result

Date: 2026-08-24 · Seat: `platform`, worker slot 146 · Wave 6
Base: `program/discovery-state` @ `fe096632f882e528f54b2a0dfbfe01a70ba165e1`
Harness: `tests/probe_per_seat_policy.py` · Raw result: `.omni/evidence/118-per-seat-policy-probe.json`

## Verdict

**CONFIRMED.** A policy declared on a seat never reaches the policy engine.
The same policy declared at the bundle root does, and applies to that seat's
sessions. Per-seat `guardrails.policies` in `program/**` is dead config.

`security` reached this conclusion on #118 by reading the code path end to end
and said so — correctly — as an unconfirmed read. This is the execution that
was owed. It agrees with the read in full, and adds one thing the read could
not establish: the failure is **not** a parse, schema or registration failure.
The seat's policy is parsed, is valid, and is sitting on the very spec object
the engine is handed. It is simply never traversed.

## What was run

A scratch `omnigent server` — own ephemeral port, own SQLite DB, own artifact
directory, own bundles, all under one temp dir removed on exit. Three bundles
registered with `--agent` at startup (the only filesystem→server registration
path on this box, ADR-0010/#113):

| bundle | root declares | seat declares |
|---|---|---|
| `probe_seatonly` | *nothing* | `ro_seat`: `read_only_os` |
| `probe_rootonly` | `read_only_os` | `plain_seat`: *nothing* |
| `probe_none` | *nothing* | `plain_seat`: *nothing* |

The `read_only_os` block is **byte-identical** between the experiment and its
positive control, modulo nothing — same builtin handler, same `type: function`
shape, same arguments. Only its *placement* differs. That equality is asserted
in `tests/test_probe_per_seat_policy.py`.

Each case creates a parent session and then a real sub-agent session
(`POST /sessions` with `parent_session_id` + `sub_agent_name`). The server
validates the name against the bundle's declared sub-agents before persisting
the row (`orchestration.py:7689` → `_require_declared_subagent`), so these are
genuine seat sessions, created the way a real seat is created — not a
look-alike.

Two enforcement paths were exercised per session, both of the paths a real
seat's write travels:

- **A.** `POST /sessions/{id}/policies/evaluate`, `PHASE_TOOL_CALL`,
  `sys_os_write` — the request-phase gate the native harness hooks call.
- **B.** `POST /sessions/{id}/mcp`, `tools/call sys_os_write` — the MCP proxy.
  The server owns TOOL_CALL policy and only then delegates execution to the
  runner.

## Results

| case | bundle | seat | evaluate (A) | mcp tools/call (B) | outcome |
|---|---|---|---|---|---|
| **EXPERIMENT** seat policy | `probe_seatonly` | `ro_seat` | `POLICY_ACTION_ALLOW` | `No runner bound…` | **ALLOWED** |
| **CONTROL +** root policy | `probe_rootonly` | `plain_seat` | `POLICY_ACTION_DENY`, reason `PROBE118 read_only_os fired` | `Denied by policy: PROBE118 read_only_os fired` | **DENIED** |
| **CONTROL −** no policy | `probe_none` | `plain_seat` | `POLICY_ACTION_ALLOW` | `No runner bound…` | **ALLOWED** |
| CONTROL root session | `probe_seatonly` | *(root)* | `POLICY_ACTION_ALLOW` | `No runner bound…` | **ALLOWED** |

Both controls behaved, in the same run, on the same two code paths, against
the same server process. The positive control is what makes the experiment
readable: a DENY was observable, on both paths, and the seat-declared policy
did not produce one.

### The positive control says more than "the harness works"

`CONTROL +` is itself a **seat** session (`sub_agent_name: plain_seat`) whose
bundle declares `read_only_os` at the root — and it was DENIED. So the finding
is not "seat sessions have no policies". It is sharper:

> A seat session is governed by the root spec's policies, and **only** by
> the root spec's policies. Its own block is inert.

### It is not a parse failure

The probe loads each bundle with the production parser (`omnigent.spec.load`,
the same entry point `cli._preregister_agent` and `AgentCache.load` use) and
reports where the policies landed:

```json
"probe_seatonly": { "root_policies": [], "seat_policies": { "ro_seat": ["report_only"] } },
"probe_rootonly": { "root_policies": ["report_only"], "seat_policies": { "plain_seat": [] } }
```

The seat's `report_only` is present, parsed and valid, on
`spec.sub_agents[i].guardrails.policies` — and the engine still allowed the
write. `spec/parser.py:287` recursively parses each `agents/<seat>/` into a
full `AgentSpec`, so the block survives all the way into memory. The engine
then reads `spec.guardrails` and nothing else
(`runtime/policies/builder.py:358-359`). There is no traversal to fail loudly;
there is no traversal at all.

## Re-verified read chain

Every link `security` cited was re-read at this base and holds:

- `server/routes/_sessions/orchestration.py:7944-7952` — the seat session is
  created with the **parent bundle's** `agent_id`; the seat identity is stored
  only as `sub_agent_name`.
- `server/routes/sessions/routes_hooks.py:733,741` — the hook loads the spec by
  `conv.agent_id`. `sub_agent_name` appears **zero** times in that file
  (`grep -c` → `0`).
- `runtime/policies/builder.py:358-359` —
  `agent_policy_specs = list(guardrails.policies or [])` off `spec.guardrails`;
  `grep sub_agent` in that file returns one hit, an unrelated comment at :1220.
- `server/routes/_sessions/helpers.py:1746-1751` — the one place
  `sub_agent_name` is consulted picks the harness name **for display only**.
- `runner/policy.py:151` — reached only from `runner/app.py:9833`, the
  synthetic `sys_agent_start` gate. Not a second enforcement point.

Two links the read did not name, checked here because they are the remaining
places a per-seat policy could have entered:

- `runtime/policies/builder.py:250` — `any_policies_apply`, the fast-path
  guard, tests `spec.guardrails and spec.guardrails.policies` only. A bundle
  whose *only* policies are per-seat short-circuits before the engine is even
  built.
- `server/routes/_sessions/helpers.py:6833` — `_load_agent_spec_for_session`,
  which feeds the other two enforcement sites (`:6951`, `:7361`), loads by
  `conv.agent_id` and returns the root `.spec`. Same blindness.

## What this means for `program/**` as it stands at `fe09663`

Enumerated with the production parser over the repo bundle:

**Root (`program/config.yaml`) — the only policies in force anywhere:**
`blast_radius`, `daily_downgrade_gate`, `session_downgrade_gate`, `spawn_bounds`.

**Dead: 44 per-seat policy declarations across 20 of 22 seats.** By handler:

| declared as | handler | seats | what the org believed |
|---|---|---|---|
| `report_only` | `read_only_os` | 5 — `security`, and `reviewer_claude` / `reviewer_codex` on both crews | reviewers and the security seat cannot write files |
| `blast_radius` | `blast_radius` | 13 | per-seat blast radius, tighter than root |
| `stuck_detector` | `context.detect_thrashing` | 8 | a thrashing worker is DENIED at 5 consecutive errors |
| `pr_close_keyword_guard` | `omnipods_policies.pr_close_keyword_guard` | 5 | `Closes #N` denied at the tool-call layer (#79/#83) |
| `stay_in_workspace` | `working_dir.block_working_dir_changes` | 2 crew leads | `cd` blocked on crew leads |
| `spawn_bounds` | `spawn_bounds` | 2 crew leads | per-crew fan-out cap |
| `worker_semaphore` | `omnipods_policies.worker_semaphore` | 2 crew leads | the global worker budget is enforced at the tool-call layer |
| `crew_downgrade_gate` / `cost_gate` | `cost.cost_budget` | 4 | per-crew and per-seat cost caps |

The **`blast_radius` floor** is the one nuance. It is not inert: the root block
loads and — per `CONTROL +` — applies to every seat session. What is dead is
every seat's *own* block, so the root's arguments govern uniformly and no seat
is tighter than root. On the running configuration that floor is the *looser*
of the two values, because the served bundle carries `gate_pushes: false`
(#115, ADR-0011).

`worker_semaphore` and `pr_close_keyword_guard` being inert is worth calling
out separately: `deploy/README.md:24` states both are "DENIED at the tool-call
layer, not remembered by prompts". They are declared on crew leads and workers
only. Neither is at root. Both are prompt discipline today.

## Incidental finding, not chased

`omnigent.spec.load(program/)` **refuses the repo bundle** at this base:

```
invalid agent spec: sub_agents['eng_claude']: duplicate sub-agent name
'eng_claude' across the spec tree; … (6 names)
```

`crew_alpha` and `crew_beta` declare identically-named seats, and validation
rejects duplicates across the whole tree. `_preregister_agent` calls the strict
`load()`, so a `--agent program/` registration of the tip would fail outright,
while `AgentCache.load` passes `prune_invalid_sub_agents=True` and would
silently drop them. That is a separate defect on the deploy path and it is not
this issue; the enumeration above uses `spec.parser.parse` to bypass the
validator. **Filed as a follow-up, not fixed here.**

## Safety

- Live server pid `3525266` (port 6767, Postgres) was never contacted,
  restarted, signalled or re-registered. Verified up 7h58m after the final run.
- The probe snapshots every pre-existing `omnigent server` pid at startup and
  **asserts** any pid it signals is not among them (`stop_server`); it signals
  only its own process group. Recorded in the result JSON as
  `live_pids_untouched: [3525265, 3525266]`.
- Own port (ephemeral), own `sqlite:///` DB, own artifact dir, own bundles, all
  under a temp dir removed on exit. `OMNIGENT_DATABASE_URI` and
  `NIGHTSHIFT_CTL_DB` are stripped from the scratch server's environment so it
  cannot inherit a pointer to live state.
- No served-bundle or cache file was read, edited, moved or deleted.
  `/var/tmp/nightshift-incident-115/` was not touched.
- `/srv/project` HEAD unmoved at `27f3915`; all work in a worktree cut from
  `fe09663`.

## Limit of the evidence, stated plainly

No byte was written to disk by the seat. With no runner bound to the scratch
server, an ALLOWED `sys_os_write` ends at the delegation step — a step the
server reaches **only after** the TOOL_CALL policy has passed. What is proven
is that **the policy layer did not object**, on both enforcement paths, while
objecting to the identical policy at root in the same run. That is exactly the
control the org documents and exactly the control that is absent. Binding a
runner would have meant standing up a second host daemon against shared
`~/.omnigent/` state next to the live one, which is not worth the risk to turn
a proven ALLOW into a written file. `tests/test_probe_per_seat_policy.py`
pins the rule that a post-policy transport error is never scored as a denial.
