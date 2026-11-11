# 439 — Nightshift Core must contain no project-specific values (portability guardrail)

Status: **frozen spec — ready for build.** This document is the review contract of
record for #439. It transcribes the FINAL acceptance criteria posted as the issue's
own comment (~21:45Z, 2026-08-30), which supersedes both the as-filed issue body's
AC-2/AC-3/AC-6/AC-7 and every draft that preceded it in the DoR thread. Do not treat
the issue body alone as the contract — read this file, which already reconciles the
two.

**Version: v2 (2026-08-30, measurement correction — see §9).** A build attempt
against v1 found that v1's AC-6 day-one numbers (51 entries / 48 AC-2 token hits /
10 debt / 41 structural) do not reconcile against the repo under any counting
convention tried, and that v1's AC-3 debt-group arithmetic (9 + 2 = 11) does not
match v1's own declared debt total (10). Per owner directive WA-020
(`.omni/working-agreements.md`), this is a measurable-fact correction, not a
re-opening of product scope: AC-2, AC-3, and AC-6 are revised below to the actual
measured values, under an explicit, reproducible scan-set/granularity/matching
rule (§9). AC-1, AC-4, AC-5, AC-7 through AC-12 are unchanged — measurement found
no contradiction in them. The v1 numbers are kept visible inline, marked
superseded, per this spec's own provenance convention.

