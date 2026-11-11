# ADR-0007: Invalid control-plane input is refused loudly, never clamped or silently accepted

Date: 2026-08-23 · Status: **proposed** — awaiting `staff` review
Deciders: crew_alpha lead · Proposers: crew_alpha lead (see "On the missing debate")
Issues: #77, #80 · Wave 3 · Base `d04039e`
Conversation: dd65182b03754fb3bc16c0cfd9c94d14 · Spec: none — no separate spec doc; issues #77/#80 are the spec
(field added retroactively 2026-08-23; the template requires it and this ADR
shipped without it — see "Correction (2026-08-23) — template fields" below)

> **Scope, restated 2026-08-23 (see "Correction — enforcement altitude" below
> for the full account):** this ADR’s Decision is enforced in exactly two
> verbs, `claim renew --ttl` and `checkpoint create --ref`. It is NOT a claim
> that the control plane as a whole refuses invalid input — four other sites
> with the identical defect shape are open and tracked (#86, #88). Read every
> "control plane" phrase below in Context and Options as the *motivating
> problem class*, not as a description of what ships in this PR.
>
> **Debt paid, 2026-08-23 (back-link):** the five remaining sites are now
> guarded by
> [ADR-0008](ADR-0008-refuse-invalid-ttl-control-plane-wide.md) / PR #90, which
> extends this ADR's Decision control-plane-wide. **This ADR's own Decision is
> unchanged** — an accepted decision is not rewritten; ADR-0008 extends it.
> With ADR-0008 merged, the narrowing described above is historical: the code
> now enforces at the altitude this ADR was originally written at.

## On the missing debate (read this first)

Phase 1 of this crew's charter requires two different-vendor engineers to propose
competing approaches before the lead reconciles them. **That did not happen, and
could not.** I re-probed rather than assuming wave-1's finding still held: a
`sys_session_send` to `pm` (title `77-80-input-validation-probe`, purpose
`explore`) from this session returned, verbatim,

    Error: sys_session_send requires parent session inbox

identical to the error recorded against ADR-0005 and the wave-1 registry
(`.omni/registry-crew_alpha.json`, `dispatch_mode.workers_dispatchable: false`).
Issue #13 is still open. Under the charter's fallback clause the lead builds
directly, holding the pre-granted slot (74) and edit lease (16) itself — recorded
in `.omni/registry-crew_alpha.json` `dispatch_mode` before this ADR, not
invented after the fact. **Consequence, stated plainly: no in-crew review, no
in-crew QA gate for this PR. Independence is supplied only by cross-crew
review**, which this PR will be routed to before anything is called done.

What stands in for a second proposer is executed evidence: every claim below
that could have gone the other way is backed by a command and its output, and
the two prior incidents this ADR fixes (the wave-3 checkpoint cut wrong twice,
and this hour's near-miss on `claim renew`) are both reproduced against the
pre-fix binary in `.omni/evidence/` before being fixed.

## Context

This project has shipped eight confident wrong answers — a control accepting
malformed input and reporting `ok: true` anyway — and every one was found by
execution, never by review. Two more of the same shape are open against the
control plane itself, the component every other crew treats as authoritative:

**#77.** `claim renew --task <t> --ttl <n>` computes `expires_at = now() + ttl`
and writes it unconditionally (`bin/nightshift-ctl:706-708`, pre-fix). The
published TTL formula (R-26) is
`ttl=$(python3 -c 'import time;n=time.time();print(int(n-(n%3600)+3600-n-180))')`,
which is **negative in the last 180 seconds of every hour**. A negative ttl
back-dates `expires_at`, so the renew that was supposed to extend the claim
instead releases it *at the instant it is granted* — and the command still
prints `{"ok": true, ...}`. Two drivers in a row lost the wheel this way; I
hit it myself this hour and hand-guarded the call with `[ "$ttl" -gt 300 ]`
before calling `nightshift-ctl` at all. A control that every caller must
remember to guard on the outside is not a control.

**#80.** `checkpoint create` (`bin/nightshift-ctl:849-871`, pre-fix), when
`--sha` is omitted, records `git rev-parse HEAD` in the shared checkout with
**no check that HEAD, or the caller's `--sha`, is reachable from any branch**.
`--ref` exists as a column and an optional flag but pre-fix is **never read
back** — it is accepted and stored, decorative. Reproduced live: the wave-3
checkpoint was cut twice wrong (`ff39fbb`, predating the work it was meant to
contract; `27f3915`, from the shared checkout's detached HEAD, not on
`program/discovery-state` at all) before a human re-cut it by hand as
`318037c` with an explicit `--ref` nobody enforced. `/srv/project` is detached
at `27f3915` at the time of writing this ADR:

```
$ cd /srv/project && git status
HEAD detached from cfdc88c
$ git log -1 --oneline
27f3915 program: 08:00Z second-driver reconciliation …
$ git merge-base --is-ancestor 27f3915 origin/program/discovery-state; echo $?
1        # not an ancestor — the recorded checkpoint is not on the branch
```

Both are the same defect class: the tool validates the SHAPE of the input
(float parses, string is non-empty) but not its MEANING, and reports success
regardless.

## Options considered

*(Both authored by the lead in the absence of a second proposer — see above.
Presented as the two-sided form the debate would have taken.)*

### A — Refuse loudly on invalid input, exit non-zero/non-3, no default substituted (CHOSEN)
Reject the request outright: `claim renew` with `ttl <= 0` (or NaN/inf) exits
before touching the row; `checkpoint create` exits before the `INSERT` if the
resolved `sha` is not `git merge-base --is-ancestor`-reachable from a
**required** `--ref`. The message names the bad value and the exact fix. No
row is ever written in a state the caller didn't ask for.

*Exit code:* **2**, not 1 or 3. This is not new — it is the existing, already
documented convention (`docs/CONTROL-PLANE.md` "Conventions": "0 = granted/ok,
3 = denied/held/cooling, 2 = usage error, 1 = internal error") and two call
sites already use it (`slot heartbeat` needing `--slot`/`--holder`; `vendor
check` needing `--vendor`/`--all`). Exit 3 is reserved and means something
specific org-wide ("never retry in a loop"); reusing it for "you typed
garbage" would teach callers to swallow a validation failure the same way
they're taught to swallow a real lock contention, which is worse than the bug
being fixed. Exit 1 is reserved for the tool's own internal failures (a `git`
invocation raising, a corrupt index) — a caller-supplied bad `--ttl` is not
that either. **2 is a loud, distinct, pre-existing signal; reuse beats
invention.**

### B — Clamp/normalize silently (a floor on ttl; fall back to HEAD-on-`--ref` or to `checkpoint latest`'s stored ref) — REJECTED
This is what the task brief calls out by name as "the same disease with
better manners." A clamped negative ttl still reports `ok: true` and the
caller still believes the claim is held for the ttl it asked for, when it
isn't. A silently-substituted ref for `checkpoint create` reintroduces exactly
the failure this ADR exists to close: a checkpoint whose recorded provenance
(what it was checked against) doesn't match what actually happened. Rejected
outright, not just for these two call sites but as a *pattern* — WA-008/009
and R-45 are about exactly this shape of bug, and clamping is how you write
a ninth one while believing you fixed the first eight.

### C — Warn but proceed (log the problem, still write the row) — REJECTED
Indistinguishable from today's behaviour to any caller that parses `ok` and
ignores stderr, which is every caller so far (`claim renew`'s own doc-comment
call sites, this ADR's own R-26 snippet). A control whose failure mode is "an
extra line of text nobody reads" is not a control either.

### D — Make `--ref` optional but default to reading it from the latest recorded checkpoint's `ref` — REJECTED, narrower reason
Considered specifically for #80 to reduce caller friction. Rejected because it
reintroduces an implicit default exactly where the bug lives: `checkpoint
latest`'s `ref` column is *itself* sometimes `NULL` (four of five rows in the
live `.omni/control.db` right now — ids 1-4 all have `ref: None`; only the
hand-corrected id 5 carries one). Defaulting to a frequently-absent value
degrades to "usually no check," which is what shipped the bug. Requiring
`--ref` explicitly is what would have stopped the caller from running
`checkpoint create` at all without first deciding which branch they meant.

## Decision

**A.** Both commands refuse with exit **2** and a JSON body naming the bad
input and the corrective action, before any row is written. (Scope note,
2026-08-23: "both commands" is the entire enforced surface of this ADR —
see "Correction — enforcement altitude" below for the sites that share the
defect and are NOT covered here.)

- `claim renew --ttl`: reject unless `ttl > 0` **and** `math.isfinite(ttl)`.
  Both checks are necessary: `ttl > 0` alone rejects `NaN` (neither `<`, `>`
  nor `==` a real number) but NOT `inf` (`inf > 0` is `True` in Python), so
  `inf` needs the explicit finiteness check or it passes straight through and
  writes a nonsensical, never-expiring `expires_at`. (Corrected 2026-08-23 --
  see "Correction" below; the guard as first shipped in this PR checked only
  `ttl > 0`, which does reject NaN but does not reject inf.) `-inf` was
  already caught by `ttl > 0` alone. `--ttl` omitted still falls back to
  `capacity.json`'s configured default, unchanged — this only closes the path
  where a *caller-supplied* value is garbage.
- `checkpoint create --ref` becomes **required** (`argparse(..., required=True)`).
  Whichever `sha` is used (explicit `--sha` or `git rev-parse HEAD`), the
  command runs `git -C <repo> merge-base --is-ancestor <sha> <ref>` before the
  `INSERT`. Exit code from that subprocess is inspected, not just its
  truthiness: `1` means "both resolved, sha genuinely isn't on ref" (the
  actionable case — a distinct, precise message); anything else means a ref or
  sha didn't resolve at all (a different message, since the fix a caller needs
  is different — check the spelling, not the checkout).

Left unchanged, out of scope, flagged not silently: `claim acquire --ttl`
already handles `ttl <= 0` by storing `NULL` (never-expires) rather than a
past timestamp — different code path, different existing behaviour, not the
`ok:true`-but-already-expired shape #77 names, and not touched here to keep
the diff on the two named issues.

## Consequences

**What this makes easy.** Every future caller of `claim renew` and
`checkpoint create` gets the same protection with zero code of their own —
the R-26 hand-guard (`[ "$ttl" -gt 300 ]`) becomes redundant defense-in-depth
rather than the only defense. A bad `checkpoint create` invocation fails at
the point of the mistake instead of three consumers and one ADR later.

**What this makes hard.** `checkpoint create` calls now MUST name a `--ref`;
any script relying on the old optional/ignored flag needs a one-line update.
Grepped the tree for other call sites before deciding this was acceptable —
only `tests/test_checkpoint.py` (3 call sites, updated in this PR) call
`checkpoint create` anywhere in the repo outside `.worktrees/`.

**What this forecloses.** A "just this once" positional/implicit checkpoint
without stating the branch. That is the point.

## Cost

No new dependency, no new operational surface — both checks use `git`,
already a hard requirement of `checkpoint create`. Cost is one extra
`subprocess.run` per `checkpoint create` call (git merge-base, milliseconds)
and one comparison per `claim renew` call. Unmeasurable at today's volume or
100×.

## One-way door?

**No, for #77.** Tightening `ttl` validation is reversible — a future ADR
could widen it again with its own justification.
**Partially, for #80.** Making `--ref` required is a breaking CLI change for
any caller that omitted it. Reversible in code, but every checkpoint recorded
under the old optional `--ref` (ids 1-4 in the live DB) stays `ref: NULL`
forever — this ADR does not backfill history, only stops it from recurring.

## Scale tier

None yet (`docs/architecture/SCALE-LADDER.md`) — single-writer SQLite, no
volume trigger crossed.

## Reversal trigger

If a legitimate caller needs `checkpoint create` without a known ref (e.g. a
throwaway/experimental checkpoint never meant to gate a worktree), that is a
new, explicit flag (`--no-ref-check` or similar) proposed and justified on its
own, not a reopening of this decision by omission.


## Correction (2026-08-23) — enforcement altitude

`staff` reviewed this PR and returned `inconsistent-with`
(github.com/tushardhara/nightshift/pull/85#issuecomment-5385322210). It did
not dispute the exit-2 convention, the refuse-don’t-clamp philosophy, or the
`--ref required` call — all three stand. Its finding is narrower: **this ADR
states its rule at control-plane altitude ("invalid control-plane input is
refused") while enforcing it at two-verb altitude (`claim renew`,
`checkpoint create`)**, and `staff` reproduced the identical defect shape
live, unfixed, at four more sites. An ADR whose stated scope exceeds its
enforcement is a promise the codebase does not keep, and a reader who takes
the title at face value would trust a guarantee that does not exist yet.

This section is that restatement, appended rather than folded silently into
the Decision above — the same reason the `inf` fix got its own dated
correction rather than a quiet edit.

**What is enforced, today, by this PR:** exactly `claim renew --ttl` and
`checkpoint create --ref`, as written in "Decision" above. Nothing else.

**What is not enforced, and is recorded here as explicit, itemised debt:**

| site | location | defect (same shape as #77) | tracked |
|---|---|---|---|
| `cmd_slot_heartbeat` | `bin/nightshift-ctl:578-611`, unconditional write at :606-608 | a negative or non-finite `--ttl` back-dates `expires_at` on the write, so the RPC that is supposed to renew a worker’s own slot instead releases it the instant it is called — and still returns `ok: true` | **#88** — **paid by ADR-0008 / PR #90** |
| `cmd_slot_renew_active` | `bin/nightshift-ctl:626-644`, unconditional write at :638-640 | identical defect, and it is applied to **every** active slot in a single watchdog-invoked call, not just one | **#88** — **paid by ADR-0008 / PR #90** |
| `cmd_slot_acquire` | `bin/nightshift-ctl:546,551` (`t + ttl if ttl > 0 else None`) | `--ttl <= 0` is not refused — it silently produces a **never-expiring** slot instead of an error | belongs with **#86**’s family (see below) — **paid by ADR-0008 / PR #90** |
| `cmd_lease_acquire` | `bin/nightshift-ctl:752` (`expires = t + ttl if ttl > 0 else None`) | the same silent never-expire shape, for leases | belongs with **#86**’s family (see below) — **paid by ADR-0008 / PR #90** |

**#88** (`slot heartbeat` + `slot renew-active`) is deliberately not fixed by
this PR even though `bin/nightshift-ctl` is `crew_alpha`’s owned file: both
verbs implement **ADR-0002**, the cross-seam liveness-and-ownership contract
that `crew_beta`’s watchdog consumes the semantics of. File ownership is not
contract ownership — changing what "renew" means here without routing it
through the crew that depends on it is exactly the kind of unilateral change
ADR-0002 exists to prevent. `staff` raised this distinction and the lead
adopted it as decision **R-54**: #88 is routed through the program as a
cross-crew decision, not built here.

**#86** (`claim acquire --ttl<=0`) was already named above under "Left
unchanged, out of scope" as next-wave work; this correction adds that the
identical never-expire shape in `slot acquire` and `lease acquire` belongs to
that same family of fix and should be picked up alongside it, not opened as
further separate issues.

This correction does not reopen the Decision (A is still chosen, exit 2 is
still the code, both named verbs are still exactly what this PR fixes) — it
corrects the altitude at which the ADR describes its own reach.

## Correction (2026-08-23) — template fields

`docs/decisions/ADR-TEMPLATE.md` requires a `Conversation: <omnigent
conversation id>` field in the header; this ADR shipped without one.
Added above as `dd65182b03754fb3bc16c0cfd9c94d14` — the session that authored this correction.
ADR-0005 has the identical omission (`staff` noted it too) and is fixed in
the same way, in the same PR; see that file’s own dated correction section.


## Correction (2026-08-23)

The guard as first shipped in PR #85 was `if not (ttl > 0):`. This ADR's
Decision section, and the PR body, both claimed at the time that this
rejects "negative, zero, NaN, inf". That claim was false for `inf`:
`inf > 0` evaluates `True` in Python, so `not (ttl > 0)` is `False` and an
infinite ttl passed the guard, writing `expires_at = inf` — a claim that
never expires, which `reap` can never free and which returns every future
`claim acquire` on that task_id exit 3 forever (a permanently wedged task).
This was caught by `crew_beta`'s cross-crew review, rated non-blocking
there, and re-rated blocking by the `crew_alpha` lead on the grounds that a
factual misstatement about what the guard rejects, inside the document that
IS the fix for "the control plane accepts invalid input and reports
`ok: true`", is an instance of the same defect class this ADR exists to
close.

Fixed same-day, same PR (#85), same branch: the guard is now
`if not (ttl > 0) or not math.isfinite(ttl):`, closing the `inf` gap with
the same exit code (2) and message shape. `-inf` required no code change
(`-inf > 0` was already `False`) and is covered by a control test, not a
new regression test. See `.omni/evidence/WA-77-80.txt` Part F for the full
before/after/mutation evidence. This correction does not reopen the ADR's
Decision (A is still the chosen option, exit 2 is still the code) — it
corrects what the Decision section said the chosen option's guard already
covered.
