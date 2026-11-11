# H1 item 0 — preflight receipts

Preflight for `docs/architecture/H1-WALKING-SKELETON-GAP.md` item 0, under issue #102,
with the merge-gate decision recorded in #112.

Standard applied, verbatim from the charter: *an environmental assumption that has not
been EXECUTED is not evidence.* Every verdict below is backed by output actually
observed. Where something could not be, or deliberately was not, executed, the verdict
says so and gives the reason.

**A distinction this document now keeps explicitly**, after cross-vendor review of head
`9949413bf366fb7c410c6ccb4a60504eb4625599` (slot 127, Codex): *applying a setting and
re-reading it back* proves configuration, not behaviour. Configuration receipts and
behavioural receipts are labelled separately throughout. Three claims in the first
revision conflated the two; they are corrected below, and two of the three were
converted from relabelling into executed behavioural probes.

- Base SHA of this branch: `ee907125b73ddfeacc7d950452716983f70a7818`
- Executed: 2026-08-23, ~22:03–22:20Z
- Seat: `platform`, from an independent clone (not `/srv/project`, not a linked worktree)
- Prior half (slot 124) proved (a) non-force push from an independent clone, (b) clone
  of a foreign origin without `--reference`, (c) `platform` writing outside
  `/srv/project`, and a green Actions run on a private repo. Not re-litigated here.

## Target repository

| Field | Value |
| --- | --- |
| URL | <https://github.com/tushardhara/nightshift-h1-target-08232203> |
| Visibility | `private` (confirmed by API, not by assumption) |
| Owner type | `User` |
| Default branch | `main` |
| Default-branch tip | `87c1e14c953d1135e189b6bce262c8215c0bde71` |
| Green CI run on default branch | `32669558679`, conclusion `success` |
| Created | 2026-08-23T22:03:40Z |

---

## Probe 1 — `gh repo create --private` authorization

**The one gap item 0 existed to close.** `repo` scope was known to be present in the
token, but scope presence is not a receipt.

Command:

```
gh repo create nightshift-h1-target-08232203 --private --description "H1 walking-skeleton proving ground (Refs nightshift#102)"
```

Output:

```
https://github.com/tushardhara/nightshift-h1-target-08232203
EXIT=0
```

Confirmation that the created repository is actually private, via
`gh api repos/tushardhara/nightshift-h1-target-08232203`:

```json
{"archived":false,"created_at":"2026-08-23T22:03:40Z","default_branch":"main",
 "fork":false,"has_issues":true,
 "html_url":"https://github.com/tushardhara/nightshift-h1-target-08232203",
 "name":"nightshift-h1-target-08232203","private":true,"size":0,"visibility":"private"}
```

**Verdict: PROVEN.** Private repository creation is authorized for this seat and was
executed.

*Correction from the first revision (review finding 4):* that revision cited `size: 0`
from this response as evidence the repository began empty. It is not. Repository `size`
is coarse, lagging metadata — the live target still reports `size: 0` today with nine
tree entries and several commits. The claim is withdrawn. Initial emptiness is instead
evidenced behaviourally, by the clone performed immediately after creation:

```
Cloning into 'target'...
warning: You appear to have cloned an empty repository.
```

and by the first push reporting `* [new branch] HEAD -> main`, which only occurs when
the ref did not previously exist.

## Probe 2 — seeding a bounded stdlib-only brief

`.omni/brief.md` on the default branch specifies `tally`, a Python 3 stdlib-only CLI
that counts newline-delimited records and prints a frequency report. Nine numbered
acceptance criteria, an explicit out-of-scope list, and a definition of done that is a
green `python -m unittest discover -s tests -v` on a pull request. No third-party
dependency, no network access, no packaging.

Seeded files, from `git diff --cached --name-status` before the commit (explicit paths
staged; `git add -A` was not used):

```
A	.github/workflows/ci.yml
A	.omni/brief.md
A	README.md
A	tests/test_smoke.py
```

Push output:

```
To https://github.com/tushardhara/nightshift-h1-target-08232203.git
 * [new branch]      HEAD -> main
branch 'main' set up to track 'origin/main'.
PUSH_EXIT=0
```

**Verdict: PROVEN.** The brief exists on the target's default branch. This also executed
a second, unlisted authorization: pushing a file under `.github/workflows/` requires the
`workflow` token scope, and that push succeeded.

