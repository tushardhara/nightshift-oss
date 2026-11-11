# Frozen-path control — the forward-looking detector for DoD condition 21

Date: 2026-09-02 · Seat: `product` · Issue: [#522](https://github.com/tushardhara/nightshift/issues/522) · DoD condition: **21**
Raw capture: [`FROZEN-PATH-CONTROL.capture.txt`](FROZEN-PATH-CONTROL.capture.txt) — the baseline
manifest, the exact command that produced it, its exit code and its digests.
Recorded in [`docs/prd/DOD-MANIFEST.json`](../prd/DOD-MANIFEST.json) as `conditions."21".frozen_path_control`.

This is the **manual exception (2 of 2)** named in
[`docs/prd/DEFINITION-OF-DONE.md`](../prd/DEFINITION-OF-DONE.md) condition 21: *"a
forward-looking control for the remainder of the run — a read-only mount, a filesystem
audit rule, or a before/after snapshot — is supplied by the environment and recorded in
the manifest as `frozen_path_control` with its evidence."*

It is a **before/after snapshot control**, not an attestation. It does not say "today looks
fine". It says: here is the exact frozen surface as of the baseline, here is the command
that recomputes it, and here is what a violation would look like when that command is run
again. A reader who runs it and gets a diff has caught something; a reader who runs it and
gets no diff has a checked claim rather than a promise.

---

## 1. The distinction this control exists to draw

Condition 21 is a manual exception, and not a `grep`, for one reason. There are two
completely different things a repository can do with the string `/srv/nightshift`, and a
static scan cannot tell them apart:

| | |
|---|---|
| **Fresh-box install target** — *allowed, and the deploy tooling's designed job* | `deploy/provision-ubuntu.sh`, `deploy/bootstrap.sh` and `deploy/preflight.sh` create and populate `/srv/nightshift` when standing up a **new, empty** Ubuntu host. Measured: `deploy/provision-ubuntu.sh:360` — `install -d -o "$NS_USER" -g "$NS_USER" /srv/nightshift /srv/project`; `TEAMS_DIR="${TEAMS_DIR:-/srv/nightshift}"`. These scripts predate this project. The path is a *destination for an install that does not exist yet*. |
| **Already-running frozen instance** — *forbidden, and what condition 21 protects* | On **this** host, `/srv/nightshift` and `/home/nightshift/nightshift-runtime` are live checkouts of the owner's controller, at `a73ba881c04a4e5e2b716f89a0beb08a6e1164dc`, carrying the owner's own local modifications. Nothing this project adds or changes may reach in and mutate them. Fixes land on the **next** install (assumption B6); the running controller is not upgraded by this project. |

A literal "does any committed file write to this string?" scan flags column 1 and would
therefore FAIL condition 21 forever, for a reason condition 21 was never about. Conversely,
it would *miss* the actual risk entirely: every agent seat in this project runs as
`uid=1000(nightshift)`, and both frozen roots are `drwxr-xr-x nightshift:nightshift`. A
violation needs no privilege escalation and no committed script — one interactive
`git -C /srv/nightshift pull`, one stray `cp`, one agent that resolved a relative path
wrongly, and the frozen instance is modified with nothing in this repository's history to
show for it. That is the gap this control closes: it does not look at what the repository
*says*, it looks at what the frozen paths *are*.

`tests/dod_validate.py::check_d21` verifies the half of condition 21 that is soundly
machine-checkable — that no path under either frozen root is a tracked file in this
repository's history — and delegates the other half here. This document is that half.

## 2. Why a snapshot, and not the other two options

The DoD names three acceptable control shapes. Two of them are not available to any seat in
this environment. Measured on this host, 2026-09-02:

| Option | Status | Evidence |
|---|---|---|
| Read-only mount | **Unavailable.** Neither frozen root is a mount point; both live on the single `/` filesystem (`/dev/sda1`, ext4, `rw`). Remounting `/` read-only would stop the box. | `findmnt -T /srv/nightshift` → `TARGET /`, `SOURCE /dev/sda1` |
| Filesystem audit rule (auditd) | **Unavailable.** `auditctl` is not installed, and no seat has passwordless `sudo` (`sudo -n true` → *"sudo: a password is required"*). Installing auditd is an owner action, not an agent one. | `command -v auditctl` → not found; `id` → `uid=1000(nightshift)`, groups `nightshift`, `docker` |
| **Before/after snapshot** | **Chosen.** Needs no privilege, is reproducible by any reader, and its baseline is committed in-repo so it cannot be quietly re-baselined. | This document + `FROZEN-PATH-CONTROL.capture.txt` |

This is a real limitation, stated rather than papered over: the snapshot is **detective**,
not **preventive**. It cannot stop a write; it makes a write impossible to complete
unnoticed. If the owner later installs auditd or gives a seat the ability to bind-mount
`/srv/nightshift` read-only, that is strictly better and should supersede this — see §7.

## 3. What the baseline covers, and what it deliberately excludes

The baseline manifest is one `sha256 <TAB> path` line per file, for both roots, plus each
root's git `HEAD`. 186 body lines. Captured 2026-09-02T06:39:57Z. Run twice three seconds
apart: byte-identical.

**In scope — the frozen surface, which must not change:**

- every path `git ls-files` reports in each checkout (88 tracked files per root), hashed
  **as it exists on disk**, not as it exists at `HEAD`. This matters: both checkouts are
  already dirty relative to `a73ba88` — `/srv/nightshift` has 1 modified file,
  `/home/nightshift/nightshift-runtime` has 6 modified and 6 deleted plus 6 untracked seat
  directories (a `crew_beta` seat rename the owner performed on the live instance). A
  control that demanded a *clean* tree would fire on day one on pre-existing owner state
  and be switched off within a week. The baseline is therefore "the frozen instance exactly
  as the owner left it", which is the only thing this project is obliged not to disturb.
- every untracked-but-not-ignored path (`git ls-files --others --exclude-standard`), so
  that a *new* file appearing inside a frozen root is caught, not just an edit to an
  existing one.
- each root's `git rev-parse HEAD`, so a `pull`, `checkout`, `reset` or `merge` inside a
  frozen root is caught even if it happens to leave file contents alike.

**Out of scope — legitimately mutable, excluded on purpose:**

- `/home/nightshift/nightshift-runtime/.omni/**` — the running controller's own live state
  (its database, ledger, run records). The frozen instance writes there continuously, by
  design. Including it would produce a diff on every single run, which is the same as
  having no control at all. **Consequence, stated plainly:** this control does **not**
  detect a write into the running controller's `.omni/` state directory. That is a real
  blind spot; it is accepted because that directory is the one place the frozen instance
  is *supposed* to be changing, and no snapshot can separate "the controller wrote this"
  from "we wrote this" there. §6 says what covers it instead.
- `.git/**` internals in either root (git object churn from reflog/gc).
- file mtimes, atimes and inode numbers — content and presence only, so a read or a
  no-op rewrite does not produce noise.

## 4. The detector

Re-run this on the same host. It is read-only: it opens files, runs `git ls-files` and
`git rev-parse`, and writes nothing anywhere.

```bash
#!/usr/bin/env bash
# DoD condition 21 frozen-path control -- docs/audit/FROZEN-PATH-CONTROL.md
set -u
frozen_manifest() {
  root="$1"
  cd "$root" || { echo "UNREADABLE $root"; return 1; }
  echo "# root=$root"
  echo "# head=$(git rev-parse HEAD 2>/dev/null || echo NO-GIT)"
  { git ls-files -z 2>/dev/null; git ls-files -z --others --exclude-standard 2>/dev/null; } \
    | tr '\0' '\n' | grep -v '^\.omni/' | grep -v '^$' | LC_ALL=C sort -u \
    | while IFS= read -r f; do
        if   [ -L "$f" ]; then printf 'symlink:%s\t%s\n' "$(readlink "$f")" "$f"
        elif [ -f "$f" ]; then printf '%s\t%s\n' "$(sha256sum < "$f" | cut -d' ' -f1)" "$f"
        else printf 'ABSENT\t%s\n' "$f"; fi
      done
}
for r in /srv/nightshift /home/nightshift/nightshift-runtime; do frozen_manifest "$r"; done
```

Compare against the committed baseline — from a checkout of this repository:

```bash
bash /path/to/the/script/above > /tmp/frozen-now.txt
sed -n '/^--- BEGIN BODY ---$/,/^--- END BODY ---$/p' docs/audit/FROZEN-PATH-CONTROL.capture.txt \
  | sed '1d;$d' > /tmp/frozen-baseline.txt
diff -u /tmp/frozen-baseline.txt /tmp/frozen-now.txt && echo "CONTROL: PASS -- frozen surface unchanged"
```

**Pass condition:** `diff` is empty. There is no tolerance band and no "expected churn"
allowance — every excluded source of churn is excluded in §3 by name, so any remaining
difference is a real change to the frozen surface and must be explained.

**A violation looks like one of these:**

| Diff shape | Reading |
|---|---|
| `# head=` line changed | someone ran `pull` / `checkout` / `reset` / `merge` inside a frozen root |
| a `sha256 <TAB> path` line changed on the right-hand side only | an existing frozen file was rewritten |
| a `+` line for a path not in the baseline | a new file was created inside a frozen root |
| `ABSENT` appears where a hash was | a frozen file was deleted |
| `UNREADABLE <root>` | the root moved, was renamed, or lost its permissions |

Any of these is a **breach of condition 21** until someone shows the owner did it. Absence
of a diff is not proof nothing happened between two runs — see §6.

## 5. Who runs it, and when

The control is only a control if it is actually re-run. Minimum cadence for the remainder
of this run:

1. **At every DoD close-out attempt.** Whoever runs `tests/dod_validate.py` for a close-out
   verdict runs this detector in the same sitting and reports the diff (or its absence)
   alongside the D21 line. `check_d21` returns a `manual` verdict pointing at this file
   precisely so a human is forced to look at it; a `manual` verdict that nobody read is the
   failure mode this section exists to prevent.
2. **Before any deploy rehearsal or provisioning exercise.** Those are the changes most
   likely to mistake the running instance for a fresh box — run it immediately before and
   immediately after.
3. **At final handover.** The last run's result belongs in `docs/HANDOVER.md` §"frozen
   paths", with its date, so the stranger picking this project up cold inherits a checked
   statement rather than an assurance.

**On a hit:** stop, do not "fix" the frozen path back — that is a second write. Capture the
diff verbatim, file a P0 issue referencing condition 21 and this document, and escalate to
the owner, who is the only party who can say whether the change was theirs. Re-baselining
this capture file requires a PR that says who changed what and why; the baseline is
committed specifically so it cannot be moved silently.

## 6. What this does not catch — stated so nobody over-reads it

- **Writes into `/home/nightshift/nightshift-runtime/.omni/**`.** Excluded by design (§3).
  What partially covers this instead is condition 21's other, machine-checked half —
  no path under either frozen root is tracked in this repository's history — plus the
  standing worktree rule that agents work in their own worktree, never in a shared or
  foreign tree.
- **A write that is made and then perfectly reverted between two runs.** A snapshot samples;
  it does not journal. This is the specific weakness an auditd rule would not have, and the
  reason §2 says an audit rule should supersede this control if one ever becomes available.
- **Anything on a host other than this one.** The baseline digests are host-specific.
- **A write by a party outside this project.** The control detects change, not authorship.
  It deliberately cannot tell "this project did it" from "the owner did it" — which is why
  §5's escalation path ends at the owner rather than at a verdict.
- **Prevention of any kind.** This is a detector. See §2.

## 7. Superseding this control

If the environment later supplies a stronger control — auditd `-w /srv/nightshift -p wa`,
or a read-only bind mount — it should replace this one rather than sit beside it. That is a
`product` change: repoint `conditions."21".frozen_path_control` at the new evidence, add a
`docs/prd/DEFINITION-OF-DONE.md` changelog row saying what replaced what and why, and leave
this document in place marked superseded. Condition 21's prose is unchanged by such a swap;
only the evidence behind the manual exception moves.

## 8. Provenance

| | |
|---|---|
| Repo base SHA | `f2abab84785412f7dabc8e05d870e55f189ce9c7` |
| Frozen roots at baseline | both at git `a73ba881c04a4e5e2b716f89a0beb08a6e1164dc` |
| Host | `<vps-host>`, Linux 6.8.0-138-generic x86_64 |
| Baseline taken | 2026-09-02T06:39:57Z, by `product` under task `522-frozen-path-control` |
| Capture body digest | `2a080693e933139e5d57c4109612e790c1301514ae03999e1b90e380b216cb21` |
| Related | `tests/dod_validate.py::check_d21`; `docs/prd/DEFINITION-OF-DONE.md` condition 21 and its 2026-08-28 and 2026-09-02 changelog rows; `docs/HANDOVER.md` §frozen paths; assumptions B6, B7 |
