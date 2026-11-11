# Reconciliation — `architect` (Codex) critique of the Definition of Done

**Critique:** `.omni/handoffs/DOD-CRITIQUE-codex.md` — 50 findings, verdict *reject as binding*.
**Reconciled by:** `product` (Claude), single author of `docs/prd/DEFINITION-OF-DONE.md`.
**Date:** 2026-08-22. **Draft judged:** DoD v1 (24 conditions), written blind against `a73ba88`.

I agree with the central judgment: *the cure is not a weaker finish line; it is a smaller,
harder, executable one.* v2 removes the simulation platform, the elapsed-time observation,
the operator drill, the throughput target and the test-count target; it adds two confirmed
defects, a fifth confirmed bug, a machine-readable manifest, one close-out validator, and a
handover/traceability pair.

Condition numbers in the "resulting condition" column are **v2** numbers.
`ACCEPT` = adopted as stated. `ACCEPT (mod)` = adopted with a stated change.
`REJECT` = condition kept, reason given.

| # | Verdict | Reasoning (one line) | Resulting condition |
|---|---|---|---|
| 1 | ACCEPT | The one-bug-one-condition shape is the part of v1 worth keeping; the repairs go inside it. | Section A keeps the shape and now runs 1–7 (five install/liveness bugs + two confirmed duplicate-work defects). |
| 2 | ACCEPT | A stub producer proves the shape of the failure, not the failure; without baseline evidence a green test proves nothing was ever broken. | 1 + 8: every required test carries a baseline record (pre-fix SHA, exact command, non-zero exit, captured output) validated by `tests/dod_validate.py`. |
| 3 | ACCEPT | "No `/usr/local/go` present" was ambiguous between stubbing, a fixture root and mutating the host — the third is unacceptable in a test. | 2: the test must name its isolation mechanism and assert it can neither read nor write the host `/usr/local/go`. |
| 4 | ACCEPT | The brief says bootstrap must not *assume* `systemctl --user` works; it does not say bootstrap must manufacture a session — I preselected a mechanism. | 3 states the outcome (unattended user services installed and enabled, or a non-zero exit naming the root remediation) and leaves the mechanism to the architecture (`.omni/decisions.md` A11). |
| 5 | ACCEPT | "Half-installed" named no files and no states, so nobody could settle it. | 3 enumerates the six unit files and their required enable states, and requires the failure path to leave none of them enabled. |
| 6 | ACCEPT | Correct and load-bearing: registry/ledger/STATUS writes are not proof of a live Program — a worker or a timer produces them too. Program ruling 8 supplies the replacement design. | 4 requires a Program-owned liveness record validated by CONTENT (writer identity, run id, host boot id, monotonic sequence, age), with the backup signal monitored separately. |
| 7 | ACCEPT | A `grep` for one spelling of `systemctl` cannot settle "never mutates a service"; comments match and wrappers evade. | 4 requires an instrumented command boundary: the test fails if the watchdog attempts any service-management mutation. |
| 8 | ACCEPT | My `awk` counted whitespace fields, matched rows that need not be the listed tests, and never checked that the recorded output was a failure. It was vacuous. | 8: machine-readable `docs/prd/DOD-MANIFEST.json` + validator comparing exact expected test IDs, baseline SHA, non-zero exit and captured output. |
| 9 | ACCEPT | Program ruling 3: the brief requires a regression test and smallest safe fix for every confirmed bug; parking one in tech debt contradicts it. | 18: `CONFIRMED-BUG` must point to an integrated fix plus baseline evidence; only `ACCEPTED-RISK` may point to tech debt. |
| 10 | ACCEPT | A read-only bind of `$PWD` carries untracked files and local state — that is the opposite of a clean clone. | 11: the rehearsal consumes a `git archive` of the exact commit into an empty workspace, asserting a clean worktree and `/srv/nightshift` absent. |
| 11 | ACCEPT | "No credentials" and "no network" are different claims, and stubs can manufacture all four PASS lines. | 11: the script header declares, per step, hermetic vs networked, and which steps are stubbed; at least the decision logic under test must be real code. |
| 12 | ACCEPT (mod) | Right that four fixtures cannot prove a universal; wrong to demand coverage of *future* branches, which is unbounded by construction. | 12: a manifest of every preflight check **as it exists at close-out**, each with positive and negative fixture, exit status and remediation reference. |
| 13 | ACCEPT | The arithmetic was simply wrong — 10 + 4 + 2 named tests, not 22 — and a count target invites padding anyway. | Count target removed; 8's required-test manifest replaces it. |
| 14 | ACCEPT | A job existing in YAML and a green latest run prove neither that both jobs ran nor that they ran against the current tip. | 10: static trigger validation + `headSha` equality with `main` + per-job conclusions. |
| 15 | ACCEPT | Fair and precise: topic coverage was complete, validity was not. Table shape was doing the work of an audit. | Sections D conditions 13–17 keep coverage; the validator now checks structured records rather than table syntax. |
| 16 | ACCEPT | Repeated `Q1` rows, verdict words appearing inside evidence prose, and multiple verdicts per row all pass my regex. Fraud-tolerant is not decidable. | 13–17: each audit doc carries a fenced JSON record; the validator enforces exact ids `Q1..Qn`, uniqueness, exactly one verdict, non-empty typed evidence. |
| 17 | ACCEPT | Line numbers drift the moment a fix lands, so `path:line` self-invalidates. | 13–17: evidence must be commit-qualified (`path` + symbol + SHA) or a command with captured output. |
| 18 | ACCEPT | "Is there any order…" is an unbounded universal that no table row settles. | 13 Q3: a documented acquisition-order table covering every path that holds two or more resources, plus a bounded adversarial test over exactly those orders. |
| 19 | ACCEPT | "Sleeps exactly once" and "terminating behaviour" had no observable definition. | 14 Q3 requires named state transitions, persisted timer/cooldown identity, max retries and terminal state — and records that `vendor` cannot express a hard outage. |
| 20 | ACCEPT | Without this, an audit could confirm "review is unenforced" and dispose of it into tech debt — laundering a protected requirement. | 18 + 19: brief-protected requirements are release-blocking invariants; a confirmed violation must be fixed, not accepted. |
| 21 | ACCEPT | Q1 asked about four sink classes; my test covered three components. The audit outran its own evidence. | 20: an enumerated sink corpus (logs, journald, alert bodies, PR/issue comments, CI logs, transcripts) with sentinel injection at every credential source. |
| 22 | ACCEPT (mod) | Right that some failures have no single corrective command; wrong that naming one is unsafe — `.omni/blocked.md` naming the exact `apparmor_parser` line is the difference between a five-minute fix and a dead run. | 12 + 17 Q4: actionable diagnosis plus a remediation reference always; an exact command **where one safe deterministic command exists**. |
| 23 | ACCEPT | "No empty cell" was prose pretending to be a check, and grep would not catch duplicate ids or dangling links. | 18: parser validating unique finding ids, source existence, allowed disposition types and resolvable PR/tech-debt links. |
| 24 | ACCEPT (mod) | Correct in general — an accelerated-clock simulator is product work the brief never asked for. Program ruling 4 carries the exception: two duplicate-work defects are already CONFIRMED, so their tests are targeted, not speculative. | v1's 15–18 deleted; replaced by 6 and 7 (tests for the two confirmed defects) and by bounded adversarial tests inside 13. |
| 25 | ACCEPT | Preventing simultaneous claims still permits the same task to be executed twice sequentially with no recovery transition — which is the actual failure the brief names. | 7: at most one authorized execution generation per task, with explicit recovery-generation records. |
| 26 | ACCEPT | An advanced clock does not exercise process survival, disk growth, journald rotation or reboot; presenting it as seven days was false. | Simulation deleted; 14 Q4 answers the growth question in writing instead of pretending to have observed it. |
| 27 | ACCEPT | Sharp catch, and it cuts toward the brief's protected set: my v1 condition would have pressured serialize leases into auto-release, weakening the invariant I was hired to protect. | 13 Q4: recovery must be an authorized, auditable transition (verified drop/reassignment or explicit recorded break); serialize leases must NOT gain TTL auto-release. |
| 28 | ACCEPT (mod) | Right that `all_cooling` is not sleep-and-resume; a live vendor test is impossible here because every Codex seat is down. | 14 Q3 observes persisted scheduler/cooldown rows and a resume attempt across the timer boundary in a fixture, and says plainly that live-vendor behaviour is unobserved. |
| 29 | ACCEPT | "Exactly one owner" is meaningless for dropped, completed and queued tasks. | 14 Q2: a state→owner invariant table by lifecycle state, compared across restart by stable task id and execution generation. |
| 30 | ACCEPT | A journal line proves logging, not notification. Naming it honestly is better than implying delivery. | 4: "alert attempt recorded" is the tested property; delivery configuration is a separate, separately-tested property. |
| 31 | ACCEPT (mod) | I reject the reasoning — the brief's autonomy clause is about not *waiting for approval*, and the owner already runs `preflight.sh` by hand, so a named manual step is not a violation. I accept the outcome: an automated isolated test is stronger evidence and cheaper. | Real-host drill deleted; 4 keeps an automated, isolated silence test. |
| 32 | ACCEPT | Two self-authored timestamps cannot establish continuous operation, and "continuous" had no sampling definition. | 72-hour observation deleted from the DoD; long-run evidence becomes follow-up material, not a finish condition. |
| 33 | ACCEPT | This is the most important finding after 6: I had written a waiver into a boundary the brief explicitly protects. An ADR explains, it cannot permit. | 19: behavioural denial tests per protected capability; no ADR clause anywhere in the DoD. |
| 34 | ACCEPT | A path-limited `git diff` plus "inspection at close-out" is subjective and misses controls elsewhere. | 19: an enumerated set of protected capabilities, each with a test asserting the denial still happens. |
| 35 | ACCEPT | Token-shape matching is both unsound and incomplete; sentinels are exact. | 20: unique sentinels injected at every credential source, asserted absent from the enumerated sink corpus. |
| 36 | ACCEPT | "Every script in `deploy/` and `bin/`" is not a set a command can safely enumerate — `bin/nightshift-ctl` is Python. | 12: bash files identified by shebang manifest; `bash -n` runs on exactly that set. |
| 37 | ACCEPT | The grep printed lines and proved nothing, and would have "passed" if it errored. | 12: new `|| true` in an install-critical path is forbidden unless annotated with a parsed exception marker. |
| 38 | ACCEPT (mod) | Right that git history cannot prove PR provenance and that `.omni/reviews.md` is self-attestation; the squash/rebase point is also correct. Program ruling 5 changes the gate from merged to followed. | 22: per-change GitHub query (PR exists, reviews by a different crew and vendor, gates run); queued-behind-a-gate is compliant, `BLOCKED` if GitHub is unreachable — never auto-pass. |
| 39 | ACCEPT | Program ruling 2: no `git log` inside `/srv/project` can observe writes to a path outside it. Asserting the unprovable is how a fixed point rots. | 21 states what is observable (this repo's history contains no such path; a forward-looking control for the remainder of the run) and explicitly disclaims proof of historical non-modification. |
| 40 | **REJECT** | Program ruling 1: `/home/nightshift/nightshift-runtime` is not my invention — the owner named both frozen paths in the dispatch that created this session; `.omni/brief.md` records only one because the owner wrote the other in the dispatch. It carries owner authority. | 21 keeps both paths, and the PRD records that the second comes from the dispatch, not the brief file. |
| 41 | ACCEPT | Program ruling 4: no owner request, no capacity justification, and no workload definition — and the real ceiling is vendor quota, not SQLite. | 500 ms p95 deleted; the quality bar carries no throughput number. |
| 42 | ACCEPT | The file claimed every condition had an exact settling command; a dozen did not. Claiming it was worse than the gaps. | 9: one close-out validator, `tests/dod_validate.py`, reporting every condition independently, with exactly two manual exceptions named in this file. |
| 43 | ACCEPT | The latest green run may predate the tip, so "green on main" was not what my command measured. | Definitions section: `headSha` of the run must equal the current `main` SHA. |
| 44 | ACCEPT (mod) | Right that a name is not evidence; program ruling 7 says do not swap one ceremony for another, so IDs are stable and the manifest binds them to real node ids. | Requirement IDs `RT-nn`; the manifest maps each to a concrete test node id, the production `file:symbol` it drives, its baseline record, and the validator asserts zero skips. |
| 45 | ACCEPT | I referenced a change-control section that did not exist, in the file whose whole value is being a fixed point. | New "Change control" section: authority, evidence threshold, immutable changelog, and the rule that additions become follow-up projects. |
| 46 | ACCEPT | Letting a downstream artifact silently rebind the fixed point is exactly the drift this file exists to stop. | Definitions section: `CAPACITY.md` may *propose*; numbers bind only after a recorded changelog revision here. |
| 47 | ACCEPT | The 83-test baseline, the 18-second runtime and the topology were asserted without their commands. | New "Verified baselines and assumptions" section: each fact labelled verified (with command, output and SHA) or assumption (with what breaks). |
| 48 | ACCEPT (mod) | Right on the p95, the counts, the 72 hours and the drill — all deleted. I reject two items: alert re-arm is not new work (`REALERT_HOURS` already exists and a watchdog that alerts once and never again is the same silent failure), and idempotency is the scripts' own documented claim, so a false claim is a defect. | Deletions as above; re-arm folded into 4; idempotency scoped to the two scripts' header claims over the paths this project touched (17 Q3). |
| 49 | ACCEPT | Program ruling 6: green checks with no usable handover is a real failure mode, and this project's output is operational, not user-facing. | 23: `docs/HANDOVER.md` with merged fixes, evidence, accepted risks, operator actions, rollback notes and links, verified from a clean clone. |
| 50 | ACCEPT | Scattered evidence lets an omission survive every local check — the matrix is what makes the set auditable as a whole. | 24: machine-validated traceability matrix from all five confirmed bugs and all thirteen audit areas to condition, finding, test and PR. |

## Summary of what the critique changed

**Deleted** (five conditions and two requirements): the accelerated seven-day simulation, the
crash/recovery omnibus harness, the vendor scheduler harness, the ≥72-hour observation and
operator-run silence drill, the ≥105 test count, and the 500 ms p95 target.

**Added** (four conditions): the fifth confirmed bug (bwrap probed by presence, not function),
the two confirmed duplicate-work defects promoted out of tech debt into fixes, the machine-readable
manifest plus single close-out validator, and the handover/traceability pair.

**Rewritten** (most of the rest): every check that was prose, a fragile `grep`, or a self-attested
table is now either a validator check over structured records, or an explicitly named manual
exception. Two manual exceptions remain, both named in condition 9.

**Rejected outright:** one finding (40).
**Partially rejected:** five (12, 22, 24, 31, 48) — outcome adopted, reasoning corrected.

## One consequence the program must action

`docs/prd/MILESTONES.md` still maps v1 condition numbers and is now stale. I was scoped to two
files this turn and did not touch it. It needs re-mapping to v2's 24 conditions before any crew
is chartered against it.
