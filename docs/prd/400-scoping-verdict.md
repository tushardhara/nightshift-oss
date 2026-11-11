# Scoping verdict — Issue #400 ("Epic: keep customer repositories clean")

**Verdict: (b) — a separate follow-on project, chartered only after the current
24 DoD-v2 conditions close.** Nothing in `docs/prd/DEFINITION-OF-DONE.md` changes
today; no condition is extended, and none is added.

---

## Why this is (b), not (a)

**1. #400 is not a variant of this project's subject — it's a different product
surface.** This project (`docs/prd/PROJECT.md`) is *Nightshift pointed at itself*:
fix four confirmed install/liveness bugs on one Hostinger VPS, then audit the
machinery that keeps a multi-day unattended run from stopping silently, corrupting
its own state, or duplicating work. The 24 DoD-v2 conditions are entirely about
*this box's* install determinism, liveness truth, concurrency enforcement, crash
survival, and pipeline enforcement. Issue #400 is about how Nightshift stores its
own state **while operating on an arbitrary customer's repository** — control
state, narrative memory, and a reviewed-publication gate that governs every future
customer engagement. That's not a missing sub-condition of this project; it's the
next product's foundation.

**2. #400's own acceptance contract directly contradicts this project's declared
out-of-scope.** From `docs/prd/PROJECT.md` → Scope → Explicitly out of scope:

> Re-architecting the control plane: no Postgres for `control.db`, no long-lived
> daemon, no compiled binary, no new runtime dependency beyond bash + python3
> stdlib. New capability: no new agents, crews, skills, harnesses, providers, or UI.

#400's target architecture requires exactly the things this project ruled out:
PostgreSQL with RLS, pgvector, encrypted object storage (condition set §"Managed
production"), and a new Customer Publication Boundary with one-use reviewer
approval — genuinely new capability, not an audit-and-fix of existing machinery.
Folding it into DoD-v2 would mean amending the DoD to reverse a scope decision
this project already made on paper. That's not what condition growth is for.

**3. No existing condition is a natural extension point.** I checked all 24 against
#400's 23 acceptance conditions and the three boundaries. The closest adjacencies
are:
   - DoD condition 21 (frozen-path / boundary controls) is about *this repo's*
     `/srv/nightshift` and `/home/nightshift/nightshift-runtime` staying untouched
     by this project's changes — a narrower, different boundary than #400's
     customer-checkout-write boundary. Stretching 21 to cover #400 would silently
     change what 21 has meant since v2 shipped.
   - DoD condition 22 (workflow/pipeline enforcement, integrated-fix discipline)
     and #400's acceptance condition 22 ("existing control-plane capacity... remain
     unchanged unless a separately reviewed contract explicitly supersedes them")
     are compatible *neighbors*, not the same condition — #400's own text treats
     this project's control-plane guarantees as an invariant to preserve, not a
     line item to absorb.
   None of the 24 can be extended without either mis-scoping the fix-and-audit
   project this DoD governs, or quietly admitting an L-sized epic through a
   one-line amendment — which is the exact failure mode the DoD's own change-control
   section warns against ("Adding to it late is how a project never ends").

**4. The issue agrees it isn't ready to be scoped as work under any DoD yet.** #400
is explicitly `needs-dor`, `size:L`, with its own decomposition instructions: don't
redo discovery, don't dispatch the epic whole, don't create all child issues, expose
only the first S/M vertical slice after DoR, validate the 23-condition contract
against current `main` first. That is the shape of a new project's Phase-1 intake —
not an addendum to a DoD that is 21/24 conditions from closing.

**5. Numbering collision, flagged so it isn't a source of future confusion.** Both
this project's DoD and #400's acceptance contract independently settled on
"~23–24 numbered conditions." They are unrelated lists governing unrelated
projects. Do not let a future skim conflate "DoD condition 14" here with
"acceptance condition 14" in #400 — I've referred to #400's list as *acceptance
conditions* throughout this verdict for that reason, and the next project's DoD
should keep its own condition numbering visibly distinct from this document's.

## What changes today

- `docs/prd/DEFINITION-OF-DONE.md` — **no change.** Still 24 conditions, still the
  only fixed point for this project.
- `docs/prd/MILESTONES.md` — **no change.** M1–M5 still map 1:1 to the 24
  conditions; #400 belongs to none of them and should not be squeezed in.
- Issue #400 stays open, `needs-dor`, unassigned to any crew. It does not block
  `crew_alpha`/`crew_beta` on #390/#392/#393, and it does not block `release`
  integrating #399. Nothing in-flight changes.

## What should carry into the NEXT project's discovery brief, so it isn't lost

The owner already paid for a full blind-draft-and-reconcile discovery pass on
this (Product/Codex/Staff, evidenced in #400's "Planning provenance" section:
77 Codex findings triaged, DoD grown 17→23, 20 disputed decisions reconciled,
7 ADRs, Staff review with 6 FOLLOW_UP + 3 tech-debt entries). The next project's
`product` should **not** redo that work. Carry forward, verbatim where possible:

1. **The three-boundary architecture as the starting shape**: Control State /
   Narrative Memory / Customer Publication, with memory explicitly advisory and
   mechanically unable to authorize control decisions or publication.
2. **The 23 acceptance conditions in #400's body** as the DoD-v3-of-that-project
   draft input — they read as already close to checkable-statement shape (e.g.
   #1's "no unrequested file/ref/`.git` change on a clean repo," #13's exact
   one-use publication-approval tuple). The next `product` should still run them
   through the same rigor this document's DoD went through (BLOCKED vs MANUAL vs
   PASS, one validator), not assume they're final as written.
