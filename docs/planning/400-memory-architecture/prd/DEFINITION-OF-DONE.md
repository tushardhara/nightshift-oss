# Definition of Done — Nightshift State & Memory Architecture

Version 2 (2026-08-29), after cross-vendor hostile review. 23 conditions. This is
the project's only fixed point: the project is finished when all 23 are met, and
not before.

## How to read this document

**The deliverable is a design, not a running system** (assumption DA1). Every
condition is settled by reading a named file in this repository or running a stated
read-only command against files in this repository. No condition requires a
deployed system, a benchmark, or an expert aesthetic judgement.

**Paths and verifier names are project convention, not owner requirements**
(DA8). They exist so that "met" has an address. Any of them may be renamed by
agreement without changing what is promised; what may not change without a
changelog entry is the substance of a condition.

**Closed sets.** Where a condition says "the closed list", the list is reproduced
in this document. A stranger never has to consult a file outside the repository, or
guess at membership, to run a check.

**Quality bar.** A required statement is judged present and specific enough to act
on, or not present. There is no prose, diagram or word-count bar. Once a
condition's check passes, that document is done; do not gild.

**Verifiers** are the person or role who runs the check: `staff` (technical
reviewer, never the author of the file under check), `qa` (owner of
`docs/tests/cases.md`), `pm` (author of this document), `program` (reconciler).
Where two are named, both run the check; a disagreement means the condition's
wording failed and the wording is fixed, not the verdict argued.

## Assumptions this document depends on

Reproduced here so no condition depends on a file outside the DoD. Each is a
choice, not a fact from the owner.

- **DA1** — The deliverable of this project is a design; the brief's hard
  constraints forbid implementation and external activity.
- **DA2** — The sixteen state classes in condition 2 are a **minimum**, not a
  maximum; the design may add classes (tenant, key, publication record, retention
  job, repository registration) and must declare the list complete.
- **DA3** — One durable authority for control state does **not** mean one physical
  component per architectural role; a role may be filled by a named composite.
- **DA4** — Secret redaction is a detection model with residual risk, not a perfect
  guarantee; the design states the model and the residual risk.
- **DA5** — A migration may have zero, one, or several irreversible boundaries;
  each must be named.
- **DA6** — Publication approval is given by a named accountable identity distinct
  from the process performing the write; a policy-backed review satisfies this if
  the accountable identity is recorded.
- **DA7** — "Safe migration" is defined here as rehearsable, reconcilable and
  recoverable. This definition is proposed by `pm`; the owner has not confirmed it.
- **DA8** — File paths and verifier roles are project conventions (means), not
  owner outcomes.
- **DA9** — "Offline" means no outbound network dependency at runtime.
- **DA10** — 100× is defined per workload axis in the capacity model, not assumed
  to be uniform growth.

## The artifact register

The closed set of files this project delivers. Conditions that say "any
architecture document" mean exactly this set. `docs/design/SYSTEM.md`,
`docs/conventions.md` and `docs/owners.md` are pre-existing scaffolds this project
does not touch.

| # | Path | Conditions it settles |
|---|---|---|
| R1 | `docs/prd/PROJECT.md` | context (no condition) |
| R2 | `docs/prd/DEFINITION-OF-DONE.md` | this document |
| R3 | `docs/prd/MILESTONES.md` | sequencing (no condition) |
| R4 | `docs/prd/DOD-CRITIQUE-codex.md` · `docs/prd/DOD-RECONCILIATION.md` | review record |
| R5 | `docs/architecture/STATE-MODEL.md` | 2, 3, 18 |
| R6 | `docs/architecture/CAPACITY.md` | 14 |
| R7 | `docs/architecture/SCALE-LADDER.md` | 15 |
| R8 | `docs/architecture/COMPONENT-EVALUATION.md` | 13 |
| R9 | `docs/architecture/REPO-FOOTPRINT.md` | 4, 5 |
| R10 | `docs/architecture/PROVENANCE.md` | 6 |
| R11 | `docs/architecture/MEMORY-CONTRACT.md` | 7 |
| R12 | `docs/architecture/DEPLOYMENT-CONTRACT.md` | 8 |
| R13 | `docs/architecture/CONCURRENCY.md` | 9 |
| R14 | `docs/architecture/SECURITY-AND-TENANCY.md` | 10, 11 |
| R15 | `docs/architecture/MIGRATION.md` | 12 |
| R16 | `docs/architecture/OPERATIONS.md` | 19, 21 |
| R17 | `docs/architecture/ACCESS-CONTROL.md` | 20 |
| R18 | `docs/architecture/EVOLUTION.md` | 22 |
| R19 | `docs/architecture/OFFBOARDING.md` | 23 |
| R20 | `docs/decisions/REGISTER.md` · `docs/decisions/ADR-*.md` | 17 |
| R21 | `docs/tests/cases.md` | 16 |

