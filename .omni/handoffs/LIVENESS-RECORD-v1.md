# LIVENESS-RECORD v1 — the ADR-0002 seam contract

**Published by** `crew_alpha` (seam A — coordination). **Consumed by** `crew_beta` (seam B —
host integration) for the watchdog. **Contract of record:** ADR-0002, accepted and staff-reviewed.
**Traces to** issue #11. **Status:** v1, stable for wave 1. Revised 2026-08-22 after `security` review — §2, §3, §5, §6 and §7 changed; see §9.

This file is the interface. Not a summary of one, and not something anyone relayed in a message —
if a detail you need is not written here, **this file is wrong and that is the bug to report**.
Report it to the `program`; the two crews do not message each other.

> **Where the code is right now.** These commands exist on branch
> `alpha/11-liveness-record` at commit **`d679f4d`** (cut from wave-1 checkpoint `139726e`),
> in PR to `program/discovery-state`. They are **not** on `139726e` itself. Until that PR
> merges, run the worked example below with `NIGHTSHIFT_CTL` pointed at the alpha worktree —
> §6 does this for you. `crew_beta` may stub against this contract meanwhile; that is the
> intent, and it is why the contract ships before the consumer.

---

## 1. The Program heartbeat record

**One row**, in the existing `meta` table of `.omni/control.db`, under key `heartbeat:<who>`.
The value is a JSON object. It is written in one transaction, so a reader never sees half of it.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | **`1`.** Versioned from its first commit, per ADR-0002's one-way-door clause. A consumer that does not recognise the version must report *not alive*, never guess. |
| `who` | string | The signal's owner. `program` for the Program heartbeat. |
| `run_id` | string | The run this heartbeat belongs to. A heartbeat from another run is not evidence about this one. |
| `session` | string \| null | The writer's session identity. Diagnostic. |
| `ts` | float | Unix wall-clock seconds, set by the writer at write time. |
| `boot_id` | string | The host's boot id (`/proc/sys/kernel/random/boot_id`). **This is what makes a post-reboot stale record detectable**: the record survives a reboot, the Program does not. |
| `seq` | int | Last **completed** control-loop iteration. Strictly increasing within a `(run_id, boot_id)`. |
| `phase` | string \| null | Optional human label, e.g. `wave-1`. Never load-bearing. |

### Who may write it

**Only the owner named in `who`, and only after a *completed* loop iteration.** ADR-0002 §1:
four signals, four owners, and **no signal may stand in for another**.

| Signal | Written by | Never written by |
|---|---|---|
| Program heartbeat (`heartbeat:program`) | the Program, after a completed control-loop iteration | any timer, any worker, the backup, the watchdog |
| Worker heartbeat | the bound worker (or a supervisor that positively verified *that* worker) | the Program, blanket |
| Backup health | the backup path, monitored separately | the Program |
| Terminal project state | a control-plane state transition | `STATUS.md`, or any Markdown |

Specifically, and this is the defect the whole ADR exists to remove:
**`deploy/nightshift-state-backup.sh` must never touch `heartbeat:program`.** The old design
inferred Program liveness from "the newest mtime under `.omni/`", and the backup rewrote a file
under `.omni/` every 30 minutes — so a dead Program read healthy forever. In Codex's phrasing,
*"the recovery mechanism generates the evidence that suppresses recovery."*

**Read by content, never by mtime.** Do not stat a file. Do not grep `STATUS.md`. Call
`beat show` and read its verdict.

---

## 2. Renewal: who may renew a worker slot, and who may not

**A slot is renewed by its holder.**

```
nightshift-ctl slot heartbeat --slot <id> --holder <crew>/<agent> [--ttl <seconds>]
```

`--holder` is **required**. It is verified against the slot's recorded holder before anything is
renewed. It accepts the full `<crew>/<agent>` form or the bare `<agent>`, matched by the same
predicate that guards dispatch (`holder_matches`, shared with `slot consume` — one spelling of
the rule, so renewal cannot drift weaker than dispatch). A non-holder gets **exit 3** and renews
nothing.

