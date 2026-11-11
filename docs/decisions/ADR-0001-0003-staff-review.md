# Staff review: ADR-0001, ADR-0002, ADR-0003

Reviewer: `staff` (Claude) · Date: 2026-08-22
Scope: cross-cutting consistency only. Bug reality, seam count, and DoD are out of
scope by the program's instruction and are not re-litigated here.

I am one of the two authors ADR-0001/0002 were reconciled from (`PROPOSAL-claude.md`).
Where a decision traces to my draft I have tried to hold it to the same bar as
Codex's half — the findings below fall almost entirely on gaps in the **merged**
contract, not on which vendor proposed which half, and I've flagged the one place
I think the merge (not either input) introduced a problem.

---

## Verdicts

| ADR | Verdict |
|---|---|
| ADR-0001 — stack and shape | **consistent-with** existing conventions, with two documentation-currency gaps named below (not blocking, but should close before crews rely on the docs the ADR points at) |
| ADR-0002 — liveness and ownership contract | **consistent-with** existing conventions and code (no second mechanism invented, migration path follows the file's own precedent) — **conditional**: the contract's central claim, "worker-owned renewal proves the corresponding live run," is not true of the primitive the contract expects crews to reuse, and the ADR text does not flag that this primitive needs new logic, not just new callers. See §2. |
| ADR-0003 — sandbox capability | **consistent-with**. Line citations verified against the live scripts; the decision forecloses nothing and correctly keeps the AppArmor profile as the owner's action. |

---

## 1. ADR-0001 against the code

The core claim — SQLite/WAL holds by orders of magnitude, no ladder tier moves — is
supportable, but the two documents ADR-0001 treats as settled are not actually in the
state it implies:

- **`docs/architecture/CAPACITY.md` is the unfilled template**, verbatim (`CAPACITY.md:1-28`
  — every table cell and every "Budget" line is blank). The ladder's own rule is
  "No datastore, queue or runtime may be named until this exists" (`CAPACITY.md:3-4`),
  yet ADR-0001 names SQLite and cites cost arithmetic that lives only inside the two
  blind proposal documents (`PROPOSAL-claude.md:154`, `PROPOSAL-codex.md:482`), which are
  discovery artifacts, not living reference docs. The arithmetic is sound — I traced it
  independently against `tests/test_stress.py` and `bin/nightshift-ctl`'s `BEGIN IMMEDIATE`
  pattern and it holds — but the canonical capacity model the process requires does not
  exist yet. Recommend: transcribe the numbers into `CAPACITY.md` before milestone 1
  closes, so a later reviewer isn't sent to a discovery-only doc to find them.
- **`docs/architecture/SCALE-LADDER.md` is still the generic template**, not edited for
  this project. Its own instruction is "Delete the rows this project does not touch. Add
  the ones it does" (`SCALE-LADDER.md:21`), but the table still lists `storage: Postgres,
  one instance` as tier 0 (`SCALE-LADDER.md:11`) for a project that runs SQLite, plus
  `files`, `search`, `realtime` rows this system has no version of at all. None of this
  changes the verdict — "T0, nothing moves" is trivially true of rows that don't apply —
  but a ladder that hasn't been pruned to the real system can't actually be used to catch
  a tier jump later, which is the entire point of keeping it. No trigger in the ladder
  has fired; I checked each of ADR-0001's six named reversal conditions (sustained write
  demand, `SQLITE_BUSY`, p95 lock wait, WAL checkpoint starvation, second host, dump/backup
  contention) against `bin/nightshift-ctl` and none is instrumented or observed — so this
  is a "not yet measured" state, not an ignored trigger. Fine for now; flag it so it
  doesn't quietly become the latter.
- **ADR-0001 point 5 says "Boundaries in `docs/owners.md`"**, but `docs/owners.md:1-11`
  still has only the generic platform-owned rows (migrations, lockfile, CI config, shared
  libs) and the placeholder `_(add a row per crew slice below)_` — it does not carry the
  two-seam split or the `deploy/policies/**` exception that `.omni/decisions.md` row A3
  says was "Recorded in `docs/owners.md` as an explicit exception." It wasn't. A lead
  opening `docs/owners.md` cold at wave kickoff to find out who owns `deploy/policies/**`
  will find nothing there and may default to the generic `deploy/**` = host-seam
  assumption, which is the wrong answer per the reconciliation. This is cheap to fix (one
  table row) and should happen before either crew starts, not discovered mid-wave.

None of these are inconsistencies in ADR-0001's reasoning — they're gaps between what it
points readers at and what those documents currently contain. I'd block on none of them
individually, but the `docs/owners.md` one is the one I'd want closed first, since it's
the thing a lead actually consults to know what they're allowed to touch.

---

## 2. ADR-0002 against the actual code — the consequential finding

**Does `bin/nightshift-ctl` have what the contract assumes?**

- **A `meta` table**: yes, already exists, empty, unused (`bin/nightshift-ctl:55`,
  `CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`; grep of the
  whole file shows no other reference to it). It's a good fit for the Program heartbeat
  record the ADR wants: one JSON blob under a fixed key, no schema change, one
  `INSERT OR REPLACE` inside the existing `BEGIN IMMEDIATE` pattern (`bin/nightshift-ctl:231`).
  This part of the contract is right-sized — smaller than the ADR's prose implies, not
  larger.
- **A place for a fencing generation on claims and slots**: no. `task_claims`
  (`bin/nightshift-ctl:62-70`) and `worker_slots` (`bin/nightshift-ctl:71-78`) have no
  generation column today. This *is* a real schema change, but it fits a convention
  already in the file: `ensure_schema` (`bin/nightshift-ctl:131-144`) already does additive
  `ALTER TABLE ... ADD COLUMN` for `dispatched_at`/`bound_agent`/`bound_title`, plus a
  column-rename migration for the pod→crew change. Adding `generation INTEGER` to both
  tables the same way is consistent with that convention, not a new mechanism — but the
  ADR should say so explicitly rather than leaving crews to discover the pattern by
  reading `ensure_schema` themselves, and it should name that the enforcement point
  ("completion succeeds only if that generation is still current") doesn't exist anywhere
  yet either — neither `cmd_task_upsert` (`:602-620`) nor `cmd_pr_create` (`:778-818`)
  consults `task_claims` or `worker_slots` at all today. This is new plumbing, not a
  column plus a check that already has somewhere to live.
- **A transactional path for a heartbeat row**: yes, `begin()`/`COMMIT` around every
  mutation is the file's only pattern (`bin/nightshift-ctl:231` and every `cmd_*`
  function). Nothing new needed here.

**The finding that matters more than the schema gap.** ADR-0002 decision item 4 sets a
specific bar for the replacement of blanket renewal: *"narrowed to a per-slot operation
requiring **proof of the corresponding live run**."* The primitive the ADR expects
crews to wire workers onto for "worker-owned renewal" **already exists** —
`nightshift-ctl slot heartbeat` (`bin/nightshift-ctl:305-317`, parser at `:907-910`) — and
is already tested (`tests/test_slots.py:120-132`, `test_heartbeat_keeps_slot_alive`). But
it proves nothing about the caller: the UPDATE is `WHERE id=? AND state='active'`
(`bin/nightshift-ctl:311-312`) with **no holder or agent check at all**, and the CLI
parser accepts only `--slot` and `--ttl` (`bin/nightshift-ctl:907-909`) — no `--holder`,
no `--agent`. Slot ids are small monotonic integers visible to anyone via `slot list`
(`bin/nightshift-ctl:349-360`). As written, any process that knows or guesses a slot id
can renew it — which is a narrower version of exactly the class of defect ADR-0002 exists
to close (a signal treated as proof of liveness when it proves nothing about who is
actually alive).

The codebase already has the fix pattern for this, one function away:
`consume_slot` (`bin/nightshift-ctl:146-167`) checks `holder == agent or
holder.endswith("/" + agent)` before it will bind a slot to a dispatch
(`bin/nightshift-ctl:158-160`). `cmd_slot_heartbeat` needs the same check before it
extends a TTL, and this is the same shape of fix the DoD already requires on the claims
side — `claim release`/`claim renew` currently take no `--claimant` and don't verify the
caller (tech-debt row 2, `.omni/tech-debt.md:11`; DoD condition 7, requiring `--claimant`
verification, `docs/prd/DEFINITION-OF-DONE.md:176-182`). ADR-0002 should say, explicitly,
that `slot heartbeat` needs the same identity check `slot consume` already has — not
imply that wiring workers onto the existing verb satisfies decision item 4 as written,
because it doesn't.

**Consequence if this is missed:** milestone 1 ships something that *looks* like it
satisfies ADR-0002 — workers call `slot heartbeat` instead of the watchdog calling
`renew-active` — while the actual guarantee ("this worker is still alive") is unchanged
from today's blanket renewal, just scoped down from "every slot" to "any slot whose id
you know." That is a real improvement (bounds the blast radius) but it is not the
"proof of the corresponding live run" the ADR promises, and nothing in the acceptance
criteria as drafted would catch the gap, because the existing test for this command
(`test_heartbeat_keeps_slot_alive`) never asserts identity at all.

**Test cost, named now:**
- `tests/test_slots.py:108-119` (`test_renew_active_respects_max_age`) and `:120-132`
  (`test_heartbeat_keeps_slot_alive`) do not pass a holder/agent to `heartbeat` today.
  If `cmd_slot_heartbeat` gains the identity check above, these two tests need updating
  to pass the holder and to add a rejection case for a mismatched holder — mirroring the
  `--claimant` fix already planned for claims. **This is a control being strengthened,
  not weakened** — worth saying plainly, since the brief forbids weakening controls and a
  reviewer skimming a diff that touches `test_slots.py` should not have to re-derive that
  from the diff.
- `tests/test_claims.py` and `tests/test_recovery.py` do **not** need to change for the
  fencing-generation addition itself: the column is additive (existing `INSERT` statements
  name columns explicitly, e.g. `bin/nightshift-ctl:361-370`, so a new nullable column
  doesn't break them), and none of the four existing claim tests or the two recovery tests
  exercise a completion/integration path — there is no completion path today that would
  need to consult a generation. New tests (DoD's `RT-19`, `RT-20`) are additions, not
  modifications. Cost here is smaller than for the slot-heartbeat identity check, and
  should not block milestone 1's start.

---

## 3. The `slot renew-active` deletion — staged path

Grep of the whole tree found exactly one production caller:
`deploy/nightshift-watchdog.sh:61`. The only other reference is
`tests/test_slots.py:108-119`. Nothing in `program/**`, no other `deploy/*.sh`, and no
policy calls it. The staged path in ADR-0002 (add worker-owned renewal → observe it →
delete `renew-active` in a **later** PR, per the one-way-door section,
`ADR-0002-liveness-and-ownership-contract.md:124`) is safe **in the sense that nothing
else breaks in between** — there's no third caller to surprise. It is only safe as a
*fix*, though, if the worker-owned renewal it's staged behind is the corrected,
identity-checked version from §2, not the as-is `slot heartbeat`. Staging the deletion
behind an unfixed primitive would remove the (bad) blanket-renewal safety net before
installing a genuinely better one, net negative for that window.

---

## 4. ADR-0001's "no ladder tier moves" claim

Covered in §1: no trigger has fired, the "T0, nothing moves" claim holds. The gap is
that the instrument that would catch a future move — a ladder pruned to this project's
actual rows, and a capacity model with real numbers in it rather than in a discovery
artifact — isn't in place yet. Recommend closing that before the next architecture
question comes up, not as part of this review's blocking items.

---

## 5. Anything settled here that shouldn't be

- ADR-0002 is correctly scoped as a program-level, cross-seam decision (`Deciders:
  program (cross-seam interface — neither crew may settle it alone)`) and stays inside
  that lane: it decides the record shape, the validation rules, and the renewal/fencing
  contract, and explicitly leaves implementation (watchdog content, worker wiring) to
  the two seams. No `program/**` edit is implied or required by any of the three ADRs —
  correct, given `.omni/decisions.md` row A4 already ruled that tree `platform`-owned and
  ADR-gated.
- ADR-0003 correctly declines to settle `qa`/`release` reassignment (option D), naming it
  product work rather than deciding it here. Correct call — reassigning Codex-native
  seats to Claude would itself be a cross-seam, cross-vendor decision, not a sandbox
  decision, and the ADR doesn't overreach into it.
- I found no ADR here trying to re-decide the seam count, the seam boundary, or the DoD —
  all three stay inside their lane.

---

## Bottom line

**Single most consequential finding:** ADR-0002's "worker-owned renewal" leans on
`nightshift-ctl slot heartbeat` (`bin/nightshift-ctl:305-317`, parser `:907-910`) as if it
already proves what the ADR requires ("proof of the corresponding live run"), but that
command has no caller-identity check at all — any process that knows a slot id can renew
it. The fix is small and already has a precedent in the same file (`consume_slot`,
`bin/nightshift-ctl:146-167`), but if it isn't named as required work, milestone 1 can
ship a renewal path that reads as fixed in review while reproducing a narrower version of
the exact defect this ADR exists to close.

**Can milestone 1 safely start on this contract as written?** Yes, with one addition to
the handoff before build starts, not after: add explicit language to ADR-0002 (or the
`.omni/handoffs/` document the control-plane seam publishes from it) stating that
`slot heartbeat` must verify the caller against the slot's bound holder/agent before this
counts as "worker-owned renewal," and that `tests/test_slots.py:108-132` will change to
assert that check (a strengthening, not a weakening — call it that in the diff). Everything
else in the contract — the `meta`-table heartbeat record, the additive fencing-generation
columns, the staged `renew-active` deletion — is implementable against the schema as it
stands today, following conventions already in `bin/nightshift-ctl`, without touching
`tests/test_claims.py` or the two `tests/test_recovery.py` tests.
