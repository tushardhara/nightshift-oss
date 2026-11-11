# DoD Reconciliation — response to `DOD-CRITIQUE-codex.md`

Date: 2026-08-29. Author: `pm` (single author of `DEFINITION-OF-DONE.md`).
Input: 77 findings from a hostile cross-vendor review that saw only
`.omni/brief.md` and the v1 definition of done.

**How I ruled.** A finding is `accepted` when it makes a condition decidable, or
adds substance the brief demanded and I missed. It is `accepted-in-part` when the
diagnosis is right but the proposed remedy needs a running system, invents scope,
or would remove the only thing making a check objective — the accepted half is
named in the reasoning. It is `rejected` when the fix could not be settled by a
stranger reading this repository, which is the same rule the critique holds me to.

**Result:** 48 accepted · 27 accepted-in-part · 2 rejected. The DoD grows from 17
to 23 conditions; six are new (18–23) and every existing one was rewritten to
replace an open predicate with a closed set or a pass predicate.

| # | Finding | Verdict | Reasoning |
|---|---|---|---|
| F1 | "One authority" checks a role label, not durability | accepted | Condition 3 now requires the selected component, write/read paths, consistency rule, recovery source and precedence rule — a label alone can no longer pass. |
| F2 | Contract may be tiny; "every difference" is reviewer judgment | accepted-in-part | Accepted the fix — condition 8 now fixes ten mandatory contract dimensions; rejected the open-world completeness claim, replaced by "observable through the dimensions". |
| F3 | Nothing forces large/sensitive artifacts into the encrypted set | accepted | Condition 2's inventory now carries a sensitivity/size classification and condition 10 requires the encrypted set to cover every class so classified, with no author-chosen exclusions. |
| F4 | "Improves retrieval" is never required or shown | accepted-in-part | Accepted: condition 7 now requires a named retrieval use case and the fallback it beats; rejected a measured relevance improvement — that needs a running system the brief forbids building. |
| F5 | Condition 6 lets any class opt out of provenance | accepted | The escape hatch is closed: zero `no` rows for artifacts and decisions; only rebuildable derived indexes may be exempt, and only by naming their rebuild source. |
| F6 | Approval is not bound to the bytes published | accepted | Condition 5 now binds approval to a content digest, the exact diff, the base commit, single-use authorization and a staleness rule. |
| F7 | "Safe migration" omits backup, live writes, cutover authority, partial failure | accepted | Condition 12 gains per-phase authority and write-freeze rules, partial-failure recovery, and a required rehearsal artifact; backup/restore becomes new condition 19. |
| F8 | Nothing shows the selection is cheapest or correct | accepted-in-part | Accepted the cheapest-correct rule: no rejected candidate may both meet the role's required guarantees and cost less, or the reason is written; rejected scored-arithmetic selection as false precision. |
| F9 | "Offline" and "simple" are undefined | accepted | Condition 8 now defines offline as zero outbound network dependency at runtime and requires a stated process-count bound for the local deployment. |
| F10 | Nine isolated sections are not a comparison | accepted | Condition 13 now requires one comparison matrix with a closed criteria list and per-role required guarantees, not nine independent verdicts. |
| F11 | Three-way separation can remain taxonomy | accepted | Condition 2 now requires distinct write paths and permissions per tier plus a prohibited-flow matrix — separation must be structural, not a column value. |
| F12 | Scenario outcomes need not preserve anything | accepted | New condition 18 fixes the invariant catalogue; condition 9 now requires each scenario to name the invariants it preserves and the effects it rejects. |
| F13 | Security outcomes remain author-selected | accepted-in-part | Accepted the minima (isolation invariant, encrypted coverage, audit event floor, backup-copy deletion, redaction failure behaviour); rejected fixing the design's answers, which is the architect's job, not the DoD's. |
| F14 | Capacity dimensions, units and cost basis unspecified | accepted | Condition 14 now names the required workload axes, units, peak factor, currency, pricing date and cost categories. |
| F15 | A port table does not bound interface width or justify present infrastructure | accepted-in-part | Accepted the width bound and the present-versus-future flag; rejected a replacement-effort acceptance test, which presumes an implementation to measure. |
| F16 | File inspection cannot prove no external action occurred | accepted | Removed the false check; external-act prohibitions moved to `## What this document cannot settle` and to PROJECT.md as an execution constraint. |
| F17 | No check for migration execution or cutover | accepted-in-part | Accepted the decidable half: condition 12 requires MIGRATION.md to declare itself unexecuted and the register to contain no execution log; absence of a cutover elsewhere is not repo-decidable and is recorded as a limit. |
| F18 | Kanban activity is undecidable; identifier scan proves nothing | accepted-in-part | Accepted the deletion of the identifier scan; the prohibition itself is recorded as a limit rather than dressed up as a check. |
| F19 | "No heartbeat or automation" unmapped | rejected | Nothing in the repository distinguishes silence from compliance, so any condition I wrote would pass by default — recorded instead as an execution constraint and a stated limit, which is the honest form. |
| F20 | "No crew chartering or worker dispatch" unmapped | rejected | Same ground: dispatch leaves no artifact a stranger could check here, and a check that always passes is worse than an admitted limit. |
| F21 | Purchase, credential change, external publication not checkable | accepted-in-part | Accepted removal of the pretence; the prohibitions stand as owner constraints in PROJECT.md, and condition 5 covers the only publication path the design itself creates. |
| F22 | Preservation reduced to sixteen names | accepted-in-part | Accepted: each inventory row now names its key identifier, its relations and its lifecycle; rejected requiring full schemas, which is design body the architect owns and would freeze detail prematurely. |
| F23 | "Clean by default" is not required to be the default | accepted | Condition 4 now requires the zero-file mode to be the default for a customer installation, or a deviation recorded in the decision register. |
| F24 | The four operator questions are not all answerable | accepted | New condition 23 requires a table mapping each of the four questions to the artifact that answers it, including after Nightshift is removed; the coverage table no longer overclaims. |
| F25 | No state machine or invariant catalogue | accepted | New condition 18: allowed transitions, preconditions, terminal states, uniqueness rules and invariants every writer must preserve. |
| F26 | No backup, restore or disaster recovery | accepted | New condition 19: failure model, backup coverage per class, ordered restore, RPO/RTO numbers, rehearsal case. |
| F27 | No authentication or authorization contract | accepted | New condition 20: actor register, tenant-scoped permissions, service-to-service authorization, revocation, separation of duties. |
| F28 | No operational health or repair contract | accepted | New condition 21: eight named failure signals, detection, operator action, repair boundary and what repair may never do. |
| F29 | Publication lacks content safety and recovery | accepted | Condition 5 now requires path normalization, symlink defence, overwrite/conflict rules, atomicity, and rollback of an approved-but-bad publication. |
| F30 | A SHA does not identify a repository, or dirty bytes | accepted | Condition 6 now requires a canonical repository identifier stable across clones, forks, renames and remotes, plus a content digest for uncommitted trees. |
| F31 | No schema or compatibility evolution | accepted | New condition 22: format versioning, forward/backward compatibility, rolling upgrade and downgrade behaviour. |
| F32 | Export is not an exit path | accepted | New condition 23: manifest, integrity verification, documented semantics, readability without Nightshift, key handover. |
| F33 | Audit lacks evidentiary integrity | accepted | Condition 11 now requires gap and tamper detection, a clock source, actor binding and failed-audit-write behaviour — the word "immutable" alone no longer passes. |
| F34 | Condition 1's command scans the customer codebase | accepted | Condition 1 is rewritten around a declared baseline commit and an artifact register; the extension test applies only to files this project changed. |
| F35 | "No person's opinion" contradicts the verifier rules | accepted-in-part | Accepted the wording fix and removed "defensible"/"acceptable" style predicates; rejected removing named verifiers — a check with no named runner is not operable. |
| F36 | Conditions depend on invisible external context | accepted | The DoD now reproduces its assumption register, its artifact register and the template columns it relies on, inline. |
| F37 | Condition 6's universal title contradicts its exception | accepted | Same fix as F5: the universal promise is now actually universal. |
| F38 | Zero-file mode and publication have no boundary | accepted | Condition 4 now states the footprint claim holds absent an approved publication, and that a publication is a separate audited event, not a footprint violation. |
| F39 | "Reversed" contradicts a point of no return | accepted | Condition 12 is retitled to a stated reversibility boundary and now requires a forward-recovery path for anything past it. |
| F40 | "Transcript" means two things | accepted | Condition 11 now requires the design to define in-flight agent transcript versus stored transcript and to name the redaction point between them. |
| F41 | "Cannot decide anything" versus permitted influence | accepted-in-part | Accepted the retitle to the brief's own scope and an enumerated allowed-influence list; rejected the implication that ranking is decision-making, which would ban retrieval itself. |
| F42 | No provenance baseline; external acts unobservable | accepted-in-part | Accepted the baseline manifest/diff and the removal of external-act claims; rejected the proposed "authoritative activity ledger", which does not exist and cannot be created locally. |
| F43 | "Defensible" and "what survives what" hide judgment | accepted | Condition 2 now uses a closed five-event failure matrix and three written tier-inclusion tests, so assignment is mechanical. |
| F44 | Condition 3 checks a label | accepted | Same fix as F1, plus a required lookup shape and conflict precedence. |
| F45 | Footprint command needs baseline and pass predicate | accepted | Condition 4 now requires a before/after manifest definition, explicit Git and filesystem scopes, an exit-code pass predicate and a fixed removal inventory. |
| F46 | "Reviewed" is not observable | accepted | Covered by the same rewrite as F6 and F29, including a minimum precondition set. |
| F47 | Identity, lookup and `no` rows undefined | accepted-in-part | Accepted formats, bidirectional query inputs/outputs and zero `no` rows for artifacts and decisions; rejected zero exemptions absolutely — a rebuildable derived index is not an artifact, and must name its rebuild source instead. |
| F48 | Memory outcomes unfalsifiable | accepted-in-part | Accepted the unreachability rule, the closed allowed-operation list and explicit absent/stale/contradictory behaviour; rejected expected-trace fixtures and measured improvement, which require a system to run. |
| F49 | Open-world completeness claim | accepted-in-part | Accepted the mandatory dimension matrix; rejected asking a stranger to prove a negative, which the dimensions replace. |
| F50 | "Corruption" and "safe outcome" undefined | accepted | Condition 9 now requires, per scenario, the event sequence, permitted transition, committed effects, rejected effects, retry result and final records, judged against condition 18's invariants. |
| F51 | Security design may be arbitrarily weak | accepted | Condition 10 now requires a minimum no-cross-tenant-read invariant, coverage tied to classification, at-rest/in-transit/backup boundaries and exact rotation outcomes. |
| F52 | Verification steps can be ceremonial | accepted-in-part | Accepted exact queries, manifests, counts and audit minima; rejected a deterministic predicate for perfect secret detection, replaced with a detection model plus residual-risk statement per F71. |
| F53 | No canonical legacy inventory or equivalence rule | accepted | Condition 12 now requires the legacy inventory to be enumerated from a named source, with per-item preservation/loss invariants and an exact reconciliation formula. |
| F54 | Comparison quality unverified; "role" undefined | accepted-in-part | Accepted the criteria matrix, disqualifiers and a single definition of "role" shared with the state model; rejected a deterministic scoring rule, which manufactures objectivity rather than exposing it. |
| F55 | Capacity table undefined | accepted | Same fix as F14: closed dimension list, units, formulas, horizon and cost perimeter. |
| F56 | Ladder terms mechanically unresolvable | accepted-in-part | Accepted the closed register, present/future flag, metric+threshold+source triggers and numeric reversal predicates; rejected the replacement acceptance test as unbuildable in a design-only project. |
| F57 | Condition 16 verifies table shape | accepted-in-part | Accepted the status vocabulary, deterministic expected results and invariant-level linkage; rejected executable fixtures, since nothing runs in this project. |
| F58 | The set of irreversible decisions is unknowable | accepted | Condition 17 now runs off a closed decision register with stable IDs cross-linked from the architecture documents; completeness is claimed only against that register. |
| F59 | Governance is invented and not owner criteria | accepted-in-part | Accepted: the follow-up-project policy moved out of the DoD into PROJECT.md and roles are now labelled a project convention; rejected deleting verifiers, since someone must run each check. |
| F60 | "Exactly one pathway" and "human" exceed the brief | accepted-in-part | Accepted multiple callers behind one enforcement boundary and policy-backed review; kept a named accountable identity distinct from the writing process, because without one "never accidental" has no meaning. |
| F61 | Memory restrictions exceed the brief | accepted-in-part | Accepted removing the organizational owner-separation rule; kept the data-tier statement that the memory store is not the control-state authority, which is the brief's own "cannot override". |
| F62 | Migration mechanics mandated as facts | accepted-in-part | Accepted labelling idempotence and the reversibility boundary as stated acceptance assumptions (DA5, DA7); kept them as conditions, because "safe" with no definition is unfalsifiable. |
| F63 | Component topology overconstrained | accepted-in-part | Accepted composite component sets and qualitative disqualifiers as valid rejections; kept one selected set per role, since two live components for one role recreates the dual-authority defect the brief exists to kill. |
| F64 | Artifact taxonomy is not an owner outcome | accepted-in-part | Accepted the label — paths are means, declared as convention and renameable by agreement; rejected removing them, as the critique's own demand for document-locatable evidence depends on them. |
| F65 | Four decisions prejudged as one-way doors | accepted | Condition 17 no longer asserts irreversibility; the register lists decisions, and each ADR states its own one-way-door answer. |
| F66 | Enumerating every difference exceeds equivalence | accepted | Condition 8(c) now only requires differences observable through the contract dimensions to be listed; invisible implementation variation is out of scope. |
| F67 | Four invisible assumptions used as authority | accepted | The DoD now carries its own assumption register DA1–DA10; no condition depends on a file outside it. |
| F68 | Verifier identities invented and unstated | accepted-in-part | Accepted stating them as a project convention rather than owner fact (DA8); rejected removal, for the reason given at F59. |
| F69 | Sixteen nouns assumed exhaustive | accepted | Condition 2 now reads "at minimum these sixteen" plus any class the design introduces, closed by an explicit completeness declaration — a needed join entity no longer fails the check. |
| F70 | One role and one component assumed right granularity | accepted | Recorded as assumption DA3: one authority for control state does not mean one component per architectural role, and condition 13 permits composite sets. |
| F71 | Perfect secret exclusion asserted without a model | accepted | Condition 11 now requires a secret definition, a detection mechanism, behaviour on detection failure and a residual-risk statement, instead of an absolute nobody can deliver. |
| F72 | Retention shape assumed | accepted | Each class now needs a duration **or** a named policy source, legal holds are explicitly permitted, and more than one role may change policy if enumerated. |
| F73 | Capacity shape invented | accepted | The 100× model must now be stated per axis rather than assumed uniform, with sources and confidence for every assumption (DA10). |
| F74 | Assumes exactly one irreversible boundary | accepted | Condition 12 now accepts zero or several boundaries, provided each is named. |
| F75 | Final file list treated as evidence | accepted | Replaced by the declared pre-project baseline and register diff in condition 1. |
| F76 | A passing design could still leave state undeterminable | accepted | The strongest finding: answered with new conditions 18 (invariants), 19 (backup/restore), 20 (authorization), 21 (repair), 22 (evolution), 23 (offboarding). |
| F77 | Too large in form, too small in substance | accepted-in-part | Substance accepted as conditions 18–23 and governance trimmed per F59; **merges rejected** — see the argument below. |

