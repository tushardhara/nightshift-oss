# Nightshift State & Memory Architecture — PROJECT

Owner input: `.omni/brief.md`. Author: `pm` (head of product). Status: revised
after cross-vendor hostile review of the definition of done. Date: 2026-08-29.

**Baseline commit.** `program` records the commit SHA of this repository as it
stood before this project began here, and does not change it afterwards:

| Field | Value |
|---|---|
| Baseline commit SHA | `ca52f103e06087dc2d8a62d7198ed0bd12729d6f` (`test: initialize isolated memory discovery fixture`), recorded by `program` on 2026-08-29 before the first architecture document landed. Nothing in this project is committed, so every path this project produced is visible as untracked or modified against this SHA. |
| If the repository had no commit at baseline | the baseline is the empty tree, and condition 1's check compares against a file manifest recorded in this table instead |

Until this cell is filled, DoD condition 1 is not met. It is the only provenance
this project has for "what did we add".

## What this is

A design — not a running system — for where Nightshift keeps its state when it is
pointed at someone else's private codebase, so that the codebase stays clean and
Nightshift stays explainable. The deliverable of this project is a set of
documents in this repository that a stranger can read and judge; no code is
written, nothing is deployed, nothing outside this machine is touched.

## Who it is for

Three concrete users. Every condition in `DEFINITION-OF-DONE.md` must be good for
all three or say explicitly which one it serves.

**(a) A company installing Nightshift on an existing private codebase.**
Job to be done: get agent work done against their repository *without accepting
someone else's operational exhaust in their source tree*. Their success test is
their own: `git status` is clean, code review sees no unrelated files, and
nothing internal to Nightshift is readable by anyone who clones the repo.

**(b) An operator running Nightshift locally or offline.**
Job to be done: run the whole product on one machine, with no outbound network and
no managed services, and get the same behaviour they would get in production. Their
success test is that a local run and a production run cannot be told apart by their
observable behaviour, only by their scale and their cost.

**(c) An operator running a multi-tenant managed service.**
Job to be done: run many customers' work on shared infrastructure and be able to
answer, per customer, what happened, why, against which exact source version, and
what state is authoritative after overlapping runs, crashes, migrations or the
removal of Nightshift — and be able to export or delete one customer entirely
without touching another.

## The problems, stated as problems

1. **Repository clutter.** Nightshift today writes `.omni/`, generated `docs/`,
   worktrees, transcripts, reports, handoffs, registries and control state *into
   the target repository*. On a customer's codebase that is unrequested material
   in a tree they own and are audited on.
2. **Git noise.** Those writes become diffs, commits, branches and refs mixed into
   the customer's history, polluting blame, review, CI triggers and release notes
   with changes no human on that team made or asked for.
3. **Internal material exposure.** Transcripts, evidence, reasoning, handoffs and
   agent registries are internal operational material. Placed in the customer's
   repository they are readable by every person and system with repository access,
   and they leave with every clone and every fork.
4. **File-vs-database authority confusion.** A file in the tree *looks*
   authoritative. When a database holds different live state, two sources disagree
   and no rule says which wins — so humans trust the stale one, and recovery after
   a crash becomes guesswork.
5. **Provenance.** An artifact or a decision that cannot be tied to a repository
   and an exact commit SHA cannot be defended later. "Why did it do that" is
   unanswerable if the source version it saw is unknown.
6. **Multi-tenant safety.** One operator holding many customers' code, transcripts
   and evidence needs isolation, encryption, redaction, retention, export,
   deletion and audit to be properties of the design, not habits of the operator.
7. **Undeterminable state.** *(added after review — finding F76.)* Even with all of
   the above answered, an operator can still be unable to say what the correct state
   *is*, or to get back to it, after overlapping runs, a crash, a migration or a
   removal. Naming an authority does not by itself produce recoverable truth: that
   needs written state transitions and invariants, a backup and restore path, an
   access-control model, and a repair procedure.

## The core loop

The smallest sequence that makes this product useful. Everything else is optional
until this works — and in *this* project each step is a design statement, not a
running behaviour:

