# ADR-0054: M2 Seam B close-out — clean-source install rehearsal (D11) and a preflight check manifest (D12)

Date: 2026-08-30 · Status: accepted · Deciders: crew_beta lead · Proposers: none (see §Process — cross-vendor debate blocked)
Conversation: crew_beta session (title `393-build`) · Spec: `docs/prd/393-m2-seamB-preflight-rehearsal.md` (frozen review contract v1, PR #396, `beta_pm`, DoR: READY)
Amends: none (additive: `docs/prd/DOD-MANIFEST.json` rows, `tests/linux/install-rehearsal.sh`, `tests/test_preflight.py`, `deploy/preflight.sh` diagnostic-only touch-up if a gap is found)

## Process — cross-vendor debate could not run (WA-5)

Per the mandatory design-debate process, a `beta_qa` planning slot was
acquired this session (`nightshift-ctl slot acquire --holder crew_beta/beta_qa
--crew crew_beta --task 393-m2-seamB-preflight-rehearsal --agent beta_qa
--title test-393-m2-seamB-preflight-rehearsal --purpose explore`, granted
`slot_id=22`, `generation=1`) and dispatched for a test plan, with
`task_id`/`slot_id`/`generation`/`purpose` on the `sys_session_send` call as
the contract requires. It was rejected three ways in sequence, reproduced
live this session:

1. `slot_id`/`task_id`/`generation` as top-level `args` keys →
   `Input validation error: Additional properties are not allowed
   ('generation', 'slot_id', 'task_id' were unexpected)` — the declared
   `sys_session_send` schema has `args: {additionalProperties: false,
   properties: [cost_budget, file_ids, input, model, purpose]}` and simply
   has no room for them.
2. The same three embedded as text inside `input` (two formattings tried) →
   `Denied by policy: args.slot_id must be a strict positive integer` — the
   root `worker_semaphore` policy (`deploy/policies/omnipods_policies.py:224`)
   reads `args.get("slot_id")` from the literal tool-call arguments, which
   the schema in (1) makes impossible to populate.
3. `args` as a JSON string instead of an object →
   `Denied by policy: worker dispatch args must be an object`.

No combination reaches a state where `worker_semaphore` sees a populated
`slot_id`/`task_id`/`generation` for a `COUNTED_AGENTS` target
(`beta_eng_claude`, `beta_eng_codex`, `beta_qa`, `beta_reviewer_claude`,
`beta_reviewer_codex`). This is issue #236 (P0, open at time of writing), the
same defect this session's `crew_alpha` entries hit repeatedly (#218, #217,
#216, #215, #214, #174; ADR-0043/0044/0045/0046/0047). The unused slot was
released (`slot release --slot 22`) rather than left to expire; the finding
is also logged in `.omni/decisions.md`.

`beta_pm` dispatch is unaffected (`beta_pm` is in `EXEMPT_AGENTS`), so the
Definition-of-Ready pass that produced the frozen 17 acceptance criteria
(D11-1..7, D12-1..9, Both-10) proceeded normally; only the two-vendor
**build** debate is blocked. One live reproduction against `beta_qa` is
treated as sufficient evidence that `beta_eng_claude`/`beta_eng_codex`
dispatch is equally sterile, per the same precedent ADR-0045/0046/0047
followed rather than re-triggering the identical failure per vendor.

Per **Working Agreement 5**, this ADR is **single-author**, and the
implementation and its tests are authored directly by the crew_beta lead
rather than by `beta_eng_claude`/`beta_eng_codex`/`beta_qa`. That is a real
gap, not a formality — flagged here, in the PR body, and in the report to
the program. Cross-crew review (`crew_alpha`, round-robin) is the mandatory
independent check for the resulting diff, standing in for both in-crew
review and QA this round. Not a standing authorization for future tasks —
reconfirmed live, for this task specifically, per this crew's own rule
against inferring WA-5 from precedent alone.

## Context

`tests/dod_validate.py` reports `D11 FAIL` (`tests/linux/install-rehearsal.sh`
not found) and `D12 FAIL` (manifest declares no `preflight_checks` section).
Per `docs/prd/MILESTONES.md`'s M2 seam table these are Seam B's two exit
conditions. `beta_pm`'s frozen spec (`docs/prd/393-m2-seamB-preflight-rehearsal.md`)
already resolved most open design questions during DoR; this ADR records the
two decisions that are genuinely architectural — how the rehearsal proves
itself in CI without touching `.github/workflows/**`, and how the D12
manifest stays honest (complete, not just individually valid) without a
build-time dependency on `tests/dod_validate.py` itself (`crew_alpha`'s
principal file).

`deploy/lib/deploy-lib.sh:148-195` already carries a **functional** bwrap
probe (`nightshift_bwrap_probe`/`nightshift_bwrap_diagnosis`, RT-13/14/15,
issue #12, closed) that distinguishes three states by distinct exit code —
missing (2), present-but-non-functional (1, the presence-vs-function defect
issue #5 named), and functional (0) — and `deploy/preflight.sh:70-79` already
calls it and fails on anything but 0. D12 does not rebuild this; it names it.

## Options considered

No competing vendor proposals exist (see §Process). The two decisions below
are the lead's own analysis.

### Decision point 1 — how does `rehearsal_ci_job` get CI-backed without touching `.github/workflows/**`?

**A — Name a new CI job in `.github/workflows/tests.yml` (rejected).**
`docs/owners.md` marks `.github/workflows/**` platform-owned, single-writer,
"never changed mid-wave." An edit lease was actually acquired
(`nightshift-ctl lease acquire --resource .github/workflows/tests.yml --mode
edit`, `lease_id=20`) while this option was still open, then released
unused once option B proved sufficient — holding it only as long as the
question was genuinely open. Rejected because: (i) `check_d11`
(`tests/dod_validate.py`) only requires `rehearsal_ci_job` to name a member
of `required_ci_jobs` that structurally exists as a job in `tests.yml` — it
does not require a *new* job; (ii) a new job means a new host of concerns
(does a `docker pull ubuntu:24.04` + apt bootstrap fit `tests.yml`'s
15-minute timeout, does it need its own timeout, does a platform reviewer
need to bless the runner image) for zero validator benefit; (iii) the
existing precedent in this repo (`tests/linux/toolchain-smoke.sh`,
`tests/linux/docker-bind-smoke.sh`) is that docker-dependent scripts are
deliberately kept **outside** the fast `tests` CI job, documented as local
`docker run` invocations in their own headers — a new heavy CI job would
break that established pattern for no stated reason.

**B — Reuse the existing `tests` job; add a hermetic, non-docker structural
test that exercises real decision logic in `install-rehearsal.sh`, discovered
automatically by `tests/run_suite.py`'s `unittest.TestLoader().discover(...,
pattern="test_*.py")` (chosen).**
`rehearsal_ci_job` is set to `"tests"` — already a member of
`required_ci_jobs` (`["tests"]`) and already green on `main` (`D10 PASS`
today). No `.github/workflows/**` edit, no new platform lease needed.
`tests/test_install_rehearsal.py` (new file, ours: `tests/linux/**`-adjacent,
"tests that drive `deploy/*.sh`") is picked up by `run_suite.py`'s
auto-discovery with zero change to `tests/run.sh` or `tests/run_suite.py`
either — confirmed by reading both files before deciding. It asserts real,
non-stubbed things about the script (header declares the three required
words per `check_d11`; the git-archive extraction genuinely excludes an
untracked file added to the working tree; the decision-logic functions the
rehearsal calls into, e.g. `nightshift_version_ge`, `nightshift_bwrap_probe`,
exist and behave as `deploy/lib/deploy-lib.sh` defines them) without ever
invoking `docker pull`/`apt-get`/a real container — those stay a documented,
header-declared local-only demonstration, exactly like the two existing
docker-smoke scripts. This satisfies condition 11's validator check
mechanically (`rehearsal_ci_job` present and required) and satisfies its
spirit partially and honestly: CI runs real, non-stub assertions about the
rehearsal's own decision logic on every PR/push; the full container run
remains a local, documented, on-demand proof, same as its two siblings. The
gap between "CI job named `tests` ran a hermetic subset" and "CI job named
`tests` ran the full container end-to-end" is real and is disclosed, not
hidden, in the rehearsal script's own header and in the PR body — condition
10 (seam A) is what would fully close that gap by confirming a
container-capable required job stays green at the tip, and is out of this
item's scope per `beta_pm`'s DoR doc.

**Decision: B.** No `.github/workflows/**` edit. The unused edit lease
(`lease_id=20`) was released the moment this became clear, per normal-lease
discipline ("do not hold what you no longer need"), rather than at PR-open.

### Decision point 2 — how does the D12 manifest stay complete, not just individually valid?

**A — Trust the manifest as written; only `check_d12`'s per-entry checks apply
(rejected).** `check_d12` validates the shape of whatever rows exist, but as
`beta_pm`'s doc notes, it has no way to catch a check that exists in
`deploy/preflight.sh` and is simply never manifested — which is exactly how
issue #5 happened (presence-only was never flagged as insufficient by
anything mechanical). Rejected: it reproduces the original failure mode one
layer up.

**B — A structural completeness test, `tests/test_preflight.py`, that
statically scans `deploy/preflight.sh` for every `bad "…"` / `warn "…"` call
site and asserts the resulting id set equals the manifest's `preflight_checks`
id set (chosen).** Mirrors the pattern `tests/test_bwrap_probe.py`'s
`RT15NoPresenceOnlyCheckSurvives` already uses for a narrower claim (no
presence-only bwrap check survives anywhere in `deploy/`). A regex scan over
`bad "` / `warn "` call sites is not a full shell parser and cannot see a
multi-line call or one embedded inside a `#`-comment reliably — both are
named explicitly as known gaps in the test's own docstring (following this
project's `tests/dod_validate.py` doctrine: "a stub that returns PASS is the
defect this file exists to prevent" — so the limitation is written down, not
silently assumed away) and verified against the actual file: every `bad`/
`warn` call site in `deploy/preflight.sh` as it stands today is single-line
and outside comments, so the scan is accurate for the file this ADR ships
against.

**Decision: B.**

## Decision

Both B options above. `docs/prd/DOD-MANIFEST.json` gains `rehearsal_ci_job:
"tests"` and a `preflight_checks` array (one entry per `bad`/`warn` call site
in `deploy/preflight.sh`, each with `id`, `severity`, `remediation`, and
either a hermetic fixture in `tests/test_preflight.py` or an explicit
`fixture_exempt_reason` for the live-only ones — systemd unit state, network
listener binds, tailscale state). The bwrap entry is `severity: FAIL`,
`remediation` names the exact deterministic command
`bwrap --ro-bind / / /bin/true; echo $?`, and its hermetic fixture exercises
all three `nightshift_bwrap_probe` exit codes (missing / present-but-broken /
functional) via `NIGHTSHIFT_BWRAP` override, not just present-vs-absent —
the presence-vs-function distinction issue #5 exists to prevent regressing.

## Consequences

**Easier:** `docs/prd/DOD-MANIFEST.json` is now the single place that answers
"what does preflight check, and how would I know if one silently broke" —
the completeness test makes that answer self-enforcing rather than
aspirational. The install rehearsal has a real, git-archive-based, non-bind-
mount proof of the install path that a future change to
`provision-ubuntu.sh`/`bootstrap.sh` cannot silently bypass without failing a
CI-visible test.

**Harder:** the full container rehearsal (with a live systemd-user session
bus) still requires a human or a follow-up platform-lease CI job to run in
GitHub Actions on every PR — this item deliberately does not close that gap,
per Decision point 1. Anyone reading `D11 PASS` alone, without the header's
per-step declaration, could over-read what actually ran in CI; the header
and this ADR are where that is made explicit.

**Foreclosed:** nothing new — `docs/prd/DEFINITION-OF-DONE.md` is untouched,
`.github/workflows/**` is untouched, `tests/dod_validate.py` is untouched.

## One-way door?

No. `rehearsal_ci_job` can be repointed at a new dedicated job later (a pure
manifest-row change plus, at that point, a genuine `.github/workflows/**`
lease) with no migration and no data-shape change. The completeness test is
additive and can be tightened (a real shell-aware scanner) without touching
its assertions' meaning.

## Scale tier

None (`docs/architecture/SCALE-LADDER.md`) — no infrastructure, cost, or
throughput characteristic changes.

## Reversal trigger

If condition 10 (seam A) or a future item needs the rehearsal's full
container run verified green at the tip of `main` on every merge, that is a
new, explicit decision to request a `.github/workflows/**` lease and add a
dedicated job — never a silent repoint of `rehearsal_ci_job`. If issue #236
clears, any follow-up change to this item's scope should get a real
two-vendor design debate before being treated as satisfying the
design-debate precondition; this ADR's single-author status is a property of
#236 being open on 2026-08-30, not a new norm for this crew (same WA-5
reversal trigger as ADR-0043/0044/0045/0046/0047).
