# ADR-0094: The record says which seat wrote the code, because the act cannot be gated

Date: 2026-09-07 · Status: accepted
Deciders: platform (owner asleep; decisions taken from the evidence recorded below)
Issue: #732, child of epic #716 (change 5 of six)
Supersedes: nothing.
Depends on: ADR-0009's finding about the policy layer, which is why this ADR exists in the shape
it does rather than as a tool-call deny.

## Context

#716 item F: the coordinator authored ~93% of the commits in the run this epic came from,
against its own Hard Rule 1 (`program/config.yaml:90`) — *"NO CODE. Source and tests belong to
crews, platform, or oncall — always. You author Markdown only."* — and *"nine rounds of fixes
went in with nobody reviewing them."* The epic asks for a rule that is **enforced rather than
stated**.

## What could not be built, and why that is the first section

The obvious enforcement is a root policy denying `git commit` of source paths. It is the shape
the previous four changes in this epic all took: find the chokepoint, gate it there.

**ADR-0009 proposed that and rejected it before implementation.** Its v2 adopted a policy-layer
deny; `architect`'s re-check refuted it, and v3/v4 removed it from the design. (Stated precisely
because an earlier draft of this ADR said ADR-0009 "built and removed" it, which is wrong — `git
log -S` finds no such deny ever added to `omnipods_policies.py`. It was a design review, not a
build. The distinction matters: what follows is a reviewed argument, not a measured experiment,
and it should be re-openable by evidence rather than treated as settled by history.) From the v4
revision note, describing v2's error:

> *"The policy-layer deny is not single-event evaluable, contrary to v2's explicit claim.
> `deploy/policies/omnipods_policies.py` sees `{"type": "tool_call", "data": {"name": ...,
> "arguments": {"command": cmd}}}` for shell tools — no authoritative cwd or resolved repository
> target. `git add -- .omni/STATUS.md` run in `/srv/project` and the identical command run in a
> dedicated worktree are indistinguishable to the policy."*

