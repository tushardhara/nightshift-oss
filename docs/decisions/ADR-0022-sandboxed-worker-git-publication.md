# ADR-0022: Broker Git publication for sandboxed Codex workers

Date: 2026-08-27 · Status: accepted · Decider: program · Proposers: Claude Sonnet, Codex

This v9 decision text supersedes the unintegrated ADR-0022 draft in PR #198
and its earlier delivery-PR drafts; ADR-0022 is not yet present on the default
branch. V9 corrects the hierarchy claim in those drafts without changing the
publication design or implementation.

## Context

Issue #190 registered the reviewed
`4fc76a6bbd0a2e19f6603775ef86645b166c11e8` bundle on an isolated VPS
Omnigent instance. Its first Program attempt failed before child dispatch
because the exact-release policy directory was absent from `PYTHONPATH`; its
second Program attempt loaded the policy but Claude execution returned HTTP
401, again before child dispatch. A separate direct diagnostic of the real
`eng_codex` seat made its one permitted file change, but `git add` failed while
creating the linked-worktree `index.lock`. The full
`program -> crew_alpha -> eng_codex` hierarchy, Claude review, and issue #190
acceptance flow therefore remain unproven.

The publication boundary in this decision comes from that measured direct-seat
failure. It is not evidence that the end-to-end hierarchy passed, and this
enabling change does not by itself complete issue #190.

That is the documented Codex boundary, not a Unix ownership defect. Under
`workspace-write`, both a writable root's `.git` entry and a linked worktree's
resolved administrative Git directory remain recursively read-only. Adding the
parent Git directory to `writable_roots` therefore cannot authorize a commit.
The only native no-prompt alternative is `danger-full-access`.

Nightshift cannot safely use that alternative today. Omnigent starts the Codex
app-server outside the seat's `os_env` sandbox, and ADR-0012 proves that
sub-agent policy declarations do not reach the policy engine. With
`eng_codex.os_env.sandbox: none`, changing `yolo` to true would give every
model-generated command the write authority of the `nightshift` Unix account.
The root `blast_radius` policy denies a catastrophic command set, but it is not
a filesystem boundary and deliberately permits ordinary pushes.

The required capability is much smaller: after a worker has edited and verified
its assigned workspace, it must be able to stage an allowlisted diff, create a
commit with its registered seat identity, push its non-default task branch
without force, and open one Draft PR through `nightshift-ctl pr-create`.

## Proposals

### A — Native full access for `eng_codex` (Codex)

Set `executor.config.yolo: true` for the one engineering seat. This is the
smallest configuration diff and matches Omnigent's headless Codex default, but
on this host it removes the only effective write boundary from arbitrary shell
commands. Rejected.

### B — Automatically approve Git escalation (Codex)

Keep `workspace-write`, switch to interactive escalation, and automatically
approve commands classified as Git metadata operations. This delegates the
safety boundary to command-text classification and cannot bind the write to a
task, workspace, branch, base SHA, changed-path set, or identity. Shell
composition makes the authority wider than its label. Rejected.

### C — Wrapped Codex plus a narrow publication tool (Claude Sonnet)

Move this one seat from `codex-native` to Omnigent's wrapped `codex` harness and
expose one repository-owned Omnigent tool to it. The tool, not a
model-generated shell, performs the Git metadata writes. It accepts declarative
publication inputs and fails closed unless all of these facts agree:

The wrapped harness registers repository-owned tools through Codex App Server's
`dynamicTools` protocol. It therefore has no MCP approval prompt to strand an
unattended worker. Omnigent executes a local function tool in the runner's tool
process rather than as a Codex native-shell command, so the worker sandbox does
not block the reviewed function. The exact-SHA host disables Codex's native
shell and unified-exec surfaces. In v1, source reads, writes, edits and tests use
Omnigent's `sys_os_shell` dynamic tool, which executes through the configured
bubblewrap sandbox. The broker is the only dynamic tool that crosses that
sandbox.

1. the workspace is a private Nightshift clone rather than a linked worktree;
2. the control-plane slot is active, belongs to the expected task and seat, and
   names this workspace;
3. `HEAD`, the task branch, remote base and allowlisted changed paths match the
   dispatch contract;
