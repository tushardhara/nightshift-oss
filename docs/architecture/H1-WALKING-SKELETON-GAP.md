# H1 walking-skeleton gap analysis

Evidence baseline: `bae01634c498278d1f99c3e59a5f903833f29273`. Scope: the H1 loop in issue #102, not a target architecture. “Exists” means executable behavior or an explicit operating procedure in this repository; prompt text alone is therefore normally **partial**, not proof that a stage runs unattended.

## Bottom line

Nightshift has a detailed self-dogfood operating manual and useful control-plane primitives, but it does not yet have an executable, repository-parameterized product loop. The shipped program describes almost every H1 stage; `nightshift-init.sh` creates project records; agent bundles define both vendors; and Release has a merge/revert procedure. No single command binds those parts to an arbitrary repository, proves vendor execution, safely creates isolated writable clones, selects the target's default branch and CI, drives a run to a durable terminal state, or starts the next task.

H1 can still be attempted while #13 remains broken, but only as an explicitly degraded topology:

```text
Program -> crew lead (planning/acceptance only)
        -> platform (Claude) -> Claude implementation task
        -> h1_eng_codex (Codex) -> Codex implementation task
        -> staff / h1_reviewer_codex -> independent review
        -> h1_qa (Codex) -> independent QA
        -> Release
```

That proves the horizontal product loop and both vendors, but **does not satisfy #102's restored crew-topology acceptance criterion**. Each bridge implementer must receive narrowly scoped product implementation authority for this run, use one isolated clone per task, and return artifacts to the crew lead for acceptance. The report must say `degraded: #13` rather than pretending the bridge seats are the crew's engineers.

The named `h1_eng_codex`, `h1_reviewer_codex`, and `h1_qa` seats do not exist today. They are deliberately top-level, Program-reachable H1 bridge seats, not crew children. Creating their configuration is insufficient: the installed runtime must load the new roster and each seat must execute once and publish a run-bound receipt before any dependent increment is scheduled. `architect` must not implement its own plan, and `release` remains merge-only so the gate stays independent. These bridge seats retire after real crew-child dispatch is proven.

## Stage-by-stage evidence and minimum increments

### 1. New project

- **Exists today: partial.** `bin/nightshift-init.sh:12-24` accepts a repository path, checks Git, and creates project-local directories. The documented operator path is still a sequence of clone/init/tracker/edit/preflight/start/run commands (`deploy/README.md:172-176`, `deploy/README.md:290`), not `nightshift init <repo> <brief>`.
- **Nightshift-specific:** host provisioning and services assume `/srv/project` (`deploy/bootstrap.sh:11`, `deploy/nightshift-heartbeat.sh:91`, `deploy/nightshift-watchdog.sh:67`); the init handoff assumes an installed bundle at `~/.nightshift` (`bin/nightshift-init.sh:422`). Those paths make one shared checkout the product boundary.
- **Smallest useful PR:** add a repository/run descriptor schema and startup record taking repository URL/path and brief, discovering origin/default branch and required CI checks, and creating a run ID plus private run root. Clone allocation belongs only to the workspace increment. It must refuse a non-empty destination and must not deploy, publish, or alter repository settings. A seeded, already-green CI workflow on the target's default branch is a startup precondition, not something the first product PR may introduce.

### 2. Init / discovery

- **Exists today: partial.** Init creates the brief, goal placeholder, records, global capacity, control DB, and per-roster registries (`bin/nightshift-init.sh:26-71`, `bin/nightshift-init.sh:192-202`, `bin/nightshift-init.sh:254-270`). Discovery specifies real Claude and Codex drafts and cross-vendor critique (`program/skills/discovery/SKILL.md:18-41`, `program/skills/discovery/SKILL.md:49-78`) and final artifacts (`program/skills/discovery/SKILL.md:119-150`). Nothing invokes this sequence from init or records that both seats actually executed.
- **Nightshift-specific:** init bakes in `crew_alpha` and `crew_beta` backlog sections (`bin/nightshift-init.sh:98-104`) and derives registries from the Nightshift bundle's installed roster (`bin/nightshift-init.sh:268-270`). Existing checked-in PRD/DoD are explicitly Nightshift self-dogfood and even validate from `/srv/project` (`docs/prd/DEFINITION-OF-DONE.md:1-6`, `docs/prd/DEFINITION-OF-DONE.md:21-30`).
- **Smallest useful PR:** make startup launch discovery for the target clone and record, per dispatch, vendor, seat, conversation/run ID, artifact SHA, and success/failure. Fail into an explicit degraded state unless at least one Claude and one Codex command actually ran.

### 3. Plan

