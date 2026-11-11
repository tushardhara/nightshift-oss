# ADR-0017 — Observe Omnigent run history with an LLM-independent detector

- **Date:** 2026-08-24
- **Status:** Accepted
- **Decider:** owner-directed maintenance for issue #132; accepted after
  `staff`, security, and cross-vendor review each returned PASS
- **Proposers:** Claude maintenance design review and the Codex `architect`
  incident proposal recorded on issue #132
- **Amends:** ADR-0002 and ADR-0006 only by adding a distinct scheduled-run
  completion signal; it does not let that signal stand in for Program liveness

## Context

The 05:00Z and 06:00Z hourly heartbeat firings both reached Omnigent, created
durable sessions, and then failed on the initial Claude inference with `529
Overloaded` before executing a tool. The scheduler remained active and advanced
the next occurrence. No component outside the failed LLM turn reported that the
autonomy loop could not proceed.

Read-only inspection of the installed Omnigent 0.10 source and live API corrects
one premise in the issue's initial proposal: Omnigent already persists every
firing in `scheduled_task_runs` and exposes the owner-scoped history at
`GET /v1/scheduled-tasks/{id}/runs`. Each record has a stable run id, schedule,
start/finish timestamps, state, conversation id, and an error code. The failed
conversation's items retain the more specific 529 even though its run-level code
is the generic `runner_error`. Duplicating that run ledger in Nightshift would
create two authorities and still lose the better failure class.

The adopted issue design has five parts. The owner-directed first increment is
parts 1–3: durable classified observations, a non-LLM failed/missed/stuck
detector, and direct delivery. The off-box dead-man and monitor-the-monitor
canary remain required follow-up work; a host-local timer cannot honestly claim
either property.

## Options considered

### A — Extend the existing shell liveness watchdog (Claude proposal rejected)

This reuses its GitHub delivery and marker. It was rejected because that
watchdog answers a different question: whether the Program wrote an attributed
control-plane beat. Combining scheduler completion, Program progress, and
monitor health would violate ADR-0002's signal separation and make one success
capable of masking another failure. Parsing paginated JSON and classifying
conversation items is also safer in a small typed Python program than in shell.

### B — Read Omnigent's run ledger and persist only normalized observations
(reconciled Claude/Codex proposal)

A stdlib-only detector polls the loopback owner-scoped task and run endpoints.
For a failed run it reads only that run's persisted session items and maps known
signatures to a closed classification vocabulary. It never launches or prompts
an LLM. Nightshift persists the observation, consecutive-failure count, expected
next occurrence, and delivery acknowledgement transactionally through new
`nightshift-ctl schedule` verbs. Omnigent remains authoritative for firings;
Nightshift is authoritative only for detector state.

This option wins because it uses the already-durable upstream record, preserves
causal fidelity, and keeps alert retry/deduplication in the existing
transactional control plane.

### C — Query PostgreSQL directly or edit the Omnigent cache (rejected early)

Direct SQL couples Nightshift to Omnigent's private schema, bypasses owner scope,
and would require a production database credential in another component.
Reading or editing the cache is worse: it is not the run ledger and manual cache
mutation is explicitly forbidden by the maintenance brief. The public loopback
API is the reviewed boundary.

### D — Add retry or provider failover in this detector (rejected for this PR)

Retry inside the heartbeat prompt cannot run when the first inference fails.
Making the detector call `run-now` would turn an observer into a scheduler,
introduce overlap and retry-budget policy, and hide a larger Omnigent-layer
change inside the alert PR. Scheduler-level bounded retry remains worthwhile,
but it is mitigation after the independent detector exists, not a substitute
for it.

## Decision

1. Add a dedicated stdlib-only scheduled-run detector. It polls only loopback
   IP-literal Omnigent HTTP endpoints with inherited proxies disabled, redirects
   refused, bounded timeouts, bounded response sizes, and at most 100 runs per
   poll.
2. Treat Omnigent's `scheduled_task_runs` history as the durable firing source.
   Persist only the normalized observation and monitor checkpoint in new
   additive control-plane tables, mutated exclusively by
   `nightshift-ctl schedule observe|expect|state|alerted`.
3. Classify only a closed set: `vendor_overloaded`, `vendor_rate_limited`,
   `auth`, `timeout`, `host_unavailable`, `runner_harness`, `incomplete`, and
   `unknown`. Never persist or deliver raw transcript text.
4. Add a sixth row to ADR-0002's signal table:

   | Signal | Written by | Never written by | Answers |
   |---|---|---|---|
   | Scheduled-run observation (`scheduled_run_observations`) | the detector process, after a completed poll reached both Omnigent and the control plane | the Program, heartbeat timer, liveness watchdog, backup, Omnigent scheduler, or a systemd restart | "did the last known firing of this monitored scheduled task complete cleanly?" |

   Omnigent owns the firing record; the detector owns only the observation of
   it. No consumer may treat this observation as evidence that the Program is
   alive or making progress. `heartbeat:program` remains the sole Program
   liveness verdict. The two signals may be displayed together but may never be
   OR'd, substituted, or used to refresh one another.
