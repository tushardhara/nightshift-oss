# ADR-0041: Split the release gate by execution capability

Date: 2026-08-28 · Status: **accepted** — `staff` review: **CONSISTENT** ([durable verdict](https://github.com/tushardhara/nightshift/issues/205#issuecomment-5452960896))

Deciders: `architect`

Conversation: `01a04857-1f0b-73a3-a5f0-e28ac8ea75a0`

Issue: #245

## Context

`release` is the only seat permitted to merge to the default branch, and its
candidate-merge procedure requires a local `tests/run.sh` gate before every
merge. When that command runs inside Codex CLI's default Linux sandbox, the
bubblewrap user namespace maps only the calling host UID. Root-owned host paths
such as `/`, `/home`, and `/tmp` consequently appear to be owned by the kernel
overflow UID, normally 65534. Paths outside the sandbox's declared writable
roots are also read-only.

`tests.test_project_bind.ProjectTrustedAncestry` contains host-environment tests
that deliberately inspect the real ownership and writability of the ancestry
chain. Those observations are meaningful on the bare host and in GitHub Actions,
but the UID-remapping sandbox cannot represent them faithfully. The result is a
deterministic environmental failure whenever the test command is routed through
that sandbox: root-owned paths appear foreign and a marker write beneath the
live home ancestry fails with `EROFS`. Issue #245 reproduced the UID mapping
three times through Codex and independently through plain `bwrap
--unshare-user`.

The production ownership rule is security-relevant. ADR-0021 classifies an
ancestry component as foreign when its owner is neither root nor the effective
user. An overflow UID inside a user namespace is not evidence that the host
owner was root, so production code and its classifier tests must not reinterpret
UID 65534 as trusted.

This ADR supplements ADR-0003; it does not edit or supersede ADR-0003's decision
that sandbox capability must be proven by execution and that a boundary is not
weakened merely to unblock a run. It also preserves ADR-0021's ownership rule
unchanged. The decision here is where environment-dependent verification runs,
not what ownership is considered trusted.

## Options considered

### A — Run the candidate suite outside the Codex sandbox

Run `release`'s complete candidate-merge test command as an ordinary host
subprocess with the real host UID view.

Rejected. The candidate controls Python and shell code executed by the suite.
Running that code unsandboxed under the `release` host identity would give a PR
the opportunity to read host credentials and reach paths that the Codex sandbox
currently protects. Moving arbitrary candidate execution across the boundary is
a larger trust grant than the ownership test requires.

A fixed, audited probe from trusted default-branch code could safely run on the
host if a future deployment check needs one. That is not permission to execute
the candidate's `tests/run.sh` outside confinement.

### B — Treat remapped ownership as trusted in a known sandbox

Detect the user namespace, or observe UID 65534 on `/`, and relax
`ProjectTrustedAncestry` or the production classifier for that context.

Rejected. UID 65534 does not prove that the corresponding host path is owned by
root; it only proves that its host owner is unmapped in the current namespace.
Trusting it would make a namespace artifact indistinguishable from genuinely
foreign ownership. This would weaken ADR-0021's security predicate rather than
solve an execution-capability mismatch.

### C — Change Codex sandbox configuration or select legacy Landlock

Expand `workspace-write` writable roots, or enable the deprecated
`use_legacy_landlock` backend so tests see host UIDs directly.

Rejected as the durable design. Additional writable roots can address the
`EROFS` symptom but cannot make root-owned paths appear as UID 0 inside a user
namespace. Legacy Landlock preserves host ownership visibility in the observed
CLI version, but it is deprecated and off by default. Making a release invariant
depend on a backend scheduled for removal would exchange today's deterministic
failure for an unowned future outage. Widening writable roots would also grant
more host write capability than these tests require.

### D — Split the gate by execution capability

Keep arbitrary candidate execution inside the Codex sandbox. Mark only the
tests whose contract inherently requires truthful host-UID ancestry, allow that
exact approved set to report as expected skips in the remapped local
environment, and require those same tests to pass on the candidate SHA in
GitHub Actions or another disposable, credential-free runner with real host
ownership visibility.

Accepted. This preserves confinement for candidate-controlled code, preserves
the ownership predicate, and moves each assertion to an environment capable of
observing the fact it asserts. The split remains fail-closed because local skips
are allowlisted exactly and the external result is mandatory before merge.

## Decision

The release gate is split by execution capability:

1. `release` continues to run the local candidate gate inside the Codex sandbox.
   It runs every hermetic and sandbox-compatible test, including the
   table-driven tests of ADR-0021's ownership classifier.
2. Tests whose contract inherently requires truthful host-UID ancestry are
   explicitly tagged. Only that reviewed tag, not a general failure pattern,
   permits an expected local skip when the runner proves that host UID ownership
   is remapped.
3. The local gate compares reported skips with an exact approved manifest. A
   missing expected skip, a newly skipped test, an untagged skip, or any other
   unexpected difference fails the gate. Expected skips are reported visibly;
   they are never recorded as passes.
4. The same tagged host-UID-ancestry tests remain mandatory on the exact
   candidate SHA in GitHub Actions or another disposable, credential-free runner
   that exposes real ownership. `release` must verify the required successful
   status for that SHA before merging.
5. Arbitrary candidate test code must not be run unsandboxed on the Nightshift
   delivery host. If hosted CI cannot represent the deployment ancestry
   semantics, the replacement is a disposable credential-free host runner. A
   same-host check, if ever required, is limited to a fixed audited probe from
   trusted/default-branch code and does not substitute for candidate tests.
6. Neither production ancestry classification nor its expected outcomes change.
   In particular, UID 65534 is not treated as root and `foreign_owner` retains
   ADR-0021's meaning.

PR #249's fail-fast diagnostic must therefore not abort the entire suite before
ordinary tests run. Its implementation may retain a clear remapping diagnostic
and hermetic bubblewrap regression coverage, but it must implement the targeted
tag, exact skip-manifest check, and continued execution of all other tests. PR
#249 must reference this ADR.

## Consequences

**Security boundary preserved.** Candidate-controlled tests remain confined.
No new writable root, unsandboxed release command, or special trust for an
overflow UID is introduced.

**Release availability restored without hiding unrelated failures.** A sandbox
that cannot observe host ownership no longer prevents compatible tests from
running. Failures elsewhere in the suite remain visible instead of being masked
by a top-level preflight abort.

**Two pieces of evidence form one gate.** The local sandboxed result alone is
insufficient when the approved manifest contains host-UID skips. The external
required status must refer to the exact candidate SHA, and `release` owns the
join before merge.

**Skip governance becomes security-relevant.** Adding or changing a tagged test
or the approved skip manifest changes which evidence may be absent locally. Such
a change requires review against this ADR; a broad environment-based skip or
skip-on-failure mechanism is outside the decision.

**Runner semantics remain observable.** The external runner must demonstrate
that it sees the ownership conditions the tagged tests claim to exercise. A
green result obtained by skipping the same tests is not acceptable evidence.

**Operational cost is unchanged at the current tier.** The design uses the
existing local gate and required GitHub Actions run. A dedicated disposable
runner is deferred unless hosted CI cannot represent the required semantics.

## Reversal trigger

Collapse the split gate only when Codex ships a supported, non-deprecated
confinement backend that preserves truthful host-ownership visibility while
maintaining isolation equivalent to the current sandbox for writes, credentials,
and network access, and that equivalence is verified by execution on every
supported Nightshift host.

If GitHub Actions or the selected external runner ceases to represent the
deployment ancestry semantics, replace it with a disposable credential-free
host runner. That event does not authorize unsandboxed execution of arbitrary
candidate code on the delivery host or relaxation of ADR-0021's ownership rule.
