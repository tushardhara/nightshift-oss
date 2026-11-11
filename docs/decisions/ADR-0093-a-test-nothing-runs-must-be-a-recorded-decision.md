# ADR-0093: A test that nothing runs must be a recorded decision, not an accident

Date: 2026-09-07 · Status: accepted
Deciders: platform (owner asleep; decisions taken from the enumeration recorded below)
Issue: #729, child of epic #716 (change 4 of six)
Supersedes: nothing. Extends `tests/dod_validate.py`'s D12 check to be bidirectional.
Unaffected: ADR-0025's DoD condition set, and #439's portability guardrail, whose mechanism
this borrows.

## Context

#716 change 4: *"Require proof that new work can actually run. The single most valuable
question of the run was 'does this actually execute?' Three times, something was written,
looked correct, and was never run by anything. Whoever adds a test or a check must show what
invokes it."*

The rule is right and, as prose, has the same problem as every other rule in this epic: nothing
enforces it. This ADR is the enforcement.

## A design that was rejected before it was built

The obvious guardrail is a census of `nightshift-ctl` subcommands and whether anything invokes
each. An enumeration was run before committing to it, and it killed the design:

Of 84 registered subcommands, roughly **14 are invoked by code**, **7 by nothing at all**, and
**63 only by prose** — `program/config.yaml` and the skills, telling an operator to type them.

That 63 is not decay. Prose invocation *is* the interface for those verbs: they exist to be run
by an agent reading instructions. A shrink-only gate over that inventory would be either
perpetually red about legitimate capability, or would need an allowlist naming nearly every
subcommand, which is not a gate. Building it would have produced something that looked like
compliance with change 4 while nagging about the wrong thing.

Recorded because a future reader will have the same idea, and because the enumeration is the
evidence against it. (It did surface one genuine finding in passing: the entire six-verb
`memory` group is invoked by nothing anywhere — which is #400's known deferred state, not a new
defect.)

## Decision

Same mechanism, different domain: **executable test artefacts that nothing executes.**

Every git-tracked test-shaped artefact under `tests/` — a `test_*.py` module anywhere, or a
shell script — is classified as:

- **discovered**, reached by `run_suite.py`'s own `unittest` discovery;
- **undiscovered**, which requires an entry in `tests/invocation_manifest.json` saying either
  what does run it (`invoked`, naming the file) or that nothing does (`debt`, with an owning
  issue).

The manifest contract is #439's, including the rule that a **stale entry fails**: an artefact
that starts being run must have its entry deleted, so the file cannot become a list of every
problem the project has ever had, which is a list nobody reads.

Discovery is **asked of `unittest`, not re-derived**. A guardrail that reimplemented the
discovery rule could drift from the suite and would then be certifying a suite that does not
run.

### Invocation is declared and verified, never inferred

The first implementation inferred it: an artefact counted as invoked if its path appeared
anywhere under `.github/`, `deploy/` or `bin/`. An adversarial review demonstrated that a
comment, a `bash -n` syntax lint, a pull-request-template checkbox, an issue template, a
disabled `.yml.disabled` file and a `.bak` suffix all satisfied it. A gate a PR checklist can
satisfy is the same "prose is enough" failure this change exists to close — and worse, the
cheapest way to clear the four debt entries below would have been to add them to a `bash -n`
loop, turning a syntax check into a certification that they run.

So the inference is gone. Discovery is the only thing detected automatically. Everything else
must be declared, and the declaration is checked: the named invoker must be a tracked file under
`.github/workflows/`, `deploy/` or `bin/` — a closed set of places that execute things, which
excludes the templates — and it must mention the artefact or its directory. The directory case
is deliberate: the natural way to run a set of scripts is a glob, and a gate that only accepted
literal paths would push people toward the worse fix.

That is also what "show what invokes it" says. The author shows; the machine checks the showing.

## What it found on its first run

Four scripts, which is to say the guardrail's first act is to make an existing problem
countable rather than to certify that there isn't one:

```
tests/linux/docker-bind-smoke.sh
tests/linux/install-rehearsal.sh
tests/linux/test-beat-producer.sh
tests/linux/toolchain-smoke.sh
```

