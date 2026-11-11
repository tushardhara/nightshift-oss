# Cross-crew review log

Every PR is reviewed by a different crew, round-robin. The retro reads this file to
compute escaped defects — the measure of whether the gate is real or theatre.

| Date | PR | Author crew | Reviewer crew | Vendor | Verdict | Blocking | Loops |
|---|---|---|---|---|---|---|---|

## 2026-08-29 — ADR-0076 through ADR-0082 (staff, ADR review gate)

**Scope of this pass.** Discovery/architecture only; nothing built yet. Reviewed
against the current **working tree** (several of these files are untracked or
modified relative to HEAD, so blob OIDs below are `git hash-object` on working-tree
content, not committed blobs).

**Reviewed head:** `ca52f103e06087dc2d8a62d7198ed0bd12729d6f` (tree
`c33fdc08a007b8569a17f7da940cc1536a9a901e`). Working tree has `.omni/decisions.md`,
`docs/architecture/CAPACITY.md`, `docs/architecture/SCALE-LADDER.md` modified, and
`docs/architecture/OVERVIEW.md`, all seven `docs/decisions/ADR-000{1..7}-*.md`,
and `docs/prd/*` untracked (`git status --porcelain=v1`).

| File | Blob OID (working tree) |
|---|---|
| docs/architecture/OVERVIEW.md | 22d5c6bf99e3a6f8b5024e34b1b9c6baa275500b |
| docs/architecture/SCALE-LADDER.md | d2b2f1cf65eb8d9f6eaee35afc55ff722a35a5cd |
| docs/architecture/CAPACITY.md | 7d28640306d0d75fd75450a33c0abd2f523db278 |
| .omni/decisions.md | 211f3038ea4d9f01eb2293120fb075dd430062bf |
| docs/decisions/ADR-0076-stack-and-shape.md | 4ecf64c26938ce1cab0d10d6b59ae7b64989fca7 |
| docs/decisions/ADR-0077-three-seams-not-five.md | fb03ade955041cf8234bf5005a6d30f44a97d0f1 |
| docs/decisions/ADR-0078-nsc-v1-one-contract-two-adapters.md | 3eff6e81ff042bf27087e18ae69e33d358afa62b |
| docs/decisions/ADR-0079-provenance-pinned-snapshots-closure-derived-from.md | 8b5efe68edeafda068515667000cfb8ae95dfe33 |
| docs/decisions/ADR-0080-tenancy-encryption-limits-of-deletion.md | a781e7898bc1037a83d50dba4dee4e32604b0cea |
| docs/decisions/ADR-0081-publication-gateway-only-repository-write-path.md | 14d744fe40840a1e40a966efe4a18902bc1122cf |
| docs/decisions/ADR-0082-migration-authority-epoch-quiesced-switch.md | 69a351766f6f18ba74c8875ca4c6613f9e4094be |
| docs/prd/DEFINITION-OF-DONE.md | 409a966989ffc2e5b12e5edf826627912ea513d9 |

Dependency seams exercised by this review: all seven ADRs cite
`docs/architecture/OVERVIEW.md` as Spec; OVERVIEW.md in turn embeds the numbers
from CAPACITY.md and the tier/trigger table from SCALE-LADDER.md. I read all
three plus `.omni/decisions.md` (the reconciliation record), `.omni/brief.md`,
and `docs/prd/DEFINITION-OF-DONE.md` in full, not by sampling.

### Verdicts

| ADR | Verdict |
|---|---|
| ADR-0076 (stack and shape) | **consistent** |
| ADR-0077 (three seams not five) | **consistent** |
| ADR-0078 (NSC-v1, one contract two adapters) | **consistent** |
| ADR-0079 (provenance) | **consistent** |
| ADR-0080 (tenancy/encryption/limits of deletion) | **consistent** |
| ADR-0081 (publication gateway) | **consistent** |
| ADR-0082 (migration by authority epoch) | **consistent** |

Template completeness (check 1): all seven have every ADR-TEMPLATE.md section
non-empty — Context, Options (each rejected option states *why* it lost, not just
that it lost), Decision, Consequences, Cost (day-one + 100×), One-way door?, Scale
tier, Reversal trigger, Date/Deciders/Conversation. No placeholder text found. This
is the one item I checked and found clean across all seven — no finding here.