1. Nightshift is pointed at a customer repository and reads it at an exact SHA.
2. It does work, writing every operational fact — tasks, edges, state, runs,
   claims, leases, decisions, evidence, transcripts — to a store *outside* the
   customer repository, under one named authority, without violating a stated
   invariant.
3. An operator asks "what happened and why", and gets an answer traceable to that
   repository and that SHA.
4. A human-accountable identity explicitly approves one document to be published
   *into* the customer repository; nothing else ever lands there.
5. Nightshift is removed, and both parties can say exactly what remains, what is
   authoritative, what left with it, and how to read what they were given.

## In scope

- The state inventory: every class of state Nightshift holds, its tier
  (authoritative control fact / narrative memory / customer-owned document), its
  key, its relations, its sensitivity and its behaviour under failure.
- The single durable authority for operational control state, its consistency and
  recovery rules, and the rule that settles disagreement between it and any file.
- The authoritative **state machine and invariant catalogue** that concurrency,
  repair and conformance are all judged against.
- The customer-repository footprint contract, including a zero-file mode that is
  the default, and the removal/residue story.
- The publication gate: the one enforcement boundary by which a document enters the
  customer repository, bound to approved bytes.
- Provenance binding to a canonical repository identity and exact source version.
- The role and the hard prohibitions of semantic memory.
- One behavioural contract implemented by both the local/offline and the
  multi-tenant deployments, across named dimensions, with divergences enumerated.
- Concurrency safety for overlapping runs and crashed workers.
- Tenant isolation, encryption, redaction, retention, deletion, audit — and the
  **identity and authorization model** that enforces them.
- **Durability**: failure model, backup coverage, restore procedure, stated RPO/RTO.
- **Operational detection and repair** for named failure signals.
- **Format and schema evolution**: versioning, compatibility, upgrade, downgrade.
- **Offboarding**: export as a usable exit path, key handover, decommission, and
  which artifact answers each operator question after removal.
- A safe, rehearsable migration path from today's `.omni/` and generated-document
  model.
- Capacity arithmetic (day one and 100×), operational cost, scale triggers and
  reversal triggers, and a comparison of the candidate components the brief names.

## Explicitly out of scope

- **Any implementation.** No code, no schema, no migration executed, no cutover.
- **Any external activity.** No GitHub or Kanban issues, projects, PRs, branches
  or schedules; no purchases, deployments, credential changes or publications; no
  heartbeat, automation, crew chartering or worker dispatch.
- **Proving that none of the above happened.** No artifact in this repository can
  distinguish silence from compliance, so the definition of done does not pretend
  to check it (findings F16–F21, F42). These stay execution constraints on the
  people doing the work, and are listed in the DoD's
  `## What this document cannot settle`.