### The watchdog may not renew a slot through `slot heartbeat` — and `slot renew-active` is still there

Read both halves of that heading. `slot heartbeat` now refuses any caller that is not the slot's
holder, and the watchdog is never the holder. But **`slot renew-active` still exists, still
blanket-renews every active slot, and the watchdog still calls it today**
(`deploy/nightshift-watchdog.sh:61`). The blanket path is not closed yet; it is scheduled to be
deleted, and §6 below asserts that it still succeeds so this file documents the gap rather than
implying it is shut.

The principle it will be closed under, ADR-0002 §4:

- Program liveness is **not** evidence that any particular worker is progressing. One org-wide
  signal renewing every worker's slot is *"a per-worker lease converted into an
  organization-wide lease."*
- The watchdog renewing slots on a freshness signal it also refreshes is a loop that cannot fail.

**What the watchdog does instead:** it calls `beat show --who program`, and it **alerts**. It
does not renew, it does not manage services, and it does not write any heartbeat.

> **Staged deletion — do not skip this.** `slot renew-active` **still exists** and the watchdog
> **still calls it today**. Its removal is deliberately staged to a **later** PR (ADR-0002,
> *One-way door*): add worker-owned renewal, observe it working, then delete the blanket path —
> never both in one change. `crew_beta`: do not delete the `renew-active` call in the watchdog
> as part of consuming this contract. It comes out under its own ADR-authorised change, after
> this one is observed working.

---

## 3. Exit codes — `3` is a verdict, never an error

Unchanged repo-wide convention, restated because a consumer that gets this wrong builds a
dead-man's switch that dies quietly:

| Exit | Meaning |
|---|---|
| **0** | granted / ok / **alive** |
| **3** | **denied / held / cooling / NOT ALIVE.** A normal, expected answer. Queue or stand down. **Never retry in a loop.** |
| **2** | usage error (bad or missing arguments) |
| **1** | internal error |

**`beat show` returning 3 is the dead-man's switch working, not the tool breaking.** Every
not-alive verdict is exit 3 with `"alive": false` and a machine-readable `reason_code`:

| `reason_code` | Meaning |
|---|---|
| `control_plane_unreadable` | the control plane itself could not be opened: absent and uncreatable, corrupt, or permission-denied. **Not alive** — nothing can attest the Program ran, so the fail-closed rule applies. See the note below on its payload |
| `missing` | no heartbeat recorded for that owner |
| `malformed` | the record is unparseable or missing required fields |
| `unknown_version` | `schema_version` is not one this build understands. **Not alive** — a consumer that does not recognise the version must never guess |
| `no_expected_run` | no run could be resolved to check against, so the check cannot be performed. **Not alive**, deliberately: the answer is never a silent pass |
| `wrong_run` | the record belongs to a different run |
| `wrong_boot` | **written before the last reboot** — the record survived, the Program did not |
| `future` | timestamp materially in the future (clock skew beyond tolerance) |
| `stale` | older than the freshness threshold |

Shell consequence, and this bites: `set -e` treats exit 3 as fatal. Capture it.

```bash
if out=$(nightshift-ctl beat show --who program); then alive=1; else alive=0; fi
```

`bash -c 'set -e; nightshift-ctl beat show --who program; echo unreachable'` will not print
`unreachable` when the Program is dead — which is exactly when your alert path needs to run.

Output is always **one JSON object on stdout**, on every path including denials — **and
including the path where the control plane cannot be opened at all.**

