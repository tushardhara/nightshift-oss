# ADR-0020 — Bind a project run with a private write-once startup descriptor

- **Date:** 2026-08-25
- **Status:** Accepted — `staff` PASS on exact design commit
  `ed5ad7ecd4761473d27d8287fba6e2a104d3a372` ([issue #102 verdict](https://github.com/tushardhara/nightshift/issues/102#issuecomment-5405804716)).
  Superseded in part by
  [ADR-0021](ADR-0021-trusted-state-root-ancestry.md) (§2
  Git-worktree-exclusion clause and the untrusted-ancestor sentence only).
- **Decider:** Program, reconciling the blind Claude Product proposal on issue
  #102 with the independent Codex architecture proposal
- **Scope:** issue #102, H1 item 1 only
- **Supersedes:** Program decision D-070 only; ADR-0002 remains unchanged

## Context

H1 item 0 created and verified the existing private target
`tushardhara/nightshift-h1-target-08232203`. Item 1 must bind an intended run
to that repository's immutable starting facts before later increments create a
workspace, dispatch an agent, or enter a lifecycle state.

The existing runtime already has two unrelated identifiers called `run_id`:
an integer `agent_runs.id`, and the string `meta.current_run_id` used by the
Program heartbeat. ADR-0002 also reserved nullable `run_id` columns on slots
and claims for later fencing. Reusing or changing any of those identifiers in
item 1 would couple project registration to live liveness and to H1 item 5.

The blind Product proposal therefore blocked on two decisions: the identity
namespace and the authoritative store. It recommended a new project-run
namespace and a project-local SQLite database. The independent Codex proposal
recommended a canonical file outside every checkout, and its adversarial
review found that the file must have explicit idempotency, safe path handling,
and no-clobber publication.

Program D-070 initially called for the authoritative per-run artifact itself to
be Git-tracked. That cannot satisfy this item's constraints: committing it to
the target would mutate the target and move its pinned SHA; committing it to
Nightshift would serialize every customer run through this repository and
could expose private repository metadata. The reproducible *schema, vectors,
tests, and validation evidence* belong in Git. A concrete run's private
descriptor does not.

## Decision

### 1. A distinct project-run identity

The new identifier is named `project_run_id` everywhere. It never uses the
ambiguous JSON key `run_id`.

The caller supplies a non-secret, **repository-scoped** `request_id` matching
`[a-z0-9][a-z0-9._-]{0,63}` and the expected numeric repository id. The tool
validates both locally and derives:

```text
project_run_id = "prun_v1_" + base32lower(
  sha256("nightshift.project-run.v1\0" + decimal_repository_id + "\0" + request_id)
)[0:26]
```

Here `sha256` returns the raw 32 digest bytes. `base32lower` means RFC 4648
base32 of those raw bytes using the standard alphabet, converted to lowercase,
with all terminal `=` padding removed before the first 26 characters are
taken. It never hashes or encodes the hexadecimal digest text.

The explicit request id is the idempotency key within one numeric repository.
A deliberate second run uses a new request id even when repository, SHA, and
brief are unchanged. A retry with the same repository id and request id plus
the same requested facts converges on the same record; changing any other fact
for that pair is a conflict. Reusing a request id for a different numeric
repository is allowed and produces a different project-run id. No global
request-id index is promised.

Deriving from the caller's validated expected id is intentional: it lets an
exact retry locate and validate the existing record before any network call.
For a new binding, the tool must still prove through GitHub that the expected
repository id is authoritative before it publishes anything. The stable
request-facts digest covers the normalized repository slug, expected repository
and owner ids, expected default branch and starting SHA, expected brief
SHA-256, the sorted declared check identities, exact Nightshift SHA, request id,
and derived project-run id. Observation timestamps, check-run ids, and other
GitHub response fields are excluded because they are not caller intent.

Three committed worked vectors, including the H1 target and the same request id
under two repository ids, live in
`docs/architecture/H1-ITEM1-ID-VECTORS.json`.

This increment does not read or write `meta.current_run_id`, `agent_runs`,
`worker_slots.run_id`, or `task_claims.run_id`. ADR-0002's fencing namespace is
not silently redefined. Item 5 must either add an explicit `project_run_id`
link or supersede ADR-0002 before it uses this identity for lifecycle fencing.

### 2. One authoritative write-once file outside Git

*The Git-worktree exclusion and the ancestor-trust sentence in this section
are superseded in part by
[ADR-0021](ADR-0021-trusted-state-root-ancestry.md); see that ADR for the
scoped form.*

The authority is a repository-keyed canonical JSON file:

```text
<state_root>/startups/<repository_id>/<project_run_id>.json
```

`state_root` is an explicit absolute argument with no default and no
environment fallback. It must already exist, be owned by the effective uid,
have mode `0700`, contain no symlink component, be outside `/srv/project`,
and any Git working tree, and reside on a filesystem that supports file and
directory `fsync` plus same-directory hard links. A uid-owned `0700` temporary
directory beneath `/tmp` or `/var/tmp` is valid; the final directory's
ownership, mode, and no-follow walk are the boundary, not a path-prefix
blacklist. Ancestor directories need not themselves be uid-owned or `0700`.

The descriptor records the immutable `state_root`, but no future workspace
path. Item 4 owns workspace allocation and must reuse the existing
`nightshift-workspace` convention rather than inherit an unmade path decision
from this item. The `startups` and numeric repository directories are mode
`0700`; the descriptor is mode `0600`. The repository-keyed directory makes
per-repository lookup bounded without introducing a second authority or extra
hard link. It contains no credential, request header, environment, raw API
response, raw error text, or brief contents.

The record is called **write-once**, not tamper-proof. A same-uid actor can
chmod and rewrite a file. The command never overwrites it, and strict canonical
validation detects malformed or internally inconsistent alteration. There is
no external authoritative copy of the returned full-file SHA-256 in item 1, so
a same-uid actor who rewrites a fully self-consistent record is **not detected
by this increment**. Item 5 must persist and compare the returned descriptor
digest before lifecycle state can bind to this record. Host-loss durability
and off-box backup are H3 work, not claimed here.

### 3. Crash-safe, no-clobber publication

The implementation opens `state_root`, `startups`, and the numeric repository
directory by file descriptor with `O_DIRECTORY|O_NOFOLLOW`, checking every path
component. It writes canonical bytes to a random same-directory temporary file
using `O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`, then `fsync`s the file.

It publishes with an atomic hard link from the temporary inode to the absent
final name, `fsync`s the directory, unlinks the temporary name, and `fsync`s
the directory again. `EEXIST` never overwrites: the winner is parsed and
validated. Matching request facts return `status: existing` without a GitHub
call or mtime change. This status proves only that the local write-once record
matches; it does not claim that the remote repository or check state remains
fresh. Different facts return `startup_conflict`. A malformed,
non-canonical, wrong-owner, wrong-mode, or digest-mismatched existing record is
`startup_corrupt` and is never adopted or repaired.

A crash before the hard link leaves no accepted startup. A crash after the
hard link leaves a complete accepted startup, possibly with an unaccepted
temporary hard-link name that later inspection may report but never treats as
another run.

### 4. Remote-observed GitHub binding

`nightshift-ctl project bind` accepts only `OWNER/REPO`, the equivalent
`https://github.com/OWNER/REPO`, or that HTTPS URL with terminal `.git`.
Credentials, ports, query strings, fragments, percent-encoded separators,
dot segments, control characters, SSH/scp syntax, and other hosts are refused.
The slug is parsed before it is inserted into an API path.

New bindings use bounded `gh api --method GET` calls only:

1. repository metadata;
2. the declared default-branch tip;
3. `.omni/brief.md` at the exact starting SHA;
4. check runs from the exact endpoint suffix
   `check-runs?filter=all&per_page=100`; and
5. the default-branch tip again immediately before local publication.

The check-runs response must declare no more than 100 total results. A larger
or paginated set is refused as `check_set_too_large`; the implementation never
accepts a truncated first page as the complete set. The committed fake-GitHub
fixture must include different default-filter and `filter=all` responses so a
regression that omits the query parameters fails.

The tool requires the expected numeric repository and owner ids; a private,
enabled, unarchived, non-fork user repository; the expected default branch;
the exact lowercase 40-hex starting SHA on both branch reads; a UTF-8 brief of
1–65,536 bytes with the expected SHA-256; at least one check run; every declared
`APP_ID:NAME` identity to appear at least once among the enumerated runs; and
every enumerated check run at that SHA to be `completed/success`. Identity is a
presence assertion, never a selector for conclusions. This follows #112:
checks are enumerated, including same-named duplicates; no run is selected by
name, no legacy status rollup is used, and absence of failure is never success.

The descriptor records the declared check identities and every observed check
run id/app/name/conclusion as a startup snapshot. The snapshot is evidence of
the bind-time observation only and must never become the input to a later gate.
It describes those checks as enforced by Nightshift, because the private H1
target cannot use native required checks. This observation does not lock the
remote ref. Item 4's CAS publication guard owns later freshness.

A private-repository 404 is `repository_undetermined` with exit `2` (absent and
unauthorized are indistinguishable), not a deterministic nonexistence verdict.
Authentication, rate-limit, and ambiguous transport responses take the same
named undetermined path. Errors are bounded and sanitized before output.

Check-run pagination and completeness live in one shared helper module used by
this command and reserved for the later release gate; that work must not grow a
second implementation of #112. GitHub invocation reuses the existing
`NIGHTSHIFT_CTL_GH` test seam and executes argv directly without a shell.

### 5. Plane-free configuration verbs only

The interface is:

```text
nightshift-ctl project bind --state-root ... --request-id ... \
  --repository ... --expect-repository-id ... --expect-owner-id ... \
  --expect-default-branch ... --expect-start-sha ... \
  --expect-brief-sha256 ... --required-check APP_ID:NAME \
  --nightshift-sha ...

nightshift-ctl project show --state-root ... --repository-id ... \
  --project-run-id ...
```

Both verbs are configuration-only and do not open `control.db`. The dispatcher
therefore gains a verb-granular `PLANE_FREE_VERBS` set containing only
`("project", "bind")` and `("project", "show")`; the whole `project` group is
never added to the existing group-granular `PLANE_FREE_COMMANDS`. `show` uses
the explicit numeric repository id for a bounded lookup beneath the
repository-keyed directory, then verifies that id against the descriptor and
the derived project-run id. The opaque project-run id is never reversed and
repository directories are never scanned. `show` performs strict local
validation and no network request. A unit test proves an unlisted
`(project, verb)` tuple does not qualify, so a later lifecycle verb cannot
silently inherit the exemption.

Canonical bytes are UTF-8:

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
```

This is a new, stricter, versioned canonical form for startup descriptors only;
the existing CLI `out()` serialization and every existing output remain
unchanged. The schema rejects unknown fields, duplicate declared checks, unbounded
strings, non-ASCII control characters, floats, and non-NFC text. It includes
`schema_version`, `kind`, `project_run_id`, `request_id`, a digest of the
caller-controlled request facts, observation timestamp, exact Nightshift SHA,
state-root path, repository identity/state/default branch/starting SHA,
brief path/blob/SHA-256/size, declared checks, observed check runs, and the
first/final branch observations. The command returns the full-file SHA-256;
that digest is not self-embedded.

Exit `0` means created or exact idempotent existing. After successful verb
parse, exit `2` is reserved for a named undetermined remote verdict, including
`repository_undetermined`. Exit `3` means a fully determined safe refusal such
as validated-but-invalid input, conflict, drift, or a red/missing declared
check. Exit `1` is reserved for local operational or persistence failure. Every
post-parse path emits one bounded JSON object with a stable `reason_code`.
Argparse's own pre-dispatch usage errors retain its existing plain-stderr exit
`2` behavior; this item does not rewrite the global parser. A caller therefore
uses the JSON body to distinguish a post-parse undetermined verdict from a
pre-dispatch usage failure, while exit `1` never carries an undetermined
verdict.

## Scope fence

This item does not create a repository, clone, fetch, push, branch, PR, issue,
comment, check, workspace, control-plane row, current-run pointer, claim, slot,
lease, heartbeat, lifecycle state, owner/takeover token, agent dispatch,
publication to the target, deployment, or repository-settings change. It does
not rename the existing `run start` output or alter the live self-dogfood run.
Product AC-15 (one run-identity namespace and removal of the overloaded
`run_id` output) is knowingly **not met** by this increment; #163 remains open.
The separate `project_run_id` is additive. Any link to ADR-0002 fencing is
deferred to item 5 and requires its own accepted ADR.

## Options rejected

1. **Org `control.db` as authority.** Rejected because it couples immutable
   startup configuration to mutable single-project coordination state, adds a
   migration, collides with the existing run meanings, and expands this item
   into H1 item 5.
2. **A new `<run_root>/run.db`.** Rejected for this item because one immutable
   record does not justify a database; lifecycle may add one later behind an
   explicit digest binding.
3. **Database plus file as co-authorities.** Rejected as split-brain.
4. **Git-tracked concrete startup record (Program D-070).** Rejected because
   either target or Nightshift must be mutated for each run. The committed
   schema and tests provide reproducible evidence without publishing private
   run state.
5. **Random or seed-only run id.** Random ids break lost-response idempotency;
   seed-only ids collapse deliberate repeats. An explicit request id plus a
   deterministic path-safe project-run id provides both properties.
6. **Atomic directory rename.** Rejected because item 1 need not create the
   workspace tree and directory replacement semantics are harder to make
   no-clobber. One atomic hard-linked file is sufficient.
7. **Name-selected required checks.** Rejected by the executed #112 evidence;
   every check run at the exact SHA must be explicitly green.

## Consequences, cost, and reversal triggers

The increment adds no service, dependency, database migration, or target
mutation. A new binding costs five GitHub GETs and one small private file; an
exact retry costs no network call. At 100 runs this is roughly 500 API reads and
well under 1 MB of descriptor data.

The network-free retry property requires the caller to repeat the correct
numeric repository id. A changed or mistyped expected id addresses a separate
repository-scoped path, then fails remote identity proof before publication;
it is not reported as a local conflict.

The record is host-local until later backup work; total host loss can lose it.
The remote branch can move after the second read. Post-creation, fully
self-consistent same-uid tampering is not detected by item 1 because no durable
external digest authority is created here. Those limitations are explicit
rather than solved vertically in item 1.

An operator must provision a persistent root explicitly, for example
`install -d -m 0700 "$HOME/.nightshift"`; the command will not silently create
or relax it. Temporary uid-owned `0700` roots remain valid for tests and the
read-only acceptance probe.

The opaque hash id is intentionally not time-sortable. Add a `created_at`-keyed
index or hard link, without changing v1 identity, when the first consumer must
enumerate runs in creation order. Revisit request-id scoping if the first
downstream consumer tries to use it as a global log correlation or scheduler
dedupe key; add a separate global correlation id rather than changing v1.
Revisit the authority if two hosts must bind
the same run concurrently, a non-GitHub H4
target is selected, mutable descriptor fields become necessary, or item 5
cannot attach lifecycle state to `project_run_id` plus the descriptor SHA-256
without ambiguity. In each case preserve v1 as an immutable historical input;
supersede it rather than rewriting it.