- **Choosing technology on my authority.** This document names roles ("the
  control-state authority"), never products.
- **Changing what Nightshift does.** This project moves state and defines its
  rules; it does not redesign task execution, agent behaviour or the feature set.
- **Certification against a named compliance regime** (assumption 8).
- **Measured performance, latency or retrieval quality.** There is no system to
  measure; a design cannot be benchmarked.
- **Executable or automated conformance tests.** Cases are written and
  `automated: no` for this project by construction.
- **A UI, an API surface, or a customer-facing console.**

## Follow-up projects (not this one)

Recorded here rather than admitted to the definition of done, so that this project
can finish. Each is genuine work the review surfaced or the brief deferred:

1. **Implementation and migration execution** — building the design, running the
   `.omni/` migration for real, and the production cutover.
2. **Automated conformance suite** — turning `docs/tests/cases.md` into executing
   tests with fixtures, including the measured claims (retrieval quality, latency,
   restore-time RTO verification).
3. **Named compliance regime** — if the owner names SOC 2, HIPAA, FedRAMP or a
   residency requirement, the evidence and control obligations that follow.
4. **Approval and identity integration** — binding the publication approver and the
   actor register to a real identity provider and a real review surface.

## Assumptions

Not confirmed by the owner. Each is a place the brief was silent and I chose a
direction rather than waiting. Assumptions 18–24 were added or corrected by the
cross-vendor review; the DoD reproduces the ones it depends on as DA1–DA10 so it
does not rely on this file.

1. The deliverable of this project is a **design**, not a running system — the
   brief's hard constraints say architecture and planning only, and work entirely
   locally. Recorded by the program. *If wrong:* every condition in the DoD is
   mis-shaped and the whole document set needs rewriting against runtime checks.
2. Nightshift already exists and works today against `.omni/`; this project
   changes **where state lives and what governs it**, not what Nightshift does.
   *If wrong:* the state inventory is incomplete and the migration has no source.
3. Day-one scale is small — a handful of tenants, tens of repositories, low
   hundreds of tasks per day. I do not put invented numbers in the DoD; the DoD
   binds to whatever `architect` writes in `docs/architecture/CAPACITY.md`.
   *If wrong:* the cheapest-correct verdict flips and the scale ladder starts a
   tier higher.
4. Customer repositories are Git repositories the operator can read, and a
   proposed change can be offered back through the customer's normal review path.
   *If wrong:* the publication gate has no delivery mechanism and must be redesigned.
5. "Zero-Nightshift-file mode" means zero files and zero refs in the customer's
   working tree and history; an out-of-repo working area on the operator's own
   machine is permitted and must be declared. *If wrong:* worktrees and checkouts
   have nowhere to live and the mode is unbuildable.
6. The managed service is one operator serving many customers; customers do not
   get direct access to the underlying stores. *If wrong:* isolation must become a
   customer-facing access-control product, which is a different project.
7. Local/offline mode must work with **no outbound network at runtime**, including
   no external embedding or model API, so semantic memory must be optional and
   degradable. *If wrong:* offline mode is a fiction and the cost model is wrong.
8. No compliance regime is named, so the design targets a generic
   "enterprise-private-code" bar. *If wrong:* a named regime adds evidence and
   control requirements that belong in follow-up project 3, not in this DoD.
9. Single data region; no residency requirement. *If wrong:* the tenancy and
   storage decisions are one-way doors that were opened wrong.
10. Existing `.omni/` installations are few and operator-controlled, so migration
    may take downtime and require a human confirmation. *If wrong:* migration must
    be online and reversible mid-flight, which changes the target state model.
11. The customer owns documents that were published into their repository;
    deleting a tenant does not retract commits already in the customer's history.
    *If wrong:* deletion becomes a distributed retraction problem across repos we
    do not control.
12. There is no stated infrastructure budget, so "cheapest correct" is judged
    against day-one numbers and assumes no dedicated infrastructure beyond the
    smallest viable managed footprint. *If wrong:* the component verdicts change.
13. Decisions in this evaluation are recorded as ADRs and may reach status
    `accepted`, but nothing is procured, deployed or credential-bound.
    *If wrong:* the ADR set overstates commitment and must be downgraded.
14. ~~The state classes enumerated in the DoD are the complete set.~~ **Superseded
    by assumption 18** after finding F69.
15. Every component the brief names is a **candidate**, not a requirement;
    rejecting all of them for a given role is a valid outcome if the reason is
    written down. *If wrong:* one of them is mandated and the comparison is theatre.
16. Publication approval is given by a **named accountable identity distinct from
    the process performing the write**; a policy-backed review satisfies this if the
    accountable identity is recorded. (Revised after finding F60 — the earlier form
    required an unautomated human, which the brief does not say.) *If wrong:* the
    gate cannot deliver "never an accidental side effect".
17. The language of record is English Markdown inside this repository; there is no
    external document system, wiki or tracker in play. *If wrong:* the DoD's file
    paths do not resolve for the reviewer.
18. The sixteen state classes are a **minimum, not a maximum**; the design may add
    classes (tenant, key, publication record, retention job, repository
    registration) and must declare its list complete. *If wrong:* a necessary
    entity is missing and conditions 2, 6, 11 and 23 all under-cover.
19. One durable authority for control state does **not** imply one physical
    component per architectural role; a role may be a named composite.
    *If wrong:* condition 13 forces an artificial decomposition.
20. Secret redaction is a detection model with residual risk, not a perfect
    guarantee. *If wrong:* the design owes a mechanism nobody has, and condition 11
    becomes unsatisfiable.
21. A migration may have zero, one or several irreversible boundaries.
    *If wrong:* condition 12 forces a topology the migration does not have.
22. Retention may be duration-based, event-based or policy-source-based, and holds
    exist. *If wrong:* condition 11(b) rules out a legitimate retention design.
23. 100× is defined per workload axis, not as uniform growth. *If wrong:* the
    capacity model overstates or understates the axes that actually scale.
24. File paths, document names and verifier roles are project conventions chosen to
    give "met" an address; they are means, not owner outcomes, and may be renamed by
    agreement. *If wrong:* nothing substantive changes — only addresses.

## Non-goals and anti-requirements

- **Not a second source of truth.** If the design ends with two things that can
  both claim to hold current control state, it has failed.
- **Not a memory product.** Semantic memory is an accelerant for retrieval. The
  moment it can decide, schedule, claim, or contradict authoritative state, the
  system becomes unexplainable.
- **Not an infrastructure shopping list.** Any component that day-one numbers do
  not justify is a defect, not a feature.
- **Not a customer-repo write path with exceptions.** One enforcement boundary, or
  none. A design with a "just this once" bypass has no gate.
- **Not a migration that cannot be undone or recovered.** A migration with no dry
  run, no named reversibility boundary and no forward-recovery path is a cutover,
  and the brief forbids cutovers.
- **Not a Nightshift that hides.** Zero-file mode must not be achieved by making
  the operational record disappear; it must be achieved by moving it somewhere
  named, durable, restorable and auditable.
- **Not a design that only works when everything is healthy.** Overlapping runs,
  crashed workers, failed audit writes and lost stores are the normal case.
- **Not a document set that passes its own checks while leaving an operator unable
  to determine or recover correct state.** *(Added after finding F76.)*

## Open questions

None blocks work. Each has a decision I am making meanwhile.

| # | Question | Status | Meanwhile |
|---|---|---|---|
| Q1 | What is real day-one volume — tenants, repos, tasks/day, artifact bytes? | `proceed-anyway` | Assumption 3; the DoD binds to `CAPACITY.md` axes and sources, not to numbers I invent. Becomes blocking before implementation. |
| Q2 | Is there a named compliance regime (SOC 2, HIPAA, FedRAMP, EU residency)? | `proceed-anyway` | Assumption 8: generic enterprise bar. A named regime is follow-up project 3, not a DoD amendment. |
| Q3 | Who is the accountable approver for a publication — operator, customer, or both? | `proceed-anyway` | Assumption 16 names the property (accountable, distinct from the writer); the identity is configuration, and condition 20(e) enforces the separation. |
| Q4 | Do customers ever need direct read access to their own operational store? | `proceed-anyway` | Assumption 6: no. Export (condition 23) is the customer-facing path instead. |
| Q5 | How many live `.omni/` installations exist today, and does any hold state that cannot be reconstructed? | `proceed-anyway` | Assumption 10; condition 12 requires the legacy inventory to name its source and its loss invariants, so an unknown answer is survivable. |
| Q6 | Is retention duration a customer-negotiated term or an operator default? | `proceed-anyway` | Assumption 22: a class may cite a duration or a named policy source; holds are permitted. |
| Q7 | Must offline mode support semantic retrieval at all, or may it degrade to keyword? | `proceed-anyway` | Assumption 7; condition 7(e) requires stated behaviour when memory is absent. |
| Q8 | What recovery objectives does the owner accept — how much work may be lost, and how long may recovery take? | `proceed-anyway` | Condition 19(d) requires `architect` to state RPO/RTO numbers and carry their cost into `CAPACITY.md`; `pm` binds the DoD to the stated numbers, not to invented targets. Becomes blocking before implementation. |

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-08-29 | Initial draft from `.omni/brief.md`. | — |
| 2026-08-29 | Revised after cross-vendor hostile review of the DoD (77 findings; see `DOD-RECONCILIATION.md`): added problem 7 (undeterminable state) and the matching scope lines (invariants, durability, access control, repair, evolution, offboarding); added the baseline-commit record required by DoD condition 1; revised assumption 16 and superseded assumption 14; added assumptions 18–24; moved the follow-up-project policy out of the DoD into this file; added open question Q8 (recovery objectives). | Findings F60, F69–F74, F76, F59, F16–F21. |