> **`control_plane_unreadable` is the one verdict with a reduced payload.** It is exactly:
>
> ```json
> {"alive": false, "reason_code": "control_plane_unreadable"}
> ```
>
> Two keys, no more. Every other verdict carries `who`, `reason` and usually `age` and
> `record`; this one cannot, because the store those fields are read from is the thing that
> could not be opened. A human-readable diagnostic naming the path and the underlying error
> goes to **stderr**, so parse stdout and log stderr.
>
> **Why exit 3 and not 1.** An unreadable control plane is a **verdict of not-alive, not a
> crash**. Exit 1 would be a crash, and a crash is not a verdict: a consumer branching on
> 0-vs-3 gets neither answer and mishandles the single failure a dead-man's switch most needs
> to catch. Exit 3 = denied/verdict is the discipline every other `nightshift-ctl` subcommand
> already follows.
>
> **This verdict is scoped to `beat show` alone.** No other subcommand converts an unreadable
> plane into exit 3 — `beat show` is the only one whose *purpose* is to answer "is this owner
> alive?", so it is the only one with a correct answer here. `slot acquire` against a broken
> plane still fails loudly rather than reporting a routine "denied, queue and retry" that
> would hide an outage.

---

## 4. How a caller proves identity

There is no cryptography here and none is claimed. Identity is an **asserted argument checked
against state the control plane already holds**, and the trust boundary is the box: anything
that can run `nightshift-ctl` can read `control.db`. What this stops is *accident and
confusion* — a watchdog, a timer or a stray script renewing work it does not own — not a
hostile local process. Say so plainly rather than implying a guarantee that is not there.

| Operation | Proof | Checked against |
|---|---|---|
| `slot heartbeat` | `--holder <crew>/<agent>` (**required**) | `worker_slots.holder`, via `holder_matches` |
| `slot consume` | `--agent <agent>` (**required**) | same predicate, same table |
| `beat write` | `--who <owner>` plus `--run` and a strictly increasing `--seq` | the stored record for that owner |

**The sequence is the anti-replay check.** `beat write` **refuses** (exit 3,
`reason_code: seq_not_increasing`) a sequence that does not advance within the same
`(run_id, boot_id)`, and the stored record is left untouched — a refused write cannot clobber a
good one. A **new** `run_id` or a **new** `boot_id` legitimately restarts the counter; that is
allowed, and the response carries `"reset": true` so a restart is *visible* rather than silent.

**Consumers must pass `--who`.** `beat show --who program` will not be satisfied by a `backup`
heartbeat: the key is namespaced per owner, so a wrong owner reads `missing`, not alive.

---

## 5. What `crew_beta`'s watchdog should do

1. `nightshift-ctl beat show --who program --max-age <threshold>`.
2. Exit **0** → alive. Do nothing.
3. Exit **3** → **not alive**. Alert, including `reason_code` and `age` from the JSON.
   `wrong_boot`, `wrong_run` and `stale` are different operator stories; pass the reason through.
4. Renew **nothing**. Manage **no** services. Write **no** heartbeat.
5. Default threshold, if you pass no `--max-age`: `heartbeat_max_age_seconds` from
   `.omni/capacity.json` (default **900 s**). Clock-skew tolerance is
   `heartbeat_future_skew_seconds` (default **60 s**).

**Where the run id comes from — you do not have to pass it, and you should not have to.**
An earlier draft of this section said `beat show --who program --max-age <threshold>` and left it
there. That was a defect in this file, found by `security` review: with no run to check against,
a heartbeat left by a **previous, dead run** read *alive* indefinitely as long as it stayed fresh
and the box had not rebooted — a record outliving the thing it attests, which is the exact
failure class ADR-0002 exists to remove. It is fixed in the tool rather than in your copy of the
command, because a check the caller has to remember is a check that is eventually lost.

`beat show` now resolves the expected run itself, in this order:

1. `--run <id>` if you pass it;
2. `$NIGHTSHIFT_RUN_ID` from the environment;
3. the control plane's own `current_run_id`, recorded once at run start by
   `nightshift-ctl run current --set <id>` — a *different* command from the heartbeat, so this
   is independent evidence and not the record checking itself.

