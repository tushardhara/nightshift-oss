# Conventions

Owned by `staff`. The written rules reviewers judge style disputes against — a
convention that lives only in someone's head cannot settle anything. Additive:
new rules get a date and a reason; retired rules stay, struck through, with why.

## 2026-08-25 — Boundary probes never follow symlinks

Any filesystem entry inspected to establish an ownership, trust, repository,
or containment boundary must be probed with `follow_symlinks=False` (or opened
with the platform's no-follow equivalent and inspected through the resulting
file descriptor). Code must classify the entry actually present at the
boundary, not the type of its target. If the contract needs the target, it
opens that target separately through an explicit, bounded, no-follow walk.

Reason: following a `.git` symlink while path components themselves are opened
with `O_NOFOLLOW` created two meanings of “boundary” in PR #166. It also turned
an ambiguous entry into an apparently ordinary directory before the code could
apply the required fail-closed policy. ADR-0021 makes the no-follow rule
citable for this and later filesystem-security reviews.