**v3 addendum (issue #400/#443 landing in `bin/`).** Two new Core files --
`bin/nightshift_project_contract.py` (#443, the borrowed-project polyglot
detector) and `bin/nightshift_memory_bootstrap.py` (#400) -- legitimately
contain toolchain/runner-name tokens (`cargo`, `cypress`, `go test`,
`gradle`, `maven`, `npm`, `pytest`, `tests/run.sh`, `unittest`, `yarn`):
the entire point of #443's detector is to RECOGNIZE these tokens in a
*borrowed* repo, the opposite of the hardcoded-Python-assumption bug this
guardrail exists to catch; #400's bootstrap module names `cargo`/`maven`/
`pytest` only inside a comment documenting a generic build-output
directory convention. Both are AC-11 `structural` by the same reasoning
`deploy/**` already is (a polyglot detector needs the vocabulary of every
language it detects, the same way deploy needs to provision every runtime
it installs) -- classified `debt` would be wrong: there is nothing to fix
here, no assumption to migrate away from. AC-11's allowlist and AC-6's
day-one counts below are updated in place (13 more `structural` hits: 10
in `nightshift_project_contract.py`, 3 in `nightshift_memory_bootstrap.
py` -- `debt` is unchanged at 7, `structural` moves 19 -> 32, total 26 ->
39); the classified manifest at §9.3 and `tests/core_portability_
manifest.json` are the source of truth, not re-transcribed line-by-line
here.

This spec is self-contained: an engineer should not need to open the GitHub thread to
build against it.

## 1. Problem statement

Nightshift is meant to work on any project, but today pointing it at a new one means
editing Nightshift itself: project-specific toolchain knowledge is baked into Core
agent prompts (pytest guidance duplicated at
`program/agents/crew_alpha/agents/qa/config.yaml:104`,
`program/agents/crew_beta/agents/beta_qa/config.yaml:104`,
`program/agents/crew_alpha/skills/cross-review/SKILL.md:20`, and its crew_beta twin;
`tests/run.sh` named as *the* test suite at `program/agents/release/config.yaml:139`;
Rust/cargo and Docker assumptions at
`program/agents/crew_alpha/agents/eng_codex/config.yaml:59`), and review policy is a
hardcoded tuple — `CANONICAL_DOMAINS` at `bin/nightshift_review_receipts.py:33`, which
issue #403 asks to edit rather than configure, a symptom of the same disease. Because
there is no check for this today, whether a given change makes Nightshift more or
less portable is answered by opinion and discovered late — on the first repository
that isn't this one. **Done when:** a test fails when a project-specific value appears
in Nightshift Core without being recorded as known debt, and the amount of that debt
is a number that can only go down.

## 2. Final acceptance criteria (AC-1–AC-12)

Provenance is marked per criterion. Criteria marked **(revised)** or **(new)** are
transcribed verbatim from the issue's final comment (~21:45Z, 2026-08-30), which is
the superseding authority. Criteria marked **(unchanged)** are transcribed verbatim
from the issue as filed, and the final comment explicitly confirms they did not
change — the final comment is still the authority for the fact of "unchanged," even
though it doesn't restate their body text.

- **AC-1** (unchanged): a checked-in declaration names the Core path set —
  `bin/`, `deploy/`, `program/`.

- **AC-2** (revised v2, corrected from measurement — see §9): a test scans the
  **scan set** — Git-tracked regular files (`git ls-tree` type `blob`) under
  `bin/`, `deploy/`, `program/` as of the frozen base commit
  `b2f41cb29f9eed1613b8e7d6ef3291e52e3e913a`, obtained via
  `git ls-tree -r --name-only <sha> -- bin/ deploy/ program/` (92 files at that
  commit; see §9.1 for the full list) — **whole-file, no extension-based
  exceptions**, for at minimum: toolchain/runner names (`pytest`, `unittest`,
  `cargo`, `gradle`, `maven`, `npm`, `yarn`, `cypress`, `playwright`, `go test`),
  the concrete suite path `tests/run.sh`, and the review-domain literal
  `CANONICAL_DOMAINS`.

  **Matching semantics (pinned, v2):** literal, case-sensitive, fixed-string
  substring match against each file's raw text (not regex, not word-boundary, not
  comment-aware). A token "appears" if it occurs anywhere in the file's bytes,
  including inside comments or unrelated words/idioms — chosen as the simplest
  rule that matches this spec's own stated intent (§1: "a project-specific value
  literally appearing in a Core file") and consistent with §3's existing
  in-comment-still-a-hit state. Measured on this repo, this choice is
  non-load-bearing versus the alternatives: case-insensitive and word-boundary
  variants produce byte-identical file sets for every tracked token (§9.2) —
  except that substring matching is what correctly catches `cargo` inside the
  idiom "cargo cult" in both `handoff/SKILL.md` files, which is precisely why
  AC-11 has to carve those two files out as `structural` rather than have them
  silently not match.

  **Hit granularity (unchanged rule, restated for precision):** one manifest hit
  = one distinct (repo-relative file path, declared token) pair. Multiple
  occurrences of the same token in the same file count once. This is a general
  rule, not a `CANONICAL_DOMAINS`-specific carve-out: under it, `CANONICAL_DOMAINS`
  measures as 2 hits (one per distinct file it appears in — currently
  `bin/nightshift_review_receipts.py` and `bin/nightshift-ctl`), which is stated
  here only as a worked example of the general rule, not a special case.

- **AC-3** (revised v2, corrected from measurement — see §9): the exemption
  manifest gains a `category: "debt" | "structural"` field per entry. Only `debt`
  entries require an owning issue number (defaulting to `#439` itself for the
  **5** currently-unticketed test-framework/suite-path hits pending a follow-up
  issue — 4 `pytest` hits + 1 `tests/run.sh` hit, per §9.3's classified manifest;
  the 2 `CANONICAL_DOMAINS` entries own `#403`). Debt total: 5 + 2 = **7**, which
  matches AC-6's debt count below by construction (both are read off the same
  classified manifest, not separately hand-asserted — see §9.3).

  Superseded (v1, incorrect): "defaulting to `#439` itself for the 9
  currently-unticketed test-framework hits ... ; the `CANONICAL_DOMAINS` entries
  own `#403`" — v1 asserted 9 unticketed hits, which summed with the 2
  `CANONICAL_DOMAINS` hits to 11, contradicting v1 AC-6's own declared debt count
  of 10. Measurement (§9) finds 4 `pytest` hits, not 9 of some larger
  "test-framework" set — `unittest`, `gradle`, `maven`, `yarn`, `cypress`,
  `playwright`, and `go test` all measure zero hits in this repo (§9.2).

