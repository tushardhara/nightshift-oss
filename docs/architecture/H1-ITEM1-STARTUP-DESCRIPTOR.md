# H1 item 1 — repository/run binding contract

This is the implementation contract for issue #102's first unfinished H1
increment. ADR-0020, as superseded in part by ADR-0021, owns the decisions;
this document is the single authoritative acceptance-criteria namespace and
defines finished.

## Acceptance criteria

1. `project bind` records the existing H1 target's numeric repository and owner
   ids, canonical URL/name, private/enabled/unarchived/non-fork state, default
   branch, exact starting SHA, exact brief identity, check observations,
   Nightshift source SHA, request id, project-run id, and immutable state root
   in canonical schema-v1 JSON. It also records required `ancestry_trust` as
   exactly `full` or `partial`, and records `ancestry_trust_boundary` when and
   only when the value is `partial`. It records no future workspace path.
2. `project_run_id` is derived from the GitHub repository id plus explicit
   `request_id` after validating the caller's expected numeric id, is
   recomputable from published vectors, and is never emitted under the
   ambiguous key `run_id`. Request-id uniqueness is repository-scoped: a new
   request id permits a new run from an identical seed, while the same request
   id under a different numeric repository id is a separate namespace. A new
   binding is not published until GitHub proves the expected repository id.
   Derivation uses RFC 4648 base32 over raw SHA-256 digest bytes, lowercase,
   with padding removed before truncation; digest hex text is never encoded.
3. The descriptor is published at
   `<state_root>/startups/<repository_id>/<project_run_id>.json` beneath an
   explicit, persistent, uid-owned `0700` state root using
   file-descriptor-relative, no-symlink checks and an atomic no-overwrite hard
   link. Intermediate directories are `0700`; the file is `0600`. The
   repository-keyed directory permits bounded per-repository lookup. Each
   path component is opened with `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` and
   inspected by fd. Git-worktree exclusion is scoped to ADR-0021's trusted
   segment. Every `.git` probe there uses `follow_symlinks=False`; a symlink,
   FIFO, socket, device, or non-`ENOENT` stat failure is a fail-closed
   `state_root_ambiguous_git_marker` refusal.
4. A crash before publication leaves no accepted startup. A crash after
   publication leaves a complete canonical file. Temporary leftovers are not
   accepted state.
5. Exact replay returns the same project-run id and descriptor SHA-256 without
   a network call, byte change, or mtime change and reports only local-record
   status, never remote freshness. For one repository-id/request-id pair, any
   changed requested fact refuses and preserves the original bytes. Changing
   the repository id selects a separate repository-scoped namespace. Recorded
   ancestry fields round-trip byte-identically and are not compared with the
   live observation. Every successful response exposes the live value as
   `observed_ancestry_trust` without rewriting the descriptor.
6. Twenty concurrent identical binds produce one `created`, nineteen
   `existing`, and one byte-identical descriptor. Conflicting concurrent binds
   produce one winner and deterministic refusals; none overwrites.
7. GitHub validation uses bounded GETs only and proves the exact repository and
   owner ids, private usable repository state, default branch, first/final
   branch tip, exact brief blob/content digest, and at least one check run with
   every observed run explicitly `completed/success`. Enumeration pins
   `filter=all&per_page=100`; every declared `APP_ID:NAME` appears at least once
   and every enumerated run is green. More than 100 reported results refuses;
   a truncated or paginated first page never passes. A fixture whose default
   and `filter=all` responses differ proves the query cannot regress.
8. Same-named duplicate check runs are enumerated, not selected. If any is not
   successful, binding refuses. The legacy status API, mergeability, local refs,
   `FETCH_HEAD`, and `git rev-parse HEAD` never establish success or the pin.
9. Unsafe URLs/paths/modes/owners/symlinks, malformed or oversized GitHub data,
   missing/red/pending/skipped checks, branch drift, brief mismatch, private
   404/auth ambiguity, and corrupt existing records fail closed with one
   sanitized JSON object and a stable reason code after successful verb parse.
   Private-repository 404/auth/rate ambiguity is named undetermined and exits
   `2`; validated-but-invalid input and other determined denials exit `3`;
   local operational failures exit `1`. `state_root_untrusted_ancestry` and
   `state_root_ambiguous_git_marker` are determined denials and both exit `3`.
10. `project show` requires both the numeric repository id and project-run id,
    uses them for one bounded repository-keyed lookup, and strictly validates
    canonical bytes, schema, bounds, digests, file owner/mode, both requested
    ids, and the derived id without scanning repository directories, opening a
    control DB, or contacting GitHub. It validates the ancestry value enum,
    the `partial`/boundary presence biconditional, and boundary types, enums,
    bounds, and proper-prefix relation; any failure is `startup_corrupt` and is
    never repaired. It accepts argv-only `--require-trusted-ancestry` and
    evaluates that live precondition before descriptor lookup.
