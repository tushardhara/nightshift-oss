# AWS EC2 provider notes

Everything about running Nightshift lives in the generic guide,
[`deploy/README.md`](../../README.md) — bring-up, credentials, security
posture, Tailscale Serve, recovery. This file covers only what is genuinely
AWS: launch mechanics, the security group, IAM, and EBS.

## Box

| | |
|---|---|
| AMI | Ubuntu 24.04 LTS |
| Type | `m7i.xlarge` (4 vCPU / 16 GB) — or `m8g.xlarge` arm64 for ~20% less |
| Disk | 100 GB gp3 (this is the "persistent VPS/local volume" of the generic guide) |
| Security group | **no inbound rules at all** |
| IAM role | none, unless the project itself calls AWS — and then every worker can read it (see Credentials) |
| Instance metadata | IMDSv2 only (`HttpTokens=required`), hop limit 1; disabled after provisioning when no role is attached |
| Cost | ≈$150–190/mo instance + ~$8 EBS, running 24/7 |

## Launch

```bash
aws ec2 run-instances \
  --image-id <ubuntu-24.04-ami> --instance-type m7i.xlarge \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=100,VolumeType=gp3}' \
  --user-data file://cloud-init.yaml \
  --metadata-options 'HttpEndpoint=enabled,HttpTokens=required,HttpPutResponseHopLimit=1' \
  --key-name <your-key> --security-group-ids <sg-with-no-inbound>
```

`HttpTokens=required` turns off IMDSv1, which hands the instance role's
credentials to any process that sends one unauthenticated GET (including an
SSRF in project code). Hop limit 1 keeps the token away from Docker containers.
Neither stops a worker: workers are host processes, so whatever role IMDS
serves, every worker can read. cloud-init needs IMDS at first boot; once the
box is provisioned and no role is attached, turn it off entirely:

```bash
aws ec2 modify-instance-metadata-options --instance-id <id> --http-endpoint disabled
```

Preflight FAILs while IMDSv1 answers and warns while a role is served.

`cloud-init.yaml` (this directory) is deliberately thin: it updates packages
and prints the next steps. The real provisioning is the same
`deploy/provision-ubuntu.sh` every provider uses — EC2 is not a special path
anymore.

An SG with no inbound needs SSM or a one-time port-22 rule to get in the first
time; after `sudo tailscale up --ssh` works, remove it. That is the AWS form of
the generic rule "public SSH is for first provisioning only" — the SG plays the
role the Hostinger/Ubuntu firewall plays elsewhere. Port 6767 and Postgres get
no SG rule, ever.

First session (as the AMI's default `ubuntu` login user, or via SSM):

```bash
rsync -a ~/.nightshift/ ubuntu@<ip>:/tmp/nightshift/
sudo NIGHTSHIFT_USER=nightshift /tmp/nightshift/deploy/provision-ubuntu.sh
sudo rsync -a --chown=root:root /tmp/nightshift/ /srv/nightshift/
sudo tailscale up --ssh
# UI stays on loopback (unauthenticated): ssh -L 6767:127.0.0.1:6767. Tailnet
# `tailscale serve` is opt-in, with an ACL: deploy/README.md "Control API".
sudo -iu nightshift
# continue with deploy/README.md "Bring-up" step 4 onward
```

### Existing boxes provisioned under the `ubuntu` user

Instances built from the old fat cloud-init ran everything as `ubuntu` with
`/srv/nightshift` owned by it, and `provision-ubuntu.sh`, when re-run, still
**adopts the existing owner of `/srv/nightshift`**. Do not keep running that
way: cloud-init gives `ubuntu` `NOPASSWD: ALL` sudo, and every worker runs as
the runtime user, so every worker is root. Preflight FAILs on it.

Migrate to the dedicated user and keep `ubuntu` as the admin login only:

```bash
sudo NIGHTSHIFT_USER=nightshift /srv/nightshift/deploy/provision-ubuntu.sh
sudo chown -R nightshift:nightshift /srv/project
sudo -iu nightshift /srv/nightshift/deploy/bootstrap.sh
# then redo the interactive logins (claude, codex, gh) as nightshift
```

If the runtime user must stay `ubuntu`, remove its sudo instead, as root:
`rm /etc/sudoers.d/90-cloud-init-users` and `gpasswd -d ubuntu sudo`, after
confirming another way in as root (SSM, or Tailscale SSH to a different
account).

## Credentials on the box

Do not put static AWS keys (`~/.aws/credentials`) on the instance — workers can
read them (see `docs/TRUST-BOUNDARY.md`). Nightshift itself needs no AWS
access: state backup rides git (`omni/state` branch). If the **project** needs
AWS, use an instance role scoped to exactly what it needs, and know that it is
not more private than a key: every worker can fetch its credentials from IMDS
(IMDSv2 and hop limit 1 stop containers and SSRF, not host processes). With no
role, disable IMDS. Preflight warns on static keys and on a served role, and
FAILs on IMDSv1.

## EBS and recovery tiers

The generic recovery tiers (`deploy/README.md` → "Stop, restart, move") map to
EBS like this:

- **Tier 2 — EC2 stop → start**: EBS persists, everything comes back. Stop/start
  may change the public IP — irrelevant, access is Tailscale. Stopping the
  instance overnight is a legitimate way to pause the run for free.
- **Tier 3 — instance dead, volume alive**: launch a replacement from
  `cloud-init.yaml`, detach the old volume, attach and mount it (or restore its
  snapshot as the new root), run `provision-ubuntu.sh` + `bootstrap.sh` (both
  idempotent), `preflight.sh`, start the services.
- **Tier 4 — instance AND volume gone**: the provider-independent
  `omni/state` rebuild in the generic guide.

Before walking away: enable an EBS daily snapshot / DLM policy — it makes
tier 3 available instead of falling through to tier 4.
