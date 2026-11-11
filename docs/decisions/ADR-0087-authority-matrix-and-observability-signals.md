# ADR-0087: A code-checked authority matrix, a closed eight-signal observability set, and the nse-v1 export format

Date: 2026-09-05 · Status: accepted
Deciders: staff (Claude) · Proposers: staff (Claude)
Relates to: ADR-0076..0083 (the #400 memory/publication/tenancy/migration ADRs),
ADR-0081 (publication gateway, the only repository write path) · Issue: #400

## Context

#400's own "Non-negotiable acceptance contract" names 23 conditions. By the time
this stream picked the epic back up, ten were genuinely proven, one (condition 7,
PostgreSQL parity) was deliberately and mechanically refused, and the remaining
twelve had either no evidence, thin evidence, or — in one case — evidence that
contradicted the condition it claimed to satisfy. This ADR records the three
conditions that needed something BUILT (4, 17, 21) and the one real code defect
found and fixed along the way (3), rather than merely measured. Conditions 2, 5, 6,
8, 11, 15, 19, 20, 22 and 23 needed only a behavioral test against code that
already existed and are not this ADR's subject — they are evidence, not decisions.

Three sibling streams — extrepo (ADR-0085, repository ownership and the merge
fence), 165/repository-scope (ADR-0084, `repository_id` on control.db's work
rows), and 442/conformance-gate (ADR-0086, the advisory-cannot-authorize gate) —
were assigned to land before this one and were **not yet merged into `main`** at
the commit this stream built against: `bin/nightshift_conformance.py` does not
exist, `nightshift_authority.classify_repository` does not exist, no
`repository_ownership` table exists, and control.db carries no `repository_id`
column. This ADR's authority matrix is accurate against that commit and is
deliberately built to fail loudly, not silently pass, the moment those streams'
tables land undeclared — see Decision 1.

## Decision 1 — the authority matrix is code, mechanically checked against the live schemas

`bin/nightshift_authority_matrix.py` holds `DATA_CLASSES`: one entry per data
class, naming its durable authority (store + table, or store + file path) and,
for a projection, exactly what it projects onto. This is the machine-checkable
form of `docs/CONTROL-PLANE.md`'s "What lives where" table, extended past
control.db to the twenty tables and one CAS object tree #400 itself added.

**Rejected: a JSON spec plus a parser.** A data file drifts from the schemas it
describes silently; a test that reads it is a test of the file, not of the
system. **Chosen:** the table set is derived by regex
(`discover_tables()`/`discover_ctl_tables()`) straight from the live schema
string constants (`PUBLICATION_SCHEMA`, `PROVENANCE_SCHEMA`, `TENANT_SCHEMA`,
`KEY_SCHEMA`, `AUTHORITY_SCHEMA`, `MEMORY_SCHEMA`) and from
`bin/nightshift-ctl`'s own source text — never hardcoded as a count. An earlier
pass at this epic hardcoded "24 inline CREATE TABLE statements" as a fact about
the repository; it was stale within one stream's worth of changes. This module
states no count, anywhere.

`verify_matrix()` diffs the discovered table set against `DATA_CLASSES` in BOTH
directions: `undeclared` (a real table with no matrix entry) and `orphaned` (a
matrix entry naming a table that no longer exists). Either non-empty means
`ok=False`. A future stream that adds a table without touching this file gets a
report naming the exact table — that is the intended failure, not a defect in
whoever added the table (see the cross-stream binding notes' own item 10).

**The projection half needs behavior, not a label.** Declaring
`.omni/control-state.json` a projection of `worker_slots` proves nothing about
whether the real `nightshift-ctl slot acquire` code path can be fooled by a
poisoned copy of that file. This ADR does not claim the label is the proof; the
proof is the behavioral test that drives the real binary with a poisoned
projection and asserts the verdict still follows `control.db`.

### Two independent axes, not merged

Per the cross-stream ruling: authority.db's `authority_binding.active_backend`
(`legacy`/`external` — ADR-0082, which store is authoritative) and the
forthcoming `repository_ownership.ownership` (`self`/`external` — extrepo,
ADR-0085, whose repository this is) are declared as **two separate data
classes** even though they will live in the same database. Neither is a
projection of the other. The matrix's pending-declarations block spells out the
exact entry the landing stream adds; this module does not fabricate it early,
because a `DataClass` entry naming a table that is not yet real would make
`verify_matrix()`'s own `orphaned` check fail against this file — exactly the
"evidence for something that did not happen" shape #400 exists to refuse.

### Identity, not re-litigated here

`repository_id` (a positive Python int, the GitHub numeric repository id) is
validated by exactly one function this module calls — `nightshift_authority.
validate_repository_id` — never reimplemented. `repository_fingerprint`,
`tenant_id`, and `commit_oid`/`tree_oid` are separate content-identity axes and
are never coerced to, or confused with, `repository_id` anywhere in this stream's
new code.

## Decision 2 — the eight AC-21 signals are a closed set, each MEASURED or REFUSED, never null

`bin/nightshift_observability.py` reports exactly the eight signals condition 21
names — no more, no fewer. `REQUIRED_SIGNALS` is exhaustive, not a lower bound;
this stream does not add a ninth (repository-scope or ownership) signal even
though the AC-19/22 sibling work in this same PR makes that temptation obvious.

Four are computed from rows that already exist: `volume`, `latency`,
`publication_queue_time`, `retention_deletion_backlog`. Four have no backing
mechanism in this repository today: `outbox_age` (no outbox table exists —
ADR-0076 describes one as target design, never built), `isolation_failures`
(cross-tenant refusals are DELIBERATELY designed to leave no row — there is
nothing to count without a new append-only refusal-audit table, which is a
schema change outside a measurement stream's remit), `restore_evidence` (no
backup-restore drill is recorded anywhere — the tenant-key deletion receipt
proves a maximum backup-expiry window, not that a restore was ever exercised),
and `export_duration` (measured once `nightshift_export.py` has produced at
least one receipt for the scope in question; refused until then).

**Rejected: return `null` for the four unmeasurable signals.** A dashboard of
nulls reads as "healthy, nothing to report." **Chosen:** each signal function
either returns a value or raises `ObservabilityError` with a typed
`reason_code`; `collect()` is the one place that turns either outcome into the
report's `{"status": "measured", "value": ...}` / `{"status": "refused",
"reason_code": ...}` shape. The set of currently-refused signals is itself an
assertable fact: the day someone adds an outbox table, `outbox_age` stops
raising, and anything that hardcoded today's refusal set breaks loudly. That is
the inverse of the rigged-evidence shape an earlier adversarial review of this
epic found — a lineage walk that stopped at a real boundary and still reported
`closed=True`. Here, an unmeasurable signal reports `refused`, never a quiet
`measured: null`.

**Rejected: a `nightshift-ctl observability` verb.** It would mean editing
`main()` in a file three other streams may also be editing this wave.
`bin/nightshift_publication.py`'s own `build_parser()`/`main()` is this
repository's established precedent for a #400 module owning its own CLI; this
module follows it, and `bin/nightshift-ctl` is untouched.

## Decision 3 — nse-v1: a digest-closure export bundle, verifiable without opening a database

`bin/nightshift_export.py`'s `export_tenant()` writes a self-describing bundle
(`MANIFEST.json` with `format_version`, integer `tenant_id` and `repository_id`,
a `{path, sha256, size}` entry per bundle file, and an `entries_digest` over that
entry list) plus the tenant/repository-scoped provenance rows and whatever this
tenant/repository has physically staged in its content-addressed object tree.

`verify_export(out_dir)` is a **pure function of the bundle**: it imports no
sibling module's `connect()`, opens no database, and takes no `state_root` — its
only input is the directory. That is what makes "verified without trusting the
producing Nightshift instance" a checkable property rather than a slogan: the
verifier's call graph cannot reach the producer even if it tried. It recomputes
every file's digest, recomputes the manifest's own `entries_digest`, and
re-derives referential integrity for `derived_from` edges entirely from the
bundle's own `lineage_node.json`/`derived_from.json` files.

### The honest limit

Digest closure detects corruption and truncation: a flipped byte, a deleted row
file, an edited manifest, a `derived_from` edge whose endpoint was quietly
removed. **It does not detect a producer that lies consistently.** A Nightshift
instance that fabricates a self-consistent set of rows and hashes its own
fabrication produces a bundle that verifies clean. There is no signing-key
infrastructure a customer can independently root in a stdlib-only system with no
pip install step, so this is the honest T0 answer to condition 17's words, not
the strongest possible reading of them. A future signed-export format is a
version bump (`nse-v2`) behind the same `format_version` field, not a rewrite of
what is here — this format is additive by construction, so nothing already
exported has to be regenerated when that lands.

`repository_id` in the manifest is the same integer identity Decision 1
describes, not a free-text repository slug.

## Also landed in this PR — ADR-0081's own stated gap, closed

ADR-0081 (accepted, 2026-08-29) states plainly: "No-generated-file mode wires
only the reviewed-patch adapter." Reading `patch_stage` against that sentence
found a real defect: it required only a `publication_stage` row — no
`publication_review` row, no reviewer identity, no self-review check — and
happily returned a diff in `no_generated_file` mode with **nothing reviewed**.
That contradicts condition 3 ("returns reviewed patches for the customer to
apply") and ADR-0081's own words about itself.

The fix is scoped to `no_generated_file` mode only: `patch_stage` now refuses
`patch_requires_review` unless the stage's latest review has `verdict='approve'`
and a `reviewed_digest` matching the stage's own `content_digest`, and re-asserts
`_assert_not_self_review` against that review — the same "checked at review AND
approve AND [now] patch" design `approve()` already documents for itself. In
`zero_nightshift_file` mode (the default), `patch` is completely unaffected: it
remains the never-writes preview tool it always was, because `apply_publication`'s
own review/approve gate is what protects that mode's real write path.

This is not a new architectural decision — it is ADR-0081 enforced as written —
so it is recorded here rather than opening a new ADR number for it. It does not
touch `bin/nightshift_publication.py`'s external-repository merge policy, which
this stream does not own.

## Consequences

**Makes easy:** a future table addition gets a named, specific CI failure
instead of a silently stale "What lives where" table; a future operations
dashboard has exactly eight keys to render, with an honest status on each; a
customer's compliance team can verify an export bundle on an air-gapped machine
with nothing but the bundle and a Python 3 stdlib interpreter.

**Makes hard, on purpose:** adding a ninth observability signal, or resolving a
signal's refusal, now requires touching this file and its ADR — that friction is
the point; AC 21's set is closed.

**Forecloses:** silently declaring the `outbox_age`/`isolation_failures`/
`restore_evidence` gaps "not applicable" instead of refused. Once a mechanism
exists to back one of them, the refusal must be replaced with a real
measurement, not deleted quietly.

## Cost

Zero new operational surface: three stdlib-only Python modules, no new service,
no new dependency, no new database. Storage cost of export bundles and export
receipts scales with how often `export_tenant()` is actually called — nothing
runs on a schedule.

## One-way door?

No. The matrix is documentation-as-code and can be re-derived at any time; the
observability refusal set shrinks (never widens) as real mechanisms land; the
export format is versioned and additive (`format_version` is read and checked
by `verify_export`, so an `nse-v2` bundle cannot be misread as `nse-v1`).

## Scale tier

T0 — see `docs/architecture/SCALE-LADDER.md`'s `observability` row, whose
numeric trigger this ADR's own signal set now makes measurable in the first
place (before this, there were no per-store row counts or attempt latencies to
compare a trigger against).

## Reversal trigger

None named. If a future stream needs a ninth AC-21-shaped signal, that is a
change to condition 21 itself (a ticket against #400 or its successor), not a
unilateral addition to `REQUIRED_SIGNALS`.