11. Existing control-plane schema and rows, `meta.current_run_id`, heartbeat,
    agent runs, slot/claim fencing columns, and every existing CLI output remain
    byte-for-byte unaffected by this feature.
12. Tests use the existing `NIGHTSHIFT_CTL_GH` seam with a fake `gh` boundary
    that rejects every non-GET invocation. Check enumeration is one shared
    helper available to the future release gate. Descriptor canonical JSON is
    a new v1 contract and does not alter existing `out()` bytes. No test creates
    or mutates a remote repository, and no artifact contains a
    credential, header, environment, raw stderr, or brief contents.
13. A read-only acceptance probe against only
    `tushardhara/nightshift-h1-target-08232203` creates a descriptor in a new
    uid-owned `0700` temporary private local state root, including under the
    system temporary directory, verifies it with `project show`, and
    proves the target's default SHA/branches/PRs/issues/settings did not change.
    Committed tests form that root beneath
    `os.path.realpath(tempfile.gettempdir())` and derive the expected live
    trust value, boundary path, and depth from the opened component chain; they
    never hard-code `/tmp`. This is required on macOS, where `/tmp` normally
    resolves through the symlink `/private/tmp`: the command receives the
    canonical real path so its no-follow walk does not traverse that symlink,
    while a separate no-follow test retains refusal of a caller-supplied
    symlink component. The Linux acceptance evidence uses the literal canonical
    `/tmp` topology and must assert exit `0`, `ancestry_trust: "partial"`, and
    boundary `{"depth":1,"path":"/tmp","reason":"other_writable"}`; its
    strict invocation exits `3` as `state_root_untrusted_ancestry` without
    mutating the target.
14. The focused suite demonstrably fails against parent
    `03f60c65ebee2a5e770a43001ed4c0473b204ac7` because the `project` namespace
    does not exist, then passes at the PR head. The full repository suite also
    passes at the exact PR head.
15. `docs/architecture/H1-ITEM1-ID-VECTORS.json` contains at least three
    repository-id/request-id tuples with canonical preimage bytes, SHA-256, and
    derived project-run id. The exact command below recomputes the file and
    produces an empty diff. The focused suite consumes the same vectors.
16. Only `project bind` and `project show` are plane-free through a
    verb-granular exemption. A unit test proves an unlisted verb in the same
    group does not qualify for the exemption. No whole-group exemption exists.
17. `project show` detects malformed and internally inconsistent records, but
    item 1 makes no claim to detect a fully self-consistent same-uid rewrite:
    no external authoritative descriptor digest is created in this increment.
    Item 5 must establish that binding before lifecycle state is allowed.
18. Let `E = os.geteuid()`. A path component opened with
    `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` is trusted exactly when it is a
    directory, its uid is `0` or `E`, and neither `S_IWGRP` nor `S_IWOTH` is
    set. Root is trusted; sticky is not an exemption. The deepest failing
    component is the boundary, and only the segment strictly below it through
    `state_root` is Git-classified. No failure means `full`; otherwise the
    observation is `partial`, with deterministic reason priority
    `foreign_owner`, `other_writable`, then `group_writable`. Writing the
    components as `d₀ = "/"` and `d₁ … dₙ = state_root`, and the boundary as
    `B = dᵢ`: under `partial`, the Git-worktree exclusion is verified for
    `d_{i+1} … dₙ` **conditional on the entry for `d_{i+1}` in `B` not having
    been substituted**. Above and at `B`, no claim is made. Under `full`, the
    exclusion is verified unconditionally over the whole chain. Both verbs
    accept only the default-off, argv-only `--require-trusted-ancestry`; a live
    `partial` observation then refuses before record lookup. The deterministic
    matrix below is required in addition to existing filesystem tests.
19. `docs/architecture/H1-ITEM1-DESCRIPTOR-VECTORS.json` contains two complete
    schema-v1 descriptors, one `full` and one `partial` with its boundary. Each
    vector records the descriptor input, canonical UTF-8 bytes as hexadecimal,
    and the full-file SHA-256. The committed regeneration command below
    reproduces it byte-for-byte, and the focused suite consumes the same
    vectors. AC-15 and the project-run-id vector file remain unchanged.

### AC-18 deterministic ancestry matrix

The fixture records whether `/`, the home parent, and `$HOME` satisfy the trust
predicate before environment-dependent full-chain cases. Evidence must show
that precondition met; it must never silently skip. The predicate is factored
into a pure function over `[(depth, st_uid, st_mode)]` plus `E`, returning
`("full"|"partial", boundary|None)`. Its table covers root `0755`, root `0775`,
root `0777`, root `1777`, effective-uid `0700`, `0750`, `0770`, and `0777`,
foreign-owner `0755` and `0777`, an all-trusted chain, and two failing
components. It pins sticky handling, failure priority, and selection of the
deepest failure.

