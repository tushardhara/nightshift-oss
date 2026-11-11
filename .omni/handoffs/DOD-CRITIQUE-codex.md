# Blind cross-vendor critique of `docs/prd/DEFINITION-OF-DONE.md`

**Author:** `architect` (Codex) — the other vendor from the DoD's author (`product`, Claude).
**Inputs given:** `.omni/brief.md` and the draft definition of done, and nothing else. Not the
PRD, not product's reasoning — the critique judges the artifact, not the author's frame.
**Status:** delivered in chunks (the transport silently truncates long messages; see
`.omni/tech-debt.md`). Findings 1–44 below; the tail is still arriving.

**`product` owns the reconciliation.** Accept, or reject with one line of reasoning per
finding — never silently ignore one. The DoD's changelog records what this critique changed.
`product` remains the single author of the final text.

---

## Overall verdict

> Reject as binding. The draft covers the brief by topic, but it is not a reliable fixed
> point. Several checks are vacuous or self-attested, some requirements contradict the brief,
> and several conditions prescribe speculative implementation work before the audit has
> established that work is necessary. With QA and release unavailable, it is unfinishable as
> written. Its dominant risk is **gilding, not under-delivery**.

---

## Findings

1. **Conditions 1–4 — The four reported bugs are covered clearly.** Each owner-reported failure has a dedicated observable behavior and regression-test target. Keep this structure, but repair the specific problems below.

2. **Condition 1 — The test proves behavior with a stub, not necessarily the actual failing pipeline.** "Drives the real discovery code" is weakened by replacing the producer, and the condition does not require demonstrating failure at `a73ba88`. Fix: require the exact test command, execution against both baseline and fixed revisions, and recorded baseline failure evidence.

3. **Condition 2 — "No `/usr/local/go` present" is materially broader than a safe unit-test fixture.** It is unclear whether this means filesystem isolation, command stubbing, or mutation of the host. Fix: name the isolation mechanism and require the test to prove it cannot inspect or modify the host `/usr/local/go`.

4. **Condition 3 — "Establishes a usable user manager session" assumes bootstrap can and should manufacture one.** The brief only requires bootstrap not to assume that `systemctl --user` works. A safe fix could instead use a system service, ensure linger before switching users, or stop with preflight instructions. Fix: state the required outcome — successful unattended service setup on the target host — without preselecting session establishment.

5. **Condition 3 — The promised rollback property is undefined.** "Does not leave systemd units half-installed" names neither the relevant units nor the state that constitutes half-installation. Fix: enumerate the files, enablement states, and commands that verify absence or a clean retryable state.

6. **Condition 4 — It hard-codes a potentially false definition of Program activity.** Registry, ledger, STATUS, and report writes are called "genuine" without establishing that only a live Program can cause them. A timer or worker could write these too. Fix: require a Program-owned heartbeat or control-plane liveness record with writer identity and age semantics, then test that exact signal.

7. **Condition 4 — The `grep` does not settle "never restarts a service."** It catches only a narrow spelling and only direct `systemctl` calls. Wrappers, variables, `service`, `loginctl`, or delegated commands evade it; comments can also match. Fix: test watchdog execution with an instrumented command boundary that fails if any service-management mutation is attempted.

8. **Condition 5 — The evidence-table check is vacuous and does not establish completeness.** `NF<5` counts whitespace-separated fields rather than Markdown cells; `^\| tests/` need not match the listed IDs; it does not compare expected IDs, verify commands, or prove output is a failure. Fix: use a machine-readable manifest and a validator that compares the exact expected test IDs, requires baseline SHA, non-zero result, command, and captured output.

9. **Condition 5 versus Condition 14 — Confirmed bugs are allowed to remain unfixed.** Condition 14 permits any `CONFIRMED-BUG` to be disposed into tech debt, while the brief requires every confirmed bug to receive a regression test and smallest safe fix. Fix: every confirmed bug must point to an integrated fix and pre-fix regression evidence. Only `ACCEPTED-RISK` may point to tech debt.

