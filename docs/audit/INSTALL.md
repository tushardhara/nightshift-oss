# INSTALL — deployment and install reliability

DoD condition 17 (`docs/prd/DEFINITION-OF-DONE.md` §D "Audits that end", condition 17;
`docs/prd/DOD-MANIFEST.json` condition `17`). Answers Q1–Q4, closes issue #481.

**Authored directly by the `crew_alpha` lead, not by a dispatched engineer — working
agreement 5 (`.omni/working-agreements.md` item 5), the standing WA-5 self-perform
fallback.** Cause, named explicitly per the owner's dispatch instruction for this task:
issue **#489** — `sys_session_send`'s own declared object-form schema
(`{additionalProperties: false, properties: {cost_budget, file_ids, input, model,
purpose}}`) has no `slot_id`/`task_id`/`generation` fields, while the root dispatch
policy `worker_semaphore` (`deploy/policies/omnipods_policies.py`) requires exactly
those fields on every `sys_session_send` call targeting a `COUNTED_AGENTS` entry
(`eng_claude`, `eng_codex`, `qa`, `reviewer_claude`, `reviewer_codex`) — confirmed live
against issue #474/PR #487 the same day this task was chartered, and named in #489's own
body as "very likely the actual root cause underlying the long run of WA-5 'self-perform'
fallbacks recorded across this repo's history." Issue **#490** is cited alongside it in
this task's dispatch instruction as further same-run evidence that this host's control
plane and served binaries are presently unreliable for a second-order redeploy/dispatch
loop; it does not itself block engineer dispatch, but corroborates that the run is
operating under known infrastructure degradation rather than a one-off fluke. Per WA-5
(`.omni/working-agreements.md` item 5): in-crew review and in-crew QA did not happen for
this document; cross-crew review (`crew_beta`) is not customary here, it is
**mandatory**, and is the sole independence check this PR gets.

Audit is documentation only: no code changed. Every command below was actually run,
today, against this exact worktree — nothing here is a paraphrase of a prior report.

**Reference commit for every citation below**, unless a citation states a different SHA
explicitly: `662600662ee17267aed75bb945556f9a40ae0e42` (this task's chartered base_sha,
branch `crew_alpha/481-install-audit`, a direct ancestor of `origin/main`). All `git`/
`python3`/`bash` commands were re-run live today (2026-09-01) in a fresh `git worktree`
cut from that exact commit, never the shared `/srv/project` tree.

