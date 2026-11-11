# Milestones — Nightshift State & Memory Architecture

Version 2 (2026-08-29), realigned to the 23-condition definition of done. Five
milestones, ordered. Every condition in `DEFINITION-OF-DONE.md` belongs to exactly
one of them; the map at the bottom proves it.

The sequence follows one rule: **the expensive and irreversible decisions are
settled before anything is allowed to depend on them.** Numbers before technology
(M1), authority, invariants, tenancy and access before everything downstream (M2),
the customer-repository contract and the migration before behaviour is described
against them (M3), behaviour under stress and failure next (M4), lifecycle, exit
and consistency last (M5).

A milestone closes when `pm` judges its exit criteria met against the checks
written in the DoD — not when the documents feel finished. Condition 1 is re-run by
`program` at every close even though it is accounted to milestone 1.

---

## M1 — The state ledger and the arithmetic

**Outcome.** A stranger can read one table and say what state Nightshift holds,
which of the three tiers each class belongs to by a written test, how it behaves
under five named failures, and how much of it there will be on day one and at 100×.

**Exit criteria.** DoD conditions **1**, **2**, **14**.

**The demonstrable thing.**
`docs/architecture/STATE-MODEL.md` `## Entity inventory` — at least sixteen rows,
each with tier, key identifier, relations, sensitivity and failure behaviour, plus
the `## Separation` prohibited-flow matrix — read beside
`docs/architecture/CAPACITY.md` with every axis filled, every day-one number
sourced, the 100× model stated per axis, and `## The arithmetic` recomputable by
the reader. The owner can react to this alone: it is the whole problem stated in
one page, with sizes and sources.

**Deliberately faked or deferred.** No component is named yet and no authority is
chosen — the Authority column holds a **role**. Choosing storage before the
arithmetic exists is the failure mode this milestone prevents. Repaid by M2
(conditions 3, 13).

---

## M2 — The one-way doors: authority, invariants, tenancy, access, components

**Outcome.** The decisions that cannot be cheaply undone are made and written down:
which single component is the durable authority for control state, the state
transitions and invariants every writer must preserve, how tenants are isolated and
who may do what, which component fills each role, and what number would move any of
it.

**Exit criteria.** DoD conditions **3**, **10**, **13**, **15**, **18**, **20**.

**The demonstrable thing.**
`docs/architecture/COMPONENT-EVALUATION.md` `## Comparison` (one matrix, nine
candidates, filled cells) and `## Selection` (one component or composite per role,
with the cheapest-correct statement), read beside `STATE-MODEL.md` `## Authority`
and its numbered **invariant catalogue**, `SECURITY-AND-TENANCY.md`'s isolation
invariant and encryption coverage table, `ACCESS-CONTROL.md`'s actor × operation
matrix, and `SCALE-LADDER.md` with a metric+threshold+source trigger in every row.
A reviewer can attack a single table and either break the choice or accept it.

**Deliberately faked or deferred.** The encryption boundary is specified here but
its operational lifecycle — redaction, retention, deletion, audit — is deferred so
this stays a decision milestone. Repaid by M5 (condition 11). ADRs are drafted here
but the decision register is not required complete and consistent until M5
(condition 17).

---

## M3 — The customer-repository contract, provenance, and the way in from today

**Outcome.** A company can see exactly what Nightshift will and will not put in
their repository, that a clean repository is the default, how a document gets in
when they want one, how anything is traced back to an exact source version, how an
existing `.omni/` installation gets across and back — and how formats change after
that without another one-off migration.

**Exit criteria.** DoD conditions **4**, **5**, **6**, **12**, **22**.

**The demonstrable thing.**
`docs/architecture/REPO-FOOTPRINT.md` — the footprint table with a `0 files / 0
refs` **default** mode, the seven relocated things, the footprint check with its
expected output and pass predicate, the publication boundary statement, the
`## Publication gate` with approval bound to a content digest and base commit, and
`## Removal` — read beside `PROVENANCE.md`'s canonical repository identifier and
bidirectional lookup, and `MIGRATION.md`'s rehearsal artifact, per-phase authority
and named reversibility boundaries. This is the milestone user (a) would read.

**Deliberately faked or deferred.** The publication gate is described as a design
with an accountable approver; no approval mechanism or identity-provider
integration is specified, because the brief forbids external activity — that is
follow-up project 4. The concurrency behaviour of a half-finished publication is
deferred to M4 (condition 9d).

---

## M4 — Behaviour under stress: contention, crash, loss, repair

**Outcome.** The design says what happens when runs overlap, workers crash, a store
is lost, memory is missing or wrong, and the same contract has to hold on a laptop
with no network and on shared multi-tenant infrastructure — and those statements
exist as written cases someone could execute.