- **AC-4** (unchanged): The test fails on any hit in the Core path set that is not
  present in the manifest, and names the file and token in the failure.

- **AC-5** (unchanged): The test also fails when the manifest lists an entry that
  **no longer matches anything** — a stale exemption is removed, not left to rot.
  This is what makes the manifest shrink-only in practice.

- **AC-6** (revised v2, corrected from measurement — see §9): day-one manifest
  has **26 entries** (23 toolchain/runner-name hits — 4 `pytest` + 13 `cargo` + 6
  `npm`, with `unittest`/`gradle`/`maven`/`yarn`/`cypress`/`playwright`/`go test`
  all measuring zero — + 1 `tests/run.sh` hit + `CANONICAL_DOMAINS` × 2 files),
  of which **7 are `debt`** and **19 are `structural`**. Full classified manifest,
  measured under AC-2's pinned scan-set/granularity/matching rule against base
  commit `b2f41cb29f9eed1613b8e7d6ef3291e52e3e913a`, is at §9.3.

  Superseded (v1, incorrect): "**51 entries** (48 AC-2 token hits + `tests/run.sh`
  + `CANONICAL_DOMAINS` × 2 files), of which **10 are `debt`** and **41 are
  `structural`**" — two independent scans against real repo content (one over 93
  files, one over 105) each produced different totals from this and from each
  other (23/49/51 and 26/54/56 hit counts respectively), because neither scan's
  file set was pinned to Git-tracked files only — both picked up `__pycache__`,
  generated files, or other untracked worktree cruft. §9 pins the scan set
  mechanically (`git ls-tree`, blob entries only) so this doesn't recur.

- **AC-7** (revised): the test reports total entry count *and* the `debt` subset
  count specifically — the "debt can only go down" number is the `debt` count, not
  the total.

- **AC-8** (unchanged): Adding a project-specific token to a Core file in a fixture
  causes the test to fail — proven by a test that does this, not by inspection.

- **AC-9** (unchanged): `python3 tests/dod_validate.py` produces the same verdicts
  before and after this change. The guardrail must not perturb v1's measurement.

- **AC-10** (new, non-goal): this guardrail does **not** detect a worker inventing an
  undeclared toolchain against a project's declared/discovered commands — no such
  mechanism exists yet (that's #438's excluded build/test slice). No test in this
  change may assert or depend on it.

- **AC-11** (new): `category: "structural"` is valid **only** inside a closed,
  path-enumerated allowlist — `deploy/**`; the 3 sandbox `writable_roots` config
  files (`program/config.yaml`, `crew_alpha/config.yaml`, `crew_beta/config.yaml`);
  the 5 capability-disclosure prompt configs (`eng_codex`, `eng_claude`, `platform`,
  `beta_eng_codex`, `beta_eng_claude`); the 2 idiom files (`handoff/SKILL.md` ×2). A
  `structural` claim outside this allowlist fails closed. This is a mechanical path
  check, not semantic reasoning about intent — consistent with AC-4. Named, accepted
  residual: `deploy/**` is directory-wide, so a future genuine hardcode inside it
  could still claim `structural` — the remaining defense is that the claim is a
  visible, reviewable manifest entry with a written reason, never an invisible
  omission.

- **AC-12** (new): manifest regeneration is **diff-based, not replace-based** —
  unlike the `#245` precedent (`tests/host-uid-skip-manifest.json`, a flat
  regenerate-from-scratch list with no hand-set fields), `#439`'s manifest has
  hand-set `category`/`reason`/`issue` fields that a mechanical rescan cannot
  derive. Regeneration must preserve hand-set fields on surviving entries, land new
  raw hits as unclassified (which then fails AC-4 until a human assigns a category),
  and prune stale entries per AC-5.

## 3. Failure and empty states

