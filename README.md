# Nightshift

**Software that keeps building while you sleep.**

You write a paragraph describing what you want. A team of AI agents — organised
like a real engineering org, with a product manager, architects, two or more
delivery crews, reviewers, a security seat and an integrator — turns it into
working code over days, unattended. Crews open pull requests, a *different*
crew reviews them, and only then does the integrator merge.

You are not in the loop. You read the morning report.

---

## Security model — read before deploying

Nightshift runs coding agents unattended, with real credentials, on text that
arrives from GitHub. Treat a running install as code execution handed to
whatever reaches the agents.

- **Claude seats run unsandboxed.** Every `claude-native` seat (engineers,
  reviewers, oncall, platform, security) uses `permission_mode: auto`: tool
  calls are approved without a prompt, and Claude Code's Bash runs as the
  runtime user outside any Omnigent sandbox, with that user's files, network
  and credentials. The fences in `deploy/policies/` inspect tool calls; they
  are not a sandbox.
- **Codex seats are sandboxed only because their config says so.** Omnigent
  turns on `--dangerously-bypass-approvals-and-sandbox` for `codex-native` by
  default. Every Codex seat here sets `yolo: false` to opt out, and falls back
  to `~/.codex/config.toml` (`approval_policy = "never"`,
  `sandbox_mode = "workspace-write"`). A seat added without `yolo: false` runs
  with no sandbox at all.
- **The GitHub token is the blast radius.** Every Claude seat runs as the user
  that holds the `gh` login, so whatever that token can do, an agent — or a
  prompt that steers one — can do: push, open and merge pull requests, file
  issues, and dispatch workflows if it has `workflow` scope. Scope it to
  `repo` on the one target repository (fine-grained where you can), never
  admin, and from an account that reaches nothing else you care about.
- **Run it only on a dedicated host.** The runtime user is in the `docker`
  group, which is root-equivalent, and the seats can read everything that user
  can. Keep no other services, keys or cloud credentials on that box.
- **Issue and comment text is agent input.** The program reads its tracker's
  issues and comments and acts on them. The author-trust gate in
  `nightshift-tracker-sync` admits an issue to a lane, and hands its body or
  comments to a seat, only when the
  author is `OWNER`, `MEMBER` or `COLLABORATOR` or is listed in
  `trusted_authors` in `.omni/tracker.json`; anyone else's text is withheld and
  their labels are ignored. That gate covers the tracker path, not a seat that
  runs `gh issue view` itself, so still prefer a tracker repository where only
  people you trust can open issues, comment, or open pull requests.
- **The control API has no authentication.** The Omnigent server on
  `127.0.0.1:6767` runs in single-user mode: any process on the box, including
  tests a worker runs, can create sessions and fire runs through it. It is not
  published beyond the box unless you opt in (`NIGHTSHIFT_TAILSCALE_SERVE=1`),
  and then only behind an owner-only Tailscale ACL — `deploy/README.md`,
  "Control API".

The full statement of what is and is not enforced is
[`docs/TRUST-BOUNDARY.md`](docs/TRUST-BOUNDARY.md).

---

## How a piece of work travels

The same journey every time, whether it takes an hour or three days.

```
   your paragraph
         │
         ▼
   ┌──────────┐   product writes the PRD and the definition of "done".
   │ discovery│   Two architects — one on Claude, one on Codex — each design
   └──────────┘   the system blind to the other. The program reconciles them
         │        decision by decision and records the result.
         ▼
   ┌──────────┐   Work is split into tickets and handed to a crew. The number
   │ chartered│   of crews is decided by the design, not by your headcount.
   └──────────┘
         │
         ▼
   ┌──────────┐   An engineer takes the ticket, gets its own copy of the repo,
   │  built   │   and writes the code and the tests.
   └──────────┘
         │
         ▼
   ┌──────────┐   A crew that did NOT write the code reviews it, told to find
   │ reviewed │   what is wrong rather than to approve. Findings are recorded
   └──────────┘   where they cannot be quietly deleted.
         │
         ▼
   ┌──────────┐   One agent — and only ever this one — merges. It checks the
   │  merged  │   review actually happened before it does.
   └──────────┘
```

Nothing waits on you at any step. If something genuinely blocks, it lands in
`.omni/blocked.md`, and that is the only file that needs a human.

---

## What it refuses to do

This is the part that makes it safe to leave running. The agents decide what
*should* happen; a separate control plane decides what is *allowed* to happen.
Agents cannot talk it out of these.

| Rule | Why it exists |
|---|---|
| Only so many agents may work at once | Stops a runaway spawning a hundred workers |
| One agent may act as the program at a time | Two coordinators giving conflicting orders is the classic failure |
| In *someone else's* repository: open pull requests, never merge | A wrong merge into a customer's main branch cannot be undone |
| A review finding cannot be weakened or deleted once filed | A blocker that can be erased is not a blocker |
| A pull request cannot merge until its review is on the record | Otherwise "reviewed" is a claim rather than a fact |
| New prose must roughly match the codebase's own commenting style | The first external run wrote 47% comments into a repo that writes 5% |