Internal consistency (check 2) and arithmetic (check 3): cost figures
($150/mo→$300 ceiling day-one, $2,000/mo→$3,000 ceiling at 100×, $50–80/$700–950
single-AZ floor), retention defaults (7d/24h/90d/365d/tenant-lifetime), the 12
NSC-v1 invariants, the 25M-vector and seam-4 promotion triggers, and the seam
count (3) all agree byte-for-byte everywhere they recur across the seven ADRs,
OVERVIEW.md and SCALE-LADDER.md. I did not find a contradiction. One arithmetic
finding below (F3) is a citation mismatch, not a contradiction.

Unbudgeted cost (check 4): none found. Every rejected option in ADR-0076
(Redis, dedicated vector DB, Mem0, temporal graph, Git-as-store) is rejected by a
CAPACITY.md number, not by taste, and nothing above T0 is adopted anywhere in the
seven ADRs. SCALE-LADDER.md's "Free now, ruinous later" checklist (UUIDv7 ids,
`tenant_id` on every row, idempotency keys, cursor pagination, versioned APIs) is
fully checked off and is reflected in NSC-v1 invariants 8 and 11
(OVERVIEW.md:~215–216, "Every row carries `tenant_id` even locally").

### Findings (all FOLLOW_UP — nothing here meets the MERGE_BLOCKER bar; nothing
is built, so there is no reachable causal path against a running system yet)

**F1 — Two reversal triggers cite a signal the design does not yet collect.**
ADR-0076:153 ("more than 5% of 10,000 sampled real commands require an
escape-hatch transaction across ports") and ADR-0078:106 (identical 10,000-command
/ escape-hatch language, different remedy — version NSC-v1 rather than revisit the
seam shape) both depend on a count of "escape-hatch transactions." OVERVIEW.md's
own list of what operators monitor (OVERVIEW.md:454–458: authority latency, lease
age, outbox depth/oldest age, failed jobs, finalization errors, search freshness,
retained bytes, cost by tenant) has no such event or counter. As written, neither
trigger can fire because nothing emits the signal it fires on. *Recommendation:*
add "escape-hatch / cross-port transaction attempt" as a named audit event before
the NSC-v1 conformance suite (DoD condition 16) is built — it is a natural
by-product of that suite, not a new subsystem.

**F2 — ADR-0081's reversal trigger references a metric absent from the same
monitored-metrics list.** ADR-0081:97 and SCALE-LADDER.md:19 both use "publish
queue p95 exceeds 10 minutes for 7 days," but OVERVIEW.md:454–458's monitored list
(cited above) does not include publish-queue latency. Same class of gap as F1,
smaller: this one is a single scalar, cheap to add.

**F3 — ADR-0076's live-vector estimate cites the wrong retention window.**
ADR-0076:61–62 rejects a dedicated vector DB partly on "roughly 414,000 live
90-day chunks at day-one load," implicitly treating all ~4,600 chunks/day
(CAPACITY.md:148) as 90-day-retained. But OVERVIEW.md:299 states indexed material
is "reports, decisions, handoffs, evidence captions and transcript summaries," and
OVERVIEW.md:365–366's own retention table gives reports/handoffs/evidence a
**365-day** retention, not 90 (90 days is the *raw transcript* retention,
OVERVIEW.md:365). Recomputed with the correct blended retention, day-one live
chunks are closer to 1–1.7M, not 414K. This does not change the T0 decision — even
1.7M is two orders of magnitude under SCALE-LADDER.md:14's 25M trigger — but the
number as written cannot be reproduced by a reader working from the cited
retention policy, which is exactly what DoD condition 14(e) asks a capacity number
to survive. *Recommendation:* fix the arithmetic note in ADR-0076 Option D, or
retire the specific figure and just cite the trigger threshold's headroom.