5. Alert every distinct terminal `failed` or `skipped` run immediately. Alert a
   known expected occurrence as `missed` after a configurable grace, and a
   `running` occurrence as `stuck` after a configurable deadline. Defaults are
   ten minutes for `missed` and 75 minutes for `stuck`; the stuck deadline must
   remain strictly below Omnigent's six-hour lazy force-fail boundary.

   An actual run's observation key is `(scheduled_task_id, run_id)` and never
   includes status or classification. A later transition of the same run from
   `running/stuck` to terminal `failed` supersedes that row's status and class;
   it does not create a second incident or increment the consecutive count
   again. Supersession clears that row's delivery acknowledgement so the
   terminal state is reported as an update while retaining the same incident
   sequence number. A missing occurrence, which has no run id, is keyed separately by
   `(scheduled_task_id, expected_scheduled_at, "missed")`. Identical keys are
   idempotent, but distinct run ids/expected occurrences are never
   time-suppressed. Their consecutive count escalates the message.
6. Deliver directly with `gh issue comment` as an argv vector and a protected
   body file. Delivery does not traverse Claude, Codex, Omnigent sessions, or a
   live Program driver. Mark an observation delivered only after `gh` succeeds;
   otherwise exit nonzero and retry the same observation on the next timer run.
   Deliver at most five observations per poll. Delivery is intentionally
   at-least-once: a process death after GitHub accepts a comment but before the
   acknowledgement commits can produce a duplicate bearing the same hidden
   observation-key marker.
7. Establish a positive baseline on first poll: inspect the latest run (alert if
   it is currently failed/stuck), then remember Omnigent's next occurrence.
   `missed` is judged only against a previously observed expectation, never
   from absence alone.
8. Install it as its own user oneshot/timer on a five-minute cadence. The unit
   is rendered with an absolute exact-SHA release root, canonical mutable
   project/control DB, task id, and alert destination. It neither restarts a
   service nor mutates the scheduled task. The service clears proxy variables,
   makes the host filesystem read-only except for the canonical `.omni` state,
   and gives the entire oneshot an explicit start deadline so a poisoned poll
   cannot leave the five-minute timer permanently activating. `RuntimeMaxSec`
   is deliberately absent because systemd ignores it for `Type=oneshot`.
9. This increment does **not** satisfy the off-box dead-man or alert-path canary
   parts of #132. They remain explicit residual risks and closing gates. The
   existing same-host Program watchdog is not relabelled as either proof.

## Consequences

A single 529 becomes a durable `DEGRADED / run could not proceed` report without
depending on the failing vendor. Two distinct failures produce two reports and
the second carries an escalated consecutive count. A broken GitHub delivery is
visible as a failed systemd unit and is retried without falsely acknowledging
the observation.

The detector adds one local timer, two additive SQLite tables, and a loopback API
read every five minutes. It still shares the VPS and GitHub transport with the
system it watches. Total host loss and a broken alert credential can therefore
remain silent until parts 4–5 land; documentation and acceptance must say so.
The same is true if this detector's own timer is masked or disabled while the
host stays otherwise healthy: no local component may infer monitor health from
the absence of an alert. That is the explicit canary gap, not a green state.

## Cost

Current volume is one HTTP poll per five minutes, at most a few hundred small
run records per day, and GitHub traffic only on a new incident. Monthly monetary
cost is zero at today's and 100x volume under the existing subscriptions and
VPS. At 100x scheduled tasks the per-task polling shape should be replaced by a
single paginated monitor endpoint or event stream before adding 100 timers.

## One-way door?

No. The tables and unit are additive. Disabling the timer stops all behavior;
dropping their data is not required for rollback. Omnigent's task, run history,
Program registration, control-plane identity, and runtime checkout are not
rewritten by the detector.

## Scale tier

Tier 0: one VPS, one hourly autonomy task, local SQLite monitor state, direct
GitHub issue delivery. Move up when more than 20 scheduled tasks need monitoring
or the polling pass exceeds 10 seconds; then add one server-side paginated
monitor feed rather than per-task timers.

## Reversal trigger

Revisit the polling boundary when Omnigent exposes normalized run failure classes
or a durable terminal-run event stream. Revisit direct GitHub delivery when an
independent owner paging channel exists. Parts 4–5 become mandatory before
claiming the autonomy loop survives VPS loss or can prove its own alert path.

## Back-link

Applied as header metadata in this PR after acceptance; neither predecessor's
decision text was edited. ADR-0002 carries:

```
Amended by: ADR-0017 (2026-08-24) — §1 gains a sixth, separately-owned
scheduled-run observation signal. It may never stand in for or refresh Program
liveness; all prior signal rows and semantics stand unchanged.
```

ADR-0006 carries:

```
Amended by: ADR-0017 (2026-08-24) — adds a separate scheduled-run completion
observation and applies D3's no-inference/no-OR rule to it. ADR-0006's five
existing signals, lifetime-binding property, and residual risks stand unchanged.
```

Refs #132