10. **Condition 6 — The command does not run from a clean clone.** A read-only bind mount of `$PWD` exposes the current checkout, including untracked files and local state. Fix: have the rehearsal clone or archive the exact tested commit into an empty container workspace, and assert the tree is clean and `/srv/nightshift` absent.

11. **Condition 6 — "No network credentials" is not the same as "no network dependency."** A real Go installation normally downloads content, while the four advertised `PASS` lines may be produced entirely by stubs. Fix: state whether network access is permitted and distinguish a hermetic regression rehearsal from an actual package/toolchain installation rehearsal.

12. **Condition 7 — The minimum cases do not prove "every FAIL is real."** Four fixture cases cannot establish that all current and future FAIL branches are necessary or actionable. Fix: enumerate every preflight check in a manifest and require positive and negative tests for each one, including its exit status and remediation message.

13. **Condition 8 — The arithmetic and wording are false.** The file does not name 22 new unit tests: conditions 1–4 name 10, conditions 15–18 name four, and conditions 20–21 name two. Other classes have unspecified test counts. "At least 105" therefore does not follow from "83 baseline + ≥22 named." Fix: remove the count target and verify an explicit required-test manifest, or provide the actual enumerated 22 tests.

14. **Condition 8 — The CI checks do not prove the claimed CI policy.** Merely finding jobs in YAML and seeing the latest workflow succeed does not prove both jobs ran, that the run corresponds to the current tip, or that the workflow triggers on every push and PR. Fix: query the run's `headSha`, conclusion, and individual job conclusions, and statically validate the exact trigger configuration.

15. **Conditions 9–13 — Audit-topic coverage is complete, but audit quality is not.** All 13 areas in the brief map somewhere: duplicate work, deadlocks, claims/leases, slots, crash recovery, vendor limits/exhaustion, 3–7 day operation, security, GitHub workflow, release gates, coverage, complexity, and installation. Nothing from that list is entirely unmapped. The problem is that table shape is being mistaken for audit validity.

16. **Conditions 9–13 — The generic audit validator can pass incomplete or fraudulent documents.** Repeated `Q1` rows can satisfy the count; verdict words can appear in evidence text; multiple verdicts can appear; missing citations are not detected; question numbers need not be complete or unique. Fix: validate structured records with exact IDs `Q1…Qn`, exactly one permitted verdict, and non-empty evidence of an allowed type.

17. **Conditions 9–13 — `path:line` is not durable evidence.** Line numbers drift with later fixes, and a citation alone does not prove runtime behavior. Fix: require commit-qualified permalinks or `path + symbol + SHA`, and command output for behavioral claims.

18. **Condition 9 — The deadlock question is not decidable from the requested evidence.** "Is there any order…" is an unbounded universal claim that a table row and one citation cannot settle. Fix: require a documented lock-order/state-machine analysis covering every acquisition path, plus bounded adversarial tests.

19. **Condition 10 — "Sleeps exactly once" and subscription exhaustion behavior are underspecified.** There is no observation window, durable event identity, or definition of "terminating behavior." Fix: specify state transitions, persisted timer identity, maximum retries, terminal state, and exact queries/tests.

20. **Condition 11 — It asks whether enforcement exists but does not require missing enforcement to be added.** The audit can answer "no enforcement" as `CONFIRMED-BUG`, then condition 14 can park it in tech debt. Fix: classify protected brief requirements as release-blocking invariants whose confirmed violations must be fixed.

21. **Condition 12 — Security coverage is incomplete relative to its own question.** Q1 asks about logs, journald, alerts, and PR/issue comments, but condition 21 tests only three output-producing components. Fix: inventory every external/operator-visible sink and test or inspect each source-to-sink path.

22. **Condition 13 — "Every failure path names the command that fixes it" is often impossible or unsafe.** Some failures have multiple causes or no single corrective command. This also fights least-privilege guidance by encouraging copy-paste root commands. Fix: require actionable diagnosis and a documented remediation reference; require an exact command only where one safe, deterministic command exists.

