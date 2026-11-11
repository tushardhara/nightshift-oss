"""Regression coverage for tests/dod_validate.py's check_d22 (condition 22).

crew_beta's round-1 review of PR #526 (docs/reviews/491-dod-validator-prose-
CONTRACT.md v2, finding F-4) noted check_d22 picked up several new branches
-- queued-gate PASS, receipt-comment PASS, merged-with-no-rollup FAIL, the
extra_prs union via git log -- with zero durable regression coverage; only
the generic --self-test evidence-removal case (break_d22) exercised it.
This file is that follow-up, in this project's normal test-file shape
(tests/test_review_receipts.py's own pattern) rather than shoehorned into
--self-test's break_evidence design, which proves a check CAN fail, not
that each branch of it is correct.

Every scenario replaces dod_validate.run_gh/run_git with a fake for the
duration of one test (setUp/tearDown save+restore) -- no live network call,
no live git subprocess beyond what the fakes intercept, matching AC-6's
"stdlib + git/gh only" instrument discipline without actually reaching
either instrument here.
"""

import json
import os
import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
import dod_validate as dv  # noqa: E402


def _recording(git):
    """Wrap a git fake so a test can assert WHICH probes ran, not only what
    the verdict was.

    R6-2. `staff` showed R5-1's two new tests assert an OUTCOME that any
    upstream fail-closed guard produces: `return False` before BOTH ancestry
    probes left both of them PASSING, and so did deleting the exclusion probe
    outright, while the same mutant reddened 13 other tests. Their
    reachability premise lived in their docstrings and nowhere else -- this
    round's own thesis, one level up, inside the tests written to close it.

    The remedy was already in this file, one test over:
    `test_every_ancestry_probe_disables_replace_refs` spies the argv and
    asserts which questions were asked. Generalised here so any fail-closed
    test can do the same in two lines.
    """
    argvs = []

    def _run(args, cwd=None, timeout=30):
        argvs.append(list(args))
        return git(args, cwd=cwd, timeout=timeout)

    return _run, argvs


def _ancestry_targets(argvs):
    """The revision each `merge-base --is-ancestor` probe asked ABOUT."""
    return [_subcommand(a)[3] for a in argvs if "--is-ancestor" in a]


def _subcommand(args):
    """`args` with git's leading GLOBAL options removed, so a fake matches on
    the subcommand it cares about. Round 4 added `--no-replace-objects` in
    front of every ancestry probe (R2); the fakes must not have to know that,
    and `test_every_ancestry_probe_disables_replace_refs` asserts the flag is
    really there rather than leaving these fakes to imply it."""
    i = 0
    while i < len(args) and args[i].startswith("--") and args[i] != "--merges":
        i += 1
    return list(args[i:])


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# A PR that touches code, i.e. a `task`-kind PR. Fixtures that are not
# themselves about kind derivation get this by default, so they exercise the
# ordinary path rather than the fail-closed fallback -- an undeterminable
# diff is a real, separately-reported condition and must not be every
# fixture's silent state.
CODE_FILES = [{"path": "tests/dod_validate.py"}]


def _fake_gh(pr_db, default_files=CODE_FILES):
    def _run(args, timeout=30):
        if args[:2] == ["pr", "view"]:
            n = int(args[2])
            data = dict(pr_db[n])
            if "files" not in data and default_files is not None:
                data["files"] = list(default_files)
            return _FakeProc(0, json.dumps(data))
        raise AssertionError(f"unexpected gh invocation: {args!r}")
    return _run


def _fake_git_merges(prs, baseline_prs=()):
    """check_d22 makes TWO distinct `git log --merges` calls and they must not
    be conflated: one over `<base>..HEAD` (PRs merged SINCE the baseline --
    the extra_prs union) and one over `<base>` alone (PRs merged AT OR BEFORE
    it -- the out-of-range set condition 22 does not audit). They are told
    apart by the revision argument's `..`, exactly as check_d22 forms them."""
    def _run(args, cwd=None, timeout=30):
        if "--merges" in args:
            rev = args[-1]
            chosen = prs if ".." in rev else baseline_prs
            lines = "\n".join(f"Merge pull request #{n} from x/y" for n in chosen)
            return _FakeProc(0, lines)
        raise AssertionError(f"unexpected git invocation: {args!r}")
    return _run


def _fake_git_grants_any_binding(prs=(), baseline_prs=(), head="d" * 40,
                                 merge_parent="e" * 40):
    """`_fake_git_merges` plus a git that grants EVERY SHA binding an evidence
    form could ask for -- in whichever DIRECTION that form needs.

    Deliberately adversarial, and the direction is the whole point. The
    closure guard below must fail if a comment-shaped evidence path is
    reintroduced. If this fake refused SHA questions, a reintroduced form
    could be rejected merely because git was unavailable to it -- the guard
    would pass for the wrong reason, which is the exact defect the guard
    exists to catch (`security` found it once already, in the guard's first
    version).

    Note that "permissive" stopped meaning "answer yes" the moment form (4)
    landed. Form (3) wanted `--is-ancestor <sha> <head>` to say YES; form (4)
    wants `--is-ancestor <sha> <mergeCommit>^1` to say NO. A fake that
    answered rc=0 to both would REFUSE every form (4), and a guard resting on
    it would go green for want of git all over again. So this fake answers
    each question in the direction that makes an evidence form ACCEPT:
    reachable from the head, not reachable from the merge's first parent.
    `test_the_permissive_git_fake_is_load_bearing` proves that matters.
    """
    merges = _fake_git_merges(prs, baseline_prs)

    def _run(args, cwd=None, timeout=30):
        if "--merges" in args:
            return merges(args, cwd=cwd, timeout=timeout)
        if _subcommand(args)[:2] == ["merge-base", "--is-ancestor"]:
            # rc 1 == git's "no, not an ancestor", which is what form (4)
            # needs to hear about `mergeCommit^1`. Everything else -- the
            # head binding and the eligibility probe against the pinned
            # tip -- is granted.
            return _FakeProc(1 if args[-1].endswith("^1") else 0, "")
        if args[:1] == ["rev-parse"]:
            return _FakeProc(0, "f" * 40 + "\n")
        if args[:1] == ["merge-base"] or args[:2] == ["cat-file", "-e"]:
            return _FakeProc(0, "")
        # Permissive rather than raising: an unexpected-invocation error would
        # fail the guard with a misleading reason instead of the meaningful
        # one ("D22 turned PASS").
        return _FakeProc(0, "f" * 40 + "\n")
    return _run


def _fake_git_refuses_every_binding(prs=(), baseline_prs=()):
    """The counterfactual: git answers `--merges` (so `check_d22` gets as far
    as the review clause at all) and refuses every SHA question with git's
    own bad-object status. Used ONLY to prove the fake above is load-bearing;
    never as the fixture for a real assertion about the form."""
    merges = _fake_git_merges(prs, baseline_prs)

    def _run(args, cwd=None, timeout=30):
        if "--merges" in args:
            return merges(args, cwd=cwd, timeout=timeout)
        return _FakeProc(128, "", "fatal: Not a valid object name")
    return _run


def _fake_git_ancestry(prs=(), baseline_prs=(), own_commits=(), base_commits=(),
                       head="d" * 40, merge_parent="e" * 40, eligible=True,
                       merge_parent_rc=None, head_rc=None):
    """A git that models real ancestry, so a test can say WHICH commits are
    the PR's own and which are shared base history -- the distinction the
    whole of `security`'s measured binding rests on -- and, since round 2,
    whether the PR's merge commit is an ancestor of the pinned eligibility
    tip.

    `own_commits` are reachable from the head and NOT from `mergeCommit^1`.
    `base_commits` are reachable from both. Anything else is reachable from
    neither. `eligible` answers the form-(4) eligibility probe with git's own
    status codes: True (rc 0) models a PR already merged when the corpus was
    audited; False (rc 1) models one merged after the pin, which is what
    actually bit and what no calendar cutoff could express; and "unknown"
    (rc 128) models git not knowing the SHA at all, which is a NON-ANSWER and
    must not be read as either. That third value has to be reachable
    INDEPENDENTLY of the other bindings -- a fake that refuses everything
    fails the head binding too, so a test built on one passes for the wrong
    reason and cannot see a validator that reads 128 as eligible.
    """
    eligibility_rc = {True: 0, False: 1, "unknown": 128}[eligible]
    merges = _fake_git_merges(prs, baseline_prs)
    own, base = set(own_commits) | {head}, set(base_commits) | {merge_parent}

    def _run(args, cwd=None, timeout=30):
        if "--merges" in args:
            return merges(args, cwd=cwd, timeout=timeout)
        argv = _subcommand(args)
        if argv[:2] == ["merge-base", "--is-ancestor"]:
            sha, target = argv[2], argv[3]
            if target == dv.FORM_4_ELIGIBLE_THROUGH:
                return _FakeProc(eligibility_rc, "",
                                 "fatal: Not a valid object name"
                                 if eligibility_rc == 128 else "")
            if target.endswith("^1"):
                # R5-1: `merge_parent_rc` forces a status on this question
                # alone -- 128 models an unfetched merge parent, a squash
                # parent or a shallow clone, while every OTHER binding is
                # still granted. Without that separation `inside` fails first
                # and the exclusion half is never reached, which is exactly
                # why the mutant survived.
                if merge_parent_rc is not None:
                    return _FakeProc(merge_parent_rc, "",
                                     "fatal: Not a valid object name"
                                     if merge_parent_rc == 128 else "")
                return _FakeProc(0 if sha in base else 1, "")
            if head_rc is not None:
                return _FakeProc(head_rc, "",
                                 "fatal: Not a valid object name"
                                 if head_rc == 128 else "")
            return _FakeProc(0 if sha in own or sha in base else 1, "")
        raise AssertionError(f"unexpected git invocation: {args!r}")
    return _run


# The form's five field lines, with no fences. Used by the closer-shape
# fixtures below so each differs from a genuine filing in EXACTLY its closer
# and nothing else -- otherwise a fixture can pass for a reason that has
# nothing to do with the property it is named for, which is the defect this
# whole round is about.
# The tag itself, so the suffix/prefix deviation rows below are built from it
# rather than retyped -- a retyped tag that drifts from the real one would
# make those rows pass for the wrong reason.
_REVIEW_FORM_TAG = "nightshift-retro-review"

# A form OPENER at column 0 -- R1's rule, and the same notion
# `_top_level_fenced_blocks` uses. Kept independent of the scanner on purpose:
# a guard that asked the scanner could not catch a scanner that had stopped
# seeing forms. `test_this_change_populates_nothing` asserts the two agree.
_FORM_OPENER_AT_COLUMN_0 = re.compile(
    r"^(?:`{3,}|~{3,})" + re.escape(_REVIEW_FORM_TAG) + r"[ \t]*$", re.MULTILINE)

_FORM_FIELDS = (
    "reviewer-seat: architect\n"
    "reviewed-sha: " + "c" * 40 + "\n"
    "verdict: PASS\n"
    "filed-at: 2026-09-02T18:00:00Z\n"
    "attests: I reviewed this pull request's changes at reviewed-sha and the "
    "verdict above is that review's own finding.\n")


# The one accepted shape, built here so every fixture that needs a GENUINE
# form gets a byte-identical one and a syntax change cannot silently make the
# positive cases stop exercising the positive path.
def _form(seat="architect", sha="c" * 40, verdict="PASS",
          filed_at="2026-09-02T18:00:00Z", attests=None, extra_lines=(),
          drop=(), tag="nightshift-retro-review"):
    lines = [
        f"reviewer-seat: {seat}",
        f"reviewed-sha: {sha}",
        f"verdict: {verdict}",
        f"filed-at: {filed_at}",
        "attests: " + (attests if attests is not None else
                       "I reviewed this pull request's changes at reviewed-sha "
                       "and the verdict above is that review's own finding."),
    ]
    lines = [ln for ln in lines if ln.split(":")[0] not in drop]
    lines.extend(extra_lines)
    return "```" + tag + "\n" + "\n".join(lines) + "\n```\n"