4. the index starts clean and the commit is authored and committed with the
   repository's fixed identity for that seat;
5. the push is a normal non-force task-branch push; and
6. the Draft PR is opened through `nightshift-ctl pr-create`, with a body file
   that passes the existing closing-keyword guard.

The worker retains read, edit and test capability through sandboxed Omnigent
tools, but those tools receive neither the host home nor a network namespace.
Only the reviewed tool process can cross the `.git` and network boundaries,
and it has no arbitrary-command parameter. Accepted.

### D — The crew lead commits for the worker (Claude Sonnet)

Have `crew_alpha` stage and commit the child's diff. This makes the parent a
state writer, weakens the worker receipt, and breaks the direct
worker-to-commit evidence #190 is intended to prove. Rejected.

### V7 cross-vendor reconciliation

Both vendors agreed the v6 review blockers were real. The second-vendor
proposal retained the broker-only name `NIGHTSHIFT_CONTROL_DB` on the theory
that the broker could own a private alias. Program rejected that split after
checking the repository: both exact-SHA wrappers, `nightshift-ctl`, release
planning and runtime proof already establish `NIGHTSHIFT_CTL_DB`. V7 therefore
uses that exact name everywhere, unsets the rejected alias at both wrapper
boundaries, and has no alias or fallback. A valid legacy value cannot rescue an
absent, empty, missing, noncanonical or symlinked canonical binding.

The initial implementation treated repository, branch and head SHA as enough
to recognize an existing Draft PR. Cross-crew review showed that a stale Draft
could share those fields while carrying the wrong base, fork repository, title
or body, or return a URL that names a different repository. Program adopted the
stricter proposal: success requires the complete reviewed identity tuple. The
URL must be the canonical credential-free
`https://github.com/<owner>/<repository>/pull/<positive-integer>` form with no
query, fragment or extra path. Repository comparison alone (in both the URL and
the explicit `headRepositoryOwner.login + "/" + headRepository.name` pair) is
case-normalized using the same validation as the dispatch contract; base, title
and body remain exact byte-for-byte values. The broker does not depend on the
optional nested `headRepository.nameWithOwner` field.
When `tasks.pr_url` is non-null, the returned URL must equal it exactly and that
value remains part of every publication revalidation. Any missing, null,
malformed or mismatched added field is the fixed `pr-mismatch` refusal, never a
field-specific diagnostic and never a reason to create another PR.

### V8 measured CodexExecutor scratch reconciliation

The v7 production-shaped model-seat run exposed one narrower integration fact:
the installed Omnigent `CodexExecutor` creates `.codex-tmp/` at the repository
root while servicing tool calls. On the recognized-commit retry path, Git
reported the runtime file below that directory as untracked, so the broker
correctly refused `changed-tree-after-commit`. The commit itself was unchanged;
the refusal was caused by executor-owned, non-project scratch appearing after
the worker commit.

This is not contrary to the worker sandbox evidence. Worker-facing file tools
run inside bubblewrap, while the publication broker runs in Omnigent's separate
host tool process and deliberately sees the real Git workspace. Executor
scratch that is masked or incidental at the model boundary is therefore still
visible to the broker's host-side `git ls-files` check.

V8 records exactly `/.codex-tmp/` in the repository ignore contract. The
leading slash and trailing slash are deliberate: only that directory at the
repository root is ignored. Nested `.codex-tmp` directories, similarly named
paths and every unrelated untracked file remain visible to the broker and
continue to fail closed. This does not weaken commit validation. A new commit
is staged from the reviewed base into a temporary index using only contract
allowlisted paths; a retry still validates the exact parent, tree, identity,
message and allowlisted commit diff. Runtime scratch cannot enter either path
because contract validation rejects `.codex-tmp` as the first allowed-path
component before any Git or GitHub operation. Git ignore rules also do not hide
an already tracked or force-added path from commit-diff validation. Ignored
scratch may contain executor config, credentials-shaped data or symlinks; the
broker neither reads nor traverses those entries, and explicit allowlist staging
cannot add them. Concurrent tool calls may populate the one runtime directory
while publication inspects the tree; the broker neither deletes it nor relies
on it being empty. Ordinary cleanup remains `CodexExecutor`'s responsibility.

