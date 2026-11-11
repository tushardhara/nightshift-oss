---
name: borrowed-repository
description: How to work in a repository we do not own — open pull requests and never merge, leave nothing behind, keep process artefacts out of their tree, and read the pull request after publishing because nobody will relay it.
---

# borrowed-repository — it is theirs, not ours

Everything here comes from the first real external run
(the first borrowed-repository engagement). Each rule is something
that went wrong, not something that might.

## Never merge. Open a pull request and stop.

`external_repo_merge_fence` denies every merge call when the repository is not
declared `ownership='self'`, and it fails closed with no escape hatch. Do not
look for one; a wrong merge into someone's default branch cannot be undone.

## Never touch `.github/`

No workflow edits, ever, and never trigger a run. **Nothing enforces the edit.**
`github_actions_fence` blocks `gh workflow run`, `gh run rerun` and dispatch
APIs, but writing to `.github/workflows/tests.yml` is an ordinary file write and
is allowed. This is on you, not on a fence.

Their CI is also theirs. If it is broken — on #737 a `lint` step ran
`git merge --abort` unguarded and failed every PR not targeting `master` —
report it in the pull request body. Do not fix it.

## Leave nothing behind

`git status --porcelain` in their checkout must be empty of anything we caused.
`nightshift-init.sh` writes its ignore rules to `.git/info/exclude`, never to
their tracked `.gitignore` (#744).

**Process artefacts stay in `.omni/`.** Evidence files, review notes, proposals,
mutation logs, round-by-round fix summaries. The #730 run committed 37 of them,
10,021 lines, into the customer's branch; a reviewer walked past all of it to
reach 78 Go files. One narrative document is what a pull request is entitled to.
Anything more is named in the brief or it does not ship.

## Write for them, not for us

- **Match the repository's own commenting style.** Measure it; do not guess. The
  #730 run wrote 47% comment lines into a codebase that writes 4-7%, and that is
  the single loudest complaint about it. A CI check refuses more than 2x the
  house style.
- **Plain English in every issue, pull request and runbook.** The audience is
  their on-call engineer at 03:00, not this org. #731's description ran to 1,400
  lines and nobody could act on it.
- **Their conventions win.** Their commit format, their branch names, their
  layout. We are guests.

## Read the pull request after you publish it

Nobody will relay it to you. On #737 the org opened a pull request, ran its own
reviewer, and spent 31 minutes fixing against that — while the customer's
reviewer had filed four HIGH findings and CI had gone red, unread.

After publishing, read both:

```
gh pr view <n> --json reviews,statusCheckRollup
```

Three things to get right, because each has already been got wrong:

- **Your own review is not the picture.** A reviewer belonging to the repository
  owner blocking the pull request is what matters. The org approving its own
  work means nothing here.
- **A check that fails and skips the others is not "one red check".** It means
  the build stopped reporting, so nothing green on it is evidence.
- **Queued is not failed.** A pull request read seconds after opening has every
  check pending. Wait; do not dispatch anyone at a build that has not run.

`release` does this on the external path (`observed_check_verdict`,
`latest_review_states`, `enumerate_commit_statuses`). Read
`docs/WHAT-IS-ACTUALLY-ENFORCED.md` before assuming any of the above is
mechanical — most of this file is convention, and it says which.

## Before building anything

If the brief names a question that decides the size of the work, do not start
until it is answered. #733 said so explicitly about continuous replication and
the org correctly waited. Record what you are assuming instead of guessing
around it.