# --- ADR anchor parsing (R3, round 4) --------------------------------------
#
# The first version of the binding test globbed `ADR-0075-*` and demanded
# `Status: accepted` ON THAT FILE. `staff` showed it inverts the control it
# was added for: on the day the pin legitimately moves, doing it the way
# AGENTS.md:204 requires -- a NEW ADR, 0075 marked superseded -- turns CI RED,
# while the forbidden in-place edit of ADR-0075 stays GREEN. A governance
# control that punishes the governed path and rewards the forbidden one is
# worse than none.
#
# So the anchor binds by ROLE: the unique ACCEPTED ADR carrying a
# `Pinned SHA:` line, whatever its number. Supersession then becomes the green
# path, which is what the governance rule actually wants.
_ADR_PIN_LINE = re.compile(r"^Pinned SHA: `([0-9a-f]{40})`\s*$", re.MULTILINE)
# Two header styles are in live use -- `Date: ... · Status: x` (52 ADRs) and a
# standalone `Status: x` line (the rest, e.g. ADR-0071:3-4, often with a
# trailing markdown line break). Reading only one of them would silently
# mis-classify the other, which matters far more now that the anchor scans
# EVERY ADR rather than one known file: a superseded ADR read as accepted, or
# an accepted one read as nothing, both break the uniqueness claim below.
_ADR_STATUS_LINE = re.compile(r"^(?:Date:.*?\s)?Status:\s*(.+?)\s*$", re.MULTILINE)


def _adr_status(text):
    m = _ADR_STATUS_LINE.search(text)
    return m.group(1).strip() if m else None


# R5-2 (`staff`). `Pinned SHA:` alone is UNRESERVED PROSE, and this repository
# already has an ADR family about pinning SHAs (0013, 0021, 0030, 0064).
# `staff` added an accepted "ADR-0080: pin the runtime by exact git SHA" and
# the D22 suite went RED -- R3's own target failure arriving from the other
# side. It failed closed and named both files, so nothing was silent, but a
# selector that collides with an unrelated decision is a selector that will
# eventually be loosened by whoever is unblocking a release at 2am.
#
# So a carrier is selected by the CONJUNCTION: the pin line AND form (4)'s
# advancement trigger. Two independent strings, both specific to this
# decision, and the trigger is the sentence the whole control exists to make
# immutable -- so an ADR that carries the number without the rule is not a
# carrier, which is the right answer rather than a near miss.
_ADR_ADVANCEMENT_TRIGGER = (
    "the pin moves only when the audited corpus is re-run at a later tip, "
    "and never to make a newer PR attestable")


def _next_free_adr_number(decisions_dir):
    """One past the highest ADR number present, so a fixture writing into a
    copy of the real namespace cannot collide with a real decision."""
    used = [0]
    for path in decisions_dir.glob("ADR-*.md"):
        m = re.match(r"ADR-(\d{4})", path.name)
        if m:
            used.append(int(m.group(1)))
    return max(used) + 1


def _carries_the_trigger(text):
    return _ADR_ADVANCEMENT_TRIGGER in re.sub(r"\s+", " ", text)


