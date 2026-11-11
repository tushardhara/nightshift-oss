# Security boundaries and unnecessary complexity audit — Q1–Q4 (DoD condition 16, issue #480)

Date: 2026-09-01 · Crew: crew_alpha · Issues: #480 (this doc), #423/#475 (parent split),
#482 (FINDINGS.md, in progress this same batch under a separate task/lease — not created by
this task), #486/D20 (`sentinel_test_node`, blocked on this doc's sink corpus — see Q1) · Base/
audit SHA: `662600662ee17267aed75bb945556f9a40ae0e42` (checkpoint 6626006; this audit made no
code changes; every permalink below is against this exact commit).

Raw capture: [`BOUNDARIES.capture.txt`](BOUNDARIES.capture.txt) — every command cited below as
`"type": "command"` evidence appears there verbatim, with its exact invocation and full
stdout/stderr. **This audit never prints the contents of a credential file** — every command
that touches a credential path uses `ls -la` (name/permission/size metadata only) or a
masked-output tool (`gh auth status`, which prints a token prefix and its scopes, never the
token itself). No secret value appears anywhere in this document, the capture file, or the PR
built from them.

## WA-5 disclosure

This crew's dispatch to a counted worker on this exact task reproduces the live deployment-lag
defect tracked at #489 (`sys_session_send`'s object-form `args` schema cannot carry the
`slot_id`/`task_id`/`generation` fields the root dispatch policy `worker_semaphore` requires for
any `COUNTED_AGENTS` target — confirmed live on issue #474/PR #487, and reproduced again this
session for #477's sibling audit task), compounded by #490 (the one documented refresh
mechanism, `nightshift-release prepare`, cannot re-run on this host because the legacy
supervision units it requires are masked-by-design after the prior successful cutover, and
unmasking does not restore them — `LoadState` goes to `not-found`, not `loaded`). Per **WA-5**
(`.omni/working-agreements.md` item 5, issue #13) and its owner-directed invocation for this
item (run_id `fixture-review-231`), the crew_alpha lead built this document directly,
single-author, under the control-plane hold recorded for this task (lease 67,
`docs/audit/BOUNDARIES.md`, edit mode, note "WA-5 build (owner-directed parallel batch, cause
\#489/\#490)" — `crew_alpha` is itself in `EXEMPT_AGENTS`, so no counted-worker `slot acquire`
applies to the lead's own build, matching the standing precedent recorded for #217/#286/#381/
#392/#477/etc. in `.omni/ledger.md`/`.omni/decisions.md`/`.omni/registry-crew_alpha.json`).
In-crew review and in-crew QA did not run. **Cross-crew review (crew_beta) is therefore
mandatory, not customary, for this PR.**

Scope, matching #477's own restated boundary and #480's own issue text: audit and cite evidence
only. No code or config changes, including to `program/agents/**` (owned by `platform`, requires
an ADR per `docs/owners.md:22` — out of this crew's scope even where this audit finds a real
gap there). Any `CONFIRMED-BUG` below is reported, not remediated.

## Summary

| Q | Verdict | Findings |
|---|---|---|
| Q1 — credential sink inventory, six named source→sink paths, with a stable id and verdict each | SOUND | — |
| Q2 — every claim in `docs/TRUST-BOUNDARY.md` verified against the code and the live host | **CONFIRMED-BUG** | F-1, F-2 |
| Q3 — can a worker escape its declared write scope by a documented path? | **CONFIRMED-BUG** | F-1 |
| Q4 — which controls are redundant and removable, which complexity is load-bearing | SOUND | — |

```json
[
  {
    "id": "Q1",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "deploy/nightshift-scheduled-run-detector.py", "symbol": "_alert_body (templated fields only; never interpolates _assistant_texts)", "line": 236, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-scheduled-run-detector.py", "symbol": "\"Raw transcript/error text is intentionally excluded.\"", "line": 254, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-scheduled-run-detector.py", "symbol": "_assistant_texts (reads Omnigent API transcript text for classification only, truncated 16384 chars, never returned in the alert)", "line": 161, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-scheduled-run-detector.py", "symbol": "_deliver (writes body to a mode-0600 tempfile, --body-file, unlinked in a finally block)", "line": 259, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-watchdog.sh", "symbol": "body= (templated from shell variables: headline/reason_label/threshold/service states; no raw env or command-output interpolation)", "line": 700, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/nightshift-watchdog.sh", "symbol": "gh issue comment \"$issue\" --body \"$body\"", "line": 755, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/systemd/omnigent-server.service", "symbol": "EnvironmentFile=__HOME__/.config/omnigent/server.env (secret injected via file, never an inline ExecStart argument)", "line": 13, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/systemd/nightshift-scheduled-run-detector.service", "symbol": "EnvironmentFile=__HOME__/.config/omnigent/scheduled-run-detector.env", "line": 10, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": ".github/workflows/live-api-smoke.yml", "symbol": "env: GH_TOKEN: ${{ secrets.GITHUB_TOKEN }} (only secret used in CI; passed as an env var, no set -x/-eux anywhere in the workflow tree)", "line": 34, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "\"3. The omnigent server/host processes themselves, and the Postgres they use for session state (loopback-only).\" (agent-transcript sink's storage boundary)", "line": 138, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "grep -rn \"secrets\\.|GH_TOKEN|set -x|set -eux\" .github/workflows/*.yml", "output": "docs/audit/BOUNDARIES.capture.txt § 9"},
      {"type": "command", "command": "grep -n \"Raw transcript/error text is intentionally excluded\" deploy/nightshift-scheduled-run-detector.py", "output": "docs/audit/BOUNDARIES.capture.txt § 8"}
    ]
  },
  {
    "id": "Q2",
    "verdict": "CONFIRMED-BUG",
    "findings": ["F-1", "F-2"],
    "evidence": [
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "orchestrator-terminal diagram box: \"Omnigent sandbox, type auto -> bwrap (Linux) / seatbelt (macOS); READ+WRITE isolation: $HOME never mounted; ~/.ssh ~/.aws ~/.claude ~/.codex ~/.config masked\"", "line": 18, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/agents/crew_alpha/config.yaml", "symbol": "os_env (top level, crew_alpha lead's own execution environment): sandbox.type: none", "line": 310, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/agents/crew_alpha/config.yaml", "symbol": "terminals.shell: sandbox.type: auto, write_paths/read_paths, comment citing docs/TRUST-BOUNDARY.md by name -- the CORRECTLY configured, but unused-by-sys_os_shell, sandboxed alternative in the SAME file", "line": 316, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/config.yaml", "symbol": "os_env (program's own top-level execution environment): sandbox.type: none", "line": 654, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/config.yaml", "symbol": "terminals.shell: sandbox.type: auto (same shape as crew_alpha's)", "line": 660, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_bwrap_probe (functional probe: runs bwrap --ro-bind / / /bin/true, not command -v)", "line": 170, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/preflight.sh", "symbol": "D5 check calls nightshift_bwrap_probe / nightshift_bwrap_diagnosis", "line": 182, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": ".omni/blocked.md", "symbol": "\"RESOLVED 2026-08-23T10:49Z -- was: every Codex seat dead (bwrap/userns, issue #5) -- CLOSED. Owner installed /etc/apparmor.d/bwrap\"", "line": 58, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "\"| GitHub (~/.config/gh) | ... | Use a fine-grained PAT scoped to the ONE project repo ... Never a classic PAT, never org-wide. |\"", "line": 43, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/systemd/omnigent-host.service", "symbol": "NoNewPrivileges=yes (verified present, matches TRUST-BOUNDARY.md's claim)", "line": 35, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/systemd/omnigent-server.service", "symbol": "NoNewPrivileges=yes", "line": 28, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "bwrap --ro-bind / / /bin/true; echo bwrap_exit=$?", "output": "docs/audit/BOUNDARIES.capture.txt § 2 -- exit 0"},
      {"type": "command", "command": "ls -la ~/.ssh ~/.claude ~/.codex ~/.config/gh", "output": "docs/audit/BOUNDARIES.capture.txt § 3 -- all four fully listable/readable from inside sys_os_shell"},
      {"type": "command", "command": "ls -la /proc/self/ns; grep -i '^NStgid|^NSpid|^Seccomp' /proc/self/status", "output": "docs/audit/BOUNDARIES.capture.txt § 4 -- default/initial namespace inodes, Seccomp: 0"},
      {"type": "command", "command": "gh auth status", "output": "docs/audit/BOUNDARIES.capture.txt § 5 -- gho_-prefixed OAuth token, scopes gist/project/read:org/repo/workflow"},
      {"type": "command", "command": "id", "output": "docs/audit/BOUNDARIES.capture.txt § 1 -- groups=1000(nightshift),988(docker)"}
    ]
  },
  {
    "id": "Q3",
    "verdict": "CONFIRMED-BUG",
    "findings": ["F-1"],
    "evidence": [
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "item 5, \"The Docker socket... this is root-equivalent\"", "line": 99, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "item 6, \"floci, and the socket it passes on... Any worker that can run floci start is exercising the docker-group privilege\"", "line": 108, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "residual risk 1, \"Docker-group membership is root-equivalent\"", "line": 148, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/agents/crew_alpha/agents/eng_claude/config.yaml", "symbol": "os_env: sandbox.type: none (Claude workers -- documented TRUSTED, matches TRUST-BOUNDARY.md exactly, not a bug)", "line": 85, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/agents/crew_alpha/config.yaml", "symbol": "same os_env.sandbox.type: none applies to the crew LEAD's own tool calls -- undocumented as such, contrast case", "line": 310, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "command", "command": "id", "output": "docs/audit/BOUNDARIES.capture.txt § 1 -- groups=1000(nightshift),988(docker), confirming live docker-group membership"},
      {"type": "command", "command": "ls -la ~/.ssh ~/.claude ~/.codex", "output": "docs/audit/BOUNDARIES.capture.txt § 3"}
    ]
  },
  {
    "id": "Q4",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "\"Deeper unit-level sandboxing (ProtectSystem=strict etc.) is deliberately NOT applied: these are user units, and user-manager namespace options fail on Ubuntu 24.04's unprivileged-userns restrictions in ways that brick the service at boot\"", "line": 63, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "deploy/systemd/omnigent-server.service", "symbol": "NoNewPrivileges=yes, no ProtectSystem= line present (matches the documented decision not to add it)", "line": 28, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "program/agents/crew_alpha/config.yaml", "symbol": "terminals.shell's bwrap block: correctly designed, currently unwired to sys_os_shell (F-1) -- inert, not redundant; must not be removed (sandboxing is in the brief's protected set)", "line": 316, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"},
      {"type": "permalink", "path": "docs/prd/DEFINITION-OF-DONE.md", "symbol": "condition 19, \"No ADR waives an invariant... nothing in the brief's protected set may be proposed for removal\"", "line": 401, "sha": "662600662ee17267aed75bb945556f9a40ae0e42"}
    ]
  }
]
```

## Q1 — the credential sink inventory

Six sink classes are named in DoD condition 16's Q1 (`docs/prd/DEFINITION-OF-DONE.md:394-395`):
process logs, journald, alert bodies, PR/issue comments, CI logs, agent transcripts. Each gets a
**stable, quotable id** below for D20's future `sentinel_test_node` (issue #486) to inject a
unique sentinel at every credential source and assert its absence from each of these — the
prerequisite this issue exists to satisfy (`docs/prd/DOD-RECONCILIATION.md:39,53`).

| id | sink | location | verdict |
|---|---|---|---|
| `SINK-01` | process stdout/stderr as captured by any supervisor | `deploy/systemd/*.service` (`StandardOutput=journal`/`StandardError=journal`); any `subprocess.run(capture_output=True)` call site that logs its result | SOUND |
| `SINK-02` | journald (the systemd query surface over SINK-01's stored output) | `journalctl --user -u omnigent-server\|omnigent-host\|nightshift-*` | SOUND |
| `SINK-03` | alert bodies | GitHub issue comments produced by `deploy/nightshift-watchdog.sh:700-755` and `deploy/nightshift-scheduled-run-detector.py:236-284` (`_alert_body`/`_deliver`) | SOUND |
| `SINK-04` | PR/issue comments (general) | any `gh pr comment` / `gh issue comment` call site, including `bin/nightshift-ctl pr-create`'s wrapped `gh pr create` and cross-crew/cross-vendor review comments | SOUND (scope: this audit reviewed the alert-delivery call sites only, `SINK-03`'s superset; the general review/PR-comment code path in `bin/nightshift-ctl` is large and was not separately line-audited here — named so a future pass does not assume it was) |
| `SINK-05` | CI logs | `.github/workflows/*.yml` step output, viewable via `gh run view --log` | SOUND |
| `SINK-06` | agent transcripts | Omnigent server/host session store (Postgres, loopback-only, `docs/TRUST-BOUNDARY.md:138`); each local harness's own on-disk transcript directory (`~/.claude/projects/**`, `~/.codex/**`) | SOUND, with the caveat noted below |

**Findings, by sink:**

- **`SINK-01`/`SINK-02` (process logs / journald).** Every systemd unit reviewed injects its
  secret via `EnvironmentFile=` (`deploy/systemd/omnigent-server.service:13`,
  `deploy/systemd/nightshift-scheduled-run-detector.service:10`), never as an inline `ExecStart`
  argument (which `ps`/journald-logged command lines would capture). No `set -x`/`set -eux` was
  found anywhere in `deploy/` or `bin/` (`docs/audit/BOUNDARIES.capture.txt` §9 ran the sweep).
  These sinks are readable only by the `nightshift` principal that already holds the
  credentials — writing a secret there would not cross a trust boundary the way it would in
  `SINK-03`–`SINK-06`, but no evidence of it happening was found either way.
- **`SINK-03` (alert bodies).** `_alert_body()` (`deploy/nightshift-scheduled-run-detector.py:236`)
  builds the GitHub comment from six templated, structured fields only (task id, run id,
  scheduled time, status, failure class, count, conversation id) and its own text says so:
  `"Raw transcript/error text is intentionally excluded."` (line 254). The one function that DOES
  read transcript content, `_assistant_texts()` (line 161), is called only by `_classify()` for
  keyword matching (vendor-outage/rate-limit/auth/timeout classification) — its return value
  never reaches `_alert_body()`. `deploy/nightshift-watchdog.sh`'s `body=` (line 700) is built
  from shell variables holding pre-classified state words (`headline`, `reason_label`,
  `server_state`, `host_state`), not raw command output. Both deliver via `gh issue comment`
  (line 755) / a mode-`0600` tempfile unlinked in a `finally` block (line 259-284) — no
  world-readable intermediate.
- **`SINK-05` (CI logs).** Exactly one workflow uses a secret (`.github/workflows/
  live-api-smoke.yml:34-35`, `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`), passed as an env var to a
  test process, never echoed, never passed as a CLI argument. `tests.yml` uses no secrets at all.
- **`SINK-06` (agent transcripts).** `docs/TRUST-BOUNDARY.md:138` places the Omnigent session
  store behind a loopback-only Postgres instance — same trust boundary as the host itself, not a
  wider one. The caveat: **this audit's own Q2 finding (F-1) means the *lead's own tool output* —
  which is exactly what becomes an agent transcript — currently has no sandbox between it and
  every credential file on the box.** Nothing in this audit's own transcript leaked a credential
  (verified: every command that touched a credential path used `ls -la` metadata or a
  masked-output tool, never `cat`/`less`/a raw read — `docs/audit/BOUNDARIES.capture.txt`,
  Safety note at the top), but the sink's *safety* here depended on operator discipline in this
  one session, not a structural control. That gap is F-1 itself, dispositioned under Q2/Q3 below,
  not re-counted as a second `SINK-06` defect.

Overall Q1 verdict: **SOUND** — none of the six sink code paths reviewed leak a credential by its
own construction. `SINK-06`'s caveat is F-1's consequence, not a new, independent sink defect.

## Q2 — every claim in `docs/TRUST-BOUNDARY.md` verified against the code and the live host

Verified and matching (SOUND, not repeated in the summary table's finding list since no id is
minted for a claim that holds):

- **`NoNewPrivileges=yes`** is present in `omnigent-server.service:28`, `omnigent-host.service:35`,
  and `nightshift-scheduled-run-detector.service:21` — matches the doc's claim exactly.
- **Deliberately no `ProtectSystem=strict`** — matches the doc's own stated reasoning (would brick
  the user-unit boot on Ubuntu 24.04) — see Q4.
- **Bug 5's remediation is function-based**, not presence-based: `nightshift_bwrap_probe`
  (`deploy/lib/deploy-lib.sh:170`) runs `bwrap --ro-bind / / /bin/true`, and `deploy/preflight.sh`'s
  D5 check (line 182) calls it rather than `command -v bwrap`.
- **The AppArmor fix for bug 5 is genuinely resolved on this host**, live-reproduced today:
  `bwrap --ro-bind / / /bin/true` exits `0` (`docs/audit/BOUNDARIES.capture.txt` §2), matching
  `.omni/blocked.md`'s `RESOLVED 2026-08-23T10:49Z` entry (line 58). This matters for what follows:
  the gap below is **not** a re-occurrence of bug 5. bwrap works. It simply is not being invoked.

**Not matching — two confirmed, newly-found violations of the brief's protected set (`docs/prd/
DEFINITION-OF-DONE.md:432`: "credential boundaries, sandboxing"), which condition 19's own rule
forbids dispositioning as `ACCEPTED-RISK`:**

### F-1 — the orchestrator terminal (`sys_os_shell`, program and every crew lead) runs with no sandbox at all, contradicting the doc's own diagram

`docs/TRUST-BOUNDARY.md:18-19` states, in its own architecture diagram: *"orchestrator terminals
(program, crew leads — sys_os_shell) — Omnigent sandbox, type auto → bwrap (Linux) / seatbelt
(macOS) — READ+WRITE isolation: $HOME never mounted; ~/.ssh ~/.aws ~/.claude ~/.codex ~/.config
masked; only cwd + declared grants exist."*

Both `program/config.yaml` and `program/agents/crew_alpha/config.yaml` carry **two different**
`os_env`/sandbox definitions for the same role:

- A **top-level `os_env` block** (`program/config.yaml:654-658`, `program/agents/crew_alpha/
  config.yaml:310-314`): `type: caller_process`, `sandbox: {type: none}`.
- A **`terminals.shell` block**, a few lines below it in the same file
  (`program/config.yaml:660-682`, `program/agents/crew_alpha/config.yaml:316-338`):
  `sandbox: {type: auto}`, with explicit `write_paths`/`read_paths` allowlists and — this is the
  block whose own comment names the doc — *"Hermetic shell for orchestrator terminals (auto =
  bwrap on Linux, seatbelt on macOS). $HOME is never mounted... See docs/TRUST-BOUNDARY.md."*

This audit's own `sys_os_shell` calls, live, this session, demonstrate they run through the
**first** (unsandboxed) block, not the second:

- `ls -la ~/.ssh` returned a full listing including `id_ed25519` (an SSH private key, mode 600)
  and its public counterpart — not "does not exist".
- `ls -la ~/.claude` and `ls -la ~/.codex` returned full listings including `.credentials.json`
  and `auth.json` respectively (subscription auth for both harness vendors) — not masked.
- `/proc/self/ns/*` resolved to inode numbers `4026531834`–`4026531841` — the Linux kernel's
  well-known **default/initial** namespace ids, not a freshly bwrap-created set. `Seccomp: 0`.
- This is **not** bug 5 recurring: `bwrap --ro-bind / / /bin/true` exits `0` in this exact same
  session (capture §2), proving bwrap itself is functional on this host right now. The
  `terminals.shell` block would work if invoked. Nothing invokes it for `sys_os_shell`.

Full evidence, including every path/namespace command, is in `docs/audit/BOUNDARIES.capture.txt`
§§1, 3–4. No credential file's *contents* were read to produce this evidence — only listings,
permissions, and namespace metadata (see the capture file's Safety note).

**Steelmanning the doc's own possible defense, for the record:** `docs/TRUST-BOUNDARY.md`'s
"What remains trusted, explicitly" §1 separately states Claude-native *workers'* Bash tool is
trusted/unsandboxed by design — but that section is about `eng_claude`/`qa`/`reviewer_claude`
(confirmed: `program/agents/crew_alpha/agents/eng_claude/config.yaml:85-89` also sets
`sandbox.type: none`, and the doc is right about that — it is not a bug, see Q3). The diagram's
"orchestrator terminals... crew leads" line is a **separate, explicit** claim about a different
role (this one), naming `sys_os_shell` specifically, and it does not hold.

### F-2 — the live GitHub credential is a classic-scoped OAuth token, not the fine-grained, repo-scoped PAT the doc requires

`docs/TRUST-BOUNDARY.md:43` requires: *"Use a fine-grained PAT scoped to the ONE project repo...
Never a classic PAT, never org-wide."*

`gh auth status` (which prints a token's prefix and scopes, never its value — the safe way to
inspect token shape) shows the live credential is `gho_`-prefixed (a GitHub **OAuth App** token,
the shape `gh auth login`'s device flow issues) with scopes `gist, project, read:org, repo,
workflow`. An OAuth App token's `repo` scope is the same **classic, account-wide** grant a
classic PAT would carry — every repository the account can access, not one. There is no
mechanism by which an OAuth App token's scope list expresses "this one repository only"; that
granularity only exists on GitHub App installation tokens and fine-grained PATs
(`github_pat_...`), neither of which this token's prefix matches. `read:org`, `gist`, and
`project` are also broader than "the one project repo".

Overall Q2 verdict: **CONFIRMED-BUG** (F-1, F-2). Both are genuine, live-reproduced departures
from `docs/TRUST-BOUNDARY.md`'s own stated design, on a live host, verified without reading any
secret's contents. Neither is bug 5 recurring.

## Q3 — can a worker escape its declared write scope by a documented path?

**Docker socket / floci (already documented, TRUST-BOUNDARY.md items 5–6, residual risk 1).**
Yes, and the doc already says so plainly: the `nightshift` runtime user is in the `docker` group
(live-confirmed, `docs/audit/BOUNDARIES.capture.txt` §1: `groups=1000(nightshift),988(docker)`),
and Docker's own documentation states docker-group membership is root-equivalent — any member
can bind-mount `/` read-write as root via a container. `floci` (the local cloud emulator) widens
the same path by making it a routine, everyday command rather than a deliberate escalation. This
is a **known, named, already-accepted** trade-off, not a new finding — `docs/TRUST-BOUNDARY.md`
states the reasoning (no real cloud credentials on the box in exchange for this accepted route)
explicitly. It is worth a **FOLLOW_UP**, not adjudicated further here: neither this residual risk
nor any row in `.omni/tech-debt.md` currently carries the trigger+owner pairing condition 18's
own validator will require once `docs/audit/FINDINGS.md` (#482) exists and a disposition must be
assigned — whoever holds #482/#483 should confirm whether this stays `ACCEPTED-RISK` (with a
tech-debt row) or is reclassified.

**Claude workers (`eng_claude`/`qa`/`reviewer_claude`).** `program/agents/crew_alpha/agents/
eng_claude/config.yaml:85-89` declares `os_env: sandbox: {type: none}` — there is no declared
write scope to escape; the doc's "What remains trusted, explicitly" §1 says exactly this, and
the config matches it. Not a bug.

**The orchestrator terminal (`sys_os_shell`, program and crew leads) — F-1, reused from Q2.**
This is the actual answer to Q3 that is *not* already documented and accepted: the diagram
claims a declared write (and read) scope — "$HOME never mounted... only cwd + declared grants
exist" — and the live tool has **no scope at all**, declared or otherwise: full read (and,
consistent with `type: caller_process`, full write) access to the real `$HOME`, not a masked
one. This is not an "escape" through a documented side door (docker socket, a symlink); it is
the complete absence of the boundary the doc claims exists. Per condition 19's rule, a confirmed
violation of the protected set (sandboxing) cannot be `ACCEPTED-RISK` even though the analogous
docker-socket case above is.

`writable_roots` and symlink-into-`$HOME` handling for **Codex workers'** `workspace-write`
sandbox specifically: this is Codex's own internal sandbox implementation, external to this
repository (the same externality shape #489 found for `sys_session_send`'s schema — the
mechanism enforcing it is not `git`-versioned here and this audit found no in-repo code to
verify it against). Marked **OUT-OF-SCOPE** for this specific sub-question rather than guessed
at; named so the next reader does not have to rediscover the gap.

Overall Q3 verdict: **CONFIRMED-BUG** (F-1). The docker/floci path is already-documented and
not re-adjudicated as a new bug here; the orchestrator-terminal path is a genuine, new,
protected-set violation.

## Q4 — which controls are redundant and removable, and which complexity is load-bearing

No genuinely redundant control was found — every layer this audit checked covers a **distinct**
process class or attack surface, not the same one twice:

- **`NoNewPrivileges=yes`** (systemd, all three units checked) stops setuid escalation
  unconditionally, independent of whether any mount-namespace sandboxing is in effect. It is not
  redundant with bwrap: bwrap's mount namespace stops *file visibility*; `NoNewPrivileges` stops
  *privilege escalation*. Removing either weakens a distinct property.
- **`ProtectSystem=strict` and deeper unit-level sandboxing were deliberately NOT added** —
  `docs/TRUST-BOUNDARY.md:63` states this explicitly (user-manager namespace options brick the
  boot on Ubuntu 24.04's userns restrictions) and the live unit files (`omnigent-server.service:
  28` and siblings) confirm no such directive was added. This is the doc's own Q4-shaped
  decision already made correctly: avoided complexity that was not load-bearing enough to justify
  its boot-time risk. **SOUND**, cited as the pattern the rest of this Q asks about, not a defect.
- **The `terminals.shell` sandboxed block (`program/agents/crew_alpha/config.yaml:316-338` and
  `program/config.yaml:660-682`) is correctly designed and currently inert for `sys_os_shell`
  (F-1) — it is NOT redundant complexity to remove.** It is the fix target: wiring `sys_os_shell`
  to it (or an equivalent) is what would close F-1. Proposing its removal would remove sandboxing
  itself from the brief's protected set, which condition 19 (`docs/prd/DEFINITION-OF-DONE.md:
  401-402`) forbids outright regardless of any argument that it is "unused". Named here only to
  be explicit that this audit is not recommending it be deleted.
- **`blast_radius`/`pr_close_keyword_guard` policies (`program/agents/crew_alpha/agents/
  eng_claude/config.yaml`'s `guardrails.policies`) and the sandbox layers are complementary, not
  duplicative:** the former denies specific catastrophic *git* operations regardless of sandbox
  state; the latter (where wired) confines *filesystem* reach. Removing one does not make the
  other redundant — they cover different operations entirely.

Overall Q4 verdict: **SOUND**. Nothing in the brief's protected set is proposed for removal here.

## Scope boundary (restated from #477/#480's own boundary)

This document does not fix F-1, F-2, or the docker/floci disposition gap; does not modify
`program/**` (platform-owned, requires an ADR per `docs/owners.md:22`); and does not create
`docs/audit/FINDINGS.md` (#482's own artifact, in progress this batch under a separate task —
that task must create F-1/F-2 there with their own dispositions). Concurrency/deadlock
prevention, GitHub/PR workflow, install/deploy reliability, and 3–7 day survival characteristics
are #477/#479/#481 and stay there.