**Exit criteria.** DoD conditions **7**, **8**, **9**, **16**, **19**, **21**.

**The demonstrable thing.**
`docs/architecture/CONCURRENCY.md` `## Scenarios` — four walkthroughs, each with
event sequence, committed effects, rejected effects, retry result and the
invariants preserved — beside `OPERATIONS.md`'s restore procedure with stated
RPO/RTO and its eight-signal detection-and-repair table,
`DEPLOYMENT-CONTRACT.md`'s ten-dimension contract with its offline definition, and
`docs/tests/cases.md` populated with rows (a)–(l), all status `specified`.
Demonstration is a walk: pick any invariant from M2, find the case, find the
document that specifies the behaviour.

**Deliberately faked or deferred.** Every case is `automated: no` — nothing runs in
this project. Repaid by follow-up project 2, not by a later milestone here, and
`pm` will say so rather than let "make them run" enter this DoD.

---

## M5 — Lifecycle, exit, and a pack that hangs together

**Outcome.** A managed-service operator can answer the data questions — what is
redacted, how long things are kept, how a tenant is deleted and audited, how a
customer leaves with something they can read — and the whole document set is
internally consistent, with every decision in a closed register carrying a cost and
a reversal trigger.

**Exit criteria.** DoD conditions **11**, **17**, **23**.

**The demonstrable thing.**
`docs/architecture/SECURITY-AND-TENANCY.md`'s four lifecycle sections, each with an
actor, a mechanism and an exact proof query, covering every class from M1 and the
ten mandatory audit events — beside `OFFBOARDING.md`'s export manifest, integrity
check, key handover and the **operator-questions table** (what happened / why /
which source version / what is authoritative, each answered after overlapping runs,
crash, migration and removal) — and `docs/decisions/REGISTER.md` with a matching
ADR per entry. Final demonstration is `program` walking the DoD coverage table end
to end and finding no unmet condition and no contradiction between two documents.

**Deliberately faked or deferred.** Nothing is deferred past this milestone. Work
identified here that does not map to one of the 23 conditions is recorded as a
follow-up project in `PROJECT.md`, not added to the DoD.

---

## Condition-to-milestone map

Every DoD condition appears exactly once.

| Milestone | Conditions | Count |
|---|---|---|
| M1 — State ledger and arithmetic | 1, 2, 14 | 3 |
| M2 — One-way doors: authority, invariants, tenancy, access, components | 3, 10, 13, 15, 18, 20 | 6 |
| M3 — Repository contract, provenance, migration, evolution | 4, 5, 6, 12, 22 | 5 |
| M4 — Behaviour under stress | 7, 8, 9, 16, 19, 21 | 6 |
| M5 — Lifecycle, exit, consistency | 11, 17, 23 | 3 |

Total: 3 + 6 + 5 + 6 + 3 = **23**. No condition unassigned; none assigned twice.

## Dependency order — why this sequence and not another

- **14 before 13.** Components chosen before the arithmetic exists is how a design
  buys scale it does not have; the cheapest-correct promise is unverifiable in the
  other order.
- **2 before 3 and 18.** You cannot name one authority, or write the transitions it
  guards, until you have agreed which state is control state.
- **18 before 9, 16 and 21.** Concurrency outcomes, conformance cases and repair
  actions are all judged against the invariant catalogue; written earlier, they have
  nothing to be judged against — this is the sequencing fix forced by finding F76.
- **20 before 11 and 5's separation of duties.** Deletion, export and approval all
  name actors; the actor register has to exist before those sections can be exact.
- **3 and 10 before 4, 5, 6, 12.** Footprint, publication, provenance and migration
  all describe how things reach the authority; each would be rewritten if the
  authority or the tenancy boundary moved.
- **12 before 8 and 9.** The migration constrains the target model — if something in
  today's `.omni/` cannot be carried across, the target changes, and behaviour
  written against the old target is wasted.
- **19 with 9.** Crash behaviour and restore behaviour answer the same operator
  question and share the failure model; splitting them across milestones would let
  one contradict the other.
- **11, 17 and 23 last.** Lifecycle policy, the decision register and the exit path
  are the only work that is cheap to redo, so they absorb whatever the earlier
  milestones changed.

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-08-29 | Initial plan: 5 milestones over 17 DoD conditions. | — |
| 2026-08-29 | Realigned to 23 conditions after cross-vendor review: added 18 and 20 to M2, 22 to M3, 19 and 21 to M4, 23 to M5; updated exit criteria, demonstrables and dependency order (18 before 9/16/21; 20 before 11). | `DOD-RECONCILIATION.md`; findings F76, F25–F28, F31, F32. |