Program rejected removing the retry-path untracked check, special-casing
untracked paths inside broker code, adding `.codex-tmp` to a task allowlist, and
broader ignore patterns such as `.codex-*` or `**/.codex-tmp/`. Each would hide
more workspace state than the one measured executor path. Revisit this rule if
Omnigent stops creating the directory, moves its scratch location, or starts
using root `.codex-tmp/` for durable project input; that evidence requires a
new ADR revision before retaining, moving or broadening the ignore.

## Decision

Adopt proposal C for `crew_alpha.eng_codex` first. Keep one active delivery PR
for #190: this ADR, the tool, its tests, and the seat prompt/config wiring travel
together.

"For `crew_alpha.eng_codex`" is enforced by bundle construction and tool
registration: the tool file exists only in that seat's agent directory. It is
not a per-seat policy claim; ADR-0012 remains authoritative that those policy
declarations are inert in the installed runtime.

The seat uses `harness: codex`. Omnigent 0.10.0 reduces the YAML sandbox to the
single Codex `workspace-write` mode at the native App Server boundary; it does
not apply `read_paths`, hidden-path rules or `allow_network` to Codex's native
shell. Treating those YAML fields as native-shell confinement was rejected by
security review because the shell could read the private `CODEX_HOME` that the
wrapped executor creates beneath the workspace and then bypass the broker.

The host launch therefore fixes `HARNESS_CODEX_DISABLE_NATIVE_TOOLS=1` and
`HARNESS_CODEX_ENABLE_WEB_SEARCH=0`. On the installed runtime this produces
`thread/start.config.features` with both `shell_tool: false` and
`unified_exec: false`; all Omnigent dynamic tools remain. This setting is
server-global in v1, so the shipping bundle must contain exactly one wrapped
`codex` seat. Adding another is a revalidation trigger, not an incidental
configuration change.

Engineering work uses Omnigent `sys_os_shell`, which applies the caller-process
bubblewrap spec. The working directory is mounted writable and `.git` is then
overlaid read-only. `read_paths` must not also contain `.`: Omnigent 0.10 applies
read mounts after the writable working-directory bind, so that spelling makes
the whole task read-only. `$HOME` and host cache/toolchain directories are not
mounted, hidden `.codex-tmp` is masked, and `allow_network` is false. Structured
source helpers remain advertised by the runtime but are not used in this v1
contract because their mount semantics do not give both arbitrary source reads
and a writable workspace. Per-command caches, when needed, live beneath the
OSEnv-private `$TMPDIR`, not the Git workspace or host home. GitHub CLI
configuration, global Git configuration and `.ssh` are not mounted. The local
publication tool runs in the runner's separate tool process and is the only
component that receives the trusted Unix home and network needed for remote
authentication.

The installed-runtime probe supplied counterevidence against treating the
advertised structured source helpers as equivalent to this shell path:
structured source read and edit were denied, while `sys_os_shell` could read,
edit and test an ordinary source file. The same sandbox could read `.git/HEAD`
but a `.git` write failed with `EROFS`; it could not see host credential or
cache canaries and a network connection was denied. That observed boundary,
not the presence of helper names in the tool list, is the v1 evidence.

The tool must use argument-vector subprocess calls, never `shell=True`, and may
not accept an executable, raw Git argument list, environment override, author
identity, remote URL, or destination path from the model. Identity is fixed in
reviewed code. Workspace, control DB, exact release root and exact release SHA
come from runner-provided environment, then are canonicalized and checked
against the live slot. Initial validation refusals happen before the first Git
metadata mutation; a later live-authorization change stops the next publication
phase at its boundary. Retry after a partial remote-side success must be
idempotent and must never create a second PR.