## What this document cannot settle

Stated plainly, because a check that always passes is worse than an admitted limit.
These remain owner constraints honoured by working agreement and are recorded as
out of scope in `PROJECT.md`; no condition below pretends to verify them:

- that no deployment, purchase, credential change, external publication, GitHub or
  Kanban activity, heartbeat, automation, crew chartering or worker dispatch
  occurred during this project — no local artifact distinguishes silence from
  compliance;
- any runtime property: latency, throughput, retrieval relevance, or whether the
  design would in fact work when built;
- whether the architect's numbers are true of the world — only that they are
  present, sourced, internally consistent and carried through the arithmetic.

---

## The conditions

### 1. Everything this project produced is a design document, and the change set is bounded.
**Check.** `docs/prd/PROJECT.md` records a **baseline commit SHA** taken before this
project began. Run `git diff --name-only <baseline>` (read-only): every path
returned appears in the artifact register above, and no path outside `docs/` is
returned. Every returned file is Markdown and contains no fenced block in an
implementation language, no DDL, and no runnable script. The register contains no
migration-execution log, deployment record, or scheduled-task definition.
**Verifier.** `program`.

### 2. Every class of Nightshift state is inventoried, tiered by a written test, and structurally separated.
**Check.** `docs/architecture/STATE-MODEL.md` `## Entity inventory` contains one
row for **at minimum** these sixteen classes (DA2): tasks; dependency edges; task
state; worker runs; worker slots; claims; leases; decisions; reports; handoffs;
evidence; transcripts; provenance records; semantic/retrieval index; audit history;
approved customer-owned documents — plus every additional class the design
introduces, and a closing sentence declaring the list complete. Each row has these
non-empty columns:
(a) **Tier**, exactly one of `control-authority`, `narrative-memory`,
`customer-document`, assigned by these three tests, which the document reproduces:
*control-authority* = a fact a scheduler or worker reads to decide what to do next,
or that changes when work is claimed or completed; *narrative-memory* = content
whose loss changes no scheduling decision and no state transition, only explanation
or retrieval quality; *customer-document* = content intended to live in the
customer repository after review. A class satisfying two tests is split into two
rows;
(b) **Key identifier** and (c) **Relations** — the classes it references (F22);
(d) **Sensitivity**, one of `large`, `sensitive`, `both`, `neither` — this column is
the input to conditions 10, 11 and 23;
(e) **Failure behaviour** — the outcome for this class under each of the five
closed failure events: worker process crash; host loss; primary store loss; operator
error (accidental deletion); loss of network (production partition / local offline).
The document also contains `## Separation` — a matrix of which tier may write to
which store, and the prohibited flows (F11); a tier boundary crossed only by
convention fails this condition.
**Verifier.** `pm` (all sixteen present, columns filled, completeness declared) and
`staff` (tier tests applied consistently — a row whose tier contradicts the written
test is a fail, and this is a mechanical comparison, not an opinion).

### 3. One durable authority for control state is named as a component, with its consistency, recovery and precedence rules.
**Check.** `docs/architecture/STATE-MODEL.md` `## Authority` states:
(a) exactly one authority for control state, named as the **component selected in
condition 13**, not only as a role label;
(b) every row tiered `control-authority` in condition 2 names that same authority;
(c) the **write path** and the **read path** by which control state is changed and
obtained, given as an operation list with inputs and outputs;
(d) the **consistency rule** — what a reader is guaranteed to see after a write
commits, and what may be stale;
(e) the **transaction/atomicity boundary** — what set of changes commits together;
(f) the **recovery source** — after the five failure events of condition 2, what
control state is restored from (cross-referenced to condition 19);
(g) the **precedence rule** in one sentence: what wins when the authority and any
file on disk disagree, plus the explicit statement that no file inside the customer
repository is ever authoritative for control state;
(h) the statement that the semantic/retrieval store of condition 7 is not this
authority.
**Verifier.** `staff`.

