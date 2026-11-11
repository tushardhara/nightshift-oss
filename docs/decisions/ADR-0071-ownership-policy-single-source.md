# ADR-0071: One machine-readable source for path ownership

Date: 2026-09-02  
Status: proposed  
Deciders: architect (Codex), for Program on issue #567
Supersedes: ADR-0036 only for (1) ownership no-match or ambiguous-owner handling after valid
authority inputs, which changes from a successful `HIGH_RISK` shadow plan to hard denial, and
(2) the definition of `rules_digest`, which expands from canonical review-risk content to the
path-keyed canonical policy bundle. ADR-0036 remains controlling for all other classification
ambiguities and planner behavior.

## Context

`docs/owners.md` says every path has exactly one owner and is the map Program uses when
granting leases. `nightshift-ctl review plan`, however, resolves owners from
`program/review-risk.json.ownership_rules`. The artifacts already disagree for
`docs/architecture/**`, `docs/audit/**`, and `bin/nightshift-workspace`. At the issue #567 base
SHA (`5eb1430fe6db5ae6723e3bdcdce9cd7451035703`), the real resolver returns no owner for
`bin/nightshift-workspace`.

PR #564, which is open at the time of writing, would close only the human-map omission identified
by #460: after it merges, its new `bin/nightshift-workspace` row will be present in
`docs/owners.md`, but there will still be no matching rule in `program/review-risk.json` and the
runtime resolver will still return no owner. Therefore #460/#564 must not be treated as having
enabled an ownership gate for that path, and the cutover implementation cannot start until #564
has merged.

This is not merely stale documentation. A lease may be granted from one policy while review
enforcement evaluates another. Worse, `_owner() == None` currently suppresses the
`other_owner` trigger. Although review classification records an ambiguity and becomes more
conservative overall, the ownership boundary itself has failed open: it does not identify the
owner whose cross-boundary review and lease evidence are required.

The documents also carry information that the current JSON cannot represent cleanly:
single-writer semantics, explanatory notes, and ownership of tests by the component they drive.
That does not justify two independently edited maps. It means the canonical schema must capture
the enforceable fields and the human document may add prose outside its generated policy table.

## Options considered

### 1. Generate `program/review-risk.json.ownership_rules` from `docs/owners.md`

Rejected. Markdown is a poor policy input: prose such as “tests that drive” and exceptions in
notes require an increasingly clever parser, formatting edits become policy edits, and malformed
documentation could disable the control. The authority would remain difficult to validate before
execution.

### 2. Make `review plan` parse `docs/owners.md` directly

Rejected for the same reasons, with the additional cost that a runtime safety decision would
depend on presentation markup. This makes documentation syntax part of the control-plane API.

### 3. Keep both hand-authored maps and test equivalence

Rejected. A test can detect drift but still leaves two places where a policy change appears
reasonable, creates ordering ambiguity during a change, and cannot establish which version is
authoritative between commits. Equivalence is also underspecified while the Markdown contains
semantics absent from the JSON.

### 4. Treat `review-risk.json.ownership_rules` as canonical and generate the Markdown table

Viable as a minimal patch, but rejected in favor of option 5. Ownership is shared by lease
granting, review planning, and future path controls; nesting it in a review-risk configuration
misstates its scope and couples unrelated policy lifecycles. The existing schema also lacks
single-writer and explanatory fields needed by the documented contract.

### 5. Create a dedicated machine-readable ownership manifest; make all consumers and generated
documentation derive from it

Chosen. `program/ownership.json` becomes the only authoritative ownership policy.
`nightshift-ctl review plan` reads it directly rather than accepting ownership rules from
`review-risk.json`. Program's lease workflow consults the same manifest. `docs/owners.md` keeps
introductory prose, but its ownership table is generated from the manifest and is not edited by
hand.

## Decision

Implement option 5 as one atomic change:

1. Add a versioned `program/ownership.json` whose rows contain at least `owner`, explicit glob or
   path selectors, `single_writer`, and a human-facing note. Encode exceptions as more-specific
   rules. Replace prose-only selectors such as “tests that drive” with explicit, mechanically
   resolvable selectors before cutover. Bootstrap this manifest only after PR #564 merges, by
   reconciling the **explicit union** of the post-#564 `docs/owners.md` table and
   `program/review-risk.json.ownership_rules`; do not use either artifact as a one-way seed. For
   every selector or effective path whose owner differs or exists in only one source, record a
   structured disposition before generation or fail the bootstrap. Each disposition records the
   path or selector, `from_owner` (nullable only when previously unmapped), `to_owner`, reason,
   and authorized decider. The check must verify that the observed old-to-new result exactly
   matches those fields; a path name alone cannot waive a change. In particular, preserve #564's
   `bin/nightshift-workspace -> platform` row and the currently resolver-winning
   `bin/nightshift_review_plan.py -> crew_alpha`, `tests/test_shadow_risk_plan.py -> crew_alpha`,
   `tests/** -> platform`, and `docs/** -> program` rules unless an explicit disposition replaces
   one. Seeding from review-risk alone would erase #564; seeding from the Markdown alone would
   erase those four resolver-winning rules and unmap 279 of 511 tracked paths at the base SHA.