## Why I rejected the merges in F77

The critique's own method defeats its proposal. Every finding from F43 to F58 is
some version of "this predicate is open, so two strangers can disagree". Merging
conditions 2, 3, 6 and 7 into one "state/authority/memory/provenance contract"
produces a single gate whose verdict is the conjunction of roughly thirty separate
predicates. A stranger who finds the invariant catalogue complete but the dirty-tree
rule missing has to decide what to write in one met/not-met box, and that decision
is exactly the judgement call the critique spent seventy findings trying to remove.
Separation is not formatting; it is what makes the answer forced.

Three further reasons. First, partial progress becomes invisible: a merged
condition is unmet until its weakest clause lands, so a milestone boundary cannot
tell "one clause outstanding" from "nothing done", and the owner loses the early
warning that a milestone is in trouble. Second, repair becomes coarse: when the
architect fixes one clause, a merged gate must be re-verified in full, which raises
the cost of every revision and quietly encourages nobody to revise. Third, the
merges hide the coverage argument — the brief's promises map one-to-few onto
conditions today, and a reader can walk the coverage table and see nothing dropped;
under the merges, four brief promises share one row, and a dropped promise no
longer shows up as a missing row.

I did take the compaction point where it was real: the governance policy, the
follow-up-project rule and the milestone accounting are out of the DoD body, and
condition 17 shrank to a register cross-check.

## What I am NOT admitting to the DoD

Genuine but out of frame; recorded as out of scope in `PROJECT.md`, not as
conditions here:

- Proving that no deployment, purchase, credential change, external publication,
  heartbeat, automation, crew chartering or Kanban activity occurred (F16–F21, F42).
  No local artifact settles it; it stays an execution constraint.
- Measured retrieval improvement, latency, or any benchmark (F4, F48). Requires the
  system the brief forbids building — a follow-up implementation project owns it.
- Executable conformance fixtures and automated cases (F48, F57). Same reason;
  `docs/tests/cases.md` stays `automated: no` for this project by design.
- Certification against a named compliance regime (unchanged from v1).
