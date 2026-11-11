# Path ownership

Every path has exactly ONE owner. A crew touching a path it does not own asks the
program for a lease first; the program grants at most one lease per glob at a time.

| Path glob | Owner | Single-writer | Notes |
|---|---|---|---|
| `db/migrations/**` | platform | yes | program hands out the sequence number with the lease |
| lockfile (`package-lock.json`, `uv.lock`, …) | platform | yes | parallel resolution produces incompatible trees |
| `.github/workflows/**` | platform | yes | never changed mid-wave |
| lint / format / typecheck config | platform | yes | never changed mid-wave |
| shared libs / utils | platform | no | additive changes preferred, callers migrated deliberately |
| test fixtures / harness | platform | no | |
| `bin/nightshift-workspace` | platform | yes | the per-agent isolated-workspace tool (R-63/R-64, ADR-0009). Deliberately **not** crew_alpha despite sitting beside `nightshift-ctl`: `platform` added it and wrote every hardening commit on it (5 of 6 commits as of `5f86d7a`). The one crew_alpha commit is #454/ADR-0058, made while this row was missing and therefore taken without a lease — that write is the gap this row closes, not a claim of ownership. Architecturally it CONSUMES seam A rather than being part of it: it reads the seat→identity mapping out of `bin/nightshift-ctl` through the attested-loader pattern. crew_alpha asks for an EDIT lease, as it would for any platform path |
| `bin/nightshift-ctl` | crew_alpha (seam A) | yes | the coordination kernel; policies move with it |
| `deploy/policies/**` | crew_alpha (seam A) | yes | **exception**: a `deploy/` path owned by the control-plane crew. The policies are the enforcement half of the kernel — `worker_semaphore` and `pr_close_keyword_guard` are meaningless apart from ctl's tables |
| `docs/CONTROL-PLANE.md`, `docs/design/SYSTEM.md` | crew_alpha (seam A) | no | |
| tests that drive `bin/nightshift-ctl` | crew_alpha (seam A) | no | ownership by what the test DRIVES, not by filename |
| `deploy/**` _(except `policies/`)_ | crew_beta (seam B) | yes | provisioning, bootstrap, preflight, systemd units, watchdog, state backup |
| `tests/linux/**` | crew_beta (seam B) | no | |
| tests that drive `deploy/*.sh` | crew_beta (seam B) | no | same rule, other side |
| `docs/TRUST-BOUNDARY.md` | crew_beta (seam B) | yes | both seams have claims in it; the other crew asks the program for an EDIT lease rather than editing it. Section-ownership inside one file is a merge conflict with extra steps |
| `program/**` (agent configs, org prompts) | platform | yes | **no crew owns this.** Both architects agreed it is not a parallel seam; it is the org's own constitution, and an unreviewed edit is how a run silently weakens its own controls. Changes require an ADR |
| `docs/prd/**` | product | yes | the definition of done is revised through its changelog, never edited silently |
| `docs/architecture/**`, `docs/decisions/**` | **the authoring crew for its own seam's decisions; `program` for cross-seam calls** (was: `architect` — reassigned 2026-08-23, see R-27) | no | every ADR still goes to `staff` before a crew builds on it — that gate is unchanged and is what makes crew authorship safe. `architect` is a Codex seat and has been dead since issue #5, so this row named an agent that could not act; three ADR commits were authored against it before crew_beta flagged the contradiction rather than assuming |
| `docs/audit/**` | **the crew that captured the evidence** (RT records, baselines, captures) | no | added 2026-08-23 after crew_alpha observed this file asserts every path has exactly one owner while `docs/audit/**` had no row — it wrote there on the program's instruction and flagged the gap rather than assuming. Evidence records belong to whoever ran the commands; the RT id is allocated by `program` (WA-007) so the namespace stays single-authority |
| `.omni/**` | program | yes | run record; persisted off-box via the `omni/state` branch, never via `main` |
