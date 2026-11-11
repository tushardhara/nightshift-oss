# The deterministic control plane

```
LLMs decide what SHOULD happen
        ↓
nightshift-ctl decides what is ALLOWED to happen     (.omni/control.db — SQLite, WAL)
        ↓
agents execute
```

Before this existed, the org's concurrency guarantees — one worker budget, one
recovery per task, one holder per shared path — were prompts. Prompts steer
one model well; they do not survive two models acting at once. Everything that
breaks under two concurrent writers now lives in a small transactional
database mutated only through `bin/nightshift-ctl`; everything narrative (ADRs,
specs, handoffs, reports, registries) stays in files, exactly as before.

## What lives where — the source-of-truth rules

| State | Authoritative home | Projection (readable, never authoritative) |
|---|---|---|
| Configured limits (max workers, reserve, TTLs) | `.omni/capacity.json` (human-editable; read inside every transaction) | — |
| Worker slots in use | `control.db worker_slots` | `nightshift-ctl dump --write` → `.omni/control-state.json` |
| First-implementation admission | `control.db task_admissions` | same |
| Task recovery claims | `control.db task_claims` | same |
| Shared-path leases | `control.db leases` | one-line files in `.omni/leases/` |
| Migration sequence numbers | `control.db sequences` | the filenames in `db/migrations/` |
| Wave checkpoints (base SHA) | `control.db checkpoints` | `base_sha` fields in `.omni/registry-*.json` |
| Task lifecycle, size and prerequisites | `control.db tasks`, `task_dependencies` | `.omni/registry-*.json` (narrative: criteria, verdicts, links) |
| Vendor cooldowns | `control.db vendors` | wave report lines |
| Scheduled-run detector checkpoints and normalized observations | `control.db scheduled_run_detector_state`, `scheduled_run_observations` | `dump.scheduled_run_detector` and direct GitHub incident comments |
| Backlog, criteria, acceptance | GitHub Issues (+ registry narrative) | `.omni/backlog.md` |
| What a review found | `.omni/reviews.md` — append-only, one fenced entry per review (ADR-0090) | not a projection: `control.db review_receipts` / `review_findings` gate merges, exist only when the plane could observe the review, and carry `source` to say how strong the evidence is |

When a file and the database disagree about *who holds what right now*, the
database is right. When anyone wants to know *why*, the files are right —
that is what they are for.

### #400's stores — the full matrix is machine-checked, not hand-maintained here

The table above is `control.db`'s own authority table; it predates, and stays
scoped to, the control plane this document describes. #400 (customer-repo
isolation: publication, provenance, tenancy, tenant keys, authority epochs, and
narrative memory) added twenty more tables across six more SQLite stores plus
one content-addressed filesystem tree, each physically separate from
`control.db` and from each other. Duplicating all of them into hand-written
prose here would drift the moment any one of them changed — which is exactly
what #400 acceptance condition 4 ("every data class names exactly one durable
authority... derived files and views... cannot override it") exists to catch
mechanically instead.

**`bin/nightshift_authority_matrix.py`** is that mechanism: one `DATA_CLASSES`
entry per data class (durable authority, or an explicit projection naming what
it projects onto), verified by `verify_matrix()` against the live schema
constants and `bin/nightshift-ctl`'s own source by regex — never a hand-counted
number. Run `python3 bin/nightshift_authority_matrix.py` for the current report
against this checkout. `docs/decisions/ADR-0087-authority-matrix-and-
observability-signals.md` records the decision to keep it there instead of
here.