- **Exists today: partial.** The roadmap procedure solicits crew plans, reconciles file boundaries, sequences dependencies, creates charters, and proceeds unattended (`program/skills/roadmap/SKILL.md:17-59`). It is instruction text; no state machine checks that discovery artifacts exist, commits a plan at an expected SHA, or advances the run.
- **Nightshift-specific:** it names `crew_beta` in its durable-history example and asserts two crews are the practical subscription maximum (`program/skills/roadmap/SKILL.md:58-64`), while #102 requires capacity/seam-driven N.
- **Smallest useful PR:** add one `DISCOVERED -> PLANNED` transition that validates required discovery artifacts, selects only independent seams within recorded capacity, writes task/crew assignments, commits them in the private run clone, and stores the exact SHA.

### 4. Create N useful crews

- **Exists today: partial.** Discovery says seam count controls active crews (`program/skills/discovery/SKILL.md:162-172`), and `bin/nightshift-add-crew.sh:13-60` clones and registers a crew. The script leaves five manual project edits (`bin/nightshift-add-crew.sh:63-78`) and mutates the installed Nightshift bundle rather than project/run-local configuration.
- **Nightshift-specific:** the source defaults to `crew_alpha`, names must start `crew_`, and insertion depends on the shipped roster shape (`bin/nightshift-add-crew.sh:14-30`, `bin/nightshift-add-crew.sh:47-58`). The shipped program itself lists alpha and beta (`program/config.yaml:481-482`).
- **Smallest useful PR:** after a run-local roster overlay and defined runtime activation/reload procedure exist, generate the selected number of **run-local** crew instances plus registries/backlog/ownership entries atomically from the planned seams; validate `active_crews <= min(independent seams, worker capacity, vendor capacity)`. Under #13, generate lead-only crews and mark worker routes as degraded routes to the named top-level seats. Planning/capacity transition and roster generation are separate PRs.

### 5. Claude and Codex execute real work

