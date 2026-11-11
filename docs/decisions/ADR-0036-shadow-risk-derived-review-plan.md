# ADR-0036: Ship the risk-derived review plan in shadow mode first

Date: 2026-08-28
Status: accepted
Deciders: owner, program
Issue: #228 (approved delivery slice #228A)
Supersedes: ADR-0034 and ADR-0035 only for delivery scope and activation order
Superseded-by: ADR-0038, only for the WAL-materialization/file-write claim in the
Decision and Acceptance boundary sections below (that text is otherwise unedited
here; see ADR-0038 for the corrected claim and full reasoning)

## Context

The first #228 design combined risk classification, pre-build admission, review
receipts, evidence reuse, Release eligibility, candidate execution, publication,
crash recovery, transport fencing, and rollback. Its reviewed branch reached about
4,000 production additions and 1,900 test additions before publication was safe.
That is too much authority to review and activate as one change.

The proposal history remains on `feat/228-risk-based-review`:

- ADR-0034 at `79468de875c85e3d9739128e75067b6556a62a90`;
- ADR-0035 at `097cb763f610f220458ebc004665e53959858228`.

Those ADRs are not copied into #228A. The numbering gap preserves the audit trail.
This ADR supersedes only their decision to deliver and activate the whole design in
one work item. It does not silently approve, reject, or alter any deferred runtime
mechanism.

`#228A` names an approved delivery slice tracked under GitHub issue #228. It is not
a separate GitHub issue number.

## Decision

#228A delivers one small, exact-SHA **shadow planner**. Its only entry point is:

```text
nightshift-ctl review plan --task ID --repo PATH --head-sha FULL40
```

The control DB comes from the normal global `--db` option or normal control-plane
discovery. It is independent of the target `--repo`; the repository path never
selects or redirects control state. The command opens the existing `control.db` as
a read-only SQLite snapshot and reads the task's exact `id`, `crew`, `size`,
`spec_ref`, and `base_sha`. BASE is that persisted `task.base_sha`, never a caller
argument. The read performs no schema creation or migration, no write transaction,
and no WAL creation. The command otherwise reads Git objects and committed rules,
emits one canonical JSON object to stdout, and stores nothing. It writes no file,
database, Git, GitHub, task, or review state. Caller capture, comparison, or
persistence is outside #228A and cannot become review or authorization evidence.

The compact ruleset is freshly authored and reviewed inside #228A. At runtime the
planner loads that ruleset from its committed executing Nightshift bundle and
reports the bundle source SHA, rules blob OID, and rules digest. The abandoned
`027d4b20aeece000b13ac996aa7c90880878b8bb` implementation and its generated
state are not authority inputs.
`rules_blob_oid` means the rules file's Git object id in that bundle;
`rules_digest` means SHA-256 of its canonical content.

The caller supplies only the task id, repository path, and full 40-hex HEAD. There
is no task JSON, caller facts, BASE option, profile option, or trigger override.
Every risk trigger not contained in the persisted task row derives from the exact
BASE-to-HEAD Git diff, paths, and blob content. The planner deterministically
reports:

- `authority: "shadow_only"`;
- `FAST`, `STANDARD`, or `HIGH_RISK`;
- the facts and changed paths that triggered that profile;
- `suggested_domains`; and
- one canonical, immutable suggested parallel plan in that stdout object, including
  its content digest and every input Git object id.

For a rename, classification evaluates both the old and new path and both the old
and new blob. One cheap side can never hide a sensitive side.

Missing or unreadable control DB, missing task, missing/invalid BASE or HEAD,
missing/malformed rules, or an unproved executing-bundle SHA/rules identity exits 3
without a plan. Once those exact authority inputs are valid, ambiguous
classification evidence succeeds with a `HIGH_RISK` shadow plan. There is no
caller-selected profile or weakening override.

Determinism means the same read-only task snapshot, executing bundle SHA and rules
blob, and target BASE/HEAD Git objects produce identical canonical stdout.

The stdout is diagnostic only. The current universal review workflow remains the
sole authority. The shadow plan must not cause a dispatch, skipped gate,
implementation admission, Release authorization, or task/review lifecycle change.
It cannot satisfy, replace, or contribute evidence to ADR-0026's review contract.

## Explicitly out of scope

#228A does not add or change:

- activation or conditional routing;
- assignments, attestations, findings, resolutions, consolidation packets,
  evidence reuse, refresh, or eligibility receipts;
- pre-build contracts, task admission, worker slots, continuation, or recovery;
- candidate execution, local suites, Bubblewrap, process teardown, or test-provider
  infrastructure;
- default-ref publication, Git/GitHub transport, leases, retries, tags, integration,
  incidents, or rollback;
- GitHub Actions workflows, Omnigent policies, Program/crew/Release instructions,
  deployment, or live control-plane migration.

Each deferred capability requires separately ready tracked work and, where it
changes authority, a successor ADR. No later slice may treat the shadow report as
an authorization receipt without that explicit decision.

## Options considered

### A. Keep the ADR-0034/0035 all-in-one implementation

Rejected. Classification defects, process isolation, SQLite crash recovery, and
default-branch mutation were coupled in one review surface. A green classifier test
could not make the publication state machine easier to audit.

### B. Add prompt-only risk labels

Rejected. A label chosen by Program or a model is a downgrade path and is not
reproducible from Git evidence.

### C. Activate the deterministic planner immediately

Rejected. #228A has no durable gate receipts or Release eligibility aggregate, so
activation would let a correct plan be enforced by memory rather than machinery.

### D. Merge the exact-SHA planner in shadow mode

Chosen. It proves the classification boundary independently while preserving the
existing fail-safe ceremony until later enforcement issues are reviewed.

## Acceptance boundary

#228A is complete only when tests prove canonical repeatability; evaluation of both
rename paths and blobs; ambiguous classification evidence promoting to HIGH_RISK
without a weakening override; and the frozen FAST/STANDARD/HIGH_RISK fixtures.
Output must use `authority: "shadow_only"` and `suggested_domains`, and identify the
executing bundle source SHA, rules blob OID, rules digest, and exact BASE/HEAD
objects. Tests must prove missing DB and missing task both exit 3 without a plan;
valid exact authority with ambiguous classification evidence returns a successful
HIGH_RISK shadow plan. Tests must also prove the result cannot change admission,
dispatch, ADR-0026 evidence, review, Release, workflow, policy, or Git state. That
side-effect proof consists of before/after control-DB bytes and state plus target
Git bytes and state, and a conformance test proving no affected subsystem imports
or calls the planner. BASE and task facts must come only from one read-only SQLite
snapshot, and planning must create no schema, write, WAL, or stored report.

## Consequences

The first merge produces evidence, not faster delivery. That is intentional: the
planner can be reviewed horizontally before any policy flip. Later issues can add
durable execution and eligibility around a stable plan format, and publication can
be assessed as a separate high-risk system.

The planner is Tier 0 local computation with no stored state. It adds no service and
no billed API cost. At 100 times current task volume, work is linear in the bounded
changed-file manifest; revisit only if measured classification time becomes
material.

## One-way door and reversal trigger

This is not a one-way door because shadow output authorizes nothing. Remove or
replace the planner if two independently reviewed fixtures cannot produce one
canonical plan, if the same Git inputs produce different output, or if keeping the
plan exact-SHA-bound requires publication or admission authority to leak into
#228A. Existing universal review remains the rollback path throughout.