23. **Condition 14 — FINDINGS completeness has no executable validator.** "No empty cell" and "every finding id … also appears" are prose, not commands, and naive grep would not ensure uniqueness or valid cross-references. Fix: add a parser that checks unique IDs, source existence, valid disposition types, and resolvable PR/tech-debt links.

24. **Conditions 15–18 — These prescribe major new simulation infrastructure before the audit justifies it.** An accelerated seven-day clock, crash injection, orchestration recovery model, and vendor scheduler harness are substantial product work absent from the brief's four confirmed fixes. Tests for confirmed audit bugs are required; speculative omnibus simulators are not. Fix: make these conditional outputs of confirmed findings, or replace them with a small, bounded control-plane concurrency/recovery suite based on risks actually demonstrated.

25. **Condition 15 — Its duplicate-work assertion is too weak.** Preventing simultaneous live claims and preventing the same `(task, worker)` pair from repeating still allows the same task to be executed sequentially by different workers without a legitimate recovery transition. Fix: define the task lifecycle and assert at most one authorized execution generation per task, with explicit recovery-generation records.

26. **Condition 15 — "Equivalent to seven days" is undefined.** Advancing a clock does not exercise process survival, filesystem growth, timer behavior, journal rotation, credential expiry, or host reboot effects. Fix: name exactly which seven-day risks simulation covers and do not present it as equivalent to elapsed operation.

27. **Condition 16 — It contradicts the documented serialize-lease invariant.** The constitution says serialize leases hold until merge or drop and early release is refused. Requiring every crashed serialize holder to become acquirable automatically risks weakening that safety boundary. Fix: require safe recovery through an authorized, auditable task transition such as verified drop/reassignment — not generic TTL or reap.

28. **Condition 17 — It tests a control-plane response, not unattended sleep and resume.** `all_cooling` and a claimable task do not prove exactly one timer is scheduled, that the Program sleeps, or that it resumes itself. Fix: observe persisted scheduler state and actual dispatch attempts across the timer boundary.

29. **Condition 18 — "Exactly one owner" is undefined across task states.** Completed, dropped, queued, recoverable, and in-flight tasks need different ownership expectations. Fix: define the state-to-owner invariant and compare stable task IDs and execution generations before and after restart.

30. **Condition 19 — The fallback does not prove that an alert occurred.** A local journal line when GitHub is unauthenticated proves logging, not operator notification. Fix: either require delivery to the configured alert channel or rename the condition to "alert attempt recorded" and separately test delivery configuration.

31. **Condition 19 — It requires operator intervention contrary to the autonomous brief.** Stopping the Program and manipulating writes is a named manual step, yet the owner said to continue without waiting unless secrets, destruction, or weakened safety are involved. Fix: automate the drill safely in an isolated installation or classify the real-host drill as optional follow-up evidence.

32. **Condition 19 — The ≥72-hour requirement is not mechanically settled.** Two timestamps in a self-authored Markdown file can satisfy the stated artifact without proving continuous observation, and "continuous" has no sampling or uptime definition. Fix: require machine-generated heartbeat/watchdog records over the interval, with maximum allowed gaps and a validator.

33. **Condition 20 — An ADR is incorrectly treated as permission to weaken a protected boundary.** The brief says not to weaken listed controls merely to ease the run. "Changed if recorded in an ADR" does not satisfy that. Fix: require semantic non-regression tests for each protected boundary; an ADR may explain a change but cannot waive the invariant.

34. **Condition 20 — The inspection command cannot settle the claim.** It only shows selected paths and then relies on subjective close-out inspection. It misses controls elsewhere and does not establish effective permissions. Fix: enumerate authoritative control files and run behavioral denial tests against each protected capability.

