# ADR-0085: External-repository operating mode — PR-only, never merge

Date: 2026-09-05 · Status: proposed
Deciders: program · Proposers: platform (Claude)
Relates to: ADR-0082 (migration by authority epoch, the registry this decision
extends), ADR-0081 (Publication Gateway — the only customer-repository *content*
write path; this ADR is about the *merge* capability, a different write path
entirely) · Spec: docs/audit/FINDINGS.md F-101, docs/audit/PIPELINE.md

## Context

Nightshift is about to operate on a repository it does not own — a customer
repository. In that mode it may open a pull request; it must never merge
one. Merging is the customer team's decision, not Nightshift's.

Nothing mechanically enforced this at the time this decision was written.
Worse, the release seat's own procedure actively instructed the opposite:
`program/agents/release/config.yaml` (step 7, before this change) told the
integrator to run, unconditionally,

    gh pr merge --merge --match-head-commit "$head_sha"

Pointed at a repository Nightshift does not own, that instruction is a
direct, autonomous write to someone else's default branch. This is not a
new observation — `docs/audit/FINDINGS.md` F-101 already recorded it:
*"no replacement mechanical enforcement (a pre-push hook or an agent-layer
policy denial on `gh pr merge`) has been built; 'release is the sole
default-branch writer' remains prose discipline, not a mechanism."*
`docs/audit/PIPELINE.md` recorded the live probe behind that finding: every
seat shares one GitHub PAT capable of `gh pr merge`, and no policy evaluator
in `deploy/policies/omnipods_policies.py` inspected a merge call.

This decision narrows that gap for one specific, high-stakes case: a
repository Nightshift does not own. It does **not** close F-101, which is
about release being the sole default-branch writer on Nightshift's **own**
repository — a different, still-open problem, and its finding record is not
edited by this ADR.

The distinction that matters, stated precisely: Nightshift's own repository
(self-owned) is self-governed, and merging there by the release seat is
correct and must keep working exactly as it does today. A repository that
is not Nightshift's own is external, and there the merge **capability**
must be **absent**, not merely discouraged by a prompt.

### Where the code learns which repository it is operating on (evidence, not assumption)

Before designing a fence, the identity source it would have to trust was
audited directly, because a fence keyed to a spoofable identity is not a
fence:

1. Repository identity is established once, at `project bind`
   (`bin/nightshift_project.py`), which calls the live GitHub API and
   captures `id`, `owner_login`, `owner_type`, `default_branch`, and related
   fields into a write-once JSON descriptor under
   `<state_root>/startups/<repository_id>/<project_run_id>.json`.
2. `control.db` (the legacy control plane) carries **no repository identity
   at all** — no repository, no owner, no remote, in any of its tables.
   `--repository-id` is plain argv on the verbs that accept it.
3. The only control-plane-shaped state that carries a durable repository
   identity is `authority_binding.repository_id` inside
   `<state_root>/authority/<repository_id>/authority.db` (ADR-0082's
   external migration-authority registry). It is single-row
   (`CHECK(id=1)`), and a trigger aborts any `UPDATE` that would change
   `repository_id`. It records no owner and no ownership classification —
   ADR-0082 built it to answer "which store", not "whose repository".

### The honest finding: this identity source is worker-forgeable

Workers run as the same uid that owns `state_root` (mode `0700`, enforced by
`nightshift_project._open_state_root`). A process running as that uid can
create, delete, or overwrite anything under `state_root`, including
`authority/<repository_id>/authority.db` — the exact file this decision
makes authoritative for ownership. The startup descriptor that first names
a repository is unsigned (a plain SHA-256 over public inputs, no key), so a
hand-forged descriptor is not detectably different from a real one at read
time; only the original `project bind` call actually talked to GitHub.

**Consequence, stated plainly: no fence built from state inside
`state_root` can be tamper-proof against a worker process that already runs
as the uid that owns `state_root`.** That is a real limit and this decision
does not claim otherwise. What such a fence *can* do — and what this
decision builds — is close the two channels that actually caused the bug
described above:

