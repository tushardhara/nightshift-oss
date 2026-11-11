# Hostile Review — Definition of Done

## Review boundary

This review uses only `.omni/brief.md` and `docs/prd/DEFINITION-OF-DONE.md`. It judges a design deliverable, not a running implementation. Requiring repository documents as evidence is valid; allowing a document to pass merely because it contains unconstrained prose is not.

## 1. Coverage

### “What would make you say this is working?”

| Brief text | Draft mapping | Verdict |
|---|---|---|
| “A customer can choose a zero-Nightshift-file repository mode.” | 4, 16(b) | Mapped. The mode and a prospective conformance case are explicitly required. |
| “Operational control state has one clearly defined durable authority.” | 2, 3 | **F1 — Partial.** The draft requires one *role* to be named, but it never defines what makes that role durable, what store or consistency boundary realizes it, or the failure conditions it must survive. A role label can satisfy the check while leaving the actual authority indeterminate. |
| “Local and production modes implement the same behavioural contract.” | 8, 16 | **F2 — Partial.** Condition 8 requires numbered statements but supplies no complete set of behavioral dimensions. A tiny contract can pass. “Every difference” is then delegated to reviewer judgment, so the sameness promise is not actually closed. |
| “Large and sensitive artifacts are encrypted, tenant-isolated, exportable and deletable.” | 10, 11, 16(h) | **F3 — Partial.** Condition 10 only asks the design to say which inventory classes are encrypted and what is not; it never requires all large or sensitive artifacts to be inside the encrypted set. Condition 11 lets the author choose export and deletion scope without requiring those same artifacts to be included. |
| “Semantic memory improves retrieval but cannot decide scheduling or override authoritative state.” | 7, 16(c–d) | **F4 — Partial.** The prohibitions are covered. “Improves retrieval” is not: there is no required retrieval use case, relevance objective, fallback equivalence, freshness rule, or comparison showing improvement. A design in which semantic memory is unused can pass 7(a). |
| “Every artifact and decision can be traced to the repository and exact Git SHA that produced it.” | 6, 16(i) | **F5 — Partial, with a direct escape hatch.** Condition 6(b) expressly allows any inventory class to be marked `no` if a reason is written. That converts “every artifact and decision” into “any exceptions the author can explain.” |
| “Publishing an ADR, PRD, release note or other document into the customer repository is an explicit reviewed operation, never an accidental side effect.” | 5, 16(a) | **F6 — Partial.** A human approval and publication record are required, but the check never says the approver reviews the exact bytes or diff that will be written. It has no binding between approval and content, no stale-approval/TOCTOU rule, and no target-path conflict rule. An approval of artifact A followed by publication of changed artifact B can satisfy the written fields. |
| “The design includes a safe migration path from today’s `.omni/` and generated-document model.” | 12, 16(f–g) | **F7 — Partial.** Ordered prose, a dry-run description, rollback prose, and a counts-or-hashes check do not establish safety. The condition omits backup/restore, live-write handling, authority during cutover, partial-failure recovery, preservation invariants, and a required rehearsal result. |
| “The architecture starts with the cheapest correct components and names measurable triggers for adding infrastructure.” | 13–15 | **F8 — Partial.** Numeric citation and a trigger do not prove that the selected day-one component is correct or cheapest. There is no common comparison basis, cost perimeter, required alternative, or rule that the selected-now component must be the least costly option satisfying stated guarantees. |

### Hard constraints

