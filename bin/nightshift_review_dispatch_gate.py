#!/usr/bin/env python3
"""Bind a Program dispatch to the review plan's answer (issue #720, ADR-0091).

ADR-0036 built a planner that decides which review domains a change needs and
marked its answer ``authority: "shadow_only"``. ADR-0042 allowed that answer to
narrow *dispatch* and never *eligibility*. Neither made anything read it: the
narrowing lived in crew prose, so the planner has never once changed what the
org did. This module is the code that reads it.

Three things constrain what can be gated here, and each of them removed a
larger, more obvious design:

1. **Gating ``code_review`` or ``pm`` is vacuous.** ``_classify`` seeds every
   plan with both before it looks at the diff, so a gate on either can never
   refuse. An ALLOW with no reachable DENY is not a gate; it is a decoration
   that reports success.
2. **The seats worth gating hold no slot.** ``security``/``ux``/``staff`` are
   ``EXEMPT_AGENTS``, so ``slot acquire`` -- the one existing dispatch
   chokepoint -- never sees them. What it does see (the ``reviewer_*`` seats)
   carries exactly the domain point 1 rules out.
3. **The fence does see them.** Every one is in ``PROGRAM_DIRECT_CHILDREN``,
   and the root policy denies any dispatch to them without a permit for that
   exact call. So the gate belongs where the permit is minted.

The mint was previously handed an opaque digest and could not know which seat
it was authorising. It now learns the call, and refuses to mint for a gated
seat whose domain the plan for that exact head does not name.
"""

import os
import re
import sqlite3
import subprocess


GIT = "/usr/bin/git"
FULL_OID = re.compile(r"[0-9a-f]{40}")

# Agent name -> the planner-vocabulary domain that agent carries.
#
# This is the PLANNER's vocabulary (`suggested_domains`), which is not the
# frozen review vocabulary the receipt/eligibility side speaks: that one says
# `staff_adr` where this says `staff`, `pm_acceptance` where the planner says
# `pm`, and it has a `qa` the planner never emits. The two are deliberately
# not reconciled here. ADR-0042 keeps `suggested_domains` away from merge
# eligibility, and a mapping table in this file would be the first step of
# carrying it there by accident.
#
# `cross_crew` is absent on purpose. It is dispatched under the `crew_alpha` /
# `crew_beta` agent names, which are also the names a BUILD dispatch uses, so
# the permit boundary cannot tell the two apart. Gating it here would mean
# refusing build dispatches on a review plan. It needs a distinguishable
# signal first, and is filed as #721.
GATED_SEATS = {
    "security": "security",
    "ux": "ux",
    "staff": "staff",
}

# Outcomes. `decide` returns exactly one, and the caller mints or refuses.
BOUND = "bound"          # plan names the domain -- mint
REFUSED = "refused"      # plan omits the domain -- do not mint
DEGRADED = "degraded"    # planner unusable -- mint anyway, and record it
FOREIGN = "foreign"      # target is not this repository -- mint, shadow only
EXEMPT = "exempt"        # caller declares this dispatch is not a review

# Stored in `program_action_permits.review_gate` for a declared non-review
# dispatch to a gated seat. Distinct from both a verified domain and NULL:
# NULL is "minted blind and never asked the question", which the fence
# refuses; this is "asked, and the caller stated on the record that this
# dispatch is not a review".
EXEMPT_GATE = "exempt"

# Stored for a mint that went through the gate but got no verdict from it --
# a degraded planner, or a repository the ruleset does not describe. It must
# NOT be stored as the domain: a permit that says `security` should mean the
# plan named `security`, and a fallback that spells itself the same way makes
# the two indistinguishable in the table that exists to tell them apart.
FALLBACK_GATE = "fallback"

# Reasons the planner refuses that are the CALLER'S doing, not the
# environment's. A caller who names a head that is not a commit, a repository
# that is not a repository, or a task that does not exist has not encountered
# a degraded planner -- it has made a bad call, and a bad call must not buy
# the fallback that a genuine outage buys. Without this split the gate is
# opt-out: supply `--head-sha 0000...0`, get "the planner could not answer",
# and mint the very permit the plan would have refused.
CALLER_INPUT_REASONS = frozenset({
    "base_invalid",
    "head_invalid",
    "head_not_descendant",
    "target_repo_unreadable",
    "task_missing",
})


class GateInput(RuntimeError):
    """The caller did not supply what a gated seat requires."""


def gate_for_agent(agent):
    """The domain `agent` carries, or None if this agent is not gated."""
    return GATED_SEATS.get(agent)


