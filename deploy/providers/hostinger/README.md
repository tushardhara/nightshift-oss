# Hostinger VPS provider notes

Current primary: **Hostinger KVM 8, Mumbai — Ubuntu 24.04, 8 vCPU, 32 GB RAM,
400 GB NVMe.** Hostinger is where we happen to run today, not an architectural
dependency: everything except this page's firewall/console specifics is the
generic flow in [`deploy/README.md`](../../README.md).

Two Hostinger realities shape the security posture:

- the VPS has a **public IP** and no AWS-style default-deny security group, so
  the Ubuntu box itself (plus Hostinger's firewall) must enforce "nothing
  public";
- there is no cloud-init user-data path in the ordinary flow — you get root SSH
  and run the generic provisioning yourself. (32 GB does **not** raise the
  worker budget — the vendor subscriptions are still the constraint. It buys
  headroom: bootstrap derives `MemoryHigh=24G` / `MemoryMax=28G` for the host
  cgroup on this machine.)

## 1. Create the VPS

In hPanel: Ubuntu 24.04 LTS (plain, no template/panel), set the root password
or SSH key. Note the public IP.

## 2. First (and last) public SSH session

```bash
ssh root@<public-ip>
```

This public SSH session exists for provisioning only — it gets closed in
step 6.

```bash
# get the bundle onto the box (either):
git clone <nightshift-bundle-repo> /tmp/nightshift
# or, from your laptop: rsync -a ~/.nightshift/ root@<public-ip>:/tmp/nightshift/

# generic provisioning (idempotent): packages, docker, node 22, go, rust,
# floci (local cloud emulators), tailscale, the nightshift user,
# /srv/nightshift, /srv/project, journald, disk guard, swap
bash /tmp/nightshift/deploy/provision-ubuntu.sh

# bundle into place, owned by the runtime user
rsync -a --chown=root:root /tmp/nightshift/ /srv/nightshift/
```

## 3. Tailscale (still root)

```bash
tailscale up --ssh           # browser auth; --ssh enables Tailscale SSH
```

One-time: `--ssh` persists in tailscaled state across reboots. The UI is NOT
published to the tailnet here — see step 5. The `nightshift` user is deliberately **not** made a tailscale
operator — every worker runs as `nightshift`, and operator rights would let a
compromised worker reconfigure the tailnet (including `tailscale funnel`,
which is PUBLIC). Tailscale management stays root-only, structurally.

Verify from your laptop **before** touching the firewall:

```bash
ssh nightshift@<machine>.<tailnet>.ts.net   # Tailscale SSH — no key setup needed
```

## 4. Bootstrap + credentials (as `nightshift`)

```bash
su - nightshift              # from the root session — or log in fresh over Tailscale SSH
/srv/nightshift/deploy/bootstrap.sh
```

Then the interactive credentials (subscriptions are OAuth, not keys):

```bash
claude setup-token           # Claude subscription; paste the browser code
codex login                  # Codex subscription; device auth
gh auth login                # scope: repo only — NOT admin
gh auth refresh -s project   # Projects board scope
```

And the project:

```bash
git clone <repo> /srv/project
/srv/nightshift/bin/nightshift-init.sh /srv/project
/srv/nightshift/bin/nightshift-tracker-init.sh /srv/project
$EDITOR /srv/project/.omni/brief.md
```

## 5. The UI — loopback by default, never a public port

The omnigent server binds `127.0.0.1:6767` and stays there. It is also the
control API, and it has **no authentication** (`deploy/README.md`, "Control
API"). **Never** open 6767 in any firewall, never rebind it, never
`tailscale funnel` it (Funnel is public).

Default: reach the UI through Tailscale SSH,
`ssh -L 6767:127.0.0.1:6767 nightshift@<machine>`, then `http://127.0.0.1:6767`.
Publishing it tailnet-wide (`NIGHTSHIFT_TAILSCALE_SERVE=1`, or
`tailscale serve --bg 6767` from a root shell) is opt-in, and only with the
owner-only Tailscale ACL from `deploy/README.md`. Preflight's "no serve proxy"
warning is expected on a default box.

## 6. Lock the public IP down

Only now — Tailscale SSH verified working in step 3.

**Hostinger firewall (hPanel → VPS → Firewall):** create a firewall, add no
accept rules (default drop covers inbound), or at most a temporary SSH rule
restricted to your own IP. Tailscale needs **no** inbound rule — it connects
outbound. Do not add rules for 6767, 5433, or 5432, ever.

**Ubuntu firewall (defense in depth, survives a firewall-config mistake in
hPanel):**

```bash
sudo ufw allow in on tailscale0
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable        # keep your current session open; verify a NEW
                       # Tailscale SSH session connects before logging out
```

Note: `sudo` here means a root session or an admin user — the `nightshift`
runtime user deliberately has no sudo. Use the hPanel **browser console** as
the out-of-band recovery path if you ever firewall yourself out.

Optionally also stop sshd answering publicly (`systemctl disable --now ssh` as
root) — Tailscale SSH does not use sshd. Leaving sshd running but firewalled is
fine too and keeps a console-recovery path; `fail2ban` (installed by
provisioning) covered the window while port 22 was public.

## 7. Preflight, start, verify reboot

```bash
/srv/nightshift/deploy/preflight.sh /srv/project
systemctl --user start omnigent-server omnigent-host
```

Start the run per `deploy/README.md` → "Starting the run".

Then prove reboot persistence once, before trusting the box with a multi-day
run:

```bash
sudo reboot   # from a root/admin session
# after it comes back, over Tailscale SSH as nightshift:
systemctl --user is-active omnigent-server omnigent-host        # both active
systemctl --user list-timers | grep nightshift                  # backup + watchdog
docker ps                                                       # postgres up
curl -sf http://127.0.0.1:6767/health && echo ok                # server answering
```

(If you opted in to Serve: `curl -sf https://$(hostname).<tailnet>.ts.net/health`
from a tailnet device the ACL admits; `tailscale serve status` needs a root shell.)

All four come back without a login because provisioning enabled linger and the
units are `WantedBy=default.target`; an opted-in Serve persists because
`--bg` writes tailscaled state.

## 8. Recovery

Enable Hostinger's VPS snapshots/backups in hPanel if the plan includes them —
that gives you the cheap tier-3 path (restore snapshot, reboot, resume). The
floor that never depends on Hostinger is the generic tier 4:

**new Ubuntu VPS → `provision-ubuntu.sh` → clone repo → restore `omni/state` →
re-auth Claude/Codex/GitHub → bootstrap + preflight → resume the Program** —
verbatim commands in `deploy/README.md` → "Tier 4". The
`nightshift-state-backup.timer` pushes `.omni/` + `docs/` + the control-plane
JSON dump to the `omni/state` branch every 30 minutes, so a dead VPS costs at
most 30 minutes of file state plus in-flight turns.

## One-time manual actions checklist

| # | Action | Where |
|---|---|---|
| 1 | create VPS, Ubuntu 24.04 | hPanel |
| 2 | `provision-ubuntu.sh` | root SSH |
| 3 | `tailscale up --ssh` (Serve only if you opt in, with the ACL) | root SSH |
| 4 | `claude setup-token`, `codex login`, `gh auth login/refresh` | as nightshift |
| 5 | hPanel firewall + ufw lockdown | hPanel + root |
| 6 | enable VPS snapshots (if plan has them) | hPanel |
| 7 | reboot verification (step 7 above) | once |