The environment assertion is not sufficient on its own. The running broker
source must match the Git blob at
`<release-sha>:program/agents/crew_alpha/agents/eng_codex/tools/python/nightshift_publish.py`.
The controller is never taken from the worker workspace. The broker opens
`<release-root>/bin/nightshift-ctl` without following symlinks, verifies the
open file descriptor against `<release-sha>:bin/nightshift-ctl`, and holds that
descriptor through guarded commit and PR creation. It executes the descriptor
by seeking the same held descriptor to byte zero before each call and supplying
it as source on the pinned system Python's standard input. The reviewed
`/usr/bin/python3` system alias is canonicalized before use because Ubuntu
24.04 packages it as a symlink to `/usr/bin/python3.12`; the resulting target
must still be a canonical, regular, non-symlink executable. Python runs with
`-I`, preventing a worker-controlled module in the workspace or user site from
shadowing a standard-library import in the credential-bearing controller. This
also avoids a macOS `/dev/fd` behavior that can return success after executing
an empty program, while retaining pathname-swap safety. Replacing either the
worker controller or the trusted pathname after attestation therefore cannot
select code for the credential-bearing process. Direct import was rejected
because the controller is an exit-oriented CLI rather than a stable module API;
a hash passed only through environment was rejected because it would not attest
the bytes being executed.

The subprocess boundary is closed rather than inherited. Production Git and
GitHub CLI calls use the reviewed absolute VPS executables (`/usr/bin/git` and
`/usr/bin/gh`) after regular-file, non-symlink and executable checks; caller
`PATH` cannot select a replacement. Child processes receive a fixed minimal
environment with the current Unix account's passwd-resolved home, a fixed
system `PATH`, C locale and interactive prompts disabled. Ambient Git override
variables, GitHub token variables and GitHub configuration overrides are not
forwarded. Git replacement objects are disabled in both argv and environment
for workspace operations and exact-release blob attestation. The only additions
are tool-owned temporary-index and fixed author/committer values. Push also
fixes `core.hooksPath=/dev/null`, so a repository-controlled pre-push hook
cannot expand the broker's authority.

This exact-SHA claim covers repository-owned bytes, not the VPS operating-system
image. The broker trusts root-owned `/usr/bin` and the package-managed Python
alias; binding executable paths and digests into the release plan was rejected
for this single-host change because a digest-only check would still leave a
hash-to-exec race and would not defend against root. Revisit immutable image or
per-call executable attestation if publication moves to multiple hosts, package
drift occurs outside a reviewed cutover, or the runtime identity can write an
executable or parent directory. Publication must be disabled if the canonical
target or those ownership and permission assumptions stop holding.

The slot and task are read in one SQLite snapshot. While holding the per-task
publication lock, the broker reloads that snapshot and the normalized contract
before the local-ref, push and PR boundaries. It requires the task binding,
including `pr_url`, and the full normalized publication contract to remain
identical. A local mutation requires five seconds of remaining slot lifetime;
push and PR creation require 65 seconds, exceeding their 60-second subprocess
timeout. Revocation linearizes at these checks: work completed before a
successful check may remain, but no later phase starts after the next failed
check. Instantaneous cancellation inside an already-authorized external call is
not claimed; that would require a transactional publication reservation shared
with every revocation path.

The exact-SHA cutover independently passes the reviewed SHA to both launch
wrappers. Fixed `/usr/bin/git` calls disable replacement objects and reject an
unexpected `HEAD`; the controller recomputes Git-object and filesystem
manifests immediately before the first supervision mutation and again at later
start boundaries. The wrappers repeat the SHA comparison, so a change after
controller verification is refused rather than silently becoming runtime
authority.

Publication is single-flight per task. The tool takes a non-blocking exclusive
`flock` on a deterministic, tool-owned lock file beside the control database
before inspecting remote state and holds it through local commit creation,
push, Draft PR creation and final inspection. A contender returns
`publication-busy`; the lock file remains in place so unlink-and-recreate
cannot split the lock domain. This makes the "no PR" check and PR creation one
serialized operation for a task instead of relying on a post-write duplicate
check.

### Binding and input contract

The public tool schema has exactly two inputs: `task_id` and integer `slot_id`.
It reads `_AP_WORKSPACE`, `NIGHTSHIFT_CTL_DB`,
`NIGHTSHIFT_RELEASE_ROOT`, and `NIGHTSHIFT_RELEASE_SHA`; absence of any binding
is a refusal. The exact release root must be canonical, non-symlinked, outside
the worker workspace, and checked out at the supplied 40-hex SHA. The tool then
opens the SQLite database read-only and joins:

- `worker_slots`: the row is active, has a non-boolean numeric `expires_at`
  strictly later than the current time, is already dispatched, has the supplied
  task id, and is bound to `eng_codex` plus the task title;