**F4 — ADR-0076's Redis-rejection trigger is a hand-blended composite of two
SCALE-LADDER rows.** ADR-0076:56–58 ("outbox depth exceeds 5,000 jobs for 10
minutes or transactional CPU exceeds 70% for 30 minutes while control p95 is above
250 ms after tuning") merges the Background-indexing row's job-depth number with
the Cache-and-lease-traffic row's CPU/p95 numbers into one condition that appears
verbatim in neither SCALE-LADDER.md row. A reader checking "has the Redis trigger
fired?" against SCALE-LADDER.md alone will not find this exact sentence. Low
severity — the intent (either row's own trigger justifies revisiting) is
reasonable — but it should say "when either the cache/lease-traffic row's trigger
or the background-indexing row's trigger fires," not synthesize a third condition.

**F5 — ADR-0079's cross-tenant reachability trigger has no named test, unlike its
deletion-side counterpart.** ADR-0079:111 lists "any unauthorized cross-tenant
object reachability" as a stop condition for the retained-Git approach, but there
is no described canary or conformance case for *source-mirror/workspace*
cross-tenant reachability anywhere in OVERVIEW.md or the ADRs — only the deletion
path (ADR-0080, OVERVIEW.md "per-adapter canary enumeration") has one. Minor;
worth folding into the same NSC-v1 tenant-isolation conformance cases
(ADR-0078's Decision section already lists "tenant-isolation checks" as part of
the suite) rather than treating it as a separate mechanism.

**F6 — DoD alignment (check 6): OVERVIEW.md + the seven ADRs do not yet let a
stranger settle every condition.** `docs/prd/DEFINITION-OF-DONE.md`:69–89 names 17
files (R5–R21) that settle conditions 2–23; only R6 (CAPACITY.md) and R7
(SCALE-LADDER.md) exist as named. The rest of that substance either doesn't exist
yet or is only partially present inside OVERVIEW.md/the ADRs. Concretely, working
from OVERVIEW.md + ADR-0076..0007 alone, a stranger **cannot** currently settle:
- **Condition 2** — OVERVIEW.md:176's "Entity family" table has exactly two
  columns (`Entity family`, `Essential fields and invariant`); condition
  2(a)–2(e) require Tier, Sensitivity and five-failure-event behavior columns
  that are not present anywhere.
- **Condition 8** — the ten contract dimensions are substantively covered but
  scattered across OVERVIEW.md's sections, not assembled as one numbered
  `## Contract` list with a `## Permitted divergences` section (DoD:250–269).
- **Condition 9(d)** — the four required concurrency scenarios are illustrated
  narratively (OVERVIEW.md's "Transaction rule" worked example) but not laid out
  as the four discrete scenario rows the condition requires.
- **Condition 11(d)** — no enumerated ten-event audit catalogue exists; OVERVIEW's
  audit description (line 455) is general, not the closed list DoD:306–329 names.
- **Condition 13** — ADR-0076 covers all nine named candidates (SQLite, Postgres,
  object storage, Git, Redis, pgvector, dedicated vector DB, Mem0, temporal graph)
  with a reasoned verdict for each, but as options prose, not the literal
  role×criterion matrix condition 13(b) requires.
- **Conditions 18, 19, 20, 21, 22** — no state-machine/invariant catalogue,
  backup-coverage/restore table, actor×operation permission matrix, failure-signal
  detection/repair table, or format-versioning policy exists yet in any reviewed
  file.
- **Condition 23(e)** — no discrete operator-questions table (the brief's four
  questions × overlapping-run/crash/migration/removal) exists; the answers are
  implied piecemeal across OVERVIEW.md and `.omni/decisions.md`'s C-rows.
- **Conditions 16 and 17 are the sharpest, because they are structural
  prerequisites rather than missing prose:** `docs/tests/cases.md` (DoD:89,417)
  and `docs/decisions/REGISTER.md` (DoD:88,173) **do not exist in this repo at
  all**. ADR-0078's Decision section says NSC-v1 "is the executable conformance
  suite, not the prose" — until docs/tests/cases.md exists, that claim is
  asserted, not yet checkable. And condition 17's register is the mechanical
  check that "no two accepted ADRs decide the same register ID differently" — I
  did that check by hand this pass (see below); it should not depend on staff
  re-deriving it from `.omni/decisions.md` every time.

This is **expected sequencing**, not a defect in the seven ADRs themselves — the
brief scoped this pass to architecture and planning, and R8–R19's authors have not
been chartered yet. I'm flagging it here because check 6 asked directly whether a
stranger could settle the DoD from what exists today, and the honest answer is:
not yet, and conditions 16/17 should be the next two files written, since one of
them (16) is what would let a future crew *prove* NSC-v1 rather than assert it.

### Check 7 — ADR-0077, three seams vs five

Both positions are recorded **verbatim**, quoted from each blind draft
(ADR-0077:15–29 for Claude's five-seam position and its seam-5-wraps-1-and-2
argument; ADR-0077:31–43 for Codex's three-seam position and cross-cutting-concern
argument). I wrote the losing side and would recognize my own position in it — the
"operator UI/CLI is a consumer, not a sixth seam" line and the seam-5-wraps-1-and-2
description are both mine, quoted intact, not paraphrased into a strawman.

The promotion trigger — "two crews blocked for more than one delivery wave on
source-mirror or workspace paths" (ADR-0077:123, identical in ADR-0076:154) — is
*not* a system metric like the other SCALE-LADDER rows (no p95, no GB, no
requests/sec); it is a program/process observation. That's appropriate given what
it's actually measuring (crew scheduling, not runtime load), and this program
already has the mechanism to notice it: `.omni/blocked.md` is exactly where
`program` would record a crew stuck on someone else's paths. It's currently empty
because no crews have been dispatched yet in this discovery-only run, not because
the mechanism is missing. I'd call this trigger *noticeable in principle* (unlike
F1/F2, which cite signals with no home at all) but note that nothing currently
defines "delivery wave" as a unit `program` logs — worth a one-line convention in
`.omni/working-agreements.md` before the first two crews are chartered, so the
trigger doesn't depend on someone's memory of what a "wave" was.

### Losing-position fact-check (per the disclosure)

I checked D1 (seam count), D2 (sequence gaps), D3 (vector trigger), D4 (budget),
D5 (zero-file definition), D7 (provenance closure), D11 (retention), and D12
(migration shape) — the eight calls PROPOSAL-claude.md lost — against what the
seven ADRs now say, specifically looking for a fact my draft had right that the
adopted text gets wrong. **I did not find one.** Two are worth naming explicitly
because the reconciliation record makes the reasoning checkable:
- **D4 (budget):** my $50–80/$700–950 figures were not discarded — SCALE-LADDER.md
  ("Current tier and cost") and ADR-0076's Cost section both retain them verbatim
  as the honest single-AZ/no-standby floor, correctly relabeled rather than
  presented as the production estimate. That's the right treatment of a real
  number that was scoped wrong, not an error in the winning side.
- **D5 (zero-file definition):** my draft used `git worktree` against the customer
  checkout, which the reconciliation correctly points out writes metadata under
  the customer's `.git` (`.git/worktrees/<name>`) regardless of where the working
  copy lives — i.e., my draft had the factual error here, and Codex's stricter
  definition (ADR-0079's synthetic-commit-in-an-external-workspace approach,
  OVERVIEW.md's zero-file-modes section) is the one that's actually zero-file.
  Correctly resolved against me.

No case where the adopted (Codex) position states something the numbers or the
codebase contradict while my rejected position had it right. I looked for this
specifically because the prompt is correct that this is the one place my having
written the losing draft is signal rather than bias, and I'd rather report a clean
result than manufacture a finding.

### Tech debt opened this pass

See `.omni/tech-debt.md` — three entries (TD-0001..TD-0003) for F1/F2 (combined),
F3, and the conditions-16/17 file gap.

### Largest technical risk in the merged design

Every one of the seven ADRs assumes NSC-v1's SQLite/PostgreSQL equivalence
already holds — ADR-0076 through ADR-0082 all cite the same conformance suite as
the mechanism that keeps two adapters behaviorally identical, and ADR-0078 names
the exact failure mode: "SQLite/PostgreSQL isolation and uniqueness semantics...
single-process locks creeping into local code." That is the one class of bug that
survives code review and unit tests and only shows up as a stale-fence acceptance
under real multi-process contention — i.e., a double-claimed task or a
double-published artifact, which is precisely the class of bug the brief's
"exactly one durable authority" promise exists to prevent. SCALE-LADDER.md's own
reversal trigger for the local coordinator sets the bar at "1 stale-fence
acceptance in 10,000 runs" — a bar nothing currently measures against, because
`docs/tests/cases.md` (condition 16, F6 above) does not exist yet. The risk is not
that the design is wrong; it's that seven ADRs and a 12-invariant contract are
resting on a conformance suite that is asserted, not yet built, and every one of
this box's local capabilities (Docker, Postgres, Node/Python, no cloud credentials
needed) says that suite is buildable today. I'd build it before any of the three
seams' feature work starts.