For cases described as using the system temporary directory, committed tests
use `os.path.realpath(tempfile.gettempdir())` and calculate the expected
boundary path/depth from the actual opened chain. They do not assume the
canonical path is `/tmp`. In particular, macOS commonly maps the `/tmp` alias
to `/private/tmp`; the canonical target is passed to the command because the
boundary walk deliberately refuses symlink components. The separate Linux
acceptance evidence remains pinned to literal canonical `/tmp`, boundary depth
`1`, and `other_writable`.

The filesystem/CLI matrix is:

1. Private `0700` root below canonical `realpath(system temp)`, default: exit
   `0`; the recorded trust value and conditional boundary object exactly match
   the pure predicate's result for the opened chain. On the Linux acceptance
   host this is `partial` with `/tmp`, depth `1`, and `other_writable`.
2. Case 1 strict: if the derived value is `partial`, exit `3` as
   `state_root_untrusted_ancestry` with that same derived boundary in the
   single JSON refusal; if it is `full`, exit `0`. The Linux acceptance host
   must exercise and prove the `partial` refusal.
3. Private root below a full `$HOME` chain, default: exit `0`, recorded `full`,
   no `ancestry_trust_boundary` key.
4. Case 3 strict: exit `0`, bytes identical to case 3.
5. FIFO `.git` in the trusted segment: exit `3`,
   `state_root_ambiguous_git_marker`.
6. Symlink `.git` to a real worktree in the trusted segment: exit `3`,
   `state_root_ambiguous_git_marker`, without classifying through the link.
7. Genuine `.git` directory in the trusted segment: exit `3`,
   `invalid_state_root`.
8. Effective-uid-owned `0777` boundary with a FIFO `.git` at the boundary and
   private trusted descendants, default: exit `0`, recorded `partial` with
   `other_writable`; the FIFO is not classified.
9. Case 8 strict: exit `3`, `state_root_untrusted_ancestry`.
10. A same-uid helper flips a trusted-segment `.git` among absent, real
    directory, and FIFO while 200 binds run: every result is exit `0`, exit `3`
    `invalid_state_root`, or exit `3` `state_root_ambiguous_git_marker`; there
    is no exit `1`, traceback, quiescence code, or unhandled path.
11. Exact replay and `show` beneath canonical `realpath(system temp)`: recorded
    fields round-trip, bytes and mtime stay unchanged, and the envelope reports
    the same live value derived from the opened chain. Linux acceptance pins
    this value to `partial` at `/tmp`, depth `1`, `other_writable`.
12. `ancestor_not_quiescent` and two-sample/quiescence machinery are absent
    from the source tree.
13. Missing trust, `full` plus boundary, `partial` without boundary, unknown
    reason, and non-prefix boundary path each produce unchanged
    `startup_corrupt` records.
14. Record `full`, make a trusted ancestor `0777`, then replay default: exit
    `0` existing, recorded `full` and bytes unchanged, live envelope `partial`.
15. Case 14 strict: exit `3`, `state_root_untrusted_ancestry`, before lookup;
    the existing record remains untouched.

## Required commands and evidence

The implementation PR must record exact output for:

```text
python3 -m unittest -v tests.test_project_bind
python3 -m unittest discover -s tests -v
```

The committed id vectors must regenerate byte-for-byte:

```sh
python3 - <<'PY' | diff -u docs/architecture/H1-ITEM1-ID-VECTORS.json -
import base64, hashlib, json
inputs = [
    (1344257718, "h1-102-item1-20260825-a"),
    (1, "a"),
    (1344257719, "h1-102-item1-20260825-a"),
]
doc = {"schema_version": 1, "kind": "nightshift.project-run-id-vectors", "vectors": []}
for repository_id, request_id in inputs:
    raw = b"nightshift.project-run.v1\0" + str(repository_id).encode("ascii") + b"\0" + request_id.encode("ascii")
    digest = hashlib.sha256(raw).digest()
    doc["vectors"].append({
        "repository_id": repository_id,
        "request_id": request_id,
        "canonical_preimage_hex": raw.hex(),
        "sha256_hex": digest.hex(),
        "project_run_id": "prun_v1_" + base64.b32encode(digest).decode("ascii").lower().rstrip("=")[:26],
    })
print(json.dumps(doc, indent=2, ensure_ascii=False))
PY
```

The committed descriptor vectors must also regenerate byte-for-byte. This
command defines the vector inputs independently of the implementation:

```sh
python3 - <<'PY' | diff -u docs/architecture/H1-ITEM1-DESCRIPTOR-VECTORS.json -
import base64, hashlib, json

def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")

def project_run_id(repository_id, request_id):
    raw = (b"nightshift.project-run.v1\0"
           + str(repository_id).encode("ascii") + b"\0"
           + request_id.encode("ascii"))
    digest = hashlib.sha256(raw).digest()
    return ("prun_v1_" + base64.b32encode(digest).decode("ascii")
            .lower().rstrip("=")[:26])

def descriptor(*, repository_id, owner_id, request_id, repository,
               state_root, ancestry_trust, boundary=None):
    project_id = project_run_id(repository_id, request_id)
    start_sha = "a" * 40
    brief_sha256 = "c" * 64
    nightshift_sha = "d" * 40
    required_checks = [{"app_id": 1, "name": "tests"}]
    facts = {
        "schema_version": 1,
        "kind": "nightshift.project-startup-request-facts",
        "project_run_id": project_id,
        "request_id": request_id,
        "repository": repository,
        "repository_id": repository_id,
        "owner_id": owner_id,
        "default_branch": "main",
        "start_sha": start_sha,
        "brief_sha256": brief_sha256,
        "required_checks": required_checks,
        "nightshift_sha": nightshift_sha,
    }
    owner_login, name = repository.split("/", 1)
    value = {
        "schema_version": 1,
        "kind": "nightshift.project-startup",
        "project_run_id": project_id,
        "request_id": request_id,
        "request_facts_sha256": hashlib.sha256(canonical(facts)).hexdigest(),
        "observed_at": 1787643000,
        "nightshift_sha": nightshift_sha,
        "state_root": state_root,
        "ancestry_trust": ancestry_trust,
        "requested_repository": repository,
        "repository": {
            "id": repository_id,
            "owner_id": owner_id,
            "owner_login": owner_login,
            "owner_type": "User",
            "name": name,
            "full_name": repository,
            "html_url": "https://github.com/" + repository,
            "private": True,
            "disabled": False,
            "archived": False,
            "fork": False,
            "default_branch": "main",
            "start_sha": start_sha,
        },
        "brief": {
            "path": ".omni/brief.md",
            "blob_sha": "b" * 40,
            "sha256": brief_sha256,
            "size": 1,
        },
        "required_checks": required_checks,
        "observed_check_runs": [{
            "id": 1,
            "app_id": 1,
            "app_slug": "ci",
            "name": "tests",
            "status": "completed",
            "conclusion": "success",
        }],
        "branch_observations": {"first": start_sha, "final": start_sha},
    }
    if boundary is not None:
        value["ancestry_trust_boundary"] = boundary
    return value

inputs = [
    ("full", descriptor(
        repository_id=1, owner_id=2, request_id="a",
        repository="owner/project",
        state_root="/home/nightshift/.nightshift",
        ancestry_trust="full")),
    ("partial", descriptor(
        repository_id=1344257718, owner_id=2,
        request_id="h1-102-item1-20260825-a",
        repository="owner/project", state_root="/tmp/ns-vector",
        ancestry_trust="partial",
        boundary={"depth": 1, "path": "/tmp",
                  "reason": "other_writable"})),
]
doc = {
    "schema_version": 1,
    "kind": "nightshift.project-startup-descriptor-vectors",
    "vectors": [],
}
for name, value in inputs:
    raw = canonical(value)
    doc["vectors"].append({
        "name": name,
        "descriptor": value,
        "canonical_bytes_hex": raw.hex(),
        "sha256_hex": hashlib.sha256(raw).hexdigest(),
    })
print(json.dumps(doc, indent=2, ensure_ascii=False))
PY
```

QA must also run the focused file against the parent binary using
`NIGHTSHIFT_TEST_CTL` or an equivalent committed seam and record the expected
RED. Security review is mandatory because the change accepts external
repository/path input, invokes `gh`, and writes private filesystem state.

The live acceptance probe is read-only with respect to GitHub and uses a
temporary state root. It is not a deployment, lifecycle start, agent dispatch,
or proof that H1 is complete. Committed AC-15 — the project-run-id vector and
regeneration requirement — is binding and is not deferred. A separate,
historical Product proposal called for one unified run-identity namespace; that
proposal is not one of this document's numbered acceptance criteria. This item
adds `project_run_id` but does not rename either existing overloaded `run_id`
output or populate ADR-0002 fencing columns; #163 remains open and item 5 needs
its own accepted ADR before linking the namespaces.

## Definition of Ready

The original Product proposal's two design blockers were resolved by ADR-0020,
but that prior clearance does not authorize implementation of ADR-0021. Source
or test work may be dispatched only after `staff` returns zero blocking
findings on both ADR-0020 and the exact committed ADR-0021 revision present on
the branch being dispatched. ADR-0020 clearance alone is insufficient; no
source or test edit may begin before the combined verdict.