Evidence types below follow the validator's rule
(`tests/dod_validate.py::_ALLOWED_EVIDENCE_TYPES`): `permalink` = path + symbol + SHA,
`command` = the exact command plus its captured result. Per `docs/audit/CONCURRENCY.md`'s
own documented convention (its file header, "a `\"type\": \"test\"`-shaped claim … is
instead cited as a `\"command\"` record that runs the named `python3 -m unittest` node
directly"), every `RT`-id citation below is a `command` record that actually executes
that test module, not an abstract `test`-typed reference — `docs/prd/DOD-MANIFEST.json`
is `product`-owned and this document does not mint or depend on new `RT` ids. `F-nnn`
finding ids are minted by *this* document (`docs/audit/FINDINGS.md`, issue #482, does not
exist as a file yet — #482 is open but unbuilt at the time of writing) in the `F-17x`
range specifically to avoid colliding with `docs/audit/PIPELINE.md`'s `F-101–F-104` and
`docs/audit/CONCURRENCY.md`'s `F-1`; reconcile, do not silently renumber, when #482 lands.

---

```json
[
  {
    "id": "Q1",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_go_want_version, nightshift_go_checksum", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 409-421 and 439-457 (helper defs) -- Bug 1 root cause: the old `curl ... | head -1 | sed` (site A, version resolution) and `curl ... | jq ... | head -1` (site B, checksum lookup) each hand an early-closing consumer to a producer under `set -o pipefail`; a perfectly valid first line still aborted the whole script before either function's own loud diagnostic could print. Fix: fetch to a variable/temp file and parse locally, owning the failure message inside the tested unit instead of a dead line of text after an unreachable substitution."},
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "go_want=... / go_have=... (inline Bug 1/Bug 2 comments)", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 232-249: 'Bug 1 site A: the old line piped curl into `head -1` under `pipefail`...' / 'Bug 2: a missing /usr/local/go/bin/go is a NORMAL state on a fresh box, not a reason to abort.' Fixed by commit 47c9096 ('provision: Go toolchain survives a fresh box - both pipefail sites and the missing-go abort'), merged via PR #22 (bf153143b2ecb431d7561717e6d5dd8233960c86), issue #8."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_go_installed_version", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 431-437 -- Bug 2 root cause: the pre-fix substitution returned non-zero for a plain absent binary and `set -e` exited before the `${go_have:-0}` comparison/install branch was ever reached. Fix distinguishes three outcomes deliberately: absent (exit 0, empty output) -> install; present+readable (exit 0, version) -> compare; present+unparseable (exit 1) -> treat as invalid and reinstall, never silently trusted."},
      {"type": "command", "cmd": "python3 -m unittest -v tests.test_go_toolchain.BothSitesFixed tests.test_go_toolchain.RT01VersionDiscoverySurvivesPipefail tests.test_go_toolchain.RT02ChecksumLookupSurvivesPipefail tests.test_go_toolchain.RT03MissingGoIsANormalState tests.test_go_toolchain.RT04UnresolvableVersionFailsLoudly", "result": "Ran 22 tests in 0.487s -- OK. Run live today against 6626006, not paraphrased: includes test_no_curl_pipes_into_an_early_closing_consumer, test_pipefail_is_not_disabled_anywhere, test_rt02_the_real_script_reaches_its_unverified_toolchain_refusal, test_rt03_the_real_script_installs_instead_of_aborting, test_rt04_the_real_script_reaches_its_loud_diagnostic -- each named test drives the REAL deploy/provision-ubuntu.sh / deploy/lib/deploy-lib.sh code, not a reimplementation."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_user_manager_ready, nightshift_user_manager_remediation", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 241-251 (ready) and 253-268 (remediation) -- Bug 3 root cause: `su - nightshift` yields a shell but no D-Bus session (`XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS` absent); the old bootstrap called `systemctl --user daemon-reload` blind and died under `set -euo pipefail` before the unit-enable lines ran, so the watchdog and state-backup timers were never enabled on the live box. Fixed by commit 658e6f2 ('deploy: establish the user-manager capability as root, probe it in bootstrap (RT-05/06/07)'), merged via PR #18 (59c8fc675a5a52bdb3d9cb4b0f00606164c9783c), issue #9. Split of duties (ADR reconciliation A11): provision-ubuntu.sh (root) establishes linger + starts the user manager + verifies the bus; bootstrap.sh only probes and fails with a named root-facing remediation, never reaching for sudo it does not have."},
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "loginctl enable-linger / systemctl start user@ / bus verification", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 371-397: enables linger, starts `user@${NS_UID}.service`, polls for `/run/user/${NS_UID}/bus` up to 5s, then verifies `systemctl --user show-environment` as the runtime user before bootstrap ever runs."},
      {"type": "command", "cmd": "python3 -m unittest -v tests.test_user_manager.RT05CapabilityAbsentButObtainable tests.test_user_manager.RT06CapabilityUnobtainable tests.test_user_manager.RT07Idempotent", "result": "Ran 9 tests in 0.256s -- OK. Run live today: test_rt05_no_sudo_is_ever_invoked, test_rt05_the_probe_precedes_every_mutation, test_rt06 (unobtainable -> non-zero + named remediation), test_rt07_second_run_succeeds_with_the_same_end_state (re-run is a no-op, not an error, not a duplicate)."},
      {"type": "permalink", "path": "deploy/nightshift-watchdog.sh", "symbol": "header (ADR-0002, issue #10, DoD condition 4) and the beat-show call", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 14-30 state the defect and fix in prose; line 377 (`\"$CTL\" --db \"$PLANE\" beat show --who program --max-age \"$max_age\"`) is the actual content-based read. Bug 4 root cause: liveness was decided from the newest mtime under `.omni/`, while `deploy/nightshift-state-backup.sh` writes `.omni/control-backup.json` every 30 minutes on its own timer regardless of Program health -- a dead Program stayed 'healthy' forever by mtime, and the watchdog kept renewing worker slots for it. Fixed by commit bdcdcfc ('watchdog: liveness by content, and it renews nothing (RT-08..RT-12)'), merged via PR #24 (89b4cb97e4c26bc33e2ab89228740e85d2dd8699), ADR-0002."},
      {"type": "command", "cmd": "python3 -m unittest -v tests.test_watchdog.RT08StaleDespiteBackupNoise", "result": "Ran 5 tests in 1.956s -- OK. Run live today: test_rt08_liveness_is_never_decided_from_mtime, test_rt08_backup_writes_do_not_suppress_the_alert, test_rt08_a_previous_boot_record_does_not_suppress, test_rt08_an_unreadable_control_plane_still_alerts, test_rt08_the_alert_carries_the_reason_code."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_bwrap_probe, nightshift_bwrap_diagnosis", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 170-179 (probe) and 186-195 (diagnosis) -- Bug 5 root cause: `command -v bwrap` reports present on stock Ubuntu 24.04 even though `kernel.apparmor_restrict_unprivileged_userns=1` (the 24.04 default) plus no shipped bwrap AppArmor profile means `bwrap --ro-bind / / /bin/true` exits non-zero -- bwrap is installed and CANNOT RUN, and every Codex seat died at its first tool call while preflight reported clean. Fixed by commit e4ae104 ('deploy: probe bwrap by function, not presence (RT-13/14/15)'), merged via PR #15 (8416cc08ad8d7468f2daddf5cdb8883e4cd39557), issue #5 / issue #12."},
      {"type": "permalink", "path": "deploy/preflight.sh", "symbol": "bwrap explicitly excluded from the presence-only 'binaries' loop", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 46-49 (presence loop over omnigent/claude/codex/node/tmux/git/gh/jq) vs. lines 176-189 (the separate functional bwrap block with its own inline comment: 'bubblewrap is deliberately NOT in the loop above. It is the one binary whose presence proves nothing'). A failure here is a FAIL, never a warn."},
      {"type": "permalink", "path": "docs/TRUST-BOUNDARY.md", "symbol": "bwrap AppArmor claim, retracted in place", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 66-77: the document itself states 'Until 2026-08-22 this file asserted that bubblewrap ships its own AppArmor profile on that release and therefore keeps working. It does not: ...' -- the false claim DoD condition 17's own spec names is retracted with its own history stated, not silently edited away."},
      {"type": "command", "cmd": "python3 -m unittest -v tests.test_bwrap_probe.RT13ProbeFailureFailsPreflight", "result": "Ran 1 test in 6.280s -- OK: test_rt13_broken_bwrap_is_a_preflight_failure_not_an_ok, run live today against the real preflight code path."}
    ]
  },
  {
    "id": "Q2",
    "verdict": "CONFIRMED-BUG",
    "evidence": [
      {"type": "command", "cmd": "grep -n \"| head\\\\|| tail -1\\\\|| sed -n '1p'\" deploy/*.sh deploy/lib/*.sh", "result": "Every remaining truncating-consumer pipeline outside the two already-fixed Bug-1 sites reads from a purely LOCAL producer with bounded output (`ps -eo args | grep -oE ... | head -1`, `df --output=avail | tail -1`, `pgrep -f ... | head -5`, version-string parsing of `gh --version`/`tailscale version`/`floci version`) -- none of these are network fetches that can SIGPIPE a remote server mid-response the way go.dev's version/checksum endpoints could. No OTHER unguarded early-closing-consumer network pipeline was found under `set -o pipefail`. SOUND on this sub-question."},
      {"type": "command", "cmd": "grep -n \"command -v \" deploy/*.sh deploy/lib/*.sh", "result": "Every remaining presence-only check (`omnigent`, `claude`, `codex`, `node`, `git`, `gh`, `jq`, `tmux`, `go`, `cargo`, `rustc`, `floci`, `rtk`, `nightshift-ctl`, `tailscale`, `docker`, `runuser`) is an ordinary binary with no kernel-gated presence-vs-function divergence comparable to bwrap's userns/AppArmor gap; deploy/preflight.sh's own inline comment (lines 176-178) states exactly this reasoning for treating bwrap alone as a functional probe. SOUND on this sub-question."},
      {"type": "permalink", "path": "deploy/bootstrap.sh", "symbol": "linger check/enable and nightshift_install_user_units call, both reading bare $USER", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 231-233 (`loginctl show-user \"$USER\"` / `loginctl enable-linger \"$USER\"`) and line 291 (`if ! nightshift_install_user_units \"$(id -u)\" \"$USER\"; then`) -- the gate that enables all eight systemd units, including the watchdog and state-backup timers Bugs 3/4 depend on. The script declares `set -euo pipefail` at line 8. **F-171.**"},
      {"type": "command", "cmd": "env -u USER -u LOGNAME bash -c 'set -euo pipefail; loginctl enable-linger \"$USER\" 2>/dev/null || echo WARN; echo after'", "result": "stderr: 'bash: line 1: USER: unbound variable'; stdout stops after 'before' is never even reached in this snippet -- exit 1, 'after' NEVER printed. Run live today, hermetic (no host mutation: the unbound-variable expansion aborts before `loginctl` is ever invoked)."},
      {"type": "command", "cmd": "env -u USER -u LOGNAME bash -c 'set -euo pipefail; my_func() { echo \"called with $2\"; return 0; }; echo before; if ! my_func \"$(id -u)\" \"$USER\"; then echo denied; fi; echo after'", "result": "stdout: 'before' only, then stderr 'bash: line 1: USER: unbound variable', exit 1 -- 'after' never printed. Proves the abort fires even though the read is the CONDITION of an `if`: bash's `set -e` suppression inside an `if` condition does NOT extend to `set -u`'s unbound-variable abort, so line 291's `if !` wrapper does not protect it."},
      {"type": "permalink", "path": "deploy/preflight.sh", "symbol": "runtime_user derivation, issue #121's prior fix for the identical class", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 30-35: 'The runtime user is derived from the effective uid, NOT from $USER. ... `id -un` still answers correctly there, $USER aborts the script (#121). $USER/$LOGNAME remain as fallbacks only for the case where `id` is unavailable.' Commit 57375c98474ef2b015b889bc34410ed85883c8a0 (issue #121) fixed exactly this class in preflight.sh, for the stated reason that 'seats that run preflight unattended (release, cron, systemd units) carry no USER entry in their environment.' deploy/bootstrap.sh's two sites above were never given the same treatment."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_user_manager_remediation already defends the identical read", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "line 255: `[ -n \"$user\" ] || user=\"${USER:-$(id -un)}\"` -- the library function bootstrap.sh calls into for its diagnosis already falls back to `id -un`, one call frame away from the two unguarded reads this finding cites. The safe pattern exists in the same file's problem space and was not applied at the call sites."},
      {"type": "permalink", "path": "deploy/README.md", "symbol": "the one documented invocation is a login shell", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 152 and 427: both documented paths are `sudo -iu nightshift` followed by `/srv/nightshift/deploy/bootstrap.sh` -- `sudo -iu` is a login shell and always sets $USER, so F-171 is a LATENT instance on the one path this project actually tells an operator to use, stated with the same honesty the project's own 2026-08-22 Bug-1-site-B changelog entry (docs/prd/DEFINITION-OF-DONE.md line 579) applied to a latent-vs-reachable distinction: not reproducible via the documented path, but nothing in bootstrap.sh, deploy/README.md, or tests/linux/install-rehearsal.sh forbids the alternate invocations (`runuser -u nightshift -- bash bootstrap.sh`, a systemd `ExecStartPre=`, a non-interactive CI/rehearsal harness) that DO reach it."}
    ],
    "findings": ["F-171"]
  },
  {
    "id": "Q3",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "header claim + runtime-user creation guard", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "line 6: 'Run ONCE as root (re-running is safe -- everything is idempotent)'. line 67: `if ! id -u \"$NS_USER\" >/dev/null 2>&1; then useradd ...; else echo \"  $NS_USER exists\"; fi` (else branch at line 71) -- a second run does not attempt to recreate an existing user."},
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "go toolchain re-run behaviour and workspace ownership drift-only fix", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 244-249: only downloads/reinstalls Go when `$go_have` is empty or below `$go_want`; symlinks use `ln -sfn` (idempotent overwrite, line 278-279). lines 360-364: `install -d -o ... /srv/nightshift /srv/project` plus `[ \"$(stat -c %U \"$d\")\" = \"$NS_USER\" ] || chown -R ...` -- ownership is fixed only when it has actually drifted, never an unconditional chown of a live tree."},
      {"type": "permalink", "path": "deploy/bootstrap.sh", "symbol": "header claim, guarded compose-file install, guarded PYTHONPATH append", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "line 6: 'Idempotent -- safe to re-run after an upgrade.' lines 168-170: `install -m 0644 ...` (not `cp -n`) specifically 'so an upgraded compose file must propagate on re-run, or \"idempotent -- safe to re-run after an upgrade\" is false for this file' -- the file's own comment states the contract and the choice of primitive honors it. lines 197-200: `if ! grep -q '^PYTHONPATH=' \"$ENVFILE\"; then ... fi` -- a second run does not duplicate the line."},
      {"type": "command", "cmd": "python3 -m unittest -v tests.test_user_manager.RT07Idempotent", "result": "Ran 1 test in 0.105s -- OK: test_rt07_second_run_succeeds_with_the_same_end_state asserts a second `nightshift_install_user_units` call exits 0 and leaves the enabled-unit set unchanged, run live today against the real function."},
      {"type": "permalink", "path": "deploy/bootstrap.sh", "symbol": "installer attestation is deliberately refreshed, not append-only, and the comment states why", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 267-268 (comment) and 284 (`date +%s > \"$NIGHTSHIFT_STATE_DIR/bootstrapped-at\"`): 'Refreshed on every run, deliberately: bootstrap is a manual, idempotent operator action, and on a re-run \"the installer ran just now\" is simply true.' A deliberate, documented exception to byte-for-byte idempotency, not a silent violation of it -- the watchdog's discrimination between a fresh box and a restored control plane depends on this file being freshest-write, not unwritten-after-first-run."},
      {"type": "permalink", "path": "docs/audit/INSTALL.md", "symbol": "cross-reference to F-171, scoped out of this verdict", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "F-171 (Q2) is an ABORT hazard under an environment precondition (missing $USER), not an idempotency violation as RT-07 defines it -- a script that fails identically both times is not what Q3 asks about, and RT-07's own re-run assertion passed cleanly above. Recorded once, under Q2, not double-counted here."}
    ]
  },
  {
    "id": "Q4",
    "verdict": "SOUND",
    "evidence": [
      {"type": "permalink", "path": "deploy/provision-ubuntu.sh", "symbol": "checksum failure diagnostics", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "line 269: 'no published sha256 for ${go_file} -- refusing to install an unverified toolchain'; line 274: 'checksum mismatch for ${go_file} -- download corrupted or tampered with' -- both name the cause; neither installs on ambiguity."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_go_want_version's unresolvable-version diagnostic", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 417-418: names the exact override and the exact re-run command -- 'Pin it explicitly and re-run:  NIGHTSHIFT_GO_VERSION=x.y.z deploy/provision-ubuntu.sh'."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh", "symbol": "nightshift_bwrap_diagnosis -- every branch names cause AND an exact remediation command", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "lines 186-195: code 2 (missing) names 're-run deploy/provision-ubuntu.sh as root'; code 3 (no runuser) names 'apt install util-linux, then re-run'; the default/non-zero-functional branch names the exact `/etc/apparmor.d/bwrap` content, the `apparmor_parser -r` command, AND a verification command with its expected printed output (`bwrap --ro-bind / / /bin/true; echo $?` -> 0)."},
      {"type": "permalink", "path": "deploy/lib/deploy-lib.sh, deploy/provision-ubuntu.sh", "symbol": "user-manager unreachable diagnostics, both root-facing and runtime-facing", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "deploy-lib.sh lines 253-268 (`nightshift_user_manager_remediation`) names the exact root commands; deploy/provision-ubuntu.sh lines 393-397 (its own WARN when the live bus check fails) names `systemctl status user@${NS_UID}.service` and `loginctl show-user $NS_USER` verbatim, so the operator has an exact command even when provisioning itself could not confirm success."},
      {"type": "permalink", "path": "deploy/nightshift-watchdog.sh", "symbol": "alert bodies name the ADR, the issue, and what is/is not proven", "sha": "662600662ee17267aed75bb945556f9a40ae0e42", "detail": "line 645 (`ctl_lacks_beat` reason): names issue #42 and the exact remediation ('Point NIGHTSHIFT_CTL at the ctl from the same checkout as this script, or re-render the unit'). lines 681-683: states the verdict is content-based (not mtime, not STATUS.md) AND names the still-open follow-up (issue #26) for the one thing it admits it cannot yet distinguish (finished vs. dead)."},
      {"type": "command", "cmd": "grep -n \"Remediation\\|remediation\\|re-run deploy\" deploy/lib/deploy-lib.sh", "result": "every non-success diagnostic function in the toolchain/bwrap/user-manager helpers (nightshift_go_want_version, nightshift_bwrap_diagnosis, nightshift_user_manager_remediation) contains the word 'remediation' or an exact re-run command inline, run live today against 6626006 -- confirms the pattern is uniform across all three helper groups, not cited selectively."}
    ]
  }
]
```

## Q1 — root cause of each of the five confirmed install/liveness bugs, commit-qualified

All five are real, independently reproducible right now against this exact commit
(`662600662ee17267aed75bb945556f9a40ae0e42`), and each has a distinct fix commit — see the
`## See it for yourself` section below for the exact command per bug.

**1. Go version discovery, both sites (`curl | head -1` / `curl | jq | head -1` under
`set -o pipefail`).** An early-closing consumer (`head -1`) can receive SIGPIPE-adjacent
truncation from its producer even when the producer's data was entirely valid, and under
`pipefail` the whole pipeline reports non-zero — which under `set -e` aborted the script
*before* the intended "refusing to install an unverified toolchain" diagnostic could ever
print. Site A (version resolution) is reproducibly abortable with real data; site B
(checksum lookup) is a **latent** instance — `jq` reads the fetched file to EOF rather than
streaming from `curl`, so `curl` itself never SIGPIPEs there, but the SAME consequence (the
refusal diagnostic being killed by `set -e` before it prints) is reachable on any failed
request. Fixed by commit `47c9096` (PR #22, merge commit `bf153143b2ecb431d7561717e6d5dd8233960c86`,
issue #8): `nightshift_go_want_version` / `nightshift_go_checksum`
(`deploy/lib/deploy-lib.sh:409,448`) fetch to a variable/temp file, parse locally, and own
their own loud failure message. The structural closure of site B specifically (proving no
network producer anywhere feeds an early-closing consumer, and that `pipefail` is not
disabled to dodge the problem) is `tests.test_go_toolchain.BothSitesFixed`.

**2. A missing Go toolchain must mean "install," never "stop."** `nightshift_go_installed_version`
(`deploy/lib/deploy-lib.sh:431`) previously propagated a non-zero exit for a plain absent
binary, which under `set -e` skipped the `${go_have:-0}` fallback comparison entirely and
aborted provisioning on a perfectly normal fresh-box state. Fixed in the same commit
`47c9096`: absence is now an explicit `exit 0, empty output` state, reserving non-zero for
a present-but-unparseable binary (an install the script correctly refuses to trust).

**3. Unattended user services must not assume `systemctl --user` already works.**
`su - nightshift` produces a shell with no D-Bus session (`XDG_RUNTIME_DIR` and
`DBUS_SESSION_BUS_ADDRESS` are simply absent); the pre-fix bootstrap called
`systemctl --user daemon-reload` blind, and under `set -euo pipefail` that died before the
`enable --now` lines that arm the watchdog and state-backup timers — so a provisioned box
had no dead-man's switch and no off-box state, silently. Fixed by commit `658e6f2` (PR #18,
merge commit `59c8fc675a5a52bdb3d9cb4b0f00606164c9783c`, issue #9), split per ADR
reconciliation A11: `deploy/provision-ubuntu.sh` (root, lines 371-397) establishes linger,
starts the user manager, and verifies the bus is reachable; `deploy/bootstrap.sh` only
*probes* (`nightshift_user_manager_ready`) and fails with a named root-facing remediation
(`nightshift_user_manager_remediation`) — it never reaches for a `sudo` the runtime account
does not have by design.

**4. Watchdog liveness must be a Program-owned signal, judged by content.** The watchdog
used to decide liveness from the newest mtime under `.omni/`, while
`deploy/nightshift-state-backup.sh` writes `.omni/control-backup.json` on its own 30-minute
timer regardless of whether the Program is alive — so a dead Program stayed "healthy"
forever by mtime, and the watchdog kept renewing worker slots for a Program that was not
actually running. Fixed by commit `bdcdcfc` (PR #24, merge commit
`89b4cb97e4c26bc33e2ab89228740e85d2dd8699`, ADR-0002): `deploy/nightshift-watchdog.sh:377`
now reads liveness through `nightshift-ctl beat show --who program --max-age <n>`, which
judges a written record's *content* (schema version, run id, boot id, monotonic sequence,
phase) — never a file timestamp, never a `STATUS.md` substring — and the watchdog itself
renews nothing.

**5. Bubblewrap must be probed by function, not by presence.** `command -v bwrap` reports
present on stock Ubuntu 24.04, but with `kernel.apparmor_restrict_unprivileged_userns=1`
(the 24.04 default) and no shipped bwrap AppArmor profile, `bwrap --ro-bind / / /bin/true`
exits non-zero — bwrap is installed and **cannot run**, and every Codex seat died at its
first tool call while a presence-only preflight reported clean. Fixed by commit `e4ae104`
(PR #15, merge commit `8416cc08ad8d7468f2daddf5cdb8883e4cd39557`, issue #5 / issue #12):
`nightshift_bwrap_probe` / `nightshift_bwrap_diagnosis` (`deploy/lib/deploy-lib.sh:170,186`)
replace the presence check; `deploy/preflight.sh` explicitly excludes bwrap from its
generic "binaries" loop (lines 46-49 vs. 176-189) and treats a probe failure as a hard FAIL,
never a warn. `docs/TRUST-BOUNDARY.md`'s prior false claim that bubblewrap "ships its own
AppArmor profile" on 24.04 is retracted in place, with its own retraction history stated
(lines 66-77) rather than silently edited away.

## Q2 — what else depends on a SIGPIPE-able pipeline, an interactive session, a login
shell, a presence-only capability probe, or a file a fresh box may not have?

Three sub-questions were surveyed directly against the current tree; two came back SOUND
and one surfaced a real, new, commit-qualified finding.

**Pipelines that can SIGPIPE:** every other truncating-consumer pipeline in `deploy/*.sh`
and `deploy/lib/deploy-lib.sh` reads from a bounded LOCAL producer (`ps`, `df`, `pgrep`,
version-string parsing of already-installed binaries) — none is a remote fetch that can
truncate mid-response the way go.dev's endpoints could. **SOUND.**

**Capabilities probed by presence rather than function:** bwrap remains the only binary
whose OS-level presence provably diverges from its ability to run; every other
`command -v` check in the tree (CLIs, language toolchains, `nightshift-ctl`, `tailscale`,
`docker`) has no comparable kernel-namespace gate. **SOUND**, and `deploy/preflight.sh`'s
own inline comment states this reasoning explicitly rather than leaving it implicit.

**Login shell / interactive session — CONFIRMED-BUG, F-171.** `deploy/bootstrap.sh`
(`set -euo pipefail`, line 8) reads bare `"$USER"` at two sites: the linger check/enable
(lines 231-233) and the entire `nightshift_install_user_units "$(id -u)" "$USER"` gate
(line 291) — the call that enables all eight systemd units, including the watchdog and
state-backup timers Bugs 3 and 4 depend on. If `$USER` is absent from the process
environment, bash's `set -u` raises `USER: unbound variable` and the whole script
terminates the instant that line is reached — reproduced live above, hermetically, with no
host mutation. This is the *identical defect class* already fixed once in this codebase:
commit `57375c98474ef2b015b889bc34410ed85883c8a0` (issue #121) rewrote
`deploy/preflight.sh` to derive the runtime user from `id -un` specifically because
"seats that run preflight unattended (release, cron, systemd units) carry no `USER` entry
in their environment," and `nightshift_user_manager_remediation`
(`deploy/lib/deploy-lib.sh:256`) already defends the identical read with
`${USER:-$(id -un)}` one call-frame away. `bootstrap.sh`'s two direct reads were never
given the same treatment.

**Stated with the same honesty the project's own Bug-1-site-B finding required (WA-008):**
the one documented invocation — `sudo -iu nightshift` then `/srv/nightshift/deploy/bootstrap.sh`
(`deploy/README.md:152,427`) — is itself a login shell and always sets `$USER`, so this is
presently a **latent** instance on that one path, not a live-reproduced failure of the
recommended flow. It becomes live the moment `bootstrap.sh` is invoked any other way this
project does not forbid — `runuser -u nightshift -- bash bootstrap.sh`, a systemd
`ExecStartPre=`, or a non-interactive CI/rehearsal harness driving it directly — and
nothing in `bootstrap.sh`, `deploy/README.md`, or `tests/linux/install-rehearsal.sh`
states that a login shell is a precondition for running it.

**A file a fresh box may not have:** not separately surveyed beyond what Q1/Q3's file
checks already cover (guarded `install -m`/`mkdir -p` calls throughout); no further gap
found in that pass, and none is claimed beyond what was actually checked.

## Q3 — are `provision-ubuntu.sh` and `bootstrap.sh` idempotent against their own header
claims, over the paths this project touched?

**SOUND**, scoped exactly as condition 17 states — "scoped to what we changed, not a
blanket rewrite" — i.e. the Go toolchain, bwrap, and user-manager/unit-installation paths.
`provision-ubuntu.sh` guards user creation (`id -u` check before `useradd`), re-downloads
Go only when the installed version is absent or stale, uses `ln -sfn` for its symlinks, and
fixes workspace ownership only when it has actually drifted rather than unconditionally
`chown -R`-ing a live tree. `bootstrap.sh` uses `install` (not `cp -n`) for its compose
file specifically so an upgrade propagates on re-run — its own comment states the contract
in those words — guards its `PYTHONPATH` append against duplication, and
`nightshift_install_user_units`'s re-run behaviour is directly asserted by
`RT07Idempotent`, run live above: a second call produces the same enabled-unit set with
exit 0. The one deliberate exception, the installer-attestation file being refreshed (not
append-only) on every run, is documented in place as intentional and is exactly what the
watchdog needs to discriminate a fresh install from a restored control plane — a stated
exception, not a silent violation. F-171 (Q2) is an abort hazard gated on an environment
precondition, not an idempotency violation as `RT07` defines it, and is not double-counted
here.

## Q4 — do failure paths give actionable diagnosis plus a remediation reference, and an
exact command wherever one exists?

**SOUND**, over the failure paths adjacent to the five confirmed bugs plus the
docker/Postgres bring-up step (this document's scope; ordinary `apt-get` failures inside
`provision-ubuntu.sh` surface apt's own diagnostics, unchanged by this project, and were
not separately re-audited). Every failure path checked names both the cause and an exact
remediation: the Go checksum/refusal messages name the exact re-run command with the
override variable spelled out; `nightshift_bwrap_diagnosis` names a different exact
command per exit code, including a verification command with its expected printed output;
`nightshift_user_manager_remediation` and `provision-ubuntu.sh`'s own bus-verification WARN
both name the exact `systemctl status` / `loginctl show-user` commands an operator would
actually run; and the watchdog's alert bodies name the governing ADR/issue plus, where a
property is not yet provable (finished-vs-dead), say so explicitly rather than overclaiming.

## See it for yourself

Every command below was run live today, from a fresh `git worktree` cut at
`662600662ee17267aed75bb945556f9a40ae0e42`, on this exact host, with only `bash` and
`python3` (plus, for the full rehearsal, `curl`/`bwrap`/`git`, all already present here).
None of them mutate `/srv/nightshift` or `/srv/project` — the rehearsal command below is
pointed at a throwaway path via `NIGHTSHIFT_REHEARSAL_TEAMS_DIR` for exactly that reason.

**Bug 1 — Go version discovery survives `set -o pipefail`, both sites:**
```
python3 -m unittest -v tests.test_go_toolchain.BothSitesFixed tests.test_go_toolchain.RT01VersionDiscoverySurvivesPipefail tests.test_go_toolchain.RT02ChecksumLookupSurvivesPipefail
```
Real output today: `Ran 11 tests in 0.942s — OK` (subset of the 22-test run captured in
Q1's evidence above).

**Bug 2 — a missing Go toolchain installs, it never aborts:**
```
python3 -m unittest -v tests.test_go_toolchain.RT03MissingGoIsANormalState tests.test_go_toolchain.RT04UnresolvableVersionFailsLoudly
```
Real output today: `Ran 11 tests in 0.435s — OK`.

**Bug 3 — unattended user services install without assuming `systemctl --user` already
works:**
```
python3 -m unittest -v tests.test_user_manager.RT05CapabilityAbsentButObtainable tests.test_user_manager.RT06CapabilityUnobtainable tests.test_user_manager.RT07Idempotent
```
Real output today: `Ran 9 tests — OK`.

**Bug 4 — watchdog liveness is judged by content, never mtime:**
```
python3 -m unittest -v tests.test_watchdog.RT08StaleDespiteBackupNoise
```
Real output today:
```
test_rt08_a_previous_boot_record_does_not_suppress ... ok
test_rt08_an_unreadable_control_plane_still_alerts ... ok
test_rt08_backup_writes_do_not_suppress_the_alert ... ok
test_rt08_liveness_is_never_decided_from_mtime ... ok
test_rt08_the_alert_carries_the_reason_code ... ok

Ran 5 tests in 1.956s

OK
```

**Bug 5 — bubblewrap is probed by function, not presence:**
```
python3 -m unittest -v tests.test_bwrap_probe.RT13ProbeFailureFailsPreflight
```
Real output today: `Ran 1 test in 6.280s — OK`.

**All four install-path bugs (1, 2, 3, 5) together, against the REAL scripts end to end —
the DoD condition 11 install rehearsal, pointed at a scratch path so it never touches this
host's real `/srv/nightshift`:**
```
NIGHTSHIFT_REHEARSAL_TEAMS_DIR=/tmp/rehearsal-fresh-$$ bash tests/linux/install-rehearsal.sh
```
Real output today:
```
=== clean-source extraction: git archive 662600662ee17267aed75bb945556f9a40ae0e42 (never a bind mount) ===
PASS: clean-source extraction: 662600662ee17267aed75bb945556f9a40ae0e42 extracted via git archive into an empty workspace

=== pre-install assertion: /tmp/rehearsal-fresh-1141122 must be absent ===
PASS: /tmp/rehearsal-fresh-1141122 is absent -- starting from an unprovisioned state

=== condition 1: Go version discovery survives 'set -o pipefail' (both sites) ===
PASS: condition 1 site A: version resolution reachable, resolved '1.27.0' via 'curl'
PASS: condition 1 site B: checksum lookup for an unresolvable filename returned non-zero to its caller (did not abort the script)

=== condition 2: a missing /usr/local/go/bin/go is a NORMAL state, not an abort ===
PASS: condition 2: absent go binary reported as 'not installed' (empty, exit 0) -- proceeds to install, does not abort

=== condition 3: user-manager capability, established without an inherited login session ===
PASS: condition 3: systemd --user is reachable in this environment (derived bus env, no inherited session needed)

=== condition 5: bwrap probed by FUNCTION, not presence ===
PASS: condition 5: bwrap functional probe: 'bwrap --ro-bind / / /bin/true' exits 0

install-rehearsal: all decision points reached and passed (fail=0)
```

**F-171 (Bug-shaped gap this audit found, login-shell dependency in `bootstrap.sh`) —
hermetic, no host mutation:**
```
env -u USER -u LOGNAME bash -c 'set -euo pipefail; loginctl enable-linger "$USER" 2>/dev/null || echo WARN; echo after'
```
Real output today: `bash: line 1: USER: unbound variable` on stderr, exit 1, `after` never
printed — demonstrating the exact abort `deploy/bootstrap.sh:232` and `:291` would hit if
invoked with `$USER` unset, without ever calling `loginctl` for real.
