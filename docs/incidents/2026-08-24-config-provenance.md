# Incident — the org's live safety configuration exists only as an uncommitted working tree

**Date:** 2026-08-24 · **Seat:** `oncall` (worker slot 142) · **Severity:** P0 · `Refs #115`

**Action taken: containment and evidence preservation only. No remediation was
performed.** No server was restarted, signalled, re-registered or reconfigured.
Nothing in the served bundle or the artifact cache was edited, moved or deleted.
No configuration value was changed anywhere. The reconciliation decision is
ADR-0011's (PR #117) and is deliberately not this note's.

---

## 1. What is true

The live server serves the `program` agent bundle from a two-tier cache. What it
serves is byte-identical to `/home/nightshift/nightshift-runtime/program` — a
personal clone, on a divergent `main`, **with uncommitted modifications** — and
differs in **17 places** from the clean deployed checkout at `/srv/project/program`.

**Therefore `/home/nightshift/nightshift-runtime` is the de facto production
configuration of this org.** It is unreviewed, uncommitted, unbacked-up, gated by
no CI, and invisible to git. Established by execution in #115 (`platform`, slot
133) and #113; not re-derived here.

### The state of the registration source, as captured

| property | value |
|---|---|
| path | `/home/nightshift/nightshift-runtime` |
| branch | `main` (divergent from the integration ref) |
| `HEAD` | `a73ba881c04a4e5e2b716f89a0beb08a6e1164dc` |
| tracked paths modified or deleted | 12 |
| untracked directories | 6 (`program/agents/crew_beta/agents/beta_*`) |
| tree size | 5.7 MB, 378 entries |

The six untracked `beta_*` seat directories are worth naming separately: they are
**not in the uncommitted diff** (`git diff` does not show untracked files), so a
`git diff`-based capture alone would have silently lost the six renamed
`crew_beta` sub-agents that the running config actually defines. They were
captured by tree copy.

### The safety divergence

`program/config.yaml:505`:

| source | value |
|---|---|
| **served bundle** (what is executing) | `gate_pushes: false` + `risky_action: "DENY"` |
| `/srv/project/program` (deployed checkout, clean) | `gate_pushes: true` |
| `/home/nightshift/nightshift-runtime` **committed** `HEAD` | `gate_pushes: true` |
| `/home/nightshift/nightshift-runtime` **working tree** | `gate_pushes: false` + `risky_action: "DENY"` |

The uncommitted edit is the entire provenance of the running safety policy. Both
git-side committed copies agree with each other and disagree with what runs.

Per the security verdict (`#115#issuecomment-5389603403`): `gate_pushes` is not a
push gate but the master switch for the whole recoverable-but-destructive tier,
so under `false` every ordinary `git push`, `gh pr merge`, `gh repo delete`,
`terraform destroy`, and **`rm -rf` of any path under `/home`, `/opt`, `/root` or
`/srv`** is un-gated. That last item is why this note exists in the order it does:
**a single stray `rm -rf` would have destroyed the only copy of the configuration
this org is running, and nothing on the box would have stopped it.** The
accompanying `risky_action: "DENY"` is inert while `gate_pushes` is `false` — it
is read only inside the branch that flag disables — and it is worse than inert,
because it reads *stricter than stock* to a reviewer.

---

## 2. What was preserved, and where

**Destination: `/var/tmp/nightshift-incident-115/`** — outside `/home/nightshift/`,
on the root filesystem (372 GB free), owned by `nightshift`.

Why `/var/tmp` and not `/srv/project` or a home path: with no passwordless sudo on
this box it is the only writable location whose recursive deletion is
**unconditionally denied** — `/var` is in `_RM_SYSTEM_PARENTS`, so `rm -rf` of
anything beneath it is Tier-1 DENY under *both* settings of `gate_pushes`, whereas
`rm -rf /srv/project` and `rm -rf /home/nightshift/...` are currently un-gated.
The distro's 30-day `/var/tmp` age rule is commented out in
`/usr/lib/tmpfiles.d/tmp.conf`, so nothing reaps it. **This is still a same-box
copy and therefore not a backup in the durable sense — see item 5 below.**

