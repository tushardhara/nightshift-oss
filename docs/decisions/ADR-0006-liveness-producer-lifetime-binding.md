# ADR-0006: A liveness producer's lifetime must be bound to what it attests — the beat timer gets its own signal, and long turns are covered by Program-authored progress beats

Date: 2026-08-23 · Status: **proposed**
Amends: **ADR-0002 §1, §2, §3** (accepted). ADR-0002's decision text is **not** edited; this ADR
adds a fifth signal, restates §1's ownership rule as a testable property, and adds intra-turn
progress semantics. ADR-0002 §4, §4a, §5, §6, §7 are untouched and stand.
Back-link required in ADR-0002's header (see "Back-link" at the end — not applied here, because
an accepted ADR is not edited in place by its reviewer).
**Amended by: ADR-0017 (2026-08-24)** — adds a separate scheduled-run completion
observation and applies D3's no-inference/no-OR rule to it. ADR-0006's five
existing signals, lifetime-binding property, and residual risks stand unchanged.
Deciders: **program** and **staff** — cross-seam. `security` escalated it explicitly as not a
crew lead's call, and the Program agreed; the ownership rule binds both seams and the Program's
own turn structure, which no crew can change.
Proposers: options (i) and (ii) below were proposed by the `security` gate in issue #76; the
freshness-over-introspection argument in D9 is `crew_beta`'s, accepted by `security` over its own
proposal; D1, D4 and D9's termination rule are `staff`'s.
Spec: issue #76 (P0). Blocks: PR #71. Related: #40, #68, #73, #74, ADR-0002, ADR-0004, R-38.

## On the missing debate, stated up front

Per the ADR-0004 precedent, this is disclosed rather than glossed. **No independent second draft
of this ADR exists.** Defect #13 means crews cannot dispatch their own engineers, so wave-3 work
carried no in-crew review. What partly compensates here is better than usual and should be
weighed: the two ownership options were authored by a different agent (`security`) than the one
choosing between them, the freshness argument in D9 was authored by a third (`crew_beta`) and
accepted by `security` *against its own proposal*, and every factual claim below was
re-measured by `staff` against the real binary at PR #71's head `78b2771` rather than taken from
the escalation. Where a measurement contradicted a hypothesis, it is recorded as such.

## Context

ADR-0002 §1 defines four signals with four owners. Row one, at
`docs/decisions/ADR-0002-liveness-and-ownership-contract.md:68`:

> | Program heartbeat | the Program, after a **completed** control-loop iteration | any timer, worker or backup | "did the org's brain run?" |

The published seam contract repeats it and widens the ban:
`.omni/handoffs/LIVENESS-RECORD-v1.md:44` — *"Program heartbeat (`heartbeat:program`) … Never
written by: any timer, any worker, the backup, the watchdog."* So does the binary's own docstring
(`bin/nightshift-ctl:1013-1014`).

PR #71 (`beta/w3-durable-beat-producer`, head `78b2771`) installs
`nightshift-heartbeat.timer` — `OnBootSec=2min`, `OnUnitActiveSec=5min` — running
`deploy/nightshift-heartbeat.sh`, which at line 186-191 executes
`beat write --who program --run "$run_id" --seq "$next_seq" --session heartbeat-timer-durable`,
where `$run_id` and `$next_seq` are read back out of the Program's own beat record
(lines 121-162). It was built to close #40 (the liveness contract had no producer at all) and #68
(a long Program turn outlives its 900s `max_age`, so a busy org reports itself dead). Neither the
diff nor the PR body mentions ADR-0002; the contract was not amended, waived or acknowledged.

**The root cause is one sentence: the timer authors the Program's signal from data it re-reads
out of that same signal, so its output is evidence that systemd fired, not that a control-loop
iteration completed.** ADR-0002's Context quotes Codex naming the class — *"the recovery
mechanism generates the evidence that suppresses recovery"* — and PR #71 is an instance of it.

### The three reproductions, re-executed by `staff` against `78b2771`

Run against the shipped script and the real binary in a throwaway plane, not a mock. All three
reproduce the `security` gate's numbers exactly.