- the release seat's own **prompt**, a git-tracked YAML string a worker can
  edit in a pull request like any other file in this repository;
- the worker's own **shell environment**, which cannot reach the policy
  evaluator's environment: `omnigent-server` (where the tool-call policy
  layer runs) reads its environment from a root-installed, `0600`
  systemd `EnvironmentFile`, not from anything a worker process exports.

Removing those two channels is a real, verifiable improvement even though
it is not a complete defense against a compromised worker uid. Hardening
against that stronger threat is out of scope here (see Consequences).

## Options considered

### A — Bundle-root tool-call policy denial, keyed on a write-once authority-registry row (proposed by platform (Claude), chosen)

Classify each repository as `self` or `external` in a new, write-once
`repository_ownership` row inside the ADR-0082 authority registry —
outside the working tree, outside `.omni/control.db`, and outside anything
a worker's prompt or shell environment can name. Deny `gh pr merge` (and
the REST equivalent, `gh api .../pulls/<n>/merge`) at the Omnigent
tool-call layer, declared at the **bundle root** so it applies to every
seat, whenever the classification is anything other than a present,
declared `ownership='self'` row. No fail-open override.

This won because the registry already exists for exactly this purpose
(binding a fact to a repository, external to every store the fact
constrains), and because the tool-call layer is the one place in this
codebase that can intercept a `gh` invocation before the shell runs it,
regardless of which seat's prompt produced the command.

### B — Refuse inside the Publication Gateway (rejected)

The Publication Gateway (ADR-0081, `bin/nightshift_publication.py`) is the
existing "only write path into a customer repository" mechanism, and its
refusal vocabulary (`PublicationError`/`_refuse`/`error_payload`) is the
idiom this decision's own registry functions deliberately reuse. But the
gateway writes finalized artifacts into a **bare local git directory**; it
never calls GitHub and never merges anything. Putting a merge fence inside
it would guard a code path that was never the risk, and leave the actual
risk — a `gh pr merge` shell call — completely unguarded.

### C — A pre-push git hook in the checkout (rejected early)

A hook is a file inside the same working tree a worker already controls;
it can be edited or removed by exactly the process this decision needs to
constrain. More fundamentally, `gh pr merge` is a server-side GitHub API
call — it does not run through `git push` and would never invoke a local
hook at all. This option does not address the actual mechanism being
misused.

### D — Prose-only: trust the release seat's procedure (rejected — the status quo)

This is what existed before this decision, and `docs/audit/FINDINGS.md`
F-101 already recorded that it has failed as a control: "prose discipline,
not a mechanism." Doing nothing is not a null option here; it is the
option that produced the bug this ADR fixes.

## Decision

Add a write-once `repository_ownership` table to the existing ADR-0082
authority registry (`bin/nightshift_authority.py`,
`<state_root>/authority/<repository_id>/authority.db`), holding exactly one
row per repository: `ownership` (`'self'` or `'external'`), who declared it,
and when. Two `BEFORE UPDATE`/`BEFORE DELETE` triggers make the row
immutable at the database layer, the same idiom `authority_binding` already
uses for the epoch. The `repository_id` column is declared with **no SQLite
type name** (BLOB affinity), not `INTEGER` — verified directly against
`sqlite3` that `INTEGER ... CHECK(typeof(x)='integer')`, the idiom used
elsewhere in this codebase (`nightshift_publication.py`,
`nightshift_provenance.py`, `nightshift_tenant_keys.py`), does **not**
reject the string `'5'`: INTEGER affinity silently converts it to the
integer `5` *before* the CHECK constraint runs. A column with no declared
type performs no such conversion, so the CHECK genuinely refuses a string.
That sibling-table gap is a real finding, reported here and left for
whoever owns those tables to decide whether it is worth a table rebuild;
this decision does not touch them.

