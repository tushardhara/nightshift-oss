# ADR-0012: Load and merge per-seat policies; do not hoist them to the root

Date: 2026-08-24 · Status: proposed
Deciders: program · Proposers: platform (worker slot 146, wave 6)
Conversation: d45f34ff-e138-4b89-b1c3-389695bea9cd (original), c1a5d1fd-b1fc-4b17-b511-88150acf20ca (revision: `staff` non-blocking notes folded in)
Issue: #118 · Evidence: `docs/audit/118-per-seat-policy-probe.md`

## Context

#118 claimed that only ROOT policies load: a seat is not an independently
registered agent, it is a `sub_agent_name` inside one bundle, and the policy
engine is built from the root spec's `guardrails.policies` alone. `security`
found this by code-read while chasing an unrelated question and said plainly
that it was unconfirmed by execution.

**It is now confirmed by execution** — scratch server, scratch port, scratch
DB, scratch artifact dir; a seat declaring `read_only_os` was ALLOWED to
write, while the identical block at root DENIED in the same run on the same
two enforcement paths. Full method and receipts in the evidence file. Three
facts from it drive this decision:

1. A seat session **is** governed by the root's policies — the positive
   control was itself a seat session and it was denied. The gap is
   specifically that a seat's *own* block is never traversed.
2. The seat's block is **parsed, valid, and in memory** on
   `spec.sub_agents[i].guardrails.policies`. This is not a schema or
   registration failure. There is no traversal to fail; there is no traversal.
3. **44 per-seat declarations across 20 of 22 seats are dead**, including five
   `read_only_os` assertions (`security` and both reviewer seats on both
   crews), 13 `blast_radius` blocks, `worker_semaphore`,
   `pr_close_keyword_guard`, `detect_thrashing` and `stay_in_workspace`.

The question this ADR answers is the one #118 poses: should the engine **load
and merge** per-seat policies, or should the repo **stop declaring them** and
hoist everything to root? Either is defensible. Asserting controls that no
engine loads is not, and that is the only outcome ruled out.

This ADR records the direction. It does not implement it — the probe was the
gate on deciding, and the decision is the gate on code.

## Options considered

### A — Load and merge per-seat policies in the engine (chosen)

Make `build_policy_engine` resolve `conv.sub_agent_name` against
`spec.sub_agents` and append that seat's `guardrails.policies` to the agent
tier, root policies first. `any_policies_apply` gains the matching check so
the fast path cannot short-circuit a seat-only bundle. Ordering follows the
existing tier rule — session, then agent, then admin — with the seat's block
sitting inside the agent tier after the root's, so a seat can only ever add
to what root already denies.

**For.** It makes 44 existing declarations mean what every reader of this
repo already believes they mean, without editing 20 files. It is the only
option that can express *tighter than root for this seat* — which is what
`read_only_os` on a reviewer is, and what root cannot say without making
every seat read-only. It matches the object model the parser already builds:
sub-agents are parsed into full `AgentSpec`s, guardrails included, and that
data is already sitting there unused.

**Against.** It is upstream behaviour, in an installed package this org does
not own, reached through four enforcement sites. It cannot ship as a repo
change. Until it does ship, every declaration stays inert, so this option
does not by itself close the gap — it needs the interim control below.

### B — Hoist everything to root; forbid per-seat `guardrails:` (rejected)

Delete every per-seat block and re-express what survives at root, gated on
the policy's own conditions (actor, labels, tool) rather than on placement.
A lint refuses any `guardrails:` under `program/agents/**`.

**For.** Ships entirely inside this repo, today, with no upstream dependency.
After it, what is declared is what loads — no gap to detect, because there is
nowhere to declare a dead policy.

**Against — and this is what decided it.** Most of the 44 cannot survive the
hoist. `read_only_os` at root makes *every* seat read-only, which stops the
org: the workers exist to write. There is no per-seat condition available at
root to scope it, because the thing that identifies the seat —
`sub_agent_name` — is exactly what the engine does not read. The same
argument kills the 13 per-seat `blast_radius` blocks, `stay_in_workspace` on
crew leads, and the per-crew cost gates. Option B does not hoist those
controls; it **deletes** them and writes down that the org has no per-seat
enforcement. That is honest, and it is a real loss of posture, not a
relabelling.

### C — Ship both: hoist now, merge later (rejected as a decision, kept as sequencing)

Do B immediately for its honesty, then A when upstream lands, then re-declare.
Rejected as a *decision* because it churns 20 files twice and, in between,
throws away the declarations that record what each seat was meant to be
constrained to — the only written statement of intent the org has for them.
Its useful half is kept: see the interim control, which delivers B's honesty
without B's deletions.

### D — Enforce per-seat policy in the repo's own tooling (rejected early)