These are refusals, not warnings, and each is tested with a deliberately broken
version to prove the check actually fails when it should.

One important rule is **not** in that list, because it is a convention rather
than a mechanism: *only the integrator merges to the main branch.* Every seat is
instructed to open a pull request and stop, and in practice they do — but
nothing currently stops a misbehaving agent from merging in the org's own
repository. In a borrowed repository it is stopped, by the third rule above.

---

## Who is in the org

| Seat | Does |
|---|---|
| **product** | The PRD, the milestones, what "finished" means |
| **architect** / **staff** | Two independent designs, then one reconciled decision record |
| **crew_alpha**, **crew_beta**, … | The delivery teams — each with a lead, engineers, QA and reviewers |
| **ux** | Flows, every screen state, copy, accessibility |
| **security** | Reviews anything touching auth, personal data, input handling or crypto |
| **platform** | CI, build, shared libraries, test infrastructure |
| **release** | The integrator — the only seat that merges |
| **oncall** | The interrupt lane: reproduce, mitigate, smallest possible fix |

Engineers are generalists. Work is routed by who already has the context, not
by invented specialisms.

---

## Start a project

```bash
cd /path/to/repo
~/.nightshift/bin/nightshift-init.sh              # once per repo
gh auth refresh -s project                        # logging in does not grant this
~/.nightshift/bin/nightshift-tracker-init.sh .    # labels, templates, board
$EDITOR .omni/brief.md                            # a paragraph — your only input
export PYTHONPATH=~/.nightshift/deploy/policies
omnigent run ~/.nightshift/program                # web UI on :6767
```

Then tell the session to begin:

```
Read .omni/brief.md and run the discovery skill. Unattended — do not wait for
me. Create your hourly heartbeat task first.
```

You do not write the goal. Discovery derives it, and the design decides how
many crews actually run.

---

## Watching it

From your phone, two views doing different jobs:

- **GitHub Mobile** — the board, issues, PRs and diffs, plus a notification
  every time the program posts its report.
- **The Omnigent UI**, over an SSH tunnel (or Tailscale, if you opted in) —
  agents working live. You can open any worker's terminal, read what it is
  thinking, and take over.

On a laptop:

| Want | Do |
|---|---|
| Watch the workers | `localhost:6767` → **Agents**, click any worker |
| See what merged | `cat .omni/integration-log.md` |
| See who reviewed what | `cat .omni/reviews.md` |
| See real progress | `cat docs/prd/DEFINITION-OF-DONE.md` — the remaining count |
| Resume tomorrow | `omnigent run ~/.nightshift/program -c` |
| Run one crew only | `omnigent run ~/.nightshift/program/agents/crew_alpha` |
| Just one agent, no ceremony | `omni claude` / `omni codex` |

## Adding an idea while it runs

You never message an agent to add work. File a GitHub issue using the **Owner
request** template — from your phone is fine. The program picks it up at its
next hourly check and either routes it to a crew, parks it for a later
milestone, or declines it, always commenting back so you get the verdict.

Ideas enter the pipeline; they do not jump it. Something actually broken is an
**Incident** instead, and that outranks the roadmap immediately.

---

## Running it on a server

Built for full-auto, PR-only operation on a dedicated Ubuntu 24.04 VPS: no
approval prompts, hard cost caps, stuck work escalated rather than blocking a
wave. Access is SSH over Tailscale — nothing is exposed publicly, and the
unauthenticated control API stays on loopback unless you opt in.

`deploy/README.md` has the box spec, bring-up, the security posture, the three
steps that need you present, and the failure table. If the box dies, recovery
is tiered: systemd restart, then provider stop/start, then snapshot, and worst
case a rebuild from GitHub plus the state branch a timer pushes every 30
minutes.

## Where things live

```
AGENTS.md          the constitution — the rules every agent follows
bin/               nightshift-ctl: the control plane, and the setup scripts
program/           the org: config, skills, and one directory per seat
docs/              decisions (ADRs), the PRD, control-plane rules
tests/             the suite that proves the guardrails actually refuse
deploy/            provisioning, systemd, policies, provider notes
```

`AGENTS.md` is the rules the agents follow. This file is how you drive it.

---

## Honest limits

- **It writes too much prose.** Left alone it explains itself at several times
  the density of the code around it. There is now a check that refuses this,
  and the habit is still there underneath.
- **A guardrail is only as good as its test.** Several were shipped looking
  like enforcement while remaining satisfiable by a value the agent chose
  itself. They were caught by review, not by the tests written alongside them.
- **Reviews find what tests miss.** The single highest-yield practice here is a
  reviewer told to refute rather than approve — and one that did not write the
  code.
- **It is not cheap.** It runs for days on your Claude and Codex
  subscriptions. Budget caps are hard limits, not warnings.