### 4. A zero-Nightshift-file repository mode exists, is the default, and its footprint claim has a pass predicate.
**Check.** `docs/architecture/REPO-FOOTPRINT.md` contains:
(a) `## Footprint modes` — one row per mode, including a mode asserting `0 files
created`, `0 files modified`, `0 refs created`, `0 ignore-file entries`,
`0 hook or config changes` in the customer repository;
(b) a statement that this mode is the **default for a customer installation**, or a
deviation recorded as a decision ID in `docs/decisions/REGISTER.md` (F23);
(c) for each of these seven things Nightshift writes today — `.omni/`, generated
`docs/`, worktrees, transcripts, reports, handoffs, registries/control state — a
named destination outside the customer repository in that mode;
(d) `## Footprint check` — a before/after manifest definition naming its scopes
(tracked files, untracked files, ignored files, refs, hooks, config, submodules),
the exact command, its **expected output**, and the **pass predicate** including
exit code. "The command looks exact" is not the test; the predicate is;
(e) `## Boundary with publication` — an explicit statement of whether an approved
publication (condition 5) is inside or outside the footprint claim. The permitted
form is: the claim holds absent an approved publication, and a publication is a
separate audited event, not a footprint violation (F38);
(f) `## Removal` — a fixed inventory of what remains in the customer repository
after Nightshift is removed, what the customer keeps and in what format, and what
becomes unrecoverable, each as an enumerated list rather than prose.
**Verifier.** `staff` (a–c, e–f) and `qa` (d resolves to pass/fail with no reading
between lines).

### 5. A file reaches the customer repository only through one enforcement boundary, bound to approved bytes.
**Check.** `docs/architecture/REPO-FOOTPRINT.md` `## Publication gate` states:
(a) exactly one **enforcement boundary** through which any write to a customer-owned
path passes; multiple callers may use it (F60);
(b) the approving identity per DA6, and that it is distinct from the process
performing the write;
(c) the **binding between approval and content**: the approved content digest, the
exact diff, the base commit of the target repository, and a single-use
authorization; plus the **staleness rule** — what happens when the target moved
between approval and write (F6);
(d) the minimum precondition set, which must include at least: target path
normalized and inside the permitted path set; no symlink traversal; the
overwrite/conflict rule when the path exists; the repository-cleanliness
precondition (F29);
(e) **atomicity** — what is guaranteed if the write is interrupted part-way — and
the **rollback** procedure for an approved publication later found bad;
(f) the publication record fields, including target path, source artifact identity,
approving identity, timestamp, content digest, and the exact Git SHA of condition 6;
(g) the unconditional statement that no other path may write to customer-owned
paths, and the stated outcome when one attempts to. A written exception fails this
condition.
**Verifier.** `staff`. `pm` confirms (g) is unconditional.

### 6. Every artifact and every decision is bound to a canonical repository identity and an exact source version.
**Check.** `docs/architecture/PROVENANCE.md` contains:
(a) the **canonical repository identifier** — its format, and what makes it stable
across clones, forks, renames, remote changes and mirrors (F30);
(b) the binding fields for an artifact, including full commit SHA, and where they
are stored;
(c) a table with one row per class in condition 2's inventory, marked
provenance-bound `yes`. **Zero `no` rows are permitted for artifact and decision
classes.** The only permitted exemption is a derived index that can be rebuilt, and
only if its row names the rebuild source (F5, F37, F47);
(d) the **dirty-tree rule**: what content digest identifies uncommitted bytes
sufficiently to reproduce the exact source seen;
(e) the behaviour when history is rewritten and a recorded SHA no longer resolves;
(f) the **bidirectional lookup**, stated as two queries with their exact inputs and
outputs: artifact → source version, and source version → artifacts produced from it.
**Verifier.** `staff`. `pm` checks (c) has the same rows as condition 2 and no `no`.

### 7. Semantic memory improves a named retrieval use case and cannot decide scheduling or override authoritative state.
**Check.** `docs/architecture/MEMORY-CONTRACT.md` contains:
(a) `## Use case` — at least one named retrieval use case memory serves, the input
it receives, the output it returns, and the **fallback it replaces** (for example
keyword or recency retrieval) so that "improves" has a referent (F4). No measured
relevance figure is required or accepted here — nothing runs in this project;
(b) `## Allowed influence` — the closed list of operations memory may affect;
ranking, filtering and suggestion are permitted (F41, F61);
(c) `## Prohibitions` — memory may not set or change task state, decide scheduling
or work ordering, grant or release claims or leases, or override the authority of
condition 3; and memory output may never be the sole input to a control-state write;
(d) `## Enforcement` — for each prohibition, the dependency or dataflow rule that
makes the forbidden write **unreachable** (which component cannot call which). A
prohibition enforced "by convention" or "by review" fails this condition (F48);
(e) `## Degradation` — the stated behaviour when the index is absent, stale, or
contradicts authoritative state, including that scheduling and completion are
unaffected, and a worked narrative example of the contradiction case.
**Verifier.** `staff` and `qa`.