Approximate the missing enforcement in `bin/nightshift-ctl` or a wrapper.
Rejected: it enforces at dispatch, not at the tool call, so it cannot see a
write the model makes mid-turn. That is the same prompt-and-dispatch-level
convention #118 found masquerading as a control, rebuilt deliberately.

## Decision

**Adopt A. Per-seat policies should be LOADED AND MERGED by the engine, and
the repo should keep declaring them.** Root policies apply to every seat as
they do today; a seat's own block is appended after them, and can only
tighten.

Pending that, three things carry immediately and are the only work this
decision authorises now:

1. **The declarations stay** — they are the specification of the fix, and
   deleting them destroys the record of intent.
2. **The docs stop asserting them.** `deploy/README.md` is corrected in the
   same PR as this ADR: every control that lives only on a seat is marked
   **NOT ENFORCED**, with what actually holds the line named in its place.
   An inert control documented as live is worse than a documented gap — an
   absent control prompts a question, a dead control answers it wrongly.
3. **A detector, before instance four.** Described below; a separate seat is
   building a sibling detector for stale worktrees and the shape is shared.

Filed upstream as a bug, with this evidence file as the reproduction.

## Consequences

**Easy.** The repo's own statement of what each seat may do becomes true
without a 20-file rewrite. Reviewers keep reading `read_only_os` on the
`security` seat and being right.

**Hard.** The gap stays open until upstream ships. Every seat is exactly as
constrained as root for that whole window, and the org must operate knowing
it — which is what the README correction is for. `blast_radius` is the sharp
edge: not inert, but uniform at the root's value, and on the running config
that value is the looser one (`gate_pushes: false`, #115/ADR-0011).

**Forecloses.** Nothing structural. If upstream refuses A, B is still
available and this ADR's Option B section is the pre-written case for it.

**#124 is a precondition on Option A's acceptance test.** Option A resolves
`conv.sub_agent_name` against `spec.sub_agents`, so the upstream fix must
resolve **parent-scoped**, not tree-global: `crew_alpha` and `crew_beta`
declare six identically-named seats (`eng_claude`, `eng_codex`, `pm`, `qa`,
`reviewer_claude`, `reviewer_codex`), and a tree-global lookup on
`sub_agent_name` would merge `crew_alpha`'s reviewer policies into
`crew_beta`'s sessions — a silent widening, not a load failure. And because
strict `spec.load('program/')` refuses the tip bundle on those same duplicate
names, Option A **cannot be integration-tested against the real bundle until
#124 lands**. The probe is unaffected (synthetic bundles), so this changes
nothing about the verdict — only what "shipped" has to mean. Refs #124.

## The detector — declared-but-never-loaded config

#118 is the **third** instance of the class: #5 (`writable_roots` declared but
missing), #115 (`risky_action: DENY` unreachable under `gate_pushes: false`),
#118 (per-seat policies never load). All three were found by a seat chasing an
unrelated question. Nothing detects the class. The shape that would:

**Compare the DECLARED set against the LOADED set, and fail on the
difference.** Both sides must be obtained by execution, never by reading YAML
twice:

- **Declared** — walk `program/**` with the production parser and enumerate
  every policy at every depth, as the probe's `introspect_specs` does. Not a
  YAML grep: a grep cannot see a block the parser drops.
- **Loaded** — build the engine the way the server builds it, for one session
  per seat, and enumerate the policies it actually holds. The probe's two
  HTTP paths are the runnable version of this and need no LLM turn.
- **Reachable** — for each loaded policy, whether its declared branches can
  fire under the config in force. This is the #115 half: `risky_action: DENY`
  was loaded and still unreachable. Cheapest useful form is per-policy and
  hand-written, not general.

Report each declared policy as `LOADED` / `NOT LOADED` / `LOADED BUT
UNREACHABLE`, exit non-zero on anything but the first, and run it at wave
close next to `spec status`. The generalisation worth stating: **a control is
not a control until something has observed it fire.** Every one of the three
instances would have been caught by an assertion that each declared control
denies at least once in a scratch probe — which is what
`tests/probe_per_seat_policy.py` is a single instance of, and why it ships
with a positive control rather than only the experiment.

## Cost

Zero at today's volume and at 100× — no new service, no new dependency. The
detector is one scratch-server run per wave close (~40s wall clock, one
ephemeral port, one temp dir). Operational surface added: one more thing that
can fail at wave close, and one upstream bug to track.

## One-way door?

No. The declarations already exist and are already inert; nothing changes
shape. If A is refused upstream, B is a mechanical edit of 20 files.

## Scale tier

None yet.

## Reversal trigger

- Upstream declines to traverse sub-agent guardrails, or has not shipped it
  within three waves → adopt Option B and delete the declarations, recording
  the lost controls explicitly in `deploy/README.md` rather than silently.
- The detector reports a *fourth* distinct instance of declared-but-not-
  enforced in a subsystem other than policies → the class is broader than
  config loading and needs a decision of its own, not this one extended.