2. Remove `ownership_rules` from `program/review-risk.json`; do not retain a generated copy.
   `review plan` loads the dedicated manifest as a separate required input. Add
   `program/ownership.json` to the planner's attested `SOURCE_PATHS`, and redefine
   `rules_digest` exactly as SHA-256 of the UTF-8 canonical JSON encoding of
   `{"program/ownership.json": <parsed ownership object>, "program/review-risk.json": <parsed
   review-risk object>}`. Parse and validate each file first; hash this one path-keyed object, not
   raw file bytes, concatenated blobs, or merged document key spaces. Thus whitespace-only
   reformatting does not invalidate evidence, shared keys such as `schema_version` cannot
   overwrite each other, and a third policy file can be added as another path key. Any semantic
   ownership-manifest change—including a schema-version-only change—necessarily changes
   `rules_digest`; the existing receipt-stamping and eligibility digest comparisons therefore
   invalidate direct satisfaction and cross-head reuse of evidence created under a different
   ownership policy. A regression must construct two distinct document pairs that collide under
   key-space merging, using an ownership `schema_version` change as the named case, and prove
   their path-keyed digests differ.
3. Generate the managed ownership table in `docs/owners.md` deterministically from the manifest.
   A check mode regenerates it in memory and requires byte-for-byte equality with the committed
   managed region. Prose outside that region may remain hand-authored but is non-authoritative.
4. Add a repository-policy test that enumerates every path from `git ls-files` and every old and
   new path in the change under review. For each path it asserts exactly one winning rule after
   specificity resolution. A path present only in the documentation is impossible inside the
   generated region and fails the byte-equivalence check. A path present only in the manifest
   appears in regenerated documentation and likewise fails that check. A repository path absent
   from the manifest, or matched by equally specific rules with different owners, fails the
   coverage test and `review plan` with a stable hard-denial reason. Tests must include all three
   negative cases: manifest-only rule, documentation-only row, and unmapped changed path. The
   documentation-only mutation test must start from the reconciled post-#564 union, add a
   synthetic row, and prove check mode rejects it; it is not permission to discard real
   documentation rows during bootstrap. Before the manifest exists, generate and commit the
   **initial** effective-owner baseline by resolving every path in `git ls-files` through the old
   enforced `program/review-risk.json.ownership_rules` at base `5eb1430`. The cutover check must
   compare every result from the new canonical resolver against that independently anchored
   baseline and reject every difference without a structured disposition whose declared
   `from_owner` and `to_owner` exactly match the observed change. Only after a successful
   anchored cutover may later baselines roll forward from the canonical resolver; each roll
   forward is compared with its committed predecessor and requires the same dispositions.
   Regenerating a baseline from the candidate manifest without that predecessor comparison must
   fail. This end-to-end assertion catches both loss-to-unmapped and silent fallback to a
   different owner caused by changed specificity semantics.
5. Change the runtime default from “no owner” to fail closed. `_owner()` may retain an internal
   no-match representation, but classification must turn no match or ambiguous ownership into a
   denied plan, not merely omit `other_owner`. It must name the path and reason and must not emit
   an executable review plan. This intentionally supersedes ADR-0036's rule that all ambiguity
   after valid authority inputs succeeds as a `HIGH_RISK` shadow plan, but only for ownership
   no-match and ambiguous-owner evidence; content, mode, and other classification ambiguities
   retain ADR-0036 behavior. Replace the existing test that expects `mystery/data.bin` to succeed
   with a `HIGH_RISK` plan with a successor-ADR-linked hard-denial expectation.
6. Bump `program/review-risk.json.schema_version` when removing `ownership_rules`, and update its
   parser/tests as an explicit schema migration. Do not change the key set while continuing to
   claim schema version 1.
7. Use one shared glob parser, validator, matcher, and specificity implementation for manifest
   reconciliation, review planning, lease planning, documentation generation, and coverage
   checks. No consumer may grow a second interpretation of `*`, `**`, path validity, or winning
   specificity. `ownership_exempt_globs` must not silently bypass canonical ownership:
   `tests/test_shadow_risk_plan.py` currently matches `tests/test_*.py`, so despite resolving to
   `crew_alpha` it emits no `other_owner` trigger. The cutover must remove that bypass for
   ownership enforcement (or replace it with an explicit manifest disposition) and prove a
   non-crew_alpha change to that path receives the effective-owner handling.