def _root_commit(repo):
    """The repository's root commit, as its identity, or None if unknowable.

    None is NOT "a different repository". A missing git, a repository with no
    commits, or a timeout cannot tell the two apart, and reporting a foreign
    target for what is really a broken environment would both mislabel the
    record and hand every mint the shadow-mode fallback.

    Not the path: Nightshift builds itself in worktrees, so path equality
    would call its own repository foreign roughly every time. Not the remote
    URL either -- a fork, a mirror or a missing remote all change it without
    changing which repository this is.
    """
    try:
        proc = subprocess.run(
            [GIT, "-C", repo, "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    roots = proc.stdout.split()
    return roots[-1] if roots else None


def names_a_real_commit(repo, sha):
    """Does `sha` name a commit that exists in `repo`?

    Checked here rather than left to the planner because the planner cannot
    tell the two apart: a head that does not exist and a git that does not
    work both surface as `git_unreadable`. One of those is the caller's fault
    and must be refused; the other is the environment's and must fall back.
    Conflating them makes the gate opt-out — name a junk head, be told the
    planner is degraded, and mint the permit the plan would have refused.
    """
    if not isinstance(sha, str) or not FULL_OID.fullmatch(sha):
        return False
    try:
        proc = subprocess.run(
            [GIT, "-C", repo, "cat-file", "-e", sha + "^{commit}"],
            capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None  # cannot tell -- the environment's problem, not the call's
    return proc.returncode == 0


def repository_identity(repo, bundle_root):
    """Decision 4: binding on this repository, shadow on a customer's.

    The ruleset the planner applies (`program/review-risk.json`) names this
    repository's own layout -- `bin/nightshift-ctl`, `deploy/**`, the crew
    directories -- and `_bundle_rules()` loads it from the executing bundle,
    so a target repository cannot supply its own. Against a customer tree
    almost nothing matches, and a binding gate would be refusing dispatches
    on a classification that never saw the code. Shadow there until an
    external repository can supply a reviewed, pinned ruleset of its own.
    """
    mine = _root_commit(bundle_root)
    theirs = _root_commit(repo)
    if not mine or not theirs:
        return None
    return mine == theirs


def decide(cx, agent, task_id, repo, head_sha, not_a_review=None):
    """Should a permit be minted for this dispatch, and on what grounds?

    Returns ``(outcome, domain, reason)``. `cx` must NOT be inside a
    transaction: the planner opens and closes its own, and the caller's
    `BEGIN IMMEDIATE` has to stay closed until this returns. Holding the
    control plane's write lock across a `git diff` is what produced a
    measured 32-second freeze the last time work like this ran inside the
    transaction rather than before it.
    """
    domain = gate_for_agent(agent)
    if domain is None:
        return None, None, None

    # These three seats do more than review. `ux` is dispatched for design
    # input BEFORE a crew is chartered (program/config.yaml step 3) and
    # `staff` writes a blind architecture draft, both at a point where no
    # PR, no head and often no task exist yet. A review plan cannot be
    # computed for a diff that has not been written, and refusing those
    # dispatches would break the earliest and cheapest design work the org
    # does -- the opposite of what #716's change 3 asks for.
    #
    # So the exception is DECLARED, never inferred. It cannot be taken by
    # omitting the facts (that still fails closed below), it names a reason,
    # and every use of it is recorded and countable. An exception available
    # by leaving an argument out is not an exception; it is the default.
    if not_a_review:
        if task_id or head_sha:
            # A call that supplies the review facts AND declares itself not a
            # review is stating two contradictory things, and the declaration
            # would win silently. Refuse rather than pick one: this is the
            # shape a refusal gets dodged in, by appending a flag to the call
            # that was just refused.
            raise GateInput(
                "--not-a-review contradicts --task/--head-sha; a dispatch is "
                "either a review of a specific head or it is not")
        return EXEMPT, domain, "declared_not_a_review:" + not_a_review
    missing = [name for name, value in
               (("--task", task_id), ("--repo", repo), ("--head-sha", head_sha))
               if not value]
    if missing:
        raise GateInput(
            "dispatching " + agent + " is gated on the review plan (ADR-0091); "
            "it needs " + ", ".join(missing) + " -- or, if this dispatch is "
            "not a review at all, --not-a-review with a reason")

    # Imported here, not at module scope: the planner proves its own bundle
    # identity on import-and-call and raises PlanDenied when it cannot, which
    # is a degradation to record rather than a crash at startup.
    import sys
    sys.dont_write_bytecode = True
    try:
        import nightshift_review_plan as planner
    except (ImportError, OSError, SyntaxError):
        return DEGRADED, domain, "planner_unimportable"

    try:
        bundle_root = os.path.dirname(os.path.dirname(
            os.path.realpath(planner.__file__)))
        same = repository_identity(repo, bundle_root)
        if same is None:
            return DEGRADED, domain, "repository_identity_unknowable"
        if not same:
            return FOREIGN, domain, "target_is_not_this_repository"
        real_head = names_a_real_commit(repo, head_sha)
        if real_head is False:
            return (REFUSED, domain,
                    "--head-sha does not name a commit in this repository, so "
                    "there is nothing to plan a review of")
        if real_head is None:
            return DEGRADED, domain, "head_unverifiable"
        report = planner.build_plan(cx, task_id, repo, head_sha)
    except planner.PlanDenied as exc:
        if exc.reason in CALLER_INPUT_REASONS:
            return REFUSED, domain, "the facts given do not describe a "\
                "reviewable head (" + exc.reason + ")"
        return DEGRADED, domain, "plan_denied:" + exc.reason
    except (sqlite3.Error, OSError, subprocess.SubprocessError) as exc:
        return DEGRADED, domain, "plan_failed:" + type(exc).__name__

    suggested = report.get("suggested_domains")
    if not isinstance(suggested, list):
        return DEGRADED, domain, "plan_malformed"
    if report.get("target", {}).get("head_sha") != head_sha:
        # Cannot currently happen -- the planner is asked for this exact head
        # and refuses any other. Checked anyway, because the whole value of
        # this gate is that the plan describes the code being dispatched
        # against, and a plan for a different head silently answers the wrong
        # question.
        return DEGRADED, domain, "plan_head_mismatch"
    if domain in suggested:
        return BOUND, domain, None
    return REFUSED, domain, "plan omits " + domain
