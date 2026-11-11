---
name: design-spec
description: Produce a design spec for one slice — flow inventory, full state matrix, exact copy, accessibility, edge behaviour — grounded in the design system so N crews ship one product, not N.
---

# design-spec — every state, the real strings, one language

The output is `docs/design/<slug>.md`. An engineer must be able to build from it
without inventing a single behaviour or string; anything they would have to invent
is a hole in the spec, and holes ship as bugs.

## Procedure

1. **Ground in what exists.** Read the current screens, components, and patterns
   in the repo (cite `file:line`) and `docs/design/SYSTEM.md`. Reuse before
   inventing: a new pattern is a cost paid by every future screen, so name why
   the existing one fails before adding one.

2. **Flow inventory.** The user's goal in their words, where they come from, and
   the flow step by step — numbered walkthrough or mermaid. Prefer removing a
   step over adding an explanation.

3. **State matrix — the load-bearing part.** For EVERY surface in the flow, a row
   per state: empty, loading, partial, success, error, permission-denied,
   offline, too-much-data. Each cell says what the user sees and can do. Naming
   only the happy path is the most common way a spec ships a broken product.

4. **Exact copy.** Labels, button text, error messages, confirmations — the real
   strings, in the product's voice. Mark any string that must be localised or
   that embeds a variable, with its worst-case length.

5. **Accessibility.** Keyboard path and focus order per surface, what a screen
   reader announces at each state change, contrast requirements, and what must
   not rely on colour alone. Mobile: touch target sizes and what happens at the
   largest system font size.

6. **Edge behaviour.** 0 items, 1 item, 10,000 items; the longest plausible
   string; a slow network; a double click; an interrupted flow resumed later.

7. **Update SYSTEM.md.** Any new token, component, or pattern this spec
   introduces gets recorded there in the same change — the spec cites it, the
   system file defines it. A pattern that lives only in one spec will be
   reinvented differently by the next crew.

8. **Close with the negative space.** What you deliberately did NOT design, and
   why. When the flow forces a genuine product tradeoff (fewer steps vs more
   control, speed vs confirmation), state both options with consequences and
   hand it to the crew's `pm` via the program rather than deciding silently.

## Notes

- Write for a builder who has never seen your other specs. Terms come from
  SYSTEM.md, not from memory.
- Specs are single-slice; the system file is cross-slice. Do not duplicate
  SYSTEM.md content into specs — cite it.