**165's repository-scope rows are not yet in this table.** `165/repository-
scope` (ADR-0084) adds a `repository_id` column to `tasks`, `task_dependencies`,
`task_claims`, `worker_slots` and `leases`, plus `meta.repository_id` as a
projection of the authority.db repository binding, and `repo bind`/`repo show`
verbs with their own reason codes. That stream owns the exact rows and
Conventions text to add here; this section is left as the explicit landing spot
rather than guessed at from outside that stream's PR.

## The tables and their guarantees

- **`worker_slots` / `task_admissions`** — one global semaphore plus the
  immutable proof that first implementation was admitted. A normal acquire
  requires exact task, agent, title and purpose and derives PLANNING, NEW, or
  CONTINUATION from the task graph, static roster, PR/admission/claim facts; a
  caller never chooses the phase. PLANNING consumes capacity without changing
  task state. NEW is reserved only for ADR-0032-ready S/M work, then the root
  policy rechecks the same graph and atomically consumes the slot, inserts the
  admission receipt, and changes `pending` to `building`. Continuations require
  that receipt and subtype evidence. `slot acquire` runs in a
  `BEGIN IMMEDIATE` transaction: expire stale rows, count active, compare to
  `capacity.json`, insert or deny. Normal acquires cap at `max − reserve`, so
  the on-call reserve is arithmetically untouchable; `--oncall` acquires may
  use the full ceiling. Two crew leads hitting it simultaneously serialize at
  SQLite's write lock, not at anyone's memory.
- **`task_claims`** — a partial unique index (`task_id WHERE state='active'`)
  makes "at most one active claim per task" a database constraint. The loser
  of a recovery race gets exit 3 plus the winner's `(agent, title)` to stand
  down toward. Claims can be created only for an admitted non-terminal task and
  the original engineer/title; generation and run ID are derived by the
  controller. Every implementer continuation claims first, and one active slot
  may bind that claim.
- **`tasks` / `task_dependencies`** — task size is nullable and otherwise one
  of `S`, `M`, or `L`; prerequisite edges are unique and cannot point to the
  same task. `task dependency-add` validates both task IDs in one transaction,
  and `task show` returns prerequisite IDs in stable order. These are persisted
  facts only. `task show` and `task ready` derive a read-time verdict from one
  full-graph snapshot: only pending S/M tasks outside a dependency cycle with
  every prerequisite exactly `integrated` are ready. The verdict is never
  persisted, does not reserve capacity, and does not authorize dispatch.
- **`leases`** — same uniqueness trick per resource string. Two modes:
  `edit` (releasable at PR-open, TTL backstop) and `serialize` (migrations,
  lockfiles, schema order — releasable only at `pr-merged`/`dropped`, no TTL;
  stale holders are broken explicitly with a recorded reason).
- **`sequences`** — `seq next` allocates atomically and, when given
  `--resource/--holder`, only to the active lease holder. Two crews can never
  both get "the next migration number".
- **`checkpoints`** — `checkpoint create` refuses while `docs/` or
  `.omni/handoffs/` are dirty, then records `git rev-parse HEAD` (or an
  explicit `--sha`). `--ref` is **required** and the recorded sha must pass
  `git merge-base --is-ancestor <sha> <ref>` — a sha that is not reachable
  from the stated ref is refused (exit 2), not recorded (ADR-0007, #80).
  Worktrees are cut FROM that SHA (`git worktree add … <base_sha>`), so every
  worker sees exactly the spec it was contracted against, and recovery after
  a reboot reproduces the same state from persisted rows.
- **`agent_runs`** — which session held which slot for which task; runs whose
  slot TTL-expires are marked `dead`, which is how "the worker crashed" turns
  into recoverable state instead of a mystery.
- **`vendors`** — `vendor limited` computes the cooldown (base 30 min,
  doubling per consecutive limit, 4 h cap); `vendor check --all` returning
  `all_cooling` hands the program the exact timer to sleep on. No agent
  re-derives backoff from memory.
- **`scheduled_run_detector_state` / `scheduled_run_observations`** —
  ADR-0017's positive expected-occurrence checkpoint, normalized failure class,
  consecutive-failure sequence, and delivery acknowledgement. Omnigent's
  `scheduled_task_runs` remains authoritative for firings; these rows record
  what the non-LLM detector observed and whether direct GitHub delivery
  succeeded. `schedule observe` is idempotent per task/run id, and a later
  status for the same run supersedes without incrementing the incident twice.

## Recovery, end to end

At Program startup/resume, `nightshift-ctl run current --new` creates the
controller-owned run fence only when none exists; concurrent/resumed calls return
the same ID. It refuses to invent one over active classified slots or claims.
`--set` remains an explicit operator/test seam, not a workflow input.

1. **Worker crashes mid-task.** Its slot and any claim expire by TTL. The
   next transaction — anyone's — expires them lazily; `nightshift-ctl reap` at each
   heartbeat does it eagerly and reports what it freed. Capacity returns
   without a human, a prompt, or a daemon.
2. **Heartbeats overlap on the same dead task.** Both run
   `claim acquire`; the database grants one. The loser's denial carries the
   winner's claim and the `(agent, title)` to reuse — standing down is the
   designed outcome, not a convention.
3. **A task finishes and leaves its worktree behind.** Rows go terminal; the
   linked worktree does not, and nothing owned it. `reap` now audits every
   worktree against the task table and exits **5** when one that no live task
   owns still carries an index or a working tree (#120). It reports; it never
   removes — see *Worktrees are reaped by report, never by deletion* below.
3. **Box reboots mid-wave.** Nothing is in memory. Slots, claims, leases,
   checkpoints, task states are rows; registries and specs are committed
   files; worker conversations rehydrate through the same `(agent, title)`
   titles. The first heartbeat reconciles `nightshift-ctl dump` against the
   registries and open PRs and carries on — a still-active claim blocks any
   duplicate dispatch by constraint, not by recollection.
4. **Recoverer itself crashes.** Its claim expires by TTL; the next heartbeat
   claims cleanly.

## Enforcement — what stops relying on model memory

Two custom policies (`deploy/policies/omnipods_policies.py`, loaded via the
same mechanism as Omnigent's builtins) close the loop between "the rule
exists" and "the rule binds":

- **`worker_semaphore`** (bundle root): named sends to either crew's counted
  workers require exact `task_id`, `purpose`, `slot_id`, target/title, and
  generation. Opaque sends, session creation, and unknown agents deny;
  Program/shared orchestrators are statically exempt and only `oncall` can use
  the taskless reserve. The gate rechecks phase-specific durable evidence in
  one `BEGIN IMMEDIATE`, consumes the one-dispatch slot, and **fails
  closed**: a missing database or a SQLite error denies the dispatch rather
  than letting the budget evaporate during an infrastructure failure. The
  only escape hatch is explicit and operator-owned —
  `NIGHTSHIFT_SEMAPHORE_FAIL_OPEN=1` in the server environment, for
  deliberately unmanaged local runs.
- **`pr_close_keyword_guard`** (engineers, release): a raw `gh pr create` /
  `gh pr edit` is DENIED when it carries an inline GitHub closing keyword,
  a `--body-file`/`-F` (a file the policy cannot read is not a body it can
  lint), or `--fill*` (body from commit messages). Task PRs open through
  **`nightshift-ctl pr-create -- <gh args>`**, which reads and lints the actual
  body bytes, then execs `gh pr create` with the same arguments. Issues
  close only through the program when `nightshift-ctl issue status` reports every
  non-dropped task `integrated` and pm acceptance is recorded.

`release` still lints every PR body (`gh pr view … | nightshift-ctl pr-lint`)
before merging, as the final layer behind both policies.

**Bookkeeping-only PRs get a fail-closed fast path (issue #286, ADR-0052),
not a weaker one.** `nightshift-ctl bookkeeping plan --task <id> --repo
<path> --head-sha <sha> [--main-sha <sha>]` mechanically classifies the exact
head as `bookkeeping-only` or `normal`; every path outside
`.omni/STATUS.md`, `.omni/ledger.md`, `.omni/tech-debt.md`,
`.omni/reports/**`, and every status other than a pure add/modify of a
`100644` regular file, classifies `normal` — renames, deletions, symlinks,
mode changes, and any diff `bookkeeping plan` cannot fully account for.
`fast_path_eligible` is data on the returned plan, not an exit-code gate:
only a genuinely unproduceable plan (bad task, bad SHA, unreadable git
state) exits 3, mirroring `review plan`'s own `PlanDenied → 3` convention.
Only a `bookkeeping-only` head runs the focused check bundle (allowed-path
validation, `.omni/ledger.md`/`.omni/tech-debt.md` append-only-by-byte-prefix,
`.omni/STATUS.md`/`.omni/reports/**` format checks, and clean mergeability
against the CURRENT tip of `main` via `git merge-tree`) — a `normal` head
gets none of it and keeps every existing gate this document already
describes. `nightshift-ctl bookkeeping verify-head` is the cheap, git-free
re-check immediately before cutover: the head about to be merged must be
byte-identical to the head that was classified, or the fast path refuses.
`nightshift-ctl bookkeeping receipt-record` persists one append-only row per
classify-then-merge attempt in `bookkeeping_receipts` (migration seq 3),
carrying the exact merged SHA once one exists — the same "the database is
the exact-SHA proof" idiom `review_receipts` already uses. This is a
diagnostic primitive, the same relationship ADR-0042's `suggested_domains`
has to every crew's dispatch procedure: it does not itself change what
`release` runs. `release` is not this crew's sub-agent, and wiring its own
procedure to consult `fast_path_eligible` is a separate, downstream,
cross-seam step.

**A heartbeat must prove a durable delta before `pr-create` will run for it
(issue #390, ADR-0053).** `nightshift-ctl heartbeat-persist decide` is a
pure, plane-free function — outcome JSON in, `{should_persist_to_git,
reason_code, route, input_digest}` out — fail-closed by construction: absent
a recognized, non-timestamp-only `durable_delta_reason_code`, or when
`authority_status` is not `"granted"` (a losing Program's stand-down,
ADR-0044 §7b), the answer is `False` and there is no parameter through which
a plain-language assertion can flip it. `nightshift-ctl heartbeat-persist
record` computes the same decision and persists exactly one append-only row
in `heartbeat_persist_decisions` (migration seq 4) per heartbeat outcome —
no-op or durable-delta alike, so "no-op heartbeat → PR count = 0" is a query
against that table, not a narrated claim — returning a `receipt_id`.
`nightshift-ctl pr-create` gains one optional flag,
`--heartbeat-persist-receipt <id>`: absent, `pr-create` is byte-for-byte
unchanged from before this issue, including staying plane-free; present, the
call must consume a matching, unconsumed `should_persist=1` row via a
compare-and-set `UPDATE` before `gh` ever runs, or it is denied — the same
one-shot idiom `program_action_permits` already established for ADR-0044's
dispatch permits. `route="bookkeeping_fast_path"` is informational for the
caller only, telling it to continue into the `bookkeeping plan →
verify-head → receipt-record` chain immediately above — this module
contains no diff/path classification logic of its own.

**A review that finished is written down, whether or not it can be attested
(issue #717, ADR-0090).** Like `capacity.json` above it, this is a row where the
file is authoritative rather than a projection — and unlike it, the database
side is a gate rather than a readable copy, so the two are authoritative for
different questions: the file for what was found, the receipts for what a merge
may rely on. ADR-0039 can only mint a receipt from a
fresh, unedited PR comment carrying a per-dispatch marker — a channel the org
does not always own — so for an entire external-repository run `review_receipts`
stayed empty while two real cross-crew reviews produced two `MERGE_BLOCKER`s, and
`.omni/reviews.md`, which nothing in the codebase wrote to, stayed byte-identical.
An org redesign was drafted on that empty reading.

Two verbs close it. `nightshift-ctl review log` takes a review report on stdin,
appends the entry to `.omni/reviews.md` FIRST, and mints the receipt second with
`review_receipts.source = 'local'`; a missing task row or a bypassed semaphore
changes what the entry records, never whether it is recorded, and the output
names what could not be minted. `nightshift-ctl review receipt-record` is
unchanged in contract but now writes the same entry immediately before its
transaction opens, labelled `'attested'` — so a persisted receipt implies a
logged entry, and the two records cannot drift apart again.
`nightshift-ctl review escaped-defects` is the read side: blocking findings
recorded after a PR already passed, either declared with `found_after` or later
than that PR's first `pass`. It exits 3 on zero parseable entries rather than
printing `{"escaped": 0}` — a count of zero is an answer, an empty read is the
mistake the whole mechanism exists to stop.

**Worktrees are reaped by report, never by deletion.** A task row reaching a
terminal state used to leave its worktree owned by nobody, and `reap` — which
expires claims, leases and slots — knew nothing about worktrees. That gap is
what #89, #98 and #120 all rode in on: a checkout left on a stale tree, whose
branch later catches up to origin, is pre-loaded with a revert of merged work,
and **every check git makes on a commit there passes**. The tree really is a
descendant; the push really is a fast-forward. Only the content is old. #120's
instance survived solely because its local ref happened to sit five commits
behind origin — a property of a ref, not a control.

`nightshift-ctl worktree audit` (and `reap`, which runs it) enumerates every
worktree, correlates each to a task by recorded path and then by branch, and
reports a verdict:

| verdict | meaning | flagged |
| --- | --- | --- |
| `primary` | the shared checkout — never removed, never committed from | no |
| `clean` | nothing staged, nothing modified, nothing untracked | no |
| `owned-dirty` | a live task's worktree, in use | no |
| `orphaned-dirty` | *every* matching task is terminal, and content remains | **yes** |
| `unowned-dirty` | no task claims it at all, and content remains | **yes** |

**One name can match several task rows, and live wins.** Task rows are a
historical log while a branch or a path is a reusable name: on this plane ten
branches span more than one row, and two rows record the same worktree path.
So correlation gathers *every* matching row rather than the first one, and a
worktree is `orphaned-dirty` only when **all** of them are terminal — a single
live owner makes it `owned-dirty` no matter how many finished tasks share the
name, and no matter what order the rows sit in. Taking the first match instead
let database row order decide the verdict, which reported live work as
reapable whenever a terminal row happened to be written first (PR #122,
`architect`). The two errors are not equally expensive, so the tie is broken
toward keeping. States outside the terminal set — including ones added later —
read as live for the same reason. Where the match set is genuinely mixed the
entry carries `ambiguous: true`, the full `tasks` list and a note naming the
terminal rows, so an overloaded branch is visible rather than merely survived.

Both staged *and* unstaged content count. Unstaging an armed index takes the
staged count to zero and leaves the working tree exactly as destructive as it
was; treating that as clean would be the false all-clear the audit exists to
prevent. Ignored files do not count — a detector that fired on `__pycache__`
would be switched off within a day.

The audit is **read-only** and deliberately stops at reporting. It aims at
other agents' live checkouts, so every git call it makes is plumbing that
cannot move a HEAD, write an index, or update a ref. Removing a worktree is
irreversible and stays a human's decision after inspecting what is in it; the
tool's job is that the next instance is caught by a machine, not that a
machine deletes it. Correlation is only as good as what tasks record — a
worktree whose task never set `--worktree` or `--branch` reports as
`unowned-dirty`, which is the honest answer, and the fix is to record it.

**Staged deletions of live paths are refused at commit time.** `nightshift-ctl
guard-commit` fails a commit whose index deletes a path still present at
`origin/program/discovery-state`, naming every such path. `nightshift-ctl hook
install` wires it into git's `pre-commit` path, which is the point: the
decision log's ruling on ADR-0009 is that *"a guard that agents must remember
to invoke cannot fix a discipline failure"* and that the wrapper *"survives as
ergonomics, not as the safety boundary. Nothing may depend on it being
called."* This runs because git runs it. It is quiet on success and names the
paths on refusal.

It is a hook rather than a policy rule for the reason ADR-0009 C3 records: the
policy layer sees a command string with no authoritative cwd, so a staging
command run in the shared checkout is indistinguishable from the same command
run in a dedicated worktree. A pre-commit hook has no such problem — it runs
*in* the repository whose index it is judging, and reads that index rather
than the text of the command that filled it. That also closes the bypass
`architect` named against ADR-0009: it fires on the staged **result**, so a
stage-everything flag, a checkout from an old tree, or a script all reach it
identically. It fails **closed** — a reference point it cannot resolve is a
refusal with its own distinct reason, because ADR-0009's freshness check
failed the opposite way (#101: it could not fetch, compared two stale values,
and passed).

**Slot TTLs and long turns.** A slot's TTL (default 4 h) exists for crash
recovery, not as a work deadline. **A slot is renewed by its holder**, with
`nightshift-ctl slot heartbeat --slot <n> --holder <crew>/<agent>`. `--holder`
is required and is verified against the slot's recorded holder by the same
predicate that guards dispatch (`holder_matches`, shared with `slot consume`);
a non-holder gets exit **3** and renews nothing. ADR-0002 §4a.

*Superseded, and why it mattered:* the watchdog used to renew every in-flight
slot at once (`slot renew-active`) whenever `.omni/` looked fresh. Two things
were wrong with that. The freshness signal was any write under `.omni/` —
including the 30-minute backup's own output — so it never went stale; and one
org-wide signal renewing every worker's slot is, in Codex's words, *"a
per-worker lease converted into an organization-wide lease"*. Program liveness
is not evidence that any particular worker is progressing, and it must never
again be spent as if it were.

`slot renew-active` still exists and the watchdog still calls it. Its deletion
is deliberately staged to a **later** PR (ADR-0002, *One-way door*): add
worker-owned renewal, observe it working, then remove the blanket path — never
both in one change. `--max-age` (default 24 h) still caps any slot's total
life, so a leaked slot returns to the budget within a day regardless.

**Liveness is attributed and read by content.** `nightshift-ctl beat write
--who <owner> --run <id> --seq <n>` records a completed loop iteration;
`nightshift-ctl beat show --who <owner>` answers *is this owner alive* with
exit 0 (alive) or exit 3 (not alive, with a `reason_code`). Four signals,
four owners, and no signal may stand in for another (ADR-0002 §1). The full
contract, including the record schema and a runnable example, is published for
the host seam as `.omni/handoffs/LIVENESS-RECORD-v1.md`.

## Deployed vs committed — is the constitution actually running?

`program/**` is the org's own constitution: `worker_semaphore`, `spawn_bounds`,
`blast_radius`, `stay_in_workspace`, the sandbox `write_paths`. Of those, only
what the **root** spec declares is ever loaded by the policy engine — every
per-seat block is dead config (#118, proven by live probe;
`docs/audit/118-per-seat-policy-probe.md`, ADR-0012). Those specs
live at `program/agents/*/config.yaml`, and there is a checkout of them on this
box that is treated as the deployed one. Nothing guarantees that checkout is at
the commit the org merged — and on this box it has not been. A `program/**`
change could pass review, pass CI, merge, and change nothing about the specs
anyone deploys from, with no error and no warning (#107; the read half of the
shared-checkout hazard whose write half is #89/#98).

**What `spec status` compares, stated exactly.** It compares the **deployed
checkout** against the **committed ref**, and **neither is necessarily what the
running server serves**. The server does not read specs from any working
directory: a spec directory becomes a content-addressed bundle at an explicit
registration event (`--agent PATH`, startup-only by construction), and sessions
load it through a two-tier cache keyed on `agent_id` alone whose extracted disk
tier persists across restarts — so a served spec can correspond to no commit on
this box at all (ADR-0010 Context, B1; **#115** is exactly that, live, today).
The comparison is still the right one to run and its findings stand — a
deployed checkout that is hundreds of commits behind the ref the org believes
is deployed (143 when this was filed, and still growing) is a real defect —
but it is evidence about the deployment, not a reading of the running server. Proving a spec is *served* needs the registered bundle, which
is ADR-0010's reversal trigger 4 and the next wave's work. The tool's own
`reason` strings say so on every verdict.

```
$ nightshift-ctl spec status
{"deployed": {"root": "/srv/project", "sha": "27f3915…", "detached": true,
              "source": "proc:3525266", "dirty": []},
 "expected": {"ref": "origin/program/discovery-state", "sha": "ee90712…"},
 "diverged": true, "status": "diverged",
 "divergence": {"kind": "not_on_ref", "behind": 143, "ahead": 1},
 "reason": "MERGED IS NOT DEPLOYED: …"}
$ echo $?
4
```

- **`spec status` signals; `dump` reports.** The same object appears under
  `dump`'s `spec` key, because `dump` is where a driver already looks to ask
  what state the org is in. `dump`'s exit code stays **0** regardless: it is a
  projection that `deploy/nightshift-state-backup.sh` redirects into
  `.omni/control-backup.json` every 30 minutes, and a state snapshot must not
  start failing over an unrelated deployment fact. Gate scripts on `spec
  status`; read `dump` for context.
- **The deployed side comes from `/proc/<pid>/cwd`, not from `ps`.** The
  server is started by a `tmux … bash -lc 'cd /srv/project; exec omnigent
  server …'` wrapper whose own argv never names the directory, so the kernel's
  view of the process's working directory is the only place the answer
  actually exists. A tool that shells out to `ps` and regexes it has moved the
  parsing, not removed it.
- **`diverged` is fail-closed.** It is `false` only when alignment is
  *proven*: both SHAs resolved, equal, no uncommitted edits under
  `program/agents`, **and the ref they were compared against of established
  currency**. No server found, an unresolvable ref, two servers in
  different directories — all report `diverged: true` with
  `status: "undetermined"` and exit 2. #107 exists because an absent signal
  was read as a good one five times in one day.
- **The expected side reports its own freshness, and the detector never
  fetches.** `expected` is resolved with `rev-parse` in *the caller's own
  clone*, so `origin/program/discovery-state` is a cached assertion about the
  remote, not an observation of it. The report says so:
  `expected.fetched` (`true` / `false` / `null`), `expected.age_seconds`,
  `expected.source` and `expected.stale`, against
  `expected.max_age_seconds` (default 3600, `--max-ref-age`). When the ref is
  remote-tracking and its currency cannot be established — or exceeds the
  bound — a SHA match is downgraded from `aligned` to
  `status: "undetermined"` and exit 2 rather than being reported as proof.
  `fetched: null` means *not applicable*: a local branch, tag or raw SHA has
  no fetch behind it to have gone stale, and is not downgraded.

  This is deliberately a field and not a `--fetch` flag. A read-only detector
  that reaches the network stops being runnable in the places this one has to
  run, and it would no longer be safe to aim at the shared checkout. A stale
  ref also never *rescues* a divergence: `diverged` still wins outright,
  because staleness can only make a real gap look smaller, never invent one.

  The timing is the point. Today the deployed commit is on no ancestor path
  in either direction, so no staleness can produce a false green. Once specs
  actually deploy, "deployed == a recent tip" becomes the normal state, and a
  clone that last fetched before the newest deploy will resolve
  `origin/<branch>` to *exactly the deployed SHA* — aligned, exit 0, wrong.
  The detector would be least trustworthy precisely when the org began
  relying on it.
- **It only ever observes.** Every git call is read-only plumbing
  (`rev-parse`, `cat-file -e`, `symbolic-ref`, `merge-base --is-ancestor`,
  `rev-list`, `status --porcelain`). Pointing a detector at the shared
  checkout is only safe because it cannot move it; moving that checkout under
  the agents working in it is #89/#98, which has silently reverted merged work
  twice. `tests/test_spec_deployment.py` asserts HEAD, refs, reflog and
  worktree are byte-identical across a run.
- **Nothing refuses to start on divergence yet.** Whether the server should,
  and how `program/**` should reach the runtime at all, is
  `docs/decisions/ADR-0010-program-spec-deployment.md`. Until that is decided
  and deployed, #107 ask 4 binds every driver: treat any `program/**` PR as
  **merged but NOT deployed**, and never record an agent-config change as
  verified without executing against the agent afterwards.

## Conventions

- Exit code **0** = granted/ok, **3** = denied/held/cooling (queue or stand
  down — never retry in a loop), **2** = usage error *or "could not
  determine"*, **1** = internal error, **4** = **deployed/committed
  divergence** (`spec status` only, #107).
  Output is always one JSON object.
- **4 is not a flavour of 3.** 3 tells a caller to stand down and try again
  later; a divergence never clears by waiting, only by a deploy. A caller
  that retried a 4 on a loop would spin forever against a condition no
  amount of patience fixes, which is why it got its own code rather than a
  `reason_code` under 3.
- **5 is orphaned worktrees**, from `reap` and `worktree audit`, and it is
  its own code by the same argument: a worktree nobody owns never clears by
  waiting either — it clears when a human inspects it and removes it. It is
  not a 4, which says the deployed specs disagree with the ref and is fixed
  by a deploy, not a removal.
- Every mutating command expires stale state inside its own transaction —
  the control plane self-heals on contact, `reap` just makes it visible.
- `nightshift-ctl` is stdlib-only Python 3 (SQLite ships in the interpreter):
  no build step, no dependencies, runs anywhere the box already runs.
  (Chosen over a compiled Go binary deliberately: the deploy target must
  work from a fresh clone with nothing but python3 present.)

## What deliberately did NOT move into the database

ADRs, specs, PRDs, handoffs, working agreements, incident notes, reports,
review logs, the ledger. They are the explainable history and the interface
between agents; a database row cannot be read in two years the way a
markdown file can. The registry JSONs also stay — the control plane holds
their concurrency-critical fields (state, base_sha, issue), the registry
holds the narrative around them.