| Brief text | Draft mapping | Verdict |
|---|---|---|
| “Support a simple local/offline deployment and a production multi-tenant deployment.” | 8, 10 | **F9 — Partial.** The production tenancy material is substantial, but neither condition defines “offline” as having no runtime network dependency, identifies a local storage/backup shape, or sets a simplicity bound. Merely describing two modes does not establish that the local one is simple or offline-capable. |
| “Compare PostgreSQL, SQLite, object storage, Git, Redis, pgvector or a dedicated vector database, Mem0, and temporal knowledge graphs before selecting components.” | 13 | **F10 — Partial.** Nine isolated sections with a verdict and one cited number are not necessarily a comparison. The check requires no shared criteria, side-by-side trade-offs, fit against required guarantees, version/feature assumptions, or explanation of interactions among candidates. |
| “Separate authoritative control facts from narrative/conversational memory and from approved customer-owned documents.” | 2, 3, 7 | **F11 — Partial.** Assigning one of three tier strings to table rows does not require separate storage boundaries, write paths, permissions, APIs, or prohibited flows. Memory-to-control prohibitions cover one direction, but the three-way separation itself can remain taxonomy only. |
| “Preserve concurrency safety for overlapping Nightshift runs and crashed workers.” | 9, 16(e) | **F12 — Partial.** The four scenarios need only end in a single outcome; the outcome is not required to preserve any invariant, prevent duplicate effects, or avoid lost work. The condition also omits the authoritative state-transition invariants against which “cannot corrupt” could be judged. |
| “Include tenant isolation, encryption, redaction, retention, export, deletion and auditability.” | 10, 11 | **F13 — Partial.** All nouns get headings, but core outcomes remain author-selected: the isolation guarantee, encrypted set, export scope, deletion scope, mandatory audit events, tamper resistance, backup-copy deletion, and redaction failure behavior are not fixed. |
| “Include capacity assumptions, day-one and 100-times scale estimates, operational cost, scale triggers and reversal triggers.” | 14, 15, 17 | **F14 — Partial.** The draft requires filled cells but does not specify the capacity dimensions, units, assumption sources, peak-versus-average treatment, cost inclusions, currency, pricing date, or uncertainty. A one-row table with arbitrary inputs passes the structural check. |
| “Prefer narrow replaceable interfaces and avoid infrastructure that current numbers do not justify.” | 15 | **F15 — Partial.** A port table does not bound interface width, dependencies, data-model leakage, or replacement effort. Condition 15(d) also allows infrastructure to be justified by a future trigger, which is not the same as requiring current numbers to justify infrastructure selected now. |
| “Architecture and planning only for this evaluation; do not implement code.” | 1 | Intent mapped, but condition 1’s verifier is invalid and overbroad; see F34 and F42. |
| “Work entirely locally. Do not create or update GitHub issues, projects, tickets, pull requests, branches, schedules or external services.” | 1 | **F16 — Partial.** Searching local files for recorded identifiers cannot prove that no external action occurred, and branches, tickets, schedules, projects, and services are not all checked. The check tests documentation residue, not the prohibited acts. |

### “Explicitly not this time”

| Brief text | Draft mapping | Verdict |
|---|---|---|
| “No implementation, migration execution or production cutover.” | 1 | **F17 — Partial.** Condition 1 attempts to detect source files, but has no check for migration execution or cutover. A prose claim that they did not occur would still not be evidence. |
| “No GitHub or Kanban activity.” | 1 | **F18 — Partial.** Condition 1 only rejects a file that *records* selected GitHub identifiers. It says nothing decidable about Kanban activity and cannot distinguish silence from compliance. |
| “No heartbeat or automation.” | None | **F19 — Unmapped.** No condition names heartbeat or automation activity. Condition 16 says test cases remain non-automated, which is narrower and does not prohibit other automation. |
| “No crew chartering or worker dispatch for building code.” | None | **F20 — Unmapped.** No condition addresses either activity. |
| “No purchase, deployment, credential change or external publication.” | 1 | **F21 — Partial.** Condition 1 checks whether a local document records these events; it cannot settle whether they happened. Credential change is not checked at all, and “purchase” and “external publication” are not defined. |

### Other promises in the owner’s input

- **F22 — Preservation is reduced to an inventory.** The brief says the system must preserve “tasks and dependency edges; task state; worker runs, slots, claims and leases; decisions; reports; handoffs; evidence; transcripts; exact Git SHA provenance; semantic retrieval; audit history; and deliberately approved documents.” Condition 2 proves only that sixteen names occur in a table. It does not require schemas, relations, lifecycle, fidelity, integrity, or retrieval of the preserved information.

- **F23 — “Clean by default” is not an acceptance condition.** The brief says the desired system “must keep customer repositories clean by default.” Condition 4 requires a zero-file *mode*, and condition 17 requires an ADR about the footprint default, but neither requires the selected default to be the zero-file mode.