Re-verified here, with one addition ADR-0009 did not need to state: the event carries **no caller
identity** either. Root policies govern the whole tree (#118), so a deny of "commit source" would
deny it for the crews, whose entire job it is. There is no signal at that layer to separate them.

Recorded first, and at length, because #716 keeps a RETRACTED section for a design that was
rebuilt without its rejection being read, and because the cost of re-deriving this is a day.

## Two facts that bound what is left

- **The coordinator has raw shell** with `write_paths: ["."]` and no commit wrapper. The crews
  commit through `nightshift_publish.py`, which hardcodes `GIT_AUTHOR_NAME` in the tool itself;
  the coordinator's author is whatever git config happens to be active.
- **Author identity is forgeable.** Nothing stops `--author`; #725 records that seat identity at
  the CLI is unauthenticated.

## Decision

A merge-time gate on the record: a commit touching source may not be authored by a seat Hard
Rule 1 does not entitle, or by a name that identifies no seat. PR-scoped, on the branch's own
commits, via `git merge-base` — mirroring the existing `project conformance gate`.

**Source** is `bin/`, `deploy/`, `tests/`, `.github/`, plus three things under `program/` that a
review pointed out were otherwise unguarded: `program/config.yaml` (Hard Rule 1 itself),
`program/conformance-rules.json`, and `program/agents/**/tools/**` (real code, including the
publish tool that hardcodes the crews' git identity). Seat configs stay unguarded — a seat
editing its own charter is not what this stops.

**Entitled** is crews, platform and oncall, from Hard Rule 1's own words. The refused set is
therefore the rest of the seat roster plus the unattributed names, matched case-insensitively on
the leading token so that `Program`, `crew_alpha lead` and `crew_alpha.eng_codex` all resolve
correctly. An author the roster does not know — a human — **passes**: refusing unknown names
would mean guessing at identity, and a gate that blocks a new contributor's first commit gets
switched off rather than fixed.

**The second half is the load-bearing one.** In the run behind #716 the coordinator's source
commits did not say `program`. One hundred and fourteen of them said `ci` — the box's fallback
git identity — which is why the epic's attribution pass had to reconstruct authorship by hand,
and why its first conclusion about the reviewer seats was wrong and had to be retracted. A source
commit whose author is the machine's default identity cannot be attributed to anyone.

### What this is worth, stated plainly

It is **not proof of authorship**, and must not be described as one. A coordinator that passes
`--author=crew_alpha` gets through, and there is a test asserting exactly that so no later reader
can mistake this gate for something stronger.

What changes is the default. The violation stops happening by omission — by a shell inheriting a
git config — and starts requiring an affirmative lie. That is a real change in cost and in what
the record means afterwards, and it is the most this layer can honestly claim while #725 is open.

## It is not vacuous

Run against `main` when written: **32 of the last 60 non-merge commits** touch source under a
name Hard Rule 1 does not entitle — 10 authored by `staff`, the rest by `program` and by
identities naming no seat. (An earlier draft of this section claimed "12 in the last 60". That
was wrong: 12 was the count in the last **200**, and in the last 60 the first version of this
guard flagged **zero** — because it permitted `staff`, which is what the shared checkout's own
git config sets `user.name` to. The section asserting the gate bites was the one place the
numbers had to be right, and they were not.)

Scoping to a branch's own commits is what makes this enforceable now rather than a demand to
rewrite history.

## Options considered

### A. Root policy denying source commits (rejected)
ADR-0009, above. Not a fresh judgement — a re-reading of one already made and tested.

### B. Extend `guard-commit` to check staged paths per committer (rejected)
`guard-commit` already runs as a pre-commit hook and reads the staged index mechanically, so it
looked promising. Two problems: it is bypassed by `--no-verify` and by writing the ref directly
with `commit-tree`/`update-ref` — which `nightshift_publish.py` itself does — and it would need
the same non-forgeable identity that does not exist. Weaker than A's failure, in the same place.

### C. Give the coordinator a commit wrapper with a hardcoded identity (deferred, complementary)
This is what makes `program` an honest signal rather than a default, and it is the natural
partner to this ADR. It does not belong here because a wrapper only helps if it is the *only*
path, and the coordinator's raw shell means it is not. Worth doing when #725 is done.

### D. Merge-time gate on the record (chosen)

## Out of scope

- **Nothing about seat taxonomy is out of scope any more.** An earlier draft excluded `staff`,
  `product` and `release` as "a different question". A review then showed that made the gate
  vacuous: `staff` is exactly what the shared checkout's git config names, so the omission path
  ran straight through the exclusion. Hard Rule 1 names three entitled groups; this now enforces
  that sentence rather than a subset of it that happened to leave the live hole open.
- **The second cause #716 names** — an owner-scoped resume instruction telling Program to
  implement directly — is not in this repository. It was searched for; it existed only in the
  run's transcript. Recorded so nobody hunts for it again.

## Acceptance boundary

Both directions, against temporary git repositories:

- The coordinator writing source is refused; a crew writing the identical file passes.
- An unattributed identity writing source is refused; the same identity writing docs passes.
- The coordinator writing Markdown passes — a gate that stopped it doing its actual job would be
  switched off within a day, and this ADR would rather be enforceable than maximal.
- A merge commit is not judged, asserted on the enumeration rather than the outcome: `git show`
  prints nothing for a merge, so an outcome-only assertion would hold even with `--no-merges`
  removed and the protection would be incidental.
- Each of the four source trees is covered, asserted one at a time.
- The CLI's exit status is the contract, since the workflow step fails on that alone.
- A forged author is shown NOT to be detected.

## Consequences

The org gains a merge-time answer to "who wrote this", which it did not have — the question #716
had to answer by hand, twice, and got wrong once.

It does not gain a guarantee, and the gap is precise: this stops the accidental case and prices
the deliberate one. If the coordinator starts forging crew identities, this gate will report
clean while the rule is broken, and the only signal left will be review — which changes 2 and 3
have just made routable. That is the shape of the remaining risk, and #725 is what closes it.

## One-way door and reversal trigger

Not a one-way door: the gate is one workflow step and one module; deleting both restores the
previous state, and no other code depends on either.

Reverse if it refuses legitimate work — which, given the author allowlist is a two-name refusal
rather than a permit list, would most likely mean a seat name this ADR did not anticipate is
being read as unattributed. Widening the refused set, rather than the permitted one, is the
deliberate design: an unknown author is allowed through, because guessing at seat taxonomy is
how a gate starts blocking work it was never meant to judge.