### 8. Local/offline and multi-tenant production implement one contract across ten named dimensions.
**Check.** `docs/architecture/DEPLOYMENT-CONTRACT.md` contains:
(a) `## Contract` — numbered behavioural statements covering, at minimum, one row
per dimension of this closed list (F49): control-state authority and precedence;
concurrency, claims and leases; provenance capture; publication gate; repository
footprint; memory influence and degradation; tenant isolation and access control;
encryption and redaction; retention, export and deletion; failure detection and
recovery;
(b) `## Offline definition` — offline means no outbound network dependency at
runtime (DA9); the list of capabilities that degrade offline and how; and a stated
**simplicity bound**: the number of processes or services required to run the local
deployment (F9);
(c) `## Permitted divergences` — every difference **observable through a dimension
in (a)**, each with its reason and why no contract statement changes. Differences
not observable through those dimensions are out of scope and do not fail this
condition (F66);
(d) the rule that any dimension-observable behaviour absent from (c) must be
identical in both modes.
**Verifier.** `qa` and `staff`.

### 9. Overlapping runs and crashed workers cannot violate an invariant.
**Check.** `docs/architecture/CONCURRENCY.md` contains:
(a) the claim/lease mechanism: acquisition, renewal, expiry, and what makes a claim
unique;
(b) the fencing rule — what prevents a worker resuming after crash, pause or lease
expiry from committing a write against state it no longer owns;
(c) the idempotency rule for externally-triggered mutations, stating exactly what
constitutes the key;
(d) `## Scenarios` — the four scenarios below, each specified as: event sequence,
permitted state transition (referencing condition 18), **committed effects**,
**rejected effects**, retry result, and the observable final records (F50):
two overlapping runs contending for the same unit of work; worker crash mid-task
with an unexpired lease; worker crash part-way through a publication; lease expiry
while the worker is alive and still writing;
(e) each scenario names the invariants from condition 18 it preserves. A scenario
ending in "one writer silently overwrites the other" fails this condition unless an
invariant explicitly permits it.
**Verifier.** `staff` and `qa`.

### 10. Tenant isolation has a minimum invariant and encryption covers every class classified sensitive or large.
**Check.** `docs/architecture/SECURITY-AND-TENANCY.md` contains:
(a) `## Isolation` — the invariant stated in this form: *no request executing in
tenant A's context can read or write data belonging to tenant B*, plus what enforces
it at each store or interface (cross-referenced to condition 20), and the stated
blast radius if enforcement fails at one layer;
(b) `## Encryption` — a coverage table over condition 2's inventory in which every
class marked `sensitive`, `large` or `both` is inside the encrypted set (F3, F51);
the boundary at which encryption is applied for **at rest, in transit, and in
backups**; key custody (per-tenant or shared, and who holds keys); the rotation
procedure and the exact outcome for data encrypted under a retired key; and the
reversal trigger — the observable condition that would revisit this choice;
(c) the enumerated list of what is not encrypted, each with the property that makes
it safe to leave out (a class marked sensitive or large may not appear here).
**Verifier.** `staff`. `program` checks (b)'s reversal trigger appears in
condition 15's reversal list.

### 11. Redaction, retention, deletion and audit each have an actor, a mechanism, and an exact proof step.
**Check.** `docs/architecture/SECURITY-AND-TENANCY.md` contains four sections, each
naming **who invokes it**, **the mechanism**, and **the exact query, manifest or
count that proves it worked** (F52). A step reading "the operator confirms" fails.
(a) `## Redaction` — the definition of a secret; where redaction happens; the
distinction between an **in-flight agent transcript** and a **stored transcript**,
and the point between them at which redaction occurs (F40); who may see unredacted
content; the behaviour when detection fails, and the residual-risk statement
required by DA4 — an absolute "never" without a mechanism fails (F71);
(b) `## Retention` — every class from condition 2 with a duration **or** a named
policy source; legal or investigative holds and their effect; the roles permitted to
change policy, enumerated (F72);
(c) `## Deletion` — the coverage matrix over every class **and every copy**
including backups and derived indexes; what tombstones or references survive and
why; the proof query and its expected result;
(d) `## Audit` — at minimum these ten events, each with its recorded fields: tenant
created; tenant deleted; publication approved; publication written; key rotated;
export produced; deletion executed; retention policy changed; access granted or
revoked; authority write conflict rejected. Plus: mutability and how tampering or
**gaps** are detected; the clock source and how ambiguous ordering is resolved; how
an actor is bound to an event; and the behaviour when an audit write itself fails
(F33).
**Verifier.** `staff` and `pm` (every class from condition 2 appears in (b) and (c)).

