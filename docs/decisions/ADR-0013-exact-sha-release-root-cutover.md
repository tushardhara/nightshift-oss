# ADR-0013 — Deterministic exact-SHA cutover: one sealed release root, five proofs

- **Date:** 2026-08-24
- **Status:** Accepted
- **Deciders:** `program` (reconciliation), `architect` (Codex, blind proposal + amendment),
  `staff` (Claude, blind proposal). Owner directive of 2026-08-24 04:41Z mandated the two-vendor debate.
- **Conversation:** program heartbeat-0400-ext, run-20260822-selfdogfood, wave 8.
- **Inputs, verbatim and unedited, both authored blind:**
  - `architect` proposal — `#115#issuecomment-5390958334`; amendment — `#115#issuecomment-5390983643`
  - `staff` proposal — `#115#issuecomment-5391032592`
  - Owner operator preflight finding — `#115#issuecomment-5390892615`
- **Amends:** ADR-0010 (Option G, B2, "One-way door?"), ADR-0011 (a) and (c). Both are amended in
  SCOPE and in one factual claim; neither's core choice — clean source, startup registration — is reversed.
- **Sequencing note:** ADR-0015 supersedes D10 narrowly: the horizontal cutover
  now precedes the breaking policy-floor expansion; #129 remains open.

## Context

`merged` does not mean deployed on this box, and a restart is proven not to deploy. The owner's
acceptance milestone is: reviewed Git SHA → registered bundle → served bundle proves the same SHA and
config → a Claude seat executes → a Codex seat executes → a durable run resumes. Two seats on two vendors
designed the path blind, from the same brief, without seeing each other. This ADR reconciles them
decision by decision. Where they disagree I say which I took and why; where only one seat saw something,
I say which one, because that is the evidence of whether the two-vendor rule earns its cost.

**It earned it.** The two proposals agreed on the shape and disagreed on four decisions, and `staff`'s
read-only inspection of the live box produced five findings that no amount of document-reading would
have surfaced. Neither proposal alone would have produced this decision.

## Decisions

### D1 — ONE sealed release root, whole tree, not a bundle directory. (Both, independently.)

`prepare <sha>` materialises one immutable SHA-named release containing the **whole reviewed tree**.
`agent_source`, `policy_source`/`PYTHONPATH`, and the control-plane tool are all DERIVED from it; none is
separately configurable. Both seats reached this independently and both rejected two pinned roots:
`architect` on coherence grounds (two roots can diverge and the protocol then has to solve it),
`staff` on evidence — **`/srv/nightshift` feeds THREE live behaviours**, the policy import path, the
`--agent` target in the installed unit, and `nightshift-ctl` itself, which on `PATH` is a symlink into
that stale dirty checkout. Pinning `program/` and leaving the other two is the owner's 04:53Z finding
one level up.

### D2 — Materialise it as a SEALED CLEAN CHECKOUT, not `git archive`. (Took `architect`. `staff` dissents.)

The one substantive disagreement. `staff` proposes `git archive <sha>` + `chmod -R a-w` at
`/srv/releases/<sha>/`; `architect` proposes a detached clean checkout retaining `.git`, sealed
non-writable, because ADR-0011(a) requires a checkout and **the first cutover must not weaken provenance
while trying to establish it**. I take the checkout, on the reversibility rule: it satisfies ADR-0011
unamended, keeps Git-verifiable identity in situ, and can be swapped for an export later. Adopting the
export first would amend a provenance rule in the same change that is supposed to prove provenance.
**Both seats independently named the same reversal trigger**, which is why I record it rather than bury
it: if maintaining a clean checkout for the whole release root proves operationally untenable, amend
ADR-0011(a) by review to "immutable Git-object materialisation" with a signed SHA/tree/digest manifest,
and `staff`'s export becomes correct. `staff`'s immutability point stands on its own and is adopted:
**the release root is `chmod -R a-w` after validation**, checkout or not.

### D3 — FIVE assertions, not three. (Took `staff`'s count, `architect`'s mechanism for #4.)

