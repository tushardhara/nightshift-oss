# ADR-0021 — Scope Git-worktree exclusion to trusted state-root ancestry

- **Date:** 2026-08-25
- **Status:** Accepted — Product READY on the exact Option F contract
  ([verdict](https://github.com/tushardhara/nightshift/pull/166#issuecomment-5409288633));
  source implementation remains gated on `staff` review of this ADR
- **Decider:** Program, adopting Product's READY contract after the cross-vendor
  `staff` review of the PR #166 ancestry-classification failure
- **Scope:** issue #102, H1 item 1 and PR #166 only
- **Supersedes:** ADR-0020 §2, and only these two clauses: the unconditional
  "…be outside `/srv/project`, and any Git working tree" requirement, and the
  sentence "Ancestor directories need not themselves be uid-owned or `0700`."
  ADR-0020 remains in force in every other respect, including all of §1, §3,
  §4 and §5.

## Context

[ADR-0020](ADR-0020-project-startup-descriptor.md) requires a private
`state_root` outside every Git working tree, while also allowing ancestors of
that root to be owned and writable by another uid. PR #166 initially tried to
verify the universal exclusion by probing every ancestor. That cannot establish
an invariant about a directory another actor may change during the probe.

The concrete failure at PR head
`82da58d68aa9f6c61dd732466b840641c29a5498` is in the pre-screen before the
bounded Git probe: `_check_git_boundary` stats `.git`, skips the probe when the
entry is neither a regular file nor a directory, and then proceeds. A foreign
uid able to write an ancestor such as shared sticky `/tmp` can replace its own
`.git` entry with a FIFO during that window without being able to replace the
service-owned `0700` state root beside it. The state root can consequently be
accepted while surrounded by a working tree that the command claimed to have
excluded.

This is not descriptor-byte tampering. ADR-0020's same-uid, post-publication
tamper disclaimer does not waive a false startup classification, and item 5's
future external descriptor digest cannot repair a record that was
misclassified before publication.

The contradiction is in ADR-0020 §2 itself: it makes an unconditional claim
about ancestors that it simultaneously declares outside the ownership
boundary. This ADR completes §2's existing statement that the final
directory's ownership, mode, and no-follow walk are the boundary. It narrows
the claim to the ancestor segment whose directory entries the service uid or
root controls, and records the residual rather than silently asserting more.

## Decision

### 1. Directory trust is ownership plus exclusive write permission

Let `E = os.geteuid()`. Each component of the absolute `state_root` path is
opened by file descriptor with `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` and inspected
with `os.fstat` on that already-open descriptor. A directory `d` is trusted if
and only if all three clauses hold:

```text
TRUSTED_DIR(d) =
  S_ISDIR(d.st_mode)
  and d.st_uid in (0, E)
  and d.st_mode & (S_IWGRP | S_IWOTH) == 0
```

Uid 0 is part of the trusted computing base: root can defeat every ownership,
mode, and no-follow check in this command. Excluding root-owned components
would add no protection and would make `full` ancestry unreachable on normal
systems.

The sticky bit is not an exemption. A root-owned `1777` `/tmp` is untrusted
because another uid can create its own `.git` entry there even though sticky
semantics prevent it from replacing the service uid's state-root entry.
Likewise, an effective-uid-owned `0777` directory is untrusted: ownership alone
does not provide exclusive write access.

If a directory fails more than one clause, its recorded reason is the first in
this fixed priority order:

1. `foreign_owner` when `st_uid not in (0, E)`;
2. `other_writable` when `S_IWOTH` is set;
3. `group_writable` when `S_IWGRP` is set.

The fixed order makes a multi-fault directory deterministic.

### 2. The deepest untrusted component bounds the claim

Let the path components in walk order be `d₀ = "/"` and
`d₁ … dₙ = state_root`. The trust boundary `B` is the deepest `dᵢ` for which
`TRUSTED_DIR(dᵢ)` is false. The trusted segment is strictly below `B`,
`d_{i+1} … dₙ`, including `state_root`. If no component fails, there is no
boundary and the trusted segment is the whole chain.

`ancestry_trust` is `full` exactly when no boundary exists. It is `partial`
when a boundary exists. Git-worktree classification runs only over the trusted
segment. Above and at `B`, the command performs no Git classification, takes no
sample, and makes no exclusion claim.

Inside the trusted segment, classification is fail-closed. Every `.git`
boundary probe uses `follow_symlinks=False`. `ENOENT` is the normal no-marker
case. A directory or regular file is passed to the existing bounded,
environment-scrubbed Git probe. A FIFO, socket, device, symlink, or any
non-`ENOENT` stat failure is refused as
`state_root_ambiguous_git_marker`, exit `3`. A confirmed working tree retains
the existing `invalid_state_root`, exit `3` behavior.

### 3. Partial ancestry is explicit and conditional

Under `partial`, the Git-worktree exclusion is verified for
`d_{i+1} … dₙ` **conditional on the entry for `d_{i+1}` in `B` not having been
substituted**. Above and at `B`, no claim is made. Under `full`, the exclusion
is verified unconditionally over the whole chain.

This is a scoped observation, not a weaker spelling of the old universal
claim. A default invocation accepts `partial` so the committed `/tmp`
acceptance probe remains valid, but the descriptor and response expose that
residual.

### 4. Strict mode is an argv-only environmental precondition

Both `project bind` and `project show` accept
`--require-trusted-ancestry`, a `store_true` flag with no argument, negated
form, environment variable, config file, or default-on behavior. When set, a
live `partial` observation is refused as
`state_root_untrusted_ancestry`, exit `3`, with one JSON object:

```json
{"ok": false, "reason_code": "state_root_untrusted_ancestry",
 "ancestry_trust": "partial",
 "ancestry_trust_boundary": {"depth": 1, "path": "/tmp", "reason": "other_writable"}}
```

The ancestry walk, boundary determination, trusted-segment classification,
and strict refusal occur inside `_open_state_root` before any descriptor lookup
on every `bind` and `show`, including exact replay. Strict mode is not part of
the stable request-facts digest. It is a precondition on the live local
environment, not caller intent about the run; including it would fork the
idempotency namespace without adding protection.

### 5. Schema v1 records bind-time ancestry evidence

Schema v1 gains a required top-level field:

```json
"ancestry_trust": "full" | "partial"
```

When and only when `ancestry_trust` is `partial`, it also contains:

```json
"ancestry_trust_boundary": {
  "depth": 0,
  "path": "/tmp",
  "reason": "foreign_owner" | "other_writable" | "group_writable"
}
```

`depth` is an integer from `0` through `255`; `0` denotes `/` and `1` the
first component below `/`. Its actual value must be less than the component
count of `state_root`. `path` is at most 4096 bytes, is `/` or begins with `/`,
is stable under `os.path.normpath`, has no `//` prefix, and is a byte-exact
proper ancestor of `state_root`. Existing NFC, control-character, type, and
size validation applies unchanged.

These fields are observed facts. They are excluded from the stable
request-facts digest and included in the canonical descriptor bytes and
therefore in the returned full-file SHA-256. Every parse of an existing record
validates the value enum, the presence biconditional, boundary field types and
enums, bounds, and proper-prefix relation. Failure is the existing
`startup_corrupt`, exit `3`; a corrupt record is never adopted or repaired.

Every successful `bind` and `show` response also has
`observed_ancestry_trust`, containing the live `full` or `partial` value. It is
a response-envelope field, not a descriptor field. The descriptor retains the
bind-time observation. `show` and the `existing` path do not compare recorded
and live ancestry and do not rewrite or touch the descriptor when they differ.
Strict mode has already enforced the live precondition before lookup.

`schema_version` remains `1`. The schema is introduced by the still-unmerged
PR #166, there is no deployed v1 population, and a descriptor without the new
required field is determinately `startup_corrupt`. ADR-0021 and its required
field must therefore merge as part of PR #166. If v1 ships first, this decision
requires a schema v2 and migration instead.

### 6. Canonical descriptor vectors become committed evidence

`docs/architecture/H1-ITEM1-DESCRIPTOR-VECTORS.json` will contain two schema-v1
descriptor vectors: one `full` and one `partial` with its boundary. Each records
the descriptor input, canonical UTF-8 bytes as hexadecimal, and the full-file
SHA-256. The authoritative H1 item-1 contract carries a regeneration command
that must produce an empty `diff -u`.

The existing project-run-id vectors and their preimage are unchanged because
descriptor observations are not inputs to `project_run_id`.

## Options considered

### A. Treat every `.git` entry as a repository

Rejected. An empty or forged `.git` entry in shared `/tmp` becomes a local
denial of service and contradicts ADR-0020's explicit acceptance of private
temporary roots.

### B. Ask Git about every ancestor

Rejected as a universal claim. It agrees with real Git for quiescent entries,
but an untrusted ancestor can change the entry between classification steps.
The implementation cannot honestly certify a fact that another uid controls.

### C. Sample twice and require quiescence

Rejected on executed evidence, not deferred. Holding the attacker's FIFO or
holding `.git` unlinked across both samples makes the samples agree and the
descriptor is accepted inside a real worktree. The hold strategy reproduced
the bypass 400/400 times, making the attack easier than the original timing
window. `ancestor_not_quiescent` and two-sample machinery must not be added.

### D. Require a fully trusted chain for every invocation

Rejected as the default and retained as the explicit
`--require-trusted-ancestry` mode. Default-on would reject the committed AC-13
probe beneath shared `/tmp`; an explicit strict precondition lets deployments
requiring the universal guarantee choose it without changing run identity.

### E. Drop Git-worktree exclusion

Rejected. Private authoritative state inside a checkout can be lost with the
checkout and exposes later Git-using lifecycle work to repository-local config
and hooks.

### F. Classify only the trusted segment and record the residual

Accepted. This is the smallest claim the ownership and mode boundary can
actually prove, preserves `/tmp` as a valid default-mode probe location, and
provides an explicit strict mode for deployments that require `full`.

## Consequences, costs, and reversal triggers

The change adds no service, database object, dependency, remote mutation, or
extra GitHub request. It adds one ownership/mode classification per opened path
component and probes `.git` only within the trusted suffix. A persistent root
beneath a normal root-owned and service-owned non-writable chain, including the
documented `$HOME/.nightshift`, records `full`; a private root beneath shared
`/tmp` records `partial` with `/tmp` and `other_writable`.

Under POSIX ACLs, the group-mode bits are the ACL mask, so an ACL granting a
foreign uid write access raises `S_IWGRP` and is caught. This ADR makes no claim
for NFSv4 ACLs, SELinux/AppArmor MAC, or network filesystems with divergent
permission semantics; it adds no syscalls to infer them.

A recorded `ancestry_trust` is a bind-time observation, not a continuing
guarantee about the environment. Exact replay remains network-free and
byte/mtime preserving. The live envelope field reveals a later trust change,
and strict mode refuses current `partial` ancestry before lookup.

Item 4 inherits a specific threat: under `partial`, a foreign uid controlling
the boundary may surround `state_root` with Git metadata. Item 1's only Git
operation is a scrubbed boolean probe, but item 4 will run Git for effect below
that root. Item 4's own ADR must decide whether it requires `full`, and whether
`-c safe.directory=*` remains justified for effectful Git operations. This is a
pointer, not item-1 scope.

Revisit the predicate when a supported platform does not expose POSIX
ownership/mode semantics, when a supported filesystem's access controls are
not represented by these bits, or when an effectful consumer cannot safely
operate under `partial`. Revisit the descriptor schema only through a new
version after schema v1 merges; never make a required v1 field optional to
paper over a deployed compatibility problem.