Nothing runs them. They are now debt owned by #728. This was already known and already written
down — `tests/live/test_pr_head_live.py:17-21` cites this exact directory: *"PR #71's silent
no-op shipped past a test that existed. A non-gated test that nothing schedules is not a safety
net, it is decoration."* A comment naming the problem sat next to the problem while it
persisted. A manifest entry that fails CI when a fifth is added is a weaker statement than the
comment, and a stronger mechanism.

## The second half: D12 was one-directional

`check_d12` verified that every `preflight_checks` entry in `DOD-MANIFEST.json` appears in
`deploy/preflight.sh` — and never the reverse. A new `bad "..."` check could be added to the
script with no catalogue entry, silently, while the DoD verdict stayed PASS. The two matched
today by luck and diligence, not by machinery.

It now checks both directions. Of the script's 80 `bad`/`warn` call sites the reverse direction
covers 76, all of which are catalogued. The four it does not cover have no fixed text to compare
— one is built by command substitution, three re-open a quote mid-message — and keep only the
forward direction's cover. That is a real gap, stated here rather than rounded away: an earlier
draft of this ADR claimed 78 of 80 and was wrong in both numbers, which is exactly the kind of
figure this epic exists to distrust.

The matcher anchors at the start of a statement and accepts single quotes. An earlier version
matched `bad "..."` anywhere in a line, so a commented-out check and the word `bad` inside an
unrelated `echo` both demanded catalogue entries that could not exist, while a check written
with single quotes was invisible to it entirely.

## Options considered

### A. Subcommand invocation census (rejected)
See above. Rejected on its own enumeration.

### B. A PR-template or DoD prose requirement (rejected)
"Show what invokes it" as a checklist item. This epic exists because prose requirements were
not met; adding another is not a change.

### C. Require every artefact to be discovered (rejected)
Simpler, but wrong: `tests/live/test_pr_head_live.py` is deliberately outside discovery and
wired to a workflow instead, and its docstring explains why that is load-bearing. The rule is
"show what invokes it", not "be in the default suite".

### D. Inventory + manifest + fails-on-growth (chosen)

## What this does not do

- **It does not fix #728.** It makes those four visible and owned.
- **It does not check that an invoked test can fail.** Being scheduled is necessary, not
  sufficient — `tests/linux` is the standing reminder, and #727 is a live example of a
  scheduled test that fails for reasons unrelated to what it tests.
- **It does not police non-test checks.** A policy factory nobody registers is the same disease
  — `pr_close_keyword_guard` is one, per #118 — but "what counts as an invoker" is a different
  question there, and answering it badly would produce the same over-broad gate that option A
  was rejected for.

## Acceptance boundary

Both directions on each, against a temporary git repository rather than this one — an audit
that can only be observed passing on the tree it ships with has never been seen to refuse:

- A test-shaped file nothing runs fails; an ordinary discovered test passes untouched.
- A file a workflow runs passes with no entry; the same file named only by `program/**` fails.
- A `debt` entry with no owning issue fails; a stale entry fails; an uncategorised entry fails.
- `check_d12` fails when a real check's catalogue entry is removed — asserted through the
  verdict function itself, because asserting that the two sets match today would still pass
  with the check deleted.
- The pinned counts are 4 total / 4 debt / 0 structural.

## Consequences

Adding a test that nothing runs now requires either wiring it to something, or writing down
that it is not run and who owns that. Both are cheap; neither is silent.

The cost is a file that must be edited when something legitimately unrunnable is added, and the
risk that it becomes a formality — an entry written to get to green. The stale-entry rule and
the owning-issue requirement are what keep it from being free, but they cannot make anyone
read it. If the debt count starts rising with well-formed entries, that is the signal this has
become paperwork rather than a gate.

## One-way door and reversal trigger

Not a one-way door: deleting the manifest and its test restores the previous state, and no
other code depends on either.

Reverse if the guardrail refuses legitimate work more often than it catches an unrun test —
the failure mode option A was rejected for, which this design avoids by scope rather than by
being immune to it.