Carried over unchanged from the issue as filed. Checked against the AC-2/AC-3/AC-6/
AC-7 revisions and AC-10/AC-11/AC-12 additions above — still accurate: none of the
revisions touch how the scanner handles a missing/empty/unparseable manifest, a
nonexistent declared path, or in-comment tokens, and AC-11's allowlist is a
classification-validity check layered on top, not a change to these states.

- **Manifest missing or unparseable:** the test fails closed. A guardrail that
  cannot read its own exemption list must not report success.
- **Manifest empty and no hits found:** the test passes and reports zero debt —
  distinguishable from a scan that failed to run.
- **A Core path in the declaration does not exist:** the test fails and names it,
  rather than silently scanning fewer files than it claims to.
- **A token appears only inside a comment explaining why it is forbidden:** still a
  hit. Exempt it explicitly rather than special-casing comments — a scanner that
  reasons about intent is a scanner that can be argued with.

## 4. Not included

Carried over unchanged from the issue as filed. Checked against the revisions —
still accurate: AC-3/AC-11's `category` field and AC-12's regeneration logic are all
manifest/test-side additions inside `tests/`; none of them require touching `bin/`,
`deploy/`, or `program/` content itself.

- **Removing any of the hardcoding.** Every hit above is recorded as an exemption
  and left in place. This item makes the debt visible and counted; separate items
  pay it down.
- Editing any agent prompt, `CANONICAL_DOMAINS`, or `docs/conventions.md`.
- Defining the Project Contract, or reading rules from one. This is the guardrail
  that will grade that work, not the work itself.
- Any change to `bin/`, `deploy/` or `program/`. This item touches `tests/` only.

## 5. Dependencies

Carried over unchanged from the issue as filed. Checked against AC-12's explicit
note that regeneration *diverges* from the #245 precedent in mechanism (diff-based,
not replace-based, because this manifest has hand-set fields #245's doesn't) — that
divergence is a build-time detail of how the pattern is adapted, not a new blocking
dependency; "Dependencies: None" still holds.

None. The mechanism already exists and should be reused rather than reinvented:
`tests/host-uid-skip-manifest.json` (an exact-id manifest derived by measurement, not
hand-written) together with `tests/run_suite.py`, which audits the actual skip set
against that manifest and fails the gate on any mismatch — built for #245. This is
the same shape applied to a different property.

## 6. Open questions (from issue as filed — resolved, non-blocking)

Carried for self-containment only; not re-derived here.