- **F24 — The operator’s questions are not all answerable.** The owner needs to answer “what happened, why it happened, which exact source version was used, and what state is authoritative after overlapping runs, crashes, migrations or removal of Nightshift.” Audit event content is unconstrained, no causal link from a decision to its evidence is required, unsafe concurrency outcomes can pass, migration authority during cutover is absent, and removal does not say where the surviving authority is queried. The coverage table’s final row overclaims coverage.

## 2. Gaps

- **F25 — No authoritative state machine or invariant catalog.** An operator cannot resolve overlapping runs or crash recovery from a list of entities and four narratives. The design needs allowed state transitions, preconditions, terminal states, uniqueness rules, and invariants that all writers must preserve.

- **F26 — No backup, restore, or disaster-recovery design.** “Durable authority” is hollow without failure assumptions, backup coverage, restore procedure, RPO/RTO or equivalent targets, and a verification exercise. Day-one operators will ask how to recover the authority, objects, audit history, and keys after corruption or loss; no condition catches the omission.

- **F27 — No authentication and authorization contract.** The draft names actors and approvers but never requires identity establishment, role binding, tenant-scoped permissions, service-to-service authorization, revocation, or separation of duties. “Who may” in prose is not an access-control design.

- **F28 — No operational health or repair contract.** There is no required design for detecting stuck leases, failed queues, missing objects, index lag, audit-write failures, key-rotation failures, storage exhaustion, or cross-store inconsistency; nor are operator actions, alerts, or repair boundaries required.

- **F29 — Publication lacks content safety and recovery.** No condition requires path normalization, symlink defense, overwrite/conflict behavior, repository cleanliness preconditions, exact-diff approval, atomic write/commit behavior, or rollback of an approved but bad publication. These are day-one customer concerns at the only intentional repository write boundary.

- **F30 — Repository identity and dirty-source fidelity are underspecified.** A Git SHA alone does not identify a repository across clones, forks, renames, remotes, or rewritten history, and it cannot identify uncommitted bytes. The draft asks the author to state “a rule” but does not require a stable repository identifier or content/diff digest sufficient to reproduce the exact source.

- **F31 — No schema and compatibility evolution.** The migration condition covers the initial `.omni/` transition only. Nothing requires versioned schemas, forward/backward compatibility, rolling upgrade behavior in production, downgrade handling, or migrations of the new authority and artifact formats.

- **F32 — Export is not an exit path.** A customer can receive an author-selected export, but no condition requires a manifest, integrity verification, documented semantics, re-import/restore, or a durable reader after Nightshift removal. “Exportable” can therefore mean an opaque archive that is complete only by its own definition.

- **F33 — Auditability lacks evidentiary integrity.** Condition 11 asks whether records are mutable, but not how tampering, gaps, clock ambiguity, actor impersonation, failed audit writes, or deletion events are detected. An ordinary mutable table with the sentence “records are immutable” can pass the document check.

## 3. Contradictions

- **F34 — Condition 1 can make an existing code repository impossible to finish.** Its command lists the entire repository, then says “The result contains no file with extension `.py .ts .tsx .js .go .rs .java .rb .sh .sql .tf`.” The brief explicitly concerns an existing private codebase, where those files are expected. `find` also cannot tell which paths this design project added or modified. The first and second sentences do not combine into a coherent check.

- **F35 — The “no person’s opinion” claim contradicts the verifier rules.** The preamble says no condition requires “a person’s opinion,” while condition 2 requires tier assignments to be “defensible,” condition 8 asks QA to judge completeness, condition 13 asks for reasons, condition 14 asks whether arithmetic follows, and condition 17 asks whether options are “real” and decisions conflict. Two verifiers must “agree,” with a human role adjudicating disagreement. Those are opinion gates without rubrics.

- **F36 — “That condition alone” contradicts pervasive external dependencies.** The preamble says a stranger can decide from “this repository and this document,” but conditions depend on undefined roles, a template, “the template’s third column,” other conditions’ classifications, other documents’ completeness, milestone accounting, and invisible `PROJECT.md` assumptions 1, 7, 15, and 16. Several checks cannot be interpreted from the condition alone.

- **F37 — Condition 6 contradicts its own universal title and the brief.** “Every artifact and decision is bound” cannot coexist with a table that may mark any class `no` merely by supplying a reason. This is not a documented exception to a soft goal; it negates an explicit universal promise.