## Probe 3 — an already-green workflow on the default branch

The hazard item 0 names: if the default branch starts empty, later pull requests have
no check at all and the merge gate silently evaporates. Avoided by seeding the workflow
in the same commit that created `main`, then confirming it ran.

`gh run list -R tushardhara/nightshift-h1-target-08232203`:

```
completed	success	Ignore __pycache__	CI	main	push	32669558679	13s	2026-08-23T22:06:13Z
completed	success	Probe: PR check gate	CI	probe/gate-check	pull_request	32669506419	10s	2026-08-23T22:05:14Z
completed	success	Seed H1 target: brief, green CI workflow, smoke test	CI	main	push	32669468656	9s	2026-08-23T22:04:33Z
```

`gh run view 32669468656`:

```json
{"conclusion":"success","databaseId":32669468656,"event":"push",
 "headSha":"b27bdf94ea9648bb8d5a5d2243bbec8631194cf0","status":"completed",
 "url":"https://github.com/tushardhara/nightshift-h1-target-08232203/actions/runs/32669468656",
 "workflowName":"CI"}
```

**Verdict: PROVEN.** The current default-branch tip `87c1e14c` is green under run
`32669558679`.

## Probe 4 — does a pull request actually receive that check?

A green workflow on the default branch is necessary but is not by itself the gate.
Executed rather than assumed: branch `probe/gate-check` pushed, PR #1 opened against
`main`.

`gh pr checks 1`:

```
unittest	pass	6s	https://github.com/tushardhara/nightshift-h1-target-08232203/actions/runs/32669506419/job/97268000146
```

`gh api repos/.../pulls/1`:

```json
{"mergeable":true,"mergeable_state":"clean","rebaseable":true}
```

**Verdict: PROVEN — pull requests do receive a CI check.** Note carefully that
`mergeable_state: clean` here means *nothing is blocking*; it is not evidence that any
check is required. PR #1 is deliberately left open on the target as a live receipt.

## Probe 5 — can that check be made REQUIRED? **No. (configuration receipt)**

Attempting to configure a required status check on the target's default branch:

```
gh api -X PUT repos/tushardhara/nightshift-h1-target-08232203/branches/main/protection \
  --input - <<< '{"required_status_checks":{"strict":true,"contexts":["unittest"]},
                  "enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null}'
```

Output:

```json
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.",
 "documentation_url":"https://docs.github.com/rest/branches/branch-protection#update-branch-protection",
 "status":"403"}
```

Reading protections and rulesets fails identically:

```
GET repos/.../branches/main/protection -> 403 "Upgrade to GitHub Pro or make this repository public"
GET repos/.../rulesets                 -> 403 "Upgrade to GitHub Pro or make this repository public"
```

**Verdict: FAILED — branch protection and rulesets are unavailable on this private
repository on this plan.** This is a *configuration* receipt: it proves no native
required-check configuration can exist here. It does not, by itself, execute any
behaviour. The behaviour is Probe 5b.

Cross-vendor review independently ruled out the alternatives: organization rulesets and
required workflows are organization/enterprise controls and cannot govern a `User`-owned
repository; merge queue depends on a protected or ruleset-governed branch; environments
gate deployments, not merges; and a GitHub App check can report a result but cannot make
itself required without protection. No native gate is available. #112 stands.

## Probe 5b — is a RED pull request actually mergeable? **Yes. (behavioural receipt)**

*This probe was added in response to review finding 1.* The first revision asserted "a
red pull request is still mergeable" on the strength of Probe 5 alone. That was an
inference from a configuration result presented as an executed fact — exactly the defect
shape this document exists to be immune to. Rather than relabel it, it was executed.

Branch `probe/red-gate` was pushed to the target carrying one deliberately failing test,
and PR #2 was opened. `gh pr checks 2`:

```
unittest	fail	6s	https://github.com/tushardhara/nightshift-h1-target-08232203/actions/runs/32670055504/job/97269387153
```

Check-run state on the PR head `5283c4029999ff4fd906e1d0409ff449a49ca006`:

```json
{"conclusion":"failure","name":"unittest","status":"completed"}
```

Mergeability with that failing check present:

```json
{"draft":false,"mergeable":true,"mergeable_state":"unstable","number":2,"rebaseable":true}
```

