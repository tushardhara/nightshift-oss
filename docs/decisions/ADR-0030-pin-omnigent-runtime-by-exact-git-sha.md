# ADR-0030: Pin the Omnigent runtime by exact Git SHA

Date: 2026-08-27 · Status: accepted
Deciders: program and staff · Proposers: Claude and Codex review for issue #190
Spec: GitHub issue #190

## ELI5

Nightshift's code was locked to a reviewed commit, but the command that installed
Omnigent still asked for "the latest Omnigent". That is like checking the recipe
carefully and then letting the shop choose an unknown ingredient at delivery
time. Two identical Nightshift checkouts could therefore run different code.

We will keep one small lock file in Git. It names the Omnigent repository and
the complete 40-character commit. Installation must use that exact address. A
shared checker then reads the installer's own receipt and refuses to continue
unless both the downloaded and requested commits match the lock.

## Context

Issue #190 reproduced a runtime boundary defect in Omnigent: bundled Python tool
code loaded from the right agent bundle, but the tool received that bundle path
instead of the worker's persisted Git workspace. The Nightshift publisher
correctly failed closed. The fix is reviewed as Omnigent commit
`ef39b91aff4050567ca0c49b303794faf7889b0c` and is proposed upstream in
`omnigent-ai/omnigent#5637`. Its source tree is byte-identical to the
independently tested commit `e6ebc7bce81c82b5002db4a8f865409c2f891d11`;
the new commit adds the required DCO sign-off without rewriting published
history.

`deploy/bootstrap.sh` currently runs `uv tool install ... omnigent`. That name
has no version or source identity. The exact-SHA release plan records the text
from `omnigent --version`, but does not compare it with a reviewed dependency.
Consequently, a green Nightshift Git/content receipt does not prove which
Omnigent runner executed it.

## Options considered

### A — Inline pin plus preparation-time receipt check (Claude proposal)

Put the repository and commit directly in `bootstrap.sh`, then make release
preparation inspect the installer's PEP 610 `direct_url.json` receipt. This is
the smallest diff and correctly avoids trusting `omnigent --version` as its own
proof. It lost because bootstrap, preflight, and the later cutover would each
have a different amount of protection, and preparation alone cannot detect a
runtime replaced between preparation and launch.

### B — Git-tracked lock plus one shared identity checker (Codex proposal)

Store repository, full commit, distribution, and expected version in a JSON
lock. One standard-library helper renders the exact install requirement and
validates PEP 610 metadata. Bootstrap, preflight, preparation, launch, and
postflight all use it. This is the chosen option: it adds more tests and one
helper, but prevents disk/live drift and keeps one expected identity.

### C — Pin only the package version

Use `omnigent==<version>`. Rejected for this cutover because the reviewed fix is
not in a published release and a version alone does not identify the reviewed
source commit.

### D — Build and copy a wheel by hand on the VPS

Rejected because an operator-local artifact has no durable review path and is
harder to reproduce. A wheel digest may later supplement the Git identity, but
it must be produced by a reviewed release process rather than substituted by
hand.

### E — Wait for the upstream PR to merge

Rejected as a blocker for #190 because an external maintainer controls that
schedule. The reviewed commit already exists in the owner's fork and installs
successfully by its full SHA. Moving to an upstream merge commit will be a
separate one-line reviewed lock update.

## Decision

Add `deploy/omnigent-runtime.lock.json` containing the distribution, HTTPS Git
repository, expected version, and full reviewed commit. The standard-library
`deploy/omnigent-runtime-identity.py` helper validates the lock, renders the
exact PEP 508 requirement, and checks the installed distribution's PEP 610
`direct_url.json`. It requires Git VCS, the locked repository, the full requested
revision, the full resolved commit, and the expected distribution version.
Bootstrap installs the rendered requirement and fails before later setup when
that check disagrees.

The exact-SHA release preparation reads the lock from the candidate Nightshift
checkout, performs the same validation against the selected Omnigent Python,
and records the canonical identity in the sealed plan. It rechecks before
launch and in postflight so a prepare-to-run replacement cannot pass on an old
receipt. An absent, malformed, or mismatched identity is a refusal. Preflight
uses the same check for normal operator diagnostics.

The existing server/host wrappers, Program registration, databases, process
control, and rollback behavior do not change. This decision does not authorize
a production restart or database mutation.

## Review evidence

Claude Staff approved the reconciled ADR and implementation on 2026-08-27. The
independent Codex review initially found two fail-closed ordering gaps: bundle
loading preceded identity verification, and postflight could continue after
detecting identity drift. Both were fixed and re-reviewed. The final focused
runtime/release suite passed 47 tests; the broader affected deployment suite
passed 202 tests before those two additional refusal tests were added. Shell
syntax, Python compilation, and Git diff checks passed. A fresh
`uv tool install` from the signed full SHA produced PEP 610 metadata whose
repository, requested revision, and resolved commit all matched the lock, and
`omnigent --version` reported `ef39b91a`.

## Consequences

- Re-running bootstrap from the same Nightshift SHA selects the same Omnigent
  source commit.
- Exact-SHA preparation cannot silently use a different installed Omnigent.
- Installing from Git requires GitHub availability and a build-capable Node
  toolchain; the same source install has been proven in an isolated local tool
  directory before this decision.
- This locks Omnigent's own source identity, not every transitive Python or Node
  dependency. A complete reproducible-environment lock is a separate supply-chain
  improvement and is not evidence required by issue #190.
- The temporary source of truth is the owner's fork. Deleting that fork or the
  commit would break fresh installs, so it must remain until a reviewed lock
  update points to an upstream merged commit or a durable release artifact.

## Cost, scale, and reversibility

This remains tier 0. It adds no running service, database, paid API, or monthly
cost at today's volume or 100x. It adds one small JSON file, one standard-library
checker, and deterministic validation during install, preflight, and release.
The decision is reversible by a normal reviewed commit that changes the lock to
another tested full SHA.

## Reversal trigger

Replace the fork SHA when the upstream fix is merged and the upstream commit
passes the same focused tests and isolated Program-to-worker smoke. Replace the
Git requirement with an immutable release artifact only when CI publishes a
wheel with a reviewed source-SHA attestation and digest that Nightshift can
verify offline.