- **F38 — Zero-file mode and the publication rule have no boundary between them.** Condition 4 requires a mode with `0 files created` and `0 files modified` “after a Nightshift run.” Condition 5 says the gate is the single way *any* file may be written to a customer-owned path. The draft never says whether an approved publication exits zero-file mode, is excluded from a “run,” or makes the footprint assertion false.

- **F39 — Migration is called reversible while requiring an irreversible point.** Condition 12’s title says the migration “can be rehearsed and reversed,” but 12(d) requires “the single named point after which rollback is no longer possible.” That can be coherent only if the acceptance promise is explicitly limited to pre-commit rollback and a post-commit recovery/forward-fix path is required; neither qualification exists.

- **F40 — The credential case uses incompatible transcript meanings.** Condition 11 requires credentials and secrets to “never reach transcripts,” while 16(j) starts with “a credential appearing in an agent transcript.” If “agent transcript” and “stored transcript” are different objects, the model must define them; if not, the precondition already violates the expected result.

- **F41 — “Cannot decide anything” conflicts with an undefined permission to influence operations.** Condition 7’s title is absolute, while 7(a) asks for operations memory may influence. Retrieval ranking, filtering, recommendation, and work ordering all contain decisions. Without a defined distinction between advisory retrieval influence and decision authority, the title and permitted behavior can be interpreted to reject each other.

## 4. Verifiability

- **F42 — Condition 1 has no valid provenance or external-activity observable.** `find` cannot identify files “added or modified by this project,” and absence of a local written record cannot prove no deployment, publication, purchase, credential change, branch, schedule, ticket, or service action occurred. Make it verifiable with an explicit pre-project filesystem manifest/diff for repository changes and remove claims about external acts unless a locally captured, authoritative activity ledger exists. Also scope the extension test to changed files, not the customer codebase.

- **F43 — Condition 2 hides judgment in “defensible” and “what survives what.”** Neither phrase supplies an enumerated failure model or assignment rules. Require a fixed failure matrix, a tier definition with inclusion/exclusion tests, named authorities with precise semantics, and deterministic consistency checks across rows.

- **F44 — Condition 3 checks a label, not durable authority.** “Role,” “durable,” and “one read path” have no required syntax or semantics; a sentence naming `authority-service` and `read()` passes. Require the selected store/interface, transaction boundary, write/read consistency rule, recovery source, conflict precedence, and an exact lookup/API shape or schema reference.

- **F45 — Condition 4’s command need only be exact-looking.** There is no required baseline, handling of ignored/untracked files and Git refs, exit-code rule, or expected output. “What the customer keeps” and “what becomes unrecoverable” are also unbounded. Require a before/after manifest definition, explicit Git and filesystem scopes, a pass predicate, and a fixed removal output inventory.

- **F46 — Condition 5 does not make “reviewed” observable.** The required record fields do not bind approval to an immutable content digest, exact diff, destination repository state, or single-use authorization. “Pre-conditions that must all hold” has no minimum set. Require those bindings plus enumerated path, conflict, authorization, and atomicity checks with explicit failure outcomes.

- **F47 — Condition 6 permits undefined identity and lookup prose.** “Repository identity,” “bound,” “full commit SHA,” and “stated lookup” are not given formats or constraints, and every `no` can be justified arbitrarily. Require zero `no` rows for artifacts and decisions, a canonical repository-ID format, immutable artifact IDs/content digests, dirty-tree capture rules, and exact bidirectional query inputs/outputs.

- **F48 — Condition 7 leaves its key outcomes unfalsifiable.** “Improves retrieval,” “structurally enforced,” and “still schedules and completes work correctly” have no measurable predicate. Require a dependency/dataflow rule that makes forbidden writes unreachable, a complete allowed-operation list, explicit behavior under absent/stale/contradictory memory, and repository-contained design test fixtures with exact expected authority and scheduling traces.

- **F49 — Condition 8 contains an open-world completeness claim.** A stranger cannot prove a table contains “every difference” between deployments, particularly when the contract itself may omit behaviors. Define a mandatory contract matrix—storage semantics, consistency, isolation, failure handling, security, provenance, publication, offline behavior, backup, and operations—and require one row per dimension in each mode.

- **F50 — Condition 9 never defines corruption or a safe outcome.** “Single stated authoritative outcome” accepts “both workers commit and one silently overwrites the other.” Require named invariants and, for every scenario, an exact event sequence, permitted state transition, committed effects, rejected effects, retry result, and observable final records.