`mergeable_state: unstable` is GitHub's term for *mergeable, with non-required checks
failing* — the precise state a required check would have prevented.

That still stops one step short of executing the merge, so the merge was executed too.
To avoid reddening the target's default branch — a precondition this preflight exists to
establish — a throwaway base branch `probe/red-base` was created at the green `main` tip
`87c1e14c`, PR #2 was retargeted onto it via `PATCH /repos/.../pulls/2`, its head check
was re-confirmed `failure`, and the merge was requested:

```json
{"merged":true,"message":"Pull Request successfully merged",
 "sha":"70ae0dd132c14838c9faed112d708e2e0067bc83"}
```

Post-merge, PR #2 is `state: closed`, `merged: true`, `merged_at: 2026-08-23T22:19:16Z`.
The default branch was re-read immediately afterwards and is unchanged at
`87c1e14c953d1135e189b6bce262c8215c0bde71`.

**Verdict: PROVEN by execution.** A pull request whose only check concluded `failure`
was merged successfully on this target. The absence of a native required check is not
merely a configuration limitation; the permissive merge behaviour it implies has now
been observed end to end.

### What this means for the H1 gate

CI reports pass/fail on every pull request, no check can be marked required, and a red
pull request merges. The gate for H1 on this target is therefore **advisory, and must be
enforced by the loop's own `release` stage** — GitHub is a reporter here, not an
enforcer. Options, none chosen here because it is the Program's call:

1. Make the target **public**. GitHub's own 403 text names this remedy. *Not executed* —
   item 0 requires a private target — so the claim that protection becomes available is
   **UNVERIFIED by execution**, sourced only to GitHub's message.
2. Upgrade the account to Pro. Not this seat's call. UNVERIFIED.
3. Accept an advisory gate and make `release` fail closed — refusing to merge unless it
   has affirmatively observed check success. This needs no plan change, but it moves the
   gate into code we own and therefore needs its own acceptance criteria and a **red-path
   regression test**: a test that a red check actually blocks `release`, since GitHub
   demonstrably will not.

## Probe 6 — persistent-surface sweep of the target

Every endpoint queried against `tushardhara/nightshift-h1-target-08232203`. The column
records whether that **persistent** surface holds a stored credential or a configured
outbound integration — not whether the repository is unable to reach the network, which
is a separate question answered below.

| Surface | Result | Stored credential / configured integration? |
| --- | --- | --- |
| Environments | `{"total_count":0,"environments":[]}` | None |
| Actions secrets | `{"total_count":0,"secrets":[]}` | None |
| Actions variables | `{"variables":[],"total_count":0}` | None |
| Dependabot secrets | `{"total_count":0,"secrets":[]}` | None |
| Codespaces secrets | `{"total_count":0,"secrets":[]}` | None |
| Deploy keys | `[]` | None |
| Webhooks | `[]` | None |
| Collaborators | `tushardhara` only | Owner only |
| Actions enabled | `{"enabled":true,"allowed_actions":"all","sha_pinning_required":false}` | n/a — see hazard below |
| Default workflow permissions | `{"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}` | Ephemeral token, read-scoped |
| Actions access to other repos | `{"access_level":"none"}` | None |
| Branch protection / rulesets | 403, unavailable on plan | n/a |

**Verdict: PROVEN, narrowly — every enumerated persistent credential and integration
surface on the target is empty.** No stored secrets, no environments, no deploy keys, no
webhooks, no cross-repository Actions access, one collaborator.

*Correction from the first revision (review finding 5):* that revision generalised this
to "the target holds no credential and no outbound integration". That is broader than
the evidence and is withdrawn. **The residual hazard is workflow execution itself**, and
it is not small:

- Every Actions job receives an **ephemeral `GITHUB_TOKEN`**. The seeded workflow pins
  `permissions: contents: read`, and the repository default is also `read`, so that
  token cannot mutate this repository — but it is a credential, and it exists per-run.
- `allowed_actions: "all"` with `sha_pinning_required: false` permits **arbitrary
  third-party actions referenced by mutable major tags**, whose contents can change
  under an unchanged reference.
- Runners have **general network egress**. A workflow here can read repository content
  and contact arbitrary external endpoints.