- **Exists today: partial.** Concrete Claude and Codex engineer bundles exist (`program/agents/crew_alpha/agents/eng_claude/config.yaml:1-16`, `program/agents/crew_alpha/agents/eng_codex/config.yaml:1-28`), and fanout calls either agent after acquiring a slot (`program/agents/crew_alpha/skills/fanout/SKILL.md:18-45`). #13 prevents the lead from making that child dispatch, so this path is not executable today.
- **Nightshift-specific:** execution assumes linked worktrees under `.worktrees` (`program/agents/crew_alpha/skills/fanout/SKILL.md:18-27`). On this box those share the read-only common Git metadata, so Codex cannot fetch (#101), and work in the shared checkout can be silently lost (#89/#98). The agent prompts also call the PATH copy of `nightshift-ctl` (`eng_claude/config.yaml:31-34`), contrary to the verified #42 constraint.
- **Smallest useful PR:** implement a degraded dispatcher that sends one Claude task to `platform` and one Codex task to the top-level `h1_eng_codex` seat, in separate independent clones, with execution receipts and explicit vendor-down/degraded outcomes. Runtime activation and a successful smoke execution receipt from both seats are prerequisites. Every control-plane call must use the run bundle's `./bin/nightshift-ctl`; no nested `bwrap` probe or wrapper may run inside Codex (#100).

### 6. Implementation

- **Exists today: partial.** Engineer contracts require scoped edits, focused verification, commit, push, and a PR (`eng_claude/config.yaml:18-44`; the Codex equivalent starts at `eng_codex/config.yaml:30`). There is no safe workspace allocator/publication path. ADR-0009 remains proposed and explicitly forbids implementation before its readiness gate (`docs/decisions/ADR-0009-state-writer-commit-guard.md:361-400`).
- **Nightshift-specific:** the present workflow creates linked worktrees from a checkpoint (`program/agents/crew_alpha/skills/fanout/SKILL.md:18-27`); ADR-0009 records that `/srv/project` and linked worktrees cannot fetch while an independent clone can (`docs/decisions/ADR-0009-state-writer-commit-guard.md:28-30`, `docs/decisions/ADR-0009-state-writer-commit-guard.md:328-330`).
- **Smallest useful PR:** add an independent-clone task workspace allocator and CAS publication guard: explicit repository URL, expected remote base SHA, task branch, allowlisted paths, clean index, and push refusal if the expected SHA moved. Do not implement ADR-0009 by implication; this narrow H1 allocator needs its own approved acceptance criteria and regression test, or waits for ADR-0009's eight preconditions.

### 7. Independent review

- **Exists today: partial.** Reviewers are read-only and judge only the diff and contract (`program/agents/crew_alpha/agents/reviewer_codex/config.yaml:22-43`); cross-crew review routes to a different crew and records the verdict (`program/skills/cross-crew-review/SKILL.md:47-70`, `program/skills/cross-crew-review/SKILL.md:92-95`). With one crew it explicitly has no cross-crew review (`program/skills/cross-crew-review/SKILL.md:28-29`). #13 blocks lead-to-reviewer dispatch.
- **Nightshift-specific:** the default assumes the shipped two-crew rotation (`program/skills/cross-crew-review/SKILL.md:31-44`) and repository-local `.omni/reviews.md`; there is no generic PR event/identity gate Release can query independently.
- **Smallest useful PR:** add a Program-routed review dispatch that pins the PR head SHA, gives only diff/criteria/ADR, requires an other-vendor reviewer, and posts/stores a structured verdict bound to that SHA. `h1_reviewer_codex` reviews `platform` work; `staff` reviews `h1_eng_codex` work. For one active crew, these independent shared reviewers are sufficient for H1 and the missing cross-crew property must be reported.

### 8. QA

- **Exists today: partial.** The QA seat defines test planning, integration/e2e execution, cases, and a report (`program/agents/crew_alpha/agents/qa/config.yaml:20-45`); fanout requires QA and review clean before readiness (`program/agents/crew_alpha/skills/fanout/SKILL.md:59-67`). #13 blocks the actual lead dispatch, and QA is Codex-only in the shipped crew.
- **Nightshift-specific:** test artifact names are generic, but neither target test commands nor required CI jobs are discovered. Nightshift's own DoD hardcodes its validator, repository, workflow, branch, and required jobs (`docs/prd/DEFINITION-OF-DONE.md:21-30`, `docs/prd/DEFINITION-OF-DONE.md:42-47`).
- **Smallest useful PR:** dispatch the top-level, Program-reachable `h1_qa` seat against the pinned PR SHA using commands declared during discovery; store a structured pass/fail receipt. Runtime activation and a successful `h1_qa` smoke receipt are prerequisites. If local full-suite execution is sandboxed or needs nested bwrap, push a temporary candidate branch and use CI instead; local sandbox results never gate the merge.

### 9. Release

- **Exists today: partial.** A dedicated Codex Release seat is the sole default-branch writer and has eligibility, candidate, test, merge, post-merge verification, and revert instructions (`program/agents/release/config.yaml:20-41`, `program/agents/release/config.yaml:60-95`). It is a prompt procedure, not a machine-enforced controller, and its local full-suite gate conflicts with the live sandbox rule.
- **Nightshift-specific:** it says every branch reaches `main` (`program/agents/release/config.yaml:21-23`), shells to PATH `nightshift-ctl` (`program/agents/release/config.yaml:73-83`), and assumes tags and release notes suit every target. It does not discover branch protection, merge method, CI workflow, or default branch.
- **Smallest useful PR:** parameterize Release with repository, discovered default branch, immutable candidate SHA, and required CI checks; push a temporary candidate branch, wait for GitHub CI, and refuse unless the structured review/QA receipts match that SHA. Only Release gets merge authority.

### 10. Merge to default branch

- **Exists today: partial.** Release instructs `gh pr merge --merge`, task integration recording, tagging, and never resetting history (`program/agents/release/config.yaml:73-95`). No code proves that only Release can invoke the merge, copes with repository merge-policy differences, or atomically checks the head/default SHAs immediately before merging.
- **Nightshift-specific:** the prose repeatedly names `main`, uses Nightshift's issue accounting, and assumes merge commits/tags (`program/agents/release/config.yaml:78-93`). H1 may target `master`, squash-only, or protected/check-required repositories.
- **Smallest useful PR:** add a Release-only merge command that resolves the target's default branch and allowed merge method, verifies PR head and default SHA plus required checks immediately before the GitHub API call, records the returned merge SHA, and deletes the temporary candidate branch. Use explicit SHAs, never `FETCH_HEAD`.

### 11. Post-merge verification

- **Exists today: partial.** Release says rerun the suite on the default branch and revert its own merge if red (`program/agents/release/config.yaml:84-95`); `finish` also verifies criteria against merged default-branch code (`program/skills/finish/SKILL.md:14-34`). The local rerun is unsafe as a sandbox merge gate and there is no CI watcher bound to the merge SHA.
- **Nightshift-specific:** it names `main`; the checked-in DoD queries `tushardhara/nightshift`, `main`, and `tests.yml` (`docs/prd/DEFINITION-OF-DONE.md:42-47`).
- **Smallest useful PR:** wait for required default-branch CI at the exact returned merge SHA and store PASS/FAIL. On failure, Release may use a narrow audited revert exemption: the branch must contain exactly one `git revert` of the recorded merge SHA, Release emits a `revert` receipt naming the failed merge/checks and revert head, and the controller verifies the diff is exactly that inverse before merging under repository protections. Normal review/QA receipts and a green-base precondition do not apply to this emergency path; the revert PR's own available checks still must complete, and every exemption use is included in the end-of-run report. It must never deploy, migrate, rotate credentials, force-push, or reset.

### 12. Next task / clean completion

- **Exists today: partial.** The Program prompt says never idle, reconcile on an hourly heartbeat, and stop after every DoD condition is verified (`program/config.yaml:385-410`). `finish` defines completion/exception/blocked states and heartbeat deletion (`program/skills/finish/SKILL.md:67-87`). Control-plane runs record child executions, not a finite project lifecycle (`bin/nightshift-ctl:1330-1350`); `current_run_id` has set/read but no terminal transition (`bin/nightshift-ctl:1270-1283`).
- **Nightshift-specific:** recovery is an hourly scheduled prompt over one `.omni` tree (`program/config.yaml:388-404`), not a project-keyed scheduler. A stale completed run can therefore be rediscovered unless prompt behavior is perfect.
- **Smallest useful PR:** use the lifecycle increment's persisted project-run state machine with `RUNNING`, `WAITING`, `BLOCKED`, `DONE`; dispatch the next ready task after verified merge; and emit an end-of-run report containing stage receipts, vendor-by-stage execution, PR/merge/check SHAs, degradations, reverts, and terminal outcome. The lifecycle must already have created a heartbeat keyed by repository and run ID. On DONE disable/delete **only that exact heartbeat**, refuse resurrection, and preserve its final receipt; never touch Nightshift's org heartbeat. Checkpoint creation must always receive the explicit `--sha`; `--ref` is metadata/containment only because the live CLI otherwise derives stale HEAD (`bin/nightshift-ctl:929-968`).

## Ordered H1 increments

Each numbered capability is an ordered dependency boundary. Where an item explicitly names multiple PRs or an executed preflight, those artifacts remain separately reviewable and all must complete before the next dependent item.

0. **Executed target/environment preflight; no wave may be cut before its receipts exist.** Create the fresh private target repository and seed its default branch with the bounded brief scaffold and an already-green CI workflow. From the real execution sandboxes, prove and publish four run-independent receipts: (a) push from an independent clone, (b) clone/fetch/push for the foreign target origin without `--reference`, (c) `platform` creating and writing a clone outside `/srv/project`, and (d) `gh repo create --private` authorization plus a successful private-repository Actions run. Record repository identity, default branch, check names, actor/seat, command outcome, and immutable SHAs. Also verify no environments, secrets, webhooks, external actions, or publication workflows exist and record the quarantine/archive/delete procedure. Any failure stops planning; it is not discovered at 03:00 in an unattended run.
1. **Repository/run descriptor and startup record.** Define the authoritative repository URL, default branch, required checks, brief, run ID, private run root, and pinned starting SHA. Discover and validate the preflight seed; do not create a clone here.
2. **Generic execution and artifact receipt schema.** Define run/repository/seat/vendor/stage, command or artifact identity, input and output SHAs, outcome, timestamp, and degradation fields. Discovery, seat smoke tests, implementation, review, and QA extend this one schema rather than inventing parallel formats.
3. **Runtime roster overlay, activation, and reload.** First make the running service load a versioned run-local/overlay roster, with a defined restart/reload procedure and an activation receipt proving the expected bundle SHA. This depends on resolving the installed-bundle staleness in #107. Then add top-level, Program-reachable `h1_eng_codex`, `h1_reviewer_codex`, and `h1_qa` bridge seats. Before any dependent increment, execute `platform`, `h1_eng_codex`, `h1_reviewer_codex`, and `h1_qa` once through Program and store successful seat/vendor receipts. A config diff or binary/login probe is not proof. `architect` does not implement and `release` stays merge-only.
4. **Independent-clone workspace allocator plus narrow CAS publication guard.** Must precede any product-agent write because #89/#98 make shared-checkout work disposable and linked worktrees do not restore Codex fetch. It consumes the preflight's proven foreign-origin and push capabilities.
5. **Finite repo/run lifecycle and run-keyed heartbeat.** Add idempotent `RUNNING/WAITING/BLOCKED/DONE` transitions keyed by repository plus run ID, create a recovery heartbeat keyed the same way, define restart ownership, and prohibit operations against another run's heartbeat. This must exist before unattended dispatch.
6. **Executable discovery with Claude/Codex receipts.** Split the discovery driver from the already-merged receipt primitive. It establishes target DoD, test commands, independent seams, checks, and fresh successful execution by both vendors; otherwise it records an explicit degraded state.
7. **`DISCOVERED -> PLANNED` transition and capacity validation.** Validate discovery artifacts, choose independent tasks and `active_crews <= min(independent seams, worker capacity, vendor capacity)`, persist assignments, and pin the plan SHA. This PR does not generate roster files.
8. **Atomic run-local crew generation.** Consume the activated overlay/reload mechanism from item 3 and the assignments from item 7; generate lead-only crew instances, registries, backlog, and ownership atomically. While #13 remains, record the exact degraded top-level worker routes.
9. **#13-degraded implementation dispatcher.** Send the Claude task to `platform` and the Codex task to `h1_eng_codex`, each in its own allocator-created clone. Require the item 3 smoke receipts, planned assignments, scoped authority, CAS publication, and implementation receipts. This is the first product implementation and proves both vendors without using `architect` or `release`.
10. **Pinned-SHA independent review and QA dispatches.** Two independent PRs may now run in parallel because item 2 fixed their receipt envelope: `h1_reviewer_codex` reviews `platform` work, `staff` reviews `h1_eng_codex` work, and `h1_qa` runs discovery-declared tests against each pinned PR head. Local sandbox results never substitute for required CI.
11. **Parameterized Release candidate and CI gate.** Require structured review and QA receipts bound to the immutable candidate SHA plus the preflight-discovered required checks. Push a temporary candidate branch and use GitHub CI; do not use a sandbox-local full suite as the merge gate.
12. **Release-only, SHA-checked default-branch merge.** Resolve the allowed merge method, verify PR head/default SHAs and required checks immediately before the GitHub API call, record the returned merge SHA, and delete the candidate branch. Only `release` may invoke it.
13. **Exact-merge-SHA verification and audited revert exception.** Watch default-branch CI at the returned merge SHA. On failure, admit only the narrowly verified single-commit inverse described in stage 11 and issue a dedicated revert receipt; bypass only normal review/QA and green-base preconditions, never identity, exact-diff, repository-protection, or audit checks.
14. **Next-task advancement, end-of-run report, and clean completion.** After verified merge, dispatch the next ready task or emit the owner-readable report and transition to DONE. Disable only the exact repository/run heartbeat created by item 5, preserve its final receipt, and prove a completed run does not restart.
15. **Restore real crew-child dispatch for #13 on the same roster foundation.** Sequence this after item 3 rather than running it as independent roster work. Prove by execution that a crew lead dispatches PM, both engineer types, QA, and reviewer. The bridge seats retire only after this succeeds; H1 remains explicitly degraded until then, even if items 0-14 complete.

## H1 target repository

Use a **fresh private repository under the same GitHub account**, not an existing third-party repository. Give it a bounded, non-deploying brief such as: “Build a small standard-library CLI that reads a JSON task file, validates it, prints a deterministic Markdown summary, and includes unit tests plus CI.” Before increment 1, seed its default branch with a README, license, and a CI workflow that has already completed green; record the stable required-check names in preflight receipts. An empty repository is not an H1 target because review, merge, and post-merge gates would have nothing trustworthy to enforce.

This is the reversible choice: Nightshift may create branches, PRs, issues, commits, tags, and merge to the default branch without touching users, production data, credentials, package registries, or upstream maintainers. Keep it private during H1, configure no external webhooks/environments or deployment secrets, restrict Actions to workflows defined in the repository, and forbid package publication. Preflight records a named quarantine procedure (disable Actions and archive while preserving receipts) and an explicit owner-authorized deletion procedure; unattended agents do not delete the repository. A failed run is quarantined and preserved for forensics by default.

The cost is weaker evidence: it proves greenfield repository portability and the whole unattended delivery loop, but not adaptation to a mature codebase, unfamiliar conventions, existing branch protections, flaky CI, or third-party contribution rules. That is appropriate for H1; H4 should supply the different-stack/mature-repository proof.

An existing third-party repository adds realism but introduces things Nightshift cannot reliably undo: an accidentally public issue/PR/comment or notification, exposure of proprietary prompt/state in a public artifact, upstream reputation damage, acceptance of contribution/licensing terms, triggering external CI or paid integrations, and—if write access exists—changes other users may consume before a revert. Reverting Git does not recall notifications, erase forks/caches, undo an external workflow side effect, or retract a published package. A fork reduces write risk but still creates public artifacts and does not test control of the real repository. For H1, none of that extra blast radius buys evidence needed before the basic loop works once.