- **F51 — Condition 10 allows an arbitrarily weak security design.** It requires a description of what a cross-tenant read must defeat, not a minimum isolation invariant; it lets the author choose an empty encrypted set; and “why acceptable” is subjective. Require tenant-scoped authorization at each store/interface, an explicit no-cross-tenant-read invariant, inventory-to-encryption coverage tied to large/sensitive classification, at-rest/in-transit/backup boundaries, and exact rotation/decryption outcomes.

- **F52 — Condition 11 accepts ceremonial verification steps.** “Run the deletion check” or “operator confirms export” is non-empty and passes. Export and deletion scope, mandatory audit events, secret definition/detection, retention semantics, backups, legal holds, and failure outcomes are unspecified. Require exact queries/manifests/counts, a fixed coverage matrix over every inventory class and copy, audit event minima, and deterministic pass predicates.

- **F53 — Condition 12 cannot establish “today’s” inventory or safety.** It provides no canonical source list against which completeness is checked, and “equivalent comparison” is undefined. Require a fixed legacy-source inventory, per-item preservation/loss invariants, authority and write-freeze rules for every phase, failure recovery at each step, exact reconciliation formulas, and an explicit dry-run/rehearsal artifact.

- **F54 — Condition 13 does not verify comparison quality or correctness.** Any verdict with any capacity number can pass, even where the number is irrelevant. “Role” is not defined consistently between state-model authority cells and component responsibilities. Require a common scored criteria matrix, mandatory guarantees per role, disqualifiers, total-cost inputs, explicit current selection versus future candidates, and a deterministic selection rule.

- **F55 — Condition 14 does not define the capacity table.** It references “the template’s third column” but specifies no required rows, units, workload axes, time horizon, pricing basis, or cost boundary. One filled row is enough. Require a fixed list of workload/storage dimensions, formulas and units, sources/confidence for assumptions, peak factors, currency/pricing date, and cost categories at both scales.

- **F56 — Condition 15 uses undefined sets and adjectives.** “Rows retained,” “adds operational surface,” “replaceable role,” “weakest guarantee,” “component appears,” and “cheapest-correct” are not mechanically resolvable. A numeric trigger can be irrelevant. Require a closed role/component inventory, a present-versus-future flag, trigger metrics tied to violated SLO/capacity bounds, a numeric reversal predicate, interface operation limits, and a replacement acceptance test.

- **F57 — Condition 16 verifies table shape, not conformance.** It does not require a passing status, precise steps, executable document query, fixed fixtures, or a one-to-one link to the invariant being tested. A row with status `blocked` and “review design” steps can pass. Require allowed status values for this design phase, exact repository-contained fixtures/inputs, deterministic expected traces or document queries, and coverage of each normative invariant rather than only contract statement IDs.

- **F58 — Condition 17 relies on an unknowable set of decisions.** A stranger cannot know whether every architecture decision marked—or simply deserving to be marked—irreversible has an ADR. “Real options,” “same question,” “contradicts,” and cost adequacy are judgment calls. Require a closed decision register with stable IDs referenced from architecture documents, exact status vocabulary, normalized decision scope, and mechanical cross-links; do not claim completeness beyond that register.

## 5. Scope Creep

- **F59 — The draft invents delivery governance.** The brief contains no sentence asking for `staff`, `qa`, `pm`, or `program` roles, dual approval, program adjudication, milestone ownership, a changelog gate, or a policy that late needs become follow-up projects. These rules may be useful project management, but they are not owner acceptance criteria and can block an otherwise complete design.

- **F60 — “Exactly one pathway” and “human approval” are stronger than the brief.** The brief asks for “an explicit reviewed operation.” It does not require one implementation entry point, one approving role, or a human rather than a policy-backed review. Multiple clients could safely call one enforcement boundary; the current wording may reject that design without owner authority.

- **F61 — The semantic-memory restrictions exceed the stated restriction.** The brief says semantic memory “cannot decide scheduling or override authoritative state.” Condition 7’s title says it “cannot decide anything,” 7(b) adds ordering, claims and leases, and condition 3 bars the same role from owning semantic memory. No brief sentence requires organizational owner separation or forbids memory from deciding retrieval ranking.

