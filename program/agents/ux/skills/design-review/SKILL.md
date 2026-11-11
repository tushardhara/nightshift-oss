---
name: design-review
description: Verify built work against its design spec state by state before integration; report only, never edit.
---

# design-review — the spec is a contract, this is its gate

Security has a gate, staff has a gate; without this one the design spec is
advisory and "one design language" is a hope. The program routes user-facing PRs
here after cross-crew review passes, before it hands them to `release`.

## Input

The diff (as text), the PR body, and the path to the spec (`docs/design/<slug>.md`).
Never the implementer's transcript or worktree. If no spec exists for a
user-facing change, that is a `MERGE_BLOCKER` against the frozen design domain.

## Procedure

1. **Walk the state matrix.** For every surface × state row in the spec, find
   where the diff handles it. A state the spec names and the code does not
   handle is `MERGE_BLOCKER`, with the spec line and changed causal path cited.
2. **Diff the strings.** Compare every user-visible string in the diff against
   the spec's exact copy. Wrong copy outside a criterion is `FOLLOW_UP`; an error
   message the engineer invented (not in the spec at all) is `MERGE_BLOCKER` — either the code or
   the spec must change, decided by the crew's `pm`, never silently by the code.
3. **Walk the keyboard.** Trace the spec's keyboard path and focus order through
   the diff. A trap or an unreachable control is `MERGE_BLOCKER` when the changed
   path causes it; announce-on-change gaps outside criteria are `FOLLOW_UP`.
4. **Check the system.** Components and tokens used should come from
   `docs/design/SYSTEM.md`. A hand-rolled variant of an existing component is
   `FOLLOW_UP` and a drift signal worth naming in the verdict.
5. **Report.** Exactly `MERGE_BLOCKER`, `PRE_DEPLOYMENT`, and `FOLLOW_UP`, each item as:
   spec cite → what the code does instead → `file:line`. Say what you checked
   and what you deliberately did not; "looks good" is not a verdict.

## Rules

- Report; never edit. Fixes go back through the authoring crew's implementer on
  the same PR, like any review finding.
- Judge against the SPEC, not your taste. If the spec itself is wrong, say so as
  a separate finding routed to the crew's `pm` — do not fail the code for
  matching a bad spec.
- A merge blocker cites the frozen criterion or protected invariant, exact changed
  hunk, reachable user path, and a concrete failure scenario or safe deterministic
  reproduction; never execute it against a live system. `PRE_DEPLOYMENT` blocks
  cutover only. Pre-existing or unreachable defects and requests for unrelated
  polish or scope expansion are `FOLLOW_UP`. After round one, new blockers require changed bytes or criteria, or
  a direct dependency the same reviewer demonstrably missed.
- Every pass states its reviewed head SHA/tree, files, blob OIDs, dependency seams,
  and evidence so the review owner can bind it to the consolidated receipt.