| artifact | what it is |
|---|---|
| `nightshift-runtime-FULL-2026-08-24.tar.gz` | whole tree, incl. `.git/` and all dirty + untracked state |
| `nightshift-runtime.cp-a/` | `cp -a` tree copy, directly inspectable |
| `served-cache-dc15106203d44a47ac5954f7d20482e1.tar.gz` | the served bundle |
| `served-cache-dc15106203d44a47ac5954f7d20482e1/` | `cp -a` copy, `chmod -R a-w` |
| `evidence/` | git text captures + per-file checksum manifests |
| `SHA256SUMS.txt` | checksums below |

```
c105c6c67960001a6a3b62b9f19c2bace48f8245be22afe3f2e649391fb2884f  nightshift-runtime-FULL-2026-08-24.tar.gz
413c09636c36b6edebd4e8bfa63fec75cc6c9befeb3d4828aee1621c5905851a  served-cache-dc15106203d44a47ac5954f7d20482e1.tar.gz
```

`evidence/` holds `git-status--short.txt`, `git-status-porcelain-all.txt`,
`git-rev-parse-HEAD.txt`, `git-branch.txt`, `git-log-oneline-5.txt`,
`git-remote.txt`, `git-untracked-files.txt`, the full uncommitted diff
(`git-diff-uncommitted.patch`, 875 lines), `served-cache-mtime.txt`, and
per-file SHA-256 manifests for both tree copies (243 and 41 files).

### Verification performed

- Tarball entry count equals live tree entry count (378 = 378); `gzip -t` passes.
- `diff -rq` live tree vs `nightshift-runtime.cp-a/` → exit 0, identical.
- `git -C nightshift-runtime.cp-a status --short` reproduces all 18 lines of dirty
  state — **the uncommitted modifications are preserved as uncommitted
  modifications.** Nothing was committed, stashed, cleaned or staged.
- `diff -rq` live cache vs the served-bundle capture → exit 0, identical.
- Served-bundle directory mtime **`2026-08-22 19:59:29 +0000`**, recorded and
  **unchanged after the capture** (verified before and after). The original was
  copied, never moved or modified. It remains the executing bundle.

That mtime is itself the evidence of the mechanism: the server process was started
`2026-08-23 18:18:32`, so the directory it is serving from is a day older than the
process serving it — a Tier-2 disk-cache hit, with `bundle_location` never read.

### Secrets

The uncommitted diff and the six untracked seat directories were scanned for
credential material (keys, tokens, `ghp_`/`github_pat_`/`sk-`/`AKIA` patterns,
private-key headers, `password`/`secret`). **No credentials found** — every match
is prose inside agent instruction text discussing the box's credential posture.
The diff is nonetheless not reproduced in the PR: it is preserved on the box only.

---

## 3. Why no remediation was performed

**Restoring `gate_pushes: true` would deadlock this org.** Under `true`, every
`git push` and every `gh pr merge` returns ASK; `ask_timeout: 86400`
(`config.yaml:491`) with no human present parks each such call for 24 hours. Every
seat on this box pushes branches and the `release` seat merges with a shell
`gh pr merge`. The result would be an org that cannot ship anything until a human
arrives — self-inflicted, unattended, overnight.

So: **on the merits, `false` is the operationally correct value for an unattended
box. The defect is the provenance, not the value.** Those are separable and only
one of them is urgent. An unattended rollback would trade an unreviewed-but-working
config for a reviewed-and-deadlocked one, and would be *precisely* the next
stale-config incident — this time with our name on it.

Three further reasons the correct action here was to stop:

1. **The deploy path is undefined.** #115 proved there is no code path from
   `program/agents/**` into the running server on this configuration, and that a
   restart does not propagate a new spec. There is no known-good mechanism to
   apply a fix with, so any "fix" would be an invention made at 01:00 with nobody
   watching.
2. **The served bundle is live.** It is what is executing right now. Editing or
   deleting it takes the org down, and under a Tier-2 cache with no invalidation
   the failure would not be recoverable by restart.
