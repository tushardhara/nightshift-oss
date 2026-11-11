# FINDINGS — machine-readable finding index

DoD condition 18 (`docs/prd/DEFINITION-OF-DONE.md` §D, `docs/prd/DOD-MANIFEST.json`).
Closes issue #482 (filed by #475, Seam B of #423's DoD-drives-the-allocator split;
blocking artifact for condition D18).

**Authored directly by the `crew_alpha` lead, not by a dispatched engineer — working
agreement 5 (`.omni/working-agreements.md` item 5, "WA-5").** This item was
**owner-directed** straight into WA-5 this turn (run_id `fixture-review-231`), citing
issues **#489** and **#490** as cause, rather than re-reproducing the dispatch failure
live a second time this session: #489 (the `nightshift-ctl` binary actually on `$PATH`,
`/srv/nightshift-releases/71eee11dd391a14a0fa09018cade0cdeb0b13bc9/checkout/bin/nightshift-ctl`,
is missing origin/main's admission-derivation fix and is behind a fresh checkout, so a
worker dispatched against it cannot correctly acquire/consume the continuation slot this
task would need) compounded by #490 (the one documented refresh mechanism,
`nightshift-release prepare`, cannot re-run on this host because the legacy supervision
units it requires are masked-by-design after the prior successful cutover, and unmasking
does not restore them). Both issues were open at the time of writing. The same pair was
already live-reproduced this session for the sibling task `477-concurrency-audit`
(`docs/audit/CONCURRENCY.md`'s own WA-5 disclosure: `qa` slot 65 and `eng_claude` slot 66
both denied). Per **WA-5** (`.omni/working-agreements.md` item 5, issue #13) and its
owner-directed invocation for this item, the crew_alpha lead built this document
directly, single-author, under the control-plane hold recorded for this task (**lease 69**,
`docs/audit/FINDINGS.md`, edit mode, task `482-findings-audit`) instead of routing to an
engineer. In-crew review and in-crew QA did not happen for this document.
**Cross-crew review (`crew_beta`) is therefore mandatory, not customary, for this PR.**

Per DoD condition 22, `pm`'s Definition-of-Ready pass for this issue is recorded
separately (this turn's owner-directed 5-ticket batch — #478/#480/#481/#482/#483 — was
already carried through DoR with a READY verdict before dispatch; see `.omni/decisions.md`
and `.omni/STATUS.md`, "5-task parallel batch in flight").

Scope, per issue #482's own text: this document does not audit anything itself. It is the
disposition **index** for findings already raised, with their own evidence, by other audit
documents. Two exist as of this commit:

- `docs/audit/PIPELINE.md` (DoD condition 15, issue #479) — **merged**, pull request 497.
  Minted **F-101**, **F-102** (Q1, both `CONFIRMED-BUG`), **F-103** (Q3, `CONFIRMED-BUG`),
  **F-104** (Q4, `CONFIRMED-BUG`). Q2 was `SOUND` and minted no finding.
- `docs/audit/CONCURRENCY.md` (DoD condition 13, issue #477) — **merged**, pull request
  502 (that pull request is the audit report itself; per its own explicit
  scope boundary it makes no code change and remediates nothing). Minted **F-1** (Q1 and
  Q3, both `CONFIRMED-BUG`; Q2/Q4/Q5 were `SOUND` and minted nothing).

No finding id is invented here. Every id below already appears, pre-minted, in its source
document; this record only supplies the `source_doc`/`verdict`/`disposition` triple
`tests/dod_validate.py::check_d18` requires, per condition 18's own rule:

> every `CONFIRMED-BUG` disposition is an **integrated fix** — a merged (or gate-queued,
> per condition 22) PR plus an `RT` id plus a baseline record; **only** `ACCEPTED-RISK`
> may point to `.omni/tech-debt.md`, and then only to a row carrying a trigger and an
> owner; a confirmed violation of anything in the brief's protected set may not be
> dispositioned as `ACCEPTED-RISK` at all.

F-101 and F-102 have no integrated fix yet; F-1's fix is integrated (PR #530, RT-32) — see
its record below. All three are confirmed violations of the brief's protected set as
enumerated in condition 19 (worker limits, ownership leases, task claims, review
requirements, credential boundaries, sandboxing, branch protections, release gates) — F-1
is ownership leases/task claims, F-101 is branch protections/release gates, F-102 is
review requirements — so condition 18's own rule forbids dispositioning F-101/F-102
`ACCEPTED-RISK`. They stay `CONFIRMED-BUG` with an honestly `OPEN` disposition instead of
being force-fit into either closed shape; F-1 also stays `CONFIRMED-BUG` — the verdict
records what was found, not whether it was fixed. F-103 and F-104 are coverage/transport
gaps, not live breaches of a protected boundary, so `ACCEPTED-RISK` (pointing at
`.omni/tech-debt.md`, each with a named trigger and owner) is the accurate call for those
two. **This document therefore leaves `check_d18` FAILING, on purpose, for a real
reason** — F-101/F-102 remaining open — rather than fabricating a closing PR or RT id to
force a green result. That is the same "exact FAIL reason instead of file-not-found"
outcome issue #482 itself predicted.

```json
[
  {
    "id": "F-1",
    "source_doc": "docs/audit/CONCURRENCY.md",
    "verdict": "CONFIRMED-BUG",
    "disposition": "INTEGRATED — fix merged. `claim release`/`slot release`/`lease release` previously performed an unauthenticated hold-freeing state transition (bin/nightshift-ctl cmd_claim_release :1669, cmd_slot_release :1485, cmd_lease_release :1758; docs/audit/CONCURRENCY.md Q1/Q3 evidence, pull request #502, the audit report, not a fix, per its own explicit scope boundary). PR #530 (merge commit 6075e6f; ADR-0068, Refs #503/#508/#488), merged 2026-09-02, adds a required, verified --claimant/--holder to the claim/slot/lease release call sites, integrating the fix. RT-32 (docs/prd/DOD-MANIFEST.json, baseline captured against the pre-fix checkpoint) binds `cmd_slot_release` and `cmd_lease_release` — two call sites, not all four as an earlier draft of this record claimed — and reproduces this exact Q1/Q3 two-simultaneous-ALLOW scenario, asserting it is now refused. RT-32 does not itself exercise `cmd_claim_release`; that site's coverage is not established by this citation. This is a confirmed violation of the brief's protected set (ownership leases, task claims; DoD condition 19); the verdict stays CONFIRMED-BUG because it records what was found, not whether it is fixed — condition 18 requires the PR+RT pair precisely for that reason."
  },
  {
    "id": "F-101",
    "source_doc": "docs/audit/PIPELINE.md",
    "verdict": "CONFIRMED-BUG",
    "disposition": "OPEN — not yet remediated. Branch protection cannot currently be enabled on this repository's GitHub plan (live 403, docs/audit/PIPELINE.md Q1 evidence) and no replacement mechanical enforcement (a pre-push hook or an agent-layer policy denial on `gh pr merge`) has been built; 'release is the sole default-branch writer' remains prose discipline, not a mechanism. This is a confirmed violation of the brief's protected set (branch protections / release gates; DoD condition 19), so per condition 18's own rule it may not be dispositioned ACCEPTED-RISK. No pull request has closed this and no RT id is bound to it. Real-fix owner: program (issue #6, the pre-existing owner-tracked item this finding traces to). This record intentionally leaves check_d18 FAILING on F-101 rather than mis-close it."
  },
  {
    "id": "F-102",
    "source_doc": "docs/audit/PIPELINE.md",
    "verdict": "CONFIRMED-BUG",
    "disposition": "OPEN — not yet remediated. Self-review and different-crew/different-vendor review independence are still not mechanically verifiable: worker_semaphore's review-continuation branch and check_d22 both stop at 'a review exists', never at 'the reviewer differs from the author' (docs/audit/PIPELINE.md Q1 evidence). Root cause is structural — every seat shares one GitHub PAT, so GitHub itself records one identity for author and reviewer alike — and no per-seat credential or control-plane review-claimant record has landed. This is a confirmed violation of the brief's protected set (review requirements; DoD condition 19), so per condition 18's own rule it may not be dispositioned ACCEPTED-RISK. No pull request has closed this and no RT id is bound to it. Real-fix owner: product (same attribution as the underlying finding). This record intentionally leaves check_d18 FAILING on F-102 rather than mis-close it."
  },
  {
    "id": "F-103",
    "source_doc": "docs/audit/PIPELINE.md",
    "verdict": "ACCEPTED-RISK",
    "disposition": "ACCEPTED-RISK — documented in .omni/tech-debt.md (2026-08-30 row, '26 of docs/prd/DOD-MANIFEST.json's 40 FAIL-severity preflight_checks entries ... carry fixture_exempt_reason instead of a hermetic tests/test_preflight.py fixture'). Owner: crew_beta (seam B) — corrected from an earlier 'platform' attribution: docs/owners.md's general 'test fixtures / harness -> platform' row is superseded here by its own more specific row, 'tests that drive deploy/*.sh -> crew_beta (seam B), same rule, other side'. tests/test_preflight.py drives deploy/preflight.sh (subprocess.run([\"bash\", str(PREFLIGHT), ...]) throughout the file; the module's own docstring states verbatim 'These tests drive deploy/preflight.sh, so they belong to seam B (docs/owners.md)'), so ownership by what the test DRIVES puts it with crew_beta, not platform. Trigger: the first time a live-host regression in one of the 26 exempted checks goes uncaught (the tech-debt row's own trigger wording), or ADR-0054's own reversal trigger, whichever comes first. The detection logic itself is present and correctly shaped (docs/audit/PIPELINE.md Q3 evidence: the branch-protection block's bad/warn/ok logic is exactly right-shaped) — this is a test-coverage gap, not a live bypass of a protected boundary, so ACCEPTED-RISK is the accurate disposition here, unlike F-101/F-102 above."
  },
  {
    "id": "F-104",
    "source_doc": "docs/audit/PIPELINE.md",
    "verdict": "ACCEPTED-RISK",
    "disposition": "ACCEPTED-RISK — documented in .omni/tech-debt.md (2026-08-22 row, 'Sub-agent results are silently truncated in transit'). Owner: program (the row's own attribution, and the mechanism — sys_session_send/sys_session_get_history — is a shared engine surface, not any one crew's file). Trigger: any org artifact that is a document rather than a diff, i.e. most of them (the tech-debt row's own trigger wording), or the first time the chunked [CONTINUES]/[END] resend workaround fails to recover the full content. Not part of the brief's protected set (transport reliability, not a worker limit/lease/claim/review/credential/sandbox/branch-protection/release-gate), so ACCEPTED-RISK is a legitimate disposition here."
  }
]
```

## Why F-101/F-102 stay open rather than being force-closed

Each of these two is a genuine, still-live gap in something condition 19 calls a
protected boundary — a thing that is supposed to deny and currently does not. Writing a
closing PR number or an RT id that does not really exist would make `check_d18` return
`PASS` while the underlying denial is still absent, which is exactly the shape working
agreement 8 (`.omni/working-agreements.md` WA-008) warns against: a citation that cannot
support its claim is worse than none, because it stops the next reader looking. Leaving
them `OPEN` is the honest, checkable state; each names the issue tracking its real fix
(`#6` for F-101, product ownership for F-102) so the next reader does not have to
re-derive that either. F-1 no longer belongs in this group: its fix is integrated (PR
#530, RT-32), and its disposition above states that plainly, including what RT-32 does
not prove.

## See it for yourself

The full `python3 tests/dod_validate.py` suite also exercises this check, but it runs
every condition — including D19/D20's subprocess-heavy checks — and takes several
minutes; it is not what the line below was captured from. The isolated form calls
`check_d18` directly, terminates in under a second, and is the reproducible command: run
it from the root of any current checkout of this repository (no worktree-specific path
required).

```
$ python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "tests")
import dod_validate as m
root = Path(".").resolve()
manifest = json.loads((root / "docs/prd/DOD-MANIFEST.json").read_text())
ctx = m.Ctx(repo_root=root, manifest=manifest, manifest_error=None)
print(m.check_d18(ctx).line("D18"))
PY
D18 FAIL F-101: CONFIRMED-BUG disposition must name an integrated PR and an RT id: "OPEN — not yet remediated. Branch protection cannot currently be enabled on this repository's GitHub plan (live 403, docs/audit/PIPELINE.md Q1 evidence) and no replacement mechanical enforcement (a pre-push hook or an agent-layer policy denial on `gh pr merge`) has been built; 'release is the sole default-branch writer' remains prose discipline, not a mechanism. This is a confirmed violation of the brief's protected set (branch protections / release gates; DoD condition 19), so per condition 18's own rule it may not be dispositioned ACCEPTED-RISK. No pull request has closed this and no RT id is bound to it. Real-fix owner: program (issue #6, the pre-existing owner-tracked item this finding traces to). This record intentionally leaves check_d18 FAILING on F-101 rather than mis-close it."
```

Before this file existed, condition D18 failed with `docs/audit/FINDINGS.md not found`
(`_parse_json_blocks` on a missing path). After this commit, D18 fails instead with the
substantive, specific reason above — `check_d18` iterates records in document order and
returns on the first failing one; F-1's disposition now names an integrated PR and RT id,
so D18 reports F-101 first, and F-102 carries the identical class of failure (verified
independently below, since D18 itself fails fast). This is the exact
"blocking_artifact"/"exact FAIL reason instead of file-not-found" behaviour issue #482
itself predicted once this artifact existed. D18 will go green only once F-101 and F-102
each land a merged/gate-queued PR with a bound RT id — not from this document alone.

Per-record citation-presence check (a regex check for a `PR #`/`RT-` pair, not schema
validation), independent of check_d18's fail-fast order (confirms F-1/F-102/F-103/F-104
are not hiding a second, different defect behind F-101's early return):

```
$ python3 -c "
import sys, re
sys.path.insert(0, 'tests')
import dod_validate as m
from pathlib import Path
records, err = m._parse_json_blocks(Path('docs/audit/FINDINGS.md'))
assert err is None
for r in records:
    v = r['verdict']; d = str(r['disposition'])
    if v == 'CONFIRMED-BUG':
        ok = bool(re.search(r'PR\s*#?\d+', d)) and bool(re.search(r'RT-\d+', d))
    else:
        ok = True
    print(r['id'], v, 'PASS' if ok else 'FAIL (expected for F-101/F-102: no integrated fix yet)')
"
F-1 CONFIRMED-BUG PASS
F-101 CONFIRMED-BUG FAIL (expected for F-101/F-102: no integrated fix yet)
F-102 CONFIRMED-BUG FAIL (expected for F-101/F-102: no integrated fix yet)
F-103 ACCEPTED-RISK PASS
F-104 ACCEPTED-RISK PASS
```