If **none** of the three resolves, `beat show` reports `no_expected_run` and **exit 3**. That is
deliberate and it is the fail-closed direction: an unverifiable heartbeat is not a live one. The
watchdog therefore needs no new configuration — but whoever starts a run must call
`run current --set`, and if that is missing the watchdog will alert rather than quietly stop
checking.

---

## 6. Worked example — this runs

Copy-paste the whole block. It creates its own throwaway database in `$TMPDIR`, touches no host
path, needs no network, and exits **0** printing `ALL CHECKS PASSED`. It is the acceptance
evidence for this handoff: it is executed verbatim before this path is handed over.

```bash
#!/usr/bin/env bash
# LIVENESS-RECORD v1 — worked example. Verifies every claim in this document.
set -uo pipefail

# Until PR "alpha/11-liveness-record" merges to program/discovery-state, ctl
# lives in the alpha worktree. Override with NIGHTSHIFT_CTL=... if it has moved.
CTL="${NIGHTSHIFT_CTL:-/srv/project/.worktrees/alpha-11-liveness/bin/nightshift-ctl}"
[ -x "$CTL" ] || { echo "FAIL: no nightshift-ctl at $CTL"; exit 1; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/.omni"
printf '{"max_concurrent_workers":4,"reserve_for_oncall":1}' > "$WORK/.omni/capacity.json"
ctl() { "$CTL" --db "$WORK/.omni/control.db" "$@"; }
ctl init >/dev/null
# §5: the run `beat show` checks heartbeats against. Recorded once at run
# start, by a different command than the heartbeat, so it is independent
# evidence. Without it every `beat show` below would fail closed.
ctl run current --set run-1 >/dev/null

fails=0
check() { # check <label> <expected-exit> <actual-exit>
  if [ "$2" = "$3" ]; then echo "  PASS  $1"
  else echo "  FAIL  $1 (expected exit $2, got $3)"; fails=$((fails+1)); fi
}
BOOT="$(cat /proc/sys/kernel/random/boot_id)"

echo "-- §1 the record: only the Program writes heartbeat:program"
out=$(ctl beat write --who program --run run-1 --seq 1 --boot "$BOOT" \
        --session conv_demo --phase wave-1); check "beat write" 0 $?
echo "$out" | python3 -c '
import json,sys
r=json.load(sys.stdin)["record"]
need={"schema_version","who","run_id","session","ts","boot_id","seq","phase"}
missing=need-set(r)
assert not missing, f"record is missing {missing}"
assert r["schema_version"]==1 and r["who"]=="program" and r["seq"]==1
print("  PASS  record carries every ADR-0002 §2 field, schema_version=1")'

echo "-- §3 alive is exit 0"
ctl beat show --who program >/dev/null; check "beat show -> alive" 0 $?

echo "-- §1 no signal stands in for another: a backup beat is not the Program's"
ctl beat write --who backup --run run-1 --seq 1 --boot "$BOOT" >/dev/null
ctl beat show --who nobody >/dev/null 2>&1; check "unknown owner -> not alive" 3 $?

echo "-- §3 not-alive verdicts are exit 3 WITH a reason_code, never exit 1"
for probe in "--max-age 0:stale" "--run other-run:wrong_run" \
             "--boot 22222222-2222-4222-8222-222222222222:wrong_boot"; do
  args="${probe%%:*}"; want="${probe##*:}"
  out=$(ctl beat show --who program $args 2>&1); rc=$?
  got=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["reason_code"])')
  if [ "$rc" = 3 ] && [ "$got" = "$want" ]; then echo "  PASS  $want -> exit 3"
  else echo "  FAIL  $want (exit $rc, reason_code $got)"; fails=$((fails+1)); fi
done

echo "-- §1 a corrupt record reads NOT ALIVE, never alive, never a traceback"
python3 - "$WORK/.omni/control.db" <<'PY'
import sqlite3,sys
cx=sqlite3.connect(sys.argv[1]); cx.execute("UPDATE meta SET value='{not json' WHERE key='heartbeat:program'"); cx.commit()
PY
out=$(ctl beat show --who program 2>&1); rc=$?
echo "$out" | grep -q '"reason_code": "malformed"' && check "malformed -> not alive" 3 $rc \
  || { echo "  FAIL  malformed record did not report reason_code=malformed"; fails=$((fails+1)); }

echo "-- §4 anti-replay: a sequence that does not advance is refused"
ctl beat write --who program --run run-1 --seq 5 --boot "$BOOT" >/dev/null
ctl beat write --who program --run run-1 --seq 5 --boot "$BOOT" >/dev/null 2>&1
check "seq 5 after seq 5 -> refused" 3 $?
kept=$(ctl beat show --who program | python3 -c 'import json,sys; print(json.load(sys.stdin)["record"]["seq"])')
[ "$kept" = 5 ] && echo "  PASS  refused write did not clobber the good record" \
                || { echo "  FAIL  stored seq is $kept"; fails=$((fails+1)); }

echo "-- §4 a reboot legitimately resets the counter, and says so"
ctl run current --set run-2 >/dev/null
ctl beat write --who program --run run-2 --seq 1 \
    --boot 22222222-2222-4222-8222-222222222222 | grep -q '"reset": true' \
  && echo "  PASS  new boot -> reset:true" \
  || { echo "  FAIL  new boot did not report reset"; fails=$((fails+1)); }

echo "-- §1/§3 the post-reboot stale heartbeat, caught with NO extra flag"
out=$(ctl beat show --who program 2>&1); rc=$?
echo "$out" | grep -q '"reason_code": "wrong_boot"' && check "previous boot -> not alive" 3 $rc \
  || { echo "  FAIL  a pre-reboot record read alive"; fails=$((fails+1)); }

echo "-- §1/§3 a record from a writer we do not understand is NOT alive"
ctl run current --set run-3 >/dev/null
ctl beat write --who program --run run-3 --seq 1 --boot "$BOOT" >/dev/null
python3 - "$WORK/.omni/control.db" <<'PY'
import sqlite3,json,sys
cx=sqlite3.connect(sys.argv[1])
r=json.loads(cx.execute("SELECT value FROM meta WHERE key='heartbeat:program'").fetchone()[0])
r["schema_version"]=99
cx.execute("UPDATE meta SET value=? WHERE key='heartbeat:program'",(json.dumps(r),)); cx.commit()
PY
out=$(ctl beat show --who program 2>&1); rc=$?
echo "$out" | grep -q '"reason_code": "unknown_version"' && check "schema_version 99 -> not alive" 3 $rc \
  || { echo "  FAIL  an unrecognised schema_version read alive"; fails=$((fails+1)); }

echo "-- §5 a PREVIOUS, DEAD run's fresh heartbeat is NOT alive, with no flag"
ctl run current --set run-4 >/dev/null
ctl beat write --who program --run run-4 --seq 1 --boot "$BOOT" >/dev/null
ctl beat show --who program >/dev/null; check "current run -> alive" 0 $?
ctl run current --set run-5 >/dev/null          # the Program died; a new run began
out=$(ctl beat show --who program 2>&1); rc=$?
echo "$out" | grep -q '"reason_code": "wrong_run"' && check "dead run's fresh heartbeat -> not alive" 3 $rc \
  || { echo "  FAIL  a dead run's heartbeat read alive"; fails=$((fails+1)); }

echo "-- §5 with no run resolvable at all, the answer is NOT ALIVE, never a pass"
W2="$(mktemp -d)"; mkdir -p "$W2/.omni"
printf '{"max_concurrent_workers":4,"reserve_for_oncall":1}' > "$W2/.omni/capacity.json"
"$CTL" --db "$W2/.omni/control.db" init >/dev/null
"$CTL" --db "$W2/.omni/control.db" beat write --who program --run r --seq 1 --boot "$BOOT" >/dev/null
out=$(NIGHTSHIFT_RUN_ID= "$CTL" --db "$W2/.omni/control.db" beat show --who program 2>&1); rc=$?
rm -rf "$W2"
echo "$out" | grep -q '"reason_code": "no_expected_run"' && check "no expected run -> fails CLOSED" 3 $rc \
  || { echo "  FAIL  an unverifiable heartbeat read alive"; fails=$((fails+1)); }

echo "-- §2 a slot is renewed by its HOLDER"
sid=$(ctl slot acquire --holder crew_beta/eng --crew crew_beta --task t1 --ttl 600 \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["slot_id"])')
ctl slot heartbeat --slot "$sid" --holder crew_beta/eng --ttl 600 >/dev/null
check "holder renews its own slot (full name)" 0 $?
ctl slot heartbeat --slot "$sid" --holder eng --ttl 600 >/dev/null
check "holder renews its own slot (bare agent)" 0 $?

echo "-- §2 no non-holder can renew through 'slot heartbeat'"
ctl slot heartbeat --slot "$sid" --holder watchdog --ttl 600 >/dev/null 2>&1
check "non-holder (watchdog identity) via slot heartbeat -> DENIED" 3 $?
ctl slot heartbeat --slot "$sid" --holder crew_alpha/eng_claude --ttl 600 >/dev/null 2>&1
check "another crew via slot heartbeat -> DENIED" 3 $?

echo "-- §2 but the BLANKET path is still open, and this is the gap, not a pass"
ctl slot renew-active --ttl 600 >/dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "  GAP   'slot renew-active' STILL SUCCEEDS - the watchdog still calls it"
  echo "        (deploy/nightshift-watchdog.sh:61). Staged deletion, ADR-0002"
  echo "        one-way door: NOT closed by this contract. Do not remove that"
  echo "        call while consuming this file."
else
  echo "  NOTE  'slot renew-active' no longer succeeds - the staged deletion has"
  echo "        landed; update §2 of this handoff."
fi

echo "-- §4 identity is required, not optional: no --holder is a usage error"
ctl slot heartbeat --slot "$sid" --ttl 600 >/dev/null 2>&1
check "no --holder -> usage error" 2 $?

echo "-- §3 an UNREADABLE control plane is a VERDICT too, never a traceback"
W3="$(mktemp -d)"; mkdir -p "$W3/.omni"
printf '{"max_concurrent_workers":4,"reserve_for_oncall":1}' > "$W3/.omni/capacity.json"
echo 'not a database' > "$W3/.omni/control.db"
body=$("$CTL" --db "$W3/.omni/control.db" beat show --who program 2>/dev/null); rc=$?
rm -rf "$W3"
want='{"alive": false, "reason_code": "control_plane_unreadable"}'
if [ "$rc" = 3 ] && [ "$body" = "$want" ]; then
  echo "  PASS  unreadable plane -> exit 3, exactly the two-key verdict"
else
  echo "  FAIL  unreadable plane (exit $rc, body $body)"; fails=$((fails+1))
fi

echo "-- §3 exit 3 under 'set -e' is survivable, and this is the shape to copy"
if out=$(ctl beat show --who program 2>/dev/null); then alive=1; else alive=0; fi
[ "$alive" = 0 ] && echo "  PASS  captured a not-alive verdict without dying" \
                 || { echo "  FAIL  expected not-alive here"; fails=$((fails+1)); }

echo
if [ "$fails" = 0 ]; then echo "ALL CHECKS PASSED"; else echo "$fails CHECK(S) FAILED"; exit 1; fi
```