**(a) A dead Program reads healthy, forever.** Program's last beat aged to 1h; correct verdict
`alive=false reason=stale age=3601`. Then three ticks of the shipped script, Program not running:

```
tick1 script exit=0   alive=True age=0 seq=6 session=heartbeat-timer-durable   beat show exit=0
tick2 script exit=0   alive=True age=0 seq=7 session=heartbeat-timer-durable   beat show exit=0
tick3 script exit=0   alive=True age=0 seq=8 session=heartbeat-timer-durable   beat show exit=0
```

**(b) The boot-ID check is erased in one tick.** ADR-0002 calls the boot ID *"exactly what makes
a post-reboot stale heartbeat detectable"*:

```
after reboot, Program NOT running:   alive=False reason=wrong_boot seq=5
timer fires ONCE (exit 0):           alive=True  reason=None      seq=6
```

The mechanism is `cmd_beat_write` at `bin/nightshift-ctl:1016`, `boot = args.boot or
host_boot_id()`: the script passes no `--boot`, so every tick restamps the record with the *live*
boot id. With `OnBootSec=2min`, a box that reboots and never starts its Program reports healthy
from T+2min onward, permanently — the "never started" class ADR-0004 and the watchdog's
`never_started_why` exist to catch.

**(c) The inverse — a false *dead* pinned across a run roll.** The script takes `run_id` from the
record (`nightshift-heartbeat.sh:162`), not from `run current`:

```
tick1 (script exit=0): alive=False reason=wrong_run rec_run=run-A seq=6
tick2 (script exit=0): alive=False reason=wrong_run rec_run=run-A seq=7
```

It self-heals only when the Program writes a beat — which, during a long turn, is by definition
the thing not happening. So it misfires *exactly* in the window #71 was built to protect.

### The rejection reasoning was already in the tree

`deploy/nightshift-watchdog.sh:221-225` records ADR-0004's rejection of Option A — bootstrap
writing the first beat — in these words: *"a beat written by anything other than its named owner
is a forged liveness signal — defect 4 rebuilt in a new place, and invisible, because a forged
beat looks exactly like a real one."*

PR #71's own script applies that rule correctly to the first beat and then abandons it. At
`deploy/nightshift-heartbeat.sh:53-55` it refuses to write when no record exists: *"We refuse to
forge `seq=1` with a guessed run-id; the Program writes the first beat. (ADR-0004 option A was
rejected for exactly this reason.)"* It then writes beats 2..∞ on the Program's behalf. **The
principle was understood, cited, and applied to exactly one write.** That is the clearest
evidence that the defect here is a gap in the *rule's form*, not in the author's diligence — which
is what D1 fixes.

### Why this inverts the queue

