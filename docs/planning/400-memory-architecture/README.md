# Nightshift memory architecture planning package

This folder contains the complete local planning result for moving Nightshift's
operational state and memory out of customer repositories.

It is planning evidence only. It does not authorize implementation and created
no GitHub tickets, pull requests, branches, commits, or external changes.

## Start here

1. [`prd/PROJECT.md`](prd/PROJECT.md) — final PRD
2. [`prd/DEFINITION-OF-DONE.md`](prd/DEFINITION-OF-DONE.md) — 23 verifiable completion conditions
3. [`prd/MILESTONES.md`](prd/MILESTONES.md) — dependency-ordered delivery plan
4. [`architecture/OVERVIEW.md`](architecture/OVERVIEW.md) — merged architecture of record
5. [`evidence/STAFF-REVIEW.md`](evidence/STAFF-REVIEW.md) — independent Claude Staff review

## How the result was produced

```text
Product (Claude/Fable) → Codex critique → Product response
                                      ↓
 Claude blind proposal → Program reconciliation ← Codex blind proposal
                                      ↓
                              Codex merged design
                                      ↓
                               Claude Staff review
```

## Folder map

- `prd/` — PRD, DoD, milestones, Codex critique, and Product reconciliation.
- `architecture/` — both blind proposals, the merged design, capacity model,
  scale ladder, and system summary.
- `decisions/` — seven accepted architecture decision records.
- `evidence/` — Program reconciliation, Staff review, remaining debt, and final
  discovery status.

The original temporary discovery package was preserved as rollback evidence at
`/private/tmp/nightshift-full-discovery-20260829.2HclZ1/`.
