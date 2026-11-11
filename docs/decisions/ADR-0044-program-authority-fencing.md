# ADR-0044: Program authority fencing — at most one Program may act

Date: 2026-08-29 · Status: accepted
Deciders: crew_alpha lead (single-author reconciliation, see Process note)
Proposers of record: `oncall` (Claude, PR #177), `architect` (Codex, PR #180), independent
Codex design audit (issue #174 comment `5413707219`)
Conversation: `174-phase1-design` · Spec: `.omni/reports/2026-08-29-0911Z-174-dor.md` (10 numbered
acceptance criteria, AC-1..AC-10)
Issue: #174 · Refs #102, #118, #169, #178, #179, #236 · Supersedes: none. ADR-0024
(PR #183/#187) covered the same issue but was retired unmerged in the 2026-08-26 backlog
reset (`docs/OPERATING-LESSONS.md:292,294`) for provenance/staleness reasons, not content — its
reasoning is prior art here, not a superseded accepted decision, because it was never accepted.

## Revision note (2026-08-29, round 2) — `staff` review on PR #296

`staff` reviewed this ADR (PR #296) and returned **inconsistent-with ADR-0006** on original
Decision §6, plus a **needs-a-wider-decision** on the Outcome's scope, which `program` settled
(`.omni/decisions.md` ~09:35Z entry, PR #298: widen to the DoR's full charter). Both are fixed in
this revision — see the corrected Decision §7 and §3b below, and the widened Outcome. One
FOLLOW_UP (the `standdown` status also needs `SCHEDULE_STATUSES`/the detector's raw-status set
widened before AC-7 is exercisable) is recorded in Decision §6 and carried into the Phase 2 spec,
non-blocking. Decision §§1–5 (as originally numbered), the no-force-break call, and the ROOT-policy
attachment were reviewed and found **consistent** with ADR-0033/ADR-0002/ADR-0036 — not
re-litigated here.

## Revision note (2026-08-29, round 3) — `174-implement-wiring`, issue #319

This item elaborates Decision §7's already-flagged follow-on work: the concrete, written
operational contract for how a Program turn acquires, renews, spends, and releases the §3/§3b
authority fence across one turn, and how it maps a lost admission race (AC-7's Program-side half)
and a mid-turn renewal failure to the `standdown` status instead of proceeding. It does **not**
reopen Decision §§1–7 — see Decision §7b below for the new content. `.omni/decisions.md`'s
2026-08-29 ~12:40Z entry names this item as the sole precondition for cutting
`program_authority_fence` over into the live-served policy; **this revision does not perform that
cutover** — it is the contract the program's own scheduled-task-prompt edit (`sys_scheduled_task_update`,
only the program can call it) must implement before cutover is authorized.

**Cross-vendor dispatch reconfirmed sterile again this round, live, not assumed from round 1 or 2's
record:** `sys_session_send` to both `qa` (title `test-174-implement-wiring`, purpose `explore`,
task/slot/generation fields set from a live-acquired slot) and `eng_claude` (title
`design-174-implement-wiring-a`) were rejected identically by the tool schema itself: `Input
validation error: Additional properties are not allowed ('generation', 'slot_id', 'task_id' were
unexpected)`. Matches issue #236 (still open) exactly — two distinct target agents, same closed
`args` schema, so the block is schema-level, not agent- or policy-specific. The QA planning slot
(id 14) was released unconsumed before this probe; the build slot below is held under
`crew_alpha/eng_claude` for bookkeeping only, per the same WA-5 convention `174-implement-fence`
and `174-implement-detector` already used — no live worker turn ran. Per Working Agreement #5, the
lead reconciles directly. Unlike the original ADR, there is no pair of independently-authored
prior design inputs to reconcile here — this is a single-author elaboration of an already-accepted
ADR's own explicitly-flagged remaining scope, not a fresh architectural choice, so the missing
independent challenge is narrower than round 1's. It is still named here, per the skill's own
single-vendor-fallback disclosure rule, and flagged in `STATUS.md`; `staff` review is requested
before this lands, as the substitute gate.

## Process note — why this is not a fresh two-vendor debate, disclosed under Working Agreement #5

This crew's charter requires two independently-dispatched, different-vendor engineers to propose
before any ADR. That dispatch was attempted and reconfirmed sterile **in this session, live**:
`sys_session_send(agent="qa", ..., args={..., task_id, slot_id, generation})` — the exact shape
`worker_semaphore` requires — was rejected by the tool schema itself: `Input validation error:
Additional properties are not allowed ('generation', 'slot_id', 'task_id' were unexpected)`. This
matches issue #236 (P0, still OPEN, confirmed via `gh issue view 236` this session,
`updatedAt: 2026-08-29T09:10:36Z`) exactly, and reproduces the same failure this org's own
`docs/OPERATING-LESSONS.md` and three prior crew_alpha sessions (#186, #214, and #233's review)
already recorded. No engineer, QA, or reviewer dispatch is reachable while #236 stands.

Per the established fallback (Working Agreement #5, precedent: issue #186 build, issue #214
build), the crew lead reconciles directly rather than blocking on an unreachable dispatch. This
case is better-evidenced than a bare single-vendor fallback: two **already independently
authored, different-vendor** design proposals exist on the record from the original 2026-08-25
incident — `oncall` (Claude) wrote PR #177 without sight of the architect's work; `architect`
(Codex) wrote PR #180 blind to PR #177 (compromised partially per #180's own disclosure, but the
core mechanism was derived independently before that). A third, independent Codex design audit
(comment `5413707219`) corroborates both on the load-bearing facts. This ADR reconciles those
three real, cross-vendor inputs — it is not one model's first idea unchallenged — but it is not a
fresh debate against **today's** code, and that gap is real: every citation below was
independently re-verified against current `main` (`4116fe13f6399e0c1ba308cad7b397a184c83b15`,
2026-08-29) by the lead, in an isolated worktree, rather than trusted from the 2026-08-25 record.

**Flagging to `staff` and in `STATUS.md`, per the skill's own instruction for single-vendor
fallback:** this reconciliation has not had a fresh independent challenge from a second
implementer reading current code. `staff` review before build is the gate that substitutes for
that missing challenge here — and did in fact catch two real gaps in round 1 (see Revision note),
which is exactly what this substitute gate is for.

## Context

Three `program` sessions ran concurrently on 2026-08-25 (issue #174). Demonstrated cost: six
conflicting adjudication comments on one PR, a false accusation against the `release` seat
(published then retracted), and a near-miss where two integrators nearly held simultaneous write
access to `main` — caught only because one Program happened to read the control plane back before
dispatching, not by any enforcement. The same class recurred 2026-08-28 (RTM readiness audit
reopened #174: "not historical-only"), and again live during this ADR's own review cycle
(`.omni/ledger.md` ~08:35Z/~09:53Z entries: a second concurrent Program session's direct
`claim acquire --claimant program` call during another Program's in-flight turn, denied only by an
unrelated ADR-0033/#236 admission-gap check — "the mechanism working as designed... but still
discipline-plus-luck, not a mechanical fence").

Nothing currently arbitrates between two Programs:
- **Slots** cap concurrent *workers*, not concurrent Programs, and are acquired by whichever
  Program asks.
- **Claims** are per `task_id` and advisory — two Programs choosing different task ids never
  collide.
- **The existing serialize lease is unusable as a role lock**: `expires=None` at acquisition
  (`bin/nightshift-ctl` lease acquire), release accepts only `pr-merged`/`dropped` (a role that
  produces no PR can never naturally reach either), `--holder` is optional on release, and
  `lease break` updates the active row with no holder/token check at all. A crashed Program would
  wedge the role forever — worse than today's advisory heartbeat, which at least goes stale.
- **The scheduler's `last_run_status` is observational**, check-then-start; manual runs bypass it
  entirely.

**Issue #179** ("Omnigent scheduler has no overlap control — a second Program cannot be prevented
from STARTING by any code we own") is **closed, `state:blocked`, no resolving fix** — confirmed
this session. True start-suppression requires a runtime-owner change (an atomic concurrency key at
session creation, shared by scheduled and manual starts) that has been asked for and not granted.
This ADR does not re-ask; it is recorded here as a standing, currently-declined dependency, and the
outcome below is capped accordingly.

## Outcome (from the DoR spec)

At any moment, **at most one Program-role session may take a control-plane-mutating action**. Per
the DoR's own charter (`.omni/reports/2026-08-29-0911Z-174-dor.md`), that action set is: (a)
dispatch to a direct-child seat (`sys_session_send`/`sys_session_create` targeting one of
`product, architect, crew_alpha, crew_beta, ux, staff, platform, security, release, oncall`); (b)
Program's own direct `nightshift-ctl claim acquire --claimant program` calls (claim-first recovery
performed by Program itself, not by a crew lead recovering its own task); and (c) Program's own
direct `nightshift-ctl slot acquire ... --oncall` calls (the incident-response reserve slot,
`program/config.yaml:255`, which only Program invokes). **Round 1 of this ADR narrowed this to (a)
only; `staff` caught the narrowing and `program` ruled it back to the full charter
(`.omni/decisions.md` ~09:35Z, PR #298) — see Decision §3b.** When two Program turns exist
concurrently, exactly one is admitted to act on any of (a)/(b)/(c); the other is denied *before* it
performs the action and stands down cleanly, preserving its own in-progress findings in the record.

**This is "at most one Program may act," never "at most one Program may start."** A losing Program
still starts, still runs, can still read, and can still take actions this mechanism does not gate:
file writes inside its own sandbox, generic shell, and — named explicitly rather than left
implicit, per `staff`'s round-1 finding — **PR/adjudication comments posted as Program via `gh`**,
and Program's own `lease acquire`/`lease break` calls administering resources on crews' behalf.
Those four are a genuinely different shape from (a)/(b)/(c): `gh` PR comments go through GitHub's
API directly, with no `nightshift-ctl`/policy hook in the path at all, so gating them would need an
authenticated wrapper around every `gh` invocation this org makes, not an addition to this
mechanism. `lease acquire`/`lease break` have no existing per-call "acting as Program" marker the
way `claim acquire --claimant program` and `slot acquire --oncall` already do (see Decision §3b),
and are structurally mitigated already by the DB's own single-active-lease-per-resource
uniqueness, which the identified live collision (claim acquire) did not have. Whether those four
must also be fenced is a real open question this ADR does not resolve — recorded as a named gap in
Consequences and left for a follow-up decision, not silently dropped.

## Options considered

### A — Fenced role lock, generation-named, permit encoded in a new `sys_session_send` field (`oncall`, PR #177, Claude)

A single active-row lock under a partial unique index (the same shape as this org's existing
`one_active_claim`/`one_active_lease`), monotonic generation, TTL-bound, token-and-generation
checked renew/release. Its ROOT-policy gate design reads `arguments.args.role_token` /
`arguments.args.role_fence` off the dispatch call.

**Rejected on one decisive, independently-reproduced fact**: `sys_session_send`'s `args` object is
a closed JSON schema (`additionalProperties: false`), and the top-level dispatch parameters object
is closed too. A caller cannot add a field. This is not a citation from the 2026-08-25 record —
this session reproduced it directly (see Process note above): an out-of-schema key is rejected by
the harness's MCP client before any policy ever sees the call. Any design that needs a
caller-supplied carrier field is not implementable against the tool surface as it exists today, in
this harness (`program` runs `harness: claude-sdk`; the reversal trigger below names what changes
if that ever stops being true).

Its authority-lock *shape* (partial-unique-index active row, monotonic generation, TTL, no
anonymous release) is sound independent of the carrier-field problem and is adopted below.

### B — Out-of-band capability + one-shot dispatch permit, digest of the canonical call, no new field (`architect`, PR #180, Codex; corroborated by the independent Codex audit)

A dedicated `program_authority` singleton (generation, token hash, TTL, no unauthenticated or
authenticated break — recovery is TTL-driven generation advance only) plus `program_action_permits`
(one-use, short-TTL, keyed by the SHA-256 digest of the *exact* call about to be made: tool name,
target agent, title, full canonicalized args). A ROOT policy in `program/config.yaml` intercepts
`sys_session_send`/`sys_session_create` when the target agent is a Program direct-child seat,
recomputes the digest of the call actually being made, and atomically consumes a matching,
unexpired, current-generation permit. Nothing new travels in `args` — the policy inspects the call
that already exists, exactly as `worker_semaphore` already inspects `args.slot_id` for worker
dispatch.

**Adopted for the dispatch surface.** Needs no schema change (verified live, this session),
explicitly rejects the existing serialize lease for the same three concrete defects
(`expires=None`, `pr-merged`/`dropped`-only release, holder-unchecked `lease break`) independently
re-derived from oncall's PR #177, and states its own scope boundary honestly (does not prevent
starting; does not authenticate the caller seat, since #118 already proved no authenticated
caller identity reaches policy evaluation; does not fence non-dispatch side effects — this last
point is exactly what `staff`'s round-1 review caught as under-scoped relative to the DoR, fixed
in Decision §3b below).

### C — Reuse/extend the existing serialize lease (PR #175's mixed serialize section)

**Rejected**, independently, by all three prior inputs and re-confirmed here: no TTL, no
reachable release event for a role that produces no PR, identity-optional release, and a `lease
break` with no holder check. A crashed Program would wedge the role permanently. Not
reconsidered.

## Where A and B genuinely disagreed, and the call made here

**Force-break vs. no-force-break for a stuck/crashed holder (AC-10).** PR #177 (A) allows an
*authenticated* break (token-and-generation checked) while rejecting only *unauthenticated*
break. PR #180 (B) has no break command of any kind — the only recovery path is TTL expiry
advancing the generation.

**Decision: no force-break, adopting B.** An authenticated break requires presenting the live
token; a crashed holder cannot present anything, so an "authenticated break" in practice means
some *other* path holds or can reconstruct the token out of band — which recreates the exact
unauthenticated-break shape that made the existing serialize lease unusable, just one layer
removed. TTL is short by design here (recommended: 30-minute authority TTL, renewed before each
permit and at least every 10 minutes during an active turn; 60-second permit TTL — see
Implementation notes), so the operational cost of "wait for expiry" is bounded to tens of
minutes, unlike the old serialize lease's unbounded exposure. This is the one point where this
ADR does not simply average the two inputs — it picks a side and states why, per the charter's
instruction not to split a real disagreement. `staff` reviewed this reasoning against
`cmd_lease_break` (`bin/nightshift-ctl:1283-1291`, unconditional, no holder check — the thing being
deliberately avoided) and found it consistent; not re-litigated.

**Everything else the two agreed on** (partial-unique-index active-row lock; TTL-bound; no
anonymous/unauthenticated release; digest-bound one-shot permits; ROOT-policy-only enforcement
point for the dispatch surface; explicit "does not prevent starting" framing) is adopted without
further debate — agreement across two independently-authored, different-vendor proposals is this
project's cheapest confidence signal.

## Decision

1. **New control-plane state** (additive `CREATE TABLE IF NOT EXISTS`, `.omni/control.db`,
   alongside the existing tables in `bin/nightshift-ctl`'s init path — satisfies AC-9 by
   construction, since nothing existing is altered):
   - `program_authority` — singleton key `'program'`; `generation INTEGER NOT NULL` (naming
     aligned to this org's existing vocabulary — ADR-0002 §5 already names this primitive
     `generation`, not `fence`; B's proposal text uses `fence` throughout and is renamed here for
     consistency, substance unchanged); `token_hash TEXT NOT NULL` (SHA-256 of a ≥256-bit
     `secrets`-generated token, the raw token never stored); `holder_label TEXT NOT NULL`
     (diagnostic only, never authenticating — #118 already proved no authenticated caller seat
     reaches policy evaluation); `acquired_at`, `renewed_at`, `expires_at`, `released_at`,
     `state ∈ {active, released, expired}`.
   - `program_action_permits` — `id`, `generation`, `action_digest` (SHA-256 of the canonicalized
     `{tool, agent, title, args}` about to be dispatched — for the ROOT-policy path in §3 — or of
     the canonicalized CLI invocation — for the `nightshift-ctl`-internal path in §3b), `expires_at`,
     `consumed_at`.
2. **New command group** `nightshift-ctl authority {acquire, renew, permit-dispatch, release,
   show}`. `acquire`/`renew` require a positive, finite, capped TTL; a live incumbent makes
   `acquire` exit 3 (read-only standdown, no mutation — AC-2). `renew`/`release`/`permit-dispatch`
   require both token and current generation; wrong token, wrong generation, or expired row are
   all denied (AC-5, AC-6). Generation is allocated in the same transaction that expires the
   incumbent, never reused (AC-4).
3. **ROOT policy** in `program/config.yaml`, same file and same attachment pattern as the existing
   `worker_semaphore`/`release_provenance` blocks (`program/config.yaml:517-538` at this SHA):
   intercepts `sys_session_send`/`sys_session_create` only when `agent` is a Program direct-child
   seat; canonicalizes the exact call; looks up an unexpired, unconsumed permit for that digest
   bound to the currently-active generation; consumes it with a compare-and-set update; ALLOWs iff
   exactly one row changed, DENYs otherwise, fail-closed on any DB/policy error (AC-2, AC-6, AC-8).
   This reuses the exact attachment point and load-proof pattern (`release_provenance` canary,
   `program/config.yaml:522-530`) this project already trusts for `worker_semaphore` — AC-8's "not
   just configured" bar is satisfied the same way that policy already satisfies it: an exact-SHA
   live-dispatch test, not a YAML read. **This covers Outcome (a) only.**
4. **§3b — second attachment point, inside `nightshift-ctl` itself, covering Outcome (b) and (c)
   (widened per `program`'s ruling, `.omni/decisions.md` ~09:35Z, PR #298).** `sys_session_send`
   is a policy-gated tool call; a Program session's own direct shell invocation of
   `nightshift-ctl` is not — there is no policy hook on `sys_os_shell`, confirmed by this session's
   own unrestricted use of it throughout this ADR's research. The only enforcement point available
   for a bare CLI invocation is inside the CLI's own command handlers. This is **buildable, not
   infeasible** — `nightshift-ctl` is crew_alpha's own code — but it needs a caller-identity signal
   these commands don't uniformly have today:
   - `claim acquire` already carries `--claimant`, free text, required. `--claimant program` is
     the existing, already-distinguishing marker for "Program is recovering a task in its own
     capacity" (as opposed to `--claimant crew_alpha`/etc., a crew's own claim-first recovery of
     its own task — unaffected, not gated). `cmd_claim_acquire` gains: when `args.claimant ==
     "program"`, require a live, current-generation authority token (via `--authority-token-stdin`
     or an env var, never argv, matching §2's own token-handling rule) before the claim row is
     inserted; DENY (exit 3, no mutation) on missing/wrong/expired token or generation, with a
     denial reason distinct from the existing "already claimed" case so the two failure modes stay
     distinguishable in tests and logs.
   - `slot acquire` already carries an `--oncall` flag (`program/config.yaml:255`) that already
     distinguishes "Program acquiring its own incident-response reserve slot" from ordinary crew
     worker-slot acquisition (e.g. `crew_alpha/qa` acquiring a planning slot for its own worker,
     used earlier in this session, correctly **not** gated by this mechanism). `cmd_slot_acquire`
     gains the same token check, scoped to `args.oncall is True` only.
   - `lease acquire`/`lease break` (Program administering a resource on a crew's behalf) and
     GitHub PR/adjudication comments posted as Program via `gh` are **explicitly not gated by this
     ADR**, named here rather than silently dropped: `lease acquire`/`lease break` have no
     existing per-call marker equivalent to `--claimant`/`--oncall` today, and adding one is a
     larger, not-yet-scoped change to two more command surfaces beyond what live evidence
     (`claim acquire`) actually demonstrated colliding; they are additionally already
     structurally mitigated by the DB's own single-active-lease-per-resource uniqueness, which
     `claim acquire`'s advisory-per-task-id model does not have. `gh` PR comments have no
     `nightshift-ctl`/policy hook in their path at all — gating them needs an authenticated
     wrapper around every `gh` invocation this org makes, a materially different and larger
     mechanism, not an extension of this one. Both are recorded as a **named, explicitly-labelled
     interim gap** per the DoR's own honesty clause (open question 3), not claimed fixed, and are
     candidates for a follow-up issue rather than blocking this ADR or #174's closure.
5. **No force-break.** Recovery from a crashed/stuck holder is TTL expiry advancing the generation
   only. Recorded and justified above.
6. **Scheduler status fix (AC-7).** A Program that loses the authority race and stands down
   records its scheduled-run observation with a **new, distinct status** — `standdown` — via
   `nightshift-ctl schedule observe --status standdown ...`. It **must not** reuse `skipped`.
   `standdown` is deliberately absent from `SCHEDULE_INCIDENT_STATUSES`
   (`bin/nightshift-ctl:1644`, re-verified at this SHA — line drifted from the audit's original
   citation at `:1581`, content unchanged) and from the detector's mirrored `INCIDENT_STATUSES`
   (`deploy/nightshift-scheduled-run-detector.py:35`). A test asserting `"standdown" not in
   SCHEDULE_INCIDENT_STATUSES` and an equivalent detector-side assertion are required acceptance
   evidence, not optional polish.

   **FOLLOW_UP, non-blocking, named for Phase 2 (`staff` round-1 finding 3):** `standdown` cannot
   actually be written yet. `schedule observe --status` is `argparse` `choices=SCHEDULE_STATUSES`
   (`bin/nightshift-ctl`), a closed set that does not include it — calling `--status standdown`
   today is `invalid choice`, exit 2, before any table is touched. The detector
   (`deploy/nightshift-scheduled-run-detector.py:316-318`) independently closes over an even
   narrower raw-status set (`{scheduled, running, succeeded, failed, skipped}`) and raises
   `DetectorError("run returned an unsupported status")` for anything else. **The Phase 2
   implementation must widen both `SCHEDULE_STATUSES` and the detector's raw-status set to admit
   `standdown` as its own first step** — AC-7's acceptance evidence cannot be exercised until then.
   Named here so it is not rediscovered as a surprise mid-build.
7. **Cross-seam dependency, corrected** (`staff` round-1 finding: **inconsistent-with ADR-0006** —
   original text below is wrong and is struck, not merely amended, because the error itself is the
   finding). ~~The scheduled entry point that launches a Program turn
   (`deploy/nightshift-heartbeat.sh` / its systemd unit) must call `nightshift-ctl authority
   acquire`...~~ — **`deploy/nightshift-heartbeat.sh` does not launch a Program turn and must
   never be entangled with one.** `staff` confirmed live via `sys_scheduled_task_list` that the
   Program turn is created directly by Omnigent's own scheduler (task `729c865e...`, `rrule:
   FREQ=HOURLY`), with no `deploy/**` script in the critical path at session-start time.
   `nightshift-heartbeat.sh` is the fixed 5-minute watchdog-liveness timer ADR-0006 explicitly
   fences off from exactly this kind of entanglement — its own header: "MUST NEVER write
   `heartbeat:program`. That signal belongs to the Program"; ADR-0006 D1's own test ("what stops
   this writer when the attested thing stops?") fails for any change coupling it to Program-turn
   boundaries, by design. **Corrected:** the "first action of a scheduled Program turn" this ADR
   needs can only be realized inside the scheduled task's own `prompt` (edited via
   `sys_scheduled_task_update`, not a tracked repo file today) or inside `program/config.yaml`'s
   own instructions if the prompt inherits from there. Both are `program/**`-adjacent: **no crew
   owns this path** (`docs/owners.md:22`, same ADR-gated, no-crew-ownership boundary this ADR's
   own §3 ROOT-policy change already reaches into) — **not `deploy/**`, and not crew_beta.** This
   ADR specifies the contract (the CLI call — `nightshift-ctl authority acquire`/`schedule
   observe --status standdown` on loss — that the scheduled task's own prompt must perform as its
   first action); the wiring itself is flagged to the program to route to whoever authors
   Program's own scheduled-task prompt, once this ADR clears and Phase 2 is chartered — not to
   crew_beta, and not built here.

7b. **Program-turn operational contract (round 3, issue #319) — how a turn actually uses
   §2/§3/§3b/§6.** Closes AC-3 (long-turn auto-renew, no coverage gap), the Program-side half of
   AC-7 (a correct stand-down is never a #132 liveness alert), and gives AC-8 (enforcement proved
   loaded) a proof at the full-sequence level, not just the single-policy-call level
   `174-implement-fence` already gave it (`tests/test_authority_fence.py::RootPolicyProvenLoadedFromConfigYaml`).

   **Constants.** Authority TTL 30 minutes, permit TTL 60 seconds (both this ADR's own
   Implementation-notes recommendation, preamble to Decision §2, and `bin/nightshift-ctl`'s capacity
   defaults `authority_ttl_seconds`/`authority_permit_ttl_seconds`). **Renewal cadence: before every
   `permit-dispatch` call, and at minimum every 10 minutes of elapsed turn time even with no
   dispatch in between — whichever trips first.** Ten minutes leaves a 20-minute margin inside the
   30-minute TTL for one retried renew before expiry.

   **Detection.** Program tracks its own turn-start wall-clock time and the `generation`/`token`
   from its `acquire` response locally, and compares elapsed-since-last-renew (a local counter, not
   a fresh `authority show` read — a local value cannot itself race a concurrent generation advance
   the way a second round-trip could) against the 10-minute bound before every dispatch and at
   least every 10 minutes regardless.

   **The full ordered sequence, verbatim, for a Program turn's own scheduled-task prompt:**

   1. At turn start: `nightshift-ctl authority acquire --holder-label <run_id-or-session_id> --ttl
      1800`.
      - **Exit 0 (granted):** record `generation`/`token` (never argv again — every later use is
        `--token-stdin`, per §2); set `renewed_at_local = now()`; proceed to step 2.
      - **Exit 3 (denied — a live authority already holds the fence, AC-7's Program-side loss):**
        perform **zero** dispatch attempts of any kind toward a `PROGRAM_DIRECT_CHILDREN` seat, and
        no `claim acquire --claimant program` / `slot acquire --oncall` either. If scheduler-launched,
        the turn's only control-plane-mutating call is exactly
        `nightshift-ctl schedule observe --task <task> --key <key> --run <run> --scheduled-at <ts>
        --status standdown --class none`; write in-progress findings to the record (this ADR's own
        Outcome language — a losing Program still reads and logs, it just does not act); end the
        turn. **Never** reuse `skipped`/`failed`/`missed`/`stuck` for this case.
   2. Before every dispatch to a `PROGRAM_DIRECT_CHILDREN` seat (`product, architect, crew_alpha,
      crew_beta, ux, staff, platform, security, release, oncall`):
      a. If `now() - renewed_at_local >= 600`: `nightshift-ctl authority renew --generation <gen>
         --token-stdin --ttl 1800`.
         - Exit 0: `renewed_at_local = now()`; continue.
         - **Exit 3 (mid-turn loss):** stop. Do **not** call `authority acquire` again mid-turn — that
           would race a fresh acquisition against whatever now holds generation N+1, and this ADR's
           no-force-break/no-ABA guarantees (AC-6) make an old generation's resumption meaningless
           even if it won. If scheduler-launched, issue the same `schedule observe --status
           standdown --class none` call as step 1's denial path; end the turn, no further dispatch.
      b. `digest = nightshift-ctl authority digest --tool sys_session_send --agent <agent> --title
         <title> --args-json '<the exact args object about to be sent>'` (or `sys_session_create`
         for the tool field, matching whichever call is about to be made).
      c. `nightshift-ctl authority permit-dispatch --generation <gen> --digest <digest>
         --token-stdin --ttl 60`.
         - Exit 0: immediately issue the dispatch call with **exactly** the args object digested in
           (b) — any mutation between digesting and dispatching changes the canonical payload and
           the ROOT policy's own recomputation denies it
           (`tests/test_authority_fence.py::test_digest_mismatch_denied`). Recompute and request a
           fresh permit rather than retrying a stale one.
         - Exit 3: do not retry blindly. Re-check `authority show`; if a newer generation is active,
           this turn has been fenced out — follow step 2a's exit-3 path.
   3. At turn end (successful completion, not a stand-down): `nightshift-ctl authority release
      --generation <gen> --token-stdin`. A release failure is logged, not retried — TTL expiry
      (AC-4) reclaims the fence regardless, and a turn that is ending has nothing further gated by
      this mechanism to protect.

   **Existing mechanical backing, cited not re-proved:** step 2a's continuous-validity claim is
   `tests/test_authority.py::test_renew_before_expiry_keeps_continuous_validity`; the exit-3
   wrong-token/generation claims in steps 1, 2a, 2c are
   `tests/test_authority.py::test_wrong_token_or_generation_denied_on_renew_release_permit`.

   **New proof, this item:** `tests/test_authority_fence.py`'s new
   `ProgramTurnSequenceProvenLoadedFromConfigYaml` class exercises steps 1–3 end-to-end against the
   real, `program/config.yaml`-resolved `program_authority_fence` evaluator (the same resolution
   method `RootPolicyProvenLoadedFromConfigYaml` already uses, applied to a full turn instead of one
   call): acquire, two permit-dispatch→send round trips against two different
   `PROGRAM_DIRECT_CHILDREN` agents (both ALLOW), and the negative case — a dispatch attempted
   against that same real evaluator with no live authority for the current generation (DENY).

   **Not covered by this contract, named rather than assumed:** actually flipping the live-serving
   process's guardrails config to enforce `program_authority_fence` (a separate program/platform
   cutover action, gated on this item per `.omni/decisions.md`'s ~12:40Z entry, not performed here),
   and editing Program's own scheduled-task prompt to perform steps 1–3 (only the program can call
   `sys_scheduled_task_update` — this section is the contract that edit must implement, per Decision
   §7's own corrected text).

## Consequences

**Makes easy:** a mechanical, testable guarantee that two Programs cannot both authorize a
release/crew dispatch, or both attempt claim-first recovery, or both grab the incident-response
reserve slot, in the same window — covering both the dispatch-shaped failure from the 2026-08-25
incident and the direct-CLI-shaped near-miss this ADR's own review cycle surfaced live
(`claim acquire --claimant program`). Reuses proven-loaded policy machinery rather than inventing a
new loading path for the dispatch surface (directly addresses #178's "a policy nobody proved
loaded" failure mode for that gate).

**Makes hard / forecloses:** does not prevent a second Program from starting (declined dependency,
#179). Does not authenticate the calling seat generally — #118 already proved no authenticated
caller identity reaches policy evaluation, so both attachment points rely on possession of an
ephemeral capability, not identity. **Four action shapes remain explicitly unfenced**, named rather
than left implicit: a losing Program's non-dispatch, non-claim, non-oncall-slot actions (generic
file writes, generic shell), PR/adjudication comments posted as Program via `gh`, and Program's own
`lease acquire`/`lease break` administration on crews' behalf. The first is unavoidable without
runtime-supplied authenticated caller identity this org does not have. The other three are
buildable in principle but out of scope for this ADR — recorded as a follow-up candidate, not
silently dropped, per `program`'s own reversal trigger on the scope-widening ruling. Does not fix
#169 (nested worker dispatch/slot binding) or #84/#93 (durable cross-parent result collection) —
deliberately out of scope, per both source proposals' own scope discipline. A TTL-driven generation
transfer can occur while an old-generation Program is mid-turn; already-admitted worker dispatches
continue (unaffected — this mechanism only gates *new* actions in the fenced set), but the old
Program cannot renew, release, or act further in that set after expiry even if it resumes.

## Reversal trigger

Revisit this ADR if: (a) `program` ever stops running under `harness: claude-sdk` — Decision §3's
closed-schema premise is harness-local, not a control-plane invariant, and a different harness may
reopen Option A's field-carrier approach; (b) issue #179 is ever granted (runtime-owner
start-suppression becomes available) — the outcome statement and Decision would need to be
widened, not just extended; (c) issue #236 clears — any follow-up change to this mechanism should
get a real two-vendor dispatch rather than another single-lead reconciliation, per this org's
standing WA-5 reversal rule; (d) `staff` review finds the no-force-break call unacceptable given
operational experience with the recommended 30-minute TTL — the TTL value, not the no-force-break
principle, would be the first thing to revisit; (e) per `program`'s own reversal trigger on the
scope-widening ruling (PR #298): if Phase 2 finds §3b's `--claimant program`/`--oncall` attachment
points are not actually sufficient to prevent a real collision once built and tested, or a new
collision shape is found outside `claim acquire`/`slot acquire --oncall`, revisit whether
`lease acquire`/`lease break`/PR-comment gating must be pulled into scope rather than left as a
named follow-up.