8. The Markdown renderer/checker must escape literal pipe and other table-significant content in
   manifest notes reversibly. A round-trip regression must use the full post-#564
   `bin/nightshift-workspace` note and prove rendering plus parsing preserves its exact semantic
   fields. Update `program/config.yaml` so its claim of a mechanical `docs/owners.md` check names
   the checker implemented by this cutover; if that checker is not delivered, remove the claim.

Fail-closed cutover is deliberately not free. At base `5eb1430`, `git ls-tree -r` enumerates 14
tracked files under `bin/`: seven mode-100755 executables/scripts and seven mode-100644 Python
modules. Executing the real resolver maps two to `crew_alpha`—`bin/nightshift-ctl` and
`bin/nightshift_review_plan.py`—so **12 tracked `bin/` files are rowless**. The coverage gate
intentionally uses tracked files, not executable mode, so all 14 are in scope.

The pre-cutover inventory is repository-wide, not limited to `bin/`. The implementation must
resolve the union of both existing maps against every tracked path, explicitly retain or replace
each existing effective assignment, and explicitly assign every remaining unmapped path before
enabling hard denial. At the base SHA, 279 of 511 tracked paths receive their winning owner from
one of four enforced rules that lack a machine-usable Markdown row; those assignments must not
vanish merely because the generated document becomes byte-equal to a new manifest. Turning on
denial before the reconciliation and full classification would make large parts of the repository
unreviewable. A temporary catch-all rule is forbidden because it would recreate the missing-owner
hazard under a nominal owner. Existing ambiguous overlaps must likewise be resolved explicitly
before the gate is enabled.

The base has **21 unmapped tracked paths**, not merely the 12 rowless paths under `bin/`. The
other nine include `.github/ISSUE_TEMPLATE/*.md`, `.github/pull_request_template.md`,
`.gitignore`, `.pr-body-detector.md`, `AGENTS.md`, `README.md`, and `nightshift-model.html`; each
needs an explicit assignment. The 279 paths needing union reconciliation also split into two
different cases: 155 paths (`docs/audit/**` 83, `docs/decisions/**` 63, and
`docs/architecture/**` 9) have a Markdown row whose owner cell is non-machine-readable prose,
while the other 124 have no Markdown row. Converting the 155 prose owners requires an explicit
disposition; it is not a mechanical addition of a missing selector. Every disposition that
assigns or moves ownership—not only these prose conversions—requires an authorized decider and
the complete before/after record defined above.

The post-#564 Markdown fix and this ADR are prerequisites, not enforcement. The path becomes
gated only when a follow-up implementation changes the real resolver to consume the canonical
manifest, assigns every tracked path, and enables the fail-closed behavior with the regressions
above.

Changing the digest construction is an intentional one-time evidence cutover. A read-only
snapshot measured 166 receipts across 39 tasks, all carrying the same legacy single-file
`rules_digest`; all 166 become stale simultaneously and can satisfy neither direct review nor
cross-head reuse after cutover. Coordinate landing the implementation tracked as #584 at the
lowest in-flight review point available, recount immediately before merge, notify every affected
crew that prior approvals must be re-recorded, and verify the expected invalidation rather than
relying only on tests that inject a synthetic stale digest.

The implementation is size **M**. It belongs to the **platform seat**: `program/**` is owned by
platform, the change alters shared policy/configuration and its test infrastructure, and no crew
owns that path. Platform must obtain leases for `docs/owners.md` and for the crew_alpha-owned
`bin/nightshift_review_plan.py` and `tests/test_shadow_risk_plan.py` before editing them; the ADR
itself is the prerequisite for the `program/**` change. Because the diff affects policy inputs and
fail-closed enforcement, staff and cross-crew review are required, and security should review
the parser/input-validation boundary.

## Consequences

There is one answer to “who owns this path” for lease planning, review planning, tests, and human
readers. Drift becomes structurally impossible for runtime consumers and mechanically detected
for the rendered documentation. New or ambiguous paths stop the plan instead of silently losing
their ownership check.

The cost is a schema, renderer/checker, repository-wide initial classification, and a stricter
change path for newly introduced top-level areas. The manifest becomes a shared control-plane
interface and must be changed with the same care as other `program/**` policy. Human-only
ownership descriptions that cannot be reduced to selectors must be rewritten as explicit rules
or kept as non-normative explanation.

This is tier-0 local-file infrastructure: no service or recurring monetary cost now or at 100x,
and no new operational dependency. The narrow ownership-manifest interface is the switchability
boundary; future lease and review implementations consume it without depending on Markdown or on
the rest of the review-risk schema.

## Reversal trigger

Supersede this ADR if ownership ceases to be path-resolvable policy (for example, if ownership is
stored transactionally per task/resource and the control plane can prove that source is the sole
authority). At that point generate both the manifest view and Markdown from the transactional
source. Do not return to two hand-authored maps. A mere preference for editing Markdown, or the
presence of newly unmapped paths, is not a reversal trigger.