`registered` / `served` / `content-at-SHA` / `enforced` / `executed`. Kept as five separate receipt
blocks; an unreadable operand is `undetermined`, **never inherited green** from a sibling.
- `enforced` (from `architect`'s amendment) proves the policy module **that actually made a denial
  decision** — resolved `__file__`, digest, PID and process start time stamped into the audit record of
  a real, harmless, denied canary. Its decisive insight: reading the unit file, `/proc/<pid>/environ`,
  or running a separate interpreter with the same `PYTHONPATH` proves **intent, not import result**,
  because the process may already have imported a shadowing module. This requires a small in-process
  provenance surface in change 2.
- `executed` (from `staff`) is a **release nonce existing at exactly one commit in history**, round-
  tripped out of a **dispatched Claude seat AND a dispatched Codex seat** through the normal session
  path — so it resolves whatever the runtime actually resolves.

### D4 — The likeliest silent failure is the `agent_id` SPLIT, not route misbinding. (Took `staff`, on evidence.)

`architect` named route-bound process identity; `staff` named the `agent_id` split and **proved it has
already happened here**: three rows named `program`
(`dc15106…` v2 — the served one, `32efc133…` v1, `9d693e55…` v1, the last created LATER than the served
one) with three extracted cache directories, against caches keyed on `agent_id` alone (ADR-0011 settled
1–2). Registration mints or targets a different id, writes a perfect row — correct bundle, artifact,
cache, version bump — every digest assertion resolves the NEW row and returns green, and every session
keeps loading the old id from a warm cache no restart invalidates. **The deploy is verified, recorded,
announced, and has not happened.** Duplicate-seat validation in `727acc5`/#128 covers duplicate *seats*,
not duplicate *agent registrations*, so it does not cover this.
Only `executed` (D3) catches it: a nonce through the normal session path. Postflight must say so in
those words — *"registered and served bundles are correct; the executing bundle is not the one you
registered — suspect an `agent_id` split"*. Route misbinding is ranked second and keeps `architect`'s
listener-inode → PID → argv binding. Stale enforcement ranks third and is closed by `enforced`.

### D5 — Neutralise the second supervision path BEFORE cutover. (Only `staff` saw this. Adopted.)

The live server is **pid 3525266, hand-run from tmux with NO `--agent`**, while the installed unit
`~/.config/systemd/user/omnigent-server.service:19-25` carries
`ExecStart=… --agent /srv/nightshift/program` — aimed at the stale dirty checkout — on a user bus that is
**down** (`Failed to connect to bus: No medium found`). A unit that claims the sanctioned mechanism,
points it at unreviewed Git, and cannot currently run. **This is not inert, it is armed:** the moment
that bus comes up or the box reboots into a working user manager, systemd registers the stale tree and
the org records a successful self-healing deploy of the wrong code. A cutover that changes only the tmux
line leaves a higher-authority path aimed at stale Git. Cutover must make the launch contract identical
in both paths, or leave exactly one. **I take "exactly one"** (`staff` PR-F) — identical-in-both is a
consistency claim nobody re-checks, which is the pattern this whole ADR exists to end.

### D6 — Rollback is a RE-REGISTRATION, and its bundle is built BEFORE cutover. (Took `staff`; corrects ADR-0010.)

ADR-0010's "One-way door?" answer — *remove the flag and restart* — **is false**, and `staff` gives the
mechanism: the warm cache keeps serving the new bundle, so removing `--agent` changes nothing. Rollback
must re-register a pre-cutover bundle **built before the live window** from the owner's checksum-backed
tarball at `/var/tmp/nightshift-cutover-20260824T043645Z`. `architect`'s framing is adopted alongside it:
pre-cutover content has no reviewed Git commit, so rollback is an **incident-recovery exception
identified by digest and snapshot identity — not a second authority source — and it expires on
acceptance.** Rehearse rollback on a disposable rig before the live window; a live rollback receipt must
prove the pre-cutover served digest and durable run identity returned. Service health is not evidence.

### D7 — Explicit `NIGHTSHIFT_CTL_DB`, triple and inode-checked. (Both; `staff` supplied the evidence.)

`/proc/3525266/environ` carries `OMNIGENT_ANALYTICS`, `PWD=/srv/project`, `OMNIGENT_DATABASE_URI`,
`OLDPWD` — and **no `NIGHTSHIFT_CTL_DB`**. ADR-0010 B2's blocking condition is **currently unmet**, and
the CWD that saves us today (`/srv/project`) is exactly what the cutover may change. Absolute canonical
path, recorded device/inode before and after, same literal value in the service environment and in every
cutover/smoke/resume subprocess. Never answer `control_plane_missing` with `init`.

### D8 — Code owns every computable fact and PRINTS the launch line; the human owns one verb.

`architect`: mechanical truth belongs to code, outage authority to the owner. `staff`, more concrete and
adopted as the operative rule: **code prints the exact launch line; the human pastes it, never composes
it**, and decides only *when* to restart. A page of hand-run Git/SQL/hash commands recreates the incident
surface — one omitted operand silently changes the meaning.

### D9 — Smallest step that PROVES OR KILLS, before any live work. (Merged; `staff`'s question first.)

On a scratch rig (own port, own database, own artifact root, own `NIGHTSHIFT_CTL_DB`), all disposable,
**no live process, cache, DB or unit touched**:
1. **Does `agent_id` survive re-registration?** `staff`'s highest-value bit and its one open question.
   If it does not, property 4 is not assertable as designed and the whole path changes shape. Ten
   minutes, and it gates everything else.
2. Digest RED against `/srv/nightshift` — the stale-tree fixture must fail today, unmodified.
3. Git digest = registered digest = served digest; second startup bumps version, preserves `agent_id`.
4. Import `omnipods_policies` in the disposable serving process; verify resolved `__file__`, digest, PID,
   start time; run the harmless DENY canary and observe the provenance-bearing denial.
5. The three `enforced` RED fixtures — stale directory, **shadowing module earlier on the path** (import
   SUCCEEDS, so an import-success gate falsely passes), and ignored candidate path.
6. Nonce round-trip through a dispatched Claude seat and a dispatched Codex seat.
Every check must be shown RED before GREEN. `architect`: *"a test that only mocks `module.__file__` does
not establish this gate."* `staff` reports six of its checks are **already red today against the live box
or a real fixture, unmodified** — that is the RED half obtained for free, and it must be captured, not
re-derived.

### D10 — Sequence, ownership, and what is NOT in scope.

Two reviewed changes, literally two, exactly as the owner directed:
1. **Policy reconciliation PR** — repo-authoritative `gate_pushes: false` with rationale, DELETE the
   inert `risky_action`, add the unconditional DENY structurally OUTSIDE the gated branch, plus tests
   proving it denies with `gate_pushes` both true and false.
2. **Exact-SHA deploy PR** — `prepare`/`cutover` state machine, the five proofs, the in-process policy
   provenance surface, the launch parameterisation, D5's supervision-path neutralisation, the receipt
   schema and the operator runbook. Runbook and receipt schema ship WITH it and are not a third change.
Implementation goes to **`platform`**, not a crew: #13 means a crew lead cannot dispatch its own
engineers. Review: `staff` + `security` + cross-vendor. **No mega-PR. No live mutation by any worker.**
The cutover SHA must be a **descendant of `main` `c28d3147cc85f58dd186f7fca8429afe5fdcec1c`** containing
both changes — tonight's `main` is a floor, not the candidate. `deploy/preflight.sh:110-117` is replaced,
not extended: it greps that `PYTHONPATH` **exists**, not where it points, and passes on today's broken
state (`declared-but-not-enforced` **#10**).

## Options rejected

- **Advance a mutable checkout to a branch tip, restart, run a status command.** (`architect`.) Close to
  ADR-0010 G but unpinned: the source can move between review and start, and the status command can
  inspect the wrong process or cache. Branch-tip language turns an exact release into a race.
- **`git archive` whole-tree export as the first cutover source.** (`staff`.) Rejected FOR NOW only, on
  D2's reversibility trigger — not on merit. Both seats agree it becomes correct if sealing a checkout
  proves untenable.
- **Two independently pinned roots.** Rejected by both seats; see D1.
- **Extending `served` to cover policy origin instead of a separate `enforced`.** Rejected: it blurs
  "Omnigent serves the reviewed bundle" with "Python executes reviewed policy code". Different facts.
- **Identical launch contract asserted in both supervision paths.** Rejected in favour of leaving exactly
  one (D5): a consistency claim nobody re-checks is the pattern this ADR exists to end.
- **Registering via a helper process, an HTTP side-channel, or "register then restart".** Rejected by
  both: registration happens in the owning server process or it is not registration.

## Consequences

- The deploy is gated on SIX things that can each be shown to fail, not on a green CI run. Cost: change 2
  is meaningfully larger than "point `--agent` at a new path", and the in-process provenance surface is
  new production code on the enforcement path — reviewed by `security`, not just `staff`.
- `enforced` and `executed` add runtime coupling to Omnigent internals (audit record fields, session
  resolution). If Omnigent's policy interface cannot surface the deciding module's provenance, **stop
  there** — neither environment inspection nor a successful standalone import is an acceptable substitute.
- D5 removes a supervision path the org believed was its sanctioned mechanism. Anyone reading the unit
  file after this ADR will find it gone or inert by design; that is the point, and it is why it is
  written here rather than done quietly.
- Tech debt accepted, from `staff`'s register: seven items, including **TD-6, convergent duplication
  called pre-emptively** between its digest function and `platform`'s wave-6 detector. Register them
  before the implementation PR so the duplication is a decision, not an accident.

## Reversal triggers

1. `agent_id` does NOT survive re-registration on the scratch rig (D9 step 1) → property 4 is not
   assertable as designed; reopen this ADR before writing change 2.
2. Sealing a clean checkout for the whole release root proves untenable → amend ADR-0011(a) to immutable
   Git-object materialisation and adopt `staff`'s export.
3. Omnigent later exposes an atomic, template-safe, no-restart registration API returning a verifiable
   served-content digest → replace startup `--agent` with it, keep exact-SHA preparation and the proofs.
4. Any production decision record lacks policy origin/PID/start-time provenance, the resolved module
   leaves the release root, its digest differs, or the canary is not denied → cutover is not accepted and
   rolls back.
5. Further repo-owned imports are found outside the release root → stop treating a fixed path list as
   complete; promote the release-root rule to an allowlisted import-closure attestation.
6. Repeated inability to complete the window within the rehearsed recovery objective, or any observed
   `agent_id` churn → stop using the implementation and reopen ADR-0010 Option I rather than weaken
   acceptance.

---

## Amendment A1 — 2026-08-24 05:35Z — acceptance algebra and trigger owners

**Source:** `architect` falsifiability review, `#115#issuecomment-5391131379`, verdict
**`weak-clauses-named`**. Its finding: *"ADR-0013 is not instance #11 yet — each of the five intended
facts has a real failure mode, and `enforced` and `executed` close different holes. But if change 2
implements the nouns and not the fail-closed state transition, it can become instance #11."*
Nothing above is reversed. A1 TIGHTENS the clauses that were prose where they needed to be algebra.

**Still outstanding and NOT satisfied by this amendment:** `staff` has not verified that this ADR
represents ITS proposal faithfully (three upstream 529s; D-035). A1 addresses falsifiability only.

### A1.1 — The acceptance algebra is now normative (replaces the prose in D3)
The `undetermined` sentence in D3 is the correct semantic rule and **insufficient as written** — a
plausible implementation (`try/read; except/log; retain prior true`) preserves green through a failure.
Change 2 MUST implement:
- every proof is a required enum initialised to `undetermined`;
- `green` is set ONLY after all fresh operands for the current plan digest AND the current PID epoch are
  read and compared;
- every exception, timeout or missing field maps to `undetermined` — never to green, never to a prior value;
- acceptance requires **all five == green**; reuse of a verdict across epochs is forbidden;
- the receipt is written **even on failure**;
- tests must cover an unreadable DB, artifact, cache, Git object, audit record and session record.
Without these, D3 is the clause most likely to become instance #11.

### A1.2 — D4's "Only `executed` catches it" is TOO BROAD. Corrected.
`architect` is right and I was loose. If registration mints or targets a DIFFERENT id, the pre-recorded
stable-id and exact-version gate should ALREADY go red at `registered`. **`executed` is uniquely
necessary for the narrower and more dangerous case: registration correctly updates the expected row
while normal session resolution selects ANOTHER EXISTING row and its warm cache.** D4 is read with that
correction. Additionally: **the row-count invariant is restored as an explicit operand** — without it the
implementation can pass while adding a fourth duplicate `program` row.

### A1.3 — Receipt-schema requirements missed by D3 as written
- `registered` must carry the **expected pre-cutover `agent_id`, the exact version delta, and the
  row-count invariant** as operands — not merely as probe observations.
- `served` must include the **production-route session epoch**. Hashing a selected cache directory is
  not `served`: a correct but unused cache is green while sessions resolve an old id.
- `enforced` must prove there is **exactly one decision-capable serving epoch**, or run the canary once
  per such PID. One provenance-bearing denial from one PID does not cover a mixed-worker deployment.
- `executed` must show a **nonce-free challenge**, the persisted session/dispatch identity and
  registration epoch, and that the nonce entered the seat context **only from the resolved bundle** —
  not from the request wrapper, and not readable from the candidate checkout or the repository.
- `content-at-SHA`: derive the Git-object digest **independently** of the filesystem canonicaliser; a
  shared canonicaliser bug omits the same bytes on every path. Keep the semantic policy assertions.

### A1.4 — D9's blanket RED-before-GREEN is literally inapplicable to step 1. Restated.
Step 1 is an **empirical capability question** — one cannot force "id does not survive" red and then
obtain green without changing the runtime. The rule is restated as: **every acceptance ASSERTION needs a
negative control; every capability QUESTION needs a recorded falsifying outcome and a stop condition.**
Also: D9.2's stale-tree red depends on today's `/srv/nightshift` and becomes brittle when that changes —
**preserve a dirty/stale FIXTURE**. D9.3 needs separate negative controls for wrong registered artifact,
correct-but-unused cache, changed id, no bump, and a bump greater than one.

### A1.5 — Reversal triggers: owners assigned, or the trigger is decoration
`architect` audited all six for "who observes it, through which artifact". Triggers **1 and 4 are
genuinely observable** as written. The rest are amended:
- **T2** (checkout untenable): owner **`platform`**; evidence = failed rehearsal receipts naming a cause,
  or a reviewed exception. "Operationally untenable" alone is judgement, not a trigger.
- **T3** (Omnigent gains a no-restart registration API): owner **`staff`**; recorded as a capability test
  in the dependency/upgrade review. Nobody is currently required to look, so nobody would.
- **T5** (further repo-owned imports outside the release root): owner **`security`** with `platform`;
  evidence = a **mechanical import-closure artifact produced at prepare time**. Without a closure scan
  this is not a control at all.
- **T6** (repeated inability / `agent_id` churn): churn IS observable in receipts; **"repeated" is not** —
  define the window counter, the threshold, the receipt field, and name the ADR owner who aggregates.
**Rule going forward, and it is general:** a reversal trigger without a named owner and a named artifact
is decoration. That is the same pattern as a gate that cannot go red, one level up.

### A1.6 — Both runtime proofs are KEPT
`architect`, having authored one and reviewed the other: *"`enforced` and `executed` compose; neither
subsumes the other."* Cutting `enforced` reopens "right Program, stale or shadowed policy". Cutting
`executed` reopens "perfect registered and cached artifacts, wrong `agent_id` used by sessions". I asked
whether keeping both was redundant cost; the answer is no, and the cost stands.

---

## Amendment A2 — 2026-08-24 05:36Z — the probe answered, and it corrected the ADR twice

**Source:** `platform`, D9 step-1 probe on a disposable rig — `#115#issuecomment-5391134593`, PR **#131**
(`scratch/` only, against `program/discovery-state`, NOT `main`). Rig torn down; live server untouched
(same pid, same uptime progression, `.cache` mtime unchanged from probe start).

### A2.1 — THE GATE IS ANSWERED: `agent_id` SURVIVES re-registration. Reversal trigger 1 does NOT fire.
Three registrations of the same agent name from three different source directories: **one row
throughout, one id (`751da309…`), version 1 → 2 → 3**, `updated_at` set, bundle sha following the source
in both directions. Property 4 is assertable and change 2 may proceed on this point.
**D6 is mechanically confirmed as a side effect:** step 3 returned the row to step 1's exact sha under
the same id — that is the rollback-by-re-registration shape, demonstrated rather than argued.

### A2.2 — CORRECTION: property 4's MECHANISM is wrong as written. (Adopted.)
`agent_id` is **server-assigned (`uuid.uuid4().hex`) and cannot be pinned** — no spec field, no flag.
Stability comes ONLY from `get_by_name(spec.name)` at `cli.py:1424`. Therefore **"stable `agent_id`"
asserts nothing on its own**: the split path also produces a stable id, just a different one. Two
consequences, both normative:
- the assertion must pin the **EXPECTED** id, recorded pre-cutover, not merely "the id did not change";
- **the agent NAME is now a load-bearing deploy operand that nothing validates.** A rename, a case
  change, or a templated name is a silent full split, exit code 0 — `platform` reproduced it by changing
  ONE character. Change 2 must validate the name against the expected registration or the whole
  stable-id property is theatre.

### A2.3 — CORRECTION: D4's three-row evidence does NOT show what I cited it for. (Adopted, and it is my error.)
Restoring the owner's own `omnigent.pgdump` with named columns (disposable `postgres:17-alpine`,
`--network none`, `:ro` mount — the box's `pg_restore` is too old for file version 1.16) yields exactly
**ONE template row named `program`**. The other two are **`kind=2` session-scoped copies**, which
`get_by_name` excludes by construction. Corroborated independently two ways: `get_by_name` ends in
`.scalar_one_or_none()`, so duplicate TEMPLATE rows would raise `MultipleResultsFound` and the server
could not serve `program` at all; and the served row sits at v2 with `updated_at` set — the positive
signature of an in-place re-registration that PRESERVED its id. **The live box is evidence FOR the
answer, not against it.**
**The split is still real** — `platform` minted one on demand — and **D4's ranking may still stand, but
it now rests on the REPRODUCTION, not on the three rows.** D4's evidence sentence is withdrawn and
replaced by A2.1/A2.2's reproduction. I accepted `staff`'s F3 reading without independently checking
what `kind=2` meant; `platform` checked. That is the whole reason a probe outranks a proposal.

### A2.4 — `executed` is now MANDATORY on mechanism, not just on judgement
Cache Tier 2 hits on **directory EXISTENCE and never consults `bundle_location`**
(`agent_cache.py:82-97`). So a green `registered` and a green `served` digest still prove nothing about
what actually executes. A1.6 kept `executed` on argument; A2.4 keeps it on code.

### A2.5 — D9.2 digest RED is captured, with controls BOTH ways
Reviewed `bab0623` `program/**` = `4f18fa45…` vs `/srv/nightshift/program` = `bc1d15ba…` — **differ,
fails today, unmodified**. Controls: an identical copy is GREEN at `4f18fa45…`; one appended byte is RED
at `2088abcc…`. That is a check demonstrated able to fail in both directions, which is the standard the
whole `declared-but-not-enforced` register exists to enforce.

### A2.6 — Still open, and NOT to be reported as done
- **D9.4–D9.6 not run** (time-boxed): policy provenance, the three `enforced` RED fixtures, and the
  two-seat nonce round-trip — the last needs dispatched seats on the rig.
- **New untested risk, filed by `platform`:** `create`'s duplicate-name check is a **read-then-insert
  with no unique index behind it**, so two servers booting against one DB with the same `--agent` could
  both miss and both insert. Off the critical path ONLY because D5 leaves exactly one supervision path —
  which is now load-bearing for a second, unrelated reason. If D5 is ever softened, this becomes live.
