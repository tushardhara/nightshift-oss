# ADR-0005: The control plane is discovered, never invented; and the clock is injectable

Date: 2026-08-23 · Status: **proposed** — awaiting `staff` review and `security` review
Deciders: crew_alpha lead · Proposers: crew_alpha lead (see "On the missing debate")
Issues: #31, #32 · Wave 2 · Base `7c16fbe`
Renumbered from ADR-0004 before review — crew_beta holds ADR-0004
("never started is not dead", PR #37). Content unchanged by the renumber.
Conversation: dd65182b03754fb3bc16c0cfd9c94d14 · Spec: none — no separate spec doc; issues #31/#32 are the spec
(field added retroactively 2026-08-23, in the id of the session that added
it — the original authoring session’s id was never recorded anywhere this
crew can recover; see "Correction (2026-08-23) — template fields" at the
end of this file.)

## On the missing debate (read this first)

Phase 1 of this crew's charter requires two different-vendor engineers to propose
competing approaches before the lead reconciles them. **That did not happen, and
could not.** Worker dispatch is still sterile (issue #13): a `sys_session_send` to
`pm` from this cleanly-created session returned, verbatim,

    Error: sys_session_send requires parent session inbox

I probed it once this wave rather than assuming wave-1's finding still held, because
working agreement 5 is binding only *while* grandchild dispatch is broken. It is
still broken. Under WA-5 the lead builds directly, and this ADR is therefore a
single model's reasoning with **no independent proposer and no in-crew review**.

That is a real weakening of the gate and I am not going to dress it up. What I have
substituted for a second proposer is *evidence*: every claim below that could have
gone the other way was executed against the live box, and the command and its output
are quoted. Two of the four candidate designs were killed by observations I did not
expect when I started (see "Consequences -> What nearly went wrong"). A reader who
wants to audit this ADR should re-run the quoted commands rather than trust the
prose.

---

## Context

### #31 — `DEFAULT_DB` is a relative path

`bin/nightshift-ctl:43` reads:

```python
DEFAULT_DB = os.path.join(".omni", "control.db")
```

and `connect()` (line 134) opens it like this:

```python
def connect(db_path):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    cx = sqlite3.connect(db_path, timeout=30, isolation_level=None)
```

`os.makedirs` + `sqlite3.connect` **create**. So from any directory that is not the
project root, the tool manufactures a brand-new empty control plane and answers
questions about it, confidently and wrongly. Reproduced on the live box:

```
$ cd /tmp/rt25-wWHz && /srv/project/bin/nightshift-ctl slot release --slot 21
{"ok": true, "released": 0}
exit=3
$ find /tmp/rt25-wWHz -name '*.db'
/tmp/rt25-wWHz/.omni/control.db          <-- created silently
$ /srv/project/bin/nightshift-ctl slot acquire --holder crew_alpha/eng_claude \
      --crew crew_alpha --task phantom-task
{"granted": true, "slot_id": 1, "active_after": 1, "max_concurrent_workers": 4}
exit=0
```

Meanwhile the real plane at `/srv/project/.omni/control.db` had slot 21 active and
held by this crew. So:

* `slot release` reported `{"released": 0}` — a **false negative** that reads as
  "your slot was already gone".
* `slot acquire` **granted** on an empty plane. That is the project's **defect 6**
  ("slot acquire always succeeds") returning through a side door, and by the same
  mechanism `claim acquire` reproduces **defect 7** and `lease acquire` grants any
  resource. The semaphore that the whole org's concurrency budget rests on becomes
  a no-op for any caller with the wrong CWD.

It does not error. It answers. That is the whole severity argument: a tool that
fails loudly costs one retry, and a tool that lies costs an investigation. It
already manufactured one false incident, briefly convincing a previous session it
had corrupted another crew's slot.

### #32 — the load-sensitive flake

Root-caused by `platform`; relayed in the charter and **not re-derived here**. I did
reproduce it to confirm the failure mode is the one described, under 8 CPU hogs on
an 8-core box:

```
FAIL: test_dead_worker_reaped_slot_claim_run (test_recovery.Recovery)
AssertionError: 0 != 1        # reaped["slots_reaped"]
```

The header comment at `bin/nightshift-ctl:24` states the invariant that causes it:
expiry is **lazy AND explicit** — every mutating command calls `expire_stale` first.
The test acquires a slot with `--ttl 0.2`, then runs two more CLI subprocesses
(`run start`, `claim acquire`), then sleeps 0.3 and asserts that the *explicit*
`reap` collected the slot. Under load the setup gap alone exceeds 0.2s, so
`claim acquire`'s lazy expiry already reaped the slot and the explicit `reap`
correctly returns 0. The test asserts something the design does not promise.

Lengthening the sleep cannot fix this: the race is between `slot acquire` and
`claim acquire`, both *before* the sleep.

---

## Decision

### D1 — `--db` > `NIGHTSHIFT_CTL_DB` > upward discovery of an **existing** plane

The default is resolved by walking up from the process CWD looking for an
**existing** `.omni/control.db`, stopping at a directory containing `.git` (the repo
root).

**The `.git` stop is not an absolute bound, and an earlier draft of this ADR said it
was.** It bounds the walk only when a `.git` ancestor actually exists. With none, the
walk continues to `/`, so a plane in any ancestor directory can be adopted —
`security` demonstrated this by adopting one three levels up. On this box that is
latent rather than live (`/tmp/.omni`, `$HOME/.omni`, `/srv/.omni` and `/.omni` do
not exist, and the suite's leaked planes are at `/tmp/nightshift-ctl-test-*/.omni/`,
not `/tmp/.omni/`), but the sentence it replaced claimed a guarantee the code does
not make.

The bound that actually holds is **D2, never-create**: no invocation of *this* build
can cause a plane to appear in a parent directory. That half is airtight and it is the
one this design relies on; the `.git` stop is a useful narrowing, not a security
boundary.

**But note exactly how far it reaches, because an earlier draft of this sentence
overreached too.** It said discovery can only find a plane "someone already created
**deliberately**". That word is wrong, and it was falsified on this box while the
evidence for D4 was being captured: a **pre-fix** build (`a73ba88`, still the checked-out
build of one clone on this host) manufactured an empty plane as a side effect of a
read-only-looking `slot list`, and every worktree of that clone then resolved to it —
including under the post-fix build, which adopted it correctly and reported an empty
org. Forensics and cleanup are in `docs/audit/RT-29.capture.txt` §11. **D2 stops a
phantom being manufactured; it does not stop one already manufactured from being
adopted**, and while any pre-fix build survives anywhere on a host, phantoms can still
be created by it. That is an argument for redeploying (issue #42), not against D2 — but
it is not a guarantee this ADR gets to claim, so it does not claim it. `security`'s stated preference was to correct this sentence rather than add
a second bound (`$HOME`, or a mount-point/device change), and I have taken it — an
ADR that overstates its own guarantee is worse than one that states a smaller one
honestly, because the overstatement is what a later reader builds on.

### D2 — `connect()` never creates. Creation requires `init`.

A missing plane raises `ControlPlaneMissing` (a `FileNotFoundError` subclass).
Non-`init` commands emit one JSON object and exit **1**. Observed, not paraphrased
— `mkdir -p /tmp/rt29-check && cd /tmp/rt29-check && nightshift-ctl slot list`, run
against `265ff26`:

```json
{"cwd": "/tmp/rt29-check", "db": "/tmp/rt29-check/.omni/control.db", "db_source": "unresolved", "error": "control_plane_missing", "hint": "run from inside the project (or a worktree of it), or pass --db. `nightshift-ctl init` CREATES a new empty control plane and is almost never what you want here", "ok": false}
```

**This block was stale until the commit carrying this paragraph, and the stale\nversion mattered.** An earlier
revision of this ADR quoted a shorter object whose hint read *"run `nightshift-ctl
init` to create it"* — which is exactly the advice the shipped code refuses to give,
and for a reason it states in a comment at `bin/nightshift-ctl:95-100`: a user who
hits this error from a worktree and follows `init` **recreates the defect this ADR
exists to remove**, manufacturing the phantom plane by hand. The quote also predated
`db_source` and `cwd`, the two keys that let a reader see *why* resolution failed
rather than only that it did. A reviewer diffing the ADR against the binary would
have found a document that understated its own fix; correcting it is not cosmetic.

The open uses SQLite URI mode `file:<path>?mode=rw`, which *refuses* to create, so
the guarantee does not rest on an `os.path.isfile` check that a race could defeat.

### D3 — `beat show` keeps its fail-closed verdict, through the existing path

`ControlPlaneMissing` subclasses `FileNotFoundError` ⊂ `OSError` **specifically so it
flows into the interception `security` already cleared at `8cd0f88`** rather than a
parallel one:

```python
try:
    cx = connect(args.db, create=(args.group == "init"))
except (sqlite3.Error, OSError) as exc:
    beat_show_verdict_on_unreadable(args, exc)
    raise
```

A missing plane is a *correct* not-alive answer: nothing can attest the Program ran.
`beat show` therefore still emits exactly `{"alive": false, "reason_code":
"control_plane_unreadable"}` with exit 3, and every other subcommand still fails
loudly.

**This is not a widening of the verdict at all — it is the code catching up to its
published contract.** LIVENESS-RECORD-v1 §3 already defines the code as "the control
plane itself could not be opened: **absent** and uncreatable, corrupt, or
permission-denied". *Absent* was in the contract from the start; it was simply
*unreachable* while `connect()` created whatever path it was handed. D2 makes it
reachable. This argument is the reviewer's and it is strictly better than the one it
replaces — the earlier text defended the change as a one-condition extension of the
input, which invited the question of where the extensions stop. There is no extension
to bound: the verdict means today exactly what §3 always said it meant.

The narrowness `security` verified at `8cd0f88` is preserved because I added no new
interception — `main()` still wraps only `connect` and `ensure_schema`, never
`args.func`, and `test_other_subcommands_do_not_fake_a_verdict` still pins the scope.

### D4 — injectable clock via `--now`, on argv only, announced on stderr

`now()` consults an override set once in `main()`, taken from `--now` and from
nothing else. This is `platform`'s prescribed fix and I am following it rather than
re-deriving.

**There is deliberately no environment channel.** An earlier revision of this change
also read `$NIGHTSHIFT_NOW`; `security` blocked it, correctly, and it is gone — see
"How the D4 risk was settled" below. Argv is the right channel because it is
per-invocation, does **not** pass to child processes, is visible in `ps`, and is
legible in any script or ledger entry that carries it. The test harness injects
through `Ctl.cmdline()`, which is the single place the suite builds argv.

**The override is announced on stderr, never in the JSON.** I considered adding
`"clock_injected": true` to the output object and rejected it: `beat show`'s
unreadable verdict is pinned by `security` to *exactly two keys*, and a third key
under an injected clock would break a security-cleared contract to serve a
debugging convenience.

---

## Alternatives rejected, and the evidence that killed them

### A1 — anchor the default on the script: `realpath(__file__)/../../.omni/control.db`

This was my first choice. It is the most literally "independent of CWD" option, and
I abandoned it on evidence.

`deploy/nightshift-watchdog.sh:70` resolves the tool **from PATH**, not from the repo:

```sh
CTL="${NIGHTSHIFT_CTL:-nightshift-ctl}"
...
verdict=$(cd "$PROJECT_DIR" && "$CTL" beat show --who program --max-age "$max_age")
```

and on this box PATH does not point into the repo:

```
/home/nightshift/.local/bin/nightshift-ctl -> /srv/nightshift/bin/nightshift-ctl
    sha256 f9f688a8...   script-anchored root = /srv/nightshift   .omni there? NO
/srv/project/bin/nightshift-ctl
    sha256 ac78c5bf...   script-anchored root = /srv/project      .omni there? YES
```

Script-anchoring would resolve the watchdog's `beat show` to
`/srv/nightshift/.omni/control.db`, which does not exist — turning the dead-man's
switch into a permanent false alarm. I may not edit the watchdog (crew_beta holds
its lease), so a design that requires editing it is not available to me. **Rejected.**

### A2 — refuse to run without `--db`

Same fatal objection, more directly: the watchdog calls `beat show` with no `--db`
and I cannot change it. **Rejected.**

### A3 — require `NIGHTSHIFT_CTL_DB` always

The watchdog does not set it. **Rejected**, same reason.

### A4 — lengthen the sleeps in the flaky tests

`platform` showed the race is upstream of the sleep. This is the "re-run until
green" reflex that launders an intermittent defect into "just the flake" — the
project's own disease, in the test layer. **Rejected.**

---

## Consequences

### What this fixes

Defects 6 and 7 lose their side door. A wrong-CWD invocation now produces an error
instead of a phantom grant, and no new `.omni/control.db` can appear anywhere except
under an explicit `init`.

### What nearly went wrong, and what it says about the ADR

Two of four candidate designs were killed by facts I discovered only by reading the
box: that the watchdog resolves the tool from PATH, and that the PATH copy is a
*different binary in a tree with no `.omni`*. Had I reasoned from the repo alone —
which is what a design debate conducted purely in prose would have done — I would
have shipped A1 and broken the dead-man's switch. This is the strongest argument I
can offer that the missing second proposer was mitigated, and also the strongest
argument that it should not have been missing.

### Known deviation from the acceptance criterion, stated plainly

Issue #31 asks for a default "independent of CWD". D1 is **not** literally
CWD-independent: discovery starts at the CWD. A1 would have been literally
CWD-independent and is unavailable for the reason above.

I claim D1 satisfies the criterion's *intent* and I want that judged, not assumed.
The harmful property is not that the answer depends on where you stand; it is that
the tool **invents a plane that does not exist**. D2 removes that property
unconditionally: under D1+D2 the resolved default is always either an
already-existing control plane or a loud error. From `/tmp` you now get an error,
not a phantom. `pm` should sign or reject this criterion explicitly.

### How the D4 risk was settled

An injectable clock is a **deliberate weakening of TTL enforcement**, so I routed it
to `security` rather than self-clearing it. `security` **blocked** the environment
channel and cleared `--now`. The block was right and the record should be honest
about why, because my conclusion survived and my reasoning did not.

**What I got wrong.** I argued the override granted no new authority because anyone
who can set the variable "can already pass `--db` at any plane and call `reap`".
That specific claim is false: `reap` is a garbage collector, not a destructor — it
collects only holds already past their TTL and returns `slots_reaped: 0` against a
healthy plane. There is no argv path by which `reap` expires an unexpired hold.

**Why the conclusion held anyway.** The release verbs check ownership only
`if args.holder`, so omitting the flag skips the check entirely; `slot release` and
`claim release` have no holder check at all. The plane is cooperative, not enforcing.
So the clock genuinely granted no authority the CLI did not already grant — for a
bigger and less comfortable reason than the one I gave. (That looseness is
pre-existing and out of scope here.)

**Why it blocked despite that.** `security`'s framing is the one worth keeping:
*this is #31 wearing a different hat*. #31 was never "the caller could have passed
`--db`" — it was that an **ambient, inherited, invisible input silently changed the
answer instead of erroring**. `$NIGHTSHIFT_NOW` had that shape exactly. The reachable
states matched the release verbs; the *accidental* reachability did not, and each
release verb requires naming its target. Removing the requirement of intent is the
substance of #31. Two verified consequences, both reproduced independently before the
fix and re-run after it:

* `expire_stale` runs inside `slot list`, `lease list`, `claim show` and `dump` — all
  read-looking commands. One `slot list` under a forward clock reaped a healthy plane
  irreversibly; there is no un-reap verb.
* Backward was worse. A clock pinned to the heartbeat's own `ts` turned a six-hour
  dead Program into `{"alive": true, "age": 0.0}`, exit 0 — a **fail-open on the
  dead-man's switch**, reachable from an inherited variable. `wrong_run` and
  `wrong_boot` do not catch it, because in the scenario the watchdog exists for, both
  match. That is the ADR-0002 failure class verbatim.

The stderr announcement did not rescue it: `deploy/nightshift-watchdog.sh` calls
`beat show` with `2>/dev/null`, so the warning never even reaches the journal. A safe
design cannot depend on anyone reading stderr.

**Resolution.** The `$NIGHTSHIFT_NOW` branch is deleted; `--now` stays. The test
harness injects through `Ctl.cmdline()` instead of `Ctl.env()`. This cost the change
nothing: the suite stays at **189 OK**, and RT-26's determinism holds **3/3 under 8
CPU hogs on an 8-core box**. The env var was never load-bearing for the seam it was
added to serve. Both consequences were reproduced against the pre-fix build and
re-observed against the post-fix build on this box; the commands and their raw output
are **`docs/audit/RT-29.md`** and its capture, including a control showing `--now`
still reaps (the fix removed the *channel*, not the *capability*) and a first fixture
attempt that did **not** reproduce. Both gates offered in the brief — a test-mode opt-in flag, and
refusing unless the DB path is under `/tmp` — were rejected by `security`: the first
adds a second ambient input without removing the first, and the second couples a
safety control to a path convention and mislabels the problem as access control.

A side effect worth recording: because `cmdline()` is the single place argv is built,
`run_parallel(c.cmdline(...))` now inherits the instance clock. Under `Ctl.env()` it
did not, since `run_parallel` never passed an env. No current caller is affected —
every `run_parallel` site constructs an unclocked `Ctl()` — but the mixed state the
module docstring calls unrepresentable is now actually unrepresentable.

### Migration

None. Every existing caller passes `--db` or runs from the project root. The suite
constructs planes through `Ctl()`, which already calls `init` explicitly.

### Out of scope, filed rather than fixed

* `/srv/nightshift/bin/nightshift-ctl` (the copy the watchdog actually runs) is a
  **stale, different build** from the repo. This fix does not reach the watchdog
  until that copy is redeployed. Now **issue #42** (P0, crew_beta): the program
  checked further than I did and found that build has **no `beat` subcommand at
  all**, so `beat show` there exits non-zero on `invalid choice: 'beat'`, the
  watchdog reads `alive=0`, and the dead-man's switch false-alarms permanently.
  Not mine to fix — nothing in `bin/nightshift-ctl` can change which copy PATH
  finds. It does sharpen A1 below: script-anchoring would have aimed the
  watchdog at `/srv/nightshift`, which has no `.omni`.
* The suite leaks ~4,245 `/tmp/nightshift-ctl-test-*` temp planes because `Ctl()`
  never cleans up. Harmless but noisy; now **issue #43** (`platform`). Deliberately
  not mass-deleted: crew_beta runs the same suite concurrently, and removing its
  temp dirs mid-run would fail its tests and be blamed on this PR.

## Correction (2026-08-23) — template fields

`docs/decisions/ADR-TEMPLATE.md` requires a `Conversation: <omnigent
conversation id>` field in the header. This ADR shipped without one —
`staff`, reviewing PR #85, noted the same omission on ADR-0007 and flagged
that this ADR has it too. Grepped `.omni/ledger.md`, `.omni/decisions.md`
and `.omni/registry-crew_alpha.json` for a wave-2 / ADR-0005 / issue-#31
conversation id before writing this: none is recorded anywhere recoverable.
Rather than invent a plausible-looking id for the original authoring
session, the field above is filled with `dd65182b03754fb3bc16c0cfd9c94d14`, the id of the session
that made this correction (2026-08-23, PR #85, alongside the same fix on
ADR-0007) — disclosed here so the provenance of that id is not mistaken for
the original wave-2 session’s.