### 12. The migration from `.omni/` is rehearsable, reconcilable and recoverable, with its reversibility boundaries named.
**Check.** `docs/architecture/MIGRATION.md` contains:
(a) `## Legacy inventory` — everything today's `.omni/` and generated-document model
holds, enumerated from a named source (a directory listing of a real installation or
a stated documented layout), each item mapped to a destination class in condition 2,
each with a **preservation or loss invariant** (F53);
(b) the ordered phases; for **each phase**: which component holds authority during
it, whether writes are frozen, and the recovery action if that phase fails
part-way (F7);
(c) a **dry run** that changes nothing, and the exact artifact it produces
(a rehearsal report: what would change, per item);
(d) the **reversibility boundaries** — zero, one, or several, each named, with what
makes it irreversible; and for anything past a boundary, the **forward-recovery**
path, since rollback is no longer available (F39, F74, DA5);
(e) what is knowingly not carried over, and why;
(f) the **reconciliation formula** — the exact counts, digests or comparison that
proves completeness, written so two people compute the same number;
(g) the stated result of running the migration twice (DA7);
(h) an explicit line stating this migration has **not been executed** in this
project, and the artifact register contains no execution log (F17).
**Verifier.** `staff`. `pm` checks (a) against condition 2 for orphans.

### 13. Candidates are compared on one matrix, and each role's selection is the cheapest option meeting its required guarantees.
**Check.** `docs/architecture/COMPONENT-EVALUATION.md` contains:
(a) `## Roles` — the role list, using the same role names as
`docs/architecture/STATE-MODEL.md`, each with its **required guarantees**
(durability, consistency, concurrency primitives, isolation support, offline
capability, retrieval capability — as applicable) and its **disqualifiers**;
(b) `## Comparison` — one matrix with a row per candidate named in the brief
(PostgreSQL, SQLite, object storage, Git, Redis, pgvector, a dedicated vector
database, Mem0, temporal knowledge graphs) and a column per criterion from (a),
filled for every cell, with version or feature assumptions stated (F10, F54);
(c) `## Selection` — every role from (a) appears exactly once with one selected
component **or one named composite set** (DA3, F63), the day-one choice flagged
`present` versus `candidate-for-later`;
(d) the **cheapest-correct statement** per role: no rejected candidate both meets
the role's required guarantees and costs less at day-one volume, or the reason it
was still not chosen (F8). A rejection may be qualitative (for example: cannot run
offline, cannot isolate tenants) — a capacity number is not required for a
disqualifier-based rejection (F63);
(e) no role has two live components; two live components for one role fails this
condition.
**Verifier.** `staff`. `pm` checks all nine candidates and all roles appear.

### 14. Capacity is arithmetic over named axes, with units, sources and cost basis.
**Check.** `docs/architecture/CAPACITY.md` contains:
(a) a table with one row per axis of this closed list, each with a **unit**, a
**day-one** value and a **100×** value: definition of the unit of work; runs/day;
tasks/day; artifacts/day and bytes per artifact; transcript bytes/day; control-state
writes/day and rows retained; retrieval queries/day; concurrent runs and workers;
peak factor over the average hour; retention horizon;
(b) for every day-one value: its **source** (measurement, owner statement, or
estimate) and a confidence marker — an unsourced number fails (F73);
(c) a stated **100× model**: which axes scale together and which do not; uniform
100× is permitted only if written as an explicit assumption (DA10);
(d) the four qualitative lines non-empty: read:write ratio; reads that tolerate
staleness; latency a user notices; durability and consistency the domain requires;
(e) `## The arithmetic` — the working from unit of work to storage and throughput,
such that a reader can recompute each derived number from (a);
(f) `## Budget` — monthly operational cost at day-one and at 100×, in a stated
**currency** with a stated **pricing date**, itemized over this closed cost list:
compute, primary store, object storage, index/retrieval, backup, egress,
managed-service fees; plus the assumed infrastructure ceiling (F14, F55).
**Verifier.** `staff` (each derived number recomputes) and `program` (no empty cell,
no unsourced number).