3. **The "First Nightshift pickup contract"**: the first slice is DoR and
   decomposition only — inventory every current `.omni`/generated-doc/worktree/
   Git-ref/`.git` write path on current `main`, reconcile against `state_root`,
   project-binding, control DB, backup, worktree, policy, and Program-prompt
   behavior *as they exist after this project's M1–M5 land*, not as they were
   when #400 was drafted. The smallest first slice named in the issue: external
   local-state seam (SQLite + content-addressed filesystem outside the customer
   repo) plus a machine-verifiable zero-file-footprint harness — explicitly no
   production backend, no migration cutover.
4. **The explicit out-of-scope list for that first slice** (production Postgres/
   pgvector rollout, object-store provider selection, publication implementation,
   tenant key rotation, real customer migration, deleting `.omni`/branches/refs/
   databases/worktrees/backups, Redis/Kafka/Mem0/graph/Kubernetes/microservices,
   broad control-plane redesign) — this is a stronger "out" list than most briefs
   arrive with; keep it rather than re-deriving it.
5. **The numeric-trigger discipline** (acceptance condition 19: infrastructure
   tier moves are governed by measured capacity/latency triggers, not anticipated
   scale) and **modular-monolith-at-initial-load default** (condition 20) — this
   mirrors the discipline this project's own DoD uses (day-one capacity, not
   "if this succeeds" numbers) and should anchor the next `docs/architecture/
   CAPACITY.md` the same way.
6. **The known-risks list in the issue** ("Known risks reviewers must attack") as
   the seed for that project's adversarial DoR review — it already names the
   sharpest failure modes (hidden `.omni` authority paths, a "zero-file" claim
   that still writes under `.git`, SQLite/Postgres semantic drift, semantic
   memory reaching a control mutation indirectly, cross-tenant leakage via
   dedup, shadow migration creating two authorities).
7. **The VPS planning package location** (`/home/nightshift/planning/
   issue-400-memory-architecture-plan/`, 23 files, 420 KiB, owned by
   `nightshift:nightshift`, outside `/srv/project`) as read-only input for the
   next project's `product` and `architect` to open first — start with its
   `README.md` — before drafting the next `PROJECT.md`/`DEFINITION-OF-DONE.md`/
   `MILESTONES.md`. Any permanent governing docs still enter this repository
   through normal small reviewed PRs; the planning volume itself does not.
8. **One coordination note for whoever charters that project**: this project's
   own out-of-scope line ("no Postgres for `control.db`... no new runtime
   dependency") and #400's acceptance condition 22 ("existing control-plane
   capacity, holder binding, one-dispatch capability, on-call reserve, and
   fail-closed policy behavior remain unchanged unless a separately reviewed
   contract explicitly supersedes them") are two independent projects agreeing
   on the same boundary from opposite sides. The next project should treat this
   project's control-plane contract (ADR-0002/ADR-0003, `bin/nightshift-ctl`,
   the liveness record) as a dependency to preserve, not a surface it revisits.

## Recommendation

Leave #400 open, `needs-dor`, unassigned. When the current 24 conditions report
`PASS` and this project closes, charter #400 as its own project with its own
`docs/prd/PROJECT.md` / `DEFINITION-OF-DONE.md` / `MILESTONES.md`, seeded from the
local planning package and the carry-forward list above — not from a fresh brief
that discards what's already been reconciled.