Classification is computed by one function, `classify_repository(state_root,
repository_id)`, which reads `authority.db` and **nothing else** — never
`.omni/control.db`, never a `program/*.yaml` prompt or config string, never
a memory store, never any environment variable other than the two named
below. It never raises: anything that is not a present, declared
`ownership='self'` row — an absent registry file, a registry that exists
but was never bootstrapped, an unreadable or corrupt file, a missing scope —
classifies `external`. This is a deliberately different default from
ADR-0082's own `guard()`, which defaults an absent registry's
`active_backend` to `legacy`: the two questions have opposite safe
defaults. "Which store is authoritative" has a correct historical answer
when nothing has moved (legacy); "do I own this repository" has no safe
default except no. Treating silence as permission to merge into someone
else's default branch is exactly the failure this decision removes.

The scope a process addresses is carried in exactly two environment
variables, defined once in `bin/nightshift_authority.py` and imported by
every other consumer rather than re-spelled: `NIGHTSHIFT_STATE_ROOT` and
`NIGHTSHIFT_REPOSITORY_ID`. `deploy/bootstrap.sh` idempotently writes both
into the same root-owned, `0600` systemd `EnvironmentFile` that already
carries `PYTHONPATH` for the custom policies — the channel a worker's own
shell environment cannot reach.

The mechanical fence is a new Omnigent tool-call policy,
`external_repo_merge_fence` (`deploy/policies/omnipods_policies.py`),
declared at the **bundle root** in `program/config.yaml` — not on the
`release` seat's own config. `deploy/README.md`'s enforcement table already
records, citing issue #118 as proven, that per-seat policy declarations
never reach the Omnigent engine; a copy on `release`'s own YAML would be
exactly as dead as that table's `stuck_detector` row. The policy matches
`gh pr merge` and `gh api .../pulls/<n>/merge` by regex (cheap, so every
unrelated shell call pays nothing), classifies the deployment, and denies
with reason code `merge_forbidden_external_repository` unless the
classification is `self`. Unlike every other fail-closed policy in that
file (`worker_semaphore`, `program_authority_fence`), there is **no**
`..._FAIL_OPEN` environment variable: a denied worker dispatch is requeued
and retried at no cost, but a wrongly-allowed merge into a repository
Nightshift does not own writes to that repository's default branch and
cannot be recalled the way a retry can.

`bin/nightshift_authority.py` also gains its own three-verb CLI
(`ownership-declare`, `ownership-show`, `merge-preflight`), in the same
`build_parser`/`main`/`_run_*_command` shape
`bin/nightshift_publication.py` already uses, so the exact classification
the tool-call fence would apply is also checkable by hand or from a script
without a control-plane roundtrip. `program/agents/release/config.yaml`
step 7 is rewritten to call `merge-preflight` before merging and to name
the refusal and the correct fallback (leave the PR open for the customer
team) on a non-zero exit, so the seat's written procedure and the mechanism
agree; step 9's self-revert is scoped to a merge step 7 actually permitted.

### A named, disclosed limitation

The fence classifies the **deployment**, not the argument of an individual
`gh` invocation. `gh pr merge -R other/repo` from a self-classified
deployment is allowed. That is the safe direction for the risk this
decision closes — an external-mode deployment denies every merge,
including one aimed at its own repository, rather than the reverse — and
parsing `gh`'s own repo-resolution fallback (the current directory's git
remote) would be a heuristic dressed up as a mechanism, not a stronger
fence.

### Repository ownership is not the same axis as anything else that uses similar words

`active_backend` on the same registry's `authority_binding` row
(ADR-0082) answers "which STORE is authoritative for this repository's own
data" — an entirely different question from `ownership` here, which
answers "does Nightshift own this repository at all". Both use the word
"external"; a future reader must not read them as one flag, and this
decision's schema comment and module docstring say so explicitly, alongside
a test asserting the two vary independently.

