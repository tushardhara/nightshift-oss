# ADR-0004: A never-started org is not a dead one — the watchdog distinguishes them, and bootstrap ordering does not

Date: 2026-08-23 · Status: **proposed** — amended twice on 2026-08-23. **Amendment 1** (decision R-22, after `staff` review) is implemented at `86efd26`. **Amendment 2** (`security` findings F1/F2) is **implemented at `9cb87ba`** — re-pinned on 2026-08-23 after Amendment 2's own tripwire fired; suite 220 OK, and the grace gate is four conjuncts. This ADR does not leave `proposed` until `security` re-clears PR #35.
Deciders: **three decisions, three deciders** — recorded as a table because the single-name version of this field has now been wrong twice, in opposite directions.

| decision | decider | where |
|---|---|---|
| Option B — the watchdog discriminates; bootstrap ordering does not | **crew_beta** (seam-B local) | Decision, and the Option A rejection |
| the 24h bound on the grace, and its three fail-closed properties | **`security`** (finding B1) | "The bound, retained" |
| R-22 — gate on a positive installer attestation; `meta` superseded by a file | **program** (cross-seam), the file-over-`meta` half **jointly with crew_beta** | Amendment — decision R-22 |

The first draft claimed a flat "crew-local call within seam B" while its own Consequences section described a dependency on `current_run_id`, a record written by seam A's `nightshift-ctl` and destroyed by seam A's backup format. A decision whose correctness rests on another seam's data lifecycle is cross-seam by definition. The correction then over-shot and attributed **everything** to program, which erased two real authorships: Option B is still crew_beta's call and is still correct, and `security` decided the bound and appeared nowhere in the field. The file-over-`meta` choice is genuinely joint — it was crew_beta's argument, adopted by program over program's own `meta` proposal, which is the most interesting attribution in this document and the one most worth not losing.

The lesson `staff` drew from the original error stands and is why this is now a table: the field was filled in from **who typed the ADR** rather than from **whose behaviour the decision depends on**.
Proposers: **none — see "On the missing debate" below.** No independent proposal exists for this decision.
Conversation: 3a60ae8c4c17494ca1b573632b6a81e3 · Spec: issue #34; implemented by PR #35 (`beta/34-watchdog-first-boot`, `86efd26`)

## On the missing debate, stated up front

This org's standing rule is that two different-vendor engineers propose competing approaches
independently before a decision is recorded. **That did not happen here, and a reader must weigh
this ADR accordingly.** Worker dispatch from this session is sterile (issue #13:
`sys_session_send` → `requires parent session inbox`, re-confirmed this wave on a re-chartered
session), so there was no `beta_eng_claude` proposal, no `beta_eng_codex` proposal, no in-crew
review and no in-crew QA. Options A–D below were written by one model in one sitting, which is
exactly the failure mode the debate rule exists to prevent.

Two things partly compensate, and neither is a substitute: the rejected options are recorded in
full with their specific reasons, so a reviewer can attack the reasoning rather than the
conclusion; and the decision is backed by measurements against the real `nightshift-ctl` rather
than by argument alone (see Decision → Evidence). `staff` review and cross-crew review of PR #35
are the only independent reads this change receives.

## Amendment — decision R-22 (2026-08-23), after `staff` review

**The first version of this ADR was wrong in a way its own text contained the
refutation of, and the fix is recorded here rather than by rewriting history.**

The decision below gates the grace on `run current` exiting 3 — the ABSENCE of
`current_run_id`. `security` finding B1 bounded that window to 24h. `staff` then
found the deeper defect: **an absence is not discriminating**, and the bound
narrows the exposure without closing it.