- `tasks`: the row is `crew_alpha` / `eng_codex`, is in an in-flight state, and
  supplies `base_sha`, canonical `worktree`, task `branch`, and `spec_ref`.

The canonical `_AP_WORKSPACE` must equal `tasks.worktree`. `spec_ref` names a
regular, non-symlink JSON contract below the control DB directory's
`contracts/` child. Program writes that file before dispatch. Version 1 has
these required fields: `task_id`, `issue`, `repository`, `default_branch`,
`allowed_paths`, `commit_subject`, `pr_title`, and `pr_body`. The task id must
match the row; repository/default branch and publication prose therefore come
from the Program-owned contract, not the worker's tool arguments. Every allowed
path is a normalized repository-relative path with no empty, dot, parent, or
`.git` component. `pr_body` must contain `Refs #<issue>` and no GitHub closing
keyword.

No control-plane migration is needed: `tasks` already stores `base_sha`,
`spec_ref`, `worktree`, and `branch`; `worker_slots` already stores the consumed
dispatch binding. The tool reads those facts and never mutates the database.

### Workspace and repository contract

The workspace must have a private `.git` directory containing a valid
`nightshift-workspace.json` version-1 marker whose canonical `path` and
`base_sha` match the task. A linked worktree, shared checkout, moved marker,
symlinked workspace or Git directory, or writable path outside the canonical
workspace is refused. `origin` must normalize to the contract's GitHub
`owner/repository`; the current branch must equal `tasks.branch` and must not be
the contract's default branch. During this preflight the broker records the
exact normalized fetch and push URLs separately in its publication state. It
revalidates the current `origin` values against those pins at each later remote
boundary, but every `ls-remote` and push receives the appropriate pinned URL
explicitly; neither operation resolves the mutable bare name `origin`. Local
Git URL rewriting still applies to explicit URL argv, so pinning alone is not
the boundary. Before origin preflight and immediately before every
`ls-remote`/push, the broker inspects the effective Git configuration with
includes enabled and refuses if any case-insensitive `url.*.insteadOf` or
`url.*.pushInsteadOf` key exists in any scope. The inspection requests names
only; rewrite values never enter a receipt or diagnostic. This catches rewrite
configuration already present at a publication boundary. Local
`HEAD`, local `origin/<default_branch>`, and the remote branch SHA returned by
`git ls-remote` must all equal the task's base SHA before a first commit. This
detects a stale base without writing `FETCH_HEAD`, and a `.git/config` swap
after preflight but before a boundary check fails before the changed
destination receives inspection credentials or task data.

The ordinary index must be clean. The complete working-tree changed-path set
must be non-empty and contained by the contract allowlist. Version 1 refuses
every tracked-path deletion, including rename-away, as `deletion-refused`
before temporary-index, ref, remote, or PR mutation. `nightshift-ctl
guard-commit` deliberately refuses deletion of a path live at the base ref, so
pretending `allowed_paths` also authorizes deletion would make the broker's
safety contract ambiguous. Intentional deletion needs a later, separately
reviewed contract field and matching control-plane guard support. These checks,
contract/body linting, GitHub authentication/read access, and existing-remote-
branch/PR inspection all run before the first Git metadata write.

### Guarded commit and publication

The tool creates a temporary index below the control DB directory, seeds it
from `HEAD`, and stages only the reviewed allowlist by setting its own fixed
`GIT_INDEX_FILE`. It rechecks the staged path set and runs the existing
`nightshift-ctl guard-commit` against that temporary index. The earlier
no-deletion preflight means this guard cannot turn an otherwise accepted
version-1 diff into an opaque deletion failure. It then uses
`git write-tree`, `git commit-tree`, and compare-and-swap `git update-ref` to
create the commit without executing repository-controlled hooks. The fixed
author and committer are
`crew_alpha.eng_codex <crew_alpha.eng_codex@nightshift.invalid>`; the
reviewed message ends with the required Omnigent co-author trailer. Temporary
index removal happens in `finally` and does not alter the worker's ordinary
index.