Separately, a sibling stream in this same wave (repository-scope routing,
platform/165) adds its own `repository_id`-scoped column to `control.db`
for routing work rows within one control plane. That column is a
**projection** — a convenience copy for a different purpose — and it can
never answer "may I merge". If the two ever disagree, `authority.db` is the
one that is right, because `classify_repository` and the tool-call fence
read that file and nothing downstream of it.

## Consequences

**Easier:** a deployment can be asked, mechanically, whether it may merge
into a given repository, and the answer does not depend on which seat's
prompt is currently running or on a config file a worker's own pull request
could have edited. Nightshift's own repository, once declared `self`,
merges exactly as it does today.

**Harder / new cost:** every existing deployment must explicitly declare
`ownership=self` once (`bin/nightshift_authority.py ownership-declare`)
before the release seat can merge again — an intentional breaking change on
upgrade, because the alternative (auto-declaring `self` for every existing
deployment) would silently re-grant merge to every deployment that already
holds a live PAT, which is precisely the "silence grants the capability"
failure this decision exists to remove. `docs/runbooks/
repository-ownership-bootstrap.md` gives the operator the exact command.

**Foreclosed, on purpose:** a deployment can no longer merge "by default."
Ownership must be declared, once, by an operator, on a registry a worker
cannot bootstrap on the fence's behalf just by wanting to.

**Named and not claimed fixed:**
- This does **not** make the ownership record tamper-proof against a
  worker process running as the uid that owns `state_root` — see the
  Context section's forgeability finding. It removes the prompt and
  environment channels that actually caused the bug being fixed here, not
  every channel a sufficiently privileged attacker could use.
- This does **not** close `docs/audit/FINDINGS.md` F-101, which is about
  release being the sole default-branch writer on Nightshift's own
  repository. That finding's record is unedited by this decision.
- This does **not** decide which repositories are external, nor does it
  relax `bin/nightshift_project.py`'s existing refusal to bind an
  organisation-owned repository (`owner_type != "User"`) — a typical
  customer repository is usually org-owned, so binding one at all remains a
  separate, unresolved blocker on the same programme.
- This does **not** fence `git push` to a default branch, a different
  capability with its own existing control path (`blast_radius`'s
  `gate_pushes`).

## Cost

**Today:** zero new infrastructure. One extra SQLite open on the rare path
where a shell command actually matches the merge regex (every other shell
call is a cheap regex miss and pays nothing). Two additional lines in the
already-existing systemd `EnvironmentFile`. One real, one-time operational
step per existing deployment: run `ownership-declare --ownership self`
once, or the release seat stops merging — by design, and disclosed above,
not a silent regression to debug later.

**At 100×:** unchanged. The registry holds one row per repository
regardless of how many merges are attempted against it, and the policy's
cost per tool call is a regex match that misses on every call that is not
a merge. Nothing about this fence scales with dispatch volume, PR volume,
or worker count.

## One-way door?

No. The ownership row is write-once by design, but *reversing a
declaration* is an operator act with no data loss: delete
`<state_root>/authority/<repository_id>/authority.db` (the ADR-0082 epoch
binding it also carries is rebuildable via `init_binding`) and declare
again. The policy itself can be removed from `program/config.yaml` in one
line. The one genuinely sticky artifact is the reason code
(`merge_forbidden_external_repository`) appearing in operator runbooks and
incident history once it has fired.

## Scale tier

None yet (T0), per `docs/architecture/SCALE-LADDER.md`. Trigger to move up:
a single deployment needing to operate on more than one repository
concurrently with different ownership per repository — deployment-level
classification would then be insufficient and each `gh` invocation's actual
target would need to be resolved and checked individually, which is exactly
the heuristic this decision declines to build today (see the named
limitation above).

## Reversal trigger

Revisit this decision if: a legitimate self-owned merge is denied on a
correctly-bootstrapped deployment (a false positive means the classifier is
reading the wrong scope, not that the mechanism is wrong in principle); or
if a single deployment genuinely needs to serve multiple repositories with
mixed ownership at once, which this design does not support.