3. **The decision is already chartered.** ADR-0011 (PR #117) decides the direction
   — repository authoritative, reconciliation deferred, the 17 differences
   adjudicated individually as ordinary `program/**` PRs, with anything worth
   keeping from the running side merged *before* re-registration. That is the
   correct venue. Pre-empting it unattended would destroy the evidence the
   decision rests on.

Containment, not correction, was the whole job. The single-copy exposure is now
addressed; the divergence is untouched and fully documented.

---

## 4. What would have caught this earlier

This was found **by accident**, by hand, by a seat chasing an unrelated cost
question. That is the finding behind the finding: the detection gap was not
partial, it was total. Nothing in the org observed the running configuration at
all. Six things, roughly in order of value per unit of work.

**1. Compare what the server *serves* against a committed ref — nothing does.**
#107's detector compares the *deployed checkout* against the *committed ref*.
Those two were byte-identical, so it was green, and both of its operands were
irrelevant to what executes. The missing check has the live server as one operand:
fetch the live spec through the agent API and diff it against
`git show <integration-ref>:program/`. This is a small periodic job and it is the
single highest-value control on this list — it would have fired on 2026-08-22, the
day the cache directory was written.

**2. Alert on the security-relevant subset at a higher severity.** Any difference
under `guardrails.policies` between served and committed should page rather than
file. A flip of `gate_pushes` is not drift, it is a change of the org's blast
radius, and it should not be able to sit unobserved for two days behind a
green dashboard.

**3. Make provenance a registration-time invariant, and record it.** Registration
is the only moment provenance exists; after it, the source is unrecoverable —
which is exactly why answering "where did the running config come from?" required
hand-diffing cache directories against home directories. Two parts: (a) refuse to
register a bundle whose source is not a *clean* checkout of a commit reachable
from the integration ref — that single rule makes this incident impossible, since
the source here was a dirty tree on a divergent branch and fails both halves; and
(b) store the source commit SHA next to `bundle_location` so the question becomes
a query instead of an investigation.

**4. Key the artifact cache by content, not by `agent_id`.** The reason the
divergence *persisted* is a two-tier cache keyed on `agent_id` with no
invalidation on a `bundle_location` change and no TTL. Including a content hash
(or the source commit SHA) in the cache key turns a changed bundle into a natural
miss, and converts a silent stale-serve into an automatic reload. This is upstream
code, so it is a request to file rather than a control the org can install itself
— items 1 and 3 are the org-level equivalents and do not depend on it.

**5. Test the policy's *behaviour*, not its text.** The deepest lesson here is
that both git-side artifacts were correct and both were irrelevant, and that the
running config read *safer than stock* to a reviewer while being wide open —
`gate_pushes: false` and `risky_action: "DENY"` each read "safe" and compose to
"un-gated". No amount of config review catches that; an assertion does. A startup
self-test that asserts a representative Tier-2 command actually returns ASK would
have failed loudly regardless of how the YAML reads, and regardless of which file
the server picked up.

**6. Treat any registration source as a backed-up asset — or forbid the category.**
The configuration this org actually runs had no backup, and per item 3 it should
never have been a personal home-directory clone in the first place. Fixing the
category (item 3a) is the real answer; until then, anything acting as a
registration source needs a snapshot. Note also that the copies this note records
are **same-box**: they survive a stray `rm -rf`, which was the acute risk, but not
loss of the host. Moving these artifacts off-box is a live follow-up, not
something this note claims is done.

**Process, not tooling:** nothing in `deploy/README.md` forbade a dirty personal
clone as a deploy source, and no inventory recorded which path the live `agent_id`
had been registered from. Both are cheap to write down and neither existed.

---

## 5. Follow-ups this note hands off

- **Off-box copy** of `/var/tmp/nightshift-incident-115/`. Same-box preservation
  addresses the deletion risk, not host loss.
- **Item 1 above** — served-vs-committed detector — as a backlog item; it is the
  gap that let this run for two days.
- **The reconciliation itself stays with ADR-0011 / PR #117.** Not this note's.
- **The security verdict's own open item** (`#115#issuecomment-5389603403`):
  per-seat policy blocks appear never to load, which would mean `read_only_os` on
  the reviewer and `security` seats is not in force. Verified by reading the code
  path, not by probe. It needs the scratch-server confirmation it asks for.

`Refs #115`