So the supportable claim is: *this repository stores nothing that authenticates to
anything outside it, and cannot act on any other repository.* It is **not** network
isolated, and no such claim is made. Narrowing `allowed_actions` to `selected` and
requiring SHA pinning are both available and were deliberately not applied, since
constraining the toolchain during preflight would prejudge what the H1 loop needs.
`can_approve_pull_request_reviews: false` does mean a workflow cannot self-approve a
pull request.

## Quarantine and disposal procedure for the target

Executed against throwaway private repositories so the target itself stayed intact.
*Corrections from the first revision (review findings 2 and 3):* the first revision
called both containment steps PROVEN on the strength of applying a setting and reading
it back. That is a configuration receipt. Runtime behaviour has now been probed
directly, and the results are more interesting than the original overclaim.

### Step 1 — stop the target executing: **partially proven, with a caveat that matters**

Configuration: `gh api -X PUT repos/OWNER/REPO/actions/permissions -F enabled=false`,
re-read from the same endpoint:

```json
{"enabled":false,"sha_pinning_required":false}
```

The boolean must be sent with `-F`, not `-f`; the string form is rejected `422 "For
'properties/enabled', \"false\" is not a boolean."` — observed.

Runtime probe, on throwaway `nightshift-h1-scratch2-0823221613` carrying a workflow that
triggers on `push` and `workflow_dispatch`. Baseline with Actions **enabled**, a push
produced a run:

```
completed	success	probe: baseline workflow	Probe	main	push	32670084969	9s
```

Actions was then disabled, and two execution attempts were made:

- **Push trigger — contained.** A second commit was pushed to `main` (`2c8f084`). No run
  was created for it; the run list shows no `push` event at that SHA.
- **`workflow_dispatch` — NOT refused.** `POST /repos/.../actions/workflows/probe.yml/dispatches`
  with `ref=main` returned success, and a run **was created**:

```json
{"conclusion":null,"event":"workflow_dispatch","id":32670108362,"status":"queued"}
```

Re-read at 22:19:50Z, roughly three minutes after dispatch at 22:17:00Z, that run was
**still `queued` with `conclusion: null`** — created, never started.

**Verdict: push-triggered execution PROVEN contained. Dispatch PROVEN not refused at the
API. Whether the queued run can ever start is UNVERIFIED** — it was observed queued and
not started at ~3 minutes, which is consistent with Actions being disabled but is not
proof it will never execute, and the observation was not carried further. Do not describe
this step as "the repository stops executing anything". The accurate statement is:
disabling Actions prevents new push-triggered runs, but the dispatch API still accepts
requests and enqueues runs. Step 2 is the stronger containment.

### Step 2 — freeze read-only: **proven for pushes and issues**

Configuration: `gh api -X PATCH repos/OWNER/REPO -F archived=true`, re-read:

```json
{"archived":true,"private":true}
```

Runtime probes against the archived throwaway `nightshift-h1-scratch-0823220651`. A
commit was created locally and pushed:

```
remote: This repository was archived so it is read-only.
fatal: unable to access 'https://github.com/tushardhara/nightshift-h1-scratch-0823220651.git/': The requested URL returned error: 403
PUSH_EXIT=128
```

Issue creation, `POST /repos/.../issues`:

```json
{"message":"Repository was archived so is read-only.",
 "documentation_url":"https://docs.github.com/rest/issues/issues#create-an-issue",
 "status":"403"}
```

**Verdict: PROVEN for pushes and for issue creation** — both refused by the server, by
execution, not by documentation. **Pull-request creation refusal is UNVERIFIED**: the
archived throwaway has no branches to open a pull request between, and creating that
state was not worth the additional writes. It is expected to be refused on the same
read-only basis, but it was not executed and is not claimed.

Archiving is reversible with `-F archived=false` (also executed, in order to run the
Step 1 runtime probe after Step 2 had been tested).

### Step 3 — permanent removal: **UNAUTHORIZED for this seat**

Requesting repository removal through the `gh repo` subcommand, non-interactively, was
refused:

```
HTTP 403: Must have admin rights to Repository.
This API operation needs the "delete_repo" scope. To request it, run:  gh auth refresh -h github.com -s delete_repo
```

The equivalent REST call — an HTTP DELETE against the repository resource — was refused
identically with `403 "Must have admin rights to Repository."`, and the repository was
confirmed still present afterwards. The token carries `gist, project, read:org, repo,
workflow` and does not carry `delete_repo`.