- **F62 — Migration mechanics are mandated as product facts.** The brief requests “a safe migration path,” not a mandatory single point of no return or a promise that the migration is idempotent when run twice. Those may be chosen safety properties, but the DOD should state them as proposed acceptance assumptions requiring owner confirmation, not silently redefine “safe.”

- **F63 — Component topology is overconstrained.** The brief asks to compare candidates, start cheaply, and prefer replaceable interfaces. It does not say every role has exactly one component, that every rejection must cite a capacity number, or that every port must have at least two adapters. Those constraints can reject a correct composite role or a qualitative security rejection.

- **F64 — Mandatory artifact taxonomy is not in the brief.** The owner did not require the exact file split, Markdown-only directories, `docs/tests/cases.md`, a particular capacity template, `ADR-TEMPLATE.md`, or accepted ADRs. File paths can make acceptance objective, but they are means, not owner outcomes, and should not be confused with coverage.

- **F65 — Four decisions are predeclared as one-way-door candidates without support.** The brief never calls the authority, tenancy, footprint default, or migration path irreversible. Requiring ADRs for them is reasonable; requiring them under a condition titled “Every one-way door” and demanding irreversibility metadata prejudges the architecture.

- **F66 — Enumerating every deployment difference is stronger than behavioral equivalence.** The brief requires the same behavioral contract, not identical implementation details unless expressly listed. Condition 8(c) can turn harmless undocumented implementation variation into failure even when observable behavior is identical.

## 6. Assumptions Stated as Facts

- **F67 — Four invisible assumptions are treated as authority.** The draft cites `PROJECT.md assumption 1`, “assumption 7,” “assumption 15,” and “assumption 16,” but does not reproduce an assumption register. Of these, the design-only premise is in the brief; rejecting candidates is a reasonable interpretation; correct scheduling without semantic memory follows from semantic non-authority. The no-automatic/human-approver rule is not in the brief. None should depend on a file outside the DOD if the DOD claims to be the fixed point.

- **F68 — Verifier identities and powers are invented and unstated as assumptions.** The owner never identifies staff, QA, PM, or program actors, says they exist, assigns document ownership, or grants `program` final authority. The draft states all of this as fact.

- **F69 — The draft assumes the sixteen listed nouns are an exhaustive entity model.** The brief says Nightshift must preserve those things; it does not say they are the only entities, that each appears exactly once, or that every one belongs to exactly one of three tiers. The assumption is not labeled, so a necessary join entity, publication record, tenant, repository, key, or retention job could make the design fail.

- **F70 — A single authority “role” and one component per role are assumed to be the right granularity.** The owner requires one durable authority for control facts, not one role label for all authority concerns or one physical component for every architectural role. The draft does not expose this modeling choice as an assumption.

- **F71 — Perfect secret exclusion is asserted without a secret model.** Condition 11 states credentials and secrets “never reach” several stores. The brief requires redaction, but does not define secrets, permit false negatives, or claim perfect detection. The draft neither labels the absolute guarantee as an assumption nor requires a mechanism capable of satisfying it.

- **F72 — Retention durations and a single changing role are assumed mandatory.** The owner asks to include retention, not that every entity tier has one fixed duration or exactly one role permitted to change it. Regulatory holds, tenant policy, and class-specific event-based retention are silently ruled into or out of the design.

- **F73 — The capacity shape is invented.** A “template’s third column,” uniform `100×-of-day-one`, one monthly cost, and an “assumed infrastructure ceiling” are treated as given facts. The owner asks for assumptions and 100-times estimates, but supplies no workload dimensions, synchronized 100× growth model, currency, ceiling, or cost perimeter. These must be explicit, reviewable assumptions.

- **F74 — The draft assumes every migration has one irreversible boundary.** A fully reversible migration or a migration with several independently irreversible conversions would fail 12(d), although the owner stated neither topology. This is an unmarked design assumption.

- **F75 — The draft assumes the existing repository can be judged by its final file list.** Condition 1 assumes source-file presence indicates project implementation and that lack of local records indicates lack of external activity. Neither follows from the brief, especially for an existing codebase. No baseline or activity evidence is declared.

## 7. Decidability