35. **Condition 21 — Token-shape scanning is neither sound nor complete.** It can miss arbitrary secret formats and falsely flag benign strings. It also ignores PR bodies, issue comments, CI logs, journals, and agent logs despite the audit requirement. Fix: inject unique sentinels at every credential source and assert their absence from an enumerated sink corpus.

36. **Condition 22 — The shell-syntax requirement is not executable as written.** "Every script in `deploy/` and `bin/`" may include non-Bash executables, and no actual command enumerates Bash scripts safely. Fix: identify Bash files by manifest or shebang and run `bash -n` on that exact set.

37. **Condition 22 — The `grep` cannot verify explanatory comments.** It merely prints added `|| true` lines; it cannot associate a comment with each hit, and zero output would also pass if the command itself failed unless status handling is specified. Fix: forbid new `|| true` in critical paths through lint rules, with explicit annotated exceptions parsed by the linter.

38. **Condition 23 — The repository cannot prove every merge came through a PR.** Git history alone does not establish PR metadata, reviewers, or historical body linting; `.omni/reviews.md` is self-attestation. Squash and rebase merges also break "every merge commit" language. Fix: query GitHub for every commit in the range, require an associated merged PR, retrieve its historical body and reviews, and validate reviewer identities and crew/vendor separation.

39. **Condition 24 — The proposed checks cannot prove the frozen controller was never touched.** Git logs in `/srv/project` cannot observe writes to `/srv/nightshift`; absence from the ledger is especially not evidence. Fix: acknowledge historical non-modification is unprovable after the fact. For the remainder of the run, use a read-only mount, filesystem audit record, or before/after immutable snapshot supplied by the environment.

40. **Condition 24 — It conflates `/srv/nightshift` with `/home/nightshift/nightshift-runtime`.** The brief freezes only `/srv/nightshift`; the second path is introduced without owner authority or explanation. Fix: remove the additional frozen path unless it is documented as an explicit assumption and justified.

41. **Unnumbered quality bar — The 500 ms p95 requirement has no settling method.** It is a binding requirement outside the claimed 24 and gives no workload, warmup, command mix, database size, sample count, or measurement command. Fix: either remove it as scope creep or promote it to a numbered, reproducible benchmark condition justified by capacity requirements.

42. **Document-wide — The file falsely claims every condition has an exact settling command.** Conditions 3, 7, 9–14, 19, 20, 22–24 include prose inspection, underspecified test invocation, or no complete command. Fix: attach a single executable close-out validator that reports each condition independently, with manual exceptions narrowly identified.

43. **Document-wide — "On the default branch" is defined incorrectly.** The latest successful workflow may target an older SHA than current `main`; it does not show that a named local test passed at the tip. Fix: compare GitHub's current `main` SHA with the workflow run's `headSha` and check the required job/test artifact for that SHA.

44. **Document-wide — Normative test names optimize for labels, not evidence.** Exact names can exist while tests are skipped, empty, mocked beyond relevance, or disconnected from production paths. Fix: keep stable requirement IDs, but verify assertions, production-path coverage, no skips, and baseline failure rather than prescribing Python class names.

45. **Document-wide — The referenced change-control section is missing.** The introduction points to "Adding to this list, at the bottom," but no such section exists. Changelog revision is also referenced without a changelog format. Fix: add the promised revision procedure, authority, evidence threshold, and immutable history rule before binding the file.

46. **Document-wide — Architecture capacity is allowed to override the owner-facing fixed point later.** "Those win" lets a downstream artifact silently force changed performance requirements, despite this file being the fixed point. Fix: architecture may propose a revision, but the DoD must record the explicit revision and rationale before the new numbers bind.

47. **Document-wide — Baseline facts are not identified as assumptions.** The 83-test count, 18-second duration, day-one topology, branch/workflow name, availability of GitHub, and behavior of existing leases are asserted without reproducible evidence in this file. Fix: label each as verified baseline or assumption, include its command/output and SHA, and define what happens if verification disagrees.