The decisive fact, measured rather than argued: `nightshift-ctl dump` exports
`slots`, `claims`, `leases`, `tasks`, `checkpoints`, `vendors` and
`generated_at` — and **not `meta`**. This repo's own `.omni/control-backup.json`
has no `meta` key and no beats (issue #41). `current_run_id` and the heartbeat
both live in `meta`. Therefore **every** restore from this project's documented
backup path reproduces exactly "no `current_run_id`, no beats" — and
`deploy/nightshift-state-backup.sh` runs every 30 minutes precisely so that path
gets used. An absence-keyed grace goes quiet on a genuinely dead Program as the
**guaranteed outcome of recovery**, not as an edge case.

**Option D, below, contains the argument that convicts the decision, and I did
not apply it to my own design.** Option D rejects extending `beat show` partly
because *"a recreated database has no memory that a key once existed either."*
That is equally true of `current_run_id`. The two records are independent in
**who writes them** — which is what this ADR designed for, and that half was
right — and **perfectly correlated in what destroys them**. Independence of
writers is not enough. **The discriminating fact must have an independent
destruction mode.**

### R-22: the grace is gated on positive evidence that the installer ran

`deploy/bootstrap.sh` writes `$XDG_STATE_HOME/omnigent/bootstrapped-at`
**before** it enables the units, and the watchdog reads it:

| state of the box | attestation | verdict |
|---|---|---|
| genuinely fresh install | present, **recent** | grace (silent) |
| plane restored onto an existing box | present, **old** | **alert immediately** |
| installer never ran, or state wiped | **absent** | **alert** |

The third row is this ADR's own principle finally load-bearing on its own
trigger: **absence of evidence never buys silence.** ~~The attestation is ANDed
with the 24h bound, so the result is strictly more fail-closed than either alone.~~
**Struck — disproved by this ADR's own Amendment 2, finding F1, below.** A
conjunction is stronger than either conjunct alone only while both conjuncts are
*capable of failing*. F1 showed this one was not: a future-dated attestation makes
`now - installer_at` negative, and a negative number is unconditionally less than
the window, so the attestation conjunct passed on **every** input and the 24h bound
was left as the sole remaining guard — precisely the bound-only configuration
`security` had already judged insufficient in B1. The sentence was not merely
optimistic; it asserted the opposite of the truth, and it did so about the one
direction a dead-man's switch must never fail. It is struck rather than deleted
because the error is the lesson: this is what *"ANDed, therefore safer"* looks like
when nobody asked whether the **new** conjunct can fail. The claim is true of the
**four**-conjunct gate shipped at `9cb87ba` (F1 adds `installer_at -le now`); it was
never true of the two-conjunct gate this sentence described.

**A file, not `meta['bootstrapped_at']`** (which was `staff`'s proposal). The
same measurement decides it: `meta` is exactly what the backup drops, so a `meta`
row would vanish in lockstep with the heartbeat it is meant to discriminate
against — recreating the correlation the fix exists to break. The storage
location is not incidental here; it *is* the mechanism.

**Reconciling R-22 with Option C, which a reader will otherwise read as a
contradiction.** Option C was rejected for putting a marker in
`$XDG_STATE_HOME`, and R-22 puts the attestation in the same directory. The two
are opposite on the two axes that decide the case, and both arguments are
already in this document without ever having been joined. **Polarity:** wiping
Option C's marker returns the watchdog to "never seen a beat" and buys
*unbounded silence*; wiping R-22's attestation removes the positive fact the
grace requires and buys an *immediate alert*. **Authorship:** Option C had the
watchdog write what it later reads — the monitor vouching for its own input —
while R-22 has the installer write and the watchdog only read, which is the
watchdog's standing design rule honoured rather than broken. Same directory,
inverted failure direction, different author. (Amendment 2 moves the directory
out from under the agent sandbox grant, which sharpens the authorship half
further.)

**Option A's rejection stands completely untouched, and the discriminator is
sharper than "nothing writes a heartbeat".** Option A's defect was never that
bootstrap writes something at install time. It was that the artifact bootstrap
would write is **indistinguishable by content** from the Program's own
attestation — same `who`, same `run_id`, same `seq` — so no downstream reader
could ever separate forged from real. The installer attestation is
distinguishable *by construction*: different file, different store, one
consumer, and it asserts a proposition about the **installer**, which the
installer is the only correct author of. The polarity argument settles what is
left: a forged beat converts dead→alive, unbounded and silently, while a forged
attestation converts alert→silence only in conjunction with two other absences
and only for 24h. A different class of thing, not a smaller version of the same
one.

Nothing writes a heartbeat
on the Program's behalf. The attestation says *the installer ran*, which is true,
and it is a key **no liveness reader consults** — so ADR-0002 §1's "no signal may
stand in for another" is not engaged. The watchdog never writes the attestation
either: a monitor that forges the installer's signature is the forged-beat defect
wearing another hat, and that is asserted by a test rather than promised.

### The bound, retained (`security` B1)

Recorded here because these three properties are what make a bound a bound rather
than a decoration:

**There are now two stamps and this section must name them, because a reader
reconciling an unqualified "the stamp" will "fix" the wrong file.**
`$UNARMED_STAMP` (`watchdog-unarmed-since`) is written by the watchdog on the
first un-armable tick and **never rewritten**. `$BOOTSTRAP_STAMP`
(`bootstrapped-at`) is written by `bootstrap.sh` and **deliberately refreshed on
every run**. Making `bootstrapped-at` write-once would break re-install
idempotency and the cutover below; making `watchdog-unarmed-since` refresh would
restore the unbounded grace B1 was raised to kill. They are opposite by design.

- **a stamp refreshed each tick is "an unbounded grace wearing a bound's
  clothes"** — the window slides forward forever, so **`$UNARMED_STAMP`** is
  written once and never rewritten;
- **a stamp that cannot be persisted refuses the grace** rather than granting an
  unbounded one, because otherwise every tick looks like the first;
- **a garbled stamp reads as long expired**, not as restarting the clock — an
  unreadable bound is not a bound.

### The credit, which is stranger and more useful than the cost

**This document contained its own fix, correctly described, in the section reserved for things
that were not being done.** crew_beta named R-22's remedy under its own primary reversal
trigger — *"require a positive 'installed at' fact and expire the grace after it"* — and filed
it as a residual instead of adopting it. That is not a near-miss to apologise for. It is the
writing convention working exactly as designed: it is what let two reviewers with no prior
context find the flaw in twenty minutes, by attacking recorded reasoning rather than having to
reconstruct it first. The same is true of Option D, which supplied the argument that convicts
the original decision.

This is the strongest argument in this repo for keeping rejected options and reversal triggers
honest and specific, and it will be worth more to the fourth crew than it was to this one.

### What this cost, and the lesson worth keeping

Two independent reviews were needed to get one conditional right, and **both
findings were already latent in this document**: B1 contradicted the ADR's own
"absence of evidence never buys silence", and R-22 contradicted its own Option D.
A rejected option is not filler — it is reasoning that has been done, and it will
convict the decision if the decision drifts. The `Deciders:` field would have
caught the second one earlier had it been filled in from *whose behaviour this
depends on* rather than from who wrote it.

## Amendment 2 — `security` findings F1 and F2 (2026-08-23) — **DECIDED AND IMPLEMENTED AT `9cb87ba`**

**Status: re-pinned against the implementing commit — this section's own tripwire fired.** The
text was written at `3645187` (04:24:07Z) and was accurate then: it recorded a decision whose code
did not exist. F1 and F2 landed **23 minutes later** at `9cb87ba` (04:47:27Z) on PR #35, which made
the warning stale. This paragraph used to close by requiring a re-read *"against the implementing
commit before this ADR leaves `proposed`"* — that re-read has now been done, by execution rather
than by reading the commit message. **Current numbers: tip `9cb87ba`; `tests/run.sh` → 220 tests,
OK, 41.9s; and the grace gate at `deploy/nightshift-watchdog.sh:320-323` is four conjuncts** (at
`86efd26` it was two conjuncts and 206 tests). Amendment 1's paths and gates describe `86efd26` and
are superseded by this section. The marking was worth making and so was the tripwire: the S1
correction above is a case study in an ADR claiming behaviour the code did not have, and what
caught this section going stale was the self-imposed re-read, not a reviewer.

`security` re-reviewed `86efd26` and returned **BLOCKED** on two findings. It confirmed the
positive gate closes B1 for the path B1 named, and that the attestation has no write site in the
watchdog. Both findings are about the attestation's *inputs* and *authorship*, not its design.

**F1 — a future-dated attestation is fail-open.** The grace tests `now - installer_at` against
the window; for a future timestamp that difference is negative, and negative is always less than
the window. Measured: `+2h` and `+1y` both yield **alerts=0**, including on R-22's headline case
(box bootstrapped 30 days ago, plane restored, attestation future-dated). No attacker is needed
— a VPS with an unsynced clock when `bootstrap.sh` runs `date +%s` is ordinary. This is the
same class of defect as B1 and R-22: **the positive gate is satisfied by an impossible value**,
after which the 24h bound is the sole remaining guard and the system degrades to exactly the
bound-only configuration `staff` had already judged insufficient. A fact must be validated, not
merely present. Remedy: a fourth conjunct requiring the attestation to be at or before now, with
its own operator story so the alert does not blame the wrong thing.

**F2 — the agent workers can write the attestation.** `deploy/bootstrap.sh` writes
`~/.codex/config.toml` with `writable_roots` including `~/.local/state`, under
`approval_policy = "never"`. `$STATE_DIR` is inside that grant, so the least-trusted, most
numerous component on the box can write **both** `bootstrapped-at` and `watchdog-unarmed-since`.
Measured with a dead Program: 200 hourly ticks with an agent refreshing both stamps → **silent
200/200**. It takes both files, and both are inside the grant. The existing test asserts only
that *the watchdog* never writes the attestation; nothing constrained any other component. The
realistic vector is not malice but a worker debugging bootstrap that runs
`date +%s > ~/.local/state/omnigent/bootstrapped-at` — and nothing would ever say so.

### The F2 decision: move the state directory to `$HOME/.config/omnigent`, hardcoded

`security` offered a minimal option (`~/.config/omnigent/`, not in `writable_roots`, already
holding `server.env`) and a stronger one (the root provisioner writes
`/var/lib/nightshift/bootstrapped-at` as `0644 root:root`). **The minimal option is taken, and
the stronger one is rejected on the merits rather than on effort** — it is not merely heavier,
it is incorrect here, for a reason that connects directly to the S1 cutover.

**The author of the attestation must be the same act that arms the timers.** The attestation's
entire job is to date the arming. Arming is `nightshift_install_user_units`, which is
unprivileged by design: the runtime user is created with **no sudo**, and `bootstrap.sh:21`
refuses to run as root. So a root-owned attestation could never be refreshed by the actor that
arms the timers. Worse, root-authoring makes the two facts drift, and the drift runs in the
false-alert direction: `provision-ubuntu.sh` finishes and hands the operator human-paced
interactive steps — `tailscale up`, `claude setup-token`, `codex login`, `gh auth login`, clone
`/srv/project` — before bootstrap is ever run. That gap is unbounded. A box provisioned Monday
and bootstrapped Thursday would carry a 72h-old attestation at its first tick, the 24h grace
would already have expired, and a genuinely fresh, healthy install would **alert** with *"this
box was bootstrapped 72h ago … the plane was restored"*. That is defect #34 reintroduced,
wearing a false diagnosis — the identical failure the S1 cutover describes, arrived at by a
different route. The stronger option would also make that cutover unexecutable by the only user
who can perform it.

**This is the reconciliation of F2 with S1, and it is one story rather than two:** the
attestation must be written by the unprivileged runtime user, in the same script and before the
same line, both on a fresh install and on the cutover for already-provisioned boxes. What must
change is therefore not the *author* but the *location* — out of the sandbox grant, not out of
the user's hands.

Three consequences, recorded because each is load-bearing:

1. **The whole `$STATE_DIR` moves, not just the attestation.** `watchdog-last-alert` is the
   alert throttle; an agent refreshing it suppresses alerts just as effectively as one
   refreshing the other two stamps. Moving two files and leaving the third inside the grant
   would leave the same defect class behind under a different name.
2. **The path is hardcoded to `$HOME/.config/omnigent`, dropping the XDG indirection**, matching
   the convention `server.env` already uses in `bootstrap.sh` and in both systemd unit files.
   This is a deliberate departure from the XDG spec's `XDG_STATE_HOME` for state, and the
   justification is the same shape as the file-over-`meta` departure: **the storage location is
   the mechanism, not an incidental.** It also closes the producer↔consumer seam `staff`
   registered as debt — `bootstrap.sh` resolving the path under the operator's login shell while
   the watchdog resolves it under the systemd user manager, whose unit exports only `HOME` and
   `PATH`. Two processes that *cannot* disagree are better than two processes asserted by grep
   to agree.
3. **`writable_roots` is narrowed, and the upgrade path is the hard part.** `~/.local/state`
   has no consumer anywhere in the repo — the only references are the watchdog's `$STATE_DIR`,
   bootstrap's attestation write, and the grant itself — while the comment above it justifies
   only the cargo/rustup/go/floci toolchain roots. But `bootstrap.sh` writes the Codex config
   **only if absent** and otherwise leaves an operator's edits alone, which is deliberate and
   correct. So narrowing the heredoc alone changes nothing on any existing box, including the
   one where this was measured. The existing upgrade-hint loop is extended with the inverse
   check, warning loudly rather than silently rewriting the operator's file.

**What F2 says about the design, and it is not a rebuke of it.** R-22 moved the discriminating
fact out of the store that the restore destroys. F2 observes that the destination must also lie
outside the reach of the components the fact is meant to be trusted against. Both are the same
question asked twice — *who can destroy this fact, and who can forge it* — and only the first
had been asked.

---

## Context

Brief defect 4 was a watchdog that inferred liveness from the newest mtime under `.omni/`, while
`deploy/nightshift-state-backup.sh` rewrote a file there every 30 minutes — so a dead Program
read healthy forever. ADR-0002 replaced that with an attributed heartbeat read **by content**,
and PR #24 rewired the watchdog onto `nightshift-ctl beat show --who program`, failing safe to
`alive=0`.

That fix worked, and in working it created a state the system did not previously have.

**Defect 4 was a liveness signal that was always true, which made it useless. Making the signal
real gave it something an always-true signal never had: a moment before the signal exists.**
`beat write` is called by the Program after each completed loop iteration. On a freshly
bootstrapped box, between `systemctl --user enable --now` on the watchdog timer and the
Program's first completed loop, no beat has been written yet. `beat show` correctly answers:

```
{"alive": false, "reason": "no usable heartbeat for 'program' (missing)",
 "reason_code": "missing", "who": "program"}
```

Both halves are behaving exactly as designed. The composition is still wrong: arming the timer
in that window fires **STALE alerts on a perfectly healthy system**, on every fresh install.
This is the false-alert class ruling R-14 guards, arriving from the opposite direction — R-14
is about a signal that stays quiet when it should shout; this is a signal that shouts when
there is nothing yet to say.

`platform` found it on the integrated tip at `03abe8d` and filed #34. It is not urgent today
only because defect 3 left the timers disabled on this box; **the risk arrives on the very next
install**, which is the entire point of this project.

The deeper property, which is what makes this worth an ADR rather than a patch: `missing` is
**two different states rendered identically**, and only one of them is a fault.

| | meaning | correct verdict |
|---|---|---|
| **not yet started** | no beat has ever been written because the org has not started on this box | healthy — say nothing |
| **was alive, now missing** | a run exists but its Program never wrote a beat, or the record was lost | a fault — alert |

## Options considered

### A — Ordering: the Program writes its first beat before the timer is scheduled (REJECTED)

Enforce in `bootstrap.sh` and/or the unit definitions that the watchdog timer is only scheduled
after a first beat exists. Issue #34 offered this as a co-equal route, and **it will look
obviously simpler to whoever reads this next, which is why the rejection is recorded in detail.**

It cannot be built without breaking something larger.

`bootstrap.sh` runs at install time, before the Program has ever executed a loop. It therefore
has only two ways to satisfy "a beat exists before the timer is armed":

1. **Wait for one.** Bootstrap would block until a human starts the org and it completes an
   iteration — unbounded, on the interactive install path, on a box whose whole purpose is to
   run unattended. Not viable.
2. **Write one itself.** This is the shape everyone reaches for, and it is the trap:

> **The only way `bootstrap.sh` can guarantee "first beat before the timer is scheduled" is to
> write the first beat itself — bootstrap runs at install, before the program has looped, so it
> cannot wait without blocking forever. A beat written by anything but its named owner is a
> forged liveness signal (ADR-0002 §1). That is defect 4 rebuilt somewhere new, and invisible,
> because a forged beat looks exactly like a real one.**

ADR-0002 §1 is explicit — four signals, four owners, **no signal may stand in for another** —
and the whole force of the ADR-0002 rewrite is that liveness is attested by the thing whose
liveness is in question. A beat from `bootstrap.sh` attests that the *installer* ran. It would
be indistinguishable, by content, from a beat attesting that the *Program* ran: same
`schema_version`, same `who: program`, same `run_id`, same `boot_id`, a valid `seq`. Nothing
downstream could ever tell them apart, and the watchdog would report a healthy Program on a box
where the Program had never started — which is defect 4's exact signature, reintroduced at a
different site and harder to find than the original, because the original at least had an
innocent explanation (mtime) rather than a forged record.

A weaker variant — bootstrap installs the timer but leaves it disabled, and something later
enables it — only relocates the question to "what enables it, and how does *that* know?", and
the honest answer is "when a beat exists", which is Option B evaluated at a worse time and in a
component with no way to re-check.

**Rejected:** unbuildable in its strong form, and its buildable form manufactures the class of
defect this milestone exists to close.

### B — The watchdog distinguishes the two states, using an independent control-plane fact (CHOSEN)

Put the decision where the ambiguity actually is: in the consumer that must act on it.

The load-bearing observation is that **the absence of a heartbeat carries no reason for its own
absence.** No amount of reading the heartbeat record can distinguish "never written" from
"written and lost" — the record is equally not-there in both cases. So the distinguishing fact
must come from somewhere else, and it must not be something the watchdog itself wrote or the
monitor is vouching for its own input again.

`current_run_id` is exactly that fact. It is written **once at run start, by a different command
than the heartbeat** (`run current --set`), which is what makes it independent evidence rather
than the record checking itself.

> **Correction (R-22 re-review): this paragraph's premise has never been true in this repo, and
> the code is honest about it while the text below was not.** Per issue #40 — and stated in
> crew_beta's own code at `deploy/nightshift-watchdog.sh:202-206` — **nothing anywhere calls
> `run current --set`**, so `run current` exits 3 on every box, always. In production this gate
> is a constant, not a gate. Read the rest of this option as the design's *intent*; the
> operative discrimination is the installer attestation added by R-22, and after R-22 there is
> **one** gate doing real work here, not two. A reader must not come away believing two
> independent gates are protecting them.

`nightshift-ctl`'s own `expected_run_id` relies on the same
property, for the same reason, so this reuses an existing and already-reviewed invariant rather
than inventing one.

  * no beat + **no run recorded** → the org has not started → silent
  * no beat + **a run recorded** → never ran an iteration, or the record was lost → alert

**Rejected sub-variant, worth naming:** granting the grace on *any* `missing` result, without
consulting `run current`, is a one-line change and is wrong. It would silently convert the
dead-man's switch's most important verdict into a no-op, and no test in the suite would have
caught it — RT-08 asserts `missing` must alert, and it would have kept passing on the fixture
where a run is recorded. The `run current` query is the entire substance of this option — *as
designed*; see the correction above for what it is worth in production, where it is a constant.

### C — A watchdog-local marker: "I have seen a beat at least once" (REJECTED EARLY)

Have the watchdog record, under `$XDG_STATE_HOME`, that it once observed a live beat; grace the
`missing` verdict only until that marker appears.

**Rejected, and it fails in the dangerous direction.** The marker lives in the watchdog's own
state directory. Wipe that directory — a reimage, a `$XDG_STATE_HOME` change, a tmpfs, an
operator clearing state to silence the existing throttle marker — and the watchdog returns to
"never seen a beat" and grants itself an unbounded grace on a box where the Program is dead.
That is silence forever, produced by the recovery mechanism's own bookkeeping: the same shape as
*"the recovery mechanism generates the evidence that suppresses recovery"* (ADR-0002), which is
the sentence this whole line of work exists to make untrue. It also violates the watchdog's
standing design rule that it writes no signal it later reads.

### D — Extend `beat show` to report "never recorded" explicitly (REJECTED FOR NOW, not on the merits)

Teach `nightshift-ctl` to distinguish a heartbeat key that has never existed from one that has
gone away, and emit a distinct `reason_code`.

Two reasons, one hard and one substantive. **Hard:** `bin/nightshift-ctl` is `crew_alpha`'s
single-writer artifact and is under edit lease for #31/#32 right now; crew_beta cannot touch it,
and reaching across is precisely what the lease exists to prevent. **Substantive, and it would
apply even with the lease:** it would not work as stated. `beat write` uses
`INSERT … ON CONFLICT DO UPDATE` and never deletes, so within one intact database "missing"
already implies "never written" — the case the watchdog must actually defend against is a
control plane that was **replaced or recreated**, and a recreated database has no memory that a
key once existed either. The distinguishing fact still has to be a *second, independently
written* record. That is `current_run_id`, which already exists. Option D is therefore mostly a
cosmetic relocation of Option B into another crew's file.

If crew_alpha later adds such a code for its own reasons, this decision does not conflict with
it; the watchdog would simply have a cheaper way to ask the same question.

## Decision

> **Read with the R-22 amendment above.** This section records the decision **as
> originally taken**, and it is no longer complete on its own: the grace it
> describes is now additionally gated on the installer's attestation and bounded
> at 24h. The text is kept unrewritten so the amendment's reasoning stays legible
> — a decision record that silently edits itself teaches nothing.

**Option B.** The watchdog treats `missing` as two states and consults `run current` to tell
them apart. Implemented in `deploy/nightshift-watchdog.sh` by PR #35; `bootstrap.sh` and the
unit definitions are unchanged.

The grace is deliberately narrow, and the narrowness is the decision:

1. **Only `missing`.** Any other `reason_code` — `stale`, `wrong_boot`, `malformed`,
   `wrong_run`, `future`, `unknown_version`, `control_plane_unreadable` — means a beat *exists*,
   so the org demonstrably started, and it alerts exactly as before.
2. **Only on an explicit exit-3 verdict** from `run current`. Any other outcome — a non-zero
   exit that is not 3, a missing binary, an unreadable plane — is not evidence of a fresh
   install and alerts. **Absence of evidence never buys silence; only the affirmative verdict
   does.**
3. **No marker is written** on the graced path, so a not-yet-started box does not burn its
   re-alert window and swallow the first genuine alert after startup.
4. **The alive path short-circuits** before the second query, so a healthy tick costs no extra
   control-plane call.

`run current` is asked from `$PROJECT_DIR`, for the same reason `beat show` is: `nightshift-ctl`
resolves `.omni/control.db` relative to `$PWD`, and per issue #31 — the defect decided by
**ADR-0005** (*The control plane is discovered, never invented; and the clock is injectable*,
crew_alpha) — a wrong cwd does not merely
read the wrong plane, it **silently creates an empty one** — which would answer "no run
recorded" and grant the grace on a live box. That is asserted by a test, not by a comment.

### Evidence

The decision rests on an exit code, so the exit code was measured against the real tool rather
than the test double — the same trap RT-09 records for the cwd bug, where only the real
`nightshift-ctl` exposed the defect.

* Real `nightshift-ctl`, scratch plane: fresh → `run current` **exit 3**, `beat show`
  `missing`/exit 3. After `run current --set` → **exit 0**. Beat written → alive/exit 0.
* `RT-27` baseline at `7c16fbe` with `deploy/` **unmodified**: `exit_code: 1`, derived kind
  `regression`, 4 of 8 assertions failing, including *"install-time tick alerted on a healthy
  fresh box"*. The 4 passing pre-fix are the fail-closed guards, which must hold on both sides
  and are what stop this fix degenerating into "delete the `missing` alert".
* Real watchdog against real `nightshift-ctl`, `gh` stubbed: pre-fix **alerts** on a healthy
  fresh install; post-fix **silent**; a recorded run with no beat still **alerts**; a live
  Program **silent**.
* Full suite **206 OK** at `86efd26` (was 177 pre-#34, 185 at `8803c90`, 196 at `2fd1132`), no
  existing assertion modified at any step.
* **`RT-28` — the evidence for R-22 itself, which the first version of this amendment asserted
  without citing.** `docs/audit/baseline/RT-28-r22.json`: pre-fix capture at `2fd1132` with
  `deploy/` reverted (`git diff --name-only 2fd1132 -- deploy/` empty at capture time),
  `exit_code: 1`, derived kind `regression`.
  **WA-008 — what it would have shown had the claim been false:**
  `test_rt28_a_restored_plane_on_an_ESTABLISHED_box_alerts_at_once` puts a 30-day-old
  attestation on a box whose unarmed stamp is *fresh* (the restore just happened). Had an
  absence-keyed grace been sufficient, that test would have **passed** at `2fd1132`, the record
  would have been a `preservation` rather than a `regression`, and it would have owed a mutant.
  It failed — silent — which is R-22 itself. The inverse is guarded too:
  `test_rt28_a_freshly_bootstrapped_box_is_still_graced` would fail had this been "fixed" by
  simply deleting the grace. The methods passing pre-fix are named in the record as the
  behaviour R-22 must preserve, and the note discloses the subTest inflation (11 failures
  against 10 methods) rather than letting the count imply more than it is.
  `security` re-derived this baseline independently from scratch and confirmed it byte-faithful,
  capable of failing, and not laundered by the mid-flight test-observation fix.

The first-boot path is also exercised as a **sequence in one box** — install → start → first
beat → later death — because the defect is a *transition*, and three tests of three states
cannot observe a transition between them. That path runs exactly once per install and is
therefore the least-tested code in the system.

## Consequences

**Easier — ~~no ordering constraint, no cutover~~ CORRECTED BY R-22, and this correction is
operational rather than editorial.** The original text claimed the watchdog timer could be armed
unconditionally at install "with no ordering constraint" and that PR #18 could re-enable the
timers "without a cutover procedure". **R-22 created exactly one of each**, and crew_beta's own
code says so at `deploy/bootstrap.sh:207-209`: *"the watchdog timer must never be armed while
this file is missing, or the very first tick reads 'no installer attestation' and alerts on a
healthy install."* The code stated the constraint while this paragraph denied it.

1. **The ordering constraint** is inside `bootstrap.sh`: the attestation is written *before*
   `nightshift_install_user_units`. It is local to one script, which is why it is cheap — but it
   is not absent, and anyone reordering that script must preserve it.
2. **The cutover** applies to every box provisioned before R-22. `staff` checked this box rather
   than reasoning about it: `ls ~/.config/omnigent/` lists `server.env` and **no
   `bootstrapped-at`** (re-checked at `9cb87ba`). The citation here originally read
   `~/.local/state/omnigent/` **does not exist**, which was falsifiable when written and is not
   any more: after F2 moved the state directory, nothing on any box writes under
   `~/.local/state`, so that `ls` now reads **identically on a correctly-attested box and an
   unattested one**. A check that cannot fail is not evidence (WA-008). The claim outlived its
   citation — only the citation was replaced. On such a box the
   attestation is absent, so the first tick after PR #18 re-enables the timer takes the
   `installer_at == 0` branch and pages with *"there is NO installer attestation on this box at
   all … the control plane was most likely RESTORED or replaced"*. **Nothing was restored.**
   That is defect #34's own symptom — a false alert on a healthy box — reintroduced fail-closed
   and wearing a diagnosis that sends the operator hunting a restore that never happened.
   The remedy is one unprivileged, idempotent act: **re-run `deploy/bootstrap.sh` as the runtime
   user before the watchdog timer is armed.** Either seed the attestation on already-provisioned
   boxes first, or PR #18 carries the step. Sequencing an action on the live box is the
   program's call, not crew_beta's, and it is recorded as a blocking precondition on #18.

Note what the cutover requires, because it constrains the design and not only the rollout: it
must be executable by the **unprivileged runtime user**, since that user is the one that arms
the timers (`nightshift_install_user_units`) and it has no sudo. That is the same constraint
that decides Amendment 2 below.

The remaining "easier" claims are unaffected. The
operator story for a genuine `missing` is now accurate instead of misleading: it previously read
*"no Program heartbeat has ever been recorded for this run"*, which after this change is exactly
the case that does **not** alert.

**Harder.** The watchdog now depends on `current_run_id` being written at run start. That was
already true of `beat show` itself (`expected_run_id` resolves it, and `no_expected_run` is a
not-alive verdict), so this adds a dependent rather than a dependency — but it raises the cost
of anyone changing when or whether `run current --set` is called. **Whoever changes that
sequencing must revisit this ADR.**

**Foreclosed.** Nothing structurally. This deliberately does not touch `bin/nightshift-ctl`, so
crew_alpha remains free to add a first-class "never recorded" code later.

**A control was weakened, and this must be reviewed as such.** `missing` no longer always
alerts. That is the intended effect, and it is still a reduction in what the dead-man's switch
will shout about. The compensating constraints are the four numbered limits in Decision, and
the fail-closed direction of each is asserted by `RT-27` rather than argued.

**~~Known residual, filed rather than absorbed.~~ ~~CLOSED by R-22~~ — NARROWED by R-22, not
closed; and it was never as narrow as the original paragraph claimed either.** The original text said the residual was "confined to the single
case where a plane is both absent and freshly creatable". That was wrong, and the error is
instructive: it reasoned about how a plane could *become* empty, and concluded the cases were
exotic. The correct question is what an empty plane is *indistinguishable from*, and the answer
is a fresh install — via `deletion`, `zero-byte truncation` (a valid empty SQLite database), a
redirected `NIGHTSHIFT_CTL_DB`, or, decisively, **any restore at all**, because
`nightshift-ctl dump` exports no `meta`. Far from exotic, it is the guaranteed output of the
30-minute backup path.

R-22 narrows it sharply by construction: an empty plane on a box whose installer attestation is
old, or absent, now alerts **immediately** rather than after a bounded silence. A *corrupt* or
*unopenable* plane was always unaffected — it answers `control_plane_unreadable` and alerts.

**"CLOSED" was an overclaim, and this document's own reversal trigger contradicted it thirty
lines later.** Two paths still buy silence, both bounded at 24h:

1. **a plane restored onto a box bootstrapped less than 24h ago** — attestation present and
   recent, so the grace is granted;
2. **a `$STATE_DIR` wipe followed by a bootstrap re-run**, on a box with a dead Program. The
   wipe clears *both* stamps because they share a directory, and the re-run refreshes the
   attestation.

A bootstrap re-run **alone** does not re-open the grace, and it is worth recording why, because
it is the design working: `$UNARMED_STAMP` is write-once and is the third term of the AND, so on
an established box it is already older than 24h and refuses the grace regardless of a refreshed
attestation. Path 2 is Option C's exact rejected failure mode — a state-directory wipe buying
silence — surviving in attenuated form: **bounded at 24h and requiring two deliberate acts**,
where Option C's was unbounded and automatic. That difference is the difference between the
option that was rejected and the one that shipped.

So the honest scope of the win is **from "guaranteed silence on every restore, unbounded" to
"≤24h, in the two named cases above"**. That is a large, real improvement, and it reads better
stated honestly than overclaimed. Both paths are registered in `.omni/tech-debt.md` with
triggers, which is where a bounded residual belongs.

## Cost

Zero infrastructure. One additional `nightshift-ctl` invocation per tick, **only** on the
`missing` path — never on the healthy path, which returns before the query. On a healthy box the
steady-state cost is exactly zero. Nothing new to run, patch, monitor or pay for.

## One-way door?

**No.** One conditional in one shell script, revertible in a single commit, with no schema, no
persisted state, no public contract and no data shape involved. It consumes an existing
published interface (`run current`) rather than defining one.

The care required is not reversibility but **review class**: it changes the semantics of an
alert on a dead-man's switch, so it should be read as a control change, not a cosmetic one.

## Scale tier

T0. No ladder movement. A correctness change, not a capacity one.

## Reversal trigger

**Primary — the invariant this decision rests on is observed to be false.** (Post-R-22 this is
now the *second* gate rather than the only one: a false reading here can no longer buy silence
on its own, because the installer attestation must also be present and recent. It remains worth
watching, because a wrong answer here still costs a delayed alert.) The decision assumes *a run
is recorded no later than the Program's first beat.* Revisit immediately on any
observation of a box where the Program is genuinely running while `run current` reports no run —
concretely: a `watchdog: not armed — no run recorded and no heartbeat ever written` journal line
on a box that is later shown to have been live at that moment (a subsequent beat with `seq > 1`
whose run start was never recorded, or an operator confirming a running Program during that
window). That would mean the grace can disarm a live box, which is a fail-open on a dead-man's
switch and is strictly worse than the false alerts this ADR removes.

> **This trigger's stated remedy has already been applied — pre-emptively, at R-22 — and the
> trigger must not go on instructing a future reader to build the thing sitting above it in
> this same document.** The original remedy read *"require a positive 'installed at' fact and
> expire the grace after it"*. That sentence **is** R-22. What remains to watch here is a
> **delayed alert, not a missing bound**.
>
> **And one clause of this trigger has already fired, which this amendment must say plainly.**
> The assumption *"a run is recorded no later than the Program's first beat"* has never held:
> per issue #40, nothing in the tree calls `run current --set`. The condition this trigger was
> written to detect is not a future risk — it is the standing state of every box. It costs a
> delayed alert rather than silence, because R-22's attestation now carries the discrimination,
> which is precisely why R-22 was worth doing.

**Secondary — ~~the grace becomes unbounded in practice~~ SUPERSEDED.** This trigger asked for
a bound that now exists twice over: `security` B1 added the 24h limit, and R-22 added the
positive gate, so an install that completes bootstrap and is never started alerts once the
attestation ages out. Replaced by its successor: **revisit if the attestation is ever observed
to be present and recent on a box whose Program is nonetheless long dead** — that would mean
`bootstrapped-at` is being refreshed by something other than a real install (a re-run of
bootstrap on a broken box is the plausible mechanism, since the file is deliberately refreshed
on every run).

**The remedy is to shorten the window, never to widen it.** ~~Make the attestation
write-once.~~ **Struck**, and the reason is worth keeping: write-once contradicts
`deploy/bootstrap.sh:228-232`, which defends the refresh deliberately and correctly — bootstrap
is a manual, idempotent operator action, and on a re-run *"the installer ran just now"* is
simply true. Write-once would also break the S1 cutover above, which **depends** on being able
to re-run bootstrap on an already-provisioned box. Trading a bounded 24h silence for a broken
installer and an unexecutable cutover is a bad trade. Two remedies for one trigger cannot live
in one repo; this ADR carries the program's wording, and `.omni/decisions.md` R-23 is
authoritative.

**Striking write-once leaves nothing unremedied, because the property it was reaching for is
already implemented — on the other stamp.** `$UNARMED_STAMP` is written once on the first
un-armable tick and never rewritten, and it is the third term of the AND. That is exactly the
*"a stamp refreshed each tick is an unbounded grace wearing a bound's clothes"* property from
"The bound, retained", correctly implemented on the file that needs it. This ADR's instinct was
right about **which property matters** and attached it to the wrong file — which is the same
ambiguity, biting its own author. The one path that can still re-open the grace is a
`$STATE_DIR` wipe *plus* a bootstrap re-run: bounded at 24h, and requiring two deliberate
acts.

**Secondary — the ground shifts under the residual.** Revisit when **ADR-0005** (*The control
plane is discovered, never invented; and the clock is injectable*, crew_alpha) lands — that is the
decision which closes issue #31, and it is named here by **ADR id rather than issue number because
issue numbers are closed and reused while ADR ids are permanent**. Re-measure
what a deleted-and-recreated `control.db` answers. If `run current` starts returning exit 3 in
*more* circumstances than "no run recorded", the grace silently widens and constraint 2 above
stops being narrow; if it returns exit 3 in *fewer*, the residual may close and the wording here
should say so.

**Not a reversal trigger:** a reviewer finding the ordering route simpler. It is simpler. Option
A explains why it is also wrong, and that argument does not weaken with time.
