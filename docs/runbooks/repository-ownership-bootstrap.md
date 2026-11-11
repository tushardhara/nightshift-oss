# Runbook: bootstrapping repository ownership (ADR-0085)

Refs: ADR-0085 (`docs/decisions/ADR-0085-external-repository-pr-only-merge-fence.md`),
ADR-0082 (`docs/decisions/ADR-0082-migration-authority-epoch-quiesced-switch.md` — the
registry this decision extends), `bin/nightshift_authority.py`,
`deploy/policies/omnipods_policies.py`'s `external_repo_merge_fence`.

## 0. What this is, and why it is breaking

Once `external_repo_merge_fence` is attached (`program/config.yaml`, bundle root) and
served, `gh pr merge` — and the equivalent `gh api .../pulls/<n>/merge` — is **denied
by default** on any deployment that has not explicitly declared which repository it
is operating on and that the deployment owns it.

This is deliberate and disclosed, not an oversight: the fence's whole purpose is that
silence must never grant merge. An existing deployment that upgrades without running
step 2 below will see the release seat start refusing every merge with reason code
`merge_forbidden_external_repository`. That is the fence working correctly on an
unbootstrapped deployment, not a bug to route around.

Run this runbook once per deployment, before or immediately after the release that
ships ADR-0085's fence.

## 1. Decide: is this deployment's target repository self-owned or external?

- **Self-owned**: Nightshift's own repository (`tushardhara/nightshift`, or wherever
  this deployment's own control plane and release procedure live). The release seat
  merging PRs into it is correct and is exactly what happens today.
- **External**: a repository this deployment does not own — a customer repository, or
  any repository where merging is someone else's decision. The release seat may open
  pull requests against it and must never merge one.

If you are unsure which one applies, declare `external`. The fence's default (no
declaration at all) is already `external`; declaring it explicitly only makes that
fact visible and auditable, and is always the safe choice when in doubt.

## 2. Find the two values you need

- `STATE_ROOT` — the same value already passed to `deploy/bootstrap.sh` for narrative
  memory (ADR-0021/#400). If this deployment has never set one, pick an absolute path
  outside every Git worktree, owned by the runtime user, mode `0700`.
- `REPOSITORY_ID` — the target repository's **GitHub numeric id** (not its name). Get
  it with a read-only API call:

  ```
  gh api repos/<owner>/<repo> -q .id
  ```

## 3. Declare ownership

Run this as the runtime user, on the box (or anywhere with filesystem access to
`STATE_ROOT` — the command itself makes no network call):

```
python3 bin/nightshift_authority.py ownership-declare \
  --state-root "$STATE_ROOT" \
  --repository-id "$REPOSITORY_ID" \
  --ownership self \
  --declared-by "<your name or identity>"
```

(Use `--ownership external` for a repository Nightshift does not own.)

This is **write-once**: a second `ownership-declare` with the same
`--repository-id`/`--ownership` pair is idempotent and returns the original row
unchanged. A second call with a *different* value for either is refused with reason
code `repository_ownership_already_declared` — the database will not let you flip an
already-declared row, by design (see ADR-0085). To change a repository's declared
ownership, remove the registry file for that repository id
(`<STATE_ROOT>/authority/<REPOSITORY_ID>/authority.db`) and declare again; see §6.

Expected output on success:

```json
{"declared_by": "...", "id": 1, "ok": true, "ownership": "self", "recorded_at": ..., "repository_id": ...}
```

## 4. Wire the environment so the served policy can see it

The served policy reads `NIGHTSHIFT_STATE_ROOT`/`NIGHTSHIFT_REPOSITORY_ID`
from `/etc/nightshift/server.env`, which root writes and the runtime user
cannot edit. The registry you just declared lives in a runtime-owned state
root, which any worker could also edit, so root promotes a read-only copy and
points the fence at that instead. As root:

```
/srv/nightshift/deploy/install-root-config.sh --user <runtime user> \
  --state-root "$STATE_ROOT" --repository-id "$REPOSITORY_ID"
```

This copies `authority.db` and `tracker.db` to
`/var/lib/nightshift/authority-root/authority/<REPOSITORY_ID>/` (root-owned,
read-only) and sets `NIGHTSHIFT_STATE_ROOT` to that root. Re-run it after
every later declaration; the fence never sees an edit until root promotes it.
Then restart `omnigent-server` and `omnigent-host`, which read the file only
at start. In step 5, pass `--state-root /var/lib/nightshift/authority-root`
to check what the fence actually reads.

## 5. Verify

Read the classification back without touching the fence:

```
python3 bin/nightshift_authority.py ownership-show \
  --state-root "$STATE_ROOT" --repository-id "$REPOSITORY_ID"
```

Run the exact preflight the release seat runs before merging:

```
python3 bin/nightshift_authority.py merge-preflight \
  --state-root "$STATE_ROOT" --repository-id "$REPOSITORY_ID"
```

Exit `0` means this deployment may merge into this repository. Exit `3` with
`reason_code: merge_forbidden_external_repository` means it may not — expected and
correct for a repository declared (or defaulted) `external`.

## 6. Reversal

Declaring ownership is write-once at the database layer; there is no "undeclare"
verb. To change a repository's classification: stop the server, delete
`<STATE_ROOT>/authority/<REPOSITORY_ID>/authority.db`, and repeat steps 3–5. This also
resets the ADR-0082 migration-authority epoch binding that file carries for this
repository id — rebuildable via the normal `init_binding` bootstrap path, and safe
because it is a fresh repository id's registry, not a live one being repointed.

## 7. Common mistakes

- **Declaring against the wrong `STATE_ROOT`.** The fence reads exactly
  `NIGHTSHIFT_STATE_ROOT`/`NIGHTSHIFT_REPOSITORY_ID` from the server's own
  environment — declaring into a different directory than the one `server.env` names
  changes nothing the fence will ever see. `ownership-show` with the *same* flags the
  server would resolve is the way to catch this before it surprises you at merge time.
- **Assuming a restart is optional.** `omnigent-server`/`omnigent-host` read
  `server.env` at process start only. Editing it (by hand or via bootstrap) with
  either unit already running does nothing until both are restarted.
- **Treating a `merge_forbidden_external_repository` denial as an error to route
  around.** There is no override, and there should not be one — see ADR-0085's
  Decision section. If a merge is genuinely supposed to succeed, the fix is to
  correctly declare `self` for the right `(STATE_ROOT, REPOSITORY_ID)` pair, not to
  bypass the fence.
