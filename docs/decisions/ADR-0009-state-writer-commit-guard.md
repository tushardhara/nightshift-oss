# ADR-0009: Program state has no shared writer. Every writer gets an independent clone; publish is compare-and-swap; the mandatory boundary is a filesystem/credential rule owned by `platform`+`security`, not a command-text policy

Date: 2026-08-23 (v1) · revised 2026-08-23 (v2) · revised 2026-08-23 (v3) · **corrected 2026-08-23 (v4, this text — two record-level overclaims fixed per `architect`'s v3 re-check; no structural change)** · Status: **proposed** — awaiting `staff`/`architect` re-review of this correction, and still awaiting the mandated two-vendor design debate (issue #13 unresolved)
Deciders: crew_alpha lead · Proposers: crew_alpha lead (single-author throughout v1–v4; v4 corrects two overclaims `architect`'s v3 re-check identified — audit-log claimed more than a hash can prove; Layer 1's stated "any process" requirement listed Option D as a candidate mechanism when it cannot satisfy that requirement — and adds the pre-implementation checklist as a named section)
Conversation: 163613275ab94c33be6b2d88d4de6ad0 · Spec: none — issue #89 is the spec; PR #99 carries the full review record this revision answers
Issues: #89 (P0), #97, #98, #100, #101, #13 · Wave: current · Base `a411721ddb292de11c4eaa2eaf7712955706d49b`

## Revision note (read this first)

**v2 adopted R-63 (per-agent worktrees + policy-layer deny on the shared checkout + CAS publish) and
got a second cross-vendor re-check from `architect`.** Verdict: **`needs-a-wider-decision` again.**
Two of v2's own load-bearing substitutions were wrong, not merely incomplete:

1. **The policy-layer deny is not single-event evaluable, contrary to v2's explicit claim.**
   `deploy/policies/omnipods_policies.py` sees `{"type": "tool_call", "data": {"name": ...,
   "arguments": {"command": cmd}}}` for shell tools — **no authoritative cwd or resolved
   repository target.** `git add -- .omni/STATUS.md` run in `/srv/project` and the identical command
   run in a dedicated worktree are indistinguishable to the policy. v2's central architectural move —
   "deny the target, not the verb, because the target is single-event decidable" — does not hold
   against the actual event schema. This is corrected below by **removing the policy-text deny from
   this ADR's mechanism entirely** and naming the real, cross-owned boundary that must replace it.

2. **The #101 mitigation I explicitly flagged as an unverified hypothesis was tested and falsified.**
   I wrote in v2: *"whether a per-agent worktree's own private FETCH_HEAD sidesteps #101 is a real
   hypothesis... unverified... must be verified empirically before any claim that per-agent worktrees
   resolve #101."* `architect` ran the experiment. A **linked** worktree (`git worktree add`) does not
   own its git-directory — `FETCH_HEAD` for a linked worktree lives at
   `/srv/project/.git/worktrees/<name>/FETCH_HEAD`, which is still physically inside the read-only
   common `.git`. `git fetch` there fails identically to `/srv/project` itself. **A fully independent
   clone under a writable root succeeded.** The distinction that matters is "linked worktree vs.
   independent repository," not "shared root vs. dedicated directory," which is a different axis than
   R-63 assumed. **R-63 directive #1 is corrected here from "per-agent worktrees" to "per-agent
   independent clones."** The instinct to flag the hypothesis as unverified rather than assert it was
   right; the hypothesis itself was wrong. Named plainly, per the same discipline that caught it.

v3 corrects both. **Still single-author** — issue #13 is P0 and still open, so the mandated two-vendor
debate still has not happened for this design at any revision. Reversal trigger #1 is unchanged: this
remains provisional pending that debate.

## Context

Unchanged from v2: three occurrences of the same mechanism in one day (11:00Z `39797dd`, 18:27Z /
R-62's re-arm inside the Program's own dedicated worktree, ~18:30Z this session's own accidental
repro), all involving a checkout that was shared, or dedicated-but-unsynced, across actors or turns.
R-33's "more assertions" rejection and its own re-arming at R-62 remain the central evidence that
assertion-based guards on a driftable checkout do not close this class.

New in v3's Context: `architect`'s v2 re-check adds two facts that constrain the *mechanism*, not
just the *target*:

- **This repo's policy-evaluation layer cannot see an operation's resolved cwd or repository
  target** — only a command string. Any design that requires distinguishing "this git command, run
  here" from "this git command, run there" cannot be built as a `deploy/policies/omnipods_policies.py`
  rule, regardless of how the rule is worded.
- **A linked worktree's private per-worktree git-dir (`FETCH_HEAD`, `HEAD`, `index`) still lives
  inside the common repository's `.git`.** Only a repository with its own independent `.git` —
  a clone, not a worktree — has a `FETCH_HEAD` outside a read-only-to-Codex-seats path. This was
  confirmed by direct experiment in this Codex sandbox: fetch fails in `/srv/project`, fails in a
  linked worktree under a writable root, and **succeeds in an independent clone under a writable
  root.**

## Response to `architect`'s v2 re-check, disposition of C1–C5 and the two new v2 issues

**C1 (check/use race / push atomicity) — CLOSED, unchanged from v2.** Compare-and-swap publish on
git's native non-fast-forward rejection stands. `architect` confirms this closes cleanly for the
wrapper's own transaction; the one open thread (failed-CAS recovery) is a *new* issue, addressed
below, not a reopening of C1 itself.

**C2 (declared-path content freshness) — PARTIALLY CLOSED, unchanged disposition, sharpened
characterization adopted, audit claim corrected.** `architect`'s v2 re-check gives this residual a
specific shape I did not have in v2: **"low-frequency after isolation, high-impact when triggered,
not hypothetical"** — because long-lived Program writers naturally declare the same hot paths
(`.omni/decisions.md`, `STATUS.md`, `ledger.md`) repeatedly, and another writer can legitimately
advance those same paths between this writer's syncs. I adopt that characterization verbatim over my
own v2 hedge ("might be rare, might recur"). **Correction (this revision): v3's original text
claimed the before/after content-hash log would make a stale-content write "detectable... from the
log alone." `architect`'s re-check is right that this overclaims what a hash can prove.** A hash
transition `A → B` is identical whether `B` is correct new content or accidentally-reintroduced stale
content — no git hash encodes intent or provenance, which is exactly the external-source-of-truth gap
this ADR's own residual section already names. **Corrected claim: the audit log is forensic
provenance, not detection.** It does not identify a stale write by itself; it makes one
**reconstructible** after the fact — given an independent reason to suspect a specific invocation
(a manual review, a downstream symptom, another system's complaint), the log lets someone determine
what changed, when, and against what prior hash, which v1's incident took six hours to work out by
hand. It does not, and cannot, flag a stale write on its own. Reversal trigger #3 is retimed
accordingly: it fires on the first **audit-assisted manual review or investigation** that identifies
a suspected stale write, not on "the log detects it" — because the log cannot do that step itself.

**Audit sink, corrected.** v3's original text proposed logging to `.omni/audit/state-commit-log` as a
plain repository file. `architect` is right that this is unspecified to the point of being unsound:
that path is itself a changed repository path, so either it's implicitly folded into the commit (and
now the changed-file set silently exceeds the caller's exact `--write` manifest — a contradiction
with step 9's own exact-manifest check) or it's left dirty outside the declared scope (and blocks the
very refusal check in step 6). "Append-only" is not a property an ordinary git-tracked file has under
concurrent writers, and a rejected publish would leave a written pre-record with no corresponding
committed after-record, an inconsistency nothing in v3 resolved. **Corrected: the audit sink is an
external, non-recursive, concurrency-safe, genuinely append-only store — not a path inside the
repository `state-commit` itself writes to** (a local append-only log file outside any git working
tree, or a row in the same control-plane database `nightshift-ctl` already uses for slots/claims/
leases, are both plausible; the exact choice is implementation-gating, not decided here). Records are
written for success, refusal, and publish-race outcomes, keyed by invocation, candidate ref, and the
expected/actual remote OIDs, so a rejected publish leaves a coherent, individually-inspectable record
rather than an orphaned half-write.

**C3 (raw-path policy deny) — STILL OPEN in v2; corrected mechanism in v3, see Decision.** Accepted
in full: the deny cannot be built as a command-text policy rule at this repo's actual event altitude.
Removed from this ADR's mechanism (Layer 1 revised below), replaced with a named, cross-owned
requirement.

**C4 (`git reset` destructive) — CLOSED, unchanged from v2.** No new finding; stays removed.

**C5 (R-33 adoption, worktree premise) — PARTIALLY CLOSED in v2; the "R-33 adopted" half stands, the
"worktree premise" half was wrong and is corrected in v3.** `architect` is explicit that v2
*correctly* adopts R-33's underlying rule (no shared writer) — the error was in the *mechanism*
chosen to realize it (linked worktrees), not the rule itself. §Decision now specifies independent
clones, which `architect`'s own experiment confirms actually restores fetch for a sandboxed Codex
seat. I assess C5 as **closed** in v3: the rule is adopted, and the mechanism realizing it is now the
one `architect` itself tested successfully.

**New issue: failed CAS publication leaves the local branch divergent, so v2's "restart from step 1"
was not actually available.** Accepted. §Decision now builds the candidate commit on an **ephemeral,
disposable ref**, never the caller's long-lived tracking branch directly. On a rejected push: the
ephemeral ref is archived (not deleted outright — kept for forensic inspection) or discarded per
operator policy, the long-lived branch is left untouched at its pre-attempt state, and "restart"
means re-fetch, re-sync a **fresh** ephemeral ref from the new tip, and **recompute** the intended
write content against that new base — never replay the old candidate's bytes blindly, since the
correctness of those bytes was exactly what the race made uncertain (this ties directly to the C2
residual: the tool refuses and hands back the new tip; deciding what to write next is an
application-level decision, not the tool's).

**New issue: `--write` needs a literal-path contract, not just "not a subset."** Accepted.
`--write` arguments must be normalized, repo-relative, literal file paths: no pathspec magic
(`:`, `*`, `?`, `[`, leading `:(...)` prefixes), no directories, no duplicates, no `..` escapes
outside the repo root. The tool rejects a `--write` argument that isn't a literal path before doing
anything else, and compares the *canonicalized* declared set against `git diff --cached --name-only`
for the exact-manifest check (v2's step 7), closing the expansion ambiguity `architect` flagged.

**New issue: sync-vs-dirty-state ordering needs its own named refusal.** Accepted. See §Decision —
this is now refusal reason **(c)**, distinct from fetch failure (a), branch divergence (b), and
dirty-outside-declared-scope (d), each independently tested per `architect`'s requirement.

## Response to the scoping question: was deferring credential/OS separation to "a future layer" wrong?

**Yes, and I am reversing that call.** `architect`'s v2 re-check says so directly: *"Because v2
explicitly says the wrapper is only ergonomics and 'nothing depends on it being called,' credential/OS
separation cannot remain merely a future suggestion after Layer 1 fails. A mandatory boundary is now
part of the minimum decision, even if `platform` owns its implementation."* I agree, for the reason
v2 itself already stated and then didn't follow through on: if the wrapper is explicitly not the
safety boundary (R-63 directive 2, which this ADR keeps), **something else has to be**, and v2 left
that something else as an unbuilt, undecided "Option E, maybe later." That is not a deferral of an
implementation detail; it is a design with no actual safety boundary, dressed as one. §Decision below
makes the mandatory boundary a **required part of this ADR's Decision**: what it must guarantee, and
which owner must ratify and build it — not what its exact mechanism is, since that is legitimately
`platform`/`security`'s call, not a single crew's.

## Options considered

### A — v1: shared-checkout wrapper, assert-then-commit (rejected; superseded by R-63)

Kept as historical record per ADR-0007's correction convention. Unchanged from v2's account.

### B — v2: per-agent linked worktrees + command-text policy deny + CAS publish (rejected;
superseded by this revision)

Kept as historical record. Correctly adopted CAS publish (C1, kept in v3) and removed `git reset`
(C4, kept in v3). **Wrong on two load-bearing points**, both corrected in v3: linked worktrees do not
restore fetch for sandboxed seats (their private git-dir is still inside the read-only common `.git`),
and a command-text policy cannot see cwd/target, so it cannot enforce "deny writes to the shared
root" regardless of how the rule is phrased.

### C — Git hooks, CI-side reactive checks, warn-not-refuse (rejected, unchanged from v1/v2)

No new evidence changes these. Still rejected for the same reasons.

### D — Trusted cwd/target field added to the tool-call event schema, so a command-text policy
*could* see the operation's real target (considered, explicitly NOT sufficient to satisfy Layer 1
alone — defense in depth only)

**Correction (this revision):** v3's first draft left Option D positioned as a possible *satisfier*
of Layer 1's "every write-capable identity, any process running as `nightshift`" requirement.
`architect`'s re-check is right that it cannot be: a trusted-cwd field, however accurate, constrains
**tool-mediated calls that go through the policy-evaluated dispatch path** — it says nothing about an
arbitrary child process, a script invoked outside that path, a direct syscall, an uninstrumented
tool, or any process simply running under the same OS identity without going through policy
evaluation at all. A tool policy constrains tools, not processes. **Option D is retained here only as
defense-in-depth on top of whatever Layer 1 mechanism `platform`/`security` select — never as a
candidate for satisfying Layer 1's structural requirement by itself.** Changing what fields a
tool-call event carries is also, separately, infrastructure outside any single crew's authority to
decide or build (a change to the sandbox/tool-dispatch layer, not to `deploy/policies/
omnipods_policies.py` or `bin/nightshift-ctl`, both crew_alpha-owned).

### E — Server-side content-invariant checks / branch protection as the *sole* boundary (rejected as
sole mechanism, unchanged framing from v2, now explicitly folded into the mandatory requirement
rather than left as "maybe later" — see Decision)

### F — Independent clones (not linked worktrees) as the writer topology, plus a filesystem/credential-level
mandatory deny on `/srv/project` as a **required, cross-owned** boundary, plus CAS publish on an
ephemeral candidate ref (ADOPTED — detailed in Decision)

## Decision

**Layer 1 — a mandatory boundary that this ADR specifies but does not itself build, and requires
routing to `platform` + `security` for ratification before this design is treated as closing C3.**
`deploy/policies/omnipods_policies.py`'s command-text evaluation is **explicitly abandoned** as the
mechanism for denying writes to the shared checkout — it cannot see cwd or resolved repository
target, so no wording of that rule is sound (per `architect`'s v2 re-check, confirmed against the
actual event schema at `deploy/policies/omnipods_policies.py`). The requirement this ADR states
instead: **every write-capable identity (every seat, sandboxed or not, and any process running as
the `nightshift` OS user) must be structurally unable to write to `/srv/project`'s working tree or
`.git`**, by a mechanism `platform`+`security` select and ratify — filesystem permissions scoped to
a designated writer identity, or a sandboxed-only bind-mount policy extended to unsandboxed seats too
(today it only constrains Codex seats — this session's own Claude-harness environment could write to
`/srv/project` freely throughout this task, which is itself evidence the boundary does not exist
uniformly today). **Correction (this revision): Option D (a trusted-cwd/target field added to the
tool-call event schema) does NOT satisfy this requirement and is not offered as a candidate for
satisfying it.** A tool-call policy, however accurate its cwd information, constrains only calls that
go through the policy-evaluated dispatch path — it cannot constrain an arbitrary child process, a
script invoked outside that path, a direct syscall, an uninstrumented tool, or any process simply
running under the same OS identity without ever hitting policy evaluation. Satisfying "every
write-capable identity, any process" needs an OS/filesystem/sandbox/credential-level boundary; Option
D may still be added on top as defense in depth (see Options, below), never as the thing that closes
Layer 1 by itself. **This ADR does not pick the mechanism; it makes "a real all-process mechanism must
exist and be ratified by its owners" a blocking precondition of calling this design built**, per the
corrected scoping answer above. Whatever mechanism is chosen also needs a named privileged maintenance
identity for the otherwise-read-only shared repository itself — something still has to be able to
fetch/update `/srv/project` deliberately (to keep it current as the object-store donor for Layer 2's
`--reference` clones, if nothing else) — and must leave ordinary reads (`git show`, `gh`, `./bin/
nightshift-ctl`) working and independently testable. Until Layer 1 lands, Layer 2 below is real but
not the *only* thing standing between an operator and a repeat of #89 — a caller can still bypass
`state-commit` and write raw `git` in `/srv/project` directly, exactly as v1/v2's own framing already
admitted ("nothing may depend on the wrapper being called").

**Layer 2 — writer topology: independent clones, not linked worktrees. Cost stated honestly.**
Each agent/task gets its own independently-cloned repository (`git clone --reference /srv/project
<remote-url> <writable-root>/<owner>-<task>` — `--reference` without `--dissociate` borrows
`/srv/project`'s object store for disk efficiency via a read-only reference, which Codex seats
already have read access to. **Verified (this revision): `architect` ran this exact command shape**
— `git clone --reference /srv/project <origin-url> <writable-temp>/clone`, then `fetch origin
<branch>` inside it — **and it succeeded**: the clone's alternate was `/srv/project/.git/objects`,
its own `.git/FETCH_HEAD` was writable, and fetch resolved the exact v3 head, no bwrap/AppArmor
denial. This confirms fetch and disk-sharing semantics for that Codex seat; it does not confirm push
authorization or every seat type — see the (narrowed) unverified list below, and note the new
dependency this creates: a non-dissociated reference clone depends on `/srv/project`'s object store
remaining present and readable, which is exactly why Layer 1's chosen mechanism needs a named
privileged maintenance identity for that repository, not a blanket "nobody can touch it.") placed
under a writable root already confirmed available to Codex seats (`/home/nightshift/...`, not `/tmp`,
for persistence across a session — `architect`'s own probe listed `/home/nightshift` and `/tmp` as
writable; `/home/nightshift` is chosen for longevity). **Cost, stated plainly, not waved through:**
- **Disk.** Even with `--reference`, each clone carries its own working tree, index, and
  remote-tracking refs — N clones is meaningfully more disk than N linked worktrees sharing one
  object store fully. `--reference` mitigates the object-store duplication specifically, not the
  rest.
- **Credentials.** Each clone needs its own `origin` remote and working `gh`/git credentials. This is
  likely **not a new cost** in practice — every seat probed so far already carries its own
  authenticated `gh` (architect's own execution evidence: "authenticated `gh` 2.45.0") — but it has
  not been verified across every seat type, and should be, before assuming it's free.
- **Existing tooling assumes linked worktrees.** `bin/nightshift-ctl`'s `_git()` helper, the
  `checkpoint create --ref` convention, and the `.worktrees/<name>` directory convention already in
  wide use (dozens of entries under `/srv/project/.worktrees/` today) all assume `git worktree`
  semantics — shared object store, `git worktree list`/`git worktree remove` lifecycle management.
  Independent clones are invisible to those commands and need their own lifecycle/cleanup convention.
  This is real migration work, not a drop-in rename, and is explicitly **not** scoped to this ADR or
  to crew_alpha — it is part of what routing this to `platform` means.
- **Setup latency.** `git clone`, even with `--reference`, is slower per-task-start than `git worktree
  add`. Not blocking, worth knowing.

**Layer 3 — `nightshift-ctl state-commit`, ergonomics only, revised algorithm (interface unchanged in
name, `--path` renamed `--write` per v2, now with a literal-path contract):**

```
nightshift-ctl state-commit --repo <independent-clone-path> --branch <target-branch> \
    --write <literal-repo-relative-path> [--write <literal-repo-relative-path> ...] \
    --message <text> | --message-file <file>
```

1. Validate every `--write` argument is a normalized, repo-relative, literal path: no pathspec magic,
   no directories, no duplicates, no path escaping the repo root. **Refuse (reason: bad-manifest)**
   otherwise, before touching git at all.
2. `git -C <repo> fetch origin <branch>`. **Refuse loudly (reason: fetch-failed)** on any failure —
   never silent, never "no news." (Answers #101 directly; this refusal is now the caller's signal
   that it may be running somewhere Layer 2's clone topology hasn't actually been provisioned yet.)
3. Capture `origin/<branch>`'s SHA as the expected base.
4. If the local branch cannot fast-forward to that SHA, **refuse loudly (reason: diverged)**, naming
   both SHAs — no merge, rebase, or force.
5. Attempt the fast-forward sync. If it refuses because local modifications on a declared path would
   be overwritten, **refuse loudly (reason: sync-blocked-by-declared-path)** — distinct from (4), and
   distinct from (6) below; this is the case `architect`'s "new v2 issues" asked to be named
   separately, with its own test.
6. If anything is staged or modified **outside** the declared `--write` set at this point, **refuse
   loudly (reason: dirty-outside-scope)**, naming the offending path(s). No `git reset` (C4, kept).
7. For each declared path, log its current content hash to the audit record (new in v3, answers C2's
   sharpened characterization).
8. Stage exactly the declared paths.
9. Verify the resulting staged set equals the declared canonical set **exactly** — refuse (reason:
   manifest-mismatch) on extra, missing, or zero-delta paths.
10. Commit **on a disposable ephemeral ref**, not the caller's tracking branch, under the fail-closed
    seat identity (issue #75).
11. Publish: `git push origin <ephemeral-ref>:<branch>`, ordinary non-force semantics. On success,
    fast-forward the local tracking branch to match and log the resulting content hash per declared
    path to the audit record. **On rejection (reason: publish-raced)**, leave the tracking branch
    untouched, archive the ephemeral ref, and refuse — the caller must re-run from step 2 with
    recomputed content, never retry the same bytes.

**Exit code:** 3 throughout, `{"ok": false, "reason": <one of the named reasons above>, ...}` —
one reason per invocation; the refusals remain sequential and mutually exclusive, per v2's precedent
citation (`checkpoint create`'s dirty-tree refusal).

## Consequences

**Makes easy:** every writer publishing state through a topology `architect` has empirically confirmed
works for the sandboxed seats that needed it, with a corrected, honestly-costed migration path off
linked worktrees, and a mandatory boundary correctly routed to the owners who can actually build it.

**Makes hard:** unchanged from v2 for wide-touching commits (must enumerate literal paths); newly
hard: every existing worktree-based workflow now needs a migration plan `platform` owns, and every
task's setup pays a clone (not worktree-add) cost.

**Not yet built, named as a precondition, not a footnote:** Layer 1's actual filesystem/credential
mechanism. This ADR cannot be treated as closing #89's mechanism class until `platform`+`security`
ratify and ship it — Layer 2/3 alone leave the same bypassable-if-nobody-uses-the-wrapper gap v1/v2
already admitted.

**Named residual, unchanged in substance, sharpened in severity:** declared-path content freshness
(C2) — now explicitly "low-frequency after isolation, high-impact when triggered," with mandatory
audit logging correctly scoped as forensic provenance (reconstruction after an independent reason to
suspect a specific invocation), not automatic detection — see the corrected C2 response above.

**Verified this revision:** `git clone --reference /srv/project <origin-url> <path>` followed by
`fetch origin <branch>` succeeds in this Codex sandbox, with a writable private `FETCH_HEAD` and the
alternate correctly pointed at `/srv/project/.git/objects`. Removed from the unverified list below.

**Still unverified, flagged rather than assumed (carrying forward the discipline that caught the
worktree error, per `architect`'s own final list):**
- **Push credentials and non-force publication** from an independent clone, for every seat type —
  fetch is now verified for the one seat tested; push has not been.
- **Layer 1's actual selected mechanism** — whichever `platform`+`security` choose — including
  whether it can uniformly cover unsandboxed seats/processes (not just Codex sandboxing) while
  leaving legitimate reads (`gh`, `./bin/nightshift-ctl`, `git show`) working and independently
  testable.
- **Persistent disk/capacity and lifecycle behavior** of many independent clones over time — creation
  is verified once; ongoing lifecycle, cleanup, and migration off the existing `.worktrees/<name>`
  convention is not.
- **Long-term safety of non-dissociated `--reference` alternates** under whatever maintenance/GC
  policy `/srv/project` ends up under once it's no longer a general write target — a clone's alternate
  becomes unsafe if the donor repository's objects are ever pruned or the repository is replaced.
- **The audit sink's concrete implementation** — this revision names the *shape* it must have
  (external, non-recursive, concurrency-safe, genuinely append-only, keyed by invocation/candidate/
  remote-OID), not a concrete choice; that choice is implementation-gating.

## One-way door?

No. Everything here is additive/replaceable tooling and process, plus a filesystem/credential rule
that is itself reversible (permissions can be changed back).

## Scale tier

None yet. Reversal trigger #4 (carried from v2) stays a number, not a feeling.

## Reversal trigger

1. **Before any engineer writes code against this ADR**, if issue #13 is resolved, this design gets
   the actual two-vendor debate the charter requires. Unchanged since v1; still true, still not
   satisfied by single-vendor-per-round cross-*checks* however many rounds they run.
2. If Layer 1's filesystem/credential mechanism cannot be built without breaking legitimate existing
   read access to `/srv/project` documented above, that is grounds to revisit Option D (trusted-cwd
   policy field) as the primary mechanism instead, not to quietly drop Layer 1's requirement.
3. **Corrected wording (this revision):** the audit log cannot detect a stale-content write by
   itself (see the corrected C2 response above) — so this trigger fires on the first
   **audit-assisted manual review or investigation** (a suspected-stale report, a downstream symptom,
   another system flagging a mismatch) that the audit trail then confirms as a stale declared-path
   write, destructive or not. That re-evaluation asks whether a git-mechanical guard is sufficient at
   all for that specific failure mode, rather than waiting for a fourth incident to cause visible
   damage first.
4. If `state-commit`'s manifest/sync/dirty-scope refusals produce **3 or more** false-positive
   refusals against legitimate commits within a single wave, revisit the dirty-check scope
   specifically — fetch-failure and non-fast-forward refusals have no equivalent false-positive risk
   and must not be weakened.
5. If a writer is caught bypassing Layer 1 after it ships, that is evidence the mechanism `platform`
   chose is incomplete, not evidence the elimination strategy is wrong — fix the mechanism.
6. **New in v3:** if `architect`'s (or any reviewer's) next re-check falsifies another of this
   revision's load-bearing claims by direct experiment, treat that as the norm for this ADR's review
   process, not the exception — two of three rounds so far have found a genuinely wrong premise, not
   a missing citation. Do not let a third clean-sounding revision be assumed correct without the same
   experimental scrutiny the first two got.

## Preconditions for implementation (this decision record's own gate)

`architect`'s v3 re-check gave 8 items that must be true before `platform` writes the first
implementation line. Recorded here verbatim in substance so the decision record carries its own gate
rather than leaving it only in a PR comment that outlives this ADR's own review history:

1. **`platform`/`security` ratify an actual all-process filesystem/sandbox/credential boundary** for
   Layer 1 — one that covers every identity/process claimed (not just tool-mediated calls), names the
   privileged maintainer of the otherwise-read-only reference repository, proves legitimate reads
   still work, and denies force/ref-bypass paths at the credential/server boundary.
2. **Resolve the audit contract**: the external, non-recursive, concurrency-safe, genuinely
   append-only sink (not a repository file) is selected and specified, with records defined for
   success, refusal, and publish-race outcomes.
3. **Ratify acceptance criteria and DoR**: real `pm` review, and the mandated two-vendor design debate
   once issue #13 allows it. No implementation while this ADR's own DoR verdict says not ready.
4. **Prove the topology per seat**: independent/reference-clone fetch (verified for one seat this
   revision) AND ordinary non-force push (not yet verified for any seat) for every seat type this org
   runs; credential availability; persistent writable-root capacity; clone lifecycle, cleanup,
   alternates/GC safety, and migration off the existing linked-worktree assumptions.
5. **Specify the transaction precisely**: exact expected remote OID capture, ephemeral candidate
   creation/archive/cleanup, index/worktree state after a rejection, successful tracking-ref update
   ordering, and recomputation (never blind replay) after a race.
6. **Pin literal manifest validation**: canonical repo-relative files only; reject pathspec magic,
   directories, duplicates, symlink/out-of-root escapes, and ambiguous path encoding/normalization.
7. **Write the tests before rollout**: every named refusal reason; same-path/different-path sync
   obstruction; dirty-state preservation; exact-manifest enforcement; concurrent-push race; tracking
   branch/index/worktree left unchanged after rejection; audit-sink failure behavior; the no-force/
   no-replay guarantee; hermetic git fixtures with no nested `bwrap` dependency (#100).
8. **Deploy Layer 1 and the clone topology before treating `state-commit` as protective** — Layer 2/3
   alone remain bypassable (v1/v2/v3 all say this explicitly and it stays true). Verify raw writes
   fail from every claimed identity, and that reads/fetches in independent clones continue to work,
   before any claim that #89's mechanism class is closed.

## Draft acceptance criteria (unratified — `pm` still unreachable, issue #13)

Revised for v3. Supersedes v2's criteria wholesale (clone topology, literal-path contract, ephemeral
candidate ref, and audit logging are all new).

1. Every write-capable identity is structurally unable to write to `/srv/project`'s working tree or
   `.git` — verified by attempting a raw `git add`/`commit`/`checkout -- .` there from every seat type
   this org runs, from both a tool-mediated call AND a raw/uninstrumented process using the same OS
   identity, and observing refusal in both cases (a tool-call policy alone cannot satisfy this
   criterion — see Layer 1's corrected Decision text; this criterion is only met once
   `platform`+`security`'s chosen mechanism is in place).
2. `state-commit` run from an independent clone (not a linked worktree) succeeds at its fetch step
   from every seat type this org runs, including sandboxed Codex seats — verified by direct
   experiment, not assumed from this revision's reasoning.
3. `state-commit` refuses loudly and distinctly for each of: bad-manifest, fetch-failed, diverged,
   sync-blocked-by-declared-path, dirty-outside-scope, manifest-mismatch, and publish-raced — with a
   dedicated test reproducing each, per `architect`'s explicit ask.
4. A rejected publish leaves the caller's tracking branch untouched (verified via `git log`/`git
   status` before and after, not just the tool's exit code) and archives the ephemeral candidate
   rather than discarding it silently.
5. **Corrected (this revision) — dropped the "detectable from the log alone" claim `architect`
   showed is false.** Every `state-commit` invocation's audit record captures a before/after content
   hash per declared path, written to an external, non-recursive, concurrency-safe, append-only sink
   (not a repository file `state-commit` itself commits — see the corrected audit-sink text above),
   keyed by invocation/candidate-ref/remote-OID, for success, refusal, and publish-race outcomes
   alike. Given an independently-sourced reason to suspect a specific invocation, the record is
   sufficient to reconstruct what changed and against what prior hash. The record does **not**, and
   is not claimed to, flag a stale write on its own — no criterion here may assume that ability.
6. The test suite for 2–5 uses local git fixtures (bare repo standing in for origin, real independent
   clones in temp directories) and never depends on `gh`, live GitHub state, or `bwrap`/nested-sandbox
   behavior (#100) — except test 2 above, which is inherently sandboxed-seat-specific and must be
   run and reported as such, not folded into the hermetic suite.
7. Tests never write to `program/discovery-state` or `/srv/project` itself.

DoR verdict: **not ready to build** — blocked on the "Preconditions for implementation" section above
in full. The `--reference` clone flag combination is now verified (this revision); the remaining
blockers are `pm` review, the mandated two-vendor debate, and `platform`+`security` ratifying and
shipping Layer 1's mechanism.