### 15. Every laddered component has a numeric trigger, a numeric reversal predicate, and a bounded interface.
**Check.** `docs/architecture/SCALE-LADDER.md` contains:
(a) a closed component/role register matching condition 13(c), each row flagged
`present` or `future` (F56);
(b) for every row, a **Switch when** cell of the form *metric + threshold + where
the metric is measured* (for example "p95 authority write latency > 300 ms measured
at the authority interface"). A date, an adjective, or a metric with no measurement
source fails;
(c) `## Reversal triggers` — for every `present` component that adds operational
surface, a numeric predicate under which it would be removed or downgraded;
(d) `## Switchability contract` — a port table: one row per replaceable role, its
operation list, an explicit **maximum operation count** for the port, the weakest
guarantee callers may depend on, at least one named alternative implementation, and
the stated switch cost in steps. Ports whose signatures name vendor-specific types
fail the narrowness test (F15);
(e) no `present` component exists that is unjustified by a day-one number in
`CAPACITY.md`; justification by a future trigger alone marks it `future`, not
`present` (F15, F56).
**Verifier.** `staff` and `program`.

### 16. Every normative invariant has a written conformance case.
**Check.** `docs/tests/cases.md` has one row per case below, each with
preconditions, **deterministic steps**, a **deterministic expected result** (a
specific final state or a specific document query and its answer), level, and
`automated: no`. Status must be `specified` for every row; `blocked`, `draft` and
empty are not permitted values in this project (F57):
(a) publication only through the gate — plus the negative case: a write attempt
outside the gate is refused, and the stale-approval case of condition 5(c);
(b) a run in zero-file mode satisfies condition 4(d)'s pass predicate;
(c) memory unavailable: scheduling and completion unaffected;
(d) memory contradicts authoritative state: authority wins;
(e) the four concurrency scenarios of condition 9(d);
(f) migration dry run changes nothing, and produces the rehearsal artifact;
(g) migration run twice produces the stated result;
(h) tenant deletion proof query, per condition 11(c);
(i) artifact → source version and back, plus the dirty-tree case;
(j) a secret present in an in-flight agent transcript does not appear in any stored
transcript, report, log or audit record, and the detection-failure behaviour of
condition 11(a) is exercised;
(k) restore from backup meets the stated RPO/RTO of condition 19;
(l) tenant export completeness and integrity, per condition 23.
Every numbered statement in condition 8(a) and every invariant in condition 18(c) is
referenced by at least one case, and each case states whether it holds in both
deployments or names the one it is specific to.
**Verifier.** `qa`. `pm` checks (a)–(l) present and all statuses `specified`.

### 17. Every decision in the closed register has an ADR with cost, reversibility and a reversal trigger.
**Check.** `docs/decisions/REGISTER.md` exists and lists every decision with a
stable ID, a one-line scope, and a status from the closed vocabulary
`proposed | accepted | superseded`. Every architecture document in the register
cross-links the decision IDs it depends on. For each register entry there is a
matching `docs/decisions/ADR-*.md` following `ADR-TEMPLATE.md` with **no template
placeholder text remaining** and these fields non-empty: Context; Options considered
(at least two, each with why it won or lost); Decision; Consequences; Cost (day-one
and 100×); One-way door? (yes/no — this document does not prejudge which decisions
are irreversible, F65); Scale tier; Reversal trigger. No two `accepted` ADRs decide
the same register ID differently. Completeness is claimed **only** against the
register (F58).
**Verifier.** `staff` and `program`.

### 18. The authoritative state has a written state machine and an invariant catalogue.
**Check.** `docs/architecture/STATE-MODEL.md` `## State machines and invariants`
contains:
(a) for every class tiered `control-authority` in condition 2: its states, the
allowed transitions, the precondition of each transition, its terminal states, and
its uniqueness rules (what may exist at most once);
(b) the actor permitted to perform each transition (cross-referenced to
condition 20);
(c) a numbered **invariant catalogue** — statements every writer must preserve,
each written as a predicate a reader could evaluate against a state dump (for
example: "at most one unexpired lease exists per unit of work"; "no task is in a
terminal state with an unreleased claim");
(d) for each invariant, what happens when a write would violate it — rejected,
retried, or repaired (cross-referenced to condition 21).
This is the catalogue conditions 9 and 16 are judged against; a design with no
invariant satisfies neither (F25, F12, F76).
**Verifier.** `staff` and `qa`.

### 19. Durability is specified: failure model, backup coverage, restore procedure, and stated RPO/RTO.
**Check.** `docs/architecture/OPERATIONS.md` contains:
(a) `## Failure model` — the five closed failure events of condition 2(e), each with
its assumed likelihood class and its blast radius;
(b) `## Backup coverage` — a table over every class in condition 2's inventory:
whether it is backed up, where, how often, how long backups are kept, and whether
backups are encrypted (cross-referenced to condition 10(b)) and tenant-separable
(cross-referenced to condition 11(c));
(c) `## Restore` — the ordered procedure to restore the control authority, the
artifact store, the audit history and the keys, including the order dependencies
between them, and what is authoritative during a partial restore;
(d) stated **RPO and RTO** numbers for both deployments, consistent with
condition 8's contract, with the day-one cost of achieving them appearing in
condition 14(f);
(e) the statement of what cannot be restored and what the operator does instead
(F26, F76).
**Verifier.** `staff`. `qa` confirms case 16(k) matches (d).

### 20. Identity, permission and revocation are specified for every actor and every privileged operation.
**Check.** `docs/architecture/ACCESS-CONTROL.md` contains:
(a) `## Actors` — every actor named anywhere in the artifact register (human roles,
workers, services, the customer, the operator), each with how its identity is
established and its tenant scope;
(b) `## Permissions` — a matrix of actor × privileged operation, where the operation
list includes at minimum: read control state; write control state; claim work;
approve a publication; perform a publication write; read unredacted content; produce
an export; execute a deletion; rotate a key; change retention policy; run a
migration phase;
(c) service-to-service authorization: how one component proves to another which
tenant it is acting for (this is the enforcement referenced by condition 10(a));
(d) **revocation** — how a credential or grant is withdrawn and what becomes
inaccessible when it is;
(e) **separation of duties** — the operations that may not be performed by the same
identity, which must at minimum include approving and performing a publication
(condition 5(b));
(f) the local/offline mapping: which actors collapse into one in the local
deployment, and what condition 8 divergence row records it (F27, F76).
**Verifier.** `staff`. `program` checks (e) against condition 5.

### 21. Every named failure signal has detection, an operator action, and a repair boundary.
**Check.** `docs/architecture/OPERATIONS.md` `## Detection and repair` contains one
row per signal of this closed list: stuck or expired lease with a live worker;
crashed worker holding an unreleased claim; missing or unreadable artifact;
retrieval index lag or rebuild required; audit write failure; key rotation failure;
storage exhaustion; divergence between the control authority and the artifact store.
Each row states: how it is detected (the observable), who or what acts, the repair
action, the invariant from condition 18 the repair must preserve, and **what the
repair may never do** (for example: never rewrite provenance, never delete audit
records, never resolve divergence by trusting a file in the customer repository)
(F28, F76).
**Verifier.** `staff`.

### 22. Persisted formats are versioned, and upgrade and downgrade behaviour is stated.
**Check.** `docs/architecture/EVOLUTION.md` contains:
(a) every persisted format or interface the design introduces — control-state
records, artifact envelopes, export format, audit record, retrieval index — each
with its versioning rule and where the version is recorded;
(b) the compatibility policy: which direction is guaranteed (forward, backward, or
both) and for how many versions;
(c) the behaviour during a rolling upgrade when two versions coexist, per deployment
mode;
(d) the downgrade behaviour, or the explicit statement that downgrade is not
supported and what an operator does instead;
(e) how a future format migration differs from the one-time `.omni/` migration of
condition 12, and which of condition 12's mechanisms it reuses (F31).
**Verifier.** `staff`.

### 23. A tenant can leave: export is a usable exit path, and the operator's questions survive removal.
**Check.** `docs/architecture/OFFBOARDING.md` contains:
(a) `## Export` — the scope over condition 2's inventory (every class marked
included or excluded, with a reason for each exclusion), the format, its
**manifest** fields, the **integrity verification** a recipient can perform, and who
invokes it;
(b) the statement of how the export is read **without Nightshift** — the documented
semantics or the reader — and whether re-import is supported or explicitly not
(F32);
(c) `## Key handover` — what happens to encryption keys for exported data
(cross-referenced to condition 10(b));
(d) `## Decommission` — the ordered steps to remove a tenant, their relationship to
condition 11(c) deletion, and what the operator retains and why (for example legal
hold);
(e) `## Operator questions` — a table with one row for each of the brief's four
questions — what happened; why it happened; which exact source version was used;
what state is authoritative — each naming the artifact that answers it **after
overlapping runs, after a crash, after a migration, and after Nightshift is
removed**. A cell reading "not answerable after removal" is a permitted answer only
if (a)'s export scope makes it answerable, in which case that is the artifact
(F24, F76).
**Verifier.** `staff` and `pm`.

---

## Coverage — every promise in the brief maps to a condition

| Brief promise or constraint | Conditions |
|---|---|
| Zero-Nightshift-file repository mode, clean **by default** | 4, 16(b) |
| One clearly defined durable authority for control state | 2, 3, 18, 19 |
| Local and production implement the same behavioural contract | 8, 16 |
| Large/sensitive artifacts encrypted, tenant-isolated, exportable, deletable | 2(d), 10, 11, 23, 16(h), 16(l) |
| Semantic memory improves retrieval, cannot decide or override | 7, 16(c), 16(d) |
| Everything traceable to repository + exact Git SHA | 6, 16(i) |
| Publishing into the customer repo is explicit, reviewed, never a side effect | 5, 20(e), 16(a) |
| Safe migration path from `.omni/` and generated documents | 12, 22, 16(f), 16(g) |
| Cheapest correct components, measurable triggers for adding infrastructure | 13, 14, 15 |
| Local/offline **and** production multi-tenant deployments | 8, 20(f) |
| Compare the nine named candidates before selecting | 13 |
| Separate control facts / narrative memory / customer documents | 2, 3(h), 7 |
| Concurrency safety: overlapping runs, crashed workers | 9, 18, 21, 16(e) |
| Tenant isolation, encryption, redaction, retention, export, deletion, auditability | 10, 11, 20, 23 |
| Capacity assumptions, day-one and 100×, cost, scale triggers, reversal triggers | 14, 15, 17 |
| Narrow replaceable interfaces; no unjustified infrastructure | 15(d), 15(e) |
| Preserve tasks, edges, runs, slots, claims, leases, decisions, reports, handoffs, evidence, transcripts, provenance, retrieval, audit, approved documents | 2, 18, 19(b) |
| "What happened, why, which source version, what is authoritative after overlapping runs, crashes, migrations or removal" | 3(g), 6, 9, 11(d), 12(b), 19(c), 23(e) |
| Architecture and planning only; no code | 1 |
| Work entirely locally; no external services or GitHub activity | 1, and `## What this document cannot settle` |

## Negative conditions — what must not be true

No second authority for control state (3a–3b); no file in the customer repository
authoritative for control state (3g); no write to a customer-owned path outside the
enforcement boundary, with no stated exception (5g); no publication whose approved
bytes differ from the written bytes (5c); no approver identical to the writer
(20e); no class marked sensitive or large outside the encrypted set (10b–10c); no
`no` row in provenance for an artifact or decision class (6c); no memory-driven
scheduling, claim, lease or state write, and no prohibition enforced by convention
(7c–7d); no dimension-observable behavioural difference between deployments outside
the divergence table (8c–8d); no concurrency scenario ending in a silent overwrite
(9e); no absolute redaction guarantee without a mechanism and a residual-risk
statement (11a); no deletion that omits a copy or a backup (11c); no migration
without a rehearsal artifact and named reversibility boundaries (12c–12d); no
`present` component unjustified by a day-one number (15e); no role with two live
components (13e); no conformance case in a status other than `specified` (16); no
repair that rewrites provenance or deletes audit records (21).

## Changelog

| Date | Change | Evidence |
|---|---|---|
| 2026-08-29 | Created: 17 conditions from `.omni/brief.md`. | — |
| 2026-08-29 | v2 after cross-vendor hostile review (`DOD-CRITIQUE-codex.md`, 77 findings; 48 accepted, 27 accepted-in-part, 2 rejected — ruling per finding in `DOD-RECONCILIATION.md`): added conditions 18–23 (state invariants, durability/restore, access control, detection and repair, format evolution, offboarding); rewrote 1–17 to replace open predicates with closed lists and pass predicates; closed the provenance exemption; removed unverifiable external-activity checks into `## What this document cannot settle`; added the assumption register, artifact register and coverage of "clean by default". | 77-finding critique; strongest finding F76 (a design could pass every v1 check and still leave an operator unable to determine or recover correct state). |
