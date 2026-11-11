# Moved

The deployment guide is provider-neutral now:

- **Generic Ubuntu VPS guide (start here):** [`deploy/README.md`](README.md)
- **AWS/EC2 specifics** (security groups, IAM, EBS, cloud-init): [`deploy/providers/aws/README.md`](providers/aws/README.md)
- **Hostinger specifics** (current primary): [`deploy/providers/hostinger/README.md`](providers/hostinger/README.md)

Nothing about existing EC2 installs changed — boxes provisioned under the
`ubuntu` user keep working (see the AWS notes).
