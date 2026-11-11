# ADR-0011: The repository is authoritative over the running configuration, and a registration source must be a clean checkout of a reviewed commit

> **⚠ AMENDED IN SCOPE by [ADR-0013](ADR-0013-exact-sha-release-root-cutover.md) — clauses (a) and (c) —
> and by [ADR-0014](ADR-0014-adr-0013-corrections.md) C1. Neither core choice (clean source, startup
> registration) is reversed; read ADR-0013 D2 and D6 before relying on (a) or (c).**

Date: 2026-08-24 (revised 2026-08-24 after `staff` review on PR #117) · Status: **proposed**
Deciders: program (pending) · Proposed by: platform, wave-5b slot 139 · Revised by: platform, wave-5b slot 143
Conversation: e8a1abfc-98e9-4c26-b1bf-f046613c4338 (original), ffae77a0-be0d-48c2-b648-d1bf16f09068 (revision)
Spec: none — issue #115 is the spec. Refs #115, Refs #107, Refs #102.
Amends: ADR-0010 (`program`-spec deployment). **Does not supersede it** — see
*Relationship to ADR-0010*.

> **This ADR is a decision record.** It changes no configuration, restarts
> nothing, re-registers nothing, and edits no served bundle. The only artefact
> it authorises for implementation is a **detector** (§ Decision (c)), and even
> that is a follow-up task, not this PR — with a named owner and a named wave
> (`platform`, wave 6; § *Detector ownership and schedule*), because every gate
> on the eventual cutover routes through it. The divergence described below is
> deliberately left running for the duration of this ADR's review; § *What we
> gave up* says so in as many words.

> **Revision note (2026-08-24, after `staff`'s review on PR #117).** `staff`
> returned `consistent` on the decision itself and raised two blocking defects,
> neither of them in the decision: the deferral's end condition had no owner and
> could not start its own clock, and the interim control cited a rule the org had
> already recorded as false. Both are fixed — see § *Detector ownership and
> schedule*, reversal triggers 1 and 5, and § *Interim control*. Separately,
> `security`'s verdict on the `gate_pushes` field landed after the first draft; it
> does **not** overturn (d), and it is recorded in § *Update after `security`'s
> verdict* rather than folded silently into the text. One claim made in the first
> draft is falsified there and is corrected in place with the falsification left
> visible.

## Context

### What forced a decision now

While executing ADR-0010's B1 cost question on PR #113, `platform` (slot 133)
found that **the `program` agent bundle the live server is serving matches
neither the deployed checkout nor the repository tip. It is byte-identical to a
personal clone on `main` with an uncommitted working tree**
(`/home/nightshift/nightshift-runtime/program`), and differs from the deployed
checkout in **17** places.

The full receipts are on issue #115 and PR #113
(`#113#issuecomment-5389010909` and its addendum). **Everything in the next
subsection is proven by execution and is not re-argued here.** This ADR
consumes those findings; it does not re-derive them, and no reader should
re-run the experiments to trust it.

### What is settled

1. **The served spec is loaded through a two-tier cache keyed on `agent_id`
   alone** — an in-memory dict with no TTL and no eviction, then an extracted
   directory under `<artifact_location>/.cache/<agent_id>/`.
   `runtime/agent_cache.py:80-100`. **Neither tier is invalidated by a change
   to `bundle_location`**, which is consulted only on a total miss of both.
2. **A full restart does not deploy a changed spec.** The on-disk tier survives
   restarts. Proven by a four-state scratch-server experiment on its own port,
   database and artifact directory.
3. **The server's working directory has no bearing on which spec loads.** This
   falsifies the Context sentence ADR-0010 was originally costed against and
   renders ADR-0010's options A–D inert. ADR-0010 was revised to say so before
   it merged; this ADR does not have to re-establish it.
4. **On this configuration there is no code path from `program/agents/**` into
   the running server at all.** The live process (pid 3525266) carries zero
   pre-register flags and zero config flags. **Startup pre-registration is
   currently the only mechanism that moves `bundle_location`.**
5. The served bundle is byte-identical to the dirty personal clone and
   differs from the deployed checkout in 17 places.
6. **The divergence includes a safety setting.** Served
   `blast_radius.gate_pushes` is **`false`**; the deployed checkout and the
   repository tip are both **`true`**. The served bundle additionally carries
   `risky_action: "DENY"` and renames all six `crew_beta` sub-agents.

### Why this needs a decision and not a patch

Point 6 is the whole reason this is an ADR. The two sides do not merely differ
in age — **they disagree about how safe the org is**, and they disagree in
*both directions*:

| setting | served (running) | repo tip | which is stricter |
|---|---|---|---|
| `blast_radius.gate_pushes` | `false` | `true` | **repo** |
| `risky_action` | `"DENY"` | absent from the repo entirely | **neither — the served line is INERT.** See § *Update after `security`'s verdict*; this cell read "**served**" in the first draft and was wrong |
| six `crew_beta` sub-agent names | renamed (`eng_claude` → `beta_eng_claude`, …) | original | neither — structural |

A reconciliation in either direction therefore *relaxes something*, and a
reconciliation performed without adjudicating the fields individually will
relax something silently. That is the trap this ADR exists to disarm, and it is
why "just re-register from the tip" is not the safe answer it looks like.

The second reason is provenance. The running configuration's source is a
working tree with uncommitted modifications. It has **no commit, no author
record, no review, and no diff anyone can read**. It is also **not a stable
artefact**: the next `git checkout` or `git stash` in that clone changes what
"the running config" even was, retroactively and without trace. Whatever the
org decides, it cannot decide to trust that.

### What the org can currently detect

Nothing. This was found by hand, by accident, while chasing a different
question. #107's `spec status` detector (merged in PR #113) compares the
**deployed checkout** against a **committed ref** — and per point 4 above,
**the live server reads neither of them.** Both operands are correct; both are
irrelevant to what executes. Worse, the detector's `--spec-root` defaults to
the directory discovered from the running server's `/proc/<pid>/cwd`, and the
CWD is precisely the thing point 3 proves does not matter. The tool is honest
about this — every `reason` it emits names the two checkouts it compared and
states that neither is necessarily what the server serves — but honesty about a
blind spot is not coverage of it.

### Constraints on this decision

1. **Prefer the reversible option.** Detector before cutover; keep the existing
   path alive one more wave; add rather than rewrite.
2. **The cache must not be "fixed" by hand.** Copying files into
   `<artifact_location>/.cache/` would restore agreement, destroy the only
   surviving evidence of how this happened, and establish no deploy path.
   Deleting the cache directory is rejected for related reasons — see
   Decision (b).
3. **No restart, re-registration, signal or reconfiguration of the live server
   is authorised by this ADR**, and the served bundle is not to be edited.
   Every agent in this org runs on that process; a restart ends every in-flight
   run.
4. Any code this ADR proposes is a **follow-up task**. This PR is the decision
   record plus the detector specification in (c). "Follow-up task" is not the
   same as "unowned": (c) names the detector's implementing owner and its wave,
   because the cutover gates route through it.

## Options considered

The central question is (d) — reconciliation direction. Options **D1–D5** are
the real fork; **B-i/B-ii** and **C-i/C-iii** are the subordinate mechanism
choices for (b) and (c) and are recorded here because their rejected forms are
the ones a future reader will otherwise re-invent.

### D1 — The repository is authoritative; the running config is reconciled to it, sequenced behind a detector *(chosen)*

Declare, permanently, that **the deployed identity of `program/**` is a bundle
registered from a clean checkout of a commit reachable on the integration
ref**, and that any running configuration which does not correspond to such a
commit is *by definition* undeployed rather than *de facto* current. Then do
not cut over yet: ship the detector, adjudicate the 17 differences field by
field as ordinary reviewed PRs, and schedule the re-registration only once the
repository already expresses every difference the org intends to keep.

**Why it wins.** It is the only option that makes the safety-relevant half of
the divergence *reviewable*. The repo's `gate_pushes: true` is the stricter
value, so authority-to-the-repo tightens rather than loosens on the one field
that made this a P0 — though what the org ultimately *commits* for that field is
`security`'s call, and its verdict has since come back `false`-with-rationale
rather than `true` (§ *Update after `security`'s verdict*). The point here is
that either answer arrives through review. And it decides the *general* case, not just today's: once
"authoritative" has a default winner, the next divergence is a bug report
instead of a negotiation held at 03:00 by whoever is on shift.

**Why it is the reversible one.** Nothing about the running system changes when
this ADR merges. The instrument ships first and gates nothing. The adjudication
happens through the normal review path, which is inspectable and revertible per
field. The cutover is a separate, owned, scheduled act — and per ADR-0010 its
reversal is removing a flag and restarting, at the same cost as adopting it.

**What it costs.** The org keeps running `gate_pushes: false` while this is
reviewed. That is stated as a cost, owned, and routed — see § *What we gave up*.

### D2 — The running configuration is authoritative; the repo is reconciled to it *(rejected)*

Commit the served bundle's 17 differences to `program/**` so that the
repository describes what is actually executing.

**Rejected, on three independent grounds, any one of which is sufficient.**

- **It launders an unreviewed safety relaxation into a reviewed commit.** The
  central difference is `gate_pushes: false`. Adopting the running config means
  opening a PR whose effect is "turn off push gating", justified by the fact
  that push gating is already off. The reason it is off is that somebody's
  uncommitted working tree got registered. That is not a justification; it is
  the incident. A process that converts *how the org broke* into *what the org
  decided* has no failure mode left that it can detect.
- **The source is not an artefact you can adopt.** A dirty working tree has no
  commit and no author record, and its contents change under `git checkout`.
  "Reconcile to the running config" names a moving target with no provenance.
  Even a reader who *wanted* D2 could not execute it reproducibly.
- **It inverts the direction of every other control the org has built — and
  against ADR-0007 it is not merely worse than D1, it is incoherent.**
  ADR-0009's write guard and #107's detector both assume the repository is the
  thing that is true, and D2 would leave them enforcing the correctness of an
  artefact downstream of the running system rather than upstream of it. ADR-0007
  goes further than assuming it. Its refusal is *literally*
  `git merge-base --is-ancestor <sha> <ref>`
  (`ADR-0007-refuse-invalid-control-plane-input.md:102,166`): **reachability
  from the repository is its definition of valid.** Under D2 that control would
  be validating checkpoints against a ref that is downstream of the very thing
  it constrains — not a weakened control but a circular one, and circular in the
  same shape as the first bullet. D2 is therefore incoherent with a control
  proposed in this same branch, and no argument about the merits of the 17
  fields repairs that.

**What survives from D2, and it is not nothing.** Some of the 17 differences
may be *right*. D1 does not discard them — it requires them to arrive through
review, on their own merits, as proposals rather than as fait accompli. **The
distinction D1 draws is between adopting a value and adopting a source.** Values
are negotiable; the source is not.

The `crew_beta` renames may reflect a real structural intent and remain open.
The other candidate was `risky_action: "DENY"`, described in the first draft as
*stricter than the repo*; it has since been reviewed and the answer came back
**delete it, do not commit it** — it is unreachable under `gate_pushes: false`
and its only effect is to persuade a reader that a control exists (§ *Update
after `security`'s verdict*). That is not a retreat from this distinction. It is
the distinction doing its job on the very first field it was handed: a
served-only value went to review as a proposal, and review declined it.

### D3 — Reconcile immediately: re-register from the repository tip now *(rejected: irreversible in the wrong direction, and it silently relaxes)*

The apparently obvious repair — advance a spec root, restart with the
pre-register flag, done tonight.

**Rejected for four reasons.**

- ~~**It would loosen `risky_action`.**~~ **This reason was FALSIFIED after
  drafting, and is kept on the record rather than deleted.** The served
  `risky_action: "DENY"` is *inert*, not stricter: it is read only inside the
  `if gate_pushes and (…)` branch and is therefore unreachable while
  `gate_pushes` is `false`, so a wholesale cutover would not in fact loosen it
  (`security`'s verdict, § *Update after `security`'s verdict*). **D3 remains
  rejected on the three grounds below, each independently sufficient.** This
  bullet stays visible for the same reason ADR-0010 keeps Option C's wrong
  rejection reason: the served block *looks* hardened, and the next reader will
  otherwise re-derive this exact wrong argument from its appearance.
- **It renames six live sub-agents mid-flight.** The served bundle calls them
  `beta_eng_claude` and so on; the repo does not. Re-registering from the repo
  reverts those names underneath anything currently dispatching to them. The
  blast radius of that is unmeasured, and measuring it is a prerequisite, not
  a formality.
- **It costs a restart that ends every in-flight run in the org**, unscheduled,
  to fix a condition that has been live for at least a day and is now
  *observed*. An observed hazard with a named owner is not an emergency.
- **It destroys the diagnostic window.** The cache directory's mtimes and the
  bundle's exact contents are the only surviving evidence of how an unreviewed
  working tree became this org's constitution. They should be snapshotted
  read-only before anything overwrites them.

### D4 — Adopt neither; freeze both and escalate *(rejected as the destination; adopted in part as the interim)*

Declare the question above `platform`'s pay grade, change nothing, and wait.

**Rejected as a destination.** It leaves an unreviewed safety relaxation
running with no owner and no end condition, which is how this became a P0 in
the first place. An escalation with no decision attached is indistinguishable
from not noticing.

**Adopted in part.** The *freeze* half is right and is folded into D1: the
personal clone at `/home/nightshift/nightshift-runtime` should not be
`checkout`ed, `stash`ed or cleaned until its working tree has been captured,
because doing so destroys the only copy of the provenance. That is a request to
its owner, recorded here, not an action this ADR takes.

### D5 — No global authority rule; adjudicate every divergence case by case, forever *(rejected)*

Skip the abstraction. Decide the 17 differences and move on.

**Rejected because it is the status quo with paperwork.** Without a default
winner, every future divergence reopens the same question, and the answer
under time pressure is always "whatever is already running", because that is
the option that requires no restart. D5 *is* D2, arrived at by attrition. The
17 differences still get adjudicated under D1 — the difference is that D1 also
says what happens to the eighteenth.

### B-i — Invalidate the on-disk cache tier out of band (`rm -rf <artifact_location>/.cache/<agent_id>`) *(rejected)*

The intuitive answer to (b): if the disk tier is stale, delete it.

**Rejected, and the reason matters more than the rejection.**

- **It does not deploy anything by itself.** Tier 1 (the in-memory dict) still
  holds the stale spec in the live process, so the delete is inert until a
  restart.
- **After a restart it deploys whatever `bundle_location` names — which may be
  the bundle built from the personal clone.** Registration writes the artifact
  store and the database together, so if the last registration event was the
  one that produced today's served bundle, a cold-cache load reproduces exactly
  the configuration the operator was trying to remove, while *looking* like a
  successful repair. **The cache is why a fix on disk would not take. It is not
  why the wrong spec is running.** Anyone who treats (b) as the root cause will
  fix the cache and still be running `gate_pushes: false`.
- It is an out-of-band mutation that no post-condition can distinguish from a
  successful deploy, which is this project's signature failure mode.

Whether `bundle_location` in fact names the same content as the cache directory
is **not established by #115's receipts**, and the two answers imply different
remediations. Establishing it is a read-only lookup and is made a blocking
pre-condition in Decision (b).

### B-ii — Invalidate through the registration event itself, in the process that owns the cache *(chosen)*

See Decision (b).

### C-i — Extend #107's detector with a third operand: the served bundle *(chosen)*

See Decision (c).

### C-ii — Supersede #107's detector and replace it with a bundle-only comparison *(rejected)*

**Rejected on two counts.** Its two existing operands are correct and its
fail-closed freshness discipline (`aligned` is unreachable from an unfetched
clone) is exactly the property the new operand needs to inherit — a rewrite
would have to re-earn it. And replacing a detector that merged this wave is a
rewrite where an addition suffices, against constraint 1.

### C-iii — Drop the deployed-checkout comparison as irrelevant *(rejected)*

Since the server reads neither operand, remove them.

**Rejected: they detect a different live failure.** The deployed checkout is
what agents *execute tooling from*, and it is stale in a way that matters — the
deployed `bin/nightshift-ctl` contains **0** occurrences of `is-ancestor`
against **5** in `bin/nightshift-ctl` at the tip of the integration ref, so
**ADR-0007's checkpoint-ancestry refusal does not exist in the tool agents
actually run.** ADR-0010 recorded those counts; `staff` re-derived both of them
independently on this box while reviewing PR #117, so this rejection reason is
*verified on the running system*, not inherited from another document. It is
also the same predicate D2 would invert (see D2's third ground): the control
that is missing from the deployed tool is precisely the ancestry check, and the
operand that would detect its absence is the one C-iii proposes to remove.
Checkout staleness and served-spec divergence are two distinct failures, both
live today. Each needs its own operand. Conflating them is what produced the
blind spot in the first place.

## Decision

**D1**, with the reconciliation sequenced behind the detector in (c), and with
one rule added to ADR-0010's chosen mechanism.

### (a) The sanctioned path from a merged `program/**` change to a registered agent

ADR-0010's Option G is **ratified as the mechanism** and is not re-decided
here: `program/**` reaches the runtime by being **re-registered from a checkout
no agent writes, via the startup pre-register flag, at server start**. The
deployed identity is the **registered bundle**; the deploy is the restart that
registers it. Working directories, symlinks and exports are not deployment
mechanisms on this box (settled point 3).

**ADR-0011 adds the rule that this incident proves is missing.** ADR-0010
describes a sanctioned *source location*; it never states what makes a source
legitimate, and the thing that actually happened is precisely the thing it does
not say. Therefore:

> **A registration source must be a clean checkout of a commit reachable from
> the integration ref.** Registering the `program` agent from a working tree
> with uncommitted modifications, from a detached checkout of an unreachable
> commit, or from any directory that is not a git checkout at all, is
> prohibited — and where it cannot yet be prevented, it must be *detected*
> (see (c)) and reported as `unreviewed`, which is a louder verdict than
> `stale`.

The full sanctioned sequence, then:

1. The change merges to the integration branch through review.
2. The spec root — a checkout no agent writes — is advanced **fast-forward
   only** to that exact SHA. (`git merge --ff-only`. Hard resets are correctly
   refused on this box.)
3. The spec root is verified clean and its HEAD verified reachable from the
   integration ref, **before** the server is started.
4. The server is restarted with the pre-register flag pointed at the spec root,
   at an announced wave boundary, with an owner's explicit go-ahead.
5. **The deploy asserts a post-condition**: the registered bundle's content
   digest corresponds to the SHA from step 2, and `agents.version` for
   `program` incremented when the content changed. A deploy step that cannot
   fail is the same object as no deploy step.

Steps 2–5 are **not authorised by this ADR merging.** They inherit ADR-0010's
sequencing, including its dependency on ADR-0009 Layer 1 and its two blocking
conditions on the restart (explicit control-plane resolution; the post-condition
above).

**Contingency, because ADR-0010 is not accepted.** `ADR-0010:3` reads
`Status: **proposed**`. (a) therefore ratifies a mechanism in a document the
program has not yet accepted, and that ratification is **contingent on
ADR-0010's acceptance**: accept the pair in one act, or, if ADR-0010's Option G
is amended or rejected, (a) is amended with it rather than left ratifying a
mechanism that no longer exists.

### (b) How the on-disk cache tier is invalidated, given a bundle change alone does not do it

**It is invalidated by the registration event itself, performed in-process by
the server that owns the cache directory — and by nothing else.** A registration
warm-swaps *both* cache tiers together with the database and artifact-store
writes (`cli.py:1505-1518`). That is the only coherent invalidation on this box,
and it is the one the sanctioned path in (a) uses.

**Two code paths perform that warm-swap, and only one of them is open to
`program`.** Stating this precisely matters, because the imprecise version makes
this ADR *weaker* than the truth:

- **Startup pre-registration** — the (a) path. Open to `program`, and it costs a
  restart.
- **The runtime bundle-update route** (`server/routes/sessions/routes_agent.py:304-317`)
  performs the identical warm-swap — `artifact_store.put`, `agent_store.update`,
  then `agent_cache.replace` — with no restart. **It is deliberately closed to
  template agents.** `routes_agent.py:261-267` refuses any agent whose
  `session_id is None` with *"Built-in agents are read-only through this
  endpoint."*, under the comment *"Shared/template agents are read-only here"* —
  and every template agent, `program` included, has `session_id is None`. There
  is no other agent-bundle `PUT` in the installed tree: the cross-reference at
  `cli.py:1515` pointing at `omnigent/server/routes/agents.py:248` names **a
  file that does not exist in this version**, so a reader who follows it finds
  nothing and concludes the ADR was sloppy rather than that the route is shut.

**The accurate statement is the stronger one.** The no-restart warm-swap route
exists, works, and is closed to us *on purpose*. That is not a gap in this ADR's
research — it is the **reason (a)'s restart is unavoidable on this box**, and
the reason **ADR-0010's Option I (ask upstream for a no-restart registration
path for template agents) is the correct upstream ask** rather than a wish.
Reversal trigger 5's remedy is evidenced, not hopeful.

Three corollaries, all binding:

- **Out-of-band invalidation is prohibited.** No deleting the cache directory,
  no copying files into it, no touching mtimes. Option B-i above records why
  the intuitive delete is both inert and misleading.
- **A deploy performed by a process that does not share the server's cache
  directory will update the database and the artifact store and change
  nothing, while reporting success.** This is why (a) step 5 is a
  post-condition and not a checklist item. **Upstream documents this failure
  mode in its own source comments** (`server/app.py:445-452`): its
  evict-on-matching-hash path exists precisely because `AgentCache.load` is
  keyed by `agent_id` and *"trusts its in-memory / on-disk entry without
  checking `bundle_location`"*, naming *"a prior boot whose `replace` failed
  after `update` succeeded"* as the hazard. The corollary is the vendor's as
  well as ours, which makes it an acknowledged property of the runtime rather
  than an inference from this incident.
- **The cache is not the root cause and must not be treated as the fix.** The
  root cause is that `bundle_location` names a bundle built from an unreviewed
  working tree. Clearing the cache does not change that; it may re-serve it.

**Blocking, read-only pre-condition before any reconciliation is scheduled:**
record what `agents.bundle_location` names for `program`, and whether that
artifact's content matches the extracted cache directory currently being
served. Both answers are possible and they imply different remediations —
if they match, the registration event that produced today's state is the last
one and a re-registration fully replaces it; if they differ, there is a
*second* undeployed change already sitting in the database, and (a) step 5's
post-condition is the only thing that would have caught it. This lookup reads
the control plane and the artifact store and touches neither the live process
nor the served bundle.

### (c) How the org detects that the served spec differs from the committed one

**ADR-0011 extends #107's detector. It does not supersede its scope.** Said
plainly, because the distinction is load-bearing: `spec status`'s two existing
operands — deployed checkout and committed ref — remain correct, remain worth
reporting, and detect a *different* live failure (checkout staleness, e.g. the
missing `is-ancestor` refusal in the deployed tool). What is added is a **third
operand: the served bundle.**

Specified here, to be implemented as a follow-up task **by `platform` in
wave 6** — see § *Detector ownership and schedule* at the end of this
subsection, which is binding and is not a suggestion:

**New `served` block in `spec status` and in `dump`.** Read-only. Resolves, in
order, and fails closed at any step:

1. `agents.bundle_location` for `program` from the control plane / server
   database.
2. The extracted cache directory `<artifact_location>/.cache/<agent_id>/`.
3. A content digest over each — a stable hash of the tree taken over sorted
   relative paths and file contents, so the value is comparable to a digest
   taken over a `git` tree without either side being a working copy.

**Three-way verdict**, replacing today's two-way one:

| verdict | meaning |
|---|---|
| `aligned` | served digest corresponds to the `--expected-ref` commit |
| `stale` | served digest corresponds to some **older commit reachable from** `--expected-ref` |
| **`unreviewed`** | served digest corresponds to **no commit reachable from** `--expected-ref` |
| `undetermined` | any operand unreadable — never downgraded to `aligned` |

**`unreviewed` is the instrument this ADR actually exists to add.** Today's
failure is not staleness. A freshness detector would call a bundle that was
never in git "old" — the same word it uses for a bundle that is merely behind —
and the operator would triage it accordingly. **Provenance and recency are
different questions and today's incident is a provenance failure.** It gets its
own verdict and its own exit code, distinct from the existing divergence exit
code, so that anything which later gates on one can decline to gate on the
other.

Four further requirements, inherited or added:

- **Fail closed, and inherit #107's freshness rule.** `aligned` must remain
  unreachable from a clone whose copy of `--expected-ref` was not fetched. The
  detector never fetches.
- **Every `reason` names its operands**, per the existing tool's discipline —
  an instrument that overstates what it observed is #107's defect recurring
  inside the detector built to catch it.
- **It gates nothing on arrival.** Read-only, no exit-code dependency
  anywhere, for at least one wave. Reversible by deletion.
- **Cache-dir divergence is reported separately from bundle divergence.** If
  the extracted cache and the artifact named by `bundle_location` disagree,
  that is its own finding — it is the (b) pre-condition, made permanent.

#### Detector ownership and schedule — binding

| what | who | when |
|---|---|---|
| Implement the `served` block specified above in `spec status` and `dump` | **`platform`** — the seat that built #107's two-operand detector in PR #113 and therefore owns the `spec` surface of `bin/nightshift-ctl` | **wave 6**, as an ordinary reviewed PR onto `program/discovery-state` |
| Confirm at wave close that it was dispatched, and escalate if it was not | the **wave-close driver** | **wave-6 close** (reversal trigger 1) |

This table exists because the first draft left the detector as *"a follow-up
task"* with **no owner and no wave**, and **every gate on the cutover routes
through it**. `security` owns the `gate_pushes` *field*; the wave-close driver
owns triggers 1 and 5; nobody owned *the instrument*. The consequence is not
theoretical: reversal trigger 1 as first written could only fire *after* the
detector shipped, so an unbuilt detector did not fire the trigger — it silently
disarmed it, and a live unreviewed safety relaxation was left resting on a
single remaining backstop.

**The detector may be reassigned; it may not become unowned.** If `platform` is
not dispatchable in wave 6, the wave-close driver reassigns it to any warm seat
that did not author this ADR and records the reassignment in `.omni/decisions.md`
with the new wave. An unassigned row at wave-6 close is itself trigger 1 firing.

And the deferral is bounded **whether or not this table is honoured**: reversal
trigger 5's clock starts at this ADR's *merge* and runs on the calendar alone,
requiring nobody to remember it. That separation — an ownership commitment for
the normal path, plus a time-based backstop that needs no owner for the abnormal
one — is what makes this a deferral rather than drift.

### (d) Should the running config be reconciled to the repo, or the repo to the running config? — **THE REPO WINS**

**The repository is authoritative. The running configuration is reconciled to
it, not the reverse.** A configuration that does not correspond to a commit
reachable from the integration ref is **undeployed**, regardless of the fact
that it is what is executing. "Currently running" confers no authority.

The justification, in the order that decides it:

1. **The provenance is unreviewable and unstable.** The running config's source
   is a dirty working tree: no commit, no author, no diff, and contents that
   change silently under an ordinary `git` operation. There is no version of
   "adopt the running config" that is reproducible.
2. **The one field that made this a P0 is stricter in the repo.**
   `gate_pushes` is `true` in the repo and `false` in what runs. Repo-authority
   tightens the safety setting; running-config-authority would require the org
   to *ratify* an unreviewed relaxation, with the relaxation's own existence as
   the argument for it. That is circular in the specific way that removes the
   org's ability to detect its next incident. **This is an argument about the
   direction of authority, not a recommendation to deploy `true`.** `security`
   has since established that restoring `gate_pushes: true` would deadlock this
   org unattended, and the reconciliation it asks for is to commit **`false`,
   with its rationale, through review**. Repo-authority is what turns that into
   a decision instead of an accident; § *Update after `security`'s verdict*.
3. **Every other control the org has built assumes it.** ADR-0007's ancestry
   refusal, ADR-0009's write guard and #107's detector are all anchored on the
   repository being upstream of the runtime. Inverting that for one artefact
   leaves those controls verifying something downstream of the system they are
   meant to constrain.

**And the reconciliation is deferred, not immediate** — this is the reversible
half, and it is as much a part of the decision as the direction:

- **Direction is decided now. Content is decided through review.** The 17
  differences are adjudicated individually, as ordinary `program/**` PRs.
  Anything the org wants to keep from the running config is proposed on its
  merits and merged **before** any re-registration, so that the cutover is a
  no-op on intent and relaxes nothing by accident. **D1 adopts values from the
  running config where they are argued for; it never adopts the source.** The
  first field through this path was `risky_action: "DENY"` — described in the
  first draft as the live candidate because it looked stricter — and review came
  back **delete it, do not commit it**. See § *Update after `security`'s
  verdict*.
- **No cutover until (c)'s detector has been reporting for at least one wave.**
  The instrument precedes the change, so the cutover has a before-and-after and
  a post-condition rather than an assertion. **The detector has a named owner
  and a named wave** — `platform`, wave 6, § *Detector ownership and schedule* —
  because a gate that routes through an unowned artefact is not a gate, it is a
  hope with a deadline attached to nothing.
- **The `crew_beta` sub-agent renames are a blocking pre-condition, not a
  detail.** Six live sub-agent identities differ between the two sides.
  Whichever way that is settled, it is settled in the repository and its
  dispatch impact is measured **before** the restart, not discovered during it.
- **The evidence is snapshotted read-only before anything overwrites it**, and
  the personal clone's working tree should not be cleaned until then.
- **`gate_pushes` itself is routed to `security`**, per the wave-5b charter.
  This ADR decides the *authority question* and hands off the *field*; it does
  not duplicate that work or pre-empt its verdict. **That verdict has since
  arrived** (`#115#issuecomment-5389603403`). It does not overturn (d) and (d)
  is not rewritten for it; it is recorded in § *Update after `security`'s
  verdict*, including the trap that repo-authority must **not** be read as "put
  `true` back".

## What we gave up

Stated explicitly, because "prefer the reversible option" is a trade and not a
free win:

- **The org keeps running `blast_radius.gate_pushes: false` for at least one
  more wave.** That is a known, unreviewed relaxation of a safety control, left
  running deliberately, in exchange for not taking an unscheduled restart that
  would end every in-flight run and would revert six live sub-agent names
  underneath whatever is dispatching to them. This is the cost.

  What makes it a deferral rather than a shrug is that **all three of the
  following are named, not two**:

  1. **An owner for the field** — `security`. Verdict now delivered; § *Update
     after `security`'s verdict*.
  2. **An owner and a wave for the instrument its end condition depends on** —
     `platform`, **wave 6**; § *Detector ownership and schedule*. The first
     draft named neither, which meant the end condition was chained to an
     artefact nobody had to build.
  3. **A backstop that starts its own clock and needs no owner at all** —
     reversal trigger 5, **running from the merge commit of this ADR**, not from
     the detector's ship date and not from the moment somebody first discusses a
     cutover. A deferral whose primary end condition cannot start its own clock
     is indistinguishable from drift, and (2) alone would not have fixed that.

- **The immediate satisfaction of a repair.** The divergence stays visible and
  uncomfortable for the length of a review cycle.
- **The option of treating the running configuration as a legitimate
  baseline.** Foreclosed on purpose.

### Interim control, binding immediately

Until reconciliation lands, **the blast-radius guard is not enforcing the
recoverable-but-destructive tier on this box.** Seats must not assume it is
stopping a push to a protected ref — nor a `gh pr merge`, a `gh release`, a
`gh repo delete`, a `terraform`/`kubectl`/`helm` destroy, or an `rm -rf` of any
project or home path. `security` established that `gate_pushes` is misnamed and
switches that entire tier rather than pushes alone (§ *Update after `security`'s
verdict*). The unconditional DENY floor — force-push, `--mirror`/`--delete`
refspecs, hard reset, recursive `rm` of system paths — is unaffected and still
holds.

**What is holding the default branch, then, is a working agreement — and it must
be cited as one.** The live rule is `.omni/working-agreements.md:8-11`, owner
rule 1 (issue #6), binding on every wave and unretired:

> **1. The program never writes to the default branch.** Program-authored
> documents go to a branch and a PR like any other change; `release` remains the
> single integrator.

**Do not cite R-11 for this.** R-11 is not that rule and it is not current.
`.omni/decisions.md:202` reads *"**R-11. `platform` becomes the integrator for
the INTEGRATION BRANCH ONLY; `main` stays `release`-only.**"* — an **integrator**
rule — and the wave-2 retro records it as already false:
`.omni/sync/2026-08-23-retro-wave-2.md:65`, *"`platform` integrates onto
`program/discovery-state` ONLY (R-11)" is now false*, because `platform` was dead
(#67) and integration ran that wave under **R-32**, which loaned integration to
a warm crew seat (`.omni/decisions.md:904`). R-32 is not current either: its own
consequence line time-boxed it to wave 2, and **R-34** (`.omni/decisions.md:987`)
records *"R-11 is restored as written; R-32 stays lapsed and is NOT renewed."*
Three rules across three waves of one run, of which one has a retired half and
one is a lapsed loan. That churn is precisely why an interim safety control must
anchor on the owner rule, which retires only when the owner retires it, and not
on whichever seat currently happens to hold the integrator role. The seat name
is the volatile part; **"the program never writes to the default branch" is the
part that has not moved.**

**And the platform is not the second line of defence either — there is no second
line.** `.omni/tech-debt.md:11` records that **`main` has no branch protection
and cannot have it on this repo's plan** (403 Upgrade required), that the
"release is the sole default-branch writer" invariant is *"prose, not
mechanism"*, and that its trigger has **already fired** — three program commits
reached `main` with nothing to stop them. So `gate_pushes: false` is not the
first enforcement layer going missing; it is the **second of two, and both are
absent simultaneously.** Nothing mechanical stands between a seat and the
default branch right now. Owner rule 1 is what is there, it is convention, and
every seat is expected to hold it without being stopped.

This paragraph is written out in full because the gap between what agents
believe is enforced and what is enforced is the exact shape of the original
incident — and a control that cites a retired rule reopens that gap *inside the
paragraph written to close it*. A seat that does the responsible thing and looks
the citation up must land on something live.

*(R-68: the preceding paragraph is an instruction addressed to agents,
appearing inside a decision document. It is recorded as the org's adopted
interim rule, which is what it is, and is flagged so the next reader handles it
as data rather than as a command arriving through reviewed material.)*

## Update after `security`'s verdict on the `gate_pushes` field

`security` returned its verdict on #115 (`#115#issuecomment-5389603403`) after
this ADR was drafted. **It does not overturn (d)** — it independently reaches
D1's ground 1, in its own words: the defect is that *"an uncommitted working
tree in a personal clone that no one reviewed, that no backup covers, that no CI
gates, and that git cannot report on"* is the authority for this org's safety
policy, and that is *"unacceptable independently of what the value is"*. The
decision in (d) stands unchanged. Four things are sharpened, and one claim made
earlier in this document is falsified; both are recorded here rather than edited
away silently.

**1. `gate_pushes` is MISNAMED, and the misnaming is load-bearing.** It is not a
push gate. It is the master switch for the whole recoverable-but-destructive
tier: with it `false`, `gh repo delete`, `gh release`,
`terraform`/`kubectl`/`helm` destroy-and-delete, and `rm -rf` of any project or
home path fall through to ALLOW alongside ordinary pushes — `/home`, `/opt` and
`/root` are deliberately excluded from the system-path DENY set on the grounds
that they are *"scoped/recoverable and gated at the ASK tier instead"*, and that
ASK tier is exactly what the flag switches off. The **unconditional DENY floor is
unaffected**: force-push and `--force-with-lease`/`-f`/`+refspec`/`--mirror`/
`--delete`/`--prune`/`:refspec` pushes, `git reset --hard <remote>/…`, and
recursive `rm` of system paths are denied under either setting. This is why the
interim control above is written in terms of the tier and not in terms of pushes.

**2. `risky_action: "DENY"` is INERT, not stricter — and it should be DELETED,
not committed.** It is read at exactly one place, *inside* the
`if gate_pushes and (…)` branch, so while `gate_pushes` is `false` it is
unreachable; the served config is behaviourally identical to the same config with
that line removed. It also appears **nowhere in the repository**. It compounds
rather than offsets, because `"DENY"` reads as *hardened beyond the stock
default* (`risky_action: "ASK"`), so two lines that each look safe compose to
wide open — failing in the direction of false confidence, which is the worst
property a safety setting can have.

> **This falsifies a claim made twice above** — the divergence table's *"which is
> stricter: **served**"* cell, and D3's first rejection reason. Both are corrected
> in place with the original left visible and struck, on the same principle
> ADR-0010 applies to Option C: **a rejection reason that execution falsified is
> the highest-value line in a decision record, and deleting it invites the next
> author to re-derive it.** D3 remains rejected on its three surviving grounds.
> D1's value-versus-source distinction is not weakened by this; it is
> *demonstrated*. `risky_action` was the live candidate for "a served-only value
> worth keeping on its merits", it went to review as a proposal, and review said
> delete it. That is D1 working exactly as specified on the first field it was
> handed.

**3. Restoring `gate_pushes: true` would DEADLOCK this org, and this ADR does not
ask for it.** Under `true` with the stock `risky_action: "ASK"`, every `git push`
and every `gh pr merge` returns ASK against `ask_timeout: 86400` with no human
present — each call parked for twenty-four hours. The root config's own comment
says as much: *"an ASK with nobody to answer it parks the turn for `ask_timeout`
and wastes a day."* `true` has never actually run on this box and would fail on
the first push. **The value `false` is operationally correct; the process that
produced it was the defect.** (d) is a claim about *which artefact is
authoritative*, not a claim that the repo's current value wins — and the
reconciliation `security` asks for is to **commit `false` at the root with its
rationale inline**, plus a second policy that unconditionally DENYs the
never-routine members of the tier (`gh repo delete`, `gh release`, infra
`destroy`/`delete`, `rm -rf` outside the caller's worktree), which costs nothing
unattended because the answer is always no. **Reversal trigger 6 below
pre-recorded precisely this outcome as D1 working as specified rather than as
precedent for D2. It has now fired, and the pre-recording holds.**

**4. `blast_radius` gates SHELL TOOLS ONLY — recorded as a standing limitation of
the control.** It classifies `sys_os_shell`, `Bash`, `bash`, `Shell`, `terminal`
and `developer__shell`, and nothing else. A merge or a deletion performed through
the GitHub REST API or any non-shell tool is outside it **under either setting**.
So `gate_pushes: true` was never the complete control the repo believes it is,
and the eventual reconciliation must not be written up as if restoring a value
closes the API path. This is a property of the control, not a consequence of the
divergence, and it survives the cutover.

**Referenced, explicitly not decided here.** `security`'s §3 finding that **only
ROOT policies load** — a seat session carries the parent bundle's `agent_id` and
the policy engine is built from `spec.guardrails.policies` alone with no
sub-agent traversal, so every per-seat policy block is dead config, including
`read_only_os` on the reviewer and `security` seats and `worktree_guard` on the
workers. That is **issue #118**. It is a distinct defect from this one, it is not
caused by the divergence and would not be cured by the cutover, and **this ADR
does not decide it.** It is named here only because it changes what the interim
control above can assume: a seat documented as read-only is not policy-constrained
today either.

## Relationship to ADR-0010

**ADR-0011 amends ADR-0010. It does not supersede it, and ADR-0010's status
does not change.**

Superseding would be wrong on the facts and destructive on the record.
ADR-0010 was *revised before merge* around the same falsification this ADR
inherits (settled point 3): its Context already reports the four-state
experiment, and its options A–D are already recorded there as rejected
*because the mechanism is inert*. That rejected-options record — including the
distinction that Option C's original rejection reason was wrong while the
rejection stood for a stronger one — is the most valuable part of the document
and the part a supersession would bury.

What ADR-0011 changes in ADR-0010, precisely:

1. **Adds the provenance rule to Option G** (Decision (a)). ADR-0010 specifies
   a source *location* that no agent writes; it never specifies what makes a
   source *legitimate*. This incident is a legitimate-location failure only by
   luck — the actual source was a personal clone — and the rule is added by
   name.
2. **Replaces step 1's detector scope with the three-operand version**
   (Decision (c)). ADR-0010's Consequences already flag this as "a gap this ADR
   creates and does not close" and its reversal trigger 4 was written to fire
   on it. ADR-0011 closes it by addition; ADR-0010's two operands are retained.
3. **Answers the reconciliation question ADR-0010 left entirely open**
   (Decision (d)). ADR-0010 records the divergence in its Context and does not
   decide what to do about it. That is this ADR's central contribution.

Everything else in ADR-0010 stands unamended: Option G as the mechanism, the
ADR-0009 Layer 1 sequencing, the two blocking conditions on the restart, the
Option E deferral, Option I as the best upstream ask, and the split of the
deployed-checkout problem out of the spec-deployment problem.

**ADR-0010 should carry a forward link to this ADR**, added in the PR that
carries it, so the pair is navigable from either end.

## Consequences

**Easier.** "Which constitution is this org running, and was it ever reviewed?"
becomes a question with a checkable answer, and — via the `unreviewed` verdict
— one that distinguishes *behind* from *never was*. Future divergences arrive
as bug reports against a stated rule rather than as open questions. The
sanctioned deploy path acquires a legitimacy criterion, not just an address.

**Harder.** Every `program/**` change now carries a restart (inherited from
ADR-0010, unchanged), and now also a provenance check on the source. The 17
differences must each be argued rather than swept up in one cutover, which is
more review work than a single re-registration would have been, and is the
point.

**Foreclosed.** That "what is running" can be a source of truth about what the
org decided. And that a cache repair can be mistaken for a deploy.

**Not closed by this ADR.** #89/#98's write half. The deployed-checkout
staleness (ADR-0010 splits it out; it belongs against ADR-0009 Layer 1). The
`gate_pushes` field decision itself, which is `security`'s — its verdict is in
and its four required amendments are `program/**` work, not this PR. Whether the
six `crew_beta` renames are wanted, which needs a dispatch-impact measurement
nobody has taken. And **issue #118** — only root policies load, so every per-seat
policy block is dead config — which is a separate defect, referenced in § *Update
after `security`'s verdict* and deliberately not implemented or decided here.

## Cost

Zero marginal money. Operationally: one additional detector subcommand block to
maintain, reading two things it did not read before (the control-plane row and
the artifact store). Everything else is inherited from ADR-0010 and is not
new spend attributable here. At 100× the volume the shape is unchanged; the
served-bundle comparison becomes per-server, which is additive because the
detector already names the pid and root it observed.

## One-way door?

**No, in both halves.** The authority rule is a sentence in a document and is
reversible by a later ADR that argues the other way — its cost is argument, not
migration. The detector is a subcommand, a `dump` key and an exit code that
nothing gates on, reversible by deletion. The reconciliation cutover is
ADR-0010's step 3 and inherits its reversal cost: remove the flag, restart.

The only genuinely irreversible act in this area is **destroying the
provenance evidence** — cleaning the personal clone's working tree, or
overwriting the served cache. This ADR authorises neither and asks explicitly
that neither happen before a read-only snapshot exists.

## Scale tier

None yet. Single-box concern. It moves up the first time this org runs more
than one server, or against a repository that is not this one (#102's H1), at
which point "the served spec" becomes per-server and the `unreviewed` verdict
must carry *which* server served it.

## Reversal trigger

Revisit this decision if **any** of the following becomes observable:

1. **Either the detector reports `unreviewed` for more than one wave after it
   ships, OR it has not shipped by wave-6 close.** Owner: the wave-close driver,
   at wave close. **The second clause is the fix for a defect in the first
   draft**: as originally written this trigger could only fire *after* a ship
   date that nobody was obliged to create, so an unbuilt detector did not fire
   it — it silently disarmed it. The detector's owner and wave are now named
   (`platform`, wave 6; § *Detector ownership and schedule*), and failing to
   ship on that schedule is itself the trigger. Either clause reads the same
   way: the direction was recorded and never executed, the decision is
   decoration. The answer is to escalate to a scheduled cutover with a named
   owner and a date, not to wait another wave.
2. **A `program/**` PR proposes a served-only value with no justification
   beyond "this is what is running."** Owner: whoever reviews `program/**`.
   That is D2 arriving by increments, and the authority rule is being laundered
   one field at a time. The remedy is to require the value be argued on its
   merits or dropped.
3. **The (a) step-5 post-condition fails once** — the registered bundle's
   digest does not correspond to the spec root's SHA after a deploy. Owner: the
   deploy step itself, every run. That means the deploy reported success and
   changed nothing, and (b) is not settled after all.
4. **A bundle is ever registered from a path other than the sanctioned spec
   root.** Owner: the detector, via the `unreviewed` verdict. The provenance
   rule is unenforced by construction and needs permission-level enforcement,
   which is ADR-0009 Layer 1 — the third independent signal that these ADRs
   should be one sequence.
5. **The reconciliation cutover has not been scheduled within two waves of
   THIS ADR'S MERGE.** Owner: the wave-close driver, checked at wave close — an
   absence with nobody assigned to notice it is not a trigger.

   **The clock starts at the merge commit of PR #117 and runs from that
   moment.** Not at the detector's ship date. Not when a cutover is first
   discussed. Not at any later event that somebody has to schedule first.
   Concretely: **if no reconciliation cutover has both a named owner and a date
   by the second wave close after this ADR merges, this trigger has fired** —
   and it fires on the calendar, whether or not the detector exists, whether or
   not anyone remembered, and whether or not the ownership table in (c) was
   honoured.

   It is written this way deliberately. The first draft chained every end
   condition to the detector's ship date, which meant a deferral whose primary
   end condition could not start its own clock — the failure mode this ADR names
   in its own D4 rejection, *"an escalation with no decision attached is
   indistinguishable from not noticing"*, arriving through the back door.
   A backstop that needs an owner to fire is not a backstop.

   When it fires: the restart cost is not payable in this org, and the answer is
   ADR-0010's **Option I** (ask upstream for a no-restart registration path for
   template agents) — which (b) now evidences rather than merely proposes, since
   the runtime warm-swap route demonstrably exists and is closed to template
   agents by an explicit guard. Not further deferral.
6. **`gate_pushes` is resolved in `security`'s favour of `false`.** Then the
   repo, not the runtime, is what changes — which is D1 working exactly as
   specified, and is recorded here so that outcome is not mistaken for D2 and
   used as precedent for it. **This has now happened** (§ *Update after
   `security`'s verdict*): `false` is the operationally correct value, `true`
   would deadlock the org unattended, and the repo is what moves — with the
   rationale committed inline and the lost coverage replaced by a separate
   unconditional-DENY policy. **The pre-recording holds and is hereby invoked:
   this is D1 working, not precedent for D2.** What was wrong was never the
   value; it was that an unreviewed working tree got to decide it.
