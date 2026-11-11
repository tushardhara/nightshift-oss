# 492 — stop serializing the Postgres credential onto argv at server launch

Status: **frozen spec — ready for build (pending DoR label swap).** This document
is the versioned review contract of record for #492 (**v1**, 2026-09-02). Sign-off
against this issue is judged against this version; a criteria or scope change
requires a visible new version and invalidates every affected approval, per this
crew's PM contract.

Mechanism is **settled and security-approved** — comments
[5502856485](https://github.com/tushardhara/nightshift/issues/492#issuecomment-5502856485)
(assessment: env-var route withdrawn — `omnigent server`'s resolution order is
CLI flag → `-c/--config` YAML → hardcoded local-sqlite default, never
`os.environ`; confirmed live against the installed `omnigent` v0.10.0 CLI in
this workspace: `omnigent server --help` shows `-c, --config PATH  Path to
YAML config file.`) and
[5502934678](https://github.com/tushardhara/nightshift/issues/492#issuecomment-5502934678)
(**APPROVED as the mechanism for #492**, six numbered conditions, transcribed
into AC-1–AC-8 below). This spec does not re-litigate the mechanism; it only
turns the approval into checkable acceptance criteria and names one build
consideration the approval thread didn't need to address.

## 1. Problem statement

The live Omnigent server's Postgres connection URI, **including the plaintext
password**, is visible to any local user on the deployment host via `ps aux` or
`/proc/<pid>/cmdline`, because both of the repo's server-launch call sites
serialize an already-in-environment secret onto the child process's argv as a
literal `--database-uri <value>` flag:

- `deploy/run-exact-sha-server.sh:76` — `--database-uri "$OMNIGENT_DATABASE_URI" \`
  inside the `exec "$omnigent_bin" server ...` invocation (exact-SHA release
  launch path).
- `deploy/systemd/omnigent-server.service:22` — `--database-uri
  ${OMNIGENT_DATABASE_URI} \` inside `ExecStart=`; systemd expands the
  `EnvironmentFile=`-sourced variable directly into the literal command line
  before exec (systemd-managed launch path).

Both call sites already have the secret in the child's environment before this
point (`server_env` is sourced with `set -a; . "$server_env"; set +a` at the
script's top, and the systemd unit sources it via `EnvironmentFile=`) — the
gap is redundant re-serialization onto argv, not a missing delivery mechanism.

**Done when:** after a (re)start via *either* launch path, no `ps aux` /
`/proc/<pid>/cmdline` output for the omnigent server process contains the
connection URI or the `--database-uri` token, and the server is still
provably talking to the real configured Postgres database (not a silent
fallback to the CLI's default local sqlite store).

## 2. Mechanism (settled — not open for re-debate here)

Replace the literal `--database-uri <value>` argument at both call sites with
`-c <path-to-generated-yaml>`, where the YAML file contains one line,
`database_uri: <value>`, generated fresh immediately before each launch.
Confirmed against the installed `omnigent` v0.10.0 CLI (`omnigent server
--help`): `-c, --config PATH — Path to YAML config file`; resolution
precedence is CLI flag > config file > default, so a `-c` value together with
no `--database-uri` flag resolves, per that precedence order, to the config file's
`database_uri`, not to the sqlite default.

## 3. Acceptance criteria (AC-1–AC-8)

Each AC transcribes one condition from the approval thread into a checkable
form. Provenance: AC-1–AC-6 transcribe comment 5502934678's six numbered
conditions verbatim in effect; AC-7–AC-8 are this spec's own test/regression
requirements, added because the approval thread scoped *what* must be true,
not *how it stays true*.

- **AC-1** (atomicity, comment 5502934678 condition 1): the generated YAML
  file is created via a temp-write-then-rename (or equivalent atomic
  construction, e.g. `install -m 0600`) such that there is no window in which
  the file exists on disk with a mode more permissive than `0600`, and no
  window in which its content is readable by any principal other than the
  runtime user or root. A `open()`-then-`chmod()` sequence does not satisfy
  this AC.

- **AC-2** (location, condition 2): the generated file's path is under
  `~/.config/omnigent/` for the runtime user. It is never written to `/tmp`,
  never to `$PROJECT_DIR` / the release checkout, and never anywhere swept by
  `deploy/nightshift-state-backup.sh` (confirmed today: that script's sweep is
  `git add -- .omni docs`, `deploy/nightshift-state-backup.sh:42` — it does not
  touch `~/.config/omnigent/`, and this fix must not change that).

- **AC-3** (freshness, condition 3): the file is regenerated (written, not
  reused) on every server launch attempt — including every `Restart=always`
  retry of the systemd unit — from the current value sourced from
  `server.env` at that launch. No code path leaves a stale generated file
  from a previous launch in place and reads it instead of regenerating.

- **AC-4** (both call sites updated, condition 4): both
  `deploy/run-exact-sha-server.sh` and `deploy/systemd/omnigent-server.service`
  drop `--database-uri` entirely from their launch command and add `-c
  <generated-path>` in its place. `~/.config/omnigent/server.env`'s own
  format, location, and provisioning are unchanged — this fix only stops
  re-serializing a value already present in the child's environment; it does
  not change how the secret is provisioned. (Restated as AC-6/AC-7 of the
  hard-boundaries list below.)

- **AC-5** (post-launch verification, condition 5 — must be demonstrated, not
  assumed): after a (re)start via **each** launch path independently:
  1. `ps aux | grep omnigent` contains no `postgresql://` substring and no
     `--database-uri` token, for that launch's server process.
  2. `/proc/<pid>/cmdline` for that process (NUL-joined argv) contains no
     `postgresql://` substring and no `--database-uri` token.
  3. `stat -c '%a %U' <generated-yaml-path>` reports `600 <runtime-user>` at
     the moment the server is running (not just at the moment of creation).
  4. The server is actually serving against the real configured Postgres
     database — e.g. a request that touches session/catalog state succeeds
     and the data is visible via a direct `psql`/driver connection to the
     same Postgres instance, or an equivalent named check — not merely "the
     process started with exit code 0". A naive fix that just deletes the
     flag with no `-c` replacement would start cleanly and silently fall
     back to the CLI's default local sqlite store (per §2); AC-5.4 exists
     specifically to catch that failure mode.

- **AC-6** (rotation out of scope, condition 6): no credential rotation logic,
  tooling, or behavior is added anywhere in this change. The value written
  into the generated YAML is the same `OMNIGENT_DATABASE_URI` value
  `server.env` already holds today, verbatim.

- **AC-7** (`preflight.sh` unaffected): `deploy/preflight.sh`'s existing
  `OMNIGENT_DATABASE_URI` / `server.env` presence-and-shape checks continue to
  pass unchanged. This fix does not touch `deploy/preflight.sh`.

- **AC-8** (regression-proof, "tests that drive `deploy/*.sh`" seam per
  `docs/owners.md:20`): a new or extended test asserts, for **both** launch
  paths:
  1. The constructed argv (`run-exact-sha-server.sh`, driven the same way
     `tests/test_exact_sha_release.py`'s existing `_server_wrapper_fixture`
     drives it today) never contains a `--database-uri` token and never
     contains the literal database URI value anywhere in argv.
  2. The rendered `ExecStart=` line for `omnigent-server.service` (after
     template substitution, however the existing systemd-unit tests render
     it) never contains `--database-uri` or the literal URI.
  3. The generated config file, once produced by either path's generation
     step, has mode `0600` and was produced via the atomic
     construction required by AC-1 (e.g. assert the temp file no longer
     exists post-rename, or drive the generation helper directly and assert
     no intermediate `stat` ever shows a wider mode).
  A future edit to either launch site or the generation helper that
  reintroduces the flag, or that weakens the mode/atomicity, must fail this
  test.

## 4. Failure and empty states

- If `OMNIGENT_DATABASE_URI` is empty or unset: `run-exact-sha-server.sh`'s
  existing check (lines ~64–65, unchanged by this fix) already aborts with
  exit 3 and `"server environment missing OMNIGENT_DATABASE_URI"` before
  reaching the generation step. That behavior is preserved as-is.
- If the YAML-generation step itself fails for any reason (cannot create
  `~/.config/omnigent/`, disk full, permission denied on that directory,
  rename fails): the script must abort with a non-zero exit **before**
  `exec`, under `set -euo pipefail` — no swallowed failures, and no fallback
  to launching with the old `--database-uri` flag or with no config at all
  (either of those would either re-expose the credential or silently launch
  against the wrong/default database).
- For the systemd path: if the `ExecStartPre=` (or equivalent) generation
  step exits non-zero, systemd must not proceed to `ExecStart=` — this is
  systemd's default behavior for `ExecStartPre=` failure and must not be
  overridden (e.g. no `-` prefix that ignores the exit code). The unit then
  fails and is retried by the existing `Restart=always` / `RestartSec=10` /
  `StartLimitIntervalSec=0` posture (unchanged) — a failed generation attempt
  must be safe to retry indefinitely (idempotent: each retry regenerates from
  scratch per AC-3, never reads a partial file left by a prior failed
  attempt).
- Empty/malformed `database_uri` value must not be silently accepted into the
  generated YAML as an empty key (which would let the CLI's own
  default-sqlite fallback kick in unnoticed) — the existing non-empty check
  in `run-exact-sha-server.sh` covers that path; the systemd path's
  generation helper must apply the equivalent check before writing.

## 5. Out of scope (hard boundaries, carried over from the issue)

- **Do not rotate the credential.** No rotation logic, tooling, or scheduling
  anywhere in this change (AC-6).
- **Do not touch any live/real deployment.** Nothing in this item deploys to
  a real environment; the fix and its tests are code/config/test changes
  only.
- **Do not change** `deploy/bootstrap.sh`, `docker-compose.postgres.yml`, or
  `~/.config/omnigent/server.env`'s format or location. This fix stops
  re-serializing an already-provisioned env var onto argv; it does not
  change how the secret is provisioned.
- **tmpfs is not required.** 0600 + atomic creation (AC-1) is the approved
  bar for this threat model; do not gold-plate with a tmpfs requirement (the
  approval thread explicitly declined this as an unjustified escalation
  relative to `server.env`'s own accepted baseline).
- No broader secret-handling audit beyond these two call sites.

## 6. Dependencies

- None blocking. The mechanism is fully security-approved
  (issuecomment-5502934678) and the CLI behavior it depends on (`-c/--config`
  resolution order) is confirmed live against the installed `omnigent` 0.10.0
  binary in this workspace (§2), not merely asserted from documentation.
- **Build-order note, not a blocking dependency:** two existing tests in
  `tests/test_exact_sha_release.py` currently *assert the vulnerable
  behavior* and will need updating in the same PR as part of satisfying AC-8,
  or CI fails on an assertion this fix is supposed to falsify:
  - `tests/test_exact_sha_release.py:764` —
    `test_launch_wrapper_has_one_agent_root_and_no_global_stop` asserts
    `self.assertIn('--database-uri "$OMNIGENT_DATABASE_URI"', wrapper)`
    against the static script text.
  - `tests/test_exact_sha_release.py:1245`–`1263` —
    `test_server_wrapper_passes_database_uri_and_release_environment` runs
    the real wrapper against a fake `omnigent` binary and asserts
    `--database-uri` appears in the captured argv with the real URI as its
    value.
  Updating these two is part of the same change, not a separate item — they
  currently pin the exposure this issue exists to close.

## 7. Open questions

- **Shared vs. duplicated generation logic between the two call sites is an
  open design question for the build's design debate — explicitly NOT
  resolved here and NOT blocking DoR.** The charter for this item calls out
  that engineers should propose approaches (e.g. a shared helper under
  `deploy/` that both `run-exact-sha-server.sh` and the systemd unit's
  `ExecStartPre=` step call, vs. two independent implementations) during
  design debate. Either approach is acceptable as long as every AC above is
  met for both launch paths; this spec does not prescribe one.
- Everything else the approval thread raised was answered inline in that
  thread (see §2 and the AC provenance above) — no other open items.

## 8. Sizing and priority note (PM record, not part of the contract)

Sized **M** (a day or two): two launch-site edits, one new generation step/
helper (design open, §7), and new/extended test coverage across two existing
suites (`tests/test_exact_sha_release.py` for the script path, plus whatever
suite already covers systemd-unit rendering) to satisfy AC-8, plus the live
verification in AC-5 (which needs a real Postgres instance reachable the way
`docker-compose.postgres.yml` already provides for other deploy tests —
unchanged by this fix, just used for verification).

Priority **P1**: this is a live, currently-exploitable credential exposure on
the deployment host (any local user can read the DB password), flagged by the
owner via a board audit (#205) and already security-assessed and approved.
Placed at the top of crew_beta's queue, ahead of the #422/#524/#525
DoD-manifest cleanup items, which are P2.