48. **Scope creep — Several requirements exceed the owner's brief without a confirmed finding.** Alert rearm policy, exact alert interval semantics, blanket install idempotency, mandatory preflight expansion, a 72-hour observation, exact test counts/names, and the 500 ms p95 target are new product requirements. Fix: retain only what is necessary to prove the four bugs and close confirmed audit findings; move optional hardening to follow-up findings.

49. **Handover gap — There is no condition for a usable final handover.** A stranger could see green checks yet lack a concise statement of changes, remaining risks, rollback instructions, operational changes, and whether reinstall is needed. Fix: require `docs/HANDOVER.md` with merged fixes, evidence, accepted risks, operator actions, rollback notes, and links to audit/findings records.

50. **Handover gap — There is no explicit traceability validator from each brief promise to evidence.** Audit tables and named tests are scattered, so omissions can survive even when every local check passes. Fix: add a machine-validated matrix mapping the four bugs and 13 audit areas to conditions, evidence, findings, tests, and integrated PRs.

## Decidability by condition

A stranger could decide conditions **1 and 2** after adding exact invocations and isolation details. Condition **4** is close, but only after defining an authentic Program-owned signal. The audit documents in **9-13** have clear subject coverage, but their present validators do not settle correctness.

As written, the conditions that fail the stated "repo plus this condition alone" test are:

- **3:** undefined session-establishment mechanism and half-installed state.
- **4:** ambiguous Program authorship and ineffective restart grep.
- **5:** broken completeness validator.
- **6:** not actually a clean clone; unclear network/real-install semantics.
- **7:** "every FAIL is real" is unbounded.
- **8:** incorrect count and inadequate GitHub query.
- **9-13:** table syntax does not establish complete, unique, evidenced answers.
- **14:** no complete validator; contradictory dispositions.
- **15:** undefined accelerated equivalence and incomplete duplicate invariant.
- **16:** undefined authorized recovery and conflict with serialize semantics.
- **17:** does not observe actual scheduling/sleep/resume.
- **18:** ownership invariant undefined by lifecycle state.
- **19:** self-recorded/manual/external evidence and alert fallback mismatch.
- **20:** subjective inspection and ADR waiver.
- **21:** incomplete sink inventory and unreliable token-shape criterion.
- **22:** incomplete commands and unverifiable comment rule.
- **23:** no exact GitHub query or trustworthy cross-crew verification.
- **24:** historical external non-modification is not observable from the repo.

## Size and finishability

Twenty-four is not inherently too many, but these are not 24 small acceptance checks. Conditions 9-13 contain 29 audit questions; 15-18 demand a new simulation/recovery test platform; 19 demands a real-host drill and 72 hours; 20-24 add another security and process audit. That is closer to a multi-milestone program than a bounded 3-7 day self-dogfood pass.

With only four worker slots and QA and release unavailable, condition 23 cannot be satisfied under the project's own workflow, and nothing can reach the required integrated/default-branch state. That is a hard impossibility, not merely aggressive scheduling.

I would cut or defer:

- **15-18** as predetermined omnibus implementations. Replace them with targeted regression tests only for bugs confirmed by audits.
- **19's 72-hour and operator-driven real-host drill.** Preserve an isolated automated silence test; make long observation follow-up evidence.
- **The >=105 test count in 8.** Evidence quality matters; raw count invites padding.
- **The 500 ms p95 requirement.** No owner request or capacity-derived justification is supplied.
- **Duplicative portions of 20-24.** Consolidate them into enforceable invariants and workflow provenance, while retaining the frozen-controller prohibition and credential-boundary checks.

Keep the four reported fixes, the five bounded audits, mandatory closure of every confirmed bug, pre-fix regression evidence, clean-clone installation rehearsal, protected-boundary non-regression, and a handover/traceability matrix.

The current draft risks gilding: it adds extensive infrastructure and ceremony while several core claims remain unverifiable. **The cure is not a weaker finish line; it is a smaller, harder, executable one.**

*(Critique complete — delivered `[END]`.)*