The next tracked task was **arm the watchdog** (R-24's hold, which #71 was said to lift). The
watchdog reads `beat show --who program` at `deploy/nightshift-watchdog.sh:140` and treats exit 0
as alive. Under reproduction (a) that verdict is causally incapable of turning red. Arming it is
therefore **strictly worse than the current unarmed state**: unarmed is a known absence of
coverage, an honest zero; armed on a laundered signal is a green light that cannot go red. It
manufactures confidence instead of absence, which is the original brief's bug 4 rebuilt one layer
out.

*Checked and clean, so it is not raised as a finding:* the healthy path no longer blanket-renews
slots — `nightshift-watchdog.sh:183-188` reads *"NOTHING is renewed here"*. An earlier version of
this concern (that a permanently-healthy signal would drive `slot renew-active` forever) does not
hold at this head.

## Options considered

### Option 1 — leave PR #71 as built: the timer writes `heartbeat:program`. **REJECTED**
Reproductions (a), (b), (c). Violates ADR-0002 §1 (`:68`), the published seam contract
(`LIVENESS-RECORD-v1.md:44`) and the binary's own docstring (`nightshift-ctl:1013`). The identical
move was already rejected once in this codebase by ADR-0004, in reasoning quoted above. There is
no reading of the contract under which this is compatible; see "Verdict" in the escalation reply.

### Option 2 — `beat show` declines to count timer-authored beats (`security`'s option (ii)). **REJECTED**
The record already carries `session: heartbeat-timer-durable` and `cmd_beat_show`
(`nightshift-ctl:1090-1129`) simply ignores it, so this is a small change. It is still wrong, on
three grounds, the first of which is decisive:

**(a) It does not preserve the evidence it would need.** `meta` is one row per key
(`nightshift-ctl:173`) and `cmd_beat_write` upserts into it (`:1043-1045`). There is no history
table — `beat write` is destructive by construction. Once the timer writes, the Program's last
real `ts` is *gone*: in reproduction (a), the 3601s-old Program timestamp is unrecoverable after
tick 1. So `beat show` could report "not alive, timer-authored" but could never again report the
Program's true age or its last completed `seq`. This trades a false ALIVE for a permanent
*unknown*, destroying the staleness measurement rather than repairing it. A filter in the reader
cannot restore data the writer already overwrote.

**(b) It is an enumerable surface.** It is a denylist of session strings. Rename
`heartbeat-timer-durable` and the filter is bypassed silently. This is precisely the objection
`crew_beta` raised against timer-state introspection and that `security` accepted over its own
proposal; adopting it here would make the codebase inconsistent with its own accepted reasoning.

**(c) It puts the correctness in the reader.** Every present and future consumer of
`heartbeat:program` must independently implement the filter, and one that forgets is wrong by
default. Option 3 puts it in the writer, where being right once makes every reader right by
construction.

### Option 3 — the timer gets its own signal, `heartbeat:heartbeat-timer` (`security`'s option (i)). **TAKEN — see D2**
Measured feasibility at `78b2771`: **zero changes to `nightshift-ctl` are required.** `--who` is a
free-form string on both `beat write` and `beat show` (`:1495`, `:1506`), keyed as
`"heartbeat:" + who` (`:989`, `:1045`). Verified working: `beat write --who heartbeat-timer` →
`ok: true`; `beat show --who heartbeat-timer` → `alive: true`, exit 0. Verified independent:
`heartbeat-timer` at `seq=1` and `program` at `seq=1` coexist, so the two sequence spaces do not
interfere — which Option 2 does *not* give, since under Option 2 the timer keeps advancing the
Program's own `seq`.

### Option 4 — raise `heartbeat_max_age_seconds` past the longest turn. **REJECTED**
`nightshift-ctl:166` sets it to 900. Detection latency for a genuinely dead Program becomes the
longest turn we are ever willing to tolerate — a number nobody can name, and one that only
ratchets. Every future long turn is an argument to raise it again, and the ratchet turns in
exactly one direction: toward silence.

### Option 5 — turn-aware `max_age` derived from an open `agent_runs` row. **REJECTED**
Superficially attractive, because `agent_runs` is written by a different command than the
heartbeat (`cmd_run_start`, `:1134`) and so is independent evidence in the sense
`expected_run_id` (`:1050-1063`) relies on. It fails on lifecycle: **a Program that dies mid-turn
leaves its row open forever**, so the extension never expires and a dead Program reads alive
indefinitely — reproduction (a) reconstructed out of a different table. ADR-0004's own amendment
already convicted this shape: *an absence is not discriminating.* The absence of an end-record is
not evidence of progress.

### Option 6 — a Program-spawned sidecar with `PR_SET_PDEATHSIG` writing progress beats. **DEFERRED to tier 1, with a trigger — see D6**
Not rejected. It satisfies D1: a child reaped when its parent dies has a lifetime bound to the
Program's, which is exactly what a systemd timer lacks. It is more machinery than the observed
problem justifies today, so it is the named escalation rather than the current answer.

## Decision

### D1 — the ownership rule is restated as a testable property

ADR-0002 §1's *"never written by any timer, worker or backup"* is correct and stays. But as
written it is a **list of banned nouns**, and a list of nouns is an enumerable surface — the same
objection `crew_beta` correctly raised against timer-state introspection. It cannot be checked
without already knowing every writer that could exist. The property that makes the list true is:

> **A liveness signal may be written only by a process whose continued existence is caused by the
> liveness of the thing that signal attests.**
>
> The review question is one line: **what stops this writer when the attested thing stops?** If
> the answer is "nothing," the writer is illegitimate, whatever it is called.

Applied: a systemd timer **fails** — an independent lifetime is the entire purpose of a timer, so
this is not a bug in `nightshift-heartbeat.sh` but a category error in using a timer at all. An
in-loop call from the Program **passes** trivially. A child process reaped with the Program
**passes**. The backup and the watchdog **fail**. This is checkable on a diff by a reviewer who
has never read ADR-0002, which the noun list is not.

### D2 — five signals, five owners: the timer gets `heartbeat:heartbeat-timer`

ADR-0002 §1's table gains a fifth row:

| Signal | Written by | Never written by | Answers |
|---|---|---|---|
| Beat-producer health (`heartbeat:heartbeat-timer`) | `nightshift-heartbeat.timer`, after a tick that reached the control plane | the Program, any worker, the backup, the watchdog | "is the recovery layer's timer still firing?" |

This is a real and useful signal, honestly scoped. It is the input to "is the monitor running",
which is a question we genuinely need answered (D9) and currently cannot answer at all. PR #71's
work is therefore **retained, not reverted.** Required changes to `deploy/nightshift-heartbeat.sh`:

1. `--who program` → `--who heartbeat-timer` (line 187).
2. **Stop reading `run_id` out of a beat record.** Take it from `run current`
   (`nightshift-ctl:1074-1087`). Reading it from the record is what produces reproduction (c),
   and renaming the signal alone would leave a timer signal permanently pinned to a stale run.
3. The `MISSING` branch (lines 156-159) **may now write.** The timer is the legitimate owner of
   its own signal, so bootstrapping its own `seq=1` is not forgery. It reads its own prior record
   and `run current`, and attests only itself. The self-reference is fully removed: its output is
   a claim about the timer, supported by inputs about the timer.

### D3 — nothing may infer Program liveness from the timer's signal

`beat show --who program` remains the sole verdict on Program liveness, unchanged. No consumer —
watchdog, report, dashboard or human — may treat `heartbeat:heartbeat-timer` as evidence that the
Program ran. The two signals may be displayed side by side; they may never be OR'd.

### D4 — #68 is fixed by Program-authored intra-turn progress beats

#68 is real and does not go away: `beat show` has been observed returning `alive: false` mid-turn
while the Program was actively working. Under D2 the timer no longer papers over it, so it needs
its own answer. A new control-plane verb:

```
nightshift-ctl beat progress --who program [--phase <label>]
```

**(a) It refreshes `ts` and `phase`. It does NOT advance `seq`.** This is why it must be a
separate verb rather than a reuse of `beat write`: the strictly-increasing-`seq` guard at
`nightshift-ctl:1023` is a control, and reusing `beat write` for progress would require weakening
it. `seq` keeps ADR-0002 §2's meaning exactly — *last **completed** loop iteration*.

**(b) It refuses rather than repairs.** It requires an existing `heartbeat:program` record whose
`run_id` equals `run current` **and** whose `boot_id` equals the live boot id. On any mismatch, or
if no record exists, it exits 3 and writes nothing. A progress beat may only extend the freshness
of a record the Program already earned by completing an iteration; it may never create, reset or
repair one. This is what keeps the three reproductions closed:

- **(a) cannot recur** — a dead Program calls nothing, so `ts` goes stale within `max_age`.
- **(b) cannot recur** — after a reboot with no Program, nothing writes; the record keeps the old
  boot id and `wrong_boot` holds. And a progress beat *refuses* on boot mismatch rather than
  restamping, which is the specific behaviour `beat write` lacks (`:1016`).
- **(c) cannot recur** — the run comes from `run current`, never from the record, and a mismatch
  refuses instead of pinning.

**(c) It records `seq_ts` — and this closes a failure mode progress beats would otherwise
introduce.** A Program wedged in an infinite loop, emitting progress beats forever, would read
perfectly healthy under freshness alone. That is this ADR's own defect class arriving by a new
route, so it is closed in the same decision rather than filed for later. The record gains an
optional `seq_ts`: the wall-clock time at which `seq` last advanced. A consumer can then
distinguish *alive and iterating* from *alive but has completed no iteration in N* — two states
the current record renders identically.

**(d) `seq_ts` is additive at `schema_version: 1`; the version is NOT bumped.** `read_beat`
(`:1005`) rejects any record whose `schema_version` differs and returns `unknown_version`, which
`cmd_beat_show` turns into **not alive**. That is the correct fail-closed direction and it must
stay — but it means **bumping the version for an additive field would take the whole org NOT
ALIVE at the instant of deploy**, until every consumer is updated: a self-inflicted outage of the
liveness signal, caused by the versioning clause meant to protect it. `read_beat` requires a
fixed field list and ignores extra keys, so an optional field is backward compatible without a
bump. Bump the version only when the meaning of an existing field changes.

**(e) Cadence.** `heartbeat_max_age_seconds` stays at 900 (`:166`). The Program emits a progress
beat at least every 300s of turn time — the same ~3x margin PR #71 chose, and it is the right
margin. Where the Program cannot emit for longer than `max_age` because a single uninterruptible
operation exceeds it, that is **not solved at tier 0**; see D6.

### D5 — R-38's ordering is endorsed, and tightened

Signal ownership is fixed **before** the watchdog is armed. `staff` agrees, for the reason in
Context: armed-on-a-laundered-signal is worse than unarmed, because it converts a known gap into
a false assurance.

**One tightening.** R-38 as recorded blocks arming on the ownership fix (D2). That is necessary
and not sufficient. **Arming must block on D2 *and* D4 together.** If only D2 lands,
`heartbeat:program` becomes truthful but #68 is unfixed — so the newly-armed watchdog will fire
STALE on every long turn. That is the false-alert class R-14 guards, arriving from the opposite
direction, and it is the exact problem PR #71 was built to prevent. A watchdog that cries wolf on
every long turn gets muted by a human, and a muted watchdog is the unarmed state again, minus the
honesty. Order: **D2 → D4 → arm.**

### D6 — scale ladder: T0, no movement, with a named trigger

This is a correctness change, not a capacity one. Zero new infrastructure; no new process, no new
daemon, no Redis-shaped anything. **Option 6 (the `PR_SET_PDEATHSIG` sidecar) is explicitly tier 1
and is not built now.**

**Trigger to move to tier 1:** after progress beats ship, an observed `beat show --who program`
returning `stale` while the Program process was demonstrably alive — i.e. a single uninterruptible
operation exceeded `max_age` with no opportunity to emit. Until that number is observed, an
in-loop call is enough, and the sidecar is unbudgeted operational surface. When it fires, build
Option 6; do **not** raise `max_age` (Option 4) and do **not** restore a timer (Option 1).

### D7 — #40 stays open when PR #71 merges

PR #71 fixes the producer **in code, not on this box**. `nightshift-watchdog.timer` and
`nightshift-state-backup.timer` are both measured `enabled=disabled / active=inactive`, as PR
#71's own body states. #40 is "the liveness contract has no producer"; it closes when a producer
is *running here*, not when a unit file exists in a merged diff.

### D8 — the mandate does not extend past accident

See "Residual risk accepted". This ADR defends against **accident** — crash, silent no-op,
misconfiguration, the #71 class. It does not defend against uid-1000 code execution, because on
this box it cannot (#73).

### D9 — where the monitoring regress terminates

`crew_beta`'s argument, accepted by `security` over its own proposal, is **upheld**: beat
freshness is the correct control, and timer-state introspection is not. Freshness is
cause-agnostic — it catches masking, disabling, crash and silent no-op identically — while
`systemctl` state is an enumerable proxy that misses active-but-failing, a wrong interval, a
renamed unit, and a script that exits 0 doing nothing. PR #71's own round-1 bug is the proof:
`Type=oneshot`, exit 0, `list-timers` green, zero beats written, for as long as anyone cared to
look.

The qualification both agents flagged is also upheld, and it is the whole reason this ADR exists:
**freshness of a signal proves the attested thing ran only to the degree that the attested thing
is the signal's only writer.** Freshness and ownership are not two controls to trade off.
Ownership (D1) is the precondition that makes freshness mean anything. Once a timer refreshes the
beat unconditionally, freshness stops proving the Program ran and starts proving the timer ran —
which is reproduction (a) restated in one line.

**On "nothing monitors the monitors", the ruling is: correct that it does not terminate locally,
with one correction to the proposed terminator.**

*It does not terminate locally.* Every component here — Program, timers, watchdog, any future
sidecar — runs as one unprivileged user in one failure domain. Any member can stop, mask or
outlive any other, and #73 shows it can also *forge* any other, since the strictly-increasing
`seq` guard lives in the binary and not in storage. N mutually-checking local monitors therefore
do not reduce to a base case; they reduce to *N components sharing one failure domain*, and the
common-mode failures are the interesting ones: the box is off, `systemd --user` is dead, lingering
is disabled, the disk is full, the VM is gone. The (N+1)th local checker adds cost and zero
coverage against every one of them.

*The correction:* **the Program's own per-turn `beat show` is not a terminator.** Keep it — it is
nearly free and it catches a wrong-run, wrong-boot or stale record *while the Program is running*,
which is worth having. But its failure mode is perfectly correlated with the failure it must
detect: **a dead Program does not run its own check.** It cannot report its own absence. That is
self-attestation, the exact class ADR-0002 exists to remove. It is an in-band consistency check,
and calling it the outermost monitor would repeat this ADR's mistake at the top of the stack.

*Where it actually stops.* The regress terminates at the first observer whose **default state is
alarm** and for whom **silence from the box is itself the trigger** — a dead-man's switch, off
box. The asymmetry is what does the work:

> A **fail-open** observer must act in order to alert, so anything that stops it also silences it.
> Every uid-1000 component here is fail-open. A **fail-closed** observer alerts unless it is
> actively reassured, so suppressing it requires the box to *act*, and the failure mode being
> detected *is* inaction. The box's failure domain cannot reach it.
>
> **Fail-closed observers terminate the regress. Fail-open observers extend it.**

Two conditions on that terminator, or it is theatre:

1. **It must be negative-acknowledgement, not alarm-forwarding.** If the box alerts on failure,
   a dead box sends nothing and the receiver stays quiet. It must alert on a *missed check-in*.
2. **The check-in must carry content, and the receiver must apply ADR-0002 §3's rules to it** —
   reject a wrong run, a previous boot, a non-increasing `seq`, a future timestamp. A check-in
   that only says "I am up" is the `.omni/` mtime heuristic relocated off-box, at greater expense.
   Carrying `seq` off-box has a second benefit that comes free: it puts a monotonicity check
   **outside** the trust domain that can forge it, which is a partial answer to #73 that no local
   control can give.

This does not reach zero. It reaches **decorrelation**: the receiver is a different failure
domain, different operator, different funding. You stop when the residual correlated failure
probability is below what you would pay to reduce it further — not when you have proved it is
nothing.

## Residual risk accepted

Stated explicitly, because unnamed residual risk is what an unarmed watchdog is made of.

**R1 — there is no terminator today, and D9 does not build one.** No off-box path exists on this
box: the state-backup timer is disabled, and `nightshift-ctl dump` does not export `meta` at all
(verified: `slots, claims, leases, tasks, checkpoints, vendors, generated_at` — no `meta`, so no
beats and no `current_run_id`, per #41 and ADR-0004's amendment). Every control in this system is
currently fail-open. A destroyed or powered-off box is silent indefinitely and nobody learns.
D1-D8 make the *local* signals honest; they do not make anyone off-box hear them. That gap is the
single largest one remaining and it should be a tracked item, not a footnote in this ADR.

**R2 — liveness is forgeable, not merely maskable (#73).** The `seq` guard is in the binary;
SQLite enforces nothing. One `sqlite3` write defeats every local control described here. The
honest consequence is D8's scope: this is an accident-tolerance design, not an
adversary-tolerance one. Anyone reasoning about the latter must start from the off-box `seq`
check in D9, because no in-box control can help.

**R3 — a false-negative window of `max_age` is accepted by construction**, plus the check-in
interval once R1 is closed. A Program that dies one second after a progress beat is undetected for
up to 900s. That is the design, not a defect.

**R4 — the off-box receiver is itself unverifiable from here, and I will not overclaim it.** This
box has `floci` emulators for AWS (:4566), Azure (:4577), GCP (:4588) and OCI (:4599), and no real
cloud credentials, by design. So the *publisher* side of a check-in **can and should** be
integration-tested against an emulator rather than a mocked SDK — that is a real test and its
absence would be a choice, not a necessity. But emulator coverage proves wire behaviour and
nothing about IAM, quota, region, cost, or whether a human is ever paged. **The end-to-end
property in D9 cannot be verified on this box.** Whoever implements it must say so in the PR, and
must not present a green emulator test as evidence that the dead-man's switch works.

**R5 — D4 depends on the Program changing its own turn structure.** No crew can deliver it. If
the Program does not adopt in-loop progress beats, #68 stays open and D5's ordering blocks the
watchdog indefinitely. That is the intended behaviour — a blocked watchdog is the honest state —
but it should be a visible dependency, not a surprise in two weeks.

## Consequences

**Easy.** All three reproductions close. `beat show --who program` becomes true again, and the
watchdog can be armed safely once D2 and D4 both land. PR #71's work is retained rather than
reverted — the timer becomes a correctly-scoped fifth signal for a change of `--who` plus a
change of run source, and it starts answering a question (D9: "is the recovery layer firing?")
that we currently cannot answer at all. D2 needs zero changes to `nightshift-ctl`, verified.

**Hard.** `beat progress` is a new verb on the control plane during a wave already in flight, and
D4 requires the Program's own loop to call it — a change to the Program's turn structure, which is
why this is a program-and-staff decision and cannot be delegated. `seq_ts` touches the record
schema, and the reasoning in D4(d) about *not* bumping `schema_version` must survive review, or
someone will bump it and take the org NOT ALIVE on deploy.

**Forecloses.** Any future proposal to keep any liveness signal fresh with an
independently-scheduled producer, for any signal, by any name — D1 makes that a category error
rather than a matter of taste. Also forecloses reusing `beat write` for progress, which would
require weakening the `seq` guard at `:1023`.

**Cost.** Zero infrastructure. One extra `meta` row for the fifth signal, plus one `UPDATE` per
progress beat: at a 300s cadence over a seven-day run, ~2,016 writes — the same order as
ADR-0002's own arithmetic for the per-minute case, and equally irrelevant against SQLite. At 100x
the run count it is ~200k writes against a database that is currently 77KB. Neither number moves
any tier.

## One-way door?

**No, and deliberately so.** D2 is a `--who` flag: reversing it is one word. D4 adds a verb and an
optional field; nothing is deleted, no existing semantics change, and `schema_version` stays at 1
precisely so that a rollback does not require a coordinated one. D1 is a rule, reversible by
writing a better one. The only irreversible thing in the area is the *deletion* of
`slot renew-active`, which ADR-0002 already stages separately and which this ADR does not touch.

## Reversal trigger

**D4:** revisit if `beat show --who program` reads `stale` while the Program process is
demonstrably alive, *after* progress beats have shipped. That means the cadence or `max_age` is
wrong, or a single uninterruptible operation exceeds `max_age`. The response is Option 6, the
lifetime-bound sidecar. It is **not** raising `max_age` (Option 4) and it is **not** restoring a
timer-authored `heartbeat:program` (Option 1), either of which would reintroduce exactly the
failure this ADR removes.

**D2:** revisit if the fifth signal costs more than it gives — but note it is a flag, so the
reversal is cheap and the bar for trying it is correspondingly low.

**D9:** revisit if an off-box dead-man's switch proves unbuildable within the project's cost
envelope. In that case the honest response is to **record that the system has no terminator** in
`.omni/tech-debt.md` with that fact stated plainly, not to promote a local monitor into the role
and call the problem solved.

## Back-link

Not applied by `staff`: an accepted ADR's decision is not edited in place by its reviewer.
The Program should add to `ADR-0002-liveness-and-ownership-contract.md`'s header:

```
Amended by: ADR-0006 (2026-08-23) — §1 gains a fifth signal and its ownership rule is restated
as a lifetime-binding property; §2/§3 gain intra-turn progress semantics and an optional
`seq_ts`. All four original rows, and §4-§7, stand unchanged.
```