Push uses the preflight-pinned normalized push URL and
`HEAD:refs/heads/<tasks.branch>` argv, with no force option. It revalidates both
pinned origin values and repeats the value-free effective-config rewrite guard
immediately before invoking Git. A fetch/push URL or `insteadOf`/
`pushInsteadOf` swap visible at that check is refused; the explicit URL also
prevents a post-preflight change to the remote name alone from selecting the
destination. PR creation invokes the already-attested controller file
descriptor from the sealed exact-SHA release with fixed `--repo`, `--head`,
`--base`, `--draft`,
`--title`, and `--body` argument positions populated from the Program-owned
contract. No caller-supplied argv reaches Git, GitHub CLI, Python or the
controller.

This check is deliberately not claimed as an atomic defense against a separate
compromised process running as the same Unix identity: such a process could
replace Git configuration between check and Git's read. That actor is outside
the v1 model-seat threat boundary; every tool exposed to the seat observes
`.git` as `EROFS`, and the adversarial tests cover mutations present before a
publication boundary. If another same-UID writer is introduced, a same-UID
process compromise is observed, or publication is packaged for a shared Unix
identity, this decision must be superseded with an immutable broker package and
a Git execution design that does not consume mutable workspace configuration
(for example, a separately attested shadow Git directory).

### Retry states

1. **No task commit, no remote branch, no PR:** create the commit, push, open
   the Draft PR.
2. **Recognized task commit, no matching remote branch:** validate the commit's
   exact parent, tree, message and fixed author/committer, then push it.
3. **Recognized task commit and identical remote branch, no PR:** skip commit
   and push, then open the Draft PR.
4. **One open Draft PR for the exact URL, base, repository, branch, head, title
   and body:** return its URL without creating another.
5. **Any mismatched remote branch, non-Draft/open PR, multiple PRs, changed
   local tree after a task commit, or closed PR for the branch:** refuse. A
   supersession gets a new task and branch rather than guessing intent.

The initial implementation is intentionally one-seat and one-repository. Beta
adoption is a later reviewed change after alpha has one successful production-
shaped receipt; copying an unproven publication boundary into both crews would
double the audit surface before evidence exists.

## Proof required before merge

- Unit tests exercise wrong seat, expired/released slot, wrong task, workspace
  escape/symlink, linked worktree, stale base, default branch, dirty index,
  out-of-scope paths, identity spoofing, remote-URL spoofing, argv injection,
  force-like input, PR closing keywords, subprocess failure, hostile executable
  search paths, ambient GitHub credentials/configuration, lock contention,
  repository push hooks, tracked deletion/rename refusal, config swaps before
  remote inspection and push, pinned explicit remote URLs, and idempotent
  retry.
- Ignore-boundary tests prove only root `.codex-tmp/` is ignored: the populated
  runtime `auth.json`, copied config and outward auth symlink are tolerated both
  before the broker-created commit and on recognized-commit retry without
  entering the commit, while `.codex-tmpish`, `.codex-tmpfile`, nested
  `src/.codex-tmp/`, and an adjacent ordinary untracked path remain visible and
  fail closed.
- Environment regressions execute the wrapper boundary and broker with the
  canonical `NIGHTSHIFT_CTL_DB`: canonical-only succeeds; missing, empty and
  legacy-only bindings refuse; a hostile legacy value cannot redirect a valid
  canonical value; an invalid or symlinked canonical value cannot fall back;
  wrappers unset the legacy name; runtime proof rejects it; and neither DB name
  crosses the broker subprocess boundary.
- GitHub CLI fixture rows use the conservative real `headRepository {id,name}`
  and sibling `headRepositoryOwner {id,login}` shape, and the broker explicitly
  requests both fields. Existing/final Draft acceptance tests cover the exact
  positive tuple and change one field at a time: wrong, missing or null base;
  missing, null, malformed or wrong repository name or owner login; wrong or
  missing title; byte-changed body including a closing keyword; and a
  PR URL with the wrong host or repository, credentials, noncanonical number,
  extra path, query or fragment. A recorded `tasks.pr_url` is also checked for
  exact equality and refuses when GitHub has no matching PR. Each returns only
  `pr-mismatch`, opens no replacement PR and performs no publication mutation.
- Shipping regressions prove there is exactly one wrapped `codex` seat, both
  host launch paths disable native tools, and its Omnigent-tool sandbox exposes
  one writable workspace with only `.git` overlaid read-only, with host homes,
  host caches, hidden `CODEX_HOME` and network absent.
