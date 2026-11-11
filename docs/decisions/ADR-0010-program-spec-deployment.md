# ADR-0010: `program/**` reaches the runtime through explicit re-registration at server start, not through any checkout

Date: 2026-08-23 (revised 2026-08-23 after execution) · Status: **proposed**
Deciders: program (pending) · Proposed by: platform, wave-5 slot 126 · Revised by: platform, wave-5 slot 133
Conversation: 98e84b94a4814f9da4b5462eca57d622 (original), 6aadf5e9f30b48c3948ea37a4fbc39e3 (revision)
Spec: none — issue #107 is the spec
**Amended by ADR-0011** (`program`-spec deployment authority, #115) — which
adds a provenance rule to Option G, replaces step 1's detector scope with a
three-operand version whose new operand is the **served bundle**, and decides
the reconciliation question this ADR records in Context and leaves open. This
ADR is **not** superseded; everything else below stands.

*Note on the third operand:* it resolves `agents.bundle_location` (the
**registered** bundle) and the extracted cache directory (what is **served**) and
reports them **separately** — ADR-0011 (b) makes distinguishing the two a
blocking read-only pre-condition, because they can disagree and the two answers
imply different remediations. Do not use "registered" and "served" as synonyms
when reading across the pair.

> **This ADR is a proposal and nothing in it is implemented.** The PR that
> carries it implements only #107 asks 1 and 3 — *detect* and *report*. Ask 2,
> the deployment mechanism decided below, is deliberately left unbuilt.
> Refs #107.

> **Revision note.** The first draft of this ADR chose Option A — repoint the
> server's working directory at a dedicated read-only checkout. `staff` found
> (B1) that the cost model rested on an unverified premise about how the
> server resolves specs, and required it be settled by execution before the
> Decision merged. It was settled, and **the premise was false**: the server
> does not read agent specs from its working directory at all. Option A's
> mechanism is inert — it would have bought a scheduled outage that ends every
> in-flight run in this org in exchange for changing which specs load by zero.
> Options B, C and D are inert for the same reason. The Decision below is
> rewritten around the one mechanism that the box actually has. Receipts are
> on PR #113 (`#issuecomment-5389010909`, `#issuecomment-5389031981`) and are
> re-runnable; the falsifying experiment is summarised in Context.

## Context

### What was believed

That the Omnigent server is launched with `cd /srv/project`, reads agent specs
from `program/agents/*/config.yaml` underneath it, and picks up changes on the
next fresh session. Every option in the first draft was costed against that
sentence.

### What is true, measured on this box

**The server's working directory has no bearing on which agent spec it loads.**

Four states, one throwaway agent, one field mutated, on a scratch server
instance with its own port, database and artifact directory — the live server
was never restarted and `/srv/project` HEAD never moved:

| # | spec on disk | how the server was started | **spec actually served** |
|---|---|---|---|
| 1 | `VERSION-ONE` | `--agent <dir>` | `VERSION-ONE` |
| 2 | **`VERSION-TWO`** | restart, **no `--agent`** | **`VERSION-ONE`** |
| 3 | `VERSION-TWO` | restart **with `--agent`** | `VERSION-TWO` |
| 4 | **`VERSION-THREE`** at `<cwd>/program/agents/<seat>/config.yaml` | restart, **no `--agent`**, CWD = that dir | **`VERSION-TWO`** |

State 4 is the falsification: the exact layout the old Context described, in
the server's own working directory, ignored across a restart. State 2 shows a
restart alone deploys nothing. State 3 shows what does.

The real path is:

1. A spec directory becomes a `.tar.gz` **bundle**, stored content-addressed
   in the artifact store, at an explicit **registration** event. The only
   filesystem→server registration path is `--agent PATH`, documented as
   *"Pre-register an agent from a directory **at startup**"*, and startup-only
   by construction.
2. `agents.bundle_location` names that artifact. It is a store key, never a
   filesystem path.
3. Sessions load through `AgentCache`, a two-tier cache — in-memory dict, then
   an extracted directory under `<artifact_location>/.cache/<agent_id>/` that
   **persists across restarts**. Both tiers are keyed on `agent_id` alone.
   `bundle_location` is consulted **only on a total miss of both**.

Consequence of (3), proven separately against the installed `AgentCache`: a
brand-new cache object over the same persistent cache directory — i.e. a
restart — still returns the stale spec. Only a cold cache reads
`bundle_location`. The paths that write `bundle_location` in-process
(`--agent`'s pre-register, and the HTTP update route) both call the cache's
warm-swap, so the happy path is coherent; a deploy that updates the database
and artifact store from a process that does not share that cache directory
would **report success and change nothing**. That is this project's signature
failure mode, sitting inside the mechanism this ADR is about to bless, and it
is why the deploy step below has a post-condition rather than a checklist.

**And this is not hypothetical here.** The `program` bundle the live server is
serving right now is byte-identical to a *dirty personal clone* on `main`
(`/home/nightshift/nightshift-runtime/program`, uncommitted), and differs from
both the deployed checkout and the branch tip — including
`blast_radius.gate_pushes`, which is `false` in what is running and `true` in
both git-side copies. `/srv/project` is clean at `27f3915`, so this is not
local drift. The org's constitution, as executing, corresponds to no reviewed
commit at all. Every agent writing this ADR and reviewing it is running it.

### What #107 was filed about, and where the damage actually is

`program/**` is **byte-identical** between the deployed commit `27f3915` and
the branch tip — verified by `staff` (B3) and unchanged since. The subtree
#107 was filed about is the one subtree that is currently correct.

The real delta in the 150-commit gap is elsewhere: `bin/nightshift-ctl`
(+595 lines), `bin/nightshift-workspace` (absent entirely from the deployed
checkout), `deploy/nightshift-heartbeat.sh` and its units (absent), and four
ADRs. The sharpest instance is a **stale tool, not a stale spec**: the
deployed copy of `bin/nightshift-ctl` contains **zero** occurrences of
`is-ancestor` against five at tip, so **ADR-0007's checkpoint-ancestry refusal
does not exist in the tool any agent working out of `/srv/project` actually
runs.** An accepted decision's enforcement is missing from the deployed tree.

So the old Context's claim that "tightening any control is currently a no-op
the org would record as shipped" was a correct *hazard* written as a statement
of *fact*. It has not happened, because no `program/**` commit is in the gap.
Something worse is happening next to it, and the first draft did not mention
it.

### The decomposition this forces

B1 splits #107 into two problems with **two different mechanisms**, and the
first draft's error was treating them as one:

- **Spec deployment** — how `program/agents/**` reaches the runtime. Answer:
  registration (`--agent`) plus a restart. `/srv/project` is irrelevant to it.
- **Deployed-checkout staleness** — how `bin/`, `deploy/` and `docs/` reach
  the agents that execute them. Answer: advance a checkout. No restart, no
  registration, and no relation to spec loading.

`staff` (B3, N3) asked that the mechanism be repo-wide rather than
`program/**`-scoped. The finding behind that is exactly right and is adopted;
the remedy is refined by B1: these cannot be one mechanism, because one of
them does not go through a checkout at all. Making them one would re-import
the confusion. They are separated below, and the second is explicitly
**not** gated on this ADR — which is also `staff`'s closing recommendation,
that H1 items be rechartered against the stale tool rather than against a
spec-deployment mechanism that currently has nothing to deploy.

### Constraints

1. **`--agent` is startup-only, so every spec deploy is a restart.** Not one
   restart at adoption — one per deploy, permanently. A running process's argv
   cannot be changed from outside it. Every agent in this org runs on that
   server; a restart is a scheduled outage that ends every in-flight run.
2. **No supported no-restart path exists for a template agent.** The only
   runtime bundle-replacement endpoint refuses them outright — template agents
   are read-only through it, and `program` is a template. This was checked
   because a no-restart path would dominate everything else. It is recorded as
   a constraint, not left as an open question, and it is the strongest
   candidate for an upstream ask.
3. **The shared checkout cannot be written under the agents in it.**
   `/srv/project` is where other agents read and write *right now*; moving its
   HEAD is #89/#98, which has silently reverted merged work **twice**.
4. `nightshift-ctl` is stdlib-only Python 3 by deliberate choice, and any
   deploy verb must work from a fresh clone with nothing but `python3` and
   `git`.

## Options considered

### G — Re-register the specs at server start with `--agent`, from a checkout no agent writes *(chosen)*

Keep a dedicated clone at a path no agent writes (say `/srv/specs`) at the
integration branch tip. Launch the server with `--agent /srv/specs/program`.
Advancing the clone is fast-forward-only; the deploy is the *restart that
re-registers*, and it asserts its own post-condition afterwards.

**Why it wins: it is the only option on this list whose mechanism exists.**
States 1–4 above eliminate every alternative that routes through a working
directory. `--agent` re-registers, warm-swaps both cache tiers, and bumps
`agents.version` — an observable that a post-condition can be written against.

**Against #89/#98:** the read path and the write path stop being the same
object, so a deploy cannot clobber an agent's working tree and an agent's
commit cannot be what "deploys" the constitution. Stronger here than under the
old Option A, because the read path is not a checkout at all — it is an
artifact registered from one.

**Costs, stated plainly, and they are worse than the first draft claimed:**
constraint 1 in full — a restart *per spec deploy*, forever, not once. A
second clone to keep and to remember to advance. A deploy step that is skipped
leaves the same divergence, which is why the detector ships first and alone.

**This option was never weighed in the first draft.** `staff` named it (B7,
Option G) as the thing that falsifies Option C's stated rejection reason. It
does more than that: it is the residual after everything else is eliminated.

### A — Repoint the server's working directory at a dedicated read-only checkout *(rejected: the mechanism is inert)*

The first draft's choice. A second clone at `/srv/specs`, the server launched
with its working directory there.

**Rejected on execution, not on judgement.** State 4: a server whose CWD
contained `program/agents/<seat>/config.yaml` served a different spec across a
restart. Repointing the CWD changes which specs load by **zero**. It would
have cost one scheduled outage — every in-flight run in the org — for nothing,
and the org would have recorded the constitution as deployed. Its
read/write-severance argument was real and survives, in Option G, which is A
with the working mechanism substituted for the broken one.

### B — A deploy step that advances the shared checkout `/srv/project` *(rejected twice over)*

Add a step that fetches and checks out the tip in `/srv/project` after every
`program/**` merge.

**Rejected, and it was the most dangerous option on the first draft's list
precisely because it was the cheapest.** It makes the automated,
every-merge case be the thing #89/#98 says destroys work: a scheduled checkout
walking over other agents' in-flight state, trading a silent-but-inert failure
for a silent-and-destructive one.

**And it does not even work.** Its stated payoff — "the server picks the new
specs up on the next fresh session" — is false (states 2 and 4; and the 21 live
sessions on this box share one cached spec rather than each reading one). It
would have paid #89/#98's price for no deployment.

**Expiry condition, per `staff` B7 — this rejection is not permanent and must
not be cited as though it were:** *B's destructive half is rejected while
`/srv/project` is writable by agent identities. If ADR-0009 Layer 1 ships and
makes it structurally unwritable, B and H must both be re-costed before this
ADR's step 3 is executed.* Its inertness (the other half) does not expire —
that is a property of the server, not of permissions.

### C — Serve specs from a pinned SHA, exported rather than checked out *(rejected alone; its discipline is adopted into G)*

Materialise specs at an explicit SHA — export the tree into a fresh directory,
swap a symlink atomically — and report that SHA.

**Rejected as a mechanism on its own: an export nothing registers is not
deployed.** Without `--agent` pointed at the result, C changes nothing (state
4). *With* `--agent`, C becomes a packaging choice inside Option G rather than
an alternative to it.

The first draft rejected C for a reason that does not survive this box —
"it still requires the server to be launched somewhere other than
`/srv/project`, so it pays constraint 1's restart in full and saves nothing" —
which `staff` (B7) correctly called out: the spec path is an *argument*, not a
working directory, so C never needed a CWD move. **The rejection reason was
wrong; the rejection stands for a different and stronger reason.** Recording
that distinction is the point of keeping rejected options.

What C buys — an explicit pinned SHA, an atomic swap, and a materialisation
that is not a git working tree and so cannot be "helpfully" committed into by
an agent who wanders in — is real, and **G adopts the pinning discipline.** G
keeps a real checkout rather than an export because a checkout answers
`git rev-parse HEAD` about itself, so the detector needs no side-channel file
that can disagree with the bytes next to it. (`staff` N5 notes a SHA-named
export directory plus `readlink` is nearly as good; agreed, and it is a fair
future substitution, not a reason to prefer C now.)

### D — Symlink `/srv/project/program` at a clean checkout's `program/` *(rejected)*

**Rejected, and now doubly.** It is a *write into the shared checkout*, which
constraint 3 forbids: git would see `program/` as deleted-and-replaced in every
agent's `git status`, a broad `git add` anywhere in the tree would try to
commit the symlink, and the next agent who "cleans up the unexpected diff"
reverts the deployment without knowing they did. It also would not have
deployed anything, for the same reason A does not.

### E — The server refuses to start when the specs diverge *(deferred, not rejected)*

Make divergence fatal at boot rather than merely loud.

**Deferred as premature.** Until G exists there is nothing a refusal could tell
an operator to *do*, and the condition is currently true — so shipping it today
means the server does not come back and every agent goes with it. Revisit as a
follow-up ADR once the aligned state is reachable and routine.

**One amendment forced by B1:** E must gate on the *registered bundle*, not on
the checkout. Gating a boot refusal on today's `spec status` comparison would
refuse to start over a checkout the server does not read, while a bundle
registered from someone's scratch directory sails through. See Consequences.

### F — Driver discipline alone *(rejected as the destination; adopted as the interim)*

#107 ask 4, binding on every driver right now: never record an agent-config
change as verified without executing against the agent afterwards.

**Rejected as the destination.** It is a rule enforced by memory, in a project
whose premise is that rules enforced by memory are the ones that fail — and it
failed here already, unnoticed, for the whole life of this run. Adopted as the
**interim** control until G lands, because it costs nothing and the detector
makes it checkable rather than remembered.

*(R-68: that sentence is an instruction addressed to agents, appearing inside a
decision document. It is recorded here as the org's adopted rule, which is what
it is. `staff` flagged the same thing at N6 and treated it as data rather than
as a command reaching them through reviewed material; that is the correct
handling and it is noted here so the next reader does the same.)*

### H — Adopt ADR-0009's endpoint instead of building a parallel one *(not rejected: sequenced ahead of this ADR's step 2)*

ADR-0009 (crew_alpha, proposed the same day) makes `/srv/project` structurally
unwritable by every agent identity, with a **named privileged maintenance
identity** that keeps it current. `staff` (B7) observed that this endpoint *is*
the first draft's Option A — "a read-only checkout that only a privileged
identity advances" — with no second checkout, no CWD change and no restart, and
that this ADR's reversal trigger #2 was a verbatim restatement of ADR-0009's
Layer 1 requirement. That is real duplication and the finding is accepted.

**B1 changes what H is worth, in both directions, and both must be said.**

- H does **not** substitute for G on spec deployment. ADR-0009 produces a
  current, unwritable *checkout*; the server does not read checkouts. Whatever
  ADR-0009 lands, specs still need `--agent` and still need a restart. The two
  ADRs are not building the same object after all — the first draft only
  appeared to be building ADR-0009's object because it was aimed at the wrong
  mechanism.
- H **does** substitute for the second half of the decomposition, and better
  than anything in this ADR. A current, privileged-maintenance `/srv/project`
  is precisely the fix for the stale `bin/nightshift-ctl` and the absent
  `bin/nightshift-workspace` — the damage that is actually live — and it needs
  no restart and no HEAD move by an agent.

**Therefore:** H is adopted for the checkout half, this ADR does not compete
for it, and **ADR-0009 Layer 1 should be sequenced before this ADR's step 2.**
If ADR-0009 lands first, `/srv/specs` may prove unnecessary: a `/srv/project`
that no agent can write is already a safe `--agent` source, and G collapses to
"add the flag and restart". That is a strictly better outcome than building a
second checkout, and the Program should prefer it if the sequencing allows.

### I — A no-restart deploy through the runtime bundle-replacement endpoint *(rejected: unavailable)*

Replace the registered bundle in the live process, skipping the restart
entirely. It would dominate every other option on cost.

**Rejected because the endpoint refuses.** Template agents are read-only
through it by an explicit guard, and `program` is a template. No supported
interface performs a no-restart deploy of a template agent. Recorded here so
the next reader does not re-derive it, and flagged as the best upstream ask
this ADR generates.

## Decision

**Option G, with Option H sequenced ahead of it, and the checkout half split
out of this ADR entirely.**

`program/**` reaches the runtime by being **re-registered from a checkout no
agent writes, via `--agent`, at server start** — not by any working directory,
symlink or export. The deployed identity is the **registered bundle**, and the
deploy is the restart that registers it.

Sequenced, because the order is the safety property:

1. **Ship the detector first, alone** — `nightshift-ctl spec status` (exit
   **4**) and the `spec` block in `dump`, including the `expected.fetched`
   freshness field that keeps `aligned` from being reachable from an unfetched
   clone. Done in the PR carrying this ADR. Read-only and reversible.
   **Scope, stated honestly:** it compares *deployed checkout* against
   *committed ref*. Given B1 that is **not** the comparison that proves a spec
   is deployed — see Consequences, and reversal trigger 4. The tool says this
   in its own output: every `reason` it emits names the two checkouts it
   compared and states that neither is necessarily what the running server
   serves, because an instrument that overstates what it observed is #107's
   defect occurring inside the detector built to catch it.
2. **Sequence ADR-0009 Layer 1.** If it lands, `/srv/project` becomes a safe
   `--agent` source and step 2b is unnecessary. This is the Program's call, not
   `platform`'s or crew_alpha's, and it is the reason this ADR's verdict is
   `needs-a-wider-decision`.
   **2b (only if ADR-0009 does not land first):** stand up `/srv/specs` and the
   advance step. Nothing points at it yet, so it is inert and independently
   reviewable.
3. **Add `--agent <spec-root>/program` to the server launch — one scheduled
   restart, at a wave boundary, announced.** Needs an owner's explicit
   go-ahead, not a worker's. **Two conditions, both blocking:**

   - **(B2) The control plane must be resolved explicitly before this runs.**
     ADR-0005 D1 resolves the plane by walking up from the process CWD
     (`discover_control_plane`, stopping at the `.git` ancestor). Any change to
     the server's environment that alters what inherited-CWD callers see makes
     every `nightshift-ctl` call that passes neither `--db` nor
     `NIGHTSHIFT_CTL_DB` resolve `control_plane_missing`. It fails closed,
     which is good — **but the recovery a driver reaches for at 03:00 is
     `init`, and that creates the phantom empty plane ADR-0005 exists to
     prevent.** Slots, claims and leases silently split across two databases is
     the failure the entire control plane was built to stop. *Therefore:*
     `NIGHTSHIFT_CTL_DB` is set explicitly in the server's environment as part
     of step 3, step 3 is not done until that is true and verified by running
     the tool with the server's environment and observing a resolved plane, and
     **`init` is not an acceptable response to `control_plane_missing` on this
     box.** Option G keeps the CWD at `/srv/project`, which narrows this
     considerably — but "narrowed" is not "gone", and it is written down rather
     than assumed.
   - **A post-condition, not a checklist.** After restarting, assert that the
     registered bundle's identity matches the SHA the spec root was advanced
     to, and that `agents.version` for `program` actually incremented when the
     content changed. A deploy step that cannot fail is the same object as no
     deploy step.

4. **Only then** consider Option E (refuse to start on divergence), as a
   separate ADR, gating on the registered bundle rather than on a checkout.

**Split out of this ADR, and not gated on it:** advancing the deployed checkout
so that agents stop running a 150-commit-old `bin/nightshift-ctl` without
ADR-0007's refusal, and a `bin/nightshift-workspace` that is not there at all.
That is the live damage, it needs no restart and no registration, and it should
be rechartered against ADR-0009 Layer 1 directly (`staff`'s recommendation, and
H above). H1 items 3, 8, 9, 10 and 15 should be re-pointed there rather than
sitting behind this ADR's step 3.

**H1 item 3 specifically cannot work as a `program/**` merge, and this is
settled rather than suspected.** There is exactly one non-builtin registered
agent on this box: `program`. The seats — `platform`, `staff`, `oncall`,
`release`, `security`, `product`, `ux`, `architect`, `crew_alpha`, `crew_beta`
— are **sub-agent directories inside that single bundle**, not independently
registered agents. Nothing scans a repository for new seats. Adding
`program/agents/<new>/config.yaml` and merging it registers nothing; it needs a
re-registration of the whole `program` agent and the restart above.

Steps 2–4 are **not** authorised by this ADR being merged. #89/#98's write half
remains open and is not addressed here.

## Consequences

**Easier.** "What constitution is this org actually running?" acquires a
checkable answer, and `program/**` acquires a definition of *deployed*
separate from *merged* — the distinction that did not exist and whose absence
is the entire defect. The deploy becomes a single observable event
(`agents.version` incrementing) rather than an assumption about a directory.

**Harder, and more so than the first draft admitted.** A restart *per spec
deploy*, permanently — not one at adoption. Steps 2b/3 may be unnecessary
depending on ADR-0009's sequencing, so this ADR cannot be executed
independently of another crew's.

**A gap this ADR creates and does not close.** The detector shipped in step 1
compares the deployed *checkout* against a committed ref. Per B1 the server
reads neither: it reads an artifact registered from whatever directory last
carried an `--agent` flag. So step 1's stated justification — that it "makes
every later step verifiable instead of asserted" — is **not yet earned**.
Verifying a `--agent` deploy requires comparing the *registered bundle* to the
committed ref, which is a different comparison than the one implemented. Today
that gap is not hypothetical: the running bundle matches a dirty personal clone
and neither git-side copy. The detector is still worth shipping — its
graph-derived divergence verdict is correct and its freshness field is now
fail-closed — but it must not be read as proof that specs are deployed, and
nothing should gate on it as though it were. **This is the honest version of a
claim the first draft made too confidently, and reversal trigger 4 is written
to fire on it.**

**Foreclosed.** The idea that `/srv/project` is *the* deployment — for specs it
never was. A fix for #89/#98 can move, repair or replace the shared checkout
without also being a spec deployment, **subject to whatever donor obligations
ADR-0009's clone topology places on it** (`staff` B6): ADR-0009 Layer 2
specifies reference clones *without* dissociation, so every writer clone
depends on `/srv/project`'s object store remaining present and readable.
Someone acting on "it is no longer the deployment" by replacing or pruning that
repository corrupts every in-flight writer clone, and the failure surfaces as
missing objects at push time — after the work is done. The read path and the
write path are severed for *spec loading*; the two **objects** remain coupled
through the object store. That coupling is ADR-0009's to sever or to keep, not
this ADR's to assume away.

## Cost

Zero marginal money. Operationally: possibly one additional clone (< 100 MB,
avoidable if ADR-0009 Layer 1 lands first), one advance step per `program/**`
merge, and **one restart per spec deploy, ongoing**. At 100× the volume the
shape is unchanged; the restart frequency scales with the number of
`program/**` merges, which is the cost that should decide whether an upstream
no-restart registration path is worth asking for (Option I).

## One-way door?

> **⚠ SUPERSEDED ON THIS POINT by [ADR-0013](ADR-0013-exact-sha-release-root-cutover.md) D6, confirmed by
> [ADR-0014](ADR-0014-adr-0013-corrections.md) C1. DO NOT ACT ON THE PARAGRAPH BELOW DURING A ROLLBACK.**
> Removing the flag and restarting **does not roll back**: the warm agent cache keeps serving the new
> bundle, so the launch line no longer determines what executes. Rollback is a **re-registration** of a
> pre-cutover bundle, built BEFORE the live window, and it is an incident-recovery exception identified by
> digest and snapshot identity — not a second authority source. See ADR-0013 D6.

**No.** Reversal is removing the flag from the launch line and restarting —
the same cost as adopting it, paid in the same currency. The detector is
independently reversible: a subcommand, a key in `dump`, and an exit code that
nothing yet gates on. Step 3 is the only expensive-to-undo step, and expensive
is not one-way.

## Scale tier

None yet. Single-box concern. It moves up the ladder the first time the org
runs more than one server, or against a repository that is not this one
(#102's H1) — at which point "the deployed spec" becomes per-server, and
because the deployed identity is a *registered bundle* rather than a checkout,
the reported value must carry which server registered it. The detector's output
already names the pid and root it observed, so the extension is additive.

## Reversal trigger

Revisit this decision if **any** of the following becomes observable:

1. **The deploy step is skipped in practice.** `spec status` reports
   `diverged` for more than one wave after step 3 lands. Owner: the wave-close
   driver, at wave close. That means the mechanism relies on a human
   remembering, which is Option F wearing G's clothes, and the answer is to
   automate the step or to adopt E.
2. **The dedicated spec root gets written to anyway.** `spec status` reports a
   non-empty `dirty` list on it, or its HEAD moves to something not on the
   integration branch. Read-only-by-convention has failed and it needs
   read-only-by-permission — which is ADR-0009 Layer 1, and is the second
   signal that these two ADRs should be one sequence.
3. **The restart proves unaffordable.** If step 3 cannot be scheduled within
   two waves because there is never a safe boundary. Owner: the wave-close
   driver, checked at wave close — an absence with nobody assigned to notice it
   is not a trigger. Then G's central cost is not payable in this org, and the
   answer is to ask upstream for a registration path that does not require a
   restart (Option I), because no rearrangement of checkouts can avoid it.
4. **The registered bundle and the committed ref disagree.** Compare the
   `program` agent's registered bundle against the spec root's HEAD after any
   deploy: if the bundle's content does not correspond to that commit, the
   deploy reported success and did not happen — the cache-coherency failure in
   Context. Owner: the deploy step itself, as its post-condition, on every run.

   *This replaces the first draft's trigger 4, which `staff` (B4) correctly
   called decoration: it fired only if somebody performed, unprompted, exactly
   the manual end-to-end experiment that #107 exists because nobody performs. A
   trigger conditioned on the org doing the thing it has never done will never
   fire. This one is a post-condition of a step that already has to run, and it
   fires on the first disagreement — including today's, where the running
   bundle corresponds to no commit at all.*
5. **`aligned` is ever reported from a clone whose expected ref was not
   fetched.** The freshness field shipped in step 1 should make this
   unreachable; if it is reached, the fail-closed rule has a hole and the
   detector's `diverged: false` has stopped meaning *proven*. Owner: whoever
   first gates CI or the heartbeat on exit 0, before doing so.
