# ADR-0003: Sandbox capability is proven by execution; the host AppArmor profile is the owner's action, not ours

Date: 2026-08-22 · Status: **accepted** — `staff` review: consistent-with (docs/decisions/ADR-0001-0003-staff-review.md)
Deciders: program · Proposers: `architect` (Codex), plus live evidence from this box
Conversation: 370d8bf6ad1442dd988d9a9f77620da3 · Issue: #5

## Context

Found on this box during discovery, not by reading code. Every Codex-native seat —
`architect`, `release`, `qa`, `eng_codex`, `reviewer_codex` — dies at its first tool call with
`bwrap: setting up uid map: Permission denied`.

Codex runs each model-issued shell command inside its own bubblewrap sandbox. This host is
stock Ubuntu 24.04 with `kernel.apparmor_restrict_unprivileged_userns = 1`, and
`bubblewrap 0.9.0-1ubuntu0.1` ships **no** AppArmor profile (`/etc/apparmor.d/` holds
`unprivileged_userns` and `lxc-usernsexec`; no `bwrap`). Unprivileged user namespaces are
refused host-wide — reproducible with no agent involved:

```
$ unshare -U -r /bin/true
unshare: write failed /proc/self/uid_map: Operation not permitted
```

Three checks should have caught it and none did, because all three test the same wrong thing:
`provision-ubuntu.sh:88-90`, `preflight.sh:37` and `bootstrap.sh:37` each run
`command -v bwrap` — that the binary is **installed**, never that it **runs**. A box in
exactly this broken state passes preflight cleanly.

`docs/TRUST-BOUNDARY.md:67` asserts that bwrap *"ships an AppArmor profile on 24.04"* and on
that basis carries the mount-namespace duty instead of systemd. The claim is false on a stock
box, and it is load-bearing: it is the documented justification for the whole sandbox choice.

Consequence, which makes this more than an install bug: `qa` and `release` are both
Codex-native, so one environmental defect removes the QA gate **and** the only role permitted
to write the default branch. Codex: *"This is a system availability defect, not merely a
staffing inconvenience."*

## Options considered

### A — Set `sandbox_mode = "danger-full-access"` in `~/.codex/config.toml`
One line, unblocks everything immediately. **Rejected, and not by me alone.** The brief
forbids weakening sandboxing to make the run easier; Codex reached the same verdict
independently — *"The system must not recommend `danger-full-access` or disabling the host
restriction as its normal workaround"* and *"Disabling sandboxing is not an acceptable
reversible experiment. It invalidates the stated trust boundary."* It would also remove the
write-protection on `~/.codex`, `~/.config/gh` and system paths that the trust boundary
depends on.

### B — `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`
Host-wide removal of a kernel hardening default to make one program work. Rejected as
disproportionate: it weakens every process on the box, not just bubblewrap.

### C — A narrow AppArmor profile for `/usr/bin/bwrap` granting `userns`
Restores the intended boundary rather than removing it, and is the mechanism Ubuntu provides
for exactly this case. **Taken** — as the recommended fix, executed by the owner.

### D — Route around it by re-seating `qa` and `release` onto Claude
Rejected for now: the agent configs are owned by `platform` and gated by ADR (`.omni/decisions.md`
row A4), the live configs sit in a directory this run must not modify, and quietly collapsing
cross-vendor independence to unblock ourselves is the same class of error as option A. Codex's
recommendation — a *documented* capability-based reassignment mechanism — is the right version
of this, and it is product work, not a run-time improvisation.

## Decision

1. **Capability is proven by execution.** `preflight.sh` and `provision-ubuntu.sh` must run a
   representative sandbox as the actual runtime user — minimally `bwrap --ro-bind / / /bin/true`
   exiting 0, exercising user-namespace creation and the required bind behaviour — not
   `command -v bwrap` and not `bwrap --version`. Provisioning fails closed with a diagnostic
   naming the userns/AppArmor cause.
2. **`docs/TRUST-BOUNDARY.md:67` is corrected** to describe a tested capability rather than a
   package assumption. A security boundary that is asserted is not a boundary.
3. **The AppArmor profile is the owner's action on this box**, published as an exact command
   in `.omni/blocked.md` and on issue #6/#5. If Nightshift ever ships such a profile, it must
   be explicit, versioned, narrowly scoped to the installed bubblewrap path, loaded and then
   **tested**, and documented as part of the trust boundary.
4. **No boundary is weakened to unblock this run.** Work continues on Claude seats; PRs queue
   behind the unavailable gates rather than around them. Deferring a gate is not weakening one.

## Consequences

**Easy:** the failure becomes loud at provision time instead of silent until first dispatch;
the trust boundary starts describing reality.

**Hard:** until the owner acts, half the org cannot execute and nothing reaches the default
branch. That is the honest cost of not taking option A, and it is the correct trade for a
project whose brief explicitly protects sandboxing.

**Forecloses:** nothing. Every option above remains available to the owner.

## Cost

Zero. A one-line probe replacing a one-line presence check.

## One-way door?

**The profile is** — Codex listed "distributing a custom host AppArmor policy" among the few
genuine one-way doors, since a shipped security policy is hard to withdraw from installed
hosts. That is precisely why this ADR keeps the profile as an owner action with a published
command, and puts only the *probe* in the product.

## Scale tier

T0. Not a capacity concern.

## Reversal trigger

Revisit if a supported platform emerges where a functional bwrap probe cannot pass and the
sandbox must be delivered another way (a different confinement backend, or a documented
degraded mode). Revisit option D — capability-based reassignment of `qa` and `release` — if a
vendor is hard-down for longer than a wave; that is a product mechanism to design, not an
improvisation to reach for under pressure.