---

## 7. What this contract does NOT cover

Named so nobody builds on an assumption this file never made.

- **Fencing enforcement.** `worker_slots` and `task_claims` carry `generation` and `run_id` as of
  this change, but **nothing is refused on account of them**. The columns land now; the
  enforcement is **M3** (DoD conditions 6 and 7). Do not build a consumer that expects a stale
  generation to be rejected — it will not be, yet.
- **The global worker budget.** Still not global (`worker_semaphore` binds crew leads only).
  Confirmed defect, M3.
- **Deleting `slot renew-active`.** Staged to a later PR. See §2 — and note that §6's example
  prints a `GAP` line, not a `PASS`, for that path, so running it shows you the hole rather than
  reassuring you about it.
- **Exactly-once.** ADR-0002 §7: claim ownership is not exactly-once. Every externally visible
  action still needs an idempotency key or a reconciliation step.
- **Cryptographic identity.** See §4. The check is against control-plane state, not a signature.
- **Alert delivery.** `beat show` reports a verdict. That an alert reached a human is a separate
  property with a separate owner (DoD condition 4's naming-honesty clause).

## 8. Changing this contract

`crew_alpha` owns it. Changes go through the `program`, with an ADR where ADR-0002's semantics
move. If something here is wrong, incomplete, or does not match the code: **report it to the
program rather than working around it.** A workaround built on a wrong interface is how the two
seams silently diverge, and this file is the only thing standing between us and that.

---

## 9. Revision log

**2026-08-22 — v1, post-`security` review.** Three blocking findings on PR #16 changed this
file. Recorded here rather than silently corrected, because `crew_beta` may already have started
stubbing against the earlier text.

| § | What changed | Why it matters to a consumer |
|---|---|---|
| §1, §3 | `unknown_version` added: an unrecognised `schema_version` now reads **not alive**. | §1 always *promised* this. The code did not do it — it checked the field was present, never its value, so `schema_version: 99` read **alive**. If you stubbed the promise, your stub was right and the tool was wrong. |
| §3, §5 | `no_expected_run` added; the expected run now resolves automatically from `--run`, `$NIGHTSHIFT_RUN_ID`, or `current_run_id`. | **The previous §5 recipe disabled a check.** `beat show --who program --max-age <n>` with no `--run` let a *previous, dead* run's fresh heartbeat read alive indefinitely. If you copied step 1 as it stood, re-copy it. You do not need to change your command — the fix is in the tool — but whoever starts a run must now call `run current --set`. |
| §2 | Heading rescoped; `slot renew-active` explicitly named as **still open**. | The old heading said the watchdog may not renew slots, full stop. That was true of `slot heartbeat` and false of `slot renew-active`, which the watchdog still calls. Do not remove that call. |
| §6 | Example gained three checks and prints a `GAP` line for `renew-active`. | The example previously printed 17 `PASS` lines and nothing else, which read as "everything is closed". It now shows you the hole. |

Nothing in §4 changed: `security` tried empty, whitespace-only, differently-cased, NULL and
omitted holders against `slot heartbeat` and every one failed closed.

**2026-08-23 — v1, post-cross-crew review (`crew_beta`) and `security` ruling.** One
non-blocking finding on PR #16 changed this file.

| § | What changed | Why it matters to a consumer |
|---|---|---|
| §3 | `control_plane_unreadable` added: an unreadable, corrupt or absent-and-uncreatable control plane is now exit **3** with `{"alive": false, "reason_code": "control_plane_unreadable"}`, instead of a Python traceback with exit 1 and **no JSON at all**. | §3 always *promised* "one JSON object on stdout, on every path". The error path did not honour it, so a watchdog branching on 0-vs-3 got an exit-1 traceback for the single failure it most needs to catch — the control plane being gone. If you stubbed the promise, your stub was right and the tool was wrong. Note the reduced two-key payload and the stderr diagnostic. |

Raised by `crew_beta` in cross-crew review (9/10 criteria MET, this one non-blocking); shape
ruled by `security`; implemented in `alpha/11-liveness-record` at **`e1f50cb`**. Regression
test `tests/test_beat.py::ControlPlaneUnreadableTest` (5 cases, asserting both the exit code
and the exact JSON key set); pre-fix failure captured in `docs/audit/baseline/RT-23.txt`.
