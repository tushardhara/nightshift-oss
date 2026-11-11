# Security policy

## Reporting a vulnerability

Report privately through GitHub: **Security → Report a vulnerability** on this
repository. Do not open a public issue, pull request or discussion for a
suspected vulnerability.

Include what you can of: the affected file and commit, the input or repository
content that triggers it, what an attacker gains, and a proof of concept.

You should get an acknowledgement within 7 days. Fixes land on `main`; there are
no maintained release branches.

## What is in scope

Nightshift runs LLM agents that read issues and pull requests, edit code, run
tests and publish through `git`/`gh` with the operator's credentials. The trust
model is in [`docs/TRUST-BOUNDARY.md`](docs/TRUST-BOUNDARY.md); read it before
deploying. Reports we most want:

- untrusted GitHub content (issue or comment text, labels, branch names, file
  contents of a target repository) gaining instructions, privileges or a lane it
  should not have;
- a seat bypassing a tool-call fence or authority check;
- escape from a seat sandbox, or a seat reading a credential;
- the control plane writing outside its state root, or executing code from a
  repository it operates on (hooks, `core.fsmonitor`, symlinks, tracked state);
- secrets reaching logs, transcripts, reports, argv or a pushed branch.

## What is not a vulnerability here

- Behaviour the trust boundary already documents as unenforced, unless you show
  it is reachable by someone the document says is untrusted.
- Anything requiring the operator's own shell access or host root.