- An installed-runtime counterfactual captures `thread/start` with native shell
  and unified exec absent while Omnigent dynamic tools and the publication
  broker remain registered. Through `sys_os_shell` the same probe reads, edits
  and tests a source file but cannot write `.git`, read credential or cache
  canaries, or reach the network.
- The Linux installed-runtime boundary is reproduced directly through
  Omnigent's exact `ToolManager` with:

  ```sh
  python3 -m unittest \
    tests.test_seat_permission_mode.RepoBundleTest.test_installed_tool_manager_enforces_worker_bwrap_boundary
  ```

  On the VPS this must run (not skip) and report one passing test. It proves
  structured write succeeds while structured source read/edit are denied,
  shell source read/edit succeeds, `.git/HEAD` is readable, `.git` writes fail
  read-only, host credential/cache canaries and `GITHUB_TOKEN` are absent, and
  a shell network connection cannot be established. Non-Linux hosts, or hosts
  truly lacking Omnigent/bwrap, report the one explicit skip.
- Unit tests bind broker and controller bytes to one exact release SHA, replace
  the worker controller with a sentinel, and swap the trusted controller path
  after attestation; neither sentinel may execute and both controller calls
  must use the one held descriptor as isolated Python source stdin. They also
  cover Ubuntu's `/usr/bin/python3` symlink, invalid canonical interpreter
  targets, worker-workspace standard-library shadowing, Git replacement refs,
  empty or malformed guard receipts, contract/PR binding changes, near-expiry
  slots and revocation at each publication boundary.
- A positive isolated Omnigent test shows the worker invokes the narrow tool,
  and the resulting commit object has exactly
  `crew_alpha.eng_codex <crew_alpha.eng_codex@nightshift.invalid>` for
  author and committer.
- Staff reviews this ADR, a different vendor reviews the implementation, and
  security reviews the subprocess, path, SQLite and credential boundaries.
- Release tests the merge result. Only then may the new exact SHA be used for
  the disposable #190 end-to-end run.

## Cost and scale

This is tier 0: one local process, SQLite reads, and ordinary Git/GitHub calls.
It adds no service and no recurring infrastructure cost at current scale or
100x. The operational surface is the tool contract and its audit events. Move
to a resident broker service only if measured process startup or concurrent
publication contention becomes material; the trigger is more than 20
publications per minute or a p95 local validation time above two seconds for
three consecutive waves.

The current VPS has no `srt` binary, so Omnigent's local tool subprocess is not
wrapped in a second tool sandbox. Registration must fail closed if a future
Omnigent/runtime combination cannot give this one reviewed local function the
Git access its contract requires while retaining the worker sandbox. Installing
`srt`, or changing local-tool sandbox semantics, is therefore a revalidation
trigger rather than a reason to broaden the worker seat.

The current threat boundary relies on the release checkout being absent from
the worker's bubblewrap view. A chmod-sealed checkout is not immutable against a
separately compromised process running as the same Unix account. Defending that
broader threat requires an operator/root-owned read-only release or immutable
mount. That is a reported future root action, not authority granted by this ADR,
and must not be performed as part of an unattended cutover.

## Reversal trigger

Disable the tool and return Codex publication to a blocked, human-observed state
if one wrong identity, branch, path, repository, or unbound task passes its
checks; if a native Codex command writes Git metadata without the tool; or if an
idempotent retry creates a second PR. Do not respond by enabling broader native
shell access. Supersede this ADR before accepting a second DB environment name,
if `nightshift-ctl` changes its canonical variable, or if GitHub CLI changes the
Draft PR JSON contract so `baseRefName`, `headRepositoryOwner.login`,
`headRepository.name`, `title` or `body` cannot be inspected exactly, or if
GitHub stops returning canonical repository-bound PR URLs.
Supersede this ADR before retaining the ignore if root `.codex-tmp/` becomes
project source or durable input, before matching any additional path, or if a
separately compromised same-UID process is observed using ignored scratch to
influence publication. The latter crosses the current model-seat boundary and
requires disabling publication until immutable broker/workspace packaging is
reviewed; it is not a reason to broaden the ignore.