def _accepted_adrs_carrying_a_pin(decisions_dir):
    """(path, pinned sha, text) for every ACCEPTED ADR that carries BOTH
    form (4)'s pin line and its advancement trigger."""
    found = []
    for path in sorted(decisions_dir.glob("ADR-*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        pin = _ADR_PIN_LINE.search(text)
        if not pin or not _carries_the_trigger(text):
            continue
        status = (_adr_status(text) or "").lower()
        if status.split(" ")[0] == "accepted":
            found.append((path, pin.group(1), text))
    return found


def _accepted_adrs_with_a_pin_line(decisions_dir):
    """Diagnostic only: accepted ADRs carrying a pin LINE, trigger or not. A
    near miss should be reportable, so a failure says which half is missing
    instead of just "expected 1, got 0"."""
    out = []
    for path in sorted(decisions_dir.glob("ADR-*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if _ADR_PIN_LINE.search(text) and \
                (_adr_status(text) or "").lower().split(" ")[0] == "accepted":
            out.append(path)
    return out


BODY = "Refs #1\n\nSome change."

RECEIPT_COMMENT = (
    "Some review prose.\n\n"
    "```nightshift-review\n"
    '{"domain": "cross_crew_review", "task_id": "t", "head_sha": "' + ("a" * 40) + '", '
    '"verdict": "pass", "marker": "m"}\n'
    "```\n"
)

PROSE_COMMENT = (
    "Cross-crew review (crew_beta): CLEAN PASS, 0 MERGE_BLOCKER. Routing to release."
)

GREEN_ROLLUP = [{"name": "tests", "conclusion": "SUCCESS"}]


class _D22Harness(unittest.TestCase):
    """setUp/tearDown shared by every class that drives `check_d22` against
    fakes. Carries no tests of its own."""

    def setUp(self):
        self._orig_gh = dv.run_gh
        self._orig_git = dv.run_git
        self._orig_merged_scan = dv._merged_pr_numbers_in_range
        self._orig_baseline_scan = dv._baseline_pr_numbers
        # Default both range scans to empty so a scenario states its own
        # corpus explicitly. Without this the baseline scan would reach the
        # REAL repository and silently drop fixture PRs #1/#2 (which really
        # are pre-baseline) out of every scenario that numbers them 1 or 2.
        dv._baseline_pr_numbers = lambda ctx, base: set()

    def tearDown(self):
        dv.run_gh = self._orig_gh
        dv.run_git = self._orig_git
        dv._merged_pr_numbers_in_range = self._orig_merged_scan
        dv._baseline_pr_numbers = self._orig_baseline_scan

    def _ctx(self, manifest):
        return dv.Ctx(dv.REPO_ROOT, manifest, None)


class CheckD22TestCase(_D22Harness):

    # -- empty manifest.changes: must fail before touching git/gh at all,
    #    the exact property break_d22's --self-test case depends on.
    def test_empty_changes_never_touches_git_or_gh(self):
        calls = {"git": 0, "gh": 0}

        def spy_gh(*a, **k):
            calls["gh"] += 1
            raise AssertionError("must not be called")

        def spy_git(*a, **k):
            calls["git"] += 1
            raise AssertionError("must not be called")

        dv.run_gh, dv.run_git = spy_gh, spy_git
        v = dv.check_d22(self._ctx({"changes": []}))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertEqual(calls, {"git": 0, "gh": 0})

    # -- grandfathered: a PR merged before ADR-0039's acceptance date is
    #    satisfied by the weaker prose-review convention.
    def test_grandfathered_pr_accepts_prose_review(self):
        manifest = {"changes": [1]}
        dv.run_gh = _fake_gh({1: {
            "state": "MERGED", "mergedAt": "2026-08-20T10:00:00Z", "body": BODY,
            "reviews": [], "comments": [{"body": PROSE_COMMENT}],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS")

    # -- round-3 boundary defect (staff re-check of PR #528, reproduced
    #    against real PR #234): the receipt tooling landed MID-DAY on
    #    2026-08-28 (09:50:53Z, commit cb8d68fc); a PR merged EARLIER THE
    #    SAME CALENDAR DAY must still be grandfathered -- a date-truncated
    #    comparison (`merged_at[:10] < "2026-08-28"`) wrongly said it wasn't,
    #    because neither this file's prior 8/20 nor 9/01 boundary case fell
    #    on the tool's own landing day. This is the exact case that must not
    #    regress.
    def test_same_day_before_tool_landed_is_grandfathered(self):
        manifest = {"changes": [234]}
        dv.run_gh = _fake_gh({234: {
            "state": "MERGED", "mergedAt": "2026-08-28T07:13:31Z", "body": BODY,
            "reviews": [], "comments": [{"body": PROSE_COMMENT}],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS",
                          "a PR merged before the receipt tool existed must be grandfathered "
                          "regardless of matching calendar date")

    # -- the exact landing instant itself is inclusive (AT OR BEFORE is
    #    grandfathered; strictly AFTER is not).
    def test_merge_at_exact_tool_landing_instant_is_grandfathered(self):
        manifest = {"changes": [235]}
        dv.run_gh = _fake_gh({235: {
            "state": "MERGED", "mergedAt": dv.ADR_0039_RECEIPT_TOOLING_LANDED_AT, "body": BODY,
            "reviews": [], "comments": [{"body": PROSE_COMMENT}],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS")

    # -- one second after the tool landed, same calendar day: no longer
    #    grandfathered, a prose-only comment is not enough.
    def test_one_second_after_tool_landed_same_day_is_not_grandfathered(self):
        manifest = {"changes": [236]}
        dv.run_gh = _fake_gh({236: {
            "state": "MERGED", "mergedAt": "2026-08-28T09:50:54Z", "body": BODY,
            "reviews": [], "comments": [{"body": PROSE_COMMENT}],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("1 of 1 checked PRs fail", v.reason)
        self.assertIn("recorded review from a different crew/vendor: 1 failing", v.reason)
        self.assertIn("(#236)", v.reason)

    # -- the exact defect the MERGE_BLOCKER named: post-cutoff, a prose
    #    comment shaped like PR #523's real one must NOT satisfy D22.
    def test_post_cutoff_pr_rejects_prose_only_review(self):
        manifest = {"changes": [2]}
        dv.run_gh = _fake_gh({2: {
            "state": "MERGED", "mergedAt": "2026-09-01T23:42:30Z", "body": BODY,
            "reviews": [], "comments": [{"body": PROSE_COMMENT}],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("recorded review from a different crew/vendor: 1 failing", v.reason)
        self.assertIn("(#2)", v.reason)

    # -- post-cutoff, a genuine nightshift-review fenced receipt passes.
    def test_post_cutoff_pr_accepts_real_receipt_block(self):
        manifest = {"changes": [3]}
        dv.run_gh = _fake_gh({3: {
            "state": "MERGED", "mergedAt": "2026-09-02T00:41:00Z", "body": BODY,
            "reviews": [], "comments": [{"body": RECEIPT_COMMENT}],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS")

    # -- native GitHub review object still accepted regardless of date.
    def test_native_review_object_always_accepted(self):
        manifest = {"changes": [4]}
        dv.run_gh = _fake_gh({4: {
            "state": "MERGED", "mergedAt": "2026-09-02T00:41:00Z", "body": BODY,
            "reviews": [{"state": "APPROVED"}], "comments": [],
            "statusCheckRollup": GREEN_ROLLUP,
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS")

    # -- a PR found only via the git-log merge scan (not in manifest.changes)
    #    is still checked, and still enforced.
    def test_uncovered_merged_pr_from_git_log_is_checked(self):
        manifest = {"changes": [5]}
        dv.run_gh = _fake_gh({
            5: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
                "reviews": [], "comments": [{"body": PROSE_COMMENT}],
                "statusCheckRollup": GREEN_ROLLUP},
            6: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
                "reviews": [], "comments": [], "statusCheckRollup": GREEN_ROLLUP},
        })
        dv.run_git = _fake_git_merges([5, 6])
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("1 of 2 checked PRs fail", v.reason)
        self.assertIn("recorded review from a different crew/vendor: 1 failing", v.reason)
        self.assertIn("(#6)", v.reason)

    # -- an open PR with no gate results and a currently-evidenced
    #    queued_gate excerpt PASSes with a queued reason.
    def test_open_pr_with_evidenced_queued_gate_passes(self):
        excerpt = "## RESOLVED 2026-08-23T10:49Z"
        real_blocked = (dv.REPO_ROOT / ".omni" / "blocked.md").read_text()
        self.assertIn(excerpt, real_blocked, "fixture excerpt must be real, not invented")
        manifest = {
            "changes": [7],
            "conditions": {"22": {"queued_gate": {"gate": "release", "blocked_md_excerpt": excerpt}}},
        }
        dv.run_gh = _fake_gh({7: {
            "state": "OPEN", "mergedAt": None, "body": BODY,
            "reviews": [], "comments": [{"body": RECEIPT_COMMENT}],
            "statusCheckRollup": [],
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS (queued: release)")

    # -- an open PR with no gate results and NO evidenced queued_gate FAILs.
    def test_open_pr_without_queued_gate_evidence_fails(self):
        manifest = {"changes": [8]}
        dv.run_gh = _fake_gh({8: {
            "state": "OPEN", "mergedAt": None, "body": BODY,
            "reviews": [], "comments": [{"body": RECEIPT_COMMENT}],
            "statusCheckRollup": [],
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("gate results: 1 failing (#8)", v.reason)

    # -- two PRs queued, and the manifest declares TWO distinct currently-
    #    unavailable gates (a list, not just one object): both real (both
    #    excerpts genuinely present in .omni/blocked.md), both must be named
    #    in the reason -- not just the last one seen (crew_beta cosmetic
    #    follow-up, tests/dod_validate.py's `queued_gates` accumulator).
    def test_multiple_queued_gates_are_all_named(self):
        excerpt_a = "## RESOLVED 2026-08-23T10:49Z"
        excerpt_b = "## OPEN 2026-08-28T~13:50Z"
        real_blocked = (dv.REPO_ROOT / ".omni" / "blocked.md").read_text()
        self.assertIn(excerpt_a, real_blocked, "fixture excerpt must be real, not invented")
        self.assertIn(excerpt_b, real_blocked, "fixture excerpt must be real, not invented")
        manifest = {
            "changes": [9, 10],
            "conditions": {"22": {"queued_gate": [
                {"gate": "release", "blocked_md_excerpt": excerpt_a},
                {"gate": "qa", "blocked_md_excerpt": excerpt_b},
            ]}},
        }
        dv.run_gh = _fake_gh({
            9: {"state": "OPEN", "mergedAt": None, "body": BODY, "reviews": [],
                "comments": [{"body": RECEIPT_COMMENT}], "statusCheckRollup": []},
            10: {"state": "OPEN", "mergedAt": None, "body": BODY, "reviews": [],
                 "comments": [{"body": RECEIPT_COMMENT}], "statusCheckRollup": []},
        })
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS (queued: qa, release)")

    # -- a bogus (unverifiable) gate excerpt in a list is silently dropped,
    #    not trusted just because it sits beside a real one.
    def test_queued_gate_list_drops_unverified_entries(self):
        excerpt_a = "## RESOLVED 2026-08-23T10:49Z"
        manifest = {
            "changes": [11],
            "conditions": {"22": {"queued_gate": [
                {"gate": "release", "blocked_md_excerpt": excerpt_a},
                {"gate": "qa", "blocked_md_excerpt": "this excerpt does not exist in blocked.md"},
            ]}},
        }
        dv.run_gh = _fake_gh({11: {
            "state": "OPEN", "mergedAt": None, "body": BODY, "reviews": [],
            "comments": [{"body": RECEIPT_COMMENT}], "statusCheckRollup": [],
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS (queued: release)")


    # ===================================================================
    # Defect 1 (#520) -- check_d22 returned on the FIRST failing PR, so the
    # condition reported "1 failure" against a 251-PR corpus. Three
    # consecutive heartbeats scoped a 251-PR problem as a 1-PR problem on
    # exactly that reason line. Per WA-015 the reason must report TOTALS.
    # ===================================================================

    def test_whole_corpus_is_evaluated_not_just_the_first_failure(self):
        """The regression that matters most: with several PRs failing for
        several different reasons, every one must be counted -- not just the
        lowest-numbered one the old loop happened to `return` on."""
        manifest = {"changes": [40, 41, 42, 43, 44]}
        good = {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
                "reviews": [{"state": "APPROVED"}], "comments": [],
                "statusCheckRollup": GREEN_ROLLUP}
        no_review = dict(good, reviews=[], comments=[])
        dv.run_gh = _fake_gh({
            40: dict(no_review),                       # review failure
            41: dict(no_review),                       # review failure
            42: dict(good),                            # clean
            43: dict(good, statusCheckRollup=[]),      # merged, no gate results
            44: dict(no_review, statusCheckRollup=[]), # both
        })
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        # 4 distinct PRs fail, out of 5 checked -- the old code said "1".
        self.assertIn("4 of 5 checked PRs fail condition 22", v.reason)
        self.assertIn("recorded review from a different crew/vendor: 3 failing", v.reason)
        self.assertIn("(#40, #41, #44)", v.reason)
        self.assertIn("gate results: 2 failing (#43, #44)", v.reason)

    def test_reason_reports_totals_and_bounds_the_example_list(self):
        """A large corpus must still report the true total, with the sample
        bounded and explicitly marked as a sample (`+N more`) so nobody can
        read the examples as the whole set."""
        prs = list(range(60, 80))  # 20 PRs, all failing the review gate
        manifest = {"changes": prs}
        dv.run_gh = _fake_gh({n: {
            "state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
            "reviews": [], "comments": [], "statusCheckRollup": GREEN_ROLLUP,
        } for n in prs})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("20 of 20 checked PRs fail condition 22", v.reason)
        self.assertIn("recorded review from a different crew/vendor: 20 failing", v.reason)
        self.assertIn("(#60, #61, #62, #63, #64, +15 more)", v.reason)
        # Zero-count classes are still named, so the line is legible as a
        # complete tally rather than only the classes that happened to fire.
        self.assertIn("pr-lint: 0 PRs failing", v.reason)
        self.assertIn("gate results: 0 failing", v.reason)

    # ===================================================================
    # Defect 2 (#520) -- pr-lint was invoked with no --kind, defaulting to
    # `task`, which asserts every PR in history is a task PR. `.omni/**`-only
    # bookkeeping PRs have no backlog issue by construction.
    # ===================================================================

    def test_kind_is_free_only_when_every_path_is_under_omni(self):
        self.assertEqual(dv._pr_lint_kind(
            {"files": [{"path": ".omni/STATUS.md"}, {"path": ".omni/ledger.md"}]}), "free")

    def test_kind_is_task_when_any_path_escapes_omni(self):
        self.assertEqual(dv._pr_lint_kind(
            {"files": [{"path": ".omni/STATUS.md"}, {"path": "bin/nightshift-ctl"}]}), "task")
        # A path that merely starts with the same letters is not under it.
        self.assertEqual(dv._pr_lint_kind({"files": [{"path": ".omnibus/x"}]}), "task")

    def test_kind_fails_closed_to_task_when_diff_is_undeterminable(self):
        """Fail closed: an undeterminable diff must never be assumed `free`.
        `task` is the strictly stronger kind, so an unknown diff can only ever
        produce MORE findings, never silently exempt a PR."""
        for undeterminable in ({}, {"files": None}, {"files": []},
                               {"files": [{"path": ""}]}, {"files": [{"nope": 1}]}):
            self.assertEqual(dv._pr_lint_kind(undeterminable), "task", undeterminable)

    def test_pr_lint_is_invoked_with_the_derived_kind(self):
        """End-to-end: the kind derived from each PR's own diff is what
        actually reaches the pr-lint command line."""
        seen = []
        real_run = dv.subprocess.run

        def spy_run(cmd, *a, **k):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] == "pr-lint":
                seen.append(list(cmd))
                return _FakeProc(0, '{"ok": true}')
            return real_run(cmd, *a, **k)

        manifest = {"changes": [90, 91]}
        dv.run_gh = _fake_gh({
            90: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": "no refs line",
                 "reviews": [{"state": "APPROVED"}], "comments": [],
                 "statusCheckRollup": GREEN_ROLLUP,
                 "files": [{"path": ".omni/STATUS.md"}]},
            91: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
                 "reviews": [{"state": "APPROVED"}], "comments": [],
                 "statusCheckRollup": GREEN_ROLLUP,
                 "files": [{"path": "bin/nightshift-ctl"}]},
        })
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        ctl = dv.REPO_ROOT / "bin" / "nightshift-ctl"
        self.assertTrue(ctl.exists(), "this test asserts on the real ctl invocation")
        dv.subprocess.run = spy_run
        try:
            dv.check_d22(self._ctx(manifest))
        finally:
            dv.subprocess.run = real_run
        kinds = {}
        for cmd in seen:
            self.assertIn("--kind", cmd, "pr-lint must never be invoked without --kind")
            k = cmd[cmd.index("--kind") + 1]
            kinds[k] = kinds.get(k, 0) + 1
        self.assertEqual(kinds, {"free": 1, "task": 1},
                         "the .omni-only PR must lint as `free`, the code-touching one as `task`")

    def test_omni_only_pr_without_refs_line_no_longer_fails_pr_lint(self):
        """The measured consequence: an `.omni/**`-only bookkeeping PR has no
        backlog issue by construction (docs/owners.md:27), so requiring
        `Refs #<n>` of it was the validator asserting a kind it never
        declared. Roughly 20 of 32 pr-lint failures were exactly this."""
        manifest = {"changes": [92]}
        dv.run_gh = _fake_gh({92: {
            "state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z",
            "body": "Program bookkeeping: heartbeat status roll-up. No backlog issue.",
            "reviews": [{"state": "APPROVED"}], "comments": [],
            "statusCheckRollup": GREEN_ROLLUP,
            "files": [{"path": ".omni/STATUS.md"}, {"path": ".omni/ledger.md"}],
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS")

    def test_code_touching_pr_without_refs_line_still_fails_pr_lint(self):
        """The other side of the same fix -- deriving the kind must not become
        a way for a real task PR to escape the `Refs #<n>` rule."""
        manifest = {"changes": [93]}
        dv.run_gh = _fake_gh({93: {
            "state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z",
            "body": "Some code change with no backlog reference at all.",
            "reviews": [{"state": "APPROVED"}], "comments": [],
            "statusCheckRollup": GREEN_ROLLUP,
            "files": [{"path": ".omni/STATUS.md"}, {"path": "bin/nightshift-ctl"}],
        }})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("pr-lint: 1 PRs failing", v.reason)
        # the breakdown names WHICH convention was missed, and how often
        self.assertIn("1x no `Refs #<n>` line", v.reason)
        self.assertIn("(#93)", v.reason)
        self.assertIn("Refs #", v.reason)

    # ===================================================================
    # Range correction -- condition 22's range is `a73ba88..main`, so PRs
    # merged at or before the baseline are outside it even when the
    # manifest's `changes` list still names them.
    # ===================================================================

    def test_prs_merged_at_or_before_the_baseline_leave_the_checked_set(self):
        manifest = {"changes": [1, 2, 50]}
        dv.run_gh = _fake_gh({50: {
            "state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
            "reviews": [{"state": "APPROVED"}], "comments": [],
            "statusCheckRollup": GREEN_ROLLUP,
        }})  # #1 and #2 absent on purpose: reaching for them would KeyError
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        dv._baseline_pr_numbers = lambda ctx, base: {1, 2}
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.line("D22"), "D22 PASS")

    def test_baseline_scan_reads_the_baseline_commit_not_the_range(self):
        """The two git scans must not be conflated: `<base>..HEAD` yields the
        PRs to ADD, `<base>` alone the PRs to REMOVE. Swapping them would
        empty the corpus entirely."""
        dv.run_git = _fake_git_merges([9, 10], baseline_prs=[1, 2])
        # setUp stubs _baseline_pr_numbers for the other scenarios; this test
        # is about the real one, so call the saved original directly.
        self.assertEqual(self._orig_baseline_scan(self._ctx({}), "a73ba88"), {1, 2})
        self.assertEqual(dv._merged_pr_numbers_in_range(self._ctx({}), "a73ba88"), {9, 10})

    def test_an_empty_checked_set_is_never_a_pass(self):
        """If the range correction ever removed everything, the condition must
        FAIL rather than vacuously PASS on an audit of nothing."""
        manifest = {"changes": [1, 2]}
        dv.run_gh = _fake_gh({})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        dv._baseline_pr_numbers = lambda ctx, base: {1, 2}
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("nothing was audited", v.reason)

    # ===================================================================
    # Reason-line fidelity. WA-015 is not satisfied by a correct TOTAL if
    # the total conflates classes that need different work: a PR that
    # merged before the receipt mechanism existed is a different problem
    # from one that had the mechanism and did not use it, and a `pr-lint`
    # count is only a work estimate if it says WHICH convention was missed.
    # ===================================================================

    def test_review_failures_split_grandfathered_from_post_cutoff(self):
        """The scoping number. A single 'N failing' total invites the same
        mis-sizing WA-015 exists to prevent -- one of these groups can only be
        closed by a real review, the other never had the tooling at all."""
        manifest = {"changes": [70, 71, 72]}
        dv.run_gh = _fake_gh({
            # merged before the tooling landed, and fails even the weaker bar
            70: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": BODY,
                 "reviews": [], "comments": [], "statusCheckRollup": GREEN_ROLLUP},
            71: {"state": "MERGED", "mergedAt": "2026-08-27T00:00:00Z", "body": BODY,
                 "reviews": [], "comments": [], "statusCheckRollup": GREEN_ROLLUP},
            # merged after the tooling landed: had the mechanism, did not use it
            72: {"state": "MERGED", "mergedAt": "2026-09-01T00:00:00Z", "body": BODY,
                 "reviews": [], "comments": [], "statusCheckRollup": GREEN_ROLLUP},
        })
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("recorded review from a different crew/vendor: 3 failing", v.reason)
        self.assertIn(f"2 merged at or before {dv.ADR_0039_RECEIPT_TOOLING_LANDED_AT}",
                      v.reason)
        self.assertIn("1 merged after it with no genuine ADR-0039 receipt", v.reason)

    def test_pr_lint_breakdown_counts_each_convention_missed(self):
        """`31 failing` is a number; `30x no Refs, 1x closing keyword` is a
        plan. The breakdown must aggregate across the corpus, not sample one
        PR's output as the prior implementation did."""
        manifest = {"changes": [80, 81, 82]}
        no_refs = "A change with no backlog reference at all."
        closing = "Refs #520\n\nCloses #999"
        dv.run_gh = _fake_gh({
            80: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": no_refs,
                 "reviews": [{"state": "APPROVED"}], "comments": [],
                 "statusCheckRollup": GREEN_ROLLUP},
            81: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": no_refs,
                 "reviews": [{"state": "APPROVED"}], "comments": [],
                 "statusCheckRollup": GREEN_ROLLUP},
            82: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": closing,
                 "reviews": [{"state": "APPROVED"}], "comments": [],
                 "statusCheckRollup": GREEN_ROLLUP},
        })
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        # #520: the headline counts PRs and the breakdown counts FINDINGS.
        # Both numbers were always right; the line did not say which was
        # which, and one PR failing two conventions (#558) makes them differ.
        # It cost a reconciliation cycle to work out, so each states its unit.
        self.assertIn("pr-lint: 3 PRs failing", v.reason)
        self.assertIn("[findings: 2x no `Refs #<n>` line", v.reason)
        self.assertIn("1x closing keyword(s) on a task PR", v.reason)

    def test_the_two_pr_lint_counts_are_different_numbers_and_say_so(self):
        """The #520 line fix, made load-bearing. One PR that misses BOTH
        conventions (the real #558) puts 1 in the headline and 2 in the
        breakdown. Nobody can read that line correctly without being told
        which count is PRs and which is findings, and nothing about the
        numbers themselves was ever wrong."""
        manifest = {"changes": [85]}
        both = "Closes #999"          # no `Refs #<n>` line AND a closing keyword
        dv.run_gh = _fake_gh({
            85: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z", "body": both,
                 "reviews": [{"state": "APPROVED"}], "comments": [],
                 "statusCheckRollup": GREEN_ROLLUP},
        })
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("pr-lint: 1 PRs failing", v.reason)
        self.assertIn("[findings: ", v.reason)
        self.assertIn("1x no `Refs #<n>` line", v.reason)
        self.assertIn("1x closing keyword(s) on a task PR", v.reason)
        # The whole point: the headline is 1 PR and the findings total 2.
        findings = re.search(r"\[findings: (.*?)\]", v.reason).group(1)
        self.assertEqual(
            sum(int(m) for m in re.findall(r"(\d+)x ", findings)), 2,
            "one PR, two findings -- which is exactly why the line has to "
            "name the unit of each count")

    def test_undeterminable_diff_is_reported_not_silently_folded_in(self):
        """The `task` fallback is safe but it is still a fallback. If a gh
        outage emptied every `files` array the whole corpus would silently
        re-acquire the exact defect this task fixed, reported as a body
        convention problem. It must be named and counted in its own right."""
        manifest = {"changes": [90]}
        dv.run_gh = _fake_gh(
            {90: {"state": "MERGED", "mergedAt": "2026-08-20T00:00:00Z",
                  "body": "no backlog reference here",
                  "reviews": [{"state": "APPROVED"}], "comments": [],
                  "statusCheckRollup": GREEN_ROLLUP, "files": []}},
            default_files=None)
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx(manifest))
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("pr-lint kind fell back to `task` (diff undeterminable, "
                      "never assumed): 1 (#90)", v.reason)

    # ===================================================================
    # A third form was implemented in round 1 of #587 and removed before
    # merge, because its stated binding did not exist: `security` showed one
    # constant string naming D22's own baseline commit satisfied it for 216
    # of 216 in-range PRs. Its successor -- form (4), the retroactive review
    # form -- landed in #520 and has its own class below.
    #
    # These two tests are unchanged by that and still do exactly what they
    # did: they forbid a comment-shaped evidence path that rests on PROSE
    # matching, under any name. Form (4) does not weaken them, because it
    # accepts nothing that is not a fenced block with five exact fields --
    # every string below is still refused, and `test_architects_four_
    # adversarial_cases_are_all_refused` re-asserts the same property against
    # the code that now exists to attack.
    # ===================================================================

    def test_a_comment_shaped_evidence_path_cannot_return_unnoticed(self):
        """Asserted THROUGH `check_d22`, which is the layer that matters.

        The first version of this guard asserted against `_has_recorded_review`
        directly and was **vacuous**. `security` mutation-tested it: form (3)
        was reached through a defaulted `is_reachable=None` parameter that
        `check_d22` -- not the test -- supplied, so a two-argument call one
        layer below could not observe that shape at all. Both behavioural
        assertions returned False and passed against *any* implementation, and
        identical code under plausible successor names, fully wired in, kept
        the whole suite green. The guard's entire weight was resting on five
        string literals in the `hasattr` loop.

        Driving `check_d22` with a fake `gh` fixes the layering: the PR below
        lints clean and has its gate results, so the review clause is the only
        thing that can fail it. D22 `FAIL` here means no comment-shaped
        evidence path accepted the comment; D22 `PASS` means one did, under
        whatever name. The git fake says yes to every SHA question, so a
        reintroduced form cannot be rejected merely for want of git."""
        for label, comment in (
            # security's defeating string: real seat, real verdict token, and
            # D22's own baseline SHA, which every in-range PR descends from.
            ("baseline-SHA universal string",
             "staff review: PASS a73ba881c04a4e5e2b716f89a0beb08a6e1164dc"),
            # architect's case: no intent to attest at all. The one that can
            # fire by accident across a corpus, with no bad actor.
            ("author's own rebase note",
             "Rebased onto a4e7d09; program bookkeeping only, CI PASS"),
            # Three unrelated clauses supplying seat, SHA and verdict.
            ("seat, SHA and verdict from three unrelated sentences",
             "Handing this to crew_beta next.\n\nBased on 5eb1430fe6db5ae6.\n\n"
             "Unrelated: the flaky test now reports PASS."),
        ):
            with self.subTest(evidence=label):
                manifest = {"changes": [700]}
                dv.run_gh = _fake_gh({700: {
                    "state": "MERGED", "mergedAt": "2026-09-01T00:00:00Z",
                    "body": BODY, "reviews": [], "statusCheckRollup": GREEN_ROLLUP,
                    "comments": [{"body": comment}],
                    # present so a successor form has an anchor to work with;
                    # withholding them would let the guard pass for want of data
                    "headRefOid": "d" * 40, "mergeCommit": {"oid": "e" * 40},
                }})
                dv.run_git = _fake_git_grants_any_binding()
                dv._merged_pr_numbers_in_range = lambda ctx, base: set()
                v = dv.check_d22(self._ctx(manifest))
                self.assertEqual(
                    v.status, dv.Status.FAIL,
                    f"D22 PASSed on a PR whose only review artifact is {label!r}. "
                    "A comment-shaped evidence path has been reintroduced. It "
                    "needs BOTH remedies together -- the SHA bound to the PR's "
                    "own commits (reachable from head, NOT from mergeCommit^1) "
                    "AND an explicit structured review assertion -- plus its own "
                    "adversarial tests. See the NOTE above _has_recorded_review.")
                self.assertIn(
                    "recorded review from a different crew/vendor: 1 failing", v.reason,
                    "D22 must fail on the REVIEW clause here, not incidentally "
                    "on pr-lint or gate results")

    def test_the_withdrawn_form_s_helpers_stay_absent(self):
        """The cheap half of the guard, kept because it catches the lazy case
        -- a straight revert -- by name. It does NOT catch a rename, which is
        why the behavioural test above exists and does the real work."""
        for gone in ("_has_sha_bound_structured_review", "_make_sha_reachability_check",
                     "_d22_head_anchor", "_REVIEW_SEAT_RE", "_HEX_SHA_RE"):
            self.assertFalse(
                hasattr(dv, gone),
                f"{gone} was removed with receipt form (3); re-adding a "
                "comment-shaped evidence path needs both the PR-own-commit "
                "binding and an explicit review assertion (see the NOTE above "
                "_has_recorded_review)")


# The universal string that defeated form (3): condition 22's own baseline
# commit, which every in-range PR descends from by construction.
UNIVERSAL_BASELINE_SHA = "a73ba881c04a4e5e2b716f89a0beb08a6e1164dc"
OWN_SHA = "c" * 40          # one of the PR's own commits
BASE_SHA = "b" * 40         # shared history, reachable from mergeCommit^1
UNRELATED_SHA = "9" * 40    # reachable from neither


class RetroReviewFormTestCase(_D22Harness):
    """Form (4) -- the retroactive structured review form, the successor to
    the withdrawn form (3).

    Every test here drives `check_d22`, not a helper one layer below it.
    That is not a style preference: `security` mutation-proved that asserting
    below `check_d22` is how the previous guard came to be vacuous, and how
    identical code under a plausible successor name passed a whole 30-test
    suite unnoticed. The layer that decides D22's verdict is the layer that
    gets asserted.
    """

    def _pr(self, comment_bodies, merged_at="2026-09-01T00:00:00Z",
            head="d" * 40, merge="e" * 40, state="MERGED", body=BODY,
            comment_created_at="2026-09-02T09:00:00Z"):
        # `createdAt` is GitHub's own clock and, since round 1's MB-1, the
        # thing the posteriority control actually rests on -- so every fixture
        # comment carries one, exactly as `gh pr view --json comments` returns
        # for all 224 PRs `security` fetched.
        data = {
            "state": state, "mergedAt": merged_at, "body": body,
            "reviews": [], "statusCheckRollup": GREEN_ROLLUP,
            "comments": [{"body": b, "createdAt": comment_created_at}
                         for b in comment_bodies],
            "headRefOid": head, "mergeCommit": {"oid": merge},
        }
        if merge is None:
            data["mergeCommit"] = None
        if head is None:
            data["headRefOid"] = None
        return data

    def _verdict(self, pr_db, git=None, eligible=True):
        dv.run_gh = _fake_gh(pr_db)
        dv.run_git = git or _fake_git_ancestry(
            own_commits={OWN_SHA}, base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA},
            eligible=eligible)
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        return dv.check_d22(self._ctx({"changes": sorted(pr_db)}))

    # ------------------------------------------------------------------
    # The positive path -- and the honesty requirement on its output.
    # ------------------------------------------------------------------

    def test_a_genuine_form_is_accepted(self):
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)])})
        self.assertEqual(v.status, dv.Status.PASS, v.reason)

    def test_the_emitted_verdict_line_declares_the_retro_weakening(self):
        """The honesty requirement, asserted where it cannot decay: on the
        LINE the gate prints, not in DoD prose. A PASS resting on an
        after-the-fact attestation must not be printable as a bare `PASS`,
        because a reader of the close-out report sees the line and nothing
        else."""
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)])})
        line = v.line("D22")
        self.assertTrue(line.startswith("D22 PASS (retro: 1"), line)
        self.assertIn("RETROACTIVE", line)
        self.assertIn("weaker than a live ADR-0039 receipt", line)
        self.assertIn("#700", line)

    def test_a_pass_with_no_forms_stays_a_bare_pass(self):
        """The corollary, and the reason `retro:` is worth anything: a PASS
        that did NOT lean on an attestation must not carry the caveat, or the
        caveat stops carrying information."""
        v = self._verdict({700: self._pr([RECEIPT_COMMENT])})
        self.assertEqual(v.line("D22"), "D22 PASS")

    def test_retro_is_named_on_the_fail_side_too(self):
        """A failing corpus still has to disclose which of its PASSing members
        rest on attestations -- otherwise the disclosure disappears at exactly
        the moment the report is longest and least read."""
        v = self._verdict({
            700: self._pr([_form(sha=OWN_SHA)]),
            701: self._pr([]),
        })
        self.assertEqual(v.status, dv.Status.FAIL)
        self.assertIn("retro: 1", v.reason)
        self.assertIn("recorded review from a different crew/vendor: 1 failing", v.reason)

    def test_this_change_populates_nothing(self):
        """Scope guard. The charter builds the mechanism and forbids filing
        attestations on the ~48 historical PRs inside a tooling PR. If a form
        ever appears in this repository's own tracked files, it arrived by a
        decision that was never taken here.

        THE NOTION OF "A FORM" USED HERE, stated because the next reader will
        otherwise narrow or widen it by accident: a form is a fence OPENER AT
        COLUMN 0 whose info string is exactly the tag -- `_FORM_OPENER_AT_
        COLUMN_0`, which is R1's rule and the same notion
        `_top_level_fenced_blocks` uses. Indented text is not a form; a
        blockquote is not a form; an inline-code mention of the tag inside a
        sentence is not a form.

        This used to be `git grep -F` for the tag as a SUBSTRING ANYWHERE IN A
        LINE, and that was broader than the property this docstring states --
        the same defect rounds 5 and 6 were about, this time in the guard that
        enforces scope. It failed the combined #626+#627 candidate on
        `docs/prd/DEFINITION-OF-DONE.md`, whose only matches are inline-code
        mentions in prose: ``a fenced `` ```<tag> `` block``. The DoD DEFINES
        form (4); a guard that forbids the specification from naming the
        mechanism it specifies has the relationship backwards. The guard
        proves the mechanism is unpopulated; it does not police prose.

        It loses no strength, and that is asserted below rather than claimed:
        a real form must be a real fence to be parsed at all, so a column-0
        match catches every form the scanner could ever read. The two-way
        proof is part of this test on purpose -- it is the assertion that
        keeps the matcher and the scanner from drifting apart, since the
        matcher is deliberately INDEPENDENT of the scanner (a guard that asked
        the scanner could not catch a scanner that had stopped seeing forms).
        """
        tag = dv._REVIEW_FORM_FENCE_TAG

        # The two-way proof, permanent rather than a one-off: the matcher and
        # `_top_level_fenced_blocks` must agree on a genuine form, on the DoD's
        # inline-code mention, and on an indented near-miss.
        genuine = _form(sha=OWN_SHA)
        inline = "form (4) is filed as a fenced `` ```" + tag + " `` block."
        indented = "  ```" + tag + "\n" + _FORM_FIELDS + "  ```\n"
        for label, text, is_form in (("a genuine column-0 form", genuine, True),
                                     ("the DoD's inline-code mention", inline, False),
                                     ("an indented near-miss", indented, False)):
            with self.subTest(sample=label):
                self.assertEqual(
                    bool(_FORM_OPENER_AT_COLUMN_0.search(text)), is_form,
                    f"the guard's matcher disagrees with its own docstring on {label}")
                self.assertEqual(
                    bool(dv._top_level_fenced_blocks(text, tag)), is_form,
                    f"the guard's matcher and the scanner have drifted on {label}")

        import subprocess as _sp
        # Cheap prefilter on the bare tag -- a superset of any real form -- then
        # the column-0 rule against each candidate blob.
        hits = _sp.run(["git", "grep", "-I", "-l", "-F", tag, "HEAD"],
                       cwd=str(dv.REPO_ROOT), capture_output=True, text=True)
        populated = []
        for path in hits.stdout.split():
            if path.startswith("HEAD:tests/"):
                continue
            blob = _sp.run(["git", "show", path], cwd=str(dv.REPO_ROOT),
                           capture_output=True, text=True).stdout
            if _FORM_OPENER_AT_COLUMN_0.search(blob):
                populated.append(path)
        self.assertEqual(populated, [], "form (4) must be a mechanism, not a populated one")

    # ------------------------------------------------------------------
    # `architect`'s four adversarial cases. Omitted from the withdrawn
    # form's PR on the strength of a guard that turned out untested; that
    # was product's error and it is not repeating.
    # ------------------------------------------------------------------

    def test_architects_four_adversarial_cases_are_all_refused(self):
        cases = (
            # 1. No intent to attest at all. The one that can fire by accident
            #    across a corpus, with no bad actor anywhere.
            ("author's own rebase note",
             f"Rebased onto {OWN_SHA}; program bookkeeping only, CI PASS"),
            # 2. Seat names as prose and as addressees -- a comment that talks
            #    ABOUT seats rather than being filed BY one.
            ("seat names as prose and as addressees",
             f"Handing this to crew_beta next; cc @staff and @security.\n"
             f"Verdict PASS on the rebase at {OWN_SHA}."),
            # 3. A reachable SHA drawn from pre-PR base history, inside an
            #    OTHERWISE PERFECT form. This is the case that tests the
            #    binding rather than the syntax, and it is the one the
            #    withdrawn form died on.
            ("perfect form naming a pre-PR base-history SHA",
             _form(sha=BASE_SHA)),
            # 4. Seat, SHA and verdict from three unrelated sentences: the
            #    three signals with no semantic relationship between them.
            ("seat, SHA and verdict from three unrelated sentences",
             f"Handing this to crew_beta next.\n\nBased on {OWN_SHA}.\n\n"
             f"Unrelated: the flaky test now reports PASS."),
        )
        for label, comment in cases:
            with self.subTest(case=label):
                v = self._verdict({700: self._pr([comment])})
                self.assertEqual(
                    v.status, dv.Status.FAIL,
                    f"form (4) accepted {label!r}. Both bindings are required "
                    "together: the SHA must be one of the PR's OWN commits AND "
                    "the claim must be an explicit form parsed as a unit.")
                self.assertIn(
                    "recorded review from a different crew/vendor: 1 failing", v.reason,
                    "must fail on the REVIEW clause, not incidentally on pr-lint")
                self.assertNotIn("retro", v.reason)

    # ------------------------------------------------------------------
    # `security`'s four defeating strings, measured against every PR's own
    # real head rather than a shared fixture head.
    # ------------------------------------------------------------------

    def test_securitys_defeating_strings_score_zero_against_every_real_head(self):
        heads = {700: "1" * 40, 701: "2" * 40, 702: "3" * 40, 703: "4" * 40}
        every_seat_word = " ".join(sorted(s.upper() for s in dv._REVIEW_FORM_SEATS))

        def probes(head):
            return (
                ("the constant baseline string that defeated form (3)",
                 f"staff review: PASS {UNIVERSAL_BASELINE_SHA}"),
                ("author's own rebase note",
                 f"Rebased onto {head}; program bookkeeping only, CI PASS"),
                ("seat + verdict + the PR's own head SHA",
                 f"crew_beta review: PASS {head}"),
                ("every seat word and verdict token, uppercase, + head SHA",
                 f"{every_seat_word} APPROVED PASS CLEAN PASS CLEARED {head}"),
            )

        for n, head in heads.items():
            for label, comment in probes(head):
                with self.subTest(pr=n, probe=label):
                    # Ancestry model: the PR's own head IS one of its own
                    # commits, so this probe gets the most favourable
                    # possible binding and still must score zero.
                    git = _fake_git_ancestry(
                        own_commits={head, OWN_SHA},
                        base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA},
                        head=head)
                    v = self._verdict({n: self._pr([comment], head=head)}, git=git)
                    self.assertEqual(v.status, dv.Status.FAIL, f"{label} accepted")
                    self.assertIn(
                        "recorded review from a different crew/vendor: 1 failing",
                        v.reason)

    # ------------------------------------------------------------------
    # `architect`'s reintroduction condition: fail closed on missing or
    # duplicated fields. A partially-present form is a FAIL, not a weaker
    # pass.
    # ------------------------------------------------------------------

    def test_the_syntax_fails_closed_on_every_deviation(self):
        canonical = ("I reviewed this pull request's changes at reviewed-sha "
                     "and the verdict above is that review's own finding.")
        deviations = (
            ("missing reviewer-seat", _form(sha=OWN_SHA, drop=("reviewer-seat",))),
            ("missing reviewed-sha", _form(sha=OWN_SHA, drop=("reviewed-sha",))),
            ("missing verdict", _form(sha=OWN_SHA, drop=("verdict",))),
            ("missing filed-at", _form(sha=OWN_SHA, drop=("filed-at",))),
            ("missing attests", _form(sha=OWN_SHA, drop=("attests",))),
            ("duplicated verdict",
             _form(sha=OWN_SHA, extra_lines=("verdict: PASS",))),
            ("duplicated reviewed-sha, second one bogus",
             _form(sha=OWN_SHA, extra_lines=(f"reviewed-sha: {BASE_SHA}",))),
            ("duplicated reviewer-seat",
             _form(sha=OWN_SHA, extra_lines=("reviewer-seat: staff",))),
            ("unknown field",
             _form(sha=OWN_SHA, extra_lines=("override: yes",))),
            ("empty value", _form(sha=OWN_SHA, verdict="")),
            ("two forms in one body", _form(sha=OWN_SHA) + "\n" + _form(sha=OWN_SHA)),
            ("a form quoted alongside the real one",
             "For reference the shape is:\n" + _form(sha=BASE_SHA) +
             "\nand here is mine:\n" + _form(sha=OWN_SHA)),
            ("lowercase verdict", _form(sha=OWN_SHA, verdict="pass")),
            ("a FAIL verdict is a review that did not clear",
             _form(sha=OWN_SHA, verdict="FAIL")),
            ("unknown verdict", _form(sha=OWN_SHA, verdict="APPROVED")),
            ("unknown seat", _form(sha=OWN_SHA, seat="reviewer")),
            ("seat with different casing", _form(sha=OWN_SHA, seat="Architect")),
            ("abbreviated sha", _form(sha=OWN_SHA[:12])),
            ("uppercase sha", _form(sha=OWN_SHA.upper())),
            ("a branch name instead of a sha", _form(sha="product/520-form")),
            ("unparseable filed-at", _form(sha=OWN_SHA, filed_at="last Tuesday")),
            ("filed-at with no timezone",
             _form(sha=OWN_SHA, filed_at="2026-09-02T18:00:00")),
            ("paraphrased assertion",
             _form(sha=OWN_SHA, attests="Looks good to me, reviewed at the sha above.")),
            ("assertion that only names the words",
             _form(sha=OWN_SHA, attests="reviewed-sha verdict finding review")),
            ("wrong fence tag", _form(sha=OWN_SHA, tag="nightshift-review-form")),
            # R6-1(a), `security`'s seventh instance and the sharpest of them.
            # The rule is "info string EXACTLY the tag", and the row above
            # cannot pin it: `nightshift-review-form` differs in a PREFIX
            # position, so it discriminates "a different tag" and not
            # "exactly". `info == tag` -> `.startswith(` survived all 76
            # tests, and a form tagged `...-EXAMPLE` went 0/224 shipped to
            # 224/224 under the mutant.
            #
            # This is not hypothetical. `-EXAMPLE` is the convention
            # `security` used in all four of its comments on this PR as the
            # safe way to name the form without filing one -- so under that
            # mutant its own review comments would have filed forms.
            ("tag with a SUFFIX -- security's `-EXAMPLE` convention",
             _form(sha=OWN_SHA, tag=_REVIEW_FORM_TAG + "-EXAMPLE")),
            ("tag with a suffix after a separator",
             _form(sha=OWN_SHA, tag=_REVIEW_FORM_TAG + ".sample")),
            # The other relaxation direction, so `.endswith(` is pinned too.
            ("tag with a PREFIX",
             _form(sha=OWN_SHA, tag="quoted-" + _REVIEW_FORM_TAG)),
            ("no fence at all",
             _form(sha=OWN_SHA).replace("```nightshift-retro-review\n", "")
                               .replace("\n```\n", "\n")),
            ("junk line inside the block before any field",
             "```nightshift-retro-review\nsee below\n" +
             _form(sha=OWN_SHA).split("\n", 1)[1]),
        )
        for label, comment in deviations:
            with self.subTest(deviation=label):
                v = self._verdict({700: self._pr([comment])})
                self.assertEqual(
                    v.status, dv.Status.FAIL,
                    f"a form with {label} was accepted. A partially-present or "
                    "deviant form is a FAIL, not a weaker pass.")

    def test_the_canonical_assertion_may_be_line_wrapped(self):
        """Fail-closed must not mean unusable: the one sentence a filer has to
        type is long enough to wrap, and a wrapped form is the SAME form."""
        wrapped = ("```nightshift-retro-review\n"
                   "reviewer-seat: architect\n"
                   f"reviewed-sha: {OWN_SHA}\n"
                   "verdict: PASS\n"
                   "filed-at: 2026-09-02T18:00:00Z\n"
                   "attests: I reviewed this pull request's changes at\n"
                   "  reviewed-sha and the verdict above is that review's\n"
                   "  own finding.\n"
                   "```\n")
        v = self._verdict({700: self._pr([wrapped])})
        self.assertEqual(v.status, dv.Status.PASS, v.reason)

    # ------------------------------------------------------------------
    # `security`'s measured binding: reachable from the head, NOT from
    # `mergeCommit^1`. Both halves, and the fail-closed edges.
    # ------------------------------------------------------------------

    def test_a_sha_reachable_from_neither_side_is_refused(self):
        v = self._verdict({700: self._pr([_form(sha=UNRELATED_SHA)])})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_the_prs_own_head_is_accepted_not_only_intermediate_commits(self):
        """`security` measured that requiring the EXACT head is too tight
        (18 of 33), which is why an earlier own-commit is accepted. The head
        itself must still qualify -- a rule that accepted round-1 SHAs but not
        the head would be absurd."""
        v = self._verdict({700: self._pr([_form(sha="d" * 40)])})
        self.assertEqual(v.status, dv.Status.PASS, v.reason)

    def test_a_form_is_refused_when_the_pr_carries_no_head_sha(self):
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)], head=None)})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_form_is_refused_when_the_pr_carries_no_merge_commit(self):
        """Without `mergeCommit^1` the exclusion half cannot be asked at all,
        and an unaskable half is not a satisfied one."""
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)], merge=None)})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_git_status_that_is_not_an_answer_is_not_read_as_one(self):
        """git's 128 (bad object) is not "no, not an ancestor" -- and it is
        certainly not "yes". Under a git that answers 128 to everything, no
        form is accepted and D22 stays FAIL rather than degrading upward."""
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)])},
                          git=_fake_git_refuses_every_binding())
        self.assertEqual(v.status, dv.Status.FAIL)

    # ------------------------------------------------------------------
    # Retroactive-only. The form cannot displace the live receipt path.
    # ------------------------------------------------------------------

    def test_the_honest_retroactive_filer_is_accepted_whatever_filed_at_says(self):
        """#636's behaviour, pinned in the direction it moved.

        `staff` measured that re-inserting the deleted gate verbatim left the
        suite GREEN at 74/74: two FAIL-asserting tests came out and no
        PASS-asserting test went in, so the behaviour the change exists to
        create was asserted by nothing. A revert, a merge resolution, or
        someone restoring the old docstring's "checked for consistency with
        GitHub's clock" would have reintroduced the trap silently. A
        behaviour change is not complete without the assertion that pins it.

        The case that matters is the FIRST row: on a retroactive attestation
        the natural `filed-at` is when the review actually happened, which is
        plausibly BEFORE the merge. `security` measured both directions on all
        224 real PRs and the old gate was worse than useless -- a filer who
        typed a LATER date passed 224/224, while one who typed the TRUE
        EARLIER date failed 0/224. It was bypassed by the party it purported
        to constrain and binding only on the party being honest.

        The other rows make the pin DELIBERATE rather than incidental. The
        default fixture happens to carry a `filed-at` later than its
        `createdAt`, so a near-restoration comparing those two instead would
        be caught only by luck. These four straddle both instants -- before
        `mergedAt`, exactly on it, between the two, and after `createdAt` --
        so any comparison that reads this field's VALUE at all reddens at
        least one row. Nothing rests on `filed-at`, and that is now a test
        rather than a sentence.
        """
        for label, filed_at in (
            ("the true review date, before the merge", "2026-08-15T09:00:00Z"),
            ("exactly the merge instant", "2026-09-01T00:00:00Z"),
            ("after the merge, before the comment", "2026-09-01T12:00:00Z"),
            ("after the comment was posted", "2026-09-03T00:00:00Z"),
        ):
            with self.subTest(filed_at=label):
                v = self._verdict({700: self._pr(
                    [_form(sha=OWN_SHA, filed_at=filed_at)],
                    merged_at="2026-09-01T00:00:00Z",
                    comment_created_at="2026-09-02T09:00:00Z")})
                self.assertEqual(
                    v.status, dv.Status.PASS,
                    f"a form whose declared filed-at is {label} was refused. "
                    "`filed-at` is declared and inert; posteriority is decided "
                    "by the comment's own GitHub `createdAt`, which is after "
                    "the merge in every row here.")
                self.assertIn(
                    "retro: 1", v.reason,
                    "and it must pass through the RETRO path, not some other one")

    def test_an_unmerged_pr_cannot_be_attested_by_a_form(self):
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)],
                                         merged_at=None, state="OPEN")})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_form_is_only_consulted_after_every_stronger_form_declines(self):
        """A PR with a real ADR-0039 receipt AND a form must not be counted as
        retro-attested: the disclosure has to name the PRs whose evidence is
        ONLY an attestation, or it over-reports and stops being believed."""
        v = self._verdict({700: self._pr([RECEIPT_COMMENT, _form(sha=OWN_SHA)])})
        self.assertEqual(v.status, dv.Status.PASS)
        self.assertEqual(v.reason, "")

    # ------------------------------------------------------------------
    # The counterfactual the charter asks for by name.
    # ------------------------------------------------------------------

    def test_the_permissive_git_fake_is_load_bearing(self):
        """`security` proved the permissive git fake is not a stylistic
        nicety: under a REFUSING fake, every "D22 must FAIL" guard in this
        file goes green while a live evidence path sits in the validator --
        passing for the wrong reason, the exact defect class the guards exist
        to catch.

        So assert the counterfactual directly, on ONE fixture that differs
        only in the git fake. If these two ever agree, either the fake stopped
        granting form (4)'s binding or the validator stopped asking git
        anything -- and in both cases every other guard here silently becomes
        theatre.

        Note the direction, which is what changed with form (4): a fake that
        answers rc=0 to EVERY `--is-ancestor` question is not permissive for
        this form, it is refusing, because form (4) needs to hear NO about
        `mergeCommit^1`.
        """
        fixture = {700: self._pr([_form(sha=OWN_SHA)])}
        granted = self._verdict(fixture, git=_fake_git_grants_any_binding())
        refused = self._verdict(fixture, git=_fake_git_refuses_every_binding())
        self.assertEqual(
            granted.status, dv.Status.PASS,
            "the permissive fake must GRANT form (4)'s binding, or every guard "
            "in this file that asserts D22 FAILs passes for want of git")
        self.assertEqual(
            refused.status, dv.Status.FAIL,
            "the refusing fake must reject the same genuine form -- if it does "
            "not, the git binding is not being consulted at all")

    def test_answering_yes_to_every_ancestry_question_refuses_form_4(self):
        """The trap named above, made a test rather than a comment. The
        withdrawn form's fake said rc=0 to everything and was correctly called
        maximally permissive FOR THAT FORM. Reused unchanged here it would
        reject every form (4), and this file's guards would go green for the
        wrong reason a second time."""
        def yes_to_everything(args, cwd=None, timeout=30):
            if "--merges" in args:
                return _FakeProc(0, "")
            return _FakeProc(0, "f" * 40)
        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)])},
                          git=yes_to_everything)
        self.assertEqual(v.status, dv.Status.FAIL)


    # ------------------------------------------------------------------
    # MB-1 (round 1) -- the form is RETROACTIVE-only, not merely posterior.
    # ------------------------------------------------------------------

    def test_a_pr_outside_the_pinned_eligibility_ancestry_cannot_use_form_4(self):
        """`architect`'s non-negotiable condition, in `crew_alpha`'s proof
        shape, re-expressed against ANCESTRY after round 2's ruling.

        Round 1 accepted on `filed-at > mergedAt` with no upper bound on
        `mergedAt` anywhere, so `[PRE] -> PASS | retro: 1` and
        `[POST] -> PASS | retro: 1` were both true. `crew_beta` named it:
        `filed-at > mergedAt` proves POSTERIORITY, not RETROACTIVITY.

        The bound is now `git merge-base --is-ancestor <mergeCommit>
        FORM_4_ELIGIBLE_THROUGH`. Everything below is held constant except
        whether the PR's merge commit is an ancestor of that pin -- the
        attestation is filed in 2027 in every row, so posteriority is
        satisfied throughout and ancestry is the only thing deciding it.
        """
        filed = "2027-01-15T00:00:00Z"
        cases = (
            ("merged long before the pin", "2026-08-01T00:00:00Z", True, dv.Status.PASS),
            ("merged the same day as the pin", "2026-09-02T09:00:00Z", True, dv.Status.PASS),
            # The case that actually bit, and the one no calendar cutoff could
            # make: #626/#627/#624/#594/#596 were all due to merge that night,
            # BEFORE any midnight constant, with ADR-0039 receipts available.
            # Under a date anchor all five became retro-attestable by morning.
            ("merged tonight, after the pin but before any calendar cutoff",
             "2026-09-02T22:30:00Z", False, dv.Status.FAIL),
            ("merged next December, receipts available and unused",
             "2026-12-01T00:00:00Z", False, dv.Status.FAIL),
            ("merged next year on a branch that never touched the pin",
             "2027-06-01T00:00:00Z", False, dv.Status.FAIL),
        )
        for label, merged_at, eligible, expected in cases:
            with self.subTest(scenario=label):
                v = self._verdict(
                    {700: self._pr([_form(sha=OWN_SHA, filed_at=filed)],
                                   merged_at=merged_at, comment_created_at=filed)},
                    eligible=eligible)
                self.assertEqual(
                    v.status, expected,
                    f"{label}: form (4) must be reachable ONLY by PRs whose "
                    "merge commit is an ancestor of FORM_4_ELIGIBLE_THROUGH -- "
                    "the tip the 224-PR corpus was audited at. Merging more "
                    "work moves `main` and never moves the pin, so the "
                    "eligible set cannot grow; a calendar anchor's residual "
                    "measured 0 and would have been 5 by the next morning.")

    def test_no_ancestry_no_evidence(self):
        """The same rule as "no clock, no evidence", one bound over. An absent
        merge commit, a SHA git does not know, or git being unavailable are
        all questions that did not get answered, and an unanswered question is
        not a satisfied bound. Only git's own rc=0 counts as yes."""
        form = _form(sha=OWN_SHA)
        # (a) git cannot answer at all
        v = self._verdict({700: self._pr([form])},
                          git=_fake_git_refuses_every_binding())
        self.assertEqual(v.status, dv.Status.FAIL)

        # (b) git answers, but the merge commit is not an ancestor of the pin
        v = self._verdict({700: self._pr([form])}, eligible=False)
        self.assertEqual(v.status, dv.Status.FAIL)

        # (c) there is no merge commit to ask about
        v = self._verdict({700: self._pr([form], merge=None)})
        self.assertEqual(v.status, dv.Status.FAIL)

        # (d) git does not know the merge commit -- rc 128, a NON-ANSWER --
        #     while every other binding is granted. This case is why (a) is
        #     not enough on its own: under the refusing fake the head binding
        #     fails too, so (a) passes for the wrong reason and cannot see a
        #     validator that reads 128 as eligible. Caught in this round's own
        #     mutation run, where exactly that mutant survived all 62 tests.
        v = self._verdict({700: self._pr([form])}, eligible="unknown")
        self.assertEqual(
            v.status, dv.Status.FAIL,
            "git's 128 is not an answer, and certainly not `yes`. Only rc=0 "
            "counts as eligible -- no ancestry, no evidence.")

    def test_the_eligibility_probe_asks_about_the_merge_commit_not_the_head(self):
        """The pin bounds which MERGES were audited. Asking about the head
        would be a different question with a different answer -- a branch head
        is not in `main`'s history at all -- so the argument is asserted
        rather than assumed."""
        seen = []
        inner = _fake_git_ancestry(
            own_commits={OWN_SHA}, base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA})

        def spy(args, cwd=None, timeout=30):
            argv = _subcommand(args)
            if argv[:2] == ["merge-base", "--is-ancestor"] \
                    and argv[3] == dv.FORM_4_ELIGIBLE_THROUGH:
                seen.append(argv[2])
            return inner(args, cwd=cwd, timeout=timeout)

        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)],
                                         head="d" * 40, merge="e" * 40)}, git=spy)
        self.assertEqual(v.status, dv.Status.PASS, v.reason)
        self.assertEqual(seen, ["e" * 40],
                         "eligibility must be asked of the merge commit, not the head")

    def test_posteriority_is_measured_on_githubs_clock_not_the_filers_field(self):
        """`security`: `filed-at` is "simply a field the filer types, verified
        against nothing", so a control resting on it rests on nothing. The
        comment's own `createdAt` is GitHub's clock and is the control.

        Here `filed-at` declares a filing after the merge and the comment was
        demonstrably created before it. Under round 1's code this passed.
        """
        v = self._verdict({700: self._pr(
            [_form(sha=OWN_SHA, filed_at="2026-09-02T09:00:00Z")],
            merged_at="2026-09-01T00:00:00Z",
            comment_created_at="2026-08-01T00:00:00Z")})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_comment_with_no_github_clock_files_nothing(self):
        """No clock, no evidence. Fail-closed rather than falling back to the
        declared field, which is the thing that was found untrustworthy."""
        dv.run_gh = _fake_gh({700: {
            "state": "MERGED", "mergedAt": "2026-09-01T00:00:00Z", "body": BODY,
            "reviews": [], "statusCheckRollup": GREEN_ROLLUP,
            "comments": [{"body": _form(sha=OWN_SHA)}],   # no createdAt
            "headRefOid": "d" * 40, "mergeCommit": {"oid": "e" * 40},
        }})
        dv.run_git = _fake_git_ancestry(
            own_commits={OWN_SHA}, base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA})
        dv._merged_pr_numbers_in_range = lambda ctx, base: set()
        v = dv.check_d22(self._ctx({"changes": [700]}))
        self.assertEqual(v.status, dv.Status.FAIL)

    # ------------------------------------------------------------------
    # MB-2 (round 1, `security` S-1) -- QUOTING a form must not FILE one.
    # ------------------------------------------------------------------

    def test_a_quoted_form_files_nothing(self):
        """Round 1's parser claimed, in its own comment, that "a comment that
        quotes a form must not be able to file one". It did not hold, and
        `security` measured 224/224: `len(blocks) != 1` catches a quotation
        ALONGSIDE a real form, but A LONE QUOTED EXAMPLE IS A FILING.

        That is narrower than form (3)'s defeat, so form (4) was still
        strictly better -- but it is the same class, a docstring asserting a
        control that is not implemented, and that is exactly what withdrew
        form (3). `security` wrote its entire review with no example form in
        it, because posting one would have filed a receipt. After this, the
        ordinary safe thing is safe.
        """
        form = _form(sha=OWN_SHA)
        quoted = (
            # An outer four-backtick fence: the canonical way to show a
            # fenced block inside markdown, and 224/224 under round 1.
            ("inside an outer four-backtick fence",
             "The shape is:\n\n````\n" + form + "````\n"),
            ("inside an outer tilde fence",
             "The shape is:\n\n~~~\n" + form + "~~~\n"),
            # `^[ \t]*` meant indentation was never the disambiguator it
            # looked like.
            ("as a four-space-indented code block",
             "The shape is:\n\n" + "\n".join("    " + ln for ln in form.splitlines())),
            ("as a tab-indented code block",
             "The shape is:\n\n" + "\n".join("\t" + ln for ln in form.splitlines())),
            # Safe in round 1 too, and only by accident -- `>` happens not to
            # match the fence regex. Asserted so it stays safe on purpose.
            ("inside a blockquote",
             "\n".join("> " + ln for ln in form.splitlines())),
            ("in a fence that is never closed",
             "````\n" + form),
            # R1, round 4. A fence in a list item is indented RELATIVE TO ITS
            # MARKER, so GitHub renders a nested code block while an
            # `indent >= 4` scanner reads a top-level filing. `security`
            # measured 224/224, and this is the most natural way a person
            # quotes a form in a review comment -- likelier than the
            # four-backtick case that made MB-2 a blocker.
            ("inside a bullet list item",
             "- file this instead:\n\n" +
             "\n".join("  " + ln for ln in form.splitlines())),
            ("inside a numbered list item",
             "1. file this instead:\n\n" +
             "\n".join("   " + ln for ln in form.splitlines())),
            ("inside a nested bullet",
             "- context\n  - file this instead:\n\n" +
             "\n".join("    " + ln for ln in form.splitlines())),
            ("indented by a single space",
             "\n".join(" " + ln for ln in form.splitlines())),
        )
        for label, comment in quoted:
            with self.subTest(quotation=label):
                v = self._verdict({700: self._pr([comment])})
                self.assertEqual(
                    v.status, dv.Status.FAIL,
                    f"a form quoted {label} FILED one. Fence identification "
                    "must understand nesting and quotation; a control the "
                    "docstring claims and the code does not implement is what "
                    "withdrew form (3).")

    def test_quoting_a_form_next_to_a_real_one_still_files_the_real_one(self):
        """The other side of MB-2, so the fix is not simply "refuse harder".
        A comment that explains the shape inside an outer fence AND files a
        genuine form at the top level has filed exactly one form, and the
        quotation is not a second block that makes it ambiguous."""
        v = self._verdict({700: self._pr([
            "For reference the shape is:\n\n````\n" + _form(sha=BASE_SHA) +
            "````\n\nAnd here is mine:\n\n" + _form(sha=OWN_SHA)])})
        self.assertEqual(v.status, dv.Status.PASS, v.reason)
        self.assertIn("retro: 1", v.reason)

    def test_two_real_top_level_forms_are_still_ambiguous_and_refused(self):
        v = self._verdict({700: self._pr(
            [_form(sha=OWN_SHA) + "\n" + _form(sha=OWN_SHA)])})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_an_indented_opener_is_refused_even_when_its_closer_is_at_column_0(self):
        """R4, and it pins a property `security` proved was NOT pinned before.

        The suite's indented-quotation case was being carried by the CLOSER's
        indent check, not the opener guard it appeared to test -- so deleting
        the opener guard left all 62 tests green while the shipped code
        refused the input. This fixture separates them: the opener is indented
        and the closer sits at column 0, so the opener guard is the only thing
        that can refuse it.

        Round 5 (`staff`, R5-2): pinned only from TWO columns while the stated
        rule is COLUMN 0, so `if indent:` -> `if indent > 1:` still survived.
        Nothing shipped wrong; the pin missed its own boundary. It now starts
        at ONE column, which is where that mutant lived.
        """
        for columns in (1, 2, 3, 4, 7):
            with self.subTest(indent=columns):
                self._assert_indented_opener_refused(" " * columns)
        with self.subTest(indent="tab"):
            self._assert_indented_opener_refused("\t")

    def _assert_indented_opener_refused(self, prefix):
        indented_opener = (
            prefix + "```nightshift-retro-review\n"
            "reviewer-seat: architect\n"
            f"reviewed-sha: {OWN_SHA}\n"
            "verdict: PASS\n"
            "filed-at: 2026-09-02T18:00:00Z\n"
            "attests: I reviewed this pull request's changes at reviewed-sha "
            "and the verdict above is that review's own finding.\n"
            "```\n")
        v = self._verdict({700: self._pr([indented_opener])})
        self.assertEqual(
            v.status, dv.Status.FAIL,
            "a fence opener that is not at column 0 is content -- of a list "
            "item, a blockquote or an indented code block -- and must not open "
            "a filing, whatever its closer does")

    def test_an_unterminated_form_fence_yields_nothing(self):
        """R4's second property, also unpinned before: `security` showed a
        mutant that emitted the accumulated block for an unclosed fence
        survived all 62 tests. The existing "never closed" case could not see
        it, because its unclosed fence was the OUTER one and carried no tag.
        Here the tagged fence itself is never closed."""
        v = self._verdict({700: self._pr([
            "```nightshift-retro-review\n"
            "reviewer-seat: architect\n"
            f"reviewed-sha: {OWN_SHA}\n"
            "verdict: PASS\n"
            "filed-at: 2026-09-02T18:00:00Z\n"
            "attests: I reviewed this pull request's changes at reviewed-sha "
            "and the verdict above is that review's own finding.\n"])})
        self.assertEqual(
            v.status, dv.Status.FAIL,
            "a block nobody closed is not a filed form")

    def test_a_form_whose_closer_is_not_at_column_0_never_closes(self):
        """The other half of the stated column-0 rule, pinned because it was
        not. Found in this round's own mutation run: relaxing the CLOSER back
        to `indent < 4` survived all 66 tests, so half of a rule this file's
        docstring states was resting on nothing.

        The practical reach is narrower than the opener's -- a quotation
        indents the OPENER, so this shape is a sloppily-closed genuine form
        rather than a hijack -- but an unpinned half of a stated rule is the
        exact defect class this PR has now re-learned three times, and the
        fail-closed direction (an unclosed block files nothing) is the one
        worth keeping either way.
        """
        v = self._verdict({700: self._pr([
            "```nightshift-retro-review\n"
            "reviewer-seat: architect\n"
            f"reviewed-sha: {OWN_SHA}\n"
            "verdict: PASS\n"
            "filed-at: 2026-09-02T18:00:00Z\n"
            "attests: I reviewed this pull request's changes at reviewed-sha "
            "and the verdict above is that review's own finding.\n"
            "  ```\n"])})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_closer_using_a_different_fence_character_does_not_close(self):
        """R5, mutant 2. The docstring says the closer must use the SAME fence
        character as the opener; nothing asserted it. A ``` fence is not
        closed by ~~~, so this form is never terminated and files nothing."""
        v = self._verdict({700: self._pr([
            "```nightshift-retro-review\n" + _FORM_FIELDS + "~~~\n"])})
        self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_closer_carrying_an_info_string_does_not_close(self):
        """R5, mutant 3. The docstring says a closer carries no info string;
        nothing asserted it. ```` ```text ```` opens a fence in CommonMark, it
        does not close one, so the form is never terminated."""
        for label, closer in (("a word", "```text\n"),
                              ("the tag itself", "```nightshift-retro-review\n")):
            with self.subTest(info=label):
                v = self._verdict({700: self._pr([
                    "```nightshift-retro-review\n" + _FORM_FIELDS + closer])})
                self.assertEqual(v.status, dv.Status.FAIL)

    def test_a_longer_closer_still_closes_the_block(self):
        """The `>=` direction of the length rule, which the four-backtick
        quotation case pins only from the other side. A ``` fence closed by
        ```` is closed."""
        v = self._verdict({700: self._pr([
            "```nightshift-retro-review\n" + _FORM_FIELDS + "````\n"])})
        self.assertEqual(v.status, dv.Status.PASS, v.reason)

    def test_the_posteriority_boundary_is_the_control_not_the_declared_field(self):
        """R5, mutant 4. `created_at <= merged_at` -> `<` survived, because the
        boundary that IS tested belongs to `filed-at` -- the decorative,
        filer-typed field -- and the boundary of the ACTUAL control was not.
        Testing the ornament's edge and not the load-bearing one is the same
        defect as a docstring nobody asserts.

        Here `filed-at` is comfortably after the merge, so only GitHub's clock
        can decide it: a comment created at the exact merge instant is not
        after the merge.
        """
        for label, created_at, expected in (
            ("created at the exact merge instant", "2026-09-01T00:00:00Z",
             dv.Status.FAIL),
            ("created one second before the merge", "2026-08-31T23:59:59Z",
             dv.Status.FAIL),
            ("created one second after the merge", "2026-09-01T00:00:01Z",
             dv.Status.PASS),
        ):
            with self.subTest(clock=label):
                v = self._verdict({700: self._pr(
                    [_form(sha=OWN_SHA, filed_at="2026-09-02T12:00:00Z")],
                    merged_at="2026-09-01T00:00:00Z",
                    comment_created_at=created_at)})
                self.assertEqual(v.status, expected)

    def test_the_exclusion_half_needs_gits_exact_no_not_a_non_answer(self):
        """R5-1, and this one is not a docstring gap.

        `outside.returncode == 1` -> `!= 0` survived all 69 tests. Under a git
        that GRANTS the head binding and answers 128 to the `mergeCommit^1`
        question -- an unfetched merge parent, a squash parent, or a SHALLOW
        CLONE, every one an ordinary environment rather than an attacker --
        the mutant ACCEPTS form (3)'s universal baseline constant. That is the
        216/216 defeat resurrected by a checkout depth.

        The cause is identical to Mutant J's, in Mutant J's sibling half:
        `test_a_git_status_that_is_not_an_answer_is_not_read_as_one` uses the
        refuses-everything fake, so `inside` fails first and this line is
        never reached. So the fake grants everything EXCEPT this question, on
        which it answers 128 -- Mutant J's fixture applied to the half that
        did not get it.

        Both SHAs are asserted, because they fail for different reasons and
        only the first is the resurrection: the universal constant IS an
        ancestor of `mergeCommit^1` in reality, so a non-answer must not be
        read as the "no" that would admit it.
        """
        for label, sha in (
            ("form (3)'s universal baseline constant", UNIVERSAL_BASELINE_SHA),
            ("one of the PR's own commits", OWN_SHA),
        ):
            with self.subTest(reviewed_sha=label):
                git, argvs = _recording(_fake_git_ancestry(
                    own_commits={OWN_SHA},
                    base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA},
                    merge_parent_rc=128))
                v = self._verdict({700: self._pr([_form(sha=sha)])}, git=git)
                # R6-2: assert the probe this test is NAMED for actually ran.
                # Without this the assertion below is satisfied by any
                # upstream fail-closed guard and pins nothing.
                self.assertTrue(
                    any(t.endswith("^1") for t in _ancestry_targets(argvs)),
                    "the mergeCommit^1 probe never ran, so this test proves "
                    f"nothing about its status handling: {_ancestry_targets(argvs)}")
                self.assertEqual(
                    v.status, dv.Status.FAIL,
                    "git's 128 on the mergeCommit^1 question is a NON-ANSWER "
                    "-- an unfetched or squashed or shallow parent -- and "
                    "reading it as `no, not an ancestor` re-admits the "
                    "constant string that defeated form (3) 216/216")

    def test_the_head_half_also_needs_gits_exact_answer(self):
        """The same structural gap as R5-1, one probe over, found by auditing
        the class rather than waiting for a sixth review to find it.

        `_sha_is_one_of_this_prs_own_commits` claims a git call that does not
        answer yields False. The exclusion half of that claim was unpinned
        (R5-1); the HEAD half was unpinned for the identical reason -- the
        refuses-everything fake fails ELIGIBILITY first, so neither line is
        reached. Relaxing `inside.returncode != 0` to `== 1` then accepts a
        SHA git has never heard of: 128 is not 1, so it falls through, and a
        SHA in neither the head's history nor `mergeCommit^1`'s answers rc 1
        on the exclusion question and is read as one of the PR's own commits.

        So: grant eligibility, model everything else normally, and answer 128
        to the head question alone -- an unfetched head, the same class of
        ordinary environment as R5-1's shallow clone.
        """
        for label, sha in (("a SHA git has never heard of", UNRELATED_SHA),
                           ("one of the PR's own commits", OWN_SHA)):
            with self.subTest(reviewed_sha=label):
                git, argvs = _recording(_fake_git_ancestry(
                    own_commits={OWN_SHA},
                    base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA},
                    head_rc=128))
                v = self._verdict({700: self._pr([_form(sha=sha)])}, git=git)
                targets = _ancestry_targets(argvs)
                # R6-2, both directions: the head probe must have run, and
                # the exclusion probe must NOT have -- a non-answer on the
                # head question must stop there rather than fall through.
                self.assertIn("d" * 40, targets,
                              f"the head probe never ran: {targets}")
                self.assertFalse(
                    [t for t in targets if t.endswith("^1")],
                    "a non-answer on the head question must not fall through "
                    f"to the exclusion probe: {targets}")
                self.assertEqual(
                    v.status, dv.Status.FAIL,
                    "a non-answer on the head question must not fall through "
                    "to the exclusion question, which would read `not an "
                    "ancestor of mergeCommit^1` as `one of the PR's own "
                    "commits` for a SHA that is neither")

    def test_every_ancestry_probe_disables_replace_refs(self):
        """R2 / PD-1. `git replace --graft <pin> <post-pin commit>` rewrites
        what "ancestor" means for every later reader -- no PAT, no network, no
        change to the constant, nothing in the diff. It is not a privilege
        escalation, but it moves the pin with neither a re-run of the audit
        nor a changelog row, which is precisely what ADR-0075 exists to
        prevent.

        The fakes deliberately strip git's global options before matching, so
        they cannot vouch for the flag being present. This asserts it on the
        raw argv of every ancestry probe the form (4) path makes -- the
        eligibility question AND both halves of the SHA binding, since both
        are ancestry controls and both are graftable.
        """
        argvs = []
        inner = _fake_git_ancestry(
            own_commits={OWN_SHA}, base_commits={BASE_SHA, UNIVERSAL_BASELINE_SHA})

        def spy(args, cwd=None, timeout=30):
            argvs.append(list(args))
            return inner(args, cwd=cwd, timeout=timeout)

        v = self._verdict({700: self._pr([_form(sha=OWN_SHA)])}, git=spy)
        self.assertEqual(v.status, dv.Status.PASS, v.reason)
        ancestry = [a for a in argvs if "--is-ancestor" in a]
        self.assertEqual(
            len(ancestry), 3,
            "the form (4) path asks three ancestry questions: eligibility, "
            f"reachable-from-head, and not-from-mergeCommit^1. Saw: {ancestry}")
        for argv in ancestry:
            self.assertEqual(
                argv[0], "--no-replace-objects",
                f"{argv!r} can be answered by a `git replace --graft` ref, which "
                "moves the pin invisibly and without the audit re-run ADR-0075 "
                "requires")

    # ------------------------------------------------------------------
    # The seat roster is borrowed, not invented.
    # ------------------------------------------------------------------

    def test_form_seat_roster_is_a_subset_of_nightshift_ctls_own(self):
        """`_REVIEW_FORM_SEATS` is copied from `bin/nightshift-ctl` rather
        than imported (that file is hyphenated, crew_alpha-owned, and drags a
        control plane ADR-0025 forbids this validator to reach). A copy drifts
        unless something checks it, so this reads the literal."""
        text = (dv.REPO_ROOT / "bin" / "nightshift-ctl").read_text(
            encoding="utf-8", errors="replace")
        roster = set()
        for name in ("EXEMPT_AGENTS", "ONCALL_AGENTS"):
            m = re.search(name + r"\s*=\s*frozenset\(\{(.*?)\}\)", text, re.DOTALL)
            self.assertIsNotNone(m, f"{name} literal not found in bin/nightshift-ctl")
            roster |= set(re.findall(r'"([a-z_]+)"', m.group(1)))
        self.assertTrue(
            dv._REVIEW_FORM_SEATS <= roster,
            "form (4) accepts seat names bin/nightshift-ctl does not know: "
            f"{sorted(dv._REVIEW_FORM_SEATS - roster)}")


if __name__ == "__main__":
    unittest.main()