1. Should the token list live in the manifest file or in the test? Resolved:
   the manifest (per AC-3's schema), so adding a token to watch for doesn't require
   a code change.
2. Does `docs/` belong in the Core path set? Resolved: no — AC-1's final path set is
   `bin/`, `deploy/`, `program/` only.
3. (Surfaced during DoR, not in the original issue) AC-3's "every exemption needs an
   owning issue" is undischargeable at the true 5 unticketed test-framework/
   suite-path `debt` hits (measured §9.3; corrected in v2 from the DoR-time
   estimate of ~9) without opening a new issue this pass. Resolved: those entries
   default-own `#439` itself until a real follow-up issue is filed later —
   explicitly a non-blocking open question, not a new issue opened now (see §8).

## 7. DoR status

**READY** (`state:ready`). This is a pointer to the record, not a re-run of the
analysis:

- `.omni/decisions.md`, entry `## 2026-08-30 ~20:15Z — #439: DoR verdict on REVISED
  contract` (PR #441, merged) — round 1: two independent verifications (`pm`,
  `architect`), revised contract drafted.
- A round-2 entry (~21:30Z per the issue's own final comment) closes out `staff`
  adversarial round 2's findings; at the time this spec was written its bookkeeping
  PR (#444) was open but not yet merged into `origin/main` — the issue's final
  comment (~21:45Z) is the up-to-date authority regardless of that PR's merge
  state, and is what this spec transcribes.
- The issue's own final comment (`gh issue view 439 --json comments --jq
  '.comments[-1].body'`) is the primary source of record for AC-1–AC-12 above.

Two PM DoR rounds, two independent staff adversarial rounds, and one independent
architect read all converged on this contract as genuinely S-sized and buildable
inside `tests/` only. Do not re-open DoR on this item; a criteria change from here
needs a new visible contract version (see §8).

## 8. Scope reminder for implementation

- **File scope:** `tests/` only. No changes to `bin/`, `deploy/`, or `program/`.
- **Issue scope:** touches only #439. Do not implement or reopen #165, #400, #403,
  or #438. (#403 is referenced only as the existing owning issue for the
  `CANONICAL_DOMAINS` manifest entries, per AC-3 — do not edit anything under #403's
  own scope. #438 is referenced only as AC-10's non-goal context.)
- **No new issues.** The 5 unticketed `debt` entries (measured §9.3) default-own
  `#439` per AC-3; opening their eventual real follow-up issue is out of scope for
  this build.
- Any criteria or scope change discovered during implementation must come back to
  the PM for a new visible contract version before landing — not be resolved
  silently in the diff.

## 9. Measurement provenance for the v2 correction (AC-2/AC-3/AC-6)

This section is normative for AC-2/AC-3/AC-6 above, not commentary: it is what makes
them reproducible. Per owner directive WA-020, this was re-derived mechanically —
numbers were not preserved from v1, negotiated, or hand-asserted; they were read off
a classified manifest built by the commands below. Anyone re-running §9.1's command
against the same base commit and applying §2's pinned matching rule must get the
same 26/7/19 split.

### 9.1 Scan set

Frozen against the exact build base commit (checkpoint 28's sha):

```
BASE=b2f41cb29f9eed1613b8e7d6ef3291e52e3e913a
git ls-tree -r --name-only $BASE -- bin/ deploy/ program/ | sort
```

Cross-checked with `git ls-tree -r $BASE -- bin/ deploy/ program/` that every entry's
type column is `blob` (a regular tracked file) — no symlinks, submodules, or
directories in the set, and by construction no `__pycache__`, generated, or
untracked files, which is exactly what caused the prior two scans (93 files / 105
files) to disagree with each other and with this one. Result: **92 files**.

```
bin/nightshift-add-crew.sh
bin/nightshift-ctl
bin/nightshift-init.sh
bin/nightshift-release
bin/nightshift-tracker-init.sh
bin/nightshift-tracker-sync
bin/nightshift-workspace
bin/nightshift_bookkeeping_release.py
bin/nightshift_github.py
bin/nightshift_heartbeat_persistence.py
bin/nightshift_project.py
bin/nightshift_review_eligibility.py
bin/nightshift_review_plan.py
bin/nightshift_review_receipts.py
deploy/EXACT-SHA-CUTOVER.md
deploy/README-EC2.md
deploy/README.md
deploy/bootstrap.sh
deploy/cloud-init.yaml
deploy/docker-compose.postgres.yml
deploy/exact_sha_release.py
deploy/exact_sha_smoke.py
deploy/lib/deploy-lib.sh
deploy/nightshift-heartbeat.sh
deploy/nightshift-scheduled-run-detector.py
deploy/nightshift-scheduler-probe.py
deploy/nightshift-state-backup.sh
deploy/nightshift-watchdog.sh
deploy/omnigent-runtime-identity.py
deploy/omnigent-runtime.lock.json
deploy/policies/omnipods_policies.py
deploy/preflight.sh
deploy/providers/aws/README.md
deploy/providers/aws/cloud-init.yaml
deploy/providers/hostinger/README.md
deploy/provision-ubuntu.sh
deploy/run-exact-sha-host.sh
deploy/run-exact-sha-server.sh
deploy/scheduled-run-detector.env.example
deploy/systemd/nightshift-heartbeat.service
deploy/systemd/nightshift-heartbeat.timer
deploy/systemd/nightshift-scheduled-run-detector.service
deploy/systemd/nightshift-scheduled-run-detector.timer
deploy/systemd/nightshift-state-backup.service
deploy/systemd/nightshift-state-backup.timer
deploy/systemd/nightshift-watchdog.service
deploy/systemd/nightshift-watchdog.timer
deploy/systemd/omnigent-host.service
deploy/systemd/omnigent-server.service
program/agents/architect/config.yaml
program/agents/crew_alpha/agents/eng_claude/config.yaml
program/agents/crew_alpha/agents/eng_codex/config.yaml
program/agents/crew_alpha/agents/eng_codex/tools/python/nightshift_publish.py
program/agents/crew_alpha/agents/pm/config.yaml
program/agents/crew_alpha/agents/qa/config.yaml
program/agents/crew_alpha/agents/reviewer_claude/config.yaml
program/agents/crew_alpha/agents/reviewer_codex/config.yaml
program/agents/crew_alpha/config.yaml
program/agents/crew_alpha/skills/cross-review/SKILL.md
program/agents/crew_alpha/skills/design-debate/SKILL.md
program/agents/crew_alpha/skills/fanout/SKILL.md
program/agents/crew_alpha/skills/handoff/SKILL.md
program/agents/crew_beta/agents/beta_eng_claude/config.yaml
program/agents/crew_beta/agents/beta_eng_codex/config.yaml
program/agents/crew_beta/agents/beta_pm/config.yaml
program/agents/crew_beta/agents/beta_qa/config.yaml
program/agents/crew_beta/agents/beta_reviewer_claude/config.yaml
program/agents/crew_beta/agents/beta_reviewer_codex/config.yaml
program/agents/crew_beta/config.yaml
program/agents/crew_beta/skills/cross-review/SKILL.md
program/agents/crew_beta/skills/design-debate/SKILL.md
program/agents/crew_beta/skills/fanout/SKILL.md
program/agents/crew_beta/skills/handoff/SKILL.md
program/agents/oncall/config.yaml
program/agents/platform/config.yaml
program/agents/product/config.yaml
program/agents/release/config.yaml
program/agents/security/config.yaml
program/agents/staff/config.yaml
program/agents/ux/config.yaml
program/agents/ux/skills/design-review/SKILL.md
program/agents/ux/skills/design-spec/SKILL.md
program/config.yaml
program/review-risk.json
program/skills/allocate/SKILL.md
program/skills/cross-crew-review/SKILL.md
program/skills/discovery/SKILL.md
program/skills/finish/SKILL.md
program/skills/retro/SKILL.md
program/skills/roadmap/SKILL.md
program/skills/sync/SKILL.md
program/skills/tracker/SKILL.md
```

### 9.2 Per-token scan

For each AC-2 token, a literal case-sensitive fixed-string match (`grep -lF --
"$tok"`) against every file in §9.1, counting distinct files matched (not raw
occurrences — AC-2's hit-granularity rule):

```
for tok in "pytest" "unittest" "cargo" "gradle" "maven" "npm" "yarn" "cypress" \
           "playwright" "go test" "tests/run.sh" "CANONICAL_DOMAINS"; do
  grep -lF -- "$tok" $(git ls-tree -r --name-only $BASE -- bin/ deploy/ program/)
done
```

| token | files matched (= hit count) |
|---|---|
| `pytest` | 4 |
| `unittest` | 0 |
| `cargo` | 13 |
| `gradle` | 0 |
| `maven` | 0 |
| `npm` | 6 |
| `yarn` | 0 |
| `cypress` | 0 |
| `playwright` | 0 |
| `go test` | 0 |
| `tests/run.sh` | 1 |
| `CANONICAL_DOMAINS` | 2 |
| **total** | **26** |

Re-run with `grep -liF` (case-insensitive) and `grep -lwE` (word-boundary): both
produce byte-identical file sets for every token above in this repo — the pinned
rule (case-sensitive substring) is not load-bearing for the current numbers, but is
stated explicitly in AC-2 because it *would* matter the moment a file introduces
`NPM` or `Cargo.toml`-style capitalization, and a future re-derivation must not have
to guess which convention was used.

### 9.3 Raw (file, token) hit list and classification

Every hit, classified `debt` or `structural` against AC-11's unchanged allowlist
(`deploy/**`; the 3 sandbox `writable_roots` configs; the 5 capability-disclosure
prompt configs; the 2 `handoff/SKILL.md` idiom files). The 5 capability-disclosure
configs were individually re-scanned against all 12 tokens (not assumed) to check
AC-11's stated multiplier — each has exactly one relevant hit (`cargo`, once per
file), so no ×2 applies there; AC-11's own ×2 language attaches only to the 2
`handoff/SKILL.md` files (one per crew).

| # | file | token | category | owning issue | allowlist basis |
|---|---|---|---|---|---|
| 1 | `program/agents/crew_alpha/agents/qa/config.yaml` | `pytest` | debt | #439 (default) | not in AC-11 allowlist |
| 2 | `program/agents/crew_alpha/skills/cross-review/SKILL.md` | `pytest` | debt | #439 (default) | not in AC-11 allowlist |
| 3 | `program/agents/crew_beta/agents/beta_qa/config.yaml` | `pytest` | debt | #439 (default) | not in AC-11 allowlist |
| 4 | `program/agents/crew_beta/skills/cross-review/SKILL.md` | `pytest` | debt | #439 (default) | not in AC-11 allowlist |
| 5 | `deploy/bootstrap.sh` | `cargo` | structural | — | `deploy/**` |
| 6 | `deploy/preflight.sh` | `cargo` | structural | — | `deploy/**` |
| 7 | `deploy/provision-ubuntu.sh` | `cargo` | structural | — | `deploy/**` |
| 8 | `program/agents/crew_alpha/agents/eng_claude/config.yaml` | `cargo` | structural | — | capability-disclosure config |
| 9 | `program/agents/crew_alpha/agents/eng_codex/config.yaml` | `cargo` | structural | — | capability-disclosure config |
| 10 | `program/agents/crew_alpha/config.yaml` | `cargo` | structural | — | sandbox `writable_roots` config |
| 11 | `program/agents/crew_alpha/skills/handoff/SKILL.md` | `cargo` | structural | — | idiom file ("cargo cult") |
| 12 | `program/agents/crew_beta/agents/beta_eng_claude/config.yaml` | `cargo` | structural | — | capability-disclosure config |
| 13 | `program/agents/crew_beta/agents/beta_eng_codex/config.yaml` | `cargo` | structural | — | capability-disclosure config |
| 14 | `program/agents/crew_beta/config.yaml` | `cargo` | structural | — | sandbox `writable_roots` config |
| 15 | `program/agents/crew_beta/skills/handoff/SKILL.md` | `cargo` | structural | — | idiom file ("cargo cult") |
| 16 | `program/agents/platform/config.yaml` | `cargo` | structural | — | capability-disclosure config |
| 17 | `program/config.yaml` | `cargo` | structural | — | sandbox `writable_roots` config |
| 18 | `deploy/README.md` | `npm` | structural | — | `deploy/**` |
| 19 | `deploy/bootstrap.sh` | `npm` | structural | — | `deploy/**` |
| 20 | `deploy/provision-ubuntu.sh` | `npm` | structural | — | `deploy/**` |
| 21 | `program/agents/crew_alpha/config.yaml` | `npm` | structural | — | sandbox `writable_roots` config |
| 22 | `program/agents/crew_beta/config.yaml` | `npm` | structural | — | sandbox `writable_roots` config |
| 23 | `program/config.yaml` | `npm` | structural | — | sandbox `writable_roots` config |
| 24 | `program/agents/release/config.yaml` | `tests/run.sh` | debt | #439 (default) | not in AC-11 allowlist |
| 25 | `bin/nightshift-ctl` | `CANONICAL_DOMAINS` | debt | #403 | not in AC-11 allowlist |
| 26 | `bin/nightshift_review_receipts.py` | `CANONICAL_DOMAINS` | debt | #403 | not in AC-11 allowlist |

**Totals (computed from this table, not asserted): 26 entries — 7 `debt` (rows
1–4, 24, 25–26) + 19 `structural` (rows 5–23).** Structural breakdown: 6 under
`deploy/**` (rows 5–7, 18–20), 6 sandbox `writable_roots` configs (rows 10, 14, 17,
21–23 — `cargo`+`npm` in each of the 3 files), 5 capability-disclosure configs (rows
8–9, 12–13, 16), 2 idiom files (rows 11, 15). Debt breakdown: 5 default-own #439
(4 `pytest` + 1 `tests/run.sh`), 2 own #403 (`CANONICAL_DOMAINS` × 2 files) —
matching AC-3's revised text above by construction.

## Identity

- Work item: #439
- Committed contract path and blob OID: `docs/specs/439-guardrail.md` at this
  commit — resolve via `git rev-parse HEAD:docs/specs/439-guardrail.md` on the
  PR #447 branch tip.
- Contract version: v2 (see the version banner at the top of this file).
- Base SHA: `b2f41cb29f9eed1613b8e7d6ef3291e52e3e913a`
- ADR: none directly (this correction is scoped under owner directive WA-020,
  `.omni/working-agreements.md`); the Required-review-domains derivation below
  is separately governed by ADR-0042 (`review plan`) and ADR-0039
  (`parse_required_domains`).
- Review owner (cross-crew lead, not the author): crew_alpha lead — performed
  one of the three independent cross-checks of the domain derivation below.
- Frozen at (UTC): this Identity/Required-review-domains section is an additive
  follow-up landed on the existing PR #447 branch after the AC-2/AC-3/AC-6
  measurement correction below was already committed; it does not reopen or
  re-date that correction.

## Required review domains

Checked/unchecked state below is not a judgment call made in this document: it
was independently derived by
`nightshift-ctl review plan --task 439-guardrail --repo . --head-sha 494013f470acb2de8c9ca3038ecf8dcb6bf2138c`
(ADR-0042), and cross-checked by three independent parties (crew_alpha lead,
reviewer_codex, a fresh non-authoring PM instance) against real diff content
for PR #448 (the implementation this spec governs):
`suggested_domains = [code_review, cross_crew, pm, qa, security]`.

- [x] PM acceptance
- [x] Different-vendor code review
- [x] QA
- [x] Cross-crew review
- [ ] Staff / ADR consistency  (not triggered — no high-risk-glob paths: constitution/control_plane/schema_migration/dependency/public_contract)
- [x] Security  (triggered — sensitive_content match on subprocess/token usage in the new scanner)
- [ ] UX  (not triggered — no ui/web/frontend/docs-HUMAN-COMMUNICATION paths touched)

**Machine-readable checklist (ADR-0039, issue #231).** This section is parsed by
`bin/nightshift_review_receipts.parse_required_domains()` to derive the exact set
of domains a work item's consolidation must wait for. The rule is exact and fails
closed:

- Each row is `- [ ]` or `- [x]`/`- [X]` followed by a label.
- A label is matched by a fixed canonical PREFIX, independent of anything after
  it (a parenthetical condition, a `--`-delimited rationale, a delegate name):
  `PM acceptance`, `Different-vendor code review`, `QA`, `Cross-crew review`,
  `Staff / ADR consistency`, `Security`, `UX`. These seven prefixes are the
  complete canonical domain set and none is a prefix of another.
- `[x]`/`[X]` means required for this work item; `[ ]` means not required.
- A label matching zero or more than one canonical prefix, a canonical domain
  appearing twice, or a canonical domain never appearing at all in the section —
  each is a parse failure (`ReceiptDenied`), never a guess.

Removing a domain after freeze requires a new contract version and rationale.