**Verdict: repository deletion is UNAUTHORIZED for this seat by execution, not merely by
policy.** Disposal requires a human to refresh the token with that scope or remove the
repository through the GitHub web interface. **Archive plus Actions-disable is
containment, not disposal** — the repository, its history and its pull requests all
still exist.

### Branch cleanup limitation

Per issue #110's blast-radius policy this seat may push a branch but may not remove one,
and the guard matches on command text — so the branch-removal invocations are described
here rather than quoted: both the Git push form that deletes a remote ref, and the REST
call that removes a `heads/*` reference. Neither was attempted, and no route around the
guard was tried.

### Live leftovers a future driver must dispose of

Disclosed deliberately rather than tidied away, and each verified present:

| Leftover | State | Disposed by |
| --- | --- | --- |
| Target PR #1, branch `probe/gate-check` | open, green — the Probe 4 receipt | Step 3 |
| Target PR #2, branch `probe/red-gate` | **merged** into `probe/red-base` — the Probe 5b receipt | Step 3 |
| Target branch `probe/red-base` | created at `87c1e14c`, now carries merge `70ae0dd1` | Step 3 |
| `tushardhara/nightshift-h1-scratch-0823220651` | archived, Actions disabled, no commits | Step 3 by a human |
| `tushardhara/nightshift-h1-scratch2-0823221613` | Actions disabled, two commits, one run stuck `queued` | Step 3 by a human |

The target's default branch `main` is unaffected by any of the above and remains at
`87c1e14c953d1135e189b6bce262c8215c0bde71`, green.

## Verdict summary

| # | Probe | Kind | Verdict |
| --- | --- | --- | --- |
| 1 | `gh repo create --private` authorization | behavioural | **PROVEN** |
| 1a | Repository began empty | behavioural | **PROVEN** — clone warning + `new branch` on first push (not `size:0`) |
| 2 | Bounded stdlib-only brief on default branch | behavioural | **PROVEN** |
| 2b | Pushing `.github/workflows/*` (`workflow` scope) | behavioural | **PROVEN** |
| 3 | Already-green CI on default branch (`32669558679`) | behavioural | **PROVEN** |
| 4 | Pull requests receive a CI check | behavioural | **PROVEN** |
| 5 | That check can be made **required** | configuration | **FAILED** — unavailable on a private repo on this plan |
| 5b | A **red** pull request is mergeable, and merges | behavioural | **PROVEN** — check `failure`, `mergeable_state:unstable`, `merged:true` |
| 5c | Making the repo public would restore protection | — | **UNVERIFIED** — GitHub's text only, not executed |
| 6 | Enumerated **persistent** credential/integration surfaces are empty | configuration | **PROVEN, narrowly** |
| 6a | Target workflows cannot reach the internet | — | **NOT CLAIMED** — egress, `allowed_actions:all`, unpinned tags, ephemeral token |
| 7 | Actions-disable setting applied and re-read | configuration | **PROVEN** |
| 7a | Actions-disable stops **push-triggered** runs | behavioural | **PROVEN** — no run created for `2c8f084` |
| 7b | Actions-disable refuses `workflow_dispatch` | behavioural | **DISPROVEN** — accepted, run `32670108362` created |
| 7c | That queued run can never start | — | **UNVERIFIED** — observed `queued` at ~3 min, not carried further |
| 8 | Archive setting applied and re-read | configuration | **PROVEN** |
| 8a | Archive refuses pushes | behavioural | **PROVEN** — `403`, "archived so it is read-only" |
| 8b | Archive refuses issue creation | behavioural | **PROVEN** — `403` |
| 8c | Archive refuses new pull requests | — | **UNVERIFIED** — not executed, expected but not claimed |
| 9 | Delete the repository from this seat | behavioural | **UNAUTHORIZED** — `403`, `delete_repo` absent |
| 9a | Delete a branch from this seat | — | **NOT ATTEMPTED** — #110 guard, documented not circumvented |

Item 0's stated gap (Probe 1) is closed by execution. Probe 5b is the finding that should
gate wave-5 planning: the H1 merge gate on a private target cannot be enforced by GitHub —
a red pull request has now been observed merging — so it must be enforced by `release`,
with its own red-path regression test, or the target's visibility must change. Probe 7b
is a smaller but real correction to the quarantine procedure: disabling Actions is not a
kill switch for the dispatch API, and archiving is the step that actually refuses writes.
