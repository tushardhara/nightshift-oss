# ADR-0001: Keep the single-host SQLite control plane; spend the project on truthful signals, not new technology

Date: 2026-08-22 · Status: **accepted** — `staff` review: consistent-with (docs/decisions/ADR-0001-0003-staff-review.md)
Deciders: program · Proposers: `architect` (Codex), `staff` (Claude) — two blind drafts
Conversation: 370d8bf6ad1442dd988d9a9f77620da3 · Spec: `docs/prd/PROJECT.md`

## Context

This is a self-dogfood run: Nightshift improving Nightshift, from `.omni/brief.md`. Four
installation and liveness bugs were confirmed during a real Hostinger install, followed by
thirteen audit areas. The system already exists — this is a brownfield repair project, so the
default answer to every architecture question is "keep what is there unless the numbers say
otherwise."

Two architects drafted independently and blind, on different vendors, from the same brief:
`docs/architecture/PROPOSAL-codex.md` and `docs/architecture/PROPOSAL-claude.md`. Both are
preserved unedited. The decision-by-decision reconciliation is in `.omni/decisions.md`.

Constraints real on this date: one Ubuntu 24.04 KVM VPS; two subscription credentials (one
Claude, one Codex); four concurrent workers with one reserved for on-call; runs of 3–7 days;
no sudo for the runtime user; the controller at `/srv/nightshift` frozen. Codex-native seats
(`architect`, `release`, `qa`, `eng_codex`, `reviewer_codex`) are non-functional on this host
for the reason recorded in ADR-0003 and issue #5.

## Options considered

### A — Keep SQLite/WAL as the single coordination authority (proposed independently by BOTH)
One database, one host, WAL mode, TTL ownership. Codex: *"A single SQLite database in WAL mode
is appropriate by a wide margin."* Claude: *"holds, by three orders of magnitude."*

Arithmetic, from Codex's draft: four slots heartbeating once a minute is 240 writes/hour,
5,760/day, 40,320 over seven days; a Program heartbeat adds ~10,080 over the same week; a
conservative tenfold allowance for claims, leases, checkpoints and vendor state still sits
below 10 writes/second at burst and far below 1/second averaged. Claude's independent
arithmetic: <200 writes/hour, <10k rows, <5 MB over seven days, with `tests/test_stress.py`
already exercising roughly 1000× the production rate.

Won. Two vendors reached the same verdict from independent arithmetic.

### B — Replace or supplement the datastore (server database, queue, distributed lock, event bus)
Neither draft proposed it, and Codex named it explicitly as something to *not* spend audit
time on: *"Four workers on one VM do not justify them."* Rejected — no arithmetic supports it,
and every component is a permanent operational tax on a box with no operator.

### C — Refine the existing liveness heuristic rather than replace it
Tighten the watchdog's `find` expression to exclude backup outputs. Rejected on Codex's
reasoning: *"Any directory-wide activity heuristic will regress when a new timer or report
writer appears."* An exclusion list is a fix that decays silently — the worst kind on an
unattended box.

## Decision

1. **Keep the single-host architecture and the SQLite/WAL control plane.** No new datastore,
   queue, lock service or event bus. No ladder tier moves this project.
2. **Spend the project on making signals truthful, not on adding technology.** In Codex's
   words, adopted verbatim as this project's thesis: *"The architecture does not need more
   technology. It needs fewer inferred signals and more executable proofs."*
3. **The system must stop conflating four distinct propositions** — a file changed; the Program
   completed a control-loop iteration; a process exists; a worker still owns and is progressing
   its task. Each gets its own signal with its own owner (ADR-0002).
4. **Capability is proven by execution, never by presence.** "Installed", "ships a profile" and
   "documented" are not properties. Every capability check in `deploy/` must run the thing.
5. **The org has exactly TWO seams**, so two crews and no more. Boundaries in `docs/owners.md`;
   the reconciliation of the two drafts' boundary disagreements is in `.omni/decisions.md`.

## Consequences

**Makes easy:** every confirmed bug is repairable inside the existing shape; no migration, no
new service to run or patch; the audit can be bounded because the technology question is
closed; both crews work in one language and one acceptance environment.

**Makes hard:** the liveness and ownership rework (ADR-0002) touches semantics that other code
depends on, which is why it is sequenced first and gated by an interface document. Deleting
`slot renew-active` removes a mechanism something currently relies on.

**Forecloses:** nothing structural. Multi-host coordination is not attempted and not designed
for beyond keeping one authoritative writer.

## Cost

No infrastructure cost at any volume: one VPS the owner already runs, one SQLite file, no
managed services. At 100× the current write rate the same design still holds — Codex's ceiling
analysis puts the real limit elsewhere entirely: **subscription quota, GitHub behaviour,
process supervision and truthful recovery**, none of which a database change would help.

Operational surface added: zero. Surface **removed**: blanket slot renewal, the `.omni`-wide
mtime heuristic, Markdown substring completion detection, and bootstrap's sudo fallback.

## One-way door?

**No.** Deliberately. Both drafts flagged the same four genuinely irreversible moves and all
four are avoided or deferred here: distributing a custom host AppArmor policy (ADR-0003, and
it stays the owner's action, not ours); changing the persisted SQLite schema without a
backward-compatible migration; changing the public semantics of slot renewal and heartbeat
ownership (ADR-0002 — versioned, with the old path kept one wave); and assuming one particular
systemd user-session model everywhere.

## Scale tier

**T0 on every concern in `docs/architecture/SCALE-LADDER.md`, and nothing moves this project.**
The only trigger either architect would accept for revisiting SQLite is measured evidence:
sustained write demand near the host's serialized transaction capacity; repeated `SQLITE_BUSY`
after bounded retries; p95 write-lock wait above ~100 ms during normal waves; WAL that cannot
checkpoint because of long-lived readers; a second host needing a single authoritative writer;
or dump time interfering with recovery objectives. None has fired.

## Reversal trigger

Revisit this ADR when any of the six measured conditions above is observed, or when the org
must coordinate across more than one host. A wish for more parallelism is **not** a trigger:
adding crews or workers does not change the storage arithmetic, and Codex's ruling stands —
more crews would queue on `deploy/`, `bin/nightshift-ctl` and shared docs while consuming the
same two subscriptions.