Strict answer: **none of the 17 conditions is decidable as written.** Each has observable document locations, but each also contains at least one open-ended predicate or invalid observable. A path is not enough if two strangers can inspect the same content and legitimately reach opposite verdicts.

| Condition | Decidable? | Why a stranger cannot answer met/not-met from the repository and condition alone |
|---:|:---:|---|
| 1 | **No** | `find` has no project-change baseline, scans pre-existing customer code, and cannot observe deployments, purchases, publications, credential changes, branches, tickets, schedules, or services. See F34, F42, F75. |
| 2 | **No** | “Defensible,” authority ownership, tier assignment, and durability have no deterministic rubric or failure model. See F43, F69. |
| 3 | **No** | A role name and prose “read path” do not settle durable authority, and the semantic-memory “owner” used for the cross-check is undefined. See F1, F44, F70. |
| 4 | **No** | An exact-looking command need not prove the footprint; no baseline, scope, expected output, or complete removal inventory is fixed. See F45. |
| 5 | **No** | “Reviewed,” the object approved, the completeness of preconditions, and the safety of the single path are unresolved. See F6, F29, F46. |
| 6 | **No** | The condition allows `no` rows, and repository identity, dirty-tree binding, history-rewrite sufficiency, and lookup format are undefined. See F5, F30, F37, F47. |
| 7 | **No** | “Improves,” “structurally enforced,” “correctly,” and the line between influence and decision have no pass predicate. See F4, F41, F48. |
| 8 | **No** | Completeness of “every difference” is open-world, while the mandatory behavioral contract dimensions are not enumerated. See F2, F49, F66. |
| 9 | **No** | A unique final outcome can still be corrupt or unsafe because no invariant or permitted result is fixed. See F12, F25, F50. |
| 10 | **No** | The minimum isolation invariant and required encrypted coverage are absent; “acceptable” exclusions and blast radius remain reviewer opinions. See F3, F13, F51. |
| 11 | **No** | “Verification step,” export/deletion scope, audit event coverage, immutability, and perfect redaction can be satisfied by unsupported assertions. See F13, F33, F52, F71–F72. |
| 12 | **No** | There is no canonical legacy inventory, safety invariant, exact equivalence rule, phase authority, or objective rehearsal evidence. See F7, F53, F62, F74. |
| 13 | **No** | A cited number need not be relevant, comparison criteria and role granularity are undefined, and qualitative correctness remains with `staff`. See F10, F54, F63. |
| 14 | **No** | Required rows, units, assumption basis, formulas, time horizon, and cost perimeter are absent; “the template” is an external dependency. See F14, F55, F73. |
| 15 | **No** | The component and role sets are open, key terms are undefined, numeric triggers need not be meaningful, and interface narrowness is not tested. See F15, F56, F63. |
| 16 | **No** | Rows need not have passing status or deterministic steps/results, and case quality remains an undefined QA judgment. See F40, F57. |
| 17 | **No** | The full set of irreversible decisions is unknowable, and “real option,” same decision scope, conflict, cost adequacy, and reversal quality lack algorithms. See F58, F65. |

## Summary answers

- **F76 — If every current check passed, the brief could still be badly unsatisfied.** The biggest remaining failure is a design that is internally well-formatted but has no enforceable authority and state-recovery model. It could label a role as authoritative, narrate four unsafe concurrency outcomes, omit state transitions and restore behavior, exclude artifacts from provenance/encryption/export/deletion with explanations, and still pass. Operators would still be unable to determine or recover the correct state after overlapping runs, crashes, migration, or removal.

- **F77 — The set is too large in form and too small in substance.** It has 17 acceptance gates plus governance machinery, yet omits core invariants and recovery. Merge 2, 3, 6, and 7 into one state/authority/memory/provenance contract with a fixed entity-and-flow matrix; merge 4 and 5 into one customer-repository boundary and publication contract; merge 10 and 11 into one security/data-lifecycle contract; merge 13, 14, and 15 into one evidence-based component and scale decision; fold each condition 16 case into the condition whose invariant it tests. Cut condition 17 as a separate gate and require decision records only for choices explicitly listed in a closed decision register. Cut the verifier-role, milestone, adjudication, and changelog policy from the DOD. Use the recovered space for state invariants, backup/restore, authorization, operational repair, schema evolution, and offboarding.
